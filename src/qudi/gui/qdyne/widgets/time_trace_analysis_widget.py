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

import copy
from inspect import Attribute
import time
import os
from PySide2.QtGui import QStandardItem, QStandardItemModel
import pyqtgraph as pg
import numpy as np
from PySide2 import QtCore, QtWidgets
from logging import getLogger

from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget


logger = getLogger(__name__)


class TimeTraceAnalysisTab(QtWidgets.QWidget):
    def __init__(self, logic, gui):
        super().__init__()
        self._instantiate_widgets(logic, gui)
        self._form_layout()

    def _instantiate_widgets(self, logic, gui):
        self._tta_layout = QtWidgets.QVBoxLayout(self)
        self._sw = TimeTraceAnalysisSettingsWidget(logic, gui)
        self._dw = TimeTraceAnalysisDataWidget(logic, gui)
        self._tta_layout.addWidget(self._sw)
        self._tta_layout.addWidget(self._dw)

    def _form_layout(self):
        self._sw.setSizePolicy(
            QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Minimum
        )
        self._dw.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

    def connect_signals(self):
        self._sw.connect_signals()
        self._dw.connect_signals()

    def disconnect_signals(self):
        self._sw.disconnect_signals()
        self._dw.disconnect_signals()

    def activate(self):
        self._sw.activate()
        self._dw.activate()

    def deactivate(self):
        pass


