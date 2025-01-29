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

from qudi.logic.qdyne.qdyne_tools import SettingsBase, get_subclasses, get_method_names
from qudi.util.network import netobtain


class Analyzer(ABC):
    def analyze(self, data, settings):
        """
        @param MainDataClass data: qdyne dataclass
        @param AnalyzerSettings settings: corresponding anlyzer settings

        @return signal
        """

        pass

    def get_freq_domain_signal(self, data, settings):
        """
        @param MainDataClass data: qdyne dataclass
        @param AnalyzerSettings settings: corresponding anlyzer settings

        @return time_domain_data
        """

        pass

    def get_time_domain_signal(self, data, settings):
        """
        @param MainDataClass data: qdyne dataclass
        @param AnalyzerSettings settings: corresponding anlyzer settings

        @return time_domain_data
        """
        pass


@dataclass
class AnalyzerSettings(SettingsBase):
    pass

@dataclass
class FourierAnalyzerSettings(AnalyzerSettings):
    name: str = "default"
    padding_parameter: int = 1
    spectrum_type: str = "amp"
    sequence_length: float = 1


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
            print("{}_is not defined".format(stg.spectrum_type))
        return spectrum

    def do_fft(self, time_trace, padding_param=0, sequence_length_bins=1):
        """
        @return ft: complex ndarray
        """
        n_fft = len(time_trace)
        n_point = self._get_fft_n_point(padding_param, n_fft)
        ft = np.fft.fft(time_trace, n_point)
        freq = np.fft.fftfreq(n_point, sequence_length_bins)
        signal = [
            freq[: n_fft // 2],
            ft[: n_fft // 2],
        ]  # only select positive frequencies
        return signal

    def _get_fft_n_point(self, padding_param, n_fft):
        """
        choose the length of the transformed axis of the output
        according to the given padding parameter
        """
        if padding_param == 0:
            return None
        elif padding_param == 1 or padding_param == 2:
            return n_fft * padding_param
        else:
            print("error")

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


class TimeTraceAnalyzerMain:
    def __init__(self):
        self.method_list = []
        self.analyzer = None
        self._method = "Fourier"
        self.generate_method_list()

    def generate_method_list(self):
        analyzer_subclasses = get_subclasses(__name__, Analyzer)
        self.method_list = get_method_names(analyzer_subclasses, Analyzer)

    @property
    def method(self):
        return self._method

    @method.setter
    def method(self, method):
        self._method = method
        self._configure_method(method)

    def _configure_method(self, method):
        self.analyzer = globals()[method + "Analyzer"]()

    def analyze(self, data, settings):
        signal = self.analyzer.analyze(data, settings)
        return signal

    def get_freq_domain_signal(self, data, settings):
        freq_domain = self.analyzer.get_freq_domain_signal(data, settings)
        return np.array(freq_domain)

    def get_time_domain_signal(self, data, settings):
        time_domain = self.analyzer.get_time_domain_signal(data, settings)
        return np.array(time_domain)
