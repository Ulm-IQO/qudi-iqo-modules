# -*- coding: utf-8 -*-
"""
Contains a QWidget for controlling the stabilization for the laser scanning toolchain GUI.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['LaserStabilizationWidget', 'LaserStabilizationDockWidget']

from PySide2 import QtCore, QtWidgets

from qudi.util.constraints import ScalarConstraint
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class LaserStabilizationWidget(QtWidgets.QWidget):
    """ Control widget for laser stabilization """

    target_spinbox: ScienDSpinBox
    stabilize_button: QtWidgets.QPushButton

    sigStabilizeLaser = QtCore.Signal(float)  # laser target value

    def __init__(self, *args, unit: str, constraint: ScalarConstraint, **kwargs):
        super().__init__(*args, **kwargs)

        self.target_spinbox = ScienDSpinBox()
        self.target_spinbox.setMinimumWidth(100)
        self.target_spinbox.setSuffix(unit)
        self.target_spinbox.setRange(*constraint.bounds)
        self.target_spinbox.setValue(constraint.default)
        self.stabilize_button = QtWidgets.QPushButton('Stabilize')
        self.stabilize_button.setCheckable(False)
        self.stabilize_button.clicked.connect(self.__stabilize_clicked)
        label = QtWidgets.QLabel('Target:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(label)
        layout.addWidget(self.target_spinbox)
        layout.addWidget(self.stabilize_button)
        layout.setStretch(1, 1)
        self.setLayout(layout)

    def get_target(self) -> float:
        return self.target_spinbox.value()

    def set_target(self, value: float) -> None:
        self.target_spinbox.setValue(value)

    def __stabilize_clicked(self) -> None:
        self.sigStabilizeLaser.emit(self.get_target())


class LaserStabilizationDockWidget(QtWidgets.QDockWidget):
    """ Dockwidget for LaserStabilizationWidget """

    control_widget: LaserStabilizationWidget

    def __init__(self, *args, unit: str, constraint: ScalarConstraint, **kwargs):
        super().__init__(*args, **kwargs)

        self.control_widget = LaserStabilizationWidget(unit=unit, constraint=constraint)
        self.setWidget(self.control_widget)
        self.control_widget.setFixedHeight(self.control_widget.sizeHint().height())
        self.set_target = self.control_widget.set_target
        self.get_target = self.control_widget.get_target
        self.sigStabilizeLaser = self.control_widget.sigStabilizeLaser
