# -*- coding: utf-8 -*-
"""
This module contains a Qdyne settings class.
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
from dataclasses import asdict
import inspect
import sys
import copy

from PySide2 import QtCore

from qudi.core.logger import get_logger
from qudi.logic.qdyne.qdyne_state_estimator import *
from qudi.logic.qdyne.qdyne_time_trace_analyzer import *
from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings

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
        self.data_manager_stg = DataManagerSettings()

class SettingsManager():

    def __init__(self, abstract_class_obj=None):
        self.abstract_class_obj = abstract_class_obj
        self.settings_dict = dict() # [setting_class][setting_name]
        self.current_method = ''
        self.current_stg_name = ''
        self.log = get_logger(__name__)

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

    @QtCore.Slot(str)
    def add_setting(self, new_name):
        default_setting = self.settings_dict[self.current_method]['default']
        new_setting = copy.deepcopy(default_setting)
        if new_name not in self.settings_dict.keys():
            new_setting.name = new_name
            self.settings_dict[self.current_method].update({new_name: new_setting})
            self.current_stg_name = new_name

        else:
            self.log.error('Name already taken')

    @QtCore.Slot(str)
    def remove_setting(self, stg_name):
        self.settings_dict[self.current_method].pop(stg_name)

    @property
    def current_setting(self):
        return self.settings_dict[self.current_method][self.current_stg_name]

    @property
    def current_setting_list(self):
        return self.settings_dict[self.current_method].keys()
