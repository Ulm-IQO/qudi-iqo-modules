# -*- coding: utf-8 -*-
"""
Attocube AMC300 stepper-based scanning probe hardware for Qudi.

Quickstart:
----------
This module provides motion control for the Attocube AMC300 stepper positioner as part of 
a confocal scanning microscope setup. It implements ScanningProbeInterface for motion only 
(stepping; no analog fine output). Use with AMC300NIScanningProbeInterfuse for full scanning.

Purpose: Provides precise 3-axis motion control for confocal microscopy where:
- Motion is via Attocube AMC300 stepper controllers (replaces NI analog output)
- Fluorescence is still measured via NI-DAQ APD channels
- Scans are software-stepped: move → settle → acquire → repeat

Units and Conventions:
- Qudi external API uses meters for all positions
- AMC300 API uses nanometers internally - conversion handled automatically
- step_size_m defines meters per step for each axis
- position_ranges define valid position bounds in meters

Configuration Example:
--------------------
hardware:
    amc300_stepper:
        module.Class: 'attocube.AMC300_stepper.AMC300_stepper'
        options:
            ip_address: '192.168.1.1'      # AMC300 controller IP
            port: 9090                     # AMC300 communication port
            axis_map: { x: 0, y: 1, z: 2 } # Qudi axis names to AMC300 channels
            step_size_m: { x: 2e-7, y: 2e-7, z: 2e-7 }   # meters per step
            position_ranges:               # Valid position bounds in meters
                x: [1.5e-3, 4.5e-3]      # 1.5mm to 4.5mm range
                y: [1.5e-3, 4.5e-3]
                z: [1.5e-3, 4.5e-3]  
            frequency_ranges:              # Motion frequency limits (Hz)
                x: [1, 500]
                y: [1, 500]
                z: [1, 100]
            resolution_ranges:             # Resolution limits (steps)
                x: [1, 100000]
                y: [1, 100000]
                z: [1, 100000]
            input_channel_units:           # For constraint compatibility
                APD2: 'c/s'
            drive_enable_on_activate: false  # Enable drives on activation
            settle_time_s: 0.001            # Time to wait after motion
            max_move_timeout_s: 5.0         # Max time to wait for motion completion
            simulation: false               # Set true to bypass hardware

Attocube AMC300 Notes:
---------------------
The AMC300 controller provides these key commands referenced by this module:
- Device connection: AMC.Device(ip_address) then dev.connect()
- Motion commands: dev.move.setNSteps(axis, backward, steps) for bulk motion
                  dev.move.setSingleStep(axis, backward) for single steps
- Position readout: dev.move.getPosition(axis) returns position in nanometers
- Motion status: dev.status.getStatusMoving(axis) returns 0=idle, 1=moving, 2=pending
- Drive control: dev.control.setControlOutput(axis, enable) to enable/disable drives
- Connection cleanup: dev.close() to release network connection

Motion Sequencing:
- All moves wait until AMC300 reports ready (status != moving) before proceeding
- Additional settle_time_s is applied after motion completes
- Position validation clips to position_ranges with warnings when exceeded

NI Integration Notes:
--------------------
This module is motion-only. For scanning with NI data acquisition, use:
- AMC300NIScanningProbeInterfuse connects this module + NIXSeriesInStreamer
- Fluorescence measured on PFI8 via NI hardware, motion via AMC300
- Pixel sequence: move → wait ready → settle → NI acquire → aggregate → next pixel

Troubleshooting:
---------------
Connection Issues:
- Check ip_address and port match AMC300 network settings
- Verify AMC300 is powered and network accessible (ping test)
- Check firewall settings allow connection to port 9090
- Look for "Could not import AMC Python package" - install AMC_API from Attocube

Motion Issues:  
- "Axis disabled/not ready" - check drive_enable_on_activate or manually enable drives
- Motion timeout warnings - increase max_move_timeout_s or check mechanical binding
- Position out of bounds - verify position_ranges match physical travel limits

Range Validation Errors:
- "Value X.X is out of bounds" - check scan ranges vs position_ranges in config
- Positions automatically clipped to valid ranges with warnings logged
- Use position_ranges to match your actual mechanical travel limits

Missing Hardware:
- Use simulation: true to bypass hardware for testing GUI/logic integration
- Set environment variable AMC300_SIM=1 as alternative simulation trigger
- Simulation provides predictable fake positions and instant ready status
"""

