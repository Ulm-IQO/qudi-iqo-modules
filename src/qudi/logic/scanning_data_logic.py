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


import time
import copy
import datetime
import numpy as np
from functools import reduce
import operator

import matplotlib as mpl
import matplotlib.pyplot as plt
from PySide2 import QtCore

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.datastorage import ImageFormat, NpyDataStorage, TextDataStorage
from qudi.util.units import ScaledFloat

from qudi.interface.scanning_probe_interface import ScanData


class ScanningDataLogic(LogicBase):
    """
    Todo: add some info about this module
    
    Example config:
    
    scanning_data_logic:
        module.Class: 'scanning_data_logic.ScanningDataLogic'
        max_history_length: 50
        connect:
            scan_logic: scanning_probe_logic
    
    """

    # declare connectors
    _scan_logic = Connector(name='scan_logic', interface='ScanningProbeLogic')

    # config options
    _max_history_length = ConfigOption(name='max_history_length', default=10)

    # status variables
    _scan_history = StatusVar(name='scan_history', default=list())

    # signals
    sigHistoryScanDataRestored = QtCore.Signal(object)
    sigSaveStateChanged = QtCore.Signal(bool)

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self._thread_lock = RecursiveMutex()

        self._curr_history_index = 0
        self._curr_data_per_scan = dict()
        self._logic_id = None
        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._shrink_history()
        if self._scan_history:
            self._curr_data_per_scan = {sd.scan_axes: sd for sd in self._scan_history}
            self.restore_from_history(-1)
        else:
            self._curr_history_index = 0
            self._curr_data_per_scan = dict()
        self._logic_id = self._scan_logic().module_uuid
        self._scan_logic().sigScanStateChanged.connect(self._update_scan_state)

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self._scan_logic().sigScanStateChanged.disconnect(self._update_scan_state)
        self._curr_data_per_scan = dict()

    @_scan_history.representer
    def __scan_history_to_dicts(self, history):
        return [data.to_dict() for data in history]

    @_scan_history.constructor
    def __scan_history_from_dicts(self, history_dicts):
        return [ScanData.from_dict(hist_dict) for hist_dict in history_dicts]

    def get_current_scan_data(self, scan_axes=None):
        """
        Get the most recent scan data for a certain (or the most recent) scan axes.
        @param tuple scan_axes: axis to get data for. If None yields most recent scan.
        @return ScanData: most recent scan data
        """
        with self._thread_lock:
            if scan_axes is None:
                try:
                    scan_axes = self._scan_history[-1].scan_axes
                except IndexError:
                    return None
            return self._curr_data_per_scan.get(scan_axes, None)

    def get_current_scan_id(self, scan_axes=None):
        """
        Yield most recent scan data id for a given scan axis or overall.
        """

        with self._thread_lock:
            ret_id = np.nan
            idx_i = -1
            for scan_data in reversed(self._scan_history):
                if scan_data.scan_axes == scan_axes or scan_axes is None:
                    ret_id = idx_i
                    break
                idx_i -= 1

            if np.isnan(ret_id):
                return np.nan

            assert self._scan_history[ret_id] == self.get_current_scan_data(scan_axes)
            return self._abs_index(ret_id)

    def get_all_current_scan_data(self):
        with self._thread_lock:
            return list(self._curr_data_per_scan.copy().values())

    def history_previous(self):
        with self._thread_lock:
            if self._curr_history_index < 1:
                self.log.warning('Unable to restore previous state from scan history. '
                                 'Already at earliest history entry.')
                return

            #self.log.debug(f"Hist_prev called, index {self._curr_history_index - 1}")
            return self.restore_from_history(self._curr_history_index - 1)

    def history_next(self):
        with self._thread_lock:
            if self._curr_history_index >= len(self._scan_history) - 1:
                self.log.warning('Unable to restore next state from scan history. '
                                 'Already at latest history entry.')
                return
            #self.log.debug(f"Hist_prev called, index {self._curr_history_index + 1}")
            return self.restore_from_history(self._curr_history_index + 1)

    def restore_from_history(self, index):
        with self._thread_lock:
            if self._scan_logic().module_state() != 'idle':
                self.log.error('Scan is running. Unable to restore history state.')
                return

            index = self._abs_index(index)

            try:
                data = self._scan_history[index]
            except IndexError:
                self.log.exception('Unable to restore scan history with index "{0}"'.format(index))
                return

            settings = {
                'range': {ax: data.scan_range[i] for i, ax in enumerate(data.scan_axes)},
                'resolution': {ax: data.scan_resolution[i] for i, ax in enumerate(data.scan_axes)},
                'frequency': {data.scan_axes[0]: data.scan_frequency}
            }
            self._scan_logic().set_scan_settings(settings)

            #self.log.debug(f"Restoring hist settings from index {index} with {settings}")

            self._curr_history_index = index
            self._curr_data_per_scan[data.scan_axes] = data
            self.sigHistoryScanDataRestored.emit(data)
            return

    def _update_scan_state(self, running, data, caller_id):

        settings = {
            'range': {ax: data.scan_range[i] for i, ax in enumerate(data.scan_axes)},
            'resolution': {ax: data.scan_resolution[i] for i, ax in enumerate(data.scan_axes)},
            'frequency': {data.scan_axes[0]: data.scan_frequency}
        }

        with self._thread_lock:
            if not running and caller_id is self._logic_id:
                #self.log.debug(f"Adding to data history with settings {settings}")
                self._scan_history.append(data)
                self._shrink_history()
                self._curr_data_per_scan[data.scan_axes] = data
                self._curr_history_index = len(self._scan_history) - 1
                self.sigHistoryScanDataRestored.emit(data)

    def _shrink_history(self):
        while len(self._scan_history) > self._max_history_length:
            self._scan_history.pop(0)

    def _abs_index(self, index):
        if index < 0:
            index = max(0, len(self._scan_history) + index)

        return index

    def draw_1d_scan_figure(self, scan_data, channel):
        """ Create an XY plot of 1D scan data.

        @return fig: a matplotlib figure object to be saved to file.
        """
        data = scan_data.data[channel]
        axis = scan_data.scan_axes[0]
        scanner_pos = self._scan_logic().scanner_target

        # Scale axes and data
        scan_range_x = (scan_data.scan_range[0][0], scan_data.scan_range[0][1])
        si_prefix_x = ScaledFloat(scan_range_x[1]-scan_range_x[0]).scale
        si_factor_x = ScaledFloat(scan_range_x[1]-scan_range_x[0]).scale_val
        si_prefix_data = ScaledFloat(np.nanmax(data)-np.nanmin(data)).scale
        si_factor_data = ScaledFloat(np.nanmax(data)-np.nanmin(data)).scale_val

        # Create figure
        fig, ax = plt.subplots()

        # Create image plot
        x_axis = np.linspace(scan_data.scan_range[0][0],
                             scan_data.scan_range[0][1],
                             scan_data.scan_resolution[0])
        x_axis = x_axis[~np.isnan(data)]
        data = data[~np.isnan(data)]

        xy_plot = ax.plot(x_axis/si_factor_x,
                          data/si_factor_data)

        # Axes labels
        if scan_data.axes_units[axis]:
            x_label = axis + f' position ({si_prefix_x}{scan_data.axes_units[axis]})'
        else:
            x_label = axis + f' position ({si_prefix_x})'
        if scan_data.channel_units[channel]:
            y_label = f'{channel} ({si_prefix_data}{scan_data.channel_units[channel]})'
        else:
            y_label = f'{channel} ({si_prefix_data})'

        # ax.set_aspect(1)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.spines['bottom'].set_position(('outward', 10))
        ax.spines['left'].set_position(('outward', 10))
        # ax.spines['top'].set_visible(False)
        # ax.spines['right'].set_visible(False)
        # ax.get_xaxis().tick_bottom()
        # ax.get_yaxis().tick_left()

        # draw the scanner position if defined
        pos_x = scanner_pos[axis]
        if pos_x > np.min(x_axis) and pos_x < np.max(x_axis):
            trans_xmark = mpl.transforms.blended_transform_factory(ax.transData, ax.transAxes)
            ax.annotate('',
                        xy=np.asarray([pos_x, 0])/si_factor_x,
                        xytext=(pos_x/si_factor_x, -0.01),
                        xycoords=trans_xmark,
                        arrowprops={'facecolor': '#17becf', 'shrink': 0.05})
        return fig

    def save_scan(self, scan_data, color_range=None):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.error('Unable to save 2D scan. Saving still in progress...')
                return

            if scan_data is None:
                raise ValueError('Unable to save 2D scan. No data available.')

            self.sigSaveStateChanged.emit(True)
            self.module_state.lock()
            try:
                ds = TextDataStorage(root_dir=self.module_default_data_dir)
                timestamp = datetime.datetime.now()

                # ToDo: Add meaningful metadata if missing:
                parameters = {}
                for range, resolution, unit, axis in zip(scan_data.scan_range,
                                      scan_data.scan_resolution,
                                      scan_data.axes_units.values(),
                                      scan_data.scan_axes):

                    parameters[f"{axis} axis name"] = axis
                    parameters[f"{axis} axis unit"] = unit
                    parameters[f"{axis} scan range"] = range
                    parameters[f"{axis} axis resolution"] = resolution
                    parameters[f"{axis} axis min"] = range[0]
                    parameters[f"{axis} axis max"] = range[1]

                parameters["pixel frequency"] = scan_data.scan_frequency
                parameters[f"scanner target at start"] = scan_data.scanner_target_at_start
                parameters['measurement start'] = str(scan_data._timestamp)

                # add meta data for axes in full target, but not scan axes
                if scan_data.scanner_target_at_start:
                    for new_ax in scan_data.scanner_target_at_start.keys():
                        if new_ax not in scan_data.scan_axes:
                            ax_info = self._scan_logic().scanner_constraints.axes[new_ax]
                            ax_name = ax_info.name
                            ax_unit = ax_info.unit
                            parameters[f"{new_ax} axis name"] = ax_name
                            parameters[f"{new_ax} axis unit"] = ax_unit

                # Save data and thumbnail to file
                for channel, data in scan_data.data.items():
                    # data
                    # nametag = '{0}_{1}{2}_image_scan'.format(channel, *scan_data.scan_axes)
                    tag = self.create_tag_from_scan_data(scan_data, channel)
                    file_path, _, _ = ds.save_data(data,
                                                   metadata=parameters,
                                                   nametag=tag,
                                                   timestamp=timestamp,
                                                   column_headers='Image (columns is X, rows is Y)')
                    # thumbnail
                    if len(scan_data.scan_axes) == 1:
                        figure = self.draw_1d_scan_figure(scan_data, channel)
                        ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])
                    elif len(scan_data.scan_axes) == 2:
                        figure = self.draw_2d_scan_figure(scan_data, channel, cbar_range=color_range)
                        ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])
                    else:
                        self.log.warning('No figure saved for data with more than 2 dimensions.')

            finally:
                self.module_state.unlock()
                self.sigSaveStateChanged.emit(False)
            return

    def save_scan_by_axis(self, scan_axes=None, color_range=None):
        # wrapper for self.save_scan. Avoids copying scan_data through QtSignals
        scan = self.get_current_scan_data(scan_axes=scan_axes)
        self.save_scan(scan, color_range=color_range)

    def create_tag_from_scan_data(self, scan_data, channel):
        axes = scan_data.scan_axes
        axis_dim = len(axes)
        axes_code = reduce(operator.add, axes)
        tag = f"{axis_dim}D-scan with {axes_code} axes from channel {channel}"
        return tag

    def draw_2d_scan_figure(self, scan_data, channel, cbar_range=None):
        """ Create a 2-D color map figure of the scan image.

        @return fig: a matplotlib figure object to be saved to file.
        """
        image_arr = scan_data.data[channel]
        scan_axes = scan_data.scan_axes
        scanner_pos = self._scan_logic().scanner_target


        # If no colorbar range was given, take full range of data
        if cbar_range is None:
            cbar_range = (np.nanmin(image_arr), np.nanmax(image_arr))

        # Create figure
        fig, ax = plt.subplots()

        # Scale axes and data
        scan_range_x = (scan_data.scan_range[0][1], scan_data.scan_range[0][0])
        scan_range_y =  (scan_data.scan_range[1][1], scan_data.scan_range[1][0])
        si_prefix_x = ScaledFloat(scan_range_x[1]-scan_range_x[0]).scale
        si_factor_x = ScaledFloat(scan_range_x[1]-scan_range_x[0]).scale_val
        si_prefix_y = ScaledFloat(scan_range_y[1]-scan_range_y[0]).scale
        si_factor_y = ScaledFloat(scan_range_y[1]-scan_range_y[0]).scale_val
        si_prefix_cb = ScaledFloat(cbar_range[1]-cbar_range[0]).scale if cbar_range[1]!=cbar_range[0] \
            else ScaledFloat(cbar_range[1])
        si_factor_cb = ScaledFloat(cbar_range[1]-cbar_range[0]).scale_val

        # Create image plot
        cfimage = ax.imshow(image_arr.transpose()/si_factor_cb,
                            cmap='inferno',  # FIXME: reference the right place in qudi
                            origin='lower',
                            vmin=cbar_range[0]/si_factor_cb,
                            vmax=cbar_range[1]/si_factor_cb,
                            interpolation='none',
                            extent=(*np.asarray(scan_data.scan_range[0])/si_factor_x,
                                    *np.asarray(scan_data.scan_range[1])/si_factor_y))

        ax.set_aspect(1)
        ax.set_xlabel(scan_axes[0] + f' position ({si_prefix_x}{scan_data.axes_units[scan_axes[0]]})')
        ax.set_ylabel(scan_axes[1] + f' position ({si_prefix_y}{scan_data.axes_units[scan_axes[1]]})')
        ax.spines['bottom'].set_position(('outward', 10))
        ax.spines['left'].set_position(('outward', 10))
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.get_xaxis().tick_bottom()
        ax.get_yaxis().tick_left()


        pos_x, pos_y = scanner_pos[scan_axes[0]], scanner_pos[scan_axes[1]]

        # draw the scanner position if defined and in range
        if pos_x > np.min(scan_range_x) and pos_x < np.max(scan_range_x) \
            and pos_y > np.min(scan_range_y) and pos_y < np.max(scan_range_y):
            trans_xmark = mpl.transforms.blended_transform_factory(ax.transData, ax.transAxes)
            trans_ymark = mpl.transforms.blended_transform_factory(ax.transAxes, ax.transData)
            ax.annotate('',
                        xy=np.asarray([pos_x, 0])/si_factor_x,
                        xytext=(pos_x/si_factor_x, -0.01),
                        xycoords=trans_xmark,
                        arrowprops={'facecolor': '#17becf', 'shrink': 0.05})
            ax.annotate('',
                        xy=np.asarray([0, pos_y])/si_factor_y,
                        xytext=(-0.01, pos_y/si_factor_y),
                        xycoords=trans_ymark,
                        arrowprops={'facecolor': '#17becf', 'shrink': 0.05})

        metainfo_str = self._pretty_print_metainfo(scan_axes, scan_data, scanner_pos)
        if metainfo_str:
            ax.annotate(metainfo_str,
                        xy=(1.10, -.17), xycoords='axes fraction',
                        horizontalalignment='left', verticalalignment='bottom',
                        fontsize=7, color='grey')

        # Draw the colorbar
        cbar = plt.colorbar(cfimage, shrink=0.8)  #, fraction=0.046, pad=0.08, shrink=0.75)
        if scan_data.channel_units[channel]:
            cbar.set_label(f'{channel} ({si_prefix_cb}{scan_data.channel_units[channel]})')
        else:
            cbar.set_label(f'{channel}')

        # remove ticks from colorbar for cleaner image
        cbar.ax.tick_params(which=u'both', length=0)
        return fig

    def _pretty_print_metainfo(self, scan_axes, scan_data, scanner_pos):
        metainfo_str = ""

        # annotate scanner position
        metainfo_str = "Scanner target:\n"
        for axis in scan_axes:
            val = scanner_pos[axis]
            unit = scan_data.axes_units[axis]
            metainfo_str += f"{axis}: {ScaledFloat(val):.3r}{unit}\n"

        # annotate the (all axes) scanner start target
        if scan_data.scanner_target_at_start:
            target_str = ""
            for (target_ax, target_val) in scan_data.scanner_target_at_start.items():
                if target_ax not in scan_axes:
                    ax_info = self._scan_logic().scanner_constraints.axes[target_ax]
                    unit = ax_info.unit
                    target_str += f"{target_ax}: {ScaledFloat(target_val):.3r}{unit}\n"
            if target_str:
                metainfo_str += "Scan start at:\n"
                metainfo_str += target_str

        return metainfo_str

