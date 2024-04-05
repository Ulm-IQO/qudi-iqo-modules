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
from qudi.logic.qdyne.qdyne_dataclass import MainDataClass
from qudi.logic.qdyne.qdyne_data_manager import QdyneDataManager
from qudi.logic.qdyne.qdyne_settings import QdyneSettings
# from qudi.logic.qdyne.qdyne_fitting import QdyneFittingMain


class MeasurementGenerator:
    """
    Class that gives access to the settings for the generation of sequences from the pulsedmasterlogic.
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
    pulsedmeasurementlogic = Connector(interface='PulsedMeasurementLogic')
    _data_streamer = Connector(name='data_streamer', interface='QdyneCounterInterface')

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
    sigFitUpdated = QtCore.Signal(str, object)
    sigToggleQdyneMeasurement = QtCore.Signal(bool)

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
            self.measure = QdyneMeasurement(self)
            self.estimator = StateEstimatorMain(self.log)
            self.analyzer = TimeTraceAnalyzerMain()
            self.settings = QdyneSettings()
            self.settings.data_manager_stg.set_data_dir_all(self.module_default_data_dir)
            self.measurement_generator = MeasurementGenerator(self.pulsedmasterlogic)
            self.data = MainDataClass()
            self.fit = QdyneFit(self, self._fit_configs)
            self.data_manager = QdyneDataManager(self.data, self.settings.data_manager_stg)
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

        self.sigToggleQdyneMeasurement.connect(
            self.measure.toggle_qdyne_measurement, QtCore.Qt.QueuedConnection)
        return

    def on_deactivate(self):
        self.sigToggleQdyneMeasurement.disconnect()

        self._save_status_variables()
        return

    def _save_status_variables(self):
        self._estimator_stg_dict = self.settings.estimator_stg.convert_settings()
        self._analyzer_stg_dict = self.settings.analyzer_stg.convert_settings()

    def input_estimator_method(self):
        self.estimator.method = self.settings.estimator_stg.current_method

    def input_analyzer_method(self):
        self.analyzer.method = self.settings.analyzer_stg.current_method

    @QtCore.Slot(bool)
    @QtCore.Slot(bool, str)
    def toggle_qdyne_measurement(self, start):
        """
        @param bool start: True for start measurement, False for stop measurement
        """
        if isinstance(start, bool):
            self.sigToggleQdyneMeasurement.emit(start)
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def do_fit(self, fit_config):
        try:
            self.data.fit_config, self.data.fit_result \
                = self.fit.perform_fit(self.data.freq_data.data_around_peak, fit_config)
        except:
            self.data.fit_config, self.data.fit_result = '', None
            self.log.exception('Something went wrong while trying to perform data fit.')
        self.sigFitUpdated.emit(self.data.fit_config, self.data.fit_result)
        return self.data.fit_result

    @QtCore.Slot(str)
    def save_data(self, data_type):
        if 'all' in data_type:
            for data_type in self.data_manager.data_types:
                self.data_manager.save_data(data_type)
        else:
            self.data_manager.save_data(data_type)

    @QtCore.Slot(str, str, str)
    def load_data(self, data_type, file_path, index):
        if 'all' in data_type:
            self.log.error('Select one data type')
            return
        self.data_manager.load_data(data_type, file_path, index)