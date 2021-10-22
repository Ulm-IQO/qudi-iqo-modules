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
from qudi.util.uic import loadUi
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget


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
        # self.setStyleSheet('border: 1px solid #f00;')  # debugging help for the gui

        self.setTabPosition(QtCore.Qt.TopDockWidgetArea, QtWidgets.QTabWidget.North)
        self.setTabPosition(QtCore.Qt.BottomDockWidgetArea, QtWidgets.QTabWidget.North)
        self.setTabPosition(QtCore.Qt.LeftDockWidgetArea, QtWidgets.QTabWidget.North)
        self.setTabPosition(QtCore.Qt.RightDockWidgetArea, QtWidgets.QTabWidget.North)

        self.controls_DockWidget = AdvancedDockWidget('Controls', parent=self)
        self.plot_DockWidget = AdvancedDockWidget('Plots', parent=self)

        # Create layout and content for the Controls DockWidget
        self.control_layout = QtWidgets.QGridLayout()
        self.control_layout.setColumnStretch(3, 1)
        self.control_layout.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.control_layout.setContentsMargins(1, 1, 1, 1)
        self.control_layout.setSpacing(5)
        control_widget = QtWidgets.QWidget()
        control_widget.setLayout(self.control_layout)
        self.controls_DockWidget.setWidget(control_widget)

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

        # Create QActions
        icon_path = os.path.join(get_artwork_dir(), 'icons', 'oxygen', '22x22')

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
        return

    def restore_alignment(self):
        pass


class SpectrometerGui(GuiBase):
    """
    """

    # declare connectors
    spectrumlogic = Connector(interface='SpectrometerLogic')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw=None
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

        self._mw.pulsed_data_layout.addWidget(self._fit_widget)
        self._fit_widget.sigDoFit.connect(self.do_fit)

        self._mw.plot_top_layout.addWidget(self._mw.plot_widget)

        self._mw.stop_diff_spec_Action.setEnabled(False)
        self._mw.resume_diff_spec_Action.setEnabled(False)
        self._mw.correct_background_Action.setChecked(self.spectrumlogic().background_correction)

        self.update_data()

        # Connect singals
        self._mw.rec_single_spectrum_Action.triggered.connect(self.record_single_spectrum)
        self._mw.start_diff_spec_Action.triggered.connect(self.start_differential_measurement)
        self._mw.stop_diff_spec_Action.triggered.connect(self.stop_differential_measurement)
        self._mw.resume_diff_spec_Action.triggered.connect(self.resume_differential_measurement)

        self._mw.save_spectrum_Action.triggered.connect(self.save_spectrum_data)
        self._mw.correct_background_Action.triggered.connect(self.correct_background)
        self._mw.acquire_background_Action.triggered.connect(self.acquire_background)
        self._mw.save_background_Action.triggered.connect(self.save_background_data)

        self._mw.restore_default_view_Action.triggered.connect(self.restore_default_view)

        self.spectrumlogic().sig_specdata_updated.connect(self.update_data)
        self.spectrumlogic().spectrum_fit_updated_Signal.connect(self.update_fit)
        self.spectrumlogic().fit_domain_updated_Signal.connect(self.update_fit_domain)

        self._mw.show()

        # Internal user input changed signals
        self._mw.fit_domain_min_doubleSpinBox.valueChanged.connect(self.set_fit_domain)
        self._mw.fit_domain_max_doubleSpinBox.valueChanged.connect(self.set_fit_domain)

        # Internal trigger signals
        self._mw.do_fit_PushButton.clicked.connect(self.do_fit)
        self._mw.fit_domain_all_data_pushButton.clicked.connect(self.reset_fit_domain_all_data)

        # # fit settings
        # self._fsd = FitSettingsDialog(self.spectrumlogic().fc)
        # self._fsd.sigFitsUpdated.connect(self._mw.fit_methods_ComboBox.setFitFunctions)
        # self._fsd.applySettings()
        # self._mw.action_FitSettings.triggered.connect(self._fsd.show)

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # disconnect signals
        self._mw.action_show_fit_settings.triggered.disconnect()
        self._fsd.close()
        self._fsd = None
        self._fit_widget.sigDoFit.disconnect()

        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        QtWidgets.QMainWindow.show(self._mw)
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

    def do_fit(self):
        """ Command spectrum logic to do the fit with the chosen fit function.
        """
        fit_function = self._mw.fit_methods_ComboBox.getCurrentFit()[0]
        self.spectrumlogic().do_fit(fit_function)

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

    def restore_default_view(self):
        """ Restore the arrangement of DockWidgets to the default
        """
        # Show any hidden dock widgets
        self._mw.spectrum_fit_dockWidget.show()

        # re-dock any floating dock widgets
        self._mw.spectrum_fit_dockWidget.setFloating(False)

        # Arrange docks widgets
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea(QtCore.Qt.TopDockWidgetArea),
                               self._mw.spectrum_fit_dockWidget
                               )

        # Set the toolbar to its initial top area
        self._mw.addToolBar(QtCore.Qt.TopToolBarArea,
                            self._mw.measure_ToolBar)
        self._mw.addToolBar(QtCore.Qt.TopToolBarArea,
                            self._mw.background_ToolBar)
        self._mw.addToolBar(QtCore.Qt.TopToolBarArea,
                            self._mw.differential_ToolBar)
        return 0
