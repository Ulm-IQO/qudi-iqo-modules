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
from qudi.util.widgets.scan_2d_widget import Scan2DPlotWidget, ScanImageItem
from qudi.util.widgets.scan_1d_widget import Scan1DPlotWidget
from qudi.util.colordefs import QudiPalette


class OptimizerDockWidget(QtWidgets.QDockWidget):
    """
    """

    def __init__(self, axes, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('Optimizer')
        self.setObjectName('optimizer_dockWidget')

        self.image_item = ScanImageItem()
        self.plot2d_widget = Scan2DPlotWidget()
        self.plot2d_widget.addItem(self.image_item)
        self.plot2d_widget.toggle_zoom_by_selection(False)
        self.plot2d_widget.toggle_selection(False)
        self.plot2d_widget.add_crosshair(movable=False, pen={'color': '#00ff00', 'width': 2})
        self.plot2d_widget.setAspectLocked(lock=True, ratio=1.0)
        self.plot2d_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.plot_item = PlotDataItem(pen=mkPen(QudiPalette.c1, style=QtCore.Qt.DotLine),
                                      symbol='o',
                                      symbolPen=QudiPalette.c1,
                                      symbolBrush=QudiPalette.c1,
                                      symbolSize=7)
        self.fit_plot_item = PlotDataItem(pen=mkPen(QudiPalette.c2))
        self.plot1d_widget = Scan1DPlotWidget()
        self.plot1d_widget.addItem(self.plot_item)
        self.plot1d_widget.add_marker(movable=False, pen={'color': '#00ff00', 'width': 2})
        self.plot1d_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self._last_optimal_pos = {}
        self._last_optimal_sigma = {}

        self.pos_ax_label = QtWidgets.QLabel(f'({", ".join(axes)}):')
        self.pos_ax_label.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.result_label = QtWidgets.QLabel(f'({", ".join(["?"]*len(axes))}):')
        self.result_label.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        label_layout = QtWidgets.QHBoxLayout()
        label_layout.addWidget(self.pos_ax_label)
        label_layout.addWidget(self.result_label)
        label_layout.setStretch(1, 1)

        layout = QtWidgets.QGridLayout()
        layout.addWidget(self.plot2d_widget, 0, 0)
        layout.addWidget(self.plot1d_widget, 0, 1)
        layout.addLayout(label_layout, 1, 0, 1, 2)
        layout.setRowStretch(0, 1)
        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        self.setWidget(widget)

    @property
    def crosshair(self):
        return self.plot2d_widget.crosshairs[-1]

    @property
    def marker(self):
        return self.plot1d_widget.markers[-1]

    def toogle_crosshair(self, enabled):
        if enabled:
            return self.plot2d_widget.show_crosshair(-1)
        return self.plot2d_widget.hide_crosshair(-1)

    def toogle_marker(self, enabled):
        if enabled:
            return self.plot1d_widget.show_marker(-1)
        return self.plot1d_widget.hide_marker(-1)

    def set_2d_position(self, pos, axs, sigma=None):
        self.crosshair.set_position(pos)

        self._last_optimal_pos[axs[0]] = pos[0]
        self._last_optimal_pos[axs[1]] = pos[1]
        if sigma:
            self._last_optimal_sigma[axs[0]] = sigma[0]
            self._last_optimal_sigma[axs[1]] = sigma[1]

        self.update_result_label()

    def set_1d_position(self, pos, axs, sigma=None):
        self.marker.set_position(pos)

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
                        out_str += f"{val*1e6:.2f}, "
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

    def set_image(self, image, extent=None):
        self.image_item.set_image(image=image)
        if extent is not None:
            self.image_item.set_image_extent(extent)

    def set_plot_data(self, x=None, y=None):
        if x is None and y is None:
            self.plot_item.clear()
            return
        elif x is None:
            x = self.plot_item.xData
            if x is None or len(x) != len(y):
                x = np.arange(len(y))
        elif y is None:
            y = self.plot_item.yData
            if y is None or len(x) != len(y):
                y = np.zeros(len(x))

        nan_mask = np.isnan(y)
        if nan_mask.all():
            self.plot_item.clear()
        else:
            self.plot_item.setData(x=x[~nan_mask], y=y[~nan_mask])
        return

    def set_fit_data(self, x=None, y=None):
        if x is None and y is None:
            self.fit_plot_item.clear()
            return
        elif x is None:
            x = self.fit_plot_item.xData
            if x is None or len(x) != len(y):
                x = np.arange(len(y))
        elif y is None:
            y = self.fit_plot_item.yData
            if y is None or len(x) != len(y):
                y = np.zeros(len(x))

        self.fit_plot_item.setData(x=x, y=y)
        return

    def set_image_label(self, axis, text=None, units=None):
        self.plot2d_widget.setLabel(axis=axis, text=text, units=units)

    def set_plot_label(self, axis, text=None, units=None):
        self.plot1d_widget.setLabel(axis=axis, text=text, units=units)


