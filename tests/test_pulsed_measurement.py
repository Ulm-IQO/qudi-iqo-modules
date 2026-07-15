from dataclasses import replace

import numpy as np

from qudi.logic.pulsed.pulse_objects import PulseBlockEnsemble, PulseSequence
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
from qudi.logic.pulsed.pulsed_data.pulsed_measurement import PulsedMeasurement, PulsedData, Settings
from qudi.logic.pulsed.pulsed_measurement_logic import _default_measurement_settings
from qudi.logic.pulsed.sequence_generator_logic import _default_generator_settings


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
    assert restored.settings.measurement_settings.timer_interval_s == 5.0
    assert restored.settings.measurement_settings.fit_configs == _default_measurement_settings().fit_configs
    assert restored.data.measurement_data.elapsed_sweeps == 3
    assert restored.data.measurement_data.elapsed_time == 12.5
    assert np.array_equal(restored.data.measurement_data.raw_data, measurement.data.measurement_data.raw_data)
    # fit_result/fit_result_alt are intentionally never restored by from_dict() - see PulsedData's
    # class docstring for why (lmfit.model.ModelResult has no reconstruction path).
    assert restored.data.fit_result is None
    assert restored.data.fit_result_alt is None
    # sequence is intentionally never part of to_dict()/from_dict() - see PulsedMeasurement's
    # class docstring for why.
    assert restored.sequence is None


def test_pulsed_measurement_round_trip_without_generator_settings_or_data():
    measurement = PulsedMeasurement(settings=Settings(measurement_settings=_default_measurement_settings()))

    restored = PulsedMeasurement.from_dict(measurement.to_dict())

    assert restored.settings.generator_settings is None
    assert restored.data is None


def test_pulsed_measurement_settings_fit_configs_round_trip():
    custom_configs = (
        {'name': 'Custom Fit', 'model': 'Sine', 'estimator': 'default', 'custom_parameters': None},
    )
    settings = replace(_default_measurement_settings(), fit_configs=custom_configs)

    restored = PulsedMeasurementSettings.from_dict(settings.to_dict())

    assert restored.fit_configs == custom_configs


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
    # PulsedMeasurementSettings (e.g. before fit_configs existed) - from_dict() must degrade to
    # that field's own default rather than raising KeyError.
    full_dict = _default_measurement_settings().to_dict()
    del full_dict['fit_configs']
    del full_dict['microwave_settings']

    restored = PulsedMeasurementSettings.from_dict(full_dict)

    assert restored.fit_configs == ()
    assert restored.microwave_settings.power == -30.0
    assert restored.microwave_settings.frequency == 2870e6
    # Fields that WERE present still round-trip correctly - this isn't a blanket "ignore
    # everything" fallback, only missing keys are defaulted.
    assert restored.timer_interval_s == _default_measurement_settings().timer_interval_s


def test_sequence_generator_settings_from_dict_tolerates_missing_keys():
    full_dict = _default_generator_settings().to_dict()
    del full_dict['generation_parameters']
    del full_dict['pulser_benchmarks']

    restored = SequenceGeneratorSettings.from_dict(full_dict)

    assert restored.generation_parameters == _default_generator_settings().generation_parameters
    assert restored.pulser_benchmarks.write.n_benchmarks == 0


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
