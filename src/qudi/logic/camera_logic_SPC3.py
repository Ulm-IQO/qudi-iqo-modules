# -*- coding: utf-8 -*-

"""Windows-safe variant of the generic CameraLogic module.

This module is identical to ``camera_logic.py`` except for the generated
filename tag: it formats timestamps without characters that are invalid on
Windows (e.g. ':').
"""

import os
import datetime
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
        module.Class: 'camera_logic_custom.CameraLogic'
        connect:
            camera: camera_dummy
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
    sigContinuousStateChanged = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__timer = None
        self.__continuous_timer = None
        self._thread_lock = RecursiveMutex()
        self._exposure = -1
        self._gain = -1
        self._last_frame = None
        self._continuous_active = False
        self._continuous_filepath = ""

    def on_activate(self):
        """Initialisation performed during activation of the module."""
        camera = self._camera()
        self._exposure = camera.get_exposure()
        self._gain = camera.get_gain()

        self.__timer = QtCore.QTimer()
        self.__timer.setSingleShot(True)
        self.__timer.timeout.connect(self.__acquire_video_frame)

        self.__continuous_timer = QtCore.QTimer()
        self.__continuous_timer.setInterval(100)
        self.__continuous_timer.timeout.connect(self._pump_continuous_memory_timer)

    def on_deactivate(self):
        """Perform required deactivation."""
        try:
            self.stop_continuous()
        except Exception:
            pass

        self.__timer.stop()
        self.__timer.timeout.disconnect()
        self.__timer = None

        if self.__continuous_timer is not None:
            self.__continuous_timer.stop()
            self.__continuous_timer.timeout.disconnect()
            self.__continuous_timer = None

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
        """"""
        with self._thread_lock:
            if self.module_state() == "idle":
                if self._continuous_active:
                    self.log.error(
                        "Unable to capture frame: continuous acquisition is active"
                    )
                    return
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
                if self._continuous_active:
                    self.log.error(
                        "Unable to start video: continuous acquisition is active"
                    )
                    return
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

    @property
    def continuous_active(self):
        return bool(self._continuous_active)

    def get_continuous_filepath(self):
        """Return the current/last continuous acquisition output file path."""
        return str(self._continuous_filepath or "")

    def toggle_continuous(self, start, filepath_stem=None):
        """Start/stop continuous acquisition.

        If `filepath_stem` is omitted, a timestamped stem is created in the
        camera's default save directory (if provided) or this module's default
        data dir.
        """
        if start:
            self.start_continuous(filepath_stem=filepath_stem)
        else:
            self.stop_continuous()

    def start_continuous(self, filepath_stem=None):
        """Start continuous acquisition and write to an .spc3 file.

        @param str|None filepath_stem: output path stem (without .spc3)
        @return str: expected .spc3 file path on success, '' on failure
        """
        with self._thread_lock:
            if self._continuous_active:
                self.log.error("Continuous acquisition already active")
                return self.get_continuous_filepath()

            if self.module_state() != "idle":
                self.log.error("Unable to start continuous: acquisition in progress")
                self.sigContinuousStateChanged.emit(False)
                return ""

            camera = self._camera()
            start_method = getattr(camera, "continuous_acquisition", None)
            if not callable(start_method):
                self.log.error("Camera does not support continuous acquisition")
                self.sigContinuousStateChanged.emit(False)
                return ""

            if not filepath_stem:
                directory = ""
                get_dir = getattr(camera, "get_default_save_directory", None)
                if callable(get_dir):
                    directory = (get_dir() or "").strip()
                if not directory:
                    directory = self.module_default_data_dir
                os.makedirs(directory, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                filepath_stem = os.path.join(directory, f"spc3_continuous_{ts}")

            filepath_stem = os.path.normpath(str(filepath_stem))
            if filepath_stem.lower().endswith(".spc3"):
                filepath_stem = filepath_stem[:-5]
            outpath = filepath_stem + ".spc3"

            self.module_state.lock()
            ok = bool(start_method(filepath_stem))
            if not ok:
                self.module_state.unlock()
                self.log.error("Failed to start continuous acquisition")
                self.sigContinuousStateChanged.emit(False)
                return ""

            self._continuous_active = True
            self._continuous_filepath = outpath
            if self.__continuous_timer is not None:
                self.__continuous_timer.start()
            self.sigContinuousStateChanged.emit(True)
            return outpath

    def stop_continuous(self):
        """Stop continuous acquisition and close the output file."""
        with self._thread_lock:
            if not self._continuous_active:
                return

            if self.__continuous_timer is not None:
                self.__continuous_timer.stop()

            camera = self._camera()

            # Best-effort flush a bit before stopping.
            get_mem = getattr(camera, "get_continuous_memory", None)
            if callable(get_mem):
                for _ in range(5):
                    try:
                        get_mem()
                    except Exception:
                        break

            stop_method = getattr(camera, "stop_continuous_acquisition", None)
            if callable(stop_method):
                try:
                    stop_method()
                except Exception as e:
                    self.log.error(f"Failed to stop continuous acquisition: {e}")

            self._continuous_active = False

            # Keep last filepath available for the user.
            if self.module_state() == "locked":
                self.module_state.unlock()
            self.sigContinuousStateChanged.emit(False)

    def pump_continuous_memory(self):
        """Manually flush continuous acquisition memory to disk.

        Useful from notebooks: call this periodically while continuous is running.

        @return int: bytes written in this call (0 if not available)
        """
        with self._thread_lock:
            if not self._continuous_active:
                return 0
            camera = self._camera()
            get_mem = getattr(camera, "get_continuous_memory", None)
            if not callable(get_mem):
                return 0
            try:
                return int(get_mem())
            except Exception as e:
                self.log.error(f"Continuous memory flush failed: {e}")
                return 0

    def _pump_continuous_memory_timer(self):
        # Timer callback: keep it best-effort and non-fatal.
        try:
            self.pump_continuous_memory()
        except Exception:
            pass

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
        safe_ts = time_stamp.strftime("%Y%m%d-%H%M%S-%f")
        return f"{safe_ts}_captured_frame"

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
