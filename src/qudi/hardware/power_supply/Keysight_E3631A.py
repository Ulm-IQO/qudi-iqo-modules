# -*- coding: utf-8 -*-

"""
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
try:
    import pyvisa as visa
except ImportError:
    import visa

from qudi.core.module import Base
from qudi.core.configoption import ConfigOption
from qudi.interface.process_control_interface import ProcessControlInterface


class E3631A(Base, ProcessControlInterface):
    """ Hardware module for power supply Keysight E3631A.

    Example config for copy-paste:
    voltage_generator:
        module.Class: 'power_supply.Keysight_E3631A.E3631A'
        options:
            address: 'ASRL9::INSTR'

    """

    _address = ConfigOption('address', missing='error')

    _voltage_min = ConfigOption('voltage_min', 0)
    _voltage_max = ConfigOption('voltage_max', 6)
    _current_max = ConfigOption('current_max', missing='error')

    _inst = None
    model = ''

    def on_activate(self):
        """ Startup the module """
        self.log.warning("This module has not been tested on the new qudi core."
                         "Use with caution and contribute bug fixed back, please.")

        rm = visa.ResourceManager()
        try:
            self._inst = rm.open_resource(self._address)
        except visa.VisaIOError:
            self.log.error('Could not connect to hardware. Please check the wires and the address.')

        self.model = self._query('*IDN?').split(',')[1]

        self._write("*RST;*CLS")
        time.sleep(3)
        self._query("*OPC?")

        self._write("INST P6V")
        self._write("VOLT 0")
        self._write("CURR {}".format(self._current_max))

        self._write("OUTP ON")

    def on_deactivate(self):
        """ Stops the module """
        self._write("OUTP OFF")
        self._inst.close()

    def _write(self, cmd):
        """ Function to write command to hardware"""
        self._inst.write(cmd)
        time.sleep(.01)

    def _query(self, cmd):
        """ Function to query hardware"""
        return self._inst.query(cmd)

    def set_control_value(self, value):
        """ Set control value, here heating power.

            @param flaot value: control value
        """
        mini, maxi = self.get_control_limit()
        if mini <= value <= maxi:
            self._write("VOLT {}".format(value))
        else:
            self.log.error('Voltage value {} out of range'.format(value))

    def get_control_value(self):
        """ Get current control value, here heating power

            @return float: current control value
        """
        return float(self._query("VOLT?").split('\r')[0])

    def get_control_unit(self):
        """ Get unit of control value.

            @return tuple(str): short and text unit of control value
        """
        return 'V', 'Volt'

    def get_control_limit(self):
        """ Get minimum and maximum of control value.

            @return tuple(float, float): minimum and maximum of control value
        """
        return self._voltage_min, self._voltage_max
