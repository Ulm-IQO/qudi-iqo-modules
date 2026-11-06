# -*- coding: utf-8 -*-
"""
Settings containers for SequenceGeneratorLogic: generation parameters and pulse generator
settings.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""
from dataclasses import dataclass, replace, field, fields
from enum import Enum
from logging import getLogger
from typing import ClassVar, Optional, get_type_hints
import numpy as np

from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType
from qudi.util.benchmark import BenchmarkTool

# Importing the extensions module defines the lab's BaseGenerationParameters subclasses, which are
# merged into GenerationParameters below. The ValueError for a mutable field default is raised
# during this import, so a hint is added here.
try:
    from qudi.logic.pulsed.pulsed_data.generation_parameter_extensions import (
        BaseGenerationParameters,
        as_bool as _as_bool,
    )
    import qudi.logic.pulsed.pulsed_data.generation_parameter_extensions as _generation_parameter_extensions  # noqa: F401
except ValueError as err:
    if 'mutable default' not in str(err):
        raise
    raise ValueError(
        f'{err}\n\n'
        'A generation parameter declared in generation_parameter_extensions.py has a mutable '
        'default. One default object would be shared by every instance of the class, so Python '
        'refuses the declaration. Give that field a factory instead:\n'
        '    envelope: PulseEnvelope = field(\n'
        '        default_factory=lambda: PulseEnvelope(PulseEnvelopeType.rectangle)\n'
        '    )\n'
        'PulseEnvelope needs this despite being a supported parameter type - it is a plain '
        '@dataclass holding a parameters dict, so it counts as mutable. The same applies to a '
        'dict, a list, a numpy array, or an instance of any class defining __eq__.'
    ) from err

_logger = getLogger(__name__)

#: Field types the Predefined Methods tab can build a widget for.
_GUI_RENDERABLE_TYPES = (str, int, float, bool, Enum, PulseEnvelope)


@dataclass(frozen=True)
class CoreGenerationParameters(BaseGenerationParameters):
    """Built-in generation parameters: channels and common timings used by predefined methods.

    Merged with the lab extensions into `GenerationParameters`, which is the class to use.
    """

    laser_channel: str = 'd_ch1'
    sync_channel: str = ''
    gate_channel: str = ''
    microwave_channel: str = 'a_ch1'
    microwave_frequency: float = 2.87e9
    microwave_amplitude: float = 0.0
    rabi_period: float = 100e-9
    laser_length: float = 3e-6
    laser_delay: float = 500e-9
    wait_time: float = 1e-6
    analog_trigger_voltage: float = 0.0
    optimal_control_assets_path: str = 'C:\\Software\\qudi_data\\optimal_control_assets'
    pulse_envelope: PulseEnvelope = field(
        default_factory=lambda: PulseEnvelope(PulseEnvelopeType.rectangle)
    )
    pulse_envelope_order: int = 1

    @staticmethod
    def _as_pulse_envelope(value):
        return value if isinstance(value, PulseEnvelope) else PulseEnvelope.from_dict(value)

    @classmethod
    def _coerce_fields(cls, data, current=None):
        """Explicit conversions for the built-in parameters."""
        pick = cls._pick
        return {
            'laser_channel': str(pick(data, current, 'laser_channel', 'd_ch1')),
            'sync_channel': str(pick(data, current, 'sync_channel', '')),
            'gate_channel': str(pick(data, current, 'gate_channel', '')),
            'microwave_channel': str(pick(data, current, 'microwave_channel', 'a_ch1')),
            'microwave_frequency': float(pick(data, current, 'microwave_frequency', 2.87e9)),
            'microwave_amplitude': float(pick(data, current, 'microwave_amplitude', 0.0)),
            'rabi_period': float(pick(data, current, 'rabi_period', 100e-9)),
            'laser_length': float(pick(data, current, 'laser_length', 3e-6)),
            'laser_delay': float(pick(data, current, 'laser_delay', 500e-9)),
            'wait_time': float(pick(data, current, 'wait_time', 1e-6)),
            'analog_trigger_voltage': float(pick(data, current, 'analog_trigger_voltage', 0.0)),
            'optimal_control_assets_path': str(
                pick(data, current, 'optimal_control_assets_path',
                     'C:\\Software\\qudi_data\\optimal_control_assets')
            ),
            'pulse_envelope': cls._as_pulse_envelope(
                pick(data, current, 'pulse_envelope', PulseEnvelope(PulseEnvelopeType.rectangle))
            ),
            'pulse_envelope_order': int(pick(data, current, 'pulse_envelope_order', 1)),
        }


def _generation_parameters_to_dict(self):
    """Flat dict of all parameters, built-in ones first.

    The GUI lays out its widgets in this order.
    """
    result = {}
    for contributor in self._CONTRIBUTORS:
        defaults = contributor()
        for name in contributor._own_field_names():
            # Older pickled instances may lack newer fields
            result[name] = getattr(self, name, getattr(defaults, name))
    return result


def _generation_parameters_from_dict(cls, data):
    """Build from a dict. Missing keys use their defaults, unknown keys are ignored."""
    kwargs = {}
    for contributor in cls._CONTRIBUTORS:
        kwargs.update(contributor._coerce_fields(data, None))
    return cls(**kwargs)


def _generation_parameters_update_from_dict(self, data):
    """Return a copy with the values in `data` applied."""
    kwargs = {}
    for contributor in self._CONTRIBUTORS:
        kwargs.update(contributor._coerce_fields(data, self))
    return type(self)(**kwargs)


def _check_is_dataclass(contributor):
    """Raise a TypeError if `contributor` is missing the @dataclass decorator.

    is_dataclass() can't be used, as it is also True for undecorated subclasses.
    """
    if '__dataclass_fields__' not in contributor.__dict__:
        raise TypeError(
            f'{contributor.__name__} is missing the @dataclass(frozen=True) decorator. Without it '
            f'its annotations never become fields, so it would contribute nothing to '
            f'GenerationParameters and its parameters would silently not exist.'
        )


def _warn_about_unrenderable_types(contributor):
    """Warn about fields the Predefined Methods tab can't build a widget for."""
    try:
        hints = get_type_hints(contributor)
    except (NameError, TypeError):
        return
    for name in contributor._own_field_names():
        declared_type = hints.get(name)
        if isinstance(declared_type, type) and not issubclass(declared_type, _GUI_RENDERABLE_TYPES):
            _logger.warning(
                'Generation parameter "%s" declared by %s has type %s, which the Predefined '
                'Methods tab cannot build a widget for - it will be usable from scripts but will '
                'not appear in the GUI. Supported types are str, int, float, bool, Enum and '
                'PulseEnvelope.',
                name, contributor.__name__, declared_type.__name__
            )


