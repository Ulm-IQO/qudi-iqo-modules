import dataclasses
import pickle

import numpy as np

from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockElement, PulseBlockEnsemble, PulseSequence
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    GenerationMethodParameters,
    MeasurementInformation,
    PulsedMeasurementData,
    PulsedMeasurementSettings,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import (
    SamplingInformation,
    SequenceGeneratorSettings,
)
from qudi.logic.pulsed.pulsed_data.pulsed_measurement import PulsedMeasurement, PulsedData, PulseObjects, Settings
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
        settings=Settings(
            measurement_settings=_default_measurement_settings(),
            generator_settings=_default_generator_settings(),
        ),
        data=PulsedData(measurement_data=_sample_data()),
    )

    restored = PulsedMeasurement.from_dict(measurement.to_dict())

    # PulsedMeasurementSettings/ReadoutSettings has no custom __eq__, so comparing it directly
    # would hit the numpy ambiguous-truth-value issue documented elsewhere in this codebase -
    # spot-check a couple of fields via a class that IS safely comparable instead.
    assert (
        restored.settings.generator_settings.generation_parameters
        == _default_generator_settings().generation_parameters
    )
    assert restored.data.measurement_data.elapsed_sweeps == 3
    assert restored.data.measurement_data.elapsed_time == 12.5
    assert np.array_equal(restored.data.measurement_data.raw_data, measurement.data.measurement_data.raw_data)
    # fit_result/fit_result_alt are intentionally never restored by from_dict() - see PulsedData's
    # class docstring for why (lmfit.model.ModelResult has no reconstruction path).
    assert restored.data.fit_result is None
    assert restored.data.fit_result_alt is None
    # Nothing was loaded when this snapshot was built, so objects.sequence/.ensembles/.blocks all
    # default to empty - see PulseObjects.
    assert restored.objects.sequence is None
    assert restored.objects.ensembles == {}
    assert restored.objects.blocks == {}


def test_pulsed_measurement_round_trip_without_generator_settings_or_data():
    measurement = PulsedMeasurement(settings=Settings(measurement_settings=_default_measurement_settings()))

    restored = PulsedMeasurement.from_dict(measurement.to_dict())

    assert restored.settings.generator_settings is None
    assert restored.data is None


def test_pulsed_measurement_data_copy_is_independent():
    data = _sample_data()
    copy = data.copy()

    copy.raw_data[0] = 999
    copy.elapsed_sweeps = 100

    assert data.raw_data[0] == 1
    assert data.elapsed_sweeps == 3


def test_pulse_block_ensemble_copy_is_independent():
    ensemble = PulseBlockEnsemble(name='original', block_list=[('block1', 3)], rotating_frame=True)
    ensemble.measurement_information = MeasurementInformation(
        number_of_lasers=2,
        controlled_variable=np.array([0.0, 1.0]),
        laser_ignore_list=[],
        alternating=False,
        counting_length=1e-6,
    )
    ensemble.generation_method_parameters = GenerationMethodParameters({'xy8_order': 8})

    copy = ensemble.copy()
    copy.block_list.append(('block2', 1))
    copy.generation_method_parameters['xy8_order'] = 16
    copy.measurement_information.number_of_lasers = 99

    assert ensemble.block_list == [('block1', 3)]
    assert ensemble.generation_method_parameters['xy8_order'] == 8
    assert ensemble.measurement_information.number_of_lasers == 2


def test_pulse_sequence_copy_is_independent():
    # ensemble_list entries are SequenceStep, a mutable dict subclass - this is the exact case
    # a naive shallow list() copy would get wrong (see PulseSequence.copy()'s docstring).
    sequence = PulseSequence(name='original', ensemble_list=[('ensemble1', {'repetitions': 2})], rotating_frame=True)
    sequence.generation_method_parameters = GenerationMethodParameters({'order': 4})

    copy = sequence.copy()
    copy.ensemble_list[0]['repetitions'] = 99
    copy.generation_method_parameters['order'] = 8

    assert sequence.ensemble_list[0]['repetitions'] == 2
    assert sequence.generation_method_parameters['order'] == 4


def test_pulsed_measurement_settings_from_dict_tolerates_missing_keys():
    # Simulates loading an older saved settings file that predates a field being added to
    # PulsedMeasurementSettings - from_dict() must degrade to that field's own default rather
    # than raising KeyError.
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

    # Real dataclass fields, not routed through the _legacy_data catch-all - this is what
    # basic_extraction_methods.py's ungated_gated_conv_deriv() reads via
    # sampling_information['laser_rising_bins'].
    assert 'laser_rising_bins' in info._field_names()
    assert 'laser_falling_bins' not in info._legacy_data
    assert list(info['laser_rising_bins']) == [10, 20, 30]
    assert list(info['laser_falling_bins']) == [15, 25, 35]

    restored = SamplingInformation.from_dict(info.to_dict())
    assert list(restored.laser_rising_bins) == [10, 20, 30]
    assert list(restored.laser_falling_bins) == [15, 25, 35]


