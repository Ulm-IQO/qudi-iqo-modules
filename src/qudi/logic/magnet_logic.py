# -*- coding: utf-8 -*-
"""
Master logic to combine sequence_generator_logic and pulsed_measurement_logic to be
used with a single GUI.

Copyright (c) 2023, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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
from PySide2 import QtCore

from qudi.core.connector import Connector
from qudi.core.module import LogicBase

class MagnetLogic(LogicBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        return

    def on_activate(self):
        pass

    def on_deactivate(self):
        pass