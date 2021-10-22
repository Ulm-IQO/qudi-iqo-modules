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

import os
import pyqtgraph as pg
import numpy as np

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util import units
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.widgets.fitting import FitConfigurationDialog, FitWidget
import os
from qudi.util.paths import get_artwork_dir
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from PySide2 import QtCore
from PySide2 import QtWidgets
from PySide2 import QtGui
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.util.widgets.toggle_switch import ToggleSwitch


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
        self.control_layout.setColumnStretch(0, 1)
        self.control_layout.setColumnStretch(2, 1)
        self.control_layout.setColumnStretch(4, 1)
        self.control_layout.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.control_layout.setContentsMargins(1, 1, 1, 1)
        self.control_layout.setSpacing(5)
        control_widget = QtWidgets.QWidget()
        control_widget.setLayout(self.control_layout)
        self.controls_DockWidget.setWidget(control_widget)

        constant_acquisition_label = QtWidgets.QLabel('Constant Acquisition:')
        self.constant_acquisition_switch = ToggleSwitch(parent=self,
                                                        state_names=('Off', 'On'))
        self.control_layout.addWidget(constant_acquisition_label, 1, 0, QtCore.Qt.AlignRight)
        self.control_layout.addWidget(self.constant_acquisition_switch, 1, 1)

        background_correction_label = QtWidgets.QLabel('Background Correction:')
        self.background_correction_switch = ToggleSwitch(parent=self,
                                                         state_names=('Off', 'On'))
        self.control_layout.addWidget(background_correction_label, 1, 2, QtCore.Qt.AlignRight)
        self.control_layout.addWidget(self.background_correction_switch, 1, 3)

        differential_spectrum_label = QtWidgets.QLabel('Differential Spectrum:')
        self.differential_spectrum_switch = ToggleSwitch(parent=self,
                                                         state_names=('Off', 'On'))
        self.control_layout.addWidget(differential_spectrum_label, 1, 4, QtCore.Qt.AlignRight)
        self.control_layout.addWidget(self.differential_spectrum_switch, 1, 5)

        self.spectrum_button = QtWidgets.QPushButton('Acquire Spectrum')
        self.spectrum_button.setCheckable(False)
        self.control_layout.addWidget(self.spectrum_button, 0, 0, QtCore.Qt.AlignCenter)

        self.background_button = QtWidgets.QPushButton('Acquire Background')
        self.control_layout.addWidget(self.background_button, 0, 1, QtCore.Qt.AlignCenter)

        self.save_spectrum_button = QtWidgets.QPushButton('Save Spectrum')
        self.control_layout.addWidget(self.save_spectrum_button, 0, 2, QtCore.Qt.AlignCenter)

        self.save_background_button = QtWidgets.QPushButton('Save Background')
        self.control_layout.addWidget(self.save_background_button, 0, 3, QtCore.Qt.AlignCenter)

        # Create layout and content for the Plot DockWidget
        self.plot_top_layout = QtWidgets.QVBoxLayout()
        self.plot_top_layout.setContentsMargins(1, 1, 1, 1)
        self.plot_top_layout.setSpacing(0)
        plot_top_widget = QtWidgets.QWidget()
        plot_top_widget.setLayout(self.plot_top_layout)
        self.plot_DockWidget.setWidget(plot_top_widget)

        self.plot_widget = pg.PlotWidget(axisItems={'bottom': CustomAxis(orientation='bottom'),
                                                    'left': CustomAxis(orientation='left')})
        self.plot_widget.getAxis('bottom').nudge = 0
        self.plot_widget.getAxis('left').nudge = 0
        self.plot_widget.showGrid(x=True, y=True, alpha=0.5)
        self.plot_item = self.plot_widget.plotItem

        # create a new ViewBox, link the right axis to its coordinate system
        right_axis = pg.ViewBox()
        self.plot_item.showAxis('right')
        self.plot_item.scene().addItem(right_axis)
        self.plot_item.getAxis('right').linkToView(right_axis)
        right_axis.setXLink(self.plot_item)

        # create a new ViewBox, link the top axis to its coordinate system
        top_axis = pg.ViewBox()
        self.plot_item.showAxis('top')
        self.plot_item.scene().addItem(top_axis)
        self.plot_item.getAxis('top').linkToView(top_axis)
        top_axis.setYLink(self.plot_item)
        top_axis.invertX(b=True)

        # Create an empty plot curve to be filled later, set its pen
        self.data_curve = self.plot_widget.plot()
        self.data_curve.setPen(palette.c1, width=2)

        self.fit_curve = self.plot_widget.plot()
        self.fit_curve.setPen(palette.c2, width=2)

        self.plot_widget.setLabel('left', 'Fluorescence', units='counts/s')
        self.plot_widget.setLabel('right', 'Number of Points', units='#')
        self.plot_widget.setLabel('bottom', 'Wavelength', units='m')
        self.plot_widget.setLabel('top', 'Relative Frequency', units='Hz')
        self.plot_widget.setMinimumHeight(300)

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

        self.action_show_fit_settings = QtWidgets.QAction()
        self.action_show_fit_settings.setText('Show Fit Settings')
        self.action_show_fit_settings.setToolTip('Show the Fit Settings.')
        self.action_show_fit_settings.setCheckable(False)

        # Create the menu bar
        menu_bar = QtWidgets.QMenuBar()
        self.setMenuBar(menu_bar)

        menu = menu_bar.addMenu('Menu')
        menu.addAction(self.action_restore_view)
        menu.addAction(self.action_show_fit_settings)
        menu.addSeparator()
        menu.addAction(self.action_close)

        # connecting up the internal signals
        self.action_close.triggered.connect(self.close)
        self.action_restore_view.triggered.connect(self.restore_alignment)

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


