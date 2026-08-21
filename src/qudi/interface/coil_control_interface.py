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

Common interface for hardware/interfuse modules that control a set of independent output
"axes" (e.g. the X/Y/Z coils of a vector magnet), where each axis has:
    - a non-negative compliance VOLTAGE limit
    - a SIGNED CURRENT setpoint, whose sign may be realised by the implementing module
      however it sees fit (e.g. a polarity relay) -- this interface does not care how the
      sign is physically achieved, only that get_current()/set_current() behave as if
      current were bipolar
    - an independent output ON/OFF state
"""

__all__ = ['CoilControlInterface']

from typing import Tuple, Dict
from abc import abstractmethod

from qudi.core.module import Base


class CoilControlInterface(Base):
    """ Methods to control a set of independent voltage/current/output axes,
    e.g. the coils of a vector magnet.

    Getter and setter functions for a single axis need to be implemented by the hardware
    or interfuse module. Default implementations for relative stepping and "all axes at
    once" convenience methods are provided based on those.
    """

    # ── Abstract methods -- must be implemented by the hardware/interfuse module ────────

    @property
    @abstractmethod
    def axes(self) -> Tuple[str, ...]:
        """ Names of all available axes, e.g. ('x', 'y', 'z').

        @return tuple: Axis name strings.
        """
        pass

    @abstractmethod
    def get_voltage_limits(self, axis: str) -> Tuple[float, float]:
        """ Return the allowed (min, max) compliance voltage for the given axis.
        Always non-negative.

        @param str axis: Axis name.
        @return tuple: (min_voltage, max_voltage) in volts.
        """
        pass

    @abstractmethod
    def get_current_limits(self, axis: str) -> Tuple[float, float]:
        """ Return the allowed (min, max) SIGNED current for the given axis.

        @param str axis: Axis name.
        @return tuple: (min_current, max_current) in amps, e.g. (-5.0, 5.0).
        """
        pass

    @abstractmethod
    def set_voltage(self, axis: str, value: float) -> float:
        """ Set the (non-negative) compliance voltage for the given axis.

        @param str axis: Axis name.
        @param float value: Desired voltage in volts.
        @return float: Voltage actually applied (after clipping to limits).
        """
        pass

    @abstractmethod
    def get_voltage(self, axis: str) -> float:
        """ Return the currently programmed compliance voltage for the given axis.

        @param str axis: Axis name.
        @return float: Voltage setpoint in volts.
        """
        pass

    @abstractmethod
    def get_measured_voltage(self, axis: str) -> float:
        """ Return the actual measured output voltage for the given axis.

        @param str axis: Axis name.
        @return float: Measured voltage in volts.
        """
        pass

    @abstractmethod
    def set_current(self, axis: str, value: float) -> float:
        """ Set a signed current setpoint for the given axis.

        @param str axis: Axis name.
        @param float value: Desired signed current in amps.
        @return float: Signed current actually applied (after clipping to limits).
        """
        pass

    @abstractmethod
    def get_current(self, axis: str) -> float:
        """ Return the currently programmed signed current setpoint for the given axis.

        @param str axis: Axis name.
        @return float: Signed current setpoint in amps.
        """
        pass

    @abstractmethod
    def get_measured_current(self, axis: str) -> float:
        """ Return the actual measured signed output current for the given axis.

        @param str axis: Axis name.
        @return float: Signed measured current in amps.
        """
        pass

    @abstractmethod
    def output_on(self, axis: str) -> None:
        """ Turn the output ON for the given axis.

        @param str axis: Axis name.
        """
        pass

    @abstractmethod
    def output_off(self, axis: str) -> None:
        """ Turn the output OFF for the given axis.

        @param str axis: Axis name.
        """
        pass

    @abstractmethod
    def get_output_state(self, axis: str) -> bool:
        """ Return whether the output is currently ON for the given axis.

        @param str axis: Axis name.
        @return bool: True if output is ON, False if OFF.
        """
        pass

    # ── Non-abstract default implementations ─────────────────────────────────────────────

    @property
    def number_of_axes(self) -> int:
        """ Number of axes provided by the hardware.

        @return int: number of axes
        """
        return len(self.axes)

    def set_voltage_relative(self, axis: str, delta: float) -> float:
        """ Step the compliance voltage for the given axis by a relative amount.
        Default implementation based on get_voltage()/set_voltage(); may be overridden
        by the implementing module for efficiency or atomicity.

        @param str axis: Axis name.
        @param float delta: Amount to add to the current voltage (volts).
        @return float: Voltage actually applied (after clipping to limits).
        """
        return self.set_voltage(axis, self.get_voltage(axis) + float(delta))

    def set_current_relative(self, axis: str, delta: float) -> float:
        """ Step the signed current setpoint for the given axis by a relative amount.
        Default implementation based on get_current()/set_current(); may be overridden
        by the implementing module for efficiency or atomicity.

        @param str axis: Axis name.
        @param float delta: Amount to add to the current signed current (amps).
        @return float: Signed current actually applied (after clipping to limits).
        """
        return self.set_current(axis, self.get_current(axis) + float(delta))

    def all_outputs_off(self) -> None:
        """ Turn off the output on all axes. """
        for axis in self.axes:
            self.output_off(axis)

    def get_all_voltages(self) -> Dict[str, float]:
        """ Return the currently programmed compliance voltage for all axes.

        @return dict: {axis: voltage} in volts.
        """
        return {axis: self.get_voltage(axis) for axis in self.axes}

    def get_all_currents(self) -> Dict[str, float]:
        """ Return the currently programmed signed current setpoint for all axes.

        @return dict: {axis: current} in amps.
        """
        return {axis: self.get_current(axis) for axis in self.axes}

    def get_all_output_states(self) -> Dict[str, bool]:
        """ Return the output ON/OFF state for all axes.

        @return dict: {axis: bool}.
        """
        return {axis: self.get_output_state(axis) for axis in self.axes}