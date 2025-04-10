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

from PySide2 import QtCore

from qudi.logic.qdyne.qdyne_state_estimator import StateEstimatorSettings
from qudi.logic.qdyne.qdyne_time_trace_analyzer import AnalyzerSettings
from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings
from qudi.logic.qdyne.tools.dataclass_tools import get_subclass_dict
from qudi.logic.qdyne.tools.multi_settings_dataclass import MultiSettingsMediator
from typing import Optional


class QdyneSettings(QtCore.QObject):
    def __init__(self, default_data_dir: str):
        super().__init__()
        self._generate_estimator_settings()
        self._generate_analyzer_settings()
        self.data_manager_stg = DataManagerSettings(default_data_dir)
        self.estimator_stg: Optional[MultiSettingsMediator] = None
        self.analyzer_stg: Optional[MultiSettingsMediator] = None

    def _generate_estimator_settings(self):
        self.estimator_cls_dict = get_subclass_dict(StateEstimatorSettings.__module__, StateEstimatorSettings)
        self.estimator_stg = MultiSettingsMediator(self)

    def _generate_analyzer_settings(self):
        self.analyzer_cls_dict = get_subclass_dict(AnalyzerSettings.__module__, AnalyzerSettings)
        self.analyzer_stg = MultiSettingsMediator(self)
