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

from PySide2 import QtWidgets

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.core.module import GuiBase

from qudi.gui.qdyne.widgets.main_window import QdyneMainWindow
from qudi.gui.qdyne.widgets.measurement_widget import MeasurementWidget
from qudi.gui.qdyne.widgets.generation_widget import GenerationWidget
from qudi.gui.qdyne.widgets.predefined_method_config_dialog_widget import PredefinedMethodsConfigDialogWidget
from qudi.gui.qdyne.widgets.state_estimation_widget import StateEstimationTab
from qudi.gui.qdyne.widgets.time_trace_analysis_widget import TimeTraceAnalysisTab

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
        self._mainw = QdyneMainWindow(self, self.logic, self.log)
        self._gw = GenerationWidget(self)
        self._gsw = PredefinedMethodsConfigDialogWidget(self)
        self._sew = StateEstimationTab(self.logic)
        self._ttaw = TimeTraceAnalysisTab(self.logic, self)
        self._fcd = FitConfigurationDialog(
            parent=self._mainw,
            fit_config_model=self.logic().fit.fit_config_model
        )

    def _activate_ui(self):
        self._mainw.activate()
        self._gw.activate()
        self._gsw.activate()
#        self._pmw.activate()
        self._sew.activate_ui()
        self._ttaw.activate()

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
        self._sew.deactivate_ui()

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
