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
from typing import Optional
from qudi.gui.scanning.scan_widget import Scan2DWidget, Scan1DWidget


class ScanDockWidget(QtWidgets.QDockWidget):
    """
    """

    def __init__(self, scan_axes, parent: Optional[QtWidgets.QWidget] = None):
        try:
            x_axis, y_axis = scan_axes
            title = f'{x_axis.name.title()}-{y_axis.name.title()} Scan'
            self._scan_axes = tuple(scan_axes)
        except ValueError:
            x_axis = scan_axes[0]
            title = f'{x_axis.name.title()} Scan'
            self._scan_axes = (x_axis,)

        super().__init__(title, parent=parent)
        self.setObjectName(
            '{0}_scan_dockWidget'.format('_'.join(ax.name for ax in self._scan_axes))
        )

        if len(self._scan_axes) == 1:
            self.scan_widget = Scan1DWidget()
            self.scan_widget.plot_widget.setLabel('bottom',
                                                  text=x_axis.name.title(),
                                                  units=x_axis.unit)
        else:
            self.scan_widget = Scan2DWidget()
            self.scan_widget.image_widget.set_axis_label('bottom',
                                                         label=x_axis.name.title(),
                                                         unit=x_axis.unit)
            self.scan_widget.image_widget.set_axis_label('left',
                                                         label=y_axis.name.title(),
                                                         unit=y_axis.unit)
        self.setWidget(self.scan_widget)

    @property
    def scan_axes(self):
        return self._scan_axes
