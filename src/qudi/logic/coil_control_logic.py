# -*- coding: utf-8 -*-
"""
Interact with coil control hardware (per-axis voltage, current, and output state).

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
from qudi.util.mutex import RecursiveMutex
from qudi.interface.coil_control_interface import CoilControlInterface


class CoilControlLogic(LogicBase):
    """ Logic module for interacting with a coil control hardware/interfuse module
    (voltage, current, and output state per magnet coil axis).

    coil_control_logic:
        module.Class: 'coil_control_logic.CoilControlLogic'
        options:
            watchdog_interval: 1  # optional
            autostart_watchdog: True  # optional
        connect:
            coil_control: <coil control hardware/interfuse name>
    """

    coil_control = Connector(interface=CoilControlInterface)

    _watchdog_interval = ConfigOption(name='watchdog_interval', default=1.0, missing='nothing')
    _autostart_watchdog = ConfigOption(name='autostart_watchdog', default=False, missing='nothing')

    sigVoltageChanged = QtCore.Signal(str, float)
    sigCurrentChanged = QtCore.Signal(str, float)
    sigOutputChanged = QtCore.Signal(str, bool)
    sigWatchdogToggled = QtCore.Signal(bool)

    # directly wrapped attributes from hardware module
    __wrapped_hw_attributes = frozenset({'axes', 'number_of_axes'})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        self._watchdog_active = False
        self._watchdog_interval_ms = 0
        self._old_voltages = dict()
        self._old_currents = dict()
        self._old_outputs = dict()

    def on_activate(self):
        """ Activate module """
        self._old_voltages = self.get_all_voltages()
        self._old_currents = self.get_all_currents()
        self._old_outputs = self.get_all_output_states()
        self._watchdog_interval_ms = int(round(self._watchdog_interval * 1000))

        if self._autostart_watchdog:
            self._watchdog_active = True
            QtCore.QMetaObject.invokeMethod(self, '_watchdog_body', QtCore.Qt.ConnectionType.QueuedConnection)
        else:
            self._watchdog_active = False

    def on_deactivate(self):
        """ Deactivate module """
        self._watchdog_active = False

    def __getattr__(self, item):
        if item in self.__wrapped_hw_attributes:
            return getattr(self.coil_control(), item)
        raise AttributeError(f'CoilControlLogic has no attribute with name "{item}"')

    @property
    def watchdog_active(self):
        return self._watchdog_active

    # =========================================================================
    # Limits
    # =========================================================================

    def get_voltage_limits(self, axis):
        """ Return (min, max) allowed compliance voltage for the given axis. """
        with self._thread_lock:
            try:
                return self.coil_control().get_voltage_limits(axis)
            except Exception:
                self.log.exception(f'Error while querying voltage limits for axis "{axis}".')
                return (0.0, 0.0)

    def get_current_limits(self, axis):
        """ Return (min, max) allowed SIGNED current for the given axis. """
        with self._thread_lock:
            try:
                return self.coil_control().get_current_limits(axis)
            except Exception:
                self.log.exception(f'Error while querying current limits for axis "{axis}".')
                return (0.0, 0.0)

    # =========================================================================
    # Getters
    # =========================================================================

    def get_voltage(self, axis):
        with self._thread_lock:
            try:
                return self.coil_control().get_voltage(axis)
            except Exception:
                self._handle_query_error(f'voltage of axis "{axis}"')
                return None

    def get_current(self, axis):
        with self._thread_lock:
            try:
                return self.coil_control().get_current(axis)
            except Exception:
                self._handle_query_error(f'current of axis "{axis}"')
                return None

    def get_output_state(self, axis):
        with self._thread_lock:
            try:
                return self.coil_control().get_output_state(axis)
            except Exception:
                self._handle_query_error(f'output state of axis "{axis}"')
                return None

    def get_all_voltages(self):
        return {axis: self.get_voltage(axis) for axis in self.axes}

    def get_all_currents(self):
        return {axis: self.get_current(axis) for axis in self.axes}

    def get_all_output_states(self):
        return {axis: self.get_output_state(axis) for axis in self.axes}

    def _handle_query_error(self, what):
        """ Shared error handling for read-only query methods: deactivates the
        watchdog (to avoid repeated errors on a broken connection) and logs.
        """
        if self._watchdog_active:
            self.toggle_watchdog(False)
            self.log.exception(f'Error while querying {what}. Deactivating watchdog to avoid constant errors.')
        else:
            self.log.exception(f'Error while querying {what}.')

    # =========================================================================
    # Setters
    # =========================================================================

    @QtCore.Slot(str, float)
    def set_voltage(self, axis, value):
        with self._thread_lock:
            try:
                applied = self.coil_control().set_voltage(axis, value)
            except Exception:
                self.log.exception(f'Error while setting voltage of axis "{axis}" to {value}.')
                return
            self.sigVoltageChanged.emit(axis, applied)

    @QtCore.Slot(str, float)
    def set_voltage_relative(self, axis, delta):
        with self._thread_lock:
            try:
                applied = self.coil_control().set_voltage_relative(axis, delta)
            except Exception:
                self.log.exception(f'Error while stepping voltage of axis "{axis}" by {delta}.')
                return
            self.sigVoltageChanged.emit(axis, applied)

    @QtCore.Slot(str, float)
    def set_current(self, axis, value):
        with self._thread_lock:
            try:
                applied = self.coil_control().set_current(axis, value)
            except Exception:
                self.log.exception(f'Error while setting current of axis "{axis}" to {value}.')
                return
            self.sigCurrentChanged.emit(axis, applied)

    @QtCore.Slot(str, float)
    def set_current_relative(self, axis, delta):
        with self._thread_lock:
            try:
                applied = self.coil_control().set_current_relative(axis, delta)
            except Exception:
                self.log.exception(f'Error while stepping current of axis "{axis}" by {delta}.')
                return
            self.sigCurrentChanged.emit(axis, applied)

    @QtCore.Slot(str, bool)
    def set_output(self, axis, state):
        with self._thread_lock:
            try:
                if state:
                    self.coil_control().output_on(axis)
                else:
                    self.coil_control().output_off(axis)
            except Exception:
                self.log.exception(f'Error while setting output of axis "{axis}" to {state}.')
                return
            actual_state = self.get_output_state(axis)
            if actual_state is not None:
                self.sigOutputChanged.emit(axis, actual_state)

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
                    QtCore.QMetaObject.invokeMethod(self,
                                                    '_watchdog_body',
                                                    QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.Slot()
    def _watchdog_body(self):
        """ Regularly poll voltage, current, and output state for all axes and
        emit change signals for anything that differs from the last poll --
        catches changes made outside of this logic module (e.g. directly on
        the instrument, or via a different qudi session).
        """
        with self._thread_lock:
            if self._watchdog_active:
                curr_voltages = self.get_all_voltages()
                curr_currents = self.get_all_currents()
                curr_outputs = self.get_all_output_states()

                for axis, value in curr_voltages.items():
                    if value is not None and value != self._old_voltages.get(axis):
                        self.sigVoltageChanged.emit(axis, value)
                for axis, value in curr_currents.items():
                    if value is not None and value != self._old_currents.get(axis):
                        self.sigCurrentChanged.emit(axis, value)
                for axis, value in curr_outputs.items():
                    if value is not None and value != self._old_outputs.get(axis):
                        self.sigOutputChanged.emit(axis, value)

                self._old_voltages = curr_voltages
                self._old_currents = curr_currents
                self._old_outputs = curr_outputs

                QtCore.QTimer.singleShot(self._watchdog_interval_ms, self._watchdog_body)