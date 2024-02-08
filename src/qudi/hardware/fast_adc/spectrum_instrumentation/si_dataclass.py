# -*- coding: utf-8 -*-

"""
This file contains the data classes for spectrum instrumentation fast counting devices.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""
import numpy as np
from dataclasses import dataclass


class Data:

    def __init__(self):
        self.dc = None
        self.dc_new = None
        self.avg = None
        self.avg_new = None

    def init_data(self, ms, cs):
        if ms.gated:
            self.dc = self.generate_gated_dataclass(ms, cs)
            self.dc_new = self.generate_gated_dataclass(ms, cs)
        else:
            self.dc = self.generate_ungated_dataclass(ms, cs)
            self.dc_new = self.generate_ungated_dataclass(ms, cs)

        self.avg = self.generate_avg_dataclass(ms)
        self.avg_new = self.generate_avg_dataclass(ms)

    def generate_ungated_dataclass(self, ms, cs):
        ungated_dc = SeqDataMulti()
        ungated_dc.data = np.empty((0,ms.seq_size_S), int)
        ungated_dc.total_pulse_number = ms.total_pulse
        ungated_dc.pule_len = ms.seg_size_S
        ungated_dc.data_range_mV = cs.ai_range_mV
        return ungated_dc

    def generate_gated_dataclass(self, ms, cs):
        gated_dc = self.generate_ungated_dataclass(ms, cs)
        gated_dc.ts_r = np.empty(0, np.uint64)
        gated_dc.ts_f = np.empty(0, np.uint64)
        return gated_dc

    def generate_avg_dataclass(self, ms):
        avg = AvgData()
        avg.num = 0
        avg.total_pulse_number = ms.total_pulse
        avg.data = np.empty((0,ms.seq_size_S))
        return avg

@dataclass
class CoreData:
    """
    Dataclass for a single data point in the ADC.
    """
    data: np.ndarray = np.array([])
    data_len: int = 0
    data_range_mV: int = 1000
    data_bit: int = 16

    def __post_init__(self):
        self.set_len()

    def add(self, d):
        self.data = d.data
        self.data_len = d.data_len

    def set_len(self):
        if not len(self.data) == 0:
            self.data_len = len(self.data)

    def reshape_1d(self):
        shape_1d = (self.data_len,)
        return self.data.reshape(shape_1d)

    @property
    def data_mV(self):
        return self.data * self.data_range_mV / 2**self.data_bit

@dataclass
class PulseDataSingle(CoreData):
    """
    Dataclass for a single pulse in the measurement.
    """

    rep_no: np.ndarray = np.array([], dtype=int)
    pulse_no: np.ndarray = np.array([], dtype=int)
    pulse_len: int = 0

    def add(self, d):
        super().add(d)
        self.rep_no = d.rep_no
        self.pulse_no = d.pulse_no

@dataclass
class PulseDataMulti(PulseDataSingle):
    """
    Dataclass for repeated data of a single pulse.
    """

    rep: int = 1

    def stack_rep(self, d):
        self.rep_no = np.vstack((self.rep_no, d.rep_no))
        self.data = np.vstack((self.data, d.data))
        self.rep = self.data.shape[0]

    def extract(self, n):
        return self.rep_no[n], self.pulse_no, self.data[n]

    def avgdata(self):
        return self.data.mean(axis=0)

    def reshape_2d_by_rep(self):
        len = int(self.data_len / self.rep)
        shape_2d = (self.rep, len)
        return self.data.reshape(shape_2d)


@dataclass
class SeqDataSingle(PulseDataSingle):
    """
    Dataclass for sequence data consisting of multiple pulses in one of the repetitions.
    """

    total_pulse_number: int = 0
    seq_len: int = 0

    def stack_pulse(self, d):
        self.pulse_no = np.hstack((self.pulse_no, d.pulse_no))
        self.data = np.hstack((self.data, d.data))
        self.total_pulse_number = len(self.pulse_no)

    def reshape_2d_by_pulse(self):
        len = int(self.data_len / self.total_pulse_number)
        shape_2d = (self.total_pulse_number, len)
        return self.data.reshape(shape_2d)

@dataclass
class SeqDataMulti(PulseDataMulti, SeqDataSingle):
    """
    Dataclass for sequence data consisting of multiple pulses with multiple repetitions.
    """

    def reshape_3d_multi_seq(self):
        shape_3d = (self.rep, self.total_pulse_number, self.pulse_len)
        return self.data.reshape(shape_3d)

    def avgdata(self):
        self.data = self.reshape_2d_by_rep()
        return self.data.mean(axis=0)

@dataclass
class GateData:
    """
    Dataclass for a single timestamp for the rising and falling edges.
    """

    ts_r: np.ndarray = np.array([], dtype=np.uint64)
    ts_f: np.ndarray = np.array([], dtype=np.uint64)

    def add_gated(self, d):
        self.ts_r = d.ts_r
        self.ts_f = d.ts_f

@dataclass
class PulseDataSingleGated(PulseDataSingle, GateData):
    """
    Dataclass for a single pulse with timestamps in the measurement.
    """

    def add(self, d):
        super().add(d)
        self.add_gated(d)

@dataclass
class PulseDataMultiGated(PulseDataSingleGated):
    """
    Dataclass for repeated data of a single pulse with timestamps.
    """

    def stack_rep_gated(self, d):
        self.ts_r = np.hstack((self.ts_r, d.ts_r))
        self.ts_f = np.hstack((self.ts_f, d.ts_f))

@dataclass
class PulseDataMultiGated(PulseDataMulti, PulseDataMultiGated):
    """
    Dataclass for repeated data of a single pulse with timestamps.
    """

    def stack_rep(self, d):
        super().stack_rep(d)
        self.stack_rep_gated(d)

@dataclass
class SeqGateData(GateData):

    def stack_pulse_gated(self, d):
        self.ts_r = np.hstack((self.ts_r, d.ts_r))
        self.ts_f = np.hstack((self.ts_f, d.ts_f))

@dataclass
class SeqDataSingleGated(SeqDataSingle, SeqGateData):
    """
    Dataclass for sequence data consisting of multiple pulses with timestamps in one of the repetitions.
    """
    def stack_pulse(self, d):
        super().stack_pulse(d)
        self.stack_pulse_gated(d)

@dataclass
class SeqDataMultiGated(PulseDataMultiGated, SeqDataSingleGated):
    """
    Dataclass for sequence data with timestamps
    consisting of multiple pulses with multiple repetitions.
    """

    pass

@dataclass
class AvgData(SeqDataSingle):
    """
    Dataclass for averaged sequence data consisting of multiple pulses.
    """

    num: int = 0

    def update(self, curr_ad):
        avg_data_set = np.vstack((self.data, curr_ad.data))
        avg_weights = np.array([self.num, curr_ad.num], dtype=int)
        self.data = np.average(avg_data_set, axis=0, weights=avg_weights)
        self.num += curr_ad.num
        return self.num, self.data

