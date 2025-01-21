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

__all__ = ('OptimizerSettingsDialog', 'OptimizerSettingsWidget', 'OptimizerAxesWidget')

from typing import List, Tuple, Dict, Iterable
from PySide2 import QtCore, QtGui, QtWidgets

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.interface.scanning_probe_interface import ScannerAxis, ScannerChannel, BackScanCapability


class OptimizerSettingsDialog(QtWidgets.QDialog):
    """Dialog for user configurable settings for the scanning optimize logic."""

    def __init__(
        self,
        scanner_axes: Iterable[ScannerAxis],
        scanner_channels: Iterable[ScannerChannel],
        sequences: Dict[list, List[Tuple[Tuple[str, ...]]]],
        sequence_dimensions: List[list],
        back_scan_capability: BackScanCapability,
    ):
        super().__init__()
        self.setObjectName('optimizer_settings_dialog')
        self.setWindowTitle('Optimizer Settings')

        self.settings_widget = OptimizerSettingsWidget(
            scanner_axes=scanner_axes,
            scanner_channels=scanner_channels,
            sequences=sequences,
            sequence_dimensions=sequence_dimensions,
            back_scan_capability=back_scan_capability,
        )

        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel | QtWidgets.QDialogButtonBox.Apply,
            QtCore.Qt.Horizontal,
            self,
        )
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
    def allowed_sequences(self) -> Tuple[Tuple[str, ...]]:
        return self.settings_widget.allowed_sequences

    @allowed_sequences.setter
    def allowed_sequences(self, sequences: Tuple[Tuple[str, ...]]) -> None:
        self.settings_widget.allowed_sequences = sequences

    @property
    def sequence_dimension(self) -> List[int]:
        return self.settings_widget.sequence_dimension

    @sequence_dimension.setter
    def sequence_dimension(self, dim: List[int]) -> None:
        self.settings_widget.sequence_dimension = dim

    @property
    def allowed_sequence_dimensions(self) -> List[int]:
        return self.settings_widget.allowed_sequence_dimensions

    @allowed_sequence_dimensions.setter
    def allowed_sequence_dimensions(self, sequence_dimensions: List[int]) -> None:
        self.settings_widget.allowed_sequence_dimensions = sequence_dimensions

    @property
    def range(self) -> Dict[str, float]:
        return self.settings_widget.axes_widget.range

    @property
    def resolution(self) -> Dict[str, int]:
        return self.settings_widget.axes_widget.resolution

    @property
    def back_resolution(self) -> Dict[str, int]:
        return self.settings_widget.axes_widget.back_resolution

    @property
    def frequency(self) -> Dict[str, float]:
        return self.settings_widget.axes_widget.frequency

    @property
    def back_frequency(self) -> Dict[str, float]:
        return self.settings_widget.axes_widget.back_frequency

    def set_range(self, settings: Dict[str, float]):
        self.settings_widget.axes_widget.set_range(settings)

    def set_resolution(self, settings: Dict[str, int]):
        self.settings_widget.axes_widget.set_resolution(settings)

    def set_back_resolution(self, settings: Dict[str, int]):
        self.settings_widget.axes_widget.set_back_resolution(settings)

    def set_frequency(self, settings: Dict[str, float]):
        self.settings_widget.axes_widget.set_frequency(settings)

    def set_back_frequency(self, settings: Dict[str, float]):
        self.settings_widget.axes_widget.set_back_frequency(settings)


