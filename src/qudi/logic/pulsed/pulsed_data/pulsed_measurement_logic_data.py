# -*- coding: utf-8 -*-
"""
This file contains the dataclasses used by PulsedMeasurementLogic to store and manage its
settings and data for pulsed measurements.

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
from dataclasses import dataclass, replace, field, fields, asdict
from typing import ClassVar, Optional
import pprint
import time
import numpy as np


##############################################################################
#   The following dataclasses are for the PulsedMeasurementLogic class and   #
# are used to store and manage the settings and data for pulsed measurements #
##############################################################################
def _as_bool(value):
    """Converts a value to a boolean. If the value is a string,
    it checks for common truthy values.
    """
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


@dataclass(frozen=True)
class FastCounterSettings:
    """Settings used to configure and describe the fast counter."""

    bin_width: float
    record_length: float
    number_of_gates: int
    is_gated: bool

    def to_dict(self):
        return {
            'bin_width': self.bin_width,
            'record_length': self.record_length,
            'number_of_gates': self.number_of_gates,
            'is_gated': self.is_gated,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            bin_width=float(data['bin_width']),
            record_length=float(data['record_length']),
            number_of_gates=int(data['number_of_gates']),
            is_gated=_as_bool(data['is_gated']),
        )

    def update_from_dict(self, data):
        return replace(
            self,
            bin_width=float(data.get('bin_width', self.bin_width)),
            record_length=float(data.get('record_length', self.record_length)),
            number_of_gates=int(data.get('number_of_gates', self.number_of_gates)),
            is_gated=_as_bool(data.get('is_gated', self.is_gated)),
        )


@dataclass(frozen=True)
class MicrowaveSettings:
    """External microwave settings used during a pulsed measurement."""

    power: float
    frequency: float
    use_ext_microwave: bool

    def to_dict(self):
        return {
            'power': self.power,
            'frequency': self.frequency,
            'use_ext_microwave': self.use_ext_microwave,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            power=float(data['power']),
            frequency=float(data['frequency']),
            use_ext_microwave=_as_bool(data['use_ext_microwave']),
        )

    def update_from_dict(self, data):
        return replace(
            self,
            power=float(data.get('power', self.power)),
            frequency=float(data.get('frequency', self.frequency)),
            use_ext_microwave=_as_bool(data.get('use_ext_microwave', self.use_ext_microwave)),
        )


@dataclass(frozen=True)
class ReadoutSettings:
    """User-facing settings that define how a pulsed measurement is analyzed."""

    invoke_settings: bool
    controlled_variable: np.ndarray
    number_of_lasers: int
    laser_ignore_list: list[int]
    alternating: bool
    units: tuple[str, str]
    labels: tuple[str, str]

    def __post_init__(self):
        # Copy first so freezing this array's writeable flag never affects a shared/live array
        # some future caller might construct this with directly.
        object.__setattr__(self, 'controlled_variable', self.controlled_variable.copy())
        self.controlled_variable.flags.writeable = False
        # Sorted here rather than only in update_from_dict() so the invariant holds for every
        # construction path - from_dict(), replace(), and direct construction alike. sorted()
        # also returns a new list, so a caller's live list can never alias our state.
        object.__setattr__(self, 'laser_ignore_list', sorted(self.laser_ignore_list))

    
    def to_dict(self):
        return {
            'invoke_settings': self.invoke_settings,
            'controlled_variable': self.controlled_variable.copy(),
            'number_of_lasers': self.number_of_lasers,
            'laser_ignore_list': self.laser_ignore_list.copy(),
            'alternating': self.alternating,
            'units': self.units,
            'labels': self.labels,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            invoke_settings=_as_bool(data['invoke_settings']),
            controlled_variable=np.array(data['controlled_variable'], dtype=float),
            number_of_lasers=int(data['number_of_lasers']),
            laser_ignore_list=sorted(data['laser_ignore_list']),
            alternating=_as_bool(data['alternating']),
            units=tuple(data['units']),
            labels=tuple(data['labels']),
        )

    def update_from_dict(self, data):
        return replace(
            self,
            invoke_settings=_as_bool(data.get('invoke_settings', self.invoke_settings)),
            controlled_variable=np.array(data.get('controlled_variable', self.controlled_variable), dtype=float),
            number_of_lasers=int(data.get('number_of_lasers', self.number_of_lasers)),
            laser_ignore_list=sorted(data.get('laser_ignore_list', self.laser_ignore_list)),
            alternating=_as_bool(data.get('alternating', self.alternating)),
            units=tuple(data.get('units', self.units)),
            labels=tuple(data.get('labels', self.labels)),
        )


@dataclass
class PulsedMeasurementData:
    """Arrays and timing information that make up the current pulsed measurement."""

    raw_data: np.ndarray
    laser_data: np.ndarray
    signal_data: np.ndarray
    signal_alt_data: np.ndarray
    measurement_error: np.ndarray
    elapsed_time: float =0.0
    elapsed_sweeps: int=0

    def copy(self):
        return type(self)(
            raw_data=self.raw_data.copy(),
            laser_data=self.laser_data.copy(),
            signal_data=self.signal_data.copy(),
            signal_alt_data=self.signal_alt_data.copy(),
            measurement_error=self.measurement_error.copy(),
            elapsed_time=float(self.elapsed_time),
            elapsed_sweeps=int(self.elapsed_sweeps),
        )

    def to_dict(self):
        return {
            'raw_data': self.raw_data.copy(),
            'laser_data': self.laser_data.copy(),
            'signal_data': self.signal_data.copy(),
            'signal_alt_data': self.signal_alt_data.copy(),
            'measurement_error': self.measurement_error.copy(),
            'elapsed_time': self.elapsed_time,
            'elapsed_sweeps': self.elapsed_sweeps,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            raw_data=np.array(data['raw_data']),
            laser_data=np.array(data['laser_data']),
            signal_data=np.array(data['signal_data'], dtype=float),
            signal_alt_data=np.array(data['signal_alt_data'], dtype=float),
            measurement_error=np.array(data['measurement_error'], dtype=float),
            elapsed_time=float(data['elapsed_time']),
            elapsed_sweeps=int(data['elapsed_sweeps']),
        )
@dataclass
class DataStashCache:
    """Manages the memory for stashed/recalled raw data."""
    cache: dict = field(default_factory=dict)
    active_tag: Optional[str] = None

    def stash(self, tag: str, raw_data: np.ndarray, sweeps: int, time_elapsed: float):
        self.cache[tag] = {
            'data': raw_data.copy(),
            'elapsed_sweeps': sweeps,
            'elapsed_time': time_elapsed
        }

    def recall(self, tag: str):
        if tag in self.cache:
            self.active_tag = tag
            return self.cache[tag]
        self.active_tag = None
        return None

    def clear_active(self):
        self.active_tag = None

    def get_active(self):
        return self.cache.get(self.active_tag, None)

@dataclass
class AlternativeSignalSettings:
    """Settings for the alternative (second) plot: which AltPlotAnalyzer method is selected, plus
    every loaded method's parameter values.

    `parameters` stays a plain dict (not fixed fields) because AltPlotAnalyzer discovers parameter
    names dynamically from whichever AltPlotMethodBase subclasses are loaded - built-in ones plus
    any lab-supplied ones via `alt_plot_import_path` - so the valid key set isn't known at
    class-definition time.

    Deliberately NOT frozen (unlike its sibling *Settings dataclasses): AltPlotAnalyzer holds this
    exact instance and mutates it in place as its live state, the same pattern
    PulseExtractor/PulseAnalyzer already use for ExtractionParameters/AnalysisParameters.
    """
    method: Optional[str] = None
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {'method': self.method, **self.parameters}

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict):
            return cls()
        data = dict(data)
        method = data.pop('method', None)
        return cls(method=method, parameters=data)

    def update_from_dict(self, data: dict):
        data = dict(data)
        if 'method' in data:
            self.method = data.pop('method')
        self.parameters.update(data)
        return self

@dataclass
class ExecutionState:
    """Manages the live running state, pausing, and timing of the measurement loop."""

    is_paused: bool = False
    start_time: float = 0.0
    time_of_pause: float = 0.0

    @property
    def elapsed_time(self) -> float:
        return self.get_live_elapsed_time()

    def start(self):
        """Called when a new measurement begins."""
        self.is_paused = False
        self.start_time = time.time()
        self.time_of_pause = 0.0

    def pause(self):
        """Called when the user hits pause."""
        if not self.is_paused:
            self.is_paused = True
            self.time_of_pause = time.time()

    def unpause(self):
        """Called when the user resumes."""
        if self.is_paused:
            self.is_paused = False
            # Push the start time forward so the pause duration isn't counted
            self.start_time += (time.time() - self.time_of_pause)
            
    def get_live_elapsed_time(self) -> float:
        """Calculates the true running time, ignoring pauses."""
        if self.is_paused:
            return self.time_of_pause - self.start_time
        return time.time() - self.start_time


@dataclass
class MeasurementInformation:
    """Metadata about the currently loaded pulse block ensemble/sequence (number of laser
    pulses, controlled variable array, etc.), used to auto-populate PulsedMeasurementSettings
    when 'invoke_settings' is enabled.

    All fields default to None ("not yet available"). Use is_valid (or plain bool(...), which
    mirrors it) to check whether enough information is present to invoke measurement settings -
    this replaces the previous "check 5 required keys are present in a plain dict" convention.
    """
    number_of_lasers: Optional[int] = None
    controlled_variable: Optional[np.ndarray] = None
    laser_ignore_list: Optional[list] = None
    alternating: Optional[bool] = None
    counting_length: Optional[float] = None
    units: Optional[tuple] = None
    labels: Optional[tuple] = None

    _MANDATORY_FIELDS: ClassVar[tuple] = (
        'number_of_lasers', 'controlled_variable', 'laser_ignore_list', 'alternating', 'counting_length'
    )

    @property
    def is_valid(self) -> bool:
        """True only if every field required to invoke measurement settings is present."""
        return all(getattr(self, name) is not None for name in self._MANDATORY_FIELDS)

    def __bool__(self):
        return self.is_valid

    def __eq__(self, other):
        """Custom equality: the auto-generated dataclass __eq__ would compare
        controlled_variable with plain '==', which raises "truth value of an array is
        ambiguous" once it holds a populated numpy array (the normal case)."""
        if not isinstance(other, MeasurementInformation):
            return NotImplemented
        if not np.array_equal(self.controlled_variable, other.controlled_variable):
            return False
        return (
            self.number_of_lasers,
            self.laser_ignore_list,
            self.alternating,
            self.counting_length,
            self.units,
            self.labels,
        ) == (
            other.number_of_lasers,
            other.laser_ignore_list,
            other.alternating,
            other.counting_length,
            other.units,
            other.labels,
        )

    def _field_names(self):
        return {f.name for f in fields(self)}

    def __contains__(self, key):
        return key in self._field_names()

    def __getitem__(self, key):
        if key in self._field_names():
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key, value):
        """Allows dict-item-assignment (e.g. block_ensemble.measurement_information['alternating']
        = False), used throughout predefined_generate_methods/*.py, on this typed dataclass."""
        if key not in self._field_names():
            raise KeyError(
                'MeasurementInformation has no field "{0}". Valid fields: {1}'.format(
                    key, sorted(self._field_names())
                )
            )
        setattr(self, key, value)

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
        return replace(
            self,
            laser_ignore_list=None if self.laser_ignore_list is None else list(self.laser_ignore_list),
            controlled_variable=None if self.controlled_variable is None else self.controlled_variable.copy(),
        )

    def to_dict(self) -> dict:
        if not self.is_valid:
            return {}
        data = {
            'number_of_lasers': self.number_of_lasers,
            'controlled_variable': self.controlled_variable,
            'laser_ignore_list': self.laser_ignore_list,
            'alternating': self.alternating,
            'counting_length': self.counting_length,
        }
        if self.units is not None:
            data['units'] = self.units
        if self.labels is not None:
            data['labels'] = self.labels
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or not all(data.get(key) is not None for key in cls._MANDATORY_FIELDS):
            return cls()
        return cls(
            number_of_lasers=int(data['number_of_lasers']),
            controlled_variable=data['controlled_variable'],
            laser_ignore_list=list(data['laser_ignore_list']),
            alternating=bool(data['alternating']),
            counting_length=float(data['counting_length']),
            units=tuple(data['units']) if data.get('units') is not None else None,
            labels=tuple(data['labels']) if data.get('labels') is not None else None,
        )