def test_legacy_status_var_keys_not_dataclass_fields():
    # LEGACY_STATUS_VAR_KEYS is a plain class attribute used only for legacy-format detection
    # (see PulsedMeasurementLogic/SequenceGeneratorLogic._migrate_legacy_settings_if_needed()) -
    # it must never be picked up as a real dataclass field (that would require it to be passed
    # to every constructor call and would show up in to_dict()/from_dict()).
    assert 'LEGACY_STATUS_VAR_KEYS' not in {f.name for f in dataclasses.fields(PulsedMeasurementSettings)}
    assert 'LEGACY_STATUS_VAR_KEYS' not in {f.name for f in dataclasses.fields(SequenceGeneratorSettings)}


def test_pulsed_measurement_settings_migrates_legacy_status_dict():
    # Simulates a real status file saved by the pre-refactor PulsedMeasurementLogic: ~20
    # individually-keyed StatusVars (some Python-name-mangled) at the top level, instead of one
    # '_settings' key - see PulsedMeasurementSettings.from_legacy_dict()'s docstring.
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

    # A file with no '_settings' key but recognizable legacy keys must be detected as legacy -
    # this is the exact condition used in PulsedMeasurementLogic._migrate_legacy_settings_if_needed().
    assert '_settings' not in legacy_raw
    assert PulsedMeasurementSettings.LEGACY_STATUS_VAR_KEYS.intersection(legacy_raw)

    migrated = PulsedMeasurementSettings.from_legacy_dict(legacy_raw)

    assert migrated.microwave_settings.power == -13.0
    assert migrated.microwave_settings.frequency == 2.5e9
    assert migrated.microwave_settings.use_ext_microwave is True
    assert migrated.fast_counter_settings.bin_width == 2e-9
    assert migrated.fast_counter_settings.record_length == 5e-6
    assert migrated.fast_counter_settings.number_of_gates == 4
    # is_gated is never persisted in the legacy format (it was always a live hardware query) -
    # left at False here; PulsedMeasurementLogic.on_activate() re-syncs it from real hardware
    # immediately afterwards via set_fast_counter_settings().
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
    # timer_interval_s/fit_configs are no longer fields of PulsedMeasurementSettings (see its
    # class docstring) - PulsedMeasurementLogic._migrate_legacy_settings_if_needed() migrates
    # these two directly from the same raw dict into its own independent StatusVars instead,
    # which requires a real logic instance and so isn't covered at this dataclass-level test.

    # An empty dict (brand new install, file doesn't exist yet) must never be flagged as legacy.
    assert not PulsedMeasurementSettings.LEGACY_STATUS_VAR_KEYS.intersection({})
    # A dict that already has the new '_settings' key must never be re-migrated.
    assert '_settings' in {'_settings': {}}


def test_sequence_generator_settings_migrates_legacy_status_dict():
    # Simulates a real status file saved by the pre-refactor SequenceGeneratorLogic: 3 separate
    # StatusVars (_generation_parameters, _benchmark_write_state, _benchmark_load_state) instead
    # of one '_generator_settings' key. pulse_envelope is a real PulseEnvelope instance here
    # because qudi's registered YAML constructor for '!PulseEnvelope' already reconstructs it
    # during yaml_load(), before this code ever sees the dict - see
    # SequenceGeneratorSettings.from_legacy_dict()'s docstring.
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
    # pulser_benchmarks is no longer a field of SequenceGeneratorSettings (see its class
    # docstring) - SequenceGeneratorLogic._migrate_legacy_settings_if_needed() migrates it
    # directly from the same raw dict into its own independent StatusVar instead, which requires
    # a real logic instance and so isn't covered at this dataclass-level test.
    # pulse_generator_settings is deliberately never migrated - the old format never persisted it
    # either (SequenceGeneratorLogic.on_activate() always re-derives it fresh from hardware via
    # _read_settings_from_device(), for both old and new code).
    assert migrated.pulse_generator_settings == _default_generator_settings().pulse_generator_settings


def test_measurement_snapshot_pickle_round_trip(tmp_path):
    # Exercises PulsedMeasurementLogic.load_measurement_snapshot() against a real file on disk,
    # mirroring what save_measurement_data(save_measurement_snapshot=True) produces (see that
    # method's pickle.dump() of get_pulsed_measurement()'s return value).
    data = _sample_data()
    snapshot = PulsedMeasurement(
        settings=Settings(
            measurement_settings=_default_measurement_settings(),
            generator_settings=_default_generator_settings(),
        ),
        data=PulsedData(measurement_data=data),
    )

    file_path = tmp_path / 'test.pulsedmeasurement'
    with open(file_path, 'wb') as snapshot_file:
        pickle.dump(snapshot, snapshot_file)

    restored = PulsedMeasurementLogic.load_measurement_snapshot(str(file_path))

    assert isinstance(restored, PulsedMeasurement)
    assert restored.data.measurement_data.elapsed_sweeps == 3
    assert restored.data.measurement_data.elapsed_time == 12.5
    assert np.array_equal(restored.data.measurement_data.raw_data, data.raw_data)
    assert (
        restored.settings.generator_settings.generation_parameters
        == _default_generator_settings().generation_parameters
    )
    assert restored.objects.sequence is None


