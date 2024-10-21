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
from dataclasses import dataclass, field
import numpy as np

from qudi.util.network import netobtain
from qudi.logic.pulsed.pulse_extractor import PulseExtractor
from qudi.logic.pulsed.pulse_analyzer import PulseAnalyzer


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
class StateEstimatorSettings(ABC):
    _settings_updated_sig: object
    name: str = ""

    def __setattr__(self, key, value):
        if hasattr(self, key) and hasattr(self, "_settings_updated_sig") and key != "_settings_updated_sig":
            old_value = getattr(self, key)
            if old_value != value:
                self._settings_updated_sig.emit()

        super().__setattr__(key, value)

    def pass_signal(self, settings_updated_sig):
        self._settings_updated_sig = settings_updated_sig

    def delete_signal(self):
        del self._settings_updated_sig


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
    time_bin: float = 1e-9
    count_threshold: int = (
        10  # Todo: this has to be either estimated or set somewhere from the logic
    )
    weight: list = field(default_factory=list)

    def get_histogram(self, time_tag_data):
        count_hist, bin_edges = np.histogram(time_tag_data, max(time_tag_data))
        return count_hist

    def set_start_count(
        self, time_tag_data
    ):  # Todo: or maybe this can be taken from the pulseextractor?
        count_hist = self.get_histogram(time_tag_data)
        self.sig_start = int(np.where(count_hist[1:] > self.count_threshold)[0][0])

    @property
    def sig_start_int(self):
        return int(self.sig_start / self.time_bin)

    @property
    def sig_end_int(self):
        return int(self.sig_end / self.time_bin)


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
        max_bins = int(max(time_tag_data))
        count_hist, bin_edges = np.histogram(
            time_tag_data, bins=max_bins, range=(1, max_bins)
        )
        time_array = settings.time_bin * np.arange(len(count_hist))
        pulse_array = [time_array, count_hist]
        return pulse_array


def get_subclasses(class_obj):
    """
    Given a class, find its subclasses and get their names.
    """

    subclasses = []
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(obj) and issubclass(obj, class_obj) and obj != class_obj:
            subclasses.append(obj)

    return subclasses


def get_method_names(subclass_obj, class_obj):
    subclass_names = [cls.__name__ for cls in subclass_obj]
    method_names = [
        subclass_name.replace(class_obj.__name__, "")
        for subclass_name in subclass_names
    ]
    return method_names


class StateEstimatorMain:
    def __init__(self, log):
        self.log = log
        self.method_list = []
        self._method = None
        self.estimator = None
        self.generate_method_list()

    def generate_method_list(self):
        estimator_subclasses = get_subclasses(StateEstimator)
        self.method_list = get_method_names(estimator_subclasses, StateEstimator)

    def configure_method(self, method):
        self.estimator = globals()[method + "StateEstimator"](self.log)

    def get_pulse(self, raw_data, settings):
        return self.estimator.get_pulse(raw_data, settings)

    def extract(self, raw_data, settings):
        extracted_data = self.estimator.extract(raw_data, settings)
        return extracted_data

    def estimate(self, extracted_data, settings):
        state_time_trace = self.estimator.estimate(extracted_data, settings)
        return state_time_trace
