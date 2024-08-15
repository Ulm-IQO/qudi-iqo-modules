# -*- coding: utf-8 -*-

"""
A module for controlling processes via PID regulation.

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

from qtpy import QtCore
import numpy as np

from qudi.interface.pid_controller_interface import PIDControllerInterface
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar


class SoftPIDController(PIDControllerInterface):
    """
    Logic module to control a process via software PID.

    Example config:

    softpid:
        module.Class: 'software_pid_controller.SoftPIDController'
        options:
            process_value_channel: 'Voltage'
            setpoint_channel: 'Power'
            # PID control value update interval (ms)
            timestep: 100
            # normalize process value to setpoint
            normalize: False
        connect:
            process_value: process_value_dummy
            setpoint: process_setpoint_dummy
    """

    # declare connectors
    process = Connector(name='process_value', interface='ProcessValueInterface')
    control = Connector(name='setpoint', interface='ProcessSetpointInterface')

    # config options
    # channels to use for process and setpoint devices
    process_value_channel = ConfigOption(default='A')
    setpoint_channel = ConfigOption(default='A')
    # timestep on which the PID updates
    timestep = ConfigOption(default=100)
    # normalize process value to setpoint
    _normalize = ConfigOption(name='normalize', default=False)

    # status vars
    kP = StatusVar(default=1)
    kI = StatusVar(default=1)
    kD = StatusVar(default=1)
    setpoint = StatusVar(default=273.15)
    manual_value = StatusVar(default=0)

    sigNewValue = QtCore.Signal(str, float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # initialize attributes
        self._process = None
        self._control = None
        self.timer = None

        self.history = None
        self.saving_state = False
        self.enable = False
        self.integrated = None
        self.countdown = None
        self.previous_delta = None
        self.cv = None
        self.P, self.I, self.D = 0, 0, 0

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._process = self.process()
        self._control = self.control()

        self._process.set_activity_state(self.process_value_channel, True)
        self._control.set_activity_state(self.setpoint_channel, True)

        self.previous_delta = 0
        self.cv = self._control.get_setpoint(self.setpoint_channel)

        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(self.timestep)

        self.timer.timeout.connect(self._calc_next_step, QtCore.Qt.QueuedConnection)
        self.sigNewValue.connect(self._control.set_setpoint)

        self.history = np.zeros([3, 5])
        self.saving_state = False
        self.enable = False
        self.integrated = 0
        self.countdown = -1

        self.timer.start(self.timestep)

    def on_deactivate(self):
        """ Perform required deactivation.
        """
        self._process.set_activity_state(self.process_value_channel, False)
        self._control.set_activity_state(self.setpoint_channel, False)

    def _calc_next_step(self):
        """ This function implements the Takahashi Type C PID
            controller: the P and D term are no longer dependent
            on the set-point, only on PV (which is Thlt).
            The D term is NOT low-pass filtered.
            This function should be called once every TS seconds.
        """
        self.pv = self._process.get_process_value(self.process_value_channel)

        if self.countdown > 0:
            self.countdown -= 1
            if self._normalize:
                pv_normalized = self.pv / self.setpoint
                self.previous_delta = 1 - pv_normalized
            else:
                self.previous_delta = self.setpoint - self.pv
        elif self.countdown == 0:
            self.countdown = -1
            self.integrated = 0
            self.enable = True

        # if PID enabled, calculate the next control value
        if self.enable:
            if self._normalize:
                pv_normalized = self.pv / self.setpoint
                delta = 1 - pv_normalized
            else:
                delta = self.setpoint - self.pv
            self.integrated += delta
            # calculate PID controller:
            self.P = self.kP * delta
            self.I = self.kI * self.timestep * self.integrated
            self.D = self.kD / self.timestep * (delta - self.previous_delta)

            self.cv += self.P + self.I + self.D
            self.previous_delta = delta

            # limit control output to maximum permissible limits
            limits = self.get_control_limits()
            if self.cv > limits[1]:
                self.cv = limits[1]
            if self.cv < limits[0]:
                self.cv = limits[0]

            self.history = np.roll(self.history, -1, axis=1)
            self.history[0, -1] = self.pv
            self.history[1, -1] = self.cv
            self.history[2, -1] = self.setpoint
            self.sigNewValue.emit(self.setpoint_channel, self.cv)

        # if PID disabled, just use the manual control value
        else:
            self.cv = self.manual_value
            limits = self.get_control_limits()
            if self.cv > limits[1]:
                self.cv = limits[1]
            if self.cv < limits[0]:
                self.cv = limits[0]
            self.sigNewValue.emit(self.setpoint_channel, self.cv)

        self.timer.start(self.timestep)

    def _start_loop(self):
        """ Start the control loop. """
        self.countdown = 2

    def _stop_loop(self):
        """ Stop the control loop. """
        self.countdown = -1
        self.enable = False

    def get_kp(self):
        """ Return the proportional constant.

            @return float: proportional constant of PID controller
        """
        return self.kP

    def set_kp(self, kp):
        """ Set the proportional constant of the PID controller.

            @param float kp: proportional constant of PID controller
        """
        self.kP = kp

    def get_ki(self):
        """ Get the integration constant of the PID controller

            @return float: integration constant of the PID controller
        """
        return self.kI

    def set_ki(self, ki):
        """ Set the integration constant of the PID controller.

            @param float ki: integration constant of the PID controller
        """
        self.kI = ki

    def get_kd(self):
        """ Get the derivative constant of the PID controller

            @return float: the derivative constant of the PID controller
        """
        return self.kD

    def set_kd(self, kd):
        """ Set the derivative constant of the PID controller

            @param float kd: the derivative constant of the PID controller
        """
        self.kD = kd

    def get_setpoint(self):
        """ Get the current setpoint of the PID controller.

            @return float: current set point of the PID controller
        """
        return self.setpoint

    def set_setpoint(self, setpoint):
        """ Set the current setpoint of the PID controller.

            @param float setpoint: new set point of the PID controller
        """
        self.setpoint = setpoint

    def get_manual_value(self):
        """ Return the control value for manual mode.

            @return float: control value for manual mode
        """
        return self.manual_value

    def set_manual_value(self, manual_value):
        """ Set the control value for manual mode.

            @param float manual_value: control value for manual mode of controller
        """
        self.manual_value = manual_value
        limits = self.get_control_limits()
        if self.manual_value > limits[1]:
            self.manual_value = limits[1]
        if self.manual_value < limits[0]:
            self.manual_value = limits[0]

    def get_enabled(self):
        """ See if the PID controller is controlling a process.

            @return bool: whether the PID controller is preparing to or controlling a process
        """
        return self.enable or self.countdown >= 0

    def set_enabled(self, enabled):
        """ Set the state of the PID controller.

            @param bool enabled: desired state of PID controller
        """
        if enabled and not self.enable and self.countdown == -1:
            self._start_loop()
        if not enabled and self.enable:
            self._stop_loop()

    def get_control_limits(self):
        """ Get the minimum and maximum value of the control actuator.

            @return list(float): (minimum, maximum) values of the control actuator
        """
        constraints = self._control.constraints
        limits = constraints.channel_limits[self.setpoint_channel]
        return limits

    def set_control_limits(self, limits):
        """ Set the minimum and maximum value of the control actuator.

            @param list(float) limits: (minimum, maximum) values of the control actuator

            This function does nothing, control limits are handled by the control module
        """
        pass

    def get_control_value(self):
        """ Get current control output value.

            @return float: control output value
        """
        return self.cv

    def control_value_unit(self):
        """ read-only property for the unit of the control value
        """
        constraints = self._control.constraints
        unit = constraints.channel_units[self.setpoint_channel]
        return unit

    def get_process_value(self):
        """ Get current process input value.

            @return float: current process input value
        """
        return self.pv

    def process_value_unit(self):
        """ read-only property for the unit of the process value
        """
        constraints = self._process.constraints
        unit = constraints.channel_units[self.process_value_channel]
        return unit

    def get_extra(self):
        """ Extra information about the controller state.

            @return dict: extra information about internal controller state

            Do not depend on the output of this function, not every field
            exists for every PID controller.
        """
        return {
            'P': self.P,
            'I': self.I,
            'D': self.D
        }
