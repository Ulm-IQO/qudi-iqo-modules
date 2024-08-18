# -*- coding: utf-8 -*-
"""
This file contains the Qudi dummy modules for scanable lasers.

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

__all__ = ['ScannableLaserDummy']

import time
from typing import Optional, Tuple

from qudi.util.mutex import Mutex
from qudi.util.constraints import ScalarConstraint
from qudi.interface.scannable_laser_interface import ScannableLaserInterface
from qudi.interface.scannable_laser_interface import ScannableLaserConstraints
from qudi.interface.scannable_laser_interface import ScannableLaserSettings
from qudi.interface.scannable_laser_interface import LaserScanDirection, LaserScanMode


class ScannableLaserDummy(ScannableLaserInterface):
    """ Simple scannable laser dummy with scan and stabilization functionality.
    # ToDo: Implement LaserScanMode.REPETITIONS

    Example config for copy-paste:

    scannable_laser_dummy:
        module.Class: 'dummy.scannable_laser_dummy.ScannableLaserDummy'
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        # Create constraints
        value = ScalarConstraint(default=0, bounds=(-10, 10), increment=0.001)
        speed = ScalarConstraint(default=1, bounds=(0, 10))
        repetitions = ScalarConstraint(default=0, bounds=(0, 0), increment=0, enforce_int=True)
        self.__constraints = ScannableLaserConstraints(
            value=value,
            unit='V',
            speed=speed,
            repetitions=repetitions,
            initial_direction=(LaserScanDirection.UNDEFINED,
                               LaserScanDirection.DOWN,
                               LaserScanDirection.UP),
            modes=(LaserScanMode.CONTINUOUS,)
        )
        # Create default scan settings
        self.__scan_settings = ScannableLaserSettings(
            bounds=value.bounds,
            speed=speed.default,
            mode=LaserScanMode.CONTINUOUS,
            initial_direction=LaserScanDirection.UNDEFINED,
            repetitions=0
        )

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        pass

    @property
    def constraints(self) -> ScannableLaserConstraints:
        return self.__constraints

    @property
    def scan_settings(self) -> ScannableLaserSettings:
        return self.__scan_settings

    def start_scan(self) -> None:
        with self._thread_lock:
            if self.module_state() != 'locked':
                time.sleep(1)
                self.module_state.lock()
                self.log.info('Laser scan started')

    def stop_scan(self) -> None:
        with self._thread_lock:
            if self.module_state() == 'locked':
                time.sleep(1)
                self.module_state.unlock()
                self.log.info('Laser scan stopped')

    def move_to(self, value: float, blocking: Optional[bool] = False) -> None:
        with self._thread_lock:
            if self.module_state() == 'locked':
                raise RuntimeError('Unable to set laser target. Laser scan in progress.')
            if blocking:
                time.sleep(0.5)
            self.log.info(f'Moved to {value} {self.__constraints.unit}')

    def configure_scan(
            self,
            bounds: Tuple[float, float],
            speed: float,
            mode: LaserScanMode,
            repetitions: Optional[int] = 0,
            initial_direction: Optional[LaserScanDirection] = LaserScanDirection.UNDEFINED
    ) -> None:
        with self._thread_lock:
            if self.module_state() == 'locked':
                raise RuntimeError('Unable to configure scan. Laser scan still in progress.')
            settings = ScannableLaserSettings(bounds=bounds,
                                              speed=speed,
                                              mode=mode,
                                              initial_direction=initial_direction,
                                              repetitions=repetitions)
            self.__constraints.value.check(settings.bounds[0])
            self.__constraints.value.check(settings.bounds[1])
            self.__constraints.speed.check(settings.speed)
            if settings.mode not in self.__constraints.modes:
                raise ValueError(f'Invalid mode "{settings.mode}". '
                                 f'Valid modes are: {self.__constraints.modes}')
            if settings.initial_direction not in self.__constraints.initial_direction:
                raise ValueError(
                    f'Invalid initial_direction "{settings.initial_direction}". '
                    f'Valid initial_directions are: {self.__constraints.initial_direction}'
                )
            if settings.mode == LaserScanMode.REPETITIONS:
                self.__constraints.repetitions.check(settings.repetitions)
            self.__scan_settings = settings
