# -*- coding: utf-8 -*-

"""
A module for controlling a camera.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import datetime
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from PySide2 import QtCore
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.core.module import LogicBase


class CameraControlLogic(LogicBase):
    """
    Control a camera.
    """
    # declare connectors
    _camera = Connector(name='camera', interface='ScientificCameraInterface')
    # declare config options
    _minimum_exposure_time = ConfigOption(name='minimum_exposure_time',
                                          default=0.05,
                                          missing='warn')

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._thread_lock = RecursiveMutex()
        self._last_frame = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self.ring_of_exposures
        self.responsitivity

    def on_deactivate(self):
        """ Perform required deactivation. """
        if self.module_state() == 'locked':
            self.stop_acquisition()

    @property
    def camera_constraints(self):
        """
        Fixed once camera is started
        """
        return self._camera().constraints

    @property
    def name(self):
        """
        Fixed from hardware 
        """
        return self._camera().name

    @property
    def size(self):
        """
        Fixed from hardware side 
        """
        return self._camera().size

    @property
    def state(self):
        """
        Non settable property. State changes occur internally. 
        """
        return self._camera().state

    @property
    def ring_of_exposures(self):
        return self._camera().ring_of_exposures

    @ring_of_exposures.setter
    def ring_of_exposures(self, exposures):
        camera_constraints = self._camera().constraints
        with self._thread_lock:
            # first check the constraints
            max_exposure_dur = camera_constraints.ring_of_exposures['max']
            if np.any(exposures > max_exposure_dur):
                self.log.warning("A required exposure duration was larger than the maximum allowed exposure duration")
                return 
            elif len(exposures) > camera_constraints.ring_of_exposures['max_num_of_exposure_times']:
                self.log.warning("The number of exposures to set was larger than allowed")
                return
            else:
                self._camera().ring_of_exposures = exposures
        return

    @property
    def responsitivity(self):
        return self._camera().responsitivity

    @responsitivity.setter
    def responsitivity(self, responsitivity):
        with self._thread_lock:
            # check the constraints:
            max_responsitivity = camera_constraints.responsitivity['max']
            min_responsitivity = camera_constraints.responsitivity['min']
            if np.any(responsitivity > max_responsitivity):
                self.log.warning("The required responsitivity was larger than the maximum allowed responsitivity")
                return
            elif np.any(responsitivity < min_responsitivity):
                self.log.warning("The required responsitivity was larger than the minimum allowed responsitivity")
                return
            else:
                self._camera().responsitivity = responsitivity
        return

    @property
    def readout_time(self):
        """
        Non settable property resulting from a specific state the camera is in.
        """
        return self._camera().readout_time

    @property
    def sensor_area_settings(self):
        """
        
        """
        return self._camera().sensor_area_settings

    @sensor_area_settings.setter
    def sensor_area_settings(self, settings):
        self._camera().sensor_area_settings = settings
        return

    @property
    def bit_depth(self):
        return self._camera().bit_depth

    @bit_depth.setter
    def bit_depth(self, bd):
        camera_constraints = self._camera().constraints
        if hasattr(camera_constraints, 'bit_depth'):
            if bd in camera_constraints.bit_depth:
                self._camera().bit_depth = bd
        else:
            self.log.error("The camera does not support setting the bit depth.")

    def stop_acquisition(self):
        return self._camera().stop_acquisition()
