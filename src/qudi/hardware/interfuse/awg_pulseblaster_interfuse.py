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

───────────────────────────────────────────────────────────────────────────────
DESIGN OVERVIEW
───────────────────────────────────────────────────────────────────────────────

Channel naming convention
  AWG channels (native):   a_ch1, a_ch2, d_ch1, d_ch2, d_ch3, d_ch4
  PulseBlaster channels:   d_ch5, d_ch6, d_ch7, ...   (continue the d_ch* sequence)

  The offset at which PB channels start is set by pb_channel_d_offset (default 5).
  This means all channels look identical to qudi — they are all d_ch*, so the
  GUI creates voltage widgets for them, they appear in laser/gate dropdowns, and
  the activation config validation works correctly.

Waveform naming
  write_waveform('rabi', ...) creates internally:
    'awg_rabi_ch1', 'awg_rabi_ch2'   stored on AWG hardware
    'pb_rabi'                          stored in PB RAM
  get_waveform_names() returns all device names plus the logical name 'rabi'.
  get_loaded_assets()  returns the logical name so the GUI shows 'rabi'.

Granularity / idle extension
  waveform_length.step = LCM(AWG_waveform_step, AWG_samples_per_PB_clock_cycle).
  Every pulse element length is a multiple of both devices' minimum periods,
  making the AWG→PB decimation always lossless (no aliasing, no rounding).

Trigger delay compensation
  awg_trigger_delay (seconds) = measured time from PB trigger edge to first
  AWG analog output sample.  The PB sample array is circularly shifted earlier
  by this amount so that laser, gate and other PB signals are aligned with the
  AWG output at the measurement point.

Start / stop ordering
  trigger_master = 'pulseblaster'  →  arm AWG first (TRIG mode, status=2),
                                       then start PB (fires the trigger edge)
  trigger_master = 'awg'           →  reverse order

Example config for copy-paste:

awg_pb_interfuse:
    module.Class: 'interfuse.awg_pulseblaster_interfuse.AwgPulseBlasterInterfuse'
    connect:
        awg: 'pulser_awg7000'
        pulseblaster: 'pulser_pulseblaster'
    options:
        trigger_master: 'pulseblaster'  # which device starts the experiment
        awg_trigger_delay: 100e-9       # seconds — AWG output latency after trigger
        pb_channels: [0, 1, 2, 3, 4]   # PB hardware channel indices to expose
        pb_channel_d_offset: 5          # PB ch0 → d_ch5, ch1 → d_ch6, ...
        # Optional: name of activation config to apply automatically at startup.
        # Example: 'A1_M1_M2_pb3' = a_ch1 + d_ch1 + d_ch2 + d_ch5 + d_ch6 + d_ch7
        default_activation_config: 'A1_M1_M2_pb3'
