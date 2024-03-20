# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware file implementation for FastComtec p7887 .

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
import numpy as np
from typing import Union, Type, Iterable, Mapping, Optional, Dict, List, Tuple, Sequence
import time
from os import path
from psutil import virtual_memory
from psutil._common import bytes2human
from itertools import islice

from qudi.util.datastorage import create_dir_for_file
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.util.mutex import Mutex
from qudi.interface.qdyne_counter_interface import GateMode, QdyneCounterInterface, QdyneCounterConstraints, CounterType
from qudi.hardware.fastcomtec.fastcomtecmcs6 import AcqStatus, BOARDSETTING, ACQDATA, AcqSettings


class FastComtecInstreamer(QdyneCounterInterface):
    """ Hardware Class for the FastComtec Card.

    Example config for copy-paste:

    fastcomtec_mcs6_instreamer:
        module.Class: 'fastcomtec.fastcomtecmcs6_instreamer.FastComtecInstreamer'
        options:
    """
    # config options
    _channel_names = ConfigOption(name='channel_names',
                                  missing='error',
                                  constructor=lambda names: [str(x) for x in names])
    _channel_units = ConfigOption(name='channel_units',
                                  missing='error',
                                  constructor=lambda units: [str(x) for x in units])
    _data_type = ConfigOption(name='data_type',
                              default='int32',
                              missing='info',
                              constructor=lambda typ: np.dtype(typ).type)
    _max_read_block_size = ConfigOption(name='max_read_block_size', default=10000,
                                        missing='info')  # specify the number of lines that can at max be read into the memory of the computer from the list file of the device
    _chunk_size = ConfigOption(name='chunk_size', default=10000, missing='nothing')
    _dll_path = ConfigOption(name='dll_path', default='C:\Windows\System32\DMCS6.dll', missing='info')
    _memory_ratio = ConfigOption(name='memory_ratio', default=0.8,
                                 missing='nothing')  # relative amount of memory that can be used for reading measurement data into the system's memory
    _gated = ConfigOption(name='gated',
                                  missing='info',
                          default=False,
                                  constructor=lambda gated: int(gated))

    # Todo: can be extracted from list file
    _line_size = ConfigOption(name='line_size', default=4,
                              missing='nothing')  # how many bytes does one line of measurement data have

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._active_channels = tuple()
        self._sample_rate = None
        self._binwidth = None
        self._buffer_size = None
        self._use_circular_buffer = None
        self._streaming_mode = None
        self._stream_length = None
        self._record_length = None

        self._data_buffer = np.empty(0, dtype=self._data_type)
        self._has_overflown = False
        self._read_lines = None
        self._filename = None

        self._constraints = None

        # Todo: is this the memory of the disc where the file will be written?
        self._available_memory = int(virtual_memory().available * self._memory_ratio)

    def on_activate(self):
        self.dll = ctypes.windll.LoadLibrary(self._dll_path)
        if self._gated:
            self.log.error("Gated mode is not yet implemented!")

        # Create constraints
        self._constraints = QdyneCounterConstraints(
            channel_units=dict(zip(self._channel_names, self._channel_units)),
            counter_type=CounterType.TIMETAGGER.value,
            gate_mode=GateMode(0),
            data_type=self._data_type,
            buffer_size=ScalarConstraint(default=1024 ** 2,
                                                 bounds=(128, self._available_memory),
                                                 increment=1,
                                                 enforce_int=True),
            sample_rate=ScalarConstraint(default=1, bounds=(0.1, 1 / 200e-12), increment=0.1, enforce_int=False)
        )

        # TODO implement the constraints for the fastcomtec max_bins, max_sweep_len and hardware_binwidth_list
        # Reset data buffer
        self._data_buffer = np.empty(0, dtype=self._data_type)
        self._has_overflown = False
        self._read_lines = 0

        self.configure(active_channels=self._constraints.channel_units,
                       streaming_mode=self._streaming_mode,
                       channel_buffer_size=self._available_memory,
                       sample_rate=self._constraints.sample_rate.default)

    def on_deactivate(self):
        # Free memory if possible while module is inactive
        self._data_buffer = np.empty(0, dtype=self._data_type)
        return

    @property
    def constraints(self) -> QdyneCounterConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
        return self._constraints

    @property
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        return list(self._active_channels)

    @property
    def gate_mode(self) -> GateMode:
        """ Read-only property returning the currently configured GateMode Enum """
        return self._gated

    @property
    def buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size """
        return self._buffer_size

    @property
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz """
        return self._sample_rate

    @property
    def binwidth(self):
        """ Read-only property returning the currently set bin width in seconds """
        return self._binwidth

    @property
    def record_length(self):
        """ Read-only property returning the currently set recording length in seconds """
        return self._record_length

    def configure(self,
                  bin_width_s: float,
                  record_length_s: float,
                  active_channels: Sequence[str],
                  gate_mode: Union[GateMode, int],
                  buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a Qdyne counter. See read-only properties for information on each parameter. """
        pass

    def get_status(self):
        """ Receives the current status of the hardware and outputs it as return value.

        0 = unconfigured
        1 = idle
        2 = running
       -1 = error state
        """
        status = AcqStatus()
        self.dll.GetStatusData(ctypes.byref(status), 0)
        # status.started = 3 measn that fct is about to stop
        while status.started == 3:
            time.sleep(0.1)
            self.dll.GetStatusData(ctypes.byref(status), 0)
        if status.started == 1:
            return 2
        elif status.started == 0:
            if self.stopped_or_halt == "stopped":
                return 1
            elif self.stopped_or_halt == "halt":
                return 3
            else:
                self.log.error('There is an unknown status from FastComtec. The status message was %s' % (str(status.started)))
                return -1
        else:
            self.log.error(
                'There is an unknown status from FastComtec. The status message was %s' % (str(status.started)))
            return -1

    def start_measure(self):
        """ Start the qdyne counter. """
        status = self.dll.Start(0)
        while self.get_status() != 2:
            time.sleep(0.05)
        return status

    def stop_measure(self):
        """ Stop the qdyne counter. """
        self.stopped_or_halt = "stopped"
        status = self.dll.Halt(0)
        while self.get_status() != 1:
            time.sleep(0.05)
        if self.gated:
            self.timetrace_tmp = []
        return status

    def get_data(self):
        """ Polls the current time tag data or time series data from the Qdyne counter.

        Return value is a numpy array of type as given in the constraints.
        The counter will return a tuple (1D-numpy-array, info_dict).
        If the counter is a time tagger it will return time tag data in the format
            returnarray = [0, timetag1, timetag2 ... 0, ...], where each 0 indicates a new sweep.
        If the counter is time series it will return time series data in the format
            returnarray = [val_11, val_12 ... val_1N, val_21 ...], where the value for every bin and every sweep
            is concatenated.

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds
        If the hardware does not support these features, the values should be None
        """
        setting = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(setting), 0)
        N = setting.range

        if self.is_gated():
            bsetting=BOARDSETTING()
            self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
            H = bsetting.cycles
            if H==0:
                H=1
            data = np.empty((H, int(N / H)), dtype=np.uint32)

        else:
            data = np.empty((N,), dtype=np.uint32)

        p_type_ulong = ctypes.POINTER(ctypes.c_uint32)
        ptr = data.ctypes.data_as(p_type_ulong)
        self.dll.LVGetDat(ptr, 0)
        time_trace = np.int64(data)

        if self.gated and self.timetrace_tmp != []:
            time_trace = time_trace + self.timetrace_tmp

        info_dict = {'elapsed_sweeps': None,
                     'elapsed_time': None}  # TODO : implement that according to hardware capabilities
        return time_trace, info_dict
