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
from typing import Iterable, Mapping, Union, Optional, Tuple, Type, Dict

_Real = Union[int, float]



class Voltcraft3005V(ProcessControlInterface):

#    _visa_address = ConfigOption('visa_address', missing='error')

    def on_activate(self):
        pass

        # self.rm = pyvisa.ResourceManager()
        # self.inst = self.rm.open_resource(self._visa_address,
        #                                   write_termination='\r\n',
        #                                   read_termination='\r\n',
        #                                   baud_rate=115200)
        #

    def on_deactivate(self):
        pass


    def constraints(self):
        pass
    @property
    def is_active(self) -> bool:
        """ Current activity state.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    @is_active.setter
    def is_active(self, active: bool):
        """ Set activity state.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    @property
    def setpoints(self) -> Dict[str, _Real]:
        """ The current setpoints (values) for all channels (keys) """
        pass

    @setpoints.setter
    def setpoints(self, values: Mapping[str, _Real]):
        """ Set the setpoints (values) for all channels (keys) at once """
        pass

    def set_activity_state(self, active: bool) -> None:
        """ Set activity state. State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def set_setpoint(self, value: _Real, channel: str) -> None:
        """ Set new setpoint for a single channel """
        pass

    def get_setpoint(self, channel: str) -> _Real:
        """ Get current setpoint for a single channel """
        pass

    @property
    def process_values(self) -> Dict[str, _Real]:
        """ Read-Only property returning a snapshot of current process values (values) for all
        channels (keys).
        """
        pass

    def set_activity_state(self, active: bool) -> None:
        """ Set activity state. State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def get_process_value(self, channel: str) -> _Real:
        """ Get current process value for a single channel """
        pass








