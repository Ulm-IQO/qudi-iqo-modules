# -*- coding: utf-8 -*-
"""
This file contains the qudi gui to continuously display data from a wavemeter device and eventually displays the
 acquired data with the simultaneously obtained counts from a time_series_reader_logic.

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
import time
import numpy as np
from pyqtgraph import PlotWidget
import pyqtgraph as pg

from qudi.util.colordefs import QudiPalettePale as palette
from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util.paths import get_artwork_dir
from typing import Optional, Mapping, Sequence, Union, Tuple, List
from lmfit.model import ModelResult as _ModelResult

from qudi.util.widgets.fitting import FitWidget
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.util.widgets.plotting.interactive_curve import InteractiveCurvesWidget
from scipy import constants


class WavemeterHistogramMainWindow(QtWidgets.QMainWindow):
    """ Create the Main Window for Wavemeter """

    sigFitClicked = QtCore.Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setWindowTitle('qudi: Laser Scanning')

        # Create QActions
        icon_path = os.path.join(get_artwork_dir(), 'icons')

        icon = QtGui.QIcon(os.path.join(icon_path, 'application-exit'))
        self.action_close = QtWidgets.QAction('Close')
        self.action_close.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'document-save'))
        self.action_save = QtWidgets.QAction('Save')
        self.action_save.setToolTip('Save all data')
        self.action_save.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'configure'))
        self.action_show_fit_configuration = QtWidgets.QAction('Fit Configuration')
        self.action_show_fit_configuration.setToolTip(
            'Open a dialog to edit data fitting configurations.')
        self.action_show_fit_configuration.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'record-counter'))
        self.start_trace_Action2 = QtWidgets.QAction('Start Wavemeter')
        self.start_trace_Action2.setCheckable(True)
        self.start_trace_Action2.setToolTip('Start/stop wavemeter to display wavelength')
        self.start_trace_Action2.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        self.start_trace_Action = QtWidgets.QAction('Start trace')
        self.start_trace_Action.setCheckable(True)
        self.start_trace_Action.setToolTip('Start/pause wavemeter for data acquisition with counts')
        self.start_trace_Action.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'edit-clear'))
        self.actionClear_trace_data = QtWidgets.QAction('Clear trace data')
        self.actionClear_trace_data.setIcon(icon)

        self.actionToggle_x_axis = QtWidgets.QAction('Change to frequency')
        self.actionToggle_x_axis.setCheckable(True)

        self.action_autoscale_hist = QtWidgets.QAction('Autoscale histogram')
        self.action_autoscale_hist.setToolTip('Automatically set boundaries of histogram with min/max x value')

        self.show_hist_region = QtWidgets.QAction('Show histogram region')
        self.show_hist_region.setCheckable(True)

        self.action_fit_envelope_histogram = QtWidgets.QAction('Fit envelope')
        self.action_fit_envelope_histogram.setCheckable(True)
        self.action_fit_envelope_histogram.setToolTip(
            'Either fit the mean histogram or envelope histogram. Default is mean histogram. ')

        self.show_all_data_action = QtWidgets.QAction('Show all data')
        self.show_all_data_action.setToolTip(
            'Show all data since due to Gui performace during acquisition only most recent *1000* points are displayed.')

        self.restore_default_view_action = QtWidgets.QAction('Restore default')

        self.save_tag_LineEdit = QtWidgets.QLineEdit()
        self.save_tag_LineEdit.setMaximumWidth(400)
        self.save_tag_LineEdit.setMinimumWidth(150)
        self.save_tag_LineEdit.setToolTip('Enter a nametag which will be\n'
                                          'added to the filename.')

        # Create menu bar and add actions
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu('File')
        menu.addAction(self.start_trace_Action2)
        menu.addAction(self.start_trace_Action)
        menu.addAction(self.actionClear_trace_data)
        menu.addSeparator()
        menu.addAction(self.action_save)
        menu.addSeparator()
        menu.addAction(self.action_close)

        menu = menu_bar.addMenu('View')
        menu.addAction(self.action_show_fit_configuration)
        menu.addAction(self.show_all_data_action)
        menu.addAction(self.actionToggle_x_axis)
        menu.addAction(self.action_autoscale_hist)
        menu.addAction(self.show_hist_region)
        menu.addAction(self.action_fit_envelope_histogram)
        menu.addSeparator()
        menu.addAction(self.restore_default_view_action)

        self.setMenuBar(menu_bar)

        # Create toolbar
        toolbar = QtWidgets.QToolBar()
        toolbar.addAction(self.start_trace_Action2)
        toolbar.addAction(self.start_trace_Action)
        toolbar.addAction(self.actionClear_trace_data)
        toolbar.addAction(self.action_save)
        toolbar.addWidget(self.save_tag_LineEdit)
        toolbar.addSeparator()
        toolbar.addAction(self.action_show_fit_configuration)
        toolbar.addAction(self.show_all_data_action)
        toolbar.addAction(self.actionToggle_x_axis)
        toolbar.addAction(self.action_autoscale_hist)
        toolbar.addAction(self.show_hist_region)
        self.toolbar = toolbar
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.toolbar)

        # Create centralwidget, windows and layout
        self.centralwidget = QtWidgets.QWidget()
        self.DockWidget5 = QtWidgets.QDockWidget()
        self.PlotWidget = InteractiveCurvesWidget()
        self.DockWidget5.setWidget(self.PlotWidget)
        self.PlotWidget.add_marker_selection(position=(0, 0),
                                             mode=self.PlotWidget.SelectionMode.X)
        self.PlotWidget._plot_editor.setVisible(False)

        self.DockWidget4 = QtWidgets.QDockWidget()  # for timeseries scatterplot
        self.scatterPlotWidget = PlotWidget()
        self.DockWidget4.setWidget(self.scatterPlotWidget)

        self.DockWidget3 = QtWidgets.QDockWidget()  # for fit widget
        self.fit_widget = FitWidget()
        self.DockWidget3.setWidget(self.fit_widget)

        # Create current wavelength/freq dock widget
        self.DockWidget2 = QtWidgets.QDockWidget()
        self.dockWidgetContents2 = QtWidgets.QWidget()
        self.dockWidgetContents2.setMinimumHeight(20)
        self.wavelengthLabel2 = QtWidgets.QLabel(self.dockWidgetContents2)
        self.wavelengthLabel2.setFont(QtGui.QFont('Times', 16))
        self.frequencyLabel = QtWidgets.QLabel(self.dockWidgetContents2)
        self.frequencyLabel.setFont(QtGui.QFont('Times', 16))

        # Create Histogram parameter dock widget
        self.DockWidget = QtWidgets.QDockWidget()
        self.dockWidgetContents = QtWidgets.QWidget()
        # label
        self.binLabel = QtWidgets.QLabel(self.dockWidgetContents)
        self.binLabel.setText('Bins (#)')
        self.minLabel = QtWidgets.QLabel(self.dockWidgetContents)
        self.minLabel.setText("Minimum wavelength (nm)")
        self.maxLabel = QtWidgets.QLabel(self.dockWidgetContents)
        self.maxLabel.setText("Maximum wavelength (nm)")
        # spin boxes
        self.binSpinBox = QtWidgets.QSpinBox(self.dockWidgetContents)
        self.binSpinBox.setMinimum(1)
        self.binSpinBox.setMaximum(10000)
        self.binSpinBox.setProperty("value", 200)

        self.minDoubleSpinBox = QtWidgets.QDoubleSpinBox(self.dockWidgetContents)
        self.minDoubleSpinBox.setDecimals(7)
        self.minDoubleSpinBox.setMinimum(1.0)
        self.minDoubleSpinBox.setMaximum(10000.0)
        self.minDoubleSpinBox.setProperty("value", 550.0)

        self.maxDoubleSpinBox = QtWidgets.QDoubleSpinBox(self.dockWidgetContents)
        self.maxDoubleSpinBox.setDecimals(7)
        self.maxDoubleSpinBox.setMinimum(1.0)
        self.maxDoubleSpinBox.setMaximum(10000.0)
        self.maxDoubleSpinBox.setProperty("value", 750.0)

        # Layouts
        layout1 = QtWidgets.QHBoxLayout()
        layout1.addWidget(self.wavelengthLabel2)
        layout1.addStretch()
        layout1.addWidget(self.frequencyLabel)

        layout3 = QtWidgets.QVBoxLayout()
        layout3.addWidget(self.DockWidget5)
        layout3.addWidget(self.DockWidget4)

        layout = QtWidgets.QHBoxLayout()
        layout.addLayout(layout3)
        layout.addWidget(self.DockWidget3)

        layout2 = QtWidgets.QHBoxLayout()
        layout2.addWidget(self.binLabel)
        layout2.addWidget(self.binSpinBox)
        layout2.addStretch()
        layout2.addWidget(self.minLabel)
        layout2.addWidget(self.minDoubleSpinBox)
        layout2.addStretch()
        layout2.addWidget(self.maxLabel)
        layout2.addWidget(self.maxDoubleSpinBox)

        self.centralwidget.setLayout(layout)

        self.setCentralWidget(self.centralwidget)

        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.DockWidget3)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.DockWidget4)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.DockWidget5)

        self.dockWidgetContents.setLayout(layout2)
        self.DockWidget.setWidget(self.dockWidgetContents)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.DockWidget)

        self.dockWidgetContents2.setLayout(layout1)
        self.DockWidget2.setWidget(self.dockWidgetContents2)
        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.DockWidget2)
        # Connect close actions
        self.action_close.triggered.connect(self.close)

        self.sigFitClicked = self.fit_widget.sigDoFit


class WavemeterHistogramGui(GuiBase):
    """
    GUI module to be used in conjunction with WavemeterLogic.

    Example config for copy-paste:

    wavemeter_scanning_gui:
        module.Class: 'wavemeter.wavemeter_scanning_gui.WavemeterHistogramGui'
        connect:
            _wavemeter_histogram_logic_con: wavemeter_scanning_logic
    """
    sigStartCounter = QtCore.Signal()
    sigStopCounter = QtCore.Signal()
    sigDoFit = QtCore.Signal(str)  # fit_config_name
    sigSaveData = QtCore.Signal(str)  # postfix_string

    # declare connectors
    _wavemeter_histogram_logic_con = Connector(interface='WavemeterLogic')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._wavemeter_logic = None
        self._mw = None
        self._pw = None

        self._fit_config_dialog = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        self._wavemeter_logic = self._wavemeter_histogram_logic_con()

        # Use the inherited class 'WavemeterHistogramMainWindow' to create the GUI window
        self._mw = WavemeterHistogramMainWindow()

        # fit stuff
        self._fit_config_dialog = FitConfigurationDialog(parent=self._mw,
                                                         fit_config_model=self._wavemeter_logic.fit_config_model)
        self._mw.fit_widget.link_fit_container(self._wavemeter_logic.get_fit_container())

        # histogram plot widget
        self._pw = self._mw.PlotWidget
        self._pw.set_labels('Wavelength', 'Fluorescence')
        self._pw.set_units('m', 'counts/s')

        # Create an empty plot curve to be filled later, set its pen
        self.curve_data_points = pg.ScatterPlotItem(
            pen=pg.mkPen(palette.c1)
        )
        self._pw._plot_widget.addItem(self.curve_data_points)

        # Add plot for interactive plot widget
        self._pw.plot(name='Histogram', pen=pg.mkPen(palette.c2))
        self._pw.plot(name='Envelope')
        # add fit plot for histogram data
        self._pw.plot_fit(name='Histogram')

        # scatter plot
        self._spw = self._mw.scatterPlotWidget
        self._spw.setLabel('bottom', 'Wavelength', units='m')
        self._spw.setLabel('left', 'Time', units='s')
        self._scatterplot = pg.ScatterPlotItem(
            pen=pg.mkPen(palette.c3)
        )
        self._spw.setXLink(self._pw._plot_widget)
        self._spw.addItem(self._scatterplot)

        #####################
        # Connecting file interactions
        self._mw.action_show_fit_configuration.triggered.connect(self._fit_config_dialog.show)
        self._mw.start_trace_Action.triggered.connect(self.start_clicked)
        self._mw.start_trace_Action2.triggered.connect(self.start_clicked_wavemeter)
        self._mw.actionClear_trace_data.triggered.connect(self.clear_trace_data)
        self._mw.action_save.triggered.connect(self._save_clicked)
        # Connect the view actions
        self._mw.actionToggle_x_axis.triggered.connect(self.toggle_axis)
        self._mw.sigFitClicked.connect(self._fit_clicked)
        self._mw.action_autoscale_hist.triggered.connect(self.autoscale_histogram_gui)
        self._mw.show_hist_region.triggered.connect(self.histogram_region)
        self._mw.restore_default_view_action.triggered.connect(self.restore_default_view)
        self._mw.show_all_data_action.triggered.connect(self.show_all_data)
        self._mw.action_fit_envelope_histogram.triggered.connect(self.fit_which_histogram)

        # Connect signals to logic
        self.sigStartCounter.connect(
            self._wavemeter_logic.start_scanning, QtCore.Qt.QueuedConnection)
        self.sigStopCounter.connect(
            self._wavemeter_logic.stop_scanning, QtCore.Qt.QueuedConnection)
        self.sigDoFit.connect(self._wavemeter_logic.do_fit, QtCore.Qt.QueuedConnection)
        self.sigSaveData.connect(self._wavemeter_logic.save_data, QtCore.Qt.BlockingQueuedConnection)

        # Connect signals from logic
        self._wavemeter_logic.sigDataChanged.connect(
            self.update_data, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigStatusChanged.connect(
            self.update_status, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigStatusChangedDisplaying.connect(
            self.start_clicked_wavemeter, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigFitChanged.connect(self._update_fit_data, QtCore.Qt.QueuedConnection)

        # signal for current wavelength
        self._wavemeter_logic.sigNewWavelength2.connect(self.display_current_wavelength,
                                                        QtCore.Qt.QueuedConnection)
        # Double spin box actions
        self._mw.binSpinBox.setValue(self._wavemeter_logic.get_bins())
        self._mw.binSpinBox.editingFinished.connect(self.recalculate_histogram)
        self._mw.minDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)
        self._mw.maxDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)

        self.show()
        self.restore_default_view()

    def show(self):
        """ Make window visible and put it above all other windows. """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def on_deactivate(self):
        """ Deactivate the module """

        # Connect the main window restore view actions

        # disconnect signals
        self._mw.action_save.triggered.disconnect()
        self._mw.start_trace_Action.triggered.disconnect()
        self._mw.start_trace_Action2.triggered.disconnect()
        self._mw.actionClear_trace_data.triggered.disconnect()
        self._mw.actionToggle_x_axis.triggered.disconnect()
        self._mw.action_autoscale_hist.triggered.disconnect()
        self._mw.sigFitClicked.disconnect()
        self._mw.show_hist_region.triggered.disconnect()
        self._mw.restore_default_view_action.triggered.disconnect()
        self._mw.show_all_data_action.triggered.disconnect()
        self._mw.action_fit_envelope_histogram.triggered.disconnect()

        self._mw.binSpinBox.editingFinished.disconnect()
        self._mw.maxDoubleSpinBox.editingFinished.disconnect()
        self._mw.minDoubleSpinBox.editingFinished.disconnect()

        # Disconnect signals to logic
        self.sigStartCounter.disconnect()
        self.sigStopCounter.disconnect()
        self.sigDoFit.disconnect()
        self.sigSaveData.disconnect()

        # Disconnect signals from logic
        self._wavemeter_logic.sigDataChanged.disconnect()
        self._wavemeter_logic.sigNewWavelength2.disconnect()
        self._wavemeter_logic.sigStatusChanged.disconnect()
        self._wavemeter_logic.sigFitChanged.disconnect()

        self._fit_config_dialog.close()
        self._mw.close()

        self._fit_config_dialog = None
        self._mw = None

    @QtCore.Slot(object, object)
    def display_current_wavelength(self, current_wavelength, current_freq):
        if np.isnan(current_wavelength):
            self._mw.wavelengthLabel2.setText(f'{current_wavelength} nm')
            self._mw.frequencyLabel.setText(f'{current_freq} THz')
        elif current_wavelength is not None:
            self._mw.wavelengthLabel2.setText('{0:,.6f} nm '.format(current_wavelength * 1.0e9))
            self._mw.frequencyLabel.setText('{0:,.9f} THz '.format(current_freq / 1.0e12))
        return

    @QtCore.Slot(object, object, object, object)
    def update_data(self, timings, counts, wavelength, frequency, histogram_axis, histogram, envelope_histogram):
        """ The function that grabs the data and sends it to the plot.
        """
        self._wavemeter_logic.sigDataChanged.disconnect(self.update_data)

        if not self._wavemeter_logic.x_axis_hz_bool:
            if len(wavelength) > 0 and len(wavelength) == len(counts) == len(timings):
                if not np.isnan(wavelength).all():
                    self.curve_data_points.setData(wavelength, counts)
                    self._scatterplot.setData(wavelength, timings)
                if not np.isnan(wavelength[-1]):
                    self._pw.move_marker_selection((wavelength[-1], 0), 0)
                self._pw.set_data('Histogram', x=histogram_axis, y=histogram)
                self._pw.set_data('Envelope', x=histogram_axis, y=envelope_histogram)

        else:
            if len(wavelength) > 0 and len(counts) == len(timings) == len(frequency):
                if not np.isnan(frequency).all():
                    self.curve_data_points.setData(frequency, counts)
                    self._scatterplot.setData(frequency, timings)
                if not np.isnan(frequency[-1]):
                    self._pw.move_marker_selection((frequency[-1], 0), 0)
                self._pw.set_data('Histogram', x=constants.speed_of_light / histogram_axis, y=histogram)
                self._pw.set_data('Envelope', x=constants.speed_of_light / histogram_axis,
                                  y=envelope_histogram)

        self._wavemeter_logic.sigDataChanged.connect(
            self.update_data, QtCore.Qt.QueuedConnection)
        return 0

    @QtCore.Slot(bool)
    def update_status(self, running=None):
        """
        Function to ensure that the GUI displays the current measurement status

        @param bool running: True if the data trace streaming is running
        """
        if running is None:
            running = self._wavemeter_logic.module_state() == 'locked'

        self._mw.start_trace_Action.setChecked(running)
        self._mw.start_trace_Action.setText('Stop trace' if running else 'Start trace')
        icon_path = os.path.join(get_artwork_dir(), 'icons')
        icon1 = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        icon2 = QtGui.QIcon(os.path.join(icon_path, 'stop-counter'))
        self._mw.start_trace_Action.setIcon(icon2 if running else icon1)

        self._mw.start_trace_Action.setEnabled(True)
        self._mw.actionClear_trace_data.setEnabled(not running)
        self._mw.actionToggle_x_axis.setEnabled(not running)
        self._mw.show_all_data_action.setEnabled(not running)
        self._mw.start_trace_Action2.setEnabled(not running)
        return

    @QtCore.Slot()
    def start_clicked(self):
        """ Handling the Start button to stop and restart the counter.
        """
        self._mw.start_trace_Action.setEnabled(False)
        self._mw.actionClear_trace_data.setEnabled(False)
        self._mw.actionToggle_x_axis.setEnabled(False)
        # self._mw.action_save.setEnabled(False)
        self._mw.show_all_data_action.setEnabled(False)
        if self._wavemeter_logic._time_series_logic.module_state() == 'locked':
            if self._mw.start_trace_Action2.isChecked():
                self._mw.start_trace_Action2.setChecked(False)
                self._mw.start_trace_Action2.setText('Start Wavemeter')
                icon_path = os.path.join(get_artwork_dir(), 'icons')
                icon1 = QtGui.QIcon(os.path.join(icon_path, 'record-counter'))
                self._mw.start_trace_Action2.setIcon(icon1)
            self._mw.start_trace_Action2.setEnabled(False)

        if self._mw.start_trace_Action.isChecked():
            self.sigStartCounter.emit()
        else:
            self.sigStopCounter.emit()
        return

    @QtCore.Slot()
    def start_clicked_wavemeter(self):
        if self._wavemeter_logic._stop_flag:
            self._mw.start_trace_Action2.setChecked(False)
            self._wavemeter_logic._stop_flag = False

        if self._mw.start_trace_Action2.isChecked():
            if self._wavemeter_logic.start_displaying_current_wavelength() < 0:
                self._mw.start_trace_Action2.setChecked(False)
                return
            self._mw.start_trace_Action2.setText('Stop Wavemeter')
            icon_path = os.path.join(get_artwork_dir(), 'icons')
            icon2 = QtGui.QIcon(os.path.join(icon_path, 'stop-counter'))
            self._mw.start_trace_Action2.setIcon(icon2)
        else:
            self._wavemeter_logic.stop_displaying_current_wavelength()
            self._mw.start_trace_Action2.setText('Start Wavemeter')
            icon_path = os.path.join(get_artwork_dir(), 'icons')
            icon1 = QtGui.QIcon(os.path.join(icon_path, 'record-counter'))
            self._mw.start_trace_Action2.setIcon(icon1)
        return

    @QtCore.Slot()
    def toggle_axis(self):
        self._mw.actionToggle_x_axis.setEnabled(False)

        if self._mw.actionToggle_x_axis.isChecked():
            # if true toggle to Hz and change boolean x_axis_hz_bool to True and change gui dispaly

            self._mw.actionToggle_x_axis.setText('Change to wavelength')
            # clear any fits
            self._pw.clear_fits()
            # Change the curve plot
            self._wavemeter_logic.x_axis_hz_bool = True
            x_axis_hz = constants.speed_of_light / self._wavemeter_logic.histogram_axis
            self._pw.set_data('Histogram', x=x_axis_hz, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Envelope', x=x_axis_hz, y=self._wavemeter_logic.envelope_histogram)
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[3, :], data[1, :])
                self._pw.move_marker_selection((data[3, -1], 0), 0)
                # Change the scatterplot
                self._scatterplot.setData(data[3, :], data[0, :])

            # change labels
            self._pw.set_labels('Frequency', 'Flourescence')
            self._pw.set_units('Hz', 'counts/s')
            self._spw.setLabel('bottom', 'Frequency', units='Hz')
            # change dockwidget
            self._mw.minLabel.setText("Minimum Frequency (THz)")
            temp = self._mw.minDoubleSpinBox.value()
            self._mw.minDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / self._mw.maxDoubleSpinBox.value())
            self._mw.maxLabel.setText('Maximum Frequency (Thz)')
            self._mw.maxDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / temp)
            if self._mw.show_hist_region.isChecked():
                min, max = self.region.getRegion()
                self.region.setRegion([constants.speed_of_light / max, constants.speed_of_light / min])

        else:
            self._mw.actionToggle_x_axis.setText('Change to frequency')
            self._wavemeter_logic.x_axis_hz_bool = False
            # clear any  fits
            self._pw.clear_fits()
            x_axis = self._wavemeter_logic.histogram_axis
            self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[2, :], data[1, :])
                self._pw.move_marker_selection((data[2, -1], 0), 0)
                # Change the scatterplot
                self._scatterplot.setData(data[2, :], data[0, :])

            self._pw.set_labels('Wavelength', 'Flourescence')
            self._pw.set_units('m', 'counts/s')
            self._spw.setLabel('bottom', 'Wavelength', units='m')
            # change dockwidget
            self._mw.minLabel.setText("Minimum Wavelength (nm)")
            temp = self._mw.minDoubleSpinBox.value()
            self._mw.minDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / self._mw.maxDoubleSpinBox.value())
            self._mw.maxLabel.setText('Maximum Wavelength (nm)')
            self._mw.maxDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / temp)
            if self._mw.show_hist_region.isChecked():
                min, max = self.region.getRegion()
                self.region.setRegion([constants.speed_of_light / max, constants.speed_of_light / min])

        self._mw.actionToggle_x_axis.setEnabled(True)
        return

    def fit_which_histogram(self) -> None:
        self._mw.action_fit_envelope_histogram.setEnabled(False)
        if self._mw.action_fit_envelope_histogram.isChecked():
            self._mw.action_fit_envelope_histogram.setText('Fit histogram')
            self._wavemeter_logic.fit_histogram = False
        else:
            self._mw.action_fit_envelope_histogram.setText('Fit envelope')
            self._wavemeter_logic.fit_histogram = True
        self._mw.action_fit_envelope_histogram.setEnabled(True)

    @QtCore.Slot()
    def histogram_region(self):
        if self._mw.show_hist_region.isChecked():
            if not len(self._wavemeter_logic.wavelength) > 0:
                self.log.warning('No data accumulated yet. Showing rectangular window not possible.')
                self._mw.show_hist_region.setChecked(False)
                return
            if not self._wavemeter_logic.x_axis_hz_bool:
                self.region = pg.LinearRegionItem(
                    values=(self._wavemeter_logic._xmin_histo, self._wavemeter_logic._xmax_histo),
                    orientation='vertical')
            else:
                self.region = pg.LinearRegionItem(
                    values=(constants.speed_of_light / self._wavemeter_logic._xmin_histo,
                            constants.speed_of_light / self._wavemeter_logic._xmax_histo),
                    orientation='vertical'
                )
            self._pw._plot_widget.addItem(self.region)
            self.region.sigRegionChangeFinished.connect(self.region_update)
        else:
            self.region.sigRegionChangeFinished.disconnect()
            self._pw._plot_widget.removeItem(self.region)
        return

    @QtCore.Slot()
    def region_update(self):
        min, max = self.region.getRegion()
        if not self._wavemeter_logic.x_axis_hz_bool:
            self._mw.minDoubleSpinBox.setValue(min * 1e9)
            self._mw.maxDoubleSpinBox.setValue(max * 1e9)
        else:
            self._mw.minDoubleSpinBox.setValue(min * 1e-12)
            self._mw.maxDoubleSpinBox.setValue(max * 1e-12)
        self.recalculate_histogram()
        return

    @QtCore.Slot()
    def clear_trace_data(self):
        # clear trace data and histogram
        self._wavemeter_logic._data_index = 0
        self._wavemeter_logic._trace_data = np.empty((4, 0), dtype=np.float64)
        self._wavemeter_logic.wavelength = []
        self._wavemeter_logic.frequency = []
        self._wavemeter_logic.timings = []
        self._wavemeter_logic.counts = []
        self._wavemeter_logic._xmax = -1
        self._wavemeter_logic._xmin = 1

        self.curve_data_points.clear()
        self._scatterplot.clear()
        self._wavemeter_logic._delay_time = None
        self._pw.clear_fits()

        self._wavemeter_logic.histogram = np.zeros(self._wavemeter_logic.histogram_axis.shape)
        self._wavemeter_logic.envelope_histogram = np.zeros(self._wavemeter_logic.histogram_axis.shape)
        self._wavemeter_logic.rawhisto = np.zeros(self._wavemeter_logic.get_bins())
        self._wavemeter_logic.sumhisto = np.ones(self._wavemeter_logic.get_bins()) * 1.0e-10
        return

    def recalculate_histogram(self) -> None:
        if not self._wavemeter_logic.x_axis_hz_bool:
            self._wavemeter_logic.recalculate_histogram(
                bins=self._mw.binSpinBox.value(),
                xmin=self._mw.minDoubleSpinBox.value() / 1.0e9,
                xmax=self._mw.maxDoubleSpinBox.value() / 1.0e9
            )
        else:  # when in Hz return value into wavelength in m
            self._wavemeter_logic.recalculate_histogram(
                bins=self._mw.binSpinBox.value(),
                xmin=constants.speed_of_light * 1.0e-12 / self._mw.maxDoubleSpinBox.value(),
                xmax=constants.speed_of_light * 1.0e-12 / self._mw.minDoubleSpinBox.value()
            )
        if not self._wavemeter_logic.module_state() == 'locked':
            self.update_histogram_only()

    def autoscale_histogram_gui(self) -> None:
        self._wavemeter_logic.autoscale_histogram()
        self.update_histogram_only()

        if not self._wavemeter_logic.x_axis_hz_bool:
            self._mw.minDoubleSpinBox.setValue(self._wavemeter_logic._xmin_histo * 1.0e9)
            self._mw.maxDoubleSpinBox.setValue(self._wavemeter_logic._xmax_histo * 1.0e9)
        else:
            self._mw.minDoubleSpinBox.setValue(constants.speed_of_light * 1.0e-12 / self._wavemeter_logic._xmax_histo)
            self._mw.maxDoubleSpinBox.setValue(constants.speed_of_light * 1.0e-12 / self._wavemeter_logic._xmin_histo)

    def update_histogram_only(self) -> None:
        if not self._wavemeter_logic.x_axis_hz_bool:
            x_axis = self._wavemeter_logic.histogram_axis
            self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
        else:
            x_axis_hz = constants.speed_of_light / self._wavemeter_logic.histogram_axis
            self._pw.set_data('Histogram', x=x_axis_hz, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Envelope', x=x_axis_hz, y=self._wavemeter_logic.envelope_histogram)

    @QtCore.Slot()
    def restore_default_view(self):
        """ Restore the arrangement of DockWidgets to the default
                """
        # Show all hidden dock widgets
        self._mw.DockWidget.show()
        self._mw.DockWidget2.show()
        self._mw.DockWidget3.show()
        self._mw.DockWidget4.show()
        self._mw.DockWidget5.show()

        # re-dock floating dock widgets
        self._mw.DockWidget.setFloating(False)
        self._mw.DockWidget2.setFloating(False)
        self._mw.DockWidget3.setFloating(False)
        self._mw.DockWidget4.setFloating(False)
        self._mw.DockWidget5.setFloating(False)

        # Arrange dock widgets
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea,
                               self._mw.DockWidget)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.TopDockWidgetArea,
                               self._mw.DockWidget2)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
                               self._mw.DockWidget3)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
                               self._mw.DockWidget5)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
                               self._mw.DockWidget4)

        # toolbar
        self._mw.addToolBar(QtCore.Qt.TopToolBarArea, self._mw.toolbar)

        # Restore status if something went wrong?
        self.update_status()

    @QtCore.Slot()
    def show_all_data(self):
        self._mw.show_all_data_action.setEnabled(False)

        if self._wavemeter_logic.x_axis_hz_bool:
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[3, :], data[1, :])
                self._scatterplot.setData(data[3, :], data[0, :])

        else:
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[2, :], data[1, :])
                self._scatterplot.setData(data[2, :], data[0, :])

        self._mw.show_all_data_action.setEnabled(True)
        return

    def _fit_clicked(self, fit_config: str) -> None:
        self.sigDoFit.emit(fit_config)

    def _update_fit_data(self, fit_config: Optional[str] = None,
                         fit_results: Optional[Mapping[str, Union[None, _ModelResult]]] = None
                         ) -> None:
        """ Function that handles the fit results received from the logic via a signal """
        if fit_config is None or fit_results is None:
            fit_config, fit_results = self._wavemeter_logic.get_fit_results()
        if not fit_config:
            fit_config = 'No Fit'

        if fit_config == 'No Fit':
            self._pw.clear_fits()
        else:
            fit_data = fit_results.high_res_best_fit

            x_data = fit_data[0]
            y_data = fit_data[1]
            self._pw.set_fit_data(name='Histogram', x=x_data, y=y_data, pen='r')

    def _save_clicked(self) -> None:
        filetag = self._mw.save_tag_LineEdit.text()
        self.sigSaveData.emit(filetag)
