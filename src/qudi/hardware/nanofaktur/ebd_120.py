# -*- coding: utf-8 -*-

"""
This module contains the Qudi interface file for scanning probe hardware.

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

from qudi.interface.process_control_interface import (
    ProcessSetpointInterface,
    ProcessControlConstraints,
)
import ctypes


class EBD120(ProcessSetpointInterface):
    """
    A module to control the nanoFaktur EBD 120 piezo scanner controller.
    This class is designed to work with the NiScanningProbeInterfuse to fullfill the ScanningProbeInterface.
    """

    _dll_location = "C:/nanoFaktur/lib/mingw/x64/nF_interface_x64.dll"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._dll = None

    def on_activate(self) -> None:
        self._dll = ctypes.CDLL(self._dll_location)

    def on_deactivate(self) -> None:
        self.unload_dll(self._dll)

    def _get_device_info(self):
        message = self._dll.nF_get_dll_revision()
        self.log.warn(f"device_info = {message}")

    def set_setpoint(self, channel: str, value) -> None:
        """Set new setpoint for a single channel"""
        pass

    def get_setpoint(self, channel: str):
        """Get current setpoint for a single channel"""
        pass

    def get_activity_state(self, channel: str) -> bool:
        pass

    def set_activity_state(self, channel: str, active: bool) -> None:
        pass

    def constraints(self) -> ProcessControlConstraints:
        pass

    def unload_dll(self, dll):
        """
        Method to free WD-DASK dll. This makes sure that the DLL can be accessed again without terminating the python thread first.
        """
        dll_handle = ctypes.c_void_p(dll._handle)
        del dll
        ctypes.windll.kernel32.FreeLibrary(dll_handle)
        print(f"Freed DLL at location {dll_handle.value}")
