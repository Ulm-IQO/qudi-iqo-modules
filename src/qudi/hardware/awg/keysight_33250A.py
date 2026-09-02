# -*- coding: utf-8 -*-
"""
Qudi hardware module for the Agilent/HP 33250A single-channel
80 MHz function/arbitrary waveform generator.

Tested against:
    Agilent Technologies, 33250A, 0, 1.03-1.01-1.00-03-1

-------------------------------------------------------------------------------
DESIGN OVERVIEW
-------------------------------------------------------------------------------

Why this module implements only qudi.core.module.Base, not a specific
qudi Interface
---------------
Same reasoning as for other general-purpose function generators in this
qudi setup: no existing qudi Interface cleanly matches the full feature
set (function shape, frequency, amplitude, offset, burst, sweep, basic
AM/FM, arbitrary waveform upload). This module exposes a complete direct
get_*/set_* API instead, intended to be used directly or wrapped later by
a thin interfuse if a specific Interface contract is needed for a
particular experiment.

SINGLE CHANNEL -- no channel argument
---------------------------------------
Unlike the 33500B series, the 33250A has exactly ONE output channel, and
its SCPI command set has NO "SOUR{n}:" channel prefix at all -- every
command implicitly addresses the single channel (e.g. "FREQ 1E6", not
"SOUR1:FREQ 1E6"). All methods here therefore take no channel argument.
Do not confuse this module with an Agilent33522A-style multi-channel
module -- the command syntax is genuinely different, not just missing a
channel number.

Constraints are queried DYNAMICALLY from the instrument, not hardcoded
-----------------------------------------------------------------------
get_frequency_limits(), get_amplitude_limits() etc. send "<command>? MIN"
/ "<command>? MAX" to the instrument itself rather than hardcoding
per-model numbers -- avoiding the exact class of bug repeatedly found in
this qudi setup's AWG modules, where a hardcoded/assumed hardware
constant silently drifted from the real hardware's actual behavior. The
one exception is get_frequency_limits(), which is function-shape-
dependent on this instrument (sine vs. pulse have very different maxima)
-- callers should re-query after changing shape.

Arbitrary waveform upload -- 33250A specifics
-----------------------------------------------
The 33250A's arbitrary-waveform subsystem is simpler than the 33500B
series: there is no per-channel DATA:VOL:CAT catalog with named
waveforms held simultaneously -- DATA:DAC / DATA:ARB write to volatile
memory as ONE working buffer at a time (labelled "VOLATILE"), which is
then selected as the active function shape via "FUNC:USER VOLATILE" or
by referencing a name if copied to non-volatile memory with DATA:COPY.
write_arbitrary_waveform() therefore does not take a name parameter for
the initial upload -- the uploaded data always becomes "VOLATILE". Call
copy_arbitrary_to_nonvolatile() afterward if you want to keep it under a
name across sessions (limited slots -- see instrument manual, typically
4 non-volatile slots depending on firmware/model variant).

Supports two upload formats:
  'float'      -- ASCII list of floats in [-1.0, 1.0] via DATA:ARB (slow,
                  simple, fine for short waveforms / debugging).
  'dac_binary' -- IEEE-488.2 binary block of 16-bit signed integers via
                  DATA:DAC VOLATILE, (fast, default). Uses pyvisa's
                  write_binary_values(), which handles the
                  '#<ndigits><nbytes>' block header automatically.

Thread safety
-------------
All VISA I/O (self.write/self.query) is serialized through self._lock
(a plain, non-reentrant Mutex).

No auto-reset on activation
-----------------------------
on_activate() does NOT send *RST -- activating this qudi module should
not disturb whatever the instrument is currently outputting. Call
reset() explicitly for factory-default state.

Example qudi config:

    signal_generator_33250a:
        module.Class: 'agilent.agilent_33250a.Agilent33250A'
        options:
            visa_address: 'GPIB0::10::INSTR'
            timeout_ms: 5000
"""

import numpy as np

try:
    import pyvisa as visa
except ImportError:
    import visa

from qudi.core.module import Base
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex


