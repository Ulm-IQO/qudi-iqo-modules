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
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from PySide2 import QtCore
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.core.module import LogicBase


class CameraLogic(LogicBase):
    """Logic class for controlling a camera.

    Unit Convention:
    - ALL TIME VALUES IN SECONDS throughout (exposure, integration)
    - Binning (NIntegFrames): Integer count (no units)
    - Hardware internally uses CLOCK CYCLES where each cycle = 10ns

    The hardware module handles all conversion: SECONDS → NANOSECONDS → CLOCK CYCLES
    CRITICAL: Hardware integration parameter uses CLOCK CYCLES (10ns each), not nanoseconds or seconds

    Example config for copy-paste:

    camera_logic:
        module.Class: 'camera_logic.CameraLogic'
        connect:
            camera: camera_dummy
        options:
            minimum_exposure_time: 0.05
    """

    # declare connectors
    _camera = Connector(name="camera", interface="CameraInterface")
    # declare config options
    _minimum_exposure_time = ConfigOption(
        name="minimum_exposure_time", default=0, missing="warn"
    )

    # signals
    sigFrameChanged = QtCore.Signal(object)
    sigAcquisitionFinished = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__timer = None
        self._thread_lock = RecursiveMutex()
        self._exposure = 100
        self._gain = 1
        self._last_frame = None

    def on_activate(self):
        """Initialisation performed during activation of the module."""
        camera = self._camera()
        self._exposure = camera.get_exposure()
        self._gain = camera.get_gain()

        self.__timer = QtCore.QTimer()
        self.__timer.setSingleShot(True)
        self.__timer.timeout.connect(self.__acquire_video_frame)
        self.__timer_continuous = QtCore.QTimer()
        self.__timer_continuous.setSingleShot(True)
        self.__timer_continuous.timeout.connect(self.__acquire_continuous_frame)

    def on_deactivate(self):
        """Perform required deactivation."""
        self.__timer.stop()
        self.__timer.timeout.disconnect()
        self.__timer = None

    @property
    def last_frame(self):
        return self._last_frame

    def set_exposure(self, time):
        """Set exposure time of camera in SECONDS

        @param float time: Exposure time in seconds
        """
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
        """Get exposure of hardware in SECONDS

        @return float: Exposure time in seconds
        """
        with self._thread_lock:
            self._exposure = self._camera().get_exposure()
            return self._exposure

    def get_display_units(self):
        """Get the display units setting

        @return str: 'counts' or 'cps'
        """
        with self._thread_lock:
            return self._camera().get_display_units()

    def set_display_units(self, units):
        """Set the display units

        @param str units: 'counts' or 'cps'
        @return bool: Success?
        """
        with self._thread_lock:
            if self.module_state() == "idle":
                return self._camera().set_display_units(units)
            else:
                self.log.warning("Cannot change display units during acquisition")
                return False

    def set_snap_frames(self, num_frames):
        """Set the number of frames for snap acquisition

        @param int num_frames: Number of frames (1-65534)
        @return bool: Success?
        """
        with self._thread_lock:
            if self.module_state() == "idle":
                camera = self._camera()
                if hasattr(camera, "_NFrames"):
                    camera._NFrames = max(1, min(num_frames, 65534))
                    # Apply the change to camera hardware
                    try:
                        camera._apply_camera_settings()
                        self.log.info(f"Snap frames set to {camera._NFrames}")
                        return True
                    except Exception as e:
                        self.log.error(f"Failed to apply snap frames setting: {e}")
                        return False
                else:
                    self.log.warning(
                        "Camera does not support snap frames configuration"
                    )
                    return False
            else:
                self.log.warning("Cannot change snap frames during acquisition")
                return False

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

    def start_single_acquisition(self):
        """Perform snap acquisition and return frames array

        @return numpy array: Acquired frames, or None if failed
        """
        with self._thread_lock:
            if self.module_state() != "idle":
                self.log.error("Cannot snap: module not idle")
                return None

            camera = self._camera()
            if camera is None:
                self.log.error("No camera hardware connected")
                return None

            return camera.start_single_acquisition()

    def save_frames_to_file(self, frames, filepath):
        """Save frames array to .spc3 file

        @param numpy array frames: Frames array
        @param str filepath: Full path where to save
        @return bool: Success?
        """
        with self._thread_lock:
            camera = self._camera()
            if camera is None:
                self.log.error("No camera hardware connected")
                return False

            return camera.save_frames_to_file(frames, filepath)

    def toggle_continuous_acquisition(self, start, settings):
        if start:
            self._start_continuous_acquisition(
                settings["directory"] + "/" + settings["filename_prefix"]
            )
        else:
            self._stop_continuous_acquisition()

    def _start_continuous_acquisition(self, filename):
        with self._thread_lock:
            if self.module_state() == "idle":
                self.module_state.lock()
                exposure = max(self._exposure, self._minimum_exposure_time)
                camera = self._camera()
                if camera.support_live_acquisition():
                    camera.continuous_acquisition(filename)
                    self.total_bytes = 0
                else:
                    camera.start_single_acquisition()
                self.__timer_continuous.start(1000 * exposure)
            else:
                self.log.error(
                    "Unable to start video acquisition. Acquisition still in progress."
                )

    def _stop_continuous_acquisition(self):
        with self._thread_lock:
            if self.module_state() == "locked":
                self.__timer_continuous.stop()
                self._camera().stop_continuous_acquisition()
                self.module_state.unlock()
                self.sigAcquisitionFinished.emit()

    def capture_background_image(self):
        """Capture background image for background subtraction"""
        with self._thread_lock:
            if self.module_state() == "idle":
                camera = self._camera()
                try:
                    result = camera.capture_background_image()
                    if not result:
                        self.log.warning("Failed to capture background image")
                    return result
                except AttributeError:
                    self.log.error("capture_background_image method not implemented")
                    return False
                except Exception as e:
                    self.log.error(f"Error capturing background image: {e}")
                    return False
            else:
                self.log.error(
                    "Unable to capture background. Acquisition still in progress."
                )
                return False

    def enable_background_subtraction(self):
        """Enable background subtraction using captured background

        Can be toggled during live acquisition since it's software-based.
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                result = camera.enable_background_subtraction()
                if not result:
                    self.log.warning("Failed to enable background subtraction")
                return result
            except AttributeError:
                self.log.error("enable_background_subtraction method not implemented")
                return False
            except Exception as e:
                self.log.error(f"Error enabling background subtraction: {e}")
                return False

    def disable_background_subtraction(self):
        """Disable background subtraction

        Can be toggled during live acquisition since it's software-based.
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                result = camera.disable_background_subtraction()
                if not result:
                    self.log.warning("Failed to disable background subtraction")
                return result
            except AttributeError:
                self.log.error("disable_background_subtraction method not implemented")
                return False
            except Exception as e:
                self.log.error(f"Error disabling background subtraction: {e}")
                return False

    def apply_background_subtraction(self, frame):
        """Apply background subtraction to a frame if enabled.

        Mode-independent: works on frames from live, snap, or loaded files.

        @param numpy.ndarray frame: Raw pixel data
        @return numpy.ndarray: Subtracted frame, or original if disabled / no background
        """
        camera = self._camera()
        if hasattr(camera, "apply_background_subtraction"):
            return camera.apply_background_subtraction(frame)
        return frame

    def toggle_background_subtraction(self, start):
        """Toggle software background subtraction (if available)

        Software-based subtraction is applied to each frame without requiring video restart.
        """
        camera = self._camera()
        # Check if background subtraction methods are available
        if not (
            hasattr(camera, "enable_background_subtraction")
            and callable(getattr(camera, "enable_background_subtraction", None))
        ):
            self.log.warning("Background subtraction not available for this camera")
            return

        if start:
            self.enable_background_subtraction()
        else:
            self.disable_background_subtraction()

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

    def __acquire_continuous_frame(self):
        """Execute step in the data recording loop: save one of each control and process values"""
        with self._thread_lock:
            camera = self._camera()
            self.total_bytes = self.total_bytes + camera.get_continuous_memory()
            # Emit the last cached live frame (with background subtraction applied)
            # so the GUI preview stays responsive during continuous acquisition
            frame = camera.get_acquired_data()
            self._last_frame = frame
            self.sigFrameChanged.emit(frame)
            if self.module_state() == "locked":
                self.__timer_continuous.start(
                    1
                )  # wait 1 ms before clearing camera memory

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

    def set_integration(self, integration_seconds):
        """Set hardware integration time in SECONDS

        Hardware internally converts to CLOCK CYCLES (10ns each) via spc.SetCameraPar().

        @param float integration_seconds: Hardware integration time in seconds
        Note: In Normal mode (10.4 µs fixed), this parameter is ignored

        CONVERSION: seconds × 1e9 ns/s ÷ 10 ns/cycle = clock_cycles
        """
        with self._thread_lock:
            if self.module_state() == "idle":
                camera = self._camera()
                try:
                    result = camera.set_hardware_integration(integration_seconds)
                    if result:
                        self.log.info(
                            f"Hardware integration set to {integration_seconds*1e6:.2f} µs"
                        )
                    else:
                        self.log.warning(
                            "Failed to set hardware integration (may be in Normal mode)"
                        )
                except AttributeError:
                    self.log.error("set_hardware_integration method not implemented")
                except Exception as e:
                    self.log.error(f"Error setting hardware integration: {e}")
            else:
                self.log.error(
                    "Unable to set hardware integration time. Acquisition still in progress."
                )

    def set_binning(self, binning):
        """Set temporal binning (NIntegFrames) - INTEGER frame count

        Higher binning increases exposure time proportionally.
        Exposure in seconds = binning × HardwareIntegration × 10ns

        @param int binning: Number of frames to integrate (1-65534)
        """
        with self._thread_lock:
            if self.module_state() == "idle":
                camera = self._camera()
                try:
                    camera.set_binning(binning)
                    # Update exposure time from hardware
                    self._exposure = camera.get_exposure()
                    self.log.info(
                        f"Binning set to {binning} frames, exposure = {self._exposure*1e3:.2f} ms"
                    )
                except Exception as e:
                    self.log.error(f"Error setting binning: {e}")
            else:
                self.log.error("Unable to set binning. Acquisition still in progress.")

    def get_binning(self):
        """Get the current binning value (NIntegFrames)

        @return int: Current binning value
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_binning()
            except Exception as e:
                self.log.error(f"Error getting binning: {e}")
                return 1

    def get_default_save_directory(self):
        """Get the default save directory from hardware config.

        @return str: Default save directory path, or empty string if not configured.
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_default_save_directory()
            except Exception:
                return ""

    def get_trigger_mode(self):
        """Get the current trigger mode.

        @return str: 'no_trigger', 'single_trigger', or 'multiple_trigger'
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_trigger_mode()
            except Exception as e:
                self.log.error(f"Error getting trigger mode: {e}")
                return "no_trigger"

    def get_trigger_frames_per_pulse(self):
        """Get the number of frames per trigger pulse.

        @return int: Frames per pulse (1-100)
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_trigger_frames_per_pulse()
            except Exception as e:
                self.log.error(f"Error getting trigger frames per pulse: {e}")
                return 1

    def set_trigger_mode(self, mode, frames_per_pulse=1):
        """Set the trigger mode and apply it to hardware.

        @param str mode: 'no_trigger', 'single_trigger', or 'multiple_trigger'
        @param int frames_per_pulse: Frames per SYNC_IN pulse (1-100, only for multiple_trigger)
        """
        with self._thread_lock:
            if self.module_state() == "locked":
                self.log.error(
                    "Cannot change trigger mode while acquisition is running."
                )
                return
            camera = self._camera()
            try:
                camera.set_trigger_mode(mode, frames_per_pulse)
            except Exception as e:
                self.log.error(f"Error setting trigger mode: {e}")

    def load_acquisition_file(self, filepath):
        """Load a .spc3 acquisition file for viewing

        Works for both snap and continuous acquisitions.

        @param str filepath: Path to the .spc3 file to load
        @return bool: True if load successful, False otherwise
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                result = camera.load_acquisition_file(filepath)
                if not result:
                    self.log.warning(f"Failed to load file: {filepath}")
                return result
            except Exception as e:
                self.log.error(f"Error loading continuous acquisition file: {e}")
                return False

    def get_loaded_frame_count(self):
        """Get the number of frames in the loaded continuous acquisition file

        @return int: Number of frames, or 0 if no file loaded
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_loaded_frame_count()
            except Exception as e:
                self.log.error(f"Error getting frame count: {e}")
                return 0

    def get_loaded_frame(self, frame_index):
        """Get a specific frame from the loaded continuous acquisition file

        @param int frame_index: Index of frame to retrieve (0-based)
        @return numpy.ndarray: Frame data (rows, cols), or None if error
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_loaded_frame(frame_index)
            except Exception as e:
                self.log.error(f"Error getting frame {frame_index}: {e}")
                return None

    def get_current_frame_index(self):
        """Get the current frame index in the loaded file

        @return int: Current frame index, or -1 if no file loaded
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_current_frame_index()
            except Exception as e:
                self.log.error(f"Error getting current frame index: {e}")
                return -1

    def get_loaded_filepath(self):
        """Get the path of the currently loaded file

        @return str: File path, or None if no file loaded
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_loaded_filepath()
            except Exception as e:
                self.log.error(f"Error getting loaded filepath: {e}")
                return None

    def get_loaded_background(self):
        """Return the background image associated with the currently loaded file.

        Populated automatically when a .bg.npy sidecar exists alongside the
        loaded .spc3 file.

        @return numpy.ndarray or None: float32 (rows, cols), or None if absent
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.get_loaded_background()
            except Exception as e:
                self.log.error(f"Error getting loaded background: {e}")
                return None

    def load_frames_from_memory(self, frames):
        """Load frames directly from memory for viewing

        @param numpy.ndarray frames: Frames array to load
        @return bool: Success?
        """
        with self._thread_lock:
            camera = self._camera()
            try:
                return camera.load_frames_from_memory(frames)
            except Exception as e:
                self.log.error(f"Error loading frames from memory: {e}")
                return False
