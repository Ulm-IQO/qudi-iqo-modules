import time
from dataclasses import dataclass
from typing import Union

from PySide2 import QtCore
from fysom import Fysom
import numpy as np

from sirah_matisse_commander import SirahMatisseCommanderDevice, MatisseControlStatus

from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.core.connector import Connector
from qudi.interface.process_control_interface import ProcessControlConstraints, ProcessControlInterface
from qudi.interface.switch_interface import SwitchInterface
from qudi.interface.sampled_finite_state_interface import SampledFiniteStateInterface, initial, transition_to, transition_from, state

SPEED_OF_LIGHT = 299792458

@dataclass
class MatisseCommanderGoToConfiguration:
    """
    Holds the configuration for the go to command.
    """
    birefringent_mode: float = 50 # GHz (default)
    birefringent_scan_range: int = 1500 # steps (default)
    birefringent_scan_increment: int = 20 # steps (default)
    thin_etalon_scan_increment: int = 80 # steps (default)
    thin_etalon_signal_start: int = 40000 # steps (default)
    thin_etalon_signal_end: int = 50000 # steps (default)
    thin_etalon_set_point_scan_increment: int = 25 # steps (default)
    thin_etalon_set_point_scan_range: int = 500 # steps (default)
    thin_etalon_free_spectral_range: float = 260 # GHz (default)
    pzetl_scan_increment: float = 0.0125 # (default)
    pzetl_full_scan_range: float = 0.4 # (default)
    pzetl_maximal_difference: float = 0.3 # GHz (default)
    pzetl_lower_point: float = -0.85 # (default)
    pzetl_upper_point: float = 0.85 # (default)
    pzetl_free_spectral_range: float = 18 # (default)
    pzetl_relaxation: float = 1000 # ms (default)
    spzt_lower_point: float = 0.01 # (default)
    spzt_upper_point: float = 0.65 # (default)
    precision: float = 0.5 # (GHz)

