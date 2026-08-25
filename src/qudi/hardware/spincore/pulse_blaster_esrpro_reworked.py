# -*- coding: utf-8 -*-

"""
This file contains the Qudi Hardware file for the PulseBlaster ESR Pro.

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

------------------------------------------------------------------------

OVERVIEW

This module wraps the SpinCore SpinAPI DLL to control a PulseBlaster-family digital
pattern generator. It has been used successfully with both the PulseBlasterESR-PRO
PCI series (21 physical channels) and the PulseBlasterUSB series (e.g. the
PBUSB-TTL-24-100-4k, 24 physical channels). It exposes two Qudi interfaces:

  - PulserInterface:  used for uploading and playing back arbitrary digital
                       waveforms (as required by the pulsed measurement
                       toolchain).
  - SwitchInterface:  used for simple, static ON/OFF control of individual
                       channels (e.g. from a GUI switch panel), independent
                       of any loaded waveform.

Internally, all waveforms are represented as a run-length-encoded (RLE)
sequence of instructions: each entry describes a set of active channels and
a duration. This keeps the number of hardware instructions manageable, since
the board can hold at most a few thousand instructions in memory regardless
of how finely time is sampled.

------------------------------------------------------------------------

BOARD IDENTIFICATION -- what is and isn't actually possible

SpinAPI, as wrapped by this module, exposes pb_get_version() (a driver/
library version string, NOT a board model) and pb_get_firmware_id() (a
numeric ID whose mapping to board model is not documented anywhere this
module's author has been able to verify). There is no SCPI-style *IDN?
equivalent available here that reliably reports board model, physical
clock frequency, or channel count.

Because of this, TRUE automatic hardware detection of board model/clock/
channel count is not implemented, since doing so would require guessing at
an unverified ID-to-model mapping. Instead, this module provides a small
table of known board profiles (BOARD_PROFILES below), selected explicitly
via the board_model config option -- this lets you specify the board model
ONCE, with clock frequency, channel count, control-bit behavior, and the
stop/output workaround (see below) all resolved consistently from that
single choice, rather than needing to set each of them separately and keep
them consistent by hand.

get_version() and get_firmware_id() are logged at activation purely for
diagnostic reference (e.g. if you want to help build a verified ID-to-model
mapping in the future) -- they are not used to make any automatic decision.

Config resolution order (highest priority first):
    1. Explicit individual config option (clock_frequency, num_channels,
       use_output_control_bits, min_instr_len, max_instructions,
       zero_output_on_stop_workaround) -- always wins if given.
    2. Value from the board_model profile, if board_model is set and
       recognised.
    3. Built-in fallback default (matches the original PulseBlasterESR-PRO
       PCI behavior: 500 MHz, 21 channels, control bits reserved, 6-cycle
       minimum instruction, 4094 max instructions, stop/output workaround
       disabled).

------------------------------------------------------------------------

MINIMUM PULSE WIDTH -- a genuine hardware limit, surfaced as an error

The minimum instruction duration a board can produce is
min_instr_len / clock_frequency (LEN_MIN). This is a REAL physical limit of
the hardware. If a pulse sequence requests an instruction shorter than
LEN_MIN, this module does NOT silently round it up or drop it (except as an
internal side-effect of channel_delays correction below) -- it is passed
through to the hardware as-is and SpinAPI will return error -91
("instruction length shorter than the minimum allowed"), which check()
reports clearly.

This is intentional: silently rounding arbitrary waveform content in this
low-level hardware file would hide upstream problems (e.g. a mismatched
clock_frequency, or an interfuse/logic layer generating a pulse pattern
that doesn't respect this board's granularity) behind an apparently-working
but subtly wrong output. Any required downsampling/re-timing to match this
board's granularity (e.g. when driven from a separate, faster-clocked
instrument via an interfuse) belongs in that upstream layer, which has the
context to do it correctly, not here.

------------------------------------------------------------------------

OUTPUT/CONTROL WORD BIT USAGE -- use_output_control_bits

On the PulseBlasterESR-PRO (21 physical output channels, 0-20), bits 21-23
of the 24-bit output/control word are NOT physical outputs -- they form a
reserved control field that must be set to 0b111 (the ON constant,
0xE00000) to indicate "normal operation" (as opposed to a special sub-10 ns
short-pulse encoding). Since that board has no physical channels 21-23,
sacrificing those bits for control purposes is harmless.

On boards with 22 or more REAL physical output channels (e.g. the
PulseBlasterUSB PBUSB-TTL-24-100-4k, which has 24 full, independent output
channels occupying the entire 24-bit output/control word, and documents no
reserved short-pulse control field at all), bits 21-23 are genuine physical
outputs. Unconditionally OR-ing in the ON constant on every instruction (as
required on the ESR-PRO) would silently force channels 21, 22, and 23
permanently HIGH on every single instruction, regardless of what the actual
pulse sequence requests for those channels -- with no error, since nothing
about this is invalid from the SpinAPI's point of view.

The use_output_control_bits config option (or board profile) controls this:
    True  -- OR in the ON constant on every instruction, exactly as
             required by the ESR-PRO's reserved control field.
    False -- write the requested channel bitmask directly, with no
             reserved control field. Required for boards where bits 21-23
             are genuine physical outputs.

------------------------------------------------------------------------

OUTPUT LEVEL AFTER STOP -- zero_output_on_stop_workaround

pb_stop() halts the instruction pointer; it does not clear the physical
output register. On the ESR-PRO series, this has never been observed to be
a problem in practice, so the plain pb_stop()/pb_start() pair is used as-is.

On the PulseBlasterUSB (PBUSB-TTL-24-100-4k), this has been confirmed on
real hardware (oscilloscope) to leave outputs frozen indefinitely at
whatever channel pattern was active in the last-executed instruction of a
waveform, with pb_stop() and pb_reset() both individually confirmed on
scope to have no effect on that frozen output level. For a pulsed
measurement, where the loaded waveform's last instruction can hold any
arbitrary pattern HIGH, this means the physical outputs stay at an
arbitrary, uncontrolled level between measurement runs.

The only mechanism confirmed on scope to force a real 0V output on this
board is to reprogram it with a trivial all-channels-LOW self-looping
instruction and start it running -- i.e. the same mechanism the
SwitchInterface uses via activate_channels(). Since this board holds only
one program in memory at a time, doing this necessarily overwrites
whatever waveform was previously loaded. To keep this transparent to the
pulsed measurement toolchain, pulser_on() re-writes the real waveform back
to the board before resuming playback, if pulser_off() had to overwrite it.

The zero_output_on_stop_workaround config option (or board profile)
controls whether this mechanism is used:
    True  -- pulser_off() forces a real 0V output as described above;
             pulser_on() transparently restores the real waveform first,
             if needed, before starting playback.
    False -- pulser_off()/pulser_on() are the plain pb_stop()/pb_start()
             calls, with no extra reprogramming. This is the confirmed-
             correct behavior for the ESR-PRO series.

------------------------------------------------------------------------

Example config for copy-paste (using a known board profile):

    pulseblaster:
        module.Class: 'spincore.pulse_blaster_esrpro.PulseBlasterESRPRO'
        options:
            board_model: 'PBUSB-TTL-24-100-4K'
            debug_mode: False
            use_smart_pulse_creation: False
            library_path: 'C:/SpinCore/SpinAPI/dll/spinapi64.dll'

Example config for copy-paste (fully manual, no board profile -- identical
behavior to specifying everything by hand):

    pulseblaster:
        module.Class: 'spincore.pulse_blaster_esrpro.PulseBlasterESRPRO'
        options:
            clock_frequency: 100e6
            num_channels: 24
            use_output_control_bits: False
            min_instr_len: 6
            max_instructions: 4094
            zero_output_on_stop_workaround: True
            debug_mode: False
            use_smart_pulse_creation: False
            library_path: 'C:/SpinCore/SpinAPI/dll/spinapi64.dll'
            #channel_delays:             # optional per-channel cable delay correction
            #    '0': 200e-9             # channel 0 has 200 ns propagation delay
            #    '2': 500e-9             # channel 2 has 500 ns propagation delay
"""

import ctypes
from ctypes.util import find_library
import platform
import os
import numpy as np

from qudi.interface.switch_interface import SwitchInterface
from qudi.interface.pulser_interface import PulserInterface, PulserConstraints
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain


# ══════════════════════════════════════════════════════════════════════════════
#  KNOWN BOARD PROFILES
#
#  Selected explicitly via the board_model config option (case-insensitive,
#  matched against both the key and each entry's 'aliases'). Values here are
#  taken from each board's published datasheet/manual, plus, where noted,
#  behavior confirmed directly on real hardware with an oscilloscope. If a
#  profile's exact max_instructions figure has not been independently
#  verified against real hardware, that is noted in its 'description'.
# ══════════════════════════════════════════════════════════════════════════════

