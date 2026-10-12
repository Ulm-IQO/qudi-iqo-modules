# -*- coding: utf-8 -*-

"""
Qudi hardware module for a stage made of Thorlabs Kinesis motor controllers (KDC101 K-Cube DC
servo and BSC10x/20x benchtop stepper), e.g. a magnet stage, through the Kinesis C libraries.

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

SAFETY

This module is meant for stages that can crash into other parts of the setup. Therefore:

  - Nothing moves except through move_absolute(), move_relative() and home(). Activation,
    deactivation, changing settings and restoring positions never move a motor.
  - Soft limits: every move target is checked here, in the hardware module. The target, widened
    by the backlash distance on both sides (backlash correction overshoots the target), must lie
    within [lower, upper]. Otherwise the move is refused. An axis seen for the first time gets
    lower = upper = its current position, so it cannot move until limits are set on purpose.
  - Unit conversion: the controllers count in device units (encoder counts / microsteps). The
    conversion counts_per_unit from the config is compared with the conversion Kinesis reports
    for the stage configured in Kinesis. If they differ by more than 0.1 %, if the config has no
    value, or if Kinesis cannot report one, the axis is BLOCKED (log says why and which value
    Kinesis reports). A wrong conversion would move the stage by the wrong distance.
  - Position memory: positions and limits are kept as status variables AND written to a backup
    file after every change (survives a crash of qudi). When the module starts and a
    controller's position differs from the remembered one (e.g. after a power cycle the counter
    restarts at 0), that axis is BLOCKED until the user either sets the controller's counter to
    the remembered position (restore_saved_position) or accepts the controller's value
    (accept_controller_position). Neither moves the motor.
  - Homing drives the stage to its home switch, ignoring the soft limits. It is only done by
    home(); the GUI asks for confirmation first.
  - Motor faults: qudi never disables a motor. If a motor that was enabled becomes disabled
    (the controller cuts it on a stall / following error, e.g. when the stage pushes against an
    obstruction, or after a controller reset), or the connection to the controller is lost, the
    axis gets a FAULT: moves and homing are refused and the position is no longer updated from
    the controller. Only reenable() clears it, never a move command. reenable() does not move;
    if the controller position changed meanwhile (reset), it raises a position mismatch.
    A motor that is disabled at activation (e.g. a stepper after power-up) is still enabled
    automatically by the first move.
  - Referencing: until a controller has been homed, it counts from wherever the stage was at
    power-up, so positions and limits do not refer to the real stage travel. Each axis remembers
    (status variable + backup file) whether its position refers to a home run; get_status()
    reports it as 'referenced' and the GUI shows NOT HOMED otherwise. Restoring the saved
    position keeps the flag, accepting the controller position after a power cycle clears it.
  - Kinesis only accepts targets within the stage travel in controller coordinates, homed or
    not; limits and targets outside it are refused here. set_position() lets the user declare
    where a non-homed axis is (e.g. "at the forward end, 25 mm") so it can move within the travel
    from there: it only sets the position counter (no motion), clears the reference and resets the
    limits to the new position.

The Kinesis software (Thorlabs.MotionControl.Kinesis.exe) must be closed: a controller can only
be used by one program at a time.

Example config for copy-paste:

    magnet_stage:
        module.Class: 'motor.thorlabs_kinesis_stage.ThorlabsKinesisStage'
        options:
            kinesis_path: 'C:/Program Files/Thorlabs/Kinesis'   # optional
            poll_interval_ms: 200        # optional, controller status update interval
            position_tolerance: 0.001    # optional, saved vs. controller position mismatch (unit)
            axes:                        # one entry per axis, any names
                x:
                    serial: '27266421'
                    controller: 'KDC101'     # 'KDC101' (K-Cube DC servo) or 'BSC' (benchtop stepper)
                    unit: 'mm'               # 'mm' or 'deg'
                    counts_per_unit: 34555.0 # device units per mm/deg. Leave out at first: the axis
                                             # is then blocked and the log shows the value Kinesis
                                             # reports for the stage -- check it and enter it here.
                phi:
                    serial: '40531224'
                    controller: 'BSC'
                    channel: 1               # benchtop controllers only, default 1
                    unit: 'deg'
                    counts_per_unit: ...
"""

import ctypes
import json
import math
import os
import time
from typing import Dict, Optional, Tuple

from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex
from qudi.util.paths import get_appdata_dir
from qudi.interface.motor_stage_interface import MotorStageInterface


# Kinesis unit types for GetRealValueFromDeviceUnit / GetDeviceUnitFromRealValue
_DISTANCE, _VELOCITY, _ACCELERATION = 0, 1, 2

# Status bits (same meaning for KDC101 and benchtop stepper controllers)
_BIT_CW_LIMIT = 0x00000001
_BIT_CCW_LIMIT = 0x00000002
_BITS_MOVING = 0x00000010 | 0x00000020 | 0x00000040 | 0x00000080
_BIT_HOMING = 0x00000200
_BIT_HOMED = 0x00000400
_BIT_ENABLED = 0x80000000

