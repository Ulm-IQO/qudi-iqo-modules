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
import threading
import time
import inspect
from enum import IntEnum

import numpy as np
from pyspcm import *
from spcm_tools import *

from core.configoption import ConfigOption
from interface.fast_counter_interface import FastCounterInterface
from hardware.Fast_ADC.si_dataclass import *

class CardStatus(IntEnum):
    unconfigured = 0
    idle = 1
    running = 2
    paused = 3
    error = -1

def check_card_error(func):
    def wrapper(self, *args, **kwargs):
        value = func(self, *args, **kwargs)
        if self.error_check == True:
            error = self._error
            frame = inspect.currentframe().f_back
            module = inspect.getfile(func)

            if error != 0:
                print('line {} Error {} at {} {} '.format(frame.f_lineno, error, frame.f_code.co_name, module))
                szErrorTextBuffer = create_string_buffer(ERRORTEXTLEN)
                spcm_dwGetErrorInfo_i32(self._card, None, None, szErrorTextBuffer)
                print("{0}\n".format(szErrorTextBuffer.value))

            else:
                print('line {} no error at {}'.format(frame.f_lineno, frame.f_code.co_name))
        else:
            pass
        return value

    return wrapper

class Card_command():
    '''
    This class contains commands related to the start and stop of the card's actions.
    '''

    def __init__(self, card):
        self._card = card
        self.error_check = False

    def start_all(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER
                            | M2CMD_DATA_STARTDMA | M2CMD_EXTRA_STARTDMA)

    def start_all_with_poll(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER
                            | M2CMD_DATA_STARTDMA | M2CMD_EXTRA_POLL)

    @check_card_error
    def card_start(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START)

    @check_card_error
    def card_stop(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_STOP)

    @check_card_error
    def card_reset(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_RESET)

    @check_card_error
    def enable_trigger(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)
        trigger_enabled = True
        return trigger_enabled

    @check_card_error
    def disable_trigger(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)
        trigger_enabled = False
        return trigger_enabled

    @check_card_error
    def force_trigger(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)

    @check_card_error
    def start_dma(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_STARTDMA)

    @check_card_error
    def stop_dma(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_STOPDMA)

    @check_card_error
    def wait_DMA(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_WAITREADY)


class Data_buffer_command(Card_command):
    '''
    This class contains commands to control the data acquistion.
    '''
    _dp_check = True
    _error_check = False

    def __init__(self, card, ms):
        self._card = card
        self._seq_size_B = ms.seq_size_B
        self._no_of_gates = ms.number_of_gates
        self.init_dp_params()

    def init_dp_params(self):
        self.status = 0
        self.avail_user_pos_B = '-----'
        self.avail_user_len_B = '-----'
        self.avail_card_len_B = '-----'
        self.trig_counter = '-----'
        self.processed_data_B = 0
        self.total_data_B = '-----'
        self.avg_num = 0

    def check_dp_status(self):
        self.get_status()
        self.get_avail_user_pos_B()
        self.get_avail_user_len_B()
        self.get_avail_user_reps()
        self.get_trig_counter()
        if self._dp_check == True:
            print("Stat:{0:04x}h Pos:{1:010}B Avail:{2:010}B "
                  "Processed:{3:010}B / {4}B: "
                  "Avail:{5} Avg:{6} / Trig:{7} \n".format(self.status,
                                                           self.avail_user_pos_B,
                                                           self.avail_user_len_B,
                                                           self.processed_data_B,
                                                           self.total_data_B,
                                                           self.avail_user_reps,
                                                           self.avg_num,
                                                           self.trig_counter)
                  )
        else:
            pass

    @check_card_error
    def get_status(self):
        status = int32()
        self._error = spcm_dwGetParam_i32(self._card, SPC_M2STATUS, byref(status))
        self.status = status.value
        return status.value

    @check_card_error
    def get_avail_user_len_B(self):
        c_avaiil_user_len = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_DATA_AVAIL_USER_LEN, byref(c_avaiil_user_len))
        self.avail_user_len_B = c_avaiil_user_len.value
        return self.avail_user_len_B

    def get_avail_user_reps(self):
        self.avail_user_reps = int(np.floor(self.get_avail_user_len_B() / self._seq_size_B))
        return self.avail_user_reps

    @check_card_error
    def get_avail_user_pos_B(self):
        c_avaiil_user_pos = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_DATA_AVAIL_USER_POS, byref(c_avaiil_user_pos))
        self.avail_user_pos_B = c_avaiil_user_pos.value
        return self.avail_user_pos_B

    @check_card_error
    def get_avail_card_len_B(self):
        c_avaiil_card_len_B = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_DATA_AVAIL_CARD_LEN, byref(c_avaiil_card_len_B))
        self.avail_card_len_B = c_avaiil_card_len_B.value
        return self.avail_card_len_B

    #@check_card_error
    def set_avail_card_len_B(self, avail_card_len_B):
        self._error = c_avaiil_card_len_B = c_int32(avail_card_len_B)
        spcm_dwSetParam_i32(self._card, SPC_DATA_AVAIL_CARD_LEN, c_avaiil_card_len_B)
        self.processed_data_B = self.processed_data_B + avail_card_len_B
        return

    @check_card_error
    def get_trig_counter(self):
        c_trig_counter = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_TRIGGERCOUNTER, byref(c_trig_counter))
        self.trig_counter = c_trig_counter.value
        return self.trig_counter

    def get_trig_reps(self):
        trig_counter = self.get_trig_counter()
        return int(trig_counter / self._no_of_gates)


    @check_card_error
    def get_bits_per_sample(self):
        c_bits_per_sample = c_int32(0)
        self._error = spcm_dwGetParam_i32(self._card, SPC_MIINST_BITSPERSAMPLE, byref(c_bits_per_sample))
        return c_bits_per_sample.value

