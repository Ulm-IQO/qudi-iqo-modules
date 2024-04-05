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

    def __init__(self, pmaster, pmeasure):
        self.pmaster = pmaster
        self.pmeasure = pmeasure
        self.stg = None

    def input_settings(self, settings: QdyneMeasurementSettings) -> None:
        self.stg = settings

    def start_qdyne_measurement(self):
        timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M-%S')
        fname = timestamp + fname if fname else timestamp
        self._data_streamer().change_filename(fname)
        self._data_streamer().start_stream()
        self.pulsedmeasurementlogic().pulse_generator_on()

    def stop_qdyne_measurement(self):
        pass

    def get_raw_data(self):
        raw_data = self.pmeasure.raw_data

        return raw_data
