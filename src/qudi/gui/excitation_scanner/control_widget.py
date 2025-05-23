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

__all__ = ['ScanningExcitationControlWidget']

from PySide2 import QtCore
from PySide2 import QtWidgets

from qudi.util.widgets.toggle_switch import ToggleSwitch
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox

_widget_mapping = {
    bool: QtWidgets.QCheckBox,
    int: QtWidgets.QSpinBox,
    float: ScienDSpinBox,
}
_signal_mapping = {
    QtWidgets.QCheckBox:"stateChanged",
    QtWidgets.QSpinBox:"valueChanged",
    ScienDSpinBox:"valueChanged",
}

class ScanningExcitationControlWidget(QtWidgets.QWidget):
    """ Widget for the spectrometer controls in ScanningExcitationGui """

    sig_toggle_acquisition = QtCore.Signal()
    sig_repetitions_set = QtCore.Signal(int)
    sig_variable_set = QtCore.Signal(str, object)

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
        self.acquire_button.clicked.connect(self.sig_toggle_acquisition.emit)
        main_layout.addWidget(self.acquire_button, 0, 0)

        self.save_spectrum_button = QtWidgets.QToolButton()
        self.save_spectrum_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.save_spectrum_button.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                                                QtWidgets.QSizePolicy.Fixed)
        main_layout.addWidget(self.save_spectrum_button, 0, 1)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar, 2, 0, 1, 2)

        # Add separator
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.HLine)
        separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(separator, 3, 0, 1, 2)

        # Common controls
        common_controls_layout = QtWidgets.QFormLayout()
        self.exposure_spinbox = ScienDSpinBox()
        self.exposure_spinbox.setMinimum(0)
        self.exposure_spinbox.setSuffix("s")
        common_controls_layout.addRow("Exposure", self.exposure_spinbox)
        self.repetitions_spinbox = QtWidgets.QSpinBox()
        self.repetitions_spinbox.setMinimum(1)
        self.repetitions_spinbox.setMaximum(100)
        common_controls_layout.addRow("Repetitions", self.repetitions_spinbox)
        self.status_label = QtWidgets.QLabel("")
        common_controls_layout.addRow("Scanner status", self.status_label)
        main_layout.addLayout(common_controls_layout, 4, 0, 1, 2)

        self._variables_layout = QtWidgets.QFormLayout()
        main_layout.addLayout(self._variables_layout, 0, 2, 1, 1)
        self._variables_widgets = {}

        #spacer = QtWidgets.QSpacerItem(1, 1, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        #main_layout.addItem(spacer, 0, 3, 1, 1)

        notes_group_box = QtWidgets.QGroupBox('Notes')
        notes_layout = QtWidgets.QVBoxLayout()
        notes_group_box.setLayout(notes_layout)
        self.notes_text_input = QtWidgets.QTextEdit()
        notes_layout.addWidget(self.notes_text_input)
        notes_group_box.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                           QtWidgets.QSizePolicy.Minimum)
        main_layout.addWidget(notes_group_box, 0, 3, 1, 1)

    def create_variable_widgets(self, variables_dict):
        for widget in self._variables_widgets.values():
            self._variables_layout.removeRow(0)
            getattr(widget, _signal_mapping[type(widget)]).disconnect()
        self._variables_widgets = {}
        for variable in variables_dict.values():
            name = variable.name
            limits = variable.limits
            t = variable.type
            unit = variable.unit
            value = variable.value
            widget = _widget_mapping[t]()
            if t==bool:
                widget.setChecked(value)
            else:
                widget.setValue(value)
            getattr(widget, _signal_mapping[type(widget)]).connect(lambda v,name=name:self.sig_variable_set.emit(name, v))
            if t != bool:
                widget.setSuffix(unit)
                widget.setMinimum(limits[0])
                widget.setMaximum(limits[1])
            self._variables_layout.addRow(name, widget)
            self._variables_widgets[name] = widget

    def update_variable_widgets(self, variables_dict):
        for variable in variables_dict.values():
            name = variable.name
            limits = variable.limits
            t = variable.type
            unit = variable.unit
            value = variable.value
            widget = self._variables_widgets[name]
            if widget.hasFocus():
                continue
            else:
                widget.blockSignals(True)
                if t==bool:
                    widget.setChecked(value)
                else:
                    widget.setValue(value)
                if t != bool:
                    widget.setSuffix(unit)
                    widget.setMinimum(limits[0])
                    widget.setMaximum(limits[1])
                widget.blockSignals(False)

