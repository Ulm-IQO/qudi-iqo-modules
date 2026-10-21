# -*- coding: utf-8 -*-
"""
Logic module driving a g(2) correlation measurement on a PicoQuant snAPI fast counter
(qudi.hardware.picoquant.snapi_fast_counter.SnapiFastCounter).

This connects directly to that concrete hardware class rather than through a formal qudi
interface: snAPI's Correlation API (setG2Parameters/measure/getG2Data) is specific to this one
hardware family, and there is no other implementation to abstract against. The fast counter and
a g(2) measurement share a single underlying snAPI device instance, so they cannot run at the
same time -- the hardware module itself enforces that; this logic just surfaces it.

Example configuration::

    correlation_logic:
        module.Class: 'correlation_logic.CorrelationLogic'
        options:
            start_channel: 1        # snAPI numbering: 0 = sync, 1..N = inputs
            stop_channel: 1         # same as start_channel -> autocorrelation
            window_size_s: 1.0e-6
            bin_width_s: null       # optional, defaults to device base resolution
            normalization: True
            poll_interval_s: 0.2
        connect:
            counter: 'fast_counter'

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

from __future__ import annotations

import datetime
from typing import Optional

import numpy as np
from PySide6 import QtCore

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.core.module import LogicBase
from qudi.util.datastorage import TextDataStorage
from qudi.hardware.picoquant.snapi_fast_counter import SnapiFastCounter


class CorrelationLogic(LogicBase):
    """ Drives a g(2) correlation measurement on the snAPI fast counter and polls live data. """

    # declare signals
    sigDataUpdated = QtCore.Signal(object, object)  # tau_s (1D array), g2 (1D array)
    sigStatusChanged = QtCore.Signal(bool)  # True while a measurement is running
    sigSettingsChanged = QtCore.Signal(dict)
    _sigStartTimer = QtCore.Signal()  # internal, used to (re)start __poll_timer on its own thread
    _sigStopTimer = QtCore.Signal()

    # declare connectors
    _counter = Connector(name='counter', interface=SnapiFastCounter)

    # config options
    _start_channel = ConfigOption('start_channel', 1, missing='nothing')
    _stop_channel = ConfigOption('stop_channel', 1, missing='nothing')
    _window_size_s = ConfigOption('window_size_s', 1.0e-6, missing='nothing')
    _bin_width_s = ConfigOption('bin_width_s', None, missing='nothing')
    _normalization = ConfigOption('normalization', True, missing='nothing')
    _poll_interval_s = ConfigOption('poll_interval_s', 0.2, missing='nothing')

    # status vars -- last-used settings, offered back as defaults on the next activation
    acquisition_time_s = StatusVar('acquisition_time_s', default=0.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__poll_timer: Optional[QtCore.QTimer] = None
        self._running = False
        self.tau = np.zeros(0, dtype=np.float64)
        self.g2 = np.zeros(0, dtype=np.float64)

    def on_activate(self):
        if self._poll_interval_s <= 0:
            raise ValueError('poll_interval_s must be greater than zero.')
        if self._window_size_s <= 0:
            raise ValueError('window_size_s must be greater than zero.')

        self._running = False
        self.tau = np.zeros(0, dtype=np.float64)
        self.g2 = np.zeros(0, dtype=np.float64)

        # QTimer must be created here rather than __init__, same reasoning as elsewhere in qudi:
        # it needs to live on this module's own worker thread, which only exists once activated.
        self.__poll_timer = QtCore.QTimer()
        self.__poll_timer.setSingleShot(False)
        self.__poll_timer.setInterval(round(1000. * self._poll_interval_s))
        self.__poll_timer.timeout.connect(self._poll_loop, QtCore.Qt.ConnectionType.QueuedConnection)
        self._sigStartTimer.connect(self.__poll_timer.start, QtCore.Qt.ConnectionType.QueuedConnection)
        self._sigStopTimer.connect(self.__poll_timer.stop, QtCore.Qt.ConnectionType.QueuedConnection)

    def on_deactivate(self):
        if self._running:
            self.stop_measurement()
        self._sigStartTimer.disconnect()
        self._sigStopTimer.disconnect()
        self.__poll_timer.timeout.disconnect()
        self.__poll_timer = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def settings(self) -> dict:
        return {
            'start_channel': self._start_channel,
            'stop_channel': self._stop_channel,
            'window_size_s': self._window_size_s,
            'bin_width_s': self._bin_width_s,
            'normalization': self._normalization,
            'acquisition_time_s': self.acquisition_time_s,
        }

    @property
    def bin_width_choices(self):
        """ Achievable bin widths (seconds), same list/floor the fast counter itself advertises
        (hardware_binwidth_list, already filtered by its own min_binwidth_s config option).
        """
        return list(self._counter().get_constraints()['hardware_binwidth_list'])

    def configure(self, start_channel=None, stop_channel=None, window_size_s=None,
                  bin_width_s=None, normalization=None):
        """ Update g(2) settings. Any argument left as None keeps its current value. """
        if self._running:
            raise RuntimeError('Cannot change settings while a measurement is running.')

        if start_channel is not None:
            self._start_channel = int(start_channel)
        if stop_channel is not None:
            self._stop_channel = int(stop_channel)
        if window_size_s is not None:
            if window_size_s <= 0:
                raise ValueError('window_size_s must be greater than zero.')
            self._window_size_s = float(window_size_s)
        if bin_width_s is not None:
            self._bin_width_s = float(bin_width_s)
        if normalization is not None:
            self._normalization = bool(normalization)

        self.sigSettingsChanged.emit(self.settings)

    def start_measurement(self, acquisition_time_s: Optional[float] = None):
        """ Configure and start a g(2) measurement. acquisition_time_s=0 runs until stopped. """
        if self._running:
            return

        if acquisition_time_s is not None:
            self.acquisition_time_s = float(acquisition_time_s)

        self.tau = np.zeros(0, dtype=np.float64)
        self.g2 = np.zeros(0, dtype=np.float64)

        self._counter().configure_g2(
            self._start_channel, self._stop_channel, self._window_size_s, self._bin_width_s,
            self._normalization,
        )
        self._counter().start_g2_measurement(self.acquisition_time_s)

        self._running = True
        self.sigStatusChanged.emit(True)
        self._sigStartTimer.emit()

    def stop_measurement(self):
        """ Stop a running g(2) measurement, keeping the last data received. """
        if not self._running:
            return

        self._sigStopTimer.emit()
        try:
            self._counter().stop_g2_measurement()
        finally:
            self._running = False
            self.sigStatusChanged.emit(False)

    def set_channel_trigger(self, channel: int, level_mv: int, edge: int):
        """ Set a discriminator trigger level/edge. channel: 0 = sync, 1..N = inputs. """
        self._counter().set_channel_trigger(channel, level_mv, edge)

    def get_channel_trigger(self, channel: int):
        """ Read back a discriminator trigger level/edge. channel: 0 = sync, 1..N = inputs.

        @return tuple(int, int): (level_mv, edge) -- edge: 0 = falling, 1 = rising
        """
        return self._counter().get_channel_trigger(channel)

    def save_data(self, tag: Optional[str] = None, notes: Optional[str] = None) -> str:
        """ Save the currently held tau/g(2) data to a text file.

        @param str tag: optional name tag included in the filename
        @param str notes: optional free-text note stored as-is in the file header
        @return str: the path the data was saved to
        """
        if self.tau.size == 0:
            raise RuntimeError('No g(2) data to save yet -- run a measurement first.')

        data_storage = TextDataStorage(root_dir=self.module_default_data_dir)
        metadata = {
            'start_channel': self._start_channel,
            'stop_channel': self._stop_channel,
            'window_size_s': self._window_size_s,
            'bin_width_s': self._bin_width_s if self._bin_width_s is not None else 'auto (device base resolution)',
            'normalization': self._normalization,
            'acquisition_time_s': self.acquisition_time_s,
        }
        data = np.vstack((self.tau, self.g2)).transpose()
        file_path, _, _ = data_storage.save_data(
            data,
            metadata=metadata,
            nametag=tag or 'g2_correlation',
            timestamp=datetime.datetime.now(),
            notes=notes,
            column_headers=('tau (s)', 'g(2)'),
        )
        self.log.info('g(2) data saved to %s', file_path)
        return file_path

    def _poll_loop(self):
        try:
            self.tau, self.g2 = self._counter().get_g2_data()
            self.sigDataUpdated.emit(self.tau, self.g2)

            # acquisition_time_s == 0 means "run until stop_measurement() is called" -- checking
            # is_g2_finished() in that case makes no sense (there is no time-based completion to
            # detect) and is what caused the measurement to appear to stop almost immediately:
            # snAPI's "finished" flag is a single ctypes value reused across every measure() call
            # on the same Correlation object, so it could still read stale from the previous run
            # for the first poll or two. Bounded (> 0) measurements still auto-stop normally.
            if self.acquisition_time_s > 0 and self._counter().is_g2_finished():
                self.stop_measurement()
        except Exception:
            self.log.exception('Error while polling g(2) data; stopping measurement.')
            self.stop_measurement()
