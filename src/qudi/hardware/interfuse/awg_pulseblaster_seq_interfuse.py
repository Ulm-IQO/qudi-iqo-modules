# -*- coding: utf-8 -*-
"""
Interfuse combining a Tektronix AWG7000 and a SpinCore PulseBlaster ESR-Pro
into a single PulserInterface for qudi pulsed measurement experiments.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level
directory of this distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.

-------------------------------------------------------------------------------
DESIGN OVERVIEW
-------------------------------------------------------------------------------

Channel naming
  AWG channels (native):   a_ch1, a_ch2, d_ch1, d_ch2, d_ch3, d_ch4
  PulseBlaster channels:   d_ch{offset}, d_ch{offset+1}, ...
  pb_channel_d_offset (default 5) marks the first PB channel: d_ch5 is PB
  hw index 0, d_ch6 is PB hw index 1, etc. AWG channels occupy d_ch1
  through d_ch{offset-1}. All channels look identical to qudi (all d_ch*),
  so the GUI creates voltage widgets, laser/gate dropdowns work, and
  activation-config validation works correctly for all of them.

  INTERNAL REPRESENTATION: everywhere INSIDE this interfuse (sample
  buffers, delay-shift logic, gap validation, caches), PB channel data is
  keyed using qudi's own d_ch naming -- the SAME names visible in the
  pulse block editor -- consistently with every other part of qudi. The
  PulseBlaster hardware module, however, expects its own zero-based hw
  channel index in its 'd_ch<N>' keys (it parses them via
  int(ch_name.replace('d_ch', ''))) -- a completely different numbering
  that happens to reuse the same string format. This mismatch is resolved
  in exactly ONE place, _to_pb_hw_keys(), called only immediately before
  each call to pulseblaster().write_waveform(). This means log/error
  messages anywhere else in this interfuse always show real, recognisable
  qudi d_ch names -- never the PB module's internal indexing.

Waveform naming
  write_waveform('rabi', ...) creates 'awg_rabi_ch1'/'awg_rabi_ch2' on the
  AWG and 'pb_rabi' on the PB. get_waveform_names()/get_loaded_assets()
  also expose the plain logical name 'rabi'.

Granularity
  waveform_length.step = LCM(AWG_native_step, PB_min_instr_cycles * AWG_per_PB).
  This guarantees AWG->PB decimation is always lossless, and that the PB
  minimum instruction length can always be represented. It is delay-
  INDEPENDENT -- it does NOT and cannot guarantee any specific segment
  (e.g. the final idle/wait period) is long enough to absorb the trigger-
  delay shift below; that depends on the actual sequence content, not on
  total-length granularity, and is checked separately (see below).

  PB_min_instr_cycles is read directly from the PulseBlaster hardware
  module's own get_constraints().waveform_length.min -- NOT duplicated as
  a separate config option here, so it can't drift out of sync with that
  module's board_model/min_instr_len setting.

  IMPORTANT: AWG_per_PB is computed from the PulseBlaster's REPORTED clock
  rate. If that doesn't match the board's actual oscillator, every PB
  timed interval will be uniformly stretched/compressed by the mismatch
  ratio, with no error anywhere in this interfuse's math. Verify the PB
  module's own clock-rate config against the board's actual oscillator
  before suspecting a bug here.

Trigger delay compensation
  awg_trigger_delay = measured time from the PB trigger edge to the
  AWG's first actual output sample. write_waveform()/write_sequence()
  compensate for this by circularly shifting every PB channel EXCEPT the
  configured trigger channel (awg_trigger_pb_channel) later in time by
  this amount; the trigger channel itself stays fixed as the reference.

  This shift can create a too-short gap between any two transitions
  (trigger-to-content, trigger-to-trigger, or content-to-content) that
  did not exist before shifting -- and the PulseBlaster's own hardware
  error (-91) is NOT reliable for catching every such case (if two
  transitions land on the exact same sample index, there is no nonzero
  short run for PB's own RLE logic to flag). This interfuse therefore
  measures the ACTUAL shifted result directly -- see
  _validate_min_gap_after_shift() -- and refuses to upload if any gap
  anywhere is shorter than the PB minimum instruction length, naming the
  exact (qudi-named) channels and location responsible. It never
  silently pads, merges, or otherwise rewrites channel data to work
  around this -- the fix is always for the user to adjust
  awg_trigger_delay, the trigger pulse width, or the sequence's idle/wait
  time, and re-sample.

EXPERIMENTAL: pb_extra_wait_time
  Extra idle time appended to the END of every PB loop cycle, making
  PB's total loop period LONGER than the AWG's actual playback duration
  by this fixed margin. The AWG's own uploaded waveform is completely
  UNCHANGED -- only the PB-side loop grows. This gives the AWG extra
  time to fully re-arm/settle after finishing one triggered playback
  before PB fires the next trigger edge.

  This was added while investigating a reproducible every-other-trigger
  drop observed across multiple different AWG7000-series units (ruling
  out AWG-hardware- and PulseBlaster-clock-specific causes). Set to 0.0
  (default) to disable -- this is a no-op at 0.0, verified by code path,
  not just by value.

  The padding is applied BEFORE the trigger-delay roll and minimum-gap
  validation, on every write/re-write path, so the roll and validation
  always see the true, final loop content -- never the un-padded content
  with padding bolted on afterward (which could reintroduce exactly the
  too-short-gap problems _validate_min_gap_after_shift() exists to catch).

  IMPORTANT: _pb_waveform_store / _pb_waveform_store_sizes cache the RAW,
  UN-padded, UN-shifted content (its size is the true one-cycle content
  length, used as-is by write_sequence()'s per-step tiling math). Padding
  is re-applied FRESH from this raw cache every time it's used (in
  write_waveform, write_sequence, and load_waveform's re-write path) --
  it is never baked into that cache, so changing pb_extra_wait_time and
  reloading (without re-sampling) picks up the new value correctly.

Waveform mode (TRIG/GAT)
  PB loops: [trigger][waveform content][extra wait, if configured].
  AWG: one waveform per trigger.

Sequence mode
  User draws AWG trigger in the first element of their sequence (same
  channel as waveform mode). AWG step 1 has TWAIT=ON forced by
  write_sequence(). PB loops the tiled content of all steps (trigger
  naturally falls in the first step), plus the extra wait tail if
  configured. AWG runs all steps once per trigger, then waits for the
  next one. Identical workflow to waveform mode from the user's
  perspective.

Re-write-on-load
  Both PulseBlaster instruction memory and the AWG's SEQ list are
  single-slot: writing any waveform/sequence wipes out whatever was there
  before. load_waveform()/load_sequence() detect when the requested asset
  isn't the one currently programmed on hardware and transparently
  RE-WRITE it from cached raw sample data (stored by write_waveform()/
  write_sequence()) before loading.

Start / stop ordering
  trigger_master = 'pulseblaster': arm AWG first, then start PB.
  trigger_master = 'awg':          reverse order.

Diagnostics
  debug_channel_routing (bool, default False): logs every digital
  channel's AWG-vs-PB classification and PB hardware index on every
  write_waveform() call, plus HIGH-duration diagnostics for the channel
  named by debug_watch_channel.

Example config:

awg_pb_interfuse:
    module.Class: 'interfuse.awg_pulseblaster_interfuse.AwgPulseBlasterInterfuse'
    connect:
        awg: 'pulser_awg7000'
        pulseblaster: 'pulser_pulseblaster'
    options:
        trigger_master: 'pulseblaster'
        awg_trigger_delay: 300e-9
        awg_trigger_pb_channel: 'd_ch9'
        pb_channels: [0, 1, 2, 3, 4, 5, 6, 7, 8]
        pb_channel_d_offset: 5
        pb_extra_wait_time: 5e-6   # EXPERIMENTAL -- see module docstring
        default_activation_config: 'A2_M3_M4_pb9'
        debug_channel_routing: False
        debug_watch_channel: 'd_ch10'
"""

