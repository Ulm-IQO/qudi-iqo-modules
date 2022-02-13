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
from dataclasses import dataclass, asdict
from qudi.core.configoption import ConfigOption
from qudi.interface.simple_laser_interface import SimpleLaserInterface
from qudi.interface.simple_laser_interface import LaserState, ShutterState, ControlMode
import pyvisa as visa
import json

@dataclass
class System_info():
    system_model_name: str = ''
    system_manufacure_date: str = ''
    system_calibration_date:str = ''
    system_serial_number: str = ''
    system_part_number: str = ''
    firmware_version: str = ''
    system_protocol_version: str = ''
    system_wavelength: str = ''
    system_power_rating: str = ''
    device_type: str = ''
    system_power_cycles: str = ''
    system_power_hours: str = ''
    diode_hours: str = ''

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
        self.command = Obis_command()

        if not self.connect_laser():
            raise RuntimeError('Laser is not connected.')

        self._model_name = self.command.get_model_name()
        self._current_setpoint = self.get_current()

    def on_deactivate(self):
        """ Deactivate module.
        """

        self.disconnect_laser()

    def connect_laser(self):
        """ Connect to Instrument.

        @return bool: connection success
        """
        self.command.open_visa(self._com_port)
        response = self.command.get_idn()[0]

        if response.startswith('ERR-100'):
            return False
        else:
            return True

    def disconnect_laser(self):
        """ Close the connection to the instrument.
        """
        self.set_laser_state(LaserState.OFF)
        self.command.close()

    def allowed_control_modes(self):
        """ Control modes for this laser
        """
        return {ControlMode.POWER}

    def get_control_mode(self):
        """ Get current laser control mode.

        @return ControlMode: current laser control mode
        """
        return ControlMode.POWER

    def set_control_mode(self, mode):
        """ Set laser control mode.

        @param ControlMode mode: desired control mode
        @return ControlMode: actual control mode
        """
        if mode == ControlMode.POWER:
            pass

        else:
            self.log.warning(self._model_name + ' does not have control modes, '
                             'cannot set to mode {}'.format(mode))

    def get_power(self):
        """ Get laser power.

        @return float: laser power in watts
        """
        return float(self.command.get_power_W())

    def get_power_setpoint(self):
        """ Get the laser power setpoint.

        @return float: laser power setpoint in watts
        """
        return float(self.command.get_power_setpoint_W())

    def get_power_range(self):
        """ Get laser power range.

        @return float[2]: laser power range
        """
        minpower = float(self.command.get_min_power())
        maxpower = float(self.command.get_max_power())
        return minpower, maxpower

    def set_power(self, power):
        """ Set laser power

        @param float power: desired laser power in watts
        """
        self.command.set_power_W(power)

    def get_current_unit(self):
        """ Get unit for laser current.

        @return str: unit for laser current
        """
        return 'A'

    def get_current_range(self):
        """ Get range for laser current.

        @return float[2]: range for laser current
        """
        low = self.command.get_min_current_A()
        high = self.command.get_max_current_A()
        return float(low), float(high)

    def get_current(self):
        """ Cet current laser current

        @return float: current laser current in amps
        """
        return self.command.get_current_A()

    def get_current_setpoint(self):
        """ Current laser current setpoint.

        @return float: laser current setpoint
        """
        return self._current_setpoint

    def set_current(self, current):
        """ Set laser current setpoint.

        @param float current_percent: laser current setpoint
        """
        self.command.set_current(current)
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
            'Diode': self.command.get_diode_temperature(),
            'Internal': self.command.get_internal_temperature(),
            'Base Plate': self.command.get_baseplate_temperature()
        }

    def get_laser_state(self):
        """ Get laser operation state

        @return LaserState: laser state
        """
        state = self.command.get_laser_state()
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
                self.command.set_laser_state_on()
            elif status == LaserState.OFF:
                self.command.set_laser_state_off()

    def get_extra_info(self):
        """ Extra information from laser.

        @return str: multiple lines of text with information about laser
        """
        return json.dumps(asdict(self.command.get_sys_info()))

