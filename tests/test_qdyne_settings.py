# -*- coding: utf-8 -*-
"""Tests for the qdyne settings containers and the settings mediator.

The bug-pinning tests here survived the mediator rewrite: they were written against the old
three-level QObject chain and now assert the same behaviours against the plain replacement. That is
the point of keeping them - the rewrite has to preserve the fixes, not just the features.

Nothing in here needs a QApplication, because nothing in the settings layer is a QObject any more.
"""
import dataclasses

import pytest

from qudi.logic.qdyne.qdyne_data.analyzer_settings import FourierAnalyzerSettings
from qudi.logic.qdyne.qdyne_data.estimator_settings import TimeTagStateEstimatorSettings
from qudi.logic.qdyne.qdyne_data.settings_base import QdyneSettingsBase
from qudi.logic.qdyne.tools.settings_mediator import DEFAULT_MODE, SettingsMediator


@pytest.fixture
def mediator():
    return SettingsMediator({'TimeTag': TimeTagStateEstimatorSettings})


# --------------------------------------------------------------------------- settings dataclasses


def test_settings_are_frozen():
    """Frozen so a settings object shared between an estimator, a widget and a saved snapshot
    cannot be edited through any one of them."""
    settings = TimeTagStateEstimatorSettings(sig_start=1e-6)

    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.sig_start = 2e-6


def test_to_dict_persists_hidden_fields_but_display_dict_hides_them():
    """`exclude` means "do not show this in the widget". It used to also mean "never save this",
    because one to_dict() served both jobs - so `weight` could be set from a script and then
    vanished on the next save, which is most of why WeightedAverage was unusable."""
    settings = TimeTagStateEstimatorSettings(weight=[1.0, 2.0, 3.0])

    assert settings.to_dict()['weight'] == [1.0, 2.0, 3.0]
    assert 'weight' not in settings.to_display_dict()
    # Everything else appears in both.
    assert 'sig_start' in settings.to_display_dict()


def test_from_dict_is_tolerant_and_coercing():
    restored = TimeTagStateEstimatorSettings.from_dict(
        {'sig_end': '5e-6', 'count_mode': 'WeightedAverage', 'gone_in_a_refactor': 1}
    )

    assert restored.sig_end == 5e-6                 # str coerced to float
    assert isinstance(restored.sig_end, float)
    assert restored.count_mode == 'WeightedAverage'
    assert not hasattr(restored, 'gone_in_a_refactor')
    assert restored.bin_width == TimeTagStateEstimatorSettings().bin_width  # missing -> default


def test_from_dict_survives_a_non_dict():
    assert TimeTagStateEstimatorSettings.from_dict(None) == TimeTagStateEstimatorSettings()


def test_update_from_dict_returns_a_new_instance():
    original = TimeTagStateEstimatorSettings(sig_start=1.0)

    updated = original.update_from_dict({'sig_start': 9.0})

    assert original.sig_start == 1.0
    assert updated.sig_start == 9.0
    assert original is not updated


def test_uncoercible_value_falls_back_instead_of_raising():
    """These objects are built from status files; one bad entry must not take the whole settings
    object down."""
    restored = TimeTagStateEstimatorSettings.from_dict({'sig_start': 'not-a-number'})

    assert restored.sig_start == TimeTagStateEstimatorSettings().sig_start


def test_analyzer_and_estimator_agree_on_sequence_length_units():
    """AnalyzerSettings.sequence_length defaulted to a bare 1 (one second) while the estimator used
    1e-9 for the same physical quantity - nine orders of magnitude apart."""
    assert FourierAnalyzerSettings().sequence_length == TimeTagStateEstimatorSettings().sequence_length


def test_selectable_values_live_next_to_the_field():
    assert TimeTagStateEstimatorSettings().count_mode in TimeTagStateEstimatorSettings.COUNT_MODES
    assert FourierAnalyzerSettings().spectrum_type in FourierAnalyzerSettings.SPECTRUM_TYPES
    # Class constants must not become dataclass fields.
    field_names = {f.name for f in dataclasses.fields(TimeTagStateEstimatorSettings)}
    assert 'COUNT_MODES' not in field_names


# --------------------------------------------------------------------------- mediator


def test_add_mode_applies_the_settings_it_is_given(mediator):
    """add_mode() accepted a `setting` argument and then ignored it, always copying the default
    mode. QdyneLogic.load_data() passes settings read back from a saved file through exactly that
    argument, so loaded settings were silently replaced by defaults - the cause of the
    "Why are settings not correctly updated after loading anymore" commit."""
    loaded = TimeTagStateEstimatorSettings(
        name='from_file', sig_start=1.23e-6, sig_end=4.56e-6, count_mode='WeightedAverage'
    )

    mediator.add_mode('loaded', True, loaded)

    stored = mediator.current_data
    assert stored.sig_start == 1.23e-6
    assert stored.sig_end == 4.56e-6
    assert stored.count_mode == 'WeightedAverage'
    assert stored.name == 'loaded'          # named for the slot, not the object handed in


