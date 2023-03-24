# -*- coding: utf-8 -*-
"""
This file contains the Qudi logic for .
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

class TimeSeriesDataProcessor:
    pass

class TimeTagDataProcessor:
    def __init__(self):
        # self.count_length = 2000
        # self.start_count = 0
        # self.stop_count = self.start_count + self.count_length
        # self.count_threshold = 90000
        # return

    def photon_count(self, time_tag, start_count, stop_count, count_threshold=90000):
        counts_time_trace = []
        counts_time_trace_append = counts_time_trace.append
        photon_counts = 0
        for i in range(len(time_tag)):  # count and filter the photons here
            if time_tag[i] != 0 and time_tag[i] < count_threshold:
                if start_count < time_tag[i] < stop_count:
                    photon_counts = photon_counts + 1
            else:
                counts_time_trace_append(photon_counts)
                photon_counts = 0
        return counts_time_trace
    def weighted_photon_count(self, time_tag, weight, start_count, stop_count, count_threshold=90000):
        counts_time_trace = []
        counts_time_trace_append = counts_time_trace.append
        photon_counts = 0
        for i in range(len(time_tag)):  # count and filter the photons here
            if time_tag[i] != 0 and time_tag[i] < count_threshold:
                if start_count < time_tag[i] < stop_count:
                    photon_counts = photon_counts + weight[i]
            else:
                counts_time_trace_append(photon_counts)
                photon_counts = 0
        return counts_time_trace

class RawDataProcessor:

    def configure(self):
        pass

    def process(self):

        return time_trace
