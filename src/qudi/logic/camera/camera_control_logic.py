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

import numpy as np
from PySide2 import QtCore
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.core.module import LogicBase
from qudi.util.uic import module_from_spec


class CameraControlLogic(LogicBase):
    """
    Control a camera.
    """
    # declare connectors
    _camera = Connector(name='camera', interface='ScientificCameraInterface')
    
    # declare config options
    
    # the software timer puffer is a puffer time in ms that is added to the 
    # exposure time and readout time of the camera to get rid of inconsistencies
    # in QTimer, causing it to finish early and thus collecting data too early
    # This timer can be set individually, depending on the speed and consistency of your PC
    _software_timer_puffer = ConfigOption(name='software_timer_puffer',
                                          default=0,
                                          missing='warn')
   
    # maximum number of images that can be stored in the memory of the PC at once
    # TODO let this number be changed in the GUI as well
    _max_image_num = ConfigOption(name='max_image_num',
                                  default=1,
                                  missing='warn')

    # Signal that is emitted upon receiving new data from the hardware
    sigDataReceived = QtCore.Signal()
    sigAcquisitionFinished = QtCore.Signal()

    # internal timer signals to start and stop the QTimer readout loop
    sigStartInternalTimerVideo = QtCore.Signal()
    sigStopInternalTimerVideo = QtCore.Signal()
    
    sigStartInternalTimerImage = QtCore.Signal()
    sigStopInternalTimerImage = QtCore.Signal()

    _received_data = None

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._thread_lock = RecursiveMutex()
        self._last_frames = None
        self._stop_requested = True
        self.expected_image_num = 1

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Initialize the software timer for the readout and acquisition loops for video acquisition
        self.__video_software_timer = QtCore.QTimer()
        self.__video_software_timer.setSingleShot(True)
        self.__video_software_timer.setInterval(self.software_timer_interval())

        # Initialize the software timer for readout of a single image
        self.__image_software_timer = QtCore.QTimer()
        self.__image_software_timer.setSingleShot(True)
        self.__image_software_timer.setInterval(self.software_timer_interval())

        # connect the signals to the correct functions
        # connect the timer timeout to the video acquisition loop
        self.__video_software_timer.timeout.connect(self.software_controlled_acquisition_loop)
        self.__image_software_timer.timeout.connect(self.acquire_single_image)

        # connect the timer's start stop signals
        self.sigStartInternalTimerVideo.connect(self.__video_software_timer.start)
        self.sigStopInternalTimerVideo.connect(self.__video_software_timer.stop)
        self.sigStartInternalTimerImage.connect(self.__image_software_timer.start)
        self.sigStopInternalTimerImage.connect(self.__image_software_timer.stop)

    def on_deactivate(self):
        """ Perform required deactivation. """
        # stop any ongoing acquisition
        if self.module_state() == 'locked':
            self.stop_data_acquisition()
            self.module_state.unlock()
        
        # disconnect all signals
        self.__video_software_timer.timeout.disconnect()
        self.sigStartInternalTimerVideo.disconnect()
        self.sigStopInternalTimerVideo.disconnect()
        
        # clear the stored frame data
        self._last_frames = None

    def start_single_image_acquisition(self):
        """
        Method for acquiring a single image from the hardware
        """
        # check whether an acquisition is already running
        if self.module_state() == 'idle':
            if len(self.ring_of_exposures) > 1:
                self.log.warn(f'Multiple exposure times set. Using the first exposure time {self.ring_of_exposures[0]} s.')
                self.ring_of_exposures = [self.ring_of_exposures[0]]

            # set the number of expected images, received from the hardware
            self.expected_image_num = 1
            # lock the module
            self.module_state.lock()
            # set the correct acquisition mode
            self._camera().acquisition_mode = (1, 1)
            # set timer to the correct interval
            # this is the set exposure time + the readout time of the camera
            self.__image_software_timer.setInterval(self.software_timer_interval())
            # tell the camera to start the acquisition
            self._camera().start_acquisition()
            # start the timer for the acquisition readout
            self.sigStartInternalTimerImage.emit()
        else:
            self.log.error('Unable to capture single frame. Acquisition still in progress.')

    def acquire_single_image(self):
        """
        Method that receives the single image data
        """
        self.receive_frames()
        self.sigDataReceived.emit()
        self.sigAcquisitionFinished.emit()
        self.module_state.unlock()
    
    def start_software_timed_video(self):
        """
        Method to acquire a live video.
        The hardware is told to record a single image.
        This is repeated upon receiving the signal of an internal software timer
        """
        if self.module_state() == 'idle':
            # lock the module
            self.module_state.lock()
            if len(self.ring_of_exposures) > 1:
                self.log.warn(f'Multiple exposure times set. Using the first exposure time {self.ring_of_exposures[0]} s.')
                self.ring_of_exposures = [self.ring_of_exposures[0]]

            # set the number of expected images, received from the hardware
            self.expected_image_num = 1
            # reset the stop request
            self.stop_requested = False
            # set the camera's acquisition mode
            self._camera().acquisition_mode = (1,-1)
            # set timer to the correct interval
            # this is the set exposure time + the readout time of the camera
            self.__video_software_timer.setInterval(self.software_timer_interval())
            # start the acquisition of the hardware
            self._camera().start_acquisition()
            # start the timer
            self.sigStartInternalTimerVideo.emit()
        else:
            self.log.error('Unable to capture single frame. Acquisition still in progress.')

    def start_image_sequence(self):
        """
        Method that starts the hardware timed acquisition of an image sequence
        of length m.
        """
        if self.module_state() == 'idle':
            # lock the module
            self.module_state.lock()
            # reset the stop request
            self.stop_requested = False
            ####
            ###
            ##
            ##
            #
            #
            #
            ##
            #
            # TODO
            # test with 5 images
            self.expected_image_num = len(self.ring_of_exposures)
            # set the camera's acquisition mode
            self._camera().acquisition_mode = (1, self.expected_image_num)
            # set timer to the correct interval
            # this is the set exposure time + the readout time of the camera
            self.__image_software_timer.setInterval(self.software_timer_interval())
            # tell the camera to start the acquisition
            self._camera().start_acquisition()
            # start the timer for the acquisition readout
            self.sigStartInternalTimerImage.emit()
        else:
            self.log.error('Unable to capture single frame. Acquisition still in progress.')

    
    def start_n_image_sequences(self):
        self.log.warn("camera_control: start_n_image_sequences (not implemented)")
        self.sigAcquisitionFinished.emit()

    def software_controlled_acquisition_loop(self):
        """
        Method to query data from the hardware by utilizing an internal software-controlled timer
        of this logic module.
        """
        # check whether an acquisition is running, else do nothing
        if self.module_state() == "locked":
            # check whether a stop was requested
            if self.stop_requested:
                self.sigStopInternalTimerVideo.emit()
                # emit the acquisition finished signal
                self.sigAcquisitionFinished.emit()
                # quit the loop and unlock the module
                self.module_state.unlock()
                return
            # collect image data from the hardware
            self.receive_frames()
            self.sigDataReceived.emit()
            # start the acquisition of an image
            self._camera().start_acquisition()
            # restart the timer
            self.sigStartInternalTimerVideo.emit()
    
    def start_data_acquisition(self):
        """
        Start the acquisition of data.
        """
        with self._thread_lock:
            if self.module_state() == 'idle':
                # check whether the acquisition mode is supported
                # lock the module
                self.module_state.lock()
                #single image acquisition
                if self.acquisition_mode == (1,1):
                    # tell the hardware to start the acquisition
                    self._camera().start_acquisition()
                # live video acquisition
                if self.acquisition_mode == (1,-1):
                    self.stop_requested = False
                    self.live_video_acquisition()
                # if no condition is met, abort the measurement start and unlock the module
                else:
                    self.module_state.unlock()
            else:
                self.log.error('Unable to capture single frame. Acquisition still in progress.')

    def stop_data_acquisition(self):
        """Stop the acquisition of data"""
        with self._thread_lock:
            if self.module_state() == 'locked':
                # tell the hardware to stop the acquisition
                self._camera().stop_acquisition()
                # unlock the module after acquisition
                self.module_state.unlock()
            else:
                self.log.error("Can not stop measurement. No measurement running.")

    def software_timer_interval(self):
        """
        Method to calculate the software timer interval for the current measurement
        in milliseconds

        returns:
            int, interval of the software timer in milliseconds
        """
        # the timer interval consists of the length of exposure for a single image
        # the readout dead time of the camera and a puffer that takes care of the
        # QTimer sometimes finishing early and thus causing readout errors
        out = 0
        for time in self.ring_of_exposures:
            out += 1000 * (time + self._camera().readout_time)
                
        out = int(out + self._software_timer_puffer)
        return out

    def receive_frames(self):
        self.last_frames = self._camera().get_images(self.expected_image_num)

    def request_stop(self):
        """
        Method to send a stop request of the current measurement
        to the hardware and this logic module's timer.
        """
        if self.module_state() == 'locked':
            # set the stop_requested flag
            self.stop_requested = True
            # emit the internal Timer stop signal if the timers are running
            if self.__video_software_timer.isActive():
                self.sigStopInternalTimerVideo.emit()
            if self.__image_software_timer.isActive():
                self.sigStopInternalTimerImage.emit()
            # tell the camera to stop acquiring
            self._camera().stop_acquisition()
            # unlock the module
            self.module_state.unlock()

        # emit the acquisition finished signal
        self.sigAcquisitionFinished.emit()

    @property
    def max_image_num(self):
        return self._max_image_num

    @max_image_num.setter
    def max_image_num(self, num):
        self._max_image_num = num
    
    @property
    def expected_image_num(self):
        return self._expected_image_num

    @expected_image_num.setter
    def expected_image_num(self, num):
        if num > self.max_image_num:
            self.log.warn("Number of images that should be stored in PC's memory is greater than the maximum allowed number of images in memory.")
        self._expected_image_num = num


    @property
    def last_frames(self):
        return self._last_frames

    @last_frames.setter
    def last_frames(self, data):
        currently_stored_data = self._last_frames
        # if the data array has not been created yet
        if currently_stored_data is None:
            # write the data into the array if its length is smaller than the max number of images
            self._last_frames = np.empty(1, dtype=object)
            if data.shape[0] <= self._max_image_num:
                self._last_frames[0] = data
                return
            # else just use the last recorded images
            self._last_frames[0] = data[-self._max_image_num:]
            self.log.warn(f"The number of received images ({data.shape[0]}) is larger than the maximum number of allowed images ({self._max_image_num}). Thus, only the last {self._max_image_num} images will be stored in memory.")
            return
        # if the number of images newly recorded is greater or equal than the 
        # maximum number of images allowed in memory, directly store the newest
        if data.shape[0] >= self._max_image_num:
            self._last_frames = np.empty(1, dtype=object)
            self.log.warn(f"The number of received images ({data.shape[0]}) is larger than the maximum number of allowed images ({self._max_image_num}). Thus, only the last {self._max_image_num} images will be stored in memory.")
            self._last_frames[0] = data[-self._max_image_num:]
            return

        # check whether the total number of images exceeds the maximum allowed images to be stored in memory
        # it is calculated out of the sum over the number of images in the sequences (2nd dimension of array)
        # in each measurement (1st dimension of array)
        total_number_of_images = np.zeros(currently_stored_data.shape[0], dtype=int)
        # go through all already stored images and add them up
        # save the number of images for each measurement num as this might be
        # needed later, when stripping any overlength measurement numbers
        for measurement_num, array in enumerate(currently_stored_data):
            if measurement_num == 0:
                total_number_of_images[0] = array.shape[0]
                continue
            total_number_of_images[measurement_num] = total_number_of_images[measurement_num-1] + array.shape[0]
        # calculate, whether the total number of images + the newly acquired number of images is
        # smaller than or equal to the maximum allowed image number
        image_diff_to_max = total_number_of_images[-1] + data.shape[0] - self._max_image_num
        index_to_cut_to = None
        #if this is not the case remove the first elements to make room
        if image_diff_to_max > 0:
            # get the first index of self._last_frames, where the number of images up to that 
            # index is greater than the number of images that need to be cut
            index_to_cut_to = np.nonzero(total_number_of_images > image_diff_to_max)[0][0]
            currently_stored_data = currently_stored_data[index_to_cut_to+1:]

        # append data to the already stored data array
        data_to_store = np.empty(currently_stored_data.shape[0]+1, dtype=object)
        data_to_store[-1] = data
        data_to_store[0:currently_stored_data.shape[0]] = currently_stored_data
        self._last_frames = data_to_store

    @property
    def stop_requested(self):
        return self._stop_requested
    
    @stop_requested.setter
    def stop_requested(self, switch: bool):
        self._stop_requested = switch

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
            if np.any(np.array(exposures) > max_exposure_dur):
                self.log.warn("A required exposure duration was larger than the maximum allowed exposure duration")
                return 
            elif len(exposures) > camera_constraints.ring_of_exposures['max_num_of_exposure_times']:
                self.log.warn("The number of exposures to set was larger than allowed")
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
            max_responsitivity = self.camera_constraints.responsitivity['max']
            min_responsitivity = self.camera_constraints.responsitivity['min']
            if np.any(np.array(responsitivity) > max_responsitivity):
                self.log.warn("The required responsitivity was larger than the maximum allowed responsitivity")
                return
            elif np.any(np.array(responsitivity) < min_responsitivity):
                self.log.warn("The required responsitivity was larger than the minimum allowed responsitivity")
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
            self.log.warn(f"The camera does not support setting the bit depth. Using the value {self._camera().bit_depth} instead.")


    @property
    def acquisition_mode(self):
        return self._camera().acquisition_mode

    @acquisition_mode.setter
    def acquisition_mode(self,mode):
        self._camera().acquisition_mode = mode

    @property
    def available_acquisition_modes(self):
        """
        Getter method of the available acquisition modes
        of the camera
        """
        return self._camera().available_acquisition_modes
    
    @property
    def binning(self):
        return self._camera().binning

    @binning.setter
    def binning(self, size):
        """
        Function that sets the binning size for the camera.
        @param size, tuple of int: (x bin width, y bin width)
        """
        # TODO: Check the constraints that cropping is within the allowed pixel numbers
        # within (0,0) and (max_px_num, max_px_num)
        if len(size) == 2:
            self._camera().binning = size

    @property
    def crop(self):
        return self._camera().crop

    @crop.setter
    def crop(self, size):
        """
        Function that sets the binning size for the camera.
        @param size, tuple of int: (x bin width, y bin width)
        """
        # TODO: Check the constraints that cropping is within the allowed pixel numbers
        # within (0,0) and (max_px_num, max_px_num)
        if len(size) == 2 and len(size[0]) == 2 and len(size[1]) == 2:
            self._camera().crop = size

    @property
    def constraints(self):
        return self._camera().constraints

    @property
    def operating_mode(self):
        return self._camera().operating_mode
    
    @operating_mode.setter
    def operating_mode(self, data):
        self._camera().operating_mode = data
