import time

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import Mutex
from qudi.interface.excitation_scanner_interface import ExcitationScannerInterface, ExcitationScannerConstraints
from qudi.interface.sampled_finite_state_interface import SampledFiniteStateInterface, transition_to, transition_from, state, initial

import numpy as np

class FiniteSamplingScanningExcitationInterfuse(ExcitationScannerInterface, SampledFiniteStateInterface):
    """
    An ExcitationScannerInterface to use a FiniteSamplingInputInterface to
    control te MOGLabs scanning laser. 

    Copy and paste example configuration:
    ```yaml
    moglabs_scanner:
        module.Class: interfuse.moglabs_scanning_excitation.FiniteSamplingScanningExcitationInterfuse
        connect:
            input: ni_finite_sampling_input
            ldd_switches: moglabs_ldd
            ldd_control: moglabs_ldd
            cem_control: moglabs_cem
            fzw_sampling: moglabs_fzw
        options:
            input_channel: 'pfi3'
            output_channel: 'ao2'
    ```
    """
    _finite_sampling_input = Connector(name='input', interface='FiniteSamplingInputInterface')
    _ldd_switches = Connector(name="ldd_switches", interface="SwitchInterface")
    _ldd_control = Connector(name="ldd_control", interface="ProcessControlInterface")
    _cem_control = Connector(name="cem_control", interface="ProcessControlInterface")
    _fzw_sampling = Connector(name="fzw_sampling", interface="FiniteSamplingInputInterface")

    _input_channel = ConfigOption(name="input_channel", missing="error")
    _chunk_size = ConfigOption(name="chunk_size", default=10)

    _scan_data = StatusVar(name="scan_data", default=np.empty((0,3)))
    _exposure_time = StatusVar(name="exposure_time", default=1e-2)
    _n_repeat = StatusVar(name="n_repeat", default=1)
    _idle_value = StatusVar(name="idle_value", default=0.0)
    _bias = StatusVar(name='bias', default=33.0)
    _offset = StatusVar(name='offset', default=0.5)
    _span = StatusVar(name='span', default=0.5)
    _frequency = StatusVar(name='frequency', default=5)
    _interpolate_frequencies = StatusVar(name='interpolate_frequencies', default=True)
    _idle_scan = StatusVar(name='idle_scan', default=False)
    _fzw_rate = StatusVar(name="fzw_rate", default=10)
    _duty = StatusVar(name="duty", default=0.5)
    _delay_start_acquitition = StatusVar(name="delay_start_acquitition", default=0.5)

    _threaded = True
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scanning_states = {"prepare_scan", "prepare_step", "record_scan_step", "wait_ready"}
        self._data_lock = Mutex()
        self._constraints = ExcitationScannerConstraints((0,0),(0,0),(0,0),[],[],[],[])
        self._waiting_start = time.perf_counter()
        self._idle_scan_start = time.perf_counter()
        self._repeat_no = 0
        self._data_row_index = 0
        self._frequency_row_index = 0
        self._scan_data = np.zeros((0, 3))
        self._measurement_time = np.zeros(0)
    # Internal utilities
    @property 
    def _number_of_samples_per_frame(self):
        return round(1/ (self._exposure_time * self._frequency))
    @property 
    def _number_of_frequencies_per_frame(self):
        return round(self._fzw_rate / self._frequency)
    def _prepare_ramp(self, prepare_input=True):
        n = self._number_of_samples_per_frame
        if prepare_input:
            self._finite_sampling_input().set_sample_rate(1/self._exposure_time)
            self._finite_sampling_input().set_active_channels((self._input_channel,))
            self._finite_sampling_input().set_frame_size(n)
            self._fzw_sampling().set_sample_rate(self._fzw_rate)
            self._fzw_sampling().set_frame_size(n)
        self._ldd_control().set_setpoint("frequency", self._frequency)
        self._ldd_control().set_setpoint("span", self._span)
        self._ldd_control().set_setpoint("offset", self._offset)
        self._ldd_control().set_setpoint("ramp_halt", 0.0)
        self._ldd_control().set_setpoint("bias", self._bias)
    def _start_ramp(self):
        self._ldd_switches().set_state("RAMP", "ON")
    def _stop_ramp(self):
        self._ldd_switches().set_state("RAMP", "OFF")
    def _start_acquisition(self):
        if self._finite_sampling_input().samples_in_buffer > 0:
            self._finite_sampling_input().get_buffered_samples()
        if self._fzw_sampling().samples_in_buffer > 0:
            self._fzw_sampling().stop_buffered_acquisition()
        self._finite_sampling_input().start_buffered_acquisition()
        self._fzw_sampling().start_buffered_acquisition()
    def _stop_acquisition(self):
        self._fzw_sampling().stop_buffered_acquisition()
        self._finite_sampling_input().stop_buffered_acquisition()
    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        cem_constraints = self._cem_control().constraints
        cem_limits = cem_constraints.channel_limits
        cem_units = cem_constraints.channel_units
        ldd_constraints = self._ldd_control().constraints
        ldd_limits = ldd_constraints.channel_limits
        ldd_units = ldd_constraints.channel_units
        fzw_constraints = self._fzw_sampling().constraints
        self._constraints = ExcitationScannerConstraints(
            exposure_limits=(1e-4,1),
            repeat_limits=(1,2**32-1),
            idle_value_limits=(0.0, 400e12),
            control_variables=("grating", "current", "offset", "span", "bias", "frequency", "duty", "fzw probe rate", "delay_start_acquitition", "interpolate_frequencies", "idle_scan"),
            control_variable_limits=(cem_limits["grating"], ldd_limits["current"], ldd_limits["offset"], ldd_limits["span"], ldd_limits["bias"], ldd_limits["frequency"], ldd_limits["duty"], fzw_constraints.sample_rate_limits, (0.0, 60), (False, True), (False, True)),
            control_variable_types=(int, float, float, float, float, float, float, float, float, bool, bool),
            control_variable_units=(cem_units["grating"], ldd_units["current"], ldd_units["offset"], ldd_units["span"], ldd_units["bias"], ldd_units["frequency"], ldd_units["duty"], "Hz", "s", None, None)
        )
        self._ldd_control().set_setpoint("frequency", self._frequency)
        self._ldd_control().set_setpoint("duty", self._duty)
        self._ldd_control().set_setpoint("bias", self._bias)
        self._ldd_control().set_setpoint("span", self._span)
        self._ldd_control().set_setpoint("offset", self._offset)
        self.enable_watchdog()
        self.start_watchdog()

    def on_deactivate(self):
        self.watchdog_event("stop")
        self.disable_watchdog()
        # time.sleep(3*self._watchdog_delay)

    # SampledFiniteStateInterface
    @state
    @initial
    @transition_to(("start", "idle"))
    def prepare_idle(self):
        "Prepare the hardware for idling: stop the ramp if idle scan is deactivated."
        self._ldd_switches().set_state("HV,MOD", "RAMP")
        self._ldd_switches().set_state("CURRENT,MOD", "INT")
        if not self._idle_scan:
            self._stop_ramp()
        self.watchdog_event("start")
    @state
    @transition_to(("start", "idle_scan"))
    def prepare_idle_scan(self):
        self._idle_scan_start = time.perf_counter()
        self._prepare_ramp(prepare_input=False)
        self._start_ramp()
        self.watchdog_event("start")
    @state
    @transition_to(("interrupt", "prepare_idle"))
    def idle_scan(self):
        if not self._idle_scan:
            self.watchdog_event("interrupt")
        elif time.perf_counter() - self._idle_scan_start >= 1/self._frequency:
            self._idle_scan_start = time.perf_counter()
            self._prepare_ramp(prepare_input=False)
            self._start_ramp()
    @state
    @transition_to(("scan", "prepare_idle_scan"))
    @transition_to(("start", "prepare_scan"))
    def idle(self):
        if self._idle_scan:
            self.watchdog_event("scan")
    @state
    @transition_to(("next", "prepare_step"))
    def prepare_scan(self):
        n = self._number_of_samples_per_frame
        with self._data_lock:
            self._scan_data = np.zeros((n*self._n_repeat, 3))
            self._scan_data[:,2] = np.repeat(range(self._n_repeat), n)
            self._measurement_time = np.zeros(n*self._n_repeat)
            self._frequency_buffer = np.zeros(self._number_of_frequencies_per_frame*self._n_repeat)
        self._repeat_no = -1
        self._data_row_index = 0
        self._frequency_row_index = 0
        self._prepare_ramp()
        self._stop_ramp()
        self.log.debug("Scan prepared.")
        self.watchdog_event("next")
    @state
    @transition_to(("next", "wait_ready"))
    @transition_to(("end", "prepare_idle"))
    def prepare_step(self):
        if self._repeat_no >= self._n_repeat:
            if self._interpolate_frequencies:
                self.log.debug("interpolating frequencies.")
                n = self._scan_data.shape[0]
                measurements_times = np.linspace(start=0, stop=(n-1)*self._exposure_time, num=n)
                frequency_times = np.linspace(start=0, stop=(n-1)*self._exposure_time, num=self._frequency_buffer.shape[0])
                self._scan_data[:,0] = np.interp(measurements_times, 
                        frequency_times, 
                        self._frequency_buffer
                    )
            self._idle_value = self._offset
            self.watchdog_event("end")
            self.log.info("Scan done.")
        else:
            self.log.debug(f"Step {self._repeat_no} prepared.")
            if self._repeat_no > 0:
                self._start_ramp()
            else:
                self._stop_ramp()
            self._waiting_start = time.perf_counter()
            self.watchdog_event("next")
    @state
    @transition_to(("next", "record_scan_step"))
    def wait_ready(self):
        time_start = time.perf_counter()
        # We wait 1.1 period the first time to let the ramp reach its initial state.
        if self._repeat_no < 0 and time_start - self._waiting_start > 1.1/self._frequency: 
            self._start_ramp()
            self._waiting_start = time_start
            self._repeat_no = 0
        elif self._repeat_no >= 0 and time_start - self._waiting_start > self._delay_start_acquitition:
            self._stop_ramp()
            self._start_acquisition()
            self.log.debug("Ready to start acquisition.")
            self.watchdog_event("next")
    @state
    @transition_to(("next", "prepare_step"))
    def record_scan_step(self):
        samples_missing_data = self._number_of_samples_per_frame * (self._repeat_no+1) - self._data_row_index
        samples_missing_frequency = self._number_of_frequencies_per_frame * (self._repeat_no+1) - self._frequency_row_index
        if samples_missing_data <= 0 and samples_missing_frequency <= 0:
            self._stop_acquisition()
            self._repeat_no += 1
            self.log.debug("Step done.")
            self.watchdog_event("next")
        else:
            if samples_missing_data > 0 and self._finite_sampling_input().samples_in_buffer >= min(self._chunk_size, samples_missing_data):
                new_data = self._finite_sampling_input().get_buffered_samples()[self._input_channel]
                i = self._data_row_index
                with self._data_lock:
                    self._scan_data[i:i+len(new_data),1] = new_data
                    self._data_row_index += len(new_data)
            if samples_missing_frequency > 0 and self._fzw_sampling().samples_in_buffer >= min(1, samples_missing_frequency):
                new_data = self._fzw_sampling().get_buffered_samples()
                new_frequencies = new_data["frequency"]
                new_timestamps = new_data["timestamp"]
                i = self._frequency_row_index
                data_size = min(self._frequency_buffer.shape[0]-i, len(new_frequencies))
                new_frequencies = new_frequencies[:data_size]
                new_timestamps = new_timestamps[:data_size]
                with self._data_lock:
                    self._frequency_buffer[i:i+len(new_frequencies)] = new_frequencies
                    #self._measurement_time[i:i+len(new_frequencies)] = new_timestamps
                    self._frequency_row_index += len(new_frequencies)
    @state
    @transition_from(("stop", "*"))
    @transition_to(("start_idle", "prepare_idle"))
    def stopped(self):
        self.log.debug("stopped")
        self._finite_sampling_input().stop_buffered_acquisition()
        self._fzw_sampling().stop_buffered_acquisition()

    # ExcitationScannerInterface
    @property
    def scan_running(self) -> bool:
        "Return True if a scan can be launched."
        return self.watchdog_state in self._scanning_states
    @property
    def state_display(self) -> str:
        return self.watchdog_state.replace("_", " ")
    def start_scan(self) -> None:
        "Start scanning in a non_blocking way."
        if not self.scan_running:
            self.watchdog_event("start_scan")
    def stop_scan(self) -> None:
        "Stop scanning in a non_blocking way."
        if self.scan_running:
            self.watchdog_event("interrupt_scan")
    @property
    def constraints(self) -> ExcitationScannerConstraints:
        "Get the list of control variables for the scanner."
        return self._constraints
    def set_control(self, variable: str, value) -> None:
        "Set a control variable value."
        if not self.constraints.variable_in_range(variable, value):
            raise ValueError(f"Cannot set {variable}={value}")
        if variable == "grating":
            self._cem_control().set_setpoint("grating", value)
        elif variable == "current":
            self._ldd_control().set_setpoint("current", value)
        elif variable == "offset":
            self._ldd_control().set_setpoint("offset", value)
            self._offset = value
        elif variable == "span":
            self._ldd_control().set_setpoint("span", value)
            self._span = value
        elif variable == "bias":
            self._ldd_control().set_setpoint("bias", value)
            self._bias = value
        elif variable == "frequency":
            self._ldd_control().set_setpoint("frequency", value)
            self._frequency = value
        elif variable == "fzw probe rate":
            self._fzw_rate = value
        elif variable == "interpolate_frequencies":
            self._interpolate_frequencies = bool(value)
        elif variable == 'idle_scan':
            self._idle_scan = bool(value)
        elif variable == 'duty':
            self._ldd_control().set_setpoint("duty", value)
            self._duty = value
        elif variable == "delay_start_acquitition":
            self._delay_start_acquitition = value


    def get_control(self, variable: str):
        "Get a control variable value."
        if variable == "grating":
            return self._cem_control().get_setpoint("grating")
        elif variable == "current":
            return self._ldd_control().get_setpoint("current")
        elif variable == "offset":
            return self._ldd_control().get_setpoint("offset")
        elif variable == "span":
            return self._ldd_control().get_setpoint("span")
        elif variable == "bias":
            return self._ldd_control().get_setpoint("bias")
        elif variable == "frequency":
            return self._ldd_control().get_setpoint("frequency")
        elif variable == "fzw probe rate":
            return self._fzw_rate
        elif variable == "interpolate_frequencies":
            return self._interpolate_frequencies
        elif variable == 'idle_scan':
            return self._idle_scan
        elif variable == 'duty':
            return self._ldd_control().get_setpoint("duty")
        elif variable == "delay_start_acquitition":
            return self._delay_start_acquitition
        else:
            raise ValueError(f"Unknown variable {variable}")
    def get_current_data(self) -> np.ndarray:
        "Return current scan data."
        return self._scan_data
    def set_exposure_time(self, time:float) -> None:
        "Set exposure time for one data point."
        if not self.constraints.exposure_in_range(time):
            raise ValueError(f"Unable to set exposure to {time}")
        self._exposure_time = time

    def set_repeat_number(self, n:int) -> None:
        "Set number of repetition of each segment of the scan."
        if not self.constraints.repeat_in_range(n):
            raise ValueError(f"Unable to set repeat to {n}")
        self._n_repeat = n
    def get_exposure_time(self) -> float:
        "Get exposure time for one data point."
        return self._exposure_time
    def get_repeat_number(self) -> int:
        "Get number of repetition of each segment of the scan."
        return self._n_repeat
    def get_idle_value(self) -> float:
        "Get idle value."
        if len(self._scan_data) <= 0:
            return 0.0
        roi = self._scan_data[:,2] == self._scan_data[0,2]
        frequencies = self._scan_data[roi,0]
        return np.interp(self._idle_value, 
                         np.linspace(start=self._offset-self._span/2, stop=self._offset+self._span/2, num=len(frequencies)), 
                         frequencies
                         )
    def set_idle_value(self, n:float) -> None:
        "Set idle value."
        if len(self._scan_data) <= 0:
            return 
        roi = self._scan_data[:,2] == self._scan_data[0,2]
        frequencies = self._scan_data[roi,0]
        self._idle_value =  np.interp(n, 
                                      frequencies,
                                      np.linspace(start=self._offset-self._span/2, 
                                                  stop=self._offset+self._span/2, 
                                                  num=len(frequencies)
                                      ))
    @property
    def data_column_names(self) -> Iterable[str]:
        "Return an iterable of the columns names for the return value of `get_current_data`."
        return ["Frequency", self._input_channel, "Step number", "Time"]
    @property
    def data_column_unit(self) -> Iterable[str]:
        "Return an iterable of the columns units for the return value of `get_current_data`."
        units = self._finite_sampling_input().constraints.channel_units
        return ["Hz", units[self._input_channel], "", "s"]
    @property
    def frequency_column_number(self) -> int:
        "Return the column number for the frequency in the data returned by `get_current_data`."
        return 0
    @property
    def step_number_column_number(self) -> int:
        "Return the column number for the step number in the data returned by `get_current_data`."
        return 2
    @property
    def time_column_number(self) -> int:
        "Return the column number for the time in the data returned by `get_current_data`."
        return 3
    @property
    def data_column_number(self) -> Iterable[int]:
        "Return an iterable of column numbers for adressing the data returned by `get_current_data`."
        return [1]

