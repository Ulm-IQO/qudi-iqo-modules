# -*- coding: utf-8 -*-

"""
This file contains the Qudi Hardware file for the Stanford Research Systems
SG384 RF Signal Generator, used as a CW carrier source for external I/Q
modulation (Option 3 installed).

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

This module controls an SRS SG380-series signal generator (SG382, SG384, or
SG386) purely as a fixed-frequency CW carrier source. It implements qudi's
plain MicrowaveInterface (CW-only usage). Frequency scanning
(configure_scan/start_scan/reset_scan) is a required part of that interface
but is NOT implemented here -- calling those methods raises a clear,
descriptive NotImplementedError rather than silently doing nothing.

Two distinct usage modes are supported, selected by the assert_iq_external
config option:

  assert_iq_external: True
      Every set_cw() call actively configures the instrument for external
      I/Q modulation: TYPE 6 (IQ), QFNC 5 (External I/Q source), and
      MODL 1 (modulation enabled). Use this when the AWG's I/Q baseband
      envelopes are physically wired to the SG384's rear-panel I/Q inputs
      and are expected to be actively shaping the carrier. With this mode
      enabled, if nothing is currently driving those inputs (I=Q=0V), the
      carrier will be suppressed by more than 40 dBc, per the SG380
      programming manual's documented IQ carrier suppression spec -- this
      is correct, expected hardware behavior, not a fault of this module.

  assert_iq_external: False
      Every set_cw() call actively configures the instrument for plain,
      unmodulated CW output: MODL 0 (modulation disabled). Use this when
      you want a plain fixed carrier with no I/Q shaping at all, e.g. for
      testing, or for a setup that does not use the SG384's IQ modulator.

  Both modes are symmetric and self-contained: whichever mode is selected,
  set_cw() actively asserts the corresponding state on every call. Neither
  mode depends on, or is affected by, whatever modulation state the
  instrument happened to be left in previously (front panel, a prior
  session, or a manual console command) -- there is no third, ambiguous
  "leave it alone" behavior. This was a deliberate design correction: an
  earlier version of this module left TYPE/QFNC/MODL completely untouched
  when assert_iq_external was False, which meant the actual output
  behavior silently depended on whatever had last been set by hand,
  leading to confusing, session-history-dependent results.

------------------------------------------------------------------------

VERIFIED HARDWARE BEHAVIOR (confirmed on real unit before writing this file)

  - Connection: GPIB, resource string 'GPIB0::28::INSTR' (address may differ
    per installation -- see the gpib_address config option below).
  - *IDN? confirmed: 'Stanford Research Systems,SG384,s/n001796,ver1.21.26'
  - Command terminator confirmed via raw read(): '\r\n', matching the SG380
    programming manual's documented default (ASCII 13, 10).
  - FREQ?, AMPR?, ENBR?, MODL?, TYPE?, QFNC? all confirmed to return exactly
    the values and formats documented in the SG380 programming manual
    (FREQ? in Hz as a plain decimal string; AMPR? in dBm; ENBR?/MODL? as
    0/1; TYPE? and QFNC? as integer mode codes).
  - Confirmed on real hardware: with assert_iq_external=True and nothing
    driving the rear I/Q inputs, the Type-N output amplitude is suppressed
    far below the set AMPR value, consistent with the manual's documented
    IQ carrier suppression spec (>40 dBc). Confirmed on real hardware:
    with assert_iq_external=False (MODL 0), a full-amplitude, unsuppressed
    CW carrier is present at the set frequency/power. Both behaviors were
    verified directly on a scope, not assumed from the manual alone.

------------------------------------------------------------------------

WHY FREQUENCY IS SET BEFORE ASSERTING IQ MODE

Per the SG380 programming manual: "External I/Q Modulation (Option 3) ...
Frequency Range: Carrier frequencies above 400 MHz" and "[MODL command]
may fail if the current modulation type is not allowed at current
settings." The factory-default carrier frequency after a power cycle is
10 MHz -- well below the 400 MHz IQ-mode floor. If this module tried to
assert TYPE=6 (IQ) before the frequency had been set to its real operating
value, that command could legitimately fail on a freshly power-cycled
instrument. To avoid this, set_cw() always writes FREQ first, and only
afterward asserts TYPE/QFNC/MODL, so IQ mode is never selected while the
carrier is still sitting below the 400 MHz floor. In practice this cannot
currently happen anyway, since the frequency constraint enforced by
MicrowaveConstraints already excludes frequencies below the IQ floor (see
_assert_cw_parameters_args(), called at the top of set_cw()) -- but the
ordering is kept defensive in case constraints are ever configured
differently.

------------------------------------------------------------------------

POWER LIMIT UNDER IQ MODULATION

The SG380 manual states: "To avoid output amplifier compression, the
maximum output power setting is +10 dBm during I/Q modulation." This is a
real, documented hardware ceiling (not a guess), and is used as the default
upper power constraint here, distinct from the much higher unmodulated
Type-N ceiling (+16.5 dBm) that would only apply outside of IQ mode. Note
that this constraint is applied globally by this module regardless of the
current assert_iq_external setting -- it is not relaxed when modulation is
off, since the constraints are fixed at module activation and are not
reconfigured dynamically per call.

------------------------------------------------------------------------

Example config for copy-paste:

    sg384:
        module.Class: 'microwave.sg384.SG384'
        options:
            gpib_address: 'GPIB0::28::INSTR'
            visa_timeout: 10000              # ms
            model: 'SG384'                   # 'SG382', 'SG384', or 'SG386'
            #frequency_limits_hz: [400e6, 4.05e9]   # optional manual override
            #power_limits_dbm: [-110.0, 10.0]       # optional manual override
            assert_iq_external: True         # True: force IQ/External/enabled on every set_cw()
                                              # False: force plain CW, modulation disabled, on every set_cw()
"""

