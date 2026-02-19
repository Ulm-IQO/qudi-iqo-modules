# -*- coding: utf-8 -*-

"""
Makes a motor usable as a magnet.

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
from typing import Dict, Any, Optional

from overrides import overrides

from qudi.core.connector import Connector
from qudi.interface.magnet_interface import MagnetInterface, MagnetConstraints, MagnetControlAxis, MagnetStatus
from qudi.util.constraints import ScalarConstraint


def _map_axis_constraints(motor_axis_name: str, motor_axis_constraint: Dict[str, Any]) -> MagnetControlAxis:
    pos_min = float(motor_axis_constraint['pos_min'])
    pos_max = float(motor_axis_constraint['pos_max'])
    pos_step = float(motor_axis_constraint['pos_step'])
    control_value = ScalarConstraint(
        default=pos_min,
        bounds=(pos_min, pos_max)
    )
    resolution = ScalarConstraint(
        default=10,
        bounds=(1, int((pos_max - pos_min) / pos_step))
    )
    step = ScalarConstraint(
        default=pos_step,
        bounds=(pos_step, pos_max - pos_min)
    )
    return MagnetControlAxis(
        name=motor_axis_name,
        unit=motor_axis_constraint['unit'],
        control_value=control_value,
        step=step,
        resolution=resolution
    )


def _map_control_accuracy(motor_constraints) -> Optional[Dict[str, float]]:
    if all('pos_accuracy' not in val for val in motor_constraints.values()):
        return None
    return {ax: val['pos_accuracy'] for ax, val in motor_constraints.items() if 'pos_accuracy' in val}


class MagnetMotorInterfuse(MagnetInterface):
    """ Interfuse for handling a motorized stage with a magnet as magnet

    Example config for copy-paste:

    magnet_motor:
        module.Class: 'interfuse.magnet_motor_interfuse.MagnetMotorInterfuse'
        connect:
            motor: motor
    """

    # connectors
    _motor = Connector(interface='MotorInterface', name='motor')

    _cached_constraints = None

    def on_activate(self):
        """ Activate the module and fill status variables.
        """
        self._cached_constraints = self._map_constraints()

    def on_deactivate(self):
        """ Deactivate the module and clean up.
        """

    def _map_constraints(self) -> MagnetConstraints:
        motor_constraints = self._motor().get_constraints()
        axis_objects = tuple(
            _map_axis_constraints(key, value) for key, value in motor_constraints.items())
        constraints = MagnetConstraints(
            axis_objects=axis_objects,
            has_position_feedback=False,
            control_accuracy=_map_control_accuracy(motor_constraints)
        )
        return constraints

    @property
    @overrides
    def constraints(self) -> MagnetConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        return self._cached_constraints

    @overrides
    def get_status(self) -> MagnetStatus:
        is_ready = self._motor().is_ready()
        return MagnetStatus(
            is_ready=is_ready
        )

    @overrides
    def set_control(self, control: Dict[str, float], blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe to an absolute position.

        Log error and return current target position if something fails or a scan is in progress.

        @param dict position: absolute positions for all axes to move to, axis names as keys
        @param float velocity: movement velocity
        @param bool blocking: If True this call returns only after the final position is reached.

        @return dict: new position of all axes
        """
        if not self._motor().is_ready():
            self.log.error('Magnet motor is not ready yet. Movement aborted')
            return self._motor().get_pos()
        self._motor().move_abs(control)
        if blocking:
            self._block_while_not_ready()
        time.sleep(0.1)
        return self._motor().get_pos()

    def _block_while_not_ready(self):
        while True:
            if self._motor().is_ready():
                break
            time.sleep(0.1)

    @overrides
    def get_control(self) -> Dict[str, float]:
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis. 
        """
        return self._motor().get_pos()

    @overrides
    def emergency_stop(self) -> None:
        """

        @return:
        """
        self._motor().abort()
