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
    StateEstimatorWidget, TimeTraceAnalysisWidget

class QdyneMainGui(GuiBase):
    def on_activate(self):
        self._instantiate_widgets()
        self._mainw.tabWidget.addTab(self._sew, 'state estimater')
        self._activate_ui()
#        self._connect()

        self.show()

    def _instantiate_widgets(self):
        self._mainw = QdyneMainWindow()
#        self._pmw = MeasurementWidget()
        self._sew = StateEstimatorWidget()
#        self._ttaw =  TimeTraceAnalysisWidget()

    def _activate_ui(self):
        self._mainw.activate()
#        self._pmw.activate()
        self._sew.activate()
#        self._ttaw.activate()

    def _connect(self):
        self._mainw.connect()
#        self._pmw.connect()
        self._sew.connect()
#        self._ttaw.connect()

    def on_deactivate(self):
        self._deactivate_ui()
        self._disconnect()

    def _deactivate_ui(self):
        self._mainw.deactivate()
#        self._pmw.deactivate()
        self._sew.deactivate()
#        self._ttaw.deactivate()

    def _disconnect(self):
        self._mainw.disconnect()
#        self._pmw.disconnect()
        self._sew.disconnect()
#        self._ttaw.disconnect()

    def show(self):
        self._mainw.show()
        self._main.activateWindow()
        self._mw.raise_()
