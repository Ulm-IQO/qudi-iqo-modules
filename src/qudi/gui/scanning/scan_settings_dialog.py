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

from PySide2 import QtCore, QtGui, QtWidgets
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class ScannerSettingDialog(QtWidgets.QDialog):
    """
    """

    def __init__(self, scanner_axes, scanner_constraints):
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
        return


class ScannerSettingsWidget(QtWidgets.QWidget):
    """ Widget containing infrequently used scanner settings
    """

    sigFrequencyChanged = QtCore.Signal(str, float, float)
    # TODO sigRangeChanged does not exist

    def __init__(self, *args, scanner_axes, scanner_constraints, **kwargs):
        super().__init__(*args, **kwargs)

        self.axes_widgets = dict()
        self._backscan_configurable = scanner_constraints._backscan_configurable

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
            forward_spinbox.setRange(*axis.frequency_range)
            forward_spinbox.setValue(max(axis.min_frequency, axis.max_frequency / 100))
            forward_spinbox.setSuffix('Hz')
            forward_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            forward_spinbox.setMinimumSize(75, 0)
            forward_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                          QtWidgets.QSizePolicy.Preferred)

            backward_spinbox = ScienDSpinBox()
            backward_spinbox.setObjectName('{0}_backward_scienDSpinBox'.format(ax_name))
            backward_spinbox.setRange(*axis.frequency_range)
            backward_spinbox.setValue(max(axis.min_frequency, axis.max_frequency / 100))
            backward_spinbox.setSuffix('Hz')
            backward_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            backward_spinbox.setMinimumSize(75, 0)
            backward_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                           QtWidgets.QSizePolicy.Preferred)
            if not self._backscan_configurable:
                backward_spinbox.setRange(0, 1)
                backward_spinbox.setValue(0.)
                backward_spinbox.setSuffix('/na')
                backward_spinbox.setDisabled(True)

            # Add to layout
            layout.addWidget(label, index, 0)
            layout.addWidget(forward_spinbox, index, 1)
            layout.addWidget(backward_spinbox, index, 2)

            # Connect signals
            forward_spinbox.editingFinished.connect(
                self.__get_axis_forward_callback(ax_name, forward_spinbox)
            )
            backward_spinbox.editingFinished.connect(
                self.__get_axis_backward_callback(ax_name, backward_spinbox)
            )

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
    def frequency(self):
        """
        :return: dict with
        """
        return {
            ax: (widgets['forward_freq_spinbox'].value(), widgets['backward_freq_spinbox'].value()
                if self._backscan_configurable else None)
            for ax, widgets in self.axes_widgets.items()
        }

    @QtCore.Slot(dict)
    @QtCore.Slot(object, str)
    def set_frequency(self, value, axis=None):
        if axis is None or isinstance(value, dict):
            for ax, (forward, backwards) in value.items():
                forward_spinbox = self.axes_widgets[ax]['forward_freq_spinbox']
                backward_spinbox = self.axes_widgets[ax]['backward_freq_spinbox']
                forward_spinbox.blockSignals(True)
                forward_spinbox.setValue(forward)
                forward_spinbox.blockSignals(False)
                backward_spinbox.blockSignals(True)
                backward_spinbox.setValue(backwards) if self._backscan_configurable else None
                backward_spinbox.blockSignals(False)
        else:
            forward_spinbox = self.axes_widgets[axis]['forward_freq_spinbox']
            backward_spinbox = self.axes_widgets[axis]['backward_freq_spinbox']
            forward, backwards = value
            forward_spinbox.blockSignals(True)
            forward_spinbox.setValue(forward)
            forward_spinbox.blockSignals(False)
            backward_spinbox.blockSignals(True)
            backward_spinbox.setValue(backwards) if self._backscan_configurable else None
            backward_spinbox.blockSignals(False)

    def __get_axis_forward_callback(self, axis, spinbox):
        def callback():
            backward_spinbox = self.axes_widgets[axis]['backward_freq_spinbox']
            self.sigFrequencyChanged.emit(axis, spinbox.value(), backward_spinbox.value())
        return callback

    def __get_axis_backward_callback(self, axis, spinbox):
        def callback():
            forward_spinbox = self.axes_widgets[axis]['forward_freq_spinbox']
            self.sigFrequencyChanged.emit(axis, spinbox.value(), backward_spinbox.value())
        return callback
