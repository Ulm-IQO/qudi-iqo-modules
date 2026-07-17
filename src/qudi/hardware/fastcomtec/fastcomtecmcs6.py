# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware file implementation for FastComtec MCS6.

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
#TODO: start stop works but pause does not work, i guess gui/logic problem
#TODO: Check if there are more modules which are missing, and more settings for FastComtec which need to be put, should we include voltage threshold?

import time
import ctypes
import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface


# ============================================================================
# ctypes / DLL constants
# ============================================================================

DMCS8_DLL_PATH = r"C:\Windows\System32\DMCS8.dll"
DMCS6_DLL_PATH = r"C:\Windows\System32\DMCS6.dll"
FALLBACK_DLL_PATHS = (DMCS8_DLL_PATH, DMCS6_DLL_PATH)
ASCII_ENCODING = "ascii"


# ============================================================================
# Hardware constants
# ============================================================================

DEFAULT_GATED = False
DEFAULT_TRIGGER_SAFETY_S = 400e-9
DEFAULT_AOM_DELAY_S = 390e-9
DEFAULT_MINIMAL_BINWIDTH_S = 0.2e-9

BITSHIFT_LIST_START = 0
BITSHIFT_LIST_STOP = 24
BITSHIFT_LIST_COUNT = 25

MAX_SWEEP_LEN_S = 6.8
MAX_BINS_REFERENCE_BINWIDTH_S = 0.1e-9
RANGE_BIN_INCREMENT = 64

DELAY_STEP_S = 6.4e-9

STATUS_STOPPED = 0
STATUS_STARTED = 1
STATUS_TRANSITIONING = 3

FAST_COUNTER_ERROR = -1
FAST_COUNTER_IDLE = 1
FAST_COUNTER_RUNNING = 2
FAST_COUNTER_PAUSED = 3

STATE_STOPPED = "stopped"
STATE_HALT = "halt"

STATUS_TRANSITION_SLEEP_S = 0.1
MEASURE_STATE_SLEEP_S = 0.05
FAST_COUNTER_CRASH_GUARD_SLEEP_S = 0.5
SSR_CONFIG_SLEEP_S = 0.1

PRESET_MODE_OFF = 0
PRESET_MODE_SWEEP = 4
PRESET_MODE_START = 16

SWEEPMODE_RAW_BYTES_DEC = 35528836


# ============================================================================
# DLL command templates
# ============================================================================

CMD_BITSHIFT = "BITSHIFT={0}"
CMD_RANGE = "RANGE={0}"
CMD_FIRST_CHANNEL = "fstchan={0}"
CMD_PRESET_ENABLE = "prena={0}"
CMD_SWEEP_PRESET = "swpreset={0}"
CMD_SWEEP_MODE = "sweepmode={0}"
CMD_CYCLES = "cycles={0}"
CMD_SEQUENCES = "sequences={0}"
CMD_MPA_NAME = "mpaname=%s"
CMD_SAVE_DATA = "savedata={0}"
CMD_SAVE_MPA = "savempa"

"""
Remark to the usage of ctypes:
All Python types except integers (int), strings (str), and bytes (byte) objects
have to be wrapped in their corresponding ctypes type, so that they can be
converted to the required C data type.

ctypes type     C type                  Python type
----------------------------------------------------------------
c_bool          _Bool                   bool (1)
c_char          char                    1-character bytes object
c_wchar         wchar_t                 1-character string
c_byte          char                    int
c_ubyte         unsigned char           int
c_short         short                   int
c_ushort        unsigned short          int
c_int           int                     int
c_uint          unsigned int            int
c_long          long                    int
c_ulong         unsigned long           int
c_longlong      __int64 or
                long long               int
c_ulonglong     unsigned __int64 or
                unsigned long long      int
c_size_t        size_t                  int
c_ssize_t       ssize_t or
                Py_ssize_t              int
c_float         float                   float
c_double        double                  float
c_longdouble    long double             float
c_char_p        char *
                (NUL terminated)        bytes object or None
c_wchar_p       wchar_t *
                (NUL terminated)        string or None
c_void_p        void *                  int or None

"""

