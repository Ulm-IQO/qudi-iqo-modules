# -*- coding: utf-8 -*-

"""
This file contains card commands classes used for spectrum instrumentation ADC.

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
from pyspcm import *


class CardCommands:
    """
    This class contains the methods which wrap the commands to control the SI card.
    Refer to the chapter 'Acquisition modes', the section 'Commands' for more information.
    """

    def __init__(self, card):
        """
        @param str card: The card handle.
        """
        self._card = card

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
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_START)

    def card_stop(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_STOP)

    def card_reset(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_RESET)

    def enable_trigger(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)

    def disable_trigger(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)

    def force_trigger(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)

    def start_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_STARTDMA)

    def stop_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_STOPDMA)

    def wait_dma(self):
        spcm_dwSetParam_i32(self._card, SPC_M2CMD, M2CMD_DATA_WAITDMA)

