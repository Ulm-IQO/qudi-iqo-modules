# -*- coding: utf-8 -*-
"""
This module contains a Qdyne manager class.
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

import numpy as np

from dataclasses import dataclass
from scipy import signal

@dataclass
class QDyneMetadata:
    measurement_settings: dict = {}
    state_estimation_settings: dict = {}
    time_trace_analysis_settings: dict = {}


class FreqDomainData:
    def __init__(self):
        """"""
        self.x = None
        self.y = None
        self.peaks = []
        self.current_peak = 0
        self.range_index = 10

    def get_peaks(self):
        """
        find the peaks of the non-negative frequency domain signal.
        """
        height = max(self.y[1:])
        self.peaks = signal.find_peaks(self.y, height=height)[0]
        if len(self.peaks) == 0:
            self.peaks = [int(np.argmax(self.y)),]

    @property
    def data_around_peak(self):
        start_index = max(0, self.current_peak - self.range_index)
        end_index = min(
            self.x.size,
            self.current_peak
            + self.range_index
            + 1,  # +1 because slicing is end exclusive
        )
        x_peak = self.x[start_index:end_index]
        y_peak = self.y[start_index:end_index]
        return [x_peak, y_peak]


@dataclass
class MainDataClass:
    raw_data: np.ndarray = np.array([])
    extracted_data: np.ndarray = np.array([])
    pulse_data: np.ndarray = np.array([])
    time_trace: np.ndarray = np.array([])
    signal: np.ndarray = np.array([])
    freq_domain: np.ndarray = np.array([])
    time_domain: np.ndarray = np.array([])
    freq_data: FreqDomainData = FreqDomainData()
    metadata: QDyneMetadata = QDyneMetadata()

    @property
    def data_list(self):
        return [
            attr
            for attr in dir(self.__class__)
            if not attr.startswith("__") and not callable(getattr(self, attr))
        ]

    def reset(self):
        self.__init__()
