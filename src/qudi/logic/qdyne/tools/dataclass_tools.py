# -*- coding: utf-8 -*-
"""
This file contains the tools to support dataclass.
Here, methods are subclasses of a dataclass

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
import inspect
import sys

__all__ = [
    'MethodRegistry',
    'get_subclasses',
    'get_subclass_qualifier',
    'get_subclass_dict',
]


class MethodRegistry:
    """Pairs each method name with BOTH its implementation and its settings dataclass.

    The functions below discover settings classes and implementation classes by scraping a module
    for subclasses - two independent scans that nothing forces to agree. A settings class whose
    implementation was commented out therefore made the GUI advertise a method that raised the
    moment it was selected, and no test could see the mismatch because each scan was individually
    correct.

    Registering the pair makes that impossible to express: there is one entry, or there is none.
    """

    def __init__(self, what: str = 'method'):
        self._what = what
        self._entries = {}

    def register(self, name: str, implementation: type, settings: type) -> type:
        """Register a method. Returns `implementation`, so this can be used as a decorator factory."""
        if name in self._entries:
            raise ValueError(f'{self._what} {name!r} is already registered.')
        self._entries[name] = (implementation, settings)
        return implementation

    @property
    def names(self) -> list:
        return list(self._entries)

    @property
    def settings_classes(self) -> dict:
        """{method name: settings dataclass} - what a SettingsMediator is built from."""
        return {name: settings for name, (_impl, settings) in self._entries.items()}

    def implementation(self, name: str) -> type:
        try:
            return self._entries[name][0]
        except KeyError:
            raise ValueError(
                f'No {self._what} implementation registered for {name!r}. '
                f'Available: {sorted(self._entries)}.'
            ) from None

    def settings_class(self, name: str) -> type:
        try:
            return self._entries[name][1]
        except KeyError:
            raise ValueError(
                f'No {self._what} settings registered for {name!r}. '
                f'Available: {sorted(self._entries)}.'
            ) from None

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)


def get_subclasses(module_name, parent_cls):
    """
    Find subclasses of a parent class defined in a module.
    """

    subclasses = []
    module = sys.modules.get(module_name)
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if inspect.isclass(obj) and issubclass(obj, parent_cls) and obj != parent_cls:
            subclasses.append(obj)

    return subclasses


def get_subclass_qualifier(subclass_cls, parent_cls):
    """
    Remove the part of subclass name common to parent class name.
    """

    subclass_name = subclass_cls.__name__
    try:
        subclass_qualifier = subclass_name.replace(parent_cls.__name__, "")
    except ValueError:
        subclass_qualifier = subclass_name
    return subclass_qualifier


def get_subclass_dict(module_name, parent_cls):
    """
    get a dictionary of subclasses defined in a moudle.
    """
    subclass_dict = dict()
    subclasses = get_subclasses(module_name, parent_cls)
    for subclass_cls in subclasses:
        subclass_qualifier = get_subclass_qualifier(subclass_cls, parent_cls)
        subclass_dict[subclass_qualifier] = subclass_cls
    return subclass_dict
