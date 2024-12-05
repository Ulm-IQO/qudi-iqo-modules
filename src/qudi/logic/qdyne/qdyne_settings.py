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

from dataclasses import asdict, replace, fields
import os
import pickle
import copy

from PySide2 import QtCore

from qudi.core.logger import get_logger
from qudi.logic.qdyne.qdyne_state_estimator import StateEstimatorSettings
from qudi.logic.qdyne.qdyne_time_trace_analyzer import AnalyzerSettings
from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings
from qudi.logic.qdyne.qdyne_tools import *

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
        """
        stg_cls_dict: dictionary containing dataclass for each method
        stg_param_dict: 2D dictionary containing parameters for each method

        """
        self.abstract_class_obj = abstract_class_obj
        self.stg_cls_dict = dict()
        self.stg_param_dict = dict()  # [setting_class][setting_name]
        self.current_method = ""
        self._current_stg_name = "default"
        self.save_path = save_path
        self.log = get_logger(__name__)
        self.settings_updated_sig = settings_updated_sig
        self.initialize_settings()

    def create_stg_cls_dict(self):
        default_stg_cls_dict = dict()
        setting_classes = get_subclasses(inspect.getmodule(self.abstract_class_obj),
                                         self.abstract_class_obj)
        for setting in setting_classes:
            method_name = get_method_name(setting, self.abstract_class_obj)
            default_stg_cls_dict[method_name] \
                = setting(_settings_updated_sig=self.settings_updated_sig)
        return default_stg_cls_dict

    def create_default_stg_param_dict(self):
        default_stg_param_dict = dict()
        setting_classes = get_subclasses(inspect.getmodule(self.abstract_class_obj),
                                         self.abstract_class_obj)

        for setting in setting_classes:
            method_name = get_method_name(setting, self.abstract_class_obj)
            default_stg_param_dict[method_name] = dict()
            default_stg = setting()
            default_stg.name = "default"
            default_stg_param_dict[method_name]["default"] = default_stg.to_dict()
        return default_stg_param_dict


    # def create_default_stg_dict(self):
    #     """
    #     create a dictionary containing default settings dataclasses
    #     """
    #     default_stg_cls_dict = dict()
    #     default_stg_param_dict = dict()
    #     setting_classes = get_subclasses(inspect.getmodule(self.abstract_class_obj),
    #                                      self.abstract_class_obj)
    #     print(setting_classes)
    #     for setting in setting_classes:
    #         method_name = get_method_name(setting, self.abstract_class_obj)
    #         default_stg_param_dict[method_name] = dict()
    #         setting.name = "default"
    #         default_stg_cls_dict[method_name] \
    #             = setting(_settings_updated_sig=self.settings_updated_sig)
    #         default_stg_param_dict[method_name]["default"] = default_stg_cls_dict[method_name].to_dict()
    #     print('create default')
    #     print(default_stg_cls_dict)
    #     print(default_stg_param_dict)
    #     return default_stg_cls_dict, default_stg_param_dict

    @property
    def current_stg_cls(self):
        print("current_stg_cls")
        print(self.stg_cls_dict)
        return self.stg_cls_dict[self.current_method]

    @property
    def current_stg_name(self):
        return self._current_stg_name

    @current_stg_name.setter
    def current_stg_name(self, name):
        self._current_stg_name = name
        self.update_current_settings()

    def initialize_settings(self):
        self.stg_cls_dict = self.create_stg_cls_dict()
        if os.path.exists(self.save_path):
            self.load_settings()
            self.log.debug("Saved settings loaded")

        else:
            self.stg_param_dict = self.create_default_stg_param_dict()
            self.log.debug("Default settings created")

    def configure_settings(self, config_dict, method=None, setting_name=None):
        if method is None:
            method = self.current_method
        elif method not in self.stg_cls_dict:
            # Todo: give error message and return
            give_an_error
            return

        if setting_name is None:
            setting_name = self.current_stg_name
        elif setting_name not in self.stg_param_dict[method]:
            # Todo: give error message and return
            give_an_error
            return

        for key, value in config_dict.items():
            if hasattr(self.stg_param_dict[method][setting_name], key):
                setattr(self.stg_param_dict[method][setting_name], key, value)

    def save_settings(self):
        try:
            # for stg_method in self.stg_param_dict.keys():
            #     stg_ = self.stg_param_dict[stg_method]
            #     for setting_name in settings.keys():
            #         del settings[setting_name]._settings_updated_sig

            with open(self.save_path, 'wb') as f:
                pickle.dump(self.stg_param_dict, f)

        except EOFError:
            self.log.error(f"cannot save settings to {self.save_path}")

    def load_settings(self):
        try:
            with open(self.save_path, 'rb') as f:
                self.stg_param_dict = pickle.load(f)
            # for setting_class in self.settings_dict.keys():
            #     settings = self.settings_dict[setting_class]
            #     for setting_name in settings.keys():
            #         settings[setting_name]._settings_updated_sig \
            #             = self.settings_updated_sig

        except EOFError:
            self.log.error(f"cannot load settings from {self.save_path}")

    @QtCore.Slot(str)
    def add_setting(self, new_name):
        default_setting = self.stg_param_dict[self.current_method]['default']

        new_setting = copy.deepcopy(default_setting)
        if new_name not in self.stg_param_dict.keys():
            new_setting["name"] = new_name
            self.stg_param_dict[self.current_method].update({new_name: new_setting})
            self.current_stg_name = new_name

        else:
            self.log.error('Name already taken')

    def update_current_settings(self):
        print(f"current_method {self.current_method}")
        print(f"current_stg_name {self.current_stg_name}")
        print(self.stg_param_dict)
        settings = self.stg_param_dict[self.current_method][self.current_stg_name]
        self._update_dataclass(self.current_stg_cls, settings)

    def _update_dataclass(self, dataclass, param_dict):
        valid_fields = {f.name for f in fields(dataclass)}
        for key, value in param_dict.items():
            if key in valid_fields:
                setattr(dataclass, key, value)

    @QtCore.Slot(str)
    def remove_setting(self, stg_name):
        self.stg_param_dict[self.current_method].pop(stg_name)

    @property
    def current_setting(self):
        return self.stg_cls_dict[self.current_method]

    @property
    def current_setting_list(self):
        return self.stg_param_dict[self.current_method].keys()

