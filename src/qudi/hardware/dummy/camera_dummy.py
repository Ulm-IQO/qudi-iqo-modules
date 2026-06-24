"""
Dummy camera module for testing purposes.

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
from collections.abc import Callable

import numpy as np
from numpy import ndarray
from PySide6 import QtCore
from PySide6.QtCore import Signal
from qudi.core import ConfigOption, StatusVar

from qudi.interface.camera_interface import CameraInterface


class _StatusWorker(QtCore.QObject):
    """Worker class that continuously calls a method at a given interval.

    This class is intended to be used with a QThread to run the method in a separate
    thread.

    To start/stop the loop, use the signals sigStartLoop and sigStopLoop, respectively.
    """

    exceptionRaised = QtCore.Signal(Exception)
    _sigStart = QtCore.Signal()
    _sigStop = QtCore.Signal()

    def __init__(self, *args, method: Callable, interval=100, **kwargs):
        super().__init__(*args, **kwargs)
        self._method = method
        self._interval = interval
        self._timer_id = None

        self._sigStart.connect(self._start, QtCore.Qt.BlockingQueuedConnection)
        self._sigStop.connect(self._stop, QtCore.Qt.BlockingQueuedConnection)

    def start(self):
        """Start the status worker."""
        self._sigStart.emit()

    def _start(self):
        self._timer_id = self.startTimer(int(self._interval))

    def stop(self):
        """Stop the status worker."""
        self._sigStop.emit()

    def _stop(self):
        if self._timer_id is not None:
            self.killTimer(self._timer_id)
            self._timer_id = None

    @property
    def is_running(self) -> bool:
        """Check if the status worker is currently running.

        :return: True if running, False otherwise
        """
        return self._timer_id is not None

    def timerEvent(self, event):
        """Call the supplied method at the specified interval."""
        try:
            self._method()
        except Exception as e:
            self.exceptionRaised.emit(e)


def _gaussian2d(x: float, y: float, sigma_x: float, sigma_y: float):
    """Return the value of a 2D Gaussian function at point (x, y)."""
    return np.exp(-((x**2) / (2 * sigma_x**2) + (y**2) / (2 * sigma_y**2)))


class CameraDummy(CameraInterface):
    """A dummy camera interface for testing purposes.

    The camera shows an image of a slowly moving Gaussian blob.

    Example config for copy-paste::

        camera_dummy:
            module.Class: 'dummy.camera_dummy.CameraDummy'
            options:
                size_horizontal: 640
                size_vertical: 480
                saturation_exposure: 0.05
                max_value: 255
    """

    size_horizontal: int = ConfigOption(default=640, converter=int)
    size_vertical: int = ConfigOption(default=480, converter=int)
    saturation_exposure: float = ConfigOption(default=0.05)
    max_value: int = ConfigOption(default=255, converter=int)
    max_fps: int = ConfigOption(default=30, converter=int)

    exposure: float = StatusVar(default=0.045)
    gain: float = StatusVar(default=1.0)

    sigNewImageData = Signal(object)

    def on_activate(self) -> None:
        self._image = None
        self._xmesh, self._ymesh = self._calculate_meshgrid()
        self._init_video_worker()

    def on_deactivate(self) -> None:
        self.stop_acquisition()
        self._close_video_worker()

    def get_name(self) -> str:
        return "Dummy Camera"

    def get_size(self) -> tuple[int, int]:
        return self.size_horizontal, self.size_vertical

    def support_live_acquisition(self) -> bool:
        return True

    def start_live_acquisition(self) -> bool:
        if not self.get_ready_state():
            return False

        self._video_worker.start()
        return True

    def start_single_acquisition(self) -> bool:
        if not self.get_ready_state():
            return False

        self._generate_image()
        return True

    def stop_acquisition(self) -> bool:
        if self.get_ready_state():
            return False

        self._video_worker.stop()
        return True

    def get_acquired_data(self) -> ndarray:
        return self._image

    def set_exposure(self, exposure: float) -> float:
        if exposure < 0.0:
            msg = "Exposure time cannot be negative."
            raise ValueError(msg)
        self.exposure = exposure
        return self.exposure

    def get_exposure(self) -> float:
        return self.exposure

    def set_gain(self, gain: float) -> float:
        if gain < 0.0:
            msg = "Gain cannot be negative."
            raise ValueError(msg)
        self.gain = gain
        return self.gain

    def get_gain(self) -> float:
        return self.gain

    def get_ready_state(self) -> bool:
        return not self._video_worker.is_running

    @property
    def new_image_data_signal(self) -> Signal:
        return self.sigNewImageData

    def _init_video_worker(self):
        interval = int(1000 / self.max_fps)  # Convert FPS to milliseconds
        self._video_thread = QtCore.QThread()
        self._video_worker = _StatusWorker(
            method=self._generate_image, interval=interval
        )
        self._video_worker.moveToThread(self._video_thread)
        self._video_thread.start()
        self._video_worker.exceptionRaised.connect(
            self._on_worker_exception, QtCore.Qt.QueuedConnection
        )

    def _close_video_worker(self):
        if self._video_thread.isRunning():
            self._video_worker.stop()
            self._video_thread.quit()
            self._video_thread.wait()

    def _generate_image(self):
        """Generate a slow moving gaussian blob image to simulate camera data."""
        timestamp = time.time()
        width, height = self.get_size()
        sigma = min(width, height) / 20

        # Ensure gaussian blob is within the image bounds
        amplitude_x = width / 2 - sigma * 4
        amplitude_y = height / 2 - sigma * 4

        # Different frequencies for x and y to create a moving pattern
        x = int(np.sin(timestamp * 0.122) * amplitude_x + width / 2)
        y = int(np.cos(timestamp * 0.143) * amplitude_y + height / 2)

        # Draw a moving gaussian blob
        x_values = self._xmesh - x
        y_values = self._ymesh - y
        image = _gaussian2d(x_values, y_values, sigma, sigma)

        # Apply exposure
        image *= self.max_value
        image *= self.exposure / self.saturation_exposure
        image += np.random.normal(0, 5, image.shape)  # Noise
        image *= self.gain
        image = np.clip(image, 0, self.max_value).astype(np.uint8)

        # Wait to simulate camera acquisition time
        time.sleep(self.exposure)

        self._image = image
        self.sigNewImageData.emit(self._image)

    def _on_worker_exception(self, e: Exception):
        raise e

    def _calculate_meshgrid(self):
        """Calculate the meshgrid for the image size."""
        width, height = self.get_size()
        x_mesh, y_mesh = np.meshgrid(np.arange(width), np.arange(height))
        return x_mesh, y_mesh
