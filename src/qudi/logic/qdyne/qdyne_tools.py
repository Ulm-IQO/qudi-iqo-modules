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
import importlib
import inspect
import sys

from abc import ABC
from dataclasses import dataclass, field, fields

def get_subclasses(module_name, class_obj):
    """
    Given a class, find its subclasses and get their names.
    """

    subclasses = []
    module = sys.modules.get(module_name)
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if inspect.isclass(obj) and issubclass(obj, class_obj) and obj != class_obj:
            subclasses.append(obj)

    return subclasses


def get_method_names(subclass_obj, class_obj):
    subclass_names = [cls.__name__ for cls in subclass_obj]
    method_names = [
        subclass_name.replace(class_obj.__name__, "")
        for subclass_name in subclass_names
    ]
    return method_names


def get_method_name(subclass_obj, class_obj):
    subclass_name = subclass_obj.__name__
    method_name = subclass_name.replace(class_obj.__name__, "")
    return method_name

@dataclass
class SettingsBase(ABC):
    _settings_updated_sig: object = field(repr=False, metadata={"exclude": True})  # Mark to exclude
    name: str = ""

    def __setattr__(self, key, value):
        if hasattr(self, key) and hasattr(self, "_settings_updated_sig") and key != "_settings_updated_sig":
            old_value = getattr(self, key)
            if old_value != value:
                self._settings_updated_sig.emit()

        super().__setattr__(key, value)

    def from_dict(self, settings: dict):
        for key, value in settings.items():
            setattr(self, key, value)

    def to_dict(self):
        """Convert the dataclass to a dictionary excluding `_settings_updated_sig`."""
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if not field.metadata.get("exclude", False)
        }
