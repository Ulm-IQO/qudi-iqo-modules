
from PySide2 import QtCore
import numpy as np
import datetime as dt
#import matplotlib as mpl
import matplotlib.pyplot as plt

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import StreamChannelType, StreamingMode


class WavemeterLogic(LogicBase):


    # declare signals
    sigDataChanged = QtCore.Signal(object)
    sig_new_data_points = QtCore.Signal(object, object)
    sigStatusChanged = QtCore.Signal(bool) #TODO 2nd boolean for recording
    sigSettingsChanged = QtCore.Signal(dict)
    _sigNextDataFrameWavelength = QtCore.Signal(bool)  # internal signal #now with boolean if complete histogram to be calculated or only new data

    # declare connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')
    #access on timeseries logic and thus nidaq instreamer
    _timeserieslogic = Connector(name='counterlogic', interface='TimeSeriesReaderLogic')

    #Adopted (unnecessary) from time series reader logic
    #config options
    _max_frame_rate = ConfigOption('max_frame_rate', default=10, missing='warn')
    _calc_digital_freq = ConfigOption('calc_digital_freq', default=True, missing='warn')
    # status vars
    _trace_window_size = StatusVar('trace_window_size', default=6)
    _moving_average_width = StatusVar('moving_average_width', default=9)
    _oversampling_factor = StatusVar('oversampling_factor', default=1)
    _data_rate = StatusVar('data_rate', default=50)
    _active_channels = StatusVar('active_channels', default=None)

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self.threadlock = Mutex()
        self._samples_per_frame = None
        self._stop_requested = True
        self.start_time_bool = True
        self.start_time = None

        # Data arrays
        self._trace_data = np.empty((2,0), dtype=np.float64)

        #####Old Core#####
        self._bins = 200
        self._data_index = 0
        self._xmin = 700
        self._xmax = 750

        # for data recording
        #self._recorded_data = None
        #self._data_recording_active = False
        #self._record_start_time = None
        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Temp reference to connected hardware module
        streamer = self._streamer()

        # Flag to stop the loop and process variables
        self._stop_requested = True
        self._data_recording_active = False
        self._record_start_time = None
        self.start_time_bool = True

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
        settings['data_rate'] = self._data_rate
        self.configure_settings(**settings)

        # create a new x axis from xmin to xmax with bins points
        self.histogram_axis = np.arange(
            self._xmin,
            self._xmax,
            (self._xmax - self._xmin) / self._bins
        )
        self.histogram = np.zeros(self.histogram_axis.shape)
        #self.envelope_histogram = np.zeros(self.histogram_axis.shape)
        self.rawhisto = np.zeros(self._bins)
        # self.envelope_histogram = np.zeros(self._bins)
        self.sumhisto = np.ones(self._bins) * 1.0e-10

        # set up internal frame loop connection
        self._sigNextDataFrameWavelength.connect(self._counts_and_wavelength, QtCore.Qt.QueuedConnection)

        return

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
                """
        # Stop measurement
        if self.module_state() == 'locked':
            self._stop_reader_wait()

        self._sigNextDataFrameWavelength.disconnect()
        #self._time_series_logic.sigDataChanged.disconnect()

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

    @QtCore.Slot()
    def _counts_and_wavelength(self, complete_histogram):
        """
                This method gets the available data from both hardwares.

                It runs repeatedly by being connected to a QTimer timeout signal.
                """
        with self.threadlock:
            if self.module_state() == 'locked':
                # check for break condition
                if self._stop_requested:
                    # terminate the hardware streaming for counts and wavelength #TODO
                    if self._streamer().stop_stream() < 0:
                        self.log.error(
                            'Error while trying to stop streaming device data acquisition (wavemeter).')
                    self.module_state.unlock()
                    self.sigStatusChanged.emit(False)
                    return
                #set the integer samples_to_read to the according value of count instreamer
                #and interpolate the wavelength values accordingly
                #TODO is the timing of data aquisition below synchronised
                #TODO what is max() exactly needed for
                samples_to_read_counts = (self._time_series_logic._streamer().available_samples // self._time_series_logic._oversampling_factor) * self._time_series_logic._oversampling_factor#,
                    #self._time_series_logic._samples_per_frame * self._time_series_logic._oversampling_factor)

                if samples_to_read_counts < 1:
                    self._sigNextDataFrameWavelength.emit(complete_histogram)
                    return

                # read the current counts values
                _data_counts = self._time_series_logic._streamer().read_data(
                    number_of_samples=samples_to_read_counts)
                # TODO be aware of digital counter channels
                # read the current wavemeter values (until at least one value available!)
                _data_wavelength = self._streamer().read_data(number_of_samples=samples_to_read_counts)

                if _data_wavelength.shape[1] != samples_to_read_counts or _data_counts.shape[1] != samples_to_read_counts:
                    self.log.error('Reading data from streamer went wrong; '
                                   'killing the stream with next data frame.')
                    self._stop_requested = True
                    self._sigNextDataFrameWavelength.emit(complete_histogram)
                    return

                # Process data
                #[0] for wavelength only
                self._process_data_for_histogram(_data_wavelength[0], _data_counts[0])
                self._update_histogram(complete_histogram)

                #Emit update signal for Gui
                self.sigDataChanged.emit(self._trace_data)

                #let time start at 0s
                if self.start_time_bool:
                    self.start_time = _data_wavelength[1, 0]
                    self.start_time_bool = False

                #emit time wavelength signal for scatterplot; /1000 to get time in s
                self.sig_new_data_points.emit(list(_data_wavelength[0]), list((_data_wavelength[1] - self.start_time)/1000))

                #emit new signal for aquisition loop
                self._sigNextDataFrameWavelength.emit(False)
        return

    def _process_data_for_histogram(self, data_wavelength, data_counts):
        """Method for generating whole data set of wavelength and counts (already interpolated)"""
        #scale counts upon sample rate in order to display counts/s
        data_counts *= self._time_series_logic.sampling_rate
        temp = [list(data_wavelength), list(data_counts)]
        self._trace_data = np.append(self._trace_data, np.array(temp), axis=1)
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

        temp = self._trace_data.tolist()
        for i in temp[0][self._data_index:]:
            self._data_index += 1

            if i < self._xmin:
                self._xmin = i
                #check if new min wavelength
                continue
            if i > self._xmax:
                self._xmax = i
                # check if new max wavelength
                continue

            # calculate the bin the new wavelength needs to go in
            newbin = np.digitize([i], self.histogram_axis)[0]

            # sum the counts in rawhisto and count the occurence of the bin in sumhisto
            self.rawhisto[newbin] += self._trace_data[1][self._data_index-1]
            self.sumhisto[newbin] += 1.0

            #self.envelope_histogram[newbin] = np.max([interpolation,
            #                                          self.envelope_histogram[newbin]
            #                                          ])
            #TODO missing code fragment old core

            # the plot data is the summed counts divided by the occurence of the respective bins
        self.histogram = self.rawhisto / self.sumhisto
        return

    @QtCore.Slot()
    def start_scanning(self):
        """
                Start data acquisition loop.

                @return error: 0 is OK, -1 is error
                """
        with self.threadlock:
            # Lock module
            if self.module_state() == 'locked':
                self.log.warning('Data acquisition already running. "start_scanning" call ignored.')
                self.sigStatusChanged.emit(True)
                return 0

            self.module_state.lock()
            self._stop_requested = False

            self.sigStatusChanged.emit(True)

            #start counterlogic aquisition loop
            if self._time_series_logic.module_state() == 'idle':
                if self._time_series_logic._streamer().start_stream() < 0:
                    self.log.error('Error while starting streaming device data acquisition.')
                    #self._stop_requested = True
                    #self._sigNextDataFrameWavelength.emit()
                    return -1

            #if self._data_recording_active:
            #    self._record_start_time = dt.datetime.now()
            #    self._recorded_data = list()

            if self._streamer().start_stream() < 0:
                self.log.error('Error while starting streaming device data acquisition.')
                self._stop_requested = True
                self._sigNextDataFrameWavelength.emit(False)
                return -1

            self._sigNextDataFrameWavelength.emit(False)
        return 0

    @QtCore.Slot()
    def stop_scanning(self):
        """
                Send a request to stop counting.

                @return int: error code (0: OK, -1: error)
                """
        with self.threadlock:
            #TODO
            #if self._time_series_logic.module_state() == 'locked':
            self._time_series_logic._streamer().stop_stream()
            if self.module_state() == 'locked':
                self._stop_requested = True
        return 0

    @property
    def all_settings(self):
        return {'oversampling_factor': self.oversampling_factor,
                'active_channels': self.active_channels,
                #'averaged_channels': self.averaged_channel_names,
                'moving_average_width': self.moving_average_width,
                'trace_window_size': self.trace_window_size,
                'data_rate': self.data_rate}

    @property
    def oversampling_factor(self):
        """

        @return int: Oversampling factor (always >= 1). Value of 1 means no oversampling.
        """
        return self._oversampling_factor

    @oversampling_factor.setter
    def oversampling_factor(self, val):
        """

        @param int val: The oversampling factor to set. Must be >= 1.
        """
        self.configure_settings(oversampling_factor=val)
        return

    @property
    def data_recording_active(self):
        return self._data_recording_active

    @property
    def sampling_rate(self):
        return self._streamer().sample_rate

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

    @property
    def trace_window_size(self):
        return self._trace_window_size

    @trace_window_size.setter
    def trace_window_size(self, val):
        self.configure_settings(trace_window_size=val)
        return

    @property
    def moving_average_width(self):
        return self._moving_average_width

    @moving_average_width.setter
    def moving_average_width(self, val):
        self.configure_settings(moving_average_width=val)
        return

    @QtCore.Slot(dict)
    def configure_settings(self, settings_dict=None, **kwargs):
        """
        Sets the number of samples to average per data point, i.e. the oversampling factor.
        The counter is stopped first and restarted afterwards.

        @param dict settings_dict: optional, dict containing all parameters to set. Entries will
                                   be overwritten by conflicting kwargs.

        @return dict: The currently configured settings
        """
        if self.data_recording_active:
            self.log.warning('Unable to configure settings while data is being recorded.')
            return self.all_settings

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
            constraints = self.streamer_constraints
            all_ch = tuple(ch.name for ch in self._streamer().available_channels)
            data_rate = self.data_rate
            active_ch = self.active_channel_names

            if 'oversampling_factor' in settings_dict:
                new_val = int(settings_dict['oversampling_factor'])
                if new_val < 1:
                    self.log.error('Oversampling factor must be integer value >= 1 '
                                   '(received: {0:d}).'.format(new_val))
                else:
                    if self.has_active_analog_channels and self.has_active_digital_channels:
                        min_val = constraints.combined_sample_rate.min
                        max_val = constraints.combined_sample_rate.max
                    elif self.has_active_analog_channels:
                        min_val = constraints.analog_sample_rate.min
                        max_val = constraints.analog_sample_rate.max
                    else:
                        min_val = constraints.digital_sample_rate.min
                        max_val = constraints.digital_sample_rate.max
                    if not (min_val <= (new_val * data_rate) <= max_val):
                        if 'data_rate' in settings_dict:
                            self._oversampling_factor = new_val
                        else:
                            self.log.error('Oversampling factor to set ({0:d}) would cause '
                                           'sampling rate outside allowed value range. '
                                           'Setting not changed.'.format(new_val))
                    else:
                        self._oversampling_factor = new_val

            if 'moving_average_width' in settings_dict:
                new_val = int(settings_dict['moving_average_width'])
                if new_val < 1:
                    self.log.error('Moving average width must be integer value >= 1 '
                                   '(received: {0:d}).'.format(new_val))
                elif new_val % 2 == 0:
                    new_val += 1
                    self.log.warning('Moving average window must be odd integer number in order to '
                                     'ensure perfect data alignment. Will increase value to {0:d}.'
                                     ''.format(new_val))
                if new_val / data_rate > self.trace_window_size:
                    if 'data_rate' in settings_dict or 'trace_window_size' in settings_dict:
                        self._moving_average_width = new_val
                        self.__moving_filter = np.full(shape=self.moving_average_width,
                                                       fill_value=1.0 / self.moving_average_width)
                    else:
                        self.log.warning('Moving average width to set ({0:d}) is smaller than the '
                                         'trace window size. Will adjust trace window size to '
                                         'match.'.format(new_val))
                        self._trace_window_size = float(new_val / data_rate)
                else:
                    self._moving_average_width = new_val
                    self.__moving_filter = np.full(shape=self.moving_average_width,
                                                   fill_value=1.0 / self.moving_average_width)

            if 'data_rate' in settings_dict:
                new_val = float(settings_dict['data_rate'])
                if new_val < 0:
                    self.log.error('Data rate must be float value > 0.')
                else:
                    if self.has_active_analog_channels and self.has_active_digital_channels:
                        min_val = constraints.combined_sample_rate.min
                        max_val = constraints.combined_sample_rate.max
                    elif self.has_active_analog_channels:
                        min_val = constraints.analog_sample_rate.min
                        max_val = constraints.analog_sample_rate.max
                    else:
                        min_val = constraints.digital_sample_rate.min
                        max_val = constraints.digital_sample_rate.max
                    sample_rate = new_val * self.oversampling_factor
                    if not (min_val <= sample_rate <= max_val):
                        self.log.warning('Data rate to set ({0:.3e}Hz) would cause sampling rate '
                                         'outside allowed value range. Will clip data rate to '
                                         'boundaries.'.format(new_val))
                        if sample_rate > max_val:
                            new_val = max_val / self.oversampling_factor
                        elif sample_rate < min_val:
                            new_val = min_val / self.oversampling_factor

                    data_rate = new_val
                    if self.moving_average_width / data_rate > self.trace_window_size:
                        if 'trace_window_size' not in settings_dict:
                            self.log.warning('Data rate to set ({0:.3e}Hz) would cause too few '
                                             'data points within the trace window. Adjusting window'
                                             ' size.'.format(new_val))
                            self._trace_window_size = self.moving_average_width / data_rate

            if 'trace_window_size' in settings_dict:
                new_val = float(settings_dict['trace_window_size'])
                if new_val < 0:
                    self.log.error('Trace window size must be float value > 0.')
                else:
                    # Round window to match data rate
                    data_points = int(round(new_val * data_rate))
                    new_val = data_points / data_rate
                    # Check if enough points are present
                    if data_points < self.moving_average_width:
                        self.log.warning('Requested trace_window_size ({0:.3e}s) would have too '
                                         'few points for moving average. Adjusting window size.'
                                         ''.format(new_val))
                        new_val = self.moving_average_width / data_rate
                    self._trace_window_size = new_val

            if 'active_channels' in settings_dict:
                new_val = tuple(settings_dict['active_channels'])
                if any(ch not in all_ch for ch in new_val):
                    self.log.error('Invalid channel found to set active.')
                else:
                    active_ch = new_val

            if 'averaged_channels' in settings_dict:
                new_val = tuple(ch for ch in settings_dict['averaged_channels'] if ch in active_ch)
                if any(ch not in all_ch for ch in new_val):
                    self.log.error('Invalid channel found to set activate moving average for.')
                else:
                    self._averaged_channels = new_val

            # Apply settings to hardware if needed
            self._streamer().configure(sample_rate=data_rate * self.oversampling_factor,
                                       streaming_mode=StreamingMode.CONTINUOUS,
                                       active_channels=active_ch,
                                       buffer_size=10000000,
                                       use_circular_buffer=True)

            # update actually set values
            #self._averaged_channels = tuple(
            #    ch for ch in self._averaged_channels if ch in self.active_channel_names)

            self._samples_per_frame = int(round(self.data_rate / self._max_frame_rate))
            #TODO self._init_data_arrays()
            settings = self.all_settings
            self.sigSettingsChanged.emit(settings)
            #if not restart:
            #    self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)
        #if restart:
            #self.start_reading()
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
            self._xmin = xmin
        if xmax is not None:
            self._xmax = xmax

        # create a new x axis from xmin to xmax with bins points
        self.rawhisto = np.zeros(self._bins)
        #self.envelope_histogram = np.zeros(self._bins)
        self.sumhisto = np.ones(self._bins) * 1.0e-10
        self.histogram_axis = np.linspace(self._xmin, self._xmax, self._bins)
        self._sigNextDataFrameWavelength.emit(True)
        return