class Agilent33250A(Base):
    """
    Qudi hardware module for the Agilent/HP 33250A single-channel
    function/arbitrary waveform generator.
    """

    # ── Config options ───────────────────────────────────────────────────
    _visa_address = ConfigOption('visa_address', default='GPIB0::10::INSTR', missing='warn')
    _timeout_ms   = ConfigOption('timeout_ms',   default=5000,               missing='nothing')
    _write_term   = ConfigOption('write_termination', default='\n',        missing='nothing')
    _read_term    = ConfigOption('read_termination',  default='\n',        missing='nothing')

    _VALID_SHAPES     = ('SIN', 'SQU', 'RAMP', 'PULS', 'NOIS', 'DC', 'USER')
    _VALID_AMPL_UNITS = ('VPP', 'VRMS', 'DBM')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rm    = None
        self._instr = None
        self._lock  = Mutex()

        self.manufacturer = ''
        self.model        = ''
        self.serial       = ''
        self.firmware     = ''

    # =========================================================================
    # Module lifecycle
    # =========================================================================

    def on_activate(self):
        """
        Open the VISA connection and identify the instrument.
        Deliberately does NOT send *RST -- see module docstring.
        """
        self._rm = visa.ResourceManager()

        try:
            self._instr = self._rm.open_resource(self._visa_address)
            self._instr.timeout = self._timeout_ms
            self._instr.write_termination = self._write_term
            self._instr.read_termination  = self._read_term
        except Exception as exc:
            self._instr = None
            raise RuntimeError(
                f'on_activate: could not open VISA resource '
                f'"{self._visa_address}". Check the GPIB address, cabling, '
                f'and that no other program is holding the session open.\n'
                f'Original error: {exc}'
            ) from exc

        try:
            idn = self.query('*IDN?').split(',')
            if len(idn) != 4:
                self.log.warning(
                    f'on_activate: unexpected *IDN? response format: {idn}. '
                    f'Expected "manufacturer,model,serial,firmware". '
                    f'Continuing anyway.'
                )
                idn = (idn + ['', '', '', ''])[:4]
            self.manufacturer, self.model, self.serial, self.firmware = idn
        except Exception as exc:
            self._instr = None
            raise RuntimeError(
                f'on_activate: connected to "{self._visa_address}" but '
                f'*IDN? query failed. Is this really a SCPI-compliant '
                f'instrument at this address?\nOriginal error: {exc}'
            ) from exc

        if '33250' not in self.model:
            self.log.warning(
                f'on_activate: identified model "{self.model}" does not '
                f'look like a 33250A. Continuing anyway -- if this is a '
                f'different, command-set-compatible model, this warning '
                f'is harmless; if not, expect SCPI errors on most calls.'
            )

        self.log.info(
            f'Connected to {self.manufacturer} {self.model} '
            f'(serial {self.serial}, firmware {self.firmware}) '
            f'at {self._visa_address}.'
        )

        # Drain any stale errors left over from a previous session.
        self.get_errors(context='on_activate (draining stale queue)')

    def on_deactivate(self):
        """Close the VISA connection. Does not change instrument output state."""
        if self._instr is not None:
            try:
                self._instr.close()
            except Exception as exc:
                self.log.warning(f'on_deactivate: error closing VISA session: {exc}')
            self._instr = None
        self.log.info(f'Disconnected from {self._visa_address}.')

    # =========================================================================
    # Low-level VISA I/O
    # =========================================================================

    def write(self, command):
        """
        Send a raw SCPI command (no response expected).

        @param str command: SCPI command string, e.g. 'OUTP ON'
        """
        with self._lock:
            try:
                self._instr.write(command)
            except Exception as exc:
                self.log.error(f'write("{command}") failed: {exc}')
                raise

    def query(self, command):
        """
        Send a raw SCPI query and return the (stripped) response string.

        @param str command: SCPI query string, e.g. '*IDN?'
        @return str: instrument response, whitespace/quote-stripped
        """
        with self._lock:
            try:
                response = self._instr.query(command)
            except Exception as exc:
                self.log.error(f'query("{command}") failed: {exc}')
                raise
        return response.strip().strip('"')

    def get_errors(self, context=''):
        """
        Drain and log every pending SCPI error from the instrument's error
        queue.

        @param str context: optional label included in log messages
        @return bool: True if at least one error was found and logged
        """
        found_error = False
        for _ in range(50):   # hard cap -- never spin forever on a stuck queue
            try:
                raw = self.query('SYST:ERR?')
            except Exception as exc:
                self.log.error(
                    f'get_errors: SYST:ERR? query itself failed: {exc}. '
                    f'Aborting error drain.'
                )
                return found_error
            code_str, _, message = raw.partition(',')
            try:
                code = int(code_str)
            except ValueError:
                self.log.error(f'get_errors: could not parse error code from "{raw}".')
                return found_error
            if code == 0:
                break
            found_error = True
            label = f' ({context})' if context else ''
            self.log.error(f'{self.model} SCPI error{label}: {code} {message.strip()}')
        return found_error

    def reset(self):
        """
        Send *RST (factory defaults) followed by *CLS (clear error queue
        and status registers). NOT called automatically by on_activate().
        """
        self.write('*RST')
        self.write('*CLS')
        self.log.info(f'{self.model}: reset to factory defaults.')

    def clear_errors(self):
        """Send *CLS (clear error queue and status registers) without resetting settings."""
        self.write('*CLS')

    def wait_for_completion(self, timeout_s=10.0):
        """
        Block until all pending overlapped commands have completed, using
        *OPC?. Useful after arbitrary-waveform uploads before issuing the
        next command.

        @param float timeout_s: max time to wait
        @return bool: True if *OPC? returned before timeout, False otherwise
        """
        old_timeout = self._instr.timeout
        try:
            self._instr.timeout = int(timeout_s * 1000)
            self.query('*OPC?')
            return True
        except Exception as exc:
            self.log.error(f'wait_for_completion: *OPC? did not complete within {timeout_s}s: {exc}')
            return False
        finally:
            self._instr.timeout = old_timeout

    # =========================================================================
    # Identification
    # =========================================================================

    def get_identity(self):
        """@return dict: {'manufacturer': str, 'model': str, 'serial': str, 'firmware': str}"""
        return {
            'manufacturer': self.manufacturer,
            'model':        self.model,
            'serial':       self.serial,
            'firmware':     self.firmware,
        }

    # =========================================================================
    # Output state / load / polarity
    # =========================================================================

    def get_output_state(self):
        """@return bool: True if output is enabled."""
        return bool(int(self.query('OUTP?')))

    def set_output_state(self, state):
        """@param bool state: True to enable output, False to disable."""
        self.write(f'OUTP {"ON" if state else "OFF"}')
        self.get_errors(context=f'set_output_state({state})')

    def get_output_load(self):
        """
        @return float or str: load impedance in Ohms, or 'INF' for
                              high-impedance (instrument reports this as
                              9.9E+37, translated here to 'INF')
        """
        raw = float(self.query('OUTP:LOAD?'))
        return 'INF' if raw > 1e30 else raw

    def set_output_load(self, load):
        """
        @param float or str load: load impedance in Ohms (1 to 10000), or
                                  'INF'/'INFINITY' for high-impedance
        """
        if isinstance(load, str) and load.upper() in ('INF', 'INFINITY'):
            self.write('OUTP:LOAD INF')
        else:
            self.write(f'OUTP:LOAD {float(load):g}')
        self.get_errors(context=f'set_output_load({load})')

    def get_output_polarity(self):
        """@return str: 'NORM' or 'INV'"""
        return self.query('OUTP:POL?')

    def set_output_polarity(self, polarity):
        """@param str polarity: 'NORM' (normal) or 'INV' (inverted)."""
        polarity = polarity.upper()
        if polarity not in ('NORM', 'NORMAL', 'INV', 'INVERTED'):
            raise ValueError(f'Invalid polarity "{polarity}". Use "NORM" or "INV".')
        self.write(f'OUTP:POL {polarity}')
        self.get_errors(context=f'set_output_polarity({polarity})')

    def get_sync_state(self):
        """@return bool: True if the front-panel Sync output is enabled."""
        return bool(int(self.query('OUTP:SYNC?')))

    def set_sync_state(self, state):
        """@param bool state: True to enable the front-panel Sync output."""
        self.write(f'OUTP:SYNC {"ON" if state else "OFF"}')
        self.get_errors(context=f'set_sync_state({state})')

    # =========================================================================
    # Function shape
    # =========================================================================

    def get_function(self):
        """@return str: current function shape, e.g. 'SIN', 'SQU', 'PULS', 'USER', 'DC'."""
        return self.query('FUNC:SHAP?')

    def set_function(self, shape):
        """
        @param str shape: one of 'SIN', 'SQU', 'RAMP', 'PULS', 'NOIS',
                          'DC', 'USER' (case-insensitive, may be fully
                          spelled out, e.g. 'SQUARE'). 'USER' outputs
                          whatever arbitrary waveform is currently
                          selected -- see select_arbitrary_waveform().
        """
        shape_key = shape.upper()[:4]
        if shape_key not in self._VALID_SHAPES:
            raise ValueError(
                f'Invalid function shape "{shape}". Must start with one of '
                f'{self._VALID_SHAPES} (case-insensitive).'
            )
        self.write(f'FUNC:SHAP {shape}')
        self.get_errors(context=f'set_function({shape})')

    def get_square_duty_cycle(self):
        """@return float: square-wave duty cycle in percent."""
        return float(self.query('FUNC:SQU:DCYC?'))

    def set_square_duty_cycle(self, percent):
        """@param float percent: duty cycle, valid range is frequency-dependent."""
        self.write(f'FUNC:SQU:DCYC {float(percent):g}')
        self.get_errors(context=f'set_square_duty_cycle({percent})')

    def get_ramp_symmetry(self):
        """@return float: ramp symmetry in percent (0=sawtooth down, 100=sawtooth up, 50=triangle)."""
        return float(self.query('FUNC:RAMP:SYMM?'))

    def set_ramp_symmetry(self, percent):
        """@param float percent: 0 to 100."""
        self.write(f'FUNC:RAMP:SYMM {float(percent):g}')
        self.get_errors(context=f'set_ramp_symmetry({percent})')

    def get_pulse_width(self):
        """@return float: pulse width in seconds."""
        return float(self.query('PULS:WIDT?'))

    def set_pulse_width(self, width_s):
        """@param float width_s: pulse width in seconds."""
        self.write(f'PULS:WIDT {float(width_s):.9e}')
        self.get_errors(context=f'set_pulse_width({width_s})')

    def get_pulse_period(self):
        """@return float: pulse period in seconds."""
        return float(self.query('PULS:PER?'))

    def set_pulse_period(self, period_s):
        """@param float period_s: pulse period in seconds."""
        self.write(f'PULS:PER {float(period_s):.9e}')
        self.get_errors(context=f'set_pulse_period({period_s})')

    def set_pulse_transition(self, leading_s, trailing_s=None):
        """
        Set pulse edge transition times.

        @param float leading_s: leading-edge transition time in seconds
        @param float trailing_s: trailing-edge transition time in seconds;
                                 if None, uses the same value as leading_s
        """
        self.write(f'PULS:TRAN:LEAD {float(leading_s):.9e}')
        trailing_s = leading_s if trailing_s is None else trailing_s
        self.write(f'PULS:TRAN:TRAI {float(trailing_s):.9e}')
        self.get_errors(context=f'set_pulse_transition({leading_s}, {trailing_s})')

    # =========================================================================
    # Frequency / amplitude / offset
    #
    # NOTE: the 33250A has no programmable output phase offset in CW mode
    # (no "PHAS" subsystem outside of burst-start-phase) -- see
    # get/set_burst_phase() under Burst mode below for the one place phase
    # is actually settable on this instrument.
    # =========================================================================

    def get_frequency(self):
        """@return float: output frequency in Hz."""
        return float(self.query('FREQ?'))

    def set_frequency(self, freq_hz):
        """@param float freq_hz: output frequency in Hz."""
        self.write(f'FREQ {float(freq_hz):.9e}')
        self.get_errors(context=f'set_frequency({freq_hz})')

    def get_frequency_limits(self):
        """
        Query the instrument's own MIN/MAX frequency for the CURRENTLY
        SELECTED function shape. Shape-dependent -- re-query after
        calling set_function().

        @return (float, float): (min_hz, max_hz)
        """
        f_min = float(self.query('FREQ? MIN'))
        f_max = float(self.query('FREQ? MAX'))
        return f_min, f_max

    def get_amplitude(self):
        """@return float: amplitude, in whatever unit get_amplitude_unit() currently reports."""
        return float(self.query('VOLT?'))

    def set_amplitude(self, amplitude):
        """@param float amplitude: amplitude value, in whatever unit is currently set (see set_amplitude_unit)."""
        self.write(f'VOLT {float(amplitude):.9e}')
        self.get_errors(context=f'set_amplitude({amplitude})')

    def get_amplitude_limits(self):
        """@return (float, float): (min, max) amplitude in the currently-set unit."""
        a_min = float(self.query('VOLT? MIN'))
        a_max = float(self.query('VOLT? MAX'))
        return a_min, a_max

    def get_amplitude_unit(self):
        """@return str: 'VPP', 'VRMS', or 'DBM'."""
        return self.query('VOLT:UNIT?')

    def set_amplitude_unit(self, unit):
        """@param str unit: one of 'VPP', 'VRMS', 'DBM' (case-insensitive)."""
        unit = unit.upper()
        if unit not in self._VALID_AMPL_UNITS:
            raise ValueError(f'Invalid amplitude unit "{unit}". Must be one of {self._VALID_AMPL_UNITS}.')
        self.write(f'VOLT:UNIT {unit}')
        self.get_errors(context=f'set_amplitude_unit({unit})')

    def get_offset(self):
        """@return float: DC offset in Volts."""
        return float(self.query('VOLT:OFFS?'))

    def set_offset(self, offset_v):
        """@param float offset_v: DC offset in Volts."""
        self.write(f'VOLT:OFFS {float(offset_v):.9e}')
        self.get_errors(context=f'set_offset({offset_v})')

    def get_high_low_levels(self):
        """
        Alternative to amplitude/offset: read the absolute high and low
        output levels directly.

        @return (float, float): (high_v, low_v)
        """
        high = float(self.query('VOLT:HIGH?'))
        low  = float(self.query('VOLT:LOW?'))
        return high, low

    def set_high_low_levels(self, high_v, low_v):
        """
        Alternative to amplitude/offset: set the absolute high and low
        output levels directly. The instrument recalculates amplitude/
        offset from these automatically.

        @param float high_v: output HIGH level in Volts
        @param float low_v: output LOW level in Volts
        """
        self.write(f'VOLT:HIGH {float(high_v):.9e}')
        self.write(f'VOLT:LOW {float(low_v):.9e}')
        self.get_errors(context=f'set_high_low_levels({high_v}, {low_v})')

    def apply_settings(self, function=None, frequency=None, amplitude=None, offset=None):
        """
        Convenience method: set several parameters in a single call. Any
        argument left as None is left unchanged.

        @param str function: function shape (see set_function)
        @param float frequency: Hz
        @param float amplitude: in whatever unit is currently set
        @param float offset: Volts
        """
        if function is not None:
            self.set_function(function)
        if frequency is not None:
            self.set_frequency(frequency)
        if amplitude is not None:
            self.set_amplitude(amplitude)
        if offset is not None:
            self.set_offset(offset)

    # =========================================================================
    # Burst mode
    # =========================================================================

    def get_burst_state(self):
        """@return bool: True if burst mode is enabled."""
        return bool(int(self.query('BM:STAT?')))

    def set_burst_state(self, state):
        """@param bool state: True to enable burst mode."""
        self.write(f'BM:STAT {"ON" if state else "OFF"}')
        self.get_errors(context=f'set_burst_state({state})')

    def get_burst_mode(self):
        """@return str: 'TRIG' (triggered) or 'GAT' (gated)."""
        return self.query('BM:MODE?')

    def set_burst_mode(self, mode):
        """@param str mode: 'TRIG' (triggered) or 'GAT' (gated)."""
        mode = mode.upper()[:4]
        if mode not in ('TRIG', 'GAT'):
            raise ValueError(f'Invalid burst mode "{mode}". Use "TRIG" or "GAT".')
        self.write(f'BM:MODE {mode}')
        self.get_errors(context=f'set_burst_mode({mode})')

    def get_burst_ncycles(self):
        """@return int or str: number of cycles per burst, or 'INF' for infinite."""
        raw = self.query('BM:NCYC?')
        try:
            return int(float(raw))
        except ValueError:
            return raw

    def set_burst_ncycles(self, ncycles):
        """@param int or str ncycles: number of cycles, or 'INF' for infinite (gated-friendly)."""
        if isinstance(ncycles, str) and ncycles.upper().startswith('INF'):
            self.write('BM:NCYC INF')
        else:
            self.write(f'BM:NCYC {int(ncycles)}')
        self.get_errors(context=f'set_burst_ncycles({ncycles})')

    def get_burst_internal_period(self):
        """@return float: internal burst period in seconds (used in triggered mode with internal trigger source)."""
        return float(self.query('BM:INT:PER?'))

    def set_burst_internal_period(self, period_s):
        """@param float period_s: internal burst period in seconds. Must exceed (ncycles / frequency)."""
        self.write(f'BM:INT:PER {float(period_s):.9e}')
        self.get_errors(context=f'set_burst_internal_period({period_s})')

    def get_burst_phase(self):
        """@return float: starting phase of each burst, in degrees."""
        return float(self.query('BM:PHAS?'))

    def set_burst_phase(self, phase_deg):
        """@param float phase_deg: starting phase of each burst, in degrees."""
        self.write(f'BM:PHAS {float(phase_deg):.9e}')
        self.get_errors(context=f'set_burst_phase({phase_deg})')

    def set_burst_gate_polarity(self, polarity):
        """
        Set gate-signal polarity for gated burst mode.

        @param str polarity: 'NORM' (output enabled while gate HIGH) or
                             'INV' (output enabled while gate LOW)
        """
        polarity = polarity.upper()[:4]
        if polarity not in ('NORM', 'INV'):
            raise ValueError(f'Invalid gate polarity "{polarity}". Use "NORM" or "INV".')
        self.write(f'BM:GATE:POL {polarity}')
        self.get_errors(context=f'set_burst_gate_polarity({polarity})')

    # =========================================================================
    # Trigger
    # =========================================================================

    def get_trigger_source(self):
        """@return str: 'IMM', 'EXT', or 'BUS'."""
        return self.query('TRIG:SOUR?')

    def set_trigger_source(self, source):
        """@param str source: 'IMM' (immediate/software), 'EXT' (rear-panel Trig In), or 'BUS' (GPIB *TRG / bus trigger)."""
        source = source.upper()[:3]
        if source not in ('IMM', 'EXT', 'BUS'):
            raise ValueError(f'Invalid trigger source "{source}". Use "IMM", "EXT", or "BUS".')
        self.write(f'TRIG:SOUR {source}')
        self.get_errors(context=f'set_trigger_source({source})')

    def get_trigger_slope(self):
        """@return str: 'POS' or 'NEG' (only relevant when trigger source is EXT)."""
        return self.query('TRIG:SLOP?')

    def set_trigger_slope(self, slope):
        """@param str slope: 'POS' or 'NEG'."""
        slope = slope.upper()[:3]
        if slope not in ('POS', 'NEG'):
            raise ValueError(f'Invalid trigger slope "{slope}". Use "POS" or "NEG".')
        self.write(f'TRIG:SLOP {slope}')
        self.get_errors(context=f'set_trigger_slope({slope})')

    def force_trigger(self):
        """Send a single software trigger (equivalent to TRIG[:IMM])."""
        self.write('TRIG')
        self.get_errors(context='force_trigger')

    def force_bus_trigger(self):
        """Send *TRG (GPIB bus trigger) -- triggers if trigger source is set to BUS."""
        self.write('*TRG')
        self.get_errors(context='force_bus_trigger')

    # =========================================================================
    # Arbitrary waveform upload
    #
    # See module docstring, "Arbitrary waveform upload -- 33250A specifics"
    # for why there is no per-upload "name" argument here.
    # =========================================================================

    def write_arbitrary_waveform(self, data, data_format='dac_binary'):
        """
        Upload sample data into the single VOLATILE arbitrary waveform
        buffer. Does NOT automatically select it as the active function
        shape -- call select_arbitrary_waveform() afterward for that.

        @param array-like data: sample values.
                                For data_format='float': floats in [-1.0, 1.0].
                                For data_format='dac_binary': ints in
                                [-32768, 32767] (16-bit signed DAC codes).
        @param str data_format: 'float' (ASCII floats via DATA:ARB, simple/
                                slow) or 'dac_binary' (binary block int16
                                via DATA:DAC, fast, default).
        """
        data_format = data_format.lower()

        if data_format == 'float':
            arr = np.asarray(data, dtype=np.float64)
            if np.any(arr < -1.0) or np.any(arr > 1.0):
                raise ValueError(
                    'write_arbitrary_waveform: data_format="float" requires '
                    'all values in [-1.0, 1.0].'
                )
            values_str = ','.join(f'{v:.6f}' for v in arr)
            with self._lock:
                self._instr.write(f'DATA:ARB VOLATILE,{values_str}')

        elif data_format == 'dac_binary':
            raw = np.asarray(data)
            if np.any(raw < -32768) or np.any(raw > 32767):
                raise ValueError(
                    'write_arbitrary_waveform: data_format="dac_binary" '
                    'requires all values in [-32768, 32767].'
                )
            arr = raw.astype(np.int16)
            with self._lock:
                self._instr.write_binary_values(
                    'DATA:DAC VOLATILE,',
                    arr,
                    datatype='h',           # signed 16-bit int
                    is_big_endian=False,
                )
        else:
            raise ValueError(
                f'Invalid data_format "{data_format}". '
                f'Use "float" or "dac_binary".'
            )

        if not self.wait_for_completion(timeout_s=30.0):
            self.log.error(
                f'write_arbitrary_waveform: upload did not complete within '
                f'30s -- check waveform size and GPIB/VISA timeout settings.'
            )
        self.get_errors(context=f'write_arbitrary_waveform(n={len(data)}, format={data_format})')

    def select_arbitrary_waveform(self):
        """
        Select the currently-uploaded VOLATILE arbitrary waveform as the
        active function shape (equivalent to FUNC:USER VOLATILE followed
        by FUNC:SHAP USER).
        """
        self.write('FUNC:USER VOLATILE')
        self.write('FUNC:SHAP USER')
        self.get_errors(context='select_arbitrary_waveform')

    def copy_arbitrary_to_nonvolatile(self, name):
        """
        Copy the current VOLATILE arbitrary waveform into non-volatile
        memory under the given name, so it survives a power cycle.
        Non-volatile storage is limited (typically 4 slots -- see
        instrument manual for your specific firmware/model variant).

        @param str name: name to store the waveform under, e.g. 'MYWFM'
                         (keep to <= 8 characters, alphanumeric, to be
                         safe across firmware versions)
        """
        self.write(f'DATA:COPY {name},VOLATILE')
        self.get_errors(context=f'copy_arbitrary_to_nonvolatile("{name}")')

    def get_arbitrary_catalog(self):
        """@return list[str]: names of arbitrary waveforms currently stored in non-volatile memory."""
        raw = self.query('DATA:CAT?')
        return [name.strip().strip('"') for name in raw.split(',') if name.strip()]

    def select_nonvolatile_waveform(self, name):
        """
        Select a previously-stored non-volatile arbitrary waveform as the
        active function shape.

        @param str name: waveform name, as passed to copy_arbitrary_to_nonvolatile()
        """
        self.write(f'FUNC:USER {name}')
        self.write('FUNC:SHAP USER')
        self.get_errors(context=f'select_nonvolatile_waveform("{name}")')

    def delete_nonvolatile_waveform(self, name):
        """@param str name: waveform name to delete from non-volatile memory."""
        self.write(f'DATA:DEL {name}')
        self.get_errors(context=f'delete_nonvolatile_waveform("{name}")')

    def get_arbitrary_points(self):
        """@return int: number of points in the current VOLATILE arbitrary waveform."""
        return int(float(self.query('DATA:ATTR:POIN?')))

    # =========================================================================
    # Sweep mode
    # =========================================================================

    def get_sweep_state(self):
        """@return bool: True if frequency sweep mode is enabled."""
        return bool(int(self.query('SWE:STAT?')))

    def set_sweep_state(self, state):
        """@param bool state: True to enable frequency sweep mode."""
        self.write(f'SWE:STAT {"ON" if state else "OFF"}')
        self.get_errors(context=f'set_sweep_state({state})')

    def configure_sweep(self, start_hz, stop_hz, sweep_time_s, spacing='LIN'):
        """
        Configure a frequency sweep (does not enable it -- call
        set_sweep_state(True) afterward).

        @param float start_hz: sweep start frequency
        @param float stop_hz: sweep stop frequency
        @param float sweep_time_s: total sweep duration in seconds
        @param str spacing: 'LIN' (linear) or 'LOG' (logarithmic)
        """
        spacing = spacing.upper()[:3]
        if spacing not in ('LIN', 'LOG'):
            raise ValueError(f'Invalid spacing "{spacing}". Use "LIN" or "LOG".')
        self.write(f'FREQ:STAR {float(start_hz):.9e}')
        self.write(f'FREQ:STOP {float(stop_hz):.9e}')
        self.write(f'SWE:TIME {float(sweep_time_s):.9e}')
        self.write(f'SWE:SPAC {spacing}')
        self.get_errors(
            context=f'configure_sweep({start_hz}, {stop_hz}, {sweep_time_s}, {spacing})'
        )

    def get_sweep_hold_times(self):
        """@return (float, float): (hold_start_s, hold_stop_s) -- dwell time at sweep endpoints."""
        hold_start = float(self.query('SWE:HTIM:STAR?'))
        hold_stop  = float(self.query('SWE:HTIM:STOP?'))
        return hold_start, hold_stop

    def set_sweep_hold_times(self, hold_start_s, hold_stop_s):
        """@param float hold_start_s, hold_stop_s: dwell time in seconds at each sweep endpoint."""
        self.write(f'SWE:HTIM:STAR {float(hold_start_s):.9e}')
        self.write(f'SWE:HTIM:STOP {float(hold_stop_s):.9e}')
        self.get_errors(context=f'set_sweep_hold_times({hold_start_s}, {hold_stop_s})')

    # =========================================================================
    # Basic amplitude modulation (AM) / frequency modulation (FM)
    #
    # These cover the common case (internal modulating source). PM and FSK
    # follow the same SCPI subtree pattern and can be added the same way
    # if ever needed.
    # =========================================================================

    def get_am_state(self):
        """@return bool: True if AM is enabled."""
        return bool(int(self.query('AM:STAT?')))

    def set_am_state(self, state):
        """@param bool state: True to enable AM."""
        self.write(f'AM:STAT {"ON" if state else "OFF"}')
        self.get_errors(context=f'set_am_state({state})')

    def configure_am_internal(self, depth_percent, mod_shape='SIN', mod_freq_hz=100.0):
        """
        Configure AM using the instrument's internal modulation source.
        Does not enable AM -- call set_am_state(True) afterward.

        @param float depth_percent: modulation depth, 0-120%
        @param str mod_shape: modulating waveform shape, e.g. 'SIN', 'SQU', 'RAMP'
        @param float mod_freq_hz: modulating waveform frequency in Hz
        """
        self.write('AM:SOUR INT')
        self.write(f'AM:INT:FUNC {mod_shape}')
        self.write(f'AM:INT:FREQ {float(mod_freq_hz):.9e}')
        self.write(f'AM:DEPT {float(depth_percent):g}')
        self.get_errors(
            context=f'configure_am_internal({depth_percent}, {mod_shape}, {mod_freq_hz})'
        )

    def get_fm_state(self):
        """@return bool: True if FM is enabled."""
        return bool(int(self.query('FM:STAT?')))

    def set_fm_state(self, state):
        """@param bool state: True to enable FM."""
        self.write(f'FM:STAT {"ON" if state else "OFF"}')
        self.get_errors(context=f'set_fm_state({state})')

    def configure_fm_internal(self, deviation_hz, mod_shape='SIN', mod_freq_hz=100.0):
        """
        Configure FM using the instrument's internal modulation source.
        Does not enable FM -- call set_fm_state(True) afterward.

        @param float deviation_hz: peak frequency deviation in Hz
        @param str mod_shape: modulating waveform shape, e.g. 'SIN', 'SQU', 'RAMP'
        @param float mod_freq_hz: modulating waveform frequency in Hz
        """
        self.write('FM:SOUR INT')
        self.write(f'FM:INT:FUNC {mod_shape}')
        self.write(f'FM:INT:FREQ {float(mod_freq_hz):.9e}')
        self.write(f'FM:DEV {float(deviation_hz):.9e}')
        self.get_errors(
            context=f'configure_fm_internal({deviation_hz}, {mod_shape}, {mod_freq_hz})'
        )