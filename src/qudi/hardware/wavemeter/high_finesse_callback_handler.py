# -*- coding: utf-8 -*-

"""
This file contains the callback handler for the HighFinesse wavemeter. It directly communicates
with the hardware. Being a module, there should only ever be a single instance running in one
qudi process.

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

from ctypes import cast, c_double, c_int, c_long, POINTER, windll, WINFUNCTYPE
from logging import getLogger

import qudi.hardware.wavemeter.high_finesse_constants as high_finesse_constants

_log = getLogger(__name__)

try:
    # load wavemeter DLL
    _wavemeterdll = windll.LoadLibrary('wlmData.dll')
except FileNotFoundError:
    _log.error('There is no wavemeter installed on this computer.\n'
               'Please install a High Finesse wavemeter and try again.')
    raise

# define function header for a later call
_wavemeterdll.Instantiate.argtypes = [c_long, c_long, POINTER(c_long), c_long]
_wavemeterdll.Instantiate.restype = POINTER(c_long)
_wavemeterdll.ConvertUnit.restype = c_double
_wavemeterdll.ConvertUnit.argtypes = [c_double, c_long, c_long]
_wavemeterdll.SetExposureNum.restype = c_long
_wavemeterdll.SetExposureNum.argtypes = [c_long, c_long, c_long]
_wavemeterdll.GetExposureNum.restype = c_long
_wavemeterdll.GetExposureNum.argtypes = [c_long, c_long, c_long]
_wavemeterdll.GetSwitcherSignalStates.restype = c_long
_wavemeterdll.GetSwitcherSignalStates.argtypes = [c_long, POINTER(c_long), POINTER(c_long)]
_wavemeterdll.SetSwitcherSignalStates.restype = c_long
_wavemeterdll.SetSwitcherSignalStates.argtypes = [c_long, c_long, c_long]
_wavemeterdll.SetSwitcherMode.restype = c_long
_wavemeterdll.SetSwitcherMode.argtypes = [c_long]


def _reset_channels():
    """
    Deactivate all channels of the multi-channel switch.
    :return: list of available channels
    """
    ch = 1
    available_channels = []
    while True:
        # do not use or show this channel
        err = _wavemeterdll.SetSwitcherSignalStates(ch, False, False)
        print(err)
        if err == high_finesse_constants.ResERR_ChannelNotAvailable:
            break
        else:
            available_channels.append(ch)
        ch += 1
    return available_channels


def _get_active_channels():
    ch = 1
    active_channels = []
    while True:
        # do not use or show this channel
        # TODO continue here
        active = False
        _ = False
        err = _wavemeterdll.GetSwitcherSignalStates(ch, active, _)
        if err == high_finesse_constants.ResERR_ChannelNotAvailable:
            break
        else:
            available_channels.append(ch)
        ch += 1
    return available_channels


def start_callback():
    __callback_function = _get_callback_function()
    _wavemeterdll.Instantiate(
        high_finesse_constants.cInstNotification,  # long ReasonForCall
        high_finesse_constants.cNotifyInstallCallbackEx,  # long Mode
        cast(__callback_function, POINTER(c_long)),  # long P1: function
        0  # long P2: callback thread priority, 0 = standard
    )
    return __callback_function


def stop_callback():
    _wavemeterdll.Instantiate(
        high_finesse_constants.cInstNotification,  # long ReasonForCall
        high_finesse_constants.cNotifyRemoveCallback,  # long mode
        cast(_callback_function, POINTER(c_long)),
        # long P1: function
        0)  # long P2: callback thread priority, 0 = standard


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

        sample = dblval
        timestamp = 1e-3 * intval  # wavemeter records timestamps in ms
        for instreamer in _running_instream_modules:
            instreamer.add_sample(ch, sample, timestamp)
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
    # TODO get active switch channels every time
    for ch in _active_switch_channels:
        t = _wavemeterdll.GetExposureNum(ch, 1, 0)
        exposure_times.append(t)
    total_exposure_time = sum(exposure_times)

    switching_time = 12
    n_channels = len(_active_switch_channels)
    if n_channels > 1:
        turnaround_time_ms = total_exposure_time + n_channels * switching_time
    else:
        turnaround_time_ms = total_exposure_time

    return 1e3 / turnaround_time_ms


def set_exposure_time(ch, exp_time):
    res = _wavemeterdll.SetExposureNum(ch, 1, exp_time)
    if res != 0:
        _log.warning(f'Wavemeter error while setting exposure time of channel {ch}.')


def add_instreamer(module):
    if module not in _running_instream_modules:
        _running_instream_modules.append(module)
    else:
        _log.warning('Instream module is already known to be running.')


def remove_instreamer(module):
    if module in _running_instream_modules:
        _running_instream_modules.remove(module)
    else:
        _log.warning('Instream module is not known to be running.')


def convert_unit(value, unit, target_unit):
    # TODO accept human-readable units here
    converted_value = _wavemeterdll.ConvertUnit(value, unit, target_unit)
    if target_unit == high_finesse_constants.cReturnFrequency:
        converted_value *= 1e12  # value is in THz
    else:
        converted_value *= 1e-9  # value is in nm
    return converted_value


_running_instream_modules = []

err = _wavemeterdll.SetSwitcherMode(True)
print(err)

# TODO should there be an exit condition out of this loop?
while True:
    while not _running_instream_modules:
        pass
    _callback_function = start_callback()
    while _running_instream_modules:
        pass
    stop_callback()
