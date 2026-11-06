# -*- coding: utf-8 -*-
"""
Settings and data containers for PulsedMeasurementLogic.

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
from enum import Enum
from typing import ClassVar, Optional
import math
import time
import numpy as np


def _as_bool(value):
    """Convert a value to bool, treating only '1', 'true', 'yes' and 'on' as True for strings."""
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
    """Settings defining how a pulsed measurement is analyzed."""

    invoke_settings: bool
    controlled_variable: np.ndarray
    number_of_lasers: int
    laser_ignore_list: list[int]
    alternating: bool
    units: tuple[str, str]
    labels: tuple[str, str]

    def __post_init__(self):
        # Copy before making read-only, so the caller's array is not affected.
        object.__setattr__(self, 'controlled_variable', self.controlled_variable.copy())
        self.controlled_variable.flags.writeable = False
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
    """Data arrays and timing of the current pulsed measurement."""

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
    """Stashed raw data, keyed by tag."""
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
    """Selected alternative plot method and the parameters of all loaded methods.

    `parameters` is a dict because the available methods are only known at runtime. Not frozen,
    since AltPlotAnalyzer updates this instance in place.
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
    """Running state and timing of the measurement loop."""

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
            # Exclude the pause from the elapsed time
            self.start_time += (time.time() - self.time_of_pause)

    def get_live_elapsed_time(self) -> float:
        """Elapsed measurement time, excluding pauses."""
        if self.is_paused:
            return self.time_of_pause - self.start_time
        return time.time() - self.start_time


@dataclass
class MeasurementInformation:
    """Measurement information of the loaded ensemble/sequence, used to fill in the readout
    settings when 'invoke_settings' is enabled.

    Fields are None until known. bool(instance) is True once all mandatory fields are set.
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
        """True if all mandatory fields are set."""
        return all(getattr(self, name) is not None for name in self._MANDATORY_FIELDS)

    def __bool__(self):
        return self.is_valid

    def __eq__(self, other):
        """Compare controlled_variable with np.array_equal, since '==' fails on arrays."""
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
        """Allow item assignment, as used by the predefined generate methods."""
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
    """PulseAnalyzer settings: the selected method under 'method' plus its parameters.

    A dict subclass because PulseAnalyzer uses it as a plain dict.
    """

    @property
    def method(self):
        """Name of the currently selected analysis method, or None if not set."""
        return self.get('method')

    @property
    def parameters(self) -> dict:
        """All parameters except 'method'."""
        return {key: value for key, value in self.items() if key != 'method'}

    def to_dict(self) -> dict:
        return dict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data) if isinstance(data, dict) else cls()


class ExtractionParameters(dict):
    """PulseExtractor settings, see AnalysisParameters."""

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
    """Keyword arguments of the predefined method that generated the loaded ensemble/sequence.

    A dict subclass because the parameter names depend on the generator method.
    """

    def to_dict(self) -> dict:
        return dict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data) if isinstance(data, dict) else cls()


def to_plain_metadata(value):
    """Recursively convert `value` into plain Python types for saved-file metadata.

    Metadata is written with repr() and read back with eval(), so values must repr as plain
    literals. This converts numpy arrays and scalars, enums and objects with a to_dict() method.
    Non-finite floats are written as the strings 'nan', 'inf' and '-inf', since Python has no
    literal for them; read them back with float(). Arrays are written in full, avoiding numpy's
    "..." summary for long arrays.
    """
    if isinstance(value, np.ndarray):
        return to_plain_metadata(value.tolist())
    if isinstance(value, np.generic):
        # Recurse so that nan/inf are handled below
        return to_plain_metadata(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {to_plain_metadata(key): to_plain_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_metadata(item) for item in value]
    if isinstance(value, tuple):
        return tuple(to_plain_metadata(item) for item in value)
    if isinstance(value, (set, frozenset)):
        # Sorted list for a stable order; an empty set would repr as "set()"
        items = [to_plain_metadata(item) for item in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    if isinstance(value, Enum):
        return to_plain_metadata(value.value)
    # Checked after dict, since some dict subclasses here also define to_dict()
    to_dict = getattr(value, 'to_dict', None)
    if callable(to_dict):
        return to_plain_metadata(to_dict())
    return value


# Defaults used by PulsedMeasurementSettings.from_dict() for missing keys and by
# PulsedMeasurementLogic for new settings.
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
    """Settings saved by PulsedMeasurementLogic.

    alternate_signal_settings, extraction_parameters and analysis_parameters are shared with
    AltPlotAnalyzer, PulseExtractor and PulseAnalyzer, so update_from_dict() updates them in place.
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
        """Missing keys fall back to their defaults."""
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

    #: StatusVar names of the old status file format, used to detect it.
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
        """Build settings from the old status file format.

        is_gated is not saved in the old format and is read from hardware on activation.
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

        # Shared objects are updated in place, see class docstring.
        alternate_signal_settings = self.alternate_signal_settings
        if 'alternate_signal_settings' in data:
            value = data['alternate_signal_settings']
            alternate_signal_settings.update_from_dict(
                value.to_dict() if isinstance(value, AlternativeSignalSettings) else value
            )

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