# ============================================================================
# ctypes structures
# ============================================================================

# Reconstruct the proper structure of the variables, which can be extracted
# from the header file 'struct.h'.

class AcqStatus(ctypes.Structure):
    """ Create a structured Data type with ctypes where the dll can write into.

    This object handles and retrieves the acquisition status data from the
    Fastcomtec.

    int started;                // acquisition status: 1 if running, 0 else
    double runtime;             // running time in seconds
    double totalsum;            // total events
    double roisum;              // events within ROI
    double roirate;             // acquired ROI-events per second
    double nettosum;            // ROI sum with background subtracted
    double sweeps;              // Number of sweeps
    double stevents;            // Start Events
    unsigned long maxval;       // Maximum value in spectrum
    """

    _fields_ = [
        ("started", ctypes.c_int),
        ("runtime", ctypes.c_double),
        ("totalsum", ctypes.c_double),
        ("roisum", ctypes.c_double),
        ("roirate", ctypes.c_double),
        ("ofls", ctypes.c_double),
        ("sweeps", ctypes.c_double),
        ("stevents", ctypes.c_double),
        ("maxval", ctypes.c_ulong),
    ]


class AcqSettings(ctypes.Structure):
    """Acquisition settings structure written by the FAST ComTec DLL."""

    _fields_ = [
        ("range", ctypes.c_long),
        ("cftfak", ctypes.c_long),
        ("roimin", ctypes.c_long),
        ("roimax", ctypes.c_long),
        ("nregions", ctypes.c_long),
        ("caluse", ctypes.c_long),
        ("calpoints", ctypes.c_long),
        ("param", ctypes.c_long),
        ("offset", ctypes.c_long),
        ("xdim", ctypes.c_long),
        ("bitshift", ctypes.c_ulong),
        ("active", ctypes.c_long),
        ("eventpreset", ctypes.c_double),
        ("dummy1", ctypes.c_double),
        ("dummy2", ctypes.c_double),
        ("dummy3", ctypes.c_double),
    ]


class ACQDATA(ctypes.Structure):
    """Acquisition data structure written by the FAST ComTec DLL."""

    _fields_ = [
        ("s0", ctypes.POINTER(ctypes.c_ulong)),
        ("region", ctypes.POINTER(ctypes.c_ulong)),
        ("comment", ctypes.c_char_p),
        ("cnt", ctypes.POINTER(ctypes.c_double)),
        ("hs0", ctypes.c_int),
        ("hrg", ctypes.c_int),
        ("hcm", ctypes.c_int),
        ("hct", ctypes.c_int),
    ]


class BOARDSETTING(ctypes.Structure):
    """Board settings structure written by the FAST ComTec DLL."""

    _fields_ = [
        ("sweepmode", ctypes.c_long),
        ("prena", ctypes.c_long),
        ("cycles", ctypes.c_long),
        ("sequences", ctypes.c_long),
        ("syncout", ctypes.c_long),
        ("digio", ctypes.c_long),
        ("digval", ctypes.c_long),
        ("dac0", ctypes.c_long),
        ("dac1", ctypes.c_long),
        ("dac2", ctypes.c_long),
        ("dac3", ctypes.c_long),
        ("dac4", ctypes.c_long),
        ("dac5", ctypes.c_long),
        ("fdac", ctypes.c_int),
        ("tagbits", ctypes.c_int),
        ("extclk", ctypes.c_int),
        ("maxchan", ctypes.c_long),
        ("serno", ctypes.c_long),
        ("ddruse", ctypes.c_long),
        ("active", ctypes.c_long),
        ("holdafter", ctypes.c_double),
        ("swpreset", ctypes.c_double),
        ("fstchan", ctypes.c_double),
        ("timepreset", ctypes.c_double),
    ]


# ============================================================================
# Hardware driver
# ============================================================================

