# -*- coding: utf-8 -*-

"""
Qudi interface definitions for a simple multi/single channel setpoint device,
a simple multi/single channel process value reading device
and the combination of both (reading/setting setpoints and reading process value).

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

__all__ = ['ProcessSetpointInterface', 'ProcessValueInterface', 'ProcessControlInterface',
           'ProcessControlConstraints']

import numpy as np
from abc import abstractmethod
from typing import Iterable, Mapping, Union, Optional, Tuple, Type, Dict

from qudi.core.module import Base
from qudi.util.helpers import in_range


_Real = Union[int, float]


class ProcessControlConstraints:
    """ Data object holding the constraints for a set of process/setpoint channels.
    """
    def __init__(self,
                 setpoint_channels: Optional[Iterable[str]] = None,
                 process_channels: Optional[Iterable[str]] = None,
                 units: Optional[Mapping[str, str]] = None,
                 limits: Optional[Mapping[str, Tuple[_Real, _Real]]] = None,
                 dtypes: Optional[Mapping[str, Union[Type[int], Type[float]]]] = None
                 ) -> None:
        """
        """
        if units is None:
            units = dict()
        if limits is None:
            limits = dict()
        if dtypes is None:
            dtypes = dict()
        if setpoint_channels is None:
            setpoint_channels = tuple()
        if process_channels is None:
            process_channels = tuple()

        self._setpoint_channels = tuple() if setpoint_channels is None else tuple(setpoint_channels)
        self._process_channels = tuple() if process_channels is None else tuple(process_channels)

        all_channels = set(self._setpoint_channels)
        all_channels.update(self._process_channels)

        assert set(units).issubset(all_channels)
        assert all(isinstance(unit, str) for unit in units.values())
        assert set(limits).issubset(all_channels)
        assert all(len(lim) == 2 for lim in limits.values())
        assert set(dtypes).issubset(all_channels)
        assert all(t in (int, float) for t in dtypes.values())

        self._channel_units = {ch: units.get(ch, '') for ch in all_channels}
        self._channel_limits = {ch: limits.get(ch, (-np.inf, np.inf)) for ch in all_channels}
        self._channel_dtypes = {ch: dtypes.get(ch, float) for ch in all_channels}

    @property
    def all_channels(self) -> Tuple[str, ...]:
        return (*self.setpoint_channels, *self.process_channels)

    @property
    def setpoint_channels(self) -> Tuple[str, ...]:
        return self._setpoint_channels

    @property
    def process_channels(self) -> Tuple[str, ...]:
        return self._process_channels

    @property
    def channel_units(self) -> Dict[str, str]:
        return self._channel_units.copy()

    @property
    def channel_limits(self) -> Dict[str, Tuple[_Real, _Real]]:
        return self._channel_limits.copy()

    @property
    def channel_dtypes(self) -> Dict[str, Union[Type[int], Type[float]]]:
        return self._channel_dtypes.copy()

    def channel_value_in_range(self, channel: str, value: _Real) -> Tuple[bool, _Real]:
        return in_range(value, *self._channel_limits[channel])


class _ProcessControlInterfaceBase(Base):
    """ Abstract base class for all interfaces in this module
    """

    @property
    @abstractmethod
    def constraints(self) -> ProcessControlConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        pass

    @abstractmethod
    def set_activity_state(self, channel: str, active: bool) -> None:
        """ Set activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    @abstractmethod
    def get_activity_state(self, channel: str) -> bool:
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    # Non-abstract default implementations below

    @property
    def activity_states(self) -> Dict[str, bool]:
        """ Current activity state (values) for each channel (keys).
        State is bool type and refers to active (True) and inactive (False).
        """
        return {ch: self.get_activity_state(ch) for ch in self.constraints.all_channels}

    @activity_states.setter
    def activity_states(self, values: Mapping[str, bool]) -> None:
        """ Set activity state (values) for multiple channels (keys).
        State is bool type and refers to active (True) and inactive (False).
        """
        for ch, enabled in values.items():
            self.set_activity_state(ch, enabled)


class ProcessSetpointInterface(_ProcessControlInterfaceBase):
    """ A simple interface to control the setpoint for one or multiple process values.

    This interface is in fact a very general/universal interface that can be used for a lot of
    things. It can be used to interface any hardware where one to control one or multiple control
    values, like a temperature or how much a PhD student get paid.
    """

    @abstractmethod
    def set_setpoint(self, channel: str, value: _Real) -> None:
        """ Set new setpoint for a single channel """
        pass

    @abstractmethod
    def get_setpoint(self, channel: str) -> _Real:
        """ Get current setpoint for a single channel """
        pass

    # Non-abstract default implementations below

    @property
    def setpoints(self) -> Dict[str, _Real]:
        """ The current setpoints (values) for all channels (keys) """
        return {ch: self.get_setpoint(ch) for ch in self.constraints.setpoint_channels}

    @setpoints.setter
    def setpoints(self, values: Mapping[str, _Real]) -> None:
        """ Set the setpoints (values) for all channels (keys) at once """
        for ch, setpoint in values.items():
            self.set_setpoint(ch, setpoint)


class ProcessValueInterface(_ProcessControlInterfaceBase):
    """ A simple interface to read one or multiple process values.

    This interface is in fact a very general/universal interface that can be used for a lot of
    things. It can be used to interface any hardware where one to control one or multiple control
    values, like a temperature or how much a PhD student get paid.
    """

    @abstractmethod
    def get_process_value(self, channel: str) -> _Real:
        """ Get current process value for a single channel """
        pass

    # Non-abstract default implementations below

    @property
    def process_values(self) -> Dict[str, _Real]:
        """ Read-Only property returning a snapshot of current process values (values) for all
        channels (keys).
        """
        return {ch: self.get_process_value(ch) for ch in self.constraints.process_channels}


class ProcessControlInterface(ProcessSetpointInterface, ProcessValueInterface):
    """ A simple interface to control the setpoints for and read one or multiple process values.

    This interface is in fact a very general/universal interface that can be used for a lot of
    things. It can be used to interface any hardware where one to control one or multiple control
    values, like a temperature or how much a PhD student get paid.
    """
    pass
