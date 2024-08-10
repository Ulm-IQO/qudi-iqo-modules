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

from PySide2 import QtCore
import numpy as np
import time
import math
import matplotlib.pyplot as plt
import scipy.interpolate as interpolate
from enum import Enum

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamConstraints, DataInStreamInterface
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
from qudi.core.statusvariable import StatusVar
from qudi.util.network import netobtain
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping, Callable
from lmfit.model import ModelResult as _ModelResult
from qudi.util.datastorage import TextDataStorage
from scipy.constants import speed_of_light as _SPEED_OF_LIGHT


class AxisType(Enum):
    WAVELENGTH = 'wavelength'
    FREQUENCY = 'frequency'


class LaserScanningLogic(LogicBase):
    """
    Example config for copy-paste:

    laser_scanning_logic:
        module.Class: 'wavemeter_scanning_logic_3.WavemeterLogic'
        connect:
            streamer: wavemeter
            counterlogic: time_series_reader_logic
    """

    @staticmethod
    def __construct_histogram_range(value: Sequence[float]) -> Tuple[float, float]:
        if len(value) != 2:
            raise ValueError(f'Expected exactly two values (min, max) but received "{value}"')
        return min(value), max(value)

    @staticmethod
    def __construct_histogram_bins(value: int) -> int:
        value = int(value)
        if value < 3:
            raise ValueError(f'Number of histogram bins must be >= 3 but received "{value:d}"')
        return value

    @staticmethod
    def _convert_wavelength_frequency(data: np.ndarray) -> np.ndarray:
        return _SPEED_OF_LIGHT / data

    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object, object, object, object)
    sigStatusChanged = QtCore.Signal(bool)
    sigStatusChangedDisplaying = QtCore.Signal()
    sigNewWavelength2 = QtCore.Signal(object, object)
    sigFitChanged = QtCore.Signal(str, dict)  # fit_name, fit_results
    _sigNextDataFrame = QtCore.Signal()  # internal signal

    # connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')
    _laser = Connector(name='laser', interface='LaserControlInterface', optional=True)

    # config options
    _max_update_rate: float = ConfigOption(name='max_update_rate', default=30.)
    _laser_channel: str = ConfigOption(name='laser_channel', missing='error')
    _laser_channel_type: AxisType = ConfigOption(name='laser_channel_type',
                                                 default=AxisType.WAVELENGTH,
                                                 missing='warn',
                                                 constructor=lambda x: AxisType(x))

    # status variables
    _fit_config_model: FitConfigurationsModel = StatusVar(name='fit_configs', default=list())
    _histogram_range: Tuple[float, float] = StatusVar(name='histogram_range',
                                                      default=(500.0e-9, 750.0e-9),
                                                      constructor=__construct_histogram_range)
    _histogram_bins: int = StatusVar(name='histogram_bins',
                                     default=200,
                                     constructor=__construct_histogram_bins)

    _laser_axis_converters = {
        AxisType.WAVELENGTH: _convert_wavelength_frequency,
        AxisType.FREQUENCY : _convert_wavelength_frequency
    }

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._threadlock = Mutex()
        self._stop_requested: bool = False

        # data fitting
        self._fit_container: FitContainer = None
        self._fit_histogram: bool = True
        self._last_fit_result = None

        # raw data
        self._scan_data: np.ndarray = np.empty(0)
        self._laser_data: Dict[AxisType, np.ndarray] = dict()
        self._timestamps: Union[None, np.ndarray] = None
        self._valid_samples: int = 0
        # min, max (changes on each iteration)
        self._laser_range: Dict[AxisType, List[float]] = dict()

        # histogram data
        self._histogram_data: np.ndarray = np.empty(0)
        self._histogram_axis: np.ndarray = np.empty(0)
        self._histogram_envelope: np.ndarray = np.empty(0)
        self.__binning: np.ndarray = np.empty(0)
        self.__rawhisto: np.ndarray = np.empty(0)
        self.__sumhisto: np.ndarray = np.empty(0)

    def on_activate(self):
        if self._laser_channel not in self.streamer_constraints.channel_units:
            raise ValueError(f'ConfigOption "{self.__class__._laser_channel.name}" value not found '
                             f'in streaming hardware available channels')

        # Initialize data fitting
        self._fit_container = FitContainer(config_model=self._fit_config_model)
        self._fit_histogram = False
        self._last_fit_result = None

        # initialize all data arrays
        self.__init_data_buffers()

        # Connect signals
        self._sigNextDataFrame.connect(self._measurement_loop_body, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        self._stop_scanning()
        self._sigNextDataFrame.disconnect()

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
    def _laser_ch_index(self) -> int:
        return self._streamer().active_channels.index(self._laser_channel)

    def __init_data_buffers(self) -> None:
        init_samples = 1024  # Initial number of samples per channel for buffer size
        streamer: DataInStreamInterface = self._streamer()
        dtype = streamer.constraints.data_type
        self._valid_samples = 0
        self._laser_range = {
            axis_type: [np.inf, -np.inf] for axis_type in self._laser_axis_converters
        }
        # data buffers
        self._scan_data = np.empty([init_samples, len(streamer.active_channels) - 1], dtype=dtype)
        self._laser_data = {axis_type: np.empty(init_samples, dtype=dtype) for axis_type in
                            self._laser_axis_converters}
        # timestamps buffer
        if streamer.constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._timestamps = np.empty(init_samples, dtype=np.float64)
        else:
            self._timestamps = None

    def __init_histogram(self) -> None:
        self._histogram_axis = np.linspace(*self._histogram_range, self._histogram_bins,
                                           dtype=np.float64)
        self._histogram_data = np.zeros([self._histogram_bins, self._scan_data.shape[1]],
                                        dtype=np.float64)
        self._histogram_envelope = np.zeros([self._histogram_bins, self._scan_data.shape[1]],
                                            dtype=np.float64)
        # n+1-dimensional binning axis, to avoid empty bin when using np.digitize
        offset = (self._histogram_range[1] - self._histogram_range[0]) / (self._histogram_bins - 1)
        offset /= 2
        self.__binning = np.linspace(self._histogram_range[0] - offset,
                                     self._histogram_range[1] + offset,
                                     self._histogram_bins + 1)
        # working arrays for histogram
        self.__rawhisto = np.zeros([self._histogram_bins, self._scan_data.shape[1]],
                                   dtype=np.float64)
        self.__sumhisto = np.zeros(self._bins, dtype=np.int64)

    def __expand_data_buffers(self) -> None:
        """ Double the existing data buffers """
        factor = 2  # Expand array size by this factor
        # Expand data buffer
        new_shape = [self._scan_data.shape[0] * factor, self._scan_data.shape[1]]
        new_scan_data = np.empty(new_shape, dtype=self._scan_data.dtype)
        new_scan_data[:self._valid_samples, :] = self._scan_data[:self._valid_samples, :]
        self._scan_data = new_scan_data
        # Expand laser data buffers
        for axis_type, laser_data in self._laser_data.items():
            new_data = np.empty(laser_data.size * factor, dtype=laser_data.dtype)
            new_data[:self._valid_samples] = laser_data[:self._valid_samples]
            self._laser_data[axis_type] = new_data
        # Expand timestamps buffer
        if self._timestamps is not None:
            new_timestamps = np.empty(self._timestamps.size * factor, dtype=self._timestamps.dtype)
            new_timestamps[:self._valid_samples] = self._timestamps[:self._valid_samples]
            self._timestamps = new_timestamps

    def __pull_new_data(self) -> int:
        """ Pulls new available samples from streaming device, appends them to existing data arrays
        and returns number of new samples per channel.
        Will stall to satisfy "max_update_rate" in case this method is called too frequent.
        """
        streamer: DataInStreamInterface = self._streamer()
        sample_rate = streamer.sample_rate
        min_samples = max(1, int(math.ceil(sample_rate / self._max_update_rate)))
        samples = max(streamer.available_samples, min_samples)
        end_index = self._valid_samples + samples
        new_data, new_timestamps = streamer.read_data(samples)
        # Expand data buffers if needed
        if (self._scan_data.shape[0] - self._valid_samples) < samples:
            self.__expand_data_buffers()
        new_data = netobtain(new_data).reshape([samples, -1])
        # efficient slicing for laser data at boundaries
        scan_data_buf = self._scan_data[self._valid_samples:end_index, :]
        if self._laser_ch_index == 0:
            scan_data_buf[:, :] = new_data[:, 1:]
        elif self._laser_ch_index == self._scan_data.shape[1]:
            scan_data_buf[:, :] = new_data[:, :-1]
        else:
            scan_data_buf[:, :self._laser_ch_index] = new_data[:, :self._laser_ch_index]
            scan_data_buf[:, self._laser_ch_index:] = new_data[:, self._laser_ch_index+1:]
        laser_data_buf = self._laser_data[self._laser_channel_type]
        laser_data_buf[self._valid_samples:end_index] = new_data[:, self._laser_ch_index]
        if self._timestamps is not None:
            new_timestamps = netobtain(new_timestamps)
            self._timestamps[self._valid_samples:self._valid_samples + samples] = new_timestamps
        return samples

    def __calculate_laser_data(self, new_samples: int) -> None:
        end_index = self._valid_samples + new_samples
        orig_data = self._laser_data[self._laser_channel_type][self._valid_samples:end_index]
        orig_range = self._laser_range[self._laser_channel_type]
        orig_range[0] = min(orig_range[0], orig_data.min())
        orig_range[1] = max(orig_range[1], orig_data.max())
        for axis_type, laser_data in self._laser_data.items():
            if axis_type != self._laser_channel_type:
                converter = self._laser_axis_converters[axis_type]
                laser_data[self._valid_samples:end_index] = converter(orig_data)
                laser_range = self._laser_range[axis_type]
                laser_range[0] = min(laser_range[0],
                                     laser_data[self._valid_samples:end_index].min())
                laser_range[1] = max(laser_range[1],
                                     laser_data[self._valid_samples:end_index].max())

    def __calculate_histogram(self, new_samples: int) -> None:
        """ Calculate new points for the histogram """
        end_index = self._valid_samples + new_samples
        # ToDo: Distinguish between wavelength and frequency
        scan_data = self._scan_data[self._valid_samples:end_index, :]
        laser_data = self._laser_data[self._laser_channel_type][self._valid_samples:end_index]
        # create data mask for sample outside histogram range
        mask = np.logical_and(laser_data >= self._histogram_range[0],
                              laser_data <= self._histogram_range[1])
        laser_data = laser_data[mask]
        scan_data = scan_data[mask]
        # occurrences = np.histogram(laser_data, self.__binning)[0]
        bin_indices = np.digitize(laser_data, self.__binning)
        bin_indices -= 1
        for sample_index, bin_index in enumerate(bin_indices):
            self.__sumhisto[bin_index] += 1
            tmp = scan_data[sample_index, :] - self._histogram_data[bin_index, :]
            tmp /= self.__sumhisto[bin_index]
            self._histogram_data[bin_index, :] += tmp
            self._histogram_envelope[bin_index, :] = np.max(
                [scan_data[sample_index, :], self._histogram_envelope[bin_index, :]],
                axis=0
            )

    def _measurement_loop_body(self) -> None:
        """ This method is periodically called during a running measurement.
        It pulls new data from hardware and processes it.
        """
        with self._threadlock:
            # Abort condition
            if self.module_state() != 'locked':
                return

            samples = self.__pull_new_data()
            self.__calculate_laser_data(samples)
            self.__calculate_histogram(samples)
            self._valid_samples += samples
            # Call this method again via event queue
            self._sigNextDataFrame.emit()

    @QtCore.Slot()
    def start_scanning(self) -> None:
        """ Start data acquisition loop """
        with self._threadlock:
            streamer: DataInStreamInterface = self._streamer()
            laser: Any = self._laser()
            try:
                if self.module_state() == 'locked':
                    self.log.warning('Measurement already running. "start_scanning" call ignored.')
                else:
                    self.module_state.lock()
                    try:
                        # Reset data buffers
                        self.__init_data_buffers()
                        # Start laser scan and stream acquisition
                        if laser is not None:
                            if laser.module_state() == 'idle':
                                laser.start_scan()
                        if streamer.module_state() == 'idle':
                            streamer.start_stream()
                    except Exception:
                        self.module_state.unlock()
                        raise
                    # Kick-off measurement loop
                    self._sigNextDataFrame.emit()
            finally:
                self.sigStatusChanged.emit(self.module_state() == 'locked')

    @QtCore.Slot()
    def stop_scanning(self):
        """ Send a request to stop counting. """
        with self._threadlock:
            try:
                self._stop_scanning()
            finally:
                self.sigStatusChanged.emit(self.module_state() == 'locked')

    def _stop_scanning(self):
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

    def get_max_wavelength(self):
        """ Current maximum wavelength of the scan.
            @return float: current maximum wavelength
        """
        return self._xmax

    def get_min_wavelength(self):
        """ Current minimum wavelength of the scan.
            @return float: current minimum wavelength
        """
        return self._xmin

    def get_bins(self):
        """ Current number of bins in the spectrum.
            @return int: current number of bins in the scan
        """
        return self._bins

    def recalculate_histogram(self, bins=None, xmin=None, xmax=None):
        """ Recalculate the current spectrum from raw data.
            @praram int bins: new number of bins
            @param float xmin: new minimum wavelength
            @param float xmax: new maximum wavelength
        """
        if bins is not None:
            self._bins = bins
        if xmin is not None:
            self._xmin_histo = xmin
        if xmax is not None:
            self._xmax_histo = xmax

        # create a new x axis from xmin to xmax with bins points
        self.rawhisto = np.zeros(self._bins)
        self.envelope_histogram = np.zeros(self._bins)
        self.sumhisto = np.ones(self._bins) * 1.0e-10
        self.histogram_axis = np.linspace(self._xmin_histo, self._xmax_histo, self._bins)
        self.complete_histogram = True
        if not self.module_state() == 'locked':
            self._update_histogram(self.complete_histogram)
        return

    def display_current_wavelength(self) -> None:
        try:
            current_wavelength, trash_time = self._streamer().read_single_point()
            # netobtain due to remote connection
            current_wavelength = netobtain(current_wavelength)
            if len(current_wavelength) > 0:
                current_freq = constants.speed_of_light / current_wavelength  # in Hz
                self.sigNewWavelength2.emit(current_wavelength[0], current_freq[0])
            # display data at a rate of 10Hz
            time.sleep(0.1)
            self._sigNextWavelength.emit()
        except Exception as e:
            self.log.warning(f'Reading data from streamer went wrong: {e}')
            self.sigStatusChangedDisplaying.emit()
            return

    def start_displaying_current_wavelength(self):
        if len(self._streamer().active_channels) != 1:
            self.log.warning('Only a single wavemeter channel supported.')
            self.sigStatusChanged.emit(False)
            return -1

        constraints = self.streamer_constraints
        unit = constraints.channel_units[self._streamer().active_channels[0]]
        if unit != 'm':
            self.log.warning('Make sure acquisition unit is m!')
            self.sigStatusChanged.emit(False)
            return -1

        self._streamer().start_stream()
        self._sigNextWavelength.connect(self.display_current_wavelength, QtCore.Qt.QueuedConnection)
        self._is_wavelength_displaying = True
        self._sigNextWavelength.emit()
        return 0

    def stop_displaying_current_wavelength(self) -> None:
        self._sigNextWavelength.disconnect()
        self._is_wavelength_displaying = False
        self._streamer().stop_stream()
        # TODO

    def autoscale_histogram(self):
        self._xmax_histo = self._xmax
        self._xmin_histo = self._xmin
        self.recalculate_histogram(self._bins, self._xmin_histo, self._xmax_histo)
        return

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

    def get_fit_container(self) -> FitContainer:
        # with self._threadlock:
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