class Ts_buffer_command():

    def __init__(self, card):
        self._card = card

    def init_ts_params(self):
        self.ts_avail_user_pos_B = 0
        self.ts_avail_user_len_B = 0
        self.ts_avail_card_len_B = 0

    def check_ts_params(self):
        self.ts_avail_user_pos_B = self.get_ts_avail_user_pos_B()
        self.ts_avail_user_len_B = self.get_ts_avail_user_len_B()
        print('ts Pos:{} Avail:{} '.format(self.ts_avail_user_pos_B,
                                           self.ts_avail_user_len_B))

    def reset_ts_counter(self):
        spcm_dwSetParam_i32(self._card, SPC_TIMESTAMP_CMD, SPC_TS_RESET)

    def start_extra_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_EXTRA_STARTDMA)

    def wait_extra_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_EXTRA_WAITDMA)

    def stop_extra_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_EXTRA_STOPDMA)

    def poll_extra_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_EXTRA_POLL)

    def get_gate_len_alignment(self):
        c_gate_len_alignment = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_GATE_LEN_ALIGNMENT, byref(c_gate_len_alignment))
        self.gate_len_alignment = c_gate_len_alignment.value
        return self.gate_len_alignment

    def get_ts_avail_user_len_B(self):
        c_ts_avaiil_user_len = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_TS_AVAIL_USER_LEN, byref(c_ts_avaiil_user_len))
        self.ts_avail_user_len_B = c_ts_avaiil_user_len.value
        return self.ts_avail_user_len_B

    def get_ts_avail_user_pos_B(self):
        c_ts_avaiil_user_pos = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_TS_AVAIL_USER_POS, byref(c_ts_avaiil_user_pos))
        self.ts_avail_user_pos_B = c_ts_avaiil_user_pos.value
        return self.ts_avail_user_pos_B

    def get_ts_avail_card_len_B(self):
        c_ts_avaiil_card_len_B = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_TS_AVAIL_CARD_LEN, byref(c_ts_avaiil_card_len_B))
        self.ts_avail_card_len_B = c_ts_avaiil_card_len_B.value
        return self.ts_avail_card_len_B

    def set_ts_avail_card_len_B(self, ts_avail_card_len_B):
        c_ts_avaiil_card_len_B = c_int64(ts_avail_card_len_B)
        self._error = spcm_dwSetParam_i64(self._card, SPC_TS_AVAIL_CARD_LEN, c_ts_avaiil_card_len_B)
        return


    def get_timestamp_command(self):
        c_ts_timestamp_command = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_TIMESTAMP_CMD, byref(c_ts_timestamp_command))
        self.ts_cmd = c_ts_timestamp_command.value
        return self.ts_cmd

