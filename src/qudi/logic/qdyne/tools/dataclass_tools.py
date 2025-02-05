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
import inspect
import sys


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
