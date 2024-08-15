# -*- coding: utf-8 -*-
"""
This file contains the qudi logic to continuously read data from a wavemeter device and eventually
interpolates the acquired data with the simultaneously obtained counts from a
time_series_reader_logic. It is intended to be used in conjunction with the
high_finesse_wavemeter.py.

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

import math
import numpy as np
import matplotlib.pyplot as plt
from enum import Enum
from PySide2 import QtCore
from dataclasses import dataclass
from lmfit.model import ModelResult as _ModelResult
from scipy.constants import speed_of_light as _SPEED_OF_LIGHT
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.interface.data_instream_interface import SampleTiming
from qudi.interface.data_instream_interface import DataInStreamConstraints, DataInStreamInterface
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain
from qudi.util.datastorage import TextDataStorage
from qudi.util.ringbuffer import InterleavedRingBuffer, RingBuffer
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
from qudi.util.thread_exception_watchdog import threaded_exception_watchdog


class AxisType(Enum):
    WAVELENGTH = 'wavelength'
    FREQUENCY = 'frequency'


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
    has_timestamps: bool
    dtype: type = np.float64
    max_samples: int = -1
    initial_size: int = 1024
    grow_factor: float = 2.

    def __post_init__(self):
        self._sample_count = 0
        self._laser_span = [float('inf'), float('-inf')]
        if self.max_samples > 0:
            self._scan_data = InterleavedRingBuffer(interleave_factor=self.channel_count,
                                                    size=self.max_samples,
                                                    dtype=self.dtype,
                                                    allow_overwrite=True)
            self._laser_data = RingBuffer(size=self.max_samples,
                                          dtype=self.dtype,
                                          allow_overwrite=True)
            self._alt_laser_data = RingBuffer(size=self.max_samples,
                                              dtype=self.dtype,
                                              allow_overwrite=True)
            if self.has_timestamps:
                self._timestamps = RingBuffer(size=self.max_samples,
                                              dtype=np.float64,
                                              allow_overwrite=True)
            else:
                self._timestamps = None
        else:
            self._scan_data = np.empty([self.initial_size, self.channel_count],
                                       dtype=self.dtype)
            self._laser_data = np.empty(self.initial_size, dtype=self.dtype)
            self._alt_laser_data = np.empty(self.initial_size, dtype=self.dtype)
            if self.has_timestamps:
                self._timestamps = np.empty(self.initial_size, dtype=np.float64)
            else:
                self._timestamps = None

    def _grow_buffers(self) -> None:
        old_sample_size = self._scan_data.shape[0]
        # grow by at least 1 sample
        new_sample_size = max(old_sample_size + 1, int(round(old_sample_size * self.grow_factor)))
        # Expand scan data buffer
        new_data = np.empty([new_sample_size, self._scan_data.shape[1]],
                            dtype=self._scan_data.dtype)
        new_data[:self._sample_count, :] = self._scan_data[:self._sample_count, :]
        self._scan_data = new_data
        # Expand laser data buffers
        new_data = np.empty(new_sample_size, dtype=self._laser_data.dtype)
        new_data[:self._sample_count] = self._laser_data[:self._sample_count]
        self._laser_data = new_data
        new_data = np.empty(new_sample_size, dtype=self._alt_laser_data.dtype)
        new_data[:self._sample_count] = self._alt_laser_data[:self._sample_count]
        self._alt_laser_data = new_data
        # Expand timestamps buffer
        if self._timestamps is not None:
            new_data = np.empty(new_sample_size, dtype=self._timestamps.dtype)
            new_data[:self._sample_count] = self._timestamps[:self._sample_count]
            self._timestamps = new_data

    @property
    def laser_data(self) -> np.ndarray:
        if self.max_samples > 0:
            return self._laser_data.unwrap()
        else:
            return self._laser_data[:self._sample_count]

    @property
    def alt_laser_data(self) -> np.ndarray:
        if self.max_samples > 0:
            return self._alt_laser_data.unwrap()
        else:
            return self._alt_laser_data[:self._sample_count]

    @property
    def scan_data(self) -> np.ndarray:
        if self.max_samples > 0:
            return self._scan_data.unwrap()
        else:
            return self._scan_data[:self._sample_count, :]

    @property
    def timestamps(self) -> Union[None, np.ndarray]:
        if self.max_samples > 0:
            return None if self._timestamps is None else self._timestamps.unwrap()
        else:
            return None if self._timestamps is None else self._timestamps[:self._sample_count]

    def add_data_frame(self,
                       laser_data: np.ndarray,
                       scan_data: np.ndarray,
                       timestamps: Optional[np.ndarray] = None):
        new_sample_count = self._sample_count + laser_data.size
        alt_laser_data = self._convert_wavelength_frequency(laser_data)
        if self.max_samples > 0:
            # Add samples to buffers
            self._laser_data.write(laser_data)
            self._alt_laser_data.write(alt_laser_data)
            self._scan_data.write(scan_data)
            if self._timestamps is not None:
                self._timestamps.write(timestamps)
        else:
            # Grow buffers if necessary
            if new_sample_count > self._laser_data.size:
                self._grow_buffers()
            # Add samples to buffers
            self._laser_data[self._sample_count:new_sample_count] = laser_data
            self._alt_laser_data[self._sample_count:new_sample_count] = alt_laser_data
            self._scan_data[self._sample_count:new_sample_count, :] = scan_data
            if self._timestamps is not None:
                self._timestamps[self._sample_count:new_sample_count] = timestamps
        self._sample_count = new_sample_count

    @staticmethod
    def _convert_wavelength_frequency(data: np.ndarray) -> np.ndarray:
        return _SPEED_OF_LIGHT / data


@dataclass(frozen=True)
class ScanSettings:
    """ """
    laser_channel_only: bool


class LaserScanningLogic(LogicBase):
    """
    Example config for copy-paste:

    laser_scanning_logic:
        module.Class: 'laser_scanning_logic.LaserScanningLogic'
        connect:
            streamer: <data_instream_hardware>
            laser: <laser_control_hardware>  # optional
    """

    @staticmethod
    def __construct_histogram_span(value: Sequence[float]) -> Tuple[float, float]:
        if len(value) != 2:
            raise ValueError(f'Expected exactly two values (min, max) but received "{value}"')
        return min(value), max(value)

    @staticmethod
    def __construct_histogram_bins(value: int) -> int:
        value = int(value)
        if value < 3:
            raise ValueError(f'Number of histogram bins must be >= 3 but received "{value:d}"')
        return value

    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object, object, object)
    sigStatusChanged = QtCore.Signal(bool)
    sigStatusChangedDisplaying = QtCore.Signal()
    sigNewWavelength2 = QtCore.Signal(object, object)
    sigFitChanged = QtCore.Signal(str, dict)  # fit_name, fit_results
    sigConfigurationChanged = QtCore.Signal()
    _sigNextDataFrame = QtCore.Signal()  # internal signal

    # connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')
    _laser = Connector(name='laser', interface='LaserControlInterface', optional=True)

    # config options
    _max_update_rate: float = ConfigOption(name='max_update_rate', default=30.)
    _max_samples: int = ConfigOption(name='max_samples', default=-1)
    _laser_channel: str = ConfigOption(name='laser_channel', missing='error')
    _laser_channel_type: AxisType = ConfigOption(name='laser_channel_type',
                                                 default=AxisType.WAVELENGTH,
                                                 missing='warn',
                                                 constructor=lambda x: AxisType(x))

    # status variables
    _fit_config_model: FitConfigurationsModel = StatusVar(name='fit_configs', default=list())
    _histogram_span: Tuple[float, float] = StatusVar(name='histogram_span',
                                                     default=(500.0e-9, 750.0e-9),
                                                     constructor=__construct_histogram_span)
    _histogram_bins: int = StatusVar(name='histogram_bins',
                                     default=200,
                                     constructor=__construct_histogram_bins)
    # Fixme:
    _scan_settings: ScanSettings = StatusVar(name='scan_settings', default=None)

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._threadlock = Mutex()
        self._stop_requested: bool = False

        # data fitting
        self._fit_container: FitContainer = None
        self._fit_envelope: bool = False
        self._last_fit_result = None

        # raw data
        self._data_buffer: LaserDataBuffer = LaserDataBuffer(1, False)
        # histogram data
        self._histogram: LaserDataHistogram = LaserDataHistogram((0, 1), 3, 1)

    def on_activate(self):
        if self._laser_channel not in self.streamer_constraints.channel_units:
            raise ValueError(f'ConfigOption "{self.__class__._laser_channel.name}" value not found '
                             f'in streaming hardware available channels')

        # Initialize data fitting
        self._fit_container = FitContainer(config_model=self._fit_config_model)
        self._fit_envelope = False
        self._last_fit_result = None

        # initialize all data arrays
        self.__init_data_buffers()
        self.__init_histogram()

        # Connect signals
        self._sigNextDataFrame.connect(self._measurement_loop_body, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        self._stop_scan()
        self._sigNextDataFrame.disconnect()

    @property
    def controllable_laser(self) -> bool:
        """ Flag indicating if a scannable laser is connected and controllable """
        return self._laser() is not None

    @property
    def laser_constraints(self) -> Union[None, Any]:
        return self._laser().constraints if self.controllable_laser else None

    @property
    def streamer_constraints(self) -> DataInStreamConstraints:
        """ Retrieve the hardware constrains from the counter device """
        # netobtain is required if streamer is a remote module
        return netobtain(self._streamer().constraints)

    @property
    def laser_contraints(self) -> Union[None, Any]:
        """ Returns constraints of connected laser control hardware """
        # ToDo: Implement
        return None

    @property
    def data_channel_units(self) -> Dict[str, str]:
        streamer: DataInStreamInterface = self._streamer()
        channels = [ch for ch in streamer.active_channels if ch != self._laser_channel]
        return {ch: u for ch, u in streamer.constraints.channel_units.items() if ch in channels}

    @property
    def sample_rate(self) -> float:
        streamer: DataInStreamInterface = self._streamer()
        return streamer.sample_rate

    @property
    def histogram_settings(self) -> Tuple[Tuple[float, float], int]:
        return self._histogram_span, self._histogram_bins

    @property
    def scan_settings(self) -> ScanSettings:
        return self._scan_settings

    @property
    def _laser_ch_index(self) -> int:
        return self._streamer().active_channels.index(self._laser_channel)

    def __init_data_buffers(self) -> None:
        streamer: DataInStreamInterface = self._streamer()
        dtype = streamer.constraints.data_type
        channel_count = len(streamer.active_channels) - 1
        has_timestamps = streamer.constraints.sample_timing == SampleTiming.TIMESTAMP
        self._data_buffer = LaserDataBuffer(channel_count=channel_count,
                                            has_timestamps=has_timestamps,
                                            dtype=dtype,
                                            max_samples=self._max_samples)

    def __init_histogram(self) -> None:
        channel_count = len(self._streamer().active_channels) - 1
        self._histogram = LaserDataHistogram(
            span=self._histogram_span,
            bins=self._histogram_bins,
            channel_count=channel_count
        )

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
        laser_data = data[:, self._laser_ch_index]
        if self._laser_ch_index == 0:
            scan_data = data[:, 1:]
        elif self._laser_ch_index == (data.shape[1] - 1):
            scan_data = data[:, :-1]
        else:
            mask = np.ones(data.size, dtype=bool)
            mask[self._laser_ch_index::data.shape[1]] = 0
            scan_data = data.reshape(data.size)[mask].reshape([samples, -1])
        return laser_data, scan_data, timestamps

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
                self._data_buffer.add_data_frame(laser_data=laser_data,
                                                 scan_data=scan_data,
                                                 timestamps=timestamps)
                self._histogram.add_data_frame(laser_data=laser_data, scan_data=scan_data)
                # Emit update signals
                self.__emit_data_update()
                # Call this method again via event queue
                self._sigNextDataFrame.emit()

    @QtCore.Slot()
    def start_scan(self) -> None:
        """ Start data acquisition loop """
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                streamer: DataInStreamInterface = self._streamer()
                laser: Any = self._laser()
                try:
                    if self.module_state() == 'locked':
                        self.log.warning('Measurement already running. "start_scan" call ignored.')
                    else:
                        if self._laser_channel not in streamer.active_channels:
                            raise RuntimeError(f'Laser channel "{self._laser_channel}" not active')
                        self.module_state.lock()
                        try:
                            # Start laser scan and stream acquisition
                            if streamer.module_state() == 'idle':
                                streamer.start_stream()
                            if (laser is not None) and (laser.module_state() == 'idle'):
                                laser.start_scan()
                            # Reset data buffers
                            self.__init_data_buffers()
                            self.__init_histogram()
                            # Kick-off measurement loop
                            self._sigNextDataFrame.emit()
                        except Exception:
                            self.module_state.unlock()
                            raise
                finally:
                    self.sigStatusChanged.emit(self.module_state() == 'locked')

    @QtCore.Slot()
    def stop_scan(self):
        """ Send a request to stop counting. """
        with self._threadlock:
            try:
                self._stop_scan()
            finally:
                self.sigStatusChanged.emit(self.module_state() == 'locked')

    def _stop_scan(self):
        with threaded_exception_watchdog(self.log):
            if self.module_state() == 'locked':
                streamer: DataInStreamInterface = self._streamer()
                laser = self._laser()
                try:
                    # stop streamer and laser
                    streamer.stop_stream()
                finally:
                    try:
                        if laser is not None:
                            laser.stop_scan()
                    finally:
                        self.module_state.unlock()

    @QtCore.Slot()
    def clear_data(self) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                self.__init_histogram()
                self.__init_data_buffers()
                self.__emit_data_update()

    @QtCore.Slot()
    def clear_histogram(self) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                self.__init_histogram()
                self.__emit_data_update()

    @QtCore.Slot()
    def autoscale_histogram(self):
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                laser_data = self._data_buffer.laser_data
                scan_data = self._data_buffer.scan_data
                if len(laser_data) > 1:
                    new_span = (laser_data.min(), laser_data.max())
                    if new_span[0] != new_span[1]:
                        self._histogram_span = new_span
                        # ToDo: Emit changed settings
                    self.__init_histogram()
                    self._histogram.add_data_frame(laser_data=laser_data, scan_data=scan_data)
                self.__emit_data_update()

    def configure_histogram(self, span: Tuple[float, float], bins: int) -> None:
        with self._threadlock:
            with threaded_exception_watchdog(self.log):
                self._histogram_span = (min(span), max(span))
                self._histogram_bins = int(bins)
                self.__init_histogram()
                self._histogram.add_data_frame(laser_data=self._data_buffer.laser_data,
                                               scan_data=self._data_buffer.scan_data)
                self.__emit_data_update()

    def configure_scan(self, settings: ScanSettings) -> None:
        with self._threadlock:
            self._scan_settings = settings

    def __emit_data_update(self) -> None:
        self.sigDataChanged.emit(self._data_buffer.timestamps,
                                 self._data_buffer.laser_data,
                                 self._data_buffer.scan_data,
                                 self._histogram.histogram_axis,
                                 self._histogram.histogram_data,
                                 self._histogram.envelope_data)

    @staticmethod
    @_fit_config_model.representer
    def __repr_fit_configs(value: FitConfigurationsModel) -> List[Dict[str, Any]]:
        return value.dump_configs()

    @_fit_config_model.constructor
    def __constr_fit_configs(self, value: Sequence[Mapping[str, Any]]) -> FitConfigurationsModel:
        model = FitConfigurationsModel(parent=self)
        model.load_configs(value)
        return model

    @property
    def fit_config_model(self) -> FitConfigurationsModel:
        return self._fit_config_model

    @property
    def fit_container(self) -> FitContainer:
        return self._get_fit_container()

    def _get_fit_container(self) -> FitContainer:
        return self._fit_container

    def do_fit(self, fit_config: str) -> Dict[str, Union[None, _ModelResult]]:
        """ Perform desired fit

        @param str fit_config: name of the fit. Must match a fit configuration in fit_config_model.
        """
        with self._threadlock:
            valid_fit_configs = self._fit_config_model.configuration_names
            if (fit_config != 'No Fit') and (fit_config not in valid_fit_configs):
                raise ValueError(f'Unknown fit configuration "{fit_config}" encountered. '
                                 f'Options are: {valid_fit_configs}')
            return self._do_fit(fit_config)

    def _do_fit(self, fit_config: str) -> Dict[str, Union[None, _ModelResult]]:

        fit_container = self._get_fit_container()

        # fit the histogram
        if not self.x_axis_hz_bool:
            fit_results = fit_container.fit_data(fit_config=fit_config, x=self.histogram_axis,
                                                 data=self.histogram if self.fit_histogram else self.envelope_histogram)[
                1]
        elif self.x_axis_hz_bool:
            fit_results = \
                fit_container.fit_data(fit_config=fit_config, x=constants.speed_of_light / self.histogram_axis,
                                       data=self.histogram if self.fit_histogram else self.envelope_histogram)[1]
        fit_config = fit_container._last_fit_config
        self._last_fit_result = fit_results

        fit_container.sigLastFitResultChanged.emit(fit_config, fit_results)
        self.sigFitChanged.emit(fit_config, fit_results)
        return fit_results

    def get_fit_results(self) -> Tuple[str, Dict[str, Union[None, _ModelResult]]]:
        # with self._threadlock:
        return self._get_fit_results()

    def _get_fit_results(self) -> Tuple[str, Dict[str, Union[None, _ModelResult]]]:
        fit_container = self._get_fit_container()
        return fit_container._last_fit_config, self._last_fit_result

    def save_data(self, postfix: Optional[str] = None, root_dir: Optional[str] = None) -> str:
        """ Save data of a single plot to file.

        @param str postfix: optional, an additional tag added to the generic filename
        @param str root_dir: optional, define a deviating folder for the data to be saved into
        @return str: file path the data was saved to
        """
        self._trace_data = np.vstack(((self.timings, self.counts), (self.wavelength, self.frequency)))
        with self._threadlock:
            return self._save_data(postfix, root_dir)

    def _save_data(self, postfix: Optional[str] = None, root_dir: Optional[str] = None) -> str:
        """ Save data of a single plot to file.

        @param str postfix: optional, an additional tag added to the generic filename
        @param str root_dir: optional, define a deviating folder for the data to be saved into
        @return str: file path the data was saved to
        """
        fit_container = self._get_fit_container()
        if len(self._trace_data[0]) < 1:
            self.log.warning(f'No data found in plot. Save aborted.')
            return ''

        data_set = self._trace_data

        data_set_histogram = np.vstack((self.histogram_axis, self.histogram))
        data_set_histogram = np.vstack((data_set_histogram, self.envelope_histogram))

        parameters_histogram = {'Number of Bins': self.get_bins(),
                                'Min Wavelength Of Histogram (m)': self._xmin_histo,
                                'Max Wavelength Of Histogram (m)': self._xmax_histo,
                                'FitResults': fit_container.formatted_result(self._last_fit_result)}

        file_label = postfix if postfix else 'qdLaserScanning'

        header = ['Timings (s)', 'Flourescence (counts/s)', 'Wavelength (m)', 'Frequency (Hz)']
        header_histogram = ['HistogramX', 'HistogramY', 'HistogramEnvelope']

        ds = TextDataStorage(
            root_dir=self.module_default_data_dir if root_dir is None else root_dir,
            column_formats='.15e',
            include_global_metadata=True)
        file_path, _, _ = ds.save_data(data_set.T,
                                       column_headers=header,
                                       column_dtypes=[float] * len(header),
                                       nametag=file_label)

        file_path, _, _ = ds.save_data(data_set_histogram.T,
                                       metadata=parameters_histogram,
                                       column_headers=header_histogram,
                                       column_dtypes=[float] * len(header),
                                       nametag=file_label + '_histogram')

        # plot graph and save as image alongside data file
        fig = self._plot_figure(self.x_axis_hz_bool, self.get_fit_results(), data_set, data_set_histogram,
                                fit_container)
        ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])

        self.log.debug(f'Data saved to: {file_path}')
        return file_path

    @staticmethod
    def _plot_figure(x_axis_bool, fitting, data_set, data_set_histogram,
                     fit_container: FitContainer) -> plt.Figure:

        fit_config, fit_results = fitting

        # High-resolution fit data and formatted result string
        if fit_results is not None:
            fit_data = fit_results.high_res_best_fit
            fit_result_str = fit_container.formatted_result(fit_results)
        else:
            fit_data = None
            fit_result_str = False

        fig, ax1 = plt.subplots()

        if not x_axis_bool:
            ax1.plot(data_set[2],
                     data_set[1],
                     linestyle=':',
                     linewidth=1,
                     label='RawData')
            ax1.plot(data_set_histogram[0],
                     data_set_histogram[1],
                     color='y',
                     label='Histogram')
            ax1.plot(data_set_histogram[0],
                     data_set_histogram[2],
                     color='g',
                     label='Envelope')
            if fit_data is not None:
                ax1.plot(fit_data[0],
                         fit_data[1],
                         color='r',
                         marker='None',
                         linewidth=1.5,
                         label='Fit')
        elif x_axis_bool:
            ax1.plot(data_set[3],
                     data_set[1],
                     linestyle=':',
                     linewidth=1,
                     label='RawData')
            ax1.plot(constants.speed_of_light / data_set_histogram[0],
                     data_set_histogram[1],
                     color='y',
                     label='Histogram')
            ax1.plot(constants.speed_of_light / data_set_histogram[0],
                     data_set_histogram[2],
                     color='g',
                     label='Envelope')
            if fit_data is not None:
                ax1.plot(fit_data[0],
                         fit_data[1],
                         color='r',
                         marker='None',
                         linewidth=1.5,
                         label='Fit')

        # Do not include fit parameter if there is no fit calculated.
        if fit_result_str:
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

        # set labels, units and limits
        if not x_axis_bool:
            ax1.set_xlabel('Wavelength (m)')
        elif x_axis_bool:
            ax1.set_xlabel('Frequency (Hz)')

        ax1.set_ylabel('Flourescence (counts/s)')

        ax1.legend()

        fig.tight_layout()
        return fig
