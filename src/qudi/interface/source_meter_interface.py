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

------------------------------------------------------------------------

OVERVIEW

Interface for a single-channel source meter / power supply used as a VOLTAGE source with a
CURRENT limit (compliance), e.g. a Keithley 2400 SourceMeter supplying an APD module.
"""

__all__ = ['SourceMeterInterface']

from typing import Tuple, Optional, Dict
from abc import abstractmethod

from qudi.core.module import Base


class SourceMeterInterface(Base):
    """ Voltage source with current limit and output switch. """

    @property
    @abstractmethod
    def voltage_limits(self) -> Tuple[float, float]:
        """ Allowed (min, max) voltage setpoint in volts. """
        pass

    @property
    @abstractmethod
    def current_limit_limits(self) -> Tuple[float, float]:
        """ Allowed (min, max) current limit (compliance) in amps. """
        pass

    @abstractmethod
    def get_voltage(self) -> float:
        """ Programmed voltage setpoint in volts (not the measured output voltage). """
        pass

    @abstractmethod
    def set_voltage(self, value: float) -> float:
        """ Set the voltage setpoint.

        @return float: the setpoint actually applied, in volts.
        """
        pass

    @abstractmethod
    def get_current_limit(self) -> float:
        """ Programmed current limit (compliance) in amps. """
        pass

    @abstractmethod
    def set_current_limit(self, value: float) -> float:
        """ Set the current limit (compliance).

        @return float: the limit actually applied, in amps.
        """
        pass

    @abstractmethod
    def get_output_state(self) -> bool:
        """ True if the output is ON. """
        pass

    @abstractmethod
    def set_output_state(self, state: bool) -> bool:
        """ Switch the output ON (True) or OFF (False).

        @return bool: the output state actually reached.
        """
        pass

    @abstractmethod
    def measure(self) -> Optional[Dict[str, float]]:
        """ Measure the output.

        @return dict or None: {'voltage': V, 'current': A, 'compliance': bool}, or None if no
                              measurement is possible (e.g. output OFF).
        """
        pass
