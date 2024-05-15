# -*- coding: utf-8 -*-
"""
This file contains the Qudi dummy module for the confocal scanner.

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

from qudi.interface.scannable_laser_interface import *
from qudi.util.constraints import ScalarConstraint
from qudi.interface.process_control_interface import *
from fysom import FysomError
from qudi.util.overload import OverloadedAttribute

from typing import Union


class ScannableLaserDummy(ScannableLaserInterface):
    """
    Simple scannable laser dummy with just simple scan functionality.

    Example config for copy-paste:

    scan_laser:
        module.Class: 'dummy.scannable_laser_dummy.ScannableLaserDummy'
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._scan_laser_constraints = None

    def on_activate(self) -> None:
        """ Initialisation performed during activation of the module.
        """
        self._scan_laser_constraints = ScannableLaserConstraints(
            scan_limits=(-10, 10),
            scan_unit='V',
            scan_speed=ScalarConstraint(bounds=(0, 10), default=1),
            scan_modes=LaserScanMode
        )

    def on_deactivate(self) -> None:
        """ Deactivate properly the dummy.
        """

    @property
    def constraints(self) -> ScannableLaserConstraints:  # Is multiple inheritance an issue here?
        """ Read-Only property holding returning ScannableLaserConstraints.
        """
        return self._scan_laser_constraints

    def start_scan(self) -> None:
        """Starts the fine scan of the laser as configured without blocking"""
        self.log.info('Starting scan')
        self.module_state.lock()

    def stop_scan(self) -> None:
        """Stops the scan immediately"""
        try:
            self.module_state.unlock()
            self.log.info('Stopping scan')
        except FysomError:
            pass

    def scan_to(self, value, blocking=False) -> None:
        """
        Fine scans the laser to value, potentially also blocking, to e.g afterwards stabilize the laser there
        """

        self.log.info(f'Starting scan to {value}')

    def set_scan_settings(self, scan_settings: ScannableLaserSettings) -> ScannableLaserSettings:
        """
        Set scan settings such as scan range, scan speed and ScanMode, ScanDirection
        """
        return scan_settings


class StabilizableScannableLaserDummy(ScannableLaserDummy, StabilizationMixin):

    """
    Example config for copy-paste:

    stabilizable_scan_laser:
        module.Class: 'dummy.scannable_laser_dummy.StabilizableScannableLaserDummy'
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._stabilization_active = False

    def start_scan(self) -> None:
        """Starts the fine scan of the laser as configured without blocking"""
        if not self._stabilization_active:  # TODO Take laser out of stabilization automatically, when starting?
            self.log.info('Starting scan')
            self.module_state.lock()
        else:
            self.log.warning('Cannot start scan while stabilization is active')

    def scan_to(self, value, blocking=False) -> None:
        """ Fine scans the laser to value, potentially also blocking, to e.g afterwards stabilize the laser there
        """
        if not self._stabilization_active:
            self.log.info(f'Starting scan to {value}')
        else:
            self.log.warning('Can not start scan while stabilization is active')

    def stabilize_laser(self, state: bool) -> None:
        """Stabilizes the laser frequency at its current position, if the laser can stabilize itself.
        Only possible when laser is not scanning
        """

        if self.is_stable and state:
            self.log.info(f'Stabilization already active')
            return

        if state and self.module_state == 'locked':
            raise RuntimeError('Scan is running. Cannot stabilize the laser')

        self.log.info(f'Stabilization now {state}')
        self._stabilization_active = state

    @property
    def is_stable(self):
        return self._stabilization_active


class ScannableLaserSetpointsDummy(StabilizableScannableLaserDummy, ProcessSetpointInterface):
    """
    A scannable laser with setpoints/working point(s) such as motor positions, therefore also ProcessSetpointInterface

    Example config for copy-paste:

    scan_laser_with_setpoins:
        module.Class: 'dummy.scannable_laser_dummy.ScannableLaserSetpointsDummy'
    """

    constraints = OverloadedAttribute()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._process_setpoint_constraints = None
        self._scan_laser_constraints = None

    def on_activate(self) -> None:

        self._process_setpoint_constraints = ProcessControlConstraints(
            setpoint_channels=('Motor1', 'Motor2'),
            units={'Motor1': 'steps', 'Motor2': 'steps'},
            dtypes={key: int for key in ('Motor1', 'Motor2')}
        )

        self._scan_laser_constraints = ScannableLaserConstraints(
            scan_unit='V',
            scan_speed=ScalarConstraint(bounds=(0, 10), default=1),
            scan_modes=LaserScanMode,
            scan_limits=(-10, 10)
        )

    def on_deactivate(self) -> None:
        """ Deactivate properly the dummy.
        """
        pass

    @constraints.overload('ScannableLaserInterface')
    @property
    def constraints(self) -> ScannableLaserConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._scan_laser_constraints

    @constraints.overload('ProcessSetpointInterface')
    @property
    def constraints(self) -> ProcessControlConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._process_setpoint_constraints

    def set_activity_state(self, channel: str, active: bool) -> None:
        """ Set activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def get_activity_state(self, channel: str) -> bool:
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def set_setpoint(self, channel: str, value: int) -> None:
        """ Set new setpoint for a single channel """
        pass

    def get_setpoint(self, channel: str) -> int:
        """ Get current setpoint for a single channel """
        pass


class ScannableLaserProcessControlDummy(StabilizableScannableLaserDummy, ProcessControlInterface):
    """
    A laser which has actively controlled working point(s) such as diode current or temperature,
    therefore also ProcessControlInterface

    Example config for copy-paste:

    scan_laser_with_process_control:
        module.Class: 'dummy.scannable_laser_dummy.ScannableLaserProcessControlDummy'
    """

    constraints = OverloadedAttribute()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._process_setpoint_constraints = None
        self._scan_laser_constraints = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

        self._process_control_constraints = ProcessControlConstraints(
            setpoint_channels=('DiodeCurrent', 'Temperature'),
            process_channels=('DiodeCurrent_sense', 'Temperature_sense'),
            units={'DiodeCurrent': 'A', 'Temperature': '°C', 'DiodeCurrent_sense': 'A', 'Temperature_sense': '°C'},
            limits={'DiodeCurrent': (0, 10.0), 'Temperature': (20.0, 40.0)},
            dtypes={key: float for key in ('DiodeCurrent', 'Temperature', 'DiodeCurrent_sense', 'Temperature_sense')}
        )

        self._scan_laser_constraints = ScannableLaserConstraints(
            scan_unit='V',
            scan_speed=ScalarConstraint(bounds=(0, 10), default=1),
            scan_modes=LaserScanMode,
            scan_limits=(-10, 10)
        )

    @constraints.overload('ScannableLaserInterface')
    @property
    def constraints(self) -> ScannableLaserConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._scan_laser_constraints

    # Process Control things

    @constraints.overload('ProcessControlInterface')
    @property
    def constraints(self) -> ProcessControlConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._process_control_constraints

    def set_activity_state(self, channel: str, active: bool) -> None:
        """ Set activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def get_activity_state(self, channel: str) -> bool:
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def get_process_value(self, channel: str) -> Union[int, float]:
        """ Get current process value for a single channel """
        pass

    def set_setpoint(self, channel: str, value: Union[int, float]) -> None:
        """ Set new setpoint for a single channel """
        pass

    def get_setpoint(self, channel: str) -> Union[int, float]:
        """ Get current setpoint for a single channel """
        pass
