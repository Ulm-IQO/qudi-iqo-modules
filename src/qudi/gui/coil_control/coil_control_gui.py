# -*- coding: utf-8 -*-
"""
This file contains the qudi coil control GUI module.

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

from PySide6 import QtWidgets, QtCore, QtGui

from qudi.core.connector import Connector
from qudi.core.module import GuiBase

from qudi.logic.coil_control_logic import CoilControlLogic
from qudi.gui.switch.switch_state_widgets import ToggleSwitchWidget


class InstantStepDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """ A QDoubleSpinBox that commits its value differently depending on HOW
    it was changed:

        - Clicking the up/down arrow buttons, using the mouse scroll wheel,
          or pressing the Up/Down arrow KEYS all internally call Qt's
          stepBy() method. Each such step is a single, complete, deliberate
          action -- there is no "intermediate" state -- so sigValueCommitted
          is emitted immediately after every step.

        - Typing digits directly does NOT call stepBy() at all. Those
          keystrokes only update the widget's displayed text; the actual
          value is only considered "committed" once editingFinished fires
          (Enter/Return pressed, or the widget loses focus). This avoids
          firing a hardware write for every intermediate value while typing
          a number character-by-character (e.g. "-5" then "-5.5").

    Without this, a plain QDoubleSpinBox connected only via editingFinished
    would mean the up/down step buttons and scroll wheel silently do
    nothing to the underlying hardware (they don't cause focus loss or an
    Enter keypress) -- this was the case in the original version of this
    GUI. Connecting only via valueChanged instead would fix the arrows but
    introduce spurious intermediate hardware writes while typing a new
    number. This class gives instant on-the-fly updates for discrete
    stepping, while still committing typed input only once, on Enter or
    focus loss.
    """

    sigValueCommitted = QtCore.Signal(float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.editingFinished.connect(self._emit_committed)

    def stepBy(self, steps):
        super().stepBy(steps)
        self._emit_committed()

    def _emit_committed(self):
        self.sigValueCommitted.emit(self.value())


class CoilControlMainWindow(QtWidgets.QMainWindow):
    """ Main window for the CoilControlGui module. """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('qudi: Coil Control')

        self.main_layout = QtWidgets.QGridLayout()
        widget = QtWidgets.QWidget()
        widget.setLayout(self.main_layout)
        self.setCentralWidget(widget)

        menu_bar = QtWidgets.QMenuBar()
        self.setMenuBar(menu_bar)

        menu = menu_bar.addMenu('Menu')
        self.action_close = QtGui.QAction('Close Window')
        self.action_close.setCheckable(False)
        menu.addAction(self.action_close)

        menu = menu_bar.addMenu('View')
        self.action_periodic_state_check = QtGui.QAction('Periodic State Checking')
        self.action_periodic_state_check.setCheckable(True)
        menu.addAction(self.action_periodic_state_check)

        self.action_close.triggered.connect(self.close)


class CoilControlGui(GuiBase):
    """
    A graphical interface to set voltage, current, and output state for a set of
    magnet coil axes (e.g. X/Y/Z).

    Example config for copy-paste:

        coil_control_gui:
            module.Class: 'coil_control.coil_control_gui.CoilControlGui'
            connect:
                coil_control_logic: 'coil_control_logic'
    """

    coil_control_logic = Connector(interface=CoilControlLogic)

    sigVoltageSet = QtCore.Signal(str, float)
    sigCurrentSet = QtCore.Signal(str, float)
    sigOutputSet = QtCore.Signal(str, bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._widgets = dict()

    def on_activate(self):
        """ Create all UI objects and show the window. """
        self._mw = CoilControlMainWindow()
        self._widgets = dict()

        self._populate_axes()

        self.sigVoltageSet.connect(
            self.coil_control_logic().set_voltage, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.sigCurrentSet.connect(
            self.coil_control_logic().set_current, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.sigOutputSet.connect(
            self.coil_control_logic().set_output, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self._mw.action_periodic_state_check.toggled.connect(
            self.coil_control_logic().toggle_watchdog, QtCore.Qt.ConnectionType.QueuedConnection
        )

        self.coil_control_logic().sigVoltageChanged.connect(
            self._voltage_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.coil_control_logic().sigCurrentChanged.connect(
            self._current_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.coil_control_logic().sigOutputChanged.connect(
            self._output_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.coil_control_logic().sigWatchdogToggled.connect(
            self._watchdog_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )

        self._restore_window_geometry(self._mw)

        self._watchdog_updated(self.coil_control_logic().watchdog_active)
        self.show()

    def on_deactivate(self):
        """ Hide window, empty the GUI, and disconnect signals. """
        self.coil_control_logic().sigVoltageChanged.disconnect(self._voltage_updated)
        self.coil_control_logic().sigCurrentChanged.disconnect(self._current_updated)
        self.coil_control_logic().sigOutputChanged.disconnect(self._output_updated)
        self.coil_control_logic().sigWatchdogToggled.disconnect(self._watchdog_updated)
        self._mw.action_periodic_state_check.toggled.disconnect()
        self.sigVoltageSet.disconnect()
        self.sigCurrentSet.disconnect()
        self.sigOutputSet.disconnect()

        self._save_window_geometry(self._mw)
        self._delete_axes()
        self._mw.close()

    def show(self):
        """ Make sure that the window is visible and at the top. """
        self._mw.show()

    @staticmethod
    def _safe(value, fallback=0.0):
        """ Fallback to a default if a hardware query returned None (error case). """
        return fallback if value is None else value

    def _populate_axes(self):
        """ Dynamically build one row per axis: label, voltage spinbox,
        current spinbox, output toggle switch.

        Voltage and current spinboxes use InstantStepDoubleSpinBox (see class
        above) rather than a plain QDoubleSpinBox, connected via its
        sigValueCommitted signal instead of editingFinished: editingFinished
        alone means the up/down step buttons and scroll wheel silently do
        nothing (no focus loss or Enter keypress occurs from those actions).
        sigValueCommitted fires immediately for discrete step actions
        (arrows, wheel, arrow keys), but only on Enter/focus-loss for typed
        input -- giving instant "on the fly" behavior for stepping while
        avoiding spurious intermediate hardware writes while typing a new
        number (e.g. typing "-5.5" would otherwise briefly write "-5" first).

        Column/row stretch factors are set at the end so the widgets actually
        grow to fill the window on resize -- mirrors the same pattern used in
        SwitchGui._populate_switches(), which sets setColumnStretch() after
        placing widgets. Without this, QGridLayout has no information about
        which columns/rows should absorb extra space, and everything stays
        pinned at its minimum size regardless of window size.
        """
        logic = self.coil_control_logic()
        self._widgets = dict()

        header_font = QtGui.QFont()
        header_font.setBold(True)

        headers = ['Axis', 'Voltage (V)', 'Current (A)', 'Output']
        for col, text in enumerate(headers):
            label = QtWidgets.QLabel(text)
            label.setFont(header_font)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._mw.main_layout.addWidget(label, 0, col)

        for row, axis in enumerate(logic.axes, start=1):
            axis_label = QtWidgets.QLabel(axis.upper())
            font = axis_label.font()
            font.setBold(True)
            font.setPointSize(12)
            axis_label.setFont(font)
            axis_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            voltage_min, voltage_max = logic.get_voltage_limits(axis)
            voltage_spinbox = InstantStepDoubleSpinBox()
            voltage_spinbox.setRange(voltage_min, voltage_max)
            voltage_spinbox.setDecimals(3)
            voltage_spinbox.setSingleStep(0.1)
            voltage_spinbox.setSuffix(' V')
            voltage_spinbox.setValue(self._safe(logic.get_voltage(axis)))
            voltage_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
            voltage_spinbox.sigValueCommitted.connect(
                self.__get_voltage_update_func(axis)
            )

            current_min, current_max = logic.get_current_limits(axis)
            current_spinbox = InstantStepDoubleSpinBox()
            current_spinbox.setRange(current_min, current_max)
            current_spinbox.setDecimals(3)
            current_spinbox.setSingleStep(0.1)
            current_spinbox.setSuffix(' A')
            current_spinbox.setValue(self._safe(logic.get_current(axis)))
            current_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
            current_spinbox.sigValueCommitted.connect(
                self.__get_current_update_func(axis)
            )

            output_switch = ToggleSwitchWidget(switch_states=('Off', 'On'),
                                               thumb_track_ratio=0.9,
                                               scale_text_in_switch=True,
                                               text_inside_switch=True)
            output_switch.set_state('On' if self._safe(logic.get_output_state(axis), False) else 'Off')
            output_switch.sigStateChanged.connect(self.__get_output_update_func(axis))

            self._widgets[axis] = {
                'label': axis_label,
                'voltage': voltage_spinbox,
                'current': current_spinbox,
                'output': output_switch,
            }

            self._mw.main_layout.addWidget(axis_label, row, 0)
            self._mw.main_layout.addWidget(voltage_spinbox, row, 1)
            self._mw.main_layout.addWidget(current_spinbox, row, 2)
            self._mw.main_layout.addWidget(output_switch, row, 3)

        # Column stretch: label column stays fixed-width, the other three
        # columns grow to absorb extra horizontal space on window resize.
        self._mw.main_layout.setColumnStretch(0, 0)
        self._mw.main_layout.setColumnStretch(1, 1)
        self._mw.main_layout.setColumnStretch(2, 1)
        self._mw.main_layout.setColumnStretch(3, 1)

        # Row stretch: header row stays fixed-height, each axis row gets
        # equal stretch so vertical resizing distributes evenly across rows
        # rather than leaving all extra space as blank area below the last row.
        self._mw.main_layout.setRowStretch(0, 0)
        for row in range(1, len(logic.axes) + 1):
            self._mw.main_layout.setRowStretch(row, 1)

    def _delete_axes(self):
        """ Delete all row widgets from the main layout. """
        self._widgets.clear()
        while True:
            item = self._mw.main_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                try:
                    widget.sigStateChanged.disconnect()
                except AttributeError:
                    pass
                try:
                    widget.sigValueCommitted.disconnect()
                except AttributeError:
                    pass
                widget.setParent(None)
                widget.deleteLater()

    @QtCore.Slot(str, float)
    def _voltage_updated(self, axis, value):
        """ Reflect a voltage change (from hardware or watchdog) in the spinbox. """
        widget = self._widgets.get(axis)
        if widget is None:
            return
        spinbox = widget['voltage']
        spinbox.blockSignals(True)
        spinbox.setValue(value)
        spinbox.blockSignals(False)

    @QtCore.Slot(str, float)
    def _current_updated(self, axis, value):
        """ Reflect a current change (from hardware or watchdog) in the spinbox. """
        widget = self._widgets.get(axis)
        if widget is None:
            return
        spinbox = widget['current']
        spinbox.blockSignals(True)
        spinbox.setValue(value)
        spinbox.blockSignals(False)

    @QtCore.Slot(str, bool)
    def _output_updated(self, axis, state):
        """ Reflect an output state change (from hardware or watchdog) in the toggle. """
        widget = self._widgets.get(axis)
        if widget is None:
            return
        widget['output'].set_state('On' if state else 'Off')

    @QtCore.Slot(bool)
    def _watchdog_updated(self, enabled):
        """ Update the menu action to match the logic's actual watchdog state. """
        if enabled != self._mw.action_periodic_state_check.isChecked():
            self._mw.action_periodic_state_check.blockSignals(True)
            self._mw.action_periodic_state_check.setChecked(enabled)
            self._mw.action_periodic_state_check.blockSignals(False)

    def __get_voltage_update_func(self, axis):
        def update_func(value):
            self.sigVoltageSet.emit(axis, value)
        return update_func

    def __get_current_update_func(self, axis):
        def update_func(value):
            self.sigCurrentSet.emit(axis, value)
        return update_func

    def __get_output_update_func(self, axis):
        def update_func(state):
            self.sigOutputSet.emit(axis, state == 'On')
        return update_func