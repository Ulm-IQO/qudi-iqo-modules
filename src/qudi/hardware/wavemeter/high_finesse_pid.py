# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module for the (optional) laser control functionality of a HighFinesse wavemeter.

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

from typing import Tuple, Dict

from scipy.constants import lambda2nu

from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.util.mutex import Mutex
from qudi.interface.pid_controller_interface import PIDControllerInterface
from qudi.hardware.wavemeter.high_finesse_proxy import HighFinesseProxy
import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants


class HighFinessePID(PIDControllerInterface):
    """
    Laser control functionality of a HighFinesse wavemeter.

    Example config for copy-paste:

    wavemeter_pid:
        module.Class: 'wavemeter.high_finesse_pid.HighFinessePID'
        connect:
            proxy: wavemeter_proxy
        options:
            input_channel: 1 # channel of multi-switch with laser light input
            output_port: 1  # port for analog control voltage output
            unit: 'm'    # wavelength (m) or frequency (Hz)
            max_control_limits: [-10^4, 10^4]
    """
    _proxy: HighFinesseProxy = Connector(name='proxy', interface='HighFinesseProxy')

    # channel of multi-switch with laser light input
    _input_channel: int = ConfigOption(name='input_channel', default=1, checker=lambda x: x > 0)
    # port for analog voltage output
    _output_port: int = ConfigOption(name='output_port', default=1, checker=lambda x: x > 0)
    _input_unit: str = ConfigOption(name='unit', default='m')
    _max_control_limits: Tuple[float, float] = ConfigOption(name='max_control_limits', default=(-10 ** 4, 10 ** 4))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()

        self._manual_value = 0.0

    def on_activate(self) -> None:
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiDeviationChannel, i_val=self._input_channel)

        if self._input_unit == 'Hz':
            proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiDeviationUnit,
                                  i_val=high_finesse_constants.cReturnFrequency)
        elif self._input_unit == 'm':
            proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiDeviationUnit,
                                  i_val=high_finesse_constants.cReturnWavelengthVac)
        else:
            raise ValueError(f'Unknown unit {self._input_unit} configured. Use m or Hz.')

    def on_deactivate(self) -> None:
        pass

    def get_kp(self):
        """ 
        Get the coefficient associated with the proportional term
        @return (float): The current kp coefficient associated with the proportional term
        """
        proxy: HighFinesseProxy = self._proxy()
        kp, _ = proxy.get_pid_setting(self._output_port, high_finesse_constants.cmiPID_P)
        return kp

    def set_kp(self, kp: float) -> None:
        """ 
        Set the coefficient associated with the proportional term
        @param (float) kp: The new kp coefficient associated with the proportional term
        """
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiPID_P, kp)

    def get_ki(self) -> float:
        """ 
        Get the coefficient associated with the integral term
        @return (float): The current ki coefficient associated with the integral term
        """
        proxy: HighFinesseProxy = self._proxy()
        ki, _ = proxy.get_pid_setting(self._output_port, high_finesse_constants.cmiPID_I)
        return ki

    def set_ki(self, ki: float) -> None:
        """ 
        Set the coefficient associated with the integral term
        @param (float) ki: The new ki coefficient associated with the integral term
        """
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiPID_I, ki)

    def get_kd(self) -> float:
        """ 
        Get the coefficient associated with the derivative term
        @return (float): The current kd coefficient associated with the derivative term
        """
        proxy: HighFinesseProxy = self._proxy()
        kd, _ = proxy.get_pid_setting(self._output_port, high_finesse_constants.cmiPID_D)
        return kd

    def set_kd(self, kd: float) -> None:
        """ 
        Set the coefficient associated with the derivative term
        @param (float) kd: The new kd coefficient associated with the derivative term
        """
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiPID_D, kd)

    def get_setpoint(self) -> float:
        """ 
        Get the setpoint value of the hardware device
        @return (float): The current setpoint value
        """
        proxy: HighFinesseProxy = self._proxy()
        setpoint = proxy.get_setpoint(self._output_port)
        if self._input_unit == 'm':
            # wavelength is in nm
            return 1e-9 * setpoint
        else:
            # frequency is in THz
            return 1e12 * setpoint

    def set_setpoint(self, setpoint: float):
        """ 
        Set the setpoint value of the hardware device
        @param (float) setpoint: The new setpoint value
        """
        proxy: HighFinesseProxy = self._proxy()
        if self._input_unit == 'm':
            # wavelength is in nm
            setpoint *= 1e9
        else:
            # frequency is in THz
            setpoint *= 1e-12
        proxy.set_setpoint(self._output_port, setpoint)

    def get_manual_value(self) -> float:
        """ 
        Get the manual value, used if the device is disabled
        @return (float): The current manual value
        """
        return self._manual_value

    def set_manual_value(self, manual_value: float) -> None:
        """ 
        Set the manual value, used if the device is disabled
        @param (float) manual_value: The new manual value
        """
        self._manual_value = manual_value
        if not self.get_enabled():
            self._apply_manual_value()

    def _apply_manual_value(self) -> None:
        """ Manually set a constant output voltage (instead of running PID). """
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_manual_value(self._output_port, self._manual_value)

    def get_enabled(self) -> bool:
        """ 
        Get if the PID is enabled (True) or if it is disabled (False) and the manual value is used
        @return (bool): True if enabled, False otherwise
        """
        proxy: HighFinesseProxy = self._proxy()
        return proxy.get_pid_enabled()

    def set_enabled(self, enabled: bool) -> None:
        """ 
        Set if the PID is enabled (True) or if it is disabled (False) and the manual value is used
        @param (bool) enabled: True if enabled, False otherwise
        """
        # TODO: is there a way to toggle PID only for a single channel?
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_pid_enabled(enabled)
        if not enabled:
            self._apply_manual_value()

    def get_control_limits(self) -> Tuple[float, float]:
        """ 
        Get the current limits of the control value as a tuple
        @return (tuple(float, float)): The current control limits
        """
        proxy: HighFinesseProxy = self._proxy()
        lower, _ = proxy.get_pid_setting(self._output_port, high_finesse_constants.cmiDeviationBoundsMin)
        upper, _ = proxy.get_pid_setting(self._output_port, high_finesse_constants.cmiDeviationBoundsMax)
        return lower, upper

    def set_control_limits(self, limits: Tuple[float, float]) -> None:
        """ 
        Set the current limits of the control value as a tuple
        @param (tuple(float, float)) limits: The new control limits
        """
        proxy: HighFinesseProxy = self._proxy()
        lower, upper = limits
        if lower < self._max_control_limits[0] or upper > self._max_control_limits[1]:
            raise ValueError(f'Control limits {limits} are outside of the maximum limits {self._max_control_limits}')
        elif lower > upper:
            raise ValueError(f'Control limits {limits} are invalid: lower limit is greater than upper limit')
        proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiDeviationBoundsMin, lower)
        proxy.set_pid_setting(self._output_port, high_finesse_constants.cmiDeviationBoundsMax, upper)

    def get_process_value(self) -> float:
        """ 
        Get the current process value read
        @return (float): The current process value
        """
        proxy: HighFinesseProxy = self._proxy()
        wavelength = proxy.get_wavelength(self._input_channel)
        if self._input_unit == 'Hz':
            return lambda2nu(wavelength)
        else:
            return wavelength

    def process_value_unit(self):
        """ read-only property for the unit of the process value """
        return self._input_unit

    def get_control_value(self) -> float:
        """ 
        Get the current control value read
        @return (float): The current control value
        """
        proxy: HighFinesseProxy = self._proxy()
        return proxy.get_control_value(self._output_port)

    def control_value_unit(self) -> str:
        """ read-only property for the unit of the control value """
        return 'V'

    def get_extra(self) -> Dict[str, float]:
        """ 
        Get the P, I and D terms computed by the hardware if available
        @return dict(): A dict with keys 'P', 'I', 'D' if available, an empty dict otherwise
        """
        # live readout of regulation parameters not supported by this hardware
        return {}
