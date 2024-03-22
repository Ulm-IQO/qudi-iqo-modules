# -*- coding: utf-8 -*-
"""
This module is responsible for controlling any kind of scanning probe imaging for 1D and 2D
scanning.

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
from itertools import combinations
from typing import Tuple, Sequence, Dict

from PySide2 import QtCore
import copy as cp

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.interface.scanning_probe_interface import ScanSettings, ScanConstraints, BackScanCapability


class ScanningProbeLogic(LogicBase):
    """
    This is the Logic class for 1D/2D SPM measurements.
    Scanning in this context means moving something along 1 or 2 dimensions and collecting data from
    possibly multiple sources at each position.

    Example config for copy-paste:

    scanning_probe_logic:
        module.Class: 'scanning_probe_logic.ScanningProbeLogic'
        options:
            max_history_length: 20
            max_scan_update_interval: 2
            position_update_interval: 1
        connect:
            scanner: scanner_dummy

    """

    # declare connectors
    _scanner = Connector(name='scanner', interface='ScanningProbeInterface')

    # status vars
    _scan_ranges = StatusVar(name='scan_ranges', default=dict())
    _scan_resolution = StatusVar(name='scan_resolution', default=dict())
    _back_scan_resolution = StatusVar(name='back_scan_resolution', default=dict())
    _scan_frequency = StatusVar(name='scan_frequency', default=dict())
    _back_scan_frequency = StatusVar(name='back_scan_frequency', default=dict())

    # config options
    _min_poll_interval = ConfigOption(name='min_poll_interval', default=None)

    # signals
    sigScanStateChanged = QtCore.Signal(bool, object, object)
    sigNewScanDataForHistory = QtCore.Signal(object)
    sigScannerTargetChanged = QtCore.Signal(dict, object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        # others
        self.__scan_poll_timer = None
        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid
        self._save_to_hist = True

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._save_to_hist = True

        # check if scan settings in status variables are valid
        # reset to defaults if required
        if not all([self.scan_ranges, self.scan_resolution, self.scan_frequency]):
            self.log.debug(f"No status variables present, using default scan settings.")
            self.set_default_scan_settings()
        try:
            self.check_scan_settings()
        except Exception as e:
            self.log.warning("Scan settings in Status Variable invalid, using defaults.", exc_info=e)
            self.set_default_scan_settings()

        axes = self.scanner_constraints.axes
        if not self._min_poll_interval:
            # defaults to maximum scan frequency of scanner
            self._min_poll_interval = 1 / max([axes[ax].frequency.maximum for ax in axes])

        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid

        self.__scan_poll_timer = QtCore.QTimer()
        self.__scan_poll_timer.setSingleShot(True)
        self.__scan_poll_timer.timeout.connect(self.__scan_poll_loop, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self.__scan_poll_timer.stop()
        self.__scan_poll_timer.timeout.disconnect()
        if self.module_state() != 'idle':
            self._scanner().stop_scan()

    @property
    def scan_data(self):
        with self._thread_lock:
            return self._scanner().get_scan_data()

    @property
    def scanner_position(self):
        with self._thread_lock:
            return self._scanner().get_position()

    @property
    def scanner_target(self):
        with self._thread_lock:
            return self._scanner().get_target()

    @property
    def scanner_axes(self):
        return self.scanner_constraints.axes

    @property
    def scanner_channels(self):
        return self.scanner_constraints.channels

    @property
    def scanner_constraints(self) -> ScanConstraints:
        return self._scanner().constraints

    @property
    def back_scan_capability(self) -> BackScanCapability:
        return self.scanner_constraints.back_scan_capability

    @property
    def scan_ranges(self) -> Dict[str, Tuple[float, float]]:
        with self._thread_lock:
            return cp.copy(self._scan_ranges)

    @property
    def scan_resolution(self) -> Dict[str, int]:
        with self._thread_lock:
            return cp.copy(self._scan_resolution)

    @property
    def back_scan_resolution(self) -> Dict[str, int]:
        with self._thread_lock:
            if self._back_scan_resolution:
                return cp.copy(self._back_scan_resolution)
            else:
                return self.scan_resolution

    @property
    def scan_frequency(self) -> Dict[str, float]:
        with self._thread_lock:
            return cp.copy(self._scan_frequency)

    @property
    def back_scan_frequency(self) -> Dict[str, float]:
        with self._thread_lock:
            if self._back_scan_frequency:
                return cp.copy(self._back_scan_frequency)
            else:
                return self.scan_frequency

    @property
    def save_to_history(self) -> bool:
        """Whether to save finished scans to history."""
        with self._thread_lock:
            return self._save_to_hist

    @save_to_history.setter
    def save_to_history(self, save: bool) -> None:
        with self._thread_lock:
            self._save_to_hist = save

    def create_scan_settings(self, scan_axes: Sequence[str]) -> ScanSettings:
        """Create a ScanSettings object for a selected 1D or 2D scan."""
        with self._thread_lock:
            return ScanSettings(
                channels=tuple(self.scanner_channels),
                axes=tuple(scan_axes),
                range=tuple(self._scan_ranges[ax] for ax in scan_axes),
                resolution=tuple(self._scan_resolution[ax] for ax in scan_axes),
                frequency=self._scan_frequency[scan_axes[0]],
            )

    def create_back_scan_settings(self, scan_axes: Sequence[str]) -> ScanSettings:
        """Create a ScanSettings object for the backwards direction of a selected 1D or 2D scan."""
        with self._thread_lock:
            return ScanSettings(
                channels=tuple(self.scanner_channels),
                axes=tuple(scan_axes),
                range=tuple(self.scan_ranges[ax] for ax in scan_axes),
                resolution=tuple(self.back_scan_resolution[ax] for ax in scan_axes),
                frequency=self.back_scan_frequency[scan_axes[0]],
            )

    def check_scan_settings(self):
        """Validate current scan settings for all possible 1D and 2D scans."""
        for dim in [1, 2]:
            for axes in combinations(self.scanner_axes, dim):
                settings = self.create_scan_settings(axes)
                self.scanner_constraints.check_settings(settings)
                back_settings = self.create_back_scan_settings(axes)
                self.scanner_constraints.check_back_scan_settings(back_settings, settings)

    def set_scan_range(self, axis: str, rng: Tuple[float, float]) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan ranges.')
            else:
                old_scan_ranges = self.scan_ranges.copy()
                self._scan_ranges[axis] = rng
                try:
                    # check only the axis with the change
                    settings = self.create_scan_settings([axis])
                    self.scanner_constraints.check_settings(settings)
                except Exception as e:
                    self.log.error("Invalid scan range or axis name.", exc_info=e)
                    self._scan_ranges = old_scan_ranges

    def set_scan_resolution(self, axis: str, resolution: int) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan resolution.')
            else:
                old_scan_resolution = self.scan_resolution.copy()
                self._scan_resolution[axis] = resolution
                try:
                    # check only the axis with the change
                    settings = self.create_scan_settings([axis])
                    self.scanner_constraints.check_settings(settings)
                except Exception as e:
                    self.log.error("Invalid scan resolution or axis name.", exc_info=e)
                    self._scan_resolution = old_scan_resolution

    def set_back_scan_resolution(self, axis: str, resolution: int) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change back scan resolution.')
            elif BackScanCapability.RESOLUTION_CONFIGURABLE not in self.back_scan_capability:
                self.log.error('Back scan resolution is not configurable for this scanner.')
            else:
                old_back_scan_resolution = self.back_scan_resolution.copy()
                self._back_scan_resolution[axis] = resolution
                try:
                    # check only the axis with the change
                    forward_settings = self.create_scan_settings([axis])
                    back_settings = self.create_back_scan_settings([axis])
                    self.scanner_constraints.check_back_scan_settings(back_settings, forward_settings)
                except Exception as e:
                    self.log.error("Invalid back scan setting.", exc_info=e)
                    self._back_scan_resolution = old_back_scan_resolution

    def set_scan_frequency(self, axis: str, frequency: float) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan frequency.')
            else:
                old_scan_frequency = self.scan_frequency.copy()
                self._scan_frequency[axis] = frequency
                try:
                    # check only the axis with the change
                    settings = self.create_scan_settings([axis])
                    self.scanner_constraints.check_settings(settings)
                except Exception as e:
                    self.log.error("Invalid scan frequency or axis name.", exc_info=e)
                    self._scan_frequency = old_scan_frequency

    def set_back_scan_frequency(self, axis: str, frequency: float) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change back scan frequency.')
            elif BackScanCapability.FREQUENCY_CONFIGURABLE not in self.back_scan_capability:
                self.log.error('Back scan frequency is not configurable for this scanner.')
            else:
                old_back_scan_frequency = self.back_scan_frequency.copy()
                self._back_scan_frequency[axis] = frequency
                try:
                    # check only the axis with the change
                    forward_settings = self.create_scan_settings([axis])
                    back_settings = self.create_back_scan_settings([axis])
                    self.scanner_constraints.check_back_scan_settings(back_settings, forward_settings)
                except Exception as e:
                    self.log.error("Invalid back scan frequency setting.", exc_info=e)
                    self._back_scan_frequency = old_back_scan_frequency

    def set_target_position(self, pos_dict, caller_id=None, move_blocking=False):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.error('Unable to change scanner target position while a scan is running.')
                new_pos = self._scanner().get_target()
                self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                return new_pos

            ax_constr = self.scanner_constraints.axes
            new_pos = pos_dict.copy()
            for ax, pos in pos_dict.items():
                if ax not in ax_constr:
                    self.log.error('Unknown scanner axis: "{0}"'.format(ax))
                    new_pos = self._scanner().get_target()
                    self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                    return new_pos

                new_pos[ax] = ax_constr[ax].position.clip(pos)
                if pos != new_pos[ax]:
                    self.log.warning('Scanner position target value out of bounds for axis "{0}". '
                                     'Clipping value to {1:.3e}.'.format(ax, new_pos[ax]))

            new_pos = self._scanner().move_absolute(new_pos, blocking=move_blocking)
            if any(pos != new_pos[ax] for ax, pos in pos_dict.items()):
                caller_id = None
            #self.log.debug(f"Logic set target with id {caller_id} to new: {new_pos}")
            self.sigScannerTargetChanged.emit(
                new_pos,
                self.module_uuid if caller_id is None else caller_id
            )
            return new_pos

    def toggle_scan(self, start, scan_axes, caller_id=None):
        with self._thread_lock:
            if start:
                self.start_scan(scan_axes, caller_id)
            else:
                self.stop_scan()

    def start_scan(self, scan_axes, caller_id=None):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
                return

            self.log.debug('Starting scan.')
            self._curr_caller_id = self.module_uuid if caller_id is None else caller_id

            self.module_state.lock()
            settings = self.create_scan_settings(tuple(scan_axes))
            back_settings = self.create_back_scan_settings(tuple(scan_axes))
            self.log.debug('Attempting to configure scanner...')
            try:
                self._scanner().configure_scan(settings)
                if self.back_scan_frequency or self.back_scan_resolution:
                    # only if these are non-empty dicts
                    self._scanner().configure_back_scan(back_settings)
            except Exception as e:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, None, self._curr_caller_id)
                self.log.error('Could not set scan settings on scanning probe hardware.', exc_info=e)
                return
            self.log.debug('Successfully configured scanner.')

            # Calculate poll time to check for scan completion. Use line scan time estimate.
            line_points = self._scan_resolution[scan_axes[0]] if len(scan_axes) > 1 else 1
            self.__scan_poll_interval = max(self._min_poll_interval,
                                            line_points / self._scan_frequency[scan_axes[0]])
            self.__scan_poll_timer.setInterval(int(round(self.__scan_poll_interval * 1000)))

            try:
                self._scanner().start_scan()
            except:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, None, self._curr_caller_id)
                self.log.error("Couldn't start scan.")

            self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
            self.__start_timer()
            return

    def stop_scan(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)
                return

            self.__stop_timer()

            try:
                if self._scanner().module_state() != 'idle':
                    self._scanner().stop_scan()
            finally:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)
                if self.save_to_history:
                    self.sigNewScanDataForHistory.emit(self.scan_data)

    def __scan_poll_loop(self):
        with self._thread_lock:
            try:
                if self.module_state() == 'idle':
                    return

                if self._scanner().module_state() == 'idle':
                    self.stop_scan()
                    return
                # TODO Added the following line as a quick test; Maybe look at it with more caution if correct
                self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)

                # Queue next call to this slot
                self.__scan_poll_timer.start()
            except TimeoutError:
                self.log.exception('Timed out while waiting for scan data:')
            except:
                self.log.exception('An exception was raised while polling the scan:')
            return

    def set_default_scan_settings(self):
        axes = self.scanner_constraints.axes
        self._scan_ranges = {ax: axes[ax].position.bounds for ax in self.scanner_axes}
        self._scan_resolution = {ax: axes[ax].resolution.default for ax in self.scanner_axes}
        self._scan_frequency = {ax: axes[ax].frequency.default for ax in self.scanner_axes}

    def set_full_scan_ranges(self):
        for name, axis in self.scanner_constraints.axes.items():
            self.set_scan_range(name, axis.position.bounds)
        return self.scan_ranges

    def __start_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__scan_poll_timer,
                                            'start',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__scan_poll_timer.start()

    def __stop_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__scan_poll_timer,
                                            'stop',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__scan_poll_timer.stop()
