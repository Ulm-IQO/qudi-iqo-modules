# -*- coding: utf-8 -*-

"""
This file contains the Qudi Interface for a camera.


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

from abc import ABC, abstractmethod
from qudi.core.module import Base
from qudi.util.immutablekeydict import ImmutableKeyDict
from enum import Enum


class CameraConstraints:
    """
    For the tool chain to work, the Camera constraints need to be implemented
    accordingly to the capabilities for each hardware device and its
    accompanying hardware file.
    """
    def __init__(self):
        # max images might depend on properties such as the  size of the
        # internal memory of camera or local pc.
        self.max_images = 10000
        self.pixel_units = ['Counts', 'Electrons', 'Photons']
        self.ring_of_exposures = {'min': 0.001, 'max': 1.0, 'num': 10}

        # readout settings
        self.readout_times = [10.0e-3, 20.0e-3, 30.0e-3]
        self.responsitivity = {'min': 1.0, 'max': 100.0, 'step': 0.1}

        # external capabilities
        self.shutter = {'states': [True, False], 'speed': {'min': 0.02, 'max': 1.0, 'step': 0.01, 'unit': 's'}}

        # operating modes
        # self.operating_modes = ['default', 'high_sensitivity', 'fast_readout']

        # image acquisition modes
        self._acquisition_modes = ImmutableKeyDict({
                                  'Image': False,
                                  'Software Timed Video': False,
                                  'Image Sequence': False,
                                  'N-Time Image Sequence': False
                                  })

        self._settable_settings = ImmutableKeyDict({
                                    'responsitivity': False,
                                    'bit_depth': False,
                                    'binning': False,
                                    'crop': False
                                    })

        self._operating_modes = None
    @property
    def acquisition_modes(self):
        return self._acquisition_modes

    @acquisition_modes.setter
    def acquisition_modes(self, data):
        for key in data:
            self._acquisition_modes[key] = data[key]
    
    @property
    def settable_settings(self):
        return self._settable_settings

    @settable_settings.setter
    def settable_settings(self, data):
        for key in data:
            self._settable_settings[key] = data[key]
    
    @property
    def operating_modes(self):
        return self._operating_modes

    @operating_modes.setter
    def operating_modes(self, data):
        if self._operating_modes is None:
            self._operating_modes = Enum('OperatingModes', data)    

class ScientificCameraInterface(Base):
    """ This interface is used to define the basic functionality
        of a scientific camera
    """

    @property
    @abstractmethod
    def constraints(self):
        """
        Return the constraints of the device under
        the current hardware settings.
        @return:
        """
        pass

    @property
    @abstractmethod
    def name(self):
        """ Retrieve an identifier of the camera that the GUI can print

        @return string: name for the camera
        """
        pass

    @property
    @abstractmethod
    def size(self):
        """ Retrieve size of the image in pixel

        @return tuple: Size (width, height)
        """
        pass

    @property
    @abstractmethod
    def state(self):
        """ Is the camera ready for an acquisition ?

        @return bool: ready ?
        """
        pass

    @property
    @abstractmethod
    def ring_of_exposures(self):
        """
        Set the ring of exposures.
        The concept of exposures is somewhat generalized in this interface.
        Exposures is a list of values that are cycled through from image to image.

        @param list exposures: list of exposure values
        """
        pass

    @property
    @abstractmethod
    def responsitivity(self):
        """
        Set the responsitivity (input/output gain) of the camera.
        @return:
        """
        pass

    @property
    @abstractmethod
    def readout_time(self):
        """
        Return how long the readout of a single image will take
        @return float time: Time it takes to read out an image from the sensor
        """
        pass

    @property
    @abstractmethod
    def sensor_area_settings(self):
        """
        Binning and extracting a certain part of the sensor e.g. {'binning': (2,2), 'crop' ((0, 127), (0, 255)} takes 4 pixels
        together to 1 and takes from all the pixels an area of 128 by 256
        """
        pass

    @property
    @abstractmethod
    def bit_depth(self):
        """
        Bit depth the camera has
        Return the current
        @return int bit_depth: Number of bits the AD converter has.
        """
        pass

    @property
    @abstractmethod
    def pixel_unit(self):
        """
        Some cameras can be set up to show different outputs.
        @return string mode: i.e. 'Counts', 'Electrons' or 'Photons'
        """
        pass

    @property
    @abstractmethod
    def operating_mode(self):
        """
        Operating mode of the camera. Every operating mode
        encapsulates a bundle of settings that are unique to the camera.
        The operating modes are defined in an Enum class.
        @return:
        """
        pass

    @abstractmethod
    def start_acquisition(self):
        """
        Start an acquisition. The acquisition settings
        will determine if you record a single image, or image sequence.
        """
        pass

    @abstractmethod
    def stop_acquisition(self):
        """
        Stop/abort acquisition
        """
        pass

    @property
    @abstractmethod
    def acquisition_mode(self):
        """
        Tuple (seq_len, repetitions) containing the sequence length
        and the repetitions of the sequence.
        Cases: (1, 1) -> take a single image
               (1, -1) -> Continuously record single images (Video)
               (1 < seq_len, rep >= 1) -> Image stack
               (-1, -1) -> Continuously run through sequence
        """
        pass


    @abstractmethod
    def get_images(self, image_num):
        """
        Get image_num oldest images from the memory.
        @param int image_num: Number of images requested
        @return: numpy nd array of dimension (image_num, px_x, px_y)
        """
        pass

    @property
    @abstractmethod
    def shutter_state(self):
        """
        Return the current shutter state
        """
        pass

    @property
    @abstractmethod
    def shutter_speed(self):
        """
        Configure the speed with which the shutter opens
        and closes
        @return:
        """
        pass
