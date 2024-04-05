# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware dummy for pulsing devices.

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

import datetime
import numpy
import os
import time
from typing import Sequence, Union

from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.interface.qdyne_counter_interface import QdyneCounterInterface, QdyneCounterConstraints, CounterType, GateMode

class QdyneCounterDummy(QdyneCounterInterface):
    """ Implementation of the QdyneCounterInterface interface methods for a dummy usage.

    Example config for copy-paste:

    qdyne_counter_dummy:
        module.Class: 'dummy.qdyne_counter_dummy.QdyneCounterDummy'
        options:
            'sine_frequency_Hz': 200e6
    """
    # Declare config options
    _sine_frequency = ConfigOption('sine_frequency_Hz', default=200e6, missing='warn')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self.statusvar = 0
        self._binwidth = 0.1
        self._block_size = 10
        self._record_length = 10
        self._sample_rate = 100
        self._active_channels = ['channel_1']
        self._buffer_size = 2042
        return

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        self.statusvar = -1
        return

    # Defining methods satisfying the qdyne_counter_interface
    def constraints(self):
        """

        :return: QdyneCounterConstraints
        """
        self.constraints = QdyneCounterConstraints(
            channel_units={'channel_1': 'counts'},
            counter_type=CounterType.TIMETAGGER,
            gate_mode=GateMode.UNGATED,
            data_type=float)

        return self.constraints

    def configure(self,
                  bin_width_s: float,
                  record_length_s: float,
                  active_channels: Sequence[str],
                  gate_mode: Union[GateMode, int],
                  buffer_size: int,
                  sample_rate: float,
                  number_of_gates) -> None:
        """ Configure a Qdyne counter. See read-only properties for information on each parameter. """
        self._binwidth_ns = bin_width_s*10e9
        self._record_length_ns = record_length_s*10e9
        return self._binwidth_ns, self._record_length_ns

    def active_channels(self) -> Sequence[str]:
        """ Read-only property returning the currently configured active channel names """
        return self._active_channels

    def gate_mode(self) -> GateMode:
        """ Read-only property returning the currently configured GateMode Enum """
        return self._gate_mode

    def buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size """
        return self._buffer_size

    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz """
        return self._sample_rate_Hz

    def binwidth(self):
        """ Read-only property returning the currently set bin width in seconds """
        return self._binwidth_s

    def record_length(self):
        """ Read-only property returning the currently set recording length in seconds """
        return self._record_length_s

    def number_of_gates(self):
        return 1

    def get_status(self) -> int:
        """ Receives the current status of the hardware and outputs it as return value.

        0 = unconfigured
        1 = idle
        2 = running
       -1 = error state
        """
        return self.statusvar

    def start_measure(self):
        """ Start the qdyne counter. """
        time.sleep(1)
        self.statusvar = 2

        def sine_func(freq, t):
            return numpy.sin(2*numpy.pi*freq*t)

        self._time_tagger_data = []

        num_samples = self._record_length*self._sample_rate
        sample_times = numpy.linspace(0, self._record_length, num_samples)
        _freq = self._sine_frequency

        self.log.debug("I ran though start measure in the dummy")

        for t in sample_times:
            self._time_tagger_data.append(sine_func(_freq, t))

        return 0

    def stop_measure(self):
        """ Stop the qdyne counter. """
        time.sleep(1)
        self.statusvar = 1

        return 0

    def get_data(self) -> tuple:
        """ Polls the current time tag data or time series data from the Qdyne counter.

        Return value is a numpy array of type as given in the constraints.
        The counter will return a tuple (1D-numpy-array, info_dict).
        If the counter is a time tagger it will return time tag data in the format
            returnarray = [0, timetag1, timetag2 ... 0, ...], where each 0 indicates a new sweep.
        If the counter is time series it will return time series data in the format
            returnarray = [val_11, val_12 ... val_1N, val_21 ...], where the value for every bin and every sweep
            is concatenated.

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds
        If the hardware does not support these features, the values should be None
        """
        time.sleep(0.5)
        info_dict = {'elapsed_sweeps': 10, 'elapsed_time': self._record_length}
        return self._time_tagger_data, info_dict








