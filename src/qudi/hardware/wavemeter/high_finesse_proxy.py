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
from ctypes import byref, cast, c_double, c_int, c_long, POINTER, WINFUNCTYPE
from PySide2.QtCore import QObject
from qudi.core.threadmanager import ThreadManager
from qudi.core.logger import get_logger

import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants
from qudi.hardware.wavemeter.high_finesse_wrapper import load_dll, setup_dll, MIN_VERSION


_log = get_logger(__name__)

try:
    # load wavemeter DLL
    _wavemeter_dll = load_dll()
except FileNotFoundError as e:
    raise ValueError('There is no wavemeter installed on this computer.\n'
                     'Please install a High Finesse wavemeter and try again.') from e
else:
    v = [_wavemeter_dll.GetWLMVersion(i) for i in range(4)]
    _log.info(f'Successfully loaded wavemeter DLL of WS{v[0]} {v[1]},'
              f' software revision {v[2]}, compilation number {v[3]}.')

software_rev = v[2]
if software_rev < MIN_VERSION:
    _log.warning(f'The wavemeter DLL software revision {software_rev} is older than the lowest revision '
                 f'tested to be working with the wrapper ({MIN_VERSION}). Setting up the wavemeter DLL might fail.')

try:
    setup_dll(_wavemeter_dll)
except AttributeError:
    _log.warning('One or more function is not available. The wavemeter version is likely outdated.')


def get_active_channels():
    """
    Get a list of all active channels on the multi-channel switch.
    :return: list of active channels
    """
    if not _wm_has_switch:
        return [1]

    active_channels = []
    active = c_long()
    err = 0
    ch = 1
    while err != high_finesse_constants.ResultError.ChannelNotAvailable.value:
        err = _wavemeter_dll.GetSwitcherSignalStates(ch, byref(active), byref(c_long()))
        if active:
            active_channels.append(ch)
        ch += 1
    return active_channels


def activate_channel(ch):
    """ Activate a channel on the multi-channel switch. """
    if not _wm_has_switch:
        if ch == 1:
            return
        else:
            raise RuntimeError(f'Cannot activate channel {ch}: wavemeter does not have a multi-channel switch.')

    err = _wavemeter_dll.SetSwitcherSignalStates(ch, 1, 1)
    if err:
        raise RuntimeError(f'Wavemeter error while activating channel {ch}: {high_finesse_constants.ResultError(err)}')


def deactivate_channel(ch):
    """ Deactivate a channel on the multi-channel switch. """
    if not _wm_has_switch:
        if ch == 1:
            return
        else:
            raise RuntimeError(f'Cannot deactivate channel {ch}: wavemeter does not have a multi-channel switch.')

    err = _wavemeter_dll.SetSwitcherSignalStates(ch, 0, 0)
    if err:
        raise RuntimeError(f'Wavemeter error while deactivating channel {ch}: '
                           f'{high_finesse_constants.ResultError(err)}')


def deactivate_all_but_lowest_channel():
    """
    Deactivate all channels except the channel with the lowest index.
    The wavemeter does not allow deactivation of all channels.
    """
    active_channels = get_active_channels()
    active_channels.sort()
    for i in active_channels[1:]:
        deactivate_channel(i)


def start_measurement():
    if _wm_has_switch:
        _wavemeter_dll.SetSwitcherMode(True)
    err = _wavemeter_dll.Operation(high_finesse_constants.cCtrlStartMeasurement)
    if err:
        raise RuntimeError(f'Wavemeter error while starting measurement: {high_finesse_constants.ResultError(err)}')


def stop_measurement():
    err = _wavemeter_dll.Operation(high_finesse_constants.cCtrlStopAll)
    if err:
        raise RuntimeError(f'Wavemeter error while stopping measurement: {high_finesse_constants.ResultError(err)}')


def _start_callback():
    """ Start the callback procedure. """
    global _callback_function
    _callback_function = _get_callback_function()
    _wavemeter_dll.Instantiate(
        high_finesse_constants.cInstNotification,  # long ReasonForCall
        high_finesse_constants.cNotifyInstallCallbackEx,  # long Mode
        cast(_callback_function, POINTER(c_long)),  # long P1: function
        0  # long P2: callback thread priority, 0 = standard
    )
    _log.debug('Started callback procedure.')


