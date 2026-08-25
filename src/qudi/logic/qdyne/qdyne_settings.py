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

from qudi.logic.qdyne.qdyne_state_estimator import ESTIMATORS
from qudi.logic.qdyne.qdyne_time_trace_analyzer import ANALYZERS
from qudi.logic.qdyne.qdyne_data_manager import DataManagerSettings
from qudi.logic.qdyne.tools.settings_mediator import SettingsMediator


class QdyneSettings:
    """Every settings container QdyneLogic owns, in one place.

    No longer a QObject: the mediators it holds are plain Python now, so there is no signal for this
    to parent. Change notification belongs to QdyneLogic (which is a QObject already) and, for the
    widgets, to the MediatorBridge in the GUI layer.

    The estimator and analyzer settings classes come from the method registries rather than from a
    scan of whichever module happened to define them. That is what guarantees every method offered
    here has an implementation behind it.
    """

    def __init__(self, default_data_dir: str):
        self.estimator_stg = SettingsMediator(ESTIMATORS.settings_classes)
        self.analyzer_stg = SettingsMediator(ANALYZERS.settings_classes)
        self.data_manager_stg = DataManagerSettings(default_data_dir)

    @property
    def estimator_cls_dict(self) -> dict:
        """{method: settings class}. Kept for callers that still ask for it by this name."""
        return ESTIMATORS.settings_classes

    @property
    def analyzer_cls_dict(self) -> dict:
        return ANALYZERS.settings_classes
