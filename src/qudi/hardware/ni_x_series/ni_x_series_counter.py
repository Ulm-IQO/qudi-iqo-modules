# -*- coding: utf-8 -*-
"""

This file contains the qudi hardware module to use a National Instruments X-series card as fastcounter
(time-resolved gated photon counting) and data instreamer (mixed analog/digital streaming for the
time-series display). Tested with NI-6323, NI-6343. and NI-6363.
100 MHz resolution (= 10 ns binwidth) for photon streams up to 10 MHz.

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

=====================================================================================================
NI USB-63xx — Combined FastCounterInterface + DataInStreamInterface
=====================================================================================================

Required hardware connections (fast counter)
-------------------------------------
  PFI?  ←  photon detector output  (each rising edge = one detected photon), can be chosen arbitrarily
  PFI?  ←  gate / excitation pulse  (each rising edge opens one counting window), can be chosen arbitrarily

Counter budget
--------------
The NI USB-63xx provides exactly 4 counters (ctr0–ctr3).

  Fast counter (running)   : ctr0, ctr1, ctr2  (ctr2 freed early after anchor)
  Instreamer clock         : ctr3  (when fast counter is NOT running)
  Instreamer digital chans : one counter each from the free pool
                             (up to 3 digital channels when FC is idle,
                              0 digital channels when FC is running)

  Priority rule: the fast counter always wins.
    start_measure()        → tears down all nidaqmx instreamer tasks first
    stop_measure()         → rebuilds instreamer tasks (if stream was active)
    pause_measure()        → rebuilds instreamer tasks (if stream was active)
    continue_measure()     → tears down instreamer tasks again

Fast counter — counter roles
------------------------------
  ctr0  period-measurement  — measures inter-photon intervals in 100 MHz ticks
  ctr1  edge-timestamp      — absolute 100 MHz tick timestamp of every gate edge
  ctr2  edge-timestamp      — absolute tick of the very first photon (t1_abs),
                              anchoring the ctr0 cumsum in the same time domain
                              as the ctr1 gate timestamps.  Freed after the
                              first sample is read.

Why ctr2?
---------
  ctr0 (period mode) returns inter-photon intervals: [t2-t1, t3-t2, ...].
  Cumsum gives [t2, t3, ...] but t1 itself is unknown without an external
  reference.  ctr2 is a plain edge-timestamp counter on the same photon PFI,
  armed by the same trigger as ctr0 and ctr1.  Its first latch value is the
  absolute 100 MHz tick of the first photon (t1_abs).  The photon reader seeds
  its cumsum:

      photon_timestamps = t1_abs + [0, cumsum(intervals_from_ctr0)]
                        = [t1, t2, t3, ...]

  making every photon timestamp directly comparable to the gate timestamps.

Threading model
---------------
  anchor_thread  ──► reads ONE sample from ctr2 → t1_abs; frees ctr2
  photon_thread  ──► blocks on t1_abs, then cumsums ctr0 intervals
  gate_thread    ──► rollover-corrects ctr1 edge timestamps (wraps every ~43 s)

  photon_list ──┐
                ├──► processor_thread ──► accumulator, count-rate counters
  gate_list   ──┘

  diag_thread  ──► prints pipeline diagnostics every diag_interval_s seconds

  poll_thread  ──► samples at sample_rate Hz → ring buffer
                   reads fast-counter rates + nidaqmx digital/analog channels

State machine (FastCounterInterface)
-------------------------------------
  [not activated]   ──on_activate()──►  unconfigured (0)
  unconfigured (0)  ──configure()──►    idle (1)
  idle (1)          ──start_measure()──► running (2)
  running (2)       ──pause_measure()──► paused (3)
  paused (3)        ──continue_measure()──► running (2)
  running (2)       ──stop_measure()──► idle (1)
  any               ──on_deactivate()──► [not activated]
  any               ──error──► error (-1)

DataInStreamInterface — unified channel layout
------------------------------------------------
  Index 0 : rate_all_hz    — photons/s within all processed windows
  Index 1 : rate_gated_hz  — photons/s normalised to gate-open time only
  Index 2+: digital PFI channels  (counts/s, 0.0 when fast counter running)
  Last N  : analog ai channels    (V,         0.0 when fast counter running)

Qudi configuration example
---------------------------
hardware:
  fast_counter_ni:
    module.Class: 'ni_x_series.ni_x_series_counter.NIXSeriesCounter'
    options:
      device_name: 'Dev2'                   # name of the device in NI MAX
      photon_pfi: 'PFI0'                    # Photon input channel
      gate_pfi: 'PFI1'                      # Gate trigger input channel
      diag_enabled: true                    # prints READER / SW BUFFER / PROCESSOR / LEFTOVERS pipeline stats every N seconds
      diag_interval_s: 2.0                  # interval at which the diagnostics are printed
      sample_rate: 10.0                     # Hz — instreamer poll rate (1-100)
      channel_buffer_size: 100              # instreamer ring-buffer depth (samples)
      digital_sources:                      # optional — PFI terminals for counting
        - 'PFI0'
      analog_sources:                       # optional — analog input channels
        - 'ai0'
      adc_voltage_range: [-10, 10]          # optional
      max_channel_samples_buffer: 1048576   # optional
      read_write_timeout: 10                # optional, seconds
"""

import collections
import ctypes
import os
import threading
import time
from typing import List, Optional, Sequence, Tuple, Union
from functools import wraps

import numpy as np
import nidaqmx as ni
from nidaqmx._lib import lib_importer
from nidaqmx.stream_readers import CounterReader
from nidaqmx.stream_readers import AnalogMultiChannelReader as _AnalogMultiChannelReader
from nidaqmx.constants import FillMode, READ_ALL_AVAILABLE
try:
    from nidaqmx._task_modules.read_functions import _read_analog_f_64  # type: ignore[import]
except ImportError:
    _read_analog_f_64 = None  # type: ignore[assignment]

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
#  Used only by the ctypes fast-counter path; the nidaqmx Python library
#  handles these symbolically for the instreamer tasks.
# ══════════════════════════════════════════════════════════════════════════════
DAQmx_Val_Rising      = 10280   # active / sample on rising edge
DAQmx_Val_CountUp     = 10128   # counter counts upward
DAQmx_Val_ContSamps   = 10123   # continuous (not finite) acquisition
DAQmx_Val_DigEdge     = 10150   # digital-edge trigger type
DAQmx_Val_Ticks       = 10304   # measurement unit: timebase ticks
DAQmx_Val_LowFreq1Ctr = 10105   # period-measurement method for low frequencies

# Hardware timebase of the NI USB-63xx.
_TIMEBASE_HZ = 100e6            # 100 MHz internal timebase
_TICK_NS     = 1e9 / _TIMEBASE_HZ   # one tick = 10 ns

# Extra photon slack added to the last gate-close time before the processor
# commits a batch.  Gives the photon reader time to deliver any photons that
# arrived just before the gate closed but have not been queued yet.
PHOTON_SLACK_TICKS = np.uint64(10_000)   # 100 us

# Upper bounds used when sizing DAQmx ring buffers and read-chunk arrays.
_MAX_PHOTON_RATE_HZ = 10_000_000   # 10 MHz
_MAX_GATE_RATE_HZ   = 10_000_000   # 10 MHz

# Names of the two fixed fast-counter rate channels in the unified channel set.
_CH_ALL      = 'rate_all_hz'    # all photons within processed windows
_CH_GATED    = 'rate_gated_hz'  # photons normalised to gate-open time
_FC_CHANNELS = (_CH_ALL, _CH_GATED)

# Sample-rate bounds for the instreamer poll thread.
_SAMPLE_RATE_MIN =   1.0   # Hz
_SAMPLE_RATE_MAX = 100.0   # Hz
_SAMPLE_RATE_DEF =  10.0   # Hz (default if not set in config)

# Counter assignments on the NI USB-63xx.
# ctr0-ctr2 are exclusively used by the fast counter when it is running.
# ctr3 is used as the instreamer sample-clock when the fast counter is idle.
_FC_COUNTERS      = ('ctr0', 'ctr1', 'ctr2')
_INSTREAM_CLK_CTR = 'ctr3'


# ══════════════════════════════════════════════════════════════════════════════
#  Patched AnalogMultiChannelReader
#  The nidaqmx library changed its internal interpreter API between versions.
#  This subclass tries the newer interpreter path first and falls back to the
#  older _read_analog_f_64 C-function wrapper if the attribute is absent,
#  keeping compatibility across nidaqmx versions.
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
            # Newer nidaqmx versions expose an interpreter object.
            _, samps_per_chan_read = self._interpreter.read_analog_f64(
                self._handle,
                number_of_samples_per_channel,
                timeout,
                FillMode.GROUP_BY_SCAN_NUMBER.value,
                data,
            )
        except AttributeError:
            # Older nidaqmx versions use the direct C-function wrapper.
            samps_per_chan_read = _read_analog_f_64(
                self._handle, data,
                number_of_samples_per_channel, timeout,
                fill_mode=FillMode.GROUP_BY_SCAN_NUMBER,
            )
        return samps_per_chan_read


