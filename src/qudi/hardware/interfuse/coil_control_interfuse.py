# -*- coding: utf-8 -*-

"""
Interfuse combining three Keithley 2200 series power supplies (one per magnet coil axis)
and a single NI digital switch (one relay per axis) into a unified control interface for a
three-axis vector magnet.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory
of this distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

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

Each Keithley 2200 power supply can only output a non-negative voltage/current. In this
setup, the PSU is run in constant-current mode: VOLTAGE is only ever a positive compliance
limit, while CURRENT is the physically meaningful quantity whose SIGN determines the
direction of field along a given coil axis. That sign is realised by a single digital relay
per axis (coil_polarity_switch), which physically reverses the current direction through
the coil -- the PSU itself always outputs a non-negative current magnitude.

This module implements CoilControlInterface (see qudi.interface.coil_control_interface),
presenting:
    - set_voltage(axis, value):  plain, always non-negative compliance voltage. Does NOT
                                 touch the polarity relay.
    - set_current(axis, value): SIGNED current setpoint. A sign change relative to the
                                 axis's current relay state will flip that relay (see safety
                                 sequence below).

    coil_control.set_voltage('x', 20.0)   -> PSU x compliance voltage = 20.0 V
    coil_control.set_current('x', -2.5)   -> PSU x current = 2.5 A, relay X = '-'

SAFETY: switching polarity while the PSU output is live briefly interrupts output.
Whenever a requested current sign change actually requires flipping the relay, this
interfuse will:
    1. Turn the PSU output OFF (only if it was already ON)
    2. Program the new current magnitude
    3. Flip the relay
    4. Turn the PSU output back ON (only if it was ON in step 1)
If the sign is unchanged, the current magnitude is updated directly with no output
interruption.

Setting a signed current of exactly 0 does NOT flip the relay -- the existing polarity is
left untouched and only the magnitude is zeroed. This avoids a spurious relay flip when
ramping a field down through zero.

------------------------------------------------------------------------

Example config for copy-paste (matches the three power supplies and switch already in use):

    coil_control:
        module.Class: 'interfuse.coil_control_interfuse.CoilControlInterfuse'
        connect:
            psu_x: 'magnet_psu_x'
            psu_y: 'magnet_psu_y'
            psu_z: 'magnet_psu_z'
            polarity_switch: 'coil_polarity_switch'
        options:
            switch_names:
                x: 'X'
                y: 'Y'
                z: 'Z'
            positive_state: '+'
            negative_state: '-'
            polarity_switch_delay: 0.2   # seconds, settle time before/after flipping polarity
                                        # while output is momentarily off
"""

import time

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex

from qudi.interface.coil_control_interface import CoilControlInterface
from qudi.hardware.power_supply.keithley_2200 import Keithley2200PowerSupply
from qudi.hardware.switches.digital_switch_ni import DigitalSwitchNI


