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
import os
import pickle
import copy

from PySide2 import QtCore

from qudi.core.logger import get_logger
from qudi.logic.qdyne.qdyne_state_estimator import *
from qudi.logic.qdyne.qdyne_time_trace_analyzer import *
from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings


def get_subclasses(class_obj):
    """
    Given a class, find its subclasses and get their names.
    """

    subclasses = []
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(obj) and issubclass(obj, class_obj) and obj != class_obj:
            subclasses.append(obj)

    return subclasses


def get_method_names(subclass_obj, class_obj):
    subclass_names = [cls.__name__ for cls in subclass_obj]
    method_names = [
        subclass_name.replace(class_obj.__name__, "")
        for subclass_name in subclass_names
    ]
    return method_names


def get_method_name(subclass_obj, class_obj):
    subclass_name = subclass_obj.__name__
    method_name = subclass_name.replace(class_obj.__name__, "")
    return method_name


class QdyneSettings:
    def __init__(self, settings_dir, estimator_stg_updated_sig, analyzer_stg_updated_sig):
        self.estimator_stg = SettingsManager(StateEstimatorSettings,
                                             os.path.join(settings_dir, 'estimator_stg.pickle'),
                                             estimator_stg_updated_sig)
        self.analyzer_stg = SettingsManager(AnalyzerSettings,
                                            os.path.join(settings_dir, 'analyzer_stg.pickle'),
                                            analyzer_stg_updated_sig)
        self.data_manager_stg = DataManagerSettings()


class SettingsManager:

    def __init__(self, abstract_class_obj=None, save_path=None, settings_updated_sig=None):
        self.abstract_class_obj = abstract_class_obj
        self.settings_dict = dict()  # [setting_class][setting_name]
        self.current_method = ""
        self.current_stg_name = ""
        self.save_path = save_path
        self.log = get_logger(__name__)
        self.settings_updated_sig = settings_updated_sig

    def create_default_settings_dict(self):
        default_settings_dict = dict()
        setting_classes = get_subclasses(self.abstract_class_obj)
        for setting in setting_classes:
            method_name = get_method_name(setting, self.abstract_class_obj)
            setting.name = "default"
            default_settings_dict[method_name] \
                = {setting.name: setting(_settings_updated_sig=self.settings_updated_sig)}
        return default_settings_dict

    def initialize_settings(self):
        if os.path.exists(self.save_path):
            self.load_settings()
        else:
            self.settings_dict = self.create_default_settings_dict()

    def configure_settings(self, config_dict, method=None, setting_name=None):
        if method is None:
            method = self.current_method
        elif method not in self.settings_dict:
            # Todo: give error message and return
            give_an_error
            return
        if setting_name is None:
            setting_name = self.current_stg_name
        elif setting_name not in self.settings_dict[method]:
            # Todo: give error message and return
            give_an_error
            return

        for key, value in config_dict.items():
            if hasattr(self.settings_dict[method][setting_name], key):
                setattr(self.settings_dict[method][setting_name], key, value)

    def save_settings(self):
        try:
            for setting_class in self.settings_dict.keys():
                settings = self.settings_dict[setting_class]
                for setting_name in settings.keys():
                    del settings[setting_name]._settings_updated_sig

            with open(self.save_path, 'wb') as f:
                pickle.dump(self.settings_dict, f)

        except EOFError:
            self.log.error(f"cannot save settings to {self.save_path}")

    def load_settings(self):
        try:
            with open(self.save_path, 'rb') as f:
                self.settings_dict = pickle.load(f)
            for setting_class in self.settings_dict.keys():
                settings = self.settings_dict[setting_class]
                for setting_name in settings.keys():
                    settings[setting_name]._settings_updated_sig \
                        = self.settings_updated_sig

        except EOFError:
            self.log.error(f"cannot load settings from {self.save_path}")

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
