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

import os
import numpy as np
import time
from datetime import datetime
from collections import OrderedDict
from PySide2 import QtCore

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex
from qudi.util.datastorage import TextDataStorage

from qudi.logic.qdyne.qdyne_raw_data_processor import RawDataProcessor
from qudi.logic.qdyne.qdyne_time_trace_analysis import TimeTraceAnalyzer

class QdyneLogic:

    def __init__(self):
        self.rd_processor = RawDataProcessor()
        self.tt_analyzer = TimeTraceAnalyzer()

    def configure(self):
        pass

    def start_measurement(self):
        pass

    def stop_measurement(self):
        pass

    def get_raw_data(self):
        pass

    def process_raw_data(self):
        self.time_trace = self.rd_processor.process()

    def analyze_time_trace(self):
        self.signal = self.tt_analyzer.analyze(self.time_trace)

    def save(self):
        pass