import time
import pyvisa

from qudi.interface.microwave_interface import MicrowaveInterface, MicrowaveConstraints
from qudi.core.configoption import ConfigOption
from qudi.util.enums import SamplingOutputMode


# ══════════════════════════════════════════════════════════════════════════════
#  KNOWN MODEL PROFILES
#
#  Only the Type-N front-panel maximum carrier frequency differs between
#  models, per the SG380 programming manual's frequency range table. The
#  minimum usable frequency in this module is governed by the IQ modulator's
#  own 400 MHz floor (see iq_min_frequency_hz config option), not by the
#  model, since all three models document the same "above 400 MHz" IQ
#  modulation requirement.
# ══════════════════════════════════════════════════════════════════════════════

MODEL_MAX_FREQUENCY_HZ = {
    'SG382': 2.025e9,
    'SG384': 4.050e9,
    'SG386': 6.075e9,
}


class SG384(MicrowaveInterface):
    """ Hardware module for an SRS SG380-series signal generator, used as a
    fixed-frequency CW carrier source, optionally with external I/Q modulation.

    Implements qudi's MicrowaveInterface. Only CW operation is supported;
    frequency scanning is not implemented (see module docstring).
    """

    # ── Config options ────────────────────────────────────────────────────────
    _gpib_address = ConfigOption('gpib_address', missing='error')
    _visa_timeout = ConfigOption('visa_timeout', default=10000)  # ms
    _model = ConfigOption('model', default='SG384')

    # None means "not overridden -- derive from model / IQ floor"
    _frequency_limits_cfg = ConfigOption('frequency_limits_hz', default=None)
    _power_limits_cfg      = ConfigOption('power_limits_dbm',   default=None)

    # Per manual: IQ modulation (Option 3) requires carrier frequencies
    # above 400 MHz. This is the same figure across all SG380-series models.
    _iq_min_frequency_hz = ConfigOption('iq_min_frequency_hz', default=400e6)

    # Per manual: "the maximum output power setting is +10 dBm during I/Q
    # modulation" -- used as the default power ceiling unless overridden.
    _default_power_limits = (-110.0, 10.0)

    # Selects which modulation state set_cw() actively asserts on every
    # call -- see module docstring for the full explanation of both modes.
    #   True  -- assert TYPE 6 (IQ), QFNC 5 (External), MODL 1 (enabled)
    #   False -- assert MODL 0 (modulation disabled), plain CW
    # Both branches are active assertions; there is no passive "leave
    # current state alone" behavior.
    _assert_iq_external = ConfigOption('assert_iq_external', default=True)

    # ── SCPI error code descriptions (transcribed from the SG380 programming
    #    manual's "Error Codes" section, not guessed) ──────────────────────────
    _ERROR_CODES = {
        0:   'No Error',
        10:  'Illegal Value',
        11:  'Illegal Mode',
        12:  'Not Allowed',
        13:  'Recall Failed',
        14:  'No Clock Option',
        15:  'No RF Doubler Option',
        16:  'No IQ Option',
        17:  'Failed Self Test',
        30:  'Lost Data',
        32:  'No Listener',
        40:  'Failed ROM Check',
        42:  'Failed EEPROM Check',
        43:  'Failed FPGA Check',
        44:  'Failed SRAM Check',
        45:  'Failed GPIB Check',
        46:  'Failed LF DDS Check',
        47:  'Failed RF DDS Check',
        48:  'Failed 20 MHz PLL',
        49:  'Failed 100 MHz PLL',
        50:  'Failed 19 MHz PLL',
        51:  'Failed 1 GHz PLL',
        52:  'Failed 4 GHz PLL',
        53:  'Failed DAC',
        110: 'Illegal Command',
        111: 'Undefined Command',
        112: 'Illegal Query',
        113: 'Illegal Set',
        114: 'Null Parameter',
        115: 'Extra Parameters',
        116: 'Missing Parameters',
        117: 'Parameter Overflow',
        118: 'Invalid Floating Point Number',
        120: 'Invalid Integer',
        121: 'Integer Overflow',
        122: 'Invalid Hexadecimal',
        126: 'Syntax Error',
        127: 'Illegal Units',
        128: 'Missing Units',
        170: 'Communication Error',
        171: 'Over run',
        254: 'Too Many Errors',
    }

    # =========================================================================
    # Qudi module lifecycle
    # =========================================================================

    def on_activate(self):
        """ Open the VISA connection, confirm identity, and build constraints. """
        self._inst = None
        self._rm = None

        # No frequency scanning is supported. scan_power/scan_frequencies/
        # scan_mode/scan_sample_rate are stored as None to reflect "not
        # configured" -- see module docstring.
        self._scan_power = None
        self._scan_frequencies = None
        self._scan_mode = None
        self._scan_sample_rate = None

        try:
            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(self._gpib_address)
            # Confirmed via console test: response terminator is '\r\n',
            # matching the manual's documented default (13, 10).
            self._inst.read_termination = '\r\n'
            self._inst.write_termination = '\n'
            self._inst.timeout = self._visa_timeout
        except Exception as exc:
            self.log.error(
                'SG384: failed to open VISA connection to "{0}": {1}'.format(
                    self._gpib_address, exc
                )
            )
            raise

        idn = self._query('*IDN?')
        self.log.info('SG384: connected, *IDN? = "{0}"'.format(idn))

        if self._model.upper() not in ('SG382', 'SG384', 'SG386'):
            self.log.warning(
                'SG384: model "{0}" is not one of the known SG380-series '
                'models (SG382, SG384, SG386). Falling back to SG384 '
                'frequency limits unless frequency_limits_hz is set '
                'explicitly in config.'.format(self._model)
            )
        model_max_freq = MODEL_MAX_FREQUENCY_HZ.get(self._model.upper(), MODEL_MAX_FREQUENCY_HZ['SG384'])

        if self._frequency_limits_cfg is not None:
            freq_limits = tuple(self._frequency_limits_cfg)
        else:
            freq_limits = (self._iq_min_frequency_hz, model_max_freq)

        if self._power_limits_cfg is not None:
            power_limits = tuple(self._power_limits_cfg)
        else:
            power_limits = self._default_power_limits

        self.log.info(
            'SG384: resolved constraints -- frequency: {0:.3e} Hz to {1:.3e} Hz, '
            'power: {2:.1f} dBm to {3:.1f} dBm. assert_iq_external={4}.'.format(
                freq_limits[0], freq_limits[1], power_limits[0], power_limits[1],
                self._assert_iq_external
            )
        )

        # Frequency scanning is not implemented by this module (see module
        # docstring) -- scan_size_limits, sample_rate_limits, and scan_modes
        # are set to trivial/empty values so that any logic layer correctly
        # sees "no scan modes supported" via constraints.mode_supported().
        self._constraints = MicrowaveConstraints(
            power_limits=power_limits,
            frequency_limits=freq_limits,
            scan_size_limits=(1, 1),
            sample_rate_limits=(0.0, 0.0),
            scan_modes=(),
        )

        # Report current hardware state for visibility at startup, without
        # changing anything yet -- set_cw() is responsible for any writes.
        try:
            current_freq = float(self._query('FREQ?'))
            current_power = float(self._query('AMPR?'))
            current_enbr = self._query('ENBR?')
            current_modl = self._query('MODL?')
            current_type = self._query('TYPE?')
            current_qfnc = self._query('QFNC?')
            self.log.info(
                'SG384: current hardware state -- FREQ={0:.6e} Hz, AMPR={1:.2f} dBm, '
                'ENBR={2}, MODL={3}, TYPE={4}, QFNC={5}.'.format(
                    current_freq, current_power, current_enbr,
                    current_modl, current_type, current_qfnc
                )
            )
        except Exception as exc:
            self.log.warning('SG384: could not read initial state: {0}'.format(exc))

        self._check_errors()

    def on_deactivate(self):
        """ Close the VISA connection.

        Deliberately does NOT change the RF output state here -- the
        carrier is meant to stay running continuously once turned on via
        cw_on(), independent of this module's own activation lifecycle.
        Call off() explicitly if you want the output disabled before
        deactivating the module.
        """
        try:
            if self._inst is not None:
                self._inst.close()
        except Exception as exc:
            self.log.warning('SG384: error closing VISA connection: {0}'.format(exc))
        finally:
            self._inst = None
            self._rm = None

    # =========================================================================
    # Low-level SCPI helpers
    # =========================================================================

    def _write(self, cmd):
        """ Send a set-only SCPI command (no response expected). """
        self._inst.write(cmd)

    def _query(self, cmd):
        """ Send a SCPI query and return the response string, whitespace-stripped. """
        return self._inst.query(cmd).strip()

    def _check_errors(self):
        """ Drain the SG384's error queue and log any errors found.

        The error queue holds up to 20 entries (per the manual); this reads
        until it reports 0 (no error) or up to 20 times, whichever comes
        first, to avoid an infinite loop if something is very wrong.
        """
        for _ in range(20):
            code_str = self._query('LERR?')
            try:
                code = int(code_str)
            except ValueError:
                self.log.error('SG384: unexpected LERR? response: "{0}"'.format(code_str))
                return
            if code == 0:
                return
            description = self._ERROR_CODES.get(code, 'Unknown error code')
            self.log.error('SG384 error {0}: {1}'.format(code, description))

    # =========================================================================
    # MicrowaveInterface -- properties
    # =========================================================================

    @property
    def constraints(self):
        return self._constraints

    @property
    def is_scanning(self):
        """ Always False: this module never runs a frequency scan. """
        return False

    @property
    def cw_frequency(self):
        """ Query the live carrier frequency from the instrument (Hz).

        Queried live rather than cached, so this always reflects the true
        hardware state even if it was changed from the front panel.
        """
        return float(self._query('FREQ?'))

    @property
    def cw_power(self):
        """ Query the live Type-N output power from the instrument (dBm). """
        return float(self._query('AMPR?'))

    @property
    def scan_power(self):
        """ Not supported: this module never configures a frequency scan. """
        return self._scan_power

    @property
    def scan_frequencies(self):
        """ Not supported: this module never configures a frequency scan. """
        return self._scan_frequencies

    @property
    def scan_mode(self):
        """ Not supported: this module never configures a frequency scan. """
        return self._scan_mode

    @property
    def scan_sample_rate(self):
        """ Not supported: this module never configures a frequency scan. """
        return self._scan_sample_rate

    # =========================================================================
    # MicrowaveInterface -- CW methods
    # =========================================================================

    def off(self):
        """ Disable the Type-N RF output (ENBR 0).

        Blocks until the instrument confirms the output is actually
        disabled (polls ENBR? rather than assuming the write succeeded
        immediately), so this returns only after the device has stopped,
        as required by the interface contract.
        """
        self._write('ENBR 0')

        for _ in range(20):
            if self._query('ENBR?') == '0':
                break
            time.sleep(0.05)
        else:
            self.log.error(
                'SG384: ENBR did not confirm 0 (output off) after repeated '
                'polling. Output may still be enabled -- check the '
                'instrument directly.'
            )

        self._check_errors()

        if self.module_state() == 'locked':
            self.module_state.unlock()

    def set_cw(self, frequency, power):
        """ Configure the CW carrier frequency and power. Does not enable output.

        Writes FREQ first, then actively asserts one of two modulation
        states depending on assert_iq_external (see module docstring for
        the full explanation):

          assert_iq_external=True:
              TYPE 6 (IQ), QFNC 5 (External I/Q source), then, after AMPR,
              MODL 1 (modulation enabled).
          assert_iq_external=False:
              MODL 0 (modulation disabled), written immediately after FREQ.

        Both branches are active assertions -- neither leaves the
        instrument's current modulation state untouched.

        @param float frequency: carrier frequency in Hz
        @param float power: Type-N output power in dBm
        """
        self._assert_cw_parameters_args(frequency, power)

        self._write('FREQ {0!r}'.format(float(frequency)))
        readback_freq = float(self._query('FREQ?'))
        if abs(readback_freq - frequency) > 1.0:
            self.log.warning(
                'SG384: requested frequency {0:.6e} Hz, instrument reports '
                '{1:.6e} Hz after write.'.format(frequency, readback_freq)
            )

        if self._assert_iq_external:
            self._write('TYPE 6')
            if self._query('TYPE?') != '6':
                self.log.error(
                    'SG384: TYPE 6 (IQ modulation) was not accepted by the '
                    'instrument. This can happen if the carrier frequency '
                    'is below the 400 MHz IQ modulation floor -- requested '
                    'frequency was {0:.6e} Hz.'.format(frequency)
                )
            self._write('QFNC 5')
            if self._query('QFNC?') != '5':
                self.log.error(
                    'SG384: QFNC 5 (External I/Q source) was not accepted '
                    'by the instrument.'
                )
        else:
            self._write('MODL 0')
            if self._query('MODL?') != '0':
                self.log.error(
                    'SG384: MODL 0 (modulation disable) was not accepted '
                    'by the instrument.'
                )

        self._write('AMPR {0:.2f}'.format(float(power)))
        readback_power = float(self._query('AMPR?'))
        if abs(readback_power - power) > 0.1:
            self.log.warning(
                'SG384: requested power {0:.2f} dBm, instrument reports '
                '{1:.2f} dBm after write.'.format(power, readback_power)
            )

        if self._assert_iq_external:
            self._write('MODL 1')
            if self._query('MODL?') != '1':
                self.log.error(
                    'SG384: MODL 1 (modulation enable) was not accepted by '
                    'the instrument.'
                )

        self._check_errors()

    def cw_on(self):
        """ Enable the Type-N RF output (ENBR 1).

        Blocks until the instrument confirms the output is actually
        enabled, as required by the interface contract.
        """
        if self.module_state() == 'idle':
            self.module_state.lock()

        self._write('ENBR 1')

        for _ in range(20):
            if self._query('ENBR?') == '1':
                break
            time.sleep(0.05)
        else:
            self.log.error(
                'SG384: ENBR did not confirm 1 (output on) after repeated '
                'polling. Output may not actually be enabled -- check the '
                'instrument directly.'
            )
            if self.module_state() == 'locked':
                self.module_state.unlock()
            return

        self._check_errors()

    def iq_modulator_active(self):
        """ Live hardware check: True only if the instrument currently
        reports TYPE=6 (IQ), QFNC=5 (External I/Q source), and MODL=1
        (modulation enabled) -- i.e. genuinely configured and enabled for
        external IQ modulation right now, on the real hardware, not just
        what assert_iq_external is configured to enforce on the next
        set_cw() call. assert_iq_external is only ever pushed to hardware
        from inside set_cw() -- at any other time (e.g. right after
        on_activate(), before set_cw() has been called this session) the
        instrument's actual modulation state may still reflect whatever
        was left over from a previous session or the front panel.

        Public, non-interface method -- intended for other modules (e.g.
        AlwaysOnMicrowaveInterfuse) that need to verify this live state
        before deciding whether it's safe to auto-enable continuous
        output. Uses the same query-and-compare-string pattern already
        used for readback verification in set_cw().

        @return bool: True if TYPE/QFNC/MODL are all confirmed as above.
        """
        try:
            type_ok = self._query('TYPE?') == '6'
            qfnc_ok = self._query('QFNC?') == '5'
            modl_ok = self._query('MODL?') == '1'
            return type_ok and qfnc_ok and modl_ok
        except Exception as exc:
            self.log.warning(
                'SG384: iq_modulator_active() check failed: {0}'.format(exc)
            )
            return False

    # =========================================================================
    # MicrowaveInterface -- scanning (not implemented)
    # =========================================================================

    def configure_scan(self, power, frequencies, mode, sample_rate):
        """ Not implemented. This module only supports fixed-CW operation.

        See module docstring for why frequency scanning is out of scope
        for this setup (pulse shaping is done entirely by an external I/Q
        source when assert_iq_external=True; the SG384 only ever supplies
        a fixed carrier).
        """
        raise NotImplementedError(
            'SG384 hardware module: frequency scanning is not implemented. '
            'This module only supports fixed-CW operation via set_cw()/'
            'cw_on(). See module docstring for details.'
        )

    def start_scan(self):
        """ Not implemented -- see configure_scan(). """
        raise NotImplementedError(
            'SG384 hardware module: frequency scanning is not implemented. '
            'See configure_scan() docstring for details.'
        )

    def reset_scan(self):
        """ Not implemented -- see configure_scan(). """
        raise NotImplementedError(
            'SG384 hardware module: frequency scanning is not implemented. '
            'See configure_scan() docstring for details.'
        )