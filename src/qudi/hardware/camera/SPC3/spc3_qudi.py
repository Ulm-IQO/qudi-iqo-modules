# -*- coding: utf-8 -*-

"""
Qudi hardware module for the MPD SPC3 SPAD camera.

Wraps the vendor-provided SPC3 Python SDK (spc.py) and exposes it through the
qudi CameraInterface.  spc.py must NOT be modified — all adaptation happens here.

---

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level
directory of this distribution and on
<https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE.  See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with
qudi.  If not, see <https://www.gnu.org/licenses/>.
"""

import os
import time
import struct
import numpy as np
from ctypes import (
    c_int,
    c_short,
    c_void_p,
    c_uint8,
    c_uint16,
    POINTER,
    byref,
    cast,
)

from qudi.core.configoption import ConfigOption
from qudi.interface.camera_interface import CameraInterface
from qudi.hardware.camera.SPC3.spc import SPC3


class SPC3_Qudi(CameraInterface):
    """Qudi hardware module for the MPD SPC3 SPAD camera.

    Example config for copy-paste (matches SPUD202603.cfg):

    camera_SPC3:
        module.Class: 'camera.SPC3.spc3_qudi.SPC3_Qudi'
        options:
            camera_mode: 'Advanced'    # 'Normal' or 'Advanced'
            exposure: 1040             # HIT in clock cycles (10 ns each)
            nframes: 1                 # Frames per snap acquisition (1-65534)
            nintegframes: 1            # Temporal binning: integrated frames per output (1-65534)
            ncounters: 1               # Counters per pixel (1-3)
            force8bit: 'Disabled'      # 'Enabled' or 'Disabled' (Advanced mode only)
            half_array: 'Enabled'      # 'Enabled' (32x32) or 'Disabled' (32x64)
            signed_data: 'Disabled'    # 'Enabled' or 'Disabled'
            trigger_mode: 'no_trigger'       # 'no_trigger' | 'single_trigger' | 'multiple_trigger'
            trigger_frames_per_pulse: 1      # Frames per SYNC_IN pulse (1-100, multiple_trigger only)
            gate_mode: 'off'                 # 'off' | 'coarse' (counter 1 only, Advanced mode)
            coarse_gate_start: 0             # Gate ON start in clock cycles (10 ns each)
            coarse_gate_stop: 100            # Gate ON stop in clock cycles (10 ns each)

    Additional optional config keys (not required, sensible defaults apply):
            display_units: 'counts'          # 'counts' or 'cps'
            save_directory: ''               # Pre-populated save directory in GUI

    Unit convention
    ---------------
    - ALL public time values (exposure, integration) are in **seconds**.
    - The config ``exposure`` parameter is in **clock cycles** (10 ns each).
    - Internally ``SetCameraPar`` uses clock cycles for the HIT parameter.
    - ``exposure_seconds = NIntegFrames × HIT_cycles × 10 ns``
    """

    # ── Config options ─────────────────────────────────────────────────
    _camera_mode = ConfigOption("camera_mode", "Advanced")
    _cfg_exposure = ConfigOption("exposure", 1040)  # clock cycles
    _cfg_nframes = ConfigOption("nframes", 1)
    _cfg_nintegframes = ConfigOption("nintegframes", 1)
    _cfg_ncounters = ConfigOption("ncounters", 1)
    _cfg_force8bit = ConfigOption("force8bit", "Disabled")
    _cfg_half_array = ConfigOption("half_array", "Enabled")
    _cfg_signed_data = ConfigOption("signed_data", "Disabled")
    _cfg_display_units = ConfigOption("display_units", "counts")
    _cfg_trigger_mode = ConfigOption("trigger_mode")  # required
    _cfg_trigger_frames_per_pulse = ConfigOption("trigger_frames_per_pulse")  # required
    _cfg_save_directory = ConfigOption("save_directory", "")
    _cfg_gate_mode = ConfigOption("gate_mode")  # required
    _cfg_coarse_gate_start = ConfigOption("coarse_gate_start")  # required
    _cfg_coarse_gate_stop = ConfigOption("coarse_gate_stop")  # required

    # ── Constants ──────────────────────────────────────────────────────
    _HIT_NORMAL = 1040  # Fixed HIT for Normal mode (clock cycles)
    _CLOCK_PERIOD = 10e-9  # 10 ns per clock cycle
    _NROWS = 32  # Pixel array is always 32 rows
    _TRIGGER_WAIT_TIMEOUT_S = 60.0
    _LIVE_THROTTLE_S = 0.01

    # ══════════════════════════════════════════════════════════════════
    #  Module lifecycle
    # ══════════════════════════════════════════════════════════════════

    def on_activate(self):
        """Initialisation performed during activation of the module."""

        # ── Resolve DLL path ───────────────────────────────────────────
        # spc.py locates the DLL via the class attribute ``lib_root_dir``.
        # Override it so the path is absolute and independent of the
        # working directory qudi happens to run from.
        SPC3.lib_root_dir = os.path.join(os.path.dirname(__file__), "lib")

        # ── Parse binary config options ────────────────────────────────
        self._force8bit = self._to_state(self._cfg_force8bit, "force8bit")
        self._half_array = self._to_state(self._cfg_half_array, "half_array")
        self._signed_data = self._to_state(self._cfg_signed_data, "signed_data")

        # ── Derived geometry ───────────────────────────────────────────
        self._ncols = 32 if self._half_array else 64

        # ── Internal state from config ─────────────────────────────────
        self._hit = int(self._cfg_exposure)  # HIT in clock cycles
        self._NFrames = int(self._cfg_nframes)
        self._NIntegFrames = int(self._cfg_nintegframes)
        self._NCounters = int(self._cfg_ncounters)
        self._display_units = str(self._cfg_display_units)
        self._trigger_mode = str(self._cfg_trigger_mode)
        self._trigger_frames_per_pulse = max(
            1, min(int(self._cfg_trigger_frames_per_pulse), 100)
        )
        self._gate_mode = str(self._cfg_gate_mode)
        self._coarse_gate_start = int(self._cfg_coarse_gate_start)
        self._coarse_gate_stop = int(self._cfg_coarse_gate_stop)

        # ── Acquisition state flags ────────────────────────────────────
        self._live = False
        self._acquiring = False
        self._continuous = False

        # ── Cached frames for GUI ─────────────────────────────────────
        self._last_live_frame = None
        self._last_frame = None
        self._last_live_ts = 0.0

        # ── Cached snap stack for browsing (counter, frame, row, col) ─
        self._last_snap_frames = None

        # ── Loaded-file viewer state ───────────────────────────────────
        self._loaded_frames = None
        self._loaded_header = None
        self._loaded_filepath = None

        # ── Construct SPC3 SDK object ──────────────────────────────────
        mode = (
            SPC3.CameraMode.ADVANCED
            if self._camera_mode == "Advanced"
            else SPC3.CameraMode.NORMAL
        )
        try:
            self._spc = SPC3(mode)
        except Exception as e:
            self.log.error(f"Failed to initialise SPC3 camera: {e}")
            return

        if self._camera_mode == "Advanced":
            self._spc.SetAdvancedMode(SPC3.State.ENABLED)

        # Step 1: Send camera parameters and commit so that HIT is
        # established before the SDK validates gate ranges.
        self._spc.SetCameraPar(
            self._effective_hit(),
            self._NFrames,
            self._NIntegFrames,
            self._NCounters,
            self._force8bit,
            self._half_array,
            self._signed_data,
        )
        self._spc.ApplySettings()

        # Step 2: Apply trigger and gate settings, then commit again.
        self._apply_trigger_settings()
        self._apply_gate_settings()
        self._spc.ApplySettings()

        # ── Log summary ───────────────────────────────────────────────
        exp_s = self._get_exposure_seconds()
        hit_us = self._effective_hit() * self._CLOCK_PERIOD * 1e6
        self.log.info(
            f"SPC3 activated ({self._camera_mode} mode): "
            f"HIT={self._effective_hit()} cycles ({hit_us:.2f} µs), "
            f"NInteg={self._NIntegFrames}, exposure={exp_s * 1e3:.2f} ms, "
            f"array={self._NROWS}×{self._ncols}, "
            f"trigger={self._trigger_mode} (fps={self._trigger_frames_per_pulse}), "
            f"gate={self._gate_mode}"
        )

    def on_deactivate(self):
        """Deinitialisation performed during deactivation of the module."""
        if self._live:
            try:
                self._spc.LiveSetModeOFF()
            except Exception:
                pass
            self._live = False

        if self._continuous:
            try:
                self._spc.ContAcqToFileStop()
            except Exception:
                pass
            self._continuous = False

        self._acquiring = False

        try:
            self._spc.Destr()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  CameraInterface — required abstract methods
    # ══════════════════════════════════════════════════════════════════

    def get_name(self):
        """Return an identifier for the camera."""
        try:
            cam_id, serial = self._spc.GetSerial()
            return f"SPC3 {serial}"
        except Exception:
            return "SPC3"

    def get_size(self):
        """Return image size as (rows, cols)."""
        return self._NROWS, self._ncols

    def support_live_acquisition(self):
        """SPC3 supports free-running live mode."""
        return True

    def start_live_acquisition(self):
        """Start free-running live acquisition."""
        if self._acquiring or self._continuous:
            self.log.error("Cannot start live: acquisition already in progress")
            return False
        try:
            self._spc.LiveSetModeON()
            self._live = True
            return True
        except Exception as e:
            self.log.error(f"Failed to start live acquisition: {e}")
            return False

    def start_single_acquisition(self):
        """Perform a snap acquisition.

        Executes: SnapPrepare → (wait for trigger) → SnapAcquire → extract buffer.

        @return bool: True on success, False otherwise.
        """
        if self._live:
            self.log.error("Cannot snap: live mode is active")
            return False
        if self._acquiring:
            self.log.error("Cannot snap: acquisition already in progress")
            return False

        try:
            self._acquiring = True

            # Ensure all settings (gate, trigger) are committed
            self._commit_settings()

            # Prepare camera for snap
            self._spc.SnapPrepare()

            # Wait for external trigger if configured (with timeout).
            if self._trigger_mode in ("single_trigger", "multiple_trigger"):
                self.log.info(
                    f"Waiting for external trigger ({self._trigger_mode}, "
                    f"timeout={self._TRIGGER_WAIT_TIMEOUT_S:.1f}s)..."
                )
                if not self._wait_for_trigger(self._TRIGGER_WAIT_TIMEOUT_S):
                    raise TimeoutError("No external trigger received")
                self.log.info("Trigger received")

            # Acquire (blocks until all frames are downloaded)
            self._spc.SnapAcquire()

            # Extract frames from SDK internal buffer
            try:
                frames = (
                    self._spc.SnapGetImageBuffer().copy()
                )  # detach from SDK-owned buffer
            except AssertionError:
                # Vendor wrapper asserts on cached _data_bits mismatch.
                # Read the buffer using the SDK-reported DataDepth instead.
                frames = self._snap_get_image_buffer_safe()

            # Cache a representative 2-D image for the generic camera GUI.
            # BufferToFrames returns (counters, frames, rows, cols).
            self._last_frame = self._select_display_frame(frames)

            # Cache the full stack so the GUI can browse multi-frame snaps.
            # Keep the same shape as returned by the SDK wrapper:
            # (counters, frames, rows, cols)
            self._last_snap_frames = np.asarray(frames).copy()

            self._acquiring = False
            self.log.info(f"Snap complete: shape={frames.shape}, dtype={frames.dtype}")
            return True

        except Exception as e:
            self._acquiring = False
            self.log.error(f"Snap acquisition failed: {type(e).__name__}: {e}")
            return False

    def get_last_snap_sequence(self, counter_index=0):
        """Return last snap as a 3-D stack for browsing.

        @param int counter_index: which counter to view (default 0)
        @return numpy.ndarray|None: shape (frames, rows, cols) or None if unavailable
        """
        if self._last_snap_frames is None:
            return None

        arr = np.asarray(self._last_snap_frames)
        if arr.ndim == 4:
            c = int(counter_index)
            c = max(0, min(c, arr.shape[0] - 1))
            return arr[c]
        if arr.ndim == 3:
            return arr
        return None

    def _snap_get_image_buffer_safe(self):
        """Read snap buffer without relying on spc.py's cached _data_bits.

        The SDK returns a pointer to its internal image buffer plus an integer
        DataDepth (8 or 16). We size and cast the buffer accordingly, then use
        SPC3.BufferToFrames to get (counters, frames, rows, cols).
        """
        buf = POINTER(c_uint8)()
        data_depth = c_int(0)

        f = self._spc.dll.SPC3_Get_Image_Buffer
        f.argtypes = [c_void_p, POINTER(POINTER(c_uint8)), POINTER(c_int)]
        f.restype = c_int

        ec = f(self._spc.c_handle, byref(buf), byref(data_depth))
        self._spc._checkError(ec)

        # Some SDK builds appear to return DataDepth=0 even on success.
        # Treat that as "unknown" and fall back to the already-committed
        # setting computed by ApplySettings()/Is16Bit().
        depth = int(data_depth.value)
        if depth not in (8, 16):
            depth = int(getattr(self._spc, "_data_bits", 0) or 0)
        if depth not in (8, 16):
            try:
                depth = 16 if self._spc.Is16Bit() else 8
            except Exception:
                depth = 16

        if not bool(buf):
            raise RuntimeError("SDK returned null image buffer pointer")

        # Prefer SDK-maintained values, fall back to our config.
        num_frames = int(
            getattr(self._spc, "_snap_num_frames", self._NFrames) or self._NFrames
        )
        num_counters = int(
            getattr(self._spc, "_num_counters", self._NCounters) or self._NCounters
        )
        num_pixels = int(
            getattr(self._spc, "_num_pixels", 0) or (1024 if self._half_array else 2048)
        )

        size_bytes = num_frames * (depth // 8) * num_pixels * num_counters
        if depth == 16:
            buf16 = cast(buf, POINTER(c_uint16))
            count = size_bytes // 2
            data = np.ctypeslib.as_array(buf16, shape=(count,))
        else:
            count = size_bytes
            data = np.ctypeslib.as_array(buf, shape=(count,))

        # Keep vendor object internally consistent for any later calls.
        try:
            self._spc._data_bits = depth
        except Exception:
            pass

        frames = SPC3.BufferToFrames(data, num_pixels, num_counters)
        return np.array(frames, copy=True)

    def stop_acquisition(self):
        """Stop live or single acquisition."""
        if self._live:
            try:
                self._spc.LiveSetModeOFF()
            except Exception as e:
                self.log.error(f"Failed to stop live acquisition: {e}")
        self._live = False
        self._acquiring = False
        return True

    def get_acquired_data(self):
        """Return the current live frame (counter 1) for GUI display.

        During continuous acquisition the last cached live frame is returned as
        a static preview.
        """
        frame = None

        if self._live:
            # Guard against extremely fast polling intervals (CameraLogic uses a QTimer in ms).
            # If called too frequently, return the cached frame instead of hammering the SDK.
            now = time.monotonic()
            if (
                self._last_live_frame is None
                or (now - self._last_live_ts) >= self._LIVE_THROTTLE_S
            ):
                try:
                    # Vendor SDK LiveGetImg returns a single-counter stack with shape
                    # (frames, rows, cols). With default live acquisition it's 1 frame.
                    live_stack = self._spc.LiveGetImg()
                    if live_stack.ndim == 3:
                        frame = live_stack[0]
                    else:
                        frame = live_stack
                    self._last_live_frame = np.array(frame, copy=True)
                    self._last_live_ts = now
                except Exception as e:
                    self.log.error(f"LiveGetImg failed: {e}")
            if frame is None and self._last_live_frame is not None:
                frame = self._last_live_frame.copy()

        if frame is None:
            # When not live, show last snap frame if available.
            if self._last_frame is not None:
                frame = self._last_frame.copy()
            else:
                frame = np.zeros((self._NROWS, self._ncols), dtype=np.uint16)

        # Scale to counts per second if requested
        if self._display_units == "cps":
            exp_s = self._get_exposure_seconds()
            if exp_s > 0:
                frame = frame.astype(np.float64) / exp_s

        # Ensure 2-D for GUI display
        if frame.ndim == 1:
            frame = frame.reshape(self._NROWS, self._ncols)

        return frame

    @staticmethod
    def _select_display_frame(frames):
        """Pick a single 2-D frame from an SPC3 snap buffer.

        Expected input shape is (counters, frames, rows, cols).
        """
        arr = np.asarray(frames)
        if arr.ndim == 4:
            return arr[0, -1]
        if arr.ndim == 3:
            return arr[-1]
        if arr.ndim == 2:
            return arr
        raise ValueError(f"Unexpected frame array ndim={arr.ndim}")

    def _is_triggered(self):
        """Correct trigger polling (work around SDK wrapper bug in spc.py)."""
        try:
            f = self._spc.dll.SPC3_IsTriggered
            f.argtypes = [c_void_p, POINTER(c_short)]
            f.restype = c_int
            is_triggered = c_short(0)
            ec = f(self._spc.c_handle, byref(is_triggered))
            self._spc._checkError(ec)
            return bool(is_triggered.value)
        except Exception as e:
            self.log.error(f"Trigger poll failed: {e}")
            return False

    def _wait_for_trigger(self, timeout_s=None):
        if timeout_s is None:
            timeout_s = self._TRIGGER_WAIT_TIMEOUT_S
        start = time.monotonic()
        while not self._is_triggered():
            if (time.monotonic() - start) > timeout_s:
                return False
            time.sleep(0.01)
        return True

    def set_exposure(self, exposure):
        """Set exposure time in seconds.

        Adjusts NIntegFrames to achieve the requested exposure while keeping
        the hardware integration time (HIT) unchanged.

            exposure = NIntegFrames × HIT_cycles × 10 ns

        @param float exposure: desired exposure in seconds
        @return float: actual achieved exposure in seconds
        """
        hit_s = self._effective_hit() * self._CLOCK_PERIOD
        n = max(1, min(int(round(exposure / hit_s)), 65534))
        self._NIntegFrames = n
        self._apply_camera_settings()
        return self._get_exposure_seconds()

    def get_exposure(self):
        """Return exposure time in seconds."""
        return self._get_exposure_seconds()

    def set_gain(self, gain):
        """Not applicable for SPAD camera."""
        return 0

    def get_gain(self):
        """Not applicable for SPAD camera."""
        return 0

    def get_ready_state(self):
        """Return True when camera is idle and ready for a new acquisition."""
        return not (self._live or self._acquiring or self._continuous)

    # ══════════════════════════════════════════════════════════════════
    #  SPC3‑specific public methods (called by camera_logic_SPC3)
    # ══════════════════════════════════════════════════════════════════

    # ── Display units ──────────────────────────────────────────────────

    def get_display_units(self):
        """Return 'counts' or 'cps'."""
        return self._display_units

    def set_display_units(self, units):
        """Set display scaling mode.

        @param str units: 'counts' or 'cps'
        @return bool: Success
        """
        if units not in ("counts", "cps"):
            self.log.error(f"Invalid display units '{units}'. Use 'counts' or 'cps'")
            return False
        self._display_units = units
        self.log.info(f"Display units set to: {units}")
        return True

    # ── Hardware integration time ──────────────────────────────────────

    def set_hardware_integration(self, seconds):
        """Set HIT in seconds (Advanced mode only).

        Internally converts to clock cycles for SetCameraPar.

        @param float seconds: HIT in seconds
        @return bool: Success
        """
        if self._camera_mode != "Advanced":
            self.log.warning("HIT is fixed in Normal mode (10.4 µs)")
            return False

        cycles = max(1, min(int(round(seconds / self._CLOCK_PERIOD)), 65534))
        self._hit = cycles
        self._apply_camera_settings()
        self.log.info(
            f"HIT set to {cycles} cycles ({cycles * self._CLOCK_PERIOD * 1e6:.2f} µs)"
        )
        return True

    # ── Temporal binning ───────────────────────────────────────────────

    def set_binning(self, binning):
        """Set temporal binning (NIntegFrames).

        @param int binning: number of frames to integrate (1-65534)
        @return bool: Success
        """
        self._NIntegFrames = max(1, min(int(binning), 65534))
        self._apply_camera_settings()
        return True

    def get_binning(self):
        """Return current NIntegFrames value."""
        return self._NIntegFrames

    # ── Save directory ─────────────────────────────────────────────────

    def get_default_save_directory(self):
        """Return the default save directory from config, or empty string."""
        return self._cfg_save_directory

    # ── Trigger ────────────────────────────────────────────────────────

    def get_trigger_mode(self):
        """Return 'no_trigger', 'single_trigger', or 'multiple_trigger'."""
        return self._trigger_mode

    def get_trigger_frames_per_pulse(self):
        """Return frames per SYNC_IN pulse (1-100)."""
        return self._trigger_frames_per_pulse

    def set_trigger_mode(self, mode, frames_per_pulse=1):
        """Set trigger mode and apply immediately.

        @param str mode: 'no_trigger' | 'single_trigger' | 'multiple_trigger'
        @param int frames_per_pulse: 1-100 (multiple_trigger only)
        """
        valid = ("no_trigger", "single_trigger", "multiple_trigger")
        if mode not in valid:
            self.log.error(f"Invalid trigger mode '{mode}'. Must be one of {valid}")
            return
        self._trigger_mode = mode
        self._trigger_frames_per_pulse = max(1, min(int(frames_per_pulse), 100))
        self._apply_trigger_settings()
        self._commit_settings()

    # ── Continuous acquisition ─────────────────────────────────────────

    def continuous_acquisition(self, filename):
        """Start streaming acquisition data to file.

        Settings must already be committed (on_activate or via set_* methods).
        Do NOT call ApplySettings() here — calling it immediately before
        ContAcqToFileStart can reset the camera into an idle state that
        prevents data generation.

        @param str filename: path stem (SDK appends .spc3)
        @return bool: Success
        """
        if self._continuous:
            self.log.error("Continuous acquisition already active")
            return False
        if self._live or self._acquiring:
            self.log.error("Cannot start continuous: another acquisition is active")
            return False

        try:
            filename = os.path.normpath(str(filename))
            if filename.lower().endswith(".spc3"):
                filename = filename[:-5]

            directory = os.path.dirname(filename)
            if directory:
                os.makedirs(directory, exist_ok=True)

            self._spc.ContAcqToFileStart(filename)
            self._continuous = True
            self._cont_filename = filename
            self.log.info(f"ContAcqToFileStart -> {filename}")
            return True
        except Exception as e:
            self._continuous = False
            self.log.error(f"Failed to start continuous acquisition: {e}")
            return False

    def stop_continuous_acquisition(self):
        """Stop streaming and close the output file."""
        if self._continuous:
            try:
                self._spc.ContAcqToFileStop()
            except Exception as e:
                self.log.error(f"Failed to stop continuous acquisition: {e}")
            self._continuous = False
        return True

    def get_continuous_memory(self):
        """Dump camera memory to disk during continuous acquisition.

        @return int: bytes read in this call
        """
        if self._continuous:
            return self._spc.ContAcqToFileGetMemory()
        return 0

    # ── File I/O ───────────────────────────────────────────────────────

    def save_frames_to_file(self, frames, filepath):
        """Save snap frames to .spc3 via SDK SaveImgDisk.

        @param numpy.ndarray frames: shape (counters, frames, rows, cols)
        @param str filepath: output path (.spc3 extension optional)
        @return bool: Success
        """
        try:
            filepath = os.path.normpath(filepath)
            if filepath.endswith(".spc3"):
                filepath = filepath[:-5]
            directory = os.path.dirname(filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)

            n_frames = frames.shape[1]
            self._spc.SaveImgDisk(
                1, n_frames, filepath, SPC3.OutFileFormat.SPC3_FILEFORMAT
            )

            expected = filepath + ".spc3"
            if os.path.exists(expected):
                return True

            self.log.error(f"SaveImgDisk completed but file not found: {expected}")
            return False

        except Exception as e:
            self.log.error(f"Failed to save frames: {e}")
            return False

    def save_last_snap_to_file(self, filepath, n_frames=None):
        """Save the most recent snap acquisition buffer to a .spc3 file.

        This is intended for *manual* saving right after a snap has completed.
        The SPC3 SDK save routine operates on the device/SDK internal snap buffer
        populated by the last SnapAcquire().

        @param str filepath: output path (.spc3 extension optional)
        @param int|None n_frames: number of frames to write (defaults to configured NFrames)
        @return bool: Success
        """
        try:
            filepath = os.path.normpath(str(filepath))
            if filepath.lower().endswith(".spc3"):
                filepath = filepath[:-5]

            directory = os.path.dirname(filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)

            if n_frames is None:
                n_frames = int(
                    getattr(self._spc, "_snap_num_frames", self._NFrames)
                    or self._NFrames
                )
            n_frames = max(1, min(int(n_frames), 65534))

            self._spc.SaveImgDisk(
                1, n_frames, filepath, SPC3.OutFileFormat.SPC3_FILEFORMAT
            )

            expected = filepath + ".spc3"
            if os.path.exists(expected):
                return True

            self.log.error(f"SaveImgDisk completed but file not found: {expected}")
            return False

        except Exception as e:
            self.log.error(f"Failed to save last snap: {e}")
            return False

    def load_acquisition_file(self, filepath):
        """Load a .spc3 file for frame-by-frame viewing.

        @param str filepath: path to file
        @return bool: Success
        """
        try:
            filepath = os.path.normpath(filepath)

            self._loaded_frames, self._loaded_header = SPC3.ReadSPC3DataFile(filepath)

            self._loaded_filepath = filepath

            n_c, n_f, n_r, n_col = self._loaded_frames.shape
            self.log.info(f"Loaded {n_f} frames ({n_r}×{n_col}) from {filepath}")
            return True

        except Exception as e:
            self.log.error(f"Failed to load {filepath}: {e}")
            return False

    # ── Gate control ───────────────────────────────────────────────────

    def get_gate_mode(self):
        """Return 'off' or 'coarse'."""
        return self._gate_mode

    def get_coarse_gate_values(self):
        """Return (start, stop) in clock cycles."""
        return self._coarse_gate_start, self._coarse_gate_stop

    def set_coarse_gate(self, start_cycles, stop_cycles):
        """Set coarse gate window and apply immediately.

        @param int start_cycles: gate ON start (clock cycles)
        @param int stop_cycles: gate ON stop (clock cycles)
        """
        self._gate_mode = "coarse"
        self._coarse_gate_start = int(start_cycles)
        self._coarse_gate_stop = int(stop_cycles)
        self._apply_gate_settings()
        self._spc.ApplySettings()

    def disable_gate(self):
        """Set counter 1 back to continuous (ungated) mode."""
        self._gate_mode = "off"
        self._apply_gate_settings()
        self._spc.ApplySettings()

    # ══════════════════════════════════════════════════════════════════
    #  Private helpers
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _to_state(value, name=""):
        """Convert config string/bool/int to SPC3 State enum (0 or 1)."""
        if value in (1, True, "Enabled", "enabled", "ENABLED"):
            return SPC3.State.ENABLED
        if value in (0, False, "Disabled", "disabled", "DISABLED"):
            return SPC3.State.DISABLED
        raise ValueError(f"{name}: expected Enabled/Disabled, got {value!r}")

    def _effective_hit(self):
        """Return active HIT in clock cycles."""
        if self._camera_mode == "Advanced":
            return self._hit
        return self._HIT_NORMAL

    def _get_exposure_seconds(self):
        """Calculate exposure: NIntegFrames × HIT × 10 ns."""
        return self._NIntegFrames * self._effective_hit() * self._CLOCK_PERIOD

    def _apply_camera_settings(self):
        """Send current camera parameters to hardware and commit."""
        self._spc.SetCameraPar(
            self._effective_hit(),
            self._NFrames,
            self._NIntegFrames,
            self._NCounters,
            self._force8bit,
            self._half_array,
            self._signed_data,
        )
        self._commit_settings()

    def _apply_trigger_settings(self):
        """Configure the SYNC_IN trigger on the camera."""
        if self._trigger_mode == "no_trigger":
            self._spc.SetSyncInState(SPC3.State.DISABLED, 0)
        elif self._trigger_mode == "single_trigger":
            self._spc.SetSyncInState(SPC3.State.ENABLED, 0)
        elif self._trigger_mode == "multiple_trigger":
            self._spc.SetSyncInState(SPC3.State.ENABLED, self._trigger_frames_per_pulse)
        else:
            self.log.warning(f"Unknown trigger_mode '{self._trigger_mode}', disabling")
            self._spc.SetSyncInState(SPC3.State.DISABLED, 0)

    def _apply_gate_settings(self):
        """Configure coarse gating on counter 1."""
        if self._gate_mode != "off" and self._camera_mode != "Advanced":
            self.log.warning(
                "Coarse gating requires Advanced mode — gate will NOT be applied"
            )
            self._spc.SetGateMode(1, SPC3.GateMode.CONTINUOUS)
            return

        if self._gate_mode == "coarse":
            hit = self._effective_hit()
            start = max(0, min(self._coarse_gate_start, hit - 6))
            stop = max(start + 1, min(self._coarse_gate_stop, hit - 5))
            if start != self._coarse_gate_start or stop != self._coarse_gate_stop:
                self.log.warning(
                    f"Gate values clamped: start={start}, stop={stop} (HIT={hit})"
                )
            self._coarse_gate_start = start
            self._coarse_gate_stop = stop
            self._spc.SetGateMode(1, SPC3.GateMode.COARSE)
            self._spc.SetCoarseGateValues(1, start, stop)
            self.log.info(
                f"Gate: coarse counter 1 — "
                f"start={start * 10} ns, stop={stop * 10} ns "
                f"(cycles {start}–{stop} of {hit})"
            )
        else:
            self._spc.SetGateMode(1, SPC3.GateMode.CONTINUOUS)

    def _commit_settings(self):
        """Re-apply gate settings then commit everything to hardware.

        SetCameraPar resets the SDK pending-settings queue, so the gate
        configuration must be re-issued before every ApplySettings() call.
        """
        self._apply_gate_settings()
        self._spc.ApplySettings()
