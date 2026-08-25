# -*- coding: utf-8 -*-
"""Regression tests for the qdyne data manager's metadata handling."""
import numpy as np
import pytest

from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings, QdyneDataManager
from qudi.logic.qdyne.qdyne_dataclass import MainDataClass, QDyneMetadata


@pytest.fixture
def manager(tmp_path):
    return QdyneDataManager(MainDataClass(), DataManagerSettings(str(tmp_path)))


def test_metadata_with_an_unknown_key_still_loads(manager):
    """QDyneMetadata(**metadata) raised TypeError for any key the current schema does not declare,
    and a blanket `except Exception` swallowed it - so self.data.metadata kept its previous value
    and QdyneLogic.load_data() went on to read the *old* estimator/analyzer method out of it."""
    restored = manager._metadata_from_dict(
        {
            'analysis_method': 'Fourier',
            'state_estimation_method': 'TimeTag',
            'key_from_a_different_version': 123,
        }
    )

    assert isinstance(restored, QDyneMetadata)
    assert restored.analysis_method == 'Fourier'
    assert restored.state_estimation_method == 'TimeTag'
    assert not hasattr(restored, 'key_from_a_different_version')


def test_metadata_missing_keys_fall_back_to_defaults(manager):
    restored = manager._metadata_from_dict({'analysis_method': 'Fourier'})

    assert restored.analysis_method == 'Fourier'
    # Everything absent takes its declared default rather than raising.
    assert restored.state_estimation_method == QDyneMetadata().state_estimation_method
    assert restored.analysis_settings == {}


def test_metadata_from_a_non_dict_is_survivable(manager):
    assert manager._metadata_from_dict(None) == QDyneMetadata()


def test_load_data_applies_metadata_to_the_shared_data_object(manager, tmp_path):
    """The round trip the previous tests protect: metadata read off a real saved file must land on
    the MainDataClass the rest of the logic reads through."""
    manager.settings.set_nametag_all('run')
    manager.data.time_trace = np.arange(8, dtype=float)
    manager.data.metadata = QDyneMetadata(analysis_method='Fourier', state_estimation_method='TimeTag')
    manager.save_data('time_trace')

    # NpyDataStorage writes <name>.npy alongside <name>_metadata.txt; load_data wants the .npy.
    saved = next(p for p in tmp_path.rglob('*.npy'))
    manager.data.metadata = QDyneMetadata()  # wipe, so a failed load is visible

    manager.load_data('time_trace', str(saved))

    assert manager.data.metadata.analysis_method == 'Fourier'
    assert manager.data.metadata.state_estimation_method == 'TimeTag'
    assert np.array_equal(manager.data.time_trace, np.arange(8, dtype=float))
