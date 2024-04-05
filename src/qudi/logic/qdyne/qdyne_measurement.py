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
from PySide2 import QtCore, QtWidgets

from qudi.core.connector import Connector


@dataclass
class QdyneMeasurementSettings:
    data_type: str = ''
    read_from_file: bool = False
    pass

@dataclass
class QdyneMeasurementStatus:
    running: bool = False

class QdyneMeasurement:
    data_type_lists = ['TimeSeries', 'TimeTag']

    def __init__(self, qdyne_logic):
        self.qdyne_logic = qdyne_logic
        self.stg = None

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

    def stop_qdyne_measurement(self):
        self.qdyne_logic.pulsedmeasurementlogic().pulse_generator_off()
        self.qdyne_logic._data_streamer().stop_stream()
        return

    def qdyne_analysis_loop(self):
        qdyne_logic.get_raw_data()
        qdyne_logic.get_pulse()
        qdyne_logic.extract_data()

        pass

    def get_raw_data(self):
        #Todo: raw_data =

        return raw_data
