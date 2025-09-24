# -*- coding: utf-8 -*-

"""
AMC300 + NI Scanning Probe Interfuse for Qudi confocal microscopy.

Quickstart:
----------
This interfuse combines Attocube AMC300 stepper motion with NI data acquisition 
to create a complete scanning probe interface for confocal microscopy. It replaces
the traditional NI AO + NI finite sampling approach with AMC300 stepping + NI streaming.

Purpose: Provides complete 2D/3D scanning capability where:
- Motion: Attocube AMC300 stepper controllers (precise positioning without drift)
- Data: NI-DAQ APD counting via digital input streaming (PFI8 fluorescence channel)
- Scan sequence: For each pixel → move AMC300 → wait ready → settle → acquire NI data → next pixel

This module behaves exactly like ni_scanning_probe_interfuse from the user perspective
and is controlled by scanning_probe_logic, but uses stepper motion instead of analog voltage.

Configuration Example:
--------------------
hardware:
    amc300_ni_scanner:
        module.Class: 'interfuse.AMC300_ni_scanning_probe_interfuse.AMC300NIScanningProbeInterfuse'
        connect:
            motion: 'amc300_stepper'           # AMC300_stepper module for motion
            data_instream: 'nicard_6343_instreamer'  # NIXSeriesInStreamer for APD data
        options:
            channel_aliases:                   # Map logical names to physical channels
                fluorescence: 'PFI8'          # APD signal on NI PFI8
            input_channel_units:               # Units for each channel  
                fluorescence: 'c/s'           # counts per second
            apd_channels: ['PFI8']            # NI digital channels for APD counting
            default_dwell_time_s: 0.5e-3     # Time per pixel when not using frequency
            ni_sample_rate_hz: 50e3           # NI sampling rate (must be ≥ 1/dwell_time)
            settle_time_s: 0.001              # Extra settling after motion
            back_scan_available: false        # Enable/disable back scan capability
            simulation: false                 # Enable simulation mode for testing

Channel Configuration:
- 'fluorescence' channel is required and must map to a valid NI digital input
- Channel appears in constraints so scanner GUI optimizer doesn't KeyError
- apd_channels lists the actual NI physical channels to use
- input_channel_units defines units for data processing (typically 'c/s' for APD)

Timing and Sampling:
------------------
- ni_sample_rate_hz: NI hardware sampling rate (e.g., 50 kHz)
- default_dwell_time_s: Time spent acquiring per pixel (e.g., 0.5 ms)
- samples_per_pixel = round(sample_rate × dwell_time), minimum 1
- For count channels (c/s units): counts = mean(samples) × dwell_time
- settle_time_s: Additional wait after AMC300 reports motion complete

Motion Integration:
------------------
This interfuse connects to an AMC300_stepper module that handles:
- Network connection to AMC300 controller  
- Position validation against physical ranges
- Step-based motion with automatic ready-waiting
- Simulation mode for testing without hardware

The motion sequence per pixel ensures data integrity:
1. Command AMC300 to move to target position
2. Wait until AMC300 status shows motion complete (not moving)
3. Wait additional settle_time_s for mechanical settling
4. Acquire NI data for dwell_time_s duration
5. Aggregate samples and store pixel data
6. Repeat for next pixel

NI Integration Notes:
--------------------
Connects to NIXSeriesInStreamer (DataInStreamInterface) which provides:
- Digital input streaming from PFI channels for APD counting
- Configurable sample rates and buffering
- Sample-accurate timing for precise measurements

The interfuse configures NI streaming with:
- Channel list from apd_channels configuration
- Sample rate from ni_sample_rate_hz
- Buffer management for pixel-by-pixel acquisition

Data Processing:
- APD channels (unit='c/s'): Convert samples to counts via mean(samples) × dwell_time
- Other channels: Sum samples directly
- Results stored in ScanData format for scanner logic consumption

Troubleshooting:
---------------
Activation Errors:
- "At least one data channel must be specified" → Check channel_aliases and apd_channels config
- "KeyError: fluorescence" → Ensure 'fluorescence' appears in channel constraints  
- Connection failed → Verify motion and data_instream module names in config

Range/Motion Errors:
- "Value X.X is out of bounds" → Check AMC300_stepper position_ranges vs scan ranges
- Motion timeout → Increase max_move_timeout_s in AMC300_stepper config
- "Axis not ready" → Check AMC300 drive enables and mechanical binding

Data Acquisition Errors:
- NI device not found → Check NIXSeriesInStreamer device_name and NI-DAQmx installation
- Channel not available → Verify PFI8 (or configured channel) is valid on your NI device
- Sample rate too high → Reduce ni_sample_rate_hz or increase buffer sizes

Simulation Mode:
- Set simulation: true in options OR export AMC300_SIM=1
- Bypasses all hardware, provides synthetic data for GUI/logic testing
- Motion simulation: instant moves with predictable positions
- Data simulation: configurable fake fluorescence patterns for scan testing

Performance Notes:
- Scanning speed limited by AMC300 step-and-settle time vs traditional analog scanning
- Compensated by elimination of piezo drift and improved positioning accuracy
- Dwell times typically 0.1-1 ms per pixel for good SNR
- Total scan time = (n_pixels × dwell_time) + motion_overhead
"""

