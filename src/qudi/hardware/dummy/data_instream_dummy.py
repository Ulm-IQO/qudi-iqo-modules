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
from typing import List, Iterable, Union, Optional, Tuple, Callable

from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming


def _make_sine_func() -> Callable[[np.ndarray, np.ndarray], None]:
    freq = (np.pi / 500) + np.random.rand() * (49 * np.pi / 500)
    amp = 1 + np.random.rand() * 9
    noise_lvl = amp * (0.1 + np.random.rand() * 0.4)
    def make_sine(x, y):
        np.sin(freq * x, out=y)
        y *= amp
        noise = np.random.rand(x.size)
        noise *= 2 * noise_lvl
        noise -= noise_lvl
        y += noise
    return make_sine


def _make_counts_func() -> Callable[[np.ndarray, np.ndarray], None]:
    count_lvl = 1_000 + np.random.rand() * (1_000_000 - 1_000)
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

        self.__start = 0  # buffer pointer
        self.__available_samples = 0
        self._sample_buffer = None
        self._timestamp_buffer = None
        self._generator_functions = list()
        self._start_time = self._last_time = 0.0
        self.restart()

    @property
    def available_samples(self) -> int:
        return self.__available_samples

    def restart(self) -> None:
        self.__start = 0  # buffer pointer
        self.__available_samples = 0
        self._sample_buffer = np.zeros([len(self.signal_shapes), self.buffer_size], dtype=self.data_type)
        if self.sample_timing == SampleTiming.TIMESTAMP:
            self._timestamp_buffer = np.zeros(self.buffer_size, dtype=np.float64)
        else:
            self._timestamp_buffer = None
        self._generator_functions = list()
        for shape in self.signal_shapes:
            if shape == SignalShape.SINE:
                self._generator_functions.append(_make_sine_func())
            elif shape == SignalShape.COUNTS:
                self._generator_functions.append(_make_counts_func())
            else:
                raise ValueError(f'Invalid SignalShape encountered: {shape}')
        self._start_time = self._last_time = time.perf_counter()

    def generate_samples(self) -> float:
        """ Generates new samples in free buffer space and updates buffer pointers. If new samples
        do not fit into buffer, raise an OverflowError.
        """
        now = time.perf_counter()
        elapsed_time = now - self._last_time
        time_offset = self._last_time - self._start_time
        insert = self.__start + self.__available_samples
        buf_size = self._sample_buffer.shape[1]
        samples = int(elapsed_time * self.sample_rate)  # truncate
        if self.streaming_mode == StreamingMode.FINITE:
            samples = min(samples, buf_size - insert)

        if samples > 0:
            # Generate x-axis (time) for sample generation
            if self.sample_timing == SampleTiming.CONSTANT:
                x = np.arange(samples, dtype=np.float64)
                x /= self.sample_rate
            else:
                # randomize ticks within time interval for non-regular sampling
                x = np.random.rand(samples)
                x.sort()
                x *= elapsed_time
            x += time_offset

            # Generate samples and write into buffer

            end = insert + samples
            if end > buf_size:
                first_samples = buf_size - insert
                end = end - buf_size
                for ch_idx, generator in enumerate(self._generator_functions):
                    generator(x[:first_samples], self._sample_buffer[ch_idx, insert:])
                    generator(x[first_samples:], self._sample_buffer[ch_idx, :end])
                if self.sample_timing == SampleTiming.TIMESTAMP:
                    self._timestamp_buffer[insert:] = x[:first_samples]
                    self._timestamp_buffer[:end] = x[first_samples:]
            else:
                for ch_idx, generator in enumerate(self._generator_functions):
                    generator(x, self._sample_buffer[ch_idx, insert:end])
                if self.sample_timing == SampleTiming.TIMESTAMP:
                    self._timestamp_buffer[insert:end] = x

        # Update pointers
        self.__available_samples += samples
        self._last_time = now
        if self.__available_samples > buf_size:
            raise OverflowError('Sample buffer has overflown. Decrease sample rate or increase '
                                'data readout rate.')
        return self._last_time

    def read_samples(self,
                     sample_buffer: np.ndarray,
                     timestamp_buffer: Optional[np.ndarray] = None) -> int:
        buf_size = self._sample_buffer.shape[1]
        samples = min(self.__available_samples, sample_buffer.shape[1])
        end = self.__start + samples
        if end > buf_size:
            first_samples = buf_size - self.__start
            end = end - buf_size
            sample_buffer[:, :first_samples] = self._sample_buffer[:, self.__start:]
            sample_buffer[:, first_samples:samples] = self._sample_buffer[:, :end]
            if timestamp_buffer is not None:
                timestamp_buffer[:, :first_samples] = self._timestamp_buffer[:, self.__start:]
                timestamp_buffer[:, first_samples:samples] = self._timestamp_buffer[:, :end]
        else:
            sample_buffer[:, :samples] = self._sample_buffer[:, self.__start:end]
            if timestamp_buffer is not None:
                timestamp_buffer[:, :samples] = self._timestamp_buffer[:, self.__start:end]
        # Update pointers
        self.__start = end
        self.__available_samples -= samples
        return samples


