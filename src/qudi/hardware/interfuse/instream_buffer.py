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

__all__ = ['DataInStreamBuffer', 'DataInStreamDistributionWorker',
           'DataInStreamMultiConsumerDelegate']

import time
import numpy as np
from PySide2 import QtCore
from uuid import UUID
from logging import getLogger
from weakref import WeakKeyDictionary
from typing import Union, Optional, Dict, List, Tuple, Sequence, Mapping, Any, MutableMapping

from qudi.util.mutex import Mutex
from qudi.util.network import netobtain
from qudi.util.thread_exception_watchdog import threaded_exception_watchdog
from qudi.util.ringbuffer import RingBuffer, InterleavedRingBuffer, RingBufferReader, SyncRingBufferReader
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.threadmanager import ThreadManager
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints


class DataInStreamDistributionWorker(QtCore.QObject):
    """ Worker class for periodically reading data from streaming hardware and distributing copies
    to a number of ring buffers
    """
    def __init__(
            self,
            streamer: DataInStreamInterface,
            buffers: Mapping[Any, Union[None, Tuple[InterleavedRingBuffer, Union[None, RingBuffer]]]]
    ):
        if netobtain(streamer.constraints.sample_timing) == SampleTiming.RANDOM:
            raise ValueError(f'{DataInStreamInterface.__name__} hardware with '
                             f'{SampleTiming.RANDOM} is not supported')
        super().__init__()
        self._streamer = streamer
        self._buffers = buffers
        self._tmp_buffer = self._tmp_timestamp_buffer = None
        self._stop_requested = False
        if type(self._streamer.constraints) == type(netobtain(self._streamer.constraints)):
            self.__is_remote_streamer = False
        else:
            self.__is_remote_streamer = True
        self.__has_timestamps = netobtain(self._streamer.constraints.sample_timing) == SampleTiming.TIMESTAMP
        self._logger = getLogger(self.__class__.__name__)

    def run(self) -> None:
        """ The worker task. Runs until an external thread calls self.stop() """
        with threaded_exception_watchdog(self._logger):
            interval = max(0.01, 1. / self._streamer.sample_rate)
            channel_count = len(self._streamer.active_channels)
            channel_buffer_size = self._streamer.channel_buffer_size
            dtype = self._streamer.constraints.data_type.__name__
            # Setup buffers
            if not self.__is_remote_streamer:
                self._tmp_buffer = np.empty(channel_count * channel_buffer_size, dtype=dtype)
                if self.__has_timestamps:
                    self._tmp_timestamp_buffer = np.empty(channel_buffer_size, dtype=dtype)
            # Loop until stopped
            while not self._stop_requested:
                time.sleep(interval)
                self._pull_data(channel_count)
            # delete tmp buffers
            self._tmp_buffer = self._tmp_timestamp_buffer = None

    def stop(self) -> None:
        self._stop_requested = True

    def _pull_data(self, channel_count: int) -> None:
        samples_per_channel = self._streamer.available_samples
        if samples_per_channel > 0:
            # Use a shared temp buffer if the streamer is a local module
            if self.__is_remote_streamer:
                tmp_data, tmp_timestamps = self._streamer.read_data(samples_per_channel)
                tmp_data = netobtain(tmp_data)
                if tmp_timestamps is not None:
                    tmp_timestamps = netobtain(tmp_timestamps)
            else:
                self._streamer.read_data_into_buffer(
                    data_buffer=self._tmp_buffer,
                    samples_per_channel=samples_per_channel,
                    timestamp_buffer=self._tmp_timestamp_buffer
                )
                tmp_data = self._tmp_buffer[:samples_per_channel * channel_count]
                if self._tmp_timestamp_buffer is None:
                    tmp_timestamps = None
                else:
                    tmp_timestamps = self._tmp_timestamp_buffer[:samples_per_channel]
            for buffers in self._buffers.values():
                if buffers is not None:
                    data, timestamps = buffers
                    data.write(tmp_data)
                    if tmp_timestamps is not None:
                        timestamps.write(tmp_timestamps)


