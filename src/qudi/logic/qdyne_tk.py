import numpy as np

class Extraction:
    def __init__(self):
        return

    def


class Analysis:

    def input_settings(self):
        self._range_around_peak =
        self._zero_padding =
        self._double_padding =
        self._cut_time_trace =
        self._sequence_length =

    def do_fft(self, time_trace, n_fft, cut_time_trace, padding_param):
        """
        @return ft: complex ndarray
        """
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
        norm_psd = psd / (len(psd))^2
        return norm_psd

    def get_half_norm_psd(self, ft):
        return self.get_norm_psd(ft)[:int(len(ft) / 2)]

    def get_half_norm_amp_spectrum(self, ft):
        return self.get_norm_amp_spectrum(ft)[:int(len(ft) / 2)]

    def get_half_frequency_array(self, sequence_length, ft):
        return 1 / (sequence_length * len(ft))*np.arange(len(ft)/2)





class Save