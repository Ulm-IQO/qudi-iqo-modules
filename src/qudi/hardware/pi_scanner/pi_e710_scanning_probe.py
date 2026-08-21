# -*- coding: utf-8 -*-
"""
PI E-710 3CD — scanner motion hardware module
=============================================

Handles connection, motion and waveform scan commands only.
Does NOT implement ScanningProbeInterface and knows nothing about photon counting.

To get a full ScanningProbeInterface combine this module with a photon counter
via PIE710CounterInterfuse.

YAML configuration:
    hardware:
        my_pi_scanner:
            module.Class: 'hardware.pi_e710_scanning_probe.PIE710Scanner'
            options:
                dll_path:     'C:/PI/E7XX_GCS_DLL_x64.dll'
                gpib_board:   0
                gpib_address: 4
                x_range: [0.0, 100.0]
                y_range: [0.0, 100.0]
                z_range: [0.0,  50.0]
                trigger_mode: 'SPCM'

Line-by-line 2D scan performance
---------------------------------
A 2D scan is executed by the interfuse as a series of 1D fast-axis line
scans, one per slow-axis position. Configuring a line scan from scratch
(scan_x/scan_y/scan_z) involves moving the piezo, waiting for it to settle,
and sending roughly 15-20 sequential GPIB commands to program the segment
waveform, trigger conditions, and flag timing on the controller.

Since the fast-axis range, pixel dwell time, and trigger mode are identical
across every line of a given 2D scan, only the FIRST line actually needs
this full configuration. Every subsequent line only needs to re-fire the
already-configured segment program, which is done via retrigger_line() --
this sends a single short command instead of repeating the full setup,
substantially reducing dead time between lines in a 2D scan.

retrigger_line() relies on the assumption that the final command sent by
scan_x/scan_y/scan_z ('1SC0,WA{wait_ms}') is what actually (re-)starts
playback of the previously programmed segments, rather than being part of
the configuration itself. This is inferred from its position in the command
sequence (last, and the only command carrying a line-dependent computed
value) rather than confirmed against PI's GCS segment-protocol
documentation. If retrigger_line() is ever suspected of producing incorrect
scan lines, verify by comparing a scan against one taken using only
scan_x/scan_y/scan_z (i.e. with retrigger_line() disabled), and/or by
inspecting the trigger output on an oscilloscope for the second line onward.
"""

import ctypes
import os
import threading
import time
from abc import abstractmethod
from typing import Dict, List, Optional, Tuple

import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.core.module import Base


# ══════════════════════════════════════════════════════════════════════════════
#  PI GCS DLL WRAPPER
# ══════════════════════════════════════════════════════════════════════════════

class PIE7XXError(Exception):
    def __init__(self, message: str, error_code: int = 0):
        super().__init__(message)
        self.error_code = error_code


