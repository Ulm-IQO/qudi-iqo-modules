# -*- coding: utf-8 -*-
"""
Attocube AMC300 stepper-based scanning probe hardware for Qudi.

Implements ScanningProbeInterface for motion only (stepping; no analog fine output).
Units:
- Qudi external API uses meters.
- AMC300 API uses nanometers for linear axes. We convert m <-> nm.

Key Python API calls (from AMC Interface Manual):
    import AMC
    dev = AMC.Device(ip)
    dev.connect()
    dev.close()

    # Motion
    dev.move.setNSteps(axis, backward, step)
    dev.move.setSingleStep(axis, backward)
    dev.move.getPosition(axis) -> position_nm
    dev.status.getStatusMoving(axis) -> 0 idle, 1 moving, 2 pending
    dev.control.setControlOutput(axis, enable)

Example config:

hardware:
    amc300_stepper:
        module.Class: 'attocube.AMC300_stepper.AMC300_stepper'
        options:
            ip_address: '192.168.1.1'
            port: 9090
            axis_map: { x: 0, y: 1, z: 2 }
            step_size_m: { x: 2e-7, y: 2e-7, z: 2e-7 }   # meters per step
            position_ranges:
                x: [1.5e-3, 4.5e-3]
                y: [1.5e-3, 4.5e-3]
                z: [1.5e-3, 4.5e-3]
            frequency_ranges:
                x: [1, 500]
                y: [1, 500]
                z: [1, 100]
            resolution_ranges:
                x: [1, 100000]
                y: [1, 100000]
                z: [1, 100000]
            input_channel_units:
                APD2: 'c/s'
            drive_enable_on_activate: false
            settle_time_s: 0.001
            max_move_timeout_s: 5.0
"""

from __future__ import annotations

import time
import math
from typing import Dict, List, Optional

from PySide2 import QtCore

from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.interface.scanning_probe_interface import (
    ScanningProbeInterface,
    ScanConstraints,
    ScannerAxis,
    ScannerChannel,
    ScanSettings,
    ScanData,
    BackScanCapability,
)
from qudi.util.constraints import ScalarConstraint

#from AMC_API import AMC

