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
from uuid import UUID
from typing import Union, Tuple, Optional

import numpy as np
from PySide2 import QtCore, QtGui, QtWidgets
from PySide2.QtWidgets import QAction

import qudi.util.uic as uic
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.interface.scanning_probe_interface import ScanData
from qudi.core.module import GuiBase

from qudi.gui.scanning.tilt_correction_dockwidget import TiltCorrectionDockWidget
from qudi.gui.scanning.axes_control_dockwidget import AxesControlDockWidget
from qudi.gui.scanning.optimizer_setting_dialog import OptimizerSettingsDialog
from qudi.gui.scanning.scan_settings_dialog import ScannerSettingDialog
from qudi.gui.scanning.scan_dockwidget import ScanDockWidget
from qudi.gui.scanning.optimizer_dockwidget import OptimizerDockWidget
from qudi.logic.scanning_data_logic import ScanningDataLogic
from qudi.logic.scanning_probe_logic import ScanningProbeLogic
from qudi.logic.scanning_optimize_logic import ScanningOptimizeLogic


class ConfocalMainWindow(QtWidgets.QMainWindow):
    """Create the Mainwindow based on the corresponding *.ui file."""

    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_scannergui.ui')

        # Load it
        super().__init__()
        uic.loadUi(ui_file, self)

        self.action_toggle_tilt_correction = ToggleIconsQAction(
            self,
            'Tilt correction',
            "./artwork/icons/correct-tilt_toggle.svg",
            "./artwork/icons/correct-tilt_toggle_off.svg",
        )
        self.action_toggle_tilt_correction.setToolTip(
            "Toggle tilt correction. \nIf greyed out, you need to configure"
            " valid support vectors (see 'View'/'Tilt correction') or your"
            " scanning device does not support coordination transformation."
        )
        self.util_toolBar.addAction(self.action_toggle_tilt_correction)
        return

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.action_utility_zoom.setChecked(not self.action_utility_zoom.isChecked())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
        return


class SaveDialog(QtWidgets.QDialog):
    """Dialog to provide feedback and block GUI while saving"""

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


