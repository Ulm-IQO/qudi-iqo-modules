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

        Example config for copy-paste:

        idquantique_ID1000:
            module.Class: 'IDQuantique.idquantique_ID1000.IDQuantique'
            options:
                gated: False
                trigger_safety: 200e-9
                aom_delay: 400e-9
                minimal_binwidth: 1e-9
                ip_address: 169.254.202.103
        """

    gated = ConfigOption('gated', False, missing='warn')
    trigger_safety = ConfigOption('trigger_safety', 200e-9, missing='warn')
    aom_delay = ConfigOption('aom_delay', 400e-9, missing='warn')
    minimal_binwidth = ConfigOption('minimal_binwidth', 1e-9, missing='warn')
    model = ConfigOption('model', 'ID1000')
    use_dma = ConfigOption('use_dma', False)
    ip = ConfigOption('ip_address', missing='warn')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_activate(self):
        port = 5555
        addr = f'tcp://{self.ip}:{port}'

        context = zmq.Context()
        self.timetagger = context.socket(zmq.REQ)
        self.timetagger.connect(addr)

    def query(self, query):
        self.timetagger.send_string(query)
        answer = self.timetagger.recv().decode('utf-8')
        return answer

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

        constraints['']



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
        pass


    def get_status(self):
        """ Receives the current status of the hardware and outputs it as return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
       -1 = error state
        """
        current_state = self.query('STATe?')



    def start_measure(self):
        """ Start the fast counter. """
        pass


    def stop_measure(self):
        """ Stop the fast counter. """
        pass

    @abstractmethod
    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        pass

    @abstractmethod
    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        pass

    @abstractmethod
    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        pass

    @abstractmethod
    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self.query('HIST:BWIDth?')

    @abstractmethod
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
        pass







