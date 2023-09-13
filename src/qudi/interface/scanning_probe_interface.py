# -*- coding: utf-8 -*-

"""
This module contains the Qudi interface file for scanning probe hardware.

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

from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, Dict
import datetime
import numpy as np
from abc import abstractmethod
from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint


@dataclass(frozen=True)
class ScannerChannel:
    """
    Data class representing a scanner channel and its constraints.
    A scanner channel is the probe device of a scanning probe measurement,
    e.g. a counter connected to an APD or other single-photon counting module.
    """
    name: str
    unit: str = ''
    # saving this as str instead of e.g. np.float64 object eases __dict__ representation
    dtype: str = 'float64'

    def __post_init__(self):
        if len(self.name) < 1:
            raise ValueError('Parameter "name" must be non-empty str.')
        # check if dtype can be understood as a compatible type by numpy
        try:
            np.dtype(self.dtype)
        except TypeError:
            raise TypeError('Parameter "dtype" must be numpy-compatible type.')


@dataclass(frozen=True)
class ScannerAxis:
    """
    Data class representing a scan axis and its constraints.
    Then scan axes are swept during a scanning probe measurement.
    """
    name: str
    unit: str
    position: ScalarConstraint
    step: ScalarConstraint
    resolution: ScalarConstraint
    frequency: ScalarConstraint

    def __post_init__(self):
        if self.name == '':
            raise ValueError('Parameter "name" must be non-empty str.')

    # properties for legacy support

    @property
    def value_range(self):
        return self.position.bounds

    @property
    def step_range(self):
        return self.step.bounds

    @property
    def resolution_range(self):
        return self.resolution.bounds

    @property
    def frequency_range(self):
        return self.frequency.bounds

    @property
    def min_resolution(self):
        return self.resolution.minimum

    @property
    def max_resolution(self):
        return self.resolution.maximum

    @property
    def min_step(self):
        return self.step.minimum

    @property
    def max_step(self):
        return self.step.maximum

    @property
    def min_value(self):
        return self.position.minimum

    @property
    def max_value(self):
        return self.position.maximum

    @property
    def min_frequency(self):
        return self.frequency.minimum

    @property
    def max_frequency(self):
        return self.frequency.maximum

    def clip_value(self, value):
        return self.position.clip(value)

    def clip_resolution(self, res):
        return self.resolution.clip(res)

    def clip_frequency(self, freq):
        return self.frequency.clip(freq)


@dataclass(frozen=True)
class ScanSettings:
    """
    Data class representing all settings specifying a scanning probe measurement.

    @param str[] channels: names of scanner channels involved in this scan
    @param str[] axes: names of scanner axes involved in this scan
    @param float[][2] range: inclusive range for each scan axis
    @param int[] resolution: planned number of points for each scan axis
    @param float frequency: Scan pixel frequency of the fast axis
    @param str[] position_feedback_axes: optional, names of axes for which to acquire position
                                         feedback during the scan.
    """

    channels: Tuple[str, ...]
    axes: Tuple[str, ...]
    range: Tuple[Tuple[float, float], ...]
    resolution: Tuple[int, ...]
    frequency: float
    position_feedback_axes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Sanity checking
        if not (0 < len(self.axes) <= 2):
            raise ValueError('Only 1D and 2D scans are implemented.')
        if len(self.channels) < 1:
            raise ValueError('At least one data channel must be specified for a valid scan.')
        if len(self.axes) != len(self.range):
            raise ValueError(f'Parameters "axes" and "range" must have same len. Given '
                             f'{len(self.axes)} and {len(self.range)}, respectively.')
        if len(self.axes) != len(self.resolution):
            raise ValueError(f'Parameters "axes" and "resolution" must have same len. '
                             f'Given {len(self.axes)} and {len(self.resolution)}, respectively.')

        if not set(self.position_feedback_axes).issubset(self.axes):
            raise TypeError(
                'The "position_feedback_axes" must be a subset of scan axes.'
            )

    @property
    def has_position_feedback(self) -> bool:
        return bool(self.position_feedback_axes)

    @property
    def scan_dimension(self) -> int:
        return len(self.axes)


@dataclass(frozen=True)
class ScanConstraints:
    """
    Data class representing the complete constraints of a scanning probe measurement.
    """
    channel_objects: Tuple[ScannerChannel, ...]
    axis_objects: Tuple[ScannerAxis, ...]
    backscan_configurable: bool  # TODO Incorporate in gui/logic toolchain?
    has_position_feedback: bool  # TODO Incorporate in gui/logic toolchain?
    square_px_only: bool  # TODO Incorporate in gui/logic toolchain?

    @property
    def channels(self) -> Dict[str, ScannerChannel]:
        return {ch.name: ch for ch in self.channel_objects}

    @property
    def axes(self) -> Dict[str, ScannerAxis]:
        return {ax.name: ax for ax in self.axis_objects}

    def is_valid(self, settings: ScanSettings) -> bool:
        try:
            self.check_settings(settings)
        except (ValueError, TypeError):
            return False
        return True

    def check_settings(self, settings: ScanSettings) -> None:
        self.check_channels(settings)
        self.check_axes(settings)
        self.check_feedback(settings)

    def check_channels(self, settings: ScanSettings) -> None:
        if not set(settings.channels).issubset(self.channels):
            raise ValueError(f'Unknown channel names encountered in {settings.channels}. '
                             f'Valid channel names are {list(self.channels.keys())}.')

    def check_axes(self, settings: ScanSettings) -> None:
        if not set(settings.axes).issubset(self.axes):
            raise ValueError(f'Unknown axis names encountered in {settings.axes}. '
                             f'Valid axis names are {list(self.axes.keys())}.')

        for axis_name, _range, resolution in zip(settings.axes, settings.range, settings.resolution):
            axis = self.axes[axis_name]
            try:
                axis.position.check(_range[0])
                axis.position.check(_range[1])
            except ValueError as e:
                raise ValueError(f'Scan range out of bounds for axis "{axis_name}".') from e
            except TypeError as e:
                raise TypeError(f'Scan range type check failed for axis "{axis_name}".') from e

            try:
                axis.resolution.check(resolution)
            except ValueError as e:
                raise ValueError(f'Scan resolution out of bounds for axis "{axis_name}".') from e
            except TypeError as e:
                raise TypeError(f'Scan resolution type check failed for axis "{axis_name}".') from e

        # frequency is only relevant for the first (fast) axis
        fast_axis_name = settings.axes[0]
        fast_axis = self.axes[fast_axis_name]
        try:
            fast_axis.frequency.check(settings.frequency)
        except ValueError as e:
            raise ValueError(f'Scan frequency out of bounds for fast axis "{fast_axis_name}".') from e
        except TypeError as e:
            raise TypeError(f'Scan frequency type check failed for fast axis "{fast_axis_name}".') from e

    def check_feedback(self, settings: ScanSettings) -> None:
        if settings.has_position_feedback and not self.has_position_feedback:
            raise ValueError(f'Scanner does not support position feedback.')

    def clip(self, settings: ScanSettings) -> ScanSettings:
        self.check_axes(settings)
        clipped_range = []
        clipped_resolution = []
        for axis, _range, resolution in zip(settings.axes, settings.range, settings.resolution):
            clipped_range.append((float(self.axes[axis].position.clip(_range[0])),
                                  float(self.axes[axis].position.clip(_range[1]))))
            clipped_resolution.append(int(self.axes[axis].resolution.clip(resolution)))
        # frequency needs to be within bounds for all axes
        clipped_frequency = settings.frequency
        for axis in settings.axes:
            clipped_frequency = self.axes[axis].frequency.clip(clipped_frequency)

        clipped_settings = ScanSettings(
            channels=settings.channels,
            axes=settings.axes,
            range=tuple(clipped_range),
            resolution=tuple(clipped_resolution),
            frequency=clipped_frequency,
            position_feedback_axes=settings.position_feedback_axes
        )
        return clipped_settings


@dataclass
class ScanData:
    """
    Data class representing settings and results of a scanning probe measurement.
    """

    settings: ScanSettings
    _channel_units: Tuple[str, ...]
    _channel_dtypes: Tuple[str, ...]
    _axis_units: Tuple[str, ...]
    _scanner_target_at_start: Optional[Tuple[float, ...]] = None
    timestamp: Optional[datetime.datetime] = None
    _data: Optional[Tuple[np.ndarray, ...]] = None
    # TODO: Automatic interpolation onto rectangular grid needs to be implemented (for position feedback HW)
    position_data: Optional[Dict[str, np.ndarray]] = None

    @classmethod
    def from_constraints(cls, settings: ScanSettings, constraints: ScanConstraints, **kwargs):
        constraints.check_settings(settings)
        _channel_units = tuple(constraints.channels[ch].unit for ch in settings.channels)
        _channel_dtypes = tuple(constraints.channels[ch].dtype for ch in settings.channels)
        _axis_units = tuple(constraints.axes[ax].unit for ax in settings.axes)
        return cls(
            settings=settings,
            _channel_units=_channel_units,
            _channel_dtypes=_channel_dtypes,
            _axis_units=_axis_units,
            **kwargs
        )

    @classmethod
    def from_dict(cls, dict_repr):
        """ Create a class instance from a dictionary.
        ScanData contains ScanSettings, which is itself a dataclass
        and needs to be reconstructed separately. """
        settings = dict_repr['settings']
        dict_repr_without_settings = dict_repr.copy()
        del dict_repr_without_settings['settings']
        return cls(settings=ScanSettings(**settings), **dict_repr_without_settings)

    def to_dict(self):
        return asdict(self)

    @property
    def channel_units(self) -> Dict[str, str]:
        return {ch: unit for ch, unit in zip(self.settings.channels, self._channel_units)}

    @property
    def channel_dtypes(self) -> Dict[str, str]:
        return {ch: dtype for ch, dtype in zip(self.settings.channels, self._channel_dtypes)}

    @property
    def axis_units(self) -> Dict[str, str]:
        return {ax: unit for ax, unit in zip(self.settings.axes, self._axis_units)}

    @property
    def scanner_target_at_start(self) -> Dict[str, float]:
        return {ax: pos for ax, pos in zip(self.settings.axes, self._scanner_target_at_start)}

    @property
    def data(self) -> Dict[str, np.ndarray]:
        """ Dict of channel data arrays with channel names as keys. """
        return {ch: data for ch, data in zip(self.settings.channels, self._data)}

    @data.setter
    def data(self, data_dict: Dict[str, np.ndarray]) -> None:
        channels = tuple(data_dict.keys())
        if channels != self.settings.channels:
            raise ValueError(f'Unknown channel names encountered in {channels}. '
                             f'Valid channel names are {self.settings.channels}.')
        if not all([val.shape == self.settings.resolution for val in data_dict.values()]):
            raise ValueError(f'Data shapes do not match resolution {self.settings.resolution}.')
        self._data = tuple(data for data in data_dict.values())

    def new_scan(self, timestamp=None):
        """
        Reset data and position data and update the timestamp.
        @param timestamp:
        """
        if timestamp is None:
            self.timestamp = datetime.datetime.now()
        elif isinstance(timestamp, datetime.datetime):
            self.timestamp = timestamp
        else:
            raise TypeError('Optional parameter "timestamp" must be datetime.datetime object.')

        if self.has_position_feedback:
            self.position_data = {ax: np.full(self.settings.resolution, np.nan) for ax in
                                  self.settings.position_feedback_axes}
        else:
            self.position_data = None
        self.data = {
            ch: np.full(self.settings.resolution, np.nan,
                        dtype=self.channel_dtypes[ch]) for ch in self.settings.channels
        }
        return

    # properties for legacy support

    @property
    def scan_axes(self) -> Tuple[str, ...]:
        return tuple(self.settings.axes)

    @property
    def channels(self) -> Tuple[str, ...]:
        return tuple(self.settings.channels)

    @property
    def axes_units(self) -> Dict[str, str]:
        return self.axis_units

    @property
    def has_position_feedback(self) -> bool:
        return self.settings.has_position_feedback

    @property
    def scan_dimension(self) -> int:
        return self.settings.scan_dimension

    @property
    def position_feedback_axes(self) -> Tuple[str, ...]:
        return self.settings.position_feedback_axes

    @property
    def scan_range(self) -> Tuple[Tuple[float, float], ...]:
        return self.settings.range

    @property
    def scan_resolution(self) -> Tuple[int, ...]:
        return self.settings.resolution

    @property
    def scan_frequency(self) -> float:
        return self.settings.frequency


class ScanningProbeInterface(Base):
    """ This is the Interface class to define the controls for a scanning probe device

    A scanner device is hardware that can move multiple axes.
    """

    @property
    @abstractmethod
    def constraints(self) -> ScanConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """ Hard reset of the hardware.
        """
        pass

    @property
    @abstractmethod
    def scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters needed for a 1D or 2D scan. Returns None if not configured.
        """
        pass

    @scan_settings.setter
    @abstractmethod
    def scan_settings(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.

        @param ScanSettings settings: ScanSettings instance holding all parameters
        """
        pass

    @abstractmethod
    def move_absolute(self, position: Dict[str, float],
                      velocity: Optional[float] = None, blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a scan is in progress.

        @param dict position: absolute positions for all axes to move to, axis names as keys
        @param float velocity: movement velocity
        @param bool blocking: If True this call returns only after the final position is reached.

        @return dict: new position of all axes
        """
        pass

    @abstractmethod
    def move_relative(self, distance: Dict[str, float],
                      velocity: Optional[float] = None, blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error if something fails or a 1D/2D scan is in progress.

        @param dict distance: relative distance for all axes to move by, axis names as keys
        @param float velocity: movement velocity
        @param bool blocking: If True this call returns only after the final position is reached.

        @return dict: new position of all axes
        """
        pass

    @abstractmethod
    def get_target(self) -> Dict[str, float]:
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """
        pass

    @abstractmethod
    def get_position(self) -> Dict[str, float]:
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).
        For the same target this value can fluctuate according to the scanners positioning accuracy.

        For scanning devices that do not have position feedback sensors, simply return the target
        position (see also: ScanningProbeInterface.get_target).

        @return dict: current position per axis.
        """
        pass

    @abstractmethod
    def start_scan(self) -> None:
        """
        Start a scan as configured beforehand.
        Log an error if something fails or a 1D/2D scan is in progress.
        """
        pass

    @abstractmethod
    def stop_scan(self) -> None:
        """
        Stop the currently running scan.
        Log an error if something fails or no 1D/2D scan is in progress.
        """
        pass

    @property
    @abstractmethod
    def scan_data(self) -> ScanData:
        """ Read-only property returning the ScanData instance used in the scan.
        """
        pass

    @abstractmethod
    def emergency_stop(self) -> None:
        """
        TODO: document what this should to differently than stop_scan.
        """
        pass
