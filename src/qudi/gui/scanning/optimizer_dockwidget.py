# -*- coding: utf-8 -*-

"""
This file contains a QDockWidget subclass to display the scanner optimizer results.

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

__all__ = ('OptimizerDockWidget',)

import numpy as np
from PySide2 import QtCore, QtWidgets
from pyqtgraph import PlotDataItem, mkPen
import copy as cp

from qudi.util.widgets.plotting.plot_widget import DataSelectionPlotWidget
from qudi.util.widgets.plotting.plot_item import DataImageItem, XYPlotItem
from qudi.util.colordefs import QudiPalette


class OptimizerDockWidget(QtWidgets.QDockWidget):
    """
    """

    def __init__(self, axes, plot_dims, sequence, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('Optimizer')
        self.setObjectName('optimizer_dockWidget')

        self._last_optimal_pos = {}
        self._last_optimal_sigma = {}
        self._scanner_sequence = sequence
        self._plot_widgets = []

        self.pos_ax_label = QtWidgets.QLabel(f'({", ".join(axes)}):')
        self.pos_ax_label.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.result_label = QtWidgets.QLabel(f'({", ".join(["?"]*len(axes))}):')
        self.result_label.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        label_layout = QtWidgets.QHBoxLayout()
        label_layout.addWidget(self.pos_ax_label)
        label_layout.addWidget(self.result_label)
        label_layout.setStretch(1, 1)

        layout = QtWidgets.QGridLayout()
        # fill list of all optimizer subplot widgets
        for i_col, n_dim in enumerate(plot_dims):
            if n_dim == 1:
                plot_item = XYPlotItem(pen=mkPen(QudiPalette.c1, style=QtCore.Qt.DotLine),
                                       symbol='o',
                                       symbolPen=QudiPalette.c1,
                                       symbolBrush=QudiPalette.c1,
                                       symbolSize=7)
                fit_plot_item = XYPlotItem(pen=mkPen(QudiPalette.c2))
                plot1d_widget = DataSelectionPlotWidget()
                plot1d_widget.set_selection_mutable(False)
                plot1d_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
                plot1d_widget.addItem(plot_item)
                plot1d_widget.add_marker_selection((0, 0),
                                                   mode=DataSelectionPlotWidget.SelectionMode.X)
                self._plot_widgets.append({'widget': plot1d_widget, 'plot_1d': plot_item,
                                           'fit_1d': fit_plot_item, 'dim': 1})
            elif n_dim == 2:
                plot2d_widget = DataSelectionPlotWidget()
                plot2d_widget.setAspectLocked(lock=True, ratio=1)
                plot2d_widget.set_selection_mutable(False)
                plot2d_widget.add_marker_selection((0, 0),
                                                   mode=DataSelectionPlotWidget.SelectionMode.XY)
                image_item = DataImageItem()
                plot2d_widget.addItem(image_item)
                self._plot_widgets.append({'widget': plot2d_widget,
                                           'image_2d': image_item,
                                           'dim': 2})
            else:
                raise ValueError(f"Optimizer widget can have axis dim= 1 or 2, not {n_dim}")

            layout.addWidget(self._plot_widgets[-1]['widget'], 0, i_col)

        layout.addLayout(label_layout, 1, 0, 1, 2)
        layout.setRowStretch(0, 1)

        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        self.setWidget(widget)

    @property
    def scan_sequence(self):
        return cp.copy(self._scanner_sequence)

    @scan_sequence.setter
    def scan_sequence(self, sequence):
        self._scanner_sequence = sequence

    def _get_all_widgets_part(self, part='widget', dim=None):
        widgets_1d = [wid for wid in self._plot_widgets if wid['dim'] == 1]
        widgets_2d = [wid for wid in self._plot_widgets if wid['dim'] == 2]

        if dim is None:
            return [wid[part] for wid in self._plot_widgets]
        elif dim == 1:
            return [wid[part] for wid in widgets_1d]
        elif dim == 2:
            return [wid[part] for wid in widgets_2d]
        else:
            raise ValueError

    def _get_widget_part(self, axs, part='widget'):
        """
        Based on the given axes, return the corresponding widget.
        Will keep the order given by self._scan_sequence. Eg. axs=('x','y') will
        give the second 2d widget for the scan order [('phi', 'z'), ('x','y')]
        """

        seqs_1d = [tuple(seq) for seq in self._scanner_sequence if len(seq) == 1]
        seqs_2d = [tuple(seq) for seq in self._scanner_sequence if len(seq) == 2]
        widgets_1d = [wid for wid in self._plot_widgets if wid['dim'] == 1]
        widgets_2d = [wid for wid in self._plot_widgets if wid['dim'] == 2]

        widget = None
        axs = tuple(axs)

        try:
            if len(axs) == 1:
                idx = seqs_1d.index(axs)
                widget = widgets_1d[idx]
            elif len(axs) == 2:
                idx = seqs_2d.index(axs)
                widget = widgets_2d[idx]
            else:
                raise ValueError
        except ValueError:
            raise ValueError(f"Given axs {axs} not in scanner sequence. Couldn't find widget.")

        return widget[part]

    def get_plot_widget(self, axs):
        return self._get_widget_part(axs, part='widget')

    def toogle_crosshair(self, axs=None, enabled=False):
        """
        Toggle all or specified crosshairds of 2d widgets.
        """
        if axs:
            plot2d_widgets = [self._get_widget_part(axs, part='widget')]
        else:
            plot2d_widgets = self._get_all_widgets_part(dim=2)

        for wid in plot2d_widgets:
            if enabled:
                wid.show_marker_selections()
            else:
                wid.hide_marker_selections()

    def toogle_marker(self, axs=None, enabled=False):
        """
        Toggle all or specified markers of 1d widgets.
        """
        if axs:
            plot1d_widgets = [self._get_widget_part(axs, part='widget')]
        else:
            plot1d_widgets = self._get_all_widgets_part(dim=1)

        for wid in plot1d_widgets:
            if enabled:
                wid.show_marker_selections()
            else:
                wid.hide_marker_selections()

    def set_2d_position(self, pos, axs, sigma=None):
        widget = self.get_plot_widget(axs)
        widget.move_marker_selection(pos, index=0)

        self._last_optimal_pos[axs[0]] = pos[0]
        self._last_optimal_pos[axs[1]] = pos[1]
        if sigma:
            self._last_optimal_sigma[axs[0]] = sigma[0]
            self._last_optimal_sigma[axs[1]] = sigma[1]

        self.update_result_label()

    def set_1d_position(self, pos, axs, sigma=None):
        widget = self.get_plot_widget(axs)
        widget.move_marker_selection((pos, 0), index=0)

        self._last_optimal_pos[axs[0]] = pos
        if sigma:
            self._last_optimal_sigma[axs[0]] = sigma

        self.update_result_label()

    def update_result_label(self):
        def _dict_2_str(in_dict, print_only_key=False):
            out_str = "("

            for key, val in dict(sorted(in_dict.items())).items():
                if print_only_key:
                    out_str += f"{key}, "
                else:
                    if val:
                        out_str += f"{val*1e6:.3f}, "
                    else:
                        out_str += "?, "

            out_str = out_str.rstrip(', ')
            out_str += ")"
            return out_str

        axis_str = _dict_2_str(self._last_optimal_pos, True) + "= "
        pos_str = _dict_2_str(self._last_optimal_pos)
        sigma_str = _dict_2_str(self._last_optimal_sigma)
        self.pos_ax_label.setText(axis_str)
        self.result_label.setText(pos_str + " µm,  σ= " + sigma_str + " µm")

    def set_image(self, image, axs, extent=None):

        image_item = self._get_widget_part(axs, 'image_2d')

        image_item.set_image(image=image)
        if extent is not None:
            image_item.set_image_extent(extent)

    def get_plot_item(self, axs):
        return self._get_widget_part(axs, 'plot_1d')

    def get_plot_fit_item(self, axs):
        return self._get_widget_part(axs, 'fit_1d')

    def set_plot_data(self, axs, x=None, y=None):

        plot_item = self.get_plot_item(axs)

        if x is None and y is None:
            plot_item.clear()
            return
        elif x is None:
            x = plot_item.xData
            if x is None or len(x) != len(y):
                x = np.arange(len(y))
        elif y is None:
            y = plot_item.yData
            if y is None or len(x) != len(y):
                y = np.zeros(len(x))

        nan_mask = np.isnan(y)
        if nan_mask.all():
            plot_item.clear()
        else:
            plot_item.setData(x=x[~nan_mask], y=y[~nan_mask])
        return

    def set_fit_data(self, axs, x=None, y=None):

        fit_plot_item = self.get_plot_fit_item(axs)

        if x is None and y is None:
            fit_plot_item.clear()
            return
        elif x is None:
            x = fit_plot_item.xData
            if x is None or len(x) != len(y):
                x = np.arange(len(y))
        elif y is None:
            y = fit_plot_item.yData
            if y is None or len(x) != len(y):
                y = np.zeros(len(x))

        fit_plot_item.setData(x=x, y=y)
        return

    def set_image_label(self, axis, axs=None, text=None, units=None):
        if len(axs) != 2:
            raise ValueError(f"For setting a image label, must be a 2d axes, not {axs}")

        widget = self._get_widget_part(axs, 'widget')

        widget.setLabel(axis=axis, text=text, units=units)

    def set_plot_label(self, axis, axs=None, text=None, units=None):
        if len(axs) != 1:
            raise ValueError(f"For setting a image label, must be a 1d axes, not {axs}")

        widget = self._get_widget_part(axs, 'widget')

        widget.setLabel(axis=axis, text=text, units=units)


