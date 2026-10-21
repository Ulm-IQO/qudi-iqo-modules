# -*- coding: utf-8 -*-

"""
End-to-end tests of the PicoQuant snAPI fast counter against a replayed PTU file.

These tests need snAPI installed ('pip install snAPI') but no PicoQuant hardware: a synthetic
Generic-T2 PTU file is written, opened by snAPI as a file device, and streamed through the real
``SnapiFastCounter`` block-processing code. They are skipped when snAPI is not available::

    pytest tests/test_snapi_fast_counter_ptu.py --noconftest

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

import logging
import threading
import time

import numpy as np
import pytest

from qudi.hardware.picoquant.snapi_fast_counter import _GateAccumulator, SnapiFastCounter

import synthetic_ptu

snAPI = pytest.importorskip('snAPI.Main', reason='snAPI is not installed').snAPI

BASE_RESOLUTION_PS = 5.0


class _FastCounterStub:
    """ Holds exactly the state the block-processing methods of SnapiFastCounter touch.

    A real qudi module cannot be instantiated outside a running qudi application, so the production
    methods are called unbound against this stub. The code under test is the real one.
    """

    # Borrow the production methods verbatim; they only touch the attributes set up below.
    _split_block = SnapiFastCounter._split_block
    _process_unfold_block = SnapiFastCounter._process_unfold_block

    def __init__(self, number_of_rows, number_of_bins, bin_width_ps,
                 photon_channels=(1,), trigger_channel=0, trigger_marker=None):
        self._data_lock = threading.RLock()
        self._accumulator = _GateAccumulator(number_of_rows, number_of_bins, bin_width_ps)
        self._photon_channels = tuple(photon_channels)
        self._trigger_channel = trigger_channel
        self._trigger_marker = trigger_marker

    def process(self, times, channels):
        # Same copy-out of the ctypes-backed snAPI buffers as _read_and_process_block does.
        self._process_unfold_block(
            np.asarray(times, dtype=np.uint64).copy(),
            np.asarray(channels, dtype=np.uint8).copy(),
        )


@pytest.fixture(scope='session')
def sn(tmp_path_factory):
    """ One snAPI instance for the whole session.

    Two snAPI constraints shape this fixture, both of which abort the interpreter rather than
    raising when violated:

    * Only one live snAPI instance per process. Constructing a second one while the first is still
      referenced aborts, so the instance is shared by every test.
    * ``getFileDevice`` may be called repeatedly to switch files, but calling ``closeDevice`` in
      between aborts. The device is therefore closed only once, at session teardown.

    Console logging is switched off through a private system.ini so that the DLL does not write to
    the stdout file descriptor that pytest swaps out while capturing.
    """
    directory = tmp_path_factory.mktemp('snapi')
    system_ini = directory / 'system.ini'
    system_ini.write_text(
        '[Paths]\n'
        f'Data = {directory}\n'
        '\n'
        '[Log]\n'
        'File = false\n'
        'Console = false\n'
        '\n'
        '[Params]\n'
        'BufferSize = 32\n',
        encoding='utf-8',
    )

    instance = snAPI(systemIni=str(system_ini))
    yield instance
    instance.closeDevice()


class _InterfaceStub(_FastCounterStub):
    """ Extends the block-processing stub with the state the interface methods read.

    Lets the real ``get_constraints`` / ``configure`` / ``get_data_trace`` run against the device
    description snAPI reports for a PTU file device.
    """

    get_constraints = SnapiFastCounter.get_constraints
    configure = SnapiFastCounter.configure
    get_data_trace = SnapiFastCounter.get_data_trace
    get_binwidth = SnapiFastCounter.get_binwidth
    is_gated = SnapiFastCounter.is_gated
    _base_resolution_ps = SnapiFastCounter._base_resolution_ps
    _check_histogram_size = SnapiFastCounter._check_histogram_size
    _elapsed_time_locked = SnapiFastCounter._elapsed_time_locked

    def __init__(self, sn, gated, **kwargs):
        super().__init__(1, 1, 1, **kwargs)
        self._sn = sn
        self._gated = gated
        self._status = 0
        self._number_of_gates = 0
        self._bin_width_s = 0.0
        self._record_length_s = 0.0
        self._reader_exception = None
        self._elapsed_before_run = 0.0
        self._run_started_at = None
        self._min_binwidth_s = 1.0e-9
        self._max_histogram_mib = 512
        self.log = logging.getLogger(__name__)


def _replay(sn, ptu_path, stub, block_size=4_000_000, poll_interval=0.02, timeout=20.0):
    """ Stream a PTU file through the fast counter exactly as the module's reader thread does. """
    assert sn.getFileDevice(str(ptu_path)), f'snAPI could not open {ptu_path}'
    assert sn.unfold.startBlock(acqTime=0, size=block_size, savePTU=False)

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            times, channels = sn.unfold.getBlock()
            if len(times):
                stub.process(times, channels)
            if sn.unfold.isFinished():
                break
        else:
            raise AssertionError('PTU replay did not finish within the timeout.')

        # Drain whatever is still buffered after the file ended.
        for _ in range(8):
            times, channels = sn.unfold.getBlock()
            if not len(times):
                break
            stub.process(times, channels)
    finally:
        sn.unfold.stopMeasure()


