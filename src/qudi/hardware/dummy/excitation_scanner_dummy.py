import time
import numpy as np
from qudi.interface.excitation_scanner_interface import ExcitationScannerInterface, ExcitationScannerConstraints
from qudi.interface.sampled_finite_state_interface import SampledFiniteStateInterface, transition_to, transition_from, state
from typing import Iterable, Union, Tuple, Dict, Type, Callable

class ExcitationScannerDummy(ExcitationScannerInterface, SampledFiniteStateInterface):
    """
    Copy and paste configuration example:
    ```
      excitation_scanner_hardware:
        module.Class: 'dummy.excitation_scanner_dummy.ExcitationScannerDummy'
        options:
          watchdog_delay: 0.2 # default
    ```

    """
    _initial_state = "idle"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._constraints = ExcitationScannerConstraints((0,1),(1,1),(-1e9,1e9),["dummy variable"],[(0,100)],["potatoe"],[int])
        self._exposure_time = 0.001
        self._timer_start = time.perf_counter()
        self._scan_stop_time = time.perf_counter() + self._exposure_time*2000
        self._repeat_no = 1
        self._scan_running = False
        self._data_frequency = np.linspace(start=-1e9, stop=1e9, num=2000)
        self._data_count = 1000 * 1/(1 + (self._data_frequency/100e6)**2)
        self._data = np.array([[], [], [], []])
        self._dummy_variable = 0
        self._current_step = 0
        self._n_values = 0
    def on_activate(self):
        self.enable_watchdog()
        self.start_watchdog()
    def on_deactivate(self):
        self.disable_watchdog()
    @property
    def scan_running(self):
        return self._scan_running
    @property 
    def state_display(self):
        return self.watchdog_state
    def start_scan(self):
        self._scan_running = True
    def stop_scan(self):
        self._scan_running = False
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
        return self._data[:self._n_values, :]
    def set_exposure_time(self, time:float) -> None:
        "Set exposure time for one data point."
        self._exposure_time = time
    def set_repeat_number(self, n:int) -> None:
        "Set number of repetition of each segment of the scan."
        self._repeat_no = n
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
    @property
    def data_column_names(self) -> Iterable[str]:
        return ["Frequency", "Step number", "Time", "Count"]
    @property
    def data_column_unit(self) -> Iterable[str]:
        return ["Hz", "", "s", "c"]
    @property
    def data_column_number(self) -> Iterable[int]:
        return [3]
    @property
    def frequency_column_number(self) -> int:
        return 0
    @property
    def step_number_column_number(self) -> int:
        return 1
    @property
    def time_column_number(self) -> int:
        return 2

    # SampledFiniteStateInterface
    @state
    @transition_to(("start_idle", "idle"))
    @transition_from(("interrupt_scan", ["prepare_scan", "prepare_step", "wait_ready", "record_scan_step"]))
    def prepare_idle(self):
        self.log.info("Preparing idle.")
        self._scan_running = False
        self.watchdog_event("start_idle")
    @state
    @transition_to(("start_scan", "prepare_scan"))
    def idle(self):
        if self._scan_running:
            self.watchdog_event("start_scan")
    @state
    @transition_to(("start_prepare_step", "prepare_step"))
    def prepare_scan(self):
        self.log.info("Preparing scan.")
        self._current_step = 0
        self._scan_start_time = time.perf_counter()
        self._n_values = 0
        # Obviously, in a real setting you'd acquire data in record_scan_step, and
        # not make them up here ;)
        datapoint_per_scan = len(self._data_frequency)
        self._data = np.zeros((datapoint_per_scan*self._repeat_no, 4))
        self._data[:, self.frequency_column_number] = np.tile(self._data_frequency, self._repeat_no)
        self._data[:, self.step_number_column_number] = np.repeat(range(self._repeat_no), datapoint_per_scan)
        self._data[:, self.time_column_number] = np.linspace(start=0, stop=self._repeat_no*self._exposure_time*datapoint_per_scan, num=self._repeat_no*datapoint_per_scan)
        self._data[:, 3] = np.tile(self._data_count, self._repeat_no) + np.random.randn(self._repeat_no*datapoint_per_scan)*50
        self.watchdog_event("start_prepare_step")
    @state
    @transition_to(("start_wait_ready", "wait_ready"))
    @transition_to(("scan_done", "prepare_idle"))
    def prepare_step(self):
        self.log.info(f"Preparing step {self._current_step + 1}/{self._repeat_no}.")
        self._current_step += 1
        if self._current_step <= self._repeat_no:
            self._timer_start = time.perf_counter()
            self.watchdog_event("start_wait_ready")
        else:
            self.log.info("Scan done!")
            self.watchdog_event("scan_done")
    @state
    @transition_to(("start_scan_step", "record_scan_step"))
    def wait_ready(self):
        if time.perf_counter() - self._timer_start > 0.5:
            self.log.info("Sucessfully wasted 0.5s of your time waiting to be ready!")
            self._timer_start = time.perf_counter()
            self.watchdog_event("start_scan_step")
    @state
    @transition_to(("step_done", "prepare_step"))
    def record_scan_step(self):
        time_elapsed = time.perf_counter() - self._timer_start
        datapoint_per_scan = len(self._data_frequency)
        time_one_spectrum = self._exposure_time*datapoint_per_scan
        n = int(time_elapsed / time_one_spectrum + (self._current_step-1)*datapoint_per_scan)
        self._n_values = min(self._current_step * datapoint_per_scan, self._n_values + n)
        if self._n_values >= datapoint_per_scan:
            self.log.info("Step done!")
            self.watchdog_event("step_done")

