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
import numpy as np
from typing import Tuple, Union, Sequence
from PySide2 import QtCore, QtWidgets, QtGui
from typing import Optional, List
from qudi.util.widgets.plotting.plot_widget import RubberbandZoomSelectionPlotWidget
from qudi.util.widgets.plotting.image_widget import RubberbandZoomSelectionImageWidget
from qudi.util.widgets.plotting.plot_item import XYPlotItem
from qudi.util.widgets.plotting.interactive_curve import CursorPositionLabel
from qudi.util.paths import get_artwork_dir
from qudi.interface.scanning_probe_interface import ScanData, ScannerAxis, ScannerChannel


class _BaseScanWidget(QtWidgets.QWidget):
    """ Base Widget to interactively display multichannel 1D or 2D scan data as well as
    toggling and saving scans.

    WARNING: Do NOT use this widget directly
    """

    def __init__(self,
                 channels: Sequence[ScannerChannel],
                 parent: Optional[QtWidgets.QWidget] = None
                 ) -> None:
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
        self.channel_selection_combobox.addItems([ch.name for ch in channels])
        self.channel_selection_combobox.setMinimumContentsLength(15)
        self.channel_selection_combobox.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToContentsOnFirstShow
        )

        layout.addWidget(self.toggle_scan_button, 0, 0)
        layout.addWidget(self.save_scan_button, 0, 1)
        layout.addWidget(self.channel_selection_label, 0, 2)
        layout.addWidget(self.channel_selection_combobox, 0, 3)
        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)

        self._scan_data = None


class Scan1DWidget(_BaseScanWidget):
    """ Widget to interactively display multichannel 1D scan data as well as toggling and saving
    scans.
    """
    sigMarkerPositionChanged = QtCore.Signal(float)
    sigZoomAreaSelected = QtCore.Signal(tuple)

    def __init__(self,
                 axes: Tuple[ScannerAxis],
                 channels: Sequence[ScannerChannel],
                 parent: Optional[QtWidgets.QWidget] = None
                 ) -> None:
        super().__init__(channels, parent=parent)

        self.plot_item = XYPlotItem([-0.5, 0.5], [0, 0])
        self.plot_widget = RubberbandZoomSelectionPlotWidget(allow_tracking_outside_data=True)
        self.plot_widget.addItem(self.plot_item)
        self.plot_widget.set_selection_mutable(True)
        self.plot_widget.add_marker_selection(position=(0, 0),
                                              mode=self.plot_widget.SelectionMode.X)
        self.plot_widget.sigMarkerSelectionChanged.connect(self._markers_changed)
        self.channel_selection_combobox.currentIndexChanged.connect(self._data_channel_changed)
        self.plot_widget.sigZoomAreaApplied.connect(self._zoom_applied)
        self.plot_widget.setLabel('bottom', text=axes[0].name.title(), units=axes[0].unit)
        self.plot_widget.setLabel('left', text=channels[0].name, units=channels[0].unit)

        self.layout().addWidget(self.plot_widget, 1, 0, 1, 4)

        # disable buggy pyqtgraph 'Export..' context menu
        self.plot_widget.getPlotItem().vb.scene().contextMenu[0].setVisible(False)

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

    @property
    def marker_bounds(self) -> Union[None, List[Union[None, Tuple[float, float]]]]:
        return self.plot_widget.selection_bounds

    def set_marker_bounds(self,
                          bounds: Union[None, List[Union[None, Tuple[float, float]]]]
                          ) -> None:
        self.plot_widget.set_selection_bounds(bounds)

    def set_plot_range(self,
                       x_range: Optional[Tuple[float, float]] = None,
                       y_range: Optional[Tuple[float, float]] = None
                       ) -> None:
        self.plot_widget.setRange(xRange=x_range, yRange=y_range)

    def set_scan_data(self, data: ScanData) -> None:
        # Save reference for channel changes
        update_range = (self._scan_data is None) or (self._scan_data.scan_range != data.scan_range) \
                        or (self._scan_data.scan_resolution != data.scan_resolution)
        self._scan_data = data
        # Set data
        self._update_scan_data(update_range=update_range)

    @QtCore.Slot(dict)
    def _markers_changed(self, markers) -> None:
        position = markers[self.plot_widget.SelectionMode.X][0]
        self.sigMarkerPositionChanged.emit(position)

    @QtCore.Slot(QtCore.QRectF)
    def _zoom_applied(self, zoom_area) -> None:
        if self.plot_widget.rubberband_zoom_selection_mode == self.plot_widget.SelectionMode.X:
            x_range = tuple(sorted([zoom_area.left(), zoom_area.right()]))
            self.sigZoomAreaSelected.emit(x_range)

    def _data_channel_changed(self) -> None:
        if self._scan_data is not None:
            current_channel = self.channel_selection_combobox.currentText()
            self.plot_widget.setLabel('left',
                                      text=current_channel,
                                      units=self._scan_data.channel_units[current_channel])
        self._update_scan_data(update_range=False)

    def _update_scan_data(self, update_range: bool) -> None:
        current_channel = self.channel_selection_combobox.currentText()
        if (self._scan_data is None) or (self._scan_data.data is None) \
                or (current_channel not in self._scan_data.channels):
            self.plot_item.clear()
        else:
            if update_range:
                x_data = np.linspace(*self._scan_data.scan_range[0],
                                     self._scan_data.scan_resolution[0])
                self.plot_item.setData(y=self._scan_data.data[current_channel], x=x_data)
            else:
                self.plot_item.setData(y=self._scan_data.data[current_channel],
                                       x=self.plot_item.xData)


