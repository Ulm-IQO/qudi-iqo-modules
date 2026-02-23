# -*- coding: utf-8 -*-
"""
This file contains the qudi logic to continuously read data from a wavemeter device and eventually
interpolates the acquired data with the simultaneously obtained counts from a
time_series_reader_logic. It is intended to be used in conjunction with the
high_finesse_wavemeter.py.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['LaserScanningLogic']

import math
import numpy as np
import matplotlib.pyplot as plt
from PySide2 import QtCore
from dataclasses import dataclass
from datetime import datetime
from lmfit.model import ModelResult as _ModelResult
from scipy.constants import speed_of_light as _SPEED_OF_LIGHT
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.interface.data_instream_interface import DataInStreamConstraints, DataInStreamInterface
from qudi.interface.scannable_laser_interface import ScannableLaserConstraints
from qudi.interface.scannable_laser_interface import ScannableLaserSettings, ScannableLaserInterface
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain
from qudi.util.datastorage import TextDataStorage
from qudi.util.ringbuffer import InterleavedRingBuffer, RingBuffer
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
from qudi.util.thread_exception_watchdog import threaded_exception_watchdog


def convert_wavelength_frequency(data: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
    return _SPEED_OF_LIGHT / data


@dataclass
class LaserDataHistogram:
    span: Tuple[float, float]
    bins: int
    channel_count: int

    def __post_init__(self):
        self.span = (min(self.span), max(self.span))
        self.histogram_axis = np.linspace(*self.span, self.bins, dtype=np.float64)
        self.histogram_data = np.zeros([self.bins, self.channel_count], dtype=np.float64)
        self.envelope_data = np.zeros([self.bins, self.channel_count], dtype=np.float64)
        # n+1-dimensional binning axis, to avoid empty bin when using np.digitize
        offset = (self.span[1] - self.span[0]) / (self.bins - 1)
        offset /= 2
        self.__binning = np.linspace(self.span[0] - offset, self.span[1] + offset, self.bins + 1)
        # working array for histogram. Stores the number of occurrences for each bin.
        self.__bin_count = np.zeros(self.bins, dtype=np.int64)

    def add_data_frame(self, laser_data: np.ndarray, scan_data: np.ndarray) -> None:
        # create data mask for sample outside histogram range
        mask = np.logical_and(laser_data >= self.span[0], laser_data <= self.span[1])
        laser_data = laser_data[mask]
        scan_data = scan_data[mask]
        # occurrences = np.histogram(laser_data, self.__binning)[0]
        bin_indices = np.digitize(laser_data, self.__binning)
        bin_indices -= 1
        for sample_index, bin_index in enumerate(bin_indices):
            self.__bin_count[bin_index] += 1
            tmp = scan_data[sample_index, :] - self.histogram_data[bin_index, :]
            tmp /= self.__bin_count[bin_index]
            self.histogram_data[bin_index, :] += tmp
            self.envelope_data[bin_index, :] = np.max(
                [scan_data[sample_index, :], self.envelope_data[bin_index, :]],
                axis=0
            )


@dataclass
class LaserDataBuffer:
    channel_count: int
    max_samples: int = -1
    initial_size: int = 1024
    grow_factor: float = 2.

    def __post_init__(self):
        self._sample_count = 0
        self._laser_span = [float('inf'), float('-inf')]
        self.initial_size = max(self.initial_size, 3)
        if self.max_samples > 0:
            self._max_samples = max(self.max_samples, self.initial_size + 1)
        else:
            self._max_samples = float('inf')
        self._scan_data = np.empty([self.initial_size, self.channel_count], dtype=np.float64)
        self._wavelength_data = np.empty(self.initial_size, dtype=np.float64)
        self._frequency_data = np.empty(self.initial_size, dtype=np.float64)
        self._timestamps = np.empty(self.initial_size, dtype=np.float64)
        self.__ringbuffers = False

    @property
    def size(self) -> int:
        return self._wavelength_data.size

    def _grow_buffers(self, new_size: int) -> None:
        # Expand scan data buffer
        new_data = np.empty([new_size, self._scan_data.shape[1]], dtype=self._scan_data.dtype)
        new_data[:self._sample_count, :] = self._scan_data[:self._sample_count, :]
        self._scan_data = new_data
        # Expand laser data buffers
        new_data = np.empty(new_size, dtype=self._wavelength_data.dtype)
        new_data[:self._sample_count] = self._wavelength_data[:self._sample_count]
        self._wavelength_data = new_data
        new_data = np.empty(new_size, dtype=self._frequency_data.dtype)
        new_data[:self._sample_count] = self._frequency_data[:self._sample_count]
        self._frequency_data = new_data
        # Expand timestamps buffer
        new_data = np.empty(new_size, dtype=self._timestamps.dtype)
        new_data[:self._sample_count] = self._timestamps[:self._sample_count]
        self._timestamps = new_data

    def _swap_for_ringbuffers(self) -> None:
        # Swap scan data buffer
        new_data = InterleavedRingBuffer(interleave_factor=self.channel_count,
                                         size=self._max_samples,
                                         dtype=np.float64,
                                         allow_overwrite=True)
        new_data.write(self._scan_data[:self._sample_count, :])
        self._scan_data = new_data
        # Swap laser data buffers
        new_data = RingBuffer(size=self._max_samples, dtype=np.float64, allow_overwrite=True)
        new_data.write(self._wavelength_data[:self._sample_count])
        self._wavelength_data = new_data
        new_data = RingBuffer(size=self._max_samples, dtype=np.float64, allow_overwrite=True)
        new_data.write(self._frequency_data[:self._sample_count])
        self._frequency_data = new_data
        # Swap timestamps buffer
        new_data = RingBuffer(size=self._max_samples, dtype=np.float64, allow_overwrite=True)
        new_data.write(self._timestamps[:self._sample_count])
        self._timestamps = new_data
        # Set flag
        self.__ringbuffers = True

    @property
    def wavelength_data(self) -> np.ndarray:
        if self.__ringbuffers:
            return self._wavelength_data.unwrap()
        else:
            return self._wavelength_data[:self._sample_count]

    @property
    def frequency_data(self) -> np.ndarray:
        if self.__ringbuffers:
            return self._frequency_data.unwrap()
        else:
            return self._frequency_data[:self._sample_count]

    @property
    def scan_data(self) -> np.ndarray:
        if self.__ringbuffers:
            return self._scan_data.unwrap()
        else:
            return self._scan_data[:self._sample_count, :]

    @property
    def timestamps(self) -> Union[None, np.ndarray]:
        if self.__ringbuffers:
            return self._timestamps.unwrap()
        else:
            return self._timestamps[:self._sample_count]

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def add_data_frame(self,
                       wavelength_data: np.ndarray,
                       frequency_data: np.ndarray,
                       scan_data: np.ndarray,
                       timestamps: np.ndarray):
        new_count = self._sample_count + wavelength_data.size
        current_size = self.size
        # Check if buffers need to be expanded
        if (not self.__ringbuffers) and (new_count > current_size) and (current_size < self._max_samples):
            # grow by at least 1 sample if possible
            new_size = max(current_size + 1, int(current_size * self.grow_factor), new_count)
            if new_size >= self._max_samples:
                self._swap_for_ringbuffers()
            else:
                self._grow_buffers(new_size)

        if self.__ringbuffers:
            # Add samples to ring buffers
            self._wavelength_data.write(wavelength_data)
            self._frequency_data.write(frequency_data)
            self._scan_data.write(scan_data)
            self._timestamps.write(timestamps)
            self._sample_count = current_size
        else:
            # Add samples to linear buffers
            self._wavelength_data[self._sample_count:new_count] = wavelength_data
            self._frequency_data[self._sample_count:new_count] = frequency_data
            self._scan_data[self._sample_count:new_count, :] = scan_data
            self._timestamps[self._sample_count:new_count] = timestamps
            self._sample_count = new_count


class LaserScanningLogic(LogicBase):
    """
    Example config for copy-paste:

    laser_scanning_logic:
        module.Class: 'laser_scanning_logic.LaserScanningLogic'
        connect:
            streamer: <data_instream_hardware>
            laser: <laser_control_hardware>  # optional
        options:
            laser_channel: 'wavelength'
            max_update_rate: 30.0  # optional, maximum poll rate for new data (1/s)
            max_samples: -1  # optional, maximum number of samples to record (-1 for unlimited)
    """

    __default_fit_configs = [
        {'name'             : 'Lorentzian Dip',
         'model'            : 'Lorentzian',
         'estimator'        : 'Dip',
         'custom_parameters': None},

        {'name'             : 'Lorentzian Peak',
         'model'            : 'Lorentzian',
         'estimator'        : 'Peak',
         'custom_parameters': None},

        {'name'             : 'Gaussian Dip',
         'model'            : 'Gaussian',
         'estimator'        : 'Dip',
         'custom_parameters': None},

        {'name'             : 'Gaussian Peak',
         'model'            : 'Gaussian',
         'estimator'        : 'Peak',
         'custom_parameters': None},
    ]

    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object, object, object)
    sigStatusChanged = QtCore.Signal(bool, bool, bool)  # running, laser_only, data_only
    sigFitChanged = QtCore.Signal(str, object, bool)  # fit_name, fit_results, fit_envelope
    sigLaserTypeChanged = QtCore.Signal(bool)  # is_frequency
    sigHistogramSettingsChanged = QtCore.Signal(tuple, int)  # span, bins
    sigLaserScanSettingsChanged = QtCore.Signal(object)  # laser_scan_settings
    sigStabilizationTargetChanged = QtCore.Signal(object)  # laser target value
    _sigNextDataFrame = QtCore.Signal()  # internal signal

    # connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')
    _laser = Connector(name='laser', interface='ScannableLaserInterface', optional=True)

    # config options
    _max_update_rate: float = ConfigOption(name='max_update_rate', default=30.)
    _max_samples: int = ConfigOption(name='max_samples', default=-1)
    _laser_channel: str = ConfigOption(name='laser_channel', missing='error')

    # status variables
    _fit_config_model: FitConfigurationsModel = StatusVar(name='fit_configs',
                                                          default=__default_fit_configs)
    _histogram_span: Tuple[float, float] = StatusVar(name='histogram_span',
                                                     default=(500.0e-9, 750.0e-9))
    _histogram_bins: int = StatusVar(name='histogram_bins', default=200)
    _current_laser_is_frequency: bool = StatusVar(name='current_laser_is_frequency', default=False)

    @staticmethod
    @_histogram_span.constructor
    def __construct_histogram_span(value: Sequence[float]) -> Tuple[float, float]:
        if (len(value) != 2) or (value[0] == value[1]) or not all(np.isfinite(v) for v in value):
            raise ValueError(
                f'Expected exactly two unequal, finite values (min, max) but received "{value}"'
            )
        return min(value), max(value)

    @staticmethod
    @_histogram_bins.constructor
    def __construct_histogram_bins(value: int) -> int:
        value = int(value)
        if value < 3:
            raise ValueError(f'Number of histogram bins must be >= 3 but received "{value:d}"')
        return value

    @staticmethod
    @_fit_config_model.representer
    def __represent_fit_configs(value: FitConfigurationsModel) -> List[Dict[str, Any]]:
        return value.dump_configs()

    @_fit_config_model.constructor
    def __construct_fit_configs(self, value: Sequence[Mapping[str, Any]]) -> FitConfigurationsModel:
        model = FitConfigurationsModel(parent=self)
        model.load_configs(value)
        return model

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._threadlock = Mutex()
        self.__laser_ch_index: int = 0
        self.__laser_is_frequency: bool = False
        self.__channel_units: Dict[str, str] = dict()
        self.__laser_only_mode: bool = False
        self.__data_only_mode: bool = False

        # data fitting
        self._fit_container: FitContainer = None
        self._last_fit_result: Tuple[str, Union[None, _ModelResult]] = ('', None)

        # raw data
        self._data_buffer: LaserDataBuffer = LaserDataBuffer(channel_count=1)
        # histogram data
        self._wavelength_histogram: LaserDataHistogram = LaserDataHistogram(span=(0, 1),
                                                                            bins=3,
                                                                            channel_count=1)
        self._frequency_histogram: LaserDataHistogram = LaserDataHistogram(span=(0, 1),
                                                                           bins=3,
                                                                           channel_count=1)

    def on_activate(self):
        if self._laser_channel not in self.streamer_constraints.channel_units:
            raise ValueError(f'ConfigOption "{self.__class__._laser_channel.name}" value not found '
                             f'in streaming hardware available channels')
        self.__laser_only_mode = False
        self.__data_only_mode = False

        # Initialize data fitting
        self._fit_container = FitContainer(config_model=self._fit_config_model)

        # initialize all data arrays
        self.__init_data_channels()
        self.__init_data_buffers()
        self.__init_histograms()

        # Determine if laser data is frequency or wavelength (hardware side)
        self.__init_laser_data_type()

        # Connect signals
        self._sigNextDataFrame.connect(self._measurement_loop_body, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        self._stop_scan()
        self._sigNextDataFrame.disconnect()

    @property
    def laser_constraints(self) -> Union[None, ScannableLaserConstraints]:
        laser = self._laser()
        if laser is None:
            return None
        else:
            return laser.constraints

    @property
    def streamer_constraints(self) -> DataInStreamConstraints:
        """ Retrieve the hardware constrains from the counter device """
        # netobtain is required if streamer is a remote module
        return netobtain(self._streamer().constraints)

    @property
    def laser_scan_settings(self) -> Union[None, ScannableLaserSettings]:
        laser = self._laser()
        if laser is None:
            return None
        else:
            return netobtain(laser.scan_settings)

    @property
    def data_channel_units(self) -> Dict[str, str]:
        return self.__channel_units.copy()

    @property
    def histogram_settings(self) -> Tuple[Tuple[float, float], int]:
        if self.laser_is_frequency:
            return self._histogram_frequency_settings
        else:
            return self._histogram_wavelength_settings

    @property
    def laser_is_frequency(self) -> bool:
        return self._current_laser_is_frequency

    @property
    def laser_only_mode(self) -> bool:
        return self.__laser_only_mode

    @property
    def scan_data(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        timestamps = self._data_buffer.timestamps
        scan_data = self._data_buffer.scan_data
        if self.laser_is_frequency:
            laser_data = self._data_buffer.frequency_data
        else:
            laser_data = self._data_buffer.wavelength_data
        return timestamps, laser_data, scan_data

    @property
    def histogram_data(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self.laser_is_frequency:
            histo = self._frequency_histogram
        else:
            histo = self._wavelength_histogram
        return histo.histogram_axis, histo.histogram_data, histo.envelope_data

    @property
    def fit_config_model(self) -> FitConfigurationsModel:
        return self._fit_config_model

    @property
    def fit_container(self) -> FitContainer:
        return self._fit_container

    @property
    def fit_result(self) -> Tuple[str, Union[None, _ModelResult]]:
        return self._last_fit_result

    @property
    def scan_state(self) -> Tuple[bool, bool, bool]:
        """ Returns the current scan state flags (is_running, laser_only, data_only) """
        return self.module_state() == 'locked', self.__laser_only_mode, self.__data_only_mode

    def start_scan(self,
                   laser_only: Optional[bool] = False,
                   data_only: Optional[bool] = False) -> None:
        """ Start data acquisition loop """
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                streamer: DataInStreamInterface = self._streamer()
                laser: Union[None, ScannableLaserInterface] = self._laser()
                try:
                    if self.module_state() == 'locked':
                        self.log.warning('Measurement already running. "start_scan" call ignored.')
                    else:
                        self.module_state.lock()
                        try:
                            self.__data_only_mode = data_only or (laser is None) or (laser.module_state() == 'locked')
                            self.__laser_only_mode = laser_only
                            # Start laser scan and stream acquisition
                            if streamer.module_state() == 'idle':
                                if self.__laser_only_mode:
                                    channels = [self._laser_channel]
                                else:
                                    channels = list(streamer.constraints.channel_units)
                                streamer.configure(active_channels=channels,
                                                   streaming_mode=streamer.streaming_mode,
                                                   channel_buffer_size=streamer.channel_buffer_size,
                                                   sample_rate=streamer.sample_rate)
                                streamer.start_stream()
                            else:
                                active = streamer.active_channels
                                if self._laser_channel not in active:
                                    raise RuntimeError(
                                        f'Streamer is already running and laser channel '
                                        f'"{self._laser_channel}" is not active'
                                    )
                                self.__laser_only_mode = len(active) == 1
                            if not self.__data_only_mode:
                                laser.start_scan()
                            # Reset data buffers
                            self.__init_data_channels()
                            self.__init_data_buffers()
                            self.__init_histograms()
                            # Reset fit
                            self._do_fit('No Fit', False)
                            # Determine laser channel index
                            self.__laser_ch_index = streamer.active_channels.index(
                                self._laser_channel
                            )
                            # Kick-off measurement loop
                            self._sigNextDataFrame.emit()
                        except Exception:
                            self._stop_scan()
                            raise
                finally:
                    self.sigStatusChanged.emit(*self.scan_state)

    def stop_scan(self):
        """ Send a request to stop counting. """
        with self._threadlock:
            try:
                self._stop_scan()
            finally:
                self.sigStatusChanged.emit(*self.scan_state)

    def stabilize_laser(self, target: float) -> None:
        """ Move laser to target value and stabilize until further instructions """
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                laser = self._laser()
                if laser is not None:
                    laser.scan_to(target, blocking=True)
                    self.sigStabilizationTargetChanged.emit(target)

    def clear_data(self) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                try:
                    self.__init_histograms()
                    self.__init_data_buffers()
                finally:
                    self.sigDataChanged.emit(*self.scan_data, *self.histogram_data)

    def autoscale_histogram(self):
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                try:
                    wavelength_data = self._data_buffer.wavelength_data
                    if len(wavelength_data) > 1:
                        self.__configure_histogram(span=(wavelength_data.min(), wavelength_data.max()),
                                                   bins=self._histogram_bins)
                        self.__reset_histogram()
                        self.sigDataChanged.emit(*self.scan_data, *self.histogram_data)
                finally:
                    self.sigHistogramSettingsChanged.emit(*self.histogram_settings)

    def configure_histogram(self, span: Tuple[float, float], bins: int) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                try:
                    if self.laser_is_frequency:
                        span = (convert_wavelength_frequency(span[1]),
                                convert_wavelength_frequency(span[0]))
                    self.__configure_histogram(span, bins)
                    self.__reset_histogram()
                    self.sigDataChanged.emit(*self.scan_data, *self.histogram_data)
                finally:
                    self.sigHistogramSettingsChanged.emit(*self.histogram_settings)

    def configure_laser_scan(self, settings: ScannableLaserSettings) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                try:
                    laser = self._laser()
                    if laser is None:
                        raise RuntimeError(
                            'Unable to configure laser scan. No scannable laser connected.'
                        )
                    laser.configure_scan(bounds=settings.bounds,
                                         speed=settings.speed,
                                         mode=settings.mode,
                                         repetitions=settings.repetitions,
                                         initial_direction=settings.initial_direction)
                finally:
                    self.sigLaserScanSettingsChanged.emit(self.laser_scan_settings)


    def toggle_laser_type(self, is_frequency: bool) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                try:
                    self._current_laser_is_frequency = bool(is_frequency)
                finally:
                    self.sigLaserTypeChanged.emit(self.laser_is_frequency)
                    self.sigDataChanged.emit(*self.scan_data, *self.histogram_data)
                    self.sigHistogramSettingsChanged.emit(*self.histogram_settings)

    def do_fit(self,
               fit_config: str,
               fit_envelope: Optional[bool] = False) -> Union[None, _ModelResult]:
        """ Perform desired fit """
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                valid_fit_configs = self._fit_config_model.configuration_names
                if (fit_config != 'No Fit') and (fit_config not in valid_fit_configs):
                    raise ValueError(f'Unknown fit configuration "{fit_config}" encountered. '
                                     f'Options are: {valid_fit_configs}')
                return self._do_fit(fit_config, fit_envelope)

    def save_data(self, postfix: Optional[str] = None, root_dir: Optional[str] = None) -> str:
        """ Save data of a single plot to file.

        @param str postfix: optional, an additional tag added to the generic filename
        @param str root_dir: optional, define a deviating folder for the data to be saved into
        @return str: file path the data was saved to
        """
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                if self._data_buffer.sample_count > 0:
                    save_tag = postfix if postfix else 'qdLaserScanning'
                    timestamp = datetime.now()
                    root_dir = root_dir if root_dir else self.module_default_data_dir
                    self._save_scan_data(save_tag=save_tag, root_dir=root_dir, timestamp=timestamp)
                    self._save_histogram(save_tag=save_tag, root_dir=root_dir, timestamp=timestamp)

    @property
    def _histogram_frequency_settings(self) -> Tuple[Tuple[float, float], int]:
        span = (convert_wavelength_frequency(self._histogram_span[1]),
                convert_wavelength_frequency(self._histogram_span[0]))
        return span, self._histogram_bins

    @property
    def _histogram_wavelength_settings(self) -> Tuple[Tuple[float, float], int]:
        return self._histogram_span, self._histogram_bins

    def _stop_scan(self):
        with threaded_exception_watchdog(self.log):
            if self.module_state() == 'locked':
                streamer: DataInStreamInterface = self._streamer()
                laser: Union[None, ScannableLaserInterface] = self._laser()
                try:
                    streamer.stop_stream()
                finally:
                    try:
                        if not self.__data_only_mode:
                            laser.stop_scan()
                    finally:
                        self.module_state.unlock()

    def _do_fit(self,
                fit_config: str,
                fit_envelope: bool,
                channel: Optional[int] = 0) -> Union[None, _ModelResult]:
        histo = self._frequency_histogram if self.laser_is_frequency else self._wavelength_histogram
        data = histo.envelope_data if fit_envelope else histo.histogram_data
        if data.size == 0:
            return None
        fit_config, fit_result = self.fit_container.fit_data(fit_config=fit_config,
                                                             x=histo.histogram_axis,
                                                             data=data[:, channel])
        self._last_fit_result = (fit_config, fit_result)
        self.sigFitChanged.emit(fit_config, fit_result, fit_envelope)
        return fit_result

    def _save_histogram(self, save_tag: str, root_dir: str, timestamp: Optional = None) -> None:
        histo_x, histo_y, histo_env = self.histogram_data
        if histo_y.size == 0:
            return
        span, bins = self.histogram_settings
        is_frequency = self.laser_is_frequency
        fit_config, fit_result = self._last_fit_result
        fit_result_str = self.fit_container.formatted_result(fit_result)
        channel_units = self.data_channel_units
        x_column_header = 'Frequency (Hz)' if is_frequency else 'Wavelength (m)'
        y_column_headers = [f'Mean {ch} ({unit})' for ch, unit in channel_units.items()]
        env_column_headers = [f'Max {ch} ({unit})' for ch, unit in channel_units.items()]
        column_headers = [x_column_header, *y_column_headers, *env_column_headers]
        data = np.hstack([histo_x.reshape([-1, 1]), histo_y, histo_env])
        metadata = {
            'Number of Bins'                                              : bins,
            'Min Frequency (Hz)' if is_frequency else 'Min Wavelength (m)': span[0],
            'Max Frequency (Hz)' if is_frequency else 'Max Wavelength (m)': span[1],
            f'Fit Result ({fit_config})'                                  : fit_result_str
        }
        fig = self._plot_histogram(x=histo_x,
                                   y=histo_y,
                                   env=histo_env,
                                   channel_units=channel_units,
                                   fit_config=fit_config,
                                   fit_result=fit_result)
        ds = TextDataStorage(root_dir=root_dir, column_formats='.15e', include_global_metadata=True)
        file_path, _, _ = ds.save_data(data,
                                       timestamp=timestamp,
                                       metadata=metadata,
                                       column_headers=column_headers,
                                       nametag=f'{save_tag}_histogram')
        ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])
        self.log.debug(f'Histogram saved to: {file_path}')

    def _save_scan_data(self, save_tag: str, root_dir: str, timestamp: Optional = None) -> None:
        timestamps, laser_data, scan_data = self.scan_data
        is_frequency = self.laser_is_frequency
        channel_units = self.data_channel_units
        laser_column_header = 'Frequency (Hz)' if is_frequency else 'Wavelength (m)'
        data_column_headers = [f'{ch} ({unit})' for ch, unit in channel_units.items()]
        column_headers = ['Time (s)', laser_column_header, *data_column_headers]
        data = np.hstack([timestamps.reshape([-1, 1]), laser_data.reshape([-1, 1]), scan_data])
        ds = TextDataStorage(root_dir=root_dir, column_formats='.15e', include_global_metadata=True)
        file_path, _, _ = ds.save_data(data,
                                       timestamp=timestamp,
                                       column_headers=column_headers,
                                       nametag=save_tag)
        # fig = self._plot_scan_data()
        # ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])
        self.log.debug(f'Scan saved to: {file_path}')

    def _plot_histogram(self,
                        x: np.ndarray,
                        y: np.ndarray,
                        env: np.ndarray,
                        channel_units: Mapping[str, str],
                        fit_config: Optional[str] = '',
                        fit_result: Optional[_ModelResult] = None) -> plt.Figure:
        """ """
        first_ch, first_unit = next(iter(channel_units.items()))
        fig, ax1 = plt.subplots()
        ax1.set_xlabel('Frequency (Hz)' if self.laser_is_frequency else 'Wavelength (m)')
        ax1.set_ylabel(f'{first_ch} ({first_unit})')
        # ax1.plot(data_set[2],
        #          data_set[1],
        #          linestyle=':',
        #          linewidth=1,
        #          label='RawData')
        for idx, (channel, unit) in enumerate(channel_units.items()):
            ax1.plot(x, y[:, idx], label=f'{channel} Histogram')
            ax1.plot(x, env[:, idx], label=f'{channel} Envelope')
        if fit_result is not None:
            fit_data = fit_result.high_res_best_fit
            fit_result_str = self.fit_container.formatted_result(fit_result)
            ax1.plot(fit_data[0],
                     fit_data[1],
                     color='r',
                     marker='None',
                     linewidth=1.5,
                     label='Fit')

            # Parameters for the text plot:
            # The position of the text annotation is controlled with the
            # relative offset in x direction and the relative length factor
            # rel_len_fac of the longest entry in one column
            rel_offset = 0.02
            rel_len_fac = 0.011
            entries_per_col = 24

            # do reverse processing to get each entry in a list
            entry_list = fit_result_str.split('\n')
            # slice the entry_list in entries_per_col
            chunks = [entry_list[x:x + entries_per_col] for x in
                      range(0, len(entry_list), entries_per_col)]

            is_first_column = True  # first entry should contain header or \n

            for column in chunks:
                max_length = max(column, key=len)  # get the longest entry
                column_text = ''

                for entry in column:
                    column_text += entry.rstrip() + '\n'

                column_text = column_text[:-1]  # remove the last new line

                heading = f'Fit results for "{fit_config}":' if is_first_column else ''
                column_text = f'{heading}\n{column_text}'

                ax1.text(1.00 + rel_offset,
                         0.99,
                         column_text,
                         verticalalignment='top',
                         horizontalalignment='left',
                         transform=ax1.transAxes,
                         fontsize=12)

                # the rel_offset in position of the text is a linear function
                # which depends on the longest entry in the column
                rel_offset += rel_len_fac * len(max_length)

                is_first_column = False

        ax1.legend()
        fig.tight_layout()
        return fig

    def _measurement_loop_body(self) -> None:
        """ This method is periodically called during a running measurement.
        It pulls new data from hardware and processes it.
        """
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                # Abort condition
                if self.module_state() != 'locked':
                    return

                laser_data, scan_data, timestamps = self.__pull_new_data()
                # Convert laser data to/from frequency
                if self.__laser_is_frequency:
                    frequency_data = laser_data
                    wavelength_data = convert_wavelength_frequency(laser_data)
                else:
                    wavelength_data = laser_data
                    frequency_data = convert_wavelength_frequency(laser_data)
                # Generate timestamps if none are returned from streamer
                if timestamps is None:
                    count = self._data_buffer.sample_count
                    timestamps = np.arange(count, count + len(laser_data), dtype=np.float64)
                    timestamps /= self._streamer().sample_rate
                # Add data frame to buffers and update histograms
                self._data_buffer.add_data_frame(wavelength_data=wavelength_data,
                                                 frequency_data=frequency_data,
                                                 scan_data=scan_data,
                                                 timestamps=timestamps)
                self._wavelength_histogram.add_data_frame(laser_data=wavelength_data,
                                                          scan_data=scan_data)
                self._frequency_histogram.add_data_frame(laser_data=frequency_data,
                                                         scan_data=scan_data)
                # Emit update signals
                self.sigDataChanged.emit(*self.scan_data, *self.histogram_data)
                # Call this method again via event queue
                self._sigNextDataFrame.emit()

    def __init_laser_data_type(self) -> None:
        laser_unit = self.streamer_constraints.channel_units[self._laser_channel].lower()
        if laser_unit == 'm':
            self.__laser_is_frequency = False
        elif laser_unit in ('hz', '1/s'):
            self.__laser_is_frequency = True
        else:
            raise RuntimeError(
                f'Laser channel "{self._laser_channel}" has unsupported unit ("{laser_unit}")'
            )

    def __init_data_channels(self) -> None:
        streamer: DataInStreamInterface = self._streamer()

        laser_units = {'m', 'hz', '1/s'}

        channels = [
            ch for ch in streamer.active_channels
            if ch != self._laser_channel and
               streamer.constraints.channel_units.get(ch, '').lower() not in laser_units
        ]

        self.__channel_units = {
            ch: u for ch, u in streamer.constraints.channel_units.items() if ch in channels
        }

    def __init_data_buffers(self) -> None:
        streamer: DataInStreamInterface = self._streamer()
        dtype = streamer.constraints.data_type
        channel_count = 0 if self.__laser_only_mode else len(streamer.active_channels) - 1
        self._data_buffer = LaserDataBuffer(channel_count=channel_count,
                                            max_samples=self._max_samples,
                                            initial_size=min(1024, self._max_samples))

    def __init_histograms(self) -> None:
        channel_count = 0 if self.__laser_only_mode else len(self._streamer().active_channels) - 1
        span, bins = self._histogram_wavelength_settings
        self._wavelength_histogram = LaserDataHistogram(span=span,
                                                        bins=bins,
                                                        channel_count=channel_count)
        span, bins = self._histogram_frequency_settings
        self._frequency_histogram = LaserDataHistogram(span=span,
                                                       bins=bins,
                                                       channel_count=channel_count)

    def __pull_new_data(self) -> Tuple[np.ndarray, np.ndarray, Union[None, np.ndarray]]:
        """ Pulls new available samples from streaming device, appends them to existing data arrays
        and returns number of new samples per channel.
        Will stall to satisfy "max_update_rate" in case this method is called too frequent.
        """
        streamer: DataInStreamInterface = self._streamer()
        sample_rate = streamer.sample_rate
        min_samples = max(1, int(math.ceil(sample_rate / self._max_update_rate)))
        samples = max(streamer.available_samples, min_samples)
        data, timestamps = streamer.read_data(samples)
        # transfer data if obtained via remote module
        data = netobtain(data).reshape([samples, -1])
        if timestamps is not None:
            timestamps = netobtain(timestamps)
        # Separate laser data from scan data
        laser_data = data[:, self.__laser_ch_index]
        if self.__laser_only_mode or data.shape[1] < 2:
            scan_data = np.empty([samples, 0], dtype=data.dtype)
        else:
            mask = np.ones(data.size, dtype=bool)
            mask[self.__laser_ch_index::data.shape[1]] = 0
            scan_data = data.reshape(data.size)[mask].reshape([samples, -1])
        return laser_data, scan_data, timestamps

    def __configure_histogram(self, span: Tuple[float, float], bins: int) -> None:
        try:
            span = self.__construct_histogram_span(span)
            bins = self.__construct_histogram_bins(bins)
        except ValueError:
            pass
        else:
            self._histogram_span = span
            self._histogram_bins = bins

    def __reset_histogram(self) -> None:
        self.__init_histograms()
        wavelength_data = self._data_buffer.wavelength_data
        frequency_data = self._data_buffer.frequency_data
        scan_data = self._data_buffer.scan_data
        if scan_data.shape[1] > 0:
            self._wavelength_histogram.add_data_frame(laser_data=wavelength_data,
                                                      scan_data=scan_data)
            self._frequency_histogram.add_data_frame(laser_data=frequency_data,
                                                     scan_data=scan_data)
