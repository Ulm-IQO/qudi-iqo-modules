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

__all__ = ['Scan1DDockWidget', 'Scan2DDockWidget']

from PySide2 import QtGui, QtWidgets
from qudi.util.widgets.scan_2d_widget import Scan2DWidget
from qudi.util.widgets.scan_1d_widget import Scan1DWidget


class Scan2DDockWidget(QtWidgets.QDockWidget):
    """
    """

    __transparent_crosshair_attrs = frozenset(
        {'sigPositionChanged', 'sigPositionDragged', 'sigDragStarted', 'sigDragFinished'}
    )
    __transparent_widget_attrs = frozenset(
        {'sigMouseClicked', 'sigMouseAreaSelected', 'sigScanToggled', 'selection_enabled',
         'zoom_by_selection_enabled', 'toggle_selection', 'toggle_zoom_by_selection',
         'set_image_extent', 'toggle_scan', 'toggle_enabled', 'set_scan_data'}
    )

    def __init__(self, *args, scan_axes, channels, **kwargs):
        x_axis, y_axis = scan_axes

        super().__init__(*args, **kwargs)

        self._axes = (x_axis.name, y_axis.name)

        self.setWindowTitle('{0}-{1} Scan'.format(x_axis.name.title(), y_axis.name.title()))
        self.setObjectName('{0}_{1}_scan_dockWidget'.format(x_axis.name, y_axis.name))

        icon = QtGui.QIcon(':/icons/scan-xy-start')
        icon.addFile(':/icons/scan-stop', mode=QtGui.QIcon.Normal, state=QtGui.QIcon.On)
        self.scan_widget = Scan2DWidget(channel_units={ch.name: ch.unit for ch in channels},
                                        scan_icon=icon)
        self.scan_widget.set_axis_label('bottom', label=x_axis.name.title(), unit=x_axis.unit)
        self.scan_widget.set_axis_label('left', label=y_axis.name.title(), unit=y_axis.unit)
        self.scan_widget.set_data_channels({ch.name: ch.unit for ch in channels})
        self.scan_widget.add_crosshair(movable=True, min_size_factor=0.02)
        self.scan_widget.crosshairs[-1].set_allowed_range((x_axis.value_range, y_axis.value_range))
        self.scan_widget.crosshairs[-1].set_size((0.1, 0.1))
        self.scan_widget.toggle_zoom_by_selection(True)

        self.setWidget(self.scan_widget)

    def __getattr__(self, item):
        if item in self.__transparent_crosshair_attrs:
            return getattr(self.scan_widget.crosshairs[-1], item)
        if item in self.__transparent_widget_attrs:
            return getattr(self.scan_widget, item)
        raise AttributeError('Scan2DDockWidget has no attribute "{0}"'.format(item))

    @property
    def axes(self):
        return self._axes

    @property
    def crosshair(self):
        return self.scan_widget.crosshairs[-1]

    def toggle_crosshair(self, enabled):
        if enabled:
            return self.scan_widget.show_crosshair(-1)
        return self.scan_widget.hide_crosshair(-1)


class Scan1DDockWidget(QtWidgets.QDockWidget):
    """
    """

    __transparent_marker_attrs = frozenset(
        {'sigPositionChanged', 'sigPositionDragged', 'sigDragStarted', 'sigDragFinished'}
    )
    __transparent_widget_attrs = frozenset(
        {'sigMouseClicked', 'sigMouseAreaSelected', 'sigScanToggled', 'selection_enabled',
         'zoom_by_selection_enabled', 'toggle_selection', 'toggle_zoom_by_selection', 'toggle_scan',
         'toggle_enabled', 'set_scan_data'}
    )

    def __init__(self, *args, scan_axis, channels, **kwargs):
        super().__init__(*args, **kwargs)

        self._axis = scan_axis.name

        self.setWindowTitle('{0} Scan'.format(scan_axis.name.title()))
        self.setObjectName('{0}_scan_dockWidget'.format(scan_axis.name))

        icon = QtGui.QIcon(':/icons/scan-xy-start')
        icon.addFile(':/icons/scan-stop', mode=QtGui.QIcon.Normal, state=QtGui.QIcon.On)
        self.scan_widget = Scan1DWidget(channel_units={ch.name: ch.unit for ch in channels},
                                        scan_icon=icon)
        self.scan_widget.set_axis_label(scan_axis.name.title(), scan_axis.unit)
        self.scan_widget.set_data_channels({ch.name: ch.unit for ch in channels})
        self.scan_widget.add_marker(movable=True)
        self.scan_widget.markers[-1].set_allowed_range(scan_axis.value_range)
        self.scan_widget.toggle_zoom_by_selection(True)

        self.setWidget(self.scan_widget)

    def __getattr__(self, item):
        if item in self.__transparent_marker_attrs:
            return getattr(self.scan_widget.markers[-1], item)
        if item in self.__transparent_widget_attrs:
            return getattr(self.scan_widget, item)
        raise AttributeError('Scan1DDockWidget has no attribute "{0}"'.format(item))

    @property
    def axis(self):
        return self._axis

    @property
    def marker(self):
        return self.scan_widget.markers[-1]

    def toggle_marker(self, enabled):
        if enabled:
            return self.scan_widget.show_marker(-1)
        return self.scan_widget.hide_marker(-1)
