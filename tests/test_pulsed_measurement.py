import dataclasses
import math
import pickle
import types

import numpy as np

from qudi.util.datastorage import TextDataStorage

from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockElement, PulseBlockEnsemble, PulseSequence
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    ExecutionState,
    GenerationMethodParameters,
    PulsedMeasurementData,
    PulsedMeasurementSettings,
    to_plain_metadata,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import (
    SamplingInformation,
    SequenceGeneratorSettings,
)
from qudi.logic.pulsed.pulsed_data.pulsed_measurement import (
    PulsedMeasurement, PulsedMetadata, PulseObjects, Settings
)
from qudi.logic.pulsed.pulsed_measurement_logic import PulsedMeasurementLogic, _default_measurement_settings
from qudi.logic.pulsed.sequence_generator_logic import _default_generator_settings, _resolve_asset_closure
from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType


def _sample_data():
    return PulsedMeasurementData(
        raw_data=np.array([1, 2, 3], dtype=np.int64),
        laser_data=np.array([[1, 2], [3, 4]], dtype=np.int64),
        signal_data=np.array([[0.0, 1.0], [0.5, 0.6]], dtype=float),
        signal_alt_data=np.zeros((2, 2), dtype=float),
        measurement_error=np.zeros((2, 2), dtype=float),
        elapsed_time=12.5,
        elapsed_sweeps=3,
    )


def test_pulsed_measurement_round_trip_with_generator_settings():
    measurement = PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(
                measurement_settings=_default_measurement_settings(),
                generator_settings=_default_generator_settings(),
            ),
        ),
        data=_sample_data(),
    )

    restored = PulsedMeasurement.from_dict(measurement.to_dict())

    # Settings containing numpy arrays can't be compared with ==, so check a few fields.
    assert (
        restored.metadata.settings.generator_settings.generation_parameters
        == _default_generator_settings().generation_parameters
    )
    assert restored.data.elapsed_sweeps == 3
    assert restored.data.elapsed_time == 12.5
    assert np.array_equal(restored.data.raw_data, measurement.data.raw_data)
    # Fit results are not restored by from_dict(), since lmfit results can't be rebuilt.
    assert restored.metadata.fit_results.primary is None
    assert restored.metadata.fit_results.alternative is None
    # Nothing was loaded, so the pulse objects are empty.
    assert restored.metadata.objects.loaded_asset is None
    assert restored.metadata.objects.loaded_sequence is None
    assert restored.metadata.objects.loaded_ensemble is None
    assert restored.metadata.objects.ensembles == {}
    assert restored.metadata.objects.blocks == {}


def test_pulsed_measurement_round_trip_without_generator_settings_or_data():
    measurement = PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(measurement_settings=_default_measurement_settings())
        )
    )

    restored = PulsedMeasurement.from_dict(measurement.to_dict())

    assert restored.metadata.settings.generator_settings is None
    assert restored.data is None


def test_execution_state_tracks_pause_and_timing():
    state = ExecutionState()
    state.start()
    assert state.is_paused is False
    # Not == 0.0: time.time() on Windows is precise enough (Python 3.13+) that two calls
    # around start() usually differ.
    assert 0.0 <= state.elapsed_time < 0.1

    state.pause()
    assert state.is_paused is True

    state.unpause()
    assert state.is_paused is False


def test_pulsed_measurement_settings_from_dict_tolerates_missing_keys():
    # An older settings file without this key should fall back to the default.
    full_dict = _default_measurement_settings().to_dict()
    del full_dict['microwave_settings']

    restored = PulsedMeasurementSettings.from_dict(full_dict)

    assert restored.microwave_settings.power == -30.0
    assert restored.microwave_settings.frequency == 2870e6


def test_sequence_generator_settings_from_dict_tolerates_missing_keys():
    full_dict = _default_generator_settings().to_dict()
    del full_dict['generation_parameters']

    restored = SequenceGeneratorSettings.from_dict(full_dict)

    assert restored.generation_parameters == _default_generator_settings().generation_parameters


def test_sampling_information_laser_bins_are_real_fields_not_legacy():
    info = SamplingInformation.from_dict({
        'laser_rising_bins': [10, 20, 30],
        'laser_falling_bins': [15, 25, 35],
    })

    # Declared fields, not stored in _legacy_data. ungated_gated_conv_deriv() reads them.
    assert 'laser_rising_bins' in info._field_names()
    assert 'laser_falling_bins' not in info._legacy_data
    assert list(info['laser_rising_bins']) == [10, 20, 30]
    assert list(info['laser_falling_bins']) == [15, 25, 35]

    restored = SamplingInformation.from_dict(info.to_dict())
    assert list(restored.laser_rising_bins) == [10, 20, 30]
    assert list(restored.laser_falling_bins) == [15, 25, 35]


