import numpy as np

from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    AnalysisParameters,
    DataStashCache,
    ExecutionState,
    ExtractionParameters,
)


def test_execution_state_tracks_pause_and_timing():
    # elapsed_sweeps is not part of ExecutionState - it's tracked exclusively on
    # PulsedMeasurementData (the acquired-data side), not here (the timing/pause-state side).
    state = ExecutionState()
    state.start()
    assert state.is_paused is False
    # Not `== 0.0`: elapsed_time is `time.time() - start_time`, and on Windows Python 3.13+ switched
    # time.time() to GetSystemTimePreciseAsFileTime (100 ns resolution, was 15.625 ms). Two calls
    # either side of start() used to return the identical value and now usually do not, so the exact
    # comparison passed on 3.10 and fails on 3.14. Assert it is merely near zero instead.
    assert 0.0 <= state.elapsed_time < 0.1

    state.pause()
    assert state.is_paused is True

    state.unpause()
    assert state.is_paused is False


def test_data_stash_cache_round_trip():
    cache = DataStashCache()
    data = np.array([1, 2, 3], dtype=np.int64)
    cache.stash('tag', data, sweeps=5, time_elapsed=1.25)

    recalled = cache.recall('tag')
    assert recalled is not None
    assert recalled['elapsed_sweeps'] == 5
    assert recalled['elapsed_time'] == 1.25
    assert np.array_equal(recalled['data'], data)


def test_analysis_and_extraction_settings_round_trip():
    analysis = AnalysisParameters.from_dict({'foo': 'bar'})
    extraction = ExtractionParameters.from_dict({'baz': 3})

    assert analysis.to_dict() == {'foo': 'bar'}
    assert extraction.to_dict() == {'baz': 3}
