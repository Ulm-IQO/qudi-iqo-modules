import time
import numpy as np
from qudi.interface.excitation_scanner_interface import ExcitationScannerInterface, ExcitationScannerConstraints

class ExcitationScannerDummy(ExcitationScannerInterface):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._constraints = ExcitationScannerConstraints((0,1),(1,1),(-1e9,1e9),["dummy variable"],[(0,100)],["potatoe"],[int])
        self._exposure_time = 0.001
        self._scan_start_time = time.perf_counter()
        self._scan_stop_time = time.perf_counter() + self._exposure_time*2000
        self._repeat_no = 0
        self._scan_running = False
        self._data_frequency = np.linspace(start=-1e9, stop=1e9, num=2000)
        self._data_count = 1000 * 1/(1 + (self._data_frequency/100e6)**2)
        self._data_repeat = np.repeat(0, 2000)
        self._dummy_variable = 0
    def on_activate(self):
        pass
    def on_deactivate(self):
        pass
    @property
    def scan_running(self):
        time_one_spectrum = self._exposure_time*len(self._data_frequency)
        delay_expired = (time.perf_counter() - self._scan_start_time) > time_one_spectrum
        if delay_expired:
            self._scan_stop_time = time.perf_counter()
            self._scan_running = False
        return self._scan_running
    @property 
    def state_display(self):
        if self.scan_running:
            return "Scanning"
        else:
            return "Idle"
    def start_scan(self):
        self._scan_start_time = time.perf_counter()
        self._scan_running = True
    def stop_scan(self):
        self._scan_running = False
        self._scan_stop_time = time.perf_counter()
    @property 
    def constraints(self):
        return self._constraints
    def set_control(self, variable: str, value) -> None:
        "Set a control variable value."
        if variable == "dummy variable": 
            self._dummy_variable = value
    def get_control(self, variable: str):
        "Get a control variable value."
        return self._dummy_variable
    def get_current_data(self) -> np.ndarray:
        "Return current scan data."
        if self.scan_running:
            dt = time.perf_counter() - self._scan_start_time
        else:
            dt =self._scan_stop_time - self._scan_start_time
        n_values = min(round(dt/self._exposure_time), len(self._data_frequency))
        return np.array([self._data_frequency[:n_values], self._data_count[:n_values], self._data_repeat[:n_values]]).T
    def set_exposure_time(self, time:float) -> None:
        "Set exposure time for one data point."
        self._exposure_time = time
    def set_repeat_number(self, n:int) -> None:
        "Set number of repetition of each segment of the scan."
        pass
    def set_idle_value(self, n:float) -> None:
        "Set idle value."
        pass
    def get_exposure_time(self) -> float:
        "Get exposure time for one data point."
        return self._exposure_time
    def get_repeat_number(self) -> int:
        "Get number of repetition of each segment of the scan."
        return 1
    def get_idle_value(self) -> float:
        "Get idle value."
        return 0.0

