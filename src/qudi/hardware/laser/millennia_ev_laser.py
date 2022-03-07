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

import pyvisa as visa
from qudi.core.configoption import ConfigOption
from qudi.interface.simple_laser_interface import SimpleLaserInterface
from qudi.interface.simple_laser_interface import ControlMode, ShutterState, LaserState
from dataclasses import dataclass, asdict
import json

@dataclass
class System_info():
    idn: str = ''
    diode_h: str = ''
    head_h: str = ''
    psu_h: str = ''

class MillenniaeVLaser(SimpleLaserInterface):
    """ Spectra Physics Millennia diode pumped solid state laser.

    Example config for copy-paste:

    millennia_laser:
        module.Class: 'laser.millennia_ev_laser.MillenniaeVLaser'
        interface: 'ASRL1::INSTR'
        maxpower: 25 # in Watt

    """

    serial_interface = ConfigOption(name='interface', default='ASRL1::INSTR', missing='warn')
    maxpower = ConfigOption(name='maxpower', default=5, missing='warn')

    def on_activate(self):
        """ Activate Module.
        """
        self.command = MEV_command()
        self._control_mode = ControlMode.POWER
        self.connect_laser(self.serial_interface)

    def on_deactivate(self):
        """ Deactivate module
        """
        self.disconnect_laser()

    def connect_laser(self, interface):
        """ Connect to Instrument.

            @param str interface: visa interface identifier

            @return bool: connection success
        """
        try:
            self.command.open_visa(interface)
        except visa.VisaIOError as e:
            self.log.exception('Communication Failure:')
            return False
        else:
            return True

    def disconnect_laser(self):
        """ Close the connection to the instrument.
        """
        self.command.close_visa()

    def allowed_control_modes(self):
        """ Control modes for this laser

            @return ControlMode: available control modes
        """
        return {ControlMode.POWER, ControlMode.CURRENT}

    def get_control_mode(self):
        """ Get active control mode

        @return ControlMode: active control mode
        """
        return self._control_mode

    def set_control_mode(self, mode):
        """ Set actve control mode

        @param ControlMode mode: desired control mode
        @return ControlMode: actual control mode
        """
        if mode in self.allowed_control_modes():
            self._control_mode = mode

    def get_power(self):
        """ Current laser power

        @return float: laser power in watts
        """
        return float(self.command.get_power())

    def get_power_setpoint(self):
        """ Current laser power setpoint

        @return float: power setpoint in watts
        """
        return float(self.command.get_power_setpoint())

    def get_power_range(self):
        """ Laser power range

        @return float[2]: laser power range
        """
        return 0, self.maxpower

    def set_power(self, power):
        """ Set laser power setpoint

        @param float power: desired laser power
        """
        self.command.set_power(power)

    def get_current_unit(self):
        """ Get unit for current

        return str: unit for laser current
        """
        return 'A'

    def get_current_range(self):
        """ Get range for laser current

            @return float[2]: range for laser current
        """
        return 0, float(self.command.get_diode_current_limit())

    def get_current(self):
        """ Get current laser current

        @return float: current laser current
        """
        return float(self.command.get_diode_current())

    def get_current_setpoint(self):
        """ Get laser current setpoint

        @return float: laser current setpoint
        """
        return float(self.command.get_diode_current_setpoint())

    def set_current(self, current):
        """ Set laser current setpoint

        @param float current_percent: desired laser current setpoint
        @return float: actual laer current setpoint
        """
        self.command.set_current(current)

    def get_shutter_state(self):
        """ Get laser shutter state

        @return ShutterState: current laser shutter state
        """
        state = int(self.command.get_shutter_state())
        if state == ShutterState.OPEN:
            return ShutterState.OPEN
        elif state == ShutterState.CLOSED:
            return ShutterState.CLOSED
        else:
            return ShutterState.UNKNOWN

    def set_shutter_state(self, state):
        """ Set laser shutter state.

        @param ShuterState state: desired laser shutter state
        @return ShutterState: actual laser shutter state
        """
        if state != self.get_shutter_state():
            self.command.set_shutter_state(state)

    def get_temperatures(self):
        """ Get all available temperatures

        @return dict: dict of temperature names and values
        """
        return self.command.get_temperatures()

    def get_laser_state(self):
        """ Get laser state.

        @return LaserState: current laser state
        """
        diode = self.command.get_diode_state()
        state = self.command.get_system_status()

        if state in ('SYS ILK', 'KEY ILK'):
            return LaserState.LOCKED
        elif state == 'System Ready':
            if diode == 1:
                return LaserState.ON
            elif diode == 0:
                return LaserState.OFF
            else:
                return LaserState.UNKNOWN
        else:
            return LaserState.UNKNOWN

    def set_laser_state(self, state):
        """ Set laser state

        @param LaserState status: desited laser state
        @return LaserState: actual laser state
        """
        if self.get_laser_state() != state:
            self.command.set_laser_state(state)


    def get_extra_info(self):
        """ Formatted information about the laser.

            @return str: Laser information
        """
        return json.dumps(asdict(self.command.get_sys_info()))

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

    def query(self, message):
        """ Send a receive messages with the laser

        @param string message: message to be delivered to the laser

        @returns string response: message received from the laser
        """
        values = self.inst.query(message)
        return values

    def get_idn(self):
        return self.query('*IDN?')

class MEV_command(Visa):
    sys_info = System_info()

    def open_visa(self, interface):
        baud_rate = 115200
        write_termination = '\n'
        read_termination = '\n'
        send_end = True
        timeout = 1000
        query_delay = 0.01
        self.open(interface, baud_rate, write_termination, read_termination, send_end, timeout, query_delay)

    def close_visa(self):
        self.close()

    def set_power(self, power):
        self.write('P:{0:f}'.format(power))

    def set_current(self, current):
        self.write('C:{0}'.format(current))

    def set_shutter_state(self, state):
        self.write('SHT:{}'.format(int(state)))

    def set_laser_state(self, state):
        on_off = 'ON' if state == 1 else 'OFF'
        self.write('{}'.format(on_off))

    def get_sys_info(self):
        self.sys_info.idn = self.get_idn()
        self.sys_info.diode_h = self.query('?DH')
        self.sys_info.head_h = self.query('?HEADHRS')
        self.sys_info.psu_h = self.query('?PSHRS')
        return self.sys_info

    def get_power(self):
        return self.query('?P')

    def get_power_setpoint(self):
        return self.query('?PSET')

    def get_diode_current_limit(self):
        return self.query('?DCL')

    def get_diode_current(self):
        return self.query('?C1')

    def get_diode_current_setpoint(self):
        return self.query('?CS1')

    def get_shutter_state(self):
        return int(self.query('?SHT'))

    def get_diode_state(self):
        return int(self.query('?D'))

    def get_system_status(self):
        return self.query('?F')

    def get_temperatures(self):
        return {'crystal': float(self.query('?SHG')),
                'diode': float(self.query('?T')),
                'tower': float(self.query('?TT')),
                'cab': float(self.query('?CABTEMP'))
                }





