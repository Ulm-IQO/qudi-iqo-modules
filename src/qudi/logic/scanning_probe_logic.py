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
from typing import Tuple, Sequence, Dict, Optional
from uuid import UUID
import copy as cp
from collections import OrderedDict

from PySide2 import QtCore
import numpy as np

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.util.datastorage import DataStorageBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.interface.scanning_probe_interface import ScanSettings, ScanConstraints, BackScanCapability, ScanData
from qudi.util.linear_transform import find_changing_axes, LinearTransformation3D
from qudi.util.linear_transform import compute_rotation_matrix_to_plane, compute_reduced_vectors


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
            save_coord_to_global_metadata: True # default
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
    _use_back_scan_settings: bool = StatusVar(name='use_back_scan_settings', default=False)
    _tilt_corr_settings = StatusVar(name='tilt_corr_settings', default={})

    # config options
    _min_poll_interval = ConfigOption(name='min_poll_interval', default=None)
    _save_coord_to_global_metadata = ConfigOption(name='save_coord_to_global_metadata', default=True)

    # signals
    sigScanStateChanged = QtCore.Signal(bool, ScanData, ScanData, UUID)
    sigNewScanDataForHistory = QtCore.Signal(ScanData, ScanData)
    sigScannerTargetChanged = QtCore.Signal(dict, object)
    sigScanSettingsChanged = QtCore.Signal()
    sigTiltCorrSettingsChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        # others
        self.__scan_poll_timer = None
        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid
        self._save_to_hist = True
        self._tilt_corr_transform = None
        self._tilt_corr_axes = []

    def on_activate(self):
        """Initialisation performed during activation of the module."""
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

        self._scan_axes = OrderedDict(sorted(self._scanner().constraints.axes.items()))
        if self._save_coord_to_global_metadata:
            pos_dict = self._scanner().get_position()
            for (k,v) in pos_dict.items():
                DataStorageBase.add_global_metadata("scanner_" + k, v, overwrite=True)

    def on_deactivate(self):
        """Reverse steps of activation"""
        self.__scan_poll_timer.stop()
        self.__scan_poll_timer.timeout.disconnect()
        if self.module_state() != 'idle':
            self._scanner().stop_scan()

    @property
    def scan_data(self) -> Optional[ScanData]:
        with self._thread_lock:
            return self._scanner().get_scan_data()

    @property
    def back_scan_data(self) -> Optional[ScanData]:
        with self._thread_lock:
            return self._scanner().get_back_scan_data()

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
            return self._scan_ranges.copy()

    @property
    def scan_resolution(self) -> Dict[str, int]:
        with self._thread_lock:
            return self._scan_resolution.copy()

    @property
    def back_scan_resolution(self) -> Dict[str, int]:
        """Resolution for the backwards scan of the fast axis."""
        with self._thread_lock:
            # use value of forward scan if not configured otherwise (merge dictionaries)
            return {**self._scan_resolution.copy(), **self._back_scan_resolution.copy()}

    @property
    def scan_frequency(self) -> Dict[str, float]:
        with self._thread_lock:
            return self._scan_frequency.copy()

    @property
    def back_scan_frequency(self) -> Dict[str, float]:
        with self._thread_lock:
            # use value of forward scan if not configured otherwise (merge dictionaries)
            return {**self._scan_frequency.copy(), **self._back_scan_frequency.copy()}

    @property
    def use_back_scan_settings(self) -> bool:
        with self._thread_lock:
            return self._use_back_scan_settings

    def set_use_back_scan_settings(self, use: bool) -> None:
        with self._thread_lock:
            self._use_back_scan_settings = use

            self.sigScanSettingsChanged.emit()

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
                range=tuple(tuple(self._scan_ranges[ax]) for ax in scan_axes),
                resolution=tuple(self._scan_resolution[ax] for ax in scan_axes),
                frequency=self._scan_frequency[scan_axes[0]],
            )

    def create_back_scan_settings(self, scan_axes: Sequence[str]) -> ScanSettings:
        """Create a ScanSettings object for the backwards direction of a selected 1D or 2D scan."""
        with self._thread_lock:
            # only use backwards scan resolution for the fast axis
            resolution = [self.back_scan_resolution[scan_axes[0]]]
            if len(scan_axes) > 1:
                # slow axis resolution always matches the forward scan
                resolution += [self.scan_resolution[ax] for ax in scan_axes[1:]]
            return ScanSettings(
                channels=tuple(self.scanner_channels),
                axes=tuple(scan_axes),
                range=tuple(tuple(self._scan_ranges[ax]) for ax in scan_axes),
                resolution=tuple(resolution),
                frequency=self.back_scan_frequency[scan_axes[0]],
            )

    def get_scan_settings_per_ax(self) -> Sequence[Tuple[ScanSettings, ScanSettings]]:
        all_settings = []
        for axes in self.scanner_axes:
            settings = self.create_scan_settings(axes)
            back_settings = self.create_back_scan_settings(axes)
            all_settings.append((settings, back_settings))

        return all_settings

    def set_scan_settings(self, setting: ScanSettings) -> None:
        """
        Set scan settings for an axis all at once through object.
        """

        if len(setting.axes) != 1:
            raise ValueError(f"Can only configure single axes, not {setting.axes}")

        axis = setting.axes[0]

        self.set_scan_range(axis, setting.range[0])
        self.set_scan_resolution(axis, setting.resolution[0])
        self.set_scan_frequency(axis, setting.frequency)

    def set_back_scan_settings(self, setting: ScanSettings) -> None:
        """
        Set back scan settings for an axis all at once through object.
        """

        if len(setting.axes) != 1:
            raise ValueError(f"Can only configure single axes, not {setting.axes}")

        axis = setting.axes[0]

        if not self.use_back_scan_settings:
            self.set_use_back_scan_settings(True)
            self.log.info(f"Tried to set a back scan setting without back scans turned on."
                          f" Now use_back_scan_settings= {self.use_back_scan_settings}")

        self.set_back_scan_resolution(axis, setting.resolution[0])
        self.set_back_scan_frequency(axis, setting.frequency)

    def check_scan_settings(self):
        """Validate current scan settings for all possible 1D and 2D scans."""
        for stg in [self.scan_ranges, self.scan_resolution, self.scan_frequency]:
            axs = stg.keys()
            for ax in axs:
                if ax not in self.scanner_axes.keys():
                    self.log.debug(f"Axis {ax} from scan settings not available on scanner" )
                    raise ValueError

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
                old_scan_ranges = self.scan_ranges
                self._scan_ranges[axis] = rng
                try:
                    # check only the axis with the change
                    settings = self.create_scan_settings([axis])
                    self.scanner_constraints.check_settings(settings)
                except Exception as e:
                    self.log.error("Invalid scan range or axis name.", exc_info=e)
                    self._scan_ranges = old_scan_ranges

                self.sigScanSettingsChanged.emit()

    def set_scan_resolution(self, axis: str, resolution: int) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan resolution.')
            else:
                old_scan_resolution = self.scan_resolution
                self._scan_resolution[axis] = resolution
                try:
                    # check only the axis with the change
                    settings = self.create_scan_settings([axis])
                    self.scanner_constraints.check_settings(settings)
                except Exception as e:
                    self.log.error("Invalid scan resolution or axis name.", exc_info=e)
                    self._scan_resolution = old_scan_resolution

                self.sigScanSettingsChanged.emit()

    def set_back_scan_resolution(self, axis: str, resolution: int) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change back scan resolution.')
            elif BackScanCapability.RESOLUTION_CONFIGURABLE not in self.back_scan_capability:
                # ignore if the value is same as forward setting or zero (used in gui if back scan not available)
                if resolution != self.scan_resolution[axis] and resolution != 0:
                    self.log.error('Backward scan resolution must be the same as forward resolution for this scanner.')
            else:
                old_back_scan_resolution = self.back_scan_resolution
                self._back_scan_resolution[axis] = resolution
                try:
                    # check only the axis with the change
                    forward_settings = self.create_scan_settings([axis])
                    back_settings = self.create_back_scan_settings([axis])
                    self.scanner_constraints.check_back_scan_settings(back_settings, forward_settings)
                except Exception as e:
                    self.log.error("Invalid back scan resolution setting.", exc_info=e)
                    self._back_scan_resolution = old_back_scan_resolution

                self.sigScanSettingsChanged.emit()

    def set_scan_frequency(self, axis: str, frequency: float) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan frequency.')
            else:
                old_scan_frequency = self.scan_frequency
                self._scan_frequency[axis] = frequency
                try:
                    # check only the axis with the change
                    settings = self.create_scan_settings([axis])
                    self.scanner_constraints.check_settings(settings)
                except Exception as e:
                    self.log.error("Invalid scan frequency or axis name.", exc_info=e)
                    self._scan_frequency = old_scan_frequency

                self.sigScanSettingsChanged.emit()

    def set_back_scan_frequency(self, axis: str, frequency: float) -> None:
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change back scan frequency.')
            elif BackScanCapability.FREQUENCY_CONFIGURABLE not in self.back_scan_capability:
                # ignore if the value is same as forward setting or zero (used in gui if back scan not available)
                if frequency != self.scan_frequency[axis] and frequency != 0.0:
                    self.log.error('Backward scan frequency must be the same as forward frequency for this scanner.')
            else:
                old_back_scan_frequency = self.back_scan_frequency
                self._back_scan_frequency[axis] = frequency
                try:
                    # check only the axis with the change
                    forward_settings = self.create_scan_settings([axis])
                    back_settings = self.create_back_scan_settings([axis])
                    self.scanner_constraints.check_back_scan_settings(back_settings, forward_settings)
                except Exception as e:
                    self.log.error("Invalid back scan frequency setting.", exc_info=e)
                    self._back_scan_frequency = old_back_scan_frequency

                self.sigScanSettingsChanged.emit()

    def set_target_position(self, pos_dict, caller_id=None, move_blocking=False):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.error('Unable to change scanner target position while a scan is running.')
                new_pos = self._scanner().get_target()
                self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                return new_pos

            # self.log.debug(f"Requested Set pos to= {pos_dict}")
            ax_constr = self.scanner_constraints.axes
            pos_dict = self._scanner()._expand_coordinate(cp.copy(pos_dict))
            # self.log.debug(f"Expand to= {pos_dict}")

            pos_dict = self._scanner().coordinate_transform(pos_dict)
            if self._save_coord_to_global_metadata:
                for (k,v) in pos_dict.items():
                    DataStorageBase.add_global_metadata("scanner_" + k, v, overwrite=True)

            for ax, pos in pos_dict.items():
                if ax not in ax_constr:
                    self.log.error('Unknown scanner axis: "{0}"'.format(ax))
                    new_pos = self._scanner().get_target()
                    self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                    return new_pos

                pos_dict[ax] = ax_constr[ax].position.clip(pos)
                if pos != pos_dict[ax]:
                    self.log.warning(
                        f'Scanner position target value {pos:.3e} out of bounds for axis "{ax}". '
                        f'Clipping value to {pos_dict[ax]:.3e}.'
                    )

            # move_absolute expects untransformed coordinatess, so invert clipped pos
            pos_dict = self._scanner().coordinate_transform(pos_dict, inverse=True)
            # self.log.debug(f"In front of hw.move_abs {pos_dict}")
            new_pos = self._scanner().move_absolute(pos_dict, blocking=move_blocking)
            # self.log.debug(f"Set pos to= {pos_dict} => new pos {new_pos}. Bare {self._scanner()._get_position_bare()}")
            if any(pos != new_pos[ax] for ax, pos in pos_dict.items()):
                caller_id = None
            # self.log.debug(f"Logic set target with id {caller_id} to new: {new_pos}")
            self.sigScannerTargetChanged.emit(new_pos, self.module_uuid if caller_id is None else caller_id)
            return new_pos

    def toggle_scan(self, start, scan_axes, caller_id=None):
        if start:
            self.start_scan(scan_axes, caller_id)
        else:
            self.stop_scan()

    def toggle_tilt_correction(self, enable=True):
        target_pos = self._scanner().get_target()
        is_enabled = self._scanner().coordinate_transform_enabled

        func = self.__transform_func if self._tilt_corr_transform else None

        if enable:
            self._scanner().set_coordinate_transform(func, self._tilt_corr_transform)
        else:
            self._scanner().set_coordinate_transform(None)

        if enable != is_enabled:
            # set target pos again with updated, (dis-) engaged tilt correction
            self.set_target_position(target_pos, move_blocking=True)

    @property
    def tilt_correction_settings(self):
        return self._tilt_corr_settings

    def configure_tilt_correction(self, support_vecs=None, shift_vec=None):
        """
        Configure the tilt correction with a set of support vector that define the tilted plane
        that should be horizontal after the correction

        @param list support_vecs: list of dicts. Each dict contains the scan axis as keys.
        @param dict shift_vec: Vector that defines the origin of rotation.
        """

        if support_vecs is None:
            self._tilt_corr_transform = None
            return

        support_vecs_arr = np.asarray(self.tilt_vector_dict_2_array(support_vecs, reduced_dim=False))
        if shift_vec is not None:
            shift_vec_arr = np.array(self.tilt_vector_dict_2_array(shift_vec, reduced_dim=False))

        if support_vecs_arr.shape[0] != 3:
            raise ValueError(f"Need 3 n-dim support vectors, not {support_vecs_arr.shape[0]}")

        auto_origin = False
        if shift_vec is None:
            auto_origin = True
            red_support_vecs = compute_reduced_vectors(support_vecs_arr)
            shift_vec_arr = np.mean(red_support_vecs, axis=0)
            shift_vec = self.tilt_vector_array_2_dict(shift_vec_arr, reduced_dim=True)
        else:
            red_support_vecs = np.vstack([support_vecs_arr, shift_vec_arr])
            red_vecs = compute_reduced_vectors(red_support_vecs)
            red_support_vecs = red_vecs[:-1, :]
            shift_vec_arr = red_vecs[-1, :]

        tilt_axes = find_changing_axes(support_vecs_arr)

        if red_support_vecs.shape != (3, 3) or shift_vec_arr.shape[0] != 3:
            n_dim = support_vecs_arr.shape[1]
            raise ValueError(
                f"Can't calculate tilt in >3 dimensions. "
                f"Given support vectors (dim= {n_dim}) must be constant in exactly {n_dim-3} dims. "
            )

        rot_mat = compute_rotation_matrix_to_plane(red_support_vecs[0], red_support_vecs[1], red_support_vecs[2])
        shift = shift_vec_arr

        # shift coord system to origin, rotate and shift shift back according to LT(x) = (R+s)*x - R*s
        lin_transform = LinearTransformation3D()
        shift_vec_transform = LinearTransformation3D()

        lin_transform.add_rotation(rot_mat)
        lin_transform.translate(shift[0], shift[1], shift[2])

        shift_vec_transform.add_rotation(rot_mat)
        shift_back = shift_vec_transform(-shift)

        lin_transform.translate(shift_back[0], shift_back[1], shift_back[2])

        self._tilt_corr_transform = lin_transform
        self._tilt_corr_axes = [el for idx, el in enumerate(self._scan_axes) if tilt_axes[idx]]
        self._tilt_corr_settings = {
            'auto_origin': auto_origin,
            'vec_1': support_vecs[0],
            'vec_2': support_vecs[1],
            'vec_3': support_vecs[2],
            'vec_shift': shift_vec,
        }

        # self.log.debug(f"Shift vec {shift}, shift back {shift_back}")
        # self.log.debug(f"Matrix: {lin_transform.matrix}")
        # self.log.debug(f"Configured tilt corr: {support_vecs}, {shift_vec}")

        self.sigTiltCorrSettingsChanged.emit(self._tilt_corr_settings)

    def tilt_vector_dict_2_array(self, vector, reduced_dim=False):
        """
        Convert vectors given as dict (with axes keys) to arrays and ensure correct order.

        @param dict vector: (single coord or arrays per key) or list of dicts
        @param bool reduced_dim: The vector given has been reduced to 3 dims (from n-dim for arbitrary vectors)
        @return np.array or list of np.array: vector(s) as array
        """

        axes = self._tilt_corr_axes if reduced_dim else self._scan_axes.keys()

        if type(vector) != list:
            vectors = [vector]
        else:
            vectors = vector

        vecs_arr = []
        for vec in vectors:
            if not isinstance(vec, dict):
                raise ValueError

            # vec_sorted dict has correct order (defined by order in axes). Then converted to array
            vec_sorted = {ax: np.nan for ax in axes}
            vec_sorted.update(vec)
            vec_arr = np.asarray(list(vec_sorted.values()))

            vecs_arr.append(vec_arr)

        if len(vecs_arr) == 1:
            return vecs_arr[0]
        return vecs_arr

    def tilt_vector_array_2_dict(self, array, reduced_dim=True):
        axes = self._tilt_corr_axes if reduced_dim else self._scan_axes.keys()

        return {ax: array[idx] for idx, ax in enumerate(axes)}

    def start_scan(self, scan_axes, caller_id=None):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigScanStateChanged.emit(True, self.scan_data, self.back_scan_data, self._curr_caller_id)
                return

            self.log.debug('Starting scan.')
            self._curr_caller_id = self.module_uuid if caller_id is None else caller_id

            self.module_state.lock()
            settings = self.create_scan_settings(tuple(scan_axes))
            back_settings = self.create_back_scan_settings(tuple(scan_axes))
            self.log.debug('Attempting to configure scanner...')
            try:
                self._scanner().configure_scan(settings)
                if self._use_back_scan_settings and BackScanCapability.FULLY_CONFIGURABLE & self.back_scan_capability:
                    self._scanner().configure_back_scan(back_settings)
            except Exception as e:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, None, None, self._curr_caller_id)
                self.log.error('Could not set scan settings on scanning probe hardware.', exc_info=e)
                return

            # Calculate poll time to check for scan completion. Use line scan time estimate.
            line_points = self._scan_resolution[scan_axes[0]] if len(scan_axes) > 1 else 1
            self.__scan_poll_interval = max(self._min_poll_interval, line_points / self._scan_frequency[scan_axes[0]])
            t_poll_ms = max(1, int(round(self.__scan_poll_interval * 1000)))

            self.log.debug(f'Successfully configured scanner and logic scan poll timer: {t_poll_ms} ms')
            self.__scan_poll_timer.setInterval(t_poll_ms)

            try:
                self._scanner().start_scan()
            except Exception as e:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, None, None, self._curr_caller_id)
                self.log.error("Couldn't start scan.", exc_info=e)

            self.sigScanStateChanged.emit(True, self.scan_data, self.back_scan_data, self._curr_caller_id)

        self.__start_timer()


    def stop_scan(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.sigScanStateChanged.emit(False, self.scan_data, self.back_scan_data, self._curr_caller_id)
                return

            self.__stop_timer()

            try:
                if self._scanner().module_state() != 'idle':
                    self._scanner().stop_scan()
            finally:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, self.scan_data, self.back_scan_data, self._curr_caller_id)
                if self.save_to_history:
                    self.sigNewScanDataForHistory.emit(self.scan_data, self.back_scan_data)

    def __scan_poll_loop(self):
        with self._thread_lock:
            try:
                if self.module_state() == 'idle':
                    return

                if self._scanner().module_state() == 'idle':
                    self.stop_scan()
                    return
                # TODO Added the following line as a quick test; Maybe look at it with more caution if correct
                self.sigScanStateChanged.emit(True, self.scan_data, self.back_scan_data, self._curr_caller_id)

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
        self._back_scan_resolution = {}
        self._back_scan_frequency = {}

    def set_full_scan_ranges(self):
        for name, axis in self.scanner_constraints.axes.items():
            self.set_scan_range(name, axis.position.bounds)
        return self.scan_ranges

    def __start_timer(self):
        """
        Offload __scan_poll_timer.start() from the caller to the module's thread.
        ATTENTION: Do not call this from within thread lock protected code to avoid deadlock (PR #178).
        :return:
        """
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__scan_poll_timer, 'start', QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__scan_poll_timer.start()

    def __stop_timer(self):
        """
        Offload __scan_poll_timer.stop() from the caller to the module's thread.
        ATTENTION: Do not call this from within thread lock protected code to avoid deadlock (PR #178).
        :return:
        """
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__scan_poll_timer, 'stop', QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__scan_poll_timer.stop()

    def __transform_func(self, coord, inverse=False):
        """
        Takes a coordinate as dict (with axes keys) and applies the tilt correction transformation.
        To this end, reduce dimensionality to 3d on the axes configured for the tilt transformation.
        :param coord: dict of the coordinate. Keys are configured scanner axes.
        :param inverse:
        :return:
        """

        coord_reduced = {key: val for key, val in list(coord.items())[:3] if key in self._tilt_corr_axes}
        coord_reduced = OrderedDict(sorted(coord_reduced.items()))

        # convert from coordinate dict to plain vector
        transform = self._tilt_corr_transform.__call__
        coord_vec = self.tilt_vector_dict_2_array(coord_reduced, reduced_dim=True).T
        coord_vec_transf = transform(coord_vec, invert=inverse).T
        # make dict again after vector rotation
        coord_transf = cp.copy(coord)
        [coord_transf.update({ax: coord_vec_transf[idx]}) for (idx, ax) in enumerate(self._tilt_corr_axes)]

        return coord_transf
