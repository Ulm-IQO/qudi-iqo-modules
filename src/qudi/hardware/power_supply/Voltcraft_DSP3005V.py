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

    _visa_address = ConfigOption(name='visa_address', missing='warn')

    def on_activate(self):
        pass

        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self._visa_address,
                                          write_termination='\r\n',
                                          read_termination='\r\n',
                                          baud_rate=115200)
        self.ctr_mode = 'CURR'


    def on_deactivate(self):
        self.inst.close()
        pass


    def constraints(self):
        pass
    @property
    def is_active(self) -> bool:
        """ Current activity state.
        State is bool type and refers to active (True) and inactive (False).
        """
        on_off = self.inst.query.('OUTP?')
        if on_off = 'ON':
            is_active = True
        elif on_off = 'OFF':
            is_active = False
        else:
            raise ValueError('Unknown output status')

        return is_active

    @is_active.setter
    def is_active(self, active: bool):
        """ Set activity state.
        State is bool type and refers to active (True) and inactive (False).
        """
        on_off = 'ON' if active else 'OFF'
        self.inst.write('{}'.format(on_off))

    @property
    def setpoints(self) -> Dict[str, _Real]:
        """ The current setpoints (values) for all channels (keys) """
        return self.get_setpoint()
    @setpoints.setter
    def setpoints(self, values: Mapping[str, _Real]):
        """ Set the setpoints (values) for all channels (keys) at once """
        for k, v in values.items():
            self.set_setpoint(v, k)

    def set_activity_state(self, active: bool) -> None:
        """ Set activity state. State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def set_setpoint(self, value: _Real, channel: str) -> None:
        """ Set new setpoint for a single channel """
        self.inst.write('{} {}'.format(self.ctr_mode, value))

    def get_setpoint(self, channel: str) -> _Real:
        """ Get current setpoint for a single channel """
        return {'ch0': int(self.inst.query('{}}?'.format(self.ctr_mode)))}

    @property
    def process_values(self) -> Dict[str, _Real]:
        """ Read-Only property returning a snapshot of current process values (values) for all
        channels (keys).
        """
        return {'ch0': self.get_process_value('ch0')}


    def get_process_value(self, channel: str) -> _Real:
        """ Get current process value for a single channel """
        return self.inst.query('MEAS:{}?'.format(self.ctr_mode))









