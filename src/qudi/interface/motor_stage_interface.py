# -*- coding: utf-8 -*-

"""
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

------------------------------------------------------------------------

OVERVIEW

Interface for a set of independently motorized axes (linear or rotation), e.g. a magnet stage
made of Thorlabs Kinesis controllers. All positions are in the axis' own unit ('mm' or 'deg').

Safety contract for implementations:
    - Nothing moves except through move_absolute(), move_relative() and home().
    - Every move target is checked against the axis' soft limits (set_limits()) by the
      implementation itself; moves outside are refused with a ValueError.
    - An axis can be blocked (e.g. unverified unit conversion, or the controller position not
      matching the remembered one). A blocked axis refuses all moves; get_status() says why.
"""

__all__ = ['MotorStageInterface']

from abc import abstractmethod
from typing import Dict, Tuple, Optional, Any

from qudi.core.module import Base


class MotorStageInterface(Base):
    """ Control of independently motorized axes with soft limits. """

    @property
    @abstractmethod
    def axes(self) -> Tuple[str, ...]:
        """ Names of all axes, in configuration order. """
        pass

    @abstractmethod
    def get_axis_info(self, axis: str) -> Dict[str, Any]:
        """ Static information about an axis.

        @return dict: at least 'unit' (str), 'travel' ((min, max) of the stage, or None if unknown)
        """
        pass

    @abstractmethod
    def get_position(self, axis: str) -> float:
        """ Current position of the axis, in its unit. """
        pass

    @abstractmethod
    def get_status(self, axis: str) -> Dict[str, Any]:
        """ Current status of the axis.

        @return dict: at least 'moving' (bool), 'homing' (bool), 'blocked' (str reason or None),
                      'position_mismatch' (dict(saved=..., controller=...) or None),
                      'fault' (str reason or None: motor disabled by the controller, connection
                      lost, ...; cleared only by reenable()),
                      'referenced' (bool: position refers to a home run, not to the power-up
                      position of the controller)
        """
        pass

    @abstractmethod
    def reenable(self, axis: str) -> None:
        """ Clear a motor fault and enable the motor again. Does not move the axis. """
        pass

    @abstractmethod
    def get_limits(self, axis: str) -> Tuple[float, float]:
        """ Soft limits (lower, upper) of the axis, in its unit. """
        pass

    @abstractmethod
    def set_limits(self, axis: str, lower: float, upper: float) -> Tuple[float, float]:
        """ Set the soft limits of the axis. @return the limits actually set """
        pass

    @abstractmethod
    def move_absolute(self, axis: str, position: float) -> None:
        """ Start a move to position. Raises ValueError if refused (limits, blocked, moving). """
        pass

    @abstractmethod
    def move_relative(self, axis: str, distance: float) -> None:
        """ Start a move by distance. Raises ValueError if refused (limits, blocked, moving). """
        pass

    @abstractmethod
    def stop(self, axis: Optional[str] = None) -> None:
        """ Stop the axis immediately, or all axes if axis is None. """
        pass

    @abstractmethod
    def home(self, axis: str) -> None:
        """ Start homing the axis (moves to its home switch, ignoring the soft limits!). """
        pass

    @abstractmethod
    def get_motion_params(self, axis: str) -> Dict[str, Optional[float]]:
        """ Motion parameters, in units of the axis: 'velocity' (unit/s), 'acceleration'
        (unit/s^2), 'backlash' (unit), 'homing_velocity' (unit/s). None if not available. """
        pass

    @abstractmethod
    def set_motion_params(self, axis: str, **params: float) -> Dict[str, Optional[float]]:
        """ Set any of the parameters of get_motion_params(). @return the values read back """
        pass

    @abstractmethod
    def restore_saved_position(self, axis: str) -> None:
        """ Resolve a position mismatch by setting the controller's position counter to the saved
        position. Does not move the axis. """
        pass

    @abstractmethod
    def set_position(self, axis: str, position: float) -> None:
        """ Declare the current position of the axis (sets the controller's position counter).
        Does not move the axis. The axis is not referenced afterwards and its limits are reset to
        the new position. """
        pass

    @abstractmethod
    def accept_controller_position(self, axis: str) -> None:
        """ Resolve a position mismatch by taking the controller's position as the true one.
        Does not move the axis. """
        pass