# Allowed difference between the configured and Kinesis' counts_per_unit (relative)
_CONVERSION_TOLERANCE = 1e-3


class KinesisAxisBackend:
    """ The only place that calls the Kinesis libraries: one axis = one controller (channel).
    All values in device units. Function names are '<prefix>_<name>'; benchtop controllers take
    the channel as second argument.
    """

    _LIBRARIES = {
        'KDC101': ('Thorlabs.MotionControl.KCube.DCServo.dll', 'CC', False),
        'BSC': ('Thorlabs.MotionControl.Benchtop.StepperMotor.dll', 'SBC', True),
    }
    _loaded = dict()        # dll file name -> [library, device list built]
    _dll_dirs = list()      # keeps os.add_dll_directory() registrations alive

    def __init__(self, kinesis_path: str, controller: str, serial: str, channel: int = 1):
        if controller not in self._LIBRARIES:
            raise ValueError('Unknown controller "{0}", must be one of {1}'.format(
                controller, sorted(self._LIBRARIES)))
        dll, self._prefix, self._has_channel = self._LIBRARIES[controller]
        self._serial = str(serial).encode()
        self._channel = ctypes.c_short(int(channel))
        self._lib = self._load(kinesis_path, dll)

    @classmethod
    def _load(cls, kinesis_path, dll):
        if dll not in cls._loaded:
            cls._dll_dirs.append(os.add_dll_directory(kinesis_path))
            lib = ctypes.CDLL(os.path.join(kinesis_path, dll))
            cls._loaded[dll] = [lib, False]
        entry = cls._loaded[dll]
        if not entry[1]:
            entry[0].TLI_BuildDeviceList()
            entry[1] = True
        return entry[0]

    def _fn(self, name, restype=ctypes.c_short):
        fn = getattr(self._lib, '{0}_{1}'.format(self._prefix, name))
        fn.restype = restype
        return fn

    def _call(self, name, *args, restype=ctypes.c_short, channel=True):
        if channel and self._has_channel:
            args = (self._channel,) + args
        return self._fn(name, restype)(self._serial, *args)

    # motor error codes from the Thorlabs Kinesis error list
    _ERRORS = {37: 'device not homed',
               38: 'invalid position: target outside the stage travel known to Kinesis',
               39: 'invalid velocity parameter'}

    def _check(self, name, *args):
        err = self._call(name, *args)
        if err != 0:
            raise RuntimeError('Kinesis {0}_{1}({2}) failed with error {3}{4}'.format(
                self._prefix, name, self._serial.decode(), err,
                ' ({0})'.format(self._ERRORS[err]) if err in self._ERRORS else ''))

    # connection
    def open(self, poll_interval_ms: int):
        err = self._call('Open', channel=False)
        if err != 0:
            raise RuntimeError(
                'Kinesis {0}_Open({1}) failed with error {2}. Is the controller connected and '
                'is the Kinesis program closed?'.format(self._prefix, self._serial.decode(), err))
        self._call('LoadSettings', restype=ctypes.c_bool)
        self._call('StartPolling', ctypes.c_int(int(poll_interval_ms)), restype=ctypes.c_bool)
        time.sleep(max(0.5, 3 * poll_interval_ms / 1000))
        self._call('ClearMessageQueue')

    def close(self):
        self._call('StopPolling', restype=None)
        self._call('Close', channel=False)

    def is_connected(self) -> bool:
        """ False if the USB connection to the controller is lost (unplugged, reset, ...). """
        return bool(self._call('CheckConnection', restype=ctypes.c_bool, channel=False))

    # state
    def position(self) -> int:
        return self._call('GetPosition', restype=ctypes.c_int)

    def status_bits(self) -> int:
        return self._call('GetStatusBits', restype=ctypes.c_uint32)

    def can_move_without_homing(self) -> bool:
        return bool(self._call('CanMoveWithoutHomingFirst', restype=ctypes.c_bool))

    def travel(self) -> Tuple[int, int]:
        return (self._call('GetStageAxisMinPos', restype=ctypes.c_int),
                self._call('GetStageAxisMaxPos', restype=ctypes.c_int))

    def device_units(self, real: float, unit_type: int) -> int:
        """ Kinesis' own conversion of a real value (mm, deg, mm/s, ...) to device units. """
        out = ctypes.c_int(0)
        self._check('GetDeviceUnitFromRealValue', ctypes.c_double(real), ctypes.byref(out),
                    ctypes.c_int(unit_type))
        return out.value

    def real_value(self, device_units: int, unit_type: int) -> float:
        out = ctypes.c_double(0)
        self._check('GetRealValueFromDeviceUnit', ctypes.c_int(int(device_units)),
                    ctypes.byref(out), ctypes.c_int(unit_type))
        return out.value

    # motion
    def enable(self):
        self._check('EnableChannel')

    def move_to(self, counts: int):
        self._check('MoveToPosition', ctypes.c_int(int(counts)))

    def stop_immediate(self):
        self._check('StopImmediate')

    def home(self):
        self._check('Home')

    def set_position_counter(self, counts: int):
        self._check('SetPositionCounter', ctypes.c_long(int(counts)))

    # motion parameters
    def vel_params(self) -> Tuple[int, int]:
        """ (acceleration, max velocity) in device units """
        self._check('RequestVelParams')
        time.sleep(0.1)
        acc, vel = ctypes.c_int(0), ctypes.c_int(0)
        self._check('GetVelParams', ctypes.byref(acc), ctypes.byref(vel))
        return acc.value, vel.value

    def set_vel_params(self, acceleration: int, velocity: int):
        self._check('SetVelParams', ctypes.c_int(int(acceleration)), ctypes.c_int(int(velocity)))

    def backlash(self) -> int:
        self._check('RequestBacklash')
        time.sleep(0.1)
        return self._call('GetBacklash', restype=ctypes.c_long)

    def set_backlash(self, counts: int):
        self._check('SetBacklash', ctypes.c_long(int(counts)))

    def homing_velocity(self) -> int:
        return self._call('GetHomingVelocity', restype=ctypes.c_uint)

    def set_homing_velocity(self, velocity: int):
        self._check('SetHomingVelocity', ctypes.c_uint(int(velocity)))


