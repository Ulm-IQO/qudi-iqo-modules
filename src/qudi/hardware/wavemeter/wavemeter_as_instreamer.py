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

from qudi.core.configoption import ConfigOption
import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants
from qudi.util.mutex import Mutex
from qudi.util.constraints import ScalarConstraint
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, StreamingMode, \
    SampleTiming

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
                red_laser_1:
                    channel: 1    # channel on the wavemeter switch
                    unit: 'nm'    # wavelength (nm) or frequency (THz)
                    medium: 'vac' # for wavelength: air or vac
                    exposure: 10  # exposure time in ms
                red_laser_2:
                    channel: 2
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
                green_laser:
                    channel: 3
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
                yellow_laser:
                    channel: 4
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
    """

    # declare signals
    sigNewWavelength = QtCore.Signal(object)

    # config options
    _wavemeter_ch_config = ConfigOption(
        name='channels',
        default={
            'default_channel': {'channel': 1, 'unit': 'nm', 'medium': 'vac', 'exposure': 10}
        },
        missing='info'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()
        self._callback_function = None
        self._wavemeterdll = None

        self._data_from_callback = None

        # Internal settings
        self._channel_names = None
        self._channel_buffer_size = -1
        self._active_channels = None
        self._unit_return_type = {}

        # Data buffer
        self._data_buffer = None
        self._timestamp_buffer = None
        self._current_buffer_positions = None

        # Stored hardware constraints
        self._constraints = None

    def on_activate(self):
        try:
            # load wavemeter DLL
            self._wavemeterdll = ctypes.windll.LoadLibrary('wlmData.dll')
        except FileNotFoundError:
            self.log.error('There is no wavemeter installed on this computer.\n'
                           'Please install a High Finesse wavemeter and try again.')
            return

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

        # configure wavemeter channels
        self._channel_names = {}
        channel_units = {}
        for ch_name, info in self._wavemeter_ch_config.items():
            ch = info['channel']
            unit = info['unit']
            medium = info['medium']
            self._channel_names[ch] = ch_name

            if unit == 'THz' or unit == 'Hz':
                channel_units[ch_name] = unit
                self._unit_return_type[ch] = high_finesse_constants.cReturnFrequency
            elif unit == 'nm' or unit == 'm':
                channel_units[ch_name] = unit
                if medium == 'vac':
                    self._unit_return_type[ch] = high_finesse_constants.cReturnWavelengthVac
                elif medium == 'air':
                    self._unit_return_type[ch] = high_finesse_constants.cReturnWavelengthAir
                else:
                    self.log.error(f'Invalid medium: {medium}. Valid media are vac and air.')
                    self._unit_return_type[ch] = high_finesse_constants.cReturnWavelengthVac
            else:
                self.log.error(f'Invalid unit: {unit}. Valid units are THz and nm.')
                channel_units[ch_name] = None

            exp_time = info.get('exposure')
            if exp_time is not None:
                res = self._wavemeterdll.SetExposureNum(ch, 1, exp_time)
                if res != 0:
                    self.log.warning('Wavemeter error while setting exposure time.')

        self._active_channels = list(self._channel_names)

        # set up constraints
        self._constraints = DataInStreamConstraints(
            channel_units=channel_units,
            sample_timings=[SampleTiming.TIMESTAMP],
            streaming_modes=[StreamingMode.CONTINUOUS],
            data_type=np.float64,
            # TODO: figure out meaningful constraints
            channel_buffer_size=ScalarConstraint(default=1000, bounds=(100, 10000), increment=1, enforce_int=True)
        )

    def on_deactivate(self):  # TODO
        self.stop_stream()

        # free memory
        self._data_buffer = None
        self._timestamp_buffer = None

        try:
            # clean up by removing reference to the ctypes library object
            del self._wavemeterdll
            return 0
        except:
            self.log.error('Could not unload the wlmData.dll of the '
                           'wavemeter.')

    @property
    def constraints(self):
        """
        Return the constraints on the settings for this data streamer.

        @return DataInStreamConstraints: Instance of DataInStreamConstraints containing constraints
        """
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
            if mode == high_finesse_constants.cmiOperation and intval == high_finesse_constants.cStop:
                self.log.error('Wavemeter acquisition was stopped during stream.')
                return 0

            with self._lock:
                # see if new data is from one of the active channels
                ch = high_finesse_constants.cmi_wavelength_n.get(mode)
                if ch in self._active_channels:
                    i = self._active_channels.index(ch)
                    # wavemeter records timestamps in ms
                    timestamp = np.datetime64(intval, 'ms')
                    # unit conversion
                    converted_value = self._wavemeterdll.ConvertUnit(
                        dblval, high_finesse_constants.cReturnWavelengthVac, self._unit_return_type[ch]
                    )

                    # insert the new data into the buffers
                    try:
                        self._timestamp_buffer[i, self._current_buffer_positions[i]] = timestamp
                        self._data_buffer[i, self._current_buffer_positions[i]] = converted_value
                    except IndexError:
                        raise OverflowError(
                            'Streaming buffer encountered an overflow while receiving a callback from the wavemeter. '
                            'Please increase the buffer size or speed up data reading.'
                        )
                    self._current_buffer_positions[i] += 1

                    # TODO emit signal for wavelength window
                    self.sigNewWavelength.emit(converted_value)
            return 0

        self._callback_function = _CALLBACK(handle_callback)
        return self._callback_function

    def start_stream(self):
        """ Start the data acquisition/streaming """
        with self._lock:
            if self.module_state() == 'idle':
                self.module_state.lock()
                self._data_from_callback = tuple([] for _ in self._wavemeter_ch_config.keys())

                # start callback procedure
                self._wavemeterdll.Instantiate(
                    high_finesse_constants.cInstNotification,  # long ReasonForCall
                    high_finesse_constants.cNotifyInstallCallbackEx,  # long Mode
                    ctypes.cast(self._get_callback_ex(), ctypes.POINTER(ctypes.c_long)),  # long P1: function
                    0)  # long P2: callback thread priority, 0 = standard

                self._init_buffers()
            else:
                self.log.warning('Unable to start input stream. It is already running.')

    def stop_stream(self):
        """ Stop the data acquisition/streaming """
        with self._thread_lock:
            if self.module_state() == 'locked':
                self._wavemeterdll.Instantiate(
                    high_finesse_constants.cInstNotification,  # long ReasonForCall
                    high_finesse_constants.cNotifyRemoveCallback,  # long mode
                    ctypes.cast(self._callback_function, ctypes.POINTER(ctypes.c_long)),
                    # long P1: function TODO unnecessary?
                    0)  # long P2: callback thread priority, 0 = standard
                self._callback_function = None
                self._data_from_callback = None

                self.module_state.unlock()
            else:
                self.log.warning('Unable to stop wavemeter input stream as nothing is running.')

    def read_data_into_buffer(self, data_buffer, timestamp_buffer=None, number_of_samples=None):
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            data_buffer.shape == (<channel_count>, <sample_count>)
        The data_buffer array must have the same data type as self.constraints.data_type.

        # TODO: 2D timestamp per channel!
        In case of SampleTiming.TIMESTAMP a 1D numpy.datetime64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        at least <number_of_samples> in size.

        If number_of_samples is omitted it will be derived from buffer.shape[1]
        """
        with self._lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

            if not isinstance(data_buffer, np.ndarray) or data_buffer.dtype != self.constraints.data_type:
                self.log.error(f'data_buffer must be numpy.ndarray with dtype {self.constraints.data_type}.')

            if not isinstance(timestamp_buffer, np.ndarray) or timestamp_buffer.dtype != np.datetime64:
                self.log.error(f'timestamp_buffer must be numpy.ndarray with dtype np.datetime64.')

            n_channels = len(self._active_channels)
            if n_channels > 1:
                if data_buffer.ndim != 2:
                    self.log.error('data_buffer must be a 2D numpy.ndarray if more then one channel is active.')

                if data_buffer.shape[0] != n_channels:
                    self.log.error(f'Configured number of channels ({n_channels}) does not match first '
                                   f'dimension of 2D data_buffer array ({data_buffer.shape[0]}).')

                if timestamp_buffer.ndim != 2:
                    self.log.error('timestamp_buffer must be a 2D numpy.ndarray if more then one channel is active.')

                if timestamp_buffer.shape[0] != n_channels:
                    self.log.error(f'Configured number of channels ({n_channels}) does not match first '
                                   f'dimension of 2D timestamp_buffer array ({timestamp_buffer.shape[0]}).')

            if number_of_samples is None:
                try:
                    number_of_samples = data_buffer.shape[1]
                except IndexError:
                    number_of_samples = data_buffer.shape[0]
            elif number_of_samples < 1:
                return

            # wait until requested number of samples is available
            while self.available_samples < number_of_samples:
                if self.module_state() != 'locked':
                    break
                # wait for 10 ms
                time.sleep(0.01)

            data_buffer[:, :number_of_samples] = self._data_buffer[:, :number_of_samples]
            if timestamp_buffer is not None:
                timestamp_buffer[:, :number_of_samples] = self._timestamp_buffer[:, :number_of_samples]

            # remove samples that have been read from buffer to make space for new samples
            self._data_buffer = np.roll(self._data_buffer, -number_of_samples, axis=1)
            self._timestamp_buffer = np.roll(self._timestamp_buffer, -number_of_samples, axis=1)
            remaining_samples = self.available_samples - number_of_samples
            self._current_buffer_positions = np.full(len(self._active_channels), remaining_samples, dtype=int)

    def read_available_data_into_buffer(self, data_buffer, timestamp_buffer=None):
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            data_buffer.shape == (<channel_count>, <sample_count>)
        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.datetime64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        at least <number_of_samples> in size.

        This method will read all currently available samples into buffer. If number of available
        samples exceed buffer size, read only as many samples as fit into the buffer.
        """
        available_samples = self.available_samples
        self.read_data_into_buffer(data_buffer=data_buffer, timestamp_buffer=timestamp_buffer,
                                   number_of_samples=available_samples)

    def read_data(self, number_of_samples=None):
        """
        Read data from the stream buffer into a 2D numpy array and return it.
        The arrays first index corresponds to the channel number and the second index serves as
        sample index:
            return_array.shape == (self.number_of_channels, number_of_samples)
        The numpy arrays data type is the one defined in self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.datetime64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.

        If number_of_samples is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        If no samples are available, this method will immediately return an empty array.
        """
        data_buffer, timestamp_buffer = np.empty((0, 0), dtype=self.data_type), np.empty((0, 0), dtype=np.datetime64)

        if self.module_state() != 'locked':
            self.log.error('Unable to read data. Device is not running.')

        elif number_of_samples is None:
            if self.available_samples > 0:
                data_buffer = np.zeros_like(self._data_buffer)
                timestamp_buffer = np.zeros_like(self._data_buffer)
                self.read_available_data_into_buffer(data_buffer, timestamp_buffer)
            else:
                return np.empty((0, 0), dtype=self.data_type), np.empty((0, 0), dtype=np.datetime64)
        else:
            data_buffer = np.zeros_like(self._data_buffer)[:, :number_of_samples]
            timestamp_buffer = np.zeros_like(self._data_buffer)[:, :number_of_samples]
            self.read_data_into_buffer(data_buffer, timestamp_buffer, number_of_samples=number_of_samples)

        return data_buffer, timestamp_buffer

    def read_single_point(self):
        """
        Return the last read sample of each configured data channel.
        In general this sample may not be acquired simultaneously for all channels and timing in
        general cannot be assured. Use this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        The returned 1D numpy array will contain one sample for each channel.

        @return numpy.ndarray: 1D array containing one sample for each channel. Empty array
                               indicates error.
        """
        if self.module_state() != 'locked':
            self.log.error('Unable to read data. Device is not running.')
            return np.empty(0, dtype=self.constraints.data_type), None

        i = self._current_buffer_positions.min() - 1
        data = self._data_buffer[i]
        timestamp = self._timestamp_buffer[i]
        return data, timestamp

    @property
    def sample_rate(self):
        """ Read-only property returning the currently set sample rate in Hz.

        Not applicable for the wavemeter since SampleTiming.TIMESTAMP.
        """
        return None

    @property
    def streaming_mode(self):
        """
        Read-only property to return the currently configured streaming mode Enum.

        @return StreamingMode: Finite (StreamingMode.FINITE) or continuous
                               (StreamingMode.CONTINUOUS) data acquisition
        """
        return StreamingMode.CONTINUOUS

    @property
    def active_channels(self):
        """ Read-only property returning the currently configured active channel names """
        ch_names = [self._channel_names[ch] for ch in self._active_channels]
        return ch_names

    @property
    def available_samples(self):
        """
        Read-only property to return the currently available number of samples per channel ready
        to read from buffer.

        @return int: Number of available samples per channel
        """
        if self.module_state() != 'locked':
            return 0

        # all channels must have been read out in order to count as an available sample
        return min(self._current_buffer_positions)

    def configure(self, active_channels=None, streaming_mode=None, sample_timing=None,
                  channel_buffer_size=None, sample_rate=None):
        """
        Method to configure all possible settings of the data input stream.

        @param iterable active_channels: Iterable of channel names (str) to be read from.
        @param StreamingMode streaming_mode: ignored (always continuous)
        @param sample_timing: ignored (always timestamp)
        @param int channel_buffer_size: The size of the data buffer to pre-allocate in samples per channel
        @param float sample_rate: ignored (not applicable for wavemeter)
        """
        if self.module_state() == 'locked':
            raise RuntimeError('Unable to configure data stream while it is already running')

        if active_channels is not None:
            self._active_channels = []
            for ch in active_channels:
                if ch in self._wavemeter_ch_config:
                    self._active_channels.append(self._wavemeter_ch_config[ch]['channel'])
                else:
                    self.log.error(f'Channel {ch} is not set up in the config file. Available channels '
                                   f'are {list(self._wavemeter_ch_config)}.')

        if streaming_mode is not None and streaming_mode != StreamingMode.CONTINUOUS:
            self.log.warning('Only continuous streaming is supported, ignoring this setting.')

        if sample_timing is not None and sample_timing != SampleTiming.TIMESTAMP:
            self.log.warning('Only timestamp sample timing is supported, ignoring this setting.')

        if channel_buffer_size is not None:
            self.constraints.channel_buffer_size.check(channel_buffer_size)
            self._channel_buffer_size = channel_buffer_size

        if sample_rate is not None:
            self.log.warning('Sample rate is not applicable for a wavemeter and is ignored.')

    def _init_buffers(self):
        self._data_buffer = np.zeros([len(self._active_channels), self._channel_buffer_size],
                                     dtype=self.constraints.data_type)
        self._timestamp_buffer = np.zeros([len(self._active_channels), self._channel_buffer_size],
                                          dtype=np.datetime64)
        self._current_buffer_positions = np.zeros(len(self._active_channels), dtype=int)
