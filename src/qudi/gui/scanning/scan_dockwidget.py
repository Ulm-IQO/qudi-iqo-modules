# -*- coding: utf-8 -*-

"""
This file contains a QDockWidget subclass to display a scanning measurement for given axes.

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

__all__ = ['ScanDockWidget']

from PySide2 import QtWidgets
from typing import Optional, Tuple, Sequence, Union
from qudi.gui.scanning.scan_widget import Scan2DWidget, Scan1DWidget
from qudi.interface.scanning_probe_interface import ScannerAxis, ScannerChannel


class ScanDockWidget(QtWidgets.QDockWidget):
    """
    """

    def __init__(self,
                 axes: Union[Tuple[ScannerAxis], Tuple[ScannerAxis, ScannerAxis]],
                 channels: Sequence[ScannerChannel],
                 parent: Optional[QtWidgets.QWidget] = None,
                 xy_region_min_size_percentile: Optional[float] = None
                 ) -> None:
        try:
            x_axis, y_axis = axes
            title = f'{x_axis.name.title()}-{y_axis.name.title()} Scan'
            self._scan_axes = tuple(axes)
        except ValueError:
            x_axis = axes[0]
            title = f'{x_axis.name.title()} Scan'
            self._scan_axes = (x_axis,)

        super().__init__(title, parent=parent)
        self.setObjectName(f'{"_".join(ax.name for ax in self._scan_axes)}_scan_dockWidget')

        if len(self._scan_axes) == 1:
            self.scan_widget = Scan1DWidget(self._scan_axes, channels)
        else:
            self.scan_widget = Scan2DWidget(self._scan_axes, channels,
                                            xy_region_min_size_percentile=xy_region_min_size_percentile)
        self.setWidget(self.scan_widget)

    @property
    def scan_axes(self):
        return self._scan_axes