class AnalysisParameters(dict):
    """Persisted PulseAnalyzer settings: analysis method kwargs plus the selected method under
    'method'. Stays a dict subclass (not a plain dataclass) because pulse_analyzer.py reads/
    mutates it directly as an ordinary dict."""

    @property
    def method(self):
        """Name of the currently selected analysis method, or None if not set."""
        return self.get('method')

    @property
    def parameters(self) -> dict:
        """All method keyword-argument parameters, excluding the 'method' key itself."""
        return {key: value for key, value in self.items() if key != 'method'}

    def to_dict(self) -> dict:
        return dict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data) if isinstance(data, dict) else cls()


class ExtractionParameters(dict):
    """Same purpose as AnalysisParameters, but for the persisted PulseExtractor settings - see
    pulse_extractor.py."""

    @property
    def method(self):
        return self.get('method')

    @property
    def parameters(self) -> dict:
        return {key: value for key, value in self.items() if key != 'method'}

    def to_dict(self) -> dict:
        return dict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data) if isinstance(data, dict) else cls()


class GenerationMethodParameters(dict):
    """dict-based structured container for the keyword arguments used to (re-)generate the
    currently loaded PulseBlockEnsemble/PulseSequence via a predefined generator method.

    Kept as a dict subclass for the same reason as AnalysisParameters/ExtractionParameters:
    PulseBlockEnsemble/PulseSequence (pulse_objects.py) already store this as a plain dict on
    their own 'generation_method_parameters' attribute, and the parameter names vary freely
    depending on which predefined generator method produced the ensemble/sequence.
    """

    def to_dict(self) -> dict:
        return dict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data) if isinstance(data, dict) else cls()


