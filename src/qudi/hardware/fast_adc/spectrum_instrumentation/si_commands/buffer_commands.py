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
from pyspcm import *


class DataBufferCommands:
    '''
    This class contains the methods which wrap the commands to control the data buffer handling.
    '''

    def __init__(self, card):
        self._card = card
        self._error = None

    def get_status(self):
        status = int32()
        self._error = spcm_dwGetParam_i32(self._card, SPC_M2STATUS, byref(status))
        return status.value

    def get_avail_user_len_B(self):
        c_avail_user_len = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_DATA_AVAIL_USER_LEN, byref(c_avail_user_len))
        return c_avail_user_len.value

    def get_avail_user_pos_B(self):
        c_avail_user_pos = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_DATA_AVAIL_USER_POS, byref(c_avail_user_pos))
        return c_avail_user_pos.value

    def get_avail_card_len_B(self):
        c_avail_card_len_B = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_DATA_AVAIL_CARD_LEN, byref(c_avail_card_len_B))
        return c_avail_card_len_B.value

    def set_avail_card_len_B(self, avail_card_len_B):
        self._error = c_avail_card_len_B = c_int32(avail_card_len_B)
        spcm_dwSetParam_i32(self._card, SPC_DATA_AVAIL_CARD_LEN, c_avail_card_len_B)
        return

    def get_trig_counter(self):
        c_trig_counter = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_TRIGGERCOUNTER, byref(c_trig_counter))
        return c_trig_counter.value

    def get_bits_per_sample(self):
        c_bits_per_sample = c_int32(0)
        self._error = spcm_dwGetParam_i32(self._card, SPC_MIINST_BITSPERSAMPLE, byref(c_bits_per_sample))
        return c_bits_per_sample.value

class TsBufferCommands:
    '''
    This class contains the methods which wrap the commands to control the timestamp buffer handling.
    '''

    def __init__(self, card):
        self._card = card
        self._error = None

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
        return c_gate_len_alignment.value

    def get_ts_avail_user_len_B(self):
        c_ts_avail_user_len = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_TS_AVAIL_USER_LEN, byref(c_ts_avail_user_len))
        return c_ts_avail_user_len.value

    def get_ts_avail_user_pos_B(self):
        c_ts_avail_user_pos = c_int64(0)
        self._error = spcm_dwGetParam_i64(self._card, SPC_TS_AVAIL_USER_POS, byref(c_ts_avail_user_pos))
        return c_ts_avail_user_pos.value

    def get_ts_avail_card_len_B(self):
        c_ts_avail_card_len_B = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_TS_AVAIL_CARD_LEN, byref(c_ts_avail_card_len_B))
        return c_ts_avail_card_len_B.value

    def set_ts_avail_card_len_B(self, ts_avail_card_len_B):
        c_ts_avail_card_len_B = c_int64(ts_avail_card_len_B)
        self._error = spcm_dwSetParam_i64(self._card, SPC_TS_AVAIL_CARD_LEN, c_ts_avail_card_len_B)
        return

    def get_timestamp_command(self):
        c_ts_timestamp_command = c_int64()
        self._error = spcm_dwGetParam_i64(self._card, SPC_TIMESTAMP_CMD, byref(c_ts_timestamp_command))
        return c_ts_timestamp_command.value
