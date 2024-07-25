# -*- coding: utf-8 -*-

"""
This file contains the GUI for qdyne measurements.

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

import copy
import os
import numpy as np
import pyqtgraph as pg
from PySide2 import QtWidgets, QtCore

from qudi.core.logger import get_logger
from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget


class SettingsWidget(QtWidgets.QWidget):
    _log = get_logger(__name__)
    method_updated_sig = QtCore.Signal()
    setting_name_updated_sig = QtCore.Signal()
    setting_widget_updated_sig = QtCore.Signal()
    add_button_pushed_sig = QtCore.Signal(str)
    remove_setting_sig = QtCore.Signal(str)

    def __init__(self, settings, method_list):
        self.settings = settings
        self.method_list = method_list
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, "ui", "settings_widget.ui")

        # Load it
        super(SettingsWidget, self).__init__()
        uic.loadUi(ui_file, self)

    def activate(self):
        self.method_comboBox.addItems(self.method_list)
        self.method_comboBox.setCurrentText(self.settings.current_method)
        self.setting_comboBox.addItems(self.settings.current_setting_list)
        self.setting_comboBox.setCurrentText(self.settings.current_stg_name)
        self.setting_comboBox.setEditable(True)
        self.setting_add_pushButton.setToolTip('Enter new name in combo box')
        
        self.settings_widget = DataclassWidget(self.settings.current_setting)
        self.setting_gridLayout.addWidget(self.settings_widget)

    def deactivate(self):
        pass

    def connect_signals(self):
        self.method_comboBox.currentTextChanged.connect(self.update_current_method)
        self.setting_comboBox.currentIndexChanged.connect(self.update_current_setting)
        self.setting_add_pushButton.clicked.connect(self.add_setting)
        self.setting_delete_pushButton.clicked.connect(self.delete_setting)
        self.add_button_pushed_sig.connect(self.settings.add_setting)
        self.remove_setting_sig.connect(self.settings.remove_setting)

    def disconnect_signals(self):
        self.method_comboBox.currentTextChanged.disconnect()
        self.setting_comboBox.currentTextChanged.disconnect()
        self.setting_add_pushButton.clicked.disconnect()
        self.setting_delete_pushButton.clicked.disconnect()

    def update_current_method(self):
        self.settings.current_method = self.method_comboBox.currentText()
        self.setting_comboBox.blockSignals(True)
        self.setting_comboBox.clear()
        self.setting_comboBox.blockSignals(False)
        self.setting_comboBox.addItems(self.settings.current_setting_list)
        self.settings.current_stg_name = "default"
        self.setting_comboBox.setCurrentText(self.settings.current_stg_name)

    def update_current_setting(self):
        self.settings.current_stg_name = self.setting_comboBox.currentText()
        self.update_widget()
        self.setting_name_updated_sig.emit()

    def update_widget(self):
        self.settings_widget.update_data(self.settings.current_setting)
        self.setting_widget_updated_sig.emit()

    def add_setting(self):
        new_name = self.setting_comboBox.currentText()
        if new_name in self.settings.current_setting_list:
            self._log.error("Setting name already exists")
        else:
            self.add_button_pushed_sig.emit(new_name)
            self.setting_comboBox.addItem(self.settings.current_stg_name)
            self.setting_comboBox.setCurrentText(self.settings.current_stg_name)
            self.update_widget()

    def delete_setting(self):
        stg_name_to_remove = self.setting_comboBox.currentText()
    
        if stg_name_to_remove == "default":
            self._log.error("Cannot delete default setting")
        else:
            index_to_remove = self.setting_comboBox.findText(stg_name_to_remove)
            next_index = int(index_to_remove - 1)
            self.setting_comboBox.setCurrentIndex(next_index)
            self.settings.current_stg_name = self.setting_comboBox.currentText()
            self.setting_comboBox.removeItem(index_to_remove)
            self.remove_setting_sig.emit(stg_name_to_remove)
