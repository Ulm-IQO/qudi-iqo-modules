# -*- coding: utf-8 -*-
"""Settings containers for the qdyne state estimators.

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
from dataclasses import dataclass, field

from qudi.logic.qdyne.qdyne_data.settings_base import QdyneSettingsBase

__all__ = ['StateEstimatorSettings', 'TimeTagStateEstimatorSettings']


@dataclass(frozen=True)
class StateEstimatorSettings(QdyneSettingsBase):
    """Fields every state estimator needs, whatever its method."""

    sequence_length: float = 1e-9
    record_length: float = 1e-9
    bin_width: float = 1e-9


@dataclass(frozen=True)
class TimeTagStateEstimatorSettings(StateEstimatorSettings):
    """Settings for the time-tag estimator.

    `weight` carries one factor per time tag for the WeightedAverage count mode. It is marked
    exclude=True because the settings widget has no editor for a list - but it IS persisted, unlike
    before: `to_dict()` covers every field and only `to_display_dict()` honours the marker. Without
    that, a weight set from a script vanished on the next save/load cycle, which is most of why
    WeightedAverage was unusable.
    """

    name: str = 'default'
    count_mode: str = 'Average'
    sig_start: float = 0.0
    sig_end: float = 0.0
    weight: list = field(default_factory=list, metadata={'exclude': True})

    #: Count modes TimeTagStateEstimator implements. Kept here so the estimator and its settings
    #: cannot disagree about what is selectable.
    COUNT_MODES = ('Average', 'WeightedAverage')

    @property
    def sig_start_int(self) -> int:
        return int(self.sig_start / self.bin_width)

    @property
    def sig_end_int(self) -> int:
        return int(self.sig_end / self.bin_width)

    @property
    def max_bins(self) -> int:
        return int(self.record_length / self.bin_width)
