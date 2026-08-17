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

import ctypes
import os
from datetime import datetime
from qudi.hardware.adlink.config_options import (
    AdlinkDataTypes,
    AdlinkADRange,
    AdlinkTimeBase,
    AdlinkTimePacer,
    AdlinkTriggerSource,
    AdlinkTriggerPolarity,
    AdlinkReadCount,
    AdlinkCardType,
    AdlinkSynchronousMode,
)


class AdlinkDefaultSettings:
    def __init__(self, device_type: AdlinkDataTypes.U16) -> None:
        self.savefile_location = self.get_default_save_location()

        # AI_CH_Config
        self.ad_range = AdlinkADRange.AD_B_1_V.value

        # AI_CH_Config
        self.timebase = AdlinkTimeBase.WD_IntTimeBase.value
        self.ad_duty_restore = ctypes.c_bool(False)
        self.ad_convert_source = AdlinkTimePacer.WD_AI_ADCONVSRC_TimePacer.value
        self.double_edged = ctypes.c_bool(False)
        self.buf_auto_reset = ctypes.c_bool(False)

        # AI_Trig_Config
        self.ad_trigger_mode = AdlinkDataTypes.U16(0)
        self.ad_trigger_source = AdlinkTriggerSource.WD_AI_TRGSRC_ExtD.value
        self.ad_trigger_polarity = AdlinkTriggerPolarity.WD_AI_TrgPositive.value
        self.analog_trigger_channel = AdlinkDataTypes.U16(0)
        self.analog_trigger_level = AdlinkDataTypes.F64(0.0)
        self.post_trigger_scans = AdlinkDataTypes.U32(0)
        self.pre_trigger_scans = AdlinkDataTypes.U32(0)
        self.trigger_delay_ticks = AdlinkDataTypes.U32(0)
        self.retrigger_count = AdlinkDataTypes.U32(0)

        # AsyncDblBufferMode
        self.double_buffered = ctypes.c_bool(False)

        # SetLoggingDataCountPerFile
        self.task = AdlinkDataTypes.U16(0)
        self.data_counts_per_file = AdlinkDataTypes.U64(0)

        self.scancount_per_trigger = AdlinkReadCount.get_value_from_name(
            AdlinkCardType(device_type)
        )

        self.scan_interval = AdlinkDataTypes.U32(1)
        self.channel_num = AdlinkDataTypes.U16(0)
        self.data_type = AdlinkDataTypes.I16
        self.synchronous_mode = AdlinkSynchronousMode.ASYNCH_OP.value

        self.timeout = AdlinkDataTypes.U32(0)

        # global measurement buffer setup
        # this buffer is in the PC's memory and stores data from multiple measurements, until it is written to file
        self.number_of_triggers_per_buffer = 1
        self.callback_function = "copy_double_buffer_callback"
        self.callback_signal = AdlinkDataTypes.I16(2)

    def update_settings(self, arguments):
        # update the settings dict
        for arg_name, arg_value in arguments:
            if arg_name in self.__dict__:
                if arg_value is not None:
                    setattr(self, arg_name, arg_value)

    def get_default_save_location(self):
        save_location = os.path.join(
            os.getcwd(),
            "AdlinkScanToFile_test_" + datetime.now().strftime("%Y%m%d-%H%M%S"),
        )
        return save_location

    def _dictionary(self):
        dictionary = {
            key: value
            for key, value in self.__dict__.items()
            if not key.startswith("__") and not callable(key)
        }
        for key, value in dictionary.items():
            if getattr(value, "__module__", None) == ctypes.c_short.__module__:
                dictionary[key] = value.value
            if key == "data_type":
                dictionary[key] = value.__name__
        return dictionary
