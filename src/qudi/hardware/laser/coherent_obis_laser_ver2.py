# -*- coding: utf-8 -*-
"""
This module controls the Coherent OBIS laser.

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


import time
from dataclasses import dataclass
from qudi.core.configoption import ConfigOption
from qudi.interface.simple_laser_interface import SimpleLaserInterface
from qudi.interface.simple_laser_interface import LaserState, ShutterState, ControlMode
import pyvisa as visa
import json

@dataclass
class System_info():
    system_model_name:
    system_manufacure_date:
    system_calibration_date:
    system_serial_number:
    system_part_number:
    firmware_version:
    system_protocol_version:
    system_wavelength:
    system_power_rating:
    device_type:
    system_power_cycles:
    system_power_hours:
    diode_hours:

class OBISLaser(SimpleLaserInterface):

    """ Implements the Coherent OBIS laser.

    Example config for copy-paste:

    obis_laser:
        module.Class: 'laser.coherent_obis_laser.OBISLaser'
        com_port: 'COM3'

    """

    _model_name = 'UNKNOWN'

    _com_port = ConfigOption('com_port', missing='error')


    def on_activate(self):
        """ Activate module.
        """
        self.obis = Obis_command()


        if not self.connect_laser():
            raise RuntimeError('Laser does not seem to be connected.')

        self._model_name = self.obis.get_model_name()
        self._current_setpoint = self.get_current()

    def on_deactivate(self):
        """ Deactivate module.
        """

        self.disconnect_laser()

    def connect_laser(self):
        """ Connect to Instrument.

        @return bool: connection success
        """
        response = self.obis.get_idn()[0]

        if response.startswith('ERR-100'):
            return False
        else:
            return True

    def disconnect_laser(self):
        """ Close the connection to the instrument.
        """
        self.set_laser_state(LaserState.OFF)
        self.obis.close()

    def allowed_control_modes(self):
        """ Control modes for this laser
        """
        return frozenset({ControlMode.UNKNOWN})

    def get_control_mode(self):
        """ Get current laser control mode.

        @return ControlMode: current laser control mode
        """
        return ControlMode.UNKNOWN

    def set_control_mode(self, mode):
        """ Set laser control mode.

        @param ControlMode mode: desired control mode
        @return ControlMode: actual control mode
        """
        if mode != ControlMode.UNKNOWN:
            self.log.warning(self._model_name + ' does not have control modes, '
                             'cannot set to mode {}'.format(mode))

    def get_power(self):
        """ Get laser power.

        @return float: laser power in watts
        """
        return float(self.obis.get_power_W())

    def get_power_setpoint(self):
        """ Get the laser power setpoint.

        @return float: laser power setpoint in watts
        """
        return float(self.obis.get_power_setpoint_W)

    def get_power_range(self):
        """ Get laser power range.

        @return float[2]: laser power range
        """
        minpower = float(self.obis.get_min_power())
        maxpower = float(self.obis.get_max_power())
        return minpower, maxpower

    def set_power(self, power):
        """ Set laser power

        @param float power: desired laser power in watts
        """
        self.obis.set_power_W(power)

    def get_current_unit(self):
        """ Get unit for laser current.

        @return str: unit for laser current
        """
        return 'A'

    def get_current_range(self):
        """ Get range for laser current.

        @return float[2]: range for laser current
        """
        low = self.obis.get_min_current_A()
        high = self.obis.get_max_current_A()
        return float(low), float(high)

    def get_current(self):
        """ Cet current laser current

        @return float: current laser current in amps
        """
        return self.obis.get_current_A()

    def get_current_setpoint(self):
        """ Current laser current setpoint.

        @return float: laser current setpoint
        """
        return self._current_setpoint

    def set_current(self, current):
        """ Set laser current setpoint.

        @param float current_percent: laser current setpoint
        """
        self.obis.set_current(current)
        self._current_setpoint = current

    def get_shutter_state(self):
        """ Get laser shutter state.

        @return ShutterState: laser shutter state
        """
        return ShutterState.NO_SHUTTER

    def set_shutter_state(self, state):
        """ Set the desired laser shutter state.

        @param ShutterState state: desired laser shutter state
        """
        if state not in (ShutterState.NO_SHUTTER, ShutterState.UNKNOWN):
            self.log.warning(self._model_name + ' does not have a shutter')

    def get_temperatures(self):
        """ Get all available temperatures.

        @return dict: dict of temperature names and value
        """
        return {
            'Diode': self.obis.get_diode_temperature(),
            'Internal': self.obis.get_internal_temperature(),
            'Base Plate': self.obis.get_baseplate_temperature()
        }

    def get_laser_state(self):
        """ Get laser operation state

        @return LaserState: laser state
        """
        state = self.obis.get_laser_state()
        if 'ON' in state:
            return LaserState.ON
        elif 'OFF' in state:
            return LaserState.OFF
        return LaserState.UNKNOWN

    def set_laser_state(self, status):
        """ Set desited laser state.

        @param LaserState status: desired laser state
        @return LaserState: actual laser state
        """
        if self.get_laser_state() != status:
            if status == LaserState.ON:
                self.obis.set_laser_state_on()
            elif status == LaserState.OFF:
                self.obis.set_laser_state_off()

    def get_extra_info(self):
        """ Extra information from laser.

        @return str: multiple lines of text with information about laser
        """
        return json.dumps(self.obis.get_sys_info().asdict)


class Visa:

    def open(self):

        self.rm = visa.ResoucrManager()
        self.inst = self.rm.open_resouce(interface,
                                         baud_rate = 115200,
                                         write_termination = '\r\n',
                                         read_termination = '\r\n',
                                         send_end = True)
        self.inst.timeout = 1000

    def close(self):
        self.inst.close()
        self.rm.close()

    def write(self, message):
        """ Send a message to to laser

        @param string message: message to be delivered to the laser
        """
        self.inst.write(message)

    def query(self, message):
        """ Send a receive messages with the laser

        @param string message: message to be delivered to the laser

        @returns string response: message received from the laser
        """
        values = self.inst.query(message)
        return values

class Obis_command:

    sys_info = System_info()
    visa = Visa()

    def open_visa(self):
        self.visa.open()

    def close_visa(self):
        self.visa.close()

    def set_power_W(self, power):
        self.visa.write('SOUR:POW:LEV:IMM:AMPL {}'.format(power))

    def set_current(self, current):
        self.visa.write('SOUR:POW:CURR {}'.format(current))


    def get_model_name(self):
        return self.visa.query('SYST:INF:MOD?')

    def get_system_info(self):
        self.sys_info.system_model_name = self.visa.query('SYST:INF:MOD?')
        self.sys_info.system_manufacure_date = self.visa.query('SYST:INF:MDAT?')
        self.sys_info.system_calibration_date = self.visa.query('SYST:INF:CDAT?')
        self.sys_info.system_serial_number = self.visa.query('SYST:INF:SNUM?')
        self.sys_info.system_part_number = self.visa.query('SYST:INF:PNUM?')
        self.sys_info.firmware_version = self.visa.query('SYST:INF:FVER?')
        self.sys_info.system_protocol_version = self.visa.query('SYST:INF:FVER?')
        self.sys_info.system_wavelength = self.visa.query('SYST:INF:WAV?')
        self.sys_info.system_power_rating = self.visa.query('SYST:INF:POW?')
        self.sys_info.device_type = self.visa.query('SYST:INF:TYP?')
        self.sys_info.system_power_cycles = self.visa.query('SYST:CYCL?')
        self.sys_info.system_power_hours = self.visa.query('SYST:HOUR?')
        self.sys_info.diode_hours = self.visa.query('SYST:DIOD:HOUR?')

        return self.sys_info

    def get_idn(self):
        return self.visa.query('?IDN')

    def get_power_W(self):
        return self.visa.query('SOUR:POW:LEV?')

    def get_power_setpoint_W(self):
        return self.visa.query('SOUR:POW:LEV:IMM:AMPL?')

    def get_min_power(self):
        return self.visa.query('SOUR:POW:LIM:LOW?')

    def get_max_power(self):
        return self.visa.query('SOUR:POW:LIM:HIGH?')

    def get_min_current_A(self):
        return self.visa.query('SOUR:CURR:LIM:LOW?')

    def get_max_current_A(self):
        return self.visa.query('SOUR:CURR:LIM:HIGH?')

    def get_current_A(self):
        return self.visa.query('SOUR:POW:CURR?')

    def get_diode_temperature(self):
        """ Get laser diode temperature

        @return float: laser diode temperature
        """

        return float(self.visa.query('SOUR:TEMP:DIOD?'))

    def get_internal_temperature(self):
        """ Get internal laser temperature

        @return float: internal laser temperature
        """

        return float(self.visa.query('SOUR:TEMP:INT?'))

    def get_baseplate_temperature(self):
        """ Get laser base plate temperature

        @return float: laser base plate temperature
        """

        return float(self.visa.query('SOUR:TEMP:BAS?'))

    def get_laser_state(self):
        return self.visa.query('SOUR:AM:STAT?')

    def set_laser_state_on(self):
        self.visa.write('SOUR:AM:STAT ON')

    def set_laser_state_off(self):
        self.visa.write('SOUR:AM:STAT OFF')

    def _get_interlock_status(self):
        """ Get the status of the system interlock

        @returns bool interlock: status of the interlock
        """

        response = self.visa.query('SYST:LOCK?')

        if response.lower() == 'ok':
            return True
        elif response.lower() == 'off':
            return False
        else:
            return False