class SpectrumInstrumentation(Base, FastCounterInterface):

    '''
    Hardware class for the spectrum instrumentation card
    Analog Inputs
    trigger_mode:
        'EXT' (External trigger),
        'SW' (Software trigger),
        'CH0' (Channel0 trigger)
    acquistion_mode:
        'STD_SINGLE'
        'STD_MULTI'
        'FIFO_SINGLE',
        'FIFO_GATE',
        'FIFO_MULTI',
        'FIFO_AVERAGE'

    Config example:

    si:
        module.Class: 'Fast_ADC.spectrum_instrumentation_ver5.SpectrumInstrumentationTest'
        ai_range_mV: 2000
        ai_offset_mV: 0
        ai_termination: '50Ohm'
        ai_coupling: 'DC'
        acq_mode: 'FIFO_GATE'
        acq_HW_avg_num: 1
        acq_pre_trigger_samples: 16
        acq_post_trigger_samples: 16
        buf_notify_size_B: 4096
        clk_reference_Hz: 10e6
        trig_mode: 'EXT'
        trig_level_mV: 1000
        gated: True
        initial_buffer_size_S: 1e9
        repetitions: 0
        row_data_save: False
    '''

    _modtype = 'SpectrumCard'
    _modclass = 'hardware'

    _ai_range_mV = ConfigOption('ai_range_mV', 1000, missing='warn')
    _ai_offset_mV = ConfigOption('ai_offset_mV', 0, missing='warn')
    _ai_term = ConfigOption('ai_termination', '50Ohm', missing='warn')
    _ai_coupling = ConfigOption('ai_coupling', 'DC', missing='warn')
    _acq_mode = ConfigOption('acq_mode', 'FIFO_MULTI', missing='warn')
    _acq_HW_avg_num = ConfigOption('acq_HW_avg_num', 1, missing='nothing')
    _acq_pre_trigs_S = ConfigOption('acq_pre_trigger_samples', 16, missing='warn')
    _acq_post_trigs_S = ConfigOption('acq_post_trigger_samples', 16, missing='nothing')

    _buf_notify_size_B = ConfigOption('buf_notify_size_B', 4096, missing='warn')
    _clk_ref_Hz = ConfigOption('clk_reference_Hz', 10e6, missing='warn')
    _trig_mode = ConfigOption('trig_mode', 'EXT', missing='warn')
    _trig_level_mV = ConfigOption('trig_level_mV', '1000', missing='warn')

    _gated = ConfigOption('gated', False, missing='warn')
    _init_buf_size_S = ConfigOption('initial_buffer_size_S', 1e9, missing='warn')
    _reps = ConfigOption('repetitions', 0, missing='nothing')

    _check_buffer = False
    _row_data_save = ConfigOption('row_data_save', True, missing='nothing')
    _path_for_buffer_check = ConfigOption('path_for_buffer_check', 'C:',missing='nothing')
    _reps_for_buffer_check = ConfigOption('repititions_for_buffer_check', 1, missing='nothing')

    _cfg_error_check = False
    _ccmd_error_check = False
    _dcmd_error_check = False


    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._card_on = False
        self._internal_status = CardStatus.idle

    def _load_settings_from_config_file(self):
        self.cs.ai_range_mV = int(self._ai_range_mV)
        self.cs.ai_offset_mV = int(self._ai_offset_mV)
        self.cs.ai_term = self._ai_term
        self.cs.ai_coupling = self._ai_coupling
        self.cs.acq_mode = self._acq_mode
        self.cs.acq_HW_avg_num = int(self._acq_HW_avg_num)
        self.cs.acq_pre_trigs_S = int(self._acq_pre_trigs_S)
        self.cs.acq_post_trigs_S = int(self._acq_post_trigs_S)
        self.cs.buf_notify_size_B = int(self._buf_notify_size_B)
        self.cs.clk_ref_Hz = int(self._clk_ref_Hz)
        self.cs.trig_mode = self._trig_mode
        self.cs.trig_level_mV = int(self._trig_level_mV)

        self.ms.gated = self._gated
        self.ms.init_buf_size_S = int(self._init_buf_size_S)
        self.ms.reps = self._reps
        self.ms.assign_data_bit(self.cs.acq_mode)

    def on_activate(self):
        """
        Open the card by activation of the module
        """

        if self._gated == True:
            self.cs = Card_settings_gated()
            self.ms = Measurement_settings_gated()
        else:
            self.cs = Card_settings()
            self.ms = Measurement_settings()

        self._load_settings_from_config_file()
        self.pl = Process_loop()
        self.cfg = Configure_command()
        self.cfg.error_check =self._cfg_error_check
        self.pl.row_data_save = self._row_data_save

        if self._card_on == False:
            self.cs.card = spcm_hOpen(create_string_buffer(b'/dev/spcm0'))
            self._card_on = True
            self.ccmd = Card_command(self.cs.card)
            self.ccmd.error_check = self._ccmd_error_check

        else:
            self.log.info('SI card is already on')

        if self.cs.card == None:
            self.log.info('No card found')

    def on_deactivate(self):
        """
        Close the card
        """
        spcm_vClose(self.cs.card)

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
        self.ccmd.card_reset()
        self.cfg.load_static_cfg_params(self.cs, self.ms)

        self.ms.load_dynamic_params(binwidth_s, record_length_s, number_of_gates)
        self.cs.calc_dynamic_cs(self.ms)
        self.ms.calc_data_size_S(self.cs.acq_pre_trigs_S, self.cs.acq_post_trigs_S, self.cs.acq_seg_size_S)
        self.ms.calc_buf_params()
        self.ms.calc_actual_length_s()
        self.cs.get_buf_size_B(self.ms.seq_size_B, self.ms.reps_per_buf)

        self.cfg.load_dynamic_cfg_params(self.cs, self.ms)

        self.cfg.configure_all()

        self.ms.c_buf_ptr = self.cfg.return_c_buf_ptr()
        if self.ms.gated == True:
            self.ms.c_ts_buf_ptr = self.cfg.return_c_ts_buf_ptr()

        self.pl.init_process(self.cs, self.ms)
        self.pl.cp.dcmd.error_check = self._dcmd_error_check

        return self.ms.binwidth_s, self.ms.record_length_s, self.ms.number_of_gates

#        return self.ms.binwidth_s, self.ms.actual_length_s, self.ms.number_of_gates

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
        if self._internal_status == CardStatus.idle:
            self.pl.init_measure_params()
            self.ccmd.start_all()
            self.ccmd.wait_DMA()
            if self.ms.gated == True:
                self.pl.cp.tscmd.wait_extra_dma()

        if self._internal_status == CardStatus.paused:
            self.ccmd.enable_trigger()

        self.pl.cp.trigger_enabled = True
        self.pl.start_data_process()

        self.log.info('Measurement started')
        self._internal_status = CardStatus.running


        return 0

    def _start_card(self):
        self._internal_status = CardStatus.running

    def get_data_trace(self):
        """ Polls the current timetrace data from the fast counter.

        Return value is a numpy array (dtype = int64).
        The binning, specified by calling configure() in forehand, must be
        taken care of in this hardware class. A possible overflow of the
        histogram bins must be caught here and taken care of.
        If the counter is NOT GATED it will return a tuple (1D-numpy-array, info_dict) with
            returnarray[timebin_index]
        If the counter is GATED it will return a tuple (2D-numpy-array, info_dict) with
            returnarray[gate_index, timebin_index]

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds

        If the hardware does not support these features, the values should be None
        """
        #self.pl.fetch_on = True
        avg_data, avg_num = self.pl.fetch_data_trace()
        #self.pl.fetch_on = False
        info_dict = {'elapsed_sweeps': avg_num, 'elapsed_time': time.time() - self.pl.start_time}

        return avg_data, info_dict

    def stop_measure(self):
        if self._internal_status == CardStatus.running:
            self.log.info('card stopped')
            self.pl.stop_data_process()
            self.ccmd.disable_trigger()
            self.ccmd.stop_dma()
            self.ccmd.card_stop()

        self._internal_status = CardStatus.idle
        self.pl.loop_on = False
        self.log.info('Measurement stopped')

        return 0

    def pause_measure(self):
        """ Pauses the current measurement.

            Fast counter must be initially in the run state to make it pause.
        """
        self.ccmd.disable_trigger()
        self.pl.cp.trigger_enabled = False
        self.pl.loop_on = False
        self.pl.stop_data_process()

        self._internal_status = CardStatus.paused
        self.log.info('Measurement paused')
        return


    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        self.log.info('Measurement continued')
        self._internal_status = CardStatus.running
        self.pl.loop_on = True
        self.pl.cp.trigger_enabled = self.ccmd.enable_trigger()
        self.pl.start_data_process()

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


class Configure_acquistion_mode():
    def set_acquistion_mode(self, card, acq_mode, pre_trigs_S, post_trigs_S, seg_size_S, seq_size_S, loops, HW_avg_num):

        if acq_mode == 'STD_SINGLE':
            self._mode_STD_SINGLE(card, post_trigs_S, seg_size_S)

        elif acq_mode == 'STD_MULTI':
            self._mode_STD_MULTI(card, post_trigs_S, seg_size_S, loops)

        elif acq_mode == 'FIFO_SINGLE':
            self._mode_FIFO_SINGLE(card, pre_trigs_S, seg_size_S, loops)

        elif acq_mode == 'STD_GATE':
            self._mode_STD_GATE(card, pre_trigs_S, post_trigs_S, seq_size_S, loops)

        elif acq_mode == 'FIFO_GATE':
            self._mode_FIFO_GATE(card, pre_trigs_S, post_trigs_S, loops)

        elif acq_mode == 'FIFO_MULTI':
            self._mode_FIFO_MULTI(card, post_trigs_S, seg_size_S, loops)

        elif acq_mode == 'FIFO_AVERAGE':
            self._mode_FIFO_AVERAGE(card, post_trigs_S, seg_size_S, loops, HW_avg_num)

        else:
            print('error at acquistion mode')

    @check_card_error
    def _mode_STD_SINGLE(self, card, post_trigs_S, seg_size_S):
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_STD_SINGLE)
        spcm_dwSetParam_i32(card, SPC_MEMSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        return

    @check_card_error
    def _mode_STD_MULTI(self, card, post_trigs_S, seg_size_S, loops):
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_STD_MULTI)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        spcm_dwSetParam_i32(card, SPC_MEMSIZE, int(seg_size_S * loops))
        self._error = spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)

        return

    @check_card_error
    def _mode_STD_GATE(self, card, pre_trig_S, post_trigs_S, seq_size_S, loops):
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_STD_GATE)
        spcm_dwSetParam_i32(card, SPC_PRETRIGGER, pre_trig_S)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(card, SPC_MEMSIZE, int(seq_size_S * loops))

        return


    @check_card_error
    def _mode_FIFO_SINGLE(self, card, pre_trigs_S, seg_size_S, loops=1):
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_SINGLE)
        spcm_dwSetParam_i32(card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

    @check_card_error
    def _mode_FIFO_GATE(self, card, pre_trigs_S, post_trigs_S, loops=0):
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_GATE)
        spcm_dwSetParam_i32(card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

    @check_card_error
    def _mode_FIFO_MULTI(self, card, post_trigs_S, seg_size_S, loops=0):
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_MULTI)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

    @check_card_error
    def _mode_FIFO_AVERAGE(self, card, post_trigs_S, seg_size_S, loops=0):#,HW_avg_num):
        max_post_trigs_S = 127984

        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_AVERAGE)
        spcm_dwSetParam_i32(card, SPC_AVERAGES, HW_avg_num)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

class Configure_trigger():
    def set_trigger(self, card, trig_mode, trig_level_mV):
        if trig_mode == 'EXT':
            self._trigger_EXT(card, trig_level_mV)

        elif trig_mode == 'SW':
            self._trigger_SW(card)

        elif trig_mode == 'CH0':
            self._trigger_CH0(card, trig_level_mV)

        else:
            print('error at trigger')

    @check_card_error
    def _trigger_EXT(self, card, trig_level_mV):
        spcm_dwSetParam_i32(card, SPC_TRIG_EXT0_MODE, SPC_TM_POS)
        spcm_dwSetParam_i32(card, SPC_TRIG_EXT0_LEVEL0, trig_level_mV)
        spcm_dwSetParam_i32(card, SPC_TRIG_ORMASK, SPC_TMASK_EXT0)
        self._error = spcm_dwSetParam_i32(card, SPC_TRIG_ANDMASK, 0)

    @check_card_error
    def _trigger_SW(self, card):
        spcm_dwSetParam_i32(card, SPC_TRIG_ORMASK, SPC_TMASK_SOFTWARE)
        self._error = spcm_dwSetParam_i32(card, SPC_TRIG_ANDMASK, 0)

    @check_card_error
    def _trigger_CH0(self, card, trig_level_mV):
        spcm_dwSetParam_i32(card, SPC_TRIG_ORMASK, SPC_TMASK_NONE)
        spcm_dwSetParam_i32(card, SPC_TRIG_CH_ANDMASK0, SPC_TMASK0_CH0)
        spcm_dwSetParam_i32(card, SPC_TRIG_CH0_LEVEL0, trig_level_mV)
        self._error = spcm_dwSetParam_i32(card, SPC_TRIG_CH0_MODE, SPC_TM_POS)





