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
        # return

    def input_settings(self, settings):
        self._count_mode = settings.count_mode
        self._count_length = settings.count_length
        self._start_count = settings.start_count
        self._stop_count = settings.stop_count
        self._count_threshold = settings.count_threshold

    def process(self, time_tag_data):
        if self._count_mode == 'Average':
            counts_time_trace = self._photon_count(time_tag_data,
                                                  self._start_count,
                                                  self._stop_count,
                                                  self._count_threshold)
        elif self._count_mode == 'WeightedAverage':
            counts_time_trace = self._weighted_photon_count(time_tag_data,
                                                           self._weight,
                                                           self._start_count,
                                                           self._stop_count,
                                                           self._count_threshold)
        return counts_time_trace

    def _photon_count(self, time_tag, start_count, stop_count, count_threshold=90000):
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
    def _weighted_photon_count(self, time_tag, weight, start_count, stop_count, count_threshold=90000):
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

    def configure_data_type(self, data_type):
        if data_type == 'TimeSeries':
            self.processor = TimeSeriesDataProcessor()
        elif data_type == 'TimeTag':
            self.processor = TimeTagDataProcessor()

    def input_settings(self, settings):
        self.processor.input_settings(settings)
    def process(self, raw_data):
        state_time_trace = self.proseccor.process(raw_data)

        return state_time_trace
