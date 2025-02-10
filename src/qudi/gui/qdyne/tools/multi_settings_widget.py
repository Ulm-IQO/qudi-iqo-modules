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
from PySide2.QtWidgets import QLabel, QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from qudi.gui.qdyne.tools.settings_widget import SettingsWidget


class MultiSettingsWidget(SettingsWidget):
    """Data widget class for multi settings widget.

        Methods could be different dataclasses.
        Modes are variants of parameters for each dataclass.
    """

    def __init__(self, mediator, dataclass_obj=None) -> None:
        """Initialize the dataclass widget with the corresponding mediator.

        Parameters
        ----------
        mediator : MultiSettingsMediator
            mediator class object to communicate with several methods and modes.
        dataclass_obj : dataclass
            dataclass object for creation of initial widgets.
            When None is passed, no widget is created. set_data should be called later.
        """
        super().__init__(mediator, dataclass_obj)

    @property
    def current_method(self):
        return self.widgets["method"].currentText()

    def reset_widgets(self, new_dataclass_obj):
        """Update the dataclass object used for widgets creation.
        """
        self.dataclass_obj = new_dataclass_obj
        print(f"dataclass_obj: {self.dataclass_obj}")
        self.create_data_widgets(self.dataclass_obj)
        self.update_data_container()

    def create_widgets(self):
        super().create_widgets()
        self.create_method_widgets()

    def create_method_widgets(self):
        method_label = QLabel()
        method_label.setText("Method")
        method_comboBox = QComboBox()
        method_comboBox.addItems(self.mediator.method_list)
        self.labels["method"] = method_label
        self.widgets["method"] = method_comboBox

    def arange_layout(self):
        """
        Arange layout for multi settings dataclass.
        When another method is selected, the layout should be updated
        by setting the new layout into the data_container widget.
        """
        self.layout_main = QVBoxLayout()
        self.layout_main.addLayout(self.create_header_layout())
        data_container = QWidget()
        data_container.setLayout(self.create_data_layout())
        self.layout_main.addWidget(data_container)

        self.layouts["data_container"] = data_container

        self.setLayout(self.layout_main)

    def create_header_layout(self):
        header_layout = QHBoxLayout()
        header_layout.addLayout(self.create_method_layout())
        header_layout.addLayout(self.create_mode_layout())
        self.layouts["header"] = header_layout
        return header_layout

    def create_method_layout(self):
        method_layout = QHBoxLayout()
        method_layout.addWidget(self.labels["method"])
        method_layout.addWidget(self.widgets["method"])

        self.layouts["method"] = method_layout
        return method_layout

    def update_data_container(self):
        data_container = self.layouts["data_container"]
        old_layout = data_container.layout()
        if old_layout is not None:
            temp = QWidget()
            temp.setLayout(old_layout)
        self._clear_layout(old_layout)

        new_layout = self.create_data_layout()
        data_container.setLayout(new_layout)
        data_container.update()
        self.layout_main.update()

    def connect_signals_from_mediator(self):
        super().connect_signals_from_mediator()
        self.mediator.method_updated_sig.connect(self.update_method_widget)
        self.mediator.data_renewed_sig.connect(self.reset_widgets)

    def disconnect_signals_from_mediator(self):
        super().disconnect_signals_from_mediator()
        self.mediator.method_updated_sig.disconnect()
        self.mediator.data_renewed_sig.disconnect()

    @Slot(str)
    def update_method_widget(self, new_method):
        """
        update the method widget with the new mode from mediator.
        """
        self.setUpdatesEnabled(False)
        self.widgets["method"].setCurrentText(new_method)
        self.reset_widgets(self.mediator.current_data)
        self.setUpdatesEnabled(True)

    def connect_signals_from_widgets(self):
        super().connect_signals_from_widgets()
        self.widgets["method"].currentIndexChanged.connect(
            lambda method: self.mediator.update_method(self.current_method))
        self.widgets["method"].currentIndexChanged[int].connect(
            lambda index: print("Index changed to:", index))

    def disconnect_signals_from_widgets(self):
        super().disconnect_signals_from_mediator()
        self.widgets["method"].currentIndexChanged.disconnect()