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

from enum import auto, Flag
from dataclasses import dataclass, field, asdict, replace
from typing import Tuple, Dict, Any
import datetime
from typing import Optional

import numpy as np
from abc import abstractmethod
from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint
from qudi.util.units import ScaledFloat


class BackScanCapability(Flag):
    """Availability and configurability of the back scan."""
    AVAILABLE = auto()
    FREQUENCY_CONFIGURABLE = auto()
    RESOLUTION_CONFIGURABLE = auto()
    FULLY_CONFIGURABLE = FREQUENCY_CONFIGURABLE | RESOLUTION_CONFIGURABLE


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

    @classmethod
    def from_dict(cls, dict_repr):
        """Create instance from dict taking care to convert arguments to tuples."""
        return cls(
            channels=tuple(dict_repr['channels']),
            axes=tuple(dict_repr['axes']),
            range=tuple((i[0], i[1]) for i in dict_repr['range']),
            resolution=tuple(dict_repr['resolution']),
            frequency=dict_repr['frequency'],
            position_feedback_axes=tuple(dict_repr['position_feedback_axes'])
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
    back_scan_capability: BackScanCapability
    has_position_feedback: bool  # TODO Incorporate in gui/logic toolchain?
    square_px_only: bool  # TODO Incorporate in gui/logic toolchain?

    @property
    def channels(self) -> Dict[str, ScannerChannel]:
        return {ch.name: ch for ch in self.channel_objects}

    @property
    def axes(self) -> Dict[str, ScannerAxis]:
        return {ax.name: ax for ax in self.axis_objects}

    def check_settings(self, settings: ScanSettings) -> None:
        self.check_channels(settings)
        self.check_axes(settings)
        self.check_feedback(settings)

    def check_back_scan_settings(self, backward_settings: ScanSettings, forward_settings: ScanSettings) -> None:
        if BackScanCapability.AVAILABLE not in self.back_scan_capability:
            raise ValueError('A back scan is not available and can therefore not be configured.')
        elif not BackScanCapability.FULLY_CONFIGURABLE & self.back_scan_capability:
            raise ValueError('Hardware does not allow any configuration of the back scan.')

        # check if settings would fulfill constraints independently
        self.check_settings(backward_settings)

        # check if back scan settings match with forward scan settings
        if backward_settings.axes != forward_settings.axes:
            raise ValueError('The back scan must use the same axes as the forward scan.')
        if backward_settings.range != forward_settings.range:
            raise ValueError('The back scan must use the same range(s) as the forward scan.')
        if backward_settings.scan_dimension == 2:
            if backward_settings.resolution[1] != forward_settings.resolution[1]:
                raise ValueError('The back scan must use the same slow axis resolution as the forward scan.')
        if BackScanCapability.FREQUENCY_CONFIGURABLE not in self.back_scan_capability:
            if backward_settings.frequency != forward_settings.frequency:
                raise ValueError('The hardware requires the frequency of the back scan to be the same as for the '
                                 'forward scan.')
        if BackScanCapability.RESOLUTION_CONFIGURABLE not in self.back_scan_capability:
            if backward_settings.resolution != forward_settings.resolution:
                raise ValueError('The hardware requires the resolution of the back scan to be the same as for the '
                                 'forward scan.')

    def check_channels(self, settings: ScanSettings) -> None:
        if not set(settings.channels).issubset(self.channels):
            raise ValueError(f'Unknown channel names encountered in {settings.channels}. '
                             f'Valid channel names are {list(self.channels.keys())}.')

    def check_axes_names(self, settings: ScanSettings) -> None:
        if not set(settings.axes).issubset(self.axes):
            raise ValueError(f'Unknown axis names encountered in {settings.axes}. '
                             f'Valid axis names are {list(self.axes.keys())}.')

    def check_axes(self, settings: ScanSettings) -> None:
        self.check_axes_names(settings)
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
        self.check_axes_names(settings)
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
    """Data class representing settings and results of a scanning probe measurement.

    scanner_target_at_start may contain positions of axes other than the axes used in this scan.
    """

    settings: ScanSettings
    _channel_units: Tuple[str, ...]
    _channel_dtypes: Tuple[str, ...]
    _axis_units: Tuple[str, ...]
    scanner_target_at_start: Dict[str, float] = field(default_factory=dict)
    timestamp: Optional[datetime.datetime] = None
    _data: Optional[Tuple[np.ndarray, ...]] = None
    # TODO: Automatic interpolation onto rectangular grid needs to be implemented (for position feedback HW)
    _position_data: Optional[Tuple[np.ndarray, ...]] = None
    coord_transform_info: Dict[str, Any] = field(default_factory=dict)

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
        return cls(settings=ScanSettings.from_dict(settings), **dict_repr_without_settings)

    def to_dict(self):
        return asdict(self)

    def copy(self):
        """Create a copy of this object.
        Take care to copy all (mutable) arrays and dicts."""
        if self._data:
            _data_copy = tuple(a.copy() for a in self._data)
        else:
            _data_copy = None
        if self._position_data:
            _position_data_copy = tuple(a.copy() for a in self._position_data)
        else:
            _position_data_copy = None
        return replace(
            self,
            _data=_data_copy,
            _position_data=_position_data_copy,
            scanner_target_at_start=self.scanner_target_at_start.copy()
        )

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
    def data(self) -> Optional[Dict[str, np.ndarray]]:
        """ Dict of channel data arrays with channel names as keys. """
        if self._data is None:
            return None
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

    @property
    def position_data(self) -> Optional[Dict[str, np.ndarray]]:
        """ Dict of (axis) position data arrays with axis names as keys. """
        if self._position_data is None:
            return None
        return {ax: data for ax, data in zip(self.settings.position_feedback_axes, self._position_data)}

    @position_data.setter
    def position_data(self, position_data_dict: Dict[str, np.ndarray]) -> None:
        if not self.settings.has_position_feedback:
            raise ValueError('Scanner does not have position feedback. Cannot set position data.')
        axes = tuple(position_data_dict.keys())
        if axes != self.settings.position_feedback_axes:
            raise ValueError(f'Unknown axis names encountered in {axes} or axes do not have position feedback. '
                             f'Valid axis names are {self.settings.position_feedback_axes}.')
        if not all([val.shape == self.settings.resolution for val in position_data_dict.values()]):
            raise ValueError(f'Data shapes do not match resolution {self.settings.resolution}.')
        self._position_data = tuple(data for data in position_data_dict.values())

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

        if self.settings.has_position_feedback:
            self.position_data = {ax: np.full(self.settings.resolution, np.nan) for ax in
                                  self.settings.position_feedback_axes}
        else:
            self._position_data = None
        self.data = {
            ch: np.full(self.settings.resolution, np.nan,
                        dtype=self.channel_dtypes[ch]) for ch in self.settings.channels
        }
        return


@dataclass(frozen=True)
class ScanImage:
    """
    Immutable class containing all data associated to a SPM image.
    """
    axis_units: Tuple[str, ...]
    axis_names: Tuple[str, ...]
    ranges: Tuple[Tuple[float, float], ...]
    data: np.ndarray
    data_name: str
    data_unit: Optional[str] = None

    def __post_init__(self) -> None:
        """
        """
        # Sanity checking
        data_dimension = len(np.array(self.data).shape)
        if not data_dimension == len(self.axis_units):
            raise ValueError('Dimension of data and length of axis_units have to be equal.')
        if not data_dimension == len(self.axis_names):
            raise ValueError('Dimension of data and length of axis_names have to be equal.')
        if not data_dimension == len(self.ranges):
            raise ValueError('Dimension of data and length of ranges have to be equal.')

    @classmethod
    def from_scan_data(cls, scan_data: ScanData, channel_name: str) -> 'ScanImage':
        if channel_name not in scan_data.settings.channels:
            raise ValueError(f'{channel_name} is not a valid channel')
        if not scan_data.data:
            raise ValueError(f'No data set yet')
        axis_units = tuple(scan_data.axis_units.values())
        axis_names = scan_data.settings.axes
        axis_ranges = scan_data.settings.range
        data = scan_data.data[channel_name]
        data_name: str = channel_name
        channel_unit: str = scan_data.channel_units[channel_name]
        return cls(axis_units=axis_units, axis_names=axis_names, ranges=axis_ranges, data=data,
                   data_name=data_name, data_unit=channel_unit)

    @property
    def scan_resolutions(self) -> Tuple[int, ...]:
        res = [len(data_tmp) for data_tmp in self.data]
        return tuple(res)

    @property
    def scan_dimension(self) -> int:
        return len(self.axis_names)

    @property
    def scan_ranges(self) -> Tuple[Tuple[float, float], ...]:
        ranges = [(scan_range[0], scan_range[1]) for scan_range in self.ranges]
        return tuple(ranges)

    @property
    def si_factors(self) -> Tuple[ScaledFloat, ...]:
        factors = [ScaledFloat(scan_range[1] - scan_range[0]) for scan_range in self.scan_ranges]
        return tuple(factors)


class ScanningProbeInterface(Base):
    """ This is the Interface class to define the controls for a scanning probe device

    A scanner device is hardware that can move multiple axes.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._coordinate_transform = None
        self._coordinate_transform_matrix = None

    def coordinate_transform(self, val, inverse=False):
        if self._coordinate_transform is None:
            return val
        return self._coordinate_transform(val, inverse)

    def set_coordinate_transform(self, transform_func, transform_matrix=None):
        if transform_func is not None:
            raise ValueError('Coordinate transformation not supported by scanning hardware.')

    @property
    def coordinate_transform_enabled(self):
        return self._coordinate_transform is not None

    @property
    def supports_coordinate_transform(self):
        return isinstance(self, CoordinateTransformMixin)

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

    @property
    @abstractmethod
    def back_scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters of the backwards scan. Returns None if not configured or not available.
        """
        pass

    @abstractmethod
    def configure_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.
        Will reset back scan configuration if back scan is available.

        @param ScanSettings settings: ScanSettings instance holding all parameters
        """
        pass

    @abstractmethod
    def configure_back_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters of the backwards scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.
        If a back scan is not explicitly configured, hardware-specific default settings will be used.

        @param ScanSettings settings: ScanSettings instance holding all parameters for the back scan
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

    @abstractmethod
    def get_scan_data(self) -> Optional[ScanData]:
        """ Retrieve the ScanData instance used in the scan.
        """
        pass

    @abstractmethod
    def get_back_scan_data(self) -> Optional[ScanData]:
        """ Retrieve the ScanData instance used in the backwards scan.
        Return None if back scan was not configured or is not available at all.
        """
        pass

    @abstractmethod
    def emergency_stop(self) -> None:
        """
        TODO: document what this should to differently than stop_scan.
        """
        pass

    def _expand_coordinate(self, coord):
        """
        Expand coord dict to all scanner dimensions, setting missing axes to current scanner target.
        """

        scanner_axes = self.constraints.axes
        current_target = self.get_target()
        len_coord = 0
        axes_unused = scanner_axes.keys()

        if coord:
            len_coord = np.asarray((list(coord.values())[0])).size
            axes_unused = [ax for ax in scanner_axes.keys() if ax not in coord.keys()]
        coord_unused = {}

        for ax in axes_unused:
            target_coord = current_target[ax]
            coords = np.ones(len_coord)*target_coord if len_coord > 1 else target_coord
            coord_unused[ax] = coords

        coord.update(coord_unused)

        return coord


class CoordinateTransformMixin(ScanningProbeInterface):
    """ Can be used by concrete hardware modules to facilitate coordinate transformation, except
    for performing scans.
    The transformation for scanning can be either implemented in the base or in the mixed hardware
    module.

    Usage:
        MyTransformationScanner(CoordinateTransformMixin, MyScanner):
            pass
    """
    def set_coordinate_transform(self, transform_func, transform_matrix=None):
        # ToDo: Proper sanity checking here, e.g. function signature etc.
        if transform_func is not None and not callable(transform_func):
            raise ValueError('Coordinate transformation function must be callable with '
                             'signature "coordinate_transform(value, inverse=False)"')
        self._coordinate_transform = transform_func
        self._coordinate_transform_matrix = transform_matrix

    def move_absolute(self, position, velocity=None, blocking=False):
        new_pos_bare = super().move_absolute(self.coordinate_transform(position), velocity, blocking)
        return self.coordinate_transform(new_pos_bare, inverse=True)

    def move_relative(self, distance, velocity=None, blocking=False):
        new_pos_bare = super().move_relative(self.coordinate_transform(distance), velocity, blocking)
        return self.coordinate_transform(new_pos_bare, inverse=True)

    def get_target(self):
        return self.coordinate_transform(super().get_target(), inverse=True)

    def get_position(self):
        return self.coordinate_transform(super().get_position(), inverse=True)

    def _calc_matr_2_tiltangle(self):
        """
        Calculates the tilt angle in radians of a given rotation matrix.
        Formula from https://en.wikipedia.org/wiki/Rotation_matrix
        """

        rotation_matrix = self._coordinate_transform_matrix.matrix[0:3,0:3]
        trace = np.trace(rotation_matrix)
        tilt_angle_abs = np.arccos((trace-1)/2)
        return tilt_angle_abs

    def get_scan_data(self):
        scan_data = super().get_scan_data()
        if scan_data:
            scan_data.coord_transform_info = self._get_coord_transform_info()
        return scan_data

    def get_back_scan_data(self):
        scan_data = super().get_back_scan_data()
        if scan_data:
            scan_data.coord_transform_info = self._get_coord_transform_info()
        return scan_data

    def _get_coord_transform_info(self) -> Dict[str, Any]:
        info = {'enabled': False}
        if self.coordinate_transform_enabled:
            info.update({
                'enabled': True,
                'transform_matrix': self._coordinate_transform_matrix.matrix,
                'tilt_angle (deg)': np.rad2deg(self._calc_matr_2_tiltangle()),
                'translation': self._coordinate_transform_matrix.matrix[0:3, -1]
            })
        return info
