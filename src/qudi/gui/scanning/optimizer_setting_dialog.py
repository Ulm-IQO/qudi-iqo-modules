# -*- coding: utf-8 -*-

"""
This file contains a custom QWidget class to provide optimizer settings for each scanner axis.

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

__all__ = ('OptimizerSettingDialog', 'OptimizerSettingWidget', 'OptimizerAxesWidget')

from typing import List, Tuple, Dict
from PySide2 import QtCore, QtGui, QtWidgets

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class OptimizerSettingDialog(QtWidgets.QDialog):
    """ User configurable settings for the scanner optimizer logic
    """

    def __init__(self, scanner_axes, scanner_channels, sequences: List[List[Tuple[str, ...]]]):
        super().__init__()
        self.setObjectName('optimizer_settings_dialog')
        self.setWindowTitle('Optimizer Settings')

        self.settings_widget = OptimizerSettingWidget(scanner_axes=scanner_axes,
                                                      scanner_channels=scanner_channels,
                                                      sequences=sequences)

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

    @property
    def data_channel(self) -> str:
        return self.settings_widget.data_channel

    @data_channel.setter
    def data_channel(self, ch: str) -> None:
        self.settings_widget.data_channel = ch

    @property
    def sequence(self) -> List[Tuple[str, ...]]:
        return self.settings_widget.sequence

    @sequence.setter
    def sequence(self, seq: List[Tuple[str, ...]]) -> None:
        self.settings_widget.sequence = seq

    @property
    def range(self) -> Dict[str, float]:
        return self.settings_widget.axes_widget.range

    @property
    def resolution(self) -> Dict[str, int]:
        return self.settings_widget.axes_widget.resolution

    @property
    def frequency(self) -> Dict[str, float]:
        return self.settings_widget.axes_widget.frequency

    def set_range(self, settings: Dict[str, float]):
        self.settings_widget.axes_widget.set_range(settings)

    def set_resolution(self, settings: Dict[str, int]):
        self.settings_widget.axes_widget.set_resolution(settings)

    def set_frequency(self, settings: Dict[str, float]):
        self.settings_widget.axes_widget.set_frequency(settings)


class OptimizerSettingWidget(QtWidgets.QWidget):
    """ User configurable settings for the scanner optimizer logic
    """

    def __init__(self, scanner_axes, scanner_channels, sequences: List[List[Tuple[str, ...]]]):
        super().__init__()
        self.setObjectName('optimizer_settings_widget')

        self._avail_axes = sorted([ax.name for ax in scanner_axes])
        self.available_opt_sequences = sequences

        font = QtGui.QFont()
        font.setBold(True)

        self.data_channel_combobox = QtWidgets.QComboBox()
        self.data_channel_combobox.addItems(tuple(ch.name for ch in scanner_channels))

        self.optimize_sequence_combobox = QtWidgets.QComboBox()
        self.optimize_sequence_combobox.addItems([str(seq) for seq in self.available_opt_sequences])

        label = QtWidgets.QLabel('Data channel:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        label.setFont(font)
        misc_settings_groupbox = QtWidgets.QGroupBox('General settings')
        misc_settings_groupbox.setFont(font)
        misc_settings_groupbox.setLayout(QtWidgets.QGridLayout())
        misc_settings_groupbox.layout().addWidget(label, 0, 0)
        misc_settings_groupbox.layout().addWidget(self.data_channel_combobox, 0, 1)
        misc_settings_groupbox.layout().setColumnStretch(1, 1)

        label_opt_seq = QtWidgets.QLabel('Sequence:')
        label_opt_seq.setAlignment(QtCore.Qt.AlignLeft)
        label_opt_seq.setFont(font)
        self.axes_widget = OptimizerAxesWidget(scanner_axes=scanner_axes)
        self.axes_widget.setObjectName('optimizer_axes_widget')
        scan_settings_groupbox = QtWidgets.QGroupBox('Scan settings')
        scan_settings_groupbox.setFont(font)
        scan_settings_groupbox.setLayout(QtWidgets.QGridLayout())

        scan_settings_groupbox.layout().addWidget(self.axes_widget, 0, 0, 1, -1)
        scan_settings_groupbox.layout().addWidget(label_opt_seq, 1, 0, 1, 1)
        scan_settings_groupbox.layout().addWidget(self.optimize_sequence_combobox, 1, 1, 1, 1)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(misc_settings_groupbox)
        layout.addWidget(scan_settings_groupbox)
        self.setLayout(layout)

    @property
    def data_channel(self) -> str:
        return self.data_channel_combobox.currentText()

    @data_channel.setter
    def data_channel(self, ch: str) -> None:
        self.data_channel_combobox.blockSignals(True)
        self.data_channel_combobox.setCurrentText(ch)
        self.data_channel_combobox.blockSignals(False)

    @property
    def sequence(self) -> List[Tuple[str, ...]]:
        return self.available_opt_sequences[self.optimize_sequence_combobox.currentIndex()]

    @sequence.setter
    def sequence(self, seq: List[Tuple[str, ...]]) -> None:
        self.optimize_sequence_combobox.blockSignals(True)
        try:
            idx_combo = self.available_opt_sequences.index(seq)
        except ValueError:
            idx_combo = 0
        self.optimize_sequence_combobox.setCurrentIndex(idx_combo)
        self.optimize_sequence_combobox.blockSignals(False)


class OptimizerAxesWidget(QtWidgets.QWidget):
    """ Widget to set optimizer parameters for each scanner axes
    """

    def __init__(self, *args, scanner_axes, **kwargs):
        super().__init__(*args, **kwargs)

        self.axes_widgets = dict()

        font = QtGui.QFont()
        font.setBold(True)
        layout = QtWidgets.QGridLayout()

        label = QtWidgets.QLabel('Range')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, 1)

        label = QtWidgets.QLabel('Resolution')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, 2)

        label = QtWidgets.QLabel('Frequency')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, 3)

        for index, axis in enumerate(scanner_axes, 1):
            ax_name = axis.name
            label = QtWidgets.QLabel('{0}-Axis:'.format(ax_name.title()))
            label.setObjectName('{0}_axis_label'.format(ax_name))
            label.setFont(font)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            max_range = abs(axis.position.maximum - axis.position.minimum)
            range_spinbox = ScienDSpinBox()
            range_spinbox.setObjectName('{0}_range_scienDSpinBox'.format(ax_name))
            range_spinbox.setRange(0, max_range)
            range_spinbox.setSuffix(axis.unit)
            range_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            range_spinbox.setMinimumSize(75, 0)
            range_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                        QtWidgets.QSizePolicy.Preferred)

            res_spinbox = QtWidgets.QSpinBox()
            res_spinbox.setObjectName('{0}_resolution_spinBox'.format(ax_name))
            res_spinbox.setRange(axis.resolution.minimum, min(2 ** 31 - 1, axis.resolution.maximum))
            res_spinbox.setValue(axis.resolution.minimum)
            res_spinbox.setSuffix(' px')
            res_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            res_spinbox.setMinimumSize(50, 0)
            res_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                      QtWidgets.QSizePolicy.Preferred)

            freq_spinbox = ScienDSpinBox()
            freq_spinbox.setObjectName('{0}_frequency_scienDSpinBox'.format(ax_name))
            freq_spinbox.setRange(*axis.frequency.bounds)
            freq_spinbox.setSuffix('Hz')
            freq_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            freq_spinbox.setMinimumSize(75, 0)
            freq_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                       QtWidgets.QSizePolicy.Preferred)

            # Add to layout
            layout.addWidget(label, index, 0)
            layout.addWidget(range_spinbox, index, 1)
            layout.addWidget(res_spinbox, index, 2)
            layout.addWidget(freq_spinbox, index, 3)

            # Remember widgets references for later access
            self.axes_widgets[ax_name] = dict()
            self.axes_widgets[ax_name]['label'] = label
            self.axes_widgets[ax_name]['res_spinbox'] = res_spinbox
            self.axes_widgets[ax_name]['range_spinbox'] = range_spinbox
            self.axes_widgets[ax_name]['freq_spinbox'] = freq_spinbox

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)
        self.setLayout(layout)
        self.setMaximumHeight(self.sizeHint().height())

    @property
    def resolution(self) -> Dict[str, int]:
        return {ax: widgets['res_spinbox'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def range(self) -> Dict[str, float]:
        return {ax: widgets['range_spinbox'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def frequency(self) -> Dict[str, float]:
        return {ax: widgets['freq_spinbox'].value() for ax, widgets in self.axes_widgets.items()}

    def set_resolution(self, resolution: Dict[str, int]):
        for ax, val in resolution.items():
            spinbox = self.axes_widgets[ax]['res_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)

    def set_range(self, rng: Dict[str, float]):
        for ax, val in rng.items():
            spinbox = self.axes_widgets[ax]['range_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)

    def set_frequency(self, frequency: Dict[str, float]):
        for ax, val in frequency.items():
            spinbox = self.axes_widgets[ax]['freq_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)
