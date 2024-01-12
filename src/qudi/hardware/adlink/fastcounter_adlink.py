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

from qudi.interface.fast_counter_interface import FastCounterInterface
import ctypes
from enum import Enum
import os
from datetime import datetime
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
import time
import numpy as np
import pickle
import struct
from copy import copy


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
    _fields_ = [("card_type", AdlinkDataTypes.I16),
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

class EnumSearchName(Enum):
    def __init__(self, *args) -> None:
        super().__init__()

    @staticmethod
    def get_value_from_name(enum_member: AdlinkCardType):
        # Iterate through EnumClassB members
        for member in AdlinkReadCount:
             if member.name == enum_member.name:
                 return member.value
        member = AdlinkReadCount.DEFAULT
        print(f"Error {enum_member} not found in AdlinkReadCount. Returning value of {member}.")
        return member.value

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
    AD_B_10_V = AdlinkDataTypes.U16(1)
    AD_B_5_V = AdlinkDataTypes.U16(2)
    AD_B_2_5_V = AdlinkDataTypes.U16(3)
    AD_B_1_25_V = AdlinkDataTypes.U16(4)
    AD_B_0_625_V = AdlinkDataTypes.U16(5)
    AD_B_0_3125_V = AdlinkDataTypes.U16(6)
    AD_B_0_5_V = AdlinkDataTypes.U16(7)
    AD_B_0_05_V = AdlinkDataTypes.U16(8)
    AD_B_0_005_V = AdlinkDataTypes.U16(9)
    AD_B_1_V = AdlinkDataTypes.U16(10)
    AD_B_0_1_V = AdlinkDataTypes.U16(11)
    AD_B_0_01_V = AdlinkDataTypes.U16(12)
    AD_B_0_001_V = AdlinkDataTypes.U16(13)
    AD_U_20_V = AdlinkDataTypes.U16(14)
    AD_U_10_V = AdlinkDataTypes.U16(15)
    AD_U_5_V = AdlinkDataTypes.U16(16)
    AD_U_2_5_V = AdlinkDataTypes.U16(17)
    AD_U_1_25_V = AdlinkDataTypes.U16(18)
    AD_U_1_V = AdlinkDataTypes.U16(19)
    AD_U_0_1_V = AdlinkDataTypes.U16(20)
    AD_U_0_01_V = AdlinkDataTypes.U16(21)
    AD_U_0_001_V = AdlinkDataTypes.U16(22)
    AD_B_2_V = AdlinkDataTypes.U16(23)
    AD_B_0_25_V = AdlinkDataTypes.U16(24)
    AD_B_0_2_V = AdlinkDataTypes.U16(25)
    AD_U_4_V = AdlinkDataTypes.U16(26)
    AD_U_2_V = AdlinkDataTypes.U16(27)
    AD_U_0_5_V = AdlinkDataTypes.U16(28)
    AD_U_0_4_V = AdlinkDataTypes.U16(29)
    AD_B_1_5_V = AdlinkDataTypes.U16(30)
    AD_B_0_2145_V = AdlinkDataTypes.U16(31)

class AdlinkSynchronousMode(Enum):
    SYNCH_OP = AdlinkDataTypes.U16(1)
    ASYNCH_OP = AdlinkDataTypes.U16(2)

class AdlinkReadCount(EnumSearchName):
    PXIe_9834 = AdlinkDataTypes.U16(8)
    PCIe_9834 = AdlinkDataTypes.U16(8)
    DEFAULT = AdlinkDataTypes.U16(1)

    def scan_count_per_trigger(self, scan_count: int, device_type: int):
        residual = scan_count % AdlinkReadCount.get_value_from_name(AdlinkCardType(device_type)).value
        if residual != 0:
            return_value = scan_count - residual
            self.scan_count_error(AdlinkDataTypes.I16(return_value), device_type)
            return AdlinkDataTypes.U32(return_value)
        return AdlinkDataTypes.U32(scan_count)

    def scan_count_error(self, set_scan_count: AdlinkDataTypes.I16, device_type: int):
        message = f"Warning: The set scan_count is not a multiple of {AdlinkReadCount.get_value_from_name(AdlinkCardType(device_type)).value} as required by AdlinkCardType(self._device_type).name for WD_AI_ContBufferSetup to work. Thus the scan_count has been set to the {set_scan_count} instead!",

class AdlinkSoftwareTriggerOp(Enum):
    SOFTTRIG_AI = AdlinkDataTypes.U16(1)

class AdlinkTask(Enum):
    AI = AdlinkDataTypes.U16(0)
    DI = AdlinkDataTypes.U16(1)

class AdlinkDefaultSettings:
    def __init__(self, device_type: AdlinkDataTypes.U16) -> None:
        self.savefile_location = self.get_default_save_location()

        # AI_CH_Config
        self.ad_range = AdlinkDataTypes.U16()

        # AI_CH_Config
        self.timebase = AdlinkTimeBase.WD_IntTimeBase.value
        self.ad_duty_restore = ctypes.c_bool(True)
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

        self.scancount_per_trigger = AdlinkReadCount.get_value_from_name(AdlinkCardType(device_type))

        self.scan_interval = AdlinkDataTypes.U32(1)
        self.channel_num = AdlinkDataTypes.U16(0)
        self.data_type = AdlinkDataTypes.I16
        self.synchronous_mode = AdlinkSynchronousMode.ASYNCH_OP.value

        self.timeout = AdlinkDataTypes.U32(5000)


        # global measurement buffer setup
        # this buffer is in the PC's memory and stores data from multiple measurements, until it is written to file
        self.measurement_buffer_length = 10
        self.callback_function = "copy_double_buffer_callback"
        self.callback_signal = AdlinkDataTypes.I16(0)

    def update_settings(self, arguments):
        # update the settings dict
        for arg_name, arg_value in arguments:
            if arg_name in self.__dict__:
                if arg_value is not None:
                    setattr(self, arg_name, arg_value)

    def get_default_save_location(self):
        save_location = os.path.join(os.getcwd(), "AdlinkScanToFile_test_" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        return save_location

    def _dictionary(self):
        dictionary = {key: value for key, value in self.__dict__.items() if not key.startswith('__') and not callable(key)}
        for key, value in dictionary.items():
            if getattr(value, '__module__', None) == ctypes.c_short.__module__:
                dictionary[key] = value.value
            if key == "data_type":
                dictionary[key] = value.__name__
        return dictionary


class Adlink9834(FastCounterInterface):
    """ Interface class to define the controls for fast counting devices.

    A "fast counter" is a hardware device that count events with a "good" time resolution.
    The goal is generally to detect when events happen after an time defining trigger. These events can be photons
    arrival on a detector for example, and the trigger the start of the acquisition.
    This type of hardware regularly records millions of repeated acquisition (ie sweeps) in a few seconds,
    with one or multiple events per trigger (depending on the hardware constrains).
    It can be used in two modes :
    - "Gated" : The result is a 2d array where each line correspond to a single trigger with one or multiple events
                in each line/box
    - "Ungated" : Only the sum of the acquisition is acquired, building an histogram of the events times. This is
                  generally enough for a lot of experiment, where a memory consuming 2d array is not necessary.

    ######################## FOR PULSED MEASUREMENTS ########################
    In order to use the hardware with the pulsed tool-chain one needs to use the
    FastCounterRestartInterfuse

    example configuration:
        adlink9834:
            module.Class: 'adlink.fastcounter_adlink.Adlink9834'
            options:
                wddask_dll_location: "C:/ADLINK/WD-DASK/Lib/wd-dask64.dll"
                card_number: 0
                max_number_triggers: 1e3
    """
    #pulsegenerator = Connector(interface='PulserInterface')

    _dll_location = ConfigOption('wddask_dll_location', default="C:/ADLINK/WD-DASK/Lib/wd-dask64.dll", missing='error')
    _card_num = ConfigOption('card_number', default=0, missing='warn')
    _max_number_triggers = ConfigOption('max_number_triggers', default=1e6, missing='warn',
                                        constructor=lambda x: int(x))
    _device_type = AdlinkCardType.PXIe_9834.value
    _maximum_onboard_ram = 512e6  # MBytes

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Initialization of all necessary variables
        self._card = AdlinkDataTypes.I16()
        self._device_props = AdlinkDeviceProperties()

        self._buffer_size_samples = AdlinkDataTypes.U32(0)
        self._ai_buffer1 = ctypes.c_void_p()
        self._buffer_id1 = AdlinkDataTypes.U16(0)

        self._start_pos = AdlinkDataTypes.U32(0)
        self._count = AdlinkDataTypes.U32(0)

        self._settings = AdlinkDefaultSettings(self._device_type)

        self._trigger_ready = ctypes.c_bool()
        self._acquisition_stop_flag = ctypes.c_bool()
        self._available_data_buffer_id = AdlinkDataTypes.U32()
        self._restart_callback = ctypes.CFUNCTYPE(ctypes.c_int)(self.arm_card)

        self._clock_freq = 200e6 # Hz Fixed value for internal timebase

        self._callback = ctypes.CFUNCTYPE(AdlinkDataTypes.U32)(self.callback_function)
        self._available_data = None

    def __del__(self):
        """
        deltion method of the class
        """
        # unload the dll so a new instance can use the dll without terminating this python thread
        try:
            self.unload_dll(self._dll)
        except:
            return

    def on_activate(self):
        if not hasattr(self, '_dll'):
            self._dll = self.load_dll(self._dll_location)
            self.set_dll_function_return_types()
        if self._dll is None:
            self._dll = self.load_dll(self._dll_location)
            self.set_dll_function_return_types()
        self._card = AdlinkDataTypes.I16(self._dll.WD_Register_Card(self._device_type, self._card_num))
        if self.check_if_error(self._card, "Register_Card"):
            raise RuntimeError("Could not initialize card. Is the DLL already loaded in another program?")
        # this function is not described in the manual, but present in the DLL and the samples
        err = AdlinkDataTypes.I16(self._dll.WD_GetDeviceProperties(self._card, AdlinkDataTypes.U16(0),
                                                                   ctypes.byref(self._device_props)))
        self._settings.ad_range = self._device_props.default_range
        self.check_if_error(err, "GetDeviceProperties")

    def on_deactivate(self):
        try:
            self.disarm_card()
        except Exception as e:
            self.log.error(Exception)
        try:
            err = AdlinkDataTypes.I16(self._dll.WD_AI_EventCallBack_x64(self._card,
                                                                        AdlinkDataTypes.I16(0),
                                                                        self._settings.callback_signal,
                                                                        None))
            self.check_if_error(err, "EventCallBack")
        except Exception as e:
            self.log.error(e)
        try:
            self.close_file_stream()
        except AttributeError as e:
            pass
        if self._card.value > 0:
            try:
                self.free_buffers()
            except Exception as e:
                self.log.error(e)
            AdlinkDataTypes.I16(self._dll.WD_Release_Card(self._card))
        self._card = AdlinkDataTypes.I16()
        self._device_props = AdlinkDeviceProperties()
        self._buffer_id1 = AdlinkDataTypes.U16(0)
        self._ai_buffer1 = AdlinkDataTypes.U16(0)
        self._start_pos = AdlinkDataTypes.U32(0)
        self._count = AdlinkDataTypes.U32(0)
        self._file_stream = None
        try:
            self._dll = self.unload_dll(self._dll)
        except Exception as e:
            self.log.error(e)

    def get_constraints(self):
        """ Retrieve the hardware constrains from the Fast counting device.

        @return dict: dict with keys being the constraint names as string and
                      items are the definition for the constaints.

         The keys of the returned dictionary are the str name for the constraints
        (which are set in this method).

                    NO OTHER KEYS SHOULD BE INVENTED!

        If you are not sure about the meaning, look in other hardware files to
        get an impression. If still additional constraints are needed, then they
        have to be added to all files containing this interface.

        The items of the keys are again dictionaries which have the generic
        dictionary form:
            {'min': <value>,
             'max': <value>,
             'step': <value>,
             'unit': '<value>'}

        Only the key 'hardware_binwidth_list' differs, since they
        contain the list of possible binwidths.

        If the constraints cannot be set in the fast counting hardware then
        write just zero to each key of the generic dicts.
        Note that there is a difference between float input (0.0) and
        integer input (0), because some logic modules might rely on that
        distinction.

        ALL THE PRESENT KEYS OF THE CONSTRAINTS DICT MUST BE ASSIGNED!

        # Example for configuration with default values:

        constraints = dict()

        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seonds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = []

        """
        constraints = dict()

        constraints['hardware_binwidth_list'] = [1 / (self._clock_freq / n)
                                                 for n in [1, 2, 3, 4, 5, 6, 7, 8, 9,
                                                           10, 20, 30, 40, 50, 60, 70, 80, 90,
                                                           100, 200, 300, 400, 500, 600, 700, 800, 900,
                                                           1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000,
                                                           10000, 20000, 30000, 40000, 50000, 60000, 64000, 65535]
                                                 ]
        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates):
        """ Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time race histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, record_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
                    number_of_gates: the number of gated, which are accepted, None if not-gated
        """
        self.free_buffers()
        self._settings.scan_interval.value = round(bin_width_s * self._clock_freq)
        samples_per_laser = round(record_length_s / bin_width_s)
        samples_per_laser_adjustment = samples_per_laser % 8
        self._settings.scancount_per_trigger.value = samples_per_laser - samples_per_laser_adjustment
        self._settings.retrigger_count.value = int(number_of_gates * (self._max_number_triggers // number_of_gates))
        self._number_of_gates = number_of_gates
        # setup the callback dll variables and simultaneosly check for buffer size constraints
        self.buffer_size_bytes()
        self._available_data = np.zeros((number_of_gates, self._settings.scancount_per_trigger.value))
        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_CH_Config(self._card, AdlinkDataTypes.I16(-1), self._settings.ad_range))
        if self.check_if_error(err, "CH_Config"):
            return

        err = AdlinkDataTypes.I16(self._dll.WD_AI_Config(self._card,
                                                         self._settings.timebase,
                                                         self._settings.ad_duty_restore,
                                                         self._settings.ad_convert_source,
                                                         self._settings.double_edged,
                                                         self._settings.buf_auto_reset))
        self.check_if_error(err, "Config")

        err = AdlinkDataTypes.I16(self._dll.WD_AI_Trig_Config(self._card,
                                                              self._settings.ad_trigger_mode,
                                                              self._settings.ad_trigger_source,
                                                              self._settings.ad_trigger_polarity,
                                                              self._settings.analog_trigger_channel,
                                                              self._settings.analog_trigger_level,
                                                              self._settings.post_trigger_scans,
                                                              self._settings.pre_trigger_scans,
                                                              self._settings.trigger_delay_ticks,
                                                              self._settings.retrigger_count))
        if self.check_if_error(err, "Trig_Config"):
            return

        # reserve memory using the DLL's function
        self._ai_buffer1 = ctypes.c_void_p(self._dll.WD_Buffer_Alloc(self._card,
                                                                     self.buffer_size_bytes()))

        if self.check_if_error(AdlinkDataTypes.I16(self._ai_buffer1.value), "BufferAlloc"):
            return
        ctypes.memset(self._ai_buffer1, 0, self.buffer_size_bytes().value)

        err = AdlinkDataTypes.I16(self._dll.WD_AI_ContBufferSetup(self._card,
                                                                  self._ai_buffer1,
                                                                  self.buffer_size_samples(),
                                                                  ctypes.byref(self._buffer_id1)))
        if self.check_if_error(err, "ContBufferSetup"):
            return

        #err = AdlinkDataTypes.I16(self._dll.WD_AI_EventCallBackEx_x64(self._card,
        #                                                              AdlinkDataTypes.I16(1),
        #                                                              self._settings.callback_signal,
        #                                                              self._callback,
        #                                                              None))
        #if self.check_if_error(err, "EventCallBack"):
        #    return

        if self._settings.timeout.value > 0:
            err = AdlinkDataTypes.I16(self._dll.WD_AI_SetTimeout(self._card, self._settings.timeout))
            if self.check_if_error(err, "SetTimeout"):
                return

        return bin_width_s, record_length_s, number_of_gates


    def get_status(self):
        """ Receives the current status of the hardware and outputs it as return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
       -1 = error state
        """
        # try and error values that were returned from the card
        # armed_value = 2147484416  # armed
        # configured_value = 515  # initialized, configured
        # value_offset = 2048
        # status = AdlinkDataTypes.U32()
        stopped = AdlinkDataTypes.U16()
        accesscnt = AdlinkDataTypes.U32()
        try:
            # self._dll.WD_AI_ContStatus(self._card, ctypes.byref(status))
            self._dll.WD_AI_AsyncCheck(self._card, ctypes.byref(stopped), ctypes.byref(accesscnt))
        except AttributeError:
            return -1
        if stopped.value == 0:
            return 2
        if stopped.value == 1:
            if accesscnt.value > 0:
                return 3
            if accesscnt.value == 0:
                return 1
        # if status.value == armed_value or status.value == armed_value + value_offset:
        #     return 2
        # if status.value == configured_value or status.value == configured_value + value_offset:
        #     return 1

    def start_measure(self):
        """ Start the fast counter. """
        self.log.warn("starting adlink")
        self._sweeps = 0
        try:
            self.arm_card()
        except Exception as e:
            self.log.warn(f"Error when arming card: {e}")
        self._start_time = time.time()

    def stop_measure(self):
        """ Stop the fast counter. """
        self.log.warn("stopping adlink")
        try:
            self.disarm_card()
        except Exception as e:
            self.log.warn(f"Error when disarming card: {e}")

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        self.log.warn("pausing adlink")
        try:
            self.disarm_card()
        except Exception as e:
            self.log.warn(f"Error when disarming card: {e}")
        pass

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        self.log.warn("resuming adlink")
        try:
            self.arm_card()
        except Exception as e:
            self.log.warn(f"Error when arming card: {e}")

    def arm_card(self):

        double_buffered_size = AdlinkDataTypes.U32(
            int(self.buffer_size_samples().value / (self._settings.channel_num.value + 1)))
        err = AdlinkDataTypes.I16(self._dll.WD_AI_ContScanChannels(self._card,
                                                                   self._settings.channel_num,
                                                                   self._buffer_id1,
                                                                   double_buffered_size,
                                                                   self._settings.scan_interval,
                                                                   self._settings.scan_interval,
                                                                   self._settings.synchronous_mode))

        if self.check_if_error(err, "ContScanChannelsToFile"):
            return
        return

    def disarm_card(self):
        err = AdlinkDataTypes.I16(self._dll.WD_AI_AsyncClear(self._card,
                                                             ctypes.byref(self._start_pos),
                                                             ctypes.byref(self._count)))
        if self.check_if_error(err, "AsyncClear"):
            return
        return

    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return True

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self._settings.scan_interval.value / self._clock_freq


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

        info_dict = {
            'elapsed_sweeps': self._sweeps,
            'elapsed_time': time.time() - self._start_time,
        }


        # self.pulsegenerator().pulser_off()
        try:
            raw_data = (self._settings.data_type * self._buffer_size_samples.value).from_address(self._ai_buffer1.value)
            data = np.array(raw_data, dtype=np.int64)
            data = data.reshape(-1, self._number_of_gates * self._settings.scancount_per_trigger.value)
            data = np.average(data, axis=0)
            data = data.reshape(self._number_of_gates, -1)
        except Exception as e:
            self.log.error(e)
        self._sweeps += round(self._settings.retrigger_count.value / self._number_of_gates)
        # try:
        #     self.arm_card()
        # except Exception as e:
        #     self.log.error(e)
        #self.pulsegenerator().pulser_on()
        self.log.warn(f"{data}")
        return data, info_dict

    def load_dll(self, location: str):
        """
        Method that loads the dll specified by self._dll_location
        """
        dll = ctypes.CDLL(location)
        return dll

    def unload_dll(self, dll):
        """
        Method to free WD-DASK dll. This makes sure that the DLL can be accessed again without terminating the python thread first.
        """
        dll_handle = ctypes.c_void_p(dll._handle)
        del dll
        ctypes.windll.kernel32.FreeLibrary(dll_handle)
        print(f"Freed DLL at location {dll_handle.value}")

    def free_buffers(self) -> None:
        """
        Method to free the allocated buffers from the currently stored data
        """
        try:
            AdlinkDataTypes.I16(self._dll.WD_AI_ContBufferReset(self._card))
            if self._ai_buffer1.value:
                AdlinkDataTypes.I16(self._dll.WD_Buffer_Free(self._card, self._ai_buffer1))
        except:
            return

    def check_if_error(self, error_code: AdlinkDataTypes.I16, error_str: str) -> bool:
        """
        Checks whether an error occured during a DLL call.
        """
        if error_code.value < 0:
            self.error_occured(error_code, error_str)
            return True
        return False

    def error_occured(self, error_code: AdlinkDataTypes.I16, error_str: str):
        """
        Helper method for printing the correct error
        """
        error_messages = {
            "BufferAlloc": f"Buffer allocation failed (WD_Buffer_Alloc)!",
            "CH_Config": f"Error while changing AI channel configuration (WD_AI_CH_Config)!",
            "ContBufferSetup": f"Error while setting continuous buffer (WD_AI_ContBufferSetup)!",
            "ContScanChannelsToFile": f"Error while starting continuous scan to file acquisition (WD_AI_ContScanChannelsToFile)!",
            "AsyncClear": f"Error while stopping continuous acquisition (WD_AI_AsyncClear)!",
            "Register_Card": f"Error while registering card (WD_Register_Card)!",
            "GetDeviceProperties": f"Error while getting device properties (WD_GetDeviceProperties)!",
            "Config": f"Error while setting AI config (WD_AI_Config)!",
            "Trig_Config": f"Error while setting AI trigger configuration (WD_AI_Trig_Config)!",
            "SetLoggingDataCountPerFile": f"Error while setting logging data count (WD_SetLoggingDataCountPerFile)!",
            "SoftTriggerGen": f"Error while sending a software trigger to the card (WD_SoftTriggerGen)!",
            "AsyncDblBufferToFile": f"Error while writing buffer to file. (WD_AI_AsyncDblBufferToFile)",
            "AsyncReTrigNextReady": f"Error while determining if next data is ready (WD_AI_AsyncReTrigNextReady)!",
            "AsyncCheck": f"Error while determining if next data is ready (WD_AI_AsyncCheck)!",
            "EventCallBack": f"Error when setting the callback function (WD_AI_EventCallBack_x64)!",
            "AsyncDblBufferMode": f"Error when setting double buffered mode (WD_AI_AsyncDblBufferMode)!",
            "SetTimeout": f"Error when setting acquisition timeout (WD_AI_SetTimeout)!",
        }
        self.log.error(error_messages[error_str] + f" ErrorCode {error_code.value}" + f" Reload module!")
        try:
            self.on_deactivate()
        except Exception as e:
            self.log.error(e)

    def buffer_size_bytes(self):
        """
        Calculates the total size of the buffer by multiplying the byte size of the used data type
        with the number of samples that are acquired.
        """
        buffer_size = self.buffer_size_samples().value * ctypes.sizeof(self._settings.data_type)
        if buffer_size > self._maximum_onboard_ram:
            self.log.error("Onboard buffer size too small for number of specified samples. "
                           "Decrease the number of sweeps in the config option")
            return

        self._buffer_size_bytes = AdlinkDataTypes.U32(buffer_size)
        return self._buffer_size_bytes

    def buffer_size_samples(self):
        """
        Calculates the number of samples that will be acquired
        """
        buffer_size = self._settings.scancount_per_trigger.value * (self._settings.channel_num.value + 1)
        if self._settings.retrigger_count.value > 0:
            buffer_size *= self._settings.retrigger_count.value
        self._buffer_size_samples = AdlinkDataTypes.U32(buffer_size)
        return self._buffer_size_samples

    def set_dll_function_return_types(self):
        """
        Function that associates the correct return types from the specified functions
        """
        self._dll.WD_Register_Card.restype = AdlinkDataTypes.I16
        self._dll.WD_GetDeviceProperties.restype = AdlinkDataTypes.I16
        self._dll.WD_AI_CH_Config.restype = AdlinkDataTypes.I16
        self._dll.WD_AI_Config.restype = AdlinkDataTypes.I16
        self._dll.WD_AI_Trig_Config.restype = AdlinkDataTypes.I16
        self._dll.WD_SetLoggingDataCountPerFile.restype = AdlinkDataTypes.I16
        self._dll.WD_AI_ContScanChannelsToFile.restype = AdlinkDataTypes.I16
        self._dll.WD_AI_AsyncClear.restype = AdlinkDataTypes.I16
        self._dll.WD_AI_ContBufferReset.restype = AdlinkDataTypes.I16
        self._dll.WD_Buffer_Free.restype = AdlinkDataTypes.I16
        self._dll.WD_Release_Card.restype = AdlinkDataTypes.I16
        self._dll.WD_Buffer_Alloc.restype = ctypes.c_void_p
        self._dll.WD_AI_ContBufferSetup.restype = AdlinkDataTypes.I16

    def callback_function(self):
        self.log.warn("in callback")
