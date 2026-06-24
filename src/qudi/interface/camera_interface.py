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

from numpy.typing import NDArray
from PySide6.QtCore import Signal
from qudi.core.module import Base


class CameraInterface(Base):
    """This interface is used to manage and visualize a simple camera"""

    @abstractmethod
    def get_name(self) -> str:
        """Retrieve an identifier of the camera.

        :return: Name for the camera.
        """
        pass

    @abstractmethod
    def get_size(self) -> tuple[int, int]:
        """Retrieve size of the image in pixel.

        :return: Size (width, height).
        """
        pass

    @abstractmethod
    def support_live_acquisition(self) -> bool:
        """Return whether or not the camera can take care of live acquisition.

        :return: True if supported, False if not.
        """
        pass

    @abstractmethod
    def start_live_acquisition(self) -> bool:
        """Start a continuous acquisition.

        :return: True if the acquisition was successfully started. False if not.
        """
        pass

    @abstractmethod
    def start_single_acquisition(self) -> bool:
        """Start a single acquisition

        :return: True if the acquisition was successfully started. False if not.
        """
        pass

    @abstractmethod
    def stop_acquisition(self) -> bool:
        """Stop/abort live or single acquisition.

        :return: True if the acquisition was successfully stopped. False if not.
        """
        pass

    @abstractmethod
    def get_acquired_data(self) -> NDArray:
        """Return an array of last acquired image.

        Each pixel might be a float, integer or sub pixels.

        :return: image data in format [[row],[row]...].
        """
        pass

    @abstractmethod
    def set_exposure(self, exposure: float) -> float:
        """Set the exposure time in seconds

        :param exposure: Desired new exposure time.
        :return: Setted new exposure time.
        """
        pass

    @abstractmethod
    def get_exposure(self) -> float:
        """Get the exposure time in seconds.

        :return: Exposure time.
        """
        pass

    @abstractmethod
    def set_gain(self, gain: float) -> float:
        """Set the gain.

        :param gain: Desired new gain.
        :return: New exposure gain.
        """
        pass

    @abstractmethod
    def get_gain(self) -> float:
        """Get the gain.

        :return: Exposure gain.
        """
        pass

    @abstractmethod
    def get_ready_state(self) -> bool:
        """Whether or not the camera is ready for acquisition.

        :return: True if ready, False if not.
        """
        pass

    @property
    @abstractmethod
    def new_image_data_signal(self) -> Signal | None:
        """Signal emitted when new image data is available.

        This signal is optional and can be None.

        :return: signal or None.
        """
        pass
