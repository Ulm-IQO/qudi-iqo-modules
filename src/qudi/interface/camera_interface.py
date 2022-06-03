# -*- coding: utf-8 -*-

"""
This file contains the Qudi Interface for a camera.

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

from abc import abstractmethod
from qudi.core.module import Base


class CameraInterface(Base):
    """ This interface is used to manage and visualize a simple camera
    """

    @abstractmethod
    def get_name(self):
        """ Retrieve an identifier of the camera that the GUI can print

        @return string: name for the camera
        """
        pass

    @abstractmethod
    def get_size(self):
        """ Retrieve size of the image in pixel

        @return tuple: Size (width, height)
        """
        pass

    @abstractmethod
    def support_live_acquisition(self):
        """ Return whether or not the camera can take care of live acquisition

        @return bool: True if supported, False if not
        """
        pass

    @abstractmethod
    def start_live_acquisition(self):
        """ Start a continuous acquisition

        @return bool: Success ?
        """
        pass

    @abstractmethod
    def start_single_acquisition(self):
        """ Start a single acquisition

        @return bool: Success ?
        """
        pass

    @abstractmethod
    def stop_acquisition(self):
        """ Stop/abort live or single acquisition

        @return bool: Success ?
        """
        pass

    @abstractmethod
    def get_acquired_data(self):
        """ Return an array of last acquired image.

        @return numpy array: image data in format [[row],[row]...]

        Each pixel might be a float, integer or sub pixels
        """
        pass

    @abstractmethod
    def set_exposure(self, exposure):
        """ Set the exposure time in seconds

        @param float exposure: desired new exposure time

        @return float: setted new exposure time
        """
        pass

    @abstractmethod
    def get_exposure(self):
        """ Get the exposure time in seconds

        @return float exposure time
        """
        pass

    @abstractmethod
    def set_gain(self, gain):
        """ Set the gain

        @param float gain: desired new gain

        @return float: new exposure gain
        """
        pass

    @abstractmethod
    def get_gain(self):
        """ Get the gain

        @return float: exposure gain
        """
        pass

    @abstractmethod
    def get_ready_state(self):
        """ Is the camera ready for an acquisition ?

        @return bool: ready ?
        """
        pass
