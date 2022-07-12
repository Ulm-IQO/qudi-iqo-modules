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


import numpy as np
from PySide2 import QtCore
import itertools
import copy as cp

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.fit_models.gaussian import Gaussian2D, Gaussian

from qudi.interface.scanning_probe_interface import ScanData


class ScanningOptimizeLogic(LogicBase):
    """
    ToDo: Write documentation
    """

    # declare connectors
    _scan_logic = Connector(name='scan_logic', interface='ScanningProbeLogic')

    # config options

    # status variables
    _scan_sequence = StatusVar(name='scan_sequence', default=None)
    _data_channel = StatusVar(name='data_channel', default=None)
    _scan_frequency = StatusVar(name='scan_frequency', default=None)
    _scan_range = StatusVar(name='scan_range', default=None)
    _scan_resolution = StatusVar(name='scan_resolution', default=None)

    # signals
    sigOptimizeStateChanged = QtCore.Signal(bool, dict, object)
    sigOptimizeSettingsChanged = QtCore.Signal(dict)

    _sigNextSequenceStep = QtCore.Signal()

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self._thread_lock = RecursiveMutex()

        self._stashed_scan_settings = dict()
        self._sequence_index = 0
        self._optimal_position = dict()
        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        axes = self._scan_logic().scanner_axes
        channels = self._scan_logic().scanner_channels

        self.log.debug(f"Opt settings at startup, type {type(self._scan_range)} {self._scan_range, self._scan_resolution}")

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

        # check nd correct optimizer settings loaded from StatusVar
        new_settings = self.check_sanity_optimizer_settings(self.optimize_settings)
        if new_settings != self.optimize_settings:
            self._scan_range = new_settings['scan_range']
            self._scan_resolution = new_settings['scan_resolution']
            self._scan_frequency = new_settings['scan_frequency']


        self._stashed_scan_settings = dict()
        self._sequence_index = 0
        self._optimal_position = dict()

        self._sigNextSequenceStep.connect(self._next_sequence_step, QtCore.Qt.QueuedConnection)
        self._scan_logic().sigScanStateChanged.connect(
            self._scan_state_changed, QtCore.Qt.QueuedConnection
        )
        return

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self._scan_logic().sigScanStateChanged.disconnect(self._scan_state_changed)
        self._sigNextSequenceStep.disconnect()
        self.stop_optimize()
        return

    @property
    def data_channel(self):
        return self._data_channel

    @property
    def scan_frequency(self):
        return self._scan_frequency.copy() if self._scan_frequency!=None else None

    @property
    def scan_range(self):
        return self._scan_range.copy() if self._scan_range!=None else None

    @property
    def scan_resolution(self):
        return self._scan_resolution.copy() if self._scan_resolution!= None else None

    @property
    def scan_sequence(self):
        # serialization into status variable changes step type <tuple> -> <list>
        return [tuple(el) for el in self._scan_sequence]

    @scan_sequence.setter
    def scan_sequence(self, sequence):
        """
        @param sequence: list of string tuples giving the scan order, eg. [('x','y'),('z')]
        """
        axs_flat = []
        list(axs_flat.extend(item) for item in sequence)
        avail_axes = [ax.name for ax in self._avail_axes]
        if not all(elem in avail_axes for elem in axs_flat):
            raise ValueError(f"Optimizer sequence {sequence} must contain only"
                             f" available axes ({avail_axes})")

        self._scan_sequence = sequence

    @property
    def optimizer_running(self):
        return self.module_state() != 'idle'

    @property
    def optimize_settings(self):
        return {'scan_frequency': self.scan_frequency,
                'data_channel': self._data_channel,
                'scan_range': self.scan_range,
                'scan_resolution': self.scan_resolution,
                'scan_sequence': self.scan_sequence}

    def check_sanity_optimizer_settings(self, settings=None, plot_dimensions=None):
        # shaddows scanning_probe_logic::check_sanity. Unify code somehow?

        if not isinstance(settings, dict):
            settings = self.optimize_settings

        settings = cp.deepcopy(settings)
        hw_axes = self._scan_logic().scanner_axes

        def check_valid(settings, key):
            is_valid = True  # non present key -> valid
            if key in settings:
                if not isinstance(settings[key], dict):
                    is_valid = False
                else:
                    axes = settings[key].keys()
                    if axes != hw_axes.keys():
                        is_valid = False

            return is_valid


        # first check settings that are defined per scanner axis
        for key, val in settings.items():
            if not check_valid(settings, key):
                if key == 'scan_range':
                    settings['scan_range'] = {ax.name: abs(ax.value_range[1] - ax.value_range[0]) / 100 for ax in
                                              hw_axes.values()}
                if key == 'scan_resolution':
                    settings['scan_resolution'] = {ax.name: max(ax.min_resolution, min(16, ax.max_resolution))
                                                   for ax in hw_axes.values()}
                if key == 'scan_frequency':
                    settings['scan_frequency'] = {ax.name: max(ax.min_frequency, min(50, ax.max_frequency)) for ax
                                                  in hw_axes.values()}

        # scan_sequence check, only sensibel if plot dimensions (eg. from confocal gui) are available
        if 'scan_sequence' in settings and plot_dimensions:
            dummy_seq = OptimizerScanSequence(tuple(self._scan_logic().scanner_axes.keys()),
                                              plot_dimensions)

            if len(dummy_seq.available_opt_sequences) == 0:
                raise ValueError(f"Configured optimizer dim= {plot_dimensions}"
                                 f" doesn't yield any sensible scan sequence.")

            if settings['scan_sequence'] not in [seq.sequence for seq in dummy_seq.available_opt_sequences]:
                new_seq = dummy_seq.available_opt_sequences[0].sequence
                settings['scan_sequence'] = new_seq

            if len(settings['scan_sequence']) != len(plot_dimensions):
                self.log.warning(f"Configured optimizer dim= {plot_dimensions}"
                                 f" doesn't fit the available sequences.")

        return settings


    @property
    def optimal_position(self):
        return self._optimal_position.copy()

    def set_optimize_settings(self, settings):
        """
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                settings_update = self.optimize_settings
                self.log.error('Can not change optimize settings when module is locked.')
            else:
                settings_update = dict()
                if 'scan_frequency' in settings:
                    self._scan_frequency.update(settings['scan_frequency'])
                    settings_update['scan_frequency'] = self.scan_frequency
                if 'data_channel' in settings:
                    self._data_channel = settings['data_channel']
                    settings_update['data_channel'] = self._data_channel
                if 'scan_range' in settings:
                    self._scan_range.update(settings['scan_range'])
                    settings_update['scan_range'] = self.scan_range
                if 'scan_resolution' in settings:
                    self._scan_resolution.update(settings['scan_resolution'])
                    settings_update['scan_resolution'] = self.scan_resolution
                if 'scan_sequence' in settings:
                    self.scan_sequence = settings['scan_sequence']
                    settings_update['scan_sequence'] = self.scan_sequence

            self.sigOptimizeSettingsChanged.emit(settings_update)
            return settings_update

    def toggle_optimize(self, start):
        if start:
            return self.start_optimize()
        return self.stop_optimize()

    def start_optimize(self):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.sigOptimizeStateChanged.emit(True, dict(), None)
                return 0

            # ToDo: Sanity checks for settings go here

            self.module_state.lock()
            self.sigOptimizeStateChanged.emit(True, dict(), None)

            # stash old scanner settings
            self._stashed_scan_settings = self._scan_logic().scan_settings

            # Set scan ranges
            curr_pos = self._scan_logic().scanner_target
            optim_ranges = {ax: (pos - self._scan_range[ax] / 2, pos + self._scan_range[ax] / 2) for
                            ax, pos in curr_pos.items()}
            actual_setting = self._scan_logic().set_scan_range(optim_ranges)
            # FIXME: Comparing floats by inequality here
            if any(val != optim_ranges[ax] for ax, val in actual_setting.items()):
                self.log.warning('Some optimize scan ranges have been changed by the scanner.')
                self.module_state.unlock()
                self.set_optimize_settings(
                    {'scan_range': {ax: abs(r[1] - r[0]) for ax, r in actual_setting.items()}}
                )
                self.module_state.lock()

            # Set scan frequency
            actual_setting = self._scan_logic().set_scan_frequency(self._scan_frequency)
            # FIXME: Comparing floats by inequality here
            if any(val != self._scan_frequency[ax] for ax, val in actual_setting.items()):
                self.log.warning('Some optimize scan frequencies have been changed by the scanner.')
                self.module_state.unlock()
                self.set_optimize_settings({'scan_frequency': actual_setting})
                self.module_state.lock()

            # Set scan resolution
            actual_setting = self._scan_logic().set_scan_resolution(self._scan_resolution)
            # FIXME: Comparing floats by inequality here
            if any(val != self._scan_resolution[ax] for ax, val in actual_setting.items()):
                self.log.warning(
                    'Some optimize scan resolutions have been changed by the scanner.')
                self.module_state.unlock()
                self.set_optimize_settings({'scan_resolution': actual_setting})
                self.module_state.lock()

            # optimizer scans are never saved
            self._scan_logic().set_scan_settings({'save_to_history': False})

            self._sequence_index = 0
            self._optimal_position = dict()
            self.sigOptimizeStateChanged.emit(True, self.optimal_position, None)
            self._sigNextSequenceStep.emit()
            return 0

    def _next_sequence_step(self):
        with self._thread_lock:

            if self.module_state() == 'idle':
                return

            if self._scan_logic().toggle_scan(True,
                                              self._scan_sequence[self._sequence_index],
                                              self.module_uuid) < 0:
                self.log.error('Unable to start {0} scan. Optimize aborted.'.format(
                    self._scan_sequence[self._sequence_index])
                )
                self.stop_optimize()
            return

    def _scan_state_changed(self, is_running, data, caller_id):

        with self._thread_lock:
            if is_running or self.module_state() == 'idle' or caller_id != self.module_uuid:
                return
            elif data is not None:
                if data.scan_dimension == 1:
                    x = np.linspace(*data.scan_range[0], data.scan_resolution[0])
                    opt_pos, fit_data, fit_res = self._get_pos_from_1d_gauss_fit(
                        x,
                        data.data[self._data_channel]
                    )
                else:
                    x = np.linspace(*data.scan_range[0], data.scan_resolution[0])
                    y = np.linspace(*data.scan_range[1], data.scan_resolution[1])
                    xy = np.meshgrid(x, y, indexing='ij')
                    opt_pos, fit_data, fit_res = self._get_pos_from_2d_gauss_fit(
                        xy,
                        data.data[self._data_channel].ravel()
                    )


                position_update = {ax: opt_pos[ii] for ii, ax in enumerate(data.scan_axes)}
                if fit_data is not None:
                    new_pos = self._scan_logic().set_target_position(position_update)
                    for ax in tuple(position_update):
                        position_update[ax] = new_pos[ax]

                    fit_data = {'fit_data':fit_data, 'full_fit_res':fit_res}

                self._optimal_position.update(position_update)
                self.sigOptimizeStateChanged.emit(True, position_update, fit_data)

                # Abort optimize if fit failed
                if fit_data is None:
                    self.stop_optimize()
                    return

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
                return 0

            if self._scan_logic().module_state() != 'idle':
                # optimizer scans are never saved in scanning history
                err = self._scan_logic().stop_scan()
            else:
                err = 0
            self._scan_logic().set_scan_settings(self._stashed_scan_settings)
            self._stashed_scan_settings = dict()
            self.module_state.unlock()
            self.sigOptimizeStateChanged.emit(False, dict(), None)
            return err

    def _get_pos_from_2d_gauss_fit(self, xy, data):
        model = Gaussian2D()

        try:
            fit_result = model.fit(data, xy=xy, **model.estimate_peak(data, xy))
        except:
            x_min, x_max = xy[0].min(), xy[0].max()
            y_min, y_max = xy[1].min(), xy[1].max()
            x_middle = (x_max - x_min) / 2 + x_min
            y_middle = (y_max - y_min) / 2 + y_min
            self.log.exception('2D Gaussian fit unsuccessful. Aborting optimization sequence.')
            return (x_middle, y_middle), None

        return (fit_result.best_values['center_x'],
                fit_result.best_values['center_y']), fit_result.best_fit.reshape(xy[0].shape), fit_result

    def _get_pos_from_1d_gauss_fit(self, x, data):
        model = Gaussian()

        try:
            fit_result = model.fit(data, x=x, **model.estimate_peak(data, x))
        except:
            x_min, x_max = x.min(), x.max()
            middle = (x_max - x_min) / 2 + x_min
            self.log.exception('1D Gaussian fit unsuccessful. Aborting optimization sequence.')
            return middle, None

        return (fit_result.best_values['center'],), fit_result.best_fit, fit_result


class OptimizerScanSequence():
    def __init__(self, axes, dimensions=[2,1], sequence=None):
        self._avail_axes = axes
        self._optimizer_dim = dimensions
        self._sequence = None
        if sequence in self._available_opt_seqs_raw():
            self.sequence = sequence

    def __eq__(self, other):
        if isinstance(other, OptimizerScanSequence):
            return self._sequence == other._sequence
        return False

    def __str__(self):
        out_str = ""
        if self.sequence:
            for step in self._sequence:
                if len(step) == 1:
                    out_str += f"{step[0]}"
                elif len(step) == 2:
                    out_str += f"{step[0]}{step[1]}"
                else:
                    raise ValueError
                out_str += ", "

            out_str = out_str.rstrip(', ')

        return out_str

    def __len__(self):
        if not self.sequence:
            return 0
        return len(self.sequence)

    @property
    def sequence(self):
        """
        @return: list of tuples
        """

        return self._sequence

    @sequence.setter
    def sequence(self, sequence):
        """
        @param sequence: list of tuples, eg. [('x','y'), ('z')]
        """
        if not sequence in self._available_opt_seqs_raw():
            raise ValueError(f"Given {sequence} sequence incompatible with axes= {self._avail_axes}, dims= {self._optimizer_dim}")

        self._sequence = sequence

    @property
    def available_opt_sequences(self):
        """
        Based on the given plot dimensions and axes configuration, give all possible permutations of scan sequences.
        """

        return [OptimizerScanSequence(self._avail_axes, self._optimizer_dim, seq) for seq in self._available_opt_seqs_raw()]


    def _available_opt_seqs_raw(self, remove_1d_in_2d=True):
        """
        @oaram remove_1d_in_2d: remove sequences where 1d steps are repeated in 2d steps, eg. [('x','y'), ('x')]
        """
        def get_n_in(comb_list, seq_step):
            if type(seq_step) != tuple:
                raise ValueError

            n_in = 0
            for old_step in comb_list:
                if type(old_step) != tuple:
                    raise ValueError
                if old_step == seq_step:
                    n_in += 1
                    continue
                if len(old_step) == 2 and len(seq_step) == 2:
                    if old_step[0] == seq_step[1] and old_step[1] == seq_step[0]:
                        n_in += 1
                        continue
            return n_in

        def add_comb(old_comb, new_seqs):
            out_comb = []

            for old_list in old_comb:
                for seq in new_seqs:
                    out_comb.append(combine(old_list, seq))

            if not old_comb:
                return new_seqs
            if not out_comb:
                return old_comb

            # clean doubles within combination
            out_clean = []
            for comb in out_comb:
                out_clean.append([el for el in comb if get_n_in(comb, el) == 1])
            out_comb = [el for el in out_clean if len(el) == len(out_comb[0])]

            return out_comb

        def combine(in1, in2):

            in1 = cp.deepcopy(in1)
            in2 = cp.deepcopy(in2)

            if type(in1) == str:
                in1 = tuple(in1)
            if type(in2) == str:
                in2 = tuple(in2)

            if type(in1) == list and type(in2) == list:
                in1.extend(in2)
                return in1
            elif type(in1) != list and type(in2) == list:
                in2.insert(0, in1)
                return in2
            elif type(in1) == list and type(in2) != list:
                in1.append(in2)
                return in1
            else:
                return [in1, in2]

        def remove_duplicates(comb_list):
            out_seqs = []
            for seq in comb_list:
                if seq not in out_seqs:
                    out_seqs.append(seq)

            return out_seqs

        def remove_1d_in_2d_axes_dupl(comb_list):
            out_seqs = []
            for seq in comb_list:
                is_1d_in_2d = False
                for step in seq:
                    if type(step) == tuple and len(step) == 1:
                        # if 1d step, check whether in any of the other 2 stpes
                        is_step_in = any([step[0] in s for s in seq if type(s)==tuple and len(s)==2])
                        if is_step_in:
                            is_1d_in_2d = True

                if not is_1d_in_2d:
                    out_seqs.append(seq)
            return out_seqs

        combs_2d = list(itertools.combinations(self._avail_axes, 2))
        combs_1d = list(itertools.combinations(self._avail_axes, 1))
        out_seqs = []

        for dim in self._optimizer_dim:
            if dim == 1:
                out_seqs = add_comb(out_seqs, combs_1d)
            elif dim == 2:
                out_seqs = add_comb(out_seqs, combs_2d)
            else:
                raise ValueError("Only support 1d and 2d optimization sequences.")

        # add permutations
        out_seqs_any_order = []
        for seq in out_seqs:
            out_seqs_any_order.extend([list(el) for el in list(itertools.permutations(seq))])
        out_seqs = out_seqs_any_order
        # clean up
        out_seqs = remove_duplicates(out_seqs)
        if remove_1d_in_2d:
            out_seqs = remove_1d_in_2d_axes_dupl(out_seqs)

        return out_seqs