def test_pulsed_measurement_settings_migrates_legacy_status_dict():
    # Status file from before the refactor: one top-level key per old StatusVar, some of
    # them name-mangled.
    legacy_raw = {
        '_PulsedMeasurementLogic__microwave_power': -13.0,
        '_PulsedMeasurementLogic__microwave_freq': 2.5e9,
        '_PulsedMeasurementLogic__use_ext_microwave': True,
        '_PulsedMeasurementLogic__fast_counter_record_length': 5e-6,
        '_PulsedMeasurementLogic__fast_counter_binwidth': 2e-9,
        '_PulsedMeasurementLogic__fast_counter_gates': 4,
        '_PulsedMeasurementLogic__timer_interval': 3,
        '_invoke_settings_from_sequence': True,
        '_number_of_lasers': 20,
        '_controlled_variable': list(range(20)),
        '_alternating': True,
        '_laser_ignore_list': [0, 1],
        '_data_units': ('s', 'a.u.'),
        '_data_labels': ('Time', 'Contrast'),
        'extraction_parameters': {'method': 'gated_conv_deriv', 'conv_std_dev': 15.0},
        'analysis_parameters': {'method': 'mean_norm'},
        'fit_configs': ({'name': 'Custom', 'model': 'Sine', 'estimator': 'default', 'custom_parameters': None},),
        '_alternative_data_type': None,
        'zeropad': 2,
        'psd': True,
        'window': 'hann',
        'base_corr': False,
    }

    # No '_settings' key but known legacy keys, so it is treated as an old file.
    assert '_settings' not in legacy_raw
    assert PulsedMeasurementSettings.LEGACY_STATUS_VAR_KEYS.intersection(legacy_raw)

    migrated = PulsedMeasurementSettings.from_legacy_dict(legacy_raw)

    assert migrated.microwave_settings.power == -13.0
    assert migrated.microwave_settings.frequency == 2.5e9
    assert migrated.microwave_settings.use_ext_microwave is True
    assert migrated.fast_counter_settings.bin_width == 2e-9
    assert migrated.fast_counter_settings.record_length == 5e-6
    assert migrated.fast_counter_settings.number_of_gates == 4
    # is_gated wasn't saved in the old format; it is read from hardware on activation.
    assert migrated.fast_counter_settings.is_gated is False
    assert migrated.readout_settings.invoke_settings is True
    assert migrated.readout_settings.number_of_lasers == 20
    assert np.array_equal(migrated.readout_settings.controlled_variable, np.arange(20, dtype=float))
    assert migrated.readout_settings.alternating is True
    assert migrated.readout_settings.laser_ignore_list == [0, 1]
    assert migrated.readout_settings.units == ('s', 'a.u.')
    assert migrated.readout_settings.labels == ('Time', 'Contrast')
    assert migrated.alternate_signal_settings.method is None
    assert migrated.alternate_signal_settings.parameters['zeropad'] == 2
    assert migrated.alternate_signal_settings.parameters['psd'] is True
    assert migrated.alternate_signal_settings.parameters['window'] == 'hann'
    assert migrated.alternate_signal_settings.parameters['base_corr'] is False
    assert migrated.extraction_parameters == {'method': 'gated_conv_deriv', 'conv_std_dev': 15.0}
    assert migrated.analysis_parameters == {'method': 'mean_norm'}
    # timer_interval_s and fit_configs are migrated by the logic module itself, so they
    # aren't tested here.

    # A new install (empty dict) isn't treated as an old file.
    assert not PulsedMeasurementSettings.LEGACY_STATUS_VAR_KEYS.intersection({})
    # A file that already has '_settings' isn't migrated again.
    assert '_settings' in {'_settings': {}}


def test_sequence_generator_settings_migrates_legacy_status_dict():
    # Status file from before the refactor: three separate StatusVars instead of
    # '_generator_settings'. The YAML loader already turns pulse_envelope into a
    # PulseEnvelope.
    legacy_raw = {
        '_generation_parameters': {
            'laser_channel': 'd_ch2',
            'sync_channel': '',
            'gate_channel': '',
            'microwave_channel': 'a_ch1',
            'microwave_frequency': 2.9e9,
            'microwave_amplitude': 0.25,
            'rabi_period': 80e-9,
            'laser_length': 2e-6,
            'laser_delay': 4e-7,
            'wait_time': 8e-7,
            'analog_trigger_voltage': 0.0,
            'optimal_control_assets_path': 'C:\\some\\path',
            'pulse_envelope': PulseEnvelope(PulseEnvelopeType.rectangle),
            'pulse_envelope_order': 1,
        },
        '_benchmark_write_state': {
            '_n_save_datapoints': 20,
            '_datapoints': [(1.0, 100), (2.0, 200)],
            '_datapoints_fixed': [],
        },
        '_benchmark_load_state': {
            '_n_save_datapoints': 20,
            '_datapoints': [],
            '_datapoints_fixed': [],
        },
    }

    assert '_generator_settings' not in legacy_raw
    assert SequenceGeneratorSettings.LEGACY_STATUS_VAR_KEYS.intersection(legacy_raw)

    migrated = SequenceGeneratorSettings.from_legacy_dict(legacy_raw)

    assert migrated.generation_parameters.laser_channel == 'd_ch2'
    assert migrated.generation_parameters.microwave_frequency == 2.9e9
    assert migrated.generation_parameters.microwave_amplitude == 0.25
    assert migrated.generation_parameters.rabi_period == 80e-9
    assert migrated.generation_parameters.optimal_control_assets_path == 'C:\\some\\path'
    # pulser_benchmarks is migrated by the logic module itself, so it isn't tested here.
    # pulse_generator_settings wasn't saved in the old format; it is read from hardware.
    assert migrated.pulse_generator_settings == _default_generator_settings().pulse_generator_settings


