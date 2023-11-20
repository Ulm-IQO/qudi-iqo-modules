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

from qudi.util.network import netobtain


class Analyzer(ABC):
    @abstractmethod
    def input_settings(self, settings):
        pass

    @abstractmethod
    def analyze(self, time_trace):
        pass

    @abstractmethod
    def get_spectrum(self, signal):
        pass


@dataclass
class FourierSettings:
    range_around_peak: int = 30
    padding_parameter: int = 1
    cut_time_trace: bool = False
    spectrum_type: str = 'amp'
    sequence_length_s: float = 0

    def ceil_log(self, sample_size, base=2):
        return int(np.ceil(np.log(sample_size, base)))

    def next_pow_2(self, sample_size):
        return int(2**self.ceil_log(sample_size))


class FourierAnalyzer(Analyzer):

    def __init__(self):
        self.stg = None

    def input_settings(self, settings: FourierSettings) -> None:
        self.stg = settings

    def analyze(self, time_trace):
        ft_signal = self.do_fft(time_trace, self.stg.cut_time_trace, self.stg.padding_parameter)
        return ft_signal

    def get_spectrum(self, ft_signal):
        if self.stg.spectrum_type == 'amp':
            spectrum = self.get_norm_amp_spectrum(ft_signal)
        elif self.stg.spectrum_type == 'power':
            spectrum = self.get_norm_psd(ft_signal)
        else:
            print('{}_is not defined'.format(self.stg.spectrum_type))
        return spectrum

    def do_fft(self, time_trace, cut_time_trace=False, padding_param=0):
        """
        @return ft: complex ndarray
        """
        n_fft = len(time_trace)
        input_time_trace = self._get_fft_input_time_trace(time_trace, cut_time_trace, n_fft)
        n_point = self._get_fft_n_point(padding_param, n_fft)
        ft = np.fft.fft(input_time_trace, n_point)

        return ft

    def _get_fft_input_time_trace(self, time_trace, cut_time_trace, n_fft):
        if cut_time_trace:
            return time_trace[:int(n_fft/2)]
        else:
            return time_trace

    def _get_fft_n_point(self, padding_param, n_fft):
        """
        choose the length of the transformed axis of the output
        according to the given padding parameter
        """
        if padding_param == 0:
            return None
        elif padding_param == 1 or padding_param ==2:
            return n_fft * padding_param
        else:
            print('error')

    def get_norm_amp_spectrum(self, ft):
        """
        get the normalized amplitude spectrum
        """
        amp_spectrum = abs(ft)
        norm_amp_spectrum = amp_spectrum / len(amp_spectrum)
        return norm_amp_spectrum

    def get_norm_psd(self, ft):
        """
        get the normalized power sepctrum density
        """
        psd = abs(ft)*2
        norm_psd = psd / (len(psd))**2
        return norm_psd

    def get_half_frequency_array(self, sequence_length_s, ft):
        return 1 / (sequence_length_s * len(ft))*np.arange(len(ft)/2)

def get_subclasses(class_obj):
    '''
    Given a class, find its subclasses and get their names.
    '''

    subclasses = []
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(obj) and issubclass(obj, class_obj) and obj != class_obj:
            subclasses.append(obj)

    return subclasses

def get_method_names(subclass_obj, class_obj):
    subclass_names = [cls.__name__ for cls in subclass_obj]
    method_names = [subclass_name.replace('Analyzer', '') for subclass_name in subclass_names]
    return method_names

class TimeTraceAnalyzer:

    def __init__(self):
        self.method_lists = []
        self.analyzer = None
        self._method = 'Fourier'
        self.generate_method_lists()

    def generate_method_lists(self):
        analyzer_subclasses = get_subclasses(Analyzer)
        self.method_lists = get_method_names(analyzer_subclasses, Analyzer)

    @property
    def method(self):
        return self._method

    @method.setter
    def method(self, method):
        self._method = method
        self._configure_method(method)

    def _configure_method(self, method):
        self.analyzer = globals()[method + 'Analyzer']()

    def input_settings(self, settings):
        self.analyzer.input_settings(settings)

    def analyze(self, time_trace):
        time_trace = netobtain(time_trace)
        signal = self.analyzer.analyze(time_trace)
        return signal

    def get_spectrum(self, signal):
        signal = netobtain(signal)
        spectrum = self.analyzer.get_spectrum(signal)
        return spectrum