from math import gcd
import time
import numpy as np
import re

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.interface.pulser_interface import PulserInterface, PulserConstraints, SequenceOption
from qudi.util.helpers import natural_sort


class AwgPulseBlasterInterfuse(PulserInterface):
    """
    Single PulserInterface coordinating a Tektronix AWG7000 and
    SpinCore PulseBlaster ESR-Pro for qudi pulsed measurement experiments.
    """

    # ── Connectors ────────────────────────────────────────────────────────
    awg          = Connector(interface=PulserInterface)
    pulseblaster = Connector(interface=PulserInterface)

    # ── Config options ───────────────────────────────────────────────────
    _trigger_master         = ConfigOption('trigger_master', default='pulseblaster', missing='warn')
    _awg_trigger_delay       = ConfigOption('awg_trigger_delay', default=0.0, missing='nothing')
    _awg_trigger_pb_channel  = ConfigOption('awg_trigger_pb_channel', default=None, missing='nothing')
    _pb_channels             = ConfigOption('pb_channels', default=[0, 1, 2, 3], missing='warn')
    _pb_channel_d_offset     = ConfigOption('pb_channel_d_offset', default=5, missing='nothing')
    _default_activation_config = ConfigOption('default_activation_config', default=None, missing='nothing')
    _debug_channel_routing   = ConfigOption('debug_channel_routing', default=False, missing='nothing')
    _debug_watch_channel     = ConfigOption('debug_watch_channel', default=None, missing='nothing')

    # EXPERIMENTAL -- see module docstring, "EXPERIMENTAL: pb_extra_wait_time".
    _pb_extra_wait_time = ConfigOption('pb_extra_wait_time', default=0.0, missing='nothing')

    # =========================================================================
    # Module lifecycle
    # =========================================================================

    def on_activate(self):
        """Initialise internal state and compute rate-dependent parameters."""
        self._written_waveform_names = []
        self._written_sequence_names = []
        self._loaded_name            = ''
        self._loaded_type            = None

        # Caches enabling re-write-on-load. Both PB instruction memory and
        # the AWG's SEQ list are single-slot hardware: writing anything new
        # wipes out whatever was there before. These caches let
        # load_waveform()/load_sequence() transparently re-write a
        # previously-uploaded asset if something else has since overwritten
        # it on the actual hardware.
        #
        # All PB channel data cached/buffered here is keyed using qudi's
        # OWN d_ch naming -- see module docstring, "Channel naming",
        # "INTERNAL REPRESENTATION". Only _to_pb_hw_keys() translates to
        # the PB module's own indexing, right before each hardware call.
        #
        # _pb_waveform_store holds RAW content: no padding, no shift.
        # _pb_seq_store holds the FULLY-PROCESSED combined sequence
        # waveform: padded AND shifted, ready to re-write as-is.
        self._pb_waveform_store       = {}   # pb_name     -> {qudi d_ch: np.ndarray} (RAW)
        self._pb_waveform_store_sizes = {}   # pb_name     -> int (RAW length, no padding)
        self._pb_seq_store            = {}   # pb_seq_name -> {qudi d_ch: np.ndarray} (padded + shifted)
        self._pb_seq_store_sizes      = {}   # pb_seq_name -> int (padded length)
        self._awg_seq_param_store     = {}   # logical seq name -> sequence_parameter_list

        self._pb_sample_buffer    = {}
        self._pb_current_wfm_name = ''

        self._pb_active_channels = {
            self._pb_index_to_d_ch(i): False for i in range(len(self._pb_channels))
        }

        self._update_rate_params()

        if self._default_activation_config is not None:
            self._apply_default_activation_config()

    def on_deactivate(self):
        pass

    # =========================================================================
    # Private helpers — channel naming
    # =========================================================================

    def _extract_d_ch_number(self, d_ch_name):
        """
        Extract the integer N from a 'd_ch<N>' name (any number of digits).
        Returns None for non-'d_ch*' names (e.g. 'a_ch1') -- a normal,
        expected input, not a parsing failure. Logs an error only if the
        name starts with 'd_ch' but doesn't match the expected pattern.
        """
        if not isinstance(d_ch_name, str) or not d_ch_name.startswith('d_ch'):
            return None
        match = re.fullmatch(r'd_ch(\d+)', d_ch_name)
        if match is None:
            self.log.error(
                'Could not parse digital channel number from name "{0}". '
                'Expected format "d_ch<N>".'.format(d_ch_name))
            return None
        return int(match.group(1))

    def _is_pb_d_ch(self, d_ch_name):
        """
        True if d_ch_name (a QUDI-facing name) belongs to the
        PulseBlaster: d_ch{offset} and above. AWG channels occupy
        d_ch1 .. d_ch{offset-1}.
        """
        ch_num = self._extract_d_ch_number(d_ch_name)
        return ch_num is not None and ch_num >= self._pb_channel_d_offset

    def _d_ch_to_pb_hw(self, d_ch_name):
        """
        Convert a QUDI-facing 'd_ch{offset+i}' name to the PB module's
        own zero-based hw index i. Returns None if out of range or not a
        PB channel. Used ONLY for validation and for the final
        translation step in _to_pb_hw_keys() -- see module docstring.
        """
        ch_num = self._extract_d_ch_number(d_ch_name)
        if ch_num is None or ch_num < self._pb_channel_d_offset:
            return None
        hw_ch = ch_num - self._pb_channel_d_offset
        if hw_ch >= len(self._pb_channels):
            self.log.error(
                'Channel "{0}" maps to PB hw index {1}, outside the '
                'configured pb_channels list (length {2}).'
                .format(d_ch_name, hw_ch, len(self._pb_channels)))
            return None
        return hw_ch

    def _pb_index_to_d_ch(self, list_index):
        """Inverse of _d_ch_to_pb_hw: PB hw index i -> QUDI 'd_ch{offset+i}'."""
        return 'd_ch{0:d}'.format(self._pb_channel_d_offset + list_index)

    def _all_pb_d_ch_names(self):
        """All configured PB channels, as QUDI-facing d_ch* names."""
        return [self._pb_index_to_d_ch(i) for i in range(len(self._pb_channels))]

    def _to_pb_hw_keys(self, qudi_keyed_dict):
        """
        Translate a dict keyed by QUDI-facing d_ch names (e.g. 'd_ch7')
        into a dict keyed by the PulseBlaster module's OWN zero-based hw
        channel index format (e.g. 'd_ch2'), as required by that module's
        write_waveform()/_convert_sample_to_pb_sequence() parsing
        (int(ch_name.replace('d_ch', ''))).

        This is the ONLY place this translation happens in the entire
        interfuse -- every dict everywhere else (buffers, caches, shift
        results, validation input) is keyed with qudi's own d_ch naming.
        Call this immediately before, and only before, each call to
        pulseblaster().write_waveform().
        """
        out = {}
        for qudi_name, samples in qudi_keyed_dict.items():
            hw_ch = self._d_ch_to_pb_hw(qudi_name)
            if hw_ch is None:
                self.log.error(
                    'Internal error: "{0}" does not resolve to a PB hw '
                    'channel at upload time. Skipped.'.format(qudi_name)
                )
                continue
            out['d_ch{0:d}'.format(hw_ch)] = samples
        return out

    @staticmethod
    def _lcm(a, b):
        """Least common multiple of two positive integers."""
        return abs(a * b) // gcd(a, b)

    @staticmethod
    def _logical_name(awg_wfm_name):
        """'awg_rabi_ch1' -> 'rabi'."""
        name = awg_wfm_name
        if name.startswith('awg_'):
            name = name[4:]
        if '_ch' in name:
            name = name.rsplit('_ch', 1)[0]
        return name

    # =========================================================================
    # Private helpers — device state / logging
    # =========================================================================

    def _ensure_stopped(self, context=''):
        """
        Stop both AWG and PulseBlaster before writing/loading a new asset.
        Both devices can silently reject or defer reprogramming commands
        while armed (status 2) or running (status 1) from a previous
        pulser_on() call.
        """
        awg_status = self.awg().get_status()[0]
        if awg_status in (1, 2):
            self.log.info('{0}: AWG was running/armed. Stopping first.'.format(context))
            self.awg().pulser_off()

        pb_status = self.pulseblaster().get_status()[0]
        if pb_status != 0:
            self.log.info('{0}: PulseBlaster was running. Stopping first.'.format(context))
            self.pulseblaster().pulser_off()

    def _log_pb_instruction_diagnostics(self, pb_name, pb_total):
        """Log PB RLE-compressed instruction count; warn near the 4094 hw limit."""
        try:
            pb_instr_count = len(self.pulseblaster()._current_pb_waveform)
            pb_theoretical = len(self.pulseblaster()._current_pb_waveform_theoretical)
            self.log.info(
                'PB "{0}": {1} samples @ {2:.3e} Hz -> {3} instructions after '
                'RLE compression (theoretical {4}, hardware max 4094).'
                ''.format(pb_name, pb_total, self._pb_sample_rate, pb_instr_count, pb_theoretical)
            )
            if pb_instr_count > 4000:
                self.log.warning(
                    'PB instruction count {0} is close to the hardware max of 4094.'
                    ''.format(pb_instr_count)
                )
        except Exception:
            self.log.debug(
                'Uploaded "{0}" to PulseBlaster: {1} samples @ {2:.3e} Hz.'
                ''.format(pb_name, pb_total, self._pb_sample_rate)
            )

    def _log_channel_routing_debug(self, d_ch_name):
        """Opt-in (debug_channel_routing): logs AWG-vs-PB classification for one channel."""
        is_pb = self._is_pb_d_ch(d_ch_name)
        hw_ch = self._d_ch_to_pb_hw(d_ch_name) if is_pb else None
        self.log.debug('DEBUG routing: "{0}" -> is_pb={1}, pb_hw={2}'.format(d_ch_name, is_pb, hw_ch))

    def _log_watch_channel_duration(self, d_ch_name, samples):
        """Opt-in (debug_channel_routing + debug_watch_channel): HIGH-duration diagnostic."""
        high_count = int(np.sum(samples))
        self.log.info(
            'DEBUG watch "{0}": {1}/{2} AWG samples HIGH = {3:.3f} us (@ {4:.3e} Hz).'
            ''.format(d_ch_name, high_count, len(samples),
                      high_count / self._awg_sample_rate * 1e6, self._awg_sample_rate)
        )

    # =========================================================================
    # Private helpers — trigger delay compensation / extra wait padding
    # =========================================================================

    def _get_trigger_pb_key(self):
        """
        Resolve and validate awg_trigger_pb_channel. Returns the QUDI-
        facing d_ch name itself (e.g. 'd_ch9') for use as a key in the
        qudi-named PB sample dicts used throughout this interfuse, or
        None if not configured or not resolvable -- in that case the
        shift below would apply uniformly to ALL channels including the
        trigger, which is a no-op (the whole periodic pattern just
        phase-shifts together with no relative effect).
        """
        if not self._awg_trigger_pb_channel:
            if self._delay_pb_samples > 0:
                self.log.warning(
                    'awg_trigger_delay is set ({0:.3e} s) but awg_trigger_pb_channel '
                    'is NOT configured. Delay compensation would apply uniformly to '
                    'ALL PB channels (no relative effect). Set awg_trigger_pb_channel '
                    'in the yaml config to the d_ch name driving the AWG trigger BNC.'
                    ''.format(self._awg_trigger_delay)
                )
            return None
        hw_ch = self._d_ch_to_pb_hw(self._awg_trigger_pb_channel)  # validation only
        if hw_ch is None:
            self.log.warning(
                'awg_trigger_pb_channel "{0}" does not resolve to a configured PB '
                'channel; delay compensation would have no effect.'
                ''.format(self._awg_trigger_pb_channel)
            )
            return None
        return self._awg_trigger_pb_channel

    def _append_extra_wait_tail(self, pb_digital_dict):
        """
        EXPERIMENTAL -- see module docstring, "EXPERIMENTAL: pb_extra_wait_time".

        Return a COPY of pb_digital_dict with self._pb_extra_wait_samples
        additional all-LOW samples appended to the END of EVERY channel
        (including the trigger channel, so every channel in the returned
        dict stays the same length as each other).

        MUST be called BEFORE _apply_trigger_delay_roll(), so the
        trigger-delay shift and minimum-gap validation operate on the
        final, true loop length including this padding.

        No-op (returns an unmodified copy) if _pb_extra_wait_samples <= 0
        -- this is a real code-path no-op, not just a zero-valued
        parameter, so pb_extra_wait_time: 0.0 provably changes nothing.
        """
        if self._pb_extra_wait_samples <= 0:
            return {k: v.copy() for k, v in pb_digital_dict.items()}

        padded = {}
        for d_key, samples in pb_digital_dict.items():
            tail = np.zeros(self._pb_extra_wait_samples, dtype=bool)
            padded[d_key] = np.concatenate([samples, tail])
        return padded

    def _find_min_run_length(self, combined_2d):
        """
        Find the shortest constant-state run in a (num_channels, n) boolean
        array representing a CIRCULARLY looping combined multi-channel
        state (PB loops this pattern forever, so the n-1 -> 0 boundary is
        a real transition point too, not an edge case to ignore).

        @return (int, int, int): (min_run_length, start_index, end_index),
                                 indices in the ORIGINAL (input) frame.
        """
        n = combined_2d.shape[1]
        if n <= 1:
            return n, 0, n

        next_cols = np.roll(combined_2d, -1, axis=1)
        change = np.any(combined_2d != next_cols, axis=0)
        change_indices = np.nonzero(change)[0]

        if len(change_indices) == 0:
            return n, 0, n  # never changes -- no transitions anywhere

        # Roll so the array starts one sample after the first transition.
        # This removes wraparound ambiguity: a simple linear run-length
        # pass on the rolled array now correctly reflects the circular
        # run structure of the original.
        shift = -(int(change_indices[0]) + 1)
        rolled = np.roll(combined_2d, shift, axis=1)
        rolled_change = np.any(rolled != np.roll(rolled, -1, axis=1), axis=0)
        rolled_idx = np.nonzero(rolled_change)[0]

        starts = np.concatenate(([0], rolled_idx + 1))
        starts = starts[starts < n]
        ends = np.concatenate((rolled_idx + 1, [n]))[:len(starts)]
        lengths = ends - starts

        min_i = int(np.argmin(lengths))
        min_len = int(lengths[min_i])

        orig_start = (int(starts[min_i]) - shift) % n
        orig_end   = (int(ends[min_i]) - shift) % n
        return min_len, orig_start, orig_end

    def _validate_min_gap_after_shift(self, pb_digital_shifted):
        """
        Directly measure the shortest constant-state run in the FINAL,
        shifted, circularly-looping PB waveform across ALL channels at
        once, and reject if it's shorter than the PB minimum instruction
        length.

        pb_digital_shifted is keyed with QUDI-facing d_ch names, so any
        error message below names real, recognisable channels (as seen
        in the pulse block editor) directly -- no translation needed.

        This measures the actual result rather than reasoning about where
        edges "should" land -- it uniformly catches trigger-to-content,
        trigger-to-trigger, and content-to-content gaps, including cases
        where two transitions land on the exact same sample index (a
        zero-length "run" that the PulseBlaster's own -91 hardware error
        can silently miss, since there's no nonzero-but-short run for its
        RLE logic to flag).

        Never auto-corrects: on failure, logs exactly which channels and
        location caused the violation and returns False. The caller must
        abort the upload -- fixing the underlying sequence (delay, trigger
        width, or idle/wait time) is left to the user.
        """
        keys = sorted(pb_digital_shifted.keys())
        if not keys:
            return True

        combined = np.vstack([pb_digital_shifted[k] for k in keys])
        n = combined.shape[1]
        if n == 0:
            return True

        min_len, run_start, run_end = self._find_min_run_length(combined)

        if min_len < self._pb_min_instr_cycles:
            prev_idx = (run_start - 1) % n
            entering = [keys[i] for i in range(len(keys)) if combined[i, prev_idx] != combined[i, run_start]]
            next_idx = run_end % n
            end_prev = (run_end - 1) % n
            leaving = [keys[i] for i in range(len(keys)) if combined[i, end_prev] != combined[i, next_idx]]

            self.log.error(
                'PulseBlaster minimum-instruction-length violation (measured '
                'directly on the final shifted waveform): shortest gap is {0} '
                'PB samples ({1:.1f} ns); minimum required is {2} PB samples '
                '({3:.1f} ns). Transition INTO this gap caused by channel(s) '
                '{4}; transition OUT caused by channel(s) {5}. Adjust '
                'awg_trigger_delay, the trigger pulse width, or the relevant '
                'element/idle duration in the sequence, then re-sample. '
                'Upload aborted.'
                ''.format(min_len, min_len / self._pb_sample_rate * 1e9,
                          self._pb_min_instr_cycles,
                          self._pb_min_instr_cycles / self._pb_sample_rate * 1e9,
                          entering, leaving)
            )
            return False
        return True

    def _apply_trigger_delay_roll(self, pb_digital_dict):
        """
        Return a COPY of pb_digital_dict (keyed with QUDI-facing d_ch
        names) with the trigger-delay circular shift applied to every
        channel except the trigger channel itself (which stays fixed as
        the time reference), or None if the resulting waveform fails
        _validate_min_gap_after_shift() -- ALL callers must check for
        None and abort rather than upload.

        Callers are expected to have already applied
        _append_extra_wait_tail() (if configured) to pb_digital_dict
        BEFORE calling this, so the shift and validation both see the
        true, final loop content.

        Shared by write_waveform(), write_sequence(), and
        load_waveform()'s cache re-write path, so validation and shifting
        happen identically everywhere PB data is actually rolled.
        """
        trigger_key = self._get_trigger_pb_key()
        shifted = {k: v.copy() for k, v in pb_digital_dict.items()}

        if self._delay_pb_samples > 0:
            for d_key in shifted:
                if d_key == trigger_key:
                    continue
                shifted[d_key] = np.roll(shifted[d_key], self._delay_pb_samples)

        if not self._validate_min_gap_after_shift(shifted):
            return None
        return shifted

    # =========================================================================
    # Private helpers — rate parameters
    # =========================================================================

    def _update_rate_params(self):
        """
        (Re)calculate sample-rate and granularity-dependent parameters.
        Call at on_activate(), after set_sample_rate(), after set_interleave().

        Granularity guarantees (delay-INDEPENDENT -- see module docstring,
        "Trigger delay compensation", for why the delay-vs-sequence-content
        interaction is checked separately, on real data, rather than here):
          1. AWG's own native waveform_length.step
          2. lossless AWG->PB decimation (multiple of awg_per_pb)
          3. PB's minimum instruction length is representable

        NOTE: self._pb_sample_rate is only as accurate as the PB module's
        reported clock rate -- if that doesn't match the board's real
        oscillator, every value computed here is self-consistently wrong
        by that same ratio. Fix that in the PB module's own config, not here.
        """
        awg_c = self.awg().get_constraints()
        pb_c  = self.pulseblaster().get_constraints()

        self._awg_sample_rate = self.awg().get_sample_rate()
        self._pb_sample_rate  = pb_c.sample_rate.default

        ratio = self._awg_sample_rate / self._pb_sample_rate
        self._awg_per_pb = int(round(ratio))
        if abs(ratio - self._awg_per_pb) > 0.01:
            self.log.warning(
                'AWG/PB sample rate ratio {0:.4f} is not close to an integer '
                '(AWG {1:.3e} Hz, PB {2:.3e} Hz). Rounded to {3:d}; small '
                'timing errors may result.'
                ''.format(ratio, self._awg_sample_rate, self._pb_sample_rate, self._awg_per_pb)
            )

        self._pb_min_instr_cycles = int(pb_c.waveform_length.min)

        awg_gran = int(awg_c.waveform_length.step)
        self._lcm_gran = self._lcm(awg_gran, self._pb_min_instr_cycles * self._awg_per_pb)

        self._delay_pb_samples = int(round(self._awg_trigger_delay * self._pb_sample_rate))

        # EXPERIMENTAL -- see module docstring, "EXPERIMENTAL: pb_extra_wait_time".
        self._pb_extra_wait_samples = int(round(self._pb_extra_wait_time * self._pb_sample_rate))

        self.log.info(
            'Interfuse rate params updated:\n'
            '  AWG sample rate : {0:.4e} Hz\n'
            '  PB  clock rate  : {1:.4e} Hz\n'
            '  AWG/PB ratio    : {2:d} AWG samples per PB cycle\n'
            '  PB min instr.   : {3:d} PB cycles  =  {4:.2f} ns\n'
            '  LCM granularity : {5:d} AWG samples =  {6:.2f} ns\n'
            '  Trigger delay   : {7:d} PB samples  =  {8:.2f} ns\n'
            '  PB extra wait   : {9:d} PB samples  =  {10:.2f} ns  [EXPERIMENTAL]'
            ''.format(
                self._awg_sample_rate, self._pb_sample_rate, self._awg_per_pb,
                self._pb_min_instr_cycles,
                self._pb_min_instr_cycles / self._pb_sample_rate * 1e9,
                self._lcm_gran, self._lcm_gran / self._awg_sample_rate * 1e9,
                self._delay_pb_samples,
                self._delay_pb_samples / self._pb_sample_rate * 1e9,
                self._pb_extra_wait_samples,
                self._pb_extra_wait_samples / self._pb_sample_rate * 1e9,
            )
        )

    def _apply_default_activation_config(self):
        """Apply the yaml-specified default_activation_config at startup."""
        constraints = self.get_constraints()
        available   = constraints.activation_config

        if self._default_activation_config not in available:
            self.log.warning(
                'default_activation_config "{0}" not found. Available: {1}. '
                'Starting with all channels inactive.'
                ''.format(self._default_activation_config, list(available.keys()))
            )
            return

        target_set = available[self._default_activation_config]
        all_possible = {**self.awg().get_active_channels(), **self._pb_active_channels}
        self.set_active_channels({ch: (ch in target_set) for ch in all_possible})

        self.log.info(
            'Default activation config "{0}" applied. Active channels: {1}'
            ''.format(self._default_activation_config, sorted(target_set))
        )

    def _get_awg_waveform_length(self, awg_wfm_name):
        """Length in AWG samples of a waveform on the AWG; 0 if undeterminable."""
        try:
            return int(self.awg().query('WLIS:WAV:LENG? "{0}"'.format(awg_wfm_name)))
        except Exception:
            return 0

    # =========================================================================
    # PulserInterface — constraints
    # =========================================================================

    def get_constraints(self):
        """Return merged constraints for the combined AWG + PulseBlaster system."""
        awg_c = self.awg().get_constraints()
        c = PulserConstraints()

        c.sample_rate.min, c.sample_rate.max = awg_c.sample_rate.min, awg_c.sample_rate.max
        c.sample_rate.step, c.sample_rate.default = awg_c.sample_rate.step, awg_c.sample_rate.default

        c.waveform_length.min, c.waveform_length.max = awg_c.waveform_length.min, awg_c.waveform_length.max
        c.waveform_length.step = self._lcm_gran
        c.waveform_length.default = awg_c.waveform_length.default

        c.a_ch_amplitude.min, c.a_ch_amplitude.max = awg_c.a_ch_amplitude.min, awg_c.a_ch_amplitude.max
        c.a_ch_amplitude.step, c.a_ch_amplitude.default = awg_c.a_ch_amplitude.step, awg_c.a_ch_amplitude.default

        c.a_ch_offset.min, c.a_ch_offset.max = awg_c.a_ch_offset.min, awg_c.a_ch_offset.max
        c.a_ch_offset.step, c.a_ch_offset.default = awg_c.a_ch_offset.step, awg_c.a_ch_offset.default

        c.d_ch_low.min, c.d_ch_low.max = awg_c.d_ch_low.min, awg_c.d_ch_low.max
        c.d_ch_low.step, c.d_ch_low.default = awg_c.d_ch_low.step, awg_c.d_ch_low.default

        c.d_ch_high.min, c.d_ch_high.max = awg_c.d_ch_high.min, awg_c.d_ch_high.max
        c.d_ch_high.step, c.d_ch_high.default = awg_c.d_ch_high.step, awg_c.d_ch_high.default

        c.waveform_num.min, c.waveform_num.max = awg_c.waveform_num.min, awg_c.waveform_num.max
        c.waveform_num.step, c.waveform_num.default = awg_c.waveform_num.step, awg_c.waveform_num.default

        c.sequence_num.min, c.sequence_num.max = awg_c.sequence_num.min, awg_c.sequence_num.max
        c.sequence_num.step, c.sequence_num.default = awg_c.sequence_num.step, awg_c.sequence_num.default

        c.subsequence_num.min, c.subsequence_num.max = awg_c.subsequence_num.min, awg_c.subsequence_num.max
        c.subsequence_num.step, c.subsequence_num.default = awg_c.subsequence_num.step, awg_c.subsequence_num.default

        c.repetitions.min, c.repetitions.max = awg_c.repetitions.min, awg_c.repetitions.max
        c.repetitions.step, c.repetitions.default = awg_c.repetitions.step, awg_c.repetitions.default

        c.event_triggers = awg_c.event_triggers
        c.flags          = awg_c.flags

        c.sequence_steps.min, c.sequence_steps.max = awg_c.sequence_steps.min, awg_c.sequence_steps.max
        c.sequence_steps.step, c.sequence_steps.default = awg_c.sequence_steps.step, awg_c.sequence_steps.default

        # Activation configs: each AWG config extended with PB channel subsets,
        # plus a PB-only config for standalone PB tests.
        activation_config = {}
        for cfg_name, awg_ch_set in awg_c.activation_config.items():
            activation_config[cfg_name] = awg_ch_set
            for n_pb in range(1, len(self._pb_channels) + 1):
                pb_subset = frozenset(self._pb_index_to_d_ch(i) for i in range(n_pb))
                activation_config['{0}_pb{1:d}'.format(cfg_name, n_pb)] = awg_ch_set | pb_subset
        activation_config['pb_only'] = frozenset(self._all_pb_d_ch_names())

        c.activation_config = activation_config
        c.sequence_option   = awg_c.sequence_option
        return c

    # =========================================================================
    # PulserInterface — start / stop
    # =========================================================================

    def pulser_on(self):
        """
        Start combined output. Waits for the AWG to report a loaded
        waveform (prevents E11203), drains its stale error queue, then
        arms/starts both devices in the order set by trigger_master.

        @return int: status code from the master device, or -1 on timeout.
        """
        master, timeout, interval = str(self._trigger_master).lower(), 10.0, 0.25
        elapsed = 0.0
        while True:
            loaded, _ = self.awg().get_loaded_assets()
            if loaded and all(v for v in loaded.values()):
                break
            if elapsed >= timeout:
                self.log.error(
                    'pulser_on: AWG reports no loaded waveform after {0:.1f}s. '
                    'Upload and load a waveform before starting.'.format(timeout))
                return -1
            time.sleep(interval)
            elapsed += interval

        try:
            self.awg().get_errors()
        except Exception as exc:
            self.log.debug('pulser_on: could not drain AWG error queue: {0}'.format(exc))

        if master == 'pulseblaster':
            self.awg().pulser_on()
            time.sleep(0.1)
            self.pulseblaster().pulser_on()
        else:
            self.pulseblaster().pulser_on()
            time.sleep(0.1)
            self.awg().pulser_on()

        return self.get_status()[0]

    def pulser_off(self):
        """Stop combined output. Master stopped first to avoid stray triggers."""
        master = str(self._trigger_master).lower()
        if master == 'pulseblaster':
            self.pulseblaster().pulser_off()
            self.awg().pulser_off()
        else:
            self.awg().pulser_off()
            self.pulseblaster().pulser_off()
        return self.get_status()[0]

    # =========================================================================
    # PulserInterface — status / sample rate
    # =========================================================================

    def get_status(self):
        """Return status of the trigger-master device."""
        if str(self._trigger_master).lower() == 'pulseblaster':
            return self.pulseblaster().get_status()
        return self.awg().get_status()

    def get_sample_rate(self):
        return self.awg().get_sample_rate()

    def set_sample_rate(self, sample_rate):
        result = self.awg().set_sample_rate(sample_rate)
        self._update_rate_params()
        return result

    # =========================================================================
    # PulserInterface — analog levels (AWG only)
    # =========================================================================

    def get_analog_level(self, amplitude=None, offset=None):
        return self.awg().get_analog_level(amplitude=amplitude, offset=offset)

    def set_analog_level(self, amplitude=None, offset=None):
        return self.awg().set_analog_level(amplitude=amplitude, offset=offset)

    # =========================================================================
    # PulserInterface — digital levels
    # =========================================================================

    def get_digital_level(self, low=None, high=None):
        """AWG markers queried live; PB channels fixed LVTTL 0.0 V / 3.3 V."""
        pb_names = self._all_pb_d_ch_names()

        if low is None and high is None:
            awg_low, awg_high = self.awg().get_digital_level()
            for ch in pb_names:
                awg_low[ch], awg_high[ch] = 0.0, 3.3
            return awg_low, awg_high

        low_val, high_val = {}, {}
        if low is not None:
            awg_req = [ch for ch in low if not self._is_pb_d_ch(ch)]
            pb_req  = [ch for ch in low if self._is_pb_d_ch(ch)]
            if awg_req:
                awg_l, _ = self.awg().get_digital_level(low=awg_req)
                low_val.update(awg_l)
            low_val.update({ch: 0.0 for ch in pb_req})

        if high is not None:
            awg_req = [ch for ch in high if not self._is_pb_d_ch(ch)]
            pb_req  = [ch for ch in high if self._is_pb_d_ch(ch)]
            if awg_req:
                _, awg_h = self.awg().get_digital_level(high=awg_req)
                high_val.update(awg_h)
            high_val.update({ch: 3.3 for ch in pb_req})

        return low_val, high_val

    def set_digital_level(self, low=None, high=None):
        """AWG markers passed through; PB channels fixed LVTTL, requests ignored."""
        low, high = low or {}, high or {}
        awg_low  = {k: v for k, v in low.items()  if not self._is_pb_d_ch(k)}
        awg_high = {k: v for k, v in high.items() if not self._is_pb_d_ch(k)}
        pb_req   = [k for k in list(low) + list(high) if self._is_pb_d_ch(k)]

        if pb_req:
            self.log.info('PB channels {0} are fixed LVTTL; voltage request ignored.'.format(pb_req))

        return self.awg().set_digital_level(low=awg_low or None, high=awg_high or None)

    # =========================================================================
    # PulserInterface — channel activation
    # =========================================================================

    def get_active_channels(self, ch=None):
        """AWG channels queried live; PB channels from internal state dict."""
        all_active = {**self.awg().get_active_channels(), **self._pb_active_channels}
        if ch is not None:
            all_active = {k: v for k, v in all_active.items() if k in ch}
        return all_active

    def set_active_channels(self, ch=None):
        """
        AWG channels -> awg().set_active_channels().
        PB channels  -> internal dict only. The PB module's own
        activation_config only accepts exactly 4 or 21 channels, so
        calling pulseblaster().set_active_channels() here would hide PB
        channels from the pulse block editor for any other channel count.
        """
        if ch is None:
            return self.get_active_channels()

        awg_ch = {k: v for k, v in ch.items() if not self._is_pb_d_ch(k)}
        pb_ch  = {k: v for k, v in ch.items() if self._is_pb_d_ch(k)}

        if awg_ch:
            self.awg().set_active_channels(awg_ch)

        for d_ch_name, state in pb_ch.items():
            if d_ch_name in self._pb_active_channels:
                self._pb_active_channels[d_ch_name] = state
            else:
                self.log.warning('set_active_channels: "{0}" is not a configured PB channel. Ignored.'.format(d_ch_name))

        return self.get_active_channels()

    # =========================================================================
    # PulserInterface — waveform upload
    # =========================================================================

    def write_waveform(self, name, analog_samples, digital_samples,
                       is_first_chunk, is_last_chunk, total_number_of_samples):
        """
        Upload waveform to AWG ('awg_{name}') and PB ('pb_{name}').
        AWG channels go straight to the AWG UNCHANGED (its uploaded
        length is exactly total_number_of_samples, regardless of
        pb_extra_wait_time). PB channels are downsampled, buffered
        across chunks (keyed with qudi d_ch names throughout), padded
        with the EXPERIMENTAL extra wait tail, delay-shifted (validated),
        translated to PB hw indices, and uploaded to PB as a single block
        on the last chunk -- meaning PB's uploaded loop is
        pb_extra_wait_time LONGER than the AWG's playback whenever that
        option is nonzero.

        @return (int, list): samples written and list of AWG waveform names.
        """
        if is_first_chunk:
            self._ensure_stopped(context='write_waveform')

        awg_digital = {k: v for k, v in digital_samples.items() if not self._is_pb_d_ch(k)}

        pb_digital_raw = {}
        for d_ch_name, samples in digital_samples.items():
            is_pb = self._is_pb_d_ch(d_ch_name)

            if self._debug_channel_routing:
                self._log_channel_routing_debug(d_ch_name)
                if self._debug_watch_channel is not None and d_ch_name == self._debug_watch_channel:
                    self._log_watch_channel_duration(d_ch_name, samples)

            if is_pb:
                if self._d_ch_to_pb_hw(d_ch_name) is not None:   # validation only
                    pb_digital_raw[d_ch_name] = samples          # keyed with QUDI name
                else:
                    self.log.warning('write_waveform: "{0}" has no PB hardware mapping. Skipped.'.format(d_ch_name))

        # AWG upload is untouched by pb_extra_wait_time -- total_number_of_samples
        # is passed through exactly as received.
        awg_name = 'awg_' + name
        awg_written, awg_waveforms = self.awg().write_waveform(
            awg_name, analog_samples, awg_digital, is_first_chunk, is_last_chunk, total_number_of_samples
        )
        if awg_written < 0:
            self.log.error('AWG write_waveform failed for "{0}".'.format(awg_name))
            return -1, []

        if is_first_chunk:
            self._pb_sample_buffer    = {}
            self._pb_current_wfm_name = 'pb_' + name

        for pb_key, samples in pb_digital_raw.items():
            downsampled = samples[::self._awg_per_pb].copy()
            if pb_key in self._pb_sample_buffer:
                self._pb_sample_buffer[pb_key] = np.concatenate([self._pb_sample_buffer[pb_key], downsampled])
            else:
                self._pb_sample_buffer[pb_key] = downsampled

        if is_last_chunk:
            if self._pb_sample_buffer:
                # RAW (un-padded, un-shifted) full period, qudi-keyed --
                # cached for write_sequence()'s tiling and for
                # load_waveform()'s re-write path. Padding/shift are
                # applied fresh from this cache every time it's used, so
                # they always reflect the CURRENT config values.
                pb_digital_raw_full = {k: v.copy() for k, v in self._pb_sample_buffer.items()}
                pb_raw_total = len(next(iter(pb_digital_raw_full.values())))

                pb_digital_padded  = self._append_extra_wait_tail(pb_digital_raw_full)
                pb_digital_shifted = self._apply_trigger_delay_roll(pb_digital_padded)
                if pb_digital_shifted is None:
                    self.log.error('write_waveform: ABORTED for "{0}" -- see validation error above.'.format(name))
                    self._pb_sample_buffer, self._pb_current_wfm_name = {}, ''
                    return -1, []

                pb_name = self._pb_current_wfm_name
                pb_padded_total = len(next(iter(pb_digital_shifted.values())))

                # Translate to PB's own hw-index keys ONLY at this final
                # boundary -- see module docstring, "INTERNAL REPRESENTATION".
                pb_written, _ = self.pulseblaster().write_waveform(
                    pb_name, {}, self._to_pb_hw_keys(pb_digital_shifted), True, True, pb_padded_total
                )
                self._pb_sample_buffer, self._pb_current_wfm_name = {}, ''

                if pb_written < 0:
                    self.log.error('PulseBlaster write_waveform failed for "{0}".'.format(pb_name))
                    return -1, []

                # Cache size is the RAW (un-padded) length -- write_sequence()'s
                # per-step tiling math depends on this being the true
                # one-cycle content length, not the padded upload length.
                self._pb_waveform_store[pb_name]       = {k: v.copy() for k, v in pb_digital_raw_full.items()}
                self._pb_waveform_store_sizes[pb_name] = pb_raw_total
                self._log_pb_instruction_diagnostics(pb_name, pb_padded_total)
            else:
                self.log.debug('write_waveform: "{0}" has no PB channel content; skipping PB upload.'.format(name))
                self._pb_current_wfm_name = ''

        if is_last_chunk and name not in self._written_waveform_names:
            self._written_waveform_names.append(name)

        return total_number_of_samples, awg_waveforms

    # =========================================================================
    # PulserInterface — sequence upload
    # =========================================================================

    def write_sequence(self, name, sequence_parameter_list):
        """
        Write AWG sequence ('awg_{name}') and build a combined PB waveform
        ('pb_seq_{name}') covering one full sequence cycle, by tiling each
        step's cached (RAW, un-padded, un-shifted, qudi-keyed) PB content,
        then applying the EXPERIMENTAL extra wait tail and the
        trigger-delay shift exactly once to the combined result before
        translating to PB hw indices for upload. The AWG's own sequence
        is completely unaffected by pb_extra_wait_time.

        @return int: number of sequence steps written, or -1 on failure.
        """
        awg_seq_name = 'awg_' + name

        max_steps = self.awg().get_constraints().sequence_steps.max
        if len(sequence_parameter_list) > max_steps:
            self.log.error(
                'write_sequence ABORTED: "{0}" requires {1} steps, exceeding '
                'AWG max of {2}. Reduce the number of tau/measurement points.'
                ''.format(name, len(sequence_parameter_list), max_steps)
            )
            return -1

        self._ensure_stopped(context='write_sequence')

        try:
            self.awg().get_errors()
        except Exception:
            pass

        result = self.awg().write_sequence(awg_seq_name, sequence_parameter_list)
        if result < 0:
            self.log.error('AWG write_sequence failed for "{0}".'.format(awg_seq_name))
            return result

        awg_run_mode = str(getattr(self.awg(), '_run_mode_config', 'TRIG')).upper()
        if awg_run_mode != 'CONT':
            self.awg().sequence_set_wait_trigger(1, 'ON')
        else:
            self.awg().sequence_set_wait_trigger(1, 'OFF')

        # Build combined PB waveform by tiling each step's cached content
        # (still RAW/un-padded/un-shifted, qudi-keyed, at this point).
        pb_combined, total_pb_len = {}, 0

        for wfm_tuple, seq_params in sequence_parameter_list:
            logical = self._logical_name(wfm_tuple[0])
            pb_key  = 'pb_' + logical
            reps    = int(seq_params.get('repetitions', 0)) + 1

            pb_samples = self._pb_waveform_store.get(pb_key, {})
            pb_size    = self._pb_waveform_store_sizes.get(pb_key, 0)

            if not pb_samples or pb_size == 0:
                try:
                    awg_len = self._get_awg_waveform_length(wfm_tuple[0])
                    pb_size = max(1, awg_len // self._awg_per_pb)
                except Exception:
                    pb_size = 1
                pb_samples = {}

            step_len = pb_size * reps

            for d_ch, samples in pb_samples.items():
                tiled = np.tile(samples, reps)
                if d_ch in pb_combined:
                    pb_combined[d_ch] = np.concatenate([pb_combined[d_ch], tiled])
                else:
                    pb_combined[d_ch] = np.concatenate([np.zeros(total_pb_len, dtype=bool), tiled])

            for d_ch in list(pb_combined.keys()):
                new_total = total_pb_len + step_len
                if len(pb_combined[d_ch]) < new_total:
                    pb_combined[d_ch] = np.concatenate(
                        [pb_combined[d_ch], np.zeros(new_total - len(pb_combined[d_ch]), dtype=bool)]
                    )

            total_pb_len += step_len

        if not pb_combined or total_pb_len == 0:
            self.log.warning('write_sequence: no PB content for "{0}"; PB not updated.'.format(name))
            self._awg_seq_param_store[name] = sequence_parameter_list
            if name not in self._written_sequence_names:
                self._written_sequence_names.append(name)
            return result

        # EXPERIMENTAL extra wait tail applied to the fully-tiled combined
        # waveform, BEFORE the trigger-delay roll -- see module docstring.
        pb_combined_padded  = self._append_extra_wait_tail(pb_combined)
        pb_combined_shifted = self._apply_trigger_delay_roll(pb_combined_padded)
        if pb_combined_shifted is None:
            self.log.error('write_sequence: ABORTED for "{0}" -- see validation error above.'.format(name))
            return -1

        pb_seq_name = 'pb_seq_' + name
        total_pb_padded_len = len(next(iter(pb_combined_shifted.values())))

        pb_written, _ = self.pulseblaster().write_waveform(
            pb_seq_name, {}, self._to_pb_hw_keys(pb_combined_shifted), True, True, total_pb_padded_len
        )
        if pb_written < 0:
            self.log.error('Failed to upload combined PB waveform "{0}".'.format(pb_seq_name))
            return -1

        # Cached data is FULLY PROCESSED (padded AND shifted) -- load_sequence()
        # re-writes this as-is, no need to re-pad or re-shift there.
        self._pb_seq_store[pb_seq_name]       = {k: v.copy() for k, v in pb_combined_shifted.items()}
        self._pb_seq_store_sizes[pb_seq_name] = total_pb_padded_len
        self._awg_seq_param_store[name]       = sequence_parameter_list

        self._log_pb_instruction_diagnostics(pb_seq_name, total_pb_padded_len)

        if name not in self._written_sequence_names:
            self._written_sequence_names.append(name)

        return result

    # =========================================================================
    # PulserInterface — loading
    # =========================================================================

    def load_waveform(self, load_dict):
        """
        Load a waveform on the AWG and (re-write + load) on the PB, since
        PB only ever holds the most recently written waveform. The PB
        re-write re-applies the EXPERIMENTAL extra wait tail and the
        trigger-delay shift FRESH from the RAW cache every time, so
        changing pb_extra_wait_time or awg_trigger_delay and reloading
        (without re-sampling) picks up the new value correctly.

        @return dict: loaded assets per channel (logical names).
        """
        self._ensure_stopped(context='load_waveform')

        if isinstance(load_dict, list):
            new_dict = {}
            for wfm_name in load_dict:
                ch_num = int(wfm_name.rsplit('_ch', 1)[1]) if '_ch' in wfm_name else 1
                new_dict[ch_num] = wfm_name
            load_dict = new_dict

        if not load_dict:
            self.log.error('load_waveform received an empty load dict.')
            return self.get_loaded_assets()[0]

        awg_load_dict, logical_name = {}, None
        for ch_num, wfm_name in load_dict.items():
            if wfm_name.startswith('awg_'):
                awg_wfm, logical = wfm_name, self._logical_name(wfm_name)
            else:
                base = wfm_name.rsplit('_ch', 1)[0] if '_ch' in wfm_name else wfm_name
                awg_wfm, logical = 'awg_{0}_ch{1}'.format(base, ch_num), base
            awg_load_dict[ch_num] = awg_wfm
            logical_name = logical

        awg_result = self.awg().load_waveform(awg_load_dict)

        awg_load_ok = all(
            (awg_result.get(ch_num) if isinstance(awg_result, dict) else None) == expected
            for ch_num, expected in awg_load_dict.items()
        )
        if not awg_load_ok:
            self.log.error('load_waveform: AWG load verification failed for "{0}".'.format(logical_name))
            return self.get_loaded_assets()[0]

        pb_name, pb_load_ok = 'pb_{0}'.format(logical_name), True

        if pb_name in self._pb_waveform_store:
            pb_digital_padded  = self._append_extra_wait_tail(self._pb_waveform_store[pb_name])
            pb_digital_shifted = self._apply_trigger_delay_roll(pb_digital_padded)

            if pb_digital_shifted is None:
                pb_load_ok = False
                self.log.error(
                    'load_waveform: trigger delay validation failed while re-writing '
                    '"{0}" from cache. Asset will NOT be marked as loaded.'.format(pb_name)
                )
            else:
                pb_padded_total = len(next(iter(pb_digital_shifted.values())))
                pb_written, _ = self.pulseblaster().write_waveform(
                    pb_name, {}, self._to_pb_hw_keys(pb_digital_shifted),
                    True, True, pb_padded_total
                )
                if pb_written < 0:
                    pb_load_ok = False
                    self.log.error('load_waveform: re-writing PB waveform "{0}" failed.'.format(pb_name))
                else:
                    self.pulseblaster().load_waveform([pb_name])
                    actual_pb = getattr(self.pulseblaster(), '_currently_loaded_waveform', None)
                    if actual_pb != pb_name:
                        pb_load_ok = False
                        self.log.error(
                            'load_waveform: PulseBlaster reports "{0}" loaded, expected "{1}".'
                            ''.format(actual_pb, pb_name)
                        )
        else:
            self.log.info('No cached PulseBlaster waveform "{0}"; AWG-only load assumed.'.format(pb_name))

        if not pb_load_ok:
            self.log.error('load_waveform: PulseBlaster load failed for "{0}".'.format(logical_name))
            return self.get_loaded_assets()[0]

        self._loaded_name, self._loaded_type = logical_name, 'waveform'
        self.log.info('load_waveform: "{0}" successfully loaded and verified.'.format(logical_name))
        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """
        Load the AWG sequence and matching combined PB waveform, re-writing
        either from cache first if hardware currently holds something else.

        _pb_seq_store already holds FULLY PROCESSED (padded + shifted)
        data from write_sequence() -- this re-writes it as-is. If
        pb_extra_wait_time or awg_trigger_delay has changed since the
        sequence was last written, re-sample (via write_sequence()) to
        pick up the new value -- this load path intentionally does NOT
        re-derive padding/shift here, matching write_sequence()'s own
        "already shifted" cache contract.

        @return dict: loaded assets per channel, using logical names.
        """
        self._ensure_stopped(context='load_sequence')

        awg_seq_name = sequence_name if sequence_name.startswith('awg_') else 'awg_' + sequence_name

        awg_currently_written_list = self.awg().get_sequence_names()
        awg_currently_written = awg_currently_written_list[0] if awg_currently_written_list else None

        if awg_currently_written != awg_seq_name:
            if sequence_name in self._awg_seq_param_store:
                self.log.info('load_sequence: re-uploading "{0}" from cache.'.format(awg_seq_name))
                if self.write_sequence(sequence_name, self._awg_seq_param_store[sequence_name]) < 0:
                    self.log.error('load_sequence: re-upload of "{0}" failed.'.format(awg_seq_name))
                    return self.get_loaded_assets()[0]
            else:
                self.log.error(
                    'load_sequence: "{0}" is not the currently-written AWG sequence, '
                    'and no cached parameters are available. Re-sample it.'.format(sequence_name)
                )
                return self.get_loaded_assets()[0]

        self.awg().load_sequence(awg_seq_name)
        awg_assets, awg_type = self.awg().get_loaded_assets()
        awg_load_ok = awg_type == 'sequence' and awg_assets and all(v == awg_seq_name for v in awg_assets.values())

        if not awg_load_ok:
            self.log.error('load_sequence: AWG load verification failed for "{0}".'.format(sequence_name))
            return self.get_loaded_assets()[0]

        pb_seq_name, pb_load_ok = 'pb_seq_' + sequence_name, True

        if pb_seq_name in self._pb_seq_store:
            pb_written, _ = self.pulseblaster().write_waveform(
                pb_seq_name, {}, self._to_pb_hw_keys(self._pb_seq_store[pb_seq_name]),
                True, True, self._pb_seq_store_sizes[pb_seq_name]
            )
            if pb_written < 0:
                pb_load_ok = False
                self.log.error('load_sequence: re-writing PB waveform "{0}" failed.'.format(pb_seq_name))
            else:
                self.pulseblaster().load_waveform([pb_seq_name])
                actual_pb = getattr(self.pulseblaster(), '_currently_loaded_waveform', None)
                if actual_pb != pb_seq_name:
                    pb_load_ok = False
                    self.log.error(
                        'load_sequence: PulseBlaster reports "{0}" loaded, expected "{1}".'
                        ''.format(actual_pb, pb_seq_name)
                    )
        else:
            self.log.warning(
                'load_sequence: no cached PB content for "{0}"; PB keeps its current '
                'pattern (expected only if this sequence uses no PB channels).'.format(pb_seq_name)
            )

        if not pb_load_ok:
            self.log.error('load_sequence: PulseBlaster load failed for "{0}".'.format(sequence_name))
            return self.get_loaded_assets()[0]

        self._loaded_name, self._loaded_type = sequence_name, 'sequence'
        self.log.info('load_sequence: "{0}" successfully loaded and verified.'.format(sequence_name))
        return self.get_loaded_assets()[0]

    def get_loaded_assets(self):
        """AWG is the source of truth; 'awg_rabi_ch1' -> 'rabi'."""
        awg_assets, awg_type = self.awg().get_loaded_assets()
        if not awg_assets:
            return {}, None
        return {ch: self._logical_name(a) for ch, a in awg_assets.items()}, awg_type

    # =========================================================================
    # PulserInterface — name lists
    # =========================================================================

    def get_waveform_names(self):
        """
        All waveform names visible to qudi: AWG device names, PB's
        currently-live name, and logical names (including any only
        re-loadable via the cache, since PB itself only ever reports the
        ONE waveform currently in its instruction memory).
        """
        awg_names = self.awg().get_waveform_names()
        pb_live   = [n for n in self.pulseblaster().get_waveform_names() if n]

        awg_base = {self._logical_name(n) for n in awg_names if n.startswith('awg_')}
        pb_base  = {n[3:] for n in pb_live if n.startswith('pb_') and not n.startswith('pb_seq_')}
        cached_pb_base = {k[3:] for k in self._pb_waveform_store if k.startswith('pb_')}

        logical = set(self._written_waveform_names) | (awg_base & pb_base) | cached_pb_base
        return natural_sort(list(set(awg_names + pb_live) | logical))

    def get_sequence_names(self):
        return list(self._written_sequence_names)

    # =========================================================================
    # PulserInterface — deletion
    # =========================================================================

    def delete_waveform(self, waveform_name):
        """Delete waveform(s) and purge the matching PB re-write cache entries."""
        if isinstance(waveform_name, str):
            waveform_name = [waveform_name]

        deleted = []
        for name in waveform_name:
            if name.startswith('awg_'):
                deleted.extend(self.awg().delete_waveform(name))
                logical = self._logical_name(name)
            elif name.startswith('pb_'):
                deleted.append(name)
                logical = name[3:]
            else:
                awg_all = self.awg().get_waveform_names()
                to_del = [n for n in awg_all if n.startswith('awg_{0}_ch'.format(name))]
                if to_del:
                    deleted.extend(self.awg().delete_waveform(to_del))
                if name in self._written_waveform_names:
                    self._written_waveform_names.remove(name)
                deleted.append(name)
                logical = name

            pb_key = 'pb_{0}'.format(logical)
            self._pb_waveform_store.pop(pb_key, None)
            self._pb_waveform_store_sizes.pop(pb_key, None)

        return natural_sort(deleted)

    def delete_sequence(self, sequence_name):
        """Delete a sequence and purge its cache entries on both devices."""
        result = self.awg().delete_sequence('awg_' + sequence_name)
        if sequence_name in self._written_sequence_names:
            self._written_sequence_names.remove(sequence_name)

        self._awg_seq_param_store.pop(sequence_name, None)
        pb_seq_key = 'pb_seq_' + sequence_name
        self._pb_seq_store.pop(pb_seq_key, None)
        self._pb_seq_store_sizes.pop(pb_seq_key, None)
        return result

    def clear_all(self):
        """Clear all waveforms and sequences from both devices and all caches."""
        self.awg().clear_all()
        self.pulseblaster().clear_all()

        self._written_waveform_names.clear()
        self._written_sequence_names.clear()
        self._pb_sample_buffer.clear()
        self._pb_waveform_store.clear()
        self._pb_waveform_store_sizes.clear()
        self._pb_seq_store.clear()
        self._pb_seq_store_sizes.clear()
        self._awg_seq_param_store.clear()

        self._pb_current_wfm_name = ''
        self._loaded_name, self._loaded_type = '', None
        return 0

    # =========================================================================
    # PulserInterface — interleave / reset
    # =========================================================================

    def get_interleave(self):
        return self.awg().get_interleave()

    def set_interleave(self, state=False):
        result = self.awg().set_interleave(state)
        self._update_rate_params()
        return result

    def reset(self):
        """Reset both devices."""
        self.awg().reset()
        try:
            self.pulseblaster().reset()
        except Exception as exc:
            self.log.warning('PulseBlaster reset failed: {0}'.format(exc))
        return 0