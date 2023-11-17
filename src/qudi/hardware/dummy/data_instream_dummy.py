# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module to use a National Instruments X-series card as mixed
signal input data streamer.

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
from enum import Enum
from typing import List, Iterable, Union, Optional, Tuple, Callable, Sequence

from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.util.mutex import Mutex
from qudi.util.helpers import is_integer_type
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming


def _make_sine_func(sample_rate: float) -> Callable[[np.ndarray, np.ndarray], None]:
    freq = sample_rate / (20 + 80 * np.random.rand())
    amp = 1 + np.random.rand() * 9
    noise_lvl = amp * (0.1 + np.random.rand() * 0.4)
    def make_sine(x, y):
        # y[:] = np.sin(2 * np.pi * freq * x)
        np.sin(2 * np.pi * freq * x, out=y)
        y *= amp
        noise = np.random.rand(x.size)
        noise *= 2 * noise_lvl
        noise -= noise_lvl
        y += noise
    return make_sine


def _make_counts_func(sample_rate: float) -> Callable[[np.ndarray, np.ndarray], None]:
    count_lvl = 1_00 + np.random.rand() * (5_000 - 1_00)
    def make_counts(x, y):
        y[:] = count_lvl
        y += np.random.poisson(count_lvl, y.size)
    return make_counts


class SignalShape(Enum):
    INVALID = -1
    SINE = 0
    COUNTS = 1


