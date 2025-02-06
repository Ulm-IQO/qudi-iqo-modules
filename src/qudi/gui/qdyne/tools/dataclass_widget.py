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
from PySide2 import QtWidgets
from PySide2.QtCore import Signal, Slot, QSize
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from qudi.logic.qdyne.tools.custom_dataclass import DataclassMediator

# class DataclassWidget(QtWidgets.QWidget):
#     def __init__(self, dataclass_obj: dataclass, invoke_func=None) -> None:
#         """
#         dataclass_obj: dataclass object used for the widgets
#         func: function invoked after values are changed.
#         """
#         super().__init__()
#         self.data = dataclass_obj
#         self.invoke_func = invoke_func
#         self.layout = None
#         self.labels = dict()
#         self.widgets = dict()
#         self.init_UI()
#
#     def init_UI(self):
#         self.create_layout()
#         self.set_data(self.data)
#
#     def set_data(self, data):
#         """
#         set data to widgets.
#         """
#         self.setUpdatesEnabled(False)
#         self.data = data
#         self.clear_layout()
#         self.set_widgets(self.data)
#         self.setLayout(self.layout)
#         self.widgets['name'].setReadOnly(True)
#         self.setUpdatesEnabled(True)
#
#     def update_params_from_data(self, data):
#         """
#         update the parameters of widgets according to the data.
#         """
#         for field in fields(data):
#             self.update_param(field, getattr(data, field.name))
#
#     def create_layout(self):
#         self.layout = QtWidgets.QGridLayout()
#
#     def clear_layout(self):
#         while self.layout.count():
#             item = self.layout.takeAt(0)
#             widget = item.widget()
#             if widget:
#                 widget.deleteLater()
#             elif item.layout():
#                 item.layout().deleteLater()
#
#     def set_widgets(self, data):
#         """
#         create widgets based on dataclass and set them in the layout
#         """
#         self.labels = dict()
#         self.widgets = dict()
#         param_index = 0
#         for field in fields(data):
#             if not field.name.startswith("_"):
#                 label = self.create_label(field.name)
#                 widget = self.create_widget(field)
#                 if widget is None:
#                     continue
#                 widget.setMinimumSize(QtCore.QSize(80, 0))
#
#                 self.layout.addWidget(label, 0, param_index + 1, 1, 1)
#                 self.layout.addWidget(widget, 1, param_index + 1, 1, 1)
#
#                 self.labels[field.name] = label
#                 self.widgets[field.name] = widget
#                 param_index += 1
#
#     def create_widget(self, field):
#         """
#         create widget based on the field of parameter.
#         """
#         widget = None
#         value = getattr(self.data, field.name)
#
#         if field.type == int:
#             widget = self.int_to_widget(field, value)
#         elif field.type == float:
#             widget = self.float_to_widget(field, value)
#         elif field.type == str:
#             widget = self.str_to_widget(field, value)
#         elif field.type == bool:
#             widget = self.bool_to_widget(field, value)
#         else:
#             return None
#
#         widget.setObjectName(field.name + '_widget')
#         widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
#
#         return widget
#
#     def create_label(self, name):
#         label = QtWidgets.QLabel()
#         label.setText(name)
#         label.setObjectName(name + '_label')
#         return label
#
#     def int_to_widget(self, field, value):
#         widget = ScienSpinBox()
#         widget.setValue(value)
# #        widget.valueChanged.connect(lambda state, f=field, w=widget: self.update_param(f, w.value()))
#         widget.editingFinished.connect(lambda f=field, w=widget: self.update_param(f, w.value()))
#         if self.invoke_func is not None:
#             widget.editingFinished.connect(self.invoke_func)
#         return widget
#
#     def float_to_widget(self, field, value):
#         widget = ScienDSpinBox()
#         widget.setValue(value)
# #        widget.valueChanged.connect(lambda state, f=field, w=widget: self.update_param(f, w.value()))
#         widget.editingFinished.connect(lambda f=field, w=widget: self.update_param(f, w.value()))
#         if self.invoke_func is not None:
#             widget.editingFinished.connect(self.invoke_func)
#         return widget
#
#     def str_to_widget(self, field, value):
#         widget = QtWidgets.QLineEdit()
#         widget.setText(value)
#         widget.editingFinished.connect(lambda f=field, w=widget: self.update_param(f, w.text()))
#         if self.invoke_func is not None:
#             widget.editingFinished.connect(self.invoke_func)
#         return widget
#
#     def bool_to_widget(self, field, value):
#         widget = QtWidgets.QCheckBox()
#         widget.setChecked(value)
#         widget.stateChanged.connect(lambda state, f=field, w=widget: self.update_param(f, w.isChecked()))
#         if self.invoke_func is not None:
#             widget.stateChanged.connect(self.invoke_func)
#         return widget
#
#     def update_param(self, field, value):
#         if field.type == int or field.type == float:
#             self.widgets[field.name].setValue(value)
#         elif field.type == str:
#             self.widgets[field.name].setText(value)
#         elif field.type == bool:
#             self.widgets[field.name].setChecked(value)
#         else:
#             return
#
#     def disconnect_widgets(self):
#         for field_name, old_widget in self.widgets.items():
#             if isinstance(old_widget, (QtWidgets.QLineEdit, ScienSpinBox, ScienDSpinBox)):
#                 old_widget.editingFinished.disconnect()
#             elif isinstance(old_widget, QtWidgets.QCheckBox):
#                 old_widget.stateChanged.disconnect()
#             else:
#                 old_widget.valueChanged.disconnect()