BOARD_PROFILES = {
    'PBUSB-TTL-24-100-4K': {
        'aliases': ['PBUSB-24-100-4K', 'PB24-100-4K', 'PULSEBLASTERUSB-24-100-4K'],
        'clock_frequency': 100e6,
        'num_channels': 24,
        'use_output_control_bits': False,
        'min_instr_len': 6,
        # Manual states "4k" memory depth (4096 words). 4094 is used here as
        # a conservative reuse of the same reserved-slot margin documented
        # for the ESR-PRO series (4096 - 2 reserved), NOT independently
        # confirmed against this specific board -- override via
        # max_instructions if you find the true usable limit differs.
        'max_instructions': 4094,
        # Confirmed on oscilloscope: this board holds its last active
        # output pattern indefinitely after pb_stop(), with pb_stop() and
        # pb_reset() individually confirmed to have no effect on that
        # frozen level. See module docstring, "OUTPUT LEVEL AFTER STOP".
        'zero_output_on_stop_workaround': True,
        'description': (
            'PulseBlasterUSB (v2), 24 physical output channels, 100 MHz core '
            'clock, ~4k instruction memory. max_instructions is a conservative '
            'estimate, not independently verified for this exact board. '
            'zero_output_on_stop_workaround is enabled based on confirmed '
            'oscilloscope testing of this board.'
        ),
    },
    'ESR-PRO-500': {
        'aliases': ['ESR-PRO', 'PULSEBLASTERESR-PRO', 'ESRPRO', 'ESRPRO-500'],
        'clock_frequency': 500e6,
        'num_channels': 21,
        'use_output_control_bits': True,
        'min_instr_len': 6,
        'max_instructions': 4094,
        'zero_output_on_stop_workaround': False,
        'description': (
            'PulseBlasterESR-PRO PCI, 21 physical output channels, 500 MHz '
            'core clock, ~4k instruction memory.'
        ),
    },
    'ESR-PRO-400': {
        'aliases': ['ESRPRO-400', 'PBESR-PRO-400'],
        'clock_frequency': 400e6,
        'num_channels': 21,
        'use_output_control_bits': True,
        'min_instr_len': 6,
        'max_instructions': 4094,
        'zero_output_on_stop_workaround': False,
        'description': (
            'PulseBlasterESR-PRO PCI, 21 physical output channels, 400 MHz '
            'core clock, ~4k instruction memory.'
        ),
    },
}


