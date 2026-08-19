# -*- coding: utf-8 -*-
"""
This file contains the dataclasses used by SequenceGeneratorLogic to store and manage the
settings for pulse/sequence object generation and the connected pulse generator hardware.

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
from typing import Optional, get_type_hints
import numpy as np

from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType
from qudi.util.benchmark import BenchmarkTool

# Imported for its side effect as much as for the name: importing this module defines every
# lab-declared BaseGenerationParameters subclass, which _build_generation_parameters() below then
# merges with CoreGenerationParameters into the public GenerationParameters class. The base lives
# over there so that file never has to import back from this one - a cycle would make the merge
# silently skip extensions depending on which module happened to be imported first.
from qudi.logic.pulsed.pulsed_data.generation_parameter_extensions import (
    BaseGenerationParameters,
    as_bool as _as_bool,
)
import qudi.logic.pulsed.pulsed_data.generation_parameter_extensions as _generation_parameter_extensions  # noqa: F401

_logger = getLogger(__name__)

#: Field types pulsed_maingui._create_pm_global_params() can build a widget for. A generation
#: parameter outside this set still works from a script, but never appears on the Predefined
#: Methods tab - _build_generation_parameters() warns about one at import rather than leaving it to
#: surface as a GUI error at activation.
_GUI_RENDERABLE_TYPES = (str, int, float, bool, Enum, PulseEnvelope)


##############################################################################
#  The following dataclasses are for the SequenceGeneratorLogic class and    #
#   are used to store and manage the settings for pulse/sequence object      #
#              generation and the connected pulse generator hardware         #
##############################################################################
@dataclass(frozen=True)
class CoreGenerationParameters(BaseGenerationParameters):
    """Global parameters describing the channel usage and common parameters used during
    pulsed object generation for predefined methods.

    The built-in contributor to `GenerationParameters`. Note this class is NOT the type the rest
    of the toolchain annotates against - that is the merged `GenerationParameters` built at the
    bottom of this module, which includes these fields plus every lab extension's.
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
        """Kept explicit, unlike a lab extension, which inherits the type-driven version in
        BaseGenerationParameters. These are the built-in settings: spelling out every conversion and
        default in one reviewable block is worth the duplication here, and it pins the behaviour the
        automatic path is expected to reproduce."""
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
    """Flat dict of every parameter, contributors in order (core first, then extensions by name).

    Order matters beyond aesthetics: the GUI builds the Predefined Methods tab's global-parameter
    widget grid by iterating these items (pulsed_maingui._create_pm_global_params), so relying on
    dataclasses.fields() here would put extension fields ahead of the built-in ones - field order
    on a merged dataclass is reverse-MRO.
    """
    result = {}
    for contributor in self._CONTRIBUTORS:
        defaults = contributor()
        for name in contributor._own_field_names():
            # Default fallback: an instance unpickled from a saved .ensemble predates the current
            # contributor set, so it can be missing a field the schema now has.
            result[name] = getattr(self, name, getattr(defaults, name))
    return result


def _generation_parameters_from_dict(cls, data):
    """Build from a saved dict, falling back to defaults for anything absent.

    Tolerant by design: a status file written before a parameter existed (or by an install without
    a given extension) must still load. The pre-refactor version indexed `data['...']` directly, so
    one missing key raised KeyError inside the StatusVar constructor and silently reset *every*
    generation parameter to its default. Unknown keys are ignored rather than rejected, so removing
    an extension does not make its old status file unloadable.
    """
    kwargs = {}
    for contributor in cls._CONTRIBUTORS:
        kwargs.update(contributor._coerce_fields(data, None))
    return cls(**kwargs)


def _generation_parameters_update_from_dict(self, data):
    """Return a new instance with the parameters named in `data` applied on top of this one."""
    kwargs = {}
    for contributor in self._CONTRIBUTORS:
        kwargs.update(contributor._coerce_fields(data, self))
    return type(self)(**kwargs)


