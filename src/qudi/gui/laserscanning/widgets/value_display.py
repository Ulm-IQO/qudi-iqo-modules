# -*- coding: utf-8 -*-
"""
Contains a QWidget for displaying the current laser value for the laser scanning toolchain GUI.

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

__all__ = ['LaserValueDisplayWidget']

import numpy as np
from typing import Optional
from PySide2 import QtCore, QtWidgets


class LaserValueDisplayWidget(QtWidgets.QWidget):
    """ Display widget for current laser value. Can switch between frequency and wavelength mode.
    """
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)
        self._is_frequency: bool = False
        # Create labels
        self._unit_label = QtWidgets.QLabel()
        self._unit_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        self._value_label = QtWidgets.QLabel()
        self._value_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        # Change font size
        font = self._unit_label.font()
        font.setPointSize(16)
        self._unit_label.setFont(font)
        self._value_label.setFont(font)
        # layout
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(self._value_label)
        layout.addWidget(self._unit_label)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setStretch(0, 1)
        self.setLayout(layout)
        # Set default values
        self.toggle_is_frequency(self._is_frequency)
        self.set_value(float('nan'))

    def set_value(self, value: float) -> None:
        if np.isfinite(value):
            if self._is_frequency:
                self._value_label.setText(f'{value / 1e12:.9f}')
            else:
                self._value_label.setText(f'{value * 1e9:.6f}')
        else:
            self._value_label.setText('NaN')

    def toggle_is_frequency(self, is_frequency: bool) -> None:
        if is_frequency:
            self._unit_label.setText('THz')
        else:
            self._unit_label.setText('nm')
        self._is_frequency = is_frequency
