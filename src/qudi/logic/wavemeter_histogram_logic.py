
from PySide2 import QtCore
import numpy as np
import time
import matplotlib.pyplot as plt

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import StreamChannelType, StreamingMode
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
from qudi.core.statusvariable import StatusVar
from qudi.util.network import netobtain
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping
from lmfit.model import ModelResult as _ModelResult
from qudi.util.datastorage import TextDataStorage


class WavemeterLogic(LogicBase):

    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object, object, object, object)
    sigStatusChanged = QtCore.Signal(bool)
    sigSettingsChanged = QtCore.Signal(dict)
    sigNewWavelength2 = QtCore.Signal(object, object)
    sigFitChanged = QtCore.Signal(str, dict)  # fit_name, fit_results
    _sigNextWavelength = QtCore.Signal()  # internal signal

    # declare connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')
    #access on timeseries logic and thus nidaq instreamer
    _timeserieslogic = Connector(name='counterlogic', interface='TimeSeriesReaderLogic')

    #config options

    # status vars
    _active_channels = StatusVar('active_channels', default=None)

    _fit_config_model = StatusVar(name='fit_configs', default=list())

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self.threadlock = Mutex()
        self._stop_requested = True
        self.start_time_bool = True
        self.complete_histogram = False
        self.start_time = None
        self._fit_container = None
        self.x_axis_hz_bool = False  # by default display wavelength
        self._is_wavelength_displaying = False

        # Data arrays #timings, counts, wavelength, wavelength in Hz
        self._trace_data = np.empty((4, 0), dtype=np.float64)
        self.timings = []
        self.counts = []
        self.wavelength = []
        self.frequency = []

        self._bins = 200
        self._data_index = 0
        self._xmin_histo = 600
        self._xmax_histo = 750
        self._xmin = 100000 #default; to be changed upon first iteration
        self._xmax = -1
        self.number_of_displayed_points = 3000

        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Temp reference to connected hardware module
        streamer = self._streamer()

        # Flag to stop the loop and process variables
        self._stop_requested = True
        self._record_start_time = None
        self.start_time_bool = True
        self._fit_container = FitContainer(config_model=self._fit_config_model)
        self._last_fit_result = None

        # Check valid StatusVar
        # active channels

        avail_channels = tuple(ch.name for ch in streamer.available_channels)
        if self._active_channels is None:
            if streamer.active_channels:
                self._active_channels = tuple(ch.name for ch in streamer.active_channels)
            else:
                self._active_channels = avail_channels
        elif any(ch not in avail_channels for ch in self._active_channels):
            self.log.warning('Invalid active channels found in StatusVar. StatusVar ignored.')
            if streamer.active_channels:
                self._active_channels = tuple(ch.name for ch in streamer.active_channels)
            else:
                self._active_channels = avail_channels

        #
        self._time_series_logic = self._timeserieslogic()

        # set settings in streamer hardware
        settings = self.all_settings
        settings['active_channels'] = self._active_channels
        self.configure_settings(**settings)

        # create a new x axis from xmin to xmax with bins points
        self.histogram_axis = np.arange(
            self._xmin_histo,
            self._xmax_histo,
            (self._xmax_histo - self._xmin_histo) / self._bins
        )
        self.histogram = np.zeros(self.histogram_axis.shape)
        self.envelope_histogram = np.zeros(self.histogram_axis.shape)
        self.rawhisto = np.zeros(self._bins)
        self.sumhisto = np.ones(self._bins) * 1.0e-10

        #streamer.sigNewWavelength.connect(
        #    self.display_current_wavelength, QtCore.Qt.QueuedConnection)
        return

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
                """
        # Stop measurement
        if self.module_state() == 'locked':
            self._stop_reader_wait()
        #self._streamer().sigNewWavelength.disconnect()
        return

    @property
    def streamer_constraints(self):
        """
        Retrieve the hardware constrains from the counter device.

        @return SlowCounterConstraints: object with constraints for the counter
        """
        return self._streamer().get_constraints()

    @property
    def data_rate(self):
        return self.sampling_rate / self.oversampling_factor

    @data_rate.setter
    def data_rate(self, val):
        self.configure_settings(data_rate=val)
        return

    @QtCore.Slot(object)
    def _counts_and_wavelength(self, new_count_data):
        """
                This method gets the available data from both ◘.

                It runs repeatedly by being connected to a QTimer timeout signal from the time series (sigDataChangedWavemeter).
                """
        with self.threadlock: #TODO lock?
            if self.module_state() == 'locked':
                # check for break condition
                if self._stop_requested:
                    # terminate the hardware streaming for counts and wavelength #TODO
                    if self._streamer().stop_stream() < 0:
                        self.log.error(
                            'Error while trying to stop streaming device data acquisition (wavemeter).')
                    self.module_state.unlock()
                    self.sigStatusChanged.emit(False)
                    self._time_series_logic.sigDataChangedWavemeter.disconnect()
                    self._trace_data = np.vstack(((self.timings, self.counts), (self.wavelength, self.frequency)))
                    return
                #set the integer samples_to_read to the according value of count instreamer
                #and interpolate the wavelength values accordingly
                #TODO is the timing of data aquisition below synchronised

                # Determine samples to read according to new_count_data size (!one channel only!)
                if new_count_data is not None:
                    samples_to_read_counts = len(new_count_data[0])

                _data_wavelength = self._streamer().read_data(number_of_samples=samples_to_read_counts)
                _data_wavelength = netobtain(_data_wavelength)

                _data_counts = new_count_data

                if _data_wavelength.shape[1] != samples_to_read_counts or _data_counts.shape[1] != samples_to_read_counts:
                    self.log.error('Reading data from streamer went wrong; '
                                   'killing the stream with next data frame.')
                    self._stop_requested = True
                    return

                # Process data
                #let time start at 0s
                if self.start_time_bool:
                    self.start_time = _data_wavelength[1, 0]
                    self.start_time_bool = False

                #timings of wavelengths in s and start at 0s
                _data_wavelength_timings = (_data_wavelength[1] - self.start_time)/1000

                #[0] for wavelength(nm), [1] for timestamp
                self._process_data_for_histogram(_data_wavelength[0], _data_counts[0], _data_wavelength_timings)
                #Fail Save
                #if len(self.wavelength) > 500:
                #    self.save_data('AutomaticFailSaveOfMoreThan500Values')

                self._update_histogram(self.complete_histogram)

                #Emit update signal for Gui
                self.sigNewWavelength2.emit(self.wavelength[-1], self.frequency[-1])
                start = len(self.wavelength)-self.number_of_displayed_points if len(self.wavelength) > self.number_of_displayed_points else 0  # only display self.number_of_displayed_points most recent values
                self.sigDataChanged.emit(self.timings[start:], self.counts[start:],
                                         self.wavelength[start:], self.frequency[start:], self.histogram_axis,
                                         self.histogram, self.envelope_histogram)

        return

    def _process_data_for_histogram(self, data_wavelength, data_counts, data_wavelength_timings):
        """Method for appending to to whole data set of wavelength and counts (already interpolated)"""

        data_freq = 3.0e17 / data_wavelength
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
        #reset data index
        if complete_histogram:
            self._data_index = 0
            self.complete_histogram = False

        #n+1-dimensional binning axis, to avoid empty bin when using np.digitize
        offset = (self.histogram_axis[1]-self.histogram_axis[0])/2
        binning_axis = np.linspace(self.histogram_axis[0]-offset, self.histogram_axis[-1]+offset, len(self.histogram_axis)+1)

        for i in self.wavelength[self._data_index:]:
            self._data_index += 1 #before because of continue

            if i < self._xmin:
                self._xmin = i
            if i > self._xmax:
                self._xmax = i
            if i < self._xmin_histo or i > self._xmax_histo:
                continue

            # calculate the bin the new wavelength needs to go in
            newbin = np.digitize([i], binning_axis)[0]

            # sum the counts in rawhisto and count the occurence of the bin in sumhisto
            self.rawhisto[newbin-1] += self.counts[self._data_index-1]
            self.sumhisto[newbin-1] += 1.0

            self.envelope_histogram[newbin-1] = np.max([self.counts[self._data_index-1], self.envelope_histogram[newbin-1]])

        # the plot data is the summed counts divided by the occurence of the respective bins
        self.histogram = self.rawhisto / self.sumhisto
        return

    @QtCore.Slot()
    def start_scanning(self):
        """
                Start data acquisition loop.
                @return error: 0 is OK, -1 is error
                """
        #with self.threadlock:
        # Lock module
        if self.module_state() == 'locked':
            self.log.warning('Data acquisition already running. "start_scanning" call ignored.')
            self.sigStatusChanged.emit(True)
            return 0

        if not self._time_series_logic.module_state() == 'locked':
            self.log.warning('Time series data acquisition has to be running!')
            self.sigStatusChanged.emit(False)
            return 0

        self.module_state.lock()
        self._stop_requested = False
        self.sigStatusChanged.emit(True)
        if self._is_wavelength_displaying:
            self.stop_displaying_current_wavelength()
        self._time_series_logic.sigDataChangedWavemeter.connect(
            self._counts_and_wavelength, QtCore.Qt.QueuedConnection)

        if self._streamer().start_stream() < 0: #TODO start stream never returns -1
            self.log.error('Error while starting streaming device data acquisition.')
            self._stop_requested = True
            return -1

        return 0

    @QtCore.Slot()
    def stop_scanning(self):
        """
                Send a request to stop counting.

                @return int: error code (0: OK, -1: error)
                """
        if self.module_state() == 'locked':
            self._stop_requested = True
        return 0

    @property
    def all_settings(self):
        return {'active_channels': self.active_channels}

    @property
    def available_channels(self):
        return self._streamer().available_channels

    @property
    def active_channels(self):
        return self._streamer().active_channels

    @property
    def active_channel_names(self):
        return tuple(ch.name for ch in self._streamer().active_channels)

    @property
    def active_channel_units(self):
        unit_dict = dict()
        for ch in self._streamer().active_channels:
            if self._calc_digital_freq and ch.type == StreamChannelType.DIGITAL:
                unit_dict[ch.name] = 'Hz'
            else:
                unit_dict[ch.name] = ch.unit
        return unit_dict

    @property
    def active_channel_types(self):
        return {ch.name: ch.type for ch in self._streamer().active_channels}

    @property
    def has_active_analog_channels(self):
        return any(ch.type == StreamChannelType.ANALOG for ch in self._streamer().active_channels)

    @property
    def has_active_digital_channels(self):
        return any(ch.type == StreamChannelType.DIGITAL for ch in self._streamer().active_channels)

    @property
    def number_of_active_channels(self):
        return self._streamer().number_of_channels

    #TODO modify this method as most of it is just copied from time series logic and redundant for wavemeter
    @QtCore.Slot(dict)
    def configure_settings(self, settings_dict=None, **kwargs):
        """
        Sets the number of samples to average per data point, i.e. the oversampling factor.
        The counter is stopped first and restarted afterwards.

        @param dict settings_dict: optional, dict containing all parameters to set. Entries will
                                   be overwritten by conflicting kwargs.

        @return dict: The currently configured settings
        """

        if settings_dict is None:
            settings_dict = kwargs
        else:
            settings_dict.update(kwargs)

        if not settings_dict:
            return self.all_settings

        # Flag indicating if the stream should be restarted
        restart = self.module_state() == 'locked'
        if restart:
            self._stop_reader_wait()

        with self.threadlock:
            all_ch = tuple(ch.name for ch in self._streamer().available_channels)

            active_ch = self.active_channel_names

            if 'active_channels' in settings_dict:
                new_val = tuple(settings_dict['active_channels'])
                if any(ch not in all_ch for ch in new_val):
                    self.log.error('Invalid channel found to set active.')
                else:
                    active_ch = new_val

            # Apply settings to hardware if needed
            self._streamer().configure(#sample_rate=data_rate * self.oversampling_factor,
                                       streaming_mode=StreamingMode.CONTINUOUS,
                                       active_channels=active_ch,
                                       buffer_size=10000000,
                                       use_circular_buffer=True)

            settings = self.all_settings
            self.sigSettingsChanged.emit(settings)

        return settings

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

    def display_current_wavelength(self):
        current_wavelength = self._streamer()._wavelength  #TODO use read_single_point for this?
        #netobtain due to remote connection
        current_wavelength = netobtain(current_wavelength)
        #current_wavelength = 750
        #print(current_wavelength[-1])
        if len(current_wavelength) > 0:
            current_freq = 3.0e17 / current_wavelength[-1][1] #in Hz
            self.sigNewWavelength2.emit(current_wavelength[-1][1], current_freq)
        self._sigNextWavelength.emit()
        return

    def start_displaying_current_wavelength(self):
        self._sigNextWavelength.connect(self.display_current_wavelength, QtCore.Qt.QueuedConnection)
        self._is_wavelength_displaying = True
        self._sigNextWavelength.emit()
        return

    def stop_displaying_current_wavelength(self):
        self._sigNextWavelength.disconnect()
        self._is_wavelength_displaying = False
        return

    def autoscale_histogram(self):
        self._xmax_histo = self._xmax
        self._xmin_histo = self._xmin
        self.recalculate_histogram(self._bins, self._xmin_histo, self._xmax_histo)
        return

    def _stop_reader_wait(self):
        """
        Stops the counter if still running when deactivate module

        @return: error code
        """
        with self.threadlock:
            self._stop_requested = True
            # terminate the hardware streaming
            if self._streamer().stop_stream() < 0:
                self.log.error(
                    'Error while trying to stop streaming device data acquisition.')

            self.module_state.unlock()
            self.sigStatusChanged.emit(False)
        return 0

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
        #with self.threadlock:
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

        #fit the histogram
        if not self.x_axis_hz_bool:
            fit_results = fit_container.fit_data(fit_config=fit_config, x=self.histogram_axis,
                                             data=self.histogram)[1]
        elif self.x_axis_hz_bool:
            fit_results = fit_container.fit_data(fit_config=fit_config, x=3.0e17 / self.histogram_axis,
                                                 data=self.histogram)[1]
        fit_config = fit_container._last_fit_config
        self._last_fit_result = fit_results

        fit_container.sigLastFitResultChanged.emit(fit_config, fit_results)
        self.sigFitChanged.emit(fit_config, fit_results)
        return fit_results

    def get_fit_results(self) -> Tuple[str, Dict[str, Union[None, _ModelResult]]]:
        #with self.threadlock:
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

        # Fill array with less values with Nans
        if len(self._trace_data[0]) > len(self.histogram):
            temp = np.full(len(self._trace_data[0])-len(self.histogram), np.nan)
            temp3 = np.append(self.envelope_histogram, temp)
            temp2 = np.append(self.histogram, temp)
            temp1 = np.append(self.histogram_axis, temp)
            data_set = np.vstack((self._trace_data, temp1))
            data_set = np.vstack((data_set, temp2))
            data_set = np.vstack((data_set, temp3))

        if len(self._trace_data[0]) < len(self.histogram):
            temp = np.full((4, len(self.histogram)-len(self._trace_data[0])), np.nan)
            temp1 = np.append(self._trace_data, temp, axis=1)
            data_set = np.vstack((temp1, self.histogram_axis))
            data_set = np.vstack((data_set, self.histogram))
            data_set = np.vstack((data_set, self.envelope_histogram))

        #TODO set parameters
        parameters = {'Number of Bins '                  : self.get_bins(),
                      'Min Wavelength Of Histogram (nm) ': self._xmin_histo,
                      'Max Wavelength Of Histogram (nm) ': self._xmax_histo,
                      'FitResults': fit_container.formatted_result(self._last_fit_result)}

        file_label = postfix if postfix else 'qdLaserScanning'

        header = ['Timings (s)', 'Flourescence (counts/s)', 'Wavelength (nm)', 'Frequency (Hz)', 'HistogramX', 'HistogramY', 'HistogramEnvelope']

        ds = TextDataStorage(
            root_dir=self.module_default_data_dir if root_dir is None else root_dir,
            column_formats='.15e',
            include_global_metadata=True)
        file_path, _, _ = ds.save_data(data_set.T,
                                       metadata=parameters,
                                       column_headers=header,
                                       column_dtypes=[float] * len(header),
                                       nametag=file_label)

        # plot graph and save as image alongside data file
        fig = self._plot_figure(self.x_axis_hz_bool, self.get_fit_results(), data_set, fit_container)
        ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])

        self.log.debug(f'Data saved to: {file_path}')
        return file_path

    @staticmethod
    def _plot_figure(x_axis_bool, fitting, data_set,
                     fit_container: FitContainer) -> plt.Figure:

        fit_config, fit_results = fitting

        # High-resolution fit data and formatted result string
        if fit_results is not None:
            fit_data = fit_results.high_res_best_fit
            fit_result_str = fit_container.formatted_result(fit_results)
        else:
            fit_data = None
            fit_result_str = False
        #TODO and label
        #if not fit_data:
        #    fit_data = [None] * len(data_set)

        fig, ax1 = plt.subplots()

        if not x_axis_bool:
            ax1.plot(data_set[2],
                     data_set[1],
                     linestyle=':',
                     linewidth=1,
                     label='RawData')
            ax1.plot(data_set[4],
                     data_set[5],
                     color='y',
                     label='Histogram')
            ax1.plot(data_set[4],
                     data_set[6],
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
            ax1.plot(3.0e17/data_set[4],
                     data_set[5],
                     color='y',
                     label='Histogram')
            ax1.plot(3.0e17 / data_set[4],
                     data_set[6],
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
            ax1.set_xlabel('Wavelength (nm)')
        elif x_axis_bool:
            ax1.set_xlabel('Frequency (Hz)')

        ax1.set_ylabel('Flourescence (counts/s)')

        ax1.legend()

        fig.tight_layout()
        return fig
