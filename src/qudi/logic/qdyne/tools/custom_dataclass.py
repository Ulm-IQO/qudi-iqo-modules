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
import os
import pickle
from dataclasses import dataclass, fields
from PySide2.QtCore import QObject, Signal, Slot

from qudi.core.logger import get_logger


@dataclass
class CustomDataclass:
    """Custom dataclass for mediator class.

    If you want to add a parameter hidden in the widget, you can define it as below:
    param: type = field(metadata={"exclude": True})
    """
    name: str = ""

    def from_dict(self, data_dict: dict):
        """Update values from dictionary if the key is contained in the dataclass.

        Partial update of parameters are also possible.
        """
        for key, value in data_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                pass

    def to_dict(self):
        """Convert the dataclass to a dictionary excluding parameters unwanted for the widgets."""
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if not field.metadata.get("exclude", False)
        }


class DataclassMediator(QObject):
    """Dataclass mediator class communicating with gui widgets.

    """
    data_updated_sig = Signal(dict)

    def __init__(self, parent):
        """Initialize the dataclass mediator with the corresponding widget."""

        super().__init__(parent)
        self._log = get_logger(__name__)
        self._data = None

    @property
    def current_data(self):
        """Current data handled by this class."""
        return self._data

    @property
    def data_container(self):
        """Data container of the class.

        This data will be saved in a data sorage.
        The data in a data sotrage will be loaded here.
        """
        return self._data

    @data_container.setter
    def data_container(self, data_container):
        self._data = data_container

    @Slot(dict)
    def sync_values(self, new_dc_dict):
        """
        sync values of dataclass according to new_dc_dict from gui.
        """
        self.current_data.from_dict(new_dc_dict)

    @Slot(dict)
    def set_values(self, new_dc_dict):
        """
        Use this function to directly set values.
        """
        self.update_values(new_dc_dict)
        self.data_updated_sig.emit(self.current_data.to_dict())

    def create_default(self, dataclass_cls):
        self._data = dataclass_cls()

    def set_single_value(self, param_name, value):
        """
        update a single value in the dataclass.
        """
        new_dc_dict = self.current_data.to_dict()
        if param_name in new_dc_dict:
            new_dc_dict[param_name] = value
            self.update_values(new_dc_dict)
            self.data_updated_sig.emit(self.current_data.to_dict())

        else:
            self._log.error(f"Parameter {param_name} not found in dataclass.")

    def load_from_dict(self, data_dict):
        """Load data from dictionary."""
        self.set_values(data_dict)

    def dump_as_dict(self):
        """dump the data container as a dictionary"""
        return self.data_container.to_dict()


