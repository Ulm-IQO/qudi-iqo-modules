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

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.util.ringbuffer import RingBuffer, InterleavedRingBuffer
from qudi.util.ringbuffer import RingBufferReader, SyncRingBufferReader
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamConstraints, DataInStreamInterface
from typing import Tuple, Optional, Sequence, Union, List

# ToDo: Add config option for settings behaviour


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
        self._stop_requested = True
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)

        # Serves as time base reference if no timestamps are returned by the hardware
        self._primary_sample_count = 0
        self._secondary_sample_count = 0

        # sample buffers
        self._data_buffer: InterleavedRingBuffer = None
        self._timestamp_buffer: Union[None, RingBuffer] = None
        self._tmp_combined_data_buf: np.ndarray = None
        self._tmp_primary_data_buf: np.ndarray = None
        self._tmp_secondary_data_buf: np.ndarray = None
        self._tmp_primary_timestamp_buf: np.ndarray = None
        self._tmp_secondary_timestamp_buf: np.ndarray = None

        # combined streamer constraints
        self._constraints: DataInStreamConstraints = None

    def on_activate(self):
        self._stop_requested = True
        self._primary_sample_count = self._secondary_sample_count = 0
        self._constraints = self.__combine_constraints()

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

    def __combine_constraints(self) -> DataInStreamConstraints:
        constr_1 = self._primary_streamer().constraints
        constr_2 = self._secondary_streamer().constraints
        timings = [constr_1.sample_timing, constr_2.sample_timing]
        if any(timing == SampleTiming.TIMESTAMP for timing in timings):
            timing = SampleTiming.TIMESTAMP
        elif any(timing == SampleTiming.RANDOM for timing in timings):
            raise RuntimeError(f'DataInStreamSync interfuse does not support streamers with '
                               f'{SampleTiming.RANDOM} sample_timing')
        else:
            timing = SampleTiming.CONSTANT
        return DataInStreamConstraints(
            channel_units={**constr_1.channel_units, **constr_2.channel_units},
            sample_timing=timing,
            streaming_modes=[StreamingMode.CONTINUOUS],
            data_type=np.float64,
            channel_buffer_size=constr_1.channel_buffer_size.copy(),
            sample_rate=constr_1.sample_rate.copy()
        )

    def __create_buffers(self) -> None:
        primary: DataInStreamInterface = self._primary_streamer()
        secondary: DataInStreamInterface = self._secondary_streamer()
        primary_buffer_size = primary.channel_buffer_size
        secondary_buffer_size = secondary.channel_buffer_size
        primary_channels = len(primary.active_channels)
        secondary_channels = len(secondary.active_channels)
        total_channels = primary_channels + secondary_channels
        primary_dtype = primary.constraints.data_type
        secondary_dtype = secondary.constraints.data_type
        # Create ringbuffers
        self._data_buffer = InterleavedRingBuffer(interleave_factor=total_channels,
                                                  size=primary_buffer_size,
                                                  dtype=np.float64,
                                                  allow_overwrite=self._allow_overwrite)
        if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._timestamp_buffer = RingBuffer(size=primary_buffer_size,
                                                dtype=np.float64,
                                                allow_overwrite=self._allow_overwrite)
        else:
            self._timestamp_buffer = None
        # Create intermediate buffer arrays to minimize memory allocation on each read at the
        # expense or more memory consumption
        self._tmp_combined_data_buf = np.empty([primary_buffer_size, total_channels],
                                               dtype=np.float64)
        self._tmp_primary_data_buf = np.empty([primary_buffer_size, primary_channels],
                                              dtype=primary_dtype)
        self._tmp_secondary_data_buf = np.empty([secondary_buffer_size, secondary_channels],
                                                dtype=secondary_dtype)
        self._tmp_primary_timestamp_buf = np.empty(primary_buffer_size, dtype=np.float64)
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
        return self._primary_streamer().sample_rate

    @property
    def channel_buffer_size(self) -> int:
        return self._data_buffer.size

    @property
    def streaming_mode(self) -> StreamingMode:
        return StreamingMode.CONTINUOUS

    @property
    def active_channels(self) -> List[str]:
        return [*self._primary_streamer().active_channels,
                *self._secondary_streamer().active_channels]

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        with self._lock:
            if self.module_state() == 'locked':
                raise RuntimeError('Streamer is running.')
            primary = self._primary_streamer()
            secondary = self._secondary_streamer()
            primary_channels = primary.constraints.channel_units
            secondary_channels = secondary.constraints.channel_units
            primary.configure(
                active_channels=[ch for ch in active_channels if ch in primary_channels],
                streaming_mode=StreamingMode.CONTINUOUS,
                channel_buffer_size=channel_buffer_size,
                sample_rate=sample_rate
            )
            secondary.configure(
                active_channels=[ch for ch in active_channels if ch in secondary_channels],
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
                    self.__create_buffers()
                    self._timer.setInterval(max(10, int(1000. / self._data_buffer.average_rate)))
                    if primary.module_state() == 'idle':
                        primary.start_stream()
                    if secondary.module_state() == 'idle':
                        secondary.start_stream()
                    self._stop_requested = False
                    self._sigStartLoop.emit()
                except Exception:
                    self.module_state.unlock()
                    self._stop_requested = True
                    raise

    def stop_stream(self) -> None:
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

    def _pull_data(self) -> None:
        """ This method synchronizes (interpolates) the available data from both the timeseries
        logic and the wavemeter hardware module. It runs repeatedly by being connected to a QTimer timeout signal from the time series (sigNewRawData).
        Note: new_count_timing is currently unused, but might be used for a more elaborate synchronization.
        #TODO Assure that the timing below is synchronized at an acceptable level and saving of raw data
        """
        primary: DataInStreamInterface = self._primary_streamer()
        secondary: DataInStreamInterface = self._secondary_streamer()

        # Break loop if stop is requested
        if self._stop_requested:
            self.__terminate()
            return

        try:
            primary_available = primary.available_samples
            secondary_available = secondary.available_samples

            # Only act if both streams deliver samples at this time. Skip this iteration if not.
            if (primary_available >= self._min_interpolation_samples) and (
                    secondary_available >= self._min_interpolation_samples):
                primary_has_timestamps = primary.constraints.sample_timing == SampleTiming.TIMESTAMP
                secondary_has_timestamps = secondary.constraints.sample_timing == SampleTiming.TIMESTAMP

                # Truncate temp buffers to match samples to process
                primary_tmp_data = self._tmp_primary_data_buf[:primary_available, :]
                secondary_tmp_data = self._tmp_secondary_data_buf[:secondary_available, :]
                combined_tmp_buf = self._tmp_combined_data_buf[:primary_available, :]
                primary_tmp_times = self._tmp_primary_timestamp_buf[:primary_available]
                secondary_tmp_times = self._tmp_secondary_timestamp_buf[:secondary_available]

                # Read data from streamers
                primary.read_data_into_buffer(data_buffer=primary_tmp_data,
                                              samples_per_channel=primary_available,
                                              timestamp_buffer=primary_tmp_times)
                secondary.read_data_into_buffer(data_buffer=secondary_tmp_data,
                                                samples_per_channel=secondary_available,
                                                timestamp_buffer=secondary_tmp_times)

                # Create timestamps if not present
                if not primary_has_timestamps:
                    primary_tmp_times = np.arange(self._primary_sample_count,
                                                  self._primary_sample_count + primary_available,
                                                  dtype=np.float64)
                    primary_tmp_times /= primary.sample_rate
                    self._primary_sample_count += primary_available
                if not secondary_has_timestamps:
                    secondary_tmp_times = np.arange(
                        self._secondary_sample_count,
                        self._secondary_sample_count + secondary_available,
                        dtype=np.float64
                    )
                    secondary_tmp_times /= secondary.sample_rate
                    self._secondary_sample_count += secondary_available

                # Add offset to secondary stream timebase if required
                if self._delay_time is not None:
                    secondary_tmp_times += self._delay_time

                # Here the interpolation is performed to match the secondary stream onto the primary
                interpolated_data = self._interpolate_data(secondary_tmp_times,
                                                           secondary_tmp_data,
                                                           primary_tmp_times)

                # Fill data into combined buffer
                primary_channels = primary_tmp_data.shape[1]
                combined_tmp_buf[:, :primary_channels] = primary_tmp_data
                combined_tmp_buf[:, primary_channels:] = interpolated_data
                # Write (interpolated) data into ring buffers
                self._data_buffer.write(combined_tmp_buf)
                if self._timestamp_buffer is not None:
                    self._timestamp_buffer.write(primary_tmp_times)

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
        if self.module_state() == 'locked':
            self.module_state.unlock()
        if primary.module_state() == 'locked':
            primary.stop_stream()
        if secondary.module_state() == 'locked':
            secondary.stop_stream()

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
