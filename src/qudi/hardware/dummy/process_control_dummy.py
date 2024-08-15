# -*- coding: utf-8 -*-

"""
This file contains the Qudi dummy hardware file to mimic a simple process control device via
ProcessControlInterface.

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
"""

import time
import numpy as np
from typing import Union

from qudi.util.mutex import Mutex
from qudi.core.configoption import ConfigOption
from qudi.interface.process_control_interface import ProcessControlConstraints
from qudi.interface.process_control_interface import ProcessControlInterface
from qudi.interface.mixins.process_control_switch import ProcessControlSwitchMixin


class ProcessControlDummy(ProcessControlSwitchMixin, ProcessControlInterface):
    """ A dummy class to emulate a process control device (setpoints and process values)

    Example config for copy-paste:

    process_control_dummy:
        module.Class: 'dummy.process_control_dummy.ProcessControlDummy'
        options:
            process_value_channels:
                Temperature:
                    unit: 'K'
                    limits: [0, .inf]
                    dtype: float
                Voltage:
                    unit: 'V'
                    limits: [-10.0, 10.0]
                    dtype: float
            setpoint_channels:
                Current:
                    unit: 'A'
                    limits: [-5, 5]
                    dtype: float
                Frequency:
                    unit: 'Hz'
                    limits: [100.0e3, 20.0e9]
                    dtype: float
            linear_dependency:
                process_value_channel: 'Temperature'
                setpoint_channel: 'Current'
                slope: 10
                offset: 100
                noise: 0.1
    """

    _setpoint_channels = ConfigOption(
        name='setpoint_channels',
        default={'Current': {'unit': 'A', 'limits': (-5, 5), 'dtype': float},
                 'Frequency': {'unit': 'Hz', 'limits': (100.0e3, 20.0e9), 'dtype': float}}
    )
    _process_value_channels = ConfigOption(
        name='process_value_channels',
        default={'Temperature': {'unit': 'K', 'limits': (0, np.inf), 'dtype': float},
                 'Voltage': {'unit': 'V', 'limits': (-10.0, 10.0), 'dtype': float}}
    )
    _linear_dependency = ConfigOption(
        name='linear_dependency',
        default={'process_value_channel': 'Temperature',
                 'setpoint_channel': 'Current',
                 'slope': 10,
                 'offset': 100,
                 'noise': 0.1}
    )

    @staticmethod
    @_process_value_channels.constructor
    @_setpoint_channels.constructor
    def _construct_channels_config(cfg_opt):
        for channel_cfg in cfg_opt.values():
            dtype_str = channel_cfg.get('dtype', '')
            if dtype_str == 'float':
                channel_cfg['dtype'] = float
            elif dtype_str == 'int':
                channel_cfg['dtype'] = int
        return cfg_opt

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()

        self._setpoints = dict()
        self._activity_states = dict()
        self._constraints = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        units = {ch: d['unit'] for ch, d in self._setpoint_channels.items() if 'unit' in d}
        units.update(
            {ch: d['unit'] for ch, d in self._process_value_channels.items() if 'unit' in d}
        )
        limits = {ch: d['limits'] for ch, d in self._setpoint_channels.items() if 'limits' in d}
        limits.update(
            {ch: d['limits'] for ch, d in self._process_value_channels.items() if 'limits' in d}
        )
        dtypes = {ch: d['dtype'] for ch, d in self._setpoint_channels.items() if 'dtype' in d}
        dtypes.update(
            {ch: d['dtype'] for ch, d in self._process_value_channels.items() if 'dtype' in d}
        )
        self._constraints = ProcessControlConstraints(
            setpoint_channels=self._setpoint_channels,
            process_channels=self._process_value_channels,
            units=units,
            limits=limits,
            dtypes=dtypes
        )
        self._setpoints = {ch: min(max(lim[0], 0), lim[1]) for ch, lim in
                           self._constraints.channel_limits.items() if
                           ch in self._setpoint_channels}
        self._activity_states = {ch: False for ch in self._constraints.all_channels}

    def on_deactivate(self):
        self._activity_states = {ch: False for ch in self.constraints.all_channels}

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
                time.sleep(0.5)
                self._activity_states[channel] = active
                self._update_module_state()

    def _update_module_state(self) -> None:
        module_busy = any(state for state in self._activity_states.values())
        if module_busy and self.module_state() != 'locked':
            self.module_state.lock()
        elif not module_busy and self.module_state() == 'locked':
            self.module_state.unlock()

    def get_activity_state(self, channel: str) -> bool:
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        with self._thread_lock:
            return self._get_activity_state(channel)

    def _get_activity_state(self, channel: str) -> bool:
        try:
            return self._activity_states[channel]
        except KeyError as err:
            raise ValueError(f'Invalid channel specifier "{channel}". Valid channels are:\n'
                             f'{self.constraints.all_channels}') from err

    def get_process_value(self, channel: str) -> Union[int, float]:
        """ Get current process value for a single channel """
        with self._thread_lock:
            try:
                min_val, max_val = self.constraints.channel_limits[channel]
            except KeyError as err:
                raise ValueError(f'Invalid process channel specifier "{channel}". Valid process '
                                 f'channels are:\n{self.constraints.process_channels}') from err

            # check if a dependency of process value should be simulated
            if channel == self._linear_dependency['process_value_channel']:
                setpoint = self._setpoints[self._linear_dependency['setpoint_channel']]
                value = self._linear_dependency['offset'] + setpoint * self._linear_dependency['slope']
                value *= 1 + np.random.rand() * self._linear_dependency['noise']
                if value < min_val:
                    return min_val
                elif value > max_val:
                    return max_val
                else:
                    return value

            # otherwise return random sample from allowed value range
            if np.isinf(min_val):
                min_val = -1000
            if np.isinf(max_val):
                max_val = 1000
            value_span = max_val - min_val
            return min_val + np.random.rand() * value_span

    def set_setpoint(self, channel: str, value: Union[int, float]) -> None:
        """ Set new setpoint for a single channel """
        with self._thread_lock:
            try:
                if not self.constraints.channel_value_in_range(channel, value)[0]:
                    raise ValueError(f'Setpoint {value} for channel "{channel}" out of allowed '
                                     f'value bounds {self.constraints.channel_limits[channel]}')
                self._setpoints[channel] = self.constraints.channel_dtypes[channel](value)
            except KeyError as err:
                raise ValueError(f'Invalid setpoint channel specifier "{channel}". Valid setpoint '
                                 f'channels are:\n{tuple(self._setpoints)}') from err

    def get_setpoint(self, channel: str) -> Union[int, float]:
        """ Get current setpoint for a single channel """
        with self._thread_lock:
            try:
                return self._setpoints[channel]
            except KeyError as err:
                raise ValueError(f'Invalid setpoint channel specifier "{channel}". Valid setpoint '
                                 f'channels are:\n{tuple(self._setpoints)}') from err


class ProcessSetpointDummy(ProcessControlDummy):
    """ A dummy class to emulate a process setpoint device.

    Example config for copy-paste:

    process_setpoint_dummy:
        module.Class: 'dummy.process_control_dummy.ProcessSetpointDummy'
        setpoint_channels:
            Current:
                unit: 'A'
                limits: [-5, 5]
                dtype: float
            Frequency:
                unit: 'Hz'
                limits: [100.0e3, 20.0e9]
                dtype: float
    """
    _process_value_channels = ConfigOption(name='process_value_channels', default=dict())


class ProcessValueDummy(ProcessControlDummy):
    """ A dummy class to emulate a process value reading device.

    Example config for copy-paste:

    process_value_dummy:
        module.Class: 'dummy.process_control_dummy.ProcessValueDummy'
        process_value_channels:
            Temperature:
                unit: 'K'
                limits: [0, .inf]
                dtype: float
            Voltage:
                unit: 'V'
                limits: [-10.0, 10.0]
                dtype: float
    """
    _setpoint_channels = ConfigOption(name='setpoint_channels', default=dict())
