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
from os import stat
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
            self.measurement_generator = MeasurementGenerator(self.pulsedmasterlogic)
            self.data = MainDataClass()
            self.save = QdyneSave(self.module_default_data_dir, self.data_storage_class)
#            self.fitting = QdyneFittingMain()

        def initialize_settings():
            self.settings.estimator_stg.initialize_settings(self._estimator_stg_dict)
            self.settings.estimator_stg.current_stg_name = self._current_estimator_stg_name
            self.settings.estimator_stg.current_method = self._current_estimator_method

            self.settings.analyzer_stg.initialize_settings(self._analyzer_stg_dict)
            self.settings.analyzer_stg.current_stg_name = self._current_analyzer_stg_name
            self.settings.analyzer_stg.current_method = self._current_analyzer_method

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

    def on_deactivate(self):
        self._save_status_variables()
        return

    def _save_status_variables(self):
        self._estimator_stg_dict = self.settings.estimator_stg.convert_settings()
        self._analyzer_stg_dict = self.settings.analyzer_stg.convert_settings()

    def input_estimator_settings(self):
        self.estimator.input_settings(self.settings.estimator_stg.current_setting)

    def input_analyzer_settings(self):
        self.analyzer.input_settings(self.settings.analyzer_stg.current_setting)

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

def get_method_name(subclass_obj, class_obj):
    subclass_name = subclass_obj.__name__
    method_name = subclass_name.replace(class_obj.__name__, '')
    return method_name

class QdyneSettings:

    def __init__(self):
        self.estimator_stg = SettingsManager(StateEstimatorSettings)
        self.analyzer_stg = SettingsManager(AnalyzerSettings)
        self.save_stg = QdyneSaveSettings()

class SettingsManager():
#    current_stg_changed_sig = QtCore.Signal()

    def __init__(self, abstract_class_obj=None):
#        super().__init__()
        self.abstract_class_obj = abstract_class_obj
        self.settings_dict = dict() # [setting_class][setting_name]
        self.current_method = ''
        self.current_stg_name = ''

    def create_default_settings_dict(self):
        default_settings_dict = dict()
        setting_classes = get_subclasses(self.abstract_class_obj)
        for setting in setting_classes:
            method_name = get_method_name(setting, self.abstract_class_obj)
            setting.name = 'default'
            default_settings_dict[method_name] = {setting.name: setting()}
        return default_settings_dict

    def initialize_settings(self, settings_dict):
        if any(settings_dict):
            self.settings_dict = self.load_settings(settings_dict)
        else:
            self.settings_dict = self.create_default_settings_dict()

    def load_settings(self, dict_tabledict):
        dataclass_tabledict = dict()
        for method_key in dict_tabledict.keys():
            dataclass_dict = dict()
            dict_dict = dict_tabledict[method_key]
            for setting_key in dict_dict.keys():
                stg_dict = dict_dict[setting_key]
                class_name = stg_dict['__class__']
                stg_dict.pop('__class__', None)
                stg_dataclass = globals()[class_name](**stg_dict)
                dataclass_dict[setting_key] = stg_dataclass
            dataclass_tabledict[method_key] = dataclass_dict
        return dataclass_tabledict

    def convert_settings(self):
        dataclass_tabledict = self.settings_dict
        dict_tabledict = dict()
        for method_key in dataclass_tabledict.keys():
            dict_dict = dict()
            dataclass_dict = dataclass_tabledict[method_key]
            for setting_key in dataclass_dict.keys():
                stg_dataclass = dataclass_dict[setting_key]
                stg_dict = asdict(stg_dataclass)
                stg_dict['__class__'] = stg_dataclass.__class__.__name__
                dict_dict[setting_key] = stg_dict
            dict_tabledict[method_key] = dict_dict
        return dict_tabledict

    @QtCore.Slot()
    def add_setting(self):
        new_setting = copy.deepcopy(self.current_setting)
        new_setting.name = new_setting.name +'_new'
        self.current_stg_name = new_setting.name
        self.settings_dict[self.current_method].update({self.current_stg_name: new_setting})
#        self.settings_dict[self.current_method][self.current_setting.name] = new_setting

    def remove_setting(self):
        self.settings_dict[self.current_method].pop(self.current_stg_name)

    @property
    def current_setting(self):
        return self.settings_dict[self.current_method][self.current_stg_name]

    @property
    def current_setting_list(self):
        return self.settings_dict[self.current_method].keys()