from __future__ import annotations

import os
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
    _simulation: bool = ConfigOption('simulation', default=False, missing='warn')

    sigPositionChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        self._dev = None  # AMC.Device instance or None in simulation
        self._target_m: Dict[str, float] = {}
        # Check for simulation mode from environment as well
        self._sim_mode = self._simulation or os.environ.get('AMC300_SIM', '').lower() in ('1', 'true', 'yes')

    # Lifecycle
    def on_activate(self):
        # Initialize target positions with default safe values
        self._target_m = {}
        for ax in self._axis_map:
            pos_range = self._position_ranges.get(ax, [0.0, 0.001])  # fallback range
            # Use middle of range as safe default, or current position if available
            self._target_m[ax] = float(pos_range[0] + pos_range[1]) / 2.0

        # Validate configuration
        if not self._axis_map:
            raise ValueError("axis_map cannot be empty")
        if not self._step_size_m:
            raise ValueError("step_size_m must be specified for all axes")
        if not self._position_ranges:
            raise ValueError("position_ranges must be specified for all axes")
            
        # Check that all required axes have configuration
        for ax in self._axis_map:
            if ax not in self._step_size_m:
                raise ValueError(f"step_size_m missing for axis '{ax}'")
            if ax not in self._position_ranges:
                raise ValueError(f"position_ranges missing for axis '{ax}'")

        # Build constraints
        axes = list()
        for axis in self._axis_map.keys():
            position_range = tuple(self._position_ranges[axis])
            resolution_range = tuple(self._resolution_ranges.get(axis, [1, 100000]))
            frequency_range = tuple(self._frequency_ranges.get(axis, [1, 1000]))
            
            # Safe defaults within ranges
            res_default = 50
            if not resolution_range[0] <= res_default <= resolution_range[1]:
                res_default = resolution_range[0]
            freq_default = 500
            if not frequency_range[0] <= freq_default <= frequency_range[1]:
                freq_default = frequency_range[0]
            max_step = abs(position_range[1] - position_range[0])

            position = ScalarConstraint(default=self._target_m[axis], bounds=position_range)
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
        
        # Build channel constraints (for compatibility with scanning interface)
        channels = list()
        for channel, unit in self._input_channel_units.items():
            channels.append(ScannerChannel(name=channel,
                                           unit=unit,
                                           dtype='float64'))

        back_scan_capability = BackScanCapability.AVAILABLE | BackScanCapability.RESOLUTION_CONFIGURABLE
        self._constraints = ScanConstraints(axis_objects=tuple(axes),
                                            channel_objects=tuple(channels),
                                            back_scan_capability=back_scan_capability,
                                            has_position_feedback=False,
                                            square_px_only=False)

        # Connect to hardware or enter simulation mode
        if self._sim_mode:
            self.log.info("AMC300_stepper: Running in simulation mode")
            self._dev = None  # No hardware connection in simulation
        else:
            try:
                # Lazy import with clear error message
                from qudi.hardware.attocube.AMC_API import AMC
            except ImportError as exc:
                raise RuntimeError(
                    'AMC300_stepper: Could not import AMC Python package. '
                    'Please install the Attocube AMC API or enable simulation mode.'
                ) from exc

            try:
                with self._thread_lock:
                    self._dev = AMC.Device(self._ip_address)
                    self._dev.connect()
                    
                    # Optionally enable drives
                    if self._drive_enable_on_activate:
                        for ax, ch in self._axis_map.items():
                            try:
                                self._dev.control.setControlOutput(ch, True)
                                self.log.debug(f'AMC300: Enabled drive for axis {ax} (channel {ch})')
                            except Exception as exc:
                                # Some axes or configs may fail; log but continue
                                self.log.warning(f'AMC300: Failed to enable drive for axis {ax}: {exc}')
                                
                    # Try to read initial positions from hardware
                    try:
                        hw_positions = self._read_hardware_positions()
                        for ax, pos_m in hw_positions.items():
                            if self._is_position_valid(ax, pos_m):
                                self._target_m[ax] = pos_m
                            else:
                                self.log.warning(
                                    f'AMC300: Hardware position {pos_m*1e6:.1f}µm for axis {ax} '
                                    f'outside configured range, using default'
                                )
                    except Exception as exc:
                        self.log.warning(f'AMC300: Could not read initial positions: {exc}')
                        
            except Exception as exc:
                self._dev = None
                raise RuntimeError(f'AMC300_stepper: Failed to connect to AMC300 at {self._ip_address}:{self._port}') from exc

    def on_deactivate(self):
        with self._thread_lock:
            if not self._sim_mode and self._dev is not None:
                try:
                    # Try to stop outputs gracefully (optional)
                    for ax, ch in self._axis_map.items():
                        try:
                            self._dev.control.setControlOutput(ch, False)
                            self.log.debug(f'AMC300: Disabled drive for axis {ax}')
                        except Exception as exc:
                            self.log.debug(f'AMC300: Failed to disable drive for axis {ax}: {exc}')
                except Exception as exc:
                    self.log.warning(f'AMC300: Error during drive shutdown: {exc}')
                finally:
                    try:
                        if self._dev is not None:
                            self._dev.close()
                            self.log.debug('AMC300: Connection closed')
                    except Exception as exc:
                        self.log.warning(f'AMC300: Error closing connection: {exc}')
                    self._dev = None

    # ScanningProbeInterface: constraints and configuration
    @property
    def constraints(self) -> ScanConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        return self._constraints

    def reset(self) -> None:
        """Reset/stop all motion."""
        with self._thread_lock:
            if self._sim_mode:
                return  # Nothing to reset in simulation
            if self._dev is not None:
                for ax, ch in self._axis_map.items():
                    try:
                        self._dev.control.setControlOutput(ch, False)
                        time.sleep(0.01)  # Brief pause
                        if self._drive_enable_on_activate:
                            self._dev.control.setControlOutput(ch, True)
                    except Exception as exc:
                        self.log.warning(f'AMC300: Reset failed for axis {ax}: {exc}')

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
        """Move to absolute positions.
        
        Args:
            position: Dict of axis_name -> target_position_in_meters
            velocity: Ignored (stepper has no continuous velocity)
            blocking: If True, wait until motion completes and settle time elapses
            
        Returns:
            Dict of axis_name -> actual_target_position_in_meters
        """
        if not position:
            return dict(self._target_m)
            
        with self._thread_lock:
            if self._sim_mode:
                # Simulation mode: instant moves with validation
                for ax, target_m in position.items():
                    if ax not in self._axis_map:
                        raise KeyError(f'Unknown axis "{ax}"')
                    clipped = self._clip_position(ax, float(target_m))
                    self._target_m[ax] = clipped
            else:
                # Hardware mode
                dev = self._require_dev()
                for ax, target_m in position.items():
                    if ax not in self._axis_map:
                        raise KeyError(f'Unknown axis "{ax}"')
                        
                    ch = self._axis_to_channel(ax)
                    step_m = float(self._step_size_m[ax])
                    
                    # Get current position from device (fallback to cached target)
                    try:
                        pos_nm = float(dev.move.getPosition(ch))
                        pos_m = pos_nm * 1e-9
                    except Exception as exc:
                        self.log.debug(f'AMC300: Could not read position for axis {ax}: {exc}')
                        pos_m = self._target_m.get(ax, 0.0)
                    
                    # Validate and clip target position
                    tgt_m = self._clip_position(ax, float(target_m))
                    delta = tgt_m - pos_m
                    n_steps = int(round(delta / step_m))
                    
                    if n_steps == 0:
                        self._target_m[ax] = pos_m
                        continue
                        
                    backward = n_steps < 0
                    steps = abs(n_steps)
                    
                    # Execute motion - try bulk first, fallback to single steps
                    try:
                        dev.move.setNSteps(ch, backward, steps)
                        self.log.debug(f'AMC300: Moving axis {ax} by {n_steps} steps '
                                     f'({delta*1e6:.2f}µm)')
                    except Exception as exc:
                        self.log.debug(f'AMC300: Bulk move failed for axis {ax}, using single steps: {exc}')
                        for _ in range(steps):
                            try:
                                dev.move.setSingleStep(ch, backward)
                            except Exception as step_exc:
                                self.log.error(f'AMC300: Single step failed for axis {ax}: {step_exc}')
                                break
                    
                    # Update target position
                    self._target_m[ax] = pos_m + n_steps * step_m

            # Wait for completion if blocking
            if blocking:
                if self._sim_mode:
                    time.sleep(self._settle_time_s)  # Just settle time in simulation
                else:
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
        """Get current positions from hardware or simulation.
        
        Returns:
            Dict of axis_name -> current_position_in_meters
        """
        with self._thread_lock:
            if self._sim_mode:
                # In simulation, return target positions (instant moves)
                return dict(self._target_m)
            else:
                return self._read_hardware_positions()

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
        """Emergency stop all motion."""
        with self._thread_lock:
            if self._sim_mode:
                return  # Nothing to stop in simulation
            if self._dev is None:
                return
            for ax, ch in self._axis_map.items():
                try:
                    # No dedicated "stop" method; disable output as emergency stop
                    self._dev.control.setControlOutput(ch, False)
                    self.log.info(f'AMC300: Emergency stop - disabled drive for axis {ax}')
                except Exception as exc:
                    self.log.error(f'AMC300: Emergency stop failed for axis {ax}: {exc}')

    # Helper methods
    def _require_dev(self):
        """Get hardware device, raising error if not available."""
        if self._sim_mode:
            raise RuntimeError('Hardware operations not available in simulation mode')
        if self._dev is None:
            raise RuntimeError('AMC300 device not connected')
        return self._dev

    def _axis_to_channel(self, axis: str) -> int:
        """Convert axis name to AMC300 channel number."""
        if axis not in self._axis_map:
            raise KeyError(f'Unknown axis "{axis}"')
        return int(self._axis_map[axis])

    def _clip_position(self, axis: str, value: float) -> float:
        """Clip position to valid range, logging if clipping occurs."""
        rng = self._position_ranges.get(axis)
        if not rng or len(rng) < 2:
            return value
        
        min_pos, max_pos = float(rng[0]), float(rng[1])
        if value < min_pos:
            self.log.warning(f'AMC300: Position {value*1e6:.1f}µm for axis {axis} clipped to min {min_pos*1e6:.1f}µm')
            return min_pos
        elif value > max_pos:
            self.log.warning(f'AMC300: Position {value*1e6:.1f}µm for axis {axis} clipped to max {max_pos*1e6:.1f}µm')
            return max_pos
        return value
    
    def _is_position_valid(self, axis: str, value: float) -> bool:
        """Check if position is within valid range."""
        rng = self._position_ranges.get(axis)
        if not rng or len(rng) < 2:
            return True
        return float(rng[0]) <= value <= float(rng[1])

    def _read_hardware_positions(self) -> Dict[str, float]:
        """Read positions from hardware, with fallback to cached targets."""
        dev = self._require_dev()
        pos = {}
        for ax, ch in self._axis_map.items():
            try:
                nm = float(dev.move.getPosition(ch))
                pos[ax] = nm * 1e-9
            except Exception as exc:
                self.log.debug(f'AMC300: Could not read position for axis {ax}: {exc}')
                pos[ax] = self._target_m.get(ax, 0.0)
        return pos

    def _wait_axis_idle(self, axis: str, timeout_s: float):
        """Wait for axis to stop moving, with timeout."""
        if self._sim_mode:
            return  # Instant in simulation
            
        ch = self._axis_to_channel(axis)
        t0 = time.time()
        while True:
            try:
                status = int(self._dev.status.getStatusMoving(ch))  # type: ignore
                moving = (status != 0)
                if not moving:
                    return
            except Exception as exc:
                # If we can't read status, assume settle time is sufficient
                self.log.debug(f'AMC300: Could not read status for axis {axis}: {exc}')
                time.sleep(self._settle_time_s)
                return
                
            if time.time() - t0 > timeout_s:
                self.log.warning(f'AMC300: Timeout waiting for axis {axis} to stop (status={status})')
                return
            time.sleep(0.005)  # Check every 5ms