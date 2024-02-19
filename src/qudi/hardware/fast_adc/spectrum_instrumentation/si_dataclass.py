# -*- coding: utf-8 -*-
"""
This file contains the data classes for spectrum instrumentation ADC.

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
from dataclasses import dataclass


class Data:
    """
    This class contains all the dataclasses of the measurement.
    All the new data and average are stores in dc_new and avg_new classes, and
    stacked on data and avg, respectively.
    """

    def __init__(self):
        self.data_info = DataInfo()
        self.dc = DataClass()
        self.dc_new = DataClass()
        self.avg = AverageDataClass()
        self.avg_new = AverageDataClass()
        self.ts = None
        self.ts_new = None

    def initialize(self, ms, cs):
        self.data_info.data_range_mV = cs.ai_range_mV
        self.data_info.data_bit = ms.data_bits

        self.dc.initialize(ms.total_pulse, ms.seq_size_S)
        self.dc_new.initialize(ms.total_pulse, ms.seq_size_S)
        self.avg.initialize(ms.total_pulse, ms.seq_size_S)
        self.avg_new.initialize(ms.total_pulse, ms.seq_size_S)
        if ms.gated:
            self.ts = TimeStamp()
            self.ts_new = TimeStamp()

@dataclass
class DataClass:
    '''
    default data structure is (num_pulses, all_pulses)
    '''
    data: np.ndarray = np.array([])
    num_pulse: int = 0
    num_rep: int = 0

    def initialize(self, num_pulse, seq_size_S):
        self.num_rep =  0
        self.num_pulse = num_pulse
        self.data = np.zeros((0, seq_size_S), int)

    def stack(self, new_data):
        self.data = np.vstack((self.data, new_data.data))
        self.num_rep += new_data.num_rep

    def get_average(self):
        return self.data.mean(axis=0)

    @property
    def pulse_array(self):
        """
        data reshaped from single multi pulse data into 2d array separated by pulse numbers.
        """
        return self.data.reshape((self.num_pulse, -1))

@dataclass
class AverageDataClass(DataClass):
    """
    Dataclass for averaged sequence data consisting of multiple pulses.
    Use pulse_array to get 2d array segmented by reps.
    """

    def initialize(self, num_pulse, seq_size_S):
        self.num_rep = 0
        self.num_pulse = num_pulse
        self.data = np.empty((0, seq_size_S))

    def update(self, new_avgdata):
        '''
        @param AverageDataClass new_avgdata: :
        '''
        avg_data_set = np.vstack((self.data, new_avgdata.data))
        avg_weights = np.array([self.num_rep, new_avgdata.num_rep], dtype=int)
        try:
            self.data = np.average(avg_data_set, axis=0, weights=avg_weights)
            self.num_rep += new_avgdata.num_rep
        except:
            print(avg_weights, avg_data_set)
        return self.num_rep, self.data


@dataclass
class TimeStamp:
    ts_r: np.ndarray = np.array([], dtype=np.uint64)
    ts_f: np.ndarray = np.array([], dtype=np.uint64)

    def __init__(self):
        self.initialize()

    def initialize(self):
        self.ts_r = np.empty(0, np.uint64)
        self.ts_f = np.empty(0, np.uint64)

    def stack(self, ts):
        """
        @param TimeStamp ts:
        """
        self.ts_r = np.hstack((self.ts_r, ts.ts_r))
        self.ts_f = np.hstack((self.ts_f, ts.ts_f))

@dataclass
class DataInfo:
    data_range_mV: int = 1000
    data_bit: int = 16

