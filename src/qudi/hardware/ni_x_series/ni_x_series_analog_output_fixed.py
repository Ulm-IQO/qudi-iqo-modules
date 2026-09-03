# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module to control the analog outputs
of a National Instruments X-series card.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import nidaqmx as ni

from qudi.util.mutex import Mutex
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.helpers import natural_sort, in_range

from qudi.interface.process_control_interface import ProcessControlConstraints
from qudi.interface.process_control_interface import ProcessSetpointInterface
from qudi.interface.mixins.process_control_switch import ProcessControlSwitchMixin
from qudi.hardware.ni_x_series.helpers import sanitize_device_name, normalize_channel_name
from qudi.hardware.ni_x_series.helpers import ao_channel_names, ao_voltage_range


class NIXSeriesAnalogOutput(ProcessControlSwitchMixin, ProcessSetpointInterface):
    """ A module to output analog voltages on a NI Card.

    Every voltage write opens a brand-new on-demand NI task, writes one
    value, and immediately closes the task again -- no task is ever kept
    open between writes. This matters because once an on-demand AO task is
    closed, the physical DAC simply keeps outputting whatever was last
    written to it, indefinitely, with no task/module/process needing to
    stay alive for that to remain true. This means the outputs survive
    Qudi being closed and reopened, and even the whole PC being logged out,
    unless something else (e.g. a power cycle, or 'keep_value: False' below)
    resets them.

    Since Qudi itself is not running continuously between sessions, this
    module can't ask the hardware "what are you currently outputting" --
    the NI API has no such query for AO. So the last-known setpoint and
    on/off state are instead remembered in a status file and restored on
    the next startup, purely so the GUI shows the correct picture again.
    This never touches hardware -- the voltage was already sitting there
    the whole time.

    Example config for copy-paste:

    nicard_63XX_ao:
        module.Class: 'ni_x_series.ni_x_series_analog_output.NIXSeriesAnalogOutput'
        options:
            device_name: 'Dev1'
            channels:
                ao0:
                    limits: [-10.0, 10.0]
                    keep_value: True
                ao1:
                    limits: [-10.0, 10.0]
                    keep_value: True
                ao2:
                    limits: [-10.0, 10.0]
                    keep_value: True
                ao3:
                    limits: [-10.0, 10.0]
                    keep_value: True
    """
    _device_name = ConfigOption(name='device_name',
                                default='Dev1',
                                missing='warn',
                                constructor=sanitize_device_name)
    _channels_config = ConfigOption(
        name='channels',
        default={
            'ao0': {'limits': (-10.0, 10.0), 'keep_value': True},
            'ao1': {'limits': (-10.0, 10.0), 'keep_value': True},
            'ao2': {'limits': (-10.0, 10.0), 'keep_value': True},
            'ao3': {'limits': (-10.0, 10.0), 'keep_value': True}
        },
        missing='warn'
    )

    # Last known setpoint per channel, persisted across restarts (GUI display only).
    _setpoints = StatusVar(name='current_setpoints', default=dict())
    # Last known on/off state per channel, persisted across restarts (GUI display only).
    _active_channels = StatusVar(name='active_channels', default=dict())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()

        self._constraints = None
        # Maps configured channel name (e.g. 'ao0') to the actual device
        # channel name reported by NI-DAQmx.
        self._device_channel_mapping = dict()
        # Whether each channel should keep outputting its last voltage
        # (True) or drop to 0 V (False) when turned off.
        self._keep_values = dict()

    def on_activate(self):
        """ Set up channel mapping, limits and constraints. Does not write
        anything to hardware -- the physical outputs are left exactly as
        they were, whatever that may be.
        """
        self._device_channel_mapping = dict()
        self._keep_values = dict()

        ao_limits = ao_voltage_range(self._device_name)
        valid_channels = ao_channel_names(self._device_name)
        valid_channels_lower = [name.lower() for name in valid_channels]
        limits = dict()
        for ch_name in natural_sort(self._channels_config):
            ch_cfg = self._channels_config[ch_name]
            norm_name = normalize_channel_name(ch_name).lower()
            try:
                device_name = valid_channels[valid_channels_lower.index(norm_name)]
            except (ValueError, IndexError):
                self.log.error(f'Invalid analog output channel "{ch_name}" configured. Channel '
                               f'will be ignored.\nValid analog output channels are: '
                               f'{valid_channels}')
                continue
            try:
                ch_limits = ch_cfg['limits']
            except KeyError:
                ch_limits = ao_limits
            else:
                if not all(in_range(lim, *ao_limits)[0] for lim in ch_limits):
                    self.log.error(
                        f'Invalid analog output voltage limits {ch_limits} configured for channel '
                        f'"{ch_name}". Channel will be ignored.\nValid analog output limits must '
                        f'lie in range {ao_limits}'
                    )
                    continue
            self._device_channel_mapping[ch_name] = device_name
            self._keep_values[ch_name] = bool(ch_cfg.get('keep_value', True))
            limits[ch_name] = ch_limits

        self._constraints = ProcessControlConstraints(
            setpoint_channels=self._device_channel_mapping,
            units={ch: 'V' for ch in self._device_channel_mapping},
            limits=limits,
            dtypes={ch: float for ch in self._device_channel_mapping}
        )

        self._sanitize_setpoint_status()
        self._sanitize_activity_status()

    def on_deactivate(self):
        """ Nothing to clean up -- no task is ever left open, and the
        physical outputs are intentionally left untouched so they keep
        outputting their last values.
        """
        pass

    @property
    def constraints(self) -> ProcessControlConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._constraints

    def set_activity_state(self, channel: str, active: bool) -> None:
        """ Turn a channel on or off.

        Turning on writes the last known setpoint to hardware.
        Turning off writes 0 V only if keep_value is False for this
        channel; otherwise the hardware is left alone and keeps outputting
        its last voltage.
        """
        try:
            active = bool(active)
        except Exception as err:
            raise TypeError('Unable to convert activity state to bool') from err
        with self._thread_lock:
            if channel not in self.constraints.all_channels:
                raise ValueError(f'Invalid channel specifier "{channel}". Valid channels are:\n'
                                 f'{self.constraints.all_channels}')
            current_state = self._active_channels.get(channel, False)
            if active != current_state:
                if active:
                    self._write_ao_value(channel, self._setpoints.get(channel, 0.0))
                elif not self._keep_values.get(channel, True):
                    self._write_ao_value(channel, 0.0)
                self._active_channels[channel] = active
                self._update_module_state()

    def get_activity_state(self, channel: str) -> bool:
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        with self._thread_lock:
            return self._get_activity_state(channel)

    def _get_activity_state(self, channel: str) -> bool:
        if channel not in self.constraints.all_channels:
            raise ValueError(f'Invalid channel specifier "{channel}". Valid channels are:\n'
                             f'{self.constraints.all_channels}')
        return self._active_channels.get(channel, False)

    def _update_module_state(self) -> None:
        busy = any(self._active_channels.values())
        if busy and self.module_state() != 'locked':
            self.module_state.lock()
        elif not busy and self.module_state() == 'locked':
            self.module_state.unlock()

    def set_setpoint(self, channel: str, value: float) -> None:
        """ Set new setpoint for a single channel """
        value = float(value)
        with self._thread_lock:
            if not self._get_activity_state(channel):
                raise RuntimeError(f'Please activate channel "{channel}" before setting setpoint')
            if not self.constraints.channel_value_in_range(channel, value)[0]:
                raise ValueError(f'Setpoint {value} for channel "{channel}" out of allowed '
                                 f'value bounds {self.constraints.channel_limits[channel]}')
            self._write_ao_value(channel, value)
            self._setpoints[channel] = value

    def get_setpoint(self, channel: str) -> float:
        """ Get current setpoint for a single channel """
        with self._thread_lock:
            if not self._get_activity_state(channel):
                raise RuntimeError(f'Please activate channel "{channel}" before getting setpoint')
            return self._setpoints[channel]

    def _write_ao_value(self, channel: str, value: float) -> None:
        """ Open a fresh task, write one voltage value, close the task.
        Once closed, the DAC keeps outputting this value by itself.
        """
        ao_phys_ch = f'/{self._device_name}/{self._device_channel_mapping[channel]}'
        min_val, max_val = self.constraints.channel_limits[channel]
        with ni.Task() as task:
            task.ao_channels.add_ao_voltage_chan(physical_channel=ao_phys_ch,
                                                 min_val=min_val,
                                                 max_val=max_val)
            task.write(value, auto_start=True)
        # task is automatically closed here (end of `with` block)

    def _sanitize_setpoint_status(self) -> None:
        """ Drop stale/out-of-range setpoints, fill in missing ones with 0. """
        for channel, value in list(self._setpoints.items()):
            try:
                if not self.constraints.channel_value_in_range(channel, value)[0]:
                    del self._setpoints[channel]
            except KeyError:
                del self._setpoints[channel]
        self._setpoints.update(
            {ch: 0 for ch in self.constraints.setpoint_channels if ch not in self._setpoints}
        )

    def _sanitize_activity_status(self) -> None:
        """ Drop stale channels, default any new/unknown ones to inactive. """
        for channel in list(self._active_channels):
            if channel not in self.constraints.setpoint_channels:
                del self._active_channels[channel]
        self._active_channels.update(
            {ch: False for ch in self.constraints.setpoint_channels
             if ch not in self._active_channels}
        )