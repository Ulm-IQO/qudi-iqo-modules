# -*- coding: utf-8 -*-
"""
This module contains a GUI for operating the spectrometer camera logic module.

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

import os
from PySide2 import QtCore, QtWidgets, QtGui
import datetime

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util.widgets.plotting.image_widget import ImageWidget
from qudi.util.datastorage import TextDataStorage
from qudi.util.paths import get_artwork_dir
from qudi.gui.camera.camera_settings_dialog import CameraSettingsDialog
from .camera_exposure_settings_SPC3 import CameraExposureDock


class _SnapWorker(QtCore.QObject):
    """Worker that runs the blocking snap acquisition off the GUI thread."""

    sigFinished = QtCore.Signal(object)  # emits the frames array (or None)

    def __init__(self, logic):
        super().__init__()
        self._logic = logic

    @QtCore.Slot()
    def run(self):
        try:
            frames = self._logic.start_single_acquisition()
        except Exception:
            frames = None
        self.sigFinished.emit(frames)


class CameraMainWindow(QtWidgets.QMainWindow):
    """QMainWindow object for qudi CameraGui module"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Create menu bar
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu("File")
        self.action_save_frame = QtWidgets.QAction("Save Frame")
        path = os.path.join(get_artwork_dir(), "icons", "document-save")
        self.action_save_frame.setIcon(QtGui.QIcon(path))
        menu.addAction(self.action_save_frame)
        menu.addSeparator()
        self.action_load_acquisition = QtWidgets.QAction("Load SPC3 File...")
        path = os.path.join(get_artwork_dir(), "icons", "document-open")
        self.action_load_acquisition.setIcon(QtGui.QIcon(path))
        menu.addAction(self.action_load_acquisition)
        menu.addSeparator()
        self.action_show_settings = QtWidgets.QAction("Settings")
        path = os.path.join(get_artwork_dir(), "icons", "configure")
        self.action_show_settings.setIcon(QtGui.QIcon(path))
        menu.addAction(self.action_show_settings)
        menu.addSeparator()
        self.action_close = QtWidgets.QAction("Close")
        path = os.path.join(get_artwork_dir(), "icons", "application-exit")
        self.action_close.setIcon(QtGui.QIcon(path))
        self.action_close.triggered.connect(self.close)
        menu.addAction(self.action_close)
        self.setMenuBar(menu_bar)

        self.action_view_controls = QtWidgets.QAction("Show Controls")
        self.action_view_controls.setCheckable(True)
        self.action_view_controls.setChecked(True)
        menu.addAction(self.action_view_controls)

        # Create toolbar
        toolbar = QtWidgets.QToolBar()
        toolbar.setAllowedAreas(QtCore.Qt.AllToolBarAreas)
        self.action_start_video = QtWidgets.QAction("Start Video")
        self.action_start_video.setCheckable(True)
        toolbar.addAction(self.action_start_video)

        # Add snap button
        self.action_snap = QtWidgets.QAction("Snap")
        toolbar.addAction(self.action_snap)

        # Replace capture frame with continuous acquisition
        self.action_continuous_acquisition = QtWidgets.QAction(
            "Start Continuous Acquisition"
        )
        self.action_continuous_acquisition.setCheckable(True)
        toolbar.addAction(self.action_continuous_acquisition)

        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        self.settings_dockwidget = CameraSettingsDockWidget()
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.settings_dockwidget)

        # Create central widget
        self.image_widget = ImageWidget()
        # FIXME: The camera hardware is currently transposing the image leading to this dirty hack
        self.image_widget.image_item.setOpts(False, axisOrder="row-major")
        self.setCentralWidget(self.image_widget)