class AMC300_stepper(ScanningProbeInterface):
    _threaded = True

    # Connection/config
    _ip_address: str = ConfigOption('ip_address', default='127.0.0.1', missing='error')
    _port: int = ConfigOption('port', default=9090, missing='warn')

    # Axis mapping and step sizes
    _axis_map: Dict[str, int] = ConfigOption('axis_map', default={'x': 0, 'y': 1, 'z': 2}, missing='warn')
    _step_size_m: Dict[str, float] = ConfigOption('step_size_m', missing='error')  # meters/step

    # Constraints
    _position_ranges: Dict[str, List[float]] = ConfigOption('position_ranges', missing='error')
    _frequency_ranges: Dict[str, List[float]] = ConfigOption('frequency_ranges', default={}, missing='warn')
    _resolution_ranges: Dict[str, List[int]] = ConfigOption('resolution_ranges', default={}, missing='warn')
    _input_channel_units: Dict[str, str] = ConfigOption('input_channel_units', default={}, missing='warn')

    # Behavior
    _drive_enable_on_activate: bool = ConfigOption('drive_enable_on_activate', default=True, missing='warn')
    _settle_time_s: float = ConfigOption('settle_time_s', default=0.001, missing='warn')
    _max_move_timeout_s: float = ConfigOption('max_move_timeout_s', default=5.0, missing='warn')

    sigPositionChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        self._dev = None  # AMC.Device instance
        self._target_m: Dict[str, float] = {ax: 0.0 for ax in self._axis_map}

    # Lifecycle
    def on_activate(self):

        # Sanity check
        # TODO check that config values within fsio range?
        assert set(self._position_ranges) == set(self._frequency_ranges) == set(self._resolution_ranges), \
            f'Channels in position ranges, frequency ranges and resolution ranges do not coincide'

        #contraints
        axes = list()
        for axis in self._position_ranges:
            position_range = tuple(self._position_ranges[axis])
            resolution_range = tuple(self._resolution_ranges[axis])
            res_default = 50
            if not resolution_range[0] <= res_default <= resolution_range[1]:
                res_default = resolution_range[0]
            frequency_range = tuple(self._frequency_ranges[axis])
            freq_default = 500
            if not frequency_range[0] <= freq_default <= frequency_range[1]:
                freq_default = frequency_range[0]
            max_step = abs(position_range[1] - position_range[0])

            position = ScalarConstraint(default=min(position_range), bounds=position_range)
            resolution = ScalarConstraint(default=res_default, bounds=resolution_range, enforce_int=True)
            frequency = ScalarConstraint(default=freq_default, bounds=frequency_range)
            step = ScalarConstraint(default=0, bounds=(0, max_step))

            axes.append(ScannerAxis(name=axis,
                                    unit='m',
                                    position=position,
                                    step=step,
                                    resolution=resolution,
                                    frequency=frequency, )
                        )
        channels = list()
        for channel, unit in self._input_channel_units.items():
            channels.append(ScannerChannel(name=channel,
                                           unit=unit,
                                           dtype='float64'))

        back_scan_capability = BackScanCapability.AVAILABLE | BackScanCapability.RESOLUTION_CONFIGURABLE
        # cap = BackScanCapability.AVAILABLE if self._back_scan_available else BackScanCapability.NOT_AVAILABLE
        self._constraints = ScanConstraints(axis_objects=tuple(axes),
                                            channel_objects=tuple(channels),
                                            back_scan_capability=back_scan_capability,
                                            has_position_feedback=False,
                                            square_px_only=False)

        try:
            from qudi.hardware.attocube.AMC_API import AMC  #import AMC  # Provided by Attocube
        except Exception as exc:
            raise RuntimeError('AMC300_stepper: Could not import AMC Python package') from exc

        with self._thread_lock:
            self._dev = AMC.Device(self._ip_address)
            self._dev.connect()
            # Optionally enable drives
            if self._drive_enable_on_activate:
                for ax, ch in self._axis_map.items():
                    try:
                        self._dev.control.setControlOutput(ch, True)
                    except Exception:
                        # Some axes or configs may fail here; log but continue
                        self.log.warning(f'AMC: setControlOutput failed for axis {ax}')

    def on_deactivate(self):
        with self._thread_lock:
            try:
                if self._dev is not None:
                    # Try to stop outputs gracefully (optional)
                    for ax, ch in self._axis_map.items():
                        try:
                            self._dev.control.setControlOutput(ch, False)
                        except Exception:
                            pass
            finally:
                try:
                    if self._dev is not None:
                        self._dev.close()
                except Exception:
                    pass
                self._dev = None

    # ScanningProbeInterface: constraints and configuration
    @property
    def constraints(self) -> ScanConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        return self._constraints

    def reset(self) -> None:
        # Stop outputs if possible
        with self._thread_lock:
            if self._dev is not None:
                for ax, ch in self._axis_map.items():
                    try:
                        self._dev.control.setControlOutput(ch, False)
                    except Exception:
                        pass

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        return None

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        return None

    def configure_scan(self, settings: ScanSettings) -> None:
        # Motion-only module does not perform scans by itself
        raise RuntimeError('AMC300_stepper is motion-only. Use AMC300NIScanningProbeInterfuse for scanning.')

    def configure_back_scan(self, settings: ScanSettings) -> None:
        return

    # Movement
    def move_absolute(self, position: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        with self._thread_lock:
            dev = self._require_dev()
            for ax, target_m in position.items():
                ch = self._axis_to_channel(ax)
                step_m = float(self._step_size_m[ax])
                # Current position from device in meters (fallback to target)
                try:
                    pos_nm = float(dev.move.getPosition(ch))
                    pos_m = pos_nm * 1e-9
                except Exception:
                    pos_m = self._target_m.get(ax, 0.0)
                # Clip and compute steps
                tgt_m = self._clip(ax, float(target_m))
                delta = tgt_m - pos_m
                n_steps = int(round(delta / step_m))
                if n_steps == 0:
                    self._target_m[ax] = pos_m
                    continue
                backward = True if n_steps < 0 else False
                steps = abs(n_steps)
                # Try bulk step; fallback to single-step loop
                try:
                    dev.move.setNSteps(ch, backward, steps)
                except Exception:
                    for _ in range(steps):
                        dev.move.setSingleStep(ch, backward)
                self._target_m[ax] = pos_m + n_steps * step_m

        if blocking:
            for ax in position.keys():
                self._wait_axis_idle(ax, self._max_move_timeout_s)
            time.sleep(self._settle_time_s)

        self.sigPositionChanged.emit(dict(self._target_m))
        return dict(self._target_m)

    def move_relative(self, distance: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        with self._thread_lock:
            curr = self.get_target()
        absolute = {ax: curr.get(ax, 0.0) + float(d) for ax, d in distance.items()}
        return self.move_absolute(absolute, velocity=velocity, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        with self._thread_lock:
            return dict(self._target_m)

    def get_position(self) -> Dict[str, float]:
        with self._thread_lock:
            dev = self._require_dev()
            pos = {}
            for ax, ch in self._axis_map.items():
                try:
                    nm = float(dev.move.getPosition(ch))
                    pos[ax] = nm * 1e-9
                except Exception:
                    pos[ax] = self._target_m.get(ax, 0.0)
            return pos

    # Scan lifecycle (not used here)
    def start_scan(self) -> None:
        raise RuntimeError('AMC300_stepper does not implement scanning.')

    def stop_scan(self) -> None:
        return

    def get_scan_data(self) -> Optional[ScanData]:
        return None

    def get_back_scan_data(self) -> Optional[ScanData]:
        return None

    def emergency_stop(self) -> None:
        with self._thread_lock:
            if self._dev is None:
                return
            for ax, ch in self._axis_map.items():
                try:
                    # No dedicated "stop" method; disable output as a soft stop
                    self._dev.control.setControlOutput(ch, False)
                except Exception:
                    pass

    # Helpers
    def _require_dev(self):
        if self._dev is None:
            raise RuntimeError('AMC device not connected')
        return self._dev

    def _axis_to_channel(self, axis: str) -> int:
        if axis not in self._axis_map:
            raise KeyError(f'Unknown axis "{axis}"')
        return int(self._axis_map[axis])

    def _clip(self, axis: str, value: float) -> float:
        rng = self._position_ranges.get(axis)
        if not rng or len(rng) < 2:
            return value
        return min(max(value, float(rng[0])), float(rng[1]))

    def _wait_axis_idle(self, axis: str, timeout_s: float):
        ch = self._axis_to_channel(axis)
        t0 = time.time()
        while True:
            try:
                status = int(self._dev.status.getStatusMoving(ch))  # type: ignore
                moving = (status != 0)
            except Exception:
                # Fallback: assume settle_time sufficient
                time.sleep(self._settle_time_s)
                return
            if not moving:
                return
            if time.time() - t0 > timeout_s:
                self.log.warning(f'AMC axis {axis}: wait idle timeout.')
                return
            time.sleep(0.002)