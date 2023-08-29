# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware file implementation for FastComtec p7887 .

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

import ctypes
import numpy as np
from typing import Union, Type, Iterable, Mapping, Optional, Dict, List, Tuple, Sequence
import time
from os import path
from psutil import virtual_memory
from psutil._common import bytes2human

from qudi.util.datastorage import create_dir_for_file
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming
from qudi.hardware.fastcomtec.fastcomtecmcs6 import AcqStatus, BOARDSETTING, ACQDATA, AcqSettings


class FastComtecInstreamer(DataInStreamInterface):
    """ Hardware Class for the FastComtec Card.

    Example config for copy-paste:

    fastcomtec_mcs6_instreamer:
        module.Class: 'fastcomtec.fastcomtecmcs6_instreamer.FastComtecInstreamer'
        options:
    """
    # config options
    _channel_names = ConfigOption(name='channel_names',
                                  missing='error',
                                  constructor=lambda names: [str(x) for x in names])
    _channel_units = ConfigOption(name='channel_units',
                                  missing='error',
                                  constructor=lambda units: [str(x) for x in units])
    _data_type = ConfigOption(name='data_type',
                              default='int32',
                              missing='info',
                              constructor=lambda typ: np.dtype(typ).type)
    _sample_timing = ConfigOption(name='sample_timing',
                                  default='RANDOM',
                                  missing='info',
                                  constructor=lambda timing: SampleTiming[timing.upper()])

    _max_read_block_size = ConfigOption(name='max_read_block_size', default=10000,
                                        missing='info')  # specify the number of lines that can at max be read into the memory of the computer from the list file of the device
    _chunk_size = ConfigOption(name='chunk_size', default=10000, missing='nothing')
    _dll_path = ConfigOption(name='dll_path', default='C:\Windows\System32\DMCS6.dll', missing='info')
    _memory_ratio = ConfigOption(name='memory_ratio', default=0.8,
                                 missing='nothing')  # relative amount of memory that can be used for reading measurement data into the system's memory

    # Todo: can be extracted from list file
    _line_size = ConfigOption(name='line_size', default=4,
                              missing='nothing')  # how many bytes does one line of measurement data have

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._active_channels = list()
        self._sample_rate = None
        self._buffer_size = None
        self._use_circular_buffer = None
        self._streaming_mode = None
        self._stream_length = None

        self._data_buffer = np.empty(0, dtype=self._data_type)
        self._has_overflown = False
        self._read_lines = None
        self._filename = None

        self._constraints = None

        # Todo: is this the memory of the disc where the file will be written?
        self._available_memory = int(virtual_memory().available * self._memory_ratio)

    def on_activate(self):
        self.dll = ctypes.windll.LoadLibrary(self._dll_path)
        self._streaming_mode = StreamingMode.CONTINUOUS

        # Create constraints
        self._constraints = DataInStreamConstraints(
            channel_units=dict(zip(self._channel_names, self._channel_units)),
            sample_timing=self._sample_timing,
            streaming_modes=[StreamingMode.CONTINUOUS,],  # TODO: Implement FINITE streaming mode
            data_type=self._data_type,
            channel_buffer_size=ScalarConstraint(default=1024 ** 2,
                                                 bounds=(128, self._available_memory),
                                                 increment=1,
                                                 enforce_int=True),
            sample_rate=ScalarConstraint(default=1, bounds=(0.1, 1 / 200e-12), increment=0.1, enforce_int=False)
        )
        self._active_channels = list(self._constraints.channel_units)

        # TODO implement the constraints for the fastcomtec max_bins, max_sweep_len and hardware_binwidth_list
        # Reset data buffer
        self._data_buffer = np.empty(0, dtype=self._data_type)
        self._has_overflown = False
        self._read_lines = 0

    def on_deactivate(self):
        # Free memory if possible while module is inactive
        self._data_buffer = np.empty(0, dtype=self._data_type)
        return

    @property
    def constraints(self) -> DataInStreamConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
        return self._constraints

    @property
    def available_samples(self) -> int:
        """ Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        """
        if not self._filename:
            self.log.error(
                "No filename has been specified yet. First call the change_filename function to create a file.")
            return
        # open the raw file and count the \n
        with open(self._filename, 'rb') as fp:
            generator = self._count_generator(fp.raw.read)
            # sum over all lines in the file and subtract the number of header lines
            count = sum(buffer.count(b'\n') for buffer in generator) - self._header_length
        return count

    @property
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.
        As here it is not timing mode SampleTiming.CONSTANT this property represents only a
        hint to the actual hardware timebase and can not be considered accurate.
        """
        return self._sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self._buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        if bsetting.sweepmode == 1880272:
            self._streaming_mode = StreamingMode.CONTINUOUS
            return self._streaming_mode
        self._streaming_mode = StreamingMode.FINITE
        return self._streaming_mode

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
        if self._is_not_settable('active channels'):
            return
        channels = set(channels)
        if not channels.issubset(self._constraints.channel_units):
            raise ValueError(f'Invalid channels to set active {channels}. Allowed channels are '
                             f'{set(self._constraints.channel_units)}')
        channel_shapes = {ch: self._channel_signals[idx] for idx, ch in
                          enumerate(self._constraints.channel_units) if ch in channels}
        self._active_channels = list(channel_shapes)

    def _set_streaming_mode(self, mode: Union[StreamingMode, int]) -> None:
        if self._is_not_settable("streaming mode"):
            return
        try:
            mode = StreamingMode(mode.value)
        except AttributeError:
            mode = StreamingMode(mode)
        if (mode == StreamingMode.INVALID) or mode not in self._constraints.streaming_modes:
            raise ValueError(
                f'Invalid streaming mode to set ({mode}). Allowed StreamingMode values are '
                f'[{", ".join(str(mod) for mod in self._constraints.streaming_modes)}]'
            )
        if mode == StreamingMode.FINITE:
            cmd = 'sweepmode={0}'.format(hex(1978496))
        else:
            cmd = 'sweepmode={0}'.format(hex(1978500))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        self._streaming_mode = mode

    def _set_channel_buffer_size(self, samples: int) -> None:
        self._constraints.channel_buffer_size.check(samples)
        self._buffer_size = buffersize

    def _set_sample_rate(self, rate: Union[int, float]) -> None:
        rate = float(rate)
        self._constraints.sample_rate.check(rate)
        self._sample_rate = rate

    def _is_not_settable(self, option: str = "") -> bool:
        """
        Method that checks whether the FastComtec is running. Throws an error if it is running.
        @return bool: True - device is running, can't set options: False - Device not running, can set options
        """
        if self.module_state() == 'locked':
            if option:
                self.log.error(f"Can't set {option} as an acquisition is currently running.")
            else:
                self.log.error(f"Can't set option as an acquisition is currently running.")
            return True
        return False

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.module_state.lock()
            self.change_save_mode(2)
            status = self.dll.Start(0)
            while not self.is_running():
                time.sleep(0.05)

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        with self._thread_lock:
            if self.module_state() == 'locked':
                self.module_state.unlock()
            status = self.dll.Halt(0)
            while self.is_running():
                time.sleep(0.05)
            # set the fastcounter save mode back to standard again
            self.change_save_mode(0)
        return status

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
            if read_data_sanity_check(data_buffer) < 0:
                raise

        if data_buffer.ndim == 2:
            data_buffer = data_buffer.flatten()
        if samples_per_channel is None:
            samples_per_channel = data_buffer.size // self.number_of_channels

        if samples_per_channel < 1:
            return

        data = read_data_from_file(number_of_samples=samples_per_channel)
        if len(data) < samples_per_channel:
            # Todo: find better solution when you have to wait for more data
            read_data_into_buffer(data_buffer, samples_per_channel)
        data_buffer = np.append(data_buffer, data)
        self._read_lines += samples_per_channel

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
            if read_data_sanity_check(data_buffer) < 0:
                raise

            if data_buffer.ndim == 2:
                data_buffer = data_buffer.flatten()

            data = read_data_from_file()
            number_of_samples = len(data) // self.number_of_channels()
            data_buffer = np.append(data_buffer, data)
            self._read_lines += number_of_samples
            return number_of_samples

    def read_data(self,
                  number_of_samples: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """ Read data from the stream buffer into a 1D numpy array and return it.
        All samples for each channel are stored in consecutive blocks one after the other.
        The returned data_buffer can be unraveled into channel samples with:

            data_buffer.reshape([<channel_count>, number_of_samples])

        The numpy array data type is the one defined in self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.

        If number_of_samples is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        """
        with self._thread_lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

            if number_of_samples is None:
                read_samples = self.read_available_data_into_buffer(self._data_buffer)
                if read_samples < 0:
                    return np.empty((0, 0), dtype=self._data_type), None
            else:
                read_samples = self.read_data_into_buffer(self._data_buffer,
                                                          samples_per_channel=number_of_samples)
                if read_samples != number_of_samples:
                    return np.empty((0, 0), dtype=self._data_type), None

            total_samples = self.number_of_channels * read_samples
            return self._data_buffer, None

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """
        This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        @return numpy.ndarray: 1D array containing one sample for each channel. Empty array
                               indicates error.
        """
        # TODO: Read only the last line from the file or next line to last that was read
        return (np.array(0, dtype=self._data_type), None)

    def is_running(self):
        """
        Read-only flag indicating if the data acquisition is running.

        Receives the current status of the Fast Counter and outputs it as return value.
        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state

        @return bool: Data acquisition is running (True) or not (False)
        """
        # dict that specifies the values the DLL returns and their associated is_running outputs.
        return_dict = {
            0: False,  # stopped
            1: True,  # running
            3: False,  # read out in progress
        }
        # what values are returned from the dll?
        status = AcqStatus()
        self.dll.GetStatusData(ctypes.byref(status), 0)
        # self.log.warn(f"status.started = {status.started}")
        try:
            return return_dict[status.started]
        except KeyError:
            raise KeyError(
                'There is an unknown status from FastComtec. The status message was %s' % (str(status.started)))

    ################################ Methods for saving ################################

    def change_filename(self, name: str):
        """ Changes filelocation to the default data directory and appends the given name as a file name 

        @param str name: Name of the file

        @return str filelocation: complete path to the file
        """
        # join the default data dir with the file name
        filelocation = path.normpath(path.join(self.module_default_data_dir, name))
        # create the directories to the filelocation
        create_dir_for_file(filelocation)
        # send the command for the filelocation to the dll
        cmd = 'mpaname=%s' % filelocation
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        self._filename = filelocation
        return filelocation

    def change_save_mode(self, mode):
        """ Changes the save mode of Mcs6

        @param int mode: Specifies the save mode (0: No Save at Halt, 1: Save at Halt,
                        2: Write list file, No Save at Halt, 3: Write list file, Save at Halt

        @return int mode: specified save mode
        """
        cmd = 'savedata={0}'.format(mode)
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return mode

    def save_data(self, filename: str):
        """ save the current settings and data in the default data directory.

        @param str filename: Name of the savefile

        @return str filelocation: path to the saved file
        """
        filelocation = self.change_filename(filename)
        cmd = 'savempa'
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return filelocation

    # =============================================================================================
    def read_data_sanity_check(self, buffer, number_of_samples=None):
        if not isinstance(buffer, np.ndarray) or buffer.dtype != self._data_type:
            self.log.error('buffer must be numpy.ndarray with dtype {0}. Read failed.'
                           ''.format(self._data_type))
            return -1

        if buffer.ndim == 2:
            if buffer.shape[0] != self.number_of_channels:
                self.log.error('Configured number of channels ({0:d}) does not match first '
                               'dimension of 2D buffer array ({1:d}).'
                               ''.format(self.number_of_channels, buffer.shape[0]))
                return -1
        elif buffer.ndim == 1:
            pass
        else:
            self.log.error('Buffer must be a 1D or 2D numpy.ndarray.')
            return -1

        # Check for buffer overflow
        if self.available_samples > self.buffer_size:
            self._has_overflown = True

        if self._filename is None:
            raise TypeError('No filename for data analysis is given.')
        return 0

    def read_data_from_file(self, filename=None, read_lines=None, chunk_size=None, number_of_samples=None):
        if filename is None:
            filename = self._filename
        if read_lines is None:
            read_lines = self._read_lines
        if chunk_size is None:
            chunk_size = self._chunk_size
        if read_lines is None:
            read_lines = 0
        if number_of_samples is None:
            number_of_chunks = int(self.buffer.shape[1] / chunk_size)  # float('inf')
            remaining_samples = self.buffer.shape[1] % chunk_size
        else:
            number_of_chunks = int(number_of_samples / chunk_size)
            remaining_samples = number_of_samples % chunk_size

        extend_data = data.extend  # avoid dots for speed-up

        data = []
        header_length = _find_header_length(filename)
        if header_length < 0:
            self.log.error('Header length could not be determined. Return empty data.')
            return data
        channel_bit, edge_bit, timedata_bit = _extract_data_format(filename)
        if not (channel_bit and edge_bit and timedata_bit):
            self.log.error('Could not extract format style of file {}'.format(filename))
            return data
        timedata_bits = (-(channel_bit + edge_bit + timedata_bit), -(channel_bit + edge_bit))

        with open(filename, 'r') as f:
            list(islice(f, int(header_length + read_lines - 1),
                        int(_header_length + read_lines)))  # ignore header and already read lines
            ii = 0
            while True:
                if ii < number_of_chunks:
                    next_lines = list(islice(f, chunk_size))
                elif ii == number_of_chunks and remaining_samples:
                    next_lines = list(islice(f, remaining_samples))
                else:
                    break
                if not next_lines:  # no next lines, finished file
                    break
                # Todo: this might be quite specific
                # if length of last entry of file isn't 9 there was being written on the file
                # when this function was called
                if len(next_lines[-1]) != 9:
                    break
                # extract arrival time of photons saved in hex to dex
                new_lines = [int(bin(int(s, 16))[timedata_bits[0]:timedata_bits[1]], 2) for s in next_lines]
                extend_data(new_lines)
                ii = ii + 1
        return data

    def _find_header_length(self, filename):
        with open(filename) as f:
            header_length = -1
            for ii, line in enumerate(f):
                if '[DATA]' in line:
                    header_length = ii
                    break
        return header_length

    def _extract_data_format(self, filename):
        with open(filename) as f:
            channel_bit, edge_bit, timedata_bit = (None, None, None)
            for line in f:
                if 'channel' in line:
                    channel_bit = int(line[line.index("bit", 2) - 3:line.index("bit", 2) - 1])
                if 'edge' in line:
                    edge_bit = int(line[line.index("bit", 2) - 3:line.index("bit", 2) - 1])
                if 'timedata' in line:
                    timedata_bit = int(line[line.index("bit", 2) - 3:line.index("bit", 2) - 1])
                if channel_bit and edge_bit and timedata_bit:
                    break
        return channel_bit, edge_bit, timedata_bit

    def _init_buffer(self):
        self._data_buffer = np.zeros(self.number_of_channels * self.buffer_size,
                                     dtype=self._data_type)
        self._has_overflown = False
        return

    ################################ old interface methods ################################
    def use_circular_buffer(self) -> bool:
        """
        A flag indicating if circular sample buffering is being used or not.

        @return bool: indicate if circular sample buffering is used (True) or not (False)
        """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        if bsetting.sweepmode == 1978500:
            self._use_circular_buffer = True
        if bsetting.sweepmode == 1978496:
            self._use_circular_buffer = False
        return self._use_circular_buffer

    def use_circular_buffer(self, flag: bool):
        """
        Set the flag indicating if circular sample buffering is being used or not.
        @param bool, flag: indicate if circular sample buffering is used (True) or not (False)
        """
        if self._is_not_settable("circular buffer"):
            return
        if flag:
            self._use_circular_buffer = False
            self.log.error("No circular buffer implemented. Circular buffer is switched off.")
            return
        self._use_circular_buffer = flag
        cmd = 'sweepmode={0}'.format(hex(1978496))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))

    def number_of_channels(self) -> int:
        """
        Read-only property to return the currently configured number of active data channels.

        @return int: the currently set number of channels
        """
        return len(self._active_channels)

    def _count_generator(self, reader):
        b = reader(1024 * 1024)
        while b:
            yield b
            b = reader(1024 * 1024)

    def buffer_overflown(self):
        """
        Read-only flag to check if the read buffer has overflown.
        In case of a circular buffer it indicates data loss.
        In case of a non-circular buffer the data acquisition should have stopped if this flag is
        coming up.
        Flag will only be reset after starting a new data acquisition.

        @return bool: Flag indicates if buffer has overflown (True) or not (False)
        """
        if self._data_buffer.size * self._data_buffer.itemsize > self._available_memory:
            return True
        return False
