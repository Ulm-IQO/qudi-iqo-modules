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

------------------------------------------------------------------------

OVERVIEW

Displays one section per connected analog output device (as reported by
AnalogOutputLogic.device_names), stacked vertically in the same window. Each
section has a bold headline showing that device's qudi module name (its
config section key, e.g. 'daq_1'), followed by one row per channel of that
device -- exactly matching the single-device layout used previously, just
repeated per device with a labeled divider between them.
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
    A graphical interface to set voltage and output (activity) state for one or
    more analog output devices, each with a set of channels (e.g. ao0-ao3),
    stacked vertically with a headline per device.

    Example config for copy-paste:

        analog_output_gui:
            module.Class: 'analog_output.analog_output_gui.AnalogOutputGui'
            connect:
                analog_output_logic: 'analog_output_logic'
    """

    analog_output_logic = Connector(interface=AnalogOutputLogic)

    sigSetpointSet = QtCore.Signal(str, str, float)
    sigActivitySet = QtCore.Signal(str, str, bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        # {(device_name, channel): {'label': ..., 'voltage': ..., 'output': ...}}
        self._widgets = dict()
        # Every non-channel-row widget (headlines, dividers, column headers),
        # tracked separately purely so _delete_channels() can clean them up
        # too -- they carry no signals to disconnect.
        self._section_widgets = []

    def on_activate(self):
        """ Create all UI objects and show the window. """
        self._mw = AnalogOutputMainWindow()
        self._widgets = dict()
        self._section_widgets = []

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
        """ Dynamically build one section per connected device (headline row
        with that device's qudi module name, followed by a column-header
        row, followed by one row per channel: label, voltage spinbox,
        output toggle switch), stacked vertically in the same grid.

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
        self._section_widgets = []

        header_font = QtGui.QFont()
        header_font.setBold(True)

        headline_font = QtGui.QFont()
        headline_font.setBold(True)
        headline_font.setPointSize(14)

        row = 0
        for device_index, device_name in enumerate(logic.device_names):
            headline = QtWidgets.QLabel(device_name)
            headline.setFont(headline_font)
            headline.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
            # Extra top margin before every headline except the first one,
            # so each device's section is visually separated from the
            # previous device's last channel row, rather than sitting flush
            # against it. (left, top, right, bottom)
            top_margin = 0 if device_index == 0 else 20
            headline.setContentsMargins(0, top_margin, 0, 0)
            self._mw.main_layout.addWidget(headline, row, 0, 1, 3)
            self._section_widgets.append(headline)
            row += 1

            for channel in logic.channels_for_device(device_name):
                key = (device_name, channel)

                channel_label = QtWidgets.QLabel(channel.upper())
                font = channel_label.font()
                font.setBold(True)
                font.setPointSize(12)
                channel_label.setFont(font)
                channel_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

                voltage_min, voltage_max = logic.get_limits(device_name, channel)
                voltage_spinbox = InstantStepDoubleSpinBox()
                voltage_spinbox.setRange(voltage_min, voltage_max)
                voltage_spinbox.setDecimals(3)
                voltage_spinbox.setSingleStep(0.1)
                voltage_spinbox.setSuffix(' V')
                voltage_spinbox.setValue(self._safe(logic.get_setpoint(device_name, channel)))
                voltage_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                              QtWidgets.QSizePolicy.Policy.Preferred)
                voltage_spinbox.sigValueCommitted.connect(
                    self.__get_setpoint_update_func(device_name, channel)
                )

                output_switch = ToggleSwitchWidget(switch_states=('Off', 'On'),
                                                   thumb_track_ratio=0.9,
                                                   scale_text_in_switch=True,
                                                   text_inside_switch=True)
                output_switch.set_state(
                    'On' if self._safe(logic.get_activity_state(device_name, channel), False) else 'Off'
                )
                output_switch.sigStateChanged.connect(
                    self.__get_activity_update_func(device_name, channel)
                )

                self._widgets[key] = {
                    'label': channel_label,
                    'voltage': voltage_spinbox,
                    'output': output_switch,
                }

                self._mw.main_layout.addWidget(channel_label, row, 0)
                self._mw.main_layout.addWidget(voltage_spinbox, row, 1)
                self._mw.main_layout.addWidget(output_switch, row, 2)
                row += 1

        # Column stretch: label column stays fixed-width, voltage and output
        # columns grow to absorb extra horizontal space on window resize.
        self._mw.main_layout.setColumnStretch(0, 0)
        self._mw.main_layout.setColumnStretch(1, 1)
        self._mw.main_layout.setColumnStretch(2, 1)

        # Row stretch: every row (headlines, column headers, and channel
        # rows alike) gets equal stretch so vertical resizing distributes
        # evenly across the whole stacked layout.
        for r in range(row):
            self._mw.main_layout.setRowStretch(r, 1)

    def _delete_channels(self):
        """ Delete all row and section widgets from the main layout. """
        self._widgets.clear()
        self._section_widgets.clear()
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

    @QtCore.Slot(str, str, float)
    def _setpoint_updated(self, device_name, channel, value):
        """ Reflect a voltage change (from hardware or watchdog) in the spinbox. """
        widget = self._widgets.get((device_name, channel))
        if widget is None:
            return
        spinbox = widget['voltage']
        spinbox.blockSignals(True)
        spinbox.setValue(value)
        spinbox.blockSignals(False)

    @QtCore.Slot(str, str, bool)
    def _activity_updated(self, device_name, channel, state):
        """ Reflect an activity/output state change (from hardware or watchdog)
        in the toggle switch.
        """
        widget = self._widgets.get((device_name, channel))
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

    def __get_setpoint_update_func(self, device_name, channel):
        def update_func(value):
            self.sigSetpointSet.emit(device_name, channel, value)
        return update_func

    def __get_activity_update_func(self, device_name, channel):
        def update_func(state):
            self.sigActivitySet.emit(device_name, channel, state == 'On')
        return update_func