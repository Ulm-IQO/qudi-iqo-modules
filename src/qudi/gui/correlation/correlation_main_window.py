# -*- coding: utf-8 -*-
"""
Main window widget for the g(2) correlation measurement GUI.

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

__all__ = ['CHANNEL_CHOICES', 'CHANNEL_TRIGGER_LEVEL_RANGE_MV', 'BoundedSpinBox',
          'CorrelationSettingsDockWidget', 'ChannelTriggerDockWidget', 'CorrelationMainWindow']

from pyqtgraph import PlotWidget
from PySide6 import QtCore, QtWidgets, QtGui

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox

# Display label -> snAPI channel number (1..4 = hardware inputs). Shared between the start/stop
# channel selectors and the per-channel trigger table so both always agree. Sync (channel 0) is
# deliberately not offered here -- its threshold stays fixed via the fast counter's own
# sync_trigger_level_mv/sync_trigger_edge config options, not edited from this GUI.
CHANNEL_CHOICES = [('Channel 1', 1), ('Channel 2', 2), ('Channel 3', 3), ('Channel 4', 4)]


def _label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight)
    return lbl


def _channel_combobox() -> QtWidgets.QComboBox:
    box = QtWidgets.QComboBox()
    for text, channel in CHANNEL_CHOICES:
        box.addItem(text, userData=channel)
    return box


def _edge_combobox() -> QtWidgets.QComboBox:
    box = QtWidgets.QComboBox()
    box.addItem('Falling', userData=0)
    box.addItem('Rising', userData=1)
    return box


class BoundedSpinBox(QtWidgets.QSpinBox):
    """ A QSpinBox that reverts to the last accepted value when the user types something outside
    [minimum, maximum], instead of Qt's normal behaviour of silently clamping to the nearest
    boundary. Qt's own range is deliberately left wide open so typing is never interrupted/
    pre-clamped mid-edit; [minimum, maximum] is enforced here once editing finishes. Arrow-key/
    mouse-wheel stepping still clamps to the boundary immediately, since that is normal, expected
    behaviour for incremental stepping (only free-form typing gets the "revert" treatment).
    """

    def __init__(self, minimum: int, maximum: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._minimum = minimum
        self._maximum = maximum
        super().setRange(-2_000_000_000, 2_000_000_000)
        self._last_valid = 0
        self.editingFinished.connect(self._revert_if_out_of_range)

    def stepBy(self, steps: int) -> None:
        super().stepBy(steps)
        if self.value() > self._maximum:
            super().setValue(self._maximum)
        elif self.value() < self._minimum:
            super().setValue(self._minimum)
        self._last_valid = self.value()

    def _revert_if_out_of_range(self):
        if self._minimum <= self.value() <= self._maximum:
            self._last_valid = self.value()
        else:
            super().setValue(self._last_valid)

    def setValue(self, value: int) -> None:
        """ Programmatic sets (e.g. a real hardware read-back) are trusted outright. """
        super().setValue(int(value))
        self._last_valid = self.value()


class CorrelationSettingsDockWidget(QtWidgets.QDockWidget):
    """ Dock widget holding the g(2) measurement parameters. """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, objectName='g(2) settings', **kwargs)
        self.setWindowTitle('g(2) settings')

        self.start_channel_combobox = _channel_combobox()
        self.start_channel_combobox.setToolTip(
            'Same as stop channel -> autocorrelation. Different -> cross-correlation.'
        )
        self.start_channel_combobox.setCurrentIndex(0)  # Channel 1

        self.stop_channel_combobox = _channel_combobox()
        self.stop_channel_combobox.setToolTip(
            'Same as start channel -> autocorrelation. Different -> cross-correlation.'
        )
        self.stop_channel_combobox.setCurrentIndex(0)  # Channel 1

        self.window_size_spinbox = ScienDSpinBox()
        self.window_size_spinbox.setToolTip('Half-width of the correlation window.')
        self.window_size_spinbox.setSuffix('s')
        self.window_size_spinbox.setRange(0, float('inf'))
        self.window_size_spinbox.setValue(1.0e-6)
        self.window_size_spinbox.setMinimumWidth(100)

        # Populated by CorrelationGui on activation (via set_bin_width_choices below) from the
        # fast counter's own achievable bin width list -- constructing it here with no hardware
        # connection available yet would be guesswork.
        self.bin_width_combobox = QtWidgets.QComboBox()
        self.bin_width_combobox.setToolTip(
            'Auto uses the device base resolution. Other entries come from the fast counter\'s '
            'own achievable bin width list.'
        )
        self.bin_width_combobox.setMinimumWidth(100)

        self.normalization_checkbox = QtWidgets.QCheckBox('Normalize g(2)')
        self.normalization_checkbox.setToolTip(
            'Unchecked counts raw coincidences instead of normalizing to g(2).'
        )
        self.normalization_checkbox.setChecked(True)

        self.acquisition_time_spinbox = ScienDSpinBox()
        self.acquisition_time_spinbox.setToolTip('0 = run until stopped manually.')
        self.acquisition_time_spinbox.setSuffix('s')
        self.acquisition_time_spinbox.setRange(0, float('inf'))
        self.acquisition_time_spinbox.setValue(0.0)
        self.acquisition_time_spinbox.setMinimumWidth(100)

        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        widget.setLayout(layout)
        self.setWidget(widget)

        layout.addWidget(_label('Start channel:'), 0, 0)
        layout.addWidget(self.start_channel_combobox, 0, 1)
        layout.addWidget(_label('Stop channel:'), 0, 2)
        layout.addWidget(self.stop_channel_combobox, 0, 3)
        layout.addWidget(_label('Window size:'), 0, 4)
        layout.addWidget(self.window_size_spinbox, 0, 5)

        layout.addWidget(_label('Bin width:'), 1, 0)
        layout.addWidget(self.bin_width_combobox, 1, 1, 1, 2)
        layout.addWidget(self.normalization_checkbox, 1, 4)

        layout.addWidget(_label('Acquisition time:'), 2, 0)
        layout.addWidget(self.acquisition_time_spinbox, 2, 1)

    def set_bin_width_choices(self, choices_s):
        """ (Re)populate the bin width dropdown. 'Auto' (None) is always the first entry. """
        current = self.bin_width_combobox.currentData() if self.bin_width_combobox.count() else None
        self.bin_width_combobox.blockSignals(True)
        self.bin_width_combobox.clear()
        self.bin_width_combobox.addItem('Auto (device base resolution)', userData=None)
        for value in choices_s:
            self.bin_width_combobox.addItem(f'{value:.6g} s', userData=float(value))
        index = self.bin_width_combobox.findData(current)
        self.bin_width_combobox.setCurrentIndex(index if index >= 0 else 0)
        self.bin_width_combobox.blockSignals(False)


# Safe discriminator level range for this hardware (PicoHarp-family input front end).
CHANNEL_TRIGGER_LEVEL_RANGE_MV = (-200, 200)


class ChannelTriggerDockWidget(QtWidgets.QDockWidget):
    """ Dock widget holding one discriminator level/edge row per channel (inputs 1-4).

    Plain per-row widgets in a grid layout, deliberately not a QTableWidget with
    setCellWidget(): QAbstractItemView's own click/selection handling only reliably forwards
    focus to a cell widget's buttons (the spin box arrows), not its line edit -- so direct typing
    silently didn't work, only the arrow buttons did. Standalone widgets in a layout have
    completely normal keyboard focus behaviour.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, objectName='Channel triggers', **kwargs)
        self.setWindowTitle('Channel triggers')

        # level_spinboxes/edge_comboboxes indexed by snAPI channel number (1..4), not row position.
        self.level_spinboxes: dict[int, BoundedSpinBox] = {}
        self.edge_comboboxes: dict[int, QtWidgets.QComboBox] = {}

        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        widget.setLayout(layout)
        self.setWidget(widget)

        layout.addWidget(_label('Channel'), 0, 0)
        layout.addWidget(_label('Level'), 0, 1)
        layout.addWidget(_label('Edge'), 0, 2)

        minimum, maximum = CHANNEL_TRIGGER_LEVEL_RANGE_MV
        for row, (text, channel) in enumerate(CHANNEL_CHOICES, start=1):
            layout.addWidget(_label(text + ':'), row, 0)

            level_spinbox = BoundedSpinBox(minimum, maximum)
            level_spinbox.setSuffix(' mV')
            level_spinbox.setSingleStep(10)
            level_spinbox.setToolTip(f'Safe range for this hardware: {minimum} to {maximum} mV. '
                                     f'An out-of-range typed value reverts to the last accepted one.')
            level_spinbox.setMinimumWidth(90)
            layout.addWidget(level_spinbox, row, 1)
            self.level_spinboxes[channel] = level_spinbox

            edge_combobox = _edge_combobox()
            layout.addWidget(edge_combobox, row, 2)
            self.edge_comboboxes[channel] = edge_combobox

        layout.setColumnStretch(3, 1)


