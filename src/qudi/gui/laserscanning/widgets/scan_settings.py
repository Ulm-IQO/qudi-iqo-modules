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

from typing import Optional, Tuple
from PySide2 import QtCore, QtWidgets
from scipy.constants import speed_of_light as _SPEED_OF_LIGHT

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.interface.scannable_laser_interface import ScannableLaserConstraints, LaserScanMode
from qudi.interface.scannable_laser_interface import ScannableLaserSettings, LaserScanDirection


class LaserScanSettingsWidget(QtWidgets.QWidget):
    """ """

    sigSettingsChanged = QtCore.Signal(object)
    sigBoundarySourceChanged = QtCore.Signal(bool)  # True: wavelength bounds, False: device bounds
    sigWavelengthBoundsChanged = QtCore.Signal(tuple)  # (min_wavelength, max_wavelength)

    min_spinbox: ScienDSpinBox
    max_spinbox: ScienDSpinBox
    speed_spinbox: ScienDSpinBox
    repetitions_spinbox: QtWidgets.QSpinBox
    mode_combobox: QtWidgets.QComboBox

    wl_min_spinbox: ScienDSpinBox
    wl_max_spinbox: ScienDSpinBox
    device_bounds_radio: QtWidgets.QRadioButton
    wavelength_bounds_radio: QtWidgets.QRadioButton
    _boundary_button_group: QtWidgets.QButtonGroup

    def __init__(self,
                 constraints: ScannableLaserConstraints,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self._is_frequency = False

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

        self.wl_min_spinbox = ScienDSpinBox()
        self.wl_min_spinbox.setMinimumWidth(100)
        self.wl_min_spinbox.setRange(1.0, 2000.0)  # nm (display)
        self.wl_min_spinbox.setSuffix('nm')
        self.wl_min_spinbox.setDecimals(9)
        self.wl_min_spinbox.setValue(500.0)

        self.wl_max_spinbox = ScienDSpinBox()
        self.wl_max_spinbox.setMinimumWidth(100)
        self.wl_max_spinbox.setRange(1.0, 2000.0)  # nm (display)
        self.wl_max_spinbox.setSuffix('nm')
        self.wl_max_spinbox.setDecimals(9)
        self.wl_max_spinbox.setValue(750.0)

        self.device_bounds_radio = QtWidgets.QRadioButton()
        self.wavelength_bounds_radio = QtWidgets.QRadioButton()
        self._boundary_button_group = QtWidgets.QButtonGroup(self)
        self._boundary_button_group.setExclusive(True)
        self._boundary_button_group.addButton(self.device_bounds_radio)
        self._boundary_button_group.addButton(self.wavelength_bounds_radio)
        self.device_bounds_radio.setChecked(True)

        # layout widgets
        layout = QtWidgets.QGridLayout()
        label = QtWidgets.QLabel('Scan bounds:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 0, 0)
        layout.addWidget(self.min_spinbox, 0, 1)
        layout.addWidget(self.max_spinbox, 0, 2)
        layout.addWidget(self.device_bounds_radio, 0, 3)

        # Wavelength bounds row with radio
        label = QtWidgets.QLabel('Wavelength bounds:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 1, 0)
        layout.addWidget(self.wl_min_spinbox, 1, 1)
        layout.addWidget(self.wl_max_spinbox, 1, 2)
        layout.addWidget(self.wavelength_bounds_radio, 1, 3)

        # Speed row
        label = QtWidgets.QLabel('Scan speed:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 2, 0)
        layout.addWidget(self.speed_spinbox, 2, 1, 1, 3)

        # Repetitions row
        label = QtWidgets.QLabel('Scan repetitions:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 3, 0)
        layout.addWidget(self.repetitions_spinbox, 3, 1, 1, 3)

        # Mode row
        label = QtWidgets.QLabel('Scan mode:')
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(label, 4, 0)
        layout.addWidget(self.mode_combobox, 4, 1, 1, 3)

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
        self.mode_combobox.currentIndexChanged.connect(self.__emit_changes)

        # Boundary source and wavelength bounds signals
        self.device_bounds_radio.toggled.connect(self.__emit_boundary_source)
        self.wavelength_bounds_radio.toggled.connect(self.__emit_boundary_source)
        self.wl_min_spinbox.editingFinished.connect(self.__emit_wavelength_bounds)
        self.wl_max_spinbox.editingFinished.connect(self.__emit_wavelength_bounds)

    def get_settings(self) -> ScannableLaserSettings:
        return ScannableLaserSettings(
            bounds=(self.min_spinbox.value(), self.max_spinbox.value()),
            speed=self.speed_spinbox.value(),
            mode=LaserScanMode[self.mode_combobox.currentText()],
            initial_direction=LaserScanDirection.UNDEFINED,
            repetitions=self.repetitions_spinbox.value()
        )

    def update_settings(self, settings: ScannableLaserSettings) -> None:
        self.min_spinbox.blockSignals(True)
        self.max_spinbox.blockSignals(True)
        self.speed_spinbox.blockSignals(True)
        self.repetitions_spinbox.blockSignals(True)
        self.mode_combobox.blockSignals(True)
        self.min_spinbox.setValue(min(settings.bounds))
        self.max_spinbox.setValue(max(settings.bounds))
        self.speed_spinbox.setValue(settings.speed)
        self.repetitions_spinbox.setValue(settings.repetitions)
        self.mode_combobox.setCurrentText(settings.mode.name)
        self.min_spinbox.blockSignals(False)
        self.max_spinbox.blockSignals(False)
        self.speed_spinbox.blockSignals(False)
        self.repetitions_spinbox.blockSignals(False)
        self.mode_combobox.blockSignals(False)
        self._mode_changed()
        self.__emit_changes()

    def set_boundary_source(self, use_wavelength_bounds: bool) -> None:
        self.device_bounds_radio.blockSignals(True)
        self.wavelength_bounds_radio.blockSignals(True)
        self.device_bounds_radio.setChecked(not use_wavelength_bounds)
        self.wavelength_bounds_radio.setChecked(use_wavelength_bounds)
        self.device_bounds_radio.blockSignals(False)
        self.wavelength_bounds_radio.blockSignals(False)

    def update_wavelength_bounds(self, span: Tuple[float, float]) -> None:
        lo_si, hi_si = min(span), max(span)

        self.wl_min_spinbox.blockSignals(True)
        self.wl_max_spinbox.blockSignals(True)

        if self._is_frequency:
            lo_disp = (_SPEED_OF_LIGHT / hi_si) * 1e-12
            hi_disp = (_SPEED_OF_LIGHT / lo_si) * 1e-12
        else:
            lo_disp = lo_si * 1e9
            hi_disp = hi_si * 1e9

        self.wl_min_spinbox.setValue(min(lo_disp, hi_disp))
        self.wl_max_spinbox.setValue(max(lo_disp, hi_disp))

        self.wl_min_spinbox.blockSignals(False)
        self.wl_max_spinbox.blockSignals(False)

        self.__emit_wavelength_bounds()

    def _mode_changed(self) -> None:
        self.repetitions_spinbox.setEnabled(
            self.mode_combobox.currentText() == LaserScanMode.REPETITIONS.name
        )

    def __emit_changes(self) -> None:
        self.sigSettingsChanged.emit(self.get_settings())

    def __emit_boundary_source(self) -> None:
        # Radio buttons are exclusive; wavelength radio checked -> True
        self.sigBoundarySourceChanged.emit(self.wavelength_bounds_radio.isChecked())

    def __emit_wavelength_bounds(self) -> None:
        self.sigWavelengthBoundsChanged.emit(self.get_wavelength_bounds_si())

    def toggle_is_frequency(self, is_frequency: bool) -> None:
        """Switch wavelength-bound controls between nm and THz display."""
        is_frequency = bool(is_frequency)
        if self._is_frequency == is_frequency:
            return

        # preserve SI values while changing display
        lo_si, hi_si = self.get_wavelength_bounds_si()

        self.wl_min_spinbox.blockSignals(True)
        self.wl_max_spinbox.blockSignals(True)

        if is_frequency:
            # meters -> THz
            lo_thz = (_SPEED_OF_LIGHT / hi_si) * 1e-12
            hi_thz = (_SPEED_OF_LIGHT / lo_si) * 1e-12
            self.wl_min_spinbox.setRange(0.01, 2000.0)
            self.wl_max_spinbox.setRange(0.01, 2000.0)
            self.wl_min_spinbox.setSuffix('THz')
            self.wl_max_spinbox.setSuffix('THz')
            self.wl_min_spinbox.setValue(min(lo_thz, hi_thz))
            self.wl_max_spinbox.setValue(max(lo_thz, hi_thz))
        else:
            # THz -> nm
            lo_nm = (_SPEED_OF_LIGHT / (hi_si if hi_si > 0 else 1e-30)) * 1e9
            hi_nm = (_SPEED_OF_LIGHT / (lo_si if lo_si > 0 else 1e-30)) * 1e9
            self.wl_min_spinbox.setRange(1.0, 2000.0)
            self.wl_max_spinbox.setRange(1.0, 2000.0)
            self.wl_min_spinbox.setSuffix('nm')
            self.wl_max_spinbox.setSuffix('nm')
            self.wl_min_spinbox.setValue(min(lo_nm, hi_nm))
            self.wl_max_spinbox.setValue(max(lo_nm, hi_nm))

        self.wl_min_spinbox.blockSignals(False)
        self.wl_max_spinbox.blockSignals(False)

        self._is_frequency = is_frequency
        self.__emit_wavelength_bounds()

    def get_wavelength_bounds_si(self) -> Tuple[float, float]:
        """Return wavelength bounds in meters independent of display mode."""
        lo = float(self.wl_min_spinbox.value())
        hi = float(self.wl_max_spinbox.value())
        lo, hi = min(lo, hi), max(lo, hi)

        if self._is_frequency:
            # THz -> Hz -> m (higher f means lower λ)
            f_lo_hz = lo * 1e12
            f_hi_hz = hi * 1e12
            wl_hi = _SPEED_OF_LIGHT / f_lo_hz
            wl_lo = _SPEED_OF_LIGHT / f_hi_hz
            return min(wl_lo, wl_hi), max(wl_lo, wl_hi)
        else:
            # nm -> m
            return lo * 1e-9, hi * 1e-9


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
        self.sigBoundarySourceChanged = self.settings_widget.sigBoundarySourceChanged
        self.sigWavelengthBoundsChanged = self.settings_widget.sigWavelengthBoundsChanged
        self.set_boundary_source = self.settings_widget.set_boundary_source
        self.update_wavelength_bounds = self.settings_widget.update_wavelength_bounds
        self.toggle_is_frequency = self.settings_widget.toggle_is_frequency


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
        self.sigBoundarySourceChanged = self.settings_widget.sigBoundarySourceChanged
        self.sigWavelengthBoundsChanged = self.settings_widget.sigWavelengthBoundsChanged
        self.set_boundary_source = self.settings_widget.set_boundary_source
        self.update_wavelength_bounds = self.settings_widget.update_wavelength_bounds
        self.toggle_is_frequency = self.settings_widget.toggle_is_frequency