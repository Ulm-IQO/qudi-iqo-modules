# -*- coding: utf-8 -*-
"""
This module contains a GUI for operating the spectrum logic module.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import pyqtgraph as pg
from qudi.util.colordefs import QudiPalettePale as palette
import os
from qudi.util.paths import get_artwork_dir
from PySide2 import QtCore
from PySide2 import QtWidgets
from PySide2 import QtGui
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.util.widgets.toggle_switch import ToggleSwitch
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from .settingsdialog import SettingsDialog


class CustomAxis(pg.AxisItem):
    """ This is a CustomAxis that extends the normal pyqtgraph to be able to nudge the axis labels. """

    @property
    def nudge(self):
        if not hasattr(self, "_nudge"):
            self._nudge = 5
        return self._nudge

    @nudge.setter
    def nudge(self, nudge):
        self._nudge = nudge
        s = self.size()
        # call resizeEvent indirectly
        self.resize(s + QtCore.QSizeF(1, 1))
        self.resize(s)

    def resizeEvent(self, ev=None):
        # Set the position of the label
        nudge = self.nudge
        br = self.label.boundingRect()
        p = QtCore.QPointF(0, 0)
        if self.orientation == "left":
            p.setY(int(self.size().height() / 2 + br.width() / 2))
            p.setX(-nudge)
        elif self.orientation == "right":
            p.setY(int(self.size().height() / 2 + br.width() / 2))
            p.setX(int(self.size().width() - br.height() + nudge))
        elif self.orientation == "top":
            p.setY(-nudge)
            p.setX(int(self.size().width() / 2.0 - br.width() / 2.0))
        elif self.orientation == "bottom":
            p.setX(int(self.size().width() / 2.0 - br.width() / 2.0))
            p.setY(int(self.size().height() - br.height() + nudge))
        self.label.setPos(p)
        self.picture = None


class SpectrometerMainWindow(QtWidgets.QMainWindow):
    """ Main Window for the SpectrometerGui module """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.setStyleSheet('border: 1px solid #f00;')  # debugging help for the gui
        self.setWindowTitle('Spectrometer')
        self.setDockNestingEnabled(True)
        icon_path = os.path.join(get_artwork_dir(), 'icons', 'oxygen', '22x22')
        # self.setStyleSheet('border: 1px solid #f00;')  # debugging help for the gui

        self.setTabPosition(QtCore.Qt.TopDockWidgetArea, QtWidgets.QTabWidget.North)
        self.setTabPosition(QtCore.Qt.BottomDockWidgetArea, QtWidgets.QTabWidget.North)
        self.setTabPosition(QtCore.Qt.LeftDockWidgetArea, QtWidgets.QTabWidget.North)
        self.setTabPosition(QtCore.Qt.RightDockWidgetArea, QtWidgets.QTabWidget.North)

        self.controls_DockWidget = AdvancedDockWidget('Controls', parent=self)
        self.plot_DockWidget = AdvancedDockWidget('Plots', parent=self)

        # Create layout and content for the Controls DockWidget
        self.control_layout = QtWidgets.QGridLayout()
        # self.control_layout.setColumnStretch(0, 1)
        self.control_layout.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.control_layout.setContentsMargins(1, 1, 1, 1)
        self.control_layout.setSpacing(5)
        control_widget = QtWidgets.QWidget()
        control_widget.setLayout(self.control_layout)
        self.controls_DockWidget.setWidget(control_widget)

        self.spectrum_button = QtWidgets.QPushButton('Acquire Spectrum')
        self.spectrum_button.setCheckable(False)
        self.spectrum_button.setMinimumWidth(130)
        self.control_layout.addWidget(self.spectrum_button, 0, 0, QtCore.Qt.AlignCenter)

        self.spectrum_continue_button = QtWidgets.QPushButton('Continue Spectrum')
        self.spectrum_continue_button.setCheckable(False)
        self.spectrum_continue_button.setMinimumWidth(130)
        self.control_layout.addWidget(self.spectrum_continue_button, 0, 1, QtCore.Qt.AlignCenter)

        self.save_spectrum_button = QtWidgets.QPushButton('Save Spectrum')
        self.save_spectrum_button.setMinimumWidth(130)
        self.control_layout.addWidget(self.save_spectrum_button, 0, 2, QtCore.Qt.AlignCenter)

        self.background_button = QtWidgets.QPushButton('Acquire Background')
        self.background_button.setMinimumWidth(130)
        self.control_layout.addWidget(self.background_button, 1, 0, QtCore.Qt.AlignCenter)

        self.save_background_button = QtWidgets.QPushButton('Save Background')
        self.save_background_button.setMinimumWidth(130)
        self.control_layout.addWidget(self.save_background_button, 1, 2, QtCore.Qt.AlignCenter)

        constant_acquisition_label = QtWidgets.QLabel('Constant Acquisition:')
        self.constant_acquisition_switch = ToggleSwitch(parent=self,
                                                        state_names=('Off', 'On'))
        self.control_layout.addWidget(constant_acquisition_label, 2, 0, QtCore.Qt.AlignRight)
        self.control_layout.addWidget(self.constant_acquisition_switch, 2, 1)

        background_correction_label = QtWidgets.QLabel('Background Correction:')
        self.background_correction_switch = ToggleSwitch(parent=self,
                                                         state_names=('Off', 'On'))
        self.control_layout.addWidget(background_correction_label, 3, 0, QtCore.Qt.AlignRight)
        self.control_layout.addWidget(self.background_correction_switch, 3, 1)

        differential_spectrum_label = QtWidgets.QLabel('Differential Spectrum:')
        self.differential_spectrum_switch = ToggleSwitch(parent=self,
                                                         state_names=('Off', 'On'))
        self.control_layout.addWidget(differential_spectrum_label, 4, 0, QtCore.Qt.AlignRight)
        self.control_layout.addWidget(self.differential_spectrum_switch, 4, 1)

        # Create layout and content for the Plot DockWidget
        self.plot_top_layout = QtWidgets.QVBoxLayout()
        self.plot_top_layout.setContentsMargins(1, 1, 1, 1)
        self.plot_top_layout.setSpacing(2)
        plot_top_widget = QtWidgets.QWidget()
        plot_top_widget.setLayout(self.plot_top_layout)
        self.plot_DockWidget.setWidget(plot_top_widget)

        self.fit_layout = QtWidgets.QGridLayout()
        self.fit_layout.setContentsMargins(1, 1, 1, 1)
        self.fit_layout.setSpacing(2)
        fit_layout_widget = QtWidgets.QWidget()
        fit_layout_widget.setLayout(self.fit_layout)
        self.plot_top_layout.addWidget(fit_layout_widget)

        fit_region_group_box = QtWidgets.QGroupBox('Fit Region')
        self.fit_layout.addWidget(fit_region_group_box, 0, 0, 1, 1)
        fit_region_layout = QtWidgets.QGridLayout()
        fit_region_layout.setContentsMargins(1, 7, 1, 1)
        fit_region_layout.setSpacing(2)
        fit_region_group_box.setLayout(fit_region_layout)
        from_label = QtWidgets.QLabel('From:')
        fit_region_layout.addWidget(from_label, 0, 0)
        to_label = QtWidgets.QLabel('To:')
        fit_region_layout.addWidget(to_label, 1, 0)
        self.fit_region_from = ScienDSpinBox()
        self.fit_region_from.setMinimumWidth(100)
        fit_region_layout.addWidget(self.fit_region_from, 0, 1)
        self.fit_region_to = ScienDSpinBox()
        self.fit_region_to.setMinimumWidth(100)
        fit_region_layout.addWidget(self.fit_region_to, 1, 1)

        target_group_box = QtWidgets.QGroupBox('Target')
        self.fit_layout.addWidget(target_group_box, 0, 1, 1, 1)
        target_layout = QtWidgets.QGridLayout()
        target_layout.setContentsMargins(1, 7, 1, 1)
        target_layout.setSpacing(2)
        target_group_box.setLayout(target_layout)
        x_label = QtWidgets.QLabel('X:')
        target_layout.addWidget(x_label, 0, 0)
        y_label = QtWidgets.QLabel('Y:')
        target_layout.addWidget(y_label, 1, 0)
        self.target_x = ScienDSpinBox()
        self.target_x.setDecimals(6, dynamic_precision=False)
        self.target_x.setMinimumWidth(100)
        target_layout.addWidget(self.target_x, 0, 1)
        self.target_y = ScienDSpinBox()
        self.target_y.setDecimals(6, dynamic_precision=False)
        self.target_y.setEnabled(False)
        self.target_y.setMinimumWidth(100)
        target_layout.addWidget(self.target_y, 1, 1)

        axis_type_label = QtWidgets.QLabel('Axis Type:')
        self.axis_type = ToggleSwitch(state_names=('Wavelength', 'Frequency'))
        self.fit_layout.addWidget(axis_type_label, 1, 0, QtCore.Qt.AlignTop)
        self.fit_layout.addWidget(self.axis_type, 1, 1, QtCore.Qt.AlignTop)

        self.plot_widget = pg.PlotWidget(axisItems={'bottom': CustomAxis(orientation='bottom'),
                                                    'left': CustomAxis(orientation='left')})
        self.plot_widget.getAxis('bottom').nudge = 0
        self.plot_widget.getAxis('left').nudge = 0
        self.plot_widget.showGrid(x=True, y=True, alpha=0.5)

        # Create an empty plot curve to be filled later, set its pen
        self.data_curve = self.plot_widget.plot()
        self.data_curve.setPen(palette.c1, width=2)

        self.fit_curve = self.plot_widget.plot()
        self.fit_curve.setPen(palette.c2, width=2)

        self.fit_region = pg.LinearRegionItem(values=(0, 1),
                                              brush=pg.mkBrush(122, 122, 122, 30),
                                              hoverBrush=pg.mkBrush(196, 196, 196, 30))
        self.plot_widget.addItem(self.fit_region)

        self.target_point = pg.TargetItem(pos=(0, 0), size=20)
        self.plot_widget.addItem(self.target_point)

        self.plot_widget.setLabel('left', 'Fluorescence', units='counts/s')
        self.plot_widget.setLabel('bottom', 'Wavelength', units='m')
        self.plot_widget.setMinimumHeight(300)
        self.plot_top_layout.addWidget(self.plot_widget)

        # Create QActions
        self.action_close = QtWidgets.QAction('Close Window')
        self.action_close.setCheckable(False)
        self.action_close.setIcon(QtGui.QIcon(os.path.join(icon_path, 'application-exit.png')))

        self.action_restore_view = QtWidgets.QAction()
        self.action_restore_view.setIcon(
            QtGui.QIcon(os.path.join(icon_path, 'view-refresh.png')))
        self.action_restore_view.setText('Restore')
        self.action_restore_view.setToolTip('Restore the view to the default.')
        self.action_restore_view.setCheckable(False)

        self.action_spectrometer_settings = QtWidgets.QAction()
        self.action_spectrometer_settings.setIcon(
            QtGui.QIcon(os.path.join(icon_path, 'utilities-terminal.png')))
        self.action_spectrometer_settings.setText('Show Spectrometer Settings')
        self.action_spectrometer_settings.setToolTip('Show the Spectrometer Settings.')
        self.action_spectrometer_settings.setCheckable(False)

        self.action_show_fit_settings = QtWidgets.QAction()
        self.action_show_fit_settings.setIcon(
            QtGui.QIcon(os.path.join(icon_path, 'configure.png')))
        self.action_show_fit_settings.setText('Show Fit Settings')
        self.action_show_fit_settings.setToolTip('Show the Fit Settings.')
        self.action_show_fit_settings.setCheckable(False)

        # Create the menu bar
        menu_bar = QtWidgets.QMenuBar()
        self.setMenuBar(menu_bar)

        menu = menu_bar.addMenu('Menu')
        menu.addAction(self.action_restore_view)
        menu.addAction(self.action_spectrometer_settings)
        menu.addAction(self.action_show_fit_settings)
        menu.addSeparator()
        menu.addAction(self.action_close)

        # connecting up the internal signals
        self.action_close.triggered.connect(self.close)
        self.action_restore_view.triggered.connect(self.restore_alignment)

        self.settings_dialog = SettingsDialog()
        self.action_spectrometer_settings.triggered.connect(self.settings_dialog.exec_)

        self.restore_alignment()
        return

    def restore_alignment(self):
        resize_docks = {'widget': list(), 'width': list(), 'height': list()}
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.controls_DockWidget)
        self.controls_DockWidget.setFeatures(self.controls_DockWidget.DockWidgetFloatable |
                                             self.controls_DockWidget.DockWidgetMovable |
                                             self.controls_DockWidget.DockWidgetClosable)
        self.controls_DockWidget.setFloating(False)
        self.controls_DockWidget.show()
        resize_docks['widget'].append(self.controls_DockWidget)
        resize_docks['width'].append(1)
        resize_docks['height'].append(1)

        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.plot_DockWidget)
        self.plot_DockWidget.setFeatures(self.plot_DockWidget.DockWidgetFloatable |
                                         self.plot_DockWidget.DockWidgetMovable |
                                         self.plot_DockWidget.DockWidgetClosable)
        self.plot_DockWidget.setFloating(False)
        self.plot_DockWidget.show()
        resize_docks['widget'].append(self.plot_DockWidget)
        resize_docks['width'].append(1)
        resize_docks['height'].append(1000)

        self.resizeDocks(resize_docks['widget'], resize_docks['height'], QtCore.Qt.Vertical)
        self.resizeDocks(resize_docks['widget'], resize_docks['width'], QtCore.Qt.Horizontal)
