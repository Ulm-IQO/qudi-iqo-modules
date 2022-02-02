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
import importlib
import threading
import time
import datetime
import inspect
import matplotlib.pyplot as plt


from pyspcm import *
from spcm_tools import *

import numpy as np
from qudi.core.module import Base
from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface
from collections import OrderedDict

from dataclasses import dataclass

@dataclass
class Card_settings:
    ai_range_mV: int = 1000
    ai_offset_mV: int = 0
    ai_term: str = ''
    ai_coupling: str = ''
    acq_mode: str = ''
    acq_HW_avg_num: int = 1
    acq_pre_trigs_S: int = 16
    acq_post_trigs_S: int = 0
    acq_seg_size_S:int = 0
    buf_size_B: int = 0
    buf_notify_size_B: int = 4096
    clk_samplerate_Hz: int = 250e6
    clk_ref_Hz: int = 10e6
    trig_mode: str = ''
    trig_level_mV: int = 1000

@dataclass
class Measurement_settings:
    gated: bool = False
    segs_per_rep_S: int = 0
    segs_per_rep_B: int = 0
    reps_per_buf: int = 0
    init_buf_size_S: int = 0
    binwidth_s: float = 0
    actual_length: float = 0
    number_of_gates:int = 0


class SpectrumInstrumentation(FastCounterInterface):

    '''Hardware class for the spectrum instrumentation card
    Analog Inputs
    trigger_mode:
        'EXT' (External trigger),
        'SW' (Software trigger),
        'CH0' (Channel0 trigger)
    acquistion_mode:
        'FIFO_SINGLE',
        'FIFO_GATE',
        'FIFO_MULTI',
        'FIFO_AVERAGE'

    Config example:

    si:
        module.Class: 'Fast_ADC.spectrum_instrumentation_ver1.SpectrumInstrumentation'
        ai_range_mV: 1000
        ai_offset_mV: 0
        ai_termination: '50Ohm'
        ai_coupling: 'AC'
        acq_mode: 'FIFO_MULTI'
        acq_HW_avg_num: 1
        acq_pre_trigger_samples: 16
        buf_notify_size: 4096
        clk_reference_Hz: 10e6
        trig_mode: 'EXT'
        trig_level_mV: 1000
        gated: False
        _init_buf_size_S: 1e9
    '''

    _modtype = 'SpectrumCard'
    _modclass = 'hardware'

    _ai_range_mV = ConfigOption('ai_range_mV', 1000, missing='warn')
    _ai_offset_mV = ConfigOption('ai_offset_mV', 0, missing='nothing')
    _ai_term = ConfigOption('ai_termination', '50Ohm', missing='warn')
    _ai_coupling = ConfigOption('ai_coupling', 'DC', missing='warn')
    _acq_mode = ConfigOption('acq_mode', 'FIFO_MULTI', missing='warn')
    _acq_HW_avg_num = ConfigOption('acq_HW_avg_num', 1, missing='nothing')
    _acq_pre_trigs_S = ConfigOption('acq_pre_trigger_samples', 16, missing='nothing')
    _buf_notify_size_B = ConfigOption('buf_notify_size_B', 4096, missing='nothing')
    _clk_ref_Hz = ConfigOption('clk_reference_Hz', 10e6, missing='nothing')
    _trig_mode = ConfigOption('trig_mode', 'EXT', missing='warn')
    _trig_level_mV = ConfigOption('trig_level_mV', '1000', missing='warn')

    _gated = ConfigOption('gated', False, missing='warn')
    _init_buf_size_S = ConfigOption('initial_buffer_size_S', 1e9, missing='warn')

    _check_buffer = False
    _path_for_buffer_check = ConfigOption('path_for_buffer_check', 'C:',missing='nothing')
    _reps_for_buffer_check = ConfigOption('repititions_for_buffer_check', 1, missing='nothing')


    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.cs = Card_settings()
        self.ms = Measurement_settings()
        self.dp = Data_process()

        self._card_on = False
        self._internal_status = 1

    def on_activate(self):
        """
        Open the card by activation of the module
        """
        self._load_settings()
        self.dp.load_params(self.cs, self.ms)

        if self._card_on == False:
            self.card = spcm_hOpen(create_string_buffer(b'/dev/spcm0'))
            self._card_on = True
            self.cfg = Configure(self.cs, self.ms)
            self.cfg.card = self.card
            self.dp.card = self.card

        else:
            print('card is on')

        if self.card == None:
            print('no card found')

    def _load_settings(self):
        self.cs.ai_range_mV = int(self._ai_range_mV)
        self.cs.ai_offset_mV = int(self._ai_offset_mV)
        self.cs.ai_term = self._ai_term
        self.cs.ai_coupling = self._ai_coupling
        self.cs.acq_mode = self._acq_mode
        self.cs.acq_HW_avg_num = int(self._acq_HW_avg_num)
        self.cs.acq_pre_trigs_S = int(self._acq_pre_trigs_S)
        self.cs.buf_notify_size_B = int(self._buf_notify_size_B)
        self.cs.clk_ref_Hz = int(self._clk_ref_Hz)
        self.cs.trig_mode = self._trig_mode
        self.cs.trig_level_mV = int(self._trig_level_mV)
        self.ms.gated = self._gated
        self.ms.init_buf_size_S = int(self._init_buf_size_S)

    def on_deactivate(self):
        """
        Close the card
        """
        spcm_vClose(self.card)


    def get_constraints(self):

        constraints = dict()

        constraints['possible_timebase_list'] = np.array([1, 2, 4, 5, 6, 7, 8, 9, 10, 20, 50, 100, 200, 500, 1e3, 2e3, 5e3, 1e4])
        constraints['hardware_binwidth_list'] = (constraints['possible_timebase_list']) / 250e6 #maximum sampling rate 250 MHz
