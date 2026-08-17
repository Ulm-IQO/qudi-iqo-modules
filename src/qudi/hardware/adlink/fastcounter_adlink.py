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
import os
from qudi.core.configoption import ConfigOption
import time
import numpy as np
from qudi.hardware.adlink.config_options import (
    AdlinkDataTypes,
    AdlinkADRange,
    AdlinkCardType,
    AdlinkDeviceProperties,
)
from qudi.hardware.adlink.settings import AdlinkDefaultSettings


class Adlink9834(FastCounterInterface):
    """
    FastCounter hardware file for the adlink PCIe_9834 card.
    This device is designed as a gated device. Modifications are needed, if used in ungated mode.
    This file uses a callback function written in C that copies and sums the acquired data from each measurement sweep
    into a buffer which qudi can read. This file is compiled for Microsoft Windows 10 with x64-based PC.
    If you are using another operating system or architecture, recompile adlink_callback_functions.c.

    In order to use the hardware with the pulsed tool-chain one needs to use the FastCounterRestartInterfuse

    example configuration:
        adlink9834:
            module.Class: 'adlink.fastcounter_adlink.Adlink9834'
            options:
                wddask_dll_location: "C:/ADLINK/WD-DASK/Lib/wd-dask64.dll"
                # callback_dll_location: "C:/path/to/file.so"
                card_number: 0
                maximum_samples: 512e6
                trigger_threshold: 1.67
                trigger_delay_ticks: 0
                ad_range: 10
    """

    _dll_location = ConfigOption(
        "wddask_dll_location",
        default="C:/ADLINK/WD-DASK/Lib/wd-dask64.dll",
        missing="error",
    )
    _callback_dll_location = ConfigOption(
        "callback_dll_location",
        default=os.path.join(os.path.dirname(__file__), "adlink_callback_functions.so"),
        missing="info",
    )
    _card_num = ConfigOption("card_number", default=0, missing="warn")
    _maximum_samples = ConfigOption(
        "maximum_samples", default=512e6, missing="info", constructor=lambda x: int(x)
    )  # Maximum number of samples for which a buffer can be set up
    _trigger_threshold = ConfigOption(
        "trigger_threshold", default=1.67, missing="info"
    )  # V
    _trigger_delay_ticks = ConfigOption(
        "trigger_delay_ticks", default=0, missing="info", constructor=lambda x: int(x)
    )
    _ad_range = ConfigOption(
        "ad_range",
        default=10,
        missing="info",
        constructor=lambda x: AdlinkADRange(x),
    )  # ADC range, selectable from AdlinkADRange
    _device_type = AdlinkCardType.PXIe_9834.value

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Initialization of all necessary variables
        self._card = AdlinkDataTypes.I16()
        self._device_props = AdlinkDeviceProperties()

        self.__buffer_size_samples = AdlinkDataTypes.U32(0)
        self._ai_buffer1 = ctypes.c_void_p()
        self._ai_buffer2 = ctypes.c_void_p()
        self._buffer_id1 = AdlinkDataTypes.U16(0)
        self._buffer_id2 = AdlinkDataTypes.U16(0)

        self._start_pos = AdlinkDataTypes.U32(0)
        self._count = AdlinkDataTypes.U32(0)

        self._settings = AdlinkDefaultSettings(self._device_type)
        self._settings.analog_trigger_level.value = self._trigger_threshold
        self._settings.trigger_delay_ticks.value = self._trigger_delay_ticks

        self._trigger_ready = ctypes.c_bool()
        self._acquisition_stop_flag = ctypes.c_bool()
        self._available_data_buffer_id = AdlinkDataTypes.U32()

        # determines how many measurements should be summed up
        # if set to 0 all acquired samples are summed up
        # if set to > 0 the number of samples will be summed up and displayed by the pulsed toolchain
        self._number_of_averages = 0
        self._current_buffer_position = 0

    def __del__(self):
        """
        deletion method of the class
        """
        # unload the dll so a new instance can use the dll without terminating this python thread
        try:
            self._unload_dll(self._dll)
            self._unload_dll(self._callback_dll)
        except:
            return

    def on_activate(self):
        if not hasattr(self, "_dll"):
            self._dll = self._load_dll(self._dll_location)
            self._set_dll_function_return_types()
        if self._dll is None:
            self._dll = self._load_dll(self._dll_location)
            self._set_dll_function_return_types()
        if not hasattr(self, "_callback_dll"):
            self._callback_dll = self._load_dll(self._callback_dll_location)
        if self._callback_dll is None:
            self._callback_dll = self._load_dll(self._callback_dll_location)
        self._card = AdlinkDataTypes.I16(
            self._dll.WD_Register_Card(self._device_type, self._card_num)
        )
        if self._check_if_error(self._card, "Register_Card"):
            raise RuntimeError(
                "Could not initialize card. Is the DLL already loaded in another program?"
            )
        # this function is not described in the manual, but present in the DLL and the samples
        err = AdlinkDataTypes.I16(
            self._dll.WD_GetDeviceProperties(
                self._card, AdlinkDataTypes.U16(0), ctypes.byref(self._device_props)
            )
        )
        self._settings.ad_range = self._ad_range.value
        self._check_if_error(err, "GetDeviceProperties")
        self._clock_freq = int(self._device_props.ctrkHz * 1e3)

    def on_deactivate(self):
        try:
            self._disarm_card()
        except Exception as e:
            self.log.error(e)
        if self._card.value > 0:
            try:
                self._free_buffers()
            except Exception as e:
                self.log.error(e)
            AdlinkDataTypes.I16(self._dll.WD_Release_Card(self._card))
        self._card = AdlinkDataTypes.I16()
        self._device_props = AdlinkDeviceProperties()
        self._buffer_id1 = AdlinkDataTypes.U16(0)
        self._ai_buffer1 = AdlinkDataTypes.U16(0)
        self._start_pos = AdlinkDataTypes.U32(0)
        self._count = AdlinkDataTypes.U32(0)
        try:
            self._dll = self._unload_dll(self._dll)
        except Exception as e:
            self.log.exception(e)

    def get_constraints(self):
        """Retrieve the hardware constrains from the Fast counting device.

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

        constraints["hardware_binwidth_list"] = [
            1 / (self._clock_freq / n)
            for n in list(range(1, 10))
            + list(range(10, 100, 10))
            + list(range(100, 1000, 100))
            + list(range(1000, 10000, 1000))
            + list(range(10000, 50001, 10000))
            + [60000, 64000, 65535]
        ]
        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time race histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, record_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
                    number_of_gates: the number of gated, which are accepted, None if not-gated
        """
        self._disarm_card()
        self._free_buffers()
        self._configure_settings(number_of_gates, bin_width_s, record_length_s)

        self._configure_card()

        self._configure_buffer()

        self._configure_callback()

        return bin_width_s, record_length_s, number_of_gates

    def get_status(self):
        """Receives the current status of the hardware and outputs it as return value.

         0 = unconfigured
         1 = idle
         2 = running
         3 = paused
        -1 = error state
        """
        stopped = AdlinkDataTypes.U16()
        accesscnt = AdlinkDataTypes.U32()
        try:
            self._dll.WD_AI_AsyncCheck(
                self._card, ctypes.byref(stopped), ctypes.byref(accesscnt)
            )
        except AttributeError:
            return -1
        if stopped.value == 0:
            return 2
        if stopped.value == 1:
            return 1
        return -1

    def start_measure(self):
        """Start the fast counter."""
        self.log.info(
            "Starting Adlink\n"
            f"Configured:\n"
            f"scancount: {self._settings.scancount_per_trigger.value},\n"
            f"scan_interval: {self._settings.scan_interval.value},\n"
            f"retrigger_count: {self._settings.retrigger_count.value}"
        )
        self._sweeps = 0
        self._data_buffer = np.zeros(
            (self._number_of_averages, self._buffer_size_samples_one_measurement()),
            dtype=np.float64,
        )
        self._last_measurement = np.zeros(
            (self._buffer_size_samples_one_measurement(),), dtype=np.float64
        )
        self._current_buffer_position = 0
        try:
            self._arm_card()
        except Exception as e:
            self.log.error(f"Error when arming card: {e}")
        self._start_time = time.time()

    def stop_measure(self):
        """Stop the fast counter."""
        self.log.info("Stopping Adlink")
        try:
            self._disarm_card()
        except Exception as e:
            self.log.error(f"Error when disarming card: {e}")

    def pause_measure(self):
        """Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        self.log.info("Pausing Adlink")
        try:
            self._disarm_card()
        except Exception as e:
            self.log.error(f"Error when disarming card: {e}")
        pass

    def continue_measure(self):
        """Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        self.log.info("Resuming Adlink")
        try:
            self._arm_card()
        except Exception as e:
            self.log.error(f"Error when arming card: {e}")

    def is_gated(self):
        """Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return True

    def get_binwidth(self):
        """Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self._settings.scan_interval.value / self._clock_freq

    def get_data_trace(self):
        """Polls the current timetrace data from the fast counter.

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
            "elapsed_sweeps": self._sweeps,
            "elapsed_time": time.time() - self._start_time,
        }

        try:
            data = np.array(self._measurement_buffer, dtype=np.float64)
            if self._number_of_averages <= 0:
                transformed_data = self._transform_raw_data(data)
                return transformed_data, info_dict

            if np.any(self._last_measurement):
                self._data_buffer[
                    self._current_buffer_position % self._number_of_averages
                ] = data - self._last_measurement
                self._current_buffer_position += 1

            transformed_data = self._transform_raw_data(
                np.sum(self._data_buffer, axis=0)
            )
            self._last_measurement = data

            return transformed_data, info_dict

        except Exception as e:
            raise ValueError("Did you invoke the counter settings?") from e

    def _arm_card(self):
        """
        Function that starts the card. After calling this function the card will acquire data on each trigger.
        """
        ctypes.memset(self._ai_buffer1, 0, self.__buffer_size_bytes.value)
        ctypes.memset(self._ai_buffer2, 0, self.__buffer_size_bytes.value)
        buffer_id_c = self._settings.data_type.in_dll(self._callback_dll, "buffer_id")
        buffer_id_c.value = self._buffer_id1.value
        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_ContScanChannels(
                self._card,
                self._settings.channel_num,
                self._buffer_id1,
                self._settings.scancount_per_trigger,
                self._settings.scan_interval,
                self._settings.scan_interval,
                self._settings.synchronous_mode,
            )
        )
        if self._check_if_error(err, "ContScanChannels"):
            return
        return

    def _disarm_card(self):
        """
        Disarms the card making it unresponsive to further triggers sent to the card.
        """
        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_AsyncClear(
                self._card, ctypes.byref(self._start_pos), ctypes.byref(self._count)
            )
        )
        if self._check_if_error(err, "AsyncClear"):
            return
        return

    def _transform_raw_data(self, data: np.ndarray):
        """
        Method that transform the raw data array to the shape expected by qudi
        """
        temp = data.reshape(self._number_of_gates, -1)
        return temp

    def _load_dll(self, location: str):
        """
        Method that loads the dll specified by self._dll_location
        """
        dll = ctypes.CDLL(location)
        return dll

    def _unload_dll(self, dll):
        """
        Method to free WD-DASK dll. This makes sure that the DLL can be accessed again without terminating the python thread first.
        """
        dll_handle = ctypes.c_void_p(dll._handle)
        del dll
        ctypes.windll.kernel32.FreeLibrary(dll_handle)
        self.log.info(f"Freed DLL at location {dll_handle.value}")
        return None

    def _free_buffers(self) -> None:
        """
        Method to free the allocated buffers from the currently stored data
        """
        try:
            AdlinkDataTypes.I16(self._dll.WD_AI_ContBufferReset(self._card))
            if self._ai_buffer1.value:
                AdlinkDataTypes.I16(
                    self._dll.WD_Buffer_Free(self._card, self._ai_buffer1)
                )
            if self._ai_buffer2.value:
                AdlinkDataTypes.I16(
                    self._dll.WD_Buffer_Free(self._card, self._ai_buffer2)
                )
        except:
            return

    def _check_if_error(self, error_code: AdlinkDataTypes.I16, error_str: str) -> bool:
        """
        Checks whether an error occured during a DLL call.
        """
        if error_code.value < 0:
            self._error_occured(error_code, error_str)
            return True
        return False

    def _error_occured(self, error_code: AdlinkDataTypes.I16, error_str: str):
        """
        Helper method for printing the correct error
        """
        error_messages = {
            "BufferAlloc": "Buffer allocation failed (WD_Buffer_Alloc)!",
            "CH_Config": "Error while changing AI channel configuration (WD_AI_CH_Config)!",
            "ContBufferSetup": "Error while setting continuous buffer (WD_AI_ContBufferSetup)!",
            "ContScanChannels": "Error while starting continuous scan to file acquisition (WD_AI_ContScanChannels)!",
            "AsyncClear": "Error while stopping continuous acquisition (WD_AI_AsyncClear)!",
            "Register_Card": "Error while registering card (WD_Register_Card)!",
            "GetDeviceProperties": "Error while getting device properties (WD_GetDeviceProperties)!",
            "Config": "Error while setting AI config (WD_AI_Config)!",
            "Trig_Config": "Error while setting AI trigger configuration (WD_AI_Trig_Config)!",
            "SetLoggingDataCountPerFile": "Error while setting logging data count (WD_SetLoggingDataCountPerFile)!",
            "SoftTriggerGen": "Error while sending a software trigger to the card (WD_SoftTriggerGen)!",
            "AsyncDblBufferToFile": "Error while writing buffer to file. (WD_AI_AsyncDblBufferToFile)",
            "AsyncReTrigNextReady": "Error while determining if next data is ready (WD_AI_AsyncReTrigNextReady)!",
            "AsyncCheck": "Error while determining if next data is ready (WD_AI_AsyncCheck)!",
            "EventCallBack": "Error when setting the callback function (WD_AI_EventCallBack_x64)!",
            "AsyncDblBufferMode": "Error when setting double buffered mode (WD_AI_AsyncDblBufferMode)!",
            "SetTimeout": "Error when setting acquisition timeout (WD_AI_SetTimeout)!",
            "ContBufferReset": "Error when resetting Buffer (WD_AI_ContBufferReset)!",
        }
        self.log.error(
            error_messages[error_str]
            + f" ErrorCode {error_code.value}"
            + " Reload module!"
        )
        try:
            self.on_deactivate()
        except Exception as e:
            self.log.error(e)

    def _buffer_size_bytes(self):
        """
        Calculates the total size of the buffer by multiplying the byte size of the used data type
        with the number of samples that are acquired.
        """
        buffer_size = self._buffer_size_samples().value * ctypes.sizeof(
            self._settings.data_type
        )
        if buffer_size > self._maximum_samples * ctypes.sizeof(
            self._settings.data_type
        ):
            buffer_size = self._buffer_size_samples().value * ctypes.sizeof(
                self._settings.data_type
            )

        self.__buffer_size_bytes = AdlinkDataTypes.U32(buffer_size)
        return self.__buffer_size_bytes

    def _buffer_size_samples(self):
        """
        Calculates the number of samples that will be acquired
        """
        # +1 is required as channel_num ranges from 0 to 3
        # but always channel_num + 1 channels are active
        buffer_size = self._settings.scancount_per_trigger.value * (
            self._settings.channel_num.value + 1
        )
        if self._settings.retrigger_count.value > 0:
            buffer_size *= self._settings.retrigger_count.value
        if buffer_size > self._maximum_samples:
            self._settings.retrigger_count.value = self._max_number_retriggers()
            self.log.error(
                "Onboard buffer size too small for number of specified samples. "
                "Decrease the number of sweeps in the config option."
                f"Adlusting the number of retriggers to {self._settings.retrigger_count.value}"
            )
            return self._buffer_size_samples()
        self.__buffer_size_samples = AdlinkDataTypes.U32(buffer_size)
        return self.__buffer_size_samples

    def _buffer_size_samples_one_measurement(self):
        """
        Calculates the number of samples that will be acquired during one (the pulse sequence has run once) measurement readout.
        """
        return (
            self._settings.scancount_per_trigger.value
            * (self._settings.channel_num.value + 1)
            * self._number_of_gates
        )

    def _set_dll_function_return_types(self):
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

    def _max_number_sequence_retriggers(self):
        """
        Calculates the number of sequences that fit into maximum samples size of the card.
        """
        return int(
            self._maximum_samples
            / self._number_of_gates
            / self._settings.scancount_per_trigger.value
        )

    def _max_number_retriggers(self):
        """
        Calculates the number of triggers that the card will acquire into one buffer.
        """
        return int(self._max_number_sequence_retriggers() * self._number_of_gates)

    @property
    def number_of_averages(self) -> int:
        """
        determines how many measurements should be summed up
        if set to 0 all acquired samples are summed up
        if set to > 0 the number of samples will be summed up and displayed by the pulsed toolchain

        @return number_of_averages, int: currently set number of averages
        """
        return self._number_of_averages

    @number_of_averages.setter
    def number_of_averages(self, number: int) -> None:
        """
        determines how many measurements should be summed up
        if set to 0 all acquired samples are summed up
        if set to > 0 the number of samples will be summed up and displayed by the pulsed toolchain

        @param number, int: number of averages to set
        """
        self._number_of_averages = int(number)

    def _set_callback_dll_variables(self):
        """
        Function to set the global variables in the callback dll.
        """
        # Get handle of the variables
        ai_buff1_c_address = ctypes.c_void_p.in_dll(
            self._callback_dll, "ai_buff1_address"
        )
        ai_buff2_c_address = ctypes.c_void_p.in_dll(
            self._callback_dll, "ai_buff2_address"
        )
        total_buffer_c_address = ctypes.c_void_p.in_dll(
            self._callback_dll, "qudi_buffer_address"
        )
        number_measurements_c = self._settings.data_type.in_dll(
            self._callback_dll, "number_of_measurements"
        )
        buffer_size_c = ctypes.c_ulong.in_dll(self._callback_dll, "buffer_size")
        buffer_id_c = self._settings.data_type.in_dll(self._callback_dll, "buffer_id")

        # Set the value of the variables
        ai_buff1_c_address.value = self._ai_buffer1.value
        ai_buff2_c_address.value = self._ai_buffer2.value
        total_buffer_c_address.value = self._measurement_buffer_address.value
        number_measurements_c.value = self._max_number_sequence_retriggers()
        buffer_size_c.value = self._buffer_size_samples_one_measurement()
        buffer_id_c.value = 0

    def _set_buffer(self, ai_buffer_address, buffer_id):
        """
        Function that configures the buffer on the card.
        Parameters
        ----------
        ai_buffer_address: ctypes.c_void_p
                           Pointer to the buffer on the computer
        buffer_id: AdlinkDataTypes.U16
                   Number specifying the buffer on the card

        """
        ctypes.memset(ai_buffer_address, 0, self.__buffer_size_bytes.value)

        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_ContBufferSetup(
                self._card,
                ai_buffer_address,
                self.__buffer_size_samples,
                ctypes.byref(buffer_id),
            )
        )
        if self._check_if_error(err, "ContBufferSetup"):
            return

        # TODO: Check whether buffer_id is properly changed after function execution

    def _configure_settings(self, number_of_gates, bin_width_s, record_length_s):
        """
        Method that calculates and sets the correct settings of the card.
        """
        self._number_of_gates = number_of_gates
        self._settings.scan_interval.value = round(bin_width_s * self._clock_freq)
        samples_per_laser = round(record_length_s / bin_width_s)
        # The card expects the number of samples to be divisible by 8
        samples_per_laser_adjustment = samples_per_laser % 8
        self._settings.scancount_per_trigger.value = (
            samples_per_laser - samples_per_laser_adjustment
        )
        self._settings.retrigger_count.value = self._max_number_retriggers()
        # calculate the resulting card buffer variables

    def _configure_card(self):
        """
        Method that sets the cards settings.
        """
        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_CH_Config(
                self._card,
                AdlinkDataTypes.I16(-1),
                AdlinkDataTypes.U16(self._settings.ad_range),
            )
        )
        if self._check_if_error(err, "CH_Config"):
            return

        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_Config(
                self._card,
                self._settings.timebase,
                self._settings.ad_duty_restore,
                self._settings.ad_convert_source,
                self._settings.double_edged,
                self._settings.buf_auto_reset,
            )
        )
        self._check_if_error(err, "Config")

        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_Trig_Config(
                self._card,
                self._settings.ad_trigger_mode,
                self._settings.ad_trigger_source,
                self._settings.ad_trigger_polarity,
                self._settings.analog_trigger_channel,
                self._settings.analog_trigger_level,
                self._settings.post_trigger_scans,
                self._settings.pre_trigger_scans,
                self._settings.trigger_delay_ticks,
                AdlinkDataTypes.U32(0),
            )
        )
        if self._check_if_error(err, "Trig_Config"):
            return

    def _configure_buffer(self):
        """
        Configures the computer buffer and the cards buffer to correctly read out the data.
        """
        self._buffer_size_bytes()
        self._measurement_buffer = (
            ctypes.c_int64 * self._buffer_size_samples_one_measurement()
        )(*[0] * self._buffer_size_samples_one_measurement())
        self._measurement_buffer_address = ctypes.cast(
            self._measurement_buffer, ctypes.c_void_p
        )

        # reserve memory using the DLL's function
        self._ai_buffer1 = ctypes.c_void_p(
            self._dll.WD_Buffer_Alloc(self._card, self.__buffer_size_bytes)
        )

        if self._check_if_error(
            AdlinkDataTypes.I16(self._ai_buffer1.value), "BufferAlloc"
        ):
            return
        self._set_buffer(self._ai_buffer1, self._buffer_id1)

        self._ai_buffer2 = ctypes.c_void_p(
            self._dll.WD_Buffer_Alloc(self._card, self.__buffer_size_bytes)
        )

        if self._check_if_error(
            AdlinkDataTypes.I16(self._ai_buffer2.value), "BufferAlloc"
        ):
            return
        self._set_buffer(self._ai_buffer2, self._buffer_id2)

        self._data_buffer = np.zeros(
            (self._number_of_averages, self._buffer_size_samples_one_measurement()),
            dtype=np.float64,
        )
        self._last_measurement = np.zeros(
            (self._buffer_size_samples_one_measurement(),), dtype=np.float64
        )

    def _configure_callback(self):
        """
        Method that sets all necessary variables in the callback dll.
        Also configures the card to use its callback functionality.
        """
        # set up variables in callback_dll
        self._set_callback_dll_variables()

        if self._settings.timeout.value > 0:
            err = AdlinkDataTypes.I16(
                self._dll.WD_AI_SetTimeout(self._card, self._settings.timeout)
            )
            if self._check_if_error(err, "SetTimeout"):
                return

        err = AdlinkDataTypes.I16(
            self._dll.WD_AI_EventCallBack_x64(
                self._card,
                AdlinkDataTypes.I16(1),
                self._settings.callback_signal,
                self._callback_dll.sum_buffer_callback,
            )
        )
        if self._check_if_error(err, "EventCallBack"):
            return
