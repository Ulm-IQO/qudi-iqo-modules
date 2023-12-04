# -*- coding: utf-8 -*-

"""
This file contains the GUI for qdyne measurements.

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
from dataclasses import dataclass, fields
from PySide2 import QtCore, QtWidgets

from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox


class DataclassWidget(QtWidgets.QWidget):
    def __init__(self, dataclass_obj: dataclass) -> None:
        super().__init__()
        self.data = dataclass_obj
        self.layout = None
        self.labels = dict()
        self.widgets = dict()
        self.init_UI()

    def init_UI(self):
        self.create_layout()
        self.set_widgets(self.data)
        self.setLayout(self.layout)

    def create_layout(self):
        self.layout = QtWidgets.QGridLayout()

    def set_widgets(self, data):
        param_index = 0
        for field in fields(data):
            label = self.create_label(field.name)
            widget = self.create_widget(field)
            if widget is None:
                continue
            widget.setMinimumSize(QtCore.QSize(80, 0))

            self.layout.addWidget(label, 0, param_index + 1, 1, 1)
            self.layout.addWidget(widget, 1, param_index + 1, 1, 1)

            self.labels[field.name] = label
            self.widgets[field.name] = widget
            param_index += 1

    def create_widget(self, field):
        widget = None
        value = getattr(self.data, field.name)

        if field.type == int:
            widget = self.int_to_widget(field, value)
        elif field.type == float:
            widget = self.float_to_widget(field, value)
        elif field.type == str:
            widget = self.str_to_widget(field, value)
        elif field.type == bool:
            widget = self.bool_to_widget(field, value)
        else:
            print('failed to convert dataclass')
            return None

        widget.setObjectName(field.name + '_widget')
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        return widget

    def create_label(self, name):
        label = QtWidgets.QLabel()
        label.setText(name)
        label.setObjectName(name + '_label')
        return label

    def int_to_widget(self, field, value):
        widget = ScienSpinBox()
        widget.setValue(value)
        widget.valueChanged.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def float_to_widget(self, field, value):
        widget = ScienDSpinBox()
        widget.setValue(value)
        widget.valueChanged.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def str_to_widget(self, field, value):
        widget = QtWidgets.QLineEdit()
        widget.setText(value)
        widget.editingFinished.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def bool_to_widget(self, field, value):
        widget = QtWidgets.QCheckBox()
        widget.setChecked(value)
        widget.stateChanged.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def update_param(self, field, value):
        setattr(self.data, field.name, value)
