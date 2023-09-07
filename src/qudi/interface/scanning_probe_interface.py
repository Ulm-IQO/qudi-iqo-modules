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

from dataclasses import dataclass, field
from typing import Sequence, Union, Optional
import datetime
import numpy as np
from abc import abstractmethod
from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint
from qudi.util.yaml import yaml_object, get_yaml


@dataclass(frozen=True)
@yaml_object(get_yaml())
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
@yaml_object(get_yaml())
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
@yaml_object(get_yaml())
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

    channels: Sequence[str]
    axes: Sequence[str]
    range: Sequence[tuple[float, float]]
    resolution: Sequence[int]
    frequency: float
    position_feedback_axes: Sequence[str] = field(default_factory=list)

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
    channel_objects: Sequence[ScannerChannel]
    axis_objects: Sequence[ScannerAxis]
    backscan_configurable: bool  # TODO Incorporate in gui/logic toolchain?
    has_position_feedback: bool  # TODO Incorporate in gui/logic toolchain?
    square_px_only: bool  # TODO Incorporate in gui/logic toolchain?

    @property
    def channels(self) -> dict[str, ScannerChannel]:
        return {ch.name: ch for ch in self.channel_objects}

    @property
    def axes(self) -> dict[str, ScannerAxis]:
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
            raise ValueError(f'Unknown channel names encountered. '
                             f'Valid channel names are {self.channels}.')

    def check_axes(self, settings: ScanSettings) -> None:
        if not set(settings.axes).issubset(self.axes):
            raise ValueError(f'Unknown axis names encountered. '
                             f'Valid axis names are {self.channels}.')

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
            clipped_resolution.append(self.axes[axis].resolution.clip(resolution))
        # frequency needs to be within bounds for all axes
        clipped_frequency = settings.frequency
        for axis in settings.axes:
            clipped_frequency = self.axes[axis].frequency.clip(clipped_frequency)

        clipped_settings = ScanSettings(
            channels=settings.channels,
            axes=settings.axes,
            range=clipped_range,
            resolution=clipped_resolution,
            frequency=clipped_frequency,
            position_feedback_axes=settings.position_feedback_axes
        )
        return clipped_settings


