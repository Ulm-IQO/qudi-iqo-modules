# -*- coding: utf-8 -*-
"""
NI USB-63xx — Combined FastCounterInterface + DataInStreamInterface
                + Two-Channel Scanner Counter
=======================================================================

Overview
--------
This module implements three Qudi interfaces in a single hardware class for
the NI USB-6363 (also tested with 6323 and 6343):

  1. FastCounterInterface
     Time-resolved, gated photon counting.  Builds a 2-D histogram of
     photon arrival times relative to a periodic gate signal, with 10 ns
     (100 MHz) time resolution.

  2. DataInStreamInterface
     Continuous time-series streaming of photon count rates and optional
     analog voltages.  Used by the Qudi time-series-reader GUI.

  3. Scanning counter interface  (consumed by PIE710CounterInterfuse)
     Triggered pixel-by-pixel photon counting for confocal scanning.
     The PI E-710 scanner outputs one gate pulse per scan sweep; this
     module counts photons within each pixel dwell window.

Two-channel mode
----------------
Set  photon_pfi2  in the YAML config to connect a second APD or SPCM.
When photon_pfi2 is present, ALL three interfaces use both detectors:

  Fast counter:
    Both channels produce independent histograms that are SUMMED before
    returning from get_data_trace().  This matches the typical NV-centre
    experiment where both APDs collect photons from the same emitter.

  Time series:
    Six rate channels are exposed instead of two:
      rate_all_hz        APD1 non-gated count rate
      rate_gated_hz      APD1 gated count rate
      rate_all_hz_ch2    APD2 non-gated count rate
      rate_gated_hz_ch2  APD2 gated count rate
      rate_all_sum_hz    (APD1 + APD2) non-gated count rate
      rate_gated_sum_hz  (APD1 + APD2) gated count rate

  Scanning:
    Two CI counter tasks (one per APD) share a single CO clock, so both
    channels are perfectly pixel-aligned.

NI counter budget (NI USB-6363 has exactly 4 counters: ctr0–ctr3)
------------------------------------------------------------------
  One-channel fast counter running : ctr0, ctr1, ctr2 (freed early)
  Two-channel fast counter running : ctr0, ctr1, ctr2 (freed early), ctr3
  Scanning (one-channel)           : ctr0 (CI), ctr1 (CO)
  Scanning (two-channel)           : ctr0 (CI1), ctr1 (CO), ctr2 (CI2)
  Instreamer clock (always ctr3)   : ctr3  — only when FC and scan are idle

Priority (highest to lowest): fast counter > scanning > instreamer.
  start_measure() stops any active scan tasks before arming the fast counter.
  arm()           stops instreamer tasks, saves state, restores after read/stop.

Required hardware connections
-----------------------------
  PFI?   <--  APD1 / SPCM1 output    (photon_pfi)
  PFI?   <--  APD2 / SPCM2 output    (photon_pfi2, optional)
  PFI?   <--  gate / excitation pulse (gate_pfi)
  PFI?   <--  PI E-710 gate output    (scan_trigger_terminal)

YAML configuration example (two-channel, full options)
------------------------------------------------------
hardware:
  ni_combined:
    module.Class: 'ni_x_series.ni_x_series_counter.NIXSeriesCounter'
    options:
      # --- Core hardware identification ---
      device_name:             'Dev1'

      # --- Fast counter terminals ---
      photon_pfi:              'PFI8'    # APD1 input (required)
      photon_pfi2:             'PFI9'    # APD2 input (enables two-channel mode)
      gate_pfi:                'PFI10'   # Gate/excitation pulse input

      # --- Diagnostics ---
      diag_enabled:            false     # Set true to print pipeline stats
      diag_interval_s:         2.0       # Stats print interval (seconds)

      # --- Time-series streaming ---
      sample_rate:             10.0      # Poll rate in Hz (1–100)
      channel_buffer_size:     10000     # Ring buffer depth (samples)
      digital_sources:                   # PFI terminals to count edges on
        - 'PFI8'
        - 'PFI9'
      # analog_sources:
      #   - 'ai0'
      adc_voltage_range:       [-10, 10]
      read_write_timeout:      10        # NI read timeout (seconds)

      # --- Scanning counter ---
      scan_counter_channel:    'ctr0'    # CI counter for APD1 during scans
      scan_counter_channel_2:  'ctr2'    # CI counter for APD2 during scans
      scan_clock_counter:      'ctr1'    # CO counter that generates the 5 kHz scan clock
      scan_trigger_terminal:   'PFI1'    # PI E-710 gate output terminal
      scan_apd_terminal:       'PFI8'    # APD1 terminal for scanning (default: photon_pfi)
      scan_apd_terminal_2:     'PFI9'    # APD2 terminal for scanning (default: photon_pfi2)
      scan_channel_name:       'APD1'    # Channel name shown in the confocal scan GUI
      scan_channel_name_2:     'APD2'    # Channel name for APD2 in confocal scan GUI
      scan_read_timeout:       30.0      # Max seconds to wait for a scan to finish
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


# =============================================================================
#  DAQmx integer constants  (from NIDAQmx.h)
#  These are used exclusively by the low-level ctypes fast-counter path.
#  The nidaqmx Python library (used for the instreamer) handles these
#  symbolically through its constants module.
# =============================================================================
DAQmx_Val_Rising      = 10280   # active/sample on rising edge
DAQmx_Val_CountUp     = 10128   # counter increments upward
DAQmx_Val_ContSamps   = 10123   # continuous (not finite) sample mode
DAQmx_Val_DigEdge     = 10150   # digital-edge trigger type
DAQmx_Val_Ticks       = 10304   # measurement unit: 100 MHz timebase ticks
DAQmx_Val_LowFreq1Ctr = 10105   # period-measurement method for low frequencies

# NI USB-63xx internal timebase: 100 MHz oscillator.
# One tick = 10 nanoseconds — this is the histogram bin width.
_TIMEBASE_HZ = 100e6
_TICK_NS     = 1e9 / _TIMEBASE_HZ   # 10 ns per tick

# After the last gate closes, the processor waits this many extra ticks
# before committing a histogram batch.  This gives slow photon reader
# threads time to deliver photons that arrived just before gate close but
# have not yet been queued in the software buffer.
PHOTON_SLACK_TICKS = np.uint64(10_000)   # 100 µs

# Conservative upper-bound photon and gate rates used to size NI hardware
# ring buffers.  The NI USB-6363 can handle up to 10 MHz per channel.
_MAX_PHOTON_RATE_HZ = 10_000_000
_MAX_GATE_RATE_HZ   = 10_000_000

# =============================================================================
#  DataInStreamInterface channel names
#
#  Single-channel mode exposes two FC rate channels.
#  Two-channel mode additionally exposes individual APD2 rates and a summed
#  rate for APD1+APD2.  The order below matches the order in _all_channels
#  and therefore the order in the ring-buffer sample vectors.
# =============================================================================

# APD1 (always present)
_CH_ALL      = 'rate_all_hz'       # APD1 non-gated count rate (counts/s)
_CH_GATED    = 'rate_gated_hz'     # APD1 gated count rate     (counts/s)
_FC_CHANNELS = (_CH_ALL, _CH_GATED)

# APD2 individual rates (two-channel mode only)
_CH_ALL2      = 'rate_all_hz_ch2'   # APD2 non-gated count rate
_CH_GATED2    = 'rate_gated_hz_ch2' # APD2 gated count rate
_FC_CHANNELS2 = (_CH_ALL2, _CH_GATED2)

# APD1 + APD2 summed rates (two-channel mode only)
_CH_ALL_SUM      = 'rate_all_sum_hz'   # (APD1+APD2) non-gated count rate
_CH_GATED_SUM    = 'rate_gated_sum_hz' # (APD1+APD2) gated count rate
_FC_SUM_CHANNELS = (_CH_ALL_SUM, _CH_GATED_SUM)

# Sample-rate bounds for the instreamer poll thread (Hz).
_SAMPLE_RATE_MIN =   1.0
_SAMPLE_RATE_MAX = 100.0
_SAMPLE_RATE_DEF =  10.0

# NI counter assignments.
# ctr0–ctr2 are reserved by the fast counter while it runs.
# ctr3 is the instreamer sample clock when FC and scan are idle, OR the
# photon2 absolute-timestamp counter when FC is running in two-channel mode.
_FC_COUNTERS      = ('ctr0', 'ctr1', 'ctr2')
_INSTREAM_CLK_CTR = 'ctr3'

# PI E-710 waveform generator sample rate (Hz).
# Must match PIE710Controller.SAMP_RATE in pi_e710_scanning_probe.py.
# One NI sample is collected per PI waveform step (every 0.2 ms).
_PI_SAMP_RATE: float = 5000.0


# =============================================================================
#  Patched AnalogMultiChannelReader
#  The nidaqmx library changed its internal interpreter API between package
#  versions.  This subclass tries the newer interpreter path first and falls
#  back to the older direct C-function wrapper, keeping compatibility across
#  nidaqmx versions without code duplication.
# =============================================================================
class _PatchedAnalogReader(_AnalogMultiChannelReader):
    """AnalogMultiChannelReader that works with multiple nidaqmx versions."""

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
            # Newer nidaqmx versions expose a dedicated interpreter object.
            _, samps_per_chan_read = self._interpreter.read_analog_f64(
                self._handle,
                number_of_samples_per_channel,
                timeout,
                FillMode.GROUP_BY_SCAN_NUMBER.value,
                data,
            )
        except AttributeError:
            # Older nidaqmx versions: call the C function directly.
            samps_per_chan_read = _read_analog_f_64(
                self._handle, data,
                number_of_samples_per_channel, timeout,
                fill_mode=FillMode.GROUP_BY_SCAN_NUMBER,
            )
        return samps_per_chan_read


# =============================================================================
#  NIXSeriesCounter
# =============================================================================
class NIXSeriesCounter(FastCounterInterface, DataInStreamInterface):
    """
    Combined Qudi hardware module for NI USB-63xx cards (6323 / 6343 / 6363).

    Implements three interfaces:
      FastCounterInterface      : time-resolved gated photon counting
      DataInStreamInterface     : continuous time-series streaming
      Scanning counter interface : triggered pixel-by-pixel counting for
                                   confocal scanning (PIE710CounterInterfuse)

    See module docstring for full counter budget, wiring, and YAML config.
    """

    # -------------------------------------------------------------------------
    #  Qudi ConfigOptions
    #  All options have sensible defaults so the module activates even with a
    #  minimal YAML config.  'missing=warn' logs a warning if an option that
    #  influences correctness is not set.
    # -------------------------------------------------------------------------

    # Core hardware
    _device_name     = ConfigOption('device_name',     'Dev2', missing='warn')
    _photon_pfi_line = ConfigOption('photon_pfi',      'PFI0', missing='warn')
    # photon_pfi2: setting this enables two-channel mode for all interfaces.
    # Leave unset (or null) for single-channel mode.
    _photon_pfi_line2 = ConfigOption('photon_pfi2',    None,   missing='nothing')
    _gate_pfi_line   = ConfigOption('gate_pfi',        'PFI1', missing='warn')

    # Diagnostics: print pipeline buffer statistics every diag_interval_s seconds.
    _diag_enabled    = ConfigOption('diag_enabled',    True,   missing='warn')
    _diag_interval_s = ConfigOption('diag_interval_s', 2.0,    missing='warn')

    # Time-series streaming parameters
    _cfg_sample_rate      = ConfigOption('sample_rate',         _SAMPLE_RATE_DEF, missing='info')
    _cfg_channel_buf_size = ConfigOption('channel_buffer_size', 100,              missing='info')
    _cfg_digital_sources  = ConfigOption('digital_sources',     [],               missing='info')
    _cfg_analog_sources   = ConfigOption('analog_sources',      [],               missing='info')
    _cfg_adc_range        = ConfigOption('adc_voltage_range',   [-10, 10],        missing='info')
    _cfg_max_hw_buf       = ConfigOption(
        'max_channel_samples_buffer', 1024**2, missing='info',
        constructor=lambda x: max(int(round(x)), 1024**2))
    _cfg_rw_timeout = ConfigOption('read_write_timeout', 10, missing='nothing')

    # Scanning counter parameters
    # scan_counter_channel   : NI CI counter for APD1 edge counting during PI scans
    # scan_counter_channel_2 : NI CI counter for APD2 edge counting (two-channel only)
    # scan_clock_counter     : NI CO counter that generates the 5 kHz scan clock
    # scan_trigger_terminal  : PFI terminal receiving the PI E-710 gate output
    # scan_apd_terminal      : APD1 input PFI for scanning (defaults to photon_pfi)
    # scan_apd_terminal_2    : APD2 input PFI for scanning (defaults to photon_pfi2)
    # scan_channel_name      : channel label shown in the Qudi confocal GUI
    # scan_read_timeout      : max seconds to wait for a scan sweep to complete
    _scan_counter_ch  = ConfigOption('scan_counter_channel',   'ctr0', missing='nothing')
    _scan_counter_ch2 = ConfigOption('scan_counter_channel_2', 'ctr2', missing='nothing')
    _scan_clock_ctr   = ConfigOption('scan_clock_counter',     'ctr1', missing='nothing')
    _scan_trigger_term = ConfigOption('scan_trigger_terminal', 'PFI1', missing='warn')
    _scan_apd_term    = ConfigOption('scan_apd_terminal',    None,   missing='nothing')
    _scan_apd_term2   = ConfigOption('scan_apd_terminal_2',  None,   missing='nothing')
    _scan_ch_name     = ConfigOption('scan_channel_name',    'APD1', missing='nothing')
    _scan_ch_name2    = ConfigOption('scan_channel_name_2',  'APD2', missing='nothing')
    _scan_rw_timeout  = ConfigOption('scan_read_timeout',    30.0,   missing='nothing')

    # Fast-counter state-machine codes
    STATUS_UNCONFIGURED = 0
    STATUS_IDLE         = 1
    STATUS_RUNNING      = 2
    STATUS_PAUSED       = 3
    STATUS_ERROR        = -1

    # =========================================================================
    #  Construction
    # =========================================================================

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Hardware terminal byte-strings — built during on_activate().
        # Stored as bytes because the ctypes DAQmx C API requires C strings.
        self._device        = None   # e.g. b'Dev1'
        self._photon_pfi    = None   # e.g. b'/Dev1/PFI8'  — APD1 input
        self._photon2_pfi   = None   # e.g. b'/Dev1/PFI9'  — APD2 input (two-channel)
        self._gate_pfi      = None   # e.g. b'/Dev1/PFI10' — gate/excitation input
        self._timebase_term = None   # e.g. b'/Dev1/100MHzTimebase'

        # Upper-bound rates used for NI hardware buffer sizing.
        self._max_photon_rate = float(_MAX_PHOTON_RATE_HZ)
        self._max_gate_rate   = float(_MAX_GATE_RATE_HZ)

        # Two-channel mode flags.  Resolved once in on_activate() and then
        # treated as read-only constants throughout the session.
        self._two_channel_fc          = False  # True when photon_pfi2 is set
        self._two_channel_scan        = False  # True when a second APD can be resolved
        self._scan_apd_term2_resolved = None   # Resolved PFI string for scan APD2

        # Fast-counter timing — set by _fc_configure() and used by all threads.
        self._gate_width_s        = None  # gate window duration in seconds
        self._num_gates_per_cycle = None  # number of gate windows per excitation cycle
        self._gate_ticks          = None  # gate_width_s expressed in 100 MHz ticks
        self._n_bins              = None  # histogram bins per gate (= gate_ticks)

        # NI hardware ring-buffer depths and software read-chunk sizes.
        # Set by _fc_configure() based on the expected photon/gate rates.
        self._photon_buffer = None
        self._gate_buffer   = None
        self._photon_chunk  = None
        self._gate_chunk    = None

        self._status = self.STATUS_UNCONFIGURED

        # ctypes task handles — None while the fast counter is not running.
        self._photon_task  = None   # ctr0: photon1 period measurement
        self._gate_task    = None   # ctr1: gate edge absolute timestamps
        self._anchor_task  = None   # ctr2: photon1 anchor (freed after first photon)
        self._photon2_task = None   # ctr3: photon2 absolute timestamps (two-channel)

        # Software queues between reader threads and the processor thread.
        # Each reader thread appends uint64 numpy arrays to its list.
        # The processor atomically swaps out the lists to avoid blocking readers.
        self._photon_list  = []
        self._gate_list    = []
        self._photon2_list = []
        self._photon_lock  = threading.Lock()
        self._gate_lock    = threading.Lock()
        self._photon2_lock = threading.Lock()

        # 2-D histogram accumulators — shape (num_gates_per_cycle, n_bins).
        # Preserved across pause/continue so counts accumulate across segments.
        # _accumulator2 is None in single-channel mode.
        self._accumulator  = None
        self._accumulator2 = None

        # Timing references for elapsed-time calculation.
        self._t_start_ref    = [0.0]   # wall-clock time when the last DAQmxStartTask ran
        self._elapsed_time_s = 0.0     # total seconds of completed acquisition segments

        # Photon count references read by the DataInStreamInterface rate readers.
        # Updated by the processor thread after each histogram batch.
        # Separate lock because the processor and poll thread run concurrently.
        self._photon_count_ref        = [0]   # cumulative photon1 count (all in windows)
        self._gated_photon_count_ref  = [0]   # cumulative photon1 count (inside gates)
        self._photon2_count_ref       = [0]   # cumulative photon2 count (two-channel)
        self._gated_photon2_count_ref = [0]   # cumulative photon2 gated (two-channel)
        self._photon_count_lock       = threading.Lock()

        # Default rate reader for get_count_rates(); created during on_activate().
        self._default_rate_reader = None
        # Photon2 rate reader for the poll loop; created during start_stream().
        # None in single-channel mode — poll loop outputs 0.0 for those channels.
        self._poll_rate_reader2   = None

        # Diagnostics counters — updated by reader and processor threads.
        # Snapshots are printed periodically by the diag thread.
        self._diag_lock = threading.Lock()
        self._diag_reader_photons_ref    = [0]
        self._diag_reader_photons2_ref   = [0]
        self._diag_reader_gates_ref      = [0]
        self._diag_proc_photons_ref      = [0]
        self._diag_proc_photons2_ref     = [0]
        self._diag_hist_photons_ref      = [0]
        self._diag_hist_photons2_ref     = [0]
        self._diag_proc_cycles_ref       = [0]
        self._diag_hist_cycles_ref       = [0]
        self._diag_leftover_photons_ref  = [0]
        self._diag_leftover_photons2_ref = [0]
        self._diag_leftover_gates_ref    = [0]
        self._diag_snap = {
            'time': 0.0,
            'reader_photons': 0, 'reader_photons2': 0, 'reader_gates': 0,
            'proc_photons': 0,   'proc_photons2': 0,
            'hist_photons': 0,   'hist_photons2': 0,
            'proc_cycles': 0,    'hist_cycles': 0,
        }

        # Worker thread handles — None between runs.
        self._photon_thread    = None
        self._gate_thread      = None
        self._anchor_thread    = None
        self._photon2_thread   = None
        self._processor_thread = None
        self._diag_thread      = None

        # Stop events: set these to ask a worker thread to exit its loop cleanly.
        self._photon_stop     = None
        self._gate_stop       = None
        self._anchor_stop     = None
        self._photon2_stop    = None
        self._processor_stop  = None
        self._diag_stop       = None

        # Overflow events: set by a reader thread when the NI hardware FIFO
        # overflows or a fatal DAQmx error occurs.
        self._photon_overflow  = None
        self._gate_overflow    = None
        self._anchor_overflow  = None
        self._photon2_overflow = None

        # Anchor synchronisation for photon1.
        # The anchor thread (ctr2) reads the absolute 100 MHz tick of the
        # first photon1 edge and stores it here.  It then sets _t1_abs_ready
        # so the photon1 reader thread can seed its cumulative timestamp sum.
        self._t1_abs_ref   = [np.uint64(0)]
        self._t1_abs_ready = threading.Event()

        # Handle to the NI-DAQmx C library loaded via ctypes.
        # None until on_activate() succeeds.
        self._nidaq = None

        # ── Instreamer (nidaqmx Python library) state ─────────────────────────
        self._digital_sources = []   # validated list of PFI terminal names
        self._analog_sources  = []   # validated list of AI channel names
        self._all_channels    = list(_FC_CHANNELS)  # full unified channel list

        self._ni_clk_task    = None  # CO pulse task on ctr3 — instreamer sample clock
        self._ni_di_tasks    = []    # CI period tasks, one per active digital channel
        self._ni_di_readers  = []    # CounterReader handles for the CI tasks
        self._ni_ai_task     = None  # AI voltage task for all analog channels
        self._ni_ai_reader   = None  # _PatchedAnalogReader for the AI task
        self._ni_tasks_lock  = threading.Lock()  # guards _ni_start/stop_tasks

        # True while all nidaqmx instreamer tasks are running.
        self._ni_tasks_running = False

        # DataInStreamInterface runtime parameters
        self._instream_constraints = None
        self._sample_rate          = _SAMPLE_RATE_DEF
        self._channel_buffer_size  = 100
        self._active_channels      = list(_FC_CHANNELS)
        self._streaming_mode       = StreamingMode.CONTINUOUS

        # Ring buffer filled by the poll thread; consumed by read_* methods.
        self._ring_buffer = collections.deque()
        self._ring_lock   = threading.Lock()

        # Poll thread state
        self._poll_thread      = None
        self._poll_stop        = threading.Event()
        self._stream_lock      = threading.Lock()
        self._streaming        = False
        self._poll_rate_reader = None   # photon1 rate reader used by the poll loop

        # ── Scanning counter state ────────────────────────────────────────────
        # _scan_lock is a REENTRANT lock (RLock) so that read()'s finally block
        # can call _scan_cleanup_unsafe() -> _ni_start_tasks() from the same
        # thread without deadlocking.  See _scan_cleanup_unsafe() docstring.
        self._scan_lock = threading.RLock()

        self._scan_task          = None   # CI task: photon1 edge counting (ctr0)
        self._scan_co_task       = None   # CO task: 5 kHz scan clock     (ctr1)
        self._scan_task2         = None   # CI task: photon2 edge counting (ctr2, two-ch)
        self._scan_reader        = None   # CounterReader for CI task 1
        self._scan_reader2       = None   # CounterReader for CI task 2 (two-channel)
        self._scan_n_steps       = 1      # PI waveform steps per pixel
        self._scan_n_pixels      = 0      # pixels per scan line
        self._scan_was_streaming = False  # True if instreamer was running when arm() was called

        # _scan_active is a plain bool rather than a lock-protected attribute.
        # _ni_start_tasks() reads it WITHOUT acquiring _scan_lock so it can be
        # called safely from _scan_cleanup_unsafe() which already holds _scan_lock.
        # Writing is only ever done while _scan_lock IS held, which is safe under
        # the GIL — no explicit lock is needed for the read path.
        self._scan_active = False

    # =========================================================================
    #  Lifecycle
    # =========================================================================

    def on_activate(self):
        """
        Connect to the NI device, validate config, and build constraints.

        Steps:
          1. Build ctypes byte-string terminal names.
          2. Resolve two-channel mode based on photon_pfi2.
          3. Load and initialise the NI-DAQmx C library, reset the device.
          4. Validate digital/analog source lists from YAML config.
          5. Build the unified channel list (_all_channels).
          6. Create DataInStreamConstraints.
          7. Validate scan counter channel assignments.
        """
        device_name = self._device_name

        # Terminal byte-strings used by the ctypes DAQmx API.
        self._device        = device_name.encode()
        self._photon_pfi    = f'/{device_name}/{self._photon_pfi_line}'.encode()
        self._gate_pfi      = f'/{device_name}/{self._gate_pfi_line}'.encode()
        self._timebase_term = f'/{device_name}/100MHzTimebase'.encode()

        # Two-channel mode for fast counting: enabled by photon_pfi2.
        if self._photon_pfi_line2:
            self._photon2_pfi    = f'/{device_name}/{self._photon_pfi_line2}'.encode()
            self._two_channel_fc = True
        else:
            self._photon2_pfi    = None
            self._two_channel_fc = False

        # Two-channel mode for scanning: enabled if a second APD terminal exists.
        # Priority: explicit scan_apd_terminal_2 > photon_pfi2 > disabled.
        apd2_term = self._scan_apd_term2 or self._photon_pfi_line2
        if apd2_term:
            self._scan_apd_term2_resolved = apd2_term
            self._two_channel_scan        = True
        else:
            self._scan_apd_term2_resolved = None
            self._two_channel_scan        = False

        # Validate that all three scan counter channels are distinct.
        ctrs = [self._scan_counter_ch, self._scan_clock_ctr]
        if self._two_channel_scan:
            ctrs.append(self._scan_counter_ch2)
        if len(set(c.lower() for c in ctrs)) != len(ctrs):
            raise ValueError(
                f'scan_counter_channel, scan_clock_counter, and '
                f'scan_counter_channel_2 must all be different. Got: {ctrs}')

        # Load the NI-DAQmx C library and reset the device to a clean state.
        # Reset clears any tasks left over from a previous session or crash.
        self._nidaq = self._load_nidaq()
        self._declare_argtypes()
        try:
            self._check(self._nidaq.DAQmxResetDevice(self._device))
        except RuntimeError as e:
            self._nidaq = None
            raise RuntimeError(
                f"on_activate: failed to reset '{device_name}'. "
                f"Check USB connection and NI-DAQmx driver.\n{e}") from e

        # Use the nidaqmx Python library (not ctypes) to enumerate terminals.
        ni_device    = ni.system.Device(device_name)
        all_di_terms = tuple(
            t.rsplit('/', 1)[-1].lower()
            for t in ni_device.terminals if 'PFI' in t)
        all_ai_terms = tuple(
            t.rsplit('/', 1)[-1].lower()
            for t in ni_device.ai_physical_chans.channel_names)

        def _normalise(sources, valid_set, kind):
            """Strip device prefix, lower-case, and remove invalid entries."""
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

        # Enforce hardware limits on source counts.
        if len(self._digital_sources) > 3:
            self.log.warning('on_activate: >3 digital sources; only first 3 used.')
            self._digital_sources = self._digital_sources[:3]
        if len(self._analog_sources) > 16:
            self.log.warning('on_activate: >16 analog sources; only first 16 used.')
            self._analog_sources = self._analog_sources[:16]

        # Build the unified channel list.
        # Two-channel layout (indices 0–5 are FC rates, 6+ are PFI/AI):
        #   0  rate_all_hz         APD1 non-gated
        #   1  rate_gated_hz       APD1 gated
        #   2  rate_all_hz_ch2     APD2 non-gated
        #   3  rate_gated_hz_ch2   APD2 gated
        #   4  rate_all_sum_hz     APD1+APD2 non-gated
        #   5  rate_gated_sum_hz   APD1+APD2 gated
        #   6+ digital PFI channels, analog AI channels
        if self._two_channel_fc:
            self._all_channels = (
                list(_FC_CHANNELS)       # APD1 individual rates
                + list(_FC_CHANNELS2)    # APD2 individual rates
                + list(_FC_SUM_CHANNELS) # Summed APD1+APD2 rates
                + self._digital_sources
                + self._analog_sources
            )
        else:
            self._all_channels = (
                list(_FC_CHANNELS)
                + self._digital_sources
                + self._analog_sources
            )

        # Build DataInStreamConstraints with correct unit strings.
        channel_units = {ch: 'counts/s' for ch in _FC_CHANNELS}
        if self._two_channel_fc:
            channel_units.update({ch: 'counts/s' for ch in _FC_CHANNELS2})
            channel_units.update({ch: 'counts/s' for ch in _FC_SUM_CHANNELS})
        channel_units.update({ch: 'counts/s' for ch in self._digital_sources})
        channel_units.update({ch: 'V'         for ch in self._analog_sources})

        # Clamp sample rate bounds to AI hardware limits when analog sources present.
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
        # Default active channels = all channels (GUI can deselect some).
        self._active_channels = list(self._all_channels)
        self._streaming_mode  = StreamingMode.CONTINUOUS

        self._status = self.STATUS_UNCONFIGURED
        self._init_default_rate_reader()

        clock_num = ''.join(filter(str.isdigit, self._scan_clock_ctr))
        self.log.info(
            f'NIXSeriesCounter activated -- '
            f'device={device_name}  '
            f'APD1={self._photon_pfi_line}  '
            f'APD2={"DISABLED" if not self._photon_pfi_line2 else self._photon_pfi_line2}  '
            f'gate={self._gate_pfi_line}  '
            f'two_channel_fc={self._two_channel_fc}  '
            f'two_channel_scan={self._two_channel_scan}  '
            f'scan_CI1={self._scan_counter_ch}  '
            f'scan_CI2={"N/A" if not self._two_channel_scan else self._scan_counter_ch2}  '
            f'scan_CO={self._scan_clock_ctr} -> Ctr{clock_num}InternalOutput  '
            f'scan_gate={self._scan_trigger_term}'
        )

    def on_deactivate(self):
        """
        Graceful shutdown in priority order: scan > stream > fast counter > device reset.
        Safe to call from any module state — catches all exceptions internally.
        """
        # 1. Stop any active scan tasks (they hold counters the FC might need).
        if self._scan_task is not None or self._scan_co_task is not None:
            try:
                with self._scan_lock:
                    self._scan_cleanup_unsafe(restart_stream=False)
            except Exception as e:
                self.log.warning(f'on_deactivate: scan cleanup: {e}')

        # 2. Stop the time-series stream (poll thread + nidaqmx tasks).
        if self._streaming:
            try:
                self.stop_stream()
            except Exception as e:
                self.log.warning(f'on_deactivate: stop_stream: {e}')

        # 3. Stop the fast counter hardware and worker threads.
        if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED,
                            self.STATUS_ERROR):
            try:
                self._stop_hardware_and_threads()
            except Exception as e:
                self.log.warning(f'on_deactivate: FC cleanup: {e}')

        # 4. Stop all remaining nidaqmx instreamer tasks.
        self._ni_stop_tasks()

        # 5. Reset the NI device so it is in a clean state for the next session.
        if self._nidaq is not None:
            try:
                self._nidaq.DAQmxResetDevice(self._device)
            except Exception as e:
                self.log.warning(f'on_deactivate: device reset: {e}')
        self._nidaq = None
        self._status = self.STATUS_UNCONFIGURED

    # =========================================================================
    #  FastCounterInterface
    # =========================================================================

    def get_constraints(self):
        """Return hardware capability limits as required by FastCounterInterface."""
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

        The FastCounterInterface and DataInStreamInterface both define a
        configure() method.  This dispatcher routes the call to the correct
        implementation based on argument types.

        FastCounterInterface call (positional or keyword):
            configure(bin_width_s, record_length_s, number_of_gates=0)

        DataInStreamInterface call (keyword-only, as Qudi logic calls it):
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
            'counter or keyword args (active_channels, ...) for the instreamer.')

    def _fc_configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """
        FastCounterInterface configure() — set timing and allocate accumulators.

        All timing parameters are rounded to the nearest 10 ns (one 100 MHz tick).

        Parameters
        ----------
        bin_width_s     : Histogram bin width in seconds. Rounded to 10 ns.
        record_length_s : Gate window duration in seconds. Must be >= bin_width_s.
        number_of_gates : Number of gate windows per excitation cycle (>= 1).

        Returns
        -------
        (actual_bin_width_s, actual_record_length_s, number_of_gates)
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

        # NI hardware ring-buffer depths.
        # 10 seconds of data at maximum photon rate for the photon buffer;
        # 2 seconds for the gate buffer (gates are typically much less frequent).
        self._photon_buffer = max(1_000_000, int(self._max_photon_rate * 10))
        self._gate_buffer   = max(200_000,   int(self._max_gate_rate   *  2))
        # Software read-chunk: how many samples to pull from the NI FIFO per call.
        # Tuned for ~20 ms read intervals.
        read_time_s        = 0.02
        self._photon_chunk = int(self._max_photon_rate * read_time_s)
        self._gate_chunk   = int(self._max_gate_rate   * read_time_s)

        # Allocate histogram accumulators.
        shape = (num_gates, gate_ticks)
        if self._accumulator is None or self._accumulator.shape != shape:
            self._accumulator = np.zeros(shape, dtype=np.uint64)
        if self._two_channel_fc:
            if self._accumulator2 is None or self._accumulator2.shape != shape:
                self._accumulator2 = np.zeros(shape, dtype=np.uint64)
        else:
            self._accumulator2 = None   # not needed in single-channel mode

        self._reset_run_state()
        self._status = self.STATUS_IDLE
        return actual_bin_width_s, actual_record_length_s, num_gates

    def get_status(self):
        """
        Return the current state-machine status code.

        Polls overflow events so that a fatal hardware error in a reader
        thread transitions the module to STATUS_ERROR immediately rather
        than waiting for the next explicit status query.
        """
        if self._status == self.STATUS_RUNNING:
            overflow_checks = [self._photon_overflow,
                               self._gate_overflow,
                               self._anchor_overflow]
            if self._two_channel_fc:
                overflow_checks.append(self._photon2_overflow)
            if any(ev and ev.is_set() for ev in overflow_checks):
                self._status = self.STATUS_ERROR
        return self._status

    def start_measure(self):
        """
        Arm and start the fast counter.  Must be called from STATUS_IDLE.

        Priority rule: fast counter > scanning.
        If scan tasks are active they are aborted first so that ctr0–ctr2
        (and ctr3 in two-channel mode) are free for the fast counter.

        Transitions to STATUS_RUNNING.
        """
        if self._status != self.STATUS_IDLE:
            raise RuntimeError(
                f'start_measure() in invalid state {self._status}.  '
                'Call configure() first, or stop_measure() if running.')

        # Abort active scan tasks — fast counter has absolute priority.
        if self._scan_active or self._scan_task is not None:
            self.log.warning(
                'start_measure(): scan tasks are active — aborting scan first.')
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=False)

        # Release instreamer counter resources before arming the fast counter.
        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def stop_measure(self):
        """
        Stop the fast counter, print a summary, reset accumulators, and
        restart the instreamer if the stream was active.

        Call get_data_trace() BEFORE stop_measure() to preserve data.
        Transitions to STATUS_IDLE.  Safe to call from any active state.
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
        # ctr0–ctr2 (and ctr3 in two-channel mode) are now free.
        # Restart the instreamer so the time-series GUI shows data again.
        if self._streaming:
            self._ni_start_tasks()

    def pause_measure(self):
        """
        Pause the fast counter without resetting the accumulator(s).
        Accumulated histogram data is preserved across pause/continue.
        Restarts the instreamer if the stream was active.
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
        Resume a paused acquisition.  Tears down the instreamer first,
        then re-arms the fast counter.  Accumulator is preserved.
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
        """
        Return the histogram bin width in seconds (10 ns = one 100 MHz tick).
        Returns None if configure() has not been called yet.
        """
        return (1.0 / _TIMEBASE_HZ) if self._gate_ticks is not None else None

    def get_data_trace(self):
        """
        Return the accumulated histogram and metadata.

        In both single- and two-channel mode this always returns a single
        numpy array of shape (num_gates_per_cycle, n_bins) so that downstream
        logic (pulsed_measurement_logic etc.) works unchanged.

        Single-channel mode:
            data = photon1 histogram

        Two-channel mode:
            data = photon1 histogram + photon2 histogram  (element-wise sum)
            This is the correct quantity when both APDs collect photons from
            the same emitter: every detected photon contributes regardless of
            which detector it arrives at.

        info_dict contains:
            'elapsed_sweeps' : number of complete histogram cycles
            'elapsed_time'   : total acquisition time in seconds
        """
        if self._accumulator is None:
            # Not yet configured — return empty array.
            return np.zeros((1, 1), dtype=np.int64), \
                   {'elapsed_sweeps': 0, 'elapsed_time': 0.0}

        elapsed = self._elapsed_time_s
        if self._status == self.STATUS_RUNNING and self._t_start_ref[0] > 0:
            elapsed += time.monotonic() - self._t_start_ref[0]

        info_dict = {
            'elapsed_sweeps': self._diag_hist_cycles_ref[0],
            'elapsed_time':   elapsed,
        }

        hist = self._accumulator.astype(np.int64).copy()
        if self._two_channel_fc and self._accumulator2 is not None:
            # Add photon2 counts to the photon1 histogram.
            hist += self._accumulator2.astype(np.int64)

        return hist, info_dict

    # =========================================================================
    #  DataInStreamInterface — properties
    # =========================================================================

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

    # =========================================================================
    #  DataInStreamInterface — configure / start / stop / read
    # =========================================================================

    def _is_configure(self, active_channels, streaming_mode,
                      channel_buffer_size, sample_rate):
        """
        DataInStreamInterface configure() implementation.

        Always forces all FC rate channels into the active set even if the
        caller did not explicitly select them.  This ensures the time-series
        GUI always receives the rate data that it expects without needing to
        know about the two-channel mode.

        In two-channel mode the forced set includes APD1, APD2, and sum channels.
        """
        if self._streaming:
            raise RuntimeError(
                'Cannot configure the instreamer while it is running.  '
                'Call stop_stream() first.')

        streaming_mode = StreamingMode(streaming_mode)
        if streaming_mode not in self._instream_constraints.streaming_modes:
            raise ValueError(f'Invalid streaming mode "{streaming_mode}".')

        invalid = set(active_channels) - set(self._all_channels)
        if invalid:
            raise ValueError(
                f'Unknown channels: {invalid}.  '
                f'Valid channels: {set(self._all_channels)}')

        self._instream_constraints.sample_rate.check(sample_rate)
        self._instream_constraints.channel_buffer_size.check(channel_buffer_size)

        # Always include ALL FC rate channels regardless of what the GUI requested.
        # In two-channel mode this means APD1 + APD2 + sum channels are always present.
        if self._two_channel_fc:
            fc_set = (list(_FC_CHANNELS)
                      + list(_FC_CHANNELS2)
                      + list(_FC_SUM_CHANNELS))
        else:
            fc_set = list(_FC_CHANNELS)

        extra = [ch for ch in active_channels if ch not in fc_set]
        self._active_channels     = fc_set + extra
        self._streaming_mode      = streaming_mode
        self._sample_rate         = float(sample_rate)
        self._channel_buffer_size = int(channel_buffer_size)

    def start_stream(self) -> None:
        """
        Start the background poll thread.

        Also starts nidaqmx instreamer tasks for digital/analog channels,
        but ONLY if the fast counter is not currently running and no scan
        tasks are active (counters must be free).

        In two-channel mode, registers a second rate reader for APD2 so
        the poll loop can compute per-channel and summed count rates.
        """
        with self._stream_lock:
            if self._streaming:
                self.log.warning('start_stream() already running.')
                return

            # Register independent rate readers for the poll loop.
            # Each reader has its own snapshot state so multiple callers
            # can call them concurrently without interfering.
            self._poll_rate_reader = self.register_rate_reader()

            # In two-channel mode register a photon2 rate reader.
            # In single-channel mode _poll_rate_reader2 stays None and the
            # poll loop outputs 0.0 for all APD2 and sum channels.
            self._poll_rate_reader2 = (
                self._register_rate_reader2() if self._two_channel_fc else None)

            self._poll_stop.clear()
            with self._ring_lock:
                self._ring_buffer = collections.deque(
                    maxlen=self._channel_buffer_size)

            # Start nidaqmx digital/analog tasks only when counters are free.
            if (self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED)
                    and not self._scan_active):
                self._ni_start_tasks()

            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True, name='instreamer-poll')
            self._poll_thread.start()
            self._streaming = True

    def stop_stream(self) -> None:
        """Stop the poll thread and tear down all nidaqmx instreamer tasks."""
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
        """
        Read exactly samples_per_channel samples per channel from the ring buffer.
        Blocks until the requested number of samples is available.
        Data is interleaved: buffer[i * n_channels + j] = sample i of channel j.
        """
        if not self._streaming:
            raise RuntimeError('Cannot read — stream is not running.')
        n_ch = len(self._active_channels)
        # Block until enough samples are available.
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
        """Read all currently available samples. Returns samples read per channel."""
        n_ch    = len(self._active_channels)
        to_read = min(self.available_samples, data_buffer.size // n_ch)
        if to_read == 0:
            return 0
        self.read_data_into_buffer(data_buffer, to_read, timestamp_buffer)
        return to_read

    def read_data(self, samples_per_channel=None):
        """Allocate buffer and return (data_array, None)."""
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        n_ch = len(self._active_channels)
        buf  = np.empty(samples_per_channel * n_ch, dtype=np.float64)
        self.read_data_into_buffer(buf, samples_per_channel)
        return buf, None

    def read_single_point(self):
        """Return one sample per active channel.  Blocks until one sample available."""
        if not self._streaming:
            raise RuntimeError('Cannot read — stream is not running.')
        while self.available_samples == 0:
            time.sleep(0.001)
        n_ch = len(self._active_channels)
        buf  = np.empty(n_ch, dtype=np.float64)
        with self._ring_lock:
            sample = self._ring_buffer.popleft()
        for ch_idx, ch_name in enumerate(self._active_channels):
            buf[ch_idx] = sample[self._all_channels.index(ch_name)]
        return buf, None

    # =========================================================================
    #  nidaqmx instreamer — task lifecycle
    # =========================================================================

    def _ni_start_tasks(self) -> None:
        """
        Build and start all nidaqmx instreamer tasks:
          1. CO clock task on ctr3 at self._sample_rate Hz.
          2. One CI period task per active digital channel.
          3. One AI voltage task for all active analog channels.

        Counter reservation logic
        -------------------------
        Counters already in use (by FC or scan tasks) must not be claimed
        for instreamer digital channels.  _scan_active is read as a plain bool
        without acquiring _scan_lock so this method can be called safely from
        _scan_cleanup_unsafe() which already holds _scan_lock.
        """
        with self._ni_tasks_lock:
            if self._ni_tasks_running:
                return
            if not self._digital_sources and not self._analog_sources:
                return   # No nidaqmx tasks needed — FC rate channels are software-only.

            dev           = self._device_name
            clock_channel = None

            # Start the sample clock (ctr3 CO pulse task).
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
                clock_channel     = f'/{clk_task.channel_names[0]}InternalOutput'
            except ni.DaqError as e:
                self.log.error(
                    f'_ni_start_tasks: clock task failed: {e}. '
                    'Digital/analog instreamer channels are unavailable.')
                self._ni_stop_tasks_unsafe()
                return

            # Build the set of counters that must not be used for digital channels.
            fc_active     = self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED)
            reserved_ctrs = (set(_FC_COUNTERS) if fc_active else set()) | {_INSTREAM_CLK_CTR}
            if fc_active and self._two_channel_fc:
                reserved_ctrs.add('ctr3')   # ctr3 holds photon2 in two-channel FC mode
            if self._scan_active:
                # Read _scan_active as plain bool — no lock needed (see docstring).
                reserved_ctrs |= {self._scan_counter_ch.lower(),
                                  self._scan_clock_ctr.lower()}
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

            # One CI period task per active digital channel.
            active_di = [ch for ch in self._digital_sources
                         if ch in self._active_channels]
            free_iter = iter(free_ctrs)
            for chnl in active_di:
                ctr = next(free_iter, None)
                if ctr is None:
                    self.log.warning(
                        f'_ni_start_tasks: no free counter for "{chnl}" -- outputs 0.')
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
                    # Route clock and signal terminals via direct C-API calls
                    # to avoid a known nidaqmx property-getter bug.
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
                        f'_ni_start_tasks: DI task failed for "{chnl}": {e}.')
                    try:
                        task.close()
                    except Exception:
                        pass

            # One AI task for all active analog channels.
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

            # Start digital and analog tasks before the clock so they are
            # ready to receive clock edges from the moment the clock starts.
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
                f'Analog:  {started_ai or "none"}'
            )

    def _ni_stop_tasks(self) -> None:
        """Stop all nidaqmx instreamer tasks (thread-safe)."""
        with self._ni_tasks_lock:
            self._ni_stop_tasks_unsafe()

    def _ni_stop_tasks_unsafe(self) -> None:
        """
        Stop all nidaqmx instreamer tasks.
        Must be called with _ni_tasks_lock already held.
        """
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
        Read one sample from every active nidaqmx instreamer channel.

        Returns a float64 array of length (n_digital + n_analog).
        Digital values are in counts/s.  Analog values are in Volts.
        Returns zeros for any channel whose task is not running.

        Called from the poll thread — must not block for more than one poll interval.
        Uses timeout=0 to drain all buffered samples and return the mean.
        """
        n_di   = len(self._digital_sources)
        n_ai   = len(self._analog_sources)
        result = np.zeros(n_di + n_ai, dtype=np.float64)

        if not self._ni_tasks_running:
            return result

        try:
            _tmp = np.empty(self._channel_buffer_size, dtype=np.float64)
            for i, reader in enumerate(self._ni_di_readers):
                # Read all available samples and take the mean.
                # Multiply by sample_rate: the CI period task returns inter-photon
                # periods in clock ticks; mean(ticks) * rate = counts/s.
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

    # =========================================================================
    #  Background poll thread
    # =========================================================================

    def _poll_loop(self) -> None:
        """
        Background thread running at self._sample_rate Hz.

        Every tick assembles one unified sample vector whose layout exactly
        matches _all_channels and appends it to the ring buffer.

        Single-channel layout:
            [0] rate_all_hz       APD1 non-gated count rate  (counts/s)
            [1] rate_gated_hz     APD1 gated count rate      (counts/s)
            [2+] digital PFI channels                        (counts/s)
            [N+] analog AI channels                          (V)

        Two-channel layout:
            [0] rate_all_hz       APD1 non-gated              (counts/s)
            [1] rate_gated_hz     APD1 gated                  (counts/s)
            [2] rate_all_hz_ch2   APD2 non-gated              (counts/s)
            [3] rate_gated_hz_ch2 APD2 gated                  (counts/s)
            [4] rate_all_sum_hz   (APD1+APD2) non-gated       (counts/s)
            [5] rate_gated_sum_hz (APD1+APD2) gated           (counts/s)
            [6+] digital PFI channels                         (counts/s)
            [N+] analog AI channels                           (V)

        All rate channels output 0.0 when the fast counter is not running.
        Digital/analog channels output 0.0 when nidaqmx tasks are paused.
        """
        interval    = 1.0 / self._sample_rate
        two_channel = self._two_channel_fc
        n_total     = len(self._all_channels)

        # Number of FC rate channels at the front of the vector.
        # Used to correctly splice in the digital/analog values.
        n_fc_total = (len(_FC_CHANNELS) + len(_FC_CHANNELS2) + len(_FC_SUM_CHANNELS)
                      if two_channel else len(_FC_CHANNELS))

        while not self._poll_stop.is_set():
            t0 = time.monotonic()

            if (self._status == self.STATUS_RUNNING
                    and self._poll_rate_reader is not None):
                # Read APD1 count rates from the photon1 rate reader.
                rate_all1, rate_gated1 = self._poll_rate_reader()
                # Read APD2 count rates (two-channel only).
                if two_channel and self._poll_rate_reader2 is not None:
                    rate_all2, rate_gated2 = self._poll_rate_reader2()
                else:
                    rate_all2 = rate_gated2 = 0.0
            else:
                rate_all1 = rate_gated1 = rate_all2 = rate_gated2 = 0.0

            # Read digital and analog nidaqmx channels.
            ni_sample = self._ni_read_sample()   # shape: (n_digital + n_analog,)

            # Assemble the unified sample vector.
            sample = np.empty(n_total, dtype=np.float64)

            # APD1 individual rates always occupy indices 0 and 1.
            sample[0] = rate_all1
            sample[1] = rate_gated1

            if two_channel:
                # APD2 individual rates at indices 2 and 3.
                sample[2] = rate_all2
                sample[3] = rate_gated2
                # Summed APD1+APD2 rates at indices 4 and 5.
                sample[4] = rate_all1 + rate_all2
                sample[5] = rate_gated1 + rate_gated2

            # Digital and analog values fill the remaining indices.
            sample[n_fc_total:] = ni_sample

            with self._ring_lock:
                self._ring_buffer.append(sample)

            # Sleep for the remainder of the poll interval.
            elapsed    = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                self._poll_stop.wait(timeout=sleep_time)

    # =========================================================================
    #  FastCounterInterface helpers — rate readers and status
    # =========================================================================

    def get_count_rates(self):
        """
        Return (rate_all_hz, rate_gated_hz) for APD1.
        Returns (0.0, 0.0) before the first complete histogram cycle.
        """
        if self._default_rate_reader is None:
            return 0.0, 0.0
        return self._default_rate_reader()

    def register_rate_reader(self):
        """
        Return an independent APD1 rate-reading callable.

        Each call creates a new callable with its own private snapshot state
        (last_time, last_counts, last_valid_rates).  Multiple callers never
        interfere with each other's rate estimates.

        The callable:
          - Returns the last valid rates when no new data has arrived.
          - Returns (0.0, 0.0) before the first histogram cycle completes.
          - Reads from _photon_count_ref (all photon1) and
            _gated_photon_count_ref (photon1 inside gate windows).
        """
        state = {
            'last_time'        : 0.0,
            'last_photon_snap' : 0,
            'last_gated_snap'  : 0,
            'last_cycle_snap'  : 0,
            'last_valid_rates' : (0.0, 0.0),
        }
        # Capture references at closure creation time so the callable remains
        # valid even after a re-configure that replaces module attributes.
        photon_count_ref       = self._photon_count_ref
        gated_photon_count_ref = self._gated_photon_count_ref
        diag_hist_cycles_ref   = self._diag_hist_cycles_ref
        photon_count_lock      = self._photon_count_lock
        diag_lock              = self._diag_lock

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
                return state['last_valid_rates']   # no new data yet
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

    def _register_rate_reader2(self):
        """
        Return an independent APD2 rate-reading callable.

        Identical structure to register_rate_reader() but reads from the
        photon2 count references.  Only called in two-channel mode.

        Returns (0.0, 0.0) if the fast counter is not running or before
        the first complete histogram cycle.
        """
        state = {
            'last_time'        : 0.0,
            'last_photon_snap' : 0,
            'last_gated_snap'  : 0,
            'last_cycle_snap'  : 0,
            'last_valid_rates' : (0.0, 0.0),
        }
        photon2_count_ref       = self._photon2_count_ref
        gated_photon2_count_ref = self._gated_photon2_count_ref
        diag_hist_cycles_ref    = self._diag_hist_cycles_ref
        photon_count_lock       = self._photon_count_lock
        diag_lock               = self._diag_lock

        def _read():
            now = time.monotonic()
            dt  = now - state['last_time']
            if self._num_gates_per_cycle is None or self._gate_width_s is None:
                return 0.0, 0.0
            with photon_count_lock:
                cur_all   = photon2_count_ref[0]
                cur_gated = gated_photon2_count_ref[0]
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
        """Create the default APD1 rate reader used by get_count_rates()."""
        self._default_rate_reader = self.register_rate_reader()

    def get_hardware_status(self):
        """Return a snapshot of NI hardware FIFO depths and software queue sizes."""
        hw_ph  = self._get_hw_available(self._photon_task)  if self._photon_task  else -1
        hw_ph2 = self._get_hw_available(self._photon2_task) if self._photon2_task else -1
        hw_gt  = self._get_hw_available(self._gate_task)    if self._gate_task    else -1
        with self._photon_lock:
            sw_ph  = sum(len(a) for a in self._photon_list)
        with self._photon2_lock:
            sw_ph2 = sum(len(a) for a in self._photon2_list)
        with self._gate_lock:
            sw_gt  = sum(len(a) for a in self._gate_list)
        return {
            'hw_photon1_available' : hw_ph,
            'hw_photon2_available' : hw_ph2,
            'hw_gate_available'    : hw_gt,
            'sw_photon1_samples'   : sw_ph,
            'sw_photon2_samples'   : sw_ph2,
            'sw_gate_samples'      : sw_gt,
        }

    def print_summary(self):
        """Print a human-readable summary of the most recent acquisition run."""
        if self._accumulator is None:
            print('No data — device not configured.')
            return
        data, info = self.get_data_trace()
        cycles_done   = info['elapsed_sweeps']
        elapsed_total = info['elapsed_time']
        if cycles_done == 0:
            print('No complete cycles acquired yet.')
            return

        # data is always (num_gates, n_bins) — sum of all channels.
        total_photons   = int(data.sum())
        total_gate_time = (cycles_done
                           * self._num_gates_per_cycle
                           * self._gate_width_s)
        rate_gated = total_photons / total_gate_time if total_gate_time > 0 else 0.0

        if elapsed_total > 0 and cycles_done > 0:
            gate_period_s = elapsed_total / (cycles_done * self._num_gates_per_cycle)
            dead_time_ns  = (gate_period_s - self._gate_width_s) * 1e9
            rate_seq      = total_photons / elapsed_total
            duty_cycle    = 100.0 * self._gate_width_s / gate_period_s
        else:
            dead_time_ns = rate_seq = duty_cycle = 0.0

        mode = ('two-channel (APD1+APD2 summed)'
                if self._two_channel_fc else 'single-channel')
        sep  = '--' * 30
        print(f'\n{sep}')
        print(f'  Mode                  : {mode}')
        print(f'  Cycles completed      : {cycles_done}')
        print(f'  Elapsed time          : {elapsed_total:.3f} s')
        print(f'  Gate width            : {self._gate_width_s * 1e6:.3f} us')
        print(f'  Dead time (inferred)  : {dead_time_ns:.1f} ns')
        print(f'  Duty cycle            : {duty_cycle:.1f} %')
        print(f'  Total photons (sum)   : {total_photons:,}')
        print(f'  Count rate (gated)    : {rate_gated / 1e3:.2f} kHz')
        print(f'  Count rate (sequence) : {rate_seq / 1e3:.2f} kHz')

        if self._two_channel_fc and self._accumulator2 is not None:
            ph1 = int(self._accumulator.sum())
            ph2 = int(self._accumulator2.sum())
            pct1 = f' ({100.0 * ph1 / total_photons:.1f} %)' if total_photons > 0 else ''
            pct2 = f' ({100.0 * ph2 / total_photons:.1f} %)' if total_photons > 0 else ''
            print(f'  --- Per-channel breakdown ---')
            print(f'  {self._scan_ch_name} photons : {ph1:,}{pct1}')
            print(f'  {self._scan_ch_name2} photons : {ph2:,}{pct2}')
        print(f'{sep}')

    # =========================================================================
    #  Fast counter hardware and thread lifecycle
    # =========================================================================

    def _reset_run_state(self):
        """
        Zero all runtime accumulators and counters without touching timing config.

        Called by _fc_configure(), stop_measure(), and pause_measure().
        Also resets photon2 references even in single-channel mode so that
        switching modes between configure() calls never leaves stale values.
        """
        if self._accumulator is not None:
            self._accumulator[:] = 0
        if self._accumulator2 is not None:
            self._accumulator2[:] = 0

        self._t_start_ref[0]  = 0.0
        self._elapsed_time_s  = 0.0

        with self._photon_count_lock:
            self._photon_count_ref[0]        = 0
            self._gated_photon_count_ref[0]  = 0
            self._photon2_count_ref[0]       = 0
            self._gated_photon2_count_ref[0] = 0

        self._t1_abs_ref[0] = np.uint64(0)
        self._t1_abs_ready.clear()

        # Re-create the default rate reader so get_count_rates() returns fresh
        # rates rather than stale snapshots from the previous run.
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
        Create all ctypes DAQmx tasks and launch every worker thread.

        Counter assignment:
          ctr0  photon1 period measurement  (inter-photon intervals)
          ctr1  gate edge absolute timestamps
          ctr2  photon1 anchor              (freed after first photon)
          ctr3  photon2 absolute timestamps (two-channel mode only)

        Hardware is armed BEFORE threads start so photon/gate edges that
        arrive during thread startup are buffered in the NI FIFOs and not lost.
        """
        if self._nidaq is None:
            raise RuntimeError('_start_hardware_and_threads() called before on_activate().')

        dev = self._device.decode()

        # Create ctypes DAQmx counter tasks.
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
            self._photon2_task = self._make_photon2_timestamp_task(
                f'{dev}/ctr3'.encode(), self._photon2_pfi, self._gate_pfi,
                self._photon_buffer, self._max_photon_rate)

        # Create per-run stop/overflow events.
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
        self._anchor_thread = self._make_anchor_reader_thread()
        self._photon_thread = self._make_reader_thread(
            self._photon_task, self._photon_chunk,
            self._photon_list, self._photon_lock,
            self._photon_stop, self._photon_overflow, 'photon')
        self._gate_thread = self._make_reader_thread(
            self._gate_task, self._gate_chunk,
            self._gate_list, self._gate_lock,
            self._gate_stop, self._gate_overflow, 'gate')
        if self._two_channel_fc:
            # photon2 uses the rollover-correction path (label='photon2'),
            # same as the gate reader.  It does NOT wait for _t1_abs_ready
            # because its timestamps are already absolute.
            self._photon2_thread = self._make_reader_thread(
                self._photon2_task, self._photon_chunk,
                self._photon2_list, self._photon2_lock,
                self._photon2_stop, self._photon2_overflow, 'photon2')
        self._processor_thread = self._make_processor_thread()
        self._diag_thread      = self._make_diag_thread()

        # Arm all hardware BEFORE starting threads.
        self._check(self._nidaq.DAQmxStartTask(self._photon_task))
        self._check(self._nidaq.DAQmxStartTask(self._gate_task))
        self._check(self._nidaq.DAQmxStartTask(self._anchor_task))
        if self._two_channel_fc:
            self._check(self._nidaq.DAQmxStartTask(self._photon2_task))

        self._t_start_ref[0] = time.monotonic()

        # Start anchor first to minimise the time the photon reader blocks
        # on _t1_abs_ready.
        self._anchor_thread.start()
        self._photon_thread.start()
        self._gate_thread.start()
        if self._two_channel_fc:
            self._photon2_thread.start()
        self._processor_thread.start()
        self._diag_thread.start()

    def _stop_hardware_and_threads(self):
        """Stop all ctypes DAQmx tasks and join every worker thread."""
        # Stop the photon and gate tasks (they have the largest FIFOs).
        for task in (self._photon_task, self._gate_task):
            if task:
                self._nidaq.DAQmxStopTask(task)
        if self._two_channel_fc and self._photon2_task:
            self._nidaq.DAQmxStopTask(self._photon2_task)

        # The anchor task might have already been cleared by the anchor thread.
        if self._anchor_task:
            try:
                self._nidaq.DAQmxStopTask(self._anchor_task)
                self._nidaq.DAQmxClearTask(self._anchor_task)
            except Exception:
                pass
            self._anchor_task = None

        # Signal all threads to exit their main loops.
        for ev in (self._anchor_stop, self._photon_stop, self._gate_stop,
                   self._processor_stop, self._diag_stop):
            if ev:
                ev.set()
        if self._photon2_stop:
            self._photon2_stop.set()

        # Safety unblock: if the anchor thread errored before setting
        # _t1_abs_ready the photon reader would hang forever.
        self._t1_abs_ready.set()

        for t, tmo in ((self._anchor_thread,    3.0),
                       (self._diag_thread,      3.0),
                       (self._processor_thread, 5.0),
                       (self._photon_thread,    2.0),
                       (self._gate_thread,      2.0),
                       (self._photon2_thread,   2.0)):
            if t and t.is_alive():
                t.join(timeout=tmo)

        # Clear the task handles.
        if self._photon_task:
            self._nidaq.DAQmxClearTask(self._photon_task)
            self._photon_task = None
        if self._gate_task:
            self._nidaq.DAQmxClearTask(self._gate_task)
            self._gate_task = None
        if self._two_channel_fc and self._photon2_task:
            self._nidaq.DAQmxClearTask(self._photon2_task)
            self._photon2_task = None

        # Drain software queues.
        with self._photon_lock:
            self._photon_list.clear()
        with self._gate_lock:
            self._gate_list.clear()
        with self._photon2_lock:
            self._photon2_list.clear()

    # =========================================================================
    #  ctypes DAQmx wrappers
    # =========================================================================

    @staticmethod
    def _load_nidaq():
        """Load the NI-DAQmx C library for the current OS."""
        if os.name == 'nt':
            return ctypes.windll.nicaiu       # Windows
        return ctypes.cdll.LoadLibrary('libnidaqmx.so')  # Linux

    def _declare_argtypes(self):
        """
        Declare C-level argtypes for every DAQmx function used by the ctypes path.

        Without explicit argtypes ctypes guesses integer widths, which can
        silently pass wrong bit patterns and cause -200077 or similar errors.
        This must be called once after loading the library.
        """
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
        """Raise RuntimeError for any non-zero DAQmx error code."""
        if err != 0:
            buf = ctypes.create_string_buffer(2048)
            self._nidaq.DAQmxGetErrorString(err, buf, 2048)
            raise RuntimeError(f'DAQmx Error {err}: {buf.value.decode()}')

    def _get_hw_available(self, task_handle):
        """Return the number of samples waiting in the NI FIFO, or -1 on error."""
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

        Measures the time interval (in 100 MHz ticks) between consecutive
        RISING edges on photon_pfi.  The counter is armed by a RISING edge
        on start_trigger (the gate signal) so pre-gate photons are excluded.

        The first returned value is the interval from the gate RISING edge to
        the first photon edge — used by the anchor thread as t1_abs seed.
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

        Counts the 100 MHz timebase continuously and latches the current
        count on each RISING edge of gate_pfi.  Each latched value is the
        absolute 100 MHz tick of that gate opening event.

        The 32-bit counter rolls over every ~43 s; the reader thread detects
        and corrects rollovers to produce monotonic uint64 timestamps.
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

        Same structure as the gate timestamp task (ctr1) but clocked by
        photon_pfi instead of gate_pfi.  The first latched value is the
        absolute 100 MHz tick of the first photon1 edge after the gate arms.

        The anchor thread reads exactly ONE value, stores it in _t1_abs_ref[0],
        then calls DAQmxClearTask to free ctr2 for other uses.

        Why is this needed?
        -------------------
        The photon period-measurement task (ctr0) returns inter-photon intervals.
        Cumulative sum reconstructs absolute timestamps, but we need to know
        the absolute time of the FIRST photon.  The anchor counter provides this.
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

        Why count-edges instead of period-measurement + anchor?
        --------------------------------------------------------
        Photon1 uses period-measurement (ctr0) + an anchor counter (ctr2) to
        reconstruct absolute timestamps.  For photon2 there is no counter left
        for a second anchor.

        Instead, ctr3 uses the exact same count-edges + sample-clock approach
        as the gate counter (ctr1): the 100 MHz timebase is counted continuously
        and its value is latched on each photon2 rising edge.  The latched values
        are already absolute timestamps — no reconstruction is required.  The
        reader thread applies rollover correction identical to the gate reader.

        Task layout:
          Counter source : 100 MHz internal timebase (counts ticks)
          Sample clock   : photon2_pfi (latch on each photon2 rising edge)
          Arm trigger    : gate_pfi rising edge (same as ctr0/ctr1)
        """
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        self._check(self._nidaq.DAQmxCreateCICountEdgesChan(
            h, channel, b'', DAQmx_Val_Rising, 0, DAQmx_Val_CountUp))
        self._check(self._nidaq.DAQmxSetCICountEdgesTerm(
            h, channel, self._timebase_term))
        self._check(self._nidaq.DAQmxCfgSampClkTiming(
            h, photon2_pfi, float(max_rate), DAQmx_Val_Rising, DAQmx_Val_ContSamps,
            ctypes.c_uint64(buffer_size)))
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(h, DAQmx_Val_Rising))
        return h

    # =========================================================================
    #  Fast-counter thread factories
    # =========================================================================

    def _make_anchor_reader_thread(self):
        """
        Factory for the photon1 anchor thread.

        Polls ctr2 until the first photon1 edge latches a 100 MHz tick value.
        Stores the value in _t1_abs_ref[0], signals _t1_abs_ready, then
        stops and clears ctr2 to free the counter for other uses.

        This thread exits immediately after reading the single anchor value.
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
            # Poll until the first photon1 edge provides a tick count.
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
                t1_abs_ready.set()   # unblock photon reader so it can exit
                return
            t1_abs_ref[0] = np.uint64(raw_buf[0])
            if diag_enabled:
                print(f'[anchor] t1_abs = {t1_abs_ref[0]} ticks '
                      f'({int(t1_abs_ref[0]) * _TICK_NS * 1e-6:.3f} ms after arm)',
                      flush=True)
            t1_abs_ready.set()   # photon reader can now seed its cumsum
            # Free ctr2 — it is no longer needed.
            nidaq.DAQmxStopTask(anchor_task)
            nidaq.DAQmxClearTask(anchor_task)
            if diag_enabled:
                print('[anchor] ctr2 freed.', flush=True)

        return threading.Thread(target=_run, daemon=True, name='anchor')

    def _make_reader_thread(self, task_handle, chunk_size, shared_list, lock,
                            stop_event, overflow_event, label):
        """
        Factory for a DAQmx counter reader thread.

        Three label values select different timestamp reconstruction strategies:

        label == 'photon'
            Reads inter-photon intervals from the ctr0 period-measurement task.

            Waits for _t1_abs_ready before emitting any data — ensures the
            cumsum is seeded at the correct absolute origin.

            On the first batch, prepends a 0 so that cumsum gives:
                timestamps = t1_abs + [0, i1, i1+i2, i1+i2+i3, ...]
            where i_k are the measured inter-photon intervals in 100 MHz ticks.

        label == 'gate'  or  label == 'photon2'
            Reads absolute 32-bit tick counts from a count-edges-with-
            sample-clock task (ctr1 or ctr3).

            Both use 32-bit hardware counters that wrap every ~43 s.
            Monotonic uint64 timestamps are reconstructed by detecting
            negative signed deltas:
              - Inter-chunk wrap: between the last emitted value and the
                first new value in the current batch.
              - Intra-chunk wrap: between consecutive raw values within
                a single batch.

            'photon2' does NOT wait for _t1_abs_ready because its
            timestamps are already absolute and independent of ctr2.
        """
        diag_enabled = self._diag_enabled
        raw_buf      = (ctypes.c_uint32 * chunk_size)()
        samps_read   = ctypes.c_int32(0)
        nidaq        = self._nidaq

        if label == 'photon':
            diag_ref = self._diag_reader_photons_ref
        elif label == 'photon2':
            diag_ref = self._diag_reader_photons2_ref
        else:
            diag_ref = self._diag_reader_gates_ref
        diag_lock    = self._diag_lock

        t1_abs_ref   = self._t1_abs_ref
        t1_abs_ready = self._t1_abs_ready

        # Per-thread local state (one closure instance per thread).
        if label == 'photon':
            period_state = {'abs_tick': np.uint64(0), 't1_emitted': False}
        else:
            rollover_state = {'prev_rollover': np.uint64(0),
                              'last_abs':      np.uint64(0)}

        # Minimum batch size before reading: avoids busy-polling overhead.
        min_batch = max(100, chunk_size // 100)

        def _run():
            avail = ctypes.c_uint32(0)

            # photon1 reader waits for the anchor before processing.
            if label == 'photon':
                if diag_enabled:
                    print('[photon1 reader] waiting for anchor...', flush=True)
                t1_abs_ready.wait()
                period_state['abs_tick'] = t1_abs_ref[0]
                if diag_enabled:
                    print(f'[photon1 reader] seeded t1_abs={period_state["abs_tick"]}',
                          flush=True)

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
                if err > 0:   # positive = warning (e.g. buffer overflow)
                    buf = ctypes.create_string_buffer(2048)
                    nidaq.DAQmxGetErrorString(err, buf, 2048)
                    self.log.warning(
                        f'[reader-{label}] warning {err}: {buf.value.decode()}')
                if n == 0:
                    continue

                if label == 'photon':
                    # Period-measurement path.
                    # raw values are inter-photon intervals in 100 MHz ticks.
                    # Cumsum converts them to absolute offsets from t1_abs.
                    intervals = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                 .copy().astype(np.uint64))
                    if not period_state['t1_emitted']:
                        # Prepend 0 so the first absolute timestamp = t1_abs
                        # (the anchor photon itself).
                        intervals = np.concatenate(
                            [np.array([0], dtype=np.uint64), intervals])
                        period_state['t1_emitted'] = True
                    absolute = period_state['abs_tick'] + np.cumsum(intervals)
                    period_state['abs_tick'] = absolute[-1]

                else:
                    # Count-edges-with-rollover path (gate and photon2).
                    # raw values are 32-bit absolute tick counts that wrap every ~43 s.
                    # Reconstruct monotonic uint64 by detecting sign-flip wraps.
                    counts64    = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                   .copy().astype(np.uint64))
                    offsets     = np.zeros(n, dtype=np.uint64)
                    n_new_wraps = np.uint64(0)

                    if rollover_state['last_abs'] > 0:
                        # Inter-chunk wrap: negative signed delta from the last
                        # value of the previous chunk to the first of this one.
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
        Factory for the histogram processor thread.

        This thread is the core of the fast counter.  It consumes photon and
        gate timestamps from the software queues and fills the histogram
        accumulator(s).

        In two-channel mode it processes photon1 and photon2 independently
        against the same gate timestamps, updating two separate accumulators.

        Algorithm overview (per batch)
        --------------------------------
        Phase alignment (first call only):
            The NI hardware uses the first gate edge as its arm trigger, so
            ctr1's first latch value is actually the SECOND physical gate.
            Discarding the first (num_gates_per_cycle - 1) timestamps aligns
            the processor to true cycle boundaries.

        Main processing loop:
            1. Wait until at least one complete gate cycle is available.
            2. Collect photon data from all reader queues.
            3. Wait for all photon streams to advance past
               last_gate_close + PHOTON_SLACK_TICKS.
               A 5-second safety timeout prevents stalling if an APD has a
               very low or zero photon rate.
            4. Drain any late-arriving photon chunks.
            5. Call histogram_batch() for each active channel.
            6. Accumulate results and update count/rate references.
            7. Carry forward data past last_cycle_end to the next iteration.
        """
        # Capture references at thread creation time so the closure remains
        # valid even if module attributes are later changed.
        photon_list  = self._photon_list
        photon2_list = self._photon2_list
        gate_list    = self._gate_list
        photon_lock  = self._photon_lock
        photon2_lock = self._photon2_lock
        gate_lock    = self._gate_lock

        accumulator  = self._accumulator
        accumulator2 = self._accumulator2
        two_channel  = self._two_channel_fc

        stop_event      = self._processor_stop
        overflow_events = [self._photon_overflow,
                           self._gate_overflow,
                           self._anchor_overflow]
        if two_channel:
            overflow_events.append(self._photon2_overflow)

        photon_count_ref        = self._photon_count_ref
        gated_photon_count_ref  = self._gated_photon_count_ref
        photon2_count_ref       = self._photon2_count_ref
        gated_photon2_count_ref = self._gated_photon2_count_ref
        photon_count_lock       = self._photon_count_lock

        num_gates_per_cycle = self._num_gates_per_cycle
        gate_ticks          = self._gate_ticks
        n_bins              = self._n_bins
        histogram_batch     = self._histogram_batch

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
            leftover_photons  = np.empty(0, dtype=np.uint64)
            leftover_photons2 = np.empty(0, dtype=np.uint64)
            leftover_gates    = np.empty(0, dtype=np.uint64)

            # ── Phase alignment ───────────────────────────────────────────────
            # Wait until num_gates_per_cycle gate timestamps have arrived,
            # then keep only the last one (= first gate of the second cycle).
            # All photon data before this cycle origin is discarded.
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
                    new_g = np.concatenate(chunks)
                    leftover_gates = (np.concatenate([leftover_gates, new_g])
                                      if len(leftover_gates) else new_g)

                leftover_gates = leftover_gates[phase_n - 1:]  # keep only cycle origin
                cutoff         = leftover_gates[0]

                # Discard photon1 data before the cycle origin.
                with photon_lock:
                    chunks = photon_list.copy(); photon_list.clear()
                if chunks:
                    new_p = np.concatenate(chunks)
                    leftover_photons = (np.concatenate([leftover_photons, new_p])
                                        if len(leftover_photons) else new_p)
                split = np.searchsorted(leftover_photons, cutoff, side='left')
                leftover_photons = leftover_photons[split:]

                # Discard photon2 data before the cycle origin (two-channel).
                if two_channel and accumulator2 is not None:
                    with photon2_lock:
                        chunks2 = photon2_list.copy(); photon2_list.clear()
                    if chunks2:
                        new_p2 = np.concatenate(chunks2)
                        leftover_photons2 = (np.concatenate([leftover_photons2, new_p2])
                                             if len(leftover_photons2) else new_p2)
                    split2 = np.searchsorted(leftover_photons2, cutoff, side='left')
                    leftover_photons2 = leftover_photons2[split2:]

                phase_aligned = True

            # ── Main processing loop ──────────────────────────────────────────
            while not stop_event.is_set():
                if any(ev.is_set() for ev in overflow_events):
                    stop_event.set(); break

                # Wait for at least one complete gate cycle.
                with gate_lock:
                    gate_count = (sum(len(a) for a in gate_list)
                                  + len(leftover_gates))
                if gate_count < num_gates_per_cycle:
                    time.sleep(0.001); continue

                # Atomically drain queues.
                with photon_lock:
                    new_ph_chunks = photon_list.copy(); photon_list.clear()
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

                # Wait for all photon streams to advance past the deadline.
                # 5-second safety timeout prevents stalling on low-rate channels.
                deadline_time = time.monotonic() + 5.0
                while not stop_event.is_set():
                    if any(ev.is_set() for ev in overflow_events):
                        stop_event.set(); break

                    ph1_max = all_photons[-1] if len(all_photons) else np.uint64(0)
                    with photon_lock:
                        for chunk in photon_list:
                            if len(chunk) and chunk[-1] > ph1_max:
                                ph1_max = chunk[-1]
                    deadline_met = ph1_max >= photon_deadline

                    if two_channel and accumulator2 is not None:
                        ph2_max = all_photons2[-1] if len(all_photons2) else np.uint64(0)
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

                # Drain any late-arriving photon1 chunks.
                with photon_lock:
                    late = photon_list.copy(); photon_list.clear()
                if late:
                    late_arr = np.concatenate(late)
                    late_arr.sort()
                    all_photons = (np.concatenate([all_photons, late_arr])
                                   if len(all_photons) else late_arr)

                # Drain any late-arriving photon2 chunks (two-channel).
                if two_channel and accumulator2 is not None:
                    with photon2_lock:
                        late2 = photon2_list.copy(); photon2_list.clear()
                    if late2:
                        late2_arr = np.concatenate(late2)
                        late2_arr.sort()
                        all_photons2 = (np.concatenate([all_photons2, late2_arr])
                                        if len(all_photons2) else late2_arr)

                # ── Histogram photon1 ─────────────────────────────────────────
                ph_lo = np.searchsorted(
                    all_photons, gate_rise_batch[0], side='left')
                ph_hi = np.searchsorted(
                    all_photons, last_cycle_end, side='right')
                photons1_batch = all_photons[ph_lo:ph_hi]

                batch_hist1    = histogram_batch(
                    photons1_batch, gate_rise_batch,
                    num_gates_per_cycle, n_bins, gate_ticks)
                n_consumed1    = len(photons1_batch)
                n_hist1        = int(batch_hist1.sum())
                accumulator[:] += batch_hist1

                # ── Histogram photon2 (two-channel) ───────────────────────────
                n_consumed2 = n_hist2 = 0
                if two_channel and accumulator2 is not None:
                    ph2_lo = np.searchsorted(
                        all_photons2, gate_rise_batch[0], side='left')
                    ph2_hi = np.searchsorted(
                        all_photons2, last_cycle_end, side='right')
                    photons2_batch = all_photons2[ph2_lo:ph2_hi]

                    batch_hist2     = histogram_batch(
                        photons2_batch, gate_rise_batch,
                        num_gates_per_cycle, n_bins, gate_ticks)
                    n_consumed2     = len(photons2_batch)
                    n_hist2         = int(batch_hist2.sum())
                    accumulator2[:] += batch_hist2

                # Update count references read by the rate readers and poll loop.
                with photon_count_lock:
                    photon_count_ref[0]       += n_consumed1
                    gated_photon_count_ref[0] += n_hist1
                    if two_channel:
                        photon2_count_ref[0]       += n_consumed2
                        gated_photon2_count_ref[0] += n_hist2

                with diag_lock:
                    diag_proc_photons_ref[0]  += n_consumed1
                    diag_hist_photons_ref[0]  += n_hist1
                    diag_proc_cycles_ref[0]   += n_complete
                    diag_hist_cycles_ref[0]   += n_complete
                    if two_channel:
                        diag_proc_photons2_ref[0] += n_consumed2
                        diag_hist_photons2_ref[0] += n_hist2

                # Carry forward data past last_cycle_end to the next iteration.
                leftover_gates   = all_gates[n_gates_batch:]
                split1           = np.searchsorted(
                    all_photons, last_cycle_end, side='right')
                leftover_photons = all_photons[split1:]

                if two_channel and accumulator2 is not None:
                    split2            = np.searchsorted(
                        all_photons2, last_cycle_end, side='right')
                    leftover_photons2 = all_photons2[split2:]

                with diag_lock:
                    diag_leftover_ph_ref[0]  = len(leftover_photons)
                    diag_leftover_ph2_ref[0] = (len(leftover_photons2)
                                                if two_channel else 0)
                    diag_leftover_gt_ref[0]  = len(leftover_gates)

        return threading.Thread(target=_run, daemon=True, name='processor')

    def _make_diag_thread(self):
        """
        Factory for the periodic pipeline diagnostics thread.

        Prints a formatted table every diag_interval_s seconds showing:
          - Reader rates and hardware FIFO depths
          - Software buffer depths
          - Processor throughput and gate efficiency
          - Leftover (carry-forward) sample counts

        Only runs when diag_enabled is True.
        """
        interval    = self._diag_interval_s
        diag_enabled = self._diag_enabled
        stop_event   = self._diag_stop
        diag_lock    = self._diag_lock
        photon_lock  = self._photon_lock
        photon2_lock = self._photon2_lock
        gate_lock    = self._gate_lock
        photon_list  = self._photon_list
        photon2_list = self._photon2_list
        gate_list    = self._gate_list
        two_channel  = self._two_channel_fc

        photon_task_ref  = lambda: self._photon_task
        photon2_task_ref = lambda: self._photon2_task
        gate_task_ref    = lambda: self._gate_task

        rph_ref  = self._diag_reader_photons_ref
        rph2_ref = self._diag_reader_photons2_ref
        rgt_ref  = self._diag_reader_gates_ref
        pph_ref  = self._diag_proc_photons_ref
        pph2_ref = self._diag_proc_photons2_ref
        hph_ref  = self._diag_hist_photons_ref
        hph2_ref = self._diag_hist_photons2_ref
        pcy_ref  = self._diag_proc_cycles_ref
        hcy_ref  = self._diag_hist_cycles_ref
        lph_ref  = self._diag_leftover_photons_ref
        lph2_ref = self._diag_leftover_photons2_ref
        lgt_ref  = self._diag_leftover_gates_ref
        snap     = self._diag_snap

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
                    sw_ph  = sum(len(a) for a in photon_list)
                with photon2_lock:
                    sw_ph2 = sum(len(a) for a in photon2_list)
                with gate_lock:
                    sw_gt  = sum(len(a) for a in gate_list)

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
                    'proc_cycles':    cur_pcy, 'hist_cycles':      cur_hcy,
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
                print(f'|  READER          cum          rate/s     HW FIFO  |')
                print(f'|  photon1  {cur_rph:>12,d}  {d_rph/dt:>10.0f}  {hw_ph:>9d}  |')
                if two_channel:
                    print(f'|  photon2  {cur_rph2:>12,d}  {d_rph2/dt:>10.0f}  {hw_ph2:>9d}  |')
                print(f'|  gate     {cur_rgt:>12,d}  {d_rgt/dt:>10.0f}  {hw_gt:>9d}  |')
                print(f'+{sep}+')
                print(f'|  SW BUFFERS      samples                           |')
                print(f'|  photon1  {sw_ph:>12,d}' + ' ' * (W - 20) + '|')
                if two_channel:
                    print(f'|  photon2  {sw_ph2:>12,d}' + ' ' * (W - 20) + '|')
                print(f'|  gate     {sw_gt:>12,d}' + ' ' * (W - 20) + '|')
                print(f'+{sep}+')
                print(f'|  PROCESSOR       cum          rate/s     gate eff  |')
                print(f'|  ph1 cons {cur_pph:>12,d}  {d_pph/dt:>10.0f}' + ' ' * 12 + '|')
                print(f'|  ph1 hist {cur_hph:>12,d}  {d_hph/dt:>10.0f}  {ge1:>7.1f}%  |')
                if two_channel:
                    print(f'|  ph2 cons {cur_pph2:>12,d}  {d_pph2/dt:>10.0f}' + ' ' * 12 + '|')
                    print(f'|  ph2 hist {cur_hph2:>12,d}  {d_hph2/dt:>10.0f}  {ge2:>7.1f}%  |')
                print(f'|  cycles   {cur_pcy:>12,d}  {d_pcy/dt:>10.1f}  {ce:>7.1f}%  |')
                print(f'+{sep}+')
                print(f'|  LEFTOVERS  ph1={left_ph:,}  '
                      + (f'ph2={left_ph2:,}  ' if two_channel else '')
                      + f'gate={left_gt:,}' + ' ' * 8 + '|')
                print(f'+{sep}+', flush=True)

        return threading.Thread(target=_run, daemon=True, name='diag')

    @staticmethod
    def _histogram_batch(photons_sorted, gate_rise_all,
                         num_gates, n_bins, gate_ticks):
        """
        Vectorised histogram kernel — the innermost computation of the fast counter.

        Maps each photon timestamp to a (gate_within_cycle, time_bin) index
        and accumulates counts using numpy.bincount.

        Parameters
        ----------
        photons_sorted : uint64 ndarray
            Absolute photon timestamps (100 MHz ticks), SORTED ascending.
            Pre-sliced by the processor to the range
            [gate_rise_all[0], last_cycle_end].
        gate_rise_all  : uint64 ndarray
            Gate-open timestamps (100 MHz ticks), sorted ascending.
            Length must be an exact multiple of num_gates.
        num_gates      : int   Number of gate windows per excitation cycle.
        n_bins         : int   Number of histogram bins (= gate_ticks).
        gate_ticks     : int   Gate window duration in 100 MHz ticks.

        Returns
        -------
        hist : uint64 ndarray, shape (num_gates, n_bins)
            Photon counts per (gate_in_cycle, time_bin_within_gate).
        """
        gate_ticks_u64 = np.uint64(gate_ticks)
        hist = np.zeros((num_gates, n_bins), dtype=np.uint64)
        if len(photons_sorted) == 0:
            return hist

        gate_ends_all = gate_rise_all + gate_ticks_u64

        # For each photon, find the most recent gate that opened before it.
        # searchsorted(..., 'right') - 1 gives the last gate_rise <= photon_tick.
        # A result of -1 means the photon arrived before the first gate.
        gate_idx = (np.searchsorted(gate_rise_all, photons_sorted, side='right')
                    .astype(np.int64) - 1)

        # Keep only photons that arrived AFTER a gate opened.
        valid    = gate_idx >= 0
        gate_idx = gate_idx[valid]
        ph       = photons_sorted[valid]

        # Keep only photons that arrived BEFORE their gate window closed.
        in_win   = ph < gate_ends_all[gate_idx]
        gate_idx = gate_idx[in_win]
        ph       = ph[in_win]
        if len(ph) == 0:
            return hist

        # Map to (row=gate_in_cycle, col=tick_offset_within_gate).
        offset        = (ph - gate_rise_all[gate_idx]).astype(np.int64)
        gate_in_cycle = gate_idx % num_gates
        flat_idx      = gate_in_cycle * n_bins + offset

        # Accumulate using bincount (single pass, no Python loop).
        counts = np.bincount(flat_idx, minlength=num_gates * n_bins)
        hist  += counts.reshape(num_gates, n_bins).astype(np.uint64)
        return hist

    # =========================================================================
    #  Scanning counter interface
    #  Consumed by PIE710CounterInterfuse.
    #  Three public methods: arm(), read(), stop()
    #  Two public properties: channel_names, channel_units
    # =========================================================================

    @property
    def channel_names(self) -> List[str]:
        """
        Scanning channel names exposed to PIE710CounterInterfuse.

        Single-channel mode : ['APD1']
        Two-channel mode    : ['APD1', 'APD2']

        These names appear as channel options in the Qudi confocal scan GUI.
        """
        names = [self._scan_ch_name]
        if self._two_channel_scan:
            names.append(self._scan_ch_name2)
        return names

    @property
    def channel_units(self) -> Dict[str, str]:
        """
        Physical unit for each scanning channel.
        The interfuse divides raw counts by t_pixel to convert to counts/s.
        """
        units = {self._scan_ch_name: 'c/s'}
        if self._two_channel_scan:
            units[self._scan_ch_name2] = 'c/s'
        return units

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Stop the instreamer (saving its state) and create CO + CI scan tasks.

        MUST be called BEFORE the PI E-710 scan command is sent.
        The tasks idle silently until the PI gate RISING edge arrives.

        Single-channel task layout:
          CO  scan_clock_counter (ctr1)  5 kHz finite pulse train
              Triggered by PI gate RISING edge on scan_trigger_terminal.
          CI  scan_counter_channel (ctr0)  APD1 photon edge counting
              Clocked by CO internal output.

        Two-channel additional task:
          CI  scan_counter_channel_2 (ctr2)  APD2 photon edge counting
              Clocked by the SAME CO output — perfect pixel alignment.

        Why n_pixels * n_steps + 1 samples?
          raw[0]       = baseline count at the instant the gate went HIGH
          np.diff(raw) = n_pixels * n_steps per-step increments
                         with the pre-gate background automatically removed

        Priority: raises RuntimeError if the fast counter is running.
        Stale scan tasks from a previous crashed scan are cleaned up first.

        @param n_pixels : pixels per sweep (1D: n_x, 2D: one fast-axis line)
        @param t_pixel  : dwell time per pixel in seconds (= 1 / scan_frequency)
        """
        with self._scan_lock:
            # Remove stale tasks from any previous crashed scan.
            if self._scan_task is not None or self._scan_co_task is not None:
                self.log.warning('arm(): stale scan tasks found — cleaning up.')
                self._scan_cleanup_unsafe(restart_stream=False)

            # Fast counter has absolute priority.
            if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                raise RuntimeError(
                    f'Cannot arm scanner while fast counter is active '
                    f'(status={self._status}).  '
                    f'Call stop_measure() or pause_measure() first.')

            # Stop instreamer to free counter resources.
            # The running state is saved so start_stream is called again after.
            self._scan_was_streaming = self._ni_tasks_running
            self._ni_stop_tasks()

            n         = max(1, round(t_pixel * _PI_SAMP_RATE))
            n_collect = n * n_pixels + 1   # +1 for baseline removal via diff

            self._scan_n_steps  = n
            self._scan_n_pixels = n_pixels

            self.log.debug(
                f'arm  n_pixels={n_pixels}  '
                f't_pixel={t_pixel * 1e3:.3f} ms  '
                f'steps/pixel={n}  n_collect={n_collect}  '
                f'two_channel_scan={self._two_channel_scan}'
            )

            try:
                self._scan_create_tasks(n_collect)
            except ni.DaqError as exc:
                self._scan_cleanup_unsafe(restart_stream=True)
                raise RuntimeError(
                    f'NIXSeriesCounter.arm() failed: {exc}') from exc

    def read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Wait for the CO scan clock to finish, read all CI buffers, and
        return per-pixel photon counts for all active scanning channels.

        This call BLOCKS until all n_pixels * n_steps + 1 CO pulses have
        been generated (i.e. the scan region has been fully traversed).

        Data processing (per channel):
            raw[k]  = cumulative photon count at the end of CO clock tick k
            raw[0]  = baseline count at gate HIGH (before first pixel)
            diff(raw) = per-step increments with baseline subtracted
            reshape(n_pixels, n_steps).sum(axis=1) = total counts per pixel

        Scan tasks are ALWAYS cleaned up in the finally block regardless of
        success or failure.  The instreamer is restarted if it was running
        before arm() was called.

        @param n_pixels : must match the value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} raw counts,
                          or None on failure.
        """
        with self._scan_lock:
            if (self._scan_task   is None or
                    self._scan_co_task is None or
                    self._scan_reader  is None):
                self.log.error('read() called but no scan tasks are active.')
                return None
            n         = self._scan_n_steps
            n_collect = n * n_pixels + 1

        result = {}
        try:
            # Block until the CO has generated all n_collect clock pulses.
            self._scan_co_task.wait_until_done(timeout=self._scan_rw_timeout)
            # CI tasks are clocked by CO and finish at the same time.
            self._scan_task.wait_until_done(timeout=10.0)

            # Read APD1 cumulative buffer.
            raw1 = np.zeros(n_collect, dtype=np.float64)
            self._scan_reader.read_many_sample_double(
                raw1, number_of_samples_per_channel=n_collect, timeout=10.0)
            counts1 = np.diff(raw1).reshape(n_pixels, n).sum(axis=1)
            result[self._scan_ch_name] = counts1

            # Read APD2 cumulative buffer (two-channel only).
            if (self._two_channel_scan
                    and self._scan_task2   is not None
                    and self._scan_reader2 is not None):
                self._scan_task2.wait_until_done(timeout=10.0)
                raw2 = np.zeros(n_collect, dtype=np.float64)
                self._scan_reader2.read_many_sample_double(
                    raw2, number_of_samples_per_channel=n_collect, timeout=10.0)
                counts2 = np.diff(raw2).reshape(n_pixels, n).sum(axis=1)
                result[self._scan_ch_name2] = counts2

            self.log.debug(
                f'read OK  n_pixels={n_pixels}  steps/px={n}  ' +
                '  '.join(f'{ch}=(total={int(v.sum())},mean={v.mean():.1f})'
                           for ch, v in result.items())
            )

        except ni.DaqError as exc:
            self.log.error(
                f'NIXSeriesCounter.read() failed: {exc}\n'
                f'  Check BNC: PI Trigger OUT -> NI {self._scan_trigger_term}\n'
                f'  Gate must go HIGH for the full scan region duration.')
            return None
        finally:
            # Always clean up scan tasks and optionally restart instreamer.
            # _scan_lock is an RLock so re-entry from the same thread is safe.
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
        Raises ni.DaqError on failure — handled by arm().

        Start order (critical for correct operation):
          1. CI task 1 — arms and waits for CO clock edges
          2. CI task 2 — same (two-channel only)
          3. CO task   — waits for gate RISING edge from PI E-710

        All CI tasks share the same CO internal output as their sample clock,
        guaranteeing that photon1 and photon2 pixel timing is identical.
        """
        dev       = self._device_name
        apd1_term = self._scan_apd_term or self._photon_pfi_line
        clock_num = ''.join(filter(str.isdigit, self._scan_clock_ctr))
        co_output = f'/{dev}/Ctr{clock_num}InternalOutput'

        # CO task: finite 5 kHz pulse train, started by PI gate edge.
        # CO tasks support start triggers on ALL NI X-Series devices.
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
        self._scan_co_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source = f'/{dev}/{self._scan_trigger_term}',
            trigger_edge   = ni.constants.Edge.RISING,
        )

        # CI task 1: APD1 edge counting, clocked by CO output.
        # Internal routing (CtrNInternalOutput) works on all NI devices.
        self._scan_task = ni.Task('APDScanCounter1')
        self._scan_task.ci_channels.add_ci_count_edges_chan(
            f'/{dev}/{self._scan_counter_ch}', edge=ni.constants.Edge.RISING)
        self._scan_task.ci_channels.all.ci_count_edges_term = f'/{dev}/{apd1_term}'
        self._scan_task.timing.cfg_samp_clk_timing(
            rate           = _PI_SAMP_RATE,
            source         = co_output,
            active_edge    = ni.constants.Edge.RISING,
            sample_mode    = ni.constants.AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )
        self._scan_reader = CounterReader(self._scan_task.in_stream)
        self._scan_reader.verify_array_shape = False

        # CI task 2: APD2 edge counting, clocked by the SAME CO output.
        # Sharing the CO clock ensures photon1 and photon2 are pixel-aligned.
        if self._two_channel_scan and self._scan_apd_term2_resolved:
            self._scan_task2 = ni.Task('APDScanCounter2')
            self._scan_task2.ci_channels.add_ci_count_edges_chan(
                f'/{dev}/{self._scan_counter_ch2}', edge=ni.constants.Edge.RISING)
            self._scan_task2.ci_channels.all.ci_count_edges_term = (
                f'/{dev}/{self._scan_apd_term2_resolved}')
            self._scan_task2.timing.cfg_samp_clk_timing(
                rate           = _PI_SAMP_RATE,
                source         = co_output,   # same CO output as CI task 1
                active_edge    = ni.constants.Edge.RISING,
                sample_mode    = ni.constants.AcquisitionType.FINITE,
                samps_per_chan = n_collect,
            )
            self._scan_reader2 = CounterReader(self._scan_task2.in_stream)
            self._scan_reader2.verify_array_shape = False

        # Set _scan_active BEFORE starting tasks so that _ni_start_tasks(),
        # if called concurrently from another thread, sees the flag and
        # excludes our counters from the instreamer free pool.
        self._scan_active = True

        # Start order: CI tasks first (ready to receive clock), then CO (arm).
        self._scan_task.start()
        if self._scan_task2 is not None:
            self._scan_task2.start()
        self._scan_co_task.start()

        self.log.debug(
            f'Scan tasks armed -- CO({self._scan_clock_ctr})->'
            f'CI1({self._scan_counter_ch})'
            + (f',CI2({self._scan_counter_ch2})' if self._two_channel_scan else '')
        )

    def _scan_cleanup_unsafe(self, restart_stream: bool = True) -> None:
        """
        Stop and close all scan tasks, then optionally restart the instreamer.

        Caller must hold _scan_lock (_scan_lock is an RLock so re-entry is safe).

        Key design detail — _scan_active ordering
        ------------------------------------------
        _scan_active is set to False BEFORE closing the tasks.  This ensures
        that any concurrent _ni_start_tasks() call (which reads _scan_active
        as a plain bool without acquiring _scan_lock) immediately sees the
        counters as free.  The tasks are then closed while _ni_start_tasks()
        is either not running or is waiting on _ni_tasks_lock, guaranteeing
        no counter double-allocation.
        """
        self._scan_active  = False   # signal counter availability immediately
        self._scan_reader  = None
        self._scan_reader2 = None

        # Close in reverse start order: CO first (it armed everything), then CIs.
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
            # Only restart instreamer if the fast counter is not using the counters.
            if self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                # _ni_start_tasks acquires _ni_tasks_lock (not _scan_lock) and
                # reads _scan_active as a plain bool — no deadlock possible.
                self._ni_start_tasks()
        else:
            self._scan_was_streaming = False