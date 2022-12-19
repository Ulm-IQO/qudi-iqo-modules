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

import numpy as np

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.core.module import Base
from qtpy import QtCore


class PIDLogic(Base):
    """ Logic module to monitor and control a PID process

    Example config:

    pid_logic:
        module.Class: 'pid_logic.PIDLogic'
        connect:
            controller: 'softpid'
        options:
            # interval at which the logging updates (s)
            timestep: 0.1

    """

    # declare connectors
    controller = Connector(interface='PIDControllerInterface')

    # status vars
    buffer_length = StatusVar('buffer_length', 1000)
    timestep = ConfigOption('timestep', 100e-3)  # timestep in seconds

    # signals
    sigUpdateDisplay = QtCore.Signal()

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.log.debug('The following configuration was found.')

        # number of lines in the matrix plot
        self.NumberOfSecondsLog = 100
        self.threadlock = Mutex()

        # initialize attributes
        self._controller = None
        self.history = None
        self.saving_state = False
        self._is_recording = False
        self.timer = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._controller = self.controller()

        self.history = np.zeros([3, self.buffer_length])
        self.saving_state = False
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(self.timestep * 1000)  # in ms
        self.timer.timeout.connect(self._loop)

    def on_deactivate(self):
        """ Perform required deactivation. """
        pass

    def get_buffer_length(self):
        """ Get the current data buffer length.
        """
        return self.buffer_length

    @property
    def is_recording(self):
        """ See if the logic is recording values
            @return bool: whether the recording loop is active
        """
        return self._is_recording

    def start_loop(self):
        """ Start the data recording loop. Not identical to enabling the PID controller.
        """
        self._is_recording = True
        self.timer.start()

    def stop_loop(self):
        """ Stop the data recording loop. Not identical to disabling the PID controller.
        """
        self._is_recording = False
        self.timer.stop()

    def _loop(self):
        """ Execute step in the data recording loop: save one of each control and process values
        """
        self.history = np.roll(self.history, -1, axis=1)
        self.history[0, -1] = self._controller.get_process_value()
        self.history[1, -1] = self._controller.get_control_value()
        self.history[2, -1] = self._controller.get_setpoint()
        self.sigUpdateDisplay.emit()
        if self._is_recording:
            self.timer.start()

    def get_saving_state(self):
        """ Return whether we are saving data

            @return bool: whether we are saving data right now
        """
        return self.saving_state

    def start_saving(self):
        """ Start saving data.

            Function does nothing right now.
        """
        pass

    def save_data(self):
        """ Stop saving data and write data to file.

            Function does nothing right now.
        """
        pass

    def set_buffer_length(self, new_buffer_length):
        """ Change buffer length to new value.

            @param int new_buffer_length: new buffer length
        """
        self.buffer_length = new_buffer_length
        self.reset_buffer()

    def reset_buffer(self):
        """ Reset the buffer, clearing out all data. """
        self.history = np.zeros([3, self.buffer_length])

    def get_kp(self):
        """ Return the proportional constant.

            @return float: proportional constant of PID controller
        """
        return self._controller.get_kp()

    def set_kp(self, kp):
        """ Set the proportional constant of the PID controller.

            @param float kp: proportional constant of PID controller
        """
        return self._controller.set_kp(kp)

    def get_ki(self):
        """ Get the integration constant of the PID controller

            @return float: integration constant of the PID controller
        """
        return self._controller.get_ki()

    def set_ki(self, ki):
        """ Set the integration constant of the PID controller.

            @param float ki: integration constant of the PID controller
        """
        return self._controller.set_ki(ki)

    def get_kd(self):
        """ Get the derivative constant of the PID controller

            @return float: the derivative constant of the PID controller
        """
        return self._controller.get_kd()

    def set_kd(self, kd):
        """ Set the derivative constant of the PID controller

            @param float kd: the derivative constant of the PID controller
        """
        return self._controller.set_kd(kd)

    def get_setpoint(self):
        """ Get the current setpoint of the PID controller.

            @return float: current set point of the PID controller
        """
        return self.history[2, -1]

    def set_setpoint(self, setpoint):
        """ Set the current setpoint of the PID controller.

            @param float setpoint: new set point of the PID controller
        """
        self._controller.set_setpoint(setpoint)

    def get_manual_value(self):
        """ Return the control value for manual mode.

            @return float: control value for manual mode
        """
        return self._controller.get_manual_value()

    def set_manual_value(self, manual_value):
        """ Set the control value for manual mode.

            @param float manual_value: control value for manual mode of controller
        """
        return self._controller.set_manual_value(manual_value)

    def get_enabled(self):
        """ See if the PID controller is controlling a process.

            @return bool: whether the PID controller is preparing to or controlling a process
        """
        return self._controller.get_enabled()

    def set_enabled(self, enabled):
        """ Set the state of the PID controller.

            @param bool enabled: desired state of PID controller
        """
        self._controller.set_enabled(enabled)

    def get_control_limits(self):
        """ Get the minimum and maximum value of the control actuator.

            @return list(float): (minimum, maximum) values of the control actuator
        """
        return self._controller.get_control_limits()

    def set_control_limits(self, limits):
        """ Set the minimum and maximum value of the control actuator.

            @param list(float) limits: (minimum, maximum) values of the control actuator

            This function does nothing, control limits are handled by the control module
        """
        return self._controller.set_control_limits(limits)

    def get_pv(self):
        """ Get current process input value.

            @return float: current process input value
        """
        return self.history[0, -1]

    @property
    def process_value_unit(self):
        """ read-only property for the unit of the process value
        """
        return self._controller.process_value_unit

    def get_cv(self):
        """ Get current control output value.

            @return float: control output value
        """
        return self.history[1, -1]

    @property
    def control_value_unit(self):
        """ read-only property for the unit of the control value
        """
        return self._controller.control_value_unit