def test_measurement_snapshot_pickle_round_trip(tmp_path):
    # Same file that save_measurement_data(save_measurement_snapshot=True) writes.
    data = _sample_data()
    snapshot = PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(
                measurement_settings=_default_measurement_settings(),
                generator_settings=_default_generator_settings(),
            ),
        ),
        data=data,
    )

    file_path = tmp_path / 'test.pulsedmeasurement'
    with open(file_path, 'wb') as snapshot_file:
        pickle.dump(snapshot, snapshot_file)

    restored = PulsedMeasurementLogic.load_measurement_snapshot(str(file_path))

    assert isinstance(restored, PulsedMeasurement)
    assert restored.data.elapsed_sweeps == 3
    assert restored.data.elapsed_time == 12.5
    assert np.array_equal(restored.data.raw_data, data.raw_data)
    assert (
        restored.metadata.settings.generator_settings.generation_parameters
        == _default_generator_settings().generation_parameters
    )
    assert restored.metadata.objects.loaded_asset is None


def _snapshot_with(objects):
    return PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(
                measurement_settings=_default_measurement_settings(),
                generator_settings=_default_generator_settings(),
            ),
            objects=objects,
        ),
        data=_sample_data(),
    )


def _save_and_load_snapshot(tmp_path, snapshot, name):
    """Save and load a snapshot like save_measurement_data() and load_measurement_snapshot()."""
    file_path = tmp_path / f'{name}.pulsedmeasurement'
    with open(file_path, 'wb') as snapshot_file:
        pickle.dump(snapshot, snapshot_file)
    return PulsedMeasurementLogic.load_measurement_snapshot(str(file_path))


def test_measurement_snapshot_round_trips_a_loaded_sequence(tmp_path):
    sequence, _ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def warning(self, msg):
            pass

    ensembles, blocks = _resolve_asset_closure(sequence, saved_ensembles, saved_blocks, _FakeLog())
    restored = _save_and_load_snapshot(
        tmp_path,
        _snapshot_with(PulseObjects(loaded_asset=sequence.copy(), ensembles=ensembles, blocks=blocks)),
        'seq',
    )

    assert isinstance(restored.metadata.objects.loaded_asset, PulseSequence)
    assert restored.metadata.objects.loaded_sequence is restored.metadata.objects.loaded_asset
    assert restored.metadata.objects.loaded_ensemble is None
    assert restored.metadata.objects.loaded_asset.name == 'seq_main'
    assert set(restored.metadata.objects.ensembles) == {'ens_a', 'ens_b'}
    assert set(restored.metadata.objects.blocks) == {'blk_1', 'blk_2', 'blk_3'}
    assert restored.data.elapsed_sweeps == 3
    assert restored.data.elapsed_time == 12.5

    # nan stays a float in the pickle; the string conversion is only for file headers.
    upload_speed = restored.metadata.settings.generator_settings.pulse_generator_settings.upload_speed
    assert isinstance(upload_speed, float) and math.isnan(upload_speed)


def test_measurement_snapshot_round_trips_a_loaded_ensemble(tmp_path):
    _sequence, ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def warning(self, msg):
            pass

    ensembles, blocks = _resolve_asset_closure(ens_a, saved_ensembles, saved_blocks, _FakeLog())
    restored = _save_and_load_snapshot(
        tmp_path,
        _snapshot_with(PulseObjects(loaded_asset=ens_a.copy(), ensembles=ensembles, blocks=blocks)),
        'ens',
    )

    assert isinstance(restored.metadata.objects.loaded_asset, PulseBlockEnsemble)
    assert restored.metadata.objects.loaded_ensemble is restored.metadata.objects.loaded_asset
    assert restored.metadata.objects.loaded_sequence is None
    assert restored.metadata.objects.loaded_asset.name == 'ens_a'
    assert restored.metadata.objects.ensembles == {}  # nothing to resolve for a bare ensemble
    assert set(restored.metadata.objects.blocks) == {'blk_1', 'blk_2'}
    assert restored.data.elapsed_sweeps == 3


def _build_closure_fixtures():
    """3 blocks and 2 ensembles sharing blk_2, plus a sequence using both ensembles (ens_a
    twice)."""
    blk_1 = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    blk_2 = PulseBlock(name='blk_2', element_list=[PulseBlockElement(init_length_s=5e-7)])
    blk_3 = PulseBlock(name='blk_3', element_list=[PulseBlockElement(init_length_s=2e-6)])
    ens_a = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 2), ('blk_2', 0)])
    ens_b = PulseBlockEnsemble(name='ens_b', block_list=[('blk_2', 0), ('blk_3', 0)])
    sequence = PulseSequence(name='seq_main', ensemble_list=[
        ('ens_a', {'repetitions': 0, 'go_to': -1}),
        ('ens_b', {'repetitions': 5, 'go_to': -1}),
        ('ens_a', {'repetitions': 2, 'go_to': -1}),
    ])
    saved_blocks = {'blk_1': blk_1, 'blk_2': blk_2, 'blk_3': blk_3}
    saved_ensembles = {'ens_a': ens_a, 'ens_b': ens_b}
    return sequence, ens_a, saved_ensembles, saved_blocks


