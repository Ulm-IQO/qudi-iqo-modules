# -*- coding: utf-8 -*-

"""
Interfuse adding a buffer to a DataInstreamInterface hardware module to allow multiple consumers
reading from the same data stream.
This will slightly adversely affect the performance of read_data_into_buffer and
read_available_data_into_buffer.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['RingBuffer', 'DataInStreamBuffer']

import time
from PySide2 import QtCore
import numpy as np
import logging
from typing import Union, Type, Iterable, Mapping, Optional, Dict, List, Tuple, Sequence, Set, Callable
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.threadmanager import ThreadManager
from qudi.util.mutex import Mutex
from collections.abc import Iterable as _Iterable
from collections.abc import Sized as _Sized
from itertools import chain
from contextlib import contextmanager
from uuid import UUID
from weakref import WeakKeyDictionary


class RingBuffer(_Iterable, _Sized):
    """ Ringbuffer using numpy arrays """
    def __init__(self,
                 size: int,
                 dtype: Optional[type] = float,
                 allow_overwrite: Optional[bool] = True):
        self.__start = 0
        self.__end = 0
        self.__fill_count = 0
        self._allow_overwrite = allow_overwrite
        self._buffer = np.empty(size, dtype)
        self._lock = Mutex()

    @property
    def size(self) -> int:
        return self._buffer.size

    @property
    def full(self) -> bool:
        return self.__fill_count >= self._buffer.size

    @property
    def empty(self) -> bool:
        return self.__fill_count <= 0

    @property
    def free_count(self) -> int:
        return self._buffer.size - self.__fill_count

    @property
    def fill_count(self) -> int:
        return self.__fill_count

    def unwrap(self) -> np.ndarray:
        """ Copy the data from this buffer into unwrapped form """
        with self._lock:
            return np.concatenate(self._filled_chunks)

    def clear(self) -> None:
        """ Clears all data from buffer """
        with self._lock:
            self.__fill_count = self.__start = self.__end = 0

    @contextmanager
    def writeable_raw_buffer(self, size: int):
        """ Contextmanager to directly write to free buffer space inside the ring buffer.
        Will only yield the next chunk of consecutive buffer space, possibly up to requested size.
        Caller is responsible to handle smaller buffer size yielded, e.g. by calling this context
        multiple times.
        """
        with self._lock:
            if self.full:
                if not self._allow_overwrite:
                    raise IndexError('Buffer full and overwrite disabled')
                self._increment(min(self._buffer.size - self.__start, size), 0)
            buffer = self._free_chunk[:size]
            yield buffer
            self._increment(0, buffer.size)

    @contextmanager
    def readable_raw_buffer(self, size: int, pop: bool):
        """ Contextmanager to directly access filled buffer space inside the ring buffer.
        Will only yield the next chunk of consecutive buffer space, possibly up to requested size.
        If requested, the yielded buffer chunk will be freed after exiting the context.
        Caller is responsible to handle smaller buffer size yielded, e.g. by calling this context
        multiple times.
        """
        with self._lock:
            buffer = self._filled_chunks[0][:size]
            yield buffer
            if pop:
                self._increment(buffer.size, 0)

    @property
    def _free_chunk(self) -> np.ndarray:
        """ Returns next free buffer chunk without wrapping around """
        if (self.__start > self.__end) or self.full:
            return self._buffer[self.__end:self.__start]
        else:
            return self._buffer[self.__end:]

    @property
    def _filled_chunks(self) -> Tuple[np.ndarray, np.ndarray]:
        if self.empty:
            return self._buffer[0:0], self._buffer[0:0]
        elif self.__start >= self.__end:
            return self._buffer[self.__start:], self._buffer[:self.__end]
        else:
            return self._buffer[self.__start:self.__end], self._buffer[0:0]

    def _increment(self, start: int, end: int) -> None:
        self.__fill_count += end - start
        self.__start = (self.__start + start) % self._buffer.size
        self.__end = (self.__end + end) % self._buffer.size

    def __len__(self) -> int:
        return self.__fill_count

    def __iter__(self):
        return chain(*[iter(chunk) for chunk in self._filled_chunks])

    def __array__(self) -> np.ndarray:
        # numpy compatibility
        return self.unwrap()


class DataInStreamReader(QtCore.QObject):
    """ Worker class for periodically reading data from streaming hardware and distributing it to
    all ring buffers
    """
    def __init__(self, streamer: DataInStreamInterface, buffers: Dict[UUID, RingBuffer]):
        super().__init__()
        self._streamer = streamer
        self._buffers = buffers
        self._tmp_buffer = np.empty(
            len(self._streamer.active_channels) * self._streamer.channel_buffer_size,
            dtype=self._streamer.constraints.data_type
        )
        self._stop_requested = False

    def run(self) -> None:
        """ The worker task. Runs until an external thread calls self.stop() """
        interval = 1 / self._streamer.sample_rate
        while not self._stop_requested:
            time.sleep(interval)
            self._pull_data()

    def stop(self) -> None:
        self._stop_requested = True

    def _pull_data(self) -> None:
        # ToDo: Handle timestamps
        samples_per_channel = self._streamer.read_available_data_into_buffer(self._tmp_buffer)
        if samples_per_channel > 0:
            sample_count = len(self._streamer.active_channels) * samples_per_channel
            for buffer in self._buffers.values():
                written = 0
                while written < sample_count:
                    with buffer.writeable_raw_buffer(sample_count - written) as raw_buf:
                        raw_buf[:] = self._tmp_buffer[written:written + raw_buf.size]
                        written += raw_buf.size


class DataInStreamBufferProxy:
    """ Proxy class managing thread-safe hardware access from multiple DataInStreamBuffer instances.
    """
    _ALLOW_OVERWRITE = True

    def __init__(self, streamer: DataInStreamInterface):
        self._streamer = streamer
        self._lock = Mutex()
        self._run_state_callbacks: Dict[UUID, Callable[[bool], None]] = WeakKeyDictionary()
        self._buffers: Dict[UUID, RingBuffer] = WeakKeyDictionary()
        self._reader: Union[None, DataInStreamReader] = None
        self._reader_thread_name = f'DataInStreamBufferProxy-{str(self._streamer.module_uuid)}'

    def register_consumer(self, uuid: UUID, run_state_cb: Callable[[bool], None]) -> None:
        """ Register a new data consumer, e.g. a DataInStreamBuffer instance. """
        with self._lock:
            self._run_state_callbacks[uuid] = run_state_cb
            self._buffers[uuid] = RingBuffer(
                size=len(self._streamer.active_channels) * self._streamer.channel_buffer_size,
                dtype=self._streamer.constraints.data_type,
                allow_overwrite=self._ALLOW_OVERWRITE
            )
            run_state_cb(self._streamer.module_state() == 'locked')

    def unregister_consumer(self, uuid: UUID) -> None:
        """ Unregisters a previously registered data consumer, e.g. a DataInStreamBuffer instance.
        """
        with self._lock:
            self._run_state_callbacks.pop(uuid, None)
            self._buffers.pop(uuid, None)

    def available_samples(self, uuid: UUID) -> int:
        """ Available samples per channel for a certain consumer """
        try:
            return self._buffers[uuid].fill_count // len(self._streamer.active_channels)
        except KeyError:
            return 0

    @property
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.
        """
        return self._streamer.sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <channel_buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self._streamer.channel_buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        return self._streamer.streaming_mode

    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        return self._streamer.active_channels

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        # ToDo: Handle timestamps
        with self._lock:
            if (self._streamer.active_channels != active_channels) or (
                    self._streamer.streaming_mode != streaming_mode) or (
                    self._streamer.channel_buffer_size != channel_buffer_size) or (
                    self._streamer.sample_rate != sample_rate):
                if self._streamer.module_state() != 'idle':
                    raise RuntimeError('Streamer not idle')
                self._streamer.configure(active_channels,
                                         streaming_mode,
                                         channel_buffer_size,
                                         sample_rate)

                buffer_size = len(self._streamer.active_channels) * self._streamer.channel_buffer_size
                dtype = self._streamer.constraints.data_type
                for uid in list(self._buffers):
                    self._buffers[uid] = RingBuffer(size=buffer_size,
                                                    dtype=dtype,
                                                    allow_overwrite=self._ALLOW_OVERWRITE)

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        with self._lock:
            try:
                if self._streamer.module_state() == 'idle':
                    self._streamer.start_stream()
                if self._reader is None:
                    self._reader = DataInStreamReader(self._streamer, self._buffers)
                    tm = ThreadManager.instance()
                    thread = tm.get_new_thread(self._reader_thread_name)
                    self._reader.moveToThread(thread)
                    thread.started.connect(self._reader.run)
                    thread.start()
            finally:
                running = self._streamer.module_state() == 'locked'
                for callback in self._run_state_callbacks.values():
                    callback(running)

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        with self._lock:
            try:
                if self._streamer.module_state() == 'locked':
                    self._streamer.stop_stream()
            finally:
                try:
                    self._reader.stop()
                except AttributeError:
                    pass
                else:
                    tm = ThreadManager.instance()
                    tm.quit_thread(self._reader_thread_name)
                    tm.join_thread(self._reader_thread_name)
                finally:
                    self._reader = None
                    running = self._streamer.module_state() == 'locked'
                    for callback in self._run_state_callbacks.values():
                        callback(running)

    def read_data_into_buffer(self,
                              uuid: UUID,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        self._read_data_into_buffer(uuid, data_buffer, samples_per_channel, timestamp_buffer)

    def _read_data_into_buffer(self,
                              uuid: UUID,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        # ToDo: Handle timestamps
        requested_samples = len(self._streamer.active_channels) * samples_per_channel
        poll_pause = 2 / self._streamer.sample_rate  # minimal waiting time for new samples
        buffer = self._buffers[uuid]
        # read data until desired amount is available. Raise RuntimeError if stream is stopped.
        read_samples = 0
        while read_samples < requested_samples:
            with buffer.readable_raw_buffer(requested_samples - read_samples, pop=True) as buf:
                if buf.size > 0:
                    data_buffer[read_samples:read_samples + buf.size] = buf[:]
                    read_samples += buf.size
            if buffer.fill_count < (requested_samples - read_samples):
                if self._streamer.module_state() == 'locked':
                    time.sleep(poll_pause)
                else:
                    raise RuntimeError('Streamer is not running.')

    def read_available_data_into_buffer(self,
                                        uuid: UUID,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        try:
            available = min(self._buffers[uuid].fill_count, data_buffer.size) // len(
                self._streamer.active_channels
            )
        except KeyError:
            return 0
        self._read_data_into_buffer(uuid, data_buffer, available, timestamp_buffer)
        return available

    def read_data(self,
                  uuid: UUID,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        # ToDo: Handle timestamps
        channel_count = len(self._streamer.active_channels)
        if samples_per_channel is None:
            buffer_size = (self._buffers[uuid].fill_count // channel_count) * channel_count
        else:
            buffer_size = samples_per_channel * channel_count
        data = np.empty(buffer_size, dtype=self._streamer.constraints.data_type)
        timestamps = None
        self._read_data_into_buffer(uuid, data, buffer_size // channel_count, timestamps)
        return data, timestamps

    def read_single_point(self, uuid: UUID) -> Tuple[np.ndarray, Union[None, np.float64]]:
        data, timestamps = self.read_data(uuid, 1)
        return data, None if timestamps is None else timestamps[0]


class DataInStreamBuffer(DataInStreamInterface):
    """ Interfuse adding a buffer to a DataInstreamInterface hardware module to allow multiple
    consumers reading from the same data stream.
    This will slightly adversely affect the performance of read_data_into_buffer and
    read_available_data_into_buffer.
    """

    _streamer = Connector(name='streamer', interface='DataInStreamInterface')

    _buffer_proxies: Dict[UUID, DataInStreamBufferProxy] = WeakKeyDictionary()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._streamer_uid: Union[None, UUID] = None

    def on_activate(self) -> None:
        streamer = self._streamer()
        # If there is already a buffer proxy for the connected hardware present, just register
        # this interfuse as additional consumer. Otherwise, also create the proxy first.
        self._streamer_uid = streamer.module_uuid
        try:
            proxy = self._buffer_proxies[self._streamer_uid]
        except KeyError:
            proxy = DataInStreamBufferProxy(streamer=streamer)
            self._buffer_proxies[self._streamer_uid] = proxy
        proxy.register_consumer(self.module_uuid, self._run_state_callback)

    def on_deactivate(self) -> None:
        if self.module_state() == 'locked':
            self.module_state.unlock()
        # Unregister this interfuse as consumer from the buffer roxy.
        try:
            proxy = self._buffer_proxies[self._streamer_uid]
        except KeyError:
            pass
        else:
            proxy.unregister_consumer(self.module_uuid)
        finally:
            self._streamer_uid = None

    @property
    def constraints(self) -> DataInStreamConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
        return self._streamer().constraints

    @property
    def available_samples(self) -> int:
        """ Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        It must be ensured that each channel can provide at least the number of samples returned
        by this property.
        """
        return self._buffer_proxies[self._streamer_uid].available_samples(self.module_uuid)

    @property
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.
        """
        return self._buffer_proxies[self._streamer_uid].sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <channel_buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self._buffer_proxies[self._streamer_uid].channel_buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        return self._buffer_proxies[self._streamer_uid].streaming_mode

    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        return self._buffer_proxies[self._streamer_uid].active_channels

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        self._buffer_proxies[self._streamer_uid].configure(active_channels,
                                                              streaming_mode,
                                                              channel_buffer_size,
                                                              sample_rate)

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        self._buffer_proxies[self._streamer_uid].start_stream()

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        self._buffer_proxies[self._streamer_uid].stop_stream()

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        """ Read data from the stream buffer into a 1D numpy array given as parameter.
        Samples of all channels are stored interleaved in contiguous memory.
        In case of a multidimensional buffer array, this buffer will be flattened before written
        into.
        The 1D data_buffer can be unraveled into channel and sample indexing with:

            data_buffer.reshape([<samples_per_channel>, <channel_count>])

        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        able to hold at least <samples_per_channel> items:

        This function is blocking until the required number of samples has been acquired.
        """
        self._buffer_proxies[self._streamer_uid].read_data_into_buffer(self.module_uuid,
                                                                          data_buffer,
                                                                          samples_per_channel,
                                                                          timestamp_buffer)

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
        return self._buffer_proxies[self._streamer_uid].read_available_data_into_buffer(
            self.module_uuid,
            data_buffer,
            timestamp_buffer
        )

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
        return self._buffer_proxies[self._streamer_uid].read_data(
            self.module_uuid,
            samples_per_channel
        )

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
        return self._buffer_proxies[self._streamer_uid].read_single_point(
            self.module_uuid,
        )

    def _run_state_callback(self, running: bool) -> None:
        if running and (self.module_state() == 'idle'):
            self.module_state.lock()
        elif (not running) and (self.module_state() == 'locked'):
            self.module_state.unlock()