class DataInStreamMultiConsumerDelegate(QtCore.QObject):
    """ Proxy class managing hardware access from multiple DataInStreamBuffer instances.
    Periodically pulls data from hardware in a separate thread and distributes it to all registered
    consumers.
    """
    _lock: Mutex
    _read_locks: MutableMapping[UUID, Mutex]
    _streamer: DataInStreamInterface
    _buffers: MutableMapping[UUID, Union[None, Tuple[InterleavedRingBuffer, Union[None, RingBuffer]]]]
    __worker: Union[None, DataInStreamDistributionWorker]
    _worker_thread: str

    def __init__(self,
                 streamer: DataInStreamInterface,
                 allow_overwrite: Optional[bool] = False,
                 max_poll_rate: Optional[float] = 100.,
                 parent: Optional[QtCore.QObject] = None):
        super().__init__(parent=parent)

        self._lock = Mutex()
        self._read_locks = WeakKeyDictionary()

        self._streamer = streamer
        self._allow_overwrite = allow_overwrite
        self.__has_timestamps = netobtain(self._streamer.constraints.sample_timing) == SampleTiming.TIMESTAMP
        self._max_poll_rate = max_poll_rate
        self._buffers = WeakKeyDictionary()

        self.__worker = None
        self._worker_thread = f'{DataInStreamDistributionWorker.__name__}-{str(streamer.module_uuid)}'

    def register_consumer(self, uuid: UUID) -> None:
        """ Register a new data consumer, e.g. a DataInStreamBuffer instance. """
        with self._lock:
            self._buffers[uuid] = None
            self._read_locks[uuid] = Mutex()

    def unregister_consumer(self, uuid: UUID) -> None:
        """ Unregisters a previously registered data consumer, e.g. a DataInStreamBuffer instance.
        """
        with self._lock:
            mutex = self._read_locks.get(uuid, None)
            if mutex is not None:
                with mutex:
                    self._buffers.pop(uuid, None)
                    self._read_locks.pop(uuid, None)

    def available_samples(self, uuid: UUID) -> int:
        """ Available samples per channel for a certain consumer UID """
        with self._read_locks[uuid]:
            buffers = self._buffers[uuid]
            if buffers is None:
                return 0
            else:
                return buffers[0].fill_count

    def _config_equal(self,
                      active_channels: Sequence[str],
                      streaming_mode: Union[StreamingMode, int],
                      channel_buffer_size: int,
                      sample_rate: float) -> bool:
        return (active_channels == self._streamer.active_channels) and \
            (streaming_mode == netobtain(self._streamer.streaming_mode)) and \
            (channel_buffer_size == self._streamer.channel_buffer_size) and \
            (sample_rate == self._streamer.sample_rate)

    def _create_buffer(self, uuid: UUID) -> None:
        with self._read_locks[uuid]:
            if self._buffers[uuid] is not None:
                raise RuntimeError(f'Can not create buffers for {uuid}. Delete old buffers first.')
            channel_count = len(self._streamer.active_channels)
            channel_buffer_size = self._streamer.channel_buffer_size
            data_dtype = np.dtype(self._streamer.constraints.data_type.__name__).type
            data_buffer = InterleavedRingBuffer(interleave_factor=channel_count,
                                                size=channel_buffer_size,
                                                dtype=data_dtype,
                                                allow_overwrite=self._allow_overwrite)
            if self.__has_timestamps:
                timestamp_buffer = RingBuffer(size=channel_buffer_size,
                                              dtype=np.float64,
                                              allow_overwrite=self._allow_overwrite)
            else:
                timestamp_buffer = None
            self._buffers[uuid] = (data_buffer, timestamp_buffer)

    def _clear_buffer(self, uuid: UUID) -> None:
        with self._read_locks[uuid]:
            self._buffers[uuid] = None

    def _clear_buffers(self) -> None:
        for uid in list(self._buffers):
            self._clear_buffer(uid)

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        with self._lock:
            # Only act if the config should be changed
            if not self._config_equal(active_channels,
                                      streaming_mode,
                                      channel_buffer_size,
                                      sample_rate):
                if self._streamer.module_state() != 'idle':
                    raise RuntimeError('Streamer not idle')
                self._clear_buffers()
                self._streamer.configure(active_channels,
                                         streaming_mode,
                                         channel_buffer_size,
                                         sample_rate)

    def start_stream(self, uuid: UUID) -> None:
        """ Start the data acquisition/streaming """
        with self._lock:
            self._clear_buffer(uuid)
            self._create_buffer(uuid)
            try:
                if self._streamer.module_state() == 'idle':
                    self._streamer.start_stream()
                if self.__worker is None:
                    tm = ThreadManager.instance()
                    thread = tm.get_new_thread(self._worker_thread)
                    # If manager returns None, clean up and fallback to unique name
                    if thread is None:
                        # Best-effort cleanup of stale entry
                        try:
                            tm.quit_thread(self._worker_thread)
                            tm.join_thread(self._worker_thread)
                        except Exception:
                            pass
                        # Fallback to a unique suffix
                        self._worker_thread = f'{self._worker_thread}-{uuid.hex[:6]}'
                        thread = tm.get_new_thread(self._worker_thread)
                        if thread is None:
                            raise RuntimeError('Failed to obtain worker QThread for DataInStreamBuffer delegate')

                    self.__worker = DataInStreamDistributionWorker(self._streamer, self._buffers)
                    self.__worker.moveToThread(thread)
                    thread.started.connect(self.__worker.run)
                    thread.start()
            except Exception:
                self._clear_buffer(uuid)
                raise

    def stop_stream(self, uuid: UUID) -> None:
        """ Stop the data acquisition/streaming """
        with self._lock:
            self._clear_buffer(uuid)
            # If this was the last consumer, stop the worker thread and (optionally) the underlying streamer
            if all(buffer is None for buffer in self._buffers.values()):
                try:
                    if self.__worker is not None:
                        for lock in self._read_locks.values():
                            lock.lock()
                        self.__worker.stop()
                    tm = ThreadManager.instance()
                    try:
                        tm.quit_thread(self._worker_thread)
                    finally:
                        tm.join_thread(self._worker_thread)
                    if self._streamer.module_state() == 'locked':
                        self._streamer.stop_stream()
                finally:
                    if self.__worker is not None:
                        self.__worker = None
                        for lock in self._read_locks.values():
                            lock.unlock()

    def read_data_into_buffer(self,
                              uuid: UUID,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        with self._read_locks[uuid]:
            buffers = self._buffers[uuid]
            if (buffers is None) or (self._streamer.module_state() != 'locked'):
                raise RuntimeError('Streamer is not running')
            data, timestamps = buffers
            channel_count = len(self._streamer.active_channels)
            # Reshape buffers without copy
            data_buffer = data_buffer[:(samples_per_channel * channel_count)].reshape(
                [samples_per_channel, channel_count]
            )
            # Read until you have all requested samples acquired
            if timestamps is None:
                RingBufferReader(data, self._max_poll_rate)(samples_per_channel, data_buffer)
            else:
                timestamp_buffer = timestamp_buffer[:samples_per_channel]
                SyncRingBufferReader([data, timestamps], self._max_poll_rate)(
                    samples_per_channel,
                    [data_buffer, timestamp_buffer]
                )

    def read_available_data_into_buffer(self,
                                        uuid: UUID,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        channel_count = len(self._streamer.active_channels)
        available = min(self.available_samples(uuid), data_buffer.size // channel_count)
        self.read_data_into_buffer(uuid, data_buffer, available, timestamp_buffer)
        return available

    def read_data(self,
                  uuid: UUID,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        channel_count = len(self._streamer.active_channels)
        dtype = self._streamer.constraints.data_type.__name__
        if samples_per_channel is None:
            samples_per_channel = self.available_samples(uuid)
        data = np.empty(samples_per_channel * channel_count, dtype=dtype)
        if self.__has_timestamps:
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

    Example config for copy-paste:

    data_instream_buffer:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False  # optional, allow ringbuffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate (1/s) for connected hardware
        connect:
            streamer: <data_instream_hardware>
    """

    _streamer = Connector(name='streamer', interface='DataInStreamInterface')

    _max_poll_rate: float = ConfigOption(name='max_poll_rate', default=100., missing='warn')
    _allow_overwrite: bool = ConfigOption(name='allow_overwrite',
                                          default=False,
                                          missing='warn',
                                          constructor=lambda x: bool(x))

    _buffer_delegates: Dict[UUID, DataInStreamMultiConsumerDelegate] = WeakKeyDictionary()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._streamer_uid: UUID = None

    def on_activate(self) -> None:
        # If there is already a buffer delegate for the connected hardware present, just register
        # this interfuse as additional consumer. Otherwise, also create the delegate first.
        self._streamer_uid = netobtain(self._streamer().module_uuid)
        try:
            delegate = self._buffer_delegates[self._streamer_uid]
        except KeyError:
            delegate = DataInStreamMultiConsumerDelegate(streamer=self._streamer(),
                                                         allow_overwrite=self._allow_overwrite,
                                                         max_poll_rate=self._max_poll_rate)
            self._buffer_delegates[self._streamer_uid] = delegate
        delegate.register_consumer(self.module_uuid)

    def on_deactivate(self) -> None:
        if self.module_state() == 'locked':
            self.module_state.unlock()
        # Unregister this interfuse as consumer from the buffer delegate.
        try:
            delegate = self._buffer_delegates[self._streamer_uid]
        except KeyError:
            pass
        else:
            delegate.unregister_consumer(self.module_uuid)
        self._streamer_uid = None

    @property
    def _buffer_delegate(self) -> DataInStreamMultiConsumerDelegate:
        return self._buffer_delegates[self._streamer_uid]

    @property
    def constraints(self) -> DataInStreamConstraints:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.constraints """
        return self._streamer().constraints

    @property
    def available_samples(self) -> int:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.available_samples """
        return self._buffer_delegate.available_samples(self.module_uuid)

    @property
    def sample_rate(self) -> float:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.sample_rate """
        return self._streamer().sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ See: qudi.interface.data_instream_interface.DataInStreamInterface.channel_buffer_size
        """
        return self._streamer().channel_buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        return self._streamer().streaming_mode

    @property
    def active_channels(self) -> List[str]:
        return self._streamer().active_channels

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        self._buffer_delegate.configure(active_channels,
                                        streaming_mode,
                                        channel_buffer_size,
                                        sample_rate)

    def start_stream(self) -> None:
        if self.module_state() == 'idle':
            self._buffer_delegate.start_stream(self.module_uuid)
            self.module_state.lock()

    def stop_stream(self) -> None:
        try:
            self._buffer_delegate.stop_stream(self.module_uuid)
        finally:
            if self.module_state() == 'locked':
                self.module_state.unlock()

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        self._buffer_delegate.read_data_into_buffer(self.module_uuid,
                                                    data_buffer,
                                                    samples_per_channel,
                                                    timestamp_buffer)

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        return self._buffer_delegate.read_available_data_into_buffer(self.module_uuid,
                                                                     data_buffer,
                                                                     timestamp_buffer)

    def read_data(self,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        return self._buffer_delegate.read_data(self.module_uuid, samples_per_channel)

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        return self._buffer_delegate.read_single_point(self.module_uuid)
