# -*- coding: utf-8 -*-

"""
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
from PySide2.QtCore import Signal
from PySide2.QtWidgets import QHBoxLayout

from qudi.gui.qdyne.tools.dataclass_widget import SettingsWidget


class MultiSettingsWidget(SettingsWidget):
    method_widget_updated_sig = Signal()

    def __init__(self, parent):
        super().__init__(parent)

    def create_widgets(self):
        super().create_widgets()
        self.create_method_widgets()

    def create_method_widgets(self):
        pass

    def create_header_layout(self):
        header_layout = QHBoxLayout()
        header_layout.addLayout(self.create_method_layout())
        header_layout.addLayout(self.create_mode_layout())
        self.layouts["header"] = header_layout
        return header_layout

    def create_method_layout(self):
        self.layouts["method"] =
        return self.layouts["method"]

