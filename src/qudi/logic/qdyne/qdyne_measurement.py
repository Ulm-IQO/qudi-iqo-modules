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

import numpy as np
from dataclasses import dataclass
import datetime
from PySide2 import QtCore
from logging import getLogger
import numpy as np

logger = getLogger(__name__)


@dataclass
class QdyneMeasurementSettings:
    data_type: str = ""
    read_from_file: bool = False
    pass


@dataclass
class QdyneMeasurementStatus:
    running: bool = False


class QdyneMeasurement(QtCore.QObject):
    data_type_lists = ["TimeSeries", "TimeTag"]

    # analysis timer interval
    __timer_interval = 5
    # analysis timer signals
    sigStartTimer = QtCore.Signal()
    sigStopTimer = QtCore.Signal()

    # notification signals for master module (i.e. GUI)
    sigPulseDataUpdated = QtCore.Signal()
    sigTimeTraceDataUpdated = QtCore.Signal()
    sigQdyneDataUpdated = QtCore.Signal()

    def __init__(self, qdyne_logic):
        super().__init__()
        self.qdyne_logic = qdyne_logic
        self.data = self.qdyne_logic.data
        self.estimator = self.qdyne_logic.estimator
        self.settings = self.qdyne_logic.settings
        self.analyzer = self.qdyne_logic.analyzer

        self.stg = None

        self.__start_time = 0
        self.__elapsed_time = 0
        self.__elapsed_sweeps = 0

        # set up the analysis timer
        self.__analysis_timer = QtCore.QTimer()
        self.__analysis_timer.setSingleShot(False)
        self.__analysis_timer.setInterval(round(1000.0 * self.__timer_interval))
        self.__analysis_timer.timeout.connect(
            self.qdyne_analysis_loop, QtCore.Qt.QueuedConnection
        )
        # set up the analysis timer signals
        self.sigStartTimer.connect(
            self.__analysis_timer.start, QtCore.Qt.QueuedConnection
        )
        self.sigStopTimer.connect(
            self.__analysis_timer.stop, QtCore.Qt.QueuedConnection
        )

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
        logger.debug("Starting QDyne measurement")
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M-%S")
        fname = timestamp + fname if fname else timestamp
        # self.qdyne_logic._data_streamer().change_filename(fname)
        self.qdyne_logic._data_streamer().start_measure()
        self.qdyne_logic.pulsedmeasurementlogic().pulse_generator_on()
        self.sigStartTimer.emit()

    def stop_qdyne_measurement(self):
        logger.debug("Stopping QDyne measurement")
        self.qdyne_logic.pulsedmeasurementlogic().pulse_generator_off()
        self.qdyne_logic._data_streamer().stop_measure()
        self.sigStopTimer.emit()
        return

    def qdyne_analysis_loop(self):
        logger.debug("Entering Analysis loop")
        self.get_raw_data()
        self.get_pulse()
        self.sigPulseDataUpdated.emit()

        self.extract_data()
        self.estimate_state()
        self.sigTimeTraceDataUpdated.emit()

        self.analyze_time_trace()
        self.get_spectrum()
        self.sigQdyneDataUpdated.emit()

    def get_raw_data(self):
        try:
            new_data, _ = self.qdyne_logic._data_streamer().get_data()
            self.data.raw_data = np.append(self.data.raw_data, new_data)
        except Exception as e:
            logger.exception(e)
            raise e

    def get_pulse(self):
        self.estimator.configure_method(self.settings.estimator_stg.current_method)
        self.data.pulse_data = self.estimator.get_pulse(
            self.data.raw_data, self.settings.estimator_stg.current_setting
        )

    def extract_data(self):
        self.data.extracted_data = self.estimator.extract(
            self.data.raw_data, self.settings.estimator_stg.current_setting
        )

    def estimate_state(self):
        self.data.time_trace = self.estimator.estimate(
            self.data.extracted_data, self.settings.estimator_stg.current_setting
        )

    def analyze_time_trace(self):
        try:
            self.data.signal = self.analyzer.analyze(
                self.data, self.settings.analyzer_stg.current_setting
            )
        except Exception as e:
            logger.exception(e)
            raise e

    def get_spectrum(self):
        try:
            self.data.freq_domain = self.analyzer.get_freq_domain_signal(
                self.data, self.settings.analyzer_stg.current_setting
            )
            self.data.freq_data.x = self.data.freq_domain[0]
            self.data.freq_data.y = self.data.freq_domain[1]
        except Exception as e:
            logger.exception(e)
            raise e

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
            self.__analysis_timer.setInterval(int(1000.0 * self.__timer_interval))
            self.sigStartTimer.emit()
        else:
            self.sigStopTimer.emit()

        self.sigTimerUpdated.emit(
            self.__elapsed_time, self.__elapsed_sweeps, self.__timer_interval
        )
