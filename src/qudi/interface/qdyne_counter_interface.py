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

__all__ = [
    "CounterType",
    "GateMode",
    "QdyneCounterConstraints",
    "QdyneCounterInterface",
]

import numpy as np
from typing import Union, Type, Iterable, Mapping, Optional, Dict, List, Tuple, Sequence
from enum import Enum
from abc import abstractmethod
from qudi.core.module import Base
from qudi.util.constraints import DiscreteScalarConstraint, ScalarConstraint


class CounterType(Enum):
    TIMETAGGER = 0
    TIMESERIES = 1


class GateMode(Enum):
    UNGATED = 0
    GATED = 1


class QdyneCounterConstraints:
    """Collection of constraints for hardware modules implementing QdyneCounterInterface"""

    def __init__(
        self,
        channel_units: Mapping[str, str],
        counter_type: Union[CounterType, int],
        gate_mode: Union[GateMode, int],
        data_type: Union[Type[int], Type[float], Type[np.integer], Type[np.floating]],
        binwidth: Optional[DiscreteScalarConstraint] = None,
        record_length: Optional[ScalarConstraint] = None,
    ):
        if not isinstance(binwidth, DiscreteScalarConstraint) and binwidth is not None:
            raise TypeError(
                f'"binwidth" must be None or'
                f"{DiscreteScalarConstraint.__module__}.{DiscreteScalarConstraint.__qualname__} instance"
            )
        if not isinstance(record_length, ScalarConstraint) and record_length is not None:
            raise TypeError(
                f'"record_length" must be None or'
                f"{ScalarConstraint.__module__}.{ScalarConstraint.__qualname__} instance"
            )
        self._channel_units = {**channel_units}
        self._counter_type = CounterType(counter_type)
        self._gate_mode = GateMode(gate_mode)
        self._data_type = np.dtype(data_type).type
        self._binwidth = binwidth
        self._record_length = record_length

    @property
    def channel_units(self) -> Dict[str, str]:
        return self._channel_units.copy()

    @property
    def counter_type(self) -> CounterType:
        return self._counter_type

    @property
    def gate_mode(self) -> GateMode:
        return self._gate_mode

    @property
    def data_type(self) -> np.dtype:
        return self._data_type

    @property
    def binwidth(self) -> DiscreteScalarConstraint:
        return self._binwidth

    @property
    def record_length(self) -> ScalarConstraint:
        return self._record_length


class QdyneCounterInterface(Base):
    """Interface class to define the controls for qdyne counting devices.

    A "qdyne counter" is a hardware device that counts events with a time stamp (timetagger, usually digital counter)
    or as a stream of data (timeseries, usually analog counter) during the acquisition period.
    The goal is generally to detect when events happen after a time defining trigger. For a timetagger each event is
    outputted individually with its respective time stamp after the trigger. The timeseries counter records the data
    during the whole acquisition interval.
    It can be used in two modes :
    - "Gated" : The counter is active during high (or low) trigger state.
    - "Ungated" : After a trigger the counter acquires one sweep for a given time and returns in its idle state
                  waiting for the next trigger.
    """

    @property
    @abstractmethod
    def constraints(self) -> QdyneCounterConstraints:
        """Read-only property returning the constraints on the settings for this data streamer."""
        pass

    @property
    @abstractmethod
    def counter_type(self) -> CounterType:
        """Read-only property returning the CounterType Enum"""
        pass

    @property
    @abstractmethod
    def gate_mode(self) -> GateMode:
        """Read-only property returning the currently configured GateMode Enum"""
        pass

    @property
    @abstractmethod
    def data_type(self) -> type:
        """Read-only property returning the current data type"""
        pass

    @property
    @abstractmethod
    def binwidth(self):
        """Read-only property returning the currently set bin width in seconds"""
        pass

    @property
    @abstractmethod
    def record_length(self):
        """Read-only property returning the currently set recording length in seconds for a single trigger/gate"""
        pass

    @abstractmethod
    def configure(
        self,
        bin_width: float,
        record_length: float,
        gate_mode: GateMode,
        data_type: type
    ) -> None:
        """Configure a Qdyne counter. See read-only properties for information on each parameter."""
        pass

    @abstractmethod
    def get_status(self) -> int:
        """Receives the current status of the hardware and outputs it as return value.

         0 = unconfigured
         1 = idle
         2 = running
        -1 = error state
        """
        pass

    @abstractmethod
    def start_measure(self):
        """Start the qdyne counter."""
        pass

    @abstractmethod
    def stop_measure(self):
        """Stop the qdyne counter."""
        pass

    @abstractmethod
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
        pass
