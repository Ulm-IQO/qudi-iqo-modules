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

import datetime
import time
import threading
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from PySide2 import QtCore
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.core.module import LogicBase

from qudi.interface.camera_interface import CameraInterface


class CameraLogic(LogicBase):
    """Logic class for controlling a camera.

    Example config for copy-paste:

    camera_logic:
        module.Class: 'camera_logic_SPC3.CameraLogic'
        connect:
            camera: camera_SPC3
        options:
            minimum_exposure_time: 0.05
    """

    # declare connectors
    _camera = Connector(name="camera", interface=CameraInterface)
    # declare config options
    _minimum_exposure_time = ConfigOption(
        name="minimum_exposure_time", default=0.05, missing="warn"
    )

    # signals
    sigFrameChanged = QtCore.Signal(object)
    sigAcquisitionFinished = QtCore.Signal()
    sigContinuousProgress = QtCore.Signal(dict)
    sigContinuousFinished = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__timer = None
        self._thread_lock = RecursiveMutex()
        self._exposure = -1
        self._gain = -1
        self._last_frame = None

        # continuous acquisition state
        self._cont_active = False
        self._cont_bytes = 0
        self._cont_errors = 0
        self._cont_start_time = 0.0
        self._cont_filename = None
        self._cont_stop_event = threading.Event()
        self._cont_thread = None

    def on_activate(self):
        """Initialisation performed during activation of the module."""
        camera = self._camera()
        self._exposure = camera.get_exposure()
        self._gain = camera.get_gain()

        self.__timer = QtCore.QTimer()
        self.__timer.setSingleShot(True)
        self.__timer.timeout.connect(self.__acquire_video_frame)

    def on_deactivate(self):
        """Perform required deactivation."""
        # Stop continuous acquisition if running
        if self._cont_active:
            self.stop_continuous_acquisition()

        self.__timer.stop()
        self.__timer.timeout.disconnect()
        self.__timer = None

    @property
    def last_frame(self):
        return self._last_frame

    def set_exposure(self, time):
        """Set exposure time of camera"""
        with self._thread_lock:
            if self.module_state() == "idle":
                camera = self._camera()
                camera.set_exposure(time)
                self._exposure = camera.get_exposure()
            else:
                self.log.error(
                    "Unable to set exposure time. Acquisition still in progress."
                )

    def get_exposure(self):
        """Get exposure of hardware"""
        with self._thread_lock:
            self._exposure = self._camera().get_exposure()
            return self._exposure

    def set_gain(self, gain):
        with self._thread_lock:
            if self.module_state() == "idle":
                camera = self._camera()
                camera.set_gain(gain)
                self._gain = camera.get_gain()
            else:
                self.log.error("Unable to set gain. Acquisition still in progress.")

    def get_gain(self):
        with self._thread_lock:
            self._gain = self._camera().get_gain()
            return self._gain

    def capture_frame(self):
        """ """
        with self._thread_lock:
            if self.module_state() == "idle":
                self.module_state.lock()
                camera = self._camera()
                camera.start_single_acquisition()
                self._last_frame = camera.get_acquired_data()
                self.module_state.unlock()
                self.sigFrameChanged.emit(self._last_frame)
                self.sigAcquisitionFinished.emit()
            else:
                self.log.error(
                    "Unable to capture single frame. Acquisition still in progress."
                )

    def toggle_video(self, start):
        if start:
            self._start_video()
        else:
            self._stop_video()

    def _start_video(self):
        """Start the data recording loop."""
        with self._thread_lock:
            if self.module_state() == "idle":
                self.module_state.lock()
                exposure = max(self._exposure, self._minimum_exposure_time)
                camera = self._camera()
                if camera.support_live_acquisition():
                    camera.start_live_acquisition()
                else:
                    camera.start_single_acquisition()
                self.__timer.start(1000 * exposure)
            else:
                self.log.error(
                    "Unable to start video acquisition. Acquisition still in progress."
                )

    def _stop_video(self):
        """Stop the data recording loop."""
        with self._thread_lock:
            if self.module_state() == "locked":
                self.__timer.stop()
                self._camera().stop_acquisition()
                self.module_state.unlock()
                self.sigAcquisitionFinished.emit()

    def __acquire_video_frame(self):
        """Execute step in the data recording loop: save one of each control and process values"""
        with self._thread_lock:
            camera = self._camera()
            self._last_frame = camera.get_acquired_data()
            self.sigFrameChanged.emit(self._last_frame)
            if self.module_state() == "locked":
                exposure = max(self._exposure, self._minimum_exposure_time)
                self.__timer.start(1000 * exposure)
                if not camera.support_live_acquisition():
                    camera.start_single_acquisition()  # the hardware has to check it's not busy

    def create_tag(self, time_stamp):
        return f"{time_stamp}_captured_frame"

    def draw_2d_image(self, data, cbar_range=None):
        # Create image plot
        fig, ax = plt.subplots()
        cfimage = ax.imshow(
            data,
            cmap="inferno",  # FIXME: reference the right place in qudi
            origin="lower",
            interpolation="none",
        )

        if cbar_range is None:
            cbar_range = (np.nanmin(data), np.nanmax(data))
        cbar = plt.colorbar(cfimage, shrink=0.8)
        cbar.ax.tick_params(which="both", length=0)
        return fig

    # ══════════════════════════════════════════════════════════════════
    #  Continuous acquisition
    # ══════════════════════════════════════════════════════════════════
    #
    #  Uses a dedicated daemon thread for polling instead of QTimer.
    #  QTimer.start() silently fails when called from a thread other
    #  than the one that created the timer (e.g. RPyC server thread).
    #  A plain threading.Thread + Event avoids this Qt thread-affinity
    #  issue entirely. This matches the proven working pattern from
    #  SPC3_continuous_acq.ipynb and SPAD_test.ipynb.
    # ══════════════════════════════════════════════════════════════════

    def start_continuous_acquisition(self, filename):
        """Start streaming continuous acquisition data to file.

        A daemon thread polls the camera every ~1 ms to drain its
        memory buffer to disk.

        @param str filename: output path stem (SDK appends .spc3)
        @return bool: True if acquisition started, False on error
        """
        with self._thread_lock:
            if self._cont_active:
                self.log.error("Continuous acquisition already running")
                return False
            if self.module_state() != "idle":
                self.log.error("Cannot start continuous acquisition: module is busy")
                return False

            camera = self._camera()
            if not camera.continuous_acquisition(filename):
                self.log.error("Hardware refused continuous acquisition start")
                return False

            self.module_state.lock()
            self._cont_active = True
            self._cont_bytes = 0
            self._cont_errors = 0
            self._cont_start_time = time.perf_counter()
            self._cont_filename = filename
            self._cont_stop_event.clear()

            self._cont_thread = threading.Thread(
                target=self._cont_poll_loop, daemon=True, name="SPC3-cont-poll"
            )
            self._cont_thread.start()

            self.log.info(f"Continuous acquisition started -> {filename}")
            return True

    def toggle_continuous_acquisition(self, start, filename=None):
        """Start or stop continuous acquisition.

        @param bool start: True to start, False to stop
        @param str filename: output path stem (SDK appends .spc3)
        """
        if start:
            return self.start_continuous_acquisition(filename)
        else:
            return self.stop_continuous_acquisition()

    def stop_continuous_acquisition(self):
        """Stop continuous acquisition and emit final status.

        @return dict: final status with bytes, elapsed_s, errors
        """
        with self._thread_lock:
            if not self._cont_active:
                self.log.warning("No continuous acquisition is running")
                return {}

            # Signal the poll thread to exit
            self._cont_stop_event.set()

        # Wait for poll thread to finish (outside the lock so it can
        # complete its last poll cycle without deadlock)
        if self._cont_thread is not None:
            self._cont_thread.join(timeout=5.0)
            self._cont_thread = None

        with self._thread_lock:
            camera = self._camera()
            camera.stop_continuous_acquisition()

            elapsed = time.perf_counter() - self._cont_start_time
            status = {
                "bytes": self._cont_bytes,
                "elapsed_s": elapsed,
                "errors": self._cont_errors,
                "filename": self._cont_filename,
            }

            self._cont_active = False
            self.module_state.unlock()

            self.log.info(
                f"Continuous acquisition stopped: "
                f"{elapsed:.2f} s, {self._cont_bytes / 1e6:.2f} MB, "
                f"{self._cont_errors} transient errors"
            )
            self.sigContinuousFinished.emit(status)
            self.sigAcquisitionFinished.emit()
            return status

    def get_continuous_status(self):
        """Return current continuous acquisition status.

        @return dict: {'active': bool, 'bytes': int, 'elapsed_s': float,
                       'errors': int, 'filename': str}
        """
        if not self._cont_active:
            return {
                "active": False,
                "bytes": 0,
                "elapsed_s": 0.0,
                "errors": 0,
                "filename": None,
            }
        elapsed = time.perf_counter() - self._cont_start_time
        return {
            "active": True,
            "bytes": self._cont_bytes,
            "elapsed_s": elapsed,
            "errors": self._cont_errors,
            "filename": self._cont_filename,
        }

    def _cont_poll_loop(self):
        """Daemon thread: poll camera memory buffer in a tight loop.

        Runs until ``_cont_stop_event`` is set.  Each iteration drains
        the hardware buffer to disk via ``get_continuous_memory()`` and
        fetches a live preview frame for the GUI.

        Matches the working pattern from camera_logic_SPC3_old
        ``__acquire_continuous_frame`` — no error handling, just poll
        as fast as possible with a 1 ms wait between iterations.
        """
        camera = self._camera()
        self.log.debug("Continuous poll thread started")
        while not self._cont_stop_event.is_set():
            self._cont_bytes += camera.get_continuous_memory()

            # Fetch live preview frame so GUI stays responsive
            frame = camera.get_acquired_data()
            self._last_frame = frame
            self.sigFrameChanged.emit(frame)

            # 1 ms wait before clearing camera memory (matches old logic)
            self._cont_stop_event.wait(0.001)
        self.log.debug("Continuous poll thread exiting")
