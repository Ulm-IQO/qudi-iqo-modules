# -*- coding: utf-8 -*-

"""
This module contains the Qudi interface file for magnet hardware.

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

import datetime
from abc import abstractmethod
from dataclasses import dataclass, field, asdict, replace
from typing import Optional, Tuple, Dict

import numpy as np

from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint


@dataclass(frozen=False)
class MagnetFOM:
    """
    Data class representing a magnet figure of merit and its constraints.

    """
    name: str
    func: callable
    # func_full: callable   # for now, only scalar FOM result
    measurement_time: float
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
class MagnetControlAxis:
    """
    Data class representing a magnet control axis and its constraints.
    Then scan axes are swept during a magnet scanning measurement.
    """
    name: str
    unit: str
    control_value: ScalarConstraint
    step: ScalarConstraint
    resolution: ScalarConstraint

    def __post_init__(self):
        if self.name == '':
            raise ValueError('Parameter "name" must be non-empty str.')


@dataclass(frozen=True)
class MagnetScanSettings:
    """
    Data class representing all settings specifying a magnet scan measurement.

    @param str[] channels: names of scanner channels involved in this scan
    @param str[] axes: names of scanner axes involved in this scan
    @param float[][2] range: inclusive range for each scan axis
    @param int[] resolution: planned number of points for each scan axis
    @param float frequency: Scan pixel frequency of the fast axis
    @param str[] position_feedback_axes: optional, names of axes for which to acquire position
                                         feedback during the scan.
    """

    axes: Tuple[str, ...]
    range: Tuple[Tuple[float, float], ...]
    resolution: Tuple[int, ...]
    frequency: float
    position_feedback_axes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Sanity checking
        if not (0 < len(self.axes) <= 2):
            raise ValueError('Only 1D and 2D scans are implemented.')
        if len(self.axes) != len(self.range):
            raise ValueError(f'Parameters "axes" and "range" must have same len. Given '
                             f'{len(self.axes)} and {len(self.range)}, respectively.')
        if len(self.axes) != len(self.resolution):
            raise ValueError(f'Parameters "axes" and "resolution" must have same len. '
                             f'Given {len(self.axes)} and {len(self.resolution)}, respectively.')
        if self.frequency <= 0:
            raise ValueError(f"Frequency must be positive, not {self.frequency}")

        if not set(self.position_feedback_axes).issubset(self.axes):
            raise TypeError(
                'The "position_feedback_axes" must be a subset of scan axes.'
            )

    @classmethod
    def from_dict(cls, dict_repr):
        """Create instance from dict taking care to convert arguments to tuples."""
        return cls(
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

    @property
    def channels(self):
        return ['FOM']


@dataclass(frozen=True)
class MagnetConstraints:
    """
    Data class representing the complete constraints of a scanning probe measurement.
    """
    axis_objects: Tuple[MagnetControlAxis, ...]
    has_position_feedback: bool  # TODO Incorporate in gui/logic toolchain?
    control_accuracy: Optional[dict] = None

    @property
    def axes(self) -> Dict[str, MagnetControlAxis]:
        return {ax.name: ax for ax in self.axis_objects}

    def is_valid(self, settings: MagnetScanSettings) -> bool:
        try:
            self.check_settings(settings)
        except (ValueError, TypeError):
            return False
        return True

    def check_settings(self, settings: MagnetScanSettings) -> None:
        self.check_axes(settings)

    def check_axes(self, settings: MagnetScanSettings) -> None:
        if not set(settings.axes).issubset(self.axes):
            raise ValueError(f'Unknown axis names encountered in {settings.axes}. '
                             f'Valid axis names are {list(self.axes.keys())}.')

        for axis_name, _range, resolution in zip(settings.axes, settings.range, settings.resolution):
            axis = self.axes[axis_name]
            try:
                axis.control_value.is_valid(_range[0])
                axis.control_value.is_valid(_range[1])
            except ValueError as e:
                raise ValueError(f'Scan range out of bounds for axis "{axis_name}".') from e
            except TypeError as e:
                raise TypeError(f'Scan range type check failed for axis "{axis_name}".') from e

            try:
                axis.resolution.is_valid(resolution)
            except ValueError as e:
                raise ValueError(f'Scan resolution out of bounds for axis "{axis_name}".') from e
            except TypeError as e:
                raise TypeError(f'Scan resolution type check failed for axis "{axis_name}".') from e

    def clip(self, settings: MagnetScanSettings) -> MagnetScanSettings:
        self.check_axes(settings)
        clipped_range = []
        clipped_resolution = []
        for axis, _range, resolution in zip(settings.axes, settings.range, settings.resolution):
            clipped_range.append((float(self.axes[axis].position.clip(_range[0])),
                                  float(self.axes[axis].position.clip(_range[1]))))
            clipped_resolution.append(int(self.axes[axis].resolution.clip(resolution)))

        clipped_settings = MagnetScanSettings(
            channels=settings.channels,
            axes=settings.axes,
            range=tuple(clipped_range),
            resolution=tuple(clipped_resolution),
            position_feedback_axes=settings.position_feedback_axes
        )
        return clipped_settings


@dataclass
class MagnetScanData:
    """Data class representing settings and results of a scanning probe measurement.

    scanner_target_at_start may contain positions of axes other than the axes used in this scan.
    """

    settings: MagnetScanSettings
    _fom_unit: Tuple[str, ...]
    _fom_dtype: Tuple[str, ...]
    _axis_units: Tuple[str, ...]
    scanner_target_at_start: Optional[Dict[str, float]] = None
    timestamp: Optional[datetime.datetime] = None
    _data: Optional[Tuple[np.ndarray, ...]] = None
    # TODO: Automatic interpolation onto rectangular grid needs to be implemented (for position feedback HW)
    _position_data: Optional[Tuple[np.ndarray, ...]] = None

    @classmethod
    def from_constraints(cls, settings: MagnetScanSettings, constraints: MagnetConstraints, **kwargs):
        constraints.check_settings(settings)
        _fom_unit = ""
        _fom_dtype = 'float'

        _axis_units = tuple(constraints.axes[ax].unit for ax in settings.axes)
        return cls(
            settings=settings,
            _fom_unit=_fom_unit,
            _fom_dtype=_fom_dtype,
            _axis_units=_axis_units,

            **kwargs
        )

    @classmethod
    def from_dict(cls, dict_repr):
        """ Create a class instance from a dictionary.
        ScanData contains MagnetScanSettings, which is itself a dataclass
        and needs to be reconstructed separately. """
        settings = dict_repr['settings']
        dict_repr_without_settings = dict_repr.copy()
        del dict_repr_without_settings['settings']
        return cls(settings=MagnetScanSettings.from_dict(settings), **dict_repr_without_settings)

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
    def channels(self):
        return ['FOM']

    @property
    def channel_units(self):
        return self._fom_unit

    @property
    def channel_dtypes(self):
        return self._fom_dtype

    @property
    def axis_units(self) -> Dict[str, str]:
        return {ax: unit for ax, unit in zip(self.settings.axes, self._axis_units)}

    @property
    def data(self) -> Optional[Dict[str, np.ndarray]]:
        """ Dict of channel data arrays with channel names as keys. """
        if self._data is None:
            return None
        return {'FOM': self._data}

    @data.setter
    def data(self, data_dict: Dict[str, np.ndarray]) -> None:
        channels = tuple(data_dict.keys())
        if channels != tuple(['FOM']):
            raise ValueError(f'Unknown channel names encountered in {channels}. '
                             f'Valid channel names are: FOM.')

        if not all([val.shape == self.settings.resolution for val in data_dict.values()]):
            raise ValueError(f'Data shapes do not match resolution {self.settings.resolution}.')
        self._data = data_dict['FOM']

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
            'FOM': np.full(self.settings.resolution, np.nan,
                           dtype=self.channel_dtypes)
        }
        return


@dataclass
class MagnetStatus:
    """Data class representing the magnet setting parameters.
    """

    is_ready: bool


class MagnetInterface(Base):
    """ This is the Interface class to define the controls for a scanning probe device

    A scanner device is hardware that can move multiple axes.
    """

    @property
    @abstractmethod
    def constraints(self) -> MagnetConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        pass

    @abstractmethod
    def get_status(self) -> MagnetStatus:
        pass

    @abstractmethod
    def set_control(self, control: Dict[str, float], blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe to an absolute position.

        Log error and return current target position if something fails or a scan is in progress.

        @param dict control: absolute positions for all axes to move to, axis names as keys
        @param bool blocking: If True this call returns only after the final position is reached.

        @return dict: new position of all axes
        """
        pass

    @abstractmethod
    def get_control(self) -> Dict[str, float]:
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """
        pass

    @abstractmethod
    def emergency_stop(self) -> None:
        """

        @return:
        """
        pass
