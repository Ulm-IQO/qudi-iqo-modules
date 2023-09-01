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

from dataclasses import dataclass, field, InitVar, asdict
from typing import Sequence, Union
import datetime
import numpy as np
from abc import abstractmethod
from qudi.core.module import Base


class ScanningProbeInterface(Base):
    """ This is the Interface class to define the controls for a scanning probe device

    A scanner device is hardware that can move multiple axes.
    """

    @abstractmethod
    def get_constraints(self):
        """ Get hardware constraints/limitations.

        @return dict: scanner constraints
        """
        pass

    @abstractmethod
    def reset(self):
        """ Hard reset of the hardware.
        """
        pass

    @abstractmethod
    def configure_scan(self, settings):
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.

        @param ScanSettings settings: ScanSettings instance holding all parameters # TODO update me!

        @return (bool, ScanSettings): Failure indicator (fail=True),
                                      altered ScanSettings instance (same as "settings")
        """
        pass

    @abstractmethod
    def move_absolute(self, position, velocity=None, blocking=False):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a scan is in progress.

        @param bool blocking: If True this call returns only after the final position is reached.
        """
        pass

    @abstractmethod
    def move_relative(self, distance, velocity=None, blocking=False):
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.

        @param bool blocking: If True this call returns only after the final position is reached.

        """
        pass

    @abstractmethod
    def get_target(self):
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """
        pass

    @abstractmethod
    def get_position(self):
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).
        For the same target this value can fluctuate according to the scanners positioning accuracy.

        For scanning devices that do not have position feedback sensors, simply return the target
        position (see also: ScanningProbeInterface.get_target).

        @return dict: current position per axis.
        """
        pass

    @abstractmethod
    def start_scan(self):
        """

        @return (bool): Failure indicator (fail=True)
        """
        pass

    @abstractmethod
    def stop_scan(self):
        """

        @return bool: Failure indicator (fail=True)
        """
        pass

    @abstractmethod
    def get_scan_data(self):
        """

        @return (bool, ScanData): Failure indicator (fail=True), ScanData instance used in the scan
        """
        pass

    @abstractmethod
    def emergency_stop(self):
        """

        @return:
        """
        pass


@dataclass(frozen=True)
class ScannerChannel:
    name: str
    unit: str = ''
    # saving this as str instead of e.g. np.float64 object eases __dict__ representation
    dtype: str = 'float64'

    def __post_init__(self):
        if not isinstance(self.name, str):
            raise TypeError('Parameter "name" must be of type str.')
        if len(self.name) < 1:
            raise ValueError('Parameter "name" must be non-empty str.')
        if not isinstance(self.unit, str):
            raise TypeError('Parameter "unit" must be of type str.')
        # check if dtype can be understood as a compatible type by numpy
        try:
            np.dtype(self.dtype)
        except TypeError:
            raise TypeError('Parameter "dtype" must be numpy-compatible type.')

    def to_dict(self):
        return self.__dict__

    @classmethod
    # TODO: is this necessary?
    def from_dict(cls, dict_repr):
        return ScannerChannel(**dict_repr)


@dataclass(frozen=True)
class ScannerAxis:
    """
    """
    name: str
    unit: str
    value_range: tuple = (-np.inf, np.inf)
    step_range: tuple = (0, np.inf)
    resolution_range: tuple = (1, np.inf)
    frequency_range: tuple = (0, np.inf)

    def __post_init__(self):
        if not isinstance(self.name, str):
            raise TypeError('Parameter "name" must be of type str.')
        if self.name == '':
            raise ValueError('Parameter "name" must be non-empty str.')
        if not isinstance(self.unit, str):
            raise TypeError('Parameter "unit" must be of type str.')
        if not (len(self.value_range) == len(self.step_range) == len(self.resolution_range) == len(
                self.frequency_range) == 2):
            raise ValueError('Range parameters must be iterables of length 2.')
        if not self.min_resolution <= self.max_resolution:
            raise ValueError('Resolution range must be in ascending order.')
        if not self.min_step <= self.max_step:
            raise ValueError('Step range must be in ascending order.')
        if not self.min_value <= self.max_value:
            raise ValueError('Value range must be in ascending order.')
        if not self.min_frequency <= self.max_frequency:
            raise ValueError('Frequency range must be in ascending order.')

    @property
    def min_resolution(self):
        return self.resolution_range[0]

    @property
    def max_resolution(self):
        return self.resolution_range[1]

    @property
    def min_step(self):
        return self.step_range[0]

    @property
    def max_step(self):
        return self.step_range[1]

    @property
    def min_value(self):
        return self.value_range[0]

    @property
    def max_value(self):
        return self.value_range[1]

    @property
    def min_frequency(self):
        return self.frequency_range[0]

    @property
    def max_frequency(self):
        return self.frequency_range[1]

    def clip_value(self, value):
        if value < self.min_value:
            return self.min_value
        elif value > self.max_value:
            return self.max_value
        return value

    def clip_resolution(self, res):
        if res < self.min_resolution:
            return self.min_resolution
        elif res > self.max_resolution:
            return self.max_resolution
        return res

    def clip_frequency(self, freq):
        if freq < self.min_frequency:
            return self.min_frequency
        elif freq > self.max_frequency:
            return self.max_frequency
        return freq

    def to_dict(self):
        return self.__dict__

    # TODO: unnecessary?
    @classmethod
    def from_dict(cls, dict_repr):
        return ScannerAxis(**dict_repr)


@dataclass(frozen=True)
class ScanConstraints:
    """
    """
    in_axes: InitVar[list[ScannerAxis]]
    in_channels: InitVar[list[ScannerChannel]]
    backscan_configurable: bool  # TODO Incorporate in gui/logic toolchain?
    has_position_feedback: bool  # TODO Incorporate in gui/logic toolchain?
    square_px_only: bool  # TODO Incorporate in gui/logic toolchain?

    def __post_init__(self, in_axes, in_channels):
        if not all(isinstance(ax, ScannerAxis) for ax in in_axes):
            raise TypeError('Parameter "in_axes" must be of type ScannerAxis.')
        if not all(isinstance(ch, ScannerChannel) for ch in in_channels):
            raise TypeError('Parameter "in_channels" must be of type ScannerChannel.')
        if not isinstance(self.backscan_configurable, bool):
            raise TypeError('Parameter "backscan_configurable" must be of type bool.')
        if not isinstance(self.has_position_feedback, bool):
            raise TypeError('Parameter "has_position_feedback" must be of type bool.')
        if not isinstance(self.square_px_only, bool):
            raise TypeError('Parameter "square_px_only" must be of type bool.')

        object.__setattr__(self, 'axes', {ax.name: ax for ax in in_axes})
        object.__setattr__(self, 'channels', {ch.name: ch for ch in in_channels})


@dataclass
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

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, dict_repr):
        channels_obj = tuple(ScannerChannel.from_dict(ch) for ch in dict_repr['channels_obj'])
        scan_axes_obj = tuple(ScannerAxis.from_dict(ax) for ax in dict_repr['scan_axes_obj'])

        new_inst = cls(channels_obj=channels_obj,
                       scan_axes_obj=scan_axes_obj,
                       scan_range=dict_repr['scan_range'],
                       scan_resolution=dict_repr['scan_resolution'],
                       scan_frequency=dict_repr['scan_frequency'],
                       scanner_target_at_start=dict_repr['scanner_target_at_start'],
                       position_feedback_axes=dict_repr['position_feedback_axes'])
        new_inst.data = dict_repr['_data']
        new_inst.position_data = dict_repr['position_data']
        new_inst.timestamp = dict_repr['timestamp']
        return new_inst
