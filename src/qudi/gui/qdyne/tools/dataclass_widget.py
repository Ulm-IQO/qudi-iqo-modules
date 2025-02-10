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
from dataclasses import fields
from PySide2 import QtWidgets
from PySide2.QtCore import Signal, Slot, QSize
from qudi.core.logger import get_logger
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox


class DataclassWidget(QtWidgets.QWidget):
    """Data widget class which can create widgets from a dataclass object."""
    data_widget_updated_sig = Signal(dict)

    def __init__(self, mediator, dataclass_obj=None) -> None:
        """Initialize the dataclass widget with the corresponding mediator.

        Parameters
        ----------
        mediator : DataclassMediator
            mediator class object to communicate with a dataclass.
        dataclass_obj : dataclass
            dataclass object for creation of initial widgets.
            When None is passed, no widget is created. set_data should be called later.
        """
        super().__init__()
        self._log = get_logger(__name__)
        self.dataclass_obj = dataclass_obj
        self.mediator = mediator
        self.layout_main = None
        self.data_labels = dict()
        self.data_widgets = dict()
        self.layouts = dict()

        self.labels = dict()
        self.widgets = dict()

        self.init_widgets()
        self.show()

    def init_widgets(self):
        """Initialize the widgets from self.dataclass_obj."""
        self.create_widgets()
        self.arange_layout()

    def create_widgets(self):
        if self.dataclass_obj is not None:
            self.create_data_widgets(self.dataclass_obj)
        else:
            return

    def arange_layout(self):
        self.layout_main = QtWidgets.QGridLayout()
        self.layout_main.addLayout(self.create_data_layout())

        self.setLayout(self.layout_main)

    def create_data_layout(self):
        """
        create grid layout for names and parameters of a dataclass.
        """
        data_layout = QtWidgets.QGridLayout()
        param_index = 0

        for param_key in self.data_labels.keys():
            data_layout.addWidget(self.data_labels[param_key], 0, param_index + 1, 1, 1)
            data_layout.addWidget(self.data_widgets[param_key], 1, param_index + 1, 1, 1)
            param_index += 1

        return data_layout

    def connect_signals(self):
        self.connect_signals_from_mediator()
        self.connect_signals_from_widgets()

    def connect_signals_from_mediator(self):
        self.mediator.data_updated_sig.connect(self.update_widgets)

    def disconnect_signals(self):
        self.disconnect_signals_from_mediator()
        self.disconnect_signals_from_widgets()

    def disconnect_signals_from_mediator(self):
        self.mediator.data_updated_sig.disconnect()

    def _emit_update_sig(self):
        self.data_widget_updated_sig.emit(self.values_dict)

    @property
    def values_dict(self):
        """Get the current values of the widget in a dictionary."""
        values_dict = dict()
        for key in self.data_widgets.keys():
            values_dict[key] = self._get_widget_value(key)
        return values_dict

    @Slot(dict)
    def set_data_from_dict(self, data_dict):
        """
        set data from a dictionary. This coule be a partial set of dataclass.
        """
        self.setUpdatesEnabled(False)
        for key in data_dict.keys():
            self._update_widget_value(key, data_dict[key])
        self._emit_update_sig()
        self.setUpdatesEnabled(True)

    @Slot(dict)
    def update_widgets(self, data_dict):
        """
        update the parameters of widgets according to the data.
        """
        for param_name in data_dict.keys():
            self._update_widget_value(param_name, data_dict[param_name])

    def _clear_layout(self, layout):
        """
        remove widgets in data_layout.
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                item.layout().deleteLater()

    def create_data_widgets(self, dataclass_obj):
        """
        create widgets based on dataclass
        """
        self.data_labels = dict()
        self.data_widgets = dict()
        for field in fields(dataclass_obj):
            if not field.name.startswith("_") or not field.metadata.get("exclude"):
                label = self._create_label(field.name)
                widget = self._create_widget(field)
                if widget is None:
                    continue
                widget.setMinimumSize(QSize(80, 0))
                if field.name == "name":
                    widget.setReadOnly(True)

                self.data_labels[field.name] = label
                self.data_widgets[field.name] = widget

    def _create_widget(self, field):
        """
        create widget based on the field of parameter.
        """
        widget = None
        value = getattr(self.dataclass_obj, field.name)

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
        return widget

    def _float_to_widget(self, value):
        widget = ScienDSpinBox()
        widget.setValue(value)
        return widget

    def _str_to_widget(self, value):
        widget = QtWidgets.QLineEdit()
        widget.setText(value)
        return widget

    def _bool_to_widget(self, value):
        widget = QtWidgets.QCheckBox()
        widget.setChecked(value)
        return widget

    def _update_widget_value(self, param_name, value):
        """
        update the value of a widget.
        """
        if hasattr(self.dataclass_obj, param_name):
            param_type = self.dataclass_obj.__dataclass_fields__[param_name].type

            if param_type == int or param_type == float:
                self.data_widgets[param_name].setValue(value)
            elif param_type == str:
                self.data_widgets[param_name].setText(value)
            elif param_type == bool:
                self.data_widgets[param_name].setChecked(value)
            else:
                self._log.debug(f"{param_type} type is not supported.")
        else:
            self._log.error("name not found in data.")

    def _get_widget_value(self, param_name):
        """
        update the value of a widget.
        """
        if hasattr(self.dataclass_obj, param_name):
            param_type = self.dataclass_obj.__dataclass_fields__[param_name].type

            if param_type == int or param_type == float:
                return self.data_widgets[param_name].value()
            elif param_type == str:
                return self.data_widgets[param_name].text()
            elif param_type == bool:
                return self.data_widgets[param_name].isChecked()
            else:
                self._log.debug(f"{param_type} type is not supported.")
                return None
        else:
            self._log.error("name not found in data.")

    def connect_signals_from_widgets(self):
        self.data_widget_updated_sig.connect(self.mediator.update_values)

        for field_name, widget in self.data_widgets.items():
            if isinstance(widget, (QtWidgets.QLineEdit, ScienSpinBox, ScienDSpinBox)):
                widget.editingFinished.connect(self._emit_update_sig)
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.stateChanged.connect(self._emit_update_sig)
            else:
                widget.valueChanged.connect(self._emit_update_sig)

    def disconnect_signals_from_widgets(self):
        self.data_widget_updated_sig.disconnect()

        for field_name, widget in self.data_widgets.items():
            if isinstance(widget, (QtWidgets.QLineEdit, ScienSpinBox, ScienDSpinBox)):
                widget.editingFinished.disconnect()
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.stateChanged.disconnect()
            else:
                widget.valueChanged.disconnect()
