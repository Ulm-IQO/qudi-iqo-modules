# -*- coding: utf-8 -*-
"""
Interact with an analog output hardware module (per-channel voltage setpoint and
activity/output state), e.g. NIXSeriesAnalogOutput.

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

Wraps any hardware module implementing qudi's ProcessSetpointInterface (with the
ProcessControlSwitchMixin activity-state methods) -- e.g. NIXSeriesAnalogOutput -- for
GUI use.

KEY DESIGN: "target setpoint" vs. "hardware activation" are fully decoupled.

    - self._target_setpoint[channel] is the value the user wants -- what is
      shown in the GUI's voltage field. It can be changed at ANY time, whether
      the channel is currently on or off, and changing it NEVER by itself
      activates or deactivates anything.

    - set_setpoint(channel, value) updates the target. If the channel happens
      to be ON at that moment, the new value is ALSO written to hardware
      immediately (true "on the fly" updates while live). If the channel is
      OFF, nothing is written to hardware -- the new target is simply
      remembered for whenever the channel is next turned on.

    - set_activity_state(channel, True) turns the physical output on: it
      activates the hardware channel and then writes the current target
      value to it.

    - set_activity_state(channel, False) turns the physical output off: it
      explicitly writes 0 V (while still active) and THEN deactivates the
      channel, guaranteeing "off" always means 0 V regardless of the
      hardware module's own 'keep_value' config option (which would
      otherwise leave the DAC holding its last voltage indefinitely after
      simply closing the task). The target value itself is left completely
      untouched by this -- it is not zeroed, so it is ready to be reapplied
      the next time the channel is turned on.

This means: on/off purely controls "output the target value" vs. "output 0",
and the target value can be edited freely at any time without side effects on
activation state -- matching a plain "voltage dial + power switch" mental model.

A background watchdog periodically polls activity state and (for active
channels only) the live hardware setpoint, catching external changes made
outside of this logic module (e.g. via a script, the console, or another
qudi session) and updating the target/GUI to match.
"""

from PySide6 import QtCore

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.interface.process_control_interface import ProcessSetpointInterface


