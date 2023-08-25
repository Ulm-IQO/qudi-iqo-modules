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
import time
from collections import OrderedDict
from PySide2 import QtCore
from dataclasses import dataclass

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex

from qudi.logic.qdyne.qdyne_measurement import (
    QdyneMeasurement, QdyneMeasurementSettings)
from qudi.logic.qdyne.qdyne_state_estimator import (
    StateEstimator, TimeTagBasedEstimatorSettings, TimeSeriesBasedEstimatorSettings)
from qudi.logic.qdyne.qdyne_time_trace_analyzer import (
    TimeTraceAnalyzer, FourierSettings)
from qudi.logic.qdyne.qdyne_save import (
    QdyneSaveSettings, QdyneSave)

@dataclass
class MainDataClass:
    raw_data: np.ndarray = np.array([], dtype=int)
    extracted_data: np.ndarray = np.array([], dtype=int)
    time_trace: np.ndarray = np.array([], dtype=float)
    signal: np.ndarray = np.array([], dtype=float)
    spectrum: np.ndarray = np.array([], dtype=float)

class QdyneLogic(LogicBase):
    """
    This is the Logic class for Qdyne measurements.

    example config for copy-paste:

    qdyne_logic:
        module.Class: 'qdyne.qdyne_logic.QdyneLogic'
        connect:
            data_streamer: <instreamer_name>
    """

    # declare connectors
    pmaster = Connector(interface='PulsedMasterLogic')
    pmeasure = Connector(interface='PulsedMeasurementLogic')
    _data_streamer = Connector(name='data_streamer', interface='DataInstreamInterface')

    # declare config options
    estimator_method = ConfigOption(name='estimator_method', default='TimeTag', missing='warn')
    analyzer_method = ConfigOption(name='analyzer_method', default='Fourier', missing='nothing')
    default_estimator_method = ConfigOption(name='default_estimator_method', default='TimeTag', missing='warn')
    default_analyzer_method = ConfigOption(name='analyzer_method', default='Fourier', missing='nothing')
    #data_save_dir = ConfigOption(name='data_save_dir')
    data_storage_class = ConfigOption(name='data_storage_class', default='text', missing='nothing')

#    estimator_method = StatusVar(default='TimeTag')
#    analyzer_method = StatusVar(default='Fourier')
    _estimator_method = 'TimeTag'
    _analysis_method = 'Fourier'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.measure = None
        self.estimator = None
        self.analyzer = None
        self.settings = None
        self.data = None
        self.save = None

    def on_activate(self):
        def activate_classes():
            self.measure = QdyneMeasurement(self.pmaster, self.pmeasure)
            self.estimator = StateEstimator()
            self.analyzer = TimeTraceAnalyzer()
            self.settings = QdyneSettings()
            self.data = MainDataClass()
            self.save = QdyneSave(self.data_save_dir, self.data_storage_class)

        def set_default_values():
            self.estimator_method = self._estimator_method
            self.analysis_method = self._analysis_method

        activate_classes()
        set_default_values()

        return

    @property
    def estimator_method(self):
        return self._estimator_method

    @estimator_method.setter
    def estimator_method(self, estimator_method):
        self._estimator_method = estimator_method
        self.settings.state_estimator_method = self._estimator_method
        self.estimator.method = self._estimator_method

    @property
    def analysis_method(self):
        return self._analysis_method

    @analysis_method.setter
    def analysis_method(self, analysis_method):
        self._analysis_method = analysis_method
        self.settings.time_trace_analysis_method = self._analysis_method
        self.analyzer.method = self._analysis_method

    def on_deactivate(self):
        return

    def configure(self):
        self.estimator.configure_method(self.estimator_method)
        self.analyzer.configure_method(self.analysis_method)
        pass

    def input_estimator_settings(self):
        self.estimator.input_settings(self.settings.state_estimator_stg)

    def input_analyzer_settings(self):
        self.analyzer.input_settings(self.settings.time_trace_analysis_stg)

    def start_measurement(self):
        pass

    def stop_measurement(self):
        pass

    def get_raw_data(self):
        pass

    def extract_data(self):
        self.data.extracted_data = self.estimator.extract(self.data.raw_data)

    def process_extracted_data(self):
        self.data.time_trace = self.estimator.estimate(self.data.extracted_data)

    def analyze_time_trace(self):
        self.data.signal = self.analyzer.analyze(self.data.time_trace)

    def get_spectrum(self):
        self.data.spectrum = self.analyzer.get_spectrum(self.data.signal)

    def save(self):
        self.save.save_data(self.data.raw_data, self.settings.save_stg.raw_data_options)
        self.save.save_data(self.data.time_trace, self.settings.save_stg.timetrace_options)
        self.save.save_data(self.data.signal, self.settings.save_stg.signal_options)
        pass

class QdyneSettings:

    def __init__(self):
        self._state_estimator_method = ''
        self._time_trace_analysis_method = ''
        self.measurement_stg = None
        self.state_estimator_stg = None
        self.time_trace_analysis_stg = None
        self.save_stg = None

    def on_activate(self):
        self.measurement_stg = None
        self.state_estimator_stg = self.get_state_estimator_stg(self.state_estimator_method)
        self.time_trace_analysis_stg = self.get_time_trace_analysis_stg(self.time_trace_analysis_method)
        self.save_stg = QdyneSaveSettings()

    def get_measurement_stg(self):
        pass

    @property
    def state_estimator_method(self):
        return self._state_estimator_method

    @state_estimator_method.setter
    def state_estimator_method(self, state_estimator_method):
        self._state_estimator_method = state_estimator_method
        self._get_state_estimator_stg()

    def _get_state_estimator_stg(self):
        if self.state_estimator_method == 'TimeSeries':
            self.state_estimator_stg = TimeSeriesBasedEstimatorSettings()

        elif self.state_estimator_method == 'TimeTag':
            self.state_estimator_stg = TimeTagBasedEstimatorSettings()

        else:
            self.state_estimator_stg = None

    @property
    def time_trace_analysis_method(self):
        return self._time_trace_analysis_method

    @time_trace_analysis_method.setter
    def time_trace_analysis_method(self, time_trace_analysis_method):
        self._time_trace_analysis_method = time_trace_analysis_method
        self._get_time_trace_analysis_stg()

    def _get_time_trace_analysis_stg(self):
        if self.time_trace_analysis_method == 'Fourier':
            self.time_trace_analysis_stg = FourierSettings()

        else:
            self.time_trace_analysis_stg = None
