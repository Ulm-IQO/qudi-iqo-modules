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

from PySide2 import QtCore
import copy as cp
import numpy as np
from collections import OrderedDict

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
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
        connect:
            scanner: scanner_dummy

    """

    # declare connectors
    _scanner = Connector(name='scanner', interface='ScanningProbeInterface')

    # status vars
    _scan_ranges = StatusVar(name='scan_ranges', default=None)
    _scan_resolution = StatusVar(name='scan_resolution', default=None)
    _scan_frequency = StatusVar(name='scan_frequency', default=None)
    _tilt_corr_settings = StatusVar(name='tilt_corr_settings', default={})

    # config options
    _min_poll_interval = ConfigOption(name='min_poll_interval', default=None)

    # signals
    sigScanStateChanged = QtCore.Signal(bool, object, object)
    sigScannerTargetChanged = QtCore.Signal(dict, object)
    sigScanSettingsChanged = QtCore.Signal(dict)
    sigTiltCorrSettingsChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        # others
        self.__scan_poll_timer = None
        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid
        self._tilt_corr_transform = None
        self._tilt_corr_axes = []

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

        constr = self.scanner_constraints
        self._scan_saved_to_hist = True

        self.log.debug(f"Scanner settings at startup, type {type(self._scan_ranges)} {self._scan_ranges, self._scan_resolution}")
        # scanner settings loaded from StatusVar or defaulted
        new_settings = self.check_sanity_scan_settings(self.scan_settings)
        if new_settings != self.scan_settings:
            self._scan_ranges = new_settings['range']
            self._scan_resolution = new_settings['resolution']
            self._scan_frequency = new_settings['frequency']

        if not self._min_poll_interval:
            # defaults to maximum scan frequency of scanner
            self._min_poll_interval = 1/np.max([constr.axes[ax].frequency_range for ax in constr.axes])

        """
        if not isinstance(self._scan_ranges, dict):
            self._scan_ranges = {ax.name: ax.value_range for ax in constr.axes.values()}
        if not isinstance(self._scan_resolution, dict):
            self._scan_resolution = {ax.name: max(ax.min_resolution, min(128, ax.max_resolution))  # TODO Hardcoded 128?
                                     for ax in constr.axes.values()}
        if not isinstance(self._scan_frequency, dict):
            self._scan_frequency = {ax.name: ax.max_frequency for ax in constr.axes.values()}
        """
        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid

        self.__scan_poll_timer = QtCore.QTimer()
        self.__scan_poll_timer.setSingleShot(True)
        self.__scan_poll_timer.timeout.connect(self.__scan_poll_loop, QtCore.Qt.QueuedConnection)

        self._scan_axes = OrderedDict(sorted(self._scanner().get_constraints().axes.items()))

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self.__scan_poll_timer.stop()
        self.__scan_poll_timer.timeout.disconnect()
        if self.module_state() != 'idle':
            self._scanner().stop_scan()
        return

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
    def scanner_constraints(self):
        return self._scanner().get_constraints()

    @property
    def scan_ranges(self):
        with self._thread_lock:
            return cp.copy(self._scan_ranges)

    @property
    def scan_resolution(self):
        with self._thread_lock:
            return cp.copy(self._scan_resolution)

    @property
    def scan_frequency(self):
        with self._thread_lock:
            return cp.copy(self._scan_frequency)

    @property
    def scan_saved_to_history(self):
        with self._thread_lock:
            return self._scan_saved_to_hist

    @property
    def scan_settings(self):
        with self._thread_lock:
            return {'range': self.scan_ranges,
                    'resolution': self.scan_resolution,
                    'frequency': self.scan_frequency,
                    'save_to_history': cp.copy(self._scan_saved_to_hist)}

    def set_scan_settings(self, settings):
        with self._thread_lock:
            if 'range' in settings:
                self.set_scan_range(settings['range'])
            if 'resolution' in settings:
                self.set_scan_resolution(settings['resolution'])
            if 'frequency' in settings:
                self.set_scan_frequency(settings['frequency'])
            if 'save_to_history' in settings:
                self._scan_saved_to_hist = settings['save_to_history']

    def check_sanity_scan_settings(self, settings=None):
        if not isinstance(settings, dict):
            settings = self.scan_settings

        settings = cp.deepcopy(settings)
        constr = self.scanner_constraints

        def check_valid(settings, key):
            is_valid = True  # non present key -> valid
            if key in settings:
                if not isinstance(settings[key], dict):
                    is_valid = False
                else:
                    axes = settings[key].keys()
                    if axes != constr.axes.keys():
                        is_valid = False

            return is_valid

        for key, val in settings.items():
            if not check_valid(settings, key):
                if key == 'range':
                    settings['range'] = {ax.name: ax.value_range for ax in constr.axes.values()}
                if key == 'resolution':
                    settings['resolution'] = {ax.name: max(ax.min_resolution, min(128, ax.max_resolution))  # TODO Hardcoded 128?
                                              for ax in constr.axes.values()}
                if key == 'frequency':
                    settings['frequency'] = {ax.name: ax.max_frequency for ax in constr.axes.values()}

        return settings

    def set_scan_range(self, ranges):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan ranges.')
                new_ranges = self.scan_ranges
                self.sigScanSettingsChanged.emit({'range': new_ranges})
                return new_ranges

            constr = self.scanner_constraints
            for ax, ax_range in ranges.items():
                if ax not in constr.axes:
                    self.log.error('Unknown scanner axis "{0}" encountered.'.format(ax))
                    new_ranges = self.scan_ranges
                    self.sigScanSettingsChanged.emit({'range': new_ranges})
                    return new_ranges

                self._scan_ranges[ax] = (constr.axes[ax].clip_value(float(min(ax_range))),
                                         constr.axes[ax].clip_value(float(max(ax_range))))

            new_ranges = {ax: self._scan_ranges[ax] for ax in ranges}
            self.sigScanSettingsChanged.emit({'range': new_ranges})
            return new_ranges

    def set_scan_resolution(self, resolution):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan resolution.')
                new_res = self.scan_resolution
                self.sigScanSettingsChanged.emit({'resolution': new_res})
                return new_res

            constr = self.scanner_constraints
            for ax, ax_res in resolution.items():
                if ax not in constr.axes:
                    self.log.error('Unknown axis "{0}" encountered.'.format(ax))
                    new_res = self.scan_resolution
                    self.sigScanSettingsChanged.emit({'resolution': new_res})
                    return new_res

                self._scan_resolution[ax] = constr.axes[ax].clip_resolution(int(ax_res))

            new_resolution = {ax: self._scan_resolution[ax] for ax in resolution}
            self.sigScanSettingsChanged.emit({'resolution': new_resolution})
            return new_resolution

    def set_scan_frequency(self, frequency):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan frequency.')
                new_freq = self.scan_frequency
                self.sigScanSettingsChanged.emit({'frequency': new_freq})
                return new_freq

            constr = self.scanner_constraints
            for ax, ax_freq in frequency.items():
                if ax not in constr.axes:
                    self.log.error('Unknown axis "{0}" encountered.'.format(ax))
                    new_freq = self.scan_frequency
                    self.sigScanSettingsChanged.emit({'frequency': new_freq})
                    return new_freq

                self._scan_frequency[ax] = constr.axes[ax].clip_frequency(float(ax_freq))

            new_freq = {ax: self._scan_frequency[ax] for ax in frequency}
            self.sigScanSettingsChanged.emit({'frequency': new_freq})
            return new_freq

    def set_target_position(self, pos_dict, caller_id=None, move_blocking=False):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.error('Unable to change scanner target position while a scan is running.')
                new_pos = self._scanner().get_target()
                self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                return new_pos

            #self.log.debug(f"Requested Set pos to= {pos_dict}")
            ax_constr = self.scanner_constraints.axes
            pos_dict = self._scanner()._expand_coordinate(cp.copy(pos_dict))
            #self.log.debug(f"Expand to= {pos_dict}")

            pos_dict = self._scanner().coordinate_transform(pos_dict)

            for ax, pos in pos_dict.items():
                if ax not in ax_constr:
                    self.log.error('Unknown scanner axis: "{0}"'.format(ax))
                    new_pos = self._scanner().get_target()
                    self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                    return new_pos

                pos_dict[ax] = ax_constr[ax].clip_value(pos)
                if pos != pos_dict[ax]:
                    self.log.warning(f'Scanner position target value {pos:.3e} out of bounds for axis "{ax}". '
                                     f'Clipping value to {pos_dict[ax]:.3e}.')


            # move_absolute expects untransformed coordinatess, so invert clipped pos
            pos_dict = self._scanner().coordinate_transform(pos_dict, inverse=True)
            #self.log.debug(f"In front of hw.move_abs {pos_dict}")
            new_pos = self._scanner().move_absolute(pos_dict, blocking=move_blocking)
            #self.log.debug(f"Set pos to= {pos_dict} => new pos {new_pos}. Bare {self._scanner()._get_position_bare()}")
            if any(pos != new_pos[ax] for ax, pos in pos_dict.items()):
                caller_id = None
            #self.log.debug(f"Logic set target with id {caller_id} to new: {new_pos}")
            self.sigScannerTargetChanged.emit(new_pos,
                self.module_uuid if caller_id is None else caller_id)
            return new_pos

    def toggle_scan(self, start, scan_axes, caller_id=None):
        with self._thread_lock:
            if start:
                return self.start_scan(scan_axes, caller_id)
            return self.stop_scan()

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
            red_support_vecs = red_vecs[:-1,:]
            shift_vec_arr = red_vecs[-1,:]

        tilt_axes = find_changing_axes(support_vecs_arr)

        if red_support_vecs.shape != (3,3) or shift_vec_arr.shape[0] != 3:
            n_dim = support_vecs_arr.shape[1]
            raise ValueError(f"Can't calculate tilt in >3 dimensions. "
                             f"Given support vectors (dim= {n_dim}) must be constant in exactly {n_dim-3} dims. ")

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
        self._tilt_corr_settings = {'auto_origin': auto_origin,
                                    'vec_1': support_vecs[0],
                                    'vec_2': support_vecs[1],
                                    'vec_3': support_vecs[2],
                                    'vec_shift': shift_vec}

        #self.log.debug(f"Shift vec {shift}, shift back {shift_back}")
        #self.log.debug(f"Matrix: {lin_transform.matrix}")
        #self.log.debug(f"Configured tilt corr: {support_vecs}, {shift_vec}")

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

    def _update_scan_settings(self, scan_axes, settings):
        for ax_index, ax in enumerate(scan_axes):
            # Update scan ranges if needed
            new = tuple(settings['range'][ax_index])
            if self._scan_ranges[ax] != new:
                self._scan_ranges[ax] = new
                self.sigScanSettingsChanged.emit({'range': {ax: self._scan_ranges[ax]}})

            # Update scan resolution if needed
            new = int(settings['resolution'][ax_index])
            if self._scan_resolution[ax] != new:
                self._scan_resolution[ax] = new
                self.sigScanSettingsChanged.emit(
                    {'resolution': {ax: self._scan_resolution[ax]}}
                )

        # Update scan frequency if needed
        new = float(settings['frequency'])
        if self._scan_frequency[scan_axes[0]] != new:
            self._scan_frequency[scan_axes[0]] = new
            self.sigScanSettingsChanged.emit({'frequency': {scan_axes[0]: new}})

    def start_scan(self, scan_axes, caller_id=None):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
                return 0

            scan_axes = tuple(scan_axes)
            self._curr_caller_id = self.module_uuid if caller_id is None else caller_id

            self.module_state.lock()

            settings = {'axes': scan_axes,
                        'range': tuple(self._scan_ranges[ax] for ax in scan_axes),
                        'resolution': tuple(self._scan_resolution[ax] for ax in scan_axes),
                        'frequency': self._scan_frequency[scan_axes[0]]}
            fail, new_settings = self._scanner().configure_scan(settings)
            if fail:
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, None, self._curr_caller_id)
                self.log.error(f"Couldn't configure scan: {settings}")
                return -1

            self._update_scan_settings(scan_axes, new_settings)
            #self.log.debug("Applied new scan settings")

            # Calculate poll time to check for scan completion. Use line scan time estimate.
            line_points = self._scan_resolution[scan_axes[0]] if len(scan_axes) > 1 else 1
            self.__scan_poll_interval = max(self._min_poll_interval,
                                            line_points / self._scan_frequency[scan_axes[0]])
            self.__scan_poll_timer.setInterval(int(round(self.__scan_poll_interval * 1000)))

            if self._scanner().start_scan() < 0:  # TODO Current interface states that bool is returned from start_scan
                self.module_state.unlock()
                self.sigScanStateChanged.emit(False, None, self._curr_caller_id)
                self.log.error("Couldn't start scan.")
                return -1

            self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
            self.__start_timer()
            return 0

    def stop_scan(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)
                return 0

            self.__stop_timer()

            err = self._scanner().stop_scan() if self._scanner().module_state() != 'idle' else 0

            self.module_state.unlock()

            if self.scan_settings['save_to_history']:
                # module_uuid signals data-ready to data logic
                self.sigScanStateChanged.emit(False, self.scan_data, self.module_uuid)
            else:
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)

            return err

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

    def set_full_scan_ranges(self):
        scan_range = {ax: axis.value_range for ax, axis in self.scanner_constraints.axes.items()}
        return self.set_scan_range(scan_range)

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

    def __transform_func(self, coord, inverse=False):
        """
        Takes a coordinate as dict (with axes keys) and applies the tilt correction transformation.
        To this end, reduce dimensionality to 3d on the axes configured for the tilt transformation.
        :param coord: dict of the coordinate. Keys are configured scanner axes.
        :param inverse:
        :return:
        """

        coord_reduced = {key:val for key, val in list(coord.items())[:3] if key in self._tilt_corr_axes}
        coord_reduced = OrderedDict(sorted(coord_reduced.items()))

        # convert from coordinate dict to plain vector
        transform = self._tilt_corr_transform.__call__
        coord_vec = self.tilt_vector_dict_2_array(coord_reduced, reduced_dim=True).T
        coord_vec_transf = transform(coord_vec, invert=inverse).T
        # make dict again after vector rotation
        coord_transf = cp.copy(coord)
        [coord_transf.update({ax: coord_vec_transf[idx]}) for (idx, ax) in enumerate(self._tilt_corr_axes)]

        return coord_transf
