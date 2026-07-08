from dataclasses import dataclass, replace, field
import time
import numpy as np


def _as_bool(value):
    """Converts a value to a boolean. If the value is a string, 
    it checks for common truthy values.
    """
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)

#done
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


#Done
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
class PulsedMeasurementSettings:
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
    active_tag: str = None

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
            alternative_data_type=str(data.get('alternative_data_type', 'None')),
            zeropad=int(data.get('zeropad', 0)),
            psd=_as_bool(data.get('psd', False)),
            window=str(data.get('window', 'none')),
            base_corr=_as_bool(data.get('base_corr', True)),
        )

    def update_from_dict(self, data: dict):
        return replace(
            self,
            alternative_data_type=str(data.get('alternative_data_type', self.alternative_data_type)),
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
    timer_interval_s: float = 5.0  # e.g., default 5 seconds

    @property
    def elapsed_time(self) -> float:
        return self.get_live_elapsed_time()

    @property
    def elapsed_sweeps(self) -> int:
        return 0

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
    
@dataclass(frozen=True)
class FitDefinition:
    """Definition for default fit configurations"""
    name: str
    model: str
    estimator: str = 'default'
    custom_parameters: dict | None = None

@dataclass(frozen=True)
class AnalysisSettings:
    """Serializable analysis parameters for the pulse analyzer."""

    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.parameters)

    @classmethod
    def from_dict(cls, data: dict | None = None):
        return cls(parameters=dict(data or {}))

    def update_from_dict(self, data: dict | None = None):
        return replace(self, parameters={**self.parameters, **dict(data or {})})


@dataclass(frozen=True)
class ExtractionSettings:
    """Serializable extraction parameters for the pulse extractor."""

    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.parameters)

    @classmethod
    def from_dict(cls, data: dict | None = None):
        return cls(parameters=dict(data or {}))

    def update_from_dict(self, data: dict | None = None):
        return replace(self, parameters={**self.parameters, **dict(data or {})})