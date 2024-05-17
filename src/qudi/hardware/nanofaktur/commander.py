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
        self._dll_return_types()

    def connect(self, connection_type):
        if connection_type == ConnectionType.simulation:
            try:
                self._interface = self._send_command(
                    self._dll.nF_intf_connect_local, b"EBD-120230"
                )
            except:
                _logger.error(
                    "An Error occured during simulation activation.\n"
                    "NOTE: In order to connect to the simulation, the simulation.bat needs to be running and"
                    " even if the command prompt says so: DO NOT close the terminal of the simulation.bat"
                )
                raise
            _logger.info(f"Connected to simulation")
        else:
            raise ConnectionTypeException(self._connection_type)

    def disconnect(self):
        if self._dll is not None and self._interface >= 0:
            self._dll.nF_intf_disconnect(self._interface)
            self._dll = None
            self._interface = None
            _logger.warn("Device disconnected")

    def _dll_return_types(self):
        self._dll.nF_intf_connect_local.restype = ctypes.c_int
        self._dll.nF_get_dll_last_error.restype = ctypes.c_int
        self._dll.nF_get_sys_last_error.restype = ctypes.c_int
        self._dll.nF_get_dev_error.restype = ctypes.c_int
        self._dll.nF_get_dll_revision.restype = ctypes.c_float

    def get_axis_position(self, axis: int):
        # TODO: Maybe the byref *args passing does not work
        axis = ctypes.c_int(axis)
        position = ctypes.c_float()
        self._send_command(
            self._dll.nF_get_dev_axis_position,
            1,
            ctypes.byref(axis),
            ctypes.byref(position),
        )
        return axis.value, position.value

    def get_axis_target(self, axis):
        pass

    def _send_command(self, command, *args):
        return_value = command(*args)
        if return_value < 0:
            _logger.error(
                f"During {command.__name__} call encountered an error with error code {return_value}."
                f"\nLast DLL error = {self.get_dll_error()}"
                f"\nLast system error = {self.get_system_error()}"
                f"\nLast device error = {self.get_device_error()}"
            )
            raise Exception(
                f"During {command.__name__} call encountered an error with error code {return_value}"
            )
        return return_value

    def get_dll_error(self):
        return self._dll.nF_get_dll_last_error()

    def get_device_error(self):
        return self._dll.nF_get_dev_error()

    def get_system_error(self):
        return self._dll.nF_get_sys_last_error()

    def get_dll_version(self):
        res = float(self._dll.nF_get_dll_revision())
        _logger.info(res)
        return res
