# -*- coding: utf-8 -*-

__all__ = ['AutoScanInterface', 'AutoScanConstraints']

import numpy as np
from abc import abstractmethod
from typing import Iterable, Mapping, Union, Optional, Tuple, Type, Dict

from qudi.core.module import Base
from qudi.util.helpers import in_range


_Real = Union[int, float]


class AutoScanConstraints:
    """ Data object holding the constraints for a autoscan channel.
    """
    def __init__(self,
                 channels: Iterable[str],
                 units: Mapping[str, str],
                 limits: Mapping[str, Tuple[_Real, _Real]],
                 dtypes: Mapping[str, Union[Type[int], Type[float]]]
                 ) -> None:
        """
        """

        self._channels = tuple(channels)

        assert set(units).issubset(channels)
        assert all(isinstance(unit, str) for unit in units.values())
        assert set(limits).issubset(channels)
        assert all(len(lim) == 2 for lim in limits.values())
        assert set(dtypes).issubset(channels)
        assert all(t in (int, float) for t in dtypes.values())

        self._channel_units = {ch: units.get(ch, '') for ch in channels}
        self._channel_limits = {ch: limits.get(ch, (-np.inf, np.inf)) for ch in channels}
        self._channel_dtypes = {ch: dtypes.get(ch, float) for ch in channels}

    @property
    def channels(self) -> Tuple[str, ...]:
        return self._channels

    @property
    def channel_units(self) -> Dict[str, str]:
        return self._channel_units.copy()

    @property
    def channel_limits(self) -> Dict[str, Tuple[_Real, _Real]]:
        return self._channel_limits.copy()

    @property
    def channel_dtypes(self) -> Dict[str, Union[Type[int], Type[float]]]:
        return self._channel_dtypes.copy()


class AutoScanInterface(Base):
    """ Abstract base class for all interfaces in this module
    """

    @property
    @abstractmethod
    def constraints(self) -> AutoScanConstraints:
        """ Read-Only property holding the constraints for this hardware module.
        See class AutoScanConstraints for more details.
        """
        pass

    @abstractmethod
    def trigger_scan(self) -> None:
        """Trigger a scan on all channels."""
        pass

    @abstractmethod
    def get_last_scan(self, channel: str):
        """Return the last scan performed for channer `channel`."""
        pass

