# -*- coding: utf-8 -*-

"""
This module contains a custom QDialog subclass for the Camera GUI module.

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

__all__ = ('CameraSettingsDialog',)

from PySide2 import QtCore, QtWidgets
from numpy import who
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class CameraSettingsDialog(QtWidgets.QDialog):
    """ Create the camera settings dialog """

    def __init__(self, *args, max_num_of_exposures=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('qudi: Camera Settings')

        layout = QtWidgets.QGridLayout()
        layout.setAlignment(QtCore.Qt.AlignCenter)
        # layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)

        ring_of_exposures_group = QtWidgets.QGroupBox('Ring of Exposure Times')
        ring_of_exposures_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        ring_of_exposures_group_layout = QtWidgets.QGridLayout()
        
        # set up the ring of exposure generation
        linspace_group = QtWidgets.QGroupBox('Linspace Creation')
        # layout for the linspace generation
        linspace_layout = QtWidgets.QGridLayout()
        linspace_group.setLayout(linspace_layout)
        linspace_layout.setAlignment(QtCore.Qt.AlignCenter)

        # linspace generation of ring_of_exposures
        # start exposure time
        self.exposure_start_spinbox = ScienDSpinBox()
        self.exposure_start_spinbox.setSuffix('s')
        self.exposure_start_spinbox.setMinimum(0)
        self.exposure_start_spinbox.setDecimals(3)
        self.exposure_start_spinbox.setMinimumWidth(100)
        exposure_start_label = QtWidgets.QLabel("Minimum\t")
        # step exposure time
        self.exposure_step_spinbox = ScienDSpinBox()
        self.exposure_step_spinbox.setSuffix('s')
        self.exposure_step_spinbox.setMinimum(0)
        self.exposure_step_spinbox.setDecimals(3)
        self.exposure_step_spinbox.setMinimumWidth(100)
        exposure_step_label = QtWidgets.QLabel("Step")
        # stop exposure time
        self.exposure_stop_spinbox = ScienDSpinBox()
        self.exposure_stop_spinbox.setSuffix('s')
        self.exposure_stop_spinbox.setMinimum(0)
        self.exposure_stop_spinbox.setDecimals(3)
        self.exposure_stop_spinbox.setMinimumWidth(100)
        exposure_stop_label = QtWidgets.QLabel("Maximum\t")
        # linspace creation button
        self.exposure_creation_button = QtWidgets.QPushButton('Create')
        # add the spinboxes to the linspace layout
        linspace_layout.addWidget(exposure_start_label, 0, 0)
        linspace_layout.addWidget(self.exposure_start_spinbox, 0, 1)
        linspace_layout.addWidget(exposure_step_label, 1, 0)
        linspace_layout.addWidget(self.exposure_step_spinbox, 1, 1)
        linspace_layout.addWidget(exposure_stop_label, 2, 0)
        linspace_layout.addWidget(self.exposure_stop_spinbox, 2, 1)
        linspace_layout.addWidget(self.exposure_creation_button, 3, 0, 1, 2)
        # add linspace layout to the general layout
        self.ring_of_exposures_table = QtWidgets.QTableWidget(1, max_num_of_exposures)
        self.ring_of_exposures_table.setVerticalHeaderLabels(['Time (s)'])
        self.ring_of_exposures_table.setSizeAdjustPolicy(QtWidgets.QScrollArea.AdjustToContents)
        self.ring_of_exposures_table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.ring_of_exposures_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.ring_of_exposures_table.setMaximumWidth(self.ring_of_exposures_table.columnWidth(1)*20)

        # ring of exposures creation of length N with all values beeing the same
        same_value_list_group = QtWidgets.QGroupBox('Same Exposure Times Creation')
        # layout for the generation of the list
        same_value_list_layout = QtWidgets.QGridLayout()
        same_value_list_group.setLayout(same_value_list_layout)
        same_value_list_layout.setAlignment(QtCore.Qt.AlignBottom)
        # exposure time spin box
        self.exposure_time_spinbox = ScienDSpinBox()
        self.exposure_time_spinbox.setSuffix('s')
        self.exposure_time_spinbox.setMinimum(0)
        self.exposure_time_spinbox.setDecimals(3)
        self.exposure_time_spinbox.setMinimumWidth(100)
        exposure_time_label = QtWidgets.QLabel("Exposure time\t")
        
        # length of list spin box
        self.exposure_num_spinbox = QtWidgets.QSpinBox()
        self.exposure_num_spinbox.setMinimum(0)
        self.exposure_num_spinbox.setMinimumWidth(100)
        exposure_num_label = QtWidgets.QLabel("Number of exposures\t")
        # same value creation button
        self.exposure_same_value_creation_button = QtWidgets.QPushButton('Create')

        same_value_list_layout.addWidget(exposure_time_label, 0, 0)
        same_value_list_layout.addWidget(self.exposure_time_spinbox, 0, 1)
        same_value_list_layout.addWidget(exposure_num_label, 1, 0)
        same_value_list_layout.addWidget(self.exposure_num_spinbox, 1, 1)
        same_value_list_layout.addWidget(self.exposure_same_value_creation_button, 2, 0, 1, 2)
        
        # add to layout
        ring_of_exposures_group_layout.addWidget(linspace_group, 0, 0)
        ring_of_exposures_group_layout.addWidget(same_value_list_group, 0, 1)
        ring_of_exposures_group_layout.addWidget(self.ring_of_exposures_table, 1, 0, 1, 2)
        ring_of_exposures_group.setLayout(ring_of_exposures_group_layout)

        responsitivity_group = QtWidgets.QGroupBox('Responsitivity')
        responsitivity_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        responsitivity_group_layout = QtWidgets.QHBoxLayout()
        responsitivity_group.setLayout(responsitivity_group_layout)
        self.responsitivity_spinbox = ScienDSpinBox()
        # ToDo: set proper unit for gain with self.gain_spinbox.setSuffix('s')
        self.responsitivity_spinbox.setMinimum(0)
        self.responsitivity_spinbox.setDecimals(3)
        self.responsitivity_spinbox.setMinimumWidth(100)
        responsitivity_group_layout.addWidget(self.responsitivity_spinbox)

        # Bit depth setting
        bitdepth_group = QtWidgets.QGroupBox('Bit Depth')
        bitdepth_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        bitdepth_group_layout = QtWidgets.QHBoxLayout()
        bitdepth_group.setLayout(bitdepth_group_layout)
        self.bitdepth_spinbox = QtWidgets.QSpinBox()
        # ToDo: set proper unit for gain with self.gain_spinbox.setSuffix('s')
        self.bitdepth_spinbox.setMinimum(0)
        self.bitdepth_spinbox.setMinimumWidth(100)
        bitdepth_group_layout.addWidget(self.bitdepth_spinbox)

        # Binning setting
        binning_group = QtWidgets.QGroupBox('Binning')
        binning_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        binning_group_layout = QtWidgets.QGridLayout()
        binning_group.setLayout(binning_group_layout)
        self.hbinning_spinbox = QtWidgets.QSpinBox()
        # ToDo: set proper unit for gain with self.gain_spinbox.setSuffix('s')
        self.hbinning_spinbox.setMinimum(0)
        self.hbinning_spinbox.setMaximum(10000)
        self.hbinning_spinbox.setMinimumWidth(100)
        labelx = QtWidgets.QLabel('x ')
        labely = QtWidgets.QLabel('y ')

        self.vbinning_spinbox = QtWidgets.QSpinBox()
        # ToDo: set proper unit for gain with self.gain_spinbox.setSuffix('s')
        self.vbinning_spinbox.setMinimum(0)
        self.vbinning_spinbox.setMaximum(10000)
        self.vbinning_spinbox.setMinimumWidth(100)
        binning_group_layout.addWidget(labelx, 0, 0)
        binning_group_layout.addWidget(self.hbinning_spinbox, 0, 1)
        binning_group_layout.addWidget(labely, 1, 0)
        binning_group_layout.addWidget(self.vbinning_spinbox, 1, 1)

        # Area selection setting
        area_selection_group = QtWidgets.QGroupBox('Area Selection')
        area_selection_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        area_selection_group_layout = QtWidgets.QGridLayout()
        area_selection_group.setLayout(area_selection_group_layout)
        self.area_selection_group_start_x_spinbox = QtWidgets.QSpinBox()
        self.area_selection_group_stop_x_spinbox = QtWidgets.QSpinBox()
        self.area_selection_group_start_y_spinbox = QtWidgets.QSpinBox()
        self.area_selection_group_stop_y_spinbox = QtWidgets.QSpinBox()
        # ToDo: set proper unit for gain with self.gain_spinbox.setSuffix('s')
        # TODO: set proper maximum for area selection
        self.area_selection_group_start_x_spinbox.setMinimum(0)
        self.area_selection_group_start_x_spinbox.setMaximum(10000)
        self.area_selection_group_start_x_spinbox.setMinimumWidth(100)
        self.area_selection_group_stop_x_spinbox.setMinimum(0)
        self.area_selection_group_stop_x_spinbox.setMaximum(10000)
        self.area_selection_group_stop_x_spinbox.setMinimumWidth(100)
        self.area_selection_group_start_y_spinbox.setMinimum(0)
        self.area_selection_group_start_y_spinbox.setMaximum(10000)
        self.area_selection_group_start_y_spinbox.setMinimumWidth(100)
        self.area_selection_group_stop_y_spinbox.setMinimum(0)
        self.area_selection_group_stop_y_spinbox.setMaximum(10000)
        self.area_selection_group_stop_y_spinbox.setMinimumWidth(100)
        labelxmin = QtWidgets.QLabel('x_min')
        labelxmax = QtWidgets.QLabel('x_max')
        labelymin = QtWidgets.QLabel('y_min')
        labelymax = QtWidgets.QLabel('y_max')
        area_selection_group_layout.addWidget(labelxmin, 0, 0)
        area_selection_group_layout.addWidget(self.area_selection_group_start_x_spinbox, 0, 1)
        area_selection_group_layout.addWidget(labelxmax, 0, 2)
        area_selection_group_layout.addWidget(self.area_selection_group_stop_x_spinbox, 0, 3)
        area_selection_group_layout.addWidget(labelymin, 1, 0)
        area_selection_group_layout.addWidget(self.area_selection_group_start_y_spinbox, 1, 1)
        area_selection_group_layout.addWidget(labelymax, 1, 2)
        area_selection_group_layout.addWidget(self.area_selection_group_stop_y_spinbox, 1, 3)

        
        # Operating Mode selection
        operating_mode_group = QtWidgets.QGroupBox('Operating Modes')
        operating_mode_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        operating_mode_layout = QtWidgets.QHBoxLayout()
        operating_mode_group.setLayout(operating_mode_layout)
        self.operating_mode_combobox = QtWidgets.QComboBox()
        operating_mode_layout.addWidget(self.operating_mode_combobox)
        
        # Number of measurements selection
        num_measurements_group = QtWidgets.QGroupBox('Number of Measurements')
        num_measurements_group.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        num_measurements_layout = QtWidgets.QHBoxLayout()
        num_measurements_group.setLayout(num_measurements_layout)
        self.num_measurements_spinbox = QtWidgets.QSpinBox()
        self.num_measurements_spinbox.setMinimum(1)
        self.num_measurements_spinbox.setMaximum(10000)
        self.num_measurements_spinbox.setMinimumWidth(100)
        num_measurements_layout.addWidget(self.num_measurements_spinbox)

        self.button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok |
                                                     QtWidgets.QDialogButtonBox.Cancel |
                                                     QtWidgets.QDialogButtonBox.Apply,
                                                     QtCore.Qt.Horizontal,
                                                     self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        # add the settings groups to the overall layout
        layout.addWidget(ring_of_exposures_group, 0, 0, 1, 3)
        layout.addWidget(responsitivity_group, 1, 0)
        layout.addWidget(bitdepth_group, 1, 1)
        layout.addWidget(binning_group, 1, 2)
        layout.addWidget(area_selection_group, 3, 0, 1, 3)
        layout.addWidget(operating_mode_group, 4, 0, 1, 1)
        layout.addWidget(num_measurements_group, 4, 1, 1, 1)
        layout.addWidget(self.button_box, 5,0, 1, 3)
        #layout.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        
        # set the layout for the Settings Dialog
        self.setLayout(layout)

        self.settable_settings_mapper = {
                                         'responsitivity': responsitivity_group,
                                         'bit_depth': bitdepth_group,
                                         'binning': binning_group,
                                         'crop': area_selection_group
                                         }
