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


class FreqDomainData:
    def __init__(self):
        """"""
        self.x = None
        self.y = None
        self.peaks = []
        self.current_peak = 0
        self.range_index = 10
        self.peak_factor = 10

    def get_peaks(self):
        mean = self.y.mean()
        std = self.y.std()
        height = mean + self.peak_factor * std
        self.peaks = signal.find_peaks(self.y, height=height)[0]

    @property
    def data_around_peak(self):
        x_peak = self.x[self.current_peak - self.range_index: self.current_peak + self.range_index]
        y_peak = self.y[self.current_peak - self.range_index: self.current_peak + self.range_index]
        return [x_peak, y_peak]

@dataclass
class MainDataClass:
    raw_data: np.ndarray = np.array([], dtype=int)
    extracted_data: np.ndarray = np.array([], dtype=int)
    pulse_data: np.ndarray = np.array([], dtype=int)
    time_trace: np.ndarray = np.array([], dtype=float)
    signal: np.ndarray = np.array([], dtype=float)
    spectrum: np.ndarray = np.array([], dtype=float)

    def __init__(self):
        self.freq_data = FreqDomainData()

    @property
    def data_list(self):
        return [attr for attr in dir(self.__class__) if not attr.startswith('__')
                and not callable(getattr(self, attr))]


    def load_np_data(self, path):
        self.raw_data = np.load(path)['arr_0']

    def load_spectrum(self, path):
        self.spectrum = np.load(path)