class ScannerGui(GuiBase):
    """Main confocal GUI Class for XY and depth scans.

    Example config for copy-paste:

    scanner_gui:
        module.Class: 'scanning.scannergui.ScannerGui'
        # options:
        #     default_position_unit_prefix: null  # optional, use unit prefix characters, e.g. 'u' or 'n'
        #     min_crosshair_size_fraction: 0.02   # minimum crosshair size as fraction of the displayed scan range
        connect:
            scanning_logic: scanning_probe_logic
            data_logic: scanning_data_logic
            optimize_logic: scanning_optimize_logic

    """

    # declare connectors
    _scanning_logic = Connector(name='scanning_logic', interface='ScanningProbeLogic')
    _data_logic = Connector(name='data_logic', interface='ScanningDataLogic')
    _optimize_logic = Connector(name='optimize_logic', interface='ScanningOptimizeLogic')

    # config options for gui
    _default_position_unit_prefix = ConfigOption(name='default_position_unit_prefix', default=None)
    # minimum crosshair size as fraction of the displayed scan range
    _min_crosshair_size_fraction = ConfigOption(name='min_crosshair_size_fraction', default=1 / 50, missing='nothing')

    # status vars
    _window_state = StatusVar(name='window_state', default=None)
    _window_geometry = StatusVar(name='window_geometry', default=None)

    # signals
    sigScannerTargetChanged = QtCore.Signal(dict, object)
    sigFrequencyChanged = QtCore.Signal(str, float)
    sigBackFrequencyChanged = QtCore.Signal(str, float)
    sigUseBackScanSettings = QtCore.Signal(bool)
    sigToggleScan = QtCore.Signal(bool, tuple, object)
    sigOptimizerSettingsChanged = QtCore.Signal(str, tuple, tuple, dict, dict, dict, dict, dict)
    sigToggleOptimize = QtCore.Signal(bool)
    sigSaveScan = QtCore.Signal(object, object, str)
    sigSaveFinished = QtCore.Signal()
    sigShowSaveDialog = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # QMainWindow and QDialog child instances
        self._mw: ConfocalMainWindow = None
        self._ssd: Optional[ScannerSettingDialog] = None
        self._osd: Optional[OptimizerSettingsDialog] = None

        # References to automatically generated GUI elements
        self.optimizer_settings_axes_widgets = None
        self.scan_2d_dockwidgets = None
        self.scan_1d_dockwidgets = None

        # References to static dockwidgets
        self.optimizer_dockwidget = None
        self.scanner_control_dockwidget: Optional[AxesControlDockWidget] = None

        # misc
        self._optimizer_id = 0
        self._optimizer_state = {'is_running': False}
        self._n_save_tasks = 0
        return

    def on_activate(self):
        """Initializes all needed UI files and establishes the connectors.

        This method executes the all the inits for the differnt GUIs and passes
        the event argument from fysom to the methods.
        """
        self._optimizer_id = self._optimize_logic().module_uuid

        self.scan_2d_dockwidgets = dict()
        self.scan_1d_dockwidgets = dict()

        # Initialize main window
        self._mw = ConfocalMainWindow()
        self._mw.setDockNestingEnabled(True)

        # Initialize fixed dockwidgets
        self._init_static_dockwidgets()

        # Initialize dialog windows
        self._init_optimizer_settings()
        self._init_scanner_settings()
        self._save_dialog = SaveDialog(self._mw)

        scan_logic: ScanningProbeLogic = self._scanning_logic()

        # Automatically generate scanning widgets for desired scans
        scans = list()
        axes = tuple(scan_logic.scanner_axes)
        for i, first_ax in enumerate(axes, 1):
            # if not scans:
            scans.append((first_ax,))
            for second_ax in axes[i:]:
                scans.append((first_ax, second_ax))
        for scan in scans:
            self._add_scan_dockwidget(scan)

        # Initialize widget data
        self.update_scanner_settings_from_logic()
        self.scanner_target_updated()
        self.scan_state_updated(scan_logic.module_state() != 'idle')

        # Connect signals
        self.sigScannerTargetChanged.connect(self._scanning_logic().set_target_position, QtCore.Qt.QueuedConnection)
        self.sigFrequencyChanged.connect(scan_logic.set_scan_frequency, QtCore.Qt.QueuedConnection)
        self.sigBackFrequencyChanged.connect(scan_logic.set_back_scan_frequency, QtCore.Qt.QueuedConnection)
        self.sigUseBackScanSettings.connect(scan_logic.set_use_back_scan_settings, QtCore.Qt.QueuedConnection)
        self.sigToggleScan.connect(scan_logic.toggle_scan, QtCore.Qt.QueuedConnection)
        self.sigToggleOptimize.connect(self._optimize_logic().toggle_optimize, QtCore.Qt.QueuedConnection)
        self._mw.action_optimize_position.triggered[bool].connect(self.toggle_optimize, QtCore.Qt.QueuedConnection)
        self._optimize_logic().sigOptimizeSequenceDimensionsChanged.connect(
            self._init_optimizer_dockwidget, QtCore.Qt.QueuedConnection
        )
        self._optimize_logic().sigOptimizeSequenceDimensionsChanged.connect(
            self.update_optimizer_settings_from_logic, QtCore.Qt.QueuedConnection
        )
        self._mw.action_restore_default_view.triggered.connect(self.restore_default_view)
        self._mw.action_save_all_scans.triggered.connect(lambda x: self.save_scan_data(scan_axes=None))
        self.sigSaveScan.connect(self._data_logic().save_scan_by_axis, QtCore.Qt.QueuedConnection)
        self.sigSaveFinished.connect(self._save_dialog.hide, QtCore.Qt.QueuedConnection)
        self._data_logic().sigSaveStateChanged.connect(self._track_save_status)

        self._mw.action_utility_zoom.toggled.connect(self.toggle_cursor_zoom)
        self._mw.action_utility_full_range.triggered.connect(self.set_full_range, QtCore.Qt.QueuedConnection)
        self._mw.action_history_forward.triggered.connect(self._data_logic().history_next, QtCore.Qt.QueuedConnection)
        self._mw.action_history_back.triggered.connect(self._data_logic().history_previous, QtCore.Qt.QueuedConnection)

        self._scanning_logic().sigScannerTargetChanged.connect(self.scanner_target_updated, QtCore.Qt.QueuedConnection)
        self._scanning_logic().sigScanSettingsChanged.connect(
            self.update_scanner_settings_from_logic, QtCore.Qt.QueuedConnection
        )
        self._scanning_logic().sigScanStateChanged.connect(self.scan_state_updated, QtCore.Qt.QueuedConnection)
        self._data_logic().sigHistoryScanDataRestored.connect(self._update_from_history, QtCore.Qt.QueuedConnection)
        self._optimize_logic().sigOptimizeStateChanged.connect(self.optimize_state_updated, QtCore.Qt.QueuedConnection)
        self.sigOptimizerSettingsChanged.connect(
            self._optimize_logic().set_optimize_settings, QtCore.Qt.QueuedConnection
        )

        self.sigShowSaveDialog.connect(
            lambda x: self._save_dialog.show() if x else self._save_dialog.hide(), QtCore.Qt.DirectConnection
        )

        # tilt correction signals
        tilt_widget = self.tilt_correction_dockwidget
        tilt_widget.tilt_set_01_pushButton.clicked.connect(
            lambda: self.tilt_corr_set_support_vector(0), QtCore.Qt.QueuedConnection
        )
        tilt_widget.tilt_set_02_pushButton.clicked.connect(
            lambda: self.tilt_corr_set_support_vector(1), QtCore.Qt.QueuedConnection
        )
        tilt_widget.tilt_set_03_pushButton.clicked.connect(
            lambda: self.tilt_corr_set_support_vector(2), QtCore.Qt.QueuedConnection
        )
        tilt_widget.tilt_set_04_pushButton.clicked.connect(
            lambda: self.tilt_corr_set_support_vector(3), QtCore.Qt.QueuedConnection
        )
        tilt_widget.auto_origin_switch.toggle_switch.sigStateChanged.connect(
            self.apply_tilt_corr_support_vectors, QtCore.Qt.QueuedConnection
        )
        self._mw.action_toggle_tilt_correction.triggered.connect(
            self.toggle_tilt_correction, QtCore.Qt.QueuedConnection
        )
        [
            box.valueChanged.connect(self.apply_tilt_corr_support_vectors, QtCore.Qt.QueuedConnection)
            for box_row in tilt_widget.support_vecs_box
            for box in box_row
        ]
        self._scanning_logic().sigTiltCorrSettingsChanged.connect(
            self.tilt_corr_support_vector_updated, QtCore.Qt.QueuedConnection
        )

        # Initialize dockwidgets to default view
        self.restore_default_view()
        self.show()

        self.restore_history()

        self._restore_window_geometry(self._mw)
        self._restore_tilt_correction()

        return

    def on_deactivate(self):
        """Reverse steps of activation

        @return int: error code (0:OK, -1:error)
        """
        # Remember window position and geometry and close window
        self._save_window_geometry(self._mw)
        self._mw.close()
        self.optimizer_dockwidget = None

        # Disconnect signals
        self.sigScannerTargetChanged.disconnect()
        self.sigFrequencyChanged.disconnect()
        self.sigBackFrequencyChanged.disconnect()
        self.sigUseBackScanSettings.disconnect()
        self.sigToggleScan.disconnect()
        self.sigToggleOptimize.disconnect()
        self.sigOptimizerSettingsChanged.disconnect()
        self._mw.action_optimize_position.triggered[bool].disconnect()
        self._mw.action_restore_default_view.triggered.disconnect()
        self._mw.action_history_forward.triggered.disconnect()
        self._mw.action_history_back.triggered.disconnect()
        self._mw.action_utility_full_range.triggered.disconnect()
        self._mw.action_utility_zoom.toggled.disconnect()
        self._scanning_logic().sigScannerTargetChanged.disconnect(self.scanner_target_updated)
        self._scanning_logic().sigScanStateChanged.disconnect(self.scan_state_updated)
        self._scanning_logic().sigScanSettingsChanged.disconnect(self.update_scanner_settings_from_logic)
        self._optimize_logic().sigOptimizeStateChanged.disconnect(self.optimize_state_updated)
        self._optimize_logic().sigOptimizeSequenceDimensionsChanged.disconnect(self._init_optimizer_dockwidget)
        self._optimize_logic().sigOptimizeSequenceDimensionsChanged.disconnect(
            self.update_optimizer_settings_from_logic
        )
        self._data_logic().sigHistoryScanDataRestored.disconnect(self._update_from_history)
        self.scanner_control_dockwidget.sigTargetChanged.disconnect()
        self.scanner_control_dockwidget.sigSliderMoved.disconnect()

        for scan in tuple(self.scan_1d_dockwidgets):
            self._remove_scan_dockwidget(scan)
        for scan in tuple(self.scan_2d_dockwidgets):
            self._remove_scan_dockwidget(scan)

        tilt_widget = self.tilt_correction_dockwidget
        tilt_widget.tilt_set_01_pushButton.clicked.disconnect()
        tilt_widget.tilt_set_02_pushButton.clicked.disconnect()
        tilt_widget.tilt_set_03_pushButton.clicked.disconnect()
        tilt_widget.tilt_set_04_pushButton.clicked.disconnect()
        tilt_widget.auto_origin_switch.toggle_switch.sigStateChanged.disconnect()
        self._scanning_logic().sigTiltCorrSettingsChanged.disconnect()
        self._mw.action_toggle_tilt_correction.triggered.disconnect()

    def show(self):
        """Make main window visible and put it above all other windows."""
        # Show the Main Confocal GUI:
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def _init_optimizer_settings(self):
        """Configuration and initialisation of the optimizer settings dialog."""
        scan_logic: ScanningProbeLogic = self._scanning_logic()
        optimize_logic: ScanningOptimizeLogic = self._optimize_logic()
        self._osd = OptimizerSettingsDialog(
            scan_logic.scanner_axes.values(),
            scan_logic.scanner_channels.values(),
            optimize_logic.allowed_scan_sequences,
            optimize_logic.allowed_optimizer_sequence_dimensions,
            scan_logic.scanner_constraints.back_scan_capability,
        )

        # Connect MainWindow actions
        self._mw.action_optimizer_settings.triggered.connect(lambda x: self._osd.exec_())

        # Connect the action of the settings window with the code:
        self._osd.accepted.connect(self.change_optimizer_settings)
        self._osd.rejected.connect(self.update_optimizer_settings_from_logic)
        self._osd.button_box.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.change_optimizer_settings)
        # pull in data
        self.update_optimizer_settings_from_logic()

    def _init_scanner_settings(self):
        """ """
        # Create the Settings dialog
        scan_logic: ScanningProbeLogic = self._scanning_logic()
        self._ssd = ScannerSettingDialog(scan_logic.scanner_axes.values(), scan_logic.scanner_constraints)

        self._ssd.settings_widget.configure_backward_scan_checkbox.setChecked(scan_logic.use_back_scan_settings)
        self._ssd.settings_widget.set_backward_settings_visibility(scan_logic.use_back_scan_settings)
        # Connect MainWindow actions
        self._mw.action_scanner_settings.triggered.connect(lambda x: self._ssd.exec_())

        # Connect the action of the settings dialog with the GUI module:
        self._ssd.accepted.connect(self.apply_scanner_settings)
        self._ssd.rejected.connect(self.update_scanner_settings_from_logic)
        self._ssd.button_box.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply_scanner_settings)

    def _init_static_dockwidgets(self):
        scan_logic: ScanningProbeLogic = self._scanning_logic()
        self.scanner_control_dockwidget = AxesControlDockWidget(
            tuple(scan_logic.scanner_axes.values()), scan_logic.back_scan_capability
        )
        self.scanner_control_dockwidget.set_backward_settings_visibility(scan_logic.use_back_scan_settings)
        if self._default_position_unit_prefix is not None:
            self.scanner_control_dockwidget.set_assumed_unit_prefix(self._default_position_unit_prefix)
        self.scanner_control_dockwidget.setAllowedAreas(QtCore.Qt.TopDockWidgetArea)
        self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.scanner_control_dockwidget)
        self.scanner_control_dockwidget.visibilityChanged.connect(self._mw.action_view_scanner_control.setChecked)
        self._mw.action_view_scanner_control.triggered[bool].connect(self.scanner_control_dockwidget.setVisible)

        self._mw.action_view_line_scan.triggered[bool].connect(
            lambda is_vis: [wid.setVisible(is_vis) for wid in self.scan_1d_dockwidgets.values()]
        )
        self.scanner_control_dockwidget.sigResolutionChanged.connect(scan_logic.set_scan_resolution)
        self.scanner_control_dockwidget.sigBackResolutionChanged.connect(scan_logic.set_back_scan_resolution)
        self.scanner_control_dockwidget.sigRangeChanged.connect(scan_logic.set_scan_range)
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
        self._init_optimizer_dockwidget()

        self._mw.util_toolBar.visibilityChanged.connect(self._mw.action_view_toolbar.setChecked)
        self._mw.action_view_toolbar.triggered[bool].connect(self._mw.util_toolBar.setVisible)

        # Add tilt correction widget to the toolbar as a button
        self.tilt_correction_dockwidget = TiltCorrectionDockWidget(scanner_axes=self._scanning_logic().scanner_axes)
        self.tilt_correction_dockwidget.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea)
        self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.tilt_correction_dockwidget)
        self.tilt_correction_dockwidget.setVisible(False)
        self.tilt_correction_dockwidget.visibilityChanged.connect(self._mw.action_view_tilt_correction.setChecked)
        self._mw.action_view_tilt_correction.triggered[bool].connect(self.tilt_correction_dockwidget.setVisible)

    def _init_optimizer_dockwidget(self):
        optimizer_dockwidget = OptimizerDockWidget(
            axes=self._scanning_logic().scanner_axes,
            plot_dims=self._optimize_logic().optimizer_sequence_dimensions,
            sequence=self._optimize_logic().scan_sequence,
        )
        if self.optimizer_dockwidget:
            try:
                self._mw.tabifyDockWidget(self.optimizer_dockwidget, optimizer_dockwidget)
                self._mw.removeDockWidget(self.optimizer_dockwidget)
            except RuntimeError:
                pass
        else:
            self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, optimizer_dockwidget)

        self.optimizer_dockwidget = optimizer_dockwidget
        self.optimizer_dockwidget.visibilityChanged.connect(self._mw.action_view_optimizer.setChecked)
        self._mw.action_view_optimizer.triggered[bool].connect(self.optimizer_dockwidget.setVisible)

    @QtCore.Slot()
    def restore_default_view(self):
        """Restore the arrangement of DockWidgets to default"""
        self._mw.setDockNestingEnabled(True)

        # Remove all dockwidgets from main window layout
        self._mw.removeDockWidget(self.optimizer_dockwidget)
        self._mw.removeDockWidget(self.scanner_control_dockwidget)
        self._mw.removeDockWidget(self.tilt_correction_dockwidget)
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

        # Add tilt correction dock widget
        self.tilt_correction_dockwidget.setFloating(False)
        self.tilt_correction_dockwidget.setVisible(False)
        self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.tilt_correction_dockwidget)

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
        # Add optimizer dock widget to layout
        self.optimizer_dockwidget.show()
        self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.optimizer_dockwidget)
        self.optimizer_dockwidget.setFloating(False)

        # split scan dock widget with optimizer dock widget if needed. Resize all groups.
        self._resize_scan_optimize_dockwidgets()
        # tabify dockwidgets if needed, needs to be done after .splitDockWidget()
        self._tabify_dockwidgets()

        return

    def _resize_scan_optimize_dockwidgets(self):
        dockwidgets_2d = tuple(self.scan_2d_dockwidgets.values())
        dockwidgets_1d = tuple(self.scan_1d_dockwidgets.values())
        multiple_2d_scans = len(dockwidgets_2d) > 1
        has_1d_scans = bool(dockwidgets_1d)
        has_2d_scans = bool(dockwidgets_2d)
        if has_1d_scans and has_2d_scans:
            self._mw.splitDockWidget(dockwidgets_1d[0], self.optimizer_dockwidget, QtCore.Qt.Vertical)
            self._mw.resizeDocks((dockwidgets_1d[0], self.optimizer_dockwidget), (3, 2), QtCore.Qt.Vertical)
            self._mw.resizeDocks((dockwidgets_2d[0], dockwidgets_1d[0]), (1, 1), QtCore.Qt.Horizontal)
        elif multiple_2d_scans:
            self._mw.splitDockWidget(dockwidgets_2d[1], self.optimizer_dockwidget, QtCore.Qt.Vertical)
            self._mw.resizeDocks((dockwidgets_2d[1], self.optimizer_dockwidget), (3, 2), QtCore.Qt.Vertical)
            self._mw.resizeDocks((dockwidgets_2d[0], dockwidgets_2d[1]), (1, 1), QtCore.Qt.Horizontal)
        elif has_1d_scans:
            self._mw.resizeDocks((dockwidgets_1d[0], self.optimizer_dockwidget), (1, 1), QtCore.Qt.Horizontal)
        elif has_2d_scans:
            self._mw.resizeDocks((dockwidgets_2d[0], self.optimizer_dockwidget), (1, 1), QtCore.Qt.Horizontal)

    def _tabify_dockwidgets(self):
        dockwidgets_2d = tuple(self.scan_2d_dockwidgets.values())
        dockwidgets_1d = tuple(self.scan_1d_dockwidgets.values())
        multiple_2d_scans = len(dockwidgets_2d) > 1
        multiple_1d_scans = len(dockwidgets_1d) > 1
        has_1d_scans = bool(dockwidgets_1d)
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

    def _restore_tilt_correction(self):
        tilt_settings = self._scanning_logic().tilt_correction_settings

        self.tilt_corr_support_vector_updated(tilt_settings)
        try:
            self.apply_tilt_corr_support_vectors()
        except ValueError:
            self.log.warning("Couldn't restore tilt correction settings.")
            pass

    @QtCore.Slot(tuple)
    def save_scan_data(self, scan_axes: Union[None, Tuple[str], Tuple[str, str]]):
        """
        Save data for a given scan axis.
        @param tuple scan_axes: Axis to save, leave None for all axes where data is available.
        """
        self.sigShowSaveDialog.emit(True)
        if scan_axes is None:
            data_logic: ScanningDataLogic = self._data_logic()
            scan_axes = data_logic.get_axes_with_history_entry()
        else:
            scan_axes = [scan_axes]
        for ax in scan_axes:
            try:
                cbar_range = self.scan_2d_dockwidgets[ax].scan_widget.image_widget.levels
            except KeyError:
                cbar_range = None
            if ax in self.scan_1d_dockwidgets:
                tag = self.scan_1d_dockwidgets[ax].scan_widget.save_nametag_lineedit.text()
            elif ax in self.scan_2d_dockwidgets:
                tag = self.scan_2d_dockwidgets[ax].scan_widget.save_nametag_lineedit.text()
            else:
                tag = None
            self.sigSaveScan.emit(ax, cbar_range, tag)

    def _track_save_status(self, in_progress):
        if in_progress:
            self._n_save_tasks += 1
        else:
            self._n_save_tasks -= 1

        if self._n_save_tasks < 1:
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
        axes_constr = self._scanning_logic().scanner_axes
        axes_constr = tuple(axes_constr[ax] for ax in axes)
        channel_constr = list(self._scanning_logic().scanner_channels.values())
        optimizer_range = self._optimize_logic().scan_range
        axes = tuple(axes)

        if len(axes) == 1:
            if axes in self.scan_1d_dockwidgets:
                self.log.error(
                    'Unable to add scanning widget for axes {0}. Widget for this scan '
                    'already created. Remove old widget first.'.format(axes)
                )
                return
            marker_bounds = (axes_constr[0].position.bounds, (None, None))
            dockwidget = ScanDockWidget(axes=axes_constr, channels=channel_constr)
            dockwidget.scan_widget.set_marker_bounds(marker_bounds)
            dockwidget.scan_widget.set_plot_range(x_range=axes_constr[0].position.bounds)
            self.scan_1d_dockwidgets[axes] = dockwidget

            # todo not working on view/restore default
            # dockwidget.visibilityChanged.connect(self._mw.action_view_line_scan.setChecked)
        else:
            if axes in self.scan_2d_dockwidgets:
                self.log.error(
                    'Unable to add scanning widget for axes {0}. Widget for this scan '
                    'already created. Remove old widget first.'.format(axes)
                )
                return
            marker_size = tuple(abs(optimizer_range[ax]) for ax in axes)
            marker_bounds = (axes_constr[0].position.bounds, axes_constr[1].position.bounds)
            dockwidget = ScanDockWidget(
                axes=axes_constr,
                channels=channel_constr,
                xy_region_min_size_percentile=self._min_crosshair_size_fraction,
            )
            dockwidget.scan_widget.set_marker_size(marker_size)
            dockwidget.scan_widget.set_marker_bounds(marker_bounds)
            dockwidget.scan_widget.set_plot_range(
                x_range=axes_constr[0].position.bounds, y_range=axes_constr[1].position.bounds
            )
            self.scan_2d_dockwidgets[axes] = dockwidget

        dockwidget.setAllowedAreas(QtCore.Qt.TopDockWidgetArea)
        self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
        dockwidget.scan_widget.sigMarkerPositionChanged.connect(self.__get_marker_update_func(axes))
        dockwidget.scan_widget.toggle_scan_button.clicked.connect(self.__get_toggle_scan_func(axes))
        dockwidget.scan_widget.save_scan_button.clicked.connect(self.__get_save_scan_data_func(axes))
        dockwidget.scan_widget.sigZoomAreaSelected.connect(self.__get_range_from_selection_func(axes))

    def _add_tilt_correction_dock_widget(self):
        dockwidget = TiltCorrectionDockWidget()
        self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)

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
    def apply_scanner_settings(self):
        """Update the logic with all scanner settings from the GUI."""
        self.scanner_control_dockwidget.emit_current_settings()
        for ax, (forward, backward) in self._ssd.settings_widget.frequency.items():
            self.sigFrequencyChanged.emit(ax, forward)
            self.sigBackFrequencyChanged.emit(ax, backward)

        use_back_settings = self._ssd.settings_widget.configure_backward_scan
        self.sigUseBackScanSettings.emit(use_back_settings)

    @QtCore.Slot()
    def update_scanner_settings_from_logic(self):
        """Update all scanner settings from the logic."""
        scan_logic: ScanningProbeLogic = self._scanning_logic()

        self.scanner_control_dockwidget.set_range(scan_logic.scan_ranges)
        self.scanner_control_dockwidget.set_resolution(scan_logic.scan_resolution)
        self.scanner_control_dockwidget.set_back_resolution(scan_logic.back_scan_resolution)

        for ax, forward in scan_logic.scan_frequency.items():
            self._ssd.settings_widget.set_forward_frequency(ax, forward)
        for ax, backward in scan_logic.back_scan_frequency.items():
            self._ssd.settings_widget.set_backward_frequency(ax, backward)

        self._ssd.settings_widget.configure_backward_scan = scan_logic.use_back_scan_settings
        self.scanner_control_dockwidget.set_backward_settings_visibility(scan_logic.use_back_scan_settings)

    @QtCore.Slot()
    def set_full_range(self) -> None:
        scan_logic: ScanningProbeLogic = self._scanning_logic()
        scan_ranges = scan_logic.set_full_scan_ranges()
        self.scanner_control_dockwidget.set_range(scan_ranges)

    @QtCore.Slot(dict)
    def set_scanner_target_position(self, target_pos):
        """
        Issues new target to logic and updates gui.

        @param dict target_pos:
        """
        self.sigScannerTargetChanged.emit(target_pos, self.module_uuid)
        # update gui with target, not actual logic values
        # we can not rely on the execution order of the above emit
        self.scanner_target_updated(pos_dict=target_pos, caller_id=None)

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
            pos_dict = self._scanning_logic().scanner_target

        self._update_scan_markers(pos_dict)
        self.scanner_control_dockwidget.set_target(pos_dict)

    def scan_state_updated(
        self,
        is_running: bool,
        scan_data: Optional[ScanData] = None,
        back_scan_data: Optional[ScanData] = None,
        caller_id: Optional[UUID] = None,
    ):
        scan_axes = scan_data.settings.axes if scan_data is not None else None
        self._toggle_enable_scan_buttons(not is_running, exclude_scan=scan_axes)
        if not self._optimizer_state['is_running']:
            self._toggle_enable_actions(not is_running)
        else:
            self._toggle_enable_actions(not is_running, exclude_action=self._mw.action_optimize_position)
        self._toggle_enable_scan_crosshairs(not is_running)
        self.scanner_control_dockwidget.setEnabled(not is_running)
        if not is_running and scan_data is None:
            # scan could not be started due to some error
            for dockwidget in {**self.scan_2d_dockwidgets, **self.scan_1d_dockwidgets}.values():
                toggle_button = dockwidget.scan_widget.toggle_scan_button
                toggle_button.setChecked(False)
            self._mw.action_optimize_position.setChecked(False)

        if scan_data is not None:
            if caller_id is self._optimizer_id:
                channel = self._osd.data_channel
                if scan_data.settings.scan_dimension == 2:
                    x_ax, y_ax = scan_data.settings.axes
                    self.optimizer_dockwidget.set_image(
                        image=scan_data.data[channel], extent=scan_data.settings.range, axs=scan_data.settings.axes
                    )
                    self.optimizer_dockwidget.set_image_label(
                        axis='bottom', text=x_ax, units=scan_data.axis_units[x_ax], axs=scan_data.settings.axes
                    )
                    self.optimizer_dockwidget.set_image_label(
                        axis='left', text=y_ax, units=scan_data.axis_units[y_ax], axs=scan_data.settings.axes
                    )
                elif scan_data.settings.scan_dimension == 1:
                    x_ax = scan_data.settings.axes[0]
                    self.optimizer_dockwidget.set_plot_data(
                        x=np.linspace(*scan_data.settings.range[0], scan_data.settings.resolution[0]),
                        y=scan_data.data[channel],
                        axs=scan_data.settings.axes,
                    )
                    self.optimizer_dockwidget.set_plot_label(
                        axis='bottom', text=x_ax, units=scan_data.axis_units[x_ax], axs=scan_data.settings.axes
                    )
                    self.optimizer_dockwidget.set_plot_label(
                        axis='left', text=channel, units=scan_data.channel_units[channel], axs=scan_data.settings.axes
                    )
            else:
                if scan_data.settings.scan_dimension == 2:
                    dockwidget = self.scan_2d_dockwidgets.get(scan_axes, None)
                else:
                    dockwidget = self.scan_1d_dockwidgets.get(scan_axes, None)
                if dockwidget is not None:
                    dockwidget.scan_widget.toggle_scan_button.setChecked(is_running)
                    self._update_scan_data(scan_data, back_scan_data)

    @QtCore.Slot(bool, dict, object)
    def optimize_state_updated(self, is_running, optimal_position=None, fit_data=None):
        self._optimizer_state['is_running'] = is_running
        if not is_running:
            # apply normal scan settings to logic again
            # they may have been changed by the optimizer logic
            self.apply_scanner_settings()
        _is_optimizer_valid_1d = not is_running
        _is_optimizer_valid_2d = not is_running

        self._toggle_enable_scan_buttons(not is_running)
        self._toggle_enable_actions(not is_running, exclude_action=self._mw.action_optimize_position)
        self._toggle_enable_scan_crosshairs(not is_running)
        self._mw.action_optimize_position.setChecked(is_running)
        self.scanner_control_dockwidget.setEnabled(not is_running)

        if fit_data is not None and optimal_position is None:
            raise ValueError("Can't understand fit_data without optimal position")

        # Update optimal position crosshair and marker
        if isinstance(optimal_position, dict):
            scan_axs = list(optimal_position.keys())
            if len(optimal_position) == 2:
                _is_optimizer_valid_2d = True
                self.optimizer_dockwidget.set_2d_position(tuple(optimal_position.values()), scan_axs)

            elif len(optimal_position) == 1:
                _is_optimizer_valid_1d = True
                self.optimizer_dockwidget.set_1d_position(next(iter(optimal_position.values())), scan_axs)
        if fit_data is not None and isinstance(optimal_position, dict):
            data = fit_data['fit_data']
            fit_res = fit_data['full_fit_res']
            if data.ndim == 1:
                self.optimizer_dockwidget.set_fit_data(scan_axs, y=data)
                sig_z = fit_res.params['sigma'].value
                self.optimizer_dockwidget.set_1d_position(next(iter(optimal_position.values())), scan_axs, sigma=sig_z)
            elif data.ndim == 2:
                sig_x, sig_y = fit_res.params['sigma_x'].value, fit_res.params['sigma_y'].value
                self.optimizer_dockwidget.set_2d_position(
                    tuple(optimal_position.values()), scan_axs, sigma=[sig_x, sig_y]
                )

        # Hide crosshair and 1d marker when scanning
        if len(scan_axs) == 2:
            self.optimizer_dockwidget.toogle_crosshair(scan_axs, _is_optimizer_valid_2d)
        else:
            self.optimizer_dockwidget.toogle_crosshair(None, _is_optimizer_valid_2d)
        if len(scan_axs) == 1:
            self.optimizer_dockwidget.toogle_marker(scan_axs, _is_optimizer_valid_1d)
        else:
            self.optimizer_dockwidget.toogle_marker(None, _is_optimizer_valid_1d)

    @QtCore.Slot(bool)
    def toggle_optimize(self, enabled):
        """ """
        self._toggle_enable_actions(not enabled, exclude_action=self._mw.action_optimize_position)
        self._toggle_enable_scan_buttons(not enabled)
        self._toggle_enable_scan_crosshairs(not enabled)
        self.sigToggleOptimize.emit(enabled)

    def restore_history(self):
        """
        For all axes, restore last taken image.
        """
        avail_axs = list(self.scan_1d_dockwidgets.keys())
        avail_axs.extend(self.scan_2d_dockwidgets.keys())

        data_logic: ScanningDataLogic = self._data_logic()
        # populate all plot widgets with scandata
        for ax in avail_axs:
            data_logic.restore_from_history(ax, set_target=False)
        # restore the latest scan
        data_logic.restore_from_history()


    def _update_scan_markers(self, pos_dict, exclude_scan=None):
        """ """
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
        """ """
        for scan_axes, dockwidget in self.scan_2d_dockwidgets.items():
            if not any(ax in pos_dict for ax in scan_axes):
                continue
        self.scanner_control_dockwidget.set_target(pos_dict)

    @QtCore.Slot(ScanData, ScanData, int)
    def _update_from_history(self, scan_data: ScanData, back_scan_data: Optional[ScanData], set_target: bool):
        if set_target:
            self.set_scanner_target_position(scan_data.scanner_target_at_start)
        self._update_scan_data(scan_data, back_scan_data)
        self.set_active_tab(scan_data.settings.axes)

    @QtCore.Slot(ScanData, ScanData)
    def _update_scan_data(self, scan_data: ScanData, back_scan_data: Optional[ScanData]):
        """
        @param ScanData scan_data:
        """
        settings = scan_data.settings
        try:
            dockwidget = self.scan_2d_dockwidgets[settings.axes]
        except KeyError:
            dockwidget = self.scan_1d_dockwidgets.get(settings.axes, None)
        if dockwidget is None:
            self.log.error(f'No scan dockwidget found for scan axes {settings.axes}')
            return
        else:
            dockwidget.scan_widget.set_scan_data(scan_data)

        # also update scan range and resolution
        rng = {ax: val for ax, val in zip(settings.axes, settings.range)}
        res = {ax: val for ax, val in zip(settings.axes, settings.resolution)}
        self.scanner_control_dockwidget.set_range(rng)
        self.scanner_control_dockwidget.set_resolution(res)
        if back_scan_data is not None:
            # update back resolution for fast axis
            fast_axis = back_scan_data.settings.axes[0]
            back_res = back_scan_data.settings.resolution[0]
            self.scanner_control_dockwidget.set_back_resolution({fast_axis: back_res})

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
        if exclude_action is not self._mw.action_optimize_position:
            self._mw.action_optimize_position.setEnabled(enable)
        if exclude_action is not self._mw.action_toggle_tilt_correction:
            self._mw.action_toggle_tilt_correction.setEnabled(enable)

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
            """Toggle the scan."""
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
                scan_logic: ScanningProbeLogic = self._scanning_logic()
                scan_logic.set_scan_range(axes[0], x_range)
                scan_logic.set_scan_range(axes[1], y_range)
                self.scanner_control_dockwidget.set_range({axes[0]: x_range, axes[1]: y_range})
                self._mw.action_utility_zoom.setChecked(False)
        else:

            def set_range_func(x_range):
                x_range = tuple(sorted(x_range))
                scan_logic: ScanningProbeLogic = self._scanning_logic()
                scan_logic.set_scan_range(axes[0], x_range)
                self.scanner_control_dockwidget.set_range({axes[0]: x_range})
                self._mw.action_utility_zoom.setChecked(False)

        return set_range_func

    @QtCore.Slot()
    def change_optimizer_settings(self):
        self.sigOptimizerSettingsChanged.emit(
            self._osd.data_channel,
            self._osd.sequence,
            self._osd.sequence_dimension,
            self._osd.range,
            self._osd.resolution,
            self._osd.frequency,
            self._osd.back_resolution,
            self._osd.back_frequency,
        )
        self.optimizer_dockwidget.scan_sequence = self._osd.sequence
        self.update_crosshair_sizes()

    def update_crosshair_sizes(self):
        axes_constr = self._scanning_logic().scanner_axes
        for ax, dockwidget in self.scan_2d_dockwidgets.items():
            width = self._osd.range[ax[0]]
            height = self._osd.range[ax[1]]
            x_min, x_max = axes_constr[ax[0]].position.bounds
            y_min, y_max = axes_constr[ax[1]].position.bounds
            marker_bounds = ((x_min - width / 2, x_max + width / 2), (y_min - height / 2, y_max + height / 2))
            dockwidget.scan_widget.blockSignals(True)
            try:
                old_pos = dockwidget.scan_widget.marker_position
                dockwidget.scan_widget.set_marker_size((width, height))
                dockwidget.scan_widget.set_marker_position(old_pos)
            finally:
                dockwidget.scan_widget.blockSignals(False)

    @QtCore.Slot()
    def update_optimizer_settings_from_logic(self):
        """Update all optimizer settings from the optimizer logic."""
        optimize_logic: ScanningOptimizeLogic = self._optimize_logic()

        # Update optimizer settings QDialog
        self._osd.data_channel = optimize_logic.data_channel
        self._osd.allowed_sequence_dimensions = optimize_logic.allowed_optimizer_sequence_dimensions
        self._osd.allowed_sequences = optimize_logic.allowed_scan_sequences
        self._osd.sequence_dimension = optimize_logic.optimizer_sequence_dimensions
        self._osd.sequence = optimize_logic.scan_sequence
        self._osd.set_range(optimize_logic.scan_range)
        self._osd.set_resolution(optimize_logic.scan_resolution)
        self._osd.set_back_resolution(optimize_logic.back_scan_resolution)
        self._osd.set_frequency(optimize_logic.scan_frequency)
        self._osd.set_back_frequency(optimize_logic.back_scan_frequency)

        axes_constr = self._scanning_logic().scanner_axes
        self.optimizer_dockwidget.scan_sequence = optimize_logic.scan_sequence

        for seq_step in optimize_logic.scan_sequence:
            if len(seq_step) == 1:
                axis = seq_step[0]
                self.optimizer_dockwidget.set_plot_label(
                    axis='bottom', axs=seq_step, text=axis, units=axes_constr[axis].unit
                )
                self.optimizer_dockwidget.set_plot_data(axs=seq_step)
                self.optimizer_dockwidget.set_fit_data(axs=seq_step)

                channel_constr = self._scanning_logic().scanner_channels
                channel = optimize_logic.data_channel
                self.optimizer_dockwidget.set_plot_label(
                    axs=seq_step, axis='left', text=channel, units=channel_constr[channel].unit
                )

            elif len(seq_step) == 2:
                x_axis, y_axis = seq_step
                self.optimizer_dockwidget.set_image_label(
                    axis='bottom', axs=seq_step, text=x_axis, units=axes_constr[x_axis].unit
                )
                self.optimizer_dockwidget.set_image_label(
                    axis='left', axs=seq_step, text=y_axis, units=axes_constr[y_axis].unit
                )
                self.optimizer_dockwidget.set_image(None, axs=seq_step, extent=((-0.5, 0.5), (-0.5, 0.5)))

            # Adjust crosshair size according to optimizer range
            self.update_crosshair_sizes()

    def tilt_corr_set_support_vector(self, idx_vector=0):
        target = self._scanning_logic().scanner_target

        self.tilt_correction_dockwidget.set_support_vector(target, idx_vector)
        self.apply_tilt_corr_support_vectors()

    def tilt_corr_support_vector_updated(self, settings):
        """
        Signal new vectors from logic and update gui accordingly.

        @param dict settings: scanning probe logic settings dict
        @return:
        """

        tilt_widget = self.tilt_correction_dockwidget

        if settings:
            sup_vecs = np.asarray([settings['vec_1'], settings['vec_2'], settings['vec_3']])
            shift_vec = settings.get('vec_shift', {})
            shift_vec = {} if shift_vec is None else shift_vec
            auto_origin = settings['auto_origin']
            axes = list(tilt_widget.support_vectors[0].keys())
            # self.log.debug(f"Update vectors from logic: {sup_vecs}, {shift_vec}")

            default_vec = {ax: np.inf for ax in axes}
            shift_vec = {**default_vec, **shift_vec}

            auto_state = 'ON' if auto_origin else 'OFF'

            tilt_widget.blockSignals(True)
            tilt_widget.set_auto_origin(auto_state, reset=False)
            tilt_widget.blockSignals(False)

            for i_row, box_row in enumerate(tilt_widget.support_vecs_box):
                for j_col, box in enumerate(box_row):
                    ax = axes[j_col]
                    if i_row == len(tilt_widget.support_vecs_box) - 1:
                        vec = shift_vec
                    else:
                        vec = sup_vecs[i_row]

                    box.blockSignals(True)
                    box.setValue(vec[ax])
                    box.blockSignals(False)

    def apply_tilt_corr_support_vectors(self):
        support_vecs = self.tilt_correction_dockwidget.support_vecs_box
        support_vecs_val = self.tilt_correction_dockwidget.support_vectors

        dim_idxs = [(idx, key) for idx, key in enumerate(self._scanning_logic().scanner_axes.keys())]

        all_vecs_valid = True
        vecs_to_check = [0, 1, 2] if self.tilt_correction_dockwidget.auto_origin else [0, 1, 2, 3]
        for vec in vecs_to_check:
            vecs_valid = [support_vecs[vec][dim[0]].is_valid for dim in dim_idxs]
            all_vecs_valid = np.all(vecs_valid) and all_vecs_valid

        self.toggle_tilt_correction(False)
        self._scanning_logic().configure_tilt_correction(None, None)
        self._mw.action_toggle_tilt_correction.setEnabled(False)

        if all_vecs_valid:
            shift_vec = support_vecs_val[-1]
            if self.tilt_correction_dockwidget.auto_origin:
                shift_vec = None

            support_vecs = support_vecs_val[:-1]
            self._scanning_logic().configure_tilt_correction(support_vecs, shift_vec)
            self._mw.action_toggle_tilt_correction.setEnabled(True)

    def toggle_tilt_correction(self, state):
        if type(state) != bool:
            raise ValueError

        self._scanning_logic().toggle_tilt_correction(state)
        self._mw.action_toggle_tilt_correction.set_state(state)
        self._mw.action_toggle_tilt_correction.setChecked(state)


class ToggleIconsQAction(QAction):
    def __init__(self, parent, text, icon_on, icon_off):
        self.icon_on = QtGui.QIcon(QtGui.QPixmap(icon_on))
        self.icon_off = QtGui.QIcon(QtGui.QPixmap(icon_off))
        super().__init__(self.icon_off, text, parent, checkable=True)

        self.triggered.connect(self.set_state, QtCore.Qt.QueuedConnection)

    def set_state(self, enabled):
        if enabled:
            self.setIcon(self.icon_on)
        else:
            self.setIcon(self.icon_off)
