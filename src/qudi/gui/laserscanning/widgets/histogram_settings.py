# -*- coding: utf-8 -*-
"""
Contains histogram settings widgets for the laser scanning toolchain GUI.

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

__all__ = ['HistogramSettingsWidget', 'HistogramSettingsDockWidget']

import numpy as np
from typing import Tuple, Optional
from PySide2 import QtCore, QtWidgets

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class HistogramSettingsWidget(QtWidgets.QWidget):
    """ """

    sigSettingsChanged = QtCore.Signal(tuple, int)

    bins_spinbox: QtWidgets.QSpinBox
    min_spinbox: ScienDSpinBox
    max_spinbox: ScienDSpinBox

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        # labels
        bin_label = QtWidgets.QLabel('Bins:')
        bin_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        min_label = QtWidgets.QLabel('Minimum:')
        min_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        max_label = QtWidgets.QLabel('Maximum:')
        max_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        # spin boxes
        self.bins_spinbox = QtWidgets.QSpinBox()
        self.bins_spinbox.setMinimumWidth(100)
        self.bins_spinbox.setRange(3, 10000)
        self.bins_spinbox.setValue(200)
        self.min_spinbox = ScienDSpinBox()
        self.min_spinbox.setMinimumWidth(120)
        self.min_spinbox.setDecimals(7, dynamic_precision=False)
        self.min_spinbox.setRange(1e-9, np.inf)
        self.min_spinbox.setValue(550e-9)
        self.min_spinbox.setSuffix('m')
        self.max_spinbox = ScienDSpinBox()
        self.max_spinbox.setMinimumWidth(120)
        self.max_spinbox.setDecimals(7, dynamic_precision=False)
        self.max_spinbox.setRange(1e-9, np.inf)
        self.max_spinbox.setValue(750e-9)
        self.max_spinbox.setSuffix('m')
        # layout
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(bin_label)
        layout.addWidget(self.bins_spinbox)
        layout.addWidget(min_label)
        layout.addWidget(self.min_spinbox)
        layout.addWidget(max_label)
        layout.addWidget(self.max_spinbox)
        layout.setStretch(0, 1)
        layout.setStretch(2, 1)
        layout.setStretch(4, 1)
        self.setLayout(layout)
        # Connect signals
        self.min_spinbox.editingFinished.connect(self.__emit_changes)
        self.max_spinbox.editingFinished.connect(self.__emit_changes)
        self.bins_spinbox.editingFinished.connect(self.__emit_changes)

    def get_settings(self) -> Tuple[Tuple[float, float], int]:
        """ """
        span = [self.min_spinbox.value(), self.max_spinbox.value()]
        return (min(span), max(span)), self.bins_spinbox.value()

    def update_settings(self, span: Tuple[float, float], bins: int) -> None:
        """ """
        self.min_spinbox.blockSignals(True)
        self.max_spinbox.blockSignals(True)
        self.bins_spinbox.blockSignals(True)
        self.min_spinbox.setValue(min(span))
        self.max_spinbox.setValue(max(span))
        self.bins_spinbox.setValue(bins)
        self.min_spinbox.blockSignals(False)
        self.max_spinbox.blockSignals(False)
        self.bins_spinbox.blockSignals(False)
        self.__emit_changes()

    def toggle_unit(self, is_frequency: bool) -> None:
        if is_frequency:
            self.min_spinbox.setSuffix('Hz')
            self.max_spinbox.setSuffix('Hz')
        else:
            self.min_spinbox.setSuffix('m')
            self.max_spinbox.setSuffix('m')

    def __emit_changes(self) -> None:
        self.sigSettingsChanged.emit(*self.get_settings())


class HistogramSettingsDockWidget(QtWidgets.QDockWidget):
    """ A QDockWidget for HistogramSettingsWidget """

    settings_widget: HistogramSettingsWidget

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.settings_widget = HistogramSettingsWidget()
        self.setWidget(self.settings_widget)
        self.settings_widget.setFixedHeight(self.settings_widget.sizeHint().height())
        self.sigSettingsChanged = self.settings_widget.sigSettingsChanged
        self.get_settings = self.settings_widget.get_settings
        self.update_settings = self.settings_widget.update_settings
        self.toggle_unit = self.settings_widget.toggle_unit
