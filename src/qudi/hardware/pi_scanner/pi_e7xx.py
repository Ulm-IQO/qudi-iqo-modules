# -*- coding: utf-8 -*-
"""
PI E-710 3CD — Qudi ScanningProbeInterface  (standalone hardware module)
=========================================================================

Single-file Qudi hardware module for the Physik Instrumente E-710 3CD
piezo controller.  The PI GCS DLL wrapper (PIE710Controller) is embedded
directly in this file — no separate module needed.

CONFIGURATION (qudi config yaml)
─────────────────────────────────
hardware:
    pi_e710_scanner:
        module.Class: 'pi_e710_scanning_probe.PIE710ScanningProbe'
        options:
            dll_path:     'C:/PI/E7XX_GCS_DLL_x64.dll'
            gpib_board:   0
            gpib_address: 4
            x_range: [0.0, 100.0]
            y_range: [0.0, 100.0]
            z_range: [0.0,  50.0]
            trigger_mode: 'SPCM'

PHOTON COUNTING — PLACEHOLDER
──────────────────────────────
Search for "PHOTON COUNTING" in this file.
Three methods need to be filled in with your counter hardware code:
    _arm_photon_counter(n_pixels, t_pixel)
    _read_photon_counts(n_pixels)
    _stop_photon_counter()

Two physical BNC cables are required:
    PI E-710  Trigger OUT  →  Counter  Gate/Clock IN
    APD/SPCM  Signal OUT   →  Counter  Count Source IN
"""

import ctypes
import os
import threading
import time
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.interface.scanning_probe_interface import (
    BackScanCapability,
    ScanConstraints,
    ScanData,
    ScannerAxis,
    ScannerChannel,
    ScanSettings,
    ScanningProbeInterface,
)


# ══════════════════════════════════════════════════════════════════════════════
#  PI E-710 GCS DLL WRAPPER
#  ─────────────────────────
#  Source: PIE710Controller  v4
#  Firmware: V7.040  (GCS v1),  NI-GPIB,  E7XX_GCS_DLL_x64.dll
#
#  Critical V7.040 facts:
#   • Axis strings are CONCATENATED, no spaces  →  b'123'  not  b'1 2 3'
#   • Init sequence: INI(b'') then SVO per axis one at a time
#   • Scanning uses the old E-710 segment protocol via E7XXSendString
#   • Sample rate: 5000 Hz = 0.2 ms per waveform point
# ══════════════════════════════════════════════════════════════════════════════

class PIE7XXError(Exception):
    def __init__(self, message: str, error_code: int = 0):
        super().__init__(message)
        self.error_code = error_code


