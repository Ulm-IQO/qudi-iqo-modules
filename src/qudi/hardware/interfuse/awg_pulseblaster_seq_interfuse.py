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

Channel naming convention
  AWG channels (native):   a_ch1, a_ch2, d_ch1, d_ch2, d_ch3, d_ch4
  PulseBlaster channels:   d_ch5, d_ch6, d_ch7, ...  (continue d_ch* sequence)

  The offset is set by pb_channel_d_offset (default 5). All channels look
  identical to qudi so the GUI creates voltage widgets, they appear in
  laser/gate dropdowns, and activation config validation works correctly.

Waveform naming
  write_waveform('rabi', ...) creates:
    'awg_rabi_ch1', 'awg_rabi_ch2'  stored on AWG
    'pb_rabi'                         stored in PB RAM
  get_waveform_names() returns all device names plus logical name 'rabi'.
  get_loaded_assets()  returns logical name so GUI shows 'rabi'.

Granularity
  waveform_length.step = LCM(AWG_gran, AWG_samples_per_PB_clock_cycle).
  Every pulse element length satisfies both devices, making AWG->PB
  decimation always lossless.

Trigger delay compensation
  awg_trigger_delay = time from PB trigger edge to first AWG output sample.
  PB sample array is circularly shifted earlier by this amount.

Waveform mode (TRIG/GAT)
  PB loops: [trigger][waveform content]
  AWG:      one waveform per trigger

Sequence mode
  User draws AWG trigger in first element of their sequence (same channel).
  AWG step 1 has TWAIT=ON forced by write_sequence().
  PB loops: [tiled content of all steps -- trigger naturally in first step]
  AWG:      all steps once per trigger, then waits for next trigger.
  Identical workflow to waveform mode from user perspective.

Re-write-on-load
  Both PulseBlaster instruction memory and the AWG's SEQ list are
  single-slot: writing any waveform/sequence wipes out whatever was
  there before. load_waveform()/load_sequence() detect when the
  requested asset is not the one currently programmed on hardware and
  transparently RE-WRITE it from cached raw sample data (stored by
  write_waveform()/write_sequence()) before loading -- this is the only
  way to make a previously-uploaded-but-since-overwritten asset active
  again.

Start / stop ordering
  trigger_master = 'pulseblaster': arm AWG first, then start PB
  trigger_master = 'awg':          reverse order

Example config:

awg_pb_interfuse:
    module.Class: 'interfuse.awg_pulseblaster_interfuse.AwgPulseBlasterInterfuse'
    connect:
        awg: 'pulser_awg7000'
        pulseblaster: 'pulser_pulseblaster'
    options:
        trigger_master: 'pulseblaster'
        awg_trigger_delay: 100e-9
        pb_channels: [0, 1, 2, 3, 4]
        pb_channel_d_offset: 5
        default_activation_config: 'A1_M1_M2_pb3'
