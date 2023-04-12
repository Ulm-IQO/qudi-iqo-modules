# -*- coding: utf-8 -*-

"""
A module for controlling a camera.

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

import matplotlib.pyplot as plt
from PySide2 import QtCore
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.core.module import LogicBase
from qudi.util.immutablekeydict import ImmutableKeyDict
import numpy as np


class CameraLogic(LogicBase):
    """
    Control a camera.
    """

    # declare connectors
    _camera_control_logic = Connector(name='camera_control_logic', interface='CameraControlLogic')

    # signals
    sigFrameChanged = QtCore.Signal(int, int)
    sigAcquisitionFinished = QtCore.Signal()

    # config options
    # declare precision of the exposure time 
    _exposure_time_precision = ConfigOption(name='exposure_time_precision', default=6)

    
    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._thread_lock = RecursiveMutex()
        self._current_image_number = 0
        self._current_measurement_number = 0

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self.last_frames = None
        self._camera_control_logic().sigDataReceived.connect(self.frame_change)
        self._camera_control_logic().sigAcquisitionFinished.connect(self._acquisition_finished)
        # Map the different acquisition modes to different functions of the camera control:
        self._acquisition_mode_mapper = ImmutableKeyDict(
                {'Image': self._camera_control_logic().start_single_image_acquisition,
                 'Software Timed Video': self._camera_control_logic().start_software_timed_video,
                 'Image Sequence': self._camera_control_logic().start_image_sequence,
                 'N-Time Image Sequence': self._camera_control_logic().start_n_image_sequences,
                 })

    def on_deactivate(self):
        """ Perform required deactivation. """
        if self.module_state() == 'locked':
            self.module_state.unlock()

        self.last_frames = None

        self._camera_control_logic().sigDataReceived.disconnect()
        self._camera_control_logic().sigAcquisitionFinished.disconnect()

    def frame_change(self):
        """ Method that indicates that new data for the frame has been received by camera control"""
        self.current_measurement_number = self.last_frames.shape[0] - 1
        self.current_image_number = self.last_frames[self._current_measurement_number].data.shape[0] - 1
        self.sigFrameChanged.emit(self._current_measurement_number, self._current_image_number)

    def _acquisition_finished(self):
        self.module_state.unlock()
        self.log.info("Acquisition finished. Logic unlocked.")
        self.sigAcquisitionFinished.emit()

    @property
    def current_image_number(self):
        if self._current_image_number > self.last_frames[self._current_measurement_number].data.shape[0]:
            self._current_image_number = self.last_frames[self.current_measurement_number].data.shape[0] - 1
        return self._current_image_number
    
    @current_image_number.setter
    def current_image_number(self, num):
        if num < self.last_frames[self._current_measurement_number].data.shape[0]:
            self._current_image_number = num

    @property
    def current_measurement_number(self):
        if self._current_measurement_number > self.last_frames.shape[0]:
            self._current_measurement_number = self.last_frames.shape[0] - 1
        return self._current_measurement_number
    
    @current_measurement_number.setter
    def current_measurement_number(self, num):
        if num < self.last_frames.shape[0]:
            self._current_measurement_number = num

    @property
    def max_image_num(self):
        return self._camera_control_logic().max_image_num

    @max_image_num.setter
    def max_image_num(self, num):
        self._camera_control_logic().max_image_num = num

    @property
    def expected_image_num(self):
        return self._camera_control_logic().expected_image_num

    @expected_image_num.setter
    def expected_image_num(self, num):
        self._camera_control_logic().expected_image_num = num

    @property
    def last_frames(self):
        return self._camera_control_logic().last_frames

    @last_frames.setter
    def last_frames(self, frames):
        self._last_frames = frames

    @property
    def responsitivity(self):
        return self._camera_control_logic().responsitivity

    @responsitivity.setter
    def responsitivity(self, responsitivity):
        self._camera_control_logic().responsitivity = responsitivity

    @property
    def ring_of_exposures(self):
        return self._camera_control_logic().ring_of_exposures

    @ring_of_exposures.setter
    def ring_of_exposures(self, ring_of_exposures):
        with self._thread_lock:
            self._camera_control_logic().ring_of_exposures = ring_of_exposures
        return

    def capture_frame(self):
        """
        """
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.module_state.lock()
                self._camera_control_logic().start_single_image_acquisition()
            else:
                self.log.error('Unable to capture single frame. Acquisition still in progress.')

    def toggle_acquisition(self, start, mode):
        if start:
            with self._thread_lock:
                if self.module_state() == 'idle':
                    self.module_state.lock()
                    self.log.info(f"Starting acquisition in {mode} mode.")
                    self._acquisition_mode_mapper[mode]()
                else:
                    self.log.error('Unable to start video acquisition. Acquisition still in progress.')
        else:
            with self._thread_lock:
                if self.module_state() == 'locked':
                    self.log.info("Acquisition stop initiated by user.")
                    self._camera_control_logic().request_stop()
                    self.module_state.unlock()

    def create_tag(self, measurement_num, image_num):
        return f"_captured_frame_measurement_{measurement_num}_image_{image_num}"

    def draw_2d_image(self, data, cbar_range=None):
        # Create image plot
        fig, ax = plt.subplots()
        cfimage = ax.imshow(data,
                            cmap='inferno',  # FIXME: reference the right place in qudi
                            origin='lower',
                            interpolation='none')

        if cbar_range is None:
            cbar_range = (np.nanmin(data), np.nanmax(data))
        cbar = plt.colorbar(cfimage, shrink=0.8)
        cbar.ax.tick_params(which=u'both', length=0)
        return fig

    @property
    def available_acquisition_modes(self):
        """
        Getter method of the available acquisition modes
        of the camera
        """
        return self._camera_control_logic().available_acquisition_modes

    def linspace_creation(self, start, step, stop):
        """
        Function that creates a linspace in the half-open interval [start, stop)
        parameters:
            start: float, first value of linspace
            step: float, step between values
            stop: float, all values are lower than this value
        returns:
            linspace: np.array
        """
        # check if start < stop
        if start < stop:
            linspace = np.arange(start=start, stop=stop, step=step, dtype=np.float64)
        if start > stop:
            linspace = np.arange(start=stop, stop=start, step=step, dtype=np.float64)
        if start == stop:
            linspace = np.array([start])
        linspace = np.around(linspace, decimals=self._exposure_time_precision)
        return linspace

    @property
    def bit_depth(self):
        return self._camera_control_logic().bit_depth

    @bit_depth.setter
    def bit_depth(self, bd):
        self._camera_control_logic().bit_depth = bd
    
    @property
    def binning(self):
        return self._camera_control_logic().binning

    @binning.setter
    def binning(self, size):
        """
        Function that sets the binning size for the camera.
        @param size, tuple of int: (x bin width, y bin width)
        """
        self._camera_control_logic().binning = size

    @property
    def crop(self):
        return self._camera_control_logic().crop

    @crop.setter
    def crop(self, size):
        """
        Function that sets the binning size for the camera.
        @param size, tuple of int: (x bin width, y bin width)
        """
        self._camera_control_logic().crop = size

    @property
    def constraints(self):
        return self._camera_control_logic().constraints
    
    @property
    def operating_mode(self):
        return self._camera_control_logic().operating_mode

    @operating_mode.setter
    def operating_mode(self, data):
        self._camera_control_logic().operating_mode = data