def test_add_mode_without_a_setting_falls_back_to_the_default(mediator):
    mediator.add_mode('blank', True, None)

    assert mediator.current_data.sig_start == TimeTagStateEstimatorSettings().sig_start


def test_load_tolerates_a_method_with_no_saved_entry():
    """A method present in the code but absent from the status file used to raise KeyError and
    abort the whole load, leaving every method unconfigured - what happens whenever an estimator is
    added after a status file was written."""
    med = SettingsMediator(
        {'TimeTag': TimeTagStateEstimatorSettings, 'AddedLater': TimeTagStateEstimatorSettings}
    )

    med.load_from_dict({'TimeTag': {DEFAULT_MODE: {'name': DEFAULT_MODE}}})

    assert sorted(med.method_list) == ['AddedLater', 'TimeTag']
    assert DEFAULT_MODE in med.method_dict['AddedLater']


def test_load_drops_saved_fields_that_no_longer_exist(mediator):
    mediator.load_from_dict(
        {'TimeTag': {DEFAULT_MODE: {'sig_end': 7.0, 'field_removed_in_a_refactor': 1}}}
    )
    mediator.set_method('TimeTag')

    assert mediator.current_data.sig_end == 7.0


def test_load_ignores_settings_for_an_unknown_method(mediator):
    mediator.load_from_dict({'TimeTag': {DEFAULT_MODE: {}}, 'NoSuchMethod': {DEFAULT_MODE: {}}})

    assert 'NoSuchMethod' not in mediator.method_list


def test_dump_and_load_round_trip(mediator):
    mediator.set_method('TimeTag')
    mediator.add_mode('loaded', True, TimeTagStateEstimatorSettings(sig_start=7e-6, weight=[1.0, 1.0]))

    restored = SettingsMediator({'TimeTag': TimeTagStateEstimatorSettings})
    restored.load_from_dict(mediator.dump_as_dict())
    restored.set_method('TimeTag')
    restored.set_mode('loaded')

    assert restored.current_data.sig_start == 7e-6
    assert restored.current_data.weight == [1.0, 1.0]   # the hidden field survives the round trip


def test_delete_mode_falls_back_to_default(mediator):
    mediator.set_method('TimeTag')
    mediator.add_mode('extra', True, None)

    mediator.delete_mode('extra')

    assert 'extra' not in mediator.mode_list
    assert mediator.current_mode == DEFAULT_MODE


def test_default_mode_cannot_be_deleted(mediator):
    mediator.set_method('TimeTag')

    mediator.delete_mode(DEFAULT_MODE)

    assert DEFAULT_MODE in mediator.mode_list


def test_observers_are_notified(mediator):
    events = []
    mediator.subscribe(
        on_data=lambda payload: events.append('data'),
        on_mode=lambda payload: events.append('mode'),
        on_method=lambda payload: events.append('method'),
        on_renewed=lambda payload: events.append('renewed'),
    )

    mediator.set_method('TimeTag')
    assert 'method' in events and 'renewed' in events

    events.clear()
    mediator.set_values({'sig_start': 3e-6})
    assert events == ['data']
    assert mediator.current_data.sig_start == 3e-6


def test_sync_values_does_not_notify(mediator):
    """Values coming FROM the widget must not be echoed back at it, or widget and store ping-pong."""
    mediator.set_method('TimeTag')
    events = []
    mediator.subscribe(on_data=lambda payload: events.append(payload))

    mediator.sync_values({'sig_start': 4e-6})

    assert events == []
    assert mediator.current_data.sig_start == 4e-6


def test_a_raising_observer_does_not_break_the_mediator(mediator):
    mediator.set_method('TimeTag')
    survivors = []
    mediator.subscribe(on_data=lambda payload: (_ for _ in ()).throw(RuntimeError('boom')))
    mediator.subscribe(on_data=lambda payload: survivors.append(payload))

    mediator.set_values({'sig_start': 5e-6})

    assert survivors, 'a misbehaving observer stopped the others'
    assert mediator.current_data.sig_start == 5e-6


def test_set_single_value_rejects_an_unknown_parameter(mediator):
    mediator.set_method('TimeTag')
    before = mediator.current_data

    mediator.set_single_value('no_such_parameter', 1)

    assert mediator.current_data == before


def test_settings_classes_come_from_the_registry():
    """QdyneSettings builds its mediators from the method registries, so every method it offers has
    an implementation behind it."""
    from qudi.logic.qdyne.qdyne_state_estimator import ESTIMATORS
    from qudi.logic.qdyne.qdyne_time_trace_analyzer import ANALYZERS

    for registry in (ESTIMATORS, ANALYZERS):
        for name in registry.names:
            assert issubclass(registry.settings_class(name), QdyneSettingsBase)
            assert registry.implementation(name) is not None