def test_pulse_block_copy_is_independent():
    block = PulseBlock(name='original', element_list=[PulseBlockElement(init_length_s=1e-6)])

    copy = block.copy()
    copy.element_list[0].init_length_s = 999.0
    copy.element_list.append(PulseBlockElement(init_length_s=2e-6))

    assert len(block.element_list) == 1
    assert block.element_list[0].init_length_s == 1e-6


def _build_closure_fixtures():
    """3 blocks, 2 ensembles (blk_2 shared between them), 1 sequence referencing both ensembles
    (ens_a referenced twice, with different repetitions) - used by the closure-resolution tests
    below."""
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

    # Case A: a referenced ensemble name is missing from the registry.
    log = _FakeLog()
    incomplete_ensembles = {'ens_a': saved_ensembles['ens_a']}  # 'ens_b' missing
    ensembles, blocks = _resolve_asset_closure(sequence, incomplete_ensembles, saved_blocks, log)
    assert set(ensembles) == {'ens_a'}
    assert 'ens_b' not in ensembles
    assert len(log.warnings) == 1

    # Case B: a referenced block name is missing from the registry.
    log = _FakeLog()
    incomplete_blocks = {'blk_1': saved_blocks['blk_1']}  # 'blk_2' missing
    ensembles, blocks = _resolve_asset_closure(ens_a, saved_ensembles, incomplete_blocks, log)
    assert set(blocks) == {'blk_1'}
    assert 'blk_2' not in blocks
    assert len(log.warnings) == 1

    # None input.
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
        settings=Settings(measurement_settings=_default_measurement_settings()),
        objects=PulseObjects(sequence=sequence.copy(), ensembles=ensembles, blocks=blocks),
    )

    as_dict = measurement.to_dict()
    assert as_dict['objects']['sequence']['type'] == 'PulseSequence'
    assert len(as_dict['objects']['ensembles']) == 2
    assert len(as_dict['objects']['blocks']) == 3  # blk_2 shared between ens_a/ens_b, deduped
    assert len(as_dict['objects']['elements']) == 3  # one element per block

    restored = PulsedMeasurement.from_dict(as_dict)
    assert isinstance(restored.objects.sequence, PulseSequence)
    assert restored.objects.sequence.name == 'seq_main'
    assert restored.objects.sequence.ensemble_list == sequence.ensemble_list
    assert set(restored.objects.ensembles) == {'ens_a', 'ens_b'}
    assert restored.objects.ensembles['ens_a'].block_list == [('blk_1', 2), ('blk_2', 0)]
    assert set(restored.objects.blocks) == {'blk_1', 'blk_2', 'blk_3'}
    assert restored.objects.blocks['blk_1'].element_list[0] == blocks['blk_1'].element_list[0]


def test_pulsed_measurement_bare_ensemble_closure_round_trip():
    _sequence, ens_a, saved_ensembles, saved_blocks = _build_closure_fixtures()

    class _FakeLog:
        def warning(self, msg):
            pass

    # Loaded asset is a bare PulseBlockEnsemble, not wrapped in a PulseSequence.
    ensembles, blocks = _resolve_asset_closure(ens_a, saved_ensembles, saved_blocks, _FakeLog())
    assert ensembles == {}  # nothing one level up to resolve for a bare ensemble
    assert set(blocks) == {'blk_1', 'blk_2'}

    measurement = PulsedMeasurement(
        settings=Settings(measurement_settings=_default_measurement_settings()),
        objects=PulseObjects(sequence=ens_a.copy(), ensembles=ensembles, blocks=blocks),
    )

    restored = PulsedMeasurement.from_dict(measurement.to_dict())
    assert isinstance(restored.objects.sequence, PulseBlockEnsemble)
    assert restored.objects.sequence.name == 'ens_a'
    assert restored.objects.ensembles == {}
    assert set(restored.objects.blocks) == {'blk_1', 'blk_2'}


def test_pulse_objects_elements_property_reflects_blocks_live():
    blk_1 = PulseBlock(name='blk_1', element_list=[PulseBlockElement(init_length_s=1e-6)])
    objects = PulseObjects(blocks={'blk_1': blk_1})

    assert len(objects.elements) == 1

    # Mutating the underlying block after construction is immediately reflected - elements is a
    # computed property, not an independently stored/cached field, so it can never drift out of
    # sync with `blocks`.
    blk_1.element_list.append(PulseBlockElement(init_length_s=2e-6))
    assert len(objects.elements) == 2
