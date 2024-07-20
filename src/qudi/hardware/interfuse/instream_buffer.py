# -*- coding: utf-8 -*-

"""
Interfuse adding a buffer to a DataInstreamInterface hardware module to allow multiple consumers
reading from the same data stream.

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

__all__ = ['RingBuffer', 'DataInStreamBuffer', 'DataInStreamReader', 'DataInStreamBufferProxy']

import time
import numpy as np
from uuid import UUID
from itertools import chain
from weakref import WeakKeyDictionary
from collections.abc import Sized as _Sized
from collections.abc import Iterable as _Iterable
from typing import Union, Optional, Dict, List, Tuple, Sequence, Callable
from PySide2.QtCore import QObject

from qudi.util.mutex import Mutex
from qudi.core.connector import Connector
from qudi.core.threadmanager import ThreadManager
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints


class RingBuffer(_Iterable, _Sized):
    """ Ringbuffer using numpy arrays. Is fairly thread-safe. """
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

    def read(self, size: int, buffer: Optional[np.ndarray] = None) -> np.ndarray:
        """ ToDo: Document """
        with self._lock:
            if buffer is None:
                buffer = np.empty(size, dtype=self._buffer.dtype.type)
            read = 0
            for chunk in self._filled_chunks:
                chunk_size = min(chunk.size, size - read)
                buffer[read:read + chunk_size] = chunk[:chunk_size]
                read += chunk_size
            self._increment(read, 0)
            return buffer[:read]

    def write(self, data: np.ndarray) -> bool:
        """ ToDo: Document """
        with self._lock:
            written = 0
            overflown = False
            while written < data.size:
                missing = data.size - written
                if self.full:
                    if not self._allow_overwrite:
                        raise IndexError('Buffer full and overwrite disabled')
                    overflown = True
                    self._increment(min(self._buffer.size - self.__start, missing), 0)
                chunk = self._free_chunk[:missing]
                chunk[:] = data[written:written + chunk.size]
                written += chunk.size
                self._increment(0, chunk.size)
            return overflown

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


class DataInStreamReader(QObject):
    """ Worker class for periodically reading data from streaming hardware and distributing it to
    all ring buffers
    """
    def __init__(self,
                 streamer: DataInStreamInterface,
                 buffers: Dict[UUID, RingBuffer],
                 timestamp_buffers: Dict[UUID, RingBuffer]):
        super().__init__()
        self._streamer = streamer
        self._buffers = buffers
        self._timestamp_buffers = timestamp_buffers
        self._tmp_buffer = np.empty(
            len(self._streamer.active_channels) * self._streamer.channel_buffer_size,
            dtype=self._streamer.constraints.data_type
        )
        if self._streamer.constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._tmp_timestamp_buffer = np.empty(self._streamer.channel_buffer_size,
                                                  dtype=self._streamer.constraints.data_type)
        else:
            self._tmp_timestamp_buffer = None
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
        samples_per_channel = self._streamer.read_available_data_into_buffer(
            self._tmp_buffer,
            self._tmp_timestamp_buffer
        )
        if samples_per_channel > 0:
            sample_count = len(self._streamer.active_channels) * samples_per_channel
            for buffer in self._buffers.values():
                buffer.write(self._tmp_buffer[:sample_count])
            if self._tmp_timestamp_buffer is not None:
                for buffer in self._timestamp_buffers.values():
                    buffer.write(self._tmp_timestamp_buffer[:samples_per_channel])


class DataInStreamBufferProxy:
    """ Proxy class managing hardware access from multiple DataInStreamBuffer instances.
    Periodically pulls data from hardware in a separate thread and distributes it to all registered
    consumers.
    """
    _ALLOW_OVERWRITE = True
    _WAIT_INTERVAL = 0.05  # default value

    def __init__(self, streamer: DataInStreamInterface):
        self._streamer = streamer
        self._lock = Mutex()

        self._run_state_callbacks: Dict[UUID, Callable[[bool], None]] = WeakKeyDictionary()
        self._buffers: Dict[UUID, RingBuffer] = WeakKeyDictionary()
        self._timestamp_buffers: Dict[UUID, RingBuffer] = WeakKeyDictionary()

        self._reader: Union[None, DataInStreamReader] = None
        self._reader_thread_name = f'DataInStreamBufferProxy-{str(self._streamer.module_uuid)}'

        # The time to wait for new samples (normally depends on sample_rate)
        if self._streamer.constraints.sample_timing == SampleTiming.RANDOM:
            self._wait_interval = self._WAIT_INTERVAL
        else:
            self._wait_interval = 1 / self._streamer.sample_rate

    def register_consumer(self, uuid: UUID, run_state_cb: Callable[[bool], None]) -> None:
        """ Register a new data consumer, e.g. a DataInStreamBuffer instance. """
        with self._lock:
            self._run_state_callbacks[uuid] = run_state_cb
            self._buffers[uuid] = RingBuffer(size=self.raw_buffer_size,
                                             dtype=self._streamer.constraints.data_type,
                                             allow_overwrite=self._ALLOW_OVERWRITE)
            if self._streamer.constraints.sample_timing == SampleTiming.TIMESTAMP:
                self._timestamp_buffers[uuid] = RingBuffer(size=self.channel_buffer_size,
                                                           dtype=np.float64,
                                                           allow_overwrite=self._ALLOW_OVERWRITE)
            run_state_cb(self._streamer.module_state() == 'locked')

    def unregister_consumer(self, uuid: UUID) -> None:
        """ Unregisters a previously registered data consumer, e.g. a DataInStreamBuffer instance.
        """
        with self._lock:
            self._run_state_callbacks.pop(uuid, None)
            self._buffers.pop(uuid, None)
            self._timestamp_buffers.pop(uuid, None)

    def available_samples(self, uuid: UUID) -> int:
        """ Available samples per channel for a certain consumer UID """
        return self._buffers[uuid].fill_count // self.channel_count

    @property
    def channel_count(self) -> int:
        return len(self._streamer.active_channels)

    @property
    def raw_buffer_size(self) -> int:
        return self.channel_count * self._streamer.channel_buffer_size

    @property
    def sample_rate(self) -> float:
        return self._streamer.sample_rate

    @property
    def channel_buffer_size(self) -> int:
        return self._streamer.channel_buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        return self._streamer.streaming_mode

    @property
    def active_channels(self) -> List[str]:
        return self._streamer.active_channels

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        with self._lock:
            if (self.active_channels != active_channels) or (
                    self.streaming_mode != streaming_mode) or (
                    self.channel_buffer_size != channel_buffer_size) or (
                    self.sample_rate != sample_rate):
                if self._streamer.module_state() != 'idle':
                    raise RuntimeError('Streamer not idle')

                self._streamer.configure(active_channels,
                                         streaming_mode,
                                         channel_buffer_size,
                                         sample_rate)

                if self._streamer.constraints.sample_timing == SampleTiming.RANDOM:
                    self._wait_interval = self._WAIT_INTERVAL
                else:
                    self._wait_interval = 1 / self.sample_rate

                dtype = self._streamer.constraints.data_type
                timestamped = self._streamer.constraints.sample_timing == SampleTiming.TIMESTAMP
                for uid in list(self._buffers):
                    self._buffers[uid] = RingBuffer(size=self.raw_buffer_size,
                                                    dtype=dtype,
                                                    allow_overwrite=self._ALLOW_OVERWRITE)
                    if timestamped:
                        self._timestamp_buffers[uid] = RingBuffer(
                            size=self.channel_buffer_size,
                            dtype=np.float64,
                            allow_overwrite=self._ALLOW_OVERWRITE
                        )
                    else:
                        self._timestamp_buffers.pop(uid, None)

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        with self._lock:
            try:
                if self._streamer.module_state() == 'idle':
                    self._streamer.start_stream()
                if self._reader is None:
                    self._reader = DataInStreamReader(self._streamer,
                                                      self._buffers,
                                                      self._timestamp_buffers)
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
        data = self._buffers[uuid]
        timestamps = self._timestamp_buffers.get(uuid, None)
        # read data until desired amount is available. Raise RuntimeError if stream is stopped.
        read = 0
        while read < samples_per_channel:
            available = self.available_samples(uuid)
            if available < 1:
                if self._streamer.module_state() != 'locked':
                    raise RuntimeError('Streamer is not running.')
                time.sleep(self._wait_interval)
            else:
                offset = read * self.channel_count
                chunk_size = data.read(available * self.channel_count,
                                       data_buffer[offset:]).size // self.channel_count
                if timestamps is not None:
                    timestamps.read(chunk_size, timestamp_buffer[read:])
                read += chunk_size

    def read_available_data_into_buffer(self,
                                        uuid: UUID,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:

        available = min(self.available_samples(uuid), data_buffer.size // self.channel_count)
        self.read_data_into_buffer(uuid, data_buffer, available, timestamp_buffer)
        return available

    def read_data(self,
                  uuid: UUID,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        if samples_per_channel is None:
            samples_per_channel = self.available_samples(uuid)
        data = np.empty(samples_per_channel * self.channel_count,
                        dtype=self._streamer.constraints.data_type)
        if self._streamer.constraints.sample_timing == SampleTiming.TIMESTAMP:
            timestamps = np.empty(samples_per_channel, dtype=np.float64)
        else:
            timestamps = None
        self.read_data_into_buffer(uuid, data, samples_per_channel, timestamps)
        return data, timestamps

    def read_single_point(self, uuid: UUID) -> Tuple[np.ndarray, Union[None, np.float64]]:
        data, timestamps = self.read_data(uuid, 1)
        return data, None if timestamps is None else timestamps[0]


class DataInStreamBuffer(DataInStreamInterface):
    """ Interfuse adding a buffer to a DataInstreamInterface hardware module to allow multiple
    consumers reading from the same data stream.
    Using this will adversely affect the performance of the interface compared to a
    single-producer/single-consumer scenario.

    Make sure all modules that need connection to the same streaming hardware are connected via
    their own instance of this module, e.g.:

              Logic1                 Logic2                Logic3
                |                      |                      |
                v                      v                      v
        DataInStreamBuffer1    DataInStreamBuffer2    DataInStreamBuffer3
                |                      |                      |
                |                      v                      |
                ------------> DataInStreamHardware <-----------
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
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.constraints """
        return self._streamer().constraints

    @property
    def available_samples(self) -> int:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.available_samples """
        return self._buffer_proxies[self._streamer_uid].available_samples(self.module_uuid)

    @property
    def sample_rate(self) -> float:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.sample_rate """
        return self._buffer_proxies[self._streamer_uid].sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.channel_buffer_size
        """
        return self._buffer_proxies[self._streamer_uid].channel_buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.streaming_mode """
        return self._buffer_proxies[self._streamer_uid].streaming_mode

    @property
    def active_channels(self) -> List[str]:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.active_channels """
        return self._buffer_proxies[self._streamer_uid].active_channels

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.configure """
        self._buffer_proxies[self._streamer_uid].configure(active_channels,
                                                           streaming_mode,
                                                           channel_buffer_size,
                                                           sample_rate)

    def start_stream(self) -> None:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.start_stream """
        self._buffer_proxies[self._streamer_uid].start_stream()

    def stop_stream(self) -> None:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.stop_stream """
        self._buffer_proxies[self._streamer_uid].stop_stream()

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.read_data_into_buffer
        """
        self._buffer_proxies[self._streamer_uid].read_data_into_buffer(self.module_uuid,
                                                                          data_buffer,
                                                                          samples_per_channel,
                                                                          timestamp_buffer)

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        """ See:
        qudi.interface.data_instream_interface.DataInStreamInterface.read_available_data_into_buffer
        """
        return self._buffer_proxies[self._streamer_uid].read_available_data_into_buffer(
            self.module_uuid,
            data_buffer,
            timestamp_buffer
        )

    def read_data(self,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.read_data """
        return self._buffer_proxies[self._streamer_uid].read_data(
            self.module_uuid,
            samples_per_channel
        )

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.read_single_point """
        return self._buffer_proxies[self._streamer_uid].read_single_point(
            self.module_uuid,
        )

    def _run_state_callback(self, running: bool) -> None:
        """ Callback that is called whenever any DataInStreamBuffer is starting/stopping the
        connected streaming hardware.
        Sets the module_state of this interfuse accordingly.
        """
        if running and (self.module_state() == 'idle'):
            self.module_state.lock()
        elif (not running) and (self.module_state() == 'locked'):
            self.module_state.unlock()
