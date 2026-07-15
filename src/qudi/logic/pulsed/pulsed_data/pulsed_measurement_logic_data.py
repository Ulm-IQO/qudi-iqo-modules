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
from dataclasses import dataclass, replace, field, fields
from typing import ClassVar, Optional
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
        self.controlled_variable.flags.writeable = False

    
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
    active_tag: str | None = None

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

@dataclass(frozen=True)
class AlternativeSignalSettings:
    """Settings for alternative signal processing methods."""
    alternative_data_type: str|None
    zeropad:int=0
    psd: bool=False
    window: str='none'
    base_corr: bool=True
    def to_dict(self) -> dict:
        return {
            'alternative_data_type': self.alternative_data_type,
            'zeropad': self.zeropad,
            'psd': self.psd,
            'window': self.window,
            'base_corr': self.base_corr,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            alternative_data_type=data.get('alternative_data_type', None),
            zeropad=int(data.get('zeropad', 0)),
            psd=_as_bool(data.get('psd', False)),
            window=str(data.get('window', 'none')),
            base_corr=_as_bool(data.get('base_corr', True)),
        )

    def update_from_dict(self, data: dict):
        return replace(
            self,
            alternative_data_type=data.get('alternative_data_type', self.alternative_data_type),
            zeropad=int(data.get('zeropad', self.zeropad)),
            psd=_as_bool(data.get('psd', self.psd)),
            window=str(data.get('window', self.window)),
            base_corr=_as_bool(data.get('base_corr', self.base_corr)),
        )

@dataclass
class ExecutionState:
    """Manages the live running state, pausing, and timing of the measurement loop."""

    is_paused: bool = False
    start_time: float = 0.0
    time_of_pause: float = 0.0
    elapsed_pause: float = 0.0

    @property
    def elapsed_time(self) -> float:
        return self.get_live_elapsed_time()

    def start(self):
        """Called when a new measurement begins."""
        self.is_paused = False
        self.start_time = time.time()
        self.time_of_pause = 0.0
        self.elapsed_pause = 0.0

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
    """dict-based structured container for the persisted PulseAnalyzer settings: the keyword
    argument values collected for all known analysis methods, plus the name of the currently
    selected method under the 'method' key.

    This stays a dict subclass (the same pattern as SequenceStep in pulse_objects.py) rather
    than a plain dataclass because PulseAnalyzer reads and mutates it directly as an ordinary
    dict (isinstance(..., dict) checks, "del container[param]", "for p in container" - see
    pulse_analyzer.py). Subclassing dict keeps that code working unchanged while giving the
    container a name, a docstring, and named read access via .method/.parameters.
    """

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


@dataclass(frozen=True)
class PulsedMeasurementSettings:
    """Single settings container bundling everything PulsedMeasurementLogic persists as
    user-configurable settings: external microwave, fast counter, readout (controlled
    variable/units/labels/...), alternative signal processing, extraction/analysis parameters,
    and the analysis-loop timer interval.

    Note: extraction_parameters and analysis_parameters are SHARED objects, not independently
    owned copies. PulseExtractor/PulseAnalyzer hold a reference to the exact same
    ExtractionParameters/AnalysisParameters instance stored here and mutate it in place
    (dict.__setitem__) as their real, live, authoritative state - see pulse_extractor.py/
    pulse_analyzer.py. Because dataclasses.replace() leaves the object identity of any field it
    doesn't touch untouched, swapping out some other field of this container (e.g. via
    _apply_fast_counter_settings) never disturbs that shared reference. update_from_dict()
    mirrors this: it mutates these two fields' dicts in place (dict.update()) rather than
    replacing them with a freshly-constructed instance, precisely so PulseExtractor/
    PulseAnalyzer's reference stays valid.

    Note: sampling_information/measurement_information/generation_method_parameters are
    intentionally NOT part of this container - they live on whichever PulseBlockEnsemble/
    PulseSequence is currently loaded (owned/persisted by SequenceGeneratorLogic), and
    PulsedMeasurementLogic exposes them as properties over a live `loaded_asset` reference
    rather than an independently persisted copy - this is what actually eliminates the
    save/load race that used to exist when that data was copied on every load instead.
    """

    microwave_settings: MicrowaveSettings
    fast_counter_settings: FastCounterSettings
    readout_settings: ReadoutSettings
    alternate_signal_settings: AlternativeSignalSettings
    extraction_parameters: ExtractionParameters
    analysis_parameters: AnalysisParameters
    timer_interval_s: float

    def to_dict(self):
        return {
            'microwave_settings': self.microwave_settings.to_dict(),
            'fast_counter_settings': self.fast_counter_settings.to_dict(),
            'readout_settings': self.readout_settings.to_dict(),
            'alternate_signal_settings': self.alternate_signal_settings.to_dict(),
            'extraction_parameters': self.extraction_parameters.to_dict(),
            'analysis_parameters': self.analysis_parameters.to_dict(),
            'timer_interval_s': float(self.timer_interval_s),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            microwave_settings=MicrowaveSettings.from_dict(data['microwave_settings']),
            fast_counter_settings=FastCounterSettings.from_dict(data['fast_counter_settings']),
            readout_settings=ReadoutSettings.from_dict(data['readout_settings']),
            alternate_signal_settings=AlternativeSignalSettings.from_dict(data['alternate_signal_settings']),
            extraction_parameters=ExtractionParameters.from_dict(data['extraction_parameters']),
            analysis_parameters=AnalysisParameters.from_dict(data['analysis_parameters']),
            timer_interval_s=float(data['timer_interval_s']),
        )

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

        alternate_signal_settings = self.alternate_signal_settings
        if 'alternate_signal_settings' in data:
            value = data['alternate_signal_settings']
            alternate_signal_settings = (
                value
                if isinstance(value, AlternativeSignalSettings)
                else self.alternate_signal_settings.update_from_dict(value)
            )

        # extraction_parameters/analysis_parameters are shared objects (see class docstring):
        # mutate the existing dict in place, never replace the reference.
        if 'extraction_parameters' in data:
            value = data['extraction_parameters']
            self.extraction_parameters.update(value if isinstance(value, dict) else ExtractionParameters.from_dict(value))

        if 'analysis_parameters' in data:
            value = data['analysis_parameters']
            self.analysis_parameters.update(value if isinstance(value, dict) else AnalysisParameters.from_dict(value))

        timer_interval_s = float(data.get('timer_interval_s', self.timer_interval_s))

        return replace(
            self,
            microwave_settings=microwave_settings,
            fast_counter_settings=fast_counter_settings,
            readout_settings=readout_settings,
            alternate_signal_settings=alternate_signal_settings,
            timer_interval_s=timer_interval_s,
        )