def _check_is_dataclass(contributor):
    """Raise if a contributor was declared without the @dataclass decorator.

    `dataclasses.is_dataclass()` cannot be used: it reports True for an undecorated subclass,
    because `__dataclass_fields__` is inherited from BaseGenerationParameters. Only the decorator
    writes that attribute into the class's own __dict__.

    Parameters
    ----------
    contributor : type
        A BaseGenerationParameters subclass about to be merged.

    Raises
    ------
    TypeError
        If `contributor` itself was never processed by @dataclass.
    """
    if '__dataclass_fields__' not in contributor.__dict__:
        raise TypeError(
            f'{contributor.__name__} is missing the @dataclass(frozen=True) decorator. Without it '
            f'its annotations never become fields, so it would contribute nothing to '
            f'GenerationParameters and its parameters would silently not exist.'
        )


def _warn_about_unrenderable_types(contributor):
    """Log a warning for each field whose type the Predefined Methods tab cannot build a widget for.

    Parameters
    ----------
    contributor : type
        A BaseGenerationParameters subclass about to be merged.
    """
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
    """Merge CoreGenerationParameters with every lab extension into one frozen dataclass.

    Runs at module import to establish the built-in schema, and again from
    rebuild_generation_parameters() during SequenceGeneratorLogic.on_activate() once the predefined
    generator modules have been imported and can be asked what they declare.

    The import-time build happens before qudi-core restores status variables
    (LogicBase.__activation_callback does _load_status_variables() then on_activate()), so a
    parameter that only joins on the second pass has already been dropped from the restored
    settings - SequenceGeneratorLogic._merge_generator_declared_parameters() re-reads those values
    off the status file rather than letting them reset.

    The result is bound to the module-level name `GenerationParameters`, matching the class's own
    __name__/__qualname__, so pickle can resolve it. That matters: a GenerationParameters instance
    is pickled into every saved .ensemble/.sequence via SamplingInformation (see
    sequence_generator_logic.sample_pulse_block_ensemble).

    Parameters
    ----------
    extensions : iterable of BaseGenerationParameters subclasses, optional
        Contributors to merge alongside CoreGenerationParameters. Defaults to discovering every
        declared subclass, which is what production uses; tests pass an explicit list so a
        throwaway contributor cannot leak into a later build (__subclasses__() only drops entries
        once the class object is garbage collected, and the merged class keeps its bases alive).
    """
    if extensions is None:
        extensions = [
            cls for cls in BaseGenerationParameters.__subclasses__() if cls is not CoreGenerationParameters
        ]
    extensions = sorted(extensions, key=lambda cls: cls.__name__)
    # Core first so it wins any tie and reads first in tracebacks; extensions in a deterministic
    # (name-sorted) order so the merged schema does not depend on import order.
    contributors = (CoreGenerationParameters, *extensions)

    # Reject duplicate field names. The method/sampling-function registries elsewhere in the
    # pulsed toolchain resolve collisions by silent last-wins, which is survivable there (you see
    # the wrong pulse shape) but not for a settings value - it would silently change a measurement
    # parameter with no other signal.
    seen = {}
    for contributor in contributors:
        _check_is_dataclass(contributor)
        _warn_about_unrenderable_types(contributor)
        own_names = contributor._own_field_names()
        if not own_names:
            # A contributor that declares nothing is always a fault, never an intention. Caught
            # here because the symptom otherwise surfaces far away: an empty parameter grid and a
            # status file with no generation parameters in it, with nothing raised in between.
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
    # Attached here rather than to the module-level GenerationParameters, so every class this
    # builder produces carries them - including the ones built in tests.
    merged.to_dict = _generation_parameters_to_dict
    merged.from_dict = classmethod(_generation_parameters_from_dict)
    merged.update_from_dict = _generation_parameters_update_from_dict
    merged.__doc__ = """Global parameters describing the channel usage and common parameters used
    during pulsed object generation for predefined methods.

    Built at import time by merging CoreGenerationParameters (the built-in settings) with every
    BaseGenerationParameters subclass declared in generation_parameter_extensions.py, so a lab's
    own parameters are indistinguishable from the built-in ones at every call site. See
    _build_generation_parameters() and the extensions module's docstring.
    """
    return merged


