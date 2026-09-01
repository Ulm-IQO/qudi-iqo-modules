# -*- coding: utf-8 -*-
"""Tests for the qdyne data containers.

The two accumulation tests are equivalence proofs, not behaviour tests: the growable buffer and the
incremental histogram exist purely to remove quadratic cost, so the bar is that they produce results
*identical* to the straightforward implementations they replace.
"""
import datetime

import numpy as np
import pytest

from qudi.logic.qdyne.qdyne_data.growable_array import GrowableArray
from qudi.logic.qdyne.qdyne_data.measurement_data import (
    DATA_TYPES,
    MainDataClass,
    MeasurementChunk,
    QDyneMetadata,
)
from qudi.logic.qdyne.qdyne_data.save_options import QdyneSaveOptions


# --------------------------------------------------------------------------- GrowableArray


def test_growable_array_matches_np_append():
    """The equivalence that matters: same chunks in, same array out."""
    chunks = [np.arange(i, i + 7, dtype=np.int64) for i in range(0, 70, 7)]

    buffer = GrowableArray(dtype=np.int64)
    reference = np.array([], dtype=np.int64)
    for chunk in chunks:
        buffer.append(chunk)
        reference = np.append(reference, chunk)

    assert buffer.view.tolist() == reference.tolist()
    assert buffer.view.dtype == np.int64
    assert len(buffer) == reference.size


def test_growable_array_view_is_a_view_not_a_copy():
    buffer = GrowableArray(dtype=np.int64)
    buffer.append([1, 2, 3])

    view = buffer.view
    view[0] = 99

    assert buffer.view[0] == 99, 'view should alias the buffer, not copy it'


def test_growable_array_grows_by_doubling():
    buffer = GrowableArray(dtype=np.int64, initial_capacity=4)
    buffer.append(np.arange(10))

    assert len(buffer) == 10
    assert buffer.capacity >= 10
    # Capacity doubles rather than growing to fit exactly, which is what makes appending amortised
    # constant rather than linear.
    assert buffer.capacity in (16, 32)


def test_growable_array_empty_and_replace_and_clear():
    buffer = GrowableArray(dtype=np.int64)
    assert buffer.view.tolist() == []

    buffer.append([])
    assert len(buffer) == 0

    buffer.append([1, 2, 3])
    buffer.replace([9, 9])
    assert buffer.view.tolist() == [9, 9]

    capacity_before = buffer.capacity
    buffer.clear()
    assert len(buffer) == 0
    assert buffer.capacity == capacity_before, 'clear() should keep the allocation'


def test_growable_array_works_with_numpy_functions():
    buffer = GrowableArray(dtype=np.int64)
    buffer.append([1, 2, 3, 4])

    assert np.asarray(buffer).tolist() == [1, 2, 3, 4]
    assert np.mean(buffer) == 2.5


# --------------------------------------------------------------------------- MainDataClass


def test_accumulated_arrays_read_as_plain_numpy():
    """Storage, analysis and the GUI all treat these as ordinary arrays; the buffer is internal."""
    data = MainDataClass()
    data.append_raw_data([1, 2, 3])
    data.append_raw_data([4, 5])

    assert isinstance(data.raw_data, np.ndarray)
    assert data.raw_data.tolist() == [1, 2, 3, 4, 5]


def test_assigning_an_array_replaces_the_contents():
    """load_data() does setattr(self.data, data_type, loaded), so assignment has to keep working."""
    data = MainDataClass()
    data.append_raw_data([1, 2, 3])

    data.raw_data = np.array([7, 8])

    assert data.raw_data.tolist() == [7, 8]


def test_incremental_pulse_histogram_matches_rehistogramming_everything():
    """The other equivalence proof. A histogram is additive, so summing per-chunk counts must equal
    histogramming the whole concatenated stream - which is what the loop used to do on every tick,
    over the entire accumulated history."""
    rng = np.random.default_rng(1234)
    chunks = [rng.integers(1, 50, size=200) for _ in range(6)]
    bins, value_range = 50, (1, 50)

    data = MainDataClass()
    for chunk in chunks:
        counts, _edges = np.histogram(chunk, bins=bins, range=value_range)
        total = data.add_pulse_counts(counts)

    reference, _edges = np.histogram(np.concatenate(chunks), bins=bins, range=value_range)

    assert total.tolist() == reference.tolist()


def test_resetting_pulse_counts_starts_over():
    data = MainDataClass()
    data.add_pulse_counts(np.array([1, 2, 3]))
    data.reset_pulse_counts()

    assert data.add_pulse_counts(np.array([5, 5, 5])).tolist() == [5, 5, 5]


