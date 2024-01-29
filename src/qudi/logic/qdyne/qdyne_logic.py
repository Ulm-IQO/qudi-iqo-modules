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

from os import stat
import numpy as np
import time
from collections import OrderedDict
from PySide2 import QtCore
import datetime

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex

from qudi.logic.qdyne.qdyne_measurement import (
    QdyneMeasurement, QdyneMeasurementSettings)
from qudi.logic.qdyne.qdyne_state_estimator import *
from qudi.logic.qdyne.qdyne_time_trace_analyzer import *
from qudi.logic.qdyne.qdyne_fit import QdyneFit
from qudi.logic.qdyne.qdyne_save import (
    QdyneSaveSettings, QdyneSave)
from qudi.logic.qdyne.qdyne_settings import QdyneSettings
# from qudi.logic.qdyne.qdyne_fitting import QdyneFittingMain

@dataclass
class MainDataClass:
    raw_data: np.ndarray = np.array([], dtype=int)
    extracted_data: np.ndarray = np.array([], dtype=int)
    time_trace: np.ndarray = np.array([], dtype=float)
    signal: np.ndarray = np.array([], dtype=float)
    spectrum: np.ndarray = np.array([], dtype=float)

    def load_np_data(self, path):
        self.raw_data = np.load(path)['arr_0']

    def load_spectrum(self, path):
        self.spectrum = np.load(path)

class MeasurementGenerator:
    """
    Class that gives acces to the settings for the generation of sequences from the pulsedmasterlogic.
    """
    def __init__(self, pulsedmasterlogic):
        self.pulsedmasterlogic = pulsedmasterlogic

    def generate_predefined_sequence(self, method_name, param_dict, sample_and_load):
        self.pulsedmasterlogic().generate_predefined_sequence(
            method_name, param_dict, sample_and_load
        )
    def set_generation_parameters(self, settings_dict):
        self.pulsedmasterlogic().set_generation_parameters(settings_dict)

    def set_fast_counter_settings(self, settings_dict):
        self.pulsedmasterlogic().set_fast_counter_settings(settings_dict)

    def set_measurement_settings(self, settings_dict):
        self.pulsedmasterlogic().set_measurement_settings(settings_dict)

    @property
    def status_dict(self):
        return self.pulsedmasterlogic().status_dict

    @property
    def generation_parameters(self):
        return self.pulsedmasterlogic().generation_parameters

    @property
    def measurement_settings(self):
        return self.pulsedmasterlogic().measurement_settings

    @property
    def fast_counter_settings(self):
        return self.pulsedmasterlogic().fast_counter_settings

    @property
    def loaded_asset(self):
        return self.pulsedmasterlogic().loaded_asset

    @property
    def digital_channels(self):
        return self.pulsedmasterlogic().digital_channels

    @property
    def analog_channels(self):
        return self.pulsedmasterlogic().analog_channels

    @property
    def generate_method_params(self):
        return self.pulsedmasterlogic().generate_method_params

    @property
    def generate_methods(self):
        return self.pulsedmasterlogic().generate_methods

    @property
    def fast_counter_constraints(self):
        return self.pulsedmasterlogic().fast_counter_constraints

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
    pulsedmasterlogic = Connector(interface='PulsedMasterLogic')
    #pmeasure = Connector(interface='PulsedMeasurementLogic')
    _data_streamer = Connector(name='data_streamer', interface='DataInStreamInterface')

    # declare config options
    estimator_method = ConfigOption(name='estimator_method', default='TimeTag', missing='warn')
    analyzer_method = ConfigOption(name='analyzer_method', default='Fourier', missing='nothing')
    default_estimator_method = ConfigOption(name='default_estimator_method', default='TimeTag', missing='warn')
    default_analyzer_method = ConfigOption(name='analyzer_method', default='Fourier', missing='nothing')
    #data_save_dir = ConfigOption(name='data_save_dir')
    data_storage_class = ConfigOption(name='data_storage_class', default='text', missing='nothing')

