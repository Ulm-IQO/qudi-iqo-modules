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
from qudi.interface.finite_sampling_input_interface import FiniteSamplingInputInterface


class AMC300NIScanningProbeInterfuse(ScanningProbeInterface):
    _threaded = True

    # Connectors
    _motion = Connector(name='motion', interface='ScanningProbeInterface')
    _ni_in = Connector(name='ni_input', interface='FiniteSamplingInputInterface')

    # Constraints mirrored to GUI/logic
    _input_channel_units: Dict[str, str] = ConfigOption('input_channel_units', default={}, missing='warn')

    # Acquisition and motion timing
    _ni_channel_mapping: Dict[str, str] = ConfigOption(name='ni_channel_mapping', missing='error')
    _default_dwell_time_s: float = ConfigOption('default_dwell_time_s', default=0.0005, missing='warn')
    _ni_sample_rate_hz: float = ConfigOption('ni_sample_rate_hz', default=50000.0, missing='warn')
    _settle_time_s: float = ConfigOption('settle_time_s', default=0.001, missing='warn')

    _back_scan_available: bool = ConfigOption('back_scan_available', default=False, missing='warn')

    # Internal state
    sigPositionChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._scan_settings: Optional[ScanSettings] = None
        self._back_cfg: Optional[ScanSettings] = None

        self._scan_data: Optional[ScanData] = None
        self._back_scan_data: Optional[ScanData] = None
        self.raw_data_container: Optional[RawDataContainer] = None
        self._constraints: Optional[ScanConstraints] = None

        # Channel name mappings: presented alias -> NI physical channel
        self._present_channels: List[str] = []
        self._present_to_ni: Dict[str, str] = {}
        self._ni_channels_in_order: List[str] = []


        self._thread_lock_data = Mutex()

        self.scanning_interfuse = AMC300NIScanningProbeInterfuse

    # Lifecycle
    def on_activate(self):

        #Constraints
        # 1) Axes from motion
        mcon: ScanConstraints = self._motion().constraints
        try:
            axis_objects = tuple(mcon.axis_objects.values())  # axes likely a dict
        except Exception:
            axis_objects = tuple(mcon.axis_objects)  # fallback if already a sequence

        # 2) Channels from NI in-streamer
        channels = list()
        for channel, unit in self._input_channel_units.items():
            channels.append(ScannerChannel(name=channel,
                                           unit=unit,
                                           dtype='float64'))

        back_scan_capability = BackScanCapability.AVAILABLE | BackScanCapability.RESOLUTION_CONFIGURABLE
        #back_scan_capability = BackScanCapability.AVAILABLE if self._back_scan_available else BackScanCapability(0)
        self._constraints = ScanConstraints(
            axis_objects=axis_objects,
            channel_objects=tuple(channels),
            back_scan_capability=back_scan_capability,
            has_position_feedback=False,
            square_px_only=False,
        )

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
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        return self._constraints


    def reset(self) -> None:
        """ Hard reset of the hardware.
                """
        pass

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters needed for a 1D or 2D scan. Returns None if not configured.
                """
        if self._scan_data:
            return self._scan_data.settings
        else:
            return None

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters of the backwards scan. Returns None if not configured or not available.
                """
        if self._back_scan_data:
            return self._back_scan_data.settings
        else:
            return None

    def configure_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.
                Raise an exception if the settings are invalid and do not comply with the hardware constraints.

                @param ScanSettings settings: ScanSettings instance holding all parameters
                """
        if self.is_scan_running:
            raise RuntimeError('Unable to configure scan parameters while scan is running. '
                               'Stop scanning and try again.')

        # Validate and clip settings against constraints
        self.constraints.check_settings(settings)
        self.log.debug('Scan settings fulfill constraints.')

        with self._thread_lock_data:
            settings = self._clip_ranges(settings)
            self._scan_data = ScanData.from_constraints(settings, self._constraints)

            # reset back scan to defaults
            if len(settings.axes) == 1:
                back_resolution = (self.__default_backward_resolution,)
            else:
                back_resolution = (self.__default_backward_resolution, settings.resolution[1])
            back_scan_settings = ScanSettings(
                channels=settings.channels,
                axes=settings.axes,
                range=settings.range,
                resolution=back_resolution,
                frequency=settings.frequency,
            )
            self._back_scan_data = ScanData.from_constraints(back_scan_settings, self._constraints)

            self.log.debug(f'New scan data and back scan data created.')
            self.raw_data_container = RawDataContainer(settings.channels,
                                                       settings.resolution[
                                                           1] if settings.scan_dimension == 2 else 1,
                                                       settings.resolution[0],
                                                       back_scan_settings.resolution[0])
            self.log.debug(f'New RawDataContainer created.')

        # Configure NI stream if API supports it
        self._ni_in().set_sample_rate(settings.frequency)
        self._ni_in().set_active_channels(channels=(self._ni_channel_mapping[in_ch] for in_ch in self._input_channel_units))

    def configure_back_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters of the backwards scan.
                Raise an exception if the settings are invalid and do not comply with the hardware constraints.

                @param ScanSettings settings: ScanSettings instance holding all parameters for the back scan
                """
        if self.is_scan_running:
            raise RuntimeError('Unable to configure scan parameters while scan is running. '
                               'Stop scanning and try again.')

        forward_settings = self.scan_settings
        # check settings - will raise appropriate exceptions if something is not right
        self.constraints.check_back_scan_settings(settings, forward_settings)
        self.log.debug('Back scan settings fulfill constraints.')
        print("debug lol")
        with self._thread_lock_data:
            self._back_scan_data = ScanData.from_constraints(settings, self._constraints)
            self.log.debug(f'New back scan data created.')
            self.raw_data_container = RawDataContainer(forward_settings.channels,
                                                       forward_settings.resolution[
                                                           1] if forward_settings.scan_dimension == 2 else 1,
                                                       forward_settings.resolution[0],
                                                       settings.resolution[0])
            self.log.debug(f'New RawDataContainer created.')

    # Movement passthrough to motion module
    def move_absolute(self, position: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
                velocity.

                Log error and return current target position if something fails or a scan is in progress.
                """

        # assert not self.is_running, 'Cannot move the scanner while, scan is running'
        if self.is_scan_running:
            self.log.error('Cannot move the scanner while, scan is running')
            return self.bare_scanner.get_target(self)

        if not set(position).issubset(self.constraints.axes):
            self.log.error('Invalid axes name in position')
            return self.bare_scanner.get_target(self)

        return self._motion().move_absolute(position, velocity=velocity, blocking=blocking)

    def move_relative(self, distance: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe by a relative distance from the current target position as fast
                as possible or with a defined velocity.

                Log error and return current target position if something fails or a 1D/2D scan is in
                progress.
                """
        return self._motion().move_relative(distance, velocity=velocity, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        return self._motion().get_target()

    def get_position(self) -> Dict[str, float]:
        return self._motion().get_position()

    # Scan lifecycle
    def start_scan(self):
        """Start a scan as configured beforehand.
        Log an error if something fails or a 1D/2D scan is in progress.

        Offload self._start_scan() from the caller to the module's thread.
        ATTENTION: Do not call this from within thread lock protected code to avoid deadlock (PR #178).
        :return:
        """

        try:
            if self.thread() is not QtCore.QThread.currentThread():
                QtCore.QMetaObject.invokeMethod(self, '_start_scan', QtCore.Qt.BlockingQueuedConnection)
            else:
                self._start_scan()

        except:
            self.log.exception("")

    @QtCore.Slot()
    def _start_scan(self):
        try:
            if self._scan_data is None:
                # todo: raising would be better, but from this delegated thread exceptions get lost
                self.log.error('Scan Data is None. Scan settings need to be configured before starting')

            if self.is_scan_running:
                self.log.error('Cannot start a scan while scanning probe is already running')

            with self._thread_lock_data:
                self._scan_data.new_scan()
                self._back_scan_data.new_scan()
                self._stored_target_pos = self.bare_scanner.get_target(self).copy()
                self.log.debug(f"Target pos at scan start: {self._stored_target_pos}")
                self._scan_data.scanner_target_at_start = self._stored_target_pos
                self._back_scan_data.scanner_target_at_start = self._stored_target_pos

            # todo: scanning_probe_logic exits when scanner not locked right away
            # should rather ignore/wait until real hw timed scanning starts
            self.module_state.lock()
            #??
            first_scan_position = {ax: pos[0] for ax, pos
                                   in zip(self.scan_settings.axes, self.scan_settings.range)}
            self._move_to_and_start_scan(first_scan_position)

        except Exception as e:
            self.module_state.unlock()
            self.log.exception("Starting scan failed.", exc_info=e)

    def stop_scan(self):
        """Stop the currently running scan.
        Log an error if something fails or no 1D/2D scan is in progress.

        Offload self._stop_scan() from the caller to the module's thread.
        ATTENTION: Do not call this from within thread lock protected code to avoid deadlock (PR #178).
        :return:
        """

        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self, '_stop_scan',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self._stop_scan()

    @QtCore.Slot()
    def _stop_scan(self):
        if not self.is_scan_running:
            self.log.error('No scan in progress. Cannot stop scan.')

        # self.log.debug("Stopping scan...")
        self._start_scan_after_cursor = False  # Ensure Scan HW is not started after movement
        #if self._ao_setpoint_channels_active:
        #    self._abort_cursor_movement()
            # self.log.debug("Move aborted")

        #if self._ni_finite_sampling_io().is_running:
        #    self._ni_finite_sampling_io().stop_buffered_frame()
            # self.log.debug("Frame stopped")

        self.module_state.unlock()
        # self.log.debug("Module unlocked")

        self.log.debug(f"Finished scan, move to stored target: {self._stored_target_pos}")
        self.bare_scanner.move_absolute(self, self._stored_target_pos)
        self._stored_target_pos = dict()

    def emergency_stop(self) -> None:

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

    def is_scan_running(self):
        """
        Read-only flag indicating the module state.

        @return bool: scanning probe is running (True) or not (False)
        """
        # module state used to indicate hw timed scan running
        #self.log.debug(f"Module in state: {self.module_state()}")
        #assert self.module_state() in ('locked', 'idle')  # TODO what about other module states?
        if self.module_state() == 'locked':
            return True
        else:
            return False

    def get_scan_data(self) -> Optional[ScanData]:
        """ Read-only property returning the ScanData instance used in the scan.
        """
        if self._scan_data is None:
            return None
        else:
            with self._thread_lock_data:
                return self._scan_data.copy()
    def get_back_scan_data(self) -> Optional[ScanData]:
        """ Retrieve the ScanData instance used in the backwards scan.
        """
        if self._scan_data is None:
            return None
        else:
            with self._thread_lock_data:
                return self._back_scan_data.copy()

    # Worker
    def _run_scan_worker(self):
        settings = self._scan_settings
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

class RawDataContainer:
    def __init__(self, channel_keys, number_of_scan_lines: int,
                 forward_line_resolution: int, backwards_line_resolution: int):
        self.forward_line_resolution = forward_line_resolution
        self.number_of_scan_lines = number_of_scan_lines
        self.forward_line_resolution = forward_line_resolution
        self.backwards_line_resolution = backwards_line_resolution

        self._raw = {key: np.full(self.frame_size, np.nan) for key in channel_keys}

    @property
    def frame_size(self) -> int:
        return self.number_of_scan_lines * (self.forward_line_resolution + self.backwards_line_resolution)

    def fill_container(self, samples_dict):
        # get index of first nan from one element of dict
        first_nan_idx = self.number_of_non_nan_values
        for key, samples in samples_dict.items():
            self._raw[key][first_nan_idx:first_nan_idx + len(samples)] = samples

    def forwards_data(self):
        reshaped_2d_dict = dict.fromkeys(self._raw)
        for key in self._raw:
            if self.number_of_scan_lines > 1:
                reshaped_arr = self._raw[key].reshape(self.number_of_scan_lines,
                                                      self.forward_line_resolution + self.backwards_line_resolution)
                reshaped_2d_dict[key] = reshaped_arr[:, :self.forward_line_resolution].T
            elif self.number_of_scan_lines == 1:
                reshaped_2d_dict[key] = self._raw[key][:self.forward_line_resolution]
        return reshaped_2d_dict

    def backwards_data(self):
        reshaped_2d_dict = dict.fromkeys(self._raw)
        for key in self._raw:
            if self.number_of_scan_lines > 1:
                reshaped_arr = self._raw[key].reshape(self.number_of_scan_lines,
                                                      self.forward_line_resolution + self.backwards_line_resolution)
                reshaped_2d_dict[key] = reshaped_arr[:, self.forward_line_resolution:].T
            elif self.number_of_scan_lines == 1:
                reshaped_2d_dict[key] = self._raw[key][self.forward_line_resolution:]

        return reshaped_2d_dict

    @property
    def number_of_non_nan_values(self):
        """
        returns number of not NaN samples
        """
        return np.sum(~np.isnan(next(iter(self._raw.values()))))

    @property
    def is_full(self):
        return self.number_of_non_nan_values == self.frame_size