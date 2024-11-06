# -*- coding: utf-8 -*-
"""
This module is responsible for performing scanning probe measurements in order to find some optimal
position and move the scanner there.

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
from uuid import UUID

import numpy as np
from PySide2 import QtCore
import copy as cp
from typing import Dict, Tuple, List, Optional

from qudi.core.module import LogicBase
from qudi.interface.scanning_probe_interface import ScanData, BackScanCapability
from qudi.logic.scanning_probe_logic import ScanningProbeLogic
from qudi.util.mutex import RecursiveMutex, Mutex
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.fit_models.gaussian import Gaussian2D, Gaussian


class ScanningOptimizeLogic(LogicBase):
    """
    This logic module makes use of the scanning probe logic to perform a sequence of
    1D and 2D spatial signal optimization steps.

    Example config for copy-paste:

    scanning_optimize_logic:
        module.Class: 'scanning_optimize_logic.ScanningOptimizeLogic'
        connect:
            scan_logic: scanning_probe_logic

    """

    # declare connectors
    _scan_logic = Connector(name='scan_logic', interface='ScanningProbeLogic')

    # status variables
    # not configuring the back scan parameters is represented by empty dictionaries
    _scan_sequence: List[Tuple[str, ...]] = StatusVar(name='scan_sequence', default=None)
    _data_channel = StatusVar(name='data_channel', default=None)
    _scan_range: Dict[str, float] = StatusVar(name='scan_range', default=dict())
    _scan_resolution: Dict[str, int] = StatusVar(name='scan_resolution', default=dict())
    _back_scan_resolution: Dict[str, int] = StatusVar(name='back_scan_resolution', default=dict())
    _scan_frequency: Dict[str, float] = StatusVar(name='scan_frequency', default=dict())
    _back_scan_frequency: Dict[str, float] = StatusVar(name='back_scan_frequency', default=dict())

    # signals
    sigOptimizeStateChanged = QtCore.Signal(bool, dict, object)
    sigOptimizeSettingsChanged = QtCore.Signal(dict)

    _sigNextSequenceStep = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()
        self._result_lock = Mutex()

        self._sequence_index = 0
        self._optimal_position = dict()
        self._last_scans = list()
        self._last_fits = list()
        self._avail_axes = tuple()

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        scan_logic: ScanningProbeLogic = self._scan_logic()
        axes = scan_logic.scanner_axes
        channels = scan_logic.scanner_channels

        # check if settings in status variables are valid
        # reset to defaults if required
        try:
            self._check_scan_settings()
        except Exception as e:
            self.log.warning("Scan settings in Status Variable empty or invalid, using defaults.", exc_info=e)
            self._set_default_scan_settings()

        self._avail_axes = tuple(axes.values())
        if self._scan_sequence is None:
            if len(self._avail_axes) >= 3:
                self._scan_sequence = [(self._avail_axes[0].name, self._avail_axes[1].name),
                                       (self._avail_axes[2].name,)]
            elif len(self._avail_axes) == 2:
                self._scan_sequence = [(self._avail_axes[0].name, self._avail_axes[1].name)]
            elif len(self._avail_axes) == 1:
                self._scan_sequence = [(self._avail_axes[0].name,)]
            else:
                self._scan_sequence = list()
        if self._data_channel is None:
            self._data_channel = tuple(channels.values())[0].name

        self._sequence_index = 0
        self._optimal_position = dict()
        self._last_scans = list()
        self._last_fits = list()

        self._sigNextSequenceStep.connect(self._next_sequence_step, QtCore.Qt.QueuedConnection)
        self._scan_logic().sigScanStateChanged.connect(
            self._scan_state_changed, QtCore.Qt.QueuedConnection
        )

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self._scan_logic().sigScanStateChanged.disconnect(self._scan_state_changed)
        self._sigNextSequenceStep.disconnect()
        self.stop_optimize()
        return

    @property
    def data_channel(self) -> str:
        return self._data_channel

    @property
    def scan_range(self) -> Dict[str, float]:
        return self._scan_range.copy()

    @property
    def scan_resolution(self) -> Dict[str, int]:
        return self._scan_resolution.copy()

    @property
    def back_scan_resolution(self) -> Dict[str, int]:
        # use value of forward scan if not configured otherwise (merge dictionaries)
        return {**self._scan_resolution, **self._back_scan_resolution}

    @property
    def scan_frequency(self) -> Dict[str, float]:
        return self._scan_frequency.copy()

    @property
    def back_scan_frequency(self) -> Dict[str, float]:
        # use value of forward scan if not configured otherwise (merge dictionaries)
        return {**self._scan_frequency, **self._back_scan_frequency}

    @property
    def scan_sequence(self) -> List[Tuple[str, ...]]:
        # serialization into status variable changes step type <tuple> -> <list>
        return [tuple(i) for i in self._scan_sequence]

    @scan_sequence.setter
    def scan_sequence(self, sequence: List[Tuple[str, ...]]):
        """
        @param sequence: list or tuple of string tuples giving the scan order, e.g. [('x','y'), ('z')]
        """
        occurring_axes = set([axis for step in sequence for axis in step])
        available_axes = [ax.name for ax in self._avail_axes]
        if not occurring_axes.issubset(available_axes):
            self.log.error(f"Optimizer sequence {sequence} must contain only"
                           f" available axes ({available_axes}).")
        else:
            self._scan_sequence = sequence

    @property
    def optimizer_running(self):
        return self.module_state() != 'idle'

    def set_optimize_settings(self, data_channel: str, scan_sequence: List[Tuple[str, ...]],
                              range: Dict[str, float], resolution: Dict[str, int], frequency: Dict[str, float],
                              back_resolution: Dict[str, int] = None, back_frequency: Dict[str, float] = None):
        """Set all optimizer settings."""
        if back_resolution is None:
            back_resolution = dict()
        if back_frequency is None:
            back_frequency = dict()
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.error('Cannot change optimize settings when module is locked.')
            else:
                self._data_channel = data_channel
                self.scan_sequence = scan_sequence
                self._scan_range.update(range)
                self._scan_resolution.update(resolution)
                self._scan_frequency.update(frequency)
                self._back_scan_resolution.update(back_resolution)
                self._back_scan_frequency.update(back_frequency)

    @property
    def last_scans(self):
        with self._result_lock:
            return self._last_scans.copy()

    @property
    def last_fits(self):
        with self._result_lock:
            return self._last_fits.copy()

    @property
    def optimal_position(self):
        return self._optimal_position.copy()

    def toggle_optimize(self, start):
        if start:
            self.start_optimize()
        else:
            self.stop_optimize()

    def start_optimize(self):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigOptimizeStateChanged.emit(True, dict(), None)
                return

            scan_logic: ScanningProbeLogic = self._scan_logic()
            curr_pos = scan_logic.scanner_target
            constraints = scan_logic.scanner_constraints
            for ax, rel_rng in self.scan_range.items():
                rng_start = curr_pos[ax] - rel_rng / 2
                rng_stop = curr_pos[ax] + rel_rng / 2
                # range needs to be clipped if optimizing at the very edge
                rng_start = constraints.axes[ax].position.clip(rng_start)
                rng_stop = constraints.axes[ax].position.clip(rng_stop)
                scan_logic.set_scan_range(ax, (rng_start, rng_stop))

            for ax, res in self.scan_resolution.items():
                scan_logic.set_scan_resolution(ax, res)
            for ax, res in self.back_scan_resolution.items():
                scan_logic.set_back_scan_resolution(ax, res)
            for ax, res in self.scan_frequency.items():
                scan_logic.set_scan_frequency(ax, res)
            for ax, res in self.back_scan_frequency.items():
                scan_logic.set_back_scan_frequency(ax, res)

            # optimizer scans always explicitly configure the backwards scan settings
            scan_logic.set_use_back_scan_settings(True)

            self.module_state.lock()
            with self._result_lock:
                self._last_scans = list()
                self._last_fits = list()
            self._scan_logic().save_to_history = False  # optimizer scans not saved
            self._sequence_index = 0
            self._optimal_position = dict()
            self.sigOptimizeStateChanged.emit(True, self.optimal_position, None)
            self._sigNextSequenceStep.emit()

    def _next_sequence_step(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                return
            self._scan_logic().toggle_scan(True, self._scan_sequence[self._sequence_index], self.module_uuid)

    def _scan_state_changed(self, is_running: bool,
                            data: Optional[ScanData], back_scan_data: Optional[ScanData],
                            caller_id: UUID):
        with self._thread_lock:
            if is_running or self.module_state() == 'idle' or caller_id != self.module_uuid:
                return
            elif not is_running and data is None:
                # scan could not be started due to some error
                self.stop_optimize()
            elif data is not None:
                #self.log.debug(f"Trying to fit on data after scan of dim {data.scan_dimension}")

                try:
                    if data.settings.scan_dimension == 1:
                        x = np.linspace(*data.settings.range[0], data.settings.resolution[0])
                        opt_pos, fit_data, fit_res = self._get_pos_from_1d_gauss_fit(
                            x,
                            data.data[self._data_channel]
                        )
                    else:
                        x = np.linspace(*data.settings.range[0], data.settings.resolution[0])
                        y = np.linspace(*data.settings.range[1], data.settings.resolution[1])
                        xy = np.meshgrid(x, y, indexing='ij')
                        opt_pos, fit_data, fit_res = self._get_pos_from_2d_gauss_fit(
                            xy,
                            data.data[self._data_channel].ravel()
                        )

                    position_update = {ax: opt_pos[ii] for ii, ax in enumerate(data.settings.axes)}
                    #self.log.debug(f"Optimizer issuing position update: {position_update}")
                    if fit_data is not None:
                        new_pos = self._scan_logic().set_target_position(position_update, move_blocking=True)
                        for ax in tuple(position_update):
                            position_update[ax] = new_pos[ax]

                        fit_data = {'fit_data': fit_data, 'full_fit_res': fit_res}

                    self._optimal_position.update(position_update)
                    with self._result_lock:
                        self._last_scans.append(cp.copy(data))
                        self._last_fits.append(fit_res)
                    self.sigOptimizeStateChanged.emit(True, position_update, fit_data)

                    # Abort optimize if fit failed
                    if fit_data is None:
                        self.log.warning("Stopping optimization due to failed fit.")
                        self.stop_optimize()
                        return

                except:
                    self.log.exception("")

            self._sequence_index += 1

            # Terminate optimize sequence if finished; continue with next sequence step otherwise
            if self._sequence_index >= len(self._scan_sequence):
                self.stop_optimize()
            else:
                self._sigNextSequenceStep.emit()
            return

    def stop_optimize(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.sigOptimizeStateChanged.emit(False, dict(), None)
                return

            try:
                if self._scan_logic().module_state() != 'idle':
                    # optimizer scans are never saved in scanning history
                    self._scan_logic().stop_scan()
            finally:
                self._scan_logic().save_to_history = True
                self.module_state.unlock()
                self.sigOptimizeStateChanged.emit(False, dict(), None)

    def _get_pos_from_2d_gauss_fit(self, xy, data):
        model = Gaussian2D()

        try:
            fit_result = model.fit(data, x=xy, **model.estimate_peak(data, xy))
        except:
            x_min, x_max = xy[0].min(), xy[0].max()
            y_min, y_max = xy[1].min(), xy[1].max()
            x_middle = (x_max - x_min) / 2 + x_min
            y_middle = (y_max - y_min) / 2 + y_min
            self.log.exception('2D Gaussian fit unsuccessful.')
            return (x_middle, y_middle), None, None

        return (fit_result.best_values['center_x'],
                fit_result.best_values['center_y']), fit_result.best_fit.reshape(xy[0].shape), fit_result

    def _get_pos_from_1d_gauss_fit(self, x, data):
        model = Gaussian()

        try:
            fit_result = model.fit(data, x=x, **model.estimate_peak(data, x))
        except:
            x_min, x_max = x.min(), x.max()
            middle = (x_max - x_min) / 2 + x_min
            self.log.exception('1D Gaussian fit unsuccessful.')
            return (middle,), None, None

        return (fit_result.best_values['center'],), fit_result.best_fit, fit_result

    def _check_scan_settings(self):
        """Basic check of scan settings for all axes."""
        scan_logic: ScanningProbeLogic = self._scan_logic()
        capability = scan_logic.back_scan_capability
        if self._back_scan_resolution and (BackScanCapability.RESOLUTION_CONFIGURABLE not in capability):
            raise AssertionError('Back scan resolution cannot be configured for this scanner hardware.')
        if self._back_scan_frequency and (BackScanCapability.FREQUENCY_CONFIGURABLE not in capability):
            raise AssertionError('Back scan frequency cannot be configured for this scanner hardware.')
        for name, ax in scan_logic.scanner_axes.items():
            ax.position.check(self.scan_range[name])
            ax.resolution.check(self.scan_resolution[name])
            ax.resolution.check(self.back_scan_resolution[name])
            ax.frequency.check(self.scan_frequency[name])
            ax.frequency.check(self.back_scan_frequency[name])

    def _set_default_scan_settings(self):
        """Set range, resolution and frequency to default values."""
        scan_logic: ScanningProbeLogic = self._scan_logic()
        axes = scan_logic.scanner_axes
        self._scan_range = {ax.name: abs(ax.position.maximum - ax.position.minimum) / 100 for ax in axes.values()}
        self._scan_resolution = {ax.name: max(16, ax.resolution.minimum) for ax in axes.values()}
        self._scan_frequency = {ax.name: max(ax.frequency.minimum, ax.frequency.maximum / 100) for ax in axes.values()}
        self._back_scan_resolution = {}
        self._back_scan_frequency = {}
