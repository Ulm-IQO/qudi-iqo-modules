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


class CameraMainWindow(QtWidgets.QMainWindow):
    """ QMainWindow object for qudi CameraGui module """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Create menu bar
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu('File')
        self.action_save_frame = QtWidgets.QAction('Save Frame')
        path = os.path.join(get_artwork_dir(), 'icons', 'document-save')
        self.action_save_frame.setIcon(QtGui.QIcon(path))
        menu.addAction(self.action_save_frame)
        menu.addSeparator()
        self.action_show_settings = QtWidgets.QAction('Settings')
        path = os.path.join(get_artwork_dir(), 'icons', 'configure')
        self.action_show_settings.setIcon(QtGui.QIcon(path))
        menu.addAction(self.action_show_settings)
        menu.addSeparator()
        self.action_close = QtWidgets.QAction('Close')
        path = os.path.join(get_artwork_dir(), 'icons', 'application-exit')
        self.action_close.setIcon(QtGui.QIcon(path))
        self.action_close.triggered.connect(self.close)
        menu.addAction(self.action_close)
        self.setMenuBar(menu_bar)

        # Create toolbar
        toolbar = QtWidgets.QToolBar()
        toolbar.setAllowedAreas(QtCore.Qt.AllToolBarAreas)
        self.action_start_video = QtWidgets.QAction('Start Video')
        self.action_start_video.setCheckable(True)
        toolbar.addAction(self.action_start_video)
        self.action_capture_frame = QtWidgets.QAction('Capture Frame')
        self.action_capture_frame.setCheckable(True)
        toolbar.addAction(self.action_capture_frame)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        # Create central widget
        self.image_widget = ImageWidget()
        # FIXME: The camera hardware is currently transposing the image leading to this dirty hack
        self.image_widget.image_item.setOpts(False, axisOrder='row-major')
        self.setCentralWidget(self.image_widget)


class CameraGui(GuiBase):
    """
    Main spectrometer camera class.

    Todo: Example config for copy-paste:

    """

    _camera_logic = Connector(name='camera_logic', interface='CameraLogic')

    sigStartStopVideoToggled = QtCore.Signal(bool)
    sigCaptureFrameTriggered = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._settings_dialog = None

    def on_activate(self):
        """ Initializes all needed UI files and establishes the connectors.
        """
        logic = self._camera_logic()

        # Create main window
        self._mw = CameraMainWindow()
        # Create settings dialog
        self._settings_dialog = CameraSettingsDialog(self._mw)
        # Connect the action of the settings dialog with this module
        self._settings_dialog.accepted.connect(self._update_settings)
        self._settings_dialog.rejected.connect(self._keep_former_settings)
        self._settings_dialog.button_box.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(
            self._update_settings
        )

        # Fill in data from logic
        logic_busy = logic.module_state() == 'locked'
        self._mw.action_start_video.setChecked(logic_busy)
        self._mw.action_capture_frame.setChecked(logic_busy)
        self._update_frame(logic.last_frame)
        self._keep_former_settings()

        # connect main window actions
        self._mw.action_start_video.triggered[bool].connect(self._start_video_clicked)
        self._mw.action_capture_frame.triggered.connect(self._capture_frame_clicked)
        self._mw.action_show_settings.triggered.connect(lambda: self._settings_dialog.exec_())
        self._mw.action_save_frame.triggered.connect(self._save_frame)
        # connect update signals from logic
        logic.sigFrameChanged.connect(self._update_frame)
        logic.sigAcquisitionFinished.connect(self._acquisition_finished)
        # connect GUI signals to logic slots
        self.sigStartStopVideoToggled.connect(logic.toggle_video)
        self.sigCaptureFrameTriggered.connect(logic.capture_frame)
        self.show()

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
        """
        logic = self._camera_logic()
        # disconnect all signals
        self.sigCaptureFrameTriggered.disconnect()
        self.sigStartStopVideoToggled.disconnect()
        logic.sigAcquisitionFinished.disconnect(self._acquisition_finished)
        logic.sigUpdateDisplay.disconnect(self._update_frame)
        self._mw.action_save_frame.triggered.disconnect()
        self._mw.action_show_settings.triggered.disconnect()
        self._mw.action_capture_frame.triggered.disconnect()
        self._mw.action_start_video.triggered.disconnect()
        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        self._mw.show()
        self._mw.raise_()
        self._mw.activateWindow()

    def _update_settings(self):
        """ Write new settings from the gui to the file. """
        logic = self._camera_logic()
        logic.set_exposure(self._settings_dialog.exposure_spinbox.value())
        logic.set_gain(self._settings_dialog.gain_spinbox.value())

    def _keep_former_settings(self):
        """ Keep the old settings and restores them in the gui. """
        logic = self._camera_logic()
        self._settings_dialog.exposure_spinbox.setValue(logic.get_exposure())
        self._settings_dialog.gain_spinbox.setValue(logic.get_gain())

    def _capture_frame_clicked(self):
        self._mw.action_start_video.setDisabled(True)
        self._mw.action_capture_frame.setDisabled(True)
        self._mw.action_show_settings.setDisabled(True)
        self.sigCaptureFrameTriggered.emit()

    def _acquisition_finished(self):
        self._mw.action_start_video.setChecked(False)
        self._mw.action_start_video.setEnabled(True)
        self._mw.action_capture_frame.setChecked(False)
        self._mw.action_capture_frame.setEnabled(True)
        self._mw.action_show_settings.setEnabled(True)

    def _start_video_clicked(self, checked):
        if checked:
            self._mw.action_show_settings.setDisabled(True)
            self._mw.action_capture_frame.setDisabled(True)
            self._mw.action_start_video.setText('Stop Video')
        else:
            self._mw.action_start_video.setText('Start Video')
        self.sigStartStopVideoToggled.emit(checked)

    def _update_frame(self, frame_data):
        """
        """
        self._mw.image_widget.set_image(frame_data)

    def _save_frame(self):
        logic = self._camera_logic()
        ds = TextDataStorage(root_dir=self.module_default_data_dir)
        timestamp = datetime.datetime.now()
        tag = logic.create_tag(timestamp)

        parameters = {}
        parameters['gain'] = logic.get_gain()
        parameters['exposure'] = logic.get_exposure()

        data = logic.last_frame
        if data is not None:
            file_path, _, _ = ds.save_data(data, metadata=parameters, nametag=tag,
                                       timestamp=timestamp, column_headers='Image (columns is X, rows is Y)')
            figure = logic.draw_2d_image(data, cbar_range=None)
            ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])
        else:
            self.log.error('No Data acquired. Nothing to save.')
        return