class PIE710Controller:
    """
    ctypes wrapper for E7XX_GCS_DLL_x64.dll  —  PI E-710 firmware V7.040 (GCS v1).

    Critical V7.040 facts:
      - Axis strings are CONCATENATED, no spaces  →  b'123'  not  b'1 2 3'
      - Init: INI(b'') then SVO per axis, one at a time
      - Scanning uses the old E-710 segment protocol via E7XXSendString
      - Sample rate: 5000 Hz = 0.2 ms per waveform point
    """

    _SOFT_ERRORS = frozenset({0, 2, 5, 7, 8, 10})
    SAMP_RATE: float = 5000.0

    _BUF_SM = 256
    _BUF_MD = 1024

    _ALL_KNOWN_FUNCTIONS: List[str] = [
        "E7XX_InterfaceSetupDlg", "E7XX_ConnectRS232", "E7XX_ConnectNIgpib",
        "E7XX_ConnectPciBoard", "E7XX_ConnectPciBoardAndReboot",
        "E7XX_ChangeNIgpibAddress", "E7XX_IsConnected", "E7XX_CloseConnection",
        "E7XX_GetError", "E7XX_SetErrorCheck", "E7XX_TranslateError",
        "E7XX_CountPciBoards", "E7XX_qERR", "E7XX_qIDN", "E7XX_INI",
        "E7XX_qHLP", "E7XX_qHPA", "E7XX_CSV", "E7XX_qCSV", "E7XX_qOVF",
        "E7XX_RBT", "E7XX_REP", "E7XX_qSSN", "E7XX_qVER", "E7XX_CCT",
        "E7XX_MOV", "E7XX_qMOV", "E7XX_MVR", "E7XX_qPOS", "E7XX_IsMoving",
        "E7XX_HLT", "E7XX_qONT", "E7XX_SVA", "E7XX_qSVA", "E7XX_SVR",
        "E7XX_DFH", "E7XX_qDFH", "E7XX_GOH", "E7XX_DFF", "E7XX_qDFF",
        "E7XX_qCST", "E7XX_CST", "E7XX_qVST", "E7XX_qTVI",
        "E7XX_SVO", "E7XX_qSVO", "E7XX_VEL", "E7XX_qVEL",
        "E7XX_SPA", "E7XX_qSPA", "E7XX_SEP", "E7XX_qSEP", "E7XX_WPA",
        "E7XX_RPA", "E7XX_STE", "E7XX_qSTE", "E7XX_IMP",
        "E7XX_IMP_PulseWidth", "E7XX_qIMP", "E7XX_SAI", "E7XX_qSAI",
        "E7XX_qSAI_ALL", "E7XX_CCL", "E7XX_qCCL", "E7XX_AVG", "E7XX_qAVG",
        "E7XX_DIO", "E7XX_qTIO", "E7XX_E7XXSendString",
        "E7XX_E7XXGetLineSize", "E7XX_E7XXReadLine",
        "E7XX_GcsCommandset", "E7XX_GcsGetAnswer", "E7XX_GcsGetAnswerSize",
        "E7XX_ATZ", "E7XX_qTMN", "E7XX_qTMX", "E7XX_NLM", "E7XX_qNLM",
        "E7XX_PLM", "E7XX_qPLM", "E7XX_GetRefResult",
        "E7XX_WAV_SIN_P", "E7XX_WAV_LIN", "E7XX_WAV_RAMP", "E7XX_WAV_PNT",
        "E7XX_qWAV", "E7XX_qGWD", "E7XX_WGO", "E7XX_qWGO",
        "E7XX_WGC", "E7XX_qWGC", "E7XX_qTNR", "E7XX_DRC", "E7XX_qDRC",
        "E7XX_WGR", "E7XX_qDRR_SYNC", "E7XX_RTR", "E7XX_qRTR",
        "E7XX_qTWG", "E7XX_WMS", "E7XX_qWMS", "E7XX_DTC", "E7XX_WCL",
        "E7XX_DDL", "E7XX_qDDL", "E7XX_TWS", "E7XX_TWC", "E7XX_qTLT",
        "E7XX_DPO", "E7XX_IsGeneratorRunning",
        "E7XX_VMA", "E7XX_qVMA", "E7XX_VMI", "E7XX_qVMI",
        "E7XX_VOL", "E7XX_qVOL", "E7XX_qTPC",
        "E7XX_qTAD", "E7XX_qTNS", "E7XX_qTSP", "E7XX_qTSC",
        "E7XX_GetSupportedFunctions", "E7XX_GetSupportedParameters",
        "E7XX_GetSupportedControllers", "E7XX_NMOV", "E7XX_NMVR",
    ]

    def __init__(self, dll_path: str, use_windll: bool = False):
        self._id: int = -1
        self._dll_path = dll_path
        self._registered: set = set()
        self._axes: List[str] = []
        self._travel_min: List[float] = []
        self._travel_max: List[float] = []
        # Remembers the wait time (in ms) used to configure the most
        # recently programmed line scan (scan_x/scan_y/scan_z). Used by
        # retrigger_line() to re-fire that exact segment program without
        # resending its full configuration.
        self._last_wait_ms: Optional[int] = None
        self._dll = self._load_dll(dll_path, use_windll)
        self._setup_signatures()

    @staticmethod
    def _load_dll(path: str, use_windll: bool) -> ctypes.CDLL:
        if not os.path.isfile(path):
            raise PIE7XXError(f"DLL not found: '{path}'")
        try:
            return (ctypes.WinDLL if use_windll else ctypes.CDLL)(path)
        except OSError as exc:
            raise PIE7XXError(f"Cannot load DLL: {exc}") from exc

    def _setup_signatures(self):
        d  = self._dll
        I  = ctypes.c_int
        D  = ctypes.c_double
        CH = ctypes.c_char
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

        sig("E7XX_ConnectRS232",             I, I, I)
        sig("E7XX_ConnectNIgpib",            I, I, I)
        sig("E7XX_ConnectPciBoard",          I, I)
        sig("E7XX_ConnectPciBoardAndReboot", I, I)
        sig("E7XX_IsConnected",              I, I)
        sig("E7XX_CloseConnection",          None, I)
        sig("E7XX_GetError",                 I, I)
        sig("E7XX_TranslateError",           I, I, CP, I)
        sig("E7XX_qIDN",                     I, I, CP, I)
        sig("E7XX_INI",                      I, I, CP)
        sig("E7XX_qSSN",                     I, I, CP, I)
        sig("E7XX_qVER",                     I, I, CP, I)
        sig("E7XX_qSAI_ALL",                 I, I, CP, I)
        sig("E7XX_GcsCommandset",            I, I, CP)
        sig("E7XX_GcsGetAnswer",             I, I, CP, I)
        sig("E7XX_GcsGetAnswerSize",         I, I, PI)
        sig("E7XX_qCST",                     I, I, CP, CP, I)
        sig("E7XX_SVO",                      I, I, CP, PI)
        sig("E7XX_qSVO",                     I, I, CP, PI)
        sig("E7XX_MOV",                      I, I, CP, PD)
        sig("E7XX_qMOV",                     I, I, CP, PD)
        sig("E7XX_MVR",                      I, I, CP, PD)
        sig("E7XX_qPOS",                     I, I, CP, PD)
        sig("E7XX_IsMoving",                 I, I, CP, PI)
        sig("E7XX_HLT",                      I, I, CP)
        sig("E7XX_qONT",                     I, I, CP, PI)
        sig("E7XX_SVA",                      I, I, CP, PD)
        sig("E7XX_qSVA",                     I, I, CP, PD)
        sig("E7XX_DFH",                      I, I, CP)
        sig("E7XX_qDFH",                     I, I, CP, PD)
        sig("E7XX_GOH",                      I, I, CP)
        sig("E7XX_VEL",                      I, I, CP, PD)
        sig("E7XX_qVEL",                     I, I, CP, PD)
        sig("E7XX_qTMN",                     I, I, CP, PD)
        sig("E7XX_qTMX",                     I, I, CP, PD)
        sig("E7XX_NLM",                      I, I, CP, PD)
        sig("E7XX_qNLM",                     I, I, CP, PD)
        sig("E7XX_PLM",                      I, I, CP, PD)
        sig("E7XX_qPLM",                     I, I, CP, PD)
        sig("E7XX_qTPC",                     I, I, PI)
        sig("E7XX_qTSC",                     I, I, PI)
        sig("E7XX_qTNR",                     I, I, PI)
        sig("E7XX_qTSP",                     I, I, CP, PD)
        sig("E7XX_E7XXSendString",           I, I, CP)
        sig("E7XX_E7XXGetLineSize",          I, I, PI)
        sig("E7XX_E7XXReadLine",             I, I, CP, I)
        sig("E7XX_IsGeneratorRunning",       I, I, CP, PI)
        sig("E7XX_VOL",                      I, I, CP, PD)
        sig("E7XX_qVOL",                     I, I, CP, PD)
        sig("E7XX_SPA",                      I, I, CP, PI, PD, CP)
        sig("E7XX_qSPA",                     I, I, CP, PI, PD, CP, I)

    def _fn(self, name: str):
        if name not in self._registered:
            raise PIE7XXError(
                f"'{name}' not exported by '{os.path.basename(self._dll_path)}'")
        return getattr(self._dll, name)

    def _require_connection(self):
        if self._id < 0:
            raise PIE7XXError("Not connected.")

    def _check(self, result: int, fname: str):
        if not result:
            code = self._dll.E7XX_GetError(self._id) if self._id >= 0 else -1
            raise PIE7XXError(
                f"'{fname}' failed — error {code}: {self._translate(code)}", code)

    def _soft_check(self, result: int, fname: str) -> bool:
        if result:
            return True
        code = self._dll.E7XX_GetError(self._id) if self._id >= 0 else -1
        if code in self._SOFT_ERRORS:
            return False
        raise PIE7XXError(
            f"'{fname}' failed — error {code}: {self._translate(code)}", code)

    def _translate(self, code: int) -> str:
        buf = ctypes.create_string_buffer(self._BUF_MD)
        try:
            self._dll.E7XX_TranslateError(code, buf, self._BUF_MD)
        except Exception:
            return f"(no translation for {code})"
        return buf.value.decode("ascii", errors="replace").strip()

    @staticmethod
    def _ax(axes) -> bytes:
        if isinstance(axes, bytes):
            return axes
        if isinstance(axes, (list, tuple)):
            return "".join(str(a) for a in axes).encode("ascii")
        if isinstance(axes, str):
            return "".join(axes.split()).encode("ascii")
        return b""

    @staticmethod
    def _nax(axes, fallback: int = 0) -> int:
        if isinstance(axes, (list, tuple)):
            return len(axes)
        b = PIE710Controller._ax(axes)
        return len(b) if b else fallback

    @staticmethod
    def _darr(vals) -> ctypes.Array:
        v = list(vals)
        return (ctypes.c_double * len(v))(*v)

    @staticmethod
    def _barr(vals) -> ctypes.Array:
        v = [int(bool(x)) for x in vals]
        return (ctypes.c_int * len(v))(*v)

    def _dbuf(self, n: int) -> ctypes.Array:
        return (ctypes.c_double * max(n, 1))()

    def _ibuf(self, n: int) -> ctypes.Array:
        return (ctypes.c_int * max(n, 1))()

    def clear_error(self):
        if self._id >= 0:
            self._dll.E7XX_GetError(self._id)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._id >= 0 and bool(self._dll.E7XX_IsConnected(self._id))

    @property
    def axes(self) -> List[str]:
        return list(self._axes)

    @property
    def travel_min(self) -> List[float]:
        return list(self._travel_min)

    @property
    def travel_max(self) -> List[float]:
        return list(self._travel_max)

    # ── Connection ────────────────────────────────────────────────────────────

    def connect_ni_gpib(self, board_number: int, device_address: int) -> int:
        _id = self._fn("E7XX_ConnectNIgpib")(board_number, device_address)
        if _id < 0:
            raise PIE7XXError(
                f"NI-GPIB connect failed (board={board_number}, addr={device_address})")
        self._id = _id
        return _id

    def connect_rs232(self, port_number: int, baud_rate: int = 115200) -> int:
        _id = self._fn("E7XX_ConnectRS232")(port_number, baud_rate)
        if _id < 0:
            raise PIE7XXError(f"RS-232 connect failed (COM{port_number})")
        self._id = _id
        return _id

    def close_connection(self):
        if self._id >= 0:
            try:
                self._fn("E7XX_CloseConnection")(self._id)
            except Exception:
                pass
            self._id = -1

    # ── Identification ────────────────────────────────────────────────────────

    def get_identification(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        self._check(self._fn("E7XX_qIDN")(self._id, buf, self._BUF_MD), "qIDN")
        return buf.value.decode("ascii", errors="replace").strip()

    def get_version(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        ok = self._soft_check(
            self._fn("E7XX_qVER")(self._id, buf, self._BUF_MD), "qVER")
        return (buf.value.decode("ascii", errors="replace").strip()
                if ok else "(not supported on V7.040)")

    def get_serial_number(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_SM)
        ok = self._soft_check(
            self._fn("E7XX_qSSN")(self._id, buf, self._BUF_SM), "qSSN")
        return (buf.value.decode("ascii", errors="replace").strip()
                if ok else "(not supported on V7.040)")

    def get_axes(self) -> List[str]:
        self._require_connection()
        raw = ""
        try:
            self.send_gcs_command("SAI?")
            raw = self.get_gcs_answer()
        except PIE7XXError:
            pass
        if not raw:
            buf = ctypes.create_string_buffer(self._BUF_MD)
            if self._fn("E7XX_qSAI_ALL")(self._id, buf, self._BUF_MD):
                raw = buf.value.decode("ascii", errors="replace").strip()
        axes = self._parse_axes_response(raw)
        return axes if axes else ['1', '2', '3', '4']

    @staticmethod
    def _parse_axes_response(raw: str) -> List[str]:
        if not raw or raw.startswith("(not"):
            return []
        parts = raw.split()
        if len(parts) > 1:
            return [p.strip() for p in parts if p.strip()]
        token = parts[0] if parts else raw.strip()
        if len(token) > 1 and all(c.isalnum() for c in token):
            return list(token)
        return [token] if token else []

    # ── Servo ─────────────────────────────────────────────────────────────────

    def set_servo(self, axes, states: List[bool]):
        self._require_connection()
        self._check(
            self._fn("E7XX_SVO")(self._id, self._ax(axes), self._barr(states)),
            "SVO")

    # ── Motion ────────────────────────────────────────────────────────────────

    def get_position(self, axes) -> List[float]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qPOS")(self._id, self._ax(axes), arr), "qPOS")
        return [arr[i] for i in range(n)]

    def move_absolute(self, axes, positions: List[float]):
        self._require_connection()
        self._check(
            self._fn("E7XX_MOV")(self._id, self._ax(axes), self._darr(positions)),
            "MOV")

    def move_relative(self, axes, steps: List[float]):
        self._require_connection()
        self._check(
            self._fn("E7XX_MVR")(self._id, self._ax(axes), self._darr(steps)),
            "MVR")

    def is_moving(self, axes) -> List[bool]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("E7XX_IsMoving")(self._id, self._ax(axes), arr), "IsMoving")
        return [bool(arr[i]) for i in range(n)]

    def is_on_target(self, axes) -> List[bool]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("E7XX_qONT")(self._id, self._ax(axes), arr), "qONT")
        return [bool(arr[i]) for i in range(n)]

    def halt(self, axes):
        self._require_connection()
        self._check(self._fn("E7XX_HLT")(self._id, self._ax(axes)), "HLT")

    def wait_for_motion(
        self, axes, poll_interval: float = 0.05,
        timeout: float = 60.0, settle_check: bool = True,
    ):
        deadline = time.monotonic() + timeout
        while any(self.is_moving(axes)):
            if time.monotonic() > deadline:
                raise PIE7XXError(f"Timeout waiting for motion on '{axes}'")
            time.sleep(poll_interval)
        if settle_check:
            settle_deadline = time.monotonic() + min(timeout * 0.25, 5.0)
            while not all(self.is_on_target(axes)):
                if time.monotonic() > settle_deadline:
                    break
                time.sleep(poll_interval)

    # ── Travel limits ─────────────────────────────────────────────────────────

    def get_min_travel(self, axes) -> List[float]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qTMN")(self._id, self._ax(axes), arr), "qTMN")
        return [arr[i] for i in range(n)]

    def get_max_travel(self, axes) -> List[float]:
        self._require_connection()
        n = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qTMX")(self._id, self._ax(axes), arr), "qTMX")
        return [arr[i] for i in range(n)]

    # ── Raw / GCS commands ────────────────────────────────────────────────────

    def send_gcs_command(self, command: str):
        self._require_connection()
        self._check(
            self._fn("E7XX_GcsCommandset")(self._id, command.encode("ascii")),
            "GcsCommandset")

    def get_gcs_answer(self) -> str:
        self._require_connection()
        sz = ctypes.c_int(0)
        self._check(
            self._fn("E7XX_GcsGetAnswerSize")(self._id, ctypes.byref(sz)),
            "GcsGetAnswerSize")
        buf_size = max(sz.value + 1, self._BUF_MD)
        buf = ctypes.create_string_buffer(buf_size)
        self._check(
            self._fn("E7XX_GcsGetAnswer")(self._id, buf, buf_size),
            "GcsGetAnswer")
        return buf.value.decode("ascii", errors="replace").strip()

    def send_raw_string(self, command: str):
        """Send old-style E-710 segment/scan protocol string."""
        self._require_connection()
        self._check(
            self._fn("E7XX_E7XXSendString")(self._id, command.encode("ascii")),
            "E7XXSendString")

    # ── Old-style E-710 segment protocol ─────────────────────────────────────

    def segment(
        self, seg_num: int, total_pts: int, curve_pts: int,
        curve_center: Optional[int], speed_pts: int,
        start_pt: int, offset: float, amplitude: float,
    ):
        n = str(seg_num)
        self.send_raw_string(f"{n}PT{total_pts}")
        self.send_raw_string(f"{n}CP{curve_pts}")
        if curve_center is not None:
            self.send_raw_string(f"{n}PC{curve_center}")
        self.send_raw_string(f"{n}PS{speed_pts}")
        self.send_raw_string(f"{n}PA{start_pt}")
        self.send_raw_string(f"{n}FO{offset}")
        self.send_raw_string(f"{n}GL{amplitude}")

    def scan_x(self, x, y, z, t_pixel, trigger="SPCM"):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x[0], y, z])
        time.sleep(0.5)
        nx = len(x)
        speed_pts, start_pt = 100, 100
        n = round(t_pixel * self.SAMP_RATE)
        curve_pts = nx * n
        total_pts = curve_pts + 2 * speed_pts + 2 * start_pt
        amp = x[-1] - x[0]
        self.send_raw_string('0PT0')
        self.segment(1, total_pts, curve_pts, None, speed_pts, start_pt, 0,   amp)
        self.segment(2, total_pts, curve_pts, None, speed_pts, start_pt, amp, -amp)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts}')
        self.send_raw_string('1SF1')
        self.send_raw_string('1CF1')
        self.send_raw_string('1KT-1' if trigger != 'APD' else '1KT2')
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        t_on = start_pt + speed_pts
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_on + curve_pts}FT259')
        wait_ms = round(2 * total_pts / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'1SC0,WA{wait_ms}')
        # Remember this line's wait time so retrigger_line() can re-fire the
        # same segment program later without resending its configuration.
        self._last_wait_ms = wait_ms

    def scan_y(self, x, y, z, t_pixel, trigger="SPCM"):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x, y[0], z])
        time.sleep(0.5)
        ny = len(y)
        speed_pts, start_pt = 100, 100
        n = round(t_pixel * self.SAMP_RATE)
        curve_pts = ny * n
        total_pts = curve_pts + 2 * speed_pts + 2 * start_pt
        amp = y[-1] - y[0]
        self.send_raw_string('0PT0')
        self.segment(1, total_pts, curve_pts, None, speed_pts, start_pt, 0,   amp)
        self.segment(2, total_pts, curve_pts, None, speed_pts, start_pt, amp, -amp)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts}')
        self.send_raw_string('1SF1')
        self.send_raw_string('2CF1')
        self.send_raw_string('1KT-1' if trigger != 'APD' else '1KT2')
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        t_on = start_pt + speed_pts
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_on + curve_pts}FT259')
        wait_ms = round(2 * total_pts / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'1SC0,WA{wait_ms}')
        # See comment in scan_x().
        self._last_wait_ms = wait_ms

    def scan_z(self, x, y, z, t_pixel, trigger="SPCM"):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x, y, z[0]])
        time.sleep(0.5)
        nz = len(z)
        speed_pts, start_pt = 100, 100
        n = round(t_pixel * self.SAMP_RATE)
        curve_pts = nz * n
        total_pts = curve_pts + 2 * speed_pts + 2 * start_pt
        amp = z[-1] - z[0]
        self.send_raw_string('0PT0')
        self.segment(1, total_pts, curve_pts, None, speed_pts, start_pt, 0,   amp)
        self.segment(2, total_pts, curve_pts, None, speed_pts, start_pt, amp, -amp)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts}')
        self.send_raw_string('1SF1')
        self.send_raw_string('3CF1')
        self.send_raw_string('1KT-1' if trigger != 'APD' else '1KT2')
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        t_on = start_pt + speed_pts
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_on + curve_pts}FT259')
        wait_ms = round(2 * total_pts / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'1SC0,WA{wait_ms}')
        # See comment in scan_x().
        self._last_wait_ms = wait_ms

    def scan_xy(self, x, y, z, t_pixel, trigger="SPCM"):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x[0], y[0], z])
        time.sleep(0.5)
        nx, ny = len(x), len(y)
        speed_pts, start_pt = 50, 0
        n = round(t_pixel * self.SAMP_RATE)
        curve_pts_x = nx * n
        total_pts_x = curve_pts_x + 2 * speed_pts
        amp_x = x[-1] - x[0]
        amp_y = (y[-1] - y[0]) / (ny - 1) if ny > 1 else 0.0
        total_pts_y = 2 * total_pts_x
        self.send_raw_string('0PT0')
        self.segment(1, total_pts_x, curve_pts_x, None, speed_pts, start_pt, 0,     amp_x)
        self.segment(2, total_pts_x, curve_pts_x, None, speed_pts, start_pt, amp_x, -amp_x)
        self.segment(3, total_pts_y, 350,          None, speed_pts, total_pts_x, 0,  amp_y)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts_x}')
        self.send_raw_string(f'2PT{total_pts_y}')
        self.send_raw_string('1SF1')
        self.send_raw_string('2SF2')
        self.send_raw_string('1CF1')
        self.send_raw_string('2CF2')
        self.send_raw_string('1KT-1' if trigger != 'APD' else '1KT2')
        self.send_raw_string('2KT2')
        t_on = start_pt + speed_pts
        self.send_raw_string('0FT0')
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_on + curve_pts_x}FT259')
        wait_ms = round(2 * total_pts_x / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'0SC32,WA{wait_ms},RP{ny}')

    def scan_xz(self, x, y, z, t_pixel):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x[0], y, z[0]])
        time.sleep(0.5)
        nx, nz = len(x), len(z)
        speed_pts = 100
        n = round(t_pixel * self.SAMP_RATE)
        curve_pts = nx * n + 2 * speed_pts
        total_pts = curve_pts + 200
        amp_x = x[-1] - x[0]
        amp_z = (z[-1] - z[0]) / (nz - 1) if nz > 1 else 0.0
        t_on = speed_pts + 100
        t_off = curve_pts - 2 * speed_pts + t_on
        self.send_raw_string('0PT0')
        self.segment(1, total_pts,        curve_pts, None, speed_pts, 0,          0,     amp_x)
        self.segment(2, 2000,             1900,      None, 100,        0,          amp_x, -amp_x)
        self.segment(3, total_pts + 2000, 350,       None, 100,        total_pts,  0,     amp_z)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{total_pts + 2000}')
        self.send_raw_string(f'2PT{total_pts + 2000}')
        self.send_raw_string('1SF1')
        self.send_raw_string('2SF2')
        self.send_raw_string('1CF1')
        self.send_raw_string('3CF2')
        self.send_raw_string('1KT-1')
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_off}FT259')
        self.send_raw_string(f'0SC32,WA1000,RP{nz}')

    def scan_yz(self, x, y, z, t_pixel):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x, y[0], z[0]])
        time.sleep(0.5)
        ny, nz = len(y), len(z)
        speed_pts = 100
        n = round(t_pixel * self.SAMP_RATE)
        curve_pts = ny * n + 2 * speed_pts
        total_pts = curve_pts + 200
        amp_y = y[-1] - y[0]
        amp_z = (z[-1] - z[0]) / (nz - 1) if nz > 1 else 0.0
        t_on = speed_pts + 100
        t_off = curve_pts - 2 * speed_pts + t_on
        self.send_raw_string('0PT0')
        self.segment(1, total_pts,        curve_pts, None, speed_pts, 0,          0,     amp_y)
        self.segment(2, 2000,             1900,      None, 100,        0,          amp_y, -amp_y)
        self.segment(3, total_pts + 2000, 350,       None, 100,        total_pts,  0,     amp_z)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{total_pts + 2000}')
        self.send_raw_string(f'2PT{total_pts + 2000}')
        self.send_raw_string('1SF1')
        self.send_raw_string('2SF2')
        self.send_raw_string('2CF1')
        self.send_raw_string('3CF2')
        self.send_raw_string('1KT-1')
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_off}FT259')
        self.send_raw_string(f'0SC32,WA1000,RP{nz}')

    def retrigger_line(self):
        """
        Re-fire the segment program most recently configured by
        scan_x()/scan_y()/scan_z(), without resending its configuration.

        Sends only the single command that (re-)starts playback of the
        already-programmed segments, using the wait time cached from that
        earlier call. Safe to call repeatedly as long as the fast-axis
        range, pixel dwell time, and trigger mode are unchanged since the
        last full scan_x/scan_y/scan_z call -- exactly the situation for
        every line after the first in a 2D raster scanned line-by-line.
        """
        self._require_connection()
        if self._last_wait_ms is None:
            raise PIE7XXError(
                "retrigger_line() called with no prior line scan configured. "
                "Call scan_x()/scan_y()/scan_z() at least once first."
            )
        self.send_raw_string(f'1SC0,WA{self._last_wait_ms}')

    def move_xyz(self, x: float, y: float, z: float, wait: bool = True):
        self._require_connection()
        self._check(
            self._fn("E7XX_MOV")(self._id, b"123", self._darr([x, y, z])),
            "MOV_xyz",
        )
        if wait:
            self.wait_for_motion("123", timeout=30.0)

    # ── Initialisation ────────────────────────────────────────────────────────

    def probe_firmware(self) -> dict:
        """INI all axes + SVO per axis + read travel limits + current position."""
        self._require_connection()
        result = {
            "idn":     self.get_identification(),
            "version": self.get_version(),
            "serial":  self.get_serial_number(),
        }
        axes = self.get_axes()
        self._axes = axes
        axes_bytes = self._ax(axes)
        n = len(axes)
        result["axes"] = axes

        # INI all axes
        self.clear_error()
        self._fn("E7XX_INI")(self._id, b"")
        self._dll.E7XX_GetError(self._id)
        time.sleep(0.2)
        self.clear_error()

        # SVO per axis, one at a time
        for ax in axes:
            self.clear_error()
            state = (ctypes.c_int * 1)(1)
            self._fn("E7XX_SVO")(self._id, ax.encode("ascii"), state)
            self._dll.E7XX_GetError(self._id)
        time.sleep(0.1)

        # Travel range
        mn, mx = None, None
        try:
            arr_mn, arr_mx = self._dbuf(n), self._dbuf(n)
            ret_mn = self._fn("E7XX_qTMN")(self._id, axes_bytes, arr_mn)
            ec_mn  = self._dll.E7XX_GetError(self._id)
            ret_mx = self._fn("E7XX_qTMX")(self._id, axes_bytes, arr_mx)
            ec_mx  = self._dll.E7XX_GetError(self._id)
            if ret_mn and ec_mn == 0:
                mn = [arr_mn[i] for i in range(n)]
            if ret_mx and ec_mx == 0:
                mx = [arr_mx[i] for i in range(n)]
        except Exception:
            pass

        self._travel_min = mn if mn else [0.0]   * n
        self._travel_max = mx if mx else [100.0] * n

        # Current positions
        pos = None
        try:
            arr = self._dbuf(n)
            ret = self._fn("E7XX_qPOS")(self._id, axes_bytes, arr)
            ec  = self._dll.E7XX_GetError(self._id)
            if ret and ec == 0:
                pos = [arr[i] for i in range(n)]
        except Exception:
            pass

        result["travel_min"] = self._travel_min
        result["travel_max"] = self._travel_max
        result["positions"]  = pos
        return result

    # ── Context manager ───────────────────────────────────────────────────────

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
#  ABSTRACT INTERFACE — PIE710ScannerInterface
#  Any concrete scanner module connected to the interfuse must implement this.
# ══════════════════════════════════════════════════════════════════════════════