class ContinuousAcquisitionDialog(QtWidgets.QDialog):
    """Dialog for setting up continuous acquisition parameters"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Continuous Acquisition Settings")
        self.setMinimumWidth(500)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # File path section
        path_group = QtWidgets.QGroupBox("Save Location")
        path_layout = QtWidgets.QVBoxLayout()
        path_group.setLayout(path_layout)

        # Directory selection
        dir_layout = QtWidgets.QHBoxLayout()
        dir_label = QtWidgets.QLabel("Directory:")
        self.dir_line_edit = QtWidgets.QLineEdit()
        self.dir_line_edit.setPlaceholderText("Select directory for saved files...")
        self.dir_browse_button = QtWidgets.QPushButton("Browse...")
        self.dir_browse_button.clicked.connect(self._browse_directory)
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_line_edit, 1)
        dir_layout.addWidget(self.dir_browse_button)
        path_layout.addLayout(dir_layout)

        # Filename prefix
        filename_layout = QtWidgets.QHBoxLayout()
        filename_label = QtWidgets.QLabel("Filename Prefix:")
        self.filename_line_edit = QtWidgets.QLineEdit()
        self.filename_line_edit.setPlaceholderText("frame")
        self.filename_line_edit.setText("frame")
        filename_layout.addWidget(filename_label)
        filename_layout.addWidget(self.filename_line_edit, 1)
        path_layout.addLayout(filename_layout)

        # Info label
        info_label = QtWidgets.QLabel("Files will be saved as: <prefix>.spc3")
        info_label.setStyleSheet("color: gray; font-style: italic;")
        path_layout.addWidget(info_label)

        layout.addWidget(path_group)

        # Button box
        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _browse_directory(self):
        """Open directory browser"""
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Directory for Continuous Acquisition",
            self.dir_line_edit.text() or os.path.expanduser("~"),
        )
        if directory:
            self.dir_line_edit.setText(directory)

    def get_settings(self):
        """Return the configured settings"""
        return {
            "directory": self.dir_line_edit.text(),
            "filename_prefix": self.filename_line_edit.text() or "frame",
        }


class AcquisitionViewerDialog(QtWidgets.QDialog):
    """Dialog for viewing frames from a continuous acquisition file"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Continuous Acquisition Viewer")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        self._frame_count = 0
        self._current_frame = 0

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # File info section
        info_group = QtWidgets.QGroupBox("File Information")
        info_layout = QtWidgets.QVBoxLayout()
        info_group.setLayout(info_layout)

        self.filepath_label = QtWidgets.QLabel("No file loaded")
        self.filepath_label.setWordWrap(True)
        info_layout.addWidget(self.filepath_label)

        self.frame_info_label = QtWidgets.QLabel("Frames: 0")
        info_layout.addWidget(self.frame_info_label)

        layout.addWidget(info_group)

        # Frame navigation section
        nav_group = QtWidgets.QGroupBox("Frame Navigation")
        nav_layout = QtWidgets.QVBoxLayout()
        nav_group.setLayout(nav_layout)

        # Frame counter display
        self.frame_counter_label = QtWidgets.QLabel("Frame: 0 / 0")
        self.frame_counter_label.setAlignment(QtCore.Qt.AlignCenter)
        nav_layout.addWidget(self.frame_counter_label)

        # Slider
        slider_layout = QtWidgets.QHBoxLayout()
        slider_layout.addWidget(QtWidgets.QLabel("Frame:"))
        self.frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.setValue(0)
        self.frame_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.frame_slider.setTickInterval(10)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        slider_layout.addWidget(self.frame_slider, 1)
        nav_layout.addLayout(slider_layout)

        # Navigation buttons
        button_layout = QtWidgets.QHBoxLayout()
        self.first_button = QtWidgets.QPushButton("⏮ First")
        self.first_button.clicked.connect(self._on_first_clicked)
        self.prev_button = QtWidgets.QPushButton("◀ Previous")
        self.prev_button.clicked.connect(self._on_prev_clicked)
        self.next_button = QtWidgets.QPushButton("Next ▶")
        self.next_button.clicked.connect(self._on_next_clicked)
        self.last_button = QtWidgets.QPushButton("Last ⏭")
        self.last_button.clicked.connect(self._on_last_clicked)
        button_layout.addWidget(self.first_button)
        button_layout.addWidget(self.prev_button)
        button_layout.addWidget(self.next_button)
        button_layout.addWidget(self.last_button)
        nav_layout.addLayout(button_layout)

        layout.addWidget(nav_group)

        # Close button
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self._update_button_states()

    def set_file_info(self, filepath, frame_count):
        """Update file information display"""
        self._frame_count = frame_count
        self.filepath_label.setText(f"File: {filepath}")
        self.frame_info_label.setText(f"Frames: {frame_count}")
        self.frame_slider.setMaximum(max(0, frame_count - 1))
        self.frame_slider.setValue(0)
        self._current_frame = 0
        self._update_frame_counter()
        self._update_button_states()

    def _update_frame_counter(self):
        """Update frame counter label"""
        if self._frame_count > 0:
            self.frame_counter_label.setText(
                f"Frame: {self._current_frame + 1} / {self._frame_count}"
            )
        else:
            self.frame_counter_label.setText("Frame: 0 / 0")

    def _update_button_states(self):
        """Enable/disable navigation buttons based on current frame"""
        has_frames = self._frame_count > 0
        at_first = self._current_frame == 0
        at_last = self._current_frame >= self._frame_count - 1

        self.first_button.setEnabled(has_frames and not at_first)
        self.prev_button.setEnabled(has_frames and not at_first)
        self.next_button.setEnabled(has_frames and not at_last)
        self.last_button.setEnabled(has_frames and not at_last)
        self.frame_slider.setEnabled(has_frames)

    def _on_slider_changed(self, value):
        """Handle slider value change"""
        self._current_frame = value
        self._update_frame_counter()
        self._update_button_states()

    def _on_first_clicked(self):
        """Jump to first frame"""
        self.frame_slider.setValue(0)

    def _on_prev_clicked(self):
        """Go to previous frame"""
        if self._current_frame > 0:
            self.frame_slider.setValue(self._current_frame - 1)

    def _on_next_clicked(self):
        """Go to next frame"""
        if self._current_frame < self._frame_count - 1:
            self.frame_slider.setValue(self._current_frame + 1)

    def _on_last_clicked(self):
        """Jump to last frame"""
        self.frame_slider.setValue(self._frame_count - 1)

    def get_current_frame_index(self):
        """Return the currently selected frame index"""
        return self._current_frame