class PIE710Controller:

    _SOFT_ERRORS = frozenset({0, 2, 5, 7, 8, 10})

    SAMP_RATE: float = 5000.0

    BIT_WGO_START_DEFAULT           = 0x00000001
    BIT_WGO_START_EXTERN_TRIGGER    = 0x00000002
    BIT_WGO_WITH_DDL_INITIALISATION = 0x00000040
    BIT_WGO_WITH_DDL                = 0x00000080
    BIT_WGO_START_AT_ENDPOSITION    = 0x00000100
    BIT_WGO_SINGLE_RUN_DDL_TEST     = 0x00000200

    DRC_DEFAULT          = 0
    DRC_AXIS_TARGET_POS  = 1
    DRC_AXIS_ACTUAL_POS  = 2
    DRC_AXIS_POS_ERROR   = 3
    DRC_AXIS_DDL_DATA    = 4
    DRC_AXIS_DRIVING_VOL = 5
    DRC_PIEZO_MODEL_VOL  = 6
    DRC_PIEZO_VOL        = 7
    DRC_SENSOR_POS       = 8

    _BUF_SM = 256
    _BUF_MD = 1024
    _BUF_LG = 65536

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

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, dll_path: str, use_windll: bool = False):
        self._id: int               = -1
        self._dll_path              = dll_path
        self._registered: set       = set()
        self._axes: List[str]       = []
        self._travel_min: List[float] = []
        self._travel_max: List[float] = []
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

        def sig(name: str, rt, *at):
            try:
                fn = getattr(d, name)
                fn.restype  = rt
                fn.argtypes = list(at)
                self._registered.add(name)
            except AttributeError:
                pass

        sig("E7XX_InterfaceSetupDlg",        I,    CP)
        sig("E7XX_ConnectRS232",             I,    I, I)
        sig("E7XX_ConnectNIgpib",            I,    I, I)
        sig("E7XX_ConnectPciBoard",          I,    I)
        sig("E7XX_ConnectPciBoardAndReboot", I,    I)
        sig("E7XX_ChangeNIgpibAddress",      I,    I, I)
        sig("E7XX_IsConnected",              I,    I)
        sig("E7XX_CloseConnection",          None, I)
        sig("E7XX_GetError",                 I,    I)
        sig("E7XX_SetErrorCheck",            I,    I, I)
        sig("E7XX_TranslateError",           I,    I, CP, I)
        sig("E7XX_CountPciBoards",           I)
        sig("E7XX_qERR",                     I,    I, PI)
        sig("E7XX_qIDN",                     I,    I, CP, I)
        sig("E7XX_INI",                      I,    I, CP)
        sig("E7XX_qHLP",                     I,    I, CP, I)
        sig("E7XX_qHPA",                     I,    I, CP, I)
        sig("E7XX_CSV",                      I,    I, D)
        sig("E7XX_qCSV",                     I,    I, PD)
        sig("E7XX_qOVF",                     I,    I, CP, PI)
        sig("E7XX_RBT",                      I,    I)
        sig("E7XX_REP",                      I,    I)
        sig("E7XX_qSSN",                     I,    I, CP, I)
        sig("E7XX_qVER",                     I,    I, CP, I)
        sig("E7XX_CCT",                      I,    I, I)
        sig("E7XX_qSAI",                     I,    I, CP, I)
        sig("E7XX_qSAI_ALL",                 I,    I, CP, I)
        sig("E7XX_SAI",                      I,    I, CP, CP)
        sig("E7XX_qTVI",                     I,    I, CP, I)
        sig("E7XX_CCL",                      I,    I, I, CP)
        sig("E7XX_qCCL",                     I,    I, PI)
        sig("E7XX_AVG",                      I,    I, I)
        sig("E7XX_qAVG",                     I,    I, PI)
        sig("E7XX_DIO",                      I,    I, CP, PI)
        sig("E7XX_qTIO",                     I,    I, PI, PI)
        sig("E7XX_MOV",                      I,    I, CP, PD)
        sig("E7XX_qMOV",                     I,    I, CP, PD)
        sig("E7XX_MVR",                      I,    I, CP, PD)
        sig("E7XX_qPOS",                     I,    I, CP, PD)
        sig("E7XX_IsMoving",                 I,    I, CP, PI)
        sig("E7XX_HLT",                      I,    I, CP)
        sig("E7XX_qONT",                     I,    I, CP, PI)
        sig("E7XX_SVA",                      I,    I, CP, PD)
        sig("E7XX_qSVA",                     I,    I, CP, PD)
        sig("E7XX_SVR",                      I,    I, CP, PD)
        sig("E7XX_DFH",                      I,    I, CP)
        sig("E7XX_qDFH",                     I,    I, CP, PD)
        sig("E7XX_GOH",                      I,    I, CP)
        sig("E7XX_DFF",                      I,    I, CP, PD)
        sig("E7XX_qDFF",                     I,    I, CP, PD)
        sig("E7XX_SVO",                      I,    I, CP, PI)
        sig("E7XX_qSVO",                     I,    I, CP, PI)
        sig("E7XX_VEL",                      I,    I, CP, PD)
        sig("E7XX_qVEL",                     I,    I, CP, PD)
        sig("E7XX_NMOV",                     I,    I, CP, PD)
        sig("E7XX_NMVR",                     I,    I, CP, PD)
        sig("E7XX_DPO",                      I,    I, CP)
        sig("E7XX_qCST",                     I,    I, CP, CP, I)
        sig("E7XX_CST",                      I,    I, CP, CP)
        sig("E7XX_qVST",                     I,    I, CP, I)
        sig("E7XX_SPA",                      I,    I, CP, PI, PD, CP)
        sig("E7XX_qSPA",                     I,    I, CP, PI, PD, CP, I)
        sig("E7XX_SEP",                      I,    I, CP, CP, PI, PD, CP)
        sig("E7XX_qSEP",                     I,    I, CP, PI, PD, CP, I)
        sig("E7XX_WPA",                      I,    I, CP, CP, PI)
        sig("E7XX_RPA",                      I,    I, CP, PI)
        sig("E7XX_STE",                      I,    I, CH, D)
        sig("E7XX_qSTE",                     I,    I, CH, I, I, PD)
        sig("E7XX_IMP",                      I,    I, CH, D)
        sig("E7XX_IMP_PulseWidth",           I,    I, CH, D, I)
        sig("E7XX_qIMP",                     I,    I, CH, I, I, PD)
        sig("E7XX_E7XXSendString",           I,    I, CP)
        sig("E7XX_E7XXGetLineSize",          I,    I, PI)
        sig("E7XX_E7XXReadLine",             I,    I, CP, I)
        sig("E7XX_GcsCommandset",            I,    I, CP)
        sig("E7XX_GcsGetAnswer",             I,    I, CP, I)
        sig("E7XX_GcsGetAnswerSize",         I,    I, PI)
        sig("E7XX_ATZ",                      I,    I, CP, PD, PI)
        sig("E7XX_qTMN",                     I,    I, CP, PD)
        sig("E7XX_qTMX",                     I,    I, CP, PD)
        sig("E7XX_NLM",                      I,    I, CP, PD)
        sig("E7XX_qNLM",                     I,    I, CP, PD)
        sig("E7XX_PLM",                      I,    I, CP, PD)
        sig("E7XX_qPLM",                     I,    I, CP, PD)
        sig("E7XX_GetRefResult",             I,    I, CP, PI)
        sig("E7XX_WAV_SIN_P",               I,    I, CP, I, I, I, I, D, D, I)
        sig("E7XX_WAV_LIN",                 I,    I, CP, I, I, I, I, D, D, I)
        sig("E7XX_WAV_RAMP",                I,    I, CP, I, I, I, I, I, D, D, I)
        sig("E7XX_WAV_PNT",                 I,    I, CP, I, I, I, PD)
        sig("E7XX_qWAV",                    I,    I, CP, PI, PD)
        sig("E7XX_qGWD",                    I,    I, CH, I, I, PD)
        sig("E7XX_WGO",                     I,    I, CP, PI)
        sig("E7XX_qWGO",                    I,    I, CP, PI)
        sig("E7XX_WGC",                     I,    I, CP, PI)
        sig("E7XX_qWGC",                    I,    I, CP, PI)
        sig("E7XX_qTNR",                    I,    I, PI)
        sig("E7XX_DRC",                     I,    I, PI, CP, PI, PI)
        sig("E7XX_qDRC",                    I,    I, PI, CP, PI, PI, I)
        sig("E7XX_WGR",                     I,    I)
        sig("E7XX_qDRR_SYNC",               I,    I, I, I, I, PD)
        sig("E7XX_RTR",                     I,    I, I)
        sig("E7XX_qRTR",                    I,    I, PI)
        sig("E7XX_qTWG",                    I,    I, PI)
        sig("E7XX_WMS",                     I,    I, CP, PI)
        sig("E7XX_qWMS",                    I,    I, CP, PI)
        sig("E7XX_DTC",                     I,    I, I)
        sig("E7XX_WCL",                     I,    I, I)
        sig("E7XX_DDL",                     I,    I, I, I, I, PD)
        sig("E7XX_qDDL",                    I,    I, I, I, I, PD)
        sig("E7XX_TWS",                     I,    I, PI, PI, I)
        sig("E7XX_TWC",                     I,    I)
        sig("E7XX_qTLT",                    I,    I, PI)
        sig("E7XX_IsGeneratorRunning",      I,    I, CP, PI)
        sig("E7XX_VMA",                     I,    I, CP, PD)
        sig("E7XX_qVMA",                    I,    I, CP, PD)
        sig("E7XX_VMI",                     I,    I, CP, PD)
        sig("E7XX_qVMI",                    I,    I, CP, PD)
        sig("E7XX_VOL",                     I,    I, CP, PD)
        sig("E7XX_qVOL",                    I,    I, CP, PD)
        sig("E7XX_qTPC",                    I,    I, PI)
        sig("E7XX_qTAD",                    I,    I, CP, PI)
        sig("E7XX_qTNS",                    I,    I, CP, PD)
        sig("E7XX_qTSP",                    I,    I, CP, PD)
        sig("E7XX_qTSC",                    I,    I, PI)
        sig("E7XX_GetSupportedFunctions",   I,    I, PI, I, CP, I)
        sig("E7XX_GetSupportedParameters",  I,    I, PI, PI, PI, I, CP, I)
        sig("E7XX_GetSupportedControllers", I,    CP, I)

    # ------------------------------------------------------------------
    # Safe accessor
    # ------------------------------------------------------------------

    def _fn(self, name: str):
        if name not in self._registered:
            raise PIE7XXError(
                f"'{name}' not exported by '{os.path.basename(self._dll_path)}'")
        return getattr(self._dll, name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_connection(self):
        if self._id < 0:
            raise PIE7XXError("Not connected.")

    def _check(self, result: int, fname: str):
        if not result:
            code = self._dll.E7XX_GetError(self._id) if self._id >= 0 else -1
            msg  = self._translate(code)
            raise PIE7XXError(f"'{fname}' failed — error {code}: {msg}", code)

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
    def _iarr(vals) -> ctypes.Array:
        v = list(vals)
        return (ctypes.c_int * len(v))(*v)

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

    def get_error_code(self) -> int:
        return self._dll.E7XX_GetError(self._id) if self._id >= 0 else 0

    def translate_error(self, code: int) -> str:
        return self._translate(code)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connection_id(self) -> int:
        return self._id

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

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_rs232(self, port_number: int, baud_rate: int = 115200) -> int:
        _id = self._fn("E7XX_ConnectRS232")(port_number, baud_rate)
        if _id < 0:
            raise PIE7XXError(f"RS-232 connect failed (COM{port_number})")
        self._id = _id
        return _id

    def connect_ni_gpib(self, board_number: int, device_address: int) -> int:
        _id = self._fn("E7XX_ConnectNIgpib")(board_number, device_address)
        if _id < 0:
            raise PIE7XXError(
                f"NI-GPIB connect failed (board={board_number}, addr={device_address})")
        self._id = _id
        return _id

    def connect_pci(self, board_number: int, reboot: bool = False) -> int:
        fn  = "E7XX_ConnectPciBoardAndReboot" if reboot else "E7XX_ConnectPciBoard"
        _id = self._fn(fn)(board_number)
        if _id < 0:
            raise PIE7XXError(f"PCI connect failed (board={board_number})")
        self._id = _id
        return _id

    def close_connection(self):
        if self._id >= 0:
            try:
                self._fn("E7XX_CloseConnection")(self._id)
            except Exception:
                pass
            self._id = -1

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    def get_identification(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        self._check(self._fn("E7XX_qIDN")(self._id, buf, self._BUF_MD), "qIDN")
        return buf.value.decode("ascii", errors="replace").strip()

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
        if not axes:
            axes = ['1', '2', '3', '4']
        return axes

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

    def get_version(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        ok  = self._soft_check(
            self._fn("E7XX_qVER")(self._id, buf, self._BUF_MD), "qVER")
        return (buf.value.decode("ascii", errors="replace").strip()
                if ok else "(not supported on V7.040)")

    def get_serial_number(self) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_SM)
        ok  = self._soft_check(
            self._fn("E7XX_qSSN")(self._id, buf, self._BUF_SM), "qSSN")
        return (buf.value.decode("ascii", errors="replace").strip()
                if ok else "(not supported on V7.040)")

    def get_stage_type(self, axis) -> str:
        self._require_connection()
        buf = ctypes.create_string_buffer(self._BUF_MD)
        ret = self._fn("E7XX_qCST")(self._id, self._ax(axis), buf, self._BUF_MD)
        ec  = self._dll.E7XX_GetError(self._id) if not ret else 0
        return (buf.value.decode("ascii", errors="replace").strip()
                if (ret and ec == 0) else f"(error {ec}: {self._translate(ec)})")

    # ------------------------------------------------------------------
    # Servo
    # ------------------------------------------------------------------

    def set_servo(self, axes, states: List[bool]):
        self._require_connection()
        self._check(
            self._fn("E7XX_SVO")(self._id, self._ax(axes), self._barr(states)),
            "SVO")

    def get_servo(self, axes) -> List[bool]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("E7XX_qSVO")(self._id, self._ax(axes), arr), "qSVO")
        return [bool(arr[i]) for i in range(n)]

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def get_position(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
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

    def get_target_position(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qMOV")(self._id, self._ax(axes), arr), "qMOV")
        return [arr[i] for i in range(n)]

    def is_moving(self, axes) -> List[bool]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("E7XX_IsMoving")(self._id, self._ax(axes), arr), "IsMoving")
        return [bool(arr[i]) for i in range(n)]

    def is_on_target(self, axes) -> List[bool]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._ibuf(n)
        self._check(
            self._fn("E7XX_qONT")(self._id, self._ax(axes), arr), "qONT")
        return [bool(arr[i]) for i in range(n)]

    def halt(self, axes):
        self._require_connection()
        self._check(self._fn("E7XX_HLT")(self._id, self._ax(axes)), "HLT")

    def wait_for_motion(
        self,
        axes,
        poll_interval: float = 0.05,
        timeout: float = 60.0,
        settle_check: bool = True,
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

    def set_velocity(self, axes, velocities: List[float]):
        self._require_connection()
        self._check(
            self._fn("E7XX_VEL")(self._id, self._ax(axes), self._darr(velocities)),
            "VEL")

    def get_velocity(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qVEL")(self._id, self._ax(axes), arr), "qVEL")
        return [arr[i] for i in range(n)]

    def define_home(self, axes):
        self._require_connection()
        self._check(self._fn("E7XX_DFH")(self._id, self._ax(axes)), "DFH")

    def go_home(self, axes):
        self._require_connection()
        self._check(self._fn("E7XX_GOH")(self._id, self._ax(axes)), "GOH")

    def get_home_position(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qDFH")(self._id, self._ax(axes), arr), "qDFH")
        return [arr[i] for i in range(n)]

    # ------------------------------------------------------------------
    # Travel limits
    # ------------------------------------------------------------------

    def get_min_travel(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qTMN")(self._id, self._ax(axes), arr), "qTMN")
        return [arr[i] for i in range(n)]

    def get_max_travel(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qTMX")(self._id, self._ax(axes), arr), "qTMX")
        return [arr[i] for i in range(n)]

    def set_low_limit(self, axes, limits: List[float]):
        self._require_connection()
        self._check(
            self._fn("E7XX_NLM")(self._id, self._ax(axes), self._darr(limits)), "NLM")

    def get_low_limit(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qNLM")(self._id, self._ax(axes), arr), "qNLM")
        return [arr[i] for i in range(n)]

    def set_high_limit(self, axes, limits: List[float]):
        self._require_connection()
        self._check(
            self._fn("E7XX_PLM")(self._id, self._ax(axes), self._darr(limits)), "PLM")

    def get_high_limit(self, axes) -> List[float]:
        self._require_connection()
        n   = self._nax(axes, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qPLM")(self._id, self._ax(axes), arr), "qPLM")
        return [arr[i] for i in range(n)]

    # ------------------------------------------------------------------
    # Channel counts / voltages / sensors
    # ------------------------------------------------------------------

    def get_total_piezo_channels(self) -> int:
        self._require_connection()
        val = ctypes.c_int(0)
        self._check(self._fn("E7XX_qTPC")(self._id, ctypes.byref(val)), "qTPC")
        return val.value

    def get_total_sensor_channels(self) -> int:
        self._require_connection()
        val = ctypes.c_int(0)
        self._check(self._fn("E7XX_qTSC")(self._id, ctypes.byref(val)), "qTSC")
        return val.value

    def get_total_record_channels(self) -> int:
        self._require_connection()
        val = ctypes.c_int(0)
        self._check(self._fn("E7XX_qTNR")(self._id, ctypes.byref(val)), "qTNR")
        return val.value

    def get_sensor_position(self, channels) -> List[float]:
        self._require_connection()
        n   = self._nax(channels, len(self._axes))
        arr = self._dbuf(n)
        self._check(
            self._fn("E7XX_qTSP")(self._id, self._ax(channels), arr), "qTSP")
        return [arr[i] for i in range(n)]

    # ------------------------------------------------------------------
    # Raw / GCS commands
    # ------------------------------------------------------------------

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
        buf      = ctypes.create_string_buffer(buf_size)
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

    # ------------------------------------------------------------------
    # Old-style E-710 segment protocol + scanning
    # ------------------------------------------------------------------

    def segment(
        self,
        seg_num: int,
        total_pts: int,
        curve_pts: int,
        curve_center: Optional[int],
        speed_pts: int,
        start_pt: int,
        offset: float,
        amplitude: float,
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

    def scan_x(
        self,
        x: List[float], y: float, z: float,
        t_pixel: float,
        trigger: str = "SPCM",
    ):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x[0], y, z])
        time.sleep(0.5)
        nx        = len(x)
        speed_pts = 100
        start_pt  = 100
        n         = round(t_pixel * self.SAMP_RATE)
        curve_pts = nx * n
        total_pts = curve_pts + 2 * speed_pts + 2 * start_pt
        amp       = x[-1] - x[0]
        self.send_raw_string('0PT0')
        self.segment(1, total_pts, curve_pts, None, speed_pts, start_pt, 0,   amp)
        self.segment(2, total_pts, curve_pts, None, speed_pts, start_pt, amp, -amp)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts}')
        self.send_raw_string('1SF1')
        self.send_raw_string('1CF1')
        trig_str = '1KT-1' if trigger != 'APD' else '1KT2'
        self.send_raw_string(trig_str)
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        t_on  = start_pt + speed_pts
        t_off = t_on + curve_pts
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_off}FT259')
        wait_ms = round(2 * total_pts / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'1SC0,WA{wait_ms}')

    def scan_y(
        self,
        x: float, y: List[float], z: float,
        t_pixel: float,
        trigger: str = "SPCM",
    ):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x, y[0], z])
        time.sleep(0.5)
        ny        = len(y)
        speed_pts = 100
        start_pt  = 100
        n         = round(t_pixel * self.SAMP_RATE)
        curve_pts = ny * n
        total_pts = curve_pts + 2 * speed_pts + 2 * start_pt
        amp       = y[-1] - y[0]
        self.send_raw_string('0PT0')
        self.segment(1, total_pts, curve_pts, None, speed_pts, start_pt, 0,   amp)
        self.segment(2, total_pts, curve_pts, None, speed_pts, start_pt, amp, -amp)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts}')
        self.send_raw_string('1SF1')
        self.send_raw_string('2CF1')
        trig_str = '1KT-1' if trigger != 'APD' else '1KT2'
        self.send_raw_string(trig_str)
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        t_on  = start_pt + speed_pts
        t_off = t_on + curve_pts
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_off}FT259')
        wait_ms = round(2 * total_pts / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'1SC0,WA{wait_ms}')

    def scan_z(
        self,
        x: float, y: float, z: List[float],
        t_pixel: float,
        trigger: str = "SPCM",
    ):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x, y, z[0]])
        time.sleep(0.5)
        nz        = len(z)
        speed_pts = 100
        start_pt  = 100
        n         = round(t_pixel * self.SAMP_RATE)
        curve_pts = nz * n
        total_pts = curve_pts + 2 * speed_pts + 2 * start_pt
        amp       = z[-1] - z[0]
        self.send_raw_string('0PT0')
        self.segment(1, total_pts, curve_pts, None, speed_pts, start_pt, 0,   amp)
        self.segment(2, total_pts, curve_pts, None, speed_pts, start_pt, amp, -amp)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts}')
        self.send_raw_string('1SF1')
        self.send_raw_string('3CF1')
        trig_str = '1KT-1' if trigger != 'APD' else '1KT2'
        self.send_raw_string(trig_str)
        self.send_raw_string('2KT2')
        self.send_raw_string('0FT0')
        t_on  = start_pt + speed_pts
        t_off = t_on + curve_pts
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_off}FT259')
        wait_ms = round(2 * total_pts / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'1SC0,WA{wait_ms}')

    def scan_xy(
        self,
        x: List[float],
        y: List[float],
        z: float,
        t_pixel: float,
        trigger: str = "SPCM",
    ):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x[0], y[0], z])
        time.sleep(0.5)
        nx = len(x)
        ny = len(y)
        speed_pts_x = 50
        start_pt_x  = 0
        n           = round(t_pixel * self.SAMP_RATE)
        curve_pts_x = nx * n
        total_pts_x = curve_pts_x + 2 * speed_pts_x + 2 * start_pt_x
        amp_x       = x[-1] - x[0]
        start_pt_y  = total_pts_x
        curve_pts_y = 350
        total_pts_y = 2 * total_pts_x
        amp_y       = (y[-1] - y[0]) / (ny - 1) if ny > 1 else 0.0
        self.send_raw_string('0PT0')
        self.segment(1, total_pts_x, curve_pts_x, None, speed_pts_x, start_pt_x, 0,     amp_x)
        self.segment(2, total_pts_x, curve_pts_x, None, speed_pts_x, start_pt_x, amp_x, -amp_x)
        self.segment(3, total_pts_y, curve_pts_y, None, speed_pts_x, start_pt_y, 0,     amp_y)
        self.send_raw_string('0PT0')
        self.send_raw_string(f'1PT{2 * total_pts_x}')
        self.send_raw_string(f'2PT{total_pts_y}')
        self.send_raw_string('1SF1')
        self.send_raw_string('2SF2')
        self.send_raw_string('1CF1')
        self.send_raw_string('2CF2')
        trig_str = '1KT-1' if trigger != 'APD' else '1KT2'
        self.send_raw_string(trig_str)
        self.send_raw_string('2KT2')
        t_on  = start_pt_x + speed_pts_x
        t_off = t_on + curve_pts_x
        self.send_raw_string('0FT0')
        self.send_raw_string(f'{t_on}FT3')
        self.send_raw_string(f'{t_off}FT259')
        wait_ms = round(2 * total_pts_x / self.SAMP_RATE * 1000 + 100)
        self.send_raw_string(f'0SC32,WA{wait_ms},RP{ny}')

    def scan_xz(
        self,
        x: List[float], y: float, z: List[float],
        t_pixel: float,
    ):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x[0], y, z[0]])
        time.sleep(0.5)
        nx, nz    = len(x), len(z)
        speed_pts = 100
        n         = round(t_pixel * self.SAMP_RATE)
        curve_pts = nx * n + 2 * speed_pts
        start_pt  = 0
        total_pts = curve_pts + start_pt + 200
        amp_x     = x[-1] - x[0]
        amp_z     = (z[-1] - z[0]) / (nz - 1) if nz > 1 else 0.0
        t_on      = speed_pts + start_pt + 100
        t_off     = curve_pts - 2 * speed_pts + t_on
        self.send_raw_string('0PT0')
        self.segment(1, total_pts,        curve_pts, None, speed_pts, start_pt, 0,     amp_x)
        self.segment(2, 2000,             1900,      None, 100,        0,        amp_x, -amp_x)
        self.segment(3, total_pts + 2000, 350,       None, 100, total_pts,       0,     amp_z)
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

    def scan_yz(
        self,
        x: float, y: List[float], z: List[float],
        t_pixel: float,
    ):
        self._require_connection()
        self.move_absolute(['1', '2', '3'], [x, y[0], z[0]])
        time.sleep(0.5)
        ny, nz    = len(y), len(z)
        speed_pts = 100
        n         = round(t_pixel * self.SAMP_RATE)
        curve_pts = ny * n + 2 * speed_pts
        start_pt  = 0
        total_pts = curve_pts + start_pt + 200
        amp_y     = y[-1] - y[0]
        amp_z     = (z[-1] - z[0]) / (nz - 1) if nz > 1 else 0.0
        t_on      = speed_pts + start_pt + 100
        t_off     = curve_pts - 2 * speed_pts + t_on
        self.send_raw_string('0PT0')
        self.segment(1, total_pts,        curve_pts, None, speed_pts, start_pt, 0,     amp_y)
        self.segment(2, 2000,             1900,      None, 100,        0,        amp_y, -amp_y)
        self.segment(3, total_pts + 2000, 350,       None, 100, total_pts,       0,     amp_z)
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

    def move_xyz(self, x: float, y: float, z: float, wait: bool = True):
        self._require_connection()
        self._check(
            self._fn("E7XX_MOV")(self._id, b"123", self._darr([x, y, z])),
            "MOV_xyz",
        )
        if wait:
            self.wait_for_motion("123", timeout=30.0)

    # ------------------------------------------------------------------
    # probe_firmware
    # ------------------------------------------------------------------

    def probe_firmware(self) -> dict:
        self._require_connection()
        result = {}

        result["idn"]     = self.get_identification()
        result["version"] = self.get_version()
        result["serial"]  = self.get_serial_number()

        axes       = self.get_axes()
        self._axes = axes
        axes_bytes = self._ax(axes)
        n          = len(axes)
        result["axes"]   = axes
        result["n_axes"] = n

        for attr, fn in [
            ("n_piezo_ch",  self.get_total_piezo_channels),
            ("n_sensor_ch", self.get_total_sensor_channels),
            ("n_record_ch", self.get_total_record_channels),
        ]:
            try:
                result[attr] = fn()
            except PIE7XXError:
                result[attr] = 0

        # INI all axes
        self.clear_error()
        ret = self._fn("E7XX_INI")(self._id, b"")
        self._dll.E7XX_GetError(self._id)
        time.sleep(0.2)
        self.clear_error()

        # SVO per axis, one at a time
        servo_ok = {}
        for ax in axes:
            self.clear_error()
            ax_b  = ax.encode("ascii")
            state = (ctypes.c_int * 1)(1)
            ret   = self._fn("E7XX_SVO")(self._id, ax_b, state)
            ec    = self._dll.E7XX_GetError(self._id)
            servo_ok[ax] = bool(ret) and ec == 0
        time.sleep(0.1)

        # Travel range
        mn, mx = None, None
        try:
            arr_mn = self._dbuf(n)
            arr_mx = self._dbuf(n)
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

        if mn is None: mn = [0.0]   * n
        if mx is None: mx = [100.0] * n
        self._travel_min = mn
        self._travel_max = mx

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

        result["travel_min"]   = mn
        result["travel_max"]   = mx
        result["servo_states"] = [servo_ok.get(ax, False) for ax in axes]
        result["positions"]    = pos
        return result

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close_connection()
        return False

    def __del__(self):
        try:
            self.close_connection()
        except Exception:
            pass

    def __repr__(self) -> str:
        state  = f"id={self._id}" if self._id >= 0 else "disconnected"
        axes_s = f" axes={''.join(self._axes)}" if self._axes else ""
        return (f"PIE710Controller('{os.path.basename(self._dll_path)}', "
                f"{state}{axes_s}, {len(self._registered)} fns)")


