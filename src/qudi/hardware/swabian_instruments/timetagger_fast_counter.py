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
import TimeTagger as tt

from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector

class TimeTaggerFastCounter(FastCounterInterface):
    """ Hardware class to controls a Time Tagger from Swabian Instruments.

    Example config for copy-paste:

    fastcounter_timetagger:
        module.Class: 'swabian_instruments.timetagger_fast_counter.TimeTaggerFastCounter'
        options:
            timetagger_channel_apd_0: 0
            timetagger_channel_apd_1: 1
            timetagger_channel_detect: 2
            timetagger_channel_sync: 3
            timetagger_sum_channels: 4
        connect:
            tagger: 'tagger'

    """
    timetagger = Connector(interface='TT')
    _channel_apd_0 = ConfigOption('timetagger_channel_apd_0', missing='error')
    _channel_apd_1 = ConfigOption('timetagger_channel_apd_1', default=None)
    _channel_detect = ConfigOption('timetagger_channel_detect', missing='error')
    _channel_sync = ConfigOption('timetagger_channel_sync', default=None)
    _sum_channels = ConfigOption('timetagger_sum_channels', default=None)

    def on_activate(self):
        """ Connect and configure the access to the FPGA.
        """
        self._tagger = self.timetagger()#tt.createTimeTagger()
        # self._tagger.tagger.reset()

        self._number_of_gates = int(100)
        self._bin_width = 1
        self._record_length = int(4000)

        if self._sum_channels:
            if self._channel_apd_1:
                self._channel_combined = self._tagger.combiner(channels=[self._channel_apd_0, self._channel_apd_1])#tt.Combiner(self._tagger, channels=[self._channel_apd_0, self._channel_apd_1])
                self._channel_apd = self._channel_combined.getChannel()
            else:
                self.log.warning('_channel_apd_1 is not set. Only _channel_apd_0 is used for now.')
                self._channel_apd = self._channel_apd_0
        else:
            self._channel_apd = self._channel_apd_0

        self.log.info('TimeTagger (fast counter) configured to use  channel {0}'
                      .format(self._channel_apd))

        self.configure()

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

        constraints = dict()

        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seonds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = [1/1e9, 1/1e8, 1/1e7, 1/1e6]

        # TODO: think maybe about a software_binwidth_list, which will
        #      postprocess the obtained counts. These bins must be integer
        #      multiples of the current hardware_binwidth

        return constraints

    def on_deactivate(self):
        """ Deactivate the FPGA.
        """
        if self.module_state() == 'locked':
            self.pulsed.stop()
        self.pulsed.clear()
        self.pulsed = None

    def configure(self, bin_width_s = 1, record_length_s = 1, number_of_gates=1):

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
        self._number_of_gates = number_of_gates
        self._bin_width = bin_width_s * 1e9
        self._record_length = 1 + int(record_length_s / bin_width_s)
        self.statusvar = 1
        bin_width = int(bin_width_s*1e12)
        n_values = int(record_length_s*1e12/bin_width)
        # self.pulsed = self._tagger.counter(channels = [self._channel_apd], bin_width=bin_width, n_values=n_values)
        self.pulsed = self._tagger.time_differences(
           click_channel=self._channel_apd,
           start_channel=self._channel_detect,
           next_channel=self._channel_detect,
           binwidth=int(np.round(self._bin_width * 1000)),
           n_bins=int(self._record_length),
           n_histograms=number_of_gates,
           sync_channel=self._channel_sync,
        )
        self.pulsed.stop()

        return bin_width_s, record_length_s, number_of_gates

    def start_measure(self):
        """ Start the fast counter. """
        #self.module_state.lock()
        self.pulsed.clear()
        self.pulsed.start()
        self._tagger.tagger.sync()
        self.statusvar = 2
        return 0

    def stop_measure(self):
        """ Stop the fast counter. """
        if self.module_state() == 'locked':
            self.pulsed.stop()
            self.module_state.unlock()
        self.statusvar = 1
        return 0

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        if self.module_state() == 'locked':
            self.pulsed.stop()
            self.statusvar = 3
        return 0

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        if self.module_state() == 'locked':
            self.pulsed.start()
            self.statusvar = 2
        return 0

    def is_gated(self):
        """ Check the gated counting possibility.

        Boolean return value indicates if the fast counter is a gated counter
        (TRUE) or not (FALSE).
        """
        return True

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
        info_dict = {'elapsed_sweeps': None,
                     'elapsed_time': None}  # TODO : implement that according to hardware capabilities
        data = self.pulsed.getData()

        return np.array(data, dtype='int64'), info_dict

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
        width_in_seconds = self._bin_width * 1e-9
        return width_in_seconds
