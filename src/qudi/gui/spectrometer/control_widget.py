# -*- coding: utf-8 -*-
"""
This module contains the spectrometer control widget for SpectrometerGui.

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

__all__ = ['SpectrometerControlWidget']

from PySide2 import QtCore
from PySide2 import QtWidgets

from qudi.util.widgets.toggle_switch import ToggleSwitch


class SpectrometerControlWidget(QtWidgets.QWidget):
    """ Widget for the spectrometer controls in SpectrometerGui """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        main_layout = QtWidgets.QGridLayout()
        self.setLayout(main_layout)
        # main_layout.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        # main_layout.setContentsMargins(1, 1, 1, 1)
        # main_layout.setSpacing(5)

        # Control buttons
        self.acquire_button = QtWidgets.QPushButton('Acquire Spectrum')
        self.acquire_button.setToolTip('Acquire a new spectrum.')
        main_layout.addWidget(self.acquire_button, 0, 0)

        self.spectrum_continue_button = QtWidgets.QPushButton('Continue Spectrum')
        self.spectrum_continue_button.setToolTip(
            'If continuous spectrum is activated, continue averaging.'
        )
        main_layout.addWidget(self.spectrum_continue_button, 0, 1)

        self.save_spectrum_button = QtWidgets.QToolButton()
        self.save_spectrum_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.save_spectrum_button.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                                                QtWidgets.QSizePolicy.Fixed)
        main_layout.addWidget(self.save_spectrum_button, 0, 2)

        self.background_button = QtWidgets.QPushButton('Acquire Background')
        self.background_button.setToolTip('Acquire a new background spectrum.')
        main_layout.addWidget(self.background_button, 1, 0)

        self.save_background_button = QtWidgets.QToolButton()
        self.save_background_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.save_background_button.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                                                  QtWidgets.QSizePolicy.Fixed)
        main_layout.addWidget(self.save_background_button, 1, 2)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar, 2, 0, 1, 3)

        # Add separator
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.VLine)
        separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(separator, 0, 3, 3, 1)

        # Control switches
        switch_layout = QtWidgets.QGridLayout()

        constant_acquisition_label = QtWidgets.QLabel('Constant Acquisition:')
        constant_acquisition_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.constant_acquisition_switch = ToggleSwitch(state_names=('Off', 'On'))
        switch_layout.addWidget(constant_acquisition_label, 0, 0)
        switch_layout.addWidget(self.constant_acquisition_switch, 0, 1)

        background_correction_label = QtWidgets.QLabel('Background Correction:')
        background_correction_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.background_correction_switch = ToggleSwitch(state_names=('Off', 'On'))
        self.background_correction_switch.setMinimumWidth(
            background_correction_label.sizeHint().width()
        )
        switch_layout.addWidget(background_correction_label, 1, 0)
        switch_layout.addWidget(self.background_correction_switch, 1, 1)

        differential_spectrum_label = QtWidgets.QLabel('Differential Spectrum:')
        differential_spectrum_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.differential_spectrum_switch = ToggleSwitch(state_names=('Off', 'On'))
        switch_layout.addWidget(differential_spectrum_label, 2, 0)
        switch_layout.addWidget(self.differential_spectrum_switch, 2, 1)

        switch_layout.setColumnStretch(2, 1)

        main_layout.addLayout(switch_layout, 0, 4, 3, 1)

        main_layout.setRowStretch(3, 1)
        main_layout.setColumnStretch(4, 1)

        self.acquire_button.setFixedWidth(self.background_button.sizeHint().width())
        self.background_button.setFixedWidth(self.background_button.sizeHint().width())
