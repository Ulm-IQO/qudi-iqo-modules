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
from qudi.core.configoption import ConfigOption
from qudi.util.paths import get_artwork_dir
from typing import Optional, Mapping, Sequence, Union, Tuple, List
from lmfit.model import ModelResult as _ModelResult

from qudi.util.widgets.fitting import FitWidget, FitConfigurationsModel, FitContainer
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.util.widgets.plotting.interactive_curve import InteractiveCurvesWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class _HistogramSettingsWidget(QtWidgets.QWidget):
    """ """

    sigSettingsChanged = QtCore.Signal(tuple, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # labels
        self.bin_label = QtWidgets.QLabel('Bins:')
        self.bin_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.min_label = QtWidgets.QLabel('Minimum:')
        self.min_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.max_label = QtWidgets.QLabel('Maximum:')
        self.max_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        # spin boxes
        self.bins_spinbox = QtWidgets.QSpinBox()
        self.bins_spinbox.setMinimumWidth(100)
        self.bins_spinbox.setRange(1, 10000)
        self.bins_spinbox.setValue(200)
        self.min_spinbox = ScienDSpinBox()
        self.min_spinbox.setMinimumWidth(100)
        self.min_spinbox.setDecimals(7, dynamic_precision=False)
        self.min_spinbox.setRange(1e-9, 10000e-9)
        self.min_spinbox.setValue(550e-9)
        self.min_spinbox.setSuffix('m')
        self.max_spinbox = ScienDSpinBox()
        self.max_spinbox.setMinimumWidth(100)
        self.max_spinbox.setDecimals(7, dynamic_precision=False)
        self.max_spinbox.setRange(1e-9, 10000e-9)
        self.max_spinbox.setValue(750e-9)
        self.max_spinbox.setSuffix('m')
        # layout
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(self.bin_label)
        layout.addWidget(self.bins_spinbox)
        layout.addWidget(self.min_label)
        layout.addWidget(self.min_spinbox)
        layout.addWidget(self.max_label)
        layout.addWidget(self.max_spinbox)
        layout.setStretch(0, 1)
        layout.setStretch(2, 1)
        layout.setStretch(4, 1)
        self.setLayout(layout)
        # Connect signals
        self.min_spinbox.editingFinished.connect(self.__emit_changes)
        self.max_spinbox.editingFinished.connect(self.__emit_changes)
        self.bins_spinbox.editingFinished.connect(self.__emit_changes)

    def get_histogram_settings(self) -> Tuple[Tuple[float, float], int]:
        """ """
        span = [self.min_spinbox.value(), self.max_spinbox.value()]
        return (min(span), max(span)), self.bins_spinbox.value()

    def set_histogram_settings(self, span: Tuple[float, float], bins: int) -> None:
        """ """
        self.min_spinbox.blockSignals(True)
        self.max_spinbox.blockSignals(True)
        self.bins_spinbox.blockSignals(True)
        self.min_spinbox.setValue(min(span))
        self.max_spinbox.setValue(max(span))
        self.bins_spinbox.setValue(bins)
        self.min_spinbox.blockSignals(False)
        self.max_spinbox.blockSignals(False)
        self.bins_spinbox.blockSignals(False)
        self.__emit_changes()

    @QtCore.Slot()
    def __emit_changes(self) -> None:
        self.sigSettingsChanged.emit(self.get_histogram_settings())


class _LaserScanningActions:
    """ """
    def __init__(self):
        super().__init__()
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
            'Open a dialog to edit data fitting configurations.'
        )
        self.action_show_fit_configuration.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'record-counter'))
        self.action_laser_only = QtWidgets.QAction('Start Laser Reading')
        self.action_laser_only.setCheckable(True)
        self.action_laser_only.setToolTip('Start/Stop laser wavelength/frequency acquisition only')
        self.action_laser_only.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        self.action_start_stop = QtWidgets.QAction('Start Laser Scanning')
        self.action_start_stop.setCheckable(True)
        self.action_start_stop.setToolTip('Start/Stop laser scanning')
        self.action_start_stop.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'edit-clear'))
        self.action_clear_data = QtWidgets.QAction('Clear trace data')
        self.action_clear_data.setIcon(icon)

        self.action_show_frequency = QtWidgets.QAction('Change to frequency')
        self.action_show_frequency.setCheckable(True)

        self.action_autoscale_histogram = QtWidgets.QAction('Autoscale histogram')
        self.action_autoscale_histogram.setToolTip(
            'Automatically set boundaries of histogram with min/max x value'
        )

        self.action_show_histogram_region = QtWidgets.QAction('Show histogram region')
        self.action_show_histogram_region.setCheckable(True)

        self.action_fit_envelope_histogram = QtWidgets.QAction('Fit envelope')
        self.action_fit_envelope_histogram.setCheckable(True)
        self.action_fit_envelope_histogram.setToolTip(
            'Either fit the mean histogram or envelope histogram. Default is mean histogram. ')

        self.action_show_all_data = QtWidgets.QAction('Show all data')
        self.action_show_all_data.setToolTip(
            'Show all data since due to Gui performace during acquisition only most recent *1000* '
            'points are displayed.'
        )

        self.action_restore_view = QtWidgets.QAction('Restore default')


