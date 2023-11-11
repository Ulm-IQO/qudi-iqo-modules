# -*- coding: utf-8 -*-
"""
This file contains the Qudi dummy module a magnet.

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
from qudi.interface.magnet_interface import MagnetInterface
from qudi.interface.magnet_interface import MagnetControlAxis, MagnetConstraints


class MagnetDummy(MagnetInterface):
    """
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

    _threaded = True

    # config options
    _control_ranges = ConfigOption(name='control_ranges', missing='error')
    _frequency_ranges = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges = ConfigOption(name='resolution_ranges', missing='error')
    _position_accuracy = ConfigOption(name='position_accuracy', missing='error')
    _velocity = 0.1  # T/s

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._current_control = dict()
        self._constraints = None
        # Mutex for access serialization
        self._thread_lock = RecursiveMutex()


    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

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
                                    unit='T',
                                    control_value=control_value,
                                    step=step,
                                    resolution=resolution))

        self._constraints = MagnetConstraints(axis_objects=tuple(axes),
                                              has_position_feedback=False,
                                              control_accuracy={key: 3*val for key, val in self._position_accuracy.items()})

    def on_deactivate(self):
        self.set_activity_state(False)

    def constraints(self):
        """

        @return:
        """
        return self._constraints

    def get_status(self):
        return {0: 'idle'}

    def set_control(self, control, blocking=False):
        """ Move the magnet to an absolute control value.
        Return current control.
        """
        with self._thread_lock:
            if not set(control).issubset(self._control_ranges):
                self.log.error('Invalid axes encountered in position dict. Valid axes are: {0}'
                               ''.format(set(self._control_ranges)))
            else:
                move_distance = {ax: np.abs(pos - self._current_control[ax]) for ax, pos in
                                 control.items()}

                move_time = max(0.01, np.sqrt(
                    np.sum(dist ** 2 for dist in move_distance.values())) / self._velocity)
                if blocking:
                    if move_time > 1:
                        self.log.debug(f"Magnet will need {move_time} s to reach target control.")
                    time.sleep(move_time)

                self.log.debug(f"Magnet control: {control}")
                self._current_control.update(control)

            return self._current_control

    def get_control(self):
        """ Get a snapshot of the actual magnet control.

        @return dict: current control per axis.
        """
        with self._thread_lock:
            position = {ax: pos + np.random.normal(0, self._position_accuracy[ax]) for ax, pos in
                        self._current_control.items()}
            return position

    def emergency_stop(self):
        """
        """
        self.log.warning('Magnet has been emergency stopped.')
        return 0

    def set_activity_state(self, active, channel=None) -> None:
        pass


