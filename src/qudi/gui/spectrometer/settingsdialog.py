# -*- coding: utf-8 -*-

"""
This file contains a settings dialog for the qudi main GUI.

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
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from qudi.util.widgets.toggle_switch import ToggleSwitch


class SettingsDialog(QtWidgets.QDialog):
    """
    Custom QDialog widget for configuration of the spectrometer
    """

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.setWindowTitle('Spectrometer settings')

        # Create main layout
        # Add widgets to layout and set as main layout
        layout = QtWidgets.QGridLayout()
        layout.setRowStretch(1, 1)
        self.setLayout(layout)

        # Create widgets and add them to the layout
        self.delete_fit = ToggleSwitch(state_names=('False', 'True'))
        self.delete_fit.setMinimumWidth(150)
        delete_fit_label = QtWidgets.QLabel('Delete Fit:')
        delete_fit_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(delete_fit_label, 0, 0)
        layout.addWidget(self.delete_fit, 0, 1)

        self.max_repetitions_spinbox = ScienSpinBox()
        self.max_repetitions_spinbox.setMinimumWidth(150)
        self.max_repetitions_spinbox.setMinimum(0)
        max_repetitions_label = QtWidgets.QLabel('Maximum Repetitions:')
        max_repetitions_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(max_repetitions_label, 1, 0)
        layout.addWidget(self.max_repetitions_spinbox, 1, 1)

        self.exposure_time_spinbox = ScienDSpinBox()
        self.exposure_time_spinbox.setMinimumWidth(150)
        exposure_time_label = QtWidgets.QLabel('Exposure Time:')
        exposure_time_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(exposure_time_label, 2, 0)
        layout.addWidget(self.exposure_time_spinbox, 2, 1)

        buttonbox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                               | QtWidgets.QDialogButtonBox.Cancel
                                               | QtWidgets.QDialogButtonBox.Apply)
        buttonbox.setOrientation(QtCore.Qt.Horizontal)
        layout.addWidget(buttonbox, 3, 0, 1, 2)

        # Add internal signals
        buttonbox.accepted.connect(self.accept)
        buttonbox.rejected.connect(self.reject)
        buttonbox.button(buttonbox.Apply).clicked.connect(self.accepted)
