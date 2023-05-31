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

import time
import numpy as np
import datetime as dt
# import matplotlib.pyplot as plt
from PySide2 import QtCore

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.util.helpers import is_integer_type
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.util.datastorage import TextDataStorage


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
    sigNewRawData = QtCore.Signal(object, object)
    sigStatusChanged = QtCore.Signal(bool, bool)
    sigTraceSettingsChanged = QtCore.Signal(dict)
    sigChannelSettingsChanged = QtCore.Signal(list, list)
    _sigNextDataFrame = QtCore.Signal()  # internal signal

    # declare connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')

    # config options
    _max_frame_rate = ConfigOption('max_frame_rate', default=10, missing='warn')
    _channel_buffer_size = ConfigOption(name='channel_buffer_size',
                                        default=1024**2,
                                        missing='info',
                                        constructor=lambda x: int(round(x)))
    _max_raw_data_bytes = ConfigOption(name='max_raw_data_bytes',
                                       default=1024**3,
                                       missing='info',
                                       constructor=lambda x: int(round(x)))

    # status vars
    _trace_window_size = StatusVar('trace_window_size', default=6)
    _moving_average_width = StatusVar('moving_average_width', default=9)
    _oversampling_factor = StatusVar('oversampling_factor', default=1)
    _data_rate = StatusVar('data_rate', default=50)
    _active_channels = StatusVar('active_channels', default=None)
    _averaged_channels = StatusVar('averaged_channels', default=None)

    @_data_rate.representer
    def __repr_data_rate(self, value):
        return self.data_rate

    @_active_channels.representer
    def __repr_active_channels(self, value):
        return self.active_channel_names

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._threadlock = Mutex()
        self._samples_per_frame = None

        # Data arrays
        self._data_buffer = None
        self._times_buffer = None
        self._trace_data = None
        self._trace_times = None
        self._trace_data_averaged = None
        self.__moving_filter = None

        # for data recording
        self._recorded_raw_data = None
        self._recorded_raw_times = None
        self._recorded_sample_count = 0
        self._data_recording_active = False
        self._record_start_time = None

    def on_activate(self):
        """ Initialisation performed during activation of the module. """
        # Temp reference to connected hardware module
        streamer = self._streamer()

        # Flag to stop the loop and process variables
        self._recorded_raw_data = None
        self._recorded_raw_times = None
        self._recorded_sample_count = 0
        self._data_recording_active = False
        self._record_start_time = None

        # Check valid StatusVar
        # active channels
        avail_channels = tuple(streamer.constraints.channel_units)
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
        self.set_channel_settings(self._active_channels, self._averaged_channels)
        self.set_trace_settings(data_rate=self._data_rate)
        # set up internal frame loop connection
        self._sigNextDataFrame.connect(self._acquire_data_block, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
        """
        try:
            self._sigNextDataFrame.disconnect()
            # Stop measurement
            if self.module_state() == 'locked':
                self._stop()
        finally:
            # Free (potentially) large raw data buffers
            self._data_buffer = None
            self._times_buffer = None

    def _init_data_arrays(self):
        channel_count = len(self.active_channel_names)
        averaged_channel_count = len(self._averaged_channels)
        window_size = int(round(self._trace_window_size * self.data_rate))
        constraints = self.streamer_constraints
        trace_dtype = np.float64 if is_integer_type(constraints.data_type) else constraints.data_type

        # processed data arrays
        self._trace_data = np.zeros(
            [channel_count, window_size + self._moving_average_width // 2],
            dtype=trace_dtype
        )
        self._trace_data_averaged = np.zeros(
            [averaged_channel_count, window_size - self._moving_average_width // 2],
            dtype=trace_dtype
        )
        self._trace_times = np.arange(window_size, dtype=np.float64)
        if constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._trace_times -= window_size
        if constraints.sample_timing != SampleTiming.RANDOM:
            self._trace_times /= self.data_rate

        # raw data buffers
        self._data_buffer = np.zeros([channel_count, self._channel_buffer_size],
                                     dtype=constraints.data_type)
        if constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._times_buffer = np.zeros(self._channel_buffer_size, dtype=np.float64)
        else:
            self._times_buffer = None

    @property
    def streamer_constraints(self):
        """ Retrieve the hardware constrains from the counter device """
        return self._streamer().constraints

    @property
    def data_rate(self):
        return self.sampling_rate / self.oversampling_factor

    @data_rate.setter
    def data_rate(self, val):
        self.set_trace_settings(data_rate=val)

    @property
    def trace_window_size(self):
        return self._trace_window_size

    @trace_window_size.setter
    def trace_window_size(self, val):
        self.set_trace_settings(trace_window_size=val)

    @property
    def moving_average_width(self):
        return self._moving_average_width

    @moving_average_width.setter
    def moving_average_width(self, val):
        self.set_trace_settings(moving_average_width=val)

    @property
    def data_recording_active(self):
        return self._data_recording_active

    @property
    def oversampling_factor(self):
        return self._oversampling_factor

    @oversampling_factor.setter
    def oversampling_factor(self, val):
        self.set_trace_settings(oversampling_factor=val)

    @property
    def sampling_rate(self):
        return self._streamer().sample_rate

    @property
    def active_channel_names(self):
        return tuple(self._streamer().active_channels)

    @property
    def averaged_channel_names(self):
        return self._averaged_channels

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
    def trace_settings(self):
        return {'oversampling_factor' : self.oversampling_factor,
                'moving_average_width': self.moving_average_width,
                'trace_window_size'   : self.trace_window_size,
                'data_rate'           : self.data_rate}

    @property
    def channel_settings(self):
        return self.active_channel_names, self.averaged_channel_names

    @QtCore.Slot(dict)
    def set_trace_settings(self, settings_dict=None, **kwargs):
        """ Sets the number of samples to average per data point, i.e. the oversampling factor.
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
            self._stop()

        try:
            # Refine and sanity check settings
            settings = self.trace_settings
            if settings_dict is not None:
                settings.update(settings_dict)
            settings.update(kwargs)
            settings['oversampling_factor'] = int(settings['oversampling_factor'])
            settings['moving_average_width'] = int(settings['moving_average_width'])
            settings['data_rate'] = float(settings['data_rate'])
            settings['trace_window_size'] = int(round(settings['trace_window_size'] * settings['data_rate'])) / settings['data_rate']

            if settings['oversampling_factor'] < 1:
                raise ValueError(f'Oversampling factor must be integer value >= 1 '
                                 f'(received: {settings["oversampling_factor"]:d})')

            if settings['moving_average_width'] < 1:
                raise ValueError(f'Moving average width must be integer value >= 1 '
                                 f'(received: {settings["moving_average_width"]:d})')
            if settings['moving_average_width'] % 2 == 0:
                settings['moving_average_width'] += 1
                self.log.warning(f'Moving average window must be odd integer number in order to '
                                 f'ensure perfect data alignment. Increased value to '
                                 f'{settings["moving_average_width"]:d}.')
            if settings['moving_average_width'] / settings['data_rate'] > settings['trace_window_size']:
                self.log.warning(f'Moving average width ({settings["moving_average_width"]:d}) is '
                                 f'smaller than the trace window size. Will adjust trace window '
                                 f'size to match.')
                settings['trace_window_size'] = float(
                    settings['moving_average_width'] / settings['data_rate']
                )

            with self._threadlock:
                # Apply settings to hardware if needed
                self._streamer().configure(
                    active_channels=self.active_channel_names,
                    streaming_mode=StreamingMode.CONTINUOUS,
                    channel_buffer_size=1024**2,
                    sample_rate=settings['data_rate'] * settings['oversampling_factor']
                )
                # update actually set values
                self._oversampling_factor = settings['oversampling_factor']
                self._moving_average_width = settings['moving_average_width']
                self._trace_window_size = settings['trace_window_size']
                self.__moving_filter = np.full(shape=self._moving_average_width,
                                               fill_value=1.0 / self._moving_average_width)
                self._samples_per_frame = max(1, int(round(self.data_rate / self._max_frame_rate)))
                self._init_data_arrays()
        except:
            self.log.exception('Error while trying to configure new trace settings:')
            raise
        finally:
            self.sigTraceSettingsChanged.emit(self.trace_settings)
            if restart:
                self.start_reading()
            else:
                self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)

    def set_channel_settings(self, enabled, averaged):
        if self.data_recording_active:
            self.log.warning('Unable to configure settings while data is being recorded.')
            return

        # Flag indicating if the stream should be restarted
        restart = self.module_state() == 'locked'
        if restart:
            self._stop()

        try:
            self._streamer().configure(
                active_channels=enabled,
                streaming_mode=StreamingMode.CONTINUOUS,
                channel_buffer_size=1024 ** 2,
                sample_rate=self.sampling_rate
            )
            self._averaged_channels = tuple(ch for ch in averaged if ch in enabled)
            self._init_data_arrays()
        except:
            self.log.exception('Error while trying to configure new channel settings:')
            raise
        finally:
            self.sigChannelSettingsChanged.emit(*self.channel_settings)
            if restart:
                self.start_reading()
            else:
                self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)

    @QtCore.Slot()
    def start_reading(self):
        """ Start data acquisition loop """
        with self._threadlock:
            if self.module_state() == 'locked':
                self.log.warning('Data acquisition already running. "start_reading" call ignored.')
                self.sigStatusChanged.emit(True, self._data_recording_active)
                return

            self.module_state.lock()
            try:
                if self._data_recording_active:
                    self._init_recording_arrays()
                    self._record_start_time = dt.datetime.now()
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
            except:
                self.log.exception('Error while trying to stop stream reader:')
                raise
            finally:
                self.module_state.unlock()
                self._stop_recording()

    @QtCore.Slot()
    def _acquire_data_block(self):
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
                    samples_to_read = min(
                        samples_to_read,
                        (self._channel_buffer_size // self._oversampling_factor) * self._oversampling_factor
                    )
                    # read the current counter values
                    streamer.read_data_into_buffer(data_buffer=self._data_buffer,
                                                   number_of_samples=samples_to_read,
                                                   timestamp_buffer=self._times_buffer)
                    # Process data
                    self._process_trace_data(samples_to_read)
                    if self._times_buffer is None:
                        if self._data_recording_active:
                            self._add_to_recording_array(self._data_buffer[:, :samples_to_read])
                        self.sigNewRawData.emit(None, self._data_buffer[:, :samples_to_read])
                    else:
                        self._process_trace_times(samples_to_read)
                        if self._data_recording_active:
                            self._add_to_recording_array(self._data_buffer[:, :samples_to_read],
                                                         self._times_buffer[:samples_to_read])
                        self.sigNewRawData.emit(self._times_buffer[:samples_to_read],
                                                self._data_buffer[:, :samples_to_read])
                    # Emit update signal
                    self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)
                except:
                    self.log.exception('Reading data from streamer went wrong:')
                    self._stop()
                    raise

                self._sigNextDataFrame.emit()

    def _process_trace_times(self, number_of_samples: int) -> None:
        times = self._times_buffer[:number_of_samples]
        if self.oversampling_factor > 1:
            times = times.reshape(
                (times.size // self.oversampling_factor, self.oversampling_factor)
            )
            times = np.mean(times, axis=1)

        times = times[-self._trace_times.size:]  # discard data outside time frame

        # Roll data array to have a continuously running time trace
        self._trace_times = np.roll(self._trace_times, -times.size)
        # Insert new data
        self._trace_times[-times.size:] = times

    def _process_trace_data(self, number_of_samples: int) -> None:
        """ Processes raw data from the streaming device """
        # Down-sample and average according to oversampling factor
        data = self._data_buffer[:, :number_of_samples]
        if self.oversampling_factor > 1:
            data = data.reshape((data.shape[0],
                                 data.shape[1] // self.oversampling_factor,
                                 self.oversampling_factor))
            data = np.mean(data, axis=2)

        data = data[:, -self._trace_data.shape[1]:]  # discard data outside time frame

        # Roll data array to have a continuously running time trace
        self._trace_data = np.roll(self._trace_data, -data.shape[1], axis=1)
        # Insert new data
        self._trace_data[:, -data.shape[1]:] = data

        # Calculate moving average by using numpy.convolve with a normalized uniform filter
        if self.moving_average_width > 1 and self.averaged_channel_names:
            # Only convolve the new data and roll the previously calculated moving average
            self._trace_data_averaged = np.roll(self._trace_data_averaged, -data.shape[1], axis=1)
            offset = data.shape[1] + len(self.__moving_filter) - 1
            for i, ch in enumerate(self.averaged_channel_names):
                data_index = self.active_channel_names.index(ch)
                self._trace_data_averaged[i, -data.shape[1]:] = np.convolve(
                    self._trace_data[data_index, -offset:],
                    self.__moving_filter,
                    mode='valid'
                )

    def _init_recording_arrays(self) -> None:
        constraints = self.streamer_constraints
        try:
            sample_bytes = np.finfo(constraints.data_type).bits // 8
        except ValueError:
            sample_bytes = np.iinfo(constraints.data_type).bits // 8
        # Try to allocate space for approx. 10sec of samples (limited by ConfigOption)
        channel_count = len(self.active_channel_names)
        channel_samples = int(10 * self.sampling_rate)
        data_byte_size = sample_bytes * channel_count * channel_samples
        if constraints.sample_timing == SampleTiming.TIMESTAMP:
            if (8 * channel_samples + data_byte_size) > self._max_raw_data_bytes:
                channel_samples = max(
                    1,
                    self._max_raw_data_bytes // (channel_count * sample_bytes + 8)
                )
            self._recorded_raw_times = np.zeros(channel_samples, dtype=np.float64)
        else:
            if data_byte_size > self._max_raw_data_bytes:
                channel_samples = max(1, self._max_raw_data_bytes // (channel_count * sample_bytes))
            self._recorded_raw_times = None
        self._recorded_raw_data = np.zeros([channel_count, channel_samples],
                                           dtype=constraints.data_type)
        self._recorded_sample_count = 0

    def _expand_recording_arrays(self) -> int:
        channel_count, current_samples = self._recorded_raw_data.shape
        sample_byte_size = channel_count * self._recorded_raw_data.itemsize
        new_byte_size = 2 * current_samples * sample_byte_size
        if self._recorded_raw_times is not None:
            new_byte_size += 16 * current_samples
            sample_byte_size += 8
        new_samples = (min(self._max_raw_data_bytes,
                           new_byte_size) // sample_byte_size) - current_samples
        self._recorded_raw_data = np.append(
            self._recorded_raw_data,
            np.empty([channel_count, new_samples], dtype=self._recorded_raw_data.dtype),
            axis=1
        )
        if self._recorded_raw_times is not None:
            self._recorded_raw_times = np.append(
                self._recorded_raw_times,
                np.empty(new_samples, dtype=self._recorded_raw_times.dtype)
            )
        return new_samples

    def _add_to_recording_array(self, data, times = None) -> None:
        free_samples = self._recorded_raw_data.shape[1] - self._recorded_sample_count
        new_samples = data.shape[1]
        if new_samples > free_samples:
            free_samples += self._expand_recording_arrays()
            if new_samples > free_samples:
                self.log.error(
                    f'Configured maximum allowed amount of raw data reached '
                    f'({self._max_raw_data_bytes:d} bytes). Saving raw data so far and terminating '
                    f'data recording.'
                )
                self._recorded_raw_data[:, self._recorded_sample_count:self._recorded_sample_count + free_samples] = data[:, :free_samples]
                try:
                    self._recorded_raw_times[self._recorded_sample_count:self._recorded_sample_count + free_samples] = times[:free_samples]
                except (AttributeError, TypeError):
                    pass
                self._recorded_sample_count += free_samples
                self._stop_recording()
        self._recorded_raw_data[:, self._recorded_sample_count:self._recorded_sample_count + new_samples] = data
        try:
            self._recorded_raw_times[self._recorded_sample_count:self._recorded_sample_count + new_samples] = times
        except (AttributeError, TypeError):
            pass
        self._recorded_sample_count += new_samples

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
                    self._init_recording_arrays()
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
            self._stop_recording()

    def _stop_recording(self) -> None:
        try:
            if self._data_recording_active:
                self._save_recorded_data(save_figure=True)
        finally:
            self._data_recording_active = False
            self.sigStatusChanged.emit(self.module_state() == 'locked', False)

    def _save_recorded_data(self, name_tag='', save_figure=True):
        """ Save the recorded counter trace data and writes it to a file """
        try:
            constraints = self.streamer_constraints
            metadata = {
                'Start recoding time': self._record_start_time.strftime('%d.%m.%Y, %H:%M:%S.%f'),
                'Sample rate (Hz)': self.sampling_rate
            }
            column_headers = [
                f'{ch} ({constraints.channel_units[ch]})' for ch in self.active_channel_names
            ]
            nametag = f'data_trace_{name_tag}' if name_tag else 'data_trace'

            if self._recorded_raw_times is None:
                data = self._recorded_raw_data[:, :self._recorded_sample_count]
            else:
                data = np.vstack(
                    [self._recorded_raw_times[:self._recorded_sample_count],
                     self._recorded_raw_data[:, :self._recorded_sample_count]]
                )
                column_headers.insert(0, 'Time (s)')

            storage = TextDataStorage(root_dir=self.module_default_data_dir)
            storage.save_data(data.transpose(),
                              metadata=metadata,
                              nametag=nametag,
                              column_headers=column_headers)
        except:
            self.log.exception('Something went wrong while saving raw data:')
            raise

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
