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
from typing import Optional
import numpy as np

from qudi.logic.pulsed.sampling_functions import PulseEnvelope
from qudi.util.benchmark import BenchmarkTool


##############################################################################
#  The following dataclasses are for the SequenceGeneratorLogic class and    #
#   are used to store and manage the settings for pulse/sequence object      #
#              generation and the connected pulse generator hardware         #
##############################################################################
def _as_bool(value):
    """Converts a value to a boolean. If the value is a string,
    it checks for common truthy values.
    """
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


@dataclass(frozen=True)
class GenerationParameters:
    """Global parameters describing the channel usage and common parameters used during
    pulsed object generation for predefined methods."""

    laser_channel: str
    sync_channel: str
    gate_channel: str
    microwave_channel: str
    microwave_frequency: float
    microwave_amplitude: float
    rabi_period: float
    laser_length: float
    laser_delay: float
    wait_time: float
    analog_trigger_voltage: float
    optimal_control_assets_path: str
    pulse_envelope: PulseEnvelope
    pulse_envelope_order: int

    def to_dict(self):
        return {
            'laser_channel': self.laser_channel,
            'sync_channel': self.sync_channel,
            'gate_channel': self.gate_channel,
            'microwave_channel': self.microwave_channel,
            'microwave_frequency': self.microwave_frequency,
            'microwave_amplitude': self.microwave_amplitude,
            'rabi_period': self.rabi_period,
            'laser_length': self.laser_length,
            'laser_delay': self.laser_delay,
            'wait_time': self.wait_time,
            'analog_trigger_voltage': self.analog_trigger_voltage,
            'optimal_control_assets_path': self.optimal_control_assets_path,
            'pulse_envelope': self.pulse_envelope,
            'pulse_envelope_order': self.pulse_envelope_order,
        }

    @staticmethod
    def _as_pulse_envelope(value):
        return value if isinstance(value, PulseEnvelope) else PulseEnvelope.from_dict(value)

    @classmethod
    def from_dict(cls, data):
        return cls(
            laser_channel=str(data['laser_channel']),
            sync_channel=str(data['sync_channel']),
            gate_channel=str(data['gate_channel']),
            microwave_channel=str(data['microwave_channel']),
            microwave_frequency=float(data['microwave_frequency']),
            microwave_amplitude=float(data['microwave_amplitude']),
            rabi_period=float(data['rabi_period']),
            laser_length=float(data['laser_length']),
            laser_delay=float(data['laser_delay']),
            wait_time=float(data['wait_time']),
            analog_trigger_voltage=float(data['analog_trigger_voltage']),
            optimal_control_assets_path=str(data['optimal_control_assets_path']),
            pulse_envelope=cls._as_pulse_envelope(data['pulse_envelope']),
            pulse_envelope_order=int(data['pulse_envelope_order']),
        )

    def update_from_dict(self, data):
        return replace(
            self,
            laser_channel=str(data.get('laser_channel', self.laser_channel)),
            sync_channel=str(data.get('sync_channel', self.sync_channel)),
            gate_channel=str(data.get('gate_channel', self.gate_channel)),
            microwave_channel=str(data.get('microwave_channel', self.microwave_channel)),
            microwave_frequency=float(data.get('microwave_frequency', self.microwave_frequency)),
            microwave_amplitude=float(data.get('microwave_amplitude', self.microwave_amplitude)),
            rabi_period=float(data.get('rabi_period', self.rabi_period)),
            laser_length=float(data.get('laser_length', self.laser_length)),
            laser_delay=float(data.get('laser_delay', self.laser_delay)),
            wait_time=float(data.get('wait_time', self.wait_time)),
            analog_trigger_voltage=float(data.get('analog_trigger_voltage', self.analog_trigger_voltage)),
            optimal_control_assets_path=str(
                data.get('optimal_control_assets_path', self.optimal_control_assets_path)
            ),
            pulse_envelope=self._as_pulse_envelope(data.get('pulse_envelope', self.pulse_envelope)),
            pulse_envelope_order=int(data.get('pulse_envelope_order', self.pulse_envelope_order)),
        )


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
        return (self.amplitude, self.offset)

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
        return (self.low, self.high)

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
        return replace(self, _legacy_data=dict(self._legacy_data))

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