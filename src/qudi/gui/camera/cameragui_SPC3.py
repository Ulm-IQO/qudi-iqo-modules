# -*- coding: utf-8 -*-
"""Custom camera GUI variant.

This is a copy of qudi's generic camera GUI with one targeted change:
- After a snap/capture finishes, optionally prompt to save the most recent
  SPC3 snap as a `.spc3` file to a predefined directory.

The original GUI in `qudi.gui.camera.cameragui` is left untouched.

Example config for copy-paste:

camera_gui:
    module.Class: 'camera.cameragui_SPC3.CameraGui'
    connect:
        camera_logic: camera_logic

Notes
-----
- Saving is manual (prompted after snap), not auto-save.
- Requires the connected camera hardware to implement
  `save_last_snap_to_file(filepath, n_frames=None)`.
"""

import os
import datetime

from PySide2 import QtCore, QtWidgets, QtGui

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util.widgets.plotting.image_widget import ImageWidget
from qudi.util.datastorage import TextDataStorage
from qudi.util.paths import get_artwork_dir
from qudi.gui.camera.camera_settings_dialog import CameraSettingsDialog
from qudi.logic.camera_logic import CameraLogic


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

        # Create toolbar
        toolbar = QtWidgets.QToolBar()
        toolbar.setAllowedAreas(QtCore.Qt.AllToolBarAreas)
        self.action_start_video = QtWidgets.QAction("Start Video")
        self.action_start_video.setCheckable(True)
        toolbar.addAction(self.action_start_video)
        self.action_capture_frame = QtWidgets.QAction("Capture Frame")
        self.action_capture_frame.setCheckable(True)
        toolbar.addAction(self.action_capture_frame)

        self.action_continuous = QtWidgets.QAction("Continuous")
        self.action_continuous.setCheckable(True)
        toolbar.addAction(self.action_continuous)

        # Snap frame browsing controls (enabled only for multi-frame snaps)
        self.snap_frame_label = QtWidgets.QLabel("Frame")
        self.snap_frame_spinbox = QtWidgets.QSpinBox()
        self.snap_frame_spinbox.setRange(0, 0)
        self.snap_frame_spinbox.setEnabled(False)
        self.snap_frame_label.setEnabled(False)
        toolbar.addSeparator()
        toolbar.addWidget(self.snap_frame_label)
        toolbar.addWidget(self.snap_frame_spinbox)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        # Create central widget
        self.image_widget = ImageWidget()
        # FIXME: The camera hardware is currently transposing the image leading to this dirty hack
        self.image_widget.image_item.setOpts(False, axisOrder="row-major")
        self.setCentralWidget(self.image_widget)


