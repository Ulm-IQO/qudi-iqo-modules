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

import numpy as np
from PySide2 import QtCore
from typing import Union, Tuple
from lmfit.model import ModelResult as _ModelResult

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.interface.scannable_laser_interface import ScannableLaserSettings
from qudi.gui.laserscanning.widgets.main_window import LaserScanningMainWindow


class LaserScanningGui(GuiBase):
    """ GUI module to be used in conjunction with qudi.logic.laser_scanning_logic.LaserScanningLogic

    Example config for copy-paste:

    laser_scanning_gui:
        module.Class: 'laserscanning.laser_scanning_gui.LaserScanningGui'
        connect:
            laser_scanning_logic: <laser_scanning_logic>
        options:
            max_display_points: 1000  # optional, Maximum number of simultaneously displayed data points
    """

    sigStartScan = QtCore.Signal(bool, bool)  # laser_only, data_only
    sigStopScan = QtCore.Signal()
    sigDoFit = QtCore.Signal(str, bool)  # fit_config_name, fit_envelope
    sigSaveData = QtCore.Signal(str)  # save_tag
    sigClearData = QtCore.Signal()
    sigAutoscaleHistogram = QtCore.Signal()
    sigHistogramSettingsChanged = QtCore.Signal(tuple, int)  # span, bins
    sigLaserTypeToggled = QtCore.Signal(bool)  # is_frequency
    sigLaserScanSettingsChanged = QtCore.Signal(object)  # ScannableLaserSettings
    sigStabilizeLaser = QtCore.Signal(object)  # target laser value

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
        logic = self._laser_scanning_logic()
        # Initialize main window
        self._mw = LaserScanningMainWindow(fit_config_model=logic.fit_config_model,
                                           fit_container=logic.fit_container,
                                           laser_constraints=logic.laser_constraints)
        # Connect signals
        self.__connect_actions()
        self.__connect_widgets()
        self.__connect_logic()
        # Update data content
        self._show_region_clicked()
        self._update_laser_type(logic.laser_is_frequency)
        self._update_status(*logic.scan_state)
        self._update_histogram_settings(*logic.histogram_settings)
        self._update_data(*logic.scan_data, *logic.histogram_data)
        self._update_laser_scan_settings(logic.laser_scan_settings)

        # Show GUI window
        self.show()
        self._mw.restore_default()

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
        self.sigLaserScanSettingsChanged.connect(logic.configure_laser_scan,
                                                 QtCore.Qt.QueuedConnection)
        self.sigStabilizeLaser.connect(logic.stabilize_laser, QtCore.Qt.QueuedConnection)
        # From logic
        logic.sigDataChanged.connect(self._update_data, QtCore.Qt.QueuedConnection)
        logic.sigStatusChanged.connect(self._update_status, QtCore.Qt.QueuedConnection)
        logic.sigFitChanged.connect(self._update_fit_data, QtCore.Qt.QueuedConnection)
        logic.sigHistogramSettingsChanged.connect(self._update_histogram_settings,
                                                  QtCore.Qt.QueuedConnection)
        logic.sigLaserTypeChanged.connect(self._update_laser_type, QtCore.Qt.QueuedConnection)
        logic.sigLaserScanSettingsChanged.connect(self._update_laser_scan_settings,
                                                  QtCore.Qt.QueuedConnection)
        logic.sigStabilizationTargetChanged.connect(self._update_stabilization_target,
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
        self.sigHistogramSettingsChanged.disconnect()
        self.sigLaserTypeToggled.disconnect()
        self.sigLaserScanSettingsChanged.disconnect()
        self.sigStabilizeLaser.disconnect()
        # From logic
        logic.sigDataChanged.disconnect(self._update_data)
        logic.sigStatusChanged.disconnect(self._update_status)
        logic.sigFitChanged.disconnect(self._update_fit_data)
        logic.sigHistogramSettingsChanged.disconnect(self._update_histogram_settings)
        logic.sigLaserTypeChanged.disconnect(self._update_laser_type)
        logic.sigLaserScanSettingsChanged.disconnect(self._update_laser_scan_settings)
        logic.sigStabilizationTargetChanged.disconnect(self._update_stabilization_target)

    def __connect_actions(self) -> None:
        # File actions
        self._mw.gui_actions.action_start_stop_scan.triggered.connect(self._start_stop_scan_clicked)
        self._mw.gui_actions.action_start_stop_record.triggered.connect(
            self._start_stop_record_clicked
        )
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
        self._mw.gui_actions.action_show_all_data.triggered.connect(
            self._show_all_data_clicked
        )

    def __disconnect_actions(self) -> None:
        # File actions
        self._mw.gui_actions.action_start_stop_scan.triggered.disconnect()
        self._mw.gui_actions.action_clear_data.triggered.disconnect()
        self._mw.gui_actions.action_save.triggered.disconnect()
        self._mw.gui_actions.action_start_stop_record.triggered.disconnect()
        # View actions
        self._mw.gui_actions.action_show_frequency.triggered.disconnect()
        self._mw.gui_actions.action_autoscale_histogram.triggered.disconnect()
        self._mw.gui_actions.action_show_histogram_region.triggered.disconnect()
        self._mw.gui_actions.action_show_all_data.triggered.disconnect()

    def __connect_widgets(self) -> None:
        self._mw.histogram_settings.sigSettingsChanged.connect(
            self._histogram_settings_edited
        )
        self._mw.fit_control.sigDoFit.connect(self._fit_clicked)
        if self._mw.laser_scan_settings is not None:
            self._mw.laser_scan_settings.sigSettingsChanged.connect(
                self._laser_scan_settings_edited
            )
        if self._mw.laser_stabilization is not None:
            self._mw.laser_stabilization.sigStabilizeLaser.connect(self._stabilize_clicked)

    def __disconnect_widgets(self) -> None:
        self._mw.histogram_settings.sigSettingsChanged.disconnect()
        self._mw.fit_control.sigDoFit.disconnect()
        if self._mw.laser_scan_settings is not None:
            self._mw.laser_scan_settings.sigSettingsChanged.disconnect()
        if self._mw.laser_stabilization is not None:
            self._mw.laser_stabilization.sigStabilizeLaser.disconnect()

    def _update_current_laser_value(self, value: float) -> None:
        """ """
        self._mw.current_laser_display.set_value(value)
        self._mw.histogram_plot.update_marker(value)

    @QtCore.Slot(bool)
    def _update_laser_type(self, is_frequency: bool) -> None:
        data_channel_units = self._laser_scanning_logic().data_channel_units
        if len(data_channel_units) > 0:
            channel, unit = next(iter(data_channel_units.items()))
        else:
            channel = self._mw.histogram_plot.labels[1]
            unit = self._mw.histogram_plot.units[1]
        self._mw.current_laser_display.toggle_is_frequency(is_frequency)
        self._mw.histogram_settings.toggle_unit(is_frequency)
        self._mw.gui_actions.action_show_frequency.setChecked(is_frequency)
        if is_frequency:
            self._mw.scatter_plot.set_labels('frequency', 'time')
            self._mw.scatter_plot.set_units('Hz', 's')
            self._mw.histogram_plot.set_labels('frequency', channel)
            self._mw.histogram_plot.set_units('Hz', unit)
        else:
            self._mw.scatter_plot.set_labels('wavelength', 'time')
            self._mw.scatter_plot.set_units('m', 's')
            self._mw.histogram_plot.set_labels('wavelength', channel)
            self._mw.histogram_plot.set_units('m', unit)

    @QtCore.Slot(tuple, int)
    def _update_histogram_settings(self, span: Tuple[float, float], bins: int) -> None:
        self._mw.histogram_settings.blockSignals(True)
        self._mw.histogram_settings.update_settings(span, bins)
        self._mw.histogram_settings.blockSignals(False)
        self._mw.histogram_plot.blockSignals(True)
        self._mw.histogram_plot.update_region(span)
        self._mw.histogram_plot.blockSignals(False)

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

    @QtCore.Slot(object, object, object, object, object, object)
    def _show_all_data(self,
                     timestamps: np.ndarray,
                     laser_data: np.ndarray,
                     scan_data: np.ndarray,
                     bins: np.ndarray,
                     histogram: np.ndarray,
                     envelope: np.ndarray) -> None:
        self._show_all_scan_data(timestamps=timestamps, laser_data=laser_data, data=scan_data)
        self._update_histogram_data(bins=bins, histogram=histogram, envelope=envelope)

    def _update_scan_data(self,
                          timestamps: np.ndarray,
                          laser_data: np.ndarray,
                          data: np.ndarray) -> None:
        """ """
        if laser_data.size == 0:
            self._update_current_laser_value(0)
            self._mw.histogram_plot.update_data(x=None, y=None)
            self._mw.scatter_plot.update_data(x=None, y=None)
        else:
            laser_data = laser_data[-self._max_display_points:]
            timestamps = timestamps[-self._max_display_points:]
            self._update_current_laser_value(laser_data[-1])
            self._mw.scatter_plot.update_data(x=laser_data, y=timestamps - timestamps[0])
            if data.size == 0:
                self._mw.histogram_plot.update_data(x=None, y=None)
            else:
                # FIXME: Support multiple data channels. Ignore all additional channels for now.
                if data.ndim > 1:
                    data = data[:, 0]
                self._mw.histogram_plot.update_data(x=laser_data,
                                                    y=data[-self._max_display_points:])
                
    # FIXME: Implement showing all data in the scatter plot
    def _show_all_scan_data(self,
                          timestamps: np.ndarray,
                          laser_data: np.ndarray,
                          data: np.ndarray) -> None:
        """ Should create a scatter plot which shows all data points """
        if laser_data.size == 0:
            self._update_current_laser_value(0)
            self._mw.histogram_plot.update_data(x=None, y=None)
            self._mw.scatter_plot.update_data(x=None, y=None)
        else:
            # laser_data = laser_data[-self._max_display_points:]
            # timestamps = timestamps[-self._max_display_points:]
            # self._update_current_laser_value(laser_data[-1])
            self._mw.scatter_plot.update_data(x=laser_data, y=timestamps - timestamps[0])
            if data.size == 0:
                self._mw.histogram_plot.update_data(x=None, y=None)
            else:
                # FIXME: Support multiple data channels. Ignore all additional channels for now.
                if data.ndim > 1:
                    data = data[:, 0]
                self._mw.histogram_plot.update_data(x=laser_data, y=data)
                
    def _update_histogram_data(self,
                               bins: np.ndarray,
                               histogram: np.ndarray,
                               envelope: np.ndarray) -> None:
        """ TODO: Check if is for this always all data or only displayed data? """
        if histogram.size == 0:
            self._mw.histogram_plot.update_histogram(x=None, y=None)
            self._mw.histogram_plot.update_envelope(x=None, y=None)
        else:
            # FIXME: Support multiple data channels. Ignore all additional channels for now.
            if histogram.ndim > 1:
                histogram = histogram[:, 0]
                envelope = envelope[:, 0]
            self._mw.histogram_plot.update_histogram(x=bins, y=histogram)
            self._mw.histogram_plot.update_envelope(x=bins, y=envelope)

    

    @QtCore.Slot(str, object, bool)
    def _update_fit_data(self,
                         fit_config: str,
                         fit_result: Union[None, _ModelResult],
                         fit_envelope: bool) -> None:
        """ Function that handles the fit results received from the logic via a signal """
        self._mw.fit_control.toggle_fit_envelope(fit_envelope)
        if (not fit_config) or (fit_config == 'No Fit') or (fit_result is None):
            self._mw.histogram_plot.update_fit(x=None, y=None)
        else:
            fit_data = fit_result.high_res_best_fit
            self._mw.histogram_plot.update_fit(x=fit_data[0], y=fit_data[1])

    @QtCore.Slot(bool, bool, bool)
    def _update_status(self, running: bool, laser_only: bool, data_only: bool) -> None:
        """ Function to ensure that the GUI displays the current measurement status """
        # Update checked states
        self._mw.gui_actions.action_start_stop_scan.setChecked(running and not data_only)
        self._mw.gui_actions.action_start_stop_record.setChecked(running and data_only)
        # Re-Enable actions and widgets
        self._mw.fit_control.setEnabled(True)
        self._mw.histogram_settings.setEnabled(True)
        self._mw.gui_actions.action_start_stop_scan.setEnabled(not running or not data_only)
        self._mw.gui_actions.action_start_stop_record.setEnabled(not running or data_only)
        self._mw.gui_actions.action_laser_only.setEnabled(not running)
        self._mw.gui_actions.action_clear_data.setEnabled(True)
        self._mw.gui_actions.action_show_frequency.setEnabled(True)
        self._mw.gui_actions.action_save.setEnabled(True)
        self._mw.gui_actions.action_autoscale_histogram.setEnabled(True)
        self._mw.gui_actions.action_show_histogram_region.setEnabled(True)
        self._mw.gui_actions.action_show_all_data.setEnabled(not running)
        if self._mw.laser_scan_settings is not None:
            self._mw.laser_scan_settings.setEnabled(not running or data_only)
            self._mw.laser_stabilization.setEnabled(not running or data_only)
        # Show/Hide laser marker and enable/disable histogram
        self._mw.histogram_plot.setEnabled(not laser_only or not running)
        if laser_only:
            self._mw.histogram_plot.hide_marker_selections()
        else:
            self._mw.histogram_plot.show_marker_selections()

    @QtCore.Slot(object)
    def _update_laser_scan_settings(self, settings: Union[None, ScannableLaserSettings]) -> None:
        widget = self._mw.laser_scan_settings
        if (settings is not None) and (widget is not None):
            widget.blockSignals(True)
            widget.update_settings(settings)
            widget.blockSignals(False)

    @QtCore.Slot(object)
    def _update_stabilization_target(self, value: float) -> None:
        widget = self._mw.laser_stabilization
        if widget is not None:
            widget.set_target(value)

    def _start_stop_scan_clicked(self):
        start = self._mw.gui_actions.action_start_stop_scan.isChecked()
        self.__start_stop(start, data_only=False)

    def _start_stop_record_clicked(self):
        start = self._mw.gui_actions.action_start_stop_record.isChecked()
        self.__start_stop(start, data_only=True)

    def _show_region_clicked(self) -> None:
        if self._mw.gui_actions.action_show_histogram_region.isChecked():
            self._mw.histogram_plot.show_region_selections()
        else:
            self._mw.histogram_plot.hide_region_selections()

    def _show_all_data_clicked(self) -> None:
        logic = self._laser_scanning_logic()
        self._show_all_data(*logic.scan_data, *logic.histogram_data)

    def _histogram_settings_edited(self, span: Tuple[float, float], bins: int) -> None:
        self.sigHistogramSettingsChanged.emit(span, bins)

    def _laser_scan_settings_edited(self) -> None:
        if self._mw.laser_scan_settings is not None:
            self.sigLaserScanSettingsChanged.emit(self._mw.laser_scan_settings.get_settings())

    def _clear_data_clicked(self):
        self.sigClearData.emit()

    def _fit_clicked(self, fit_config: str, fit_envelope: bool) -> None:
        self.sigDoFit.emit(fit_config, fit_envelope)

    def _save_clicked(self) -> None:
        self.sigSaveData.emit(self.save_tag)

    def _autoscale_histogram_clicked(self) -> None:
        self.sigAutoscaleHistogram.emit()

    def _toggle_laser_type_clicked(self) -> None:
        self.sigLaserTypeToggled.emit(self._mw.gui_actions.action_show_frequency.isChecked())

    def _stabilize_clicked(self, target: float) -> None:
        # Emit laser scan settings before starting
        self._laser_scan_settings_edited()
        self.sigStabilizeLaser.emit(target)

    def __start_stop(self, start: bool, data_only: bool) -> None:
        if (not data_only) and (self._mw.laser_scan_settings is not None):
            # Emit laser scan settings before starting
            if start:
                self._laser_scan_settings_edited()
            self._mw.laser_scan_settings.setEnabled(False)
            self._mw.laser_stabilization.setEnabled(False)
        self._mw.fit_control.setEnabled(False)
        self._mw.histogram_settings.setEnabled(False)
        self._mw.gui_actions.action_start_stop_scan.setEnabled(False)
        self._mw.gui_actions.action_start_stop_record.setEnabled(False)
        self._mw.gui_actions.action_clear_data.setEnabled(False)
        self._mw.gui_actions.action_show_frequency.setEnabled(False)
        self._mw.gui_actions.action_save.setEnabled(False)
        self._mw.gui_actions.action_autoscale_histogram.setEnabled(False)
        self._mw.gui_actions.action_show_all_data.setEnabled(False)
        self._mw.gui_actions.action_show_histogram_region.setEnabled(False)
        self._mw.gui_actions.action_laser_only.setEnabled(False)
        if start:
            self.sigStartScan.emit(self._mw.gui_actions.action_laser_only.isChecked(), data_only)
        else:
            self.sigStopScan.emit()
