# -*- coding: utf-8 -*-
"""
PI E-727 + P-562.3CD scanner motion hardware module.

Handles connection, motion, and waveform line-scan commands only (no
photon counting). Implements PIE710ScannerInterface, so it's a drop-in
replacement for PIE710Scanner behind PIE710CounterInterfuse.

Units: the PI GCS2 DLL and all internal math here work in micrometers
(um), matching the hardware's native units. Qudi's ScanningProbeInterface
(and its GUI) expects meters everywhere. Conversion between the two
happens ONLY at the public interface boundary (x_range/y_range/z_range,
move_absolute, get_target, get_position, start_scan, get_scan_safe_range)
-- everything internal (PIE727Controller, padding math, DLL calls) stays
in real hardware micrometers throughout.

Scan mechanism: one-way waveform ramp per line (WAV_LIN), triggered via
GCS TriggerMode 0 ("Position Distance") -- one real hardware trigger
pulse per physical step of motion, counted on the DAQ side by
NIXSeriesCounter.arm_position_trigger()/read_position_trigger(). Ported
from a proven MATLAB implementation (ClassPiezoE727.m).

Run-up (padding): the stage cannot start a line at full speed, so every line starts a run-up
distance BELOW its first trigger position and ends the same distance above its last one. No
pixels are recorded during the run-up. Per side it is the LARGEST of three limits (each one
disabled if set to 0), with v = line speed = scan range / (pixels * pixel time):

    max_acceleration_um_s2   run-up = v**2 / a   the stage may not accelerate harder than a
    min_speedup_time_s       run-up = v * t      ramp at least this long (a line with no ramp
                                                 sometimes gave zero trigger edges)
    min_padding_um           run-up = fixed      at least this distance. Short, slow lines (e.g.
                                                 the optimizer's) otherwise start only nm below
                                                 the first trigger position; the stage then
                                                 settles past it and the controller outputs no
                                                 trigger pulses for the whole line.

The run-up has to fit inside the travel on both ends, so the usable range of the fast axis is
travel - 2 * run-up (get_scan_safe_range(); the interfuse shrinks larger requests to it). Fast
lines over long ranges are limited by max_acceleration_um_s2, slow or short lines by
min_padding_um. To scan larger ranges: scan slower (lower frequency = longer pixel time, or more
pixels) or raise max_acceleration_um_s2 as far as the stage really follows (check the image
edges for distortion).

YAML configuration:
    hardware:
        my_pi_e727_scanner:
            module.Class: 'pi_scanner.pi_e727_scanning_probe.PIE727Scanner'
            options:
                dll_path: 'C:/PI/PI_GCS2_DLL_x64.dll'  # PI GCS2 DLL
                usb_serial: ''                # controller serial number; '' only works if the PI
                                              # DLL lists exactly one USB device
                axis_ids: ['1', '2', '3']     # controller axes used as x, y, z
                invert_axes: []               # e.g. ['y'] to mirror axes, see below
                trigger_output_id: 1          # controller digital output wired to the DAQ trigger
                                              # input (scan_trigger_terminal of the counter)
                wave_generator_rate_hz: 20000.0  # the controller's wave generator output rate. Not
                                              # set on the controller -- must match it, otherwise
                                              # pixel times and run-ups are off by that ratio
                max_acceleration_um_s2: 3000.0   # run-up limits, see "Run-up" above
                min_speedup_time_s: 0.005
                min_padding_um: 0.5

The travel range of each axis is read from the controller on activation.

invert_axes mirrors the listed axes within their travel range: a qudi position p
corresponds to the controller position (min + max - p). All motion, position read-back and
scan commands are converted at this module's interface, so scans run physically mirrored
while pixels and positions stay consistent. Positions saved before changing this option
(e.g. POIs) refer to the old, unmirrored coordinates.
"""

import ctypes
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from qudi.core.configoption import ConfigOption

from .pi_e710_scanning_probe import PIE710ScannerInterface


# Hardware (PI DLL) works in micrometers; Qudi's interface works in
# meters. Conversion happens only at the PIE727Scanner public boundary.
_M_PER_UM = 1e-6
_UM_PER_M = 1e6


# ══════════════════════════════════════════════════════════════════════════════
#  PI GCS2 DLL WRAPPER  (PI_GCS2_DLL_x64.dll)  -- all values in micrometers
# ══════════════════════════════════════════════════════════════════════════════

class PIE727Error(Exception):
    def __init__(self, message: str, error_code: int = 0):
        super().__init__(message)
        self.error_code = error_code