#    estimator_method = StatusVar(default='TimeTag')
#    analyzer_method = StatusVar(default='Fourier')
    _estimator_stg_dict = StatusVar(default=dict())
    _analyzer_stg_dict = StatusVar(default=dict())
    _current_estimator_method = StatusVar(default='TimeTag')
    _current_estimator_stg_name = StatusVar(default='default')
    _current_analyzer_method = StatusVar(default='Fourier')
    _current_analyzer_stg_name = StatusVar(default='default')

    _fit_configs = StatusVar(name='fit_configs', default=None)
    _estimator_method = 'TimeTag'
    _analysis_method = 'Fourier'

    # signals for connecting modules
    sigTTFileNameUpdated = QtCore.Signal(str)
    sigFitUpdated = QtCore.Signal(str, object)


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.measure = None
        self.estimator = None
        self.analyzer = None
        self.settings = None
        self.data = None
        self.save = None

        self.tt_filename = None


    def on_activate(self):
        def activate_classes():
            #self.measure = QdyneMeasurement(self.pmaster, self.pmeasure)
            self.estimator = StateEstimatorMain(self.log)
            self.analyzer = TimeTraceAnalyzerMain()
            self.settings = QdyneSettings()
            self.measurement_generator = MeasurementGenerator(self.pulsedmasterlogic)
            self.data = MainDataClass()
            self.fit = QdyneFit(self, self._fit_configs)
            self.save = QdyneSave(self.module_default_data_dir, self.data_storage_class)
#            self.fitting = QdyneFittingMain()

        def initialize_settings():
            self.settings.estimator_stg.initialize_settings(self._estimator_stg_dict)
            self.settings.estimator_stg.current_stg_name = self._current_estimator_stg_name
            self.settings.estimator_stg.current_method = self._current_estimator_method

            self.settings.analyzer_stg.initialize_settings(self._analyzer_stg_dict)
            self.settings.analyzer_stg.current_stg_name = self._current_analyzer_stg_name
            self.settings.analyzer_stg.current_method = self._current_analyzer_method

        def input_initial_settings():
            self.input_estimator_method()
            self.input_analyzer_method()

        activate_classes()
        initialize_settings()
        input_initial_settings()
        return

    def on_deactivate(self):
        self._save_status_variables()
        return

    def _save_status_variables(self):
        self._estimator_stg_dict = self.settings.estimator_stg.convert_settings()
        self._analyzer_stg_dict = self.settings.analyzer_stg.convert_settings()

    def input_estimator_method(self):
        self.estimator.method = self.settings.estimator_stg.current_method

    def input_analyzer_method(self):
        self.analyzer.method = self.settings.analyzer_stg.current_method

    def start_measurement(self, fname=None):
        timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M-%S')
        fname = timestamp + fname if fname else timestamp
        self._data_streamer().change_filename(fname)
        self._data_streamer().start_stream()

    def stop_measurement(self):
        self._data_streamer().stop_stream()

    def get_raw_data(self):
        new_data, _ = self._data_streamer().read_data()
        self.data.raw_data = np.append(self.data.raw_data, new_data)

    def get_pulse(self):
        return self.estimator.get_pulse(self.data.raw_data, self.settings.estimator_stg.current_setting)

    def extract_data(self):
        self.data.extracted_data = self.estimator.extract(self.data.raw_data,
                                                          self.settings.estimator_stg.current_setting)

    def estimate_state(self):
        self.data.time_trace = self.estimator.estimate(self.data.extracted_data,
                                                       self.settings.estimator_stg.current_setting)

    def analyze_time_trace(self):
        self.data.signal = self.analyzer.analyze(self.data.time_trace, self.settings.analyzer_stg.current_setting)

    def get_spectrum(self):
        self.data.spectrum = self.analyzer.get_spectrum(self.data.signal, self.settings.analyzer_stg.current_setting)

    def save(self):
        self.save.save_data(self.data.raw_data, self.settings.save_stg.raw_data_options)
        self.save.save_data(self.data.time_trace, self.settings.save_stg.timetrace_options)
        self.save.save_data(self.data.signal, self.settings.save_stg.signal_options)
        pass

    @QtCore.Slot(str)
    def set_tt_filename(self, name):
        if name is None or isinstance(name, str):
            if name == '':
                name = None
            self.tt_filename = name
            self.sigTTFileNameUpdated.emit(self.tt_filename)
        else:
            self.log.error('Time trace filename must be str or None.')
        return

    @QtCore.Slot()
    def load_tt_from_file(self, filename=None):
        if filename is None or isinstance(filename, str):
            if filename is None:
                filename = self.tt_filename
            time_trace = np.load(filename)
            self.data.time_trace = time_trace['time_trace']
        else:
            self.log.error('Time trace filename to load must be str or None.')
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def do_fit(self, fit_config):
        try:
            self.data.fit_config, self.data.fit_result = self.fit.perform_fit(self.data.spectrum, fit_config)
        except:
            self.data.fit_config, self.data.fit_result = '', None
            self.log.exception('Something went wrong while trying to perform data fit.')
        self.sigFitUpdated.emit(self.data.fit_config, self.data.fit_result)
        return self.data.fit_result

