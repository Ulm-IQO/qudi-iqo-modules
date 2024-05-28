# -*- coding: utf-8 -*-

"""
This file contains buffer command classes used for spectrum instrumentation ADC.

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
import pyspcm as spcm
from ctypes import byref


class DataBufferCommands:
    """
    This class contains the methods which wrap the commands to control the data buffer handling.
    Refer to the chapter 'Buffer handling' in the manual for more details.
    """

    def __init__(self, card):
        """
        @param str card: The card handle.
        """
        self._card = card

    def get_status(self):
        status = spcm.int32()
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_M2STATUS, byref(status))
        return status.value

    def get_avail_user_len_B(self):
        c_avail_user_len = spcm.c_int64(0)
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_DATA_AVAIL_USER_LEN, byref(c_avail_user_len))
        return c_avail_user_len.value

    def get_avail_user_pos_B(self):
        c_avail_user_pos = spcm.c_int64(0)
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_DATA_AVAIL_USER_POS, byref(c_avail_user_pos))
        return c_avail_user_pos.value

    def get_avail_card_len_B(self):
        c_avail_card_len_B = spcm.c_int64()
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_DATA_AVAIL_CARD_LEN, byref(c_avail_card_len_B))
        return c_avail_card_len_B.value

    def set_avail_card_len_B(self, avail_card_len_B):
        c_avail_card_len_B = spcm.c_int32(avail_card_len_B)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_DATA_AVAIL_CARD_LEN, c_avail_card_len_B)
        return

    def get_trig_counter(self):
        c_trig_counter = spcm.c_int64()
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_TRIGGERCOUNTER, byref(c_trig_counter))
        return c_trig_counter.value

    def get_bits_per_sample(self):
        c_bits_per_sample = spcm.c_int32(0)
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_MIINST_BITSPERSAMPLE, byref(c_bits_per_sample))
        return c_bits_per_sample.value

class TsBufferCommands:
    """
    This class contains the methods which wrap the commands to control the timestamp buffer handling.
    Refer to the chapter 'Timestamps' in the manual for more details.
    """

    def __init__(self, card):
        """
        @param str card: The card handle.
        """
        self._card = card

    def reset_ts_counter(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TIMESTAMP_CMD, spcm.SPC_TS_RESET)

    def start_extra_dma(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_M2CMD, spcm.M2CMD_EXTRA_STARTDMA)

    def wait_extra_dma(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_M2CMD, spcm.M2CMD_EXTRA_WAITDMA)

    def stop_extra_dma(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_M2CMD, spcm.M2CMD_EXTRA_STOPDMA)

    def poll_extra_dma(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_M2CMD, spcm.M2CMD_EXTRA_POLL)

    def get_gate_len_alignment(self):
        c_gate_len_alignment = spcm.c_int64(0)
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_GATE_LEN_ALIGNMENT, byref(c_gate_len_alignment))
        return c_gate_len_alignment.value

    def get_ts_avail_user_len_B(self):
        c_ts_avail_user_len = spcm.c_int64(0)
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_TS_AVAIL_USER_LEN, byref(c_ts_avail_user_len))
        return c_ts_avail_user_len.value

    def get_ts_avail_user_pos_B(self):
        c_ts_avail_user_pos = spcm.c_int64(0)
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_TS_AVAIL_USER_POS, byref(c_ts_avail_user_pos))
        return c_ts_avail_user_pos.value

    def get_ts_avail_card_len_B(self):
        c_ts_avail_card_len_B = spcm.c_int64()
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_TS_AVAIL_CARD_LEN, byref(c_ts_avail_card_len_B))
        return c_ts_avail_card_len_B.value

    def set_ts_avail_card_len_B(self, ts_avail_card_len_B):
        c_ts_avail_card_len_B = spcm.c_int64(ts_avail_card_len_B)
        spcm.spcm_dwSetParam_i64(self._card, spcm.SPC_TS_AVAIL_CARD_LEN, c_ts_avail_card_len_B)
        return

    def get_timestamp_command(self):
        c_ts_timestamp_command = spcm.c_int64()
        spcm.spcm_dwGetParam_i64(self._card, spcm.SPC_TIMESTAMP_CMD, byref(c_ts_timestamp_command))
        return c_ts_timestamp_command.value