class CameraSettingsDockWidget(QtWidgets.QDockWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle("Camera settings")

        # Create widget and layout
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout()
        widget.setLayout(layout)
        self.setWidget(widget)

        # Add snap frames spinbox
        self.snap_frames_spinbox = QtWidgets.QSpinBox()
        self.snap_frames_spinbox.setMinimum(1)
        self.snap_frames_spinbox.setMaximum(65534)
        self.snap_frames_spinbox.setValue(100)
        self.snap_frames_spinbox.setToolTip("Number of frames to acquire in snap mode")
        layout.addRow("Snap Frames:", self.snap_frames_spinbox)


class CameraGui(GuiBase):
    """Main camera gui class.

    Example config for copy-paste:

    camera_gui:
        module.Class: 'camera.cameragui.CameraGui'
        connect:
            camera_logic: camera_logic

    """

    _camera_logic = Connector(name="camera_logic", interface="CameraLogic")

    sigStartStopVideoToggled = QtCore.Signal(bool)
    sigContinuousAcquisitionToggled = QtCore.Signal(bool, dict)  # (enabled, settings)
    sigBackgroundSubtractionToggled = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._settings_dialog = None
        self._continuous_acq_dialog = None
        self._continuous_acq_settings = None
        self._viewer_dialog = None
        self._snap_frames = None  # Store in-memory snap frames

    def on_activate(self):
        """Initializes all needed UI files and establishes the connectors."""
        logic = self._camera_logic()
        camera = logic._camera()

        # Create main window
        self._mw = CameraMainWindow()

        # Create settings dialog
        self._settings_dialog = CameraSettingsDialog(self._mw)

        # Create continuous acquisition dialog
        self._continuous_acq_dialog = ContinuousAcquisitionDialog(self._mw)

        # Create acquisition viewer dialog
        self._viewer_dialog = AcquisitionViewerDialog(self._mw)

        # Connect the action of the settings dialog with this module
        self._settings_dialog.accepted.connect(self._update_settings)
        self._settings_dialog.rejected.connect(self._keep_former_settings)
        self._settings_dialog.button_box.button(
            QtWidgets.QDialogButtonBox.Apply
        ).clicked.connect(self._update_settings)

        self.control_dock_widget = CameraExposureDock()
        self.control_dock_widget.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
        )
        self.control_dock_widget.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self._mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.control_dock_widget)

        self.control_dock_widget.visibilityChanged.connect(
            self._mw.action_view_controls.setChecked
        )
        self._mw.action_view_controls.triggered[bool].connect(
            self.control_dock_widget.setVisible
        )

        # Check if background subtraction is available
        bg_sub_available = (
            hasattr(camera, "capture_background_image")
            and callable(getattr(camera, "capture_background_image", None))
            and hasattr(camera, "enable_background_subtraction")
            and callable(getattr(camera, "enable_background_subtraction", None))
        )

        if not bg_sub_available:
            # Hide/disable background subtraction if not available
            self.control_dock_widget.capture_background_button.setEnabled(False)
            self.control_dock_widget.capture_background_button.setToolTip(
                "Background subtraction not available for this camera"
            )
            self.control_dock_widget.background_subtraction_button.setEnabled(False)
            self.control_dock_widget.background_subtraction_button.setToolTip(
                "Background subtraction not available for this camera"
            )
            self.log.info(
                "Background subtraction not available - feature disabled in GUI"
            )
        else:
            # Connect background subtraction signals
            self.control_dock_widget.sigCaptureBackground.connect(
                self._capture_background_clicked
            )
            self.control_dock_widget.sigBackgroundSubtractionToggled.connect(
                self._background_subtraction_toggled
            )

        self.control_dock_widget.sigIntegrationChanged.connect(self._update_integration)
        self.control_dock_widget.sigBinningChanged.connect(self._update_binning)
        self.control_dock_widget.sigDisplayUnitsChanged.connect(
            self._update_display_units
        )
        self.control_dock_widget.sigTriggerModeChanged.connect(
            self._update_trigger_mode
        )

        # Fill in data from logic
        logic_busy = logic.module_state() == "locked"
        self._mw.action_start_video.setChecked(logic_busy)
        self._mw.action_continuous_acquisition.setChecked(False)
        self._update_frame(logic.last_frame)
        self._keep_former_settings()

        # Initialize default save directory from hardware config
        try:
            default_dir = logic.get_default_save_directory()
            if default_dir:
                self._continuous_acq_dialog.dir_line_edit.setText(default_dir)
        except Exception as e:
            self.log.warning(f"Could not initialize default save directory: {e}")

        # Initialize display units from hardware
        try:
            units = logic.get_display_units()
            self.control_dock_widget.set_units_value(units)
            self._update_colorbar_label(units)
        except Exception as e:
            self.log.warning(f"Could not initialize display units: {e}")

        # Initialize trigger mode from hardware
        try:
            trigger_mode = logic.get_trigger_mode()
            trigger_frames = logic.get_trigger_frames_per_pulse()
            self.control_dock_widget.set_trigger_mode(trigger_mode, trigger_frames)
        except Exception as e:
            self.log.warning(f"Could not initialize trigger mode: {e}")

        # Initialize snap frames from hardware
        try:
            if hasattr(camera, "_NFrames"):
                self._mw.settings_dockwidget.snap_frames_spinbox.setValue(
                    camera._NFrames
                )
        except Exception as e:
            self.log.warning(f"Could not initialize snap frames: {e}")

        # Connect snap frames spinbox
        self._mw.settings_dockwidget.snap_frames_spinbox.valueChanged.connect(
            self._update_snap_frames
        )

        # Update camera mode display and controls from hardware config
        try:
            if hasattr(camera, "_camera_mode"):
                if camera._camera_mode == "Advanced":
                    self.control_dock_widget.advanced_mode_radio_button.setChecked(True)
                    self.control_dock_widget._set_advanced_mode()
                    # Update integration time from hardware (convert cycles to nanoseconds)
                    if hasattr(camera, "_HardwareIntegration"):
                        integration_ns = (
                            camera._HardwareIntegration * 10
                        )  # 10ns per clock cycle
                        self.control_dock_widget.set_integration_value(integration_ns)
                    # Update binning from logic
                    try:
                        binning = logic.get_binning()
                        self.control_dock_widget.set_binning_value(binning)
                    except Exception as e:
                        self.log.warning(f"Could not get initial binning: {e}")
                else:
                    self.control_dock_widget.normal_mode_radio_button.setChecked(True)
                    self.control_dock_widget._set_normal_mode()
                    # Update binning from logic (also available in Normal mode)
                    try:
                        binning = logic.get_binning()
                        self.control_dock_widget.set_binning_value(binning)
                    except Exception as e:
                        self.log.warning(f"Could not get initial binning: {e}")
        except Exception as e:
            self.log.warning(f"Could not initialize camera mode display: {e}")

        # connect main window actions
        self._mw.action_start_video.triggered[bool].connect(self._start_video_clicked)
        self._mw.action_snap.triggered.connect(self._snap_clicked)
        self._mw.action_continuous_acquisition.triggered[bool].connect(
            self._continuous_acquisition_clicked
        )
        self._mw.action_show_settings.triggered.connect(
            lambda: self._settings_dialog.exec_()
        )
        self._mw.action_load_acquisition.triggered.connect(
            self._load_acquisition_clicked
        )
        self._mw.action_save_frame.triggered.connect(self._save_frame)

        # connect update signals from logic
        logic.sigFrameChanged.connect(self._update_frame)
        logic.sigAcquisitionFinished.connect(self._acquisition_finished)

        # connect GUI signals to logic slots
        self.sigStartStopVideoToggled.connect(logic.toggle_video)

        self.sigContinuousAcquisitionToggled.connect(
            logic.toggle_continuous_acquisition
        )
        if bg_sub_available:
            self.sigBackgroundSubtractionToggled.connect(
                logic.toggle_background_subtraction
            )

        self.show()

    def on_deactivate(self):
        """De-initialisation performed during deactivation of the module."""
        logic = self._camera_logic()
        # disconnect all signals
        self.sigContinuousAcquisitionToggled.disconnect()
        self.sigStartStopVideoToggled.disconnect()

        # Only disconnect background subtraction if it was connected
        camera = logic._camera()
        bg_sub_available = (
            hasattr(camera, "capture_background_image")
            and callable(getattr(camera, "capture_background_image", None))
            and hasattr(camera, "enable_background_subtraction")
            and callable(getattr(camera, "enable_background_subtraction", None))
        )
        if bg_sub_available:
            try:
                self.control_dock_widget.sigCaptureBackground.disconnect()
            except RuntimeError:
                pass  # Already disconnected
            try:
                self.sigBackgroundSubtractionToggled.disconnect()
            except RuntimeError:
                pass  # Already disconnected

        logic.sigAcquisitionFinished.disconnect(self._acquisition_finished)
        logic.sigFrameChanged.disconnect(self._update_frame)
        self._mw.action_save_frame.triggered.disconnect()
        self._mw.action_snap.triggered.disconnect()
        self._mw.action_show_settings.triggered.disconnect()
        self._mw.action_load_acquisition.triggered.disconnect()
        self._mw.action_continuous_acquisition.triggered.disconnect()
        self._mw.action_start_video.triggered.disconnect()

        if bg_sub_available:
            try:
                self.control_dock_widget.sigBackgroundSubtractionToggled.disconnect()
            except RuntimeError:
                pass  # Already disconnected

        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows."""
        self._mw.show()
        self._mw.raise_()
        self._mw.activateWindow()

    def _update_settings(self):
        """Write new settings from the gui to the file."""
        logic = self._camera_logic()
        logic.set_exposure(self._settings_dialog.exposure_spinbox.value())
        logic.set_gain(self._settings_dialog.gain_spinbox.value())

    def _update_integration(self, integration_seconds):
        """Update hardware integration time in SECONDS

        Hardware internally uses CLOCK CYCLES (10ns each) - conversion handled by hardware module.
        """
        logic = self._camera_logic()
        logic.set_integration(integration_seconds)
        # Update exposure display in settings dialog
        self._settings_dialog.exposure_spinbox.setValue(logic.get_exposure())

    def _update_binning(self, binning):
        """Update hardware binning (NIntegFrames)"""
        logic = self._camera_logic()
        logic.set_binning(binning)
        # Update exposure display in settings dialog
        self._settings_dialog.exposure_spinbox.setValue(logic.get_exposure())

    def _update_display_units(self, units):
        """Update display units (counts or cps)"""
        logic = self._camera_logic()
        if logic.set_display_units(units):
            self._update_colorbar_label(units)

    def _update_trigger_mode(self, mode, frames_per_pulse):
        """Apply trigger mode change from GUI to hardware and update status label."""
        logic = self._camera_logic()
        try:
            logic.set_trigger_mode(mode, frames_per_pulse)
            # Reflect the committed state back on the status label
            self.control_dock_widget._update_trigger_status_label(
                mode, frames_per_pulse
            )
        except Exception as e:
            self.log.warning(f"Could not set trigger mode: {e}")

    def _update_snap_frames(self, num_frames):
        """Update number of frames for snap acquisition"""
        logic = self._camera_logic()
        logic.set_snap_frames(num_frames)

    def _update_colorbar_label(self, units):
        """Update colorbar label with current units"""
        if hasattr(self._mw, "_image_widget") and hasattr(
            self._mw._image_widget, "colorbar"
        ):
            if units == "cps":
                self._mw._image_widget.colorbar.setLabel("Counts/s")
            else:
                self._mw._image_widget.colorbar.setLabel("Counts")

    def _keep_former_settings(self):
        """Keep the old settings and restores them in the gui."""
        logic = self._camera_logic()
        self._settings_dialog.exposure_spinbox.setValue(logic.get_exposure())
        self._settings_dialog.gain_spinbox.setValue(logic.get_gain())

    def _continuous_acquisition_clicked(self, checked):
        """Handle continuous acquisition button click"""
        if checked:
            # Show dialog to get settings
            if self._continuous_acq_dialog.exec_() == QtWidgets.QDialog.Accepted:
                self._continuous_acq_settings = (
                    self._continuous_acq_dialog.get_settings()
                )

                # Validate settings
                if not self._continuous_acq_settings["directory"]:
                    QtWidgets.QMessageBox.warning(
                        self._mw,
                        "Invalid Settings",
                        "Please select a directory for saving files.",
                    )
                    self._mw.action_continuous_acquisition.setChecked(False)
                    return

                # Disable other controls
                self._mw.action_start_video.setDisabled(True)
                self._mw.action_show_settings.setDisabled(True)
                self._mw.action_continuous_acquisition.setText(
                    "Stop Continuous Acquisition"
                )

                # Emit signal to start continuous acquisition
                self.sigContinuousAcquisitionToggled.emit(
                    True, self._continuous_acq_settings
                )
            else:
                # User cancelled dialog
                self._mw.action_continuous_acquisition.setChecked(False)
        else:
            # Stop continuous acquisition
            self._mw.action_continuous_acquisition.setText(
                "Start Continuous Acquisition"
            )
            self._mw.action_start_video.setEnabled(True)
            self._mw.action_show_settings.setEnabled(True)
            self.sigContinuousAcquisitionToggled.emit(False, {})

            # Offer to view the saved file
            settings = getattr(self, "_continuous_acq_settings", {})
            if settings.get("directory") and settings.get("filename_prefix"):
                saved_path = os.path.join(
                    settings["directory"],
                    settings["filename_prefix"] + ".spc3",
                )
                if os.path.exists(saved_path):
                    reply = QtWidgets.QMessageBox.question(
                        self._mw,
                        "View Acquisition",
                        f"Acquisition stopped.\n\nWould you like to view the saved file?\n{saved_path}",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.Yes,
                    )
                    if reply == QtWidgets.QMessageBox.Yes:
                        self._load_file_into_viewer(saved_path)

    def _acquisition_finished(self):
        self._mw.action_start_video.setChecked(False)
        self._mw.action_start_video.setEnabled(True)
        self._mw.action_continuous_acquisition.setChecked(False)
        self._mw.action_continuous_acquisition.setEnabled(True)
        self._mw.action_continuous_acquisition.setText("Start Continuous Acquisition")
        self._mw.action_show_settings.setEnabled(True)

    def _load_acquisition_clicked(self):
        """Handle load acquisition menu action"""
        # Open file dialog to select .spc3 file
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._mw,
            "Load Acquisition File",
            os.path.expanduser("~"),
            "SPC3 Files (*.spc3);;All Files (*.*)",
        )

        if not filepath:
            return  # User cancelled

        self._load_file_into_viewer(filepath)

    def _load_file_into_viewer(self, filepath):
        """Load a .spc3 file and open the viewer dialog"""
        # Load the file in logic layer
        logic = self._camera_logic()
        success = logic.load_acquisition_file(filepath)

        if not success:
            QtWidgets.QMessageBox.critical(
                self._mw,
                "Load Error",
                f"Failed to load file:\n{filepath}\n\nCheck log for details.",
            )
            return

        # Get frame count
        frame_count = logic.get_loaded_frame_count()
        if frame_count == 0:
            QtWidgets.QMessageBox.warning(
                self._mw, "No Frames", "The loaded file contains no frames."
            )
            return

        # Update viewer dialog with file info
        self._viewer_dialog.set_file_info(filepath, frame_count)

        # Connect slider to frame update
        try:
            self._viewer_dialog.frame_slider.valueChanged.disconnect(
                self._on_viewer_frame_changed
            )
        except:
            pass  # Not connected yet

        self._viewer_dialog.frame_slider.valueChanged.connect(
            self._on_viewer_frame_changed
        )

        # Show first frame
        self._on_viewer_frame_changed(0)

        # Show the viewer dialog
        self._viewer_dialog.exec_()

    def _on_viewer_frame_changed(self, frame_index):
        """Handle frame selection change in viewer"""
        logic = self._camera_logic()
        frame = logic.get_loaded_frame(frame_index)

        if frame is not None:
            # Update the dialog's internal state (frame counter and buttons)
            self._viewer_dialog._current_frame = frame_index
            self._viewer_dialog._update_frame_counter()
            self._viewer_dialog._update_button_states()

            # Apply background subtraction if enabled (same as live/continuous display)
            frame = logic.apply_background_subtraction(frame)

            # Update the main image display with the selected frame
            self._mw.image_widget.set_image(frame)

    def _display_snap_frames_in_viewer(self, frames, filepath=None):
        """Display snap frames (from memory) in the viewer dialog

        @param frames: Acquired frames array (counters, frames, rows, cols)
        @param filepath: Optional file path if frames were saved to disk
        """
        # frames shape: (counters, frames, rows, cols)
        num_frames = frames.shape[1]

        # Update viewer dialog with frame info
        display_path = filepath if filepath else "(In Memory - Not Saved)"
        self._viewer_dialog.filepath_label.setText(f"File: {display_path}")
        self._viewer_dialog.frame_info_label.setText(f"Frames: {num_frames}")
        self._viewer_dialog._frame_count = num_frames
        self._viewer_dialog.frame_slider.setMaximum(max(0, num_frames - 1))
        self._viewer_dialog.frame_slider.setValue(0)
        self._viewer_dialog._current_frame = 0
        self._viewer_dialog._update_frame_counter()
        self._viewer_dialog._update_button_states()

        # Disconnect old signal and connect to memory-based frame display
        try:
            self._viewer_dialog.frame_slider.valueChanged.disconnect()
        except:
            pass

        # Create lambda that captures frames from memory
        self._viewer_dialog.frame_slider.valueChanged.connect(
            lambda idx: self._display_snap_frame_by_index(frames, idx)
        )

        # Display first frame
        self._display_snap_frame_by_index(frames, 0)

        # Show viewer dialog
        self._viewer_dialog.exec_()

    def _display_snap_frame_by_index(self, frames, frame_index):
        """Display a specific frame from in-memory snap frames

        @param frames: Frames array (counters, frames, rows, cols)
        @param frame_index: Index of frame to display (0-based)
        """
        if frames is None or frame_index < 0 or frame_index >= frames.shape[1]:
            return

        # Extract frame (counter 0, specific frame index, all rows/cols)
        frame = frames[0, frame_index, :, :]

        # Update dialog state
        self._viewer_dialog._current_frame = frame_index
        self._viewer_dialog._update_frame_counter()
        self._viewer_dialog._update_button_states()

        # Apply background subtraction if enabled (same as live/continuous display)
        frame = self._camera_logic().apply_background_subtraction(frame)

        # Display the frame
        self._mw.image_widget.set_image(frame)

    def _snap_clicked(self):
        """Handle snap button click — start acquisition in a background thread."""
        # Disable controls while acquiring
        self._mw.action_snap.setEnabled(False)
        self._mw.action_start_video.setEnabled(False)
        self._mw.action_continuous_acquisition.setEnabled(False)
        self._mw.statusBar().showMessage("Snap acquisition in progress…")

        logic = self._camera_logic()

        # Spin up a worker thread so the GUI stays responsive
        self._snap_thread = QtCore.QThread()
        self._snap_worker = _SnapWorker(logic)
        self._snap_worker.moveToThread(self._snap_thread)
        self._snap_thread.started.connect(self._snap_worker.run)
        self._snap_worker.sigFinished.connect(self._snap_finished)
        self._snap_worker.sigFinished.connect(self._snap_thread.quit)
        self._snap_thread.start()

    @QtCore.Slot(object)
    def _snap_finished(self, frames):
        """Called on the GUI thread when the background snap is done."""
        # Re-enable controls
        self._mw.action_snap.setEnabled(True)
        self._mw.action_start_video.setEnabled(True)
        self._mw.action_continuous_acquisition.setEnabled(True)
        self._mw.statusBar().clearMessage()

        logic = self._camera_logic()
        camera = logic._camera()

        if frames is None:
            QtWidgets.QMessageBox.critical(
                self._mw,
                "Snap Error",
                "Failed to perform snap acquisition.\n\nCheck log for details.",
            )
            return

        # Get actual requested frame count from hardware (array may have buffer data)
        num_frames = camera._NFrames if hasattr(camera, "_NFrames") else frames.shape[1]

        # Crop to requested frames and store in memory
        self._snap_frames = frames[:, :num_frames, :, :]

        # Ask if user wants to save
        reply = QtWidgets.QMessageBox.question(
            self._mw,
            "Snap Complete",
            f"Snap acquisition complete ({num_frames} frames).\n\nWould you like to save to file?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )

        filepath = None
        if reply == QtWidgets.QMessageBox.Yes:
            filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
                self._mw,
                "Save Snap Acquisition",
                os.path.expanduser("~"),
                "SPC3 Files (*.spc3);;All Files (*.*)",
            )
            if filepath:
                if not filepath.lower().endswith(".spc3"):
                    filepath += ".spc3"
                success = logic.save_frames_to_file(self._snap_frames, filepath)
                if not success:
                    QtWidgets.QMessageBox.warning(
                        self._mw,
                        "Save Error",
                        "Failed to save frames to file.\n\nCheck log for details.",
                    )
                    filepath = None

        # Ask if user wants to view frames
        view_reply = QtWidgets.QMessageBox.question(
            self._mw,
            "View Frames",
            "Would you like to view the acquired frames?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )

        if view_reply == QtWidgets.QMessageBox.Yes:
            if filepath:
                if logic.load_acquisition_file(filepath):
                    frame_count = logic.get_loaded_frame_count()
                    self._viewer_dialog.set_file_info(filepath, frame_count)
                    try:
                        self._viewer_dialog.frame_slider.valueChanged.disconnect(
                            self._on_viewer_frame_changed
                        )
                    except Exception:
                        pass
                    self._viewer_dialog.frame_slider.valueChanged.connect(
                        self._on_viewer_frame_changed
                    )
                    self._on_viewer_frame_changed(0)
                    self._viewer_dialog.exec_()
            else:
                if logic.load_frames_from_memory(frames):
                    frame_count = num_frames
                    filepath_display = logic.get_loaded_filepath()
                    self._viewer_dialog.set_file_info(filepath_display, frame_count)
                    try:
                        self._viewer_dialog.frame_slider.valueChanged.disconnect(
                            self._on_viewer_frame_changed
                        )
                    except Exception:
                        pass
                    self._viewer_dialog.frame_slider.valueChanged.connect(
                        self._on_viewer_frame_changed
                    )
                    self._on_viewer_frame_changed(0)
                    self._viewer_dialog.exec_()
                else:
                    QtWidgets.QMessageBox.warning(
                        self._mw, "View Error", "Failed to load frames for viewing."
                    )

    def _start_video_clicked(self, checked):
        if checked:
            self._mw.action_show_settings.setDisabled(True)
            self._mw.action_continuous_acquisition.setDisabled(True)
            self._mw.action_start_video.setText("Stop Video")
        else:
            self._mw.action_start_video.setText("Start Video")
        self.sigStartStopVideoToggled.emit(checked)

    def _update_frame(self, frame_data):
        """ """
        self._mw.image_widget.set_image(frame_data)

    def _capture_background_clicked(self):
        """Handle capture background button click"""
        logic = self._camera_logic()
        success = logic.capture_background_image()
        if not success:
            self.log.warning("Failed to capture background image")

    def _background_subtraction_toggled(self, enabled):
        """Handle background subtraction toggle from control dock"""
        self.sigBackgroundSubtractionToggled.emit(enabled)

    def _save_frame(self):
        logic = self._camera_logic()
        ds = TextDataStorage(root_dir=self.module_default_data_dir)
        timestamp = datetime.datetime.now()
        tag = logic.create_tag(timestamp)

        parameters = {}
        parameters["gain"] = logic.get_gain()
        parameters["exposure"] = logic.get_exposure()

        data = logic.last_frame
        if data is not None:
            file_path, _, _ = ds.save_data(
                data,
                metadata=parameters,
                nametag=tag,
                timestamp=timestamp,
                column_headers="Image (columns is X, rows is Y)",
            )
            figure = logic.draw_2d_image(data, cbar_range=None)
            ds.save_thumbnail(figure, file_path=file_path.rsplit(".", 1)[0])
        else:
            self.log.error("No Data acquired. Nothing to save.")
        return