class PIE727Controller:
    """ctypes wrapper for PI_GCS2_DLL_x64.dll (E-727 + P-562.3CD stage).
    All positions/amplitudes here are real hardware micrometers."""

    _SOFT_ERRORS = frozenset({0, 2, 5, 7, 8, 10})
    _BUF_SM = 256
    _BUF_MD = 1024

    def __init__(self, dll_path: str, use_windll: bool = False, logger=None):
        self._id: int = -1
        self._dll_path = dll_path
        self._registered: set = set()
        self._axes: List[str] = []
        self._travel_min: List[float] = []
        self._travel_max: List[float] = []

        # Optional logger from PIE727Scanner (this class has no qudi
        # logger of its own). If None, padding-debug logs are skipped.
        self._logger = logger

        # Run-up (padding) limits -- see module docstring. Combined per scan
        # in padding_um(); None or 0 disables a limit.
        self.max_acceleration_um_s2: Optional[float] = None
        self.min_speedup_time_s: Optional[float] = None
        self.min_padding_um: Optional[float] = None

        # Cached kwargs from the last scan_axis() call, for retrigger_line().
        self._last_scan_kwargs: Optional[dict] = None

        self._dll = self._load_dll(dll_path, use_windll)
        self._setup_signatures()

    @staticmethod
    def _load_dll(path: str, use_windll: bool) -> ctypes.CDLL:
        if not os.path.isfile(path):
            raise PIE727Error(f"DLL not found: '{path}'")
        try:
            return (ctypes.WinDLL if use_windll else ctypes.CDLL)(path)
        except OSError as exc:
            raise PIE727Error(f"Cannot load DLL: {exc}") from exc

    def _setup_signatures(self):
        """Declare ctypes argtypes/restype for each DLL function used."""
        d  = self._dll
        I  = ctypes.c_int
        D  = ctypes.c_double
        CP = ctypes.c_char_p
        PI = ctypes.POINTER(ctypes.c_int)
        PD = ctypes.POINTER(ctypes.c_double)

        def sig(name, rt, *at):
            try:
                fn = getattr(d, name)
                fn.restype = rt
                fn.argtypes = list(at)
                self._registered.add(name)
            except AttributeError:
                pass

        sig("PI_ConnectUSB",       I, CP)
        sig("PI_ConnectRS232",     I, I, I)
        sig("PI_IsConnected",      I, I)
        sig("PI_CloseConnection",  None, I)
        sig("PI_GetError",         I, I)
        sig("PI_TranslateError",   I, I, CP, I)

        sig("PI_qIDN",   I, I, CP, I)
        sig("PI_qVER",   I, I, CP, I)
        sig("PI_qSAI",   I, I, CP, I)

        sig("PI_SVO",       I, I, CP, PI)
        sig("PI_qSVO",      I, I, CP, PI)
        sig("PI_MOV",       I, I, CP, PD)
        sig("PI_MVR",       I, I, CP, PD)
        sig("PI_qPOS",      I, I, CP, PD)
        sig("PI_qONT",      I, I, CP, PI)
        sig("PI_IsMoving",  I, I, CP, PI)
        sig("PI_HLT",       I, I, CP)
        sig("PI_STP",       I, I)
        sig("PI_qTMN",      I, I, CP, PD)
        sig("PI_qTMX",      I, I, CP, PD)
        sig("PI_VEL",       I, I, CP, PD)
        sig("PI_qVEL",      I, I, CP, PD)

        sig("PI_qTIO", I, I, PI, PI)

        sig("PI_IsGeneratorRunning", I, I, PI, PI, I)
        sig("PI_WAV_LIN", I, I, I, I, I, I, I, D, D, I)
        sig("PI_qWAV",    I, I, PI, PI, PD, I)
        sig("PI_WGO",     I, I, PI, PI, I)
        sig("PI_qWGO",    I, I, PI, PI, I)
        sig("PI_WGC",     I, I, PI, PI, I)
        sig("PI_WSL",     I, I, PI, PI, I)
        sig("PI_WCL",     I, I, PI, I)
        sig("PI_WOS",     I, I, PI, PD, I)

        sig("PI_CTO",  I, I, PI, PI, PD, I)
        sig("PI_qCTO", I, I, PI, PI, PD, I)

    def _fn(self, name: str):
        if name not in self._registered:
            raise PIE727Error(
                f"'{name}' not exported by '{os.path.basename(self._dll_path)}'")
        return getattr(self._dll, name)

    def _require_connection(self):
        if self._id < 0:
            raise PIE727Error("Not connected.")

    def _check(self, result: int, fname: str):
        if not result:
            code = self._dll.PI_GetError(self._id) if self._id >= 0 else -1
            raise PIE727Error(
                f"'{fname}' failed -- error {code}: {self._translate(code)}", code)

    def _soft_check(self, result: int, fname: str) -> bool:
        if result:
            return True
        code = self._dll.PI_GetError(self._id) if self._id >= 0 else -1
        if code in self._SOFT_ERRORS:
            return False
        raise PIE727Error(
            f"'{fname}' failed -- error {code}: {self._translate(code)}", code)

    def _translate(self, code: int) -> str:
        if "PI_TranslateError" not in self._registered:
            return f"(PI_TranslateError not exported -- code {code} untranslated)"
        buf = ctypes.create_string_buffer(self._BUF_MD)
        try:
            self._fn("PI_TranslateError")(code, buf, self._BUF_MD)
        except Exception as exc:
            return f"(translation call failed for code {code}: {exc})"
        return buf.value.decode("ascii", errors="replace").strip()

    @staticmethod
    def _ax(axes) -> bytes:
        if isinstance(axes, bytes):
            return axes
        if isinstance(axes, (list, tuple)):
            return " ".join(str(a) for a in axes).encode("ascii")
        if isinstance(axes, str):
            return axes.encode("ascii")
        return b""

    @staticmethod
    def _nax(axes, fallback: int = 0) -> int:
        if isinstance(axes, (list, tuple)):
            return len(axes)
        b = PIE727Controller._ax(axes)
        return len(b.split()) if b else fallback

    @staticmethod
    def _darr(vals) -> ctypes.Array:
        v = list(vals)
        return (ctypes.c_double * len(v))(*v)

    @staticmethod
    def _iarr(vals) -> ctypes.Array:
        v = [int(x) for x in vals]
        return (ctypes.c_int * len(v))(*v)

    def _dbuf(self, n: int) -> ctypes.Array:
        return (ctypes.c_double * max(n, 1))()

    def _ibuf(self, n: int) -> ctypes.Array:
        return (ctypes.c_int * max(n, 1))()

    def clear_error(self):
        if self._id >= 0:
            self._dll.PI_GetError(self._id)

    @property
    def connected(self) -> bool:
        return self._id >= 0 and bool(self._dll.PI_IsConnected(self._id))

    @property
    def axes(self) -> List[str]:
        return list(self._axes)

    @property
    def travel_min(self) -> List[float]:
        return list(self._travel_min)

    @property
    def travel_max(self) -> List[float]:
        return list(self._travel_max)

    def connect_usb(self, description: str = "") -> int:
        _id = self._fn("PI_ConnectUSB")(description.encode("ascii"))
        if _id < 0:
            raise PIE727Error(f"USB connect failed (description='{description}')")
        self._id = _id
        return _id

    def connect_rs232(self, port_number: int, baud_rate: int = 115200) -> int:
        _id = self._fn("PI_ConnectRS232")(port_number, baud_rate)
        if _id < 0:
            raise PIE727Error(f"RS-232 connect failed (COM{port_number})")
        self._id = _id
        return _id

    def close_connection(self):
        if self._id >= 0:
            try:
                self._fn("PI_CloseConnection")(self._id)
            except Exception:
                pass
            self._id = -1

    def get_identification(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        self._check(self._fn("PI_qIDN")(self._id, buf, self._BUF_MD), "qIDN")
        return buf.value.decode("ascii", errors="replace").strip()

    def get_version(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        ok = self._soft_check(
            self._fn("PI_qVER")(self._id, buf, self._BUF_MD), "qVER")
        return (buf.value.decode("ascii", errors="replace").strip()
                if ok else "(not supported)")

    def get_axes(self) -> List[str]:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        ok = self._soft_check(
            self._fn("PI_qSAI")(self._id, buf, self._BUF_MD), "qSAI")
        if not ok:
            return ['1', '2', '3']
        raw = buf.value.decode("ascii", errors="replace").strip()
        axes = [line.strip() for line in raw.splitlines() if line.strip()]
        return axes if axes else ['1', '2', '3']

    def get_digital_io_counts(self) -> Tuple[int, int]:
        self._require_connection()
        n_in, n_out = ctypes.c_int(0), ctypes.c_int(0)
        self._check(
            self._fn("PI_qTIO")(self._id, ctypes.byref(n_in), ctypes.byref(n_out)),
            "qTIO")
        return n_in.value, n_out.value

    def set_servo(self, axes, states: List[bool]):
        self._require_connection()
        self._check(
            self._fn("PI_SVO")(self._id, self._ax(axes), self._iarr(states)),
            "SVO")

    def get_servo(self, axes) -> List[bool]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(self._fn("PI_qSVO")(self._id, self._ax(axes), arr), "qSVO")
        return [bool(arr[i]) for i in range(n)]

    def get_position(self, axes) -> List[float]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(self._fn("PI_qPOS")(self._id, self._ax(axes), arr), "qPOS")
        return [arr[i] for i in range(n)]

    def move_absolute(self, axes, positions: List[float]):
        self._require_connection()
        self._check(
            self._fn("PI_MOV")(self._id, self._ax(axes), self._darr(positions)),
            "MOV")

    def move_relative(self, axes, steps: List[float]):
        self._require_connection()
        self._check(
            self._fn("PI_MVR")(self._id, self._ax(axes), self._darr(steps)),
            "MVR")

    def is_moving(self, axes) -> List[bool]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("PI_IsMoving")(self._id, self._ax(axes), arr), "IsMoving")
        return [bool(arr[i]) for i in range(n)]

    def is_on_target(self, axes) -> List[bool]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("PI_qONT")(self._id, self._ax(axes), arr), "qONT")
        return [bool(arr[i]) for i in range(n)]

    def halt(self, axes):
        self._require_connection()
        self._check(self._fn("PI_HLT")(self._id, self._ax(axes)), "HLT")

    def stop_all(self):
        self._require_connection()
        self._check(self._fn("PI_STP")(self._id), "STP")

    def wait_for_motion(
        self, axes, poll_interval: float = 0.05,
        timeout: float = 60.0, settle_check: bool = True,
    ):
        deadline = time.monotonic() + timeout
        while any(self.is_moving(axes)):
            if time.monotonic() > deadline:
                raise PIE727Error(f"Timeout waiting for motion on '{axes}'")
            time.sleep(poll_interval)
        if settle_check:
            settle_deadline = time.monotonic() + min(timeout * 0.25, 5.0)
            while not all(self.is_on_target(axes)):
                if time.monotonic() > settle_deadline:
                    break
                time.sleep(poll_interval)

    def get_min_travel(self, axes) -> List[float]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(self._fn("PI_qTMN")(self._id, self._ax(axes), arr), "qTMN")
        return [arr[i] for i in range(n)]

    def get_max_travel(self, axes) -> List[float]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(self._fn("PI_qTMX")(self._id, self._ax(axes), arr), "qTMX")
        return [arr[i] for i in range(n)]

    def set_velocity(self, axes, velocities: List[float]):
        self._require_connection()
        self._check(
            self._fn("PI_VEL")(self._id, self._ax(axes), self._darr(velocities)),
            "VEL")

    def wave_clear(self, wave_table_ids: List[int]):
        self._require_connection()
        arr = self._iarr(wave_table_ids)
        self._check(
            self._fn("PI_WCL")(self._id, arr, len(wave_table_ids)), "WCL")

    def wave_lin(
        self, wave_table_id: int, offset_first_point: int,
        n_points: int, add_append_wave: int,
        n_speedupdown_points: int, amplitude: float, offset: float,
        segment_length: int,
    ):
        """PI_WAV_LIN -- program one linear ramp segment into the wave table."""
        self._require_connection()
        self._check(
            self._fn("PI_WAV_LIN")(
                self._id, wave_table_id, offset_first_point, n_points,
                add_append_wave, n_speedupdown_points,
                ctypes.c_double(amplitude), ctypes.c_double(offset),
                segment_length),
            "WAV_LIN")

    def wave_select(self, wave_gen_ids: List[int], wave_table_ids: List[int]):
        self._require_connection()
        assert len(wave_gen_ids) == len(wave_table_ids)
        self._check(
            self._fn("PI_WSL")(
                self._id, self._iarr(wave_gen_ids), self._iarr(wave_table_ids),
                len(wave_gen_ids)),
            "WSL")

    def wave_set_cycles(self, wave_gen_ids: List[int], cycles: List[int]):
        self._require_connection()
        assert len(wave_gen_ids) == len(cycles)
        self._check(
            self._fn("PI_WGC")(
                self._id, self._iarr(wave_gen_ids), self._iarr(cycles),
                len(wave_gen_ids)),
            "WGC")

    def wave_set_offset(self, wave_gen_ids: List[int], offsets: List[float]):
        self._require_connection()
        assert len(wave_gen_ids) == len(offsets)
        self._check(
            self._fn("PI_WOS")(
                self._id, self._iarr(wave_gen_ids), self._darr(offsets),
                len(wave_gen_ids)),
            "WOS")

    def wave_start_stop(self, wave_gen_ids: List[int], start_modes: List[int]):
        """PI_WGO. start_modes: 1 = start, 0 = stop."""
        self._require_connection()
        assert len(wave_gen_ids) == len(start_modes)
        self._check(
            self._fn("PI_WGO")(
                self._id, self._iarr(wave_gen_ids), self._iarr(start_modes),
                len(wave_gen_ids)),
            "WGO")

    def is_wave_generator_running(self, wave_gen_ids: List[int]) -> List[bool]:
        self._require_connection()
        arr = self._iarr(wave_gen_ids)
        out = self._ibuf(len(wave_gen_ids))
        self._check(
            self._fn("PI_IsGeneratorRunning")(
                self._id, arr, out, len(wave_gen_ids)),
            "IsGeneratorRunning")
        return [bool(out[i]) for i in range(len(wave_gen_ids))]

    def set_trigger_param(self, trig_out_id: int, cto_param: int, value: float):
        self._require_connection()
        self._check(
            self._fn("PI_CTO")(
                self._id,
                self._iarr([trig_out_id]),
                self._iarr([cto_param]),
                self._darr([value]),
                1),
            f"CTO (param={cto_param}, value={value})")

    def get_trigger_param(self, trig_out_id: int, cto_param: int) -> float:
        self._require_connection()
        out = self._dbuf(1)
        self._check(
            self._fn("PI_qCTO")(
                self._id, self._iarr([trig_out_id]), self._iarr([cto_param]),
                out, 1),
            "qCTO")
        return out[0]

    def configure_position_distance_trigger(
        self, trigger_output_id: int, axis_num: int,
        trig_step: float, trig_start: float, trig_stop: float,
        disable_threshold: float,
    ):
        """GCS TriggerMode 0: one trigger pulse per trig_step of real
        motion along axis_num, active only within [trig_start, trig_stop]."""
        self.set_trigger_param(trigger_output_id, 8, disable_threshold)
        self.set_trigger_param(trigger_output_id, 2, axis_num)
        self.set_trigger_param(trigger_output_id, 3, 0)
        self.set_trigger_param(trigger_output_id, 1, trig_step)
        self.set_trigger_param(trigger_output_id, 8, trig_start)
        self.set_trigger_param(trigger_output_id, 9, trig_stop)

    # ── Automatic padding (run-up) computation ─────────────────────────────────

    def padding_um(self, t_pixel: float, n_points: int, span_um: float,
                   with_binding: bool = False):
        """Run-up distance per side (um) for a line of span_um scanned in
        t_pixel * n_points: the LARGEST of the three limits (see module
        docstring), each disabled if not set or <= 0:
            max_acceleration_um_s2  ->  v**2 / a
            min_speedup_time_s      ->  v * t
            min_padding_um          ->  fixed distance
        with v = span_um / (t_pixel * n_points) the line speed.

        @return float, or (float, str name of the deciding limit) if with_binding
        """
        line_time = t_pixel * n_points
        span_um = abs(span_um)
        limits = {}
        if line_time > 0 and span_um > 0:
            speed = span_um / line_time
            if self.max_acceleration_um_s2:
                limits['max_acceleration_um_s2'] = speed ** 2 / self.max_acceleration_um_s2
            if self.min_speedup_time_s:
                limits['min_speedup_time_s'] = speed * self.min_speedup_time_s
        if self.min_padding_um:
            limits['min_padding_um'] = float(self.min_padding_um)
        if not limits:
            return (0.0, 'none') if with_binding else 0.0
        binding = max(limits, key=limits.get)
        return (limits[binding], binding) if with_binding else limits[binding]

    # ── High-level single-axis line scan (ported from proven MATLAB ScanLine) ─

    def scan_axis(
        self, axis_num: int, positions, t_pixel: float,
        trigger_output_id: int, wave_generator_rate_hz: float,
        disable_threshold: float,
    ) -> float:
        """Configure and run a single-axis line scan (all units um).
        Returns the estimated scan duration in seconds."""
        self._require_connection()
        n_points = len(positions)
        start, stop = positions[0], positions[-1]
        amp_true = stop - start

        # Fraction of the padded ramp that is the real (triggered) line
        pad_um, binding = self.padding_um(t_pixel, n_points, amp_true, with_binding=True)
        ratio = amp_true / (amp_true + 2.0 * pad_um) if amp_true > 0 else 1.0
        if self._logger is not None:
            self._logger.debug(
                f"scan_axis: run-up {pad_um:.3f} um per side for span={amp_true:.3f} um, "
                f"n_points={n_points}, t_pixel={t_pixel*1e3:.4f} ms -- deciding limit: {binding}"
            )
        servo_pt = 1.0 / wave_generator_rate_hz
        line_time = t_pixel * n_points / min(ratio, 1.0)

        if ratio >= 1.0:
            # No padding (both limits disabled) -- real risk of zero
            # trigger edges, see module docstring.
            wave_offset = start
            amp_padded = amp_true
            n_speedupdown = 0
            linear_region_len = round(line_time / servo_pt)
        else:
            perc_over = ratio / ((1.0 - ratio) / 2.0)
            wave_offset = start - amp_true / perc_over
            amp_padded = amp_true / ratio
            linear_region_len = round(line_time * ratio / servo_pt)
            n_speedupdown = round(line_time * (1.0 - ratio) / servo_pt / 2.0)

        n_wave_points = linear_region_len + 2 * n_speedupdown
        segment_length = n_wave_points

        # Sanity-check padded range against real travel limits before
        # issuing MOV. Tolerance clears float rounding noise only.
        travel_min = self.get_min_travel([str(axis_num)])[0]
        travel_max = self.get_max_travel([str(axis_num)])[0]
        padded_low  = wave_offset
        padded_high = wave_offset + amp_padded
        _tol = 1e-6
        if padded_low < travel_min - _tol or padded_high > travel_max + _tol:
            raise PIE727Error(
                f"scan_axis: padded scan range [{padded_low:.4f}, "
                f"{padded_high:.4f}] um (requested [{start:.4f}, "
                f"{stop:.4f}] um"
                + ("" if ratio >= 1.0 else
                   f" + automatic speed-up/slow-down padding, effective "
                   f"ratio={ratio:.4f}")
                + f") exceeds axis {axis_num}'s real travel range "
                f"[{travel_min:.4f}, {travel_max:.4f}] um."
            )

        # Clamp to the real boundary (zero tolerance on real firmware),
        # after the check above already confirmed we're within tolerance.
        wave_offset = float(np.clip(wave_offset, travel_min, travel_max))

        # Disable triggering BEFORE moving to the new line's offset, to
        # avoid arming the previous line's thresholds during this move.
        self.set_trigger_param(trigger_output_id, 8, disable_threshold)

        self.move_absolute([str(axis_num)], [wave_offset])
        self.wait_for_motion([str(axis_num)], timeout=30.0)

        self.wave_clear([axis_num])
        self.wave_set_cycles([axis_num], [1])
        self.wave_lin(
            wave_table_id=axis_num, offset_first_point=0,
            n_points=n_wave_points, add_append_wave=0,
            n_speedupdown_points=n_speedupdown,
            amplitude=amp_padded, offset=wave_offset,
            segment_length=segment_length,
        )
        self.wave_select([axis_num], [axis_num])

        trig_step = amp_true / n_points
        trig_start = start
        trig_stop = trig_start + amp_true + trig_step * 0.5
        self.configure_position_distance_trigger(
            trigger_output_id=trigger_output_id, axis_num=axis_num,
            trig_step=trig_step, trig_start=trig_start, trig_stop=trig_stop,
            disable_threshold=disable_threshold,
        )

        self.wave_set_offset([axis_num], [0.0])
        self.wave_start_stop([axis_num], [1])

        return segment_length * servo_pt * 1.1

    def retrigger_line(self) -> float:
        """Re-run the most recently configured line from scratch (full
        move + reprogram + retrigger -- the waveform doesn't self-repeat)."""
        if self._last_scan_kwargs is None:
            raise PIE727Error(
                "retrigger_line() called with no prior scan_axis() call.")
        return self.scan_axis(**self._last_scan_kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close_connection()

    def __del__(self):
        try:
            self.close_connection()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  CONCRETE HARDWARE MODULE — PIE727Scanner (public interface: meters)
# ══════════════════════════════════════════════════════════════════════════════

class PIE727Scanner(PIE710ScannerInterface):
    """PI E-727 + P-562.3CD Qudi hardware module. Drop-in replacement for
    PIE710Scanner behind PIE710CounterInterfuse.

    Internal state/hardware calls use micrometers; every public method
    below converts to/from meters at the boundary (see module docstring).
    """

    # See the module docstring for what each option does and how they interact.
    _dll_path               = ConfigOption('dll_path', default="C:/PI/PI_GCS2_DLL_x64.dll")
    _usb_serial             = ConfigOption('usb_serial', default="")
    _axis_ids               = ConfigOption('axis_ids', default=['1', '2', '3'])
    _invert_axes            = ConfigOption('invert_axes', default=[])
    _trigger_output_id      = ConfigOption('trigger_output_id', default=1)
    _wave_generator_rate_hz = ConfigOption('wave_generator_rate_hz', default=20000.0)
    _max_acceleration_um_s2 = ConfigOption('max_acceleration_um_s2', default=3000.0)
    _min_speedup_time_s     = ConfigOption('min_speedup_time_s', default=0.005)
    _min_padding_um         = ConfigOption('min_padding_um', default=0.5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Travel ranges in um, read from the controller in on_activate()
        self._x_range: List[float] = [0.0, 0.0]
        self._y_range: List[float] = [0.0, 0.0]
        self._z_range: List[float] = [0.0, 0.0]
        self._ctrl: Optional[PIE727Controller] = None
        # Internal target position cache -- always micrometers, in qudi (i.e. possibly
        # mirrored, see invert_axes) coordinates.
        self._target_pos: Dict[str, float] = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._axis_of: Dict[str, str] = {}
        self._active_scan_axis: Optional[str] = None
        self._inverted = frozenset()

    def is_axis_inverted(self, axis: str) -> bool:
        """True if `axis` is mirrored (invert_axes). Line scans along a mirrored axis still ramp
        upward in controller coordinates, so their pixels arrive in reversed qudi order and
        must be reversed by the caller (see PIE710CounterInterfuse)."""
        return axis in self._inverted

    def _mirror_um(self, axis: str, value_um: float) -> float:
        """Converts a position of `axis` (um) between qudi and controller coordinates. Identity
        unless the axis is listed in invert_axes; the mirroring is its own inverse, so it works
        in both directions."""
        if axis not in self._inverted:
            return value_um
        lo_um, hi_um = {'x': self._x_range, 'y': self._y_range, 'z': self._z_range}[axis]
        return lo_um + hi_um - value_um

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._ctrl = None
        try:
            self._ctrl = PIE727Controller(self._dll_path, logger=self.log)
            self._ctrl.connect_usb(self._usb_serial)

            ids = list(self._axis_ids)
            if len(ids) < 3:
                raise PIE727Error(
                    f"axis_ids config must have 3 entries (x,y,z); got {ids}")
            self._axis_of = {'x': ids[0], 'y': ids[1], 'z': ids[2]}
            self._ctrl._axes = ids

            invert = [str(ax).lower() for ax in (self._invert_axes or [])]
            unknown = sorted(set(invert) - {'x', 'y', 'z'})
            if unknown:
                raise PIE727Error(
                    f"invert_axes may only contain 'x', 'y', 'z'; got {unknown}")
            self._inverted = frozenset(invert)
            if self._inverted:
                self.log.info(f"Mirrored (inverted) axes: {sorted(self._inverted)}")

            idn = self._ctrl.get_identification()
            self.log.info(f"PI E-727 connected: {idn}")

            self._ctrl.set_servo(ids, [True, True, True])
            time.sleep(0.1)

            # Travel limits from the controller, in um
            mn = self._ctrl.get_min_travel(ids)
            mx = self._ctrl.get_max_travel(ids)
            self.log.info(
                f"Travel limits (from hardware): "
                f"{ids[0]}:[{mn[0]:.3f},{mx[0]:.3f}]  "
                f"{ids[1]}:[{mn[1]:.3f},{mx[1]:.3f}]  "
                f"{ids[2]}:[{mn[2]:.3f},{mx[2]:.3f}] um"
            )
            self._x_range = [mn[0], mx[0]]
            self._y_range = [mn[1], mx[1]]
            self._z_range = [mn[2], mx[2]]

            pos = self._ctrl.get_position(ids)
            self._target_pos = {ax: self._mirror_um(ax, p) for ax, p in zip('xyz', pos)}
            self.log.info(
                f"Position  x={pos[0]:.3f}  y={pos[1]:.3f}  z={pos[2]:.3f} um"
            )

            def _limit(value):
                return float(value) if value else None

            self._ctrl.max_acceleration_um_s2 = _limit(self._max_acceleration_um_s2)
            self._ctrl.min_speedup_time_s = _limit(self._min_speedup_time_s)
            self._ctrl.min_padding_um = _limit(self._min_padding_um)
            self.log.info(
                f"Scan line run-up: largest of max_acceleration_um_s2="
                f"{self._ctrl.max_acceleration_um_s2}, min_speedup_time_s="
                f"{self._ctrl.min_speedup_time_s}, min_padding_um={self._ctrl.min_padding_um} "
                f"(None = disabled)"
            )
        except Exception as exc:
            if self._ctrl is not None:
                try:
                    self._ctrl.close_connection()
                except Exception:
                    pass
                self._ctrl = None
            self.log.exception(f"PI E-727 activation failed: {exc}")
            raise

    def on_deactivate(self) -> None:
        if self._ctrl is not None:
            try:
                self._ctrl.close_connection()
            except Exception as exc:
                self.log.warning(f"PI close_connection: {exc}")
            self._ctrl = None

    # ── Range properties -- real hardware travel limits, in meters ────────────

    @property
    def x_range(self) -> List[float]:
        return [v * _M_PER_UM for v in self._x_range]

    @property
    def y_range(self) -> List[float]:
        return [v * _M_PER_UM for v in self._y_range]

    @property
    def z_range(self) -> List[float]:
        return [v * _M_PER_UM for v in self._z_range]

    # ── Scan-safe range (padding-aware), in meters ─────────────────────────────

    def get_scan_safe_range(
        self, axis: str,
        t_pixel: Optional[float] = None, n_points: Optional[int] = None,
        span: Optional[float] = None,
    ) -> List[float]:
        """Sub-range of this axis' travel in which a line of this t_pixel/n_points (and span, in
        meters) can be scanned, i.e. the travel minus the line's run-up on both ends (see
        PIE727Controller.padding_um(), the same calculation scan_axis() uses).

        The run-up grows with the line's speed, so it is computed for exactly `span` (the
        planned line's |stop - start|). Without `span`, the full travel is assumed. If the line
        does not fit with its run-up at all, the range is computed for the largest span that
        does, so the interfuse shrinks the line to it.

        Without t_pixel/n_points, returns the full, unclamped travel (logs a warning).
        Only affects scan-range clamping in the interfuse; ordinary motion (move_absolute)
        always uses the full travel.
        """
        lo_um, hi_um = {'x': self._x_range, 'y': self._y_range, 'z': self._z_range}[axis]
        travel_um = hi_um - lo_um

        if t_pixel is None or n_points is None or self._ctrl is None:
            self.log.warning(
                f"get_scan_safe_range('{axis}') called without real scan "
                f"parameters -- returning the full, unclamped travel range."
            )
            return [lo_um * _M_PER_UM, hi_um * _M_PER_UM]

        span_um = travel_um if span is None else abs(float(span)) * _UM_PER_M
        if span_um <= 0:
            return [lo_um * _M_PER_UM, hi_um * _M_PER_UM]

        def _pad(s_um):
            return self._ctrl.padding_um(t_pixel, n_points, s_um)

        pad_um = _pad(span_um)
        if span_um + 2.0 * pad_um > travel_um:
            # Largest span that fits together with its own run-up (run-up grows with the span)
            fit_lo, fit_hi = 0.0, min(span_um, travel_um)
            for _ in range(60):
                mid = 0.5 * (fit_lo + fit_hi)
                if mid + 2.0 * _pad(mid) <= travel_um:
                    fit_lo = mid
                else:
                    fit_hi = mid
            pad_um = _pad(fit_lo) if fit_lo > 0 else travel_um / 2.0
        pad_um = min(pad_um, travel_um / 2.0)
        return [round((lo_um + pad_um) * _M_PER_UM, 12),
                round((hi_um - pad_um) * _M_PER_UM, 12)]

    # ── Motion (public interface: meters; internal state/hardware: um) ────────

    def move_absolute(
        self, position: Dict[str, float], blocking: bool = False,
    ) -> Dict[str, float]:
        target_um = dict(self._target_pos)
        for ax, val_m in position.items():
            if ax in ('x', 'y', 'z'):
                target_um[ax] = float(val_m) * _UM_PER_M
        target_um['x'] = float(np.clip(target_um['x'], *self._x_range))
        target_um['y'] = float(np.clip(target_um['y'], *self._y_range))
        target_um['z'] = float(np.clip(target_um['z'], *self._z_range))

        ids = [self._axis_of['x'], self._axis_of['y'], self._axis_of['z']]
        self._ctrl.move_absolute(
            ids, [self._mirror_um(ax, target_um[ax]) for ax in ('x', 'y', 'z')])
        self._target_pos = target_um
        if blocking:
            self._ctrl.wait_for_motion(ids, timeout=60.0)
        return {ax: v * _M_PER_UM for ax, v in self._target_pos.items()}

    def move_relative(
        self, distance: Dict[str, float], blocking: bool = False,
    ) -> Dict[str, float]:
        current = self.get_target()  # meters
        new_pos = {ax: current[ax] + distance.get(ax, 0.0) for ax in ('x', 'y', 'z')}
        return self.move_absolute(new_pos, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        return {ax: v * _M_PER_UM for ax, v in self._target_pos.items()}

    def get_position(self) -> Dict[str, float]:
        try:
            ids = [self._axis_of['x'], self._axis_of['y'], self._axis_of['z']]
            pos_um = self._ctrl.get_position(ids)
            return {ax: self._mirror_um(ax, p) * _M_PER_UM for ax, p in zip('xyz', pos_um)}
        except PIE727Error:
            return {ax: v * _M_PER_UM for ax, v in self._target_pos.items()}

    def sync_position(self) -> None:
        """Re-read real hardware position into the internal (um) cache."""
        try:
            ids = [self._axis_of['x'], self._axis_of['y'], self._axis_of['z']]
            pos_um = self._ctrl.get_position(ids)
            self._target_pos = {ax: self._mirror_um(ax, p) for ax, p in zip('xyz', pos_um)}
        except PIE727Error as exc:
            self.log.warning(f"sync_position failed: {exc}")

    def halt(self) -> None:
        try:
            ids = [self._axis_of['x'], self._axis_of['y'], self._axis_of['z']]
            self._ctrl.halt(ids)
        except PIE727Error:
            pass

    def halt_generators(self) -> None:
        if self._active_scan_axis is not None:
            try:
                axis_num = int(self._axis_of[self._active_scan_axis])
                self._ctrl.wave_start_stop([axis_num], [0])
            except Exception:
                pass

    def reset(self) -> None:
        try:
            ids = [self._axis_of['x'], self._axis_of['y'], self._axis_of['z']]
            self._ctrl.set_servo(ids, [True, True, True])
            self.log.info("PI E-727 reset complete.")
        except PIE727Error as exc:
            self.log.error(f"PI E-727 reset failed: {exc}")

    # ── Scan commands (positions arrive in meters, converted to um) ───────────

    def start_scan(
        self,
        axes:        Tuple[str, ...],
        positions:   Tuple[List[float], ...],
        t_pixel:     float,
        current_pos: Dict[str, float],
    ) -> float:
        if len(axes) != 1:
            raise NotImplementedError(
                "PIE727Scanner implements single-axis (1D fast-axis) line "
                "scans only. 2D scanning is done by the interfuse via "
                "repeated calls to this method + retrigger_line()."
            )

        axis = axes[0]
        axis_num = int(self._axis_of[axis])
        # m -> um, qudi -> controller coordinates
        pos_array_um = [self._mirror_um(axis, p * _UM_PER_M) for p in positions[0]]
        if axis in self._inverted:
            # Mirroring turns the line into a downward ramp, which scan_axis does not support
            # (negative trigger step). Keep the controller ramp upward instead; the pixel
            # order is then reversed relative to the qudi positions, which the interfuse
            # corrects via is_axis_inverted().
            pos_array_um = pos_array_um[::-1]
        disable_threshold = max(self._x_range[1], self._y_range[1], self._z_range[1]) + 1.0

        scan_kwargs = dict(
            axis_num=axis_num, positions=pos_array_um, t_pixel=t_pixel,
            trigger_output_id=self._trigger_output_id,
            wave_generator_rate_hz=self._wave_generator_rate_hz,
            disable_threshold=disable_threshold,
        )
        duration_s = self._ctrl.scan_axis(**scan_kwargs)
        self._ctrl._last_scan_kwargs = scan_kwargs
        self._active_scan_axis = axis

        # Stage ends the scan at the far end of the ramp, not the start.
        self._target_pos[axis] = self._mirror_um(axis, pos_array_um[-1])

        return duration_s

    def retrigger_line(self) -> float:
        duration_s = self._ctrl.retrigger_line()
        if self._active_scan_axis is not None:
            positions_um = self._ctrl._last_scan_kwargs['positions']  # controller coordinates
            self._target_pos[self._active_scan_axis] = self._mirror_um(
                self._active_scan_axis, positions_um[-1])
        return duration_s

    def wait_for_scan_complete(
        self,
        estimated_s:   float,
        stop_event:    Optional[threading.Event] = None,
        poll_interval: float = 0.25,
    ) -> bool:
        coarse_s = max(0.0, estimated_s * 0.80 - poll_interval)
        deadline = time.monotonic() + estimated_s * 3.0 + 10.0

        elapsed, chunk = 0.0, 0.1
        while elapsed < coarse_s:
            if stop_event and stop_event.is_set():
                return False
            time.sleep(chunk)
            elapsed += chunk

        axis_num = int(self._axis_of[self._active_scan_axis]) if self._active_scan_axis else None
        try:
            while time.monotonic() < deadline:
                if stop_event and stop_event.is_set():
                    return False
                if axis_num is not None and not self._ctrl.is_wave_generator_running([axis_num])[0]:
                    break
                time.sleep(poll_interval)
            else:
                self.log.warning("Scan completion timeout exceeded.")
        except Exception as exc:
            self.log.warning(f"Wave generator poll failed ({exc}); time-based fallback.")
            time.sleep(max(0.0, estimated_s - elapsed))

        # Waveform doesn't stop itself -- explicitly stop it now.
        if axis_num is not None:
            try:
                self._ctrl.wave_start_stop([axis_num], [0])
            except Exception as exc:
                self.log.warning(f"Failed to explicitly stop wave generator: {exc}")

        return True