class Scan2DWidget(_BaseScanWidget):
    """ Widget to interactively display multichannel 2D scan data as well as toggling and saving
    scans.
    """

    sigMarkerPositionChanged = QtCore.Signal(tuple)
    sigZoomAreaSelected = QtCore.Signal(tuple, tuple)

    def __init__(self,
                 axes: Tuple[ScannerAxis, ScannerAxis],
                 channels: Sequence[ScannerChannel],
                 parent: Optional[QtWidgets.QWidget] = None,
                 xy_region_min_size_percentile: Optional[float] = None,
                 max_mouse_pos_update_rate: Optional[float] = 20.
                 ) -> None:
        super().__init__(channels, parent=parent)
         
        self.position_label = CursorPositionLabel()
        self.position_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        self.position_label.set_units(axes[0].unit, axes[1].unit)
        self.image_widget = RubberbandZoomSelectionImageWidget(
            allow_tracking_outside_data=True,
            max_mouse_pos_update_rate=max_mouse_pos_update_rate,
            xy_region_selection_crosshair=True,
            xy_region_selection_handles=False,
            xy_region_min_size_percentile=xy_region_min_size_percentile
        )
        self.image_widget.sigMouseMoved.connect(self.position_label.update_position)
        self.image_widget.set_selection_mutable(True)
        self.image_widget.add_region_selection(span=((-0.5, 0.5), (-0.5, 0.5)),
                                               mode=self.image_widget.SelectionMode.XY)
        self.image_item = self.image_widget.image_item
        self.image_widget.sigRegionSelectionChanged.connect(self._region_changed)
        self.channel_selection_combobox.currentIndexChanged.connect(self._data_channel_changed)
        self.image_widget.sigZoomAreaApplied.connect(self._zoom_applied)
        self.image_widget.set_axis_label('bottom', label=axes[0].name.title(), unit=axes[0].unit)
        self.image_widget.set_axis_label('left', label=axes[1].name.title(), unit=axes[1].unit)
        self.image_widget.set_data_label(label=channels[0].name, unit=channels[0].unit)

        self.layout().addWidget(self.image_widget, 1, 0, 1, 4)
        self.layout().addWidget(self.position_label, 2, 0, 1, 4)

        # disable buggy pyqtgraph 'Export..' context menu
        self.image_widget.plot_widget.getPlotItem().vb.scene().contextMenu[0].setVisible(False)

    @property
    def marker_position(self) -> Tuple[float, float]:
        return self.image_widget.region_selection[self.image_widget.SelectionMode.XY][0][0]

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
        return self.image_widget.region_selection[self.image_widget.SelectionMode.XY][0][1]

    def set_marker_size(self, size: Tuple[float, float]) -> None:
        position = self.marker_position
        x_min = position[0] - size[0] / 2
        x_max = position[0] + size[0] / 2
        y_min = position[1] - size[1] / 2
        y_max = position[1] + size[1] / 2
        self.image_widget.move_region_selection(((x_min, x_max), (y_min, y_max)), 0)

    @property
    def marker_bounds(self) -> Union[None, List[Union[None, Tuple[float, float]]]]:
        return self.image_widget.selection_bounds

    def set_marker_bounds(self,
                          bounds: Union[None, List[Union[None, Tuple[float, float]]]]
                          ) -> None:
        self.image_widget.set_selection_bounds(bounds)

    def set_plot_range(self,
                       x_range: Optional[Tuple[float, float]] = None,
                       y_range: Optional[Tuple[float, float]] = None
                       ) -> None:
        vb = self.image_item.getViewBox()
        vb.setRange(xRange=x_range, yRange=y_range)

    def set_scan_data(self, data: ScanData) -> None:
        # Save reference for channel changes
        self._scan_data = data
        # Set data
        self._update_scan_data()

    @QtCore.Slot(dict)
    def _region_changed(self, regions) -> None:
        center = regions[self.image_widget.SelectionMode.XY][0][0]
        self.sigMarkerPositionChanged.emit(center)

    @QtCore.Slot(QtCore.QRectF)
    def _zoom_applied(self, zoom_area) -> None:
        if self.image_widget.rubberband_zoom_selection_mode == self.image_widget.SelectionMode.XY:
            x_range = tuple(sorted([zoom_area.left(), zoom_area.right()]))
            y_range = tuple(sorted([zoom_area.top(), zoom_area.bottom()]))
            self.sigZoomAreaSelected.emit(x_range, y_range)

    def _data_channel_changed(self) -> None:
        if self._scan_data is not None:
            current_channel = self.channel_selection_combobox.currentText()
            self.image_widget.set_data_label(label=current_channel,
                                             unit=self._scan_data.channel_units[current_channel])
        self._update_scan_data()

    def _update_scan_data(self) -> None:
        current_channel = self.channel_selection_combobox.currentText()
        if (self._scan_data is None) or (self._scan_data.data is None) \
            or (current_channel not in self._scan_data.channels):
            self.image_widget.set_image(None)
        else:
            self.image_widget.set_image(self._scan_data.data[current_channel])
            self.image_widget.set_image_extent(self._scan_data.scan_range,
                                               adjust_for_px_size=True)
            self.image_widget.autoRange()
