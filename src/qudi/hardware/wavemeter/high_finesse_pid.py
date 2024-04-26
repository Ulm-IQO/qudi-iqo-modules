# -*- coding: utf-8 -*-

from qudi.core.module import Base
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.util.mutex import Mutex
import numpy as np
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
            kp: 0.0
            ki: 0.0
            kd: 0.0
    """
    _proxy: HighFinesseProxy = Connector(name='proxy', interface='HighFinesseProxy')

    # Config options
    # TODO: add port option as well, the analog voltage output
    # ports might be starting from 0
    _ch = ConfigOption(name='channel', default=1) # light input
    _kp = ConfigOption(name='kp', default=0.0)
    _ki = ConfigOption(name='ki', default=0.0)
    _kd = ConfigOption(name='kd', default=0.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()

    def on_activate(self) -> None:
        # Set PID values
        # NOTE: Not sure if necessary
        self.set_kp(self._kp)
        self.set_ki(self._ki)
        self.set_kd(self._kd)

    def on_deactivate(self) -> None:
        self.disconnect()

    def disconnect(self) -> None:
        self._proxy().disconnect_instreamer(self) # no need

    def get_kp(self):
        """ Get the coefficient associated with the proportional term

         @return (float): The current kp coefficient associated with the proportional term
         """
        kp, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiPID_P)
        return kp

    def set_kp(self, kp):
        """ Set the coefficient associated with the proportional term

         @param (float) kp: The new kp coefficient associated with the proportional term
         """
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiPID_P, kp)
        self._kp = kp

    def get_ki(self):
        """ Get the coefficient associated with the integral term

         @return (float): The current ki coefficient associated with the integral term
         """
        ki, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiPID_I)
        return ki

    def set_ki(self, ki):
        """ Set the coefficient associated with the integral term

         @param (float) ki: The new ki coefficient associated with the integral term
         """
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiPID_I, ki)
        self._ki = ki

    def get_kd(self):
        """ Get the coefficient associated with the derivative term

         @return (float): The current kd coefficient associated with the derivative term
         """
        kd, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiPID_D)
        return kd

    def set_kd(self, kd):
        """ Set the coefficient associated with the derivative term

         @param (float) kd: The new kd coefficient associated with the derivative term
         """
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiPID_D, kd)
        self._kd = kd

    def get_setpoint(self):
        """ Get the setpoint value of the hardware device

         @return (float): The current setpoint value
         """
        return self._proxy().get_setpoint(self._ch)

    def set_setpoint(self, setpoint):
        """ Set the setpoint value of the hardware device

        @param (float) setpoint: The new setpoint value
        """
        self._proxy().set_setpoint(self._ch, setpoint)

    def get_manual_value(self): # TODO: implement
        """ Get the manual value, used if the device is disabled

        @return (float): The current manual value
        """
        return 1.0

    def set_manual_value(self, manual_value): # TODO: implement
        """ Set the manual value, used if the device is disabled

        @param (float) manual_value: The new manual value
        """
        pass

    def get_enabled(self):
        """ Get if the PID is enabled (True) or if it is disabled (False) and the manual value is used

        @return (bool): True if enabled, False otherwise
        """
        return self._proxy().get_pid_enabled()

    def set_enabled(self, enabled):
        """ Set if the PID is enabled (True) or if it is disabled (False) and the manual value is used

        @param (bool) enabled: True if enabled, False otherwise
        """
        return self._proxy().set_pid_enabled(enabled)

    def get_control_limits(self):
        """ Get the current limits of the control value as a tuple

        @return (tuple(float, float)): The current control limits
        """
        lower, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMin)
        upper, _ = self._proxy().get_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMax)
        return (lower, upper)

    def set_control_limits(self, limits):
        """ Set the current limits of the control value as a tuple

        @param (tuple(float, float)) limits: The new control limits

        The hardware should check if these limits are within the maximum limits set by a config option.
        """
        lower, upper = limits
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMin, lower)
        self._proxy().set_pid_setting(self._ch, high_finesse_constants.cmiDeviationBoundsMax, upper)

    def get_process_value(self):
        """ Get the current process value read

        @return (float): The current process value
        """
        return self._proxy().get_process_value(self._ch)

    def process_value_unit(self) -> str: # TODO: implement
        """ read-only property for the unit of the process value
        """
        pass

    def get_control_value(self):
        """ Get the current control value read

        @return (float): The current control value
        """
        return self._proxy().get_control_value(self._ch)

    def control_value_unit(self) -> str:
        """ read-only property for the unit of the control value
        """
        return 'mV'

    def get_extra(self): # TODO: implement
        """ Get the P, I and D terms computed by the hardware if available

         @return dict(): A dict with keys 'P', 'I', 'D' if available, an empty dict otherwise
         """
        return {}