class ThorlabsKinesisStage(MotorStageInterface):
    """ Stage of independent axes, each driven by one Thorlabs Kinesis controller (channel).
    See the module docstring for the safety rules and an example config.
    """

    _kinesis_path = ConfigOption(name='kinesis_path', default='C:/Program Files/Thorlabs/Kinesis',
                                 missing='nothing')
    _axes_config = ConfigOption(name='axes', missing='error')
    _poll_interval_ms = ConfigOption(name='poll_interval_ms', default=200, missing='nothing')
    _position_tolerance = ConfigOption(name='position_tolerance', default=1e-3, missing='nothing')

    # {'time': float, 'positions': {axis: float}, 'limits': {axis: [lower, upper]}}
    _stage_state = StatusVar(name='stage_state', default=dict())

    # Backend class, replaceable for offline tests
    _backend_class = KinesisAxisBackend

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = RecursiveMutex()
        self._backends = dict()
        self._axis_cfg = dict()
        self._blocked = dict()
        self._mismatch = dict()
        self._positions = dict()
        self._limits = dict()
        self._faults = dict()           # axis -> reason: motor disabled by the controller, ...
        self._seen_enabled = set()      # axes whose motor was seen enabled since activation
        self._referenced = dict()       # axis -> True if the position refers to a home run
        self._home_started = dict()     # axis -> time.monotonic() of the last home() command
        self._counter_overridden = set()  # axes whose counter qudi set to an unreferenced value

    # =============================================================================================
    # Activation
    # =============================================================================================

    def on_activate(self):
        self._backends, self._blocked, self._mismatch = dict(), dict(), dict()
        self._faults, self._seen_enabled, self._home_started = dict(), set(), dict()
        self._axis_cfg = self._parse_axes_config(self._axes_config)
        state = self._load_state()
        self._positions = dict(state.get('positions', dict()))
        self._limits = {ax: tuple(lim) for ax, lim in state.get('limits', dict()).items()}
        self._referenced = {ax: bool(ref) for ax, ref in state.get('referenced', dict()).items()}
        self._counter_overridden = set(state.get('counter_overridden', list()))

        for axis, cfg in self._axis_cfg.items():
            try:
                backend = self._backend_class(self._kinesis_path, cfg['controller'],
                                              cfg['serial'], cfg['channel'])
                backend.open(int(self._poll_interval_ms))
            except Exception as exc:
                self._blocked[axis] = 'controller not available: {0}'.format(exc)
                self.log.error('Axis "{0}" (S/N {1}): {2}'.format(axis, cfg['serial'], exc))
                continue
            self._backends[axis] = backend
            self._check_conversion(axis)
            self._check_position(axis)
            self.log.info('Axis "{0}" ({1}, S/N {2}): position {3}, limits {4}{5}'.format(
                axis, cfg['controller'], cfg['serial'], self._format(axis, self._positions.get(axis)),
                self._limits.get(axis),
                '' if axis not in self._blocked else ', BLOCKED: ' + self._blocked[axis]))
        self._save_state()

    def on_deactivate(self):
        with self._lock:
            # Nothing may keep moving unobserved after qudi / the module is closed: stop axes
            # still moving and wait for them, so that the saved position is the final one.
            moving = [axis for axis in self._backends if self._moving_bits(axis)]
            if moving:
                self.log.warning('Axes {0} still moving while the stage module closes: stopping '
                                 'them.'.format(moving))
                for axis in moving:
                    self.stop(axis)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and any(self._moving_bits(ax) for ax in moving):
                    time.sleep(0.1)
            for axis in list(self._backends):
                self._update_saved_position(axis)
            self._save_state()
            for axis, backend in self._backends.items():
                try:
                    backend.close()
                except Exception as exc:
                    self.log.warning('Closing axis "{0}" failed: {1}'.format(axis, exc))
            self._backends = dict()

    def _parse_axes_config(self, axes):
        if not isinstance(axes, dict) or not axes:
            raise ValueError('Config option "axes" must be a non-empty dict of axis configs.')
        parsed = dict()
        serials = set()
        for axis, cfg in axes.items():
            axis = str(axis)
            controller = str(cfg.get('controller', '')).upper()
            unit = str(cfg.get('unit', ''))
            serial = str(cfg.get('serial', ''))
            if unit not in ('mm', 'deg'):
                raise ValueError('Axis "{0}": unit must be "mm" or "deg", got "{1}"'.format(axis, unit))
            if not serial:
                raise ValueError('Axis "{0}": serial missing'.format(axis))
            channel = int(cfg.get('channel', 1))
            if (serial, channel) in serials:
                raise ValueError('Axis "{0}": serial {1} channel {2} used twice'.format(
                    axis, serial, channel))
            serials.add((serial, channel))
            cpu = cfg.get('counts_per_unit')
            parsed[axis] = {'serial': serial, 'controller': controller, 'channel': channel,
                            'unit': unit,
                            'counts_per_unit': None if cpu in (None, '') else float(cpu)}
        return parsed

    def _check_conversion(self, axis):
        """ Block the axis unless the configured and Kinesis' counts_per_unit agree. """
        cfg = self._axis_cfg[axis]
        try:
            kinesis_cpu = self._backends[axis].device_units(10.0, _DISTANCE) / 10.0
        except Exception as exc:
            kinesis_cpu = None
            self.log.warning('Axis "{0}": Kinesis conversion not available: {1}'.format(axis, exc))
        cpu = cfg['counts_per_unit']
        if not kinesis_cpu or kinesis_cpu <= 0:
            self._blocked[axis] = (
                'Kinesis reports no unit conversion for this controller: set up the stage type '
                '({0}) in Kinesis first'.format(cfg['unit']))
        elif cpu is None:
            self._blocked[axis] = (
                'counts_per_unit missing in config. Kinesis reports {0:.6g} counts per {1} for '
                'the stage configured in Kinesis: check that this is the right stage and enter '
                'it as counts_per_unit'.format(kinesis_cpu, cfg['unit']))
        elif abs(kinesis_cpu - cpu) > _CONVERSION_TOLERANCE * abs(cpu):
            self._blocked[axis] = (
                'counts_per_unit in config ({0:.6g}) differs from Kinesis ({1:.6g} counts per '
                '{2}): wrong stage type in Kinesis or in the config'.format(
                    cpu, kinesis_cpu, cfg['unit']))
        if axis in self._blocked:
            self.log.error('Axis "{0}" BLOCKED: {1}'.format(axis, self._blocked[axis]))

    def _check_position(self, axis):
        """ Compare the controller position with the remembered one. """
        if axis in self._blocked:
            return
        controller = self._read_position(axis)
        saved = self._positions.get(axis)
        if saved is None:
            self._positions[axis] = controller
            self._referenced.setdefault(axis, False)
        elif abs(saved - controller) > self._position_tolerance:
            self._mismatch[axis] = {'saved': saved, 'controller': controller}
            self.log.warning(
                'Axis "{0}": controller position {1} differs from the saved position {2} (e.g. '
                'after a power cycle). Axis blocked until you restore the saved position or '
                'accept the controller position.'.format(
                    axis, self._format(axis, controller), self._format(axis, saved)))
        if axis not in self._limits:
            pos = self._positions[axis]
            self._limits[axis] = (pos, pos)
            self.log.warning('Axis "{0}": no limits set yet, limits = current position. Set the '
                             'limits before moving.'.format(axis))

    # =============================================================================================
    # Interface
    # =============================================================================================

    @property
    def axes(self):
        return tuple(self._axis_cfg)

    def get_axis_info(self, axis):
        cfg = self._axis_cfg[axis]
        info = {'unit': cfg['unit'], 'serial': cfg['serial'], 'controller': cfg['controller'],
                'channel': cfg['channel'], 'counts_per_unit': cfg['counts_per_unit'],
                'travel': None}
        backend = self._backends.get(axis)
        if backend is not None and cfg['counts_per_unit'] and axis not in self._blocked:
            try:
                lo, hi = backend.travel()
                if hi > lo:
                    info['travel'] = (lo / cfg['counts_per_unit'], hi / cfg['counts_per_unit'])
            except Exception:
                pass
        return info

    def get_position(self, axis):
        with self._lock:
            if axis not in self._backends or axis in self._blocked:
                return self._positions.get(axis)
            self._check_fault(axis)
            if axis in self._faults:
                # the controller value may be stale or reset: keep showing the last good position
                return self._positions.get(axis)
            position = self._read_position(axis)
            self._update_saved_position(axis, position)
            return position

    def get_status(self, axis):
        with self._lock:
            status = {'moving': False, 'homing': False, 'homed': False, 'enabled': False,
                      'cw_limit': False, 'ccw_limit': False,
                      'blocked': self._blocked.get(axis), 'position_mismatch': None,
                      'fault': None, 'connected': axis in self._backends,
                      'referenced': bool(self._referenced.get(axis))}
            if axis in self._backends:
                bits = self._check_fault(axis)
                status.update(moving=bool(bits & _BITS_MOVING), homing=bool(bits & _BIT_HOMING),
                              homed=bool(bits & _BIT_HOMED), enabled=bool(bits & _BIT_ENABLED),
                              cw_limit=bool(bits & _BIT_CW_LIMIT),
                              ccw_limit=bool(bits & _BIT_CCW_LIMIT))
            if axis in self._mismatch:
                status['position_mismatch'] = dict(self._mismatch[axis])
            status['fault'] = self._faults.get(axis)
            status['referenced'] = bool(self._referenced.get(axis))
            return status

    def reenable(self, axis):
        """ Clear a motor fault and enable the motor again. Does not move the axis.
        If the controller lost its position (reset), a position mismatch is raised instead of
        trusting the new controller value. """
        with self._lock:
            if axis not in self._faults:
                raise ValueError('Axis "{0}" has no motor fault'.format(axis))
            backend = self._backends[axis]
            if not backend.is_connected():
                raise ValueError('Axis "{0}": the controller is still not connected. Check cable and '
                                 'power, then reload the stage module.'.format(axis))
            position = self._read_position(axis)
            saved = self._positions.get(axis)
            if saved is not None and abs(position - saved) > self._position_tolerance:
                self._mismatch[axis] = {'saved': saved, 'controller': position}
                self.log.warning(
                    'Axis "{0}": controller position {1} differs from the last known position {2} '
                    '(controller reset?). Resolve the position mismatch before moving.'.format(
                        axis, self._format(axis, position), self._format(axis, saved)))
            before = position
            backend.enable()
            # The controller should hold the current position when the motor is switched on.
            # Verify it: if the axis moves anyway, stop it at once and keep the fault.
            deadline = time.monotonic() + max(0.5, 3 * self._poll_interval_ms / 1000)
            while time.monotonic() < deadline:
                time.sleep(0.1)
                moved = abs(self._read_position(axis) - before) > self._position_tolerance
                if moved or self._moving_bits(axis):
                    self.stop(axis)
                    self._faults[axis] = ('the axis started moving when the motor was re-enabled '
                                          'and was stopped')
                    self.log.error('Axis "{0}": {1}. Position now {2}.'.format(
                        axis, self._faults[axis], self._format(axis, self._read_position(axis))))
                    return
            del self._faults[axis]
            self._seen_enabled.discard(axis)
            self.log.warning('Axis "{0}": motor re-enabled by the user (no motion).'.format(axis))

    def get_limits(self, axis):
        """ (lower, upper), or None if the axis has never had a known position """
        return self._limits.get(axis)

    def set_limits(self, axis, lower, upper):
        with self._lock:
            lower, upper = float(lower), float(upper)
            if not (math.isfinite(lower) and math.isfinite(upper)):
                raise ValueError('Limits must be finite numbers, got [{0}, {1}]'.format(lower, upper))
            if axis in self._backends and self._moving_bits(axis):
                # a running move must not end up outside limits changed underneath it
                raise ValueError('Axis "{0}" is moving: change the limits when it has stopped'.format(axis))
            if lower > upper:
                raise ValueError('Lower limit {0} is above the upper limit {1}'.format(lower, upper))
            unit = self._axis_cfg[axis]['unit']
            travel = self.get_axis_info(axis)['travel']
            # Kinesis only accepts targets within the stage travel in controller coordinates,
            # homed or not, so limits outside it would never be reachable
            if travel is not None and (lower < travel[0] or upper > travel[1]):
                raise ValueError('Limits [{0}, {1}] outside the stage travel [{2:.4f}, {3:.4f}] {4}{5}'.format(
                    lower, upper, travel[0], travel[1], unit, self._not_homed_hint(axis)))
            self._limits[axis] = (lower, upper)
            self._save_state()
            self.log.info('Axis "{0}": limits set to [{1}, {2}] {3}'.format(
                axis, lower, upper, self._axis_cfg[axis]['unit']))
            return self._limits[axis]

    def move_absolute(self, axis, position):
        with self._lock:
            self._start_move(axis, float(position))

    def move_relative(self, axis, distance):
        with self._lock:
            self._check_can_move(axis)
            self._start_move(axis, self._read_position(axis) + float(distance))

    def stop(self, axis=None):
        # Deliberately without the lock: a stop must never wait for another command.
        for name in ([axis] if axis is not None else list(self._backends)):
            backend = self._backends.get(name)
            if backend is None:
                continue
            try:
                backend.stop_immediate()
            except Exception as exc:
                self.log.error('Stopping axis "{0}" failed: {1}'.format(name, exc))

    def home(self, axis):
        with self._lock:
            if axis not in self._backends:
                raise ValueError('Axis "{0}" not connected'.format(axis))
            if axis in self._blocked:
                raise ValueError('Axis "{0}" blocked: {1}'.format(axis, self._blocked[axis]))
            self._check_fault(axis)
            if axis in self._faults:
                raise ValueError('Axis "{0}": {1}. Re-enable the motor first.'.format(
                    axis, self._faults[axis]))
            if self.get_status(axis)['moving']:
                raise ValueError('Axis "{0}" is moving'.format(axis))
            self._ensure_enabled(axis)
            self.log.warning('Axis "{0}": homing started (ignores the soft limits).'.format(axis))
            self._backends[axis].home()
            # homing defines the position anew, so a mismatch is resolved by it; the axis counts
            # as referenced once the controller reports "homed"
            self._mismatch.pop(axis, None)
            self._referenced[axis] = False
            self._home_started[axis] = time.monotonic()
            self._counter_overridden.discard(axis)
            self._save_state()

    def get_motion_params(self, axis):
        with self._lock:
            params = dict.fromkeys(('velocity', 'acceleration', 'backlash', 'homing_velocity'))
            backend = self._backends.get(axis)
            if backend is None or axis in self._blocked:
                return params
            cpu = self._axis_cfg[axis]['counts_per_unit']
            try:
                acc, vel = backend.vel_params()
                params['acceleration'] = backend.real_value(acc, _ACCELERATION)
                params['velocity'] = backend.real_value(vel, _VELOCITY)
            except Exception as exc:
                self.log.debug('Axis "{0}": velocity parameters not available: {1}'.format(axis, exc))
            try:
                params['backlash'] = backend.backlash() / cpu
            except Exception as exc:
                self.log.debug('Axis "{0}": backlash not available: {1}'.format(axis, exc))
            try:
                params['homing_velocity'] = backend.real_value(backend.homing_velocity(), _VELOCITY)
            except Exception as exc:
                self.log.debug('Axis "{0}": homing velocity not available: {1}'.format(axis, exc))
            return params

    def set_motion_params(self, axis, **params):
        with self._lock:
            backend = self._backends.get(axis)
            if backend is None or axis in self._blocked:
                raise ValueError('Axis "{0}" not available'.format(axis))
            if self._moving_bits(axis):
                # e.g. a larger backlash would make a running move overshoot past the limits
                raise ValueError('Axis "{0}" is moving: change motion parameters when it has '
                                 'stopped'.format(axis))
            unknown = set(params) - {'velocity', 'acceleration', 'backlash', 'homing_velocity'}
            if unknown:
                raise ValueError('Unknown motion parameter(s) {0}'.format(sorted(unknown)))
            for key, value in params.items():
                if value is None:
                    continue
                if value < 0 or (value == 0 and key != 'backlash'):
                    raise ValueError('{0} must be {1} 0'.format(key, '>=' if key == 'backlash' else '>'))
            if 'velocity' in params or 'acceleration' in params:
                acc, vel = backend.vel_params()
                if params.get('acceleration') is not None:
                    acc = backend.device_units(params['acceleration'], _ACCELERATION)
                if params.get('velocity') is not None:
                    vel = backend.device_units(params['velocity'], _VELOCITY)
                backend.set_vel_params(acc, vel)
            if params.get('backlash') is not None:
                backend.set_backlash(round(params['backlash'] * self._axis_cfg[axis]['counts_per_unit']))
            if params.get('homing_velocity') is not None:
                backend.set_homing_velocity(backend.device_units(params['homing_velocity'], _VELOCITY))
            new = self.get_motion_params(axis)
            self.log.info('Axis "{0}": motion parameters set to {1}'.format(axis, new))
            return new

    def restore_saved_position(self, axis):
        with self._lock:
            mismatch = self._mismatch.get(axis)
            if mismatch is None:
                raise ValueError('Axis "{0}" has no position mismatch'.format(axis))
            counts = round(mismatch['saved'] * self._axis_cfg[axis]['counts_per_unit'])
            self._backends[axis].set_position_counter(counts)
            time.sleep(max(0.3, 2 * self._poll_interval_ms / 1000))
            now = self._read_position(axis)
            if abs(now - mismatch['saved']) > self._position_tolerance:
                raise RuntimeError('Axis "{0}": controller reads {1} after setting the counter to '
                                   '{2}'.format(axis, now, mismatch['saved']))
            del self._mismatch[axis]
            if not self._referenced.get(axis):
                # the counter now holds a power-up-relative value: a "homed" bit the controller
                # may still report does not apply to it (until the next home())
                self._counter_overridden.add(axis)
            self._update_saved_position(axis, now)
            self.log.info('Axis "{0}": controller position counter set to the saved position '
                          '{1} (no motion).'.format(axis, self._format(axis, now)))

    def set_position(self, axis, position):
        """ Declare the current position of the axis (e.g. "it is at the forward end = 25 mm").
        Only the controller's position counter is changed, the motor does not move. The axis is
        NOT HOMED afterwards and its limits are reset to the new position (the zero point moved).
        """
        with self._lock:
            position = float(position)
            if not math.isfinite(position):
                raise ValueError('Invalid position {0}'.format(position))
            if axis not in self._backends:
                raise ValueError('Axis "{0}" not connected'.format(axis))
            if axis in self._blocked:
                raise ValueError('Axis "{0}" blocked: {1}'.format(axis, self._blocked[axis]))
            self._check_fault(axis)
            if axis in self._faults:
                raise ValueError('Axis "{0}": {1}. Re-enable the motor first.'.format(
                    axis, self._faults[axis]))
            if self._moving_bits(axis):
                raise ValueError('Axis "{0}" is moving'.format(axis))
            unit = self._axis_cfg[axis]['unit']
            travel = self.get_axis_info(axis)['travel']
            if travel is not None and not travel[0] <= position <= travel[1]:
                raise ValueError('Position {0} outside the stage travel [{1:.4f}, {2:.4f}] {3}'.format(
                    position, travel[0], travel[1], unit))
            old = self._read_position(axis)
            self._backends[axis].set_position_counter(
                round(position * self._axis_cfg[axis]['counts_per_unit']))
            time.sleep(max(0.3, 2 * self._poll_interval_ms / 1000))
            now = self._read_position(axis)
            if abs(now - position) > self._position_tolerance:
                raise RuntimeError('Axis "{0}": controller reads {1} after setting the position to '
                                   '{2}'.format(axis, self._format(axis, now), self._format(axis, position)))
            self._mismatch.pop(axis, None)
            self._referenced[axis] = False
            self._counter_overridden.add(axis)
            self._reset_limits(axis, now, 'position set by the user from {0} to {1} (no motion, '
                                          'axis NOT HOMED)'.format(self._format(axis, old),
                                                                   self._format(axis, now)))

    def accept_controller_position(self, axis):
        with self._lock:
            if self._mismatch.pop(axis, None) is None:
                raise ValueError('Axis "{0}" has no position mismatch'.format(axis))
            position = self._read_position(axis)
            # a controller value after a power cycle counts from wherever the stage was then
            self._referenced[axis] = bool(self._backends[axis].status_bits() & _BIT_HOMED)
            self._counter_overridden.discard(axis)
            self._update_saved_position(axis, position)
            self._reset_limits(axis, position, 'controller position {0} accepted'.format(
                self._format(axis, position)))

    # =============================================================================================
    # Helpers
    # =============================================================================================

    def _read_position(self, axis):
        return self._backends[axis].position() / self._axis_cfg[axis]['counts_per_unit']

    def _not_homed_hint(self, axis):
        if self._referenced.get(axis):
            return ''
        return ('. The axis is NOT HOMED: controller coordinates count from the power-up position, '
                'so the stage may physically reach beyond this range in one direction and not at '
                'all in the other. Home the axis to make the coordinates match the stage.')

    def _moving_bits(self, axis):
        """ Moving/homing bits of the axis, 0 if they cannot be read. """
        try:
            return self._backends[axis].status_bits() & (_BITS_MOVING | _BIT_HOMING)
        except Exception:
            return 0

    def _reset_limits(self, axis, position, reason):
        """ The coordinate origin changed (homed, or power-up position accepted): the old limits
        point somewhere else now, so they are replaced by lower = upper = position. """
        self._positions[axis] = position
        self._limits[axis] = (position, position)
        self._save_state()
        self.log.warning('Axis "{0}": {1}. The zero point changed, so the limits were reset to the '
                         'current position {2}: set new limits before moving.'.format(
                             axis, reason, self._format(axis, position)))

    def _check_fault(self, axis):
        """ Detect a lost connection or a motor the controller disabled by itself (stall,
        obstruction, controller reset). A faulted axis refuses moves and homing until the user
        re-enables it with reenable(); it is never re-enabled automatically.
        @return int: status bits (0 if the controller is not connected)
        """
        backend = self._backends[axis]
        if not backend.is_connected():
            if axis not in self._faults:
                self._faults[axis] = ('connection to the controller lost (controller reset, power '
                                      'or USB?). Last known position {0}'.format(
                                          self._format(axis, self._positions.get(axis))))
                self.log.error('Axis "{0}": {1}'.format(axis, self._faults[axis]))
            return 0
        bits = backend.status_bits()
        if axis in self._home_started:
            # after home(): wait until the controller has visibly started homing (or a few
            # seconds passed), so a stale "homed" bit from before is not taken as the new result
            if bits & _BIT_HOMING or time.monotonic() - self._home_started[axis] > 5.0:
                del self._home_started[axis]
        elif (bits & _BIT_HOMED and not bits & _BIT_HOMING and not self._referenced.get(axis)
              and axis not in self._mismatch and axis not in self._counter_overridden):
            self._referenced[axis] = True
            self._reset_limits(axis, self._read_position(axis),
                               'homed, positions now refer to the home switch')
        if bits & _BIT_ENABLED:
            self._seen_enabled.add(axis)
        elif axis in self._seen_enabled and axis not in self._faults:
            # qudi never disables a motor, so the controller did (stall / following error,
            # obstruction, reset, front panel)
            self._faults[axis] = ('motor disabled by the controller (stall or obstruction?) at '
                                  '{0}'.format(self._format(axis, self._positions.get(axis))))
            self.log.error('Axis "{0}": {1}. Moves are blocked: check the stage mechanically, '
                           'then re-enable the motor in the GUI.'.format(axis, self._faults[axis]))
        return bits

    def _check_can_move(self, axis):
        if axis not in self._axis_cfg:
            raise ValueError('Unknown axis "{0}"'.format(axis))
        if axis not in self._backends:
            raise ValueError('Axis "{0}" not connected'.format(axis))
        if axis in self._blocked:
            raise ValueError('Axis "{0}" blocked: {1}'.format(axis, self._blocked[axis]))
        self._check_fault(axis)
        if axis in self._faults:
            raise ValueError('Axis "{0}": {1}. Re-enable the motor first.'.format(
                axis, self._faults[axis]))
        if axis in self._mismatch:
            raise ValueError('Axis "{0}": resolve the position mismatch first'.format(axis))
        if self.get_status(axis)['moving']:
            raise ValueError('Axis "{0}" is already moving'.format(axis))

    def _start_move(self, axis, target):
        self._check_can_move(axis)
        if not math.isfinite(target):
            raise ValueError('Axis "{0}": invalid target {1}'.format(axis, target))
        if self._limits.get(axis) is None:
            raise ValueError('Axis "{0}": no limits set'.format(axis))
        lower, upper = self._limits[axis]
        travel = self.get_axis_info(axis)['travel']
        if travel is not None and not travel[0] - 1e-9 <= target <= travel[1] + 1e-9:
            raise ValueError('Axis "{0}": target {1} outside the stage travel [{2:.4f}, {3:.4f}] {4}, '
                             'Kinesis would refuse it{5}'.format(
                                 axis, target, travel[0], travel[1], self._axis_cfg[axis]['unit'],
                                 self._not_homed_hint(axis)))
        cpu = self._axis_cfg[axis]['counts_per_unit']
        try:
            backlash = abs(self._backends[axis].backlash()) / cpu
        except Exception as exc:
            # unknown overshoot: the limit check below would not be reliable
            raise ValueError('Axis "{0}": cannot read the backlash from the controller, move '
                             'refused: {1}'.format(axis, exc))
        if target - backlash < lower - 1e-9 or target + backlash > upper + 1e-9:
            raise ValueError(
                'Axis "{0}": target {1} (with backlash {2}) outside the limits [{3}, {4}] {5}'.format(
                    axis, target, backlash, lower, upper, self._axis_cfg[axis]['unit']))
        # counts rounded towards the inside of the limits, so the target never lies outside them
        counts = round(target * cpu)
        if (counts / cpu) - backlash < lower:
            counts += 1
        elif (counts / cpu) + backlash > upper:
            counts -= 1
        if (counts / cpu) - backlash < lower or (counts / cpu) + backlash > upper:
            raise ValueError('Axis "{0}": no controller position for target {1} within the limits '
                             '[{2}, {3}]'.format(axis, target, lower, upper))
        self._ensure_enabled(axis)
        self._backends[axis].move_to(counts)
        self.log.info('Axis "{0}": moving to {1}'.format(axis, self._format(axis, target)))

    def _ensure_enabled(self, axis):
        if not self._backends[axis].status_bits() & _BIT_ENABLED:
            self._backends[axis].enable()
            time.sleep(0.2)

    def _update_saved_position(self, axis, position=None):
        if (axis not in self._backends or axis in self._blocked or axis in self._mismatch
                or axis in self._faults):
            return
        if position is None:
            position = self._read_position(axis)
        if self._positions.get(axis) != position:
            self._positions[axis] = position
            self._save_state()

    def _format(self, axis, value):
        if value is None:
            return 'unknown'
        return '{0:.4f} {1}'.format(value, self._axis_cfg[axis]['unit'])

    # ---- persistence: status variable + backup file ---------------------------------------------

    @property
    def _backup_file(self):
        return os.path.join(get_appdata_dir(), 'stage-{0}.json'.format(self.module_name))

    def _load_state(self):
        """ Newest of status variable and backup file. """
        candidates = [dict(self._stage_state or dict())]
        try:
            with open(self._backup_file, 'r') as file:
                candidates.append(json.load(file))
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.log.warning('Could not read stage backup file: {0}'.format(exc))
        return max(candidates, key=lambda s: s.get('time', 0))

    def _save_state(self):
        state = {'time': time.time(),
                 'positions': {ax: float(p) for ax, p in self._positions.items()},
                 'limits': {ax: [float(lim[0]), float(lim[1])] for ax, lim in self._limits.items()},
                 'referenced': {ax: bool(ref) for ax, ref in self._referenced.items()},
                 'counter_overridden': sorted(self._counter_overridden)}
        self._stage_state = state
        try:
            tmp = self._backup_file + '.tmp'
            with open(tmp, 'w') as file:
                json.dump(state, file, indent=1)
            os.replace(tmp, self._backup_file)
        except Exception as exc:
            self.log.warning('Could not write stage backup file: {0}'.format(exc))