def _build_generation_parameters(extensions=None):
    """Merge CoreGenerationParameters and the extensions into one frozen dataclass.

    Called on import and again by rebuild_generation_parameters() on activation, once the
    predefined generator modules are loaded. The result must be bound to the module-level name
    `GenerationParameters` so pickle can find it.

    Parameters
    ----------
    extensions : iterable of BaseGenerationParameters subclasses, optional
        Contributors to merge. Defaults to all declared subclasses.
    """
    if extensions is None:
        extensions = [
            cls for cls in BaseGenerationParameters.__subclasses__() if cls is not CoreGenerationParameters
        ]
    # Sorted, so the result does not depend on import order
    extensions = sorted(extensions, key=lambda cls: cls.__name__)
    contributors = (CoreGenerationParameters, *extensions)

    seen = {}
    for contributor in contributors:
        _check_is_dataclass(contributor)
        _warn_about_unrenderable_types(contributor)
        own_names = contributor._own_field_names()
        if not own_names:
            raise TypeError(
                f'{contributor.__name__} contributes no generation parameters. Every contributor '
                f'must declare at least one field, so either it declares none or _own_field_names() '
                f'failed to see them.'
            )
        for name in own_names:
            if name in seen:
                raise TypeError(
                    f'Duplicate generation parameter "{name}" declared by both '
                    f'{seen[name].__name__} and {contributor.__name__}. Rename one of them - '
                    f'generation parameter names must be unique across all contributors.'
                )
            seen[name] = contributor

    merged = dataclass(frozen=True)(type('GenerationParameters', contributors, {}))
    merged._CONTRIBUTORS = contributors
    merged.to_dict = _generation_parameters_to_dict
    merged.from_dict = classmethod(_generation_parameters_from_dict)
    merged.update_from_dict = _generation_parameters_update_from_dict
    merged.__doc__ = """Global parameters used by the predefined generate methods.

    Combines CoreGenerationParameters with the classes declared in
    generation_parameter_extensions.py.
    """
    return merged


