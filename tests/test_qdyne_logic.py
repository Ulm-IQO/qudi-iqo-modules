# -*- coding: utf-8 -*-
"""Regression tests for QdyneLogic itself.

These drive the real methods off a stub rather than a live module: QdyneLogic is a LogicBase with
Connectors, so instantiating it needs qudi's module manager. Everything exercised here reads only
from attributes the stub provides.
"""
import types

import pytest

from qudi.logic.qdyne.qdyne_logic import QdyneLogic


def _config_options(cls):
    """Map config key -> attribute names declaring it."""
    keys = {}
    for attr_name, value in vars(cls).items():
        if value.__class__.__name__ == 'ConfigOption':
            keys.setdefault(getattr(value, 'name', attr_name), []).append(attr_name)
    return keys


def test_no_two_config_options_claim_the_same_key():
    """default_analyzer_method used to declare name='analyzer_method', the same key as the
    analyzer_method option. Both attributes then read one config value and whichever descriptor
    resolved last won. It failed silently because both use missing='nothing' - which is why the
    startup log warns about estimator_method and default_estimator_method but never the analyzer
    pair."""
    collisions = {key: attrs for key, attrs in _config_options(QdyneLogic).items() if len(attrs) > 1}

    assert not collisions, f'ConfigOption key collision: {collisions}'


def test_every_config_option_key_matches_its_attribute_name():
    """Not required by qudi, but a mismatch is what allowed the collision above to hide."""
    mismatched = {
        attrs[0]: key for key, attrs in _config_options(QdyneLogic).items() if attrs[0] != key
    }

    assert not mismatched, f'ConfigOption name does not match its attribute: {mismatched}'


def _logic_stub(generation_parameters):
    """Minimal stand-in exposing exactly what _save_status_variables() reads."""
    stg = types.SimpleNamespace(dump_as_dict=lambda: {})
    return types.SimpleNamespace(
        measurement_generator=types.SimpleNamespace(
            generation_parameters=generation_parameters,
            counter_settings={'bin_width': 1e-9},
            measurement_settings={'sequence_length': 1e-6},
        ),
        settings=types.SimpleNamespace(estimator_stg=stg, analyzer_stg=stg),
    )


def test_saving_status_variables_survives_missing_is_gated():
    """is_gated is deliberately kept out of the persisted generation parameters, but it is not
    guaranteed to be present. A bare pop() raised KeyError out of on_deactivate(), which aborted
    shutdown before any of the remaining status variables were saved - so a crash here silently
    cost the user every other setting too."""
    stub = _logic_stub({'laser_channel': 'd_ch1'})   # no is_gated

    QdyneLogic._save_status_variables(stub)

    assert stub._measurement_generator_dict == {'laser_channel': 'd_ch1'}
    assert stub._counter_settings_dict == {'bin_width': 1e-9}
    assert stub._measurement_settings_dict == {'sequence_length': 1e-6}


def test_saving_status_variables_strips_is_gated_when_present():
    stub = _logic_stub({'laser_channel': 'd_ch1', 'is_gated': True})

    QdyneLogic._save_status_variables(stub)

    assert 'is_gated' not in stub._measurement_generator_dict
    assert stub._measurement_generator_dict == {'laser_channel': 'd_ch1'}


def test_saving_status_variables_reaches_the_later_assignments():
    """The point of the fix: a KeyError on the first line used to skip everything after it."""
    stub = _logic_stub({})

    QdyneLogic._save_status_variables(stub)

    for attr in (
        '_measurement_generator_dict',
        '_counter_settings_dict',
        '_measurement_settings_dict',
        '_estimator_stg_dict',
        '_analyzer_stg_dict',
    ):
        assert hasattr(stub, attr), f'{attr} was never assigned'
