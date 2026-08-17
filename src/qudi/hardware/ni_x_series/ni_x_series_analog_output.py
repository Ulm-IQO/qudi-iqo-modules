# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module to use a National Instruments X-series card as mixed
signal input data streamer.

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

# ToDo: Handle case where zero volts is not a good default value

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
    """ A module to output and read back (internally routed) analog voltages
    on an Ni Card in a software timed fashion.
    (only tested with Ni X-Series cards so far)

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

    _setpoints = StatusVar(name='current_setpoints', default=dict())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()

        self._constraints = None
        self._device_channel_mapping = dict()
        self._ao_task_handles = dict()
        self._keep_values = dict()

    def on_activate(self):
        """ Starts up the NI-card and performs sanity checks """
        # Check if device is connected and set device to use
        self._device_channel_mapping = dict()
        self._ao_task_handles = dict()
        self._keep_values = dict()

        # Sanitize channel configuration
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

        # Initialization of hardware constraints defined in the config file
        self._constraints = ProcessControlConstraints(
            setpoint_channels=self._device_channel_mapping,
            units={ch: 'V' for ch in self._device_channel_mapping},
            limits=limits,
            dtypes={ch: float for ch in self._device_channel_mapping}
        )

        # Sanitize status variables
        self._sanitize_setpoint_status()

    def on_deactivate(self):
        for channel in list(self._ao_task_handles):
            try:
                self._terminate_ao_task(channel)
            except:
                self.log.exception(f'Error while terminating NI analog out task "{channel}":')

    @property
    def constraints(self) -> ProcessControlConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._constraints

    def set_activity_state(self, channel: str, active: bool) -> None:
        """ Set activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        try:
            active = bool(active)
        except Exception as err:
            raise TypeError('Unable to convert activity state to bool') from err
        with self._thread_lock:
            try:
                current_state = self._get_activity_state(channel)
            except KeyError as err:
                raise ValueError(f'Invalid channel specifier "{channel}". Valid channels are:\n'
                                 f'{self.constraints.all_channels}') from err
            if active != current_state:
                try:
                    if active:
                        self._create_ao_task(channel)
                        self._write_ao_value(channel, self._setpoints.get(channel, 0))
                    else:
                        self._terminate_ao_task(channel)
                finally:
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
        return channel in self._ao_task_handles

    def _update_module_state(self) -> None:
        busy = len(self._ao_task_handles) > 0
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

    def _terminate_ao_task(self, channel: str) -> None:
        """ Reset analog output to 0 if keep_values flag is not set """
        try:
            if not self._keep_values[channel]:
                self._write_ao_value(0)
            task = self._ao_task_handles.pop(channel)
        except KeyError:
            return
        try:
            if not task.is_task_done():
                task.stop()
        finally:
            task.close()

    def _create_ao_task(self, channel: str) -> None:
        if channel in self._ao_task_handles:
            raise ValueError(f'AO task with name "{channel}" already present.')
        try:
            ao_task = ni.Task(channel)
        except ni.DaqError as err:
            raise RuntimeError(f'Unable to create NI task "{channel}"') from err
        try:
            ao_phys_ch = f'/{self._device_name}/{self._device_channel_mapping[channel]}'
            min_val, max_val = self.constraints.channel_limits[channel]
            ao_task.ao_channels.add_ao_voltage_chan(physical_channel=ao_phys_ch,
                                                    min_val=min_val,
                                                    max_val=max_val)
        except Exception as err:
            try:
                ao_task.close()
            except ni.DaqError:
                pass
            raise RuntimeError('Error while configuring NI analog out task') from err
        self._ao_task_handles[channel] = ao_task

    def _write_ao_value(self, channel: str, value: float) -> None:
        self._ao_task_handles[channel].write(value)

    def _sanitize_setpoint_status(self) -> None:
        # Remove obsolete channels and out-of-bounds values
        for channel, value in list(self._setpoints.items()):
            try:
                if not self.constraints.channel_value_in_range(channel, value)[0]:
                    del self._setpoints[channel]
            except KeyError:
                del self._setpoints[channel]
        # Add missing setpoint channels and set initial value to zero
        self._setpoints.update(
            {ch: 0 for ch in self.constraints.setpoint_channels if ch not in self._setpoints}
        )