class AnalogOutputLogic(LogicBase):
    """ Logic module for interacting with an analog output hardware module
    (per-channel voltage setpoint and activity/output state).

    analog_output_logic:
        module.Class: 'analog_output_logic.AnalogOutputLogic'
        options:
            watchdog_interval: 1  # optional
            autostart_watchdog: True  # optional
        connect:
            ao: <analog output hardware module name>
    """

    ao = Connector(interface=ProcessSetpointInterface)

    _watchdog_interval = ConfigOption(name='watchdog_interval', default=1.0, missing='nothing')
    _autostart_watchdog = ConfigOption(name='autostart_watchdog', default=False, missing='nothing')

    sigSetpointChanged = QtCore.Signal(str, float)
    sigActivityChanged = QtCore.Signal(str, bool)
    sigWatchdogToggled = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        self._watchdog_active = False
        self._watchdog_interval_ms = 0

        # The "target" voltage per channel -- what the user wants, shown in
        # the GUI, independent of whether the channel is currently on or off.
        self._target_setpoint = dict()
        self._old_activity = dict()

    def on_activate(self):
        """ Activate module. Initializes the target setpoint for each channel
        from the hardware's live value if the channel happens to already be
        active, or falls back to the hardware module's own remembered
        setpoint otherwise (best-effort; see comment below), so the
        displayed target survives a Qudi restart even for channels that are
        currently off.
        """
        for channel in self.channels:
            try:
                active = self.ao().get_activity_state(channel)
            except Exception:
                active = False

            if active:
                try:
                    self._target_setpoint[channel] = self.ao().get_setpoint(channel)
                except Exception:
                    self._target_setpoint[channel] = 0.0
            else:
                # The public ProcessSetpointInterface API only allows
                # querying the setpoint of an ACTIVE channel -- there is no
                # public method to ask "what would this channel output if
                # it were turned on". As a best-effort fallback (NOT a
                # hardware file change -- purely reading a value from
                # outside), we look at the hardware module's own persisted
                # setpoint dictionary directly. If this fails for any
                # reason, default to 0.0.
                try:
                    self._target_setpoint[channel] = float(self.ao()._setpoints.get(channel, 0.0))
                except Exception:
                    self._target_setpoint[channel] = 0.0

            self._old_activity[channel] = active

        self._watchdog_interval_ms = int(round(self._watchdog_interval * 1000))

        if self._autostart_watchdog:
            self._watchdog_active = True
            QtCore.QMetaObject.invokeMethod(self, '_watchdog_body', QtCore.Qt.ConnectionType.QueuedConnection)
        else:
            self._watchdog_active = False

    def on_deactivate(self):
        """ Deactivate module """
        self._watchdog_active = False

    @property
    def channels(self):
        """ Names of all configured analog output channels.

        @return tuple: Channel name strings, e.g. ('ao0', 'ao1', 'ao2', 'ao3').
        """
        return tuple(self.ao().constraints.setpoint_channels)

    @property
    def watchdog_active(self):
        return self._watchdog_active

    # =========================================================================
    # Limits
    # =========================================================================

    def get_limits(self, channel):
        """ Return (min, max) allowed voltage for the given channel.

        @param str channel: Channel name, e.g. 'ao0'.
        @return tuple: (min_voltage, max_voltage) in volts.
        """
        with self._thread_lock:
            try:
                return self.ao().constraints.channel_limits[channel]
            except Exception:
                self.log.exception(f'Error while querying voltage limits for channel "{channel}".')
                return (0.0, 0.0)

    # =========================================================================
    # Getters
    # =========================================================================

    def get_setpoint(self, channel):
        """ Return the current TARGET voltage for the given channel -- i.e.
        the value that either is currently being output (if the channel is
        on) or will be output the next time it is turned on (if currently
        off). This is a purely local value, not a live hardware query.

        @param str channel: Channel name, e.g. 'ao0'.
        @return float: Target voltage in volts.
        """
        with self._thread_lock:
            return self._target_setpoint.get(channel, 0.0)

    def get_activity_state(self, channel):
        """ Return whether the given channel is currently active (output enabled).

        @param str channel: Channel name, e.g. 'ao0'.
        @return bool: True if active, False if inactive.
        """
        with self._thread_lock:
            try:
                return self.ao().get_activity_state(channel)
            except Exception:
                self._handle_query_error(f'activity state of channel "{channel}"')
                return None

    def get_all_setpoints(self):
        return {channel: self.get_setpoint(channel) for channel in self.channels}

    def get_all_activity_states(self):
        return {channel: self.get_activity_state(channel) for channel in self.channels}

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

    @QtCore.Slot(str, float)
    def set_setpoint(self, channel, value):
        """ Update the target voltage for the given channel. Never changes
        activation state.

        If the channel is currently ON, the new value is ALSO written to
        hardware immediately -- this is the "on the fly" behavior: typing a
        new number while the output is live changes the physical voltage
        right away.

        If the channel is currently OFF, only the local target is updated --
        nothing is written to hardware, and the channel stays off. The new
        value will be applied the next time the channel is turned on.

        @param str channel: Channel name, e.g. 'ao0'.
        @param float value: Desired target voltage in volts.
        """
        value = float(value)
        with self._thread_lock:
            self._target_setpoint[channel] = value

            is_active = self.get_activity_state(channel)
            if is_active:
                try:
                    self.ao().set_setpoint(channel, value)
                except Exception:
                    self.log.exception(f'Error while setting voltage of channel "{channel}" to {value}.')
                    return

            self.sigSetpointChanged.emit(channel, value)

    @QtCore.Slot(str, bool)
    def set_activity_state(self, channel, active):
        """ Turn the physical output on or off for the given channel.

        Turning ON: activates the hardware channel, then writes the current
        target voltage to it immediately.

        Turning OFF: explicitly writes 0 V (while still active) BEFORE
        deactivating, guaranteeing "off" always physically means 0 V
        regardless of the hardware module's 'keep_value' setting. The
        target voltage is left completely untouched -- it is NOT zeroed,
        so it is ready to be reapplied the next time this channel is
        turned back on.

        @param str channel: Channel name, e.g. 'ao0'.
        @param bool active: True to turn output on, False to turn it off.
        """
        active = bool(active)
        with self._thread_lock:
            if active:
                try:
                    self.ao().set_activity_state(channel, True)
                except Exception:
                    self.log.exception(f'Error while activating channel "{channel}".')
                    return

                target = self._target_setpoint.get(channel, 0.0)
                try:
                    self.ao().set_setpoint(channel, target)
                except Exception:
                    self.log.exception(
                        f'Error while applying target voltage {target} to channel '
                        f'"{channel}" after activation.'
                    )

                self.sigActivityChanged.emit(channel, True)

            else:
                try:
                    if self.ao().get_activity_state(channel):
                        self.ao().set_setpoint(channel, 0.0)
                    self.ao().set_activity_state(channel, False)
                except Exception:
                    self.log.exception(f'Error while deactivating channel "{channel}".')
                    return

                self.sigActivityChanged.emit(channel, False)
                # NOTE: self._target_setpoint is intentionally left untouched
                # here -- see method docstring above.

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
        """ Regularly poll activity state for all channels, and for
        currently-active channels, also poll the live hardware setpoint and
        compare it to the locally-tracked target -- catches changes made
        outside of this logic module (e.g. directly via a script or another
        qudi session) and updates the target/GUI to match.

        Inactive channels are not polled for voltage (the hardware module
        cannot report a setpoint for an inactive channel), so their target
        value is left as whatever was last known locally.
        """
        with self._thread_lock:
            if self._watchdog_active:
                curr_activity = self.get_all_activity_states()

                for channel, active in curr_activity.items():
                    if active is not None and active != self._old_activity.get(channel):
                        self.sigActivityChanged.emit(channel, active)

                    if active:
                        try:
                            hw_value = self.ao().get_setpoint(channel)
                        except Exception:
                            continue
                        if hw_value != self._target_setpoint.get(channel):
                            self._target_setpoint[channel] = hw_value
                            self.sigSetpointChanged.emit(channel, hw_value)

                self._old_activity = curr_activity

                QtCore.QTimer.singleShot(self._watchdog_interval_ms, self._watchdog_body)