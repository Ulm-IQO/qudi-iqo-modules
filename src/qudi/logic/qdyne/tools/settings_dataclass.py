# -*- coding: utf-8 -*-
"""
This file contains the Qudi Manager class.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-core/>

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
from copy import deepcopy
from PySide2.QtCore import Signal, Slot

from qudi.logic.qdyne.tools.custom_dataclass import DataclassMediator


class SettingsMediator(DataclassMediator):
    """Extended DataclassManager class to provide multiple settings modes.

    This class can manage several sets of settings modes sharing the same dataclass,
    but not different types of classes.
    """
    mode_updated_sig = Signal(str)

    def __init__(self, parent):
        """Initialize the dataclass mediator with the corresponding widget."""

        super().__init__(parent)
        if hasattr(self, "_data"):
            del self._data

        self._mode_dict = dict()
        self.current_mode = "default"

    @property
    def current_data(self):
        """Current data handled by this class.

        In SettingsMediator, this is given by the currently selected mode
        inside the mode dictionary.
        """

        return self.mode_dict[self.current_mode]

    @property
    def mode_dict(self):
        """Current mode dictionary."""
        return self._mode_dict

    @property
    def data_container(self):
        """Data container of the class.

        This data will be saved in a data sorage.
        The data in a data sotrage will be loaded here.
        """
        return self.mode_dict

    @data_container.setter
    def data_container(self, data_container):
        self.mode_dict = data_container

    @property
    def default_data(self):
        return self.mode_dict["default"]

    @Slot(str)
    def update_mode(self, new_mode: str):
        """Update mode from the new mode from widget."""
        self.current_mode = new_mode
        self.data_updated_sig.emit(self.current_data.to_dict())

    def set_mode(self, new_mode: str):
        """Set mode from logic."""
        self.update_mode(new_mode)
        self.mode_updated_sig.emit(new_mode)

    @Slot(str)
    def add_mode(self, new_mode_name):
        if new_mode_name not in self.mode_dict:
            self.mode_dict[new_mode_name] = deepcopy(self.default_data)
            self.set_mode(new_mode_name)

        else:
            self._log.error('Name already taken in settings modes')

    @Slot(str)
    def delete_mode(self, mode_name: str):
        if mode_name not in self.mode_dict:
            self._log.error("Name not found in settings modes")
            return
        else:
            if mode_name == "default":
                self._log.error("Cannot delete default mode")
            else:
                try:
                    new_mode = self.find_key_before(self.mode_dict, mode_name) #get the mode one before
                except KeyError:
                    new_mode = "default"
                self.mode_dict.pop(mode_name)
                self.set_mode(new_mode)

    @staticmethod
    def find_key_before(d: dict, k: str):
        it = iter(d)
        for key in it:
            if key == k:
                break
            previous_key = key
        return previous_key

    @property
    def mode_list(self):
        return list(self.mode_dict.keys())

    def create_default(self, dataclass_cls):
        """
        create default settings mode from initialized dataclass.
        """
        self.mode_dict["default"] = dataclass_cls()

    def load_from_dict(self, dataclass_cls, mode_map):
        """Load data from dict."""
        for mode in mode_map:
            self.mode_dict[mode] = dataclass_cls(**mode_map[mode])

    def dump_as_dict(self):
        mode_map = dict()
        for mode in self.data_container:
            mode_map[mode] = self.data_container[mode].to_dict()
        return mode_map
