# -*- coding: utf-8 -*-
"""
Interface file for lasers whose frequency can be scanned and also stabilized

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

__all__ = ('ScannableLaserInterface', 'ScannableLaserConstraints', 'LaserScanMode',
           'LaserScanDirection', 'ScannableLaserSettings', 'StabilizationMixin')

from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Union, Iterable, Optional, Tuple

from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint

from qudi.interface.process_control_interface import ProcessControlConstraints, ProcessSetpointInterface, \
    ProcessControlInterface


class LaserScanMode(Enum):
    CONTINUOUS = 0
    FIXED_REPETITIONS = 1  # Includes single sweep


class LaserScanDirection(Enum):
    UP = 0
    DOWN = 1


@dataclass(frozen=True)
class ScannableLaserConstraints:

    scan_limits: Tuple[float, float]
    scan_unit: str
    scan_speed: ScalarConstraint
    scan_modes: Iterable[Union[LaserScanMode, int]]

    def __post_init__(self) -> None:
        # TODO implement sanity checks
        pass


@dataclass(frozen=True)
class ScannableLaserSettings:

    scan_range: Tuple[float, float]
    scan_speed: Union[int, float]
    scan_mode: Union[LaserScanMode, int]
    initial_scan_direction: Union[LaserScanDirection, int]
    scan_repetitions: Optional[int]

    def __post_init__(self) -> None:
        # TODO implement sanity checks
        pass


class ScannableLaserInterface(Base):
    """
    # TODO Docstring
    """

    @property
    @abstractmethod
    def constraints(self) -> ScannableLaserConstraints:
        """
        returns the ScannableLaserConstraints
        """
        pass

    @abstractmethod
    def start_scan(self) -> None:
        """Starts the fine scan of the laser as configured with ScannableLaserSettings without blocking"""
        pass

    @abstractmethod
    def stop_scan(self) -> None:
        """Stops the scan immediately"""
        pass

    @abstractmethod
    def scan_to(self, value, blocking=False) -> None:
        """
        Fine scans the laser to value, potentially also blocking, to e.g afterwards stabilize the laser afterwards
        :raises ValueError: When value is out of constraints
        """
        pass

    @abstractmethod
    def set_scan_settings(self, scan_settings: ScannableLaserSettings) -> ScannableLaserSettings:
        """
        Set scan settings
        Returns the actual ScannableLaserSettings, if e.g. the scan_range was clipped to boundaries
        """
        pass

    # non abstract properties

    @property
    def has_setpoints(self):
        """
        When Laser is a subclass of ProcessSetpointInterface, it has some working points which can be set
        e.g. a Motor Position for a grating tilt or some Operating wavelength.
        """
        return issubclass(self.__class__, ProcessSetpointInterface)

    @property
    def has_controlled_values(self):
        """
        When Laser is a subclass of ProcessControlInterface, it has some actively controlled working points
        e.g. a Diode Current or a Temperature, which also can be read back.
        """
        return issubclass(self.__class__, ProcessControlInterface)

    @property
    def has_hardware_stabilization(self):
        """
        When has the StabilizationMixin, the hardware itself has stabilization capabilities and can be kept at a
        certain value to which it can be scanned.
        """
        return issubclass(self.__class__, StabilizationMixin)


class StabilizationMixin:

    @abstractmethod
    def stabilize_laser(self, state: bool) -> None:
        """
        Stabilizes the laser frequency depending on state at its current position if hardware_stabilization is
        available as stated in ScannableLaserConstraints.
        :raises RuntimeError: If laser is scanning and stabilization is requested.
        """
        pass

    @property
    @abstractmethod
    def is_stable(self):
        """
        Read-only property if the laser is currently stable.
        """
        # TODO Should be read by logic and user informed if out of lock.
        pass