"""

from math import gcd
import numpy as np

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.interface.pulser_interface import PulserInterface, PulserConstraints, SequenceOption
from qudi.util.helpers import natural_sort


class AwgPulseBlasterInterfuse(PulserInterface):
    """
    Single PulserInterface that coordinates a Tektronix AWG7000 and a
    SpinCore PulseBlaster ESR-Pro for qudi pulsed measurement experiments.
    """

    # ── Connectors ─────────────────────────────────────────────────────────────
    # NOTE: No leading underscore. Qudi derives the connector name directly
    # from the Python attribute name, which must match the 'connect' key in
    # the yaml config exactly. ConfigOption uses underscores but Connector does not.
    awg          = Connector(interface=PulserInterface)
    pulseblaster = Connector(interface=PulserInterface)

    # ── Config options ──────────────────────────────────────────────────────────
    _trigger_master    = ConfigOption('trigger_master',    default='pulseblaster', missing='warn')
    _awg_trigger_delay = ConfigOption('awg_trigger_delay', default=0.0,            missing='nothing')
    _pb_channels       = ConfigOption('pb_channels',       default=[0, 1, 2, 3],   missing='warn')

    # First d_ch number assigned to PulseBlaster channels.
    # AWG uses d_ch1..d_ch4 (2 markers × 2 analog channels).
    # PB channels follow immediately: d_ch5, d_ch6, ...
    # Change this only if your AWG has more or fewer marker channels.
    _pb_d_offset = ConfigOption('pb_channel_d_offset', default=5, missing='nothing')

    # Optional: activation config name to apply automatically at startup.
    # Saves the user from having to select it manually in Generator Settings.
    _default_activation_config = ConfigOption(
        'default_activation_config', default=None, missing='nothing'
    )

    # =========================================================================
    # Module lifecycle
    # =========================================================================

    def on_activate(self):
        """Initialise internal state and compute rate-dependent parameters."""

        # Waveform / sequence tracking
        self._written_waveform_names = []
        self._written_sequence_names = []
        self._loaded_name            = ''
        self._loaded_type            = None

        # PB sample buffer: accumulates downsampled chunks across a chunked
        # upload so the trigger-delay circular shift is applied to the full
        # periodic waveform, not to individual chunks.
        self._pb_sample_buffer    = {}
        self._pb_current_wfm_name = ''

        # PB channel active states tracked internally.
        #
        # WHY internal tracking instead of calling pulseblaster().set_active_channels():
        #   The PB module's activation_config only accepts exactly 4 channels
        #   ('4_ch') or exactly 21 channels ('all'). Any other count is rejected
        #   with "Requested channel configuration is not in the hardware constraints",
        #   which causes the pulse block editor to hide the PB channels entirely.
        #   By tracking state here we bypass that constraint completely — the PB
        #   module only ever needs to receive sample data (write_waveform), not an
        #   explicit channel activation command.
        #
        # All channels start as False so the logic does not include them in the
        # default activation set — the user explicitly selects a config in
        # Generator Settings (or the default_activation_config yaml option applies it).
        self._pb_active_channels = {
            self._pb_index_to_d_ch(i): False
            for i in range(len(self._pb_channels))
        }

        self._update_rate_params()

        # Apply default activation config if one is specified in the yaml config
        if self._default_activation_config is not None:
            self._apply_default_activation_config()

    def on_deactivate(self):
        """Clean up — nothing to release for a pure software interfuse."""
        pass

    # =========================================================================
    # Private helpers — channel naming
    # =========================================================================

    def _is_pb_d_ch(self, ch_name):
        """
        Return True if ch_name belongs to the PulseBlaster (d_chN, N >= offset).

        Examples (with pb_d_offset=5):
          'd_ch1'  → False   (AWG marker)
          'd_ch5'  → True    (PB channel 0)
          'a_ch1'  → False   (AWG analog)
        """
        if not ch_name.startswith('d_ch'):
            return False
        try:
            return int(ch_name.rsplit('_ch', 1)[1]) >= self._pb_d_offset
        except (ValueError, IndexError):
            return False

    def _pb_index_to_d_ch(self, list_index):
        """
        Convert a position in self._pb_channels to its qudi d_ch name.

        Example (pb_d_offset=5):
          0 → 'd_ch5'
          1 → 'd_ch6'
          4 → 'd_ch9'
        """
        return 'd_ch{0:d}'.format(self._pb_d_offset + list_index)

    def _d_ch_to_pb_hw(self, d_ch_name):
        """
        Convert a d_ch name (e.g. 'd_ch6') to the PB hardware channel number
        as used by the PulseBlaster DLL (e.g. 1).

        Returns None if the channel is not a configured PB channel.

        Example (pb_d_offset=5, pb_channels=[0,1,2,3,4]):
          'd_ch5' → 0
          'd_ch6' → 1
          'd_ch7' → 2
          'd_ch3' → None  (AWG marker)
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
        """
        Return the list of all PB channel names in d_ch* notation.

        Example (pb_d_offset=5, pb_channels=[0,1,2,3,4]):
          ['d_ch5', 'd_ch6', 'd_ch7', 'd_ch8', 'd_ch9']
        """
        return [self._pb_index_to_d_ch(i) for i in range(len(self._pb_channels))]

    @staticmethod
    def _lcm(a, b):
        """Least common multiple of two positive integers."""
        return abs(a * b) // gcd(a, b)

    @staticmethod
    def _logical_name(awg_wfm_name):
        """
        Strip device prefix and channel suffix to derive the logical name.

        Examples:
          'awg_rabi_ch1'  →  'rabi'
          'awg_rabi'      →  'rabi'
          'rabi_ch1'      →  'rabi'
          'rabi'          →  'rabi'
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
        (Re)calculate all sample-rate-dependent parameters.

        Must be called:
          - Once at on_activate()
          - After set_sample_rate()
          - After set_interleave() (interleave changes the AWG sample rate)
        """
        awg_c = self.awg().get_constraints()
        pb_c  = self.pulseblaster().get_constraints()

        self._awg_sample_rate = self.awg().get_sample_rate()
        self._pb_sample_rate  = pb_c.sample_rate.default   # PB clock is fixed

        # Number of AWG samples that fit in one PB clock cycle
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

        # LCM granularity: smallest waveform step satisfying both devices.
        # Every pulse element length must be a multiple of this value.
        # This guarantees the AWG→PB stride decimation is always lossless.
        awg_gran       = int(awg_c.waveform_length.step)
        self._lcm_gran = self._lcm(awg_gran, self._awg_per_pb)

        # Trigger delay in PB clock cycles
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
        """
        Apply the yaml-specified default_activation_config at startup.

        Sets both AWG hardware channel states and the internal PB channel
        state dict so get_active_channels() immediately returns the correct
        set without requiring a manual Generator Settings selection.
        """
        constraints = self.get_constraints()
        available   = constraints.activation_config

        if self._default_activation_config not in available:
            self.log.warning(
                'default_activation_config "{0}" not found in interfuse constraints.\n'
                'Available configs: {1}\n'
                'Starting with all channels inactive.'
                ''.format(
                    self._default_activation_config, list(available.keys())
                )
            )
            return

        target_set = available[self._default_activation_config]

        # Build a True/False dict for every possible channel
        all_possible    = {**self.awg().get_active_channels(), **self._pb_active_channels}
        activation_dict = {ch: (ch in target_set) for ch in all_possible}

        self.set_active_channels(activation_dict)

        self.log.info(
            'Default activation config "{0}" applied. '
            'Active channels: {1}'
            ''.format(self._default_activation_config, sorted(target_set))
        )

    # =========================================================================
    # PulserInterface — constraints
    # =========================================================================

    def get_constraints(self):
        """
        Return merged constraints for the combined AWG + PulseBlaster system.

        Key differences from a standalone AWG:
          waveform_length.step  →  LCM(AWG_gran, AWG_samples_per_PB_cycle)
          activation_config     →  each AWG config extended with PB channel subsets
          d_ch_high/low         →  PB channels fixed at 0.0 V / 3.3 V (LVTTL)
        """
        awg_c = self.awg().get_constraints()
        c     = PulserConstraints()

        # ── Sample rate: governed by AWG ──────────────────────────────────────
        c.sample_rate.min     = awg_c.sample_rate.min
        c.sample_rate.max     = awg_c.sample_rate.max
        c.sample_rate.step    = awg_c.sample_rate.step
        c.sample_rate.default = awg_c.sample_rate.default

        # ── Waveform length: LCM granularity forces elements to match both ────
        c.waveform_length.min     = awg_c.waveform_length.min
        c.waveform_length.max     = awg_c.waveform_length.max
        c.waveform_length.step    = self._lcm_gran   # ← KEY constraint
        c.waveform_length.default = awg_c.waveform_length.default

        # ── Analog levels: AWG only ───────────────────────────────────────────
        c.a_ch_amplitude.min     = awg_c.a_ch_amplitude.min
        c.a_ch_amplitude.max     = awg_c.a_ch_amplitude.max
        c.a_ch_amplitude.step    = awg_c.a_ch_amplitude.step
        c.a_ch_amplitude.default = awg_c.a_ch_amplitude.default

        c.a_ch_offset.min     = awg_c.a_ch_offset.min
        c.a_ch_offset.max     = awg_c.a_ch_offset.max
        c.a_ch_offset.step    = awg_c.a_ch_offset.step
        c.a_ch_offset.default = awg_c.a_ch_offset.default

        # ── Digital levels: AWG markers adjustable; PB is fixed LVTTL ─────────
        # Using the AWG range for all d_ch* channels. PB channels will show
        # 0.0 V / 3.3 V (set in get_digital_level) and cannot be changed
        # (set_digital_level ignores PB channels).
        c.d_ch_low.min     = awg_c.d_ch_low.min
        c.d_ch_low.max     = awg_c.d_ch_low.max
        c.d_ch_low.step    = awg_c.d_ch_low.step
        c.d_ch_low.default = awg_c.d_ch_low.default

        c.d_ch_high.min     = awg_c.d_ch_high.min
        c.d_ch_high.max     = awg_c.d_ch_high.max
        c.d_ch_high.step    = awg_c.d_ch_high.step
        c.d_ch_high.default = awg_c.d_ch_high.default

        # ── Waveform / sequence counts: AWG ───────────────────────────────────
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

        # ── Activation configs ────────────────────────────────────────────────
        # For each AWG config, generate:
        #   <cfg_name>          : AWG channels only (backward-compatible)
        #   <cfg_name>_pb1      : AWG channels + first  PB channel
        #   <cfg_name>_pb2      : AWG channels + first2 PB channels
        #   ...
        #   <cfg_name>_pb<N>    : AWG channels + all N PB channels
        # Plus:
        #   pb_only             : all configured PB channels, no AWG
        #
        # Example with AWG config 'A1_M1_M2' and pb_channels=[0,1,2,3,4]:
        #   'A1_M1_M2'           = {a_ch1, d_ch1, d_ch2}
        #   'A1_M1_M2_pb1'       = {a_ch1, d_ch1, d_ch2, d_ch5}
        #   'A1_M1_M2_pb2'       = {a_ch1, d_ch1, d_ch2, d_ch5, d_ch6}
        #   'A1_M1_M2_pb3'       = {a_ch1, d_ch1, d_ch2, d_ch5, d_ch6, d_ch7}
        #   'A1_M1_M2_pb4'       = {a_ch1, d_ch1, d_ch2, d_ch5, d_ch6, d_ch7, d_ch8}
        #   'A1_M1_M2_pb5'       = {a_ch1, d_ch1, d_ch2, d_ch5, d_ch6, d_ch7, d_ch8, d_ch9}
        activation_config = {}

        for cfg_name, awg_ch_set in awg_c.activation_config.items():
            # AWG-only (unchanged)
            activation_config[cfg_name] = awg_ch_set

            # AWG + first n PB channels
            for n_pb in range(1, len(self._pb_channels) + 1):
                pb_subset = frozenset(
                    self._pb_index_to_d_ch(i)
                    for i in range(n_pb)
                )
                activation_config['{0}_pb{1:d}'.format(cfg_name, n_pb)] = (
                    awg_ch_set | pb_subset
                )

        # PB-only config for standalone PB tests
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

        trigger_master = 'pulseblaster':
          1. Arm AWG  (TRIG mode → status 2, waiting for trigger edge)
          2. Start PB (its TTL output fires the AWG trigger)

        trigger_master = 'awg':
          1. Arm PB
          2. Start AWG (its output fires the PB trigger)

        @return int: status code from the master device.
        """
        master = str(self._trigger_master).lower()

        if master == 'pulseblaster':
            awg_status = self.awg().pulser_on()
            self.log.debug('AWG armed, status={0}.'.format(awg_status))
            pb_status  = self.pulseblaster().pulser_on()
            self.log.debug('PulseBlaster started, status={0}.'.format(pb_status))
        else:
            pb_status  = self.pulseblaster().pulser_on()
            awg_status = self.awg().pulser_on()

        return self.get_status()[0]

    def pulser_off(self):
        """
        Stop combined output.
        The master (trigger source) is stopped first to prevent stray triggers
        reaching the slave device after it has already been stopped.

        @return int: status code from the master device.
        """
        master = str(self._trigger_master).lower()

        if master == 'pulseblaster':
            self.pulseblaster().pulser_off()   # stop trigger source first
            self.awg().pulser_off()
        else:
            self.awg().pulser_off()
            self.pulseblaster().pulser_off()

        return self.get_status()[0]

    # =========================================================================
    # PulserInterface — status / sample rate
    # =========================================================================

    def get_status(self):
        """Return the status of the trigger-master device."""
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
    # PulserInterface — analog levels (AWG only)
    # =========================================================================

    def get_analog_level(self, amplitude=None, offset=None):
        """Pass through to AWG — PulseBlaster has no analog outputs."""
        return self.awg().get_analog_level(amplitude=amplitude, offset=offset)

    def set_analog_level(self, amplitude=None, offset=None):
        """Pass through to AWG — PulseBlaster has no analog outputs."""
        return self.awg().set_analog_level(amplitude=amplitude, offset=offset)

    # =========================================================================
    # PulserInterface — digital levels
    # =========================================================================

    def get_digital_level(self, low=None, high=None):
        """
        Return digital output voltage levels for all channels.

        AWG marker channels (d_ch1..d_ch4): queried live from AWG hardware.
        PB channels (d_ch5, d_ch6, ...):    fixed LVTTL — 0.0 V low, 3.3 V high.

        All channels are returned in the default (no-argument) call so that
        the pulsed GUI creates voltage-setting widgets for PB channels.  PB
        voltage widgets will display 0.0 V / 3.3 V and are not editable
        (set_digital_level silently ignores PB channel requests).
        """
        pb_names = self._all_pb_d_ch_names()

        if low is None and high is None:
            # Default query — return AWG levels plus fixed PB levels
            awg_low, awg_high = self.awg().get_digital_level()
            for ch in pb_names:
                awg_low[ch]  = 0.0
                awg_high[ch] = 3.3
            return awg_low, awg_high

        # Explicit query — route to correct device
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
        Set digital output voltage levels.

        AWG marker channels: passed to AWG hardware.
        PB channels:         fixed LVTTL — requests are logged and ignored.
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
        Return the combined active-channel dict for all devices.

        AWG channels (a_ch*, d_ch1..d_ch4): queried live from AWG hardware.
        PB channels  (d_ch5, d_ch6, ...):   from internal _pb_active_channels dict.

        The PB module is NOT queried directly because its get_active_channels()
        only reflects _current_activation_config, which we never update
        (to avoid its constraint-validation rejecting our channel subsets).
        """
        awg_active = self.awg().get_active_channels()
        all_active = {**awg_active, **self._pb_active_channels}

        if ch is not None:
            all_active = {k: v for k, v in all_active.items() if k in ch}
        return all_active

    def set_active_channels(self, ch=None):
        """
        Activate or deactivate channels.

        Channels below pb_d_offset (a_ch*, d_ch1..d_ch4):
          Passed to awg().set_active_channels() — AWG validates constraints.

        Channels at or above pb_d_offset (d_ch5, d_ch6, ...):
          Internal _pb_active_channels dict is updated only.
          pulseblaster().set_active_channels() is NEVER called because the
          PB module only accepts exactly 4 or 21 channels; any other count
          is rejected and makes PB channels invisible in the GUI.
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
                    'set_active_channels: "{0}" is not a configured PB channel '
                    '(pb_channels={1}, offset={2}). Ignored.'
                    ''.format(d_ch_name, self._pb_channels, self._pb_d_offset)
                )

        return self.get_active_channels()

    # =========================================================================
    # PulserInterface — waveform upload
    # =========================================================================

    def write_waveform(self, name, analog_samples, digital_samples,
                       is_first_chunk, is_last_chunk, total_number_of_samples):
        """
        Upload a waveform to both the AWG ('awg_{name}') and the PB ('pb_{name}').

        Channel routing
        ───────────────
          a_ch*, d_ch1..d_ch4  → AWG unchanged (analog + marker channels).
          d_ch5, d_ch6, ...    → PulseBlaster:
                                   1. 'd_ch5' remapped to PB hw ch0, 'd_ch6' → hw ch1, etc.
                                   2. Downsampled by awg_per_pb (lossless due to LCM gran).
                                   3. Circularly shifted earlier by delay_pb_samples.

        Chunked upload handling
        ───────────────────────
          The AWG supports chunked uploads natively and receives each chunk
          directly.  PB samples are accumulated in _pb_sample_buffer across all
          chunks and uploaded as a single complete block on the last chunk.
          This is necessary so the circular delay shift is applied to the full
          periodic waveform rather than to individual fragments.

        Return value
        ────────────
          (total_number_of_samples, awg_waveform_names)
          awg_waveform_names carries '_chN' suffixes (e.g. ['awg_rabi_ch1'])
          so qudi's sequence_generator_logic can build load_dicts automatically.
        """
        # ── 1. Split sample dicts by channel type ─────────────────────────────
        awg_digital = {
            k: v for k, v in digital_samples.items()
            if not self._is_pb_d_ch(k)
        }

        # PB channels: remap d_ch5→d_ch0, d_ch6→d_ch1, ...
        # The PB module expects channel names in its own numbering (d_ch0..d_ch20).
        pb_digital_raw = {}
        for d_ch_name, samples in digital_samples.items():
            if self._is_pb_d_ch(d_ch_name):
                hw_ch = self._d_ch_to_pb_hw(d_ch_name)
                if hw_ch is not None:
                    pb_digital_raw['d_ch{0:d}'.format(hw_ch)] = samples
                else:
                    self.log.warning(
                        'write_waveform: "{0}" has no PB hardware mapping '
                        '(pb_channels={1}, offset={2}). Skipped.'
                        ''.format(d_ch_name, self._pb_channels, self._pb_d_offset)
                    )

        # ── 2. Upload to AWG ──────────────────────────────────────────────────
        awg_name = 'awg_' + name

        awg_written, awg_waveforms = self.awg().write_waveform(
            awg_name, analog_samples, awg_digital,
            is_first_chunk, is_last_chunk, total_number_of_samples
        )

        if awg_written < 0:
            self.log.error('AWG write_waveform failed for "{0}".'.format(awg_name))
            return -1, []

        # ── 3. Buffer and downsample PB samples ───────────────────────────────
        # Reset buffer at the start of a new waveform (even if no PB channels
        # are present) to avoid stale data from a previous incomplete upload.
        if is_first_chunk:
            self._pb_sample_buffer    = {}
            self._pb_current_wfm_name = 'pb_' + name

        for pb_key, samples in pb_digital_raw.items():
            # Stride decimation: keep every awg_per_pb-th sample.
            # LCM granularity guarantees all samples in each group are identical,
            # so this is lossless (no aliasing).
            downsampled = samples[::self._awg_per_pb].copy()

            if pb_key in self._pb_sample_buffer:
                self._pb_sample_buffer[pb_key] = np.concatenate(
                    [self._pb_sample_buffer[pb_key], downsampled]
                )
            else:
                self._pb_sample_buffer[pb_key] = downsampled

        # ── 4. Last chunk: apply delay roll and upload full waveform to PB ────
        if is_last_chunk and self._pb_sample_buffer:

            pb_digital_full = {k: v.copy() for k, v in self._pb_sample_buffer.items()}

            # Circular left-shift: moves all PB channels earlier in time by
            # delay_pb_samples, so that PB outputs (laser, gate, etc.) arrive
            # at the measurement point aligned with the AWG analog output.
            if self._delay_pb_samples > 0:
                for d_key in pb_digital_full:
                    pb_digital_full[d_key] = np.roll(
                        pb_digital_full[d_key], -self._delay_pb_samples
                    )

            pb_name  = self._pb_current_wfm_name
            pb_total = len(next(iter(pb_digital_full.values())))

            # PB module always receives a single complete block
            pb_written, _ = self.pulseblaster().write_waveform(
                pb_name, {}, pb_digital_full,
                True, True, pb_total
            )

            # Clear buffer regardless of success to prevent poisoning next call
            self._pb_sample_buffer    = {}
            self._pb_current_wfm_name = ''

            if pb_written < 0:
                self.log.error(
                    'PulseBlaster write_waveform failed for "{0}".'.format(pb_name)
                )
                return -1, []

            self.log.debug(
                'Uploaded "{0}" to PulseBlaster: {1} samples @ {2:.3e} Hz.'
                ''.format(pb_name, pb_total, self._pb_sample_rate)
            )

        # ── 5. Track logical name ─────────────────────────────────────────────
        if is_last_chunk and name not in self._written_waveform_names:
            self._written_waveform_names.append(name)

        # Return AWG waveform names (with '_chN' suffix) so qudi logic can
        # automatically build load_dicts and sequence parameter lists.
        return total_number_of_samples, awg_waveforms

    # =========================================================================
    # PulserInterface — sequence upload
    # =========================================================================

    def write_sequence(self, name, sequence_parameter_list):
        """
        Write a sequence to the AWG as 'awg_{name}'.

        The waveform names inside sequence_parameter_list are the names
        returned by write_waveform (e.g. 'awg_rabi_ch1') and are passed
        through unchanged.

        PulseBlaster note
        ─────────────────
        The PB has no sequence memory. It loops its last-loaded waveform,
        sending one trigger edge per loop cycle. Each edge advances the AWG
        to its next sequence step. For typical experiments (Rabi, Ramsey,
        ODMR, ...) all PB timing patterns within a sequence are identical,
        so the PB retains the correct one automatically without any extra
        action here.

        @return int: number of sequence steps written, or -1 on failure.
        """
        awg_seq_name = 'awg_' + name
        result = self.awg().write_sequence(awg_seq_name, sequence_parameter_list)

        if result >= 0 and name not in self._written_sequence_names:
            self._written_sequence_names.append(name)

        return result

    # =========================================================================
    # PulserInterface — loading
    # =========================================================================

    def load_waveform(self, load_dict):
        """
        Load a waveform on both the AWG and the PulseBlaster.

        Accepted formats:
          list : ['awg_rabi_ch1', 'awg_rabi_ch2']   (return value of write_waveform)
          dict : {1: 'awg_rabi_ch1', 2: 'awg_rabi_ch2'}
          dict : {1: 'rabi_ch1', 2: 'rabi_ch2'}     (bare logical names also work)

        The logical base name is derived from the first entry. The corresponding
        'pb_{logical_name}' waveform is then loaded on the PulseBlaster.

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

        # Build AWG load dict and derive logical name
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
            logical_name          = logical   # same base for all channels

        # Load on AWG
        self.awg().load_waveform(awg_load_dict)

        # Load on PulseBlaster (may not exist for AWG-only configs)
        pb_name = 'pb_{0}'.format(logical_name)
        if pb_name in self.pulseblaster().get_waveform_names():
            self.pulseblaster().load_waveform([pb_name])
        else:
            self.log.info(
                'No PulseBlaster waveform "{0}" found; '
                'AWG-only load assumed.'.format(pb_name)
            )

        self._loaded_name = logical_name
        self._loaded_type = 'waveform'

        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """
        Load an AWG sequence ('awg_{sequence_name}').
        The PulseBlaster keeps its currently loaded looping waveform.

        @return dict: loaded assets per channel.
        """
        awg_seq_name = ('awg_' + sequence_name
                        if not sequence_name.startswith('awg_')
                        else sequence_name)

        result = self.awg().load_sequence(awg_seq_name)

        self._loaded_name = sequence_name
        self._loaded_type = 'sequence'

        return result

    def get_loaded_assets(self):
        """
        Return currently loaded assets using logical names.

        The AWG is queried as the source of truth.
        'awg_rabi_ch1' is reported as 'rabi' so that the qudi GUI shows the
        human-readable logical name and Invoke Settings works correctly.

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
    # PulserInterface — waveform / sequence name lists
    # =========================================================================

    def get_waveform_names(self):
        """
        Return all waveform names visible to qudi:
          'awg_rabi_ch1', 'awg_rabi_ch2'  stored on AWG hardware
          'pb_rabi'                         stored in PB RAM
          'rabi'                            logical name (both sides present)

        The logical name is included so the GUI asset list is readable and
        write_waveform can verify the upload succeeded.
        """
        awg_names = self.awg().get_waveform_names()
        pb_names  = [n for n in self.pulseblaster().get_waveform_names() if n]

        awg_base = {self._logical_name(n) for n in awg_names if n.startswith('awg_')}
        pb_base  = {n[3:] for n in pb_names if n.startswith('pb_')}
        logical  = list(awg_base & pb_base)

        return natural_sort(list(set(awg_names + pb_names + logical)))

    def get_sequence_names(self):
        """Return logical sequence names tracked since module activation."""
        return list(self._written_sequence_names)

    # =========================================================================
    # PulserInterface — deletion
    # =========================================================================

    def delete_waveform(self, waveform_name):
        """
        Delete waveform(s).

        'awg_rabi_ch1'  →  direct AWG deletion.
        'pb_rabi'       →  PB has no persistent storage; logged only.
        'rabi'          →  finds and deletes all 'awg_rabi_chN' on AWG.

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
                    '"{0}" exists only in RAM and is cleared on the next '
                    'write_waveform call.'.format(name)
                )
                deleted.append(name)

            else:
                # Logical name — find and delete all 'awg_<name>_chN' variants
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
        """
        Set AWG interleave state and recalculate LCM granularity.
        Interleave doubles the AWG sample rate, which changes awg_per_pb.
        """
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