# -*- coding: utf-8 -*-
"""
NI USB-63xx — Combined FastCounterInterface + DataInStreamInterface + Scanner Counter
======================================================================================

Required hardware connections (fast counter)
---------------------------------------------
  PFI?  <-  photon detector output  (each rising edge = one detected photon)
  PFI?  <-  gate / excitation pulse (each rising edge opens one counting window)

Counter budget
--------------
  Fast counter (running)   : ctr0, ctr1, ctr2
  Instreamer clock         : ctr3
  Scan counter             : ctr0 (CI), ctr1 (CO) -- configurable
  Instreamer digital chans : one counter each from the free pool

  Priority: fast counter > scanning > instreamer

SCANNING EXTENSION
------------------
Three public methods satisfy the scanning counter interface for PIE710CounterInterfuse:

    channel_names    property  -> list of channel name strings
    channel_units    property  -> dict {channel_name: 'c/s'}
    arm(n_pixels, t_pixel)     -> stop instreamer, create CO+CI scan tasks
    read(n_pixels)             -> wait for scan to finish, return per-pixel counts
    stop()                     -> abort scan tasks, restart instreamer if needed

State interaction
-----------------
    arm()          : stops instreamer tasks, saves their running state,
                     creates CO+CI scan tasks
    read()  }      : cleans up scan tasks, restarts instreamer if it was running
    stop()  }        before arm() was called
    start_measure(): stops any active scan tasks first (fast counter has priority)

Deadlock prevention
-------------------
    _scan_lock is a threading.RLock (reentrant) so that read()'s finally block
    can call _scan_cleanup_unsafe() -> _ni_start_tasks() from the same thread
    without deadlocking.
    _ni_start_tasks() reads the plain boolean _scan_active instead of acquiring
    _scan_lock, further preventing any lock-ordering issues.

Qudi configuration example
---------------------------
hardware:
  ni_combined:
    module.Class: 'ni_x_series.ni_x_series_counter.NIXSeriesCounter'
    options:
      device_name:           'Dev1'
      photon_pfi:            'PFI8'
      gate_pfi:              'PFI10'
      diag_enabled:          false
      diag_interval_s:       2.0
      sample_rate:           10.0
      channel_buffer_size:   10000
      digital_sources:
        - 'PFI8'
      adc_voltage_range:     [-10, 10]
      read_write_timeout:    10
      scan_counter_channel:  'ctr0'
      scan_clock_counter:    'ctr1'
      scan_trigger_terminal: 'PFI1'
      scan_apd_terminal:     'PFI8'
      scan_channel_name:     'APD1'
      scan_read_timeout:     30.0
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
#  DAQmx integer constants
# ══════════════════════════════════════════════════════════════════════════════
DAQmx_Val_Rising      = 10280
DAQmx_Val_CountUp     = 10128
DAQmx_Val_ContSamps   = 10123
DAQmx_Val_DigEdge     = 10150
DAQmx_Val_Ticks       = 10304
DAQmx_Val_LowFreq1Ctr = 10105

_TIMEBASE_HZ = 100e6
_TICK_NS     = 1e9 / _TIMEBASE_HZ

PHOTON_SLACK_TICKS = np.uint64(10_000)

_MAX_PHOTON_RATE_HZ = 10_000_000
_MAX_GATE_RATE_HZ   = 10_000_000

_CH_ALL      = 'rate_all_hz'
_CH_GATED    = 'rate_gated_hz'
_FC_CHANNELS = (_CH_ALL, _CH_GATED)

_SAMPLE_RATE_MIN =   1.0
_SAMPLE_RATE_MAX = 100.0
_SAMPLE_RATE_DEF =  10.0

_FC_COUNTERS      = ('ctr0', 'ctr1', 'ctr2')
_INSTREAM_CLK_CTR = 'ctr3'

# PI E-710 waveform generator sample rate -- must match PIE710Controller.SAMP_RATE
_PI_SAMP_RATE: float = 5000.0


# ══════════════════════════════════════════════════════════════════════════════
#  Patched AnalogMultiChannelReader
# ══════════════════════════════════════════════════════════════════════════════
class _PatchedAnalogReader(_AnalogMultiChannelReader):
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
    Combined Qudi hardware module for NI USB-63xx.
    Implements FastCounterInterface + DataInStreamInterface + scanning counter.
    """

    # ── Original ConfigOptions ─────────────────────────────────────────────────
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

    # ── Scanning ConfigOptions ─────────────────────────────────────────────────
    _scan_counter_ch   = ConfigOption(
        'scan_counter_channel',   'ctr0', missing='nothing')
    _scan_clock_ctr    = ConfigOption(
        'scan_clock_counter',     'ctr1', missing='nothing')
    _scan_trigger_term = ConfigOption(
        'scan_trigger_terminal',  'PFI1', missing='warn')
    _scan_apd_term     = ConfigOption(
        'scan_apd_terminal',      None,   missing='nothing')
    _scan_ch_name      = ConfigOption(
        'scan_channel_name',      'APD1', missing='nothing')
    _scan_rw_timeout   = ConfigOption(
        'scan_read_timeout',      30.0,   missing='nothing')

    STATUS_UNCONFIGURED = 0
    STATUS_IDLE         = 1
    STATUS_RUNNING      = 2
    STATUS_PAUSED       = 3
    STATUS_ERROR        = -1

    # ══════════════════════════════════════════════════════════════════════════
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._device        = None
        self._photon_pfi    = None
        self._gate_pfi      = None
        self._timebase_term = None

        self._max_photon_rate = float(_MAX_PHOTON_RATE_HZ)
        self._max_gate_rate   = float(_MAX_GATE_RATE_HZ)

        self._gate_width_s        = None
        self._num_gates_per_cycle = None
        self._gate_ticks          = None
        self._n_bins              = None

        self._photon_buffer = None
        self._gate_buffer   = None
        self._photon_chunk  = None
        self._gate_chunk    = None

        self._status = self.STATUS_UNCONFIGURED

        self._photon_task = None
        self._gate_task   = None
        self._anchor_task = None

        self._photon_list = []
        self._gate_list   = []
        self._photon_lock = threading.Lock()
        self._gate_lock   = threading.Lock()

        self._accumulator    = None
        self._t_start_ref    = [0.0]
        self._elapsed_time_s = 0.0

        self._photon_count_ref       = [0]
        self._gated_photon_count_ref = [0]
        self._photon_count_lock      = threading.Lock()

        self._default_rate_reader = None

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

        self._photon_thread    = None
        self._gate_thread      = None
        self._anchor_thread    = None
        self._processor_thread = None
        self._diag_thread      = None

        self._photon_stop     = None
        self._gate_stop       = None
        self._anchor_stop     = None
        self._processor_stop  = None
        self._diag_stop       = None
        self._photon_overflow = None
        self._gate_overflow   = None
        self._anchor_overflow = None

        self._t1_abs_ref   = [np.uint64(0)]
        self._t1_abs_ready = threading.Event()

        self._nidaq = None

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

        # ── Scan task state ────────────────────────────────────────────────────
        self._scan_task          = None    # CI task (photon counting)
        self._scan_co_task       = None    # CO task (scan clock)
        self._scan_reader        = None    # CounterReader for CI task
        self._scan_n_steps       = 1       # PI waveform steps per pixel
        self._scan_n_pixels      = 0       # pixels per scan line
        self._scan_was_streaming = False   # instreamer state before scan started
        self._scan_active        = False   # True only while CO+CI tasks are running
        # RLock (reentrant) so read()'s finally block can call
        # _scan_cleanup_unsafe -> _ni_start_tasks on the same thread.
        self._scan_lock = threading.RLock()

    # ══════════════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def on_activate(self):
        device_name = self._device_name

        self._device        = device_name.encode()
        self._photon_pfi    = f'/{device_name}/{self._photon_pfi_line}'.encode()
        self._gate_pfi      = f'/{device_name}/{self._gate_pfi_line}'.encode()
        self._timebase_term = f'/{device_name}/100MHzTimebase'.encode()

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
                'on_activate: >3 digital sources; only first 3 used.')
            self._digital_sources = self._digital_sources[:3]
        if len(self._analog_sources) > 16:
            self.log.warning(
                'on_activate: >16 analog sources; only first 16 used.')
            self._analog_sources = self._analog_sources[:16]

        self._all_channels = (list(_FC_CHANNELS)
                              + self._digital_sources
                              + self._analog_sources)

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

        self._sample_rate         = float(np.clip(
            self._cfg_sample_rate, sr_min, sr_max))
        self._channel_buffer_size = max(2, int(self._cfg_channel_buf_size))
        self._active_channels     = list(self._all_channels)
        self._streaming_mode      = StreamingMode.CONTINUOUS

        self._status = self.STATUS_UNCONFIGURED
        self._init_default_rate_reader()

        # Validate scan config
        if self._scan_counter_ch.lower() == self._scan_clock_ctr.lower():
            self.log.warning(
                f'scan_counter_channel and scan_clock_counter are both '
                f'"{self._scan_counter_ch}". They must be different.')
        apd = self._scan_apd_term if self._scan_apd_term else self._photon_pfi_line
        clock_num = ''.join(filter(str.isdigit, self._scan_clock_ctr))
        self.log.info(
            f'NIXSeriesCounter ready -- '
            f'device={self._device_name}  '
            f'scan CI={self._scan_counter_ch}  '
            f'scan CO={self._scan_clock_ctr}  '
            f'CO output=Ctr{clock_num}InternalOutput  '
            f'APD={apd}  gate={self._scan_trigger_term}  '
            f'scan channel="{self._scan_ch_name}"'
        )

    def on_deactivate(self):
        # Stop scan tasks before anything else
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
            'sample_rate) for the instreamer.')

    def _fc_configure(self, bin_width_s, record_length_s, number_of_gates=0):
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
        if self._status == self.STATUS_RUNNING:
            if ((self._photon_overflow and self._photon_overflow.is_set()) or
                    (self._gate_overflow   and self._gate_overflow.is_set())   or
                    (self._anchor_overflow and self._anchor_overflow.is_set())):
                self._status = self.STATUS_ERROR
        return self._status

    def start_measure(self):
        if self._status != self.STATUS_IDLE:
            raise RuntimeError(
                f'start_measure() called in invalid state {self._status}. '
                'Call configure() first, or stop_measure() if currently running.')

        # Fast counter has priority -- stop any active scan tasks first
        if self._scan_active or self._scan_task is not None:
            self.log.warning(
                'start_measure() called while scanner counter tasks are active. '
                'Stopping scanner counter first.')
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=False)

        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def stop_measure(self):
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
        if self._streaming:
            self._ni_start_tasks()

    def pause_measure(self):
        if self._status != self.STATUS_RUNNING:
            raise RuntimeError(
                f'pause_measure() called in invalid state {self._status}. '
                'Must be running.')
        self._stop_hardware_and_threads()
        if self._t_start_ref[0] > 0:
            self._elapsed_time_s += time.monotonic() - self._t_start_ref[0]
            self._t_start_ref[0] = 0.0
        self._status = self.STATUS_PAUSED
        if self._streaming:
            self._ni_start_tasks()

    def continue_measure(self):
        if self._status != self.STATUS_PAUSED:
            raise RuntimeError(
                f'continue_measure() called in invalid state {self._status}. '
                'Must be paused.')
        self._ni_stop_tasks()
        self._start_hardware_and_threads()
        self._status = self.STATUS_RUNNING

    def is_gated(self):
        return True

    def get_binwidth(self):
        if self._gate_ticks is None:
            return None
        return 1.0 / _TIMEBASE_HZ

    def get_data_trace(self):
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

    def _is_configure(self, active_channels, streaming_mode,
                      channel_buffer_size, sample_rate):
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

        fc_set = list(_FC_CHANNELS)
        extra  = [ch for ch in active_channels if ch not in fc_set]
        self._active_channels     = fc_set + extra
        self._streaming_mode      = streaming_mode
        self._sample_rate         = float(sample_rate)
        self._channel_buffer_size = int(channel_buffer_size)

    def start_stream(self) -> None:
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

            # Only start nidaqmx tasks when FC is not running AND no scan active
            if (self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED)
                    and not self._scan_active):
                self._ni_start_tasks()

            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
                name='instreamer-poll',
            )
            self._poll_thread.start()
            self._streaming = True

    def stop_stream(self) -> None:
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
        if not self._streaming:
            raise RuntimeError('Cannot read data -- stream is not running.')
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
        n_ch    = len(self._active_channels)
        to_read = min(self.available_samples, data_buffer.size // n_ch)
        if to_read == 0:
            return 0
        self.read_data_into_buffer(data_buffer, to_read, timestamp_buffer)
        return to_read

    def read_data(self, samples_per_channel=None):
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        n_ch = len(self._active_channels)
        buf  = np.empty(samples_per_channel * n_ch, dtype=np.float64)
        self.read_data_into_buffer(buf, samples_per_channel)
        return buf, None

    def read_single_point(self):
        if not self._streaming:
            raise RuntimeError('Cannot read data -- stream is not running.')
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
        with self._ni_tasks_lock:
            if self._ni_tasks_running:
                return
            if not self._digital_sources and not self._analog_sources:
                return

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
                    f'_ni_start_tasks: failed to start clock task: {e}. '
                    'Digital/analog instreamer channels unavailable.')
                self._ni_stop_tasks_unsafe()
                return

            # Determine reserved counters
            fc_active     = self._status in (self.STATUS_RUNNING,
                                             self.STATUS_PAUSED)
            reserved_ctrs = (set(_FC_COUNTERS) if fc_active else set()) | {_INSTREAM_CLK_CTR}

            # Read _scan_active as a plain boolean -- no lock needed.
            # This avoids deadlock when _ni_start_tasks is called from
            # _scan_cleanup_unsafe which may hold _scan_lock.
            if self._scan_active:
                reserved_ctrs |= {
                    self._scan_counter_ch.lower(),
                    self._scan_clock_ctr.lower(),
                }

            try:
                all_ctrs = tuple(
                    c.split('/')[-1]
                    for c in ni.system.Device(dev).co_physical_chans.channel_names
                    if 'ctr' in c.lower()
                )
            except Exception:
                all_ctrs = ()

            free_ctrs = [c for c in all_ctrs if c not in reserved_ctrs]

            # Digital counter tasks
            active_di = [ch for ch in self._digital_sources
                         if ch in self._active_channels]
            free_ctr_iter = iter(free_ctrs)
            for chnl in active_di:
                ctr = next(free_ctr_iter, None)
                if ctr is None:
                    self.log.warning(
                        f'_ni_start_tasks: no free counter for '
                        f'digital channel {chnl} -- outputs zeros.')
                    continue
                ctr_full  = f'/{dev}/{ctr}'
                chnl_full = f'/{dev}/{chnl}'
                task_name = f'NiUsb63xx_DI_{chnl}_{id(self):d}'
                try:
                    task = ni.Task(task_name)
                    task.ci_channels.add_ci_period_chan(
                        ctr_full,
                        min_val=0, max_val=100_000_000,
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
                        f'_ni_start_tasks: failed DI task {chnl} on {ctr}: {e}.')
                    try:
                        task.close()
                    except Exception:
                        pass

            # Analog input task
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
                        f'_ni_start_tasks: failed AI task: {e}.')
                    try:
                        ai_task.close()
                    except Exception:
                        pass

            # Start all tasks
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
        with self._ni_tasks_lock:
            self._ni_stop_tasks_unsafe()

    def _ni_stop_tasks_unsafe(self) -> None:
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
                n_ai_ch = len(self._analog_sources)
                _tmp_ai = np.empty(
                    self._channel_buffer_size * n_ai_ch, dtype=np.float64)
                n = self._ni_ai_reader.read_many_sample(
                    _tmp_ai,
                    number_of_samples_per_channel=ni.constants.READ_ALL_AVAILABLE,
                    timeout=0.0,
                )
                if n > 0:
                    result[n_di:] = (
                        _tmp_ai[:n * n_ai_ch].reshape(n, n_ai_ch).mean(axis=0))

        except Exception as e:
            self.log.warning(f'_ni_read_sample: read error: {e}')

        return result

    # ══════════════════════════════════════════════════════════════════════════
    #  Background poll thread
    # ══════════════════════════════════════════════════════════════════════════

    def _poll_loop(self) -> None:
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

            ni_sample = self._ni_read_sample()

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
        if self._default_rate_reader is None:
            return 0.0, 0.0
        return self._default_rate_reader()

    def register_rate_reader(self):
        state = {
            'last_time'        : 0.0,
            'last_photon_snap' : 0,
            'last_gated_snap'  : 0,
            'last_cycle_snap'  : 0,
            'last_valid_rates' : (0.0, 0.0),
        }
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
                return state['last_valid_rates']
            state['last_time']        = now
            state['last_photon_snap'] = cur_all
            state['last_gated_snap']  = cur_gated
            state['last_cycle_snap']  = cur_cycles
            rate_all_hz    = interval_all / dt
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
        self._default_rate_reader = self.register_rate_reader()

    def get_hardware_status(self):
        hw_ph   = self._get_hw_available(self._photon_task) if self._photon_task else -1
        hw_gate = self._get_hw_available(self._gate_task)   if self._gate_task   else -1
        with self._photon_lock:
            sw_ph_samples = sum(len(a) for a in self._photon_list)
            sw_ph_chunks  = len(self._photon_list)
        with self._gate_lock:
            sw_gate_samples = sum(len(a) for a in self._gate_list)
            sw_gate_chunks  = len(self._gate_list)
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
            print('No data -- device not configured.')
            return
        data, info = self.get_data_trace()
        cycles_done   = info['elapsed_sweeps']
        elapsed_total = info['elapsed_time']
        if cycles_done == 0:
            print('No complete cycles acquired yet.')
            return
        total_photons       = int(data.sum())
        total_gate_time_s   = (cycles_done
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
        print(f"\n{'--'*30}")
        print(f'  Cycles completed      : {cycles_done}')
        print(f'  Total gated photons   : {total_photons:,}')
        print(f'  Mean photons/cycle    : {total_photons / cycles_done:.1f}')
        print(f'  Mean photons/gate     : '
              f'{total_photons / (cycles_done * self._num_gates_per_cycle):.2f}')
        print(f'  Gate width            : {self._gate_width_s*1e6:.3f} us')
        print(f'  Dead time (inferred)  : {dead_time_ns:.1f} ns')
        print(f'  Duty cycle            : {duty_cycle_pct:.1f} %')
        print(f'  Total gate open time  : {total_gate_time_s*1e3:.3f} ms')
        print(f'  Count rate (gated)    : {count_rate_gated_hz/1e3:.2f} kHz')
        print(f'  Count rate (sequence) : {count_rate_seq_hz/1e3:.2f} kHz')
        print(f'  Histogram shape       : {self._accumulator.shape}  dtype=uint64')
        print(f"{'--'*30}")

    # ══════════════════════════════════════════════════════════════════════════
    #  Fast counter hardware and thread lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def _reset_run_state(self):
        if self._accumulator is not None:
            self._accumulator[:] = 0
        self._t_start_ref[0]   = 0.0
        self._elapsed_time_s   = 0.0
        with self._photon_count_lock:
            self._photon_count_ref[0]       = 0
            self._gated_photon_count_ref[0] = 0
        if self._nidaq is not None:
            self._init_default_rate_reader()
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

        self._check(self._nidaq.DAQmxStartTask(self._photon_task))
        self._check(self._nidaq.DAQmxStartTask(self._gate_task))
        self._check(self._nidaq.DAQmxStartTask(self._anchor_task))

        self._t_start_ref[0] = time.monotonic()

        self._anchor_thread.start()
        self._photon_thread.start()
        self._gate_thread.start()
        self._processor_thread.start()
        self._diag_thread.start()

    def _stop_hardware_and_threads(self):
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
    #  ctypes DAQmx wrappers
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _load_nidaq():
        if os.name == 'nt':
            return ctypes.windll.nicaiu
        return ctypes.cdll.LoadLibrary('libnidaqmx.so')

    def _declare_argtypes(self):
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
        if err != 0:
            buf = ctypes.create_string_buffer(2048)
            self._nidaq.DAQmxGetErrorString(err, buf, 2048)
            raise RuntimeError(f'DAQmx Error {err}: {buf.value.decode()}')

    def _get_hw_available(self, task_handle):
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
            ctypes.c_double(1.0),
            ctypes.c_double(float(2**32 - 1)),
            ctypes.c_int32(DAQmx_Val_Ticks), ctypes.c_int32(DAQmx_Val_Rising),
            ctypes.c_int32(DAQmx_Val_LowFreq1Ctr),
            ctypes.c_double(0.001),
            ctypes.c_uint32(1),
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
    #  Fast counter thread factories
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
                self.log.error(
                    f'[anchor] FATAL read error err={err}: {buf.value.decode()}')
                anchor_overflow.set()
                t1_abs_ready.set()
                return
            t1_abs_ref[0] = np.uint64(raw_buf[0])
            if diag_enabled:
                print(f'[anchor] t1_abs = {t1_abs_ref[0]} ticks', flush=True)
            t1_abs_ready.set()
            nidaq.DAQmxStopTask(anchor_task)
            nidaq.DAQmxClearTask(anchor_task)
            if diag_enabled:
                print('[anchor] ctr2 stopped and cleared.', flush=True)

        return threading.Thread(target=_run, daemon=True, name='anchor')

    def _make_reader_thread(self, task_handle, chunk_size, shared_list, lock,
                            stop_event, overflow_event, label):
        diag_enabled = self._diag_enabled
        raw_buf      = (ctypes.c_uint32 * chunk_size)()
        samps_read   = ctypes.c_int32(0)
        nidaq        = self._nidaq
        diag_ref     = (self._diag_reader_photons_ref if label == 'photon'
                        else self._diag_reader_gates_ref)
        diag_lock    = self._diag_lock
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
                    print('[photon reader] waiting for anchor t1_abs ...', flush=True)
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
                    self.log.error(
                        f'[reader-{label}] FATAL err={err}: {buf.value.decode()}')
                    overflow_event.set()
                    stop_event.set()
                    break
                if err > 0:
                    buf = ctypes.create_string_buffer(2048)
                    nidaq.DAQmxGetErrorString(err, buf, 2048)
                    self.log.warning(
                        f'[reader-{label}] warning={err}: {buf.value.decode()}')
                if n == 0:
                    continue

                if label == 'photon':
                    intervals = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                 .copy().astype(np.uint64))
                    if not period_state['t1_emitted']:
                        intervals = np.concatenate(
                            [np.array([0], dtype=np.uint64), intervals])
                        period_state['t1_emitted'] = True
                    absolute = period_state['abs_tick'] + np.cumsum(intervals)
                    period_state['abs_tick'] = absolute[-1]
                else:
                    counts64    = (np.frombuffer(raw_buf, dtype=np.uint32, count=n)
                                   .copy().astype(np.uint64))
                    offsets     = np.zeros(n, dtype=np.uint64)
                    n_new_wraps = np.uint64(0)
                    if rollover_state['last_abs'] > 0:
                        last_raw    = rollover_state['last_abs'] % np.uint64(2**32)
                        delta_first = np.int64(counts64[0]) - np.int64(last_raw)
                        if delta_first < 0:
                            offsets     += np.uint64(2**32)
                            n_new_wraps += np.uint64(1)
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
        photon_list            = self._photon_list
        gate_list              = self._gate_list
        photon_lock            = self._photon_lock
        gate_lock              = self._gate_lock
        accumulator            = self._accumulator
        stop_event             = self._processor_stop
        overflow_events        = [self._photon_overflow, self._gate_overflow,
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
            phase_aligned    = False
            phase_n          = num_gates_per_cycle

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
                leftover_gates = leftover_gates[phase_n - 1:]
                cutoff = leftover_gates[0]
                with photon_lock:
                    chunks = photon_list.copy(); photon_list.clear()
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
                with gate_lock:
                    gate_count = (sum(len(a) for a in gate_list)
                                  + len(leftover_gates))
                if gate_count < num_gates_per_cycle:
                    time.sleep(0.001); continue

                with photon_lock:
                    new_photon_chunks = photon_list.copy(); photon_list.clear()
                with gate_lock:
                    new_gate_chunks   = gate_list.copy();   gate_list.clear()

                if new_gate_chunks:
                    new_gates = np.concatenate(new_gate_chunks)
                    all_gates = (np.concatenate([leftover_gates, new_gates])
                                 if len(leftover_gates) else new_gates)
                else:
                    all_gates = leftover_gates

                n_complete = len(all_gates) // num_gates_per_cycle
                if n_complete == 0:
                    leftover_gates = all_gates
                    time.sleep(0.001); continue

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
                    late_chunks = photon_list.copy(); photon_list.clear()
                if late_chunks:
                    late = np.concatenate(late_chunks)
                    late.sort()
                    all_photons = (np.concatenate([all_photons, late])
                                   if len(all_photons) else late)

                ph_lo = np.searchsorted(all_photons, gate_rise_batch[0], side='left')
                ph_hi = np.searchsorted(all_photons, last_cycle_end, side='right')
                photons_batch  = all_photons[ph_lo:ph_hi]
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

                leftover_gates   = all_gates[n_gates_batch:]
                split            = np.searchsorted(all_photons, last_cycle_end,
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
                    sw_ph_samples = sum(len(a) for a in photon_list)
                    sw_ph_chunks  = len(photon_list)
                with gate_lock:
                    sw_gt_samples = sum(len(a) for a in gate_list)
                    sw_gt_chunks  = len(gate_list)
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
                W = 62
                print(f'\n+{"--"*31}+')
                print(f'|  DIAGNOSTICS  (dt={dt:.2f}s)' + ' '*(W-27) + '|')
                print(f'+{"--"*31}+')
                print(f'|  {"photons (reader)":20s}  {cur_rph:>12,d}  {d_rph/dt:>9.0f}/s  HW:{hw_ph:>6d}  |')
                print(f'|  {"gates   (reader)":20s}  {cur_rgt:>12,d}  {d_rgt/dt:>9.0f}/s  HW:{hw_gt:>6d}  |')
                print(f'+{"--"*31}+')
                print(f'|  {"ph consumed":20s}  {cur_pph:>12,d}  {d_pph/dt:>9.0f}/s' + ' '*14 + '|')
                print(f'|  {"ph histogrammed":20s}  {cur_hph:>12,d}  {d_hph/dt:>9.0f}/s  {gate_eff:>5.1f}%  |')
                print(f'|  {"cycles processed":20s}  {cur_pcy:>12,d}  {d_pcy/dt:>9.1f}/s' + ' '*14 + '|')
                print(f'|  {"cycles histgrmd":20s}  {cur_hcy:>12,d}  {d_hcy/dt:>9.1f}/s  {cycle_eff:>5.1f}%  |')
                print(f'+{"--"*31}+')
                print(f'|  {"leftover photons":20s}  {left_ph:>12,d}' + ' '*26 + '|')
                print(f'|  {"leftover gates":20s}  {left_gt:>12,d}' + ' '*26 + '|')
                print(f'+{"--"*31}+', flush=True)

        return threading.Thread(target=_run, daemon=True, name='diag')

    @staticmethod
    def _histogram_batch(photons_sorted, gate_rise_all, num_gates, n_bins, gate_ticks):
        gate_ticks_u64 = np.uint64(gate_ticks)
        hist = np.zeros((num_gates, n_bins), dtype=np.uint64)
        if len(photons_sorted) == 0:
            return hist
        gate_ends_all = gate_rise_all + gate_ticks_u64
        gate_idx = (np.searchsorted(gate_rise_all, photons_sorted, side='right')
                    .astype(np.int64) - 1)
        valid    = gate_idx >= 0
        gate_idx = gate_idx[valid]
        ph       = photons_sorted[valid]
        in_win   = ph < gate_ends_all[gate_idx]
        gate_idx = gate_idx[in_win]
        ph       = ph[in_win]
        if len(ph) == 0:
            return hist
        offset        = (ph - gate_rise_all[gate_idx]).astype(np.int64)
        gate_in_cycle = gate_idx % num_gates
        flat_idx      = gate_in_cycle * n_bins + offset
        counts = np.bincount(flat_idx, minlength=num_gates * n_bins)
        hist  += counts.reshape(num_gates, n_bins).astype(np.uint64)
        return hist

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning counter interface
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def channel_names(self) -> List[str]:
        """Channel names exposed to PIE710CounterInterfuse and Qudi confocal GUI."""
        return [self._scan_ch_name]

    @property
    def channel_units(self) -> dict:
        """Units per channel. Interfuse divides raw counts by t_pixel to get c/s."""
        return {self._scan_ch_name: 'c/s'}

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Stop instreamer tasks, then create and start CO + CI scan task pair.

        Must be called BEFORE sending the PI E-710 scan command.

        Priority rules:
            - Fails immediately if fast counter is running or paused.
            - Stops any stale scan tasks from a previous run.
            - Stops instreamer tasks to free counter resources.
              Instreamer state is saved and restored after read() or stop().

        CO task (scan_clock_counter):
            5000 Hz finite pulse train, n_pixels * n_steps + 1 pulses.
            Triggered by PI gate RISING edge on scan_trigger_terminal.

        CI task (scan_counter_channel):
            Counts APD photon rising edges (cumulative).
            Clocked by CO internal output (Ctr{N}InternalOutput).
            Finite: n_pixels * n_steps + 1 samples.

        Why n + 1 samples:
            raw[0]        = baseline count at gate HIGH instant
            np.diff(raw)  = n*n_pixels per-step increments (background-free)
        """
        with self._scan_lock:
            # Clean up any stale scan tasks
            if self._scan_task is not None or self._scan_co_task is not None:
                self.log.warning(
                    'arm() called while previous scan tasks are still active. '
                    'Cleaning up stale tasks first.')
                self._scan_cleanup_unsafe(restart_stream=False)

            # Fast counter has absolute priority
            if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                raise RuntimeError(
                    f'Cannot arm scanner counter while fast counter is '
                    f'running (status={self._status}). '
                    f'Call stop_measure() or pause_measure() first.')

            # Save instreamer state then stop it to free counter resources
            self._scan_was_streaming = self._ni_tasks_running
            self._ni_stop_tasks()

            n         = max(1, round(t_pixel * _PI_SAMP_RATE))
            n_collect = n * n_pixels + 1

            self._scan_n_steps  = n
            self._scan_n_pixels = n_pixels

            self.log.debug(
                f'arm  n_pixels={n_pixels}  '
                f't_pixel={t_pixel * 1e3:.3f} ms  '
                f'steps/pixel={n}  '
                f'n_collect={n_collect}  '
                f'gate terminal={self._scan_trigger_term}'
            )

            try:
                self._scan_create_tasks(n_collect)
            except ni.DaqError as exc:
                self._scan_cleanup_unsafe(restart_stream=True)
                raise RuntimeError(
                    f'NIXSeriesCounter.arm() failed: {exc}'
                ) from exc

    def read(self, n_pixels: int) -> Optional[dict]:
        """
        Wait for CO task to finish, read buffer, return per-pixel counts.

        Blocks until all n_pixels * n_steps + 1 CO pulses have been generated.
        Scan tasks are always cleaned up in the finally block, and the
        instreamer is restarted if it was running before arm() was called.

        np.diff(raw) subtracts the baseline (raw[0]) automatically, giving
        background-free per-step photon increments.

        @param n_pixels : must match value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} or None
        """
        with self._scan_lock:
            if (self._scan_task   is None or
                    self._scan_co_task is None or
                    self._scan_reader  is None):
                self.log.error('read() called but no scan tasks are active.')
                return None

            n         = self._scan_n_steps
            n_collect = n * n_pixels + 1

        try:
            # Block until CO has generated all n_collect clock pulses
            self._scan_co_task.wait_until_done(timeout=self._scan_rw_timeout)
            # CI is clocked by CO and finishes at the same time
            self._scan_task.wait_until_done(timeout=10.0)

            raw = np.zeros(n_collect, dtype=np.float64)
            self._scan_reader.read_many_sample_double(
                raw,
                number_of_samples_per_channel=n_collect,
                timeout=10.0,
            )

        except ni.DaqError as exc:
            self.log.error(
                f'NIXSeriesCounter.read() failed: {exc}\n'
                f'  Confirm BNC: PI Trigger OUT -> NI {self._scan_trigger_term}\n'
                f'  Gate must go HIGH for the full scan region duration.'
            )
            return None
        finally:
            # Always clean up scan tasks and restart instreamer if needed.
            # _scan_lock is an RLock so this is safe even though we already
            # hold it from the outer 'with' block above.
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=True)

        # Background-free per-pixel counts
        increments = np.diff(raw)
        counts     = increments.reshape(n_pixels, n).sum(axis=1)

        self.log.debug(
            f'read OK  n_pixels={n_pixels}  steps/pixel={n}  '
            f'total={int(counts.sum())}  '
            f'mean={counts.mean():.1f}  max={counts.max():.0f} cts/px'
        )

        return {self._scan_ch_name: counts}

    def stop(self) -> None:
        """
        Abort scan tasks immediately and restart instreamer if it was running.
        Called by PIE710CounterInterfuse on scan abort or emergency stop.
        Must never raise exceptions.
        """
        try:
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=True)
        except Exception as exc:
            self.log.warning(f'NIXSeriesCounter.stop() warning: {exc}')

    # ── Scan task helpers ──────────────────────────────────────────────────────

    def _scan_create_tasks(self, n_collect: int) -> None:
        """
        Create and start CO + CI scan task pair.
        Caller must hold _scan_lock.
        Raises ni.DaqError on failure -- caller handles it.
        """
        dev       = self._device_name
        apd_term  = self._scan_apd_term if self._scan_apd_term else self._photon_pfi_line
        clock_num = ''.join(filter(str.isdigit, self._scan_clock_ctr))
        co_output = f'/{dev}/Ctr{clock_num}InternalOutput'

        # CO task: finite 5 kHz pulse train, triggered by PI gate
        self._scan_co_task = ni.Task(f'ScanClock_{id(self):d}')
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
        # CO tasks support start triggers on all NI X-Series devices
        self._scan_co_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source = f'/{dev}/{self._scan_trigger_term}',
            trigger_edge   = ni.constants.Edge.RISING,
        )

        # CI task: count photons, clocked by CO internal output
        self._scan_task = ni.Task(f'APDScanCounter_{id(self):d}')
        self._scan_task.ci_channels.add_ci_count_edges_chan(
            f'/{dev}/{self._scan_counter_ch}',
            edge=ni.constants.Edge.RISING,
        )
        self._scan_task.ci_channels.all.ci_count_edges_term = (
            f'/{dev}/{apd_term}'
        )
        # Internal routing: always works between counter channels
        self._scan_task.timing.cfg_samp_clk_timing(
            rate           = _PI_SAMP_RATE,
            source         = co_output,
            active_edge    = ni.constants.Edge.RISING,
            sample_mode    = ni.constants.AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )

        self._scan_reader = CounterReader(self._scan_task.in_stream)
        self._scan_reader.verify_array_shape = False

        # CI starts first -- waits for CO to provide first clock edge
        self._scan_task.start()
        # CO starts -- waits for gate RISING edge on scan_trigger_terminal
        self._scan_co_task.start()

        # Mark as active AFTER both tasks are started.
        # _ni_start_tasks reads _scan_active as a plain bool (no lock) to
        # decide which counters to reserve -- this must be True by the time
        # any concurrent _ni_start_tasks call could run.
        self._scan_active = True

        self.log.debug(
            f'Scan tasks started: CO({self._scan_clock_ctr}) -> '
            f'CI({self._scan_counter_ch}) via {co_output}'
        )

    def _scan_cleanup_unsafe(self, restart_stream: bool = True) -> None:
        """
        Stop and close CO + CI scan tasks, then optionally restart instreamer.

        Caller must hold _scan_lock (which is an RLock -- reentrant is safe).

        Clears _scan_active BEFORE closing tasks so that any concurrent
        _ni_start_tasks call (which reads _scan_active without a lock) will
        see False and will not try to reserve our counters.
        """
        # Clear flag first so _ni_start_tasks sees the counters as free
        self._scan_active = False

        self._scan_reader = None

        for attr in ('_scan_task', '_scan_co_task'):
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
            # Only restart if fast counter is not holding the counters
            if self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                # _ni_start_tasks acquires _ni_tasks_lock (not _scan_lock)
                # and reads _scan_active as a plain bool -- no deadlock.
                self._ni_start_tasks()
        else:
            self._scan_was_streaming = False