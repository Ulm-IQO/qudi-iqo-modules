# -*- coding: utf-8 -*-
"""
This file contains the qudi source meter control GUI module (voltage, current limit and output of
e.g. the Keithley 2400s supplying the APDs).

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
from qudi.core.configoption import ConfigOption
from qudi.core.module import GuiBase

from qudi.logic.source_meter_control_logic import SourceMeterControlLogic
from qudi.gui.switch.switch_state_widgets import ToggleSwitchWidget
from qudi.gui.coil_control.coil_control_gui import InstantStepDoubleSpinBox


class EasyInputDoubleSpinBox(InstantStepDoubleSpinBox):
    """ InstantStepDoubleSpinBox that is easier to type into:
        - clicking/tabbing into the field selects the whole number, so typing replaces it,
        - a typed number outside the range is not blocked while typing; it is clamped to the
          range when committed (Enter or leaving the field).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCorrectionMode(QtWidgets.QAbstractSpinBox.CorrectionMode.CorrectToNearestValue)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        # deferred, since the mouse press that gave the focus would clear the selection again
        QtCore.QTimer.singleShot(0, self.selectAll)

    def validate(self, text, pos):
        result = super().validate(text, pos)
        state = result[0] if isinstance(result, tuple) else result
        if state == QtGui.QValidator.State.Invalid and self._parse(text) is not None:
            return QtGui.QValidator.State.Intermediate, text, pos
        return result

    def fixup(self, text):
        value = self._parse(text)
        if value is None:
            return super().fixup(text)
        value = min(max(value, self.minimum()), self.maximum())
        return self.textFromValue(value)

    def _parse(self, text):
        text = text.strip()
        if self.suffix() and text.endswith(self.suffix().strip()):
            text = text[:-len(self.suffix().strip())]
        try:
            return float(text.strip().replace(',', '.'))
        except ValueError:
            return None


