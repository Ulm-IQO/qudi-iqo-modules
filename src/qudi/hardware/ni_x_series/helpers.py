# -*- coding: utf-8 -*-

"""
This file contains various helper objects to facilitate hardware modules using the nidaqmx package.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

__all__ = ['sanitize_device_name', 'ao_channel_names', 'ai_channel_names', 'ao_voltage_range',
           'ai_voltage_range', 'pfi_channel_names', 'counter_names', 'normalize_channel_name']

import nidaqmx as ni
from typing import List, Tuple


def sanitize_device_name(device_name: str) -> str:
    """ Performs case-insensitive comparison of connected device names reported by nidaqmx.system
    with given name and returns match.
    Raises ValueError if no connected device can be matched.
    """
    device_name = device_name.lower()
    connected_devices = ni.system.System().devices.device_names
    for name in connected_devices:
        if name.lower() == device_name:
            return name
    raise ValueError(f'No connected device matches given name "{device_name}". '
                     f'Found devices: {connected_devices}')


def ao_channel_names(device_name: str) -> List[str]:
    """ Extracts available physical analog output channel names from device """
    channel_names = ni.system.Device(device_name).ao_physical_chans.channel_names
    return [normalize_channel_name(channel) for channel in channel_names]


def ai_channel_names(device_name: str) -> List[str]:
    """ Extracts available physical analog input channel names from device """
    channel_names = ni.system.Device(device_name).ai_physical_chans.channel_names
    return [normalize_channel_name(channel) for channel in channel_names]


def ao_voltage_range(device_name: str) -> Tuple[float, float]:
    """ Extracts the biggest available analog output voltage range from device """
    ao_ranges = ni.system.Device(device_name).ao_voltage_rngs
    max_ao_range = (0, 0)
    for voltage_range in zip(ao_ranges[::2], ao_ranges[1::2]):
        low, high = sorted(voltage_range)
        if (high - low) > (max_ao_range[1] - max_ao_range[0]):
            max_ao_range = (low, high)
    return max_ao_range


def ai_voltage_range(device_name: str) -> Tuple[float, float]:
    """ Extracts the biggest available analog input voltage range from device """
    ai_ranges = ni.system.Device(device_name).ai_voltage_rngs
    max_ai_range = (0, 0)
    for voltage_range in zip(ai_ranges[::2], ai_ranges[1::2]):
        low, high = sorted(voltage_range)
        if (high - low) > (max_ai_range[1] - max_ai_range[0]):
            max_ai_range = (low, high)
    return max_ai_range


def pfi_channel_names(device_name: str) -> List[str]:
    """ Extracts available physical PFI channel names from device """
    channel_names = ni.system.Device(device_name).terminals
    return [normalize_channel_name(channel) for channel in channel_names if 'PFI' in channel]


def counter_names(device_name: str) -> List[str]:
    """ Extracts available counter (channel) names from device """
    channel_names = ni.system.Device(device_name).co_physical_chans.channel_names
    return [normalize_channel_name(chnl) for chnl in channel_names if 'ctr' in chnl.lower()]


def normalize_channel_name(channel_name: str) -> str:
    """ Extracts the bare terminal name from a string and strip it of the device name and dashes """
    name = channel_name.strip('/')
    if 'dev' in name.lower():
        name = name.split('/', 1)[-1]
    return name
