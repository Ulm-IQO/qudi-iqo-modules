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

from ctypes import byref, cast, c_double, c_int, c_long, POINTER, WINFUNCTYPE
from logging import getLogger
import numpy as np

import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants
from qudi.hardware.wavemeter.high_finesse_wrapper import load_dll


_log = getLogger(__name__)

try:
    # load wavemeter DLL
    _wavemeter_dll = load_dll()
except FileNotFoundError:
    _log.error('There is no wavemeter installed on this computer.\n'
               'Please install a High Finesse wavemeter and try again.')
    raise
else:
    _log.debug('Successfully loaded wavemeter DLL.')


def get_active_channels():
    """
    Get a list of all active channels on the multi-channel switch.
    :return: list of active channels
    """
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
    err = _wavemeter_dll.SetSwitcherSignalStates(ch, 1, 1)
    if err:
        _log.error(f'Wavemeter error while activating channel {ch}: {high_finesse_constants.ResultError(err)}')


def deactivate_channel(ch):
    """ Deactivate a channel on the multi-channel switch. """
    err = _wavemeter_dll.SetSwitcherSignalStates(ch, 0, 0)
    if err:
        _log.error(f'Wavemeter error while deactivating channel {ch}: {high_finesse_constants.ResultError(err)}')


def deactivate_all_but_lowest_channel():
    """
    Deactivate all channels except the channel with the lowest index.
    The wavemeter does not allow deactivation of all channels.
    """
    active_channels = get_active_channels()
    active_channels.sort()
    for i in active_channels[:-1]:
        deactivate_channel(i)


def start_measurement():
    err = _wavemeter_dll.Operation(high_finesse_constants.cCtrlStartMeasurement)
    if err:
        _log.error(f'Wavemeter error while starting measurement: {high_finesse_constants.ResultError(err)}')


def stop_measurement():
    err = _wavemeter_dll.Operation(high_finesse_constants.cCtrlStopAll)
    if err:
        _log.error(f'Wavemeter error while stopping measurement: {high_finesse_constants.ResultError(err)}')


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
        if mode == high_finesse_constants.cmiOperation and intval == high_finesse_constants.cStop:
            _log.error('Wavemeter acquisition was stopped during stream.')
            return 0

        # see if new data is from one of the active channels
        ch = high_finesse_constants.cmi_wavelength_n.get(mode)
        if ch is None:
            # not a new sample, something else happened at the wavemeter
            return 0

        if dblval < 0:
            # the wavemeter is either over- or underexposed
            dblval = np.nan

        wavelength = 1e-9 * dblval  # measurement is in nm
        timestamp = 1e-3 * intval  # wavemeter records timestamps in ms
        for instreamer in _connected_instream_modules:
            instreamer.process_new_wavelength(ch, wavelength, timestamp)
        return 0

    _CALLBACK = WINFUNCTYPE(c_int, c_long, c_long, c_long, c_double, c_long)
    return _CALLBACK(handle_callback)


def sample_rate() -> float:
    """
    Estimate the current sample rate by the exposure times per channel and switching times if
    more than one channel is active.
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
    if n_channels > 1:
        turnaround_time_ms = total_exposure_time + n_channels * switching_time
    else:
        turnaround_time_ms = total_exposure_time

    return 1e3 / turnaround_time_ms


def set_exposure_time(ch, exp_time):
    """ Set the exposure time for a specific switch channel. """
    err = _wavemeter_dll.SetExposureNum(ch, 1, exp_time)
    if err:
        _log.error(f'Wavemeter error while setting exposure time of channel {ch}: '
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


# activate the multi-channel switch
_wavemeter_dll.SetSwitcherMode(True)
stop_measurement()
# deactivate all channels during activation - fixes issues with incorrect sample rate estimation
deactivate_all_but_lowest_channel()

_connected_instream_modules = []
_callback_function = None