def _stop_callback():
    """ Stop the callback procedure. """
    global _callback_function
    _wavemeter_dll.Instantiate(
        high_finesse_constants.cInstNotification,  # long ReasonForCall
        high_finesse_constants.cNotifyRemoveCallback,  # long mode
        cast(_callback_function, POINTER(c_long)),
        # long P1: function
        0)  # long P2: callback thread priority, 0 = standard
    _callback_function = None
    _log.debug('Stopped callback procedure.')


def _get_callback_function():
    """
    Define the callback procedure that should be called by the DLL every time a new measurement result
    is available or any of the wavelength meter's states changes.
    :return: callback function
    """

    def handle_callback(version, mode, intval, dblval, res1):
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
        global _error_in_callback
        # check if an evil user messed with the manufacturer GUI
        if mode == high_finesse_constants.cmiOperation and intval == high_finesse_constants.cStop:
            _log.warning('Wavemeter acquisition was stopped during stream.')
            _error_in_callback = True
            return 0
        elif mode == high_finesse_constants.cmiSwitcherMode:
            _log.warning('Wavemeter switcher mode was changed during stream.')
            _error_in_callback = True
            return 0
        elif mode == high_finesse_constants.cmiPulseMode:
            _log.warning('Wavemeter pulse mode was changed during stream.')
            _error_in_callback = True
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
        for instreamer in _connected_instream_modules:
            instreamer.process_new_wavelength(ch, wavelength, timestamp)
        return 0

    _CALLBACK = WINFUNCTYPE(c_int, c_long, c_long, c_long, c_double, c_long)
    return _CALLBACK(handle_callback)


class CallbackErrorWatchdog(QObject):
    """A watchdog that can take care of errors in the callback function."""
    def wait_for_error(self) -> None:
        while True:
            while not _error_in_callback:
                time.sleep(1)
            self.handle_error()

    @staticmethod
    def handle_error() -> None:
        global _error_in_callback
        _log.warning('Error in callback function. Stopping all streams.')
        streamers = _connected_instream_modules.copy()
        for streamer in streamers:
            # stopping all streams should also stop callback and measurement
            streamer.stop_stream()
        _error_in_callback = False


def create_watchdog():
    """This function will create a watchdog in its own thread."""
    thread_manager = ThreadManager.instance()
    thread = thread_manager.get_new_thread('wavemeter_callback_error_watchdog')
    watchdog = CallbackErrorWatchdog()
    watchdog.moveToThread(thread)
    thread.started.connect(watchdog.wait_for_error)
    thread.start()
    return watchdog


def sample_rate() -> float:
    """
    Estimate the current sample rate by the exposure times per channel and switching times.
    :return: sample rate in Hz
    """
    exposure_times = []
    active_channels = get_active_channels()
    for ch in active_channels:
        t = _wavemeter_dll.GetExposureNum(ch, 1, 0)
        exposure_times.append(t)
    total_exposure_time = sum(exposure_times)

    switching_time = 12
    n_channels = len(active_channels)
    turnaround_time_ms = total_exposure_time + n_channels * switching_time

    return 1e3 / turnaround_time_ms


def set_exposure_time(ch, exp_time):
    """ Set the exposure time for a specific switch channel. """
    err = _wavemeter_dll.SetExposureNum(ch, 1, exp_time)
    if err:
        raise RuntimeError(f'Wavemeter error while setting exposure time of channel {ch}: '
                           f'{high_finesse_constants.ResultError(err)}')


def connect_instreamer(module):
    """
    Connect an instreamer module to the proxy.
    The proxy will start to put new samples into the instreamer buffer.
    """
    if module not in _connected_instream_modules:
        _connected_instream_modules.append(module)
        if _callback_function is None:
            start_measurement()
            _start_callback()
    else:
        _log.warning('Instream module is already connected.')


def disconnect_instreamer(module):
    """ Disconnect an instreamer module from the proxy. """
    if module in _connected_instream_modules:
        _connected_instream_modules.remove(module)
        if not _connected_instream_modules:
            _stop_callback()
            stop_measurement()
    else:
        _log.warning('Instream module is not connected and can therefore not be disconnected.')


_watchdog = create_watchdog()
_error_in_callback = False

# try to activate the multi-channel switch and check if switch is present
_wavemeter_dll.SetSwitcherMode(True)
is_active = _wavemeter_dll.GetSwitcherMode(0)
if is_active:
    _wm_has_switch = True
else:
    _wm_has_switch = False

stop_measurement()
# deactivate all channels during activation - fixes issues with incorrect sample rate estimation
deactivate_all_but_lowest_channel()

_connected_instream_modules = []
_callback_function = None
