# -*- coding: utf-8 -*-
"""Regression tests for QdyneLogic itself.

These drive the real methods off a stub rather than a live module: QdyneLogic is a LogicBase with
Connectors, so instantiating it needs qudi's module manager. Everything exercised here reads only
from attributes the stub provides.
"""
import logging
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
    estimator_stg = types.SimpleNamespace(
        dump_as_dict=lambda: {}, current_method='TimeTag', current_mode='custom'
    )
    analyzer_stg = types.SimpleNamespace(
        dump_as_dict=lambda: {}, current_method='Fourier', current_mode='default'
    )
    return types.SimpleNamespace(
        log=logging.getLogger('test_qdyne_logic'),
        measurement_generator=types.SimpleNamespace(
            generation_parameters=generation_parameters,
            counter_settings={'bin_width': 1e-9},
            measurement_settings={'sequence_length': 1e-6},
        ),
        settings=types.SimpleNamespace(estimator_stg=estimator_stg, analyzer_stg=analyzer_stg),
        fit=types.SimpleNamespace(
            fit_config_model=types.SimpleNamespace(
                dump_configs=lambda: [{'name': 'Lorentzian Peak'}]
            )
        ),
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


def _measurement_generator_stub():
    """Enough of a MeasurementGenerator for set_measurement_settings() to run unbound."""
    recorded = {}

    def remember(target):
        def set_single_value(name, value):
            recorded.setdefault(target, {})[name] = value
        return set_single_value

    stub = types.SimpleNamespace(
        log=types.SimpleNamespace(warning=lambda *a, **k: None, debug=lambda *a, **k: None),
        _invoke_settings=False,
        _pulsedmasterlogic=None,
        _qdyne_logic=types.SimpleNamespace(
            settings=types.SimpleNamespace(
                estimator_stg=types.SimpleNamespace(set_single_value=remember('estimator')),
                analyzer_stg=types.SimpleNamespace(set_single_value=remember('analyzer')),
            ),
            data=types.SimpleNamespace(metadata=types.SimpleNamespace(measurement_settings={})),
            sigMeasurementSettingsUpdated=types.SimpleNamespace(emit=lambda *a: None),
        ),
    )
    # Name-mangled private attribute the method reads for its fallback.
    setattr(stub, '_MeasurementGenerator__sequence_length', 1e-6)
    return stub, recorded


def test_bin_width_is_forwarded_to_the_estimator_settings():
    """The guard read "_bin_width" while the body read "bin_width", so it never fired for the key
    callers actually pass - the estimator's bin_width was never kept in step with the measurement
    settings. Fixed in a06c0748 but left unpinned until now."""
    from qudi.logic.qdyne.qdyne_logic import MeasurementGenerator

    stub, recorded = _measurement_generator_stub()

    MeasurementGenerator.set_measurement_settings(stub, {'bin_width': 2e-9})

    assert recorded.get('estimator', {}).get('bin_width') == 2e-9


def test_sequence_length_is_forwarded_to_both_estimator_and_analyzer():
    from qudi.logic.qdyne.qdyne_logic import MeasurementGenerator

    stub, recorded = _measurement_generator_stub()

    MeasurementGenerator.set_measurement_settings(stub, {'sequence_length': 5e-6})

    assert recorded.get('estimator', {}).get('sequence_length') == 5e-6
    assert recorded.get('analyzer', {}).get('sequence_length') == 5e-6


def test_settings_dicts_are_copied_into_metadata():
    """The metadata used to hold the caller's own dict, so anything they did to it afterwards
    silently rewrote what would be saved."""
    from qudi.logic.qdyne.qdyne_logic import MeasurementGenerator

    stub, _recorded = _measurement_generator_stub()
    caller_dict = {'sequence_length': 5e-6}

    MeasurementGenerator.set_measurement_settings(stub, caller_dict)
    caller_dict['sequence_length'] = 999

    assert stub._qdyne_logic.data.metadata.measurement_settings['sequence_length'] == 5e-6


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
        '_current_estimator_method',
        '_current_estimator_mode',
        '_current_analyzer_method',
        '_current_analyzer_mode',
        '_fit_configs',
    ):
        assert hasattr(stub, attr), f'{attr} was never assigned'


def test_saving_status_variables_records_the_selected_method_and_mode():
    """These four StatusVars are read at activation to restore the user's selection and used to be
    assigned nowhere, so qudi kept re-persisting whatever had been loaded and the module always
    reopened on TimeTag/default. The per-method settings WERE saved, which is what made it look as
    though the settings had been lost when only the selection had."""
    stub = _logic_stub({})

    QdyneLogic._save_status_variables(stub)

    assert stub._current_estimator_method == 'TimeTag'
    assert stub._current_estimator_mode == 'custom'
    assert stub._current_analyzer_method == 'Fourier'
    assert stub._current_analyzer_mode == 'default'


def test_saving_status_variables_records_the_fit_configurations():
    """fit_configs was loaded at activation into QdyneFit and never written back, so every fit
    configuration a user added was lost on deactivate. Both sibling modules declaring the same
    StatusVar - odmr_logic and pulsed_measurement_logic - persist theirs."""
    stub = _logic_stub({})

    QdyneLogic._save_status_variables(stub)

    assert stub._fit_configs == [{'name': 'Lorentzian Peak'}]


def test_one_unreachable_source_does_not_cost_the_other_status_variables():
    """Each source is guarded on its own. A connector that has already gone away - reading
    generation_parameters through pulsedmasterlogic, say - must cost only its own StatusVar."""
    stub = _logic_stub({})

    class _Dead:
        @property
        def generation_parameters(self):
            raise RuntimeError('pulsed logic is gone')

        counter_settings = {'bin_width': 1e-9}
        measurement_settings = {'sequence_length': 1e-6}

    stub.measurement_generator = _Dead()

    QdyneLogic._save_status_variables(stub)

    assert not hasattr(stub, '_measurement_generator_dict')
    assert stub._counter_settings_dict == {'bin_width': 1e-9}
    assert stub._current_estimator_method == 'TimeTag'
    assert stub._fit_configs == [{'name': 'Lorentzian Peak'}]


def test_non_positive_settings_are_rejected_instead_of_raising():
    """The settings containers are frozen dataclasses that raise ValueError on a non-positive
    sequence_length. set_measurement_settings() is driven from on_activate() with whatever the
    status file holds, so an uncaught raise here stopped the module activating at all - and, since
    the bad value is itself persisted, kept stopping it."""
    from qudi.logic.qdyne.qdyne_logic import MeasurementGenerator

    stub, recorded = _measurement_generator_stub()

    MeasurementGenerator.set_measurement_settings(stub, {'sequence_length': 0.0})
    MeasurementGenerator.set_measurement_settings(stub, {'sequence_length': 'nonsense'})
    MeasurementGenerator.set_measurement_settings(stub, {'bin_width': -1.0})

    assert 'sequence_length' not in recorded.get('estimator', {})
    assert 'bin_width' not in recorded.get('estimator', {})


def test_caller_settings_dict_is_not_mutated():
    """set_measurement_settings() writes derived values back into the dict it is given. At
    activation that dict IS the StatusVar, so applying the saved settings rewrote them."""
    from qudi.logic.qdyne.qdyne_logic import MeasurementGenerator

    stub, _recorded = _measurement_generator_stub()
    caller_dict = {'sequence_length': 5e-6, 'bin_width': '2e-9'}
    before = dict(caller_dict)

    MeasurementGenerator.set_measurement_settings(stub, caller_dict)

    assert caller_dict == before