class TimeTraceAnalysisSettingsWidget(QtWidgets.QWidget):
    def __init__(self, logic, gui):
        self._logic = logic()
        self._gui = gui
        self.analyzer = logic().analyzer
        self.settings = logic().settings.analyzer_stg
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(
            qdyne_dir, "ui", "time_trace_analysis_settings_widget.ui"
        )

        # Load it
        super(TimeTraceAnalysisSettingsWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._activate_widgets()

    def _activate_widgets(self):
        self.tta_method_comboBox.addItems(self.analyzer.method_lists)
        self.tta_setting_comboBox.addItems(self.settings.current_setting_list)
        self.tta_settings_widget = DataclassWidget(self.settings.current_setting)
        self.tta_settings_gridLayout.addWidget(self.tta_settings_widget)

    def connect_signals(self):
        self.tta_method_comboBox.currentTextChanged.connect(self.update_current_method)
        self.tta_setting_comboBox.currentTextChanged.connect(
            self.update_current_setting
        )
        self.tta_setting_add_pushButton.clicked.connect(self.add_setting)
        self.tta_setting_delete_pushButton.clicked.connect(self.delete_setting)

    def disconnect_signals(self):
        self.tta_method_comboBox.currentTextChanged.disconnect()
        self.tta_setting_comboBox.currentTextChanged.disconnect()
        self.tta_setting_add_pushButton.clicked.disconnect()
        self.tta_setting_delete_pushButton.clicked.disconnect()

    def update_current_method(self):
        self.settings.current_method = self.tta_method_comboBox.currentText()

    def update_current_setting(self):
        self.settings.current_stg_name = self.tta_setting_comboBox.currentText()
        current_setting = copy.deepcopy(self.settings.current_setting)
        self.tta_settings_widget.update_data(current_setting)

    def add_setting(self):
        self.settings.add_setting()
        self.tta_setting_comboBox.addItem(self.settings.current_stg_name)
        self.tta_setting_comboBox.setCurrentText(self.settings.current_stg_name)

    def delete_setting(self):
        self.settings.current_stg_name = self.tta_setting_comboBox.currentText()
        current_index = self.tta_setting_comboBox.currentIndex()
        self.settings.remove_setting()
        self.tta_setting_comboBox.removeItem(current_index)


class TimeTraceAnalysisDataWidget(QtWidgets.QWidget):
    def __init__(self, logic, gui):
        self._logic = logic()
        self._gui = gui

        self.freq_data = self._logic.data.freq_data

        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, "ui", "time_trace_analysis_data_widget.ui")

        # Load it
        super(TimeTraceAnalysisDataWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._form_layout()
        self._activate_plot1_widget()
        # self._activate_plot2_widget()

    def _form_layout(self):
        self.tta_gridGroupBox.setSizePolicy(
            QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Minimum
        )
        self.plot1_GroupBox.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self.plot2_GroupBox.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

    def _activate_plot1_widget(self):
        self.range_spinBox.setValue(self.freq_data.range_index)
        self.signal_image = pg.PlotDataItem(
            pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
            style=QtCore.Qt.DotLine,
            symbol="o",
            symbolPen=palette.c1,
            symbolBrush=palette.c1,
            symbolSize=7,
        )
        self.plot1_PlotWidget.addItem(self.signal_image)
        self.plot1_PlotWidget.showGrid(x=True, y=True, alpha=0.8)
        self.plot1_PlotWidget.setLabel(axis="bottom", text="frequency", units="Hz")
        self.plot1_PlotWidget.setLabel(axis="left", text="signal", units="")

        # Configure the fit of the data in the signal display:
        self.fit_image = pg.PlotDataItem(pen=palette.c3)
        self.plot1_PlotWidget.addItem(self.fit_image)

        # Configure the errorbars of the data in the signal display:
        self.signal_image_error_bars = pg.ErrorBarItem(
            x=np.arange(10), y=np.zeros(10), top=0.0, bottom=0.0, pen=palette.c2
        )

        self.plot1_fitwidget.link_fit_container(self._logic.fit.fit_container)
        self.model = QStandardItemModel()

    def _activate_plot2_widget(self):
        # Configure the second signal plot display:
        self.second_signal_image = pg.PlotDataItem(
            pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
            style=QtCore.Qt.DotLine,
            symbol="o",
            symbolPen=palette.c1,
            symbolBrush=palette.c1,
            symbolSize=7,
        )
        self.plot2_PlotWidget.addItem(self.second_signal_image)
        self.plot2_PlotWidget.showGrid(x=True, y=True, alpha=0.8)
        # Configure the fit of the data in the secondary pulse analysis display:
        self.second_fit_image = pg.PlotDataItem(pen=palette.c3)
        self.plot2_PlotWidget.addItem(self.second_fit_image)
        self.plot2_fitwidget.link_fit_container(self._logic.fit_container2)

    def connect_signals(self):
        self.analyze_pushButton.clicked.connect(self._logic.measure.analyze_time_trace)
        self.get_freq_domain_pushButton.clicked.connect(self.get_spectrum)
        self.get_peaks_pushButton.clicked.connect(self.get_peaks)
        self.current_peak_comboBox.currentTextChanged.connect(self.update_spectrum)
        self.range_spinBox.valueChanged.connect(self.update_spectrum)
        self.plot1_fitwidget.sigDoFit.connect(
            lambda x: self._logic.do_fit(x)
        )  # fit config is input
        self.plot2_fitwidget.sigDoFit.connect(lambda x: self._logic.do_fit(x))

        self._logic.sigFitUpdated.connect(self.fit_data_updated)
        # Connect update signals from qdyne_measurement_logic
        self._logic.measure.sigQdyneDataUpdated.connect(self.data_updated)

    def disconnect_signals(self):
        self.get_peaks_pushButton.clicked.disconnect()
        self.current_peak_comboBox.currentTextChanged.disconnect()
        self.range_spinBox.valueChanged.disconnect()
        self.plot1_fitwidget.sigDoFit.disconnect()
        self.plot2_fitwidget.sigDoFit.disconnect()

    def get_spectrum(self):
        self._logic.measure.get_spectrum()
        # self.freq_data = self._logic.data.freq_data

    def get_peaks(self):
        self.freq_data.get_peaks()
        self.model.clear()
        for peak in self.freq_data.peaks:
            item = QStandardItem(str(self.freq_data.x[peak]))
            item.setData(peak)
            self.model.appendRow(item)

        self.current_peak_comboBox.setModel(self.model)

    def update_spectrum(self):
        if self.current_peak_comboBox.currentText():
            self.freq_data.current_peak = self.model.item(
                self.current_peak_comboBox.currentIndex()
            ).data()
            self.freq_data.range_index = self.range_spinBox.value()
            spectrum = self.freq_data.data_around_peak
            self.signal_image.setData(x=spectrum[0], y=spectrum[1])
            self.plot1_PlotWidget.clear()
            self.plot1_PlotWidget.addItem(self.signal_image)

    def data_updated(self):
        self.range_spinBox.setMaximum(self.freq_data.x.size)
        self.range_spinBox.setMinimum(0)
        self.get_peaks()
        self.update_spectrum()
        pass

    @QtCore.Slot(str, object)
    def fit_data_updated(self, fit_config, fit_result):
        """

        @param str fit_config:
        @param object fit_result:
        @param bool use_alternative_data:
        @return:
        """
        if not fit_config or fit_config == "No Fit":
            self._set_plot_removed()
        else:
            self._set_fit(fit_result)

    def _set_plot_removed(self):
        if self.fit_image in self.plot1_PlotWidget.items():
            self.plot1_PlotWidget.removeItem(self.fit_image)

    def _set_fit(self, fit_result):
        self.fit_image.setData(
            x=fit_result.high_res_best_fit[0], y=fit_result.high_res_best_fit[1]
        )
        if self.fit_image not in self.plot1_PlotWidget.items():
            self.plot1_PlotWidget.addItem(self.fit_image)
