from dataclasses import dataclass
import numpy as np

#####Dataclass#####
@dataclass
class TimeSeriesDataclass:
    data: np.ndarray = np.array([], dtype=float)

@dataclass
class TimeTagDataclass:
    data: np.ndarray = np.array([], dtype=float)
    hist:
    rise_edge:
    fall_edge:
    MN: int=5

    def get_histogram(self, time_tag):
        count_hist, bin_edges= np.histogram(time_tag, max(time_tag))
        return count_hist
    def get_histogram_N(self, time_tag, N):
        count_hist, bin_edges= np.histogram(time_tag, bins=N, range=(1,N))
        return  count_hist

    def get_start_count(self):
        count_hist = self.get_histogram()
        return np.where(count_hist[1:] > len(self.data) / self.MN) #TODO what is MN?


@dataclass
class FT_dataclass:
    frequency: np.ndarray = np.array([], dtype=float)
    data: np.arra = np.array([], dtype=float)

    @property
    def peak_index(self):
        return np.argmax(self.data)

#####Settings#####
@dataclass
class Sequence_generator_settings:
    pass

@dataclass
class Counter_settings:
    bin_width: float  = 0
    record_length: float = 0
    number_of_gates: int = 1

@dataclass
class Measurement_settings(Sequence_generator_settings, Counter_settings):
    pass

@dataclass
class Master_settings:
    status: dict = {} #TODO more specific
    estimated_measurement_time: float = 0
    update_time: float = 0
    extraction_method: str = ''
    fit_method: str = ''

@dataclass
class save_settings:
    save_binary: bool = True
    show_full: bool = True
    full_list_name: str = ''
    file_extension: str= ''

    def get_full_list_name(self):

@dataclass
class Plot_settings:


##### not general#####
@dataclass
class readout_settings:
    filename: str = ''
    headerlength: int = 72
    read_lines: int = 0
    lines_to_read: int = 0
    lines_per_chunk: int = 10000

    @property
    def number_of_chunks(self):
        if self.lines_to_read is None:
            number_of_chunks = float('inf')
        else:
            number_of_chunks = int(lines_to_read / lines_per_chunk)
        return number_of_chunks

@dataclass
class Qdyne_extraction_settings:
    # TODO
    number_of_sweeps: int = 0
    read_whole_file: bool = False
    all_counts: bool = False

@dataclass
class analyze_FFT_settings:
    range_around_peak: int = 30
    zero_padding: bool = True
    double_padding: bool = False
    cut_time_trace: bool = False
    sequence_length: float = 0

    def ceiled_power_of_two(self, data_len):
        return int(np.ceil(np.log(data_len, 2)))

    def n_fft(self, data_len):
        return int(2**self.ceiled_power_of_two(data_len))

@dataclass
class Fit_settings:
