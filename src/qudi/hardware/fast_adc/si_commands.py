# -*- coding: utf-8 -*-

"""
This file contains command classes used for spectrum instrumentation fast counting devices.

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
import time
import numpy as np
import inspect

from pyspcm import *
from spcm_tools import *

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

def benchmark(func):
    def wrapper(self, *args, **kwargs):
        time0 = time.time()
        ret = func(self, *args, **kwargs)
        print('{} took {}s'.format(func.__name__, time.time()-time0))
        return ret
    return wrapper

class Card_command():
    '''
    This class contains the methods which wrap the commands to control the SI card.
    '''

    def __init__(self, card):
        self._card = card
        self.error_check = False

    def start_all(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER
                            | M2CMD_DATA_STARTDMA)

    def start_all_with_extradma(self):
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
    def wait_dma(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_WAITDMA)


class Data_buffer_command(Card_command):
    '''
    This class contains the methods which wrap the commands to control the data buffer handling.
    '''
    _dp_check = True
    _error_check = False

    def __init__(self, card, ms):
        self._card = card
        self._seq_size_B = ms.seq_size_B
        self._total_gate = ms.total_gate
        self._assign_get_trig_reps(ms.gated)
        self.init_dp_params()

    def _assign_get_trig_reps(self, gated):
        if gated:
            self.get_trig_reps = self._get_trig_reps_gated
        else:
            self.get_trig_reps = self._get_trig_reps_ungated

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

    def _get_trig_reps_ungated(self):
        return int(self.get_trig_counter())

    def _get_trig_reps_gated(self):
        return int(self.get_trig_counter() / self._total_gate)

    @check_card_error
    def get_bits_per_sample(self):
        c_bits_per_sample = c_int32(0)
        self._error = spcm_dwGetParam_i32(self._card, SPC_MIINST_BITSPERSAMPLE, byref(c_bits_per_sample))
        return c_bits_per_sample.value

class Ts_buffer_command():
    '''
    This class contains the methods which wrap the commands to control the timestamp buffer handling.
    '''

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

    def get_ts_avail_user_reps(self, ts_seq_size_B):
        return int(self.get_ts_avail_user_len_B() / ts_seq_size_B)

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

class Configure_acquisition_mode():

    def set_STD_trigger_mode(self, card, acq_mode, post_trig_S, seg_size_S, mem_size_S):
        if acq_mode == 'STD_SINGLE':
            mem_size_S = post_trig_S
            self._mode_STD_SINGLE(card, post_trigs_S, mem_size_S)

        elif acq_mode == 'STD_MULTI':
            self._mode_STD_MULTI(card, post_trigs_S, seg_size_S, mem_size_S)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def set_STD_gate_mode(self, card, acq_mode, pre_trigs_S, post_trigs_S, mem_size_S):
        if acq_mode == 'STD_GATE':
            self._mode_STD_GATE(card, pre_trigs_S, post_trigs_S, mem_size_S)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def set_FIFO_trigger_mode(self, card, acq_mode, pre_trigs_S, post_trigs_S, seg_size_S, loops, HW_avg_num=0):
        if acq_mode == 'FIFO_SINGLE':
            self._mode_FIFO_SINGLE(card, pre_trigs_S, seg_size_S, loops)

        elif acq_mode == 'FIFO_MULTI':
            self._mode_FIFO_MULTI(card, post_trigs_S, seg_size_S, loops)

        elif acq_mode == 'FIFO_AVERAGE':
            self._mode_FIFO_AVERAGE(card, post_trigs_S, seg_size_S, loops, HW_avg_num)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def set_FIFO_gate_mode(self, card, acq_mode, pre_trigs_S, post_trigs_S, loops):

        if acq_mode == 'FIFO_GATE':
            self._mode_FIFO_GATE(card, pre_trigs_S, post_trigs_S, loops)

        else:
            raise ValueError('The used acquistion mode is not defined')

    @check_card_error
    def _mode_STD_SINGLE(self, card, post_trigs_S, memsize_S):
        """
        In this mode, pre trigger = memsize - post trigger.
        @params str card: handle of the card
        @params int post_trig_S: the number of samples to be recorded after the trigger event has been detected.
        @params int memsize_S: the total number of samples to be recorded
        """
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_STD_SINGLE)
        spcm_dwSetParam_i32(card, SPC_MEMSIZE, memsize_S)
        self._error = spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        return

    @check_card_error
    def _mode_STD_MULTI(self, card, post_trigs_S, seg_size_S, mem_size_S):
        """
        SEGMENTSIZE is the numbe of samples recorded after detection of one trigger
        including the pre trigger.
        MEMSIZE defines the total number of samples to be recorded per channel.

        @params str card: handle ofthe card
        @params int post_trig_S:
        @params int seg_size_S:
        @params int reps: The number of repetitions.
        """
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_STD_MULTI)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        spcm_dwSetParam_i32(card, SPC_MEMSIZE, mem_size_S)
        self._error = spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)

        return

    @check_card_error
    def _mode_STD_GATE(self, card, pre_trigs_S, post_trigs_S, mem_size_S):
        """
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int post_trigs_S: the number of samples to be recorded after the gate end
        @params int mem_size_S: the total number of samples to be recorded
        """
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_STD_GATE)
        spcm_dwSetParam_i32(card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(card, SPC_MEMSIZE, mem_size_S)
        return

    @check_card_error
    def _mode_FIFO_SINGLE(self, card, pre_trigs_S, seg_size_S, loops=1):
        """
        SEGMENTSIZE is the numbe of samples recorded after detection of one trigger
        including the pre trigger.

        @params str card: handle ofthe card
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int seg_size_S: the numbe of samples recorded after detection of one trigger
                                including the pre trigger.
        @params int loops: the total number of loops
        """

        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_SINGLE)
        spcm_dwSetParam_i32(card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

    @check_card_error
    def _mode_FIFO_MULTI(self, card, post_trigs_S, seg_size_S, loops=0):
        """
        SEGMENTSIZE is the numbe of samples recorded after detection of one trigger
        including the pre trigger.

        @params str card: handle ofthe card
        @params int pre_trigs_S: the number of samples to be recorded after the gate start
        @params int seg_size_S: the numbe of samples recorded after detection of one trigger
                                including the pre trigger.
        @params int loops: the total number of loops
        """
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_MULTI)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

    @check_card_error
    def _mode_FIFO_AVERAGE(self, card, post_trigs_S, seg_size_S, loops, HW_avg_num):
        max_post_trigs_S = 127984

        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_AVERAGE)
        spcm_dwSetParam_i32(card, SPC_AVERAGES, HW_avg_num)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        spcm_dwSetParam_i32(card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return

    @check_card_error
    def _mode_FIFO_GATE(self, card, pre_trigs_S, post_trigs_S, loops):
        """
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int post_trigs_S: the number of samples to be recorded after the gate end
        @params int loops: the total number of loops
        """
        spcm_dwSetParam_i32(card, SPC_CARDMODE, SPC_REC_FIFO_GATE)
        spcm_dwSetParam_i32(card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(card, SPC_LOOPS, loops)
        return


class Configure_trigger():
    ''''
    This class configures the trigger modes and the input parameters accordingly.
    '''
    def set_trigger(self, card, trig_mode, trig_level_mV):
        """
        set the trigger settings.
        @param str card: the handle of the card
        @param str trig_mode: trigger mode
        @param int trig_level_mV: the voltage level for triggering in mV
        """
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
        spcm_dwSetParam_i32(card, SPC_TRIG_TERM, 0)
        spcm_dwSetParam_i32(card, SPC_TRIG_EXT0_ACDC, 0)
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
    '''
    This class configures the transfer buffer dependent on the buffer type specified in the argument.
    '''
    def configure_data_transfer(self, card, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B):
        """
        Configure the data transfer buffer

        @param str card: handle of the card
        @param str buf_type: register of data or timestamp buffer
        @param c_buf_ptr: ctypes pointer for the buffer
        @param int buf_size_B: length of the buffer size in bytes
        @param int buf_notify_size_B: length of the notify size of the buffer in bytes

        @return c_buf_ptr:
        """
        c_buf_ptr = self.set_buffer(card, c_buf_ptr, buf_size_B)
        self.set_data_transfer(card, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B)
        return c_buf_ptr

    def set_buffer(self, card, c_buf_ptr, buf_size_B):
        """
        Set the continuous buffer if possible. See the documentation for the details.
        """
        cont_buf_len = self.get_cont_buf_len(card, c_buf_ptr)
        if cont_buf_len > buf_size_B:
            print('Use continuous buffer')
        else:
            c_buf_ptr = pvAllocMemPageAligned(buf_size_B)
#            print('User Scatter gather')

        return c_buf_ptr

    def get_cont_buf_len(self, card, c_buf_ptr):
        """
        Get length of the continuous buffer set in the card.
        Check also the Spectrum Control Center.

        @param str card: handle of the card
        @param c_buf_ptr: ctypes pointer for the buffer

        @return int: length of the available continuous buffer
        """
        c_cont_buf_len = uint64(0)
        spcm_dwGetContBuf_i64(card, SPCM_BUF_DATA, byref(c_buf_ptr), byref(c_cont_buf_len))
        return c_cont_buf_len.value

    def pvAllocMemPageAligned(self, qwBytes):
        """
        Taken from the example
        """
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
        """
       set the data transfer buffer

        @param str card: handle of the card
        @param str buf_type: register of data or timestamp buffer
        @param c_buf_ptr: ctypes pointer for the buffer
        @param int buf_size_B: length of the buffer size in bytes
        @param int buf_notify_size_B: length of the notify size of the buffer in bytes
        """

        c_buf_offset = uint64(0)
        c_buf_size_B = uint64(buf_size_B)
        spcm_dwDefTransfer_i64(card,
                               buf_type,
                               SPCM_DIR_CARDTOPC,
                               buf_notify_size_B,
                               byref(c_buf_ptr),
                               c_buf_offset,
                               c_buf_size_B
                               )
        return

class Configure_timestamp():
    '''
    This class configures the timestamp mode.
    '''

    def configure_ts_standard(self, card):
        self.ts_standard_mode(card)

    def ts_standard_mode(self, card):
        spcm_dwSetParam_i32(card, SPC_TIMESTAMP_CMD, SPC_TSMODE_STANDARD | SPC_TSCNT_INTERNAL | SPC_TSFEAT_NONE)

    def ts_internal_clock(self, card):
        spcm_dwSetParam_i32(card, SPC_TIMESTAMP_CMD, SPC_TSCNT_INTERNAL)

    def ts_no_additional_timestamp(self, card):
        spcm_dwSetParam_i32(card, SPC_TIMESTAMP_CMD, SPC_TSFEAT_NONE)


class Configure_command(Configure_acquisition_mode, Configure_trigger, Configure_data_transfer, Configure_timestamp):
    '''
    This class inherets the configure classes above and configures all the card settings given by the cs(Card_settings).
    '''

    def load_static_cfg_params(self, card, cs, ms):
        """
        Load the static parameters for the card configuration from the card and measurement settings.
        """

        self._gated = ms.gated
        self._card = card

        self._c_buf_ptr = ms.return_c_buf_ptr()
        self._ai_ch = cs.ai_ch
        self._ai_range_mV = cs.ai_range_mV
        self._ai_offset_mV =cs.ai_offset_mV
        self._ai_term = cs.ai_term
        self._ai_coupling = cs.ai_coupling
        self._acq_mode = cs.acq_mode
        self._acq_HW_avg_num = cs.acq_HW_avg_num
        self._acq_pre_trigs_S = cs.acq_pre_trigs_S
        self._buf_notify_size_B = cs.buf_notify_size_B
        self._clk_ref_Hz = int(cs.clk_ref_Hz)
        self._trig_mode = cs.trig_mode
        self._trig_level_mV = cs.trig_level_mV
        if self._gated == True:
            self._c_ts_buf_ptr = ms.return_c_ts_buf_ptr()
            self._ts_buf_notify_size_B = cs.ts_buf_notify_size_B

        self.reg = Configure_register_checker(self._card)


    def load_dynamic_cfg_params(self, cs, ms):
        """
        Load the measurement settings dependent parameters for the card configuration
        from the card and measurement settings.
        """
        self._clk_samplerate_Hz = int(cs.clk_samplerate_Hz)
        self._acq_post_trigs_S = cs.acq_post_trigs_S
        self._acq_seg_size_S = cs.acq_seg_size_S
        self._acq_loops = cs.acq_loops
        self._buf_size_B = cs.buf_size_B
        self._acq_seq_size_S = ms.seq_size_S
        self._acq_mem_size_S = cs.acq_mem_size_S
        if self._gated == True:
            self._ts_buf_size_B = cs.ts_buf_size_B


    def configure_all(self):
        """
        Collection of all the setting methods.
        """

        self.set_analog_input_conditions(self._card)
        self.set_acquisition_mode(self._card)
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
        ai_ch_dict ={'CH0': CHANNEL0, 'CH1':CHANNEL1}
        spcm_dwSetParam_i32(card, SPC_TIMEOUT, 5000)
        spcm_dwSetParam_i32(card, SPC_CHENABLE, ai_ch_dict[self._ai_ch])
        if 'CH0' in self._ai_ch:
            self._set_ch0(card)
        elif 'CH1' in self._ai_ch:
            self._set_ch1(card)

        return

    def _set_ch0(self, card):
        ai_term_dict = {'1MOhm':0, '50Ohm':1}
        ai_coupling_dict = {'DC':0, 'AC':1}
        spcm_dwSetParam_i32(card, SPC_AMP0, self._ai_range_mV) # +- 10 V
        spcm_dwSetParam_i32(card, SPC_OFFS0, self._ai_offset_mV)
        spcm_dwSetParam_i32(card, SPC_50OHM0, ai_term_dict[self._ai_term]) # A "1"("0") sets the 50(1M) ohm termination
        self._error = spcm_dwSetParam_i32(card, SPC_ACDC0, ai_coupling_dict[self._ai_coupling])  # A "0"("1") sets he DC(AC)coupling

    def _set_ch1(self, card):
        ai_term_dict = {'1MOhm':0, '50Ohm':1}
        ai_coupling_dict = {'DC':0, 'AC':1}
        spcm_dwSetParam_i32(card, SPC_AMP1, self._ai_range_mV) # +- 10 V
        spcm_dwSetParam_i32(card, SPC_OFFS1, self._ai_offset_mV)
        spcm_dwSetParam_i32(card, SPC_50OHM1, ai_term_dict[self._ai_term]) # A "1"("0") sets the 50(1M) ohm termination
        self._error = spcm_dwSetParam_i32(card, SPC_ACDC1, ai_coupling_dict[self._ai_coupling])  # A "0"("1") sets he DC(AC)coupling

    def set_acquisition_mode(self, card):
        if 'STD' in self._acq_mode:
            if 'GATE' in self._acq_mode:
                self.set_STD_gate_mode(card, self._acq_mode,
                                       self._acq_pre_trigs_S,self._acq_post_trigs_S,
                                       self._acq_mem_size_S)
            else:
                self.set_STD_trigger_mode(card, self._acq_mode,
                                          self._acq_post_trigs_S, self._acq_seg_size_S,
                                          self._acq_mem_size_S)

        elif 'FIFO' in self._acq_mode:
            if 'GATE' in self._acq_mode:
                self.set_FIFO_gate_mode(card, self._acq_mode,
                                        self._acq_pre_trigs_S, self._acq_post_trigs_S,
                                        self._acq_loops)
            else:
                self.set_FIFO_trigger_mode(card, self._acq_mode,
                                           self._acq_pre_trigs_S, self._acq_post_trigs_S, self._acq_seg_size_S,
                                           self._acq_loops, self._acq_HW_avg_num)

        else:
            raise ValueError('The acquisition mode is not proper')

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
    '''
    This class can be used to check if the card settings are correctly input.
    The registers can be obtained from the card to the Card_settings (csr).
    '''
    def __init__(self, card):
        self._card = card

    def check_cs_registers(self):
        """
        Use this method to fetch the card settings stored in the card registers
        and check them all with csr.
        """

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
