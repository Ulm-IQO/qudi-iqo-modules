# -*- coding: utf-8 -*-

"""
This file contains a specialized widget to display interactive scan data.

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

__all__ = ['Scan1DWidget', 'Scan2DWidget']

import os
from typing import Tuple, Union, Sequence
from PySide2 import QtCore, QtWidgets, QtGui
from typing import Optional, Any
from qudi.util.widgets.plotting.plot_widget import RubberbandZoomSelectionPlotWidget
from qudi.util.widgets.plotting.image_widget import RubberbandZoomSelectionImageWidget
from qudi.util.widgets.plotting.plot_item import XYPlotItem
from qudi.util.paths import get_artwork_dir
from qudi.interface.scanning_probe_interface import ScanData


class _BaseScanWidget(QtWidgets.QWidget):
    """ Base Widget to interactively display multichannel 1D or 2D scan data as well as
    toggling and saving scans.

    WARNING: Do NOT use this widget directly
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent=parent)

        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        scan_icon = QtGui.QIcon()
        scan_icon.addFile(os.path.join(get_artwork_dir(), 'icons', 'start-counter.svg'),
                          QtCore.QSize(),
                          QtGui.QIcon.Normal,
                          QtGui.QIcon.Off)
        scan_icon.addFile(os.path.join(get_artwork_dir(), 'icons', 'stop-counter.svg'),
                          QtCore.QSize(),
                          QtGui.QIcon.Normal,
                          QtGui.QIcon.On)
        self.toggle_scan_button = QtWidgets.QPushButton(scan_icon, 'Toggle Scan')
        self.toggle_scan_button.setCheckable(True)

        save_icon = QtGui.QIcon(os.path.join(get_artwork_dir(), 'icons', 'document-save.svg'))
        self.save_scan_button = QtWidgets.QPushButton(save_icon, '')
        self.save_scan_button.setCheckable(False)

        self.channel_selection_label = QtWidgets.QLabel('Channel:')
        self.channel_selection_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.channel_selection_combobox = QtWidgets.QComboBox()
        self.channel_selection_combobox.setMinimumContentsLength(15)
        self.channel_selection_combobox.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)

        layout.addWidget(self.toggle_scan_button, 0, 0)
        layout.addWidget(self.save_scan_button, 0, 1)
        layout.addWidget(self.channel_selection_label, 0, 2)
        layout.addWidget(self.channel_selection_combobox, 0, 3)
        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)

        self._scan_data = None

    def _set_available_channels(self, channels: Sequence[str]) -> None:
        current_channel = self.channel_selection_combobox.currentText()
        self.channel_selection_combobox.blockSignals(True)
        self.channel_selection_combobox.clear()
        self.channel_selection_combobox.addItems(channels)
        if current_channel and (current_channel in channels):
            self.channel_selection_combobox.setCurrentText(current_channel)
        self.channel_selection_combobox.blockSignals(False)


class Scan1DWidget(_BaseScanWidget):
    """ Widget to interactively display multichannel 1D scan data as well as toggling and saving
    scans.
    """
    sigMarkerPositionChanged = QtCore.Signal(float)
    sigZoomAreaSelected = QtCore.Signal(tuple)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent=parent)

        self.plot_item = XYPlotItem([-0.5, 0.5], [0, 0])
        self.plot_widget = RubberbandZoomSelectionPlotWidget(allow_tracking_outside_data=True,
                                                             emit_while_dragging=True)
        self.plot_widget.addItem(self.plot_item)
        self.plot_widget.set_selection_mutable(True)
        self.plot_widget.add_marker_selection(position=(0, 0),
                                              mode=self.plot_widget.SelectionMode.X)
        self.plot_widget.sigMarkerSelectionChanged.connect(self._markers_changed)
        self.channel_selection_combobox.currentIndexChanged.connect(self._update_scan_data)
        self.plot_widget.sigMouseDragged.connect(self._mouse_dragged)

        self.layout().addWidget(self.plot_widget, 1, 0, 1, 4)

    @property
    def marker_position(self) -> float:
        return self.plot_widget.marker_selection[self.plot_widget.SelectionMode.X][0]

    def set_marker_position(self, position: float) -> None:
        self.plot_widget.move_marker_selection((position, 0), 0)

    def toggle_zoom(self, enable: bool) -> None:
        if enable:
            mode = self.plot_widget.SelectionMode.X
        else:
            mode = self.plot_widget.SelectionMode.Disabled
        self.plot_widget.set_rubberband_zoom_selection_mode(mode)

    def toggle_marker(self, show: bool) -> None:
        if show:
            self.plot_widget.show_marker_selections()
        else:
            self.plot_widget.hide_marker_selections()

    def set_scan_data(self, data: ScanData) -> None:
        # Set axis label
        scan_axis = data.scan_axes[0]
        axis_unit = data.axes_units[scan_axis]
        self.plot_widget.setLabel('bottom', text=scan_axis.title(), units=axis_unit)
        # Set channels
        self._set_available_channels(data.channels)
        # Save reference for channel changes
        self._scan_data = data
        # Set data
        self._update_scan_data()

    @QtCore.Slot(dict)
    def _markers_changed(self, markers) -> None:
        position = markers[self.plot_widget.SelectionMode.X][0]
        self.sigMarkerPositionChanged.emit(position)

    @QtCore.Slot(tuple, tuple, object)
    def _mouse_dragged(self, start_position, current_position, event) -> None:
        if self.plot_widget.rubberband_zoom_selection_mode == self.plot_widget.SelectionMode.X:
            if event.isFinish():
                self.sigZoomAreaSelected.emit(
                    tuple(sorted([start_position[0], current_position[0]]))
                )

    def _update_scan_data(self) -> None:
        if (self._scan_data is None) or (self._scan_data.data is None):
            self.plot_item.clear()
        else:
            current_channel = self.channel_selection_combobox.currentText()
            self.plot_item.setData(self._scan_data.data[current_channel])
            self.plot_widget.setLabel('left',
                                      text=current_channel,
                                      units=self._scan_data.channel_units[current_channel])


