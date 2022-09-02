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

__all__ = ['PlotDockWidget', 'QDPlotWidget']

import os
from pyqtgraph import AxisItem, SignalProxy
from PySide2 import QtWidgets, QtCore, QtGui
from typing import Optional, Tuple

from qudi.util.paths import get_artwork_dir
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.util.widgets.fitting import FitWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.util.widgets.separator_lines import VerticalLine
from qudi.util.widgets.plotting.plot_widget import DataSelectionPlotWidget


class LabelNudgeAxis(AxisItem):
    """ This is a custom axis that extends the normal pyqtgraph to be able to nudge the axis labels
    """

    @property
    def nudge(self):
        if not hasattr(self, "_nudge"):
            self._nudge = 5
        return self._nudge

    @nudge.setter
    def nudge(self, nudge):
        self._nudge = nudge
        s = self.size()
        # call resizeEvent indirectly
        self.resize(s + QtCore.QSizeF(1, 1))
        self.resize(s)

    def resizeEvent(self, ev=None):
        # Set the position of the label
        nudge = self.nudge
        br = self.label.boundingRect()
        p = QtCore.QPointF(0, 0)
        if self.orientation == "left":
            p.setY(int(self.size().height() / 2 + br.width() / 2))
            p.setX(-nudge)
        elif self.orientation == "right":
            p.setY(int(self.size().height() / 2 + br.width() / 2))
            p.setX(int(self.size().width() - br.height() + nudge))
        elif self.orientation == "top":
            p.setY(-nudge)
            p.setX(int(self.size().width() / 2.0 - br.width() / 2.0))
        elif self.orientation == "bottom":
            p.setX(int(self.size().width() / 2.0 - br.width() / 2.0))
            p.setY(int(self.size().height() - br.height() + nudge))
        self.label.setPos(p)
        self.picture = None


class PlotEditorWidget(QtWidgets.QGroupBox):
    """
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__('Plot Control', parent=parent)

        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        # Generate labels
        x_label = QtWidgets.QLabel('Horizontal Axis:')
        x_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        y_label = QtWidgets.QLabel('Vertical Axis:')
        y_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        label_label = QtWidgets.QLabel('Label')
        label_label.setAlignment(QtCore.Qt.AlignCenter)
        unit_label = QtWidgets.QLabel('Units')
        unit_label.setAlignment(QtCore.Qt.AlignCenter)
        range_label = QtWidgets.QLabel('Range')
        range_label.setAlignment(QtCore.Qt.AlignCenter)
        # Generate editors
        self.x_label_lineEdit = QtWidgets.QLineEdit()
        self.x_label_lineEdit.setMinimumWidth(50)
        self.x_unit_lineEdit = QtWidgets.QLineEdit()
        self.x_unit_lineEdit.setMinimumWidth(50)
        self.x_lower_limit_spinBox = ScienDSpinBox()
        self.x_lower_limit_spinBox.setMinimumWidth(70)
        self.x_upper_limit_spinBox = ScienDSpinBox()
        self.x_upper_limit_spinBox.setMinimumWidth(70)
        self.x_auto_button = QtWidgets.QPushButton('Auto Range')
        self.y_label_lineEdit = QtWidgets.QLineEdit()
        self.y_label_lineEdit.setMinimumWidth(50)
        self.y_unit_lineEdit = QtWidgets.QLineEdit()
        self.y_unit_lineEdit.setMinimumWidth(50)
        self.y_lower_limit_spinBox = ScienDSpinBox()
        self.y_lower_limit_spinBox.setMinimumWidth(70)
        self.y_upper_limit_spinBox = ScienDSpinBox()
        self.y_upper_limit_spinBox.setMinimumWidth(70)
        self.y_auto_button = QtWidgets.QPushButton('Auto Range')

        row = 0
        layout.addWidget(label_label, row, 1)
        layout.addWidget(unit_label, row, 2)
        layout.addWidget(range_label, row, 4, 1, 3)
        row += 1
        layout.addWidget(x_label, row, 0)
        layout.addWidget(self.x_label_lineEdit, row, 1)
        layout.addWidget(self.x_unit_lineEdit, row, 2)
        layout.addWidget(self.x_lower_limit_spinBox, row, 4)
        layout.addWidget(self.x_upper_limit_spinBox, row, 5)
        layout.addWidget(self.x_auto_button, row, 6)
        row += 1
        layout.addWidget(y_label, row, 0)
        layout.addWidget(self.y_label_lineEdit, row, 1)
        layout.addWidget(self.y_unit_lineEdit, row, 2)
        layout.addWidget(self.y_lower_limit_spinBox, row, 4)
        layout.addWidget(self.y_upper_limit_spinBox, row, 5)
        layout.addWidget(self.y_auto_button, row, 6)
        row += 1
        layout.addWidget(VerticalLine(), 0, 3, row, 1)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(4, 3)
        layout.setColumnStretch(5, 3)


class LabelNudgeDataSelectionPlotWidget(DataSelectionPlotWidget):
    """
    """
    def __init__(self):
        super().__init__(
            axisItems={'bottom': LabelNudgeAxis(orientation='bottom'),
                       'left'  : LabelNudgeAxis(orientation='left')}
        )
        self.getAxis('bottom').nudge = 0
        self.getAxis('left').nudge = 0
        self.addLegend()


class PlotControlWidget(QtWidgets.QWidget):
    """
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        layout = QtWidgets.QHBoxLayout()
        self.setLayout(layout)

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

        layout.addWidget(self.save_button)
        layout.addStretch()
        layout.addWidget(self.show_editor_checkbox)
        layout.addWidget(self.show_fit_checkbox)
        layout.addStretch()
        layout.addWidget(self.remove_button)


