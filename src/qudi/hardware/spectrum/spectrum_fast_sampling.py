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
import time
import numpy as np
import multiprocessing as mp
from pyspcm import *
from spcm_tools import *

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.core.configoption import ConfigOption

from qudi.hardware.spectrum.spectrum_control import communicating, average_func


class SpectrumFastSampling(FastCounterInterface):
    """
    Example config for copy-paste:
    hardware:
        nspectrum_fast_sampling:
            module.Class: 'spectrum.spectrum_fast_sampling.SpectrumFastSampling'
            options:
                buffer_size: 4             # unit: uint64(MEGA_B(4)), as huge as possible
                segment_size: 4096         # samples per trigger
                samples_per_loop: 512      # unit: uint64(KILO_B( ))
                sample_rate: 20            # unit: int64(MEGA( ))
                channel: 1                 # channel = 1
                timeout: 5000              # unit: ms
                input_range: 5000          # unit: mV
    
    """

    # config options
    qwBufferSize = ConfigOption(name='buffer_size', default=4, missing="info")
    lSegmentSize = ConfigOption(name='segment_size', default=4096, missing="info")
    # NotifySize = int32(KILO_B(lSegmentSize / 1024 * 2)) # data with type int16
    qwToTransfer = ConfigOption(name='samples_per_loop', default=512, missing="info")
    samplerate = ConfigOption(name='sample_rate', default=20, missing="info")
    channel = ConfigOption(name='channel', default=1, missing="info")
    timeout = ConfigOption(name='timeout', default=5000, missing="info")
    input_range = ConfigOption(name='input_range', default=5000, missing="info")
    _enable_debug = ConfigOption('enable_debug', default=False)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # self._thread_lock = RecursiveMutex()
        self.controler_pipe1, self.spectrum_pipe1 = mp.Pipe()
        self.average_pipe2, self.spectrum_pipe2 = mp.Pipe()
        self.controler_pipe3, self.average_pipe3 = mp.Pipe()
        self.state_1 = mp.Value("i", 1) #1 for RUN, 0 for STOP
        self.state_3 = mp.Value("i", 1) #1 for RUN, 0 for STOP

        self.spectrum_process = mp.Process(target=communicating, args=(self.spectrum_pipe1, self.spectrum_pipe2, self.state_1, self._enable_debug))
        self.average_process = mp.Process(target=average_func, args=(self.average_pipe2, self.average_pipe3, self.state_3, ))
        self.spectrum_process.start()
        self.average_process.start()

        self.controler_pipe1.send('init')
        if self.controler_pipe1.recv() == 1: # ready
            self.controler_pipe1.send(np.array([self.qwBufferSize,
                                                self.lSegmentSize,
                                                self.qwToTransfer,
                                                self.samplerate,
                                                self.channel,
                                                self.timeout,
                                                self.input_range], dtype='int32')) # send parameters
        else:
            if self._enable_debug: print('not ready for init ') # not ready
        if self.controler_pipe1.recv() == 0: # finished
            if self._enable_debug: print('init finished')
        else:
            if self._enable_debug: print('init failed')

    def on_activate(self):
        """Starts up the NI-card and performs sanity checks. 
        """
        if self._enable_debug: print('on activate')
        self._number_of_gates = int(100)
        self._bin_width = 1
        self._record_length = int(4000)

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
        if self._enable_debug:  print('get_constraints')
        constraints = dict()
        constraints['hardware_binwidth_list'] = [1/40e6, 1/20e6, 1/15e6, 1/10e6, 1/2e6]
        return constraints

    def on_deactivate(self):
        """ Shut down the NI card.
        """
        if self._enable_debug:  print('on_deactivate')
        self.stop_measure()
        self.state_3.value = -1
        self.controler_pipe1.send('deactive')
        self.controler_pipe1.send(None) # excute communicating
        self.spectrum_process.join()
        self.average_process.join()
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
        if self._enable_debug:  print('configure')
        if self._enable_debug:  print('number of gates:',number_of_gates)
        # self._number_of_gates = number_of_gates
        # print(self._number_of_gates)
        # self._bin_width = bin_width_s * 1e5 
        # self._sample_rate = int(1 / self._bin_width)
        # self._record_length = record_length_s
        # self.num_samples = int(self._record_length / self._bin_width) + 1   # per pulse
        # self.statusvar = 1
        # print(self._bin_width, self._record_length, self._sample_rate)
        self._bin_width = bin_width_s
        self._record_length = record_length_s   
        self._number_of_gates = number_of_gates

        
        lNotifySize_list = np.array([1,2,4,8,16,32,64,128,256,512,1e3,2e3,4e3],dtype='int') # Notify size can only in this list
        lSegmentSize_list = lNotifySize_list * 1024 / 2 
        temp_list = np.absolute(lSegmentSize_list - self._record_length / self._bin_width)
         
        self.lSegmentSize = int(lSegmentSize_list[np.where(temp_list == np.min(temp_list))])
        self.qwToTransfer = self.lSegmentSize * self._number_of_gates * 2 / 1024
        self.qwBufferSize = self.qwToTransfer 
        self.samplerate = int(1/self._bin_width/1e3)
        
        self.controler_pipe1.send('config')
        if self.controler_pipe1.recv() == 1: # ready
            self.controler_pipe1.send(np.array([self.qwBufferSize,
                                                self.lSegmentSize,
                                                self.qwToTransfer,
                                                self.samplerate,
                                                self.channel,
                                                self.timeout,
                                                self.input_range], dtype='int')) # send parameters
            if self._enable_debug:  print('not ready for config ') # not ready
        if self.controler_pipe1.recv() == 0: # finished
            if self._enable_debug:  print('config finished')
        else:
            if self._enable_debug:  print('config failed')

        return self._bin_width, self._record_length, self._number_of_gates

    def start_measure(self):
        """ Start the fast counter. """
        print('start_measure')
        self.module_state.lock()
        self.state_1.value, self.state_3.value = 1, 1
        self.controler_pipe1.send('start')
        time.sleep(1)
        self.statusvar = 2
        return 0

    def stop_measure(self):
        """ Stop the fast counter. """
        if self.module_state() == 'locked':
            if self._enable_debug:  print('stop_measure')
            self.state_1.value, self.state_3.value = 0, 0
            time.sleep(0.5) # wait for stop
            self.controler_pipe1.send('stop')
            self.module_state.unlock()
        self.statusvar = 1
        return 0

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        if self._enable_debug:  print('pause_measure')
        if self.module_state() == 'locked':
            self.stop_measure()
            self.statusvar = 3
        return 0

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        if self._enable_debug:  print('continue_measure')
        if self.module_state() == 'locked':
            self.start_measure()
            time.sleep(1)
            self.statusvar = 2
        return 0

    def is_gated(self):
        """ Check the gated counting possibility.
        Boolean return value indicates if the fast counter is a gated counter
        (TRUE) or not (FALSE).
        """
        return True

    def get_data_trace(self, accumulate=True, timeout=None):
        """ Polls the current timetrace data from the fast counter.
        accumulate:          Determine whether to average the return data with the old record.
                             return the accumulated data if True
                             return the last measured data if False
                             default True

        @return numpy.array: 2 dimensional array of dtype = int64. This counter
                             is gated the the return array has the following
                             shape:
                                returnarray[gate_index, timebin_index]

        The binning, specified by calling configure() in forehand, must be taken
        care of in this hardware class. A possible overflow of the histogram
        bins must be caught here and taken care of.
        """
        if self._enable_debug:  print('get_data_trace')
        self.state_3.value = 2 if accumulate else 3

        if timeout:
            if self.controler_pipe3.poll(timeout):
                data = self.controler_pipe3.recv()
            else:
                print('Timeout after %d seconds' % timeout)
                return -1
            
            if self.controler_pipe3.poll(timeout):
                cur_sweep = self.controler_pipe3.recv()
            else:
                print('Timeout after %d seconds' % timeout)
                return -1
        else:
            data = self.controler_pipe3.recv()
            cur_sweep = self.controler_pipe3.recv()

        if self.state_3.value == 1:
            if self._enable_debug:  print('run well---')
        info_dict = {'elapsed_sweeps': cur_sweep,
                     'elapsed_time': None}
        print(data.shape)
        return data, info_dict

    def reset_sweep_count(self):
        self.state_1.value = 2  # Reset counter

    def get_status(self):
        """ Receives the current status of the Fast Counter and outputs it as
            return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        return self.statusvar

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds. """
        if self._enable_debug:  print('get_binwidth')
        width_in_seconds = self._bin_width 
        return width_in_seconds

