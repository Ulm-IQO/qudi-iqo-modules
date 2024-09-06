# -*- coding: utf-8 -*-

"""
This file contains a custom QWidget class to provide controls for each scanner axis.

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

__all__ = ('AxesControlDockWidget', 'AxesControlWidget')

from typing import Tuple, Dict
from PySide2 import QtCore, QtGui, QtWidgets
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.util.widgets.slider import DoubleSlider
from qudi.interface.scanning_probe_interface import ScannerAxis, BackScanCapability


class AxesControlDockWidget(QtWidgets.QDockWidget):
    """ Scanner control QDockWidget based on the corresponding QWidget subclass
    """
    __wrapped_attributes = frozenset({'sigResolutionChanged', 'sigBackResolutionChanged', 'sigRangeChanged',
                                      'sigTargetChanged', 'sigSliderMoved', 'axes', 'resolution', 'back_resolution',
                                      'range', 'target', 'set_resolution', 'set_back_resolution',
                                      'get_range', 'set_range', 'get_target', 'set_target',
                                      'set_assumed_unit_prefix', 'emit_current_settings',
                                      'set_backward_settings_visibility'})

    def __init__(self, scanner_axes: Tuple[ScannerAxis], back_scan_capability: BackScanCapability):
        super().__init__('Axes Control')
        self.setObjectName('axes_control_dockWidget')
        widget = AxesControlWidget(scanner_axes=scanner_axes, back_scan_capability=back_scan_capability)
        widget.setObjectName('axes_control_widget')
        self.setWidget(widget)

    def __getattr__(self, item):
        if item in self.__wrapped_attributes:
            return getattr(self.widget(), item)
        raise AttributeError('AxesControlDockWidget has not attribute "{0}"'.format(item))


class AxesControlWidget(QtWidgets.QWidget):
    """ Widget to control scan parameters and target position of scanner axes.
    """

    sigResolutionChanged = QtCore.Signal(str, int)
    sigBackResolutionChanged = QtCore.Signal(str, int)
    sigRangeChanged = QtCore.Signal(str, tuple)
    sigTargetChanged = QtCore.Signal(str, float)
    sigSliderMoved = QtCore.Signal(str, float)

    def __init__(self, *args, scanner_axes: Tuple[ScannerAxis], back_scan_capability: BackScanCapability, **kwargs):
        super().__init__(*args, **kwargs)
        self._back_scan_capability = back_scan_capability
        self.axes_widgets = dict()

        font = QtGui.QFont()
        font.setBold(True)
        layout = QtWidgets.QGridLayout()

        column = 1

        label = QtWidgets.QLabel('Resolution')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, column, 1, 3)

        self._forward_backward_labels = []
        for label_text in ['Forward', '=', 'Backward']:
            label = QtWidgets.QLabel(label_text)
            label.setFont(font)
            label.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(label, 1, column)
            column += 1
            self._forward_backward_labels.append(label)

        vline = QtWidgets.QFrame()
        vline.setFrameShape(QtWidgets.QFrame.VLine)
        vline.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(vline, 0, column, len(scanner_axes) + 2, 1)
        column += 1

        label = QtWidgets.QLabel('Scan Range')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, column, 1, 2)
        column += 2

        vline = QtWidgets.QFrame()
        vline.setFrameShape(QtWidgets.QFrame.VLine)
        vline.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(vline, 0, column, len(scanner_axes) + 2, 1)
        column += 2

        label = QtWidgets.QLabel('Current Target')
        label.setFont(font)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label, 0, column, 1, 1)

        for index, axis in enumerate(scanner_axes, 2):
            ax_name = axis.name
            widgets = {}
            label = QtWidgets.QLabel('{0}-Axis:'.format(ax_name.title()))
            label.setObjectName('{0}_axis_label'.format(ax_name))
            label.setFont(font)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            for direction in ['forward', 'backward']:
                res_spinbox = QtWidgets.QSpinBox()
                res_spinbox.setObjectName(f'{ax_name}_{direction}_resolution_spinBox')
                res_spinbox.setRange(axis.resolution.minimum, min(2 ** 31 - 1, axis.resolution.maximum))
                res_spinbox.setValue(axis.resolution.minimum)
                res_spinbox.setSuffix(' px')
                res_spinbox.setMinimumSize(50, 0)
                widgets[f'{direction}_res_spinbox'] = res_spinbox

            init_pos = (axis.position.maximum - axis.position.minimum) / 2 + axis.position.minimum
            for i in ['min', 'max', 'pos']:
                spinbox = ScienDSpinBox()
                spinbox.setObjectName(f'{ax_name}_range_{i}_scienDSpinBox')
                spinbox.setRange(*axis.position.bounds)
                spinbox.setSuffix(axis.unit)
                spinbox.setMinimumSize(75, 0)
                widgets[f'{i}_spinbox'] = spinbox
            widgets['min_spinbox'].setValue(axis.position.minimum)
            widgets['max_spinbox'].setValue(axis.position.maximum)
            widgets['pos_spinbox'].setValue(init_pos)

            # same for every spinbox
            for widget in widgets.values():
                widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
                widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                     QtWidgets.QSizePolicy.Preferred)

            # checkbox for having back resolution equal to forward resolution
            check_box = QtWidgets.QCheckBox()
            widgets['eq_checkbox'] = check_box

            # slider for moving the scanner
            slider = DoubleSlider(QtCore.Qt.Horizontal)
            slider.setObjectName('{0}_position_doubleSlider'.format(ax_name))
            slider.setRange(*axis.position.bounds)
            granularity = 2 ** 31 - 1
            if axis.step.minimum > 0:
                granularity = min(granularity,
                                  round((axis.position.maximum - axis.position.minimum) / axis.step.minimum) + 1)
            slider.set_granularity(granularity)
            slider.setValue(init_pos)
            slider.setMinimumSize(150, 0)
            slider.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            widgets['slider'] = slider

            # Add to layout
            layout.addWidget(label, index, 0)
            layout.addWidget(widgets['forward_res_spinbox'], index, 1)
            layout.addWidget(widgets['eq_checkbox'], index, 2)
            layout.addWidget(widgets['backward_res_spinbox'], index, 3)
            layout.addWidget(widgets['min_spinbox'], index, 5)
            layout.addWidget(widgets['max_spinbox'], index, 6)
            layout.addWidget(widgets['slider'], index, 8)
            layout.addWidget(widgets['pos_spinbox'], index, 9)

            # Remember widgets references for later access
            self.axes_widgets[ax_name] = widgets

            # adapt to back scan capability of hardware
            if BackScanCapability.AVAILABLE not in self._back_scan_capability:
                self.set_backward_settings_visibility(False)
            elif BackScanCapability.RESOLUTION_CONFIGURABLE not in self._back_scan_capability:
                for widget in ['backward_res_spinbox', 'eq_checkbox']:
                    widgets[widget].setEnabled(False)
                widgets['eq_checkbox'].setChecked(True)
                widgets['backward_res_spinbox'].setToolTip("Back resolution is not configurable.")
                # keep back resolution spinbox up to date
                widgets['forward_res_spinbox'].valueChanged.connect(widgets['backward_res_spinbox'].setValue)

            # Connect signals
            # TODO "editingFinished" also emits when window gets focus again, so also after alt+tab.
            #  "valueChanged" was considered as a replacement but is emitted when scrolled or while typing numbers.

            widgets['eq_checkbox'].stateChanged.connect(self.__get_equal_checkbox_callback(ax_name))
            widgets['forward_res_spinbox'].editingFinished.connect(
                self.__get_axis_resolution_callback(ax_name, widgets['forward_res_spinbox'])
            )
            widgets['backward_res_spinbox'].editingFinished.connect(
                self.__get_axis_back_resolution_callback(ax_name, widgets['backward_res_spinbox'])
            )
            widgets['min_spinbox'].editingFinished.connect(
                self.__get_axis_min_range_callback(ax_name, widgets['min_spinbox'])
            )
            widgets['max_spinbox'].editingFinished.connect(
                self.__get_axis_max_range_callback(ax_name, widgets['max_spinbox'])
            )
            widgets['slider'].doubleSliderMoved.connect(self.__get_axis_slider_moved_callback(ax_name))
            widgets['slider'].sliderReleased.connect(
                self.__get_axis_slider_released_callback(ax_name, widgets['slider']))
            widgets['pos_spinbox'].editingFinished.connect(
                self.__get_axis_target_callback(ax_name, widgets['pos_spinbox'])
            )
            if widgets['eq_checkbox'].isEnabled() and widgets['eq_checkbox'].isVisible():
                widgets['eq_checkbox'].setChecked(True)  # check equal checkbox by default

        layout.setColumnStretch(7, 1)
        self.setLayout(layout)
        self.setMaximumHeight(self.sizeHint().height())

        # set tab order of Widgets
        tab_order = [self.axes_widgets[ax][f'{d}_res_spinbox'] for ax in self.axes for d in ['forward', 'backward']]
        tab_order += [self.axes_widgets[ax][f'{i}_spinbox'] for ax in self.axes for i in ['min', 'max']]
        tab_order += [self.axes_widgets[ax]['pos_spinbox'] for ax in self.axes]
        for i in range(len(tab_order) - 1):
            self.setTabOrder(tab_order[i], tab_order[i + 1])

    @property
    def axes(self):
        return tuple(self.axes_widgets)

    @property
    def resolution(self):
        return {ax: widgets['forward_res_spinbox'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def back_resolution(self):
        return {ax: widgets['backward_res_spinbox'].value() for ax, widgets in self.axes_widgets.items()}

    @property
    def range(self):
        return {ax: (widgets['min_spinbox'].value(), widgets['max_spinbox'].value()) for ax, widgets
                in self.axes_widgets.items()}

    @property
    def target(self):
        return {ax: widgets['pos_spinbox'].value() for ax, widgets in self.axes_widgets.items()}

    @QtCore.Slot(dict)
    def set_resolution(self, resolution: Dict[str, int]) -> None:
        back_res = {}
        for ax, val in resolution.items():
            spinbox = self.axes_widgets[ax]['forward_res_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)
            if self.axes_widgets[ax]['eq_checkbox'].isChecked():
                back_res[ax] = val
        self.set_back_resolution(back_res)

    @QtCore.Slot(dict)
    def set_back_resolution(self, resolution: Dict[str, int]) -> None:
        for ax, val in resolution.items():
            spinbox = self.axes_widgets[ax]['backward_res_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)

    def get_range(self, axis):
        widget_dict = self.axes_widgets[axis]
        return widget_dict['min_spinbox'].value(), widget_dict['max_spinbox'].value()

    @QtCore.Slot(dict)
    def set_range(self, rng: Dict[str, Tuple[float, float]]) -> None:
        for ax, val in rng.items():
            min_spinbox = self.axes_widgets[ax]['min_spinbox']
            max_spinbox = self.axes_widgets[ax]['max_spinbox']
            min_val, max_val = val
            min_spinbox.blockSignals(True)
            min_spinbox.setValue(min_val)
            min_spinbox.blockSignals(False)
            max_spinbox.blockSignals(True)
            max_spinbox.setValue(max_val)
            max_spinbox.blockSignals(False)

    def get_target(self, axis):
        return self.axes_widgets[axis]['pos_spinbox'].value()

    @QtCore.Slot(dict)
    def set_target(self, target: Dict[str, float]):
        for ax, val in target.items():
            spinbox = self.axes_widgets[ax]['pos_spinbox']
            slider = self.axes_widgets[ax]['slider']
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)

    def set_assumed_unit_prefix(self, prefix):
        for widgets in self.axes_widgets.values():
            widgets['pos_spinbox'].assumed_unit_prefix = prefix
            widgets['min_spinbox'].assumed_unit_prefix = prefix
            widgets['max_spinbox'].assumed_unit_prefix = prefix

    def emit_current_settings(self):
        """Emit signals with current settings."""
        for ax, rng in self.range.items():
            self.sigRangeChanged.emit(ax, rng)
        for ax, res in self.resolution.items():
            self.sigResolutionChanged.emit(ax, res)
        for ax, res in self.back_resolution.items():
            self.sigBackResolutionChanged.emit(ax, res)

    def set_backward_settings_visibility(self, visible: bool):
        for widgets in self.axes_widgets.values():
            widgets['backward_res_spinbox'].setVisible(visible)
            widgets['eq_checkbox'].setVisible(visible)
        for label in self._forward_backward_labels:
            label.setVisible(visible)

    def __get_axis_resolution_callback(self, axis, spinbox):
        def callback():
            self.sigResolutionChanged.emit(axis, spinbox.value())
        return callback

    def __get_axis_back_resolution_callback(self, axis, spinbox):
        def callback():
            self.sigBackResolutionChanged.emit(axis, spinbox.value())
        return callback

    def __get_equal_checkbox_callback(self, axis: str):
        @QtCore.Slot(bool)
        def callback(checked: bool):
            forward_spinbox = self.axes_widgets[axis][f'forward_res_spinbox']
            backward_spinbox = self.axes_widgets[axis][f'backward_res_spinbox']
            if checked:
                forward_spinbox.valueChanged.connect(backward_spinbox.setValue)
                # ensure that a sigBackResolutionChanged is emitted
                forward_spinbox.editingFinished.connect(self.__get_axis_back_resolution_callback(axis, forward_spinbox))
                forward_spinbox.editingFinished.emit()  # emit manually once
                backward_spinbox.setValue(forward_spinbox.value())  # set manually once
            else:
                # disconnect from everything and reconnect only to forward resolution callback
                forward_spinbox.valueChanged.disconnect()
                forward_spinbox.editingFinished.disconnect()
                forward_spinbox.editingFinished.connect(self.__get_axis_resolution_callback(axis, forward_spinbox))
            backward_spinbox.setDisabled(checked)
        return callback

    def __get_axis_min_range_callback(self, axis, spinbox):
        def callback():
            max_spinbox = self.axes_widgets[axis]['max_spinbox']
            min_value = spinbox.value()
            max_value = max_spinbox.value()
            if min_value > max_value:
                max_spinbox.blockSignals(True)
                max_spinbox.setValue(min_value)
                max_spinbox.blockSignals(False)
                max_value = min_value
            self.sigRangeChanged.emit(axis, (min_value, max_value))
        return callback

    def __get_axis_max_range_callback(self, axis, spinbox):
        def callback():
            min_spinbox = self.axes_widgets[axis]['min_spinbox']
            min_value = min_spinbox.value()
            max_value = spinbox.value()
            if max_value < min_value:
                min_spinbox.blockSignals(True)
                min_spinbox.setValue(max_value)
                min_spinbox.blockSignals(False)
                min_value = max_value
            self.sigRangeChanged.emit(axis, (min_value, max_value))
        return callback

    def __get_axis_slider_moved_callback(self, axis):
        def callback(value):
            spinbox = self.axes_widgets[axis]['pos_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(value)
            spinbox.blockSignals(False)
            self.sigSliderMoved.emit(axis, value)
        return callback

    def __get_axis_slider_released_callback(self, axis, slider):
        def callback():
            value = slider.value()
            spinbox = self.axes_widgets[axis]['pos_spinbox']
            spinbox.blockSignals(True)
            spinbox.setValue(value)
            spinbox.blockSignals(False)
            self.sigTargetChanged.emit(axis, value)
        return callback

    def __get_axis_target_callback(self, axis, spinbox):
        def callback():
            value = spinbox.value()
            slider = self.axes_widgets[axis]['slider']
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
            self.sigTargetChanged.emit(axis, value)
        return callback
