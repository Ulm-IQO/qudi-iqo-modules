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

import sys
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
import numpy as np

from qudi.logic.qdyne.tools.dataclass_tools import get_subclasses, get_subclass_qualifier
from qudi.logic.qdyne.tools.custom_dataclass import CustomDataclass
from logging import getLogger

logger = getLogger(__name__)


class StateEstimator(ABC):
    @abstractmethod
    def extract(self, raw_data, settings):
        pass

    @abstractmethod
    def estimate(self, data, settings):
        pass

    @abstractmethod
    def get_pulse(self, data, settings):
        pass


@dataclass
class StateEstimatorSettings(CustomDataclass):
    sequence_length: float = 1e-9
    bin_width: float = 1e-9


@dataclass
class TimeSeriesStateEstimatorSettings(StateEstimatorSettings):
    name: str = "TimeSeries"


#    extractor_settings: dict
#    estimator_settings: dict


# class TimeSeriesStateEstimator(StateEstimator):
#
#     def __init__(self, log, pmel):
#         self.log = log
#         self.pmel = pmel
#         self.on_activate()
#
#     def on_activate(self):
#         self._extractor = PulseExtractor(pulsedmeasurementlogic=self.pmel)
#         self._estimator = PulseAnalyzer(pulsedmeasurementlogic=self.pmel)
#
#     def extract(self, raw_data, settings):
#         extracted_data = self._extractor.extract_laser_pulses(raw_data)['laser_counts_arr']
#         return extracted_data
#
#     def estimate(self, data, settings):
#         tmp_signal, tmp_error = self._estimator.analyse_laser_pulses(data)
#         return tmp_signal, tmp_error
#
#     def get_pulse(self, data, settings):
#         y = data.mean(axis=0)
#         x = np.arange(len(y))
#         pulse_array = [x, y]
#         return pulse_array


@dataclass
class TimeTagStateEstimatorSettings(StateEstimatorSettings):
    name: str = 'default'
    count_mode: str = 'Average'
    sig_start: float = 0
    sig_end: float = 0
    weight: list = field(default_factory=list)

    @property
    def sig_start_int(self):
        return int(self.sig_start / self.bin_width)

    @property
    def sig_end_int(self):
        return int(self.sig_end / self.bin_width)

    @property
    def max_bins(self):
        return int(self.sequence_length / self.bin_width)


class TimeTagStateEstimator(StateEstimator):
    def __init__(self, log, *args):
        super().__init__()
        self.log = log
        self.stg = None

    def extract(self, raw_data, settings=None):
        return raw_data

    def estimate(self, time_tag_data, settings: TimeTagStateEstimatorSettings):
        if settings.count_mode == "Average":
            counts_time_trace = self._photon_count(
                time_tag_data,
                settings.sig_start_int,
                settings.sig_end_int,
            )

        elif settings.count_mode == "WeightedAverage":
            counts_time_trace = self._weighted_photon_count(
                time_tag_data,
                settings.weight,
                settings.sig_start_int,
                settings.sig_end_int,
            )
        else:
            logger.error(f"Count_mode '{settings.count_mode}' not supported, choose [Average, WeightedAverage]")
            raise ValueError(f"Encountered unsupported count_mode '{settings.count_mode}'.")
        return counts_time_trace

    def _photon_count(self, time_tag, start_count, stop_count):
        counts_time_trace = []
        counts_time_trace_append = counts_time_trace.append
        photon_counts = 0
        for i in range(1, len(time_tag)):  # count and filter the photons here
            if time_tag[i] != 0:
                if start_count <= time_tag[i] < stop_count:
                    photon_counts = photon_counts + 1
            else:
                counts_time_trace_append(photon_counts)
                photon_counts = 0
        return np.array(counts_time_trace)

    def _weighted_photon_count(
        self, time_tag, weight, start_count, stop_count
    ):
        counts_time_trace = []
        counts_time_trace_append = counts_time_trace.append
        photon_counts = 0
        for i in range(len(time_tag)):  # count and filter the photons here
            if time_tag[i] != 0:
                if start_count < time_tag[i] < stop_count:
                    photon_counts = photon_counts + weight[i]
            else:
                counts_time_trace_append(photon_counts)
                photon_counts = 0
        return np.array(counts_time_trace)

    def get_pulse(self, time_tag_data, settings: TimeTagStateEstimatorSettings):
        self.log.debug(f"TimeTageStateEstimator get_pulse, {time_tag_data=}, {settings=}")
        # max_bins = int(max(time_tag_data))
        count_hist, bin_edges = np.histogram(
            time_tag_data, bins=settings.max_bins, range=(1, settings.max_bins)
        )
        time_array = settings.bin_width * np.arange(len(count_hist))
        pulse_array = [time_array, count_hist]
        self.log.debug(f"{pulse_array=}")
        return pulse_array


class StateEstimatorMain:
    def __init__(self, log):
        self.log = log
        self.method_list = []
        self._method = None
        self.estimator = None
        self.generate_method_list()

    def generate_method_list(self):
        estimator_subclasses = get_subclasses(__name__, StateEstimator)
        self.method_list = [get_subclass_qualifier(subclass, StateEstimator) for subclass in estimator_subclasses]

    def configure_method(self, method):
        self.estimator = globals()[method + "StateEstimator"](self.log)

    def get_pulse(self, raw_data, settings):
        self.log.debug("StateEstimatorMain: get_pulse: estimator.get_pulse")
        return self.estimator.get_pulse(raw_data, settings)

    def extract(self, raw_data, settings):
        extracted_data = self.estimator.extract(raw_data, settings)
        return extracted_data

    def estimate(self, extracted_data, settings):
        state_time_trace = self.estimator.estimate(extracted_data, settings)
        return state_time_trace
