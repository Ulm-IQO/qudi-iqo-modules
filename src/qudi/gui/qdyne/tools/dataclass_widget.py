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
from PySide6 import QtWidgets
from PySide6.QtCore import Signal, Slot, QSize
from qudi.core.logger import get_logger
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox


class DataclassWidget(QtWidgets.QWidget):
    """Data widget class which can create widgets from a dataclass object."""
    data_widget_synced_sig = Signal(dict) #used when data widgets are synced from mediator
    data_widget_refreshed_sig = Signal(dict) #used when mediator has to be changed

    def __init__(self, mediator, dataclass_obj=None) -> None:
        """Initialize the dataclass widget with the corresponding mediator.

        Parameters
        ----------
        mediator : MediatorBridge
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
        self.mediator.data_updated_sig.connect(self.sync_data_widgets)

    def disconnect_signals(self):
        self.disconnect_signals_from_mediator()
        self.disconnect_signals_from_widgets()

    def disconnect_signals_from_mediator(self):
        self.mediator.data_updated_sig.disconnect()

    def _emit_data_widget_refreshed_sig(self):
        """Emit data widget refreshed signal.
        This signal is emitted when the widget refreshed and the dataclass has to be synchronized..
        """
        self.data_widget_refreshed_sig.emit(self.values_dict)

    def _emit_data_widget_synced_sig(self):
        """Emit data widget synchronized signal.
        This signals is emitted when the widget is synchronized from the dataclass
        and no synchronization of the dataclass is needed.
        By default, this is not connected to anything.
        data_widget_synced_sig can be used when additional synchronization with another widget(e.g. line widgets).
        """
        self.data_widget_synced_sig.emit(self.values_dict)
    @property
    def values_dict(self):
        """Get the current values of the widget in a dictionary."""
        values_dict = dict()
        for key in self.data_widgets.keys():
            values_dict[key] = self._get_widget_value(key)
        return values_dict

    @Slot(dict)
    def refresh_data_widgets(self, data_dict):
        """Refresh the data widgets.
        """
        self.setUpdatesEnabled(False)
        self._set_data_widgets(data_dict)
        self._emit_data_widget_refreshed_sig()
        self.setUpdatesEnabled(True)

    @Slot(dict)
    def sync_data_widgets(self, data_dict):
        """Sync the data widgets from mediator.
        No signal will be sent to the mediator.
        """
        self.setUpdatesEnabled(False)
        self._set_data_widgets(data_dict)
        self._emit_data_widget_synced_sig()
        self.setUpdatesEnabled(True)


    def _set_data_widgets(self, data_dict):
        """Set the parameters of widgets according to the data.
        This coule be a partial set of dataclass.
        """
        for param_name in data_dict.keys():
            self._set_data_widget_value(param_name, data_dict[param_name])

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
            # `and`, not `or`. The original read "include unless (private AND excluded)", so an
            # excluded field was still offered an editor - `weight` (exclude=True) came through.
            # It never showed up only because `list` has no widget type and _create_widget()
            # returns None, but any excluded str/int/float/bool field would have appeared, and it
            # disagreed with to_display_dict(), which honours the marker correctly.
            if not field.name.startswith("_") and not field.metadata.get("exclude"):
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

        # A field that advertises a fixed set of values gets a drop-down. Rendering these as free
        # text meant a typo reached a frozen dataclass that raises ValueError on an unknown value -
        # out of __post_init__, through the mediator, and into the Qt event loop unguarded.
        choices = field.metadata.get('choices')
        if choices:
            widget = self._choices_to_widget(value, choices)
        elif field.type == int:
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

    def _choices_to_widget(self, value, choices):
        widget = QtWidgets.QComboBox()
        widget.addItems([str(choice) for choice in choices])
        widget.setCurrentText(str(value))
        return widget

    def _set_data_widget_value(self, param_name, value):
        """
        set the value of a widget.
        """
        if hasattr(self.dataclass_obj, param_name):
            param_type = self.dataclass_obj.__dataclass_fields__[param_name].type
            widget = self.data_widgets[param_name]

            # Dispatched on the WIDGET first: a `choices` field is a str whose editor is a combo
            # box, so going by field type alone would call setText() on something that has none.
            if isinstance(widget, QtWidgets.QComboBox):
                widget.setCurrentText(str(value))
            elif param_type == int or param_type == float:
                widget.setValue(value)
            elif param_type == str:
                widget.setText(value)
            elif param_type == bool:
                widget.setChecked(value)
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
            widget = self.data_widgets[param_name]

            # Widget first - see _set_data_widget_value().
            if isinstance(widget, QtWidgets.QComboBox):
                return widget.currentText()
            elif param_type == int or param_type == float:
                return widget.value()
            elif param_type == str:
                return widget.text()
            elif param_type == bool:
                return widget.isChecked()
            else:
                self._log.debug(f"{param_type} type is not supported.")
                return None
        else:
            self._log.error("name not found in data.")

    def connect_signals_from_widgets(self):
        # set_values(), not sync_values(). The two differ deliberately: sync_values() stores without
        # notifying, which is right when the MEDIATOR told the widget and the widget is echoing
        # back, but wrong for a USER edit - observers have to hear about that. QdyneMeasurement
        # subscribes to on_data precisely so it can drop the accumulated pulse histogram when the
        # binning changes; with sync_values() that never fired for a GUI edit.
        # (StateEstimationSettingsWidget used to work around this by overriding
        # _emit_data_widget_refreshed_sig to call set_values directly - which silently stopped the
        # signal being emitted at all and left two of its own connections dead.)
        self.data_widget_refreshed_sig.connect(self.mediator.set_values)

        for field_name, widget in self.data_widgets.items():
            if isinstance(widget, QtWidgets.QComboBox):
                widget.currentTextChanged.connect(self._emit_data_widget_refreshed_sig)
            elif isinstance(widget, (QtWidgets.QLineEdit, ScienSpinBox, ScienDSpinBox)):
                widget.editingFinished.connect(self._emit_data_widget_refreshed_sig)
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.stateChanged.connect(self._emit_data_widget_refreshed_sig)
            else:
                widget.valueChanged.connect(self._emit_data_widget_refreshed_sig)

    def disconnect_signals_from_widgets(self):
        self.data_widget_refreshed_sig.disconnect()

        # Mirrors connect_signals_from_widgets() branch for branch. It used to disconnect a
        # different signal than it connected for some widget kinds, which is what made the blanket
        # `except` below necessary - it was swallowing "failed to disconnect" for signals that had
        # never been connected, while the real connections stayed live.
        for field_name, widget in self.data_widgets.items():
            try:
                if isinstance(widget, QtWidgets.QComboBox):
                    widget.currentTextChanged.disconnect()
                elif isinstance(widget, (QtWidgets.QLineEdit, ScienSpinBox, ScienDSpinBox)):
                    widget.editingFinished.disconnect()
                elif isinstance(widget, QtWidgets.QCheckBox):
                    widget.stateChanged.disconnect()
                else:
                    widget.valueChanged.disconnect()
            except RuntimeError:
                # Qt raises this when there was nothing connected - harmless, and no longer the
                # normal case. Narrowed from `except Exception` so a real fault is visible.
                self._log.debug(f"Nothing was connected to the '{field_name}' widget.")
