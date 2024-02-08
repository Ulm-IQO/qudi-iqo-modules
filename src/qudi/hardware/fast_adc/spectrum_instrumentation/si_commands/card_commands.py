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
import inspect
from pyspcm import *


class CardCommands:
    '''
    This class contains the methods which wrap the commands to control the SI card.
    '''

    def __init__(self, card):
        self._card = card
        self._error = None

    def start_all(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER
                            | M2CMD_DATA_STARTDMA)

    def start_all_with_extradma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER
                            | M2CMD_DATA_STARTDMA | M2CMD_EXTRA_STARTDMA)

    def start_all_with_poll(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER
                            | M2CMD_DATA_STARTDMA | M2CMD_EXTRA_POLL)

    def card_start(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START)

    def card_stop(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_STOP)

    def card_reset(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_RESET)

    def enable_trigger(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)

    def disable_trigger(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)

    def force_trigger(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)

    def start_dma(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_STARTDMA)

    def stop_dma(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_STOPDMA)

    def wait_dma(self):
        self._error = spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_WAITDMA)

