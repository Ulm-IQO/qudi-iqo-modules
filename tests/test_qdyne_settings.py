# -*- coding: utf-8 -*-
"""Regression tests for the qdyne settings mediator chain.

Every test here pins a bug that was live before this file existed - each one fails against the
previous implementation. The mediators are QObjects, so each needs a parent that outlives it;
`parent` returns one and keeps a reference, because a garbage-collected parent makes Qt raise
"Signal source has been deleted" on the next emit.
"""
import pytest
from PySide6.QtCore import QObject

from qudi.logic.qdyne.tools.settings_dataclass import SettingsMediator
from qudi.logic.qdyne.tools.multi_settings_dataclass import MultiSettingsMediator
from qudi.logic.qdyne.qdyne_state_estimator import TimeTagStateEstimatorSettings


@pytest.fixture
def parent():
    holder = []

    def make():
        obj = QObject()
        holder.append(obj)
        return obj

    yield make


@pytest.fixture
def mediator(parent):
    med = MultiSettingsMediator(parent())
    med.create_default({'TimeTag': TimeTagStateEstimatorSettings})
    med.set_method('TimeTag')
    return med


def test_add_mode_applies_the_settings_it_is_given(mediator):
    """add_mode() accepted a `setting` argument and then ignored it, always deep-copying the
    default mode. QdyneLogic.load_data() passes the settings read back from a saved file in through
    exactly that argument, so loaded settings were silently replaced by defaults - the cause of the
    "Why are settings not correctly updated after loading anymore" commit."""
    loaded = TimeTagStateEstimatorSettings(
        name='from_file', sig_start=1.23e-6, sig_end=4.56e-6, count_mode='WeightedAverage'
    )

    mediator.add_mode('loaded', True, loaded)

    stored = mediator.method_dict['TimeTag']['loaded']
    assert stored.sig_start == 1.23e-6
    assert stored.sig_end == 4.56e-6
    assert stored.count_mode == 'WeightedAverage'
    # The mode is still named after the slot it occupies, not after the object handed in.
    assert stored.name == 'loaded'
    # And it is a copy - mutating the caller's object must not reach into the mediator.
    loaded.sig_start = 9.9
    assert stored.sig_start == 1.23e-6


def test_add_mode_without_a_setting_still_falls_back_to_the_default(mediator):
    mediator.add_mode('blank', True, None)

    stored = mediator.method_dict['TimeTag']['blank']
    assert stored.sig_start == TimeTagStateEstimatorSettings().sig_start
    assert stored.count_mode == TimeTagStateEstimatorSettings().count_mode


def test_load_from_dict_tolerates_a_method_with_no_saved_entry(parent):
    """A method present in the code but absent from the status file used to raise KeyError and
    abort the whole load, leaving every method unconfigured - which is what happens whenever a new
    estimator is added after a status file was written."""
    med = MultiSettingsMediator(parent())

    med.load_from_dict(
        {'TimeTag': TimeTagStateEstimatorSettings, 'Added_Later': TimeTagStateEstimatorSettings},
        {'TimeTag': {'default': {'name': 'default'}}},
    )

    assert sorted(med.method_dict) == ['Added_Later', 'TimeTag']
    # The unsaved method is created with defaults rather than left missing.
    assert 'default' in med.method_dict['Added_Later']


def test_load_from_dict_drops_saved_fields_that_no_longer_exist(parent):
    """A saved key that is no longer a dataclass field went straight into __init__ as **kwargs and
    raised TypeError, aborting the load."""
    med = MultiSettingsMediator(parent())

    med.load_from_dict(
        {'TimeTag': TimeTagStateEstimatorSettings},
        {'TimeTag': {'default': {'name': 'd', 'sig_end': 7.0, 'field_removed_in_a_refactor': 1}}},
    )

    restored = med.method_dict['TimeTag']['default']
    assert restored.sig_end == 7.0          # the still-valid key survives
    assert not hasattr(restored, 'field_removed_in_a_refactor')


def test_data_container_setter_works(parent):
    """The setter assigned to `mode_dict`, which is a read-only property on this class, so it could
    never be used - it raised AttributeError instead."""
    med = SettingsMediator(parent())

    med.data_container = {'default': TimeTagStateEstimatorSettings(name='default')}

    assert sorted(med.mode_dict) == ['default']


def test_find_key_before_raises_keyerror_for_the_first_key():
    """delete_mode() guards this call with `except KeyError`, but deleting the first mode left
    `previous_key` unbound and raised UnboundLocalError straight past that guard."""
    with pytest.raises(KeyError):
        SettingsMediator.find_key_before({'default': 1, 'other': 2}, 'default')

    assert SettingsMediator.find_key_before({'default': 1, 'other': 2}, 'other') == 'default'


def test_delete_mode_falls_back_to_default_for_the_first_deletable_mode(mediator):
    """The end-to-end path the previous test protects: deleting a mode that has no predecessor."""
    mediator.add_mode('only_extra', True, None)
    assert 'only_extra' in mediator.mode_dict

    mediator.delete_mode('only_extra')

    assert 'only_extra' not in mediator.mode_dict
    assert mediator.current_mode in mediator.mode_dict
