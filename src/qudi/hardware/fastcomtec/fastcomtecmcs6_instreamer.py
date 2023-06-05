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
import time
from os import path
from psutil import virtual_memory
from psutil._common import bytes2human

from qudi.util.datastorage import create_dir_for_file
from qudi.core.configoption import ConfigOption
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, StreamingMode, StreamChannel, StreamChannelType
from qudi.hardware.fastcomtec.fastcomtecmcs6 import AcqStatus, BOARDSETTING, ACQDATA, AcqSettings

class FastComtec(DataInStreamInterface):
    """ Hardware Class for the FastComtec Card.

    stable: Jochen Scheuer, Simon Schmitt

    Example config for copy-paste:

    fastcomtec_mcs6:
        module.Class: 'fastcomtec.fastcomtecmcs6.FastComtec'
        options:
    """
    # config options
    _digital_sources = ConfigOption(name='digital_sources', missing='error') # specify the digital channels on the device that should be used for streaming in data
    _max_read_block_size = ConfigOption(name='max_read_block_size', default=10000, missing='info') # specify the number of lines that can at max be read into the memory of the computer from the list file of the device
    _chunk_size = ConfigOption(name='chunk_size', default=10000, missing='nothing')
    _data_type = ConfigOption(name='data_type', default=np.int32, missing='info')
    _dll_path = ConfigOption(name='dll_path', default='C:\Windows\System32\DMCS6.dll', missing='info')

    # Todo: can be extracted from list file
    _memory_ratio = ConfigOption(name='memory_ratio', default=0.8, missing='nothing') # relative amount of memory that can be used for reading measurement data into the systems memory
    _line_size = ConfigOption(name='line_size', default=4, missing='nothing') # how many bytes does one line of measurement data have

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._sample_rate = None
        self._buffer_size = None
        self._use_circular_buffer = None
        #self._data_type = None
        self._streaming_mode = None
        self._stream_length = None
        self._active_channels = tuple()

        self._data_buffer = np.empty(0, dtype=self._data_type)
        self._has_overflown = False
        self._read_lines = None
        self._filename = None

        self._constraints = None

        self._available_memory = virtual_memory().available * self._memory_ratio

        

    def on_activate(self):
        self.dll = ctypes.windll.LoadLibrary(self._dll_path)
        self._streaming_mode = StreamingMode.CONTINUOUS
        
        # Create constraints
        self._constraints = DataInStreamConstraints(
            digital_channels=tuple(
                StreamChannel(name=src,
                              type=StreamChannelType.DIGITAL,
                              unit='counts') for src in self._digital_sources
            ),
            # TODO correctly implement the minimal sample_rate
            digital_sample_rate={'default'    : 1,
                                 'bounds'     : (0.1, 1/200e-12),
                                 'increment'  : 0.1,
                                 'enforce_int': False},
            combined_sample_rate={'default'    : 1,
                                 'bounds'     : (0.1, 1/100e-12),
                                 'increment'  : 0.1,
                                 'enforce_int': False},
            read_block_size={'default'    : 1,
                             'bounds'     : (1, int(self._max_read_block_size)),
                             'increment'  : 1,
                             'enforce_int': True},
            streaming_modes=(StreamingMode.CONTINUOUS,),  # TODO: Implement FINITE streaming mode
            data_type=np.int32,  #np.float64
            allow_circular_buffer=True
        )
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
    def sample_rate(self) -> float:
        """
        The currently set sample rate

        @return float: current sample rate in Hz
        """
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, samplerate: float):
        """
        Set the sample rate

        @param float, samplerate: samplerate that should be set
        """
        self._sample_rate = samplerate

    @property
    def data_type(self) -> type:
        """
        Read-only property.
        The data type of the stream data. Must be numpy type.

        @return type: stream data type (numpy type)
        """
        return self._data_type

    @property
    def buffer_size(self) -> int:
        """
        The currently set buffer size.
        Buffer size corresponds to the number of samples that can be buffered. 
        It determines the number of lines than can be read by from the measurement file to the internal memory of the PC.

        @return int: current buffer size in samples per channel
        """
        return self._buffer_size

    @buffer_size.setter
    def buffer_size(self, buffersize: int):
        """
        Sets number of lines that can be read from the measurement file into the memory of the PC.

        @param int, buffersize: number of lines that should be read into the memory
        """
        if buffersize * self._line_size > self._available_memory:
            self.log.error(f"The required buffersize ({bytes2human(buffersize)}B) is greater than 80 % of the available memory ({bytes2human(available_memory)}B). Can't set new buffersize.")
            return
        self._buffer_size = buffersize

    @property
    def use_circular_buffer(self) -> bool:
        """
        A flag indicating if circular sample buffering is being used or not.

        @return bool: indicate if circular sample buffering is used (True) or not (False)
        """
        bsetting=BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        if bsetting.sweepmode == 1978500:
            self._use_circular_buffer = True
        if bsetting.sweepmode == 1978496:
            self._use_circular_buffer = False
        return self._use_circular_buffer

    @use_circular_buffer.setter
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
        
    @property
    def streaming_mode(self) -> StreamingMode:
        """
        The currently configured streaming mode Enum.

        @return StreamingMode: Finite (StreamingMode.FINITE) or continuous
                               (StreamingMode.CONTINUOUS) data acquisition
        """
        bsetting=BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        if bsetting.sweepmode == 1880272:
            self._streaming_mode = StreamingMode.CONTINUOUS
            return self._streaming_mode
        self._streaming_mode = StreamingMode.FINITE
        return self._streaming_mode

    @streaming_mode.setter
    def streaming_mode(self, mode: int):
        """
        Method to set the streaming mode

        @param int mode: value that is in the StreamingMode class (StreamingMode.CONTINUOUS: 0, StreamingMode.FINITE: 1)
        """
        if self._is_not_settable("streaming mode"):
            return
        mode = StreamingMode(mode)
        if mode not in self._constraints.streaming_modes:
            self.log.error('Unknown streaming mode "{0}" encountered.\nValid modes are: {1}.'
                           ''.format(mode, self._constraints.streaming_modes))
            return
        if mode == StreamingMode.FINITE:
            cmd = 'sweepmode={0}'.format(hex(1978496))
        else:
            cmd = 'sweepmode={0}'.format(hex(1978500))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        self._streaming_mode = mode
        return mode.value

    @property
    def stream_length(self) -> int:
        """
        Property holding the total number of samples per channel to be acquired by this stream.
        This number is only relevant if the streaming mode is set to StreamingMode.FINITE.

        @return int: The number of samples to acquire per channel. Ignored for continuous streaming.
        """
        setting = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(setting), 0)
        self._stream_length = setting.range
        return self._stream_length
    
    @stream_length.setter
    def stream_length(self, length: int):
        if self._is_not_settable('stream length'):
            return
        length = int(length)
        if length < 1:
            self.log.error('Stream_length must be a positive integer >= 1.')
            return
        cmd = 'range={0}'.format(int(length))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        self._stream_length = length

    @property
    def all_settings(self) -> dict:
        """
        Read-only property to return a dict containing all current settings and values that can be
        configured using the method "configure". Basically returns the same as "configure".

        @return dict: Dictionary containing all configurable settings
        """
        return {'sample_rate': self._sample_rate,
                'streaming_mode': self._streaming_mode,
                'active_channels': self.active_channels,
                'stream_length': self._stream_length,
                'buffer_size': self._buffer_size,
                'use_circular_buffer': self._use_circular_buffer}

    @property
    def number_of_channels(self) -> int:
        """
        Read-only property to return the currently configured number of active data channels.

        @return int: the currently set number of channels
        """
        return len(self._active_channels)

    @property
    def active_channels(self) -> tuple:
        """
        The currently configured data channel properties.
        Returns a dict with channel names as keys and corresponding StreamChannel instances as
        values.

        @return dict: currently active data channel properties with keys being the channel names
                      and values being the corresponding StreamChannel instances.
        """
        constr = self._constraints
        return(*(ch.copy() for ch in constr.digital_channels if ch.name in self._active_channels),
               *(ch.copy() for ch in constr.analog_channels if ch.name in self._active_channels))

    @active_channels.setter
    def active_channels(self, channels: list):
        if self._is_not_settable('active channels'):
            return
        avail_channels = tuple(ch.name for ch in self.available_channels)
        if any(ch not in avail_channels for ch in channels):
            self.log.error('Invalid channel to stream from encountered {0}.\nValid channels '
                           'are: {1}'
                           ''.format(tuple(channels), tuple(ch.name for ch in self.available_channels)))
            return
        self._active_channels = tuple(channels)
        return

    @property
    def available_channels(self):
        """
        Read-only property to return the currently used data channel properties.
        Returns a dict with channel names as keys and corresponding StreamChannel instances as
        values.

        @return dict: data channel properties for all available channels with keys being the channel
                      names and values being the corresponding StreamChannel instances.
        """
        return (*(ch.copy() for ch in self._constraints.digital_channels),
                *(ch.copy() for ch in self._constraints.analog_channels))

    @property
    def available_samples(self):
        """
        Read-only property to return the currently available number of samples per channel ready
        to read from buffer.

        @return int: Number of available samples per channel
        """
        if not self._filename:
            self.log.error("No filename has been specified yet. First call the change_filename function to create a file.")
            return
        # open the raw file and count the \n
        with open(self._filename, 'rb') as fp:
            generator = self._count_generator(fp.raw.read)
            # sum over all lines in the file and subtract the number of header lines
            count = sum(buffer.count(b'\n') for buffer in generator) - self._header_length
        return count

    def _count_generator(self, reader):
        b = reader(1024 * 1024)
        while b:
            yield b
            b = reader(1024 * 1024)

    @property
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

    @property
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
                0: False, # stopped
                1: True, # running
                3: False, # read out in progress
                }
        # what values are returned from the dll?
        status=AcqStatus()
        self.dll.GetStatusData(ctypes.byref(status), 0) 
        # self.log.warn(f"status.started = {status.started}") 
        try:
            return return_dict[status.started]
        except KeyError:
            self.log.error(
                'There is an unknown status from FastComtec. The status message was %s' % (str(status.started)))
            return
    
    def _is_not_settable(self, option: str = ""):
        """
        Method that checks whether the FastComtec is running. Throws an error if it is running.
        @return bool: True - device is running, can't set options: False - Device not running, can set options
        """
        if self.is_running:
            if option:
                self.log.error(f"Can't set {option} as an acquisition is currently running.")
            else:
                self.log.error(f"Can't set option as an acquisition is currently running.")
            return True
        return False


    def configure(self, sample_rate=None, streaming_mode=None, active_channels=None,
                  total_number_of_samples=None, buffer_size=None, use_circular_buffer=None):
        """
        Method to configure all possible settings of the data input stream.

        @param float sample_rate: The sample rate in Hz at which data points are acquired
        @param StreamingMode streaming_mode: The streaming mode to use (finite or continuous)
        @param iterable active_channels: Iterable of channel names (str) to be read from.
        @param int total_number_of_samples: In case of a finite data stream, the total number of
                                            samples to read per channel
        @param int buffer_size: The size of the data buffer to pre-allocate in samples per channel
        @param bool use_circular_buffer: Use circular buffering (True) or stop upon buffer overflow
                                         (False)

        @return dict: All current settings in a dict. Keywords are the same as kwarg names.
        """
        # TODO: how to handle, if no arguments are passed
        if sample_rate == None:
            sample_rate = self._sample_rate
        if streaming_mode == None:
            streaming_mode = self._streaming_mode
        if active_channels == None:
            active_channels = self._active_channels
        if total_number_of_samples == None and use_circular_buffer:
            self.log.error("Set finite sample acquisition, but the total number of samples was not set.")
            return
        if buffer_size == None:
            buffer_size = self.buffer_size
        if use_circular_buffer == None:
            use_circular_buffer = False

        # set the settings
        self.streaming_mode = streaming_mode
        self.use_circular_buffer = use_circular_buffer
        self.active_channels = active_channels

        self.buffer_size = buffer_size
        self.stream_length = total_number_of_samples
        self.sample_rate = sample_rate

        return self.all_settings

    def start_stream(self):
        """
        Start the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        # TODO: implement the correct return code
        self.change_save_mode(2)
        status = self.dll.Start(0)
        while not self.is_running:
            time.sleep(0.05)
        return status

    def stop_stream(self):
        """
        Stop the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        # TODO: implement the correct return code
        status = self.dll.Halt(0)
        while self.is_running:
            time.sleep(0.05)
        # set the fastcounter save mode back to standard again
        self.change_save_mode(0)
        return status

    def read_data_into_buffer(self, buffer, number_of_samples=None):
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            buffer.shape == (self.number_of_channels, number_of_samples)
        The numpy array must have the same data type as self.data_type.
        If number_of_samples is omitted it will be derived from buffer.shape[1]

        This method will not return until all requested samples have been read or a timeout occurs.

        @param numpy.ndarray buffer: The numpy array to write the samples to
        @param int number_of_samples: optional, number of samples to read per channel. If omitted,
                                      this number will be derived from buffer axis 1 size.

        @return int: Number of samples read into buffer; negative value indicates error
                     (e.g. read timeout)
        """
        if read_data_sanity_check(buffer, number_of_samples) < 1:
            return -1

        if buffer.ndim == 2:
            number_of_samples = buffer.shape[1] if number_of_samples is None else number_of_samples
            buffer = buffer.flatten()
        else:
            if number_of_samples is None:
                number_of_samples = buffer.size // self.number_of_channels

        if number_of_samples < 1:
            return 0

        data = read_data_from_file(number_of_samples=number_of_samples)
        if len(data) < number_of_samples:
            # Todo: find better solution when you have to wait for more data
            read_data_into_buffer(buffer, number_of_samples)
        buffer[:number_of_samples] = data
        self._read_lines += number_of_samples
        return number_of_samples

    def read_available_data_into_buffer(self, buffer):
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            buffer.shape == (self.number_of_channels, number_of_samples)
        The numpy array must have the same data type as self.data_type.

        This method will read all currently available samples into buffer. If number of available
        samples exceed buffer size, read only as many samples as fit into the buffer.

        @param numpy.ndarray buffer: The numpy array to write the samples to

        @return int: Number of samples read into buffer; negative value indicates error
                     (e.g. read timeout)
        """
        if read_data_sanity_check(buffer) < 0:
            return -1

        data = read_data_from_file()
        number_of_samples = len(data)
        buffer[:number_of_samples] = data
        self._read_lines += number_of_samples
        return number_of_samples

    def read_data(self, number_of_samples=None):
        """
        Read data from the stream buffer into a 2D numpy array and return it.
        The arrays first index corresponds to the channel number and the second index serves as
        sample index:
            return_array.shape == (self.number_of_channels, number_of_samples)
        The numpy arrays data type is the one defined in self.data_type.
        If number_of_samples is omitted all currently available samples are read from buffer.

        This method will not return until all requested samples have been read or a timeout occurs.

        If no samples are available, this method will immediately return an empty array.
        You can check for a failed data read if number_of_samples != <return_array>.shape[1].

        @param int number_of_samples: optional, number of samples to read per channel. If omitted,
                                      all available samples are read from buffer.

        @return numpy.ndarray: The read samples in a numpy array
        """
        if not self.is_running:
            self.log.error('Unable to read data. Device is not running.')
            return np.empty((0, 0), dtype=self._data_type)

        if number_of_samples is None:
            read_samples = self.read_available_data_into_buffer(self._data_buffer)
            if read_samples < 0:
                return np.empty((0, 0), dtype=self._data_type)
        else:
            read_samples = self.read_data_into_buffer(self._data_buffer,
                                                      number_of_samples=number_of_samples)
            if read_samples != number_of_samples:
                return np.empty((0, 0), dtype=self._data_type)

        total_samples = self.number_of_channels * read_samples
        return self._data_buffer[:total_samples].reshape((self.number_of_channels,
                                                          number_of_samples))

    def read_single_point(self):
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
        pass
    
    def get_constraints(self):
        """
        Return the constraints on the settings for this data streamer.

        @return DataInStreamConstraints: Instance of DataInStreamConstraints containing constraints
        """
        return self._constraints

#################################### Methods for saving ###############################################

    def change_filename(self, name: str):
        """ Changes filelocation to the default data directory and appends the given name as a file name 

        @param str name: Name of the file

        @return str filelocation: complete path to the file
        """
        # join the default data dir with the file name
        filelocation =  path.normpath(path.join(self.module_default_data_dir, name))
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
        if not self.is_running:
            self.log.error('Unable to read data. Device is not running.')
            return -1

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
            number_of_chunks = int(self.buffer.shape[1] / chunk_size)  #float('inf')
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
