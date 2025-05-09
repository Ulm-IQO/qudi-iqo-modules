# -*- coding: utf-8 -*-
"""
This module controls the MOGLabs laser.
Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution.
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

import socket
import queue
import struct
import time
from typing import Union, Optional, Tuple, Sequence

from PySide2 import QtCore
from PySide2.QtGui import QGuiApplication

import serial
import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
# from qudi.interface.scanning_laser_interface import ScanningLaserInterface, ScanningState, ScanningLaserReturnError
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, SampleTiming, StreamingMode, ScalarConstraint
from qudi.interface.process_control_interface import ProcessControlConstraints, ProcessControlInterface
from qudi.interface.autoscan_interface import AutoScanInterface, AutoScanConstraints
from qudi.interface.switch_interface import SwitchInterface
from qudi.interface.finite_sampling_input_interface import FiniteSamplingInputInterface, FiniteSamplingInputConstraints
from qudi.interface.scanning_probe_interface import ScanningProbeInterface, ScanData, ScannerChannel, ScannerAxis, ScanConstraints
from qudi.util.mutex import Mutex
from qudi.core.connector import Connector
from qudi.util.overload import OverloadedAttribute
from qudi.util.helpers import in_range
from qudi.util.enums import SamplingOutputMode

from qudi.hardware.laser.moglabs_helper import MOGLABSDeviceFinder

class MOGLABSMotorizedLaserDriver(SwitchInterface, ProcessControlInterface, DataInStreamInterface):
    """
    Control the Laser diode driver directly.
    """
    port = ConfigOption("port")
    default_buffer_size = ConfigOption("buffer_size", 2**16)
    poll_time = StatusVar('poll_time', 0.01)

    _threaded = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.serial = serial.Serial()
        self._lock = Mutex()
        self._data_buffer: Optional[np.ndarray] = None
        self._timestamp_buffer: Optional[np.ndarray] = None
        self._current_buffer_position = 0
        self.buffer_size = 0
        self._watchdog_active = False
        self._time_start = time.monotonic()
        self._time_last_read = time.monotonic() 
        self._constraints: Optional[DataInStreamConstraints] = None

    # Qudi activation / deactivation
    def on_activate(self):
        """Activate module.
        """
        device_finder = MOGLABSDeviceFinder()
        self.serial = device_finder.ldd
        self._ramp_running = False
        self._ramp_halt = 0.0
        self.serial.open()
        self._set_ramp_status("OFF")
        self.buffer_size = self.default_buffer_size
        self._constraints = DataInStreamConstraints(
            channel_units = {
                'current': 'A',
            },
            sample_timing=SampleTiming.TIMESTAMP,
            streaming_modes = [StreamingMode.CONTINUOUS],
            data_type=np.float64,
            channel_buffer_size=ScalarConstraint(default=2**16,
                                                 bounds=(2, (2**32)//10),
                                                 increment=1,
                                                 enforce_int=True),
            sample_rate=ScalarConstraint(default=1/self.poll_time,bounds=(1,100),increment=1),
        )

    def on_deactivate(self):
        """Deactivate module.
        """
        self._stop_continuous_read()
        time.sleep(self.poll_time)
        self.serial.close()

    # Internal management of buffers
    def _prepare_buffers(self):
        self._data_buffer = np.empty(self.buffer_size, dtype=np.float64)
        self._timestamp_buffer = np.empty(self.buffer_size, dtype=np.float64)
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
                    val = self._get_current()
                except ValueError:
                    val = np.nan
                if self._current_buffer_position < len(self._data_buffer):
                    i = self._current_buffer_position
                    self._data_buffer[i] = val * 1e-3
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
            self._start_continuous_read()
        else:
            self.log.warning('Unable to start input stream. It is already running.')

    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        self.log.debug("Requested stop.")
        if self.module_state() == 'locked':
            self._stop_continuous_read()
            self.log.debug("unlocking")
            self.module_state.unlock()
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
            time.sleep(self.poll_time)
        with self._lock:
            n_read = min(self._current_buffer_position, samples_per_channel)
            data_buffer[:n_read] = self._data_buffer[:n_read]
            if timestamp_buffer is not None:
                timestamp_buffer[:n_read] = self._timestamp_buffer[:n_read]
            self._roll_buffers(n_read)

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
            n_read = min(self._current_buffer_position, len(data_buffer))
            data_buffer[:n_read] = self._data_buffer[:n_read]
            timestamp_buffer[:n_read] = self._timestamp_buffer[:n_read]
            self._roll_buffers(n_read)
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
            self.log.debug("Zzzzz")
            time.sleep(self.poll_time)
        self.log.debug("Awaiting lock")
        with self._lock:
            n_read = min(self._current_buffer_position, samples_per_channel)
            data_buffer = np.empty(n_read,dtype=np.float64)
            timestamp_buffer = np.empty(n_read,dtype=np.float64)
            data_buffer[:n_read] = self._data_buffer[:n_read]
            timestamp_buffer[:n_read] = self._timestamp_buffer[:n_read]
            self.log.debug("rolling")
            self._roll_buffers(n_read)
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
            f = self._get_current()
            if f is None:
                return np.empty(0),None
            try:
                return np.array(float(f)),None 
            except ValueError:
                return np.empty(0), None

    @property
    def available_samples(self) -> int:
        with self._lock:
            return self._current_buffer_position

    @property
    def sample_rate(self) -> float:
        return 1/self.poll_time

    @property
    def channel_buffer_size(self) -> int:
        return self.buffer_size

    @property 
    def streaming_mode(self) -> StreamingMode:
        return StreamingMode.CONTINUOUS

    @property
    def active_channels(self):
        return ['current']

    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        with self._lock:
            self.buffer_size = channel_buffer_size
            self._prepare_buffers()
            self.poll_time = 1/sample_rate

    # SwitchInterface
    @property
    def name(self):
        return "LDD"
    @property
    def available_states(self):
        return {
                "HV,MOD":("EXT", "RAMP"),
                "Temp. control":("OFF", "ON"),
                "Curr. control":("OFF", "ON"),
                "RAMP":("OFF", "ON"),
                "CURRENT,MOD":("OFF", "+RAMP"),
        }
    def get_state(self, switch):
        with self._lock:
            if switch == "HV,MOD":
                return self._mod_status()
            elif switch == "Temp. control":
                return self._temp_status()
            elif switch == "RAMP":
                return self._ramp_status()
            elif switch == "CURRENT,MOD":
                return self._get_current_mod()
            else:
                return self._current_status()

    def set_state(self, switch, state):
        with self._lock:
            if switch == "HV,MOD":
                self._set_mod_status(state)
            elif switch == "Temp. control":
                self._set_temp_status(state)
            elif switch == "RAMP":
                self._set_ramp_status(state)
            elif switch == "CURRENT,MOD":
                return self._set_current_mod(state)
            else:
                self._set_current_status(state)

    # ProcessControlInterface
    def set_setpoint(self, channel, value):
        with self._lock:
            if channel == "frequency":
                return self._set_freq(value)
            elif channel == "span":
                return self._set_span(value)
            elif channel == "offset":
                return self._set_offset(value)
            elif channel == "bias":
                return self._set_bias(value)
            elif channel == "duty":
                return self._set_duty(value)
            elif channel == "ramp_halt": 
                self._ramp_halt=value
            elif channel == "current":
                self._set_current(value)

    def get_setpoint(self, channel):
        with self._lock:
            if channel == "frequency":
                return self._get_freq()
            elif channel == "span":
                return self._get_span()
            elif channel == "offset":
                return self._get_offset()
            elif channel == "bias":
                return self._get_bias()
            elif channel == "duty":
                return self._get_duty()
            elif channel == "ramp_halt": 
                return self._ramp_halt
            elif channel == "current":
                return self._get_current_setpoint()

    def get_process_value(self, channel):
        with self._lock:
            return self._get_current()

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
        with self._lock:
            return ProcessControlConstraints(
                ["frequency", "span", "offset", "bias", "duty", "ramp_halt", "current"],
                ["current"],
                {
                    "frequency":"Hz",
                    "span":"",
                    "offset":"",
                    "bias":"mA",
                    "duty":"",
                    "current":"mA",
                    "ramp_halt":"",
                },
                {
                    "frequency":(0.0, 50),
                    "span":(0.0,1.0),
                    "offset":(0.0,1.0),
                    "bias":(0.0,50.0),
                    "duty":(0.0,1.0),
                    "current":(0.0,self._get_current_lim()),
                    "ramp_halt":(0.0,1.0),
                },
                {
                    "frequency":float,
                    "span":float,
                    "offset":float,
                    "bias":float,
                    "duty":float,
                    "current":float,
                    "ramp_halt":float,
                },
            )

    # Internal communication facilities
    def send_and_recv(self, value, check_ok=True):
        if not value.endswith("\r\n"):
            value += "\r\n"
        self.serial.write(value.encode("utf8"))
        ret = self.serial.readline().decode('utf8')
        if check_ok and not ret.startswith("OK"):
            self.log.error(f"Command \"{value}\" errored: \"{ret}\"")
        return ret

    def _mod_status(self):
        return self.send_and_recv("hv,mod", check_ok=False).rstrip()

    def _set_mod_status(self, val):
        return self.send_and_recv(f"hv,mod,{val}")

    def _temp_status(self):
        return self.send_and_recv("TEC,ONOFF", check_ok=False).rstrip()

    def _set_temp_status(self, val):
        return self.send_and_recv(f"TEC,ONOFF,{val}")

    def _current_status(self):
        return self.send_and_recv("CURRENT,ONOFF", check_ok=False).rstrip()

    def _set_current_status(self, val):
        return self.send_and_recv(f"CURRENT,ONOFF,{val}")

    def _set_freq(self, val):
        return self.send_and_recv(f"RAMP,FREQ,{val}")
    def _get_freq(self):
        return float(self.send_and_recv(f"RAMP,FREQ", check_ok=False).split()[0])
    def _set_span(self, val):
        return self.send_and_recv(f"RAMP,SPAN,{val}")
    def _get_span(self):
        return float(self.send_and_recv(f"RAMP,SPAN", check_ok=False).split()[0])
    def _set_offset(self, val):
        return self.send_and_recv(f"RAMP,OFFSET,{val}")
    def _get_offset(self):
        return float(self.send_and_recv(f"RAMP,OFFSET", check_ok=False).split()[0])
    def _set_bias(self, val):
        return self.send_and_recv(f"RAMP,BIAS,{val}")
    def _get_bias(self):
        return float(self.send_and_recv(f"RAMP,BIAS", check_ok=False).split()[0])
    def _set_duty(self, val):
        return self.send_and_recv(f"RAMP,DUTY,{val}")
    def _get_duty(self):
        return float(self.send_and_recv(f"RAMP,DUTY", check_ok=False).split()[0])
    def _get_current_lim(self):
        return float(self.send_and_recv(f"current,ilim", check_ok=False).split()[0])
    def _get_current(self):
        return float(self.send_and_recv(f"current,meas", check_ok=False).split()[0])
    def _get_current_setpoint(self):
        return float(self.send_and_recv(f"current,iset", check_ok=False).split()[0])
    def _set_current(self,value):
        return self.send_and_recv(f"current,iset,{value}")
    def _set_ramp_status(self, st):
        if st == "OFF":
            self._ramp_running = False
            self.send_and_recv(f"ramp,halt,{self._ramp_halt}", check_ok=False)
        else:
            self._ramp_running = True
            self.send_and_recv(f"ramp,resume")
    def _ramp_status(self):
        if self._ramp_running:
            return "ON"
        else:
            return "OFF"
    def _get_current_mod(self):
        return self.send_and_recv("CURRENT,MOD", check_ok=False).rstrip()
    def _set_current_mod(self, value):
        return self.send_and_recv(f"CURRENT,MOD,{value}")

class MOGLABSCateyeLaser(ProcessControlInterface, AutoScanInterface):
    _scan_duration = StatusVar(name="scan_duration", default=1.0)
    __sigResetMotor = QtCore.Signal()
    _last_scan_pd = StatusVar(name="last_scan_pd", default=np.zeros(0, dtype=float))
    _last_scan_piezo = StatusVar(name="last_scan_piezo", default=np.zeros(0, dtype=int))
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.serial = serial.Serial()

    # Qudi activation / deactivation
    def on_activate(self):
        """Activate module.
        """
        device_finder = MOGLABSDeviceFinder()
        self.serial = device_finder.cem
        self.serial.open()
        self._lock = Mutex()     
        self.__sigResetMotor.connect(self.__reset_motor, QtCore.Qt.QueuedConnection)
        self._reset_motor()

    def on_deactivate(self):
        """Deactivate module.
        """
        self.serial.close()
    def set_setpoint(self, channel, value):
        with self._lock:
            if channel=="grating":
                self._set_motor_position(value)
            else:
                self._scan_duration=value

    def get_setpoint(self, channel):
        with self._lock:
            if channel=="grating":
                return self._get_motor_setpoint()
            else:
                return self._scan_duration

    def get_process_value(self, channel):
        with self._lock:
            if channel=="grating":
                return self._motor_position()
            elif channel=="photodiode":
                return self._get_pd()
            else:
                return self._get_piezo()

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

    constraints = OverloadedAttribute()
    @constraints.overload("ProcessControlInterface")
    @property
    def constraints(self):
        """ Read-Only property holding the constraints for this hardware module.
        See class ProcessControlConstraints for more details.
        """
        return ProcessControlConstraints(
            ["grating", "scan_duration"],
            ["grating", "photodiode", "piezo"],
            {"grating":"step", "photodiode":"V", "piezo":"V", "scan_duration":"s"},
            {"grating":self._motor_range(), "scan_duration":(0.1,20)},
            {"grating":int, "photodiode":float, "piezo":float, "scan_duration":float},
        )

    # AutoScanInterface
    @constraints.overload("AutoScanInterface")
    @property
    def constraints(self):
        return AutoScanConstraints(
            channels=["photodiode", "piezo"],
            units={"photodiode":"V", "piezo":""},
            limits={"photodiode":(0,5), "piezo":(0,2**16)},
            dtypes={"photodiode":float, "piezo":int}
        )
    def trigger_scan(self):
        with self._lock:
            vals = self._scan_pd()
            self._last_scan_pd = vals[0,:]*5.0/(2**12-1)
            self._last_scan_piezo = vals[1,:]

    def get_last_scan(self, channel):
        if channel == "photodiode":
            return self._last_scan_pd
        else:
            return self._last_scan_piezo

    # Internal communication facilities
    def send_and_recv(self, value, check_ok=True):
        if not value.endswith("\r\n"):
            value += "\r\n"
        self.serial.write(value.encode("utf8"))
        ret = self.serial.readline().decode('utf8')
        if check_ok and not ret.startswith("OK"):
            self.log.error(f"Command \"{value}\" errored: \"{ret}\"")
        return ret

    def _motor_range(self):
        mini,maxi = self.send_and_recv("motor,travel", check_ok=False).split(" ")
        return int(mini), int(maxi)

    def _motor_position(self):
        return int(self.send_and_recv("motor,position", check_ok=False))

    def _set_motor_position(self, value):
        return self.send_and_recv(f"motor,dest,{int(value)}")

    def _get_motor_setpoint(self):
        return int(self.send_and_recv(f"motor,dest", check_ok=False))

    def _move_motor_rel(self, value):
        return self.send_and_recv(f"motor,step,{int(value)}")

    def _motor_status(self):
        return self.send_and_recv("motor,status", check_ok=False).rstrip()

    def _reset_motor(self):
        self.__sigResetMotor.emit()

    def __reset_motor(self):
        with self._lock:
            old_setpoint = self._get_motor_setpoint()
            self.send_and_recv("motor,home")
            self.log.info("Please wait while the grating is being homed.")
            while self._motor_status() not in ("STABILISING", "ERR STATE"):
                time.sleep(0.01)
            if self._motor_status() == "ERR STATE":
                self.log.error("Motor is in error state! Consider restarting the CEM.")
            else:
                self.log.debug(f"Seting CEM setpoint to {old_setpoint}")
                self._set_motor_position(old_setpoint)
                while np.abs(self._motor_position() - old_setpoint) > 1:
                    time.sleep(0.01)
                self.log.info("Homing done.")

    def _get_pd(self):
        return float(self.send_and_recv("pd,read,0", check_ok=False).split()[0])

    def _get_piezo(self):
        return float(self.send_and_recv("pd,read,1", check_ok=False).split()[0])

    def _scan_pd(self,duration=None):
        if duration is None:
            duration = self._scan_duration
        self.send_and_recv("pd,rate,1")
        self.serial.read(self.serial.in_waiting)
        old_timeout = self.serial.timeout
        self.serial.timeout = duration * 1.5
        cmd = f"pd,scan,{duration}\r\n"
        self.serial.write(cmd.encode("utf8"))
        l = struct.unpack("<I", self.serial.read(4))[0]
        vals = np.empty(l//2, int)
        binary_data = self.serial.read(l)
        if len(binary_data) == 0:
            self.log.warning(f"I read 0")
            binary_data = self.serial.read(l)
        if len(binary_data) < l:
            self.log.warning(f"I did not read everything. read {len(binary_data)}/{l}.")
            binary_data += self.serial.read(l)
        self.log.debug(f"Read {len(binary_data)}/{l}")
        for i in range(0, l, 2):
            vals[i//2] = struct.unpack("<H", binary_data[i:i+2])[0]
        self.serial.timeout = old_timeout
        return np.reshape(vals, (2, len(vals)//2), 'F')

