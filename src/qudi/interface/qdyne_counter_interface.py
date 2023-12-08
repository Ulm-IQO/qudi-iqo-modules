# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware interface for qdyne counting devices.

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

from abc import abstractmethod
from qudi.core.module import Base


class FastCounterInterface(Base):
    """ Interface class to define the controls for qdyne counting devices.

    A "qdyne counter" is a hardware device that count events with a time stamp.
    The goal is generally to detect when events happen after a time defining trigger. Each event is outputted
    individually with its respective time stamp after the trigger. These events can be photons arrival on a detector
    for example, and the trigger the start of the acquisition.
    This type of hardware regularly records millions of events in repeated acquisition (ie sweeps) in a few seconds,
    with one or multiple events per trigger (depending on the hardware constraints).
    It can be used in two modes :
    - "Gated" : The counter is active during high (or low) trigger state.
    - "Ungated" : After a trigger the counter acquires one sweep for a given time and returns in its idle state
                  waiting for the next trigger.

    """

    @abstractmethod
    def get_constraints(self):
        """ Retrieve the hardware constrains from the qdyne counting device.

        @return dict: dict with keys being the constraint names as string and
                      items are the definition for the constraints.

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
        # current binwidth in seconds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = []

        """
        pass

    @abstractmethod
    def configure(self, bin_width_s, record_length_s):
        """ Configuration of the qdyne counter.

        @param float bin_width_s: Length of a single time bin in the time race histogram in seconds.
        @param float record_length_s: Length of the single acquisition/gate in seconds.

        @return tuple(binwidth_s, record_length_s):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
        """
        pass

    @abstractmethod
    def get_status(self):
        """ Receives the current status of the hardware and outputs it as return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
       -1 = error state
        """
        pass

    @abstractmethod
    def start_measure(self):
        """ Start the qdyne counter. """
        pass

    @abstractmethod
    def stop_measure(self):
        """ Stop the qdyne counter. """
        pass

    @abstractmethod
    def is_time_tagger(self):
        """ Check whether the qdyne counter is a time tagger (commonly digital counters) or
        time series counter (commonly analog counters).

        @return bool: Boolean value indicates if the qdyne counter is a time tagger (TRUE) or
                      a time series counter (FALSE).
        """

    @abstractmethod
    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the qdyne counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        pass

    @abstractmethod
    def get_binwidth(self):
        """ Returns the width of a single timebin.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        pass

    @abstractmethod
    def get_data_trace(self):
        """ Polls the current time tag data or time series data from the qdyne counter.

        Return value is a numpy array (dtype = int64).
        The binning, specified by calling configure() in forehand, must be
        taken care of in this hardware class.
        The counter will return a tuple (1D-numpy-array, info_dict).
        If the counter is digital it will commonly return time tag data in the format
            returnarray = [0, timetag1, timetag2 ... 0, ...], where each 0 indicates a new sweep.
        If the counter is analog it will commonly return time series data in the format
            returnarray = [val_11, val_12 ... val_1N, val_21 ...], where the value for every bin and every sweep
            is concatenated.

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds
        If the hardware does not support these features, the values should be None
        """
        pass
