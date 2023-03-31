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

from qudi.core.configoption import ConfigOption
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, StreamingMode, StreamChannel, StreamChannelType

class FastComtec(DataInStreamInterface):
    """ Hardware Class for the FastComtec Card.

    stable: Jochen Scheuer, Simon Schmitt

    Example config for copy-paste:

    fastcomtec_mcs6:
        module.Class: 'fastcomtec.fastcomtecmcs6.FastComtec'
        options:
            

    """
    # config options
    _digital_sources = ConfigOption(name='digital_sources', default=tuple(), missing='info') # specify the digital channels on the device that should be used for streaming in data
    _max_read_block_size = ConfigOption(name='max_read_block_size', default=10000, missing='info') # specify the number of lines that can at max be read into the memory of the computer from the list file of the device

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._sample_rate = None
        self._buffer_size = None
        self._use_circular_buffer = None
        self._data_type = None
        self._streaming_mode = None
        self._stream_length = None

        self._constraints = None
        

    def on_activate(self):
        self.dll = ctypes.windll.LoadLibrary('C:\Windows\System32\DMCS6.dll')
        # if self.gated:
        #     self.change_sweep_mode(gated=True)
        # else:
        #     self.change_sweep_mode(gated=False)
        # return

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
                                 'bounds'     : (0.1, 1/100e-12),
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
            data_type=np.float64,
            allow_circular_buffer=True
        )
        # TODO implement the constraints for the fastcomtec max_bins, max_sweep_len and hardware_binwidth_list

    def on_deactivate(self):
        pass

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
        # TODO implement a useful check whether sample rate is in a sensible range
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
        # TODO implement reasonable check for buffersize's constraints
        self._buffer_size = buffersize

    @property
    def use_circular_buffer(self) -> bool:
        """
        A flag indicating if circular sample buffering is being used or not.

        @return bool: indicate if circular sample buffering is used (True) or not (False)
        """
        return self._use_circular_buffer

    @use_circular_buffer.setter
    def use_circular_buffer(self, flag: bool):
        """
        Set the flag indicating if circular sample buffering is being used or not.
        @param bool, flag: indicate if circular sample buffering is used (True) or not (False) 
        """
        self._use_circular_buffer = flag

    @property
    def streaming_mode(self) -> StreamingMode:
        """
        The currently configured streaming mode Enum.

        @return StreamingMode: Finite (StreamingMode.FINITE) or continuous
                               (StreamingMode.CONTINUOUS) data acquisition
        """
        return self._streaming_mode

    @streaming_mode.setter
    def streaming_mode(self, mode: int):
        """
        Method to set the streaming mode

        @param int mode: value that is in the StreamingMode class (StreamingMode.CONTINUOUS: 0, StreamingMode.FINITE: 1)
        """
        # TODO do we need a settings settable checker?
        # if self._check_settings_change():
        mode = StreamingMode(mode)
        if mode not in self._constraints.streaming_modes:
            self.log.error('Unknown streaming mode "{0}" encountered.\nValid modes are: {1}.'
                           ''.format(mode, self._constraints.streaming_modes))
            return
        self._streaming_mode = mode
        return

    @property
    def stream_length(self) -> int:
        """
        Property holding the total number of samples per channel to be acquired by this stream.
        This number is only relevant if the streaming mode is set to StreamingMode.FINITE.

        @return int: The number of samples to acquire per channel. Ignored for continuous streaming.
        """
        return self._stream_length
    
    @stream_length.setter
    def stream_length(self, length: int):
        # TODO do we need the check whether settings are currently changeable, e.g. are settings changeable during a running measurement?
        # if not then a checker has to be implemented, which checks the status of the device.
        # thus only proceeding when the status is idle or something like that.
        # get_status() method earlier in this file could be used for that
        # if self._check_settings_change():
        length = int(length)
        if length < 1:
            self.log.error('Stream_length must be a positive integer >= 1.')
            return
        self._stream_length = length
        return

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
        return len(self.__active_channels)

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
        return(*(ch.copy() for ch in constr.digital_channels if ch.name in self.__active_channels),
               *(ch.copy() for ch in constr.analog_channels if ch.name in self.__active_channels))

    @active_channels.setter
    def active_channels(self, channels: str):
        # TODO checker for channel settings
        # if self._check_settings_change():
        avail_channels = tuple(ch.name for ch in self.available_channels)
        if any(ch not in avail_channels for ch in channels):
            self.log.error('Invalid channel to stream from encountered ({0}).\nValid channels '
                           'are: {1}'
                           ''.format(tuple(channels), tuple(self.available_channels)))
            return
        self.__active_channels = tuple(channels)
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
        pass

    @property
    def available_samples(self):
        """
        Read-only property to return the currently available number of samples per channel ready
        to read from buffer.

        @return int: Number of available samples per channel
        """
        pass

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
        pass

    @property
    def is_running(self):
        """
        Read-only flag indicating if the data acquisition is running.

        @return bool: Data acquisition is running (True) or not (False)
        """
        pass

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
        pass

    def start_stream(self):
        """
        Start the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        pass

    def stop_stream(self):
        """
        Stop the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        pass

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
        pass

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
        pass

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
        pass

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
        pass
    
    def get_constraints(self):
        """
        Return the constraints on the settings for this data streamer.

        @return DataInStreamConstraints: Instance of DataInStreamConstraints containing constraints
        """
        pass
