# -*- coding: utf-8 -*-

"""
This file contains the qudi time series streaming gui.

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

__all__ = ['TimeSeriesGui']

import pyqtgraph as pg
import numpy as np
from PySide2 import QtCore, QtWidgets
from typing import Union, Dict, Tuple

from qudi.core.statusvariable import StatusVar
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.helpers import is_integer_type
from qudi.util.units import ScaledFloat
from qudi.core.module import GuiBase
from qudi.gui.time_series.main_window import TimeSeriesGuiMainWindow
from qudi.gui.time_series.settings_dialog import TraceViewDialog, ChannelSettingsDialog
from qudi.interface.data_instream_interface import SampleTiming


class TimeSeriesGui(GuiBase):
    """
    GUI module to be used in conjunction with TimeSeriesReaderLogic.

    Example config for copy-paste:

    time_series_gui:
        module.Class: 'time_series.time_series_gui.TimeSeriesGui'
        options:
            use_antialias: True  # optional, set to False if you encounter performance issues
        connect:
            _time_series_logic_con: <TimeSeriesReaderLogic_name>
    """

    # declare connectors
    _time_series_logic_con = Connector(interface='TimeSeriesReaderLogic')

    # declare ConfigOptions
    _use_antialias = ConfigOption('use_antialias', default=True, constructor=lambda x: bool(x))

    sigStartCounter = QtCore.Signal()
    sigStopCounter = QtCore.Signal()
    sigStartRecording = QtCore.Signal()
    sigStopRecording = QtCore.Signal()
    sigTraceSettingsChanged = QtCore.Signal(dict)
    sigChannelSettingsChanged = QtCore.Signal(list, list)

    _current_value_channel = StatusVar(name='current_value_channel', default='None')
    _visible_traces = StatusVar(name='visible_traces', default=dict())
    _current_value_channel_precision = StatusVar(name='current_value_channel_precision',
                                                 default=dict())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._streamer_constraints = None
        self._mw = None
        self._vb = None
        self.curves = dict()
        self.averaged_curves = dict()

        self._channels_per_axis = [set(), set()]

    def on_activate(self):
        """ Initialisation of the GUI """
        self._mw = TimeSeriesGuiMainWindow()
        # Get hardware constraints
        logic = self._time_series_logic_con()
        self._streamer_constraints = logic.streamer_constraints
        all_channels = list(self._streamer_constraints.channel_units)

        # Refine ConfigOptions
        self._visible_traces = {
            ch: self._visible_traces.get(ch, (True, True)) for ch in all_channels
        }
        self._current_value_channel_precision = {
            ch: self._current_value_channel_precision.get(ch, None) for ch in all_channels
        }

        # Configure PlotWidget
        if self._streamer_constraints.sample_timing == SampleTiming.RANDOM:
            self._mw.trace_plot_widget.setLabel('bottom', 'Sample')
        else:
            self._mw.trace_plot_widget.setLabel('bottom', 'Time', units='s')
        self._mw.trace_plot_widget.setMouseEnabled(x=False, y=False)
        self._mw.trace_plot_widget.setMouseTracking(False)
        self._mw.trace_plot_widget.setMenuEnabled(False)
        self._mw.trace_plot_widget.hideButtons()
        # Create second ViewBox to plot with two independent y-axes
        self._vb = pg.ViewBox()
        self._mw.trace_plot_widget.scene().addItem(self._vb)
        self._mw.trace_plot_widget.getAxis('right').linkToView(self._vb)
        self._vb.setXLink(self._mw.trace_plot_widget)
        self._vb.setMouseEnabled(x=False, y=False)
        self._vb.setMenuEnabled(False)
        # Sync resize events
        self._mw.trace_plot_widget.plotItem.vb.sigResized.connect(self.__update_viewbox_sync)

        self._mw.trace_plot_widget.disableAutoRange(axis='x')
        # self._mw.trace_plot_widget.setAutoVisible(x=True)

        self.curves = dict()
        self.averaged_curves = dict()
        for i, ch in enumerate(all_channels):
            # Determine pen style
            # FIXME: Choosing a pen width != 1px (not cosmetic) causes massive performance drops
            # For mixed signals each signal type (digital or analog) has the same color
            # If just a single signal type is present, alternate the colors accordingly
            if i % 3 == 0:
                pen1 = pg.mkPen(palette.c2, cosmetic=True)
                pen2 = pg.mkPen(palette.c1, cosmetic=True)
            elif i % 3 == 1:
                pen1 = pg.mkPen(palette.c3, cosmetic=True)
                pen2 = pg.mkPen(palette.c4, cosmetic=True)
            else:
                pen1 = pg.mkPen(palette.c5, cosmetic=True)
                pen2 = pg.mkPen(palette.c6, cosmetic=True)
            self.averaged_curves[ch] = pg.PlotCurveItem(pen=pen1,
                                                        clipToView=True,
                                                        downsampleMethod='subsample',
                                                        autoDownsample=True,
                                                        antialias=self._use_antialias)
            self.curves[ch] = pg.PlotCurveItem(pen=pen2,
                                               clipToView=True,
                                               downsampleMethod='subsample',
                                               autoDownsample=True,
                                               antialias=self._use_antialias)

        # Connecting user interactions
        self._mw.toggle_trace_action.triggered[bool].connect(self._trace_toggled)
        self._mw.record_trace_action.triggered[bool].connect(self._record_toggled)
        self._mw.snapshot_trace_action.triggered.connect(logic.save_trace_snapshot,
                                                         QtCore.Qt.QueuedConnection)
        self._mw.freeze_y_axis_action.triggered[bool].connect(self._toggle_y_axis_frozen)
        self._mw.settings_dockwidget.trace_length_spinbox.editingFinished.connect(
            self._trace_settings_changed
        )
        self._mw.settings_dockwidget.data_rate_spinbox.editingFinished.connect(
            self._trace_settings_changed
        )
        self._mw.settings_dockwidget.oversampling_spinbox.editingFinished.connect(
            self._trace_settings_changed
        )
        self._mw.settings_dockwidget.moving_average_spinbox.editingFinished.connect(
            self._trace_settings_changed
        )
        self._mw.current_value_combobox.currentIndexChanged.connect(
            self._current_value_channel_changed
        )

        # Connect the default view and settings actions
        self._mw.restore_default_view_action.triggered.connect(self._restore_default_view)
        self._mw.trace_view_selection_action.triggered.connect(self._exec_trace_view_dialog)
        self._mw.channel_settings_action.triggered.connect(self._exec_channel_settings_dialog)

        # Connect signals to/from logic
        self.sigStartCounter.connect(logic.start_reading, QtCore.Qt.QueuedConnection)
        self.sigStopCounter.connect(logic.stop_reading, QtCore.Qt.QueuedConnection)
        self.sigStartRecording.connect(logic.start_recording, QtCore.Qt.QueuedConnection)
        self.sigStopRecording.connect(logic.stop_recording, QtCore.Qt.QueuedConnection)
        self.sigTraceSettingsChanged.connect(logic.set_trace_settings, QtCore.Qt.QueuedConnection)
        self.sigChannelSettingsChanged.connect(logic.set_channel_settings,
                                               QtCore.Qt.QueuedConnection)

        logic.sigDataChanged.connect(self.update_data, QtCore.Qt.QueuedConnection)
        logic.sigTraceSettingsChanged.connect(self.update_trace_settings,
                                              QtCore.Qt.QueuedConnection)
        logic.sigChannelSettingsChanged.connect(self.update_channel_settings,
                                                QtCore.Qt.QueuedConnection)
        logic.sigStatusChanged.connect(self.update_status, QtCore.Qt.QueuedConnection)

        self.update_status(running=logic.module_state() == 'locked',
                           recording=logic.data_recording_active)
        self.update_channel_settings(logic.active_channel_names, logic.averaged_channel_names)
        self.update_trace_settings(logic.trace_settings)
        self.update_data(*logic.trace_data, *logic.averaged_trace_data)
        self._apply_trace_view_settings(self.trace_view_settings)
        index = self._mw.current_value_combobox.findText(self._current_value_channel)
        if index < 0:
            self._mw.current_value_combobox.setCurrentIndex(0)
        else:
            self._mw.current_value_combobox.setCurrentIndex(index)
        self.show()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        self._restore_window_geometry(self._mw)
        self._mw.show()
        self._mw.raise_()
        self._mw.activateWindow()

    def on_deactivate(self):
        """ Deactivate the module
        """
        logic = self._time_series_logic_con()

        # disconnect signals
        self._mw.trace_plot_widget.plotItem.vb.sigResized.disconnect()
        self._mw.toggle_trace_action.triggered.disconnect()
        self._mw.record_trace_action.triggered.disconnect()
        self._mw.snapshot_trace_action.triggered.disconnect()
        self._mw.settings_dockwidget.trace_length_spinbox.editingFinished.disconnect()
        self._mw.settings_dockwidget.data_rate_spinbox.editingFinished.disconnect()
        self._mw.settings_dockwidget.oversampling_spinbox.editingFinished.disconnect()
        self._mw.settings_dockwidget.moving_average_spinbox.editingFinished.disconnect()
        self._mw.restore_default_view_action.triggered.disconnect()
        self.sigStartCounter.disconnect()
        self.sigStopCounter.disconnect()
        self.sigStartRecording.disconnect()
        self.sigStopRecording.disconnect()
        self.sigTraceSettingsChanged.disconnect()
        self.sigChannelSettingsChanged.disconnect()
        logic.sigDataChanged.disconnect(self.update_data)
        logic.sigTraceSettingsChanged.disconnect(self.update_trace_settings)
        logic.sigChannelSettingsChanged.disconnect(self.update_channel_settings)
        logic.sigStatusChanged.disconnect(self.update_status)
        self._save_window_geometry(self._mw)
        self._mw.close()

    @property
    def trace_view_settings(self) -> Dict[str, Tuple[bool, bool, Union[int, None]]]:
        """ Read-only """
        return {
            ch: [*flags, self._current_value_channel_precision[ch]] for ch, flags in
            self._visible_traces.items()
        }

    def _exec_trace_view_dialog(self):
        current_settings = self.trace_view_settings
        dialog = TraceViewDialog(current_settings.keys(), parent=self._mw)
        dialog.set_channel_states(current_settings)
        # Show modal dialog and update logic if necessary
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._apply_trace_view_settings(dialog.get_channel_states())

    def _exec_channel_settings_dialog(self):
        logic = self._time_series_logic_con()
        active_channels, averaged_channels = logic.channel_settings
        channels = list(self._streamer_constraints.channel_units)
        channel_states = {ch: (ch in active_channels, ch in averaged_channels) for ch in channels}
        dialog = ChannelSettingsDialog(channels, parent=self._mw)
        dialog.set_channel_states(channel_states)
        # Show modal dialog and update logic if necessary
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._apply_channel_settings(dialog.get_channel_states())

    @QtCore.Slot()
    def __update_viewbox_sync(self):
        """ Helper method to sync plots for both y-axes """
        try:
            self._vb.setGeometry(self._mw.trace_plot_widget.plotItem.vb.sceneBoundingRect())
            self._vb.linkedViewChanged(self._mw.trace_plot_widget.plotItem.vb, self._vb.XAxis)
        except:
            self.log.exception('sdsdasd')
            raise

    def _apply_trace_view_settings(self, setting):
        active_channels, averaged_channels = self._time_series_logic_con().channel_settings
        for chnl, (show_data, show_average, precision) in setting.items():
            chnl_active = chnl in active_channels
            data_visible = show_data and chnl_active
            average_visible = show_average and chnl_active and (chnl in averaged_channels)
            self._toggle_channel_data_plot(chnl, data_visible, average_visible)
            self._visible_traces[chnl] = (show_data, show_average)
            self._current_value_channel_precision[chnl] = precision

    def _apply_channel_settings(self, setting):
        self.sigChannelSettingsChanged.emit(
            [ch for ch, (enabled, _) in setting.items() if enabled],
            [ch for ch, (_, averaged) in setting.items() if averaged]
        )

    @QtCore.Slot(list, list)
    def update_channel_settings(self, enabled, averaged):
        # Update combobox
        self._mw.current_value_combobox.blockSignals(True)
        try:
            self._mw.current_value_combobox.clear()
            self._mw.current_value_combobox.addItem('None')
            self._mw.current_value_combobox.addItems(
                [f'average {ch}' for ch in averaged if ch in enabled]
            )
            self._mw.current_value_combobox.addItems(enabled)
            index = self._mw.current_value_combobox.findText(self._current_value_channel)
            if index < 0:
                self._mw.current_value_combobox.setCurrentIndex(0)
            else:
                self._mw.current_value_combobox.setCurrentIndex(index)
        finally:
            self._mw.current_value_combobox.blockSignals(False)
        self._current_value_channel = self._mw.current_value_combobox.currentText()

        # Update plot widget axes
        self._streamer_constraints = self._time_series_logic_con().streamer_constraints
        channel_units = self._streamer_constraints.channel_units
        different_units = list({unit for ch, unit in channel_units.items() if ch in enabled})
        self._channels_per_axis = list()
        if len(different_units) == 2:
            self._mw.trace_plot_widget.showAxis('right')
            self._channels_per_axis = [
                tuple(ch for ch in enabled if channel_units[ch] == different_units[0]),
                tuple(ch for ch in enabled if channel_units[ch] == different_units[1])
            ]
            if len(enabled) == 2:
                self._mw.trace_plot_widget.setLabel('left',
                                                    self._channels_per_axis[0][0],
                                                    units=different_units[0])
                self._mw.trace_plot_widget.setLabel('right',
                                                    self._channels_per_axis[1][0],
                                                    units=different_units[1])
            else:
                self._mw.trace_plot_widget.setLabel('left', 'Signal', units=different_units[0])
                self._mw.trace_plot_widget.setLabel('right', 'Signal', units=different_units[1])
        elif len(different_units) > 2:
            self._mw.trace_plot_widget.hideAxis('right')
            self._channels_per_axis = [tuple(enabled), tuple()]
            self._mw.trace_plot_widget.setLabel('left', 'Signal', units='')
        elif len(enabled) == 2:
            self._mw.trace_plot_widget.hideAxis('right')
            self._channels_per_axis = [(enabled[0],), (enabled[1],)]
            self._mw.trace_plot_widget.setLabel('left',
                                                enabled[0],
                                                units=channel_units[enabled[0]])
            self._mw.trace_plot_widget.setLabel('right',
                                                enabled[1],
                                                units=channel_units[enabled[1]])
        elif len(enabled) > 2:
            self._mw.trace_plot_widget.hideAxis('right')
            self._channels_per_axis = [tuple(enabled), tuple()]
            self._mw.trace_plot_widget.setLabel('left', 'Signal', units=different_units[0])
        else:
            self._mw.trace_plot_widget.hideAxis('right')
            self._channels_per_axis = [tuple(enabled), tuple()]
            self._mw.trace_plot_widget.setLabel('left', enabled[0], units=different_units[0])

        for ch in channel_units:
            show_channel = (ch in enabled) and self._visible_traces[ch][0]
            show_average = show_channel and (ch in averaged) and self._visible_traces[ch][1]
            self._toggle_channel_data_plot(ch, show_channel, show_average)

    @QtCore.Slot(object, object, object, object)
    def update_data(self, data_time, data, smooth_time, smooth_data):
        """ The function that grabs the data and sends it to the plot """
        shift_time = data_time[0] != 0
        if data is not None:
            if shift_time:
                data_time = data_time - data_time[0]
            for channel, y_arr in data.items():
                self.curves[channel].setData(y=y_arr, x=data_time)
        if smooth_data is not None:
            if shift_time:
                smooth_time = smooth_time + (
                    data_time[data_time.shape[0] - smooth_time.shape[0]] - smooth_time[0]
                )
            for channel, y_arr in smooth_data.items():
                self.averaged_curves[channel].setData(y=y_arr, x=smooth_time)

        channel = self._mw.current_value_combobox.currentText()
        if channel and channel != 'None':
            try:
                if channel.startswith('average '):
                    channel = channel.split('average ', 1)[-1]
                    val = smooth_data[channel][-1]
                else:
                    val = data[channel][-1]
                constraints = self._time_series_logic_con().streamer_constraints
                ch_unit = constraints.channel_units[channel]
                precision = self._current_value_channel_precision[channel]
                if np.isnan(val):
                    self._mw.current_value_label.setText(f'{val} {ch_unit}')
                elif is_integer_type(constraints.data_type):
                    self._mw.current_value_label.setText(f'{val:,d} {ch_unit}')
                elif precision is None:
                    self._mw.current_value_label.setText(f'{ScaledFloat(val):.5r}{ch_unit}')
                else:
                    self._mw.current_value_label.setText(f'{val:,.{precision:d}f} {ch_unit}')
            except (TypeError, IndexError, KeyError):
                pass

    @QtCore.Slot(bool)
    def _trace_toggled(self, enabled: bool) -> None:
        """ Handling the toggle button to stop and start the stream """
        self._mw.toggle_trace_action.setEnabled(False)
        self._mw.record_trace_action.setEnabled(False)
        self._mw.settings_dockwidget.setEnabled(False)
        self._mw.channel_settings_action.setEnabled(False)
        if enabled:
            self._trace_settings_changed()
            self.sigStartCounter.emit()
        else:
            self.sigStopCounter.emit()

    @QtCore.Slot(bool)
    def _record_toggled(self, enabled: bool) -> None:
        """ Handling the save button to save the data into a file """
        self._mw.toggle_trace_action.setEnabled(False)
        self._mw.record_trace_action.setEnabled(False)
        if enabled:
            self.sigStartRecording.emit()
        else:
            self.sigStopRecording.emit()

    @QtCore.Slot(bool, bool)
    def update_status(self, running: bool, recording: bool) -> None:
        """ Function to ensure that the GUI represents the current measurement status """
        # Update toolbutton states
        self._mw.toggle_trace_action.setChecked(running)
        self._mw.toggle_trace_action.setText('Stop trace' if running else 'Start trace')
        self._mw.record_trace_action.setChecked(recording)
        self._mw.record_trace_action.setText('Save recorded' if recording else 'Start recording')
        # Enable/Disable widgets and actions
        self._mw.settings_dockwidget.setEnabled(True)
        self._mw.channel_settings_action.setEnabled(not running)
        self._mw.toggle_trace_action.setEnabled(True)
        self._mw.record_trace_action.setEnabled(running)

    @QtCore.Slot()
    def _trace_settings_changed(self):
        """ Handling the change of the count_length and sending it to the measurement.
        """
        settings = {
            'trace_window_size': self._mw.settings_dockwidget.trace_length_spinbox.value(),
            'data_rate': self._mw.settings_dockwidget.data_rate_spinbox.value(),
            'oversampling_factor': self._mw.settings_dockwidget.oversampling_spinbox.value(),
            'moving_average_width': self._mw.settings_dockwidget.moving_average_spinbox.value()
        }
        self.sigTraceSettingsChanged.emit(settings)

    @QtCore.Slot()
    def _current_value_channel_changed(self):
        val = self._mw.current_value_combobox.currentText()
        if val == 'None':
            self._mw.current_value_label.setVisible(False)
            self._mw.current_value_label.setText('0')
        else:
            self._mw.current_value_label.setVisible(True)
        self._current_value_channel = val

    @QtCore.Slot()
    def _restore_default_view(self):
        """ Restore the arrangement of DockWidgets to the default
        """
        # Show hidden dock widget and re-dock
        self._mw.settings_dockwidget.show()
        self._mw.settings_dockwidget.setFloating(False)
        self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._mw.settings_dockwidget)
        # Set the toolbar to its initial top area
        self._mw.toolbar.show()
        self._mw.addToolBar(QtCore.Qt.TopToolBarArea, self._mw.toolbar)
        # Restore status if something went wrong
        self.update_status(running=self._time_series_logic_con().module_state() == 'locked',
                           recording=self._time_series_logic_con().data_recording_active)

    @QtCore.Slot(dict)
    def update_trace_settings(self, settings_dict):
        if settings_dict['oversampling_factor'] != self._mw.settings_dockwidget.oversampling_spinbox.value():
            self._mw.settings_dockwidget.oversampling_spinbox.blockSignals(True)
            self._mw.settings_dockwidget.oversampling_spinbox.setValue(
                settings_dict['oversampling_factor']
            )
            self._mw.settings_dockwidget.oversampling_spinbox.blockSignals(False)
        if settings_dict['trace_window_size'] != self._mw.settings_dockwidget.trace_length_spinbox.value():
            self._mw.settings_dockwidget.trace_length_spinbox.blockSignals(True)
            self._mw.settings_dockwidget.trace_length_spinbox.setValue(
                settings_dict['trace_window_size']
            )
            self._mw.settings_dockwidget.trace_length_spinbox.blockSignals(False)
        if settings_dict['data_rate'] != self._mw.settings_dockwidget.data_rate_spinbox.value():
            self._mw.settings_dockwidget.data_rate_spinbox.blockSignals(True)
            self._mw.settings_dockwidget.data_rate_spinbox.setValue(settings_dict['data_rate'])
            self._mw.settings_dockwidget.data_rate_spinbox.blockSignals(False)
        if settings_dict['moving_average_width'] != self._mw.settings_dockwidget.moving_average_spinbox.value():
            self._mw.settings_dockwidget.moving_average_spinbox.blockSignals(True)
            self._mw.settings_dockwidget.moving_average_spinbox.setValue(
                settings_dict['moving_average_width']
            )
            self._mw.settings_dockwidget.moving_average_spinbox.blockSignals(False)
        self._streamer_constraints = self._time_series_logic_con().streamer_constraints
        if self._streamer_constraints.sample_timing == SampleTiming.RANDOM:
            self._mw.trace_plot_widget.setRange(
                xRange=[0, settings_dict['trace_window_size'] * settings_dict['data_rate']],
                disableAutoRange=self._mw.freeze_y_axis_action.isChecked()
            )
        else:
            self._mw.trace_plot_widget.setRange(
                xRange=[0, settings_dict['trace_window_size']],
                disableAutoRange=self._mw.freeze_y_axis_action.isChecked()
            )

    def _remove_channel_from_plot(self, channel: str) -> None:
        data_curve = self.curves[channel]
        average_curve = self.averaged_curves[channel]
        if data_curve in self._vb.addedItems:
            self._vb.removeItem(data_curve)
        if data_curve in self._mw.trace_plot_widget.items():
            self._mw.trace_plot_widget.removeItem(data_curve)
        if average_curve in self._vb.addedItems:
            self._vb.removeItem(average_curve)
        if average_curve in self._mw.trace_plot_widget.items():
            self._mw.trace_plot_widget.removeItem(average_curve)

    def _toggle_channel_data_plot(self, channel, show_data: bool, show_average: bool):
        self._remove_channel_from_plot(channel)
        if show_data:
            if channel in self._channels_per_axis[0]:
                self._mw.trace_plot_widget.addItem(self.curves[channel])
            else:
                self._vb.addItem(self.curves[channel])
        if show_average:
            if channel in self._channels_per_axis[0]:
                self._mw.trace_plot_widget.addItem(self.averaged_curves[channel])
            else:
                self._vb.addItem(self.averaged_curves[channel])

    def _toggle_y_axis_frozen(self, st):
        if st:
            self._mw.trace_plot_widget.disableAutoRange(axis='y')
            self._mw.freeze_y_axis_action.setText("Un-freeze Y axis")
        else:
            self._mw.trace_plot_widget.enableAutoRange(axis='y')
            self._mw.freeze_y_axis_action.setText("Freeze Y axis")
