# -*- coding: utf-8 -*-

"""
This file contains the GUI for qdyne measurements.

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

import os
import datetime
import numpy as np
import pyqtgraph as pg
from enum import Enum

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.helpers import natural_sort
from qudi.util.datastorage import get_timestamp_filename
from qudi.util.datastorage import TextDataStorage, CsvDataStorage, NpyDataStorage
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.core.module import GuiBase
from qudi.util import uic
from PySide2 import QtCore, QtWidgets
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from qudi.util.widgets.loading_indicator import CircleLoadingIndicator

from qudi.gui.qdyne.qdyne_widgets import QdyneMainWindow, MeasurementWidget, \
    StateEstimationWidget, TimeTraceAnalysisWidget, GenerationWidget, PredefinedMethodsConfigDialogWidget

class QdyneMainGui(GuiBase):
    _predefined_methods_to_show = StatusVar('predefined_methods_to_show', [])

    logic = Connector(interface='QdyneLogic')
    def on_activate(self):
        self._instantiate_widgets()
        self._mainw.tabWidget.addTab(self._gw, 'Measurement Generation')
        self._mainw.tabWidget.addTab(self._ttaw, 'Time Trace Analysis')
        self._mainw.tabWidget.addTab(self._sew, 'State Estimation')
        self._activate_ui()
        self._connect()

        self.show()

    def _instantiate_widgets(self):
        self._mainw = QdyneMainWindow(self)
        self._gw = GenerationWidget(self)
        self._gsw = PredefinedMethodsConfigDialogWidget(self)
        self._sew = StateEstimationWidget()
        self._ttaw = TimeTraceAnalysisWidget(self)
        self._fcd = FitConfigurationDialog(
            parent=self._mainw,
            fit_config_model=self.logic().pulsedmasterlogic().fit_config_model
        )

    def _activate_ui(self):
        self._mainw.activate()
        self._gw.activate()
        self._gsw.activate()
#        self._pmw.activate()
        self._sew.activate(self.logic().estimator, self.logic().settings.state_estimator_stg)
        self._ttaw.activate(self.logic().analyzer, self.logic().settings.time_trace_analysis_stg)

    def _connect(self):
        self._mainw.connect_signals()
        self._gw.connect_signals()
        self._gsw.connect_signals()
#        self._pmw.connect_signals()
        self._sew.connect_signals()
        self._ttaw.connect_signals()

    def on_deactivate(self):
        self._deactivate_ui()
        self._disconnect()

    def _deactivate_ui(self):
        self._mainw.deactivate()
        self._gw.deactivate()
        self._gsw.deactivate()
#        self._pmw.deactivate()
        self._sew.deactivate()
        self._ttaw.deactivate()

    def _disconnect(self):
        self._mainw.disconnect_signals()
        self._gw.disconnect_signals()
        self._gsw.disconnect_signals()
#        self._pmw.disconnect_signals()
        self._sew.disconnect_signals()
        self._ttaw.disconnect_signals()

    def show(self):
        self._mainw.show()
        self._mainw.activateWindow()
        self._mainw.raise_()
