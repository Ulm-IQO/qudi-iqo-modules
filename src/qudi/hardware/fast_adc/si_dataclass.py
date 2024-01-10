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
from pyspcm import *
from spcm_tools import *
import typing


@dataclass
class Card_settings:
    '''
    This dataclass contains parameters input to the card.
    '''
    ai_ch: str = 'CH0'
    ai_range_mV: int = 1000
    ai_offset_mV: int = 0
    ai_term: str = ''
    ai_coupling: str = ''
    acq_mode: str = ''
    acq_HW_avg_num: int = 1
    acq_pre_trigs_S: int = 16
    acq_post_trigs_S: int = 0 #Dynamic
    acq_seg_size_S:int = 0 #Dynamic
    acq_mem_size_S: int = 0 #Dynamic
    acq_loops: int = 0
    buf_size_B: int = 0 #Dynamic
    buf_notify_size_B: int = 4096
    clk_samplerate_Hz: int = 250e6 #Dynamic
    clk_ref_Hz: int = 10e6
    trig_mode: str = ''
    trig_level_mV: int = 1000
    ts_buf_size_B: int = 0
    ts_buf_notify_size_B: int = 2048

    def calc_dynamic_cs(self, ms):
        """
        Calculate the card settings parameter which changes according to the measurement settings.

        @param ms: measurement settings class instance
        """
        self.clk_samplerate_Hz = int(np.ceil(1 / ms.binwidth_s))

        self._calc_acq_params(ms.seg_size_S, ms.seq_size_S, ms.reps, ms.total_gate)

    def _calc_acq_params(self, seg_size_S, seq_size_S, reps, total_gate):
        if 'STD' in self.acq_mode:
            self.acq_mem_size_S = int(seq_size_S * reps)
            if not 'GATE' in self.acq_mode:
                self.acq_seg_size_S = int(seg_size_S)
                self.acq_post_trigs_S = int(seg_size_S - self.acq_pre_trigs_S)

        elif 'FIFO' in self.acq_mode:
            if 'GATE' in self.acq_mode:
                self.acq_loops = int(reps * total_gate)
            else:
                self.acq_seg_size_S = int(seg_size_S)
                self.acq_post_trigs_S = int(self.acq_seg_size_S - self.acq_pre_trigs_S)
                self.acq_loops = int(reps)

    def get_buf_size_B(self, seq_size_B, reps_per_buf, total_pulse=1):
        """
        Calculate the buffer size in bytes.

        @param int seq_size_B: sequence size in bytes
        @param int reps_per_buf: Total number of repetitions which can be stored in the given memory
        """
        self.buf_size_B = seq_size_B * reps_per_buf
        self.ts_buf_size_B = int(2 * 16 * total_pulse * reps_per_buf)

    @property
    def gated(self):
        return 'GATE' in self.acq_mode

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
    reps: int = 0
    #Given by the measurement
    binwidth_s: float = 0
    record_length_s: float = 0
    total_gate: int = 1
    total_pulse: int = 1
    #Calculated
    seg_size_S: int = 0
    seq_size_S: int = 0
    seq_size_B: int = 0
    reps_per_buf: int = 0
    actual_length_s: float = 0
    data_bits: int = 0
    #Gate
    c_ts_buf_ptr:  typing.Any = c_void_p()
    ts_data_bits: int = 64
    ts_data_bytes_B: int = 8
    ts_seg_size_S: int = 4 #rise + null + fall + null
    ts_seg_size_B: int = 32
    ts_seq_size_S: int = 8
    ts_seq_size_B: int = 32
    gate_length_S: int = 0
    gate_length_rounded_S: int = 0
    gate_end_alignment_S: int = 16
    double_gate_acquisition: bool = False

    def return_c_buf_ptr(self):
        return self.c_buf_ptr

    def load_dynamic_params(self, binwidth_s, record_length_s, number_of_gates):
        self.binwidth_s = binwidth_s
        self.record_length_s = record_length_s
        self.total_pulse = number_of_gates
        if self.double_gate_acquisition:
            self.total_gate = 2 * self.total_pulse
        else:
            self.total_gate = self.total_pulse

    def calc_data_size_S(self, pre_trigs_S, post_trigs_S):
        if not self.gated:
            self._calc_triggered_data_size_S()
        else:
            self._calc_gated_data_size_S(pre_trigs_S, post_trigs_S)

    def _calc_triggered_data_size_S(self):
        """
        defines the data size parameters for the trigger mode.
        record_length_s is the length of data to be recorded at one trigger in seconds.
        The sequence size is the length of data to be recorded in samples.
        The segment size used for the acquisition setting corresponds to the sequence size.
        """
        self.seq_size_S = int(np.ceil((self.record_length_s / self.binwidth_s) / 16) * 16)  # necessary to be multuples of 16
        self.seg_size_S = self.seq_size_S

    def _calc_gated_data_size_S(self, pre_trigs_S, post_trigs_S):
        """
        defines the data size parameters for the gate mode.
        The gate length is given by the input gate length, which is calculated from record_length_s.
        Note that the actual gate length is rounded by the card specific alignment.
        seg_size_S is the segment size recorded per gate.
        seq_size_S is the sequence size per repetition.
        """
        self.gate_length_S = int(np.ceil(self.record_length_s / self.binwidth_s))
        self.gate_length_rounded_S = int(np.ceil(self.gate_length_S / self.gate_end_alignment_S) * self.gate_end_alignment_S)
        self.seg_size_S = self.gate_length_rounded_S + pre_trigs_S + post_trigs_S
        self.seq_size_S = self.seg_size_S * self.total_gate
        self.ts_seq_size_S = self.ts_seg_size_S * self.total_gate
        self.ts_seq_size_B = self.ts_seg_size_B * self.total_gate

    def calc_actual_length_s(self):
        if not self.gated:
            self.actual_length_s = self.seq_size_S * self.binwidth_s
        else:
            self.actual_length_s = self.seg_size_S * self.binwidth_s

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
        """
        Calculate the parameters related to the buffer size.
        The number of repetitions which can be recorded in the buffer is calculated
        based on the given initial buffer size and the calculated sequence size.
        Maximum repetitions per buffer is used because memory allocation error was observed
        when too big a buffer was defined for lower sampling rate.
        """
        try:
            max_reps_per_buf = 1e6 #empirical value
            reps_per_buf = int(self.init_buf_size_S / self.seq_size_S)
            self.reps_per_buf = min(reps_per_buf, max_reps_per_buf)
            self.seq_size_B = self.seq_size_S * self.get_data_bytes_B()
        except:
            pass

    #Gate
    def get_ts_data_type(self):
        return c_uint64

    def return_c_ts_buf_ptr(self):
        return self.c_ts_buf_ptr

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

