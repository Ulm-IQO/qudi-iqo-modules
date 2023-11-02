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

import time
import numpy as np
from PySide2 import QtCore
from fysom import FysomError
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.util.constraints import ScalarConstraint
from qudi.interface.magnet_interface import MagnetInterface, MagnetScanData
from qudi.interface.magnet_interface import MagnetControlAxis, MagnetConstraints


class MagnetDummy(MagnetInterface):
    """
    Dummy scanning probe microscope. Produces a picture with several gaussian spots.

    Example config for copy-paste:

    magnet_dummy:
        module.Class: 'dummy.magnet_dummy.MagnetDummy'
        options:
            control_ranges:
                x: [0, 1]  # T (see constraints)
                y: [0, 1]  # T
                z: [0, 1]  # T
            frequency_ranges:
                x: [1, 5000]
                y: [1, 5000]
                z: [1, 1000]
            resolution_ranges:
                x: [1, 10000]
                y: [1, 10000]
                z: [1, 10000]
            position_accuracy:
                x: 10e-3
                y: 10e-3
                z: 10e-3
    """
    # TODO Bool indicators deprecated; Change in scanning probe toolchain

    _threaded = True

    # config options
    _control_ranges = ConfigOption(name='control_ranges', missing='error')
    _frequency_ranges = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges = ConfigOption(name='resolution_ranges', missing='error')
    _position_accuracy = ConfigOption(name='position_accuracy', missing='error')
    _velocity = 0.1  # T/s

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Scan process parameters
        self._current_scan_frequency = -1
        self._current_scan_ranges = [tuple(), tuple()]
        self._current_scan_axes = tuple()
        self._current_scan_resolution = tuple()
        self._current_control = dict()


        # "Hardware" constraints
        self._constraints = None
        # Mutex for access serialization
        self._thread_lock = RecursiveMutex()

        self.__scan_start = 0
        self.__last_line = -1
        self.__update_timer = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Set default process values
        self._current_scan_ranges = tuple(tuple(rng) for rng in tuple(self._control_ranges.values())[:2])
        self._current_scan_axes = tuple(self._control_ranges)[:2]
        self._current_scan_frequency = max(self._frequency_ranges[self._current_scan_axes[0]])
        self._current_scan_resolution = tuple([100] * len(self._current_scan_axes))
        self._current_control = {ax: min(rng) + (max(rng) - min(rng)) / 2 for ax, rng in
                                  self._control_ranges.items()}




        # Generate static constraints
        axes = list()
        for axis, ax_range in self._control_ranges.items():
            dist = max(ax_range) - min(ax_range)
            resolution_range = tuple(self._resolution_ranges[axis])
            frequency_range = tuple(self._frequency_ranges[axis])

            control_value = ScalarConstraint(default=min(ax_range), bounds=ax_range)
            resolution = ScalarConstraint(default=min(resolution_range), bounds=resolution_range, enforce_int=True)
            frequency = ScalarConstraint(default=min(frequency_range), bounds=frequency_range)
            step = ScalarConstraint(default=0, bounds=(0, dist))

            axes.append(MagnetControlAxis(name=axis,
                                    unit='m',
                                    control_value=control_value,
                                    step=step,
                                    resolution=resolution,
                                    frequency=frequency))

        self._constraints = MagnetConstraints(axis_objects=tuple(axes),
                                              has_position_feedback=False)
        self.__scan_start = 0
        self.__last_line = -1
        self.__update_timer = QtCore.QTimer()
        self.__update_timer.setSingleShot(True)
        #self.__update_timer.timeout.connect(self.get_scan_data, QtCore.Qt.QueuedConnection)
        return

    def on_deactivate(self):
        """ Deactivate properly the confocal scanner dummy.
        """
        self.set_activity_state(False)
        # free memory
        try:
            self.__update_timer.stop()
        except:
            pass
        self.__update_timer.timeout.disconnect()

    def constraints(self):
        """

        @return:
        """
        #self.log.debug('Scanning probe dummy "get_constraints" called.')
        return self._constraints

    def get_status(self):
        return {0: 'ready'}

    def set_control(self, control, blocking=False):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.
        """
        with self._thread_lock:
            # self.log.debug('Scanning probe dummy "move_absolute" called.')
            if self.module_state() != 'idle':
                self.log.error('Scanning in progress. Unable to move to position.')
            elif not set(control).issubset(self._control_ranges):
                self.log.error('Invalid axes encountered in position dict. Valid axes are: {0}'
                               ''.format(set(self._control_ranges)))
            else:
                move_distance = {ax: np.abs(pos - self._current_control[ax]) for ax, pos in
                                 control.items()}

                move_time = max(0.01, np.sqrt(
                    np.sum(dist ** 2 for dist in move_distance.values())) / self._velocity)
                if blocking:
                    time.sleep(move_time)
                self._current_control.update(control)

            return self._current_control

    def get_control(self):
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).

        @return dict: current target position per axis.
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "get_position" called.')
            position = {ax: pos + np.random.normal(0, self._position_accuracy[ax]) for ax, pos in
                        self._current_control.items()}
            return position

    def emergency_stop(self):
        """
        """
        try:
            self.module_state.unlock()
        except FysomError:
            pass
        self._scan_image = None
        self.log.warning('Scanner has been emergency stopped.')
        return 0

    def set_activity_state(self, channel: str, active: bool) -> None:
        pass


    def __start_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__update_timer,
                                            'start',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__update_timer.start()

    def __stop_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__update_timer,
                                            'stop',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__update_timer.stop()

