# -*- coding: utf-8 -*-

"""
This file contains the qudi hardware module for a Keithley 2400 SourceMeter, used as a voltage
source with a current limit (compliance), controlled via GPIB/VISA.

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

Each instance of this module controls exactly ONE Keithley 2400 SourceMeter. Configure one
instance per GPIB address.

The instrument is operated as a VOLTAGE source (:SOUR:FUNC VOLT) with a CURRENT limit
(:SENS:CURR:PROT, "compliance"). If the instrument is found in current source mode, it is
switched to voltage source mode on the first voltage change -- but only while the output is OFF.

On activation, the instrument's OWN programmed voltage, current limit, and output state are read
back and nothing is changed, so (re-)starting qudi never disturbs a running supply. On
deactivation the output is left as it is (see output_off_on_deactivate) and the instrument is
returned to front panel (local) control.

measure() triggers one measurement (:READ?) and is only done while the output is ON, since the
2400 can not measure with the output OFF.

Example config for copy-paste:

    apd_supply_1:
        module.Class: 'power_supply.keithley_2400.Keithley2400SourceMeter'
        options:
            visa_address: 'GPIB0::24::INSTR'
            visa_timeout: 5000                   # ms
            voltage_limits: [0.0, 6.0]           # V, allowed voltage setpoints
            current_limit_limits: [0.0, 1.05]    # A, allowed current limit (compliance)
            output_terminals: 'REAR'             # 'FRONT' | 'REAR', set when switching the output on
            output_off_on_deactivate: False
"""

import pyvisa

from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.util.helpers import in_range
from qudi.interface.source_meter_interface import SourceMeterInterface