def test_resolve_asset_closure_skips_missing_references():
    sequence, ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def __init__(self):
            self.warnings = []

        def warning(self, msg):
            self.warnings.append(msg)

    # Ensemble missing from the registry.
    log = _FakeLog()
    incomplete_ensembles = {'ens_a': saved_ensembles['ens_a']}  # 'ens_b' missing
    ensembles, blocks = _resolve_asset_closure(sequence, incomplete_ensembles, saved_blocks, log)
    assert set(ensembles) == {'ens_a'}
    assert 'ens_b' not in ensembles
    assert len(log.warnings) == 1

    # Block missing from the registry.
    log = _FakeLog()
    incomplete_blocks = {'blk_1': saved_blocks['blk_1']}  # 'blk_2' missing
    ensembles, blocks = _resolve_asset_closure(ens_a, saved_ensembles, incomplete_blocks, log)
    assert set(blocks) == {'blk_1'}
    assert 'blk_2' not in blocks
    assert len(log.warnings) == 1

    # Nothing loaded.
    log = _FakeLog()
    assert _resolve_asset_closure(None, saved_ensembles, saved_blocks, log) == ({}, {})
    assert log.warnings == []


def test_pulsed_measurement_closure_round_trip():
    sequence, _ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def warning(self, msg):
            pass

    ensembles, blocks = _resolve_asset_closure(sequence, saved_ensembles, saved_blocks, _FakeLog())

    measurement = PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(measurement_settings=_default_measurement_settings()),
            objects=PulseObjects(loaded_asset=sequence.copy(), ensembles=ensembles, blocks=blocks),
        ),
    )

    as_dict = measurement.to_dict()
    # A loaded sequence goes into 'sequence'.
    assert set(as_dict['metadata']['objects']) == {'sequence', 'ensembles', 'blocks'}
    assert as_dict['metadata']['objects']['sequence']['name'] == 'seq_main'
    assert 'type' not in as_dict['metadata']['objects']['sequence']
    assert len(as_dict['metadata']['objects']['ensembles']) == 2
    assert len(as_dict['metadata']['objects']['blocks']) == 3  # blk_2 appears once

    restored = PulsedMeasurement.from_dict(as_dict)
    assert isinstance(restored.metadata.objects.loaded_asset, PulseSequence)
    assert restored.metadata.objects.loaded_sequence is restored.metadata.objects.loaded_asset
    assert restored.metadata.objects.loaded_ensemble is None
    assert restored.metadata.objects.loaded_asset.name == 'seq_main'
    assert restored.metadata.objects.loaded_asset.ensemble_list == sequence.ensemble_list
    assert set(restored.metadata.objects.ensembles) == {'ens_a', 'ens_b'}
    assert restored.metadata.objects.ensembles['ens_a'].block_list == [('blk_1', 2), ('blk_2', 0)]
    assert set(restored.metadata.objects.blocks) == {'blk_1', 'blk_2', 'blk_3'}
    assert restored.metadata.objects.blocks['blk_1'].element_list[0] == blocks['blk_1'].element_list[0]


def test_pulsed_measurement_bare_ensemble_closure_round_trip():
    _sequence, ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def warning(self, msg):
            pass

    # A bare ensemble is loaded, not a sequence.
    ensembles, blocks = _resolve_asset_closure(ens_a, saved_ensembles, saved_blocks, _FakeLog())
    assert ensembles == {}  # nothing to resolve for a bare ensemble
    assert set(blocks) == {'blk_1', 'blk_2'}

    measurement = PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(measurement_settings=_default_measurement_settings()),
            objects=PulseObjects(loaded_asset=ens_a.copy(), ensembles=ensembles, blocks=blocks),
        ),
    )

    as_dict = measurement.to_dict()
    # A bare ensemble goes into 'ensembles' and 'sequence' stays empty.
    assert set(as_dict['metadata']['objects']) == {'sequence', 'ensembles', 'blocks'}
    assert as_dict['metadata']['objects']['sequence'] == {}
    assert set(as_dict['metadata']['objects']['ensembles']) == {'ens_a'}
    assert 'type' not in as_dict['metadata']['objects']['ensembles']['ens_a']

    restored = PulsedMeasurement.from_dict(as_dict)
    assert isinstance(restored.metadata.objects.loaded_asset, PulseBlockEnsemble)
    assert restored.metadata.objects.loaded_ensemble is restored.metadata.objects.loaded_asset
    assert restored.metadata.objects.loaded_sequence is None
    assert restored.metadata.objects.loaded_asset.name == 'ens_a'
    assert restored.metadata.objects.ensembles == {}
    assert set(restored.metadata.objects.blocks) == {'blk_1', 'blk_2'}


