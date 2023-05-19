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
    def __init__(self, parent=None, scanner_axes=None):
        super(TiltCorrectionDockWidget, self).__init__(parent)

        self._n_dim = len(scanner_axes)

        self.setWindowTitle("Tilt Correction")
        # Create the dock widget contents
        dock_widget_contents = QWidget()
        dock_widget_layout = QGridLayout(dock_widget_contents)
        # Create the widgets for tilt correction

        tiltpoint_label = QLabel("Support Vectors")
        dock_widget_layout.addWidget(tiltpoint_label, 0, 0)

        for idx, ax in enumerate(list(scanner_axes.keys())):
            tiltpoint_label = QLabel(ax)
            dock_widget_layout.addWidget(tiltpoint_label, 0, 1+idx)

        self.tilt_set_01_pushButton = QPushButton("Vec 1")
        self.tilt_set_01_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_01_pushButton, 1, 0)

        self.tilt_set_02_pushButton = QPushButton("Vec 2")
        self.tilt_set_02_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_02_pushButton, 2, 0)

        self.tilt_set_03_pushButton = QPushButton("Vec 3")
        self.tilt_set_03_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_03_pushButton, 3, 0)

        origin_switch_label = QLabel("Auto origin")
        dock_widget_layout.addWidget(origin_switch_label, 4, 0)
        self.auto_origin_switch = ToggleSwitchWidget(switch_states=('OFF', 'ON'))
        # todo: disconnect  self.auto_origin_switch.toggle_switch.sigStateChanged
        self.auto_origin_switch.toggle_switch.sigStateChanged.connect(self.auto_origin_changed,
                                                                    QtCore.Qt.QueuedConnection)
        self.auto_origin_switch.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        dock_widget_layout.addWidget(self.auto_origin_switch, 4, 1)

        self.tilt_set_04_pushButton = QPushButton("Origin")
        self.tilt_set_04_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(self.tilt_set_04_pushButton, 5, 0)

        self.support_vecs_box = []  # row: idx of support vecs (1-4), col: dimension (0-n)
        for idx_row in [1, 2, 3, 5]:
            pos_vecs = []
            for idx_dim in range(0, self._n_dim):
                x_i_position = ScienDSpinBox()
                x_i_position.setMinimumWidth(70)
                dock_widget_layout.addWidget(x_i_position, idx_row, idx_dim+1)
                x_i_position.setValue(np.nan)
                pos_vecs.append(x_i_position)
            self.support_vecs_box.append(pos_vecs)

        # Set the dock widget contents
        dock_widget_contents.setLayout(dock_widget_layout)
        self.setWidget(dock_widget_contents)

        # default init auto origin button = True
        self.auto_origin_switch.set_state('ON')
        self.auto_origin_changed(self.auto_origin_switch.switch_state)

    @property
    def support_vectors(self):
        support_vecs = self.support_vecs_box

        vec_vals = []
        for vec in support_vecs:
            vec_vals.append([box.value() for box in vec])

        return vec_vals

    @property
    def auto_origin(self):
        return True if self.auto_origin_switch.switch_state == 'ON' else False

    def auto_origin_changed(self, state):

        auto_enabled = True if state == 'ON' else False

        [el.setEnabled(not auto_enabled) for el in self.support_vecs_box[-1]]

        # nan renders the gui boxes invalid/red, so instead inf
        [el.setValue(np.inf) for el in self.support_vecs_box[-1]]
        if not auto_enabled:
            [el.setValue(np.nan) for el in self.support_vecs_box[-1]]

        self.tilt_set_04_pushButton.setEnabled(not auto_enabled)









