# -*- coding: utf-8 -*-

"""
This file contains a custom QDockWidget subclass to be used in the QD Plot GUI module.

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

__all__ = ['QDPlotDockWidget', 'QDPlotWidget', 'QDPlotControlWidget']

import os
from PySide2 import QtWidgets, QtCore, QtGui
from typing import Tuple, Dict, Union, List

from qudi.util.paths import get_artwork_dir
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.util.widgets.fitting import FitWidget
from qudi.util.widgets.plotting.interactive_curve import InteractiveCurvesWidget


class QDPlotControlWidget(QtWidgets.QWidget):
    """
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        icons_path = os.path.join(get_artwork_dir(), 'icons')

        self.save_button = QtWidgets.QPushButton(
            icon=QtGui.QIcon(os.path.join(icons_path, 'document-save')),
            text='Save'
        )
        self.remove_button = QtWidgets.QPushButton(
            icon=QtGui.QIcon(os.path.join(icons_path, 'edit-delete')),
            text='Remove'
        )
        self.show_editor_checkbox = QtWidgets.QCheckBox('Editor')
        self.show_fit_checkbox = QtWidgets.QCheckBox('Fit')
        self.show_selector_checkbox = QtWidgets.QCheckBox('Selector')
        self.track_mouse_checkbox = QtWidgets.QCheckBox('Track Cursor')
        self.x_zoom_checkbox = QtWidgets.QCheckBox('X Zoom')
        self.y_zoom_checkbox = QtWidgets.QCheckBox('Y Zoom')

        show_layout = QtWidgets.QVBoxLayout()
        show_layout.addWidget(self.show_editor_checkbox)
        show_layout.addWidget(self.show_fit_checkbox)
        show_layout.addWidget(self.show_selector_checkbox)
        show_layout.addWidget(self.track_mouse_checkbox)
        show_layout.addStretch()

        zoom_layout = QtWidgets.QVBoxLayout()
        zoom_layout.addWidget(self.x_zoom_checkbox)
        zoom_layout.addWidget(self.y_zoom_checkbox)
        zoom_layout.addStretch()

        button_layout = QtWidgets.QVBoxLayout()
        button_layout.addWidget(self.save_button)
        button_layout.addStretch()
        button_layout.addWidget(self.remove_button)

        layout = QtWidgets.QHBoxLayout()
        layout.addLayout(button_layout)
        layout.addStretch()
        layout.addLayout(zoom_layout)
        layout.addStretch()
        layout.addLayout(show_layout)
        self.setLayout(layout)


