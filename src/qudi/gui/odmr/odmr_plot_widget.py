# -*- coding: utf-8 -*-

"""
This file contains a custom QWidget subclass to be used in the ODMR GUI module.

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

__all__ = ('OdmrPlotWidget',)

import pyqtgraph as pg
from PySide2 import QtCore, QtWidgets

from qudi.util.widgets.plotting.image_widget import ImageWidget
from qudi.util.colordefs import QudiPalettePale as palette


class OdmrPlotWidget(QtWidgets.QWidget):
    """
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(main_layout)

        # Create data plot
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.getPlotItem().setContentsMargins(0, 1, 5, 2)
        self._data_item = pg.PlotDataItem(pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
                                          symbol='o',
                                          symbolPen=palette.c1,
                                          symbolBrush=palette.c1,
                                          symbolSize=7)
        self._fit_data_item = pg.PlotDataItem(pen=pg.mkPen(palette.c2))
        self._plot_widget.addItem(self._data_item)
        self._plot_widget.addItem(self._fit_data_item)
        self._plot_widget.setMinimumWidth(100)
        self._plot_widget.setMinimumHeight(100)
        self._plot_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                        QtWidgets.QSizePolicy.Expanding)
        self._plot_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._plot_widget.setLabel('bottom', text='Frequency', units='Hz')
        self._plot_widget.setLabel('left', text='Signal')
        self._plot_widget.showGrid(x=True, y=True)
        main_layout.addWidget(self._plot_widget)

        # Create matrix plot
        self._image_widget = ImageWidget()
        # self._image_widget.getPlotItem().setContentsMargins(0, 1, 5, 2)
        self._image_item = self._image_widget.image_item
        self._image_widget.setMinimumWidth(100)
        self._image_widget.setMinimumHeight(100)
        self._image_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                         QtWidgets.QSizePolicy.Expanding)
        self._image_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        # self._image_widget.setAspectLocked(lock=True, ratio=1.0)
        self._image_widget.set_axis_label('bottom', label='Frequency', unit='Hz')
        self._image_widget.set_axis_label('left', label='Scan Line')
        self._image_widget.set_data_label(label='Signal', unit='P')
        main_layout.addWidget(self._image_widget)

    def set_signal_label(self, channel, unit):
        self._plot_widget.setLabel('left', text=channel, units=unit)
        self._image_widget.set_data_label(label=channel, unit=unit)

    def set_data(self, frequency, image=None, signal=None, fit=None):
        if image is not None:
            self.set_image_data(frequency, image)
        if signal is not None:
            self.set_signal_data(frequency, signal)
        if fit is not None:
            self.set_fit_data(frequency, fit)

    def set_image_data(self, frequency, data):
        if frequency is None or data is None:
            extent = None
        else:
            extent = ((frequency[0], frequency[-1]), (0, data.shape[1]))
        self._image_widget.set_image(data, extent)

    def set_signal_data(self, frequency, data):
        if data is None:
            self._data_item.clear()
        else:
            self._data_item.setData(y=data, x=frequency)

    def set_fit_data(self, frequency, data):
        if data is None:
            self._fit_data_item.clear()
        else:
            self._fit_data_item.setData(y=data, x=frequency)
