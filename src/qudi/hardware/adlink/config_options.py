# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware interface for fast counting devices.

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

from enum import Enum
from qudi.hardware.adlink.custom_enum import EnumSearchName
import ctypes


class AdlinkDataTypes:
    """
    Utility class that wraps the datatype-naming-convention
    of the adlink function manual to the original c-types datatypes.
    """

    U8 = ctypes.c_ubyte
    I16 = ctypes.c_short
    U16 = ctypes.c_ushort
    I32 = ctypes.c_long
    U32 = ctypes.c_ulong
    I64 = ctypes.c_longlong
    U64 = ctypes.c_ulonglong
    F32 = ctypes.c_float
    F64 = ctypes.c_double


class AdlinkDeviceProperties(ctypes.Structure):
    _fields_ = [
        ("card_type", AdlinkDataTypes.I16),
        ("num_of_channels", AdlinkDataTypes.I16),
        ("data_width", AdlinkDataTypes.I16),
        ("default_range", AdlinkDataTypes.I16),
        ("ctrkHz", AdlinkDataTypes.U32),
        ("bdbase", AdlinkDataTypes.U32),
        ("mask", AdlinkDataTypes.U32),
        ("maxscans", AdlinkDataTypes.U32),
        ("alignForCnt", AdlinkDataTypes.U32),
        ("reserved", AdlinkDataTypes.U32),
    ]


class AdlinkTimeBase(Enum):
    WD_ExtTimeBase = AdlinkDataTypes.U16(0x0)
    WD_SSITimeBase = AdlinkDataTypes.U16(0x1)
    WD_StarTimeBase = AdlinkDataTypes.U16(0x2)
    WD_IntTimeBase = AdlinkDataTypes.U16(0x3)
    WD_PXI_CLK10 = AdlinkDataTypes.U16(0x4)
    WD_PLL_REF_PXICLK10 = AdlinkDataTypes.U16(0x4)
    WD_PLL_REF_EXT10 = AdlinkDataTypes.U16(0x5)
    WD_PLL_REF_EXT = AdlinkDataTypes.U16(0x5)
    WD_PXIe_CLK100 = AdlinkDataTypes.U16(0x6)
    WD_PLL_REF_PXIeCLK100 = AdlinkDataTypes.U16(0x6)
    WD_DBoard_TimeBase = AdlinkDataTypes.U16(0x7)


class AdlinkTimePacer(Enum):
    WD_AI_ADCONVSRC_TimePacer = AdlinkDataTypes.U16(0)


class AdlinkAdvancedMode(Enum):
    DAQSTEPPED = AdlinkDataTypes.U16(0x1)
    RestartEn = AdlinkDataTypes.U16(0x2)
    DualBufEn = AdlinkDataTypes.U16(0x4)
    ManualSoftTrg = AdlinkDataTypes.U16(0x40)
    DMASTEPPED = AdlinkDataTypes.U16(0x80)
    AI_AVE = AdlinkDataTypes.U16(0x8)
    AI_AVE_32 = AdlinkDataTypes.U16(0x10)


class AdlinkCardType(Enum):
    """
    Utility class to wrap the card type integers to a human-readable name.
    """

    PCIe_9834 = AdlinkDataTypes.U16(0x37)
    PXIe_9834 = AdlinkDataTypes.U16(0x39)


class AdlinkTriggerSource(Enum):
    WD_AI_TRGSRC_SOFT = AdlinkDataTypes.U16(0)
    WD_AI_TRGSRC_ANA = AdlinkDataTypes.U16(1)
    WD_AI_TRGSRC_ExtD = AdlinkDataTypes.U16(2)
    WD_AI_TRSRC_SSI_1 = AdlinkDataTypes.U16(3)
    WD_AI_TRSRC_SSI_2 = AdlinkDataTypes.U16(4)
    WD_AI_TRSRC_PXIStar = AdlinkDataTypes.U16(5)
    WD_AI_TRSRC_PXIeStar = AdlinkDataTypes.U16(6)
    WD_AI_TRGSRC_ANA_MCHs = AdlinkDataTypes.U16(8)


class AdlinkTriggerMode(Enum):
    WD_AI_TRGMOD_POST = AdlinkDataTypes.U16(0)
    WD_AI_TRGMOD_PRE = AdlinkDataTypes.U16(1)
    WD_AI_TRGMOD_MIDL = AdlinkDataTypes.U16(2)
    WD_AI_TRGMOD_DELAY = AdlinkDataTypes.U16(3)


class AdlinkTriggerPolarity(Enum):
    WD_AI_TrgPositive = AdlinkDataTypes.U16(1)
    WD_AI_TrgNegative = AdlinkDataTypes.U16(0)


class AdlinkAnalogTriggerChannel(Enum):
    CH0ATRIG = AdlinkDataTypes.U16(0)
    CH1ATRIG = AdlinkDataTypes.U16(1)
    CH2ATRIG = AdlinkDataTypes.U16(2)
    CH3ATRIG = AdlinkDataTypes.U16(3)
    CH4ATRIG = AdlinkDataTypes.U16(4)
    CH5ATRIG = AdlinkDataTypes.U16(5)
    CH6ATRIG = AdlinkDataTypes.U16(6)
    CH7ATRIG = AdlinkDataTypes.U16(7)


class AdlinkADRange(Enum):
    AD_B_10_V = 1
    AD_B_5_V = 2
    AD_B_2_5_V = 3
    AD_B_1_25_V = 4
    AD_B_0_625_V = 5
    AD_B_0_3125_V = 6
    AD_B_0_5_V = 7
    AD_B_0_05_V = 8
    AD_B_0_005_V = 9
    AD_B_1_V = 10
    AD_B_0_1_V = 11
    AD_B_0_01_V = 12
    AD_B_0_001_V = 13
    AD_U_20_V = 14
    AD_U_10_V = 15
    AD_U_5_V = 16
    AD_U_2_5_V = 17
    AD_U_1_25_V = 18
    AD_U_1_V = 19
    AD_U_0_1_V = 20
    AD_U_0_01_V = 21
    AD_U_0_001_V = 22
    AD_B_2_V = 23
    AD_B_0_25_V = 24
    AD_B_0_2_V = 25
    AD_U_4_V = 26
    AD_U_2_V = 27
    AD_U_0_5_V = 28
    AD_U_0_4_V = 29
    AD_B_1_5_V = 30
    AD_B_0_2145_V = 31


class AdlinkSynchronousMode(Enum):
    SYNCH_OP = AdlinkDataTypes.U16(1)
    ASYNCH_OP = AdlinkDataTypes.U16(2)


class AdlinkReadCount(EnumSearchName):
    PXIe_9834 = AdlinkDataTypes.U16(8)
    PCIe_9834 = AdlinkDataTypes.U16(8)
    DEFAULT = AdlinkDataTypes.U16(1)


class AdlinkSoftwareTriggerOp(Enum):
    SOFTTRIG_AI = AdlinkDataTypes.U16(1)


class AdlinkTask(Enum):
    AI = AdlinkDataTypes.U16(0)
    DI = AdlinkDataTypes.U16(1)
