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
