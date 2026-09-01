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

from qudi.logic.qdyne.tools.dataclass_tools import MethodRegistry
from qudi.logic.qdyne.qdyne_data.estimator_settings import (
    StateEstimatorSettings,
    TimeTagStateEstimatorSettings,
)
from logging import getLogger

logger = getLogger(__name__)

#: Every selectable estimator method, each registered with BOTH its implementation and its settings
#: class. Registering the pair is what stops the two drifting apart - see MethodRegistry.
ESTIMATORS = MethodRegistry('state estimator')


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


# NOTE: TimeSeriesStateEstimatorSettings used to be declared here, but the matching
# TimeSeriesStateEstimator below has been commented out for a long time. Because the settings
# classes and the estimator classes are discovered separately (get_subclass_dict over the settings,
# get_subclasses over the implementations), that orphan made the GUI advertise a "TimeSeries" method
# that raised KeyError in configure_method() the moment it was selected - settings offered
# ['TimeSeries', 'TimeTag'] while implementations were only ['TimeTag'].
#
# The settings class is removed rather than the estimator restored, because the implementation below
# needs PulseExtractor/PulseAnalyzer and a PulsedMeasurementLogic reference that nothing currently
# supplies. If you bring TimeSeries back, restore BOTH halves together, or the same mismatch returns.


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


class TimeTagStateEstimator(StateEstimator):
    def __init__(self, log, *args):
        super().__init__()
        self.log = log

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

    @staticmethod
    def _sweep_sums(time_tag, start_count, stop_count, weight=None):
        """Sum the photons of each sweep that fall inside [start_count, stop_count).

        A zero in the time-tag stream marks the start of a new sweep (see
        QdyneCounterInterface.get_data), so the result has one entry per zero encountered.

        Vectorised with numpy rather than looping in Python: a Qdyne run accumulates millions of
        time tags and this is called on every analysis tick.

        The two count modes used to be separate loops that disagreed with each other - one started
        at index 1 and used `start <= tag`, the other started at index 0 and used `start < tag`, so
        the same data gave different answers depending on the mode. They share this one
        implementation now; `weight` is the only difference.
        """
        tags = np.asarray(time_tag)
        if tags.size == 0:
            return np.array([], dtype=float if weight is not None else int)

        # Sweep index of every sample: starts at -1 and increments on each zero, so the samples
        # before the first zero belong to sweep -1 and are discarded along with it.
        is_new_sweep = tags == 0
        sweep_index = np.cumsum(is_new_sweep) - 1

        in_window = (~is_new_sweep) & (tags >= start_count) & (tags < stop_count)
        if weight is None:
            contributions = in_window.astype(np.int64)
        else:
            weights = np.zeros(tags.shape, dtype=float)
            usable = min(len(weight), tags.size)
            weights[:usable] = np.asarray(weight[:usable], dtype=float)
            contributions = np.where(in_window, weights, 0.0)

        number_of_sweeps = int(is_new_sweep.sum())
        if number_of_sweeps == 0:
            return np.array([], dtype=contributions.dtype)

        # A sweep's counts are the samples AFTER its zero, so a sample belongs to the sweep opened
        # by the most recent zero. The final sweep is dropped: it has no closing zero, so it is
        # still being filled and would report a partial count.
        valid = sweep_index >= 0
        sums = np.bincount(
            sweep_index[valid], weights=contributions[valid], minlength=number_of_sweeps
        )
        sums = sums[:-1]
        # bincount(weights=...) always returns float64. Unweighted counts are whole photons and
        # were an integer array before, and downstream code appends them to an integer time trace.
        return sums if weight is not None else sums.astype(np.int64)

    def _photon_count(self, time_tag, start_count, stop_count):
        return self._sweep_sums(time_tag, start_count, stop_count)

    def _weighted_photon_count(self, time_tag, weight, start_count, stop_count):
        return self._sweep_sums(time_tag, start_count, stop_count, weight=weight)

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


ESTIMATORS.register('TimeTag', TimeTagStateEstimator, TimeTagStateEstimatorSettings)


class StateEstimatorMain:
    def __init__(self, log):
        self.log = log
        self._method = None
        self.estimator = None

    @property
    def method_list(self):
        return ESTIMATORS.names

    @property
    def method(self):
        return self._method

    @method.setter
    def method(self, method):
        """Select the estimator method and build it.

        This property is the reason QdyneLogic.input_estimator_method() works at all: without it,
        `self.estimator.method = ...` merely created a stray attribute and configure_method() was
        never reached, leaving self.estimator as None. TimeTraceAnalyzerMain has had the equivalent
        property all along - the two Main classes are meant to present the same interface.
        """
        self._method = method
        self.configure_method(method)

    def configure_method(self, method):
        # Looked up in the registry rather than by building a class name and fishing it out of
        # globals(): the registry cannot hold a settings class without its implementation, and it
        # raises with the available names instead of a bare KeyError.
        self.estimator = ESTIMATORS.implementation(method)(self.log)

    def get_pulse(self, raw_data, settings):
        self.log.debug("StateEstimatorMain: get_pulse: estimator.get_pulse")
        return self.estimator.get_pulse(raw_data, settings)

    def extract(self, raw_data, settings):
        extracted_data = self.estimator.extract(raw_data, settings)
        return extracted_data

    def estimate(self, extracted_data, settings):
        state_time_trace = self.estimator.estimate(extracted_data, settings)
        return state_time_trace
