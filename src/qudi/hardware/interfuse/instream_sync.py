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

import time
import numpy as np
from PySide2 import QtCore
from scipy import interpolate
from enum import Enum

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain
from qudi.util.constraints import ScalarConstraint
from qudi.util.ringbuffer import RingBuffer, InterleavedRingBuffer
from qudi.util.ringbuffer import RingBufferReader, SyncRingBufferReader
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamConstraints, DataInStreamInterface
from typing import Tuple, Optional, Sequence, Union, List

# ToDo: Add config option for settings behaviour, i.e. streamer configuration priority


class IgnoreStream(Enum):
    NONE = 0
    PRIMARY = 1
    SECONDARY = 2


class DataInStreamSync(DataInStreamInterface):
    """
    Example config for copy-paste:

    data_instream_sync:
        module.Class: 'interfuse.instream_sync.DataInStreamSync'
        options:
            allow_overwrite: False  # optional, ignore buffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate from connected hardware
            min_interpolation_samples: 2  # optional, minimum samples per frame (must be >= 2)
            # delay_time: 0.0  # optional, time offset for secondary stream interpolation
        connect:
            primary_streamer: <data_instream_hardware_1>
            secondary_streamer: <data_instream_hardware_2>
    """
    # add separate thread affinity for this module
    _threaded = True

    # Signals
    _sigStartLoop = QtCore.Signal()

    # Config options
    _allow_overwrite: bool = ConfigOption(name='allow_overwrite',
                                          default=False,
                                          constructor=lambda x: bool(x))
    _max_poll_rate: float = ConfigOption(name='max_poll_rate', default=100., missing='warn')
    _min_interpolation_samples: int = ConfigOption(name='min_interpolation_samples',
                                                   default=2,
                                                   missing='warn',
                                                   constructor=lambda x: max(2, int(x)))
    _delay_time: Union[None, float] = ConfigOption(name='delay_time', default=None)

    # declare streaming hardware connectors
    _primary_streamer: DataInStreamInterface = Connector(name='primary_streamer',
                                                         interface='DataInStreamInterface')
    _secondary_streamer: DataInStreamInterface = Connector(name='secondary_streamer',
                                                           interface='DataInStreamInterface')

    def __init__(self, *args, **kwargs):
        """ """
        super().__init__(*args, **kwargs)

        self._lock = Mutex()
        self.__primary_is_remote: bool = False
        self.__secondary_is_remote: bool = False
        self._stop_requested: bool = True
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)

        # Serves as time base reference if no timestamps are returned by the hardware
        self._primary_sample_count: int = 0
        self._secondary_sample_count: int = 0

        # sample buffers
        self._data_buffer: InterleavedRingBuffer = None
        self._timestamp_buffer: Union[None, RingBuffer] = None
        self._tmp_combined_data_buf: np.ndarray = None
        self._tmp_primary_data_buf: Union[None, np.ndarray] = None
        self._tmp_secondary_data_buf: Union[None, np.ndarray] = None
        self._tmp_primary_timestamp_buf: Union[None, np.ndarray] = None
        self._tmp_secondary_timestamp_buf: Union[None, np.ndarray] = None

        # combined streamer constraints
        self._constraints: DataInStreamConstraints = None

        # mute streamer flag
        self.__ignore: IgnoreStream = IgnoreStream.NONE

    def on_activate(self):
        primary: DataInStreamInterface = self._primary_streamer()
        secondary: DataInStreamInterface = self._secondary_streamer()
        primary_constr = netobtain(primary.constraints)
        secondary_constr = netobtain(secondary.constraints)
        if type(primary_constr) == type(primary.constraints):
            self.__primary_is_remote = False
        else:
            self.__primary_is_remote = True
        if type(secondary_constr) == type(secondary.constraints):
            self.__secondary_is_remote = False
        else:
            self.__secondary_is_remote = True
        self._stop_requested = True
        self.__ignore = IgnoreStream.NONE
        self._primary_sample_count = self._secondary_sample_count = 0
        self._constraints = self.__combine_constraints(primary_constr, secondary_constr)

        self.__create_buffers()

        self._timer.timeout.connect(self._pull_data, QtCore.Qt.QueuedConnection)
        self._sigStartLoop.connect(self._timer.start, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        self._stop_requested = True
        try:
            self._sigStartLoop.disconnect()
            self._timer.timeout.disconnect()
        finally:
            if self.module_state() == 'locked':
                self.module_state.unlock()
                primary = self._primary_streamer()
                secondary = self._secondary_streamer()
                if primary.module_state() == 'locked':
                    primary.stop_stream()
                if secondary.module_state() == 'locked':
                    secondary.stop_stream()
            self._data_buffer = self._timestamp_buffer = None
            self._tmp_combined_data_buf = None
            self._tmp_primary_data_buf = self._tmp_secondary_data_buf = None
            self._tmp_primary_timestamp_buf = self._tmp_secondary_timestamp_buf = None

    @staticmethod
    def __combine_constraints(primary: DataInStreamConstraints,
                              secondary: DataInStreamConstraints) -> DataInStreamConstraints:
        timings = [SampleTiming(primary.sample_timing.value),
                   SampleTiming(secondary.sample_timing.value)]
        if any(timing == SampleTiming.TIMESTAMP for timing in timings):
            timing = SampleTiming.TIMESTAMP
        elif any(timing == SampleTiming.RANDOM for timing in timings):
            raise RuntimeError(f'DataInStreamSync interfuse does not support streamers with '
                               f'{SampleTiming.RANDOM} sample_timing')
        else:
            timing = SampleTiming.CONSTANT
        return DataInStreamConstraints(
            channel_units={**primary.channel_units, **secondary.channel_units},
            sample_timing=timing,
            streaming_modes=[StreamingMode.CONTINUOUS],
            data_type=np.float64,
            channel_buffer_size=netobtain(primary.channel_buffer_size.copy()),
            sample_rate=netobtain(primary.sample_rate.copy())
        )

    def __create_buffers(self) -> None:
        primary: DataInStreamInterface = self._primary_streamer()
        secondary: DataInStreamInterface = self._secondary_streamer()
        primary_buffer_size = primary.channel_buffer_size
        secondary_buffer_size = secondary.channel_buffer_size
        combined_buffer_size = max(primary_buffer_size, secondary_buffer_size)
        primary_channels = len(primary.active_channels)
        secondary_channels = len(secondary.active_channels)
        sample_rate = primary.sample_rate
        if self.__ignore == IgnoreStream.PRIMARY:
            primary_channels = 0
            sample_rate = secondary.sample_rate
        elif self.__ignore == IgnoreStream.SECONDARY:
            secondary_channels = 0
        total_channels = primary_channels + secondary_channels
        primary_dtype = primary.constraints.data_type
        secondary_dtype = secondary.constraints.data_type
        # Create ringbuffers
        self._data_buffer = InterleavedRingBuffer(
            interleave_factor=total_channels,
            size=combined_buffer_size,
            dtype=np.float64,
            allow_overwrite=self._allow_overwrite,
            expected_sample_rate=sample_rate
        )
        if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._timestamp_buffer = RingBuffer(
                size=combined_buffer_size,
                dtype=np.float64,
                allow_overwrite=self._allow_overwrite,
                expected_sample_rate=sample_rate
            )
        else:
            self._timestamp_buffer = None
        # Create intermediate buffer arrays to minimize memory allocation on each read at the
        # expense or more memory consumption. Only works if connected streamer is local.
        self._tmp_combined_data_buf = np.empty([combined_buffer_size, total_channels],
                                               dtype=np.float64)
        if self.__primary_is_remote:
            self._tmp_primary_data_buf = self._tmp_primary_timestamp_buf = None
        else:
            self._tmp_primary_data_buf = np.empty([primary_buffer_size, primary_channels],
                                                  dtype=primary_dtype)
            self._tmp_primary_timestamp_buf = np.empty(primary_buffer_size, dtype=np.float64)
        if self.__secondary_is_remote:
            self._tmp_secondary_data_buf = self._tmp_secondary_timestamp_buf = None
        else:
            self._tmp_secondary_data_buf = np.empty([secondary_buffer_size, secondary_channels],
                                                    dtype=secondary_dtype)
            self._tmp_secondary_timestamp_buf = np.empty(secondary_buffer_size, dtype=np.float64)

    #############################################
    # DataInStreamInterface implementation below
    #############################################

    @property
    def constraints(self) -> DataInStreamConstraints:
        return self._constraints

    @property
    def available_samples(self) -> int:
        if self._timestamp_buffer is None:
            return self._data_buffer.fill_count
        else:
            return min(self._data_buffer.fill_count, self._timestamp_buffer.fill_count)

    @property
    def sample_rate(self) -> float:
        if self.__ignore == IgnoreStream.PRIMARY:
            return self._secondary_streamer().sample_rate
        return self._primary_streamer().sample_rate

    @property
    def channel_buffer_size(self) -> int:
        return self._data_buffer.size

    @property
    def streaming_mode(self) -> StreamingMode:
        return StreamingMode.CONTINUOUS

    @property
    def active_channels(self) -> List[str]:
        if self.__ignore == IgnoreStream.NONE:
            return [*self._primary_streamer().active_channels,
                    *self._secondary_streamer().active_channels]
        elif self.__ignore == IgnoreStream.PRIMARY:
            return [*self._secondary_streamer().active_channels]
        else:
            return [*self._primary_streamer().active_channels]

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        with self._lock:
            if self.module_state() == 'locked':
                raise RuntimeError('Streamer is running.')
            primary: DataInStreamInterface = self._primary_streamer()
            secondary: DataInStreamInterface = self._secondary_streamer()
            primary_channels = [
                ch for ch in active_channels if ch in primary.constraints.channel_units
            ]
            secondary_channels = [
                ch for ch in active_channels if ch in secondary.constraints.channel_units
            ]
            if len(primary_channels) == len(secondary_channels) == 0:
                raise ValueError('At least one channel needs to be active')
            elif len(primary_channels) == 0:
                self.__ignore = IgnoreStream.PRIMARY
            elif len(secondary_channels) == 0:
                self.__ignore = IgnoreStream.SECONDARY
            else:
                self.__ignore = IgnoreStream.NONE
            if self.__ignore != IgnoreStream.PRIMARY and primary.module_state() == 'idle':
                primary.configure(
                    active_channels=primary_channels,
                    streaming_mode=StreamingMode.CONTINUOUS,
                    channel_buffer_size=channel_buffer_size,
                    sample_rate=sample_rate
                )
            if self.__ignore != IgnoreStream.SECONDARY and secondary.module_state() == 'idle':
                secondary.configure(
                    active_channels=secondary_channels,
                    streaming_mode=StreamingMode.CONTINUOUS,
                    channel_buffer_size=secondary.channel_buffer_size,
                    sample_rate=secondary.sample_rate
                )
            self.__create_buffers()

    def start_stream(self) -> None:
        with self._lock:
            if self.module_state() == 'idle':
                self.module_state.lock()
                try:
                    primary = self._primary_streamer()
                    secondary = self._secondary_streamer()
                    self._primary_sample_count = self._secondary_sample_count = 0
                    self.__create_buffers()
                    self._timer.setInterval(max(10, int(1000. / self._data_buffer.average_rate)))
                    if self.__ignore != IgnoreStream.PRIMARY and primary.module_state() == 'idle':
                        primary.start_stream()
                    if self.__ignore != IgnoreStream.SECONDARY and secondary.module_state() == 'idle':
                        secondary.start_stream()
                    self._stop_requested = False
                    self._sigStartLoop.emit()
                except Exception:
                    self._stop_requested = True
                    self.module_state.unlock()
                    raise

    def stop_stream(self) -> None:
        with self._lock:
            self._stop_requested = True
        # Wait until the threaded loop has actually stopped
        while self.module_state() == 'locked':
            time.sleep(0.1)

    def _read_data_into_buffer(self,
                               data_buffer: np.ndarray,
                               samples_per_channel: int,
                               timestamp_buffer: Optional[np.ndarray] = None) -> None:
        # read data until desired amount is available. Raise RuntimeError if stream is stopped.
        channel_count = self._data_buffer.interleave_factor
        # Reshape buffers without copy
        data_buffer = data_buffer[:(samples_per_channel * channel_count)].reshape(
            [samples_per_channel, channel_count]
        )
        # Read until you have all requested samples acquired
        if self._timestamp_buffer is None:
            RingBufferReader(self._data_buffer, self._max_poll_rate)(
                samples_per_channel,
                data_buffer
            )
        else:
            timestamp_buffer = timestamp_buffer[:samples_per_channel]
            SyncRingBufferReader([self._data_buffer, self._timestamp_buffer], self._max_poll_rate)(
                samples_per_channel,
                [data_buffer, timestamp_buffer]
            )

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        with self._lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Streamer is not running.')
            return self._read_data_into_buffer(data_buffer, samples_per_channel, timestamp_buffer)

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        """ Read data from the stream buffer into a 1D numpy array given as parameter.
        All samples for each channel are stored in consecutive blocks one after the other.
        The number of samples read per channel is returned and can be used to slice out valid data
        from the buffer arrays like:

            valid_data = data_buffer[:<channel_count> * <return_value>]
            valid_timestamps = timestamp_buffer[:<return_value>]

        See "read_data_into_buffer" documentation for more details.

        This method will read all currently available samples into buffer. If number of available
        samples exceeds buffer size, read only as many samples as fit into the buffer.
        """
        with self._lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Streamer is not running.')
            channel_count = self._data_buffer.interleave_factor
            available = min(self.available_samples, data_buffer.size // channel_count)
            self._read_data_into_buffer(data_buffer, available, timestamp_buffer)
            return available

    def read_data(self,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """ Read data from the stream buffer into a 1D numpy array and return it.
        All samples for each channel are stored in consecutive blocks one after the other.
        The returned data_buffer can be unraveled into channel samples with:

            data_buffer.reshape([<samples_per_channel>, <channel_count>])

        The numpy array data type is the one defined in self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.

        If samples_per_channel is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        """
        with self._lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Streamer is not running.')
            if samples_per_channel is None:
                samples_per_channel = self.available_samples
            channel_count = self._data_buffer.interleave_factor
            data = np.empty(samples_per_channel * channel_count, dtype=self._data_buffer.dtype)
            if self._timestamp_buffer is None:
                timestamps = None
            else:
                timestamps = np.empty(samples_per_channel, dtype=self._timestamp_buffer.dtype)
            self._read_data_into_buffer(data, samples_per_channel, timestamps)
            return data, timestamps

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """ This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        In case of SampleTiming.TIMESTAMP a single numpy.float64 timestamp value will be returned
        as well.
        """
        data, timestamps = self.read_data(1)
        return data, None if timestamps is None else timestamps[0]

    #########################################
    # Data poll and interpolation loop below
    #########################################

    def __pull_primary_data(self, samples: int) -> Tuple[np.ndarray, np.ndarray]:
        streamer: DataInStreamInterface = self._primary_streamer()
        timestamp = SampleTiming(streamer.constraints.sample_timing.value) == SampleTiming.TIMESTAMP
        if self.__primary_is_remote:
            tmp_data, tmp_times = streamer.read_data(samples)
            tmp_data = netobtain(tmp_data)
            if timestamp:
                tmp_times = netobtain(tmp_times)
        else:
            tmp_data = self._tmp_primary_data_buf[:samples, :]
            tmp_times = self._tmp_primary_timestamp_buf[:samples]
            streamer.read_data_into_buffer(data_buffer=tmp_data.reshape(-1),
                                           samples_per_channel=samples,
                                           timestamp_buffer=tmp_times)
        if not timestamp:
            tmp_times = np.arange(self._primary_sample_count,
                                  self._primary_sample_count + samples,
                                  dtype=np.float64)
            tmp_times /= streamer.sample_rate
        self._primary_sample_count += samples
        return tmp_times, tmp_data

    def __pull_secondary_data(self, samples: int) -> Tuple[np.ndarray, np.ndarray]:
        streamer: DataInStreamInterface = self._secondary_streamer()
        timestamp = SampleTiming(streamer.constraints.sample_timing.value) == SampleTiming.TIMESTAMP
        if self.__secondary_is_remote:
            tmp_data, tmp_times = streamer.read_data(samples)
            tmp_data = netobtain(tmp_data)
            if timestamp:
                tmp_times = netobtain(tmp_times)
        else:
            tmp_data = self._tmp_secondary_data_buf[:samples, :]
            tmp_times = self._tmp_secondary_timestamp_buf[:samples]
            streamer.read_data_into_buffer(data_buffer=tmp_data.reshape(-1),
                                           samples_per_channel=samples,
                                           timestamp_buffer=tmp_times)
        if not timestamp:
            tmp_times = np.arange(self._secondary_sample_count,
                                  self._secondary_sample_count + samples,
                                  dtype=np.float64)
            tmp_times /= streamer.sample_rate
        self._secondary_sample_count += samples
        return tmp_times, tmp_data

    def _pull_data(self) -> None:
        """ This method synchronizes (interpolates) the available data from both the timeseries
        logic and the wavemeter hardware module. It runs repeatedly by being connected to a QTimer timeout signal from the time series (sigNewRawData).
        Note: new_count_timing is currently unused, but might be used for a more elaborate synchronization.
        #TODO Assure that the timing below is synchronized at an acceptable level and saving of raw data
        """
        # Break loop if stop is requested
        if self._stop_requested:
            self.__terminate()
            return

        try:
            primary: DataInStreamInterface = self._primary_streamer()
            secondary: DataInStreamInterface = self._secondary_streamer()

            if self.__ignore == IgnoreStream.PRIMARY:
                samples = max(1, secondary.available_samples)
                timestamps, data = self.__pull_secondary_data(samples)
                self._data_buffer.write(data)
                if self._timestamp_buffer is not None:
                    self._timestamp_buffer.write(timestamps)
            elif self.__ignore == IgnoreStream.SECONDARY:
                samples = max(1, primary.available_samples)
                timestamps, data = self.__pull_primary_data(samples)
                self._data_buffer.write(data)
                if self._timestamp_buffer is not None:
                    self._timestamp_buffer.write(timestamps)
            else:
                primary_samples = primary.available_samples
                secondary_samples = secondary.available_samples
                if (primary_samples >= self._min_interpolation_samples) and (secondary_samples >= self._min_interpolation_samples):
                    primary_times, primary_data = self.__pull_primary_data(primary_samples)
                    secondary_times, secondary_data = self.__pull_secondary_data(secondary_samples)
                    # Add offset to secondary stream timebase if required
                    if self._delay_time is not None:
                        secondary_times += self._delay_time
                    # Perform interpolation to match the secondary stream onto the primary
                    interpolated_data = self._interpolate_data(secondary_times,
                                                               secondary_data,
                                                               primary_times)
                    # Fill data into combined buffer
                    combined_tmp_buf = self._tmp_combined_data_buf[:primary_samples, :]
                    primary_channels = primary_data.shape[1]
                    combined_tmp_buf[:, :primary_channels] = primary_data
                    combined_tmp_buf[:, primary_channels:] = interpolated_data
                    # Write interpolated combined data into ring buffers
                    self._data_buffer.write(combined_tmp_buf)
                    if self._timestamp_buffer is not None:
                        self._timestamp_buffer.write(primary_times)

            # Call this method again via QTimer for next loop iteration
            self._timer.start(max(10, int(1000. / self._data_buffer.average_rate)))
        except Exception as e:
            self._stop_requested = True
            self.log.exception('DataInStream synchronization went wrong')
            self.__terminate()
            raise

    def __terminate(self) -> None:
        primary: DataInStreamInterface = self._primary_streamer()
        secondary: DataInStreamInterface = self._secondary_streamer()
        try:
            if self.__ignore != IgnoreStream.PRIMARY and primary.module_state() == 'locked':
                primary.stop_stream()
        finally:
            try:
                if self.__ignore != IgnoreStream.SECONDARY and secondary.module_state() == 'locked':
                    secondary.stop_stream()
            finally:
                if self.module_state() == 'locked':
                    self.module_state.unlock()

    @staticmethod
    def _interpolate_data(x: np.ndarray, y: np.ndarray, x_interp: np.ndarray) -> np.ndarray:
        return interpolate.interp1d(x,
                                    y,
                                    kind='linear',
                                    axis=0,
                                    copy=False,
                                    bounds_error=False,
                                    fill_value=(y[0, :], y[-1, :]),
                                    assume_sorted=True)(x_interp)
