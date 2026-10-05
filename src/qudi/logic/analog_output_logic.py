# -*- coding: utf-8 -*-
"""
Interact with one or more analog output hardware modules (per-channel voltage
setpoint and activity/output state), e.g. NIXSeriesAnalogOutput.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.

------------------------------------------------------------------------

OVERVIEW

Wraps one or more hardware modules implementing qudi's ProcessSetpointInterface
(with the ProcessControlSwitchMixin activity-state methods) -- e.g.
NIXSeriesAnalogOutput -- for GUI use.

MULTIPLE DEVICES

Qudi Connectors are declared statically in code (resolved at class definition
time, before config is read), so there is no way to create "however many the
config specifies" via a runtime loop. This module declares a fixed set of
optional connectors (ao1 through ao8) and simply ignores whichever ones are
left unconnected in your YAML config -- so you can stack anywhere from 1 to 8
analog output devices without any code changes, just by wiring up that many
connect: entries. If more than 8 are ever needed, this number needs to be
increased here, in code (one line per extra slot).

Since two connected devices can easily expose channels with the same name
(e.g. both report 'ao0'..'ao3'), every public method in this logic operates
on a (device_name, channel) PAIR, not on a bare channel name. device_name is
each hardware module's own qudi module name (its config section key, e.g.
'daq_1'), which is guaranteed unique by qudi's own configuration -- this is
also what is used as the section headline in the GUI.

KEY DESIGN: "target setpoint" vs. "hardware activation" are fully decoupled.

    - self._target_setpoint[(device_name, channel)] is the value the user
      wants -- what is shown in the GUI's voltage field. It can be changed at
      ANY time, whether the channel is currently on or off, and changing it
      NEVER by itself activates or deactivates anything.

    - set_setpoint(device_name, channel, value) updates the target. If the
      channel happens to be ON at that moment, the new value is ALSO written
      to hardware immediately (true "on the fly" updates while live). If the
      channel is OFF, nothing is written to hardware -- the new target is
      simply remembered for whenever the channel is next turned on.

    - set_activity_state(device_name, channel, True) turns the physical
      output on: it activates the hardware channel and then writes the
      current target value to it.

    - set_activity_state(device_name, channel, False) turns the physical
      output off: it explicitly writes 0 V (while still active) and THEN
      deactivates the channel, guaranteeing "off" always means 0 V regardless
      of the hardware module's own 'keep_value' config option (which would
      otherwise leave the DAC holding its last voltage indefinitely after
      simply closing the task). The target value itself is left completely
      untouched by this -- it is not zeroed, so it is ready to be reapplied
      the next time the channel is turned on.

This means: on/off purely controls "output the target value" vs. "output 0",
and the target value can be edited freely at any time without side effects on
activation state -- matching a plain "voltage dial + power switch" mental
model, now per (device, channel) pair.

A background watchdog periodically polls activity state and (for active
channels only) the live hardware setpoint, catching external changes made
outside of this logic module (e.g. via a script, the console, or another
qudi session) and updating the target/GUI to match.

------------------------------------------------------------------------

Example config for copy-paste:

    analog_output_logic:
        module.Class: 'analog_output_logic.AnalogOutputLogic'
        options:
            watchdog_interval: 1  # optional
            autostart_watchdog: True  # optional
        connect:
            ao1: 'daq_1'
            ao2: 'daq_2'
"""

from PySide6 import QtCore

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.interface.process_control_interface import ProcessSetpointInterface


# Fixed, generous connector count -- see module docstring, "MULTIPLE DEVICES".
_MAX_AO_DEVICES = 8