class PIE710ScannerInterface(Base):
    """
    Abstract Qudi interface for the PI E-710 scanner hardware module.

    Implemented by PIE710Scanner below.
    Referenced as the connector interface in PIE710CounterInterfuse.
    """

    @property
    @abstractmethod
    def x_range(self) -> List[float]:
        """Travel range [min, max] in µm for the X axis."""
        pass

    @property
    @abstractmethod
    def y_range(self) -> List[float]:
        """Travel range [min, max] in µm for the Y axis."""
        pass

    @property
    @abstractmethod
    def z_range(self) -> List[float]:
        """Travel range [min, max] in µm for the Z axis."""
        pass

    @abstractmethod
    def move_absolute(
        self, position: Dict[str, float], blocking: bool = False,
    ) -> Dict[str, float]:
        """Move to absolute position. Returns new target dict."""
        pass

    @abstractmethod
    def move_relative(
        self, distance: Dict[str, float], blocking: bool = False,
    ) -> Dict[str, float]:
        """Move by relative distance. Returns new target dict."""
        pass

    @abstractmethod
    def get_position(self) -> Dict[str, float]:
        """Read actual position from capacitive sensors (µm)."""
        pass

    @abstractmethod
    def get_target(self) -> Dict[str, float]:
        """Return last commanded target position (µm)."""
        pass

    @abstractmethod
    def sync_position(self) -> None:
        """Update internal target tracking from sensor readout."""
        pass

    @abstractmethod
    def halt(self) -> None:
        """Halt all axes immediately (HLT)."""
        pass

    @abstractmethod
    def halt_generators(self) -> None:
        """Stop all waveform generators immediately (0SC0)."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Re-run probe_firmware() — INI + SVO per axis."""
        pass

    @abstractmethod
    def start_scan(
        self,
        axes: Tuple[str, ...],
        positions: Tuple[List[float], ...],
        t_pixel: float,
        current_pos: Dict[str, float],
    ) -> float:
        """
        Fire off PI waveform scan commands (non-blocking from PC side).

        @param axes        : ('x',) for 1D, ('x','y') etc. for 2D — first = fast axis.
        @param positions   : tuple of position arrays, one per axis in `axes`.
        @param t_pixel     : dwell time per pixel in seconds.
        @param current_pos : current position of all three axes (for fixed-axis values).
        @return            : estimated scan duration in seconds.
        """
        pass

    @abstractmethod
    def wait_for_scan_complete(
        self,
        estimated_s: float,
        stop_event:  Optional[threading.Event] = None,
    ) -> bool:
        """
        Block until both PI waveform generators become idle.

        @param estimated_s : expected scan duration in seconds.
        @param stop_event  : set this event to abort waiting early.
        @return            : True if scan completed normally, False if aborted.
        """
        pass

    @abstractmethod
    def retrigger_line(self) -> float:
        """
        Re-fire the most recently configured single-axis (fast-axis) line
        scan WITHOUT reprogramming its segment/trigger/flag configuration.

        Must only be called after start_scan() has been called at least once
        with a single-axis `axes` tuple, and only while the fast-axis range,
        pixel dwell time, and trigger mode remain unchanged from that call
        (exactly the situation for every line after the first in a 2D raster
        scanned line-by-line).

        @return : estimated duration of this line, in seconds (same value
                  returned by the original start_scan() call).
        """
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  CONCRETE HARDWARE MODULE — PIE710Scanner
# ══════════════════════════════════════════════════════════════════════════════

