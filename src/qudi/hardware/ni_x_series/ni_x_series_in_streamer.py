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

import ctypes
import numpy as np
import nidaqmx as ni
from functools import wraps
from typing import Tuple, List, Optional, Sequence, Union
from nidaqmx._lib import lib_importer  # Due to NIDAQmx C-API bug needed to bypass property getter
from nidaqmx.stream_readers import CounterReader
from nidaqmx.stream_readers import AnalogMultiChannelReader as _AnalogMultiChannelReader
from nidaqmx.constants import FillMode, READ_ALL_AVAILABLE
try:
    from nidaqmx._task_modules.read_functions import _read_analog_f_64
except ImportError:
    pass

from qudi.core.configoption import ConfigOption
from qudi.util.helpers import natural_sort
from qudi.util.constraints import ScalarConstraint
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming


class AnalogMultiChannelReader(_AnalogMultiChannelReader):
    __doc__ = _AnalogMultiChannelReader.__doc__

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @wraps(_AnalogMultiChannelReader.read_many_sample)
    def read_many_sample(self,
                         data,
                         number_of_samples_per_channel=READ_ALL_AVAILABLE,
                         timeout=10.0):
        number_of_samples_per_channel = (
            self._task._calculate_num_samps_per_chan(number_of_samples_per_channel)
        )

        self._verify_array(data, number_of_samples_per_channel, False, True)

        try:
            _, samps_per_chan_read = self._interpreter.read_analog_f64(
                self._handle,
                number_of_samples_per_channel,
                timeout,
                FillMode.GROUP_BY_SCAN_NUMBER.value,
                data
            )
        except AttributeError:
            samps_per_chan_read = _read_analog_f_64(
                self._handle,
                data,
                number_of_samples_per_channel,
                timeout,
                fill_mode=FillMode.GROUP_BY_SCAN_NUMBER
            )
        return samps_per_chan_read


