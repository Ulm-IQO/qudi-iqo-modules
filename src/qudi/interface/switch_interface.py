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

__all__ = ['SwitchInterface']

from typing import Dict, Mapping, Sequence, Tuple
from abc import abstractmethod

from qudi.core.module import Base


class SwitchInterface(Base):
    """ Methods to control slow (mechanical) switching devices.

    Getter and setter functions to control single switches need to be implemented by the hardware
    module.
    Automatically implements Python properties to access and set the switch states based on the
    single switch getter and setter method.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """ Name of the hardware as string.

        @return str: The name of the hardware
        """
        pass

    @property
    @abstractmethod
    def available_states(self) -> Dict[str, Tuple[str, ...]]:
        """ Names of the states as a dict of tuples.

        The keys contain the names for each of the switches. The values are tuples of strings
        representing the ordered names of available states for each switch.

        @return dict: Available states per switch in the form {"switch": ("state1", "state2")}
        """
        pass

    @abstractmethod
    def get_state(self, switch: str) -> str:
        """ Query state of single switch by name

        @param str switch: name of the switch to query the state for
        @return str: The current switch state
        """
        pass

    @abstractmethod
    def set_state(self, switch: str, state: str) -> None:
        """ Query state of single switch by name

        @param str switch: name of the switch to change
        @param str state: name of the state to set
        """
        pass

    # Non-abstract default implementations below

    @property
    def number_of_switches(self) -> int:
        """ Number of switches provided by the hardware.

        @return int: number of switches
        """
        return len(self.available_states)

    @property
    def switch_names(self) -> Tuple[str, ...]:
        """ Names of all available switches as tuple.

        @return str[]: Tuple of strings of available switch names.
        """
        return tuple(self.available_states)

    @property
    def states(self) -> Dict[str, str]:
        """ The current states the hardware is in as state dictionary with switch names as keys and
        state names as values.

        @return dict: All the current states of the switches in the form {"switch": "state"}
        """
        return {switch: self.get_state(switch) for switch in self.available_states}

    @states.setter
    def states(self, state_dict: Mapping[str, str]) -> None:
        """ The setter for the states of the hardware.

        The states of the system can be set by specifying a dict that has the switch names as keys
        and the names of the states as values.

        @param dict state_dict: state dict of the form {"switch": "state"}
        """
        assert isinstance(state_dict, dict), 'Parameter "state_dict" must be dict type'
        for switch, state in state_dict.items():
            self.set_state(switch, state)

    @staticmethod
    def _chk_refine_available_switches(switch_dict: Dict[str, Sequence[str]]
                                       ) -> Dict[str, Tuple[str, ...]]:
        """ Perform some general checking of the configured available switches and their possible
        states. When implementing a hardware module, you can overwrite this method to include
        custom checks, but make sure to call this implementation first via super().

        @param dict switch_dict: available switches in a dict like {"switch1": ["state1", "state2"]}
        @return dict: The refined switch dict to replace the dict passed as argument
        """
        assert isinstance(switch_dict, dict), 'switch_dict must be a dict of tuples'
        assert all((isinstance(sw, str) and sw) for sw in
                   switch_dict), 'Switch name must be non-empty string'
        assert all(len(states) > 1 for states in
                   switch_dict.values()), 'State tuple must contain at least 2 states'
        assert all(all((s and isinstance(s, str)) for s in states) for states in
                   switch_dict.values()), 'Switch states must be non-empty strings'
        # Convert state lists to tuples in order to restrict mutation
        return {switch: tuple(states) for switch, states in switch_dict.items()}
