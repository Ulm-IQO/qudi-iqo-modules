# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module for a Keithley 2200 series DC power supply,
controlled via GPIB/VISA.

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

This module wraps a single Keithley 2200 series bench power supply (tested against a
2200-20-5, 20 V / 5 A model) over GPIB using SCPI commands via PyVISA.

Each instance of this hardware module controls exactly ONE physical power supply. If you
have multiple units (e.g. driving three magnet coil axes, as in a vector magnet setup),
configure three separate instances of this module, one per GPIB address -- exactly as the
original control software for this hardware did.

The power supply's output relay (ON/OFF) is exposed through Qudi's SwitchInterface as a
single named switch, 'output'. Voltage and current are controlled through plain methods
(not a generic Qudi process-control interface) supporting both absolute and relative
(step) changes:

    set_voltage(value)            -- absolute voltage setpoint (V)
    set_voltage_relative(delta)   -- step the voltage setpoint by +/- delta (V)
    get_voltage()                 -- currently programmed voltage setpoint (V)
    get_measured_voltage()        -- actual measured output voltage (V)

    set_current(value)            -- absolute current setpoint (A)
    set_current_relative(delta)   -- step the current setpoint by +/- delta (A)
    get_current()                 -- currently programmed current setpoint (A)
    get_measured_current()        -- actual measured output current (A)

    output_on() / output_off() / get_output_state()   -- convenience wrappers around the
                                                          SwitchInterface 'output' switch

On activation, the instrument's OWN programmed voltage/current setpoints are read back and
adopted as the current state (rather than pushing a value remembered by Qudi from a
previous session) -- this avoids silently overwriting a live setpoint (e.g. an energized
magnet) with a stale value if Qudi is restarted while the supply is still running.

The output relay state is intentionally NOT changed automatically on deactivation, for the
same reason (see output_off_on_deactivate config option to change this).

------------------------------------------------------------------------

Example config for copy-paste (single power supply):

    magnet_psu_x:
        module.Class: 'keithley.keithley_2200.Keithley2200PowerSupply'
        options:
            visa_address: 'GPIB2::19::INSTR'
            visa_timeout: 5000            # ms
            voltage_limits: [0.0, 20.0]   # V, matches 2200-20-5 rating
            current_limits: [0.0, 5.0]    # A, matches 2200-20-5 rating
            command_delay: 0.05           # s, settle time after each SCPI write
            reset_on_activate: False
            output_off_on_deactivate: False

Example config for a three-axis vector magnet (X/Y/Z coils, matching legacy setup):

    magnet_psu_x:
        module.Class: 'keithley.keithley_2200.Keithley2200PowerSupply'
        options:
            visa_address: 'GPIB2::17::INSTR'
            voltage_limits: [0.0, 20.0]
            current_limits: [0.0, 5.0]

    magnet_psu_y:
        module.Class: 'keithley.keithley_2200.Keithley2200PowerSupply'
        options:
            visa_address: 'GPIB2::18::INSTR'
            voltage_limits: [0.0, 20.0]
            current_limits: [0.0, 5.0]

    magnet_psu_z:
        module.Class: 'keithley.keithley_2200.Keithley2200PowerSupply'
        options:
            visa_address: 'GPIB2::19::INSTR'
            voltage_limits: [0.0, 20.0]
            current_limits: [0.0, 5.0]