class Visa:

    def open(self, interface):

        self.rm = visa.ResourceManager()
        self.inst = self.rm.open_resource(interface,
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

    def get_idn(self):
        return self.query('*IDN?')


class Obis_command(Visa):

    sys_info = System_info()
    visa = Visa()

    def open_visa(self, com_port):
        self.open(com_port)
        self.turn_off_handshaking()

    def close_visa(self):
        self.close()

    def turn_off_handshaking(self):
        self.write('SYST:COMM:HAND OFF')

    def get_operating_mode(self):
        return self.query('SOUR:AM:SOUR?')

    def set_operating_CW_mode(self, mode):
        self.write('SOUR:AM:INT {}'.format(mode))

    def set_power_W(self, power):
        self.write('SOUR:POW:LEV:IMM:AMPL {}'.format(power))

    def set_current_A(self, current):
        self.write('SOUR:POW:CURR {}'.format(current))

    def get_model_name(self):
        return self.query('SYST:INF:MOD?')

    def get_sys_info(self):
        self.sys_info.system_model_name = self.query('SYST:INF:MOD?')
        self.sys_info.system_manufacure_date = self.query('SYST:INF:MDAT?')
        self.sys_info.system_calibration_date = self.query('SYST:INF:CDAT?')
        self.sys_info.system_serial_number = self.query('SYST:INF:SNUM?')
        self.sys_info.system_part_number = self.query('SYST:INF:PNUM?')
        self.sys_info.firmware_version = self.query('SYST:INF:FVER?')
        self.sys_info.system_protocol_version = self.query('SYST:INF:FVER?')
        self.sys_info.system_wavelength = self.query('SYST:INF:WAV?')
        self.sys_info.system_power_rating = self.query('SYST:INF:POW?')
        self.sys_info.device_type = self.query('SYST:INF:TYP?')
        self.sys_info.system_power_cycles = self.query('SYST:CYCL?')
        self.sys_info.system_power_hours = self.query('SYST:HOUR?')
        self.sys_info.diode_hours = self.query('SYST:DIOD:HOUR?')

        return self.sys_info

    def get_power_W(self):
        return float(self.query('SOUR:POW:LEV?'))

    def get_power_setpoint_W(self):
        return float(self.query('SOUR:POW:LEV:IMM:AMPL?'))

    def get_min_power(self):
        return float(self.query('SOUR:POW:LIM:LOW?'))

    def get_max_power(self):
        return float(self.query('SOUR:POW:LIM:HIGH?'))

    def get_min_current_A(self):
        return float(self.query('SOUR:CURR:LIM:LOW?'))

    def get_max_current_A(self):
        return float(self.query('SOUR:CURR:LIM:HIGH?'))

    def get_current_A(self):
        return float(self.query('SOUR:POW:CURR?'))

    def get_diode_temperature(self):
        """ Get laser diode temperature

        @return float: laser diode temperature
        """

        return float(self.query('SOUR:TEMP:DIOD?').replace('C', ''))

    def get_internal_temperature(self):
        """ Get internal laser temperature

        @return float: internal laser temperature
        """

        return str(self.query('SOUR:TEMP:INT?').replace('C', ''))

    def get_baseplate_temperature(self):
        """ Get laser base plate temperature

        @return float: laser base plate temperature
        """

        return str(self.query('SOUR:TEMP:BAS?').replace('C', ''))

    def get_laser_state(self):
        return self.query('SOUR:AM:STAT?')

    def set_laser_state_on(self):
        self.write('SOUR:AM:STAT ON')

    def set_laser_state_off(self):
        self.write('SOUR:AM:STAT OFF')

    def get_interlock_status(self):
        """ Get the status of the system interlock

        @returns bool interlock: status of the interlock
        """

        response = self.query('SYST:LOCK?')

        if response.lower() == 'ok':
            return True
        elif response.lower() == 'off':
            return False
        else:
            return False
