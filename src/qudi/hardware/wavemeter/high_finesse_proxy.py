# -*- coding: utf-8 -*-

"""
This module acts as a proxy for the HighFinesse wavemeter. It directly communicates
with the hardware and can process callbacks from it. Being a module, there should only
ever be a single instance running in one qudi process.

Copyright (c) 2023, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

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

import time
from typing import Optional, List, Set, TYPE_CHECKING, Dict
from ctypes import byref, cast, c_double, c_int, c_char_p, c_long, POINTER, WINFUNCTYPE, WinDLL
from PySide2.QtCore import QObject
from qudi.core.threadmanager import ThreadManager
from qudi.core.logger import get_logger
from qudi.core.module import Base
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex

import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants
from qudi.hardware.wavemeter.high_finesse_wrapper import load_dll, setup_dll, MIN_VERSION
if TYPE_CHECKING:
    from qudi.hardware.wavemeter.high_finesse_wavemeter import HighFinesseWavemeter


THREAD_NAME_WATCHDOG = 'wavemeter_callback_error_watchdog'


class Watchdog(QObject):
    """A watchdog that can take care of errors in the callback function and checks for other changes."""
    def __init__(self, proxy: 'HighFinesseProxy', watch_interval: float):
        super().__init__()
        self._proxy = proxy
        self.log = get_logger(__name__)
        self._watch_interval = watch_interval
        self._stop = False

    def loop(self) -> None:
        while not self._stop:
            if self._proxy.error_in_callback:
                self.handle_error()
            if self._proxy.module_state() == 'locked':
                self.check_for_channel_activation_change()
            time.sleep(self._watch_interval)

    def stop_loop(self) -> None:
        self._stop = True

    def check_for_channel_activation_change(self) -> None:
        actual_active_channels = set(self._proxy.get_active_channels())
        if self._proxy.get_connected_channels() != actual_active_channels:
            self.log.warning('Channel was deactivated or activated through GUI.')
            self._proxy.stop_everything()

    def handle_error(self) -> None:
        self.log.warning('Error in callback function.')
        self._proxy.stop_everything()
        self._proxy.error_in_callback = False


class HighFinesseProxy(Base):
    """Proxy between a physical HighFinesse wavemeter and one or multiple HighFinesse wavemeter hardware modules.

    Example config for copy-paste:

    wavemeter_proxy:
        module.Class: 'wavemeter.high_finesse_proxy.HighFinesseProxy'
        options:
            watchdog_interval: 1.0  # how often the watchdog checks for errors/changes in s
    """

    _watchdog_interval: float = ConfigOption(name='watchdog_interval', default=1.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Mutex()

        self._wavemeter_dll: Optional[WinDLL] = None
        self._watchdog: Optional[Watchdog] = None
        self._thread_manager: ThreadManager = ThreadManager.instance()
        self._callback_function: Optional[callable] = None
        self.error_in_callback: bool = False
        self._wm_has_switch: bool = False

        self._connected_instream_modules: Dict['HighFinesseWavemeter', Set[int]] = {}

    def on_activate(self) -> None:
        if self._check_for_second_instance():
            raise RuntimeError('There is already a running proxy instance. '
                               'Did you configure more than a single instance of this proxy?')

        # load and prepare the wavemeter DLL
        try:
            self._wavemeter_dll = load_dll()
        except FileNotFoundError as e:
            raise ValueError('There is no wavemeter installed on this computer.\n'
                             'Please install a High Finesse wavemeter and try again.') from e
        else:
            v = [self._wavemeter_dll.GetWLMVersion(i) for i in range(4)]
            if v[0] == high_finesse_constants.GetFrequencyError.ErrWlmMissing.value:
                raise RuntimeError('The wavemeter application is not active. '
                                   'Start the wavemeter application before activating the qudi module.')

            self.log.info(f'Successfully loaded wavemeter DLL of WS{v[0]} {v[1]},'
                          f' software revision {v[2]}, compilation number {v[3]}.')

        software_rev = v[2]
        if software_rev < MIN_VERSION:
            self.log.warning(f'The wavemeter DLL software revision {software_rev} is older than the lowest revision '
                             f'tested to be working with the wrapper ({MIN_VERSION}). '
                             f'Setting up the wavemeter DLL might fail.')
        try:
            setup_dll(self._wavemeter_dll)
        except AttributeError:
            self.log.warning('One or more function is not available. The wavemeter version is likely outdated.')

        # try to activate the multi-channel switch and check if switch is present
        self._wavemeter_dll.SetSwitcherMode(True)
        is_active = self._wavemeter_dll.GetSwitcherMode(0)
        if is_active:
            self._wm_has_switch = True
        else:
            self._wm_has_switch = False

        self._set_up_watchdog()

    def on_deactivate(self) -> None:
        self._tear_down_watchdog()
        del self._wavemeter_dll

    def connect_instreamer(self, module: 'HighFinesseWavemeter', channels: List[int]):
        """
        Connect an instreamer module to the proxy.
        The proxy will start to put new samples into the instreamer buffer.
        """
        if module not in self._connected_instream_modules:
            with self._lock:
                # do channel activation in a lock to prevent the watchdog from stopping things
                not_connected_yet = set(channels) - self.get_connected_channels()
                for ch in not_connected_yet:
                    self._activate_channel(ch)
                self._connected_instream_modules[module] = set(channels)
            if self._callback_function is None:
                self._activate_only_connected_channels()
                self._start_measurement()
                self._start_callback()
        else:
            self.log.warning('Instream module is already connected.')

    def disconnect_instreamer(self, module: 'HighFinesseWavemeter'):
        """ Disconnect an instreamer module from the proxy. """
        if module in self._connected_instream_modules:
            channels_disconnecting_instreamer = self._connected_instream_modules[module]
            with self._lock:
                del self._connected_instream_modules[module]
                if not self._connected_instream_modules:
                    self._stop_callback()
                else:
                    # deactivate channels that are not connected by other instreamers
                    for ch in (channels_disconnecting_instreamer - self.get_connected_channels()):
                        self._deactivate_channel(ch)
        else:
            self.log.warning('Instream module is not connected and can therefore not be disconnected.')

    def sample_rate(self) -> float:
        """
        Estimate the current sample rate by the exposure times per channel and switching times.
        :return: sample rate in Hz
        """
        exposure_times = []
        active_channels = self.get_active_channels()
        for ch in active_channels:
            t = self._wavemeter_dll.GetExposureNum(ch, 1, 0)
            exposure_times.append(t)
        total_exposure_time = sum(exposure_times)

        switching_time = 12
        n_channels = len(active_channels)
        turnaround_time_ms = total_exposure_time + n_channels * switching_time

        return 1e3 / turnaround_time_ms

    def set_exposure_time(self, ch: int, exp_time: float) -> None:
        """ Set the exposure time for a specific switch channel. """
        err = self._wavemeter_dll.SetExposureNum(ch, 1, exp_time)
        if err:
            raise RuntimeError(f'Wavemeter error while setting exposure time of channel {ch}: '
                               f'{high_finesse_constants.ResultError(err)}')

    def get_active_channels(self) -> List[int]:
        """
        Get a list of all active channels on the multi-channel switch.
        :return: list of active channels
        """
        if not self._wm_has_switch:
            return [1]

        active_channels = []
        active = c_long()
        err = 0
        ch = 1
        while err != high_finesse_constants.ResultError.ChannelNotAvailable.value:
            err = self._wavemeter_dll.GetSwitcherSignalStates(ch, byref(active), byref(c_long()))
            if active:
                active_channels.append(ch)
            ch += 1
        return active_channels

    def get_connected_channels(self) -> Set[int]:
        """Channels on the multi-channel switch which are active on a connected instreamer."""
        channels = set()
        for i in self._connected_instream_modules.values():
            channels = channels | i
        return channels

    def stop_everything(self) -> None:
        """Meant to be called from watchdog."""
        self.log.warning('Stopping all streams.')
        streamers = list(self._connected_instream_modules).copy()
        self._stop_callback()
        self._connected_instream_modules = {}
        for streamer in streamers:
            # stop all streams without them triggering the proxy disconnect
            streamer.stop_stream_watchdog()

    # --- PID related methods ---
    
    def get_pid_setting(self, output_port: int, cmi_val: int):
        """
        Generic method to get PID values and settings
        @return: PID value or setting
        """
        i_val = c_long()
        d_val = c_double()
        err = self._wavemeter_dll.GetPIDSetting(cmi_val, output_port, byref(i_val), byref(d_val))
        if err == 1:
            return d_val.value, i_val.value
        else:
            raise RuntimeError(f'Error while getting PID value/setting: {high_finesse_constants.ResultError(err)}')

    def set_pid_setting(self, output_port: int, cmi_val: int, d_val: float = 0.0, i_val: int = 0):
        """ Generic method to set PID values and settings """
        i_val = c_long(i_val)
        d_val = c_double(d_val)
        err = self._wavemeter_dll.SetPIDSetting(cmi_val, output_port, i_val, d_val)
        if err:
            raise RuntimeError(f'Error while setting PID value/setting: {high_finesse_constants.ResultError(err)}')

    def get_setpoint(self, output_port: int) -> float:
        """
        Get the setpoint for a specific control voltage output port
        @return (float): The setpoint for this output port
        """
        pidc = c_char_p(b'0' * 1024)
        err = self._wavemeter_dll.GetPIDCourseNum(output_port, pidc)
        if err == high_finesse_constants.ResultError.NoErr.value:
            # wavemeter returns comma instead of point
            val = pidc.value.decode('utf-8').replace(',', '.')
            # wavemeter returns '= 123,456' when first turned on
            if val.startswith('= '):
                val = val[2:]
            try:
                val = float(val)
            except ValueError:
                raise ValueError('Could not convert PID course to a number.')
            return val
        else:
            raise RuntimeError(f'Error while getting setpoint: {high_finesse_constants.ResultError(err)}')

    def set_setpoint(self, output_port: int, setpoint: float):
        """ Set the setpoint for a specific output port """
        # wavemeter wants comma instead of point
        setpoint = str(setpoint).replace('.', ',').encode('utf-8')
        # convert setpoint to char array with 1024 bytes
        err = self._wavemeter_dll.SetPIDCourseNum(output_port, setpoint)
        if err:
            raise RuntimeError(f'Error while setting setpoint: {high_finesse_constants.ResultError(err)}')

    def set_manual_value(self, output_port: int, voltage: float) -> None:
        """ Set the control value to put out when PID is not running. """
        d_voltage = c_double(1e3 * voltage)  # wavemeter wants mV
        err = self._wavemeter_dll.SetDeviationSignalNum(output_port, d_voltage)
        if err:
            raise RuntimeError(f'Error while manual value: {high_finesse_constants.ResultError(err)}')

    def get_pid_enabled(self) -> bool:
        """
        Get the PID status
        @return (bool): True if PID is enabled, False otherwise
        """
        return self._wavemeter_dll.GetDeviationMode(False)
    
    def set_pid_enabled(self, enabled: bool):
        """ Set the PID status """
        err = self._wavemeter_dll.SetDeviationMode(enabled)
        if err:
            raise RuntimeError(f'Error while setting PID enabled: {high_finesse_constants.ResultError(err)}')

    def get_laser_control_setting(self, output_port: int, cmi_val: int):
        """ 
        Generic method to get laser control settings
        @return: laser control setting
        """
        pidc = c_char_p(b'0' * 1024)
        i_val = c_long()
        d_val = c_double()
        err = self._wavemeter_dll.GetLaserControlSetting(cmi_val, output_port, byref(i_val), byref(d_val), pidc)
        if err == 1:
            return d_val.value, i_val.value, pidc.value
        else:
            raise RuntimeError(f'Error while getting laser control setting: {high_finesse_constants.ResultError(err)}')
    
    def get_control_value(self, output_port: int):
        """
        Get the control value for a specific voltage output port
        @return (float): The control value in V for the output port
        """
        i_val = c_long(output_port)
        d_val = c_double()
        return 1e-3 * self._wavemeter_dll.GetDeviationSignalNum(i_val, d_val)

    def get_wavelength(self, channel: int) -> float:
        """
        Get the current wavelength for a specific input channel
        @return (float): wavelength in m
        """
        i_val = c_long(channel)
        d_val = c_double()
        res = self._wavemeter_dll.GetWavelengthNum(i_val, d_val)
        if res in [e.value for e in high_finesse_constants.GetFrequencyError]:
            raise RuntimeError(f'Error while getting process value: {high_finesse_constants.ResultError(res)}')
        else:
            return 1e-9 * res

    # --- protected methods ---

    def _check_for_second_instance(self) -> bool:
        """Check if there already is a proxy running."""
        return THREAD_NAME_WATCHDOG in self._thread_manager.thread_names

    def _activate_channel(self, ch: int) -> None:
        """ Activate a channel on the multi-channel switch. """
        if not self._wm_has_switch:
            if ch == 1:
                return
            else:
                raise RuntimeError(f'Cannot activate channel {ch}: wavemeter does not have a multi-channel switch.')

        err = self._wavemeter_dll.SetSwitcherSignalStates(ch, 1, 1)
        if err:
            raise RuntimeError(
                f'Wavemeter error while activating channel {ch}: {high_finesse_constants.ResultError(err)}'
            )

    def _deactivate_channel(self, ch: int) -> None:
        """ Deactivate a channel on the multi-channel switch. """
        if not self._wm_has_switch:
            if ch == 1:
                return
            else:
                raise RuntimeError(f'Cannot deactivate channel {ch}: wavemeter does not have a multi-channel switch.')

        err = self._wavemeter_dll.SetSwitcherSignalStates(ch, 0, 0)
        if err:
            raise RuntimeError(f'Wavemeter error while deactivating channel {ch}: '
                               f'{high_finesse_constants.ResultError(err)}')

    def _activate_only_connected_channels(self) -> None:
        """Activate all channels active on a connected instreamer and disable all others."""
        connected_channels = self.get_connected_channels()
        if not connected_channels:
            raise RuntimeError('Cannot deactivate all channels.')

        for ch in connected_channels:
            self._activate_channel(ch)
        for ch in self.get_active_channels():
            if ch not in connected_channels:
                self._deactivate_channel(ch)

    def _start_measurement(self) -> None:
        if self._wm_has_switch:
            self._wavemeter_dll.SetSwitcherMode(True)
        err = self._wavemeter_dll.Operation(high_finesse_constants.cCtrlStartMeasurement)
        if err:
            raise RuntimeError(f'Wavemeter error while starting measurement: {high_finesse_constants.ResultError(err)}')

    def _set_up_watchdog(self) -> None:
        self._watchdog_thread = self._thread_manager.get_new_thread(THREAD_NAME_WATCHDOG)
        self._watchdog = Watchdog(self, self._watchdog_interval)
        self._watchdog.moveToThread(self._watchdog_thread)
        self._watchdog_thread.started.connect(self._watchdog.loop)
        self._watchdog_thread.start()

    def _tear_down_watchdog(self) -> None:
        self._watchdog.stop_loop()
        self._thread_manager.quit_thread(self._watchdog_thread)
        del self._watchdog

    def _start_callback(self) -> None:
        """ Start the callback procedure. """
        self._callback_function = self._get_callback_function()
        self._wavemeter_dll.Instantiate(
            high_finesse_constants.cInstNotification,  # long ReasonForCall
            high_finesse_constants.cNotifyInstallCallbackEx,  # long Mode
            cast(self._callback_function, POINTER(c_long)),  # long P1: function
            0  # long P2: callback thread priority, 0 = standard
        )
        self.module_state.lock()
        self.log.debug('Started callback procedure.')

    def _stop_callback(self) -> None:
        """ Stop the callback procedure. """
        self._wavemeter_dll.Instantiate(
            high_finesse_constants.cInstNotification,  # long ReasonForCall
            high_finesse_constants.cNotifyRemoveCallback,  # long mode
            cast(self._callback_function, POINTER(c_long)),
            # long P1: function
            0)  # long P2: callback thread priority, 0 = standard
        self._callback_function = None
        self.module_state.unlock()
        self.log.debug('Stopped callback procedure.')

    def _get_callback_function(self) -> WINFUNCTYPE:
        """
        Define the callback procedure that should be called by the DLL every time a new measurement result
        is available or any of the wavelength meter's states changes.
        :return: callback function
        """
        def handle_callback(version, mode: int, intval: int, dblval: float, res1) -> int:
            """
            Function called upon wavelength meter state change or if a new measurement result is available.
            See wavemeter manual section on CallbackProc for details.

            In this implementation, the new wavelength is converted to the desired unit and
            appended to a list together with the current timestamp.

            :param version: Device version number which called the procedure.
            Only relevant if multiple wavemeter applications are running.
            :param mode: Indicates which state has changed or what new result is available.
            :param intval: Contains the time stamp rounded to ms if mode indicates that the new value is in dblval.
            If not, it contains the new value itself.
            :param dblval: May contain the new value (e.g. wavelength), depending on mode.
            :param res1: Mostly meaningless.
            :return: 0
            """
            # check if an evil user messed with the manufacturer GUI
            if mode == high_finesse_constants.cmiOperation and intval == high_finesse_constants.cStop:
                self.log.warning('Wavemeter acquisition was stopped during stream.')
                self.error_in_callback = True
                return 0
            elif mode == high_finesse_constants.cmiSwitcherMode:
                self.log.warning('Wavemeter switcher mode was changed during stream.')
                self.error_in_callback = True
                return 0
            elif mode == high_finesse_constants.cmiPulseMode:
                self.log.warning('Wavemeter pulse mode was changed during stream.')
                self.error_in_callback = True
                return 0

            # see if new data is from one of the active channels
            ch = high_finesse_constants.cmi_wavelength_n.get(mode)
            if ch is None:
                # not a new sample, something else happened at the wavemeter
                return 0

            if dblval < 0:
                # the wavemeter is either over- or underexposed
                # retain error code for further processing
                wavelength = dblval
            else:
                wavelength = 1e-9 * dblval  # measurement is in nm
            timestamp = 1e-3 * intval  # wavemeter records timestamps in ms
            for instreamer, channels in self._connected_instream_modules.items():
                if ch in channels:
                    instreamer.process_new_wavelength(ch, wavelength, timestamp)
            return 0

        _CALLBACK = WINFUNCTYPE(c_int, c_long, c_long, c_long, c_double, c_long)
        return _CALLBACK(handle_callback)