class AnalogOutputLogic(LogicBase):
    """ Logic module for interacting with one or more analog output hardware
    modules (per-channel voltage setpoint and activity/output state).
    """

    ao1 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao2 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao3 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao4 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao5 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao6 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao7 = Connector(interface=ProcessSetpointInterface, optional=True)
    ao8 = Connector(interface=ProcessSetpointInterface, optional=True)

    _watchdog_interval = ConfigOption(name='watchdog_interval', default=1.0, missing='nothing')
    _autostart_watchdog = ConfigOption(name='autostart_watchdog', default=False, missing='nothing')

    # (device_name, channel, value) / (device_name, channel, active)
    sigSetpointChanged = QtCore.Signal(str, str, float)
    sigActivityChanged = QtCore.Signal(str, str, bool)
    sigWatchdogToggled = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        self._watchdog_active = False
        self._watchdog_interval_ms = 0

        # Connected devices, in connect: declaration order, as
        # (device_name, hardware_instance) tuples. device_name is each
        # hardware module's own qudi module name.
        self._devices = []
        # device_name -> hardware_instance, for O(1) lookup by name.
        self._device_by_name = {}

        # The "target" voltage per (device_name, channel) -- what the user
        # wants, shown in the GUI, independent of whether the channel is
        # currently on or off.
        self._target_setpoint = dict()
        self._old_activity = dict()

    def on_activate(self):
        """ Activate module. Collects every connected analog output device,
        then initializes the target setpoint for each of its channels from
        the hardware's live value if the channel happens to already be
        active, or falls back to the hardware module's own remembered
        setpoint otherwise (best-effort; see comment below), so the
        displayed target survives a Qudi restart even for channels that are
        currently off.
        """
        self._devices = []
        self._device_by_name = {}
        for i in range(1, _MAX_AO_DEVICES + 1):
            connector = getattr(self, f'ao{i}')
            if connector.is_connected:
                instance = connector()
                name = instance.module_name
                self._devices.append((name, instance))
                self._device_by_name[name] = instance

        if not self._devices:
            raise RuntimeError(
                'AnalogOutputLogic: no analog output devices connected. '
                'Wire up at least one of ao1..ao{0} in the connect: block '
                'of this module\'s config.'.format(_MAX_AO_DEVICES)
            )

        self.log.info(
            'AnalogOutputLogic: {0} device(s) connected: {1}'.format(
                len(self._devices), [name for name, _ in self._devices]
            )
        )

        self._target_setpoint = dict()
        self._old_activity = dict()

        for device_name, instance in self._devices:
            for channel in instance.constraints.setpoint_channels:
                key = (device_name, channel)
                try:
                    active = instance.get_activity_state(channel)
                except Exception:
                    active = False

                if active:
                    try:
                        self._target_setpoint[key] = instance.get_setpoint(channel)
                    except Exception:
                        self._target_setpoint[key] = 0.0
                else:
                    # The public ProcessSetpointInterface API only allows
                    # querying the setpoint of an ACTIVE channel -- there is
                    # no public method to ask "what would this channel
                    # output if it were turned on". As a best-effort
                    # fallback (NOT a hardware file change -- purely reading
                    # a value from outside), we look at the hardware
                    # module's own persisted setpoint dictionary directly.
                    # If this fails for any reason, default to 0.0.
                    try:
                        self._target_setpoint[key] = float(instance._setpoints.get(channel, 0.0))
                    except Exception:
                        self._target_setpoint[key] = 0.0

                self._old_activity[key] = active

        self._watchdog_interval_ms = int(round(self._watchdog_interval * 1000))

        if self._autostart_watchdog:
            self._watchdog_active = True
            QtCore.QMetaObject.invokeMethod(self, '_watchdog_body', QtCore.Qt.ConnectionType.QueuedConnection)
        else:
            self._watchdog_active = False

    def on_deactivate(self):
        """ Deactivate module """
        self._watchdog_active = False

    def _get_device(self, device_name):
        """ Look up a connected device instance by its qudi module name.

        @param str device_name: One of the names in self.device_names.
        @return object: The connected hardware instance.
        """
        try:
            return self._device_by_name[device_name]
        except KeyError:
            raise ValueError(
                f'Invalid device_name "{device_name}". Connected devices '
                f'are: {self.device_names}'
            ) from None

    @property
    def device_names(self):
        """ Names of all connected analog output devices, in connect:
        declaration order.

        @return tuple: Device name strings, e.g. ('daq_1', 'daq_2').
        """
        return tuple(name for name, _ in self._devices)

    def channels_for_device(self, device_name):
        """ Names of all configured channels for one connected device.

        @param str device_name: One of the names in self.device_names.
        @return tuple: Channel name strings, e.g. ('ao0', 'ao1', 'ao2', 'ao3').
        """
        return tuple(self._get_device(device_name).constraints.setpoint_channels)

    @property
    def watchdog_active(self):
        return self._watchdog_active

    # =========================================================================
    # Limits
    # =========================================================================

    def get_limits(self, device_name, channel):
        """ Return (min, max) allowed voltage for the given device/channel.

        @param str device_name: One of the names in self.device_names.
        @param str channel: Channel name, e.g. 'ao0'.
        @return tuple: (min_voltage, max_voltage) in volts.
        """
        with self._thread_lock:
            try:
                return self._get_device(device_name).constraints.channel_limits[channel]
            except Exception:
                self.log.exception(
                    f'Error while querying voltage limits for channel '
                    f'"{channel}" on device "{device_name}".'
                )
                return (0.0, 0.0)

    # =========================================================================
    # Getters
    # =========================================================================

    def get_setpoint(self, device_name, channel):
        """ Return the current TARGET voltage for the given device/channel --
        i.e. the value that either is currently being output (if the channel
        is on) or will be output the next time it is turned on (if currently
        off). This is a purely local value, not a live hardware query.

        @param str device_name: One of the names in self.device_names.
        @param str channel: Channel name, e.g. 'ao0'.
        @return float: Target voltage in volts.
        """
        with self._thread_lock:
            return self._target_setpoint.get((device_name, channel), 0.0)

    def get_activity_state(self, device_name, channel):
        """ Return whether the given device/channel is currently active
        (output enabled).

        @param str device_name: One of the names in self.device_names.
        @param str channel: Channel name, e.g. 'ao0'.
        @return bool: True if active, False if inactive.
        """
        with self._thread_lock:
            try:
                return self._get_device(device_name).get_activity_state(channel)
            except Exception:
                self._handle_query_error(
                    f'activity state of channel "{channel}" on device "{device_name}"'
                )
                return None

    def get_all_setpoints(self):
        """ @return dict: {(device_name, channel): target_voltage} for every
        connected device/channel.
        """
        return {
            (device_name, channel): self.get_setpoint(device_name, channel)
            for device_name in self.device_names
            for channel in self.channels_for_device(device_name)
        }

    def get_all_activity_states(self):
        """ @return dict: {(device_name, channel): active} for every
        connected device/channel.
        """
        return {
            (device_name, channel): self.get_activity_state(device_name, channel)
            for device_name in self.device_names
            for channel in self.channels_for_device(device_name)
        }

    def _handle_query_error(self, what):
        """ Shared error handling for read-only query methods: deactivates the
        watchdog (to avoid repeated errors on a broken connection) and logs.
        """
        if self._watchdog_active:
            self.toggle_watchdog(False)
            self.log.exception(f'Error while querying {what}. Deactivating watchdog to avoid constant errors.')
        else:
            self.log.exception(f'Error while querying {what}.')

    # =========================================================================
    # Setters
    # =========================================================================

    @QtCore.Slot(str, str, float)
    def set_setpoint(self, device_name, channel, value):
        """ Update the target voltage for the given device/channel. Never
        changes activation state.

        If the channel is currently ON, the new value is ALSO written to
        hardware immediately -- this is the "on the fly" behavior: typing a
        new number while the output is live changes the physical voltage
        right away.

        If the channel is currently OFF, only the local target is updated --
        nothing is written to hardware, and the channel stays off. The new
        value will be applied the next time the channel is turned on.

        @param str device_name: One of the names in self.device_names.
        @param str channel: Channel name, e.g. 'ao0'.
        @param float value: Desired target voltage in volts.
        """
        value = float(value)
        with self._thread_lock:
            key = (device_name, channel)
            self._target_setpoint[key] = value

            is_active = self.get_activity_state(device_name, channel)
            if is_active:
                try:
                    self._get_device(device_name).set_setpoint(channel, value)
                except Exception:
                    self.log.exception(
                        f'Error while setting voltage of channel "{channel}" '
                        f'on device "{device_name}" to {value}.'
                    )
                    return

            self.sigSetpointChanged.emit(device_name, channel, value)

    @QtCore.Slot(str, str, bool)
    def set_activity_state(self, device_name, channel, active):
        """ Turn the physical output on or off for the given device/channel.

        Turning ON: activates the hardware channel, then writes the current
        target voltage to it immediately.

        Turning OFF: explicitly writes 0 V (while still active) BEFORE
        deactivating, guaranteeing "off" always physically means 0 V
        regardless of the hardware module's 'keep_value' setting. The
        target voltage is left completely untouched -- it is NOT zeroed,
        so it is ready to be reapplied the next time this channel is
        turned back on.

        @param str device_name: One of the names in self.device_names.
        @param str channel: Channel name, e.g. 'ao0'.
        @param bool active: True to turn output on, False to turn it off.
        """
        active = bool(active)
        with self._thread_lock:
            device = self._get_device(device_name)
            key = (device_name, channel)

            if active:
                try:
                    device.set_activity_state(channel, True)
                except Exception:
                    self.log.exception(
                        f'Error while activating channel "{channel}" on '
                        f'device "{device_name}".'
                    )
                    return

                target = self._target_setpoint.get(key, 0.0)
                try:
                    device.set_setpoint(channel, target)
                except Exception:
                    self.log.exception(
                        f'Error while applying target voltage {target} to '
                        f'channel "{channel}" on device "{device_name}" '
                        f'after activation.'
                    )

                self.sigActivityChanged.emit(device_name, channel, True)

            else:
                try:
                    if device.get_activity_state(channel):
                        device.set_setpoint(channel, 0.0)
                    device.set_activity_state(channel, False)
                except Exception:
                    self.log.exception(
                        f'Error while deactivating channel "{channel}" on '
                        f'device "{device_name}".'
                    )
                    return

                self.sigActivityChanged.emit(device_name, channel, False)
                # NOTE: self._target_setpoint is intentionally left
                # untouched here -- see method docstring above.

    # =========================================================================
    # Watchdog
    # =========================================================================

    @QtCore.Slot(bool)
    def toggle_watchdog(self, enable):
        enable = bool(enable)
        with self._thread_lock:
            if enable != self._watchdog_active:
                self._watchdog_active = enable
                self.sigWatchdogToggled.emit(enable)
                if enable:
                    QtCore.QMetaObject.invokeMethod(self,
                                                    '_watchdog_body',
                                                    QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.Slot()
    def _watchdog_body(self):
        """ Regularly poll activity state for every channel of every
        connected device, and for currently-active channels, also poll the
        live hardware setpoint and compare it to the locally-tracked target
        -- catches changes made outside of this logic module (e.g. directly
        via a script or another qudi session) and updates the target/GUI to
        match.

        Inactive channels are not polled for voltage (the hardware module
        cannot report a setpoint for an inactive channel), so their target
        value is left as whatever was last known locally.
        """
        with self._thread_lock:
            if self._watchdog_active:
                curr_activity = self.get_all_activity_states()

                for (device_name, channel), active in curr_activity.items():
                    key = (device_name, channel)
                    if active is not None and active != self._old_activity.get(key):
                        self.sigActivityChanged.emit(device_name, channel, active)

                    if active:
                        try:
                            hw_value = self._get_device(device_name).get_setpoint(channel)
                        except Exception:
                            continue
                        if hw_value != self._target_setpoint.get(key):
                            self._target_setpoint[key] = hw_value
                            self.sigSetpointChanged.emit(device_name, channel, hw_value)

                self._old_activity = curr_activity

                QtCore.QTimer.singleShot(self._watchdog_interval_ms, self._watchdog_body)