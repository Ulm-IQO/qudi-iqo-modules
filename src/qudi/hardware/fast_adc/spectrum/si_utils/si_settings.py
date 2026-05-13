# -*- coding: utf-8 -*-
"""
This file contains the settings classes for spectrum instrumentation ADC.

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
import typing
from ctypes import c_void_p, c_int16, c_int32, c_uint64


@dataclass
class CardSettings:
    """
    This dataclass contains parameters input to the card.
    """
    ai_ch: tuple = 'CH0'
    ai0_range_mV: int = 1000
    ai0_offset_mV: int = 0
    ai0_term: str = ''
    ai0_coupling: str = ''
    ai1_range_mV: int = 1000
    ai1_offset_mV: int = 0
    ai1_term: str = ''
    ai1_coupling: str = ''
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
            if 'GATE' not in self.acq_mode:
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
        self.buf_size_B = int(seq_size_B * reps_per_buf)
        self.ts_buf_size_B = int(2 * 16 * total_pulse * reps_per_buf)

    @property
    def gated(self):
        return 'GATE' in self.acq_mode

@dataclass
class MeasurementSettings:
    """
    This dataclass contains paremeters given by the logic and used for data process.
    """
    #Fixed
    c_buf_ptr: typing.Any = c_void_p()
    #Given by the config
    num_channels: int = 1
    gated: bool = False
    init_buf_size_S: int = 0
    reps: int = 0
    max_reps_per_buf = 1e4
    data_stack_on: bool = True
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
        seq_size_S_per_ch = int(np.ceil((self.record_length_s / self.binwidth_s) / 16) * 16)  # necessary to be multuples of 16
        self.seq_size_S = self.num_channels * seq_size_S_per_ch
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
        seg_size_S_per_ch = self.gate_length_rounded_S + pre_trigs_S + post_trigs_S
        self.seg_size_S = self.num_channels * seg_size_S_per_ch
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

    @property
    def data_type(self):
        if self.data_bits == 16:
            return c_int16
        elif self.data_bits == 32:
            return c_int32
        else:
            pass

    @property
    def data_bytes_B(self):
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
            reps_per_buf = int(self.init_buf_size_S / self.seq_size_S)
        except ZeroDivisionError:
            reps_per_buf = self.max_reps_per_buf
        self.reps_per_buf = int(min(reps_per_buf, self.max_reps_per_buf))
        self.seq_size_B = int(self.seq_size_S * self.data_bytes_B)

    #Gate
    @property
    def ts_data_type(self):
        return c_uint64


