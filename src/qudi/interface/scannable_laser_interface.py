# -*- coding: utf-8 -*-
"""
Interface file for lasers whose frequency can be scanned and stabilized to a fixed value.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['ScannableLaserInterface', 'ScannableLaserConstraints', 'ScannableLaserSettings',
           'LaserScanMode', 'LaserScanDirection']

from enum import Enum
from abc import abstractmethod
from dataclasses import dataclass
from typing import Sequence, Optional, Tuple

from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint


class LaserScanMode(Enum):
    CONTINUOUS = 0
    REPETITIONS = 1  # Includes single sweep


class LaserScanDirection(Enum):
    UNDEFINED = 0
    UP = 1
    DOWN = 2


@dataclass(frozen=True)
class ScannableLaserConstraints:
    """ """
    value: ScalarConstraint
    unit: str
    speed: ScalarConstraint
    repetitions: ScalarConstraint
    initial_directions: Sequence[LaserScanDirection]
    modes: Sequence[LaserScanMode]

    def __post_init__(self) -> None:
        if not isinstance(self.value, ScalarConstraint):
            raise TypeError(
                f'"value" must be {ScalarConstraint.__module__}.{ScalarConstraint.__name__} type'
            )
        if not isinstance(self.speed, ScalarConstraint):
            raise TypeError(
                f'"speed" must be {ScalarConstraint.__module__}.{ScalarConstraint.__name__} type'
            )
        if not isinstance(self.unit, str):
            raise TypeError(f'"unit" must be str type')
        if not isinstance(self.repetitions, ScalarConstraint):
            raise TypeError(f'"repetitions" must be '
                            f'{ScalarConstraint.__module__}.{ScalarConstraint.__name__} type')
        if not self.repetitions.enforce_int:
            raise ValueError(f'"repetitions" must have ScalarConstraint.enforce_int flag set')
        if len(self.modes) < 1:
            raise ValueError('"mode" sequence must contain at least one element')
        if not all(isinstance(mode, LaserScanMode) for mode in self.modes):
            raise TypeError(f'"mode" sequence must only contain '
                            f'{LaserScanMode.__module__}.{LaserScanMode.__name__}')
        if len(self.initial_directions) < 1:
            raise ValueError('"initial_directions" sequence must contain at least one element')
        if not all(isinstance(d, LaserScanDirection) for d in self.initial_directions):
            raise TypeError(f'"initial_directions" sequence must only contain '
                            f'{LaserScanDirection.__module__}.{LaserScanDirection.__name__}')


@dataclass(frozen=True)
class ScannableLaserSettings:
    """ """
    bounds: Tuple[float, float]
    speed: float
    mode: LaserScanMode
    initial_direction: LaserScanDirection
    repetitions: Optional[int] = 0

    def __post_init__(self):
        min_val, max_val = self.bounds
        if min_val == max_val:
            raise ValueError('"bounds" must represent non-empty span')
        if min_val > max_val:
            raise ValueError('"bounds" must be sorted from small to large value')
        if self.speed <= 0:
            raise ValueError('"speed" must be value > 0')
        if not isinstance(self.mode, LaserScanMode):
            raise TypeError(
                f'"mode" must be {LaserScanMode.__module__}.{LaserScanMode.__name__} type'
            )
        if not isinstance(self.initial_direction, LaserScanDirection):
            raise TypeError(f'"initial_direction" must be '
                            f'{LaserScanDirection.__module__}.{LaserScanDirection.__name__} type')
        if not isinstance(self.repetitions, int):
            raise TypeError(f'"repetitions" must be int type')
        if (self.mode == LaserScanMode.REPETITIONS) and (self.repetitions < 1):
            raise ValueError(
                f'"repetitions" value must be >= 1 for mode {LaserScanMode.REPETITIONS}'
            )


class ScannableLaserInterface(Base):
    """ # TODO Docstring
    """

    @property
    @abstractmethod
    def constraints(self) -> ScannableLaserConstraints:
        """ returns the ScannableLaserConstraints """
        raise NotImplementedError

    @property
    @abstractmethod
    def scan_settings(self) -> ScannableLaserSettings:
        raise NotImplementedError

    @abstractmethod
    def start_scan(self) -> None:
        """ Starts the fine scan of the laser as configured with ScannableLaserSettings. Blocks
        until the scan has actually started. Does nothing if a scan is already running.
        :raises RuntimeError: If something goes wrong
        """
        raise NotImplementedError

    @abstractmethod
    def stop_scan(self) -> None:
        """ Stops the scan immediately. Blocks until the scan has actually stopped. Does nothing if
        no scan is running.
        """
        raise NotImplementedError

    @abstractmethod
    def move_to(self, value: float, blocking: Optional[bool] = False) -> None:
        """ Moves the laser wavelength/frequency to a certain value. Only works if the laser is not
        actively scanning, i.e. the module state must be idle.
        :raises ValueError: If value is out of bounds (see constraints)
        :raises RuntimeError: If called while a laser scan is running, i.e. module_state == 'locked'
        """
        raise NotImplementedError

    @abstractmethod
    def configure_scan(
            self,
            bounds: Tuple[float, float],
            speed: float,
            mode: LaserScanMode,
            repetitions: Optional[int] = 0,
            initial_direction: Optional[LaserScanDirection] = LaserScanDirection.UNDEFINED
    ) -> None:
        """ Configure scan settings. Check actually configuration with "scan_settings" property.
        Can only be called if no scan is running.
        :raises ValueError: If any setting does not comply with constraints or "mode" is set to
        LaserScanMode.REPETITIONS without providing "repetitions" argument.
        :raises RuntimeError: If called while a laser scan is running, i.e. module_state == 'locked'
        """
        raise NotImplementedError