class PulseBlasterESRPRO(SwitchInterface, PulserInterface):
    """ Hardware class to control a SpinCore PulseBlaster-family digital pattern
    generator (PCI ESR-PRO series or USB series).

    The wrapped commands are based on the 'spinapi.h' header file and can be
    looked up in the SpinAPI Documentation on the SpinCore website.

    The SpinCore programming library data types map to C types as follows:
        char                  → 8-bit byte / ASCII character
        short int             → 16-bit signed integer
        unsigned short int    → 16-bit unsigned integer
        int / long int        → 32-bit signed integer
        unsigned int          → 32-bit unsigned integer
        float                 → 32-bit floating point
        double                → 64-bit floating point
    """

    # ── Config options ────────────────────────────────────────────────────────
    _library_path = ConfigOption('library_path', default='', missing='info')
    _board_model  = ConfigOption('board_model', default=None, missing='nothing')

    # These six default to None (sentinel "not explicitly set") so that
    # on_activate() can tell the difference between "the user explicitly
    # asked for this value" and "fall back to the board_model profile, or
    # ultimately the built-in default". See _resolve_hardware_parameters().
    _clock_freq_cfg              = ConfigOption('clock_frequency', default=None, missing='nothing')
    _num_channels_cfg            = ConfigOption('num_channels', default=None, missing='nothing')
    _use_output_control_bits_cfg = ConfigOption('use_output_control_bits', default=None, missing='nothing')
    _min_instr_len_cfg           = ConfigOption('min_instr_len', default=None, missing='nothing')
    _max_instructions_cfg        = ConfigOption('max_instructions', default=None, missing='nothing')
    _zero_output_workaround_cfg  = ConfigOption('zero_output_on_stop_workaround', default=None, missing='nothing')

    _debug_mode   = ConfigOption('debug_mode', default=False)
    _use_smart_pulse_creation = ConfigOption('use_smart_pulse_creation', default=False)
    _channel_delays = ConfigOption('channel_delays', default=[])

    # Loaded library handle (set in on_activate)
    _lib = None

    # ── SpinAPI constants ─────────────────────────────────────────────────────

    # Programming mode: always PULSE_PROGRAM for this board (no DDS/RF output)
    PULSE_PROGRAM = 0

    # Instruction opcodes passed as 'inst' argument to pb_inst_pbonly()
    # See spinapi.h and the board manual for detailed descriptions.
    CONTINUE   = 0   # Continue to next instruction
    STOP       = 1   # Stop execution
    LOOP       = 2   # Begin a loop (inst_data = loop count)
    END_LOOP   = 3   # End a loop (inst_data = address of matching LOOP)
    JSR        = 4   # Jump to subroutine (inst_data = subroutine start address)
    RTS        = 5   # Return from subroutine
    BRANCH     = 6   # Unconditional branch (inst_data = target address)
    LONG_DELAY = 7   # Long delay via repetition (inst_data = repeat count >= 2)
    WAIT       = 8   # Wait for hardware/software trigger
    RTI        = 9   # Return from interrupt

    # Short-pulse control flags (bits 21-23 of the flags word). ONLY
    # applicable on boards where these bits are a reserved control field
    # rather than genuine physical outputs -- see use_output_control_bits
    # in the module docstring. For instruction durations > 10 ns on such
    # boards, bits 21-23 must all be 1 (= ON). For sub-10 ns pulses, these
    # bits select how many clock cycles are HIGH. See the ESR-PRO manual's
    # instruction set appendix for the full description.
    ONE_PERIOD   = 0x200000  # bits 21-23 = 001 → 1 clock cycle HIGH
    TWO_PERIOD   = 0x400000  # bits 21-23 = 010 → 2 clock cycles HIGH
    THREE_PERIOD = 0x600000  # bits 21-23 = 011 → 3 clock cycles HIGH
    FOUR_PERIOD  = 0x800000  # bits 21-23 = 100 → 4 clock cycles HIGH
    FIVE_PERIOD  = 0xA00000  # bits 21-23 = 101 → 5 clock cycles HIGH (= 10 ns)
    ON           = 0xE00000  # bits 21-23 = 111 → normal operation (> 10 ns)
    SIX_PERIOD   = 0xC00000  # bits 21-23 = 110 → used on some old boards only

    # Human-readable labels for the pb_read_status() bitmask
    STATUS_DICT = {
        1:  'Stopped',
        2:  'Reset',
        4:  'Running',
        8:  'Waiting',
        16: 'Scanning',   # RadioProcessor boards only
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Used as a context manager ('with self.threadlock:') in public
        # methods that can be invoked directly from another thread (e.g. the
        # SwitchInterface, which the GUI switch panel can call outside of the
        # normal pulsed-measurement call chain).
        self.threadlock = Mutex()

    # =========================================================================
    # Qudi module lifecycle
    # =========================================================================

    def _resolve_board_profile(self, board_model):
        """ Look up a board profile by name, matching against both the
        BOARD_PROFILES dict keys and each entry's 'aliases' list,
        case-insensitively.

        @param str board_model: Board model string from config, or None.
        @return dict: The matched profile dict, or an empty dict if
                      board_model is None or not recognised (in which case
                      a warning is logged).
        """
        if not board_model:
            return {}

        needle = str(board_model).strip().upper()

        for key, profile in BOARD_PROFILES.items():
            candidates = [key.upper()] + [a.upper() for a in profile.get('aliases', [])]
            if needle in candidates:
                self.log.info(
                    'board_model "{0}" matched known profile "{1}": {2}'.format(
                        board_model, key, profile.get('description', '')
                    )
                )
                return profile

        self.log.warning(
            'board_model "{0}" was not found in the list of known profiles: '
            '{1}. Falling back to explicit individual config options (or '
            'their built-in defaults).'.format(
                board_model, list(BOARD_PROFILES.keys())
            )
        )
        return {}

    def _resolve_hardware_parameters(self):
        """ Resolve clock_frequency, num_channels, use_output_control_bits,
        min_instr_len, max_instructions, and zero_output_on_stop_workaround,
        in this priority order:
            1. Explicit individual config option, if given.
            2. Value from the board_model profile, if board_model is set
               and recognised.
            3. Built-in fallback default (matches the original
               PulseBlasterESR-PRO PCI behavior).

        Sets self._clock_freq, self._num_channels,
        self._use_output_control_bits, self._min_instr_len,
        self._max_instructions, and self._zero_output_on_stop_workaround.
        """
        profile = self._resolve_board_profile(self._board_model)

        self._clock_freq = (
            self._clock_freq_cfg if self._clock_freq_cfg is not None
            else profile.get('clock_frequency', 500e6)
        )
        self._num_channels = (
            self._num_channels_cfg if self._num_channels_cfg is not None
            else profile.get('num_channels', 21)
        )
        self._use_output_control_bits = (
            self._use_output_control_bits_cfg if self._use_output_control_bits_cfg is not None
            else profile.get('use_output_control_bits', True)
        )
        self._min_instr_len = (
            self._min_instr_len_cfg if self._min_instr_len_cfg is not None
            else profile.get('min_instr_len', 6)
        )
        self._max_instructions = (
            self._max_instructions_cfg if self._max_instructions_cfg is not None
            else profile.get('max_instructions', 4094)
        )
        self._zero_output_on_stop_workaround = (
            self._zero_output_workaround_cfg if self._zero_output_workaround_cfg is not None
            else profile.get('zero_output_on_stop_workaround', False)
        )

        self.log.info(
            'PulseBlaster hardware parameters resolved: clock_frequency='
            '{0:.3e} Hz, num_channels={1}, use_output_control_bits={2}, '
            'min_instr_len={3} cycles, max_instructions={4}, '
            'zero_output_on_stop_workaround={5}.'.format(
                self._clock_freq, self._num_channels,
                self._use_output_control_bits, self._min_instr_len,
                self._max_instructions, self._zero_output_on_stop_workaround
            )
        )

        if self._num_channels >= 22 and self._use_output_control_bits:
            self.log.warning(
                'num_channels={0} but use_output_control_bits=True. On '
                'boards with 22 or more real output channels, bits 21-23 '
                'are typically genuine physical outputs, not a reserved '
                'control field -- leaving use_output_control_bits=True in '
                'this configuration will silently force channels 21, 22, '
                'and 23 permanently HIGH on every instruction. Set '
                'use_output_control_bits: False (or select a board_model '
                'profile with the correct value) unless you have '
                'specifically confirmed this board reserves those bits '
                'for control purposes.'.format(self._num_channels)
            )

    def on_activate(self):
        """ Initialization performed during activation of the module. """

        self._resolve_hardware_parameters()

        # ── Channel count and state tracking ────────────────────────────────────
        self._switch_states = {
            'd_ch{0}'.format(i): False for i in range(self._num_channels)
        }
        self._channel_states = self._switch_states.copy()

        # ── Output/control word base flags ──────────────────────────────────────
        # Precomputed once here so every call site below uses the same
        # value consistently rather than repeating the config-dependent
        # choice inline everywhere.
        self._output_flag_base = self.ON if self._use_output_control_bits else 0x000000

        # ── Timing constants ──────────────────────────────────────────────────
        # GRAN_MIN: the time represented by one clock cycle (in seconds)
        self.GRAN_MIN   = 1.0 / self._clock_freq
        # LEN_MIN: the minimum valid instruction duration in seconds. This is
        # a genuine physical limit of the hardware -- see the module
        # docstring's "MINIMUM PULSE WIDTH" section.
        self.LEN_MIN    = self.GRAN_MIN * self._min_instr_len
        self.SAMPLE_RATE = self._clock_freq

        # ── Waveform state ────────────────────────────────────────────────────
        self._current_pb_waveform_name         = ''
        self._current_pb_waveform_theoretical  = [{'active_channels': [], 'length': self.LEN_MIN}]
        self._current_pb_waveform              = [{'active_channels': [], 'length': self.LEN_MIN}]
        self._currently_loaded_waveform        = ''
        self._current_activation_config = list(self.get_constraints().activation_config['all'])
        self._current_activation_config.sort()

        # Tracks, on boards where zero_output_on_stop_workaround is enabled,
        # whether the board's single program memory slot currently holds the
        # real loaded waveform (True) or the all-zero filler pattern written
        # by pulser_off() to force a 0V output (False). Used by pulser_on()
        # to decide whether the real waveform needs to be re-written before
        # resuming playback. Unused, and harmless, on boards where the
        # workaround is disabled.
        self._board_holds_real_waveform = False

        # ── Library loading ───────────────────────────────────────────────────
        # find_library() only searches the system PATH and (on Windows)
        # System32, so a few well-known SpinCore installation paths are also
        # checked directly as a fallback, in case the DLL was never added to
        # PATH by the installer.
        lib_path = None

        # Step 1: Try the user-supplied path from the config option
        if self._library_path:
            lib_path = find_library(self._library_path)
            if lib_path is None and os.path.isfile(self._library_path):
                # Config value is a direct absolute path rather than a library name
                lib_path = self._library_path

        # Step 2: Auto-detect library name from OS architecture and search PATH
        if lib_path is None:
            arch = platform.architecture()
            if arch == ('32bit', 'WindowsPE'):
                libname = 'spinapi.dll'
            elif arch == ('64bit', 'WindowsPE'):
                libname = 'spinapi64.dll'
            elif arch == ('32bit', 'ELF'):
                libname = 'libspinapi.so'
            elif arch == ('64bit', 'ELF'):
                libname = 'libspinapi64.so'
            else:
                libname = 'spinapi64.dll'  # reasonable default fallback
            lib_path = find_library(libname)

        # Step 3: Try known SpinCore installation directories directly, in
        # case the DLL is present but not registered on PATH.
        if lib_path is None:
            arch = platform.architecture()
            if 'WindowsPE' in arch[1]:
                candidate_paths = [
                    'C:/SpinCore/SpinAPI/dll/spinapi64.dll',
                    'C:/SpinCore/SpinAPI/dll/spinapi.dll',
                    'C:/Program Files/SpinCore/SpinAPI/dll/spinapi64.dll',
                    'C:/Program Files (x86)/SpinCore/SpinAPI/dll/spinapi.dll',
                ]
            else:
                candidate_paths = [
                    '/usr/local/lib/libspinapi64.so',
                    '/usr/local/lib/libspinapi.so',
                    '/usr/lib/libspinapi.so',
                ]
            for candidate in candidate_paths:
                if os.path.isfile(candidate):
                    lib_path = candidate
                    break

        if lib_path is None:
            self.log.error(
                'SpinCore library not found. Please set the "library_path" '
                'config option to the full path of spinapi64.dll, or ensure '
                'the SpinCore API package is installed.'
            )
            return -1

        self._lib = ctypes.cdll.LoadLibrary(lib_path)
        self.log.debug('SpinCore library loaded from: {0}'.format(lib_path))

        if self._debug_mode:
            # When debug_mode=True, SpinAPI writes a log.txt to the working directory
            self._lib.pb_set_debug(1)

        # ctypes argument/return types must be declared before any calls are
        # made — otherwise ctypes falls back to unsafe defaults (all
        # arguments treated as plain C int, return type as int), which can
        # corrupt values for functions that actually take unsigned int,
        # double, or return a pointer.
        self._setup_lib_signatures()

        self.open_connection()

        # Log diagnostic identification info. These are NOT used to make
        # any automatic configuration decision (see module docstring,
        # "BOARD IDENTIFICATION") -- they are logged purely so that, if you
        # ever want to help build a verified ID-to-model mapping, the raw
        # values are available for reference.
        try:
            self.log.info(
                'PulseBlaster diagnostic info -- SpinAPI version: {0}, '
                'firmware ID: {1}'.format(
                    self.get_version(), self.get_firmware_id()
                )
            )
        except Exception:
            self.log.debug('Could not query SpinAPI version/firmware ID.')

    def _setup_lib_signatures(self):
        """
        Define ctypes return types and argument types for all used SpinAPI functions.

        Must be called once after self._lib is loaded.
        """

        # ── Board detection and initialization ────────────────────────────────
        self._lib.pb_count_boards.restype  = ctypes.c_int
        self._lib.pb_count_boards.argtypes = []

        self._lib.pb_select_board.restype  = ctypes.c_int
        self._lib.pb_select_board.argtypes = [ctypes.c_int]

        self._lib.pb_init.restype  = ctypes.c_int
        self._lib.pb_init.argtypes = []

        self._lib.pb_close.restype  = ctypes.c_int
        self._lib.pb_close.argtypes = []

        # pb_core_clock takes MHz (not Hz) and returns void
        self._lib.pb_core_clock.restype  = None
        self._lib.pb_core_clock.argtypes = [ctypes.c_double]

        # ── Status and diagnostics ────────────────────────────────────────────
        # pb_get_error returns a char* pointer; must be c_char_p not c_int
        self._lib.pb_get_error.restype  = ctypes.c_char_p
        self._lib.pb_get_error.argtypes = []

        self._lib.pb_get_version.restype  = ctypes.c_char_p
        self._lib.pb_get_version.argtypes = []

        self._lib.pb_get_firmware_id.restype  = ctypes.c_uint
        self._lib.pb_get_firmware_id.argtypes = []

        self._lib.pb_status_message.restype  = ctypes.c_char_p
        self._lib.pb_status_message.argtypes = []

        self._lib.pb_read_status.restype  = ctypes.c_uint32
        self._lib.pb_read_status.argtypes = []

        # ── Debug control ─────────────────────────────────────────────────────
        self._lib.pb_set_debug.restype  = ctypes.c_int
        self._lib.pb_set_debug.argtypes = [ctypes.c_int]

        # ── Programming sequence ──────────────────────────────────────────────
        self._lib.pb_start_programming.restype  = ctypes.c_int
        self._lib.pb_start_programming.argtypes = [ctypes.c_int]

        self._lib.pb_stop_programming.restype  = ctypes.c_int
        self._lib.pb_stop_programming.argtypes = []

        # spinapi.h declares pb_inst_pbonly as:
        #   int pb_inst_pbonly(unsigned int flags, int inst, int inst_data, double length)
        # 'flags' is unsigned int — using signed int here would corrupt any
        # flags value with bit 31 set (the sign bit in a signed 32-bit int).
        self._lib.pb_inst_pbonly.restype  = ctypes.c_int
        self._lib.pb_inst_pbonly.argtypes = [
            ctypes.c_uint,    # flags    : unsigned int — bit pattern of active channels
            ctypes.c_int,     # inst     : signed int   — opcode (CONTINUE, STOP, LOOP …)
            ctypes.c_int,     # inst_data: signed int   — opcode-specific parameter
            ctypes.c_double,  # length   : double       — instruction duration in nanoseconds
        ]

        # ── Execution control ─────────────────────────────────────────────────
        self._lib.pb_start.restype  = ctypes.c_int
        self._lib.pb_start.argtypes = []

        self._lib.pb_stop.restype  = ctypes.c_int
        self._lib.pb_stop.argtypes = []

        self._lib.pb_reset.restype  = ctypes.c_int
        self._lib.pb_reset.argtypes = []

    def on_deactivate(self):
        """ Deinitialization performed during deactivation of the module. """
        self.stop()
        self.close_connection()

    # =========================================================================
    # Low-level DLL wrapper methods
    # =========================================================================

    def check(self, func_val):
        """ Check the return value of a SpinAPI call and log any error.

        @param int func_val: Return value from the called DLL function.
        @return int: The same value is returned so callers can chain checks.

        All SpinAPI functions return 0 on success and a negative integer on
        failure. The special undocumented code -91 indicates that an instruction
        length was shorter than the minimum allowed by the hardware; this is only
        reported via the error string in debug mode, so a fallback message is
        provided here.
        """
        if func_val < 0:
            err_str = self.get_error_string()

            # Error code -91 means instruction too short. The SpinAPI only
            # provides the description text when debug mode is enabled; provide
            # a helpful fallback for when debug mode is off.
            if func_val == -91 and err_str == '':
                err_str = (
                    'Instruction length is shorter than the minimum allowed length! '
                    'Depending on your device this must be at least 5-7 clock cycles. '
                    'Check the board manual for the exact value and update min_instr_len '
                    '(or board_model) in the config accordingly.'
                )

            self.log.error(
                'PulseBlaster error code {0}:\n{1}'.format(func_val, err_str)
            )
        return func_val

    def get_error_string(self):
        """ Return the most recent error string from the SpinAPI library.

        @return str: Human-readable error description, or 'No Error' if none.
        """
        return self._lib.pb_get_error().decode('utf-8')

    def count_boards(self):
        """ Return the number of SpinCore boards detected in the system.

        @return int: Number of boards present, or -1 on error.
        """
        return self._lib.pb_count_boards()

    def select_board(self, board_num=0):
        """ Select which SpinCore board to communicate with.

        @param int board_num: Zero-based board index. Only needed when multiple
                              SpinCore boards are installed.
        @return int: Chosen board number, or -1 on failure.
        """
        if not isinstance(board_num, int):
            self.log.error(
                'PulseBlaster select_board expects an integer, '
                'got: {0}'.format(board_num)
            )
            return
        self.check(self._lib.pb_select_board(board_num))

    def set_debug_mode(self, value):
        """ Enable or disable SpinAPI debug logging to log.txt.

        @param bool value: True to enable debug output, False to disable.
        @return bool: The new debug mode state.
        """
        self._debug_mode = value
        self._lib.pb_set_debug(int(value))
        return self._debug_mode

    def get_debug_mode(self):
        """ Query whether SpinAPI debug logging is currently enabled.

        @return bool: True if debug mode is on.
        """
        return self._debug_mode

    def get_version(self):
        """ Get the SpinAPI library version string.

        @return str: Version in the form 'YYYYMMDD_architecture'. This is a
                     driver/library version, NOT a board model identifier.
        """
        return self._lib.pb_get_version().decode('utf-8')

    def get_firmware_id(self):
        """ Get the firmware version ID stored on the board.

        @return int: Firmware ID value, or 0 if this board does not support it.
                     This is logged for diagnostic reference only -- its
                     mapping to board model is not used anywhere in this
                     module. See module docstring, "BOARD IDENTIFICATION".
        """
        firmware_id = self._lib.pb_get_firmware_id()
        if firmware_id == 0:
            self.log.info('Firmware ID readout is not supported on this board.')
        return firmware_id

    def start(self):
        """ Send a software trigger to start execution of the loaded pulse program.

        @return int: 0 on success, negative on failure.

        Also resumes a program that is paused at a WAIT instruction.
        Hardware triggering is also possible; see the board manual.
        """
        return self.check(self._lib.pb_start())

    def stop(self):
        """ Stop pulse program output.

        @return int: 0 on success, negative on failure.

        TTL outputs remain in their final state or return to ground depending
        on board configuration. The board can be restarted with start() after
        calling reset_device().
        """
        return self.check(self._lib.pb_stop())

    def reset_device(self):
        """ Stop output and reset the PulseBlaster instruction pointer to address 0.

        @return int: 0 on success, negative on failure.

        SpinCore documentation requires reset() or stop() to be called before
        start() to guarantee that execution begins at instruction 0.
        """
        return self.check(self._lib.pb_reset())

    def open_connection(self):
        """ Initialize the board and configure the clock frequency.

        @return int: 0 on success, negative on failure.

        Must be called before any board communication. If multiple boards are
        present, call select_board() first.

        No threadlock here — qudi's framework serializes module calls through
        Qt's event loop, making explicit DLL-level locking unnecessary at
        activation time.
        """
        self.log.debug('Opening connection to SpinCore PulseBlaster.')
        ret_val = self.check(self._lib.pb_init())
        self._set_core_clock(self.SAMPLE_RATE)
        return ret_val

    def close_connection(self):
        """ End communication with the board.

        @return int: 0 on success, negative on failure.

        Any pulse program currently running will continue to run after this call.
        """
        self.log.debug('Closing connection to SpinCore PulseBlaster.')
        return self.check(self._lib.pb_close())

    def start_programming(self):
        """ Begin a pulse program upload session.

        @return int: 0 on success, negative on failure.

        Must be followed by one or more _write_pulse() calls and then
        stop_programming(). Only one programming session can be open at a time.
        This implementation always programs PULSE_PROGRAM (the TTL output
        program), as this board has no DDS or analog outputs.
        """
        return self.check(self._lib.pb_start_programming(self.PULSE_PROGRAM))

    def stop_programming(self):
        """ Finalize and close the pulse program upload session.

        @return int: 0 on success, negative on failure.
        """
        return self.check(self._lib.pb_stop_programming())

    def _set_core_clock(self, clock_freq):
        """ Inform the SpinAPI driver of the board's clock oscillator frequency.

        @param float clock_freq: Oscillator frequency in Hz.

        IMPORTANT: This does NOT change the physical clock. It only tells the
        driver what frequency to use when converting nanosecond durations into
        clock cycle counts. The actual frequency is determined by the crystal
        oscillator soldered to the board (printed on the board label, or found
        in the board's datasheet). If this value does not match the board's
        real oscillator, every timed instruction on every channel will be
        uniformly stretched or compressed by the mismatch ratio -- this fails
        silently, with no error, so it is worth double-checking against the
        actual hardware model (or using the board_model profile) rather than
        assuming a value.

        pb_core_clock() expects the frequency in MHz, so Hz is divided by 1e6.
        """
        clock_mhz = ctypes.c_double(clock_freq / 1e6)
        return self._lib.pb_core_clock(clock_mhz)

    def _write_pulse(self, flags, inst, inst_data, length):
        """ Write one instruction line to the PulseBlaster pulse program.

        @param int flags:     Bitmask of active TTL output channels, plus
                              (only if use_output_control_bits=True) the
                              reserved short-pulse control bits 21-23. Bit N
                              controls channel N (0-indexed). Valid range:
                              0x0 – 0xFFFFFF.
        @param int inst:      Instruction opcode. One of the class constants:
                              CONTINUE, STOP, LOOP, END_LOOP, JSR, RTS,
                              BRANCH, LONG_DELAY, WAIT, RTI.
        @param int inst_data: Opcode-specific argument:
                              LOOP       → desired number of loop iterations (≥ 1)
                              END_LOOP   → address of the matching LOOP instruction
                              BRANCH/JSR → target instruction address
                              LONG_DELAY → repetition multiplier (≥ 2)
                              All others → ignored; pass 0.
        @param float length:  Duration of this instruction in seconds.
                              Converted to nanoseconds internally before the DLL call.

        @return int: Memory address of the created instruction. Used as the
                     target for BRANCH, JSR, or END_LOOP instructions.
                     Returns a negative value on failure.

        Opcodes that ignore inst_data (CONTINUE, STOP, RTS, RTI, WAIT) should
        be called with inst_data=0 explicitly. A defensive guard below
        substitutes 0 if None slips through, since None is not a valid C int
        once argtypes are declared.
        """
        if inst_data is None:
            self.log.debug(
                '_write_pulse received inst_data=None for opcode {0}; '
                'substituting 0.'.format(inst)
            )
            inst_data = 0

        # Convert length from seconds to nanoseconds — the DLL expects nanoseconds.
        length_ns = ctypes.c_double(length * 1e9)

        return self.check(
            self._lib.pb_inst_pbonly(flags, inst, inst_data, length_ns)
        )

    def get_status_bit(self):
        """ Read the board status register as an integer bitmask.

        @return int: Bitmask where each set bit indicates a board state:
                     Bit 0 (value 1)  → Stopped
                     Bit 1 (value 2)  → Reset
                     Bit 2 (value 4)  → Running
                     Bit 3 (value 8)  → Waiting
                     Bit 4 (value 16) → Scanning (RadioProcessor boards only)

        The Reset bit is set immediately after pb_init() and remains set until
        a software or hardware trigger occurs.
        """
        return self._lib.pb_read_status()

    def get_status_message(self):
        """ Read a human-readable status string from the board.

        @return str: Board status message or error description.
        """
        return self._lib.pb_status_message().decode('utf-8')

    # =========================================================================
    # Higher-level sequence creation methods
    # =========================================================================

    def write_pulse_form(self, sequence_list, loop=True):
        """ Program a complete pulse sequence to the board.

        @param list sequence_list: Sequence as a list of instruction dicts:
            [
            {'active_channels': [int, ...], 'length': float},
            ...
            ]
            'active_channels' is a list of 0-based channel indices to set HIGH.
            'length' is the instruction duration in seconds.

        @param bool loop: If True (default), the sequence loops indefinitely by
                        adding a BRANCH back to the first instruction at the end.
                        If False, the sequence runs once and stops.

        @return int: Address of the last written instruction, or negative on error.

        Single-element sequences are handled inline rather than delegating to
        activate_channels(), so that loop=False is respected (a single BRANCH
        instruction would otherwise always loop indefinitely regardless of
        the loop flag) and so that no nested lock acquisition is possible —
        activate_channels() holds self.threadlock, and this method does not.

        Every entry's 'length' must already be >= LEN_MIN. Any entry shorter
        than LEN_MIN that reaches this method will fail at the hardware level
        with SpinAPI error -91 -- see module docstring, "MINIMUM PULSE WIDTH",
        for why this is surfaced as an error here rather than silently
        corrected.
        """

        # Pre-check instruction count before writing anything. Writing
        # partial instructions to the board when we already know we'll
        # exceed the limit would leave the board in an inconsistent state.
        if len(sequence_list) > self._max_instructions:
            self.log.error(
                'PulseBlaster write_pulse_form ABORTED.\n'
                'Sequence requires {0} instructions after RLE compression, '
                'exceeding the configured maximum of {1}.\n'
                'Reduce the number of channel transitions, shorten the '
                'sequence, or enable use_smart_pulse_creation in the '
                'PulseBlaster config to compress long constant segments '
                'further.'.format(len(sequence_list), self._max_instructions)
            )
            return -1

        # ── Single-instruction sequence ───────────────────────────────────────
        if len(sequence_list) == 1:
            active_channels = sequence_list[0]['active_channels']
            length          = sequence_list[0]['length']
            bitmask         = self._convert_to_bitmask(active_channels)

            # Ensure length meets the minimum instruction requirement
            length = max(length, self.LEN_MIN)

            self.start_programming()

            if loop:
                # Infinite loop: single BRANCH-to-self instruction holds
                # the channel pattern indefinitely
                retval = self._write_pulse(
                    flags=self._output_flag_base | bitmask,
                    inst=self.BRANCH,
                    inst_data=0,   # address 0: branch to this same instruction
                    length=length
                )
            else:
                # Run-once: write STOP instead of BRANCH
                retval = self._write_pulse(
                    flags=self._output_flag_base | bitmask,
                    inst=self.STOP,
                    inst_data=0,
                    length=length
                )

            self.stop_programming()
            return retval

        # ── Multi-instruction sequence ────────────────────────────────────────
        self.start_programming()

        # Write the first instruction and record its address.
        # For a looping sequence this address is the BRANCH target at the end.
        start_pulse = self._convert_pulse_to_inst(
            sequence_list[0]['active_channels'],
            sequence_list[0]['length']
        )

        write_failed = False
        for pulse in sequence_list[1:-1]:
            num = self._convert_pulse_to_inst(
                pulse['active_channels'],
                pulse['length']
            )
            if num > self._max_instructions - 2:  # reserve room for final + branch
                self.log.error(
                    'Instruction count {0} exceeds board maximum ({1}) '
                    'mid-sequence. Aborting write.'
                    ''.format(num, self._max_instructions)
                )
                write_failed = True
                break

        if write_failed:
            self.stop_programming()
            return -1

        # ── Final instruction: loop (BRANCH) or run-once (STOP) ───────────────
        active_channels = sequence_list[-1]['active_channels']
        length          = sequence_list[-1]['length']
        bitmask         = self._convert_to_bitmask(active_channels)

        # For old boards without LONG_DELAY in the terminal instruction,
        # split a very long final pulse into a compressible part and a
        # short remainder that will become the terminal instruction.
        if self._use_smart_pulse_creation and length > 256 * self.GRAN_MIN:
            self._convert_pulse_to_inst(active_channels, length - 128 * self.GRAN_MIN)
            length = 128 * self.GRAN_MIN

        # Round to the nearest valid clock cycle boundary
        length = np.round(np.round(length / self.GRAN_MIN + 0.01) * self.GRAN_MIN, 12)

        if loop:
            # Infinite loop: branch back to the first instruction
            num = self._write_pulse(
                flags=self._output_flag_base | bitmask,
                inst=self.BRANCH,
                inst_data=start_pulse,  # address of the first instruction
                length=length
            )
        else:
            # Run-once: halt after this instruction
            num = self._write_pulse(
                flags=self._output_flag_base | bitmask,
                inst=self.STOP,
                inst_data=0,
                length=length
            )

        if num > self._max_instructions:
            self.log.error(
                'Final instruction count {0} exceeds board maximum ({1}). '
                'Sequence write FAILED — board state may be inconsistent. '
                'Call reset_device() before retrying.'
                ''.format(num, self._max_instructions)
            )
            self.stop_programming()
            return -1

        self.stop_programming()
        return num

    def _convert_pulse_to_inst(self, active_channels, length):
        """ Convert one sequence row to one (or more) board instructions.

        @param list active_channels: 0-based channel indices to set HIGH.
        @param float length: Duration in seconds.
        @return int: Address of the first instruction created for this pulse.

        For standard operation (use_smart_pulse_creation=False), every pulse is
        written as a single CONTINUE instruction.

        For smart pulse creation mode (use_smart_pulse_creation=True), long
        pulses are compressed using LONG_DELAY to reduce instruction memory usage.
        A LONG_DELAY instruction of (value × factor) clock cycles uses only one
        instruction slot rather than 'factor' separate ones.
        """
        channel_bitmask = self._convert_to_bitmask(active_channels)

        # Round length to the nearest valid clock cycle boundary.
        # The +0.01 shifts the rounding threshold slightly away from exact
        # multiples of GRAN_MIN to avoid ambiguity (e.g. 13.0 vs 13.01 cycles).
        old_length = length
        length = np.round(np.round(length / self.GRAN_MIN + 0.01) * self.GRAN_MIN, 12)

        residual = old_length - length
        if not np.isclose(residual, 0.0, atol=1e-12):
            self.log.warning(
                'Pulse length {0:.6e} s does not align to clock granularity '
                '{1:.2e} s. Rounded: {2:.2e} s dropped.'.format(
                    old_length, self.GRAN_MIN, residual
                )
            )

        if self._use_smart_pulse_creation:

            if length <= 256 * self.GRAN_MIN:
                # Short pulse: fits in a single CONTINUE instruction
                num = self._write_pulse(
                    flags=self._output_flag_base | channel_bitmask,
                    inst=self.CONTINUE,
                    inst_data=0,
                    length=length
                )

            else:
                # Long pulse: attempt to factorize into value × factor
                # and use LONG_DELAY to repeat a shorter instruction 'factor' times,
                # saving ('factor' - 1) instruction slots.
                remaining_time = length
                i = 4
                while True:
                    num_clock_cycles = int(length / self.GRAN_MIN)
                    value, factor    = self._factor(num_clock_cycles)

                    if value > 4:
                        if factor == 1:
                            # Number is prime or has no factor ≤ 256;
                            # write as a plain CONTINUE
                            num = self._write_pulse(
                                flags=self._output_flag_base | channel_bitmask,
                                inst=self.CONTINUE,
                                inst_data=0,
                                length=value * self.GRAN_MIN
                            )
                        elif factor < 1048576:  # 2^20, maximum LONG_DELAY repeat count
                            # Use LONG_DELAY: execute for (value * factor) clock cycles
                            # using only one instruction slot
                            num = self._write_pulse(
                                flags=self._output_flag_base | channel_bitmask,
                                inst=self.LONG_DELAY,
                                inst_data=int(factor),   # repeat count
                                length=value * self.GRAN_MIN
                            )
                        else:
                            self.log.error(
                                'LONG_DELAY repetition count {0} exceeds the '
                                'maximum of 2^20 = 1048576. Adjust pulse '
                                'parameters.'.format(factor)
                            )

                        # If we had to peel off 'i' short cycles to make the
                        # remainder factorisable, write those cycles now
                        if i > 4:
                            self._write_pulse(
                                flags=self._output_flag_base | channel_bitmask,
                                inst=self.CONTINUE,
                                inst_data=0,
                                length=i * self.GRAN_MIN
                            )
                        break

                    # No valid factor found yet; peel off one more clock cycle
                    # and try again
                    i += 1
                    length = remaining_time - i * self.GRAN_MIN

        else:
            # Standard mode: every pulse is a single CONTINUE instruction
            num = self._write_pulse(
                flags=self._output_flag_base | channel_bitmask,
                inst=self.CONTINUE,
                inst_data=0,
                length=length
            )

        return num

    def _convert_to_bitmask(self, active_channels):
        """ Convert a list of channel indices to an integer bitmask.

        @param list active_channels: 0-based channel indices, e.g. [0, 3, 7].
        @return int: Integer where bit N is 1 if channel N is in active_channels.
                     E.g. [0, 1, 3] → 0b00001011 = 11.

        The rightmost bit (bit 0) corresponds to channel 0.
        Use bin(result) in Python to inspect the binary representation.
        """
        bits = 0
        for channel in active_channels:
            bits = bits | (1 << channel)
        return bits

    def _factor(self, number):
        """ Find a factorization of 'number' as (divisor, quotient) with divisor ≤ 256.

        @param int number: Integer to factorise.
        @return tuple(int, int): (divisor, quotient) where divisor × quotient == number
                                 and divisor ≤ 256. Returns (1, number) if no such
                                 divisor exists (i.e. number is prime or its smallest
                                 factor exceeds 256).

        Used by the smart pulse creation algorithm to compress long delays.
        The divisor becomes the per-iteration clock count in a LONG_DELAY
        instruction, and the quotient becomes the LONG_DELAY repetition count.
        Starting from 256 gives preference to larger blocks (fewer instructions).
        """
        div = 256
        while div > 4:
            if number % div == 0:
                return div, number // div
            div -= 1
        return 1, number

    def _correct_sequence_for_delays(self, sequence):
        """ Adjust a pulse sequence to compensate for per-channel propagation delays.

        @param list sequence: Theoretical sequence, list of dicts with keys
                              'active_channels' (list of int, 0-based) and
                              'length' (float, seconds).
        @return list: Delay-corrected sequence in the same format.

        When a channel has a known cable or output propagation delay, its
        transitions must be scheduled earlier so that the signal arrives at the
        measurement point at the correct time relative to other channels.

        Delays are specified in the config as:
            channel_delays:
                '0': 200e-9    # channel 0 has 200 ns delay
                '2': 500e-9    # channel 2 has 500 ns delay

        Works by converting the sequence into a list of channel edge events,
        shifting each event earlier by its channel's configured delay
        (wrapping around the sequence period), and then reconstructing the
        RLE sequence from the shifted, re-sorted events.

        If no channel_delays are configured, the sequence is returned
        unmodified -- including any entries shorter than LEN_MIN, which will
        then surface as a clear -91 error from the hardware rather than
        being silently rounded here. See module docstring, "MINIMUM PULSE
        WIDTH". The round/drop cleanup below only exists to clean up
        sub-minimum fragments that the delay-shifting math itself can
        introduce as a side effect -- it is not a general-purpose waveform
        sanitizer.
        """
        # Nothing to correct if no delays are configured
        if len(self._channel_delays) == 0:
            return sequence

        # Build per-channel delay array (index = channel number, values in
        # seconds), sized to match the configured number of channels for
        # this board rather than a hardcoded value.
        delays = np.zeros(self._num_channels)
        for entry in self._channel_delays:
            delays[int(entry)] = self._channel_delays[entry]

        # ── Convert sequence to a list of edge events ─────────────────────────
        # An event describes a single channel transition:
        #   {'channel': int, 'direction': bool (True=rising edge), 'time': float}
        # 'always_on' tracks channels that are HIGH in every instruction
        # (they need to be OR-ed back in after event reconstruction).
        last_state = set(sequence[-1]['active_channels'])
        always_on  = set(sequence[-1]['active_channels'])
        time       = 0.0
        events     = []

        for pulse in sequence:
            new_state  = set(pulse['active_channels'])
            always_on &= new_state              # intersect: keep only universally-on channels

            toggle_on  = new_state - last_state  # channels going LOW → HIGH
            toggle_off = last_state - new_state  # channels going HIGH → LOW

            for channel in toggle_on:
                events.append({'channel': channel, 'direction': True,  'time': time})
            for channel in toggle_off:
                events.append({'channel': channel, 'direction': False, 'time': time})

            time      += pulse['length']
            last_state = new_state

        total_time = time

        # ── Shift each event earlier by that channel's delay ──────────────────
        # Modulo total_time wraps events that shift before t=0 to the end of
        # the sequence period (the sequence is periodic by design).
        for event in events:
            event['time'] -= delays[event['channel']]
            event['time'] %= total_time

        # Sort events chronologically after the time shifts
        events = sorted(events, key=lambda x: x['time'])

        # ── Determine the channel state at t=0 after shifting ─────────────────
        # Walk through all events to find the state left by the last event,
        # which becomes the initial state at the sequence boundary.
        last_state = set()
        for event in events:
            if event['direction']:
                last_state |= {event['channel']}
            else:
                last_state -= {event['channel']}

        # ── Reconstruct the corrected sequence from events ────────────────────
        corrected_sequence = []
        time  = 0.0
        state = last_state

        for event in events:
            duration = event['time'] - time
            corrected_sequence.append({
                'active_channels': list(state | always_on),
                'length': duration
            })
            if event['direction']:
                state |= {event['channel']}
            else:
                state -= {event['channel']}
            time += duration

        # Append the final segment from the last event to the end of the period
        corrected_sequence.append({
            'active_channels': list(state | always_on),
            'length': total_time - time
        })

        # ── Filter and fix sub-minimum-length pulses ──────────────────────────
        # Iterates over corrected_sequence and builds a new list
        # (cleaned_sequence) rather than removing elements from the list
        # being iterated over, since that would silently skip entries.
        #
        # This cleanup exists because the delay-shift math above can itself
        # produce new, artificially short fragments near event boundaries
        # (e.g. two nearby edges on different channels shifting to land
        # very close together) -- it is specific to this delay-correction
        # transform, not a general substitute for respecting LEN_MIN
        # elsewhere in the sequence.
        delta_time    = 0.0
        cleaned_sequence = []

        for pulse in corrected_sequence:
            length = pulse['length']

            if length == 0 or length < self.LEN_MIN / 1e3:
                # Effectively zero — floating-point rounding artefact; drop silently
                pass

            elif length < self.LEN_MIN / 2:
                # Too short to round up without excessive timing distortion; drop
                # and log a message so the user is aware.
                self.log.info(
                    'Delay correction produced a {0:.2f} ns pulse (minimum is '
                    '{1:.2f} ns) on channels {2}. Pulses shorter than half the '
                    'minimum are dropped.'.format(
                        length * 1e9, self.LEN_MIN * 1e9, pulse['active_channels']
                    )
                )
                delta_time -= length   # track total time removed

            elif self.LEN_MIN / 2 <= length < self.LEN_MIN:
                # Close to minimum: round up and keep, log the adjustment
                self.log.info(
                    'Delay correction produced a {0:.2f} ns pulse (minimum is '
                    '{1:.2f} ns) on channels {2}. Rounded up to {1:.2f} ns.'.format(
                        length * 1e9, self.LEN_MIN * 1e9, pulse['active_channels']
                    )
                )
                delta_time      += self.LEN_MIN - length   # track time added
                pulse['length']  = self.LEN_MIN
                cleaned_sequence.append(pulse)

            else:
                # Normal-length pulse: keep unchanged
                cleaned_sequence.append(pulse)

        if delta_time > 0:
            self.log.warning(
                'Delay correction has extended the total sequence length by '
                '{0:.2f} ns. New total: {1:.6e} s. Account for this in '
                'acquisition timing if needed.'.format(
                    delta_time * 1e9, total_time + delta_time
                )
            )

        return cleaned_sequence

    # =========================================================================
    # Switch-like static channel control
    # =========================================================================

    def activate_channels(self, ch_list, length=None, immediate_start=True):
        """ Hold a set of channels statically HIGH (all others LOW).

        @param list ch_list:        0-based channel indices to set HIGH.
                                    An empty list sets all channels LOW.
        @param float length:        Loop iteration period in seconds.
                                    Defaults to self.LEN_MIN if not specified.
        @param bool immediate_start: If True (default), the board is reset and
                                    started immediately after programming.

        @return int: Address of the created instruction (normally 0).

        "Constantly on" is implemented by writing a single BRANCH instruction
        that loops back to itself. The 'length' parameter sets the loop period
        only — it does NOT limit how long the output stays high. Any value
        >= LEN_MIN is functionally equivalent; LEN_MIN minimises latency.

        This board holds only one program in memory at a time, so calling
        this overwrites whatever waveform was previously loaded. Callers that
        need to resume a previously loaded waveform afterward (see
        pulser_on()/pulser_off() below) are responsible for tracking that and
        re-writing it.

        This is the one method here that acquires self.threadlock, since it
        is the entry point most likely to be called directly from another
        thread (e.g. the GUI switch panel via SwitchInterface). It is never
        called from write_pulse_form(), avoiding any possibility of nested
        lock acquisition.
        """
        if length is None:
            length = self.LEN_MIN

        with self.threadlock:
            bitmask = self._convert_to_bitmask(ch_list)

            self.start_programming()
            retval = self._write_pulse(
                flags=self._output_flag_base | bitmask,
                inst=self.BRANCH,
                inst_data=0,     # branch to address 0: self-loop
                length=length
            )
            self.stop_programming()

            if immediate_start:
                # reset_device() must precede start() per SpinCore docs, to
                # guarantee execution begins at instruction 0.
                self.reset_device()
                self.start()

            return retval

    # =========================================================================
    # SwitchInterface implementation
    # =========================================================================

    def getNumberOfSwitches(self):
        """ Return the total number of available digital output channels.

        @return int: The number of TTL outputs on this board, as resolved
                     via board_model / individual config options.
        """
        return len(self._switch_states)

    def _get_switch_state(self, switch_num):
        """ Return the tracked ON/OFF state of a channel by its 1-based number.

        @param int switch_num: 1-based channel number (1 = d_ch0, 2 = d_ch1 …).
        @return bool: True if ON, False if OFF.
        """
        return self._switch_states['d_ch{0}'.format(switch_num)]

    def getCalibration(self, switch_num, switch_state):
        """ Return the voltage associated with a given switch state.

        @param int switch_num:   Channel number (unused; all channels identical).
        @param str switch_state: 'On' or 'Off'.
        @return float: 3.3 V for 'On', 0.0 V for 'Off' (fixed LVTTL levels).
        """
        possible_states = {'On': 3.3, 'Off': 0.0}
        return possible_states[switch_state]

    def setCalibration(self, switch_num, switch_state, value):
        """ Attempt to set output voltage (not supported; levels are fixed in hardware).

        @return bool: Always True (command ignored with a warning).
        """
        self.log.warning(
            'PulseBlaster output voltages are fixed at 3.3 V / 0 V (LVTTL). '
            'setCalibration() ignored.'
        )
        return True

    def _set_switch_on(self, switch_num):
        """ Turn a single output channel ON and update the board immediately.

        @param int switch_num: 1-based channel number.
        @return bool: True (the new state of the channel).

        Updates the internal state dict, then reprograms the board with all
        currently-ON channels held high via activate_channels().
        """
        self._switch_states['d_ch{0}'.format(switch_num)] = True

        # Collect all channels currently tracked as ON (0-based indices for the DLL)
        ch_list = [
            int(entry.replace('d_ch', ''))
            for entry in self._switch_states
            if self._switch_states[entry]
        ]

        self.activate_channels(ch_list=ch_list, length=self.LEN_MIN, immediate_start=True)

        return self._switch_states['d_ch{0}'.format(switch_num)]

    def _set_switch_off(self, switch_num):
        """ Turn a single output channel OFF and update the board immediately.

        @param int switch_num: 1-based channel number.
        @return bool: False (the new state of the channel).
        """
        self._switch_states['d_ch{0}'.format(switch_num)] = False

        ch_list = [
            int(entry.replace('d_ch', ''))
            for entry in self._switch_states
            if self._switch_states[entry]
        ]

        self.activate_channels(ch_list=ch_list, length=self.LEN_MIN, immediate_start=True)

        return self._switch_states['d_ch{0}'.format(switch_num)]

    def getSwitchTime(self, switch_num):
        """ Return the estimated time to change this switch state.

        @param int switch_num: Channel number (unused; same for all channels).
        @return float: ~1 ms (limited by PCI/USB communication latency).
        """
        return 0.001

    # =========================================================================
    # PulserInterface implementation
    # =========================================================================

    def get_constraints(self):
        """ Return the hardware constraints for this pulse generator.

        @return PulserConstraints: Constraints object with min/max sample rate,
                                   voltage levels, waveform lengths, and
                                   supported channel configurations.
        """
        constraints = PulserConstraints()

        # Sample rate is fixed by the on-board oscillator; it cannot be changed.
        constraints.sample_rate.min     = self._clock_freq
        constraints.sample_rate.max     = self._clock_freq
        constraints.sample_rate.step    = 0.0
        constraints.sample_rate.default = self._clock_freq

        # Digital output voltages are fixed by LVTTL hardware (0 V low, 3.3 V high)
        constraints.d_ch_low.min     = 0.0
        constraints.d_ch_low.max     = 0.0
        constraints.d_ch_low.step    = 0.0
        constraints.d_ch_low.default = 0.0

        constraints.d_ch_high.min     = 3.3
        constraints.d_ch_high.max     = 3.3
        constraints.d_ch_high.step    = 0.0   # fixed in hardware, cannot be adjusted
        constraints.d_ch_high.default = 3.3

        # Waveform length is measured in clock cycles.
        # Minimum: min_instr_len cycles (hardware constraint, typically 5-7).
        # Maximum: nominally unbounded at the sample level; the real ceiling
        # is the number of RLE instructions the board can hold
        # (self._max_instructions), not the raw sample count.
        constraints.waveform_length.min     = self._min_instr_len
        constraints.waveform_length.max     = 2 ** 20 - 1
        constraints.waveform_length.step    = 1
        constraints.waveform_length.default = 128

        # Channel activation configurations available to the sequencer logic.
        # 'all' exposes every configured output channel for this board model.
        activation_config = dict()
        activation_config['4_ch'] = frozenset(
            {'d_ch{0}'.format(i) for i in range(min(4, self._num_channels))}
        )
        activation_config['all']  = frozenset(
            {'d_ch{0}'.format(i) for i in range(self._num_channels)}
        )
        constraints.activation_config = activation_config

        return constraints

    def pulser_on(self):
        """ Start the pulse program.

        On boards where zero_output_on_stop_workaround is enabled (see
        module docstring, "OUTPUT LEVEL AFTER STOP"), pulser_off() may have
        overwritten the board's single program memory slot with a trivial
        all-zero pattern in order to force a real 0V output. If that has
        happened (tracked via self._board_holds_real_waveform) and a real
        waveform was previously loaded, that waveform is transparently
        re-written to the board here before starting, so that playback
        resumes the intended sequence rather than the zero-filler pattern.
        reset_device() is required in that case to guarantee playback
        begins at instruction 0 of the freshly-written program.

        On boards where the workaround is disabled (the default, and the
        confirmed-correct behavior for the ESR-PRO series), this is simply
        the plain start().

        @return int: 0 on success, negative on failure.
        """
        if self._zero_output_on_stop_workaround:
            if not self._board_holds_real_waveform and self._current_pb_waveform_name:
                self.write_pulse_form(self._current_pb_waveform)
                self._board_holds_real_waveform = True
                self.reset_device()

        return self.start()

    def pulser_off(self):
        """ Stop the pulse program.

        On boards where zero_output_on_stop_workaround is enabled (see
        module docstring, "OUTPUT LEVEL AFTER STOP"), this additionally
        forces a real 0V output by reprogramming the board with a trivial
        all-channels-LOW self-looping instruction via activate_channels()
        and briefly starting it, since plain stop() alone leaves outputs
        frozen at whatever pattern was last active -- confirmed on
        oscilloscope for the PBUSB-TTL-24-100-4K. Since this board holds
        only one program at a time, this overwrites the loaded waveform in
        board memory; pulser_on() transparently restores it before
        resuming playback.

        On boards where the workaround is disabled (the default, and the
        confirmed-correct behavior for the ESR-PRO series), this is simply
        the plain stop().

        @return int: 0 on success, negative on failure.
        """
        if self._zero_output_on_stop_workaround:
            self.activate_channels(ch_list=[])
            self._board_holds_real_waveform = False

        return self.stop()

    def load_waveform(self, load_dict):
        """ Load and arm a previously written waveform for playback.

        @param dict|list load_dict: A dict {channel: name} or list of names.
                                    Exactly one unique waveform name is accepted;
                                    PulseBlaster holds only one program at a time.
        @return dict: Loaded asset name per channel index.
        """
        if isinstance(load_dict, list):
            waveforms = list(set(load_dict))
        elif isinstance(load_dict, dict):
            waveforms = list(set(load_dict.values()))
        else:
            self.log.error('load_waveform expects a list or dict of waveform names.')
            return self.get_loaded_assets()[0]

        if len(waveforms) != 1:
            self.log.error(
                'PulseBlaster accepts exactly one waveform at a time; '
                '{0} names provided.'.format(len(waveforms))
            )
            return self.get_loaded_assets()[0]

        waveform = waveforms[0]
        if waveform != self._current_pb_waveform_name:
            self.log.error(
                'Waveform "{0}" not available. Only the most recently written '
                'waveform ("{1}") can be loaded.'.format(
                    waveform, self._current_pb_waveform_name
                )
            )
            return self.get_loaded_assets()[0]

        self.write_pulse_form(self._current_pb_waveform)
        self._currently_loaded_waveform = waveform
        # The board's memory now holds the real waveform (as opposed to the
        # all-zero filler pattern that pulser_off() may write on boards
        # where zero_output_on_stop_workaround is enabled).
        self._board_holds_real_waveform = True
        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """ Not supported: PulseBlaster has no sequence memory.

        @return dict: Empty dict.
        """
        self.log.warning(
            'PulseBlaster has no sequencing capability. load_sequence() ignored.'
        )
        return {}

    def get_loaded_assets(self):
        """ Return the currently programmed waveform name for each active channel.

        @return (dict, str): {channel_index: waveform_name} and asset type string
                             ('waveform' or None if nothing is loaded).
        """
        asset_type = 'waveform' if self._currently_loaded_waveform else None
        asset_dict = {}
        for index, entry in enumerate(self._current_activation_config):
            asset_dict[index + 1] = self._current_pb_waveform_name
        return asset_dict, asset_type

    def clear_all(self):
        """ Clear all waveform state (does not change board hardware output).

        @return int: 0 on success.
        """
        self._currently_loaded_waveform        = ''
        self._current_pb_waveform_name         = ''
        self._current_pb_waveform              = [{'active_channels': [], 'length': self.LEN_MIN}]
        self._current_pb_waveform_theoretical  = [{'active_channels': [], 'length': self.LEN_MIN}]
        self._board_holds_real_waveform        = False
        return 0

    def get_status(self):
        """ Return the current pulsing hardware status.

        @return (int, dict): Status integer (0=Idle, 1=Running) and
                             {0: 'Idle', 1: 'Running'} description dict.
        """
        num = self.get_status_bit()
        # Status values 0 (unknown), 1 (Stopped), 2 (Reset) are all considered idle
        state = 0 if num in [0, 1, 2] else 1
        return state, {0: 'Idle', 1: 'Running'}

    def get_sample_rate(self):
        """ Return the board's fixed sample rate in Hz.

        @return float: Clock frequency in Hz.
        """
        return self.SAMPLE_RATE

    def set_sample_rate(self, sample_rate):
        """ Attempt to change sample rate (ignored; rate is fixed in hardware).

        @param float sample_rate: Ignored.
        @return float: Actual (unchanged) sample rate in Hz.
        """
        self.log.warning(
            'PulseBlaster sample rate is fixed by the on-board oscillator and '
            'cannot be changed. Command ignored.'
        )
        return self.get_sample_rate()

    def get_analog_level(self, amplitude=None, offset=None):
        """ Return analog channel levels (not applicable; board is digital-only).

        @return (dict, dict): Empty dicts — no analog channels present.
        """
        return dict(), dict()

    def set_analog_level(self, amplitude=None, offset=None):
        """ Set analog levels (not supported; board is digital-only).

        @return (dict, dict): Empty dicts.
        """
        return {}, {}

    def get_digital_level(self, low=None, high=None):
        """ Return the digital low and high voltage levels for the given channels.

        @param list low:  Optional list of channel name strings for low-level query.
        @param list high: Optional list of channel name strings for high-level query.
        @return (dict, dict): (low_voltages, high_voltages) keyed by channel name.

        All channels are fixed LVTTL: 0.0 V low, 3.3 V high, consistently
        across both the default (no-argument) and specific-channel cases,
        matching get_constraints().
        """
        if low:
            low_dict = {chnl: 0.0 for chnl in low}
        else:
            low_dict = {'d_ch{0:d}'.format(chnl): 0.0 for chnl in range(self._num_channels)}

        if high:
            high_dict = {chnl: 3.3 for chnl in high}
        else:
            high_dict = {'d_ch{0:d}'.format(chnl): 3.3 for chnl in range(self._num_channels)}

        return low_dict, high_dict

    def set_digital_level(self, low=None, high=None):
        """ Attempt to change digital voltage levels (not supported; levels are fixed).

        @return (dict, dict): Current (unchanged) digital levels.
        """
        self.log.warning(
            'PulseBlaster output levels are fixed at 3.3 V / 0 V and cannot '
            'be adjusted. Command ignored.'
        )
        return self.get_digital_level()

    def get_active_channels(self, ch=None):
        """ Return the activation state of specified or all channels.

        @param list ch: Optional list of channel name strings. All channels
                        returned if None.
        @return dict: {channel_name: bool} where True means the channel is
                      in the current activation configuration.
        """
        if ch is None:
            ch = list(self._channel_states.keys())
        return {channel: channel in self._current_activation_config for channel in ch}

    def set_active_channels(self, ch=None):
        """ Change the active channel configuration.

        @param dict ch: {channel_name: bool} mapping. E.g.
                        {'d_ch1': True, 'd_ch3': False}.
        @return dict: Resulting activation state for the requested channels.

        The resulting active channel set must match one of the configurations
        in get_constraints().activation_config; otherwise the change is rejected.
        """
        if ch is None:
            ch = {}

        old_activation = self._channel_states.copy()

        for channel in ch:
            self._channel_states[channel] = ch[channel]

        active_channel_set = {
            chnl for chnl, is_active in self._channel_states.items() if is_active
        }

        if active_channel_set not in self.get_constraints().activation_config.values():
            self.log.error(
                'Requested channel configuration is not in the hardware constraints. '
                'Channel activation unchanged.'
            )
            self._channel_states = old_activation
        else:
            self._current_activation_config = active_channel_set

        return self.get_active_channels(ch=list(ch))

    def write_waveform(self, name, analog_samples, digital_samples,
                       is_first_chunk, is_last_chunk, total_number_of_samples):
        """ Convert digital sample arrays and program them as a pulse sequence.

        @param str name:               Waveform identifier string.
        @param numpy.ndarray analog_samples:   Must be empty (not supported).
        @param dict digital_samples:   {channel_name: bool_array} mapping.
                                       Each bool array has one entry per sample point.
        @param bool is_first_chunk:    True when this is the first data chunk.
        @param bool is_last_chunk:     True when this is the last data chunk.
        @param int total_number_of_samples: Total sample count (all chunks combined).

        @return (int, list): Number of samples processed (-1 on error) and
                             list containing the waveform name.

        Converts sample arrays into a compressed run-length-encoded sequence
        (_convert_sample_to_pb_sequence), applies delay corrections
        (_correct_sequence_for_delays), and programs the board (write_pulse_form)
        on the final chunk.

        Multi-chunk support: if the waveform is delivered in multiple calls,
        adjacent chunks are merged at their boundary when the channel state
        matches, so no extra instructions are generated at chunk boundaries.
        """
        analog_samples  = netobtain(analog_samples)
        digital_samples = netobtain(digital_samples)

        if analog_samples:
            self.log.error(
                'PulseBlaster is a purely digital device and does not support '
                'analog samples. write_waveform() failed.'
            )
            return -1, list()

        if not digital_samples:
            if total_number_of_samples > 0:
                self.log.warning(
                    'write_waveform() called with no digital samples but '
                    'total_number_of_samples > 0. No waveform written.'
                )
                return -1, list()
            else:
                # Empty waveform request: reset state to idle
                self._current_pb_waveform_theoretical = [{'active_channels': [], 'length': self.LEN_MIN}]
                self._current_pb_waveform             = [{'active_channels': [], 'length': self.LEN_MIN}]
                self._current_pb_waveform_name        = ''
                return 0, list()

        # Determine sorted channel names and chunk length from the sample arrays
        chan         = sorted(digital_samples.keys())
        chunk_length = len(digital_samples[chan[0]])
        self._current_activation_config = chan

        if is_first_chunk:
            # Convert sample arrays to run-length-encoded sequence
            self._current_pb_waveform_theoretical = \
                self._convert_sample_to_pb_sequence(digital_samples)
            self._current_pb_waveform_name = name

        else:
            # Append this chunk's sequence to the existing one
            pb_waveform_temp = self._convert_sample_to_pb_sequence(digital_samples)

            # Merge the boundary entry if the channel state is identical on both sides
            if (self._current_pb_waveform_theoretical[-1]['active_channels'] ==
                    pb_waveform_temp[0]['active_channels']):
                self._current_pb_waveform_theoretical[-1]['length'] += pb_waveform_temp[0]['length']
                pb_waveform_temp.pop(0)

            self._current_pb_waveform_theoretical.extend(pb_waveform_temp)

        if is_last_chunk:
            # Apply delay corrections, then program the board
            self._current_pb_waveform = self._correct_sequence_for_delays(
                self._current_pb_waveform_theoretical
            )
            self.write_pulse_form(self._current_pb_waveform)
            self._board_holds_real_waveform = True
            self.log.debug(
                'Waveform "{0}" programmed: {1} instruction entries.'.format(
                    self._current_pb_waveform_name,
                    len(self._current_pb_waveform)
                )
            )

        return chunk_length, [self._current_pb_waveform_name]

    def _convert_sample_to_pb_sequence(self, digital_samples):
        """ Convert per-sample bool arrays into a run-length-encoded sequence.

        @param dict digital_samples: {channel_name: bool_array} where channel
                                     names are strings like 'd_ch1', 'd_ch2', …
        @return list: Run-length-encoded sequence:
                      [{'active_channels': [int, ...], 'length': float}, ...]
                      where 'active_channels' are 0-based channel indices and
                      'length' is in seconds.

        Groups consecutive samples with the same channel state into single
        entries, drastically reducing the number of board instructions needed.
        Each sample point represents one clock cycle (GRAN_MIN seconds).

        Example: three consecutive samples all with channel 0 high becomes
        one entry: {'active_channels': [0], 'length': 3 * GRAN_MIN}
        """
        ch_list     = sorted(digital_samples.keys())
        num_entries = len(digital_samples[ch_list[0]])

        last_sequence_dict = None
        pb_sequence_list   = []

        for index in range(num_entries):

            # Build the set of active channels for this single sample point.
            # Each sample represents one clock cycle (GRAN_MIN).
            temp_sequence_dict = {
                'active_channels': [],
                'length': self.GRAN_MIN
            }

            for ch_name in ch_list:
                if digital_samples[ch_name][index]:
                    # Convert 'd_ch0' → 0, 'd_ch1' → 1, …
                    temp_sequence_dict['active_channels'].append(
                        int(ch_name.replace('d_ch', ''))
                    )

            if last_sequence_dict is None:
                # First sample: initialise the run
                last_sequence_dict = temp_sequence_dict

            else:
                if (temp_sequence_dict['active_channels'] ==
                        last_sequence_dict['active_channels']):
                    # Same channel state: extend the current run by one cycle
                    last_sequence_dict['length'] += temp_sequence_dict['length']

                else:
                    # Channel state changed: finalise the previous run
                    # Warn if the run is shorter than the minimum instruction length.
                    # 1.01× comparison provides a small tolerance for float comparison.
                    if last_sequence_dict['length'] * 1.01 < self.LEN_MIN:
                        self.log.warning(
                            'Pulse of {0:.2f} ns is below the minimum '
                            'instruction length of {1:.2f} ns. The output '
                            'may not look as expected.'.format(
                                last_sequence_dict['length'] * 1e9,
                                self.LEN_MIN * 1e9
                            )
                        )
                    pb_sequence_list.append(last_sequence_dict)
                    last_sequence_dict = temp_sequence_dict

        # Append the final run (always at least one entry)
        pb_sequence_list.append(last_sequence_dict)
        return pb_sequence_list

    def write_sequence(self, name, sequence_parameters):
        """ Not supported: PulseBlaster has no sequence memory.

        @return int: -1 (not supported).
        """
        self.log.warning(
            'PulseBlaster has no sequencing capability. write_sequence() ignored.'
        )
        return -1

    def get_waveform_names(self):
        """ Return the name of the currently held waveform (only one at a time).

        @return list: Single-element list with the current waveform name,
                      or a list containing an empty string if none is loaded.
        """
        return [self._current_pb_waveform_name]

    def get_sequence_names(self):
        """ Return all stored sequence names (always empty; not supported).

        @return list: Empty list.
        """
        return list()

    def delete_waveform(self, waveform_name):
        """ Delete a waveform (no-op; PulseBlaster has no persistent storage).

        @param str waveform_name: Ignored.
        @return list: Empty list.
        """
        self.log.info('PulseBlaster has no waveform storage; delete_waveform() ignored.')
        return list()

    def delete_sequence(self, sequence_name):
        """ Delete a sequence (no-op; PulseBlaster has no sequence storage).

        @param str sequence_name: Ignored.
        @return list: Empty list.
        """
        return list()

    def get_interleave(self):
        """ Return interleave state (always False; not supported).

        @return bool: False.
        """
        return False

    def set_interleave(self, state=False):
        """ Attempt to enable interleave mode (not supported).

        @param bool state: If True, logs an error.
        @return bool: Always False.
        """
        if state:
            self.log.error('PulseBlaster does not support interleave mode.')
        return False

    def reset(self):
        """ Reset the device via the PulserInterface.

        @return int: 0 on success, negative on failure.
        """
        return self.reset_device()

    def has_sequence_mode(self):
        """ Report whether sequence mode is available (it is not).

        @return bool: False.
        """
        return False

    # =========================================================================
    # SwitchInterface property implementations
    # =========================================================================

    @property
    def name(self):
        """ Hardware module name string.

        @return str: The qudi module name for this hardware instance.
        """
        return self.module_name

    @property
    def available_states(self):
        """ Describe the available states for each switch (channel).

        @return dict: {channel_name: (False, True)} for all configured channels.
                      False = OFF, True = ON.
        """
        return {ch: (False, True) for ch in self._switch_states.keys()}

    def _switch_name_to_num(self, switch):
        """ Convert a channel name string to its integer channel number.

        @param str switch: Channel name, e.g. 'd_ch3'.
        @return int: Channel number (e.g. 3 for 'd_ch3'), or None on error.
        """
        if switch not in self._switch_states.keys():
            self.log.error('Unknown channel name: {0}'.format(switch))
            return None
        return int(switch.replace('d_ch', ''))

    def get_state(self, switch):
        """ Return the current ON/OFF state of a named switch.

        @param str switch: Channel name string, e.g. 'd_ch1'.
        @return bool: True if ON, False if OFF.
        """
        return self._get_switch_state(self._switch_name_to_num(switch))

    def set_state(self, switch, state):
        """ Set the ON/OFF state of a named switch.

        @param str switch: Channel name string, e.g. 'd_ch1'.
        @param bool state: True to switch ON, False to switch OFF.
        """
        if state:
            self._set_switch_on(self._switch_name_to_num(switch))
        else:
            self._set_switch_off(self._switch_name_to_num(switch))