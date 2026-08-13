# -*- coding: utf-8 -*-
"""
Contains stabilization configuration widgets for the laser scanning toolchain GUI.

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

from PySide2 import QtCore, QtWidgets
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class StabilizationConfigDialog(QtWidgets.QDialog):
    sigConfigChanged = QtCore.Signal(float, float, int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle('Stabilization Configuration')

        self.step_spin = ScienDSpinBox()
        self.step_spin.setRange(1e-9, 10.0)
        self.step_spin.setDecimals(9)
        self.step_spin.setSuffix(' V')
        self.step_spin.setValue(1e-4)

        self.tol_spin = ScienDSpinBox()
        self.tol_spin.setRange(1e-18, 1.0)
        self.tol_spin.setDecimals(12)
        self.tol_spin.setSuffix(' m')
        self.tol_spin.setValue(3e-13)

        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(10, 60000)
        self.interval_spin.setSuffix(' ms')
        self.interval_spin.setValue(200)

        self.max_steps_spin = QtWidgets.QSpinBox()
        self.max_steps_spin.setRange(1, 1_000_000)
        self.max_steps_spin.setValue(200)

        self.invert_check = QtWidgets.QCheckBox('Invert polarity')
        self.invert_check.setChecked(False)

        form = QtWidgets.QFormLayout()
        form.addRow('Step:', self.step_spin)
        form.addRow('Tolerance:', self.tol_spin)
        form.addRow('Interval:', self.interval_spin)
        form.addRow('Max steps:', self.max_steps_spin)
        form.addRow('', self.invert_check)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Apply |
            QtWidgets.QDialogButtonBox.Close
        )
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._emit_config)
        buttons.rejected.connect(self.close)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def _emit_config(self):
        self.sigConfigChanged.emit(
            float(self.step_spin.value()),
            float(self.tol_spin.value()),
            int(self.interval_spin.value()),
            int(self.max_steps_spin.value()),
            bool(self.invert_check.isChecked())
        )

    def update_from_dict(self, cfg: dict):
        self.step_spin.setValue(float(cfg.get('step', 1e-4)))
        self.tol_spin.setValue(float(cfg.get('tolerance', 3e-13)))
        self.interval_spin.setValue(int(cfg.get('interval_ms', 200)))
        self.max_steps_spin.setValue(int(cfg.get('max_steps', 200)))
        self.invert_check.setChecked(bool(cfg.get('invert', False)))