class NIXSeriesInStreamer(DataInStreamInterface):
    """
    A National Instruments device that can detect and count digital pulses and measure analog
    voltages as data stream.

    !!!!!! NI USB 63XX, NI PCIe 63XX and NI PXIe 63XX DEVICES ONLY !!!!!!

    See [National Instruments X Series Documentation](@ref nidaq-x-series) for details.

    Example config for copy-paste:

    nicard_6343_instreamer:
        module.Class: 'ni_x_series.ni_x_series_in_streamer.NIXSeriesInStreamer'
        options:
            device_name: 'Dev1'
            digital_sources:  # optional
                - 'PFI15'
            analog_sources:  # optional
                - 'ai0'
                - 'ai1'
            # external_sample_clock_source: 'PFI0'  # optional
            # external_sample_clock_frequency: 1000  # optional
            adc_voltage_range: [-10, 10]  # optional
            max_channel_samples_buffer: 10000000  # optional
            read_write_timeout: 10  # optional

    """

    # config options
    _device_name = ConfigOption(name='device_name', default='Dev1', missing='warn')
    _digital_sources = ConfigOption(name='digital_sources', default=tuple(), missing='info')
    _analog_sources = ConfigOption(name='analog_sources', default=tuple(), missing='info')
    _external_sample_clock_source = ConfigOption(name='external_sample_clock_source',
                                                 default=None,
                                                 missing='nothing')
    _external_sample_clock_frequency = ConfigOption(
        name='external_sample_clock_frequency',
        default=None,
        missing='nothing',
        constructor=lambda x: x if x is None else float(x)
    )
    _adc_voltage_range = ConfigOption('adc_voltage_range', default=(-10, 10), missing='info')
    _max_channel_samples_buffer = ConfigOption(name='max_channel_samples_buffer',
                                               default=1024**2,
                                               missing='info',
                                               constructor=lambda x: max(int(round(x)), 1024**2))
    _rw_timeout = ConfigOption('read_write_timeout', default=10, missing='nothing')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # NIDAQmx device handle
        self._device_handle = None
        # Task handles for NIDAQmx tasks
        self._di_task_handles = list()
        self._ai_task_handle = None
        self._clk_task_handle = None
        # nidaqmx stream reader instances to help with data acquisition
        self._di_readers = list()
        self._ai_reader = None
        # Internal settings
        self.__sample_rate = -1.0
        self.__buffer_size = -1
        self.__streaming_mode = None
        # List of all available counters and terminals for this device
        self.__all_counters = tuple()
        self.__all_digital_terminals = tuple()
        self.__all_analog_terminals = tuple()
        # currently active channels
        self.__active_channels = tuple()
        # Stored hardware constraints
        self._constraints = None
        self.__tmp_buffer = None

    def on_activate(self):
        """
        Starts up the NI-card and performs sanity checks.
        """
        # Check if device is connected and set device to use
        dev_names = ni.system.System().devices.device_names
        if self._device_name.lower() not in set(dev.lower() for dev in dev_names):
            raise ValueError(
                f'Device name "{self._device_name}" not found in list of connected devices: '
                f'{dev_names}\nActivation of NIXSeriesInStreamer failed!'
            )
        for dev in dev_names:
            if dev.lower() == self._device_name.lower():
                self._device_name = dev
                break
        self._device_handle = ni.system.Device(self._device_name)

        self.__all_counters = tuple(
            ctr.split('/')[-1] for ctr in self._device_handle.co_physical_chans.channel_names if
            'ctr' in ctr.lower()
        )
        self.__all_digital_terminals = tuple(
            term.rsplit('/', 1)[-1].lower() for term in self._device_handle.terminals if
            'PFI' in term
        )
        self.__all_analog_terminals = tuple(
            term.rsplit('/', 1)[-1].lower() for term in
            self._device_handle.ai_physical_chans.channel_names
        )

        # Check digital input terminals
        if self._digital_sources:
            source_set = set(self._extract_terminal(src) for src in self._digital_sources)
            invalid_sources = source_set.difference(set(self.__all_digital_terminals))
            if invalid_sources:
                self.log.error(
                    'Invalid digital source terminals encountered. Following sources will '
                    'be ignored:\n  {0}\nValid digital input terminals are:\n  {1}'
                    ''.format(', '.join(natural_sort(invalid_sources)),
                              ', '.join(self.__all_digital_terminals)))
            self._digital_sources = natural_sort(source_set.difference(invalid_sources))

        # Check analog input channels
        if self._analog_sources:
            source_set = set(self._extract_terminal(src) for src in self._analog_sources)
            invalid_sources = source_set.difference(set(self.__all_analog_terminals))
            if invalid_sources:
                self.log.error('Invalid analog source channels encountered. Following sources will '
                               'be ignored:\n  {0}\nValid analog input channels are:\n  {1}'
                               ''.format(', '.join(natural_sort(invalid_sources)),
                                         ', '.join(self.__all_analog_terminals)))
            self._analog_sources = natural_sort(source_set.difference(invalid_sources))

        # Check if all input channels fit in the device
        if len(self._digital_sources) > 3:
            raise ValueError(
                'Too many digital channels specified. Maximum number of digital channels is 3.'
            )
        if len(self._analog_sources) > 16:
            raise ValueError(
                'Too many analog channels specified. Maximum number of analog channels is 16.'
            )

        # Check if there are any valid input channels left
        if not self._analog_sources and not self._digital_sources:
            raise ValueError(
                'No valid analog or digital sources defined in config. Activation of '
                'NIXSeriesInStreamer failed!'
            )

        # Create constraints
        channel_units = {chnl: 'counts/s' for chnl in self._digital_sources}
        channel_units.update({chnl: 'V' for chnl in self._analog_sources})
        self._constraints = DataInStreamConstraints(
            channel_units=channel_units,
            sample_timing=SampleTiming.CONSTANT,
            streaming_modes=[StreamingMode.CONTINUOUS], # TODO: Implement FINITE streaming mode
            data_type=np.float64,
            channel_buffer_size=ScalarConstraint(default=1024**2,
                                                 bounds=(2, self._max_channel_samples_buffer),
                                                 increment=1,
                                                 enforce_int=True),
            # FIXME: What is the minimum frequency for the digital counter timebase?
            sample_rate=ScalarConstraint(default=50.0,
                                         bounds=(self._device_handle.ai_min_rate,
                                                 self._device_handle.ai_max_multi_chan_rate),
                                         increment=1,
                                         enforce_int=False)
        )

        # Check external sample clock source
        if self._external_sample_clock_source is not None:
            new_name = self._extract_terminal(self._external_sample_clock_source)
            if new_name in self.__all_digital_terminals:
                self._external_sample_clock_source = new_name
            else:
                self.log.error(
                    f'No valid source terminal found for external_sample_clock_source '
                    f'"{self._external_sample_clock_source}". Falling back to internal sampling '
                    f'clock.'
                )
                self._external_sample_clock_source = None

        # Check external sample clock frequency
        if self._external_sample_clock_source is None:
            self._external_sample_clock_frequency = None
        elif self._external_sample_clock_frequency is None:
            self.log.error('External sample clock source supplied but no clock frequency. '
                           'Falling back to internal clock instead.')
            self._external_sample_clock_source = None
        elif not self._constraints.sample_rate.is_valid(self._external_sample_clock_frequency):
            self.log.error(
                f'External sample clock frequency requested '
                f'({self._external_sample_clock_frequency:.3e}Hz) is out of bounds. Please '
                f'choose a value between {self._constraints.sample_rate.minimum:.3e}Hz and '
                f'{self._constraints.sample_rate.maximum:.3e}Hz. Value will be clipped to the '
                f'closest boundary.'
            )
            self._external_sample_clock_frequency = self._constraints.sample_rate.clip(
                self._external_sample_clock_frequency
            )

        self._terminate_all_tasks()
        if self._external_sample_clock_frequency is None:
            sample_rate = self._constraints.sample_rate.default
        else:
            sample_rate = self._external_sample_clock_frequency
        self.configure(active_channels=self._constraints.channel_units,
                       streaming_mode=StreamingMode.CONTINUOUS,
                       channel_buffer_size=self._constraints.channel_buffer_size.default,
                       sample_rate=sample_rate)

    def on_deactivate(self):
        """ Shut down the NI card. """
        self._terminate_all_tasks()

    @property
    def constraints(self) -> DataInStreamConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
        return self._constraints

    @property
    def sample_rate(self):
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.
        """
        return self.__sample_rate

    @property
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        return self.__buffer_size

    @property
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        return self.__streaming_mode

    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        return list(self.__active_channels)

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        if self.module_state() == 'locked':
            raise RuntimeError('Unable to configure data stream while it is already running')
        streaming_mode = StreamingMode(streaming_mode)
        channel_buffer_size = int(round(channel_buffer_size))
        if any(ch not in self._constraints.channel_units for ch in active_channels):
            raise ValueError(
                f'Invalid channel to stream from encountered {tuple(active_channels)}. \n'
                f'Valid channels are: {tuple(self._constraints.channel_units)}'
            )
        if streaming_mode not in self._constraints.streaming_modes or streaming_mode == StreamingMode.INVALID:
            raise ValueError(f'Invalid streaming mode "{streaming_mode}" encountered.\n'
                             f'Valid modes are: {self._constraints.streaming_modes}.')
        self._constraints.channel_buffer_size.check(channel_buffer_size)
        self._constraints.sample_rate.check(sample_rate)

        self.__active_channels = tuple(active_channels)
        self.__streaming_mode = streaming_mode
        self.__buffer_size = channel_buffer_size
        self.__sample_rate = sample_rate
        digital_count = len([ch for ch in self.__active_channels if ch in self._digital_sources])
        analog_count = len(self.__active_channels) - digital_count
        self.__tmp_buffer = np.empty(
            self.__buffer_size * max(analog_count, int(digital_count > 0)),
            dtype=self._constraints.data_type
        )

    @property
    def available_samples(self):
        """ Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        """
        if self.module_state() == 'locked':
            if self._ai_task_handle is None:
                return self._di_task_handles[0].in_stream.avail_samp_per_chan
            else:
                return self._ai_task_handle.in_stream.avail_samp_per_chan
        else:
            return 0

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        if self.module_state() == 'locked':
            self.log.warning('Unable to start input stream. It is already running.')
        else:
            self.module_state.lock()
            try:
                self._init_sample_clock()
                self._init_digital_tasks()
                self._init_analog_task()

                self._clk_task_handle.start()
                if self._ai_task_handle is not None:
                    self._ai_task_handle.start()
                for task in self._di_task_handles:
                    task.start()
            except:
                self.module_state.unlock()
                self._terminate_all_tasks()
                raise

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        try:
            self._terminate_all_tasks()
        finally:
            if self.module_state() == 'locked':
                self.module_state.unlock()

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int = None,
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
        if self.module_state() != 'locked':
            raise RuntimeError('Unable to read data. Device is not running.')
        # Check for buffer overflow
        if self.available_samples > self.__buffer_size:
            raise OverflowError('Hardware channel buffer has overflown. Please increase readout '
                                'speed or decrease sample rate.')
        if not isinstance(data_buffer, np.ndarray) or data_buffer.dtype != self._constraints.data_type:
            raise TypeError(
                f'data_buffer must be numpy.ndarray with dtype {self._constraints.data_type}'
            )

        channel_count = len(self.__active_channels)
        digital_count = len(self._di_readers)
        analog_count = channel_count - digital_count
        if samples_per_channel is None:
            samples_per_channel = len(data_buffer) // channel_count
        total_samples = channel_count * samples_per_channel
        if samples_per_channel > 0:
            try:
                channel_offset = 0
                # Read digital channels
                for i, reader in enumerate(self._di_readers):
                    # read the counter value. This function is blocking.
                    read_samples = reader.read_many_sample_double(
                        self.__tmp_buffer,
                        number_of_samples_per_channel=samples_per_channel,
                        timeout=self._rw_timeout
                    )
                    self.__tmp_buffer[:samples_per_channel] *= self.__sample_rate
                    data_buffer[channel_offset:total_samples:channel_count] = self.__tmp_buffer[
                        :samples_per_channel]
                    channel_offset += 1
                # Read analog channels
                if self._ai_reader is not None:
                    if channel_offset == 0:
                        read_samples = self._ai_reader.read_many_sample(
                            data_buffer,
                            number_of_samples_per_channel=samples_per_channel,
                            timeout=self._rw_timeout
                        )
                    else:
                        tmp_view = self.__tmp_buffer[:analog_count * samples_per_channel]
                        read_samples = self._ai_reader.read_many_sample(
                            tmp_view,
                            number_of_samples_per_channel=samples_per_channel,
                            timeout=self._rw_timeout
                        )
                        buf_view = data_buffer[:total_samples].reshape(
                            [samples_per_channel, channel_count]
                        )
                        buf_view[:, digital_count:] = tmp_view.reshape(
                            [samples_per_channel, tmp_view.size // samples_per_channel]
                        )
            except:
                self.log.exception('Getting samples from streamer failed. Stopping streamer.')
                self.stop_stream()

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
        channel_count = len(self.__active_channels)
        samples_per_channel = min(self.available_samples, data_buffer.size // channel_count)
        self.read_data_into_buffer(data_buffer=data_buffer,
                                   samples_per_channel=samples_per_channel,
                                   timestamp_buffer=timestamp_buffer)
        return samples_per_channel

    def read_data(self,
                  samples_per_channel: Optional[int] = None
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
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        channel_count = len(self.__active_channels)
        data_buffer = np.empty(samples_per_channel * channel_count,
                               dtype=self._constraints.data_type)
        self.read_data_into_buffer(data_buffer=data_buffer, samples_per_channel=samples_per_channel)
        return data_buffer, None

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
            raise RuntimeError('Unable to read data. Device is not running.')

        data_buffer = np.empty(len(self.__active_channels), dtype=self._constraints.data_type)
        try:
            offset = 0
            # Read digital channels
            for reader in self._di_readers:
                # read the counter value. This function is blocking. Scale with sample rate.
                data_buffer[offset] = self.__sample_rate * reader.read_one_sample_double(
                    timeout=self._rw_timeout
                )
                offset += 1
            # Read analog channels
            if self._ai_reader is not None:
                self._ai_reader.read_one_sample(data_buffer[offset:], timeout=self._rw_timeout)
        except:
            self.log.exception('Getting samples from data stream failed. Stopping streamer.')
            self.stop_stream()
        return data_buffer, None

    # =============================================================================================
    def _init_sample_clock(self):
        """ If no external clock is given, configures a counter to provide the sample clock for all
        channels.
        """
        # Return if sample clock is externally supplied
        if self._external_sample_clock_source is None:
            if self._clk_task_handle is not None:
                raise RuntimeError(
                    'Sample clock task is already running. Unable to set up a new clock before you '
                    'close the previous one.'
                )

            # Try to find an available counter
            for src in self.__all_counters:
                # Check if task by that name already exists
                task_name = f'SampleClock_{id(self):d}'
                try:
                    task = ni.Task(task_name)
                except ni.DaqError as err:
                    raise RuntimeError(f'Could not create task with name "{task_name}"') from err

                # Try to configure the task
                try:
                    task.co_channels.add_co_pulse_chan_freq(f'/{self._device_name}/{src}',
                                                            freq=self.__sample_rate,
                                                            idle_state=ni.constants.Level.LOW)
                    task.timing.cfg_implicit_timing(
                        sample_mode=ni.constants.AcquisitionType.CONTINUOUS
                    )
                except ni.DaqError as err:
                    try:
                        del task
                    except NameError:
                        pass
                    raise RuntimeError('Error while configuring sample clock task') from err

                # Try to reserve resources for the task
                try:
                    task.control(ni.constants.TaskMode.TASK_RESERVE)
                except ni.DaqError as err:
                    # Try to clean up task handle
                    try:
                        task.close()
                    except ni.DaqError:
                        pass
                    try:
                        del task
                    except NameError:
                        pass

                    # Return if no counter could be reserved
                    if src == self.__all_counters[-1]:
                        raise RuntimeError('Error while setting up clock. Probably because no free '
                                           'counter resource could be reserved.') from err
                else:
                    self._clk_task_handle = task
                    break

    def _init_digital_tasks(self):
        """ Set up tasks for digital event counting. """
        all_channels = list(self._constraints.channel_units)
        digital_channels = [ch for ch in all_channels[:len(self._digital_sources)] if
                            ch in self.__active_channels]
        if digital_channels:
            if self._di_task_handles:
                raise RuntimeError(
                    'Digital counting tasks have already been generated. Setting up counter tasks '
                    'has failed.'
                )
            if self._clk_task_handle is None and self._external_sample_clock_source is None:
                raise RuntimeError(
                    'No sample clock task has been generated and no external clock source '
                    'specified. Unable to create digital counting tasks.'
                )

            if self._external_sample_clock_source:
                clock_channel = f'/{self._device_name}/{self._external_sample_clock_source}'
                sample_freq = float(self._external_sample_clock_frequency)
            else:
                clock_channel = f'/{self._clk_task_handle.channel_names[0]}InternalOutput'
                sample_freq = float(self._clk_task_handle.co_channels.all.co_pulse_freq)

            # Set up digital counting tasks
            for i, chnl in enumerate(digital_channels):
                chnl_name = f'/{self._device_name}/{chnl}'
                task_name = f'PeriodCounter_{self._device_name}_{chnl}'
                # Try to find available counter
                for ctr in self.__all_counters:
                    ctr_name = f'/{self._device_name}/{ctr}'
                    try:
                        task = ni.Task(task_name)
                    except ni.DaqError as err:
                        raise RuntimeError(
                            f'Could not create task with name "{task_name}"'
                        ) from err

                    try:
                        task.ci_channels.add_ci_period_chan(
                            ctr_name,
                            min_val=0,
                            max_val=100000000,
                            units=ni.constants.TimeUnits.TICKS,
                            edge=ni.constants.Edge.RISING
                        )
                        # NOTE: The following two direct calls to C-function wrappers are a
                        # workaround due to a bug in some NIDAQmx.lib property getters. If one of
                        # these getters is called, it will mess up the task timing.
                        # This behaviour has been confirmed using pure C code.
                        # nidaqmx will call these getters and so the C function is called directly.
                        try:
                            lib_importer.windll.DAQmxSetCIPeriodTerm(
                                task._handle,
                                ctypes.c_char_p(ctr_name.encode('ascii')),
                                ctypes.c_char_p(clock_channel.encode('ascii')))
                            lib_importer.windll.DAQmxSetCICtrTimebaseSrc(
                                task._handle,
                                ctypes.c_char_p(ctr_name.encode('ascii')),
                                ctypes.c_char_p(chnl_name.encode('ascii')))
                        except:
                            lib_importer.cdll.DAQmxSetCIPeriodTerm(
                                task._handle,
                                ctypes.c_char_p(ctr_name.encode('ascii')),
                                ctypes.c_char_p(clock_channel.encode('ascii')))
                            lib_importer.cdll.DAQmxSetCICtrTimebaseSrc(
                                task._handle,
                                ctypes.c_char_p(ctr_name.encode('ascii')),
                                ctypes.c_char_p(chnl_name.encode('ascii')))

                        task.timing.cfg_implicit_timing(
                            sample_mode=ni.constants.AcquisitionType.CONTINUOUS,
                            samps_per_chan=self.__buffer_size
                        )
                    except ni.DaqError as err:
                        try:
                            del task
                        except NameError:
                            pass
                        raise RuntimeError(
                            f'Something went wrong while configuring digital counter task for '
                            f'channel "{chnl}"'
                        ) from err

                    try:
                        task.control(ni.constants.TaskMode.TASK_RESERVE)
                    except ni.DaqError as err:
                        try:
                            task.close()
                        except ni.DaqError:
                            pass
                        try:
                            del task
                        except NameError:
                            pass

                        if ctr == self.__all_counters[-1]:
                            raise RuntimeError(
                                f'Unable to reserve resources for digital counting task of channel '
                                f'"{chnl}". No available counter found!'
                            ) from err
                    else:
                        try:
                            self._di_readers.append(CounterReader(task.in_stream))
                            self._di_readers[-1].verify_array_shape = False
                        except ni.DaqError as err:
                            try:
                                task.close()
                            except ni.DaqError:
                                pass
                            try:
                                del task
                            except NameError:
                                pass
                            raise RuntimeError(
                                f'Something went wrong while setting up the digital counter reader '
                                f'for channel "{chnl}".'
                            ) from err

                        self._di_task_handles.append(task)
                        break

    def _init_analog_task(self):
        """ Set up task for analog voltage measurement. """
        all_channels = list(self._constraints.channel_units)
        analog_channels = [ch for ch in all_channels[len(self._digital_sources):] if
                           ch in self.__active_channels]
        if analog_channels:
            if self._ai_task_handle:
                raise RuntimeError('Analog input task has already been generated')
            if self._clk_task_handle is None and self._external_sample_clock_source is None:
                raise RuntimeError(
                    'No sample clock task has been generated and no external clock source '
                    'specified. Unable to create analog sample tasks.'
                )

            if self._external_sample_clock_source:
                clock_channel = f'/{self._device_name}/{self._external_sample_clock_source}'
                sample_freq = float(self._external_sample_clock_frequency)
            else:
                clock_channel = f'/{self._clk_task_handle.channel_names[0]}InternalOutput'
                sample_freq = float(self._clk_task_handle.co_channels.all.co_pulse_freq)

            # Set up analog input task
            task_name = f'AnalogIn_{id(self):d}'
            try:
                ai_task = ni.Task(task_name)
            except ni.DaqError as err:
                raise RuntimeError(
                    f'Unable to create analog-in task with name "{task_name}"'
                ) from err

            try:
                ai_ch_str = ','.join([f'/{self._device_name}/{ch}' for ch in analog_channels])
                ai_task.ai_channels.add_ai_voltage_chan(ai_ch_str,
                                                        max_val=max(self._adc_voltage_range),
                                                        min_val=min(self._adc_voltage_range))
                ai_task.timing.cfg_samp_clk_timing(
                    sample_freq,
                    source=clock_channel,
                    active_edge=ni.constants.Edge.RISING,
                    sample_mode=ni.constants.AcquisitionType.CONTINUOUS,
                    samps_per_chan=self.__buffer_size
                )
            except ni.DaqError as err:
                try:
                    del ai_task
                except NameError:
                    pass
                raise RuntimeError(
                    'Something went wrong while configuring the analog-in task'
                ) from err

            try:
                ai_task.control(ni.constants.TaskMode.TASK_RESERVE)
            except ni.DaqError as err:
                try:
                    ai_task.close()
                except ni.DaqError:
                    pass
                try:
                    del ai_task
                except NameError:
                    pass
                raise RuntimeError('Unable to reserve resources for analog-in task') from err

            try:
                self._ai_reader = AnalogMultiChannelReader(ai_task.in_stream)
                self._ai_reader.verify_array_shape = False
            except ni.DaqError as err:
                try:
                    ai_task.close()
                except ni.DaqError:
                    pass
                try:
                    del ai_task
                except NameError:
                    pass
                raise RuntimeError(
                    'Something went wrong while setting up the analog input reader'
                ) from err
            self._ai_task_handle = ai_task

    def _terminate_all_tasks(self):
        self._di_readers = list()
        self._ai_reader = None
        while len(self._di_task_handles) > 0:
            task = self._di_task_handles.pop(-1)
            try:
                if not task.is_task_done():
                    task.stop()
                task.close()
            except ni.DaqError:
                self.log.exception('Error while trying to terminate digital counter task.')

        if self._ai_task_handle is not None:
            try:
                if not self._ai_task_handle.is_task_done():
                    self._ai_task_handle.stop()
                self._ai_task_handle.close()
            except ni.DaqError:
                self.log.exception('Error while trying to terminate analog input task.')
            finally:
                self._ai_task_handle = None

        if self._clk_task_handle is not None:
            try:
                if not self._clk_task_handle.is_task_done():
                    self._clk_task_handle.stop()
                self._clk_task_handle.close()
            except ni.DaqError:
                self.log.exception('Error while trying to terminate clock task.')
                err = -1
            finally:
                self._clk_task_handle = None

    @staticmethod
    def _extract_terminal(term_str):
        """
        Helper function to extract the bare terminal name from a string and strip it of the device
        name and dashes.
        Will return the terminal name in lower case.

        @param str term_str: The str to extract the terminal name from
        @return str: The terminal name in lower case
        """
        term = term_str.strip('/').lower()
        if 'dev' in term:
            term = term.split('/', 1)[-1]
        return term
