# -*- coding: utf-8 -*-

"""
Ringbuffer based on numpy arrays.

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

__all__ = ['RingBuffer', 'InterleavedRingBuffer', 'RateAverageCalculator', 'RingBufferReader',
           'SyncRingBufferReader']

import time
import numpy as np
from typing import Optional, Tuple, Callable, Sequence, List
from collections.abc import Sized as _Sized

from qudi.util.mutex import Mutex


class RateAverageCalculator:
    """ Utility class to calculate a running average on sample rates. Every time "update" is called,
    this average is re-calculated based in the new sample count given as parameter.
    For best accuracy call "update" every time you receive new samples.
    First non-default value will be available after calling "update" for the second time.
    """
    def __init__(self, default: Optional[float] = 1000.):
        self._average: float = default
        self.__count: int = 0
        self.__start_time: float = 0.
        self.__update_func: Callable[[int], None] = self.__first_update

    @property
    def average_rate(self) -> float:
        return self._average

    def __first_update(self, count: int) -> None:
        self.__start_time = time.perf_counter()
        self.__update_func = self.__update

    def __update(self, count: int) -> None:
        self.__count += count
        self._average = self.__count / (time.perf_counter() - self.__start_time)

    def update(self, count: int) -> None:
        """ Re-Calculate the running average with the number of samples added since last call """
        self.__update_func(count)


class RingBuffer(_Sized):
    """ Ringbuffer based on numpy arrays. Is fairly thread-safe. """
    def __init__(self,
                 size: int,
                 dtype: Optional[type] = float,
                 allow_overwrite: Optional[bool] = True,
                 expected_sample_rate: Optional[float] = None):
        super().__init__()
        self._lock = Mutex()
        self.__start = 0
        self.__end = 0
        self.__fill_count = 0
        self._allow_overwrite = allow_overwrite
        self._buffer = np.empty(size, dtype)
        if expected_sample_rate is None:
            self.__rate_calculator = RateAverageCalculator()
        else:
            self.__rate_calculator = RateAverageCalculator(default=expected_sample_rate)

    @property
    def size(self) -> int:
        return self._buffer.size

    @property
    def dtype(self) -> type:
        return self._buffer.dtype.type

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

    @property
    def average_rate(self) -> float:
        return self.__rate_calculator.average_rate

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
                buffer = np.empty(size, dtype=self.dtype)
            elif size > buffer.size:
                size = buffer.size
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
            self.__rate_calculator.update(written)
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
        return self.fill_count

    def __array__(self) -> np.ndarray:
        # numpy compatibility
        return self.unwrap()


class InterleavedRingBuffer(RingBuffer):
    """ """
    def __init__(self,
                 interleave_factor: int,
                 size: int,
                 dtype: Optional[type] = float,
                 allow_overwrite: Optional[bool] = True,
                 expected_sample_rate: Optional[float] = None):
        self._interleave_factor = max(1, int(interleave_factor))
        super().__init__(size=size * self._interleave_factor,
                         dtype=dtype,
                         allow_overwrite=allow_overwrite,
                         expected_sample_rate=expected_sample_rate)

    @property
    def size(self) -> int:
        return super().size // self._interleave_factor

    @property
    def free_count(self) -> int:
        return super().free_count // self._interleave_factor

    @property
    def fill_count(self) -> int:
        return super().fill_count // self._interleave_factor

    @property
    def average_rate(self) -> float:
        return super().average_rate / self._interleave_factor

    @property
    def interleave_factor(self) -> int:
        return self._interleave_factor

    def unwrap(self) -> np.ndarray:
        """ Copy the data from this buffer into unwrapped form as a 2D array """
        arr = super().unwrap()
        return arr.reshape([-1, self._interleave_factor])

    def read(self, size: int, buffer: Optional[np.ndarray] = None) -> np.ndarray:
        """ ToDo: Document """
        if buffer is not None:
            if (buffer.size % self._interleave_factor) != 0:
                raise ValueError(f'Buffer size must be multiple of interleave_factor '
                                 f'({self._interleave_factor:d})')
            buffer = buffer.reshape(buffer.size)
        arr = super().read(size * self._interleave_factor, buffer)
        return arr.reshape([-1, self._interleave_factor])

    def write(self, data: np.ndarray) -> bool:
        """ ToDo: Document """
        if (data.size % self._interleave_factor) != 0:
            raise ValueError(
                f'Data size must be multiple of interleave_factor ({self._interleave_factor:d})'
            )
        return super().write(data.reshape(data.size))


class RingBufferReader:
    """ Helper class to pull arbitrary amounts of data from a RingBuffer with a single "read" call
    """

    def __init__(self, buffer: RingBuffer, max_poll_rate: Optional[float] = 30.):
        self._buffer = buffer
        self._max_poll_rate = max_poll_rate

    def __call__(self, size: int, buffer: Optional[np.ndarray] = None) -> np.ndarray:
        if buffer is None:
            if isinstance(self._buffer, InterleavedRingBuffer):
                buffer = np.empty([size, self._buffer.interleave_factor], dtype=self._buffer.dtype)
            else:
                buffer = np.empty(size, dtype=self._buffer.dtype)
        # Read until you have all requested samples acquired
        read = 0
        while read < size:
            # Request either all remaining samples or at maximum half the RingBuffer size
            request = min(self._buffer.size // 2, size - read)
            # If not all requested samples are available yet, wait for a bit
            available = self._buffer.fill_count
            if available < request:
                time.sleep(
                    max((request - available) / self._buffer.average_rate, 1. / self._max_poll_rate)
                )
            read += self._buffer.read(request, buffer[read:]).shape[0]
        return buffer[:read]


class SyncRingBufferReader:
    """ Helper class to pull arbitrary amounts of data from multiple RingBuffers with same data
    rate and size by a single "read" call
    """

    def __init__(self, buffers: Sequence[RingBuffer], max_poll_rate: Optional[float] = 30.):
        self._buffers = buffers
        self._min_poll_interval = 1. / max_poll_rate
        self._min_request_size = max(1, min(buf.size for buf in self._buffers) // 2)

    @staticmethod
    def _get_return_buffer(size: int, ringbuffer: RingBuffer) -> np.ndarray:
        if isinstance(ringbuffer, InterleavedRingBuffer):
            return np.empty([size, ringbuffer.interleave_factor], dtype=ringbuffer.dtype)
        else:
            return np.empty(size, dtype=ringbuffer.dtype)

    @property
    def min_fill_count(self) -> int:
        return min(buf.fill_count for buf in self._buffers)

    @property
    def average_rate(self) -> float:
        return sum(buf.average_rate for buf in self._buffers) / len(self._buffers)

    def __call__(self,
                 size: int,
                 buffers: Optional[Sequence[np.ndarray]] = None) -> List[np.ndarray]:
        if buffers is None:
            buffers = [self._get_return_buffer(size, ringbuf) for ringbuf in self._buffers]
        else:
            buffers = [buf[:size] for buf in buffers]
        # Read until you have all requested samples acquired
        read = 0
        while read < size:
            # Request either all remaining samples or at maximum half the RingBuffer size
            request = min(self._min_request_size, size - read)
            # If not all requested samples are available yet, wait for a bit
            available = self.min_fill_count
            if available < request:
                time.sleep(max((request - available) / self.average_rate, self._min_poll_interval))
            request = min(request, self.min_fill_count)
            read_counts = [ringbuf.read(request, buf[read:]).shape[0] for buf, ringbuf in
                           zip(buffers, self._buffers)]
            read += read_counts[0]
        return buffers
