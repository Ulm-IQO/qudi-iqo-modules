# -*- coding: utf-8 -*-
"""
This module controls LaserQuantum lasers.

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

try:
    import pyvisa as visa
except ImportError:
    import visa
from enum import Enum

from qudi.core.configoption import ConfigOption
from qudi.interface.simple_laser_interface import SimpleLaserInterface
from qudi.interface.simple_laser_interface import ControlMode, ShutterState, LaserState
from dataclasses import dataclass, asdict
import re

class PSUTypes(Enum):
    """ LaserQuantum power supply types.
    """
    MPC6000 = 0
    MPC3000 = 1
    SMD12 = 2
    SMD6000 = 3

@dataclass
class System_info():
    firmware_ver: str = ''
    psu_time: str = ''
    laser_time: str = ''
    laser_over_1A_time: str = ''


class LaserQuantumLaser(SimpleLaserInterface):
    """
    Hardware file for laser quantum laser.
    Example config for copy-paste:

    laserquantum_laser:
        module.Class: 'laser.laserquantum_laser.LaserQuantumLaser'
        options:
            interface: 'ASRL1::INSTR'
            maxpower: 0.250 # in Watt
            psu: 'SMD6000'
    """

    serial_interface = ConfigOption(name='interface', default='ASRL1::INSTR', missing='warn')
    maxpower = ConfigOption(name='maxpower', default=0.250, missing='warn')
    psu_type = ConfigOption(name='psu', default='SMD6000', missing='warn')

    def on_activate(self):
        """ Activate module.
        """
        self.psu = PSUTypes[self.psu_type]
        if self.psu in (PSUTypes.SMD6000, PSUTypes.SMD12):
            self.cmd = QL_SMD_command(self.psu)
        elif self.psu in (PSUTypes.MPC3000, PSUTypes.MPC6000):
            self.cmd = QL_MPC_command(self.psu)

        self.connect_laser()
        self.cmd.power_setpoint = self.get_power()
        self.cmd.current_setpoint_pct = self.get_current()
    def on_deactivate(self):
        """ Deactivate module.
        """
        self.cmd.close_visa()

    def connect_laser(self):
        """ Connect to Instrument.

            @param str interface: visa interface identifier

            @return bool: connection success
        """
        try:
            self.cmd.open_visa(self.serial_interface, self.cmd.baud_rate)
        except visa.VisaIOError:
            self.log.exception('Communication Failure:')
            return False
        else:
            return True

    def disconnect_laser(self):
        """ Close the connection to the instrument.
        """
        self.cmd.close_visa()

    def allowed_control_modes(self):
        """ Control modes for this laser
        """
        return self.cmd.allowed_control_modes

    def get_control_mode(self):
        """ Get current laser control mode.

        @return ControlMode: current laser control mode
        """
        control_mode = self.cmd.get_control_mode()
        mode = ControlMode.POWER if control_mode == 'POWER' else ControlMode.CURRENT
        return mode

    def set_control_mode(self, mode):
        """ Set laser control mode.

        @param ControlMode mode: desired control mode
        """
        print('control mode {}'.format(mode))
        control_mode = 'POWER' if mode == ControlMode.POWER else 'CURRENT'
        self.cmd.set_control_mode(control_mode)

    def get_power(self):
        """ Get laser power.

        @return float: laser power in watts
        """
        answer = self.cmd.get_power()
        try:
            return(self.cmd.get_power_W())
        except ValueError:
            self.log.exception("Answer was {0}.".format(answer))
            return -1

    def get_power_setpoint(self):
        """ Get the laser power setpoint.

        @return float: laser power setpoint in watts
        """
        return self.cmd.power_setpoint

    def get_power_range(self):
        """ Get laser power range.

        @return float[2]: laser power range
        """
        return 0, self.maxpower

    def set_power(self, power):
        """ Set laser power

        @param float power: desired laser power in watts
        """
        self.cmd.power_setpoint = power
        self.cmd.set_power(power)

    def get_current_unit(self):
        """ Get unit for laser current.

        @return str: unit for laser current
        """
        return '%'

    def get_current_range(self):
        """ Get range for laser current.

        @return float[2]: range for laser current
        """
        return 0, 100

    def get_current(self):
        """ Cet current laser current

        @return float: current laser current
        """
        return float(self.cmd.get_current_pct())

    def get_current_setpoint(self):
        """ Current laser current setpoint.

        @return float: laser current setpoint
        """
        return float(self.cmd.get_current_setpoint_pct())

    def set_current(self, current_percent):
        """ Set laser current setpoint.

        @param float current_percent: laser current setpoint
        """
        self.cmd.current_setpoint_pct = current_percent
        self.cmd.set_current_pct(current_percent)
        return self.get_current()

    def get_shutter_state(self):
        """ Get laser shutter state.

        @return ShutterState: laser shutter state
        """
        return ShutterState.NO_SHUTTER

    def set_shutter_state(self, state):
        """ Set the desired laser shutter state.

        @param ShutterState state: desired laser shutter state
        @return ShutterState: actual laser shutter state
        """
        pass


    def get_temperatures(self):
        """ Get all available temperatures.

        @return dict: dict of temperature names and value
        """
        return {'psu': float(self.cmd.get_psu_temperature()),
                'laser': float(self.cmd.get_laser_temperature())}

    def get_lcd(self):
        """ Get the text displayed on the PSU display.

        @return str: text on power supply display
        """
        if self.psu in (PSUTypes.SMD12, PSUTypes.SMD6000):
            return ''
        else:
            return self.cmd.get_lcd_status()


    def get_laser_state(self):
        """ Get laser operation state

        @return LaserState: laser state
        """
        state = self.cmd.get_laser_status()
        if 'ENABLED' in state:
            return LaserState.ON
        elif 'DISABLED' in state:
            return LaserState.OFF
        else:
            return LaserState.UNKNOWN

    def set_laser_state(self, status):
        """ Set desited laser state.

        @param LaserState status: desired laser state
        """
        if self.get_laser_state() != status:
            if status == LaserState.ON:
                on_off = ('ON')
            elif status == LaserState.OFF:
                on_off = ('OFF')
            self.cmd.set_laser_state(on_off)


    def get_extra_info(self):
        """ Extra information from laser.
        For LaserQuantum devices, this is the firmware version, dump and timers information

        @return str: multiple lines of text with information about laser
        """
        self.cmd.get_sys_info()
        return str((asdict(self.cmd.sys_info)))

class Visa:

    def open(self, interface, baud_rate, write_termination, read_termination, send_end, timeout, query_delay):

        self.rm = visa.ResourceManager()
        self.inst = self.rm.open_resource(interface,
                                         baud_rate = baud_rate,
                                         write_termination = write_termination,
                                         read_termination = read_termination,
                                         send_end = send_end,
                                         query_delay = query_delay)

        self.inst.timeout = timeout

    def close(self):
        self.inst.close()
        self.rm.close()

    def write(self, message):
        """ Send a message to to laser

        @param string message: message to be delivered to the laser
        """
        self.inst.write(message)

    def read(self):
        values = self.inst.read()
        return values

    def query(self, message):
        """ Send a receive messages with the laser

        @param string message: message to be delivered to the laser

        @returns string response: message received from the laser
        """
        values = self.inst.query(message)
        return values

    def get_idn(self):
        return self.query('*IDN?')

class QL_common_command(Visa):
    sys_info = System_info()
    current_setpoint_pct = 0
    power_setpoint = 0

    def open_visa(self, interface, baud_rate):
        write_termination = '\r\n'
        read_termination = '\r\n'
        send_end = True
        timeout = 2000
        query_delay = 0.1
        self.open(interface, baud_rate, write_termination, read_termination, send_end, timeout, query_delay)

    def close_visa(self):
        self.close()

    def set_current_pct(self, current_pct):
        self.write('CURRENT={0}'.format(current_pct))
        self.read()

    def get_psu_temperature(self):
        try:
            return self._extract_num(self.query('PSUTEMP?'))
        except:
            return 0

    def get_laser_temperature(self):
        try:
            return self._extract_num(self.query('LASTEMP?'))
        except:
            return 0

    def set_laser_state(self, on_off):
        self.write('{}'.format(on_off))
        self.read()

    def get_power(self):
        return self.query('POWER?')

    def get_power_W(self):
        response = self.get_power()
        if 'mW' in response:
            return float(self._extract_num(response)) / 1000
        elif 'W' in response:
            return float(self._extract_num(response))
        else:
            return

    def set_power(self, power):
        self.write('POWER={0:f}'.format(power*1000))
        self.read()

    def get_runtimes(self):
        self.write('TIMERS')
        self.sys_info.psu_time = self._extract_num(self.read())
        self.sys_info.laser_time = self._extract_num(self.read())
        self.sys_info.laser_over_1A_time = self._extract_num(self.read())
        na = self.read() #for empty strings''

    def _extract_num(self, string):
        num = re.sub(r'[^\d.]', '', string)
        return num


class QL_SMD_command(QL_common_command):
    baud_rate = 9600
    allowed_control_modes = {ControlMode.POWER}

    def __init__(self, psu):
        self.psu = psu

    def get_current(self):
        return self.query('CURRENT?')

    def get_current_pct(self):
        return self._extract_num(self.get_current())

    def get_current_setpoint_pct(self):
        return self.current_setpoint_pct


    def get_laser_status(self):
        if self.psu == PSUTypes.SMD6000:
            return self.query('STAT?')
        else:
            return self.query('STATUS?')

    def get_control_mode(self):
        return 'POWER'

    def set_control_mode(self, mode):
        pass

    def get_firmware_version(self):
        if self.psu == PSUTypes.SMD6000:
            self.write('VERSION')
            version = self.read()
            na = self.read() #output empty string ''
            return version
        else:
            return self.query('SOFTVER?')

    def get_sys_info(self):
        self.get_runtimes()
        self.sys_info.firmware_ver = self.get_firmware_version()



class QL_MPC_command(QL_common_command):
    baud_rate = 19200
    allowed_control_modes = {ControlMode.POWER, ControlMode.CURRENT}

    def __init__(self, psu):
        self.psu = psu

    def get_current(self):
        return self.query('SETCURRENT1?')

    def get_current_pct(self):
        return self._extract_num(self.get_current())

    def get_current_setpoint_pct(self):
        return self.current_setpoint_pct

    def get_laser_status(self):
        return self.query('STATUS?')

    def get_control_mode(self):
        return self.query('CONTROL?')

    def set_control_mode(self, mode):
        self.write('CONTROL={}'.format(mode))
        self.read()


    def get_lcd_status(self):
        return self.query('STATUSLCD?')

    def get_firmware_version(self):
        return self.query('SOFTVER?')