GenerationParameters = _build_generation_parameters()

#: Extensions from generation_parameter_extensions.py, without CoreGenerationParameters.
_DECLARED_EXTENSIONS = tuple(GenerationParameters._CONTRIBUTORS[1:])


def generation_parameters_class():
    """Return the current GenerationParameters class.

    Use this instead of importing GenerationParameters directly, since the class is replaced by
    rebuild_generation_parameters().
    """
    return GenerationParameters


def rebuild_generation_parameters(extensions):
    """Rebuild GenerationParameters from the declared extensions plus `extensions`.

    Adds the contributors declared by predefined generator classes. If the merge fails, the
    error is logged and the current class is kept.

    Parameters
    ----------
    extensions : iterable of BaseGenerationParameters subclasses

    Returns
    -------
    type
        The GenerationParameters class now in effect.
    """
    global GenerationParameters, _DEFAULT_GENERATION_PARAMETERS
    merged = list(_DECLARED_EXTENSIONS)
    merged.extend(cls for cls in extensions if cls not in merged)
    try:
        rebuilt = _build_generation_parameters(merged)
    except TypeError:
        _logger.exception('Cannot merge the declared generation parameter contributors. Keeping '
                          'the current generation parameters:')
        return GenerationParameters
    GenerationParameters = rebuilt
    _DEFAULT_GENERATION_PARAMETERS = rebuilt()
    return rebuilt


@dataclass(frozen=True)
class ActivationConfig:
    """Name of an activation config and the channels it activates."""

    name: str
    channels: set

    @property
    def as_tuple(self):
        return (self.name, set(self.channels))

    def to_dict(self):
        return {'name': self.name, 'channels': set(self.channels)}

    @classmethod
    def from_dict(cls, data):
        return cls(name=str(data['name']), channels=set(data['channels']))

    @classmethod
    def from_tuple(cls, name, channels):
        return cls(name=str(name), channels=set(channels))

    def update_from_dict(self, data):
        return replace(
            self,
            name=str(data.get('name', self.name)),
            channels=set(data.get('channels', self.channels)),
        )


@dataclass(frozen=True)
class AnalogLevels:
    """Analog channel amplitudes (peak-to-peak) and offsets, keyed by channel."""

    amplitude: dict
    offset: dict

    @property
    def as_tuple(self):
        return (dict(self.amplitude), dict(self.offset))

    def to_dict(self):
        return {'amplitude': dict(self.amplitude), 'offset': dict(self.offset)}

    @classmethod
    def from_dict(cls, data):
        return cls(amplitude=dict(data['amplitude']), offset=dict(data['offset']))

    @classmethod
    def from_tuple(cls, levels):
        amplitude, offset = levels
        return cls(amplitude=dict(amplitude), offset=dict(offset))

    def update_from_dict(self, data):
        return replace(
            self,
            amplitude=dict(data.get('amplitude', self.amplitude)),
            offset=dict(data.get('offset', self.offset)),
        )


