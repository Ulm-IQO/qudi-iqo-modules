# -*- coding: utf-8 -*-
"""
Contains the GUI module for the laser scanning toolchain.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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
import numpy as np
from pyqtgraph import mkPen
from PySide2 import QtCore, QtWidgets, QtGui

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.paths import get_artwork_dir
from qudi.util.colordefs import QudiPalette
from typing import Optional, Union, Tuple
from lmfit.model import ModelResult as _ModelResult

from qudi.util.datafitting import FitConfigurationsModel, FitContainer
from qudi.util.widgets.fitting import FitWidget, FitConfigurationDialog
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
        self.bins_spinbox.setRange(3, 10000)
        self.bins_spinbox.setValue(200)
        self.min_spinbox = ScienDSpinBox()
        self.min_spinbox.setMinimumWidth(120)
        self.min_spinbox.setDecimals(7, dynamic_precision=False)
        self.min_spinbox.setRange(1e-9, np.inf)
        self.min_spinbox.setValue(550e-9)
        self.min_spinbox.setSuffix('m')
        self.max_spinbox = ScienDSpinBox()
        self.max_spinbox.setMinimumWidth(120)
        self.max_spinbox.setDecimals(7, dynamic_precision=False)
        self.max_spinbox.setRange(1e-9, np.inf)
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

    def toggle_laser_type(self, is_frequency: bool) -> None:
        if is_frequency:
            self.min_spinbox.setSuffix('Hz')
            self.max_spinbox.setSuffix('Hz')
        else:
            self.min_spinbox.setSuffix('m')
            self.max_spinbox.setSuffix('m')

    def __emit_changes(self) -> None:
        self.sigSettingsChanged.emit(*self.get_histogram_settings())


class _LaserValueDisplayWidget(QtWidgets.QWidget):
    """ """
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)
        self._is_frequency: bool = False
        # Create labels
        self._unit_label = QtWidgets.QLabel()
        self._unit_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        self._value_label = QtWidgets.QLabel()
        self._value_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        # Change font size
        font = self._unit_label.font()
        font.setPointSize(16)
        self._unit_label.setFont(font)
        self._value_label.setFont(font)
        # layout
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(self._value_label)
        layout.addWidget(self._unit_label)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setStretch(0, 1)
        self.setLayout(layout)
        # Set default values
        self.toggle_is_frequency(self._is_frequency)
        self.set_value(float('nan'))

    def set_value(self, value: float) -> None:
        if np.isfinite(value):
            if self._is_frequency:
                self._value_label.setText(f'{value / 1e12:.9f}')
            else:
                self._value_label.setText(f'{value * 1e9:.6f}')
        else:
            self._value_label.setText('NaN')

    def toggle_is_frequency(self, is_frequency: bool) -> None:
        if is_frequency:
            self._unit_label.setText('THz')
        else:
            self._unit_label.setText('nm')
        self._is_frequency = is_frequency


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
        icon.addFile(os.path.join(icon_path, 'stop-counter'), state=QtGui.QIcon.State.On)
        self.action_laser_only = QtWidgets.QAction('Start/Stop Laser Recording')
        self.action_laser_only.setCheckable(True)
        self.action_laser_only.setToolTip(
            'Start/Stop laser data acquisition only without running an actual scan'
        )
        self.action_laser_only.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        icon.addFile(os.path.join(icon_path, 'stop-counter'), state=QtGui.QIcon.State.On)
        self.action_start_stop = QtWidgets.QAction('Start/Stop Laser Scanning')
        self.action_start_stop.setCheckable(True)
        self.action_start_stop.setToolTip('Start/Stop laser scanning')
        self.action_start_stop.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'edit-clear'))
        self.action_clear_data = QtWidgets.QAction('Clear trace data')
        self.action_clear_data.setIcon(icon)

        self.action_show_frequency = QtWidgets.QAction('Frequency Mode')
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
            'Either fit the mean histogram or envelope histogram. Default is mean histogram.'
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
        menu.addAction(actions.action_save)
        menu.addSeparator()
        menu.addAction(actions.action_show_fit_configuration)
        menu.addAction(actions.action_fit_envelope_histogram)
        menu.addAction(actions.action_autoscale_histogram)
        menu.addSeparator()
        menu.addAction(actions.action_close)
        menu = self.addMenu('View')
        menu.addAction(actions.action_show_frequency)
        menu.addAction(actions.action_show_histogram_region)
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
        self.addAction(actions.action_show_fit_configuration)
        self.addAction(actions.action_save)
        self.addWidget(self.save_tag_line_edit)
        self.addSeparator()
        self.addAction(actions.action_show_frequency)
        self.addAction(actions.action_autoscale_histogram)
        self.addAction(actions.action_show_histogram_region)
        self.addAction(actions.action_fit_envelope_histogram)


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
        self.histogram_widget.add_region_selection(span=[(0, 0), (0, 0)],
                                                   mode=InteractiveCurvesWidget.SelectionMode.X)
        self.histogram_widget.hide_region_selections()
        self.histogram_widget.set_selection_mutable(False)
        self.histogram_widget.toggle_plot_editor(False)

        self.current_laser_label = _LaserValueDisplayWidget()

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
    sigStartScan = QtCore.Signal(bool)  # laser_only
    sigStopScan = QtCore.Signal()
    sigDoFit = QtCore.Signal(str, bool)  # fit_config_name, fit_envelope
    sigSaveData = QtCore.Signal(str)  # save_tag
    sigClearData = QtCore.Signal()
    sigAutoscaleHistogram = QtCore.Signal()
    sigHistogramSettingsChanged = QtCore.Signal(tuple, int)  # span, bins
    sigLaserTypeToggled = QtCore.Signal(bool)  # is_frequency

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
        # Update data from logic
        self._update_laser_type(logic.laser_is_frequency)
        self._update_status(logic.module_state() == 'locked', logic.laser_only_mode)
        self._update_histogram_settings(*logic.histogram_settings)
        self._update_data(*logic.scan_data, *logic.histogram_data)

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
        for plot in self._mw.histogram_widget.plot_names:
            self._mw.histogram_widget.remove_fit_plot(plot)
            self._mw.histogram_widget.remove_plot(plot)
        self._mw.histogram_widget.plot(name='Data', pen=None, symbol='o')
        self._mw.histogram_widget.plot(name='Histogram', pen=mkPen(QudiPalette.c2))
        self._mw.histogram_widget.plot(name='Envelope', pen=mkPen(QudiPalette.c1))
        self._mw.histogram_widget.plot_fit(name='Histogram', pen='r')
        self._mw.histogram_widget.set_plot_selection({'Data'     : True,
                                                      'Histogram': True,
                                                      'Envelope' : False})

    def __init_scatter_plot(self) -> None:
        for plot in self._mw.scatter_widget.plot_names:
            self._mw.scatter_widget.remove_plot(plot)
        self._mw.scatter_widget.plot('Data', pen=None, symbol='o')

    def __connect_logic(self) -> None:
        logic = self._laser_scanning_logic()
        # To logic
        self.sigStartScan.connect(logic.start_scan, QtCore.Qt.QueuedConnection)
        self.sigStopScan.connect(logic.stop_scan, QtCore.Qt.QueuedConnection)
        self.sigDoFit.connect(logic.do_fit, QtCore.Qt.QueuedConnection)
        self.sigSaveData.connect(logic.save_data, QtCore.Qt.BlockingQueuedConnection)
        self.sigClearData.connect(logic.clear_data, QtCore.Qt.QueuedConnection)
        self.sigAutoscaleHistogram.connect(logic.autoscale_histogram, QtCore.Qt.QueuedConnection)
        self.sigHistogramSettingsChanged.connect(logic.configure_histogram,
                                                 QtCore.Qt.QueuedConnection)
        self.sigLaserTypeToggled.connect(logic.toggle_laser_type, QtCore.Qt.QueuedConnection)
        # From logic
        logic.sigDataChanged.connect(self._update_data, QtCore.Qt.QueuedConnection)
        logic.sigStatusChanged.connect(self._update_status, QtCore.Qt.QueuedConnection)
        logic.sigFitChanged.connect(self._update_fit_data, QtCore.Qt.QueuedConnection)
        logic.sigHistogramSettingsChanged.connect(self._update_histogram_settings,
                                                  QtCore.Qt.QueuedConnection)
        logic.sigLaserTypeChanged.connect(self._update_laser_type, QtCore.Qt.QueuedConnection)

    def __disconnect_logic(self) -> None:
        logic = self._laser_scanning_logic()
        # To logic
        self.sigStartScan.disconnect()
        self.sigStopScan.disconnect()
        self.sigDoFit.disconnect()
        self.sigSaveData.disconnect()
        self.sigClearData.disconnect()
        self.sigAutoscaleHistogram.disconnect()
        self.sigHistogramSettingsChanged.disconnect()
        self.sigLaserTypeToggled.disconnect()
        # From logic
        logic.sigDataChanged.disconnect(self._update_data)
        logic.sigStatusChanged.disconnect(self._update_status)
        logic.sigFitChanged.disconnect(self._update_fit_data)
        logic.sigHistogramSettingsChanged.disconnect(self._update_histogram_settings)
        logic.sigLaserTypeChanged.disconnect(self._update_laser_type)

    def __connect_actions(self) -> None:
        # File actions
        self._mw.gui_actions.action_start_stop.triggered.connect(self._start_stop_clicked)
        self._mw.gui_actions.action_laser_only.triggered.connect(self._laser_only_clicked)
        self._mw.gui_actions.action_clear_data.triggered.connect(self._clear_data_clicked)
        self._mw.gui_actions.action_save.triggered.connect(self._save_clicked)
        # View actions
        self._mw.gui_actions.action_show_frequency.triggered.connect(
            self._toggle_laser_type_clicked
        )
        self._mw.gui_actions.action_autoscale_histogram.triggered.connect(
            self._autoscale_histogram_clicked
        )
        self._mw.gui_actions.action_show_histogram_region.triggered.connect(
            self._show_region_clicked
        )

    def __disconnect_actions(self) -> None:
        # File actions
        self._mw.gui_actions.action_start_stop.triggered.disconnect()
        self._mw.gui_actions.action_clear_data.triggered.disconnect()
        self._mw.gui_actions.action_save.triggered.disconnect()
        self._mw.gui_actions.action_laser_only.triggered.disconnect()
        # View actions
        self._mw.gui_actions.action_show_frequency.triggered.disconnect()
        self._mw.gui_actions.action_autoscale_histogram.triggered.disconnect()
        self._mw.gui_actions.action_show_histogram_region.triggered.disconnect()

    def __connect_widgets(self) -> None:
        self._mw.histogram_settings_widget.sigSettingsChanged.connect(
            self._histogram_settings_edited
        )
        self._mw.fit_widget.sigDoFit.connect(self._fit_clicked)

    def __disconnect_widgets(self) -> None:
        self._mw.histogram_settings_widget.sigSettingsChanged.disconnect()
        self._mw.fit_widget.sigDoFit.disconnect()

    def _update_current_laser_value(self, value: float) -> None:
        """ """
        self._mw.current_laser_label.set_value(value)
        self._mw.histogram_widget.move_marker_selection((value, 0), 0)

    @QtCore.Slot(bool)
    def _update_laser_type(self, is_frequency: bool) -> None:
        data_channel_units = self._laser_scanning_logic().data_channel_units
        if len(data_channel_units) > 0:
            channel, unit = next(iter(data_channel_units.items()))
        else:
            channel = self._mw.histogram_widget.labels[1]
            unit = self._mw.histogram_widget.units[1]
        self._mw.current_laser_label.toggle_is_frequency(is_frequency)
        self._mw.histogram_settings_widget.toggle_laser_type(is_frequency)
        self._mw.gui_actions.action_show_frequency.setChecked(is_frequency)
        if is_frequency:
            self._mw.scatter_widget.set_labels('frequency', 'time')
            self._mw.scatter_widget.set_units('Hz', 's')
            self._mw.histogram_widget.set_labels('frequency', channel)
            self._mw.histogram_widget.set_units('Hz', unit)
        else:
            self._mw.scatter_widget.set_labels('wavelength', 'time')
            self._mw.scatter_widget.set_units('m', 's')
            self._mw.histogram_widget.set_labels('wavelength', channel)
            self._mw.histogram_widget.set_units('m', unit)

    @QtCore.Slot(tuple, int)
    def _update_histogram_settings(self, span: Tuple[float, float], bins: int) -> None:
        self._mw.histogram_settings_widget.blockSignals(True)
        self._mw.histogram_settings_widget.set_histogram_settings(span, bins)
        self._mw.histogram_settings_widget.blockSignals(False)
        self._mw.histogram_widget.blockSignals(True)
        self._mw.histogram_widget.move_region_selection(span=[span, (0, 0)], index=0)
        self._mw.histogram_widget.blockSignals(False)

    @QtCore.Slot(object, object, object, object, object, object)
    def _update_data(self,
                     timestamps: np.ndarray,
                     laser_data: np.ndarray,
                     scan_data: np.ndarray,
                     bins: np.ndarray,
                     histogram: np.ndarray,
                     envelope: np.ndarray) -> None:
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
            laser_data = laser_data[-self._max_display_points:]
            timestamps = timestamps[-self._max_display_points:]
            self._update_current_laser_value(laser_data[-1])
            self._mw.scatter_widget.set_data('Data',
                                             x=laser_data,
                                             y=timestamps - timestamps[0])
            if data.size == 0:
                self._mw.histogram_widget.set_data('Data', x=None, y=None)
            else:
                # FIXME: Support multiple data channels. Ignore all additional channels for now.
                if data.ndim > 1:
                    data = data[:, 0]
                self._mw.histogram_widget.set_data('Data',
                                                   x=laser_data,
                                                   y=data[-self._max_display_points:])

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

    @QtCore.Slot(str, object)
    def _update_fit_data(self, fit_config: str, fit_result: Union[None, _ModelResult]) -> None:
        """ Function that handles the fit results received from the logic via a signal """
        if (not fit_config) or (fit_config == 'No Fit') or (fit_result is None):
            self._mw.histogram_widget.set_fit_data(name='Histogram', x=None, y=None)
        else:
            fit_data = fit_result.high_res_best_fit
            self._mw.histogram_widget.set_fit_data(name='Histogram', x=fit_data[0], y=fit_data[1])

    @QtCore.Slot(bool, bool)
    def _update_status(self, running: bool, laser_only: bool) -> None:
        """ Function to ensure that the GUI displays the current measurement status """
        # Update checked states
        self._mw.gui_actions.action_start_stop.setChecked(running and not laser_only)
        self._mw.gui_actions.action_laser_only.setChecked(running and laser_only)
        # Re-Enable actions
        self._mw.gui_actions.action_start_stop.setEnabled(not running or not laser_only)
        self._mw.gui_actions.action_laser_only.setEnabled(not running or laser_only)
        self._mw.gui_actions.action_clear_data.setEnabled(True)
        self._mw.gui_actions.action_show_frequency.setEnabled(True)
        self._mw.gui_actions.action_save.setEnabled(True)
        self._mw.gui_actions.action_autoscale_histogram.setEnabled(True)
        self._mw.gui_actions.action_fit_envelope_histogram.setEnabled(True)
        self._mw.gui_actions.action_show_histogram_region.setEnabled(True)

    def _start_stop_clicked(self):
        """ Handling the Start button to stop and restart the counter """
        start = self._mw.gui_actions.action_start_stop.isChecked()
        if start:
            self.sigStartScan.emit(False)
        else:
            self.sigStopScan.emit()
        self._mw.gui_actions.action_start_stop.setEnabled(False)
        self._mw.gui_actions.action_laser_only.setEnabled(False)
        self._mw.gui_actions.action_clear_data.setEnabled(False)
        self._mw.gui_actions.action_show_frequency.setEnabled(False)
        self._mw.gui_actions.action_save.setEnabled(False)
        self._mw.gui_actions.action_autoscale_histogram.setEnabled(False)
        self._mw.gui_actions.action_fit_envelope_histogram.setEnabled(False)
        self._mw.gui_actions.action_show_histogram_region.setEnabled(False)

    def _laser_only_clicked(self):
        start = self._mw.gui_actions.action_laser_only.isChecked()
        if start:
            self.sigStartScan.emit(True)
        else:
            self.sigStopScan.emit()
        self._mw.gui_actions.action_start_stop.setEnabled(False)
        self._mw.gui_actions.action_laser_only.setEnabled(False)
        self._mw.gui_actions.action_clear_data.setEnabled(False)
        self._mw.gui_actions.action_show_frequency.setEnabled(False)
        self._mw.gui_actions.action_save.setEnabled(False)
        self._mw.gui_actions.action_autoscale_histogram.setEnabled(False)
        self._mw.gui_actions.action_fit_envelope_histogram.setEnabled(False)
        self._mw.gui_actions.action_show_histogram_region.setEnabled(False)

    def _show_region_clicked(self) -> None:
        show = self._mw.gui_actions.action_show_histogram_region.isChecked()
        if show:
            self._mw.histogram_widget.show_region_selections()
        else:
            self._mw.histogram_widget.hide_region_selections()

    def _histogram_settings_edited(self, span: Tuple[float, float], bins: int) -> None:
        self.sigHistogramSettingsChanged.emit(span, bins)

    def _clear_data_clicked(self):
        self.sigClearData.emit()

    def _fit_clicked(self, fit_config: str) -> None:
        self.sigDoFit.emit(fit_config,
                           self._mw.gui_actions.action_fit_envelope_histogram.isChecked())

    def _save_clicked(self) -> None:
        self.sigSaveData.emit(self.save_tag)

    def _autoscale_histogram_clicked(self) -> None:
        self.sigAutoscaleHistogram.emit()

    def _toggle_laser_type_clicked(self) -> None:
        self.sigLaserTypeToggled.emit(self._mw.gui_actions.action_show_frequency.isChecked())
