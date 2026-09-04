# -*- coding: utf-8 -*-
"""
NI USB-63xx combined FastCounter + DataInStream + Scanner Counter interface.

Wiring: photon_pfi <- APD detector, gate_pfi <- gate/excitation pulse.

Counters: fast counter uses ctr0-2, instreamer clock uses ctr3, scanning
reuses ctr0-2 (mutually exclusive with the fast counter). Priority order:
fast counter > scanning > instreamer.

Scanning exposes three independent, mutually-exclusive acquisition trios,
selected by the caller (e.g. counter_trigger_mode). All three share
_scan_lock/_scan_active and can never run concurrently:

  'clock'             : arm(n_pixels, t_pixel) / read(n_pixels) / stop()
                        Assumes the scan trigger is a fixed-rate clock.

  'position_distance' : arm_position_trigger / read_position_trigger /
                        stop_position_trigger -- for scanners emitting one
                        real trigger edge per physical step. Both the APD
                        and the trigger line are counted (CI count-edges,
                        not DI -- PFI lines have no buffered DI hardware)
                        on a shared free-running sample clock. Real edges
                        are matched to expected pixel boundaries
                        sequentially, each anchored to the previous match
                        (tolerance = t_pixel * position_trigger_match_
                        tolerance_frac), so systematic drift never
                        accumulates. After settling, read_position_trigger
                        polls for up to position_trigger_read_poll_
                        timeout_s to avoid truncating a still-arriving
                        trace. Confirmed unreliable on real hardware below
                        roughly 0.5 um steps (multiple genuine edges per
                        intended pixel) -- use 'point_by_point' instead.

  'point_by_point'    : arm_point_scan / count_point(duration_s) /
                        disarm_point_scan -- no trigger at all: caller
                        moves+settles (blocking) then this module counts
                        for a fixed software-timed duration. No step-size
                        floor, slower per pixel.

Cross-counter sync: get_data_trace_up_to(max_cycles) returns the histogram
truncated to the nearest recorded checkpoint at or below max_cycles, for
combining several independently-clocked counters (see
NICounterStackInterfuse). Bounded by sync_max_lag_cycles.

Example config:
hardware:
  ni_combined:
    module.Class: 'ni_x_series.ni_x_series_counter.NIXSeriesCounter'
    options:
        device_name: 'Dev1'
        photon_pfi: 'PFI8'
        gate_pfi: 'PFI10'
        sample_rate: 10.0
        channel_buffer_size: 10000
        digital_sources: ['PFI8']
        adc_voltage_range: [-10, 10]
        read_write_timeout: 10
        sync_max_lag_cycles: 2000

        scan_counter_channel: 'ctr0'
        scan_clock_counter: 'ctr1'
        scan_trigger_terminal: 'PFI1'
        scan_trigger_counter_channel: 'ctr2'
        scan_apd_terminal: 'PFI8'
        scan_channel_name: 'APD1'
        
        # 'position_distance' mode only:
        position_trigger_sample_rate_hz: 100000.0
        position_trigger_max_total_time_s: 30.0
        position_trigger_read_settle_s: 0.1
        position_trigger_read_poll_timeout_s: 3.0
        position_trigger_match_tolerance_frac: 0.4
"""

import collections
import ctypes
import os
import threading
import time
from typing import List, Optional
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

# PI E-710 waveform generator rate -- must match PIE710Controller.SAMP_RATE.
# Only used by the clock-based arm()/read()/stop() trio.
_PI_SAMP_RATE: float = 5000.0


