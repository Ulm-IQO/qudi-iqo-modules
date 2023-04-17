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


from PySide2.QtWidgets import QDockWidget, QWidget,QGridLayout, QLabel, QPushButton,QTableWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
#from qudi.interface.scanning_probe_interface import ScanData
#from qudi.gui.scanning.scan_dockwidget import ScanDockWidget

class TiltCorrectionDockWidget(QDockWidget):
    def __init__(self, parent=None):
        super(TiltCorrectionDockWidget, self).__init__(parent)
        self.setWindowTitle("Tilt Correction")
        # Create the dock widget contents
        dock_widget_contents = QWidget()
        dock_widget_layout = QGridLayout(dock_widget_contents)
        # Create the widgets for tilt correction

        tiltpoint_label = QLabel("Set Tiltpoint")
        dock_widget_layout.addWidget(tiltpoint_label,0,0)
        tiltpoint_label = QLabel("X")
        dock_widget_layout.addWidget(tiltpoint_label,0,1)
        tiltpoint_label = QLabel("Y")
        dock_widget_layout.addWidget(tiltpoint_label, 0, 2)
        tiltpoint_label = QLabel("Z")
        dock_widget_layout.addWidget(tiltpoint_label, 0, 3)
        tilt_set_01_pushButton = QPushButton("01")
        tilt_set_01_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(tilt_set_01_pushButton,1,0)

        tilt_set_02_pushButton = QPushButton("02")
        tilt_set_02_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(tilt_set_02_pushButton,2,0)

        tilt_set_03_pushButton = QPushButton("03")
        tilt_set_03_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(tilt_set_03_pushButton,3,0)

        tilt_set_03_pushButton = QPushButton("04")
        tilt_set_03_pushButton.setMaximumSize(70, 16777215)
        dock_widget_layout.addWidget(tilt_set_03_pushButton, 4, 0)

        x0_position = ScienDSpinBox()
        dock_widget_layout.addWidget(x0_position, 1, 1)
        x1_position = ScienDSpinBox()
        dock_widget_layout.addWidget(x1_position, 2, 1)
        x2_position = ScienDSpinBox()
        dock_widget_layout.addWidget(x2_position, 3, 1)
        x3_position = ScienDSpinBox()
        dock_widget_layout.addWidget(x3_position, 4, 1)

        y0_position = ScienDSpinBox()
        dock_widget_layout.addWidget(y0_position, 1, 2)
        y1_position = ScienDSpinBox()
        dock_widget_layout.addWidget(y1_position, 2, 2)
        y2_position = ScienDSpinBox()
        dock_widget_layout.addWidget(y2_position, 3, 2)
        y3_position = ScienDSpinBox()
        dock_widget_layout.addWidget(y3_position, 4, 2)

        z0_position = ScienDSpinBox()
        dock_widget_layout.addWidget(z0_position, 1, 3)
        z1_position = ScienDSpinBox()
        dock_widget_layout.addWidget(z1_position, 2, 3)
        z2_position = ScienDSpinBox()
        dock_widget_layout.addWidget(z2_position, 3, 3)
        z3_position = ScienDSpinBox()
        dock_widget_layout.addWidget(z3_position, 4, 3)


        calc_tilt_pushButton = QPushButton("Calc. Tilt")
        dock_widget_layout.addWidget(calc_tilt_pushButton)

        # Set the dock widget contents

        dock_widget_contents.setLayout(dock_widget_layout)
        self.setWidget(dock_widget_contents)

