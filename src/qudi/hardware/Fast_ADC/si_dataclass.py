# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware dummy for fast counting devices.

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
from pyspcm import *
from spcm_tools import *
import typing


@dataclass
class Card_settings:
    '''
    This dataclass contains parameters input to the card.
    '''
    card: typing.Any = ''
    ai_range_mV: int = 1000
    ai_offset_mV: int = 0
    ai_term: str = ''
    ai_coupling: str = ''
    acq_mode: str = ''
    acq_HW_avg_num: int = 1
    acq_pre_trigs_S: int = 16
    acq_post_trigs_S: int = 0 #Dynamic
    acq_mem_size_S: int = 0 #Dynamic
    acq_seg_size_S:int = 0 #Dynamic
    acq_loops: int = 0
    buf_size_B: int = 0 #Dynamic
    buf_notify_size_B: int = 4096
    clk_samplerate_Hz: int = 250e6 #Dynamic
    clk_ref_Hz: int = 10e6
    trig_mode: str = ''
    trig_level_mV: int = 1000


    def calc_dynamic_cs(self, gated, binwidth_s, record_length_s):
        self.calc_samplerate_Hz(binwidth_s)
        if gated == False:
            self.calc_ungated_seg_size_S(binwidth_s, record_length_s)


    def calc_samplerate_Hz(self, binwidth_s):
        self.clk_samplerate_Hz = int(np.ceil(1 / binwidth_s))

    def calc_ungated_seg_size_S(self, binwidth_s, record_length_s):
        self.acq_seg_size_S = int(np.ceil((record_length_s / binwidth_s) / 16) * 16)  # necessary to be multuples of 16
        self.acq_post_trigs_S = int(self.acq_seg_size_S - self.acq_pre_trigs_S)

    def get_buf_size_B(self, seq_size_B, reps_per_buf):
        self.buf_size_B = seq_size_B * reps_per_buf

@dataclass
class Card_settings_gated(Card_settings):
    ts_buf_size_B: int = 0
    ts_buf_notify_size_B: int = 4096

    def get_buf_size_B(self, seq_size_B, reps_per_buf):
        super().get_buf_size_B(seq_size_B, reps_per_buf)
        self.ts_buf_size_B = int(16 * reps_per_buf)



@dataclass
class Measurement_settings:
    '''
    This dataclass contains paremeters given by the logic and used for data process.
    '''
    #Fixed
    c_buf_ptr: typing.Any = c_void_p()
    #Given by the config
    gated: bool = False
    init_buf_size_S: int = 0
    #Given by the measurement
    binwidth_s: float = 0
    record_length_s: float =0
    number_of_gates:int = 0
    #Calculated
    seq_size_S: int = 0
    seq_size_B: int = 0
    reps_per_buf: int = 0
    actual_length: float = 0
    data_bits: int = 0
    gate_length_S: int = 0
    gate_end_alignment_S: int = 16

    def return_c_buf_ptr(self):
        return self.c_buf_ptr

    def load_dynamic_params(self, binwidth_s, record_length_s, number_of_gates):
        self.binwidth_s = binwidth_s
        self.record_length_s = record_length_s
        self.number_of_gates = number_of_gates

    def calc_data_size_S(self, pre_trigs_S, post_trigs_S, seg_size_S):
        if self.gated == True:
            self.gate_length_S = self._calc_gate_length_S()
            self.seg_size_S = self._calc_gate_seg_size_S(pre_trigs_S, post_trigs_S)
            self.seq_size_S = self._calc_gate_seq_size_S()

        else:
            self.seg_size_S = seg_size_S
            self.seq_size_S = self.seg_size_S


    def _calc_gate_length_S(self):
        return int(self.record_length_s / self.binwidth_s) #On theory

    def _calc_gate_seg_size_S(self, pre_trigs_S, post_trigs_S):
        return self.gate_length_S + self.gate_end_alignment_S + pre_trigs_S + post_trigs_S


    def _calc_gate_seq_size_S(self):
        return self.seg_size_S * self.number_of_gates

    def assign_data_bit(self, acq_mode):

        if acq_mode == 'FIFO_AVERAGE':
            self.data_bits = 32
        else:
            self.data_bits = 16

    def get_data_type(self):
        if self.data_bits == 16:
            return c_int16
        elif self.data_bits == 32:
            return c_int32
        else:
            pass

    def get_data_bytes_B(self):
        if self.data_bits == 16:
            return 2
        elif self.data_bits == 32:
            return 4
        else:
            pass

    def calc_buf_params(self):
        self.reps_per_buf = int(self.init_buf_size_S / self.seq_size_S)
        self.seq_size_B = self.seq_size_S * self.get_data_bytes_B()