class _LaserScanningMenuBar(QtWidgets.QMenuBar):
    """ """
    def __init__(self, actions: _LaserScanningActions, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)
        menu = self.addMenu('File')
        menu.addAction(actions.action_laser_only)
        menu.addAction(actions.action_start_stop)
        menu.addAction(actions.action_clear_data)
        menu.addSeparator()
        menu.addAction(actions.action_save)
        menu.addSeparator()
        menu.addAction(actions.action_close)
        menu = self.addMenu('View')
        menu.addAction(actions.action_show_fit_configuration)
        menu.addAction(actions.action_show_all_data)
        menu.addAction(actions.action_show_frequency)
        menu.addAction(actions.action_autoscale_histogram)
        menu.addAction(actions.action_show_histogram_region)
        menu.addAction(actions.action_fit_envelope_histogram)
        menu.addSeparator()
        menu.addAction(actions.action_restore_view)


class _LaserScanningToolBar(QtWidgets.QToolBar):
    """ """
    def __init__(self,
                 actions: _LaserScanningActions,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)
        self.setMovable(False)
        self.setFloatable(False)

        self.save_tag_line_edit = QtWidgets.QLineEdit()
        self.save_tag_line_edit.setMaximumWidth(400)
        self.save_tag_line_edit.setMinimumWidth(150)
        self.save_tag_line_edit.setToolTip('Enter a nametag which will be added to the filename.')

        self.addAction(actions.action_laser_only)
        self.addAction(actions.action_start_stop)
        self.addAction(actions.action_clear_data)
        self.addAction(actions.action_save)
        self.addWidget(self.save_tag_line_edit)
        self.addSeparator()
        self.addAction(actions.action_show_fit_configuration)
        self.addAction(actions.action_show_all_data)
        self.addAction(actions.action_show_frequency)
        self.addAction(actions.action_autoscale_histogram)
        self.addAction(actions.action_show_histogram_region)