class SampleGenerator:
    """ Generator object that periodically generates new samples based on a certain timebase but
    the actual sample timestamps can be irregular depending on configured SampleTiming
    """
    def __init__(self,
                 signal_shapes: Iterable[SignalShape],
                 sample_rate: float,
                 sample_timing: SampleTiming,
                 streaming_mode: StreamingMode,
                 data_type: Union[type, str],
                 buffer_size: int,
                 ) -> None:
        self.data_type = np.dtype(data_type).type
        self.sample_rate = float(sample_rate)
        self.sample_timing = SampleTiming(sample_timing)
        self.streaming_mode = StreamingMode(streaming_mode)
        self.signal_shapes = [SignalShape(shape) for shape in signal_shapes]
        self.buffer_size = buffer_size

        self.__start = 0  # buffer start sample index
        self.__end = 0  # buffer end sample index
        self.__available_samples = 0
        self._sample_buffer = None
        self._timestamp_buffer = None
        self._generator_functions = list()
        self._start_time = self._last_time = 0.0
        self.restart()

    @property
    def available_samples(self) -> int:
        self.generate_samples()
        return self.__available_samples

    @property
    def channel_count(self) -> int:
        return len(self._generator_functions)

    @property
    def _buffer_size(self) -> int:
        return self._sample_buffer.size

    @property
    def _buffer_sample_size(self) -> int:
        return self._buffer_size // self.channel_count

    @property
    def _free_samples(self) -> int:
        return max(0, self._buffer_sample_size - self.__available_samples)

    def restart(self) -> None:
        # Init generator functions
        self._generator_functions = list()
        for shape in self.signal_shapes:
            if shape == SignalShape.SINE:
                self._generator_functions.append(_make_sine_func(self.sample_rate))
            elif shape == SignalShape.COUNTS:
                self._generator_functions.append(_make_counts_func(self.sample_rate))
            else:
                raise ValueError(f'Invalid SignalShape encountered: {shape}')
        # Init buffer
        self.__start = self.__end = 0
        self.__available_samples = 0
        self._sample_buffer = np.empty(self.buffer_size * self.channel_count, dtype=self.data_type)
        if self.sample_timing == SampleTiming.TIMESTAMP:
            self._timestamp_buffer = np.zeros(self.buffer_size, dtype=np.float64)
        else:
            self._timestamp_buffer = None
        # Set start time
        self._start_time = self._last_time = time.perf_counter()

    def generate_samples(self) -> float:
        """ Generates new samples in free buffer space and updates buffer pointers. If new samples
        do not fit into buffer, raise an OverflowError.
        """
        now = time.perf_counter()
        elapsed_time = now - self._last_time
        time_offset = self._last_time - self._start_time
        samples_per_channel = int(elapsed_time * self.sample_rate)  # truncate
        if self.streaming_mode == StreamingMode.FINITE:
            samples_per_channel = min(samples_per_channel, self._free_samples)
        elapsed_time = samples_per_channel / self.sample_rate
        ch_count = self.channel_count

        if samples_per_channel > 0:
            # Generate x-axis (time) for sample generation
            if self.sample_timing == SampleTiming.CONSTANT:
                x = np.arange(samples_per_channel, dtype=np.float64)
                x /= self.sample_rate
            else:
                # randomize ticks within time interval for non-regular sampling
                x = np.random.rand(samples_per_channel)
                x.sort()
                x *= elapsed_time
            x += time_offset

            # ToDo:
            # Generate samples and write into buffer
            buffer = self._sample_buffer.reshape([self._buffer_sample_size, ch_count])
            end = self.__end + samples_per_channel
            if end > self._buffer_sample_size:
                first_samples = self._buffer_sample_size - self.__end
                end -= self._buffer_sample_size
                for ch_idx, generator in enumerate(self._generator_functions):
                    generator(x[:first_samples], buffer[self.__end:, ch_idx])
                    generator(x[first_samples:], buffer[:end, ch_idx])
                if self.sample_timing == SampleTiming.TIMESTAMP:
                    self._timestamp_buffer[self.__end:] = x[:first_samples]
                    self._timestamp_buffer[:end] = x[first_samples:]
            else:
                for ch_idx, generator in enumerate(self._generator_functions):
                    generator(x, buffer[self.__end:end, ch_idx])
                if self.sample_timing == SampleTiming.TIMESTAMP:
                    self._timestamp_buffer[self.__end:end] = x
            # Update pointers
            self.__available_samples += samples_per_channel
            self.__end = end
        self._last_time += elapsed_time
        if self.__available_samples > self._buffer_sample_size:
            raise OverflowError('Sample buffer has overflown. Decrease sample rate or increase '
                                'data readout rate.')
        return self._last_time

    def read_samples(self,
                     sample_buffer: np.ndarray,
                     samples_per_channel: int,
                     timestamp_buffer: Optional[np.ndarray] = None) -> int:
        ch_count = self.channel_count
        buf_size = self._buffer_size
        samples = min(self.__available_samples, samples_per_channel) * ch_count
        start = self.__start * ch_count
        end = start + samples
        if end > buf_size:
            first_samples = buf_size - start
            end -= buf_size
            sample_buffer[:first_samples] = self._sample_buffer[start:]
            sample_buffer[first_samples:first_samples + end] = self._sample_buffer[:end]
            if timestamp_buffer is not None:
                timestamp_buffer[:first_samples // ch_count] = self._timestamp_buffer[self.__start:]
                timestamp_buffer[first_samples // ch_count:(first_samples + end) // ch_count] = self._timestamp_buffer[:end // ch_count]
        else:
            sample_buffer[:samples] = self._sample_buffer[start:end]
            if timestamp_buffer is not None:
                timestamp_buffer[:samples // ch_count] = self._timestamp_buffer[self.__start:end // ch_count]
        # Update pointers
        samples //= ch_count
        self.__start = end // ch_count
        self.__available_samples -= samples
        return samples

    def wait_get_available_samples(self, samples: int) -> int:
        available = self.available_samples
        if available < samples:
            # Wait for bulk time
            time.sleep((samples - available) / self.sample_rate)
            available = self.available_samples
            # Wait a little more if necessary
            while available < samples:
                time.sleep(1 / self.sample_rate)
                available = self.available_samples
        return available


class InStreamDummy(DataInStreamInterface):
    """
    A dummy module to act as data in-streaming device (continuously read values)

    Example config for copy-paste:

    instream_dummy:
        module.Class: 'dummy.data_instream_dummy.InStreamDummy'
        options:
            channel_names:
                - 'digital 1'
                - 'analog 1'
                - 'digital 2'
            channel_units:
                - 'Hz'
                - 'V'
                - 'Hz'
            channel_signals:  # Can be 'counts' or 'sine'
                - 'counts'
                - 'sine'
                - 'counts'
            data_type: 'float64'
            sample_timing: 'CONSTANT'  # Can be 'CONSTANT', 'TIMESTAMP' or 'RANDOM'
    """
    # config options
    _channel_names = ConfigOption(name='channel_names',
                                  missing='error',
                                  constructor=lambda names: [str(x) for x in names])
    _channel_units = ConfigOption(name='channel_units',
                                  missing='error',
                                  constructor=lambda units: [str(x) for x in units])
    _channel_signals = ConfigOption(
        name='channel_signals',
        missing='error',
        constructor=lambda signals: [SignalShape[x.upper()] for x in signals]
    )
    _data_type = ConfigOption(name='data_type',
                              default='float64',
                              missing='info',
                              constructor=lambda typ: np.dtype(typ).type)
    _sample_timing = ConfigOption(name='sample_timing',
                                  default='CONSTANT',
                                  missing='info',
                                  constructor=lambda timing: SampleTiming[timing.upper()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._active_channels = list()
        self._sample_generator = None
        self._constraints = None

    def on_activate(self):
        # Sanity check ConfigOptions
        if not (len(self._channel_names) == len(self._channel_units) == len(self._channel_signals)):
            raise ValueError('ConfigOptions "channel_names", "channel_units" and "channel_signals" '
                             'must contain same number of elements')
        if len(set(self._channel_names)) != len(self._channel_names):
            raise ValueError('ConfigOptions "channel_names" must not contain duplicates')
        if is_integer_type(self._data_type) and any(sig == SignalShape.SINE for sig in self._channel_signals):
            self.log.warning('Integer data type not supported for Sine signal shape. '
                             'Falling back to numpy.float64 data type instead.')
            self._data_type = np.float64

        self._constraints = DataInStreamConstraints(
            channel_units=dict(zip(self._channel_names, self._channel_units)),
            sample_timing=self._sample_timing,
            streaming_modes=[StreamingMode.CONTINUOUS, StreamingMode.FINITE],
            data_type=self._data_type,
            channel_buffer_size=ScalarConstraint(default=1024**2,
                                                 bounds=(128, 1024**3),
                                                 increment=1,
                                                 enforce_int=True),
            sample_rate=ScalarConstraint(default=10.0, bounds=(0.1, 1024**2), increment=0.1)
        )
        self._active_channels = list(self._constraints.channel_units)
        self._sample_generator = SampleGenerator(
            signal_shapes=self._channel_signals,
            sample_rate=self._constraints.sample_rate.default,
            sample_timing=self._constraints.sample_timing,
            streaming_mode=self._constraints.streaming_modes[0],
            data_type=self._constraints.data_type,
            buffer_size=self._constraints.channel_buffer_size.default
        )

    def on_deactivate(self):
        # Free memory
        self._sample_generator = None

    @property
    def constraints(self) -> DataInStreamConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
        return self._constraints

    @property
    def available_samples(self) -> int:
        """ Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        """
        with self._thread_lock:
            if self.module_state() == 'locked':
                return self._sample_generator.available_samples
            return 0

    @property
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.
        """
        return self._sample_generator.sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self._sample_generator.buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        return self._sample_generator.streaming_mode

    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        return self._active_channels.copy()

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        with self._thread_lock:
            if self.module_state() == 'locked':
                raise RuntimeError('Unable to configure data stream while it is already running')

            # Cache current values to restore them if configuration fails
            old_channels = self.active_channels
            old_streaming_mode = self.streaming_mode
            old_buffer_size = self.channel_buffer_size
            old_sample_rate = self.sample_rate
            try:
                self._set_active_channels(active_channels)
                self._set_streaming_mode(streaming_mode)
                self._set_channel_buffer_size(channel_buffer_size)
                self._set_sample_rate(sample_rate)
            except Exception as err:
                self._set_active_channels(old_channels)
                self._set_streaming_mode(old_streaming_mode)
                self._set_channel_buffer_size(old_buffer_size)
                self._set_sample_rate(old_sample_rate)
                raise RuntimeError('Error while trying to configure data in-streamer') from err

    def _set_active_channels(self, channels: Iterable[str]) -> None:
        channels = set(channels)
        if not channels.issubset(self._constraints.channel_units):
            raise ValueError(f'Invalid channels to set active {channels}. Allowed channels are '
                             f'{set(self._constraints.channel_units)}')
        channel_shapes = {ch: self._channel_signals[idx] for idx, ch in
                          enumerate(self._constraints.channel_units) if ch in channels}
        self._sample_generator.signal_shapes = [shape for shape in channel_shapes.values()]
        self._active_channels = list(channel_shapes)

    def _set_streaming_mode(self, mode: Union[StreamingMode, int]) -> None:
        try:
            mode = StreamingMode(mode.value)
        except AttributeError:
            mode = StreamingMode(mode)
        if (mode == StreamingMode.INVALID) or mode not in self._constraints.streaming_modes:
            raise ValueError(
                f'Invalid streaming mode to set ({mode}). Allowed StreamingMode values are '
                f'[{", ".join(str(mod) for mod in self._constraints.streaming_modes)}]'
            )
        self._sample_generator.streaming_mode = mode

    def _set_channel_buffer_size(self, samples: int) -> None:
        self._constraints.channel_buffer_size.check(samples)
        self._sample_generator.buffer_size = samples

    def _set_sample_rate(self, rate: Union[int, float]) -> None:
        rate = float(rate)
        self._constraints.sample_rate.check(rate)
        self._sample_generator.sample_rate = rate

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.module_state.lock()
                try:
                    self._sample_generator.restart()
                except:
                    self.module_state.unlock()
                    raise
            else:
                self.log.warning('Unable to start input stream. It is already running.')

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        with self._thread_lock:
            if self.module_state() == 'locked':
                self.module_state.unlock()

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
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')
            if (self.constraints.sample_timing == SampleTiming.TIMESTAMP) and timestamp_buffer is None:
                raise RuntimeError('SampleTiming.TIMESTAMP mode requires a timestamp buffer array')

            channel_count = len(self.active_channels)
            if data_buffer.size < samples_per_channel * channel_count:
                raise RuntimeError(
                    f'data_buffer too small ({data_buffer.size:d}) to hold all requested '
                    f'samples for all channels ({channel_count:d} * {samples_per_channel:d} = '
                    f'{samples_per_channel * channel_count:d})'
                )
            if (timestamp_buffer is not None) and (timestamp_buffer.size < samples_per_channel):
                raise RuntimeError(
                    f'timestamp_buffer too small ({timestamp_buffer.size:d}) to hold all requested '
                    f'samples ({samples_per_channel:d})'
                )

            self._sample_generator.wait_get_available_samples(samples_per_channel)
            if timestamp_buffer is None:
                self._sample_generator.read_samples(
                    sample_buffer=data_buffer,
                    samples_per_channel=samples_per_channel
                )
            else:
                self._sample_generator.read_samples(
                    sample_buffer=data_buffer,
                    samples_per_channel=samples_per_channel,
                    timestamp_buffer=timestamp_buffer
                )

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
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')
            self._sample_generator.generate_samples()
            available_samples = self._sample_generator.available_samples
            if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
                if timestamp_buffer is None:
                    raise RuntimeError(
                        'SampleTiming.TIMESTAMP mode requires a timestamp buffer array'
                    )
                timestamp_buffer = timestamp_buffer[:available_samples]
            channel_count = len(self.active_channels)
            data_buffer = data_buffer[:channel_count * available_samples]
            return self._sample_generator.read_samples(sample_buffer=data_buffer,
                                                       samples_per_channel=available_samples,
                                                       timestamp_buffer=timestamp_buffer)

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
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

            self._sample_generator.generate_samples()
            if samples_per_channel is None:
                samples_per_channel = self._sample_generator.available_samples
            else:
                self._sample_generator.wait_get_available_samples(samples_per_channel)

            data_buffer = np.empty(len(self.active_channels) * samples_per_channel,
                                   dtype=self._constraints.data_type)
            if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
                timestamp_buffer = np.empty(samples_per_channel, dtype=np.float64)
            else:
                timestamp_buffer = None
            if samples_per_channel > 0:
                self._sample_generator.read_samples(sample_buffer=data_buffer,
                                                    samples_per_channel=samples_per_channel,
                                                    timestamp_buffer=timestamp_buffer)
            return data_buffer, timestamp_buffer

    def read_single_point(self):
        """ This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        @return numpy.ndarray: 1D array containing one sample for each channel. Empty array
                               indicates error.
        """
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

            data_buffer = np.empty(len(self.active_channels), dtype=self._constraints.data_type)
            if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
                timestamp_buffer = np.empty(1, dtype=np.float64)
            else:
                timestamp_buffer = None
            self._sample_generator.wait_get_available_samples(1)
            self._sample_generator.read_samples(sample_buffer=np.expand_dims(data_buffer, axis=0),
                                                samples_per_channel=1,
                                                timestamp_buffer=timestamp_buffer)
            return data_buffer, timestamp_buffer
