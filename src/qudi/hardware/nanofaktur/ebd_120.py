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

from qudi.interface.process_control_interface import ProcessSetpointInterface


class EBD120(ProcessSetpointInterface):
    """
    A module to control the nanoFaktur EBD 120 piezo scanner controller.
    This class is designed to work with the NiScanningProbeInterfuse to fullfill the ScanningProbeInterface.
    """

    def ___init__(self, *args, **kwargs):
        pass

    def set_setpoint(self, channel: str, value: _Real) -> None:
        """Set new setpoint for a single channel"""
        pass

    def get_setpoint(self, channel: str) -> _Real:
        """Get current setpoint for a single channel"""
        pass