def test_reset_clears_the_accumulated_data():
    data = MainDataClass()
    data.append_raw_data([1, 2, 3])
    data.append_time_trace([4, 5])
    data.add_pulse_counts(np.array([1, 1]))
    data.metadata = QDyneMetadata(analysis_method='Fourier')

    data.reset()

    assert data.raw_data.tolist() == []
    assert data.time_trace.tolist() == []
    assert data.pulse_counts is None
    assert data.metadata.analysis_method == ''


def test_data_types_is_defined_once():
    """It used to be declared separately on DataManagerSettings and QdyneDataManager."""
    from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings, QdyneDataManager

    assert MainDataClass().data_types is DATA_TYPES
    assert DataManagerSettings.data_types is DATA_TYPES
    assert QdyneDataManager.data_types is DATA_TYPES


def test_data_types_are_all_real_attributes():
    """Guards against a data type being saved that the container cannot supply."""
    data = MainDataClass()
    for data_type in DATA_TYPES:
        assert hasattr(data, data_type), data_type


def test_measurement_chunk_is_distinct_from_accumulated_data():
    chunk = MeasurementChunk()

    assert not hasattr(chunk, 'freq_domain')
    assert not hasattr(chunk, 'metadata')
    assert chunk.info == {}


# --------------------------------------------------------------------------- metadata


def test_metadata_tolerates_schema_drift():
    restored = QDyneMetadata.from_dict(
        {'analysis_method': 'Fourier', 'key_from_another_version': 1}
    )

    assert restored.analysis_method == 'Fourier'
    assert not hasattr(restored, 'key_from_another_version')
    assert QDyneMetadata.from_dict(None) == QDyneMetadata()


def test_metadata_round_trips():
    original = QDyneMetadata(analysis_method='Fourier', elapsed_sweeps=17, counter_hardware='dummy')

    assert QDyneMetadata.from_dict(original.to_dict()) == original


def test_metadata_carries_provenance():
    """A saved measurement previously recorded nothing about when it ran or on what."""
    for field_name in ('start_time', 'elapsed_sweeps', 'elapsed_time', 'counter_hardware'):
        assert hasattr(QDyneMetadata(), field_name)


# --------------------------------------------------------------------------- save options


def test_save_options_do_not_share_a_timestamp():
    """`timestamp = datetime.datetime.now()` as a field default is evaluated once at import, so
    every save without an explicit timestamp was stamped with process start."""
    first = QdyneSaveOptions()
    second = QdyneSaveOptions()

    assert isinstance(first.timestamp, datetime.datetime)
    # Distinct objects, and recent - not frozen at module import.
    assert (datetime.datetime.now() - first.timestamp).total_seconds() < 5
    assert first.timestamp is not second.timestamp


def test_save_options_do_not_share_metadata():
    first = QdyneSaveOptions()
    second = QdyneSaveOptions()

    first.metadata['only_mine'] = True

    assert 'only_mine' not in second.metadata


def test_accumulation_scales_linearly_not_quadratically():
    """Guards the reason GrowableArray exists.

    Nothing in a correctness test can see a quadratic path - it just gets slower. Appending 10x as
    many chunks should cost roughly 10x, not 100x. The threshold is deliberately loose (25x) so a
    noisy machine does not fail the build; np.append on this workload lands near 100x, so the two
    are nowhere near each other.
    """
    import time

    def elapsed_for(chunk_count):
        chunk = np.arange(256, dtype=np.int64)
        buffer = GrowableArray(dtype=np.int64)
        start = time.perf_counter()
        for _ in range(chunk_count):
            buffer.append(chunk)
        return time.perf_counter() - start, len(buffer)

    small_time, small_len = elapsed_for(200)
    large_time, large_len = elapsed_for(2000)

    assert large_len == 10 * small_len
    # Guard against a zero measurement on a fast machine making the ratio meaningless.
    if small_time < 1e-4:
        pytest.skip('timer resolution too coarse for a meaningful ratio here')
    assert large_time / small_time < 25, (
        f'appending 10x the chunks took {large_time / small_time:.1f}x the time - '
        f'that looks quadratic again'
    )


def test_data_storage_save_data_has_no_mutable_default():
    """`options=QdyneSaveOptions()` in the signature was one shared object across every call."""
    import inspect

    from qudi.logic.qdyne.qdyne_data_manager import DataStorage

    default = inspect.signature(DataStorage.save_data).parameters['options'].default
    assert default is None
