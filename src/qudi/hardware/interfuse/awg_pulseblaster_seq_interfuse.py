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
  PB loops: [trigger + all step content tiled]
  AWG:      all steps once per trigger, then waits for next trigger.
  Identical workflow to waveform mode from user perspective.

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

        # Storage for PB sample arrays written per waveform.
        # write_sequence() uses these to tile per-step PB content.
        self._pb_waveform_store       = {}   # 'pb_name' -> {d_chN: np.ndarray}
        self._pb_waveform_store_sizes = {}   # 'pb_name' -> int (PB sample count)

        # PB sample buffer across chunked uploads
        self._pb_sample_buffer    = {}
        self._pb_current_wfm_name = ''

        # PB channel states tracked internally (never call pulseblaster().set_active_channels
        # because the PB module only accepts exactly 4 or 21 channels)
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

        # Step 1: Wait for AWG waveform to be ready
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

        # Step 2: Drain AWG error queue
        try:
            self.awg().get_errors()
        except Exception as exc:
            self.log.debug('pulser_on: could not drain AWG error queue: {0}'.format(exc))

        # Step 3: Arm and start
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

        PB samples buffered across chunks so delay shift applies to full waveform.

        @return (int, list): samples written and list of AWG waveform names.
        """
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

        # Last chunk: apply delay roll and upload to PB
        # RAW (un-rolled) full-period array.
        # This is stored for write_sequence() to concatenate later.
        # The delay roll must NOT be applied here — if applied per-segment
        # before tiling, each segment's own wraparound displaces content
        # into neighbouring segments once concatenated, producing stray
        # pulses at segment boundaries (e.g. an extra laser+gate blip
        # appearing after the sequence finishes).
        pb_digital_raw_full = {k: v.copy() for k, v in self._pb_sample_buffer.items()}

        # Separate shifted copy — ONLY used for standalone waveform mode
        # (TRIG/GAT), where this single waveform genuinely loops on itself
        # and the roll correctly represents that periodicity.
        pb_digital_shifted = {k: v.copy() for k, v in pb_digital_raw_full.items()}
        if self._delay_pb_samples > 0:
            for d_key in pb_digital_shifted:
                pb_digital_shifted[d_key] = np.roll(
                    pb_digital_shifted[d_key], -self._delay_pb_samples
                )

        pb_name  = self._pb_current_wfm_name
        pb_total = len(next(iter(pb_digital_shifted.values())))

        # Upload the SHIFTED version for standalone waveform-mode playback
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

        # Store the RAW (un-rolled) samples for write_sequence() to use.
        # write_sequence() will concatenate these raw segments and apply
        # the delay roll exactly once, to the correct combined period.
        self._pb_waveform_store[pb_name]       = {
            k: v.copy() for k, v in pb_digital_raw_full.items()
        }
        self._pb_waveform_store_sizes[pb_name] = pb_total

        # Diagnostic logging
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
        ──────────────────────────────
        Waveform mode:
          User draws AWG trigger channel in pulse block.
          PB loops: [trigger][waveform content]
          AWG:      one waveform per trigger

        Sequence mode (this method):
          User draws AWG trigger in FIRST element of their sequence.
          TWAIT=ON is forced on AWG step 1 here.
          PB loops: [tiled content of all steps — trigger naturally in first step]
          AWG:      all steps once per trigger, then waits

        No extra config needed. Same channel, same BNC, same workflow.

        @return int: number of sequence steps written, or -1 on failure.
        """
        awg_seq_name = 'awg_' + name

        # Stop AWG before writing.
        # If armed (status 2) from a previous run, OUTPUT:STATE? queries in the
        # AWG reflect the OLD hardware state, not the current activation config.
        # Stopping first ensures _internal_ch_state is the source of truth.
        if self.awg()._is_output_on():
            self.log.info(
                'write_sequence: stopping AWG (status={0}) before sequence write.'
                ''.format(self.awg().get_status()[0])
            )
            self.awg().pulser_off()

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

        # Only force TWAIT=ON if NOT in continuous mode.
        # In CONT mode the sequence loops freely without waiting for
        # an external trigger — accepting the AWG/PB drift risk noted above.
        awg_run_mode = str(getattr(self.awg(), '_run_mode_config', 'TRIG')).upper()
        if awg_run_mode != 'CONT':
            # Force TWAIT=ON on step 1 so it waits for the user-drawn trigger pulse
            self.awg().sequence_set_wait_trigger(1, 'ON')
            self.log.debug(
                        'write_sequence: TWAIT=ON forced on step 1 of "{0}".'.format(awg_seq_name)
                    )
        else:
            self.awg().sequence_set_wait_trigger(1, 'OFF')
            self.log.debug(
                'write_sequence: TWAIT=OFF on step 1 (continuous free-run mode). '
                'PB trigger insertion is unnecessary but harmless if present.'
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
                # No PB content — derive idle block length from AWG waveform
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

            # Tile and accumulate
            for d_ch, samples in pb_samples.items():
                tiled = np.tile(samples, reps)
                if d_ch in pb_combined:
                    pb_combined[d_ch] = np.concatenate([pb_combined[d_ch], tiled])
                else:
                    prefix = np.zeros(total_pb_len, dtype=bool)
                    pb_combined[d_ch] = np.concatenate([prefix, tiled])

            # Pad all channels to equal length
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
            if name not in self._written_sequence_names:
                self._written_sequence_names.append(name)
            return result

        # ── Apply delay roll ONCE, to the fully assembled combined waveform ────
        # This is the correct point to compensate for AWG trigger latency:
        # the combined waveform is the TRUE period that loops (PB fires
        # trigger -> AWG plays all sequence steps -> loops back to step 1
        # waiting). Rolling here shifts all PB channels earlier relative to
        # that true period boundary, with no risk of segment-boundary
        # artifacts since there are no longer any individual segment
        # boundaries to worry about — it's one array now.
        if self._delay_pb_samples > 0:
            for d_ch in pb_combined:
                pb_combined[d_ch] = np.roll(
                    pb_combined[d_ch], -self._delay_pb_samples
                )

        # ── Upload combined PB waveform ─────────────────────────────────────────
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

        try:
            pb_instr = len(self.pulseblaster()._current_pb_waveform)
            self.log.info(
                'Combined PB sequence "{0}": {1} PB samples -> '
                '{2} instructions (hardware max 4094).'
                ''.format(pb_seq_name, total_pb_len, pb_instr)
            )
            if pb_instr > 4000:
                self.log.warning(
                    'PB instruction count {0} is close to the hardware '
                    'maximum of 4094.'.format(pb_instr)
                )
        except Exception:
            self.log.debug(
                'Combined PB sequence "{0}": {1} samples uploaded.'
                ''.format(pb_seq_name, total_pb_len)
            )

        if name not in self._written_sequence_names:
            self._written_sequence_names.append(name)

        return result

    # =========================================================================
    # PulserInterface — loading
    # =========================================================================

    def load_waveform(self, load_dict):
        """
        Load waveform on AWG and PulseBlaster.

        @return dict: loaded assets per channel (logical names).
        """
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

        self.awg().load_waveform(awg_load_dict)

        pb_name = 'pb_{0}'.format(logical_name)
        if pb_name in self.pulseblaster().get_waveform_names():
            self.pulseblaster().load_waveform([pb_name])
        else:
            self.log.info(
                'No PulseBlaster waveform "{0}" found; AWG-only load assumed.'.format(pb_name)
            )

        self._loaded_name = logical_name
        self._loaded_type = 'waveform'

        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """
        Load AWG sequence ('awg_{sequence_name}') and combined PB waveform
        ('pb_seq_{sequence_name}').

        @return dict: loaded assets per channel.
        """
        awg_seq_name = ('awg_' + sequence_name
                        if not sequence_name.startswith('awg_')
                        else sequence_name)

        result = self.awg().load_sequence(awg_seq_name)

        pb_seq_name  = 'pb_seq_' + sequence_name
        pb_wfm_names = self.pulseblaster().get_waveform_names()

        if pb_seq_name in pb_wfm_names:
            self.pulseblaster().load_waveform([pb_seq_name])
            self.log.info(
                'Loaded combined PB sequence waveform "{0}".'.format(pb_seq_name)
            )
        else:
            self.log.warning(
                'Combined PB sequence waveform "{0}" not found in PB memory.\n'
                'Available: {1}\n'
                'PB will loop its current waveform -- timing may be incorrect.'
                ''.format(pb_seq_name, pb_wfm_names)
            )

        self._loaded_name = sequence_name
        self._loaded_type = 'sequence'

        return result

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
        Return all waveform names:
          'awg_rabi_ch1', 'awg_rabi_ch2'  on AWG hardware
          'pb_rabi'                         in PB RAM
          'rabi'                            logical name (both present)
        """
        awg_names = self.awg().get_waveform_names()
        pb_names  = [n for n in self.pulseblaster().get_waveform_names() if n]

        awg_base = {self._logical_name(n) for n in awg_names if n.startswith('awg_')}
        pb_base  = {n[3:] for n in pb_names if n.startswith('pb_')}
        logical  = list(awg_base & pb_base)

        return natural_sort(list(set(awg_names + pb_names + logical)))

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

        @return list: deleted waveform names.
        """
        if isinstance(waveform_name, str):
            waveform_name = [waveform_name]

        deleted = []

        for name in waveform_name:
            if name.startswith('awg_'):
                deleted.extend(self.awg().delete_waveform(name))

            elif name.startswith('pb_'):
                self.log.info(
                    'PulseBlaster has no persistent storage; '
                    '"{0}" cleared on next write_waveform call.'.format(name)
                )
                deleted.append(name)

            else:
                awg_all = self.awg().get_waveform_names()
                prefix  = 'awg_{0}_ch'.format(name)
                to_del  = [n for n in awg_all if n.startswith(prefix)]
                if to_del:
                    deleted.extend(self.awg().delete_waveform(to_del))
                if name in self._written_waveform_names:
                    self._written_waveform_names.remove(name)
                deleted.append(name)

        return natural_sort(deleted)

    def delete_sequence(self, sequence_name):
        """Delete a sequence from the AWG."""
        awg_seq = 'awg_' + sequence_name
        result  = self.awg().delete_sequence(awg_seq)
        if sequence_name in self._written_sequence_names:
            self._written_sequence_names.remove(sequence_name)
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