# -*- coding: utf-8 -*-
from typing import Tuple, Dict

from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.util.mutex import Mutex
from qudi.interface.pid_controller_interface import PIDControllerInterface
from qudi.hardware.wavemeter.high_finesse_proxy import HighFinesseProxy
import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants


class HighFinessePID(PIDControllerInterface):
    """
    HighFinessePID class

    Example config for copy-paste:

    wavemeter_pid:
        module.Class: 'wavemeter.high_finesse_pid.HighFinessePID'
        connect:
            proxy: wavemeter_proxy
        options:
            channel: 0
    """
    _proxy: HighFinesseProxy = Connector(name='proxy', interface='HighFinesseProxy')

    # Config options
    # TODO: add port option as well, the analog voltage output
    # ports might be starting from 0
    _ch: int = ConfigOption(name='channel', default=1) # light input

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()

        self._manual_value = 0.0

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        pass

    def get_kp(self):
        """ 
        Get the coefficient associated with the proportional term
        @return (float): The current kp coefficient associated with the proportional term
        """
        kp, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiPID_P)
        return kp

    def set_kp(self, kp: float) -> None:
        """ 
        Set the coefficient associated with the proportional term
        @param (float) kp: The new kp coefficient associated with the proportional term
        """
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiPID_P, kp)

    def get_ki(self) -> float:
        """ 
        Get the coefficient associated with the integral term
        @return (float): The current ki coefficient associated with the integral term
        """
        ki, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiPID_I)
        return ki

    def set_ki(self, ki: float) -> None:
        """ 
        Set the coefficient associated with the integral term
        @param (float) ki: The new ki coefficient associated with the integral term
        """
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiPID_I, ki)

    def get_kd(self) -> float:
        """ 
        Get the coefficient associated with the derivative term
        @return (float): The current kd coefficient associated with the derivative term
        """
        kd, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiPID_D)
        return kd

    def set_kd(self, kd: float) -> None:
        """ 
        Set the coefficient associated with the derivative term
        @param (float) kd: The new kd coefficient associated with the derivative term
        """
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiPID_D, kd)

    def get_setpoint(self) -> float:
        """ 
        Get the setpoint value of the hardware device
        @return (float): The current setpoint value
        """
        return self._proxy().get_setpoint(self._ch)

    def set_setpoint(self, setpoint: float):
        """ 
        Set the setpoint value of the hardware device
        @param (float) setpoint: The new setpoint value
        """
        self._proxy().set_setpoint(self._ch, setpoint)

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
        """Manually set a constant output voltage (instead of running PID)."""
        proxy: HighFinesseProxy = self._proxy()
        proxy.set_manual_value(self._ch, self._manual_value)

    def get_enabled(self) -> bool:
        """ 
        Get if the PID is enabled (True) or if it is disabled (False) and the manual value is used
        @return (bool): True if enabled, False otherwise
        """
        return self._proxy().get_pid_enabled()

    def set_enabled(self, enabled: bool) -> None:
        """ 
        Set if the PID is enabled (True) or if it is disabled (False) and the manual value is used
        @param (bool) enabled: True if enabled, False otherwise
        """
        # TODO: is there a way to toggle PID only for a single channel?
        self._proxy().set_pid_enabled(enabled)
        if not enabled:
            self._apply_manual_value()

    def get_control_limits(self) -> Tuple[float, float]:
        """ 
        Get the current limits of the control value as a tuple
        @return (tuple(float, float)): The current control limits
        """
        lower, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMin)
        upper, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMax)
        return lower, upper

    def set_control_limits(self, limits: Tuple[float, float]) -> None:
        """ 
        Set the current limits of the control value as a tuple
        @param (tuple(float, float)) limits: The new control limits
        The hardware should check if these limits are within the maximum limits set by a config option.
        """
        lower, upper = limits
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMin, lower)
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMax, upper)

    def get_process_value(self) -> float:
        """ 
        Get the current process value read
        @return (float): The current process value
        """
        # nm to m conversion
        return self._proxy().get_process_value(self._ch) ** 10^-9

    @property
    def process_value_unit(self) -> str:
        """ read-only property for the unit of the process value"""
        # TODO: support other units via config option
        # can be done using Get/SetPIDSetting and cmiDeviationUnit
        return 'm'

    def get_control_value(self) -> float:
        """ 
        Get the current control value read
        @return (float): The current control value
        """
        # mV to V conversion
        return self._proxy().get_control_value(self._ch) * 10^-3

    @property
    def control_value_unit(self) -> str:
        """ read-only property for the unit of the control value"""
        return 'V'

    def get_extra(self) -> Dict[str, float]:
        """ 
        Get the P, I and D terms computed by the hardware if available
        @return dict(): A dict with keys 'P', 'I', 'D' if available, an empty dict otherwise
        """
        # live readout of regulation parameters not supported by this hardware
        return {}
