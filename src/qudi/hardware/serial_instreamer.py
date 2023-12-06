import numpy as np
import matplotlib.pyplot as plt
import time
import datetime
import os
from qtpy import QtCore
import copy as cp

#import serial
#import minimalmodbus
from typing import List, Iterable, Union, Optional, Tuple, Callable, Sequence


import logging

from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.util.helpers import is_integer_type
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.util.mutex import Mutex

class SerialInStreamer(DataInStreamInterface):
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
            data_type: 'float64'
            sample_timing: 'CONSTANT'  # Can be 'CONSTANT', 'TIMESTAMP' or 'RANDOM'
    """

    _threaded = True


    # config options
    _channel_names = ConfigOption(name='channel_names',
                                  missing='error',
                                  constructor=lambda names: [str(x) for x in names])
    _channel_units = ConfigOption(name='channel_units',
                                  missing='error',
                                  constructor=lambda units: [str(x) for x in units])

    _data_type = ConfigOption(name='data_type',
                              default='float64',
                              missing='info',
                              constructor=lambda typ: np.dtype(typ).type)

    # todo make ConfigOption
    _sample_timing = SampleTiming.TIMESTAMP
    _streaming_mode = StreamingMode.CONTINUOUS
    _sample_rate = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._active_channels = list()
        self._sample_generator = None
        self._constraints = None

    def on_activate(self):
        # Sanity check ConfigOptions
        if not len(self._channel_names) == len(self._channel_units):
            raise ValueError('ConfigOptions "channel_names", "channel_units" and "channel_signals" '
                             'must contain same number of elements')
        if len(set(self._channel_names)) != len(self._channel_names):
            raise ValueError('ConfigOptions "channel_names" must not contain duplicates')
        if is_integer_type(self._data_type):
            self.log.warning('Integer data type not supported for Sine signal shape. '
                             'Falling back to numpy.float64 data type instead.')
            self._data_type = np.float64

        self._constraints = DataInStreamConstraints(
            channel_units=dict(zip(self._channel_names, self._channel_units)),
            sample_timing=self._sample_timing,
            streaming_modes=[StreamingMode.CONTINUOUS, StreamingMode.FINITE],
            data_type=self._data_type,
            channel_buffer_size=ScalarConstraint(default=1024**2,
                                                 bounds=(16, 1024**3),
                                                 increment=1,
                                                 enforce_int=True),
            sample_rate=ScalarConstraint(default=10.0, bounds=(0.1, 1024**2), increment=0.1)
        )
        self._active_channels = list(self._constraints.channel_units)

        self._channel_map = {'pv': 1025,
                             'sp': 1029}
        self._channel_map = {'pv': 1025}
        self._buffer_size = 16  # todo: make configOption
        self.data_type = 'float64'
        self._sample_rate = 1

        self.__poll_timer = QtCore.QTimer()
        self.__poll_timer.setInterval(int(round(1./self._sample_rate * 1000)))
        self.__poll_timer.setSingleShot(True)
        self.__poll_timer.timeout.connect(self._pull_data_loop)
        self.__stop_stream = False


    def on_deactivate(self):
        # Free memory
        self._sample_generator = None

    @property
    def constraints(self):
        """ Read-only property returning the constraints on the settings for this data streamer. """
        return self._constraints

    @property
    def available_samples(self):
        """ Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        """
        with self._thread_lock:
            if self.module_state() == 'locked':
                return self.__available_samples
            return 0

    @property
    def sample_rate(self):
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.
        """
        return self._sample_rate

    @property
    def channel_buffer_size(self):
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self._buffer_size

    @property
    def streaming_mode(self):
        """ Read-only property returning the currently configured StreamingMode Enum """
        return self._streaming_mode

    @property
    def active_channels(self):
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


    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        return list(self._channel_map.copy().keys())

    @property
    def channel_count(self):
        return len(self.active_channels)

    def read_samples(self, sample_buffer, samples_per_channel):
        pass

    def _connect_device(self):
        # todo: use serial or minimalmodbug
        self.configure_port(com_port=com_port, baudrate=baudrate, bytesize=bytesize, stopbits=stopbits, timeout=timeout,
                            parity=parity)

    def _reset_stream(self):
        with self._thread_lock:
            # Init buffer
            self.__pos_sample_buffer = 0
            self.__idx_read_start = 0
            self.__idx_write_start = 0
            self.__available_samples = 0
            self._sample_buffer = np.ones(self._buffer_size * self.channel_count, dtype=self.data_type)*np.nan
            self._timestamp_buffer = np.zeros(self._buffer_size, dtype=np.float64)

            self.__stop_stream = False
            self._start_time = self._last_time = time.perf_counter()

    def stop_stream(self):
        with self._thread_lock:
            self.__stop_stream = True
            self.module_state.unlock()

    def _read_serial_register(self, register):
        try:
            response = self.read_register(1029, 1)  # from modbus
        except:
            response = 0.0
            raise ValueError(f"Couldn't read register: {register}")
        return response

    def _get_sample(self, debug=True):

        _sample_per_ch = {key: np.nan for key in self._channel_map.keys()}
        for ch_name, ch_reg in self._channel_map.items():
            if debug:
                _sample_per_ch[ch_name] = float(np.random.random_sample(1))
            else:
                _sample_per_ch[ch_name] = self._read_serial_register(ch_reg)

        return _sample_per_ch


    def _pull_data_loop(self):

        # todo: thread lock?
        if self.__stop_stream:
            return

        if self.__pos_sample_buffer == 0:
            self._t_start = time.perf_counter()

        channel_count = self.channel_count

        idx_start = self.__pos_sample_buffer
        idx_end = self.__pos_sample_buffer + channel_count

        self.log.debug(f"Pulling datab Buffer pos: {self.__pos_sample_buffer}/{len(self._sample_buffer)}")
        if idx_end > len(self._sample_buffer) -1 :
            raise OverflowError("Full buffer! Increase data buffer or reduce sampling rate")

        sample_dict = self._get_sample()
        t_now = time.perf_counter()
        dt_now = time.perf_counter() - self._t_start

        self._sample_buffer[idx_start:idx_end] = np.asarray(list(sample_dict.values()), dtype=self.data_type)
        self._timestamp_buffer[idx_start:idx_start+1] = dt_now


        # Update pointers
        self.__pos_sample_buffer += channel_count
        self.__available_samples += 1

        #self.log.debug(f"Polled samples: {sample_dict} at t= {t_now}. Buffer pos: {self.__pos_sample_buffer}")
        self.__poll_timer.start()


    def _start_query_loop(self):
        """ Start the readout loop. """
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__poll_timer,
                                            'start',
                                            QtCore.Qt.BlockingQueuedConnection)
            return

        if self.module_state() == 'idle':
            self.module_state.lock()
            self.__poll_timer.start()



    def _set_active_channels(self, channels: Iterable[str]) -> None:
        channels = set(channels)
        if not channels.issubset(self._constraints.channel_units):
            raise ValueError(f'Invalid channels to set active {channels}. Allowed channels are '
                             f'{set(self._constraints.channel_units)}')
        # todo: manipulse channeld map
        self._active_channels = list(self.active_channels)

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
        self._streaming_mode = mode

    def _set_channel_buffer_size(self, samples: int) -> None:
        self._constraints.channel_buffer_size.check(samples)
        self.log.debug(f"Setting buffer size: {self._buffer_size}->{samples}")
        self._buffer_size = samples

    def _set_sample_rate(self, rate: Union[int, float]) -> None:
        rate = float(rate)
        self._constraints.sample_rate.check(rate)
        self._sample_rate = rate
        self.__poll_timer.setInterval(int(round(1./self._sample_rate * 1000)))

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """

        self._reset_stream()  # todo: think about where to but mutexes
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.module_state.lock()
                try:
                    self._start_query_loop()
                except:
                    self.module_state.unlock()
                    raise
            else:
                self.log.warning('Unable to start input stream. It is already running.')

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        with self._thread_lock:
            self.__stop_stream = True
            if self.module_state() == 'locked':
                self.module_state.unlock()

    def _wait_get_available_samples(self, samples):
        available = self.available_samples
        if available < samples:
            self.log.debug(f"Waiting for samples: {available}/{samples}")
            # Wait for bulk time
            time.sleep((samples - available) / self.sample_rate)
            available = self.available_samples
            # Wait a little more if necessary
            while available < samples:
                time.sleep(1 / self.sample_rate)
                available = self.available_samples
        return available


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
        #with self._thread_lock:
        if self.module_state() != 'locked':
            raise RuntimeError('Unable to read data. Stream is not running.')
        if (self.constraints.sample_timing == SampleTiming.TIMESTAMP) and timestamp_buffer is None:
            raise RuntimeError('SampleTiming.TIMESTAMP mode requires a timestamp buffer array')

        n_ch = self.channel_count
        if data_buffer.size < samples_per_channel * n_ch:
            rais555555e RuntimeError(
                f'data_buffer too small ({data_buffer.size:d}) to hold all requested '
                f'samples for all channels ({channel_count:d} * {samples_per_channel:d} = '
                f'{samples_per_channel * channel_count:d})'
            )
        if (timestamp_buffer is not None) and (timestamp_buffer.size < samples_per_channel):
            raise RuntimeError(
                f'timestamp_buffer too small ({timestamp_buffer.size:d}) to hold all requested '
                f'samples ({samples_per_channel:d})'
            )

        self.log.debug(f"Trying to read {samples_per_channel} samples")
        self._wait_get_available_samples(samples_per_channel)
        #time.sleep(5)


        n_samples = min(self.__available_samples, samples_per_channel) * n_ch
        idx_start = self.__idx_read_start * n_ch
        idx_end = idx_start + n_samples
        idx_t_start = self.__idx_read_start
        idx_t_end = idx_t_start + n_samples

        # todo: broken for >1 channels
        data_buffer[:] = np.roll(data_buffer, n_samples)
        timestamp_buffer[:] = np.roll(timestamp_buffer, n_samples // n_ch)
        data_buffer[:n_samples] = cp.copy(self._sample_buffer)[idx_start:idx_end]
        timestamp_buffer[:n_samples//n_ch] = cp.copy(self._timestamp_buffer)[idx_t_start:idx_t_end]

        n_samples //= n_ch
        # reset memory pointer to overwritten alread read data
        self.__pos_sample_buffer = max(1,idx_start)
        self.__idx_read_start = self.__pos_sample_buffer
        self.__available_samples -= n_samples


        self.log.debug(f"Filled data idx={idx_start}/{idx_end} into buffer:"
                       f" {data_buffer}, t= {timestamp_buffer}."
                       f" Write pos: {self.__pos_sample_buffer}")

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
        raise NotImplementedError
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

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
        raise NotImplementedError
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
        raise NotImplementedError
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


