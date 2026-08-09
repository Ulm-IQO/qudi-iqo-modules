# -*- coding: utf-8 -*-
"""
NI USB-63xx — Combined FastCounterInterface + DataInStreamInterface
                + Two-Channel Scanner Counter
=======================================================================

Two-channel photon counting extension
--------------------------------------
Set  photon_pfi2  in the YAML config to enable two-channel mode.
When photon_pfi2 is set, BOTH photon channels are used in every mode:

  Fast counter  (gated histograms):
    ctr0  photon1 period measurement  (inter-photon intervals)
    ctr1  gate edge timestamps        (absolute 100 MHz ticks per gate edge)
    ctr2  photon1 anchor              (freed after first photon1 — unchanged)
    ctr3  photon2 absolute timestamps (count-edges, clocked by photon2_pfi)

    Photon2 uses the count-edges / rollover-correction approach (same as the
    gate counter, ctr1) rather than period measurement + anchor.
    This gives absolute 100 MHz tick timestamps directly, with no anchor
    counter required.  The processor runs histogram_batch() independently
    for each channel against the shared gate timestamps.

    get_data_trace() returns a dict {ch_name: array} in two-channel mode
    and the original ndarray in single-channel mode (backward compatible).

  Scanning  (PI E-710 triggered pixel counting):
    ctr0  CI photon1        (edge counting, clocked by CO output)
    ctr1  CO 5 kHz clock    (triggered by PI gate RISING edge)
    ctr2  CI photon2        (edge counting, clocked by SAME CO output)
    Both CI tasks are synchronised to the same CO clock, guaranteeing
    perfect per-pixel alignment between channels.

  Instreamer  (time-series display):
    No code change needed.  Add both PFI terminals to digital_sources
    in the YAML config and they appear as independent channels.

Counter budget summary
----------------------
  One-channel mode:
    Fast counter active : ctr0, ctr1, ctr2 (freed early), (ctr3 = instreamer)
    Scanning active     : ctr0, ctr1,                      (ctr3 = instreamer)
    Instreamer only     : ctr3 (clock)

  Two-channel mode:
    Fast counter active : ctr0, ctr1, ctr2 (freed early), ctr3
    Scanning active     : ctr0, ctr1, ctr2,               (ctr3 = instreamer)
    Instreamer only     : ctr3 (clock)

Priority: fast counter > scanning > instreamer.
  start_measure() stops any active scan tasks first.
  arm()          stops instreamer tasks, saves state, restores after read/stop.

YAML configuration example (two-channel)
-----------------------------------------
hardware:
  ni_combined:
    module.Class: 'ni_x_series.ni_x_series_counter.NIXSeriesCounter'
    options:
      device_name:             'Dev1'
      photon_pfi:              'PFI8'    # APD1 input
      photon_pfi2:             'PFI9'    # APD2 input (enables two-channel mode)
      gate_pfi:                'PFI10'   # gate / excitation pulse input
      diag_enabled:            false
      diag_interval_s:         2.0
      sample_rate:             10.0
      channel_buffer_size:     10000
      digital_sources:
        - 'PFI8'
        - 'PFI9'
      adc_voltage_range:       [-10, 10]
      read_write_timeout:      10
      scan_counter_channel:    'ctr0'    # CI1 counter for scanning (photon1)
      scan_counter_channel_2:  'ctr2'    # CI2 counter for scanning (photon2)
      scan_clock_counter:      'ctr1'    # CO counter for 5 kHz scan clock
      scan_trigger_terminal:   'PFI1'    # PI E-710 gate output
      scan_apd_terminal:       'PFI8'    # scan APD1 (defaults to photon_pfi)
      scan_apd_terminal_2:     'PFI9'    # scan APD2 (defaults to photon_pfi2)
      scan_channel_name:       'APD1'    # channel name in confocal GUI
      scan_channel_name_2:     'APD2'    # channel name in confocal GUI
      scan_read_timeout:       30.0
"""

import collections
import ctypes
import os
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple, Union
from functools import wraps

import numpy as np
import nidaqmx as ni
from nidaqmx._lib import lib_importer
from nidaqmx.stream_readers import CounterReader
from nidaqmx.stream_readers import AnalogMultiChannelReader as _AnalogMultiChannelReader
from nidaqmx.constants import FillMode, READ_ALL_AVAILABLE
try:
    from nidaqmx._task_modules.read_functions import _read_analog_f_64
except ImportError:
    _read_analog_f_64 = None

from qudi.core.configoption import ConfigOption
from qudi.util.helpers import natural_sort
from qudi.util.constraints import ScalarConstraint
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.interface.data_instream_interface import (
    DataInStreamConstraints,
    DataInStreamInterface,
    SampleTiming,
    StreamingMode,
)


# ══════════════════════════════════════════════════════════════════════════════
#  DAQmx integer constants (from NIDAQmx.h)
# ══════════════════════════════════════════════════════════════════════════════
DAQmx_Val_Rising      = 10280   # active / sample on rising edge
DAQmx_Val_CountUp     = 10128   # counter counts upward
DAQmx_Val_ContSamps   = 10123   # continuous (not finite) acquisition
DAQmx_Val_DigEdge     = 10150   # digital-edge trigger type
DAQmx_Val_Ticks       = 10304   # measurement unit: 100 MHz timebase ticks
DAQmx_Val_LowFreq1Ctr = 10105   # period-measurement method (LowFreq1Counter)

# NI USB-63xx hardware timebase: 100 MHz internal oscillator.
_TIMEBASE_HZ = 100e6            # Hz
_TICK_NS     = 1e9 / _TIMEBASE_HZ   # one tick = 10 nanoseconds

# Extra time (in 100 MHz ticks) added to the last-gate-close timestamp before
# the processor commits a histogram batch.  Gives slow photon reader threads
# time to deliver the last few photons that arrived just before gate close.
PHOTON_SLACK_TICKS = np.uint64(10_000)   # 100 µs

# Upper-bound estimates used for NI hardware ring-buffer sizing.
_MAX_PHOTON_RATE_HZ = 10_000_000   # 10 MHz per channel
_MAX_GATE_RATE_HZ   = 10_000_000   # 10 MHz gate rate

# Fixed channel names for the two fast-counter rate channels that always
# appear as the first two channels in the unified DataInStreamInterface layout.
_CH_ALL      = 'rate_all_hz'    # photons/s for all photons in processed windows
_CH_GATED    = 'rate_gated_hz'  # photons/s normalised to gate-open time only
_FC_CHANNELS = (_CH_ALL, _CH_GATED)

# DataInStreamInterface sample-rate limits (Hz).
_SAMPLE_RATE_MIN =   1.0
_SAMPLE_RATE_MAX = 100.0
_SAMPLE_RATE_DEF =  10.0

# NI counter assignments.  ctr0–ctr2 are reserved by the fast counter while
# it is running.  ctr3 is the instreamer sample-clock (when FC is idle and
# scan is not active) OR the photon2 absolute-timestamp counter (when FC is
# running in two-channel mode).
_FC_COUNTERS      = ('ctr0', 'ctr1', 'ctr2')
_INSTREAM_CLK_CTR = 'ctr3'

# PI E-710 waveform generator sample rate (Hz).
# One NI scan counter sample is collected per PI waveform step.
# Must match PIE710Controller.SAMP_RATE in pi_e710_scanning_probe.py.
_PI_SAMP_RATE: float = 5000.0


# ══════════════════════════════════════════════════════════════════════════════
#  Patched AnalogMultiChannelReader
#  Compatible with multiple nidaqmx package versions (pre- and post-
#  interpreter-refactor).  Tries the newer interpreter API first and falls
#  back to the direct C-function wrapper if the attribute is absent.
# ══════════════════════════════════════════════════════════════════════════════
class _PatchedAnalogReader(_AnalogMultiChannelReader):
    """AnalogMultiChannelReader compatible with multiple nidaqmx versions."""
    @wraps(_AnalogMultiChannelReader.read_many_sample)
    def read_many_sample(self, data,
                         number_of_samples_per_channel=READ_ALL_AVAILABLE,
                         timeout=10.0):
        number_of_samples_per_channel = (
            self._task._calculate_num_samps_per_chan(
                number_of_samples_per_channel)
        )
        self._verify_array(data, number_of_samples_per_channel, False, True)
        try:
            _, samps_per_chan_read = self._interpreter.read_analog_f64(
                self._handle,
                number_of_samples_per_channel,
                timeout,
                FillMode.GROUP_BY_SCAN_NUMBER.value,
                data,
            )
        except AttributeError:
            samps_per_chan_read = _read_analog_f_64(
                self._handle, data,
                number_of_samples_per_channel, timeout,
                fill_mode=FillMode.GROUP_BY_SCAN_NUMBER,
            )
        return samps_per_chan_read


