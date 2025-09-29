# -*- coding: utf-8 -*-

"""
Interfuse: AMC300 motion (stepping) + NI FiniteSamplingInput (APD) as a ScanningProbeInterface.

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

from __future__ import annotations

import time
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import asdict

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
                ni_input: 'ni_finite_sampling_input'
            options:
                ni_channel_mapping:
                    fluorescence: 'PFI8'
                input_channel_units:
                    fluorescence: 'c/s'
                default_dwell_time_s: 0.5e-3    # optional if not deriving from frequency
                ni_sample_rate_hz: 50e3         # choose ≥ 1/dwell resolution you need
                settle_time_s: 0.001
                back_scan_available: true
                _use_closed_loop_for_deferred: true
                _closed_loop_window_nm: 300
                _closed_loop_timeout_s: 1.5
                _closed_loop_disable_after: true
    """

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
    __default_backward_resolution: int = ConfigOption(name='default_backward_resolution', default=50)
    # Defered movement
    _defer_cursor_moves: bool = ConfigOption('defer_cursor_moves', default=True, missing='nothing')
    _cursor_move_debounce_ms: int = ConfigOption('cursor_move_debounce_ms', default=250, missing='nothing')
    # Use controller closed-loop for the one deferred move (single window parameter, nm)
    _use_closed_loop_for_deferred: bool = ConfigOption('use_closed_loop_for_deferred', default=True, missing='nothing')
    _closed_loop_window_nm: int = ConfigOption('closed_loop_window_nm', default=200,
                                               missing='nothing')  # ±200 nm window
    _closed_loop_timeout_s: float = ConfigOption('closed_loop_timeout_s', default=1.5, missing='nothing')
    _closed_loop_disable_after: bool = ConfigOption('closed_loop_disable_after', default=True, missing='nothing')

    # Internal state
    sigPositionChanged = QtCore.Signal(dict)
    _sigDeferredMoveRequested = QtCore.Signal(dict)
    _sigCancelDeferredMove = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._scan_settings: Optional[ScanSettings] = None
        self._back_cfg: Optional[ScanSettings] = None

        self._scan_data: Optional[ScanData] = None
        self._back_scan_data: Optional[ScanData] = None
        self.raw_data_container: Optional[RawDataContainer] = None
        self._constraints: Optional[ScanConstraints] = None

        self._thread_lock_data = Mutex()

        #Defered Movement
        self._scan_active: bool = False  # if not already present
        self._move_debounce_timer: Optional[QtCore.QTimer] = None
        self._pending_move_target: Optional[Dict[str, float]] = None
        self._ui_target: Dict[str, float] = {}

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

        # Re-emit position updates from motion so logic/GUI listening to THIS scanner get live cursor updates
        try:
            self._motion().sigPositionChanged.connect(self.sigPositionChanged.emit, QtCore.Qt.QueuedConnection)
        except Exception:
            pass

        # Emit current position once so the cursor/target snap to the actual position on activation (no motion)
        try:
            curr = self._motion().get_position()
            self.sigPositionChanged.emit(curr)
        except Exception:
            pass

        #Defered Movement
        self._move_debounce_timer = QtCore.QTimer(self)
        self._move_debounce_timer.setSingleShot(True)
        self._move_debounce_timer.timeout.connect(self._perform_deferred_move, QtCore.Qt.QueuedConnection)

        self._sigDeferredMoveRequested.connect(self._on_deferred_move_requested, QtCore.Qt.QueuedConnection)
        self._sigCancelDeferredMove.connect(self._on_cancel_deferred_move, QtCore.Qt.QueuedConnection)
        # Initialize and publish current position as UI target so cursor matches hardware initially
        try:
            curr = self._motion().get_position()
            self._ui_target = dict(curr)
            self.sigPositionChanged.emit(curr)
        except Exception:
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
            self.log.error('Cannot move the scanner while scan is running')
            return self._motion().get_target()

        if not set(position).issubset(self.constraints.axes):
            self.log.error('Invalid axes name in position')
            return self._motion().get_target()

        # For interactive GUI drags (non-blocking), defer the actual hardware move
        if self._defer_cursor_moves and not blocking:
            try:
                # hand off to module thread; will update shadow target and start debounce
                self._sigDeferredMoveRequested.emit(dict(position))
            except Exception:
                # if anything goes wrong, fall back to immediate move
                return self._motion().move_absolute(position, velocity=velocity, blocking=blocking)
            # Return the requested target so GUI/logic won’t snap back while we defer the hardware move
            return dict(position)

        return self._motion().move_absolute(position, velocity=velocity, blocking= blocking)

    def move_relative(self, distance: Dict[str, float], velocity: Optional[float] = None,
                      blocking: bool = False) -> Dict[str, float]:
        """ Move the scanning probe by a relative distance from the current target position as fast
                as possible or with a defined velocity.

                Log error and return current target position if something fails or a 1D/2D scan is in
                progress.
                """
        if self.is_scan_running():
            self.log.error('Cannot move the scanner while, scan is running')
            return self._motion().get_target()

            # Convert to absolute based on current target, then reuse move_absolute (will defer if configured)
        curr = self.get_target()
        absolute = {ax: curr.get(ax, 0.0) + float(d) for ax, d in distance.items()}
        return self.move_absolute(absolute, velocity=velocity, blocking=blocking)


    def get_target(self) -> Dict[str, float]:
        if self._defer_cursor_moves and self._ui_target:
            return dict(self._ui_target)
        return self._motion().get_target()

    def get_position(self) -> Dict[str, float]:
        return self._motion().get_position()

    @QtCore.Slot(dict)
    def _on_deferred_move_requested(self, position: Dict[str, float]):

        # Update shadow target immediately for smooth UI; clip to axes present
        if not self._ui_target:
            self._ui_target = dict(self._motion().get_target())
        for ax, val in position.items():
            self._ui_target[ax] = float(val)
        try:
            self.sigPositionChanged.emit(dict(self._ui_target))
        except Exception:
            pass

        # Coalesce pending move; restart debounce
        self._pending_move_target = dict(self._ui_target)
        if self._move_debounce_timer is not None:
            self._move_debounce_timer.start(int(self._cursor_move_debounce_ms))

    @QtCore.Slot()
    def _on_cancel_deferred_move(self):
        self._pending_move_target = None
        if self._move_debounce_timer is not None:
            self._move_debounce_timer.stop()

    @QtCore.Slot()
    def _perform_deferred_move(self):

        if self._pending_move_target is None:
            return
        # Do not interfere with scans
        if self.is_scan_running:
            self._pending_move_target = None
            return
        pos = self._pending_move_target
        self._pending_move_target = None
        try:
            # NEW: use controller closed-loop move for the consolidated cursor move (single window)
            if self._use_closed_loop_for_deferred and hasattr(self._motion(), 'move_absolute_closed_loop'):
                final_target = getattr(self._motion(), 'move_absolute_closed_loop')(
                    pos,
                    window_nm=int(self._closed_loop_window_nm),
                    timeout_s=float(self._closed_loop_timeout_s),
                    disable_after=bool(self._closed_loop_disable_after),
                )
            else:
                # Fallback: single consolidated move; blocking for cleanliness
                final_target = self._motion().move_absolute(pos, velocity=None, blocking=True)

            # Sync shadow target to hardware and notify UI
            self._ui_target = dict(final_target)
            self.sigPositionChanged.emit(dict(self._ui_target))
        except Exception:
            self.log.exception('Deferred move failed')

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

            #first_scan_position = {ax: pos[0] for ax, pos
            #                       in zip(self.scan_settings.axes, self.scan_settings.range)}
            #self._move_to_and_start_scan(first_scan_position)

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

        try:
            self._move_debounce_timer.stop()
            self._pending_move_target = None
        except Exception:
            pass

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
        try:
            if self.module_state() != 'idle':
                self.module_state.unlock()
        except Exception:
            pass
        self._sigCancelDeferredMove.emit()
        try:
            self._ui_target = self._motion().get_target()
            self.sigPositionChanged.emit(dict(self._ui_target))
        except Exception:
            pass

    @property
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

    def _get_scan_lines(self, settings: ScanSettings, back_settings: ScanSettings) -> Dict[str, np.ndarray]:
        if settings.scan_dimension == 1:
            axis = settings.axes[0]

            horizontal = np.linspace(settings.range[0][0], settings.range[0][1],
                                     settings.resolution[0])
            horizontal_return_line = np.linspace(settings.range[0][1], settings.range[0][0],
                                                 back_settings.resolution[0])

            horizontal_single_line = np.concatenate((horizontal,
                                                     horizontal_return_line))

            coord_dict = {axis: horizontal_single_line}

        elif settings.scan_dimension == 2:
            horizontal_resolution = settings.resolution[0]
            horizontal_back_resolution = back_settings.resolution[0]
            vertical_resolution = settings.resolution[1]

            # horizontal scan array / "fast axis"
            horizontal_axis = settings.axes[0]
            horizontal = np.linspace(settings.range[0][0], settings.range[0][1],
                                     horizontal_resolution)

            horizontal_return_line = np.linspace(settings.range[0][1],
                                                 settings.range[0][0],
                                                 horizontal_back_resolution)
            # a single back and forth line
            horizontal_single_line = np.concatenate((horizontal, horizontal_return_line))
            # need as much lines as we have in the vertical directions
            horizontal_scan_array = np.tile(horizontal_single_line, vertical_resolution)

            # vertical scan array / "slow axis"
            vertical_axis = settings.axes[1]
            vertical = np.linspace(settings.range[1][0], settings.range[1][1],
                                   vertical_resolution)

            # during horizontal line, the vertical line keeps its value
            vertical_lines = np.repeat(vertical.reshape(vertical_resolution, 1), horizontal_resolution, axis=1)
            # during backscan of horizontal, the vertical axis increases its value by "one index"
            vertical_return_lines = np.linspace(vertical[:-1], vertical[1:], horizontal_back_resolution).T
            # need to extend the vertical lines at the end, as we reach it earlier then for the horizontal axes
            vertical_return_lines = np.concatenate((vertical_return_lines,
                                                    np.ones((1, horizontal_back_resolution)) * vertical[-1]
                                                    ))

            vertical_scan_array = np.concatenate((vertical_lines, vertical_return_lines), axis=1).ravel()

            # TODO We could drop the last return line in the initialization, as it is not read in anyways till yet.

            coord_dict = {horizontal_axis: horizontal_scan_array,
                          vertical_axis: vertical_scan_array
            }

        else:
            raise ValueError(f"Not supported scan dimension: {settings.scan_dimension}")

        return self._expand_coordinate(coord_dict)

    def _init_scan_grid(self, settings: ScanSettings, back_settings: ScanSettings) -> Dict[str, np.ndarray]:
        scan_coords = self._get_scan_lines(settings, back_settings)
        return scan_coords

    def _check_scan_grid(self, scan_coords):
        for ax, coords in scan_coords.items():
            position_min = self.constraints.axes[ax].position.minimum
            position_max = self.constraints.axes[ax].position.maximum
            out_of_range = any(coords < position_min) or any(coords > position_max)

            if out_of_range:
                raise ValueError(f"Scan axis {ax} out of range [{position_min}, {position_max}]")

    def _clip_ranges(self, settings: ScanSettings):
        valid_scan_grid = False
        i_trial, n_max_trials = 0, 25

        while not valid_scan_grid and i_trial < n_max_trials:
            ranges = settings.range
            if i_trial > 0:
                ranges = self._shrink_scan_ranges(ranges)
            settings_dict = asdict(settings)
            settings_dict['range'] = ranges
            settings = ScanSettings.from_dict(settings_dict)

            try:
                self._init_scan_arrays(settings, settings)
                valid_scan_grid = True
            except ValueError:
                valid_scan_grid = False

            i_trial += 1

        if not valid_scan_grid:
            raise ValueError("Couldn't create scan grid. ")

        if i_trial > 1:
            self.log.warning(f"Adapted out-of-bounds scan range to {ranges}")
        return settings

    def _init_scan_arrays(self, settings: ScanSettings, back_settings: ScanSettings)\
            -> Dict[str, np.ndarray]:
        """
        @param ScanSettings settings: scan parameters

        @return dict: NI channel name to voltage 1D numpy array mapping for all axes
        """
        # TODO maybe need to clip to voltage range in case of float precision error in conversion?
        scan_coords = self._init_scan_grid(settings, back_settings)
        self._check_scan_grid(scan_coords)

        #scan_voltages = {self._ni_channel_mapping[ax]: self._position_to_voltage(ax, val) for ax, val in scan_coords.items()}
        return scan_coords

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