@dataclass
@yaml_object(get_yaml())
class ScanData:
    """
    Object representing all data associated to a SPM measurement.

    @param ScannerChannel[] channels_obj: ScannerChannel objects involved in this scan
    @param ScannerAxis[] scan_axes_obj: ScannerAxis instances involved in the scan
    @param float[][2] scan_range: inclusive range for each scan axis
    @param int[] scan_resolution: planned number of points for each scan axis
    @param float scan_frequency: Scan pixel frequency of the fast axis
    @param dict scanner_target_at_start: optional, save scanner target (all axes) at beginning of scan
    @param str[] position_feedback_axes: optional, names of axes for which to acquire position
                                         feedback during the scan.
    """

    channels_obj: Sequence[ScannerChannel]
    scan_axes_obj: Sequence[ScannerAxis]
    scan_range: Sequence[tuple[float, float]]
    scan_resolution: Sequence[int]
    scan_frequency: Union[int, float]
    scanner_target_at_start: dict = None
    position_feedback_axes: Union[Sequence[str], None] = None

    timestamp: Union[datetime.datetime, None] = field(default=None, init=False)
    _data: Union[dict, None] = field(default=None, init=False)
    # TODO: Automatic interpolation onto rectangular grid needs to be implemented (for position feedback HW)
    position_data: Union[dict, None] = field(default=None, init=False)

    def __post_init__(self):
        # Sanity checking
        if not (0 < len(self.scan_axes_obj) <= 2):
            raise ValueError('ScanData can only be used for 1D or 2D scans.')
        if len(self.channels_obj) < 1:
            raise ValueError('At least one data channel must be specified for a valid scan.')
        if len(self.scan_axes_obj) != len(self.scan_range):
            raise ValueError('Parameters "scan_axes_obj" and "scan_range" must have same len. Given '
                             '{0:d} and {1:d}, respectively.'.format(len(self.scan_axes_obj),
                                                                     len(self.scan_range)))
        if len(self.scan_axes_obj) != len(self.scan_resolution):
            raise ValueError('Parameters "scan_axes_obj" and "scan_resolution" must have same len. '
                             'Given {0:d} and {1:d}, respectively.'.format(len(self.scan_axes_obj),
                                                                           len(self.scan_resolution)))
        if not all(isinstance(ax, ScannerAxis) for ax in self.scan_axes_obj):
            raise TypeError(
                'Parameter "scan_axes_obj" must be Sequence containing only ScannerAxis objects')
        if not all(len(ax_range) == 2 for ax_range in self.scan_range):
            raise TypeError(
                'Parameter "scan_range" must be Sequence containing only value pairs (len=2).')
        if not all(isinstance(res, int) for res in self.scan_resolution):
            raise TypeError(
                'Parameter "scan_resolution" must be Sequence containing only integers.')
        if not all(isinstance(ch, ScannerChannel) for ch in self.channels_obj):
            raise TypeError(
                'Parameter "channels_obj" must be Sequence containing only ScannerChannel objects.')
        if not all(np.issubdtype(ch.dtype, np.floating) for ch in self.channels_obj):
            raise TypeError('channel dtypes must be either builtin or numpy floating types')

        self.scan_axes_obj = tuple(self.scan_axes_obj)
        self.channels_obj = tuple(self.channels_obj)
        self.scan_range = tuple(self.scan_range)
        self.scan_resolution = tuple(self.scan_resolution)
        self.scan_frequency = float(self.scan_frequency)
        if self.position_feedback_axes is not None:
            if not set(self.position_feedback_axes).issubset(self.scan_axes):
                raise TypeError(
                    'The "position_feedback_axes" must be a subset of scan axes.'
                )
            self.position_feedback_axes = tuple(self.position_feedback_axes)

    def __copy__(self):
        new_inst = ScanData(channels_obj=self.channels_obj,
                            scan_axes_obj=self.scan_axes_obj,
                            scan_range=self.scan_range,
                            scan_resolution=self.scan_resolution,
                            scan_frequency=self.scan_frequency,
                            scanner_target_at_start=self.scanner_target_at_start,
                            position_feedback_axes=self.position_feedback_axes)
        new_inst.timestamp = self.timestamp
        if self._data is not None:
            new_inst.data = {ch: arr.copy() for ch, arr in self._data.items()}
        if self.position_data is not None:
            new_inst.position_data = {ch: arr.copy() for ch, arr in self.position_data.items()}
        return new_inst

    def __deepcopy__(self, memo=None):
        return self.__copy__()

    def copy(self):
        return self.__copy__()

    @property
    def scan_axes(self):
        return tuple(ax.name for ax in self.scan_axes_obj)

    @property
    def channels(self):
        return tuple(ch.name for ch in self.channels_obj)

    @property
    def channel_units(self):
        return {ch.name: ch.unit for ch in self.channels_obj}

    @property
    def axes_units(self):
        units = {ax.name: ax.unit for ax in self.scan_axes_obj}
        if self.has_position_feedback:
            units.update({ax.name: ax.unit for ax in self.position_feedback_axes})
        return units

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, data_dict):
        assert tuple(data_dict.keys()) == self.channels
        assert all([val.shape == self.scan_resolution for val in data_dict.values()])
        self._data = data_dict

    @property
    def has_position_feedback(self):
        return bool(self.position_feedback_axes)

    @property
    def scan_dimension(self):
        return len(self.scan_axes)

    def new_scan(self, timestamp=None):
        """

        @param timestamp:
        """
        if timestamp is None:
            self.timestamp = datetime.datetime.now()
        elif isinstance(timestamp, datetime.datetime):
            self.timestamp = timestamp
        else:
            raise TypeError('Optional parameter "timestamp" must be datetime.datetime object.')

        if self.has_position_feedback:
            self.position_data = {ax.name: np.full(self.scan_resolution, np.nan) for ax in
                                  self.position_feedback_axes}
        else:
            self.position_data = None
        self.data = {
            ch.name: np.full(self.scan_resolution, np.nan, dtype=ch.dtype) for ch in self.channels_obj
        }
        return


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
    def move_absolute(self, position: dict[str: float],
                      velocity: Optional[float] = None, blocking: bool = False) -> dict[str: float]:
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
    def move_relative(self, distance: dict[str: float],
                      velocity: Optional[float] = None, blocking: bool = False) -> dict[str: float]:
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
    def get_target(self) -> dict[str: float]:
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """
        pass

    @abstractmethod
    def get_position(self) -> dict[str: float]:
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
    def scan_data(self) -> Optional[ScanData]:
        """ Read-only property returning the ScanData instance used in the scan. Returns None if no scan has run yet.
        TODO: think about returning None or raising an exception
        """
        pass

    @abstractmethod
    def emergency_stop(self) -> None:
        """
        TODO: document what this should to differently than stop_scan.
        """
        pass
