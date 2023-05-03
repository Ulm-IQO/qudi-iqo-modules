# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module for the HighFinesse wavemeter. It implements the
DataInStreamInterface. Communication with the hardware is done via callback functions such that no new data is missed.

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
import ctypes

from PySide2 import QtCore
import scipy.interpolate as interpolate

from qudi.core.configoption import ConfigOption
import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants
from qudi.util.mutex import Mutex
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode, StreamChannelType, StreamChannel

# DLL_PATH = 'C:\Windows\System32\wlmData.dll'
DLL_PATH = 'wlmData.dll'

_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_int,
                               ctypes.c_long,
                               ctypes.c_long,
                               ctypes.c_long,
                               ctypes.c_double,
                               ctypes.c_long)


class WavemeterAsInstreamer(DataInStreamInterface):
    """
    HighFinesse wavelength meter as an in-streaming device.

    Example config for copy-paste:

    wavemeter:
        module.Class: 'wavemeter.wavemeter_as_instreamer.WavemeterAsInstreamer'
        options:
            channels:
                1:
                    unit: 'nm'    # wavelength (nm) or frequency (THz)
                    medium: 'vac' # for wavelength: air or vac
                    exposure: 10  # exposure time in ms
                2:
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
                3:
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
                4:
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
    """

    #declare signals
    sigNewWavelength = QtCore.Signal(object)

    # config options
    _wavemeter_ch_config = ConfigOption(
        name='channels',
        default={
            1: {'unit': 'nm', 'medium': 'vac', 'exposure': 10}
        },
        missing='info'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()
        self._callback_function = None
        self._wavemeterdll = None

        self._digital_event_rates = [1000, 1000, 1000, 1000]

        self._data_from_callback = None

        # Internal settings
        self.__sample_rate = -1.0
        self.__data_type = np.float64
        self.__stream_length = -1
        self.__buffer_size = -1
        self.__use_circular_buffer = False
        self.__streaming_mode = None
        self.__active_channels = tuple()
        self.__unit_return_type = {}

        # Data buffer
        self._data_buffer = None
        self._has_overflown = False
        self._last_read = None
        self._start_time = None

        # Stored hardware constraints
        self._constraints = None
        return

    def on_activate(self):
        #############################################
        # Initialisation to access external DLL
        #############################################

        try:
            # imports the spectrometer specific function from dll
            self._wavemeterdll = ctypes.windll.LoadLibrary('wlmData.dll')
        except:
            self.log.critical('There is no Wavemeter installed on this Computer.\n'
                              'Please install a High Finesse Wavemeter and try again.')

        # define function header for a later call
        self._wavemeterdll.Instantiate.argtypes = [ctypes.c_long,
                                                   ctypes.c_long,
                                                   ctypes.POINTER(ctypes.c_long),
                                                   ctypes.c_long]
        self._wavemeterdll.Instantiate.restype = ctypes.POINTER(ctypes.c_long)
        self._wavemeterdll.ConvertUnit.restype = ctypes.c_double
        self._wavemeterdll.ConvertUnit.argtypes = [ctypes.c_double, ctypes.c_long, ctypes.c_long]
        self._wavemeterdll.SetExposureNum.restype = ctypes.c_long
        self._wavemeterdll.SetExposureNum.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_long]

        self.__sample_rate = self.get_constraints().combined_sample_rate.min
        self.__data_type = np.float64
        self.__stream_length = 0
        self.__buffer_size = 1000
        self.__use_circular_buffer = False
        self.__streaming_mode = StreamingMode.CONTINUOUS
        constr = self.get_constraints()
        self.__active_channels = tuple(ch.copy() for ch in constr.analog_channels if ch.name)

        # configure wavemeter units and exposure time
        for ch, info in self._wavemeter_ch_config.items():
            unit = info['unit']
            medium = info['medium']
            if unit == 'THz' or unit == 'Hz':
                self.__unit_return_type[ch] = high_finesse_constants.cReturnFrequency
            elif unit == 'nm' or unit == 'm':
                if medium == 'vac':
                    self.__unit_return_type[ch] = high_finesse_constants.cReturnWavelengthVac
                elif medium == 'air':
                    self.__unit_return_type[ch] = high_finesse_constants.cReturnWavelengthAir
                else:
                    self.log.error(f'Invalid medium: {medium}. Valid media are vac and air.')
            else:
                self.log.error(f'Invalid unit: {unit}. Valid units are THz and nm.')

            try:
                exp_time = info['exposure']
            except KeyError:
                continue
            res = self._wavemeterdll.SetExposureNum(ch, 1, exp_time)
            if res != 0:
                self.log.error('Wavemeter error while setting exposure time.')

        # Reset data buffer
        self._init_buffer()
        self._last_read = None
        self._start_time = None
        return

    def on_deactivate(self):  # TODO
        self.stop_stream()

        self._has_overflown = False
        self._last_read = None
        # Free memory if possible while module is inactive
        self._init_buffer()

        try:
            # clean up by removing reference to the ctypes library object
            del self._wavemeterdll
            return 0
        except:
            self.log.error('Could not unload the wlmData.dll of the '
                           'wavemeter.')

    def get_constraints(self):
        """
        Return the constraints on the settings for this data streamer.

        @return DataInStreamConstraints: Instance of DataInStreamConstraints containing constraints
        """
        # Create constraints
        self._constraints = DataInStreamConstraints()
        self._constraints.digital_channels = tuple()

        self._constraints.analog_channels = tuple()
        for i, info in self._wavemeter_ch_config.items():
            timestamp_channel = StreamChannel(name=f'time_ch_{i}', type=StreamChannelType.ANALOG, unit='s')
            data_channel = StreamChannel(name=f'data_ch_{i}', type=StreamChannelType.ANALOG, unit=info['unit'])
            self._constraints.analog_channels += (timestamp_channel, data_channel)

        self._constraints.analog_sample_rate.min = 1
        self._constraints.analog_sample_rate.max = 2 ** 31 - 1
        self._constraints.analog_sample_rate.step = 1
        self._constraints.analog_sample_rate.unit = 'Hz'
        self._constraints.digital_sample_rate.min = 1
        self._constraints.digital_sample_rate.max = 2 ** 31 - 1
        self._constraints.digital_sample_rate.step = 1
        self._constraints.digital_sample_rate.unit = 'Hz'
        self._constraints.combined_sample_rate = self._constraints.analog_sample_rate

        self._constraints.read_block_size.min = 1
        self._constraints.read_block_size.max = 1000000
        self._constraints.read_block_size.step = 1

        # TODO: Implement FINITE streaming mode
        self._constraints.streaming_modes = (StreamingMode.CONTINUOUS,)  # , StreamingMode.FINITE)
        self._constraints.data_type = np.float64
        self._constraints.allow_circular_buffer = True

        return self._constraints

    def _get_callback_ex(self):
        """
        Define the callback procedure that should be called by the dll every time a new measurement result
        is available or any of the wavelength meter's states changes.
        :return: callback function
        """
        def handle_callback(version, mode, intval, dblval, res1):
            """
            Function called upon wavelength meter state change or if a new measurement result is available.
            See wavemeter manual section on CallbackProc for details.

            In this implementation, the new wavelength is converted to the desired unit and
            appended to a list together with the current timestamp.

            :param version: Device version number which called the procedure.
            Only relevant if multiple wavemeter applications are running.
            :param mode: Indicates which state has changed or what new result is available.
            :param intval: Contains the time stamp rounded to ms if mode indicates that the new value is in dblval.
            If not, it contains the new value itself.
            :param dblval: May contain the new value (e.g. wavelength), depending on mode.
            :param res1: Mostly meaningless.
            :return: 0
            """
            with self._lock:
                # got through configured channels to see if new data is from one of them
                for i, (ch, return_unit) in enumerate(self.__unit_return_type.items()):
                    if mode != high_finesse_constants.cmi_wavelength_n[ch]:
                        continue
                    timestamp, wavelength = intval, dblval
                    # unit conversion
                    converted_value = self._wavemeterdll.ConvertUnit(
                        wavelength, high_finesse_constants.cReturnWavelengthVac, return_unit
                    )
                    # saving the data
                    self._data_from_callback[i].append((timestamp, converted_value))

                    #TODO emit signal for wavelength window
                    self.sigNewWavelength.emit(converted_value)
                    break
            return 0

        self._callback_function = _CALLBACK(handle_callback)
        return self._callback_function

    def start_stream(self):
        """
        Start the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        if self.is_running:
            self.log.warning('Unable to start input stream. It is already running.')
            return 0

        self._data_from_callback = tuple([] for _ in self._wavemeter_ch_config.keys())

        # start callback procedure
        self._wavemeterdll.Instantiate(
            high_finesse_constants.cInstNotification,  # long ReasonForCall
            high_finesse_constants.cNotifyInstallCallbackEx,  # long Mode
            ctypes.cast(self._get_callback_ex(), ctypes.POINTER(ctypes.c_long)),  # long P1: function
            0)  # long P2: callback thread priority, 0 = standard

        self._init_buffer()
        self._start_time = time.perf_counter()
        self._last_read = self._start_time
        return 0

    def stop_stream(self):
        """
        Stop the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        if not self.is_running:
            self.log.warning('Unable to stop wavemeter input stream as nothing is running.')
            return 0

        # end callback procedure
        self._wavemeterdll.Instantiate(
            high_finesse_constants.cInstNotification,  # long ReasonForCall
            high_finesse_constants.cNotifyRemoveCallback,  # long mode
            ctypes.cast(self._callback_function, ctypes.POINTER(ctypes.c_long)),  # long P1: function TODO unnecessary?
            0)  # long P2: callback thread priority, 0 = standard
        self._callback_function = None
        self._data_from_callback = None
        return 0

    def read_data_into_buffer(self, buffer, number_of_samples=None):
        """
        Read data from the stream buffer into a 2D numpy array given as parameter.
        Even in the case of a single data channel, the array must be 2D with
        the first index corresponding to the channel number and
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
        if not self.is_running:
            self.log.error('Unable to read data. Device is not running.')
            return -1

        if not isinstance(buffer, np.ndarray) or buffer.dtype != self.__data_type:
            self.log.error('buffer must be numpy.ndarray with dtype {0}. Read failed.'
                           ''.format(self.__data_type))
            return -1

        if buffer.ndim != 2:
            self.log.error('Buffer must be a 2D numpy.ndarray.')
            return -1

        if buffer.shape[0] != self.number_of_channels:
            self.log.error('Configured number of channels ({0:d}) does not match first '
                           'dimension of 2D buffer array ({1:d}).'
                           ''.format(self.number_of_channels, buffer.shape[0]))
            return -1

        number_of_samples = buffer.shape[1] if number_of_samples is None else number_of_samples

        if number_of_samples < 1:
            return 0
        while self.available_samples < number_of_samples:
            # TODO: organize this with signals?
            if not self.is_running:
                break
            time.sleep(0.001)

        # Check for buffer overflow
        if self.available_samples > self.buffer_size:
            self._has_overflown = True

        previous_read = self._last_read
        self._last_read = time.perf_counter()

        with self._lock:
            for i, readings in enumerate(self._data_from_callback):
                timestamp_ch = i * 2
                data_ch = timestamp_ch + 1

                if len(readings) == 0:
                    # no new readings
                    buffer[timestamp_ch, :number_of_samples] = np.nan
                    buffer[data_ch, :number_of_samples] = np.nan

                elif len(readings) == 1:
                    # only one new reading
                    reading = readings.pop()
                    buffer[timestamp_ch, :number_of_samples] = reading[0]
                    buffer[data_ch, :number_of_samples] = reading[1]

                else:
                    # multiple new readings
                    readings_array = np.array(readings)
                    del readings[:len(readings)]
                    timestamps = readings_array[:, 0]
                    measured_values = readings_array[:, 1]

                    # create a function to interpolate in between readings
                    arr_interp = interpolate.interp1d(timestamps, measured_values)
                    # calculate timestamps for which readings were requested
                    new_timestamps = np.linspace(timestamps[0],
                                                 timestamps[0] + self._last_read - previous_read,
                                                 number_of_samples)
                    # perform the actual interpolation to get readings for those timestamps
                    buffer[timestamp_ch, :number_of_samples] = new_timestamps
                    buffer[data_ch, :number_of_samples] = arr_interp(new_timestamps)

        return number_of_samples

    def read_available_data_into_buffer(self, buffer):
        """
        Read data from the stream buffer into a 2D numpy array given as parameter.
        Even in the case of a single data channel, the array must be 2D with
        the first index corresponding to the channel number and
        the second index serving as sample index:
            buffer.shape == (self.number_of_channels, number_of_samples)
        The numpy array must have the same data type as self.data_type.

        This method will read all currently available samples into buffer. If number of available
        samples exceed buffer size, read only as many samples as fit into the buffer.

        @param numpy.ndarray buffer: The numpy array to write the samples to

        @return int: Number of samples read into buffer; negative value indicates error
                     (e.g. read timeout)
        """
        avail_samples = min(buffer.size // self.number_of_channels, self.available_samples)
        return self.read_data_into_buffer(buffer=buffer, number_of_samples=avail_samples)

    def read_data(self, number_of_samples=None):
        """
        Read data from the stream buffer into a 2D numpy array and return it.
        The arrays first index corresponds to the channel number and the second index serves as
        sample index:
            return_array.shape == (self.number_of_channels, number_of_samples)
        The numpy arrays data type is the one defined in self.data_type.
        If number_of_samples is omitted all currently available samples are read from buffer.

        This method will not return until all requested samples have been read or a timeout occurs.

        @param int number_of_samples: optional, number of samples to read per channel. If omitted,
                                      all available samples are read from buffer.

        @return numpy.ndarray: The read samples
        """
        if not self.is_running:
            self.log.error('Unable to read data. Device is not running.')
            return np.empty((0, 0), dtype=self.data_type)

        if number_of_samples is None:
            read_samples = self.read_available_data_into_buffer(self._data_buffer)
            if read_samples < 0:
                return np.empty((0, 0), dtype=self.data_type)
        else:
            read_samples = self.read_data_into_buffer(self._data_buffer,
                                                      number_of_samples=number_of_samples)
            if read_samples != number_of_samples:
                return np.empty((0, 0), dtype=self.data_type)

        return self._data_buffer[:, :read_samples]

    def read_single_point(self):  # TODO
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
        if not self.is_running:
            self.log.error('Unable to read data. Device is not running.')
            return np.empty(0, dtype=self.__data_type)

        data = np.empty(self.number_of_channels, dtype=self.__data_type)
        analog_x = 2 * np.pi * (self._last_read - self._start_time)
        self._last_read = time.perf_counter()
        for i, chnl in enumerate(self.__active_channels):
            if chnl in self._digital_channels:
                ch_index = self._digital_channels.index(chnl)
                events_per_bin = self._digital_event_rates[ch_index] / self.__sample_rate
                data[i] = np.random.poisson(events_per_bin)
            else:
                ch_index = self._analog_channels.index(chnl)
                amplitude = self._analog_amplitudes[ch_index]
                noise_level = 0.05 * amplitude
                noise = noise_level - 2 * noise_level * np.random.rand()
                data[i] = amplitude * np.sin(analog_x) + noise
        return data

    @property
    def sample_rate(self):
        """
        Read-only property to return the currently set sample rate

        @return float: current sample rate in Hz
        """
        return self.__sample_rate

    @sample_rate.setter
    def sample_rate(self, rate):
        if self._check_settings_change():
            if not self._clk_frequency_valid(rate):
                min_val = self._constraints.digital_sample_rate.min
                max_val = self._constraints.digital_sample_rate.max
                self.log.warning(
                    'Sample rate requested ({0:.3e}Hz) is out of bounds. Please choose '
                    'a value between {1:.3e}Hz and {2:.3e}Hz. Value will be clipped to '
                    'the closest boundary.'.format(rate, min_val, max_val))
                rate = max(min(max_val, rate), min_val)
            self.__sample_rate = float(rate)
        return

    @property
    def data_type(self):
        """
        Read-only property to return the currently set data type

        @return type: current data type
        """
        return self.__data_type

    @property
    def buffer_size(self):
        """
        Read-only property to return the currently buffer size.
        Buffer size corresponds to the number of samples per channel that can be buffered. So the
        actual buffer size in bytes can be estimated by:
            buffer_size * number_of_channels * size_in_bytes(data_type)

        @return int: current buffer size in samples per channel
        """
        return self.__buffer_size

    @buffer_size.setter
    def buffer_size(self, size):
        if self._check_settings_change():
            size = int(size)
            if size < 1:
                self.log.error('Buffer size smaller than 1 makes no sense. Tried to set {0} as '
                               'buffer size and failed.'.format(size))
                return
            self.__buffer_size = int(size)
            self._init_buffer()
        return

    @property
    def use_circular_buffer(self):
        """
        Read-only property to return a flag indicating if circular sample buffering is being used
        or not.

        @return bool: indicate if circular sample buffering is used (True) or not (False)
        """
        return self.__use_circular_buffer

    @use_circular_buffer.setter
    def use_circular_buffer(self, flag):
        if self._check_settings_change():
            if flag and not self._constraints.allow_circular_buffer:
                self.log.error('Circular buffer not allowed for this hardware module.')
                return
            self.__use_circular_buffer = bool(flag)
        return

    @property
    def streaming_mode(self):
        """
        Read-only property to return the currently configured streaming mode Enum.

        @return StreamingMode: Finite (StreamingMode.FINITE) or continuous
                               (StreamingMode.CONTINUOUS) data acquisition
        """
        return self.__streaming_mode

    @streaming_mode.setter
    def streaming_mode(self, mode):
        if self._check_settings_change():
            mode = StreamingMode(mode)
            if mode not in self._constraints.streaming_modes:
                self.log.error('Unknown streaming mode "{0}" encountered.\nValid modes are: {1}.'
                               ''.format(mode, self._constraints.streaming_modes))
                return
            self.__streaming_mode = mode
        return

    @property
    def number_of_channels(self):
        """
        Read-only property to return the currently configured number of data channels.

        @return int: the currently set number of channels
        """
        return len(self.__active_channels)

    @property
    def active_channels(self):
        """
        The currently configured data channel properties.
        Returns a dict with channel names as keys and corresponding StreamChannel instances as
        values.

        @return dict: currently active data channel properties with keys being the channel names
                      and values being the corresponding StreamChannel instances.
        """
        constr = self._constraints
        return [ch.copy() for ch in constr.analog_channels if ch.name in self.__active_channels]

    @active_channels.setter
    def active_channels(self, channels):
        if self._check_settings_change():
            channels = tuple(channels)
            avail_chnl_names = tuple(ch.name for ch in self.available_channels)
            if any(ch not in avail_chnl_names for ch in channels):
                self.log.error('Invalid channel to stream from encountered: {0}.\nValid channels '
                               'are: {1}'
                               ''.format(channels, avail_chnl_names))
                return
            self.__active_channels = channels
        return

    @property
    def available_channels(self):
        """
        Read-only property to return the currently used data channel properties.
        Returns a dict with channel names as keys and corresponding StreamChannel instances as
        values.

        @return tuple: data channel properties for all available channels with keys being the
                       channel names and values being the corresponding StreamChannel instances.
        """
        return (ch.copy() for ch in self._constraints.analog_channels)

    @property
    def available_samples(self):
        """
        Read-only property to return the currently available number of samples per channel ready
        to read from buffer.

        @return int: Number of available samples per channel
        """
        if not self.is_running:
            return 0
        return int((time.perf_counter() - self._last_read) * self.__sample_rate)

    @property
    def stream_length(self):
        """
        Property holding the total number of samples per channel to be acquired by this stream.
        This number is only relevant if the streaming mode is set to StreamingMode.FINITE.

        @return int: The number of samples to acquire per channel. Ignored for continuous streaming.
        """
        return self.__stream_length

    @stream_length.setter
    def stream_length(self, length):
        if self._check_settings_change():
            length = int(length)
            if length < 1:
                self.log.error('Stream_length must be a positive integer >= 1.')
                return
            self.__stream_length = length
        return

    @property
    def is_running(self):
        """
        Read-only flag indicating if the data acquisition is running.

        @return bool: Data acquisition is running (True) or not (False)
        """
        return self._callback_function is not None

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
        return self._has_overflown

    @property
    def all_settings(self):
        """
        Read-only property to return a dict containing all current settings and values that can be
        configured using the method "configure". Basically returns the same as "configure".

        @return dict: Dictionary containing all configurable settings
        """
        return {'sample_rate': self.__sample_rate,
                'streaming_mode': self.__streaming_mode,
                'active_channels': self.active_channels,
                'stream_length': self.__stream_length,
                'buffer_size': self.__buffer_size,
                'use_circular_buffer': self.__use_circular_buffer}

    def configure(self, sample_rate=None, streaming_mode=None, active_channels=None,
                  stream_length=None, buffer_size=None, use_circular_buffer=None):
        """
        Method to configure all possible settings of the data input stream.

        @param float sample_rate: The sample rate in Hz at which data points are acquired
        @param StreamingMode streaming_mode: The streaming mode to use (finite or continuous)
        @param iterable active_channels: Iterable of channel names (str) to be read from.
        @param int stream_length: In case of a finite data stream, the total number of
                                            samples to read per channel
        @param int buffer_size: The size of the data buffer to pre-allocate in samples per channel
        @param bool use_circular_buffer: Use circular buffering (True) or stop upon buffer overflow
                                         (False)

        @return dict: All current settings in a dict. Keywords are the same as kwarg names.
        """
        if self._check_settings_change():
            # Handle sample rate change
            if sample_rate is not None:
                self.sample_rate = sample_rate

            # Handle streaming mode change
            if streaming_mode is not None:
                self.streaming_mode = streaming_mode

            # Handle active channels
            if active_channels is not None:
                self.active_channels = active_channels

            # Handle total number of samples
            if stream_length is not None:
                self.stream_length = stream_length

            # Handle buffer size
            if buffer_size is not None:
                self.buffer_size = buffer_size

            # Handle circular buffer flag
            if use_circular_buffer is not None:
                self.use_circular_buffer = use_circular_buffer
        return self.all_settings

    # =============================================================================================
    def _clk_frequency_valid(self, frequency):
        max_rate = self._constraints.digital_sample_rate.max
        min_rate = self._constraints.digital_sample_rate.min
        return min_rate <= frequency <= max_rate

    def _init_buffer(self):
        if not self.is_running:
            self._data_buffer = np.zeros((self.number_of_channels, self.buffer_size), dtype=self.data_type)
            self._has_overflown = False
        return

    def _check_settings_change(self):
        """
        Helper method to check if streamer settings can be changed, i.e. if the streamer is idle.
        Throw a warning if the streamer is running.

        @return bool: Flag indicating if settings can be changed (True) or not (False)
        """
        if self.is_running:
            self.log.warning('Unable to change streamer settings while streamer is running. '
                             'New settings ignored.')
            return False
        return True
