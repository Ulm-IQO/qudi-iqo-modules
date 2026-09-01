# -*- coding: utf-8 -*-

"""

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
from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QLabel, QComboBox, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit

from qudi.gui.qdyne.tools.dataclass_widget import DataclassWidget
from qudi.logic.qdyne.qdyne_data.settings_base import QdyneSettingsBase
from qudi.logic.qdyne.tools.settings_mediator import DEFAULT_MODE


class SettingsWidget(DataclassWidget):
    """Data widget class for settings widget.

    Several modes of settings can be handled.
    Modes are variants of a dataclass.
    """
    mode_widget_updated_sig = Signal()
    add_mode_pushed_sig = Signal(str, bool, QdyneSettingsBase)
    delete_mode_pushed_sig = Signal(str)

    def __init__(self, mediator, dataclass_obj=None) -> None:
        """Initialize the dataclass widget with the corresponding mediator.

        Parameters
        ----------
        mediator : MediatorBridge
            mediator class object to communicate with a set of variants for a single dataclass.
        dataclass_obj : dataclass
            dataclass object for creation of initial widgets.
            When None is passed, no widget is created. set_data should be called later.
        """
        super().__init__(mediator, dataclass_obj)

    @property
    def current_mode(self):
        return self.widgets["mode"].currentText()

    def create_widgets(self):
        super().create_widgets()
        self.create_mode_widgets()

    def create_mode_widgets(self):
        mode_label = QLabel()
        mode_label.setText("Mode")

        mode_comboBox = QComboBox()
        mode_comboBox.addItems(self.mediator.mode_list)
        mode_comboBox.setEditable(False)

        delete_mode_pushButton = QPushButton("Delete")

        new_mode_label = QLabel()
        new_mode_label.setText("New Name")
        new_mode_lineEdit = QLineEdit()
        add_mode_pushButton = QPushButton("Add")
        add_mode_pushButton.setToolTip('Enter new name')

        self.labels["mode"] = mode_label
        self.widgets["mode"] = mode_comboBox
        self.widgets["delete_mode"] = delete_mode_pushButton
        self.labels["new_mode"] = new_mode_label
        self.widgets["new_mode"] = new_mode_lineEdit
        self.widgets["add_mode"] = add_mode_pushButton

    def arange_layout(self):
        self.layout_main = QVBoxLayout()
        self.layout_main.addLayout(self.create_header_layout())
        self.layout_main.addLayout(self.create_data_layout())

        self.setLayout(self.layout_main)

    def create_header_layout(self):
        self.layouts['header'] = self.create_mode_layout()
        return self.layouts['header']

    def create_mode_layout(self):
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.labels["mode"])
        mode_layout.addWidget(self.widgets["mode"])
        mode_layout.addWidget(self.widgets["delete_mode"])
        mode_layout.addWidget(self.labels["new_mode"])
        mode_layout.addWidget(self.widgets["new_mode"])
        mode_layout.addWidget(self.widgets["add_mode"])

        self.layouts["mode"] = mode_layout
        return mode_layout

    def connect_signals_from_mediator(self):
        super().connect_signals_from_mediator()
        self.mediator.mode_updated_sig.connect(self.update_mode_widget)

    def disconnect_signals_from_mediator(self):
        super().disconnect_signals_from_mediator()
        self.mediator.mode_updated_sig.disconnect()

    @Slot(str)
    def update_mode_widget(self, new_mode):
        """
        update the mode widget with the new mode from mediator.
        """
        self.setUpdatesEnabled(False)
        self.widgets["mode"].setCurrentText(new_mode)
        self.setUpdatesEnabled(True)

    def _add_button_pushed(self):
        self.setUpdatesEnabled(False)
        mode_to_add = self.widgets["new_mode"].text()
        if mode_to_add not in self.mediator.mode_list:
            self.widgets["mode"].addItem(mode_to_add)
            self.widgets["mode"].setCurrentText(mode_to_add)
            self.add_mode_pushed_sig.emit(mode_to_add, False, None)
        else:
            self._log.error(f"Mode {mode_to_add} name already taken.")
        self.setUpdatesEnabled(True)

    def _delete_button_pushed(self):
        mode_to_delete = self.current_mode
        if mode_to_delete == DEFAULT_MODE:
            # The button is kept disabled while this mode is selected, so this is belt and braces.
            # It used to be an early return *after* setUpdatesEnabled(False), which left Qt painting
            # switched off for this widget and every child of it - so the whole settings panel
            # stopped redrawing and looked as though it had been wiped.
            self._log.warning(
                f"'{DEFAULT_MODE}' is the base settings mode and cannot be deleted. Add a named "
                f"mode first if you want a variant to remove."
            )
            return

        index = self.widgets["mode"].findText(mode_to_delete)
        if index < 0:
            # findText returns -1 when the text is absent, and removeItem(-1) silently does nothing.
            self._log.error(f"Mode '{mode_to_delete}' is not in the mode list.")
            return

        # The mediator is told FIRST. Removing the combo item moves its current index, which fires
        # currentTextChanged -> mediator.update_mode(...) - so doing it the other way round meant
        # _current_mode had already moved on by the time delete_mode() ran, and the
        # "fall back to default if the mode we just deleted was current" branch in the mediator was
        # unreachable from here.
        self.delete_mode_pushed_sig.emit(mode_to_delete)

        # try/finally, so no path can leave painting disabled - see the default branch above for
        # what that costs.
        self.setUpdatesEnabled(False)
        try:
            self.widgets["mode"].removeItem(index)
        finally:
            self.setUpdatesEnabled(True)

    def update_delete_button_enabled(self, *_args):
        """Grey out Delete while the base mode is selected.

        `default` can never be deleted - both this widget and SettingsMediator.delete_mode() refuse
        it. Leaving the button enabled made it look broken instead of unavailable: clicking did
        nothing visible, and said nothing either, because the mediator's own
        'Cannot delete the default mode.' error was never reached.
        """
        is_default = self.current_mode == DEFAULT_MODE
        button = self.widgets["delete_mode"]
        button.setEnabled(not is_default)
        button.setToolTip(
            f"'{DEFAULT_MODE}' cannot be deleted" if is_default
            else f"Delete the '{self.current_mode}' mode"
        )

    def connect_signals_from_widgets(self):
        super().connect_signals_from_widgets()
        self.widgets["mode"].currentTextChanged.connect(lambda clicked : self.mediator.update_mode(self.current_mode))
        self.widgets["mode"].currentTextChanged.connect(self.update_delete_button_enabled)
        self.widgets["add_mode"].clicked.connect(self._add_button_pushed)
        self.widgets["delete_mode"].clicked.connect(self._delete_button_pushed)
        self.add_mode_pushed_sig.connect(self.mediator.add_mode)
        self.delete_mode_pushed_sig.connect(self.mediator.delete_mode)
        # The combo only fires on a *change*, so set the initial state by hand - otherwise Delete
        # starts out enabled on 'default', which is the state that looked broken.
        self.update_delete_button_enabled()

    def disconnect_signals_from_widgets(self):
        super().disconnect_signals_from_widgets()
        self.widgets["mode"].currentTextChanged.disconnect()
        self.widgets["add_mode"].clicked.disconnect()
        self.widgets["delete_mode"].clicked.disconnect()
        self.add_mode_pushed_sig.disconnect()
        self.delete_mode_pushed_sig.disconnect()
