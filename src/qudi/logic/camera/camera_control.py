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

class CameraControl(LogicBase):
    """
    Configure and control a camera. Fulfills tasks like setting the sensitivity, exposure,
    acquisition mode and starting and stopping acquisitions. 
    """

    # declare connectors
    _camera = Connector(name='camera', interface='CameraInterface')
    # declare config options
    _minimum_exposure_time = ConfigOption(name='minimum_exposure_time',
                                          default=0.05,
                                          missing='warn')

    # signals
    sigFrameChanged = QtCore.Signal(object)
    sigAcquisitionFinished = QtCore.Signal()

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.__timer = None
        self._thread_lock = RecursiveMutex()
        self._exposure = -1
        self._gain = -1
        self._last_frame = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        camera = self._camera()
        self._exposure = camera.exposure
        self.sensitivity = camera.sensitivity

        self.__timer = QtCore.QTimer()
        self.__timer.setSingleShot(True)
        self.__timer.timeout.connect(self.__acquire_video_frame)

    def on_deactivate(self):
        """ Perform required deactivation. """
        self.__timer.stop()
        self.__timer.timeout.disconnect()
        self.__timer = None

    @property 
    def exposures(self):
        # TODO safety measures 
        camera = self._camera()
        return camera.exposures

    @exposures.setter
    def exposures(self, exposure_ring):
        # TODO safety measures 
        camera = self._camera()
        camera.exposures = exposure_ring
        return

    @property
    def sensitivity(self):
        camera = self._camera()
        return camera.sensitivity

    @sensitivity.setter
    def sensitivity(self, sensitivity_val):
        camera = self._camera()
        camera.sensitivity = sensitivity_val
        return