class MatisseCommander(ProcessControlInterface, SwitchInterface, SampledFiniteStateInterface):
    """
    A Light proxy to talk to the MatisseCommander program.

    Copy and paste example configuration:
    ```yaml
    matisse:
        connect:
            wavemeter: wavemeter # required only if go to routine is used.
        config:
            address: 'localhost' # default
            port: 30000 # default
            go_to_position: # go to position parameters
                birefringent_mode: 50 # GHz (default)
                birefringent_scan_range: 1500 # steps (default)
                birefringent_scan_increment: 20 # steps (default)
                thin_etalon_scan_increment: 80 # steps (default)
                thin_etalon_signal_start: 40000 # steps (default)
                thin_etalon_signal_end: 60000 # steps (default)
                thin_etalon_signal_end: 60000 # steps (default)
                thin_etalon_set_point_scan_increment: 25 # steps (default)
                thin_etalon_set_point_scan_range: 500 # steps (default)
                thin_etalon_free_spectral_range: 260 # GHz (default)
                pzetl_scan_increment: 0.0125 # (default)
                pzetl_full_scan_range: 0.4 # (default)
                pzetl_maximal_difference: 0.3 # GHz (default)
                pzetl_lower_point: -0.85 # (default)
                pzetl_upper_point: 0.85 # (default)
                pzetl_free_spectral_range: 18 # (default)
                pzetl_relaxation: float = 1000 # ms (default)
                spzt_lower_point: 0.01 # (default)
                spzt_upper_point: 0.65 # (default)
                precision: 0.5 # (GHz)
    ```
    """
    _wavemeter = Connector(name='wavemeter', interface='DataInStreamInterface', optional = True)
    "A connector to the wavemeter of the matisse."

    _address = ConfigOption(name="address", default="localhost")
    """Configuration option for the address at which the MatisseCommander program lives. most likely it is on the same computer, so the default is "localhost"""
    _port = ConfigOption(name="port", default=3000)
    """Configuration option for the port at which the MatisseCommander program listens"""
    _go_to_position_config = ConfigOption(name="go_to_position", default=dict(), constructor=lambda yaml_data: MatisseCommanderGoToConfiguration(**yaml_data))
    _watchdog_delay = ConfigOption(name="watchdog_delay", default=0.1)

    conversionfactor = StatusVar(default=1)
    "Internal status variable to remember the conversion factor between tension and frequency."
    _go_to_target = StatusVar(default=0.0)
    "Target frequency for the Go To Position procedure"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._device = None
        self._constraints_process = None
        self._idle_activated = False
        self._bifi_scan = []
        self._te_scan = []
        self._te_scan_direction = 0
        self._last_bifi_position = 0
        self._last_pzetl_position = 0
        self._relax_timer = -1
        self._te_setpoint_scan = []
        self._last_te_position = 0
        self._reoptimize_bifi = False
        self._reoptimize_te = False
        self._pzetl_previous_error = np.inf
        self._te_previous_error = np.inf
        self._bifi_reset_countdown = 0

    # Qudi base 
    def on_activate(self):
        self._device = SirahMatisseCommanderDevice(self._address, self._port)
        self._device.connect()
        maximum_possible_wavelength = self._device.query('MOTORBIREFRINGENT:MAXIMUM') * 1e-9
        minimum_possible_frequency = SPEED_OF_LIGHT / maximum_possible_wavelength
        self._constraints_process = ProcessControlConstraints(
            setpoint_channels=["piezo ref cell", "piezo slow", "scan rising speed", 
                               "scan falling speed", "scan lower limit", "scan upper limit", 
                               "conversion factor", "scan mode", "scan value", "go to target"],
            process_channels=["diode power dc"],
            units={"scan rising speed":"1/s", "scan falling speed":"1/s", "conversion factor":"MHz", "go to target": "Hz"},
            limits={"scan lower limit":(0.0,0.7), "scan upper limit":(0.0,0.7), "scan mode":(0,7), "scan value":(0.0,0.7), "go to target": (minimum_possible_frequency, np.inf)},
            dtypes={"scan mode":int}
        )
        self._idle_activated = False
        self._bifi_reset_countdown = 0
        self.enable_watchdog()
        self.start_watchdog()

    def on_deactivate(self):
        retry = 5
        while self._watchdog_state != "idle":
            if retry < 0:
                break
            self.watchdog_event("stop")
            time.sleep(0.5)
            retry -= 1
        self.disable_watchdog()
        if self._device is not None:
            self._device.disconnect()

    @property
    def _current_frequency(self) -> Union[None, float]:
        if self._wavemeter() is None:
            return None
        else:
            freq = np.nan
            while np.isnan(freq):
                freq_array,_ = self._wavemeter().read_single_point()
                freq = freq_array[0]
            return freq

    def _scale_position(self, prev_position: int, position: int, next_position: int, prev_frequency: float, frequency: float, next_frequency: float) -> int:
        diff_prev = prev_frequency - self._go_to_target
        diff = frequency - self._go_to_target
        diff_next = next_frequency - self._go_to_target
        if np.sign(diff) != np.sign(diff_prev):
            step = prev_frequency - frequency
            t = np.abs(diff_prev/step) # if t is 1 scale to previous position, if it is 0 scale to current position
            return int(position * (1-t) + prev_position * t)
        elif np.sign(diff) != np.sign(diff_next):
            step = next_frequency - frequency
            t = np.abs(diff_next/step) # if t is 1 scale to previous position, if it is 0 scale to current position
            return int(position * (1-t) + next_position * t)
        else:
            return position

    # ProcessControlInterface
    def get_process_value(self, channel):
        if channel == "diode power dc":
            return self._device.diode_power_dc
        else:
            raise ValueError(f'Invalid process channel specifier "{channel}".')
    def set_setpoint(self, channel, value):
        if self.watchdog_state != "idle":
            self.log.warn("Go to position procedure in progress, cannot set {channel} to {value}.")
            return
        if channel == "piezo ref cell":
            self._device.piezo_ref_cell = value
        elif channel == "piezo slow":
            self._device.piezo_slow = value
        elif channel == "scan rising speed":
            if self._device.set('SCAN:RISINGSPEED', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan falling speed": 
            if self._device.set('SCAN:FALLINGSPEED', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan lower limit": 
            if self._device.set('SCAN:LOWERLIMIT', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan upper limit":
            if self._device.set('SCAN:UPPERLIMIT', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan mode":
            if self._device.set('SCAN:MODE', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan value":
            if not self._idle_activated:
                return
            if self._device.set('SCAN:NOW', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "conversion factor":
            self.conversionfactor = value
        elif channel == "go to target":
            self._go_to_target = value
        else:
            raise ValueError(f'Invalid process channel specifier "{channel}".')
    def get_setpoint(self, channel):
        if channel == "piezo ref cell":
            return self._device.piezo_ref_cell
        elif channel == "piezo slow":
            return self._device.piezo_slow
        elif channel == "scan rising speed":
            return self._device.query('SCAN:RISINGSPEED')
        elif channel == "scan falling speed": 
            return self._device.query('SCAN:FALLINGSPEED')
        elif channel == "scan lower limit": 
            return self._device.query('SCAN:LOWERLIMIT')
        elif channel == "scan upper limit":
            return self._device.query('SCAN:UPPERLIMIT')
        elif channel == "scan mode":
            return self._device.query('SCAN:MODE')
        elif channel == "scan value":
            return self._device.query('SCAN:NOW')
        elif channel == "conversion factor":
            return self.conversionfactor
        elif channel == "go to target":
            return self._go_to_target
        else:
            raise ValueError(f'Invalid process channel specifier "{channel}".')
    def get_activity_state(self, channel):
        if channel == "scan value":
            return self._idle_activated
        return True
    def set_activity_state(self, channel, active):
        if channel == "scan value":
            self._idle_activated = active
    @property
    def constraints(self):
        return self._constraints_process

    # SwitchInterface
    def name(self):
        return "Matisse laser"
    def available_states(self):
        return {
            "Scan Status": ("STOP", "RUN"),
            "Go to Position": ("Idle", "Running"),
        }
    def get_state(self, switch):
        if switch == "Scan Status":
            v = self._device.query('SCAN:STATUS')
            if v == MatisseControlStatus.RUN:
                return "RUN"
            else:
                return "STOP"
        elif switch == "Go to Position":
            if self._watchdog_state == "idle":
                return "Idle"
            else:
                return "Running"
        else:
            raise ValueError(f'Invalid switch specifier "{switch}".')
    def set_state(self, switch, state):
        if switch == "Scan Status":
            if self.watchdog_state != "idle":
                self.log.warn("Go to position procedure in progress, cannot set {switch} to {state}.")
                return
            if state == "RUN":
                v = "RUN"
            else:
                v = "STOP"
            self._device.set('SCAN:STATUS', v)
        elif switch == "Go To Position":
            if self._wavemeter() is None:
                self.log.error("Cannot perform go to position procedure without a wavemeter connected.")
                return
            if state == "Running":
                self.watchdog_event("start")
            else:
                self.watchdog_event("stop")
        else:
            raise ValueError(f'Invalid switch specifier "{switch}".') 
        
    # SampledFiniteStateInterface
    @state
    @initial
    @transition_to(("start", "prepare_procedure"))
    @transition_from(("stop", "*"))
    def idle(self):
        "Do nothing"
        pass
    @state
    @transition_to(("next", "prepare_bifi_scan"))
    def prepare_procedure(self):
        "Set piezo scan range and BiFi wavelength. Disable control loops and scans."
        self.log.info("Preparing go to position procedure.")
        pos_min = self._go_to_position_config.spzt_lower_point
        pos_max = self._go_to_position_config.spzt_upper_point
        self.log.info(f"Setting piezo scan range to [{pos_min}, {pos_max}].")
        self._device.set('SCAN:LOWERLIMIT', pos_min)
        self._device.set('SCAN:UPPERLIMIT', pos_max)
        self.log.info(f"Stopping piezo etalon control loop.")
        self._device.set('PIEZOETALON:CONTROLSTATUS', 'STOP')
        self.log.info(f"Stopping thin etalon control loop.")
        self._device.set('THINETALON:CONTROLSTATUS', 'STOP')
        self.log.info("Resetting piezo etalon")
        self._device.set('PIEZOETALON:BASELINE', (self._go_to_position_config.pzetl_lower_point + self._go_to_position_config.pzetl_upper_point)/2)
        self.log.info("Resetting slow piezo")
        self._device.set("SLOWPIEZO:NOW", (self._go_to_position_config.spzt_lower_point + self._go_to_position_config.spzt_upper_point)/2)
        if self._wavemeter().module_state() != 'locked':
            self._wavemeter().start_stream()
        self._bifi_scan = []
        self._te_scan = []
        self._last_bifi_position = 0
        self._last_pzetl_position = 0
        self._relax_timer = -1
        self._te_setpoint_scan = []
        self._last_te_position = 0
        self._reoptimize_bifi = False
        self._reoptimize_te = False
        self.watchdog_event("next")
    @state
    @transition_to(("next", "bifi_scan"))
    @transition_to(("skip_step", "prepare_te_scan"))
    def prepare_bifi_scan(self):
        "Prepare the positions scanned by the birefringent filter."
        status = self._device.query('MOTORBIREFRINGENT:STATUS')
        bifi_target_wavelength = 1e9 * SPEED_OF_LIGHT / self._go_to_target
        bifi_current_frequency = 1e9 * SPEED_OF_LIGHT / self._device.query('MOTORBIREFRINGENT:WAVELENGTH')
        status_ready = status & 0xff == 0x02
        if (self._bifi_reset_countdown > 0) and not self._reoptimize_bifi and np.abs(self._current_frequency - self._go_to_target) < self._go_to_position_config.thin_etalon_free_spectral_range*1e9/2:
            self.log.info(f"Current set frequency is less than {self._go_to_position_config.thin_etalon_free_spectral_range/2} GHz from current frequency. Skipping Bifi step.")
            self._bifi_reset_countdown -= 1
            self.watchdog_event("skip_step")
        elif not status_ready:
            pass
        elif self._bifi_reset_countdown <= 0 or np.abs(bifi_current_frequency - self._go_to_target) > self._go_to_position_config.thin_etalon_free_spectral_range*1e9/2:
            self._bifi_reset_countdown = 2
            self.log.debug(f"bifi current freq: {bifi_current_frequency}, current frequency:{self._current_frequency}, go_to_target {self._go_to_target}, delta {np.abs(bifi_current_frequency - self._go_to_target)*1e-9} GHz, threshold {self._go_to_position_config.thin_etalon_free_spectral_range} GHz")
            self.log.info(f"Setting BiFi wavelength to {bifi_target_wavelength} nm.")
            self._device.set('MOTORBIREFRINGENT:WAVELENGTH', bifi_target_wavelength)
            self.log.info("Resetting thin etalon position")
            self._device.set('MOTORTHINETALON:POSITION', (self._go_to_position_config.thin_etalon_signal_start + self._go_to_position_config.thin_etalon_signal_end)/2)
        else:
            self._bifi_reset_countdown -= 1
            current_bifi_position = self._device.query('MOTORBIREFRINGENT:POSITION')
            bifi_first_position = int(current_bifi_position - self._go_to_position_config.birefringent_scan_range/2)
            self._last_bifi_position = int(current_bifi_position + self._go_to_position_config.birefringent_scan_range/2) 
            self.log.info(f"Sending BiFi to first scan position {bifi_first_position}.")
            self._device.set('MOTORBIREFRINGENT:POSITION', bifi_first_position)
            self._bifi_scan = []
            self.watchdog_event("next")
    @state
    @transition_to(("next", "choose_bifi_position"))
    def bifi_scan(self):
        "Perform BiFi scan and save wavelength and diode power for each scanned position."
        status = self._device.query('MOTORBIREFRINGENT:STATUS')
        status_ready = status & 0xff == 0x02
        current_bifi_position = self._device.query('MOTORBIREFRINGENT:POSITION')
        if not status_ready:
            return
        elif current_bifi_position >= self._last_bifi_position:
            self.watchdog_event("next")
        else:
            self._bifi_scan.append([current_bifi_position, self._current_frequency, self._device.query('DIODEPOWER:DCVALUE')])
            self._device.set('MOTORBIREFRINGENT:RELATIVE', self._go_to_position_config.birefringent_scan_increment)
    @state
    @transition_to(("next", "prepare_te_scan"))
    @transition_to(("redo", "prepare_bifi_scan"))
    def choose_bifi_position(self):
        "Choose the best BiFi position based on the latest BiFi scan."
        if self._relax_timer < 0:
            self._bifi_pos_array = np.array(self._bifi_scan)
            position_index = np.argmin(np.absolute(self._bifi_pos_array[:, 1] - self._go_to_target))
            position = self._bifi_pos_array[position_index, 0]
            frequency = self._bifi_pos_array[position_index, 1]
            prev_position_index = max(0, position_index - 1)
            prev_position = self._bifi_pos_array[prev_position_index, 0]
            prev_frequency = self._bifi_pos_array[position_index, 1]
            next_position_index = min(len(self._bifi_scan)-1, position_index + 1)
            next_position = self._bifi_pos_array[next_position_index, 0]
            next_frequency = self._bifi_pos_array[position_index, 1]
            best_position = self._scale_position(prev_position, position, next_position, prev_frequency, frequency, next_frequency)
            self.log.info(f"Setting BiFi position to {best_position}")
            self._device.set('MOTORBIREFRINGENT:POSITION', best_position)
            self._relax_timer = time.perf_counter()
        elif time.perf_counter() - self._relax_timer < 0.5:
            pass
        else:
            self._relax_timer = -1
            delta = self._current_frequency - self._go_to_target
            self.log.info(f"Delta is now: {delta*1e-9} GHz.")
            if np.abs(delta) < self._go_to_position_config.thin_etalon_free_spectral_range*1e9:
                self.watchdog_event("next")
            else:
                self.log.info("Bifi setting failed redoing this step.")
                self._reoptimize_bifi = True
                self.watchdog_event("redo")
    @state
    @transition_to(("next", "te_scan"))
    @transition_to(("skip_step", "prepare_pzetl_scan"))
    def prepare_te_scan(self):
        """
        Prepare the positions scanned by the thin etalon filter. Move the thin etalon to the first
        position thin_etalon_signal_start.
        """
        if self._relax_timer > 0 and time.perf_counter() - self._relax_timer<0.5:
            return
        self._relax_timer = -1
        delta = self._current_frequency - self._go_to_target
        if np.abs(delta) < self._go_to_position_config.pzetl_free_spectral_range*1e9/2:
            self.watchdog_event("skip_step")
            return
        status = self._device.query('MOTORTHINETALON:STATUS')
        status_ready = status & 0xff == 0x02
        self.log.debug(f"Thin etalon status {status}")
        if not status_ready:
            self._device.set("MOTORTHINETALON:CLEAR", "")
            self._relax_timer = time.perf_counter()
        else:  
            self._te_previous_error = np.inf
            if self._current_frequency < self._go_to_target:
                self._te_scan_direction = -1
                self.log.info("Scanning TE down.")
            else:
                self._te_scan_direction = 1
                self.log.info("Scanning TE up.")
            self._te_scan = []
            self.watchdog_event("next")
    @state
    @transition_to(("next", "choose_te_position"))
    def te_scan(self):
        """
        Perform thin etalon scan and save wavelength and thin etalon DC power for each scanned 
        position. The scan spans until thin_etalon_signal_end with steps thin_etalon_scan_increment.
        """
        if self._relax_timer > 0 and time.perf_counter() - self._relax_timer<0.5:
            return
        status = self._device.query('MOTORTHINETALON:STATUS')
        status_ready = status & 0xff == 0x02
        current_te_position = self._device.query('MOTORTHINETALON:POSITION')
        error = np.abs(self._current_frequency - self._go_to_target)
        if not status_ready:
            self._device.set("MOTORTHINETALON:CLEAR", "")
            self._relax_timer = time.perf_counter()
            return
        self._relax_timer = -1
        self._te_scan.append([current_te_position, self._current_frequency, self._device.query('THINETALON:DCVALUE')])
        if self._te_scan_direction > 0 and current_te_position >= self._go_to_position_config.thin_etalon_signal_end:
            self.log.debug("Stopping scan because upper end has been reached.")
            self.watchdog_event("next")
        elif self._te_scan_direction < 0 and current_te_position <= self._go_to_position_config.thin_etalon_signal_start:
            self.log.debug("Stopping scan because lower end has been reached.")
            self.watchdog_event("next")
        else:
            self._te_previous_error = error
            self._device.set('MOTORTHINETALON:RELATIVE', self._te_scan_direction * self._go_to_position_config.thin_etalon_scan_increment)
    @state
    @transition_to(("next", "prepare_pzetl_scan"))
    @transition_to(("reoptimize_bifi", "prepare_bifi_scan"))
    @transition_to(("redo", "prepare_te_scan"))
    def choose_te_position(self):
        """
        Choose the best thin etalon position based on the latest thin etalon scan.
        Will reset BiFi if the optimized mode is more distant than thin_etalon_free_spectral_range. 
        """
        if self._relax_timer < 0:
            self._te_pos_array = np.array(self._te_scan)
            position_index = np.argmin(np.absolute(self._te_pos_array[:, 1] - self._go_to_target))
            position = self._te_pos_array[position_index, 0]
            frequency = self._te_pos_array[position_index, 1]
            prev_position_index = max(0, position_index-1)
            prev_position = self._te_pos_array[prev_position_index, 0]
            prev_frequency = self._te_pos_array[prev_position_index, 1]
            next_position_index = min(len(self._te_scan)-1, position_index+1)
            next_position = self._te_pos_array[next_position_index, 0]
            next_frequency = self._te_pos_array[next_position_index, 1]
            best_position = self._scale_position(prev_position, position, next_position, prev_frequency, frequency, next_frequency)
            self.log.info(f"Setting TE position to {best_position}.")
            self._device.set('MOTORTHINETALON:POSITION', best_position)
            self._relax_timer = time.perf_counter()
        elif time.perf_counter() - self._relax_timer > 0.5:
            self._relax_timer = -1
            delta = np.abs(self._current_frequency - self._go_to_target)
            if np.abs(delta) < self._go_to_position_config.pzetl_free_spectral_range*1e9: 
                self.log.info(f"thin etalon step successful (delta: {delta*1e-9} GHz)")
                self.watchdog_event("next")
            elif np.abs(delta) < self._go_to_position_config.thin_etalon_free_spectral_range*1e9: 
                self.log.info(f"Redoing TE scan (delta: {delta*1e-9} GHz)")
                self._device.set('MOTORTHINETALON:POSITION', self._go_to_position_config.thin_etalon_signal_start)
                self.watchdog_event("redo")
            else:
                self.log.info(f"Resetting BiFi (delta: {delta*1e-9} GHz)")
                self._reoptimize_bifi = True
                self.watchdog_event("reoptimize_bifi")
    @state
    @transition_to(("next", "pzetl_scan"))
    @transition_to(("skip_step", "finalize_procedure"))
    def prepare_pzetl_scan(self):
        """
        Prepare to scan the piezo etalon. The routine will scan around the current position
        and span pzetl_full_scan_range with a scan step of pzetl_scan_increment. The scan stops
        as soon as we cross the target frequency. Wait pzetl_relaxation before starting scan.
        """
        if self._relax_timer < 0:
            delta = np.abs(self._current_frequency - self._go_to_target)
            if delta < self._go_to_position_config.precision*1e9:
                self.watchdog_event("skip_step")
                return
            pzetl_current_position = self._device.query('PIEZOETALON:BASELINE')
            pzetl_first_position = pzetl_current_position - self._go_to_position_config.pzetl_full_scan_range/2
            self._last_pzetl_position = pzetl_current_position + self._go_to_position_config.pzetl_full_scan_range/2
            self.log.info(f"Setting PZETL to position {pzetl_first_position}.")
            self._device.set('PIEZOETALON:BASELINE', pzetl_first_position)
            self._relax_timer = time.perf_counter()
        elif time.perf_counter() - self._relax_timer >= self._go_to_position_config.pzetl_relaxation/1000:
            self._relax_timer = -1
            self._pzetl_previous_error = np.inf
            self.watchdog_event("next")
    @state
    @transition_to(("next", "prepare_te_setpoint"))
    def pzetl_scan(self):
        "Perform piezo etalon scan as parametrized before."
        pzetl_current_position = self._device.query('PIEZOETALON:BASELINE')
        delta = self._current_frequency - self._go_to_target
        if pzetl_current_position > self._last_pzetl_position:
            self.log.warn(f"Scanned the full piezo etalon without finding an optimal position. Expect the unexpected. Delta: {delta*1e-9} GHz.")
            self.watchdog_event("next")
        elif np.abs(delta) > self._pzetl_previous_error and self._pzetl_previous_error < self._go_to_position_config.pzetl_maximal_difference:
            pzetl_next_position = pzetl_current_position - self._go_to_position_config.pzetl_scan_increment
            self._device.set('PIEZOETALON:BASELINE', pzetl_next_position)
            delta = self._current_frequency - self._go_to_target
            self.log.info(f"Found PZETL position at {pzetl_next_position}, Delta: {delta*1e-9} GHz.")
            self.watchdog_event("next")
        else:
            self._pzetl_previous_error = np.abs(delta)
            pzetl_next_position = pzetl_current_position + self._go_to_position_config.pzetl_scan_increment
            self._device.set('PIEZOETALON:BASELINE', pzetl_next_position)
    @state
    @transition_to(("next", "te_setpoint_scan"))
    def prepare_te_setpoint(self):
        """
        Prepare fine scan of the thin etalon to choose the setpoint. Will scan thin_etalon_set_point_scan_range
        around the current position with increment thin_etalon_set_point_scan_increment.
        """
        status = self._device.query('MOTORTHINETALON:STATUS')
        status_ready = status & 0xff == 0x02
        if not status_ready:
            pass
        else:
            te_current_position = self._device.query('MOTORTHINETALON:POSITION')
            te_first_position = int(te_current_position - self._go_to_position_config.thin_etalon_set_point_scan_range/2)
            self._last_te_position = int(te_current_position + self._go_to_position_config.thin_etalon_set_point_scan_range/2)
            self.log.info(f"Sending thin etalon to first scan position {te_first_position} for setpoint selection.")
            self._device.set('MOTORTHINETALON:POSITION', te_first_position)
            self._te_setpoint_scan = []
            self.watchdog_event("next")
    @state
    @transition_to(("next", "choose_te_setpoint"))
    def te_setpoint_scan(self):
        """
        Perform the thin etalon setpoint scan.
        """
        status = self._device.query('MOTORTHINETALON:STATUS')
        status_ready = status & 0xff == 0x02
        current_te_position = self._device.query('MOTORTHINETALON:POSITION')
        if not status_ready:
            return
        elif current_te_position >= self._last_te_position:
            self.watchdog_event("next")
        else:
            self._te_setpoint_scan.append([current_te_position, self._current_frequency, self._device.query('THINETALON:DCVALUE')])
            self._device.set('MOTORTHINETALON:RELATIVE', self._go_to_position_config.thin_etalon_set_point_scan_increment)
    @state
    @transition_to(("next", "finalize_procedure"))
    def choose_te_setpoint(self):
        """
        Choose the best thin etalon setpoint position based on the latest thin etalon scan. Will also
        compute the side of the valley of the mode on which we stand.
        """
        self._te_setpoint_pos_array =np.array(self._te_setpoint_scan)
        position_index = np.argmin(np.absolute(self._te_setpoint_pos_array[:, 1] - self._go_to_target))
        position = self._te_setpoint_pos_array[position_index, 0]
        frequency = self._te_setpoint_pos_array[position_index, 1]
        prev_position_index = max(0, position_index-1)
        prev_position = self._te_setpoint_pos_array[prev_position_index, 0]
        prev_frequency = self._te_setpoint_pos_array[prev_position_index, 1]
        next_position_index = min(len(self._te_setpoint_scan)-1, position_index+1)
        next_position = self._te_setpoint_pos_array[next_position_index, 0]
        next_frequency = self._te_setpoint_pos_array[next_position_index, 1]
        best_position = self._scale_position(prev_position, position, next_position, prev_frequency, frequency, next_frequency)
        if self._relax_timer < 0:
            self.log.info(f"Setting TE position to {best_position}.")
            self._device.set('MOTORTHINETALON:POSITION', best_position)
            self._relax_timer = time.perf_counter()
        elif time.perf_counter() - self._relax_timer < 0.5:
            return
        else:
            self._relax_timer = -1
            prev_position_power = self._te_setpoint_pos_array[prev_position_index, 2]
            next_position_power = self._te_setpoint_pos_array[prev_position_index, 2]
            slope = next_position_power - prev_position_power
            te_proportional_gain = self._device.query('THINETALON:CONTROLPROPORTIONAL')
            te_integral_gain = self._device.query('THINETALON:CONTROLINTEGRAL')
            if slope < 0: # left side of the valley, need negative gains
                te_proportional_gain = - np.abs(te_proportional_gain)
                te_integral_gain = - np.abs(te_integral_gain)
                self.log.info("Choosing left flank of thin etalon.")
            else: # right side of the valley, need positive gains
                te_proportional_gain = np.abs(te_proportional_gain)
                te_integral_gain = np.abs(te_integral_gain)
                self.log.info("Choosing right flank of thin etalon.")
            te_proportional_gain = self._device.set('THINETALON:CONTROLPROPORTIONAL', te_proportional_gain)
            te_integral_gain = self._device.set('THINETALON:CONTROLINTEGRAL', te_integral_gain)
            delta = np.abs(self._current_frequency - self._go_to_target)
            self.log.info(f"Thin etalon setpoint scan over (delta: {delta*1e-9} GHz).")
            self.watchdog_event("next")
    @state
    @transition_to(("next", "idle"))
    @transition_to(("reoptimize_te", "prepare_te_setpoint"))
    def finalize_procedure(self):
        """
        Reset the piezo etalon and the slow piezo and triggers a thin etalon setpoint reoptimization if
        required. Then finalize the procedure by re-enabling the control loops.
        """
        delta = np.abs(self._current_frequency - self._go_to_target)
        if self._relax_timer < 0:
            self.log.info("Resetting piezo etalon")
            self._device.set('PIEZOETALON:BASELINE', (self._go_to_position_config.pzetl_lower_point + self._go_to_position_config.pzetl_upper_point)/2)
            self.log.info("Resetting slow piezo")
            self._device.set("SLOWPIEZO:NOW", (self._go_to_position_config.spzt_lower_point + self._go_to_position_config.spzt_upper_point)/2)
            self._relax_timer = time.perf_counter()
        elif time.perf_counter() - self._relax_timer < self._go_to_position_config.pzetl_relaxation/1000:
            pass
        elif delta < self._go_to_position_config.precision*1e9 or self._reoptimize_te:
            setpoint_te = self._device.query("THINETALON:DCVALUE") / self._device.query("DIODEPOWER:DCVALUE")
            self._device.set("THINETALON:CONTROLSETPOINT", setpoint_te)
            self.log.info(f"Enabling thin etalon control loop.")
            self._device.set('THINETALON:CONTROLSTATUS', 'RUN')
            self.log.info(f"Enabling piezo etalon control loop.")
            self._device.set('PIEZOETALON:CONTROLSTATUS', 'RUN')
            delta = self._current_frequency - self._go_to_target
            self.log.info(f"Procedure finished. Delta: {delta*1e-9} GHz.")
            self.watchdog_event("next")
        else:
            self.log.info(f"Thin etalon setpoint re-optimization required. Delta: {delta*1e-9} GHz.")
            self._reoptimize_te = True
            self.watchdog_event("reoptimize_te")

