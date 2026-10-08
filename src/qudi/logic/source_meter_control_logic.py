# -*- coding: utf-8 -*-
"""
Control a set of source meters / power supplies (voltage, current limit, output), e.g. the
Keithley 2400s supplying APD modules, and remember their settings across qudi sessions.

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

from PySide6 import QtCore

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex
from qudi.interface.source_meter_interface import SourceMeterInterface


class SourceMeterControlLogic(LogicBase):
    """ Logic module for up to four source meters, each used as voltage source with a current
    limit.

    The settings last set through this module (voltage, current limit, output state) are kept
    in a status variable, so they survive closing qudi and deactivating the module. On
    activation the instruments' actual settings are read and shown; the instruments are not
    changed. If they differ from the remembered settings (e.g. after an instrument was power
    cycled), a warning is logged and the remembered settings can be restored with
    restore_saved_settings() (GUI: Settings menu), or automatically at startup with
    restore_saved_settings_on_startup.

    Example config for copy-paste:

    apd_supply_logic:
        module.Class: 'source_meter_control_logic.SourceMeterControlLogic'
        options:
            channel_names: ['APD1', 'APD2']  # one per connected supply, in connector order
            watchdog_interval: 1  # s, optional
            autostart_watchdog: True  # optional
            restore_saved_settings_on_startup: False  # optional, re-apply voltage/current limit
            restore_output_state_on_startup: False  # optional, also re-apply output ON/OFF
        connect:
            supply_1: 'apd_supply_1'
            supply_2: 'apd_supply_2'  # optional, as are supply_3 and supply_4
    """

    supply_1 = Connector(interface=SourceMeterInterface)
    supply_2 = Connector(interface=SourceMeterInterface, optional=True)
    supply_3 = Connector(interface=SourceMeterInterface, optional=True)
    supply_4 = Connector(interface=SourceMeterInterface, optional=True)

    _channel_names = ConfigOption(name='channel_names', default=list(), missing='nothing')
    _watchdog_interval = ConfigOption(name='watchdog_interval', default=1.0, missing='nothing')
    _autostart_watchdog = ConfigOption(name='autostart_watchdog', default=False, missing='nothing')
    _restore_on_startup = ConfigOption(name='restore_saved_settings_on_startup', default=False,
                                       missing='nothing')
    _restore_output_on_startup = ConfigOption(name='restore_output_state_on_startup',
                                              default=False, missing='nothing')

    # {channel name: {'voltage': float, 'current_limit': float, 'output': bool}}
    _saved_settings = StatusVar(name='saved_settings', default=dict())

    sigVoltageChanged = QtCore.Signal(str, float)
    sigCurrentLimitChanged = QtCore.Signal(str, float)
    sigOutputChanged = QtCore.Signal(str, bool)
    sigMeasurementUpdated = QtCore.Signal(str, object)  # dict or None, see measure()
    sigSavedSettingsDifferUpdated = QtCore.Signal(object)  # {channel name: bool}
    sigWatchdogToggled = QtCore.Signal(bool)

    _SETTINGS = ('voltage', 'current_limit', 'output')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = RecursiveMutex()
        self._supplies = dict()
        self._state = dict()
        self._watchdog_active = False
        self._watchdog_interval_ms = 0

    def on_activate(self):
        """ Activate module """
        self._supplies = dict()
        names = list(self._channel_names)
        for index, connector in enumerate(
                (self.supply_1, self.supply_2, self.supply_3, self.supply_4)):
            supply = connector()
            if supply is None:
                continue
            name = str(names[index]) if index < len(names) else f'Supply {index + 1}'
            self._supplies[name] = supply

        self._saved_settings = {name: dict(settings)
                                for name, settings in self._saved_settings.items()}
        self._state = {name: self._read_settings(name) for name in self._supplies}

        differing = [name for name, differs in self.saved_settings_differ().items() if differs]
        if differing and self._restore_on_startup:
            self.log.info(f'Restoring saved settings of {differing} at startup.')
            for name in differing:
                self._apply_settings(name, self._saved_settings[name],
                                     with_output=self._restore_output_on_startup)
        elif differing:
            self.log.warning(
                'Instrument settings differ from the settings saved in the last session: '
                + '; '.join(f'{name}: instrument {self._format(self._state[name])}, saved '
                            f'{self._format(self._saved_settings[name])}' for name in differing)
                + '. Use "Restore saved settings" to apply the saved ones.')

        # Channels without saved settings yet start out with the instrument's settings.
        for name, state in self._state.items():
            if name not in self._saved_settings and None not in state.values():
                self._saved_settings[name] = dict(state)

        self._watchdog_interval_ms = int(round(self._watchdog_interval * 1000))
        self._watchdog_active = bool(self._autostart_watchdog)
        if self._watchdog_active:
            QtCore.QMetaObject.invokeMethod(self, '_watchdog_body',
                                            QtCore.Qt.ConnectionType.QueuedConnection)

    def on_deactivate(self):
        """ Deactivate module. The supplies are left as they are. """
        self._watchdog_active = False

    # =========================================================================
    # Properties / getters
    # =========================================================================

    @property
    def channels(self):
        """ Names of all connected supplies, in connector order. """
        return tuple(self._supplies)

    @property
    def watchdog_active(self):
        return self._watchdog_active

    def get_voltage_limits(self, name):
        return self._supplies[name].voltage_limits

    def get_current_limit_limits(self, name):
        return self._supplies[name].current_limit_limits

    def get_settings(self, name):
        """ Last known instrument settings {'voltage', 'current_limit', 'output'} (values may be
        None if they could not be read).
        """
        with self._thread_lock:
            return dict(self._state[name])

    def get_saved_settings(self, name):
        """ Settings remembered from the last change through this module, or None. """
        with self._thread_lock:
            saved = self._saved_settings.get(name)
            return None if saved is None else dict(saved)

    def saved_settings_differ(self):
        """ {channel name: True if the instrument settings differ from the saved ones} """
        with self._thread_lock:
            return {name: self._differs(self._state[name], self._saved_settings.get(name))
                    for name in self._supplies}

    # =========================================================================
    # Setters
    # =========================================================================

    @QtCore.Slot(str, float)
    def set_voltage(self, name, value):
        with self._thread_lock:
            try:
                applied = self._supplies[name].set_voltage(value)
            except Exception:
                self.log.exception(f'Error while setting voltage of "{name}" to {value}.')
                return
            self._update_setting(name, 'voltage', applied, save=True)

    @QtCore.Slot(str, float)
    def set_current_limit(self, name, value):
        with self._thread_lock:
            try:
                applied = self._supplies[name].set_current_limit(value)
            except Exception:
                self.log.exception(f'Error while setting current limit of "{name}" to {value}.')
                return
            self._update_setting(name, 'current_limit', applied, save=True)

    @QtCore.Slot(str, bool)
    def set_output(self, name, state):
        with self._thread_lock:
            try:
                applied = self._supplies[name].set_output_state(bool(state))
            except Exception:
                self.log.exception(f'Error while switching output of "{name}" to {state}.')
                return
            self._update_setting(name, 'output', applied, save=True)
            self._emit_measurement(name)

    @QtCore.Slot()
    def restore_saved_settings(self):
        """ Apply the saved voltage, current limit and output state to all supplies whose
        settings differ from the saved ones.
        """
        with self._thread_lock:
            for name, differs in self.saved_settings_differ().items():
                if differs:
                    self.log.info(f'Restoring saved settings of "{name}": '
                                  f'{self._format(self._saved_settings[name])}')
                    self._apply_settings(name, self._saved_settings[name], with_output=True)

    def _apply_settings(self, name, settings, with_output):
        """ Apply saved settings. Output OFF is applied first, output ON last. """
        if with_output and not settings['output']:
            self.set_output(name, False)
        self.set_current_limit(name, settings['current_limit'])
        self.set_voltage(name, settings['voltage'])
        if with_output and settings['output']:
            self.set_output(name, True)

    # =========================================================================
    # Watchdog
    # =========================================================================

    @QtCore.Slot(bool)
    def toggle_watchdog(self, enable):
        enable = bool(enable)
        with self._thread_lock:
            if enable != self._watchdog_active:
                self._watchdog_active = enable
                self.sigWatchdogToggled.emit(enable)
                if enable:
                    QtCore.QMetaObject.invokeMethod(self, '_watchdog_body',
                                                    QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.Slot()
    def _watchdog_body(self):
        """ Regularly read all settings and measure the outputs, emitting changes. Catches
        changes made outside of this module, e.g. on the instrument front panel.
        """
        with self._thread_lock:
            if not self._watchdog_active:
                return
            for name in self._supplies:
                settings = self._read_settings(name)
                if None in settings.values():
                    self.toggle_watchdog(False)
                    self.log.error('Deactivated periodic state checking to avoid repeated '
                                   'errors. Re-enable it in the View menu.')
                    return
                for key, value in settings.items():
                    self._update_setting(name, key, value, save=False)
                self._emit_measurement(name)
            QtCore.QTimer.singleShot(self._watchdog_interval_ms, self._watchdog_body)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _read_settings(self, name):
        supply = self._supplies[name]
        settings = dict()
        for key, getter in (('voltage', supply.get_voltage),
                            ('current_limit', supply.get_current_limit),
                            ('output', supply.get_output_state)):
            try:
                settings[key] = getter()
            except Exception:
                self.log.exception(f'Error while reading {key} of "{name}".')
                settings[key] = None
        return settings

    def _update_setting(self, name, key, value, save):
        """ Store a new instrument value, optionally remember it, and emit the changes. """
        old_differs = self.saved_settings_differ()
        changed = self._state[name].get(key) != value
        self._state[name][key] = value
        if save:
            self._saved_settings.setdefault(name, dict(self._state[name]))[key] = value
        if changed or save:
            if key == 'voltage':
                self.sigVoltageChanged.emit(name, value)
            elif key == 'current_limit':
                self.sigCurrentLimitChanged.emit(name, value)
            else:
                self.sigOutputChanged.emit(name, value)
        new_differs = self.saved_settings_differ()
        if new_differs != old_differs or save:
            self.sigSavedSettingsDifferUpdated.emit(new_differs)

    def _emit_measurement(self, name):
        try:
            measurement = self._supplies[name].measure()
        except Exception:
            self.log.exception(f'Error while measuring output of "{name}".')
            measurement = None
        self.sigMeasurementUpdated.emit(name, measurement)

    @staticmethod
    def _differs(state, saved):
        if saved is None or None in state.values() or None in saved.values():
            return False
        return (state['output'] != saved['output']
                or abs(state['voltage'] - saved['voltage']) > 1e-6 * max(1.0, abs(saved['voltage']))
                or abs(state['current_limit'] - saved['current_limit'])
                > 1e-6 * max(1e-3, abs(saved['current_limit'])))

    @staticmethod
    def _format(settings):
        return '{0:.4f} V, limit {1:.4g} mA, output {2}'.format(
            settings['voltage'], settings['current_limit'] * 1e3,
            'ON' if settings['output'] else 'OFF')