class Configure_data_transfer():
    def configure_data_transfer(self, card, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B):
        c_buf_ptr = self.set_buffer(card, c_buf_ptr, buf_size_B)
        self.set_data_transfer(card, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B)
        return c_buf_ptr

    def set_buffer(self, card, c_buf_ptr, buf_size_B):
        cont_buf_len = self.get_cont_buf_len(card, c_buf_ptr)
        if cont_buf_len > buf_size_B:
            print('Use continuour buffer')
        else:
            c_buf_ptr = pvAllocMemPageAligned(buf_size_B)
            print('User Scatter gather')

        return c_buf_ptr

    def get_cont_buf_len(self, card, c_buf_ptr):
        c_cont_buf_len = uint64(0)
        spcm_dwGetContBuf_i64(card, SPCM_BUF_DATA, byref(c_buf_ptr), byref(c_cont_buf_len))
        return c_cont_buf_len.value

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

    def set_data_transfer(self, card, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B):
        c_buf_offset = uint64(0)
        c_buf_size_B = uint64(buf_size_B)
        spcm_dwDefTransfer_i64(card, buf_type, SPCM_DIR_CARDTOPC,
                               buf_notify_size_B, byref(c_buf_ptr),
                               c_buf_offset, c_buf_size_B
                               )
        return

class Configure_timestamp():

    def configure_ts_standard(self, card):
        self.ts_standard_mode(card)
#        self.ts_internal_clock(card)
        #self.ts_no_additional_timestamp(card)

    def ts_standard_mode(self, card):
        spcm_dwSetParam_i32(card, SPC_TIMESTAMP_CMD, SPC_TSMODE_STARTRESET | SPC_TSCNT_INTERNAL | SPC_TSFEAT_NONE)

    def ts_internal_clock(self, card):
        spcm_dwSetParam_i32(card, SPC_TIMESTAMP_CMD, SPC_TSCNT_INTERNAL)

    def ts_no_additional_timestamp(self, card):
        spcm_dwSetParam_i32(card, SPC_TIMESTAMP_CMD, SPC_TSFEAT_NONE)


class Configure_command(Configure_acquistion_mode, Configure_trigger, Configure_data_transfer, Configure_timestamp):
    '''
    This class contains methods to configure the card.
    '''

    def load_static_cfg_params(self, cs, ms):

        self._gated = ms.gated
        self._card = cs.card

        self._c_buf_ptr = ms.return_c_buf_ptr()
        self._ai_range_mV = cs.ai_range_mV
        self._ai_offset_mV =cs.ai_offset_mV
        self._ai_term = cs.ai_term
        self._ai_coupling = cs.ai_coupling
        self._acq_mode = cs.acq_mode
        self._acq_HW_avg_num = cs.acq_HW_avg_num
        self._acq_pre_trigs_S = cs.acq_pre_trigs_S
        self._acq_loops = cs.acq_loops
        self._buf_notify_size_B = cs.buf_notify_size_B
        self._clk_samplerate_Hz = int(cs.clk_samplerate_Hz)
        self._clk_ref_Hz = int(cs.clk_ref_Hz)
        self._trig_mode = cs.trig_mode
        self._trig_level_mV = cs.trig_level_mV
        if self._gated == True:
            self._c_ts_buf_ptr = ms.return_c_ts_buf_ptr()
            self._ts_buf_notify_size_B = cs.ts_buf_notify_size_B

        self.reg = Configure_register_checker(self._card)


    def load_dynamic_cfg_params(self, cs, ms):
        self._acq_post_trigs_S = cs.acq_post_trigs_S
        self._acq_seg_size_S = cs.acq_seg_size_S
        self._buf_size_B = cs.buf_size_B
        self._acq_seq_size_S = ms.seq_size_S
        if self._gated == True:
            self._ts_buf_size_B = cs.ts_buf_size_B


    def configure_all(self):

        self.set_analog_input_conditions(self._card)
        self.set_acquistion_mode(self._card, self._acq_mode, self._acq_pre_trigs_S, self._acq_post_trigs_S,
                                 self._acq_seg_size_S, self._acq_seq_size_S, self._acq_loops, self._acq_HW_avg_num)
        self.set_sampling_clock(self._card)
        self.set_trigger(self._card, self._trig_mode, self._trig_level_mV)
        self._c_buf_ptr = self.configure_data_transfer(self._card, SPCM_BUF_DATA, self._c_buf_ptr,
                                                       self._buf_size_B, self._buf_notify_size_B)
        if self._gated == True:
            self._c_ts_buf_ptr = self.configure_data_transfer(self._card, SPCM_BUF_TIMESTAMP, self._c_ts_buf_ptr,
                                                              self._ts_buf_size_B, self._ts_buf_notify_size_B)
            self.configure_ts_standard(self._card)

    @check_card_error
    def set_analog_input_conditions(self, card):
        ai_term_dict = {'1Mohm':0, '50Ohm':1}
        ai_coupling_dict = {'DC':0, 'AC':1}
        spcm_dwSetParam_i32(card, SPC_TIMEOUT, 5000)
        spcm_dwSetParam_i32(card, SPC_CHENABLE, CHANNEL0)
        spcm_dwSetParam_i32(card, SPC_AMP0, self._ai_range_mV) # +- 10 V
        spcm_dwSetParam_i32(card, SPC_OFFS0, self._ai_offset_mV)
        spcm_dwSetParam_i32(card, SPC_50OHM0, ai_term_dict[self._ai_term]) # A "1"("0") sets the 50(1M) ohm termination
        self._error = spcm_dwSetParam_i32(card, SPC_ACDC0, ai_coupling_dict[self._ai_coupling])  # A "0"("1") sets he DC(AC)coupling
        return

    @check_card_error
    def set_sampling_clock(self, card):
        spcm_dwSetParam_i32(card, SPC_CLOCKMODE, SPC_CM_INTPLL)
        spcm_dwSetParam_i32(card, SPC_REFERENCECLOCK, self._clk_ref_Hz)
        spcm_dwSetParam_i32(card, SPC_SAMPLERATE, self._clk_samplerate_Hz)
        self._error = spcm_dwSetParam_i32(card, SPC_CLOCKOUT, 1)
        return

    def return_c_buf_ptr(self):
        return self._c_buf_ptr

    def return_c_ts_buf_ptr(self):
        return self._c_ts_buf_ptr

