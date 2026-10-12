# -*- coding: utf-8 -*-
"""
Logic for a motorized stage of independent axes (e.g. a magnet stage on Thorlabs Kinesis
controllers): polls positions/status and passes commands to the hardware, which enforces the
soft limits.

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
from qudi.interface.motor_stage_interface import MotorStageInterface


class MotorStageLogic(LogicBase):
    """ Polls the stage and forwards commands. Refused commands (limits, blocked axis, ...) are
    logged as errors; nothing moves except on explicit move/home commands.

    Example config for copy-paste:

    magnet_stage_logic:
        module.Class: 'motor_stage_logic.MotorStageLogic'
        options:
            poll_interval: 0.2   # s, position/status refresh
        connect:
            stage: 'magnet_stage'
    """

    stage = Connector(interface=MotorStageInterface)

    _poll_interval = ConfigOption(name='poll_interval', default=0.2, missing='nothing')

    # axis, {'position', 'status', 'limits'}
    sigAxisUpdated = QtCore.Signal(str, object)
    # axis, {'velocity', 'acceleration', 'backlash', 'homing_velocity'}
    sigMotionParamsUpdated = QtCore.Signal(str, object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = RecursiveMutex()
        self._timer = None

    def on_activate(self):
        self._timer = QtCore.QTimer()
        self._timer.setInterval(int(round(self._poll_interval * 1000)))
        self._timer.timeout.connect(self._poll, QtCore.Qt.ConnectionType.QueuedConnection)
        self._timer.start()

    def on_deactivate(self):
        self._timer.stop()
        self._timer.timeout.disconnect()
        self._timer = None

    # =============================================================================================
    # Read access
    # =============================================================================================

    @property
    def axes(self):
        return self.stage().axes

    def get_axis_info(self, axis):
        return self.stage().get_axis_info(axis)

    def get_axis_state(self, axis):
        """ {'position', 'status', 'limits'} of one axis. """
        with self._lock:
            stage = self.stage()
            return {'position': stage.get_position(axis), 'status': stage.get_status(axis),
                    'limits': stage.get_limits(axis)}

    @QtCore.Slot()
    def _poll(self):
        with self._lock:
            for axis in self.axes:
                try:
                    self.sigAxisUpdated.emit(axis, self.get_axis_state(axis))
                except Exception:
                    self.log.exception('Reading axis "{0}" failed.'.format(axis))

    # =============================================================================================
    # Commands
    # =============================================================================================

    def _run(self, description, func, *args, **kwargs):
        """ Run a stage command; refused/failed commands are logged, not raised. """
        with self._lock:
            try:
                return func(*args, **kwargs)
            except ValueError as exc:
                self.log.error('{0} refused: {1}'.format(description, exc))
            except Exception:
                self.log.exception('{0} failed.'.format(description))
            finally:
                self._poll()

    @QtCore.Slot(str, float)
    def move_absolute(self, axis, position):
        self._run('Move of "{0}" to {1}'.format(axis, position),
                  self.stage().move_absolute, axis, position)

    @QtCore.Slot(str, float)
    def move_relative(self, axis, distance):
        self._run('Move of "{0}" by {1}'.format(axis, distance),
                  self.stage().move_relative, axis, distance)

    @QtCore.Slot()
    def stop_all(self):
        # No lock and meant to be called directly (not queued) from the GUI thread: stopping must
        # never wait behind another command.
        try:
            self.stage().stop()
        except Exception:
            self.log.exception('STOP failed.')
        self.log.warning('All axes stopped.')

    @QtCore.Slot(str)
    def stop(self, axis):
        self._run('Stop of "{0}"'.format(axis), self.stage().stop, axis)

    @QtCore.Slot(str, float, float)
    def set_limits(self, axis, lower, upper):
        self._run('Setting limits of "{0}"'.format(axis), self.stage().set_limits, axis, lower, upper)

    @QtCore.Slot(str)
    def home(self, axis):
        self._run('Homing of "{0}"'.format(axis), self.stage().home, axis)

    @QtCore.Slot(str)
    def refresh_motion_params(self, axis):
        params = self._run('Reading motion parameters of "{0}"'.format(axis),
                           self.stage().get_motion_params, axis)
        if params is not None:
            self.sigMotionParamsUpdated.emit(axis, params)

    @QtCore.Slot(str, object)
    def set_motion_params(self, axis, params):
        new = self._run('Setting motion parameters of "{0}"'.format(axis),
                        self.stage().set_motion_params, axis, **params)
        if new is None:
            new = self._run('Reading motion parameters of "{0}"'.format(axis),
                            self.stage().get_motion_params, axis)
        if new is not None:
            self.sigMotionParamsUpdated.emit(axis, new)

    @QtCore.Slot(str)
    def restore_saved_position(self, axis):
        self._run('Restoring the saved position of "{0}"'.format(axis),
                  self.stage().restore_saved_position, axis)

    @QtCore.Slot(str, float)
    def set_position(self, axis, position):
        self._run('Setting the position of "{0}" to {1}'.format(axis, position),
                  self.stage().set_position, axis, position)

    @QtCore.Slot(str)
    def reenable(self, axis):
        self._run('Re-enabling the motor of "{0}"'.format(axis), self.stage().reenable, axis)

    @QtCore.Slot(str)
    def accept_controller_position(self, axis):
        self._run('Accepting the controller position of "{0}"'.format(axis),
                  self.stage().accept_controller_position, axis)