class Scan2DWidget(_BaseScanWidget):
    """ Widget to interactively display multichannel 2D scan data as well as toggling and saving
    scans.
    """

    sigMarkerPositionChanged = QtCore.Signal(tuple)
    sigZoomAreaSelected = QtCore.Signal(tuple, tuple)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent=parent)

        self.image_widget = RubberbandZoomSelectionImageWidget(allow_tracking_outside_data=True,
                                                               xy_region_selection_crosshair=True,
                                                               xy_region_selection_handles=False,
                                                               emit_while_dragging=True)
        self.image_widget.set_selection_mutable(True)
        self.image_widget.add_region_selection(span=((-0.5, 0.5), (-0.5, 0.5)),
                                               mode=self.image_widget.SelectionMode.XY)
        self.image_item = self.image_widget.image_item
        self.image_widget.sigRegionSelectionChanged.connect(self._region_changed)
        self.channel_selection_combobox.currentIndexChanged.connect(self._update_scan_data)
        self.image_widget.sigMouseDragged.connect(self._mouse_dragged)

        self.layout().addWidget(self.image_widget, 1, 0, 1, 4)

    @property
    def marker_position(self) -> Tuple[float, float]:
        center = self.image_widget.region_selection[self.image_widget.SelectionMode.XY][0].center()
        return center.x(), center.y()

    def set_marker_position(self, position: Tuple[float, float]) -> None:
        size = self.marker_size
        x_min = position[0] - size[0] / 2
        x_max = position[0] + size[0] / 2
        y_min = position[1] - size[1] / 2
        y_max = position[1] + size[1] / 2
        self.image_widget.move_region_selection(((x_min, x_max), (y_min, y_max)), 0)

    def toggle_zoom(self, enable: bool) -> None:
        if enable:
            mode = self.image_widget.SelectionMode.XY
        else:
            mode = self.image_widget.SelectionMode.Disabled
        self.image_widget.set_rubberband_zoom_selection_mode(mode)

    def toggle_marker(self, show: bool) -> None:
        if show:
            self.image_widget.show_region_selections()
        else:
            self.image_widget.hide_region_selections()

    @property
    def marker_size(self) -> Tuple[float, float]:
        rect = self.image_widget.region_selection[self.image_widget.SelectionMode.XY][0]
        return abs(rect.width()), abs(rect.height())

    def set_marker_size(self, size: Tuple[float, float]) -> None:
        position = self.marker_position
        x_min = position[0] - size[0] / 2
        x_max = position[0] + size[0] / 2
        y_min = position[1] - size[1] / 2
        y_max = position[1] + size[1] / 2
        self.image_widget.move_region_selection(((x_min, x_max), (y_min, y_max)), 0)

    def set_scan_data(self, data: ScanData) -> None:
        # Set axes labels
        scan_axes = data.scan_axes
        axes_units = data.axes_units
        self.image_widget.set_axis_label('bottom',
                                         label=scan_axes[0].title(),
                                         unit=axes_units[scan_axes[0]])
        self.image_widget.set_axis_label('left',
                                         label=scan_axes[1].title(),
                                         unit=axes_units[scan_axes[1]])
        # Set channels
        self._set_available_channels(data.channels)
        # Save reference for channel changes
        self._scan_data = data
        # Set data
        self._update_scan_data()

    @QtCore.Slot(dict)
    def _region_changed(self, regions) -> None:
        center = regions[self.image_widget.SelectionMode.XY][0].center()
        self.sigMarkerPositionChanged.emit((center.x(), center.y()))

    @QtCore.Slot(tuple, tuple, object)
    def _mouse_dragged(self, start_position, current_position, event) -> None:
        if self.image_widget.rubberband_zoom_selection_mode == self.image_widget.SelectionMode.XY:
            if event.isFinish():
                self.sigZoomAreaSelected.emit(
                    tuple(sorted([start_position[0], current_position[0]])),
                    tuple(sorted([start_position[1], current_position[1]]))
                )

    def _update_scan_data(self) -> None:
        if (self._scan_data is None) or (self._scan_data.data is None):
            self.image_widget.set_image(None)
        else:
            current_channel = self.channel_selection_combobox.currentText()
            self.image_widget.set_image(self._scan_data.data[current_channel])
            self.image_widget.set_image_extent(self._scan_data.scan_range, adjust_for_px_size=True)
            self.image_widget.set_data_label(label=current_channel,
                                             unit=self._scan_data.channel_units[current_channel])
            self.image_widget.autoRange()