class QDPlotWidget(QtWidgets.QWidget):
    """
    """

    sigLimitsChanged = QtCore.Signal(tuple, tuple)   # x_limits, y_limits
    sigLabelsChanged = QtCore.Signal(str, str)       # x_label, y_label
    sigUnitsChanged = QtCore.Signal(str, str)        # x_unit, y_unit
    sigAutoRangeClicked = QtCore.Signal(bool, bool)  # x_axis, y_axis
    sigFitClicked = QtCore.Signal(str)               # fit_function_name
    sigSaveClicked = QtCore.Signal()
    sigRemoveClicked = QtCore.Signal()

    _proxy_delay = 0.2

    def __init__(self, *args, fit_container=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        self.plot_widget = LabelNudgeDataSelectionPlotWidget()
        self.fit_widget = FitWidget(fit_container=fit_container)
        self.control_widget = PlotControlWidget()
        self.editor_widget = PlotEditorWidget()
        self.control_widget.layout().setContentsMargins(0, 0, 0, 0)
        self.fit_widget.layout().setContentsMargins(0, 0, 0, 0)

        row = 0
        layout.addWidget(self.control_widget, row, 0, 1, 2)
        row += 1
        layout.addWidget(self.plot_widget, row, 0)
        layout.addWidget(self.fit_widget, row, 1)
        row += 1
        layout.addWidget(self.editor_widget, row, 0, 1, 2)
        row += 1
        layout.setColumnStretch(0, 1)

        self.fit_widget.hide()
        self.editor_widget.hide()

        self.sigFitClicked = self.fit_widget.sigDoFit
        self.control_widget.show_fit_checkbox.toggled[bool].connect(self.fit_widget.setVisible)
        self.control_widget.show_editor_checkbox.toggled[bool].connect(
            self.editor_widget.setVisible
        )
        self.editor_widget.x_label_lineEdit.editingFinished.connect(self.__emit_labels_changed)
        self.editor_widget.y_label_lineEdit.editingFinished.connect(self.__emit_labels_changed)
        self.editor_widget.x_unit_lineEdit.editingFinished.connect(self.__emit_units_changed)
        self.editor_widget.y_unit_lineEdit.editingFinished.connect(self.__emit_units_changed)
        self.editor_widget.x_lower_limit_spinBox.valueChanged.connect(self.__emit_limits_changed)
        self.editor_widget.x_upper_limit_spinBox.valueChanged.connect(self.__emit_limits_changed)
        self.editor_widget.y_lower_limit_spinBox.valueChanged.connect(self.__emit_limits_changed)
        self.editor_widget.y_upper_limit_spinBox.valueChanged.connect(self.__emit_limits_changed)
        self.editor_widget.x_auto_button.clicked.connect(
            lambda: self.sigAutoRangeClicked.emit(True, False)
        )
        self.editor_widget.y_auto_button.clicked.connect(
            lambda: self.sigAutoRangeClicked.emit(False, True)
        )
        self.control_widget.save_button.clicked.connect(self.__save_clicked)
        self.control_widget.remove_button.clicked.connect(self.__remove_clicked)
        self.__limits_signal_proxy = SignalProxy(
            self.plot_widget.sigRangeChanged,
            delay=self._proxy_delay,
            slot=lambda x: self.__plot_limits_changed(x[1])
        )

    def toggle_fit(self, show: bool) -> None:
        self.control_widget.show_fit_checkbox.setChecked(show)

    def toggle_editor(self, show: bool) -> None:
        self.control_widget.show_editor_checkbox.setChecked(show)

    @property
    def labels(self) -> Tuple[str, str]:
        editor = self.editor_widget
        return editor.x_label_lineEdit.text(), editor.y_label_lineEdit.text()

    @property
    def units(self) -> Tuple[str, str]:
        editor = self.editor_widget
        return editor.x_unit_lineEdit.text(), editor.y_unit_lineEdit.text()

    @property
    def limits(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        editor = self.editor_widget
        return (editor.x_lower_limit_spinBox.value(), editor.x_upper_limit_spinBox.value()),\
               (editor.y_lower_limit_spinBox.value(), editor.y_upper_limit_spinBox.value())

    def set_labels(self, x_label: Optional[str] = None, y_label: Optional[str] = None) -> None:
        if x_label is not None:
            self.set_x_label(x_label)
        if y_label is not None:
            self.set_y_label(y_label)

    def set_x_label(self, label: str) -> None:
        unit = self.editor_widget.x_unit_lineEdit.text()
        self.editor_widget.x_label_lineEdit.setText(label)
        self.plot_widget.setLabel('bottom', label, units=unit)

    def set_y_label(self, label: str) -> None:
        unit = self.editor_widget.y_unit_lineEdit.text()
        self.editor_widget.y_label_lineEdit.setText(label)
        self.plot_widget.setLabel('left', label, units=unit)

    def set_units(self, x_unit: Optional[str] = None, y_unit: Optional[str] = None) -> None:
        if x_unit is not None:
            self.set_x_unit(x_unit)
        if y_unit is not None:
            self.set_y_unit(y_unit)

    def set_x_unit(self, unit: str) -> None:
        label = self.editor_widget.x_label_lineEdit.text()
        self.editor_widget.x_unit_lineEdit.setText(unit)
        self.plot_widget.setLabel('bottom', label, units=unit)

    def set_y_unit(self, unit: str) -> None:
        label = self.editor_widget.y_label_lineEdit.text()
        self.editor_widget.y_unit_lineEdit.setText(unit)
        self.plot_widget.setLabel('left', label, units=unit)

    def set_limits(self,
                   x_limits: Optional[Tuple[float, float]] = None,
                   y_limits: Optional[Tuple[float, float]] = None
                   ) -> None:
        if x_limits is not None:
            self.set_x_limits(x_limits)
        if y_limits is not None:
            self.set_y_limits(y_limits)

    def set_x_limits(self, limits: Tuple[float, float]) -> None:
        limits = sorted(limits)

        # Pyqtgraph signal proxy behaves differently from version 0.12.0 on
        try:
            with self.__limits_signal_proxy.block():
                self.plot_widget.setXRange(*limits, padding=0)
        except AttributeError:
            self.__limits_signal_proxy.block = True
            try:
                self.plot_widget.setXRange(*limits, padding=0)
            finally:
                self.__limits_signal_proxy.block = False

        self.editor_widget.x_lower_limit_spinBox.blockSignals(True)
        self.editor_widget.x_upper_limit_spinBox.blockSignals(True)
        try:
            self.editor_widget.x_lower_limit_spinBox.setValue(limits[0])
            self.editor_widget.x_upper_limit_spinBox.setValue(limits[1])
        finally:
            self.editor_widget.x_lower_limit_spinBox.blockSignals(False)
            self.editor_widget.x_upper_limit_spinBox.blockSignals(False)

    def set_y_limits(self, limits: Tuple[float, float]) -> None:
        limits = sorted(limits)

        # Pyqtgraph signal proxy behaves differently from version 0.12.0 on
        try:
            with self.__limits_signal_proxy.block():
                self.plot_widget.setYRange(*limits, padding=0)
        except AttributeError:
            self.__limits_signal_proxy.block = True
            try:
                self.plot_widget.setYRange(*limits, padding=0)
            finally:
                self.__limits_signal_proxy.block = False

        self.editor_widget.y_lower_limit_spinBox.blockSignals(True)
        self.editor_widget.y_upper_limit_spinBox.blockSignals(True)
        try:
            self.editor_widget.y_lower_limit_spinBox.setValue(limits[0])
            self.editor_widget.y_upper_limit_spinBox.setValue(limits[1])
        finally:
            self.editor_widget.y_lower_limit_spinBox.blockSignals(False)
            self.editor_widget.y_upper_limit_spinBox.blockSignals(False)

    def __plot_limits_changed(self,
                              limits: Tuple[Tuple[float, float], Tuple[float, float]]
                              ) -> None:
        item_ctrl = self.plot_widget.getPlotItem().ctrl
        # Do nothing if axes are logarithmic or if FFT is shown
        if item_ctrl.logXCheck.isChecked() or item_ctrl.logYCheck.isChecked() or item_ctrl.fftCheck.isChecked():
            return
        self.sigLimitsChanged.emit(*limits)

    def __emit_labels_changed(self) -> None:
        self.sigLabelsChanged.emit(*self.labels)

    def __emit_units_changed(self) -> None:
        self.sigUnitsChanged.emit(*self.units)

    def __emit_limits_changed(self) -> None:
        self.sigLimitsChanged.emit(*self.limits)

    def __save_clicked(self) -> None:
        self.__limits_signal_proxy.flush()
        self.sigSaveClicked.emit()

    def __remove_clicked(self) -> None:
        self.__limits_signal_proxy.flush()
        self.sigRemoveClicked.emit()


class PlotDockWidget(AdvancedDockWidget):
    """
    """

    def __init__(self, *args, plot_number=0, fit_container=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle(f'Plot {plot_number:d}')
        self.setFeatures(self.DockWidgetFloatable | self.DockWidgetMovable)
        widget = QDPlotWidget(fit_container=fit_container)
        self.setWidget(widget)

