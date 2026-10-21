# -*- coding: utf-8 -*-
"""
Qudi FastCounterInterface implementation for PicoQuant TCSPC / event-timer devices
(MultiHarp 150/160, HydraHarp 400/500, PicoHarp 330, TimeHarp 260) using snAPI.

The device is run in T2 mode and the raw timetag stream is read through snAPI's
"Unfold" interface. Every trigger event starts a new time histogram:

* ungated  -> all triggers accumulate into a single histogram row and
              ``get_data_trace()`` returns a 1D arrival-time histogram
              (fluorescence lifetime / decay curve).
* gated    -> successive triggers are assigned cyclically to gate rows and
              ``get_data_trace()`` returns a 2D array ``[gate_index, timebin]``
              (Rabi / Ramsey / Hahn echo / CPMG sweeps).

snAPI channel numbering used here:
    0       sync input
    1..N    hardware input channels 1..N

A trigger can either be a normal snAPI channel or one of the four marker inputs.
Photon events from all configured photon channels are summed.

This module is based on the reference implementation provided by PicoQuant.

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

from __future__ import annotations

import threading
import time
from typing import Optional, Sequence, Tuple

import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface

# snAPI is a Windows-only PicoQuant package ('pip install snAPI') that additionally requires the
# device driver of the connected harp to be installed. It is imported lazily so that this file can
# be imported (and its binning logic unit-tested) on machines without the hardware present.
try:
    from snAPI.Constants import MeasMode as _MeasMode
    from snAPI.Constants import RefSource as _RefSource
    from snAPI.Main import snAPI as _snAPI
except ImportError:  # pragma: no cover - depends on the local installation
    _MeasMode = None
    _RefSource = None
    _snAPI = None

# Maps the 'ref_source' config option (a plain string, so it is checkable without snAPI installed)
# to the snAPI enum. Only the two clock sources this lab actually uses are exposed; extend as needed.
_REF_SOURCE_BY_NAME = {
    'internal': _RefSource.Internal if _RefSource is not None else None,
    'external_10mhz': _RefSource.External_10MHZ if _RefSource is not None else None,
}

# Bit set in the snAPI unfold channel number to flag a marker event.
_MARKER_FLAG = np.uint8(0x80)

# snAPI wraps a process-global C API: constructing a second snAPI() while another one is still
# alive aborts the whole process instead of raising. Track the active instance so a misconfigured
# qudi setup gets a readable error rather than taking the application down.
_active_instance_lock = threading.Lock()
_active_instance = None


class _GateAccumulator:
    """ Bins snAPI T2 unfold timetags into a (rows x timebins) histogram.

    Photons are histogrammed relative to the most recent preceding trigger. Triggers are assigned
    cyclically to the histogram rows, so a single row yields the summed arrival-time histogram
    (ungated operation) and N rows yield one row per gate of an N-gate pulse sequence.

    This class deliberately holds no snAPI or qudi state so that the binning can be verified
    without hardware.
    """

    def __init__(self, number_of_rows: int, number_of_bins: int, bin_width_ps: int):
        if int(number_of_rows) < 1:
            raise ValueError('number_of_rows must be at least 1.')
        if int(number_of_bins) < 1:
            raise ValueError('number_of_bins must be at least 1.')
        if int(bin_width_ps) < 1:
            raise ValueError('bin_width_ps must be at least 1.')

        self.number_of_rows = int(number_of_rows)
        self.number_of_bins = int(number_of_bins)
        self.bin_width_ps = int(bin_width_ps)
        self.record_length_ps = self.number_of_bins * self.bin_width_ps

        self.counts = np.zeros((self.number_of_rows, self.number_of_bins), dtype=np.uint64)
        self.trigger_count = 0
        self._last_trigger_ps: Optional[int] = None
        self._last_row: Optional[int] = None

    def reset(self, clear_counts: bool = True):
        """ Drop the trigger reference and optionally the accumulated counts. """
        if clear_counts:
            self.counts.fill(0)
            self.trigger_count = 0
        self.forget_trigger_reference()

    def forget_trigger_reference(self):
        """ Forget the last trigger so that binning restarts at the next trigger.

        Used across a pause/continue cycle, where the timetag clock restarts but the gate
        numbering must continue.
        """
        self._last_trigger_ps = None
        self._last_row = None

    def add_block(self, trigger_times: np.ndarray, photon_times: np.ndarray):
        """ Accumulate one block of time-ordered trigger and photon timetags (in picoseconds). """
        trigger_times = np.asarray(trigger_times, dtype=np.uint64)
        photon_times = np.asarray(photon_times, dtype=np.uint64)

        previous_trigger = self._last_trigger_ps
        previous_row = self._last_row

        if trigger_times.size:
            new_rows = (
                np.arange(trigger_times.size, dtype=np.uint64) + np.uint64(self.trigger_count)
            ) % np.uint64(self.number_of_rows)

            self.trigger_count += int(trigger_times.size)
            self._last_trigger_ps = int(trigger_times[-1])
            self._last_row = int(new_rows[-1])
        else:
            new_rows = np.empty(0, dtype=np.uint64)

        if photon_times.size == 0:
            return

        if previous_trigger is None:
            reference_times = trigger_times
            reference_rows = new_rows
        else:
            reference_times = np.concatenate(
                (np.array([previous_trigger], dtype=np.uint64), trigger_times)
            )
            reference_rows = np.concatenate(
                (np.array([previous_row], dtype=np.uint64), new_rows)
            )

        if reference_times.size == 0:
            # No trigger has been seen yet; the photons have no time reference.
            return

        reference_indices = np.searchsorted(reference_times, photon_times, side='right') - 1
        valid = reference_indices >= 0
        if not np.any(valid):
            return

        photon_times = photon_times[valid]
        reference_indices = reference_indices[valid]
        delta_times = photon_times - reference_times[reference_indices]

        # Photons arriving later than the record length belong to the dead time between gates.
        valid = delta_times < np.uint64(self.record_length_ps)
        if not np.any(valid):
            return

        delta_times = delta_times[valid]
        reference_indices = reference_indices[valid]

        bin_indices = (delta_times // np.uint64(self.bin_width_ps)).astype(np.int64)
        row_indices = reference_rows[reference_indices].astype(np.int64)

        flat_indices = row_indices * self.number_of_bins + bin_indices
        increments = np.bincount(
            flat_indices,
            minlength=self.number_of_rows * self.number_of_bins,
        ).reshape(self.number_of_rows, self.number_of_bins)
        self.counts += increments.astype(np.uint64, copy=False)


class SnapiFastCounter(FastCounterInterface):
    """ Qudi fast counter backed by snAPI's T2 Unfold stream.

    Works with any snAPI-supported PicoQuant device (MultiHarp 150/160, HydraHarp 400/500,
    PicoHarp 330, TimeHarp 260). The concrete model is detected at activation and logged.

    Example configuration::

        fastcounter_picoquant:
            module.Class: 'picoquant.snapi_fast_counter.SnapiFastCounter'
            options:
                gated: False               # False: lifetime/decay (1D), True: pulsed gates (2D)
                serial_number: '1000509'   # empty -> first device found
                ini_config: 'C:\\Data\\PicoQuant\\Configs\\MH150.ini'
                ref_source: 'internal'     # 'internal' or 'external_10mhz'
                photon_channels: [1, 2]
                trigger_channel: 0
                # Alternatively, use one of the four marker inputs:
                # trigger_marker: 1
                min_binwidth_s: 1.0e-9
                unfold_block_size: 4000000
                reader_interval_s: 0.05

    For a hardware-free dry run, point ``ptu_file`` at a recorded T2 PTU file instead. snAPI opens
    it as a file device, so the module runs its normal acquisition path against the replayed
    timetag stream::

        fastcounter_picoquant:
            module.Class: 'picoquant.snapi_fast_counter.SnapiFastCounter'
            options:
                ptu_file: 'C:\\Data\\PicoQuant\\recorded_t2.ptu'
                photon_channels: [1]
                trigger_channel: 0

    Notes:
        * ``photon_channels`` and ``trigger_channel`` use snAPI event-channel numbering: sync is 0,
          hardware input 1 is 1, and so on.
        * Set exactly one of ``trigger_channel`` and ``trigger_marker``.
        * The configured bin width is implemented in software. It is rounded up to an integer
          multiple of the device base resolution.
        * snAPI wraps a process-global C API, so only one module of this class may be active per
          qudi process. Activating a second one raises instead of aborting the process.
        * All device front-end settings (CFD/trigger levels, edges, offsets, sync divider) are taken
          from the snAPI ``ini_config`` file, i.e. from the same configuration you would use in the
          PicoQuant software. This module does not duplicate them as qudi config options.
    """

    # config options
    _gated = ConfigOption('gated', False, missing='warn')

    _serial_number = ConfigOption('serial_number', '', missing='warn')
    _ini_config = ConfigOption('ini_config', '', missing='warn')
    _system_ini = ConfigOption('system_ini', '', missing='nothing')

    # Reference clock for initDevice(). 'internal' (default) uses the device's own crystal.
    # 'external_10mhz' locks to an external 10 MHz reference (e.g. a shared lab clock) fed into the
    # device's REF IN. Not applicable when replaying a ptu_file.
    _ref_source = ConfigOption('ref_source', 'internal', missing='nothing')

    # Optional per-channel discriminator trigger levels/edges, applied at activation after
    # ini_config (if any) so a single level can be tweaked here without regenerating a whole
    # exported .ini file. Left unset (None / empty dict), a channel keeps whatever ini_config or the
    # library default set. Can also be changed after activation via set_sync_trigger() /
    # set_photon_trigger() below -- e.g. from a notebook, or eventually a GUI control.
    # Units/convention match snAPI's own setSyncEdgeTrig/setInputEdgeTrig: level in mV, edge 0=falling
    # 1=rising. photon_trigger_levels_mv/edges are keyed by the *unfold* channel number (1..N, same
    # numbering as photon_channels/trigger_channel above) -- not snAPI's 0-indexed device.setInput*
    # channel arg; the off-by-one is handled internally.
    _sync_trigger_level_mv = ConfigOption('sync_trigger_level_mv', None, missing='nothing')
    _sync_trigger_edge = ConfigOption('sync_trigger_edge', 1, missing='nothing')
    _photon_trigger_levels_mv = ConfigOption('photon_trigger_levels_mv', {}, missing='nothing')
    _photon_trigger_edges = ConfigOption('photon_trigger_edges', {}, missing='nothing')

    # Replay a recorded PTU file instead of opening a real device. snAPI treats such a file as a
    # regular device, so the whole module can be exercised without hardware. The replay is
    # finite: once the file is exhausted the histogram stops growing.
    _ptu_file = ConfigOption('ptu_file', '', missing='nothing')

    _photon_channels = ConfigOption('photon_channels', [1], missing='warn')
    _trigger_channel = ConfigOption('trigger_channel', 0, missing='warn')
    _trigger_marker = ConfigOption('trigger_marker', None, missing='nothing')

    # Lower cut-off for the advertised bin width list. The base resolution of a MultiHarp 150P is
    # 5 ps; advertising it would make the pulsed logic default to 5 ps bins and allocate enormous
    # gate histograms. Lower this for high-resolution lifetime measurements.
    _min_binwidth_s = ConfigOption('min_binwidth_s', 1.0e-9, missing='nothing')
    # Guard against accidentally allocating an unreasonably large histogram.
    _max_histogram_mib = ConfigOption('max_histogram_mib', 512, missing='nothing')

    _unfold_block_size = ConfigOption('unfold_block_size', 4_000_000, missing='warn')
    _reader_interval_s = ConfigOption('reader_interval_s', 0.05, missing='warn')
    _save_ptu = ConfigOption('save_ptu', False, missing='warn')

    def on_activate(self):
        """ Acquire and initialize the PicoQuant device in T2 mode. """
        if _snAPI is None:
            raise ImportError(
                'The snAPI package is not available in this environment. Install it with '
                '"pip install snAPI" and make sure the PicoQuant device driver/software for your '
                'harp is installed.'
            )

        self._api_lock = threading.RLock()
        self._data_lock = threading.RLock()
        self._reader_stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_exception: Optional[BaseException] = None

        self._sn = None
        self._status = 0
        self._g2_running = False

        self._number_of_gates = 0
        self._bin_width_s = 0.0
        self._record_length_s = 0.0
        self._accumulator = _GateAccumulator(1, 1, 1)

        self._elapsed_before_run = 0.0
        self._run_started_at: Optional[float] = None

        self._validate_static_config()

        global _active_instance
        with _active_instance_lock:
            if _active_instance is not None:
                raise RuntimeError(
                    f'Another {type(self).__name__} module ({_active_instance!r}) is already '
                    f'active. snAPI wraps a process-global API and only tolerates one instance per '
                    f'process, so configure a single fast counter module and share it.'
                )

            system_ini = str(self._system_ini).strip()
            self._sn = _snAPI(systemIni=system_ini) if system_ini else _snAPI()
            _active_instance = self.module_name

        serial = str(self._serial_number).strip()
        ptu_file = str(self._ptu_file).strip()

        try:
            self._open_device(serial, ptu_file)
        except BaseException:
            # Hand the process-global snAPI back, otherwise a failed activation would block every
            # later attempt to activate the module.
            self._release_snapi()
            raise

        model = self._sn.deviceConfig.get('Model', 'unknown')
        device_id = self._sn.deviceConfig.get('ID', serial or 'unknown')
        self.log.info(
            'snAPI fast counter activated%s: %s (%s), base resolution %s ps, mode=%s, photons=%s, '
            'trigger=%s, ref_source=%s',
            f' in PTU replay mode from {ptu_file!r}' if ptu_file else '',
            model,
            device_id,
            self._base_resolution_ps(),
            'gated' if self._gated else 'ungated',
            list(self._photon_channels),
            self._trigger_description(),
            self._ref_source if not ptu_file else 'n/a (ptu replay)',
        )

    def _open_device(self, serial: str, ptu_file: str):
        """ Acquire either the real harp or a PTU file device and validate the channel routing. """
        if ptu_file:
            # File device: snAPI reads the measurement mode and the device description from the
            # PTU header, so initDevice() and loadIniConfig() do not apply here.
            if not self._sn.getFileDevice(ptu_file):
                self._status = -1
                raise RuntimeError(f'Could not open PTU file device {ptu_file!r}.')

            file_meas_mode = int(self._sn.deviceConfig.get('MeasMode', -1))
            if file_meas_mode != _MeasMode.T2.value:
                raise RuntimeError(
                    f'{ptu_file!r} was recorded in measurement mode {file_meas_mode}, but this '
                    f'module needs T2 ({_MeasMode.T2.value}) data: in T3 the sync records are '
                    f'stripped from the unfold stream, so there is no trigger to bin against.'
                )
        else:
            acquired = self._sn.getDevice(serial) if serial else self._sn.getDevice()
            if not acquired:
                self._status = -1
                raise RuntimeError(f'Could not acquire PicoQuant device {serial!r}.')

            ref_source = _REF_SOURCE_BY_NAME[self._ref_source]
            if not self._sn.initDevice(_MeasMode.T2, ref_source):
                self._status = -1
                raise RuntimeError(
                    f'Could not initialize PicoQuant device in T2 mode with ref_source '
                    f'{self._ref_source!r}. If using external_10mhz, check the reference clock is '
                    f'actually connected and locked.'
                )

            ini_config = str(self._ini_config).strip()
            if ini_config and not self._sn.loadIniConfig(ini_config):
                self._status = -1
                raise RuntimeError(f'Could not load snAPI device configuration {ini_config!r}.')

            self._apply_trigger_config()

            self._sn.getDeviceConfig()

        self._validate_device_channels()

    def on_deactivate(self):
        """ Stop acquisition and release the snAPI device. """
        try:
            self._stop_reader_and_measurement(drain=True)
            self.stop_g2_measurement()
        finally:
            if self.module_state() == 'locked':
                self.module_state.unlock()

            if self._sn is not None:
                try:
                    with self._api_lock:
                        self._sn.closeDevice(allDevices=False)
                except Exception:
                    self.log.exception('Error while closing PicoQuant device.')

            self._release_snapi()
            self._status = 0

    def _release_snapi(self):
        """ Drop the snAPI instance and free the process-global slot for the next activation. """
        global _active_instance
        with _active_instance_lock:
            self._sn = None
            _active_instance = None

    def get_constraints(self):
        """ Return the hardware constraints of the fast counter.

        T2 timetags are binned in software. For compatibility with qudi's FastCounterInterface,
        powers of two of the device base resolution are advertised, matching the natural PicoQuant
        binning choices. Entries below ``min_binwidth_s`` are omitted.
        """
        constraints = dict()
        constraints['hardware_binwidth_list'] = []

        if self._sn is None:
            return constraints

        base_resolution_ps = self._base_resolution_ps()
        bin_steps = int(self._sn.deviceConfig.get('BinSteps', 24))
        min_binwidth_s = float(self._min_binwidth_s)

        binwidth_list = [
            base_resolution_ps * (2 ** step) * 1e-12 for step in range(bin_steps + 1)
        ]
        usable = [width for width in binwidth_list if width >= min_binwidth_s]
        if not usable:
            self.log.warning(
                'min_binwidth_s (%s s) is larger than every bin width the device supports; '
                'advertising the full hardware list instead.',
                min_binwidth_s,
            )
            usable = binwidth_list

        constraints['hardware_binwidth_list'] = usable
        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """ Configure software binning and, in gated mode, the number of cyclic gate rows.

        @param float bin_width_s: length of a single time bin in seconds
        @param float record_length_s: length of the timetrace / of each single gate in seconds
        @param int number_of_gates: number of gates in the pulse sequence, ignored when ungated

        @return tuple(binwidth_s, record_length_s, number_of_gates): the actually achieved settings
        """
        if self._status == 2:
            raise RuntimeError('Cannot configure the fast counter while it is running.')
        if bin_width_s <= 0:
            raise ValueError('bin_width_s must be greater than zero.')
        if record_length_s <= 0:
            raise ValueError('record_length_s must be greater than zero.')
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')

        number_of_gates = int(number_of_gates)
        if self._gated:
            if number_of_gates <= 0:
                raise ValueError(
                    'This fast counter is configured as gated; number_of_gates must be greater '
                    'than zero.'
                )
            number_of_rows = number_of_gates
        else:
            if number_of_gates > 0:
                self.log.warning(
                    'Ignoring number_of_gates=%d: this fast counter is configured as ungated. Set '
                    'the "gated" config option to True for gate resolved acquisition.',
                    number_of_gates,
                )
            number_of_gates = 0
            number_of_rows = 1

        base_resolution_ps = self._base_resolution_ps()
        requested_bin_width_ps = float(bin_width_s) * 1e12
        resolution_steps = max(1, int(np.ceil(requested_bin_width_ps / base_resolution_ps)))

        # Unfold T2 timetags are integer picoseconds. Keep an integer bin width
        # while ensuring it is not smaller than the requested value.
        actual_bin_width_ps = max(1, int(np.ceil(resolution_steps * base_resolution_ps)))
        number_of_bins = max(1, int(np.ceil(float(record_length_s) * 1e12 / actual_bin_width_ps)))

        self._check_histogram_size(number_of_rows, number_of_bins)

        with self._data_lock:
            self._number_of_gates = number_of_gates
            self._accumulator = _GateAccumulator(
                number_of_rows, number_of_bins, actual_bin_width_ps
            )
            self._bin_width_s = actual_bin_width_ps * 1e-12
            self._record_length_s = self._accumulator.record_length_ps * 1e-12

        self._status = 1
        return self._bin_width_s, self._record_length_s, number_of_gates

    def start_measure(self):
        """ Clear all accumulated data and start continuous TTTR acquisition. """
        if self._g2_running:
            raise RuntimeError('Cannot start the fast counter while a g(2) measurement is running.')
        if self._status == 0:
            raise RuntimeError('Fast counter must be configured before starting.')
        if self._status == 2:
            return 0
        if self._status == -1:
            raise RuntimeError('Fast counter is in an error state.')

        with self._data_lock:
            self._accumulator.reset(clear_counts=True)
            self._reader_exception = None
            self._elapsed_before_run = 0.0
            self._run_started_at = time.monotonic()

        self._start_unfold_measurement()
        self._start_reader_thread()

        if self.module_state() != 'locked':
            self.module_state.lock()
        self._status = 2
        return 0

    def stop_measure(self):
        """ Stop acquisition, drain the final block, and keep accumulated data. """
        if self._status in (2, 3, -1):
            self._stop_reader_and_measurement(drain=True)

        with self._data_lock:
            self._freeze_elapsed_time_locked()

        if self.module_state() == 'locked':
            self.module_state.unlock()
        if self._status != 0:
            self._status = 1
        return 0

    def pause_measure(self):
        """ Pause acquisition while preserving accumulated histograms. """
        if self._status != 2:
            return 0

        self._stop_reader_and_measurement(drain=True)
        with self._data_lock:
            self._freeze_elapsed_time_locked()
        self._status = 3
        return 0

    def continue_measure(self):
        """ Resume a paused acquisition without clearing accumulated data. """
        if self._status != 3:
            return 0

        with self._data_lock:
            self._reader_exception = None
            self._run_started_at = time.monotonic()
            # Keep the trigger count so gate numbering continues across the pause.
            # The first new trigger establishes a fresh time reference.
            self._accumulator.forget_trigger_reference()

        self._start_unfold_measurement()
        self._start_reader_thread()
        self._status = 2
        return 0

    def is_gated(self):
        """ Whether this fast counter returns gate resolved (2D) data.

        @return bool: True for gate resolved acquisition, False for a summed histogram
        """
        return bool(self._gated)

    def get_data_trace(self):
        """ Return the accumulated histogram as ``int64``.

        @return tuple(numpy.ndarray, dict): ``counts[timebin]`` when ungated,
                                            ``counts[gate_index, timebin]`` when gated, plus an
                                            info dict with 'elapsed_sweeps' and 'elapsed_time'
        """
        with self._data_lock:
            if self._reader_exception is not None:
                self._status = -1
                raise RuntimeError('snAPI reader thread failed.') from self._reader_exception

            counts = self._accumulator.counts
            max_count = int(counts.max(initial=0))
            if max_count > np.iinfo(np.int64).max:
                self._status = -1
                raise OverflowError(
                    'Fast-counter histogram exceeded the int64 range required by qudi.'
                )

            data = counts.astype(np.int64, copy=True)
            trigger_count = self._accumulator.trigger_count
            if self._gated:
                elapsed_sweeps = trigger_count // self._number_of_gates
            else:
                # Every trigger starts one sweep of the summed histogram.
                elapsed_sweeps = trigger_count
                data = data[0]
            elapsed_time = self._elapsed_time_locked()

        return data, {
            'elapsed_sweeps': elapsed_sweeps,
            'elapsed_time': elapsed_time,
        }

    def get_status(self):
        """ Return the qudi fast-counter state.

        @return int: 0 unconfigured, 1 idle, 2 running, 3 paused, -1 error
        """
        with self._data_lock:
            if self._reader_exception is not None:
                return -1
            return self._status

    def get_binwidth(self):
        """ Return the configured bin width in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self._bin_width_s

    # ------------------------------------------------------------------
    # Runtime trigger-level control
    #
    # Not part of FastCounterInterface (it is hardware specific), so nothing in qudi's generic
    # pulsed logic calls these automatically. They exist so a threshold can be tuned without
    # restarting qudi -- from a notebook today (`fast_counter.set_sync_trigger(130, 0)`), and as
    # the hook a future GUI control would call into. Safe to call between measurements; not while
    # the counter is running (status 2).
    # ------------------------------------------------------------------

    def set_sync_trigger(self, level_mv: int, edge: int):
        """ Change the sync discriminator trigger level/edge on the live device.

        @param int level_mv: trigger level in mV
        @param int edge: 0 = falling, 1 = rising
        """
        if self._status == 2:
            raise RuntimeError('Cannot change trigger settings while the counter is running.')
        if self._g2_running:
            raise RuntimeError('Cannot change trigger settings while a g(2) measurement is running.')
        if edge not in (0, 1):
            raise ValueError('edge must be 0 (falling) or 1 (rising).')
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')

        with self._api_lock:
            if not self._sn.device.setSyncEdgeTrig(int(level_mv), int(edge)):
                raise RuntimeError(f'setSyncEdgeTrig({level_mv}, {edge}) failed.')
        self.log.info('Sync trigger set to %s mV, edge=%s.', level_mv, edge)

    def get_sync_trigger(self) -> Tuple[int, int]:
        """ Read back the sync discriminator trigger level/edge currently set on the device.

        @return tuple(int, int): (level_mv, edge) -- edge: 0 = falling, 1 = rising
        """
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')
        with self._api_lock:
            self._sn.getDeviceConfig()
            cfg = self._sn.deviceConfig
        return int(cfg['SyncTrigLvl']), int(cfg['SyncTrigEdge'])

    def set_photon_trigger(self, channel: int, level_mv: int, edge: int):
        """ Change one photon channel's discriminator trigger level/edge on the live device.

        @param int channel: unfold/event channel number (1..N, i.e. photon_channels numbering,
                             not snAPI's 0-indexed device.setInputEdgeTrig channel argument)
        @param int level_mv: trigger level in mV
        @param int edge: 0 = falling, 1 = rising
        """
        if self._status == 2:
            raise RuntimeError('Cannot change trigger settings while the counter is running.')
        if self._g2_running:
            raise RuntimeError('Cannot change trigger settings while a g(2) measurement is running.')
        if edge not in (0, 1):
            raise ValueError('edge must be 0 (falling) or 1 (rising).')
        if int(channel) < 1:
            raise ValueError(f'channel must be >= 1 (0 is sync, use set_sync_trigger for that), '
                              f'got {channel!r}.')
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')

        device_index = int(channel) - 1  # unfold numbering (1..N) -> setInputEdgeTrig's 0-indexed
        with self._api_lock:
            if not self._sn.device.setInputEdgeTrig(device_index, int(level_mv), int(edge)):
                raise RuntimeError(
                    f'setInputEdgeTrig({device_index}, {level_mv}, {edge}) failed for channel '
                    f'{channel}.'
                )
        self.log.info('Channel %s trigger set to %s mV, edge=%s.', channel, level_mv, edge)

    def get_photon_trigger(self, channel: int) -> Tuple[int, int]:
        """ Read back one photon channel's discriminator trigger level/edge from the device.

        @param int channel: unfold/event channel number (1..N)
        @return tuple(int, int): (level_mv, edge) -- edge: 0 = falling, 1 = rising
        """
        if int(channel) < 1:
            raise ValueError(f'channel must be >= 1 (0 is sync, use get_sync_trigger for that), '
                              f'got {channel!r}.')
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')

        device_index = int(channel) - 1
        with self._api_lock:
            self._sn.getDeviceConfig()
            chan_cfg = self._sn.deviceConfig['ChansCfg'][device_index]
        return int(chan_cfg['TrigLvl']), int(chan_cfg['TrigEdge'])

    def set_channel_trigger(self, channel: int, level_mv: int, edge: int):
        """ Change a trigger level/edge by snAPI channel number (0 = sync, 1..N = inputs). """
        if int(channel) == 0:
            self.set_sync_trigger(level_mv, edge)
        else:
            self.set_photon_trigger(channel, level_mv, edge)

    def get_channel_trigger(self, channel: int) -> Tuple[int, int]:
        """ Read a trigger level/edge by snAPI channel number (0 = sync, 1..N = inputs). """
        if int(channel) == 0:
            return self.get_sync_trigger()
        return self.get_photon_trigger(channel)

    # ------------------------------------------------------------------
    # g(2) correlation measurement
    #
    # Thin wrapper around snAPI's own Correlation class (sn.correlation), which already
    # implements the g(2) math -- this module does not reimplement any binning/correlation
    # logic itself, unlike get_data_trace()'s software gate accumulation. Runs on the same
    # single snAPI instance this module owns (snAPI tolerates only one per process), so a g(2)
    # measurement and a fast-counter measurement cannot run at the same time -- guarded below
    # rather than left to fail opaquely inside the DLL.
    #
    # Not part of FastCounterInterface (hardware specific). Channel numbering matches the rest
    # of this module: 0 = sync, 1..N = hardware inputs. snAPI itself takes windowSize/binWidth
    # in picoseconds and returns bin times in seconds; this wrapper works in seconds throughout,
    # like the rest of the module, and converts at the boundary.
    # ------------------------------------------------------------------

    def configure_g2(self, start_channel: int, stop_channel: int, window_size_s: float,
                      bin_width_s: Optional[float] = None, normalization: bool = True):
        """ Configure a g(2) correlation between two channels (same channel -> autocorrelation).

        @param int start_channel: snAPI channel (0 = sync, 1..N = inputs)
        @param int stop_channel: snAPI channel (0 = sync, 1..N = inputs)
        @param float window_size_s: half-width of the correlation window, in seconds
        @param float|None bin_width_s: bin width in seconds (default: device base resolution)
        @param bool normalization: True for g(2) normalization, False to count raw coincidences
        """
        if self._status == 2:
            raise RuntimeError('Cannot configure g(2) while the fast counter is running.')
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')
        if window_size_s <= 0:
            raise ValueError('window_size_s must be greater than zero.')

        window_size_ps = float(window_size_s) * 1e12
        bin_width_ps = float(bin_width_s) * 1e12 if bin_width_s is not None else None

        with self._api_lock:
            ok = self._sn.correlation.setG2Parameters(
                int(start_channel), int(stop_channel), window_size_ps, bin_width_ps,
                bool(normalization),
            )
        if ok is False:
            raise RuntimeError(
                f'setG2Parameters({start_channel}, {stop_channel}, {window_size_ps} ps, '
                f'{bin_width_ps} ps) failed -- see the snAPI log for the reason.'
            )

    def start_g2_measurement(self, acquisition_time_s: float = 0.0):
        """ Start a g(2) measurement (non-blocking). 0 runs until stop_g2_measurement(). """
        if self._status == 2:
            raise RuntimeError('Cannot start g(2) while the fast counter is running.')
        if self._g2_running:
            raise RuntimeError('A g(2) measurement is already running.')
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')

        acq_ms = int(round(float(acquisition_time_s) * 1000))
        with self._api_lock:
            # snAPI's Correlation object (and its internal "finished" flag) is created once and
            # reused across every measure() call on this device -- clearMeasure() resets that
            # stale state before a new run, so is_g2_finished() can't see a leftover True from a
            # previous measurement and report the brand-new one as already finished.
            self._sn.correlation.clearMeasure()
            if not self._sn.correlation.measure(acqTime=acq_ms, waitFinished=False):
                raise RuntimeError('snAPI g(2) measurement could not be started.')
        self._g2_running = True

    def stop_g2_measurement(self):
        """ Stop a running g(2) measurement. Safe to call even if nothing is running. """
        self._g2_running = False
        if self._sn is None:
            return
        with self._api_lock:
            self._sn.correlation.stopMeasure()

    def get_g2_data(self) -> Tuple[np.ndarray, np.ndarray]:
        """ Return the g(2) data accumulated so far.

        @return tuple(numpy.ndarray, numpy.ndarray): ``(tau_s, g2)`` -- lag time in seconds and
                                                      the (optionally normalized) g(2) value.
        """
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')
        with self._api_lock:
            data, bins = self._sn.correlation.getG2Data()
        return np.asarray(bins, dtype=np.float64), np.asarray(data, dtype=np.float64)

    def is_g2_finished(self) -> bool:
        """ Whether the running g(2) measurement has reached its acquisition time. """
        if self._sn is None:
            return True
        with self._api_lock:
            return bool(self._sn.correlation.isFinished())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_trigger_config(self):
        """ Apply the optional sync_trigger_*/photon_trigger_* config options, if set.

        Runs after ini_config (if any) is loaded, so these act as targeted overrides on top of a
        full exported .ini rather than requiring one just to tweak a single channel's level.
        """
        if self._sync_trigger_level_mv is not None:
            self.set_sync_trigger(self._sync_trigger_level_mv, self._sync_trigger_edge)

        for channel, level_mv in self._photon_trigger_levels_mv.items():
            edge = self._photon_trigger_edges.get(channel, 1)
            self.set_photon_trigger(channel, level_mv, edge)

    def _base_resolution_ps(self) -> float:
        return float(self._sn.deviceConfig['BaseResolution'])

    def _check_histogram_size(self, number_of_rows: int, number_of_bins: int):
        required_mib = number_of_rows * number_of_bins * 8 / 1024 ** 2
        if required_mib > float(self._max_histogram_mib):
            raise ValueError(
                f'The requested histogram of {number_of_rows} gate(s) x {number_of_bins} bins '
                f'needs {required_mib:.0f} MiB, which exceeds max_histogram_mib '
                f'({self._max_histogram_mib}). Increase the bin width, shorten the record length, '
                f'or raise max_histogram_mib.'
            )

    def _validate_static_config(self):
        ref_source_key = str(self._ref_source).strip().lower()
        if ref_source_key not in _REF_SOURCE_BY_NAME:
            raise ValueError(
                f"ref_source must be one of {sorted(_REF_SOURCE_BY_NAME)}, got {self._ref_source!r}."
            )
        self._ref_source = ref_source_key

        if self._sync_trigger_edge not in (0, 1):
            raise ValueError(f'sync_trigger_edge must be 0 (falling) or 1 (rising), got '
                              f'{self._sync_trigger_edge!r}.')
        for channel, edge in self._photon_trigger_edges.items():
            if edge not in (0, 1):
                raise ValueError(f'photon_trigger_edges[{channel!r}] must be 0 (falling) or 1 '
                                  f'(rising), got {edge!r}.')
        unknown_edge_channels = set(self._photon_trigger_edges) - set(self._photon_trigger_levels_mv)
        if unknown_edge_channels:
            raise ValueError(f'photon_trigger_edges has channel(s) {sorted(unknown_edge_channels)} '
                              f'not present in photon_trigger_levels_mv.')

        photon_channels = self._normalize_channel_list(self._photon_channels)
        if not photon_channels:
            raise ValueError('photon_channels must contain at least one snAPI channel.')
        self._photon_channels = tuple(photon_channels)

        if self._trigger_marker is not None:
            marker = int(self._trigger_marker)
            if marker not in (1, 2, 3, 4):
                raise ValueError('trigger_marker must be one of 1, 2, 3, or 4.')
            self._trigger_marker = marker
        else:
            self._trigger_channel = int(self._trigger_channel)

        if self._trigger_marker is None and self._trigger_channel in self._photon_channels:
            raise ValueError('trigger_channel must not also be listed in photon_channels.')

        self._unfold_block_size = int(self._unfold_block_size)
        if self._unfold_block_size <= 0:
            raise ValueError('unfold_block_size must be greater than zero.')

        self._reader_interval_s = float(self._reader_interval_s)
        if self._reader_interval_s <= 0:
            raise ValueError('reader_interval_s must be greater than zero.')

        self._min_binwidth_s = float(self._min_binwidth_s)
        if self._min_binwidth_s < 0:
            raise ValueError('min_binwidth_s must not be negative.')

    def _validate_device_channels(self):
        number_of_inputs = int(self._sn.deviceConfig['NumChans'])
        max_event_channel = number_of_inputs

        invalid_photon_channels = [
            channel
            for channel in self._photon_channels
            if channel < 0 or channel > max_event_channel
        ]
        if invalid_photon_channels:
            raise ValueError(
                f'Invalid photon_channels {invalid_photon_channels}; valid snAPI channels are '
                f'0..{max_event_channel} (0 is sync).'
            )

        if self._trigger_marker is None:
            if self._trigger_channel < 0 or self._trigger_channel > max_event_channel:
                raise ValueError(
                    f'Invalid trigger_channel {self._trigger_channel}; valid snAPI channels are '
                    f'0..{max_event_channel} (0 is sync).'
                )

    @staticmethod
    def _normalize_channel_list(channels: Sequence[int]) -> list:
        if isinstance(channels, (int, np.integer)):
            channels = [int(channels)]
        return sorted({int(channel) for channel in channels})

    def _trigger_description(self) -> str:
        if self._trigger_marker is not None:
            return f'marker {self._trigger_marker}'
        return f'channel {self._trigger_channel}'

    def _start_unfold_measurement(self):
        if self._sn is None:
            raise RuntimeError('snAPI device is not active.')

        self._reader_stop.clear()
        with self._api_lock:
            started = self._sn.unfold.startBlock(
                acqTime=0,
                size=self._unfold_block_size,
                savePTU=bool(self._save_ptu),
            )
        if not started:
            self._status = -1
            raise RuntimeError('snAPI Unfold block measurement could not be started.')

    def _start_reader_thread(self):
        if self._reader_thread is not None and self._reader_thread.is_alive():
            raise RuntimeError('snAPI reader thread is already running.')

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f'{self.__class__.__name__}-reader',
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self):
        try:
            while not self._reader_stop.wait(self._reader_interval_s):
                self._read_and_process_block()
        except BaseException as exc:
            with self._data_lock:
                self._reader_exception = exc
                self._status = -1
            self.log.exception('snAPI Unfold reader failed.')

    def _stop_reader_and_measurement(self, drain: bool):
        self._reader_stop.set()

        if self._sn is not None:
            try:
                with self._api_lock:
                    self._sn.unfold.stopMeasure()
            except Exception:
                self.log.exception('Error while stopping snAPI Unfold measurement.')

        reader = self._reader_thread
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=max(1.0, 4.0 * self._reader_interval_s))
        self._reader_thread = None

        if drain and self._sn is not None:
            # snAPI documents that data may remain available after the
            # measurement has finished. Drain a bounded number of final blocks.
            for _ in range(8):
                if self._read_and_process_block() == 0:
                    break

    def _read_and_process_block(self) -> int:
        if self._sn is None:
            return 0

        with self._api_lock:
            times, channels = self._sn.unfold.getBlock()

        number_of_records = len(times)
        if number_of_records == 0:
            return 0

        # Copy the ctypes-backed views before snAPI reuses its block buffers.
        times_copy = np.asarray(times, dtype=np.uint64).copy()
        channels_copy = np.asarray(channels, dtype=np.uint8).copy()
        self._process_unfold_block(times_copy, channels_copy)
        return number_of_records

    def _process_unfold_block(self, times: np.ndarray, channels: np.ndarray):
        if times.size == 0:
            return
        if times.shape != channels.shape:
            raise RuntimeError('snAPI returned mismatched time and channel arrays.')

        trigger_times, photon_times = self._split_block(times, channels)

        with self._data_lock:
            self._accumulator.add_block(trigger_times, photon_times)

    def _split_block(
        self, times: np.ndarray, channels: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """ Separate an unfold block into trigger and photon timetags. """
        marker_mask = (channels & _MARKER_FLAG) != 0
        if self._trigger_marker is not None:
            marker_bits = channels & np.uint8(0x7F)
            trigger_mask = marker_mask & (
                (marker_bits & np.uint8(1 << (self._trigger_marker - 1))) != 0
            )
        else:
            trigger_mask = (~marker_mask) & (channels == self._trigger_channel)

        normal_mask = ~marker_mask
        if len(self._photon_channels) == 1:
            photon_mask = normal_mask & (channels == self._photon_channels[0])
        else:
            photon_mask = normal_mask & np.isin(channels, self._photon_channels)

        return times[trigger_mask], times[photon_mask]

    def _freeze_elapsed_time_locked(self):
        if self._run_started_at is not None:
            self._elapsed_before_run += time.monotonic() - self._run_started_at
            self._run_started_at = None

    def _elapsed_time_locked(self) -> float:
        elapsed = self._elapsed_before_run
        if self._run_started_at is not None:
            elapsed += time.monotonic() - self._run_started_at
        return elapsed
