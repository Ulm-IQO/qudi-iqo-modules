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

from qudi.core.configoption import ConfigOption
from qudi.interface.process_control_interface import (
    ProcessSetpointInterface,
    ProcessControlConstraints,
)
from qudi.interface.mixins.process_control_switch import ProcessControlSwitchMixin
from qudi.hardware.nanofaktur.settings import ConnectionType
from qudi.hardware.nanofaktur.exceptions import ConnectionTypeException
import ctypes


class EBD120(ProcessControlSwitchMixin, ProcessSetpointInterface):
    """
    A module to control the nanoFaktur EBD 120 piezo scanner controller.
    This class is designed to work with the NiScanningProbeInterfuse to fullfill the ScanningProbeInterface.
    """

    _dll_location = ConfigOption(
        name="dll_location",
        default="C:/nanoFaktur/lib/mingw/x64/nF_interface_x64.dll",
        missing="info",
    )
    _connection_type = ConfigOption(
        name="connection_type",
        default="simulation",
        missing="info",
        constructor=lambda x: x.lower(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._dll = None
        self._interface = None

    def on_activate(self) -> None:
        self._dll = ctypes.CDLL(self._dll_location)
        self._connect()

    def on_deactivate(self) -> None:
        self._disconnect()

    def _connect(self):
        if self._connection_type == ConnectionType.simulation:
            self.log.warn(
                "NOTE: In order to connect to the simulation, the simulation.bat needs to be running and even if the command prompt says so. DO NOT close the terminal of the simulation.bat"
            )
            # TODO: Make commander class that catches any occuring errors and correctly notifies the user
            self._interface = self._dll.nF_intf_connect_local(b"EBD-1202x0")
            self.log.info(f"Connected to simulation")
        else:
            raise ConnectionTypeException(self._connection_type)

    def _disconnect(self):
        if self._dll is not None and self._interface >= 0:
            self._dll.nF_intf_disconnect(self._interface)
            self._dll = None
            self._interface = None
            self.log.warn("Device disconnected")

    def _get_dll_error(self):
        res = self._dll.nF_get_dll_last_error()
        self.log.warn(f"DLL error code: {res}")

    def _get_device_error(self):
        res = self._dll.nF_get_dev_error()
        self.log.warn(f"Device error code: {res}")

    def _get_system_error(self):
        res = self._dll.nF_get_sys_last_error()
        self.log.warn(f"System error code: {res}")

    def _get_dll_version(self):
        message = float(self._dll.nF_get_dll_revision())
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