class Configure_register_checker():
    def __init__(self, card):
        self._card = card

    def check_cs_registers(self):
        '''
        This method can be used to check the card settings registered in the card.
        '''
        self.csr = Card_settings()
        self.error_check = True
        self._check_csr_ai()
        self._check_csr_acq()
        self._check_csr_clk()
        self._check_csr_trig()

    @check_card_error
    def _check_csr_ai(self):
        ai_term_dict = {0:'1Mohm', 1:'50Ohm'}
        ai_coupling_dict = {0:'DC', 1:'AC'}

        c_ai_range_mV = c_int32()
        c_ai_offset_mV = c_int32()
        c_ai_term = c_int32()
        c_ai_coupling = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_AMP0, byref(c_ai_range_mV)) # +- 10 V
        spcm_dwGetParam_i32(self._card, SPC_OFFS0, byref(c_ai_offset_mV))
        spcm_dwGetParam_i32(self._card, SPC_50OHM0, byref(c_ai_term))
        self._error = spcm_dwGetParam_i32(self._card, SPC_ACDC0, byref(c_ai_coupling))
        self.csr.ai_range_mV = int(c_ai_range_mV.value)
        self.csr.ai_offset_mV = int(c_ai_offset_mV.value)
        self.csr.ai_term = ai_term_dict[c_ai_term.value]
        self.csr.ai_coupling = ai_coupling_dict[c_ai_coupling.value]

    @check_card_error
    def _check_csr_acq(self):
        c_acq_mode = c_int32()
        c_acq_HW_avg_num = c_int32()
        c_acq_pre_trigs_S = c_int32()
        c_acq_post_trigs_S = c_int32()
        c_acq_mem_size_S = c_int32()
        c_acq_seg_size_S = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_CARDMODE, byref(c_acq_mode))
        spcm_dwGetParam_i32(self._card, SPC_AVERAGES, byref(c_acq_HW_avg_num))
        spcm_dwGetParam_i32(self._card, SPC_PRETRIGGER, byref(c_acq_pre_trigs_S))
        spcm_dwGetParam_i32(self._card, SPC_POSTTRIGGER, byref(c_acq_post_trigs_S))
        spcm_dwGetParam_i32(self._card, SPC_MEMSIZE, byref(c_acq_mem_size_S))
        self._error = spcm_dwGetParam_i32(self._card, SPC_SEGMENTSIZE, byref(c_acq_seg_size_S))
        self.csr.acq_mode = c_acq_mode.value
        self.csr.acq_HW_avg_num = int(c_acq_HW_avg_num.value)
        self.csr.acq_pre_trigs_S = int(c_acq_pre_trigs_S.value)
        self.csr.acq_post_trigs_S = int(c_acq_post_trigs_S.value)
        self.csr.acq_mem_size_S = int(c_acq_mem_size_S.value)
        self.csr.acq_seg_size_S = int(c_acq_seg_size_S.value)

    @check_card_error
    def _check_csr_clk(self):
        c_clk_samplerate_Hz = c_int32()
        c_clk_ref_Hz = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_REFERENCECLOCK, byref(c_clk_ref_Hz))
        self._error = spcm_dwGetParam_i32(self._card, SPC_SAMPLERATE, byref(c_clk_samplerate_Hz))
        self.csr.clk_samplerate_Hz = int(c_clk_samplerate_Hz.value)
        self.csr.clk_ref_Hz = int(c_clk_ref_Hz.value)

    @check_card_error
    def _check_csr_trig(self):
        c_trig_mode = c_int32()
        c_trig_level_mV = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_TRIG_EXT0_MODE, byref(c_trig_mode))
        self._error = spcm_dwGetParam_i32(self._card, SPC_TRIG_EXT0_LEVEL0, byref(c_trig_level_mV))
        self.csr.trig_mode = c_trig_mode.value
        self.csr.trig_level_mV = int(c_trig_level_mV.value)

