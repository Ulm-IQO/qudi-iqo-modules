# -*- coding: utf-8 -*-

"""
This file contains the qudi GUI for general Confocal control.

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
from typing import Union, Tuple

import numpy as np
from PySide2 import QtCore, QtWidgets

import qudi.util.uic as uic
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.core.module import GuiBase
from qudi.core.statusvariable import StatusVar
from qudi.gui.magnet.axes_control_dockwidget import AxesControlDockWidget
from qudi.gui.scanning.scan_dockwidget import ScanDockWidget
from qudi.interface.scanning_probe_interface import ScannerChannel


class ConfocalMainWindow(QtWidgets.QMainWindow):
    """ Create the Mainwindow based on the corresponding *.ui file. """

    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_magnetgui.ui')

        # Load it
        super().__init__()
        uic.loadUi(ui_file, self)
        return

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.action_utility_zoom.setChecked(not self.action_utility_zoom.isChecked())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
        return


class SaveDialog(QtWidgets.QDialog):
    """ Dialog to provide feedback and block GUI while saving """

    def __init__(self, parent, title="Please wait", text="Saving..."):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        # Dialog layout
        self.text = QtWidgets.QLabel("<font size='16'>" + text + "</font>")
        self.hbox = QtWidgets.QHBoxLayout()
        self.hbox.addSpacerItem(QtWidgets.QSpacerItem(50, 0))
        self.hbox.addWidget(self.text)
        self.hbox.addSpacerItem(QtWidgets.QSpacerItem(50, 0))
        self.setLayout(self.hbox)


class MagnetGui(GuiBase):
    """ Main magnet GUI Class for XY and depth scans. Derived from scanning probe gui.

    Example config for copy-paste:

    magnet_gui:
        module.Class: 'magnet.magnetgui.MagnetGui'
        options:
            image_axes_padding: 0.02
            default_position_unit_prefix: null  # optional, use unit prefix characters, e.g. 'u' or 'n'
        connect:
                scanning_logic: magnet_logic
                data_logic: magnet_data_logic
    """

    # declare connectors
    _scanning_logic = Connector(name='scanning_logic', interface='MagnetLogic')
    _data_logic = Connector(name='data_logic', interface='MagnetDataLogic')

    # config options for gui
    _default_position_unit_prefix = ConfigOption(name='default_position_unit_prefix', default=None)

    # status vars
    _window_state = StatusVar(name='window_state', default=None)
    _window_geometry = StatusVar(name='window_geometry', default=None)

    # signals
    sigScannerTargetChanged = QtCore.Signal(dict, object)
    sigScanSettingsChanged = QtCore.Signal(dict)
    sigToggleScan = QtCore.Signal(bool, tuple, object)

    sigSaveScan = QtCore.Signal(object, object)
    sigSaveFinished = QtCore.Signal()
    sigShowSaveDialog = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # QMainWindow and QDialog child instances
        self._mw = None

        # References to automatically generated GUI elements
        self.axes_control_widgets = None
        self.scanner_settings_axes_widgets = None
        self.scan_2d_dockwidgets = None
        self.scan_1d_dockwidgets = None

        # References to static dockwidgets
        self.scanner_control_dockwidget = None

        # misc
        self._scanner_settings_locked = False
        self._n_save_tasks = 0
        return

    def on_activate(self):
        """ Initializes all needed UI files and establishes the connectors.

        This method executes the all the inits for the differnt GUIs and passes
        the event argument from fysom to the methods.
        """
        self.scan_2d_dockwidgets = dict()
        self.scan_1d_dockwidgets = dict()

        # Initialize main window
        self._mw = ConfocalMainWindow()
        self._mw.setDockNestingEnabled(True)

        # Initialize fixed dockwidgets
        self._init_static_dockwidgets()

        # Initialize dialog windows
        self._save_dialog = SaveDialog(self._mw)

        # Automatically generate scanning widgets for desired scans
        scans = list()
        axes = tuple(self._scanning_logic().magnet_control_axes)
        for i, first_ax in enumerate(axes, 1):
            # if not scans:
            scans.append((first_ax,))
            for second_ax in axes[i:]:
                scans.append((first_ax, second_ax))
        for scan in scans:
            self._add_scan_dockwidget(scan)

        # Initialize widget data
        self.scanner_settings_updated()
        self.scanner_target_updated()
        self.scan_state_updated(self._scanning_logic().module_state() != 'idle')

        # Connect signals
        self.sigScannerTargetChanged.connect(
            self._scanning_logic().set_target, QtCore.Qt.QueuedConnection
        )
        self.sigScanSettingsChanged.connect(
            self._scanning_logic().set_scan_settings, QtCore.Qt.QueuedConnection
        )
        self.sigToggleScan.connect(self._scanning_logic().toggle_scan, QtCore.Qt.QueuedConnection)

        self._mw.action_restore_default_view.triggered.connect(self.restore_default_view)
        self._mw.action_save_all_scans.triggered.connect(lambda x: self.save_scan_data(scan_axes=None))
        self.sigSaveScan.connect(self._data_logic().save_scan_by_axis, QtCore.Qt.QueuedConnection)
        self.sigSaveFinished.connect(self._save_dialog.hide, QtCore.Qt.QueuedConnection)
        self._data_logic().sigSaveStateChanged.connect(self._track_save_status)

        self._mw.action_utility_zoom.toggled.connect(self.toggle_cursor_zoom)
        self._mw.action_utility_full_range.triggered.connect(
            self._scanning_logic().set_full_scan_ranges, QtCore.Qt.QueuedConnection
        )

        self._mw.action_history_forward.triggered.connect(
            self._data_logic().history_next, QtCore.Qt.QueuedConnection
        )
        self._mw.action_history_back.triggered.connect(
            self._data_logic().history_previous, QtCore.Qt.QueuedConnection
        )

        self._scanning_logic().sigScannerTargetChanged.connect(
            self.scanner_target_updated, QtCore.Qt.QueuedConnection
        )
        self._scanning_logic().sigScanSettingsChanged.connect(
            self.scanner_settings_updated, QtCore.Qt.QueuedConnection
        )
        self._scanning_logic().sigScanStateChanged.connect(
            self.scan_state_updated, QtCore.Qt.QueuedConnection
        )

        self._data_logic().sigHistoryScanDataRestored.connect(
            self._update_from_history, QtCore.Qt.QueuedConnection
        )

        self.sigShowSaveDialog.connect(lambda x: self._save_dialog.show() if x else self._save_dialog.hide(),
                                       QtCore.Qt.DirectConnection)

        # Initialize dockwidgets to default view
        self.restore_default_view()
        self.show()

        self.restore_history()

        self._restore_window_geometry(self._mw)
        self.update_crosshair_sizes()
        return

    def on_deactivate(self):
        """ Reverse steps of activation

        @return int: error code (0:OK, -1:error)
        """
        # Remember window position and geometry and close window
        self._save_window_geometry(self._mw)
        self._mw.close()

        # Disconnect signals
        self.sigScannerTargetChanged.disconnect()
        self.sigScanSettingsChanged.disconnect()
        self.sigToggleScan.disconnect()
        self._mw.action_restore_default_view.triggered.disconnect()
        self._mw.action_history_forward.triggered.disconnect()
        self._mw.action_history_back.triggered.disconnect()
        self._mw.action_utility_full_range.triggered.disconnect()
        self._mw.action_utility_zoom.toggled.disconnect()
        self._scanning_logic().sigScannerTargetChanged.disconnect(self.scanner_target_updated)
        self._scanning_logic().sigScanSettingsChanged.disconnect(self.scanner_settings_updated)
        self._scanning_logic().sigScanStateChanged.disconnect(self.scan_state_updated)
        self._data_logic().sigHistoryScanDataRestored.disconnect(self._update_from_history)
        self.scanner_control_dockwidget.sigTargetChanged.disconnect()
        self.scanner_control_dockwidget.sigSliderMoved.disconnect()
        self.scanner_control_dockwidget.widget().move_pushButton.clicked.disconnect()

        for scan in tuple(self.scan_1d_dockwidgets):
            self._remove_scan_dockwidget(scan)
        for scan in tuple(self.scan_2d_dockwidgets):
            self._remove_scan_dockwidget(scan)

    def show(self):
        """Make main window visible and put it above all other windows. """
        # Show the Main Confocal GUI:
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def _init_static_dockwidgets(self):
        self.scanner_control_dockwidget = AxesControlDockWidget(
            tuple(self._scanning_logic().magnet_control_axes.values())
        )
        if self._default_position_unit_prefix is not None:
            self.scanner_control_dockwidget.set_assumed_unit_prefix(
                self._default_position_unit_prefix
            )
        self.scanner_control_dockwidget.setAllowedAreas(QtCore.Qt.TopDockWidgetArea)
        self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.scanner_control_dockwidget)
        self.scanner_control_dockwidget.visibilityChanged.connect(
            self._mw.action_view_scanner_control.setChecked)
        self._mw.action_view_scanner_control.triggered[bool].connect(
            self.scanner_control_dockwidget.setVisible)
        self._mw.action_view_line_scan.triggered[bool].connect(
            lambda is_vis: [wid.setVisible(is_vis) for wid in self.scan_1d_dockwidgets.values()]
        )
        self.scanner_control_dockwidget.sigResolutionChanged.connect(
            lambda ax, res: self.sigScanSettingsChanged.emit({'resolution': {ax: res}})
            if not self._scanner_settings_locked else None
        )
        self.scanner_control_dockwidget.sigRangeChanged.connect(
            lambda ax, ranges: self.sigScanSettingsChanged.emit({'range': {ax: ranges}})
            if not self._scanner_settings_locked else None
        )
        # TODO: When "current target" value box is clicked in, a move is excecuted. Why and how?
        self.scanner_control_dockwidget.sigTargetChanged.connect(
            lambda ax, pos: self.set_scanner_target_position({ax: pos})
        )
        # ToDo: Implement a way to avoid too fast position update from slider movement.
        # todo: why is _update_scan_crosshairds issuing (not only displaying) at all?
        self.scanner_control_dockwidget.sigSliderMoved.connect(
            # lambda ax, pos: self._update_scan_markers(pos_dict={ax: pos}, exclude_scan=None)
            lambda ax, pos: self.set_scanner_target_position({ax: pos})
        )

        self.scanner_control_dockwidget.widget().move_pushButton.clicked.connect(
            self._scanning_logic().set_control
        )

        self._mw.util_toolBar.visibilityChanged.connect(
            self._mw.action_view_toolbar.setChecked)
        self._mw.action_view_toolbar.triggered[bool].connect(self._mw.util_toolBar.setVisible)

    @QtCore.Slot()
    def restore_default_view(self):
        """ Restore the arrangement of DockWidgets to default """
        self._mw.setDockNestingEnabled(True)

        # Remove all dockwidgets from main window layout
        self._mw.removeDockWidget(self.scanner_control_dockwidget)
        for dockwidget in self.scan_2d_dockwidgets.values():
            self._mw.removeDockWidget(dockwidget)
        for dockwidget in self.scan_1d_dockwidgets.values():
            self._mw.removeDockWidget(dockwidget)

        # Return toolbar to default position
        self._mw.util_toolBar.show()
        self._mw.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self._mw.util_toolBar)

        # Add axes control dock widget to layout
        self.scanner_control_dockwidget.setFloating(False)
        self.scanner_control_dockwidget.show()
        self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.scanner_control_dockwidget)
        # Add dynamically created dock widgets to layout
        dockwidgets_2d = tuple(self.scan_2d_dockwidgets.values())
        dockwidgets_1d = tuple(self.scan_1d_dockwidgets.values())
        multiple_2d_scans = len(dockwidgets_2d) > 1
        multiple_1d_scans = len(dockwidgets_1d) > 1
        has_1d_scans = bool(dockwidgets_1d)
        has_2d_scans = bool(dockwidgets_2d)
        if has_2d_scans:
            for i, dockwidget in enumerate(dockwidgets_2d):
                dockwidget.show()
                self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
                dockwidget.setFloating(False)
        if has_1d_scans:
            for i, dockwidget in enumerate(dockwidgets_1d):
                dockwidget.show()
                self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
                dockwidget.setFloating(False)

        if has_1d_scans and has_2d_scans:

            self._mw.resizeDocks((dockwidgets_2d[0], dockwidgets_1d[0]),
                                 (1, 1),
                                 QtCore.Qt.Horizontal)
        elif multiple_2d_scans:
            self._mw.resizeDocks((dockwidgets_2d[0], dockwidgets_2d[1]),
                                 (1, 1),
                                 QtCore.Qt.Horizontal)

        # tabify dockwidgets if needed, needs to be done after .splitDockWidget()
        if multiple_2d_scans:
            if has_1d_scans:
                for ii, dockwidget in enumerate(dockwidgets_2d[1:]):
                    self._mw.tabifyDockWidget(dockwidgets_2d[ii], dockwidget)
                dockwidgets_2d[0].raise_()
            else:
                for ii, dockwidget in enumerate(dockwidgets_2d[2:]):
                    if ii == 0:
                        self._mw.tabifyDockWidget(dockwidgets_2d[ii], dockwidget)
                    else:
                        self._mw.tabifyDockWidget(dockwidgets_2d[ii + 1], dockwidget)
                dockwidgets_2d[0].raise_()
        if multiple_1d_scans:
            for ii, dockwidget in enumerate(dockwidgets_1d[1:]):
                self._mw.tabifyDockWidget(dockwidgets_1d[ii], dockwidget)
            dockwidgets_1d[0].raise_()

        return

    @QtCore.Slot(tuple)
    def save_scan_data(self, scan_axes=None):
        """
        Save data for a given (or all) scan axis.
        @param tuple: Axis to save. Save all currently displayed if None.
        """
        self.sigShowSaveDialog.emit(True)
        try:
            data_logic = self._data_logic()
            if scan_axes is None:
                scan_axes = [scan.scan_axes for scan in data_logic.get_all_current_scan_data()]
            else:
                scan_axes = [scan_axes]
            for ax in scan_axes:
                try:
                    cbar_range = self.scan_2d_dockwidgets[ax].scan_widget.image_widget.levels
                except KeyError:
                    cbar_range = None
                self.sigSaveScan.emit(ax, cbar_range)
        finally:
            pass

    def _track_save_status(self, in_progress):
        if in_progress:
            self._n_save_tasks += 1
        else:
            self._n_save_tasks -= 1

        if self._n_save_tasks == 0:
            self.sigSaveFinished.emit()

    def _remove_scan_dockwidget(self, axes):
        try:
            dockwidget = self.scan_1d_dockwidgets.pop(axes)
        except KeyError:
            dockwidget = self.scan_2d_dockwidgets.pop(axes)
        dockwidget.scan_widget.sigMarkerPositionChanged.disconnect()
        dockwidget.scan_widget.toggle_scan_button.clicked.disconnect()
        dockwidget.scan_widget.save_scan_button.clicked.disconnect()
        dockwidget.scan_widget.sigZoomAreaSelected.disconnect()
        self._mw.removeDockWidget(dockwidget)
        dockwidget.setParent(None)
        dockwidget.deleteLater()

    def _add_scan_dockwidget(self, axes):
        axes_constr = self._scanning_logic().magnet_control_axes
        axes_constr = tuple(axes_constr[ax] for ax in axes)
        channel_constr = [ScannerChannel('FOM')]

        axes = tuple(axes)

        if len(axes) == 1:
            if axes in self.scan_1d_dockwidgets:
                self.log.error('Unable to add scanning widget for axes {0}. Widget for this scan '
                               'already created. Remove old widget first.'.format(axes))
                return
            marker_bounds = (axes_constr[0].control_value.bounds, (None, None))
            dockwidget = ScanDockWidget(axes=axes_constr, channels=channel_constr)
            dockwidget.scan_widget.set_marker_bounds(marker_bounds)
            dockwidget.scan_widget.set_plot_range(x_range=axes_constr[0].control_value.bounds)
            self.scan_1d_dockwidgets[axes] = dockwidget
        else:
            if axes in self.scan_2d_dockwidgets:
                self.log.error('Unable to add scanning widget for axes {0}. Widget for this scan '
                               'already created. Remove old widget first.'.format(axes))
                return

            marker_bounds = (axes_constr[0].control_value.bounds, axes_constr[1].control_value.bounds)
            marker_size = [(marker_bounds[0][1]-marker_bounds[0][0])/10,
                           (marker_bounds[1][1]-marker_bounds[1][0])/10]
            dockwidget = ScanDockWidget(axes=axes_constr, channels=channel_constr)
            dockwidget.scan_widget.set_marker_size(marker_size)
            dockwidget.scan_widget.set_marker_bounds(marker_bounds)
            dockwidget.scan_widget.set_plot_range(x_range=axes_constr[0].control_value.bounds,
                                                  y_range=axes_constr[1].control_value.bounds)
            self.scan_2d_dockwidgets[axes] = dockwidget

        dockwidget.setAllowedAreas(QtCore.Qt.TopDockWidgetArea)
        self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
        dockwidget.scan_widget.sigMarkerPositionChanged.connect(
            self.__get_marker_update_func(axes)
        )
        dockwidget.scan_widget.toggle_scan_button.clicked.connect(
            self.__get_toggle_scan_func(axes)
        )
        dockwidget.scan_widget.save_scan_button.clicked.connect(
            self.__get_save_scan_data_func(axes)
        )
        dockwidget.scan_widget.sigZoomAreaSelected.connect(
            self.__get_range_from_selection_func(axes)
        )

    def set_active_tab(self, axes):
        avail_axs = list(self.scan_1d_dockwidgets.keys())
        avail_axs.extend(self.scan_2d_dockwidgets.keys())

        if axes not in avail_axs:
            raise ValueError(f"Unknown axes: {axes}")

        if len(axes) == 1:
            self.scan_1d_dockwidgets.get(axes).raise_()
        else:
            self.scan_2d_dockwidgets.get(axes).raise_()

    @QtCore.Slot(bool)
    def toggle_cursor_zoom(self, enable):
        if self._mw.action_utility_zoom.isChecked() != enable:
            self._mw.action_utility_zoom.blockSignals(True)
            self._mw.action_utility_zoom.setChecked(enable)
            self._mw.action_utility_zoom.blockSignals(False)

        for dockwidget in self.scan_2d_dockwidgets.values():
            dockwidget.scan_widget.toggle_zoom(enable)
        for dockwidget in self.scan_1d_dockwidgets.values():
            dockwidget.scan_widget.toggle_zoom(enable)

    @QtCore.Slot()
    def restore_scanner_settings(self):
        """ ToDo: Document
        """
        self.scanner_settings_updated({'frequency': self._scanning_logic().scan_frequency})

    @QtCore.Slot(bool)
    def scanner_settings_toggle_gui_lock(self, locked):
        if locked:
            self._scanner_settings_locked = True
            # todo: maybe disable/grey out scanner gui elements
        else:
            self._scanner_settings_locked = False #unlock

    @QtCore.Slot(dict)
    def scanner_settings_updated(self, settings=None):
        """
        Update scanner settings from logic and set widgets accordingly.

        @param dict settings: Settings dict containing the scanner settings to update.
                              If None (default) read the scanner setting from logic and update.
        """
        if not isinstance(settings, dict):
            settings = self._scanning_logic().scan_settings

        if self._scanner_settings_locked:
            return
        # ToDo: Handle all remaining settings
        # ToDo: Implement backwards scanning functionality

        if 'resolution' in settings:
            self.scanner_control_dockwidget.set_resolution(settings['resolution'])
        if 'range' in settings:
            self.scanner_control_dockwidget.set_range(settings['range'])
        if 'frequency' in settings:
            """
            old_freq = self._ssd.settings_widget.frequency
            new_freq = {
                ax: (forward, old_freq[ax][1]) for ax, forward in settings['frequency'].items()
            }
            self._ssd.settings_widget.set_frequency(new_freq)
            """
            self.update_crosshair_sizes()
        return

    @QtCore.Slot(dict)
    def set_scanner_target_position(self, target_pos):
        """
        Issues new target to logic and updates gui.

        @param dict target_pos:
        """
        if not self._scanner_settings_locked:
            self.sigScannerTargetChanged.emit(target_pos, self.module_uuid)
            # update gui with target, not actual logic values
            # we can not rely on the execution order of the above emit
            self.scanner_target_updated(pos_dict=target_pos, caller_id=None)
        else:
            # refresh gui with stored values
            self.scanner_target_updated(pos_dict=None, caller_id=None)

    def scanner_target_updated(self, pos_dict=None, caller_id=None):
        """
        Updates the scanner target and set widgets accordingly.

        @param dict pos_dict: The scanner position dict to update each axis position.
                              If None (default) read the scanner position from logic and update.
        @param int caller_id: The qudi module object id responsible for triggering this update
        """

        # If this update has been issued by this module, do not update display.
        # This has already been done before notifying the logic.
        if caller_id is self.module_uuid:
            return

        if not isinstance(pos_dict, dict):
            pos_dict = self._scanning_logic().magnet_target

        self.log.debug(f"Updating gui: target_reached: {self._scanning_logic().target_reached}")

        target_reached = self._scanning_logic().target_reached
        spinboxes = [w['pos_spinbox'] for w in self.scanner_control_dockwidget.widget().axes_widgets.values()]
        for sb in spinboxes:
            color_str = "QAbstractSpinBox { color: red; }" if not target_reached else ""
            sb.setStyleSheet(color_str)

        self._update_scan_markers(pos_dict)
        self.scanner_control_dockwidget.set_target(pos_dict)

    def scan_state_updated(self, is_running, scan_data=None, caller_id=None):
        scan_axes = scan_data.settings.axes if scan_data is not None else None
        self._toggle_enable_scan_buttons(not is_running, exclude_scan=scan_axes)

        self._toggle_enable_actions(not is_running)
        self._toggle_enable_scan_crosshairs(not is_running)
        self.scanner_settings_toggle_gui_lock(is_running)

        if scan_data is not None:
            if scan_data.settings.scan_dimension == 2:
                dockwidget = self.scan_2d_dockwidgets.get(scan_axes, None)
            else:
                dockwidget = self.scan_1d_dockwidgets.get(scan_axes, None)
            if dockwidget is not None:
                dockwidget.scan_widget.toggle_scan_button.setChecked(is_running)
                self._update_scan_data(scan_data)
                self.update_crosshair_sizes()
        return

    def restore_history(self):
        """
        For all axes, restore last taken image.
        """
        avail_axs = list(self.scan_1d_dockwidgets.keys())
        avail_axs.extend(self.scan_2d_dockwidgets.keys())

        restored_axs = []

        ids_to_restore = np.asarray([self._data_logic().get_current_scan_id(ax) for ax in avail_axs])
        ids_to_restore = ids_to_restore[~np.isnan(ids_to_restore)].astype(int)

        [self._data_logic().restore_from_history(id) for id in ids_to_restore]

        # auto range 2d widgets
        # todo: shouldn't be needed, as .restore_from_history() calls _update_scan_data() calls autoRange()
        for ax in avail_axs:
            if len(ax) == 2:
                dockwidget = self.scan_2d_dockwidgets.get(ax, None)
                dockwidget.scan_widget.image_widget.autoRange()

    def _update_scan_markers(self, pos_dict, exclude_scan=None):
        """
        """
        for scan_axes, dockwidget in self.scan_2d_dockwidgets.items():
            if exclude_scan != scan_axes:
                old_x, old_y = dockwidget.scan_widget.marker_position
                new_pos = (pos_dict.get(scan_axes[0], old_x), pos_dict.get(scan_axes[1], old_y))
                dockwidget.scan_widget.blockSignals(True)
                dockwidget.scan_widget.set_marker_position(new_pos)
                dockwidget.scan_widget.blockSignals(False)
        for scan_axes, dockwidget in self.scan_1d_dockwidgets.items():
            if exclude_scan != scan_axes:
                new_pos = pos_dict.get(scan_axes[0], dockwidget.scan_widget.marker_position)
                dockwidget.scan_widget.blockSignals(True)
                dockwidget.scan_widget.set_marker_position(new_pos)
                dockwidget.scan_widget.blockSignals(False)

    def _update_scan_sliders(self, pos_dict):
        """
        """
        for scan_axes, dockwidget in self.scan_2d_dockwidgets.items():
            if not any(ax in pos_dict for ax in scan_axes):
                continue
        self.scanner_control_dockwidget.set_target(pos_dict)

    @QtCore.Slot(object)
    def _update_from_history(self, scan_data):
        self._update_scan_data(scan_data)
        self.set_active_tab(scan_data.settings.axes)

    @QtCore.Slot(object)
    def _update_scan_data(self, scan_data):
        """
        @param ScanData scan_data:
        """
        axes = scan_data.settings.axes
        try:
            dockwidget = self.scan_2d_dockwidgets[axes]
        except KeyError:
            dockwidget = self.scan_1d_dockwidgets.get(axes, None)
        if dockwidget is None:
            self.log.error(f'No scan dockwidget found for scan axes {axes}')
        else:
            dockwidget.scan_widget.set_scan_data(scan_data)

    def _toggle_enable_scan_crosshairs(self, enable):
        for dockwidget in self.scan_2d_dockwidgets.values():
            dockwidget.scan_widget.toggle_marker(enable)
        for axes, dockwidget in self.scan_1d_dockwidgets.items():
            dockwidget.scan_widget.toggle_marker(enable)

    def _toggle_enable_scan_buttons(self, enable, exclude_scan=None):
        for axes, dockwidget in self.scan_2d_dockwidgets.items():
            if exclude_scan != axes:
                dockwidget.scan_widget.toggle_scan_button.setEnabled(enable)
        for axes, dockwidget in self.scan_1d_dockwidgets.items():
            if exclude_scan != axes:
                dockwidget.scan_widget.toggle_scan_button.setEnabled(enable)

    def _toggle_enable_actions(self, enable, exclude_action=None):
        if exclude_action is not self._mw.action_utility_zoom:
            self._mw.action_utility_zoom.setEnabled(enable)
        if exclude_action is not self._mw.action_utility_full_range:
            self._mw.action_utility_full_range.setEnabled(enable)
        if exclude_action is not self._mw.action_history_back:
            self._mw.action_history_back.setEnabled(enable)
        if exclude_action is not self._mw.action_history_forward:
            self._mw.action_history_forward.setEnabled(enable)

    def __get_marker_update_func(self, axes: Union[Tuple[str], Tuple[str, str]]):
        def update_func(pos: Union[float, Tuple[float, float]]):
            if len(axes) == 1:
                pos_dict = {axes[0]: pos}
            else:
                pos_dict = {axes[0]: pos[0], axes[1]: pos[1]}
            # self._update_scan_markers(pos_dict, exclude_scan=axes)
            # self.scanner_control_dockwidget.widget().set_target(pos_dict)
            self.set_scanner_target_position(pos_dict)
        return update_func

    def __get_toggle_scan_func(self, axes: Union[Tuple[str], Tuple[str, str]]):
        def toggle_func(enabled):
            self._toggle_enable_scan_buttons(not enabled, exclude_scan=axes)
            self._toggle_enable_actions(not enabled)
            self._toggle_enable_scan_crosshairs(not enabled)
            self.sigToggleScan.emit(enabled, axes, self.module_uuid)
        return toggle_func

    def __get_save_scan_data_func(self, axes: Union[Tuple[str], Tuple[str, str]]):
        def save_scan_func():
            self.save_scan_data(axes)
        return save_scan_func

    def __get_range_from_selection_func(self, axes):
        if len(axes) == 2:
            def set_range_func(x_range, y_range):
                x_range = tuple(sorted(x_range))
                y_range = tuple(sorted(y_range))
                self.sigScanSettingsChanged.emit({'range': {axes[0]: x_range, axes[1]: y_range}})
                self._mw.action_utility_zoom.setChecked(False)
        else:
            def set_range_func(x_range):
                x_range = tuple(sorted(x_range))
                self.sigScanSettingsChanged.emit({'range': {axes[0]: x_range}})
                self._mw.action_utility_zoom.setChecked(False)
        return set_range_func

    def update_crosshair_sizes(self):
        scan_ranges = self._scanning_logic()._scan_ranges
        scan_resolution = self._scanning_logic().scan_resolution
        for ax, dockwidget in self.scan_2d_dockwidgets.items():
            x_min, x_max = scan_ranges[ax[0]]
            y_min, y_max = scan_ranges[ax[1]]
            dockwidget.scan_widget.blockSignals(True)
            try:
                old_pos = dockwidget.scan_widget.marker_position
                dockwidget.scan_widget.set_marker_size(((x_max - x_min) / (scan_resolution[ax[0]] - 1), (y_max - y_min) / (scan_resolution[ax[1]] - 1)))
                dockwidget.scan_widget.set_marker_position(old_pos)
            finally:
                dockwidget.scan_widget.blockSignals(False)