@dataclass(frozen=True)
class DigitalLevels:
    """Digital channel low and high voltages, keyed by channel."""

    low: dict
    high: dict

    @property
    def as_tuple(self):
        return (dict(self.low), dict(self.high))

    def to_dict(self):
        return {'low': dict(self.low), 'high': dict(self.high)}

    @classmethod
    def from_dict(cls, data):
        return cls(low=dict(data['low']), high=dict(data['high']))

    @classmethod
    def from_tuple(cls, levels):
        low, high = levels
        return cls(low=dict(low), high=dict(high))

    def update_from_dict(self, data):
        return replace(
            self,
            low=dict(data.get('low', self.low)),
            high=dict(data.get('high', self.high)),
        )


@dataclass(frozen=True)
class PulseGeneratorSettings:
    """Local copy of the pulse generator hardware settings."""

    activation_config: ActivationConfig
    sample_rate: float
    analog_levels: AnalogLevels
    digital_levels: DigitalLevels
    interleave: bool
    flags: set
    upload_speed: float

    @property
    def analog_channels(self):
        return {chnl for chnl in self.activation_config.channels if chnl.startswith('a_ch')}

    @property
    def digital_channels(self):
        return {chnl for chnl in self.activation_config.channels if chnl.startswith('d_ch')}

    def to_dict(self):
        return {
            'activation_config': self.activation_config.as_tuple,
            'sample_rate': float(self.sample_rate),
            'analog_levels': self.analog_levels.as_tuple,
            'digital_levels': self.digital_levels.as_tuple,
            'interleave': bool(self.interleave),
            'flags': set(self.flags),
            'upload_speed': float(self.upload_speed),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            activation_config=ActivationConfig.from_tuple(*data['activation_config']),
            sample_rate=float(data['sample_rate']),
            analog_levels=AnalogLevels.from_tuple(data['analog_levels']),
            digital_levels=DigitalLevels.from_tuple(data['digital_levels']),
            interleave=_as_bool(data['interleave']),
            flags=set(data['flags']),
            upload_speed=float(data['upload_speed']),
        )

    def update_from_dict(self, data):
        activation_config = self.activation_config
        if 'activation_config' in data:
            value = data['activation_config']
            activation_config = value if isinstance(value, ActivationConfig) else ActivationConfig.from_tuple(*value)

        analog_levels = self.analog_levels
        if 'analog_levels' in data:
            value = data['analog_levels']
            analog_levels = value if isinstance(value, AnalogLevels) else AnalogLevels.from_tuple(value)

        digital_levels = self.digital_levels
        if 'digital_levels' in data:
            value = data['digital_levels']
            digital_levels = value if isinstance(value, DigitalLevels) else DigitalLevels.from_tuple(value)

        return replace(
            self,
            activation_config=activation_config,
            sample_rate=float(data.get('sample_rate', self.sample_rate)),
            analog_levels=analog_levels,
            digital_levels=digital_levels,
            interleave=_as_bool(data.get('interleave', self.interleave)),
            flags=set(data.get('flags', self.flags)),
            upload_speed=float(data.get('upload_speed', self.upload_speed)),
        )


@dataclass(frozen=True)
class AssetInfo:
    """Length and number of laser pulses of a sampled ensemble or sequence."""

    length_s: float
    length_bins: int
    number_of_lasers: int

    @property
    def as_tuple(self):
        return (self.length_s, self.length_bins, self.number_of_lasers)

    def __iter__(self):
        return iter(self.as_tuple)

    def __len__(self):
        return len(self.as_tuple)

    def __getitem__(self, index):
        return self.as_tuple[index]

    def to_dict(self):
        return {
            'length_s': self.length_s,
            'length_bins': self.length_bins,
            'number_of_lasers': self.number_of_lasers,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            length_s=float(data['length_s']),
            length_bins=int(data['length_bins']),
            number_of_lasers=int(data['number_of_lasers']),
        )

    @classmethod
    def from_tuple(cls, values):
        length_s, length_bins, number_of_lasers = values
        return cls(length_s=float(length_s), length_bins=int(length_bins), number_of_lasers=int(number_of_lasers))


