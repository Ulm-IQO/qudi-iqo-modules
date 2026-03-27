# -*- coding: utf-8 -*-

"""
This file contains the dummy for a motorized stage interface.

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
from __future__ import annotations

import time
from dataclasses import dataclass

from qudi.core import ConfigOption

from qudi.interface.motor_interface import MotorInterface


@dataclass
class MotorDummyAxis:
    """Generic dummy motor representing one axis."""

    label: str
    pos: float = 0.0
    vel: float = 0.0
    status: int = 0


class MotorDummy(MotorInterface):
    """This is the dummy class to simulate a motorized stage.

    Example config for copy-paste:

    motor_dummy:
        module.Class: 'dummy.motor_dummy.MotorDummy'
        options:
            # Time to wait after each movement in seconds.
            wait_after_movement: 0.1

    """

    wait_after_movement: float = ConfigOption(default=0.1)

    def on_activate(self):
        self._x_axis = MotorDummyAxis("x")
        self._y_axis = MotorDummyAxis("y")
        self._z_axis = MotorDummyAxis("z")
        self._phi_axis = MotorDummyAxis("phi")
        self._axes = [self._x_axis, self._y_axis, self._z_axis, self._phi_axis]

    def on_deactivate(self):
        pass

    def get_constraints(self) -> dict[str, dict[str, float | str | list[str] | None]]:
        x_constraints = {
            "label": self._x_axis.label,
            "unit": "m",
            "ramp": ["Sinus", "Linear"],
            "pos_min": 0,
            "pos_max": 100,
            "pos_step": 0.001,
            "vel_min": 0,
            "vel_max": 100,
            "vel_step": 0.01,
            "acc_min": 0.1,
            "acc_max": 1000.0,
            "acc_step": 0.0,
        }
        y_constraints = {
            "label": self._y_axis.label,
            "unit": "m",
            "ramp": ["Sinus", "Linear"],
            "pos_min": 0,
            "pos_max": 100,
            "pos_step": 0.001,
            "vel_min": 0,
            "vel_max": 100,
            "vel_step": 0.01,
            "acc_min": 0.1,
            "acc_max": 100.0,
            "acc_step": 0.0,
        }
        z_constraints = {
            "label": self._z_axis.label,
            "unit": "m",
            "ramp": ["Sinus", "Linear"],
            "pos_min": 0,
            "pos_max": 100,
            "pos_step": 0.001,
            "vel_min": 0,
            "vel_max": 100,
            "vel_step": 0.01,
            "acc_min": 0.1,
            "acc_max": 100.0,
            "acc_step": 0.0,
        }
        phi_constraints = {
            "label": self._phi_axis.label,
            "unit": "°",
            "ramp": ["Sinus", "Trapez"],
            "pos_min": 0,
            "pos_max": 360,
            "pos_step": 0.1,
            "vel_min": 1,
            "vel_max": 20,
            "vel_step": 0.1,
            "acc_min": None,
            "acc_max": None,
            "acc_step": None,
        }
        return {
            x_constraints["label"]: x_constraints,
            y_constraints["label"]: y_constraints,
            z_constraints["label"]: z_constraints,
            phi_constraints["label"]: phi_constraints,
        }

    def move_rel(self, param_dict: dict[str, float]) -> None:
        if not param_dict:
            return

        cur_pos_dict = self.get_pos()
        constraints = self.get_constraints()

        for axis in self._axes:
            distance = param_dict.get(axis.label)
            if distance is None:
                continue

            cur_constraints = constraints[axis.label]
            pos_min = cur_constraints["pos_min"]
            pos_max = cur_constraints["pos_max"]
            desired_pos = cur_pos_dict[axis.label] + distance
            if not (pos_min <= desired_pos <= pos_max):
                self.log.warning(
                    f"Cannot make further movement of the axis "
                    f'"{axis.label}" with the step {distance}, '
                    f"since the border [{pos_min},{pos_max}] "
                    "was reached. Ignoring the command!"
                )
            else:
                self._make_wait_after_movement()
                axis.pos = desired_pos

    def move_abs(self, param_dict: dict[str, float]) -> None:
        if not param_dict:
            return

        constraints = self.get_constraints()

        for axis in self._axes:
            desired_pos = param_dict.get(axis.label)
            if desired_pos is None:
                continue

            cur_constraints = constraints[axis.label]
            pos_min = cur_constraints["pos_min"]
            pos_max = cur_constraints["pos_max"]
            if not (pos_min <= desired_pos <= pos_max):
                self.log.warning(
                    f"Cannot make absolute movement of the axis "
                    f'"{axis.label}" to position {desired_pos}, '
                    f"since it exceeds the limits "
                    f"[{pos_min},{pos_max}]. Ignoring the command!"
                )
            else:
                self._make_wait_after_movement()
                axis.pos = desired_pos

    def abort(self) -> None:
        self.log.info("MotorDummy: Movement stopped!")

    def get_pos(self, param_list: list[str] | None = None) -> dict[str, float]:
        if param_list is None:
            return {
                self._x_axis.label: self._x_axis.pos,
                self._y_axis.label: self._y_axis.pos,
                self._z_axis.label: self._z_axis.pos,
                self._phi_axis.label: self._phi_axis.pos,
            }

        pos = {}
        for axis in self._axes:
            if axis.label in param_list:
                pos[axis.label] = axis.pos
        return pos

    def get_status(self, param_list: list[str] | None = None) -> dict[str, int]:
        # In the dummy, the status is always 0 (OK)
        if param_list is None:
            return {
                self._x_axis.label: self._x_axis.status,
                self._y_axis.label: self._y_axis.status,
                self._z_axis.label: self._z_axis.status,
                self._phi_axis.label: self._phi_axis.status,
            }

        status = {}
        for axis in self._axes:
            if axis.label in param_list:
                status[axis.label] = axis.status
        return status

    def calibrate(self, param_list: list[str] | None = None) -> None:
        if param_list is None:
            for axis in self._axes:
                axis.pos = 0.0
        else:
            for axis in self._axes:
                if axis.label in param_list:
                    axis.pos = 0.0

    def get_velocity(self, param_list: list[str] | None = None) -> dict[str, float]:
        if param_list is None:
            return {
                self._x_axis.label: self._x_axis.vel,
                self._y_axis.label: self._y_axis.vel,
                self._z_axis.label: self._z_axis.vel,
                self._phi_axis.label: self._phi_axis.vel,
            }

        velocity = {}
        for axis in self._axes:
            if axis.label in param_list:
                velocity[axis.label] = axis.vel
        return velocity

    def set_velocity(self, param_dict: dict[str, float]) -> None:
        if not param_dict:
            return

        constraints = self.get_constraints()

        for axis in self._axes:
            desired_vel = param_dict.get(axis.label)
            if desired_vel is None:
                continue

            cur_constraints = constraints[axis.label]
            vel_min = cur_constraints["vel_min"]
            vel_max = cur_constraints["vel_max"]
            if not (vel_min <= desired_vel <= vel_max):
                self.log.warning(
                    f"Cannot set velocity of the axis "
                    f'"{axis.label}" to {desired_vel}, '
                    f"since it exceeds the limits "
                    f"[{vel_min},{vel_max}]. Ignoring the command!"
                )
            else:
                axis.vel = desired_vel

    def _make_wait_after_movement(self):
        """Define a time which the dummy should wait after each movement."""
        time.sleep(self.wait_after_movement)
