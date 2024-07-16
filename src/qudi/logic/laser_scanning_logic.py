# -*- coding: utf-8 -*-
"""
This file contains the qudi logic to continuously read data from a wavemeter device and eventually interpolates the
 acquired data with the simultaneously obtained counts from a time_series_reader_logic. It is intended to be used in
 conjunction with the high_finesse_wavemeter.py.

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
import matplotlib.pyplot as plt
import scipy.interpolate as interpolate

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamConstraints
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
from qudi.core.statusvariable import StatusVar
from qudi.util.network import netobtain
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping
from lmfit.model import ModelResult as _ModelResult
from qudi.util.datastorage import TextDataStorage
from scipy import constants


class WavemeterLogic(LogicBase):
    """
    Example config for copy-paste:

    wavemeter_scanning_logic:
        module.Class: 'wavemeter_scanning_logic_3.WavemeterLogic'
        connect:
            streamer: wavemeter
            counterlogic: time_series_reader_logic
    """
    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object, object, object, object)
    sigStatusChanged = QtCore.Signal(bool)
    sigStatusChangedDisplaying = QtCore.Signal()
    sigNewWavelength2 = QtCore.Signal(object, object)
    sigFitChanged = QtCore.Signal(str, dict)  # fit_name, fit_results
    _sigNextWavelength = QtCore.Signal()  # internal signal

    # declare connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')
    # access on timeseries logic and thus nidaq instreamer
    _timeserieslogic = Connector(name='counterlogic', interface='TimeSeriesReaderLogic')

    # TODO status vars...
    # config options
    _fit_config_model = StatusVar(name='fit_configs', default=list())

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self.threadlock = Mutex()
        self.complete_histogram = False
        self._fit_container = None
        self.x_axis_hz_bool = False  # by default display wavelength
        self._is_wavelength_displaying = False
        self._delay_time = None
        self._stop_flag = False

        # Data arrays #timings, counts, wavelength, wavelength in Hz
        self._trace_data = np.empty((4, 0), dtype=np.float64)
        self.timings = []
        self.counts = []
        self.wavelength = []
        self.frequency = []

        self._bins = 200
        self._data_index = 0
        self._xmin_histo = 500.0e-9  # in SI units, default starting range
        self._xmax_histo = 750.0e-9
        self._xmin = 1  # default; to be changed upon first iteration
        self._xmax = -1
        self.number_of_displayed_points = 1000
        self.fit_histogram = True

        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._fit_container = FitContainer(config_model=self._fit_config_model)
        self._last_fit_result = None

        # connect to time series
        self._time_series_logic = self._timeserieslogic()

        # create a new x axis from xmin to xmax with bins points
        self.histogram_axis = np.linspace(self._xmin_histo, self._xmax_histo, self._bins)
        self.histogram = np.zeros(self._bins)
        self.envelope_histogram = np.zeros(self._bins)
        self.rawhisto = np.zeros(self._bins)
        self.sumhisto = np.ones(self._bins) * 1.0e-10

        self._time_series_logic.sigStopped.connect(
            self.stop_scanning, QtCore.Qt.QueuedConnection)
        return

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
                """
        # Stop measurement
        if self._is_wavelength_displaying:
            self.stop_displaying_current_wavelength()
        elif self.module_state() == 'locked':
            self.stop_scanning()

        self._time_series_logic.sigStopped.disconnect()
        return

    @property
    def streamer_constraints(self) -> DataInStreamConstraints:
        """ Retrieve the hardware constrains from the counter device """
        # netobtain is required if streamer is a remote module
        return netobtain(self._streamer().constraints)

    @QtCore.Slot(object)
    def _counts_and_wavelength(self, new_count_data=None, new_count_timings=None) -> None:
        """
                This method synchronizes (interpolates) the available data from both the timeseries logic and the wavemeter hardware module.
                It runs repeatedly by being connected to a QTimer timeout signal from the time series (sigNewRawData).
                Note: new_count_timing is currently unused, but might be used for a more elaborate synchronization.
                #TODO Assure that the timing below is synchronized at an acceptable level and saving of raw data
                """
        with self.threadlock:
            if self.module_state() == 'locked':
                # Determine samples to read according to new_count_data size (!one channel only!)
                if new_count_data is not None:
                    samples_to_read_counts = len(new_count_data)

                try:
                    n = self._streamer().available_samples if self._streamer().available_samples > 1 else 1

                    raw_data_wavelength, raw_timings = self._streamer().read_data(samples_per_channel=n)
                    raw_data_wavelength = netobtain(raw_data_wavelength)  # netobtain due to remote connection
                    raw_timings = netobtain(raw_timings)
                    # Above buffers as an option to save the raw wavemeter values
                    if self._delay_time:
                        raw_timings += self._delay_time
                    # Here the interpolation is performed to match the counts onto wavelength
                    if n == 1:
                        wavemeter_data, new_timings = np.ones(samples_to_read_counts) * raw_data_wavelength, np.ones(
                            samples_to_read_counts) * raw_timings
                    else:
                        arr_interp = interpolate.interp1d(raw_timings, raw_data_wavelength)
                        new_timings = np.linspace(raw_timings[0],
                                                  raw_timings[-1],
                                                  samples_to_read_counts) if samples_to_read_counts > 1 else np.ones(
                            1) * np.mean(raw_timings)
                        wavemeter_data = arr_interp(new_timings)

                    if len(wavemeter_data) != len(new_timings) != len(new_count_data) != samples_to_read_counts:
                        self.log.error('Reading data from streamers went wrong; '
                                       'stopping the stream.')
                        self.stop_scanning()
                        return

                    # Process data
                    self._process_data_for_histogram(wavemeter_data, new_count_data, new_timings)
                    self._update_histogram(self.complete_histogram)

                    # Emit update signal for Gui
                    self.sigNewWavelength2.emit(self.wavelength[-1], self.frequency[-1])
                    # only display self.number_of_displayed_points most recent values
                    start = len(self.wavelength) - self.number_of_displayed_points if len(
                        self.wavelength) > self.number_of_displayed_points else 0
                    self.sigDataChanged.emit(self.timings[start:], self.counts[start:],
                                             self.wavelength[start:], self.frequency[start:], self.histogram_axis,
                                             self.histogram, self.envelope_histogram)

                except TimeoutError as err:
                    self.log.warning(f'Timeout error: {err}')
                    self.stop_scanning()
                    return

                except Exception as e:
                    self.log.warning(f'Reading data from streamer went wrong: {e}')
                    self._time_series_logic.sigNewRawData.disconnect()
                    self._stop_cleanup()
                    return

    def _process_data_for_histogram(self, data_wavelength, data_counts, data_wavelength_timings):
        """Method for appending to whole data set of wavelength and counts (already interpolated)"""
        data_freq = constants.speed_of_light / data_wavelength
        for i in range(len(data_wavelength)):
            self.wavelength.append(data_wavelength[i])
            self.counts.append(data_counts[i])
            self.timings.append(data_wavelength_timings[i])
            self.frequency.append(data_freq[i])
        return

    def _update_histogram(self, complete_histogram):
        """ Calculate new points for the histogram.
        @param bool complete_histogram: should the complete histogram be recalculated, or just the
                                                most recent data?
        @return:
        """
        # reset data index
        if complete_histogram:
            self._data_index = 0
            self.complete_histogram = False

        # n+1-dimensional binning axis, to avoid empty bin when using np.digitize
        offset = (self.histogram_axis[1] - self.histogram_axis[0]) / 2
        binning_axis = np.linspace(self.histogram_axis[0] - offset, self.histogram_axis[-1] + offset,
                                   len(self.histogram_axis) + 1)

        for i in self.wavelength[self._data_index:]:
            self._data_index += 1  # before because of continue

            if i < self._xmin:
                self._xmin = i
            if i > self._xmax:
                self._xmax = i
            if i < self._xmin_histo or i > self._xmax_histo or np.isnan(i):
                continue

            # calculate the bin the new wavelength needs to go in
            newbin = np.digitize([i], binning_axis)[0]

            # sum the counts in rawhisto and count the occurence of the bin in sumhisto
            self.rawhisto[newbin - 1] += self.counts[self._data_index - 1]
            self.sumhisto[newbin - 1] += 1.0

            self.envelope_histogram[newbin - 1] = np.max(
                [self.counts[self._data_index - 1], self.envelope_histogram[newbin - 1]])

        # the plot data is the summed counts divided by the occurence of the respective bins
        self.histogram = self.rawhisto / self.sumhisto
        return

    @QtCore.Slot()
    def start_scanning(self):
        """
                Start data acquisition loop.
                """

        if self.module_state() == 'locked':
            self.log.warning('Data acquisition already running. "start_scanning" call ignored.')
            self.sigStatusChanged.emit(True)
            return

        if not self._time_series_logic.module_state() == 'locked':
            self.log.warning('Time series data acquisition has to be running!')
            self.sigStatusChanged.emit(False)
            return

        if not len(self._time_series_logic.active_channel_names) == 1:
            self.log.warning('Number of channels of time series data acquisition has to be 1!')
            self.sigStatusChanged.emit(False)
            return

        if len(self._streamer().active_channels) != 1:
            self.log.warning('Only a single wavemeter channel supported.')
            self.sigStatusChanged.emit(False)
            return

        constraints = self.streamer_constraints
        unit = constraints.channel_units[self._streamer().active_channels[0]]
        if unit != 'm':
            self.log.warning('Make sure acquisition unit is m!')
            self.sigStatusChanged.emit(False)
            return

        self.module_state.lock()

        self.sigStatusChanged.emit(True)

        if self._is_wavelength_displaying:
            self.stop_displaying_current_wavelength()

        self._time_series_logic.sigNewRawData.connect(
            self._counts_and_wavelength, QtCore.Qt.QueuedConnection)

        if not len(self.timings) == 0:
            self._delay_time = self.timings[-1] + time.time() - self._start
        self._streamer().start_stream()
        return 0

    @QtCore.Slot()
    def stop_scanning(self):
        """
                Send a request to stop counting.
                """
        if self.module_state() == 'locked':
            try:
                # disconnect to time series signal
                self._time_series_logic.sigNewRawData.disconnect()

                # terminate the hardware streaming device #TODO test with remote module
                self._streamer().stop_stream()
            except:
                self.log.exception('Error while trying to stop stream reader:')
                raise
            finally:
                self._stop_cleanup()
        return 0

    def _stop_cleanup(self) -> None:
        # save start_time information of high finesse wavemeter
        self.module_state.unlock()
        self._start = time.time()
        self.sigStatusChanged.emit(False)
        self._trace_data = np.vstack(((self.timings, self.counts), (self.wavelength, self.frequency)))

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
            self._stop_flag = True
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
        # with self.threadlock:
        return self._get_fit_container()

    def _get_fit_container(self) -> FitContainer:
        return self._fit_container

    def do_fit(self, fit_config: str) -> Dict[str, Union[None, _ModelResult]]:
        """ Perform desired fit

        @param str fit_config: name of the fit. Must match a fit configuration in fit_config_model.
        """
        with self.threadlock:
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
        # with self.threadlock:
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
        with self.threadlock:
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
