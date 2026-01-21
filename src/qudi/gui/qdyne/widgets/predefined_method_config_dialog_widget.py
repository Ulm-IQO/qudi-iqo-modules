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
from PySide2 import QtCore, QtWidgets

from qudi.util import uic
from qudi.util.helpers import natural_sort

class PredefinedMethodsConfigDialogWidget(QtWidgets.QDialog):
    def __init__(self, gui):
        self._gui = gui
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, 'ui', 'predefined_methods_config.ui')

        # Load it
        super().__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        """ Initialize, connect and configure the pulse generator settings to be displayed in the
        editor.
        """
        # create all GUI elements and check all boxes listed in the methods to show
        for method_name in natural_sort(self._gui.logic().pulsedmasterlogic().generate_methods):
            # create checkboxes for the config dialogue
            name_checkbox = 'checkbox_' + method_name
            setattr(self, name_checkbox, QtWidgets.QCheckBox(self.scrollArea))
            checkbox = getattr(self, name_checkbox)
            checkbox.setObjectName(name_checkbox)
            checkbox.setText(method_name)
            checkbox.setChecked(method_name in self._gui._predefined_methods_to_show)
            self.verticalLayout.addWidget(checkbox)

        # apply the chosen methods to the methods dialogue
        self.apply_predefined_methods_config()
        return

    def deactivate(self):
        self.close()

    def connect_signals(self):
        # Connect signals used in predefined methods config dialog
        self.accepted.connect(self.apply_predefined_methods_config)
        self.rejected.connect(self.keep_former_predefined_methods_config)
        self.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply_predefined_methods_config)

    def disconnect_signals(self):
        self.accepted.disconnect()
        self.rejected.disconnect()
        self.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.disconnect()

    def show_predefined_methods_config(self):
        """ Opens the Window for the config of predefined methods."""
        self.show()
        self.raise_()
        return

    def keep_former_predefined_methods_config(self):
        for method_name in self._gui.logic().pulsedmasterlogic().generate_methods:
            groupbox = getattr(self._gui._gw, method_name + '_GroupBox')
            checkbox = getattr(self, 'checkbox_' + method_name)
            checkbox.setChecked(groupbox.isVisible())
        return

    def apply_predefined_methods_config(self):
        self._gui._predefined_methods_to_show = list()
        for method_name in self._gui.logic().pulsedmasterlogic().generate_methods:
            groupbox = getattr(self._gui._gw, method_name + '_GroupBox')
            checkbox = getattr(self, 'checkbox_' + method_name)
            groupbox.setVisible(checkbox.isChecked())
            if checkbox.isChecked():
                self._gui._predefined_methods_to_show.append(method_name)

        self._gui._gw.hintLabel.setVisible(len(self._gui._predefined_methods_to_show) == 0)
        return
