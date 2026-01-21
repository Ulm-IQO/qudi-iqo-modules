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
from PySide2.QtCore import Signal, Slot

from qudi.logic.qdyne.tools.settings_dataclass import SettingsMediator


class MultiSettingsMediator(SettingsMediator):
    """A class to manage multiple settings dataclasses.

    These settings could be different and called methods.
    Each method can also have several modes.
    """
    method_updated_sig = Signal(str)
    data_renewed_sig = Signal(object)

    def __init__(self, parent):
        """Initialize the dataclass mediator with the corresponding widget."""
        super().__init__(parent)
        if hasattr(self, "_mode_dict"):
            del self._mode_dict

        self.current_method = "default"
        self.method_dict = dict() #2D dict [method][mode]

    @property
    def current_data(self):
        """Current data handled by this class.

        In MultiSettingsMediator, this is given by the currently selected method and mode.
        """
        if self.current_method in self.method_dict:
            if self.current_mode in self.method_dict[self.current_method]:
                return self.method_dict[self.current_method][self.current_mode]
            else:
                self._log.error(f"Mode {self.current_mode} not found. Use default mode.")
                return self.method_dict[self.current_method]["default"]
        else:
            self._log.error(f"Method {self.current_method} not implemented.")
            return None

    @property
    def mode_dict(self):
        """Current mode dictionary.

        In MultiSettingsMediator, this is the mode dictionary of currently slected method.
        """
        return self.method_dict[self.current_method]

    @property
    def data_container(self):
        """Data container of the class.

        This data will be saved in a data sorage.
        The data in a data sotrage will be loaded here.
        """
        return self.method_dict

    @data_container.setter
    def data_container(self, data_container):
        self.method_dict = data_container

    @Slot(str)
    def update_method(self, new_method: str):
        self._log.debug(f"update_method {new_method=}")
        self.current_method = new_method
        self.current_mode = "default"
        self.mode_updated_sig.emit("default")
        self._log.warning(f"{self.current_data=}")
        self.data_renewed_sig.emit(self.current_data)

    def set_method(self, new_method: str):
        self.update_method(new_method)
        self.method_updated_sig.emit(new_method)

    @property
    def method_list(self):
        return list(self.method_dict.keys())

    def create_default(self, dataclass_cls_dict: dict):
        """Create default method dictionary from a dictionary with different dataclasses.

        Parameters
        ----------
        dataclass_cls_dict : dict
            Dictionary of dataclass to be used as different methods.
        """
        for key in dataclass_cls_dict:
            self.method_dict[key] = self._create_default(dataclass_cls_dict[key])

    def load_from_dict(self, dataclass_cls_dict, method_map):
        """Load data from dict."""
        self._log.debug(f"load_from_dict {dataclass_cls_dict=}, {method_map=}")

        for method in dataclass_cls_dict:
            dataclass_cls = dataclass_cls_dict[method]
            mode_map = method_map[method]

            mode_dict = dict()
            for mode in method_map[method].keys():
                mode_dict[mode] = dataclass_cls(**mode_map[mode])
            self.method_dict[method] = mode_dict

    def dump_as_dict(self):
        method_map = dict()
        for method in self.data_container:
            mode_map = dict()
            for mode in self.data_container[method]:
                mode_map[mode] = self.data_container[method][mode].to_dict()
            method_map[method] = mode_map

        return method_map