class FastComtec(FastCounterInterface):
    """Qudi hardware class for the FAST ComTec MCS8 card.

    stable: Jochen Scheuer, Simon Schmitt

    Example config for copy-paste:

    fastcomtec_mcs6:
        module.Class: 'fastcomtec.fastcomtecmcs6.FastComtec'
        options:
            gated: False
            trigger_safety: 400e-9
            aom_delay: 390e-9
            minimal_binwidth: 0.2e-9
            dll_path: 'C:\\Windows\\System32\\DMCS8.dll'
    """

    gated = ConfigOption("gated", DEFAULT_GATED, missing="warn")
    trigger_safety = ConfigOption(
        "trigger_safety", DEFAULT_TRIGGER_SAFETY_S, missing="warn"
    )
    aom_delay = ConfigOption("aom_delay", DEFAULT_AOM_DELAY_S, missing="warn")
    minimal_binwidth = ConfigOption(
        "minimal_binwidth", DEFAULT_MINIMAL_BINWIDTH_S, missing="warn"
    )
    dll_path = ConfigOption("dll_path", None, missing="nothing")

    # ------------------------------------------------------------------
    # lifecycle methods
    # ------------------------------------------------------------------

    def __init__(self, *args, **kwargs):
        """Initialize local state used to distinguish halted and stopped mode."""
        super().__init__(*args, **kwargs)

        # This variable has to be added because there is no difference in the
        # FastComtec status between "stopped" and "halt".
        self.stopped_or_halt = STATE_STOPPED
        self.timetrace_tmp = []
        self.loaded_dll_path = None

    def on_activate(self):
        """Load the FAST ComTec DLL without changing software settings."""
        self.dll = self._load_dll()
        return

    def on_deactivate(self):
        """Deactivate the module without sending additional hardware commands."""
        return

    def _load_dll(self):
        """Load the configured FAST ComTec DLL or fall back to known defaults."""
        dll_paths = []
        if self.dll_path:
            dll_paths.append(self.dll_path)
        dll_paths.extend(path for path in FALLBACK_DLL_PATHS if path not in dll_paths)

        errors = []
        for dll_path in dll_paths:
            try:
                dll = ctypes.windll.LoadLibrary(dll_path)
            except OSError as exc:
                errors.append("{0}: {1}".format(dll_path, exc))
            else:
                self.loaded_dll_path = dll_path
                return dll

        raise OSError(
            "Could not load FAST ComTec DLL. Tried: {0}. Errors: {1}".format(
                ", ".join(dll_paths), "; ".join(errors)
            )
        )

    # ------------------------------------------------------------------
    # FastCounterInterface methods
    # ------------------------------------------------------------------

    def get_constraints(self):
        """Return the hardware constraints expected by the fast counter logic.

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
        """
        constraints = {}
        constraints["hardware_binwidth_list"] = list(
            self.minimal_binwidth
            * (
                2
                ** np.array(
                    np.linspace(
                        BITSHIFT_LIST_START,
                        BITSHIFT_LIST_STOP,
                        BITSHIFT_LIST_COUNT,
                    )
                )
            )
        )
        constraints["max_sweep_len"] = MAX_SWEEP_LEN_S
        constraints["max_bins"] = MAX_SWEEP_LEN_S / MAX_BINS_REFERENCE_BINWIDTH_S
        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates=1):
        """Configure the fast counter.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse
                                    sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, record_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
                    number_of_gates: the number of gated, which are accepted,
                    None if not-gated
        """
        # When not gated, record length = total sequence length. When gated,
        # record length = laser length. Subtract time to make sure no sequence
        # trigger is missed.
        self.set_binwidth(bin_width_s)

        if self.gated:
            # Sequential acquisition, new line on every "sync" trigger.
            self.configure_gated_counter(
                bin_width_s, record_length_s, cycles=number_of_gates, preset=1
            )
        else:
            # One acquisition for all taus, one sync trigger per acquisition.
            no_of_bins = int((record_length_s - self.trigger_safety) / bin_width_s)
            self.change_sweep_mode(False, cycles=None, preset=None)
            self.set_length(no_of_bins)

        self.set_cycles(number_of_gates)

        return (
            self.get_binwidth(),
            self.get_length() * self.get_binwidth(),
            number_of_gates,
        )

    def get_status(self):
        """Return the current fast counter status.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        status = AcqStatus()
        self.dll.GetStatusData(ctypes.byref(status), 0)
        # status.started = 3 measn that fct is about to stop
        while status.started == STATUS_TRANSITIONING:
            time.sleep(STATUS_TRANSITION_SLEEP_S)
            self.dll.GetStatusData(ctypes.byref(status), 0)
        if status.started == STATUS_STARTED:
            return FAST_COUNTER_RUNNING
        elif status.started == STATUS_STOPPED:
            if self.stopped_or_halt == STATE_STOPPED:
                return FAST_COUNTER_IDLE
            elif self.stopped_or_halt == STATE_HALT:
                return FAST_COUNTER_PAUSED
            else:
                self.log.error(
                    "There is an unknown status from FastComtec. "
                    "The status message was %s" % (str(status.started))
                )
                return FAST_COUNTER_ERROR
        else:
            self.log.error(
                "There is an unknown status from FastComtec. "
                "The status message was %s" % (str(status.started))
            )
            return FAST_COUNTER_ERROR

    def start_measure(self):
        """Start the measurement."""
        status = self.dll.Start(0)
        while self.get_status() != FAST_COUNTER_RUNNING:
            time.sleep(MEASURE_STATE_SLEEP_S)
        return status

    def stop_measure(self):
        """Stop the measurement."""
        self.stopped_or_halt = STATE_STOPPED
        status = self.dll.Halt(0)
        while self.get_status() != FAST_COUNTER_IDLE:
            time.sleep(MEASURE_STATE_SLEEP_S)
        if self.gated:
            self.timetrace_tmp = []
        return status

    def pause_measure(self):
        """Pause the measurement so it can be continued."""
        self.stopped_or_halt = STATE_HALT
        status = self.dll.Halt(0)
        while self.get_status() != FAST_COUNTER_PAUSED:
            time.sleep(MEASURE_STATE_SLEEP_S)

        if self.gated:
            self.timetrace_tmp = self.get_data_trace()
        return status

    def continue_measure(self):
        """Continue a paused measurement."""
        if self.gated:
            status = self.start_measure()
        else:
            status = self.dll.Continue(0)
            while self.get_status() != FAST_COUNTER_RUNNING:
                time.sleep(MEASURE_STATE_SLEEP_S)
        return status

    def is_gated(self):
        """Return whether the fast counter is configured as gated.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return self.gated

    def get_binwidth(self):
        """Return the current time-bin width in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)

        The read out bitshift will be converted to binwidth. The binwidth is
        defined as 2**bitshift*minimal_binwidth.
        """
        return self.minimal_binwidth * (2 ** int(self.get_bitshift()))

    def get_data_trace(self):
        """Read the current time trace from the fast counter.

        The binning specified by calling configure() must be handled by this
        hardware class. A possible overflow of the histogram bins must be caught
        here and handled.

        If the counter is ungated, the return value is a 1D numpy array with
        returnarray[timebin_index]. If the counter is gated, the return value is
        a 2D numpy array with returnarray[gate_index, timebin_index].

        @return array: Time trace and an info dictionary.
        """
        setting = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(setting), 0)
        data_length = setting.range

        status = AcqStatus()
        self.dll.GetStatusData(ctypes.byref(status), 0)
        elapsed_sweeps = status.stevents
        elapsed_time = status.runtime

        if self.is_gated():
            board_setting = BOARDSETTING()
            self.dll.GetMCSSetting(ctypes.byref(board_setting), 0)
            gate_count = board_setting.cycles
            if gate_count == 0:
                gate_count = 1
            data = np.empty(
                (gate_count, int(data_length / gate_count)), dtype=np.uint32
            )
        else:
            data = np.empty((data_length,), dtype=np.uint32)

        p_type_ulong = ctypes.POINTER(ctypes.c_uint32)
        ptr = data.ctypes.data_as(p_type_ulong)
        self.dll.LVGetDat(ptr, 0)
        time_trace = np.int64(data)

        # NOTE: This preserves the original paused gated behavior. In the
        # original code timetrace_tmp may contain the full return tuple from
        # get_data_trace(), which can make the addition suspicious.
        if self.gated and self.timetrace_tmp != []:
            time_trace = time_trace + self.timetrace_tmp

        info_dict = {
            "elapsed_sweeps": elapsed_sweeps,
            "elapsed_time": elapsed_time,
        }
        return time_trace, info_dict

    # ------------------------------------------------------------------
    # binwidth / bitshift helpers
    # ------------------------------------------------------------------

    def set_gated(self, gated):
        """Set and return the gated status of the fast counter.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        self.change_sweep_mode(gated)
        return self.gated

    def get_bitshift(self):
        """Return the bitshift from the FAST ComTec settings.

        @return int settings.bitshift: the read out bitshift
        """
        settings = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(settings), 0)
        return int(settings.bitshift)

    def set_bitshift(self, bitshift):
        """Set the bitshift for this card.

        @param int bitshift: requested bitshift

        @return int: asks the actual bitshift and returns the read out value
        """
        cmd = CMD_BITSHIFT.format(hex(bitshift))
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return self.get_bitshift()

    def set_binwidth(self, binwidth):
        """Set the binwidth on the card.

        @param float binwidth: the current binwidth in seconds

        @return float: Read out bitshift converted to binwidth

        The binwidth is converted into an appropriate bitshift defined as
        2**bitshift*minimal_binwidth.
        """
        bitshift = int(np.log2(binwidth / self.minimal_binwidth))
        new_bitshift = self.set_bitshift(bitshift)

        return self.minimal_binwidth * (2 ** new_bitshift)

    # ------------------------------------------------------------------
    # length / delay helpers
    # ------------------------------------------------------------------



    # def set_length(self, length_bins, preset=None, cycles=None, sequences=None):
    #     """ Sets the length of the length of the actual measurement.
    #
    #     @param int length_bins: Length of the measurement in bins
    #
    #     @return float: Red out length of measurement
    #     """
    #     constraints = self.get_constraints()
    #     if length_bins * self.get_binwidth() < constraints['max_sweep_len']:
    #         # Smallest increment is 64 bins. Since it is better if the range is too short than too long, round down
    #         if self.gated:
    #             length_bins = int(64 * int(length_bins / 64))
    #             cmd = 'RANGE={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             cmd = 'roimax={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             if preset != None:
    #                 cmd = 'swpreset={0}'.format(preset)
    #                 self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             if cycles != None and cycles != 0:
    #                 cmd = 'cycles={0}'.format(cycles)
    #                 self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #                 # Fastcomtec crashes for big number of cycles without waiting time
    #                 if cycles > 1000:
    #                     time.sleep(10)
    #             if sequences != None and sequences != 0:
    #                 cmd = 'sequences={0}'.format(sequences)
    #                 self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             return self.get_length()
    #         else:
    #             if preset != None:
    #                 cmd = 'swpreset={0}'.format(preset)
    #             else:
    #                 cmd = 'swpreset={0}'.format(1)
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             if cycles != None and cycles != 0:
    #                 cmd = 'cycles={0}'.format(cycles)
    #             else:
    #                 cmd = 'cycles={0}'.format(1)
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             # Fastcomtec crashes for big number of cycles without waiting time
    #             if cycles > 1000:
    #                 time.sleep(10)
    #             if sequences != None and sequences != 0:
    #                 cmd = 'sequences={0}'.format(sequences)
    #             else:
    #                 cmd = 'sequences={0}'.format(1)
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             length_bins = int(64 * int(length_bins / 64))
    #             cmd = 'RANGE={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             cmd = 'roimax={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             return self.get_length()
    #
    #     else:
    #         self.log.error(
    #             'Length of sequence is too high: %s' % (str(length_bins * self.get_binwidth())))
    #         return -1

    def set_length(self, length_bins):
        """Set the length of the actual measurement.

        @param int length_bins: Length of the measurement in bins

        @return float: Read out length of measurement
        """
        constraints = self.get_constraints()
        if self.is_gated():
            cycles = self.get_cycles()
        else:
            cycles = 1
        if length_bins * cycles < constraints["max_bins"]:
            # Smallest increment is 64 bins. Since it is better if the range is
            # too short than too long, round down.
            length_bins = int(
                RANGE_BIN_INCREMENT * int(length_bins / RANGE_BIN_INCREMENT)
            )
            cmd = CMD_RANGE.format(int(length_bins))
            self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))

            # Insert sleep time, otherwise fast counter crashed sometimes!
            time.sleep(FAST_COUNTER_CRASH_GUARD_SLEEP_S)
            return length_bins
        else:
            self.log.error(
                "Dimensions {0} are too large for fast counter1!".format(
                    length_bins * cycles
                )
            )
            return -1

    def get_length(self):
        """Return the current measurement length in bins.

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

    def set_delay_start(self, delay_s):
        """Set the record delay after receiving a start trigger.

        @param int delay_s: Record delay after receiving a start trigger

        @return int: specified delay in unit of bins
        """
        # A delay can only be adjusted in steps of 6.4ns.
        delay_bins = np.rint(delay_s / DELAY_STEP_S)
        cmd = CMD_FIRST_CHANNEL.format(int(delay_bins))
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return delay_bins

    def get_delay_start(self):
        """Return the current record delay length in seconds.

        @return float delay_s: current record delay length in seconds
        """
        board_setting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(board_setting), 0)
        delay_s = board_setting.fstchan * DELAY_STEP_S
        return delay_s

    # ------------------------------------------------------------------
    # gated counting
    # ------------------------------------------------------------------

    def configure_gated_counter(
        self, bin_width_s, record_length_s, preset=None, cycles=None, sequences=None
    ):
        """Configure the gated counter.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int preset: optional, number of preset
        @param int cycles: optional, number of cycles
        @param int sequences: optional, number of sequences.

        @return tuple(binwidth_s, no_of_bins, cycles, preset, sequences):
                    binwidth_s: float the actual set binwidth in seconds
                    no_of_bins: Length in bins
                    cycles: Number of Cycles
                    preset: Number of preset
                    sequences: Number of sequences
        """
        self.set_binwidth(bin_width_s)
        # Change to gated sweep mode.
        self.change_sweep_mode(True, cycles, preset)

        no_of_bins = int((record_length_s + self.aom_delay) / bin_width_s)
        self.set_length(no_of_bins)
        if sequences is not None:
            self.set_sequences(sequences)

        return (
            self.get_binwidth(),
            no_of_bins,
            self.get_cycles(),
            self.get_preset(),
            self.get_sequences(),
        )

    def change_sweep_mode(self, gated, cycles=None, preset=None):
        """Change the sweep mode between gated and ungated.

        @param bool gated: Gated or ungated
        @param int cycles: Optional, change number of cycles. If gated = number
                           of laser pulses.
        @param int preset: Optional, change number of preset. If gated,
                           typically = 1.
        """
        # Reduce length to prevent crashes.
        if gated:
            self.set_cycle_mode(sequential_mode=True, cycles=cycles)
            self.set_preset_mode(mode=PRESET_MODE_START, preset=preset)
            self.gated = True
        else:
            self.set_cycle_mode(sequential_mode=False, cycles=cycles)
            self.set_preset_mode(mode=PRESET_MODE_OFF, preset=preset)
            self.gated = False
        return gated

    def set_preset_mode(self, mode=16, preset=None):
        """Turn on or off a specific preset mode.

        @param int mode: 0 for off, 4 for sweep preset, 16 for start preset
        @param int preset: Optional, change number of presets

        @return just the input
        """
        # Specify preset mode.
        cmd = CMD_PRESET_ENABLE.format(hex(mode))
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))

        # Set the preset if specified.
        if preset is not None:
            self.set_preset(preset)

        return mode, preset

    def set_preset(self, preset):
        """Set the preset.

        @param int preset: Preset in sweeps of starts

        @return int mode: specified save mode
        """
        cmd = CMD_SWEEP_PRESET.format(preset)
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return preset

    def get_preset(self):
        """Return the current preset.

        @return int mode: current preset
        """
        board_setting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(board_setting), 0)
        preset = board_setting.swpreset
        return int(preset)

    def set_cycle_mode(self, sequential_mode=True, cycles=None):
        """Turn sequential cycle mode on or off.

        Sequential cycle mode writes to new memory on every sync trigger. If it
        is disabled, photons are summed.

        @param bool sequential_mode: Set or unset cycle mode for sequential
                                     acquisition
        @param int cycles: Optional, Change number of cycles

        @return: just the input
        """
        # First set cycles to 1 to prevent crashes.
        cycles_old = self.get_cycles() if cycles is None else cycles
        self.set_cycles(1)

        # Turn on or off sequential cycle mode.
        if sequential_mode:
            self.log.debug(
                "Sequential mode enabled. Make sure to set 'checksync=0' and "
                "'nomessages=1' in mcs6a.ini."
            )
            # old standard setting: 1978500
            # old settings + disable "sweep counter not needed"
            # + disable "allow 6 byte words"
            raw_bytes_dec = SWEEPMODE_RAW_BYTES_DEC
        else:
            # NOTE: This preserves the original value. Sequential and
            # non-sequential modes both used the same sweepmode value.
            raw_bytes_dec = SWEEPMODE_RAW_BYTES_DEC

        cmd = CMD_SWEEP_MODE.format(hex(raw_bytes_dec))
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))

        self.set_cycles(cycles_old)

        return sequential_mode, cycles

    def set_cycles(self, cycles):
        """Set the total amount of cycles.

        @param int cycles: Total amount of cycles

        @return int mode: current cycles
        """
        # Check that no constraint is violated.
        constraints = self.get_constraints()
        if cycles == 0:
            cycles = 1
        if self.get_length() * cycles < constraints["max_bins"]:
            cmd = CMD_CYCLES.format(cycles)
            self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
            time.sleep(FAST_COUNTER_CRASH_GUARD_SLEEP_S)
            return cycles
        else:
            self.log.error(
                "Dimensions {0} are too large for fast counter!".format(
                    self.get_length() * cycles
                )
            )
            return -1

    def get_cycles(self):
        """Return the current number of cycles.

        @return int mode: current cycles
        """
        board_setting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(board_setting), 0)
        cycles = board_setting.cycles
        return cycles

    def set_sequences(self, sequences):
        """Set the number of sequences.

        @param int cycles: Total amount of cycles

        @return int mode: current cycles
        """
        # Check that no constraint is violated.
        cmd = CMD_SEQUENCES.format(sequences)
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return sequences

    def get_sequences(self):
        """Return the current number of sequences.

        @return int mode: current cycles
        """
        board_setting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(board_setting), 0)
        sequences = board_setting.sequences
        return sequences

    def set_dimension(self, length, cycles):
        """Set the 2D trace dimensions.

        @param int cycles: Vertical dimension in bins
        @param int length: Horizontal dimension in bins
        """
        self.set_length(length)
        self.set_cycles(cycles)
        return length, cycles

    def get_dimension(self):
        """Return the 2D trace dimensions.

        @return int cycles: Vertical dimension in bins
        @return int length: Horizontal dimension in bins
        """
        cycles = self.get_cycles()
        length = self.get_length()
        return length, cycles

    # ------------------------------------------------------------------
    # SSR counter
    # ------------------------------------------------------------------

    def configure_ssr_counter(self, counts_per_readout=None, countlength=None):
        # FIXME: Change description
        """Configure the gated counter for SSR readout.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int preset: optional, number of preset
        @param int cycles: optional, number of cycles
        @param int sequences: optional, number of sequences.

        @return tuple(binwidth_s, no_of_bins, cycles, preset, sequences):
                    binwidth_s: float the actual set binwidth in seconds
                    no_of_bins: Length in bins
                    cycles: Number of Cycles
                    preset: Number of preset
                    sequences: Number of sequences
        """
        self.change_sweep_mode(
            gated=True, cycles=countlength, preset=counts_per_readout
        )
        self.set_sequences(1)
        time.sleep(SSR_CONFIG_SLEEP_S)
        return

    # ------------------------------------------------------------------
    # saving helpers
    # ------------------------------------------------------------------

    def change_filename(self, name):
        """Change the filename used by the FAST ComTec software.

        @param str name: Location and name of the file
        """
        cmd = CMD_MPA_NAME % name
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return name

    def change_save_mode(self, mode):
        """Change the save mode of the FAST ComTec software.

        @param int mode: Specifies the save mode (0: No Save at Halt, 1: Save
                         at Halt, 2: Write list file, No Save at Halt, 3: Write
                         list file, Save at Halt

        @return int mode: specified save mode
        """
        cmd = CMD_SAVE_DATA.format(mode)
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return mode

    def save_data(self, filename):
        """Save the current settings and data.

        @param str filename: Location and name of the savefile
        """
        self.change_filename(filename)
        cmd = CMD_SAVE_MPA
        self.dll.RunCmd(0, bytes(cmd, ASCII_ENCODING))
        return filename

    # ------------------------------------------------------------------
    # compatibility methods
    # Methods to fulfill gated counter interface
    # (NOT TESTED SINCE GATED COUNTER IS NOT WORKING PROBABLY YET)
    # ------------------------------------------------------------------

    def get_2D_trace(self):
        """Return a 2D trace when the counter is configured as gated."""
        if self.is_gated():
            return self.get_data_trace()
        else:
            self.log.error("Counter is not gated!!!")
            return -1

    def get_count_length(self):
        """Return the configured count length."""
        return self.get_length()

    def set_count_length(self, length):
        """Set and return the configured count length."""
        self.set_length(length)
        return length

    def get_counting_samples(self):
        """Return the configured number of counting samples."""
        return self.get_cycles()

    def set_counting_samples(self, samples):
        """Set and return the configured number of counting samples."""
        self.set_cycles(samples)
        return samples

    def save_raw_data(self, nametag):
        """Save raw data under the provided name tag."""
        self.save_data(nametag)
        return nametag


# ============================================================================
# not-yet-integrated utility functions
# ============================================================================
#
# The following methods have to be carefully reviewed and integrated as internal
# methods/functions, because they might be important one day.

def SetLevel(self, start, stop):
    """Set DAC start and stop levels through stored acquisition settings."""
    setting = AcqSettings()
    self.dll.GetSettingData(ctypes.byref(setting), 0)

    def FloatToWord(r):
        """Convert a floating-point voltage level to a 16-bit word."""
        return int((r + 2.048) / 4.096 * int("ffff", 16))

    # NOTE: This preserves the original utility function exactly. It writes
    # dac0/dac1 on AcqSettings even though those fields are defined on
    # BOARDSETTING, not AcqSettings.
    setting.dac0 = (setting.dac0 & int("ffff0000", 16)) | FloatToWord(start)
    setting.dac1 = (setting.dac1 & int("ffff0000", 16)) | FloatToWord(stop)
    self.dll.StoreSettingData(ctypes.byref(setting), 0)
    self.dll.NewSetting(0)
    return self.GetLevel()


def GetLevel(self):
    """Return DAC start and stop levels from stored acquisition settings."""
    setting = AcqSettings()
    self.dll.GetSettingData(ctypes.byref(setting), 0)

    def WordToFloat(word):
        """Convert a 16-bit word to a floating-point voltage level."""
        return (word & int("ffff", 16)) * 4.096 / int("ffff", 16) - 2.048

    # NOTE: This preserves the original utility function exactly. It reads
    # dac0/dac1 on AcqSettings even though those fields are defined on
    # BOARDSETTING, not AcqSettings.
    return WordToFloat(setting.dac0), WordToFloat(setting.dac1)
