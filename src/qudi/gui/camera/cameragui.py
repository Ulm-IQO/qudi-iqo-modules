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
from qudi.util.widgets.plotting.image_widget import RubberbandZoomSelectionImageWidget
from qudi.util.datastorage import TextDataStorage
from qudi.util.paths import get_artwork_dir
from qudi.gui.camera.camera_settings_dialog import CameraSettingsDialog
from typing import Union, Optional, Tuple, List, Dict
import numpy as np


class CameraMainWindow(QtWidgets.QMainWindow):
    """ QMainWindow object for qudi CameraGui module """

    def __init__(self, available_acquisition_modes, *args, **kwargs):
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
        self.action_toggle_acquisition = QtWidgets.QAction('Start Acquisition')
        self.action_toggle_acquisition.setCheckable(True)
        toolbar.addAction(self.action_toggle_acquisition)
        # acquisition mode selector combobox
        self.acquisition_modes_combobox = CameraModeSelector(available_acquisition_modes)
        toolbar.addWidget(self.acquisition_modes_combobox)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        # Settings Button
        self.action_open_settings = QtWidgets.QAction('Settings')
        self.action_open_settings.setCheckable(True)
        toolbar.addAction(self.action_open_settings)


        # Create central widget
        #
        #
        #
        #
        #
        #
        self.cw = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(self.cw)
        self.image_widget = RubberbandZoomSelectionImageWidget()
        #self.image_widget = MyImageWidget()
        # FIXME: The camera hardware is currently transposing the image leading to this dirty hack
        self.image_widget.image_item.setOpts(False, axisOrder='row-major')
        self.setCentralWidget(self.cw)

        # Measurement Selector spi
        measurement_number_layout = QtWidgets.QVBoxLayout()
        self.measurement_number_label = QtWidgets.QLabel()
        self.measurement_number_label.setText("Measurement Number")
        self.measurement_number_spinbox = QtWidgets.QSpinBox()
        self.measurement_number_spinbox.setMinimum(0)
        self.measurement_number_spinbox.setMaximum(0)
        measurement_number_layout.addWidget(self.measurement_number_label)
        measurement_number_layout.addWidget(self.measurement_number_spinbox)

        # Image number label
        image_number_layout = QtWidgets.QVBoxLayout()
        self.image_number_label = QtWidgets.QLabel()
        self.image_number_label.setText("Image Number")
        self.image_number_num_label = QtWidgets.QLabel()
        self.image_number_num_label.setText("0 / 0")
        image_number_layout.addWidget(self.image_number_label)
        image_number_layout.addWidget(self.image_number_num_label)

        # Image scrollbar
        self.image_scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Horizontal)
        self.image_scrollbar.setMinimum(0)
        self.image_scrollbar.setValue(0)
        self.image_scrollbar.setMaximum(0)

        
        # add the widgets to the central widget layout
        layout.addLayout(measurement_number_layout, 2, 0, 1, 1)
        layout.addWidget(self.image_widget, 0, 0, 1, 2)
        layout.addLayout(image_number_layout, 2, 1, 1, 1)
        layout.addWidget(self.image_scrollbar, 1, 0, 1, 2)

    def update_toolbar(self, text):
        """
        Method that updates the toolbar depending on which acquisition mode is chosen
        """
        # TODO implement this function
        pass