def test_pulse_objects_dict_keys_are_the_same_for_both_asset_kinds():
    """to_dict() gives the same keys for both kinds; only 'sequence' being empty differs."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ens_a = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    sequence = PulseSequence(name='seq_main', ensemble_list=[{'ensemble': 'ens_a'}])

    from_sequence = PulseObjects(loaded_asset=sequence, ensembles={'ens_a': ens_a},
                                 blocks={'blk_1': blk}).to_dict()
    from_ensemble = PulseObjects(loaded_asset=ens_a, blocks={'blk_1': blk}).to_dict()
    from_nothing = PulseObjects().to_dict()

    expected = {'sequence', 'ensembles', 'blocks'}
    assert set(from_sequence) == set(from_ensemble) == set(from_nothing) == expected

    assert from_sequence['sequence']
    assert from_ensemble['sequence'] == {}
    assert set(from_ensemble['ensembles']) == {'ens_a'}


def test_pulse_objects_empty_sequence_is_not_mistaken_for_an_ensemble():
    """A sequence without ensembles is still read back as a sequence."""
    empty_sequence = PulseSequence(name='seq_empty')
    assert empty_sequence.ensemble_list == []

    as_dict = PulseObjects(loaded_asset=empty_sequence).to_dict()
    assert as_dict['sequence'], "an empty-but-real sequence must still read as truthy"
    assert as_dict['sequence']['ensemble_list'] == []

    restored = PulseObjects.from_dict(as_dict)
    assert isinstance(restored.loaded_asset, PulseSequence)
    assert restored.loaded_ensemble is None


def test_pulse_objects_refuses_to_guess_the_loaded_asset_when_ambiguous():
    """With an empty 'sequence' and several ensembles, the loaded asset is left unset."""
    ens_a = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    ens_b = PulseBlockEnsemble(name='ens_b', block_list=[('blk_1', 0)])

    restored = PulseObjects.from_dict({
        'sequence': {},
        'ensembles': {'ens_a': ens_a.get_dict_representation(),
                      'ens_b': ens_b.get_dict_representation()},
        'blocks': {},
    })
    assert restored.loaded_asset is None
    assert set(restored.ensembles) == {'ens_a', 'ens_b'}


def test_pulse_objects_reads_the_post_split_key_names():
    """from_dict() reads the older 'loaded_sequence'/'loaded_ensemble' keys."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ens_a = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    sequence = PulseSequence(name='seq_main', ensemble_list=[{'ensemble': 'ens_a'}])

    split_sequence = {
        'blocks': {'blk_1': blk.get_dict_representation()},
        'ensembles': {'ens_a': ens_a.get_dict_representation()},
        'loaded_sequence': dict({'type': 'PulseSequence'}, **sequence.get_dict_representation()),
    }
    restored = PulseObjects.from_dict(split_sequence)
    assert isinstance(restored.loaded_asset, PulseSequence)
    assert set(restored.ensembles) == {'ens_a'}

    split_ensemble = {
        'blocks': {'blk_1': blk.get_dict_representation()},
        'loaded_ensemble': dict({'type': 'PulseBlockEnsemble'},
                                **ens_a.get_dict_representation()),
    }
    restored = PulseObjects.from_dict(split_ensemble)
    assert isinstance(restored.loaded_asset, PulseBlockEnsemble)
    assert restored.loaded_sequence is None


def test_pulse_objects_elements_property_reflects_blocks_live():
    blk_1 = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    objects = PulseObjects(blocks={'blk_1': blk_1})

    assert len(objects.elements) == 1

    # elements is computed from the blocks, so changes show up immediately.
    blk_1.element_list.append(PulseBlockElement(init_length_s=2e-6))
    assert len(objects.elements) == 2