def _write(tmp_path, name, events):
    path = tmp_path / name
    synthetic_ptu.write_ptu(str(path), events, base_resolution_ps=BASE_RESOLUTION_PS)
    return path


def test_file_device_reports_the_header_device_description(sn, tmp_path):
    """ snAPI exposes a PTU file as a device, which is what makes hardware-free runs possible. """
    path = _write(tmp_path, 'config.ptu', synthetic_ptu.gated_sequence(sweeps=2))

    assert sn.getFileDevice(str(path))
    config = sn.deviceConfig
    assert config['Model'] == 'MultiHarp 150'
    assert config['MeasMode'] == synthetic_ptu.MEAS_MODE_T2
    assert config['BaseResolution'] == pytest.approx(BASE_RESOLUTION_PS)
    assert config['NumChans'] == 4


def test_gated_replay_puts_each_gate_peak_in_its_own_row(sn, tmp_path):
    """ A 5-gate sequence replayed from file yields the expected 2D trace. """
    number_of_gates = 5
    sweeps = 20
    gate_delay_ps = 100
    bin_width_ps = 100
    number_of_bins = 10

    path = _write(
        tmp_path,
        'gated.ptu',
        synthetic_ptu.gated_sequence(
            number_of_gates=number_of_gates, sweeps=sweeps, gate_delay_ps=gate_delay_ps
        ),
    )
    stub = _FastCounterStub(number_of_gates, number_of_bins, bin_width_ps)
    _replay(sn, path, stub)

    counts = stub._accumulator.counts
    assert counts.shape == (number_of_gates, number_of_bins)
    assert stub._accumulator.trigger_count == number_of_gates * sweeps

    expected = np.zeros((number_of_gates, number_of_bins), dtype=np.uint64)
    for gate in range(number_of_gates):
        # Gate g fires its photon at (g + 1) * 100 ps -> bin (g + 1) at a 100 ps bin width.
        expected[gate, gate + 1] = sweeps
    np.testing.assert_array_equal(counts, expected)


def test_ungated_replay_reproduces_the_decay_histogram(sn, tmp_path):
    """ A single row accumulates the arrival-time histogram of an exponential decay. """
    sweeps = 2000
    lifetime_ps = 800
    bin_width_ps = 100
    number_of_bins = 40  # 4 ns record length

    events = synthetic_ptu.decay_sequence(sweeps=sweeps, lifetime_ps=lifetime_ps)
    path = _write(tmp_path, 'decay.ptu', events)

    stub = _FastCounterStub(1, number_of_bins, bin_width_ps)
    _replay(sn, path, stub)

    counts = stub._accumulator.counts
    assert counts.shape == (1, number_of_bins)
    assert stub._accumulator.trigger_count == sweeps

    # Cross-check against the histogram of the delays that were written into the file.
    trigger_times = [t for t, kind, _ in events if kind == 'sync']
    photon_times = [t for t, kind, _ in events if kind == 'photon']
    delays = np.array(photon_times) - np.array(trigger_times)
    expected, _ = np.histogram(
        delays, bins=number_of_bins, range=(0, number_of_bins * bin_width_ps)
    )
    np.testing.assert_array_equal(counts[0], expected.astype(np.uint64))

    # And it must actually look like a decay: monotonically falling in the well-populated region
    # and consistent with the lifetime it was generated from.
    assert counts[0, 0] > counts[0, 5] > counts[0, 15]
    bin_centres = (np.arange(number_of_bins) + 0.5) * bin_width_ps
    mean_delay = float((counts[0] * bin_centres).sum() / counts[0].sum())
    assert lifetime_ps * 0.8 < mean_delay < lifetime_ps * 1.2


