# -*- coding: utf-8 -*-

"""
Logic module for controlling the SPC3 SPAD camera.

Provides continuous acquisition support through the qudi CameraLogic layer.
Connects to the SPC3 hardware module (spc3_qudi.SPC3_Qudi).

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

import time as _time
import threading
import os
import numpy as np
import matplotlib.pyplot as plt
from PySide2 import QtCore
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.core.module import LogicBase


class CameraLogic(LogicBase):
    """Logic class for controlling the SPC3 SPAD camera.

    Unit Convention:
    - ALL TIME VALUES IN SECONDS throughout (exposure, integration)
    - Binning (NIntegFrames): Integer count (no units)
    - Hardware internally uses CLOCK CYCLES where each cycle = 10ns

    The hardware module handles all conversion: SECONDS -> CLOCK CYCLES.

    Example config for copy-paste:

    camera_logic:
        module.Class: 'camera_logic_SPC3.CameraLogic'
        connect:
            camera: camera_SPC3
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

        # Continuous acquisition tracking
        self._cont_total_bytes = 0
        self._cont_errors = 0
        self._cont_start_time = None
        self._cont_filename = None
        self._cont_polling_active = False
        self._cont_poll_thread = None
        self._cont_stop_phase = "idle"
        self._cont_last_sdk_total = 0
        self._cont_error_streak = 0
        self._cont_last_error = None
        self._cont_last_success_ts = None
        self._cont_preview_interval_s = 0.1
        self._cont_next_preview_ts = 0.0
        self._cont_last_error_log_ts = 0.0

    def _normalize_continuous_filepath(self, filepath):
        """Normalise and preflight an output filepath stem for SPC3 streaming."""
        if filepath is None:
            raise ValueError("Output filepath must not be None")

        stem = os.path.abspath(os.path.normpath(str(filepath).strip()))
        if not stem:
            raise ValueError("Output filepath must not be empty")
        if stem.lower().endswith(".spc3"):
            stem = stem[:-5]

        out_file = stem + ".spc3"
        if len(out_file) > 1024:
            raise ValueError(
                "Output filepath is too long for SPC3 SDK (max 1024 chars)"
            )

        directory = os.path.dirname(stem)
        if directory:
            os.makedirs(directory, exist_ok=True)

            probe = os.path.join(directory, ".spc3_write_test.tmp")
            with open(probe, "wb") as fh:
                fh.write(b"")
            os.remove(probe)

        return stem

    def _update_continuous_bytes_from_sdk_total(self, sdk_total):
        """Update monotonic byte accounting from SDK total-bytes counter."""
        total = int(sdk_total)
        if total < self._cont_last_sdk_total:
            delta = total
        else:
            delta = total - self._cont_last_sdk_total
        self._cont_last_sdk_total = total
        self._cont_total_bytes += max(0, delta)
        return max(0, delta)

    def _drain_before_stop(
        self, camera, max_cycles=100, stable_cycles=3, sleep_s=0.002
    ):
        """Drain remaining camera memory before stop to minimise data loss."""
        drained = 0
        stable = 0
        errors = 0

        for _ in range(max_cycles):
            try:
                sdk_total = camera.get_continuous_memory()
                delta = self._update_continuous_bytes_from_sdk_total(sdk_total)
                drained += delta
                self._cont_last_success_ts = _time.time()
                if delta == 0:
                    stable += 1
                    if stable >= stable_cycles:
                        break
                else:
                    stable = 0
            except Exception as e:
                errors += 1
                self._cont_errors += 1
                self._cont_last_error = str(e)
                if errors >= 3:
                    break
            _time.sleep(sleep_s)

        return {"drained_bytes": drained, "drain_errors": errors}

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
        # Stop continuous acquisition polling thread if running
        self._cont_polling_active = False
        if self._cont_poll_thread is not None and self._cont_poll_thread.is_alive():
            self._cont_poll_thread.join(timeout=2.0)
            self._cont_poll_thread = None

        if self.__timer is not None:
            self.__timer.stop()
            self.__timer.timeout.disconnect()
            self.__timer = None

    # ══════════════════════════════════════════════════════════════════
    #  Properties
    # ══════════════════════════════════════════════════════════════════

    @property
    def last_frame(self):
        return self._last_frame

    # ══════════════════════════════════════════════════════════════════
    #  Exposure / gain
    # ══════════════════════════════════════════════════════════════════

    def set_exposure(self, time):
        """Set exposure time of camera in SECONDS."""
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
        """Get exposure of hardware in SECONDS."""
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

    # ══════════════════════════════════════════════════════════════════
    #  Live video acquisition
    # ══════════════════════════════════════════════════════════════════

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
        """Execute step in the data recording loop."""
        with self._thread_lock:
            camera = self._camera()
            self._last_frame = camera.get_acquired_data()
            self.sigFrameChanged.emit(self._last_frame)
            if self.module_state() == "locked":
                exposure = max(self._exposure, self._minimum_exposure_time)
                self.__timer.start(1000 * exposure)
                if not camera.support_live_acquisition():
                    camera.start_single_acquisition()

    # ══════════════════════════════════════════════════════════════════
    #  Continuous acquisition
    # ══════════════════════════════════════════════════════════════════

    def start_continuous_acquisition(self, filepath):
        """Start continuous acquisition, streaming data to file.

        @param str filepath: path stem (SDK appends .spc3)
        @return bool: True if acquisition started successfully
        """
        with self._thread_lock:
            if self.module_state() != "idle":
                self.log.error(
                    "Unable to start continuous acquisition. "
                    "Acquisition still in progress."
                )
                return False

            camera = self._camera()
            if not camera.support_live_acquisition():
                self.log.error("Camera does not support continuous acquisition.")
                return False

            try:
                filepath = self._normalize_continuous_filepath(filepath)
            except Exception as e:
                self.log.error(f"Invalid continuous-acquisition path: {e}")
                return False

            # Start the hardware continuous acquisition
            try:
                ok = camera.continuous_acquisition(filepath)
            except Exception as e:
                self.log.error(f"Hardware start failed: {e}")
                return False
            if not ok:
                self.log.error("Hardware refused continuous acquisition start.")
                return False

            # Lock the module and initialise tracking state
            self.module_state.lock()
            self._cont_total_bytes = 0
            self._cont_errors = 0
            self._cont_start_time = _time.time()
            self._cont_filename = filepath
            self._cont_stop_phase = "running"
            self._cont_last_sdk_total = 0
            self._cont_error_streak = 0
            self._cont_last_error = None
            self._cont_last_success_ts = self._cont_start_time
            self._cont_next_preview_ts = self._cont_start_time
            self._cont_last_error_log_ts = 0.0

            # Start background polling thread to drain camera memory
            self._cont_polling_active = True
            self._cont_poll_thread = threading.Thread(
                target=self._continuous_poll_loop,
                name="SPC3-ContAcq-Poll",
                daemon=True,
            )
            self._cont_poll_thread.start()
            self.log.info(f"Continuous acquisition started -> {filepath}")
            return True

    def get_continuous_status(self):
        """Return status dict for a running continuous acquisition.

        @return dict: keys 'active', 'elapsed_s', 'bytes', 'errors'
        """
        with self._thread_lock:
            if self.module_state() == "locked" and self._cont_start_time is not None:
                return {
                    "active": True,
                    "elapsed_s": _time.time() - self._cont_start_time,
                    "bytes": self._cont_total_bytes,
                    "errors": self._cont_errors,
                    "phase": self._cont_stop_phase,
                    "last_error": self._cont_last_error,
                }
            return {
                "active": False,
                "elapsed_s": 0,
                "bytes": 0,
                "errors": 0,
                "phase": self._cont_stop_phase,
                "last_error": self._cont_last_error,
            }

    def stop_continuous_acquisition(self):
        """Stop continuous acquisition and return summary dict.

        @return dict or None: summary with 'elapsed_s', 'bytes', 'errors',
                              'filename'; None if nothing was running
        """
        with self._thread_lock:
            if self.module_state() != "locked" or self._cont_start_time is None:
                return None

            # Stop the polling thread
            self._cont_stop_phase = "stopping"
            self._cont_polling_active = False
            # Release lock briefly so the poll thread can finish its cycle
        if self._cont_poll_thread is not None and self._cont_poll_thread.is_alive():
            self._cont_poll_thread.join(timeout=2.0)
            self._cont_poll_thread = None

        with self._thread_lock:
            camera = self._camera()

            self._cont_stop_phase = "draining"
            drain = self._drain_before_stop(camera)

            # Stop hardware
            self._cont_stop_phase = "hardware_stop"
            stop_ok = False
            stop_error = None
            for attempt in range(2):
                try:
                    stop_ok = bool(camera.stop_continuous_acquisition())
                    if stop_ok:
                        break
                except Exception as e:
                    stop_error = str(e)
                    self._cont_errors += 1
                    self._cont_last_error = stop_error
                    self.log.warning(
                        f"Continuous stop attempt {attempt + 1} failed: {stop_error}"
                    )
                    if attempt == 0:
                        retry_drain = self._drain_before_stop(
                            camera, max_cycles=20, stable_cycles=2, sleep_s=0.005
                        )
                        drain["drained_bytes"] += retry_drain["drained_bytes"]
                        drain["drain_errors"] += retry_drain["drain_errors"]
                        _time.sleep(0.05)

            elapsed = _time.time() - self._cont_start_time

            result = {
                "elapsed_s": elapsed,
                "bytes": self._cont_total_bytes,
                "errors": self._cont_errors,
                "filename": self._cont_filename,
                "phase": "stopped" if stop_ok else "stop_failed",
                "stop_ok": stop_ok,
                "stop_error": stop_error,
                "drained_bytes": drain["drained_bytes"],
            }

            # Reset tracking state
            self._cont_start_time = None
            self._cont_last_sdk_total = 0
            self._cont_error_streak = 0
            self._cont_stop_phase = result["phase"]

            self.module_state.unlock()
            self.sigAcquisitionFinished.emit()

            if stop_ok:
                self.log.info(
                    f"Continuous acquisition stopped: "
                    f'{elapsed:.2f} s, {result["bytes"] / 1e6:.2f} MB, '
                    f'{result["errors"]} errors, '
                    f'{result["drained_bytes"] / 1e6:.2f} MB drained on stop'
                )
            else:
                self.log.error(
                    f"Continuous acquisition stop failed after retries: {stop_error}"
                )
            return result

    def _continuous_poll_loop(self):
        """Background thread: drain camera memory to disk at ~1 ms intervals."""
        while self._cont_polling_active:
            with self._thread_lock:
                if self.module_state() != "locked":
                    break
                camera = self._camera()
                try:
                    sdk_total = camera.get_continuous_memory()
                    self._update_continuous_bytes_from_sdk_total(sdk_total)
                    self._cont_last_success_ts = _time.time()
                    self._cont_stop_phase = "running"
                    self._cont_error_streak = 0
                except Exception as e:
                    self._cont_errors += 1
                    self._cont_error_streak += 1
                    self._cont_last_error = str(e)
                    self._cont_stop_phase = "degraded"
                    now = _time.time()
                    if now - self._cont_last_error_log_ts >= 1.0:
                        self.log.warning(
                            f"ContAcq memory read error (streak={self._cont_error_streak}): {e}"
                        )
                        self._cont_last_error_log_ts = now

                    if self._cont_error_streak in (10, 25, 50, 100):
                        self.log.error(
                            "ContAcq persistent communication errors; "
                            "keeping poll loop alive and retrying"
                        )

                # Update cached frame for preview / status
                now = _time.time()
                if now >= self._cont_next_preview_ts:
                    self._cont_next_preview_ts = now + self._cont_preview_interval_s
                    try:
                        frame = camera.get_acquired_data()
                        self._last_frame = frame
                        self.sigFrameChanged.emit(frame)
                    except Exception:
                        pass
            if self._cont_error_streak > 0:
                sleep_s = min(0.01, 0.001 * (2 ** min(self._cont_error_streak, 5)))
            else:
                sleep_s = 0.001
            _time.sleep(sleep_s)

    # ══════════════════════════════════════════════════════════════════
    #  Utilities
    # ══════════════════════════════════════════════════════════════════

    def create_tag(self, time_stamp):
        return f"{time_stamp}_captured_frame"

    def draw_2d_image(self, data, cbar_range=None):
        fig, ax = plt.subplots()
        cfimage = ax.imshow(data, cmap="inferno", origin="lower", interpolation="none")
        if cbar_range is None:
            cbar_range = (np.nanmin(data), np.nanmax(data))
        cbar = plt.colorbar(cfimage, shrink=0.8)
        cbar.ax.tick_params(which="both", length=0)
        return fig