class CameraGui(GuiBase):
    """Main camera gui class (custom save-on-snap variant)."""

    _camera_logic = Connector(name="camera_logic", interface=CameraLogic)

    sigStartStopVideoToggled = QtCore.Signal(bool)
    sigCaptureFrameTriggered = QtCore.Signal()
    sigContinuousToggled = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._settings_dialog = None
        self._pending_snap_save_prompt = False
        self._snap_sequence = None
        self._continuous_active = False

    def on_activate(self):
        """Initializes all needed UI files and establishes the connectors."""
        logic = self._camera_logic()

        # Create main window
        self._mw = CameraMainWindow()
        # Create settings dialog
        self._settings_dialog = CameraSettingsDialog(self._mw)
        # Connect the action of the settings dialog with this module
        self._settings_dialog.accepted.connect(self._update_settings)
        self._settings_dialog.rejected.connect(self._keep_former_settings)
        self._settings_dialog.button_box.button(
            QtWidgets.QDialogButtonBox.Apply
        ).clicked.connect(self._update_settings)

        # Fill in data from logic
        logic_busy = logic.module_state() == "locked"
        self._mw.action_start_video.setChecked(logic_busy)
        self._mw.action_capture_frame.setChecked(logic_busy)
        self._update_frame(logic.last_frame)
        self._keep_former_settings()

        # connect main window actions
        self._mw.action_start_video.triggered[bool].connect(self._start_video_clicked)
        self._mw.action_capture_frame.triggered.connect(self._capture_frame_clicked)
        self._mw.action_show_settings.triggered.connect(
            lambda: self._settings_dialog.exec_()
        )
        self._mw.action_save_frame.triggered.connect(self._save_frame)
        self._mw.snap_frame_spinbox.valueChanged.connect(self._snap_frame_index_changed)
        self._mw.action_continuous.triggered[bool].connect(self._continuous_clicked)

        # connect update signals from logic
        logic.sigFrameChanged.connect(self._update_frame)
        logic.sigAcquisitionFinished.connect(self._acquisition_finished)

        # connect GUI signals to logic slots
        self.sigStartStopVideoToggled.connect(logic.toggle_video)
        self.sigCaptureFrameTriggered.connect(logic.capture_frame)
        self.sigContinuousToggled.connect(logic.toggle_continuous)

        cont_sig = getattr(logic, "sigContinuousStateChanged", None)
        if cont_sig is not None:
            try:
                cont_sig.connect(self._continuous_state_changed)
            except Exception:
                pass

        # Initial state
        self._continuous_state_changed(bool(getattr(logic, "continuous_active", False)))
        self.show()

    def on_deactivate(self):
        """De-initialisation performed during deactivation of the module."""
        logic = self._camera_logic()
        # disconnect all signals
        self.sigCaptureFrameTriggered.disconnect()
        self.sigStartStopVideoToggled.disconnect()
        self.sigContinuousToggled.disconnect()
        logic.sigAcquisitionFinished.disconnect(self._acquisition_finished)
        logic.sigFrameChanged.disconnect(self._update_frame)

        cont_sig = getattr(logic, "sigContinuousStateChanged", None)
        if cont_sig is not None:
            try:
                cont_sig.disconnect(self._continuous_state_changed)
            except Exception:
                pass

        self._mw.action_save_frame.triggered.disconnect()
        self._mw.action_show_settings.triggered.disconnect()
        self._mw.action_capture_frame.triggered.disconnect()
        self._mw.action_start_video.triggered.disconnect()
        self._mw.action_continuous.triggered.disconnect()
        self._mw.snap_frame_spinbox.valueChanged.disconnect()
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

    def _keep_former_settings(self):
        """Keep the old settings and restores them in the gui."""
        logic = self._camera_logic()
        self._settings_dialog.exposure_spinbox.setValue(logic.get_exposure())
        self._settings_dialog.gain_spinbox.setValue(logic.get_gain())

    def _capture_frame_clicked(self):
        if self._continuous_active:
            return
        self._mw.action_start_video.setDisabled(True)
        self._mw.action_capture_frame.setDisabled(True)
        self._mw.action_continuous.setDisabled(True)
        self._mw.action_show_settings.setDisabled(True)
        self._pending_snap_save_prompt = True
        self.sigCaptureFrameTriggered.emit()

    def _acquisition_finished(self):
        self._mw.action_start_video.setChecked(False)
        self._mw.action_start_video.setEnabled(True)
        self._mw.action_capture_frame.setChecked(False)
        self._mw.action_capture_frame.setEnabled(True)
        self._mw.action_show_settings.setEnabled(True)
        if not self._continuous_active:
            self._mw.action_continuous.setEnabled(True)

        # If this snap produced a multi-frame sequence, enable browsing.
        self._refresh_snap_sequence()

        if self._pending_snap_save_prompt:
            self._pending_snap_save_prompt = False
            self._prompt_save_spc3_after_snap()

    def _start_video_clicked(self, checked):
        if checked and self._continuous_active:
            # Don't allow live mode while continuous is running
            self._mw.action_start_video.blockSignals(True)
            try:
                self._mw.action_start_video.setChecked(False)
            finally:
                self._mw.action_start_video.blockSignals(False)
            return

        if checked:
            self._mw.action_show_settings.setDisabled(True)
            self._mw.action_capture_frame.setDisabled(True)
            self._mw.action_continuous.setDisabled(True)
            self._mw.action_start_video.setText("Stop Video")

            # Video/live mode overrides snap browsing.
            self._set_snap_browsing_enabled(False)
        else:
            self._mw.action_start_video.setText("Start Video")
            self._mw.action_continuous.setEnabled(True)
        self.sigStartStopVideoToggled.emit(checked)

    def _continuous_clicked(self, checked):
        self.sigContinuousToggled.emit(bool(checked))

    def _continuous_state_changed(self, active):
        self._continuous_active = bool(active)

        self._mw.action_continuous.blockSignals(True)
        try:
            self._mw.action_continuous.setChecked(self._continuous_active)
        finally:
            self._mw.action_continuous.blockSignals(False)

        if self._continuous_active:
            self._mw.action_continuous.setText("Stop Continuous")
            self._mw.action_start_video.setDisabled(True)
            self._mw.action_capture_frame.setDisabled(True)
            self._mw.action_show_settings.setDisabled(True)
            self._set_snap_browsing_enabled(False)
        else:
            self._mw.action_continuous.setText("Continuous")
            self._mw.action_start_video.setEnabled(True)
            self._mw.action_capture_frame.setEnabled(True)
            self._mw.action_show_settings.setEnabled(True)
            self._mw.action_continuous.setEnabled(True)

    def _update_frame(self, frame_data):
        self._mw.image_widget.set_image(frame_data)

    def _set_snap_browsing_enabled(self, enabled, n_frames=0):
        enabled = bool(enabled) and int(n_frames) > 1
        self._mw.snap_frame_label.setEnabled(enabled)
        self._mw.snap_frame_spinbox.setEnabled(enabled)
        if enabled:
            self._mw.snap_frame_spinbox.setRange(0, int(n_frames) - 1)
        else:
            self._mw.snap_frame_spinbox.setRange(0, 0)
            self._mw.snap_frame_spinbox.setValue(0)

    def _refresh_snap_sequence(self):
        """Try to fetch the last snap stack from hardware (SPC3 only)."""
        logic = self._camera_logic()

        camera = getattr(logic, "_camera", None)
        camera = camera() if callable(camera) else None

        get_seq = getattr(camera, "get_last_snap_sequence", None)
        if camera is None or not callable(get_seq):
            self._snap_sequence = None
            self._set_snap_browsing_enabled(False)
            return

        seq = get_seq()
        if seq is None:
            self._snap_sequence = None
            self._set_snap_browsing_enabled(False)
            return

        try:
            n_frames = int(seq.shape[0])
        except Exception:
            self._snap_sequence = None
            self._set_snap_browsing_enabled(False)
            return

        self._snap_sequence = seq
        self._set_snap_browsing_enabled(True, n_frames=n_frames)

        # Default to the last frame (what the logic already shows).
        if n_frames > 1:
            self._mw.snap_frame_spinbox.blockSignals(True)
            try:
                self._mw.snap_frame_spinbox.setValue(n_frames - 1)
            finally:
                self._mw.snap_frame_spinbox.blockSignals(False)

    def _snap_frame_index_changed(self, idx):
        if self._snap_sequence is None:
            return
        try:
            idx = int(idx)
            idx = max(0, min(idx, int(self._snap_sequence.shape[0]) - 1))
            frame = self._snap_sequence[idx]
        except Exception:
            return
        self._update_frame(frame)

    def _prompt_save_spc3_after_snap(self):
        """Optionally save the most recent SPC3 snap buffer as a .spc3 file."""
        logic = self._camera_logic()

        # Access camera directly (best-effort; this is a custom GUI).
        camera = getattr(logic, "_camera", None)
        camera = camera() if callable(camera) else None

        save_method = getattr(camera, "save_last_snap_to_file", None)
        if camera is None or not callable(save_method):
            # Not an SPC3 camera or hardware doesn’t support manual snap saving.
            return

        reply = QtWidgets.QMessageBox.question(
            self._mw,
            "Snap Complete",
            "Snap acquisition complete.\n\nWould you like to save the snap as a .spc3 file?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        directory = ""
        get_dir = getattr(camera, "get_default_save_directory", None)
        if callable(get_dir):
            directory = (get_dir() or "").strip()

        if not directory:
            directory = self.module_default_data_dir

        os.makedirs(directory, exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        stem = os.path.join(directory, f"spc3_snap_{ts}")
        ok = bool(save_method(stem))

        if not ok:
            QtWidgets.QMessageBox.warning(
                self._mw,
                "Save Error",
                "Failed to save .spc3 file.\n\nCheck log for details.",
            )

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
