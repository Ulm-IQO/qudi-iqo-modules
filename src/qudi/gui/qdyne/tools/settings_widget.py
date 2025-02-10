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
from PySide2.QtCore import Signal, Slot
from PySide2.QtWidgets import QLabel, QComboBox, QVBoxLayout, QHBoxLayout, QPushButton

from qudi.gui.qdyne.tools.dataclass_widget import DataclassWidget


class SettingsWidget(DataclassWidget):
    """Data widget class for settings widget.

    Several modes of settings can be handled.
    Modes are variants of a dataclass.
    """
    mode_widget_updated_sig = Signal()
    add_mode_pushed_sig = Signal(str)
    delete_mode_pushed_sig = Signal(str)

    def __init__(self, mediator, dataclass_obj=None) -> None:
        """Initialize the dataclass widget with the corresponding mediator.

        Parameters
        ----------
        mediator : SettingsMediator
            mediator class object to communicate with a set of variants for a single dataclass.
        dataclass_obj : dataclass
            dataclass object for creation of initial widgets.
            When None is passed, no widget is created. set_data should be called later.
        """
        super().__init__(mediator, dataclass_obj)

    @property
    def current_mode(self):
        return self.widgets["mode"].currentText()

    def create_widgets(self):
        super().create_widgets()
        self.create_mode_widgets()

    def create_mode_widgets(self):
        mode_label = QLabel()
        mode_label.setText("Mode")

        mode_comboBox = QComboBox()
        mode_comboBox.addItems(self.mediator.mode_list)
        mode_comboBox.setEditable(True)

        add_mode_pushButton = QPushButton("Add")
        add_mode_pushButton.setToolTip('Enter new name in combo box')
        delete_mode_pushButton = QPushButton("Delete")

        self.labels["mode"] = mode_label
        self.widgets["mode"] = mode_comboBox
        self.widgets["add_mode"] = add_mode_pushButton
        self.widgets["delete_mode"] = delete_mode_pushButton

    def arange_layout(self):
        self.layout_main = QVBoxLayout()
        self.layout_main.addLayout(self.create_header_layout())
        self.layout_main.addLayout(self.create_data_layout())

        self.setLayout(self.layout_main)

    def create_header_layout(self):
        self.layouts['header'] = self.create_mode_layout()
        return self.layouts['header']

    def create_mode_layout(self):
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.labels["mode"])
        mode_layout.addWidget(self.widgets["mode"])
        mode_layout.addWidget(self.widgets["add_mode"])
        mode_layout.addWidget(self.widgets["delete_mode"])
        self.layouts["mode"] = mode_layout
        return mode_layout

    def connect_signals_from_mediator(self):
        super().connect_signals_from_mediator()
        self.mediator.mode_updated_sig.connect(self.update_mode_widget)

    def disconnect_signals_from_mediator(self):
        super().disconnect_signals_from_mediator()
        self.mediator.mode_updated_sig.disconnect()

    @Slot(str)
    def update_mode_widget(self, new_mode):
        """
        update the mode widget with the new mode from mediator.
        """
        self.setUpdatesEnabled(False)
        self.widgets["mode"].setCurrentText(new_mode)
        self.setUpdatesEnabled(True)

    def _add_button_pushed(self):
        self.setUpdatesEnabled(False)
        mode_to_add = self.current_mode
        self.widgets["mode"].addItem(mode_to_add)
        self.widgets["mode"].setCurrentText(mode_to_add)
        self.setUpdatesEnabled(True)
        self.add_mode_pushed_sig.emit(mode_to_add)

    def _delete_button_pushed(self):
        self.setUpdatesEnabled(False)
        mode_to_delete = self.current_mode
        if mode_to_delete != "default":
            # self.widgets["mode"].setCurrentItem(mode_to_add)
            self.widgets["mode"].removeItem(self.widgets["mode"].findText(mode_to_delete))
            self.setUpdatesEnabled(True)
            self.delete_mode_pushed_sig.emit(mode_to_delete)

    def connect_signals_from_widgets(self):
        self.widgets["mode"].currentIndexChanged.connect(lambda clicked : self.mediator.update_mode(self.current_mode))
        self.widgets["add_mode"].clicked.connect(self._add_button_pushed)
        self.widgets["delete_mode"].clicked.connect(self._delete_button_pushed)
        self.add_mode_pushed_sig.connect(self.mediator.add_mode)
        self.delete_mode_pushed_sig.connect(self.mediator.delete_mode)

    def disconnect_signals_from_widgets(self):
        self.widgets["mode"].currentIndexChanged.disconnect()
        self.widgets["add_mode"].clicked.disconnect()
        self.widgets["delete_mode"].clicked.disconnect()
        self.add_mode_pushed_sig.disconnect()
        self.delete_mode_pushed_sig.disconnect()
