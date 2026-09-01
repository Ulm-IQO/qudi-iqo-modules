# -*- coding: utf-8 -*-
"""Backwards-compatible re-exports of the qdyne data containers.

The containers themselves moved to `qudi.logic.qdyne.qdyne_data.measurement_data`, alongside the
settings containers, so everything qdyne persists or passes around lives in one package. This module
stays as a thin re-export because `MainDataClass` and `QDyneMetadata` are imported by name from
several places.

Import from `qudi.logic.qdyne.qdyne_data` in new code.

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
from qudi.logic.qdyne.qdyne_data.measurement_data import (
    DATA_TYPES,
    FreqDomainData,
    MainDataClass,
    MeasurementChunk,
    QDyneMetadata,
)

__all__ = [
    'DATA_TYPES',
    'FreqDomainData',
    'MainDataClass',
    'MeasurementChunk',
    'QDyneMetadata',
]