class Data_transfer:

    def __init__(self, c_buf_ptr, data_type, data_bytes_B, seq_size_S, reps_per_buf):
        self.c_buf_ptr = c_buf_ptr
        self.data_type = data_type
        self.data_bytes_B = data_bytes_B
        self.seq_size_S = seq_size_S
        self.seq_size_B = seq_size_S * self.data_bytes_B
        self.reps_per_buf = reps_per_buf

    def _cast_buf_ptr(self, user_pos_B):
        c_buffer = cast(addressof(self.c_buf_ptr) + user_pos_B, POINTER(self.data_type))
        return c_buffer

    def _asnparray(self, c_buffer, shape):
        np_buffer = np.ctypeslib.as_array(c_buffer, shape=shape)
        return np_buffer

    def _fetch_from_buf(self, user_pos_B, sample_S):
        shape = (sample_S,)
        c_buffer = self._cast_buf_ptr(user_pos_B)
        np_buffer = self._asnparray(c_buffer, shape)
        return np_buffer

    def get_new_data(self, user_pos_B, curr_avail_reps):
        rep_end = int(user_pos_B / self.seq_size_B) + curr_avail_reps

        if 0 < rep_end <= self.reps_per_buf:
            np_data = self._fetch_data(user_pos_B, curr_avail_reps)

        elif self.reps_per_buf < rep_end < 2 * self.reps_per_buf:
            np_data = self._fetch_data_buf_end(user_pos_B, curr_avail_reps)
        else:
            print('error: rep_end {} is out of range'.format(rep_end))
            return

        return np_data

    def _fetch_data(self, user_pos_B, curr_avail_reps):
        np_data = self._fetch_from_buf(user_pos_B, curr_avail_reps * self.seq_size_S)
        return np_data

    def _fetch_data_buf_end(self, user_pos_B, curr_avail_reps):
        start_rep = int((user_pos_B / self.seq_size_B) + 1)
        reps_tail = self.reps_per_buf - (start_rep - 1)
        reps_head = curr_avail_reps - reps_tail

        np_data_tail = self._fetch_data(user_pos_B, reps_tail)
        np_data_head = self._fetch_data(0, reps_head)
        np_data = np.append(np_data_tail, np_data_head)

        return np_data


class Data_fetch_ungated():

    def __init__(self, cs, ms):
        self.ms = ms
        self.cs = cs
    def init_data_fetch(self):
        self.input_params(self.cs, self.ms)
        self.create_data_trsnsfer()

    def input_params(self, cs, ms):
        self.c_buf_ptr = ms.c_buf_ptr
        self.data_type = ms.get_data_type()
        self.data_bytes_B = ms.get_data_bytes_B()
        self.seq_size_S = ms.seq_size_S
        self.seq_size_B = self.seq_size_S * self.data_bytes_B

    def create_data_trsnsfer(self):
        self.hw_dt = Data_transfer(self.c_buf_ptr, self.data_type,
                                   self.data_bytes_B, self.seq_size_S, self.ms.reps_per_buf)

    def fetch_data(self, user_pos_B, curr_avail_reps):
        #user_pos_B = self.dpcmd.get_avail_user_pos_B()
        data = self.hw_dt.get_new_data(user_pos_B, curr_avail_reps)
        rep = curr_avail_reps
        return data, rep

class Data_fetch_gated(Data_fetch_ungated):
    def input_params(self, cs, ms):
        super().input_params(cs, ms)
        self.c_ts_buf_ptr = ms.c_ts_buf_ptr
        self.ts_data_type = ms.get_ts_data_type()
        self.ts_data_bytes_B = ms.ts_data_bytes_B
        self.ts_seq_size_S = ms.ts_seq_size_S

    def create_data_trsnsfer(self):
        super().create_data_trsnsfer()
        self.ts_dt = Data_transfer(self.c_ts_buf_ptr, self.ts_data_type,
                                   self.ts_data_bytes_B, self.ts_seq_size_S, self.ms.reps_per_buf * 100)

    def fetch_ts_data(self, ts_user_pos_B, curr_avail_reps):
        ts_row = self.ts_dt.get_new_data(ts_user_pos_B, curr_avail_reps)
        ts_r, ts_f = self._get_ts_rf(ts_row)
        return ts_r, ts_f

    def _get_ts_rf(self, ts_row):
        ts_used = ts_row[::2] #odd data is always filled with zero
        ts_r = ts_used[::2]
        ts_f = ts_used[1::2]
        return ts_r, ts_f

class Data_process_ungated():

    def init_data_process(self, cs, ms):
        self._input_settings_to_dp(cs, ms)
        self._generate_data_cls()
        self.df.init_data_fetch()
        self.avg = AvgData()
        self.avg.num = 0
        self.avg.pulse = ms.number_of_gates
        self.avg.data = np.empty(ms.seq_size_S)

    def _input_settings_to_dp(self, cs, ms):
        self.ms = ms
        self.cs = cs

    def _generate_data_cls(self):
        self.dc = SeqDataMulti()
        self.dc.data = np.empty(self.ms.seq_size_S)
        self.df = Data_fetch_ungated(self.cs, self.ms)

    def create_dc_new(self):
        self.dc_new = SeqDataMulti()
        self.dc_new.pulse = self.ms.number_of_gates
        self.dc_new.pule_len = self.ms.seg_size_S

    def fetch_data_to_dc(self, user_pos_B, curr_avail_reps):
        self.dc_new.data, self.dc_new.rep = self.df.fetch_data(user_pos_B, curr_avail_reps)
        self.dc_new.set_len()
        self.dc_new.data = self.dc_new.reshape_2d_by_rep()

    def stack_new_data(self):
        self.dc.stack_rep(self.dc_new)

    def get_new_avg_data(self):
        self.new_avg = AvgData()
        self.new_avg.num, self.new_avg.data = self.dc_new.avgdata()

    def update_avg_data(self):
        self.avg.update(self.new_avg)
        self.avg.set_len()

    def return_avg_data(self):
        return self.avg.data, self.avg.num

class Data_process_gated(Data_process_ungated):

    def _generate_data_cls(self):
        self.dc = SeqDataMultiGated()
        self.dc.data = np.empty((0, self.ms.seq_size_S), int)
        self.dc.ts_r = np.empty(0, int)
        self.dc.ts_f = np.empty(0, int)
        self.df = Data_fetch_gated(self.cs, self.ms)

    def create_dc_new(self):
        self.dc_new = SeqDataMultiGated()
        self.dc_new.pulse = self.ms.number_of_gates
        self.dc_new.pule_len = self.ms.seg_size_S

    def fetch_ts_data_to_dc(self, ts_user_pos_B, curr_avail_reps):
        self.dc_new.ts_r, self.dc_new.ts_f = self.df.fetch_ts_data(ts_user_pos_B, curr_avail_reps)

    def return_avg_data(self):
        avg_data_2d = self.avg.reshape_2d_by_pulse()
        return avg_data_2d, self.avg.num