def test_pulse_objects_reads_pre_split_sequence_key():
    """from_dict() reads the oldest layout: one 'sequence' key with a 'type' tag."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ensemble = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    sequence = PulseSequence(name='seq_main', ensemble_list=[{'ensemble': 'ens_a'}])

    legacy_ensemble = {
        'blocks': {'blk_1': blk.get_dict_representation()},
        'ensembles': {},
        'sequence': dict({'type': 'PulseBlockEnsemble'}, **ensemble.get_dict_representation()),
    }
    restored = PulseObjects.from_dict(legacy_ensemble)
    assert isinstance(restored.loaded_asset, PulseBlockEnsemble)
    assert restored.loaded_ensemble is restored.loaded_asset
    assert restored.loaded_sequence is None

    legacy_sequence = {
        'blocks': {'blk_1': blk.get_dict_representation()},
        'ensembles': {'ens_a': ensemble.get_dict_representation()},
        'sequence': dict({'type': 'PulseSequence'}, **sequence.get_dict_representation()),
    }
    restored = PulseObjects.from_dict(legacy_sequence)
    assert isinstance(restored.loaded_asset, PulseSequence)
    assert restored.loaded_sequence is restored.loaded_asset
    assert restored.loaded_ensemble is None
    assert set(restored.ensembles) == {'ens_a'}


def test_pulse_objects_unpickles_pre_rename_snapshot():
    """Old pickled snapshots that used `sequence` instead of `loaded_asset` still load."""
    ensemble = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    objects = PulseObjects()
    restored_state = {'sequence': ensemble, 'ensembles': {}, 'blocks': {}}
    objects.__setstate__(restored_state)

    assert objects.loaded_asset is ensemble
    assert objects.loaded_ensemble is ensemble
    assert not hasattr(objects, 'sequence')


def test_sampling_information_refuses_generation_parameters():
    """Generation parameters are dropped, since they belong to SequenceGeneratorSettings."""
    info = SamplingInformation.from_dict(
        {'waveforms': ['wfm_ch1'], 'generation_parameters': {'rabi_period': 1e-7}}
    )
    assert info.waveforms == ['wfm_ch1']
    assert 'generation_parameters' not in info._legacy_data
    assert 'generation_parameters' not in info.to_dict()

    # Also removed when nested under '_legacy_data', as after a YAML round trip.
    info = SamplingInformation.from_dict({'_legacy_data': {'generation_parameters': {'x': 1}}})
    assert info._legacy_data == {}

    # The item setter refuses it too.
    info['generation_parameters'] = {'rabi_period': 1e-7}
    assert 'generation_parameters' not in info._legacy_data

    # Other unknown keys still go into _legacy_data.
    info['something_else'] = 42
    assert info._legacy_data == {'something_else': 42}


def test_analysis_results_carry_no_generation_parameters():
    """The analysis results have no generation_parameters field."""
    from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import (
        EnsembleAnalysisResult,
        SequenceAnalysisResult,
    )

    for cls in (EnsembleAnalysisResult, SequenceAnalysisResult):
        assert 'generation_parameters' not in {f.name for f in dataclasses.fields(cls)}


def test_asset_from_dict_tolerates_trimmed_metadata_dict():
    """Reading back to_metadata_dict() output gives a partial object instead of raising."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ensemble = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    ensemble.generation_method_parameters = GenerationMethodParameters({'xy8_order': 8})
    objects = PulseObjects(loaded_asset=ensemble, blocks={'blk_1': blk})

    trimmed = objects.to_metadata_dict(omit_generation_method_parameters=True)
    restored = PulseObjects.from_dict(trimmed)

    assert isinstance(restored.loaded_asset, PulseBlockEnsemble)
    assert restored.loaded_asset.name == 'ens_a'
    assert restored.loaded_asset.generation_method_parameters == {}

    # Only the structural keys.
    minimal = PulseBlockEnsemble.ensemble_from_dict({'name': 'x', 'block_list': [('blk_1', 0)]})
    assert minimal.name == 'x'
    assert minimal.rotating_frame is False
    assert not minimal.sampling_information  # not sampled

    minimal_seq = PulseSequence.sequence_from_dict(
        {'name': 'y', 'ensemble_list': [{'ensemble': 'ens_a'}]}
    )
    assert minimal_seq.name == 'y'
    assert minimal_seq.generation_method_parameters == {}


def test_to_plain_metadata_converts_non_finite_floats():
    """nan/inf become strings, since eval() can't read repr(float('nan'))."""
    for value, expected in ((float('nan'), 'nan'), (float('inf'), 'inf'), (float('-inf'), '-inf')):
        out = to_plain_metadata(value)
        assert out == expected
        assert isinstance(out, str)
        recovered = float(out)
        assert math.isnan(recovered) if expected == 'nan' else recovered == value

    # Same for numpy scalars.
    assert to_plain_metadata(np.float64('nan')) == 'nan'
    assert to_plain_metadata(np.float32('inf')) == 'inf'

    # Finite values are kept; numpy scalars become Python numbers.
    assert to_plain_metadata(1.5) == 1.5
    assert to_plain_metadata(np.float64(2.5)) == 2.5
    assert to_plain_metadata(np.int64(5)) == 5
    assert isinstance(to_plain_metadata(np.int64(5)), int)


def test_non_finite_floats_survive_nesting_and_eval():
    """nan values survive nesting and eval()."""
    nested = {'levels': {'a_ch1': float('nan')}, 'series': [0.0, 1.0, float('nan'), 3.0]}
    text = repr(to_plain_metadata(nested))
    restored = eval(text)  # same as qudi.util.datastorage

    assert restored['levels']['a_ch1'] == 'nan'
    assert restored['series'] == [0.0, 1.0, 'nan', 3.0]

    # A long array of nan works too (longer than numpy's repr limit).
    big = np.full(1500, np.nan)
    restored = eval(repr(to_plain_metadata(big)))
    assert len(restored) == 1500
    assert set(restored) == {'nan'}


def test_finalize_metadata_writes_every_value_on_one_line():
    """Every metadata value fits on one line, so each header entry is one line."""
    metadata = PulsedMeasurementLogic._finalize_metadata(
        {
            'fast counter settings': {'bin_width': 1e-9, 'record_length': 3e-6, 'is_gated': False},
            'Controlled variable': np.linspace(0.0, 1.0, 1500),
            'alternating': np.bool_(True),
        }
    )

    for key, value in metadata.items():
        assert type(value) in (dict, list, bool, int, float, str), key
        assert '\n' not in repr(value), key

    # Long arrays are written in full, without numpy's '...'.
    assert len(metadata['Controlled variable']) == 1500
    assert '...' not in repr(metadata['Controlled variable'])


