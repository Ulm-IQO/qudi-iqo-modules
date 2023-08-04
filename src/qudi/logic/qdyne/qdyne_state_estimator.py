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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from qudi.logic.pulsed.pulse_extractor import PulseExtractor
from qudi.logic.pulsed.pulse_analyzer import PulseAnalyzer


class StateEstimator(ABC):

    @abstractmethod
    def input_settings(self, settings):
        pass

    @abstractmethod
    def extract(self, raw_data):
        pass

    @abstractmethod
    def estimate(self, data):
        pass


@dataclass
class TimeSeriesBasedEstimatorSettings:
    extractor_settings: dict
    estimator_settings: dict


class TimeSeriesBasedEstimator(StateEstimator):

    def __init__(self):
        self.on_activate()

    def on_activate(self):
        self._extractor = PulseExtractor(pulsedmeasurementlogic=self)
        self._estimator = PulseAnalyzer(pulsedmeasurementlogic=self)

    def input_settings(self, settings):
        self._extractor.extraction_settings = settings.extractor_settings
        self._estimator.analysis_settings = settings.estimator_settings
        pass

    def extract(self, raw_data):
        extracted_data = self._extractor.extract_laser_pulses(raw_data)['laser_counts_arr']
        return extracted_data

    def estimate(self, data):
        tmp_signal, tmp_error = self._estimator.analyse_laser_pulses(data)
        return tmp_signal, tmp_error


@dataclass
class TimeTagBasedEstimatorSettings:
    count_mode: str = 'Average'
    count_length: int = 2000
    start_count: int = 0
    count_threshold: int = 90000
    weight: list = field(default_factory=list)

    @property
    def stop_count(self):
        return self.start_count + self.count_length


class TimeTagBasedEstimator(StateEstimator):
    def __init__(self):
        super().__init__()
        self.stg = None

    def input_settings(self, settings: TimeTagBasedEstimatorSettings) -> None:
        self.stg = settings

    def extract(self, raw_data):
        pass

    def estimate(self, time_tag_data):
        if self._count_mode == 'Average':
            counts_time_trace = self._photon_count(time_tag_data,
                                                   self.stg.start_count,
                                                   self.stg.stop_count,
                                                   self.stg.count_threshold)
        elif self._count_mode == 'WeightedAverage':
            counts_time_trace = self._weighted_photon_count(time_tag_data,
                                                            self.stg.weight,
                                                            self.stg.start_count,
                                                            self.stg.stop_count,
                                                            self.stg.count_threshold)
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


class StateEstimator:
    method_lists = ['TimeSeries', 'TimeTag']


    def __init__(self):
        self._method = None
        self.estimator = None

    @property
    def method(self):
        return self._method

    @method.setter
    def method(self, method):
        self._method = method
        self._configure_method(self._method)

    def _configure_method(self, method):
        if method == 'TimeSeries':
            self.estimator = TimeSeriesBasedEstimator()
        elif method == 'TimeTag':
            self.estimator = TimeTagBasedEstimator()

    def input_settings(self, settings):
        self.estimator.input_settings(settings)

    def extract(self, raw_data):
        extracted_data = self.estimator.extract(raw_data)
        return extracted_data

    def estimate(self, extracted_data):
        state_time_trace = self.estimator.estimate(extracted_data)
        return state_time_trace