class CoilControlInterfuse(CoilControlInterface):
    """ Combines three Keithley 2200 power supplies and one NI digital polarity switch
    into a unified control interface, one channel per magnet axis ('x', 'y', 'z').

    Voltage is a plain, non-negative compliance limit. Current is signed and drives the
    polarity relay for its axis.
    """

    # ── Connectors ────────────────────────────────────────────────────────────
    # Attribute names must match the yaml 'connect:' keys exactly.
    psu_x = Connector(interface=Keithley2200PowerSupply)
    psu_y = Connector(interface=Keithley2200PowerSupply)
    psu_z = Connector(interface=Keithley2200PowerSupply)
    polarity_switch = Connector(interface=DigitalSwitchNI)

    # ── Config options ───────────────────────────────────────────────────────
    # Maps axis letter ('x'/'y'/'z') to the switch name used in the
    # coil_polarity_switch module's own 'switches:' config dict.
    _switch_names = ConfigOption(
        'switch_names',
        default={'x': 'X', 'y': 'Y', 'z': 'Z'},
        missing='nothing'
    )

    # The two state strings used by the polarity switch to represent positive
    # and negative current direction. Must match the strings used in the
    # switch's own 'switches:' config (e.g. ['+', '-']).
    _positive_state = ConfigOption('positive_state', default='+', missing='nothing')
    _negative_state = ConfigOption('negative_state', default='-', missing='nothing')

    # Settle delay applied before and after flipping the polarity relay, while
    # the PSU output is momentarily off. Mirrors the settle delays already
    # used in the underlying PSU and switch modules.
    _polarity_switch_delay = ConfigOption('polarity_switch_delay', default=0.2, missing='nothing')

    # Preferred display/iteration order for axes, used by the axes property.
    # Any configured axis name not in this tuple is appended afterward,
    # sorted alphabetically.
    _AXIS_ORDER = ('x', 'y', 'z')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Per-axis locks: operations on different axes are fully independent
        # (separate GPIB instruments), so they should not block one another.
        # Operations on the SAME axis are serialized to keep the "read
        # polarity -> possibly flip relay -> set magnitude" sequence atomic.
        self._axis_locks = {'x': Mutex(), 'y': Mutex(), 'z': Mutex()}

    # =========================================================================
    # Qudi module lifecycle
    # =========================================================================

    def on_activate(self):
        """ Validate that the polarity switch is configured with the expected
        per-axis switch names and polarity state strings.
        """
        available = self.polarity_switch().available_states

        for axis, switch_name in self._switch_names.items():
            if switch_name not in available:
                self.log.error(
                    f'Configured switch_names entry "{axis}" -> "{switch_name}" not found '
                    f'in polarity switch available_states: {list(available)}. '
                    f'Axis "{axis}" will not function correctly.'
                )
                continue

            states = available[switch_name]
            if self._positive_state not in states or self._negative_state not in states:
                self.log.error(
                    f'Switch "{switch_name}" (axis "{axis}") has states {states}, which '
                    f'does not contain both the configured positive_state '
                    f'("{self._positive_state}") and negative_state ("{self._negative_state}"). '
                    f'Axis "{axis}" will not function correctly.'
                )

        self.log.info('CoilControlInterfuse activated for axes: {0}'.format(list(self.axes)))

    def on_deactivate(self):
        """ Nothing to do -- the underlying PSU and switch modules manage their
        own connections and lifecycle independently.
        """
        pass

    # =========================================================================
    # CoilControlInterface -- axes and limits
    # =========================================================================

    @property
    def axes(self):
        """ Names of all available axes, in a stable, predictable order
        (x, y, z first if present, then any other configured axis names
        sorted alphabetically).

        @return tuple: Axis name strings.
        """
        known = [a for a in self._AXIS_ORDER if a in self._switch_names]
        extra = sorted(a for a in self._switch_names if a not in self._AXIS_ORDER)
        return tuple(known + extra)

    def get_voltage_limits(self, axis):
        """ Return the allowed (min, max) compliance voltage for the given axis,
        taken directly from the underlying PSU's configured voltage_limits.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return tuple: (min_voltage, max_voltage) in volts, always non-negative.
        """
        axis = self._validate_axis(axis)
        min_v, max_v = self._get_psu(axis)._voltage_limits
        return (max(0.0, min_v), max_v)

    def get_current_limits(self, axis):
        """ Return the allowed (min, max) SIGNED current for the given axis,
        derived from the underlying PSU's configured current_limits magnitude.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return tuple: (-max_magnitude, +max_magnitude) in amps.
        """
        axis = self._validate_axis(axis)
        min_mag, max_mag = self._get_psu(axis)._current_limits
        magnitude_limit = max(abs(min_mag), abs(max_mag))
        return (-magnitude_limit, magnitude_limit)

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _validate_axis(self, axis):
        """ Normalize and validate an axis specifier.

        @param str axis: Axis name, case-insensitive ('x', 'X', 'y', ...).
        @return str: Lower-case axis name ('x', 'y', or 'z').
        """
        axis = str(axis).strip().lower()
        if axis not in self._switch_names:
            raise ValueError(
                f'Invalid axis "{axis}". Valid axes are: {list(self._switch_names)}'
            )
        return axis

    def _get_psu(self, axis):
        """ Return the PSU hardware module instance for the given axis.

        @param str axis: Lower-case axis name ('x', 'y', or 'z').
        @return Keithley2200PowerSupply: The connected PSU instance.
        """
        if axis == 'x':
            return self.psu_x()
        elif axis == 'y':
            return self.psu_y()
        elif axis == 'z':
            return self.psu_z()
        raise ValueError(f'Invalid axis "{axis}".')

    def _get_switch_name(self, axis):
        """ Return the switch name (as configured in coil_polarity_switch's own
        'switches:' config) corresponding to the given axis.

        @param str axis: Lower-case axis name ('x', 'y', or 'z').
        @return str: Switch name, e.g. 'X'.
        """
        return self._switch_names[axis]

    def _get_polarity(self, axis):
        """ Return the current relay polarity state string for the given axis.

        @param str axis: Lower-case axis name ('x', 'y', or 'z').
        @return str: Either the configured positive_state or negative_state string.
        """
        switch_name = self._get_switch_name(axis)
        return self.polarity_switch().get_state(switch_name)

    def _sign_of_polarity(self, polarity_state):
        """ Convert a polarity state string into a numeric sign multiplier.

        @param str polarity_state: Either positive_state or negative_state.
        @return float: +1.0 or -1.0.
        """
        return 1.0 if polarity_state == self._positive_state else -1.0

    # =========================================================================
    # Voltage control -- plain, non-negative compliance limit, no relay involved
    # =========================================================================

    def set_voltage(self, axis, value):
        """ Set the (non-negative) compliance voltage for the given axis. Does
        NOT touch the polarity relay -- voltage has no sign in this setup.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @param float value: Compliance voltage in volts. Must be >= 0; a
                            negative value will be logged as an error and its
                            absolute value used instead, since voltage carries
                            no directional meaning here.
        @return float: The voltage actually applied (after clipping to PSU limits).
        """
        axis = self._validate_axis(axis)
        value = float(value)

        if value < 0:
            self.log.error(
                f'set_voltage("{axis}", {value}): voltage is always non-negative in this '
                f'setup (only current direction is signed, via the polarity relay). '
                f'Using absolute value {abs(value)} V instead.'
            )
            value = abs(value)

        with self._axis_locks[axis]:
            return self._get_psu(axis).set_voltage(value)

    def get_voltage(self, axis):
        """ Return the currently programmed compliance voltage for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return float: Voltage setpoint in volts (always >= 0).
        """
        axis = self._validate_axis(axis)
        return self._get_psu(axis).get_voltage()

    def get_measured_voltage(self, axis):
        """ Return the actual measured output voltage for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return float: Measured voltage in volts (always >= 0).
        """
        axis = self._validate_axis(axis)
        return self._get_psu(axis).get_measured_voltage()

    # =========================================================================
    # Current control -- SIGNED, drives the polarity relay
    # =========================================================================

    def set_current(self, axis, value):
        """ Set a signed current setpoint for the given axis. A sign change
        relative to the current relay polarity will flip that axis's polarity
        relay (see module docstring for the safety sequence used).

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @param float value: Signed current setpoint in amps.
        @return float: The signed current actually applied (after clipping).
        """
        axis = self._validate_axis(axis)
        value = float(value)
        psu = self._get_psu(axis)
        switch = self.polarity_switch()
        switch_name = self._get_switch_name(axis)

        with self._axis_locks[axis]:
            current_polarity = switch.get_state(switch_name)

            if value == 0:
                # Setting exactly zero should not trigger a polarity flip --
                # relevant when ramping a field down through zero.
                desired_polarity = current_polarity
            else:
                desired_polarity = self._positive_state if value > 0 else self._negative_state

            magnitude = abs(value)

            if desired_polarity != current_polarity:
                was_on = psu.get_output_state()

                if was_on:
                    self.log.info(
                        f'Axis "{axis}": current polarity change requires flipping the '
                        f'relay. Turning PSU output off before switching.'
                    )
                    psu.output_off()
                    time.sleep(self._polarity_switch_delay)

                applied_magnitude = psu.set_current(magnitude)
                switch.set_state(switch_name, desired_polarity)
                time.sleep(self._polarity_switch_delay)

                if was_on:
                    psu.output_on()
                    self.log.info(f'Axis "{axis}": PSU output restored after polarity flip.')

            else:
                applied_magnitude = psu.set_current(magnitude)

        return applied_magnitude * self._sign_of_polarity(desired_polarity)

    def get_current(self, axis):
        """ Return the currently programmed signed current setpoint for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return float: Signed current setpoint in amps.
        """
        axis = self._validate_axis(axis)
        psu = self._get_psu(axis)
        polarity = self._get_polarity(axis)
        return psu.get_current() * self._sign_of_polarity(polarity)

    def get_measured_current(self, axis):
        """ Return the actual measured output current for the given axis, signed
        according to the axis's current relay polarity.

        NOTE: the sign here is a LOGICAL construct combining the PSU's own
        non-negative measurement with the relay's known state, not a literal
        negative-current measurement performed by the instrument itself.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return float: Signed measured current in amps.
        """
        axis = self._validate_axis(axis)
        psu = self._get_psu(axis)
        polarity = self._get_polarity(axis)
        return psu.get_measured_current() * self._sign_of_polarity(polarity)

    # =========================================================================
    # Output on/off and polarity readback
    # =========================================================================

    def output_on(self, axis):
        """ Turn the PSU output ON for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        """
        axis = self._validate_axis(axis)
        self._get_psu(axis).output_on()

    def output_off(self, axis):
        """ Turn the PSU output OFF for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        """
        axis = self._validate_axis(axis)
        self._get_psu(axis).output_off()

    def get_output_state(self, axis):
        """ Return whether the PSU output is currently ON for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return bool: True if output is ON, False if OFF.
        """
        axis = self._validate_axis(axis)
        return self._get_psu(axis).get_output_state()

    def get_polarity(self, axis):
        """ Return the current relay polarity state string for the given axis.

        @param str axis: Axis name, case-insensitive ('x', 'y', or 'z').
        @return str: Either the configured positive_state or negative_state string.
        """
        axis = self._validate_axis(axis)
        return self._get_polarity(axis)