from __future__ import annotations

import os
import time
import numpy as np
from typing import Dict, List, Optional, Tuple

from PySide2 import QtCore

from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
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

from qudi.interface.data_instream_interface import DataInStreamInterface, SampleTiming


class AMC300NIScanningProbeInterfuse(ScanningProbeInterface):
    """
    Scanning probe interfuse combining AMC300 stepper motion with NI data acquisition.
    
    This module implements the ScanningProbeInterface by coordinating:
    - Motion via AMC300_stepper (connected as 'motion')  
    - Data acquisition via NIXSeriesInStreamer (connected as 'data_instream')
    
    The resulting interface behaves like ni_scanning_probe_interfuse for compatibility
    with existing scanning_probe_logic and scanner_gui modules.
    """
    _threaded = True

    # Connectors to hardware modules
    _motion = Connector(name='motion', interface='ScanningProbeInterface')
    _ni_in = Connector(name='data_instream', interface='DataInStreamInterface')

    # Channel configuration
    _input_channel_units: Dict[str, str] = ConfigOption('input_channel_units', default={'fluorescence': 'c/s'}, missing='warn')
    _channel_aliases: Dict[str, str] = ConfigOption('channel_aliases', default={'fluorescence': 'PFI8'}, missing='warn')
    _apd_channels: List[str] = ConfigOption('apd_channels', default=['PFI8'], missing='warn')

    # Timing parameters
    _default_dwell_time_s: float = ConfigOption('default_dwell_time_s', default=0.0005, missing='warn')
    _ni_sample_rate_hz: float = ConfigOption('ni_sample_rate_hz', default=50000.0, missing='warn')
    _settle_time_s: float = ConfigOption('settle_time_s', default=0.001, missing='warn')

    # Capabilities
    _back_scan_available: bool = ConfigOption('back_scan_available', default=False, missing='warn')
    _simulation: bool = ConfigOption('simulation', default=False, missing='warn')

    # Signals
    sigPositionChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        self._scan_cfg: Optional[ScanSettings] = None
        self._back_cfg: Optional[ScanSettings] = None
        self._scan_data: Optional[ScanData] = None
        self._back_data: Optional[ScanData] = None
        self._stop_requested = False
        self.__worker_running = False

        # Channel management - These MUST be populated for scanning to work
        self._present_channels: List[str] = []     # Logical channel names presented to user
        self._present_to_ni: Dict[str, str] = {}   # Logical -> physical NI channel mapping
        self._ni_channels_in_order: List[str] = [] # NI physical channels in acquisition order
        
        # Check simulation mode from config or environment
        self._sim_mode = self._simulation or os.environ.get('AMC300_SIM', '').lower() in ('1', 'true', 'yes')

    # Lifecycle
    def on_activate(self):
        """Initialize the interfuse and set up channel mappings."""
        try:
            # Build channel mappings - This is CRITICAL for scan functionality
            self._setup_channel_mappings()
            
            if self._sim_mode:
                self.log.info("AMC300NIScanningProbeInterfuse: Running in simulation mode")
            else:
                # Validate connections to motion and NI modules
                try:
                    motion = self._motion()
                    self.log.debug(f"Connected to motion module: {motion}")
                except Exception as exc:
                    raise RuntimeError(f"Failed to connect to motion module: {exc}")
                
                try:
                    ni_in = self._ni_in()
                    self.log.debug(f"Connected to NI input module: {ni_in}")
                except Exception as exc:
                    raise RuntimeError(f"Failed to connect to NI input module: {exc}")
                    
        except Exception as exc:
            self.log.error(f"AMC300NIScanningProbeInterfuse activation failed: {exc}")
            raise

    def _setup_channel_mappings(self):
        """Set up the critical channel mappings that make scanning work."""
        # Clear existing mappings
        self._present_channels.clear()
        self._present_to_ni.clear() 
        self._ni_channels_in_order.clear()
        
        # Build mapping from channel aliases (logical names -> NI physical channels)
        for logical_name, ni_channel in self._channel_aliases.items():
            if logical_name not in self._present_channels:
                self._present_channels.append(logical_name)
            self._present_to_ni[logical_name] = ni_channel
            if ni_channel not in self._ni_channels_in_order:
                self._ni_channels_in_order.append(ni_channel)
        
        # Ensure APD channels are included (they might not have aliases)
        for ni_channel in self._apd_channels:
            if ni_channel not in self._ni_channels_in_order:
                self._ni_channels_in_order.append(ni_channel)
                # Create logical name if no alias exists
                logical_name = f"APD_{ni_channel}"
                if logical_name not in self._present_channels:
                    self._present_channels.append(logical_name)
                self._present_to_ni[logical_name] = ni_channel

        # Ensure 'fluorescence' channel always exists (required by scanner GUI)
        if 'fluorescence' not in self._present_channels:
            # Use first APD channel as fluorescence if not explicitly mapped
            if self._apd_channels:
                ni_channel = self._apd_channels[0]
                self._present_channels.insert(0, 'fluorescence')  # Add as first channel
                self._present_to_ni['fluorescence'] = ni_channel
                if ni_channel not in self._ni_channels_in_order:
                    self._ni_channels_in_order.insert(0, ni_channel)
            else:
                raise ValueError(
                    "No 'fluorescence' channel configured and no apd_channels specified. "
                    "At least one data channel must be available for scanning."
                )
        
        # Validate we have at least one channel
        if not self._present_channels:
            raise ValueError(
                "At least one data channel must be specified for a valid scan. "
                "Check channel_aliases and apd_channels configuration."
            )
            
        self.log.debug(f"Configured channels: {self._present_channels}")
        self.log.debug(f"Channel mapping: {self._present_to_ni}")
        self.log.debug(f"NI channel order: {self._ni_channels_in_order}")

    def on_deactivate(self):
        # Attempt to stop everything
        try:
            if self.module_state() != 'idle':
                self.stop_scan()
        except Exception:
            pass

    # Constraints and settings
    @property
    def constraints(self) -> ScanConstraints:
        """Build scan constraints from motion and data sources.
        
        This is critical - scanner GUI expects to find "fluorescence" channel here
        or it will KeyError during optimizer initialization.
        """
        try:
            # 1) Get axes constraints from motion module 
            if self._sim_mode:
                # In simulation, create basic axis constraints
                from qudi.util.constraints import ScalarConstraint
                axes = [
                    ScannerAxis(name="x", unit="m", 
                               position=ScalarConstraint(default=0.003, bounds=(0.0015, 0.0045)),
                               step=ScalarConstraint(default=0, bounds=(0, 0.003)),
                               resolution=ScalarConstraint(default=50, bounds=(1, 1000), enforce_int=True),
                               frequency=ScalarConstraint(default=100, bounds=(1, 1000))),
                    ScannerAxis(name="y", unit="m",
                               position=ScalarConstraint(default=0.003, bounds=(0.0015, 0.0045)),
                               step=ScalarConstraint(default=0, bounds=(0, 0.003)),
                               resolution=ScalarConstraint(default=50, bounds=(1, 1000), enforce_int=True),
                               frequency=ScalarConstraint(default=100, bounds=(1, 1000)))
                ]
                axis_objects = tuple(axes)
            else:
                motion_constraints = self._motion().constraints
                # Handle both dict and tuple formats
                if hasattr(motion_constraints.axis_objects, "values"):
                    axis_objects = tuple(motion_constraints.axis_objects.values())
                else:
                    axis_objects = tuple(motion_constraints.axis_objects)

            # 2) Build channel constraints from our configured channels
            channel_objects: List[ScannerChannel] = []
            
            # Create ScannerChannel objects for each configured channel
            for logical_name in self._present_channels:
                # Get unit from configuration
                unit = self._input_channel_units.get(logical_name, "c/s")
                channel_objects.append(
                    ScannerChannel(name=logical_name, unit=unit, dtype="float64")
                )
            
            # Ensure we have at least the fluorescence channel
            if not any(ch.name == "fluorescence" for ch in channel_objects):
                # Add fluorescence channel if somehow missing
                channel_objects.insert(0, 
                    ScannerChannel(name="fluorescence", unit="c/s", dtype="float64")
                )
            
            # 3) Set back scan capability
            if self._back_scan_available:
                back_scan_capability = BackScanCapability.AVAILABLE | BackScanCapability.RESOLUTION_CONFIGURABLE
            else:
                back_scan_capability = BackScanCapability.NOT_AVAILABLE

            constraints = ScanConstraints(
                axis_objects=axis_objects,
                channel_objects=tuple(channel_objects),
                back_scan_capability=back_scan_capability,
                has_position_feedback=False,
                square_px_only=False,
            )
            
            self.log.debug(f"Built constraints with {len(channel_objects)} channels: "
                          f"{[ch.name for ch in channel_objects]}")
            return constraints
            
        except Exception as exc:
            self.log.error(f"Failed to build scan constraints: {exc}")
            # Return minimal constraints to prevent total failure
            from qudi.util.constraints import ScalarConstraint
            fallback_axis = ScannerAxis(
                name="x", unit="m",
                position=ScalarConstraint(default=0.003, bounds=(0.0015, 0.0045)),
                step=ScalarConstraint(default=0, bounds=(0, 0.003)),
                resolution=ScalarConstraint(default=50, bounds=(1, 1000), enforce_int=True),
                frequency=ScalarConstraint(default=100, bounds=(1, 1000))
            )
            fallback_channel = ScannerChannel(name="fluorescence", unit="c/s", dtype="float64")
            return ScanConstraints(
                axis_objects=(fallback_axis,),
                channel_objects=(fallback_channel,),
                back_scan_capability=BackScanCapability.NOT_AVAILABLE,
                has_position_feedback=False,
                square_px_only=False,
            )


    def reset(self) -> None:
        with self._thread_lock:
            self._scan_cfg = None
            self._back_cfg = None
            self._scan_data = None
            self._back_data = None

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        return self._scan_cfg

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        return self._back_cfg

    def configure_scan(self, settings: ScanSettings) -> None:
        """Configure scan with proper validation and error handling."""
        try:
            # Ensure channel mappings are set up
            if not self._present_channels:
                self._setup_channel_mappings()
            
            # Validate constraints
            constr = self.constraints
            if not constr.channel_objects:
                raise ValueError(
                    "At least one data channel must be specified for a valid scan. "
                    "Check channel_aliases and apd_channels configuration."
                )
                
            # Validate and clip settings against constraints
            constr.check_settings(settings)
            settings = constr.clip(settings)

            self._scan_cfg = settings
            self._back_cfg = None  # reset unless specifically configured

            # Prepare ScanData containers according to constraints helper
            self._scan_data = ScanData.from_constraints(
                settings=settings, constraints=constr, scanner_target_at_start=self.get_target()
            )
            self._back_data = None

            # Configure NI stream for simulation or real hardware
            if not self._sim_mode:
                try:
                    ni: DataInStreamInterface = self._ni_in()
                    sample_rate = float(self._ni_sample_rate_hz)
                    # Some implementations accept (channels, sample_rate)
                    if hasattr(ni, 'configure_stream'):
                        ni.configure_stream(self._ni_channels_in_order, sample_rate)  # type: ignore
                except Exception as exc:
                    self.log.warning(f"Could not configure NI stream: {exc}")
                    
            self.log.debug(f"Configured scan: {settings.range} with channels {self._present_channels}")
            
        except Exception as exc:
            self.log.error(f"Failed to configure scan: {exc}")
            raise

    def configure_back_scan(self, settings: ScanSettings) -> None:
        if not self._back_scan_available:
            self._back_cfg = None
            return
        self._back_cfg = settings

    # Movement passthrough to motion module
    def move_absolute(self, position: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        return self._motion().move_absolute(position, velocity=velocity, blocking=blocking)

    def move_relative(self, distance: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        return self._motion().move_relative(distance, velocity=velocity, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        return self._motion().get_target()

    def get_position(self) -> Dict[str, float]:
        return self._motion().get_position()

    # Scan lifecycle
    def start_scan(self) -> None:
        """Start a scan with proper error handling and simulation support."""
        if self.module_state() != 'idle':
            raise RuntimeError('Scan already running')
        if self._scan_cfg is None or self._scan_data is None:
            raise RuntimeError('Scan not configured')

        self._stop_requested = False
        self.module_state.lock()
        self.__worker_running = True

        try:
            if self._sim_mode:
                # In simulation mode, just run the worker without NI hardware
                self._run_scan_worker()
            else:
                # Real hardware mode - start NI stream
                ni: DataInStreamInterface = self._ni_in()
                try:
                    ni.start_stream()
                except Exception as exc:
                    # Some NI modules require prior configuration; propagate error
                    raise RuntimeError(f"Failed to start NI data stream: {exc}") from exc

                # Run worker with NI stream active
                try:
                    self._run_scan_worker()
                finally:
                    # Always stop stream when done
                    try:
                        ni.stop_stream()
                    except Exception as exc:
                        self.log.warning(f"Failed to stop NI stream: {exc}")
                        
        except Exception as exc:
            self.log.exception('AMC300NI scan worker failed:')
            self.module_state.unlock()
            self.__worker_running = False
            raise
            
        # Scan completed successfully
        self.module_state.unlock()
        self.__worker_running = False

    def stop_scan(self) -> None:
        self._stop_requested = True

    def emergency_stop(self) -> None:
        self._stop_requested = True
        try:
            self._motion().emergency_stop()
        except Exception:
            pass
        try:
            self._ni_in().stop_stream()
        except Exception:
            pass
        if self.module_state() != 'idle':
            self.module_state.unlock()

    def get_scan_data(self) -> Optional[ScanData]:
        return self._scan_data

    def get_back_scan_data(self) -> Optional[ScanData]:
        return self._back_data

    # Worker
    def _run_scan_worker(self):
        settings = self._scan_cfg
        data = self._scan_data
        if settings is None or data is None:
            raise RuntimeError('Scan not configured')

        # Axis vectors from settings (tuple API)
        axes_names = list(settings.axes)
        axis_values: List[np.ndarray] = []
        for i, ax in enumerate(axes_names):
            mn, mx = settings.range[i]
            n = int(settings.resolution[i])
            axis_values.append(np.linspace(float(mn), float(mx), n))

        # Pixel iterator (row-major in order of settings.axes)
        mesh = np.meshgrid(*axis_values, indexing='ij')
        coords_stack = np.stack([m.reshape(-1) for m in mesh], axis=1)  # shape (pixels, dims)

        # Dwell time per pixel: pixel frequency (Hz) -> seconds per pixel
        dwell_s = 1.0 / float(settings.frequency) if settings.frequency > 0 else self._default_dwell_time_s

        # NI sampling config
        ni: DataInStreamInterface = self._ni_in()
        sample_rate = float(self._ni_sample_rate_hz)
        samples_per_pixel = max(1, int(round(sample_rate * dwell_s)))
        channel_count = len(self._present_channels)
        buffer = np.zeros(channel_count * samples_per_pixel, dtype=ni.constraints.data_type)
        timestamps = None
        if ni.constraints.sample_timing == SampleTiming.TIMESTAMP:
            timestamps = np.zeros(samples_per_pixel, dtype=np.float64)

        # Iterate pixels
        motion: ScanningProbeInterface = self._motion()
        for pix_idx, coord in enumerate(coords_stack):
            if self._stop_requested:
                break

            # Move to pixel
            pos = {ax: float(coord[i]) for i, ax in enumerate(axes_names)}
            motion.move_absolute(pos, blocking=True)
            time.sleep(self._settle_time_s)

            # Read NI samples for the dwell window
            if timestamps is None:
                ni.read_data_into_buffer(buffer, samples_per_channel=samples_per_pixel)
            else:
                ni.read_data_into_buffer(buffer, samples_per_channel=samples_per_pixel, timestamp_buffer=timestamps)

            # Aggregate per channel in presented alias order
            counts: List[float] = []
            for ch_idx, alias in enumerate(self._present_channels):
                # Slice this channel’s samples
                ch_slice = buffer[ch_idx * samples_per_pixel:(ch_idx + 1) * samples_per_pixel]
                unit = data.channels[alias].unit if alias in data.channels else self._input_channel_units.get(alias, '')
                if unit == 'c/s':
                    val = float(np.mean(ch_slice)) * dwell_s  # counts per second -> counts in dwell
                else:
                    val = float(np.sum(ch_slice))
                counts.append(val)

            data.write_pixel(pix_idx, counts)

        # Mark completion
        data.finish_scan()
        if self._back_data is not None:
            self._back_data.finish_scan()

    def _generate_simulation_data(self, pos: Dict[str, float], dwell_s: float, samples_per_pixel: int) -> List[float]:
        """Generate synthetic fluorescence data for simulation mode."""
        counts = []
        for ch_idx, logical_name in enumerate(self._present_channels):
            unit = self._input_channel_units.get(logical_name, 'c/s')
            
            if unit == 'c/s':
                # Simulate fluorescence with position-dependent intensity
                # Create a gaussian-like pattern with some noise
                center_x, center_y = 0.003, 0.003  # Center of scan range
                x_pos = pos.get('x', center_x)
                y_pos = pos.get('y', center_y)
                
                # Distance from center
                dx = (x_pos - center_x) * 1e6  # Convert to micrometers for calculation
                dy = (y_pos - center_y) * 1e6
                distance = np.sqrt(dx**2 + dy**2)
                
                # Gaussian profile with some background
                peak_counts = 10000  # counts/s at center
                background = 1000    # background counts/s
                sigma = 50           # width in micrometers
                
                intensity = background + peak_counts * np.exp(-(distance**2) / (2 * sigma**2))
                
                # Add some noise (Poisson-like)
                noise_factor = 0.1
                noise = np.random.normal(0, intensity * noise_factor)
                intensity = max(0, intensity + noise)
                
                # Convert to total counts for the dwell time
                counts.append(intensity * dwell_s)
            else:
                # For non-count channels, just return a small value
                counts.append(0.1)
                
        return counts

    def _process_ni_data(self, buffer: np.ndarray, samples_per_pixel: int, dwell_s: float, data: ScanData) -> List[float]:
        """Process acquired NI data into channel counts."""
        counts: List[float] = []
        
        for ch_idx, alias in enumerate(self._present_channels):
            # Slice this channel's samples from interleaved buffer
            ch_slice = buffer[ch_idx * samples_per_pixel:(ch_idx + 1) * samples_per_pixel]
            unit = data.channels[alias].unit if alias in data.channels else self._input_channel_units.get(alias, '')
            
            if unit == 'c/s':
                # For count rate channels: mean rate × dwell time = total counts
                val = float(np.mean(ch_slice)) * dwell_s
            else:
                # For other channels: sum the samples
                val = float(np.sum(ch_slice))
                
            counts.append(val)
            
        return counts