class QDPlotWidget(QtWidgets.QWidget):
    """
    """

    sigFitClicked = QtCore.Signal(str)               # fit_function_name
    sigSaveClicked = QtCore.Signal()
    sigRemoveClicked = QtCore.Signal()

    SelectionMode = InteractiveCurvesWidget.SelectionMode

    def __init__(self, *args, fit_container=None, show_fit: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        self.curve_widget = InteractiveCurvesWidget()
        self.fit_widget = FitWidget(fit_container=fit_container)
        self.control_widget = QDPlotControlWidget()
        self.curve_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.control_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.fit_widget.layout().setContentsMargins(0, 0, 0, 0)

        row = 0
        layout.addWidget(self.control_widget, row, 0, 1, 2)
        row += 1
        layout.addWidget(self.curve_widget, row, 0)
        layout.addWidget(self.fit_widget, row, 1)
        row += 1
        layout.setColumnStretch(0, 1)

        self._show_fit = show_fit

        self.fit_widget.hide()

        self.sigFitClicked = self.fit_widget.sigDoFit
        self.control_widget.show_fit_checkbox.toggled[bool].connect(self.fit_widget.setVisible)
        self.control_widget.show_editor_checkbox.toggled[bool].connect(
            self.curve_widget.toggle_plot_editor
        )
        self.control_widget.show_selector_checkbox.toggled[bool].connect(
            self.curve_widget.toggle_plot_selector
        )
        self.control_widget.track_mouse_checkbox.toggled[bool].connect(
            self.curve_widget.toggle_cursor_position
        )
        self.control_widget.x_zoom_checkbox.toggled.connect(self.__zoom_mode_changed)
        self.control_widget.y_zoom_checkbox.toggled.connect(self.__zoom_mode_changed)
        self.control_widget.save_button.clicked.connect(self.sigSaveClicked)
        self.control_widget.remove_button.clicked.connect(self.sigRemoveClicked)

        self.set_rubberband_zoom_selection_mode = self.curve_widget.set_rubberband_zoom_selection_mode
        self.set_data = self.curve_widget.set_data
        self.set_fit_data = self.curve_widget.set_fit_data
        self.set_limits = self.curve_widget.set_limits
        self.set_units = self.curve_widget.set_units
        self.set_labels = self.curve_widget.set_labels
        self.set_auto_range = self.curve_widget.set_auto_range
        self.set_marker_selection_mode = self.curve_widget.set_marker_selection_mode
        self.set_region_selection_mode = self.curve_widget.set_region_selection_mode
        self.set_plot_selection = self.curve_widget.set_plot_selection
        self.set_selection_bounds = self.curve_widget.set_selection_bounds
        self.set_selection_mutable = self.curve_widget.set_selection_mutable
        self.add_marker_selection = self.curve_widget.add_marker_selection
        self.add_region_selection = self.curve_widget.add_region_selection
        self.remove_plot = self.curve_widget.remove_plot
        self.remove_fit_plot = self.curve_widget.remove_fit_plot
        self.clear = self.curve_widget.clear
        self.clear_fits = self.curve_widget.clear_fits
        self.clear_marker_selections = self.curve_widget.clear_marker_selections
        self.clear_region_selections = self.curve_widget.clear_region_selections
        self.move_marker_selection = self.curve_widget.move_marker_selection
        self.move_region_selection = self.curve_widget.move_region_selection
        self.plot = self.curve_widget.plot
        self.plot_fit = self.curve_widget.plot_fit

        self.toggle_selector(True)
        self.toggle_fit(self._show_fit)
        self.toggle_editor(True)
        self.toggle_cursor_tracking(True)

    @property
    def plot_names(self) -> List[str]:
        return self.curve_widget.plot_names

    @property
    def plot_selection(self) -> Dict[str, bool]:
        return self.curve_widget.plot_selection

    @property
    def labels(self) -> Tuple[str, str]:
        return self.curve_widget.labels

    @property
    def units(self) -> Tuple[str, str]:
        return self.curve_widget.units

    @property
    def limits(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        return self.curve_widget.limits

    @property
    def rubberband_zoom_selection_mode(self) -> SelectionMode:
        return self.curve_widget.rubberband_zoom_selection_mode

    @property
    def marker_selection(self) -> Dict[SelectionMode, List[Union[float, Tuple[float, float]]]]:
        return self.curve_widget.marker_selection

    @property
    def region_selection(self) -> Dict[SelectionMode, List[tuple]]:
        return self.curve_widget.region_selection

    @property
    def region_selection_mode(self) -> SelectionMode:
        return self.curve_widget.region_selection_mode

    @property
    def marker_selection_mode(self) -> SelectionMode:
        return self.curve_widget.marker_selection_mode

    @property
    def selection_mutable(self) -> bool:
        return self.curve_widget.selection_mutable

    @property
    def selection_bounds(self) -> Union[None, List[Union[None, Tuple[float, float]]]]:
        return self.curve_widget.selection_bounds

    @property
    def allow_tracking_outside_data(self) -> bool:
        return self.curve_widget.allow_tracking_outside_data

    @allow_tracking_outside_data.setter
    def allow_tracking_outside_data(self, allow: bool) -> None:
        self.curve_widget.allow_tracking_outside_data = bool(allow)

    @property
    def sigMarkerSelectionChanged(self) -> QtCore.Signal:
        return self.curve_widget.sigMarkerSelectionChanged

    @property
    def sigRegionSelectionChanged(self) -> QtCore.Signal:
        return self.curve_widget.sigRegionSelectionChanged

    @property
    def sigMouseMoved(self) -> QtCore.Signal:
        return self.curve_widget.sigMouseMoved

    @property
    def sigMouseDragged(self) -> QtCore.Signal:
        return self.curve_widget.sigMouseDragged

    @property
    def sigMouseClicked(self) -> QtCore.Signal:
        return self.curve_widget.sigMouseClicked

    @property
    def sigZoomAreaApplied(self) -> QtCore.Signal:
        return self.curve_widget.sigZoomAreaApplied

    @property
    def sigAutoLimitsApplied(self) -> QtCore.Signal:
        return self.curve_widget.sigAutoLimitsApplied

    @property
    def sigPlotParametersChanged(self) -> QtCore.Signal:
        return self.curve_widget.sigPlotParametersChanged

    @property
    def show_fit(self) -> bool:
        return self._show_fit

    def toggle_fit(self, show: bool) -> None:
        self.control_widget.show_fit_checkbox.setChecked(show)
        self._show_fit = show

    def toggle_editor(self, show: bool) -> None:
        self.control_widget.show_editor_checkbox.setChecked(show)

    def toggle_selector(self, show: bool) -> None:
        self.control_widget.show_selector_checkbox.setChecked(show)

    def toggle_cursor_tracking(self, enable: bool) -> None:
        self.control_widget.track_mouse_checkbox.setChecked(enable)

    def __zoom_mode_changed(self) -> None:
        x = self.control_widget.x_zoom_checkbox.isChecked()
        y = self.control_widget.y_zoom_checkbox.isChecked()
        if x and y:
            mode = InteractiveCurvesWidget.SelectionMode.XY
        elif x:
            mode = InteractiveCurvesWidget.SelectionMode.X
        elif y:
            mode = InteractiveCurvesWidget.SelectionMode.Y
        else:
            mode = InteractiveCurvesWidget.SelectionMode.Disabled
        self.curve_widget.set_rubberband_zoom_selection_mode(mode)


class QDPlotDockWidget(AdvancedDockWidget):
    """
    """

    def __init__(self, *args, plot_number=0, fit_container=None, show_fit: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle(f'Plot {plot_number:d}')
        self.setFeatures(self.DockWidgetFloatable | self.DockWidgetMovable)
        widget = QDPlotWidget(fit_container=fit_container, show_fit=show_fit)
        self.setWidget(widget)