GenerationParameters = _build_generation_parameters()

#: What generation_parameter_extensions.py declared, captured before any rebuild can widen the
#: schema. [0] is CoreGenerationParameters, which _build_generation_parameters() re-adds itself.
_DECLARED_EXTENSIONS = tuple(GenerationParameters._CONTRIBUTORS[1:])


def generation_parameters_class():
    """The GenerationParameters class currently in effect.

    Always call this instead of holding a `from ... import GenerationParameters` binding:
    rebuild_generation_parameters() rebinds the module global, and an imported name would keep
    pointing at the superseded class.
    """
    return GenerationParameters


def rebuild_generation_parameters(extensions):
    """Rebuild GenerationParameters as the extensions file plus `extensions`, rebinding the globals.

    Lets contributors declared by predefined generator classes join the schema, which the
    import-time build cannot see - those modules are imported during module activation, long after
    this one.

    Always rebuilt from `_DECLARED_EXTENSIONS` rather than from whatever the last call produced, so
    repeated calls are idempotent. Module re-activation re-imports the generator modules and hands
    over freshly created contributor classes; accumulating them would collide with the equivalent
    classes from the previous activation on every field name.

    A contributor the merge rejects is logged and the current class kept, so a faulty lab
    declaration costs its own parameters rather than the whole toolchain.

    Parameters
    ----------
    extensions : iterable of BaseGenerationParameters subclasses

    Returns
    -------
    type
        The class now in effect - the rebuilt one, or the existing one if the merge failed.
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
    """The activation config name and the set of pulse generator channels it activates."""

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
    """Pulse generator analog channel levels (pp-amplitude and offset), keyed by channel
    descriptor."""

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
    """Pulse generator digital channel levels (low/high voltage), keyed by channel descriptor."""

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
    """Current pulse generator hardware settings, mirrored locally by SequenceGeneratorLogic to
    avoid repeated slow hardware reads."""

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
    """Length and laser-pulse-count summary for a sampled PulseBlockEnsemble or PulseSequence."""

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
    """Tracks the bookkeeping needed while sampling a PulseSequence step-by-step.

    One instance is created per sampling run and discarded once the run finishes
    (successfully or not) - it is not persisted.
    """

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
        """Called when the PulseSequence sampling run ends, successfully or not."""
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
    """Bundles the write/load BenchmarkTool instances used to estimate pulser upload speed."""

    write: BenchmarkTool = field(default_factory=BenchmarkTool)
    load: BenchmarkTool = field(default_factory=BenchmarkTool)

    def reset(self):
        self.write.reset()
        self.load.reset()

    def estimate_combined_speed(self):
        """Combined write+load speed estimate in Sa/s, or NaN if there isn't enough data yet."""
        if not (self.write.sanity or self.load.sanity):
            return np.nan
        speed_combined = 1 / (
            1 / self.write.estimate_speed(check_sanity=False) + 1 / self.load.estimate_speed(check_sanity=False)
        )
        return speed_combined if speed_combined >= 0 else np.nan

    def has_valid_estimate(self, ignore=False):
        """Whether a sane combined speed estimate is available, or True if checks are ignored."""
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
    """Result of analyzing a single PulseBlockEnsemble: sample/element counts, channel
    transition timings and the generation parameters used, as returned by
    SequenceGeneratorLogic.analyze_block_ensemble()."""

    number_of_samples: int
    number_of_elements: int
    elements_length_bins: np.ndarray
    digital_rising_bins: dict
    digital_falling_bins: dict
    analog_channels: set
    digital_channels: set
    channel_set: set
    generation_parameters: GenerationParameters
    ideal_length: float
    laser_rising_bins: np.ndarray
    laser_falling_bins: np.ndarray


