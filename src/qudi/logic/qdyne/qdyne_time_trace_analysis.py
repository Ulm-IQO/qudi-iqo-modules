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
import numpy as np

class FourierAnalysis:

    def input_settings(self, settings):
        self._range_around_peak = settings.range_around_peak
        self._padding_parameter = settings.padding_parameter
        self._cut_time_trace = settings.cut_time_trace
        self._spectrum_type = settings.spectrum_type
        self._sequence_length_s = settings.sequence_length_s

    def analyze(self, time_trace):
        ft_signal = self.do_fft(time_trace, self._cut_time_trace, self._padding_parameter)
        return ft_signal

    def get_spectrum(self, ft_signal):
        if self._spectrum_type == 'amp':
            spectrum = self.get_norm_amp_spectrum(ft_signal)
        elif self._spectrum_type == 'power':
            spectrum = self.get_norm_psd(ft_signal)
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
        psd = np.square(ft)
        norm_psd = psd / (len(psd))**2
        return norm_psd

    def get_half_frequency_array(self, sequence_length_s, ft):
        return 1 / (sequence_length_s * len(ft))*np.arange(len(ft)/2)

class TimeTraceAnalyzer:

    def __init__(self):
        self.method = 'FT'

    def configure_method(self, method):
        if method == 'FT':
            self.analyzer = FourierAnalysis()

    def input_settings(self, settings):
        self.analyzer.input_settings(settings)

    def analyze(self, time_trace):
        signal = self.analyzer.analyze(time_trace)

        return signal

    def get_spectrum(self, signal):
        spectrum = self.analyzer.get_spectrum(signal)
        return spectrum