"""

from math import gcd
import time
import numpy as np

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.interface.pulser_interface import PulserInterface, PulserConstraints, SequenceOption
from qudi.util.helpers import natural_sort


class AwgPulseBlasterInterfuse(PulserInterface):
    """
    Single PulserInterface coordinating a Tektronix AWG7000 and
    SpinCore PulseBlaster ESR-Pro for qudi pulsed measurement experiments.
    """

    # ── Connectors ─────────────────────────────────────────────────────────────
    awg          = Connector(interface=PulserInterface)
    pulseblaster = Connector(interface=PulserInterface)

    # ── Config options ──────────────────────────────────────────────────────────
    _trigger_master    = ConfigOption('trigger_master',    default='pulseblaster', missing='warn')
    _awg_trigger_delay = ConfigOption('awg_trigger_delay', default=0.0,            missing='nothing')
    _pb_channels       = ConfigOption('pb_channels',       default=[0, 1, 2, 3],   missing='warn')
    _pb_d_offset       = ConfigOption('pb_channel_d_offset', default=5,            missing='nothing')
    _default_activation_config = ConfigOption(
        'default_activation_config', default=None, missing='nothing'
    )

    # =========================================================================
    # Module lifecycle
    # =========================================================================

    def on_activate(self):
        """Initialise internal state and compute rate-dependent parameters."""

        self._written_waveform_names = []
        self._written_sequence_names = []
        self._loaded_name            = ''
        self._loaded_type            = None

        self._pb_waveform_store       = {}
        self._pb_waveform_store_sizes = {}

        # Caches enabling re-write-on-load.
        #
        # PulseBlaster has no multi-slot memory: write_waveform() always
        # immediately reprograms the single instruction-memory bank, and
        # PB's own load_waveform() refuses anything except the most
        # recently written name. AWG sequences are similarly single-slot:
        # write_sequence() always wipes SEQ:LENG to 0 and rebuilds from
        # scratch, destroying any previously-written sequence's step
        # definitions.
        #
        # These caches let load_waveform()/load_sequence() transparently
        # RE-WRITE the requested asset from already-computed data whenever
        # it is not the currently-programmed one on hardware -- turning
        # "load" into "ensure this exact asset is active right now",
        # which is the only correct semantics given this hardware.
        self._pb_seq_store        = {}   # pb_seq_name -> {d_chN: np.ndarray}
        self._pb_seq_store_sizes  = {}   # pb_seq_name -> int
        self._awg_seq_param_store = {}   # logical sequence name -> sequence_parameter_list

        self._pb_sample_buffer    = {}
        self._pb_current_wfm_name = ''

        self._pb_active_channels = {
            self._pb_index_to_d_ch(i): False
            for i in range(len(self._pb_channels))
        }

        self._update_rate_params()

        if self._default_activation_config is not None:
            self._apply_default_activation_config()

    def on_deactivate(self):
        pass

    # =========================================================================
    # Private helpers — channel naming
    # =========================================================================

    def _is_pb_d_ch(self, ch_name):
        """Return True if ch_name belongs to PulseBlaster (d_chN, N >= offset)."""
        if not ch_name.startswith('d_ch'):
            return False
        try:
            return int(ch_name.rsplit('_ch', 1)[1]) >= self._pb_d_offset
        except (ValueError, IndexError):
            return False

    def _pb_index_to_d_ch(self, list_index):
        """Convert position in self._pb_channels list to qudi d_ch name."""
        return 'd_ch{0:d}'.format(self._pb_d_offset + list_index)

    def _d_ch_to_pb_hw(self, d_ch_name):
        """
        Convert d_ch name to PB hardware channel number.
        Returns None if not a configured PB channel.
        """
        try:
            d_num = int(d_ch_name.rsplit('_ch', 1)[1])
            idx   = d_num - self._pb_d_offset
            if 0 <= idx < len(self._pb_channels):
                return self._pb_channels[idx]
        except (ValueError, IndexError):
            pass
        return None

    def _all_pb_d_ch_names(self):
        """Return list of all PB channel names in d_ch* notation."""
        return [self._pb_index_to_d_ch(i) for i in range(len(self._pb_channels))]

    @staticmethod
    def _lcm(a, b):
        """Least common multiple of two positive integers."""
        return abs(a * b) // gcd(a, b)

    @staticmethod
    def _logical_name(awg_wfm_name):
        """
        Strip device prefix and channel suffix to get logical name.
        'awg_rabi_ch1' -> 'rabi'
        """
        name = awg_wfm_name
        if name.startswith('awg_'):
            name = name[4:]
        if '_ch' in name:
            name = name.rsplit('_ch', 1)[0]
        return name

    def _ensure_stopped(self, context=''):
        """
        Stop both AWG and PulseBlaster before writing or loading a new
        waveform or sequence.

        WHY THIS IS NECESSARY:
        SOUR:WAV / SEQ:ELEM selection commands (AWG) and instruction-memory
        reprogramming (PulseBlaster) can be silently rejected -- or take
        effect only in a pending register that never reaches actual
        output/execution -- if either device is currently armed (status 2)
        or running (status 1) from a PREVIOUS pulser_on() call.

        Used by write_waveform(), write_sequence(), load_waveform() and
        load_sequence() -- previously write_sequence() only checked/stopped
        the AWG and never the PulseBlaster, even though it also writes PB
        content further along in the same call.

        @param str context: label for log messages, e.g. 'load_waveform'
        """
        awg_status = self.awg().get_status()[0]
        if awg_status in (1, 2):
            self.log.info(
                '{0}: AWG was in state {1} (running/armed). Stopping '
                'before proceeding.'.format(context, awg_status)
            )
            self.awg().pulser_off()

        pb_status = self.pulseblaster().get_status()[0]
        if pb_status != 0:
            self.log.info(
                '{0}: PulseBlaster was running. Stopping before '
                'proceeding.'.format(context)
            )
            self.pulseblaster().pulser_off()

    def _log_pb_instruction_diagnostics(self, pb_name, pb_total):
        """
        Log PB RLE-compressed instruction count and warn if close to the
        hardware limit of 4094.

        Shared by write_waveform() and write_sequence(), which both upload
        PB content and previously duplicated this exact logging block
        independently with only cosmetic variable-name differences.

        @param str pb_name: PulseBlaster waveform name just uploaded
        @param int pb_total: number of PB samples in the uploaded waveform
        """
        try:
            pb_instr_count       = len(self.pulseblaster()._current_pb_waveform)
            pb_instr_theoretical = len(self.pulseblaster()._current_pb_waveform_theoretical)
            self.log.info(
                'PB "{0}": {1} samples @ {2:.3e} Hz -> '
                '{3} instructions after RLE compression '
                '(theoretical {4}, hardware max 4094).'
                ''.format(pb_name, pb_total, self._pb_sample_rate,
                          pb_instr_count, pb_instr_theoretical)
            )
            if pb_instr_count > 4000:
                self.log.warning(
                    'PB instruction count {0} is close to the hardware '
                    'maximum of 4094.'.format(pb_instr_count)
                )
        except Exception:
            self.log.debug(
                'Uploaded "{0}" to PulseBlaster: {1} samples @ {2:.3e} Hz.'
                ''.format(pb_name, pb_total, self._pb_sample_rate)
            )

    # =========================================================================
    # Private helpers — rate parameters
    # =========================================================================

    def _update_rate_params(self):
        """
        Recalculate sample-rate-dependent parameters.
        Call at on_activate(), after set_sample_rate(), after set_interleave().
        """
        awg_c = self.awg().get_constraints()
        pb_c  = self.pulseblaster().get_constraints()

        self._awg_sample_rate = self.awg().get_sample_rate()
        self._pb_sample_rate  = pb_c.sample_rate.default

        ratio            = self._awg_sample_rate / self._pb_sample_rate
        self._awg_per_pb = int(round(ratio))

        if abs(ratio - self._awg_per_pb) > 0.01:
            self.log.warning(
                'AWG sample rate ({0:.3e} Hz) / PB clock rate ({1:.3e} Hz) = {2:.4f} '
                'is not close to an integer. Rounding to {3:d}; '
                'small timing errors may result.'
                ''.format(self._awg_sample_rate, self._pb_sample_rate,
                          ratio, self._awg_per_pb)
            )

        awg_gran       = int(awg_c.waveform_length.step)
        self._lcm_gran = self._lcm(awg_gran, self._awg_per_pb)

        self._delay_pb_samples = int(
            round(self._awg_trigger_delay * self._pb_sample_rate)
        )

        self.log.info(
            'Interfuse rate params updated:\n'
            '  AWG sample rate : {0:.4e} Hz\n'
            '  PB  clock rate  : {1:.4e} Hz\n'
            '  AWG/PB ratio    : {2:d} AWG samples per PB cycle\n'
            '  LCM granularity : {3:d} AWG samples  =  {4:.2f} ns\n'
            '  Trigger delay   : {5:d} PB samples   =  {6:.2f} ns'
            ''.format(
                self._awg_sample_rate,
                self._pb_sample_rate,
                self._awg_per_pb,
                self._lcm_gran,
                self._lcm_gran / self._awg_sample_rate * 1e9,
                self._delay_pb_samples,
                self._delay_pb_samples / self._pb_sample_rate * 1e9
                if self._pb_sample_rate > 0 else 0.0,
            )
        )

    def _apply_default_activation_config(self):
        """Apply the yaml-specified default_activation_config at startup."""
        constraints = self.get_constraints()
        available   = constraints.activation_config

        if self._default_activation_config not in available:
            self.log.warning(
                'default_activation_config "{0}" not found in interfuse constraints.\n'
                'Available configs: {1}\n'
                'Starting with all channels inactive.'
                ''.format(self._default_activation_config, list(available.keys()))
            )
            return

        target_set      = available[self._default_activation_config]
        all_possible    = {**self.awg().get_active_channels(), **self._pb_active_channels}
        activation_dict = {ch: (ch in target_set) for ch in all_possible}

        self.set_active_channels(activation_dict)

        self.log.info(
            'Default activation config "{0}" applied. Active channels: {1}'
            ''.format(self._default_activation_config, sorted(target_set))
        )

    def _get_awg_waveform_length(self, awg_wfm_name):
        """
        Determine length in AWG samples of a waveform stored on the AWG.
        Returns 0 if not determinable.
        """
        try:
            length = int(self.awg().query(
                'WLIS:WAV:LENG? "{0}"'.format(awg_wfm_name)
            ))
            return length
        except Exception:
            return 0

    # =========================================================================
    # PulserInterface — constraints
    # =========================================================================

    def get_constraints(self):
        """
        Return merged constraints for the combined AWG + PulseBlaster system.
        """
        awg_c = self.awg().get_constraints()
        c     = PulserConstraints()

        c.sample_rate.min     = awg_c.sample_rate.min
        c.sample_rate.max     = awg_c.sample_rate.max
        c.sample_rate.step    = awg_c.sample_rate.step
        c.sample_rate.default = awg_c.sample_rate.default

        c.waveform_length.min     = awg_c.waveform_length.min
        c.waveform_length.max     = awg_c.waveform_length.max
        c.waveform_length.step    = self._lcm_gran
        c.waveform_length.default = awg_c.waveform_length.default

        c.a_ch_amplitude.min     = awg_c.a_ch_amplitude.min
        c.a_ch_amplitude.max     = awg_c.a_ch_amplitude.max
        c.a_ch_amplitude.step    = awg_c.a_ch_amplitude.step
        c.a_ch_amplitude.default = awg_c.a_ch_amplitude.default

        c.a_ch_offset.min     = awg_c.a_ch_offset.min
        c.a_ch_offset.max     = awg_c.a_ch_offset.max
        c.a_ch_offset.step    = awg_c.a_ch_offset.step
        c.a_ch_offset.default = awg_c.a_ch_offset.default

        c.d_ch_low.min     = awg_c.d_ch_low.min
        c.d_ch_low.max     = awg_c.d_ch_low.max
        c.d_ch_low.step    = awg_c.d_ch_low.step
        c.d_ch_low.default = awg_c.d_ch_low.default

        c.d_ch_high.min     = awg_c.d_ch_high.min
        c.d_ch_high.max     = awg_c.d_ch_high.max
        c.d_ch_high.step    = awg_c.d_ch_high.step
        c.d_ch_high.default = awg_c.d_ch_high.default

        c.waveform_num.min      = awg_c.waveform_num.min
        c.waveform_num.max      = awg_c.waveform_num.max
        c.waveform_num.step     = awg_c.waveform_num.step
        c.waveform_num.default  = awg_c.waveform_num.default

        c.sequence_num.min      = awg_c.sequence_num.min
        c.sequence_num.max      = awg_c.sequence_num.max
        c.sequence_num.step     = awg_c.sequence_num.step
        c.sequence_num.default  = awg_c.sequence_num.default

        c.subsequence_num.min     = awg_c.subsequence_num.min
        c.subsequence_num.max     = awg_c.subsequence_num.max
        c.subsequence_num.step    = awg_c.subsequence_num.step
        c.subsequence_num.default = awg_c.subsequence_num.default

        c.repetitions.min     = awg_c.repetitions.min
        c.repetitions.max     = awg_c.repetitions.max
        c.repetitions.step    = awg_c.repetitions.step
        c.repetitions.default = awg_c.repetitions.default

        c.event_triggers = awg_c.event_triggers
        c.flags          = awg_c.flags

        c.sequence_steps.min     = awg_c.sequence_steps.min
        c.sequence_steps.max     = awg_c.sequence_steps.max
        c.sequence_steps.step    = awg_c.sequence_steps.step
        c.sequence_steps.default = awg_c.sequence_steps.default

        # Activation configs: each AWG config extended with PB channel subsets
        activation_config = {}
        for cfg_name, awg_ch_set in awg_c.activation_config.items():
            activation_config[cfg_name] = awg_ch_set
            for n_pb in range(1, len(self._pb_channels) + 1):
                pb_subset = frozenset(
                    self._pb_index_to_d_ch(i) for i in range(n_pb)
                )
                activation_config['{0}_pb{1:d}'.format(cfg_name, n_pb)] = (
                    awg_ch_set | pb_subset
                )

        activation_config['pb_only'] = frozenset(self._all_pb_d_ch_names())

        c.activation_config = activation_config
        c.sequence_option   = awg_c.sequence_option

        return c

    # =========================================================================
    # PulserInterface — start / stop
    # =========================================================================

    def pulser_on(self):
        """
        Start combined output with correct device ordering.

        Steps:
          1. Wait for AWG to have a waveform loaded (prevents E11203)
          2. Drain AWG error queue (removes stale power-on events)
          3. Arm/start in correct order per trigger_master config

        @return int: status code from master device, or -1 on timeout.
        """
        master   = str(self._trigger_master).lower()
        timeout  = 10.0
        interval = 0.25

        elapsed = 0.0
        while True:
            loaded, _ = self.awg().get_loaded_assets()
            if loaded and all(v for v in loaded.values()):
                break
            if elapsed >= timeout:
                self.log.error(
                    'pulser_on: AWG reports no loaded waveform after {0:.1f}s.\n'
                    'Upload and load a waveform before starting.'
                    ''.format(timeout)
                )
                return -1
            self.log.debug(
                'pulser_on: waiting for AWG waveform ({0:.1f}s elapsed)...'.format(elapsed)
            )
            time.sleep(interval)
            elapsed += interval

        try:
            self.awg().get_errors()
        except Exception as exc:
            self.log.debug('pulser_on: could not drain AWG error queue: {0}'.format(exc))

        if master == 'pulseblaster':
            awg_status = self.awg().pulser_on()
            self.log.debug('AWG armed/started, status={0}.'.format(awg_status))
            time.sleep(0.1)
            pb_status = self.pulseblaster().pulser_on()
            self.log.debug('PulseBlaster started, status={0}.'.format(pb_status))
        else:
            pb_status  = self.pulseblaster().pulser_on()
            time.sleep(0.1)
            awg_status = self.awg().pulser_on()

        return self.get_status()[0]

    def pulser_off(self):
        """
        Stop combined output. Master stopped first to prevent stray triggers.

        @return int: status code from master device.
        """
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
        """The unified sample rate is the AWG sample rate."""
        return self.awg().get_sample_rate()

    def set_sample_rate(self, sample_rate):
        """Set AWG sample rate and recalculate LCM granularity."""
        result = self.awg().set_sample_rate(sample_rate)
        self._update_rate_params()
        return result

    # =========================================================================
    # PulserInterface — analog levels
    # =========================================================================

    def get_analog_level(self, amplitude=None, offset=None):
        """Pass through to AWG."""
        return self.awg().get_analog_level(amplitude=amplitude, offset=offset)

    def set_analog_level(self, amplitude=None, offset=None):
        """Pass through to AWG."""
        return self.awg().set_analog_level(amplitude=amplitude, offset=offset)

    # =========================================================================
    # PulserInterface — digital levels
    # =========================================================================

    def get_digital_level(self, low=None, high=None):
        """
        AWG marker channels (d_ch1..d_ch4): queried from AWG hardware.
        PB channels (d_ch5, ...):           fixed LVTTL 0.0 V / 3.3 V.

        All channels returned in default call so GUI creates widgets for all.
        """
        pb_names = self._all_pb_d_ch_names()

        if low is None and high is None:
            awg_low, awg_high = self.awg().get_digital_level()
            for ch in pb_names:
                awg_low[ch]  = 0.0
                awg_high[ch] = 3.3
            return awg_low, awg_high

        low_val  = {}
        high_val = {}

        if low is not None:
            awg_req = [ch for ch in low if not self._is_pb_d_ch(ch)]
            pb_req  = [ch for ch in low if     self._is_pb_d_ch(ch)]
            if awg_req:
                awg_l, _ = self.awg().get_digital_level(low=awg_req)
                low_val.update(awg_l)
            low_val.update({ch: 0.0 for ch in pb_req})

        if high is not None:
            awg_req = [ch for ch in high if not self._is_pb_d_ch(ch)]
            pb_req  = [ch for ch in high if     self._is_pb_d_ch(ch)]
            if awg_req:
                _, awg_h = self.awg().get_digital_level(high=awg_req)
                high_val.update(awg_h)
            high_val.update({ch: 3.3 for ch in pb_req})

        return low_val, high_val

    def set_digital_level(self, low=None, high=None):
        """
        AWG marker channels: passed to AWG hardware.
        PB channels: fixed LVTTL, requests silently ignored.
        """
        if low  is None: low  = {}
        if high is None: high = {}

        awg_low  = {k: v for k, v in low.items()  if not self._is_pb_d_ch(k)}
        awg_high = {k: v for k, v in high.items() if not self._is_pb_d_ch(k)}
        pb_req   = [k for k in list(low) + list(high) if self._is_pb_d_ch(k)]

        if pb_req:
            self.log.info(
                'PB channels {0} are fixed LVTTL (0 V / 3.3 V). '
                'Voltage change request ignored.'.format(pb_req)
            )

        return self.awg().set_digital_level(
            low=awg_low  or None,
            high=awg_high or None
        )

    # =========================================================================
    # PulserInterface — channel activation
    # =========================================================================

    def get_active_channels(self, ch=None):
        """
        AWG channels: queried from AWG hardware.
        PB channels:  from internal _pb_active_channels dict.
        """
        awg_active = self.awg().get_active_channels()
        all_active = {**awg_active, **self._pb_active_channels}

        if ch is not None:
            all_active = {k: v for k, v in all_active.items() if k in ch}
        return all_active

    def set_active_channels(self, ch=None):
        """
        AWG channels -> awg().set_active_channels()
        PB channels  -> internal dict only (never call pulseblaster().set_active_channels)
        """
        if ch is None:
            return self.get_active_channels()

        awg_ch = {k: v for k, v in ch.items() if not self._is_pb_d_ch(k)}
        pb_ch  = {k: v for k, v in ch.items() if     self._is_pb_d_ch(k)}

        if awg_ch:
            self.awg().set_active_channels(awg_ch)

        for d_ch_name, state in pb_ch.items():
            if d_ch_name in self._pb_active_channels:
                self._pb_active_channels[d_ch_name] = state
            else:
                self.log.warning(
                    'set_active_channels: "{0}" is not a configured PB channel. '
                    'Ignored.'.format(d_ch_name)
                )

        return self.get_active_channels()

    # =========================================================================
    # PulserInterface — waveform upload
    # =========================================================================

    def write_waveform(self, name, analog_samples, digital_samples,
                       is_first_chunk, is_last_chunk, total_number_of_samples):
        """
        Upload waveform to AWG ('awg_{name}') and PB ('pb_{name}').

        AWG channels (a_ch*, d_ch1..d_ch4) -> AWG directly.
        PB channels (d_ch5, d_ch6, ...)    -> downsampled, delay-shifted, then PB.

        PB samples buffered across chunks so delay shift applies to full
        waveform, and the final PB upload happens ONLY on is_last_chunk
        (see the guard below -- its absence was a critical bug that caused
        incomplete PB uploads for chunked writes and crashes for waveforms
        with zero PB channels).

        @return (int, list): samples written and list of AWG waveform names.
        """
        if is_first_chunk:
            self._ensure_stopped(context='write_waveform')

        # Split channels
        awg_digital = {
            k: v for k, v in digital_samples.items()
            if not self._is_pb_d_ch(k)
        }

        pb_digital_raw = {}
        for d_ch_name, samples in digital_samples.items():
            if self._is_pb_d_ch(d_ch_name):
                hw_ch = self._d_ch_to_pb_hw(d_ch_name)
                if hw_ch is not None:
                    pb_digital_raw['d_ch{0:d}'.format(hw_ch)] = samples
                else:
                    self.log.warning(
                        'write_waveform: "{0}" has no PB hardware mapping. Skipped.'
                        ''.format(d_ch_name)
                    )

        # Upload to AWG
        awg_name = 'awg_' + name
        awg_written, awg_waveforms = self.awg().write_waveform(
            awg_name, analog_samples, awg_digital,
            is_first_chunk, is_last_chunk, total_number_of_samples
        )

        if awg_written < 0:
            self.log.error('AWG write_waveform failed for "{0}".'.format(awg_name))
            return -1, []

        # Buffer PB samples
        if is_first_chunk:
            self._pb_sample_buffer    = {}
            self._pb_current_wfm_name = 'pb_' + name

        for pb_key, samples in pb_digital_raw.items():
            downsampled = samples[::self._awg_per_pb].copy()
            if pb_key in self._pb_sample_buffer:
                self._pb_sample_buffer[pb_key] = np.concatenate(
                    [self._pb_sample_buffer[pb_key], downsampled]
                )
            else:
                self._pb_sample_buffer[pb_key] = downsampled

        # ── 4. Last chunk: apply delay roll and upload to PB ──────────────────
        # CRITICAL FIX: this block MUST only run once per waveform, on the
        # FINAL chunk, when the full sample buffer has been accumulated.
        # Previously this ran unconditionally on EVERY chunk call, which:
        #   - Wrote incomplete/partial PB content mid-upload for chunked
        #     waveforms (large waveforms split across multiple calls)
        #   - Cleared _pb_sample_buffer / _pb_current_wfm_name before the
        #     final chunk arrived, corrupting the next chunk's accumulation
        #     (pb_name became '' on the following call)
        #   - Crashed with StopIteration for ANY waveform using zero PB
        #     channels, since pb_digital_shifted would be an empty dict
        if is_last_chunk:
            if self._pb_sample_buffer:
                # RAW (un-rolled) full-period array, stored for
                # write_sequence() to concatenate later. The delay roll
                # must NOT be applied to this raw copy -- if applied
                # per-segment before tiling, each segment's own wraparound
                # displaces content into neighbouring segments once
                # concatenated, producing stray pulses at segment
                # boundaries (e.g. an extra laser+gate blip after the
                # sequence finishes).
                pb_digital_raw_full = {k: v.copy() for k, v in self._pb_sample_buffer.items()}

                # Separate shifted copy -- ONLY used for standalone
                # waveform mode (TRIG/GAT), where this single waveform
                # genuinely loops on itself and the roll correctly
                # represents that periodicity.
                pb_digital_shifted = {k: v.copy() for k, v in pb_digital_raw_full.items()}
                if self._delay_pb_samples > 0:
                    for d_key in pb_digital_shifted:
                        pb_digital_shifted[d_key] = np.roll(
                            pb_digital_shifted[d_key], -self._delay_pb_samples
                        )

                pb_name  = self._pb_current_wfm_name
                pb_total = len(next(iter(pb_digital_shifted.values())))

                pb_written, _ = self.pulseblaster().write_waveform(
                    pb_name, {}, pb_digital_shifted,
                    True, True, pb_total
                )

                self._pb_sample_buffer    = {}
                self._pb_current_wfm_name = ''

                if pb_written < 0:
                    self.log.error(
                        'PulseBlaster write_waveform failed for "{0}".'.format(pb_name)
                    )
                    return -1, []

                # Store the RAW (un-rolled) samples for write_sequence() to
                # use. write_sequence() will concatenate these raw segments
                # and apply the delay roll exactly once, to the correct
                # combined period.
                self._pb_waveform_store[pb_name]       = {
                    k: v.copy() for k, v in pb_digital_raw_full.items()
                }
                self._pb_waveform_store_sizes[pb_name] = pb_total

                self._log_pb_instruction_diagnostics(pb_name, pb_total)
            else:
                # No PB channels were part of this waveform (e.g. an
                # AWG-only activation config). Nothing to upload to PB;
                # this is a normal, expected case, not an error.
                self.log.debug(
                    'write_waveform: "{0}" contains no PulseBlaster channel '
                    'content; skipping PB upload.'.format(name)
                )
                self._pb_current_wfm_name = ''

        if is_last_chunk and name not in self._written_waveform_names:
            self._written_waveform_names.append(name)

        return total_number_of_samples, awg_waveforms

    # =========================================================================
    # PulserInterface — sequence upload
    # =========================================================================

    def write_sequence(self, name, sequence_parameter_list):
        """
        Write AWG sequence ('awg_{name}') and build combined PB waveform
        ('pb_seq_{name}') covering exactly one full sequence cycle.

        Equivalence with waveform mode
        -------------------------------
        Waveform mode:
          User draws AWG trigger channel in pulse block.
          PB loops: [trigger][waveform content]
          AWG:      one waveform per trigger

        Sequence mode (this method):
          User draws AWG trigger in FIRST element of their sequence.
          TWAIT=ON is forced on AWG step 1 here.
          PB loops: [tiled content of all steps -- trigger naturally in first step]
          AWG:      all steps once per trigger, then waits

        No extra config needed. Same channel, same BNC, same workflow.

        @return int: number of sequence steps written, or -1 on failure.
        """
        awg_seq_name = 'awg_' + name

        # Pre-check AWG sequence step count before doing ANY work -- avoids
        # wasting time on AWG upload if we already know the limit will be
        # exceeded.
        max_steps = self.awg().get_constraints().sequence_steps.max
        if len(sequence_parameter_list) > max_steps:
            self.log.error(
                'write_sequence ABORTED before upload.\n'
                'Sequence "{0}" requires {1} steps, exceeding AWG hardware '
                'maximum of {2} steps.\n'
                'Reduce the number of tau/measurement points.'
                ''.format(name, len(sequence_parameter_list), max_steps)
            )
            return -1

        # FIX: stop BOTH devices, not just the AWG. This method also
        # calls pulseblaster().write_waveform() further below to upload
        # the combined PB sequence content -- PB must be stopped first
        # for the same reason the AWG must be, but this was previously
        # missing entirely.
        self._ensure_stopped(context='write_sequence')

        # Drain error queue to remove stale events (E11506, E11203, power-on)
        # so they don't appear as alarming errors during normal write operations.
        try:
            self.awg().get_errors()
        except Exception:
            pass

        result = self.awg().write_sequence(awg_seq_name, sequence_parameter_list)

        if result < 0:
            self.log.error(
                'AWG write_sequence failed for "{0}". '
                'Skipping PB combined waveform build.'.format(awg_seq_name)
            )
            return result

        # Only force TWAIT=ON if NOT in continuous mode. In CONT mode the
        # sequence loops freely without waiting for an external trigger
        # (accepting the AWG/PB clock-drift risk that entails).
        awg_run_mode = str(getattr(self.awg(), '_run_mode_config', 'TRIG')).upper()
        if awg_run_mode != 'CONT':
            # Force TWAIT=ON on step 1 so it waits for the user-drawn
            # trigger pulse -- the same physical channel/BNC used in
            # waveform mode.
            self.awg().sequence_set_wait_trigger(1, 'ON')
            self.log.debug(
                'write_sequence: TWAIT=ON forced on step 1 of "{0}".'.format(awg_seq_name)
            )
        else:
            self.awg().sequence_set_wait_trigger(1, 'OFF')
            self.log.debug(
                'write_sequence: TWAIT=OFF on step 1 (continuous free-run mode).'
            )

        # Build combined PB waveform by tiling per-step PB content
        pb_combined  = {}
        total_pb_len = 0

        for wfm_tuple, seq_params in sequence_parameter_list:

            logical = self._logical_name(wfm_tuple[0])
            pb_key  = 'pb_' + logical
            reps    = int(seq_params.get('repetitions', 0)) + 1

            pb_samples = self._pb_waveform_store.get(pb_key, {})
            pb_size    = self._pb_waveform_store_sizes.get(pb_key, 0)

            if not pb_samples or pb_size == 0:
                # No PB content -- derive idle block length from AWG waveform
                self.log.debug(
                    'write_sequence: no PB samples for "{0}"; '
                    'using all-LOW idle block.'.format(pb_key)
                )
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
                    prefix = np.zeros(total_pb_len, dtype=bool)
                    pb_combined[d_ch] = np.concatenate([prefix, tiled])

            for d_ch in list(pb_combined.keys()):
                current_len = len(pb_combined[d_ch])
                new_total   = total_pb_len + step_len
                if current_len < new_total:
                    pb_combined[d_ch] = np.concatenate(
                        [pb_combined[d_ch],
                         np.zeros(new_total - current_len, dtype=bool)]
                    )

            total_pb_len += step_len

        if not pb_combined or total_pb_len == 0:
            self.log.warning(
                'write_sequence: no PB content for sequence "{0}". '
                'PB will not be updated.'.format(name)
            )
            self._awg_seq_param_store[name] = sequence_parameter_list

            if name not in self._written_sequence_names:
                self._written_sequence_names.append(name)
            return result

        if self._delay_pb_samples > 0:
            for d_ch in pb_combined:
                pb_combined[d_ch] = np.roll(
                    pb_combined[d_ch], -self._delay_pb_samples
                )

        pb_seq_name = 'pb_seq_' + name

        pb_written, _ = self.pulseblaster().write_waveform(
            pb_seq_name, {}, pb_combined,
            True, True, total_pb_len
        )

        if pb_written < 0:
            self.log.error(
                'Failed to upload combined PB waveform "{0}".'.format(pb_seq_name)
            )
            return -1

        # Cache the combined PB waveform and the AWG sequence params. This
        # is what makes it possible to re-program BOTH devices from this
        # specific sequence's data on a later load_sequence() call,
        # regardless of what has been written to hardware since.
        self._pb_seq_store[pb_seq_name]       = {k: v.copy() for k, v in pb_combined.items()}
        self._pb_seq_store_sizes[pb_seq_name] = total_pb_len
        self._awg_seq_param_store[name]       = sequence_parameter_list

        self._log_pb_instruction_diagnostics(pb_seq_name, total_pb_len)

        if name not in self._written_sequence_names:
            self._written_sequence_names.append(name)

        return result

    # =========================================================================
    # PulserInterface — loading
    # =========================================================================

    def load_waveform(self, load_dict):
        """
        Load a waveform on both the AWG and the PulseBlaster.

        PulseBlaster has no multi-slot memory -- it only ever holds the
        MOST RECENTLY WRITTEN waveform. If a different waveform has been
        written since this one, PB's hardware no longer contains this
        waveform's pattern at all, even though this interfuse still has
        the raw sample data cached from when it was originally written.

        This method detects that situation and RE-WRITES the requested
        waveform from the cache before loading -- this is the only way
        to genuinely make a previously-uploaded (but since overwritten)
        PulseBlaster waveform active again.

        @return dict: loaded assets per channel (logical names).
        """
        self._ensure_stopped(context='load_waveform')

        if isinstance(load_dict, list):
            new_dict = {}
            for wfm_name in load_dict:
                if '_ch' in wfm_name:
                    ch_num = int(wfm_name.rsplit('_ch', 1)[1])
                    new_dict[ch_num] = wfm_name
                else:
                    new_dict[1] = wfm_name
            load_dict = new_dict

        if not load_dict:
            self.log.error('load_waveform received an empty load dict.')
            return self.get_loaded_assets()[0]

        awg_load_dict = {}
        logical_name  = None

        for ch_num, wfm_name in load_dict.items():
            if wfm_name.startswith('awg_'):
                awg_wfm = wfm_name
                logical = self._logical_name(wfm_name)
            else:
                base    = wfm_name.rsplit('_ch', 1)[0] if '_ch' in wfm_name else wfm_name
                awg_wfm = 'awg_{0}_ch{1}'.format(base, ch_num)
                logical = base

            awg_load_dict[ch_num] = awg_wfm
            logical_name          = logical

        # AWG side: waveforms are multi-slot (WLIS holds many simultaneously),
        # so a simple SOUR:WAV selection is sufficient -- no re-write needed.
        awg_result = self.awg().load_waveform(awg_load_dict)

        awg_load_ok = True
        for ch_num, expected_wfm in awg_load_dict.items():
            actual_wfm = awg_result.get(ch_num) if isinstance(awg_result, dict) else None
            if actual_wfm != expected_wfm:
                awg_load_ok = False
                self.log.error(
                    'load_waveform: AWG channel {0} reports "{1}" loaded, '
                    'expected "{2}". AWG load FAILED.'
                    ''.format(ch_num, actual_wfm, expected_wfm)
                )

        if not awg_load_ok:
            self.log.error(
                'load_waveform: AWG load verification failed for "{0}". '
                'Asset will NOT be marked as loaded.'.format(logical_name)
            )
            return self.get_loaded_assets()[0]

        # PB side: single-slot memory. ALWAYS re-write from cache rather
        # than checking whether it's "already" the active one -- this is
        # cheap (just re-programs instruction memory, no resampling) and
        # guarantees correctness regardless of what was written since.
        pb_name    = 'pb_{0}'.format(logical_name)
        pb_load_ok = True

        if pb_name in self._pb_waveform_store:
            pb_written, _ = self.pulseblaster().write_waveform(
                pb_name, {},
                self._pb_waveform_store[pb_name],
                True, True,
                self._pb_waveform_store_sizes[pb_name]
            )

            if pb_written < 0:
                pb_load_ok = False
                self.log.error(
                    'load_waveform: re-writing PB waveform "{0}" from '
                    'cache failed.'.format(pb_name)
                )
            else:
                self.pulseblaster().load_waveform([pb_name])
                actual_pb = getattr(self.pulseblaster(), '_currently_loaded_waveform', None)
                if actual_pb != pb_name:
                    pb_load_ok = False
                    self.log.error(
                        'load_waveform: PulseBlaster reports "{0}" loaded, '
                        'expected "{1}" after re-write.'
                        ''.format(actual_pb, pb_name)
                    )
                else:
                    self.log.info(
                        'Re-wrote and loaded PulseBlaster waveform "{0}".'
                        ''.format(pb_name)
                    )
        else:
            self.log.info(
                'No cached PulseBlaster waveform "{0}"; AWG-only load assumed.'
                ''.format(pb_name)
            )

        if not pb_load_ok:
            self.log.error(
                'load_waveform: PulseBlaster load failed for "{0}". '
                'Asset will NOT be marked as loaded.'.format(logical_name)
            )
            return self.get_loaded_assets()[0]

        self._loaded_name = logical_name
        self._loaded_type = 'waveform'

        self.log.info(
            'load_waveform: "{0}" successfully loaded and verified.'
            ''.format(logical_name)
        )

        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """
        Load the AWG sequence and the matching combined PB waveform.

        Both the AWG's sequence list and the PulseBlaster's instruction
        memory are single-slot: writing ANY sequence wipes out whatever
        was there before. If sequence_name is not the one currently
        programmed on either device, this method RE-WRITES it from the
        cached data stored by write_sequence() before attempting to load.

        @return dict: loaded assets per channel, using LOGICAL names.
        """
        self._ensure_stopped(context='load_sequence')

        awg_seq_name = ('awg_' + sequence_name
                        if not sequence_name.startswith('awg_')
                        else sequence_name)

        # Re-write AWG sequence if it's not the currently-programmed one.
        # FIX: use the public get_sequence_names() interface method instead
        # of reaching into the AWG module's private _written_sequences list.
        awg_currently_written_list = self.awg().get_sequence_names()
        awg_currently_written = (
            awg_currently_written_list[0] if awg_currently_written_list else None
        )

        if awg_currently_written != awg_seq_name:
            if sequence_name in self._awg_seq_param_store:
                self.log.info(
                    'load_sequence: AWG currently has "{0}" written, not '
                    '"{1}". Re-uploading from cache before loading.'
                    ''.format(awg_currently_written, awg_seq_name)
                )
                # FIX: call the INTERFUSE's own write_sequence() here, not
                # self.awg().write_sequence() directly. The direct AWG call
                # bypasses TWAIT=ON forcing on step 1 and the PB combined-
                # waveform rebuild/caching that only happen inside this
                # interfuse's write_sequence() wrapper -- silently breaking
                # trigger synchronization after a cache-based re-upload.
                rewrite_result = self.write_sequence(
                    sequence_name, self._awg_seq_param_store[sequence_name]
                )
                if rewrite_result < 0:
                    self.log.error(
                        'load_sequence: re-upload of "{0}" failed. '
                        'Cannot load.'.format(awg_seq_name)
                    )
                    return self.get_loaded_assets()[0]
            else:
                self.log.error(
                    'load_sequence: "{0}" is not the currently-written AWG '
                    'sequence, and no cached parameters are available to '
                    're-upload it (it may have been written before this '
                    'interfuse session started, or clear_all()/'
                    'delete_sequence() was called since). Re-sample the '
                    'sequence to make it loadable again.'
                    ''.format(sequence_name)
                )
                return self.get_loaded_assets()[0]

        self.awg().load_sequence(awg_seq_name)

        awg_assets, awg_type = self.awg().get_loaded_assets()
        awg_load_ok = (
            awg_type == 'sequence'
            and awg_assets
            and all(v == awg_seq_name for v in awg_assets.values())
        )

        if not awg_load_ok:
            self.log.error(
                'load_sequence: AWG load verification failed for "{0}".\n'
                'AWG reports type="{1}", assets={2}. Expected type='
                '"sequence" with all channels = "{3}".'
                ''.format(sequence_name, awg_type, awg_assets, awg_seq_name)
            )
            return self.get_loaded_assets()[0]

        # Re-write PB combined waveform (ALWAYS, same reasoning as
        # load_waveform: cheap, guarantees correctness).
        pb_seq_name = 'pb_seq_' + sequence_name
        pb_load_ok  = True

        if pb_seq_name in self._pb_seq_store:
            pb_written, _ = self.pulseblaster().write_waveform(
                pb_seq_name, {},
                self._pb_seq_store[pb_seq_name],
                True, True,
                self._pb_seq_store_sizes[pb_seq_name]
            )

            if pb_written < 0:
                pb_load_ok = False
                self.log.error(
                    'load_sequence: re-writing combined PB waveform "{0}" '
                    'from cache failed.'.format(pb_seq_name)
                )
            else:
                self.pulseblaster().load_waveform([pb_seq_name])
                actual_pb = getattr(self.pulseblaster(), '_currently_loaded_waveform', None)
                if actual_pb != pb_seq_name:
                    pb_load_ok = False
                    self.log.error(
                        'load_sequence: PulseBlaster reports "{0}" loaded, '
                        'expected "{1}" after re-write.'
                        ''.format(actual_pb, pb_seq_name)
                    )
                else:
                    self.log.info(
                        'Re-wrote and loaded combined PB sequence waveform '
                        '"{0}".'.format(pb_seq_name)
                    )
        else:
            self.log.warning(
                'load_sequence: no cached PB content for "{0}". PB will '
                'keep running its current pattern -- this is expected only '
                'if this sequence genuinely uses no PB channels.'
                ''.format(pb_seq_name)
            )

        if not pb_load_ok:
            self.log.error(
                'load_sequence: PulseBlaster load failed for "{0}". '
                'Asset will NOT be marked as loaded.'.format(sequence_name)
            )
            return self.get_loaded_assets()[0]

        self._loaded_name = sequence_name
        self._loaded_type = 'sequence'

        self.log.info(
            'load_sequence: "{0}" successfully loaded and verified on '
            'AWG and PulseBlaster.'.format(sequence_name)
        )

        return self.get_loaded_assets()[0]

    def get_loaded_assets(self):
        """
        Return loaded assets using logical names.
        AWG is the source of truth; 'awg_rabi_ch1' -> 'rabi'.

        @return (dict, str): {channel_num: logical_name}, asset_type
        """
        awg_assets, awg_type = self.awg().get_loaded_assets()

        if not awg_assets:
            return {}, None

        logical_assets = {
            ch_num: self._logical_name(asset_name)
            for ch_num, asset_name in awg_assets.items()
        }

        return logical_assets, awg_type

    # =========================================================================
    # PulserInterface — name lists
    # =========================================================================

    def get_waveform_names(self):
        """
        Return all waveform names visible to qudi:
          'awg_rabi_ch1', 'awg_rabi_ch2'  stored on AWG hardware
          'pb_rabi'                         currently programmed on PB hardware
          'rabi'                            logical name, re-loadable via cache

        NOTE on PulseBlaster: the PB board is single-slot hardware -- it
        only ever reports the ONE waveform currently programmed into its
        instruction memory via its own get_waveform_names(). Relying on
        that alone would under-report which logical waveforms are
        genuinely available: load_waveform()'s cache-based re-write
        mechanism can make ANY previously-written waveform active again
        even if PB currently holds something else. self._written_waveform_names
        and the _pb_waveform_store cache keys are included below so the
        reported list matches what is actually re-loadable.

        Also excludes 'pb_seq_*' names from the naive 3-character prefix
        strip used to derive logical names -- stripping just 'pb_' from
        'pb_seq_t1_seq' would incorrectly yield 'seq_t1_seq' instead of a
        real logical name.
        """
        awg_names = self.awg().get_waveform_names()
        pb_live   = [n for n in self.pulseblaster().get_waveform_names() if n]

        awg_base = {self._logical_name(n) for n in awg_names if n.startswith('awg_')}
        pb_base  = {
            n[3:] for n in pb_live
            if n.startswith('pb_') and not n.startswith('pb_seq_')
        }

        cached_pb_base = {
            key[3:] for key in self._pb_waveform_store if key.startswith('pb_')
        }

        logical = set(self._written_waveform_names) | (awg_base & pb_base) | cached_pb_base

        return natural_sort(list(set(awg_names + pb_live) | logical))

    def get_sequence_names(self):
        """Return logical sequence names tracked since activation."""
        return list(self._written_sequence_names)

    # =========================================================================
    # PulserInterface — deletion
    # =========================================================================

    def delete_waveform(self, waveform_name):
        """
        Delete waveform(s).
        Logical name 'rabi' -> deletes 'awg_rabi_chN' variants on AWG.

        Also purges the corresponding entries from the PulseBlaster
        re-write caches (_pb_waveform_store / _pb_waveform_store_sizes).
        Without this, a "deleted" waveform remained silently re-loadable
        via load_waveform()'s cache-based re-write mechanism, directly
        contradicting the deletion.

        @return list: deleted waveform names.
        """
        if isinstance(waveform_name, str):
            waveform_name = [waveform_name]

        deleted = []

        for name in waveform_name:
            if name.startswith('awg_'):
                deleted.extend(self.awg().delete_waveform(name))
                logical = self._logical_name(name)

            elif name.startswith('pb_'):
                self.log.info(
                    'PulseBlaster has no persistent storage; '
                    '"{0}" cleared on next write_waveform call.'.format(name)
                )
                deleted.append(name)
                logical = name[3:]

            else:
                awg_all = self.awg().get_waveform_names()
                prefix  = 'awg_{0}_ch'.format(name)
                to_del  = [n for n in awg_all if n.startswith(prefix)]
                if to_del:
                    deleted.extend(self.awg().delete_waveform(to_del))
                if name in self._written_waveform_names:
                    self._written_waveform_names.remove(name)
                deleted.append(name)
                logical = name

            # FIX: purge the PB re-write cache entry too, so a deleted
            # waveform genuinely cannot be silently reloaded afterward.
            pb_key = 'pb_{0}'.format(logical)
            self._pb_waveform_store.pop(pb_key, None)
            self._pb_waveform_store_sizes.pop(pb_key, None)

        return natural_sort(deleted)

    def delete_sequence(self, sequence_name):
        """
        Delete a sequence from the AWG.

        Also purges the corresponding cache entries (_awg_seq_param_store,
        _pb_seq_store, _pb_seq_store_sizes) so a deleted sequence cannot be
        silently re-loaded via the cache-based re-write mechanism in
        load_sequence(). Previously missing -- same class of bug as
        delete_waveform() above.

        @return: result from AWG delete_sequence()
        """
        awg_seq = 'awg_' + sequence_name
        result  = self.awg().delete_sequence(awg_seq)
        if sequence_name in self._written_sequence_names:
            self._written_sequence_names.remove(sequence_name)

        self._awg_seq_param_store.pop(sequence_name, None)
        pb_seq_key = 'pb_seq_' + sequence_name
        self._pb_seq_store.pop(pb_seq_key, None)
        self._pb_seq_store_sizes.pop(pb_seq_key, None)

        return result

    def clear_all(self):
        """Clear all waveforms and sequences from both devices."""
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
        self._loaded_name         = ''
        self._loaded_type         = None

        return 0

    # =========================================================================
    # PulserInterface — interleave / reset
    # =========================================================================

    def get_interleave(self):
        """Return AWG interleave state."""
        return self.awg().get_interleave()

    def set_interleave(self, state=False):
        """Set AWG interleave and recalculate LCM granularity."""
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