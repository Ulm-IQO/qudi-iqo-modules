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
import pickle
from dataclasses import dataclass, field, fields
from PySide2.QtCore import QObject, Signal, Slot

from qudi.core.logger import get_logger

from qudi.util.datastorage import NpyDataStorage

@dataclass
class CustomDataclass:
    name: str = ""

    def from_dict(self, data_dict: dict):
        for key, value in data_dict.items():
            setattr(self, key, value)

    def to_dict(self):
        """Convert the dataclass to a dictionary excluding `_settings_updated_sig`."""
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if not field.metadata.get("exclude", False)
        }

class DataclassMediator(QObject):
    """

    """
    data_updated_sig = Signal()
    def __init__(self, parent=None):
        self.data = None
        self.data_container = None
        pass

    @property
    def current_data(self):
        return self.data

    @Slot(dict)
    def update_values(self, new_dc_dict):
        self.data.from_dict(new_dc_dict)

    def set_values(self, new_dc_dict):
        """
        Use this function to directly set values.
        """
        self.update_values(new_dc_dict)
        self.data_updated_sig.emit()

    def create_default(self, dataclass_cls):
        self.data = dataclass_cls()

class DataclassStorage:


    def __init__(self, save_path, data_container):
        self._save_path = save_path
        self._data_container = data_container
        self.log = get_logger(__name__)

    def save(self):
        try:

            with open(self._save_path, 'wb') as f:
                pickle.dump(self._data_container, f)

        except EOFError:
            self.log.error(f"cannot save settings to {self._save_path}")

    def load(self):
        try:
            with open(self._save_path, 'rb') as f:
                self._data_container = pickle.load(f)

        except EOFError:
            self.log.error(f"cannot load settings from {self._save_path}")


class DataclassManager:

    def __init__(self, mediator, save_path):
        self._mediator = mediator
        self._data_storage = DataclassStorage(save_path, self._mediator.data_container)

    def load_data_container(self):
        self._data_storage.load()

    def save_data_container(self):
        self._data_storage.save()