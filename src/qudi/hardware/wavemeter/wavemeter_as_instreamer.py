# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module for the HighFinesse wavemeter. It implements the
DataInStreamInterface. Communication with the hardware is done via callback functions such that no new data is missed.
Measurement timestamps provided by the hardware are used for sample timing. As an approximation, the timestamps
corresponding to the first active channel are equally used for all channels. Considering the fixed round-trip time
composing the individual channel exposure times and switching times, this implementation should satisfy all
synchronization needs to an extent that is possible to implement on this software level.

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
from ctypes import cast, c_double, c_int, c_long, POINTER, windll, WINFUNCTYPE
from typing import Union, Optional, List, Tuple, Sequence

import numpy as np
from PySide2 import QtCore

from qudi.core.configoption import ConfigOption
import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants
from qudi.util.mutex import Mutex
from qudi.util.constraints import ScalarConstraint
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, StreamingMode, \
    SampleTiming

_CALLBACK = WINFUNCTYPE(c_int, c_long, c_long, c_long, c_double, c_long)


class WavemeterAsInstreamer(DataInStreamInterface):
    """
    HighFinesse wavelength meter as an in-streaming device.

    Example config for copy-paste:

    wavemeter:
        module.Class: 'wavemeter.wavemeter_as_instreamer.WavemeterAsInstreamer'
        options:
            channels:
                red_laser_1:
                    switch_ch: 1    # channel on the wavemeter switch
                    unit: 'nm'    # wavelength (nm) or frequency (THz)
                    medium: 'vac' # for wavelength: air or vac
                    exposure: 10  # exposure time in ms
                red_laser_2:
                    switch_ch: 2
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
                green_laser:
                    switch_ch: 3
                    unit: 'nm'
                    medium: 'vac'
                    exposure: 10
                yellow_laser:
                    switch_ch: 4
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
            'default_channel': {'switch_ch': 1, 'unit': 'nm', 'medium': 'vac', 'exposure': 10}
        },
        missing='info'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()
        self._callback_function = None
        self._wavemeterdll = None

        # Internal settings
        self._channel_names = None  # dictionary with switch channel numbers as keys, channel names as values
        self._channel_buffer_size = 1000
        self._active_switch_channels = None  # list of active switch channel numbers
        self._unit_return_type = {}

        # Data buffer
        self._data_buffer = None
        self._timestamp_buffer = None
        self._current_buffer_positions = None

        # Stored hardware constraints
        self._constraints = None

    def on_activate(self) -> None:
        try:
            # load wavemeter DLL
            self._wavemeterdll = windll.LoadLibrary('wlmData.dll')
        except FileNotFoundError:
            self.log.error('There is no wavemeter installed on this computer.\n'
                           'Please install a High Finesse wavemeter and try again.')
            return

        # define function header for a later call
        self._wavemeterdll.Instantiate.argtypes = [c_long, c_long, POINTER(c_long), c_long]
        self._wavemeterdll.Instantiate.restype = POINTER(c_long)
        self._wavemeterdll.ConvertUnit.restype = c_double
        self._wavemeterdll.ConvertUnit.argtypes = [c_double, c_long, c_long]
        self._wavemeterdll.SetExposureNum.restype = c_long
        self._wavemeterdll.SetExposureNum.argtypes = [c_long, c_long, c_long]
        self._wavemeterdll.GetExposureNum.restype = c_long
        self._wavemeterdll.GetExposureNum.argtypes = [c_long, c_long, c_long]

        # configure wavemeter channels
        self._channel_names = {}
        channel_units = {}
        for ch_name, info in self._wavemeter_ch_config.items():
            ch = info['switch_ch']
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

        self._active_switch_channels = list(self._channel_names)

        # set up constraints
        self._constraints = DataInStreamConstraints(
            channel_units=channel_units,
            sample_timing=SampleTiming.TIMESTAMP,
            # TODO: implement fixed streaming mode
            streaming_modes=[StreamingMode.CONTINUOUS],
            data_type=np.float64,
            # TODO: figure out meaningful constraints
            channel_buffer_size=ScalarConstraint(default=1000, bounds=(100, 1024**2), increment=1, enforce_int=True)
        )

    def on_deactivate(self) -> None:
        self.stop_stream()

        # free memory
        self._data_buffer = None
        self._timestamp_buffer = None

        # clean up by removing reference to the ctypes library object
        del self._wavemeterdll

    @property
    def constraints(self) -> DataInStreamConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
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

            # TODO: why does this not work in the lock anymore?
            # with self._lock:
            if True:
                # see if new data is from one of the active channels
                ch = high_finesse_constants.cmi_wavelength_n.get(mode)
                if ch in self._active_switch_channels:
                    i = self._active_switch_channels.index(ch)

                    if self._current_buffer_positions[i] >= self.channel_buffer_size:
                        # TODO: what to do after raising this error for the first time?
                        raise OverflowError(
                            'Streaming buffer encountered an overflow while receiving a callback from the wavemeter. '
                            'Please increase the buffer size or speed up data reading.'
                        )

                    # wavemeter records timestamps in ms
                    timestamp = np.datetime64(intval, 'ms')
                    # unit conversion
                    converted_value = self._wavemeterdll.ConvertUnit(
                        dblval, high_finesse_constants.cReturnWavelengthVac, self._unit_return_type[ch]
                    )

                    # insert the new data into the buffers
                    self._data_buffer[i, self._current_buffer_positions[i]] = converted_value
                    if i == 0:
                        # only record the timestamp of the first active channel
                        self._timestamp_buffer[self._current_buffer_positions[0]] = timestamp
                    self._current_buffer_positions[i] += 1

                    # TODO emit signal for wavelength window
                    self.sigNewWavelength.emit(converted_value)
            return 0

        self._callback_function = _CALLBACK(handle_callback)
        return self._callback_function

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        with self._lock:
            if self.module_state() == 'idle':
                self.module_state.lock()

                # start callback procedure
                self._wavemeterdll.Instantiate(
                    high_finesse_constants.cInstNotification,  # long ReasonForCall
                    high_finesse_constants.cNotifyInstallCallbackEx,  # long Mode
                    cast(self._get_callback_ex(), POINTER(c_long)),  # long P1: function
                    0)  # long P2: callback thread priority, 0 = standard

                self._init_buffers()
            else:
                self.log.warning('Unable to start input stream. It is already running.')

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        with self._lock:
            if self.module_state() == 'locked':
                self._wavemeterdll.Instantiate(
                    high_finesse_constants.cInstNotification,  # long ReasonForCall
                    high_finesse_constants.cNotifyRemoveCallback,  # long mode
                    cast(self._callback_function, POINTER(c_long)),
                    # long P1: function
                    0)  # long P2: callback thread priority, 0 = standard
                self._callback_function = None

                self.module_state.unlock()
            else:
                self.log.warning('Unable to stop wavemeter input stream as nothing is running.')

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              number_of_samples: Optional[int] = None,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
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

        If number_of_samples is omitted it will be derived from buffer.shape[1]
        """
        with self._lock:
            if self.module_state() != 'locked':
                raise RuntimeError('Unable to read data. Stream is not running.')

            if not isinstance(data_buffer, np.ndarray) or data_buffer.dtype != self.constraints.data_type:
                self.log.error(f'data_buffer must be numpy.ndarray with dtype {self.constraints.data_type}.')

            if not isinstance(timestamp_buffer, np.ndarray) or not np.issubdtype(timestamp_buffer.dtype, np.datetime64):
                # TODO: explicit datetime64 unit checking
                self.log.error(f'timestamp_buffer must be numpy.ndarray with dtype np.datetime64.')

            n_channels = len(self.active_channels)
            if n_channels > 1:
                if data_buffer.ndim != 2:
                    self.log.error('data_buffer must be a 2D numpy.ndarray if more then one channel is active.')

                if data_buffer.shape[0] != n_channels:
                    self.log.error(f'Configured number of channels ({n_channels}) does not match first '
                                   f'dimension of 2D data_buffer array ({data_buffer.shape[0]}).')

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
                timestamp_buffer[:number_of_samples] = self._timestamp_buffer[:number_of_samples]

            # remove samples that have been read from buffer to make space for new samples
            self._data_buffer = np.roll(self._data_buffer, -number_of_samples, axis=1)
            self._timestamp_buffer = np.roll(self._timestamp_buffer, -number_of_samples)
            self._current_buffer_positions -= number_of_samples

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
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
        self.read_data_into_buffer(data_buffer=data_buffer,
                                   number_of_samples=available_samples,
                                   timestamp_buffer=timestamp_buffer)
        return available_samples

    def read_data(self,
                  number_of_samples: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
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
        data_buffer = np.empty((0, 0), dtype=self.constraints.data_type)
        timestamp_buffer = np.empty(0, dtype=np.datetime64)

        if self.module_state() != 'locked':
            self.log.error('Unable to read data. Device is not running.')

        elif number_of_samples is None:
            if self.available_samples > 0:
                data_buffer = np.zeros_like(self._data_buffer)
                timestamp_buffer = np.zeros_like(self._timestamp_buffer)
                self.read_available_data_into_buffer(data_buffer, timestamp_buffer)

        else:
            data_buffer = np.zeros_like(self._data_buffer)[:, :number_of_samples]
            timestamp_buffer = np.zeros_like(self._timestamp_buffer)[:number_of_samples]
            self.read_data_into_buffer(data_buffer, number_of_samples, timestamp_buffer)

        return data_buffer, timestamp_buffer

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """ This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        In case of SampleTiming.TIMESTAMP a single numpy.float64 timestamp value will be returned
        as well.
        """
        if self.module_state() != 'locked':
            self.log.error('Unable to read data. Device is not running.')
            return np.empty(0, dtype=self.constraints.data_type), None

        i = self._current_buffer_positions.min() - 1
        data = self._data_buffer[:, i]
        timestamp = self._timestamp_buffer[i]
        return data, timestamp

    @property
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.

        For the wavemeter, it is estimated by the exposure times per channel and switching times if
        more than one channel is active.
        """
        exposure_times = []
        for ch in self._active_switch_channels:
            t = self._wavemeterdll.GetExposureNum(ch, 1, 0)
            exposure_times.append(t)
        total_exposure_time = sum(exposure_times)

        switching_time = 12
        n_channels = len(self._active_switch_channels)
        if n_channels > 1:
            turnaround_time_ms = total_exposure_time + n_channels * switching_time
        else:
            turnaround_time_ms = total_exposure_time

        sample_rate = 1e3 / turnaround_time_ms
        return sample_rate

    @property
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        return StreamingMode.CONTINUOUS

    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        ch_names = [self._channel_names[ch] for ch in self._active_switch_channels]
        return ch_names

    @property
    def available_samples(self) -> int:
        """
        Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        """
        if self.module_state() != 'locked':
            return 0

        # all channels must have been read out in order to count as an available sample
        return min(self._current_buffer_positions)

    @property
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self._channel_buffer_size

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        if self.module_state() == 'locked':
            raise RuntimeError('Unable to configure data stream while it is already running')

        if active_channels is not None:
            self._active_switch_channels = []
            for ch in active_channels:
                if ch in self._wavemeter_ch_config:
                    self._active_switch_channels.append(self._wavemeter_ch_config[ch]['switch_ch'])
                else:
                    self.log.error(f'Channel {ch} is not set up in the config file. Available channels '
                                   f'are {list(self._channel_names.keys())}.')

        if streaming_mode is not None and streaming_mode != StreamingMode.CONTINUOUS:
            self.log.warning('Only continuous streaming is supported, ignoring this setting.')

        if channel_buffer_size is not None:
            self.constraints.channel_buffer_size.is_valid(channel_buffer_size)
            self._channel_buffer_size = channel_buffer_size

    def _init_buffers(self) -> None:
        """ Initialize buffers and the current buffer position marker. """
        n = len(self._active_switch_channels)
        self._data_buffer = np.zeros([n, self._channel_buffer_size], dtype=self.constraints.data_type)
        self._timestamp_buffer = np.zeros(self._channel_buffer_size, dtype='datetime64[ms]')
        self._current_buffer_positions = np.zeros(n, dtype=int)