class MetadataDictRepresentation(dict):
    """A dict whose repr() is pretty-printed (multi-line, indented) instead of the default
    single-line dict repr.

    qudi's DataStorage header writer (qudi.util.datastorage) renders every saved-file metadata
    value via plain repr() and then prefixes each resulting physical line with the file's
    comment marker. A plain dict's repr() never inserts line breaks no matter how deeply nested
    it is, so a big settings dict ends up as one unreadable wall-of-text line. Wrapping such a
    dict in this class instead makes repr() return pprint-formatted text - which does contain
    real line breaks - so the existing line-by-line comment-prefixing already handles it, with
    no changes needed to DataStorage itself.
    """

    def __repr__(self):
        return pprint.pformat(dict(self), indent=2, sort_dicts=False, width=100)


# Default sub-settings for PulsedMeasurementSettings.from_dict()'s per-field fallbacks (a saved
# settings file predating a given field) and for pulsed_measurement_logic.py's
# _default_measurement_settings() (a brand new settings object). Defined once, here, since
# pulsed_measurement_logic_data.py cannot import from pulsed_measurement_logic.py (that direction
# would be circular) - this is the lower-level file, so it owns the shared literals instead.
_DEFAULT_MICROWAVE_SETTINGS = MicrowaveSettings(power=-30.0, frequency=2870e6, use_ext_microwave=False)
_DEFAULT_FAST_COUNTER_SETTINGS = FastCounterSettings(
    record_length=3.0e-6, bin_width=1.0e-9, number_of_gates=0, is_gated=False
)
_DEFAULT_READOUT_SETTINGS = ReadoutSettings(
    invoke_settings=False,
    controlled_variable=np.array(list(range(50)), dtype=float),
    number_of_lasers=50,
    laser_ignore_list=list(),
    alternating=False,
    units=('s', ''),
    labels=('Tau', 'Signal'),
)