class SpectrometerGui(GuiBase):
    """
    """

    # declare connectors
    spectrumlogic = Connector(interface='SpectrometerLogic')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._fsd = None
        self._fit_widget = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        # setting up the window
        self._mw = SpectrometerMainWindow()

        # Fit settings dialogs
        self._fsd = FitConfigurationDialog(parent=self._mw,
                                           fit_config_model=self.spectrumlogic().fit_config_model)
        self._mw.action_show_fit_settings.triggered.connect(self._fsd.show)

        self._fit_widget = FitWidget(fit_container=self.spectrumlogic().fit_container)

        self._mw.plot_top_layout.addWidget(self._fit_widget)
        self._fit_widget.sigDoFit.connect(self.spectrumlogic().do_fit)

        self._mw.plot_top_layout.addWidget(self._mw.plot_widget)

        # Connect signals
        self.spectrumlogic().sig_data_updated.connect(self.update_data)
        self.spectrumlogic().sig_spectrum_fit_updated.connect(self.update_fit)
        self.spectrumlogic().sig_fit_domain_updated.connect(self.update_fit_domain)

        self._mw.show()
        self.update_data()

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # disconnect signals
        self._mw.action_show_fit_settings.triggered.disconnect()
        self._fsd.close()
        self._fsd = None
        self._fit_widget.sigDoFit.disconnect()

        self.spectrumlogic().sig_data_updated.disconnect(self.update_data)
        self.spectrumlogic().sig_spectrum_fit_updated.disconnect(self.update_fit)
        self.spectrumlogic().sig_fit_domain_updated.disconnect(self.update_fit_domain)

        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def update_data(self):
        """ The function that grabs the data and sends it to the plot.
        """
        # erase previous fit line
        self._mw.fit_curve.setData(x=[], y=[])

        # draw new data
        self._mw.data_curve.setData(x=self.spectrumlogic().wavelength,
                                    y=self.spectrumlogic().spectrum)

    def update_fit(self, fit_data, result_str_dict, current_fit):
        """ Update the drawn fit curve and displayed fit results.
        """
        if current_fit != 'No Fit':
            # display results as formatted text
            self._mw.spectrum_fit_results_DisplayWidget.clear()
            try:
                formated_results = units.create_formatted_output(result_str_dict)
            except:
                formated_results = 'this fit does not return formatted results'
            self._mw.spectrum_fit_results_DisplayWidget.setPlainText(formated_results)

            # redraw the fit curve in the GUI plot.
            self._curve2.setData(x=fit_data[0, :], y=fit_data[1, :])

    def record_single_spectrum(self):
        """ Handle resume of the scanning without resetting the data.
        """
        self.spectrumlogic().get_single_spectrum()

    def start_differential_measurement(self):

        # Change enabling of GUI actions
        self._mw.stop_diff_spec_Action.setEnabled(True)
        self._mw.start_diff_spec_Action.setEnabled(False)
        self._mw.rec_single_spectrum_Action.setEnabled(False)
        self._mw.resume_diff_spec_Action.setEnabled(False)

        self.spectrumlogic().start_differential_spectrum()

    def stop_differential_measurement(self):
        self.spectrumlogic().stop_differential_spectrum()

        # Change enabling of GUI actions
        self._mw.stop_diff_spec_Action.setEnabled(False)
        self._mw.start_diff_spec_Action.setEnabled(True)
        self._mw.rec_single_spectrum_Action.setEnabled(True)
        self._mw.resume_diff_spec_Action.setEnabled(True)

    def resume_differential_measurement(self):
        self.spectrumlogic().resume_differential_spectrum()

        # Change enabling of GUI actions
        self._mw.stop_diff_spec_Action.setEnabled(True)
        self._mw.start_diff_spec_Action.setEnabled(False)
        self._mw.rec_single_spectrum_Action.setEnabled(False)
        self._mw.resume_diff_spec_Action.setEnabled(False)

    def save_spectrum_data(self):
        self.spectrumlogic().save_spectrum_data()

    def correct_background(self):
        self.spectrumlogic().background_correction = self._mw.correct_background_Action.isChecked()

    def acquire_background(self):
        self.spectrumlogic().get_single_spectrum(background=True)

    def save_background_data(self):
        self.spectrumlogic().save_spectrum_data(background=True)

    def set_fit_domain(self):
        """ Set the fit domain in the spectrum logic to values given by the GUI spinboxes.
        """
        lambda_min = self._mw.fit_domain_min_doubleSpinBox.value()
        lambda_max = self._mw.fit_domain_max_doubleSpinBox.value()

        new_fit_domain = np.array([lambda_min, lambda_max])

        self.spectrumlogic().set_fit_domain(new_fit_domain)

    def reset_fit_domain_all_data(self):
        """ Reset the fit domain to match the full data set.
        """
        self.spectrumlogic().set_fit_domain()

    def update_fit_domain(self, domain):
        """ Update the displayed fit domain to new values (set elsewhere).
        """
        self._mw.fit_domain_min_doubleSpinBox.setValue(domain[0])
        self._mw.fit_domain_max_doubleSpinBox.setValue(domain[1])
