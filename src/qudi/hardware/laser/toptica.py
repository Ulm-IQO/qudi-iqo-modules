# -*- coding: utf-8 -*-
"""
This module controls LaserQuantum lasers.

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
import time
import re
from typing import List, Type, Union

from debugpy.launcher import channel
from qudi.core.configoption import ConfigOption
from qudi.interface.simple_laser_interface import SimpleLaserInterface
from qudi.interface.simple_laser_interface import ControlMode, ShutterState, LaserState
from dataclasses import dataclass, asdict

@dataclass
class System_info():
    firmware_ver: str = ''
    psu_time: str = ''
    laser_time: str = ''
    laser_over_1A_time: str = ''


@dataclass(frozen=True)
class SerialSettings:
    baud_rate: int
    data_bits: int
    parity: pyvisa.constants.Parity
    stop_bits: pyvisa.constants.StopBits
    flow_control: pyvisa.constants.ControlFlow
    timeout: float
    read_timeout: float = 2
    line_termination: str = '\r\n'

@dataclass(frozen=True, init=False)
class IBeamSerialSettings(SerialSettings):
    def __init__(self, timeout: float = 0.2, read_timeout: float = 2, *args, **kwargs):
        super().__init__(
            baud_rate = 115200,
            data_bits = 8,
            parity = pyvisa.constants.Parity.none,
            stop_bits = pyvisa.constants.StopBits.one,
            flow_control = pyvisa.constants.ControlFlow.none,
            timeout = timeout,
            line_termination = '\r\n',
            read_timeout = read_timeout
        )


class TopticaLaser(SimpleLaserInterface):
    """
    Hardware file for laser quantum laser.
    Example config for copy-paste:

    ibeam:
        module.Class: 'laser.toptica.TopticaLaser'
        options:
            serial_address: 'ASRL1::INSTR'
            timeout_s: 0.2 # optional
            channel: 2 # optional
    """

    _serial_address: str = ConfigOption(name='serial_address', default='ASRL1::INSTR', missing='error')
    _timeout: float = ConfigOption(name='timeout_s', default=0.2, missing='warn')  # [s]
    _read_timeout: float = ConfigOption(name='read_timeout_s', default=2, missing='warn')  # [s]
    _channel = ConfigOption(name='channel', default=2, missing='warn')  # Channel number that is used to control the laser

    def on_activate(self):
        """ Activate module.
        """
        self._serial_settings = IBeamSerialSettings(timeout=self._timeout, read_timeout=self._read_timeout)
        self._device = IBeamSmart(self._serial_address, self._serial_settings, channel=self._channel)
        self._device.enable_channels()

    def on_deactivate(self):
        """ Deactivate module.
        """
        self._device.close()

    def allowed_control_modes(self):
        """ Control modes for this laser
        """
        return self._device.allowed_control_modes

    def get_control_mode(self):
        """ Get current laser control mode.

        @return ControlMode: current laser control mode
        """
        return ControlMode.POWER

    def set_control_mode(self, mode):
        """ Set laser control mode.

        @param ControlMode mode: desired control mode
        """
        if mode == ControlMode.CURRENT:
            self.log.error(f"Currently current control is not implemented.")
            raise ValueError

    def get_power(self):
        """ Get laser power.

        @return float: laser power in watts
        """
        return self._device.get_power()

    def get_power_setpoint(self):
        """ Get the laser power setpoint.

        @return float: laser power setpoint in watts
        """
        return self._device.power_setpoint

    def get_power_range(self):
        """ Get laser power range.

        @return float[2]: laser power range
        """
        return self._device.power_range

    def set_power(self, power):
        """ Set laser power

        @param float power: desired laser power in watts
        """
        self._device.set_power(power)

    def get_current_unit(self):
        """ Get unit for laser current.

        @return str: unit for laser current
        """
        return self._device.current_unit

    def get_current_range(self):
        """ Get range for laser current.

        @return float[2]: range for laser current
        """
        return self._device.current_range

    def get_current(self):
        """ Cet current laser current

        @return float: current laser current
        """
        return self._device.current

    def get_current_setpoint(self):
        """ Current laser current setpoint.

        @return float: laser current setpoint
        """
        return self._device.current

    def set_current(self, current_percent):
        """ Set laser current setpoint.

        @param float current_percent: laser current setpoint
        """
        self.cmd.current_setpoint_pct = current_percent
        self.cmd.set_current_pct(current_percent)
        return self.get_current()

    def get_shutter_state(self):
        """ Get laser shutter state.

        @return ShutterState: laser shutter state
        """
        return ShutterState.UNKNOWN

    def set_shutter_state(self, state):
        """ Set the desired laser shutter state.

        @param ShutterState state: desired laser shutter state
        @return ShutterState: actual laser shutter state
        """
        pass


    def get_temperatures(self):
        """ Get all available temperatures.

        @return dict: dict of temperature names and value
        """
        return self._device.temperature

    def get_laser_state(self):
        """ Get laser operation state

        @return LaserState: laser state
        """
        return self._device.laser_state

    def set_laser_state(self, status):
        """ Set desited laser state.

        @param LaserState status: desired laser state
        """
        if self.get_laser_state() != status:
            self._device.laser_state = status


    def get_extra_info(self):
        """ Extra information from laser.
        For LaserQuantum devices, this is the firmware version, dump and timers information

        @return str: multiple lines of text with information about laser
        """
        return str(self._device.sys_info)

class IBeamSmart:
    def __init__(self, address: str, serial_settings: SerialSettings, channel: int):
        # Channel to control using hardware file
        self.channel = int(channel)
        self.rm = pyvisa.ResourceManager()
        self.device = self.rm.open_resource(address)
        self.serial_settings = serial_settings
        self._set_serial_settings(serial_settings)

        self._sys_info = self._get_sys_info()
        self.current_unit = 'mA'

    def _set_serial_settings(self, settings: SerialSettings):
        self.device.baud_rate = settings.baud_rate
        self.device.data_bits = settings.data_bits
        self.device.parity = settings.parity
        self.device.stop_bits = settings.stop_bits
        self.device.flow_control = settings.flow_control
        self.device.timeout = settings.timeout

    def close(self):
        self.laser_state = LaserState.OFF
        self.device.close()
        self.rm.close()

    @property
    def allowed_control_modes(self):
        return {ControlMode.POWER}

    @property
    def laser_state(self):
        state = self.query("sta la")[0]
        if state == "ON":
            return LaserState.ON
        if state == "OFF":
            return LaserState.OFF
        return LaserState.UNKNOWN

    @laser_state.setter
    def laser_state(self, state: LaserState):
        if state == LaserState.ON:
            self.write("la on")
        if state == LaserState.OFF:
            self.write("la off")

    @property
    def temperature(self) -> dict:
        heatsink = self.query("sh temp sys")
        laser_diode = self.query("sh temp")
        return {"Heatsink": self._extract_numbers(heatsink[0])[0],
                "Laser Diode": self._extract_numbers(laser_diode[1])[0]}

    @property
    def current(self) -> float:
        return self._extract_numbers(self.query("sh cur")[1])[0]

    @property
    def current_range(self) -> tuple:
        return 0, self._extract_numbers(self.sys_info['satellite']['Imax'])[0]

    @property
    def power_range(self) -> tuple:
        return 0, self._extract_numbers(self.sys_info['satellite']['Pmax'])[0]

    @property
    def power_setpoint(self):
        powers = self.query("sh level pow")
        power = self._extract_numbers(powers[self.channel - 1])[1]
        return power / 1e3

    @property
    def sys_info(self):
        return self._sys_info

    def _get_sys_info(self) -> dict:
        info_dict = self._extract_dictionary(self.query('serial'))
        info_dict['satellite'] = self._extract_dictionary(self.query('sh sat'))
        info_dict['system'] = self._extract_dictionary(self.query('sh sys'))
        return info_dict

    def get_power(self) -> float:
        power = self.query("sh pow")
        power = self._extract_numbers(power[1])[0]
        return power / 1e6

    def set_power(self, power: float):
        self.write(f"ch {self.channel} pow {power * 1e3}")

    def write(self, message: str):
        self.device.write(message)
        time.sleep(self.serial_settings.timeout)
        # device will always print something, clear it with this statement
        self._read_all()
        time.sleep(self.serial_settings.timeout)

    def query(self, message: str) -> list:
        self.device.write(message)
        return self.read()

    def read(self) -> list:
        answer = self._read_all()
        return self._strip_answer(answer)

    def enable_channels(self):
        self.write("en ch 1")
        self.write("en ch 2")

    def _read_all(self):
        full_data = b''
        start = time.time()
        while True:
            try:
                chunk = self.device.read_raw()
                if not chunk:
                    break
                full_data += chunk
                time.sleep(0.05)
            except pyvisa.errors.VisaIOError as e:
                if 'VI_ERROR_TMO' in str(e):
                    if self.serial_settings.line_termination.encode() in full_data:
                        break
                    if time.time() - start > self.serial_settings.read_timeout:
                        raise TimeoutError("Did not manage read anything within the read timeout set.")
                else:
                    raise e

        return full_data.decode('utf-8')

    def _strip_answer(self, answer: str) -> list:
        """Strips the device's answer from unnecessary '\r\n' and '\r\nCMD>' and puts the individual lines into a list"""
        return [part.strip() for part in answer.replace('CMD>', '').splitlines() if part.strip()]

    def _extract_numbers(self, string: str, return_type: Type[Union[int, float]] = float) -> List[Union[int, float]]:
        matches = re.findall(r'[-+]?\d*\.\d+|\d+', string)
        numbers = [return_type(m) for m in matches]
        return numbers

    def _extract_dictionary(self, response_list: list):
        result = {}
        for line in response_list:
            if ': ' in line:
                key, value = line.split(': ', 1)  # Split only on the first ':'
                result[key.strip()] = value.strip()
        return result



