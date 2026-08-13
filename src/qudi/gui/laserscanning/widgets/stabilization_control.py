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
from scipy.constants import speed_of_light as _SPEED_OF_LIGHT

from qudi.util.constraints import ScalarConstraint
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class LaserStabilizationWidget(QtWidgets.QWidget):
    """ Control widget for laser stabilization """

    target_spinbox: ScienDSpinBox
    stabilize_button: QtWidgets.QPushButton

    sigStabilizeLaser = QtCore.Signal(float)  # laser target value
    sigStopStabilize = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._is_frequency = False

        self.target_spinbox = ScienDSpinBox()
        self.target_spinbox.setMinimumWidth(130)
        self.target_spinbox.setDecimals(9)

        # display defaults (nm)
        self.target_spinbox.setRange(1.0, 2000.0)  # nm (display)
        self.target_spinbox.setSuffix('nm')
        self.target_spinbox.setValue(780.0)

        self.stabilize_button = QtWidgets.QPushButton('Stabilize')
        self.stabilize_button.setCheckable(True)
        self.stabilize_button.toggled.connect(self._stabilize_toggled)
        label = QtWidgets.QLabel('Target:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(label)
        layout.addWidget(self.target_spinbox)
        layout.addWidget(self.stabilize_button)
        layout.setStretch(1, 1)
        self.setLayout(layout)

    def toggle_is_frequency(self, is_frequency: bool) -> None:
        """Switch display between nm and THz, but always emit SI (m/Hz)."""
        if self._is_frequency == bool(is_frequency):
            return

        # convert current displayed value to the new display domain
        cur_display = float(self.target_spinbox.value())
        if is_frequency:
            # nm -> THz
            cur_m = cur_display * 1e-9
            cur_hz = (_SPEED_OF_LIGHT / cur_m)
            cur_thz = cur_hz * 1e-12
            self.target_spinbox.setRange(0.01, 2000.0)  # THz (display)
            self.target_spinbox.setSuffix('THz')
            self.target_spinbox.setValue(cur_thz)
        else:
            # THz -> nm
            cur_hz = cur_display * 1e12
            cur_m = (_SPEED_OF_LIGHT / cur_hz)
            cur_nm = cur_m * 1e9
            self.target_spinbox.setRange(1.0, 2000.0)  # nm (display)
            self.target_spinbox.setSuffix('nm')
            self.target_spinbox.setValue(cur_nm)

        self._is_frequency = bool(is_frequency)

    def get_target_si(self) -> float:
        """Return target in SI units: Hz if frequency mode else meters."""
        v = float(self.target_spinbox.value())
        if self._is_frequency:
            return v * 1e12  # THz -> Hz
        return v * 1e-9      # nm -> m

    def set_target(self, value_si: float) -> None:
        """
        Update spinbox from SI value emitted by logic:
        - if _is_frequency: value_si is Hz -> display THz
        - else: value_si is m -> display nm
        """
        if self._is_frequency:
            self.target_spinbox.setValue(float(value_si) * 1e-12)  # Hz -> THz
        else:
            self.target_spinbox.setValue(float(value_si) * 1e9)  # m -> nm

    def set_stabilizing(self, enabled: bool) -> None:
        """Update button state from logic (avoid double-click weirdness)."""
        self.stabilize_button.blockSignals(True)
        self.stabilize_button.setChecked(bool(enabled))
        self.stabilize_button.blockSignals(False)
        self.stabilize_button.setText('Stop' if enabled else 'Stabilize')

    def _stabilize_toggled(self, checked: bool) -> None:
        if checked:
            self.stabilize_button.setText('Stop')
            self.sigStabilizeLaser.emit(self.get_target_si())
        else:
            self.stabilize_button.setText('Stabilize')
            self.sigStopStabilize.emit()


class LaserStabilizationDockWidget(QtWidgets.QDockWidget):
    """ Dockwidget for LaserStabilizationWidget """

    control_widget: LaserStabilizationWidget

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_widget = LaserStabilizationWidget()
        self.setWidget(self.control_widget)
        self.control_widget.setFixedHeight(self.control_widget.sizeHint().height())

        self.sigStabilizeLaser = self.control_widget.sigStabilizeLaser
        self.sigStopStabilize = self.control_widget.sigStopStabilize
        self.toggle_is_frequency = self.control_widget.toggle_is_frequency
        self.set_stabilizing = self.control_widget.set_stabilizing
        self.set_target = self.control_widget.set_target
