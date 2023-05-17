# -*- coding: utf-8 -*-

"""
This file contains a custom QWidget class to provide Tilt correction parameters and to calculate the Tilt.

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

__all__ = ('TiltCorrectionDockWidget')

import numpy as np

from PySide2 import QtCore, QtGui, QtWidgets
from PySide2.QtWidgets import QDockWidget, QWidget,QGridLayout, QLabel, QPushButton,QTableWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.gui.switch.switch_state_widgets import SwitchRadioButtonWidget, ToggleSwitchWidget
#from qudi.interface.scanning_probe_interface import ScanData
#from qudi.gui.scanning.scan_dockwidget import ScanDockWidget

class TiltCorrectionDockWidget(QDockWidget):
    def __init__(self, parent=None, n_dim=3):
        super(TiltCorrectionDockWidget, self).__init__(parent)

        self._n_dim = n_dim

        self.setWindowTitle("Tilt Correction")
        # Create the dock widget contents
        dock_widget_contents = QWidget()
        dock_widget_layout = QGridLayout(dock_widget_contents)
        # Create the widgets for tilt correction

        tiltpoint_label = QLabel("Support Vectors")
        dock_widget_layout.addWidget(tiltpoint_label,0,0)
        tiltpoint_label = QLabel("X")
        dock_widget_layout.addWidget(tiltpoint_label,0,1)
        tiltpoint_label = QLabel("Y")
        dock_widget_layout.addWidget(tiltpoint_label, 0, 2)
        tiltpoint_label = QLabel("Z")
        dock_widget_layout.addWidget(tiltpoint_label, 0, 3)
        self.tilt_set_01_pushButton = QPushButton("Vec 1")
        self.tilt_set_01_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_01_pushButton,1,0)

        self.tilt_set_02_pushButton = QPushButton("Vec 2")
        self.tilt_set_02_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_02_pushButton,2,0)

        self.tilt_set_03_pushButton = QPushButton("Vec 3")
        self.tilt_set_03_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_03_pushButton,3,0)

        origin_switch_label = QLabel("Auto origin")
        dock_widget_layout.addWidget(origin_switch_label, 4, 0)
        toggle_switch_widget = ToggleSwitchWidget(switch_states=('OFF', 'ON'))
        # Set size policy for the ToggleSwitchWidget
        toggle_switch_widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        dock_widget_layout.addWidget(toggle_switch_widget,4,1)

        self.tilt_set_04_pushButton = QPushButton("Origin")
        self.tilt_set_04_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_04_pushButton, 5, 0)

        self.support_vecs_box = []  # row: idx of support vecs (1-4), col: dimension (0-n)
        for idx_row in [1, 2, 3, 5]:
            pos_vecs = []
            for idx_dim in range(0, n_dim):
                x_i_position = ScienDSpinBox()
                dock_widget_layout.addWidget(x_i_position, idx_row, idx_dim+1)
                x_i_position.setValue(np.nan)
                pos_vecs.append(x_i_position)
            self.support_vecs_box.append(pos_vecs)




        #calc_tilt_pushButton = QPushButton("Calc. Tilt")
        #dock_widget_layout.addWidget(calc_tilt_pushButton)

        # Set the dock widget contents

        dock_widget_contents.setLayout(dock_widget_layout)
        self.setWidget(dock_widget_contents)

    @property
    def support_vectors(self):
        support_vecs = self.support_vecs_box

        vec_vals = []
        for vec in support_vecs:
            vec_vals.append([box.value() for box in vec])

        return vec_vals
        """
        dim_idxs = list(range(self._n_dim))

        all_vecs_valid = True
        for vec in [0, 1, 2, 3]:
            vecs_valid = [support_vecs[vec][dim].is_valid for dim in dim_idxs]
            all_vecs_valid = np.all(vecs_valid) and all_vecs_valid

        """



