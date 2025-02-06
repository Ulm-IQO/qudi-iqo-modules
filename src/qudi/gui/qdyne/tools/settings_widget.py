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
from PySide2 import QtCore, QtWidgets
from PySide2.QtCore import Signal
from PySide2.QtWidgets import QLabel, QComboBox, QHBoxLayout

from qudi.gui.qdyne.tools.dataclass_widget import DataclassWidget


class SettingsWidget(DataclassWidget):
    mode_widget_updated_sig = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.mode_list =

    def create_widgets(self):
        super().create_widgets()
        self.create_mode_widgets()

    def create_mode_widgets(self):
        mode_label = QLabel()
        mode_label.setText("Mode")
        mode_comboBox = QComboBox()
        mode_comboBox.addItems(self.mode_list)
        self.labels["mode"] = mode_label
        self.widgets["mode"] = mode_comboBox

    def arange_layout(self):
        self.layout_main = QtWidgets.QVBoxLayout()
        self.layout_main.addLayout(self.create_header_layout())
        self.layout_main.addLayout(self.create_data_layout())

    def create_header_layout(self):
        self.layouts['header'] = self.create_mode_layout()
        return self.layouts['header']

    def create_mode_layout(self):
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.labels["mode"])
        mode_layout.addWidget(self.widgets["mode"])
        self.layouts["mode"] = mode_layout
        return mode_layout

