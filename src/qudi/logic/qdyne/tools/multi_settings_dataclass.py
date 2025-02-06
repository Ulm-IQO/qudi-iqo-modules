# -*- coding: utf-8 -*-
"""
This file contains the Qudi Manager class.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-core/>

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
from dataclasses import dataclass, field, fields
from PySide2.QtCore import QObject, Signal, Slot

from qudi.logic.qdyne.tools.settings_dataclass import Settings, SettingsMediator


class MultiSettingsMediator(SettingsMediator):
    """
    A class to manage multiple settings dataclasses.
    """
    method_updated_sig = Signal()

    def __init__(self):
        super().__init__()
        self.current_method = "default"
        self.method_dict = dict() #2D dict [method][mode]
        self.mode_dict = self.method_dict[self.current_method] #current_mode_dict
        self.data_container = self.method_dict
        self.data = self.method_dict[self.current_method][self.current_mode]

    @Slot(str)
    def update_method(self, new_method: str):
        self.current_method = new_method

    def set_method(self, new_method: str):
        self.update_method(new_method)
        self.method_updated_sig.emit()

    @property
    def method_list(self):
        return list(self.method_dict.keys())

    def create_default(self, dataclass_cls_dict: dict):
        """
        create default method dictionary from a dictionary with different dataclasses.
        """
        for key in dataclass_cls_dict:
            default_mode_dict = {"default": dataclass_cls_dict[key]()}
            self.method_dict[key] = default_mode_dict

    def connect_signals(self):
        super().connect_signals()
        self.widget.method_widget_updated_sig.connect(self.update_method) #TODO consider how

    def disconnect_signas(self):
        self.widget.method_widget_updated_sig.disconnect()