@dataclass(frozen=True)
class SequenceAnalysisResult:
    """Result of analyzing a PulseSequence: the same information as
    EnsembleAnalysisResult, aggregated over the whole sequence plus broken down per step, as
    returned by SequenceGeneratorLogic.analyze_sequence()."""

    digital_channels: set
    analog_channels: set
    channel_set: set
    generation_parameters: GenerationParameters
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


# Note: SamplingInformation is the only class in this file that is also used by
# pulsed_measurement_logic (imported directly from there). It stores the results of the
# ensemble/sequence analysis performed by SequenceGeneratorLogic as well as the results of the
# sampling performed by PulsedMeasurementLogic.
@dataclass
class SamplingInformation:
    """Stores sampling metadata and waveform references for generated ensembles and sequences.

    Note: pulse_generator_settings is typed as Optional[dict], NOT PulseGeneratorSettings,
    even though it conceptually is a pulse generator settings snapshot. It is always populated
    from SequenceGeneratorLogic.pulse_generator_settings, which itself returns
    PulseGeneratorSettings.to_dict() (a plain dict) rather than the dataclass instance - see
    sequence_generator_logic.py's sample_pulse_sequence/from_dict call sites. Two call sites
    depend on this being a plain dict: the sampling-cache comparison in
    SequenceGeneratorLogic.sample_pulse_sequence (dict equality) and
    pulse_extraction_methods/basic_extraction_methods.py (dict subscript access,
    e.g. sampling_information['pulse_generator_settings']['sample_rate']). Reconstructing a real
    PulseGeneratorSettings instance here would require updating both of those call sites plus
    defensively handling older pickled sessions whose pulse_generator_settings dict may not match
    the current schema - left as a plain dict to avoid that risk.
    """
    waveforms: list = field(default_factory=list)
    pulse_generator_settings: Optional[dict] = None
    number_of_samples: int = 0
    number_of_elements: int = 0
    elements_length_bins: np.ndarray = field(default_factory=lambda: np.empty(0, dtype='int64'))
    ideal_length: float = 0.0
    # Populated from the EnsembleAnalysisResult/SequenceAnalysisResult computed at sampling time
    # (sequence_generator_logic.py: info_dict = _dataclass_to_dict(ensemble_info)/
    # _dataclass_to_dict(self.analyze_sequence(sequence)), which dumps every field of that result
    # - these two used to only arrive here implicitly, via that dump landing in _legacy_data
    # (since they weren't declared fields), rather than because they were designed to live here.
    # Read by pulse_extraction_methods/basic_extraction_methods.py's ungated_gated_conv_deriv().
    laser_rising_bins: np.ndarray = field(default_factory=lambda: np.empty(0, dtype='int64'))
    laser_falling_bins: np.ndarray = field(default_factory=lambda: np.empty(0, dtype='int64'))

    # Sequence-specific fields
    step_waveform_list: list = field(default_factory=list)
    ensemble_info: dict = field(default_factory=dict)

    # Legacy fields catch-all (optional, to ensure no data is lost)
    _legacy_data: dict = field(default_factory=dict)

    def __bool__(self):
        """
        Crucial for Qudi logic. Returns True only if the object has actually been sampled
        (indicated by the presence of stored waveforms).
        """
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
        """Independent copy: mutating any mutable field on the copy never affects the original."""
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
        """Inverse of from_dict(): flattens the known dataclass fields plus any legacy/unknown
        keys back into a plain dict."""
        data = {name: getattr(self, name) for name in self._field_names()}
        data.update(self._legacy_data)
        return data

    @classmethod
    def from_dict(cls, data: dict):
        """Safely parses a legacy dictionary into the dataclass."""
        if not data:
            return cls()

        # Extract known fields, excluding '_legacy_data' itself: a round-tripped YAML dump (via
        # the generic dataclass_representer registered for this class) includes '_legacy_data' as
        # a top-level key alongside the real fields, since it iterates over ALL dataclass fields.
        # Treating it as a "known field" here would make it end up in both init_kwargs and the
        # explicit _legacy_data= kwarg below, raising "got multiple values for keyword argument
        # '_legacy_data'". Instead, seed legacy_kwargs from it (if present) and merge in any other
        # unrecognized keys on top.
        known_field_names = {f.name for f in fields(cls) if f.name != '_legacy_data'}
        init_kwargs = {}
        legacy_kwargs = dict(data.get('_legacy_data') or {})

        for key, value in data.items():
            if key == '_legacy_data':
                continue
            elif key in known_field_names:
                init_kwargs[key] = value
            else:
                legacy_kwargs[key] = value

        return cls(**init_kwargs, _legacy_data=legacy_kwargs)


