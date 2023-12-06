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
from dataclasses import dataclass, asdict
import datetime

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex
from qudi.util.datafitting import FitConfigurationsModel, FitContainer

from qudi.logic.qdyne.qdyne_measurement import (
    QdyneMeasurement, QdyneMeasurementSettings)
from qudi.logic.qdyne.qdyne_state_estimator import *
from qudi.logic.qdyne.qdyne_time_trace_analyzer import *
from qudi.logic.qdyne.qdyne_save import (
    QdyneSaveSettings, QdyneSave)
# from qudi.logic.qdyne.qdyne_fitting import QdyneFittingMain

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

    analyzer_method_lists = StatusVar()
    analyzer_method = StatusVar()
#    estimator_method = StatusVar(default='TimeTag')
#    analyzer_method = StatusVar(default='Fourier')
    _estimator_stg_dict = StatusVar(default=dict())
    _analyzer_stg_dict = StatusVar(default=dict())
    _current_estimator_method = StatusVar(default='TimeTag')
    _current_estimator_stg = StatusVar(default='TimeTag')
    _current_analyzer_method = StatusVar(default='Fourier')
    _current_analyzer_stg = StatusVar(default='Fourier')

    _fit_configs = StatusVar(name='fit_configs', default=None)
    _estimator_method = 'TimeTag'
    _analysis_method = 'Fourier'

    # signals for connecting modules
    sigTTFileNameUpdated = QtCore.Signal(str)
    sigFitUpdated = QtCore.Signal(str, object, bool)

    __default_fit_configs = (
        {'name': 'Lorentzian Dip',
         'model': 'Lorentzian',
         'estimator': 'Dip',
         'custom_parameters': None},

        {'name': 'Lorentzian Peak',
         'model': 'Lorentzian',
         'estimator': 'Peak',
         'custom_parameters': None},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.measure = None
        self.estimator = None
        self.analyzer = None
        self.settings = None
        self.data = None
        self.save = None

        self.tt_filename = None

        # for fitting
        self.fit_config_model = None  # Model for custom fit configurations
        self.fc = None  # Fit container
        self.alt_fc = None
        self._fit_result = None
        self._fit_result_alt = None

    def on_activate(self):
        def activate_classes():
            #self.measure = QdyneMeasurement(self.pmaster, self.pmeasure)
            self.estimator = StateEstimatorMain()
            self.analyzer = TimeTraceAnalyzerMain()
            self.settings = QdyneSettings()
            self.data = MainDataClass()
            self.save = QdyneSave(self.module_default_data_dir, self.data_storage_class)
#            self.fitting = QdyneFittingMain()

        def initialize_settings():
            if any(self._estimator_stg_dict):
                self.settings.estimator_stg_dict = self.settings.load_settings(self._estimator_stg_dict)
            else:
                self.settings.estimator_stg_dict = self.settings.create_default_settings_dict(StateEstimatorSettings)

            if any(self._analyzer_stg_dict):
                self.settings.analyzer_stg_dict = self.settings.load_settings(self._analyzer_stg_dict)
            else:
                self.settings.analyzer_stg_dict = self.settings.create_default_settings_dict(AnalyzerSettings)

            self.settings.current_analyzer_method = self._current_analyzer_method
            self.settings.current_estimator_method = self._current_estimator_method
            self.settings.current_analyzer_stg = self._current_analyzer_stg
            self.settings.current_estimator_stg = self._current_estimator_stg


        activate_classes()
        initialize_settings()

        # Fitting
        self.fit_config_model = FitConfigurationsModel(parent=self)
        self.fit_config_model.load_configs(self._fit_configs)
        self.fc = FitContainer(parent=self, config_model=self.fit_config_model)
        self.alt_fc = FitContainer(parent=self, config_model=self.fit_config_model)
        self.fit_container1 = FitContainer(parent=self, config_model=self.fit_config_model)
        self.fit_container2 = FitContainer(parent=self, config_model=self.fit_config_model)

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
        self._save_status_variables()
        return

    def _save_status_variables(self):
        self._estimator_stg_dict = self.settings.convert_settings(self.settings.estimator_stg_dict)
        self._analyzer_stg_dict = self.settings.convert_settings(self.settings.analyzer_stg_dict)

    def configure(self):
        self.estimator.configure_method(self.estimator_method)
        self.analyzer.configure_method(self.analysis_method)
        pass

    def input_estimator_settings(self):
        self.estimator.input_settings(self.settings.state_estimator_stg)

    def input_analyzer_settings(self):
        self.analyzer.input_settings(self.settings.time_trace_analysis_stg)

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

    @_fit_configs.representer
    def __repr_fit_configs(self, value):
        configs = self.fit_config_model.dump_configs()
        if len(configs) < 1:
            configs = None
        return configs

    @_fit_configs.constructor
    def __constr_fit_configs(self, value):
        if not value:
            return self.__default_fit_configs
        return value

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def do_fit(self, fit_config, use_alternative_data=False):
        try:
            config, result = self.perform_fit(fit_config, use_alternative_data=False)
        except:
            config, result = '', None
            self.log.exception('Something went wrong while trying to perform data fit.')
        self.sigFitUpdated.emit(config, result, use_alternative_data)
        return result

    def perform_fit(self, fit_config, use_alternative_data=False):
        """
        Performs the chosen fit on the measured data.

        @param str fit_config: name of the fit configuration to use
        @param bool use_alternative_data: Flag indicating if the signal data (False) or the
                                          alternative signal data (True) should be fitted.
                                          Ignored if data is given as parameter

        @return result_object: the lmfit result object
        """
        container = self.alt_fc if use_alternative_data else self.fc
        data = self.signal_alt_data if use_alternative_data else self.signal_data
        config, result = container.fit_data(fit_config, data[0], data[1])
        if result:
            result.result_str = container.formatted_result(result)
        if use_alternative_data:
            self._fit_result_alt = result
        else:
            self._fit_result = result
        return config, result

def get_subclasses(class_obj):
    '''
    Given a class, find its subclasses and get their names.
    '''

    subclasses = []
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(obj) and issubclass(obj, class_obj) and obj != class_obj:
            subclasses.append(obj)

    return subclasses

def get_method_names(subclass_obj, class_obj):
    subclass_names = [cls.__name__ for cls in subclass_obj]
    method_names = [subclass_name.replace(class_obj.__name__, '') for subclass_name in subclass_names]
    return method_names

class QdyneSettings:

    def __init__(self):
        self._state_estimator_method = ''
        self._time_trace_analysis_method = ''
        self.measurement_stg = None
        self.state_estimator_stg = None
        self.time_trace_analysis_stg = None
        self.save_stg = None

        self.estimator_stg_dict = dict()
        self.current_estimator_method = ''
        self.current_estimator_stg = ''

        self.analyzer_stg_dict = dict()
        self.current_analyzer_method = ''
        self.current_analyzer_stg = ''

    def on_activate(self):
        self.measurement_stg = None
        self.state_estimator_stg = self.get_state_estimator_stg(self.state_estimator_method)
        self.time_trace_analysis_stg = self.get_time_trace_analysis_stg(self.time_trace_analysis_method)
        self.save_stg = QdyneSaveSettings()

    def create_default_settings_dict(self, abstract_class_obj):
        default_settings_dict = dict()
        setting_classes = get_subclasses(abstract_class_obj)
        for setting in setting_classes:
            default_settings_dict[setting.name] = setting()
        return default_settings_dict

    def load_settings(self, dict_dict):
        dataclass_dict = dict()
        for key in dict_dict.keys():
            stg_dict = dict_dict[key]
            class_name = stg_dict['__class__']
            stg_dict.pop('__class__', None)
            stg_dataclass = globals()[class_name](**stg_dict)
            dataclass_dict[key] = stg_dataclass
        return dataclass_dict

    def convert_settings(self, dataclass_dict):
        dict_dict = dict()
        for key in dataclass_dict.keys():
            stg_dataclass = dataclass_dict[key]
            stg_dict = asdict(dataclass)
            stg_dict['class_name'] = stg_dataclass.__class__.__name__
            dict_dict[key] = stg_dict
        return dict_dict
    def add_setting(self, stg_dict, setting):
        stg_dict[setting.name] = setting

    def remove_setting(self, stg_dict, setting_name):
        stg_dict.pop(setting_name)


    @property
    def estimator_setting(self):
        return self.estimator_stg_dict[self.current_estimator_stg]

    @property
    def analyzer_setting(self):
        return self.analyzer_stg_dict[self.current_analyzer_stg]