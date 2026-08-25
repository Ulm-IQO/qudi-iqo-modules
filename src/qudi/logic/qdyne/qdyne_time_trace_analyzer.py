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
from dataclasses import dataclass
import numpy as np

from qudi.logic.qdyne.tools.dataclass_tools import MethodRegistry
from qudi.logic.qdyne.qdyne_data.analyzer_settings import (
    AnalyzerSettings,
    FourierAnalyzerSettings,
)

#: Every selectable analyzer method, registered with BOTH its implementation and its settings class.
ANALYZERS = MethodRegistry('time trace analyzer')


class Analyzer(ABC):
    """Base for time trace analyzers.

    These are real @abstractmethods now. They used to be plain `pass` bodies on an ABC with no
    abstract markers, so a subclass implementing none of them instantiated happily and returned
    None from every call - a silent wrong answer rather than an error. Its sibling StateEstimator
    always enforced its interface; the two are meant to behave the same way.
    """

    @abstractmethod
    def analyze(self, data, settings):
        """
        @param MainDataClass data: qdyne dataclass
        @param AnalyzerSettings settings: corresponding analyzer settings

        @return signal
        """

    @abstractmethod
    def get_freq_domain_signal(self, data, settings):
        """
        @param MainDataClass data: qdyne dataclass
        @param AnalyzerSettings settings: corresponding analyzer settings

        @return freq_domain_data
        """

    def get_time_domain_signal(self, data, settings):
        """Optional - not every analyzer has a meaningful time-domain view.

        Left concrete on purpose: making it abstract would force every analyzer to implement
        something it may not have. Returns None unless a subclass overrides it.
        """
        return None


class FourierAnalyzer(Analyzer):
    def analyze(self, data, stg: FourierAnalyzerSettings):
        time_trace = data.time_trace - np.mean(data.time_trace)
        ft_signal = self.do_fft(time_trace, stg.padding_parameter, stg.sequence_length)
        return ft_signal

    def get_freq_domain_signal(self, data, stg: FourierAnalyzerSettings):
        ft_signal = data.signal
        if stg.spectrum_type == "amp":
            spectrum = self.get_norm_amp_spectrum(ft_signal)
        elif stg.spectrum_type == "power":
            spectrum = self.get_norm_psd(ft_signal)
        else:
            # Previously this printed to stdout and fell through, leaving `spectrum` unbound so the
            # next line raised UnboundLocalError - which says nothing about the real cause.
            raise ValueError(
                f"Unsupported spectrum_type '{stg.spectrum_type}'. Choose 'amp' or 'power'."
            )
        return spectrum

    def do_fft(self, time_trace, padding_param=0, sequence_length_bins=1):
        """
        @return ft: complex ndarray
        """
        time_trace = time_trace - np.mean(time_trace)
        n_point = self._get_padded_time_trace_length(time_trace, padding_param)
        ft = np.fft.rfft(time_trace, n_point)
        freq = np.fft.rfftfreq(n_point, sequence_length_bins)
        signal = [freq, ft]
        return signal

    def _get_padded_time_trace_length(self, time_trace: np.ndarray, padding_param: int) -> int:
        """
        Method that calculates a padded timetrace depending on the padding param.
        If padding param == 0: time trace is returned as input
        Else the padding_param next power of 2 to the current length is used as new length

        The method returns the length of the padded_timetrace
        """
        m = len(time_trace)
        if m == 0:
            # np.log2(0) is -inf and int(-inf) raises OverflowError, so an empty time trace used to
            # crash here rather than simply producing an empty spectrum.
            return 0
        if padding_param == 0:
            return m
        # as fft works fastest for a number of datapoints following 2^n pad (if padding_param != 0)  to the next power of 2
        n = int(np.floor(np.log2(m)))
        if padding_param > 0:
            target_length = 2**(n + padding_param)
        elif padding_param < - n:
            raise ValueError(f"Padding parameter too small. Minimum padding parameter: {-n}")
        else:
            target_length = 2**(n + padding_param + 1)
        return target_length

    def get_norm_amp_spectrum(self, signal):
        """
        get the normalized amplitude spectrum
        """
        freq = signal[0]
        ft = signal[1]
        amp_spectrum = abs(ft)
        norm_amp_spectrum = amp_spectrum / len(amp_spectrum)
        return [freq, norm_amp_spectrum]

    def get_norm_psd(self, signal):
        """
        get the normalized power sepctrum density
        """
        freq = signal[0]
        ft = signal[1]
        psd = abs(ft) * 2
        norm_psd = psd / (len(psd)) ** 2
        return [freq, norm_psd]

    def get_half_frequency_array(self, sequence_length, ft):
        return 1 / (sequence_length * len(ft)) * np.arange(len(ft) / 2)


ANALYZERS.register('Fourier', FourierAnalyzer, FourierAnalyzerSettings)


class TimeTraceAnalyzerMain:
    def __init__(self):
        self.analyzer = None
        self._method = "Fourier"

    @property
    def method_list(self):
        return ANALYZERS.names

    @property
    def method(self):
        return self._method

    @method.setter
    def method(self, method):
        self._method = method
        self._configure_method(method)

    def _configure_method(self, method):
        # Registry lookup rather than globals() - see StateEstimatorMain.configure_method().
        self.analyzer = ANALYZERS.implementation(method)()

    def analyze(self, data, settings):
        signal = self.analyzer.analyze(data, settings)
        return signal

    def get_freq_domain_signal(self, data, settings):
        freq_domain = self.analyzer.get_freq_domain_signal(data, settings)
        return np.array(freq_domain)

    def get_time_domain_signal(self, data, settings):
        time_domain = self.analyzer.get_time_domain_signal(data, settings)
        return np.array(time_domain)
