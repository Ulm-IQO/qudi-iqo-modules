# -*- coding: utf-8 -*-
"""
Contains data fit widgets for the laser scanning toolchain GUI.

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

__all__ = ['FitControl', 'FitControlDockWidget']

from PySide2 import QtCore, QtWidgets

from qudi.util.datafitting import FitContainer
from qudi.util.widgets.fitting import FitWidget as _FitWidget


class FitControl(QtWidgets.QWidget):
    """ Fit control widget for the laser scanning toolchain GUI """

    fit_envelope_checkbox: QtWidgets.QCheckBox
    fit_widget: _FitWidget

    sigDoFit = QtCore.Signal(str, bool)  # fit_config, fit_envelope

    def __init__(self, *args, fit_container: FitContainer, **kwargs):
        super().__init__(*args, **kwargs)

        self.fit_envelope_checkbox = QtWidgets.QCheckBox('Fit Envelope')
        self.fit_envelope_checkbox.setToolTip('Instead of the mean histogram data, will fit the '
                                              'envelope histogram if checked.')
        self.fit_widget = _FitWidget(fit_container=fit_container)
        self.fit_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.fit_widget.sigDoFit.connect(self._fit_clicked)
        self.update_fit_result = self.fit_widget.update_fit_result
        # layout
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.fit_envelope_checkbox)
        layout.addWidget(self.fit_widget)
        layout.setStretch(1, 1)
        self.setLayout(layout)

    def toggle_fit_envelope(self, enable: bool) -> None:
        self.fit_envelope_checkbox.setChecked(enable)

    def _fit_clicked(self, config: str) -> None:
        self.sigDoFit.emit(config, self.fit_envelope_checkbox.isChecked())


class FitControlDockWidget(QtWidgets.QDockWidget):
    """ QDockWidget for FitControl """

    fit_control: FitControl

    def __init__(self, *args, fit_container: FitContainer, **kwargs):
        super().__init__(*args, **kwargs)

        self.fit_control = FitControl(fit_container=fit_container)
        self.setWidget(self.fit_control)
        self.sigDoFit = self.fit_control.sigDoFit
        self.toggle_fit_envelope = self.fit_control.toggle_fit_envelope
        self.update_fit_result = self.fit_control.update_fit_result