class CameraModeSelector(QtWidgets.QComboBox):
    """
    Combobox for the selction of the acquisition mode
    """

    def __init__(self, acquisition_modes, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # store the information on which acquisition modes are enabled
        self._acquisition_modes = acquisition_modes
        # add the enabled acquisition modes to the combobox
        self.populate_combobox()

    def populate_combobox(self):
        """
        Method to add all enabled acquisition modes to the Combobox
        """
        # check which acquisition_modes are set to True
        # and assign them an index, based on their position in the tuple
        self._available_acquisition_modes = [mode for mode in self._acquisition_modes if self._acquisition_modes[mode] == True]
        self.addItems(self._available_acquisition_modes)

class CameraGui(GuiBase):
    """
    Main spectrometer camera class.

    Todo: Example config for copy-paste:

    """
    _camera_logic = Connector(name='camera_logic', interface='CameraLogic')

    sigAcquisitionToggled = QtCore.Signal(bool, str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._settings_dialog = None

    def on_activate(self):
        """ Initializes all needed UI files and establishes the connectors.
        """
        logic = self._camera_logic()

        # Create main window
        self._mw = CameraMainWindow(available_acquisition_modes=logic.available_acquisition_modes)
        # Create settings dialog
        self._settings_dialog = CameraSettingsDialog(self._mw, max_num_of_exposures=50)
        # Connect the action of the settings dialog with this module
        self._settings_dialog.accepted.connect(self._update_settings)
        self._settings_dialog.rejected.connect(self._keep_former_settings)
        self._settings_dialog.button_box.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(
            self._update_settings
        )
        self._settings_dialog.exposure_creation_button.clicked.connect(self._create_exposure_linspace)
        self._settings_dialog.exposure_same_value_creation_button.clicked.connect(self._same_value_exposure_list)
        self._mw.acquisition_modes_combobox.currentTextChanged.connect(self._update_toolbar)

        # Fill in data from logic
        logic_busy = logic.module_state() == 'locked'
        self._mw.action_toggle_acquisition.setChecked(logic_busy)
        self._update_frame()
        self._keep_former_settings()
        # deactivate all not settable options
        for setting in logic.constraints.settable_settings:
                self._settings_dialog.settable_settings_mapper[setting].setEnabled(logic.constraints.settable_settings[setting])
        # get all operating modes
        operating_modes = [mode.name for mode in self._camera_logic().constraints.operating_modes]
        if not operating_modes:
            self._settings_dialog.operating_mode_combobox.setEnabled(False)
        self._settings_dialog.operating_mode_combobox.addItems(operating_modes)
        # connect main window actions
        self._mw.action_toggle_acquisition.triggered[bool].connect(self._start_acquisition_clicked)
        self._mw.action_show_settings.triggered.connect(lambda: self._settings_dialog.exec_())
        self._mw.action_save_frame.triggered.connect(self._save_frame)
        self._mw.action_open_settings.triggered.connect(lambda: (self._keep_former_settings(), self._settings_dialog.exec_()))
        self._mw.image_scrollbar.valueChanged.connect(self._image_slider_moved)
        self._mw.measurement_number_spinbox.valueChanged.connect(self._measurement_slider_moved)
        # connect update signals from logic
        logic.sigAcquisitionFinished.connect(self._acquisition_finished)
        logic.sigFrameChanged.connect(self._update_frame)
        # connect GUI signals to logic slots
        self.sigAcquisitionToggled.connect(logic.toggle_acquisition)
        self.show()

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module.
        """
        logic = self._camera_logic()
        # disconnect all signals
        self.sigAcquisitionToggled.disconnect()
        logic.sigAcquisitionFinished.disconnect()
        logic.sigFrameChanged.disconnect()
        self._mw.action_toggle_acquisition.triggered.disconnect()
        self._mw.action_show_settings.triggered.disconnect()
        self._mw.action_save_frame.triggered.disconnect()
        self._mw.action_open_settings.triggered.disconnect()
        self._mw.image_scrollbar.valueChanged.disconnect()
        self._mw.measurement_number_spinbox.valueChanged.disconnect()
        self._settings_dialog.exposure_creation_button.clicked.disconnect()
        self._settings_dialog.exposure_same_value_creation_button.clicked.disconnect()
        self._mw.acquisition_modes_combobox.currentTextChanged.disconnect()
        self._settings_dialog.accepted.disconnect()
        self._settings_dialog.rejected.disconnect()
        self._settings_dialog.button_box.button(QtWidgets.QDialogButtonBox.Apply).clicked.disconnect()

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
        # get the settable settings of the camera
        settable_settings = logic.constraints.settable_settings
        logic.ring_of_exposures = self.get_ring_of_exposure()
        if settable_settings['responsitivity']:
            logic.responsitivity = self._settings_dialog.responsitivity_spinbox.value()
        if settable_settings['bit_depth']:
            logic.bit_depth = self._settings_dialog.bitdepth_spinbox.value()
        if settable_settings['binning']:
            logic.binning = (self._settings_dialog.hbinning_spinbox.value(), self._settings_dialog.vbinning_spinbox.value())
        if settable_settings['crop']:
            logic.crop = ((self._settings_dialog.area_selection_group_start_x_spinbox.value(), self._settings_dialog.area_selection_group_stop_x_spinbox.value()), (self._settings_dialog.area_selection_group_start_y_spinbox.value(), self._settings_dialog.area_selection_group_stop_y_spinbox.value()))
        if self._settings_dialog.operating_mode_combobox.isEnabled():
            logic.operating_mode = logic.constraints.operating_modes(self._settings_dialog.operating_mode_combobox.currentIndex())

    def get_ring_of_exposure(self):
        """
        Gets the ring of exposure set in the settings and converts it to a list
        """
        list_of_exposures = []
        for column in range(self._settings_dialog.ring_of_exposures_table.columnCount()):
            list_of_exposures.append(self._settings_dialog.ring_of_exposures_table.item(0, column).data(0))
        return np.array(list_of_exposures, dtype=float)

    def _keep_former_settings(self):
        """ Keep the old settings and restores them in the gui. """
        logic = self._camera_logic()
        start = logic.ring_of_exposures[0]
        stop = logic.ring_of_exposures[-1]
        step = abs(stop-start)/len(logic.ring_of_exposures)
        if step == 0:
            step = 0.01
        # TODO: set the correct values for all options (bin width, etc.)
        self._settings_dialog.exposure_start_spinbox.setValue(start)
        self._settings_dialog.exposure_step_spinbox.setValue(step)
        self._settings_dialog.exposure_stop_spinbox.setValue(stop)
        self._settings_dialog.exposure_time_spinbox.setValue(start)
        self._settings_dialog.exposure_num_spinbox.setValue(len(logic.ring_of_exposures))
        self._settings_dialog.responsitivity_spinbox.setValue(logic.responsitivity)
        self._settings_dialog.bitdepth_spinbox.setValue(logic.bit_depth)
        self._settings_dialog.hbinning_spinbox.setValue(logic.binning[0])
        self._settings_dialog.vbinning_spinbox.setValue(logic.binning[1])
        self._settings_dialog.area_selection_group_start_x_spinbox.setValue(logic.crop[0][0])
        self._settings_dialog.area_selection_group_stop_x_spinbox.setValue(logic.crop[0][1])
        self._settings_dialog.area_selection_group_start_y_spinbox.setValue(logic.crop[1][0])
        self._settings_dialog.area_selection_group_stop_y_spinbox.setValue(logic.crop[1][1])
        self._display_ring_of_exposures(logic.ring_of_exposures)
        self._settings_dialog.operating_mode_combobox.setCurrentIndex(logic.operating_mode.value)

    def _update_toolbar(self, text):
        """
        Method that updates the toolbar with settings specific to the current acquisition mode.
        """
        # TODO implement this function
        self._mw.update_toolbar(text)

    def _create_exposure_linspace(self):
        """
        Function that is called when the linspace creation button is clicked. 
        """
        start = self._settings_dialog.exposure_start_spinbox.value()
        step = self._settings_dialog.exposure_step_spinbox.value()
        stop = self._settings_dialog.exposure_stop_spinbox.value()
        self._display_ring_of_exposures(self._camera_logic().linspace_creation(start=start,step=step,stop=stop))

    def _same_value_exposure_list(self):
        """
        Function that creates a list of exposure times with all having the same value.
        The exposure time is taken from the spinbox in the same value exposure creation group in the SettingsDialog.
        """
        self._display_ring_of_exposures([self._settings_dialog.exposure_time_spinbox.value() for i in range(self._settings_dialog.exposure_num_spinbox.value())])

    def _display_ring_of_exposures(self, exposures_list):
        """
        Change the data of the ring of exposures table in the
        settings dialog to the current ring_of_exposures list
        """
        logic = self._camera_logic()
        self._settings_dialog.ring_of_exposures_table.setColumnCount(len(exposures_list))
        counter = 0
        for exposure in exposures_list:
            item = QtWidgets.QTableWidgetItem()
            item.setData(0, exposure)
            item.setText(str(exposure))
            self._settings_dialog.ring_of_exposures_table.setItem(0, counter, item)
            counter += 1

    def _acquisition_finished(self):
        self._mw.action_toggle_acquisition.setChecked(False)
        self._mw.action_toggle_acquisition.setEnabled(True)
        self._mw.action_toggle_acquisition.setText("Start Acquisition")
        self._mw.action_open_settings.setChecked(False)
        self._mw.action_open_settings.setEnabled(True)
        self._mw.acquisition_modes_combobox.setEnabled(True)
        self._mw.action_show_settings.setEnabled(True)

    def _start_acquisition_clicked(self, checked):
        if checked:
            self._mw.action_show_settings.setDisabled(True)
            self._mw.action_open_settings.setDisabled(True)
            self._mw.acquisition_modes_combobox.setEnabled(False)
            self._mw.action_toggle_acquisition.setText('Stop Acquisition')
        else:
            self._mw.action_toggle_acquisition.setText('Start Acquisition')
        self.sigAcquisitionToggled.emit(checked, self._mw.acquisition_modes_combobox.currentText())

    def _update_frame(self, sequence_num=0, image_num=0):
        """
        """
        frame_data = self._camera_logic().last_frames
        if frame_data is None:
            self._mw.image_widget.set_image(frame_data)
            return
        # update measurement and image number slider
        image_num_string = str(image_num) + " / " + str(frame_data[sequence_num].data.shape[0]-1)
        self._mw.measurement_number_spinbox.setMaximum(frame_data.shape[0]-1)
        self._mw.measurement_number_spinbox.setValue(sequence_num)
        self._mw.image_number_num_label.setText(image_num_string)
        # set scrollbar position and maximum value
        self._mw.image_scrollbar.setMaximum(frame_data[sequence_num].data.shape[0]-1)
        self._mw.image_scrollbar.setValue(image_num)
        # update the imageview with the new image data
        self._mw.image_widget.set_image(frame_data[sequence_num].data[image_num])

    def _image_slider_moved(self, image_num):
        self._camera_logic().current_image_number = image_num
        self._update_frame(sequence_num=self._camera_logic().current_measurement_number, image_num=image_num)

    def _measurement_slider_moved(self, sequence_num):
        self._camera_logic().current_measurement_number = sequence_num
        self._update_frame(sequence_num=sequence_num, image_num=self._camera_logic().current_image_number)

    def _save_frame(self):
        logic = self._camera_logic()
        # check if data is available
        full_data = logic.last_frames
        if full_data is None:
            # if not throw an error and return
            self.log.error('No Data acquired. Nothing to save.')
            return
        # if data is available, get the metadata of the saved data and write all different images to different image files
        ds = TextDataStorage(root_dir=self.module_default_data_dir)
        for ii, measurement_data in enumerate(full_data):
            timestamp = datetime.datetime.now()
            # set the metadata
            parameters = {
                    'timestamp': measurement_data.timestamp.strftime('%Y-%m-%d, %H:%M:%S'),
                    'measurement number': ii,
                    'responsitivity': measurement_data.responsitivity,
                    'measurement exposure list': list(measurement_data.ring_of_exposures),
                    'bit depth': measurement_data.bit_depth,
                    'binning': measurement_data.binning,
                    'crop': measurement_data.crop,
                    'operating mode': measurement_data.operating_mode.name,
                        }
            for kk, data in enumerate(measurement_data.data):
                # set the image specific metadata
                parameters['image number'] = kk
                parameters['exposure'] = measurement_data.ring_of_exposures[kk]
                tag = logic.create_tag(measurement_num = ii, image_num = kk)
                # save the raw data
                file_path, _, _ = ds.save_data(data, metadata=parameters, nametag=tag,
                                           timestamp=timestamp, column_headers='Image (columns is X, rows is Y)')
                # save a drawn image of the data
                figure = logic.draw_2d_image(data, cbar_range=None)
                ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])
