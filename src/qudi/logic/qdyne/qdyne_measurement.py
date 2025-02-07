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
import copy
import numpy as np
from dataclasses import dataclass
import datetime
from PySide2 import QtCore
from logging import getLogger
import numpy as np

from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex

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

    sigTimerIntervalUpdated = QtCore.Signal(float)
    # analysis timer signals
    sigStartTimer = QtCore.Signal()
    sigStopTimer = QtCore.Signal()
    sigMeasurementStarted = QtCore.Signal()
    sigMeasurementStopped = QtCore.Signal()
    # notification signals for master module (i.e. GUI)
    sigPulseDataUpdated = QtCore.Signal()
    sigTimeTraceDataUpdated = QtCore.Signal()
    sigQdyneDataUpdated = QtCore.Signal()

    def __init__(self, qdyne_logic):
        super().__init__()

        self.__lock = RecursiveMutex()

        self.qdyne_logic = qdyne_logic
        self.data = self.qdyne_logic.data
        self.new_data = self.qdyne_logic.new_data
        self.estimator = self.qdyne_logic.estimator
        self.settings = self.qdyne_logic.settings
        self.analyzer = self.qdyne_logic.analyzer

        self.stg = None

        self.__start_time = 0
        self.__elapsed_time = 0
        self.__elapsed_sweeps = 0

        self._measurement_running = False

        # set up the analysis timer
        self.__analysis_timer = QtCore.QTimer()
        self.__analysis_timer.setSingleShot(True)
        self.__analysis_timer.setInterval(round(1000.0 * self.qdyne_logic.analysis_timer_interval))
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

    @property
    def readout_interval(self):
        return (
            self.qdyne_logic.pulsedmasterlogic()
            .sequencegeneratorlogic()
            .get_ensemble_info(
                self.qdyne_logic.pulsedmasterlogic().sequencegeneratorlogic().loaded_asset[0]
            )[0]
        )

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
        logger.debug("resetting data")
        self.data.reset()
        # Todo: is this needed?
        #  set settings to make sure that hardware has actual settings (and not of pulsed)
        logger.debug("set counter settings")
        self.qdyne_logic.measurement_generator.set_counter_settings(
            self.qdyne_logic.measurement_generator.counter_settings
        )
        logger.debug("set measurement_settings")
        self.qdyne_logic.measurement_generator.set_measurement_settings(
            self.qdyne_logic.measurement_generator.measurement_settings
        )
        logger.debug("start measurement")
        self.qdyne_logic._data_streamer().start_measure()
        logger.debug("start pulser")
        self.qdyne_logic.pulsedmasterlogic().pulsedmeasurementlogic().pulse_generator_on()
        logger.debug("creating metadata")
        try:
            metadata = {}
            metadata.update({'generation parameters': self.qdyne_logic.measurement_generator.generation_parameters})
            metadata.update({'measurement settings': self.qdyne_logic.measurement_generator.measurement_settings})
            metadata.update({'counter settings': self.qdyne_logic.measurement_generator.counter_settings})
            metadata.update({'generation method parameters': self.qdyne_logic.measurement_generator.generate_method_params[self.qdyne_logic.measurement_generator.loaded_asset[0]]})
            logger.debug("set metadata")
            self.qdyne_logic.data_manager.set_metadata(metadata)
        except Exception as e:
            logger.exception(e)
            pass
        logger.debug("emitting started signals")
        self.sigMeasurementStarted.emit()
        self._measurement_running = True
        self.sigStartTimer.emit()

    def stop_qdyne_measurement(self):
        logger.debug("Stopping QDyne measurement")
        self.qdyne_logic.pulsedmasterlogic().pulsedmeasurementlogic().pulse_generator_off()
        self.qdyne_logic._data_streamer().stop_measure()
        self.sigMeasurementStopped.emit()
        self._measurement_running = False
        self.sigStopTimer.emit()
        return

    def qdyne_analysis_loop(self):
        with self.__lock:
            logger.debug("Entering Analysis loop")
            try:
                self.get_raw_data()
                self.get_pulse()
                logger.debug("emitting sigPulseDataUpdated")
                self.sigPulseDataUpdated.emit(self.data.pulse_data)

                self.extract_data()
                self.estimate_state()
                logger.debug("emitting sigTimeTraceDataUpdated")
                self.sigTimeTraceDataUpdated.emit(self.data.time_trace, self.readout_interval)

                self.analyze_time_trace()
                self.get_spectrum()
                logger.debug("emitting sigQdyneDataUpdated")
                self.sigQdyneDataUpdated.emit()
            except Exception as e:
                logger.exception(e)
            logger.debug("Exiting Analysis loop")
            if self._measurement_running:
                self.sigStartTimer.emit()

    def get_raw_data(self):
        try:
            self.new_data.raw_data, _ = self.qdyne_logic._data_streamer().get_data()
            self.data.raw_data = np.append(self.data.raw_data, self.new_data.raw_data)
        except Exception as e:
            logger.exception(e)
            raise e

    def get_pulse(self):
        logger.debug(f"Qdyne Measurement: get_pulse: estimator.configure_method")
        self.estimator.configure_method(self.settings.estimator_stg.current_method)
        logger.debug(f"Qdyne Measurement: get_pulse: estimator.get_pulse")
        self.data.pulse_data = self.estimator.get_pulse(
            self.data.raw_data, self.settings.estimator_stg.current_setting
        )
        logger.debug(f"Qdyne Measurement: get_pulse: emitting signal")
        self.sigPulseDataUpdated.emit()

    def extract_data(self):
        self.new_data.extracted_data = self.estimator.extract(
            self.new_data.raw_data, self.settings.estimator_stg.current_setting
        )
        self.data.extracted_data = np.append(self.data.extracted_data, self.new_data.extracted_data)

    def estimate_state(self):
        self.new_data.time_trace = self.estimator.estimate(
            self.new_data.extracted_data, self.settings.estimator_stg.current_setting
        )
        self.data.time_trace = np.append(self.data.time_trace, self.new_data.time_trace)
        self.sigTimeTraceDataUpdated.emit()

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
        return self.qdyne_logic.analysis_timer_interval

    @analysis_timer_interval.setter
    def analysis_timer_interval(self, interval: float):
        """
        Property to return the currently set analysis timer interval in seconds.
        """
        self.qdyne_logic.analysis_timer_interval = float(interval)
        if self.qdyne_logic.analysis_timer_interval > 0:
            self.__analysis_timer.blockSignals(False)

            self.__analysis_timer.setInterval(int(1000.0 * self.qdyne_logic.analysis_timer_interval))
            if self._measurement_running:
                self.sigStartTimer.emit()
        else:
            logger.info(f"Analysis interval <= 0. Analysis timer disabled.")
            self.sigStopTimer.emit()
            self.__analysis_timer.blockSignals(True)

        self.sigTimerIntervalUpdated.emit(self.qdyne_logic.analysis_timer_interval)

    def get_time_trace(self):
        """
        For test purpose. Try to get time trace from input raw data.
        """
        self.get_raw_data()
        self.get_pulse()
        self.extract_data()
        self.estimate_state()