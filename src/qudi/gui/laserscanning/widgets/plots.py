# -*- coding: utf-8 -*-
"""
Contains widgets for plotting data in the laser scanning toolchain GUI.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['HistogramPlotWidget', 'ScatterPlotWidget', 'ScatterPlotDockWidget']

import numpy as np
from pyqtgraph import mkPen
from PySide2 import QtWidgets
from typing import Optional, Tuple

from qudi.util.colordefs import QudiPalette
from qudi.util.widgets.plotting.interactive_curve import InteractiveCurvesWidget


class HistogramPlotWidget(InteractiveCurvesWidget):
    """ InteractiveCurvesWidget specialization for histogram in laser scanning toolchain GUI """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.add_marker_selection(position=(0, 0), mode=self.SelectionMode.X)
        self.add_region_selection(span=[(0, 0), (0, 0)], mode=self.SelectionMode.X)
        self.set_selection_mutable(False)
        self.toggle_plot_editor(False)

        self.plot(name='Data', pen=None, symbol='o')
        self.plot(name='Histogram', pen=mkPen(QudiPalette.c2))
        self.plot(name='Envelope', pen=mkPen(QudiPalette.c1))
        self.plot_fit(name='Histogram', pen='r')
        self.set_plot_selection({'Data': True, 'Histogram': True, 'Envelope': False})

    def update_fit(self, x: Optional[np.ndarray] = None, y: Optional[np.ndarray] = None) -> None:
        self.set_fit_data('Histogram', x=x, y=y)

    def update_data(self, x: Optional[np.ndarray] = None, y: Optional[np.ndarray] = None) -> None:
        self.set_data('Data', x=x, y=y)

    def update_histogram(self,
                         x: Optional[np.ndarray] = None,
                         y: Optional[np.ndarray] = None) -> None:
        self.set_data('Histogram', x=x, y=y)

    def update_envelope(self,
                        x: Optional[np.ndarray] = None,
                        y: Optional[np.ndarray] = None) -> None:
        self.set_data('Envelope', x=x, y=y)

    def update_marker(self, value: float) -> None:
        self.move_marker_selection((value, 0), 0)

    def update_region(self, span: Tuple[float, float]) -> None:
        self.move_region_selection(span=[span, (0, 0)], index=0)


class ScatterPlotWidget(InteractiveCurvesWidget):
    """ InteractiveCurvesWidget specialization for scan data in laser scanning toolchain GUI """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.toggle_plot_editor(False)
        self.toggle_plot_selector(False)

        self.plot('Data', pen=None, symbol='o')

    def update_data(self, x: Optional[np.ndarray] = None, y: Optional[np.ndarray] = None) -> None:
        self.set_data('Data', x=x, y=y)


class ScatterPlotDockWidget(QtWidgets.QDockWidget):
    """ A QDockWidget for LaserScanSettingsWidget """

    scatter_plot: ScatterPlotWidget

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.scatter_plot = ScatterPlotWidget()
        self.setWidget(self.scatter_plot)
