# -*- coding: utf-8 -*-
"""
This module is responsible for controlling any kind of magnet control and 1D and 2D
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

import copy as cp

import numpy as np
from PySide2 import QtCore

from qudi.core.connector import Connector
from qudi.core.module import LogicBase
from qudi.core.statusvariable import StatusVar
from qudi.interface.magnet_interface import MagnetFOM, MagnetScanData, MagnetScanSettings
from qudi.util.mutex import RecursiveMutex


class MagnetLogic(LogicBase):
    """

    Example config for copy-paste:

    magnet_logic:
        module.Class: 'magnet_logic.MagnetLogic'
        options:
            max_history_length: 20
            max_scan_update_interval: 2
            position_update_interval: 1
        connect:
            magnet: magnet_dummy

    """

    # declare connectors
    _magnet = Connector(name='magnet', interface='MagnetInterface')

    # status vars
    _scan_ranges = StatusVar(name='scan_ranges', default=None)
    _scan_resolution = StatusVar(name='scan_resolution', default=None)
    _scan_frequency = StatusVar(name='scan_frequency', default=None)

    # signals
    sigScanStateChanged = QtCore.Signal(bool, object, object)
    sigScannerTargetChanged = QtCore.Signal(dict, object)
    sigScanSettingsChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        # others
        self.__scan_poll_timer = None
        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid

        self._scan_data = None
        self._scan_data_flat = None

        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        constr = self.magnet_constraints
        self._scan_saved_to_hist = True

        self.log.debug(
            f"Magnet scan settings at startup, type {type(self._scan_ranges)} {self._scan_ranges, self._scan_resolution}")
        # scanner settings loaded from StatusVar or defaulted
        new_settings = self.check_sanity_scan_settings(self.scan_settings)
        if new_settings != self.scan_settings:
            self._scan_ranges = new_settings['range']
            self._scan_resolution = new_settings['resolution']
            self._scan_frequency = new_settings['frequency']

        self._scan_axes = []
        self._target = self.magnet_control

        self.configure_figure_of_merit(lambda: None, lambda: None, mes_time=0,
                                       name="/na")
        self.log.warning("Configuring debug FOM. For testing only")
        self._config_debug_fom()

        self.__scan_poll_interval = 0
        self.__scan_stop_requested = True
        self._curr_caller_id = self.module_uuid

        self.__scan_poll_timer = QtCore.QTimer()
        self.__scan_poll_timer.setSingleShot(True)
        self.__scan_poll_timer.timeout.connect(self.__scan_loop, QtCore.Qt.QueuedConnection)
        return

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self.__scan_poll_timer.stop()
        self.__scan_poll_timer.timeout.disconnect()
        if self.module_state() != 'idle':
            self.stop_scan()
        return

    @property
    def scan_data(self):
        with self._thread_lock:
            return self._scan_data

    @property
    def magnet_control(self):
        with self._thread_lock:
            return self._magnet().get_control()

    @property
    def magnet_target(self):
        with self._thread_lock:
            return self._target

    @property
    def magnet_control_axes(self):
        return self.magnet_constraints.axes

    @property
    def figure_of_merit(self):
        pass
        # return self.scanner_constraints.channels

    @property
    def magnet_constraints(self):
        return self._magnet().constraints

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
        # todo: no hw scanner. derive scan frequency from FOM mes time?
        with self._thread_lock:
            return 0.1  # todo: needed or handled in fom?
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
        constr = self.magnet_constraints

        def check_valid(settings, key):
            is_valid = True  # non present key -> valid

            if key == 'frequency':
                if type(settings[key]) == 'float':
                    return True

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
                    settings['range'] = {ax.name: ax.control_value.bounds for ax in constr.axes.values()}
                if key == 'resolution':
                    # TODO Hardcoded dfeault values, 128?
                    settings['resolution'] = {ax.name: max(ax.resolution.minimum, min(128, ax.resolution.maximum))
                                              for ax in constr.axes.values()}
                if key == 'frequency':
                    settings['frequency'] = 0.1

        return settings

    def set_scan_range(self, ranges):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan ranges.')
                new_ranges = self.scan_ranges
                self.sigScanSettingsChanged.emit({'range': new_ranges})
                return new_ranges

            constr = self.magnet_constraints
            for ax, ax_range in ranges.items():
                if ax not in constr.axes:
                    self.log.error('Unknown scanner axis "{0}" encountered.'.format(ax))
                    new_ranges = self.scan_ranges
                    self.sigScanSettingsChanged.emit({'range': new_ranges})
                    return new_ranges

                self._scan_ranges[ax] = (constr.axes[ax].control_value.clip(float(min(ax_range))),
                                         constr.axes[ax].control_value.clip(float(max(ax_range))))

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

            constr = self.magnet_constraints
            for ax, ax_res in resolution.items():
                if ax not in constr.axes:
                    self.log.error('Unknown axis "{0}" encountered.'.format(ax))
                    new_res = self.scan_resolution
                    self.sigScanSettingsChanged.emit({'resolution': new_res})
                    return new_res

                self._scan_resolution[ax] = constr.axes[ax].resolution.clip(int(ax_res))

            new_resolution = {ax: self._scan_resolution[ax] for ax in resolution}
            self.sigScanSettingsChanged.emit({'resolution': new_resolution})
            return new_resolution

    def set_scan_frequency(self, frequency):
        with self._thread_lock:
            self._scan_frequency = 0.1  # todo handle frequency

            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan frequency.')
                new_freq = self.scan_frequency

                # todo: this emit seems broken
                # self.sigScanSettingsChanged.emit({'frequency': new_freq})

                return new_freq

            self._scan_frequency = 0.1  # todo handle frequency
            # self._scan_frequency = frequency

            self.sigScanSettingsChanged.emit({'frequency': self._scan_frequency})
            return self._scan_frequency

    def check_dicts_close(self, dict1, dict2, tolerance=None):
        import math
        # todo: check what happens if target has not all axes

        if tolerance is None:
            tolerance = {key: 0 for key in dict1.keys()}

        for key, value in dict1.items():
            if key not in dict2:
                continue
            if math.fabs(value - dict2[key]) > tolerance[key]:
                return False
        return True

    @property
    def target_reached(self):
        # todo: check control within accuracy at target

        tol = self._magnet().constraints.control_accuracy
        magnet_control = self._magnet().get_control()
        target = self._target

        return self.check_dicts_close(magnet_control, target, tol)

    def set_control(self, caller_id=None, move_blocking=False):

        # todo: set target an execute move, emit signals
        # todo: we should be able to set a target thats executed only later (eg. after hitting a button)
        with self._thread_lock:
            self.log.debug(f"Setting new control: {self._target}")

            try:
                new_pos = self._sanitize_target(self._target)
            except Exception as e:
                self.log.error(str(e))
                new_pos = self._magnet().get_control()
                self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                return new_pos

            new_pos = self._magnet().set_control(new_pos, blocking=move_blocking)
            if any(pos != new_pos[ax] for ax, pos in self._target.items()):
                caller_id = None

            # self.log.debug(f"Logic set target with id {caller_id} to new: {new_pos}")
            self.sigScannerTargetChanged.emit(new_pos,
                                              self.module_uuid if caller_id is None else caller_id)

            return new_pos

    def set_target(self, pos_dict, caller_id=None, move_blocking=False):
        # todo: we should be able to set a target thats executed only later (eg. after hitting a button)
        with self._thread_lock:

            try:
                new_pos = self._sanitize_target(pos_dict)
            except Exception as e:
                self.log.error(str(e))
                new_pos = self._magnet().get_control()
                self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                return new_pos

            self._target = new_pos
            self.log.debug(f"Storing new target {self._target}. Not executed yet!")
            # new_pos = self._magnet().set_control(new_pos, blocking=move_blocking)
            # if any(pos != new_pos[ax] for ax, pos in pos_dict.items()):
            #    caller_id = None
            # self.log.debug(f"Logic set target with id {caller_id} to new: {new_pos}")
            self.sigScannerTargetChanged.emit(new_pos,
                                              self.module_uuid if caller_id is None else caller_id)

            return new_pos

    def _sanitize_target(self, pos_dict):

        new_pos = pos_dict.copy()

        if self.module_state() != 'idle':
            raise RuntimeError('Unable to change scanner target position while a scan is running.')

        ax_constr = self.magnet_constraints.axes
        for ax, pos in pos_dict.items():
            if ax not in ax_constr:
                raise ValueError('Unknown magnet axis: "{0}"'.format(ax))

            new_pos[ax] = ax_constr[ax].control_value.clip(pos)
            if pos != new_pos[ax]:
                self.log.warning('Magnet position target value out of bounds for axis "{0}". '
                                 'Clipping value to {1:.3e}.'.format(ax, new_pos[ax]))

        return new_pos

    def toggle_scan(self, start, scan_axes, caller_id=None):
        with self._thread_lock:
            if start:
                return self.start_scan(scan_axes, caller_id)
            return self.stop_scan()

    def _update_scan_settings(self, scan_axes, settings):
        # todo: probably can be dropped
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
        if self._scan_frequency != new:
            self._scan_frequency = 0.1  # new todo: new
            self.sigScanSettingsChanged.emit({'frequency': self._scan_frequency})

    def configure_figure_of_merit(self, func_scalar, func_full, mes_time=1,
                                  name="", unit=""):

        fom = MagnetFOM(name, func_scalar, mes_time, unit)
        self._fom = fom

    def _config_debug_fom_counter(self, mes_time=0.1, handle_counter=None):

        if handle_counter is None:
            raise ValueError

        counter_logic = handle_counter

        def get_counts():
            import time
            # todo: need a public way how to check this
            if counter_logic.module_state() != 'locked':
                counter_logic.start_reading()
            sample_rate = counter_logic.sampling_rate
            n_samples = int(mes_time * sample_rate)

            time.sleep(mes_time)

            cts_arr = counter_logic.trace_data[1]
            cts_mean = np.average(next(iter(cts_arr.values()))[-n_samples:])

            return cts_mean

        fom = MagnetFOM('counter', get_counts, mes_time)

        self._fom = fom

    def _config_debug_fom(self, mes_time=0.1):
        func_scalar = lambda: np.random.random()

        def func_full():
            import time
            time.sleep(mes_time)
            return np.random.rand(20)

        dummy_fom = MagnetFOM('dummy', func_scalar, mes_time)
        self._fom = dummy_fom

    def start_scan(self, scan_axes, caller_id=None):

        self.log.debug(f"Scan started on axes {scan_axes}")

        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
                self.log.debug("Aborted scan start. Already running.")
                return 0

            self.log.debug('Locking module..')
            scan_axes = tuple(scan_axes)
            self._curr_caller_id = self.module_uuid if caller_id is None else caller_id

            self.module_state.lock()

            # todo: settings needed?
            self.log.debug('Creating scan settings. ')
            settings = MagnetScanSettings(
                axes=scan_axes,
                range=tuple(self._scan_ranges[ax] for ax in scan_axes),
                resolution=tuple(self._scan_resolution[ax] for ax in scan_axes),
                frequency=self._scan_frequency,
            )

            self.magnet_constraints.check_settings(settings)
            self.log.debug('Scan settings fulfill constraints.')

            self._scan_settings = settings
            self._fom.measurement_time = 1 / self.scan_frequency

            self._scan_data = MagnetScanData.from_constraints(
                settings=settings,
                constraints=self.magnet_constraints)
            self._scan_data.new_scan()
            self._scan_data_flat = np.copy(self._scan_data.data['FOM']).T.flatten()
            self.log.debug(f'New ScanData created.')

            self._scan_path = self._init_scan_path(self._scan_data)
            self._scan_idx = 0

            # self._update_scan_settings(scan_axes, settings)  # check whether can be dropped, most likely
            # self.log.debug("Applied new scan settings")

            self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
            self.__start_timer()

            return 0

    def stop_scan(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)
                return 0

            # self.__stop_timer()   # todo understand why locks
            self.module_state.unlock()
            self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)

            if self.scan_settings['save_to_history']:
                # module_uuid signals data-ready to data logic
                self.sigScanStateChanged.emit(False, self.scan_data, self.module_uuid)
            else:
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)

            # todo: revert to start pos
            """
            err = self._maget.stop_scan() if self._maget.module_state() != 'idle' else 0

            self.module_state.unlock()

            if self.scan_settings['save_to_history']:
                # module_uuid signals data-ready to data logic
                self.sigScanStateChanged.emit(False, self.scan_data, self.module_uuid)
            else:
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)
            """

    def _swap_2nd_rows(self, arr):

        if arr.ndim == 1:
            return arr

        even_rows = np.arange(arr.shape[0]) % 2 == 1
        swapped_arr = arr.copy()

        # Swap elements within even-indexed rows
        swapped_arr[even_rows] = arr[even_rows][:, ::-1]

        return swapped_arr

    def _init_scan_path(self, scan_data):

        if scan_data.settings.scan_dimension == 1:

            axis = scan_data.settings.axes[0]
            horizontal_resolution = scan_data.settings.resolution[0]

            horizontal_line = np.linspace(scan_data.settings.range[0][0], scan_data.settings.range[0][1],
                                          horizontal_resolution)

            coord_dict = {axis: horizontal_line}

        elif scan_data.settings.scan_dimension == 2:
            # todo: do we need a backscan, like in the scanning_probe toolchain?

            horizontal_resolution = scan_data.settings.resolution[0]
            vertical_resolution = scan_data.settings.resolution[1]

            horizontal_axis = scan_data.settings.axes[0]
            vertical_axis = scan_data.settings.axes[1]

            horizontal_line = np.linspace(scan_data.settings.range[0][0], scan_data.settings.range[0][1],
                                          horizontal_resolution)
            vertical_line = np.linspace(scan_data.settings.range[1][0], scan_data.settings.range[1][1],
                                        vertical_resolution)
            self.log.debug(f"Horizontal linspace: {np.min(horizontal_line), np.max(horizontal_line)},"
                           f"vertical linspace: {np.min(vertical_line), np.max(vertical_line)}")

            h, v = np.meshgrid(horizontal_line, vertical_line)

            # make a meander like scan path
            h, v = self._swap_2nd_rows(h), self._swap_2nd_rows(v)

            horizontal_scan_array = h.ravel()
            vertical_scan_array = v.ravel()

            coord_dict = {horizontal_axis: horizontal_scan_array,
                          vertical_axis: vertical_scan_array
                          }

        else:
            raise ValueError(f"Not supported scan dimension: {scan_data.settings.scan_dimension}")

        return coord_dict

    def _set_settings(selt):
        pass
        # todo: set frequency, scan_path, fom

    def __scan_loop(self):
        with self._thread_lock:
            try:

                if self.module_state() == 'idle':
                    return

                if not self._magnet().get_status().is_ready:
                    self.log.warning("Magnet hardware not ready. Aborting scan.")
                    self.stop_scan()
                    return

                if self._scan_idx >= len(list(self._scan_path.values())[0]):
                    self.stop_scan()
                    return

                target_control = {ax: self._scan_path[ax][self._scan_idx] for ax in self._scan_path.keys()}

                self._magnet().set_control(target_control, blocking=True)

                # todo: insert into scan data. (always?)
                actual_control = self._magnet().get_control()
                self.log.debug(f"Next scan pos: [{self._scan_idx}] {target_control}. Actual control: {actual_control}")

                fom_value = self._fom.func()
                # fom_full_result = self._fom.func_full()

                self._scan_data_flat[self._scan_idx] = fom_value
                resolution = self.scan_data.settings.resolution

                # swap rows, assuming a meander like scan path
                transpose_resolution = (resolution[1], resolution[0]) if len(resolution) > 1 else resolution
                self._scan_data.data['FOM'][:] = self._swap_2nd_rows(self._scan_data_flat.reshape(transpose_resolution)).T
                # self.log.debug(f"New data: {fom_value}. Scan: {self._scan_data.data['FOM']}")

                self._scan_idx += 1
                self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
                # Queue next call to this slot
                self.__scan_poll_timer.start()
            except TimeoutError:
                self.log.exception('Timed out while waiting for scan data:')
            except:
                self.log.exception('An exception was raised while polling the scan:')
            return

    def set_full_scan_ranges(self):
        scan_range = {ax: axis.control_value.bounds for ax, axis in self.magnet_constraints.axes.items()}
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