class DataclassWidget(QtWidgets.QWidget):
    widget_value_updated_sig = Signal()
    def __init__(self, dataclass_obj: Dataclass, mediator: DataclassMediator) -> None:
        """
        dataclass_obj: dataclass object used for the widgets
        func: function invoked after values are changed.
        """
        super().__init__()
        self.data = dataclass_obj
        self.mediator = mediator
        self.layout_main = None
        self.data_labels = dict()
        self.data_widgets = dict()
        self.layouts = dict()

        self.labels = dict()
        self.widgets = dict()

        self.init_UI()

        self._widget_value_updated_sig = None

    def init_UI(self):
        self.create_widgets()
        self.arange_layout()
        self.set_data(self.data)

    def create_widgets(self):
        self.create_data_widgets(self.data)

    def arange_layout(self):
        self.layout_main = QtWidgets.QGridLayout()
        self.layout_main.addLayout(self.create_data_layout())

    def create_data_layout(self):
        """
        create grid layout for names and parameters of a dataclass.
        """
        data_layout = QtWidgets.QGridLayout()
        param_index = 0

        for field in fields(self.data):
            data_layout.addWidget(self.data_labels[field.name], 0, param_index + 1, 1, 1)
            data_layout.addWidget(self.data_widgets[field.name], 1, param_index + 1, 1, 1)
            param_index += 1

        data_layout
        return data_layout

    def connect_signals(self):
        self.connect_signals_from_mediator()

    def connect_signals_from_mediator(self):
        self.mediator.data_updated_sig.connect(self.update_widgets)

    def disconnect_signals(self):
        self.disconnect_signals_from_mediator()
    def disconnect_signals_from_mediator(self):
        self.mediator.data_updated_sig.disconnect()


    def _emit_update_sig(self):
        self._widget_value_updated_sig.emit(self.current_values_dict)

    @property
    def values_dict(self):
        return

    def set_data(self, data):
        """
        set data to widgets.
        """
        self.setUpdatesEnabled(False)
        self.data = data
        self._clear_layout()
        self._set_widgets(self.data)
        self._setLayout(self.layout)
        self.data_widgets['name'].setReadOnly(True)
        self.setUpdatesEnabled(True)

    @Slot()
    def update_widgets(self, data_dict):
        """
        update the parameters of widgets according to the data.
        """
        for param_name in data_dict.keys():
            self.update_widget_value(param_name, data_dict[param_name])

    def _clear_data_layout(self):
        """
        remove widgets in data_layout.
        """
        data_layout = self.layouts['data']
        while data_layout.count():
            item = data_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                item.layout().deleteLater()

    def create_data_widgets(self, data):
        """
        create widgets based on dataclass
        """
        self.data_labels = dict()
        self.data_widgets = dict()
        for field in fields(data):
            if not field.name.startswith("_"):
                label = self.create_label(field.name)
                widget = self.create_widget(field)
                if widget is None:
                    continue
                widget.setMinimumSize(QSize(80, 0))

                self.data_labels[field.name] = label
                self.data_widgets[field.name] = widget

    def _create_widget(self, field):
        """
        create widget based on the field of parameter.
        """
        widget = None
        value = getattr(self.data, field.name)

        if field.type == int:
            widget = self._int_to_widget(value)
        elif field.type == float:
            widget = self._float_to_widget(value)
        elif field.type == str:
            widget = self._str_to_widget(value)
        elif field.type == bool:
            widget = self._bool_to_widget(value)
        else:
            return None

        widget.setObjectName(field.name + '_widget')
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        return widget

    def _create_label(self, name):
        label = QtWidgets.QLabel()
        label.setText(name)
        label.setObjectName(name + '_label')
        return label

    def _int_to_widget(self, value):
        widget = ScienSpinBox()
        widget.setValue(value)
        widget.editingFinished.connect(self._emit_update_sig)
        return widget

    def _float_to_widget(self, value):
        widget = ScienDSpinBox()
        widget.setValue(value)
        widget.editingFinished.connect(self._emit_update_sig)
        return widget

    def _str_to_widget(self, value):
        widget = QtWidgets.QLineEdit()
        widget.setText(value)
        widget.editingFinished.connect(self._emit_update_sig)
        return widget

    def _bool_to_widget(self, value):
        widget = QtWidgets.QCheckBox()
        widget.setChecked(value)
        widget.stateChanged.connect(self._emit_update_sig)
        return widget

    def _update_widget_value(self, param_name, value):
        """
        update the value of a widget.
        """
        param_type = self.data.__dataclass_fields__[param_name]

        if param_type == int or param_type == float:
            self.data_widgets[param_name].setValue(value)
        elif param_type == str:
            self.data_widgets[param_name].setText(value)
        elif param_type == bool:
            self.data_widgets[param_name].setChecked(value)
        else:
            return

    def disconnect_widgets(self):
        for field_name, old_widget in self.data_widgets.items():
            if isinstance(old_widget, (QtWidgets.QLineEdit, ScienSpinBox, ScienDSpinBox)):
                old_widget.editingFinished.disconnect()
            elif isinstance(old_widget, QtWidgets.QCheckBox):
                old_widget.stateChanged.disconnect()
            else:
                old_widget.valueChanged.disconnect()
