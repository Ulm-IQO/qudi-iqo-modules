# -*- coding: utf-8 -*-
"""
This file contains the qudi logic to continuously read data from a streaming device as time series.

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
import datetime as dt
# import matplotlib.pyplot as plt

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import StreamingMode


class TimeSeriesReaderLogic(LogicBase):
    """
    This logic module gathers data from a hardware streaming device.

    Example config for copy-paste:

    time_series_reader_logic:
        module.Class: 'time_series_reader_logic.TimeSeriesReaderLogic'
        options:
            max_frame_rate: 10  # optional (10Hz by default)
            calc_digital_freq: True  # optional (True by default)
        connect:
            _streamer_con: <streamer_name>
            _savelogic_con: <save_logic_name>
    """
    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object)
    sigStatusChanged = QtCore.Signal(bool, bool)
    sigSettingsChanged = QtCore.Signal(dict)
    _sigNextDataFrame = QtCore.Signal()  # internal signal

    # declare connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')

    # config options
    _max_frame_rate = ConfigOption('max_frame_rate', default=10, missing='warn')

    # status vars
    _trace_window_size = StatusVar('trace_window_size', default=6)
    _moving_average_width = StatusVar('moving_average_width', default=9)
    _oversampling_factor = StatusVar('oversampling_factor', default=1)
    _data_rate = StatusVar('data_rate', default=50)
    _active_channels = StatusVar('active_channels', default=None)
    _averaged_channels = StatusVar('averaged_channels', default=None)

    def __init__(self, *args, **kwargs):
        """
        """
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._threadlock = Mutex()
        self._samples_per_frame = None

        # Data arrays
        self._trace_data = None
        self._trace_times = None
        self._trace_data_averaged = None
        self.__moving_filter = None

        # for data recording
        self._recorded_data = None
        self._data_recording_active = False
        self._record_start_time = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Temp reference to connected hardware module
        streamer = self._streamer()

        # Flag to stop the loop and process variables
        self._data_recording_active = False
        self._record_start_time = None

        # Check valid StatusVar
        # active channels
        avail_channels = self.available_channels
        if self._active_channels is None:
            if streamer.active_channels:
                self._active_channels = tuple(streamer.active_channels)
            else:
                self._active_channels = avail_channels
        elif any(ch not in avail_channels for ch in self._active_channels):
            self.log.warning('Invalid active channels found in StatusVar. StatusVar ignored.')
            if streamer.active_channels:
                self._active_channels = tuple(streamer.active_channels)
            else:
                self._active_channels = avail_channels

        # averaged channels
        if self._averaged_channels is None:
            self._averaged_channels = self._active_channels
        else:
            self._averaged_channels = tuple(
                ch for ch in self._averaged_channels if ch in self._active_channels
            )

        # Check for odd moving averaging window
        if self._moving_average_width % 2 == 0:
            self.log.warning('Moving average width ConfigOption must be odd integer number. '
                             'Changing value from {0:d} to {1:d}.'
                             ''.format(self._moving_average_width, self._moving_average_width + 1))
            self._moving_average_width += 1

        # set settings in streamer hardware
        settings = self.all_settings
        settings['active_channels'] = self._active_channels
        settings['data_rate'] = self._data_rate
        self.configure_settings(settings)

        # set up internal frame loop connection
        self._sigNextDataFrame.connect(self.acquire_data_block, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
        """
        try:
            self._sigNextDataFrame.disconnect()
            # Stop measurement
            if self.module_state() == 'locked':
                self._stop()
        finally:
            # Save status vars
            self._active_channels = self.active_channel_names
            self._data_rate = self.data_rate

    def _init_data_arrays(self):
        window_size = self.trace_window_size_samples
        self._trace_data = np.zeros(
            [self.number_of_active_channels, window_size + self._moving_average_width // 2],
            dtype=self.streamer_constraints.data_type
        )
        self._trace_data_averaged = np.zeros(
            [len(self._averaged_channels), window_size - self._moving_average_width // 2],
            dtype=self.streamer_constraints.data_type
        )
        self._trace_times = np.arange(window_size, dtype=np.float64) / self.data_rate
        self._recorded_data = list()

    @property
    def trace_window_size_samples(self):
        return int(round(self._trace_window_size * self.data_rate))

    @property
    def streamer_constraints(self):
        """ Retrieve the hardware constrains from the counter device """
        return self._streamer().constraints

    @property
    def data_rate(self):
        return self.sampling_rate / self.oversampling_factor

    @data_rate.setter
    def data_rate(self, val):
        self.configure_settings(data_rate=val)

    @property
    def trace_window_size(self):
        return self._trace_window_size

    @trace_window_size.setter
    def trace_window_size(self, val):
        self.configure_settings(trace_window_size=val)

    @property
    def moving_average_width(self):
        return self._moving_average_width

    @moving_average_width.setter
    def moving_average_width(self, val):
        self.configure_settings(moving_average_width=val)

    @property
    def data_recording_active(self):
        return self._data_recording_active

    @property
    def oversampling_factor(self):
        return self._oversampling_factor

    @oversampling_factor.setter
    def oversampling_factor(self, val):
        self.configure_settings(oversampling_factor=val)

    @property
    def sampling_rate(self):
        return self._streamer().sample_rate

    @property
    def available_channels(self):
        return tuple(self.streamer_constraints.channel_units)

    @property
    def active_channel_names(self):
        return tuple(self._streamer().active_channels)

    @property
    def active_channel_units(self):
        streamer = self._streamer()
        active_channels = streamer.active_channels
        channel_units = streamer.constraints.channel_units
        return {ch: unit for ch, unit in channel_units.items() if ch in active_channels}

    @property
    def averaged_channel_names(self):
        return self._averaged_channels

    @property
    def number_of_active_channels(self):
        return len(self.active_channel_names)

    @property
    def trace_data(self):
        data_offset = self._trace_data.shape[1] - self._moving_average_width // 2
        data = {ch: self._trace_data[i, :data_offset] for i, ch in
                enumerate(self.active_channel_names)}
        return self._trace_times, data

    @property
    def averaged_trace_data(self):
        if not self.averaged_channel_names or self.moving_average_width <= 1:
            return None, None
        data = {ch: self._trace_data_averaged[i] for i, ch in
                enumerate(self.averaged_channel_names)}
        return self._trace_times[-self._trace_data_averaged.shape[1]:], data

    @property
    def all_settings(self):
        return {'oversampling_factor': self.oversampling_factor,
                'active_channels': self.active_channel_names,
                'averaged_channels': self.averaged_channel_names,
                'moving_average_width': self.moving_average_width,
                'trace_window_size': self.trace_window_size,
                'data_rate': self.data_rate}

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
            return

        # Flag indicating if the stream should be restarted
        restart = self.module_state() == 'locked'
        if restart:
            self._stop_reader_wait()

        try:
            # Refine and sanity check settings
            settings = self.all_settings
            if settings_dict is not None:
                settings.update(settings_dict)
            settings.update(kwargs)
            settings['oversampling_factor'] = int(settings['oversampling_factor'])
            settings['moving_average_width'] = int(settings_dict['moving_average_width'])
            settings_dict['data_rate'] = float(settings_dict['data_rate'])
            settings_dict['trace_window_size'] = int(round(settings_dict['trace_window_size'] * settings_dict['data_rate'])) / settings_dict['data_rate']
            settings_dict['active_channels'] = tuple(settings_dict['active_channels'])
            settings_dict['averaged_channels'] = tuple(
                ch for ch in settings_dict['averaged_channels'] if
                ch in settings_dict['active_channels']
            )

            if settings['oversampling_factor'] < 1:
                raise ValueError(f'Oversampling factor must be integer value >= 1 '
                                 f'(received: {settings["oversampling_factor"]:d})')

            if settings['moving_average_width'] < 1:
                raise ValueError(f'Moving average width must be integer value >= 1 '
                                 f'(received: {settings["moving_average_width"]:d})')
            if settings['moving_average_width'] % 2 == 0:
                settings['moving_average_width'] += 1
                self.log.warning(f'Moving average window must be odd integer number in order to ensure '
                                 f'perfect data alignment. Increased value to '
                                 f'{settings["moving_average_width"]:d}.')
            if settings['moving_average_width'] / settings['data_rate'] > settings['trace_window_size']:
                self.log.warning(f'Moving average width ({settings["moving_average_width"]:d}) is '
                                 f'smaller than the trace window size. Will adjust trace window size '
                                 f'to match.')
                settings['trace_window_size'] = float(
                    settings['moving_average_width'] / settings['data_rate']
                )

            with self._threadlock:
                # Apply settings to hardware if needed
                self._streamer().configure(
                    active_channels=settings_dict['active_channels'],
                    streaming_mode=StreamingMode.CONTINUOUS,
                    channel_buffer_size=1024**2,
                    sample_rate=settings_dict['data_rate'] * settings['oversampling_factor']
                )
                # update actually set values
                self._oversampling_factor = settings['oversampling_factor']
                self._moving_average_width = settings['moving_average_width']
                self._trace_window_size = settings_dict['trace_window_size']
                self._averaged_channels = tuple(ch for ch in settings_dict['averaged_channels'] if
                                                ch in self.active_channel_names)
                self.__moving_filter = np.full(shape=self._moving_average_width,
                                               fill_value=1.0 / self._moving_average_width)
                self._samples_per_frame = int(round(self.data_rate / self._max_frame_rate))
                self._init_data_arrays()
        except:
            self.log.exception('Error while trying to configure new settings:')
            raise
        finally:
                self.sigSettingsChanged.emit(self.all_settings)
                if restart:
                    self.start_reading()
                else:
                    self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)

    @QtCore.Slot()
    def start_reading(self):
        """ Start data acquisition loop """
        with self._threadlock:
            # Lock module
            if self.module_state() == 'locked':
                self.log.warning('Data acquisition already running. "start_reading" call ignored.')
                self.sigStatusChanged.emit(True, self._data_recording_active)
                return

            self.module_state.lock()
            try:
                if self._data_recording_active:
                    self._record_start_time = dt.datetime.now()
                    self._recorded_data = list()
                self._streamer().start_stream()
            except:
                self.module_state.unlock()
                self.log.exception('Error while starting stream reader:')
                raise
            finally:
                self._sigNextDataFrame.emit()
                self.sigStatusChanged.emit(self.module_state() == 'locked',
                                           self._data_recording_active)

    @QtCore.Slot()
    def stop_reading(self):
        """ Send a request to stop counting """
        with self._threadlock:
            self._stop()

    def _stop(self) -> None:
        if self.module_state() == 'locked':
            try:
                self._streamer().stop_stream()
                if self._data_recording_active:
                    self._save_recorded_data(to_file=True, save_figure=True)
                    self._recorded_data = list()
            except:
                self.log.exception('Error while trying to stop stream reader:')
                raise
            finally:
                self.module_state.unlock()
                self._data_recording_active = False
                self.sigStatusChanged.emit(False, False)

    @QtCore.Slot()
    def acquire_data_block(self):
        """ This method gets the available data from the hardware. It runs repeatedly by being
        connected to a QTimer timeout signal.
        """
        with self._threadlock:
            if self.module_state() == 'locked':
                try:
                    streamer = self._streamer()
                    samples_to_read = max(
                        (streamer.available_samples // self._oversampling_factor) * self._oversampling_factor,
                        self._samples_per_frame * self._oversampling_factor
                    )
                    if samples_to_read > 0:
                        # read the current counter values
                        data, timestamps = streamer.read_data(number_of_samples=samples_to_read)
                        if data.shape[1] != samples_to_read:
                            raise RuntimeError(
                                f'Number of samples read from stream ({data.shape[1]:d}) does not '
                                f'match requested number of samples ({samples_to_read:d})'
                            )
                        # Process data
                        self._process_trace_data(data)
                        # Emit update signal
                        self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)
                except:
                    self.log.exception('Reading data from streamer went wrong')
                    self._stop()
                    raise

                self._sigNextDataFrame.emit()

    def _process_trace_data(self, data):
        """
        Processes raw data from the streaming device
        """
        # Down-sample and average according to oversampling factor
        if self.oversampling_factor > 1:
            tmp = data.reshape((data.shape[0],
                                data.shape[1] // self.oversampling_factor,
                                self.oversampling_factor))
            data = np.mean(tmp, axis=2)

        # Append data to save if necessary
        if self._data_recording_active:
            self._recorded_data.append(data)

        data = data[:, -self._trace_data.shape[1]:]  # discard data outside time frame
        new_samples = data.shape[1]

        # Roll data array to have a continuously running time trace
        self._trace_data = np.roll(self._trace_data, -new_samples, axis=1)
        # Insert new data
        self._trace_data[:, -new_samples:] = data

        # Calculate moving average by using numpy.convolve with a normalized uniform filter
        if self.moving_average_width > 1 and self.averaged_channel_names:
            # Only convolve the new data and roll the previously calculated moving average
            self._trace_data_averaged = np.roll(self._trace_data_averaged, -new_samples, axis=1)
            offset = new_samples + len(self.__moving_filter) - 1
            for i, ch in enumerate(self.averaged_channel_names):
                data_index = self.active_channel_names.index(ch)
                self._trace_data_averaged[i, -new_samples:] = np.convolve(
                    self._trace_data[data_index, -offset:], self.__moving_filter, mode='valid')

    @QtCore.Slot()
    def start_recording(self):
        """ Sets up start-time and initializes data array, if not resuming, and changes saving
        state. If the counter is not running it will be started in order to have data to save.
        """
        with self._threadlock:
            if self._data_recording_active:
                self.sigStatusChanged.emit(self.module_state() == 'locked', True)
            else:
                self._data_recording_active = True
                if self.module_state() == 'locked':
                    self._recorded_data = list()
                    self._record_start_time = dt.datetime.now()
                    self.sigStatusChanged.emit(True, True)
                else:
                    self.start_reading()

    @QtCore.Slot()
    def stop_recording(self):
        """
        Stop the accumulative data recording and save data to file. Will not stop the data stream.
        Ignored if stream reading is inactive (module is in idle state).

        @return int: Error code (0: OK, -1: Error)
        """
        with self._threadlock:
            if self._data_recording_active:
                self._data_recording_active = False
                if self.module_state() == 'locked':
                    self._save_recorded_data(to_file=True, save_figure=True)
                    self._recorded_data = list()
                    self.sigStatusChanged.emit(True, False)
            else:
                self.sigStatusChanged.emit(self.module_state() == 'locked', False)

    def _save_recorded_data(self, to_file=True, name_tag='', save_figure=True):
        """ Save the counter trace data and writes it to a file.

        @param bool to_file: indicate, whether data have to be saved to file
        @param str name_tag: an additional tag, which will be added to the filename upon save
        @param bool save_figure: select whether png and pdf should be saved

        @return dict parameters: Dictionary which contains the saving parameters
        """
        pass
        # if not self._recorded_data:
        #     self.log.error('No data has been recorded. Save to file failed.')
        #     return np.empty(0), dict()
        #
        # data_arr = np.concatenate(self._recorded_data, axis=1)
        # if data_arr.size == 0:
        #     self.log.error('No data has been recorded. Save to file failed.')
        #     return np.empty(0), dict()
        #
        # saving_stop_time = self._record_start_time + dt.timedelta(
        #     seconds=data_arr.shape[1] * self.data_rate)
        #
        # # write the parameters:
        # parameters = dict()
        # parameters['Start recoding time'] = self._record_start_time.strftime(
        #     '%d.%m.%Y, %H:%M:%S.%f')
        # parameters['Stop recoding time'] = saving_stop_time.strftime('%d.%m.%Y, %H:%M:%S.%f')
        # parameters['Data rate (Hz)'] = self.data_rate
        # parameters['Oversampling factor (samples)'] = self.oversampling_factor
        # parameters['Sampling rate (Hz)'] = self.sampling_rate
        #
        # if to_file:
        #     # If there is a postfix then add separating underscore
        #     filelabel = 'data_trace_{0}'.format(name_tag) if name_tag else 'data_trace'
        #
        #     # prepare the data in a dict:
        #     header = ', '.join(
        #         '{0} ({1})'.format(ch, unit) for ch, unit in self.active_channel_units.items())
        #
        #     data = {header: data_arr.transpose()}
        #     filepath = self._savelogic.get_path_for_module(module_name='TimeSeriesReader')
        #     set_of_units = set(self.active_channel_units.values())
        #     unit_list = tuple(self.active_channel_units)
        #     y_unit = 'arb.u.'
        #     occurrences = 0
        #     for unit in set_of_units:
        #         count = unit_list.count(unit)
        #         if count > occurrences:
        #             occurrences = count
        #             y_unit = unit
        #
        #     fig = self._draw_figure(data_arr, self.data_rate, y_unit) if save_figure else None
        #
        #     self._savelogic.save_data(data=data,
        #                               filepath=filepath,
        #                               parameters=parameters,
        #                               filelabel=filelabel,
        #                               plotfig=fig,
        #                               delimiter='\t',
        #                               timestamp=saving_stop_time)
        #     self.log.info('Time series saved to: {0}'.format(filepath))
        # return data_arr, parameters

    def _draw_figure(self, data, timebase, y_unit):
        """ Draw figure to save with data file.

        @param: nparray data: a numpy array containing counts vs time for all detectors

        @return: fig fig: a matplotlib figure object to be saved to file.
        """
        pass
        # # Create figure and scale data
        # max_abs_value = ScaledFloat(max(data.max(), np.abs(data.min())))
        # time_data = np.arange(data.shape[1]) / timebase
        # fig, ax = plt.subplots()
        # if max_abs_value.scale:
        #     ax.plot(time_data,
        #             data.transpose() / max_abs_value.scale_val,
        #             linestyle=':',
        #             linewidth=0.5)
        # else:
        #     ax.plot(time_data, data.transpose(), linestyle=':', linewidth=0.5)
        # ax.set_xlabel('Time (s)')
        # ax.set_ylabel('Signal ({0}{1})'.format(max_abs_value.scale, y_unit))
        # return fig

    @QtCore.Slot()
    def save_trace_snapshot(self, to_file=True, name_tag='', save_figure=True):
        """
        The currently displayed data trace will be saved.

        @param bool to_file: optional, whether data should be saved to a text file
        @param str name_tag: optional, additional description that will be appended to the file name
        @param bool save_figure: optional, whether a data thumbnail figure should be saved

        @return dict, dict: Data which was saved, Experiment parameters

        This method saves the already displayed counts to file and does not accumulate them.
        """
        pass
        # with self._threadlock:
        #     timestamp = dt.datetime.now()
        #
        #     # write the parameters:
        #     parameters = dict()
        #     parameters['Time stamp'] = timestamp.strftime('%d.%m.%Y, %H:%M:%S.%f')
        #     parameters['Data rate (Hz)'] = self.data_rate
        #     parameters['Oversampling factor (samples)'] = self.oversampling_factor
        #     parameters['Sampling rate (Hz)'] = self.sampling_rate
        #
        #     header = ', '.join(
        #         '{0} ({1})'.format(ch, unit) for ch, unit in self.active_channel_units.items())
        #     data_offset = self._trace_data.shape[1] - self.moving_average_width // 2
        #     data = {header: self._trace_data[:, :data_offset].transpose()}
        #
        #     if to_file:
        #         filepath = self._savelogic.get_path_for_module(module_name='TimeSeriesReader')
        #         filelabel = 'data_trace_snapshot_{0}'.format(
        #             name_tag) if name_tag else 'data_trace_snapshot'
        #         self._savelogic.save_data(data=data,
        #                                   filepath=filepath,
        #                                   parameters=parameters,
        #                                   filelabel=filelabel,
        #                                   timestamp=timestamp,
        #                                   delimiter='\t')
        #         self.log.info('Time series snapshot saved to: {0}'.format(filepath))
        # return data, parameters
