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

from PySide2 import QtCore
import copy as cp
import numpy as np

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar

from qudi.interface.magnet_interface import MagnetFOM, MagnetScanData, MagnetScanSettings
from qudi.interface.magnet_interface import MagnetControlAxis, MagnetConstraints



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

        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        constr = self.magnet_constraints
        self._scan_saved_to_hist = True

        self.log.debug(f"Magnet scan settings at startup, type {type(self._scan_ranges)} {self._scan_ranges, self._scan_resolution}")
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
        #return self.scanner_constraints.channels

    @property
    def magnet_constraints(self):
        return self._magnet().constraints()

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
            if self.module_state() != 'idle':
                self.log.warning('Scan is running. Unable to change scan frequency.')
                new_freq = self.scan_frequency
                self.sigScanSettingsChanged.emit({'frequency': new_freq})
                return new_freq

            self._scan_frequency = frequency

            self.sigScanSettingsChanged.emit({'frequency': self._scan_frequency})
            return self._scan_frequency

    def set_control(self, control, caller_id=None, move_blocking=False):
        self.set_target()
        # todo: set target an execute move, emit signals

    def set_target(self, pos_dict, caller_id=None, move_blocking=False):
        # todo: we should be able to set a target thats executed only later (eg. after hitting a button)
        with self._thread_lock:
            self.log.debug(f"Set target: {pos_dict}")

            if self.module_state() != 'idle':
                self.log.error('Unable to change scanner target position while a scan is running.')
                new_pos = self._magnet.get_target()
                self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                return new_pos

            ax_constr = self.magnet_constraints.axes
            new_pos = pos_dict.copy()
            for ax, pos in pos_dict.items():
                if ax not in ax_constr:
                    self.log.error('Unknown magnet axis: "{0}"'.format(ax))
                    new_pos = self._magnet().get_control()
                    self.sigScannerTargetChanged.emit(new_pos, self.module_uuid)
                    return new_pos

                new_pos[ax] = ax_constr[ax].control_value.clip(pos)
                if pos != new_pos[ax]:
                    self.log.warning('Scanner position target value out of bounds for axis "{0}". '
                                     'Clipping value to {1:.3e}.'.format(ax, new_pos[ax]))

            new_pos = self._magnet().set_control(new_pos, blocking=move_blocking)
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
            self._scan_frequency = new
            self.sigScanSettingsChanged.emit({'frequency': self._scan_frequency})

    def configure_figure_of_merit(self, func_scalar, func_full, mes_time=1,
                                  name="", unit=""):

        fom = MagnetFOM(name, func_scalar, mes_time, unit)
        self._fom = fom

    def _config_debug_fom(self, mes_time=1):
        func_scalar = lambda : np.random.random()
        def func_full():
            import time
            time.sleep(mes_time)
            return np.random.rand(20)

        dummy_fom = MagnetFOM('dummy', func_scalar, mes_time)
        self._fom = dummy_fom


    def start_scan(self, scan_axes, caller_id=None):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
                self.log.debug("Aborted scan start. Already running.")
                return 0


            scan_axes = tuple(scan_axes)
            self._curr_caller_id = self.module_uuid if caller_id is None else caller_id

            self.module_state.lock()

            # todo: settings needed?
            settings = MagnetScanSettings(
                axes=scan_axes,
                range=tuple(self._scan_ranges[ax] for ax in scan_axes),
                resolution=tuple(self._scan_resolution[ax] for ax in scan_axes),
                frequency=self._scan_frequency,
            )

            self.magnet_constraints.check_settings(settings)
            self.log.debug('Scan settings fulfill constraints.')

            self._scan_settings = settings
            self._fom.measurement_time = 1/self.scan_frequency

            self._scan_data = MagnetScanData.from_constraints(
                settings=settings,
                constraints=self.magnet_constraints
            )
            self._scan_data.new_scan()
            self.log.debug(f'New ScanData created.')


            self._scan_path = self._init_scan_path(self._scan_data)
            self._scan_idx = 0


            #self._update_scan_settings(scan_axes, settings)  # check whether can be dropped, most likely

            #self.log.debug("Applied new scan settings")

            # Calculate poll time to check for scan completion. Use line scan time estimate.
            #self.__scan_poll_interval = max(self._min_poll_interval,
            #                                line_points / self._scan_frequency[scan_axes[0]])
            #self.__scan_poll_timer.setInterval(int(round(self.__scan_poll_interval * 1000)))


            self.sigScanStateChanged.emit(True, self.scan_data, self._curr_caller_id)
            self.__start_timer()

            return 0

    def stop_scan(self):
        with self._thread_lock:
            if self.module_state() == 'idle':
                self.sigScanStateChanged.emit(False, self.scan_data, self._curr_caller_id)
                return 0

            #self.__stop_timer()   # todo understand why locks
            self.module_state.unlock()
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

    def _init_scan_path(self, scan_data):
        if scan_data.settings.scan_dimension == 1:

            axis = scan_data.settings.axes[0]
            horizontal_resolution = scan_data.settings.resolution[0]

            horizontal_line = np.linspace(scan_data.settings.range[0][0], scan_data.settings.range[0][1],
                                     horizontal_resolution)

            coord_dict = {axis: horizontal_line}

        elif scan_data.settings.scan_dimension == 2:
            # todo: check how to include backward scan (should yield meander 2d line)
            # need to make meander line default
            horizontal_resolution = scan_data.settings.resolution[0]
            vertical_resolution = scan_data.settings.resolution[1]

            # horizontal scan array / "fast axis"
            horizontal_axis = scan_data.settings.axes[0]

            horizontal_line = np.linspace(scan_data.settings.range[0][0], scan_data.settings.range[0][1],
                                     horizontal_resolution)

            # need as much lines as we have in the vertical directions
            horizontal_scan_array = np.tile(horizontal_line, vertical_resolution)

            # vertical scan array / "slow axis"
            vertical_axis = scan_data.settings.axes[1]
            vertical = np.linspace(scan_data.settings.range[1][0], scan_data.settings.range[1][1],
                                   vertical_resolution)

            backwards_line_resolution = horizontal_resolution
            # during horizontal line, the vertical line keeps its value
            vertical_lines = np.repeat(vertical.reshape(vertical_resolution, 1), horizontal_resolution, axis=1)
            # during backscan of horizontal, the vertical axis increases its value by "one index"
            vertical_return_lines = np.linspace(vertical[:-1], vertical[1:], backwards_line_resolution).T
            ## need to extend the vertical lines at the end, as we reach it earlier then for the horizontal axes
            vertical_return_lines = np.concatenate((vertical_return_lines,
                                                    np.ones((1, backwards_line_resolution)) * vertical[-1]
                                                    ))

            vertical_scan_array = np.concatenate((vertical_lines, vertical_return_lines), axis=1).ravel()


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

                if next(iter(self._magnet().get_status())) != 0:
                    self.log.warning("Magnet hardware not ready. Aborting scan.")
                    self.stop_scan()
                    return

                if self._scan_idx >= len(list(self._scan_path.values())[0]):
                    self.stop_scan()
                    return

                target_control = {ax: self._scan_path[ax][self._scan_idx] for ax in self._scan_path.keys()}
                self.log.debug(f"Next value in scan path: [{self._scan_idx}] {target_control}")
                self._magnet().set_control(target_control, blocking=True)

                # todo: insert into scan data. (always?)
                actual_control = self._magnet().get_control()

                fom_value = self._fom.func()
                #fom_full_result = self._fom.func_full()

                # todo: check that this is working for a meander line scan
                scan_data_flat = self._scan_data.data['FOM'].flatten()
                scan_data_flat[self._scan_idx] = fom_value
                resolution = self.scan_data.settings.resolution

                self._scan_data.data['FOM'][:] = scan_data_flat.reshape(resolution)
                self.log.debug(f"New data: {fom_value}. Scan: {self._scan_data.data['FOM']}")
                # todo: insert into ScanData object
                """
                Get_next_pos(pathway)
                Set_control_value(blocking=True)
                Get_control_value()
                Perform_fom()
                Save/Emit(control_target, control_actual, fom, meta_data)

                """

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
        scan_range = {ax: axis.value_range for ax, axis in self.magnet_constraints.axes.items()}
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