# ══════════════════════════════════════════════════════════════════════════════
#  NiUsb63xx
# ══════════════════════════════════════════════════════════════════════════════
class NIXSeriesCounter(FastCounterInterface, DataInStreamInterface):
    """
    Combined Qudi hardware module for the NI USB-63xx.

    Implements FastCounterInterface (time-resolved gated photon counting) and
    DataInStreamInterface (mixed analog/digital streaming).

    The fast counter uses the ctypes DAQmx API directly (ctr0/ctr1/ctr2).
    The instreamer uses the nidaqmx Python library (ctr3 clock + per-channel
    counter tasks for digital channels, one AI task for analog channels).

    The fast counter has absolute priority over counter resources.  Calling
    start_measure() tears down all nidaqmx instreamer tasks first.  Calling
    stop_measure() or pause_measure() automatically rebuilds and restarts
    them if the stream is active.

    See module docstring for full channel layout and configuration.

    Example configuration for copy paste:

    ni_fastcounter:
    module.Class: 'ni_x_series.ni_x_series_counter.NIXSeriesCounter'
        options:
            device_name: 'Dev1'
            photon_pfi: 'PFI8'
            gate_pfi: 'PFI10'
            diag_enabled: false
            diag_interval_s: 2.0
            sample_rate: 10.0
            channel_buffer_size: 10000
            digital_sources:
            - 'PFI8'
            # analog_sources:
            # - 'ai0'
            adc_voltage_range: [-10, 10]
            read_write_timeout: 10

    """

    # ── ConfigOptions ─────────────────────────────────────────────────────────
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

    # ── FastCounterInterface status codes ─────────────────────────────────────
    STATUS_UNCONFIGURED = 0
    STATUS_IDLE         = 1
    STATUS_RUNNING      = 2
    STATUS_PAUSED       = 3
    STATUS_ERROR        = -1

    # ══════════════════════════════════════════════════════════════════════════
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Hardware terminal byte-strings — built from ConfigOptions in on_activate().
        self._device        = None   # e.g. b"Dev2"
        self._photon_pfi    = None   # e.g. b"/Dev2/PFI0"
        self._gate_pfi      = None   # e.g. b"/Dev2/PFI1"
        self._timebase_term = None   # e.g. b"/Dev2/100MHzTimebase"

        self._max_photon_rate = float(_MAX_PHOTON_RATE_HZ)
        self._max_gate_rate   = float(_MAX_GATE_RATE_HZ)

        # ── Fast-counter timing parameters (set by _fc_configure()) ──────────
        self._gate_width_s        = None   # gate window duration in seconds
        self._num_gates_per_cycle = None   # gates per excitation cycle
        self._gate_ticks          = None   # gate_width_s expressed in 100 MHz ticks
        self._n_bins              = None   # histogram bins per gate (= gate_ticks)

        # DAQmx ring-buffer depths and read-chunk sizes (set by _fc_configure()).
        self._photon_buffer = None   # ring-buffer depth for ctr0
        self._gate_buffer   = None   # ring-buffer depth for ctr1
        self._photon_chunk  = None   # max samples per read call (ctr0)
        self._gate_chunk    = None   # max samples per read call (ctr1)

        self._status = self.STATUS_UNCONFIGURED

        # ctypes DAQmx task handles (None when not running).
        self._photon_task = None   # ctr0 — period measurement
        self._gate_task   = None   # ctr1 — gate edge timestamps
        self._anchor_task = None   # ctr2 — first-photon anchor (freed early)

        # Software queues between reader threads and the processor thread.
        # Each reader appends uint64 numpy arrays; the processor swaps them out.
        self._photon_list = []
        self._gate_list   = []
        self._photon_lock = threading.Lock()
        self._gate_lock   = threading.Lock()

        # 2-D histogram accumulator — shape (num_gates_per_cycle, n_bins).
        # Preserved across pause/continue so data accumulates.
        self._accumulator    = None
        self._t_start_ref    = [0.0]   # wall-clock time of the last DAQmxStartTask
        self._elapsed_time_s = 0.0     # total acquisition time across all segments

        # Cumulative photon counters read by the rate readers.
        self._photon_count_ref       = [0]   # all photons inside processed windows
        self._gated_photon_count_ref = [0]   # photons that landed inside a gate
        self._photon_count_lock      = threading.Lock()

        # Default rate reader used by get_count_rates().
        self._default_rate_reader = None

        # Diagnostics counters (reader/proc/hist/leftover) written by worker threads.
        self._diag_lock                 = threading.Lock()
        self._diag_reader_photons_ref   = [0]
        self._diag_reader_gates_ref     = [0]
        self._diag_proc_photons_ref     = [0]
        self._diag_hist_photons_ref     = [0]
        self._diag_proc_cycles_ref      = [0]
        self._diag_hist_cycles_ref      = [0]
        self._diag_leftover_photons_ref = [0]
        self._diag_leftover_gates_ref   = [0]
        self._diag_snap = {
            'time': 0.0, 'reader_photons': 0, 'reader_gates': 0,
            'proc_photons': 0, 'hist_photons': 0,
            'proc_cycles': 0, 'hist_cycles': 0,
        }

        # Worker thread handles (None between runs).
        self._photon_thread    = None
        self._gate_thread      = None
        self._anchor_thread    = None
        self._processor_thread = None
        self._diag_thread      = None

        # Stop events: set to ask a thread to exit its loop cleanly.
        self._photon_stop     = None
        self._gate_stop       = None
        self._anchor_stop     = None
        self._processor_stop  = None
        self._diag_stop       = None
        # Overflow events: set by a reader thread on a fatal hardware error.
        self._photon_overflow = None
        self._gate_overflow   = None
        self._anchor_overflow = None

        # Anchor sync: the anchor thread sets _t1_abs_ready after writing t1_abs_ref[0].
        # The photon reader blocks on this event before emitting any timestamps.
        self._t1_abs_ref   = [np.uint64(0)]
        self._t1_abs_ready = threading.Event()

        # Handle to the NI-DAQmx C library loaded by ctypes.
        self._nidaq = None

        # ── Instreamer (nidaqmx) state ─────────────────────────────────────────
        # Resolved and validated source lists — populated in on_activate.
        self._digital_sources = []   # e.g. ['pfi2', 'pfi3']
        self._analog_sources  = []   # e.g. ['ai0', 'ai1']

        # Ordered unified channel list: [rate_all_hz, rate_gated_hz, <digital...>, <analog...>]
        self._all_channels = list(_FC_CHANNELS)

        # nidaqmx task and reader handles (None / empty when tasks are stopped).
        self._ni_clk_task    = None   # CO pulse task on ctr3 — sample clock
        self._ni_di_tasks    = []     # CI period task, one per digital channel
        self._ni_di_readers  = []     # CounterReader, one per digital task
        self._ni_ai_task     = None   # AI voltage task (all analog channels)
        self._ni_ai_reader   = None   # _PatchedAnalogReader for the AI task
        self._ni_tasks_lock  = threading.Lock()   # guards _ni_start/stop_tasks

        # True while nidaqmx instreamer tasks are started and running.
        self._ni_tasks_running = False

        # DataInStreamInterface parameters
        self._instream_constraints = None
        self._sample_rate          = _SAMPLE_RATE_DEF
        self._channel_buffer_size  = 100
        self._active_channels      = list(_FC_CHANNELS)
        self._streaming_mode       = StreamingMode.CONTINUOUS

        # Ring buffer filled by the poll thread; consumed by the read methods.
        # maxlen is set to channel_buffer_size in start_stream().
        self._ring_buffer = collections.deque()
        self._ring_lock   = threading.Lock()

        # Poll thread state.
        self._poll_thread      = None
        self._poll_stop        = threading.Event()
        self._stream_lock      = threading.Lock()
        self._streaming        = False
        self._poll_rate_reader = None

    # ══════════════════════════════════════════════════════════════════════════
    #  Qudi lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def on_activate(self):
        """
        Load the ctypes DAQmx library, validate config options, reset the
        device, and build DataInStreamInterface constraints.

        After returning the module is in STATUS_UNCONFIGURED and ready for a
        configure() call.
        """
        device_name = self._device_name

        # Build byte-string terminal names required by the ctypes DAQmx API.
        self._device        = device_name.encode()
        self._photon_pfi    = f'/{device_name}/{self._photon_pfi_line}'.encode()
        self._gate_pfi      = f'/{device_name}/{self._gate_pfi_line}'.encode()
        self._timebase_term = f'/{device_name}/100MHzTimebase'.encode()

        # Load the ctypes DAQmx library and reset the device to a clean state.
        self._nidaq = self._load_nidaq()
        self._declare_argtypes()
        try:
            self._check(self._nidaq.DAQmxResetDevice(self._device))
        except RuntimeError as e:
            self._nidaq = None
            raise RuntimeError(
                f"on_activate: failed to reset device '{device_name}'. "
                f"Check USB connection and NI-DAQmx driver installation.\n"
                f"Original error: {e}"
            ) from e

        # Use the nidaqmx Python library to enumerate available terminals and
        # validate the digital/analog source lists from config.
        # Use the nidaqmx device object to enumerate valid terminals and
        # validate the digital/analog source lists from config.
        ni_device = ni.system.Device(device_name)

        all_di_terms = tuple(
            t.rsplit('/', 1)[-1].lower()
            for t in ni_device.terminals if 'PFI' in t
        )
        all_ai_terms = tuple(
            t.rsplit('/', 1)[-1].lower()
            for t in ni_device.ai_physical_chans.channel_names
        )

        def _normalise(sources, valid_set, kind):
            """Strip device prefix, lower-case, and drop any invalid entries."""
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
            self.log.warning(
                'on_activate: >3 digital sources requested; only first 3 used '
                '(ctr0-ctr2 reserved for fast counter, ctr3 for clock).')
            self._digital_sources = self._digital_sources[:3]

        if len(self._analog_sources) > 16:
            self.log.warning(
                'on_activate: >16 analog sources requested; only first 16 used.')
            self._analog_sources = self._analog_sources[:16]

        # Build the unified channel list exposed by the DataInStreamInterface.
        self._all_channels = (list(_FC_CHANNELS)
                              + self._digital_sources
                              + self._analog_sources)

        # Build the constraints object.  Units: counts/s for all counter
        # channels (FC rates + digital), V for analog.
        channel_units = {ch: 'counts/s' for ch in _FC_CHANNELS}
        channel_units.update({ch: 'counts/s' for ch in self._digital_sources})
        channel_units.update({ch: 'V'         for ch in self._analog_sources})

        # Clamp sample-rate bounds to AI hardware limits when analog sources present.
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
                increment=1,
                enforce_int=True,
            ),
            sample_rate=ScalarConstraint(
                default=float(np.clip(self._cfg_sample_rate, sr_min, sr_max)),
                bounds=(sr_min, sr_max),
                increment=0.1,
                enforce_int=False,
            ),
        )

        self._sample_rate         = float(
            np.clip(self._cfg_sample_rate, sr_min, sr_max))
        self._channel_buffer_size = max(2, int(self._cfg_channel_buf_size))
        self._active_channels     = list(self._all_channels)
        self._streaming_mode      = StreamingMode.CONTINUOUS

        self._status = self.STATUS_UNCONFIGURED
        self._init_default_rate_reader()

    def on_deactivate(self):
        """
        Stop any running stream and measurement, tear down all tasks, reset
        the device and release the library.  Safe to call from any state.
        """
        if self._streaming:
            try:
                self.stop_stream()
            except Exception as e:
                self.log.warning(f'on_deactivate: warning during stream stop: {e}')

        if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED,
                            self.STATUS_ERROR):
            try:
                self._stop_hardware_and_threads()
            except Exception as e:
                self.log.warning(f'on_deactivate: warning during FC cleanup: {e}')

        self._ni_stop_tasks()

        if self._nidaq is not None:
            try:
                self._nidaq.DAQmxResetDevice(self._device)
            except Exception as e:
                self.log.warning(f'on_deactivate: warning during device reset: {e}')
        self._nidaq = None
        self._status = self.STATUS_UNCONFIGURED

    # ══════════════════════════════════════════════════════════════════════════
    #  FastCounterInterface — mandatory abstract methods
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
                  number_of_gates=0,
                  active_channels=None, streaming_mode=None,
                  channel_buffer_size=None, sample_rate=None):
        """
        Unified configure() — dispatches to the correct interface.

        FastCounterInterface call (positional or keyword):
            configure(bin_width_s, record_length_s, number_of_gates=0)
            → _fc_configure()

        DataInStreamInterface call (keyword-only, as Qudi's
        time_series_reader_logic always calls it):
            configure(active_channels=..., streaming_mode=...,
                      channel_buffer_size=..., sample_rate=...)
            → _is_configure()

        Dispatch rule: if bin_width_s is a number the call is for the fast
        counter; if active_channels is provided the call is for the instreamer.
        """
        if bin_width_s is not None and isinstance(bin_width_s, (int, float)):
            return self._fc_configure(bin_width_s, record_length_s,
                                      number_of_gates)
        if active_channels is not None:
            return self._is_configure(active_channels, streaming_mode,
                                      channel_buffer_size, sample_rate)
        raise TypeError(
            'configure() requires either (bin_width_s, record_length_s) '
            'for the fast counter or keyword arguments '
            '(active_channels, streaming_mode, channel_buffer_size, '
            'sample_rate) for the instreamer.'
        )

    def _fc_configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """
        FastCounterInterface configure() implementation.

        bin_width_s      : Histogram bin width in seconds (rounded to 10 ns).
        record_length_s  : Gate window duration in seconds (rounded up to tick).
        number_of_gates  : Gates per excitation cycle (>= 1).

        Returns (actual_bin_width_s, actual_record_length_s, num_gates).
        Resets the accumulator if the histogram shape changes.
        Transitions to STATUS_IDLE.
        """
        if self._status == self.STATUS_RUNNING:
            raise RuntimeError(
                'Cannot reconfigure while running. Call stop_measure() first.')

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

        if (self._accumulator is None
                or self._accumulator.shape != (num_gates, gate_ticks)):
            self._accumulator = np.zeros((num_gates, gate_ticks), dtype=np.uint64)

        self._reset_run_state()
        self._status = self.STATUS_IDLE
        return actual_bin_width_s, actual_record_length_s, num_gates

    def get_status(self):
        """
        Return the current state-machine status code.
        Polls overflow events so a hardware error is reflected immediately.
        """
        if self._status == self.STATUS_RUNNING:
            if ((self._photon_overflow and self._photon_overflow.is_set()) or
                    (self._gate_overflow   and self._gate_overflow.is_set())   or
                    (self._anchor_overflow and self._anchor_overflow.is_set())):
                self._status = self.STATUS_ERROR
        return self._status

    def start_measure(self):
        """
        Tear down instreamer nidaqmx tasks (freeing counters), then arm the
        fast counter.  Must be called from STATUS_IDLE.
        Transitions to STATUS_RUNNING.
        """
        if self._status != self.STATUS_IDLE:
            raise RuntimeError(
                f'start_measure() called in invalid state {self._status}. '
                'Call configure() first, or stop_measure() if currently running.')
        # Release instreamer counter resources before arming the fast counter.
        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def stop_measure(self):
        """
        Stop fast-counter hardware and threads, print a summary, reset all
        accumulators, and restart instreamer tasks (if stream is active).
        Safe to call from any active state.  Transitions to STATUS_IDLE.
        Call get_data_trace() before stop_measure() to preserve data.
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
        # Fast counter has released ctr0-ctr2; restart instreamer tasks
        # so the time-series display resumes immediately with real data.
        if self._streaming:
            self._ni_start_tasks()

    def pause_measure(self):
        """
        Stop fast-counter hardware without resetting the accumulator.
        Restarts instreamer tasks (if stream is active).
        Transitions to STATUS_PAUSED.
        """
        if self._status != self.STATUS_RUNNING:
            raise RuntimeError(
                f'pause_measure() called in invalid state {self._status}. '
                'Must be running.')
        self._stop_hardware_and_threads()
        if self._t_start_ref[0] > 0:
            self._elapsed_time_s += time.monotonic() - self._t_start_ref[0]
            self._t_start_ref[0] = 0.0
        self._status = self.STATUS_PAUSED
        # Fast counter has released ctr0-ctr2; restart instreamer tasks.
        if self._streaming:
            self._ni_start_tasks()

    def continue_measure(self):
        """
        Resume a paused acquisition.  Tears down instreamer tasks again,
        then re-arms the fast counter.  Accumulator is preserved.
        Transitions to STATUS_RUNNING.
        """
        if self._status != self.STATUS_PAUSED:
            raise RuntimeError(
                f'continue_measure() called in invalid state {self._status}. '
                'Must be paused.')
        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def is_gated(self):
        """Return True — this module always operates in gated mode."""
        return True

    def get_binwidth(self):
        """Return the histogram bin width in seconds (10 ns = one tick).
        Returns None if configure() has not been called."""
        if self._gate_ticks is None:
            return None
        return 1.0 / _TIMEBASE_HZ

    def get_data_trace(self):
        """
        Return the current accumulated histogram and metadata.

        Returns
        -------
        data      : int64 ndarray, shape (num_gates_per_cycle, n_bins)
        info_dict : {'elapsed_sweeps': int, 'elapsed_time': float}
        """
        if self._accumulator is None:
            return (np.zeros((1, 1), dtype=np.int64),
                    {'elapsed_sweeps': 0, 'elapsed_time': 0.0})
        elapsed = self._elapsed_time_s
        if self._status == self.STATUS_RUNNING and self._t_start_ref[0] > 0:
            elapsed += time.monotonic() - self._t_start_ref[0]
        return (self._accumulator.astype(np.int64).copy(),
                {'elapsed_sweeps': self._diag_hist_cycles_ref[0],
                 'elapsed_time':   elapsed})

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

    def _is_configure(self,
                      active_channels: Sequence[str],
                      streaming_mode: Union[StreamingMode, int],
                      channel_buffer_size: int,
                      sample_rate: float) -> None:
        """
        DataInStreamInterface configure() implementation.
        Called internally by the configure() dispatcher when keyword
        arguments are detected.

        Validates and stores streaming parameters.  nidaqmx tasks are not
        started here — they are created in start_stream().
        """
        if self._streaming:
            raise RuntimeError(
                'Cannot configure instreamer while it is running. '
                'Call stop_stream() first.')

        streaming_mode = StreamingMode(streaming_mode)
        if streaming_mode not in self._instream_constraints.streaming_modes:
            raise ValueError(
                f'Invalid streaming mode "{streaming_mode}". '
                'Only CONTINUOUS is supported.')

        invalid = set(active_channels) - set(self._all_channels)
        if invalid:
            raise ValueError(
                f'Invalid channels {invalid}. '
                f'Valid channels are {set(self._all_channels)}.')

        self._instream_constraints.sample_rate.check(sample_rate)
        self._instream_constraints.channel_buffer_size.check(channel_buffer_size)

        # Always include the two FC rate channels even if the GUI did not
        # select them — they are zero-cost (no hardware task) and the
        # time-series logic expects them to be present.
        fc_set = list(_FC_CHANNELS)
        extra  = [ch for ch in active_channels if ch not in fc_set]
        self._active_channels     = fc_set + extra
        self._streaming_mode      = streaming_mode
        self._sample_rate         = float(sample_rate)
        self._channel_buffer_size = int(channel_buffer_size)

    def start_stream(self) -> None:
        """
        Start the background poll thread.  If the fast counter is not running,
        also starts the nidaqmx instreamer tasks for digital/analog channels.
        Safe to call regardless of the fast-counter state.
        """
        with self._stream_lock:
            if self._streaming:
                self.log.warning(
                    'start_stream() called but stream is already running.')
                return

            self._poll_rate_reader = self.register_rate_reader()
            self._poll_stop.clear()
            with self._ring_lock:
                self._ring_buffer = collections.deque(
                    maxlen=self._channel_buffer_size)

            # Start nidaqmx tasks only if the fast counter is not holding ctr0-ctr2.
            # Start nidaqmx tasks only when the FC is not holding ctr0-ctr2.
            if self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                self._ni_start_tasks()

            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
                name='instreamer-poll',
            )
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

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None,
                              ) -> None:
        """
        Read exactly samples_per_channel samples per channel from the ring
        buffer into data_buffer (interleaved layout).  Blocks until available.
        """
        if not self._streaming:
            raise RuntimeError('Cannot read data — stream is not running.')
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

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None,
                                        ) -> int:
        """Read all currently available samples.  Returns samples read per channel."""
        n_ch    = len(self._active_channels)
        to_read = min(self.available_samples, data_buffer.size // n_ch)
        if to_read == 0:
            return 0
        self.read_data_into_buffer(data_buffer, to_read, timestamp_buffer)
        return to_read

    def read_data(self,
                  samples_per_channel: Optional[int] = None,
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """Allocate a buffer and read samples.  Returns (data_buffer, None)."""
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        n_ch = len(self._active_channels)
        buf  = np.empty(samples_per_channel * n_ch, dtype=np.float64)
        self.read_data_into_buffer(buf, samples_per_channel)
        return buf, None

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """Return one sample for each active channel.  Blocks until available."""
        if not self._streaming:
            raise RuntimeError('Cannot read data — stream is not running.')
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
    #  nidaqmx instreamer — task lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def _ni_start_tasks(self) -> None:
        """
        Build and start all nidaqmx instreamer tasks:
          1. CO pulse clock on ctr3 at self._sample_rate Hz.
          2. One CI period task per active digital channel (one free counter each).
          3. One AI voltage task for all active analog channels.

        Mirrors the logic of NIXSeriesInStreamer._init_sample_clock /
        _init_digital_tasks / _init_analog_task, adapted for the fixed ctr3
        clock and the unified channel set.

        If any step fails, a warning is logged and only the successfully
        started tasks are left running; failed channels output zeros.

        Thread-safe via _ni_tasks_lock.  Does nothing if tasks are already
        running.
        """
        with self._ni_tasks_lock:
            if self._ni_tasks_running:
                return
            if not self._digital_sources and not self._analog_sources:
                # Rate channels only — no nidaqmx tasks needed.
                return

            dev = self._device_name
            clock_channel = None

            try:
                # ── 1. Sample-clock task (ctr3) ────────────────────────────────
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
                clock_channel = (
                    f'/{clk_task.channel_names[0]}InternalOutput')

            except ni.DaqError as e:
                self.log.error(
                    f'_ni_start_tasks: failed to start clock task: {e}. '
                    'Digital/analog instreamer channels will be unavailable.')
                self._ni_stop_tasks_unsafe()
                return

            # Determine which counters are free for digital channel tasks.
            # ctr3 is always reserved (clock just started above).
            # ctr0-ctr2 are only reserved when the fast counter is active.
            fc_active     = self._status in (self.STATUS_RUNNING,
                                             self.STATUS_PAUSED)
            reserved_ctrs = (set(_FC_COUNTERS) if fc_active else set()) | {_INSTREAM_CLK_CTR}
            try:
                all_ctrs = tuple(
                    c.split('/')[-1]
                    for c in ni.system.Device(dev).co_physical_chans.channel_names
                    if 'ctr' in c.lower()
                )
            except Exception:
                all_ctrs = ()

            free_ctrs = [c for c in all_ctrs if c not in reserved_ctrs]

            # ── 2. Digital counter tasks ───────────────────────────────────────
            active_di = [ch for ch in self._digital_sources
                         if ch in self._active_channels]
            free_ctr_iter = iter(free_ctrs)
            for chnl in active_di:
                ctr = next(free_ctr_iter, None)
                if ctr is None:
                    self.log.warning(
                        f'_ni_start_tasks: no free counter available for '
                        f'digital channel {chnl} — channel outputs zeros.')
                    continue
                ctr_full  = f'/{dev}/{ctr}'
                chnl_full = f'/{dev}/{chnl}'
                task_name = f'NiUsb63xx_DI_{chnl}_{id(self):d}'
                try:
                    task = ni.Task(task_name)
                    task.ci_channels.add_ci_period_chan(
                        ctr_full,
                        min_val=0,
                        max_val=100_000_000,
                        units=ni.constants.TimeUnits.TICKS,
                        edge=ni.constants.Edge.RISING,
                    )
                    # Route: gate = clock output, timebase source = signal PFI.
                    # Uses the same direct C-API workaround as NIXSeriesInStreamer
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
                        f'_ni_start_tasks: failed to create DI task for '
                        f'{chnl} on {ctr}: {e}. Channel outputs zeros.')
                    try:
                        task.close()
                    except Exception:
                        pass

            # ── 3. Analog input task ───────────────────────────────────────────
            active_ai = [ch for ch in self._analog_sources
                         if ch in self._active_channels]
            if active_ai:
                ai_ch_str = ','.join(f'/{dev}/{ch}' for ch in active_ai)
                try:
                    ai_task = ni.Task(f'NiUsb63xx_AI_{id(self):d}')
                    ai_task.ai_channels.add_ai_voltage_chan(
                        ai_ch_str,
                        max_val=max(self._cfg_adc_range),
                        min_val=min(self._cfg_adc_range),
                    )
                    ai_task.timing.cfg_samp_clk_timing(
                        self._sample_rate,
                        source=clock_channel,
                        active_edge=ni.constants.Edge.RISING,
                        sample_mode=ni.constants.AcquisitionType.CONTINUOUS,
                        samps_per_chan=self._channel_buffer_size,
                    )
                    ai_task.control(ni.constants.TaskMode.TASK_RESERVE)
                    self._ni_ai_reader = _PatchedAnalogReader(ai_task.in_stream)
                    self._ni_ai_reader.verify_array_shape = False
                    self._ni_ai_task = ai_task
                except ni.DaqError as e:
                    self.log.warning(
                        f'_ni_start_tasks: failed to create AI task: {e}. '
                        'Analog channels output zeros.')
                    try:
                        ai_task.close()
                    except Exception:
                        pass

            # ── 4. Start all tasks ─────────────────────────────────────────────
            # Digital and AI tasks first so they are ready when the clock fires.
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
                f'Digital: {started_di if started_di else "none"}  '
                f'Analog: {started_ai if started_ai else "none"}'
            )

    def _ni_stop_tasks(self) -> None:
        """Stop and clear all nidaqmx instreamer tasks (thread-safe)."""
        with self._ni_tasks_lock:
            self._ni_stop_tasks_unsafe()

    def _ni_stop_tasks_unsafe(self) -> None:
        """
        Stop and clear all nidaqmx instreamer tasks.
        Must be called with _ni_tasks_lock held (or during single-threaded
        teardown such as on_deactivate / error recovery in _ni_start_tasks).
        """
        if self._ni_tasks_running:
            self.log.info('Instreamer tasks stopped.')

        # Drop reader references first; the tasks themselves are closed below.
        self._ni_di_readers = []
        self._ni_ai_reader  = None

        for task in self._ni_di_tasks:
            try:
                if not task.is_task_done():
                    task.stop()
                task.close()
            except Exception as e:
                self.log.warning(f'_ni_stop_tasks: error closing DI task: {e}')
        self._ni_di_tasks = []

        if self._ni_ai_task is not None:
            try:
                if not self._ni_ai_task.is_task_done():
                    self._ni_ai_task.stop()
                self._ni_ai_task.close()
            except Exception as e:
                self.log.warning(f'_ni_stop_tasks: error closing AI task: {e}')
            self._ni_ai_task = None

        if self._ni_clk_task is not None:
            try:
                if not self._ni_clk_task.is_task_done():
                    self._ni_clk_task.stop()
                self._ni_clk_task.close()
            except Exception as e:
                self.log.warning(f'_ni_stop_tasks: error closing clock task: {e}')
            self._ni_clk_task = None

        self._ni_tasks_running = False

    def _ni_read_sample(self) -> np.ndarray:
        """
        Read one sample from each active nidaqmx instreamer channel.

        Returns a float64 array of length
        len(_digital_sources) + len(_analog_sources), in that order.
        Channels whose tasks are not running output 0.0.

        Called from the poll thread — must not block for more than one poll
        interval.  Uses read_one_sample_double (digital) and read_one_sample
        (analog), matching NIXSeriesInStreamer.read_single_point().
        """
        n_di = len(self._digital_sources)
        n_ai = len(self._analog_sources)
        result = np.zeros(n_di + n_ai, dtype=np.float64)

        if not self._ni_tasks_running:
            return result

        try:
            # Digital: raw tick count scaled by sample_rate gives counts/s.
            _tmp = np.empty(self._channel_buffer_size, dtype=np.float64)
            for i, reader in enumerate(self._ni_di_readers):
                n = reader.read_many_sample_double(
                    _tmp,
                    number_of_samples_per_channel=ni.constants.READ_ALL_AVAILABLE,
                    timeout=0.0,
                )
                if n > 0:
                    # Each sample is one inter-photon period in ticks.
                    # Multiply by sample_rate (= 1/tick_duration in clock units)
                    # to get counts/s, then take the mean across all drained
                    # samples — matching exactly what the standalone does per
                    # sample, averaged over the poll interval.
                    result[i] = float(np.mean(_tmp[:n])) * self._sample_rate

            # Analog: drain all buffered samples and return the mean per channel.
            if self._ni_ai_reader is not None:
                n_ai = len(self._analog_sources)
                _tmp_ai = np.empty(self._channel_buffer_size * n_ai, dtype=np.float64)
                n = self._ni_ai_reader.read_many_sample(
                    _tmp_ai,
                    number_of_samples_per_channel=ni.constants.READ_ALL_AVAILABLE,
                    timeout=0.0,
                )
                if n > 0:
                    result[n_di:] = _tmp_ai[:n * n_ai].reshape(n, n_ai).mean(axis=0)


        except Exception as e:
            self.log.warning(f'_ni_read_sample: read error: {e}')

        return result

    # ══════════════════════════════════════════════════════════════════════════
    #  Background poll thread
    # ══════════════════════════════════════════════════════════════════════════

    def _poll_loop(self) -> None:
        """
        Background thread — runs at sample_rate Hz.

        Each tick assembles one unified sample vector:
          [rate_all_hz, rate_gated_hz, <digital values...>, <analog values...>]

        - Fast-counter channels: registered rate reader (0.0 if FC not running).
        - Digital/analog channels: _ni_read_sample() (0.0 if tasks paused).
        """
        interval = 1.0 / self._sample_rate
        n_total  = len(self._all_channels)
        n_fc     = len(_FC_CHANNELS)

        while not self._poll_stop.is_set():
            t0 = time.monotonic()

            # Fast-counter rate channels — zero when FC is not running.
            # Fast-counter rate channels — zero when FC is not running.
            if (self._status == self.STATUS_RUNNING
                    and self._poll_rate_reader is not None):
                rate_all, rate_gated = self._poll_rate_reader()
            else:
                rate_all, rate_gated = 0.0, 0.0

            # Digital/analog channels — zero when nidaqmx tasks are stopped.
            ni_sample = self._ni_read_sample()

            sample        = np.empty(n_total, dtype=np.float64)
            sample[0]     = rate_all
            sample[1]     = rate_gated
            sample[n_fc:] = ni_sample

            with self._ring_lock:
                self._ring_buffer.append(sample)

            # Sleep for the remainder of the interval; wake early on stop signal.
            # Sleep for the remainder of the interval; wake early on stop signal.
            elapsed    = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                self._poll_stop.wait(timeout=sleep_time)

    # ══════════════════════════════════════════════════════════════════════════
    #  Additional public methods  (FastCounterInterface helpers)
    # ══════════════════════════════════════════════════════════════════════════

    def get_count_rates(self):
        """
        Return (rate_all_hz, rate_gated_hz) instantaneous count rates.
        Returns (0.0, 0.0) before the first processed cycle.
        """
        if self._default_rate_reader is None:
            return 0.0, 0.0
        return self._default_rate_reader()

    def register_rate_reader(self):
        """
        Return an independent rate-reading callable with private snapshot state.

        Each callable tracks its own (last counts, last time, last valid rates)
        so multiple callers never interfere.  Returns the last valid rates when
        no new data has arrived; returns (0.0, 0.0) before the first cycle.

        Usage
        -----
        read_rates = hw.register_rate_reader()
        rate_all, rate_gated = read_rates()
        """
        # Private snapshot state, one dict per registered reader.
        state = {
            'last_time'        : 0.0,
            'last_photon_snap' : 0,
            'last_gated_snap'  : 0,
            'last_cycle_snap'  : 0,
            'last_valid_rates' : (0.0, 0.0),
        }
        # Capture shared counter references as locals so the closure works
        # correctly even if the reader callable outlives the module.
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
            interval_all    = cur_all   - state['last_photon_snap']
            interval_gated  = cur_gated - state['last_gated_snap']
            interval_cycles = cur_cycles - state['last_cycle_snap']
            if interval_all == 0 or dt <= 0:
                # No new data since last call — return last valid result.
                return state['last_valid_rates']
            state['last_time']        = now
            state['last_photon_snap'] = cur_all
            state['last_gated_snap']  = cur_gated
            state['last_cycle_snap']  = cur_cycles
            rate_all_hz    = interval_all / dt
            # Total gate-open time = completed cycles × gates/cycle × gate width.
            gate_open_time = (interval_cycles
                              * self._num_gates_per_cycle
                              * self._gate_width_s)
            rate_gated_hz  = (interval_gated / gate_open_time
                              if gate_open_time > 0 else 0.0)
            rates = (rate_all_hz, rate_gated_hz)
            state['last_valid_rates'] = rates
            return rates

        return _read

    def _init_default_rate_reader(self):
        """Create the private rate reader used by get_count_rates()."""
        self._default_rate_reader = self.register_rate_reader()

    def get_hardware_status(self):
        """Return a snapshot of fast-counter pipeline buffer depths."""
        hw_ph   = (self._get_hw_available(self._photon_task)
                   if self._photon_task else -1)
        hw_gate = (self._get_hw_available(self._gate_task)
                   if self._gate_task else -1)
        with self._photon_lock:
            sw_ph_chunks  = len(self._photon_list)
            sw_ph_samples = sum(len(a) for a in self._photon_list)
        with self._gate_lock:
            sw_gate_chunks  = len(self._gate_list)
            sw_gate_samples = sum(len(a) for a in self._gate_list)
        stall = (self._gate_buffer is not None and
                 sw_gate_samples > self._gate_buffer // 2)
        return {
            'hw_photon_available': hw_ph,
            'hw_gate_available'  : hw_gate,
            'sw_photon_samples'  : sw_ph_samples,
            'sw_gate_samples'    : sw_gate_samples,
            'sw_photon_chunks'   : sw_ph_chunks,
            'sw_gate_chunks'     : sw_gate_chunks,
            'gate_stall_warning' : stall,
        }

    def print_summary(self):
        if self._accumulator is None:
            print('No data — device not configured.')
            return
        data, info = self.get_data_trace()
        cycles_done   = info['elapsed_sweeps']
        elapsed_total = info['elapsed_time']
        if cycles_done == 0:
            print('No complete cycles acquired yet.')
            return
        total_photons     = int(data.sum())
        total_gate_time_s = (cycles_done
                             * self._num_gates_per_cycle
                             * self._gate_width_s)
        count_rate_gated_hz = (total_photons / total_gate_time_s
                               if total_gate_time_s > 0 else 0.0)
        if cycles_done > 0 and elapsed_total > 0:
            gate_period_s     = (elapsed_total
                                 / (cycles_done * self._num_gates_per_cycle))
            dead_time_ns      = (gate_period_s - self._gate_width_s) * 1e9
            count_rate_seq_hz = total_photons / elapsed_total
            duty_cycle_pct    = 100.0 * self._gate_width_s / gate_period_s
        else:
            dead_time_ns = count_rate_seq_hz = duty_cycle_pct = 0.0
        print(f"\n{'─'*60}")
        print(f'  Cycles completed      : {cycles_done}')
        print(f'  Total gated photons   : {total_photons:,}')
        print(f'  Mean photons/cycle    : {total_photons / cycles_done:.1f}')
        print(f'  Mean photons/gate     : '
              f'{total_photons / (cycles_done * self._num_gates_per_cycle):.2f}')
        print(f'  Gate width            : {self._gate_width_s*1e6:.3f} µs')
        print(f'  Dead time (inferred)  : {dead_time_ns:.1f} ns')
        print(f'  Duty cycle            : {duty_cycle_pct:.1f} %')
        print(f'  Total gate open time  : {total_gate_time_s*1e3:.3f} ms')
        print(f'  Count rate (gated)    : {count_rate_gated_hz/1e3:.2f} kHz')
        print(f'  Count rate (sequence) : {count_rate_seq_hz/1e3:.2f} kHz')
        print(f'  Histogram shape       : {self._accumulator.shape}  dtype=uint64')
        print(f"{'─'*60}")

    # ══════════════════════════════════════════════════════════════════════════
    #  Internal helpers — fast-counter hardware and thread lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def _reset_run_state(self):
        """Zero all runtime accumulators without touching timing configuration."""
        if self._accumulator is not None:
            self._accumulator[:] = 0
        self._t_start_ref[0]   = 0.0
        self._elapsed_time_s   = 0.0
        with self._photon_count_lock:
            self._photon_count_ref[0]       = 0
            self._gated_photon_count_ref[0] = 0
        # Re-create the default rate reader so get_count_rates() returns
        # fresh rates rather than stale values from the previous run.
        if self._nidaq is not None:
            self._init_default_rate_reader()
        # Clear anchor sync so the next start_measure() captures a fresh t1_abs.
        self._t1_abs_ref[0] = np.uint64(0)
        self._t1_abs_ready.clear()
        with self._diag_lock:
            self._diag_reader_photons_ref[0]   = 0
            self._diag_reader_gates_ref[0]     = 0
            self._diag_proc_photons_ref[0]     = 0
            self._diag_hist_photons_ref[0]     = 0
            self._diag_proc_cycles_ref[0]      = 0
            self._diag_hist_cycles_ref[0]      = 0
            self._diag_leftover_photons_ref[0] = 0
            self._diag_leftover_gates_ref[0]   = 0
        self._diag_snap = {
            'time': 0.0, 'reader_photons': 0, 'reader_gates': 0,
            'proc_photons': 0, 'hist_photons': 0,
            'proc_cycles': 0, 'hist_cycles': 0,
        }

    def _start_hardware_and_threads(self):
        """Create all three ctypes DAQmx tasks and start every worker thread."""
        if self._nidaq is None:
            raise RuntimeError(
                '_start_hardware_and_threads() called before on_activate().')
        dev = self._device.decode()
        self._photon_task = self._make_photon_period_task(
            f'{dev}/ctr0'.encode(), self._photon_pfi, self._gate_pfi,
            self._photon_buffer, self._max_photon_rate)
        self._gate_task = self._make_gate_timestamp_task(
            f'{dev}/ctr1'.encode(), self._gate_pfi, self._gate_pfi,
            self._gate_buffer, self._max_gate_rate)
        self._anchor_task = self._make_anchor_timestamp_task(
            f'{dev}/ctr2'.encode(), self._photon_pfi, self._gate_pfi,
            buffer_size=1024)

        self._photon_stop     = threading.Event()
        self._gate_stop       = threading.Event()
        self._anchor_stop     = threading.Event()
        self._processor_stop  = threading.Event()
        self._diag_stop       = threading.Event()
        self._photon_overflow = threading.Event()
        self._gate_overflow   = threading.Event()
        self._anchor_overflow = threading.Event()

        self._t1_abs_ref[0] = np.uint64(0)
        self._t1_abs_ready.clear()

        self._anchor_thread    = self._make_anchor_reader_thread()
        self._photon_thread    = self._make_reader_thread(
            self._photon_task, self._photon_chunk,
            self._photon_list, self._photon_lock,
            self._photon_stop, self._photon_overflow, 'photon')
        self._gate_thread = self._make_reader_thread(
            self._gate_task, self._gate_chunk,
            self._gate_list, self._gate_lock,
            self._gate_stop, self._gate_overflow, 'gate')
        self._processor_thread = self._make_processor_thread()
        self._diag_thread      = self._make_diag_thread()

        # Arm hardware before starting threads so any edges during startup
        # are buffered in the hardware FIFOs and not lost.
        self._check(self._nidaq.DAQmxStartTask(self._photon_task))
        self._check(self._nidaq.DAQmxStartTask(self._gate_task))
        self._check(self._nidaq.DAQmxStartTask(self._anchor_task))

        self._t_start_ref[0] = time.monotonic()

        # Start anchor first to minimise photon-reader block time on t1_abs_ready.
        self._anchor_thread.start()
        self._photon_thread.start()
        self._gate_thread.start()
        self._processor_thread.start()
        self._diag_thread.start()

    def _stop_hardware_and_threads(self):
        """Stop all ctypes DAQmx tasks and join every worker thread."""
        if self._photon_task:
            self._nidaq.DAQmxStopTask(self._photon_task)
        if self._gate_task:
            self._nidaq.DAQmxStopTask(self._gate_task)
        if self._anchor_task:
            try:
                self._nidaq.DAQmxStopTask(self._anchor_task)
                self._nidaq.DAQmxClearTask(self._anchor_task)
            except Exception:
                pass
            self._anchor_task = None

        for ev in (self._anchor_stop, self._photon_stop, self._gate_stop,
                   self._processor_stop, self._diag_stop):
            if ev:
                ev.set()
        # Safety unblock: if the anchor thread errored before setting
        # t1_abs_ready the photon reader would hang — unblock it now.
        self._t1_abs_ready.set()

        for t, tmo in ((self._anchor_thread,    3.0),
                       (self._diag_thread,      3.0),
                       (self._processor_thread, 5.0),
                       (self._photon_thread,    2.0),
                       (self._gate_thread,      2.0)):
            if t and t.is_alive():
                t.join(timeout=tmo)

        if self._photon_task:
            self._nidaq.DAQmxClearTask(self._photon_task)
            self._photon_task = None
        if self._gate_task:
            self._nidaq.DAQmxClearTask(self._gate_task)
            self._gate_task = None

        with self._photon_lock:
            self._photon_list.clear()
        with self._gate_lock:
            self._gate_list.clear()

    # ══════════════════════════════════════════════════════════════════════════
    #  Internal helpers — ctypes DAQmx wrappers
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _load_nidaq():
        if os.name == 'nt':
            return ctypes.windll.nicaiu                        # Windows
        return ctypes.cdll.LoadLibrary('libnidaqmx.so')         # Linux

    def _declare_argtypes(self):
        """
        Declare C-level argtypes for every DAQmx function used by the ctypes
        fast-counter path.  Without explicit argtypes ctypes guesses integer
        widths, which can silently pass wrong bit patterns and cause -200077
        or similar DAQmx errors.
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
        n.DAQmxResetDevice.argtypes = [ctypes.c_char_p]
        n.DAQmxResetDevice.restype  = ctypes.c_int32
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
        """Translate a non-zero DAQmx error code into a Python RuntimeError."""
        if err != 0:
            buf = ctypes.create_string_buffer(2048)
            self._nidaq.DAQmxGetErrorString(err, buf, 2048)
            raise RuntimeError(f'DAQmx Error {err}: {buf.value.decode()}')

    def _get_hw_available(self, task_handle):
        """Return samples waiting in the hardware FIFO, or -1 on error / no task."""
        if task_handle is None:
            return -1
        avail = ctypes.c_uint32(0)
        err = self._nidaq.DAQmxGetReadAvailSampPerChan(
            task_handle, ctypes.byref(avail))
        return int(avail.value) if err == 0 else -1

    def _make_photon_period_task(self, channel, photon_pfi, start_trigger,
                                 buffer_size, max_rate):
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        self._check(self._nidaq.DAQmxCreateCIPeriodChan(
            h, channel, b'',
            ctypes.c_double(1.0),               # min expected period (ticks)
            ctypes.c_double(float(2**32 - 1)),  # max expected period (ticks)
            ctypes.c_int32(DAQmx_Val_Ticks), ctypes.c_int32(DAQmx_Val_Rising),
            ctypes.c_int32(DAQmx_Val_LowFreq1Ctr),
            ctypes.c_double(0.001),  # divisor frequency (unused for ticks)
            ctypes.c_uint32(1),      # edge count divisor
            None,
        ))
        self._check(self._nidaq.DAQmxSetCIPeriodTerm(h, channel, photon_pfi))
        self._check(self._nidaq.DAQmxSetCICtrTimebaseSrc(
            h, channel, self._timebase_term))
        self._check(self._nidaq.DAQmxCfgImplicitTiming(
            h, ctypes.c_int32(DAQmx_Val_ContSamps),
            ctypes.c_uint64(buffer_size)))
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(
            h, DAQmx_Val_Rising))
        return h

    def _make_gate_timestamp_task(self, channel, gate_pfi, start_trigger,
                                  buffer_size, max_rate):
        h = ctypes.c_void_p(0)
        self._check(self._nidaq.DAQmxCreateTask(b'', ctypes.byref(h)))
        self._check(self._nidaq.DAQmxCreateCICountEdgesChan(
            h, channel, b'', DAQmx_Val_Rising, 0, DAQmx_Val_CountUp))
        self._check(self._nidaq.DAQmxSetCICountEdgesTerm(
            h, channel, self._timebase_term))
        self._check(self._nidaq.DAQmxCfgSampClkTiming(
            h, gate_pfi, float(max_rate),
            DAQmx_Val_Rising, DAQmx_Val_ContSamps,
            ctypes.c_uint64(buffer_size)))
        self._check(self._nidaq.DAQmxSetArmStartTrigType(h, DAQmx_Val_DigEdge))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigSrc(h, start_trigger))
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(
            h, DAQmx_Val_Rising))
        return h

    def _make_anchor_timestamp_task(self, channel, photon_pfi, start_trigger,
                                    buffer_size=1024):
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
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(
            h, DAQmx_Val_Rising))
        return h

    # ══════════════════════════════════════════════════════════════════════════
    #  Internal helpers — fast-counter thread factories
    # ══════════════════════════════════════════════════════════════════════════

    def _make_anchor_reader_thread(self):
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
            # Poll ctr2 until the first photon edge latches a tick count.
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
                ctypes.byref(samps_read), None,
            )
            if err < 0 or samps_read.value != 1:
                buf = ctypes.create_string_buffer(2048)
                nidaq.DAQmxGetErrorString(err, buf, 2048)
                self.log.error(f'\n[anchor] FATAL read error err={err}: '
                               f'{buf.value.decode()}', flush=True)
                anchor_overflow.set()
                t1_abs_ready.set()
                return
            t1_abs_ref[0] = np.uint64(raw_buf[0])
            if diag_enabled:
                print(f'[anchor] t1_abs = {t1_abs_ref[0]} ticks  '
                    f'({int(t1_abs_ref[0]) * _TICK_NS * 1e-6:.3f} ms after arm)',
                    flush=True)
            # Signal the photon reader — it can now seed its cumsum.
            t1_abs_ready.set()
            # ctr2 has served its purpose; free the counter resource.
            nidaq.DAQmxStopTask(anchor_task)
            nidaq.DAQmxClearTask(anchor_task)
            if diag_enabled:
                print('[anchor] ctr2 stopped and cleared.', flush=True)

        return threading.Thread(target=_run, daemon=True, name='anchor')

    def _make_reader_thread(self, task_handle, chunk_size, shared_list, lock,
                            stop_event, overflow_event, label):

        diag_enabled    = self._diag_enabled

        raw_buf    = (ctypes.c_uint32 * chunk_size)()
        samps_read = ctypes.c_int32(0)
        nidaq      = self._nidaq

        diag_ref  = (self._diag_reader_photons_ref if label == 'photon'
                     else self._diag_reader_gates_ref)
        diag_lock = self._diag_lock

        t1_abs_ref   = self._t1_abs_ref
        t1_abs_ready = self._t1_abs_ready

        if label == 'photon':
            period_state = {'abs_tick': np.uint64(0), 't1_emitted': False}
        else:
            rollover_state = {'prev_rollover': np.uint64(0),
                              'last_abs':      np.uint64(0)}

        min_batch = max(100, chunk_size // 100)

        def _run():
            avail = ctypes.c_uint32(0)
            if label == 'photon':
                if diag_enabled:
                    print('[photon reader] waiting for anchor t1_abs …', flush=True)
                t1_abs_ready.wait()
                period_state['abs_tick'] = t1_abs_ref[0]
                if diag_enabled:
                    print(f'[photon reader] seeded abs_tick = '
                        f'{period_state["abs_tick"]}', flush=True)

            while not stop_event.is_set():
                nidaq.DAQmxGetReadAvailSampPerChan(
                    task_handle, ctypes.byref(avail))
                to_read = min(avail.value, chunk_size)
                if to_read < min_batch:
                    time.sleep(0.0001)
                    continue
                err = nidaq.DAQmxReadCounterU32(
                    task_handle, ctypes.c_int32(to_read),
                    ctypes.c_double(1.0),
                    raw_buf, ctypes.c_uint32(chunk_size),
                    ctypes.byref(samps_read), None,
                )
                n = samps_read.value
                if err < 0:
                    buf = ctypes.create_string_buffer(2048)
                    nidaq.DAQmxGetErrorString(err, buf, 2048)
                    self.log.error(f'\n[reader-{label}] FATAL err={err}: '
                                   f'{buf.value.decode()}', flush=True)
                    overflow_event.set()
                    stop_event.set()
                    break
                if err > 0:
                    # Positive codes are warnings (e.g. hardware buffer overflow).
                    buf = ctypes.create_string_buffer(2048)
                    nidaq.DAQmxGetErrorString(err, buf, 2048)
                    self.log.warning(f'\n[reader-{label}] warning={err}: '
                                     f'{buf.value.decode()}', flush=True)
                if n == 0:
                    continue

                if label == 'photon':
                    # ctr0 returns inter-photon intervals (ticks between rising edges).
                    # Cumsum converts them to absolute tick timestamps.
                    intervals = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                 .copy().astype(np.uint64))
                    if not period_state['t1_emitted']:
                        intervals = np.concatenate(
                            [np.array([0], dtype=np.uint64), intervals])
                        period_state['t1_emitted'] = True
                    absolute = period_state['abs_tick'] + np.cumsum(intervals)
                    period_state['abs_tick'] = absolute[-1]
                else:
                    # ctr1 returns raw uint32 edge-timestamps that wrap every ~43 s.
                    # Reconstruct monotonic uint64 absolute ticks by detecting
                    # wraps both between chunks (inter) and within chunks (intra).
                    counts64 = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                .copy().astype(np.uint64))
                    offsets     = np.zeros(n, dtype=np.uint64)
                    n_new_wraps = np.uint64(0)
                    if rollover_state['last_abs'] > 0:
                        # Inter-chunk wrap: a negative signed delta from the
                        # last emitted value to the first new value means the
                        # counter wrapped between reads.
                        last_raw    = (rollover_state['last_abs']
                                       % np.uint64(2**32))
                        delta_first = (np.int64(counts64[0])
                                       - np.int64(last_raw))
                        if delta_first < 0:
                            offsets     += np.uint64(2**32)
                            n_new_wraps += np.uint64(1)
                    # Intra-chunk wrap: negative signed delta between
                    # consecutive raw values means the counter wrapped there.
                    diffs    = np.diff(counts64.view(np.int64))
                    wrap_idx = np.where(diffs < 0)[0] + 1
                    for idx in wrap_idx:
                        offsets[idx:] += np.uint64(2**32)
                        n_new_wraps   += np.uint64(1)
                    absolute = (counts64 + offsets
                                + rollover_state['prev_rollover'])
                    # Update prev_rollover after computing absolute so this
                    # chunk used the old base and the next chunk gets the new one.
                    rollover_state['prev_rollover'] += (
                        n_new_wraps * np.uint64(2**32))
                    rollover_state['last_abs'] = absolute[-1]

                with lock:
                    shared_list.append(absolute)
                with diag_lock:
                    diag_ref[0] += n

        return threading.Thread(target=_run, daemon=True,
                                name=f'reader-{label}')

    def _make_processor_thread(self):
        photon_list        = self._photon_list
        gate_list          = self._gate_list
        photon_lock        = self._photon_lock
        gate_lock          = self._gate_lock
        accumulator        = self._accumulator
        stop_event         = self._processor_stop
        overflow_events    = [self._photon_overflow, self._gate_overflow,
                              self._anchor_overflow]
        photon_count_ref       = self._photon_count_ref
        gated_photon_count_ref = self._gated_photon_count_ref
        photon_count_lock      = self._photon_count_lock
        num_gates_per_cycle    = self._num_gates_per_cycle
        gate_ticks             = self._gate_ticks
        _n_bins                = self._n_bins
        histogram_batch        = self._histogram_batch
        diag_lock              = self._diag_lock
        diag_proc_photons_ref  = self._diag_proc_photons_ref
        diag_hist_photons_ref  = self._diag_hist_photons_ref
        diag_proc_cycles_ref   = self._diag_proc_cycles_ref
        diag_hist_cycles_ref   = self._diag_hist_cycles_ref
        diag_leftover_ph_ref   = self._diag_leftover_photons_ref
        diag_leftover_gt_ref   = self._diag_leftover_gates_ref

        def _run():
            leftover_photons = np.empty(0, dtype=np.uint64)
            leftover_gates   = np.empty(0, dtype=np.uint64)

            # ── Phase alignment ───────────────────────────────────────────────────
            # The NI hardware misses the very first gate edge (it serves as the
            # arm-start trigger and cannot simultaneously be a sample-clock latch).
            # ctr1's first sample is therefore the SECOND physical gate, making the
            # first N-1 timestamps an incomplete cycle with wrong phase.
            # We wait until N gate timestamps have arrived, then discard the first
            # N-1 of them.  The remaining one (index N-1) is the first gate of the
            # second physical cycle and becomes our true cycle origin.
            # Photons that arrived before this point are also discarded
            phase_aligned = False
            phase_n = num_gates_per_cycle  # need N gates to drop N-1 and keep 1

            while not stop_event.is_set() and not phase_aligned:
                if any(ev.is_set() for ev in overflow_events):
                    stop_event.set()
                    return
                with gate_lock:
                    collected = sum(len(a) for a in gate_list) + len(leftover_gates)
                if collected < phase_n:
                    time.sleep(0.001)
                    continue
                # Pull whatever has arrived so far.
                with gate_lock:
                    chunks = gate_list.copy()
                    gate_list.clear()
                if chunks:
                    new_gates = np.concatenate(chunks)
                    leftover_gates = (np.concatenate([leftover_gates, new_gates])
                                    if len(leftover_gates) else new_gates)
                # Keep only the last gate timestamp (true cycle origin); discard the rest.
                leftover_gates = leftover_gates[phase_n - 1:]
                # Discard all photons accumulated before the true cycle origin.
                cutoff = leftover_gates[0]
                with photon_lock:
                    chunks = photon_list.copy()
                    photon_list.clear()
                if chunks:
                    new_photons = np.concatenate(chunks)
                    leftover_photons = (np.concatenate([leftover_photons, new_photons])
                                        if len(leftover_photons) else new_photons)
                split = np.searchsorted(leftover_photons, cutoff, side='left')
                leftover_photons = leftover_photons[split:]
                phase_aligned = True

            while not stop_event.is_set():
                if any(ev.is_set() for ev in overflow_events):
                    stop_event.set(); break

                # Wait until at least one full gate cycle is available.
                with gate_lock:
                    gate_count = (sum(len(a) for a in gate_list)
                                  + len(leftover_gates))
                if gate_count < num_gates_per_cycle:
                    time.sleep(0.001)
                    continue

                # Atomically swap out both shared queues.
                with photon_lock:
                    new_photon_chunks = photon_list.copy()
                    photon_list.clear()
                with gate_lock:
                    new_gate_chunks = gate_list.copy()
                    gate_list.clear()

                if new_gate_chunks:
                    new_gates = np.concatenate(new_gate_chunks)
                    all_gates = (np.concatenate([leftover_gates, new_gates])
                                 if len(leftover_gates) else new_gates)
                else:
                    all_gates = leftover_gates

                n_complete = len(all_gates) // num_gates_per_cycle
                if n_complete == 0:
                    leftover_gates = all_gates
                    time.sleep(0.001)
                    continue

                if new_photon_chunks:
                    new_photons = np.concatenate(new_photon_chunks)
                    all_photons = (np.concatenate([leftover_photons, new_photons])
                                   if len(leftover_photons) else new_photons)
                else:
                    all_photons = leftover_photons

                n_gates_batch   = n_complete * num_gates_per_cycle
                gate_rise_batch = all_gates[:n_gates_batch]
                last_cycle_end  = gate_rise_batch[-1] + np.uint64(gate_ticks)
                photon_deadline = last_cycle_end + PHOTON_SLACK_TICKS

                # Wait until the photon stream has advanced far enough past
                # the last gate close that no late-arriving photons will be
                # missed (deadline = last gate close + PHOTON_SLACK_TICKS).
                while not stop_event.is_set():
                    if any(ev.is_set() for ev in overflow_events):
                        stop_event.set(); break
                    photon_max = (all_photons[-1] if len(all_photons)
                                  else np.uint64(0))
                    with photon_lock:
                        for chunk in photon_list:
                            if len(chunk) and chunk[-1] > photon_max:
                                photon_max = chunk[-1]
                    if photon_max >= photon_deadline:
                        break
                    time.sleep(0.001)

                if stop_event.is_set():
                    break

                with photon_lock:
                    late_chunks = photon_list.copy()
                    photon_list.clear()
                if late_chunks:
                    late = np.concatenate(late_chunks)
                    late.sort()
                    all_photons = (np.concatenate([all_photons, late])
                                   if len(all_photons) else late)

                ph_lo = np.searchsorted(all_photons, gate_rise_batch[0],
                                        side='left')
                ph_hi = np.searchsorted(all_photons, last_cycle_end,
                                        side='right')
                photons_batch = all_photons[ph_lo:ph_hi]

                batch_hist     = histogram_batch(photons_batch, gate_rise_batch,
                                                 num_gates_per_cycle, _n_bins,
                                                 gate_ticks)
                n_consumed     = len(photons_batch)
                n_hist_photons = int(batch_hist.sum())
                accumulator[:] += batch_hist

                with photon_count_lock:
                    photon_count_ref[0]       += n_consumed
                    gated_photon_count_ref[0] += n_hist_photons
                with diag_lock:
                    diag_proc_photons_ref[0] += n_consumed
                    diag_hist_photons_ref[0] += n_hist_photons
                    diag_proc_cycles_ref[0]  += n_complete
                    diag_hist_cycles_ref[0]  += n_complete

                # Carry forward gates and photons that belong to future batches.
                leftover_gates = all_gates[n_gates_batch:]
                split = np.searchsorted(all_photons, last_cycle_end,
                                        side='right')
                leftover_photons = all_photons[split:]

                with diag_lock:
                    diag_leftover_ph_ref[0] = len(leftover_photons)
                    diag_leftover_gt_ref[0] = len(leftover_gates)

        return threading.Thread(target=_run, daemon=True, name='processor')

    def _make_diag_thread(self):
        interval        = self._diag_interval_s
        diag_enabled    = self._diag_enabled
        stop_event      = self._diag_stop
        diag_lock       = self._diag_lock
        photon_lock     = self._photon_lock
        gate_lock       = self._gate_lock
        photon_list     = self._photon_list
        gate_list       = self._gate_list
        photon_task_ref = lambda: self._photon_task
        gate_task_ref   = lambda: self._gate_task
        reader_ph_ref   = self._diag_reader_photons_ref
        reader_gt_ref   = self._diag_reader_gates_ref
        proc_ph_ref     = self._diag_proc_photons_ref
        hist_ph_ref     = self._diag_hist_photons_ref
        proc_cy_ref     = self._diag_proc_cycles_ref
        hist_cy_ref     = self._diag_hist_cycles_ref
        leftover_ph_ref = self._diag_leftover_photons_ref
        leftover_gt_ref = self._diag_leftover_gates_ref
        snap            = self._diag_snap

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
                    cur_rph = reader_ph_ref[0]; cur_rgt = reader_gt_ref[0]
                    cur_pph = proc_ph_ref[0];   cur_hph = hist_ph_ref[0]
                    cur_pcy = proc_cy_ref[0];   cur_hcy = hist_cy_ref[0]
                    left_ph = leftover_ph_ref[0]; left_gt = leftover_gt_ref[0]
                with photon_lock:
                    sw_ph_chunks  = len(photon_list)
                    sw_ph_samples = sum(len(a) for a in photon_list)
                with gate_lock:
                    sw_gt_chunks  = len(gate_list)
                    sw_gt_samples = sum(len(a) for a in gate_list)
                hw_ph = self._get_hw_available(photon_task_ref())
                hw_gt = self._get_hw_available(gate_task_ref())
                d_rph = cur_rph - snap['reader_photons']
                d_rgt = cur_rgt - snap['reader_gates']
                d_pph = cur_pph - snap['proc_photons']
                d_hph = cur_hph - snap['hist_photons']
                d_pcy = cur_pcy - snap['proc_cycles']
                d_hcy = cur_hcy - snap['hist_cycles']
                snap.update({
                    'time': now, 'reader_photons': cur_rph,
                    'reader_gates': cur_rgt, 'proc_photons': cur_pph,
                    'hist_photons': cur_hph, 'proc_cycles': cur_pcy,
                    'hist_cycles': cur_hcy,
                })
                gate_eff  = (100.0 * cur_hph / cur_pph) if cur_pph > 0 else 0.0
                cycle_eff = (100.0 * cur_hcy / cur_pcy) if cur_pcy > 0 else 0.0
                W = 62; sep = '─' * W
                print(f'\n┌{sep}┐')
                print(f'│  DIAGNOSTICS  (Δt = {dt:.2f} s)' + ' ' * (W - 32) + '│')
                print(f'├{sep}┤')
                print(f'│  {"READER":12s}  {"cumulative":>14s}   {"rate/s":>10s}   {"HW FIFO":>7s}  │')
                print(f'│  {"photons":12s}  {cur_rph:>14,d}   {d_rph/dt:>10.0f}   {hw_ph:>7d}  │')
                print(f'│  {"gates":12s}  {cur_rgt:>14,d}   {d_rgt/dt:>10.0f}   {hw_gt:>7d}  │')
                print(f'├{sep}┤')
                print(f'│  {"SW BUFFER":12s}  {"samples":>14s}   {"chunks":>10s}' + ' ' * 12 + '│')
                print(f'│  {"photon list":12s}  {sw_ph_samples:>14,d}   {sw_ph_chunks:>10d}' + ' ' * 12 + '│')
                print(f'│  {"gate list":12s}  {sw_gt_samples:>14,d}   {sw_gt_chunks:>10d}' + ' ' * 12 + '│')
                print(f'├{sep}┤')
                print(f'│  {"PROCESSOR":12s}  {"cumulative":>14s}   {"rate/s":>10s}' + ' ' * 12 + '│')
                print(f'│  {"ph consumed":12s}  {cur_pph:>14,d}   {d_pph/dt:>10.0f}' + ' ' * 12 + '│')
                print(f'│  {"ph histgrm\'d":12s}  {cur_hph:>14,d}   {d_hph/dt:>10.0f}   {gate_eff:>5.1f} %  │')
                print(f'│  {"cy processed":12s}  {cur_pcy:>14,d}   {d_pcy/dt:>10.1f}' + ' ' * 12 + '│')
                print(f'│  {"cy histgrm\'d":12s}  {cur_hcy:>14,d}   {d_hcy/dt:>10.1f}   {cycle_eff:>5.1f} %  │')
                print(f'├{sep}┤')
                print(f'│  {"LEFTOVERS":12s}  {"(instantaneous)":>25s}' + ' ' * 21 + '│')
                print(f'│  {"photons":12s}  {left_ph:>14,d}' + ' ' * 34 + '│')
                print(f'│  {"gates":12s}  {left_gt:>14,d}' + ' ' * 34 + '│')
                print(f'└{sep}┘', flush=True)

        return threading.Thread(target=_run, daemon=True, name='diag')

    @staticmethod
    def _histogram_batch(photons_sorted, gate_rise_all,
                         num_gates, n_bins, gate_ticks):
        """
        Vectorised histogram kernel.

        Parameters
        ----------
        photons_sorted : uint64 ndarray  Pre-sliced photon timestamps, sorted.
        gate_rise_all  : uint64 ndarray  Gate-open timestamps, sorted ascending.
        num_gates      : int
        n_bins         : int
        gate_ticks     : int

        Returns
        -------
        hist : uint64 ndarray, shape (num_gates, n_bins)
        """
        gate_ticks_u64 = np.uint64(gate_ticks)
        hist = np.zeros((num_gates, n_bins), dtype=np.uint64)
        if len(photons_sorted) == 0:
            return hist
        gate_ends_all = gate_rise_all + gate_ticks_u64

        # For each photon, find which gate window it belongs to.
        # searchsorted(..., "right") - 1 gives the most recent gate whose
        # open-time is <= the photon timestamp; -1 means before any gate.
        gate_idx = (np.searchsorted(gate_rise_all, photons_sorted, side='right')
                    .astype(np.int64) - 1)
        # Discard photons that preceded the first gate opening.
        valid    = gate_idx >= 0
        gate_idx = gate_idx[valid]
        ph       = photons_sorted[valid]
        # Discard photons that arrived after their gate window closed.
        in_win   = ph < gate_ends_all[gate_idx]
        gate_idx = gate_idx[in_win]
        ph       = ph[in_win]
        if len(ph) == 0:
            return hist
        # Map each photon to a flat accumulator index.
        # offset = tick position within the gate window (= histogram bin number).
        # gate_in_cycle = which gate within the cycle (= accumulator row).
        offset        = (ph - gate_rise_all[gate_idx]).astype(np.int64)
        gate_in_cycle = gate_idx % num_gates
        flat_idx      = gate_in_cycle * n_bins + offset
        counts = np.bincount(flat_idx, minlength=num_gates * n_bins)
        hist  += counts.reshape(num_gates, n_bins).astype(np.uint64)
        return hist
