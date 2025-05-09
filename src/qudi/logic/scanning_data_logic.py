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


import datetime

import numpy as np
from functools import reduce
import operator
from typing import List, Optional, Tuple, Dict, Set, Union

import matplotlib as mpl
import matplotlib.pyplot as plt
from PySide2 import QtCore

from qudi.core.module import LogicBase
from qudi.util.mutex import RecursiveMutex
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.datastorage import TextDataStorage
from qudi.util.units import ScaledFloat

from qudi.interface.scanning_probe_interface import ScanData, ScanImage
from qudi.logic.scanning_probe_logic import ScanningProbeLogic


class ScanningDataLogic(LogicBase):
    """
    Todo: add some info about this module

    Example config:

    scanning_data_logic:
        module.Class: 'scanning_data_logic.ScanningDataLogic'
        options:
            max_history_length: 50
            save_back_scan_data: False
        connect:
            scan_logic: scanning_probe_logic

    """

    # declare connectors
    _scan_logic = Connector(name='scan_logic', interface='ScanningProbeLogic')

    # config options
    _max_history_length: int = ConfigOption(name='max_history_length', default=10)
    _save_back_scan_data: bool = ConfigOption(name='save_back_scan_data', default=False)

    # status variables
    # both forward and backward scan data are retained
    _scan_history: List[Tuple[ScanData, Optional[ScanData]]] = StatusVar(name='scan_history', default=list())

    # signals
    sigHistoryScanDataRestored = QtCore.Signal(ScanData, ScanData, int)
    sigSaveStateChanged = QtCore.Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = RecursiveMutex()

        self._curr_history_index = 0
        self._logic_id = None
        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._shrink_history()
        if self._scan_history:
            self._restore_from_history_index(-1)
        else:
            self._curr_history_index = 0
        self._logic_id = self._scan_logic().module_uuid
        self._scan_logic().sigNewScanDataForHistory.connect(self._append_to_history)

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self._scan_logic().sigNewScanDataForHistory.disconnect(self._append_to_history)

    @_scan_history.representer
    def __scan_history_to_dicts(self, history: List[Tuple[ScanData, Optional[ScanData]]])\
            -> List[Tuple[Dict, Optional[Dict]]]:
        history_dicts = []
        for data, back_data in history:
            data_dict = data.to_dict()
            back_data_dict = back_data.to_dict() if back_data is not None else None
            history_dicts.append((data_dict, back_data_dict))
        return history_dicts

    @_scan_history.constructor
    def __scan_history_from_dicts(self, history_dicts: List[List[Optional[Dict]]])\
            -> List[Tuple[ScanData, Optional[ScanData]]]:
        history = []

        scan_axes = self._scan_logic().scanner_axes
        scan_axes_avail = [ax.name for ax in scan_axes.values()]

        data_dropped = False
        try:
            for data_dict, back_data_dict in history_dicts:
                data = ScanData.from_dict(data_dict)
                back_data = ScanData.from_dict(back_data_dict) if back_data_dict is not None else None
                data_axs = data.scanner_target_at_start.keys()
                if not (set(data_axs) <= set(scan_axes_avail)):
                    data_dropped = True
                    continue

                history.append((data, back_data))
        except Exception as e:
            self.log.warning("Unable to load scan history. Deleting scan history.", exc_info=e)

        if data_dropped:
            self.log.warning("Deleted scan history entries containing an incompatible scan axes configuration.")
            
        return history

    def get_last_history_entry(self, scan_axes: Optional[Tuple[str, ...]] = None)\
            -> Tuple[Optional[ScanData], Optional[ScanData]]:
        """
        Get the most recent scan data / back scan data (for a certain scan axes).
        @param tuple scan_axes: axis or 2D axis pair to get data for
        @return tuple: most recent scan data and back scan data
        """
        with self._thread_lock:
            if scan_axes is None:
                if self._scan_history:
                    return self._scan_history[-1]
                else:
                    # history is empty
                    return None, None
            else:
                index = self._get_last_history_entry_index(scan_axes)
                if index is not None:
                    return self._scan_history[index]
                else:
                    # no scan saved in history for these axes
                    return None, None

    def get_axes_with_history_entry(self) -> Set[Tuple[str, ...]]:
        """Get all axes with at least one history entry."""
        return {data.settings.axes for data, _ in self._scan_history}

    def restore_from_history(self, scan_axes: Optional[Tuple[str, ...]] = None, set_target: bool = True):
        """Restore the latest entry in history for specified scan axes."""
        with self._thread_lock:
            index = self._get_last_history_entry_index(scan_axes)
            if index is not None:
                self._restore_from_history_index(index, set_target)

    def history_previous(self):
        with self._thread_lock:
            if self._curr_history_index < 1:
                self.log.warning('Unable to restore previous state from scan history. '
                                 'Already at earliest history entry.')
                return

            #self.log.debug(f"Hist_prev called, index {self._curr_history_index - 1}")
            return self._restore_from_history_index(self._curr_history_index - 1)

    def history_next(self):
        with self._thread_lock:
            if self._curr_history_index >= len(self._scan_history) - 1:
                self.log.warning('Unable to restore next state from scan history. '
                                 'Already at latest history entry.')
                return
            return self._restore_from_history_index(self._curr_history_index + 1)

    def _restore_from_history_index(self, index: int, set_target: bool = True):
        with self._thread_lock:
            scan_logic: ScanningProbeLogic = self._scan_logic()
            if scan_logic.module_state() != 'idle':
                self.log.error('Scan is running. Unable to restore history state.')
                return

            index = self._abs_index(index)

            try:
                data, back_data = self._scan_history[index]
            except IndexError:
                self.log.exception('Unable to restore scan history with index "{0}"'.format(index))
                return

            self._curr_history_index = index
            self.sigHistoryScanDataRestored.emit(data, back_data, set_target)

    def _get_last_history_entry_index(self, scan_axes: Optional[Tuple[str, ...]] = None)\
            -> Union[int, None]:
        """
        Get the history index of the most recent entry for a certain scan axes.
        @param tuple scan_axes: axis or 2D axis pair to get data for
        @return int: index
        """
        with self._thread_lock:
            if scan_axes is None and self._scan_history:
                return -1
            for i in range(len(self._scan_history) - 1, -1, -1):
                data, _ = self._scan_history[i]
                if data.settings.axes == scan_axes:
                    return i

    def _append_to_history(self, data: ScanData, back_data: Optional[ScanData]):
        with self._thread_lock:
            self._scan_history.append((data, back_data))
            self._shrink_history()
            self._curr_history_index = len(self._scan_history) - 1
            self.sigHistoryScanDataRestored.emit(data, back_data, True)

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
        axis = scan_data.settings.axes[0]
        scanner_pos = self._scan_logic().scanner_target

        # Scale axes and data
        scan_range_x = (scan_data.settings.range[0][0], scan_data.settings.range[0][1])
        si_prefix_x = ScaledFloat(scan_range_x[1]-scan_range_x[0]).scale
        si_factor_x = ScaledFloat(scan_range_x[1]-scan_range_x[0]).scale_val
        si_prefix_data = ScaledFloat(np.nanmax(data)-np.nanmin(data)).scale
        si_factor_data = ScaledFloat(np.nanmax(data)-np.nanmin(data)).scale_val

        # Create figure
        fig, ax = plt.subplots()

        # Create image plot
        x_axis = np.linspace(scan_data.settings.range[0][0],
                             scan_data.settings.range[0][1],
                             scan_data.settings.resolution[0])
        x_axis = x_axis[~np.isnan(data)]
        data = data[~np.isnan(data)]

        xy_plot = ax.plot(x_axis/si_factor_x,
                          data/si_factor_data)

        # Axes labels
        if scan_data.axis_units[axis]:
            x_label = axis + f' position ({si_prefix_x}{scan_data.axis_units[axis]})'
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

    def save_scan(self, scan_data, color_range=None, is_back: bool = False,
                  custom_tag: str = None):
        with self._thread_lock:
            if self.module_state() != 'idle':
                self.log.error('Unable to save 2D scan. Saving still in progress...')
                return

            self.sigSaveStateChanged.emit(True)
            self.module_state.lock()
            try:
                if scan_data is None:
                    self.log.error('Unable to save 2D scan. No data available.')
                    raise ValueError('Unable to save 2D scan. No data available.')

                ds = TextDataStorage(root_dir=self.module_default_data_dir)
                timestamp = datetime.datetime.now()

                # ToDo: Add meaningful metadata if missing:
                parameters = {}
                for range, resolution, unit, axis in zip(scan_data.settings.range,
                                      scan_data.settings.resolution,
                                      scan_data.axis_units.values(),
                                      scan_data.settings.axes):

                    parameters[f"{axis} axis name"] = axis
                    parameters[f"{axis} axis unit"] = unit
                    parameters[f"{axis} scan range"] = range
                    parameters[f"{axis} axis resolution"] = resolution
                    parameters[f"{axis} axis min"] = range[0]
                    parameters[f"{axis} axis max"] = range[1]

                parameters["pixel frequency"] = scan_data.settings.frequency
                parameters[f"scanner target at start"] = scan_data.scanner_target_at_start
                parameters['measurement start'] = str(scan_data.timestamp)
                parameters['coordinate transform info'] = scan_data.coord_transform_info

                # add meta data for axes in full target, but not scan axes
                if scan_data.scanner_target_at_start:
                    for new_ax in scan_data.scanner_target_at_start.keys():
                        if new_ax not in scan_data.settings.axes:
                            ax_info = self._scan_logic().scanner_constraints.axes[new_ax]
                            ax_name = ax_info.name
                            ax_unit = ax_info.unit
                            parameters[f"{new_ax} axis name"] = ax_name
                            parameters[f"{new_ax} axis unit"] = ax_unit

                # Save data and thumbnail to file
                for channel, data in scan_data.data.items():

                    tag = self.create_tag_from_scan_data(scan_data, channel, is_back,
                                                         custom_tag)

                    file_path, _, _ = ds.save_data(data,
                                                   metadata=parameters,
                                                   nametag=tag,
                                                   timestamp=timestamp,
                                                   column_headers='Image (columns is X, rows is Y)')
                    # thumbnail
                    if len(scan_data.settings.axes) == 1:
                        figure = self.draw_1d_scan_figure(scan_data, channel)
                        ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])
                    elif len(scan_data.settings.axes) == 2:
                        scan_image = ScanImage.from_scan_data(scan_data, channel)
                        figure = self.draw_2d_scan_figure(scan_image, cbar_range=color_range)
                        ax = plt.gca()
                        self._add_draw_scanner_pos(ax, scan_data)
                        ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])
                    else:
                        self.log.warning('No figure saved for data with more than 2 dimensions.')

            finally:
                self.module_state.unlock()
                self.sigSaveStateChanged.emit(False)
            return

    def save_scan_by_axis(self, scan_axes=None, color_range=None, custom_tag=None):
        # wrapper for self.save_scan. Avoids copying scan_data through QtSignals
        scan_data, back_scan_data = self.get_last_history_entry(scan_axes=scan_axes)
        self.log.debug(f"Attempting to save {scan_data}")
        if scan_data is not None:
            self.save_scan(scan_data, color_range=color_range, custom_tag=custom_tag)
            if self._save_back_scan_data and back_scan_data is None:
                self.log.warning(f"No back scan data to save.")
            elif self._save_back_scan_data:
                self.save_scan(back_scan_data, color_range=color_range, is_back=True, custom_tag=custom_tag)
        else:
            self.log.warning(f"No data in history for {scan_axes} scan.")
            self.sigSaveStateChanged.emit(False)

    @staticmethod
    def create_tag_from_scan_data(scan_data: ScanData, channel: str,
                                  is_back: bool = False, custom_tag: str = None):
        axes = scan_data.settings.axes
        axis_dim = len(axes)
        axes_code = reduce(operator.add, axes)

        tag = f"{axis_dim}D-scan with {axes_code} axes from channel {channel}"
        if custom_tag:
            tag = f"{custom_tag} {channel}"

        if is_back:
            tag = "back " + tag

        return tag

    def draw_2d_scan_figure(self, scan_image: ScanImage, cbar_range=None):
        """ Create a 2-D color map figure of the scan image.

        @return fig: a matplotlib figure object to be saved to file.
        """
        image_arr = scan_image.data
        scan_axes = scan_image.axis_names

        # If no colorbar range was given, take full range of data
        if cbar_range is None:
            cbar_range = (np.nanmin(image_arr), np.nanmax(image_arr))

        # Create figure
        fig, ax = plt.subplots()

        # Scale axes and data
        si_x = scan_image.si_factors[0]
        si_prefix_x = si_x.scale
        si_factor_x = si_x.scale_val
        si_y = scan_image.si_factors[0]
        si_prefix_y = si_y.scale
        si_factor_y = si_y.scale_val
        si_prefix_cb = ScaledFloat(cbar_range[1]-cbar_range[0]).scale if cbar_range[1] != cbar_range[0] \
            else ScaledFloat(cbar_range[1])
        si_factor_cb = ScaledFloat(cbar_range[1]-cbar_range[0]).scale_val

        # Create image plot
        cfimage = ax.imshow(image_arr.transpose()/si_factor_cb,
                            cmap='inferno',  # FIXME: reference the right place in qudi
                            origin='lower',
                            vmin=cbar_range[0]/si_factor_cb,
                            vmax=cbar_range[1]/si_factor_cb,
                            interpolation='none',
                            extent=(*np.asarray(scan_image.ranges[0])/si_factor_x,
                                    *np.asarray(scan_image.ranges[1])/si_factor_y))

        ax.set_aspect(1)
        ax.set_xlabel(scan_axes[0] + f' position ({si_prefix_x}{scan_image.axis_units[0]})')
        ax.set_ylabel(scan_axes[1] + f' position ({si_prefix_y}{scan_image.axis_units[1]})')
        ax.spines['bottom'].set_position(('outward', 10))
        ax.spines['left'].set_position(('outward', 10))
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.get_xaxis().tick_bottom()
        ax.get_yaxis().tick_left()

        # Draw the colorbar
        cbar = plt.colorbar(cfimage, shrink=0.8)  # , fraction=0.046, pad=0.08, shrink=0.75)
        if scan_image.data_unit:
            cbar.set_label(f'{scan_image.data_name} ({si_prefix_cb}{scan_image.data_unit})')
        else:
            cbar.set_label(f'{scan_image.data_name}')

        # remove ticks from colorbar for cleaner image
        cbar.ax.tick_params(which=u'both', length=0)
        return fig

    def _add_draw_scanner_pos(self, ax, scan_data: ScanData):

        scanner_pos = self._scan_logic().scanner_target
        scan_axes = scan_data.settings.axes

        scan_range_x = (scan_data.settings.range[0][1], scan_data.settings.range[0][0])
        scan_range_y = (scan_data.settings.range[1][1], scan_data.settings.range[1][0])

        si_factor_x = ScaledFloat(scan_range_x[1] - scan_range_x[0]).scale_val
        si_factor_y = ScaledFloat(scan_range_y[1] - scan_range_y[0]).scale_val

        pos_x, pos_y = scanner_pos[scan_axes[0]], scanner_pos[scan_axes[1]]

        # draw the scanner position if defined and in range
        if np.min(scan_range_x) < pos_x < np.max(scan_range_x) \
                and np.min(scan_range_y) < pos_y < np.max(scan_range_y):
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

    def _pretty_print_metainfo(self, scan_axes, scan_data, scanner_pos):
        metainfo_str = ""

        # annotate scanner position
        metainfo_str = "Scanner target:\n"
        for axis in scan_axes:
            val = scanner_pos[axis]
            unit = scan_data.axis_units[axis]
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