# ══════════════════════════════════════════════════════════════════════════════
#  NIXSeriesCounter
# ══════════════════════════════════════════════════════════════════════════════
class NIXSeriesCounter(FastCounterInterface, DataInStreamInterface):
    """
    Combined Qudi hardware module for the NI USB-63xx (6323 / 6343 / 6363).

    Implements:
      FastCounterInterface      — time-resolved gated photon counting
      DataInStreamInterface     — mixed analog/digital streaming (time series)
      Scanning counter interface — triggered pixel-by-pixel photon counting
                                   for use with PIE710CounterInterfuse

    Single-channel vs two-channel mode
    ------------------------------------
    Set photon_pfi2 in the YAML config to enable two-channel mode.
    In single-channel mode all two-channel code is dormant; behaviour is
    identical to the original single-channel implementation.

    See module docstring for full counter-budget and wiring details.
    """

    # ── Original config options ────────────────────────────────────────────────
    _device_name          = ConfigOption('device_name',          'Dev2',           missing='warn')
    _photon_pfi_line      = ConfigOption('photon_pfi',           'PFI0',           missing='warn')
    _gate_pfi_line        = ConfigOption('gate_pfi',             'PFI1',           missing='warn')
    _diag_enabled         = ConfigOption('diag_enabled',         True,             missing='warn')
    _diag_interval_s      = ConfigOption('diag_interval_s',      2.0,              missing='warn')
    _cfg_sample_rate      = ConfigOption('sample_rate',          _SAMPLE_RATE_DEF, missing='info')
    _cfg_channel_buf_size = ConfigOption('channel_buffer_size',  100,              missing='info')
    _cfg_digital_sources  = ConfigOption('digital_sources',      [],               missing='info')
    _cfg_analog_sources   = ConfigOption('analog_sources',       [],               missing='info')
    _cfg_adc_range        = ConfigOption('adc_voltage_range',    [-10, 10],        missing='info')
    _cfg_max_hw_buf       = ConfigOption(
        'max_channel_samples_buffer', 1024**2, missing='info',
        constructor=lambda x: max(int(round(x)), 1024**2))
    _cfg_rw_timeout       = ConfigOption('read_write_timeout',   10,               missing='nothing')

    # ── Two-channel photon config options ─────────────────────────────────────
    _photon_pfi_line2  = ConfigOption(
        'photon_pfi2', None, missing='nothing')

    # ── Scanning config options ────────────────────────────────────────────────
    _scan_counter_ch   = ConfigOption(
        'scan_counter_channel',   'ctr0', missing='nothing')
    _scan_counter_ch2  = ConfigOption(
        'scan_counter_channel_2', 'ctr2', missing='nothing')
    _scan_clock_ctr    = ConfigOption(
        'scan_clock_counter',     'ctr1', missing='nothing')
    _scan_trigger_term = ConfigOption(
        'scan_trigger_terminal',  'PFI1', missing='warn')
    _scan_apd_term     = ConfigOption(
        'scan_apd_terminal',   None,   missing='nothing')
    _scan_apd_term2    = ConfigOption(
        'scan_apd_terminal_2', None,   missing='nothing')
    _scan_ch_name      = ConfigOption(
        'scan_channel_name',   'APD1', missing='nothing')
    _scan_ch_name2     = ConfigOption(
        'scan_channel_name_2', 'APD2', missing='nothing')
    _scan_rw_timeout   = ConfigOption(
        'scan_read_timeout',   30.0,   missing='nothing')

    # ── Fast-counter state-machine status codes ───────────────────────────────
    STATUS_UNCONFIGURED = 0
    STATUS_IDLE         = 1
    STATUS_RUNNING      = 2
    STATUS_PAUSED       = 3
    STATUS_ERROR        = -1

    # ══════════════════════════════════════════════════════════════════════════
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Hardware terminal byte-strings — built in on_activate().
        self._device        = None   # e.g. b"Dev1"
        self._photon_pfi    = None   # e.g. b"/Dev1/PFI8"
        self._photon2_pfi   = None   # e.g. b"/Dev1/PFI9" (None in single-channel)
        self._gate_pfi      = None   # e.g. b"/Dev1/PFI10"
        self._timebase_term = None   # e.g. b"/Dev1/100MHzTimebase"

        self._max_photon_rate = float(_MAX_PHOTON_RATE_HZ)
        self._max_gate_rate   = float(_MAX_GATE_RATE_HZ)

        # ── Two-channel mode flags (resolved in on_activate) ──────────────────
        # _two_channel_fc   : True when photon_pfi2 is set → use ctr3 for photon2
        # _two_channel_scan : True when a second APD terminal can be resolved
        self._two_channel_fc             = False
        self._two_channel_scan           = False
        self._scan_apd_term2_resolved    = None   # resolved second APD PFI string

        # ── Fast-counter timing parameters (set by _fc_configure) ────────────
        self._gate_width_s        = None
        self._num_gates_per_cycle = None
        self._gate_ticks          = None
        self._n_bins              = None

        self._photon_buffer = None   # hardware ring-buffer depth for ctr0/ctr3
        self._gate_buffer   = None   # hardware ring-buffer depth for ctr1
        self._photon_chunk  = None   # max samples per read (ctr0/ctr3)
        self._gate_chunk    = None   # max samples per read (ctr1)

        self._status = self.STATUS_UNCONFIGURED

        # ctypes DAQmx task handles — None when not running.
        self._photon_task  = None   # ctr0 — photon1 period measurement
        self._gate_task    = None   # ctr1 — gate edge absolute timestamps
        self._anchor_task  = None   # ctr2 — photon1 anchor (freed early)
        self._photon2_task = None   # ctr3 — photon2 absolute timestamps (two-ch)

        # Software queues between reader threads and the processor thread.
        # Each reader appends uint64 numpy arrays; the processor swaps them.
        self._photon_list  = []
        self._gate_list    = []
        self._photon2_list = []   # absolute photon2 ticks (two-channel)
        self._photon_lock  = threading.Lock()
        self._gate_lock    = threading.Lock()
        self._photon2_lock = threading.Lock()

        # 2-D histogram accumulators — shape (num_gates_per_cycle, n_bins).
        # Preserved across pause/continue so data accumulates across segments.
        self._accumulator  = None   # photon1 histogram
        self._accumulator2 = None   # photon2 histogram (None in single-channel)

        self._t_start_ref    = [0.0]   # wall-clock time of the last DAQmxStartTask
        self._elapsed_time_s = 0.0     # total acquisition time across all segments

        # Running photon counters for the DataInStreamInterface rate display.
        self._photon_count_ref        = [0]   # all photon1 in processed windows
        self._gated_photon_count_ref  = [0]   # photon1 inside gate windows
        self._photon_count_lock       = threading.Lock()

        self._default_rate_reader = None

        # ── Diagnostics counters ───────────────────────────────────────────────
        self._diag_lock = threading.Lock()
        # photon1 pipeline counters
        self._diag_reader_photons_ref   = [0]
        self._diag_proc_photons_ref     = [0]
        self._diag_hist_photons_ref     = [0]
        self._diag_proc_cycles_ref      = [0]
        self._diag_hist_cycles_ref      = [0]
        self._diag_leftover_photons_ref = [0]
        self._diag_leftover_gates_ref   = [0]
        # gate pipeline counter
        self._diag_reader_gates_ref     = [0]
        # photon2 pipeline counters (two-channel mode only; always allocated
        # but only updated when _two_channel_fc is True)
        self._diag_reader_photons2_ref  = [0]
        self._diag_proc_photons2_ref    = [0]
        self._diag_hist_photons2_ref    = [0]
        self._diag_leftover_photons2_ref = [0]
        self._diag_snap = {
            'time': 0.0,
            'reader_photons': 0, 'reader_photons2': 0, 'reader_gates': 0,
            'proc_photons': 0,   'proc_photons2': 0,
            'hist_photons': 0,   'hist_photons2': 0,
            'proc_cycles': 0,    'hist_cycles': 0,
        }

        # Worker thread handles.
        self._photon_thread    = None
        self._gate_thread      = None
        self._anchor_thread    = None
        self._photon2_thread   = None   # reader thread for photon2 (two-channel)
        self._processor_thread = None
        self._diag_thread      = None

        # Stop events — set to ask a thread to exit its loop cleanly.
        self._photon_stop     = None
        self._gate_stop       = None
        self._anchor_stop     = None
        self._photon2_stop    = None
        self._processor_stop  = None
        self._diag_stop       = None
        # Overflow events — set by a reader thread on a fatal hardware error.
        self._photon_overflow  = None
        self._gate_overflow    = None
        self._anchor_overflow  = None
        self._photon2_overflow = None

        # Anchor synchronisation for photon1.
        # The anchor thread sets _t1_abs_ready after writing t1_abs_ref[0].
        # The photon1 reader blocks on this event before emitting timestamps.
        self._t1_abs_ref   = [np.uint64(0)]
        self._t1_abs_ready = threading.Event()

        # Handle to the NI-DAQmx C library loaded via ctypes.
        self._nidaq = None

        # ── Instreamer (nidaqmx Python library) state ─────────────────────────
        self._digital_sources = []
        self._analog_sources  = []
        self._all_channels    = list(_FC_CHANNELS)

        self._ni_clk_task    = None
        self._ni_di_tasks    = []
        self._ni_di_readers  = []
        self._ni_ai_task     = None
        self._ni_ai_reader   = None
        self._ni_tasks_lock  = threading.Lock()

        self._ni_tasks_running = False

        self._instream_constraints = None
        self._sample_rate          = _SAMPLE_RATE_DEF
        self._channel_buffer_size  = 100
        self._active_channels      = list(_FC_CHANNELS)
        self._streaming_mode       = StreamingMode.CONTINUOUS

        self._ring_buffer = collections.deque()
        self._ring_lock   = threading.Lock()

        self._poll_thread      = None
        self._poll_stop        = threading.Event()
        self._stream_lock      = threading.Lock()
        self._streaming        = False
        self._poll_rate_reader = None

        # ── Scanning counter state ────────────────────────────────────────────
        # _scan_lock is a reentrant lock (RLock) so that read()'s finally block
        # can call _scan_cleanup_unsafe → _ni_start_tasks on the same thread
        # without deadlocking.
        self._scan_lock          = threading.RLock()

        self._scan_task          = None   # CI task for photon1 (ctr0)
        self._scan_co_task       = None   # CO task for scan clock (ctr1)
        self._scan_task2         = None   # CI task for photon2 (ctr2, two-ch)
        self._scan_reader        = None   # CounterReader for CI task 1
        self._scan_reader2       = None   # CounterReader for CI task 2 (two-ch)
        self._scan_n_steps       = 1      # PI waveform steps per pixel
        self._scan_n_pixels      = 0      # pixels per scan line
        self._scan_was_streaming = False  # instreamer running state before arm()
        # _scan_active is a plain bool (not behind _scan_lock) so that
        # _ni_start_tasks can read it without acquiring any lock.
        # Written only while _scan_lock is held; read safely under the GIL.
        self._scan_active        = False

    # ══════════════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def on_activate(self):
        """
        Load the ctypes DAQmx library, validate all config options, reset the
        NI device to a clean state, and build DataInStreamInterface constraints.

        Two-channel mode is enabled automatically if photon_pfi2 is set.
        After returning the module is in STATUS_UNCONFIGURED.
        """
        device_name = self._device_name

        # Build byte-string terminal names used by the ctypes DAQmx C API.
        self._device        = device_name.encode()
        self._photon_pfi    = f'/{device_name}/{self._photon_pfi_line}'.encode()
        self._gate_pfi      = f'/{device_name}/{self._gate_pfi_line}'.encode()
        self._timebase_term = f'/{device_name}/100MHzTimebase'.encode()

        # Resolve two-channel mode for fast counting.
        if self._photon_pfi_line2:
            self._photon2_pfi    = f'/{device_name}/{self._photon_pfi_line2}'.encode()
            self._two_channel_fc = True
        else:
            self._photon2_pfi    = None
            self._two_channel_fc = False

        # Resolve second APD terminal for scanning.
        # Priority: explicit scan_apd_terminal_2 > photon_pfi2 > disabled.
        apd2_term = self._scan_apd_term2 or self._photon_pfi_line2
        if apd2_term:
            self._scan_apd_term2_resolved = apd2_term
            self._two_channel_scan        = True
        else:
            self._scan_apd_term2_resolved = None
            self._two_channel_scan        = False

        # Validate that scan counter channels are all distinct.
        ctrs = [self._scan_counter_ch, self._scan_clock_ctr]
        if self._two_channel_scan:
            ctrs.append(self._scan_counter_ch2)
        if len(set(c.lower() for c in ctrs)) != len(ctrs):
            raise ValueError(
                f'Scan counter channels must all be distinct. '
                f'Got: {ctrs}'
            )

        # Load the ctypes NI-DAQmx library and perform a device reset.
        self._nidaq = self._load_nidaq()
        self._declare_argtypes()
        try:
            self._check(self._nidaq.DAQmxResetDevice(self._device))
        except RuntimeError as e:
            self._nidaq = None
            raise RuntimeError(
                f"on_activate: failed to reset '{device_name}'. "
                f"Check USB and NI-DAQmx driver.\n{e}"
            ) from e

        # Use the nidaqmx Python library to enumerate terminals and validate
        # the digital/analog source lists from config.
        ni_device    = ni.system.Device(device_name)
        all_di_terms = tuple(
            t.rsplit('/', 1)[-1].lower()
            for t in ni_device.terminals if 'PFI' in t
        )
        all_ai_terms = tuple(
            t.rsplit('/', 1)[-1].lower()
            for t in ni_device.ai_physical_chans.channel_names
        )

        def _normalise(sources, valid_set, kind):
            out = []
            for src in sources:
                norm = src.strip('/').lower()
                if 'dev' in norm:
                    norm = norm.split('/', 1)[-1]
                if norm in valid_set:
                    out.append(norm)
                else:
                    self.log.warning(
                        f'on_activate: invalid {kind} source "{src}" ignored.')
            return natural_sort(out)

        self._digital_sources = _normalise(
            list(self._cfg_digital_sources), set(all_di_terms), 'digital')
        self._analog_sources  = _normalise(
            list(self._cfg_analog_sources),  set(all_ai_terms), 'analog')

        if len(self._digital_sources) > 3:
            self.log.warning('on_activate: >3 digital sources; only first 3 used.')
            self._digital_sources = self._digital_sources[:3]
        if len(self._analog_sources) > 16:
            self.log.warning('on_activate: >16 analog sources; only first 16 used.')
            self._analog_sources = self._analog_sources[:16]

        self._all_channels = (list(_FC_CHANNELS)
                              + self._digital_sources
                              + self._analog_sources)

        # Build DataInStreamInterface constraints.
        channel_units = {ch: 'counts/s' for ch in _FC_CHANNELS}
        channel_units.update({ch: 'counts/s' for ch in self._digital_sources})
        channel_units.update({ch: 'V'         for ch in self._analog_sources})

        sr_min = (max(_SAMPLE_RATE_MIN, float(ni_device.ai_min_rate))
                  if self._analog_sources else _SAMPLE_RATE_MIN)
        sr_max = (min(_SAMPLE_RATE_MAX, float(ni_device.ai_max_multi_chan_rate))
                  if self._analog_sources else _SAMPLE_RATE_MAX)

        self._instream_constraints = DataInStreamConstraints(
            channel_units=channel_units,
            sample_timing=SampleTiming.CONSTANT,
            streaming_modes=[StreamingMode.CONTINUOUS],
            data_type=np.float64,
            channel_buffer_size=ScalarConstraint(
                default=self._cfg_channel_buf_size,
                bounds=(2, self._cfg_max_hw_buf),
                increment=1, enforce_int=True,
            ),
            sample_rate=ScalarConstraint(
                default=float(np.clip(self._cfg_sample_rate, sr_min, sr_max)),
                bounds=(sr_min, sr_max),
                increment=0.1, enforce_int=False,
            ),
        )

        self._sample_rate         = float(np.clip(
            self._cfg_sample_rate, sr_min, sr_max))
        self._channel_buffer_size = max(2, int(self._cfg_channel_buf_size))
        self._active_channels     = list(self._all_channels)
        self._streaming_mode      = StreamingMode.CONTINUOUS

        self._status = self.STATUS_UNCONFIGURED
        self._init_default_rate_reader()

        clock_num = ''.join(filter(str.isdigit, self._scan_clock_ctr))
        self.log.info(
            f'NIXSeriesCounter ready -- '
            f'device={device_name}  '
            f'photon1={self._photon_pfi_line}  '
            f'photon2={"DISABLED" if not self._photon_pfi_line2 else self._photon_pfi_line2}  '
            f'gate={self._gate_pfi_line}  '
            f'two_channel_fc={self._two_channel_fc}  '
            f'two_channel_scan={self._two_channel_scan}  '
            f'scan CI1={self._scan_counter_ch}  '
            f'scan CI2={"N/A" if not self._two_channel_scan else self._scan_counter_ch2}  '
            f'scan CO={self._scan_clock_ctr} (Ctr{clock_num}InternalOutput)  '
            f'scan gate={self._scan_trigger_term}'
        )

    def on_deactivate(self):
        """
        Stop all running tasks in priority order (scan > stream > FC), reset
        the NI device and release the ctypes library handle.
        Safe to call from any module state.
        """
        # Stop scan tasks first (highest-priority cleanup path)
        if self._scan_task is not None or self._scan_co_task is not None:
            try:
                with self._scan_lock:
                    self._scan_cleanup_unsafe(restart_stream=False)
            except Exception as e:
                self.log.warning(f'on_deactivate: scan cleanup warning: {e}')

        if self._streaming:
            try:
                self.stop_stream()
            except Exception as e:
                self.log.warning(f'on_deactivate: stream stop warning: {e}')

        if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED,
                            self.STATUS_ERROR):
            try:
                self._stop_hardware_and_threads()
            except Exception as e:
                self.log.warning(f'on_deactivate: FC cleanup warning: {e}')

        self._ni_stop_tasks()

        if self._nidaq is not None:
            try:
                self._nidaq.DAQmxResetDevice(self._device)
            except Exception as e:
                self.log.warning(f'on_deactivate: device reset warning: {e}')
        self._nidaq = None
        self._status = self.STATUS_UNCONFIGURED

    # ══════════════════════════════════════════════════════════════════════════
    #  FastCounterInterface
    # ══════════════════════════════════════════════════════════════════════════

    def get_constraints(self):
        """Return hardware capability limits required by FastCounterInterface."""
        return {
            'hardware_binwidth_list': [_TICK_NS * 1e-9],
            'max_sweep_len': {
                'min': _TICK_NS * 1e-9, 'max': 1.0,
                'step': _TICK_NS * 1e-9, 'unit': 's',
            },
            'max_bins': {
                'min': 1, 'max': 2**31 - 1, 'step': 1, 'unit': 'bins',
            },
        }

    def configure(self, bin_width_s=None, record_length_s=None,
                  number_of_gates=0, active_channels=None,
                  streaming_mode=None, channel_buffer_size=None,
                  sample_rate=None):
        """
        Unified configure() dispatcher.

        FastCounterInterface call  (positional / keyword):
            configure(bin_width_s, record_length_s, number_of_gates=0)
        DataInStreamInterface call (keyword-only):
            configure(active_channels=..., streaming_mode=...,
                      channel_buffer_size=..., sample_rate=...)
        """
        if bin_width_s is not None and isinstance(bin_width_s, (int, float)):
            return self._fc_configure(bin_width_s, record_length_s, number_of_gates)
        if active_channels is not None:
            return self._is_configure(active_channels, streaming_mode,
                                      channel_buffer_size, sample_rate)
        raise TypeError(
            'configure() needs (bin_width_s, record_length_s) for the fast '
            'counter, or keyword args (active_channels, ...) for the instreamer.')

    def _fc_configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """
        FastCounterInterface configure() implementation.

        Rounds parameters to 10 ns boundaries, initialises (or resets) the
        histogram accumulator(s), and transitions to STATUS_IDLE.

        In two-channel mode both accumulators (_accumulator and _accumulator2)
        are sized identically.  Each processor batch will update both.
        """
        if self._status == self.STATUS_RUNNING:
            raise RuntimeError(
                'Cannot reconfigure while running.  Call stop_measure() first.')

        ticks_per_bin          = max(1, int(round(bin_width_s * _TIMEBASE_HZ)))
        actual_bin_width_s     = ticks_per_bin / _TIMEBASE_HZ
        gate_ticks             = max(ticks_per_bin,
                                     int(round(record_length_s * _TIMEBASE_HZ)))
        actual_record_length_s = gate_ticks / _TIMEBASE_HZ
        num_gates              = max(1, int(number_of_gates))

        self._gate_width_s        = actual_record_length_s
        self._num_gates_per_cycle = num_gates
        self._gate_ticks          = gate_ticks
        self._n_bins              = gate_ticks

        self._photon_buffer = max(1_000_000, int(self._max_photon_rate * 10))
        self._gate_buffer   = max(200_000,   int(self._max_gate_rate   * 2))
        read_time_s         = 0.02
        self._photon_chunk  = int(self._max_photon_rate * read_time_s)
        self._gate_chunk    = int(self._max_gate_rate   * read_time_s)

        # Allocate / resize photon1 accumulator.
        shape = (num_gates, gate_ticks)
        if self._accumulator is None or self._accumulator.shape != shape:
            self._accumulator = np.zeros(shape, dtype=np.uint64)

        # Allocate / resize photon2 accumulator (two-channel mode only).
        if self._two_channel_fc:
            if self._accumulator2 is None or self._accumulator2.shape != shape:
                self._accumulator2 = np.zeros(shape, dtype=np.uint64)
        else:
            self._accumulator2 = None

        self._reset_run_state()
        self._status = self.STATUS_IDLE
        return actual_bin_width_s, actual_record_length_s, num_gates

    def get_status(self):
        """
        Return the current state-machine code.
        Polls overflow events so hardware errors are reflected immediately.
        """
        if self._status == self.STATUS_RUNNING:
            ov = [self._photon_overflow, self._gate_overflow, self._anchor_overflow]
            if self._two_channel_fc:
                ov.append(self._photon2_overflow)
            if any(ev and ev.is_set() for ev in ov):
                self._status = self.STATUS_ERROR
        return self._status

    def start_measure(self):
        """
        Arm the fast counter.  Must be called from STATUS_IDLE.

        Priority rule: fast counter > scanning.  If scan tasks are currently
        active they are aborted first with a warning, freeing ctr0–ctr2 (and
        ctr3 in two-channel mode) for the fast counter.
        Transitions to STATUS_RUNNING.
        """
        if self._status != self.STATUS_IDLE:
            raise RuntimeError(
                f'start_measure() in invalid state {self._status}.  '
                'Call configure() first, or stop_measure() if running.')

        # Abort any active scan (fast counter has absolute priority).
        if self._scan_active or self._scan_task is not None:
            self.log.warning(
                'start_measure(): scanner counter tasks are active — '
                'aborting scan first.')
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=False)

        # Release instreamer counter resources before arming the fast counter.
        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def stop_measure(self):
        """
        Stop the fast counter, print a summary, reset all accumulators, and
        restart instreamer tasks (if the stream is active).
        Safe to call from any active state.  Transitions to STATUS_IDLE.
        Call get_data_trace() BEFORE stop_measure() to preserve data.
        """
        if self._status in (self.STATUS_UNCONFIGURED, self.STATUS_IDLE):
            return
        self._stop_hardware_and_threads()
        if self._t_start_ref[0] > 0:
            self._elapsed_time_s += time.monotonic() - self._t_start_ref[0]
            self._t_start_ref[0] = 0.0
        if self._diag_enabled:
            self.print_summary()
        self._reset_run_state()
        self._status = self.STATUS_IDLE
        # Fast counter has released ctr0–ctr2 (and ctr3 in two-channel mode).
        # Restart instreamer tasks so the time-series display resumes.
        if self._streaming:
            self._ni_start_tasks()

    def pause_measure(self):
        """
        Stop hardware without resetting the accumulator(s).
        Restarts instreamer tasks (if stream is active).
        Transitions to STATUS_PAUSED.
        """
        if self._status != self.STATUS_RUNNING:
            raise RuntimeError(
                f'pause_measure() in invalid state {self._status}.  '
                'Must be running.')
        self._stop_hardware_and_threads()
        if self._t_start_ref[0] > 0:
            self._elapsed_time_s += time.monotonic() - self._t_start_ref[0]
            self._t_start_ref[0] = 0.0
        self._status = self.STATUS_PAUSED
        if self._streaming:
            self._ni_start_tasks()

    def continue_measure(self):
        """
        Resume a paused acquisition (accumulator is preserved).
        Tears down instreamer tasks again, then re-arms the fast counter.
        Transitions to STATUS_RUNNING.
        """
        if self._status != self.STATUS_PAUSED:
            raise RuntimeError(
                f'continue_measure() in invalid state {self._status}.  '
                'Must be paused.')
        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def is_gated(self):
        """Return True — this module always operates in gated mode."""
        return True

    def get_binwidth(self):
        """Return the histogram bin width in seconds (10 ns = one 100 MHz tick).
        Returns None if configure() has not been called yet."""
        return (1.0 / _TIMEBASE_HZ) if self._gate_ticks is not None else None

    def get_data_trace(self):
        """
        Return the accumulated histogram(s) and metadata.

        Single-channel mode
        -------------------
        Returns:
            (data, info_dict)
            data      : int64 ndarray, shape (num_gates_per_cycle, n_bins)
            info_dict : {'elapsed_sweeps': int, 'elapsed_time': float}

        Two-channel mode
        ----------------
        Returns:
            (data, info_dict)
            data : int64 ndarray, shape (2, num_gates_per_cycle, n_bins)
                data[0] = photon1 histogram (APD1)
                data[1] = photon2 histogram (APD2)
            info_dict : {'elapsed_sweeps': int, 'elapsed_time': float}

        In both cases data is always a numpy ndarray so that existing logic
        code (e.g. pulsed_measurement_logic.py) can call .any() on the result.
        Single-channel return shape is unchanged -- fully backward compatible.
        Two-channel consumers should check data.ndim == 3 to detect the format.
        """
        if self._accumulator is None:
            empty = np.zeros((1, 1), dtype=np.int64)
            info  = {'elapsed_sweeps': 0, 'elapsed_time': 0.0}
            if self._two_channel_fc:
                # Stack two empty arrays so shape is (2, 1, 1).
                return np.stack([empty, empty], axis=0), info
            return empty, info

        elapsed = self._elapsed_time_s
        if self._status == self.STATUS_RUNNING and self._t_start_ref[0] > 0:
            elapsed += time.monotonic() - self._t_start_ref[0]

        info_dict = {
            'elapsed_sweeps': self._diag_hist_cycles_ref[0],
            'elapsed_time':   elapsed,
        }

        if self._two_channel_fc and self._accumulator2 is not None:
            # Two-channel: stack histograms along a new first axis.
            # Shape: (2, num_gates_per_cycle, n_bins)
            # Callers check data.ndim == 3 to distinguish from single-channel.
            data = np.stack([
                self._accumulator.astype(np.int64).copy(),
                self._accumulator2.astype(np.int64).copy(),
            ], axis=0)
        else:
            # Single-channel: shape (num_gates_per_cycle, n_bins).
            # Identical to original behaviour -- pulsed logic works unchanged.
            data = self._accumulator.astype(np.int64).copy()

        return data, info_dict

    # ══════════════════════════════════════════════════════════════════════════
    #  DataInStreamInterface — properties
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def constraints(self) -> DataInStreamConstraints:
        return self._instream_constraints

    @property
    def available_samples(self) -> int:
        with self._ring_lock:
            return len(self._ring_buffer)

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @property
    def channel_buffer_size(self) -> int:
        return self._channel_buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        return self._streaming_mode

    @property
    def active_channels(self) -> List[str]:
        return list(self._active_channels)

    # ══════════════════════════════════════════════════════════════════════════
    #  DataInStreamInterface — configure / start / stop / read
    # ══════════════════════════════════════════════════════════════════════════

    def _is_configure(self, active_channels, streaming_mode,
                      channel_buffer_size, sample_rate):
        """DataInStreamInterface configure() implementation."""
        if self._streaming:
            raise RuntimeError(
                'Cannot configure instreamer while running.  '
                'Call stop_stream() first.')
        streaming_mode = StreamingMode(streaming_mode)
        if streaming_mode not in self._instream_constraints.streaming_modes:
            raise ValueError(f'Invalid streaming mode "{streaming_mode}".')
        invalid = set(active_channels) - set(self._all_channels)
        if invalid:
            raise ValueError(f'Invalid channels: {invalid}')
        self._instream_constraints.sample_rate.check(sample_rate)
        self._instream_constraints.channel_buffer_size.check(channel_buffer_size)
        fc_set = list(_FC_CHANNELS)
        extra  = [ch for ch in active_channels if ch not in fc_set]
        self._active_channels     = fc_set + extra
        self._streaming_mode      = streaming_mode
        self._sample_rate         = float(sample_rate)
        self._channel_buffer_size = int(channel_buffer_size)

    def start_stream(self) -> None:
        """
        Start the background poll thread.
        Starts nidaqmx instreamer tasks for digital/analog channels only if
        the fast counter is not running AND no scan tasks are active.
        """
        with self._stream_lock:
            if self._streaming:
                self.log.warning('start_stream() already running.')
                return

            self._poll_rate_reader = self.register_rate_reader()
            self._poll_stop.clear()
            with self._ring_lock:
                self._ring_buffer = collections.deque(
                    maxlen=self._channel_buffer_size)

            # Start nidaqmx tasks only when counters are free.
            if (self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED)
                    and not self._scan_active):
                self._ni_start_tasks()

            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True, name='instreamer-poll')
            self._poll_thread.start()
            self._streaming = True

    def stop_stream(self) -> None:
        """Stop the poll thread and tear down nidaqmx instreamer tasks."""
        with self._stream_lock:
            if not self._streaming:
                return
            self._poll_stop.set()
            if self._poll_thread is not None and self._poll_thread.is_alive():
                self._poll_thread.join(timeout=5.0)
            self._poll_thread = None
            self._ni_stop_tasks()
            self._streaming = False

    def read_data_into_buffer(self, data_buffer, samples_per_channel,
                              timestamp_buffer=None):
        """Block until samples_per_channel samples are available, then read."""
        if not self._streaming:
            raise RuntimeError('Stream is not running.')
        n_ch = len(self._active_channels)
        while True:
            with self._ring_lock:
                if len(self._ring_buffer) >= samples_per_channel:
                    break
            time.sleep(0.001)
        flat = data_buffer.ravel()
        with self._ring_lock:
            for i in range(samples_per_channel):
                sample = self._ring_buffer.popleft()
                for ch_idx, ch_name in enumerate(self._active_channels):
                    flat[i * n_ch + ch_idx] = sample[
                        self._all_channels.index(ch_name)]

    def read_available_data_into_buffer(self, data_buffer,
                                        timestamp_buffer=None):
        """Read all available samples into buffer.  Returns samples per ch."""
        n_ch    = len(self._active_channels)
        to_read = min(self.available_samples, data_buffer.size // n_ch)
        if to_read == 0:
            return 0
        self.read_data_into_buffer(data_buffer, to_read, timestamp_buffer)
        return to_read

    def read_data(self, samples_per_channel=None):
        """Allocate and return a buffer with the requested samples."""
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        n_ch = len(self._active_channels)
        buf  = np.empty(samples_per_channel * n_ch, dtype=np.float64)
        self.read_data_into_buffer(buf, samples_per_channel)
        return buf, None

    def read_single_point(self):
        """Return one sample per active channel.  Blocks until available."""
        if not self._streaming:
            raise RuntimeError('Stream is not running.')
        while self.available_samples == 0:
            time.sleep(0.001)
        n_ch = len(self._active_channels)
        buf  = np.empty(n_ch, dtype=np.float64)
        with self._ring_lock:
            sample = self._ring_buffer.popleft()
        for ch_idx, ch_name in enumerate(self._active_channels):
            buf[ch_idx] = sample[self._all_channels.index(ch_name)]
        return buf, None

    # ══════════════════════════════════════════════════════════════════════════
    #  nidaqmx instreamer task lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def _ni_start_tasks(self) -> None:
        """
        Build and start all nidaqmx instreamer tasks:
          1. CO pulse clock on ctr3 at self._sample_rate Hz.
          2. One CI period task per active digital channel.
          3. One AI voltage task for all active analog channels.

        Counter reservation logic
        --------------------------
        The _scan_active flag is read WITHOUT acquiring _scan_lock so that
        this method can be called safely from _scan_cleanup_unsafe (which
        holds _scan_lock).  Writing _scan_active is only ever done while
        _scan_lock is held, so reading it under the GIL is thread-safe.

        Fast-counter counters (ctr0–ctr2 and ctr3 in two-channel mode) are
        excluded from the free-counter pool when the FC is running/paused.
        Scan counters are excluded when a scan is active.
        """
        with self._ni_tasks_lock:
            if self._ni_tasks_running:
                return
            if not self._digital_sources and not self._analog_sources:
                return   # Rate-only channels need no nidaqmx tasks.

            dev = self._device_name
            clock_channel = None

            try:
                clk_task = ni.Task(f'NiUsb63xx_Clk_{id(self):d}')
                clk_task.co_channels.add_co_pulse_chan_freq(
                    f'/{dev}/{_INSTREAM_CLK_CTR}',
                    freq=self._sample_rate,
                    idle_state=ni.constants.Level.LOW,
                )
                clk_task.timing.cfg_implicit_timing(
                    sample_mode=ni.constants.AcquisitionType.CONTINUOUS)
                clk_task.control(ni.constants.TaskMode.TASK_RESERVE)
                self._ni_clk_task = clk_task
                clock_channel = f'/{clk_task.channel_names[0]}InternalOutput'
            except ni.DaqError as e:
                self.log.error(
                    f'_ni_start_tasks: clock task failed: {e}. '
                    'Digital/analog channels unavailable.')
                self._ni_stop_tasks_unsafe()
                return

            # Build the set of counters that must NOT be used for instreamer tasks.
            fc_active     = self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED)
            reserved_ctrs = (set(_FC_COUNTERS) if fc_active else set()) | {_INSTREAM_CLK_CTR}

            # In two-channel FC mode, ctr3 is used for photon2 while FC runs.
            if fc_active and self._two_channel_fc:
                reserved_ctrs.add('ctr3')

            # While a scan is active, reserve the scan counters.
            # Read _scan_active as a plain bool (no lock — see docstring above).
            if self._scan_active:
                reserved_ctrs |= {
                    self._scan_counter_ch.lower(),
                    self._scan_clock_ctr.lower(),
                }
                if self._two_channel_scan:
                    reserved_ctrs.add(self._scan_counter_ch2.lower())

            try:
                all_ctrs = tuple(
                    c.split('/')[-1]
                    for c in ni.system.Device(dev).co_physical_chans.channel_names
                    if 'ctr' in c.lower()
                )
            except Exception:
                all_ctrs = ()

            free_ctrs = [c for c in all_ctrs if c not in reserved_ctrs]

            # Create one CI period task per active digital channel.
            active_di = [ch for ch in self._digital_sources
                         if ch in self._active_channels]
            free_iter = iter(free_ctrs)
            for chnl in active_di:
                ctr = next(free_iter, None)
                if ctr is None:
                    self.log.warning(
                        f'_ni_start_tasks: no free counter for "{chnl}" -- zeros.')
                    continue
                ctr_full  = f'/{dev}/{ctr}'
                chnl_full = f'/{dev}/{chnl}'
                try:
                    task = ni.Task(f'NiUsb63xx_DI_{chnl}_{id(self):d}')
                    task.ci_channels.add_ci_period_chan(
                        ctr_full, min_val=0, max_val=100_000_000,
                        units=ni.constants.TimeUnits.TICKS,
                        edge=ni.constants.Edge.RISING,
                    )
                    try:
                        lib_importer.windll.DAQmxSetCIPeriodTerm(
                            task._handle,
                            ctypes.c_char_p(ctr_full.encode('ascii')),
                            ctypes.c_char_p(clock_channel.encode('ascii')))
                        lib_importer.windll.DAQmxSetCICtrTimebaseSrc(
                            task._handle,
                            ctypes.c_char_p(ctr_full.encode('ascii')),
                            ctypes.c_char_p(chnl_full.encode('ascii')))
                    except Exception:
                        lib_importer.cdll.DAQmxSetCIPeriodTerm(
                            task._handle,
                            ctypes.c_char_p(ctr_full.encode('ascii')),
                            ctypes.c_char_p(clock_channel.encode('ascii')))
                        lib_importer.cdll.DAQmxSetCICtrTimebaseSrc(
                            task._handle,
                            ctypes.c_char_p(ctr_full.encode('ascii')),
                            ctypes.c_char_p(chnl_full.encode('ascii')))
                    task.timing.cfg_implicit_timing(
                        sample_mode=ni.constants.AcquisitionType.CONTINUOUS,
                        samps_per_chan=self._channel_buffer_size,
                    )
                    task.control(ni.constants.TaskMode.TASK_RESERVE)
                    reader = CounterReader(task.in_stream)
                    reader.verify_array_shape = False
                    self._ni_di_tasks.append(task)
                    self._ni_di_readers.append(reader)
                except ni.DaqError as e:
                    self.log.warning(
                        f'_ni_start_tasks: DI task failed for {chnl}: {e}.')
                    try:
                        task.close()
                    except Exception:
                        pass

            # Create one AI task for all active analog channels.
            active_ai = [ch for ch in self._analog_sources
                         if ch in self._active_channels]
            if active_ai:
                ai_str = ','.join(f'/{dev}/{ch}' for ch in active_ai)
                try:
                    ai_task = ni.Task(f'NiUsb63xx_AI_{id(self):d}')
                    ai_task.ai_channels.add_ai_voltage_chan(
                        ai_str,
                        max_val=max(self._cfg_adc_range),
                        min_val=min(self._cfg_adc_range),
                    )
                    ai_task.timing.cfg_samp_clk_timing(
                        self._sample_rate, source=clock_channel,
                        active_edge=ni.constants.Edge.RISING,
                        sample_mode=ni.constants.AcquisitionType.CONTINUOUS,
                        samps_per_chan=self._channel_buffer_size,
                    )
                    ai_task.control(ni.constants.TaskMode.TASK_RESERVE)
                    self._ni_ai_reader = _PatchedAnalogReader(ai_task.in_stream)
                    self._ni_ai_reader.verify_array_shape = False
                    self._ni_ai_task = ai_task
                except ni.DaqError as e:
                    self.log.warning(f'_ni_start_tasks: AI task failed: {e}.')
                    try:
                        ai_task.close()
                    except Exception:
                        pass

            # Start digital and analog tasks first, then the clock.
            for task in self._ni_di_tasks:
                task.start()
            if self._ni_ai_task is not None:
                self._ni_ai_task.start()
            self._ni_clk_task.start()

            self._ni_tasks_running = True
            started_di = [ch for ch in self._digital_sources
                          if ch in self._active_channels][:len(self._ni_di_tasks)]
            started_ai = ([ch for ch in self._analog_sources
                           if ch in self._active_channels]
                          if self._ni_ai_task is not None else [])
            self.log.info(
                f'Instreamer tasks started at {self._sample_rate:.1f} Hz.  '
                f'Digital: {started_di or "none"}  '
                f'Analog: {started_ai or "none"}'
            )

    def _ni_stop_tasks(self) -> None:
        with self._ni_tasks_lock:
            self._ni_stop_tasks_unsafe()

    def _ni_stop_tasks_unsafe(self) -> None:
        """Stop and clear all nidaqmx instreamer tasks (must hold _ni_tasks_lock)."""
        if self._ni_tasks_running:
            self.log.info('Instreamer tasks stopped.')
        self._ni_di_readers = []
        self._ni_ai_reader  = None
        for task in self._ni_di_tasks:
            try:
                if not task.is_task_done():
                    task.stop()
                task.close()
            except Exception as e:
                self.log.warning(f'_ni_stop_tasks: DI task error: {e}')
        self._ni_di_tasks = []
        if self._ni_ai_task is not None:
            try:
                if not self._ni_ai_task.is_task_done():
                    self._ni_ai_task.stop()
                self._ni_ai_task.close()
            except Exception as e:
                self.log.warning(f'_ni_stop_tasks: AI task error: {e}')
            self._ni_ai_task = None
        if self._ni_clk_task is not None:
            try:
                if not self._ni_clk_task.is_task_done():
                    self._ni_clk_task.stop()
                self._ni_clk_task.close()
            except Exception as e:
                self.log.warning(f'_ni_stop_tasks: clock task error: {e}')
            self._ni_clk_task = None
        self._ni_tasks_running = False

    def _ni_read_sample(self) -> np.ndarray:
        """
        Read one sample from each active nidaqmx instreamer channel.
        Returns zeros for channels whose tasks are not running.
        Called from the poll thread — must not block for more than one interval.
        """
        n_di = len(self._digital_sources)
        n_ai = len(self._analog_sources)
        result = np.zeros(n_di + n_ai, dtype=np.float64)
        if not self._ni_tasks_running:
            return result
        try:
            _tmp = np.empty(self._channel_buffer_size, dtype=np.float64)
            for i, reader in enumerate(self._ni_di_readers):
                n = reader.read_many_sample_double(
                    _tmp,
                    number_of_samples_per_channel=ni.constants.READ_ALL_AVAILABLE,
                    timeout=0.0,
                )
                if n > 0:
                    result[i] = float(np.mean(_tmp[:n])) * self._sample_rate
            if self._ni_ai_reader is not None:
                _tmp_ai = np.empty(
                    self._channel_buffer_size * n_ai, dtype=np.float64)
                n = self._ni_ai_reader.read_many_sample(
                    _tmp_ai,
                    number_of_samples_per_channel=ni.constants.READ_ALL_AVAILABLE,
                    timeout=0.0,
                )
                if n > 0:
                    result[n_di:] = (
                        _tmp_ai[:n * n_ai].reshape(n, n_ai).mean(axis=0))
        except Exception as e:
            self.log.warning(f'_ni_read_sample: {e}')
        return result

    # ══════════════════════════════════════════════════════════════════════════
    #  Background poll thread
    # ══════════════════════════════════════════════════════════════════════════

    def _poll_loop(self) -> None:
        """
        Background thread running at sample_rate Hz.

        Assembles one unified sample vector per tick:
          [rate_all_hz, rate_gated_hz, <digital values>, <analog values>]

        Fast-counter rates are zero when the FC is not running.
        Digital/analog values are zero when nidaqmx tasks are paused.
        """
        interval = 1.0 / self._sample_rate
        n_total  = len(self._all_channels)
        n_fc     = len(_FC_CHANNELS)

        while not self._poll_stop.is_set():
            t0 = time.monotonic()

            if (self._status == self.STATUS_RUNNING
                    and self._poll_rate_reader is not None):
                rate_all, rate_gated = self._poll_rate_reader()
            else:
                rate_all, rate_gated = 0.0, 0.0

            ni_sample     = self._ni_read_sample()
            sample        = np.empty(n_total, dtype=np.float64)
            sample[0]     = rate_all
            sample[1]     = rate_gated
            sample[n_fc:] = ni_sample

            with self._ring_lock:
                self._ring_buffer.append(sample)

            elapsed    = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                self._poll_stop.wait(timeout=sleep_time)

    # ══════════════════════════════════════════════════════════════════════════
    #  FastCounterInterface helpers
    # ══════════════════════════════════════════════════════════════════════════

    def get_count_rates(self):
        """Return (rate_all_hz, rate_gated_hz) for photon1. Returns (0,0) before first cycle."""
        if self._default_rate_reader is None:
            return 0.0, 0.0
        return self._default_rate_reader()

    def register_rate_reader(self):
        """
        Return an independent rate-reading callable with private snapshot state.

        Each callable tracks its own (last counts, last time, last valid rates)
        independently, so multiple callers never interfere.

        Returns the last valid rates when no new data has arrived, and (0,0)
        before the first histogram cycle completes.

        The returned callable reads only photon1 rates.  In two-channel mode
        the photon2 histogram is accessible via get_data_trace().
        """
        state = {
            'last_time'        : 0.0,
            'last_photon_snap' : 0,
            'last_gated_snap'  : 0,
            'last_cycle_snap'  : 0,
            'last_valid_rates' : (0.0, 0.0),
        }
        photon_count_ref      = self._photon_count_ref
        gated_photon_count_ref = self._gated_photon_count_ref
        diag_hist_cycles_ref  = self._diag_hist_cycles_ref
        photon_count_lock     = self._photon_count_lock
        diag_lock             = self._diag_lock

        def _read():
            now = time.monotonic()
            dt  = now - state['last_time']
            if self._num_gates_per_cycle is None or self._gate_width_s is None:
                return 0.0, 0.0
            with photon_count_lock:
                cur_all   = photon_count_ref[0]
                cur_gated = gated_photon_count_ref[0]
            with diag_lock:
                cur_cycles = diag_hist_cycles_ref[0]
            delta_all    = cur_all   - state['last_photon_snap']
            delta_gated  = cur_gated - state['last_gated_snap']
            delta_cycles = cur_cycles - state['last_cycle_snap']
            if delta_all == 0 or dt <= 0:
                return state['last_valid_rates']
            state['last_time']        = now
            state['last_photon_snap'] = cur_all
            state['last_gated_snap']  = cur_gated
            state['last_cycle_snap']  = cur_cycles
            rate_all_hz    = delta_all / dt
            gate_open_time = (delta_cycles
                              * self._num_gates_per_cycle
                              * self._gate_width_s)
            rate_gated_hz  = (delta_gated / gate_open_time
                              if gate_open_time > 0 else 0.0)
            rates = (rate_all_hz, rate_gated_hz)
            state['last_valid_rates'] = rates
            return rates

        return _read

    def _init_default_rate_reader(self):
        self._default_rate_reader = self.register_rate_reader()

    def get_hardware_status(self):
        """Return a snapshot of fast-counter pipeline buffer depths."""
        hw_ph   = self._get_hw_available(self._photon_task)  if self._photon_task  else -1
        hw_gt   = self._get_hw_available(self._gate_task)    if self._gate_task    else -1
        hw_ph2  = self._get_hw_available(self._photon2_task) if self._photon2_task else -1
        with self._photon_lock:
            sw_ph_samples = sum(len(a) for a in self._photon_list)
        with self._gate_lock:
            sw_gt_samples = sum(len(a) for a in self._gate_list)
        with self._photon2_lock:
            sw_ph2_samples = sum(len(a) for a in self._photon2_list)
        return {
            'hw_photon1_available' : hw_ph,
            'hw_photon2_available' : hw_ph2,
            'hw_gate_available'    : hw_gt,
            'sw_photon1_samples'   : sw_ph_samples,
            'sw_photon2_samples'   : sw_ph2_samples,
            'sw_gate_samples'      : sw_gt_samples,
        }

    def print_summary(self):
        """Print a human-readable summary of the most recent acquisition run."""
        if self._accumulator is None:
            print('No data -- device not configured.')
            return
        data, info = self.get_data_trace()
        cycles_done   = info['elapsed_sweeps']
        elapsed_total = info['elapsed_time']
        if cycles_done == 0:
            print('No complete cycles acquired yet.')
            return

        # Extract per-channel histograms.
        # Two-channel: data.shape == (2, num_gates, n_bins)
        # Single-channel: data.shape == (num_gates, n_bins)
        if self._two_channel_fc and data.ndim == 3:
            hist1 = data[0]   # photon1
            hist2 = data[1]   # photon2
        else:
            hist1 = data
            hist2 = None

        total_ph1       = int(hist1.sum())
        total_gate_time = (cycles_done
                        * self._num_gates_per_cycle
                        * self._gate_width_s)
        rate_gated1 = total_ph1 / total_gate_time if total_gate_time > 0 else 0.0

        if elapsed_total > 0 and cycles_done > 0:
            gate_period_s = elapsed_total / (cycles_done * self._num_gates_per_cycle)
            dead_time_ns  = (gate_period_s - self._gate_width_s) * 1e9
            rate_seq1     = total_ph1 / elapsed_total
            duty_cycle    = 100.0 * self._gate_width_s / gate_period_s
        else:
            dead_time_ns = rate_seq1 = duty_cycle = 0.0

        sep = '--' * 30
        print(f'\n{sep}')
        print(f'  Mode                  : '
            f'{"two-channel" if self._two_channel_fc else "single-channel"}')
        print(f'  Cycles completed      : {cycles_done}')
        print(f'  Elapsed time          : {elapsed_total:.3f} s')
        print(f'  Gate width            : {self._gate_width_s * 1e6:.3f} us')
        print(f'  Dead time (inferred)  : {dead_time_ns:.1f} ns')
        print(f'  Duty cycle            : {duty_cycle:.1f} %')
        print(f'  --- Channel 1 ({self._scan_ch_name}) ---')
        print(f'  Total photons         : {total_ph1:,}')
        print(f'  Count rate (gated)    : {rate_gated1 / 1e3:.2f} kHz')
        print(f'  Count rate (sequence) : {rate_seq1 / 1e3:.2f} kHz')

        if hist2 is not None:
            total_ph2 = int(hist2.sum())
            rate_g2   = total_ph2 / total_gate_time if total_gate_time > 0 else 0.0
            rate_s2   = total_ph2 / elapsed_total   if elapsed_total > 0 else 0.0
            print(f'  --- Channel 2 ({self._scan_ch_name2}) ---')
            print(f'  Total photons         : {total_ph2:,}')
            print(f'  Count rate (gated)    : {rate_g2 / 1e3:.2f} kHz')
            print(f'  Count rate (sequence) : {rate_s2 / 1e3:.2f} kHz')
        print(f'{sep}')

    # ══════════════════════════════════════════════════════════════════════════
    #  Fast counter hardware and thread lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def _reset_run_state(self):
        """Zero all runtime accumulators and counters without altering timing config."""
        if self._accumulator is not None:
            self._accumulator[:] = 0
        if self._accumulator2 is not None:
            self._accumulator2[:] = 0
        self._t_start_ref[0]   = 0.0
        self._elapsed_time_s   = 0.0
        with self._photon_count_lock:
            self._photon_count_ref[0]       = 0
            self._gated_photon_count_ref[0] = 0
        self._t1_abs_ref[0] = np.uint64(0)
        self._t1_abs_ready.clear()
        if self._nidaq is not None:
            self._init_default_rate_reader()
        with self._diag_lock:
            for ref in (self._diag_reader_photons_ref,
                        self._diag_reader_photons2_ref,
                        self._diag_reader_gates_ref,
                        self._diag_proc_photons_ref,
                        self._diag_proc_photons2_ref,
                        self._diag_hist_photons_ref,
                        self._diag_hist_photons2_ref,
                        self._diag_proc_cycles_ref,
                        self._diag_hist_cycles_ref,
                        self._diag_leftover_photons_ref,
                        self._diag_leftover_photons2_ref,
                        self._diag_leftover_gates_ref):
                ref[0] = 0
        self._diag_snap = {
            'time': 0.0,
            'reader_photons': 0, 'reader_photons2': 0, 'reader_gates': 0,
            'proc_photons': 0,   'proc_photons2': 0,
            'hist_photons': 0,   'hist_photons2': 0,
            'proc_cycles': 0,    'hist_cycles': 0,
        }

    def _start_hardware_and_threads(self):
        """
        Create all ctypes DAQmx tasks and start every worker thread.

        Counter assignment:
          ctr0  photon1 period measurement
          ctr1  gate edge absolute timestamps
          ctr2  photon1 anchor (freed early by anchor thread)
          ctr3  photon2 absolute timestamps (two-channel mode only)
        """
        if self._nidaq is None:
            raise RuntimeError('_start_hardware_and_threads() before on_activate().')

        dev = self._device.decode()

        # Create ctypes DAQmx tasks.
        self._photon_task = self._make_photon_period_task(
            f'{dev}/ctr0'.encode(), self._photon_pfi, self._gate_pfi,
            self._photon_buffer, self._max_photon_rate)
        self._gate_task = self._make_gate_timestamp_task(
            f'{dev}/ctr1'.encode(), self._gate_pfi, self._gate_pfi,
            self._gate_buffer, self._max_gate_rate)
        self._anchor_task = self._make_anchor_timestamp_task(
            f'{dev}/ctr2'.encode(), self._photon_pfi, self._gate_pfi,
            buffer_size=1024)

        if self._two_channel_fc:
            # ctr3: count-edges task for photon2 absolute timestamps.
            # Uses the same approach as the gate counter (ctr1): counts 100 MHz
            # ticks, sampled at each photon2 edge, armed at the gate rising edge.
            # The reader thread applies rollover correction identically to ctr1.
            self._photon2_task = self._make_photon2_timestamp_task(
                f'{dev}/ctr3'.encode(), self._photon2_pfi, self._gate_pfi,
                self._photon_buffer, self._max_photon_rate)

        # Create stop/overflow events.
        self._photon_stop     = threading.Event()
        self._gate_stop       = threading.Event()
        self._anchor_stop     = threading.Event()
        self._processor_stop  = threading.Event()
        self._diag_stop       = threading.Event()
        self._photon_overflow = threading.Event()
        self._gate_overflow   = threading.Event()
        self._anchor_overflow = threading.Event()

        if self._two_channel_fc:
            self._photon2_stop     = threading.Event()
            self._photon2_overflow = threading.Event()

        self._t1_abs_ref[0] = np.uint64(0)
        self._t1_abs_ready.clear()

        # Create worker threads.
        self._anchor_thread    = self._make_anchor_reader_thread()
        self._photon_thread    = self._make_reader_thread(
            self._photon_task, self._photon_chunk,
            self._photon_list, self._photon_lock,
            self._photon_stop, self._photon_overflow, 'photon')
        self._gate_thread = self._make_reader_thread(
            self._gate_task, self._gate_chunk,
            self._gate_list, self._gate_lock,
            self._gate_stop, self._gate_overflow, 'gate')

        if self._two_channel_fc:
            # photon2 reader uses label='photon2' which triggers the
            # rollover-correction path in _make_reader_thread (same as 'gate').
            # It does NOT wait for _t1_abs_ready because photon2 timestamps are
            # already absolute (no inter-photon-interval reconstruction needed).
            self._photon2_thread = self._make_reader_thread(
                self._photon2_task, self._photon_chunk,
                self._photon2_list, self._photon2_lock,
                self._photon2_stop, self._photon2_overflow, 'photon2')

        self._processor_thread = self._make_processor_thread()
        self._diag_thread      = self._make_diag_thread()

        # Arm hardware before starting threads so edges during startup are
        # buffered in hardware FIFOs and not lost.
        self._check(self._nidaq.DAQmxStartTask(self._photon_task))
        self._check(self._nidaq.DAQmxStartTask(self._gate_task))
        self._check(self._nidaq.DAQmxStartTask(self._anchor_task))
        if self._two_channel_fc:
            self._check(self._nidaq.DAQmxStartTask(self._photon2_task))

        self._t_start_ref[0] = time.monotonic()

        # Start anchor first so _t1_abs_ready is set as early as possible,
        # minimising the time the photon reader spends waiting.
        self._anchor_thread.start()
        self._photon_thread.start()
        self._gate_thread.start()
        if self._two_channel_fc:
            self._photon2_thread.start()
        self._processor_thread.start()
        self._diag_thread.start()

    def _stop_hardware_and_threads(self):
        """Stop all ctypes DAQmx tasks and join every worker thread."""
        for task in (self._photon_task, self._gate_task):
            if task:
                self._nidaq.DAQmxStopTask(task)
        if self._two_channel_fc and self._photon2_task:
            self._nidaq.DAQmxStopTask(self._photon2_task)

        if self._anchor_task:
            try:
                self._nidaq.DAQmxStopTask(self._anchor_task)
                self._nidaq.DAQmxClearTask(self._anchor_task)
            except Exception:
                pass
            self._anchor_task = None

        # Signal all threads to exit.
        for ev in (self._anchor_stop, self._photon_stop, self._gate_stop,
                   self._processor_stop, self._diag_stop):
            if ev:
                ev.set()
        if self._photon2_stop:
            self._photon2_stop.set()

        # Safety unblock: if anchor errored before setting t1_abs_ready the
        # photon reader would hang — unblock it now.
        self._t1_abs_ready.set()

        for t, tmo in ((self._anchor_thread,    3.0),
                       (self._diag_thread,      3.0),
                       (self._processor_thread, 5.0),
                       (self._photon_thread,    2.0),
                       (self._gate_thread,      2.0),
                       (self._photon2_thread,   2.0)):
            if t and t.is_alive():
                t.join(timeout=tmo)

        if self._photon_task:
            self._nidaq.DAQmxClearTask(self._photon_task)
            self._photon_task = None
        if self._gate_task:
            self._nidaq.DAQmxClearTask(self._gate_task)
            self._gate_task = None
        if self._two_channel_fc and self._photon2_task:
            self._nidaq.DAQmxClearTask(self._photon2_task)
            self._photon2_task = None

        with self._photon_lock:
            self._photon_list.clear()
        with self._gate_lock:
            self._gate_list.clear()
        with self._photon2_lock:
            self._photon2_list.clear()

    # ══════════════════════════════════════════════════════════════════════════
    #  ctypes DAQmx wrappers
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _load_nidaq():
        if os.name == 'nt':
            return ctypes.windll.nicaiu
        return ctypes.cdll.LoadLibrary('libnidaqmx.so')

    def _declare_argtypes(self):
        """Declare C-level argtypes for every DAQmx function used by the ctypes path."""
        n = self._nidaq
        n.DAQmxReadCounterU32.argtypes = [
            ctypes.c_void_p, ctypes.c_int32, ctypes.c_double,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_int32), ctypes.c_void_p,
        ]
        n.DAQmxReadCounterU32.restype = ctypes.c_int32
        n.DAQmxCfgSampClkTiming.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_uint64,
        ]
        n.DAQmxCfgSampClkTiming.restype = ctypes.c_int32
        n.DAQmxSetCICountEdgesTerm.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
        ]
        n.DAQmxSetCICountEdgesTerm.restype = ctypes.c_int32
        n.DAQmxGetReadAvailSampPerChan.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ]
        n.DAQmxGetReadAvailSampPerChan.restype = ctypes.c_int32
        n.DAQmxResetDevice.argtypes   = [ctypes.c_char_p]
        n.DAQmxResetDevice.restype    = ctypes.c_int32
        n.DAQmxGetBufInputBufSize.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ]
        n.DAQmxGetBufInputBufSize.restype = ctypes.c_int32
        n.DAQmxCreateCIPeriodChan.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_double, ctypes.c_double,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_double, ctypes.c_uint32, ctypes.c_char_p,
        ]
        n.DAQmxCreateCIPeriodChan.restype = ctypes.c_int32
        n.DAQmxSetCIPeriodTerm.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
        ]
        n.DAQmxSetCIPeriodTerm.restype = ctypes.c_int32
        n.DAQmxSetCICtrTimebaseSrc.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
        ]
        n.DAQmxSetCICtrTimebaseSrc.restype = ctypes.c_int32
        n.DAQmxCfgImplicitTiming.argtypes = [
            ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint64,
        ]
        n.DAQmxCfgImplicitTiming.restype = ctypes.c_int32

    def _check(self, err):
        if err != 0:
            buf = ctypes.create_string_buffer(2048)
            self._nidaq.DAQmxGetErrorString(err, buf, 2048)
            raise RuntimeError(f'DAQmx Error {err}: {buf.value.decode()}')

    def _get_hw_available(self, task_handle):
        """Return samples waiting in the hardware FIFO, or -1 on error."""
        if task_handle is None:
            return -1
        avail = ctypes.c_uint32(0)
        err   = self._nidaq.DAQmxGetReadAvailSampPerChan(
            task_handle, ctypes.byref(avail))
        return int(avail.value) if err == 0 else -1

    def _make_photon_period_task(self, channel, photon_pfi, start_trigger,
                                 buffer_size, max_rate):
        """
        Create a CI period-measurement task (ctr0) for photon1.

        Measures the interval (in 100 MHz ticks) between consecutive rising
        edges on photon_pfi.  The first value returned is the time from the
        gate ARM trigger to the first photon edge.

        Arm trigger = gate_pfi RISING edge.  Measurement does not start until
        the gate signal goes high, so pre-gate photons are excluded.
        """
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        self._check(self._nidaq.DAQmxCreateCIPeriodChan(
            h, channel, b'',
            ctypes.c_double(1.0), ctypes.c_double(float(2**32 - 1)),
            ctypes.c_int32(DAQmx_Val_Ticks), ctypes.c_int32(DAQmx_Val_Rising),
            ctypes.c_int32(DAQmx_Val_LowFreq1Ctr),
            ctypes.c_double(0.001), ctypes.c_uint32(1), None))
        self._check(self._nidaq.DAQmxSetCIPeriodTerm(h, channel, photon_pfi))
        self._check(self._nidaq.DAQmxSetCICtrTimebaseSrc(
            h, channel, self._timebase_term))
        self._check(self._nidaq.DAQmxCfgImplicitTiming(
            h, ctypes.c_int32(DAQmx_Val_ContSamps),
            ctypes.c_uint64(buffer_size)))
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(h, DAQmx_Val_Rising))
        return h

    def _make_gate_timestamp_task(self, channel, gate_pfi, start_trigger,
                                  buffer_size, max_rate):
        """
        Create a CI count-edges task (ctr1) for gate timestamps.

        Counts 100 MHz timebase ticks continuously and latches the current
        count on each RISING edge of gate_pfi.  Each latch value is the
        absolute 100 MHz tick of that gate opening.  Roll-over (every ~43 s)
        is corrected by the reader thread.
        """
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        self._check(self._nidaq.DAQmxCreateCICountEdgesChan(
            h, channel, b'', DAQmx_Val_Rising, 0, DAQmx_Val_CountUp))
        self._check(self._nidaq.DAQmxSetCICountEdgesTerm(
            h, channel, self._timebase_term))
        self._check(self._nidaq.DAQmxCfgSampClkTiming(
            h, gate_pfi, float(max_rate), DAQmx_Val_Rising, DAQmx_Val_ContSamps,
            ctypes.c_uint64(buffer_size)))
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(h, DAQmx_Val_Rising))
        return h

    def _make_anchor_timestamp_task(self, channel, photon_pfi, start_trigger,
                                    buffer_size=1024):
        """
        Create a CI count-edges task (ctr2) for the photon1 anchor.

        Identical structure to the gate timestamp task (ctr1), but the sample
        clock is photon_pfi instead of gate_pfi.  The first latched value is
        the absolute 100 MHz tick of the first photon1 edge after the gate
        arm trigger.  This is used by the anchor thread to seed the photon1
        cumulative timestamp reconstruction.

        The anchor thread reads exactly ONE value then clears this task,
        freeing ctr2 for use by other parts of the system.
        """
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        self._check(self._nidaq.DAQmxCreateCICountEdgesChan(
            h, channel, b'', DAQmx_Val_Rising, 0, DAQmx_Val_CountUp))
        self._check(self._nidaq.DAQmxSetCICountEdgesTerm(
            h, channel, self._timebase_term))
        self._check(self._nidaq.DAQmxCfgSampClkTiming(
            h, photon_pfi, float(self._max_photon_rate),
            DAQmx_Val_Rising, DAQmx_Val_ContSamps,
            ctypes.c_uint64(buffer_size)))
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(h, DAQmx_Val_Rising))
        return h

    def _make_photon2_timestamp_task(self, channel, photon2_pfi, start_trigger,
                                     buffer_size, max_rate):
        """
        Create a CI count-edges task (ctr3) for photon2 absolute timestamps.
        TWO-CHANNEL MODE ONLY.

        Design rationale
        ----------------
        Photon1 uses a period-measurement task (ctr0) that returns inter-photon
        intervals.  Absolute timestamps are reconstructed via cumsum seeded by
        the photon1 anchor (ctr2).

        Photon2 cannot use this approach because there is no counter left for a
        separate anchor.  Instead, ctr3 uses the same count-edges-with-sample-clock
        approach as the gate counter (ctr1): the 100 MHz timebase is counted
        continuously and latched on each photon2 rising edge.  The latched values
        ARE absolute timestamps (no reconstruction needed).  Roll-over is corrected
        by the reader thread identically to the gate reader.

        This design requires no anchor and uses only one counter (ctr3).

        Task layout
        -----------
          Source terminal   : 100 MHz internal timebase (counts ticks continuously)
          Sample clock      : photon2_pfi (latch on each photon2 rising edge)
          Arm trigger       : gate_pfi RISING edge (exclude pre-gate photons)
          Buffer            : same size as photon1 buffer
        """
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        # Counter source: counts 100 MHz timebase ticks
        self._check(self._nidaq.DAQmxCreateCICountEdgesChan(
            h, channel, b'', DAQmx_Val_Rising, 0, DAQmx_Val_CountUp))
        self._check(self._nidaq.DAQmxSetCICountEdgesTerm(
            h, channel, self._timebase_term))
        # Sample clock: latch tick count on each photon2 edge
        self._check(self._nidaq.DAQmxCfgSampClkTiming(
            h, photon2_pfi, float(max_rate), DAQmx_Val_Rising, DAQmx_Val_ContSamps,
            ctypes.c_uint64(buffer_size)))
        # Arm trigger: same gate_pfi as ctr0/ctr1 — all three tasks arm together
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(h, DAQmx_Val_Rising))
        return h

    # ══════════════════════════════════════════════════════════════════════════
    #  Fast-counter thread factories
    # ══════════════════════════════════════════════════════════════════════════

    def _make_anchor_reader_thread(self):
        """
        Thread factory for the photon1 anchor reader.

        Polls ctr2 until the first photon1 edge arrives, reads the absolute
        100 MHz tick, stores it in t1_abs_ref[0], signals t1_abs_ready, then
        stops and clears ctr2 to free the counter resource.
        """
        nidaq           = self._nidaq
        anchor_task     = self._anchor_task
        t1_abs_ref      = self._t1_abs_ref
        t1_abs_ready    = self._t1_abs_ready
        anchor_overflow = self._anchor_overflow
        stop_event      = self._anchor_stop
        diag_enabled    = self._diag_enabled

        def _run():
            raw_buf    = (ctypes.c_uint32 * 1)()
            samps_read = ctypes.c_int32(0)
            avail      = ctypes.c_uint32(0)
            # Poll until the first photon1 edge latches a tick count.
            while not stop_event.is_set():
                nidaq.DAQmxGetReadAvailSampPerChan(
                    anchor_task, ctypes.byref(avail))
                if avail.value >= 1:
                    break
                time.sleep(0.0001)
            if stop_event.is_set():
                return
            err = nidaq.DAQmxReadCounterU32(
                anchor_task, ctypes.c_int32(1), ctypes.c_double(2.0),
                raw_buf, ctypes.c_uint32(1),
                ctypes.byref(samps_read), None)
            if err < 0 or samps_read.value != 1:
                buf = ctypes.create_string_buffer(2048)
                nidaq.DAQmxGetErrorString(err, buf, 2048)
                self.log.error(
                    f'[anchor] FATAL: err={err}: {buf.value.decode()}')
                anchor_overflow.set()
                t1_abs_ready.set()
                return
            t1_abs_ref[0] = np.uint64(raw_buf[0])
            if diag_enabled:
                print(f'[anchor] t1_abs = {t1_abs_ref[0]} ticks '
                      f'({int(t1_abs_ref[0]) * _TICK_NS * 1e-6:.3f} ms after arm)',
                      flush=True)
            # Signal the photon1 reader thread — it can now seed its cumsum.
            t1_abs_ready.set()
            # ctr2 has served its purpose: release the counter resource.
            nidaq.DAQmxStopTask(anchor_task)
            nidaq.DAQmxClearTask(anchor_task)
            if diag_enabled:
                print('[anchor] ctr2 freed.', flush=True)

        return threading.Thread(target=_run, daemon=True, name='anchor')

    def _make_reader_thread(self, task_handle, chunk_size, shared_list, lock,
                            stop_event, overflow_event, label):
        """
        Thread factory for DAQmx counter read loops.

        Supports three labels, each producing absolute uint64 photon/gate
        timestamps in the 100 MHz tick domain:

        label == 'photon'
            Reads inter-photon intervals from the ctr0 period-measurement task.
            Waits for t1_abs_ready before emitting any timestamps.
            Prepends a 0 on the first batch so cumsum gives:
                [t1, t2, t3, ...] = t1_abs + [0, i1, i1+i2, ...]
            where i_k are the measured inter-photon intervals.

        label == 'gate' or label == 'photon2'
            Reads absolute 100 MHz tick counts from a count-edges-with-sample-
            clock task (ctr1 or ctr3).  Both use 32-bit hardware counters that
            roll over every ~43 s.  Monotonic uint64 timestamps are reconstructed
            by detecting negative signed deltas between consecutive raw values
            (intra-chunk wraps) and between the last emitted value and the first
            new value (inter-chunk wraps).

            'photon2' does NOT wait for t1_abs_ready — its timestamps are
            already absolute and do not depend on the photon1 anchor.
        """
        diag_enabled = self._diag_enabled

        raw_buf    = (ctypes.c_uint32 * chunk_size)()
        samps_read = ctypes.c_int32(0)
        nidaq      = self._nidaq

        # Diagnostics counter reference for this specific reader.
        if label == 'photon':
            diag_ref = self._diag_reader_photons_ref
        elif label == 'photon2':
            diag_ref = self._diag_reader_photons2_ref
        else:
            diag_ref = self._diag_reader_gates_ref
        diag_lock = self._diag_lock

        t1_abs_ref   = self._t1_abs_ref
        t1_abs_ready = self._t1_abs_ready

        # Reader-thread-local state (one dict per closure instance).
        if label == 'photon':
            period_state = {'abs_tick': np.uint64(0), 't1_emitted': False}
        else:
            rollover_state = {'prev_rollover': np.uint64(0),
                              'last_abs':      np.uint64(0)}

        # Read in batches of at least min_batch to avoid busy-polling.
        min_batch = max(100, chunk_size // 100)

        def _run():
            avail = ctypes.c_uint32(0)

            # photon1 reader must wait until the anchor sets t1_abs_ready so
            # that the cumsum is seeded at the correct absolute origin.
            # photon2 and gate readers start immediately.
            if label == 'photon':
                if diag_enabled:
                    print('[photon1 reader] waiting for anchor...', flush=True)
                t1_abs_ready.wait()
                period_state['abs_tick'] = t1_abs_ref[0]
                if diag_enabled:
                    print(f'[photon1 reader] seeded t1_abs = '
                          f'{period_state["abs_tick"]}', flush=True)

            while not stop_event.is_set():
                nidaq.DAQmxGetReadAvailSampPerChan(
                    task_handle, ctypes.byref(avail))
                to_read = min(avail.value, chunk_size)
                if to_read < min_batch:
                    time.sleep(0.0001)
                    continue
                err = nidaq.DAQmxReadCounterU32(
                    task_handle, ctypes.c_int32(to_read), ctypes.c_double(1.0),
                    raw_buf, ctypes.c_uint32(chunk_size),
                    ctypes.byref(samps_read), None)
                n = samps_read.value
                if err < 0:
                    buf = ctypes.create_string_buffer(2048)
                    nidaq.DAQmxGetErrorString(err, buf, 2048)
                    self.log.error(
                        f'[reader-{label}] FATAL err={err}: {buf.value.decode()}')
                    overflow_event.set()
                    stop_event.set()
                    break
                if err > 0:
                    buf = ctypes.create_string_buffer(2048)
                    nidaq.DAQmxGetErrorString(err, buf, 2048)
                    self.log.warning(
                        f'[reader-{label}] warning {err}: {buf.value.decode()}')
                if n == 0:
                    continue

                if label == 'photon':
                    # Period-measurement path: reconstruct absolute timestamps.
                    # raw_buf contains inter-photon intervals in 100 MHz ticks.
                    # Cumsum converts intervals to absolute offsets from t1_abs.
                    intervals = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                 .copy().astype(np.uint64))
                    if not period_state['t1_emitted']:
                        # Prepend 0 so index 0 = t1_abs (the anchor photon itself).
                        intervals = np.concatenate(
                            [np.array([0], dtype=np.uint64), intervals])
                        period_state['t1_emitted'] = True
                    absolute = period_state['abs_tick'] + np.cumsum(intervals)
                    period_state['abs_tick'] = absolute[-1]

                else:
                    # Count-edges-with-rollover path (gate and photon2).
                    # raw_buf contains absolute 32-bit tick counts; reconstruct
                    # monotonic uint64 by detecting and correcting roll-overs.
                    counts64    = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                   .copy().astype(np.uint64))
                    offsets     = np.zeros(n, dtype=np.uint64)
                    n_new_wraps = np.uint64(0)

                    # Inter-chunk wrap: negative signed delta from last emitted
                    # value to the first new raw value.
                    if rollover_state['last_abs'] > 0:
                        last_raw    = rollover_state['last_abs'] % np.uint64(2**32)
                        delta_first = np.int64(counts64[0]) - np.int64(last_raw)
                        if delta_first < 0:
                            offsets     += np.uint64(2**32)
                            n_new_wraps += np.uint64(1)

                    # Intra-chunk wraps: negative signed delta between consecutive
                    # raw values within this batch.
                    diffs    = np.diff(counts64.view(np.int64))
                    wrap_idx = np.where(diffs < 0)[0] + 1
                    for idx in wrap_idx:
                        offsets[idx:] += np.uint64(2**32)
                        n_new_wraps   += np.uint64(1)

                    absolute = counts64 + offsets + rollover_state['prev_rollover']
                    rollover_state['prev_rollover'] += n_new_wraps * np.uint64(2**32)
                    rollover_state['last_abs']       = absolute[-1]

                with lock:
                    shared_list.append(absolute)
                with diag_lock:
                    diag_ref[0] += n

        return threading.Thread(target=_run, daemon=True, name=f'reader-{label}')

    def _make_processor_thread(self):
        """
        Thread factory for the histogram processor.

        In single-channel mode this thread processes photon1 and gates only.
        In two-channel mode it processes photon1, photon2, and gates — with
        two independent histograms accumulated simultaneously.

        Algorithm (per batch)
        ----------------------
        1. Phase alignment (first call only):
           Discard N-1 gate timestamps that arrived before the first true cycle
           boundary (the NI hardware misses the very first gate edge — it serves
           as the arm trigger).  Discard all photon data before that boundary.

        2. Main loop:
           a. Wait for at least num_gates_per_cycle complete gate edges.
           b. Collect photon data from all active reader threads.
           c. Wait for all photon streams to advance past
              last_gate_close + PHOTON_SLACK_TICKS.  A 5-second safety timeout
              prevents stalling if one channel has a very low photon rate.
           d. Drain any late-arriving photon chunks.
           e. Call histogram_batch() independently for each channel.
           f. Accumulate results into the respective accumulators.
           g. Carry forward leftover data for the next iteration.
        """
        # --- Capture all required references in local variables.
        # This allows the closure to remain valid even if module attributes
        # are later modified (e.g. on re-configure).
        photon_list  = self._photon_list
        photon2_list = self._photon2_list
        gate_list    = self._gate_list
        photon_lock  = self._photon_lock
        photon2_lock = self._photon2_lock
        gate_lock    = self._gate_lock

        accumulator  = self._accumulator   # photon1 histogram
        accumulator2 = self._accumulator2  # photon2 histogram (None in single-ch)
        two_channel  = self._two_channel_fc

        stop_event      = self._processor_stop
        overflow_events = [self._photon_overflow, self._gate_overflow,
                           self._anchor_overflow]
        if two_channel:
            overflow_events.append(self._photon2_overflow)

        photon_count_ref       = self._photon_count_ref
        gated_photon_count_ref = self._gated_photon_count_ref
        photon_count_lock      = self._photon_count_lock
        num_gates_per_cycle    = self._num_gates_per_cycle
        gate_ticks             = self._gate_ticks
        n_bins                 = self._n_bins
        histogram_batch        = self._histogram_batch

        diag_lock                = self._diag_lock
        diag_proc_photons_ref    = self._diag_proc_photons_ref
        diag_hist_photons_ref    = self._diag_hist_photons_ref
        diag_proc_photons2_ref   = self._diag_proc_photons2_ref
        diag_hist_photons2_ref   = self._diag_hist_photons2_ref
        diag_proc_cycles_ref     = self._diag_proc_cycles_ref
        diag_hist_cycles_ref     = self._diag_hist_cycles_ref
        diag_leftover_ph_ref     = self._diag_leftover_photons_ref
        diag_leftover_ph2_ref    = self._diag_leftover_photons2_ref
        diag_leftover_gt_ref     = self._diag_leftover_gates_ref

        def _run():
            leftover_photons  = np.empty(0, dtype=np.uint64)  # photon1 carry-forward
            leftover_photons2 = np.empty(0, dtype=np.uint64)  # photon2 carry-forward
            leftover_gates    = np.empty(0, dtype=np.uint64)  # gate carry-forward

            # ── Phase alignment ────────────────────────────────────────────────
            # The NI hardware cannot simultaneously be gated and latch the
            # triggering edge, so the first gate edge in ctr1's buffer is the
            # SECOND physical gate (the first physical gate was the arm trigger).
            # Discarding the first (num_gates_per_cycle - 1) timestamps aligns
            # the processor to true cycle boundaries.
            phase_aligned = False
            phase_n       = num_gates_per_cycle

            while not stop_event.is_set() and not phase_aligned:
                if any(ev.is_set() for ev in overflow_events):
                    stop_event.set(); return
                with gate_lock:
                    collected = sum(len(a) for a in gate_list) + len(leftover_gates)
                if collected < phase_n:
                    time.sleep(0.001); continue

                with gate_lock:
                    chunks = gate_list.copy(); gate_list.clear()
                if chunks:
                    new_gates = np.concatenate(chunks)
                    leftover_gates = (np.concatenate([leftover_gates, new_gates])
                                      if len(leftover_gates) else new_gates)

                # Keep only from index (phase_n-1) onward; this is the first
                # gate of the second cycle and serves as the cycle origin.
                leftover_gates = leftover_gates[phase_n - 1:]
                cutoff         = leftover_gates[0]   # absolute tick of cycle origin

                # Discard photon1 data that arrived before the cycle origin.
                with photon_lock:
                    chunks = photon_list.copy(); photon_list.clear()
                if chunks:
                    new_ph = np.concatenate(chunks)
                    leftover_photons = (np.concatenate([leftover_photons, new_ph])
                                        if len(leftover_photons) else new_ph)
                split = np.searchsorted(leftover_photons, cutoff, side='left')
                leftover_photons = leftover_photons[split:]

                # Discard photon2 data before cycle origin (two-channel only).
                if two_channel and accumulator2 is not None:
                    with photon2_lock:
                        chunks2 = photon2_list.copy(); photon2_list.clear()
                    if chunks2:
                        new_ph2 = np.concatenate(chunks2)
                        leftover_photons2 = (np.concatenate([leftover_photons2, new_ph2])
                                             if len(leftover_photons2) else new_ph2)
                    split2 = np.searchsorted(leftover_photons2, cutoff, side='left')
                    leftover_photons2 = leftover_photons2[split2:]

                phase_aligned = True

            # ── Main processing loop ────────────────────────────────────────────
            while not stop_event.is_set():
                if any(ev.is_set() for ev in overflow_events):
                    stop_event.set(); break

                # Wait for at least one complete gate cycle.
                with gate_lock:
                    gate_count = (sum(len(a) for a in gate_list)
                                  + len(leftover_gates))
                if gate_count < num_gates_per_cycle:
                    time.sleep(0.001); continue

                # Atomically drain the photon1 queue.
                with photon_lock:
                    new_ph_chunks = photon_list.copy(); photon_list.clear()
                # Atomically drain the gate queue.
                with gate_lock:
                    new_gt_chunks = gate_list.copy(); gate_list.clear()

                if new_gt_chunks:
                    new_gates = np.concatenate(new_gt_chunks)
                    all_gates = (np.concatenate([leftover_gates, new_gates])
                                 if len(leftover_gates) else new_gates)
                else:
                    all_gates = leftover_gates

                n_complete = len(all_gates) // num_gates_per_cycle
                if n_complete == 0:
                    leftover_gates = all_gates
                    time.sleep(0.001); continue

                if new_ph_chunks:
                    new_ph = np.concatenate(new_ph_chunks)
                    all_photons = (np.concatenate([leftover_photons, new_ph])
                                   if len(leftover_photons) else new_ph)
                else:
                    all_photons = leftover_photons

                # Collect photon2 data alongside photon1 (two-channel only).
                if two_channel and accumulator2 is not None:
                    with photon2_lock:
                        new_ph2_chunks = photon2_list.copy(); photon2_list.clear()
                    if new_ph2_chunks:
                        new_ph2 = np.concatenate(new_ph2_chunks)
                        all_photons2 = (np.concatenate([leftover_photons2, new_ph2])
                                        if len(leftover_photons2) else new_ph2)
                    else:
                        all_photons2 = leftover_photons2

                n_gates_batch   = n_complete * num_gates_per_cycle
                gate_rise_batch = all_gates[:n_gates_batch]
                last_cycle_end  = gate_rise_batch[-1] + np.uint64(gate_ticks)
                photon_deadline = last_cycle_end + PHOTON_SLACK_TICKS

                # ── Wait for photon stream(s) to advance past deadline ─────────
                # All active channels must advance past photon_deadline before
                # we commit this batch.  A 5-second safety timeout prevents
                # stalling if one channel has a very low or zero photon rate
                # (e.g. a disconnected APD).
                deadline_time = time.monotonic() + 5.0
                while not stop_event.is_set():
                    if any(ev.is_set() for ev in overflow_events):
                        stop_event.set(); break

                    # Check photon1 stream.
                    ph1_max = (all_photons[-1] if len(all_photons)
                               else np.uint64(0))
                    with photon_lock:
                        for chunk in photon_list:
                            if len(chunk) and chunk[-1] > ph1_max:
                                ph1_max = chunk[-1]
                    deadline_met = ph1_max >= photon_deadline

                    # Check photon2 stream (two-channel only).
                    if two_channel and accumulator2 is not None:
                        ph2_max = (all_photons2[-1] if len(all_photons2)
                                   else np.uint64(0))
                        with photon2_lock:
                            for chunk in photon2_list:
                                if len(chunk) and chunk[-1] > ph2_max:
                                    ph2_max = chunk[-1]
                        deadline_met = deadline_met and (ph2_max >= photon_deadline)

                    if deadline_met or time.monotonic() > deadline_time:
                        break
                    time.sleep(0.001)

                if stop_event.is_set():
                    break

                # Drain any late-arriving photon1 chunks that slipped in after
                # the deadline loop exited.
                with photon_lock:
                    late_chunks = photon_list.copy(); photon_list.clear()
                if late_chunks:
                    late = np.concatenate(late_chunks)
                    late.sort()
                    all_photons = (np.concatenate([all_photons, late])
                                   if len(all_photons) else late)

                # Drain any late-arriving photon2 chunks (two-channel only).
                if two_channel and accumulator2 is not None:
                    with photon2_lock:
                        late2_chunks = photon2_list.copy(); photon2_list.clear()
                    if late2_chunks:
                        late2 = np.concatenate(late2_chunks)
                        late2.sort()
                        all_photons2 = (np.concatenate([all_photons2, late2])
                                        if len(all_photons2) else late2)

                # ── Compute photon1 histogram ─────────────────────────────────
                ph_lo = np.searchsorted(
                    all_photons, gate_rise_batch[0], side='left')
                ph_hi = np.searchsorted(
                    all_photons, last_cycle_end, side='right')
                photons1_batch = all_photons[ph_lo:ph_hi]

                batch_hist1    = histogram_batch(photons1_batch, gate_rise_batch,
                                                 num_gates_per_cycle, n_bins, gate_ticks)
                n_consumed1    = len(photons1_batch)
                n_hist1        = int(batch_hist1.sum())
                accumulator[:] += batch_hist1

                # ── Compute photon2 histogram (two-channel only) ───────────────
                n_consumed2 = 0
                n_hist2     = 0
                if two_channel and accumulator2 is not None:
                    ph2_lo = np.searchsorted(
                        all_photons2, gate_rise_batch[0], side='left')
                    ph2_hi = np.searchsorted(
                        all_photons2, last_cycle_end, side='right')
                    photons2_batch = all_photons2[ph2_lo:ph2_hi]

                    batch_hist2     = histogram_batch(photons2_batch, gate_rise_batch,
                                                      num_gates_per_cycle, n_bins,
                                                      gate_ticks)
                    n_consumed2     = len(photons2_batch)
                    n_hist2         = int(batch_hist2.sum())
                    accumulator2[:] += batch_hist2

                # Update photon count references used by the rate reader.
                with photon_count_lock:
                    photon_count_ref[0]       += n_consumed1
                    gated_photon_count_ref[0] += n_hist1

                with diag_lock:
                    diag_proc_photons_ref[0]  += n_consumed1
                    diag_hist_photons_ref[0]  += n_hist1
                    diag_proc_cycles_ref[0]   += n_complete
                    diag_hist_cycles_ref[0]   += n_complete
                    if two_channel:
                        diag_proc_photons2_ref[0] += n_consumed2
                        diag_hist_photons2_ref[0] += n_hist2

                # Carry forward data past last_cycle_end for the next iteration.
                leftover_gates   = all_gates[n_gates_batch:]
                split1           = np.searchsorted(all_photons, last_cycle_end,
                                                   side='right')
                leftover_photons = all_photons[split1:]

                if two_channel and accumulator2 is not None:
                    split2            = np.searchsorted(all_photons2, last_cycle_end,
                                                        side='right')
                    leftover_photons2 = all_photons2[split2:]

                with diag_lock:
                    diag_leftover_ph_ref[0]  = len(leftover_photons)
                    diag_leftover_ph2_ref[0] = (len(leftover_photons2)
                                                if two_channel else 0)
                    diag_leftover_gt_ref[0]  = len(leftover_gates)

        return threading.Thread(target=_run, daemon=True, name='processor')

    def _make_diag_thread(self):
        """
        Thread factory for the periodic pipeline diagnostics printout.
        Prints reader rates, SW buffer depths, processor rates, and leftover
        counts for all active channels.  Only runs when diag_enabled is True.
        """
        interval         = self._diag_interval_s
        diag_enabled     = self._diag_enabled
        stop_event       = self._diag_stop
        diag_lock        = self._diag_lock
        photon_lock      = self._photon_lock
        photon2_lock     = self._photon2_lock
        gate_lock        = self._gate_lock
        photon_list      = self._photon_list
        photon2_list     = self._photon2_list
        gate_list        = self._gate_list
        two_channel      = self._two_channel_fc

        photon_task_ref  = lambda: self._photon_task
        photon2_task_ref = lambda: self._photon2_task
        gate_task_ref    = lambda: self._gate_task

        rph_ref   = self._diag_reader_photons_ref
        rph2_ref  = self._diag_reader_photons2_ref
        rgt_ref   = self._diag_reader_gates_ref
        pph_ref   = self._diag_proc_photons_ref
        pph2_ref  = self._diag_proc_photons2_ref
        hph_ref   = self._diag_hist_photons_ref
        hph2_ref  = self._diag_hist_photons2_ref
        pcy_ref   = self._diag_proc_cycles_ref
        hcy_ref   = self._diag_hist_cycles_ref
        lph_ref   = self._diag_leftover_photons_ref
        lph2_ref  = self._diag_leftover_photons2_ref
        lgt_ref   = self._diag_leftover_gates_ref
        snap      = self._diag_snap

        def _run():
            if not diag_enabled or interval <= 0:
                return
            snap['time'] = time.monotonic()
            while not stop_event.wait(timeout=interval):
                now = time.monotonic()
                dt  = now - snap['time']
                if dt <= 0:
                    continue
                with diag_lock:
                    cur_rph  = rph_ref[0];  cur_rph2 = rph2_ref[0]
                    cur_rgt  = rgt_ref[0]
                    cur_pph  = pph_ref[0];  cur_pph2 = pph2_ref[0]
                    cur_hph  = hph_ref[0];  cur_hph2 = hph2_ref[0]
                    cur_pcy  = pcy_ref[0];  cur_hcy  = hcy_ref[0]
                    left_ph  = lph_ref[0];  left_ph2 = lph2_ref[0]
                    left_gt  = lgt_ref[0]
                with photon_lock:
                    sw_ph_s  = sum(len(a) for a in photon_list)
                with photon2_lock:
                    sw_ph2_s = sum(len(a) for a in photon2_list)
                with gate_lock:
                    sw_gt_s  = sum(len(a) for a in gate_list)

                hw_ph  = self._get_hw_available(photon_task_ref())
                hw_ph2 = self._get_hw_available(photon2_task_ref()) if two_channel else -1
                hw_gt  = self._get_hw_available(gate_task_ref())

                d_rph  = cur_rph  - snap['reader_photons']
                d_rph2 = cur_rph2 - snap['reader_photons2']
                d_rgt  = cur_rgt  - snap['reader_gates']
                d_pph  = cur_pph  - snap['proc_photons']
                d_pph2 = cur_pph2 - snap['proc_photons2']
                d_hph  = cur_hph  - snap['hist_photons']
                d_hph2 = cur_hph2 - snap['hist_photons2']
                d_pcy  = cur_pcy  - snap['proc_cycles']
                d_hcy  = cur_hcy  - snap['hist_cycles']

                snap.update({
                    'time': now,
                    'reader_photons': cur_rph, 'reader_photons2': cur_rph2,
                    'reader_gates':   cur_rgt,
                    'proc_photons':   cur_pph, 'proc_photons2':   cur_pph2,
                    'hist_photons':   cur_hph, 'hist_photons2':   cur_hph2,
                    'proc_cycles':    cur_pcy, 'hist_cycles':     cur_hcy,
                })

                ge1 = (100.0 * cur_hph  / cur_pph)  if cur_pph  > 0 else 0.0
                ge2 = (100.0 * cur_hph2 / cur_pph2) if cur_pph2 > 0 else 0.0
                ce  = (100.0 * cur_hcy  / cur_pcy)  if cur_pcy  > 0 else 0.0
                W   = 70
                sep = '--' * (W // 2)
                print(f'\n+{sep}+')
                print(f'|  DIAGNOSTICS  dt={dt:.2f}s  '
                      f'{"two-channel" if two_channel else "single-channel"}'
                      + ' ' * max(0, W - 38) + '|')
                print(f'+{sep}+')
                print(f'|  READER              cum          rate/s     HW FIFO  |')
                print(f'|  photon1        {cur_rph:>10,d}  {d_rph/dt:>10.0f}  {hw_ph:>9d}  |')
                if two_channel:
                    print(f'|  photon2        {cur_rph2:>10,d}  {d_rph2/dt:>10.0f}  {hw_ph2:>9d}  |')
                print(f'|  gate           {cur_rgt:>10,d}  {d_rgt/dt:>10.0f}  {hw_gt:>9d}  |')
                print(f'+{sep}+')
                print(f'|  SW BUFFERS          samples                           |')
                print(f'|  photon1        {sw_ph_s:>10,d}' + ' ' * (W - 23) + '|')
                if two_channel:
                    print(f'|  photon2        {sw_ph2_s:>10,d}' + ' ' * (W - 23) + '|')
                print(f'|  gate           {sw_gt_s:>10,d}' + ' ' * (W - 23) + '|')
                print(f'+{sep}+')
                print(f'|  PROCESSOR           cum          rate/s     gate eff  |')
                print(f'|  ph1 consumed   {cur_pph:>10,d}  {d_pph/dt:>10.0f}' + ' ' * 12 + '|')
                print(f'|  ph1 histgrmd   {cur_hph:>10,d}  {d_hph/dt:>10.0f}  {ge1:>7.1f}%  |')
                if two_channel:
                    print(f'|  ph2 consumed   {cur_pph2:>10,d}  {d_pph2/dt:>10.0f}' + ' ' * 12 + '|')
                    print(f'|  ph2 histgrmd   {cur_hph2:>10,d}  {d_hph2/dt:>10.0f}  {ge2:>7.1f}%  |')
                print(f'|  cycles proc    {cur_pcy:>10,d}  {d_pcy/dt:>10.1f}  {ce:>7.1f}%  |')
                print(f'+{sep}+')
                print(f'|  LEFTOVERS  photon1={left_ph:,}  '
                      f'{"photon2="+str(left_ph2)+"  " if two_channel else ""}'
                      f'gate={left_gt:,}' + ' ' * 10 + '|')
                print(f'+{sep}+', flush=True)

        return threading.Thread(target=_run, daemon=True, name='diag')

    @staticmethod
    def _histogram_batch(photons_sorted, gate_rise_all,
                         num_gates, n_bins, gate_ticks):
        """
        Vectorised histogram kernel.

        Maps each photon timestamp to a (gate_in_cycle, bin_within_gate) index
        and accumulates counts using numpy bincount.

        Parameters
        ----------
        photons_sorted : uint64 ndarray
            Absolute photon timestamps (100 MHz ticks), sorted ascending.
            These are the photons that arrived within [gate_rise_all[0],
            last_cycle_end].  Pre-sliced by the processor before calling.
        gate_rise_all  : uint64 ndarray
            Gate-open timestamps (100 MHz ticks), sorted ascending.
            Length = n_complete * num_gates  (an integer multiple of num_gates).
        num_gates      : int    Gates per excitation cycle.
        n_bins         : int    Histogram bins (= gate_ticks).
        gate_ticks     : int    Gate window duration in 100 MHz ticks.

        Returns
        -------
        hist : uint64 ndarray, shape (num_gates, n_bins)
            Photon counts per (gate_in_cycle, time_bin).
        """
        gate_ticks_u64 = np.uint64(gate_ticks)
        hist = np.zeros((num_gates, n_bins), dtype=np.uint64)
        if len(photons_sorted) == 0:
            return hist

        gate_ends_all = gate_rise_all + gate_ticks_u64

        # For each photon find the most recent gate that opened before it.
        # searchsorted(..., 'right') - 1 gives the last gate_rise <= photon.
        # -1 means the photon arrived before the first gate (should not occur
        # after phase alignment, but guarded below).
        gate_idx = (np.searchsorted(gate_rise_all, photons_sorted, side='right')
                    .astype(np.int64) - 1)
        valid    = gate_idx >= 0
        gate_idx = gate_idx[valid]
        ph       = photons_sorted[valid]

        # Discard photons that arrived after their gate window closed.
        in_win   = ph < gate_ends_all[gate_idx]
        gate_idx = gate_idx[in_win]
        ph       = ph[in_win]
        if len(ph) == 0:
            return hist

        # Convert to (row, col) = (gate_in_cycle, offset_within_gate).
        offset        = (ph - gate_rise_all[gate_idx]).astype(np.int64)
        gate_in_cycle = gate_idx % num_gates
        flat_idx      = gate_in_cycle * n_bins + offset
        counts = np.bincount(flat_idx, minlength=num_gates * n_bins)
        hist  += counts.reshape(num_gates, n_bins).astype(np.uint64)
        return hist

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning counter interface
    #  (consumed by PIE710CounterInterfuse)
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def channel_names(self) -> List[str]:
        """
        Channel names exposed to PIE710CounterInterfuse and the Qudi confocal GUI.

        Single-channel mode : ['APD1']
        Two-channel mode    : ['APD1', 'APD2']
        """
        names = [self._scan_ch_name]
        if self._two_channel_scan:
            names.append(self._scan_ch_name2)
        return names

    @property
    def channel_units(self) -> Dict[str, str]:
        """
        Physical unit for each scanning channel.
        The interfuse divides raw counts by t_pixel to produce counts/s.
        """
        units = {self._scan_ch_name: 'c/s'}
        if self._two_channel_scan:
            units[self._scan_ch_name2] = 'c/s'
        return units

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Stop instreamer tasks (saving their state) and create the CO + CI
        scan task pair(s).

        Must be called BEFORE the PI E-710 scan command is sent.
        The tasks wait silently for the PI gate RISING edge before collecting
        any data.

        Single-channel:
          CO  ctr1  5 kHz finite pulse train, gated by PI trigger
          CI  ctr0  photon1 edge counting, clocked by CO output

        Two-channel (additional):
          CI  ctr2  photon2 edge counting, clocked by the SAME CO output
                    → perfect per-pixel synchronisation between channels

        Priority:
          Fails immediately if the fast counter is running or paused.
          Stale scan tasks from a previous run are cleaned up first.

        @param n_pixels : pixels per sweep (1D: n_x, 2D: one fast-axis line)
        @param t_pixel  : dwell time per pixel in seconds (= 1 / frequency)
        """
        with self._scan_lock:
            # Clean up any stale scan tasks from a crashed previous scan.
            if self._scan_task is not None or self._scan_co_task is not None:
                self.log.warning(
                    'arm(): stale scan tasks found — cleaning up first.')
                self._scan_cleanup_unsafe(restart_stream=False)

            # Fast counter takes absolute priority over scanning.
            if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                raise RuntimeError(
                    f'Cannot arm scanner while fast counter is active '
                    f'(status={self._status}).  '
                    f'Call stop_measure() or pause_measure() first.')

            # Stop instreamer tasks to free counter resources.
            # Their running state is saved and will be restored by read()/stop().
            self._scan_was_streaming = self._ni_tasks_running
            self._ni_stop_tasks()

            n         = max(1, round(t_pixel * _PI_SAMP_RATE))  # steps per pixel
            n_collect = n * n_pixels + 1   # +1 for np.diff baseline subtraction

            self._scan_n_steps  = n
            self._scan_n_pixels = n_pixels

            self.log.debug(
                f'arm  n_pixels={n_pixels}  '
                f't_pixel={t_pixel * 1e3:.3f} ms  '
                f'steps/pixel={n}  '
                f'n_collect={n_collect}  '
                f'two_channel_scan={self._two_channel_scan}'
            )

            try:
                self._scan_create_tasks(n_collect)
            except ni.DaqError as exc:
                self._scan_cleanup_unsafe(restart_stream=True)
                raise RuntimeError(f'NIXSeriesCounter.arm() failed: {exc}') from exc

    def read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Wait for the CO scan clock to complete, read all CI buffers, and
        return per-pixel photon counts for all active channels.

        Blocking: returns only after all n_pixels * n_steps + 1 CO pulses have
        been generated (i.e. the entire scan region has been traversed).

        Data processing (per channel)
        ------------------------------
        raw[k]  = cumulative photon count at the end of CO clock tick k.
        raw[0]  = baseline count at the moment the PI gate went HIGH.

        np.diff(raw) gives per-step increments with the baseline automatically
        subtracted (raw[0] cancels out in the first difference).

        reshape(n_pixels, n_steps).sum(axis=1) sums all increments within each
        pixel dwell window to give the total count per pixel.

        Scan tasks are ALWAYS cleaned up in the finally block regardless of
        success or failure.  The instreamer is restarted if it was running
        before arm() was called.

        @param n_pixels : must match the value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} raw counts,
                          or None on failure.
        """
        # Check that scan tasks are still active (read _scan_lock first).
        with self._scan_lock:
            if (self._scan_task   is None or
                    self._scan_co_task is None or
                    self._scan_reader  is None):
                self.log.error('read() called but no scan tasks are active.')
                return None
            n        = self._scan_n_steps
            n_collect = n * n_pixels + 1

        result = {}
        try:
            # Block until the CO has finished generating all n_collect clock pulses.
            # With FINITE mode + CO start trigger this is deterministic.
            self._scan_co_task.wait_until_done(timeout=self._scan_rw_timeout)
            # The CI tasks are clocked by the CO and finish simultaneously.
            self._scan_task.wait_until_done(timeout=10.0)

            # Read photon1 cumulative buffer.
            raw1 = np.zeros(n_collect, dtype=np.float64)
            self._scan_reader.read_many_sample_double(
                raw1, number_of_samples_per_channel=n_collect, timeout=10.0)
            incr1     = np.diff(raw1)   # baseline-subtracted per-step increments
            counts1   = incr1.reshape(n_pixels, n).sum(axis=1)
            result[self._scan_ch_name] = counts1

            # Read photon2 cumulative buffer (two-channel only).
            if (self._two_channel_scan and
                    self._scan_task2    is not None and
                    self._scan_reader2  is not None):
                self._scan_task2.wait_until_done(timeout=10.0)
                raw2    = np.zeros(n_collect, dtype=np.float64)
                self._scan_reader2.read_many_sample_double(
                    raw2, number_of_samples_per_channel=n_collect, timeout=10.0)
                incr2   = np.diff(raw2)
                counts2 = incr2.reshape(n_pixels, n).sum(axis=1)
                result[self._scan_ch_name2] = counts2

            self.log.debug(
                f'read OK  n_pixels={n_pixels}  steps/px={n}  '
                + '  '.join(
                    f'{ch}=(total={int(v.sum())}, mean={v.mean():.1f})'
                    for ch, v in result.items())
            )

        except ni.DaqError as exc:
            self.log.error(
                f'NIXSeriesCounter.read() failed: {exc}\n'
                f'  Confirm BNC: PI Trigger OUT -> NI {self._scan_trigger_term}\n'
                f'  Gate must go HIGH for the full scan region duration.'
            )
            return None
        finally:
            # Always clean up scan tasks and optionally restart the instreamer.
            # _scan_lock is an RLock — re-entry from the same thread is safe.
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=True)

        return result if result else None

    def stop(self) -> None:
        """
        Abort scan tasks immediately and restart the instreamer if needed.

        Called by PIE710CounterInterfuse on scan abort or emergency stop.
        Must never raise exceptions.
        """
        try:
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=True)
        except Exception as exc:
            self.log.warning(f'NIXSeriesCounter.stop(): {exc}')

    # ── Scan task helpers ──────────────────────────────────────────────────────

    def _scan_create_tasks(self, n_collect: int) -> None:
        """
        Create and start the CO + CI scan task pair(s).

        Caller must hold _scan_lock.
        Raises ni.DaqError on any NI failure — the caller (arm) handles it.

        Task start order (critical):
          1. CI task 1 — waits for CO to provide first clock edge
          2. CI task 2 — same (two-channel only)
          3. CO task   — waits for gate RISING edge from PI

        All CI tasks are clocked by the same CO internal output, guaranteeing
        that photon1 and photon2 counts are perfectly pixel-aligned.
        """
        dev       = self._device_name
        apd1      = self._scan_apd_term or self._photon_pfi_line
        clock_num = ''.join(filter(str.isdigit, self._scan_clock_ctr))
        co_output = f'/{dev}/Ctr{clock_num}InternalOutput'

        # ── CO task: finite 5 kHz pulse train, triggered by PI gate ───────────
        self._scan_co_task = ni.Task('ScanClock')
        self._scan_co_task.co_channels.add_co_pulse_chan_freq(
            counter       = f'/{dev}/{self._scan_clock_ctr}',
            freq          = _PI_SAMP_RATE,
            duty_cycle    = 0.5,
            idle_state    = ni.constants.Level.LOW,
            initial_delay = 0.0,
        )
        self._scan_co_task.timing.cfg_implicit_timing(
            sample_mode    = ni.constants.AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )
        # CO tasks support start triggers on all NI X-Series devices.
        self._scan_co_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source = f'/{dev}/{self._scan_trigger_term}',
            trigger_edge   = ni.constants.Edge.RISING,
        )

        # ── CI task 1: photon1 edge counting, clocked by CO ───────────────────
        self._scan_task = ni.Task('APDScanCounter1')
        self._scan_task.ci_channels.add_ci_count_edges_chan(
            f'/{dev}/{self._scan_counter_ch}',
            edge=ni.constants.Edge.RISING,
        )
        self._scan_task.ci_channels.all.ci_count_edges_term = f'/{dev}/{apd1}'
        self._scan_task.timing.cfg_samp_clk_timing(
            rate           = _PI_SAMP_RATE,
            source         = co_output,   # internal routing — always works
            active_edge    = ni.constants.Edge.RISING,
            sample_mode    = ni.constants.AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )
        self._scan_reader = CounterReader(self._scan_task.in_stream)
        self._scan_reader.verify_array_shape = False

        # ── CI task 2: photon2 edge counting, clocked by SAME CO ──────────────
        # Using the same CO output as CI task 1 ensures that both channels
        # receive identical sample clock edges → perfect pixel alignment.
        if self._two_channel_scan and self._scan_apd_term2_resolved:
            self._scan_task2 = ni.Task('APDScanCounter2')
            self._scan_task2.ci_channels.add_ci_count_edges_chan(
                f'/{dev}/{self._scan_counter_ch2}',
                edge=ni.constants.Edge.RISING,
            )
            self._scan_task2.ci_channels.all.ci_count_edges_term = (
                f'/{dev}/{self._scan_apd_term2_resolved}'
            )
            self._scan_task2.timing.cfg_samp_clk_timing(
                rate           = _PI_SAMP_RATE,
                source         = co_output,   # same CO output as CI task 1
                active_edge    = ni.constants.Edge.RISING,
                sample_mode    = ni.constants.AcquisitionType.FINITE,
                samps_per_chan = n_collect,
            )
            self._scan_reader2 = CounterReader(self._scan_task2.in_stream)
            self._scan_reader2.verify_array_shape = False

        # Mark as active BEFORE starting so _ni_start_tasks (if called from
        # another thread) sees the flag and excludes our counters from the
        # instreamer free pool.
        self._scan_active = True

        # Start in the correct order: CI first (ready to latch), then CO (arm).
        self._scan_task.start()
        if self._scan_task2 is not None:
            self._scan_task2.start()
        self._scan_co_task.start()

        self.log.debug(
            f'Scan tasks started -- '
            f'CO({self._scan_clock_ctr}) output -> '
            f'CI1({self._scan_counter_ch}), '
            f'CI2({self._scan_counter_ch2 if self._two_channel_scan else "N/A"})'
        )

    def _scan_cleanup_unsafe(self, restart_stream: bool = True) -> None:
        """
        Stop and close all scan tasks, then optionally restart the instreamer.

        Caller must hold _scan_lock (which is an RLock — reentrant is safe).

        Implementation note on _scan_active
        ------------------------------------
        _scan_active is set to False FIRST so that any concurrent
        _ni_start_tasks call (which reads this flag without a lock) immediately
        sees that the scan counters are free.  The actual task close follows,
        but by the time _ni_start_tasks runs and allocates counters the tasks
        are guaranteed to be closed (since this method holds _scan_lock and
        _ni_start_tasks will have run serially after this method returns).
        """
        # Signal counter availability BEFORE closing tasks.
        self._scan_active  = False
        self._scan_reader  = None
        self._scan_reader2 = None

        # Close all scan tasks in reverse start order (CO first, then CIs).
        for attr in ('_scan_co_task', '_scan_task', '_scan_task2'):
            task = getattr(self, attr, None)
            if task is not None:
                try:
                    if not task.is_task_done():
                        task.stop()
                    task.close()
                except ni.DaqError as exc:
                    self.log.warning(f'Scan task cleanup ({attr}): {exc}')
                finally:
                    setattr(self, attr, None)

        if restart_stream and self._scan_was_streaming:
            self._scan_was_streaming = False
            # Restart instreamer only when the fast counter is not holding counters.
            if self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                # _ni_start_tasks acquires _ni_tasks_lock (not _scan_lock) and
                # reads _scan_active as a plain bool — no deadlock possible.
                self._ni_start_tasks()
        else:
            self._scan_was_streaming = False