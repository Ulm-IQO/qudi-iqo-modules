# -*- coding: utf-8 -*-

"""
Hardware-free tests for the timetag binning of the PicoQuant snAPI fast counter.

These tests exercise the pure-numpy parts of
``qudi.hardware.picoquant.snapi_fast_counter`` and therefore run without snAPI, without a
PicoQuant device and without a running qudi instance::

    pytest tests/test_snapi_fast_counter.py --noconftest

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

import numpy as np
import pytest

from qudi.hardware.picoquant.snapi_fast_counter import _GateAccumulator, SnapiFastCounter


class _SplitStub:
    """ Minimal stand-in exposing only the attributes ``_split_block`` reads. """

    def __init__(self, photon_channels, trigger_channel=0, trigger_marker=None):
        self._photon_channels = tuple(photon_channels)
        self._trigger_channel = trigger_channel
        self._trigger_marker = trigger_marker


def _split(stub, times, channels):
    return SnapiFastCounter._split_block(
        stub,
        np.asarray(times, dtype=np.uint64),
        np.asarray(channels, dtype=np.uint8),
    )


# ----------------------------------------------------------------------------------------------
# _GateAccumulator
# ----------------------------------------------------------------------------------------------

def test_ungated_sums_all_triggers_into_one_row():
    """ A single row accumulates the arrival-time histogram over all triggers. """
    accumulator = _GateAccumulator(number_of_rows=1, number_of_bins=10, bin_width_ps=100)

    triggers = [0, 5_000, 10_000]
    # Same 250 ps delay after every trigger -> all counts in bin 2.
    photons = [250, 5_250, 10_250]
    accumulator.add_block(triggers, photons)

    expected = np.zeros((1, 10), dtype=np.uint64)
    expected[0, 2] = 3
    np.testing.assert_array_equal(accumulator.counts, expected)
    assert accumulator.trigger_count == 3


def test_gated_assigns_triggers_cyclically_to_rows():
    """ Successive triggers land in successive gate rows and wrap around. """
    accumulator = _GateAccumulator(number_of_rows=3, number_of_bins=4, bin_width_ps=100)

    # 4 triggers -> rows 0, 1, 2, 0 again. One photon each, at a row specific delay.
    triggers = [0, 1_000, 2_000, 3_000]
    photons = [50, 1_150, 2_250, 3_350]
    accumulator.add_block(triggers, photons)

    expected = np.zeros((3, 4), dtype=np.uint64)
    expected[0, 0] = 1  # first trigger,  50 ps  -> bin 0
    expected[1, 1] = 1  # second trigger, 150 ps -> bin 1
    expected[2, 2] = 1  # third trigger,  250 ps -> bin 2
    expected[0, 3] = 1  # fourth trigger, 350 ps -> bin 3, wrapped back to row 0
    np.testing.assert_array_equal(accumulator.counts, expected)
    assert accumulator.trigger_count == 4


def test_photons_before_the_first_trigger_are_discarded():
    accumulator = _GateAccumulator(number_of_rows=1, number_of_bins=10, bin_width_ps=100)

    accumulator.add_block(trigger_times=[1_000], photon_times=[10, 500, 1_100])

    assert accumulator.counts.sum() == 1
    assert accumulator.counts[0, 1] == 1


def test_photons_beyond_the_record_length_are_discarded():
    """ Photons in the dead time between gates must not fold back into the histogram. """
    accumulator = _GateAccumulator(number_of_rows=1, number_of_bins=5, bin_width_ps=100)
    # record length = 5 * 100 ps = 500 ps

    accumulator.add_block(trigger_times=[0], photon_times=[100, 499, 500, 900])

    assert accumulator.counts.sum() == 2
    assert accumulator.counts[0, 1] == 1
    assert accumulator.counts[0, 4] == 1


def test_trigger_reference_carries_over_between_blocks():
    """ A photon read in a later block is still assigned to the trigger of the earlier block. """
    accumulator = _GateAccumulator(number_of_rows=2, number_of_bins=10, bin_width_ps=100)

    accumulator.add_block(trigger_times=[1_000], photon_times=[])
    accumulator.add_block(trigger_times=[], photon_times=[1_300])

    assert accumulator.counts[0, 3] == 1
    assert accumulator.counts.sum() == 1

    # The next trigger continues the cyclic gate numbering in row 1.
    accumulator.add_block(trigger_times=[2_000], photon_times=[2_300])
    assert accumulator.counts[1, 3] == 1
    assert accumulator.trigger_count == 2


def test_forget_trigger_reference_keeps_counts_and_gate_numbering():
    """ Pause/continue drops the stale time reference but not the accumulated data. """
    accumulator = _GateAccumulator(number_of_rows=2, number_of_bins=10, bin_width_ps=100)
    accumulator.add_block(trigger_times=[1_000], photon_times=[1_100])

    accumulator.forget_trigger_reference()
    # After the restart the timetag clock begins again at 0. Without a fresh trigger the photon
    # must be dropped instead of being binned against the pre-pause trigger.
    accumulator.add_block(trigger_times=[], photon_times=[50])
    assert accumulator.counts.sum() == 1

    accumulator.add_block(trigger_times=[100], photon_times=[200])
    assert accumulator.counts[1, 1] == 1  # gate numbering continued at row 1
    assert accumulator.trigger_count == 2


def test_reset_clears_counts_and_trigger_count():
    accumulator = _GateAccumulator(number_of_rows=2, number_of_bins=4, bin_width_ps=100)
    accumulator.add_block(trigger_times=[0], photon_times=[100])

    accumulator.reset(clear_counts=True)

    assert accumulator.counts.sum() == 0
    assert accumulator.trigger_count == 0


@pytest.mark.parametrize('rows,bins,width', [(0, 4, 100), (2, 0, 100), (2, 4, 0)])
def test_invalid_accumulator_geometry_is_rejected(rows, bins, width):
    with pytest.raises(ValueError):
        _GateAccumulator(rows, bins, width)


def test_gated_shape_matches_configure_contract():
    """ A gated acquisition yields exactly (number_of_gates, record_length / binwidth). """
    number_of_gates = 7
    number_of_bins = 300
    accumulator = _GateAccumulator(number_of_gates, number_of_bins, 1000)

    assert accumulator.counts.shape == (number_of_gates, number_of_bins)
    assert accumulator.record_length_ps == number_of_bins * 1000


# ----------------------------------------------------------------------------------------------
# _split_block
# ----------------------------------------------------------------------------------------------

def test_split_block_uses_sync_as_trigger_and_sums_photon_channels():
    stub = _SplitStub(photon_channels=(1, 2), trigger_channel=0)

    times = [0, 10, 20, 30, 40]
    channels = [0, 1, 2, 3, 0]  # channel 3 is neither trigger nor a configured photon channel

    triggers, photons = _split(stub, times, channels)

    np.testing.assert_array_equal(triggers, np.array([0, 40], dtype=np.uint64))
    np.testing.assert_array_equal(photons, np.array([10, 20], dtype=np.uint64))


def test_split_block_uses_marker_as_trigger():
    stub = _SplitStub(photon_channels=(1,), trigger_marker=2)

    marker_2 = 0x80 | (1 << 1)
    marker_1 = 0x80 | (1 << 0)
    times = [0, 10, 20, 30]
    channels = [marker_2, 1, marker_1, 1]

    triggers, photons = _split(stub, times, channels)

    np.testing.assert_array_equal(triggers, np.array([0], dtype=np.uint64))
    np.testing.assert_array_equal(photons, np.array([10, 30], dtype=np.uint64))


def test_split_block_does_not_confuse_markers_with_channels():
    """ Marker events must never be counted as photons on the channel of the same number. """
    stub = _SplitStub(photon_channels=(1,), trigger_channel=0)

    marker_1 = 0x80 | (1 << 0)
    triggers, photons = _split(stub, [0, 10, 20], [0, marker_1, 1])

    np.testing.assert_array_equal(triggers, np.array([0], dtype=np.uint64))
    np.testing.assert_array_equal(photons, np.array([20], dtype=np.uint64))
