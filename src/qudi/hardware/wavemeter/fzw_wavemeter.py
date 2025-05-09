import serial
import queue
import struct
import time
from typing import Union, Optional, Tuple, Sequence

from PySide2 import QtCore

import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
# from qudi.interface.scanning_laser_interface import ScanningLaserInterface, ScanningState, ScanningLaserReturnError
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, SampleTiming, StreamingMode, ScalarConstraint
from qudi.interface.finite_sampling_input_interface import FiniteSamplingInputInterface, FiniteSamplingInputConstraints
from qudi.interface.process_control_interface import ProcessControlConstraints, ProcessControlInterface
from qudi.interface.switch_interface import SwitchInterface
from qudi.interface.pid_controller_interface import PIDControllerInterface
from qudi.util.mutex import Mutex
from qudi.util.overload import OverloadedAttribute

from qudi.hardware.laser.moglabs_helper import MOGLABSDeviceFinder

CRLF=b"\r\n"

class MOGLabsFZW(DataInStreamInterface, SwitchInterface, FiniteSamplingInputInterface, ProcessControlInterface, PIDControllerInterface):
    """A class to control our MOGLabs laser to perform excitation spectroscopy.
    Example config:
    """
    port = ConfigOption('port', 'COM6')
    poll_time = StatusVar('poll_time', 1e-3)
    default_buffer_size = ConfigOption("buffer_size", 2**16)
    auto_start_acquisition = ConfigOption("auto_start_acquisition", False)

    instream_rate = StatusVar("instream_rate", default=50)
    _threaded = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._constraints: Optional[DataInStreamConstraints] = None
        self._constraints_finite_sampling = None
        self._lock = Mutex()
        self._data_buffer: Optional[np.ndarray] = None
        self._timestamp_buffer: Optional[np.ndarray] = None
        self._instream_data_buffer = None
        self._instream_timestamp_buffer = None
        self._current_buffer_position = 0
        self.buffer_size = 0
        self.serial = serial.Serial()
        self._watchdog_active = False
        self._time_start = time.monotonic()
        self._time_last_read = time.monotonic() 
        self._instream_offset = 0
        self._frame_size = 1
        self.instream_running = False
        self._instream_consume_buffer = True
        self._instream_read_start = 0

    # Qudi activation / deactivation
    def on_activate(self):
        """Activate module.
        """
        self.buffer_size = self.default_buffer_size
        self._constraints = DataInStreamConstraints(
            channel_units = {
                'frequency': 'Hz',
            },
            sample_timing=SampleTiming.TIMESTAMP,
            streaming_modes = [StreamingMode.CONTINUOUS],
            data_type=np.float64,
            channel_buffer_size=ScalarConstraint(default=2**16,
                                                 bounds=(2, (2**32)//10),
                                                 increment=1,
                                                 enforce_int=True),
            sample_rate=ScalarConstraint(default=self.instream_rate,bounds=(1,100),increment=1),
        )
        self._constraints_finite_sampling = FiniteSamplingInputConstraints(
            channel_units = {
                'frequency': 'Hz',
            },
            frame_size_limits = (1, self.default_buffer_size),
            sample_rate_limits = (1, 100)
        )
        self._constraints_process_control = ProcessControlConstraints(
            ["tension"],
            ["frequency", "wavelength"],
            {"tension":"V", "frequency":"THz", "wavelength":"nm"},
            {"tension":(-2.5,2.5)},
            {"tension":float, "wavelength":float, "frequency":float},
        )
        device_finder = MOGLABSDeviceFinder()
        self.serial = device_finder.fzw
        self.serial.open()
        self._set_offset(0.0)
        if self.auto_start_acquisition :
            self._watchdog_active = True
            self._prepare_buffers()
            QtCore.QMetaObject.invokeMethod(self, '_continuous_read_callback', QtCore.Qt.QueuedConnection)
        else:
            self._watchdog_active = False

    def on_deactivate(self):
        """Deactivate module.
        """
        self.log.debug("Stopping continuous acquisition.")
        self._stop_continuous_read()
        time.sleep(self.poll_time/1000)
        self.log.debug("Closing serial port.")
        self.serial.close()

    # Internal communication facilities
    def send_and_recv(self, value, check_ok=True):
        if not value.endswith("\r\n"):
            value += "\r\n"
        self.serial.write(value.encode("utf8"))
        ret = self.serial.readline().decode('utf8')
        if check_ok and not ret.startswith("OK"):
            self.log.error(f"Command \"{value}\" errored: \"{ret}\"")
        return ret
    def _set_exposure(self, value="auto"):
        self.send_and_recv(f"cam,exp,{value}")
    def _get_exposure(self):
        ret = self.send_and_recv("cam,exp", check_ok=False)
        val = float(ret.split()[0])
        return val
    def _clear_hw_buffer(self):
        self.send_and_recv("meas,clear")
    def _get_trig(self):
        return self.send_and_recv("meas,extrig", check_ok=False)
    def _set_trig(self,value):
        return self.send_and_recv(f"meas,extrig,{value}")
    def _softrig(self):
        return self.send_and_recv(f"meas,softrig")
    def _get_pulse(self):
        return self.send_and_recv("meas,pulse", check_ok=False)
    def _set_pulse(self, st):
        return self.send_and_recv(f"meas,pulse,{st}")
    def _get_pid_value(self):
        return float(self.send_and_recv("pid,value", check_ok=False).split()[0])
    def _set_pid_value(self, v):
        return self.send_and_recv(f"pid,write,{v}")
    def _set_offset(self,v):
        return self.send_and_recv(f"pid,offset,{v}")
    def _is_pid_disabled(self):
        return "DISABLED" in self.send_and_recv("pid,status", check_ok=False)        
    # Internal management of buffers
    def _prepare_buffers(self):
        self._data_buffer = np.empty(self.buffer_size, dtype=np.float64)
        self._timestamp_buffer = np.empty(self.buffer_size, dtype=np.float64)
        self._instream_data_buffer = np.empty(self.buffer_size, dtype=np.float64)
        self._instream_timestamp_buffer = np.empty(self.buffer_size, dtype=np.float64)
        self._current_buffer_position = 0
    @QtCore.Slot()
    def _start_continuous_read(self):
        self._watchdog_active = True
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self, '_continuous_read_callback', QtCore.Qt.BlockingQueuedConnection)
        else:
            self._time_start = time.monotonic()
            self._time_last_read = self._time_start
            self._continuous_read_callback()
    @QtCore.Slot()
    def _stop_continuous_read(self):
        self._watchdog_active = False
    @QtCore.Slot()
    def _continuous_read_callback(self):
        time_start = time.perf_counter()
        try:
            with self._lock:
                t = time.monotonic()
                delta_t = t - self._time_last_read
                if delta_t < self.poll_time:
                    postpone = int(round(1000*max(0, self.poll_time-delta_t)))
                    QtCore.QTimer.singleShot(postpone, self._continuous_read_callback)
                    return
                self._time_last_read = t
                try:
                    val = float(self.send_and_recv("meas,freq", check_ok=False).split()[0])
                except ValueError:
                    val = np.nan
                if self._current_buffer_position < len(self._data_buffer):
                    i = self._current_buffer_position
                    self._data_buffer[i] = val * 1e12
                    self._timestamp_buffer[i] = t - self._time_start
                    if i > 0 and self._timestamp_buffer[i-1] > self._timestamp_buffer[i]:
                        self._timestamp_buffer[i] = self._timestamp_buffer[i-1]
                    self._current_buffer_position += 1
                else:
                    pass
        except:
            self.log.exception("")
        if self._watchdog_active:
            overhead_time = time.perf_counter() - time_start
            QtCore.QTimer.singleShot(int(round(1000*max(0, self.poll_time - overhead_time))), self._continuous_read_callback)
    def _roll_buffers(self, n_read):
        self._data_buffer = np.roll(self._data_buffer, -n_read)
        self._timestamp_buffer = np.roll(self._timestamp_buffer, -n_read)
        self._current_buffer_position -= n_read
        self._instream_read_start = max(0, self._instream_read_start-n_read)

    @QtCore.Slot()
    def _instream_buffers_callback(self):
        # self.log.debug("instream callback")
        t_start = time.perf_counter()
        try:
            with self._lock:
                if self._instream_offset < len(self._instream_data_buffer):
                    view = self._data_buffer[self._instream_read_start:self._current_buffer_position]
                    roi = ~np.isnan(view)
                    view = view[roi]
                    view_time = self._timestamp_buffer[self._instream_read_start:self._current_buffer_position]
                    self._instream_timestamp_buffer[self._instream_offset] = view_time.mean() + self._time_start - self._time_start_instream
                    if len(view) > 0:
                        self._instream_data_buffer[self._instream_offset] = view.mean()
                    else:
                        self._instream_data_buffer[self._instream_offset] = np.nan
                    if self._instream_consume_buffer:
                        self._roll_buffers(self._current_buffer_position)
                        self._instream_read_start = 0
                    else:
                        self._instream_read_start = self._current_buffer_position
                    self._instream_offset += 1
            if self.module_state() == 'locked':
                t_overhead = time.perf_counter() - t_start
                QtCore.QTimer.singleShot(int(round(1000 * max(0, 1/self.instream_rate - t_overhead))), self._instream_buffers_callback)
        except:
            self.log.exception("")

    def _roll_instream_buffers(self, n_read):
        self._instream_data_buffer = np.roll(self._instream_data_buffer, -n_read)
        self._instream_timestamp_buffer = np.roll(self._instream_timestamp_buffer, -n_read)
        self._instream_offset -= n_read

    # DataInStreamInterface
    constraints = OverloadedAttribute()
    @constraints.overload("DataInStreamInterface")
    @property
    def constraints(self) -> DataInStreamConstraints:
        if self._constraints is None:
            raise ValueError("Constraints have not yet been initialized.")
        return self._constraints

    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        if self.module_state() == 'idle':
            self.module_state.lock()
            with self._lock:
                self._prepare_buffers()
            self._time_start_instream = time.monotonic()
            self._instream_offset = 0
            self.instream_running = True
            self._start_continuous_read()
            QtCore.QMetaObject.invokeMethod(self, '_instream_buffers_callback', QtCore.Qt.QueuedConnection)
        else:
            self.log.warning('Unable to start input stream. It is already running.')

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        self.log.debug("Requested stop.")
        if self.module_state() == 'locked':
            self._stop_continuous_read()
            self.log.debug("unlocking")
            self.module_state.unlock()
            self.instream_running = False
        else:
            self.log.warning('Unable to stop wavemeter input stream as nothing is running.')

    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        """ Read data from the stream buffer into a 1D numpy array given as parameter.
        Samples of all channels are stored interleaved in contiguous memory.
        In case of a multidimensional buffer array, this buffer will be flattened before written
        into.
        The 1D data_buffer can be unraveled into channel and sample indexing with:
            data_buffer.reshape([<samples_per_channel>, <channel_count>])
        The data_buffer array must have the same data type as self.constraints.data_type.
        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        able to hold at least <samples_per_channel> items:
        This function is blocking until the required number of samples has been acquired.
        """
        if self.module_state() != 'locked':
            raise RuntimeError('Unable to read data. Stream is not running.')
        while self.available_samples < samples_per_channel:
            time.sleep(1/self.instream_rate)
        with self._lock:
            n_read = min(self._instream_offset, samples_per_channel)
            data_buffer[:n_read] = self._instream_data_buffer[:n_read]
            if timestamp_buffer is not None:
                timestamp_buffer[:n_read] = self._instream_timestamp_buffer[:n_read]
            self._roll_instream_buffers(n_read)

    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        """ Read data from the stream buffer into a 1D numpy array given as parameter.
        All samples for each channel are stored in consecutive blocks one after the other.
        The number of samples read per channel is returned and can be used to slice out valid data
        from the buffer arrays like:
            valid_data = data_buffer[:<channel_count> * <return_value>]
            valid_timestamps = timestamp_buffer[:<return_value>]
        See "read_data_into_buffer" documentation for more details.
        This method will read all currently available samples into buffer. If number of available
        samples exceeds buffer size, read only as many samples as fit into the buffer.
        """
        if self.module_state() != 'locked':
            raise RuntimeError('Unable to read data. Stream is not running.')
        with self._lock:
            if timestamp_buffer is None:
                raise RuntimeError(
                    'SampleTiming.TIMESTAMP mode requires a timestamp buffer array'
                )
            n_read = min(self._instream_offset, len(data_buffer))
            data_buffer[:n_read] = self._instream_data_buffer[:n_read]
            timestamp_buffer[:n_read] = self._instream_timestamp_buffer[:n_read] / 1000
            self._roll_instream_buffers(n_read)
            return n_read

    def read_data(self,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """ Read data from the stream buffer into a 1D numpy array and return it.
        All samples for each channel are stored in consecutive blocks one after the other.
        The returned data_buffer can be unraveled into channel samples with:
            data_buffer.reshape([<samples_per_channel>, <channel_count>])
        The numpy array data type is the one defined in self.constraints.data_type.
        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.
np.concatenate(np.array([self._time_offset]), wrapped)
TypeError: only integer scalar arrays can be converted to a scalar index
        If samples_per_channel is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        """
        if self.module_state() != 'locked':
            raise RuntimeError('Unable to read data. Stream is not running.')
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        while self.available_samples < samples_per_channel:
            time.sleep(1/self.instream_rate)
        with self._lock:
            n_read = min(self._instream_offset, samples_per_channel)
            data_buffer = np.empty(n_read,dtype=np.float64)
            timestamp_buffer = np.empty(n_read,dtype=np.float64)
            data_buffer[:n_read] = self._instream_data_buffer[:n_read]
            timestamp_buffer[:n_read] = self._instream_timestamp_buffer[:n_read] / 1000
            self._roll_instream_buffers(n_read)
            return (data_buffer, timestamp_buffer)

    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """ This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.
        In case of SampleTiming.TIMESTAMP a single numpy.float64 timestamp value will be returned
        as well.
        """
        if self.module_state() != 'locked':
            raise RuntimeError('Unable to read data. Stream is not running.')
        with self._lock:
            self._softrig()
            f = self.send_and_recv("meas,freq", check_ok=False)
            if f is None:
                return np.empty(0),None
            try:
                return np.array(float(f)),None 
            except ValueError:
                return np.empty(0), None

    @property
    def available_samples(self) -> int:
        with self._lock:
            return self._instream_offset

    @property
    def sample_rate(self) -> float:
        return self.instream_rate

    @property
    def channel_buffer_size(self) -> int:
        return self.buffer_size

    @property 
    def streaming_mode(self) -> StreamingMode:
        return StreamingMode.CONTINUOUS

    @property
    def active_channels(self):
        return ['frequency']

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        with self._lock:
            self.buffer_size = channel_buffer_size
            self._prepare_buffers()
            self.instream_rate = sample_rate

    # SwitchInterface
    @property
    def name(self):
        return "FZW"
    @property
    def available_states(self):
        return {
                "MEAS,EXTRIG":("OFF", "ON"),
                "MEAS,PULSE":("OFF", "ON"),
                "PID,ENABLED": ("OFF", "ON"),
        }
    def get_state(self, switch):
        with self._lock:
            if switch == "MEAS,EXTRIG":
                st = self._get_trig()
            elif switch == "MEAS,PULSE":
                st = self._get_pulse()
            else:
                enabled = not self._is_pid_disabled()
                st = "ON" if enabled else "OFF"
            if "ON" in st:
                return "ON"
            else:
                return "OFF"
    def set_state(self, switch, state):
        with self._lock:
            if switch == "MEAS,EXTRIG":
                self._set_trig(state)
            elif switch == "MEAS,PULSE":
                self._set_pulse(state)
            else:
                self.set_enabled(state == "ON")

    # FiniteSamplingInputInterface
    @constraints.overload("FiniteSamplingInputInterface")
    @property
    def constraints(self):
        return self._constraints_finite_sampling

    @property 
    def frame_size(self):
        return self._frame_size

    @property
    def samples_in_buffer(self):
        return self._current_buffer_position

    def set_active_channels(self, _):
        pass 

    def set_sample_rate(self, rate):
        self.poll_time = 1/rate

    def set_frame_size(self, size):
        self._frame_size = size
        self.buffer_size = size

    def start_buffered_acquisition(self):
        self._instream_consume_buffer = False
        if self.module_state() == "idle":
            self.module_state.lock()
        with self._lock:
            self._prepare_buffers()
        self._start_continuous_read()

    def stop_buffered_acquisition(self):
        if self.module_state() == 'locked':
            if not self.instream_running:
                self._stop_continuous_read()
                self.module_state.unlock()
                self.log.debug("unlocked")
            with self._lock:
                self._instream_consume_buffer = True

    def get_buffered_samples(self, number_of_samples: Optional[int] =None):
        if number_of_samples is None:
            number_of_samples = self.samples_in_buffer
        if number_of_samples > len(self._data_buffer):
            raise ValueError(f"You are asking for too many samples ({number_of_samples} for a maximum of {self.frame_size}.")
        while self.samples_in_buffer < number_of_samples:
            time.sleep(0.1)
        data_buffer = np.empty(number_of_samples,dtype=np.float64)
        timestamp_buffer = np.empty(number_of_samples,dtype=np.float64)
        with self._lock:
            data_buffer[:number_of_samples] = self._data_buffer[:number_of_samples]
            timestamp_buffer[:number_of_samples] = self._timestamp_buffer[:number_of_samples]
            self._roll_buffers(number_of_samples)
        return {"frequency": data_buffer, "timestamp": timestamp_buffer}

    def acquire_frame(self, frame_size=None):
        old_frame_size = self.frame_size
        if frame_size is not None:
            self.set_frame_size(frame_size)
        self.start_buffered_acquisition()
        data = self.get_buffered_samples(self.frame_size)
        self.stop_buffered_acquisition()
        if frame_size is not None:
            self.set_frame_size(old_frame_size)
        return data

    # ProcessControlInterface
    set_setpoint = OverloadedAttribute()
    @set_setpoint.overload("ProcessControlInterface")
    def set_setpoint(self, channel, value):
        with self._lock:
            return self._set_pid_value(value)

    get_setpoint = OverloadedAttribute()
    @get_setpoint.overload("ProcessControlInterface")
    def get_setpoint(self, channel):
        with self._lock:
            return self._get_pid_value()

    get_process_value = OverloadedAttribute()
    @get_process_value.overload("ProcessControlInterface")
    def get_process_value(self, channel):
        with self._lock:
            self._softrig()
            if channel=="frequency":
                f = self.send_and_recv("meas,freq", check_ok=False)
            else:
                f = self.send_and_recv("meas,wl,nm(vac)", check_ok=False)
        try:
            return float(f.split()[0])
        except ValueError:
            return np.nan

    def set_activity_state(self, channel, active):
        """ Set activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        pass

    def get_activity_state(self, channel):
        """ Get activity state for given channel.
        State is bool type and refers to active (True) and inactive (False).
        """
        return True

    @constraints.overload("ProcessControlInterface")
    @property
    def constraints(self):
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return self._constraints_process_control

    # PIDControllerInterface
    def get_kp(self):
        with self._lock:
            return float(self.send_and_recv("pid,kp", check_ok=False))
    def set_kp(self, kp):
        with self._lock:
            self.send_and_recv(f"pid,kp,{kp}")
    def get_ki(self):
        with self._lock:
            return float(self.send_and_recv("pid,ki", check_ok=False))
    def set_ki(self, ki):
        with self._lock:
            self.send_and_recv(f"pid,ki,{ki}")
    def get_kd(self):
        with self._lock:
            return float(self.send_and_recv("pid,kd", check_ok=False))
    def set_kd(self, kd):
        with self._lock:
            self.send_and_recv(f"pid,kd,{kd}")
    @get_setpoint.overload("PIDControllerInterface")
    def get_setpoint(self):
        with self._lock:
            return float(self.send_and_recv(f"pid,setpoint", check_ok=False).split()[0])
    @set_setpoint.overload("PIDControllerInterface")
    def set_setpoint(self, setpoint):
        with self._lock:
            self.send_and_recv(f"pid,setpoint,{setpoint}")
    def get_manual_value(self):
        with self._lock:
            return self._get_pid_value()
    def set_manual_value(self, manual_value):
        with self._lock:
            return self._set_pid_value(manual_value)
    def get_enabled(self):
        with self._lock:
            ret = not self._is_pid_disabled()
            self.log.debug(f"enabled={ret}")
            return ret
    def set_enabled(self, enabled):
        with self._lock:
            currently_disabled = self._is_pid_disabled()
            if enabled and currently_disabled:
                self.send_and_recv("pid,enable,on")
            elif not enabled and not currently_disabled:
                self.send_and_recv("pid,disable")
    def get_control_limits(self):
        return (-2.5, 2.5)
    def set_control_limits(self, limits):
        pass
    @get_process_value.overload("PIDControllerInterface")
    def get_process_value(self):
        with self._lock:
            self._softrig()
            f = self.send_and_recv("meas,freq", check_ok=False)
        try:
            return np.float64(f.split()[0])
        except ValueError:
            return np.nan
    def process_value_unit(self) -> str:
        return "THz"
    def get_control_value(self):
        with self._lock:
            return self._get_pid_value()
    def control_value_unit(self) -> str:
        return "V"
    def get_extra(self):
        return {
            "P":self.get_kp(),
            "I":self.get_ki(),
            "D":self.get_kd(),
        }

