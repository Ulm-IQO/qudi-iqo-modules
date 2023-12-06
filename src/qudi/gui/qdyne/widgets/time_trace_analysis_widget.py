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
import pyqtgraph as pg
import numpy as np
from PySide2 import QtCore, QtWidgets

from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget

class TimeTraceAnalysisWidget(QtWidgets.QWidget):
    # declare signals
    sigTTFileNameChanged = QtCore.Signal(str)
    sigLoadTT = QtCore.Signal()

    def __init__(self, gui):
        self._gui = gui

        self.analyzer = None
        self.settings = None

        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\time_trace_analysis_widget.ui')

        # Load it
        super(TimeTraceAnalysisWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self, analyzer, settings):
        self.analyzer = analyzer
        self.settings = settings
        self._activate_widgets()

    def _activate_widgets(self):
        self.tta_method_comboBox.addItems(self.analyzer.method_lists)
        self.tta_settings_widget = DataclassWidget(self.settings.analyzer_setting)
        self.tta_settings_gridLayout.addWidget(self.tta_settings_widget)

        # Configure the main signal display:
        self.signal_image = pg.PlotDataItem(pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
                                             style=QtCore.Qt.DotLine,
                                             symbol='o',
                                             symbolPen=palette.c1,
                                             symbolBrush=palette.c1,
                                             symbolSize=7)
        self.plot1_PlotWidget.addItem(self.signal_image)
        self.plot1_PlotWidget.showGrid(x=True, y=True, alpha=0.8)

        # Configure the fit of the data in the signal display:
        self.fit_image = pg.PlotDataItem(pen=palette.c3)
        self.plot1_PlotWidget.addItem(self.fit_image)

        # Configure the errorbars of the data in the signal display:
        self.signal_image_error_bars = pg.ErrorBarItem(x=np.arange(10),
                                                       y=np.zeros(10),
                                                       top=0.,
                                                       bottom=0.,
                                                       pen=palette.c2)

        # Configure the second signal plot display:
        self.second_signal_image = pg.PlotDataItem(pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
                                                   style=QtCore.Qt.DotLine,
                                                   symbol='o',
                                                   symbolPen=palette.c1,
                                                   symbolBrush=palette.c1,
                                                   symbolSize=7)
        self.plot2_PlotWidget.addItem(self.second_signal_image)
        self.plot2_PlotWidget.showGrid(x=True, y=True, alpha=0.8)
        # Configure the fit of the data in the secondary pulse analysis display:
        self.second_fit_image = pg.PlotDataItem(pen=palette.c3)
        self.plot2_PlotWidget.addItem(self.second_fit_image)

        # Fit settings dialog
        fit_containers = (self._gui.logic().fit_container1, self._gui.logic().fit_container2)
        self.plot1_fitwidget.link_fit_container(fit_containers[0])
        self.plot2_fitwidget.link_fit_container(fit_containers[1])

    def deactivate(self):
        pass

    def connect_signals(self):
        # connect control signals to logic
        self.tta_method_comboBox.editTextChanged.connect(self.update_current_analyzer_setting)
        self.sigTTFileNameChanged.connect(
            self._gui.logic().set_tt_filename, QtCore.Qt.QueuedConnection)
        self.sigLoadTT.connect(self._gui.logic().load_tt_from_file, QtCore.Qt.QueuedConnection)
        self.plot1_fitwidget.sigDoFit.connect(
            lambda x: self._gui.logic().do_fit(x, False)
        )
        self.plot2_fitwidget.sigDoFit.connect(
            lambda x: self._gui.logic().do_fit(x, True)
        )

        # connect update signals from logic
        self._gui.logic().sigTTFileNameUpdated.connect(self.update_tt_filename, QtCore.Qt.QueuedConnection)
        self._gui.logic().sigFitUpdated.connect(self.fit_data_updated)

        # connect internal signals
        self.tta_filename_LineEdit.editingFinished.connect(self.tt_filename_changed)
        self.tta_browsefile_PushButton.clicked.connect(self.browse_file)
        self.tta_loadfile_PushButton.clicked.connect(self.load_time_trace)

    def disconnect_signals(self):
        self.sigTTFileNameChanged.disconnect()
        self.sigLoadTT.disconnect()
        self.plot1_fitwidget.sigDoFit.disconnect()
        self.plot2_fitwidget.sigDoFit.disconnect()

        self._gui.logic().sigTTFileNameUpdated.disconnect()
        self._gui.logic().sigFitUpdated.disconnect()

        self.tta_filename_LineEdit.editingFinished.disconnect()
        self.tta_browsefile_PushButton.clicked.disconnect()
        self.tta_loadfile_PushButton.clicked.disconnect()

    @QtCore.Slot(str)
    def update_current_analyzer_setting(self, setting):
        self.settings.current_analyzer_stg = setting

    @QtCore.Slot()
    def tt_filename_changed(self):
        self.sigTTFileNameChanged.emit(self.tta_filename_LineEdit.text())
        return

    @QtCore.Slot(str)
    def update_tt_filename(self, name):
        if name is None:
            name = ''
        self.tta_filename_LineEdit.blockSignals(True)
        self.tta_filename_LineEdit.setText(name)
        self.tta_filename_LineEdit.blockSignals(False)
        return

    @QtCore.Slot()
    def browse_file(self):
        """ Browse a saved time trace from file."""
        this_file = QtWidgets.QFileDialog.getOpenFileName(self._gui._mainw,
                                                          'Open time trace file',
                                                          self._gui.logic().module_default_data_dir,
                                                          'Data files (*.npz)')[0]
        if this_file:
            self.sigTTFileNameChanged.emit(this_file)
        return

    @QtCore.Slot()
    def load_time_trace(self):
        self.sigLoadTT.emit()
        return

    @QtCore.Slot(str, object, bool)
    def fit_data_updated(self, fit_config, result, use_alternative_data):
        """

        @param str fit_config:
        @param object result:
        @param bool use_alternative_data:
        @return:
        """
        plot1_fitwidget
        # Update plot.
        if use_alternative_data:
            if not fit_config or fit_config == 'No Fit':
                if self.second_fit_image in self._pa.pulse_analysis_second_PlotWidget.items():
                    self._pa.pulse_analysis_second_PlotWidget.removeItem(self.second_fit_image)
            else:
                self.second_fit_image.setData(x=result.high_res_best_fit[0],
                                              y=result.high_res_best_fit[1])
                if self.second_fit_image not in self._pa.pulse_analysis_second_PlotWidget.items():
                    self._pa.pulse_analysis_second_PlotWidget.addItem(self.second_fit_image)
        else:
            if not fit_config or fit_config == 'No Fit':
                if self.fit_image in self._pa.pulse_analysis_PlotWidget.items():
                    self._pa.pulse_analysis_PlotWidget.removeItem(self.fit_image)
            else:
                self.fit_image.setData(x=result.high_res_best_fit[0],
                                       y=result.high_res_best_fit[1])
                if self.fit_image not in self._pa.pulse_analysis_PlotWidget.items():
                    self._pa.pulse_analysis_PlotWidget.addItem(self.fit_image)
        return
