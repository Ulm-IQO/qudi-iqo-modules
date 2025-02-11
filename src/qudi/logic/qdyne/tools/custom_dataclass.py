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
        for key, value in data_dict.items():
            setattr(self, key, value)

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
    def update_values(self, new_dc_dict):
        """
        update values of dataclass according to new_dc_dict from gui.
        """
        self.current_data.from_dict(new_dc_dict)

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


class DataclassStorage:

    def __init__(self, save_path, data_container):
        self.save_path = save_path
        self._data_container = data_container
        self.log = get_logger(__name__)

    def save(self):
        try:

            with open(self.save_path, 'wb') as f:
                pickle.dump(self._data_container, f)

        except EOFError:
            self.log.error(f"cannot save settings to {self.save_path}")

    def load(self):
        try:
            with open(self.save_path, 'rb') as f:
                return pickle.load(f)

        except EOFError:
            self.log.error(f"cannot load settings from {self.save_path}")


class DataclassManager(QObject):
    """ Manager class for dataclass mediator.

    This class manages the slected mediator and data storage class.
    The mediator has to take care of the communication between the dataclass and gui.
    The storage class has to save and load the data in the mediator.
    """

    def __init__(self, parent, mediator_cls, save_path):
        """Initialize the dataclass with the selected mediator class.

        Parameters
        ----------
        mediator_cls
            Mediator class to be used in this manager.
        save_path : str
            Path to save the data container.
        """
        super().__init__(parent)
        self._log = get_logger(__name__)
        self.mediator = mediator_cls(parent=self)
        self._data_storage = DataclassStorage(save_path, self.mediator.data_container)

    def load_data_container(self):
        self._data_storage.load()

    def save_data_container(self):
        self._data_storage.save()

    def initialize_data_container(self, *args):
        """
        Initialize the data container.
        """
        if os.path.exists(self._data_storage.save_path):
            self.mediator.data_container = self._data_storage.load()
            self._log.debug(f"Saved settings loaded from {self._data_storage.save_path}")

        else:
            self.mediator.create_default(*args)
            self._log.debug("Default settings created")