def test_marker_triggered_replay_matches_sync_triggered_replay(sn, tmp_path):
    """ Feeding the sequence trigger into a marker input gives the same gate assignment. """
    number_of_gates = 4
    sweeps = 10

    marker_events = synthetic_ptu.gated_sequence(
        number_of_gates=number_of_gates,
        sweeps=sweeps,
        trigger_kind='marker',
        trigger_number=2,
    )
    path = _write(tmp_path, 'marker.ptu', marker_events)

    stub = _FastCounterStub(number_of_gates, 10, 100, trigger_channel=None, trigger_marker=2)
    _replay(sn, path, stub)

    expected = np.zeros((number_of_gates, 10), dtype=np.uint64)
    for gate in range(number_of_gates):
        expected[gate, gate + 1] = sweeps
    np.testing.assert_array_equal(stub._accumulator.counts, expected)


def test_gated_configure_and_trace_honour_the_interface_contract(sn, tmp_path):
    """ configure(..., N) then get_data_trace() must give a 2D (N, record/binwidth) array. """
    path = _write(tmp_path, 'contract_gated.ptu', synthetic_ptu.gated_sequence(sweeps=2))
    assert sn.getFileDevice(str(path))
    counter = _InterfaceStub(sn, gated=True)

    binwidths = counter.get_constraints()['hardware_binwidth_list']
    assert binwidths, 'no bin widths advertised'
    assert min(binwidths) >= 1.0e-9  # min_binwidth_s cut-off applied
    # Every entry is a power-of-two multiple of the 5 ps base resolution.
    for width in binwidths:
        ticks = round(width * 1e12 / BASE_RESOLUTION_PS)
        assert ticks & (ticks - 1) == 0

    number_of_gates = 12
    bin_width_s, record_length_s, gates = counter.configure(binwidths[0], 3.0e-6, number_of_gates)
    assert gates == number_of_gates
    assert bin_width_s == pytest.approx(binwidths[0])
    assert counter.get_binwidth() == bin_width_s
    assert counter.is_gated() is True

    data, info = counter.get_data_trace()
    assert data.dtype == np.int64
    assert data.shape == (number_of_gates, round(record_length_s / bin_width_s))
    assert info['elapsed_sweeps'] == 0


def test_ungated_configure_returns_a_one_dimensional_trace(sn, tmp_path):
    """ An ungated counter reports 0 gates and a 1D trace, as fast_counter_dummy does. """
    path = _write(tmp_path, 'contract_ungated.ptu', synthetic_ptu.gated_sequence(sweeps=2))
    assert sn.getFileDevice(str(path))
    counter = _InterfaceStub(sn, gated=False)

    bin_width_s, record_length_s, gates = counter.configure(1.0e-9, 3.0e-6, 0)
    assert gates == 0
    assert counter.is_gated() is False

    data, info = counter.get_data_trace()
    assert data.ndim == 1
    assert data.shape == (round(record_length_s / bin_width_s),)
    assert info['elapsed_sweeps'] == 0

    # An oversized histogram must be refused with a readable error, not a MemoryError.
    with pytest.raises(ValueError, match='max_histogram_mib'):
        counter.configure(5.0e-12, 1.0e-3, 0)


def test_replay_survives_timetag_overflows(sn, tmp_path):
    """ A stream longer than the 25 bit timetag range (~33.5 us) is unfolded correctly. """
    # 40 gates x 200 sweeps x 5 ns = 40 us, i.e. more than one wraparound.
    number_of_gates = 4
    sweeps = 2000
    path = _write(
        tmp_path,
        'overflow.ptu',
        synthetic_ptu.gated_sequence(number_of_gates=number_of_gates, sweeps=sweeps),
    )

    stub = _FastCounterStub(number_of_gates, 10, 100)
    _replay(sn, path, stub)

    assert stub._accumulator.trigger_count == number_of_gates * sweeps
    expected = np.zeros((number_of_gates, 10), dtype=np.uint64)
    for gate in range(number_of_gates):
        expected[gate, gate + 1] = sweeps
    np.testing.assert_array_equal(stub._accumulator.counts, expected)
