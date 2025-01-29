# -*- coding: utf-8 -*-

"""
ToDo: Document

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

import numpy as np
from typing import Any


def convert_nested_numpy_to_list(data: Any) -> Any:
    """
    Recursively convert all numpy arrays in a nested dictionary or list to Python lists.

    This function traverses the given data structure and replaces any `numpy.ndarray`
    values with their equivalent Python lists while preserving the overall structure.
    If the input is neither a dict or list, it is returned unchanged.

    Parameters
    ----------
    data : Any
        A dictionary or list that may contain nested `numpy.ndarray` values.

    Returns
    -------
    Any
        A new dictionary or list where all `numpy.ndarray` values are converted to lists.
        If `data` is neither a dict or list, it is returned unchanged.

    """
    if isinstance(data, dict):
        return {key: convert_nested_numpy_to_list(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [convert_nested_numpy_to_list(item) for item in data]
    elif isinstance(data, np.ndarray):
        return data.tolist()
    else:
        return data