@dataclass(frozen=True)
class LoadedAsset:
    """Name and type ('PulseBlockEnsemble', 'PulseSequence', or '' if nothing/unknown is
    currently loaded) of the waveform/sequence currently loaded on the pulse generator hardware.

    Supports iteration/indexing so existing tuple-style call sites (*loaded_asset,
    loaded_asset[0], loaded_asset[1]) keep working unchanged - the same backward-compatibility
    pattern already used by AssetInfo above.
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
        """True only if both a name and a recognized asset type are set."""
        return bool(self.name) and bool(self.asset_type)

    @classmethod
    def empty(cls):
        return cls(name='', asset_type='')


# Default sub-settings for SequenceGeneratorSettings.from_dict()'s per-field fallbacks (a saved
# settings file predating a given field) and for sequence_generator_logic.py's
# _default_generator_settings() (a brand new settings object). Defined once, here, since this
# file can't import back from sequence_generator_logic.py (that direction would be circular) -
# this is the lower-level file, so it owns the shared literals instead.
#
# Every field now carries its default on the dataclass itself (required: a merged dataclass cannot
# have a no-default field following a defaulted one), so this is just a zero-argument construction
# rather than the explicit field-by-field literal it used to be. Extensions supply their own
# defaults the same way, so this stays correct however many are declared.
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
    """Single settings container bundling everything SequenceGeneratorLogic persists as
    user-configurable settings: the generation parameters (laser/microwave channels, timings,
    ...) and the pulse generator hardware settings (activation config, sample rate, levels, ...).

    Note: pulser_benchmarks is intentionally NOT part of this container - it's runtime telemetry
    (hardware upload/download speed statistics), not a measurement-defining setting, and is
    persisted as its own independent, per-instance StatusVar directly on SequenceGeneratorLogic
    instead (see `pulser_benchmarks` there).
    """

    generation_parameters: GenerationParameters
    pulse_generator_settings: PulseGeneratorSettings

    def to_dict(self):
        return {
            'generation_parameters': self.generation_parameters.to_dict(),
            'pulse_generator_settings': self.pulse_generator_settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        """Tolerant of a saved dict missing keys added after it was written - falls back to
        that field's own default rather than raising KeyError."""
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

    #: Top-level status-file key names used by the 3 individually-declared StatusVars that
    #: existed on SequenceGeneratorLogic before this class was introduced (one file-level key
    #: per StatusVar, instead of everything nested under a single '_generator_settings' key).
    #: Used only to detect a pre-refactor status file - see
    #: SequenceGeneratorLogic._migrate_legacy_settings_if_needed().
    LEGACY_STATUS_VAR_KEYS = frozenset({
        '_generation_parameters',
        '_benchmark_write_state',
        '_benchmark_load_state',
    })

    @classmethod
    def from_legacy_dict(cls, raw):
        """Reconstruct settings from the pre-refactor format, where '_generation_parameters' was
        its own StatusVar holding the same flat dict shape GenerationParameters.from_dict() expects.

        pulse_generator_settings is deliberately not migrated - the old format never persisted it
        either, and on_activate() always re-derives it fresh from hardware regardless.

        '_benchmark_write_state'/'_benchmark_load_state' stay in LEGACY_STATUS_VAR_KEYS (valid
        legacy-format signals) but aren't reconstructed here - pulser_benchmarks is no longer a
        field of this class (see the class docstring); SequenceGeneratorLogic.
        _migrate_legacy_settings_if_needed() migrates it directly into its own StatusVar instead.
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