# ══════════════════════════════════════════════════════════════════════════════
#  Patched AnalogMultiChannelReader (works across nidaqmx-python versions)
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
    """Combined FastCounterInterface + DataInStreamInterface + scanning
    counter for NI USB-63xx. See module docstring for the full picture."""

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

    # ── Cross-counter synchronization ConfigOption ────────────────────────────
    _sync_max_lag_cycles = ConfigOption(
        'sync_max_lag_cycles', 2000, missing='nothing')

    # ── Position-distance trigger acquisition ConfigOptions ───────────────────
    # Used only by arm_position_trigger()/read_position_trigger()/
    # stop_position_trigger() -- clock-based arm()/read()/stop() is unaffected.
    _pt_trigger_counter_ch = ConfigOption(
        'scan_trigger_counter_channel', default='ctr2', missing='nothing')
    _pt_sample_rate_hz = ConfigOption(
        'position_trigger_sample_rate_hz', default=100000.0, missing='nothing')
    _pt_max_total_time_s = ConfigOption(
        'position_trigger_max_total_time_s', default=30.0, missing='nothing')
    _pt_read_settle_s = ConfigOption(
        'position_trigger_read_settle_s', default=0.1, missing='nothing')
    _pt_read_poll_timeout_s = ConfigOption(
        'position_trigger_read_poll_timeout_s', default=3.0, missing='nothing')
    _pt_match_tolerance_frac = ConfigOption(
        'position_trigger_match_tolerance_frac', default=0.4, missing='nothing')

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

        # ── Clock-based scan task state ───────────────────────────────────────
        self._scan_task          = None    # CI task (photon counting)
        self._scan_co_task       = None    # CO task (scan clock)
        self._scan_reader        = None    # CounterReader for CI task
        self._scan_n_steps       = 1       # PI waveform steps per pixel
        self._scan_n_pixels      = 0       # pixels per scan line
        self._scan_was_streaming = False   # instreamer state before arm()
        self._scan_active        = False   # True while ANY scan mode is running
        # RLock: read()'s finally block re-enters via _scan_cleanup_unsafe ->
        # _ni_start_tasks on the same thread. Shared by all 3 scan modes,
        # which are mutually exclusive and never run concurrently.
        self._scan_lock = threading.RLock()

        # ── Position-distance trigger acquisition state ──────────────────────
        self._pt_co_task       = None   # CO: free-running sample clock
        self._pt_ci_task       = None   # CI: cumulative APD edge count
        self._pt_trig_task     = None   # CI: cumulative TRIGGER edge count
        self._pt_n_pixels      = 0
        self._pt_t_pixel       = None   # cached for edge-matching in read
        self._pt_was_streaming = False
        self._pt_last_ci_raw   = None
        self._pt_last_trig_raw = None

        # ── Point-by-point (step-and-settle) acquisition state ───────────────
        self._point_task          = None  # software-timed CI count-edges task
        self._point_was_streaming = False

        # ── Cross-counter cycle checkpoint history ────────────────────────────
        self._checkpoint_lock             = threading.Lock()
        self._checkpoint_history          = []   # [(cycle_count, batch_hist)]
        self._checkpoint_base_accumulator = None
        self._checkpoint_base_cycles      = 0

    # ══════════════════════════════════════════════════════════════════════════
    #  Scan-mode helpers shared across all 3 scan trios
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def _effective_apd_terminal(self) -> str:
        """APD PFI terminal, falling back to photon_pfi if unset."""
        return self._scan_apd_term if self._scan_apd_term else self._photon_pfi_line

    @staticmethod
    def _ctr_num(ctr_name: str) -> str:
        """Digits of a counter name, e.g. 'ctr1' -> '1'."""
        return ''.join(filter(str.isdigit, ctr_name))

    def _scan_mode_table(self):
        """(is_active, cleanup_fn, label) for each mutually-exclusive scan mode."""
        return (
            (lambda: self._scan_task is not None or self._scan_co_task is not None,
             self._scan_cleanup_unsafe, 'clock-based scan'),
            (lambda: self._pt_ci_task is not None or self._pt_co_task is not None
                     or self._pt_trig_task is not None,
             self._pt_cleanup_unsafe, 'position-trigger scan'),
            (lambda: self._point_task is not None,
             self._point_cleanup_unsafe, 'point-by-point scan'),
        )

    def _stop_other_scan_modes(self, except_cleanup=None, restart_stream=False,
                               context=''):
        """Stop any active scan mode other than except_cleanup. Hold _scan_lock."""
        for is_active, cleanup, label in self._scan_mode_table():
            if cleanup is except_cleanup:
                continue
            if is_active():
                self.log.warning(
                    f'{context} called while {label} tasks are still active. '
                    f'Cleaning up stale tasks first.')
                cleanup(restart_stream=restart_stream)

    def _safe_stop_close(self, attr_name: str) -> None:
        """Stop+close the task stored in attr_name (if any), then clear it."""
        task = getattr(self, attr_name, None)
        if task is None:
            return
        try:
            if not task.is_task_done():
                task.stop()
            task.close()
        except ni.DaqError as exc:
            self.log.warning(f'Task cleanup ({attr_name}): {exc}')
        finally:
            setattr(self, attr_name, None)

    def _maybe_restart_instreamer(self, was_streaming_attr: str,
                                  restart_stream: bool) -> None:
        """Restart instreamer if it was running before this mode's arm(), unless FC is active."""
        was_streaming = getattr(self, was_streaming_attr)
        setattr(self, was_streaming_attr, False)
        if (restart_stream and was_streaming
                and self._status not in (self.STATUS_RUNNING, self.STATUS_PAUSED)):
            self._ni_start_tasks()

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
        apd = self._effective_apd_terminal
        clock_num = self._ctr_num(self._scan_clock_ctr)
        self.log.info(
            f'NIXSeriesCounter ready -- '
            f'device={self._device_name}  '
            f'scan CI={self._scan_counter_ch}  '
            f'scan CO={self._scan_clock_ctr}  '
            f'CO output=Ctr{clock_num}InternalOutput  '
            f'APD={apd}  gate={self._scan_trigger_term}  '
            f'scan channel="{self._scan_ch_name}"  '
            f'sync_max_lag_cycles={self._sync_max_lag_cycles}  '
            f'position-trigger counter={self._pt_trigger_counter_ch}  '
            f'position-trigger match tolerance='
            f'{self._pt_match_tolerance_frac} * t_pixel (per-step, sequential)  '
            f'read poll timeout={self._pt_read_poll_timeout_s} s  '
            f'point-by-point mode available (reuses scan_counter_channel)'
        )

    def on_deactivate(self):
        # Stop any active scan mode before anything else.
        for is_active, cleanup, label in self._scan_mode_table():
            if is_active():
                try:
                    with self._scan_lock:
                        cleanup(restart_stream=False)
                except Exception as e:
                    self.log.warning(f'on_deactivate: {label} cleanup warning: {e}')

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
        """Dispatches to fast-counter or instreamer configuration, based
        on which arguments are given."""
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

        # Checkpoint base accumulator must always match the main one's shape.
        if (self._checkpoint_base_accumulator is None
                or self._checkpoint_base_accumulator.shape != (num_gates, gate_ticks)):
            self._checkpoint_base_accumulator = np.zeros(
                (num_gates, gate_ticks), dtype=np.uint64)

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

        # Fast counter has priority over any active scan mode.
        with self._scan_lock:
            self._stop_other_scan_modes(restart_stream=False,
                                        context='start_measure()')

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
        """Full cumulative histogram over every cycle processed so far.

        For a cycle-aligned truncated view (combining with another
        counter), see get_data_trace_up_to().
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

    def get_data_trace_up_to(self, max_cycles):
        """Histogram truncated to at most max_cycles, rounded down to the
        nearest recorded checkpoint. Batches once merged into the main
        accumulator can't be un-merged, hence the rounding and the bounded
        checkpoint history (see module docstring).

        @param int max_cycles: cycle count to truncate to
        @return (np.ndarray, dict): same shape as get_data_trace(), with
                                    'elapsed_sweeps' <= max_cycles
        """
        if self._accumulator is None:
            return (np.zeros((1, 1), dtype=np.int64),
                    {'elapsed_sweeps': 0, 'elapsed_time': 0.0})

        with self._checkpoint_lock:
            total_cycles = self._diag_hist_cycles_ref[0]
            if max_cycles >= total_cycles:
                served_cycles = total_cycles
                result = self._accumulator.copy()
            else:
                served_cycles = self._checkpoint_base_cycles
                result = self._checkpoint_base_accumulator.copy()
                # Stored oldest-first and strictly increasing -> safe to
                # stop at the first entry exceeding max_cycles.
                for cyc, hist in self._checkpoint_history:
                    if cyc <= max_cycles:
                        result += hist
                        served_cycles = cyc
                    else:
                        break

        elapsed = self._elapsed_time_s
        if self._status == self.STATUS_RUNNING and self._t_start_ref[0] > 0:
            elapsed += time.monotonic() - self._t_start_ref[0]

        return result.astype(np.int64), {
            'elapsed_sweeps': served_cycles, 'elapsed_time': elapsed
        }

    def _record_checkpoint(self, cycle_count, batch_hist):
        """Records one processor-batch increment tagged with the cumulative
        cycle count it brings the counter to, folding old entries into a
        permanent base once sync_max_lag_cycles is exceeded (bounded memory).
        """
        with self._checkpoint_lock:
            self._checkpoint_history.append((cycle_count, batch_hist.copy()))
            while (len(self._checkpoint_history) > 1 and
                   cycle_count - self._checkpoint_history[0][0] > self._sync_max_lag_cycles):
                old_cycle, old_hist = self._checkpoint_history.pop(0)
                self._checkpoint_base_accumulator += old_hist
                self._checkpoint_base_cycles = old_cycle

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

            # _scan_active read as a plain bool (no lock) to avoid deadlock
            # when called from a scan mode's own cleanup while holding
            # _scan_lock. Any active scan mode reserves its own counters.
            if self._scan_active:
                reserved_ctrs |= {
                    self._scan_counter_ch.lower(),
                    self._scan_clock_ctr.lower(),
                    self._pt_trigger_counter_ch.lower(),
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
        """Returns a closure computing (rate_all_hz, rate_gated_hz) since
        its last call, from the shared counters/refs (thread-safe)."""
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
        # Reset cross-counter checkpoint state alongside everything else.
        with self._checkpoint_lock:
            self._checkpoint_history = []
            if self._checkpoint_base_accumulator is not None:
                self._checkpoint_base_accumulator[:] = 0
            self._checkpoint_base_cycles = 0

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
        self._check(self._nidaq.DAQmxSetDigEdgeArmStartTrigEdge(
            h, DAQmx_Val_Rising))
        return h

    # ══════════════════════════════════════════════════════════════════════════
    #  Fast counter thread factories
    # ══════════════════════════════════════════════════════════════════════════

    def _make_anchor_reader_thread(self):
        """Reads a single ctr2 sample at t=0 as the absolute-time anchor
        for the photon-period-to-absolute-tick conversion below."""
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
        """Polls task_handle for new samples, converts to absolute ticks
        (handling U32 counter rollover for gates, period-to-tick anchoring
        for photons), and appends to shared_list."""
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
        """Aligns photon/gate streams into complete cycles, histograms
        each batch into per-gate bins, and merges into the accumulator."""
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
        record_checkpoint      = self._record_checkpoint

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
                    cumulative_cycles_now = diag_hist_cycles_ref[0]

                # Record this batch's pre-merge histogram, tagged with the
                # cumulative cycle count -- the only point per-batch
                # resolution still exists (see get_data_trace_up_to()).
                record_checkpoint(cumulative_cycles_now, batch_hist)

                leftover_gates   = all_gates[n_gates_batch:]
                split            = np.searchsorted(all_photons, last_cycle_end,
                                                   side='right')
                leftover_photons = all_photons[split:]

                with diag_lock:
                    diag_leftover_ph_ref[0] = len(leftover_photons)
                    diag_leftover_gt_ref[0] = len(leftover_gates)

        return threading.Thread(target=_run, daemon=True, name='processor')

    def _make_diag_thread(self):
        """Periodically prints reader/processor throughput and efficiency
        stats, if diag_enabled."""
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
        """Bins photons_sorted into (gate_in_cycle, offset_from_gate_rise)."""
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
    #  Scanning counter interface -- clock-based trigger mode
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
        """Stop instreamer, create+start CO+CI scan task pair. Call BEFORE
        sending the PI E-710 scan command.

        CO: finite 5 kHz pulse train, n_pixels*n_steps+1 pulses, triggered
        by the gate's rising edge. CI: counts APD edges, clocked by CO,
        same sample count. raw[0] is a baseline; np.diff(raw) in read()
        gives background-free per-step increments.

        Assumes the trigger is a fixed-rate clock -- use
        arm_position_trigger() instead if that assumption doesn't hold.
        """
        with self._scan_lock:
            self._stop_other_scan_modes(except_cleanup=self._scan_cleanup_unsafe,
                                        restart_stream=False, context='arm()')

            if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                raise RuntimeError(
                    f'Cannot arm scanner counter while fast counter is '
                    f'running (status={self._status}). '
                    f'Call stop_measure() or pause_measure() first.')

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
        """Block until CO finishes, read the buffer, return per-pixel
        counts. Always cleans up scan tasks and restarts the instreamer
        if it was running before arm().

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
            # Block until CO has generated all n_collect clock pulses;
            # CI is clocked by CO and finishes at the same time.
            self._scan_co_task.wait_until_done(timeout=self._scan_rw_timeout)
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
            # _scan_lock is an RLock -- safe to re-enter here.
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=True)

        # raw[0] is baseline; diff() removes it, giving per-step increments.
        increments = np.diff(raw)
        counts     = increments.reshape(n_pixels, n).sum(axis=1)

        self.log.debug(
            f'read OK  n_pixels={n_pixels}  steps/pixel={n}  '
            f'total={int(counts.sum())}  '
            f'mean={counts.mean():.1f}  max={counts.max():.0f} cts/px'
        )

        return {self._scan_ch_name: counts}

    def stop(self) -> None:
        """Abort scan tasks and restart instreamer if it was running.
        Never raises -- safe to call from abort/emergency-stop paths."""
        try:
            with self._scan_lock:
                self._scan_cleanup_unsafe(restart_stream=True)
        except Exception as exc:
            self.log.warning(f'NIXSeriesCounter.stop() warning: {exc}')

    # ── Scan task helpers (clock-based mode) ──────────────────────────────────

    def _scan_create_tasks(self, n_collect: int) -> None:
        """Create+start CO+CI scan task pair. Caller holds _scan_lock.
        Raises ni.DaqError on failure -- caller handles it."""
        dev       = self._device_name
        apd_term  = self._effective_apd_terminal
        clock_num = self._ctr_num(self._scan_clock_ctr)
        co_output = f'/{dev}/Ctr{clock_num}InternalOutput'

        # id(self) in task names keeps them unique across the process, in
        # case several NIXSeriesCounter instances arm() concurrently.
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
        self._scan_co_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source = f'/{dev}/{self._scan_trigger_term}',
            trigger_edge   = ni.constants.Edge.RISING,
        )

        self._scan_task = ni.Task(f'APDScanCounter_{id(self):d}')
        self._scan_task.ci_channels.add_ci_count_edges_chan(
            f'/{dev}/{self._scan_counter_ch}',
            edge=ni.constants.Edge.RISING,
        )
        self._scan_task.ci_channels.all.ci_count_edges_term = (
            f'/{dev}/{apd_term}'
        )
        self._scan_task.timing.cfg_samp_clk_timing(
            rate           = _PI_SAMP_RATE,
            source         = co_output,
            active_edge    = ni.constants.Edge.RISING,
            sample_mode    = ni.constants.AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )

        self._scan_reader = CounterReader(self._scan_task.in_stream)
        self._scan_reader.verify_array_shape = False

        # CI starts first (waits for CO's first clock edge), then CO
        # (waits for the gate's rising edge).
        self._scan_task.start()
        self._scan_co_task.start()

        # Set AFTER both tasks start -- _ni_start_tasks reads this bool
        # without a lock to decide which counters are free.
        self._scan_active = True

        self.log.debug(
            f'Scan tasks started: CO({self._scan_clock_ctr}) -> '
            f'CI({self._scan_counter_ch}) via {co_output}'
        )

    def _scan_cleanup_unsafe(self, restart_stream: bool = True) -> None:
        """Stop+close CO+CI scan tasks, then optionally restart the
        instreamer. Caller holds _scan_lock (RLock -- reentrant-safe)."""
        self._scan_active = False
        self._scan_reader = None
        for attr in ('_scan_task', '_scan_co_task'):
            self._safe_stop_close(attr)
        self._maybe_restart_instreamer('_scan_was_streaming', restart_stream)

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning counter interface -- position-distance trigger mode
    # ══════════════════════════════════════════════════════════════════════════

    def arm_position_trigger(self, n_pixels: int, t_pixel: float) -> None:
        """Start continuous counting of the APD and the raw trigger line,
        both clocked by one shared free-running CO. Call BEFORE firing
        the scan. Caches t_pixel for read_position_trigger()'s edge
        matching. See module docstring for the full algorithm.
        """
        with self._scan_lock:
            self._stop_other_scan_modes(except_cleanup=self._pt_cleanup_unsafe,
                                        restart_stream=False,
                                        context='arm_position_trigger()')

            if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                raise RuntimeError(
                    f'Cannot arm scanner counter while fast counter is '
                    f'running (status={self._status}). '
                    f'Call stop_measure() or pause_measure() first.')

            self._pt_was_streaming = self._ni_tasks_running
            self._ni_stop_tasks()

            self._pt_n_pixels = int(n_pixels)
            self._pt_t_pixel  = float(t_pixel)
            self._scan_active = True

            dev       = self._device_name
            apd_term  = self._effective_apd_terminal
            trig_term = self._scan_trigger_term
            clock_ctr = self._scan_clock_ctr
            count_ctr = self._scan_counter_ch
            trig_ctr  = self._pt_trigger_counter_ch
            fs        = float(self._pt_sample_rate_hz)
            n_buf     = max(2, int(round(fs * self._pt_max_total_time_s)))

            self.log.debug(
                f'arm_position_trigger  n_pixels={n_pixels}  '
                f't_pixel={t_pixel * 1e3:.3f} ms  fs={fs:.0f} Hz  '
                f'buffer={n_buf} samples  '
                f'trigger counter={trig_ctr} on {trig_term}  APD={apd_term}'
            )

            co_task = ci_task = trig_task = None
            try:
                # CO: free-running clock, starts immediately -- covers the
                # move+settle preamble as well as the actual scan line.
                co_task = ni.Task(f'PosTrigClock_{id(self):d}')
                co_task.co_channels.add_co_pulse_chan_freq(
                    counter    = f'/{dev}/{clock_ctr}',
                    freq       = fs,
                    duty_cycle = 0.5,
                    idle_state = ni.constants.Level.LOW,
                )
                co_task.timing.cfg_implicit_timing(
                    sample_mode = ni.constants.AcquisitionType.CONTINUOUS,
                )
                co_output = f'/{co_task.channel_names[0]}InternalOutput'

                ci_task = ni.Task(f'PosTrigCI_{id(self):d}')
                ci_task.ci_channels.add_ci_count_edges_chan(
                    f'/{dev}/{count_ctr}', edge=ni.constants.Edge.RISING,
                )
                ci_task.ci_channels.all.ci_count_edges_term = f'/{dev}/{apd_term}'
                ci_task.timing.cfg_samp_clk_timing(
                    rate           = fs,
                    source         = co_output,
                    active_edge    = ni.constants.Edge.RISING,
                    sample_mode    = ni.constants.AcquisitionType.CONTINUOUS,
                    samps_per_chan = n_buf,
                )

                # Trigger line counted via CI (not DI -- PFI lines lack
                # buffered DI hardware), on the same PFI pin used by the
                # clock-based mode's start trigger, clocked by the same CO.
                trig_task = ni.Task(f'PosTrigCount_{id(self):d}')
                trig_task.ci_channels.add_ci_count_edges_chan(
                    f'/{dev}/{trig_ctr}', edge=ni.constants.Edge.RISING,
                )
                trig_task.ci_channels.all.ci_count_edges_term = f'/{dev}/{trig_term}'
                trig_task.timing.cfg_samp_clk_timing(
                    rate           = fs,
                    source         = co_output,
                    active_edge    = ni.constants.Edge.RISING,
                    sample_mode    = ni.constants.AcquisitionType.CONTINUOUS,
                    samps_per_chan = n_buf,
                )

                # Start clocked (slave) tasks before the clock (master).
                trig_task.start()
                ci_task.start()
                co_task.start()

                self._pt_co_task   = co_task
                self._pt_ci_task   = ci_task
                self._pt_trig_task = trig_task

            except ni.DaqError as exc:
                for t in (trig_task, ci_task, co_task):
                    if t is not None:
                        try:
                            t.close()
                        except Exception:
                            pass
                self._pt_co_task = self._pt_ci_task = self._pt_trig_task = None
                self._pt_cleanup_unsafe(restart_stream=True)
                raise RuntimeError(
                    f'NIXSeriesCounter.arm_position_trigger() failed: {exc}'
                ) from exc

    def read_position_trigger(self, n_pixels: int) -> Optional[dict]:
        """Stop counting, pull the buffered cumulative APD/trigger traces,
        and return per-pixel counts.

        Polls after settling until n_pixels+1 real trigger edges are seen
        (or the poll timeout elapses) to avoid truncating a still-arriving
        trace, then matches real edges to expected pixel boundaries
        sequentially (each anchored to the previous match) -- see module
        docstring for the full rationale and the real-hardware resolution
        limit of this mode.

        @param n_pixels : must match the value passed to arm_position_trigger()
        @return         : {channel_name: np.ndarray(n_pixels,)} or None
        """
        with self._scan_lock:
            if (self._pt_ci_task is None or self._pt_trig_task is None
                    or self._pt_co_task is None):
                self.log.error(
                    'read_position_trigger() called but no position-'
                    'trigger tasks are active.')
                return None
            if int(n_pixels) != self._pt_n_pixels:
                self.log.warning(
                    f'read_position_trigger(n_pixels={n_pixels}) does not '
                    f'match arm_position_trigger(n_pixels={self._pt_n_pixels})'
                    f' -- using the value passed to read_position_trigger().')
            ci_task   = self._pt_ci_task
            trig_task = self._pt_trig_task
            t_pixel   = self._pt_t_pixel
            fs        = float(self._pt_sample_rate_hz)

        expected = n_pixels + 1

        try:
            # Caller has already confirmed motion is done; this is just a
            # cheap first wait before polling.
            time.sleep(max(0.0, self._pt_read_settle_s))

            ci_chunks: List[np.ndarray]   = []
            trig_chunks: List[np.ndarray] = []
            total_edges = 0
            poll_deadline = time.monotonic() + max(0.0, self._pt_read_poll_timeout_s)

            while True:
                n_ci   = ci_task.in_stream.avail_samp_per_chan
                n_trig = trig_task.in_stream.avail_samp_per_chan
                n_avail = min(n_ci, n_trig)

                if n_avail > 0:
                    ci_chunk = np.asarray(
                        ci_task.read(number_of_samples_per_channel=n_avail,
                                     timeout=10.0),
                        dtype=np.int64,
                    )
                    trig_chunk = np.asarray(
                        trig_task.read(number_of_samples_per_channel=n_avail,
                                       timeout=10.0),
                        dtype=np.int64,
                    )
                    ci_chunks.append(ci_chunk)
                    trig_chunks.append(trig_chunk)
                    total_edges = int(trig_chunk[-1]) if len(trig_chunk) else total_edges

                if total_edges >= expected:
                    break
                if time.monotonic() > poll_deadline:
                    break
                time.sleep(0.01)

            if not ci_chunks:
                raise RuntimeError(
                    f'no samples available at all -- expected many more '
                    f'for a real scan line. Check DAQ wiring / clock '
                    f'routing.'
                )

            ci_raw   = np.concatenate(ci_chunks)
            trig_raw = np.concatenate(trig_chunks)

        except ni.DaqError as exc:
            apd_term = self._effective_apd_terminal
            self.log.error(
                f'NIXSeriesCounter.read_position_trigger() failed: {exc}\n'
                f'  Confirm BNC: trigger OUT -> NI {self._scan_trigger_term}, '
                f'and -> NI {apd_term} for APD.'
            )
            return None
        finally:
            with self._scan_lock:
                self._pt_cleanup_unsafe(restart_stream=True)

        self._pt_last_ci_raw   = ci_raw
        self._pt_last_trig_raw = trig_raw

        # Every place the cumulative trigger count rose -- a real edge.
        all_edges = np.where(np.diff(trig_raw) > 0)[0] + 1
        if len(all_edges) == 0:
            raise RuntimeError(
                'NIXSeriesCounter.read_position_trigger(): hardware '
                'detected ZERO real trigger edges over the acquisition '
                'window -- check BNC wiring / scan_trigger_terminal, or '
                'that the scanner actually fired a line.'
            )

        step_samples = t_pixel * fs
        tol_samples  = self._pt_match_tolerance_frac * step_samples

        # Sequential matching: each expected position anchored to the
        # PREVIOUS real match, not a fixed grid from edge 0 (see module
        # docstring, "EXPECTATION-BASED EDGE MATCHING").
        matched_indices = np.empty(expected, dtype=np.int64)
        matched_indices[0] = all_edges[0]
        search_from = 1

        for k in range(1, expected):
            if search_from >= len(all_edges):
                raise RuntimeError(
                    f'NIXSeriesCounter.read_position_trigger(): ran out of '
                    f'real trigger edges while matching pixel boundary '
                    f'{k}/{expected - 1} -- found {len(all_edges)} real '
                    f'edges total, expected {expected}. Consider '
                    f'counter_trigger_mode="point_by_point" for finer '
                    f'steps. Raw traces: _pt_last_ci_raw / _pt_last_trig_raw.'
                )

            target = matched_indices[k - 1] + step_samples
            sub = all_edges[search_from:]
            pos = np.searchsorted(sub, target)

            candidates = []
            if pos < len(sub):
                candidates.append(pos)
            if pos > 0:
                candidates.append(pos - 1)

            best_local = min(candidates, key=lambda i: abs(sub[i] - target))
            diff = abs(sub[best_local] - target)

            if diff > tol_samples:
                raise RuntimeError(
                    f'NIXSeriesCounter.read_position_trigger(): no real '
                    f'trigger edge found within tolerance '
                    f'({tol_samples:.1f} samples = '
                    f'{tol_samples / fs * 1e3:.3f} ms) of expected pixel '
                    f'boundary {k}/{expected - 1}, measured from the '
                    f'PREVIOUS real match (expected sample index '
                    f'{target:.1f}). Found {len(all_edges)} real edges '
                    f'total, expected {expected}. Consider '
                    f'counter_trigger_mode="point_by_point" for finer '
                    f'steps. Raw traces: _pt_last_ci_raw / _pt_last_trig_raw.'
                )

            matched_indices[k] = sub[best_local]
            # Any unselected edges before this one are discarded as spurious.
            search_from = search_from + best_local + 1

        n_discarded = len(all_edges) - expected
        if n_discarded > 0:
            self.log.debug(
                f'read_position_trigger: matched {expected} real edges '
                f'to expected pixel boundaries, discarded {n_discarded} '
                f'extra real edge(s) as spurious (sequential nearest-'
                f'match selection).'
            )

        counts_at_edges  = ci_raw[matched_indices]
        counts_per_pixel = np.diff(counts_at_edges).astype(np.float64)

        self.log.debug(
            f'read_position_trigger OK  n_pixels={n_pixels}  '
            f'real_edges_seen={len(all_edges)}  matched={expected}  '
            f'total={int(counts_per_pixel.sum())}  '
            f'mean={counts_per_pixel.mean():.1f}  '
            f'max={counts_per_pixel.max():.0f} cts/px'
        )

        return {self._scan_ch_name: counts_per_pixel}

    def stop_position_trigger(self) -> None:
        """Abort position-trigger tasks, restart instreamer if it was
        running. Never raises."""
        try:
            with self._scan_lock:
                self._pt_cleanup_unsafe(restart_stream=True)
        except Exception as exc:
            self.log.warning(
                f'NIXSeriesCounter.stop_position_trigger() warning: {exc}')

    # ── Scan task helpers (position-distance trigger mode) ────────────────────

    def _pt_cleanup_unsafe(self, restart_stream: bool = True) -> None:
        """Stop+close CO+CI+trigger-count tasks, then optionally restart
        the instreamer. Caller holds _scan_lock (RLock -- reentrant-safe)."""
        self._scan_active = False
        for attr in ('_pt_trig_task', '_pt_ci_task', '_pt_co_task'):
            self._safe_stop_close(attr)
        self._maybe_restart_instreamer('_pt_was_streaming', restart_stream)

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning counter interface -- point-by-point (step-and-settle) mode
    # ══════════════════════════════════════════════════════════════════════════

    def arm_point_scan(self) -> None:
        """Stop instreamer, create (but don't start) one software-timed
        CI count-edges task on scan_counter_channel/scan_apd_terminal --
        reused across many start/stop cycles in count_point() (mutually
        exclusive with the other two modes via _scan_active).

        Call ONCE before a sequence of count_point() calls, not once per
        pixel.
        """
        with self._scan_lock:
            self._stop_other_scan_modes(except_cleanup=self._point_cleanup_unsafe,
                                        restart_stream=False,
                                        context='arm_point_scan()')

            if self._status in (self.STATUS_RUNNING, self.STATUS_PAUSED):
                raise RuntimeError(
                    f'Cannot arm scanner counter while fast counter is '
                    f'running (status={self._status}). '
                    f'Call stop_measure() or pause_measure() first.')

            self._point_was_streaming = self._ni_tasks_running
            self._ni_stop_tasks()
            self._scan_active = True

            dev       = self._device_name
            apd_term  = self._effective_apd_terminal
            count_ctr = self._scan_counter_ch

            self.log.debug(
                f'arm_point_scan  counter={count_ctr}  APD={apd_term}'
            )

            try:
                task = ni.Task(f'PointScanCounter_{id(self):d}')
                task.ci_channels.add_ci_count_edges_chan(
                    f'/{dev}/{count_ctr}', edge=ni.constants.Edge.RISING,
                )
                task.ci_channels.all.ci_count_edges_term = f'/{dev}/{apd_term}'
                self._point_task = task
            except ni.DaqError as exc:
                self._point_cleanup_unsafe(restart_stream=True)
                raise RuntimeError(
                    f'NIXSeriesCounter.arm_point_scan() failed: {exc}'
                ) from exc

    def count_point(self, duration_s: float) -> float:
        """Count real APD edges for exactly duration_s: task.start()
        resets the count, sleep, read, task.stop() -- each cycle is
        independent, so repeated calls return non-overlapping per-point
        counts. Caller must already have moved+settled at the pixel.

        @param duration_s : real time to count for, in seconds
        @return           : total real APD edges counted, as a float
        """
        with self._scan_lock:
            if self._point_task is None:
                raise RuntimeError(
                    'count_point() called without a prior '
                    'arm_point_scan() call.')
            task = self._point_task

        task.start()
        try:
            time.sleep(max(0.0, duration_s))
            count = task.read()
        finally:
            try:
                task.stop()
            except ni.DaqError as exc:
                self.log.warning(f'count_point(): task.stop() warning: {exc}')

        return float(count)

    def disarm_point_scan(self) -> None:
        """Close the point-by-point task, restart instreamer if it was
        running before arm_point_scan(). Never raises."""
        try:
            with self._scan_lock:
                self._point_cleanup_unsafe(restart_stream=True)
        except Exception as exc:
            self.log.warning(
                f'NIXSeriesCounter.disarm_point_scan() warning: {exc}')

    # ── Scan task helpers (point-by-point mode) ────────────────────────────────

    def _point_cleanup_unsafe(self, restart_stream: bool = True) -> None:
        """Stop+close the point-by-point task, then optionally restart
        the instreamer. Caller holds _scan_lock (RLock -- reentrant-safe)."""
        self._scan_active = False
        self._safe_stop_close('_point_task')
        self._maybe_restart_instreamer('_point_was_streaming', restart_stream)