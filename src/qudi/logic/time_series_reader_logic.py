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

import numpy as np
import datetime as dt
import matplotlib.pyplot as plt
from PySide2 import QtCore
from scipy.signal import decimate
from typing import Union, Optional, Sequence, Iterable, List, Dict, Mapping, Tuple

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.util.helpers import is_integer_type
from qudi.util.network import netobtain
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamConstraints
from qudi.util.datastorage import TextDataStorage
from qudi.util.units import ScaledFloat


class TimeSeriesReaderLogic(LogicBase):
    """
    This logic module gathers data from a hardware streaming device.

    Example config for copy-paste:

    time_series_reader_logic:
        module.Class: 'time_series_reader_logic.TimeSeriesReaderLogic'
        options:
            max_frame_rate: 20  # optional (default: 20Hz)
            channel_buffer_size: 1048576  # optional (default: 1MSample)
            max_raw_data_bytes: 1073741824  # optional (default: 1GB)
        connect:
            streamer: <streamer_name>
    """
    # declare signals
    sigDataChanged = QtCore.Signal(object, object, object, object)
    sigNewRawData = QtCore.Signal(object, object)  # raw data samples, timestamp samples (optional)
    sigStatusChanged = QtCore.Signal(bool, bool)
    sigTraceSettingsChanged = QtCore.Signal(dict)
    sigChannelSettingsChanged = QtCore.Signal(list, list)
    _sigNextDataFrame = QtCore.Signal()  # internal signal

    # declare connectors
    _streamer = Connector(name='streamer', interface='DataInStreamInterface')

    # config options
    _max_frame_rate = ConfigOption('max_frame_rate', default=20, missing='warn')
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

        # important to know for method of reading the buffer
        self._streamer_is_remote = False

    def on_activate(self) -> None:
        """ Initialisation performed during activation of the module. """
        # Temp reference to connected hardware module
        streamer = self._streamer()

        # check if streamer is a remote module
        constraints = streamer.constraints
        if type(constraints) != type(netobtain(constraints)):
            self._streamer_is_remote = True
            self.log.debug('Streamer is a remote module. Do not use a shared buffer.')

        # Flag to stop the loop and process variables
        self._recorded_raw_data = None
        self._recorded_raw_times = None
        self._recorded_sample_count = 0
        self._data_recording_active = False
        self._record_start_time = None

        # Check valid StatusVar
        # active channels
        avail_channels = list(constraints.channel_units)
        if self._active_channels is None:
            if streamer.active_channels:
                self._active_channels = streamer.active_channels.copy()
            else:
                self._active_channels = avail_channels
            self._averaged_channels = self._active_channels
        elif any(ch not in avail_channels for ch in self._active_channels):
            self.log.warning('Invalid active channels found in StatusVar. StatusVar ignored.')
            if streamer.active_channels:
                self._active_channels = streamer.active_channels.copy()
            else:
                self._active_channels = avail_channels
            self._averaged_channels = self._active_channels
        elif self._averaged_channels is not None:
            self._averaged_channels = [
                ch for ch in self._averaged_channels if ch in self._active_channels
            ]

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

    def on_deactivate(self) -> None:
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

    def _init_data_arrays(self) -> None:
        channel_count = len(self.active_channel_names)
        averaged_channel_count = len(self._averaged_channels)
        window_size = int(round(self._trace_window_size * self.data_rate))
        constraints = self.streamer_constraints
        trace_dtype = np.float64 if is_integer_type(constraints.data_type) else constraints.data_type

        # processed data arrays
        self._trace_data = np.zeros(
            [window_size + self._moving_average_width // 2, channel_count],
            dtype=trace_dtype
        )
        self._trace_data_averaged = np.zeros(
            [window_size - self._moving_average_width // 2, averaged_channel_count],
            dtype=trace_dtype
        )
        self._trace_times = np.arange(window_size, dtype=np.float64)
        if constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._trace_times -= window_size
        if constraints.sample_timing != SampleTiming.RANDOM:
            self._trace_times /= self.data_rate

        # raw data buffers
        self._data_buffer = np.empty(channel_count * self._channel_buffer_size,
                                     dtype=constraints.data_type)
        if constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._times_buffer = np.zeros(self._channel_buffer_size, dtype=np.float64)
        else:
            self._times_buffer = None

    @property
    def streamer_constraints(self) -> DataInStreamConstraints:
        """ Retrieve the hardware constrains from the counter device """
        # netobtain is required if streamer is a remote module
        return netobtain(self._streamer().constraints)

    @property
    def data_rate(self) -> float:
        """ Data rate in Hz. The data rate describes the effective sample rate of the processed
        sample trace taking into account oversampling:

            data_rate = hardware_sample_rate / oversampling_factor
        """
        return self.sampling_rate / self.oversampling_factor

    @data_rate.setter
    def data_rate(self, val: float) -> None:
        self.set_trace_settings(data_rate=val)

    @property
    def trace_window_size(self) -> float:
        """ The size of the running trace window in seconds """
        return self._trace_window_size

    @trace_window_size.setter
    def trace_window_size(self, val: Union[int, float]) -> None:
        self.set_trace_settings(trace_window_size=val)

    @property
    def moving_average_width(self) -> int:
        """ The width of the moving average filter in samples. Must be an odd number. """
        return self._moving_average_width

    @moving_average_width.setter
    def moving_average_width(self, val: int) -> None:
        self.set_trace_settings(moving_average_width=val)

    @property
    def data_recording_active(self) -> bool:
        """ Read-only bool flag indicating active data logging so it can be saved to file later """
        return self._data_recording_active

    @property
    def oversampling_factor(self) -> int:
        """ This integer value determines how many times more samples are acquired by the hardware
        and averaged before being processed by the trace logic.
        An oversampling factor <= 1 means no oversampling is performed.
        """
        return self._oversampling_factor

    @oversampling_factor.setter
    def oversampling_factor(self, val: int) -> None:
        self.set_trace_settings(oversampling_factor=val)

    @property
    def sampling_rate(self) -> float:
        """ Read-only property returning the actually set sample rate of the streaming hardware.
        If not oversampling is used, this should be the same value as data_rate.
        If oversampling is active, this value will be larger (by the oversampling factor) than
        data_rate.
        """
        return self._streamer().sample_rate

    @property
    def active_channel_names(self) -> List[str]:
        """ Read-only property returning the currently active channel names """
        return netobtain(self._streamer().active_channels.copy())

    @property
    def averaged_channel_names(self) -> List[str]:
        """ Read-only property returning the currently active and averaged channel names """
        return self._averaged_channels.copy()

    @property
    def trace_data(self) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        """ Read-only property returning the x-axis of the data trace and a dictionary of the
        corresponding trace data arrays for each channel
        """
        data_offset = self._trace_data.shape[0] - self._moving_average_width // 2
        data = {ch: self._trace_data[:data_offset, i] for i, ch in
                enumerate(self.active_channel_names)}
        return self._trace_times, data

    @property
    def averaged_trace_data(self) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        """ Read-only property returning the x-axis of the averaged data trace and a dictionary of
        the corresponding averaged trace data arrays for each channel
        """
        if not self.averaged_channel_names or self.moving_average_width <= 1:
            return None, None
        data = {ch: self._trace_data_averaged[:, i] for i, ch in
                enumerate(self.averaged_channel_names)}
        return self._trace_times[-self._trace_data_averaged.shape[0]:], data

    @property
    def trace_settings(self) -> Dict[str, Union[int, float]]:
        """ Read-only property returning the current trace settings as dictionary """
        return {'oversampling_factor' : self.oversampling_factor,
                'moving_average_width': self.moving_average_width,
                'trace_window_size'   : self.trace_window_size,
                'data_rate'           : self.data_rate}

    @property
    def channel_settings(self) -> Tuple[List[str], List[str]]:
        """ Read-only property returning the currently active channel names and the currently
        averaged channel names.
        """
        return self.active_channel_names, self.averaged_channel_names

    @QtCore.Slot(dict)
    def set_trace_settings(self,
                           settings_dict: Optional[Mapping[str, Union[int, float]]] = None,
                           **kwargs) -> None:
        """ Method to set new trace settings.
        Can either provide (a subset of) trace_settings via dict as first positional argument
        and/or as keyword arguments (**kwargs overwrites settings_dict for duplicate keys).

        See property trace_settings for valid setting keywords.

        Calling this method while a trace is running will stop the trace first and restart after
        successful application of new settings.
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
                    channel_buffer_size=self._channel_buffer_size,
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

    @QtCore.Slot(list, list)
    def set_channel_settings(self, enabled: Sequence[str], averaged: Sequence[str]) -> None:
        """ Method to set new channel settings by providing a sequence of active channel names
        (enabled) as well as a sequence of channel names to be averaged (averaged).

        Calling this method while a trace is running will stop the trace first and restart after
        successful application of new settings.
        """
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
                channel_buffer_size=self._channel_buffer_size,
                sample_rate=self.sampling_rate
            )
            self._averaged_channels = [ch for ch in averaged if ch in enabled]
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
    def start_reading(self) -> None:
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
    def stop_reading(self) -> None:
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
                self._stop_cleanup()

    def _stop_cleanup(self) -> None:
        self.module_state.unlock()
        self._stop_recording()

    @QtCore.Slot()
    def _acquire_data_block(self) -> None:
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
                    if not self._streamer_is_remote:
                        # we can use the more efficient method of using a shared buffer
                        streamer.read_data_into_buffer(data_buffer=self._data_buffer,
                                                       samples_per_channel=samples_to_read,
                                                       timestamp_buffer=self._times_buffer)
                    else:
                        # streamer is remote, we need to have a new buffer created and passed to us
                        self._data_buffer, self._times_buffer = streamer.read_data(
                            samples_per_channel=samples_to_read
                        )
                        self._data_buffer = netobtain(self._data_buffer)
                        self._times_buffer = netobtain(self._times_buffer)

                    # Process data
                    channel_count = len(self.active_channel_names)
                    data_view = self._data_buffer[:channel_count * samples_to_read]
                    self._process_trace_data(data_view)
                    if self._times_buffer is None:
                        times_view = None
                    else:
                        times_view = self._times_buffer[:samples_to_read]
                        self._process_trace_times(times_view)

                    if self._data_recording_active:
                        self._add_to_recording_array(data_view, times_view)
                    self.sigNewRawData.emit(data_view, times_view)
                    # Emit update signal
                    self.sigDataChanged.emit(*self.trace_data, *self.averaged_trace_data)
                except Exception as e:
                    self.log.warning(f'Reading data from streamer went wrong: {e}')
                    self._stop_cleanup()
                    return
                self._sigNextDataFrame.emit()

    def _process_trace_times(self, times_buffer: np.ndarray) -> None:
        if self.oversampling_factor > 1:
            times_buffer = times_buffer.reshape(
                (times_buffer.size // self.oversampling_factor, self.oversampling_factor)
            )
            times_buffer = np.mean(times_buffer, axis=1)

        # discard data outside time frame
        times_buffer = times_buffer[-self._trace_times.size:]

        # Roll data array to have a continuously running time trace
        self._trace_times = np.roll(self._trace_times, -times_buffer.size)
        # Insert new data
        self._trace_times[-times_buffer.size:] = times_buffer

    def _process_trace_data(self, data_buffer: np.ndarray) -> None:
        """ Processes raw data from the streaming device """
        channel_count = len(self.active_channel_names)
        samples_per_channel = data_buffer.size // channel_count
        data_view = data_buffer.reshape([samples_per_channel, channel_count])
        # Down-sample and average according to oversampling factor
        if self.oversampling_factor > 1:
            data_view = data_view.reshape(
                [samples_per_channel // self.oversampling_factor,
                 self.oversampling_factor,
                 channel_count]
            )
            data_view = np.mean(data_view, axis=1)

        # discard data outside time frame
        data_view = data_view[-self._trace_data.shape[0]:, :]
        new_channel_samples = data_view.shape[0]

        # Roll data array to have a continuously running time trace
        self._trace_data = np.roll(self._trace_data, -new_channel_samples, axis=0)
        # Insert new data
        self._trace_data[-new_channel_samples:, :] = data_view

        # Calculate moving average by using numpy.convolve with a normalized uniform filter
        if self.moving_average_width > 1 and self.averaged_channel_names:
            # Only convolve the new data and roll the previously calculated moving average
            self._trace_data_averaged = np.roll(self._trace_data_averaged,
                                                -new_channel_samples,
                                                axis=0)
            offset = new_channel_samples + len(self.__moving_filter) - 1
            for i, ch in enumerate(self.averaged_channel_names):
                data_index = self.active_channel_names.index(ch)
                self._trace_data_averaged[-new_channel_samples:, i] = np.convolve(
                    self._trace_data[-offset:, data_index],
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
        self._recorded_raw_data = np.empty(channel_count * channel_samples,
                                           dtype=constraints.data_type)
        self._recorded_sample_count = 0

    def _expand_recording_arrays(self) -> int:
        total_samples = self._recorded_raw_data.size
        channel_count = len(self.active_channel_names)
        current_samples = total_samples // channel_count
        byte_granularity = channel_count * self._recorded_raw_data.itemsize
        new_byte_size = 2 * current_samples * byte_granularity
        if self._recorded_raw_times is not None:
            new_byte_size += 2 * current_samples * self._recorded_raw_times.itemsize
            byte_granularity += self._recorded_raw_times.itemsize
        new_samples_per_channel = min(self._max_raw_data_bytes, new_byte_size) // byte_granularity
        additional_samples = new_samples_per_channel - current_samples
        self._recorded_raw_data = np.append(
            self._recorded_raw_data,
            np.empty(channel_count * additional_samples, dtype=self._recorded_raw_data.dtype),
        )
        if self._recorded_raw_times is not None:
            self._recorded_raw_times = np.append(
                self._recorded_raw_times,
                np.empty(additional_samples, dtype=self._recorded_raw_times.dtype)
            )
        return additional_samples

    def _add_to_recording_array(self, data, times=None) -> None:
        channel_count = len(self.active_channel_names)
        free_samples_per_channel = (self._recorded_raw_data.size // channel_count) - \
            self._recorded_sample_count
        new_samples = data.size // channel_count
        total_new_samples = new_samples * channel_count
        if new_samples > free_samples_per_channel:
            free_samples_per_channel += self._expand_recording_arrays()
            if new_samples > free_samples_per_channel:
                self.log.error(
                    f'Configured maximum allowed amount of raw data reached '
                    f'({self._max_raw_data_bytes:d} bytes). Saving raw data so far and terminating '
                    f'data recording.'
                )
                self._recorded_raw_data[channel_count * self._recorded_sample_count:] = data[:channel_count * free_samples_per_channel]
                if self._recorded_raw_times is not None:
                    self._recorded_raw_times[self._recorded_sample_count:] = times[:free_samples_per_channel]
                self._recorded_sample_count += free_samples_per_channel
                self._stop_recording()
                return

        begin = self._recorded_sample_count * channel_count
        end = begin + total_new_samples
        self._recorded_raw_data[begin:end] = data[:total_new_samples]
        if self._recorded_raw_times is not None:
            begin = self._recorded_sample_count
            end = begin + new_samples
            self._recorded_raw_times[begin:end] = times[:new_samples]
        self._recorded_sample_count += new_samples

    @QtCore.Slot()
    def start_recording(self):
        """ Will start to continuously accumulate raw data from the streaming hardware (without
        running average and oversampling). Data will be saved to file once the trace acquisition is
        stopped.
        If the streamer is not running it will be started in order to have data to save.
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
        """ Stop the accumulative data recording and save data to file. Will not stop the data
        streaming. Ignored if no stream is running (module is in idle state).
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
                'Sample rate (Hz)'   : self.sampling_rate,
                'Sample timing'      : constraints.sample_timing.name
            }
            column_headers = [
                f'{ch} ({constraints.channel_units[ch]})' for ch in self.active_channel_names
            ]
            channel_count = len(column_headers)
            nametag = f'data_trace_{name_tag}' if name_tag else 'data_trace'

            data = self._recorded_raw_data[:channel_count * self._recorded_sample_count].reshape(
                [self._recorded_sample_count, channel_count]
            )
            if self._recorded_raw_times is not None:
                print(data.shape)
                data = np.column_stack(
                    [self._recorded_raw_times[:self._recorded_sample_count], data]
                )
                print(data.shape, '\n')
                column_headers.insert(0, 'Time (s)')
            try:
                fig = self._draw_raw_data_thumbnail(data) if save_figure else None
            finally:
                storage = TextDataStorage(root_dir=self.module_default_data_dir)
                filepath, _, _ = storage.save_data(data,
                                                   metadata=metadata,
                                                   nametag=nametag,
                                                   column_headers=column_headers)
            if fig is not None:
                storage.save_thumbnail(mpl_figure=fig, file_path=filepath)
        except:
            self.log.exception('Something went wrong while saving raw data:')
            raise

    def _draw_raw_data_thumbnail(self, data: np.ndarray) -> plt.Figure:
        """ Draw figure to save with data file """
        constraints = self.streamer_constraints
        # Handle excessive data size for plotting. Artefacts may occur due to IIR decimation filter.
        decimate_factor = 0
        while data.shape[0] >= 20000:
            print(data.shape[0])
            decimate_factor += 2
            data = decimate(data, q=2, axis=0)

        if constraints.sample_timing == SampleTiming.RANDOM:
            x = np.arange(data.shape[0])
            x_label = 'Sample Index'
        elif constraints.sample_timing == SampleTiming.CONSTANT:
            if decimate_factor > 0:
                x = np.arange(data.shape[0]) / (self.sampling_rate / decimate_factor)
            else:
                x = np.arange(data.shape[0]) / self.sampling_rate
            x_label = 'Time (s)'
        else:
            x = data[:, 0] - data[0, 0]
            data = data[:, 1:]
            x_label = 'Time (s)'
        # Create figure and scale data
        max_abs_value = ScaledFloat(max(data.max(), np.abs(data.min())))
        if max_abs_value.scale:
            data = data / max_abs_value.scale_val
            y_label = f'Signal ({max_abs_value.scale}arb.u.)'
        else:
            y_label = 'Signal (arb.u.)'

        fig, ax = plt.subplots()
        ax.plot(x, data, linestyle='-', marker='', linewidth=0.5)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        return fig

    @QtCore.Slot()
    def save_trace_snapshot(self, name_tag: Optional[str] = '', save_figure: Optional[bool] = True):
        """ A snapshot of the current data trace window will be saved """
        try:
            timestamp = dt.datetime.now()
            constraints = self.streamer_constraints
            metadata = {
                'Timestamp': timestamp.strftime('%d.%m.%Y, %H:%M:%S.%f'),
                'Data rate (Hz)': self.data_rate,
                'Oversampling factor (samples)': self.oversampling_factor,
                'Sampling rate (Hz)': self.sampling_rate
            }
            column_headers = [
                f'{ch} ({constraints.channel_units[ch]})' for ch in self.active_channel_names
            ]
            nametag = f'trace_snapshot_{name_tag}' if name_tag else 'trace_snapshot'

            data_offset = self._trace_data.shape[0] - self._moving_average_width // 2
            data = self._trace_data[:data_offset, :]
            x = self._trace_times
            try:
                fig = self._draw_trace_snapshot_thumbnail(x, data) if save_figure else None
            finally:
                if constraints.sample_timing != SampleTiming.RANDOM:
                    data = np.column_stack([x, data])
                    column_headers.insert(0, 'Time (s)')

                storage = TextDataStorage(root_dir=self.module_default_data_dir)
                filepath, _, _ = storage.save_data(data,
                                                   timestamp=timestamp,
                                                   metadata=metadata,
                                                   nametag=nametag,
                                                   column_headers=column_headers)
            if fig is not None:
                storage.save_thumbnail(mpl_figure=fig, file_path=filepath)
        except:
            self.log.exception('Something went wrong while saving trace snapshot:')
            raise

    def _draw_trace_snapshot_thumbnail(self, x: np.ndarray, data: np.ndarray) -> plt.Figure:
        """ Draw figure to save with data file """
        if self.streamer_constraints.sample_timing == SampleTiming.RANDOM:
            x_label = 'Sample Index'
        else:
            x_label = 'Time (s)'

        # Create figure and scale data
        max_abs_value = ScaledFloat(max(data.max(), np.abs(data.min())))
        if max_abs_value.scale:
            data = data / max_abs_value.scale_val
            y_label = f'Signal ({max_abs_value.scale}arb.u.)'
        else:
            y_label = 'Signal (arb.u.)'

        fig, ax = plt.subplots()
        ax.plot(x, data, linestyle='-', marker='', linewidth=0.5)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        return fig
