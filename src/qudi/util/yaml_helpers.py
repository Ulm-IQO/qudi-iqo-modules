# -*- coding: utf-8 -*-

"""
This module contains yaml representer and constructor functions to serialize custom classes, e.g. for StatusVar saving.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

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
from dataclasses import fields

from qudi.logic.pulsed.sampling_functions import PulseEnvelope

def dataclass_representer(representer, data):
    tag = f'!{data.__class__.__name__}'
    mapping = {f.name: getattr(data, f.name) for f in fields(data)}
    return representer.represent_mapping(tag, mapping)


def pulse_envelope_constructor(loader, node):
    data = loader.construct_mapping(node, deep=True)
    return PulseEnvelope.from_dict(data)
