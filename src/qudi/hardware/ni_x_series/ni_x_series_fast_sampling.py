# -*- coding: utf-8 -*-

"""
A hardware module for communicating with the fast counter FPGA.

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

import numpy as np
import ctypes
import time
import numpy as np
import nidaqmx as ni
import nidaqmx.constants as cst
from nidaqmx._lib import lib_importer  # Due to NIDAQmx C-API bug needed to bypass property getter
from nidaqmx.stream_readers import AnalogMultiChannelReader, CounterReader

from qudi.util.mutex import RecursiveMutex
from qudi.core.configoption import ConfigOption
from qudi.util.helpers import natural_sort
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.core.configoption import ConfigOption
from qudi.interface.finite_sampling_input_interface import FiniteSamplingInputConstraints

class NiXFastSampling(FastCounterInterface):
    """ Hardware class to controls a Time Tagger from Swabian Instruments.

    Example config for copy-paste:

    ni_fast_sampling:
        module.Class: 'ni_x_series.ni_x_series_fast_sampling.NiXFastSampling'
        options:
            device_name: 'Dev3'
            analog_channel_units:  # optional
                'ai0': 'V'
            external_sample_clock_source: 'PFI0'  # optional
            num_pusles : 100 
            adc_voltage_range: [-10, 10]  # optional, default [-10, 10]
            max_channel_samples_buffer: 10000000  # optional, default 10000000
            #read_write_timeout: 10  # optional, default 10
            # sample_clock_output: '/Dev3/PFI12'  # optional

    """

    # config options
    _device_name = ConfigOption(name='device_name', default='Dev1', missing='warn')
    _digital_channel_units = ConfigOption(name='digital_channel_units', default=dict(), missing='info')
    _analog_channel_units = ConfigOption(name='analog_channel_units', default=dict(), missing='info')
    _external_sample_clock_source = ConfigOption(
        name='external_sample_clock_source', default=None, missing='nothing')
    _external_sample_clock_frequency = ConfigOption(
        name='external_sample_clock_frequency', default=None, missing='nothing')

    _physical_sample_clock_output = ConfigOption(name='sample_clock_output', default=None)

    _adc_voltage_range = ConfigOption('adc_voltage_range', default=(-10, 10), missing='info')
    _max_channel_samples_buffer = ConfigOption(
        'max_channel_samples_buffer', default=25e6, missing='info')
    
    _num_pusles = ConfigOption('num_pusles', default=200, missing='info')

    _sample_rate_limits = ConfigOption(name='sample_rate_limits', default=(1, 1.25e6))
    _frame_size_limits = ConfigOption(name='frame_size_limits', default=(1, 1e9))
    _rw_timeout = ConfigOption('read_write_timeout', default=20, missing='nothing')

   # Hardcoded data type
    __data_type = np.float64
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # NIDAQmx device handle
        self._device_handle = None
        # Task handles for NIDAQmx tasks
        self._ai_task_handle = None
        self._clk_task_handle = None
        # nidaqmx stream reader instances to help with data acquisition
        self._ai_reader = None

        # List of all available counters and terminals for this device
        self.__all_counters = tuple()
        self.__all_analog_terminals = tuple()

        # currently active channels
        self.__active_channels = dict(ai_channels=frozenset())

        self._thread_lock = RecursiveMutex()
        self._sample_rate = -1
        self._frame_size = -1
        self._constraints = None


    def on_activate(self):
        """Starts up the NI-card and performs sanity checks. 
        """
        print('on activate')
        self._number_of_gates = int(100)
        self._bin_width = 1
        self._record_length = int(4000)

        self._analog_channel_units = dict() if not self._analog_channel_units else self._analog_channel_units
        self._analog_channel_units = {self._extract_terminal(key): value
                                      for key, value in self._analog_channel_units.items()}

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
            'ctr' in ctr.lower())
        self.__all_analog_terminals = tuple(
            term.rsplit('/', 1)[-1].lower() for term in self._device_handle.ai_physical_chans.channel_names)

        # Check analog input channels
        analog_sources = set(src for src in self._analog_channel_units)
        if analog_sources:
            source_set = set(self._extract_terminal(src) for src in analog_sources)
            invalid_sources = source_set.difference(set(self.__all_analog_terminals))
            if invalid_sources:
                self.log.error('Invalid analog source channels encountered. Following sources will '
                               'be ignored:\n  {0}\nValid analog input channels are:\n  {1}'
                               ''.format(', '.join(natural_sort(invalid_sources)),
                                         ', '.join(self.__all_analog_terminals)))
            analog_sources = set(natural_sort(source_set.difference(invalid_sources)))

        # Create constraints object and perform sanity/type checking
        self._channel_units = self._analog_channel_units.copy()
        self._constraints = FiniteSamplingInputConstraints(
            channel_units=self._channel_units,
            frame_size_limits=self._frame_size_limits,
            sample_rate_limits=self._sample_rate_limits
        )

        # Make sure the ConfigOptions have correct values and types
        # (ensured by FiniteSamplingInputConstraints)
        self._sample_rate_limits = self._constraints.sample_rate_limits
        self._frame_size_limits = self._constraints.frame_size_limits
        self._channel_units = self._constraints.channel_units

        # initialize default settings
        self._sample_rate = self._constraints.max_sample_rate
        # TODO: Get real sample rate limits depending on specified channels (see NI FSIO), or include in "ni helper".
        self._frame_size = 0

        self.set_active_channels(analog_sources)
        print('on activate finished')
        self.statusvar = 0

    def get_constraints(self):
        """ Retrieve the hardware constrains from the Fast counting device.

        @return dict: dict with keys being the constraint names as string and
                      items are the definition for the constaints.

         The keys of the returned dictionary are the str name for the constraints
        (which are set in this method).

                    NO OTHER KEYS SHOULD BE INVENTED!

        If you are not sure about the meaning, look in other hardware files to
        get an impression. If still additional constraints are needed, then they
        have to be added to all files containing this interface.

        The items of the keys are again dictionaries which have the generic
        dictionary form:
            {'min': <value>,
             'max': <value>,
             'step': <value>,
             'unit': '<value>'}

        Only the key 'hardware_binwidth_list' differs, since they
        contain the list of possible binwidths.

        If the constraints cannot be set in the fast counting hardware then
        write just zero to each key of the generic dicts.
        Note that there is a difference between float input (0.0) and
        integer input (0), because some logic modules might rely on that
        distinction.

        ALL THE PRESENT KEYS OF THE CONSTRAINTS DICT MUST BE ASSIGNED!
        """
        print('get_constraints')
        constraints = dict()
        constraints['hardware_binwidth_list'] = [1 / 1000e6]
        return constraints

    def on_deactivate(self):
        """ Shut down the NI card.
        """
        print('on_deactivate')
        self.terminate_all_tasks()
        return

    def configure(self, bin_width_s, record_length_s, number_of_gates=0):

        """ Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse
                                    sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, gate_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual set gate length in seconds
                    number_of_gates: the number of gated, which are accepted
        """
        print('configure')
        self._number_of_gates = number_of_gates
        print(self._number_of_gates)
        self._bin_width = bin_width_s * 1e5 
        self._sample_rate = int(1 / self._bin_width)
        self._record_length = record_length_s
        self.num_samples = int(self._record_length / self._bin_width) + 1   # per pulse
        self.statusvar = 1
        print(self._bin_width, self._record_length, self._sample_rate)

        # set up tasks
        self.set_frame_size()
        if self._init_sample_clock() < 0:
            self.terminate_all_tasks()
            self.module_state.unlock()
            raise NiInitError('Sample clock initialization failed; all tasks terminated')
        if self._init_analog_task() < 0:
            self.terminate_all_tasks()
            self.module_state.unlock()
            raise NiInitError('Analog in task initialization failed; all tasks terminated')

        print('configure finished')

        return self._bin_width, self._record_length, self._number_of_gates

    def start_measure(self):
        """ Start the fast counter. """
        print('start_measure')
        assert self.module_state() == 'idle', \
            'Unable to start data acquisition. Data acquisition already in progress.'
        self.module_state.lock()
        
        # start tasks
        # attention: ai_task can only be one, but it can contain several channels!
        # no ai_task is possible, but no clk_task is impossible
        if self._ai_task_handle is not None:
            try:
                self._ai_task_handle.start()
            except ni.DaqError:
                self.terminate_all_tasks()
                self.module_state.unlock()
                raise
        try:
            self._clk_task_handle.start()
        except ni.DaqError:
            self.terminate_all_tasks()
            self.module_state.unlock()
            raise
        
        self.statusvar = 2
        return 0

    def stop_measure(self):
        """ Stop the fast counter. """
        print('stop_measure')
        if self.module_state() == 'locked':
            self.terminate_all_tasks()
            self.module_state.unlock()
        print('stop_measure finished')
        self.statusvar = 1
        return 0

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        print('pause_measure')
        if self.module_state() == 'locked':
            self.terminate_all_tasks()
            self.statusvar = 3
        return 0

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        print('continue_measure')
        if self.module_state() == 'locked':
            self.start_measure()
            self.statusvar = 2
        return 0

    def is_gated(self):
        """ Check the gated counting possibility.
        Boolean return value indicates if the fast counter is a gated counter
        (TRUE) or not (FALSE).
        """
        print('is_gated')
        return False

    def get_data_trace(self):
        """ Polls the current timetrace data from the fast counter.

        @return numpy.array: 2 dimensional array of dtype = int64. This counter
                             is gated the the return array has the following
                             shape:
                                returnarray[gate_index, timebin_index]

        The binning, specified by calling configure() in forehand, must be taken
        care of in this hardware class. A possible overflow of the histogram
        bins must be caught here and taken care of.
        """
        print('get_data_trace')
        print('get_data_trace1')
        info_dict = {'elapsed_sweeps': None,
                     'elapsed_time': None}  
        print('get_data_trace2', self._frame_size)
        print('get_data_trace2,5', self._frame_size * self._num_pusles)
        data = self.get_buffered_samples(self._frame_size * self._num_pusles)
        data = np.array(data) * 1e3 # 1000 corresponds accuration!
        print('get_data_trace3')
        # print(data)
        return data, info_dict


    def get_status(self):
        """ Receives the current status of the Fast Counter and outputs it as
            return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        print('get_status')
        return self.statusvar

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds. """
        print('get_binwidth')
        width_in_seconds = self._bin_width 
        return width_in_seconds

    
# =============================================================================================
    def _init_sample_clock(self):
        """
        NOTE:
        Cause here we use a externel trigger, the clk here will be configerated as a counter to
        drive the ai channels.

        @return int: error code (0: OK, -1: Error)
        """
        if self._clk_task_handle is not None:
            # case 1: exist a externel clk
            # case 2: exist already a previously configerated internel clk
            self.log.error('Sample clock task is already running. Unable to set up a new clock '
                           'before you close the previous one.')
            return -1

        # Try to find an available counter
        # Because Nidaqmx can only set one clk task, so it's ok to use FOR-LOOP here to search one
        # available clk channel. After one is configerated, the others cannot be setted.
        for src in self.__all_counters:
            # Check if task by that name already exists
            task_name = 'SampleClock_{0:d}'.format(id(self))
            try:
                task = ni.Task(task_name)
            except ni.DaqError:
                # can only create one task with this name, otherwise run into except and drop out
                self.log.exception(f'Could not create task with name "{task_name}".')
                return -1

            # Try to configure the task
            try:
                task.co_channels.add_co_pulse_chan_freq(
                    '/{0}/{1}'.format(self._device_name, src),
                    freq=self._sample_rate,
                    idle_state=ni.constants.Level.LOW)
                task.timing.cfg_implicit_timing(
                    sample_mode=ni.constants.AcquisitionType.FINITE,
                    samps_per_chan=self._frame_size)
                task.triggers.start_trigger.cfg_dig_edge_start_trig(
                    '/{0}/{1}'.format(self._device_name, 
                    self._external_sample_clock_source), 
                    trigger_edge=cst.Edge.RISING)
                task.triggers.start_trigger.retriggerable = True
            except ni.DaqError:
                self.log.exception('Error while configuring sample clock task.')
                try:
                    del task
                except NameError:
                    pass
                return -1

            # Try to reserve resources for the task
            try:
                task.control(ni.constants.TaskMode.TASK_RESERVE)
            except ni.DaqError:
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
                    self.log.exception('Error while setting up clock. Probably because no free '
                                       'counter resource could be reserved.')
                    return -1
                continue
            break

        self._clk_task_handle = task
        # if it needs to be outputed, then output
        if self._physical_sample_clock_output is not None:
            clock_channel = '/{0}InternalOutput'.format(self._clk_task_handle.channel_names[0])
            ni.system.System().connect_terms(source_terminal=clock_channel,
                                             destination_terminal='/{0}/{1}'.format(
                                                 self._device_name, self._physical_sample_clock_output))
        return 0

    
    def _init_analog_task(self):
        """
        Set up task for analog voltage measurement.

        @return int: error code (0:OK, -1:error)
        """
        analog_channels = self.__active_channels['ai_channels']
        if not analog_channels:
            return 0
        if self._ai_task_handle:
            self.log.error(
                'Analog input task has already been generated. Unable to set up analog in task.')
            self.terminate_all_tasks()
            return -1
        if self._clk_task_handle is None and self._external_sample_clock_source is None:
            self.log.error(
                'No sample clock task has been generated and no external clock source specified. '
                'Unable to create analog voltage measurement tasks.')
            self.terminate_all_tasks()
            return -1

        clock_channel = '/{0}InternalOutput'.format(self._clk_task_handle.channel_names[0])
        sample_freq = float(self._clk_task_handle.co_channels.all.co_pulse_freq)

        # Set up analog input task
        task_name = 'AnalogIn_{0:d}'.format(id(self))
        try:
            ai_task = ni.Task(task_name)
        except ni.DaqError:
            self.log.exception('Unable to create analog-in task with name "{0}".'.format(task_name))
            self.terminate_all_tasks()
            return -1

        try:
            ai_ch_str = ','.join(['/{0}/{1}'.format(self._device_name, c) for c in analog_channels])
            ai_task.ai_channels.add_ai_voltage_chan(ai_ch_str,
                                                    max_val=max(self._adc_voltage_range),
                                                    min_val=min(self._adc_voltage_range))
            ai_task.timing.cfg_samp_clk_timing(sample_freq,
                                               source=clock_channel,
                                               active_edge=ni.constants.Edge.RISING,
                                               sample_mode=ni.constants.AcquisitionType.CONTINUOUS,
                                               samps_per_chan=self._frame_size)
        except ni.DaqError:
            self.log.exception(
                'Something went wrong while configuring the analog-in task.')
            try:
                del ai_task
            except NameError:
                pass
            self.terminate_all_tasks()
            return -1

        try:
            ai_task.control(ni.constants.TaskMode.TASK_RESERVE)
        except ni.DaqError:
            try:
                ai_task.close()
            except ni.DaqError:
                self.log.exception('Unable to close task.')
            try:
                del ai_task
            except NameError:
                self.log.exception('Some weird namespace voodoo happened here...')

            self.log.exception('Unable to reserve resources for analog-in task.')
            self.terminate_all_tasks()
            return -1

        try:
            self._ai_reader = AnalogMultiChannelReader(ai_task.in_stream)
            # refer to the definition of di_reader above
            self._ai_reader.verify_array_shape = False
        except ni.DaqError:
            try:
                ai_task.close()
            except ni.DaqError:
                self.log.exception('Unable to close task.')
            try:
                del ai_task
            except NameError:
                self.log.exception('Some weird namespace voodoo happened here...')
            self.log.exception('Something went wrong while setting up the analog input reader.')
            self.terminate_all_tasks()
            return -1

        self._ai_task_handle = ai_task
        return 0
    

    def set_active_channels(self, channels):
        """ Will set the currently active channels. All other channels will be deactivated.

        @param iterable(str) channels: Iterable of channel names to set active.
        """
        # the input channel should be the type of "set"
        # the aim of this function is to update the self.__active_channels(a dic type)
        assert hasattr(channels, '__iter__') and not isinstance(channels, str), \
            f'Given input channels {channels} are not iterable'

        assert self.module_state() != 'locked', \
            'Unable to change active channels while finite sampling is running. New settings ignored.'

        channels = tuple(self._extract_terminal(channel) for channel in channels)

        assert set(channels).issubset(set(self._constraints.channel_names)), \
            f'Trying to set invalid input channels "' \
            f'{set(channels).difference(set(self._constraints.channel_names))}" not defined in config.'

        di_channels, ai_channels = self._extract_ai_di_from_input_channels(channels)

        with self._thread_lock:
            self.__active_channels['di_channels'], self.__active_channels['ai_channels'] \
                = frozenset(di_channels), frozenset(ai_channels)

    def set_frame_size(self):
        """ Will set the number of samples per channel to acquire within one frame.

        @param int size: The sample rate to set
        """
        samples = int(round(self.num_samples))
        assert self._constraints.frame_size_in_range(samples)[0], \
            f'frame size "{samples}" to set is out of bounds {self._constraints.frame_size_limits}'
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set frame size. Data acquisition in progress.'
            self._frame_size = samples
            self.log.debug(f'set frame_size to {self._frame_size}')

    def get_buffered_samples(self, number_of_samples=None):
        """ Returns a chunk of the current data frame for all active channels read from the frame
        buffer.
        If parameter <number_of_samples> is omitted, this method will return the currently
        available samples within the frame buffer (i.e. the value of property <samples_in_buffer>).
        If <number_of_samples> is exceeding the currently available samples in the frame buffer,
        this method will block until the requested number of samples is available.
        If the explicitly requested number of samples is exceeding the number of samples pending
        for acquisition in the rest of this frame, raise an exception.

        Samples that have been already returned from an earlier call to this method are not
        available anymore and can be considered discarded by the hardware. So this method is
        effectively decreasing the value of property <samples_in_buffer> (until new samples have
        been read).

        If the data acquisition has been stopped before the frame has been acquired completely,
        this method must still return all available samples already read into buffer.

        @param int number_of_samples: optional, the number of samples to read from buffer

        @return dict: Sample arrays (values) for each active channel (keys)
        """
        # anyway, this function is the same as the NI original read function:
        # read --> demand exceed current number of samples --> wait until it fits (in state block)
        # read --> not exceed or parameter is None --> return demand/current samples  
        data = dict()
        if self.module_state() == 'idle': 
            # samples_in_buffer returns not 0 only when in state 'locked'
            # therefore always read before stopping acquisition
            self.log.error('Unable to read data. Device is not running and no data in buffer.')
            return data


        if number_of_samples > self._frame_size * self._num_pusles:
            raise ValueError(
                f'Number of requested samples ({number_of_samples}) exceeds number of samples '
                f'pending for acquisition ({self._frame_size}).'
            )
        if number_of_samples is not None and self.module_state() == 'locked':
            # wait for the time to read
            while number_of_samples > self.samples_in_buffer():  # TODO: Check whether this works with a real HW
                print(number_of_samples, self.samples_in_buffer())
                time.sleep(0.05)
        try:
            # TODO: What if counter stops while waiting for samples?
            # Read analog channels
            if self._ai_reader is not None:
            # same as above, _ai_reader is already initialized after configerating the task
                # print(number_of_samples , self._num_pusles , len(self.__active_channels['ai_channels']))
                data_buffer = np.zeros(number_of_samples * len(self.__active_channels['ai_channels']))
                read_samples = self._ai_reader.read_many_sample(
                    data_buffer,
                    number_of_samples_per_channel=number_of_samples,
                    timeout=self._rw_timeout)
                if read_samples != number_of_samples  * len(self.__active_channels['ai_channels']):
                    return data
                # for num, ai_channel in enumerate(self.__active_channels['ai_channels']):
                #     data[ai_channel] = data_buffer[num * number_of_samples : (num + 1) * number_of_samples]

        except ni.DaqError:
            self.log.exception('Getting samples from streamer failed.')
            return data
        # return data[self.__active_channels['ai_channels']]
        return data_buffer
    

    def samples_in_buffer(self):
        """ Currently available samples per channel being held in the input buffer.
        This is the current minimum number of samples to be read with "get_buffered_samples()"
        without blocking.

        @return int: Number of unread samples per channel
        """
        with self._thread_lock:
            if self.module_state() == 'locked':
                return self._ai_task_handle.in_stream.avail_samp_per_chan
            return 0
        

    def terminate_all_tasks(self):
        err = 0
        self._ai_reader = None

        if self._ai_task_handle is not None:
            try:
                if not self._ai_task_handle.is_task_done():
                    self._ai_task_handle.stop()
                self._ai_task_handle.close()
            except ni.DaqError:
                self.log.exception('Error while trying to terminate analog input task.')
                err = -1
        self._ai_task_handle = None

        if self._clk_task_handle is not None:
            try:
                if not self._clk_task_handle.is_task_done():
                    self._clk_task_handle.stop()
                self._clk_task_handle.close()
            except ni.DaqError:
                self.log.exception('Error while trying to terminate clock task.')
                err = -1
        self._clk_task_handle = None
        return err

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

    def _extract_ai_di_from_input_channels(self, input_channels):
        """
        Takes an iterable and returns the split up ai and di channels
        @return tuple(di_channels), tuple(ai_channels))
        """
        input_channels = tuple(self._extract_terminal(src) for src in input_channels)

        di_channels = tuple(channel for channel in input_channels if 'pfi' in channel)
        ai_channels = tuple(channel for channel in input_channels if 'ai' in channel)

        assert (di_channels or ai_channels), f'No channels could be extracted from {*input_channels,}'

        return tuple(di_channels), tuple(ai_channels)
    
class NiInitError(Exception):
    pass