@dataclass
class SequenceSamplingState:
    """Temporary state while sampling a PulseSequence. Not saved."""

    in_progress: bool = False
    offset_bin: int = 0
    written_waveforms: set = field(default_factory=set)
    generated_ensembles: dict = field(default_factory=dict)
    step_results: list = field(default_factory=list)

    def start(self):
        """Called when a new PulseSequence sampling run begins."""
        self.in_progress = True
        self.offset_bin = 0
        self.written_waveforms = set()
        self.generated_ensembles = dict()
        self.step_results = list()

    def finish(self):
        """Called when the PulseSequence sampling run ends."""
        self.in_progress = False

    def record_ensemble(self, name_tag, ensemble_info, waveforms):
        """Store the sampling result for one sequence step's PulseBlockEnsemble."""
        ensemble_info = dict(ensemble_info)
        ensemble_info['waveforms'] = waveforms
        self.generated_ensembles[name_tag] = ensemble_info
        self.written_waveforms.update(waveforms)

    def record_step(self, name_tag, seq_step):
        """Append the waveform names produced for one PulseSequence step."""
        waveforms = tuple(self.generated_ensembles[name_tag]['waveforms'])
        self.step_results.append((waveforms, seq_step))


@dataclass
class PulserBenchmarks:
    """Write and load benchmarks used to estimate the pulser upload speed."""

    write: BenchmarkTool = field(default_factory=BenchmarkTool)
    load: BenchmarkTool = field(default_factory=BenchmarkTool)

    def reset(self):
        self.write.reset()
        self.load.reset()

    def estimate_combined_speed(self):
        """Combined write and load speed in Sa/s, or NaN if there is not enough data."""
        if not (self.write.sanity or self.load.sanity):
            return np.nan
        speed_combined = 1 / (
            1 / self.write.estimate_speed(check_sanity=False) + 1 / self.load.estimate_speed(check_sanity=False)
        )
        return speed_combined if speed_combined >= 0 else np.nan

    def has_valid_estimate(self, ignore=False):
        """True if a valid speed estimate is available or `ignore` is set."""
        if ignore:
            return True
        return not np.isnan(self.estimate_combined_speed())

    def to_dict(self):
        return {'write': self.write.save(), 'load': self.load.save()}

    @classmethod
    def from_dict(cls, data):
        write = BenchmarkTool()
        write.load_from_dict(saved_dict=data['write'])
        load = BenchmarkTool()
        load.load_from_dict(saved_dict=data['load'])
        return cls(write=write, load=load)


@dataclass(frozen=True)
class EnsembleAnalysisResult:
    """Result of SequenceGeneratorLogic.analyze_block_ensemble()."""

    number_of_samples: int
    number_of_elements: int
    elements_length_bins: np.ndarray
    digital_rising_bins: dict
    digital_falling_bins: dict
    analog_channels: set
    digital_channels: set
    channel_set: set
    ideal_length: float
    laser_rising_bins: np.ndarray
    laser_falling_bins: np.ndarray


@dataclass(frozen=True)
class SequenceAnalysisResult:
    """Result of SequenceGeneratorLogic.analyze_sequence(), in total and per step."""

    digital_channels: set
    analog_channels: set
    channel_set: set
    digital_rising_bins: dict
    digital_falling_bins: dict
    number_of_steps: int
    number_of_samples: int
    number_of_samples_per_step: np.ndarray
    number_of_ensembles: int
    ensemble_names: set
    number_of_elements: int
    number_of_elements_per_step: np.ndarray
    elements_length_bins_per_step: list
    ideal_length_per_step: np.ndarray
    ideal_length: float
    laser_rising_bins: np.ndarray
    laser_falling_bins: np.ndarray


