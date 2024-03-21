# -*- coding: utf-8 -*-

"""
This file contains a custom QWidget class to provide scanner settings that do not need to be
accessed frequently (in contrast, see: axes_control_widget.py).

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

__all__ = ('ScannerSettingDialog', 'ScannerSettingsWidget')

from typing import List, Dict, Tuple

from PySide2 import QtCore, QtGui, QtWidgets

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.interface.scanning_probe_interface import BackScanCapability, ScanConstraints, ScannerAxis


class ScannerSettingDialog(QtWidgets.QDialog):
    """
    """
    def __init__(self, scanner_axes: List[ScannerAxis], scanner_constraints: ScanConstraints):
        super().__init__()
        self.setObjectName('scanner_settings_dialog')
        self.setWindowTitle('Scanner Settings')

        self.settings_widget = ScannerSettingsWidget(scanner_axes=scanner_axes,
                                                     scanner_constraints=scanner_constraints)
        self.button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok |
                                                     QtWidgets.QDialogButtonBox.Cancel |
                                                     QtWidgets.QDialogButtonBox.Apply,
                                                     QtCore.Qt.Horizontal,
                                                     self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.settings_widget)
        layout.addWidget(self.button_box)
        layout.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.setLayout(layout)


class ScannerSettingsWidget(QtWidgets.QWidget):
    """ Widget containing infrequently used scanner settings
    """
    def __init__(self, *args, scanner_axes: List[ScannerAxis], scanner_constraints: ScanConstraints, **kwargs):
        super().__init__(*args, **kwargs)

        self.axes_widgets = dict()
        back_scan_capability = scanner_constraints.back_scan_capability

        font = QtGui.QFont()
        font.setBold(True)
        layout = QtWidgets.QGridLayout()

        label = QtWidgets.QLabel('Forward')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, 1)

        label = QtWidgets.QLabel('Backward')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, 2)

        for index, axis in enumerate(scanner_axes, 1):
            ax_name = axis.name
            label = QtWidgets.QLabel('{0}-Axis:'.format(ax_name.title()))
            label.setObjectName('{0}_axis_label'.format(ax_name))
            label.setFont(font)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            forward_spinbox = ScienDSpinBox()
            forward_spinbox.setObjectName('{0}_forward_scienDSpinBox'.format(ax_name))
            forward_spinbox.setRange(*axis.frequency.bounds)
            forward_spinbox.setSuffix('Hz')
            forward_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            forward_spinbox.setMinimumSize(75, 0)
            forward_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                          QtWidgets.QSizePolicy.Preferred)

            backward_spinbox = ScienDSpinBox()
            backward_spinbox.setObjectName('{0}_backward_scienDSpinBox'.format(ax_name))
            backward_spinbox.setRange(*axis.frequency.bounds)
            backward_spinbox.setSuffix('Hz')
            backward_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            backward_spinbox.setMinimumSize(75, 0)
            backward_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                           QtWidgets.QSizePolicy.Preferred)
            if BackScanCapability.FREQUENCY_CONFIGURABLE not in back_scan_capability:
                backward_spinbox.setDisabled(True)
            if BackScanCapability.AVAILABLE not in back_scan_capability:
                backward_spinbox.setValue(0)
                backward_spinbox.setSuffix('na')

            # Add to layout
            layout.addWidget(label, index, 0)
            layout.addWidget(forward_spinbox, index, 1)
            layout.addWidget(backward_spinbox, index, 2)

            # Remember widgets references for later access
            self.axes_widgets[ax_name] = dict()
            self.axes_widgets[ax_name]['label'] = label
            self.axes_widgets[ax_name]['forward_freq_spinbox'] = forward_spinbox
            self.axes_widgets[ax_name]['backward_freq_spinbox'] = backward_spinbox

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)

        frequency_groupbox = QtWidgets.QGroupBox('Pixel Frequency')
        frequency_groupbox.setFont(font)
        frequency_groupbox.setLayout(layout)

        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().addWidget(frequency_groupbox)

    @property
    def axes(self):
        return tuple(self.axes_widgets)

    @property
    def frequency(self) -> Dict[str, Tuple[float, float]]:
        """
        :return: dict with
        """
        return {
            ax: (widgets['forward_freq_spinbox'].value(), widgets['backward_freq_spinbox'].value())
            for ax, widgets in self.axes_widgets.items()
        }

    def set_forward_frequency(self, ax: str, freq: float) -> None:
        spinbox = self.axes_widgets[ax]['forward_freq_spinbox']
        spinbox.blockSignals(True)
        spinbox.setValue(freq)
        spinbox.blockSignals(False)

    def set_backward_frequency(self, ax: str, freq: float) -> None:
        spinbox = self.axes_widgets[ax]['backward_freq_spinbox']
        spinbox.blockSignals(True)
        spinbox.setValue(freq)
        spinbox.blockSignals(False)
