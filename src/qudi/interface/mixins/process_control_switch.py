# -*- coding: utf-8 -*-

"""
Copyright (c) 2022, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['ProcessControlSwitchMixin']

from typing import Dict, Tuple

from qudi.interface.switch_interface import SwitchInterface


class ProcessControlSwitchMixin(SwitchInterface):
    """ Mixin to inherit alongside interfaces contained in qudi.interface.process_control_interface
    to automatically provide a SwitchInterface for enabling/disabling process control hardware.

    Use like this:

        class MyHardwareModule(ProcessControlSwitchMixin, ProcessControlInterface):
            ...
    """

    @property
    def name(self) -> str:
        return self.module_name

    @property
    def available_states(self) -> Dict[str, Tuple[str, ...]]:
        return {'state': ('disabled', 'enabled')}

    def get_state(self, switch: str) -> str:
        """ Query state of single switch by name

        @param str switch: name of the switch to query the state for
        @return str: The current switch state
        """
        return self.available_states['state'][int(self.is_active)]

    def set_state(self, switch: str, state: str) -> None:
        """ Query state of single switch by name

        @param str switch: name of the switch to change
        @param str state: name of the state to set
        """
        try:
            active = bool(self.available_states[switch].index(state))
        except (KeyError, ValueError) as err:
            raise ValueError('Invalid switch name or state descriptor') from err
        self.set_activity_state(active)