@dataclass
class SamplingInformation:
    """Sampling results and waveform names of a sampled ensemble or sequence.

    pulse_generator_settings is kept as a plain dict, since the extraction methods and the
    sampling cache use it as one.
    """
    waveforms: list = field(default_factory=list)
    pulse_generator_settings: Optional[dict] = None
    number_of_samples: int = 0
    number_of_elements: int = 0
    elements_length_bins: np.ndarray = field(default_factory=lambda: np.empty(0, dtype='int64'))
    ideal_length: float = 0.0
    # Used by the ungated extraction methods
    laser_rising_bins: np.ndarray = field(default_factory=lambda: np.empty(0, dtype='int64'))
    laser_falling_bins: np.ndarray = field(default_factory=lambda: np.empty(0, dtype='int64'))

    # Sequence-specific fields
    step_waveform_list: list = field(default_factory=list)
    ensemble_info: dict = field(default_factory=dict)

    # Unknown keys
    _legacy_data: dict = field(default_factory=dict)

    #: Keys that are dropped instead of stored in _legacy_data. Generation parameters belong to
    #: SequenceGeneratorSettings; this also removes them from older saved assets.
    _REJECTED_KEYS: ClassVar[frozenset] = frozenset({'generation_parameters'})

    def __bool__(self):
        """True if the asset has been sampled, i.e. waveforms are stored."""
        return bool(self.waveforms)

    def _field_names(self):
        return {f.name for f in fields(self) if f.name != '_legacy_data'}

    def __contains__(self, key):
        return key in self._field_names() or key in self._legacy_data

    def __getitem__(self, key):
        if key in self._field_names():
            return getattr(self, key)
        if key in self._legacy_data:
            return self._legacy_data[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key in self._REJECTED_KEYS:
            return
        if key in self._field_names():
            setattr(self, key, value)
        else:
            self._legacy_data[key] = value

    def __delitem__(self, key):
        if key in self._field_names():
            raise KeyError('Cannot delete required field "{0}" from SamplingInformation.'.format(key))
        del self._legacy_data[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def update(self, other=(), **kwargs):
        items = other.items() if hasattr(other, 'items') else other
        for key, value in items:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def copy(self):
        """Return an independent copy."""
        return replace(
            self,
            waveforms=list(self.waveforms),
            pulse_generator_settings=(
                None if self.pulse_generator_settings is None else dict(self.pulse_generator_settings)
            ),
            elements_length_bins=self.elements_length_bins.copy(),
            laser_rising_bins=self.laser_rising_bins.copy(),
            laser_falling_bins=self.laser_falling_bins.copy(),
            step_waveform_list=list(self.step_waveform_list),
            ensemble_info=dict(self.ensemble_info),
            _legacy_data=dict(self._legacy_data),
        )

    def to_dict(self) -> dict:
        """Flat dict of all fields and unknown keys."""
        data = {name: getattr(self, name) for name in self._field_names()}
        data.update(self._legacy_data)
        return data

    @classmethod
    def from_dict(cls, data: dict):
        """Build from a dict. Unknown keys are kept in _legacy_data."""
        if not data:
            return cls()

        # A YAML dump contains '_legacy_data' as a key, so merge it instead of passing it twice
        known_field_names = {f.name for f in fields(cls) if f.name != '_legacy_data'}
        init_kwargs = {}
        legacy_kwargs = {
            key: value
            for key, value in (data.get('_legacy_data') or {}).items()
            if key not in cls._REJECTED_KEYS
        }

        for key, value in data.items():
            if key == '_legacy_data' or key in cls._REJECTED_KEYS:
                continue
            elif key in known_field_names:
                init_kwargs[key] = value
            else:
                legacy_kwargs[key] = value

        return cls(**init_kwargs, _legacy_data=legacy_kwargs)


@dataclass(frozen=True)
class LoadedAsset:
    """Name and type of the asset loaded on the pulse generator.

    asset_type is 'PulseBlockEnsemble', 'PulseSequence' or ''. Also supports tuple-style
    unpacking and indexing.
    """

    name: str
    asset_type: str

    @property
    def as_tuple(self):
        return (self.name, self.asset_type)

    def __iter__(self):
        return iter(self.as_tuple)

    def __len__(self):
        return len(self.as_tuple)

    def __getitem__(self, index):
        return self.as_tuple[index]

    def __bool__(self):
        """True if both name and asset type are set."""
        return bool(self.name) and bool(self.asset_type)

    @classmethod
    def empty(cls):
        return cls(name='', asset_type='')


# Defaults used by SequenceGeneratorSettings.from_dict() for missing keys and by
# SequenceGeneratorLogic for new settings.
_DEFAULT_GENERATION_PARAMETERS = GenerationParameters()
_DEFAULT_PULSE_GENERATOR_SETTINGS = PulseGeneratorSettings(
    activation_config=ActivationConfig(name='', channels=set()),
    sample_rate=0.0,
    analog_levels=AnalogLevels(amplitude=dict(), offset=dict()),
    digital_levels=DigitalLevels(low=dict(), high=dict()),
    interleave=False,
    flags=set(),
    upload_speed=np.nan,
)


@dataclass(frozen=True)
class SequenceGeneratorSettings:
    """Settings saved by SequenceGeneratorLogic: generation parameters and pulse generator
    settings."""

    generation_parameters: GenerationParameters
    pulse_generator_settings: PulseGeneratorSettings

    def to_dict(self):
        return {
            'generation_parameters': self.generation_parameters.to_dict(),
            'pulse_generator_settings': self.pulse_generator_settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        """Missing keys fall back to their defaults."""
        return cls(
            generation_parameters=(
                generation_parameters_class().from_dict(data['generation_parameters'])
                if 'generation_parameters' in data
                else _DEFAULT_GENERATION_PARAMETERS
            ),
            pulse_generator_settings=(
                PulseGeneratorSettings.from_dict(data['pulse_generator_settings'])
                if 'pulse_generator_settings' in data
                else _DEFAULT_PULSE_GENERATOR_SETTINGS
            ),
        )

    #: StatusVar names of the old status file format, used to detect it.
    LEGACY_STATUS_VAR_KEYS = frozenset({
        '_generation_parameters',
        '_benchmark_write_state',
        '_benchmark_load_state',
    })

    @classmethod
    def from_legacy_dict(cls, raw):
        """Build settings from the old status file format.

        pulse_generator_settings is not saved in the old format and is read from hardware on
        activation.
        """
        generation_parameters = raw.get('_generation_parameters')
        return cls(
            generation_parameters=(
                generation_parameters_class().from_dict(generation_parameters)
                if isinstance(generation_parameters, dict)
                else _DEFAULT_GENERATION_PARAMETERS
            ),
            pulse_generator_settings=_DEFAULT_PULSE_GENERATOR_SETTINGS,
        )

    def update_from_dict(self, data):
        generation_parameters = self.generation_parameters
        if 'generation_parameters' in data:
            value = data['generation_parameters']
            generation_parameters = (
                value
                if isinstance(value, generation_parameters_class())
                else self.generation_parameters.update_from_dict(value)
            )

        pulse_generator_settings = self.pulse_generator_settings
        if 'pulse_generator_settings' in data:
            value = data['pulse_generator_settings']
            pulse_generator_settings = (
                value
                if isinstance(value, PulseGeneratorSettings)
                else self.pulse_generator_settings.update_from_dict(value)
            )

        return replace(
            self,
            generation_parameters=generation_parameters,
            pulse_generator_settings=pulse_generator_settings,
        )