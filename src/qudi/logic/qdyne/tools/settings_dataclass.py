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
from dataclasses import dataclass, field, fields
from PySide2.QtCore import QObject, Signal, Slot

from qudi.logic.qdyne.tools.custom_dataclass import CustomDataclass, DataclassMediator


class Settings(CustomDataclass):
    pass


class SettingsMediator(DataclassMediator):
    """
    Extended DataclassManager class to provide multiple settings modes.
    This class can manage several sets of settings modes sharing the same dataclass,
    but not different types of classes.
    """
    mode_updated_sig = Signal()

    def __init__(self):
        super().__init__()
        self.current_mode = "default"
        self.mode_dict = dict()
        self.data = self.mode_dict[self.current_mode]
        self.data_container = self.mode_dict

    @Slot(str)
    def update_mode(self, new_mode: str):
        self.current_mode = new_mode

    def set_mode(self, new_mode: str):
        self.update_mode(new_mode)
        self.mode_updated_sig.emit(new_mode)

    def add_mode(self, new_mode_name, new_setting):
        if new_mode_name not in self.mode_dict:
            self.mode_dict[new_mode_name] = new_setting
        else:
            self._log.error('Name already taken in settings modes')

    def remove_mode(self, mode_name: str):
        self.mode_dict.pop(mode_name)

    @property
    def mode_list(self):
        return list(self.mode_dict.keys())

    def create_default(self, dataclass_cls):
        """
        create default settings mode from initialized dataclass.
        """
        self.mode_dict["default"] = dataclass_cls()
