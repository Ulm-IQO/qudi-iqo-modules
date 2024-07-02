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
from typing import Union, List, Sequence
import time
from os import path
from psutil import virtual_memory
from itertools import islice

from qudi.util.datastorage import create_dir_for_file
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.util.mutex import Mutex
from qudi.interface.qdyne_counter_interface import (
    GateMode,
    QdyneCounterInterface,
    QdyneCounterConstraints,
    CounterType,
)
from qudi.hardware.fastcomtec.fastcomtecmcs6 import AcqStatus, BOARDSETTING, AcqSettings


class FastComtecQdyneCounter(QdyneCounterInterface):
    """Hardware Class for the FastComtec Card.

    Example config for copy-paste:

    fastcomtec_mcs6_instreamer:
        module.Class: 'fastcomtec.fastcomtecmcs6_instreamer.FastComtecInstreamer'
        options:
            channel_names:
            channel_units:
            data_type:
            gated: False
            minimal_binwidth: 0.2e-9
            trigger_safety: 400e-9
            dll_path: 'C:\Windows\System32\DMCS6.dll'
            # optional options for performance
            block_size: 10000
            chunk_size: 10000
            memory_ratio: 0.5
    """

    # config options
    _channel_names = ConfigOption(
        name="channel_names",
        missing="error",
        constructor=lambda names: [str(x) for x in names],
    )
    _channel_units = ConfigOption(
        name="channel_units",
        missing="error",
        constructor=lambda units: [str(x) for x in units],
    )
    _data_type = ConfigOption(
        name="data_type",
        default="int32",
        missing="info",
        constructor=lambda typ: np.dtype(typ).type,
    )
    _gated = ConfigOption(
        name="gated",
        missing="warn",
        default=False,
        constructor=lambda gated: int(gated),
    )
    _minimal_binwidth = ConfigOption("minimal_binwidth", 0.2e-9, missing="warn")
    _trigger_safety = ConfigOption("trigger_safety", 400e-9, missing="warn")

    _dll_path = ConfigOption(
        name="dll_path", default="C:\Windows\System32\DMCS6.dll", missing="info"
    )
    # specify the number of lines that can at max be read into the memory of the computer from
    # the list file of the device
    _block_size = ConfigOption(name="block_size", default=10000, missing="info")
    # specify the amount of data that is processed at one read operation of the list file
    _chunk_size = ConfigOption(name="chunk_size", default=10000, missing="nothing")
    # relative amount of memory that can be used for reading measurement data into the system's memory
    _memory_ratio = ConfigOption(name="memory_ratio", default=0.8, missing="nothing")

    # Todo: can be extracted from list file
    # how many bytes does one line of measurement data have
    _line_size = ConfigOption(name="line_size", default=4, missing="nothing")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._active_channels = tuple()
        self._binwidth = None
        self._buffer_size = None
        self._use_circular_buffer = None
        self._record_length = None
        self._number_of_gates = None

        self._data_buffer = np.empty(0, dtype=self._data_type)
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
            block_size=ScalarConstraint(
                default=1024**2,
                bounds=(128, self._available_memory),
                increment=1,
                enforce_int=True,
            ),
            binwidth=ScalarConstraint(
                default=self._minimal_binwidth,
                bounds=(self._minimal_binwidth, self._minimal_binwidth * 2**24),
                increment=0.1,
                enforce_int=False,
            ),
        )

        # TODO implement the constraints for the fastcomtec max_bins, max_sweep_len and hardware_binwidth_list
        # Reset data buffer
        self._data_buffer = np.empty(0, dtype=self._data_type)
        self._has_overflown = False
        self._read_lines = 0

    def on_deactivate(self):
        # Free memory if possible while module is inactive
        self._data_buffer = np.empty(0, dtype=self._data_type)
        return

    @property
    def constraints(self) -> QdyneCounterConstraints:
        """Read-only property returning the constraints on the settings for this data streamer."""
        return self._constraints

    @property
    def active_channels(self) -> List[str]:
        """Read-only property returning the currently configured active channel names"""
        return list(self._active_channels)

    @property
    def gate_mode(self) -> GateMode:
        """Read-only property returning the currently configured GateMode Enum"""
        return self._gated

    @property
    def buffer_size(self) -> int:
        """Read-only property returning the currently set buffer size"""
        return self._buffer_size

    @property
    def number_of_gates(self) -> int:
        return self._number_of_gates

    @property
    def binwidth(self):
        """Read-only property returning the currently set bin width in seconds"""
        return self._binwidth

    @property
    def record_length(self):
        """Read-only property returning the currently set recording length in seconds"""
        return self._record_length

    @property
    def block_size(self):
        return self._block_size

    def configure(
        self,
        bin_width_s: float,
        record_length_s: float,
        active_channels: Sequence[str],
        gate_mode: Union[GateMode, int],
        buffer_size: int,
        number_of_gates: int,
    ) -> None:
        """Configure a Qdyne counter. See read-only properties for information on each parameter."""
        with self._thread_lock:
            if self.module_state() == "locked":
                raise RuntimeError(
                    "Unable to configure data stream while it is already running"
                )

            # Cache current values to restore them if configuration fails
            old_binwidth = self.binwidth
            old_record_length = self.record_length
            old_channels = self.active_channels
            old_gate_mode = self.gate_mode
            old_buffer_size = self.buffer_size
            old_number_of_gates = self.number_of_gates
            try:
                self.set_binwidth(bin_width_s)
                no_of_bins = int((record_length_s - self.trigger_safety) / bin_width_s)

                self.set_record_length(record_length_s)
                self._set_active_channels(active_channels)
                self._set_buffer_size(buffer_size)
                self.change_sweep_mode(gated=False, cycles=None, preset=None)
                self.set_length(no_of_bins)
                self.set_cycles(number_of_gates)
            except Exception as err:
                self.set_binwidth(old_binwidth)
                self.set_record_length(old_record_length)
                self._set_active_channels(old_channels)
                self._set_buffer_size(old_buffer_size)
                self.change_sweep_mode(old_gate_mode)
                self.set_cycles(old_number_of_gates)
                raise RuntimeError(
                    "Error while trying to configure data in-streamer"
                ) from err

        # TODO: Should we return the set values, similar to the fastcounter toolchain?

    def _set_active_channels(self, channels: Sequence[str]) -> None:
        if self._is_not_settable("active channels"):
            return
        if any(ch not in self._constraints.channel_units for ch in channels):
            raise ValueError(
                f"Invalid channel to stream from encountered {tuple(channels)}. \n"
                f"Valid channels are: {tuple(self._constraints.channel_units)}"
            )
        self._active_channels = tuple(channels)

    def _set_cycle_mode(self) -> None:
        if self._is_not_settable("streaming mode"):
            return
        cmd = "sweepmode={0}".format(hex(1978500))
        self.dll.RunCmd(0, bytes(cmd, "ascii"))

    def _set_buffer_size(self, samples: int) -> None:
        self._constraints.channel_buffer_size.check(samples)
        self._buffer_size = samples

    def _is_not_settable(self, option: str = "") -> bool:
        """
        Method that checks whether the FastComtec is running. Throws an error if it is running.
        @return bool: True - device is running, can't set options: False - Device not running, can set options
        """
        if self.module_state() == "locked":
            if option:
                self.log.error(
                    f"Can't set {option} as an acquisition is currently running."
                )
            else:
                self.log.error(
                    f"Can't set option as an acquisition is currently running."
                )
            return True
        return False

    def get_status(self):
        """Receives the current status of the hardware and outputs it as return value.

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
                self.log.error(
                    "There is an unknown status from FastComtec. The status message was %s"
                    % (str(status.started))
                )
                return -1
        else:
            self.log.error(
                "There is an unknown status from FastComtec. The status message was %s"
                % (str(status.started))
            )
            return -1

    def start_measure(self):
        """Start the qdyne counter."""
        with self._thread_lock:
            if self.module_state() == "idle":
                self.module_state.lock()
            self.change_save_mode(2)
            status = self.dll.Start(0)
            while self.get_status() != 2:
                time.sleep(0.05)
            return status

    def stop_measure(self):
        """Stop the qdyne counter."""
        with self._thread_lock:
            if self.module_state() == "locked":
                self.module_state.unlock()
            self.stopped_or_halt = "stopped"
            status = self.dll.Halt(0)
            while self.get_status() != 1:
                time.sleep(0.05)
            self.change_save_mode(0)
            return status

    def get_data(self):
        """Polls the current time tag data or time series data from the Qdyne counter.

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
        new_data = self.read_data_from_file(
            filename=self._filename,
            read_lines=self._read_lines,
            chunk_size=self._chunk_size,
            number_of_samples=None,
        )
        info_dict = {
            "elapsed_sweeps": None,
            "elapsed_time": None,
        }  # TODO : implement that according to hardware capabilities
        return new_data, info_dict

    def set_binwidth(self, binwidth):
        """Set defined binwidth in Card.

        @param float binwidth: the current binwidth in seconds

        @return float: Red out bitshift converted to binwidth

        The binwidth is converted into to an appropiate bitshift defined as
        2**bitshift*minimal_binwidth.
        """
        bitshift = int(np.log2(binwidth / self._minimal_binwidth))
        new_bitshift = self.set_bitshift(bitshift)

        return self._minimal_binwidth * (2**new_bitshift)

    def change_sweep_mode(self, gated, cycles=None, preset=None):
        """Change the sweep mode (gated, ungated)

        @param bool gated: Gated or ungated
        @param int cycles: Optional, change number of cycles. If gated = number of laser pulses.
        @param int preset: Optional, change number of preset. If gated, typically = 1.
        """

        # Reduce length to prevent crashes
        # self.set_length(1440)
        if gated:
            self.set_cycle_mode(sequential_mode=True, cycles=cycles)
            self.set_preset_mode(mode=16, preset=preset)
            self.gated = True
        else:
            self.set_cycle_mode(sequential_mode=False, cycles=cycles)
            self.set_preset_mode(mode=0, preset=preset)
            self.gated = False
        return gated

    def set_length(self, length_bins):
        """Sets the length of the length of the actual measurement.

        @param int length_bins: Length of the measurement in bins

        @return float: Red out length of measurement
        """
        # First check if no constraint is
        constraints = self.get_constraints()
        if self.is_gated():
            cycles = self.get_cycles()
        else:
            cycles = 1
        if length_bins * cycles < constraints["max_bins"]:
            # Smallest increment is 64 bins. Since it is better if the range is too short than too long, round down
            length_bins = int(64 * int(length_bins / 64))
            cmd = "RANGE={0}".format(int(length_bins))
            self.dll.RunCmd(0, bytes(cmd, "ascii"))
            # cmd = 'roimax={0}'.format(int(length_bins))
            # self.dll.RunCmd(0, bytes(cmd, 'ascii'))

            # insert sleep time, otherwise fast counter crashed sometimes!
            time.sleep(0.5)
            return length_bins
        else:
            self.log.error(
                "Dimensions {0} are too large for fast counter1!".format(
                    length_bins * cycles
                )
            )
            return -1

    def set_cycles(self, cycles):
        """Sets the cycles

        @param int cycles: Total amount of cycles

        @return int mode: current cycles
        """
        # Check that no constraint is violated
        constraints = self.get_constraints()
        if cycles == 0:
            cycles = 1
        if self.get_length() * cycles < constraints["max_bins"]:
            cmd = "cycles={0}".format(cycles)
            self.dll.RunCmd(0, bytes(cmd, "ascii"))
            time.sleep(0.5)
            return cycles
        else:
            self.log.error(
                "Dimensions {0} are too large for fast counter!".format(
                    self.get_length() * cycles
                )
            )
            return -1

    def get_length(self):
        """Get the length of the current measurement.

        @return int: length of the current measurement in bins
        """

        if self.is_gated():
            cycles = self.get_cycles()
            if cycles == 0:
                cycles = 1
        else:
            cycles = 1
        setting = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(setting), 0)
        length = int(setting.range / cycles)
        return length

    def get_cycles(self):
        """Gets the cycles
        @return int mode: current cycles
        """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        cycles = bsetting.cycles
        return cycles

    def set_cycle_mode(self, sequential_mode=True, cycles=None):
        """Turns on or off the sequential cycle mode that writes to new memory on every
        sync trigger. If disabled, photons are summed.

        @param bool sequential_mode: Set or unset cycle mode for sequential acquisition
        @param int cycles: Optional, Change number of cycles

        @return: just the input
        """
        # First set cycles to 1 to prevent crashes

        cycles_old = self.get_cycles() if cycles is None else cycles
        self.set_cycles(1)

        # Turn on or off sequential cycle mode
        if sequential_mode:
            cmd = "sweepmode={0}".format(hex(1978500))
        else:
            cmd = "sweepmode={0}".format(hex(1978496))
        self.dll.RunCmd(0, bytes(cmd, "ascii"))

        self.set_cycles(cycles_old)

        return sequential_mode, cycles

    def set_preset_mode(self, mode=16, preset=None):
        """Turns on or off a specific preset mode

        @param int mode: O for off, 4 for sweep preset, 16 for start preset
        @param int preset: Optional, change number of presets

        @return just the input
        """

        # Specify preset mode
        cmd = "prena={0}".format(hex(mode))
        self.dll.RunCmd(0, bytes(cmd, "ascii"))

        # Set the cycles if specified
        if preset is not None:
            self.set_preset(preset)

        return mode, preset

    def set_preset(self, preset):
        """Sets the preset/

        @param int preset: Preset in sweeps of starts

        @return int mode: specified save mode
        """
        cmd = "swpreset={0}".format(preset)
        self.dll.RunCmd(0, bytes(cmd, "ascii"))
        return preset

    ################################ Methods for saving ################################

    def change_filename(self, name: str):
        """Changes filelocation to the default data directory and appends the given name as a file name

        @param str name: Name of the file

        @return str filelocation: complete path to the file
        """
        # join the default data dir with the file name
        filelocation = path.normpath(path.join(self.module_default_data_dir, name))
        # create the directories to the filelocation
        create_dir_for_file(filelocation)
        # send the command for the filelocation to the dll
        cmd = "mpaname=%s" % filelocation
        self.dll.RunCmd(0, bytes(cmd, "ascii"))
        self._filename = filelocation
        return filelocation

    def change_save_mode(self, mode):
        """Changes the save mode of Mcs6

        @param int mode: Specifies the save mode (0: No Save at Halt, 1: Save at Halt,
                        2: Write list file, No Save at Halt, 3: Write list file, Save at Halt

        @return int mode: specified save mode
        """
        cmd = "savedata={0}".format(mode)
        self.dll.RunCmd(0, bytes(cmd, "ascii"))
        return mode

    def save_data(self, filename: str):
        """save the current settings and data in the default data directory.

        @param str filename: Name of the savefile

        @return str filelocation: path to the saved file
        """
        filelocation = self.change_filename(filename)
        cmd = "savempa"
        self.dll.RunCmd(0, bytes(cmd, "ascii"))
        return filelocation

    # =============================================================================================
    def read_data_sanity_check(self, buffer, number_of_samples=None):
        if not isinstance(buffer, np.ndarray) or buffer.dtype != self._data_type:
            self.log.error(
                "buffer must be numpy.ndarray with dtype {0}. Read failed." "".format(
                    self._data_type
                )
            )
            return -1

        if buffer.ndim == 2:
            if buffer.shape[0] != self.number_of_channels():
                self.log.error(
                    "Configured number of channels ({0:d}) does not match first "
                    "dimension of 2D buffer array ({1:d})."
                    "".format(self.number_of_channels(), buffer.shape[0])
                )
                return -1
        elif buffer.ndim != 1:
            self.log.error("Buffer must be a 1D or 2D numpy.ndarray.")
            return -1

        # Check for buffer overflow
        # if self.available_samples > self.channel_buffer_size:
        #    self._has_overflown = True

        if self._filename is None:
            raise TypeError("No filename for data analysis is given.")
        return 0

    def read_data_from_file(
        self, filename=None, read_lines=None, chunk_size=None, number_of_samples=None
    ):
        if filename is None:
            filename = self._filename
        if read_lines is None:
            read_lines = self._read_lines
        if chunk_size is None:
            chunk_size = self._chunk_size
        if read_lines is None:
            read_lines = 0
        if number_of_samples is None:
            number_of_chunks = int(
                self.channel_buffer_size / chunk_size
            )  # float('inf')
            remaining_samples = self.channel_buffer_size % chunk_size
        else:
            number_of_chunks = int(number_of_samples / chunk_size)
            remaining_samples = number_of_samples % chunk_size

        data = []
        extend_data = data.extend  # avoid dots for speed-up
        header_length = self._find_header_length(filename)
        if header_length < 0:
            self.log.error("Header length could not be determined. Return empty data.")
            return data
        channel_bit, edge_bit, timedata_bit, _ = self._extract_data_format(filename)
        if not (channel_bit and edge_bit and timedata_bit):
            self.log.error("Could not extract format style of file {}".format(filename))
            return data
        timedata_bits = (
            -(channel_bit + edge_bit + timedata_bit),
            -(channel_bit + edge_bit),
        )

        with open(filename, "r") as f:
            list(
                islice(
                    f,
                    int(header_length + read_lines - 1),
                    int(header_length + read_lines),
                )
            )  # ignore header and already read lines
            ii = 0
            while True:
                if ii < number_of_chunks:
                    next_lines = list(islice(f, chunk_size))
                elif ii == number_of_chunks and remaining_samples:
                    next_lines = list(islice(f, remaining_samples))
                else:
                    break
                if not next_lines:  # no next lines, finished file
                    break
                # Todo: this might be quite specific
                # if length of last entry of file isn't 9 there was being written on the file
                # when this function was called
                if len(next_lines[-1]) != 9:
                    break
                # extract arrival time of photons saved in hex to dex
                new_lines = [
                    int(
                        "0"
                        + format(int(s, 16), "b")[timedata_bits[0] : timedata_bits[1]],
                        2,
                    )
                    for s in next_lines
                ]
                extend_data(new_lines)
                ii = ii + 1
        return data

    def _find_header_length(self, filename):
        with open(filename) as f:
            header_length = -1
            for ii, line in enumerate(f):
                if "[DATA]" in line:
                    header_length = ii + 1
                    break
        return header_length

    def _extract_data_format(self, filename):
        with open(filename) as f:
            channel_bit, edge_bit, timedata_bit, data_length = (None, None, None, None)
            for line in f:
                if "datalength" in line:
                    data_length = int(
                        line[
                            line.index("datalength") + 11 : line.index("datalength")
                            + 12
                        ]
                    )
                if "channel" in line:
                    channel_bit = int(
                        line[line.index("bit", 2) - 3 : line.index("bit", 2) - 1]
                    )
                if "edge" in line:
                    edge_bit = int(
                        line[line.index("bit", 2) - 3 : line.index("bit", 2) - 1]
                    )
                if "timedata" in line:
                    timedata_bit = int(
                        line[line.index("bit", 2) - 3 : line.index("bit", 2) - 1]
                    )
                if channel_bit and edge_bit and timedata_bit:
                    break
        return channel_bit, edge_bit, timedata_bit, data_length

    def _init_buffer(self):
        # Todo properly
        self._data_buffer = np.zeros(
            self.number_of_channels * self.channel_buffer_size, dtype=self._data_type
        )
        self._has_overflown = False
        return