@dataclass()
class Measurement_settings_gated(Measurement_settings):
    c_ts_buf_ptr:  typing.Any = c_void_p()
    ts_data_bits: int = 64
    ts_data_bytes_B: int = 8

    def get_ts_data_type(self):
        return c_int8

    def return_c_ts_buf_ptr(self):
        return self.c_ts_buf_ptr






@dataclass
class CoreData:
    data: np.ndarray = np.array([])
    data_len: int = 0

    def __post_init__(self):
        self.set_len()

    def add(self, d):
        self.data = d.data
        self.data_len = d.data_len

    def set_len(self):
        if not len(self.data) == 0:
            self.data_len = len(self.data)

@dataclass
class AvgData(CoreData):
    num: int = 0

    def add(self, avg_num, avg_data):
        self.avg_num = avg_num
        self.data = avg_data
        self.set_len()

    def update(self, curr_ad):
        avg_data_set = np.vstack((self.data, curr_ad.data))
        avg_weights = np.array([self.avg_num, curr_ad.avg_num])
        self.avg_data = np.average(avg_data_set, axis=0, weights=avg_weights)
        self.avg_num += curr_ad.avg_num
        return self.avg_num, self.avg_data


@dataclass
class PulseDataSingle(CoreData):
    rep_no: np.ndarray = np.array([])
    pulse_no: np.ndarray = np.array([])

    def add(self, d):
        super().add(d)
        self.rep_no = d.rep_no
        self.pulse_no = d.pulse_no

@dataclass
class PulseDataMulti(PulseDataSingle):
    rep: int = 1

    def stack_rep(self, d):
        self.rep_no = np.vstack((self.rep_no, d.rep_no))
        self.data = np.vstack((self.data, d.data))
        self.rep = len(self.rep_no)

    def extract(self, n):
        return self.rep_no[n], self.pulse_no, self.data[n]

    def avgdata(self):
        return self.rep, self.data.mean(axis=0)

@dataclass
class SeqDataSingle(PulseDataSingle):
    pulse: int = 0

    def stack_pulse(self, d):
        self.pulse_no = np.hstack((self.pulse_no, d.pulse_no))
        self.data = np.hstack((self.data, d.data))
        self.pulse = len(self.pulse_no)

    def reshape_1d(self):
        shape_1d = (self.pulse * self.data_len,)
        return self.data.reshape(shape=shape_1d)

    def reshape_2d(self):
        shape_2d = (self.pulse, self.data_len)
        return self.data.reshape(shape=shape2d)

@dataclass
class SeqDataMulti(PulseDataMulti, SeqDataSingle):
    pass

@dataclass
class GateData:
    ts_r: np.ndarray = np.array([])
    ts_f: np.ndarray = np.array([])

    def add_gated(self, d):
        self.ts_r = d.ts_r
        self.ts_f = d.ts_f

@dataclass
class PulseDataSingleGated(PulseDataSingle, GateData):
    def add(self, d):
        super().add(d)
        self.add_gated(d)

@dataclass
class GateDataMulti(PulseDataSingleGated):
    def stack_rep_gated(self, d):
        self.ts_r = np.vstack((self.ts_r, d.ts_r))
        self.ts_f = np.vstack((self.ts_f, d.ts_f))

@dataclass
class PulseDataMultiGated(PulseDataMulti, GateDataMulti):
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
        def stack_pulse(self, d):
            super().stack_pulse(d)
            self.stack_pulse_gated(d)

@dataclass
class SeqDataMultiGated(PulseDataMultiGated, SeqDataSingleGated):
    pass