"""

import time
import pyvisa

from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.util.helpers import in_range
from qudi.interface.switch_interface import SwitchInterface


class Keithley2200PowerSupply(SwitchInterface):
    """ Hardware class to control a Keithley 2200 series DC power supply over GPIB/VISA.

    Voltage and current are controlled via plain get/set methods (absolute and relative).
    The output relay is exposed as a single named switch ('output') through SwitchInterface.
    """

    # ── Config options ────────────────────────────────────────────────────────
    _visa_address = ConfigOption('visa_address', missing='error')
    _visa_timeout = ConfigOption('visa_timeout', default=5000, missing='nothing')  # ms

    _voltage_limits = ConfigOption('voltage_limits', default=(0.0, 20.0), missing='nothing')
    _current_limits = ConfigOption('current_limits', default=(0.0, 5.0), missing='nothing')

    # Small settle delay after each SCPI write, mirroring the pause(.2) used in the
    # original control software. Kept short by default since modern GPIB/VISA writes
    # are synchronous and this is mostly a safety margin for the instrument's own
    # internal settling, not a communication necessity.
    _command_delay = ConfigOption('command_delay', default=0.05, missing='nothing')  # s

    # If True, sends *RST on activation, resetting the instrument to its power-on
    # defaults (0 V, 0 A, output OFF). Default False so activating this module does
    # not disturb a supply that is already running (e.g. an energized magnet coil).
    _reset_on_activate = ConfigOption('reset_on_activate', default=False, missing='nothing')

    # If True, turns the output off when this module is deactivated. Default False
    # for the same reason as above -- deactivating the Qudi module (e.g. during a
    # restart) should not silently de-energize a live magnet.
    _output_off_on_deactivate = ConfigOption(
        'output_off_on_deactivate', default=False, missing='nothing'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        self._rm = None
        self._inst = None

        # Cached setpoints, refreshed from the instrument on activation and kept in
        # sync locally after every set_voltage()/set_current() call to avoid a GPIB
        # round-trip on every relative-step calculation.
        self._voltage_setpoint = 0.0
        self._current_setpoint = 0.0

    # =========================================================================
    # Qudi module lifecycle
    # =========================================================================

    def on_activate(self):
        """ Open the GPIB/VISA connection, put the instrument in remote mode, and
        synchronize local setpoint cache with whatever the instrument is currently
        programmed to.
        """
        self._rm = pyvisa.ResourceManager()

        try:
            self._inst = self._rm.open_resource(self._visa_address)
        except Exception as err:
            raise RuntimeError(
                f'Could not open VISA connection to Keithley PSU at '
                f'"{self._visa_address}". Check that the GPIB address is correct and '
                f'the instrument is powered on and connected.'
            ) from err

        self._inst.timeout = self._visa_timeout
        self._inst.write_termination = '\n'
        self._inst.read_termination = '\n'

        # Put the instrument under remote (computer) control, mirroring the
        # legacy control software's initial handshake.
        self._write('SYST:REM')
        time.sleep(0.2)

        idn = self._query('*IDN?')
        self.log.info(f'Connected to power supply at "{self._visa_address}": {idn}')

        if self._reset_on_activate:
            self.log.warning(
                'reset_on_activate=True: sending *RST. Output will be forced OFF '
                'and setpoints reset to 0.'
            )
            self._write('*RST')
            time.sleep(0.5)

        # Adopt the instrument's OWN current setpoints as truth, rather than
        # pushing a value Qudi might remember from a previous session. This
        # avoids silently changing a live setpoint (e.g. an energized magnet)
        # on module (re-)activation.
        self._voltage_setpoint = self._query_float('VOLT?')
        self._current_setpoint = self._query_float('CURR?')

        self.log.info(
            f'Power supply "{self._visa_address}" activated. '
            f'Current setpoints: {self._voltage_setpoint:.4f} V, '
            f'{self._current_setpoint:.4f} A. Output is '
            f'{"ON" if self._get_output_state() else "OFF"}.'
        )

    def on_deactivate(self):
        """ Optionally turn off the output, then close the VISA connection. """
        if self._output_off_on_deactivate:
            try:
                self._set_output_state(False)
            except Exception:
                self.log.exception('Error while turning off output during deactivation.')

        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                self.log.exception('Error while closing VISA connection.')
            self._inst = None

        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                self.log.exception('Error while closing VISA resource manager.')
            self._rm = None

    # =========================================================================
    # Low-level VISA/SCPI helpers
    # =========================================================================

    def _write(self, command):
        """ Send a SCPI command with no response expected, then wait the
        configured settle delay.

        @param str command: SCPI command string, e.g. 'VOLT 12.5'
        """
        self._inst.write(command)
        if self._command_delay > 0:
            time.sleep(self._command_delay)

    def _query(self, command):
        """ Send a SCPI query and return the raw response string, stripped of
        surrounding whitespace.

        @param str command: SCPI query string, e.g. 'VOLT?'
        @return str: Instrument response.
        """
        response = self._inst.query(command).strip()
        return response

    def _query_float(self, command):
        """ Send a SCPI query and parse the response as a float.

        @param str command: SCPI query string, e.g. 'VOLT?'
        @return float: Parsed response value.
        """
        response = self._query(command)
        try:
            return float(response)
        except ValueError as err:
            raise RuntimeError(
                f'Expected a numeric response to "{command}", got "{response}"'
            ) from err

    def _check_errors(self):
        """ Query and log the instrument's error queue, if it supports SCPI-style
        error reporting. Failure to query is logged at debug level rather than
        raised, since not all instrument firmware revisions support SYST:ERR?.

        @return str or None: Error string if an error was present, else None.
        """
        try:
            err = self._query('SYST:ERR?')
        except Exception as exc:
            self.log.debug(f'Could not query error queue: {exc}')
            return None

        if err and not err.startswith('0,'):
            self.log.error(f'Power supply reported error: {err}')
            return err
        return None

    # =========================================================================
    # Voltage control
    # =========================================================================

    def get_voltage(self):
        """ Return the currently programmed voltage setpoint (not the measured
        output voltage -- see get_measured_voltage() for that).

        @return float: Voltage setpoint in volts.
        """
        with self._thread_lock:
            return self._voltage_setpoint

    def set_voltage(self, value):
        """ Set an absolute voltage setpoint.

        @param float value: Desired voltage setpoint in volts.
        @return float: The voltage setpoint that was actually applied (after
                       clipping to configured limits, if necessary).
        """
        value = float(value)
        with self._thread_lock:
            in_limits, clipped_value = in_range(value, *self._voltage_limits)
            if not in_limits:
                self.log.warning(
                    f'Requested voltage {value:.4f} V is outside configured limits '
                    f'{self._voltage_limits}. Clipping to {clipped_value:.4f} V.'
                )
            self._write(f'VOLT {clipped_value}')
            self._voltage_setpoint = clipped_value
            self._check_errors()
            return clipped_value

    def set_voltage_relative(self, delta):
        """ Step the voltage setpoint by a relative amount.

        @param float delta: Amount to add to the current voltage setpoint (volts).
                            Use a negative value to decrease.
        @return float: The new voltage setpoint that was actually applied (after
                       clipping to configured limits, if necessary).
        """
        with self._thread_lock:
            new_value = self._voltage_setpoint + float(delta)
        return self.set_voltage(new_value)

    def get_measured_voltage(self):
        """ Return the actual measured output voltage (as opposed to the
        programmed setpoint).

        @return float: Measured output voltage in volts.
        """
        with self._thread_lock:
            return self._query_float('MEAS:VOLT?')

    # =========================================================================
    # Current control
    # =========================================================================

    def get_current(self):
        """ Return the currently programmed current setpoint (not the measured
        output current -- see get_measured_current() for that).

        @return float: Current setpoint in amps.
        """
        with self._thread_lock:
            return self._current_setpoint

    def set_current(self, value):
        """ Set an absolute current setpoint.

        @param float value: Desired current setpoint in amps.
        @return float: The current setpoint that was actually applied (after
                       clipping to configured limits, if necessary).
        """
        value = float(value)
        with self._thread_lock:
            in_limits, clipped_value = in_range(value, *self._current_limits)
            if not in_limits:
                self.log.warning(
                    f'Requested current {value:.4f} A is outside configured limits '
                    f'{self._current_limits}. Clipping to {clipped_value:.4f} A.'
                )
            self._write(f'CURR {clipped_value}')
            self._current_setpoint = clipped_value
            self._check_errors()
            return clipped_value

    def set_current_relative(self, delta):
        """ Step the current setpoint by a relative amount.

        @param float delta: Amount to add to the current current-setpoint (amps).
                            Use a negative value to decrease.
        @return float: The new current setpoint that was actually applied (after
                       clipping to configured limits, if necessary).
        """
        with self._thread_lock:
            new_value = self._current_setpoint + float(delta)
        return self.set_current(new_value)

    def get_measured_current(self):
        """ Return the actual measured output current (as opposed to the
        programmed setpoint).

        @return float: Measured output current in amps.
        """
        with self._thread_lock:
            return self._query_float('MEAS:CURR?')

    # =========================================================================
    # Output on/off -- convenience wrappers around the SwitchInterface below
    # =========================================================================

    def output_on(self):
        """ Turn the power supply output ON. """
        self.set_state('output', True)

    def output_off(self):
        """ Turn the power supply output OFF. """
        self.set_state('output', False)

    def get_output_state(self):
        """ Return whether the power supply output is currently ON.

        @return bool: True if output is ON, False if OFF.
        """
        return self.get_state('output')

    def _set_output_state(self, state):
        """ Internal: write the OUTP ON/OFF SCPI command.

        @param bool state: True to turn output ON, False to turn it OFF.
        """
        self._write('OUTP ON' if state else 'OUTP OFF')
        self._check_errors()

    def _get_output_state(self):
        """ Internal: query the instrument's actual output relay state.

        @return bool: True if output is ON, False if OFF.
        """
        response = self._query('OUTP?')
        # Keithley 2200 series returns '0' or '1' for OUTP?
        return response.strip() in ('1', 'ON')

    # =========================================================================
    # SwitchInterface implementation
    # =========================================================================
    #
    # The power supply has exactly one physically meaningful switch: its output
    # relay. Modeled here as a single named switch, 'output', with states
    # False (OFF) and True (ON).

    def getNumberOfSwitches(self):
        """ Return the total number of available switches.

        @return int: 1 (the single output relay).
        """
        return 1

    def getCalibration(self, switch_num, switch_state):
        """ Not applicable: the output relay has no associated calibration
        voltage (unlike, e.g., a TTL switch line).

        @return float: Always 0.0, with a warning logged.
        """
        self.log.warning(
            'getCalibration() is not meaningful for a power supply output '
            'relay. Returning 0.0.'
        )
        return 0.0

    def setCalibration(self, switch_num, switch_state, value):
        """ Not applicable: see getCalibration().

        @return bool: Always True (command ignored with a warning).
        """
        self.log.warning(
            'setCalibration() is not meaningful for a power supply output '
            'relay. Command ignored.'
        )
        return True

    def getSwitchTime(self, switch_num):
        """ Return the estimated time for the output relay to change state.

        @param int switch_num: Unused (only one switch exists).
        @return float: Conservative estimate in seconds. Not specified in the
                       instrument manual; adjust if you observe otherwise.
        """
        return 0.05

    @property
    def name(self):
        """ Hardware module name string.

        @return str: The qudi module name for this hardware instance.
        """
        return self.module_name

    @property
    def available_states(self):
        """ Describe the available states for the output switch.

        @return dict: {'output': (False, True)}.
        """
        return {'output': (False, True)}

    def get_state(self, switch):
        """ Return the current ON/OFF state of the named switch.

        @param str switch: Must be 'output'.
        @return bool: True if ON, False if OFF.
        """
        if switch != 'output':
            self.log.error(f'Unknown switch name: "{switch}". Only "output" exists.')
            return False
        with self._thread_lock:
            return self._get_output_state()

    def set_state(self, switch, state):
        """ Set the ON/OFF state of the named switch.

        @param str switch: Must be 'output'.
        @param bool state: True to switch ON, False to switch OFF.
        """
        if switch != 'output':
            self.log.error(f'Unknown switch name: "{switch}". Only "output" exists.')
            return
        with self._thread_lock:
            self._set_output_state(bool(state))