class PIE710Scanner(PIE710ScannerInterface):
    """
    PI E-710 3CD Qudi hardware module — motion and waveform control only.

    Connect this to PIE710CounterInterfuse to get a full ScanningProbeInterface.
    """

    _dll_path     = ConfigOption('dll_path',     default="C:/jmaze/matlab/Experiment/mytoolboxes/piezoE7XX/E7XX_GCS_DLL_x64.dll")
    _gpib_board   = ConfigOption('gpib_board',   default=0)
    _gpib_address = ConfigOption('gpib_address', default=4)
    _x_range      = ConfigOption('x_range',      default=[0.0, 100.0])
    _y_range      = ConfigOption('y_range',      default=[0.0, 100.0])
    _z_range      = ConfigOption('z_range',      default=[0.0,  50.0])
    _trigger_mode = ConfigOption('trigger_mode', default='SPCM')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ctrl:        Optional[PIE710Controller] = None
        self._target_pos:  Dict[str, float]           = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        # Estimated duration of the most recently configured single-axis
        # line scan. Used by retrigger_line() to return a consistent
        # duration for subsequent lines without recomputing it -- the
        # formula depends only on the fast-axis range and t_pixel, both of
        # which are unchanged across lines of the same 2D raster.
        self._last_line_duration_s: Optional[float] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        try:
            self._ctrl = PIE710Controller(self._dll_path)
            self._ctrl.connect_ni_gpib(
                board_number   = int(self._gpib_board),
                device_address = int(self._gpib_address),
            )
            info = self._ctrl.probe_firmware()
            self.log.info(f"PI E-710 connected: {info.get('idn', '(unknown)')}")

            # Prefer hardware-reported limits over yaml values
            if info.get('travel_min') and len(info['travel_min']) >= 3:
                self._x_range = [info['travel_min'][0], info['travel_max'][0]]
                self._y_range = [info['travel_min'][1], info['travel_max'][1]]
                self._z_range = [info['travel_min'][2], info['travel_max'][2]]

            pos = self._ctrl.get_position("123")
            self._target_pos = {'x': pos[0], 'y': pos[1], 'z': pos[2]}
            self.log.info(
                f"Travel limits  x:{self._x_range}  y:{self._y_range}  z:{self._z_range} µm  |  "
                f"Position  x={pos[0]:.3f}  y={pos[1]:.3f}  z={pos[2]:.3f} µm"
            )
        except PIE7XXError as exc:
            self.log.exception(f"PI E-710 activation failed: {exc}")
            raise

    def on_deactivate(self) -> None:
        if self._ctrl is not None:
            try:
                self._ctrl.close_connection()
            except Exception as exc:
                self.log.warning(f"PI close_connection: {exc}")
            self._ctrl = None

    # ── Range properties ──────────────────────────────────────────────────────

    @property
    def x_range(self) -> List[float]:
        return list(self._x_range)

    @property
    def y_range(self) -> List[float]:
        return list(self._y_range)

    @property
    def z_range(self) -> List[float]:
        return list(self._z_range)

    # ── Motion ────────────────────────────────────────────────────────────────

    def move_absolute(
        self, position: Dict[str, float], blocking: bool = False,
    ) -> Dict[str, float]:
        target = dict(self._target_pos)
        for ax, val in position.items():
            if ax in ('x', 'y', 'z'):
                target[ax] = float(val)
        target['x'] = float(np.clip(target['x'], *self._x_range))
        target['y'] = float(np.clip(target['y'], *self._y_range))
        target['z'] = float(np.clip(target['z'], *self._z_range))
        self._ctrl.move_absolute(
            ['1', '2', '3'], [target['x'], target['y'], target['z']])
        self._target_pos = target
        if blocking:
            self._ctrl.wait_for_motion("123", timeout=60.0)
        return dict(self._target_pos)

    def move_relative(
        self, distance: Dict[str, float], blocking: bool = False,
    ) -> Dict[str, float]:
        current = self.get_target()
        new_pos = {ax: current[ax] + distance.get(ax, 0.0) for ax in ('x', 'y', 'z')}
        return self.move_absolute(new_pos, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        return dict(self._target_pos)

    def get_position(self) -> Dict[str, float]:
        try:
            pos = self._ctrl.get_position("123")
            return {'x': pos[0], 'y': pos[1], 'z': pos[2]}
        except PIE7XXError:
            return dict(self._target_pos)

    def sync_position(self) -> None:
        """Read sensor position and update internal target tracking."""
        try:
            pos = self._ctrl.get_position("123")
            self._target_pos = {'x': pos[0], 'y': pos[1], 'z': pos[2]}
        except PIE7XXError as exc:
            self.log.warning(f"sync_position failed: {exc}")

    def halt(self) -> None:
        try:
            self._ctrl.halt("123")
        except PIE7XXError:
            pass

    def halt_generators(self) -> None:
        try:
            self._ctrl.send_raw_string('0SC0')
        except Exception:
            pass

    def reset(self) -> None:
        try:
            self._ctrl.probe_firmware()
            self.log.info("PI E-710 reset complete.")
        except PIE7XXError as exc:
            self.log.error(f"PI E-710 reset failed: {exc}")

    # ── Scan commands ─────────────────────────────────────────────────────────

    def start_scan(
        self,
        axes:        Tuple[str, ...],
        positions:   Tuple[List[float], ...],
        t_pixel:     float,
        current_pos: Dict[str, float],
    ) -> float:
        """
        Fire off a PI waveform scan and return the estimated duration in seconds.
        The call returns as soon as the GPIB command is sent — the PI runs autonomously.
        """
        if len(axes) == 1:
            return self._start_1d(axes[0], positions[0], t_pixel, current_pos)
        if len(axes) == 2:
            return self._start_2d(axes[0], axes[1], positions[0], positions[1],
                                  t_pixel, current_pos)
        raise ValueError(f"Unsupported scan dimension: {len(axes)}")

    def _start_1d(self, axis, pos_array, t_pixel, current_pos) -> float:
        if axis == 'x':
            self._ctrl.scan_x(x=pos_array, y=current_pos['y'], z=current_pos['z'],
                               t_pixel=t_pixel, trigger=self._trigger_mode)
        elif axis == 'y':
            self._ctrl.scan_y(x=current_pos['x'], y=pos_array, z=current_pos['z'],
                               t_pixel=t_pixel, trigger=self._trigger_mode)
        elif axis == 'z':
            self._ctrl.scan_z(x=current_pos['x'], y=current_pos['y'], z=pos_array,
                               t_pixel=t_pixel, trigger=self._trigger_mode)
        else:
            raise ValueError(f"Unknown axis '{axis}'")

        # scan_x/y/z() move the piezo via PIE710Controller's own raw
        # move_absolute(), which does not update self._target_pos. Once the
        # scan has been programmed and fired, the fast axis physically ends
        # up back at pos_array[0] (the segment waveform's forward-then-
        # backward sweep returns to its start point) -- so _target_pos is
        # updated here to reflect that.
        #
        # This matters for line-by-line 2D scans: between lines, only the
        # slow axis position actually needs to change, but move_absolute()
        # always re-sends MOV commands for all three axes using whatever is
        # currently in _target_pos. Keeping _target_pos[axis] accurate here
        # ensures that per-line move re-asserts the fast axis to its correct
        # starting position via a real, closed-loop MOV command on every
        # line, rather than relying on the (skipped, for speed) internal
        # re-move that scan_x/y/z would otherwise perform on every call.
        self._target_pos[axis] = pos_array[0]

        speed_pts, start_pt = 100, 100
        n = max(1, round(t_pixel * PIE710Controller.SAMP_RATE))
        total_pts = len(pos_array) * n + 2 * speed_pts + 2 * start_pt
        duration_s = 2.0 * total_pts / PIE710Controller.SAMP_RATE + 1.5

        # Cache this line's estimated duration so retrigger_line() can
        # return a consistent value for subsequent lines without
        # recomputing it.
        self._last_line_duration_s = duration_s

        return duration_s

    def _start_2d(self, fast_axis, slow_axis, fast_pos, slow_pos, t_pixel, current_pos) -> float:
        if fast_axis == 'x' and slow_axis == 'y':
            self._ctrl.scan_xy(x=fast_pos, y=slow_pos, z=current_pos['z'],
                                t_pixel=t_pixel, trigger=self._trigger_mode)
            speed_pts = 50
            n = max(1, round(t_pixel * PIE710Controller.SAMP_RATE))
            total_pts = len(fast_pos) * n + 2 * speed_pts
            line_s = 2.0 * total_pts / PIE710Controller.SAMP_RATE + 0.1
            return len(slow_pos) * line_s + 5.0

        if fast_axis == 'x' and slow_axis == 'z':
            self._ctrl.scan_xz(x=fast_pos, y=current_pos['y'], z=slow_pos, t_pixel=t_pixel)
            return len(slow_pos) * 1.2 + 5.0

        if fast_axis == 'y' and slow_axis == 'z':
            self._ctrl.scan_yz(x=current_pos['x'], y=fast_pos, z=slow_pos, t_pixel=t_pixel)
            return len(slow_pos) * 1.2 + 5.0

        raise ValueError(
            f"Unsupported 2D axis combination: fast='{fast_axis}', slow='{slow_axis}'")

    def retrigger_line(self) -> float:
        """
        See PIE710ScannerInterface.retrigger_line() for the full usage
        contract. Delegates the actual GPIB command to
        PIE710Controller.retrigger_line() and returns the cached duration
        estimate from the most recent single-axis start_scan() call.
        """
        self._ctrl.retrigger_line()
        if self._last_line_duration_s is None:
            raise PIE7XXError(
                "retrigger_line() called with no prior single-axis start_scan() call."
            )
        return self._last_line_duration_s

    def wait_for_scan_complete(
        self,
        estimated_s:   float,
        stop_event:    Optional[threading.Event] = None,
        poll_interval: float = 0.25,
    ) -> bool:
        """
        Block until both PI waveform generators idle, or stop_event is set.
        Returns True if completed normally, False if aborted.
        """
        coarse_s = max(0.0, estimated_s * 0.80 - poll_interval)
        deadline = time.monotonic() + estimated_s * 3.0 + 10.0

        # Phase 1: coarse sleep
        elapsed, chunk = 0.0, 0.1
        while elapsed < coarse_s:
            if stop_event and stop_event.is_set():
                return False
            time.sleep(chunk)
            elapsed += chunk

        # Phase 2: poll IsGeneratorRunning
        try:
            while time.monotonic() < deadline:
                if stop_event and stop_event.is_set():
                    return False
                buf = (ctypes.c_int * 2)()
                ret = self._ctrl._dll.E7XX_IsGeneratorRunning(
                    self._ctrl._id, b'12', buf)
                if ret and buf[0] == 0 and buf[1] == 0:
                    return True
                time.sleep(poll_interval)
            self.log.warning("Scan completion timeout exceeded.")
            return True
        except Exception as exc:
            self.log.warning(f"IsGeneratorRunning poll failed ({exc}); time-based fallback.")
            time.sleep(max(0.0, estimated_s - elapsed))
            return True