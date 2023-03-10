import numpy as np


class DirectEstimator:
    def __init__(self):
        self.count_length = 2000
        self.start_count = 0
        self.stop_count = self.start_count + self.count_length
        self.count_threshold = 90000
        return
    def photon_count(self, time_tag, start_count, stop_count, count_threshold=90000):
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
    def weighted_photon_count(self, time_tag, weight, start_count, stop_count, count_threshold=90000):
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

class FourierAnalysis:

    def input_settings(self):
        self._range_around_peak =
        self._zero_padding =
        self._double_padding =
        self._cut_time_trace =
        self._sequence_length =

    def do_fft(self, time_trace=None, cut_time_trace=False, padding_param=0):
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

    def get_half_norm_psd(self, ft):
        return self.get_norm_psd(ft)[:int(len(ft) / 2)]

    def get_half_norm_amp_spectrum(self, ft):
        return self.get_norm_amp_spectrum(ft)[:int(len(ft) / 2)]

    def get_half_frequency_array(self, sequence_length, ft):
        return 1 / (sequence_length * len(ft))*np.arange(len(ft)/2)





class Save