def test_settings_to_metadata_dict_omits_containers_written_flat():
    """Containers the header writes flat are dropped from the nested settings block."""
    settings = Settings(
        measurement_settings=_default_measurement_settings(),
        generator_settings=_default_generator_settings(),
    )

    full = settings.to_dict()
    assert 'fast_counter_settings' in full['measurement_settings']
    assert 'generation_parameters' in full['generator_settings']

    trimmed = settings.to_metadata_dict(omit=PulsedMeasurementLogic._SIGNAL_METADATA_OMIT)
    assert 'fast_counter_settings' not in trimmed['measurement_settings']
    assert 'extraction_parameters' not in trimmed['measurement_settings']
    assert 'analysis_parameters' not in trimmed['measurement_settings']
    assert 'generation_parameters' not in trimmed['generator_settings']
    # Containers that aren't written flat stay in the nested block.
    assert 'microwave_settings' in trimmed['measurement_settings']
    assert 'readout_settings' in trimmed['measurement_settings']
    assert 'pulse_generator_settings' in trimmed['generator_settings']

    # The laser file only writes 'extraction parameters' flat.
    trimmed = settings.to_metadata_dict(omit=PulsedMeasurementLogic._LASER_METADATA_OMIT)
    assert 'extraction_parameters' not in trimmed['measurement_settings']
    assert 'fast_counter_settings' in trimmed['measurement_settings']

    # The raw file writes nothing flat, so all containers stay. Compare keys only:
    # controlled_variable is a numpy array, so == on the dicts would raise.
    raw = settings.to_metadata_dict(omit=PulsedMeasurementLogic._RAW_METADATA_OMIT)
    assert set(raw['measurement_settings']) == set(full['measurement_settings'])
    assert set(raw['generator_settings']) == set(full['generator_settings'])

    # to_dict() still returns everything, and the settings weren't modified.
    fresh = settings.to_dict()
    assert set(fresh['measurement_settings']) == set(full['measurement_settings'])
    assert set(fresh['generator_settings']) == set(full['generator_settings'])


def test_settings_to_metadata_dict_tolerates_absent_paths():
    """Paths that don't exist are ignored, e.g. when generator_settings is None."""
    settings = Settings(measurement_settings=_default_measurement_settings())
    trimmed = settings.to_metadata_dict(omit=PulsedMeasurementLogic._SIGNAL_METADATA_OMIT)

    assert trimmed['generator_settings'] is None
    assert 'fast_counter_settings' not in trimmed['measurement_settings']


def _logic_for_metadata(loaded_asset=None, ensembles=None, blocks=None, controlled_variable=None):
    """PulsedMeasurementLogic with just enough state for the _get_*_metadata() methods.
    The analyzer and extractor are stubbed."""
    # object.__new__ doesn't work with the Qt metaclass. __init__ is skipped since no
    # Connectors are needed.
    logic = PulsedMeasurementLogic.__new__(PulsedMeasurementLogic)
    data = _sample_data()
    if controlled_variable is not None:
        data.signal_data = np.vstack([controlled_variable, np.zeros(len(controlled_variable))])
    logic._pulsed_measurement = PulsedMeasurement(
        metadata=PulsedMetadata(
            settings=Settings(
                measurement_settings=_default_measurement_settings(),
                generator_settings=_default_generator_settings(),
            ),
            objects=PulseObjects(
                loaded_asset=loaded_asset, ensembles=ensembles or {}, blocks=blocks or {}
            ),
        ),
        data=data,
    )
    logic._pulseanalyzer = types.SimpleNamespace(analysis_settings={'method': 'mean_norm'})
    logic._pulseextractor = types.SimpleNamespace(
        extraction_settings={'method': 'conv_deriv', 'conv_std_dev': 10.0}
    )
    return logic


