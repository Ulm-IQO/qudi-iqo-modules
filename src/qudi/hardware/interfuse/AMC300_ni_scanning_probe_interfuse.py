# -*- coding: utf-8 -*-

"""
Interfuse: AMC300 motion (stepping) + NI FiniteSamplingInput (APD) as a ScanningProbeInterface.

- Motion is executed stepwise via a connected AMC300_stepper (ScanningProbeInterface).
- APD data is acquired via NIXSeriesFiniteSamplingInput (DataInStreamInterface).
- Scans are software-stepped: for each pixel, move->settle->read NI samples->aggregate->store.

This mirrors the structure of NiScanningProbeInterfuseBare, but without NI AO output.

Example config:

hardware:
    amc300_ni_scanner:
        module.Class: 'interfuse.AMC300_ni_scanning_probe_interfuse.AMC300NIScanningProbeInterfuse'
        connect:
            motion: 'amc300_stepper'
            data_instream: 'nicard_6343_instreamer'
        options:
            channel_aliases:
                fluorescence: 'PFI8'
            input_channel_units:
                fluorescence: 'c/s'
            apd_channel_names: ['PFI8']     # map to NI input channels
            default_dwell_time_s: 0.5e-3    # optional if not deriving from frequency
            ni_sample_rate_hz: 50e3         # choose ≥ 1/dwell resolution you need
            settle_time_s: 0.001
            back_scan_available: false
"""

from __future__ import annotations

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
    _threaded = True

    # Connectors
    _motion = Connector(name='motion', interface='ScanningProbeInterface')
    _ni_in = Connector(name='data_instream', interface='DataInStreamInterface')

    # Constraints mirrored to GUI/logic
    _input_channel_units: Dict[str, str] = ConfigOption('input_channel_units', default={}, missing='warn')
    _channel_aliases: Dict[str, str] = ConfigOption('channel_aliases', default={'fluorescence': 'PFI8'}, missing='warn')

    # Acquisition and motion timing
    _apd_channels: List[str] = ConfigOption('apd_channels', default=['PFI8'], missing='warn')
    _default_dwell_time_s: float = ConfigOption('default_dwell_time_s', default=0.0005, missing='warn')
    _ni_sample_rate_hz: float = ConfigOption('ni_sample_rate_hz', default=50000.0, missing='warn')
    _settle_time_s: float = ConfigOption('settle_time_s', default=0.001, missing='warn')

    _back_scan_available: bool = ConfigOption('back_scan_available', default=False, missing='warn')

    # Internal state
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

        # Channel name mappings: presented alias -> NI physical channel
        self._present_channels: List[str] = []
        self._present_to_ni: Dict[str, str] = {}
        self._ni_channels_in_order: List[str] = []

    # Lifecycle
    def on_activate(self):
        # Nothing extra; connectors are resolved on use
        pass

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

        # 1) Axes from motion
        mcon: ScanConstraints = self._motion().constraints
        try:
            axis_objects = tuple(mcon.axis_objects.values())  # axes likely a dict
        except Exception:
            axis_objects = tuple(mcon.axis_objects)  # fallback if already a sequence

        # 2) Channels from NI in-streamer
        channel_objects: List[ScannerChannel] = []
        try:
            ni_con = self._ni_in().constraints  # DataInStreamConstraints
            ch_units = ni_con.channel_units
            if self._apd_channels:
                for name in self._apd_channels:
                    unit = ch_units.get(name)
                    if unit is not None:
                        channel_objects.append(ScannerChannel(name=name, unit=unit))
            else:
                for name, unit in ch_units.items():
                    channel_objects.append(ScannerChannel(name=name, unit=unit))
        except Exception:
            # Fallback: config-based units if NI isn’t ready
            for name, unit in self._input_channel_units.items():
                channel_objects.append(ScannerChannel(name=name, unit=unit))

        cap = BackScanCapability.AVAILABLE if self._back_scan_available else BackScanCapability.RESOLUTION_CONFIGURABLE
        return ScanConstraints(
            axis_objects=axis_objects,
            channel_objects=tuple(channel_objects),
            back_scan_capability=cap,
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
        # Validate and clip settings against constraints
        constr = self.constraints
        constr.check_settings(settings)
        settings = constr.clip(settings)

        self._scan_cfg = settings
        self._back_cfg = None  # reset unless specifically configured

        # Prepare ScanData containers according to constraints helper
        self._scan_data = ScanData.from_constraints(
            settings=settings, constraints=constr, scanner_target_at_start=self.get_target()
        )
        self._back_data = None

        # Configure NI stream if API supports it
        ni: DataInStreamInterface = self._ni_in()
        sample_rate = float(self._ni_sample_rate_hz)
        try:
            # Some implementations accept (channels, sample_rate)
            ni.configure_stream(self._ni_channels_in_order, sample_rate)  # type: ignore
        except Exception:
            pass

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
        if self.module_state() != 'idle':
            raise RuntimeError('Scan already running')
        if self._scan_cfg is None or self._scan_data is None:
            raise RuntimeError('Scan not configured')

        self._stop_requested = False
        self.module_state.lock()
        self.__worker_running = True

        # Start NI stream
        ni: DataInStreamInterface = self._ni_in()
        try:
            ni.start_stream()
        except Exception:
            # Some NI modules require prior configuration; propagate error
            self.module_state.unlock()
            self.__worker_running = False
            raise

        # Run worker in our thread
        try:
            self._run_scan_worker()
        except Exception:
            self.log.exception('AMC300NI scan worker crashed:')
            try:
                ni.stop_stream()
            finally:
                self.module_state.unlock()
                self.__worker_running = False
                raise

        # Finished
        try:
            ni.stop_stream()
        finally:
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