# -*- coding: utf-8 -*-

"""
Hardware module for using a Thorlabs power meter as a process value device.
It uses the TLPM driver, which supersedes the now legacy PM100D driver. It is installed
together with the Optical Power Monitor software.

Compatible devices according to Thorlabs:
- PM100A, PM100D, PM100USB
- PM101 Series, PM102 Series, PM103 Series
- PM16 Series, PM160, PM160T, PM160T-HP
- PM200, PM400

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

import platform
from ctypes import byref, c_bool, c_char_p, cdll, c_double, c_int, c_int16, c_long, create_string_buffer, c_uint32

from qudi.core.configoption import ConfigOption
from qudi.interface.process_control_interface import ProcessValueInterface, ProcessControlConstraints

# constants
SET_VALUE = c_int16(0)
MIN_VALUE = c_int16(1)
MAX_VALUE = c_int16(2)


class ThorlabsPowermeter(ProcessValueInterface):
    """ Hardware module for Thorlabs powermeter using the TLPM library.

    Example config:

    powermeter:
        module.Class: 'powermeter.thorlabs_powermeter.ThorlabsPowermeter'
        options:
            # Device address of the powermeter.
            # If omitted, the module will connect to the first powermeter found on the system.
            # The module logs an info message with the addresses of all available powermeters upon activation.
            address: 'USB0::0x1313::0x8078::P0012345::INSTR'
            wavelength: 637.0
    """

    _address = ConfigOption('address', missing='warn')
    _wavelength = ConfigOption('wavelength', default=None, missing='warn')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._channel_name = 'Power'
        self._constraints = None
        self._is_active = False

        self._dll = None
        self._devSession = c_long()
        self._devSession.value = 0
        self._device_address = None

    def _test_for_error(self, status):
        if status < 0:
            msg = create_string_buffer(1024)
            self._dll.TLPM_errorMessage(self._devSession, c_int(status), msg)
            self.log.exception(c_char_p(msg.raw).value)
            raise ValueError

    def on_activate(self):
        """ Startup the module """
        # load the dll
        try:
            if platform.architecture()[0] == '32bit':
                self._dll = cdll.LoadLibrary('C:/Program Files (x86)/IVI Foundation/VISA/WinNT/Bin/TLPM_32.dll')
            else:
                self._dll = cdll.LoadLibrary('C:/Program Files/IVI Foundation/VISA/Win64/Bin/TLPM_64.dll')
        except FileNotFoundError as e:
            self.log.error('TLPM _dll not found. Is the Thorlabs Optical Power Monitor software installed?')
            raise e

        # get list of available power meters
        device_count = c_uint32()
        result = self._dll.TLPM_findRsrc(self._devSession, byref(device_count))
        self._test_for_error(result)

        available_power_meters = []
        resource_name = create_string_buffer(1024)

        for i in range(0, device_count.value):
            result = self._dll.TLPM_getRsrcName(self._devSession, c_int(i), resource_name)
            self._test_for_error(result)
            available_power_meters.append(c_char_p(resource_name.raw).value.decode())

        self.log.info(f'Available power meters: {available_power_meters}')

        # figure out address of powermeter
        if self._address is None:
            try:
                first = available_power_meters[0]
            except IndexError:
                self.log.exception('No powermeter available on system.')
                raise ValueError
            else:
                self.log.info(f'Using first available powermeter with address {first}.')
                self._device_address = first
        else:
            if self._address in available_power_meters:
                self.log.info(f'Using powermeter with address {self._address}.')
                self._device_address = self._address
            else:
                self.log.exception(f'No powermeter with address {self._address} found.')
                raise ValueError

        # try connecting to the powermeter
        try:
            self._init_powermeter(reset=True)
        except ValueError as e:
            self.log.exception('Connection to powermeter was unsuccessful. Try using the Power Meter Driver '
                               + 'Switcher application to switch your powermeter to the TLPM driver.')
            raise e

        # set wavelength if defined in config
        if self._wavelength is not None:
            self._set_wavelength(self._wavelength)

        # get power range
        min_power, max_power = c_double(), c_double()
        result = self._dll.TLPM_getPowerRange(self._devSession, MIN_VALUE, byref(min_power))
        self._test_for_error(result)
        result = self._dll.TLPM_getPowerRange(self._devSession, MAX_VALUE, byref(max_power))
        self._test_for_error(result)

        # set constraints
        self._constraints = ProcessControlConstraints(
            process_channels=(self._channel_name,),
            units={self._channel_name: 'W'},
            limits={self._channel_name: (min_power.value, max_power.value)},
            dtypes={self._channel_name: float},
        )

        # close connection since default state is not active
        self._close_powermeter()

    def on_deactivate(self):
        """ Stops the module """
        self.set_activity_state(self._channel_name, False)

    @property
    def process_values(self):
        """ Read-Only property returning a snapshot of current process values for all channels.

        @return dict: Snapshot of the current process values (values) for all channels (keys)
        """
        value = self.get_process_value(self._channel_name)
        return {self._channel_name: value}

    @property
    def constraints(self):
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.

        @return ProcessControlConstraints: Hardware constraints
        """
        return self._constraints

    def set_activity_state(self, channel, active):
        """ Set activity state. State is bool type and refers to active (True) and inactive (False).
        """
        if channel != self._channel_name:
            raise AssertionError(f'Invalid channel name. Only valid channel is: {self._channel_name}')
        if active != self._is_active:
            self._is_active = active
            if active:
                self._init_powermeter()
            else:
                self._close_powermeter()

    def get_activity_state(self, channel):
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        if channel != self._channel_name:
            raise AssertionError(f'Invalid channel name. Only valid channel is: {self._channel_name}')
        return self._is_active

    @property
    def activity_states(self):
        """ Current activity state (values) for each channel (keys).
        State is bool type and refers to active (True) and inactive (False).
        """
        return {self._channel_name: self._is_active}

    @activity_states.setter
    def activity_states(self, values):
        """ Set activity state (values) for multiple channels (keys).
        State is bool type and refers to active (True) and inactive (False).
        """
        for ch, enabled in values.items():
            if ch != self._channel_name:
                raise AssertionError(f'Invalid channel name. Only valid channel is: {self._channel_name}')
            self.set_activity_state(ch, enabled)

    def get_process_value(self, channel):
        """ Return a measured value """
        if channel != self._channel_name:
            raise AssertionError(f'Invalid channel name. Only valid channel is: {self._channel_name}')
        if not self.get_activity_state(self._channel_name):
            raise AssertionError('Channel is not active. Activate first before getting process value.')
        return self._get_power()

    def _init_powermeter(self, reset=False):
        """
        Initialize powermeter and open a connection to it.
        :param reset: whether to reset the powermeter upon connection
        """
        id_query, reset_device = c_bool(True), c_bool(reset)
        address = create_string_buffer(self._device_address.encode('utf-8'))
        result = self._dll.TLPM_init(address, id_query, reset_device, byref(self._devSession))
        try:
            self._test_for_error(result)
        except ValueError as e:
            self.log.exception('Connection to powermeter was unsuccessful.')
            raise e

    def _close_powermeter(self):
        """ Close connection to powermeter. """
        result = self._dll.TLPM_close(self._devSession)
        self._test_for_error(result)

    def _get_power(self):
        """ Return the power reading from the power meter """
        power = c_double()
        result = self._dll.TLPM_measPower(self._devSession, byref(power))
        try:
            self._test_for_error(result)
        except ValueError as e:
            self.log.exception('Getting power from powermeter was unsuccessful.')
            raise e
        return power.value

    def _get_wavelength(self):
        """ Return the current measurement wavelength in nanometers """
        wavelength = c_double()
        result = self._dll.TLPM_getWavelength(self._devSession, SET_VALUE, byref(wavelength))
        self._test_for_error(result)
        return wavelength.value

    def _get_wavelength_range(self):
        """ Return the measurement wavelength range of the power meter in nanometers """
        wavelength_min = c_double()
        wavelength_max = c_double()
        result = self._dll.TLPM_getWavelength(self._devSession, MIN_VALUE, byref(wavelength_min))
        self._test_for_error(result)
        result = self._dll.TLPM_getWavelength(self._devSession, MAX_VALUE, byref(wavelength_max))
        self._test_for_error(result)

        return wavelength_min.value, wavelength_max.value

    def _set_wavelength(self, value):
        """ Set the new measurement wavelength in nanometers """
        min_wl, max_wl = self._get_wavelength_range()
        if min_wl <= value <= max_wl:
            result = self._dll.TLPM_setWavelength(self._devSession, c_double(value))
            self._test_for_error(result)
        else:
            self.log.error(f'Wavelength {value} nm is out of the range {min_wl} to {max_wl} nm.')