@dataclass(frozen=True)
class PulsedMeasurementSettings:
    """Settings PulsedMeasurementLogic persists: microwave, fast counter, readout, alternative
    signal processing, extraction/analysis parameters.

    extraction_parameters/analysis_parameters are SHARED, live objects - PulseExtractor/
    PulseAnalyzer mutate them in place, so update_from_dict() mutates them in place too rather
    than replacing the reference.

    timer_interval_s/fit_configs/pulser_benchmarks are deliberately NOT here - they're
    operational bookkeeping, persisted as independent StatusVars on their owning logic module.

    sampling_information/measurement_information/generation_method_parameters live on the loaded
    asset (SequenceGeneratorLogic) and are exposed as properties here, not copied - avoids a
    save/load race that existed when this data was copied on every load.
    """

    microwave_settings: MicrowaveSettings
    fast_counter_settings: FastCounterSettings
    readout_settings: ReadoutSettings
    alternate_signal_settings: AlternativeSignalSettings
    extraction_parameters: ExtractionParameters
    analysis_parameters: AnalysisParameters

    def to_dict(self):
        return {
            'microwave_settings': self.microwave_settings.to_dict(),
            'fast_counter_settings': self.fast_counter_settings.to_dict(),
            'readout_settings': self.readout_settings.to_dict(),
            'alternate_signal_settings': self.alternate_signal_settings.to_dict(),
            'extraction_parameters': self.extraction_parameters.to_dict(),
            'analysis_parameters': self.analysis_parameters.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        """Tolerant of a saved dict missing keys added after it was written (e.g. an older save
        file that predates a field being added to this class) - falls back to that field's
        own default rather than raising KeyError. extraction_parameters/analysis_parameters
        don't need an explicit fallback here: ExtractionParameters.from_dict()/
        AnalysisParameters.from_dict() already return an empty container for non-dict input
        (including None from a missing key), so data.get(...) with no second argument is
        sufficient.
        """
        return cls(
            microwave_settings=(
                MicrowaveSettings.from_dict(data['microwave_settings'])
                if 'microwave_settings' in data
                else _DEFAULT_MICROWAVE_SETTINGS
            ),
            fast_counter_settings=(
                FastCounterSettings.from_dict(data['fast_counter_settings'])
                if 'fast_counter_settings' in data
                else _DEFAULT_FAST_COUNTER_SETTINGS
            ),
            readout_settings=(
                ReadoutSettings.from_dict(data['readout_settings'])
                if 'readout_settings' in data
                else _DEFAULT_READOUT_SETTINGS
            ),
            alternate_signal_settings=AlternativeSignalSettings.from_dict(data.get('alternate_signal_settings')),
            extraction_parameters=ExtractionParameters.from_dict(data.get('extraction_parameters')),
            analysis_parameters=AnalysisParameters.from_dict(data.get('analysis_parameters')),
        )

    #: Top-level status-file key names used by the ~20 individually-declared StatusVars that
    #: existed on PulsedMeasurementLogic before this class was introduced (one file-level key
    #: per StatusVar, instead of everything nested under a single '_settings' key). Used only to
    #: detect a pre-refactor status file - see PulsedMeasurementLogic._migrate_legacy_settings_if_needed().
    LEGACY_STATUS_VAR_KEYS = frozenset({
        '_PulsedMeasurementLogic__microwave_power',
        '_PulsedMeasurementLogic__microwave_freq',
        '_PulsedMeasurementLogic__use_ext_microwave',
        '_PulsedMeasurementLogic__fast_counter_record_length',
        '_PulsedMeasurementLogic__fast_counter_binwidth',
        '_PulsedMeasurementLogic__fast_counter_gates',
        '_PulsedMeasurementLogic__timer_interval',
        '_invoke_settings_from_sequence',
        '_number_of_lasers',
        '_controlled_variable',
        '_alternating',
        '_laser_ignore_list',
        '_data_units',
        '_data_labels',
        'extraction_parameters',
        'analysis_parameters',
        'fit_configs',
        '_alternative_data_type',
        'zeropad',
        'psd',
        'window',
        'base_corr',
    })

    @classmethod
    def from_legacy_dict(cls, raw):
        """Reconstruct settings from the pre-refactor flat, individually-StatusVar'd format
        (key names below are the exact, name-mangled-where-applicable old StatusVar names).

        is_gated stays at its default: the old format never persisted it (always a live hardware
        query), and on_activate() re-syncs it from hardware right after this runs anyway.

        '_PulsedMeasurementLogic__timer_interval'/'fit_configs' are still in
        LEGACY_STATUS_VAR_KEYS (valid legacy-format signals) but deliberately not reconstructed
        here - PulsedMeasurementLogic._migrate_legacy_settings_if_needed() migrates those two
        directly into its own independent StatusVars instead.
        """
        return cls.from_dict({
            'microwave_settings': {
                'power': raw.get('_PulsedMeasurementLogic__microwave_power', -30.0),
                'frequency': raw.get('_PulsedMeasurementLogic__microwave_freq', 2870e6),
                'use_ext_microwave': raw.get('_PulsedMeasurementLogic__use_ext_microwave', False),
            },
            'fast_counter_settings': {
                'bin_width': raw.get('_PulsedMeasurementLogic__fast_counter_binwidth', 1.0e-9),
                'record_length': raw.get('_PulsedMeasurementLogic__fast_counter_record_length', 3.0e-6),
                'number_of_gates': raw.get('_PulsedMeasurementLogic__fast_counter_gates', 0),
                'is_gated': False,
            },
            'readout_settings': {
                'invoke_settings': raw.get('_invoke_settings_from_sequence', False),
                'controlled_variable': raw.get('_controlled_variable', list(range(50))),
                'number_of_lasers': raw.get('_number_of_lasers', 50),
                'laser_ignore_list': raw.get('_laser_ignore_list', []),
                'alternating': raw.get('_alternating', False),
                'units': raw.get('_data_units', ('s', '')),
                'labels': raw.get('_data_labels', ('Tau', 'Signal')),
            },
            'alternate_signal_settings': {
                'method': raw.get('_alternative_data_type'),
                'zeropad': raw.get('zeropad', 0),
                'psd': raw.get('psd', False),
                'window': raw.get('window', 'none'),
                'base_corr': raw.get('base_corr', True),
            },
            'extraction_parameters': raw.get('extraction_parameters'),
            'analysis_parameters': raw.get('analysis_parameters'),
        })

    def update_from_dict(self, data):
        microwave_settings = self.microwave_settings
        if 'microwave_settings' in data:
            value = data['microwave_settings']
            microwave_settings = value if isinstance(value, MicrowaveSettings) else self.microwave_settings.update_from_dict(value)

        fast_counter_settings = self.fast_counter_settings
        if 'fast_counter_settings' in data:
            value = data['fast_counter_settings']
            fast_counter_settings = (
                value if isinstance(value, FastCounterSettings) else self.fast_counter_settings.update_from_dict(value)
            )

        readout_settings = self.readout_settings
        if 'readout_settings' in data:
            value = data['readout_settings']
            readout_settings = value if isinstance(value, ReadoutSettings) else self.readout_settings.update_from_dict(value)

        # alternate_signal_settings is a shared object too (AltPlotAnalyzer holds this exact
        # instance, see its class docstring): mutate it in place, never replace the reference.
        alternate_signal_settings = self.alternate_signal_settings
        if 'alternate_signal_settings' in data:
            value = data['alternate_signal_settings']
            alternate_signal_settings.update_from_dict(
                value.to_dict() if isinstance(value, AlternativeSignalSettings) else value
            )

        # extraction_parameters/analysis_parameters are shared objects (see class docstring):
        # mutate the existing dict in place, never replace the reference.
        if 'extraction_parameters' in data:
            value = data['extraction_parameters']
            self.extraction_parameters.update(value if isinstance(value, dict) else ExtractionParameters.from_dict(value))

        if 'analysis_parameters' in data:
            value = data['analysis_parameters']
            self.analysis_parameters.update(value if isinstance(value, dict) else AnalysisParameters.from_dict(value))

        return replace(
            self,
            microwave_settings=microwave_settings,
            fast_counter_settings=fast_counter_settings,
            readout_settings=readout_settings,
            alternate_signal_settings=alternate_signal_settings,
        )

