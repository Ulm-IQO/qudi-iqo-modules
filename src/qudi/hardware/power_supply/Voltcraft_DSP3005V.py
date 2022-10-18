# -*- coding: utf-8 -*-

"""
Hardware file for Volcraft DSP3005 Power supply
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
import pyvisa
from qudi.core.configoption import ConfigOption
from qudi.interface.process_control_interface import ProcessControlInterface




class Voltcraft3005V(ProcessControlInterface):

    _visa_address = ConfigOption('visa_address', missing='error')

    def on_activate(self):

        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self._visa_address,
                                          write_termination='\r\n',
                                          read_termination='\r\n',
                                          baud_rate=115200)


    def on_deactivate(self):


    def constraints(self):

    def is_active(self):

    def set_control_value(self, value):

    def get_control_value(self):

    def get_control_unit(self):