# ══════════════════════════════════════════════════════════════════════════════
#  QUDI HARDWARE MODULE
# ══════════════════════════════════════════════════════════════════════════════

class PIE710ScanningProbe(ScanningProbeInterface):
    """
    Qudi ScanningProbeInterface for the PI E-710 3CD piezo scanner.

    Axis mapping
    ────────────
        'x'  →  PI physical axis 1   (fast axis in XY and XZ scans)
        'y'  →  PI physical axis 2   (fast axis in YZ; slow axis in XY)
        'z'  →  PI physical axis 3   (slow axis in XZ and YZ scans)

    Supported scan axis combinations
    ─────────────────────────────────
        1D :  ('x',)  ('y',)  ('z',)
        2D :  ('x','y')  ('x','z')  ('y','z')
              First element = fast axis.

    Photon counting — PLACEHOLDER
    ──────────────────────────────
        Three methods at the bottom of this class need to be filled in:
            _arm_photon_counter(n_pixels, t_pixel)
            _read_photon_counts(n_pixels)
            _stop_photon_counter()

        Two BNC cables required:
            PI E-710 Trigger OUT  →  Counter Gate / Clock IN
            APD / SPCM TTL OUT    →  Counter Count Source IN
    """

    # ── Config options ────────────────────────────────────────────────────────
    _dll_path     = ConfigOption('dll_path',     default='C:\\Program Files\\PI\\E-710\\E7XX.dll')
    _gpib_board   = ConfigOption('gpib_board',   default=0)
    _gpib_address = ConfigOption('gpib_address', default=4)
    _x_range      = ConfigOption('x_range',      default=[0.0, 100.0])
    _y_range      = ConfigOption('y_range',      default=[0.0, 100.0])
    _z_range      = ConfigOption('z_range',      default=[0.0,  50.0])
    _trigger_mode = ConfigOption('trigger_mode', default='SPCM')

    # ══════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ctrl:               Optional[PIE710Controller] = None
        self._constraints:        Optional[ScanConstraints]  = None
        self._scan_settings:      Optional[ScanSettings]     = None
        self._scan_data:          Optional[ScanData]         = None
        self._is_scanning:        bool                       = False
        self._stop_requested:     bool                       = False
        self._scan_thread:        Optional[threading.Thread] = None
        self._lock                                           = threading.Lock()
        self._target_pos: Dict[str, float] = {'x': 0.0, 'y': 0.0, 'z': 0.0}

    def on_activate(self) -> None:
        try:
            self._ctrl = PIE710Controller(self._dll_path)
            self._ctrl.connect_ni_gpib(
                board_number   = int(self._gpib_board),
                device_address = int(self._gpib_address),
            )
            self.log.info("PI E-710 connected.")

            info = self._ctrl.probe_firmware()
            self.log.info(f"Controller: {info.get('idn', '(unknown)')}")

            # Use hardware-reported travel limits if available
            if (info.get('travel_min') and info.get('travel_max')
                    and len(info['travel_min']) >= 3):
                self._x_range = [info['travel_min'][0], info['travel_max'][0]]
                self._y_range = [info['travel_min'][1], info['travel_max'][1]]
                self._z_range = [info['travel_min'][2], info['travel_max'][2]]

            pos = self._ctrl.get_position("123")
            self._target_pos = {'x': pos[0], 'y': pos[1], 'z': pos[2]}

            self._constraints = self._build_constraints()
            self.log.info("PI E-710 ScanningProbe ready.")

        except PIE7XXError as exc:
            self.log.exception(f"PI E-710 activation failed: {exc}")
            raise

    def on_deactivate(self) -> None:
        if self._is_scanning:
            self.stop_scan()
            if self._scan_thread and self._scan_thread.is_alive():
                self._scan_thread.join(timeout=15.0)
        if self._ctrl is not None:
            try:
                self._ctrl.close_connection()
            except Exception as exc:
                self.log.warning(f"PI close_connection: {exc}")
            self._ctrl = None
        self.log.info("PI E-710 ScanningProbe deactivated.")

    # ══════════════════════════════════════════════════════════════════════════
    # Constraints
    # ══════════════════════════════════════════════════════════════════════════

    def _build_constraints(self) -> ScanConstraints:
        """
        Build ScanConstraints from hardware travel limits.

        Add extra ScannerChannel entries here if you have multiple detectors.
        Update _store_1d_data / _store_2d_data to fill them with real data.
        """
        channel_objects = (
            ScannerChannel(name='APD', unit='c/s', dtype='float64'),
            # ScannerChannel(name='APD2', unit='c/s', dtype='float64'),
        )

        def _make_axis(name: str, lo: float, hi: float) -> ScannerAxis:
            span = float(hi - lo)
            return ScannerAxis(
                name=name,
                unit='µm',
                position=ScalarConstraint(
                    default=round((lo + hi) / 2.0, 3),
                    bounds=(float(lo), float(hi)),
                ),
                step=ScalarConstraint(
                    default=0.1,
                    bounds=(1e-3, span),
                ),
                resolution=ScalarConstraint(
                    default=100,
                    bounds=(2, 2000),
                    enforce_int=True,
                ),
                frequency=ScalarConstraint(
                    default=1000.0,
                    bounds=(1.0, 5000.0),
                ),
            )

        axis_objects = (
            _make_axis('x', *self._x_range),
            _make_axis('y', *self._y_range),
            _make_axis('z', *self._z_range),
        )

        return ScanConstraints(
            channel_objects=channel_objects,
            axis_objects=axis_objects,
            back_scan_capability=BackScanCapability(0),
            has_position_feedback=True,
            square_px_only=False,
        )

    @property
    def constraints(self) -> ScanConstraints:
        return self._constraints

    # ══════════════════════════════════════════════════════════════════════════
    # Scan settings
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        return self._scan_settings

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # Reset
    # ══════════════════════════════════════════════════════════════════════════

    def reset(self) -> None:
        with self._lock:
            if self._is_scanning:
                self._stop_requested = True
                self._halt_generators()
            try:
                self._ctrl.probe_firmware()
                self.log.info("PI E-710 reset complete.")
            except PIE7XXError as exc:
                self.log.error(f"PI E-710 reset error: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    # Configure
    # ══════════════════════════════════════════════════════════════════════════

    def configure_scan(self, settings: ScanSettings) -> None:
        with self._lock:
            if self._is_scanning:
                self.log.error("Cannot configure scan while scanning.")
                return

            self._constraints.check_settings(settings)

            supported = {
                ('x',), ('y',), ('z',),
                ('x', 'y'), ('x', 'z'), ('y', 'z'),
            }
            if tuple(settings.axes) not in supported:
                raise ValueError(
                    f"Axis combination {settings.axes} not supported. "
                    f"Supported: {supported}"
                )

            self._scan_settings = settings
            self._scan_data     = None
            self.log.debug(
                f"Scan configured: axes={settings.axes}, "
                f"range={settings.range}, res={settings.resolution}, "
                f"freq={settings.frequency:.1f} Hz"
            )

    def configure_back_scan(self, settings: ScanSettings) -> None:
        raise ValueError(
            "PI E-710 does not support back-scan data acquisition. "
            "The PI sweeps forward then backward automatically, but "
            "photon triggers are only gated on the forward sweep."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Motion
    # ══════════════════════════════════════════════════════════════════════════

    def move_absolute(
        self,
        position: Dict[str, float],
        velocity: Optional[float] = None,
        blocking: bool = False,
    ) -> Dict[str, float]:
        if self._is_scanning:
            self.log.error("Cannot move while scanning.")
            return self.get_target()

        with self._lock:
            try:
                target = dict(self._target_pos)
                for ax, val in position.items():
                    if ax in ('x', 'y', 'z'):
                        target[ax] = float(val)

                target['x'] = float(np.clip(target['x'], *self._x_range))
                target['y'] = float(np.clip(target['y'], *self._y_range))
                target['z'] = float(np.clip(target['z'], *self._z_range))

                self._ctrl.move_absolute(
                    ['1', '2', '3'],
                    [target['x'], target['y'], target['z']],
                )
                self._target_pos = target

                if blocking:
                    self._ctrl.wait_for_motion("123", timeout=60.0)

                return dict(self._target_pos)

            except PIE7XXError as exc:
                self.log.error(f"move_absolute error: {exc}")
                return self.get_target()

    def move_relative(
        self,
        distance: Dict[str, float],
        velocity: Optional[float] = None,
        blocking: bool = False,
    ) -> Dict[str, float]:
        if self._is_scanning:
            self.log.error("Cannot move while scanning.")
            return self.get_target()
        current = self.get_target()
        new_pos = {ax: current[ax] + distance.get(ax, 0.0)
                   for ax in ('x', 'y', 'z')}
        return self.move_absolute(new_pos, velocity=velocity, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        return dict(self._target_pos)

    def get_position(self) -> Dict[str, float]:
        if self._ctrl is None or not self._ctrl.connected:
            return dict(self._target_pos)
        try:
            pos = self._ctrl.get_position("123")
            return {'x': pos[0], 'y': pos[1], 'z': pos[2]}
        except PIE7XXError as exc:
            self.log.warning(f"get_position failed, returning target: {exc}")
            return dict(self._target_pos)

    # ══════════════════════════════════════════════════════════════════════════
    # Scanning
    # ══════════════════════════════════════════════════════════════════════════

    def start_scan(self) -> None:
        with self._lock:
            if self._is_scanning:
                self.log.error("Scan already running.")
                return
            if self._scan_settings is None:
                self.log.error("No scan configured.")
                return

            self._scan_data = ScanData.from_constraints(
                settings=self._scan_settings,
                constraints=self._constraints,
                scanner_target_at_start=self.get_target(),
            )
            self._scan_data.new_scan()

            self._is_scanning    = True
            self._stop_requested = False
            self._scan_thread    = threading.Thread(
                target=self._scan_worker,
                name='PIE710ScanWorker',
                daemon=True,
            )
            self._scan_thread.start()

    def stop_scan(self) -> None:
        if not self._is_scanning:
            self.log.warning("stop_scan called but no scan running.")
            return
        self._stop_requested = True
        self._halt_generators()
        self._stop_photon_counter()
        self._is_scanning = False
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=10.0)
        self.log.info("Scan stopped.")

    def get_scan_data(self) -> Optional[ScanData]:
        return self._scan_data

    def get_back_scan_data(self) -> Optional[ScanData]:
        return None

    def emergency_stop(self) -> None:
        self._stop_requested = True
        try:
            self._ctrl.halt("123")
        except PIE7XXError:
            pass
        self._halt_generators()
        self._stop_photon_counter()
        self._is_scanning = False
        self.log.warning("EMERGENCY STOP.")

    # ══════════════════════════════════════════════════════════════════════════
    # Internal scan orchestration
    # ══════════════════════════════════════════════════════════════════════════

    def _scan_worker(self) -> None:
        try:
            s       = self._scan_settings
            t_pixel = 1.0 / s.frequency

            if len(s.axes) == 1:
                self._run_1d_scan(
                    axis       = s.axes[0],
                    scan_range = s.range[0],
                    n_pts      = s.resolution[0],
                    t_pixel    = t_pixel,
                )
            elif len(s.axes) == 2:
                self._run_2d_scan(
                    fast_axis  = s.axes[0],
                    slow_axis  = s.axes[1],
                    fast_range = s.range[0],
                    slow_range = s.range[1],
                    n_fast     = s.resolution[0],
                    n_slow     = s.resolution[1],
                    t_pixel    = t_pixel,
                )
        except Exception as exc:
            self.log.exception(f"Scan worker error: {exc}")
        finally:
            self._is_scanning = False

    def _run_1d_scan(
        self,
        axis:       str,
        scan_range: Tuple[float, float],
        n_pts:      int,
        t_pixel:    float,
    ) -> None:
        pos_array = np.linspace(scan_range[0], scan_range[1], n_pts).tolist()
        cur       = self.get_target()

        # 1. Arm counter BEFORE sending scan command to PI
        self._arm_photon_counter(n_pixels=n_pts, t_pixel=t_pixel)
        if self._stop_requested:
            return

        # 2. Send PI scan command (non-blocking — PI runs autonomously)
        try:
            if axis == 'x':
                self._ctrl.scan_x(
                    x=pos_array, y=cur['y'], z=cur['z'],
                    t_pixel=t_pixel, trigger=self._trigger_mode,
                )
            elif axis == 'y':
                self._ctrl.scan_y(
                    x=cur['x'], y=pos_array, z=cur['z'],
                    t_pixel=t_pixel, trigger=self._trigger_mode,
                )
            elif axis == 'z':
                self._ctrl.scan_z(
                    x=cur['x'], y=cur['y'], z=pos_array,
                    t_pixel=t_pixel, trigger=self._trigger_mode,
                )
        except PIE7XXError as exc:
            self.log.error(f"PI 1D scan error: {exc}")
            self._stop_photon_counter()
            return

        # 3. Wait for PI waveform generators to finish
        speed_pts   = 100
        estimated_s = 2.0 * n_pts * t_pixel + 2.0 * speed_pts / PIE710Controller.SAMP_RATE + 1.5
        self._wait_for_scan_complete(estimated_s=estimated_s)
        if self._stop_requested:
            self._stop_photon_counter()
            return

        # 4. Read counts from counter hardware
        counts = self._read_photon_counts(n_pixels=n_pts)

        # 5. Store in ScanData
        self._store_1d_data(counts=counts, axis=axis,
                            axis_end=pos_array[-1],
                            t_pixel=t_pixel, n_pts=n_pts)

    def _run_2d_scan(
        self,
        fast_axis:  str,
        slow_axis:  str,
        fast_range: Tuple[float, float],
        slow_range: Tuple[float, float],
        n_fast:     int,
        n_slow:     int,
        t_pixel:    float,
    ) -> None:
        fast_pos = np.linspace(fast_range[0], fast_range[1], n_fast).tolist()
        slow_pos = np.linspace(slow_range[0], slow_range[1], n_slow).tolist()
        cur      = self.get_target()
        n_total  = n_fast * n_slow

        # 1. Arm counter BEFORE sending scan command to PI
        self._arm_photon_counter(n_pixels=n_total, t_pixel=t_pixel)
        if self._stop_requested:
            return

        # 2. Send PI scan command
        try:
            if fast_axis == 'x' and slow_axis == 'y':
                self._ctrl.scan_xy(
                    x=fast_pos, y=slow_pos, z=cur['z'],
                    t_pixel=t_pixel, trigger=self._trigger_mode,
                )
            elif fast_axis == 'x' and slow_axis == 'z':
                self._ctrl.scan_xz(
                    x=fast_pos, y=cur['y'], z=slow_pos,
                    t_pixel=t_pixel,
                )
            elif fast_axis == 'y' and slow_axis == 'z':
                self._ctrl.scan_yz(
                    x=cur['x'], y=fast_pos, z=slow_pos,
                    t_pixel=t_pixel,
                )
        except PIE7XXError as exc:
            self.log.error(f"PI 2D scan error: {exc}")
            self._stop_photon_counter()
            return

        # 3. Wait for PI to finish
        if slow_axis == 'y':
            speed_pts   = 50
            n           = max(1, round(t_pixel * PIE710Controller.SAMP_RATE))
            total_pts   = n_fast * n + 2 * speed_pts
            line_s      = 2.0 * total_pts / PIE710Controller.SAMP_RATE + 0.1
        else:
            line_s = 1.0 + 0.2     # scan_xz / scan_yz: WA1000 hard-coded
        estimated_s = n_slow * line_s + 5.0

        self._wait_for_scan_complete(estimated_s=estimated_s)
        if self._stop_requested:
            self._stop_photon_counter()
            return

        # 4. Read counts
        counts = self._read_photon_counts(n_pixels=n_total)

        # 5. Store in ScanData
        self._store_2d_data(counts=counts,
                            fast_axis=fast_axis, slow_axis=slow_axis,
                            fast_end=fast_pos[-1], slow_end=slow_pos[-1],
                            t_pixel=t_pixel, n_fast=n_fast, n_slow=n_slow)

    # ══════════════════════════════════════════════════════════════════════════
    # Data storage
    # ══════════════════════════════════════════════════════════════════════════

    def _store_1d_data(
        self,
        counts:   Optional[np.ndarray],
        axis:     str,
        axis_end: float,
        t_pixel:  float,
        n_pts:    int,
    ) -> None:
        if self._scan_data is None:
            return
        channels = self._scan_settings.channels
        if counts is not None and len(counts) == n_pts:
            rate = np.asarray(counts, dtype=float) / t_pixel
            data_dict = {}
            for idx, ch in enumerate(channels):
                data_dict[ch] = rate if idx == 0 else np.zeros(n_pts, dtype=float)
            try:
                self._scan_data.data = data_dict
            except ValueError as exc:
                self.log.error(f"ScanData 1D write failed: {exc}")
        else:
            self.log.warning(
                f"Count array length mismatch: "
                f"got {len(counts) if counts is not None else 'None'}, "
                f"expected {n_pts}."
            )
        self._target_pos[axis] = axis_end

    def _store_2d_data(
        self,
        counts:    Optional[np.ndarray],
        fast_axis: str,
        slow_axis: str,
        fast_end:  float,
        slow_end:  float,
        t_pixel:   float,
        n_fast:    int,
        n_slow:    int,
    ) -> None:
        if self._scan_data is None:
            return
        channels   = self._scan_settings.channels
        resolution = (n_fast, n_slow)
        n_total    = n_fast * n_slow
        if counts is not None and len(counts) == n_total:
            # Counter delivers rows in slow-axis order →
            # reshape (n_slow, n_fast) then transpose to (n_fast, n_slow)
            rate_2d = (
                np.asarray(counts, dtype=float)
                .reshape(n_slow, n_fast)
                .T
                / t_pixel
            )
            data_dict = {}
            for idx, ch in enumerate(channels):
                data_dict[ch] = (rate_2d if idx == 0
                                 else np.zeros(resolution, dtype=float))
            try:
                self._scan_data.data = data_dict
            except ValueError as exc:
                self.log.error(f"ScanData 2D write failed: {exc}")
        else:
            self.log.warning(
                f"Count array length mismatch: "
                f"got {len(counts) if counts is not None else 'None'}, "
                f"expected {n_total}."
            )
        self._target_pos[fast_axis] = fast_end
        self._target_pos[slow_axis] = slow_end

    # ══════════════════════════════════════════════════════════════════════════
    # Wait / control helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _wait_for_scan_complete(
        self,
        estimated_s:   float = 30.0,
        poll_interval: float = 0.25,
    ) -> None:
        """
        Block until PI waveform generators are idle.
        Coarse sleep for 80% of estimated time, then poll IsGeneratorRunning.
        """
        coarse_s = max(0.0, estimated_s * 0.80 - poll_interval)
        deadline = time.monotonic() + estimated_s * 3.0 + 10.0

        # Phase 1 — coarse sleep in 100 ms chunks
        elapsed, chunk = 0.0, 0.1
        while elapsed < coarse_s:
            if self._stop_requested:
                return
            time.sleep(chunk)
            elapsed += chunk

        # Phase 2 — poll
        try:
            while time.monotonic() < deadline:
                if self._stop_requested:
                    return
                buf = (ctypes.c_int * 2)()
                ret = self._ctrl._dll.E7XX_IsGeneratorRunning(
                    self._ctrl._id, b'12', buf)
                if ret and buf[0] == 0 and buf[1] == 0:
                    return
                time.sleep(poll_interval)
            self.log.warning("Scan completion timeout exceeded.")
        except Exception as exc:
            self.log.warning(f"IsGeneratorRunning poll failed ({exc}); sleeping.")
            time.sleep(max(0.0, estimated_s - elapsed))

    def _halt_generators(self) -> None:
        try:
            self._ctrl.send_raw_string('0SC0')
        except Exception as exc:
            self.log.warning(f"Halt generators: {exc}")

    # ══════════════════════════════════════════════════════════════════════════
    #
    #   PHOTON COUNTING — PLACEHOLDER METHODS
    #
    #   Fill these three methods in with your counter hardware code.
    #
    #   Call order:
    #       _arm_photon_counter()   ← called BEFORE the PI scan fires
    #       [PI scan runs autonomously]
    #       _read_photon_counts()   ← called AFTER PI finishes
    #
    #       _stop_photon_counter()  ← called on abort / emergency stop
    #
    #   Physical wiring:
    #       PI E-710  Trigger OUT  →  Counter  Gate / Clock IN
    #       APD/SPCM  Signal OUT   →  Counter  Count Source IN
    #
    # ══════════════════════════════════════════════════════════════════════════

    def _arm_photon_counter(self, n_pixels: int, t_pixel: float) -> None:
        """
        Prepare and arm the photon counter for n_pixels gated acquisitions.

        Called BEFORE the PI scan command is sent.
        The counter must be ready to accept triggers the instant the
        PI starts its waveform.

        Parameters
        ----------
        n_pixels : total number of pixels (n_x for 1D; n_x * n_y for 2D)
        t_pixel  : dwell time per pixel in seconds  (= 1 / frequency)

        ── NI-DAQ example (nidaqmx) ──────────────────────────────────────
        import nidaqmx
        from nidaqmx.constants import Edge, AcquisitionType

        self._daq_task = nidaqmx.Task()
        self._daq_task.ci_channels.add_ci_count_edges_chan('Dev1/ctr0')
        self._daq_task.timing.cfg_samp_clk_timing(
            rate           = 1.0 / t_pixel,
            source         = '/Dev1/PFI0',       # ← PI Trigger OUT
            active_edge    = Edge.RISING,
            sample_mode    = AcquisitionType.FINITE,
            samps_per_chan = n_pixels,
        )
        self._daq_task.start()
        ──────────────────────────────────────────────────────────────────

        ── Swabian Time Tagger example ───────────────────────────────────
        import TimeTagger
        self._tt       = TimeTagger.createTimeTagger()
        self._tt_meas  = TimeTagger.CountBetweenMarkers(
            tagger        = self._tt,
            click_channel = 1,     # APD / SPCM
            begin_channel = 2,     # PI Trigger OUT
            n_values      = n_pixels,
        )
        ──────────────────────────────────────────────────────────────────
        """
        # ── YOUR CODE HERE ────────────────────────────────────────────────
        self.log.debug(
            f"[PLACEHOLDER] _arm_photon_counter  "
            f"n_pixels={n_pixels}  t_pixel={t_pixel * 1e3:.3f} ms"
        )
        # ─────────────────────────────────────────────────────────────────

    def _read_photon_counts(self, n_pixels: int) -> Optional[np.ndarray]:
        """
        Read n_pixels raw photon counts from the counter hardware.

        Called AFTER _wait_for_scan_complete() confirms the PI is idle.

        Returns
        -------
        np.ndarray, shape (n_pixels,)
            Raw integer counts per pixel.
            Conversion to counts/s is done in _store_1d_data/_store_2d_data.

            For a 2D scan the array must be in row-major (line-major) order:
                index k = i_slow * n_fast + i_fast
        None
            Return None if the counter is not yet connected.

        ── NI-DAQ example ────────────────────────────────────────────────
        raw = np.array(
            self._daq_task.read(number_of_samples_per_channel=n_pixels,
                                timeout=120.0),
            dtype=float,
        )
        self._daq_task.stop()
        self._daq_task.close()
        self._daq_task = None
        # NI counters return cumulative edges — differentiate to get per-pixel counts
        return np.diff(np.concatenate([[0.0], raw]))
        ──────────────────────────────────────────────────────────────────

        ── Swabian Time Tagger example ───────────────────────────────────
        deadline = time.monotonic() + 120.0
        while not self._tt_meas.ready():
            if time.monotonic() > deadline:
                self.log.error("Time Tagger data not ready.")
                return None
            time.sleep(0.05)
        return self._tt_meas.getData().ravel().astype(float)
        ──────────────────────────────────────────────────────────────────
        """
        # ── YOUR CODE HERE ────────────────────────────────────────────────
        self.log.debug(f"[PLACEHOLDER] _read_photon_counts  n_pixels={n_pixels}")
        # Returning zeros keeps Qudi running during development.
        # Replace with your real hardware read.
        return np.zeros(n_pixels, dtype=float)
        # ─────────────────────────────────────────────────────────────────

    def _stop_photon_counter(self) -> None:
        """
        Abort and clean up the photon counter immediately.

        Called by stop_scan() and emergency_stop().
        Must never raise an exception.

        ── NI-DAQ example ────────────────────────────────────────────────
        try:
            if getattr(self, '_daq_task', None) is not None:
                self._daq_task.stop()
                self._daq_task.close()
                self._daq_task = None
        except Exception as exc:
            self.log.warning(f"NI-DAQ stop: {exc}")
        ──────────────────────────────────────────────────────────────────

        ── Swabian Time Tagger example ───────────────────────────────────
        try:
            if getattr(self, '_tt_meas', None) is not None:
                del self._tt_meas
                self._tt_meas = None
            if getattr(self, '_tt', None) is not None:
                TimeTagger.freeTimeTagger(self._tt)
                self._tt = None
        except Exception as exc:
            self.log.warning(f"Time Tagger stop: {exc}")
        ──────────────────────────────────────────────────────────────────
        """
        # ── YOUR CODE HERE ────────────────────────────────────────────────
        self.log.debug("[PLACEHOLDER] _stop_photon_counter")
        # ─────────────────────────────────────────────────────────────────