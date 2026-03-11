# -*- coding: utf-8 -*-

# FIXME: This module is obviously taken from someone else and altered without attribution.
"""
This hardware module implement the camera spectrometer interface to use an Andor Camera.
It use a dll to interface with instruments via USB (only available physical interface)
This module does aim at replacing Solis.

---

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

from enum import Enum
from ctypes import *
from ctypes import c_uint32, c_uint16
import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.interface.camera_interface import CameraInterface

from qudi.hardware.camera.SPC3.spc_old import SPC3, SPC3_H, SPC3Return


class SPC3_Qudi(CameraInterface):
    """Hardware class for SPC3 SPAD Camera

    Example config for copy-paste:

    camera_SPC3:
        module.Class: 'camera.SPC3.spc3_qudi.SPC3_Qudi'
        options:
            dll_location: 'C:\\Users\\...\\qudi-iqo-modules\\src\\qudi\\hardware\\camera\\SPC3\\lib\\Win\\'
            camera_mode: 'Normal'  # or 'Advanced'
            default_hardware_integration: 5200  # in units of 10ns clock cycles (52 us)
            default_NFrames: 100  # frames per Snap acquisition
            default_NIntegFrames: 1000  # temporal binning (integrated frames per output)
            default_NCounters: 1  # number of counters per pixel (1-3)
            default_Force8bit: 'Disabled'  # or 'Enabled' (Advanced mode only)
            default_Half_array: 'Disabled'  # or 'Enabled' (32x32 instead of 32x64)
            default_Signed_data: 'Disabled'  # or 'Enabled'
            default_display_units: 'counts'  # or 'cps'
            trigger_mode: 'no_trigger'  # 'no_trigger' | 'single_trigger' | 'multiple_trigger'
            trigger_frames_per_pulse: 1  # frames per SYNC_IN pulse (only for 'multiple_trigger', 1-100)

    Unit Convention:
        - ALL public parameters use SECONDS for time values (exposure, integration)
        - Binning is integer frame count (no units)
        - CRITICAL: SetCameraPar hardware integration parameter uses CLOCK CYCLES (each cycle = 10ns)

    Note on exposure time:
        - Normal mode: Hardware integration fixed at 10.4 µs (1040 clock cycles)
        - Advanced mode: Hardware integration configurable (1-65534 clock cycles)
        - Actual exposure = NIntegFrames × HardwareIntegration × 10ns

    """

    _camera_mode = ConfigOption("camera_mode", missing="error")
    _dll_location = ConfigOption("dll_location", missing="error")
    _default_hardware_integration = ConfigOption("default_hardware_integration", 5200)
    _default_NFrames = ConfigOption("default_NFrames", 1)
    _default_NIntegFrames = ConfigOption("default_NIntegFrames", 1000)
    _default_NCounters = ConfigOption("default_NCounters", 1)
    _default_Force8bit = ConfigOption("default_Force8bit", 0)
    _default_display_units = ConfigOption(
        "default_display_units", "counts"
    )  # 'counts' or 'cps'
    _default_Half_array = ConfigOption("default_Half_array", 0)
    _default_Signed_data = ConfigOption("default_Signed_data", 0)
    # Trigger mode: 'no_trigger' | 'single_trigger' | 'multiple_trigger'
    _trigger_mode = ConfigOption("trigger_mode", "no_trigger")
    # Number of frames acquired per trigger pulse (only for 'multiple_trigger', valid range 1-100)
    _trigger_frames_per_pulse = ConfigOption("trigger_frames_per_pulse", 1)
    # Default directory for saving acquisition files (empty string = no default)
    _default_save_directory = ConfigOption("default_save_directory", "")
    # Coarse gate mode: 'off' | 'coarse'  (counter 1 only)
    _gate_mode = ConfigOption("gate_mode", "off")
    # Coarse gate start position in clock cycles (10 ns each). Range: 0 .. (HIT - 6)
    _coarse_gate_start = ConfigOption("coarse_gate_start", 0)
    # Coarse gate stop position in clock cycles (10 ns each). Range: (start+1) .. (HIT - 5)
    _coarse_gate_stop = ConfigOption("coarse_gate_stop", 100)

    _HardwareIntegration = _default_hardware_integration
    _NFrames = _default_NFrames
    _NIntegFrames = _default_NIntegFrames
    _NCounters = _default_NCounters
    _Force8bit = _default_Force8bit
    _display_units = _default_display_units  # 'counts' or 'cps'
    _Half_array = _default_Half_array
    _Signed_data = _default_Signed_data

    _HardwareIntegration_Normal = (
        1040  # fixed to 10.4 us in Normal mode (in units of 10ns clock cycles)
    )
    _Nrows = 32
    _Ncols = 64
    _exposure = 0.02  # in seconds (for GUI display, calculated from NIntegFrames * HardwareIntegration * 10ns)

    # Valid parameter ranges from spc.py documentation
    _MIN_HARDWARE_INTEGRATION = 1  # clock cycles
    _MAX_HARDWARE_INTEGRATION = 65534  # clock cycles
    _MIN_FRAMES = 1
    _MAX_FRAMES = 65534
    _MIN_INTEG_FRAMES = 1
    _MAX_INTEG_FRAMES = 65534

    _live = False
    _acquiring = False
    _continuous = False
    _current_cont_filename = None  # stores stem passed to ContAcqToFileStart
    _camera_name = "SPC3"
    _background_subtraction_enabled = (
        False  # Track software background subtraction state
    )
    _last_display_frame = (
        None  # cached raw frame from last live tick; used as ContAcq preview
    )

    def _to_binary(self, value, name):
        """Normalize binary config options to 0/1.

        Accepts 0/1, True/False, and 'Enabled'/'Disabled' variants.
        """
        if value in (1, True, "Enabled", "enabled", "ENABLED"):
            return 1
        if value in (0, False, "Disabled", "disabled", "DISABLED"):
            return 0
        raise ValueError(f"{name} config option must be 0/1 or Disabled/Enabled")

    def on_activate(self):
        """Initialisation performed during activation of the module."""

        # Normalize binary options using configured values
        force8bit = self._to_binary(self._Force8bit, "Force8bit")
        half_array = self._to_binary(self._Half_array, "Half_array")
        signed_data = self._to_binary(self._Signed_data, "Signed_data")

        # Advanced Camera Mode
        if self._camera_mode == "Advanced":
            self.spc3 = SPC3(1, "", self._dll_location)
            self.spc3.SetAdvancedMode(SPC3.State.ENABLED)
            self.spc3.SetCameraPar(
                self._HardwareIntegration,
                self._NFrames,
                self._NIntegFrames,
                self._NCounters,
                force8bit,
                half_array,
                signed_data,
            )
            hardware_time = self._HardwareIntegration * 10e-9
            self.log.info(
                f"SPC3 initialized in Advanced mode: HW integration = {hardware_time*1e6:.2f} µs"
            )

        ### GATING OPTIONS FOR ADVANCED MODE ###
        # SetGateMode(self, counter, Mode)
        # SetGateValues(self, Shift, Length)
        # SetDualGate(self, DualGate_State, StartShift, FirstGateWidth, SecondGateWidth, Gap)
        # SetTripleGate(self,TripleGate_State,StartShift,FirstGateWidth,SecondGateWidth,ThirdGateWidth,Gap1,Gap2)
        # SetCoarseGateValues(self, Counter, Start, Stop)

        # Normal Camera Mode
        else:
            self.spc3 = SPC3(0, "", self._dll_location)
            self.spc3.SetCameraPar(
                self._HardwareIntegration_Normal,
                self._NFrames,
                self._NIntegFrames,
                self._NCounters,
                force8bit,
                half_array,
                signed_data,
            )
            hardware_time = self._HardwareIntegration_Normal * 10e-9
            self.log.info(
                f"SPC3 initialized in Normal mode: HW integration = {hardware_time*1e6:.2f} µs (fixed)"
            )

        # Commit camera parameters to hardware first so that the HIT is
        # established before gate values are validated by the SDK.
        self.spc3.ApplySettings()

        # Apply trigger settings
        self._apply_trigger_settings()

        # Apply gate settings (must come after the first ApplySettings so the
        # SDK knows the HIT when validating SetCoarseGateValues ranges)
        self._apply_gate_settings()

        # Commit trigger + gate settings
        self.spc3.ApplySettings()
        self._Ncols = self._Ncols >> half_array

        # Calculate and log initial exposure time
        self._exposure = self._NIntegFrames * hardware_time
        self.log.info(
            f"Initial exposure: {self._exposure*1e3:.2f} ms ({self._NIntegFrames} frames × {hardware_time*1e6:.2f} µs)"
        )

    def _apply_trigger_settings(self):
        """Apply trigger mode settings to hardware.

        Trigger modes:
            'no_trigger'       - start acquisition immediately when commanded
            'single_trigger'   - wait for one SYNC_IN pulse, then run to completion
            'multiple_trigger' - acquire trigger_frames_per_pulse frames per SYNC_IN pulse
        """
        if self._trigger_mode == "no_trigger":
            self.spc3.SetSyncInState(SPC3.State.DISABLED, 0)
            self.log.info("Trigger: disabled (free-running)")

        elif self._trigger_mode == "single_trigger":
            # frames=0 → wait for one pulse then run to completion
            self.spc3.SetSyncInState(SPC3.State.ENABLED, 0)
            self.log.info("Trigger: single external trigger on SYNC_IN")

        elif self._trigger_mode == "multiple_trigger":
            frames = max(1, min(int(self._trigger_frames_per_pulse), 100))
            self.spc3.SetSyncInState(SPC3.State.ENABLED, frames)
            self.log.info(
                f"Trigger: multiple external trigger on SYNC_IN, {frames} frames per pulse"
            )

        else:
            self.log.warning(
                f"Unknown trigger_mode '{self._trigger_mode}', defaulting to no_trigger"
            )
            self.spc3.SetSyncInState(SPC3.State.DISABLED, 0)

    def get_trigger_mode(self):
        """Get the current trigger mode.

        @return str: 'no_trigger', 'single_trigger', or 'multiple_trigger'
        """
        return self._trigger_mode

    def get_trigger_frames_per_pulse(self):
        """Get the number of frames acquired per trigger pulse (multiple_trigger mode only).

        @return int: Frames per trigger pulse (1-100)
        """
        return int(self._trigger_frames_per_pulse)

    def set_trigger_mode(self, mode, frames_per_pulse=1):
        """Set the trigger mode and apply it to hardware immediately.

        @param str mode: 'no_trigger', 'single_trigger', or 'multiple_trigger'
        @param int frames_per_pulse: Frames per SYNC_IN pulse (1-100, only for 'multiple_trigger')
        """
        valid_modes = ("no_trigger", "single_trigger", "multiple_trigger")
        if mode not in valid_modes:
            self.log.error(
                f"Invalid trigger mode '{mode}'. Must be one of {valid_modes}"
            )
            return
        self._trigger_mode = mode
        self._trigger_frames_per_pulse = max(1, min(int(frames_per_pulse), 100))
        self._apply_trigger_settings()
        # Commit trigger change to hardware (gate is re-issued by _apply_settings)
        self._apply_settings()

    def _apply_gate_settings(self):
        """Apply coarse gate settings to hardware (counter 1 only).

        Gate modes:
            'off'    - counter 1 runs in continuous (ungated) mode
            'coarse' - counter 1 counts only during the [start, stop] window within each
                       hardware integration period.  Start and stop are in clock cycles
                       (10 ns each).  Valid ranges per SDK:
                           start: 0 .. (HIT - 6)
                           stop : (start + 1) .. (HIT - 5)
                       where HIT = hardware integration time in clock cycles.
        """
        if self._gate_mode != "off" and self._camera_mode != "Advanced":
            self.log.warning(
                f"Gate mode '{self._gate_mode}' requested but camera is in Normal mode. "
                "Coarse gating requires Advanced mode — gate will NOT be applied. "
                "Set camera_mode: 'Advanced' in the config to enable gating."
            )
            self.spc3.SetGateMode(1, SPC3.GateMode.CONTINUOUS)
            return

        if self._gate_mode == "off":
            self.spc3.SetGateMode(1, SPC3.GateMode.CONTINUOUS)
            self.log.info("Gate: disabled (continuous mode) for counter 1")

        elif self._gate_mode == "coarse":
            hit = (
                self._HardwareIntegration
                if self._camera_mode == "Advanced"
                else self._HardwareIntegration_Normal
            )
            start = int(self._coarse_gate_start)
            stop = int(self._coarse_gate_stop)

            # Clamp to valid SDK ranges
            start = max(0, min(start, hit - 6))
            stop = max(start + 1, min(stop, hit - 5))

            if start != int(self._coarse_gate_start) or stop != int(
                self._coarse_gate_stop
            ):
                self.log.warning(
                    f"Coarse gate values clamped to valid range: "
                    f"start={start}, stop={stop} (HIT={hit} cycles)"
                )

            self._coarse_gate_start = start
            self._coarse_gate_stop = stop

            self.spc3.SetGateMode(1, SPC3.GateMode.COARSE)
            self.spc3.SetCoarseGateValues(1, start, stop)
            self.log.info(
                f"Gate: coarse mode for counter 1 — "
                f"start={start*10} ns, stop={stop*10} ns "
                f"(cycles {start}–{stop} of {hit})"
            )

        else:
            self.log.warning(
                f"Unknown gate_mode '{self._gate_mode}', defaulting to off"
            )
            self.spc3.SetGateMode(1, SPC3.GateMode.CONTINUOUS)

    def _apply_settings(self):
        """Re-queue gate settings then commit all pending settings to hardware.

        This wrapper ensures gate configuration is always re-issued before
        ApplySettings(), preventing SetCameraPar() calls from wiping the gate.
        Use this instead of calling spc3.ApplySettings() directly in any
        method that also calls SetCameraPar().
        """
        self._apply_gate_settings()
        self.spc3.ApplySettings()

    def get_gate_mode(self):
        """Return the current gate mode string ('off' or 'coarse')."""
        return self._gate_mode

    def get_coarse_gate_values(self):
        """Return (start, stop) coarse gate positions in clock cycles.

        @return tuple(int, int): (start_cycles, stop_cycles)
        """
        return int(self._coarse_gate_start), int(self._coarse_gate_stop)

    def set_coarse_gate(self, start_cycles, stop_cycles):
        """Set coarse gate start/stop positions and apply to hardware immediately.

        @param int start_cycles: Gate-on start position in clock cycles (10 ns each)
        @param int stop_cycles:  Gate-on stop  position in clock cycles (10 ns each)
        """
        self._gate_mode = "coarse"
        self._coarse_gate_start = int(start_cycles)
        self._coarse_gate_stop = int(stop_cycles)
        self._apply_gate_settings()
        self.spc3.ApplySettings()

    def disable_gate(self):
        """Disable gating (set counter 1 back to continuous mode)."""
        self._gate_mode = "off"
        self._apply_gate_settings()
        self.spc3.ApplySettings()

    def _apply_camera_settings(self):
        """Apply current camera parameters to hardware"""
        force8bit = self._to_binary(self._Force8bit, "Force8bit")
        half_array = self._to_binary(self._Half_array, "Half_array")
        signed_data = self._to_binary(self._Signed_data, "Signed_data")

        if self._camera_mode == "Advanced":
            self.spc3.SetCameraPar(
                self._HardwareIntegration,
                self._NFrames,
                self._NIntegFrames,
                self._NCounters,
                force8bit,
                half_array,
                signed_data,
            )
        else:
            self.spc3.SetCameraPar(
                self._HardwareIntegration_Normal,
                self._NFrames,
                self._NIntegFrames,
                self._NCounters,
                force8bit,
                half_array,
                signed_data,
            )
        self._apply_settings()

    def on_deactivate(self):
        """Deinitialisation performed during deactivation of the module."""
        # self._spc3.ContAcqToMemoryStop()
        if self._live:
            self.spc3.LiveSetModeOFF()
            self._live = False
        if self._acquiring:
            self._acquiring = False
        if self._continuous:
            self._continuous = False
            self.spc3.ContAcqToFileStop()
            if self._current_cont_filename is not None:
                self._patch_gate_header(self._current_cont_filename + ".spc3")
                self._current_cont_filename = None
        self.spc3.Destr()

    def get_name(self):
        """Retrieve an identifier of the camera that the GUI can print

        @return string: name for the camera
        """
        return self.spc3.GetSerial()

    def get_size(self):
        """Retrieve size of the image in pixel

        @return tuple: Size (width, height)
        """
        # Return (height, width) to match numpy array shape convention
        # _Ncols is adjusted based on Half_array setting during activation
        return self._Nrows, self._Ncols

    def support_live_acquisition(self):
        """Return whether or not the camera can take care of live acquisition

        @return bool: True if supported, False if not
        """
        return True

    def start_live_acquisition(self):
        """Start a continuous acquisition

        @return bool: Success ?
        """
        self._live = True
        self._acquiring = False
        self.spc3.LiveSetModeON()

        return True

    def start_single_acquisition(self):
        """Perform snap acquisition using proper SDK sequence

        Executes: SnapPrepare → SnapAcquire → Extract frames using SnapGetImgPosition
        Returns frames array built from SDK's internal buffer (same source as SaveImgDisk).

        @return numpy array: Acquired frames, or None if failed
        """
        if self._live:
            self.log.error("Cannot snap: live mode is active")
            return None

        try:
            self._acquiring = True
            # Step 1: Re-apply gate + trigger settings and commit to hardware.
            # SetCameraPar (called from set_exposure / set_binning) resets the SDK's
            # pending-settings queue, which would silently drop any gate or trigger
            # configuration set earlier.  Calling _apply_settings() here ensures the
            # hardware state is always consistent with the module's internal state
            # immediately before every snap acquisition.
            self._apply_settings()

            # Step 2: Prepare camera for snap
            self.spc3.SnapPrepare()

            # Step 2: Wait for trigger (if trigger mode active) then acquire
            # SnapAcquire() blocks until all frames are downloaded. In trigger mode
            # the camera waits for a SYNC_IN pulse before capturing, so calling
            # SnapAcquire() immediately causes the SDK to time out with COMMUNICATION_ERROR.
            # Instead, poll IsTriggered() until the camera has received its trigger pulse
            # and started acquiring, then call SnapAcquire() to download the frames.
            if self._trigger_mode in ("single_trigger", "multiple_trigger"):
                import time

                self.log.info(
                    f"Waiting for external trigger on SYNC_IN "
                    f"(trigger_mode='{self._trigger_mode}')..."
                )
                while not self.spc3.IsTriggered():
                    time.sleep(0.01)  # poll every 10 ms
                self.log.info("Trigger received, downloading frames...")

            # Step 3: Trigger acquisition (blocks until frames downloaded)
            self.spc3.SnapAcquire()

            # Step 3: Extract frames from SDK internal buffer by calling SDK directly
            # This uses the SAME internal buffer that SaveImgDisk uses, ensuring consistent data
            # We bypass spc.py's buggy SnapGetImgPosition wrapper and call the SDK directly
            num_frames = self._NFrames
            num_counters = self._NCounters

            # Determine correct dtype based on bit depth
            data_bits = self.spc3._data_bits
            if data_bits == 16:
                dtype = np.uint16
            else:
                dtype = np.uint8

            # Setup SDK function call
            f = self.spc3.dll.SPC3_Get_Img_Position
            f.argtypes = [
                SPC3_H,
                np.ctypeslib.ndpointer(dtype=dtype, ndim=1, flags="C_CONTIGUOUS"),
                c_uint32,
                c_uint16,
            ]
            f.restype = SPC3Return

            # Extract frames
            frames_list = []
            for counter_idx in range(1, num_counters + 1):  # SDK uses 1-based indexing
                counter_frames = []
                for frame_idx in range(1, num_frames + 1):  # SDK uses 1-based indexing
                    # Allocate buffer for single frame
                    data = np.zeros(
                        self.spc3.row_size * self.spc3._num_rows, dtype=dtype
                    )

                    # Call SDK to get frame
                    ec = f(self.spc3.c_handle, data, frame_idx, counter_idx)
                    self.spc3._checkError(ec)

                    # Transform using BufferToFrames
                    frame = self.spc3.BufferToFrames(data, self.spc3._num_pixels, 1)
                    # Remove counter and frame dimensions to get (cols, rows)
                    frame = frame[0, 0, :, :]
                    counter_frames.append(frame)
                frames_list.append(counter_frames)

            # Stack into final array: (counters, frames, cols, rows)
            frames = np.array(frames_list)

            # Apply background subtraction (if enabled) to every frame
            for ci in range(frames.shape[0]):
                for fi in range(frames.shape[1]):
                    frames[ci, fi] = self.apply_background_subtraction(frames[ci, fi])

            self._acquiring = False
            self.log.info(
                f"Snap acquisition complete: shape={frames.shape}, dtype={frames.dtype}"
            )
            return frames

        except Exception as e:
            self._acquiring = False
            self.log.error(f"Snap acquisition failed: {e}")
            import traceback

            self.log.error(f"Traceback: {traceback.format_exc()}")
            return None

    def _save_background_sidecar(self, spc3_filepath):
        """Save the current background image as a sidecar .bg.npy file.

        The sidecar is placed next to the .spc3 file with the same stem:
            mydata.spc3  →  mydata.bg.npy

        The background is saved as a float32 2-D array (rows × cols) so that
        it can be directly subtracted from any frame loaded from the .spc3
        file during offline analysis:

            import numpy as np
            frames, header = ...  # load via spc.ReadSPC3DataFile or similar
            bg = np.load('mydata.bg.npy')          # shape (rows, cols)
            corrected = np.maximum(frames[0] - bg, 0).astype(np.uint16)

        The .spc3 header byte at metadata offset +112 (file byte 120) is also
        patched to 1 so that the SDK / ImageJ plugin can indicate that a
        background was captured at acquisition time.

        Does nothing if no background image has been captured.
        """
        if not hasattr(self, "_background_image") or self._background_image is None:
            if self._background_subtraction_enabled:
                self.log.warning(
                    "Background subtraction is ENABLED but no background image has been "
                    "captured — sidecar will NOT be saved. "
                    "Click 'Capture Background' before starting acquisition."
                )
            else:
                self.log.info("No background image captured — skipping sidecar save.")
            return

        import os
        import struct

        # Derive sidecar path:  strip .spc3 if present, append .bg.npy
        stem = spc3_filepath
        if stem.lower().endswith(".spc3"):
            stem = stem[:-5]
        sidecar_path = stem + ".bg.npy"

        # Reshape to 2-D (rows × cols) for convenient analysis
        bg_2d = self._background_image.reshape(self._Nrows, self._Ncols).astype(
            np.float32
        )
        np.save(sidecar_path, bg_2d)
        self.log.info(f"Background sidecar saved: {sidecar_path}")

        # Patch the .spc3 header byte 112 (background subtraction enabled flag).
        # The metadata section starts at file byte 8, so the flag is at byte 120.
        if os.path.exists(spc3_filepath):
            try:
                with open(spc3_filepath, "r+b") as fh:
                    fh.seek(8 + 112)
                    fh.write(struct.pack("<B", 1))
            except Exception as exc:
                self.log.warning(
                    f"Could not patch background flag in {spc3_filepath}: {exc}"
                )

    def _patch_gate_header(self, filepath):
        """Write coarse gate settings directly into the .spc3 file header.

        The SDK does not persist coarse gate settings to the file header when
        using ContAcqToFileStart/Stop.  After the file is closed by
        ContAcqToFileStop we inject the values that were configured in the
        module settings.  Offsets within the file (metadata section begins at
        file byte 8):

            file byte 240  (meta +232)  CoarseGate_C1_ON    uint8
            file byte 241  (meta +233)  CoarseGate_C1_Start uint16 LE
            file byte 243  (meta +235)  CoarseGate_C1_Stop  uint16 LE
        """
        if self._gate_mode != "coarse":
            return
        import struct

        try:
            with open(filepath, "r+b") as fh:
                fh.seek(8 + 232)  # gate ON flag
                fh.write(struct.pack("<B", 1))
                fh.write(struct.pack("<H", self._coarse_gate_start))  # start
                fh.write(struct.pack("<H", self._coarse_gate_stop))  # stop
            self.log.info(
                f"Gate header patched: {filepath}  "
                f"start={self._coarse_gate_start} stop={self._coarse_gate_stop}"
            )
        except Exception as exc:
            self.log.warning(f"Failed to patch gate header in {filepath}: {exc}")

    def continuous_acquisition(self, filename):
        """Start a continuous acquisition to file

        @return bool: Success ?
        """
        if self._live or self._acquiring:
            return False
        else:
            self._continuous = True
            self._current_cont_filename = filename
            # Re-apply gate + trigger settings and commit to hardware before
            # starting the continuous acquisition.  Any intermediate SetCameraPar
            # call (e.g. from set_exposure / set_binning) resets the SDK's
            # pending-settings queue, which would silently drop gate/trigger config.
            # Calling _apply_settings() here guarantees the hardware state matches
            # the module's internal state at the moment acquisition begins.
            # Note: ContAcqToFileStart zeroes the gate header bytes in the file,
            # which is why gate values are patched back in stop_continuous_acquisition()
            # after ContAcqToFileStop() closes the file.
            self._apply_settings()
            self.spc3.ContAcqToFileStart(filename)
        return True

    def stop_continuous_acquisition(self):
        """Stop continuous acquisition

        @return bool: Success ?
        """
        if self._continuous:
            self.spc3.ContAcqToFileStop()
            if self._current_cont_filename is not None:
                spc3_path = self._current_cont_filename + ".spc3"
                self._patch_gate_header(spc3_path)
                self._save_background_sidecar(spc3_path)
                self._current_cont_filename = None
            self._continuous = False
        return True

    def get_continuous_memory(self):
        """Get continuous acquisition memory data

        @return int: Total number of bytes read
        """
        if self._continuous:
            return self.spc3.ContAcqToFileGetMemory()
        else:
            return 0

    def stop_acquisition(self):
        """Stop/abort live or single acquisition

        @return bool: Success ?
        """
        if self._live:
            self.spc3.LiveSetModeOFF()
        self._live = False
        self._acquiring = False

    def get_acquired_data(self):
        """Return current live mode frame.

        This method is ONLY for live mode video display in the GUI.
        For snap mode, use start_single_acquisition() which returns frames directly.
        For continuous mode, data streams directly to file.

        @return numpy array: Live frame data with background subtraction and scaling applied
        """

        image_array = np.zeros(self._Nrows * self._Ncols)
        if self._live:
            image_array = self.spc3.LiveGetImg()
            # Keep a rolling cache of the last raw live frame.  This is used as
            # a static preview during continuous acquisition (where the SDK
            # streams directly to file and LiveGetImg() cannot be called).
            self._last_display_frame = image_array[0].copy()

        # During continuous acquisition Live and ContAcq are mutually exclusive
        # in the SDK — fall back to whatever the last live frame was.
        if self._continuous and not self._live:
            raw = (
                self._last_display_frame.copy()
                if self._last_display_frame is not None
                else np.zeros((self._Nrows, self._Ncols), dtype=np.uint16)
            )
        else:
            raw = image_array[0]

        # Apply background subtraction, CPS scaling, and reshape, then return
        counter1_frame = self.apply_background_subtraction(raw)

        # Scale to counts per second if enabled
        if self._display_units == "cps":
            hardware_integration = (
                self._HardwareIntegration
                if self._camera_mode == "Advanced"
                else self._HardwareIntegration_Normal
            )
            exposure_time_seconds = hardware_integration * 10e-9 * self._NIntegFrames
            counter1_frame = (
                counter1_frame.astype(np.float32) / exposure_time_seconds
            ).astype(counter1_frame.dtype)

        # Ensure 2D shape for GUI display (rows, cols)
        if counter1_frame.ndim == 1:
            counter1_frame = counter1_frame.reshape(self._Nrows, self._Ncols)

        return counter1_frame

    def set_exposure(self, exposure):
        """Set the exposure time in seconds

        @param float exposure: desired new exposure time in seconds

        @return bool: Success?

        FORMULA: exposure_seconds = NIntegFrames × HardwareIntegration_cycles × 10ns_per_cycle
        Note: HardwareIntegration is in CLOCK CYCLES where each cycle = 10ns
        """
        # Calculate NIntegFrames needed to achieve desired exposure time
        # Actual exposure = NIntegFrames * HardwareIntegration_cycles * 10ns
        # IMPORTANT: HardwareIntegration is in CLOCK CYCLES (each cycle = 10ns)
        # For Normal mode: HardwareIntegration fixed at 1040 cycles (1040 × 10ns = 10.4 µs)
        # For Advanced mode: use configured _HardwareIntegration (in cycles)

        if self._camera_mode == "Advanced":
            hardware_time = self._HardwareIntegration * 10e-9  # convert to seconds
        else:
            hardware_time = (
                self._HardwareIntegration_Normal * 10e-9
            )  # convert to seconds

        # Calculate required NIntegFrames
        n_integ_frames = int(round(exposure / hardware_time))

        # Clamp to valid range
        n_integ_frames = max(
            self._MIN_INTEG_FRAMES, min(n_integ_frames, self._MAX_INTEG_FRAMES)
        )

        if n_integ_frames != int(round(exposure / hardware_time)):
            self.log.warning(
                f"Requested exposure {exposure}s clamped to {n_integ_frames} frames"
            )

        self._NIntegFrames = n_integ_frames
        self._exposure = n_integ_frames * hardware_time  # actual achieved exposure

        # Normalize binary options for safe re-application
        force8bit = self._to_binary(self._Force8bit, "Force8bit")
        half_array = self._to_binary(self._Half_array, "Half_array")
        signed_data = self._to_binary(self._Signed_data, "Signed_data")

        self.spc3.SetCameraPar(
            (
                self._HardwareIntegration
                if self._camera_mode == "Advanced"
                else self._HardwareIntegration_Normal
            ),
            self._NFrames,
            self._NIntegFrames,
            self._NCounters,
            force8bit,
            half_array,
            signed_data,
        )
        self._apply_settings()
        return True

    def get_exposure(self):
        """Get the exposure time in seconds

        @return float exposure time

        FORMULA: exposure = NIntegFrames × HardwareIntegration_cycles × 10ns_per_cycle
        Each clock cycle = 10ns = 10e-9 seconds
        """
        # Calculate actual exposure time: NIntegFrames * HardwareIntegration_cycles * 10ns
        # HardwareIntegration is in clock cycles (10ns per cycle)
        if self._camera_mode == "Advanced":
            hardware_time = (
                self._HardwareIntegration * 10e-9
            )  # cycles × 10ns/cycle = seconds
        else:
            hardware_time = (
                self._HardwareIntegration_Normal * 10e-9
            )  # cycles × 10ns/cycle = seconds

        self._exposure = self._NIntegFrames * hardware_time
        return self._exposure

    # def get_actual_exposure(self):
    # """Get the actual exposure time in seconds

    # @return float exposure time
    # """
    # return self._NIntegFrames * (self._HardwareIntegration_Normal / 100) / 1000

    def set_hardware_integration(self, integration_seconds):
        """Set hardware integration time (only for Advanced mode)

        SetCameraPar first parameter uses CLOCK CYCLES (each cycle = 10ns).
        This method accepts SECONDS and converts to clock cycles.

        @param float integration_seconds: Hardware integration time in SECONDS
        @return bool: Success?

        Conversion formula: seconds × 1e9 ns/s ÷ 10 ns/cycle = clock_cycles
        """
        if self._camera_mode != "Advanced":
            self.log.warning(
                "Hardware integration time is fixed in Normal mode (10.4 us)"
            )
            return False

        # STEP 1: Convert SECONDS to NANOSECONDS (multiply by 1e9)
        integration_ns = integration_seconds * 1e9
        # STEP 2: Convert NANOSECONDS to CLOCK CYCLES (divide by 10, since each cycle = 10ns)
        integration_cycles = int(round(integration_ns / 10.0))

        # Clamp to valid range
        integration_cycles = max(
            self._MIN_HARDWARE_INTEGRATION,
            min(integration_cycles, self._MAX_HARDWARE_INTEGRATION),
        )

        self._HardwareIntegration = integration_cycles

        # Update exposure time calculation
        self._exposure = self._NIntegFrames * integration_cycles * 10e-9

        force8bit = self._to_binary(self._Force8bit, "Force8bit")
        half_array = self._to_binary(self._Half_array, "Half_array")
        signed_data = self._to_binary(self._Signed_data, "Signed_data")

        self.spc3.SetCameraPar(
            integration_cycles,
            self._NFrames,
            self._NIntegFrames,
            self._NCounters,
            force8bit,
            half_array,
            signed_data,
        )
        self._apply_settings()
        return True

    def set_binning(self, binning):
        """Set temporal binning (NIntegFrames)

        @param int binning: Number of frames to integrate
        @return bool: Success?
        """
        # Clamp to valid range
        binning = max(self._MIN_INTEG_FRAMES, min(binning, self._MAX_INTEG_FRAMES))

        self._NIntegFrames = binning

        # Update exposure time
        if self._camera_mode == "Advanced":
            hardware_time = self._HardwareIntegration * 10e-9
        else:
            hardware_time = self._HardwareIntegration_Normal * 10e-9
        self._exposure = binning * hardware_time

        force8bit = self._to_binary(self._Force8bit, "Force8bit")
        half_array = self._to_binary(self._Half_array, "Half_array")
        signed_data = self._to_binary(self._Signed_data, "Signed_data")

        self.spc3.SetCameraPar(
            (
                self._HardwareIntegration
                if self._camera_mode == "Advanced"
                else self._HardwareIntegration_Normal
            ),
            self._NFrames,
            self._NIntegFrames,
            self._NCounters,
            force8bit,
            half_array,
            signed_data,
        )
        self._apply_settings()
        return True

    def get_binning(self):
        """Get the current temporal binning (NIntegFrames)

        @return int: Current binning value
        """
        return self._NIntegFrames

    def get_default_save_directory(self):
        """Return the default save directory from config, or empty string if not set."""
        return self._default_save_directory

    # not applicable for SPAD
    def set_gain(self, gain):
        """Set the gain

        @param float gain: desired new gain

        @return float: new exposure gain
        """
        return 0

    def get_display_units(self):
        """Get the display units setting

        @return str: 'counts' or 'cps'
        """
        return self._display_units

    def set_display_units(self, units):
        """Set the display units

        @param str units: 'counts' or 'cps'
        @return bool: Success?
        """
        if units not in ["counts", "cps"]:
            self.log.error(f"Invalid display units: {units}. Must be 'counts' or 'cps'")
            return False
        self._display_units = units
        self.log.info(f"Display units set to: {units}")
        return True

    def get_gain(self):
        """Get the gain

        @return float: exposure gain
        """
        return 0

    def get_ready_state(self):
        """Is the camera ready for an acquisition ?

        @return bool: ready ?
        """
        if self._live:
            return False
        else:
            return True

    def capture_background_image(self):
        """Capture a background image for background subtraction.

        Uses live mode to capture multiple frames and averages them.
        This ensures the same NIntegFrames settings as normal live acquisition.
        Camera can be in live or idle mode.

        @return bool: Success ?
        """
        try:
            # Determine if we need to start/stop live mode
            was_live = self._live
            if not was_live:
                self.start_live_acquisition()
                import time

                time.sleep(0.5)  # Give hardware time to stabilize

            # Capture multiple live frames using NFrames from config
            num_frames_to_average = self._NFrames
            self.log.info(
                f"Capturing background: averaging {num_frames_to_average} frames"
            )
            frames_list = []

            for i in range(num_frames_to_average):
                image_array = self.spc3.LiveGetImg()
                counter0_frame = image_array[0]  # Shape: (rows, cols)
                frames_list.append(counter0_frame)

            # Stop live if we started it
            if not was_live:
                self.stop_acquisition()

            # Stack and average all frames
            frames_stack = np.stack(
                frames_list, axis=0
            )  # Shape: (num_frames, rows, cols)
            background_2d = np.mean(frames_stack, axis=0).astype(
                np.uint16
            )  # Shape: (rows, cols)

            # Flatten to 1D for storage
            self._background_image = background_2d.flatten()

            self.log.info(
                f"Background image captured: averaged {num_frames_to_average} frames"
            )
            return True
        except Exception as e:
            import traceback

            self.log.error(f"Failed to capture background image: {e}")
            self.log.error(f"Traceback: {traceback.format_exc()}")
            return False

    def apply_background_subtraction(self, frame):
        """Apply stored background image to *frame* if subtraction is enabled.

        This is a pure, mode-independent helper.  It can be called on any
        2-D or 1-D frame array — from live, snap, or loaded-file display.

        @param numpy.ndarray frame: Raw pixel data (any shape)
        @return numpy.ndarray: Subtracted frame (same shape and dtype), or the
                               original frame unchanged if subtraction is off /
                               no background has been captured yet.
        """
        if not self._background_subtraction_enabled:
            return frame
        if not hasattr(self, "_background_image") or self._background_image is None:
            return frame

        original_shape = frame.shape
        frame_flat = frame.flatten().astype(np.float32)

        if self._background_image.size != frame_flat.size:
            self.log.warning(
                f"Background size mismatch: frame={frame_flat.size}, "
                f"background={self._background_image.size} — subtraction skipped"
            )
            return frame

        subtracted = np.maximum(
            frame_flat - self._background_image.astype(np.float32), 0
        )
        return subtracted.astype(frame.dtype).reshape(original_shape)

    def enable_background_subtraction(self):
        """Enable software background subtraction.

        The background image must be captured first using capture_background_image().

        @return bool: Success ?
        """
        if not hasattr(self, "_background_image") or self._background_image is None:
            self.log.warning(
                "No background image captured. Call capture_background_image() first."
            )
            return False

        self._background_subtraction_enabled = True
        self.log.info("Background subtraction enabled")
        return True

    def disable_background_subtraction(self):
        """Disable software background subtraction.

        @return bool: Success ?
        """
        self._background_subtraction_enabled = False
        self.log.info("Software background subtraction disabled")
        return True

    def read_spc3_file(self, path):
        """Read a .spc3 data file and return frames array and header

        @param str path: Path to .spc3 file
        @return tuple: (frames array, header dict)
        """
        frames, header = self.spc3.ReadSPC3DataFile(path)
        return frames, header

    def save_frames_to_file(self, frames, filepath):
        """Save acquired snap frames to .spc3 file using SDK

        Uses SDK's SaveImgDisk to write directly from internal buffer.
        SDK may add .spc3 extension automatically.

        @param numpy array frames: Frames array (to get actual frame count)
        @param str filepath: Path to save file
        @return bool: Success?
        """
        try:
            import os

            # Normalize path to Windows format (handles spaces in directory names)
            filepath = os.path.normpath(filepath)

            # Remove .spc3 extension if present (SDK adds it automatically)
            if filepath.endswith(".spc3"):
                filepath = filepath[:-5]

            # Ensure directory exists
            directory = os.path.dirname(filepath)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                self.log.info(f"Created directory: {directory}")

            # Get actual number of frames from the frames array
            # Shape is (num_counters, num_frames, rows, cols)
            actual_num_frames = frames.shape[1]

            self.log.info(f"Saving {actual_num_frames} frames to '{filepath}'")

            # SaveImgDisk(Start_Img, End_Img, filename, mode)
            # Note: SDK uses 1-based indexing, so 1 to actual_num_frames saves all frames
            self.spc3.SaveImgDisk(
                1, actual_num_frames, filepath, SPC3.OutFileFormat.SPC3_FILEFORMAT
            )

            # SDK adds .spc3 extension automatically
            expected_file = filepath + ".spc3"
            if os.path.exists(expected_file):
                # Patch gate header: SaveImgDisk zeroes the coarse-gate bytes in
                # the file header (same behaviour as ContAcqToFileStart), so we
                # must write the configured gate values back after saving.
                self._patch_gate_header(expected_file)
                self._save_background_sidecar(expected_file)
                return True
            else:
                self.log.error(
                    f"SaveImgDisk completed but file not found at {expected_file}"
                )
                return False
        except Exception as e:
            self.log.error(f"Failed to save frames: {e}")
            import traceback

            self.log.error(f"Traceback: {traceback.format_exc()}")
            return False

    def load_acquisition_file(self, filepath):
        """Load acquisition file for viewing

        Loads .npy files (from snap) or .spc3 files (from continuous acquisitions).
        Works with any image dimensions stored in the file.

        @param str filepath: Path to .npy or .spc3 file
        @return bool: Success?
        """
        try:
            import os

            # Normalize path to Windows format (handles spaces in directory names)
            filepath = os.path.normpath(filepath)

            # Load based on file extension
            if filepath.endswith(".npy"):
                # Numpy format (snap acquisitions)
                self._loaded_frames = np.load(filepath)
                self._loaded_header = {}  # No header in numpy files
            else:
                # SPC3 format (continuous acquisitions)
                self._loaded_frames, self._loaded_header = self.read_spc3_file(filepath)
            self._current_frame_index = 0
            self._loaded_filepath = filepath

            num_counters, num_frames, rows, cols = self._loaded_frames.shape
            self.log.info(
                f"Loaded {num_frames} frames ({rows}\u00d7{cols}) from {filepath}"
            )

            # Auto-load background sidecar if present
            import os

            stem = os.path.splitext(filepath)[0]
            sidecar = stem + ".bg.npy"
            if os.path.exists(sidecar):
                self._loaded_background = np.load(
                    sidecar
                )  # float32, shape (rows, cols)
                self.log.info(f"Background sidecar loaded: {sidecar}")
            else:
                self._loaded_background = None

            return True
        except Exception as e:
            self.log.error(f"Failed to load file {filepath}: {e}")
            import traceback

            self.log.error(f"Traceback: {traceback.format_exc()}")
            return False

    def convert_spc3_to_numpy(self, spc3_filepath, numpy_filepath):
        """Convert SPC3 format file to numpy format

        @param str spc3_filepath: Path to input .spc3 file
        @param str numpy_filepath: Path to output .npy file
        @return bool: Success?
        """
        try:
            frames, header = self.read_spc3_file(spc3_filepath)
            np.save(numpy_filepath, frames)
            self.log.info(
                f"Converted {spc3_filepath} to numpy format: {numpy_filepath}"
            )
            return True
        except Exception as e:
            self.log.error(f"Failed to convert SPC3 to numpy: {e}")
            return False

    def get_loaded_frame_count(self):
        """Get number of frames in loaded file

        @return int: Number of frames, or 0 if no file loaded
        """
        if hasattr(self, "_loaded_frames") and self._loaded_frames is not None:
            # frames shape is (num_counters, num_frames, rows, cols)
            return self._loaded_frames.shape[1]
        return 0

    def get_loaded_frame(self, frame_index):
        """Get a specific frame from loaded file

        @param int frame_index: Frame index (0-based)
        @return numpy array: Frame data, or None if invalid
        """
        if not hasattr(self, "_loaded_frames") or self._loaded_frames is None:
            self.log.warning("No file loaded")
            return None

        num_frames = self._loaded_frames.shape[1]
        if frame_index < 0 or frame_index >= num_frames:
            self.log.warning(
                f"Frame index {frame_index} out of range [0, {num_frames-1}]"
            )
            return None

        # Extract counter 0, frame at index
        # Shape: (num_counters, num_frames, rows, cols) after BufferToFrames
        frame = self._loaded_frames[0, frame_index, :, :]  # Returns (rows, cols)
        self._current_frame_index = frame_index
        return frame

    def get_current_frame_index(self):
        """Get current frame index in loaded file

        @return int: Current frame index
        """
        if hasattr(self, "_current_frame_index"):
            return self._current_frame_index
        return 0

    def get_loaded_filepath(self):
        """Get path of currently loaded file

        @return str: Filepath, or None if no file loaded
        """
        if hasattr(self, "_loaded_filepath"):
            return self._loaded_filepath
        return None

    def get_loaded_background(self):
        """Return the background image associated with the currently loaded file.

        Automatically populated by load_acquisition_file() when a .bg.npy
        sidecar is found next to the .spc3 file.

        @return numpy.ndarray or None: float32 array of shape (rows, cols),
                                       or None if no sidecar was found.
        """
        if hasattr(self, "_loaded_background"):
            return self._loaded_background
        return None

    def load_frames_from_memory(self, frames):
        """Load frames directly from memory for viewing

        @param numpy.ndarray frames: Frames array to load (counters, frames, rows, cols)
        @return bool: Success?
        """
        try:
            self._loaded_frames = frames
            self._loaded_header = {}  # No header for memory frames
            self._current_frame_index = 0
            self._loaded_filepath = "(unsaved snap acquisition)"

            num_counters, num_frames, rows, cols = frames.shape
            self.log.info(
                f"Loaded {num_frames} frames ({rows}×{cols}) from memory (counters: {num_counters})"
            )
            return True
        except Exception as e:
            self.log.error(f"Failed to load frames from memory: {e}")
            import traceback

            self.log.error(f"Traceback: {traceback.format_exc()}")
            return False
