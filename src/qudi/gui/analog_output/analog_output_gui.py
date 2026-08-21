# -*- coding: utf-8 -*-
"""
This file contains the qudi analog output GUI module.

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

from qudi.logic.analog_output_logic import AnalogOutputLogic
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

    This gives instant on-the-fly updates for discrete stepping, while
    avoiding spurious intermediate hardware writes while typing.
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


class AnalogOutputMainWindow(QtWidgets.QMainWindow):
    """ Main window for the AnalogOutputGui module. """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('qudi: Analog Output')

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


class AnalogOutputGui(GuiBase):
    """
    A graphical interface to set voltage and output (activity) state for a set of
    analog output channels (e.g. ao0-ao3).

    Example config for copy-paste:

        analog_output_gui:
            module.Class: 'analog_output.analog_output_gui.AnalogOutputGui'
            connect:
                analog_output_logic: 'analog_output_logic'
    """

    analog_output_logic = Connector(interface=AnalogOutputLogic)

    sigSetpointSet = QtCore.Signal(str, float)
    sigActivitySet = QtCore.Signal(str, bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._widgets = dict()

    def on_activate(self):
        """ Create all UI objects and show the window. """
        self._mw = AnalogOutputMainWindow()
        self._widgets = dict()

        self._populate_channels()

        self.sigSetpointSet.connect(
            self.analog_output_logic().set_setpoint, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.sigActivitySet.connect(
            self.analog_output_logic().set_activity_state, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self._mw.action_periodic_state_check.toggled.connect(
            self.analog_output_logic().toggle_watchdog, QtCore.Qt.ConnectionType.QueuedConnection
        )

        self.analog_output_logic().sigSetpointChanged.connect(
            self._setpoint_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.analog_output_logic().sigActivityChanged.connect(
            self._activity_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.analog_output_logic().sigWatchdogToggled.connect(
            self._watchdog_updated, QtCore.Qt.ConnectionType.QueuedConnection
        )

        self._restore_window_geometry(self._mw)

        self._watchdog_updated(self.analog_output_logic().watchdog_active)
        self.show()

    def on_deactivate(self):
        """ Hide window, empty the GUI, and disconnect signals. """
        self.analog_output_logic().sigSetpointChanged.disconnect(self._setpoint_updated)
        self.analog_output_logic().sigActivityChanged.disconnect(self._activity_updated)
        self.analog_output_logic().sigWatchdogToggled.disconnect(self._watchdog_updated)
        self._mw.action_periodic_state_check.toggled.disconnect()
        self.sigSetpointSet.disconnect()
        self.sigActivitySet.disconnect()

        self._save_window_geometry(self._mw)
        self._delete_channels()
        self._mw.close()

    def show(self):
        """ Make sure that the window is visible and at the top. """
        self._mw.show()

    @staticmethod
    def _safe(value, fallback=0.0):
        """ Fallback to a default if a hardware query returned None (error case). """
        return fallback if value is None else value

    def _populate_channels(self):
        """ Dynamically build one row per channel: label, voltage spinbox,
        output toggle switch.

        Voltage spinboxes use InstantStepDoubleSpinBox (see class above)
        rather than a plain QDoubleSpinBox, connected via its
        sigValueCommitted signal instead of editingFinished or valueChanged:

            - editingFinished alone would mean the up/down step buttons and
              scroll wheel silently do nothing (they don't cause focus loss
              or an Enter keypress).
            - valueChanged alone would mean typing "-5.5" fires a hardware
              write for "-5" and then again for "-5.5" -- an intermediate
              value getting briefly (but really) written to the DAC.

        sigValueCommitted fires immediately for discrete step actions
        (arrows, wheel, arrow keys), but only on Enter/focus-loss for typed
        input -- giving instant "on the fly" behavior for stepping while
        avoiding spurious intermediate writes while typing a new number.

        Column/row stretch factors are set at the end so the widgets grow to
        fill the window on resize.
        """
        logic = self.analog_output_logic()
        self._widgets = dict()

        header_font = QtGui.QFont()
        header_font.setBold(True)

        headers = ['Channel', 'Voltage (V)', 'Output']
        for col, text in enumerate(headers):
            label = QtWidgets.QLabel(text)
            label.setFont(header_font)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._mw.main_layout.addWidget(label, 0, col)

        for row, channel in enumerate(logic.channels, start=1):
            channel_label = QtWidgets.QLabel(channel.upper())
            font = channel_label.font()
            font.setBold(True)
            font.setPointSize(12)
            channel_label.setFont(font)
            channel_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            voltage_min, voltage_max = logic.get_limits(channel)
            voltage_spinbox = InstantStepDoubleSpinBox()
            voltage_spinbox.setRange(voltage_min, voltage_max)
            voltage_spinbox.setDecimals(3)
            voltage_spinbox.setSingleStep(0.1)
            voltage_spinbox.setSuffix(' V')
            voltage_spinbox.setValue(self._safe(logic.get_setpoint(channel)))
            voltage_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
            voltage_spinbox.sigValueCommitted.connect(
                self.__get_setpoint_update_func(channel)
            )

            output_switch = ToggleSwitchWidget(switch_states=('Off', 'On'),
                                               thumb_track_ratio=0.9,
                                               scale_text_in_switch=True,
                                               text_inside_switch=True)
            output_switch.set_state('On' if self._safe(logic.get_activity_state(channel), False) else 'Off')
            output_switch.sigStateChanged.connect(self.__get_activity_update_func(channel))

            self._widgets[channel] = {
                'label': channel_label,
                'voltage': voltage_spinbox,
                'output': output_switch,
            }

            self._mw.main_layout.addWidget(channel_label, row, 0)
            self._mw.main_layout.addWidget(voltage_spinbox, row, 1)
            self._mw.main_layout.addWidget(output_switch, row, 2)

        # Column stretch: label column stays fixed-width, voltage and output
        # columns grow to absorb extra horizontal space on window resize.
        self._mw.main_layout.setColumnStretch(0, 0)
        self._mw.main_layout.setColumnStretch(1, 1)
        self._mw.main_layout.setColumnStretch(2, 1)

        # Row stretch: header row stays fixed-height, each channel row gets
        # equal stretch so vertical resizing distributes evenly across rows.
        self._mw.main_layout.setRowStretch(0, 0)
        for row in range(1, len(logic.channels) + 1):
            self._mw.main_layout.setRowStretch(row, 1)

    def _delete_channels(self):
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
    def _setpoint_updated(self, channel, value):
        """ Reflect a voltage change (from hardware or watchdog) in the spinbox. """
        widget = self._widgets.get(channel)
        if widget is None:
            return
        spinbox = widget['voltage']
        spinbox.blockSignals(True)
        spinbox.setValue(value)
        spinbox.blockSignals(False)

    @QtCore.Slot(str, bool)
    def _activity_updated(self, channel, state):
        """ Reflect an activity/output state change (from hardware or watchdog)
        in the toggle switch.
        """
        widget = self._widgets.get(channel)
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

    def __get_setpoint_update_func(self, channel):
        def update_func(value):
            self.sigSetpointSet.emit(channel, value)
        return update_func

    def __get_activity_update_func(self, channel):
        def update_func(state):
            self.sigActivitySet.emit(channel, state == 'On')
        return update_func