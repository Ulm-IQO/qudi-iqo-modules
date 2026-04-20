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
import numpy as np
import os
import time
from typing import Sequence, Union

from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import DiscreteScalarConstraint, ScalarConstraint
from qudi.interface.qdyne_counter_interface import (
    QdyneCounterInterface,
    QdyneCounterConstraints,
    CounterType,
    GateMode,
)


class QdyneCounterDummy(QdyneCounterInterface):
    """Implementation of the QdyneCounterInterface interface methods for a dummy usage.

    Example config for copy-paste:

    qdyne_counter_dummy:
        module.Class: 'dummy.qdyne_counter_dummy.QdyneCounterDummy'
        options:
            'sine_frequency_Hz': 200e6
    """

    # Declare config options
    _measurements_per_data_poll: int = ConfigOption("measurements_per_data_poll", default=100)
    _max_number_bins: int = ConfigOption("max_number_bins", default=1e3)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_activate(self):
        """Initialisation performed during activation of the module."""
        self.statusvar = 0
        self._binwidth = 0.1
        self._block_size = 10
        self._record_length = 10
        self._gate_mode = GateMode(0)
        self._number_of_gates = 0
        self._constraints = QdyneCounterConstraints(
            channel_units={"channel_1": "counts"},
            counter_type=CounterType.TIMETAGGER,
            gate_mode=GateMode.UNGATED,
            data_type=float,
            binwidth=DiscreteScalarConstraint(
                default=100e-9,
                allowed_values=set(np.arange(10e-9, 1e-6, 10e-9)),
                #checker=lambda x: (x / 1e-9).is_integer(),
            ),
            record_length=ScalarConstraint(default=1e-6, bounds=(100e-9, 100e-6)),
        )
        self._elapsed_sweeps = 0
        return

    def on_deactivate(self):
        """Deinitialisation performed during deactivation of the module."""
        self.statusvar = -1
        return

    # Defining methods satisfying the qdyne_counter_interface
    @property
    def constraints(self):
        """

        :return: QdyneCounterConstraints
        """
        return self._constraints

    def counter_type(self) -> CounterType:
        """Read-only property returning the CounterType Enum"""
        return self._counter_type

    @property
    def gate_mode(self) -> GateMode:
        """Read-only property returning the currently configured GateMode Enum"""
        return self._gate_mode

    @property
    def data_type(self) -> type:
        """Read-only property returning the current data type"""
        return self._constraints.data_type

    @property
    def binwidth(self):
        """Read-only property returning the currently set bin width in seconds"""
        return self._binwidth

    @property
    def record_length(self):
        """Read-only property returning the currently set recording length in seconds"""
        return self._record_length

    def configure(
        self,
        bin_width: float,
        record_length: float,
        gate_mode: GateMode,
        data_type: type,
    ) -> None:
        """Configure a Qdyne counter. See read-only properties for information on each parameter."""
        self._binwidth = bin_width
        self._record_length = record_length
        if gate_mode != GateMode.UNGATED:
            self.log.error(
                f"Cannot set gate mode {gate_mode}. "
                + f"Use gate mode {0} ({GateMode(0)}) instead."
            )
            self._gate_mode = GateMode.UNGATED
        if any((data_type == dtype for dtype in (int, float, np.int64, np.float64))):
            self._data_type = data_type
        else:
            self.log.error(f"Data type {data_type} is not a valid data type.")
        return self._binwidth, self._record_length, self._gate_mode, self._data_type

    def get_status(self) -> int:
        """Receives the current status of the hardware and outputs it as return value.

         0 = unconfigured
         1 = idle
         2 = running
        -1 = error state
        """
        return self.statusvar

    def start_measure(self):
        """Start the qdyne counter."""
        time.sleep(1)
        self._time_tagger_data = []
        self._elapsed_sweeps = 0
        self.statusvar = 2

    def stop_measure(self):
        """Stop the qdyne counter."""
        time.sleep(1)
        self.statusvar = 1

        return 0

    def get_data(self) -> tuple:
        """Polls the current time tag data or time series data from the Qdyne counter.

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
        self._time_tagger_data = []
        _contrast = 0.69
        _mean = 500

        def poisson_process(t, number_samples):
            mean = (np.sin(2 * np.pi * t) * _contrast * _mean + _mean)
            num_photons = np.random.poisson(mean)
            time_tags = sorted(
                np.random.choice(
                    range(int(0.1 * number_samples), int(0.6 * number_samples)),
                    num_photons,
                )
            )
            return [0] + time_tags

        num_samples = min(self._max_number_bins, round(self.record_length / self.binwidth))
        sample_times = np.linspace(0, 1, self._measurements_per_data_poll)

        for t in sample_times:
            self._time_tagger_data += poisson_process(t, num_samples)
        self._elapsed_sweeps += self._measurements_per_data_poll
        info_dict = {
            "elapsed_sweeps": self._elapsed_sweeps,
            "elapsed_time": self.record_length,
        }
        return self._time_tagger_data, info_dict