#        constraints['hardware_binwidth_list'] = 1

        return constraints

    def configure(self, binwidth_s, record_length_s, number_of_gates=0):
        """
        Configure the card parameters.
        @param float binwidth_s: Length of a single time bin in the time trace
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

        self._set_clk_and_acq_params(binwidth_s, record_length_s, number_of_gates)
        self.cfg.set_analog_input_conditions()
        self.dp.data_type, self.dp.data_bytes_B = self.cfg.set_acquistion_mode()
        self.cfg.set_sampling_clock()
        self._set_buf_params()
        self.cfg.set_buffer()
        self.cfg.set_data_transfer()
        self.cfg.set_trigger()

        return self.ms.binwidth_s, self.ms.actual_length, self.ms.number_of_gates

    def _set_clk_and_acq_params(self, binwidth_s, record_length_s, number_of_gates):
        self.ms.binwidth_s = binwidth_s
        self.ms.number_of_gates = number_of_gates

        self.cs.clk_samplerate_Hz = int(np.ceil(1 / binwidth_s))
        self.cs.acq_seg_size_S = int(np.ceil((record_length_s / binwidth_s) / 16) * 16)  # necessary to be multuples of 16
        self.cs.acq_post_trigs_S =  int(self.cs.acq_seg_size_S - self.cs.acq_pre_trigs_S)
        self.ms.actual_length = self.ms.binwidth_s * self.cs.acq_seg_size_S

        if self.ms.gated == True:
            self.ms.segs_per_rep_S = self.cs.acq_seg_size_S * number_of_gates
        else:
            self.ms.segs_per_rep_S = self.cs.acq_seg_size_S

    def _set_buf_params(self):
        self.cfg.c_buf_ptr = c_void_p()
        self.dp.c_buf_ptr = self.cfg.c_buf_ptr
        self.ms.reps_per_buf = int(self.ms.init_buf_size_S / self.ms.segs_per_rep_S)
        self.cs.buf_size_B = self.ms.segs_per_rep_S * self.ms.reps_per_buf
        self.ms.segs_per_rep_B = self.ms.segs_per_rep_S * self.dp.data_bytes_B


    def get_status(self):
        """
        Receives the current status of the Fast Counter and outputs it as
                    return value.

                0 = unconfigured
                1 = idle
                2 = running
                3 = paused
                -1 = error state
        """
        return self._internal_status

    def start_measure(self):
        """
        Start the acquistion and data process loop
        """
        print('start_measure')
        self.configure(self._binwidth, self._actual_length, self._number_of_gates)
        spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_START)
        spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)
        spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_DATA_STARTDMA)
        self.dp.start_data_process()

        return 0

    def get_data_trace(self):
        """
        Fetch the averaged data so far.
        """
        self.dp.fetch_on = True
        avg_data = self.dp.avg_data
        avg_num = self.dp.avg_num
        self.dp.fetch_on = False
#        print('avg_data = {}'.format(avg_data))
        info_dict = {'elapsed_sweeps': avg_num, 'elapsed_time': time.time() - self.dp.start_time}

        return avg_data, info_dict

    def stop_measure(self):
        print('stop_internal_status = {}'.format(self._internal_status))

        if self._internal_status == 2 :
            print('card_stop?')
            self.dp.loop_on = False
            self.dp.stop_data_process()
            spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)
            spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_DATA_STOPDMA)
            spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_STOP)

        #spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_RESET)
        self._internal_status = 1
        self.dp.loop_on = False
        print('stopped')

        return 0

    def pause_measure(self):
        """ Pauses the current measurement.

            Fast counter must be initially in the run state to make it pause.
        """
        spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)
        self.dp.loop_on = False
        self.dp.stop_data_process()

        self._internal_status = 3 #paused
        print('paused')
        return


    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        print('continue measure')
        self._internal_status = 2
        self.dp.loop_on = True
        spcm_dwSetParam_i32(self.card, SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)
        self.dp.start_data_process()

        return 0

    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return self.ms.gated

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self.ms.binwidth_s


##### Check #####

    def _check_error(self, error):

        frame = inspect.currentframe().f_back

        if error != 0:
            print('Error {} at {} in line {}'.format(error, frame.f_code.co_name, frame.f_lineno))
            return

        else:
            #print('no error at {} in line {}'.format(frame.f_code.co_name, frame.f_lineno))
            return

    def _check_overall_error(self, error):
        szErrorTextBuffer = create_string_buffer(ERRORTEXTLEN)
        if error != 0:  # != ERR_OKf
            spcm_dwGetErrorInfo_i32(self.card, None, None, szErrorTextBuffer)
            sys.stdout.write("{0}\n".format(szErrorTextBuffer.value))
            self.log.error("{0}\n".format(szErrorTextBuffer.value))
        else:
            print('no error so far')

        return



class Configure():

    def __init__(self, cs, ms):

        self._ai_range_mV = cs.ai_range_mV
        self._ai_offset_mV =cs.ai_offset_mV
        self._ai_term = cs.ai_term
        self._ai_coupling = cs.ai_coupling
        self._acq_mode = cs.acq_mode
        self._acq_HW_avg_num = cs.acq_HW_avg_num
        self._acq_pre_trigs_S = cs.acq_pre_trigs_S
        self._acq_post_trigs_S = cs.acq_post_trigs_S
        self._acq_seg_size_S = cs.acq_seg_size_S
        self._buf_size_B = cs.buf_size_B
        self._buf_notify_size_B = cs.buf_notify_size_B

        self._clk_samplerate_Hz = int(cs.clk_samplerate_Hz)
        self._clk_ref_Hz = int(cs.clk_ref_Hz)

        self._trig_mode = cs.trig_mode
        self._trig_level_mV = cs.trig_level_mV


    def set_analog_input_conditions(self):
        ai_term_dict = {'1Mohm':0, '50Ohm':1}
        ai_coupling_dict = {'DC':0, 'AC':1}

        spcm_dwSetParam_i32(self.card, SPC_CHENABLE, CHANNEL0)
        spcm_dwSetParam_i32(self.card, SPC_AMP0, self._ai_range_mV) # +- 10 V
        spcm_dwSetParam_i32(self.card, SPC_OFFS0, self._ai_offset_mV)
        spcm_dwSetParam_i32(self.card, SPC_50OHM0, ai_term_dict[self._ai_term]) # A "1"("0") sets the 50(1M) ohm termination
        spcm_dwSetParam_i32(self.card, SPC_ACDC0, ai_coupling_dict[self._ai_coupling])  # A "0"("1") sets he DC(AC)coupling

        return
    def set_acquistion_mode(self):

        if self._acq_mode == 'FIFO_SINGLE':
            data_type, data_bytes_B = self._mode_FIFO_SINGLE()
            print('FIFO_SINGLE is used')

        elif self._acq_mode == 'FIFO_GATE':
            data_type, data_bytes_B = self._mode_FIFO_GATE()

        elif self._acq_mode == 'FIFO_MULTI':
            data_type, data_bytes_B = self._mode_FIFO_MULTI()
            print('FIFO_MULTI is used')

        elif self._acq_mode == 'FIFO_AVERAGE':
            data_type, data_bytes_B = self._mode_FIFO_AVERAGE()

        return data_type, data_bytes_B

    def _mode_FIFO_SINGLE(self):
        data_type = c_int16
        data_bits = 16
        data_bytes_B = 2

        spcm_dwSetParam_i32(self.card, SPC_CARDMODE, SPC_REC_FIFO_SINGLE)
        spcm_dwSetParam_i32(self.card, SPC_PRETRIGGER, self._acq_pre_trigs_S)
        spcm_dwSetParam_i32(self.card, SPC_SEGMENTSIZE, self._acq_seg_size_S)
        spcm_dwSetParam_i32(self.card, SPC_LOOPS, 1)

        return data_type, data_bytes_B


    def _mode_FIFO_GATE(self):
        data_type = c_int16
        data_bits = 16
        data_bytes_B = 2

        spcm_dwSetParam_i32(self.card, SPC_CARDMODE, SPC_REC_FIFO_GATE)
        spcm_dwSetParam_i32(self.card, SPC_PRETRIGGER, self._acq_pre_trigs_S)
        spcm_dwSetParam_i32(self.card, SPC_POSTTRIGGER, self._acq_post_trigs_S)
        spcm_dwSetParam_i32(self.card, SPC_LOOPS, 0)

        return data_type, data_bytes_B
    def _mode_FIFO_MULTI(self):
        data_type = c_int16
        data_bits = 16
        data_bytes_B = 2

        spcm_dwSetParam_i32(self.card, SPC_CARDMODE, SPC_REC_FIFO_MULTI)
        spcm_dwSetParam_i32(self.card, SPC_SEGMENTSIZE, self._acq_seg_size_S)
        spcm_dwSetParam_i32(self.card, SPC_POSTTRIGGER, self._acq_post_trigs_S)
        spcm_dwSetParam_i32(self.card, SPC_LOOPS, 0)

        return data_type, data_bytes_B


    def _mode_FIFO_AVERAGE(self):

        data_type = c_int32
        data_bits = 32
        data_bytes_B = 4
        max_post_trigs_S = 127984

        spcm_dwSetParam_i32(self.card, SPC_CARDMODE, SPC_REC_FIFO_AVERAGE)
        spcm_dwSetParam_i32(self.card, SPC_AVERAGES, self._acq_HW_avg_num)

        #spcm_dwSetParam_i32(self.card, SPC_PRETRIGGER, pre_trig_samples)
        spcm_dwSetParam_i32(self.card, SPC_SEGMENTSIZE, self._acq_seg_size_S)
        spcm_dwSetParam_i32(self.card, SPC_POSTTRIGGER, self._acq_post_trigs_S)
        spcm_dwSetParam_i32(self.card, SPC_LOOPS, 0)

        return data_type, data_bytes_B

    def set_sampling_clock(self):
        spcm_dwSetParam_i32(self.card, SPC_CLOCKMODE, SPC_CM_INTPLL)
        #spcm_dwSetParam_i32(self.card, SPC_CLOCKMODE, SPC_CM_EXTREFCLOCK)
        spcm_dwSetParam_i32(self.card, SPC_REFERENCECLOCK, self._clk_ref_Hz)

        spcm_dwSetParam_i32(self.card, SPC_SAMPLERATE, self._clk_samplerate_Hz)
        spcm_dwSetParam_i32(self.card, SPC_CLOCKOUT, 1)


        return

    def set_buffer(self):
        c_buf_size_B = uint64(self._buf_size_B)  # (self._pre_trigger_samples + self._post_trigger_samples)
        c_cont_buff_len = uint64(0)

        spcm_dwGetContBuf_i64(self.card, SPCM_BUF_DATA, byref(self.c_buf_ptr), byref(c_cont_buff_len))
        self.c_buf_ptr = pvAllocMemPageAligned(c_buf_size_B.value)

        return


    def pvAllocMemPageAligned(self, qwBytes):
        dwAlignment = 4096
        dwMask = dwAlignment - 1

        # allocate non-aligned, slightly larger buffer
        qwRequiredNonAlignedBytes = qwBytes * sizeof(c_char) + dwMask
        pvNonAlignedBuf = (c_char * qwRequiredNonAlignedBytes)()

        # get offset of next aligned address in non-aligned buffer
        misalignment = addressof(pvNonAlignedBuf) & dwMask
        if misalignment:
            dwOffset = dwAlignment - misalignment
        else:
            dwOffset = 0
        return (c_char * qwBytes).from_buffer(pvNonAlignedBuf, dwOffset)

    def set_data_transfer(self):
        c_buf_offset = uint64(0)
        c_buf_size_B = uint64(self._buf_size_B)

        spcm_dwDefTransfer_i64(self.card, SPCM_BUF_DATA, SPCM_DIR_CARDTOPC,
                                       self._buf_notify_size_B, byref(self.c_buf_ptr),
                                       c_buf_offset, c_buf_size_B
                                       )
        return


    def set_trigger(self):

        if self._trig_mode == 'EXT':
            self._trigger_EXT()
            print('External trigger is used')
        elif self._trig_mode == 'SW':
            self._trigger_SW()
            print('Software trigger is used')
        elif self._trig_mode == 'CH0':
            self._trigger_CH0()
            print('Channel0 trigger is used')
        else:
            print('error in set trig')

    def _trigger_EXT(self):

        spcm_dwSetParam_i32(self.card, SPC_TRIG_EXT0_MODE, SPC_TM_POS)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_EXT0_LEVEL0, self._trig_level_mV)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_ORMASK, SPC_TMASK_EXT0)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_ANDMASK, 0)

    def _trigger_SW(self):

#        spcm_dwSetParam_i32(self.card, SPC_TRIG_EXT0_MODE, SPC_TM_POS)
#        spcm_dwSetParam_i32(self.card, SPC_TRIG_EXT0_LEVEL0, trig_level)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_ORMASK, SPC_TMASK_SOFTWARE)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_ANDMASK, 0)

    def _trigger_CH0(self):

        spcm_dwSetParam_i32(self.card, SPC_TRIG_ORMASK, SPC_TMASK_NONE)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_CH_ANDMASK0, SPC_TMASK0_CH0)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_CH0_LEVEL0, self._trig_level_mV)
        spcm_dwSetParam_i32(self.card, SPC_TRIG_CH0_MODE, SPC_TM_POS)

class SIwrapper():
    ##### get parameters from card #####
#    def __init__(self):

    def get_status(self):
        status = int32()
        spcm_dwGetParam_i32(self.card, SPC_M2STATUS, byref(status))
        return status.value

    def get_avail_user_len_B(self):
        c_avaiil_user_len = c_int64(0)
        spcm_dwGetParam_i64(self.card, SPC_DATA_AVAIL_USER_LEN, byref(c_avaiil_user_len))
        return c_avaiil_user_len.value

    def get_avail_user_pos_B(self):
        c_avaiil_user_pos = c_int64(0)
        spcm_dwGetParam_i64(self.card, SPC_DATA_AVAIL_USER_POS, byref(c_avaiil_user_pos))
        return c_avaiil_user_pos.value

    def get_avail_card_len_B(self):
        c_avaiil_card_len = c_int64(0)
        spcm_dwGetParam_i64(self.card, SPC_DATA_AVAIL_CARD_LEN, byref(c_avaiil_card_len))
        return c_avaiil_card_len.value

    def set_avail_card_len_B(self, avail_card_len_B):
        c_avaiil_card_len_B = c_int32(avail_card_len_B)
        spcm_dwSetParam_i32(self.card, SPC_DATA_AVAIL_CARD_LEN, c_avaiil_card_len_B)
        return

    def get_trig_counter(self):
        c_trig_counter = c_int64(0)
        spcm_dwGetParam_i64(self.card, SPC_TRIGGERCOUNTER, byref(c_trig_counter))
        return c_trig_counter.value

class Data_process():

    def __init__(self):
        self.siwrap = SIwrapper()

    def load_params(self, cs, ms):

        self.segs_per_rep_B = ms.segs_per_rep_B
        self.segs_per_rep_S = ms.segs_per_rep_S
        self.reps_per_buf = ms.reps_per_buf
        self.notify_size_B = cs.buf_notify_size_B
        self.V_conv_ratio = cs.ai_range_mV / (2**15)

        self.loop_on = False
        self.fetch_on = False
        self.initial_reps = True

    def init_measure_params(self):
        self.start_time = time.time()
        self.avg_data = np.zeros((self.ms.segs_per_rep_S,), dtype=np.float64)
        self.acg_num = 0
        self.loop_on = True

    def start_data_process(self):

        if self.check_buffer == True:
            print('check buffer')
            self.check_buffer(self._path, self._reps)

        else:
            print('loop')
            self.data_proc_th = threading.Thread(target=self.start_data_process_loop)
            self.data_proc_th.start()

        return

    def stop_data_process(self):
        self.data_proc_th.join()

    def start_data_process_loop(self):
        self.siwrap.card = self.card

        print('start_data_process_loop')

        while self.loop_on == True:
            if self.fetch_on == False:
                curr_avail_reps = self._wait_new_avail_reps()
                seg_start_B = self.siwrap.get_avail_user_pos_B()
                new_avg_data, new_avg_num = self._get_new_data_by_mean(seg_start_B, curr_avail_reps)
                self.avg_data, self.avg_num = self._get_avg_data(self.avg_data, self.avg_num,
                                                                   new_avg_data, new_avg_num)
                self.siwrap.set_avail_card_len_B(self.segs_per_rep_B * curr_avail_reps)
            else:
                print('fetching')
        print('end_data_process_loop')

        return

    def check_buffer(self, path, reps):
        print('start check buffer')
        self.siwrap.card = self.card
        curr_avail_reps = self._wait_new_avail_reps()
        seg_start_B = self.siwrap.get_avail_user_pos_B()
        while curr_avail_reps < reps:#self.reps_per_buf:
            #prev_avail_reps = curr_avail_reps
            curr_avail_reps = self._wait_new_avail_reps()

        np_buffer = (np.ctypeslib.as_array(cast(addressof(self.c_buf_ptr) + seg_start_B,
                              POINTER(self.data_type)),
                              shape=((curr_avail_reps * self.segs_per_rep_S, )))
 #                             .mean(axis=0)
                  )#*self.V_conv_ratio
        np.savetxt(path, np_buffer,'%d')
        print('Done with writing')


    def _wait_new_trigger(self, prev_trig_counts):
        print('waiting for triggers')
        curr_trig_counts = self.siwrap.get_trig_counter()
        while curr_trig_counts == prev_trig_counts:
                curr_trig_counts = self.siwrap.get_trig_counter()
        print('got_new_triggs {}'.format(curr_trig_counts))

        return curr_trig_counts

    def _wait_new_avail_reps(self):
        curr_avail_reps = int(np.floor(self.siwrap.get_avail_user_len_B() / self.segs_per_rep_B))
        if curr_avail_reps == 0:
            #print('waiting for new avail reps')
            while curr_avail_reps == 0:
                curr_avail_reps = int(np.floor(self.siwrap.get_avail_user_len_B() / self.segs_per_rep_B))

        return curr_avail_reps

    def _get_new_data_by_mean(self, seg_start_B, curr_avail_reps):
        print('#####get_new_data_by_mean#####')
        rep_end = int(seg_start_B /self.segs_per_rep_B) + curr_avail_reps

        if 0 < rep_end <= reps_per_buf:
            print('***within buffer***')
            np_new_avg_data = self._fetch_reps_by_mean(seg_start_B, curr_avail_reps)

        elif self.reps_per_buf < rep_end < 2 * self.reps_per_buf:
            print('***end of buffer***')
            start_rep_num = int((seg_start_B / self.segs_per_rep_B) + 1)
            curr_avail_reps_tail = self.reps_per_buf - (start_rep_num - 1)
            curr_avail_reps_head = curr_avail_reps - curr_avail_reps_tail

            np_avg_data_tail = self._fetch_reps_by_mean(seg_start_B, curr_avail_reps_tail)
            np_avg_data_head = self._fetch_reps_by_mean(0, curr_avail_reps_head)
            avg_weights = np.array([curr_avail_reps_tail, curr_avail_reps_head])

            np_new_avg_data_set = (np.append(np_avg_data_tail, np_avg_data_head, axis=0)
                                   .reshape((2, segs_per_rep_S))
                                   )
            np_new_avg_data = np.average(np_new_avg_data_set, weights=avg_weights, axis=0)

        else:
            print('error')
            return


        return np_new_avg_data, curr_avail_reps

    def _fetch_reps_by_mean (self, seg_start_B, curr_avail_reps):
        np_data = (np.ctypeslib.as_array(cast(addressof(self.c_buf_ptr) + seg_start_B,
                                         POINTER(self.data_type)),
                                         shape=((curr_avail_reps, self.segs_per_rep_S ))
                                         )
                   .mean(axis=0)
                  )*self.V_conv_ratio

        return np_data



    def _get_one_rep_per_notify(self):
        np_new_data = np.empty((0, 1))
        processed_data_B = 0
        while processed_data_B < self.segs_per_rep_B:
            avail_user_len_B = self.siwrap.get_avail_user_len_B()
            if avail_user_len_B >= self.notify_size_B:
                np_new_data = self._append_notified(np_new_data)
                processed_data_B += notify_size_B

        return np_new_data, 1


    def _append_notified(self, np_old_data):
        notify_size_S = int(self.notify_size_B / self.data_bytes_B)
        user_pos_B = int(self.siwrap.get_avail_user_pos_B())
        np_data =np.append(np_old_data,
                           np.ctypeslib.as_array(cast(addressof(self.c_buf) + user_pos_B,
                                                      POINTER(c_int16)
                                                      ),
                                                 shape=((int(notify_size_S), ))
                                                 )
                           )
        self.siwrap.set_avail_card_len_B(self.notify_size_B)
        return np_data


    def _get_avg_data(self, np_avg_data, prev_avg_reps, np_new_avg_data,  curr_avail_reps):


        if self.initial_reps == True:
            np_avg_data = np.empty((self.segs_per_rep_S,), dtype=np.float64)
            avg_num = 0
            self.initial_reps = False


        else:
            if prev_avg_reps == 0:
                np_avg_data = np_new_avg_data
                avg_num = curr_avail_reps

            else:
                np_avg_data_set = (np.append(np_avg_data, np_new_avg_data, axis=0)
                                     .reshape((2, self.segs_per_rep_S))
                                   )
                avg_weights = np.array([prev_avg_reps, curr_avail_reps])
                np_avg_data = np.average(np_avg_data_set, axis=0, weights=avg_weights)
                avg_num = prev_avg_reps + curr_avail_reps

        return np_avg_data, avg_num

