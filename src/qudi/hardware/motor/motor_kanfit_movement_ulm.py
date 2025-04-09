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

import serial
import numpy as np

from qudi.interface.motor_interface import MotorInterface
from qudi.core.configoption import ConfigOption, MissingOption

class KanfitMotorStage(MotorInterface):
    """ Control class for the 3 axes magnet motor stage in Ulm university."""

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
    _abs_pos = (0.0, 0.0, 0.0)
    _pos = dict()

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        @return: error code
        """
        # establish connection to the stage
        self.con = serial.Serial(self._com_port)
        self._pos[self._first_axis_label] = 0.0
        self._pos[self._second_axis_label] = 0.0
        self._pos[self._third_axis_label] = 0.0

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
        pass


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
                 'pos_step': (self._max_first - self._min_first) / 4096,
                 'pos_accuracy': (self._max_first - self._min_first) / 4096,
                 'vel_min': None,
                 'vel_max': None,
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
                 'pos_step': (self._max_second - self._min_second) / 4096,
                 'pos_accuracy': (self._max_second - self._min_second) / 4096,
                 'vel_min': None,
                 'vel_max': None,
                 'vel_step': None,
                 'acc_min': None,
                 'acc_max': None,
                 'acc_step': None}

        axis2 = {'label': self._third_axis_label,
                 'ID': None,
                 'unit': 'm', 'ramp': None,
                 'pos_min': self._min_third,
                 'pos_max': self._max_third,
                 'pos_step': (self._max_third - self._min_third) / 4096,
                 'pos_accuracy': (self._max_third - self._min_third) / 4096,
                 'vel_min': None,
                 'vel_max': None,
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

        def movement_to_num(mov, ax_id):
            """Translates the movement in meter to a number, that is understood
            by the stage."""
            thread_pitch_xy = 3e-3
            thread_pitch_z = 1.16e-3
            # one rotation corresponds to 2 ** 12
            bit_encoding = 12
            enc = mov * 2 ** bit_encoding
            if ax_id == 'z':
                rot = hex(int(enc / thread_pitch_z))
            else:
                rot = hex(int(enc / thread_pitch_xy))
            return rot

        def format_hex(hn, ln):
            """ Format number into hex that is understood by the stage"""
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

        # the base command contain the 3 bytes that are in front of
        # every movement command.
        base_command = "23 63 3E B3"
        hns = list()
        self.log.warning(f"self._pos {self._pos} before update")
        self._pos.update(param_dict)
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
        return self._pos


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
        self.con.write(bytes.fromhex('23 63 3e 77'))
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
        pass

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
        return True