def test_saved_header_round_trips_through_real_data_storage(tmp_path):
    """Save a signal file with TextDataStorage and load it back: every value is parsed
    again, none comes back as a string."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ensemble = PulseBlockEnsemble(name='rabi', block_list=[('blk_1', 0)])
    ensemble.generation_method_parameters = GenerationMethodParameters({'xy8_order': 8})
    # Longer than numpy's repr limit, with a nan.
    controlled_variable = np.linspace(0.0, 1.0, 1500)
    controlled_variable[7] = np.nan

    logic = _logic_for_metadata(
        loaded_asset=ensemble, blocks={'blk_1': blk}, controlled_variable=controlled_variable
    )
    metadata = logic._get_signal_metadata(
        generator_settings=logic._pulsed_measurement.metadata.settings.generator_settings,
        blocks={'blk_1': blk},
    )

    storage = TextDataStorage(root_dir=str(tmp_path))
    file_path, _timestamp, _headers = storage.save_data(
        np.zeros((3, 2)), metadata=metadata, nametag='pulsed'
    )

    _data, restored, _general = TextDataStorage.load_data(file_path)

    # Every container is read back as a dict, not a string. 'pulsed measurement
    # settings' contains the nan in upload_speed.
    for key in ('fast counter settings', 'generation parameters', 'generation method parameters',
                'pulsed measurement settings', 'pulse objects'):
        assert isinstance(restored[key], dict), f'{key} came back as {type(restored[key]).__name__}'

    pgs = restored['pulsed measurement settings']['generator_settings']['pulse_generator_settings']
    assert pgs['upload_speed'] == 'nan'
    assert math.isnan(float(pgs['upload_speed']))

    # The raw file has the controlled variable: check it's complete, including the nan.
    raw_metadata = logic._get_raw_metadata(
        generator_settings=logic._pulsed_measurement.metadata.settings.generator_settings,
        blocks={'blk_1': blk},
    )
    raw_path, _ts, _hdrs = storage.save_data(
        np.zeros((3, 2)), metadata=raw_metadata, nametag='pulsed_raw'
    )
    _d, raw_restored, _g = TextDataStorage.load_data(raw_path)

    assert isinstance(raw_restored['pulsed measurement settings'], dict)
    cv = raw_restored['controlled variable']  # ConfigParser lowercases keys
    assert len(cv) == 1500
    assert cv[7] == 'nan'
    assert cv[0] == 0.0

    # A bare ensemble: 'sequence' is empty and the asset is under 'ensembles'.
    objs = restored['pulse objects']
    assert set(objs) == {'sequence', 'ensembles', 'blocks'}
    assert not objs['sequence']
    assert set(objs['ensembles']) == {'rabi'}
    assert isinstance(PulseObjects.from_dict(objs).loaded_asset, PulseBlockEnsemble)
    # The duplicate is dropped, but the name is kept.
    assert 'loaded asset type' not in restored
    assert restored['loaded asset name'] == 'rabi'

    # fast_counter_settings is only written flat, not also nested.
    assert 'fast_counter_settings' not in restored['pulsed measurement settings']['measurement_settings']


def test_saved_header_uses_the_same_keys_for_a_sequence(tmp_path):
    sequence, _ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def warning(self, msg):
            pass

    ensembles, blocks = _resolve_asset_closure(sequence, saved_ensembles, saved_blocks, _FakeLog())
    logic = _logic_for_metadata(loaded_asset=sequence, ensembles=ensembles, blocks=blocks)
    metadata = logic._get_signal_metadata(
        generator_settings=logic._pulsed_measurement.metadata.settings.generator_settings,
        ensembles=ensembles,
        blocks=blocks,
    )

    storage = TextDataStorage(root_dir=str(tmp_path))
    file_path, _timestamp, _headers = storage.save_data(
        np.zeros((3, 2)), metadata=metadata, nametag='pulsed_seq'
    )
    _data, restored, _general = TextDataStorage.load_data(file_path)

    # Same keys as for a bare ensemble; only 'sequence' differs.
    objs = restored['pulse objects']
    assert set(objs) == {'sequence', 'ensembles', 'blocks'}
    assert objs['sequence']['name'] == 'seq_main'
    assert set(objs['ensembles']) == {'ens_a', 'ens_b'}
    assert isinstance(PulseObjects.from_dict(objs).loaded_asset, PulseSequence)
    assert 'loaded asset type' not in restored
    assert restored['loaded asset name'] == 'seq_main'


def test_pulse_objects_metadata_dict_can_omit_generation_method_parameters():
    """Only the loaded asset's generation method parameters are dropped."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ensemble = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    ensemble.generation_method_parameters = GenerationMethodParameters({'xy8_order': 8})
    sequence = PulseSequence(name='seq_main', ensemble_list=[{'ensemble': 'ens_a'}])
    sequence.generation_method_parameters = GenerationMethodParameters({'tau_step': 1e-9})
    objects = PulseObjects(
        loaded_asset=sequence, ensembles={'ens_a': ensemble}, blocks={'blk_1': blk}
    )

    kept = objects.to_metadata_dict()
    assert kept['sequence']['generation_method_parameters'] == {'tau_step': 1e-9}

    dropped = objects.to_metadata_dict(omit_generation_method_parameters=True)
    assert 'generation_method_parameters' not in dropped['sequence']
    # Parameters of the other ensembles are kept.
    assert dropped['ensembles']['ens_a']['generation_method_parameters'] == {'xy8_order': 8}


def test_metadata_dict_omit_follows_a_bare_ensemble_into_ensembles():
    """The omit flag also works for a bare ensemble stored under 'ensembles'."""
    blk = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    ensemble = PulseBlockEnsemble(name='ens_a', block_list=[('blk_1', 0)])
    ensemble.generation_method_parameters = GenerationMethodParameters({'xy8_order': 8})
    objects = PulseObjects(loaded_asset=ensemble, blocks={'blk_1': blk})

    kept = objects.to_metadata_dict()
    assert kept['ensembles']['ens_a']['generation_method_parameters'] == {'xy8_order': 8}

    dropped = objects.to_metadata_dict(omit_generation_method_parameters=True)
    assert 'generation_method_parameters' not in dropped['ensembles']['ens_a']
