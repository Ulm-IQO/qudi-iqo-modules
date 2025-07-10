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
import os
from PySide2 import QtWidgets, QtCore

from qudi.util import uic

class QdyneMainWindow(QtWidgets.QMainWindow):
    sigSaveData = QtCore.Signal(str)
    sigLoadData = QtCore.Signal(str, str, str)
    def __init__(self, gui, logic, log):
        self._gui = gui
        self._logic = logic()
        self._log = log
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, 'ui', 'maingui.ui')
        # Load it
        super(QdyneMainWindow, self).__init__()
        uic.loadUi(ui_file, self)

    def activate(self):
        self._setup_toolbar()
        pass

    def deactivate(self):
        self.close()

    def _setup_toolbar(self):
        self.data_type_ComboBox = QtWidgets.QComboBox()
        self.data_type_ComboBox.addItems(self._logic.data_manager.save_data_types)
        self.save_tag_LineEdit = QtWidgets.QLineEdit()
        self.save_tag_LineEdit.setPlaceholderText('save tag')
        self.save_tag_LineEdit.setToolTip('input save tag')
        self.data_index_LineEdit = QtWidgets.QLineEdit()
        self.data_index_LineEdit.setPlaceholderText('data index')
        self.data_index_LineEdit.setToolTip('input data index if needed')

        self.data_manager_ToolBar.addWidget(self.data_type_ComboBox)
        self.data_manager_ToolBar.addWidget(self.save_tag_LineEdit)
        self.data_manager_ToolBar.addWidget(self.data_index_LineEdit)

    def connect_signals(self):
        self.action_Predefined_Methods_Config.triggered.connect(self._gui._gsw.show_predefined_methods_config)
        self.action_FitSettings.triggered.connect(self._gui._fcd.show)

        self.action_load_data.triggered.connect(self.load_clicked)
        self.action_save_data.triggered.connect(self.save_clicked)

        self.sigSaveData.connect(self._logic.save_data)
        self.sigLoadData.connect(self._logic.load_data)

    def disconnect_signals(self):
        self.action_Predefined_Methods_Config.triggered.disconnect()
        self.action_FitSettings.triggered.disconnect()

        self.action_load_data.triggered.disconnect()
        self.action_save_data.triggered.disconnect()

        self.sigSaveData.disconnect()
        self.sigLoadData.disconnect()

    def save_clicked(self):
        data_type = self.data_type_ComboBox.currentText()
        name_tag = self.save_tag_LineEdit.text()
        self._logic.settings.data_manager_stg.set_nametag_all(name_tag)
        self.sigSaveData.emit(data_type)

    def load_clicked(self):
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open data file', self._logic.module_default_data_dir)
        data_type = self.data_type_ComboBox.currentText()
        data_index = self.data_index_LineEdit.text()
        self.sigLoadData.emit(data_type, file_name, data_index)