class LaserScanningMainWindow(QtWidgets.QMainWindow):
    """ Create the main window for laser scanning toolchain """

    def __init__(self, fit_config_model: FitConfigurationsModel, fit_container: FitContainer):
        super().__init__()
        self.setWindowTitle('qudi: Laser Scanning')

        # Create QActions
        self.gui_actions = _LaserScanningActions()
        # Create menu bar and add actions
        self.setMenuBar(_LaserScanningMenuBar(self.gui_actions))
        # Create toolbar and add actions
        self.toolbar = _LaserScanningToolBar(self.gui_actions)
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.toolbar)

        # Create central widget with wavelength/freq display and histogram plot
        self.histogram_widget = InteractiveCurvesWidget()
        self.histogram_widget.setMinimumHeight(400)
        self.histogram_widget.add_marker_selection(position=(0, 0),
                                                   mode=InteractiveCurvesWidget.SelectionMode.X)
        self.histogram_widget.toggle_plot_editor(False)

        self.current_laser_label = QtWidgets.QLabel()
        font = self.current_laser_label.font()
        font.setPointSize(16)
        self.current_laser_label.setFont(font)
        self.current_laser_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.current_laser_label)
        layout.addWidget(self.histogram_widget)
        layout.setStretch(1, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        # Create dockwidgets and add them
        self.scatter_widget = InteractiveCurvesWidget()  # xy_region_selection_handles=False
        self.scatter_widget.toggle_plot_selector(False)
        self.scatter_widget.toggle_plot_editor(False)
        # self.scatter_widget.toggle_plot_selector(False)
        self.scatter_dockwidget = QtWidgets.QDockWidget('Scan Data')
        self.scatter_dockwidget.setWidget(self.scatter_widget)

        self.fit_dockwidget = QtWidgets.QDockWidget('Fit')
        self.fit_widget = FitWidget()
        self.fit_dockwidget.setWidget(self.fit_widget)

        self.histogram_settings_dockwidget = QtWidgets.QDockWidget('Histogram Settings')
        self.histogram_settings_widget = _HistogramSettingsWidget()
        self.histogram_settings_dockwidget.setWidget(self.histogram_settings_widget)

        # Create child dialog for fit settings and link it to the fit widget
        self.fit_config_dialog = FitConfigurationDialog(parent=self,
                                                        fit_config_model=fit_config_model)
        self.fit_widget.link_fit_container(fit_container)

        # Connect some actions
        self.gui_actions.action_close.triggered.connect(self.close)
        self.gui_actions.action_restore_view.triggered.connect(self.restore_default)
        self.gui_actions.action_show_fit_configuration.triggered.connect(
            self.fit_config_dialog.show
        )
        self.restore_default()

    def restore_default(self) -> None:
        """ """
        # Show all hidden dock widgets
        self.scatter_dockwidget.show()
        self.fit_dockwidget.show()
        self.histogram_settings_dockwidget.show()

        # re-dock floating dock widgets
        self.scatter_dockwidget.setFloating(False)
        self.fit_dockwidget.setFloating(False)
        self.histogram_settings_dockwidget.setFloating(False)

        # Arrange dock widgets
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.fit_dockwidget)
        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.histogram_settings_dockwidget)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.scatter_dockwidget)


