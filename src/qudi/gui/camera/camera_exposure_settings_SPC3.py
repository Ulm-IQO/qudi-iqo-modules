# -*- coding: utf-8 -*-
__all__ = ("CameraExposureDock",)

from PySide2 import QtCore, QtWidgets
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.util.widgets.slider import DoubleSlider
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.interface.simple_laser_interface import ControlMode


class CameraExposureDock(AdvancedDockWidget):
    """Camera exposure and timing control dock.

    Units:
    - Hardware Integration: Displayed in µs (microeconds), emitted in ns (nanoseconds)
    - Binning (NIntegFrames): Integer frame count, no units
    - Exposure (calculated): In seconds (from hardware.get_exposure())

    Unit conversion:
    - Clock cycles (10ns): value_ns / 10
    """

    sigCaptureBackground = QtCore.Signal()
    sigBackgroundSubtractionToggled = QtCore.Signal(bool)
    sigIntegrationChanged = QtCore.Signal(float)  # Value in seconds
    sigBinningChanged = QtCore.Signal(int)
    sigDisplayUnitsChanged = QtCore.Signal(str)  # 'counts' or 'cps'
    sigTriggerModeChanged = QtCore.Signal(str, int)  # (mode_str, frames_per_pulse)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # generate main widget and layout
        main_widget = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(15)
        main_widget.setLayout(main_layout)
        self.setWidget(main_widget)

        # Camera Mode Group
        mode_group_box = QtWidgets.QGroupBox("Camera Mode")
        mode_group_box.setMinimumHeight(70)
        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.setContentsMargins(10, 15, 10, 10)
        mode_group_box.setLayout(mode_layout)

        button_group = QtWidgets.QButtonGroup(self)
        self.normal_mode_radio_button = QtWidgets.QRadioButton("Normal")
        self.advanced_mode_radio_button = QtWidgets.QRadioButton("Advanced")
        button_group.addButton(self.normal_mode_radio_button)
        button_group.addButton(self.advanced_mode_radio_button)

        mode_layout.addWidget(self.normal_mode_radio_button)
        mode_layout.addWidget(self.advanced_mode_radio_button)

        # Disable mode buttons - mode can only be set in config and takes effect on activation
        self.normal_mode_radio_button.setEnabled(False)
        self.advanced_mode_radio_button.setEnabled(False)

        # Set normal mode as default
        self.normal_mode_radio_button.setChecked(True)

        main_layout.addWidget(mode_group_box)

        # Capture Background Button
        self.capture_background_button = QtWidgets.QPushButton(
            "Capture Background Image"
        )
        self.capture_background_button.setMinimumHeight(35)
        self.capture_background_button.clicked.connect(
            self._on_capture_background_clicked
        )
        main_layout.addWidget(self.capture_background_button)

        # Background Subtraction Toggle Button
        self.background_subtraction_button = QtWidgets.QPushButton(
            "Background Subtraction: OFF"
        )
        self.background_subtraction_button.setCheckable(True)
        self.background_subtraction_button.setMinimumHeight(35)
        self.background_subtraction_button.clicked[bool].connect(
            self._on_background_subtraction_toggled
        )
        main_layout.addWidget(self.background_subtraction_button)

        # Hardware Integration Group
        # Range: 10 ns to 655340 ns (1 to 65534 * 10 ns)
        # Display in microseconds for readability
        integration_group_box = QtWidgets.QGroupBox("Hardware Integration")
        integration_group_box.setMinimumHeight(100)
        integration_layout = QtWidgets.QVBoxLayout()
        integration_layout.setContentsMargins(10, 15, 10, 10)
        integration_layout.setSpacing(8)
        integration_group_box.setLayout(integration_layout)

        # Setpoint row with slider
        setpoint_row = QtWidgets.QHBoxLayout()
        setpoint_row.setSpacing(10)

        self.integration_spinbox = ScienDSpinBox()
        self.integration_spinbox.setDecimals(3)  # Show 3 decimal places for µs
        self.integration_spinbox.setMinimum(0.010)  # 10 ns = 0.010 µs
        self.integration_spinbox.setMaximum(655.340)  # 655340 ns = 655.340 µs
        self.integration_spinbox.setSingleStep(0.010)  # 10 ns steps
        self.integration_spinbox.setSuffix(" µs")
        self.integration_spinbox.setValue(10.40)  # Default to normal mode value
        self.integration_spinbox.valueChanged.connect(
            self._on_integration_spinbox_changed
        )

        setpoint_row.addWidget(self.integration_spinbox, 1)
        integration_layout.addLayout(setpoint_row)

        # Slider - values in nanoseconds internally
        self.integration_slider = DoubleSlider(QtCore.Qt.Horizontal)
        self.integration_slider.set_granularity(100000)  # High precision for 10ns steps
        self.integration_slider.setRange(10, 655340)  # 10 ns to 655340 ns
        self.integration_slider.setValue(10400)  # 10.40 µs in nanoseconds
        self.integration_slider.setMinimumHeight(25)
        self.integration_slider.setMaximumHeight(40)
        self.integration_slider.valueChanged.connect(
            self._on_integration_slider_changed
        )
        integration_layout.addWidget(self.integration_slider)

        main_layout.addWidget(integration_group_box)

        # Hardware Binning Group
        # Range: 1 to 65534 (no units)
        binning_group_box = QtWidgets.QGroupBox("Hardware Binning")
        binning_group_box.setMinimumHeight(100)
        binning_layout = QtWidgets.QVBoxLayout()
        binning_layout.setContentsMargins(10, 15, 10, 10)
        binning_layout.setSpacing(8)
        binning_group_box.setLayout(binning_layout)

        # Setpoint row
        binning_setpoint_row = QtWidgets.QHBoxLayout()
        binning_setpoint_row.setSpacing(10)

        self.binning_spinbox = ScienDSpinBox()
        self.binning_spinbox.setDecimals(0)
        self.binning_spinbox.setMinimum(1)
        self.binning_spinbox.setMaximum(65534)
        self.binning_spinbox.setValue(1)
        self.binning_spinbox.valueChanged.connect(self._on_binning_spinbox_changed)

        binning_setpoint_row.addWidget(self.binning_spinbox, 1)
        binning_layout.addLayout(binning_setpoint_row)

        # Slider
        self.binning_slider = DoubleSlider(QtCore.Qt.Horizontal)
        self.binning_slider.set_granularity(100000)  # High precision for full range
        self.binning_slider.setRange(1, 65534)
        self.binning_slider.setValue(1)
        self.binning_slider.setMinimumHeight(25)
        self.binning_slider.setMaximumHeight(40)
        self.binning_slider.valueChanged.connect(self._on_binning_slider_changed)
        binning_layout.addWidget(self.binning_slider)

        main_layout.addWidget(binning_group_box)

        # Display Units Group
        units_group_box = QtWidgets.QGroupBox("Display Units")
        units_group_box.setMinimumHeight(80)
        units_layout = QtWidgets.QVBoxLayout()
        units_layout.setContentsMargins(10, 15, 10, 10)
        units_layout.setSpacing(8)
        units_group_box.setLayout(units_layout)

        self.units_combo = QtWidgets.QComboBox()
        self.units_combo.addItems(["counts", "cps"])
        self.units_combo.currentTextChanged.connect(self._on_units_changed)
        units_layout.addWidget(self.units_combo)

        main_layout.addWidget(units_group_box)

        # Trigger Mode Group
        trigger_group_box = QtWidgets.QGroupBox("Trigger Mode")
        trigger_layout = QtWidgets.QVBoxLayout()
        trigger_layout.setContentsMargins(10, 15, 10, 10)
        trigger_layout.setSpacing(8)
        trigger_group_box.setLayout(trigger_layout)

        # Dropdown selector
        self.trigger_mode_combo = QtWidgets.QComboBox()
        self.trigger_mode_combo.addItem("No Trigger", userData="no_trigger")
        self.trigger_mode_combo.addItem("Single Trigger", userData="single_trigger")
        self.trigger_mode_combo.addItem("Multiple Trigger", userData="multiple_trigger")
        self.trigger_mode_combo.currentIndexChanged.connect(self._on_trigger_mode_changed)
        trigger_layout.addWidget(self.trigger_mode_combo)

        # Frames per pulse row (only relevant for Multiple Trigger)
        frames_row = QtWidgets.QHBoxLayout()
        frames_row.setSpacing(6)
        self.trigger_frames_label = QtWidgets.QLabel("Frames / pulse:")
        self.trigger_frames_spinbox = QtWidgets.QSpinBox()
        self.trigger_frames_spinbox.setMinimum(1)
        self.trigger_frames_spinbox.setMaximum(100)
        self.trigger_frames_spinbox.setValue(1)
        self.trigger_frames_spinbox.setEnabled(False)  # disabled until Multiple Trigger selected
        self.trigger_frames_spinbox.valueChanged.connect(self._on_trigger_frames_changed)
        frames_row.addWidget(self.trigger_frames_label)
        frames_row.addWidget(self.trigger_frames_spinbox)
        frames_row.addStretch(1)
        trigger_layout.addLayout(frames_row)

        # Status indicator label
        self.trigger_status_label = QtWidgets.QLabel("Active: No Trigger")
        self.trigger_status_label.setStyleSheet("color: gray; font-style: italic;")
        trigger_layout.addWidget(self.trigger_status_label)

        main_layout.addWidget(trigger_group_box)

        # Add stretch to push everything to the top
        main_layout.addStretch(1)

        # Set size policies to allow resizing
        main_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding
        )

        # Set minimum size for the dock widget itself
        self.setMinimumHeight(400)

        # Initialize in normal mode
        self._set_normal_mode()

    def _set_normal_mode(self):
        """Set controls to normal mode - integration locked to 10.40 µs"""
        # Lock integration to 10.40 µs (10400 ns)
        self.integration_spinbox.setValue(10.40)
        self.integration_spinbox.setEnabled(False)
        self.integration_slider.setValue(10400)
        self.integration_slider.setEnabled(False)

        # Enable binning controls
        self.binning_spinbox.setEnabled(True)
        self.binning_slider.setEnabled(True)

    def _set_advanced_mode(self):
        """Set controls to advanced mode - all controls enabled"""
        self.integration_spinbox.setEnabled(True)
        self.integration_slider.setEnabled(True)
        self.binning_spinbox.setEnabled(True)
        self.binning_slider.setEnabled(True)

    def _on_integration_spinbox_changed(self, value):
        """Sync slider when spinbox changes (value in µs)"""
        value_us = value
        # Round to nearest 0.01 µs for precision
        value_us = round(value_us * 100) / 100

        # Convert µs to seconds for internal consistency
        value_seconds = value_us * 1e-6

        self.integration_slider.blockSignals(True)
        self.integration_slider.setValue(
            value_us * 1000
        )  # Store slider in ns for display
        self.integration_slider.blockSignals(False)

        # Emit signal with value in seconds
        self.sigIntegrationChanged.emit(value_seconds)

    def _on_integration_slider_changed(self, value):
        """Sync spinbox when slider changes (convert ns to µs)"""
        # Round to nearest 10 ns
        value_ns = round(value / 10) * 10
        value_us = value_ns / 1000  # Convert ns to µs

        # Convert to seconds for emission
        value_seconds = value_us * 1e-6

        self.integration_spinbox.blockSignals(True)
        self.integration_spinbox.setValue(value_us)
        self.integration_spinbox.blockSignals(False)

        # Emit signal with value in seconds
        self.sigIntegrationChanged.emit(value_seconds)

    def _on_binning_spinbox_changed(self, value):
        """Sync slider when spinbox changes"""
        self.binning_slider.blockSignals(True)
        self.binning_slider.setValue(int(value))
        self.binning_slider.blockSignals(False)

        # Emit signal
        self.sigBinningChanged.emit(int(value))

    def _on_binning_slider_changed(self, value):
        """Sync spinbox when slider changes"""
        int_value = int(round(value))

        self.binning_spinbox.blockSignals(True)
        self.binning_spinbox.setValue(int_value)
        self.binning_spinbox.blockSignals(False)

        # Emit signal
        self.sigBinningChanged.emit(int_value)

    def _on_capture_background_clicked(self):
        """Handle capture background button click"""
        self.sigCaptureBackground.emit()

    def _on_background_subtraction_toggled(self, checked):
        """Handle background subtraction button toggle"""
        if checked:
            self.background_subtraction_button.setText("Background Subtraction: ON")
        else:
            self.background_subtraction_button.setText("Background Subtraction: OFF")
        self.sigBackgroundSubtractionToggled.emit(checked)

    def set_integration_value(self, value_ns):
        """Set integration value from external source (value in nanoseconds)"""
        value_us = value_ns / 1000
        self.integration_spinbox.blockSignals(True)
        self.integration_slider.blockSignals(True)
        self.integration_spinbox.setValue(value_us)
        self.integration_slider.setValue(value_ns)
        self.integration_spinbox.blockSignals(False)
        self.integration_slider.blockSignals(False)

    def set_binning_value(self, value):
        """Set binning value from external source"""
        self.binning_spinbox.blockSignals(True)
        self.binning_slider.blockSignals(True)
        self.binning_spinbox.setValue(value)
        self.binning_slider.setValue(value)
        self.binning_spinbox.blockSignals(False)
        self.binning_slider.blockSignals(False)

    def _on_units_changed(self, units):
        """Handle display units change"""
        self.sigDisplayUnitsChanged.emit(units)

    def set_units_value(self, units):
        """Set display units from external source"""
        self.units_combo.blockSignals(True)
        index = self.units_combo.findText(units)
        if index >= 0:
            self.units_combo.setCurrentIndex(index)
        self.units_combo.blockSignals(False)

    def _on_trigger_mode_changed(self, index):
        """Handle trigger mode combo box change"""
        mode = self.trigger_mode_combo.itemData(index)
        is_multiple = (mode == "multiple_trigger")
        self.trigger_frames_spinbox.setEnabled(is_multiple)
        self.trigger_frames_label.setEnabled(is_multiple)
        frames = self.trigger_frames_spinbox.value() if is_multiple else 1
        self.sigTriggerModeChanged.emit(mode, frames)

    def _on_trigger_frames_changed(self, value):
        """Handle frames per pulse spinbox change"""
        mode = self.trigger_mode_combo.currentData()
        if mode == "multiple_trigger":
            self.sigTriggerModeChanged.emit(mode, value)

    def set_trigger_mode(self, mode, frames_per_pulse=1):
        """Set trigger mode from external source without emitting signal.

        @param str mode: 'no_trigger', 'single_trigger', or 'multiple_trigger'
        @param int frames_per_pulse: Frames per pulse (1-100)
        """
        self.trigger_mode_combo.blockSignals(True)
        self.trigger_frames_spinbox.blockSignals(True)

        index = self.trigger_mode_combo.findData(mode)
        if index >= 0:
            self.trigger_mode_combo.setCurrentIndex(index)

        self.trigger_frames_spinbox.setValue(max(1, min(int(frames_per_pulse), 100)))
        is_multiple = (mode == "multiple_trigger")
        self.trigger_frames_spinbox.setEnabled(is_multiple)
        self.trigger_frames_label.setEnabled(is_multiple)

        self.trigger_mode_combo.blockSignals(False)
        self.trigger_frames_spinbox.blockSignals(False)

        self._update_trigger_status_label(mode, frames_per_pulse)

    def _update_trigger_status_label(self, mode, frames_per_pulse=1):
        """Update the status indicator label text and colour."""
        if mode == "no_trigger":
            text = "Active: No Trigger"
            colour = "gray"
        elif mode == "single_trigger":
            text = "Active: Single Trigger"
            colour = "#0a7abf"
        elif mode == "multiple_trigger":
            text = f"Active: Multiple Trigger ({frames_per_pulse} frames/pulse)"
            colour = "#0a7abf"
        else:
            text = f"Active: {mode}"
            colour = "gray"
        self.trigger_status_label.setText(text)
        self.trigger_status_label.setStyleSheet(f"color: {colour}; font-style: italic;")

    def get_integration_value_ns(self):
        """Get current integration value in nanoseconds (deprecated, use get_integration_value_seconds)"""
        return int(self.integration_spinbox.value() * 1000)

    def get_integration_value_seconds(self):
        """Get current integration value in seconds"""
        return self.integration_spinbox.value() * 1e-6

    def get_binning_value(self):
        """Get current binning value"""
        return int(self.binning_spinbox.value())
