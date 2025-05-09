# -*- coding: utf-8 -*-

"""
This file contains the qudi time series streaming gui main window QWidget.

Copyright (c) 2023, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['TraceSettingsDockWidget', 'TimeSeriesGuiMainWindow']

import os
from pyqtgraph import PlotWidget
from PySide2 import QtCore, QtWidgets, QtGui

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.util.paths import get_artwork_dir


class TraceSettingsDockWidget(QtWidgets.QDockWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('Trace settings')

        # Create content widgets
        self.trace_length_spinbox = ScienDSpinBox()
        self.trace_length_spinbox.setToolTip('Length of the time window showing the data trace.')
        self.trace_length_spinbox.setSuffix('s')
        self.trace_length_spinbox.setRange(0, float('inf'))
        self.trace_length_spinbox.setValue('6.0')
        self.trace_length_spinbox.setMinimumWidth(75)
        self.data_rate_spinbox = ScienDSpinBox()
        self.data_rate_spinbox.setToolTip(
            'Rate at which data points occur within the data trace.\n'
            'The physical sampling rate is this value times the oversampling factor.'
        )
        self.data_rate_spinbox.setSuffix('Hz')
        self.data_rate_spinbox.setRange(0, float('inf'))
        self.data_rate_spinbox.setValue('50.0')
        self.data_rate_spinbox.setMinimumWidth(75)
        self.oversampling_spinbox = QtWidgets.QSpinBox()
        self.oversampling_spinbox.setToolTip(
            'If bigger than 1, this number of samples is acquired for each period of data rate.\n'
            'The average over these samples is giving the value of the data point.\n'
            'In other words, the physical sampling rate is oversampling factor times data rate.'
        )
        self.oversampling_spinbox.setRange(1, 10000)
        self.oversampling_spinbox.setValue(1)
        self.oversampling_spinbox.setMinimumWidth(50)
        self.moving_average_spinbox = QtWidgets.QSpinBox()
        self.moving_average_spinbox.setToolTip(
            'The window size in samples of the moving average for each data trace.\n'
            'Must be an odd number to ensure perfect trace data alignment.'
        )
        self.moving_average_spinbox.setRange(1, 99)
        self.moving_average_spinbox.setSingleStep(2)
        self.moving_average_spinbox.setValue(3)
        self.moving_average_spinbox.setMinimumWidth(50)

        # Put contents into layout
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        widget.setLayout(layout)
        self.setWidget(widget)
        label = QtWidgets.QLabel('Trace length:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        layout.addWidget(label)
        layout.addWidget(self.trace_length_spinbox)
        label = QtWidgets.QLabel('Data rate:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        layout.addWidget(label)
        layout.addWidget(self.data_rate_spinbox)
        label = QtWidgets.QLabel('Oversampling factor:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        layout.addWidget(label)
        layout.addWidget(self.oversampling_spinbox)
        label = QtWidgets.QLabel('Moving average width:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        layout.addWidget(label)
        layout.addWidget(self.moving_average_spinbox)


class TimeSeriesGuiMainWindow(QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setWindowTitle('qudi: Time Series Viewer')
        self.setDockNestingEnabled(True)

        # Create QActions
        icons_dir = os.path.join(get_artwork_dir(), 'icons')
        icon = QtGui.QIcon(os.path.join(icons_dir, 'start-counter'))
        icon.addFile(os.path.join(icons_dir, 'stop-counter'), state=QtGui.QIcon.On)
        self.toggle_trace_action = QtWidgets.QAction(icon, 'Start trace', self)
        self.toggle_trace_action.setCheckable(True)
        self.toggle_trace_action.setToolTip('Start/Stop continuous reading of the data trace.')
        icon = QtGui.QIcon(os.path.join(icons_dir, 'record-counter'))
        icon.addFile(os.path.join(icons_dir, 'stop-record-counter'), state=QtGui.QIcon.On)
        self.record_trace_action = QtWidgets.QAction(icon, 'Start recording', self)
        self.record_trace_action.setCheckable(True)
        self.record_trace_action.setToolTip(
            'Start/Stop trace recorder. This will continuously accumulate trace data and save it '
            'to file once it is stopped.'
        )
        icon = QtGui.QIcon(os.path.join(icons_dir, 'camera-photo'))
        self.snapshot_trace_action = QtWidgets.QAction(icon, 'Take snapshot', self)
        self.snapshot_trace_action.setCheckable(False)
        self.snapshot_trace_action.setToolTip(
            'Take a snapshot of only the currently shown data trace and save it to file.'
        )
        icon = QtGui.QIcon(os.path.join(icons_dir, 'media-playback-pause'))
        icon.addFile(os.path.join(icons_dir, 'media-playback-start'), state=QtGui.QIcon.On)
        self.freeze_y_axis_action = QtWidgets.QAction(icon, 'Freeze y axis', self)
        self.freeze_y_axis_action.setCheckable(True)
        self.freeze_y_axis_action.setToolTip(
            'Freeze y axis range to current value.'
        )
        icon = QtGui.QIcon(os.path.join(icons_dir, 'configure'))
        self.trace_view_selection_action = QtWidgets.QAction(icon, 'Trace view selection', self)
        self.trace_view_selection_action.setCheckable(False)
        self.trace_view_selection_action.setToolTip(
            'Opens the trace view selection dialog to configure the data traces to show.'
        )
        icon = QtGui.QIcon(os.path.join(icons_dir, 'configure'))
        self.channel_settings_action = QtWidgets.QAction(icon, 'Channel settings', self)
        self.channel_settings_action.setCheckable(False)
        self.channel_settings_action.setToolTip(
            'Opens the channel settings dialog to configure the active data channels.'
        )
        self.show_trace_settings_action = QtWidgets.QAction('Trace settings', self)
        self.show_trace_settings_action.setCheckable(True)
        self.show_trace_settings_action.setToolTip('Show data trace settings.')
        self.show_toolbar_action = QtWidgets.QAction('Toolbar', self)
        self.show_toolbar_action.setCheckable(True)
        self.show_toolbar_action.setToolTip('Show the trace control toolbar.')
        self.restore_default_view_action = QtWidgets.QAction('Restore default', self)
        self.restore_default_view_action.setCheckable(False)
        self.restore_default_view_action.setToolTip('Restore the default view.')
        icon = QtGui.QIcon(os.path.join(icons_dir, 'application-exit'))
        self.close_action = QtWidgets.QAction(icon, 'Close', self)
        self.close_action.setCheckable(False)
        self.close_action.setToolTip('Close')
        self.close_action.setShortcut(QtGui.QKeySequence(QtGui.Qt.CTRL + QtGui.Qt.Key_Q))

        # Create toolbar
        self.toolbar = QtWidgets.QToolBar('Trace controls')
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.toolbar)
        self.toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.toolbar.addAction(self.toggle_trace_action)
        self.toolbar.addAction(self.record_trace_action)
        self.toolbar.addAction(self.snapshot_trace_action)
        self.toolbar.addAction(self.freeze_y_axis_action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.trace_view_selection_action)
        self.toolbar.addAction(self.channel_settings_action)

        # Create menubar
        menubar = QtWidgets.QMenuBar()
        menu = menubar.addMenu('File')
        menu.addAction(self.toggle_trace_action)
        menu.addAction(self.record_trace_action)
        menu.addAction(self.snapshot_trace_action)
        menu.addAction(self.freeze_y_axis_action)
        menu.addSeparator()
        menu.addAction(self.close_action)
        menu = menubar.addMenu('View')
        menu.addAction(self.show_trace_settings_action)
        menu.addAction(self.show_toolbar_action)
        menu.addSeparator()
        menu.addAction(self.restore_default_view_action)
        menu = menubar.addMenu('Settings')
        menu.addAction(self.channel_settings_action)
        menu.addAction(self.trace_view_selection_action)
        self.setMenuBar(menubar)

        # Create content widgets
        self.trace_plot_widget = PlotWidget()
        self.current_value_label = QtWidgets.QLabel('0')
        font = self.current_value_label.font()
        font.setBold(True)
        font.setPointSize(60)
        self.current_value_label.setFont(font)
        self.current_value_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        self.current_value_combobox = QtWidgets.QComboBox()
        self.current_value_combobox.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.current_value_combobox.setMinimumContentsLength(20)
        self.current_value_combobox.setMaxVisibleItems(10)

        # Put contents into layout
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        layout.setColumnStretch(0, 1)
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        label = QtWidgets.QLabel('Current value channel:')
        label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        label.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(label, 0, 0)
        layout.addWidget(self.current_value_combobox, 0, 1)
        layout.addWidget(self.current_value_label, 1, 0, 1, 2)
        layout.addWidget(self.trace_plot_widget, 2, 0, 1, 2)

        # Create and add trace settings QDockWidget
        self.settings_dockwidget = TraceSettingsDockWidget()
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.settings_dockwidget)

        # Connect some show/hide signals/actions
        self.show_toolbar_action.triggered[bool].connect(self.toolbar.setVisible)
        self.toolbar.visibilityChanged.connect(self.show_toolbar_action.setChecked)
        self.show_trace_settings_action.triggered[bool].connect(self.settings_dockwidget.setVisible)
        self.settings_dockwidget.visibilityChanged.connect(
            self.show_trace_settings_action.setChecked
        )
        self.close_action.triggered.connect(self.close)
