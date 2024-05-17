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
from qudi.hardware.nanofaktur.settings import ScannerSettings
from qudi.interface.process_control_interface import (
    ProcessSetpointInterface,
    ProcessControlConstraints,
)
from qudi.interface.mixins.process_control_switch import ProcessControlSwitchMixin
from qudi.hardware.nanofaktur.commander import NanoFakturDLLCommander


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

    def on_activate(self) -> None:
        self._commander = NanoFakturDLLCommander(self._dll_location)
        self._settings = ScannerSettings()
        self._commander.connect(self._connection_type)

    def on_deactivate(self) -> None:
        self._commander.disconnect()

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
