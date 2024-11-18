# -*- coding: utf-8 -*-
"""
Contains the QMainWindow for the laser scanning toolchain GUI.

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

__all__ = ['LaserScanningMainWindow']

from typing import Optional
from PySide2 import QtCore, QtWidgets

from qudi.util.datafitting import FitConfigurationsModel, FitContainer
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.interface.scannable_laser_interface import ScannableLaserConstraints
from qudi.gui.laserscanning.widgets.histogram_settings import HistogramSettingsDockWidget
from qudi.gui.laserscanning.widgets.scan_settings import LaserScanSettingsDockWidget
from qudi.gui.laserscanning.widgets.actions import LaserScanningActions
from qudi.gui.laserscanning.widgets.menubar import LaserScanningMenuBar
from qudi.gui.laserscanning.widgets.toolbar import LaserScanningToolBar
from qudi.gui.laserscanning.widgets.value_display import LaserValueDisplayWidget
from qudi.gui.laserscanning.widgets.plots import HistogramPlotWidget, ScatterPlotDockWidget
from qudi.gui.laserscanning.widgets.stabilization_control import LaserStabilizationDockWidget
from qudi.gui.laserscanning.widgets.fit import FitControlDockWidget


class _CentralWidget(QtWidgets.QWidget):
    """ Central widget for LaserScanningMainWindow """

    histogram_plot: HistogramPlotWidget
    current_laser_label: LaserValueDisplayWidget

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self.histogram_plot = HistogramPlotWidget()
        self.current_laser_display = LaserValueDisplayWidget()

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.current_laser_display)
        layout.addWidget(self.histogram_plot)
        layout.setStretch(1, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.histogram_plot.setMinimumHeight(200)  # Get rid of this if possible


class LaserScanningMainWindow(QtWidgets.QMainWindow):
    """ Create the main window for laser scanning toolchain """

    def __init__(self,
                 fit_config_model: FitConfigurationsModel,
                 fit_container: FitContainer,
                 laser_constraints: Optional[ScannableLaserConstraints] = None):
        super().__init__()

        self.setWindowTitle('qudi: Laser Scanning')

        # Create QActions
        self.gui_actions = LaserScanningActions()

        # Create menu bar and add actions
        self.setMenuBar(LaserScanningMenuBar(self.gui_actions))

        # Create toolbar and add actions
        self.toolbar = LaserScanningToolBar(self.gui_actions)
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.toolbar)

        # Create central widget with wavelength/freq display and histogram plot
        widget = _CentralWidget()
        self.histogram_plot = widget.histogram_plot
        self.current_laser_display = widget.current_laser_display
        self.setCentralWidget(widget)

        # Create dockwidgets and add them
        self.scatter_dock = ScatterPlotDockWidget(f'{self.windowTitle()} - Scan Data')
        self.scatter_plot = self.scatter_dock.scatter_plot
        self.histogram_settings_dock = HistogramSettingsDockWidget(
            f'{self.windowTitle()} - Histogram Settings'
        )
        self.histogram_settings = self.histogram_settings_dock.settings_widget
        self.fit_dock = FitControlDockWidget(f'{self.windowTitle()} - Fit',
                                             fit_container=fit_container)
        self.fit_control = self.fit_dock.fit_control
        if laser_constraints is None:
            self.laser_scan_settings_dock = None
            self.laser_scan_settings = None
            self.laser_stabilization_dock = None
            self.laser_stabilization = None
        else:
            self.laser_scan_settings_dock = LaserScanSettingsDockWidget(
                f'{self.windowTitle()} - Scan Settings',
                constraints=laser_constraints
            )
            self.laser_scan_settings = self.laser_scan_settings_dock.settings_widget
            self.laser_stabilization_dock = LaserStabilizationDockWidget(
                f'{self.windowTitle()} - Stabilization',
                unit=laser_constraints.unit,
                constraint=laser_constraints.value
            )
            self.laser_stabilization = self.laser_stabilization_dock.control_widget

        # Create dialog for fit settings and link it to the fit widget
        self.fit_config_dialog = FitConfigurationDialog(parent=self,
                                                        fit_config_model=fit_config_model)

        # Connect some actions
        self.gui_actions.action_close.triggered.connect(self.close)
        self.gui_actions.action_restore_view.triggered.connect(self.restore_default)
        self.gui_actions.action_show_fit_configuration.triggered.connect(
            self.fit_config_dialog.show
        )
        self.restore_default()

    def restore_default(self) -> None:
        # Show all hidden dock widgets
        self.scatter_dock.show()
        self.fit_dock.show()
        self.histogram_settings_dock.show()
        if self.laser_scan_settings_dock is not None:
            self.laser_scan_settings_dock.show()
        # re-dock floating dock widgets
        self.scatter_dock.setFloating(False)
        self.fit_dock.setFloating(False)
        self.histogram_settings_dock.setFloating(False)
        if self.laser_scan_settings_dock is not None:
            self.laser_scan_settings_dock.setFloating(False)
        # Arrange dock widgets
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.fit_dock)
        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.histogram_settings_dock)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.scatter_dock)
        if self.laser_scan_settings_dock is not None:
            self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.laser_stabilization_dock)
            self.splitDockWidget(self.histogram_settings_dock,
                                 self.laser_stabilization_dock,
                                 QtCore.Qt.Horizontal)
            self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.laser_scan_settings_dock)
            self.splitDockWidget(self.fit_dock, self.laser_scan_settings_dock, QtCore.Qt.Vertical)
            height = self.height() - (2 * self.histogram_settings_dock.height())
            self.resizeDocks([self.fit_dock, self.laser_scan_settings_dock, self.scatter_dock],
                             [height // 2, 1, height//2],
                             QtCore.Qt.Vertical)