class OptimizerSettingsWidget(QtWidgets.QWidget):
    """User configurable settings for the scanner optimizer logic."""

    def __init__(
        self,
        scanner_axes: Iterable[ScannerAxis],
        scanner_channels: Iterable[ScannerChannel],
        sequences: Dict[list, Tuple[Tuple[str, ...]]],
        sequence_dimensions: List[list],
        back_scan_capability: BackScanCapability,
    ):
        super().__init__()
        self.setObjectName('optimizer_settings_widget')

        self._avail_axes = sorted([ax.name for ax in scanner_axes])
        self._allowed_sequences = sequences
        self._allowed_sequence_dimensions = sequence_dimensions

        font = QtGui.QFont()
        font.setBold(True)

        self.data_channel_combobox = QtWidgets.QComboBox()
        self.data_channel_combobox.addItems(tuple(ch.name for ch in scanner_channels))

        self.optimize_sequence_dimensions_combobox = QtWidgets.QComboBox()
        self.optimize_sequence_dimensions_combobox.addItems([str(dim) for dim in self._allowed_sequence_dimensions])

        self.optimize_sequence_combobox = QtWidgets.QComboBox()
        self.optimize_sequence_combobox.addItems(
            [str(seq) for seq in self._allowed_sequences[self._allowed_sequence_dimensions[0]]]
        )

        # general settings
        label = QtWidgets.QLabel('Data channel:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        label.setFont(font)
        misc_settings_groupbox = QtWidgets.QGroupBox('General settings')
        misc_settings_groupbox.setFont(font)

        layout = QtWidgets.QGridLayout()
        layout.addWidget(label, 0, 0)
        layout.addWidget(self.data_channel_combobox, 0, 1)
        layout.setColumnStretch(1, 1)
        misc_settings_groupbox.setLayout(layout)

        # scan settings
        label_opt_seq = QtWidgets.QLabel('Sequence:')
        label_opt_seq.setAlignment(QtCore.Qt.AlignLeft)
        label_opt_seq.setFont(font)

        label_opt_seq_dim = QtWidgets.QLabel('Sequence Dimension:')
        label_opt_seq_dim.setAlignment(QtCore.Qt.AlignLeft)
        label_opt_seq_dim.setFont(font)

        self.axes_widget = OptimizerAxesWidget(scanner_axes=scanner_axes, back_scan_capability=back_scan_capability)
        self.axes_widget.setObjectName('optimizer_axes_widget')

        scan_settings_groupbox = QtWidgets.QGroupBox('Scan settings')
        scan_settings_groupbox.setFont(font)

        layout = QtWidgets.QGridLayout()
        layout.addWidget(self.axes_widget, 0, 0, 1, -1)
        layout.addWidget(label_opt_seq_dim, 1, 0, 1, 1)
        layout.addWidget(self.optimize_sequence_dimensions_combobox, 1, 1, 1, 1)
        layout.addWidget(label_opt_seq, 2, 0, 1, 1)
        layout.addWidget(self.optimize_sequence_combobox, 2, 1, 1, 1)
        scan_settings_groupbox.setLayout(layout)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(misc_settings_groupbox)
        layout.addWidget(scan_settings_groupbox)
        self.setLayout(layout)

        self.optimize_sequence_dimensions_combobox.currentIndexChanged.connect(self._update_sequence_combobox)

    @property
    def data_channel(self) -> str:
        return self.data_channel_combobox.currentText()

    @data_channel.setter
    def data_channel(self, ch: str) -> None:
        self.data_channel_combobox.blockSignals(True)
        self.data_channel_combobox.setCurrentText(ch)
        self.data_channel_combobox.blockSignals(False)

    @property
    def sequence(self) -> Tuple[Tuple[str, ...]]:
        return self._allowed_sequences[self.sequence_dimension][self.optimize_sequence_combobox.currentIndex()]

    @sequence.setter
    def sequence(self, seq: Tuple[Tuple[str, ...]]) -> None:
        self.optimize_sequence_combobox.blockSignals(True)
        try:
            idx_combo = self._allowed_sequences[self.sequence_dimension].index(seq)
        except ValueError:
            idx_combo = 0
        self.optimize_sequence_combobox.setCurrentIndex(idx_combo)
        self.optimize_sequence_combobox.blockSignals(False)

    @property
    def allowed_sequences(self) -> List[Tuple[str, ...]]:
        return self._allowed_sequences[self.sequence_dimension]

    @allowed_sequences.setter
    def allowed_sequences(self, sequences: Dict[list, List[Tuple[str, ...]]]) -> None:
        self._allowed_sequences = sequences
        self._populate_sequence_combobox()

    def _populate_sequence_combobox(self):
        self.optimize_sequence_combobox.blockSignals(True)
        self.optimize_sequence_combobox.clear()
        self.optimize_sequence_combobox.addItems([str(seq) for seq in self._allowed_sequences[self.sequence_dimension]])
        self.optimize_sequence_combobox.blockSignals(False)

    @property
    def sequence_dimension(self) -> List[int]:
        return self._allowed_sequence_dimensions[self.optimize_sequence_dimensions_combobox.currentIndex()]

    @sequence_dimension.setter
    def sequence_dimension(self, seq_dim: List[int]) -> None:
        self.optimize_sequence_dimensions_combobox.blockSignals(True)
        try:
            idx_combo = self._allowed_sequence_dimensions.index(seq_dim)
        except ValueError:
            idx_combo = 0
        self.optimize_sequence_dimensions_combobox.setCurrentIndex(idx_combo)
        self.optimize_sequence_dimensions_combobox.blockSignals(False)
        self._populate_sequence_combobox()

    @property
    def allowed_sequence_dimensions(self) -> List[int]:
        return self._allowed_sequence_dimensions

    @allowed_sequence_dimensions.setter
    def allowed_sequence_dimensions(self, sequence_dimensions: List[int]) -> None:
        self.optimize_sequence_dimensions_combobox.blockSignals(True)
        self._allowed_sequence_dimensions = sequence_dimensions
        self.optimize_sequence_dimensions_combobox.clear()
        self.optimize_sequence_dimensions_combobox.addItems([str(dim) for dim in self._allowed_sequence_dimensions])
        self.optimize_sequence_dimensions_combobox.blockSignals(False)
        self._populate_sequence_combobox()

    def _update_sequence_combobox(self, index: int) -> None:
        self.sequence_dimension = self.allowed_sequence_dimensions[index]
        self._populate_sequence_combobox()
        self.sequence = self.allowed_sequences[0]


class OptimizerAxesWidget(QtWidgets.QWidget):
    """Widget to set optimizer parameters for each scanner axes.

    There are spin boxes for range, resolution, backward resolution, frequency and backward frequency.
    A checkbox between the forward and backward resolution/frequency can be used to automatically
    have an equal setting for both directions. Depending on the back scan capability of the hardware, this checkbox
    is checked and disabled (if available but not configurable) or unchecked and disabled (not available).
    """

    def __init__(self, *args, scanner_axes: Iterable[ScannerAxis], back_scan_capability: BackScanCapability, **kwargs):
        super().__init__(*args, **kwargs)

        self._back_scan_capability = back_scan_capability

        # remember widgets references for later access
        self.axes_widgets = dict()

        font = QtGui.QFont()
        font.setBold(True)
        layout = QtWidgets.QGridLayout()

        for i, label_text in enumerate(
            ['Range', 'Resolution', '=', 'Back\nResolution', 'Frequency', '=', 'Back\nFrequency']
        ):
            label = QtWidgets.QLabel(label_text)
            label.setFont(font)
            label.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(label, 0, i + 1)
            if (
                '=' in label_text or 'Back' in label_text
            ) and BackScanCapability.AVAILABLE not in self._back_scan_capability:
                label.hide()

        for index, axis in enumerate(scanner_axes, 1):
            ax_name = axis.name
            self.axes_widgets[ax_name] = dict()
            label = QtWidgets.QLabel('{0}-Axis:'.format(ax_name.title()))
            label.setObjectName('{0}_axis_label'.format(ax_name))
            label.setFont(font)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            max_range = abs(axis.position.maximum - axis.position.minimum)
            range_spinbox = ScienDSpinBox()
            range_spinbox.setObjectName('{0}_range_scienDSpinBox'.format(ax_name))
            range_spinbox.setRange(0, max_range)
            range_spinbox.setSuffix(axis.unit)
            range_spinbox.setMinimumSize(75, 0)
            self.axes_widgets[ax_name]['range'] = range_spinbox

            for direction in ['forward', 'backward']:
                res_spinbox = QtWidgets.QSpinBox()
                res_spinbox.setObjectName(f'{ax_name}_{direction}_resolution_spinBox')
                res_spinbox.setRange(axis.resolution.minimum, min(2**31 - 1, axis.resolution.maximum))
                res_spinbox.setSuffix(' px')
                res_spinbox.setMinimumSize(50, 0)
                self.axes_widgets[ax_name][f'{direction}_res'] = res_spinbox

                freq_spinbox = ScienDSpinBox()
                freq_spinbox.setObjectName(f'{ax_name}_{direction}_frequency_scienDSpinBox')
                freq_spinbox.setRange(*axis.frequency.bounds)
                freq_spinbox.setSuffix('Hz')
                freq_spinbox.setMinimumSize(75, 0)
                self.axes_widgets[ax_name][f'{direction}_freq'] = freq_spinbox

            # same for every spinbox
            for spinbox in self.axes_widgets[ax_name].values():
                spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
                spinbox.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)

            # checkbox for having back settings equal to forward settings
            for setting in ['res', 'freq']:
                check_box = QtWidgets.QCheckBox()
                check_box.stateChanged.connect(self._get_equal_checkbox_callback(ax_name, setting))
                self.axes_widgets[ax_name][f'{setting}_eq'] = check_box

            if BackScanCapability.AVAILABLE not in self._back_scan_capability:
                for widget in ['res_eq', 'freq_eq', 'backward_res', 'backward_freq']:
                    self.axes_widgets[ax_name][widget].hide()
            else:
                if BackScanCapability.RESOLUTION_CONFIGURABLE not in self._back_scan_capability:
                    self.axes_widgets[ax_name]['res_eq'].setChecked(True)
                    self.axes_widgets[ax_name]['res_eq'].setEnabled(False)
                    for widget in ['res_eq', 'backward_res']:
                        self.axes_widgets[ax_name][widget].setToolTip("Back resolution is not configurable.")
                if BackScanCapability.FREQUENCY_CONFIGURABLE not in self._back_scan_capability:
                    self.axes_widgets[ax_name]['freq_eq'].setChecked(True)
                    self.axes_widgets[ax_name]['freq_eq'].setEnabled(False)
                    for widget in ['freq_eq', 'backward_freq']:
                        self.axes_widgets[ax_name][widget].setToolTip("Back frequency is not configurable.")

            for widget in ['res_eq', 'freq_eq']:
                if self.axes_widgets[ax_name][widget].isVisible() and self.axes_widgets[ax_name][widget].isEnabled():
                    self.axes_widgets[ax_name][widget].setChecked(True)

            # Add to layout
            layout.addWidget(label, index, 0)
            layout.addWidget(self.axes_widgets[ax_name]['range'], index, 1)
            layout.addWidget(self.axes_widgets[ax_name]['forward_res'], index, 2)
            layout.addWidget(self.axes_widgets[ax_name]['res_eq'], index, 3)
            layout.addWidget(self.axes_widgets[ax_name]['backward_res'], index, 4)
            layout.addWidget(self.axes_widgets[ax_name]['forward_freq'], index, 5)
            layout.addWidget(self.axes_widgets[ax_name]['freq_eq'], index, 6)
            layout.addWidget(self.axes_widgets[ax_name]['backward_freq'], index, 7)

        self.setLayout(layout)
        self.setMaximumHeight(self.sizeHint().height())

    @property
    def range(self) -> Dict[str, float]:
        return {ax: widgets['range'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def resolution(self) -> Dict[str, int]:
        return {ax: widgets['forward_res'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def back_resolution(self) -> Dict[str, int]:
        if BackScanCapability.RESOLUTION_CONFIGURABLE in self._back_scan_capability:
            return {ax: widgets['backward_res'].value() for ax, widgets in self.axes_widgets.items()}
        else:
            return {}

    @property
    def frequency(self) -> Dict[str, float]:
        return {ax: widgets['forward_freq'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def back_frequency(self) -> Dict[str, float]:
        if BackScanCapability.FREQUENCY_CONFIGURABLE in self._back_scan_capability:
            return {ax: widgets['backward_freq'].value() for ax, widgets in self.axes_widgets.items()}
        else:
            return {}

    def set_range(self, rng: Dict[str, float]):
        self._set_setting('range', rng)

    def set_resolution(self, resolution: Dict[str, int]):
        self._set_setting('forward_res', resolution)

    def set_back_resolution(self, resolution: Dict[str, int]):
        if BackScanCapability.RESOLUTION_CONFIGURABLE in self._back_scan_capability:
            self._set_setting('backward_res', resolution)

    def set_frequency(self, frequency: Dict[str, float]):
        self._set_setting('forward_freq', frequency)

    def set_back_frequency(self, frequency: Dict[str, float]):
        if BackScanCapability.FREQUENCY_CONFIGURABLE in self._back_scan_capability:
            self._set_setting('backward_freq', frequency)

    def _set_setting(self, setting: str, values: Dict[str, float]) -> None:
        for ax, val in values.items():
            spinbox = self.axes_widgets[ax][setting]
            spinbox.setValue(val)

    def _get_equal_checkbox_callback(self, axis: str, setting: str):
        @QtCore.Slot(bool)
        def callback(checked: bool):
            forward_spinbox = self.axes_widgets[axis][f'forward_{setting}']
            backward_spinbox = self.axes_widgets[axis][f'backward_{setting}']
            if checked:
                forward_spinbox.valueChanged.connect(backward_spinbox.setValue)
                backward_spinbox.setValue(forward_spinbox.value())  # set manually once
            else:
                forward_spinbox.valueChanged.disconnect()
            backward_spinbox.setDisabled(checked)

        return callback
