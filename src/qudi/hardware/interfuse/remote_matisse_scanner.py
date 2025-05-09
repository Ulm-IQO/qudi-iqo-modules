import time
from enum import Enum

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import Mutex
from qudi.util.enums import SamplingOutputMode
from qudi.interface.excitation_scanner_interface import ExcitationScannerInterface, ExcitationScannerConstraints
from qudi.util.network import netobtain

from PySide2 import QtCore
from fysom import Fysom
import numpy as np
from numpy.polynomial import polynomial


class MatisseScanMode(Enum):
    INCREASE_VOLTAGE_STOP_NEITHER = 0
    DECREASE_VOLTAGE_STOP_NEITHER = 1
    INCREASE_VOLTAGE_STOP_LOW = 2
    DECREASE_VOLTAGE_STOP_LOW = 3
    INCREASE_VOLTAGE_STOP_UP = 4
    DECREASE_VOLTAGE_STOP_UP = 5
    INCREASE_VOLTAGE_STOP_EITHER = 6
    DECREASE_VOLTAGE_STOP_EITHER = 7

class RemoteMatisseScanner(ExcitationScannerInterface):
    _finite_sampling_input = Connector(name='input', interface='FiniteSamplingInputInterface')
    _matisse = Connector(name='matisse', interface='ProcessControlInterface')
    _matisse_sw = Connector(name='matisse_sw', interface='SwitchInterface')
    _wavemeter = Connector(name='wavemeter', interface='DataInStreamInterface')

    _input_channels = ConfigOption(name="input_channels")
    _chunk_size = ConfigOption(name="chunk_size", default=10)
    _watchdog_delay = ConfigOption(name="watchdog_delay", default=0.2)
    _max_scan_speed = ConfigOption(name="max_scan_speed", default=0.01)

    _scan_data = StatusVar(name="scan_data", default=np.empty((0,3)))
    _exposure_time = StatusVar(name="exposure_time", default=1e-2)
    _sleep_time_before_scan = StatusVar(name="sleep_time_before_scan", default=60)
    _n_repeat = StatusVar(name="n_repeat", default=1)
    _idle_value = StatusVar(name="idle_value", default=0.4)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log.info(self._matisse.interface)
        self._watchdog_state = Fysom({
            "initial": "stopped",
            "events": [
                {"name":"start_idle", "src":"prepare_idle", "dst":"idle"},
                {"name":"start_idle", "src":"stopped", "dst":"prepare_idle"},

                {"name":"start_scan", "src":"idle", "dst":"prepare_scan"},
                {"name":"start_prepare_step", "src":"prepare_scan", "dst":"prepare_step"},
                {"name":"start_wait_first_value", "src":"prepare_step", "dst":"wait_ready"},
                {"name":"start_scan_step", "src":"wait_ready", "dst":"record_scan_step"},
                {"name":"step_done", "src":"record_scan_step", "dst":"prepare_step"},
                {"name":"end_scan", "src":"prepare_step", "dst":"prepare_idle"},

                {"name":"interrupt_scan", "src":["prepare_scan","prepare_step","wait_ready","record_scan_step"], "dst":"prepare_idle"},

                {"name":"stop_watchdog", "src":"*", "dst":"stopped"},
            ],
            # "callbacks":{
            #     "on_start_scan": self._on_start_scan,
            #     "on_prepare_idle": self._on_stop_scan,
            #     "on_step_done": self._on_step_done,
            # }
        })
        self._scanning_states = {"prepare_scan", "prepare_step", "wait_ready", "record_scan_step"}
        self._watchdog_lock = Mutex()
        self._data_lock = Mutex()
        self._constraints = ExcitationScannerConstraints((0,0),(0,0),(0,0),[],[],[],[])
        self._waiting_start = time.perf_counter()
        self._repeat_no = 0
        self._data_row_index = 0
        self._watchdog_timer = QtCore.QTimer(parent=self)
        self._conversion_offset = 0.0
        self._scan_start_time = 0
        self._step_start_time = 0


    # Internal utilities
    @property
    def watchdog_state(self):
        with self._watchdog_lock:
            return self._watchdog_state.current
    def watchdog_event(self, event):
        with self._watchdog_lock:
            self._watchdog_state.trigger(event)

    @property
    def _scan_mini(self):
        v = self._matisse().get_setpoint("scan lower limit")
        return netobtain(v)
    @_scan_mini.setter
    def _scan_mini(self, v):
        self._matisse().set_setpoint("scan lower limit", v)
    @property
    def _scan_maxi(self):
        v = self._matisse().get_setpoint("scan upper limit")
        return netobtain(v)
    @_scan_maxi.setter
    def _scan_maxi(self, v):
        self._matisse().set_setpoint("scan upper limit", v)
    @property
    def _conversion_factor(self):
        v = self._matisse().get_setpoint("conversion factor")
        return netobtain(v)
    @_conversion_factor.setter
    def _conversion_factor(self, v):
        self._matisse().set_setpoint("conversion factor", v)
    @property
    def _scan_speed(self):
        v = self._matisse().get_setpoint("scan rising speed")
        return netobtain(v)
    @_scan_speed.setter
    def _scan_speed(self, value):
        self._matisse().set_setpoint("scan rising speed", value)
    @property
    def _scan_speed(self):
        v = self._matisse().get_setpoint("scan rising speed")
        return netobtain(v)
    @_scan_speed.setter
    def _scan_speed(self, value):
        self._matisse().set_setpoint("scan rising speed", value)
    @property
    def _fall_speed(self):
        v = self._matisse().get_setpoint("scan falling speed")
        return netobtain(v)
    @_fall_speed.setter
    def _fall_speed(self, value):
        self._matisse().set_setpoint("scan falling speed", value)
    @property
    def _scan_value(self):
        v = self._matisse().get_setpoint("scan value")
        return netobtain(v)
    @_scan_value.setter
    def _scan_value(self, v):
        self._matisse().set_setpoint("scan value", v)
    @property
    def _scanning(self):
        v = self._matisse_sw().get_state("Scan Status")
        return netobtain(v) == "RUN"


    @property 
    def _number_of_samples_per_frame(self):
        return round((self._scan_maxi - self._scan_mini) / self._scan_speed / self._exposure_time)
    @property
    def _number_of_wavemeter_point_per_frame(self):
        return round(self._number_of_samples_per_frame * self._exposure_time * self._wavemeter().sample_rate * 1.5)
    def _watchdog(self):
        try:
            time_start = time.perf_counter()
            watchdog_state = self.watchdog_state
            if watchdog_state == "prepare_idle": 
                try:
                    if self._finite_sampling_input().module_state() == 'locked':
                        self._finite_sampling_input().stop_buffered_acquisition()
                    if self._matisse_sw().get_state("Scan Status") == "RUN":
                        self._matisse_sw().set_state("Scan Status", "STOP")
                except Exception as e:
                    self.log.warn(f"Could not prepare idling: {e}")
                self.watchdog_event("start_idle")
            elif watchdog_state == "idle": 
                if self._scan_value != self._idle_value:
                    self._scan_value = self._idle_value
            elif watchdog_state == "prepare_scan": 
                n = self._number_of_samples_per_frame
                self.log.debug(f"Preparing scan from {self._scan_mini} to {self._scan_maxi} with {n} points.")
                with self._data_lock:
                    self._scan_data = np.zeros((n*self._n_repeat, 3 + len(self._input_channels)))
                    self._scan_data[:,self.frequency_column_number] = np.tile(np.linspace(start=self._scan_mini, stop=self._scan_maxi, num=n), self._n_repeat)*self._conversion_factor + self._conversion_offset
                    self._scan_data[:,self.step_number_column_number] = np.repeat(range(self._n_repeat), n)
                    self._scan_data[:,self.time_column_number] = range(self._n_repeat*n)

                self._repeat_no = 0
                self._data_row_index = 0
                try:
                    self._finite_sampling_input().set_sample_rate(1/self._exposure_time)
                    self._finite_sampling_input().set_frame_size(n)

                    self._finite_sampling_input().set_active_channels(self._input_channels)
                except Exception as e:
                    self.log.warn(f"Could not prepare the scan: {e}")
                    self.watchdog_event("interrupt_scan")
                self._scan_start_time = time.perf_counter()
                self.log.debug("Scan prepared.")
                self.watchdog_event("start_prepare_step")
            elif watchdog_state == "prepare_step": 
                if self._repeat_no >= self._n_repeat:
                    poly = polynomial.polyfit(self.scanner_positions, self.sampled_frequencies, deg=1)
                    self.log.debug(f"Fitted polynomial {poly}.")
                    if not any(np.isnan(poly)):
                        self._conversion_offset, self._conversion_factor = poly
                    self._update_conversion()
                    self.watchdog_event("end_scan")
                    self.log.info("Scan done.")
                else:
                    try:
                        if self._wavemeter().module_state() == 'locked':
                            self._wavemeter().stop_stream()
                        self._wavemeter().configure(
                            active_channels=None,
                            streaming_mode=None,
                            channel_buffer_size = max(self._number_of_wavemeter_point_per_frame, self._wavemeter().channel_buffer_size),
                            sample_rate=None
                        )
                        if self._finite_sampling_input().module_state() == 'locked':
                            self._finite_sampling_input().stop_buffered_acquisition()
                        self.log.debug("Step prepared, starting wait.")
                        self._waiting_start = time.perf_counter()
                        if self._scan_value < self._scan_mini:
                            self._matisse().set_setpoint("scan mode", MatisseScanMode.INCREASE_VOLTAGE_STOP_LOW.value)
                            self._matisse_sw().set_state("Scan Status", "RUN")
                        elif self._scan_value > self._scan_mini:
                            self._matisse().set_setpoint("scan mode", MatisseScanMode.DECREASE_VOLTAGE_STOP_LOW.value)
                            self._matisse_sw().set_state("Scan Status", "RUN")
                        self.watchdog_event("start_wait_first_value")
                    except Exception as e:
                        self.log.warn(f"Could not prepare the step: {e}")
                        self.watchdog_event("interrupt_scan")
            elif watchdog_state == "wait_ready": 
                if time_start - self._waiting_start > self._sleep_time_before_scan or not self._scanning:
                    self.log.debug("Ready.")
                    try:
                        self._matisse().set_setpoint("scan mode", MatisseScanMode.INCREASE_VOLTAGE_STOP_LOW.value)
                        self._matisse_sw().set_state("Scan Status", "RUN")
                        self._finite_sampling_input().start_buffered_acquisition()
                        self.watchdog_event("start_scan_step")
                        self._wavemeter().start_stream()
                        self._step_start_time = time.perf_counter()
                    except Exception as e:
                        self.log.warn(f"Could not start the step: {e}")
                        self.watchdog_event("interrupt_scan")
            elif watchdog_state == "record_scan_step": 
                samples_missing = self._number_of_samples_per_frame * (self._repeat_no+1) - self._data_row_index
                if samples_missing <= 0:
                    self.sampled_frequencies, _ = netobtain(self._wavemeter().read_data())
                    self.sampled_frequencies = netobtain(self.sampled_frequencies)
                    self._wavemeter().stop_stream()
                    self.scanner_positions = np.linspace(start=self._scan_mini, stop=self._scan_maxi, num=len(self.sampled_frequencies))
                    n = self._number_of_samples_per_frame
                    offset = n * self._repeat_no
                    interp = np.interp(
                        np.linspace(start=self._scan_mini, stop=self._scan_maxi, num=n),
                        self.scanner_positions,
                        self.sampled_frequencies
                    )
                    self.log.debug(f"Preparing interpolation. interp={interp.shape}, scndata {self._scan_data[offset:(offset+n),self.frequency_column_number].shape}")
                    self._scan_data[offset:(offset+n),self.frequency_column_number] = interp
                    self._scan_data[offset:(offset+n),self.time_column_number] = (self._step_start_time - self._scan_start_time) + np.arange(n)*self._exposure_time
                    self._repeat_no += 1
                    self.log.debug("Step done.")
                    self.watchdog_event("step_done")
                elif self._finite_sampling_input().samples_in_buffer < min(self._chunk_size, samples_missing):
                    pass
                else:
                    i = self._data_row_index
                    l = 0
                    with self._data_lock:
                        new_data = self._finite_sampling_input().get_buffered_samples()
                        for (chnum, ch) in enumerate(self._input_channels):
                            self._scan_data[i:i+len(new_data[ch]),3+chnum] = new_data[ch]
                            l = len(new_data[ch])
                        self._data_row_index += l
            elif watchdog_state == "stopped": 
                self.log.debug("stopped")
                if self._finite_sampling_input().module_state() == 'locked':
                    self._finite_sampling_input().stop_buffered_acquisition()
            time_end = time.perf_counter()
            time_overhead = time_end-time_start
            new_time = max(0, self._watchdog_delay - time_overhead)
            self._watchdog_timer.start(new_time*1000)
        except:
            self.log.exception("")

    # Activation/De-activation
    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        scan_limits = (0.0, 0.7)
        self._constraints = ExcitationScannerConstraints(
            exposure_limits=(1e-4,1),
            repeat_limits=(1,1000),
            idle_value_limits=(0.0, 0.7),
            control_variables=("Conversion factor", "Conversion offset", "Minimum scan", "Maximum scan", "Minimum frequency", "Maximum frequency", "Frequency step", "Sleep before scan", "Idle active", "Idle value", "Idle frequency"),
            control_variable_limits=((0.0, 1e17), (0.0, 1e17), scan_limits, scan_limits, (0.0, 1e17), (0, 1e17), (0.0, 1e17), (0.0, 3000.0), (False, True), scan_limits, (-1e17, 1e17)),
            control_variable_types=(float, float, float, float, float, float, float, float, bool, float, float),
            control_variable_units=("Hz", "Hz", "", "", "Hz", "Hz", "Hz", "s", "", "", "Hz")
        )
        self.watchdog_event("start_idle")
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self._watchdog, QtCore.Qt.QueuedConnection)
        self._watchdog_timer.start(self._watchdog_delay)
        self._wavemeter().start_stream()
        time.sleep(1)
        self._conversion_offset = float(netobtain(self._wavemeter().read_single_point())[0][0])
        self.log.debug(self._conversion_offset)
        self._wavemeter().stop_stream()
        self._update_conversion()

    def on_deactivate(self):
        self.watchdog_event("stop_watchdog")
    def get_frame_size(self):
        return self._number_of_samples_per_frame
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
    def _update_conversion(self):
        freq_mini = self.get_control("Minimum frequency")
        freq_maxi = self.get_control("Maximum frequency")
        freq_step = self.get_control("Frequency step")
        idle_value = self.get_control("Idle frequency")
        self._scan_mini = max(0.0, (freq_mini-self._conversion_offset)/self._conversion_factor)
        self._scan_maxi = min(0.7, (freq_maxi-self._conversion_offset)/self._conversion_factor)
        self._idle_value = min(0.7, max(0.0, (idle_value-self._conversion_offset)/self._conversion_factor))
        lims = (self._conversion_offset, 0.7*self._conversion_factor + self._conversion_offset)
        self._constraints.set_limits("Minimum frequency", *lims)
        self._constraints.set_limits("Maximum frequency", *lims)
        self._constraints.set_limits("Frequency step", 0, lims[1]-self._conversion_offset)
    def set_control(self, variable: str, value) -> None:
        "Set a control variable value."
        if not self.constraints.variable_in_range(variable, value):
            raise ValueError(f"Cannot set {variable}={value}")
        if variable == "Conversion factor":
            self._conversion_factor = value
            self._update_conversion()
        elif variable == "Conversion offset":
            self._conversion_offset = value
            self._update_conversion()
        elif variable == "Minimum scan":
            self._scan_mini = value
        elif variable == "Maximum scan":
            self._scan_maxi = value
        elif variable == "Minimum frequency":
            self.set_control("Minimum scan", (value-self._conversion_offset)/self._conversion_factor)
        elif variable == "Maximum frequency":
            self.set_control("Maximum scan", (value-self._conversion_offset)/self._conversion_factor)
        elif variable == "Frequency step":
            val_scan = value/self._conversion_factor
            self._scan_speed = min(val_scan/self._exposure_time, self._max_scan_speed)
            self._fall_speed = min(10*val_scan/self._exposure_time, self._max_scan_speed)
        elif variable == "Sleep before scan":
            self._sleep_time_before_scan = value
        elif variable == "Idle active":
            self._matisse().set_activity_state("scan value", value)
        elif variable == "Idle value":
            self.log.debug(f"Setting idle value to {value}")
            self._idle_value = value
        elif variable == "Idle frequency":
            self.log.debug(f"Setting idle frequency to {value}")
            self.set_control("Idle value", (value-self._conversion_offset)/self._conversion_factor)
    def get_control(self, variable: str):
        "Get a control variable value."
        if variable == "Conversion factor":
            return self._conversion_factor
        elif variable == "Conversion offset":
            return self._conversion_offset
        elif variable == "Minimum scan":
            return self._scan_mini
        elif variable == "Maximum scan":
            return self._scan_maxi
        elif variable == "Minimum frequency":
            return self._scan_mini*self._conversion_factor + self._conversion_offset
        elif variable == "Maximum frequency":
            return self._scan_maxi*self._conversion_factor + self._conversion_offset
        elif variable == "Frequency step":
            return self._scan_speed*self._exposure_time*self._conversion_factor
        elif variable == "Sleep before scan":
            return self._sleep_time_before_scan
        elif variable == "Idle active":
            v = self._matisse().get_activity_state("scan value")
            return netobtain(v)
        elif variable == "Idle value":
            return self._idle_value
        elif variable == "Idle frequency":
            return self._idle_value*self._conversion_factor + self._conversion_offset
        else:
            raise ValueError(f"Unknown variable {variable}")
    def get_current_data(self) -> np.ndarray:
        "Return current scan data."
        return self._scan_data
    @property
    def data_column_names(self):
        return ["Frequency", "Step number", "Time"] + list(self._input_channels)
    @property
    def data_column_unit(self):
        units = self._finite_sampling_input().constraints.channel_units
        return ["Hz", "", "s"] + [units[ch] for ch in self._input_channels]
    @property
    def data_column_number(self):
        return [i+3 for i in range(len(self._input_channels))]
    @property
    def frequency_column_number(self):
        return 0
    @property
    def step_number_column_number(self):
        return 1
    @property
    def time_column_number(self):
        return 2
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
        return self._idle_value * self._conversion_factor + self._conversion_offset
    def set_idle_value(self, v):
        tension = (v-self._conversion_offset) / self._conversion_factor
        if not self.constraints.idle_value_in_range(tension):
            tension=0.0
        self._idle_value = tension

