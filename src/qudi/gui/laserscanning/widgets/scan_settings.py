# -*- coding: utf-8 -*-
"""
Contains scan settings widgets for the laser scanning toolchain GUI.

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

__all__ = ['LaserScanSettingsWidget', 'LaserScanSettingsDialog', 'LaserScanSettingsDockWidget']

from typing import Optional
from PySide2 import QtCore, QtWidgets

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.interface.scannable_laser_interface import ScannableLaserConstraints, LaserScanMode
from qudi.interface.scannable_laser_interface import ScannableLaserSettings, LaserScanDirection


class LaserScanSettingsWidget(QtWidgets.QWidget):
    """ """

    sigSettingsChanged = QtCore.Signal(object)

    min_spinbox: ScienDSpinBox
    max_spinbox: ScienDSpinBox
    speed_spinbox: ScienDSpinBox
    repetitions_spinbox: QtWidgets.QSpinBox
    mode_combobox: QtWidgets.QComboBox
    direction_combobox: QtWidgets.QComboBox

    def __init__(self,
                 constraints: ScannableLaserConstraints,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self.min_spinbox = ScienDSpinBox()
        self.min_spinbox.setMinimumWidth(100)
        self.min_spinbox.setRange(*constraints.value.bounds)
        self.min_spinbox.setSuffix(constraints.unit)
        self.min_spinbox.setDecimals(6)
        self.min_spinbox.setValue(constraints.value.minimum)
        if constraints.value.increment:
            self.min_spinbox.setSingleStep(constraints.value.increment, dynamic_stepping=False)

        self.max_spinbox = ScienDSpinBox()
        self.max_spinbox.setMinimumWidth(100)
        self.max_spinbox.setRange(*constraints.value.bounds)
        self.max_spinbox.setSuffix(constraints.unit)
        self.max_spinbox.setDecimals(6)
        self.max_spinbox.setValue(constraints.value.maximum)
        if constraints.value.increment:
            self.max_spinbox.setSingleStep(constraints.value.increment, dynamic_stepping=False)

        self.speed_spinbox = ScienDSpinBox()
        self.speed_spinbox.setMinimumWidth(100)
        self.speed_spinbox.setRange(*constraints.speed.bounds)
        self.speed_spinbox.setSuffix(f'{constraints.unit}/s')
        self.speed_spinbox.setDecimals(6)
        self.speed_spinbox.setValue(constraints.speed.default)
        if constraints.speed.increment:
            self.speed_spinbox.setSingleStep(constraints.speed.increment, dynamic_stepping=False)

        self.repetitions_spinbox = QtWidgets.QSpinBox()
        self.repetitions_spinbox.setMinimumWidth(100)
        self.repetitions_spinbox.setRange(*constraints.repetitions.bounds)
        self.repetitions_spinbox.setValue(constraints.repetitions.default)
        if constraints.repetitions.increment:
            self.repetitions_spinbox.setSingleStep(constraints.repetitions.increment)

        self.mode_combobox = QtWidgets.QComboBox()
        self.mode_combobox.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow
        )
        self.mode_combobox.addItems([mode.name for mode in constraints.modes])
        self.mode_combobox.setCurrentIndex(0)

        self.direction_combobox = QtWidgets.QComboBox()
        self.direction_combobox.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow
        )
        self.direction_combobox.addItems([mode.name for mode in constraints.initial_directions])
        self.direction_combobox.setCurrentIndex(0)

        # layout widgets
        layout = QtWidgets.QGridLayout()
        label = QtWidgets.QLabel('Scan bounds:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 0, 0)
        layout.addWidget(self.min_spinbox, 0, 1)
        layout.addWidget(self.max_spinbox, 0, 2)
        label = QtWidgets.QLabel('Scan speed:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 1, 0)
        layout.addWidget(self.speed_spinbox, 1, 1, 1, 2)
        label = QtWidgets.QLabel('Scan repetitions:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 2, 0)
        layout.addWidget(self.repetitions_spinbox, 2, 1, 1, 2)
        label = QtWidgets.QLabel('Scan mode:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 3, 0)
        layout.addWidget(self.mode_combobox, 3, 1, 1, 2)
        label = QtWidgets.QLabel('Initial direction:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 4, 0)
        layout.addWidget(self.direction_combobox, 4, 1, 1, 2)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        layout.setRowStretch(5, 1)
        self.setLayout(layout)

        # disable/enable repetitions according to scan mode
        self.mode_combobox.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()
        # Connect editing finished signals
        self.min_spinbox.editingFinished.connect(self.__emit_changes)
        self.max_spinbox.editingFinished.connect(self.__emit_changes)
        self.speed_spinbox.editingFinished.connect(self.__emit_changes)
        self.repetitions_spinbox.editingFinished.connect(self.__emit_changes)
        self.direction_combobox.currentIndexChanged.connect(self.__emit_changes)
        self.mode_combobox.currentIndexChanged.connect(self.__emit_changes)

    def get_settings(self) -> ScannableLaserSettings:
        return ScannableLaserSettings(
            bounds=(self.min_spinbox.value(), self.max_spinbox.value()),
            speed=self.speed_spinbox.value(),
            mode=LaserScanMode[self.mode_combobox.currentText()],
            initial_direction=LaserScanDirection[self.direction_combobox.currentText()],
            repetitions=self.repetitions_spinbox.value()
        )

    def update_settings(self, settings: ScannableLaserSettings) -> None:
        self.min_spinbox.blockSignals(True)
        self.max_spinbox.blockSignals(True)
        self.speed_spinbox.blockSignals(True)
        self.repetitions_spinbox.blockSignals(True)
        self.direction_combobox.blockSignals(True)
        self.mode_combobox.blockSignals(True)
        self.min_spinbox.setValue(min(settings.bounds))
        self.max_spinbox.setValue(max(settings.bounds))
        self.speed_spinbox.setValue(settings.speed)
        self.repetitions_spinbox.setValue(settings.repetitions)
        self.direction_combobox.setCurrentText(settings.initial_direction.name)
        self.mode_combobox.setCurrentText(settings.mode.name)
        self.min_spinbox.blockSignals(False)
        self.max_spinbox.blockSignals(False)
        self.speed_spinbox.blockSignals(False)
        self.repetitions_spinbox.blockSignals(False)
        self.direction_combobox.blockSignals(False)
        self.mode_combobox.blockSignals(False)
        self._mode_changed()
        self.__emit_changes()

    def _mode_changed(self) -> None:
        self.repetitions_spinbox.setEnabled(
            self.mode_combobox.currentText() == LaserScanMode.REPETITIONS.name
        )

    def __emit_changes(self) -> None:
        self.sigSettingsChanged.emit(self.get_settings())


class LaserScanSettingsDialog(QtWidgets.QDialog):
    """ A QDialog for LaserScanSettingsWidget """

    settings_widget: LaserScanSettingsWidget
    button_box: QtWidgets.QDialogButtonBox

    def __init__(self, *args, constraints: ScannableLaserConstraints, **kwargs):
        super().__init__(*args, **kwargs)

        self.settings_widget = LaserScanSettingsWidget(constraints=constraints)
        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal
        )
        self.button_box.setCenterButtons(True)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.settings_widget)
        layout.addWidget(self.button_box)
        layout.setStretch(0, 1)
        self.setLayout(layout)
        self.get_settings = self.settings_widget.get_settings
        self.update_settings = self.settings_widget.update_settings
        self.sigSettingsChanged = self.settings_widget.sigSettingsChanged


class LaserScanSettingsDockWidget(QtWidgets.QDockWidget):
    """ A QDockWidget for LaserScanSettingsWidget """

    settings_widget: LaserScanSettingsWidget

    def __init__(self, *args, constraints: ScannableLaserConstraints, **kwargs):
        super().__init__(*args, **kwargs)

        self.settings_widget = LaserScanSettingsWidget(constraints=constraints)
        self.setWidget(self.settings_widget)
        self.settings_widget.setFixedHeight(self.settings_widget.sizeHint().height())
        self.get_settings = self.settings_widget.get_settings
        self.update_settings = self.settings_widget.update_settings
        self.sigSettingsChanged = self.settings_widget.sigSettingsChanged
