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

import os
import time
import numpy as np
import zmq, socket

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface

class IDQuantique(FastCounterInterface):
    """ Hardware Class for the IDQ Time Tagger.

        This card has a start input and up to 4 inputs for stops. To include an input, include it as an
        input#_config option to specify which inputs are being used. The start input is assumed
        to be the start trigger.

        Example config for copy-paste:

        idquantique_ID1000:
            module.Class: 'IDQuantique.idquantique_ID1000.IDQuantique'
            options:
                gated: False
                minimal_binwidth: 1e-12
                ip_address: '169.254.202.103'
                start_channel_config: {'threshold_voltage': '0.2V'}
                input1_config: {'threshold_voltage': '0.2V', 'delay_ps': 0.0}
        """

    gated = ConfigOption('gated', False, missing='warn')
    trigger_safety = ConfigOption('trigger_safety', 200e-9, missing='warn')
    aom_delay = ConfigOption('aom_delay', 0.0, missing='warn')
    minimal_binwidth = ConfigOption('minimal_binwidth', 1e-12, missing='warn')
    model = ConfigOption('model', 'ID1000', missing='warn')
    ip = ConfigOption('ip_address', missing='error')
    _start_config = ConfigOption('start_channel_config', default=None, missing='error')
    _input1_config = ConfigOption('input1_config', default=None, missing='nothing')
    _input2_config = ConfigOption('input2_config', default=None, missing='nothing')
    _input3_config = ConfigOption('input3_config', default=None, missing='nothing')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_activate(self):
        try:
            port = 5555
            addr = f'tcp://{self.ip}:{port}'

            context = zmq.Context()
            self.timetagger = context.socket(zmq.REQ)
            self.timetagger.connect(addr)
            self.log.info(f'Connection to IDQ card successful')
        except:
            self.log.info('Connection unsuccessful')

        # Finding active inputs
        self._input_configs = [self._input1_config, self._input2_config, self._input3_config]
        self._active_input_nums = self.find_active_input_nums(self._input_configs)

        # Check that start and at least one input is configured
        if self._start_config == None:
            self.log.error('Start channel not configured, see config file')
        if self._active_input_nums == []:
            self.log.error('No input channels configured, see config file')

        # change this so you can select which one you look at at the end
        self._measure_hist_num = 2
        self._binwidth_s = self.get_constraints()['hardware_binwidth_list'][0]

        # Setting up the channel configurations
        self.prepare_fast_card()
        return

    def on_deactivate(self):
        port = 5555
        addr = f'tcp://{self.ip}:{port}'

        self.stop_measure()
        self.timetagger.disconnect(addr)
        return

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

        # Example for configuration with default values:

        constraints = dict()

        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seonds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = []

        """
        constraints = dict()

        constraints['hardware_binwidth_list'] = np.linspace(1e-12,1e-6,100)
        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """ Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time race histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, record_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
                    number_of_gates: the number of gated, which are accepted, None if not-gated
        """

        old_bin_width_s = self.get_binwidth()
        self._binwidth_s = bin_width_s

        self.configure_histograms(self._binwidth_s, record_length_s)

        self.log.info(self.execute('STATe?'))

        return self.get_binwidth(), record_length_s, number_of_gates

    def get_status(self):
        """ Receives the current status of the hardware and outputs it as return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
       -1 = error state
        """
        idq_current_state = self.execute('REC:STAGe?')
        if idq_current_state == "IDLE":
            return 0
        if idq_current_state == "WAITing":
            return 1
        if idq_current_state == "PLAYing":
            return 2
        if idq_current_state == "ARMed":
            return 3
        if not self.execute('HIREs:ERROr?') == 0:
            return -1

    def start_measure(self):
        """ Start the fast counter. """
        self.execute(f'STAR:COUNter:RESEt')
        for input_num in self._active_input_nums:
            # tsco_num = input_num + 4
            self.execute(f'INPUt{input_num}:COUNter:RESEt')
        self.execute(f'REC:TRIG:ARM:MODE AUTO')
        return

    def stop_measure(self):
        """ Stop the fast counter. """
        self.execute(f'REC:TRIG:ARM:MODE MANUAL')
        self.execute('REC:TRIG:DARM')
        return

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        return

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        return

    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return self.gated

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        bwidth_str = self.execute('HIST1:BWIDth?')[:-2]
        return float(bwidth_str)*1e-12

    def get_data_trace(self):
        """ Polls the current timetrace data from the fast counter.

        Return value is a numpy array (dtype = int64).
        The binning, specified by calling configure() in forehand, must be
        taken care of in this hardware class. A possible overflow of the
        histogram bins must be caught here and taken care of.
        If the counter is NOT GATED it will return a tuple (1D-numpy-array, info_dict) with
            returnarray[timebin_index]
        If the counter is GATED it will return a tuple (2D-numpy-array, info_dict) with
            returnarray[gate_index, timebin_index]

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds

        If the hardware does not support these features, the values should be None
        """
        info_dict = {'elapsed_sweeps': '', 'elapsed_time': ''}
        time_trace = self.execute(f'HIST{self._measure_hist_num}:DATA?')
        elapsed_sweeps = self.execute('STARt:COUNTer?')
        elapsed_time = self._record_length_s * elapsed_sweeps
        info_dict['elapsed_sweeps'] = elapsed_sweeps
        info_dict['elapsed_time'] = elapsed_time
        self.log.info(time_trace)

        return time_trace, info_dict


#########################################################################################################

#                                     Non-interface methods                                             #

#########################################################################################################


    def execute(self, command):
        self.timetagger.send_string(command)
        answer = self.timetagger.recv().decode('utf-8')
        return answer

    def find_active_input_nums(self, input_configs):
        active_input_nums = []
        input_num = 1
        if input_configs != []:
            for input_config in input_configs:
                if input_config != None:
                    active_input_nums.append(input_num)
                input_num += 1
        return active_input_nums

    def prepare_fast_card(self):
        # Setting up the device
        self.execute('DEVIce:RES HIRES')
        self.execute('DEVI:SYNC EXTERNAL')

        # Enabling, configuring and linking the blocks
        # Configure the start channel

        self.execute('STARt:ENABle ON')
        self.execute('STARt:COUPling DC')
        self.execute('STARt:COUNter:MODE ACCUM')
        self.execute('STARt:COUNter:RESEt')
        self.execute('STARt:EDGE RISING')
        self.execute(f'STARt:THREshold {self._start_config["threshold_voltage"]}')
        self.execute('STARt:SELEct UNSHAPED')
        self.execute('STARt:HIREs:ERROr:CLEAr')
        # Connecting Start to delay5
        self.execute(f'DELA5:LINK STAR')
        self.execute(f'DELA5:VALUe 0.0')
        # Linking start (via delay5) to the RECord block
        self.execute('RECord:ENABle ON')
        self.execute('REC:TRIG:LINK DELA5')
        self.execute('REC:TRIG:DELA 0')


        # Configure active inputs, active input channels are entered using the config options
        for input_num in self._active_input_nums:
            input_config = self._input_configs[input_num - 1]
            input_threshold = input_config['threshold_voltage']
            input_delay_ps = input_config['delay_ps']
            # Configure the input
            self.execute(f'INPUt{input_num}:ENAB ON')
            self.execute(f'INPUt{input_num}:COUP DC')
            self.execute(f'INPUt{input_num}:EDGE RISING')
            self.execute(f'INPUt{input_num}:THREshold {input_threshold}')
            self.execute(f'INPUt{input_num}:SELEct UNSHAPED')
            self.execute(f'INPUt{input_num}:HIREs:ERROr:CLEAr')
            self.execute(f'INPUt{input_num}:COUNter:MODe ACCUm')
            self.execute(f'INPUt{input_num}:COUNter:RESEt')

            # Configure the delay
            self.execute(f'DELA{input_num}:VALUe {input_delay_ps}')
            self.execute(f'DELA{input_num}:LINK INPUt{input_num}')

            #TODO Implement TSCOs with software update to do gated counting

            # Configure combiners, input 1 -> tsco5, input 2 -> tsco6, input 3 -> tsco7, input 4 -> tsco8
            tsco_num = input_num + 4
            self.execute(f'TSCO{tsco_num}:FIRst:LINK DELA{input_num};:TSCO{tsco_num}:OPIN ONLYFIR')
            self.execute(f'TSCO{tsco_num}:WIND:ENABle OFF')

            # Link the histograms of the active inputs and disconnect ref from start
            self.execute(f'HIST{input_num}:ENABle:LINK REC')
            self.execute(f'HIST{input_num}:REF:LINK STAR')
            self.execute(f'HIST{input_num}:STOP:LINK TSCO{tsco_num}') # when gated, needs to be TSCO
            self.execute(f'HIST{input_num}:BWID {self._binwidth_s * 1e12}')

    def configure_histograms(self, bin_width_s, record_length_s):

        self._bin_count = int((record_length_s - self.trigger_safety) / bin_width_s)
        self._binwidth_s = bin_width_s
        self.log.info(f'bin count: {self._bin_count}, bin width {self._binwidth_s}')
        self._record_length_s = self._bin_count * bin_width_s

        # Configure histograms
        for input_num in self._active_input_nums:
            # Setting the bin count number
            self.execute(f'HISTogram{input_num}:BCOUnt {self._bin_count}')
            # Setting the bin width in ps
            self.execute(f'HISTogram{input_num}:BWIDth {bin_width_s * 1e12}')

        # flush histogram in case there is some data in it
        for input_num in self._active_input_nums:
            self.execute(f'HIST{input_num}:FLUS')

        # Configure the recording of the data
        self.execute(f'REC:NUM 1;DUR {self._record_length_s * 1e12}')

    def wait_end_of_acquisition(self):
        # Wait while RECord is playing
        while self.execute("REC:STAGe?").upper() == "PLAYING":
            time.sleep(1)