class CorrelationMainWindow(QtWidgets.QMainWindow):
    """ Main window for the g(2) correlation GUI. """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setWindowTitle('qudi: g(2) Correlation')
        self.setDockNestingEnabled(True)

        # Actions
        self.start_stop_action = QtGui.QAction('Start', self)
        self.start_stop_action.setCheckable(True)
        self.start_stop_action.setToolTip('Start/Stop the g(2) measurement.')
        self.save_action = QtGui.QAction('Save', self)
        self.save_action.setToolTip('Save the current tau/g(2) data to a file.')
        self.show_settings_action = QtGui.QAction('g(2) settings', self)
        self.show_settings_action.setCheckable(True)
        self.show_settings_action.setToolTip('Show g(2) measurement settings.')
        self.show_triggers_action = QtGui.QAction('Channel triggers', self)
        self.show_triggers_action.setCheckable(True)
        self.show_triggers_action.setToolTip('Show per-channel discriminator trigger settings.')
        self.close_action = QtGui.QAction('Close', self)
        self.close_action.setShortcut(QtGui.QKeySequence(QtGui.Qt.Key.Key_Control | QtGui.Qt.Key.Key_Q))

        # Toolbar
        self.toolbar = QtWidgets.QToolBar('g(2) controls')
        self.toolbar.setObjectName('g(2) controls')
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.toolbar.addAction(self.start_stop_action)
        self.toolbar.addAction(self.save_action)

        # Menu bar
        menubar = QtWidgets.QMenuBar()
        menu = menubar.addMenu('File')
        menu.addAction(self.start_stop_action)
        menu.addAction(self.save_action)
        menu.addSeparator()
        menu.addAction(self.close_action)
        menu = menubar.addMenu('View')
        menu.addAction(self.show_settings_action)
        menu.addAction(self.show_triggers_action)
        self.setMenuBar(menubar)

        # Central widget: status label + plot
        self.status_label = QtWidgets.QLabel('Idle')
        self.g2_plot_widget = PlotWidget()
        self.g2_plot_widget.setLabel('bottom', 'tau', units='s')
        self.g2_plot_widget.setLabel('left', 'g(2)')

        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        layout.addWidget(self.status_label)
        layout.addWidget(self.g2_plot_widget)

        # Dock widgets
        self.settings_dockwidget = CorrelationSettingsDockWidget()
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.settings_dockwidget)
        self.trigger_dockwidget = ChannelTriggerDockWidget()
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.trigger_dockwidget)

        # Show/hide wiring
        self.show_settings_action.triggered[bool].connect(self.settings_dockwidget.setVisible)
        self.settings_dockwidget.visibilityChanged.connect(self.show_settings_action.setChecked)
        self.show_triggers_action.triggered[bool].connect(self.trigger_dockwidget.setVisible)
        self.trigger_dockwidget.visibilityChanged.connect(self.show_triggers_action.setChecked)
        self.close_action.triggered.connect(self.close)
