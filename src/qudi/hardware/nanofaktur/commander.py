# -*- coding: utf-8 -*-

"""
This module contains settings classes for the nanoFaktur EBD120x piezo scanner controller.

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

from qudi.hardware.nanofaktur.settings import ConnectionType
from qudi.hardware.nanofaktur.exceptions import ConnectionTypeException
import ctypes
from logging import getLogger

_logger = getLogger(__name__)


class NanoFakturDLLCommander:
    def __init__(self, dll_location: str):
        self._interface = None
        self._dll = ctypes.CDLL(dll_location)

    def connect(self, connection_type):
        if connection_type == ConnectionType.simulation:
            _logger.warn(
                "NOTE: In order to connect to the simulation, the simulation.bat needs to be running and even if the command prompt says so. DO NOT close the terminal of the simulation.bat"
            )
            # TODO: Make commander class that catches any occuring errors and correctly notifies the user
            self._interface = self._dll.nF_intf_connect_local(b"EBD-1202x0")
            _logger.info(f"Connected to simulation")
        else:
            raise ConnectionTypeException(self._connection_type)

    def disconnect(self):
        if self._dll is not None and self._interface >= 0:
            self._dll.nF_intf_disconnect(self._interface)
            self._dll = None
            self._interface = None
            _logger.warn("Device disconnected")

    def _send_command(self, command, *args):
        return_value = command(*args)
        if return_value < 0:
            _logger.error(f"Encountered an error with error code {return_value}")

    def get_dll_error(self):
        res = self._dll.nF_get_dll_last_error()
        _logger.warn(f"DLL error code: {res}")

    def get_device_error(self):
        res = self._dll.nF_get_dev_error()
        _logger.warn(f"Device error code: {res}")

    def get_system_error(self):
        res = self._dll.nF_get_sys_last_error()
        _logger.warn(f"System error code: {res}")

    def get_dll_version(self):
        message = float(self._dll.nF_get_dll_revision())
        _logger.warn(f"device_info = {message}")
