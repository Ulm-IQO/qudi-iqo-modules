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
from PySide2 import QtWidgets, QtCore

from qudi.util import uic
from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget

class StateEstimationWidget(QtWidgets.QWidget):
    def __init__(self):
        self.estimator = None
        self.settings = None
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\state_estimation_widget.ui')

        # Load it
        super(StateEstimationWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self, estimator, settings):
        self.estimator = estimator
        self.settings = settings
        self._activate_widgets()

    def _activate_widgets(self):
        self.se_method_comboBox.addItems(self.estimator.method_lists)
        self.se_method_comboBox.setCurrentText(self.settings.current_method)
        self.se_setting_comboBox.addItems(self.settings.current_setting_list)
        self.se_setting_comboBox.setCurrentText(self.settings.current_stg_name)

        self.se_settings_widget = DataclassWidget(self.settings.current_setting)
        self.se_settings_gridLayout.addWidget(self.se_settings_widget)

    def deactivate(self):
        pass

    def connect_signals(self):
        self.se_method_comboBox.currentTextChanged.connect(self.update_current_method)
        self.se_setting_comboBox.currentTextChanged.connect(self.update_current_setting)
        self.se_setting_add_pushButton.clicked.connect(self.add_setting)
        self.se_setting_delete_pushButton.clicked.connect(self.delete_setting)

    def disconnect_signals(self):
        self.se_method_comboBox.currentTextChanged.disconnect()
        self.se_setting_comboBox.currentTextChanged.disconnect()
        self.se_setting_add_pushButton.clicked.disconnect()
        self.se_setting_delete_pushButton.clicked.disconnect()

    def update_current_method(self):
        self.settings.current_method = self.se_method_comboBox.currentText()

    def update_current_setting(self):
        self.settings.current_stg_name = self.se_setting_comboBox.currentText()
        current_setting = copy.deepcopy(self.settings.current_setting)
        self.se_settings_widget.update_data(current_setting)

    def add_setting(self):
        self.settings.add_setting()
        self.se_setting_comboBox.addItem(self.settings.current_stg_name)
        self.se_setting_comboBox.setCurrentText(self.settings.current_stg_name)

    def delete_setting(self):
        self.settings.current_stg_name = self.se_setting_comboBox.currentText()
        current_index = self.se_setting_comboBox.currentIndex()
        self.settings.remove_setting()
        self.se_setting_comboBox.removeItem(current_index)