class LaserScanningGui(GuiBase):
    """ GUI module to be used in conjunction with qudi.logic.laser_scanning_logic.LaserScanningLogic

    Example config for copy-paste:

    laser_scanning_gui:
        module.Class: 'laserscanning.laser_scanning_gui.LaserScanningGui'
        connect:
            laser_scanning_logic: <laser_scanning_logic>
    """
    sigStartScan = QtCore.Signal()
    sigStopScan = QtCore.Signal()
    sigDoFit = QtCore.Signal(str)  # fit_config_name
    sigSaveData = QtCore.Signal(str)  # save_tag
    sigClearData = QtCore.Signal()
    sigAutoscaleHistogram = QtCore.Signal()

    # declare connectors
    _laser_scanning_logic = Connector(name='laser_scanning_logic', interface='LaserScanningLogic')

    # declare config options
    _max_display_points = ConfigOption(name='max_display_points',
                                       default=1_000,
                                       missing='warn',
                                       constructor=lambda x: max(1, int(x)))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw: LaserScanningMainWindow = None
        self.__is_frequency: bool = False
        self.__time_axis = None

    def on_activate(self) -> None:
        # Initialize main window
        logic = self._laser_scanning_logic()
        self._mw = LaserScanningMainWindow(fit_config_model=logic.fit_config_model,
                                           fit_container=logic.fit_container)

        # Configure plot widgets
        self.__init_histogram_plot()
        self.__init_scatter_plot()
        # Connect signals
        self.__connect_actions()
        self.__connect_widgets()
        self.__connect_logic()
        # Show GUI window
        self.show()

    def show(self) -> None:
        self._mw.show()
        self._mw.raise_()
        self._mw.activateWindow()

    def on_deactivate(self) -> None:
        # Disconnect signals
        self.__disconnect_actions()
        self.__disconnect_widgets()
        self.__disconnect_logic()

        # Close and delete main window
        self._mw.close()
        self._mw.deleteLater()
        self._mw = None

    @property
    def save_tag(self) -> str:
        try:
            return self._mw.toolbar.save_tag_line_edit.text()
        except AttributeError:
            return ''

    def __init_histogram_plot(self) -> None:
        channel, unit = next(iter(self._laser_scanning_logic().data_channel_units.items()))
        for plot in self._mw.histogram_widget.plot_names:
            self._mw.histogram_widget.remove_fit_plot(plot)
            self._mw.histogram_widget.remove_plot(plot)
        if self.__is_frequency:
            self._mw.histogram_widget.set_labels('frequency', channel)
            self._mw.histogram_widget.set_units('Hz', unit)
        else:
            self._mw.histogram_widget.set_labels('wavelength', channel)
            self._mw.histogram_widget.set_units('m', unit)
        self._mw.histogram_widget.plot(name='Data', pen=None, symbol='o')  # symbolPen=pg.mkPen(palette.c1))
        self._mw.histogram_widget.plot(name='Histogram')  # , pen=pg.mkPen(palette.c2))
        self._mw.histogram_widget.plot(name='Envelope')
        self._mw.histogram_widget.plot_fit(name='Histogram', pen='r')

    def __init_scatter_plot(self) -> None:
        for plot in self._mw.scatter_widget.plot_names:
            self._mw.scatter_widget.remove_plot(plot)
        if self.__is_frequency:
            self._mw.scatter_widget.set_labels('frequency', 'time')
            self._mw.scatter_widget.set_units('Hz', 's')
        else:
            self._mw.scatter_widget.set_labels('wavelength', 'time')
            self._mw.scatter_widget.set_units('m', 's')
        self._mw.scatter_widget.plot('Data', pen=None, symbol='o')  # symbolPen=pg.mkPen(palette.c3))

    def __connect_logic(self) -> None:
        logic = self._laser_scanning_logic()
        # To logic
        self.sigStartScan.connect(logic.start_scan, QtCore.Qt.QueuedConnection)
        self.sigStopScan.connect(logic.stop_scan, QtCore.Qt.QueuedConnection)
        self.sigDoFit.connect(logic.do_fit, QtCore.Qt.QueuedConnection)
        self.sigSaveData.connect(logic.save_data, QtCore.Qt.BlockingQueuedConnection)
        self.sigClearData.connect(logic.clear_data, QtCore.Qt.QueuedConnection)
        self.sigAutoscaleHistogram.connect(logic.autoscale_histogram, QtCore.Qt.QueuedConnection)
        # From logic
        logic.sigDataChanged.connect(self._update_data, QtCore.Qt.QueuedConnection)
        logic.sigStatusChanged.connect(self._update_status, QtCore.Qt.QueuedConnection)
        logic.sigFitChanged.connect(self._update_fit_data, QtCore.Qt.QueuedConnection)
        logic.sigConfigurationChanged.connect(self._update_configuration,
                                              QtCore.Qt.QueuedConnection)

    def __disconnect_logic(self) -> None:
        logic = self._laser_scanning_logic()
        # To logic
        self.sigStartScan.disconnect()
        self.sigStopScan.disconnect()
        self.sigDoFit.disconnect()
        self.sigSaveData.disconnect()
        self.sigClearData.disconnect()
        self.sigAutoscaleHistogram.disconnect()
        # From logic
        logic.sigDataChanged.disconnect(self._update_data)
        logic.sigStatusChanged.disconnect(self._update_status)
        logic.sigFitChanged.disconnect(self._update_fit_data)
        logic.sigConfigurationChanged.disconnect(self._update_configuration)

    def __connect_actions(self) -> None:
        # FIXME:
        # File actions
        self._mw.gui_actions.action_start_stop.triggered.connect(self._start_stop_clicked)
        self._mw.gui_actions.action_clear_data.triggered.connect(self._clear_data_clicked)
        self._mw.gui_actions.action_save.triggered.connect(self._save_clicked)
        # View actions
        # self._mw.action_show_frequency.triggered.connect(self.toggle_axis)
        # self._mw.sigFitClicked.connect(self._fit_clicked)
        self._mw.gui_actions.action_autoscale_histogram.triggered.connect(
            self._autoscale_histogram_clicked
        )
        # self._mw.gui_actions.action_show_histogram_region.triggered.connect(self.histogram_region)
        # self._mw.gui_actions.action_restore_view.triggered.connect(self.restore_default_view)
        # self._mw.gui_actions.action_show_all_data.triggered.connect(self.show_all_data)
        # self._mw.gui_actions.action_fit_envelope_histogram.triggered.connect(self.fit_which_histogram)

    def __disconnect_actions(self) -> None:
        # FIXME:
        # File actions
        self._mw.gui_actions.action_start_stop.triggered.disconnect()
        self._mw.gui_actions.action_clear_data.triggered.disconnect()
        self._mw.gui_actions.action_save.triggered.disconnect()
        # View actions
        self._mw.gui_actions.action_show_frequency.triggered.disconnect()
        # self._mw.sigFitClicked.disconnect()
        self._mw.gui_actions.action_autoscale_histogram.triggered.disconnect()
        self._mw.gui_actions.action_show_histogram_region.triggered.disconnect()
        self._mw.gui_actions.action_restore_view.triggered.disconnect()
        self._mw.gui_actions.action_show_all_data.triggered.disconnect()
        self._mw.gui_actions.action_fit_envelope_histogram.triggered.disconnect()

    def __connect_widgets(self) -> None:
        self._mw.histogram_settings_widget.sigSettingsChanged.connect(
            self._histogram_settings_edited
        )

    def __disconnect_widgets(self) -> None:
        self._mw.histogram_settings_widget.sigSettingsChanged.disconnect()

    def _update_current_laser_value(self, value: float) -> None:
        """ """
        if self.__is_frequency:
            self._mw.current_laser_label.setText(f'{value / 1e12:.9f} THz')
        else:
            self._mw.current_laser_label.setText(f'{value * 1e9:.6f} nm')
        self._mw.histogram_widget.move_marker_selection((value, 0), 0)

    def _update_configuration(self, config) -> None:
        # FIXME:
        self.__is_frequency = False
        self.__init_scatter_plot()
        self.__init_histogram_plot()

    @QtCore.Slot(object, object, object, object, object)
    def _update_data(self,
                     timestamps: Union[None, np.ndarray],
                     laser_data: np.ndarray,
                     scan_data: np.ndarray,
                     bins: np.ndarray,
                     histogram: np.ndarray,
                     envelope: np.ndarray) -> None:
        # Create time axis if none is provided by streamer
        if timestamps is None:
            sample_rate = self._laser_scanning_logic().sample_rate
            timestamps = np.arange(min(self._max_display_points, laser_data.size), dtype=np.float64)
            timestamps /= sample_rate
        self._update_scan_data(timestamps=timestamps, laser_data=laser_data, data=scan_data)
        self._update_histogram_data(bins=bins, histogram=histogram, envelope=envelope)

    def _update_scan_data(self,
                          timestamps: np.ndarray,
                          laser_data: np.ndarray,
                          data: np.ndarray) -> None:
        """ """
        if laser_data.size == 0:
            self._update_current_laser_value(0)
            self._mw.histogram_widget.set_data('Data', x=None, y=None)
            self._mw.scatter_widget.set_data('Data', x=None, y=None)
        else:
            self._update_current_laser_value(laser_data[-1])
            if data.size == 0:
                self._mw.histogram_widget.set_data('Data', x=None, y=None)
                self._mw.scatter_widget.set_data('Data', x=None, y=None)
            else:
                # FIXME: Support multiple data channels. Ignore all additional channels for now.
                if data.ndim > 1:
                    data = data[:, 0]
                laser_data = laser_data[-self._max_display_points:]
                timestamps = timestamps[-self._max_display_points:]
                self._mw.histogram_widget.set_data('Data',
                                                   x=laser_data,
                                                   y=data[-self._max_display_points:])
                self._mw.scatter_widget.set_data('Data',
                                                 x=laser_data,
                                                 y=timestamps - timestamps[0])

    def _update_histogram_data(self,
                               bins: np.ndarray,
                               histogram: np.ndarray,
                               envelope: np.ndarray) -> None:
        """ """
        if histogram.size == 0:
            self._mw.histogram_widget.set_data('Histogram', x=None, y=None)
            self._mw.histogram_widget.set_data('Envelope', x=None, y=None)
        else:
            # FIXME: Support multiple data channels. Ignore all additional channels for now.
            if histogram.ndim > 1:
                histogram = histogram[:, 0]
                envelope = envelope[:, 0]
            self._mw.histogram_widget.set_data('Histogram', x=bins, y=histogram)
            self._mw.histogram_widget.set_data('Envelope', x=bins, y=envelope)

    @QtCore.Slot(object, object)
    def _update_fit_data(self, fit_config: str, fit_result: Union[None, _ModelResult]) -> None:
        """ Function that handles the fit results received from the logic via a signal """
        if (not fit_config) or (fit_config == 'No Fit') or (fit_result is None):
            self._mw.histogram_widget.clear_fits()
        else:
            fit_data = fit_result.high_res_best_fit
            self._mw.histogram_widget.set_fit_data('Histogram',
                                                   x=fit_data[0],
                                                   y=fit_data[1])

    @QtCore.Slot(bool)
    def _update_status(self, running: bool) -> None:
        """ Function to ensure that the GUI displays the current measurement status """
        self._mw.gui_actions.action_start_stop.setChecked(running)
        self._mw.gui_actions.action_start_stop.setText('Stop Scan' if running else 'Start Scan')
        icon_path = os.path.join(get_artwork_dir(), 'icons')
        icon1 = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        icon2 = QtGui.QIcon(os.path.join(icon_path, 'stop-counter'))
        self._mw.gui_actions.action_start_stop.setIcon(icon2 if running else icon1)

        self._mw.gui_actions.action_start_stop.setEnabled(True)
        self._mw.gui_actions.action_clear_data.setEnabled(not running)
        self._mw.gui_actions.action_show_frequency.setEnabled(not running)
        self._mw.gui_actions.action_show_all_data.setEnabled(not running)
        self._mw.gui_actions.action_laser_only.setEnabled(not running)

    def _start_stop_clicked(self):
        """ Handling the Start button to stop and restart the counter """
        # FIXME: Configure logic
        if self._mw.gui_actions.action_start_stop.isChecked():
            self.sigStartScan.emit()
        else:
            self.sigStopScan.emit()
        # self._mw.gui_actions.action_start_stop.setEnabled(False)
        # self._mw.gui_actions.action_clear_data.setEnabled(False)
        # self._mw.gui_actions.action_show_frequency.setEnabled(False)
        # # self._mw.gui_actions.action_save.setEnabled(False)
        # self._mw.gui_actions.action_show_all_data.setEnabled(False)
        # if self._wavemeter_logic._time_series_logic.module_state() == 'locked':
        #     if self._mw.action_laser_only.isChecked():
        #         self._mw.action_laser_only.setChecked(False)
        #         self._mw.action_laser_only.setText('Start Wavemeter')
        #         icon_path = os.path.join(get_artwork_dir(), 'icons')
        #         icon1 = QtGui.QIcon(os.path.join(icon_path, 'record-counter'))
        #         self._mw.action_laser_only.setIcon(icon1)
        #     self._mw.action_laser_only.setEnabled(False)
        #
        # if self._mw.action_start_stop.isChecked():
        #     self.sigStartCounter.emit()
        # else:
        #     self.sigStopCounter.emit()

    # @QtCore.Slot()
    # def start_clicked_wavemeter(self):
    #     if self._wavemeter_logic._stop_flag:
    #         self._mw.action_laser_only.setChecked(False)
    #         self._wavemeter_logic._stop_flag = False
    #
    #     if self._mw.action_laser_only.isChecked():
    #         if self._wavemeter_logic.start_displaying_current_wavelength() < 0:
    #             self._mw.action_laser_only.setChecked(False)
    #             return
    #         self._mw.action_laser_only.setText('Stop Wavemeter')
    #         icon_path = os.path.join(get_artwork_dir(), 'icons')
    #         icon2 = QtGui.QIcon(os.path.join(icon_path, 'stop-counter'))
    #         self._mw.action_laser_only.setIcon(icon2)
    #     else:
    #         self._wavemeter_logic.stop_displaying_current_wavelength()
    #         self._mw.action_laser_only.setText('Start Wavemeter')
    #         icon_path = os.path.join(get_artwork_dir(), 'icons')
    #         icon1 = QtGui.QIcon(os.path.join(icon_path, 'record-counter'))
    #         self._mw.action_laser_only.setIcon(icon1)

    # @QtCore.Slot()
    # def toggle_axis(self):
    #     self._mw.action_show_frequency.setEnabled(False)
    #
    #     if self._mw.action_show_frequency.isChecked():
    #         # if true toggle to Hz and change boolean x_axis_hz_bool to True and change gui dispaly
    #
    #         self._mw.action_show_frequency.setText('Change to wavelength')
    #         # clear any fits
    #         self._pw.clear_fits()
    #         # Change the curve plot
    #         self._wavemeter_logic.x_axis_hz_bool = True
    #         x_axis_hz = constants.speed_of_light / self._wavemeter_logic.histogram_axis
    #         self._pw.set_data('Histogram', x=x_axis_hz, y=self._wavemeter_logic.histogram)
    #         self._pw.set_data('Envelope', x=x_axis_hz, y=self._wavemeter_logic.envelope_histogram)
    #         data = self._wavemeter_logic._trace_data
    #         if len(data[0]) > 0:
    #             self.curve_data_points.setData(data[3, :], data[1, :])
    #             self._pw.move_marker_selection((data[3, -1], 0), 0)
    #             # Change the scatterplot
    #             self._scatterplot.setData(data[3, :], data[0, :])
    #
    #         # change labels
    #         self._pw.set_labels('Frequency', 'Flourescence')
    #         self._pw.set_units('Hz', 'counts/s')
    #         self._spw.setLabel('bottom', 'Frequency', units='Hz')
    #         # change dockwidget
    #         self._mw.minLabel.setText("Minimum Frequency (THz)")
    #         temp = self._mw.minDoubleSpinBox.value()
    #         self._mw.minDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / self._mw.maxDoubleSpinBox.value())
    #         self._mw.maxLabel.setText('Maximum Frequency (Thz)')
    #         self._mw.maxDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / temp)
    #         if self._mw.action_show_histogram_region.isChecked():
    #             min, max = self.region.getRegion()
    #             self.region.setRegion([constants.speed_of_light / max, constants.speed_of_light / min])
    #
    #     else:
    #         self._mw.action_show_frequency.setText('Change to frequency')
    #         self._wavemeter_logic.x_axis_hz_bool = False
    #         # clear any  fits
    #         self._pw.clear_fits()
    #         x_axis = self._wavemeter_logic.histogram_axis
    #         self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
    #         self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
    #         data = self._wavemeter_logic._trace_data
    #         if len(data[0]) > 0:
    #             self.curve_data_points.setData(data[2, :], data[1, :])
    #             self._pw.move_marker_selection((data[2, -1], 0), 0)
    #             # Change the scatterplot
    #             self._scatterplot.setData(data[2, :], data[0, :])
    #
    #         self._pw.set_labels('Wavelength', 'Flourescence')
    #         self._pw.set_units('m', 'counts/s')
    #         self._spw.setLabel('bottom', 'Wavelength', units='m')
    #         # change dockwidget
    #         self._mw.minLabel.setText("Minimum Wavelength (nm)")
    #         temp = self._mw.minDoubleSpinBox.value()
    #         self._mw.minDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / self._mw.maxDoubleSpinBox.value())
    #         self._mw.maxLabel.setText('Maximum Wavelength (nm)')
    #         self._mw.maxDoubleSpinBox.setValue(constants.speed_of_light * 1e-3 / temp)
    #         if self._mw.action_show_histogram_region.isChecked():
    #             min, max = self.region.getRegion()
    #             self.region.setRegion([constants.speed_of_light / max, constants.speed_of_light / min])
    #
    #     self._mw.action_show_frequency.setEnabled(True)
    #     return

    # def fit_which_histogram(self) -> None:
    #     self._mw.action_fit_envelope_histogram.setEnabled(False)
    #     if self._mw.action_fit_envelope_histogram.isChecked():
    #         self._mw.action_fit_envelope_histogram.setText('Fit histogram')
    #         self._wavemeter_logic.fit_histogram = False
    #     else:
    #         self._mw.action_fit_envelope_histogram.setText('Fit envelope')
    #         self._wavemeter_logic.fit_histogram = True
    #     self._mw.action_fit_envelope_histogram.setEnabled(True)
    #
    # @QtCore.Slot()
    # def histogram_region(self):
    #     if self._mw.action_show_histogram_region.isChecked():
    #         if not len(self._wavemeter_logic.wavelength) > 0:
    #             self.log.warning('No data accumulated yet. Showing rectangular window not possible.')
    #             self._mw.action_show_histogram_region.setChecked(False)
    #             return
    #         if not self._wavemeter_logic.x_axis_hz_bool:
    #             self.region = pg.LinearRegionItem(
    #                 values=(self._wavemeter_logic._xmin_histo, self._wavemeter_logic._xmax_histo),
    #                 orientation='vertical')
    #         else:
    #             self.region = pg.LinearRegionItem(
    #                 values=(constants.speed_of_light / self._wavemeter_logic._xmin_histo,
    #                         constants.speed_of_light / self._wavemeter_logic._xmax_histo),
    #                 orientation='vertical'
    #             )
    #         self._pw._plot_widget.addItem(self.region)
    #         self.region.sigRegionChangeFinished.connect(self.region_update)
    #     else:
    #         self.region.sigRegionChangeFinished.disconnect()
    #         self._pw._plot_widget.removeItem(self.region)
    #     return
    #
    # @QtCore.Slot()
    # def region_update(self):
    #     min, max = self.region.getRegion()
    #     if not self._wavemeter_logic.x_axis_hz_bool:
    #         self._mw.minDoubleSpinBox.setValue(min * 1e9)
    #         self._mw.maxDoubleSpinBox.setValue(max * 1e9)
    #     else:
    #         self._mw.minDoubleSpinBox.setValue(min * 1e-12)
    #         self._mw.maxDoubleSpinBox.setValue(max * 1e-12)
    #     self.recalculate_histogram()
    #     return

    def _histogram_settings_edited(self, span: Tuple[float, float], bins: int) -> None:
        self._laser_scanning_logic().configure_histogram(span, bins)

    def _clear_data_clicked(self):
        self.sigClearData.emit()

    def _fit_clicked(self, fit_config: str) -> None:
        self.sigDoFit.emit(fit_config)

    def _save_clicked(self) -> None:
        self.sigSaveData.emit(self.save_tag)

    def _autoscale_histogram_clicked(self) -> None:
        self.sigAutoscaleHistogram.emit()

    # def recalculate_histogram(self) -> None:
    #     if not self._wavemeter_logic.x_axis_hz_bool:
    #         self._wavemeter_logic.recalculate_histogram(
    #             bins=self._mw.binSpinBox.value(),
    #             xmin=self._mw.minDoubleSpinBox.value() / 1.0e9,
    #             xmax=self._mw.maxDoubleSpinBox.value() / 1.0e9
    #         )
    #     else:  # when in Hz return value into wavelength in m
    #         self._wavemeter_logic.recalculate_histogram(
    #             bins=self._mw.binSpinBox.value(),
    #             xmin=constants.speed_of_light * 1.0e-12 / self._mw.maxDoubleSpinBox.value(),
    #             xmax=constants.speed_of_light * 1.0e-12 / self._mw.minDoubleSpinBox.value()
    #         )
    #     if not self._wavemeter_logic.module_state() == 'locked':
    #         self.update_histogram_only()
    #
    # def autoscale_histogram_gui(self) -> None:
    #     self._wavemeter_logic.autoscale_histogram()
    #     self.update_histogram_only()
    #
    #     if not self._wavemeter_logic.x_axis_hz_bool:
    #         self._mw.minDoubleSpinBox.setValue(self._wavemeter_logic._xmin_histo * 1.0e9)
    #         self._mw.maxDoubleSpinBox.setValue(self._wavemeter_logic._xmax_histo * 1.0e9)
    #     else:
    #         self._mw.minDoubleSpinBox.setValue(constants.speed_of_light * 1.0e-12 / self._wavemeter_logic._xmax_histo)
    #         self._mw.maxDoubleSpinBox.setValue(constants.speed_of_light * 1.0e-12 / self._wavemeter_logic._xmin_histo)
    #
    # def update_histogram_only(self) -> None:
    #     if not self._wavemeter_logic.x_axis_hz_bool:
    #         x_axis = self._wavemeter_logic.histogram_axis
    #         self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
    #         self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
    #     else:
    #         x_axis_hz = constants.speed_of_light / self._wavemeter_logic.histogram_axis
    #         self._pw.set_data('Histogram', x=x_axis_hz, y=self._wavemeter_logic.histogram)
    #         self._pw.set_data('Envelope', x=x_axis_hz, y=self._wavemeter_logic.envelope_histogram)

    # @QtCore.Slot()
    # def show_all_data(self):
    #     self._mw.action_show_all_data.setEnabled(False)
    #
    #     if self._wavemeter_logic.x_axis_hz_bool:
    #         data = self._wavemeter_logic._trace_data
    #         if len(data[0]) > 0:
    #             self.curve_data_points.setData(data[3, :], data[1, :])
    #             self._scatterplot.setData(data[3, :], data[0, :])
    #
    #     else:
    #         data = self._wavemeter_logic._trace_data
    #         if len(data[0]) > 0:
    #             self.curve_data_points.setData(data[2, :], data[1, :])
    #             self._scatterplot.setData(data[2, :], data[0, :])
    #
    #     self._mw.action_show_all_data.setEnabled(True)
    #     return