class SourceMeterControlMainWindow(QtWidgets.QMainWindow):
    """ Main window for the SourceMeterControlGui module. """

    def __init__(self, *args, title='qudi: Source Meter Control', **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle(title)

        self.main_layout = QtWidgets.QGridLayout()
        widget = QtWidgets.QWidget()
        widget.setLayout(self.main_layout)
        self.setCentralWidget(widget)

        menu_bar = QtWidgets.QMenuBar()
        self.setMenuBar(menu_bar)

        menu = menu_bar.addMenu('Menu')
        self.action_close = QtGui.QAction('Close Window')
        menu.addAction(self.action_close)

        menu = menu_bar.addMenu('View')
        self.action_periodic_state_check = QtGui.QAction('Periodic State Checking')
        self.action_periodic_state_check.setCheckable(True)
        menu.addAction(self.action_periodic_state_check)

        menu = menu_bar.addMenu('Settings')
        self.action_restore_saved = QtGui.QAction('Restore saved settings')
        self.action_restore_saved.setToolTip(
            'Apply the voltage, current limit and output state saved in the last session to all '
            'supplies whose settings differ from them.')
        menu.addAction(self.action_restore_saved)
        menu.setToolTipsVisible(True)

        self.action_close.triggered.connect(self.close)


class SourceMeterControlGui(GuiBase):
    """
    A graphical interface to set voltage, current limit and output state of a set of source
    meters, and to show their measured output.

    Example config for copy-paste:

        apd_supply_gui:
            module.Class: 'source_meter_control.source_meter_control_gui.SourceMeterControlGui'
            options:
                window_title: 'qudi: APD Supplies'  # optional
            connect:
                source_meter_logic: 'apd_supply_logic'
    """

    source_meter_logic = Connector(interface=SourceMeterControlLogic)

    _window_title = ConfigOption(name='window_title', default='qudi: Source Meter Control',
                                 missing='nothing')

    sigVoltageSet = QtCore.Signal(str, float)
    sigCurrentLimitSet = QtCore.Signal(str, float)
    sigOutputSet = QtCore.Signal(str, bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._widgets = dict()

    def on_activate(self):
        """ Create all UI objects and show the window. """
        self._mw = SourceMeterControlMainWindow(title=self._window_title)
        self._widgets = dict()
        logic = self.source_meter_logic()

        self._populate_channels()

        self.sigVoltageSet.connect(logic.set_voltage, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigCurrentLimitSet.connect(logic.set_current_limit,
                                        QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigOutputSet.connect(logic.set_output, QtCore.Qt.ConnectionType.QueuedConnection)
        self._mw.action_periodic_state_check.toggled.connect(
            logic.toggle_watchdog, QtCore.Qt.ConnectionType.QueuedConnection)
        self._mw.action_restore_saved.triggered.connect(
            logic.restore_saved_settings, QtCore.Qt.ConnectionType.QueuedConnection)

        logic.sigVoltageChanged.connect(self._voltage_updated,
                                        QtCore.Qt.ConnectionType.QueuedConnection)
        logic.sigCurrentLimitChanged.connect(self._current_limit_updated,
                                             QtCore.Qt.ConnectionType.QueuedConnection)
        logic.sigOutputChanged.connect(self._output_updated,
                                       QtCore.Qt.ConnectionType.QueuedConnection)
        logic.sigMeasurementUpdated.connect(self._measurement_updated,
                                            QtCore.Qt.ConnectionType.QueuedConnection)
        logic.sigSavedSettingsDifferUpdated.connect(self._saved_differ_updated,
                                                    QtCore.Qt.ConnectionType.QueuedConnection)
        logic.sigWatchdogToggled.connect(self._watchdog_updated,
                                         QtCore.Qt.ConnectionType.QueuedConnection)

        self._restore_window_geometry(self._mw)
        self._watchdog_updated(logic.watchdog_active)
        self._saved_differ_updated(logic.saved_settings_differ())
        self.show()

    def on_deactivate(self):
        """ Hide window, empty the GUI, and disconnect signals. """
        logic = self.source_meter_logic()
        logic.sigVoltageChanged.disconnect(self._voltage_updated)
        logic.sigCurrentLimitChanged.disconnect(self._current_limit_updated)
        logic.sigOutputChanged.disconnect(self._output_updated)
        logic.sigMeasurementUpdated.disconnect(self._measurement_updated)
        logic.sigSavedSettingsDifferUpdated.disconnect(self._saved_differ_updated)
        logic.sigWatchdogToggled.disconnect(self._watchdog_updated)
        self._mw.action_periodic_state_check.toggled.disconnect()
        self._mw.action_restore_saved.triggered.disconnect()
        self.sigVoltageSet.disconnect()
        self.sigCurrentLimitSet.disconnect()
        self.sigOutputSet.disconnect()

        self._save_window_geometry(self._mw)
        self._delete_channels()
        self._mw.close()

    def show(self):
        """ Make sure that the window is visible and at the top. """
        self._mw.show()
        self._mw.raise_()
        self._mw.activateWindow()

    def _populate_channels(self):
        """ Build one row per supply: name, voltage, current limit (mA), output switch, measured
        output. Spinboxes commit on arrow/wheel steps immediately and on Enter/focus loss for
        typed input (see InstantStepDoubleSpinBox).
        """
        logic = self.source_meter_logic()

        header_font = QtGui.QFont()
        header_font.setBold(True)
        for col, text in enumerate(['Supply', 'Voltage', 'Current limit', 'Output', 'Measured']):
            label = QtWidgets.QLabel(text)
            label.setFont(header_font)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._mw.main_layout.addWidget(label, 0, col)

        for row, name in enumerate(logic.channels, start=1):
            settings = logic.get_settings(name)

            name_label = QtWidgets.QLabel(name)
            font = name_label.font()
            font.setBold(True)
            font.setPointSize(12)
            name_label.setFont(font)
            name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            v_min, v_max = logic.get_voltage_limits(name)
            voltage_spinbox = EasyInputDoubleSpinBox()
            voltage_spinbox.setRange(v_min, v_max)
            voltage_spinbox.setDecimals(3)
            voltage_spinbox.setSingleStep(0.1)
            voltage_spinbox.setSuffix(' V')
            voltage_spinbox.setValue(self._safe(settings['voltage']))
            voltage_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
            voltage_spinbox.sigValueCommitted.connect(self.__get_voltage_update_func(name))

            # Current limit is shown in mA
            i_min, i_max = logic.get_current_limit_limits(name)
            current_spinbox = EasyInputDoubleSpinBox()
            current_spinbox.setDecimals(3)
            current_spinbox.setRange(i_min * 1e3, i_max * 1e3)
            current_spinbox.setSingleStep(1.0)
            current_spinbox.setSuffix(' mA')
            current_spinbox.setValue(self._safe(settings['current_limit']) * 1e3)
            current_spinbox.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
            current_spinbox.sigValueCommitted.connect(self.__get_current_limit_update_func(name))

            output_switch = ToggleSwitchWidget(switch_states=('Off', 'On'),
                                               thumb_track_ratio=0.9,
                                               scale_text_in_switch=True,
                                               text_inside_switch=True)
            output_switch.set_state('On' if settings['output'] else 'Off')
            output_switch.sigStateChanged.connect(self.__get_output_update_func(name))

            measured_label = QtWidgets.QLabel('—')
            measured_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            measured_label.setMinimumWidth(170)

            self._widgets[name] = {'label': name_label,
                                   'voltage': voltage_spinbox,
                                   'current_limit': current_spinbox,
                                   'output': output_switch,
                                   'measured': measured_label}
            for col, widget in enumerate(self._widgets[name].values()):
                self._mw.main_layout.addWidget(widget, row, col)

        self._mw.main_layout.setColumnStretch(0, 0)
        for col in range(1, 5):
            self._mw.main_layout.setColumnStretch(col, 1)
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
                for signal in ('sigStateChanged', 'sigValueCommitted'):
                    try:
                        getattr(widget, signal).disconnect()
                    except (AttributeError, RuntimeError):
                        pass
                widget.setParent(None)
                widget.deleteLater()

    @staticmethod
    def _safe(value, fallback=0.0):
        """ Fallback to a default if a hardware query returned None (error case). """
        return fallback if value is None else value

    @staticmethod
    def _set_spinbox(spinbox, value):
        if spinbox.hasFocus():
            return  # do not overwrite a value the user is currently typing
        spinbox.blockSignals(True)
        spinbox.setValue(value)
        spinbox.blockSignals(False)

    @QtCore.Slot(str, float)
    def _voltage_updated(self, name, value):
        widgets = self._widgets.get(name)
        if widgets is not None:
            self._set_spinbox(widgets['voltage'], value)

    @QtCore.Slot(str, float)
    def _current_limit_updated(self, name, value):
        widgets = self._widgets.get(name)
        if widgets is not None:
            self._set_spinbox(widgets['current_limit'], value * 1e3)

    @QtCore.Slot(str, bool)
    def _output_updated(self, name, state):
        widgets = self._widgets.get(name)
        if widgets is not None:
            widgets['output'].blockSignals(True)
            widgets['output'].set_state('On' if state else 'Off')
            widgets['output'].blockSignals(False)

    @QtCore.Slot(str, object)
    def _measurement_updated(self, name, measurement):
        widgets = self._widgets.get(name)
        if widgets is None:
            return
        label = widgets['measured']
        if measurement is None:
            label.setText('—')
            label.setStyleSheet('')
            label.setToolTip('Output is off')
            return
        text = '{0:.4f} V   {1}'.format(measurement['voltage'],
                                         self._format_current(measurement['current']))
        if measurement['compliance']:
            label.setText(text + '\nCURRENT LIMIT REACHED')
            label.setStyleSheet('color: red; font-weight: bold;')
            label.setToolTip('The output is limited by the current limit (compliance).')
        else:
            label.setText(text)
            label.setStyleSheet('')
            label.setToolTip('')

    @QtCore.Slot(object)
    def _saved_differ_updated(self, differ):
        """ Mark supplies whose instrument settings differ from the saved settings. """
        logic = self.source_meter_logic()
        for name, widgets in self._widgets.items():
            if differ.get(name):
                saved = logic.get_saved_settings(name)
                widgets['label'].setStyleSheet('color: orange;')
                widgets['label'].setToolTip(
                    'Instrument settings differ from the saved settings:\n'
                    '{0:.3f} V, limit {1:.4f} mA, output {2}\n'
                    'Use Settings > Restore saved settings to apply them.'.format(
                        saved['voltage'], saved['current_limit'] * 1e3,
                        'ON' if saved['output'] else 'OFF'))
            else:
                widgets['label'].setStyleSheet('')
                widgets['label'].setToolTip('')
        self._mw.action_restore_saved.setEnabled(any(differ.values()))

    @QtCore.Slot(bool)
    def _watchdog_updated(self, enabled):
        """ Update the menu action to match the logic's actual watchdog state. """
        if enabled != self._mw.action_periodic_state_check.isChecked():
            self._mw.action_periodic_state_check.blockSignals(True)
            self._mw.action_periodic_state_check.setChecked(enabled)
            self._mw.action_periodic_state_check.blockSignals(False)

    @staticmethod
    def _format_current(current):
        if abs(current) >= 1e-1:
            return '{0:.4f} A'.format(current)
        if abs(current) >= 1e-4:
            return '{0:.4f} mA'.format(current * 1e3)
        return '{0:.3f} µA'.format(current * 1e6)

    def __get_voltage_update_func(self, name):
        def update_func(value):
            self.sigVoltageSet.emit(name, value)
        return update_func

    def __get_current_limit_update_func(self, name):
        def update_func(value):
            self.sigCurrentLimitSet.emit(name, value * 1e-3)
        return update_func

    def __get_output_update_func(self, name):
        def update_func(state):
            self.sigOutputSet.emit(name, state == 'On')
        return update_func
