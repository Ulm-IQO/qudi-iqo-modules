# -*- coding: utf-8 -*-
"""
This module contains a Qdyne manager class.
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

from dataclasses import dataclass
import datetime
from PySide2 import QtCore

from PySide2 import QtCore


@dataclass
class QdyneMeasurementSettings:
    data_type: str = ''
    read_from_file: bool = False
    pass

@dataclass
class QdyneMeasurementStatus:
    running: bool = False

class QdyneMeasurement(QtCore.QObject):
    data_type_lists = ['TimeSeries', 'TimeTag']

    # analysis timer interval
    __timer_interval = 5
    # analysis timer signals
    sigStartTimer = QtCore.Signal()
    sigStopTimer = QtCore.Signal()

    def __init__(self, qdyne_logic):
        super().__init__()
        self.qdyne_logic = qdyne_logic

        self.stg = None

        self.__start_time = 0
        self.__elapsed_time = 0
        self.__elapsed_sweeps = 0

        # set up the analysis timer
        self.__analysis_timer = QtCore.QTimer()
        self.__analysis_timer.setSingleShot(False)
        self.__analysis_timer.setInterval(round(1000. * self.__timer_interval))
        self.__analysis_timer.timeout.connect(self.qdyne_analysis_loop,
                                              QtCore.Qt.QueuedConnection)
        # set up the analysis timer signals
        self.sigStartTimer.connect(self.__analysis_timer.start, QtCore.Qt.QueuedConnection)
        self.sigStopTimer.connect(self.__analysis_timer.stop, QtCore.Qt.QueuedConnection)

    def __del__(self):
        """
        Upon deactivation of the module all signals should be disconnected.
        """
        self.__analysis_timer.timeout.disconnect()
        self.sigStartTimer.disconnect()
        self.sigStopTimer.disconnect()

    def input_settings(self, settings: QdyneMeasurementSettings) -> None:
        self.stg = settings

    @QtCore.Slot(bool, str)
    def toggle_qdyne_measurement(self, start):
        """
        Convenience method to start/stop measurement

        @param bool start: Start the measurement (True) or stop the measurement (False)
        """
        if start:
            self.start_qdyne_measurement()
        else:
            self.stop_qdyne_measurement()
        return

    def start_qdyne_measurement(self, fname=None):
        timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M-%S')
        fname = timestamp + fname if fname else timestamp
        #self.qdyne_logic._data_streamer().change_filename(fname)
        self.qdyne_logic._data_streamer().start_stream()
        self.qdyne_logic.pulsedmeasurementlogic().pulse_generator_on()
        self.sigStartTimer.emit()

    def stop_qdyne_measurement(self):
        self.qdyne_logic.pulsedmeasurementlogic().pulse_generator_off()
        self.qdyne_logic._data_streamer().stop_stream()
        self.sigStopTimer.emit()
        return

    def qdyne_analysis_loop(self):
        qdyne_logic.get_raw_data()
        qdyne_logic.get_pulse()
        qdyne_logic.extract_data()

        pass

    def get_raw_data(self):
        #Todo: raw_data =

        return raw_data

    @property
    def analysis_timer_interval(self) -> float:
        """
        Property to return the currently set analysis timer interval in seconds.
        """
        return self.__timer_interval

    @QtCore.Slot(float)
    @analysis_timer_interval.setter
    def analysis_timer_interval(self, interval: float):
        """
        Property to return the currently set analysis timer interval in seconds.
        """
        self.__timer_interval = interval
        if self.__timer_interval > 0:
            self.__analysis_timer.setInterval(int(1000. * self.__timer_interval))
            self.sigStartTimer.emit()
        else:
            self.sigStopTimer.emit()

        self.sigTimerUpdated.emit(self.__elapsed_time, self.__elapsed_sweeps,
                                  self.__timer_interval)
