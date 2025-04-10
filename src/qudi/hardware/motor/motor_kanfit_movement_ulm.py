# -*- coding: utf-8 -*-
"""
This module controls the three motors, that move the table on which the
permanent magnet sits.

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
from collections import OrderedDict
from typing import Dict, Any
import time

import serial
import numpy as np

from qudi.interface.motor_interface import MotorInterface
from qudi.core.configoption import ConfigOption, MissingOption

def moving_p(finish_time):
    if time.time() < finish_time:
        return True
    else:
        return False

class KanfitMotorStage(MotorInterface):
    """ Control class for the 3 axes magnet motor stage in Ulm university."""
    # hardware specifics
    _thread_pitch_xy = 3e-3
    # for z this is not exactly the thread pitch, but one
    # also has to consider the gear ratio.
    _thread_pitch_z = 1.16e-3
    # determined through experiment.
    _vel_x = 0.003467
    _vel_y = 0.003467
    _vel_z = 0.003467 / 3
    # number of positions between min and max value (arb. chosen)
    _steps = 1000

    _com_port = ConfigOption('com_port', 'COM8', missing='warn')
    _baud_rate = ConfigOption('baud_rate', 115_200, missing='warn')
    _timeout = ConfigOption('timeout', 1000, missing='warn')

    _first_axis_label = ConfigOption('first_axis_label', 'x', missing='warn')
    _second_axis_label = ConfigOption('second_axis_label', 'y', missing='warn')
    _third_axis_label = ConfigOption('third_axis_label', 'z', missing='warn')

    _min_first = ConfigOption('first_min', 0.0, missing='warn')
    _max_first = ConfigOption('first_max', 0.2, missing='warn')
    _min_second = ConfigOption('second_min', 0.0, missing='warn')
    _max_second = ConfigOption('second_max', 0.2, missing='warn')
    _min_third = ConfigOption('third_min', 0.0, missing='warn')
    _max_third = ConfigOption('third_max', 0.5, missing='warn')

    # we can only send absolute positions to the stage.
    # relative movement therefore has to be emulated by comparing
    # to the previous position

    _pos = dict()
    _target_pos = dict()
    _mov = False
    _mov_time = 0.0
    _start_time = 0.0
    _estimated_time = 0.0
    _finish_time = 0.0


    def on_activate(self):
        """ Initialisation performed during activation of the module.
        @return: error code
        """
        # establish connection to the stage
        self.con = serial.Serial(self._com_port)
        self._pos[self._first_axis_label] = 0.0
        self._pos[self._second_axis_label] = 0.0
        self._pos[self._third_axis_label] = 0.0

        self._vels = dict()
        self._vels[self._first_axis_label] = self._vel_x
        self._vels[self._second_axis_label] = self._vel_y
        self._vels[self._third_axis_label] = self._vel_z

        if self.con.readable():
            self.log.info("Successfully established connection to the stage.")
        else:
            raise ValueError("Connection to the stage couldn't be established")
        return

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        @return: error code
        """
        self.con.close()
        return

    def get_status(self, param_list=None) -> Dict[str, Any]:
        """ Get the status of the position

        @param list param_list: optional, if a specific status of an axis
                                is desired, then the labels of the needed
                                axis should be passed in the param_list.
                                If nothing is passed, then from each axis the
                                status is asked.

        @return dict: with the axis label as key and the axis status as value.
        """
        rtrn = {}
        self._mov = moving_p(self._finish_time)

        if param_list:
            for key in param_list:
                rtrn[key] = not self._mov
        else:
            rtrn[self._first_axis_label] = not self._mov
            rtrn[self._second_axis_label] = not self._mov
            rtrn[self._third_axis_label] = not self._mov

        return rtrn


    def get_constraints(self):
        """ Retrieve the hardware constrains from the motor device.

        @return dict: dict with constraints for the sequence generation and GUI

        Provides all the constraints for the motorized stage (like total
        movement, velocity, ...)
        Each constraint is a tuple of the form
            (min_value, max_value, stepsize)

        The possible keys in the constraint are defined here in the interface
        file. If the hardware does not support the values for the constraints,
        then insert just None.
        If you are not sure about the meaning, look in other hardware files
        to get an impression.
        """
        constraints = OrderedDict()

        axis0 = {'label': self._first_axis_label,
                 'ID': None,
                 'unit': 'm',
                 'ramp': None,
                 'pos_min': self._min_first,
                 'pos_max': self._max_first,
                 'pos_step': (self._max_first - self._min_first) / self._steps,
                 'pos_accuracy': (self._max_first - self._min_first) / self._steps,
                 'vel_min': self._vels[self._first_axis_label],
                 'vel_max': self._vels[self._first_axis_label],
                 'vel_step': None,
                 'acc_min': None,
                 'acc_max': None,
                 'acc_step': None}

        axis1 = {'label': self._second_axis_label,
                 'ID': None,
                 'unit': 'm',
                 'ramp': None,
                 'pos_min': self._min_second,
                 'pos_max': self._max_second,
                 'pos_step': (self._max_second - self._min_second) / self._steps,
                 'pos_accuracy': (self._max_second - self._min_second) / self._steps,
                 'vel_min': self._vels[self._second_axis_label],
                 'vel_max': self._vels[self._second_axis_label],
                 'vel_step': None,
                 'acc_min': None,
                 'acc_max': None,
                 'acc_step': None}

        axis2 = {'label': self._third_axis_label,
                 'ID': None,
                 'unit': 'm', 'ramp': None,
                 'pos_min': self._min_third,
                 'pos_max': self._max_third,
                 'pos_step': (self._max_third - self._min_third) / self._steps,
                 'pos_accuracy': (self._max_third - self._min_third) / self._steps,
                 'vel_min': self._vels[self._third_axis_label],
                 'vel_max': self._vels[self._third_axis_label],
                 'vel_step': None,
                 'acc_min': None,
                 'acc_max': None,
                 'acc_step': None}

        # assign the parameter container for x to a name which will identify it
        constraints[axis0['label']] = axis0
        constraints[axis1['label']] = axis1
        constraints[axis2['label']] = axis2

        return constraints

    def move_rel(self,  param_dict):
        """ The stage doesn't provide this functionality
        """
        pass

    def move_abs(self, param_dict):
        """ Moves stage to absolute position (absolute movement)

        @param dict param_dict: dictionary, which passes all the relevant
                                parameters, which should be changed. Usage:
                                 {'axis_label': <the-abs-pos-value>}.
                                 'axis_label' must correspond to a label given
                                 to one of the axis.

        @return int: error code (0:OK, -1:error)
        """
        # TODO implement safety checks
        self.log.warning(f"what is the input to move_abs {param_dict}")

        def calc_total_movement_duration(old_param_dict, new_param_dict):
            """Given a set of movements calculate an estimate for how long it will take
            to complete the movement."""
            duration = 0.0
            # TODO axes selection shouldn't be config dependent
            for key in old_param_dict:
                dist = np.abs(new_param_dict[key] - old_param_dict[key])
                duration += dist / self._vels[key]
            return duration

        def movement_to_num(mov, ax_id):
            """Translates the movement in meter to a number, that is understood
            by the stage."""

            # one rotation corresponds to 2 ** 12
            bit_encoding = 12
            enc = mov * 2 ** bit_encoding
            if ax_id == 'z':
                rot = hex(int(enc / self._thread_pitch_z))
            else:
                rot = hex(int(enc / self._thread_pitch_xy))
            return rot

        def format_hex(hn, ln):
            """ Format number into hex, needed for the stage"""
            _, rhn = hn.split('x')
            if len(rhn) < ln:
                thn = ""
                for ii in range(ln - len(rhn)):
                    thn += '0'
                fhn = thn + rhn
            elif len(rhn) > ln:
                self.log.warning("the rotations you ask for are bigger than the encoding space.")
                dln = rhn - ln
                fhn = rhn[dln:]
            else:
                fhn = rhn
            # finally produce representation that puts every two characters a space in between
            ffhn = ""
            for hh, tt in zip(fhn[0::2], fhn[1::2]):
                ffhn += hh + tt + " "
            return ffhn

        # first check if we are still moving
        self._mov = moving_p(self._finish_time)
        # if we are already moving just return
        # should this throw an error?
        if self._mov:
            self.log.warning("Got a movement command although moving already.")
            return 0

        # calculate how long we will need for all the movement.
        self._start_time = time.time()
        self._mov_time = calc_total_movement_duration(param_dict, self._pos)
        self._finish_time = self._mov_time + self._start_time
        # the base command contain the 3 bytes that are in front of
        # every movement command.
        base_command = "23 63 3E B3"
        hns = list()
        self.log.warning(f"self._pos {self._pos} before update")
        self._target_pos.update(param_dict)
        self.log.warning(f"self._pos {self._pos} after update")
        for key in self._pos:
            rot = movement_to_num(self._pos[key],  key)
            self.log.warning(f"rot {rot} for key {key}")
            # each axis has 6 byte and 2 byte represent [0 .. 255] which equal to the number
            # range of a two digit hex number [00 .. FF].
            hn = format_hex(rot, 6)
            self.log.warning(f"hex num {hn} for key {key}")
            hns.append(hn)

        # construct the final command an remove the last space
        cmd = "".join([base_command, " "] + hns)[0:-1]
        self.log.warning(f"sending cmd {cmd} to stage")
        self.con.write(bytes.fromhex(cmd))
        return

    def abort(self):
        """ The stage doesn't provide this functionality

        @return int: error code (0:OK, -1:error)
        """
        pass

    def get_pos(self, param_list=None):
        """ Gets current position of the stage arms

        @param list param_list: optional, if a specific position of an axis
                                is desired, then the labels of the needed
                                axis should be passed in the param_list.
                                If nothing is passed, then from each axis the
                                position is asked.

        @return dict: with keys being the axis labels and value the current
                      position.
        """
        # we will need to calculate some estimate where we are given the time we already
        # moved and the speed that we have

        # the way the stage moves is ordered according to the axes.
        # meaning 1. axis, 2. axis then 3rd axis.
        rtrn = dict()

        # are we still moving?
        if self._finish_time < time.time():
            self._mov = True
        else:
            self._mov = False

        # begin by calculating the current position
        if self._mov:
            tmp_d0 = {}
            time_running = time.time() - self._start_time

            dist_x = np.abs(self._target_pos[self._first_axis_label] - self._pos[self._first_axis_label])
            dist_y = np.abs(self._target_pos[self._second_axis_label] - self._pos[self._second_axis_label])

            time_mov_x = dist_x / self._vel_x
            time_mov_y = dist_y / self._vel_y

            # if we are moving we can only be sure that
            # there was a movement in the first axis movement
            # we move one axis at a time.
            if time_running < time_mov_x:
                dx = time_running * self._vel_x
                cur_x = dx + self._pos[self._first_axis_label]

                tmp_d0[self._first_axis_label] = cur_x
                tmp_d0[self._second_axis_label] = self._pos[self._second_axis_label]
                tmp_d0[self._third_axis_label] = self._pos[self._third_axis_label]
            elif time_running < (time_mov_x + time_mov_y):
                dy = (time_running - time_mov_x) * self._vel_y
                cur_y = dy + self._pos[self._second_axis_label]

                tmp_d0[self._first_axis_label] = self._target_pos[self._first_axis_label]
                tmp_d0[self._second_axis_label] = cur_y
                tmp_d0[self._third_axis_label] = self._pos[self._third_axis_label]
            else:
                dz = (time_running - time_mov_x - time_mov_y) * self._vel_z
                cur_z = dz + self._pos[self._third_axis_label]

                tmp_d0[self._first_axis_label] = self._target_pos[self._first_axis_label]
                tmp_d0[self._second_axis_label] = self._target_pos[self._second_axis_label]
                tmp_d0[self._third_axis_label] = cur_z
            if param_list:
                for key in param_list:
                    rtrn[key] = self._tmp_d0[key]
            else:
                for key in self._pos:
                    rtrn[key] = self._tmp_d0[key]
        # if there is no movement we can just assume that
        # the last position we set was reached.
        else:
            if param_list:
                for key in param_list:
                    rtrn[key] = self._pos[key]
            else:
                for key in self._pos:
                    rtrn[key] = self._pos[key]

        # at last we return what is queried by the function
        return rtrn


    def calibrate(self, param_list=None):
        """ Calibrates the stage.

        @param dict param_list: param_list: optional, if a specific calibration
                                of an axis is desired, then the labels of the
                                needed axis should be passed in the param_list.
                                If nothing is passed, then all connected axis
                                will be calibrated.

        @return int: error code (0:OK, -1:error)

        After calibration the stage moves to home position which will be the
        zero point for the passed axis. The calibration procedure will be
        different for each stage.
        """
        # this command sets the zero of the stage
        # we set the encoders to 0
        self.con.write(bytes.fromhex('23 63 3e C0'))
        time.sleep(0.1)
        self.con.write(bytes.fromhex('23 63 3e C1'))
        time.sleep(0.1)
        self.con.write(bytes.fromhex('23 63 3e C2'))
        time.sleep(0.1)
        return

    def get_velocity(self, param_list=None):
        """ Gets the current velocity for all connected axes.

        @param dict param_list: optional, if a specific velocity of an axis
                                is desired, then the labels of the needed
                                axis should be passed as the param_list.
                                If nothing is passed, then from each axis the
                                velocity is asked.

        @return dict : with the axis label as key and the velocity as item.
        """
        rtrn = {}
        if param_list:
            for key in param_list:
                rtrn[param] =  self._vels[key]
        else:
            for key in self._vels:
                rtrn[key] = self._vels[key]

        return

    def set_velocity(self, param_dict):
        """ Write new value for velocity.

        @param dict param_dict: dictionary, which passes all the relevant
                                parameters, which should be changed. Usage:
                                 {'axis_label': <the-velocity-value>}.
                                 'axis_label' must correspond to a label given
                                 to one of the axis.

        @return int: error code (0:OK, -1:error)
        """
        pass

    def is_ready(self) -> bool:
        """ Queries if the motor is ready to accept a command

        @return bool: True if ready False otherwise
        """
        self._mov = moving_p(self._finish_time)
        return not self._mov