class Keithley2400SourceMeter(SourceMeterInterface):
    """ Hardware class to control a Keithley 2400 SourceMeter as voltage source with current
    limit over GPIB/VISA.
    """

    _visa_address = ConfigOption('visa_address', missing='error')
    _visa_timeout = ConfigOption('visa_timeout', default=5000, missing='nothing')  # ms

    _voltage_limits = ConfigOption('voltage_limits', default=(0.0, 21.0), missing='warn')
    _current_limit_limits = ConfigOption('current_limit_limits', default=(0.0, 1.05),
                                         missing='warn')

    # 'FRONT' or 'REAR': output terminals the supplied device is wired to. Applied whenever the
    # output is switched ON (a reset/power cycle selects the front terminals). None: leave as is.
    _output_terminals = ConfigOption('output_terminals', default=None, missing='nothing')

    # If True, turns the output off when this module is deactivated. Default False, so that
    # closing qudi does not switch off the supplied device.
    _output_off_on_deactivate = ConfigOption('output_off_on_deactivate', default=False,
                                             missing='nothing')

    # The 2400's absolute output limits
    _MAX_VOLTAGE = 210.0
    _MAX_CURRENT = 1.05

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        self._rm = None
        self._inst = None

    # =========================================================================
    # Qudi module lifecycle
    # =========================================================================

    def on_activate(self):
        """ Open the VISA connection and read the instrument state, without changing it. """
        self._rm = pyvisa.ResourceManager()
        try:
            self._inst = self._rm.open_resource(self._visa_address)
        except Exception as err:
            raise RuntimeError(
                f'Could not open VISA connection to Keithley 2400 at "{self._visa_address}". '
                f'Check that the GPIB address is correct and the instrument is powered on.'
            ) from err

        self._inst.timeout = self._visa_timeout
        self._inst.write_termination = '\n'
        self._inst.read_termination = '\n'

        idn = self._query('*IDN?')
        if 'MODEL 24' not in idn.upper():
            self.log.warning(f'Instrument at "{self._visa_address}" does not identify as a '
                             f'Keithley 24xx: "{idn}"')
        self._drain_errors(stale=True)

        terminals = self._query_terminals()
        self.log.info(
            f'Connected to "{idn}" at "{self._visa_address}". Source function '
            f'{self._query(":SOUR:FUNC?")}, voltage {self._query_float(":SOUR:VOLT:LEV?"):.4f} V, '
            f'current limit {self._query_float(":SENS:CURR:PROT?"):.4g} A, {terminals} terminals, '
            f'output {"ON" if self._query_output() else "OFF"}.'
        )
        wanted = self._wanted_terminals()
        if wanted is not None and terminals != wanted:
            self.log.warning(f'"{self._visa_address}" uses the {terminals} output terminals, but '
                             f'output_terminals is {wanted}. They are switched to {wanted} the '
                             f'next time the output is switched on.')

    def on_deactivate(self):
        """ Optionally turn off the output, return to local control, close the connection. """
        if self._inst is not None:
            if self._output_off_on_deactivate:
                try:
                    self.set_output_state(False)
                except Exception:
                    self.log.exception('Error while turning off output during deactivation.')
            try:
                self._inst.control_ren(pyvisa.constants.RENLineOperation.address_gtl)
            except Exception:
                self.log.debug('Could not return instrument to local control.')
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
    # Limits
    # =========================================================================

    @property
    def voltage_limits(self):
        low, high = (float(v) for v in self._voltage_limits)
        return max(low, -self._MAX_VOLTAGE), min(high, self._MAX_VOLTAGE)

    @property
    def current_limit_limits(self):
        low, high = (float(v) for v in self._current_limit_limits)
        return max(low, 0.0), min(high, self._MAX_CURRENT)

    # =========================================================================
    # Voltage
    # =========================================================================

    def get_voltage(self):
        """ Programmed voltage setpoint in volts. """
        with self._thread_lock:
            return self._query_float(':SOUR:VOLT:LEV?')

    def set_voltage(self, value):
        """ Set the voltage setpoint, clipped to voltage_limits.

        @return float: the setpoint read back from the instrument, in volts.
        """
        value = float(value)
        with self._thread_lock:
            in_limits, value = in_range(value, *self.voltage_limits)
            if not in_limits:
                self.log.warning(f'Requested voltage is outside the configured limits '
                                 f'{self.voltage_limits}. Clipping to {value:.4f} V.')
            if not self._ensure_voltage_source():
                return self._query_float(':SOUR:VOLT:LEV?')
            # A level above the fixed source range is rejected by the instrument
            if abs(value) > self._query_float(':SOUR:VOLT:RANG?'):
                self.log.info(f'{value:.4f} V exceeds the source range. Enabling source '
                              f'autorange.')
                self._inst.write(':SOUR:VOLT:RANG:AUTO ON')
            self._inst.write(f':SOUR:VOLT:LEV {value:.6g}')
            self._drain_errors()
            return self._query_float(':SOUR:VOLT:LEV?')

    def _ensure_voltage_source(self):
        """ Switch to voltage source mode if needed, but only while the output is OFF.

        @return bool: True if the instrument is in voltage source mode.
        """
        if self._query(':SOUR:FUNC?').upper().startswith('VOLT'):
            return True
        if self._query_output():
            self.log.error('Instrument is in current source mode with the output ON. Switch the '
                           'output OFF first, then set the voltage again.')
            return False
        self.log.info('Switching instrument from current to voltage source mode.')
        self._inst.write(':SOUR:FUNC VOLT')
        return self._query(':SOUR:FUNC?').upper().startswith('VOLT')

    # =========================================================================
    # Current limit (compliance)
    # =========================================================================

    def get_current_limit(self):
        """ Programmed current limit (compliance) in amps. """
        with self._thread_lock:
            return self._query_float(':SENS:CURR:PROT?')

    def set_current_limit(self, value):
        """ Set the current limit (compliance), clipped to current_limit_limits.

        @return float: the limit read back from the instrument, in amps.
        """
        value = float(value)
        with self._thread_lock:
            in_limits, value = in_range(value, *self.current_limit_limits)
            if not in_limits:
                self.log.warning(f'Requested current limit is outside the configured limits '
                                 f'{self.current_limit_limits}. Clipping to {value:.4g} A.')
            self._inst.write(f':SENS:CURR:PROT {value:.6g}')
            self._drain_errors()
            return self._query_float(':SENS:CURR:PROT?')

    # =========================================================================
    # Output
    # =========================================================================

    def get_output_state(self):
        """ True if the output is ON. """
        with self._thread_lock:
            return self._query_output()

    def set_output_state(self, state):
        """ Switch the output ON or OFF.

        @return bool: the output state read back from the instrument.
        """
        with self._thread_lock:
            if state:
                self._select_terminals()
            self._inst.write(':OUTP ON' if state else ':OUTP OFF')
            self._drain_errors()
            return self._query_output()

    def _select_terminals(self):
        """ Switch to the configured output terminals, if needed. Only done while the output is
        OFF, since changing the terminals switches the output off.
        """
        wanted = self._wanted_terminals()
        if wanted is None:
            return
        current = self._query_terminals()
        if current == wanted:
            return
        if self._query_output():
            self.log.warning(f'Output is ON at the {current} terminals, but output_terminals is '
                             f'{wanted}. Switch the output off and on again to change them.')
            return
        self.log.info(f'Switching "{self._visa_address}" from the {current} to the {wanted} '
                      f'output terminals.')
        self._inst.write(':ROUT:TERM REAR' if wanted == 'REAR' else ':ROUT:TERM FRON')
        self._drain_errors()

    def _wanted_terminals(self):
        if self._output_terminals is None:
            return None
        wanted = str(self._output_terminals).upper()
        if wanted.startswith('FRON'):
            return 'FRONT'
        if wanted == 'REAR':
            return 'REAR'
        self.log.error(f'Invalid output_terminals "{self._output_terminals}". Use FRONT or REAR.')
        return None

    def _query_terminals(self):
        return 'REAR' if self._query(':ROUT:TERM?').upper().startswith('REAR') else 'FRONT'

    # =========================================================================
    # Measurement
    # =========================================================================

    def measure(self):
        """ Trigger one measurement of output voltage and current.

        @return dict or None: {'voltage': V, 'current': A, 'compliance': bool}, or None if the
                              output is OFF.
        """
        with self._thread_lock:
            if not self._query_output():
                return None
            values = [float(v) for v in self._query(':READ?').split(',')]
            # default :FORM:ELEM is VOLT,CURR,RES,TIME,STAT. Status bit 3 = in compliance.
            compliance = len(values) >= 5 and bool(int(values[4]) & 0b1000)
            return {'voltage': values[0], 'current': values[1], 'compliance': compliance}

    # =========================================================================
    # Low-level helpers
    # =========================================================================

    def _query(self, command):
        return self._inst.query(command).strip()

    def _query_float(self, command):
        response = self._query(command)
        try:
            return float(response)
        except ValueError as err:
            raise RuntimeError(f'Expected a numeric response to "{command}", got "{response}"'
                               ) from err

    def _query_output(self):
        return self._query(':OUTP?') in ('1', 'ON')

    def _drain_errors(self, stale=False):
        """ Read and log the instrument error queue.

        @param bool stale: log as warnings about errors left over from before.
        @return bool: whether any error was found.
        """
        found = False
        for _ in range(30):
            err = self._query(':SYST:ERR?')
            if err.startswith('0,') or not err:
                break
            found = True
            if stale:
                self.log.warning(f'Keithley 2400 "{self._visa_address}": error left over from '
                                 f'earlier: {err}')
            else:
                self.log.error(f'Keithley 2400 "{self._visa_address}" reported error: {err}')
        return found
