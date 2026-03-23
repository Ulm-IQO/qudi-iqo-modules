# -*- coding: utf-8 -*-
"""
Sirah Matisse Commander client implementing ScannableLaserInterface.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

from __future__ import annotations

import socket
import struct
import time
from typing import Optional, Tuple

from qtpy import QtCore

from qudi.core.configoption import ConfigOption
from qudi.interface.scannable_laser_interface import (
    ScannableLaserInterface,
    ScannableLaserConstraints,
    ScannableLaserSettings,
    LaserScanMode,
    LaserScanDirection,
)
from qudi.util.constraints import ScalarConstraint


class SirahMatisseCommanderLaser(ScannableLaserInterface):
    """
    This is the Hardware class for the control of sirah matisse laser.

    Example config:

    sirah_matisse:
        module.Class: 'scannable_laser.matisse_laser.SirahMatisseCommanderLaser'
        options:
            address: '127.0.0.1' #IP in Matisse Commander: Matisse -> Communication Options -> Network Server Settings
            port: 5902
            timeout_s: 1.0
            position_bounds: [0.0, 0.65]
            speed_bounds: [0.0, 0.010]
            position_default: 0.3
            speed_default: 0.001
    """
    # Connection
    _address: str = ConfigOption('address', default='127.0.0.1', missing='nothing')
    _port: int = ConfigOption('port', default=5900, missing='nothing')
    _timeout_s: float = ConfigOption('timeout_s', default=1.0, missing='warn')

    # Bounds and defaults (constraints / GUI)
    _value_bounds: Tuple[float, float] = ConfigOption('position_bounds', default=(0.0, 0.65), missing='warn')
    _speed_bounds: Tuple[float, float] = ConfigOption('speed_bounds', default=(0.0, 0.006), missing='warn')
    _speed_default: float = ConfigOption('speed_default', default=0.001, missing='nothing')

    # Default mode (accepts enum, str or int via inline coercion)
    _mode_default: LaserScanMode = ConfigOption(
        'mode_default',
        default=LaserScanMode.CONTINUOUS,
        missing='nothing',
        constructor=lambda v: (
            LaserScanMode[v.strip().upper()] if isinstance(v, str)
            else (LaserScanMode(v) if isinstance(v, int) else v)
        )
    )

    sigPositionChanged = QtCore.Signal(float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock: Optional[socket.socket] = None
        self._constraints: Optional[ScannableLaserConstraints] = None
        self._settings: Optional[ScannableLaserSettings] = None
        self._current_direction: LaserScanDirection = LaserScanDirection.UP

    def on_activate(self):
        # Set constraints
        lo, hi = tuple(self._value_bounds)
        center = 0.5 * (lo + hi)

        value_c = ScalarConstraint(default=center,
                                   bounds=(lo, hi),
                                   increment=1e-4)
        speed_c = ScalarConstraint(default=self._speed_default,
                                   bounds=tuple(self._speed_bounds),
                                   increment=1e-4)
        reps_c = ScalarConstraint(default=1, bounds=(1, 10000), increment=1, enforce_int=True)
        self._constraints = ScannableLaserConstraints(
            value=value_c,
            unit='V',
            speed=speed_c,
            repetitions=reps_c,
            initial_directions=(LaserScanDirection.UP, LaserScanDirection.DOWN),
            modes=(LaserScanMode.CONTINUOUS, LaserScanMode.REPETITIONS),
        )

        # Open socket (no handshake)
        host = (self._address or '').strip()
        port = int(self._port)
        # allow "host:port" in address
        if ':' in host and host.count(':') == 1 and host.rfind(']') == -1:
            maybe_host, maybe_port = host.split(':')
            if maybe_host and maybe_port.isdigit():
                host, port = maybe_host.strip(), int(maybe_port)
        self._sock = socket.create_connection((host, port), timeout=float(self._timeout_s))

        # Default scan settings
        mode = self._mode_default
        self._settings = ScannableLaserSettings(bounds=tuple(self._value_bounds),
                                                speed=self._speed_default,
                                                mode=mode,
                                                repetitions=0,
                                                initial_direction=LaserScanDirection.UP)

    def on_deactivate(self):
        try:
            if self.module_state() == 'locked':
                self.stop_scan()
        finally:
            if self._sock is not None:
                try:
                    # Politely request remote to close (optional; ignore errors)
                    self._send('Close_Network_Connection')
                    time.sleep(0.1)
                except Exception:
                    pass
                try:
                    self._sock.close()
                except Exception:
                    pass
            self._sock = None

    @property
    def constraints(self) -> ScannableLaserConstraints:
        return self._constraints

    @property
    def scan_settings(self) -> ScannableLaserSettings:
        return self._settings

    # -------------------- main API --------------------

    def configure_scan(self,
                       bounds: Tuple[float, float],
                       speed: float,
                       mode: LaserScanMode,
                       repetitions: Optional[int] = 0,
                       initial_direction: Optional[LaserScanDirection] = LaserScanDirection.UNDEFINED) -> None:
        if self.module_state() != 'idle':
            raise RuntimeError('Cannot configure scan while scan is running.')

        lo, hi = float(bounds[0]), float(bounds[1])
        lo = max(self._value_bounds[0], min(lo, self._value_bounds[1]))
        hi = max(self._value_bounds[0], min(hi, self._value_bounds[1]))
        if lo >= hi:
            raise ValueError('bounds min must be < max')
        self._constraints.speed.check(float(speed))

        mode_enum = mode if isinstance(mode, LaserScanMode) else LaserScanMode(int(mode))
        reps = int(repetitions or 0)
        if mode_enum == LaserScanMode.REPETITIONS and reps < 1:
            raise ValueError('repetitions must be >= 1 for REPETITIONS mode')

        init_dir = LaserScanDirection.UP if initial_direction == LaserScanDirection.UNDEFINED \
            else (initial_direction if isinstance(initial_direction, LaserScanDirection)
                  else LaserScanDirection(int(initial_direction)))

        # Program device
        self.set_scan_bounds(lo, hi)
        self.set_scan_speeds(float(speed))

        # Store settings
        self._settings = ScannableLaserSettings(bounds=(lo, hi),
                                                speed=float(speed),
                                                mode=mode_enum,
                                                repetitions=reps,
                                                initial_direction=init_dir)

    def start_scan(self) -> None:
        if self.module_state() == 'locked':
            return
        # Set initial direction and RUN
        self.set_scan_direction(self._settings.initial_direction)
        self._set('SCAN:STA', 'RU')  # RUN
        self.module_state.lock()

    def stop_scan(self) -> None:
        try:
            self._set('SCAN:STA', 'ST')  # STOP
        finally:
            if self.module_state() == 'locked':
                self.module_state.unlock()

    def scan_to(self, value: float, blocking: Optional[bool] = False) -> None:
        """
        Move to a position using the actuator (reference cell scan position).
        Only allowed if not actively scanning.
        """
        if self.module_state() != 'idle':
            raise RuntimeError('Cannot move while scan is running. Stop the scan first.')

        v = float(value)
        v = max(self._value_bounds[0], min(v, self._value_bounds[1]))
        self.set_scan_speeds(self._settings.speed)

        # Move to position
        self._set('SCAN:NOW', v)
        self.sigPositionChanged.emit(v)

        if blocking:
            try:
                t0 = time.time()
                while time.time() - t0 < 5.0:
                    pos = float(self._query('SCAN:NOW?'))
                    if abs(pos - v) < 1e-4:
                        break
                    time.sleep(0.05)
            except Exception:
                pass

    # -------------------- public helpers --------------------

    def set_scan_speeds(self, rising_speed: float, falling_speed: Optional[float] = None) -> None:
        """Set rising and falling speeds. If falling_speed is None, use rising_speed for both."""
        rs = float(rising_speed)
        fs = float(falling_speed) if falling_speed is not None else rs
        self._set('SCAN:RSPD', rs)
        self._set('SCAN:FSPD', fs)

    def set_scan_bounds(self, lower: float, upper: float) -> None:
        """Set lower and upper bounds for the scan window."""
        lo = float(lower)
        hi = float(upper)
        if lo >= hi:
            raise ValueError('lower must be < upper')
        self._set('SCAN:LLM', lo)
        self._set('SCAN:ULM', hi)

    def set_scan_direction(self, direction: LaserScanDirection) -> None:
        """Set scan direction (UP->0, DOWN->1)."""
        if not isinstance(direction, LaserScanDirection):
            direction = LaserScanDirection(int(direction))
        self._set('SCAN:MODE', 0 if direction == LaserScanDirection.UP else 1)
        self._current_direction = direction

    # -------------------- query helpers for logic supervision --------------------

    def is_running(self) -> bool:
        try:
            status = self._query('SCAN:STA?')
            if isinstance(status, str):
                s = status.strip().upper()
                # take last "word" after separators
                last = s.replace(':', ' ').split()[-1] if s else ''
                return last in ('RUN', 'RU')
            return bool(status)
        except Exception:
            return False

    def get_scan_position(self) -> float:
        """Return current actuator position (e.g., applied voltage)"""
        return float(self._query('SCAN:NOW?'))

    # -------------------- low-level I/O -----------------

    def _send(self, data: str) -> None:
        if self._sock is None:
            raise RuntimeError('Not connected to Matisse Commander.')
        payload = data.encode('ascii')
        header = struct.pack('>L', len(payload))
        sent = self._sock.send(header + payload)
        if sent == 0:
            raise RuntimeError('No data sent to server.')

    def _recv(self) -> str:
        if self._sock is None:
            raise RuntimeError('Not connected to Matisse Commander.')
        hdr = b''
        while len(hdr) < 4:
            chunk = self._sock.recv(4 - len(hdr))
            if not chunk:
                raise TimeoutError('No header bytes from server')
            hdr += chunk
        n = struct.unpack('>L', hdr)[0]
        data = b''
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise TimeoutError('Connection closed while reading payload')
            data += chunk
        return data.decode('ascii')

    def _set(self, base_cmd: str, value) -> None:
        """
        Setter: fire-and-forget. No parsing of reply needed.
        """
        s = f'{value:.6f}' if isinstance(value, float) else (f'{value:d}' if isinstance(value, int) else str(value))
        self._send(f'{base_cmd} {s}')
        try:
            # Read and discard reply
            _ = self._recv()
        except Exception:
            # Reply parsing is not required here; keep robust
            pass

    def _query(self, cmd: str):
        """
        Query: returns a primitive (float/str/int) parsed from replies like:
        - 'OK'
        - ':SCAN:NOW: 3.95e-01'
        - 'NOW: 3.95e-01'
        - 'STA: RUN'
        """
        self._send(cmd)
        resp = self._recv()
        content = (resp or '').strip()

        if content.upper() == 'OK':
            return 'OK'

        # Try to extract the payload after the last ':' (supports both ':KEY: value' and 'KEY: value')
        if ':' in content:
            try:
                value = content.rsplit(':', 1)[-1].strip()
            except Exception:
                return content

            vup = value.upper()

            # RUN/STOP (accept variants)
            if vup.startswith(('RUN', 'RU')):
                return 'RUN'
            if vup.startswith(('STOP', 'ST')):
                return 'STOP'

            # TRUE/FALSE
            if vup in ('TRUE', 'FALSE'):
                return vup == 'TRUE'

            # float or int
            try:
                if any(c in value for c in ('.', 'e', 'E')):
                    return float(value)
                return int(value)
            except Exception:
                return value

        return content