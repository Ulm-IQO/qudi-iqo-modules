import numpy as np

from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    AnalysisSettings,
    DataStashCache,
    ExecutionState,
    ExtractionSettings,
)


def test_execution_state_tracks_pause_and_timing():
    state = ExecutionState()
    state.start()
    assert state.is_paused is False
    assert state.elapsed_time == 0.0
    assert state.elapsed_sweeps == 0

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
    analysis = AnalysisSettings.from_dict({'foo': 'bar'})
    extraction = ExtractionSettings.from_dict({'baz': 3})

    assert analysis.to_dict() == {'foo': 'bar'}
    assert extraction.to_dict() == {'baz': 3}