class InStreamDummy(DataInStreamInterface):
    """
    A dummy module to act as data in-streaming device (continuously read values)

    Example config for copy-paste:

    instream_dummy:
        module.Class: 'data_instream_dummy.InStreamDummy'
        options:
            digital_channels:  # optional, must provide at least one digital or analog channel
                - 'digital 1'
                - 'digital 2'
                - 'digital 3'
            analog_channels:  # optional, must provide at least one digital or analog channel
                - 'analog 1'
                - 'analog 2'
            digital_event_rates:  # optional, must have as many entries as digital_channels or just one
                - 1000
                - 10000
                - 100000
            # digital_event_rates: 100000
            analog_amplitudes:  # optional, must have as many entries as analog_channels or just one
                - 5
                - 10
            # analog_amplitudes: 10  # optional (10V by default)
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

        Ignored for anything but SampleTiming.CONSTANT.
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
                  active_channels: Iterable[str],
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
                              number_of_samples: Optional[int] = None,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        """ Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            data_buffer.shape == (<channel_count>, <sample_count>)
        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        at least <number_of_samples> in size.

        If number_of_samples is omitted it will be derived from buffer.shape[1]
        """
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')
            if (self.constraints.sample_timing == SampleTiming.TIMESTAMP) and timestamp_buffer is None:
                raise RuntimeError('SampleTiming.TIMESTAMP mode requires a timestamp buffer array')

            if data_buffer.ndim == 1:
                data_buffer = np.expand_dims(data_buffer, axis=0)

            if number_of_samples is None:
                number_of_samples = data_buffer.shape[1]
            elif number_of_samples > data_buffer.shape[1]:
                raise RuntimeError(f'data_buffer too small ({data_buffer.shape[1]:d}) to contain '
                                   f'all requested samples ({number_of_samples:d})')

            if (timestamp_buffer is not None) and (timestamp_buffer.size < number_of_samples):
                raise RuntimeError(f'timestamp_buffer too small ({timestamp_buffer.shape[1]:d}) to '
                                   f'contain all requested samples ({number_of_samples:d})')

            # Return immediately if no samples are requested
            offset = 0
            while number_of_samples > 0:
                self._sample_generator.generate_samples()
                read_samples = self._sample_generator.read_samples(
                    sample_buffer=data_buffer[:, offset:offset + number_of_samples],
                    timestamp_buffer=timestamp_buffer[offset:offset + number_of_samples]
                )
                number_of_samples -= read_samples
                offset += read_samples

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        """ Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            data_buffer.shape == (<channel_count>, <sample_count>)
        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        at least <number_of_samples> in size.

        This method will read all currently available samples into buffer. If number of available
        samples exceed buffer size, read only as many samples as fit into the buffer.
        Returns the number of samples read (per channel).
        """
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')
            if (self.constraints.sample_timing == SampleTiming.TIMESTAMP) and timestamp_buffer is None:
                raise RuntimeError('SampleTiming.TIMESTAMP mode requires a timestamp buffer array')

            if data_buffer.ndim == 1:
                data_buffer = np.expand_dims(data_buffer, axis=0)

            self._sample_generator.generate_samples()
            return self._sample_generator.read_samples(sample_buffer=data_buffer,
                                                       timestamp_buffer=timestamp_buffer)

    def read_data(self,
                  number_of_samples: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """ Read data from the stream buffer into a 2D numpy array and return it.
        The arrays first index corresponds to the channel number and the second index serves as
        sample index:
            return_array.shape == (self.number_of_channels, number_of_samples)
        The numpy arrays data type is the one defined in self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.

        If number_of_samples is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        If no samples are available, this method will immediately return an empty array.
        """
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

            self._sample_generator.generate_samples()
            if number_of_samples is None:
                number_of_samples = self._sample_generator.available_samples

            data_buffer = np.zeros([len(self.active_channels), number_of_samples],
                                   dtype=self._constraints.data_type)
            if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
                timestamp_buffer = np.zeros(number_of_samples, dtype=np.float64)
            else:
                timestamp_buffer = None
            self._sample_generator.read_samples(sample_buffer=data_buffer,
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

            data_buffer = np.zeros(len(self.active_channels), dtype=self._constraints.data_type)
            if self.constraints.sample_timing == SampleTiming.TIMESTAMP:
                timestamp_buffer = np.zeros([1, 1], dtype=np.float64)
            else:
                timestamp_buffer = None
            self._sample_generator.generate_samples()
            self._sample_generator.read_samples(sample_buffer=np.expand_dims(data_buffer, axis=0),
                                                timestamp_buffer=timestamp_buffer)
            return data_buffer, None if timestamp_buffer is None else timestamp_buffer[0]