class Card_process():

    def init_card_process(self, cs, ms):
        self._input_settings_to_cp(cs, ms)
        self._generate_buffer_command()

    def _input_settings_to_cp(self, cs, ms):
        self.cs = cs
        self.ms = ms

    def _generate_buffer_command(self):
        self.dcmd = Data_buffer_command(self.cs.card, self.ms)
        self.tscmd = Ts_buffer_command(self.cs.card)

    def toggle_trigger(self, trigger_on):
        if trigger_on == self.trigger_enabled:
            return
        else:
            if trigger_on == True:
                self.trigger_enabled = self.dcmd.enable_trigger()
            elif trigger_on == False:
                self.trigger_enabled = self.dcmd.disable_trigger()

    def wait_new_trigger(self, wait_trig_on):
        if wait_trig_on == True:
            prev_trig_counts = self.dcmd.trig_counter
            curr_trig_counts = self.dcmd.get_trig_counter()
            if curr_trig_counts == prev_trig_counts:
                time.sleep(1e-3)

            return curr_trig_counts

    def _wait_new_avail_reps(self):
        curr_avail_reps = self.dcmd.get_avail_user_reps()
        while curr_avail_reps == 0:
            curr_avail_reps = self.dcmd.get_avail_user_reps()

        return curr_avail_reps

class Process_commander:
    '''
    This class contains the command to be executed in a single loop body.
    '''

    def init_process(self, cs, ms):
        self._input_settings_to_dpc(ms)
        self._create_process_cls()
        self.dp.init_data_process(cs, ms)
        self.cp.init_card_process(cs, ms)

    def _create_process_cls(self):
        if self._gated == True:
            self.dp = Data_process_gated()
        else:
            self.dp = Data_process_ungated()

        self.cp = Card_process()

    def _input_settings_to_dpc(self, ms):
        self._gated = ms.gated
        self._reps_per_buf = ms.reps_per_buf
        self._seq_size_B = ms.seq_size_B
        self._ts_seq_size_B = ms.ts_seq_size_B

    def command_process(self):
        trig_reps = self.cp.dcmd.get_trig_reps()
        unprocessed_reps = trig_reps - self.dp.avg.num
        if trig_reps == 0 or unprocessed_reps == 0:
            self.trigger_on = True
            self.wait_trigger_on = True
            self.data_process_on = False

        elif unprocessed_reps < 2 * self._reps_per_buf:
            self.trigger_on = True
            self.wait_trigger_on = False
            self.data_process_on = True

        elif unprocessed_reps >= 2 * self._reps_per_buf:
            self.trigger_on = False
            self.wait_trigger_on = False
            self.data_process_on = True

        self.cp.toggle_trigger(self.trigger_on)
        self.cp.wait_new_trigger(self.wait_trigger_on)
        self._process_data(self.data_process_on)

    def _process_data(self, data_process_on):
        if data_process_on == True:
            curr_avail_reps = self.cp.dcmd.get_avail_user_reps()
            if curr_avail_reps == 0:
                return

            self.dp.create_dc_new()

            user_pos_B = self.cp.dcmd.get_avail_user_pos_B()
            self.dp.fetch_data_to_dc(user_pos_B, curr_avail_reps)
            if self.dp.dc_new.rep == 0:
                return

            if self.row_data_save == True:
                self.dp.stack_new_data()

            self._average_data(curr_avail_reps)

            if self._gated == True:
                self._fetch_ts(curr_avail_reps)


    def _average_data(self, curr_avail_reps):
        self.dp.get_new_avg_data()
        self.dp.update_avg_data()
        self.cp.dcmd.set_avail_card_len_B(curr_avail_reps * self._seq_size_B)

    def _fetch_ts(self, curr_avail_reps):
        ts_user_pos_B = self.cp.tscmd.get_ts_avail_user_pos_B()
        self.dp.fetch_ts_data_to_dc(ts_user_pos_B, curr_avail_reps)
        self.cp.tscmd.set_ts_avail_card_len_B(curr_avail_reps * self._ts_seq_size_B)


class Process_loop(Process_commander):
    '''
    This is the main data process loop class.
    '''

    def init_measure_params(self):
        self.cp.dcmd.init_dp_params()
        self.start_time = time.time()
        self.loop_on = True
        self.fetch_on = False

    def start_data_process(self):
        self.data_proc_th = threading.Thread(target=self.start_data_process_loop)
        self.data_proc_th.start()

        return

    def stop_data_process(self):

        self.loop_on = False
        self.data_proc_th.join()

    def start_data_process_loop(self):

        while self.loop_on == True:
            if self.fetch_on == False:
                self.command_process()
            elif self.fetch_on == True:
                print('fetching')
                time.sleep(1e-3)
            else:
                print('error on loop')

        return

    def start_data_process_loop_n(self, n):

        while self.dp.avg_num <= n:
            if self.fetch_on == False:
                self.command_process()
            elif self.fetch_on == True:
                print('fetching')
                time.sleep(1)

            else:
                print('error on loop')

        return

    def fetch_data_trace(self):
        self.fetch_on = True
        avg_data, avg_num = self.dp.return_avg_data()
        self.fetch_on = False

        return avg_data, avg_num
