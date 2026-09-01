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
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PySide6.QtCore import Slot, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
import pyqtgraph as pg
import numpy as np
from logging import getLogger

from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.core.connector import Connector

from qudi.gui.qdyne.tools.mediator_bridge import MediatorBridge
from qudi.gui.qdyne.tools.multi_settings_widget import MultiSettingsWidget
import qudi.logic.qdyne.qdyne_logic

logger = getLogger(__name__)


class TimeTraceAnalysisTab(QWidget):
    def __init__(self, logic):
        super().__init__()
        self._logic = logic
        self._instantiate_widgets(logic)
        self._form_layout()

    def _instantiate_widgets(self, logic: "qudi.logic.qdyne.qdyne_logic.QdyneLogic"):
        self._tta_layout = QVBoxLayout(self)
        # See StateEstimationWidget._instantiate_widgets() for why the mediator is wrapped.
        self._analyzer_bridge = MediatorBridge(logic().settings.analyzer_stg, parent=self)
        self._sw = MultiSettingsWidget(self._analyzer_bridge, self._analyzer_bridge.current_data)
        self._dw = TimeTraceAnalysisDataWidget(logic(), logic().fit)
        self._tta_layout.addWidget(self._sw)
        self._tta_layout.addWidget(self._dw)

    def _form_layout(self):
        self._sw.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self._dw.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def connect_signals(self):
        self._sw.connect_signals()
        self._dw.connect_signals()
        self._connect_signals_from_data_widget()
        self._connect_signals_from_logic()

    def _connect_signals_from_data_widget(self):
        self._dw.plot1_fitwidget.sigDoFit.connect(lambda fit_config: self._logic().do_fit(fit_config))
        self._dw.plot2_fitwidget.sigDoFit.connect(lambda fit_config: self._logic().do_fit(fit_config))

        self._dw.analyze_pushButton.clicked.connect(self._logic().measure.analyze_time_trace)
        # self._dw.get_freq_domain_pushButton.clicked.connect(self._logic().measure.get_spectrum)

    def _connect_signals_from_logic(self):
        self._logic().measure.sigQdyneDataUpdated.connect(self._dw.data_updated)
        self._logic().sigFitUpdated.connect(self._dw.fit_data_updated)

    def disconnect_signals(self):
        # See StateEstimationTab.disconnect_signals() - the analyzer bridge is subscribed to a
        # mediator owned by the logic, which outlives this GUI, so it must unsubscribe here.
        self._analyzer_bridge.teardown()
        self._sw.disconnect_signals()
        # connect_signals() calls self._dw.connect_signals(); this had no counterpart, so the data
        # widget's own connections - the Analyze button and the three peak spin boxes - were never
        # released at all and survived into the next GUI session against destroyed widgets.
        self._dw.disconnect_signals()
        self._disconnect_signals_from_data_widget()
        self._disconnect_signals_from_logic()

    def _disconnect_signals_from_data_widget(self):
        self._dw.plot1_fitwidget.sigDoFit.disconnect()
        self._dw.plot2_fitwidget.sigDoFit.disconnect()

        self._dw.analyze_pushButton.clicked.disconnect()
        # self._dw.get_freq_domain_pushButton.clicked.disconnect()

    def _disconnect_signals_from_logic(self):
        self._logic().measure.sigQdyneDataUpdated.disconnect()
        self._logic().sigFitUpdated.disconnect()

    def activate(self):
        self._dw.activate()

    def deactivate(self):
        self.close()


class TimeTraceAnalysisDataWidget(QWidget):
    def __init__(self, logic: "qudi.logic.qdyne.qdyne_logic.QdyneLogic", fit_logic: ""):
        self._fit = fit_logic
        self._logic = logic

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
        self.tta_gridGroupBox.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.plot1_GroupBox.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.plot2_GroupBox.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _activate_plot1_widget(self):
        self.peak_range_spinBox.setValue(self._logic.data.freq_data.range_index)
        self.peak_threshold_spinBox.setValue(self._logic.data.freq_data.peak_threshold)
        self.peak_separation_spinBox.setValue(self._logic.data.freq_data.peak_separation)
        self.signal_image = pg.PlotDataItem(
            pen=pg.mkPen(palette.c1, style=Qt.PenStyle.DotLine),
            style=Qt.PenStyle.DotLine,
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

        self.plot1_fitwidget.link_fit_container(self._fit.fit_container)
        self.model = QStandardItemModel()

    def _activate_plot2_widget(self):
        # Configure the second signal plot display:
        self.second_signal_image = pg.PlotDataItem(
            pen=pg.mkPen(palette.c1, style=Qt.PenStyle.DotLine),
            style=Qt.PenStyle.DotLine,
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
        self.plot2_fitwidget.link_fit_container(self._fit.fit_container2)

    def connect_signals(self):
        self.analyze_pushButton.clicked.connect(self.analyze_data)
        self.current_peak_comboBox.currentTextChanged.connect(self.update_spectrum)
        self.peak_range_spinBox.editingFinished.connect(self.update_spectrum)
        self.peak_threshold_spinBox.editingFinished.connect(self.update_spectrum)
        self.peak_separation_spinBox.editingFinished.connect(self.update_spectrum)

    def disconnect_signals(self):
        # Mirrors connect_signals() exactly. The three spin boxes used to disconnect `valueChanged`,
        # which was never connected - so PySide warned about three phantom disconnects while the
        # real `editingFinished` connections were left in place, surviving into the next GUI session
        # against widgets that had already been destroyed.
        self.analyze_pushButton.clicked.disconnect()
        self.current_peak_comboBox.currentTextChanged.disconnect()
        self.peak_range_spinBox.editingFinished.disconnect()
        self.peak_threshold_spinBox.editingFinished.disconnect()
        self.peak_separation_spinBox.editingFinished.disconnect()
        # plot1_fitwidget / plot2_fitwidget sigDoFit are connected by TimeTraceAnalysisTab and
        # released there too, so disconnecting them here as well made it a double disconnect.

    def analyze_data(self):
        self._logic.measure.analyze_time_trace()
        self._logic.measure.get_spectrum()
        self.get_peaks()
        self.update_spectrum()

    def get_peaks(self):
        # block signals to avoid emitting currentTextChanged() and redundant call of update_spectrum()
        self.current_peak_comboBox.blockSignals(True)

        self._logic.data.freq_data.get_peaks()
        self.model.clear()
        for peak in self._logic.data.freq_data.peaks:
            item = QStandardItem(str(self._logic.data.freq_data.x[peak]))
            item.setData(peak)
            self.model.appendRow(item)

        current_peak = (
            float(self.current_peak_comboBox.currentText()) if self.current_peak_comboBox.currentText() else 0
        )
        model_float_list = [
            float(self.model.item(i, 0).text()) for i in range(self.model.rowCount()) if self.model.item(i, 0)
        ]
        # FreqDomainData.get_peaks() yields an empty list whenever y is None or shorter than two
        # points - i.e. before anything has been measured. argmin() on an empty array raises
        # "attempt to get argmin of an empty sequence", so there is nothing to select here.
        if not model_float_list:
            self.current_peak_comboBox.setModel(self.model)
            self.current_peak_comboBox.blockSignals(False)
            return
        new_idx = (np.abs(np.array(model_float_list) - current_peak)).argmin()
        self.current_peak_comboBox.setModel(self.model)
        self.current_peak_comboBox.setCurrentIndex(new_idx)
        self.current_peak_comboBox.blockSignals(False)

    def update_spectrum(self):
        current_index = (
            self.current_peak_comboBox.currentIndex() if self.current_peak_comboBox.currentIndex() >= 0 else 0
        )
        try:
            self._logic.data.freq_data.current_peak = self.model.item(current_index).data()
        except AttributeError:
            # return if there is no data yet
            return
        self._logic.data.freq_data.range_index = (
            self.peak_range_spinBox.value()
            if self.peak_range_spinBox.value() < self._logic.data.freq_data.x.size
            else self._logic.data.freq_data.x.size
        )
        self._logic.data.freq_data.peak_threshold = self.peak_threshold_spinBox.value()
        self._logic.data.freq_data.peak_separation = self.peak_separation_spinBox.value()
        spectrum = self._logic.data.freq_data.data_around_peak
        self.signal_image.setData(x=spectrum[0], y=spectrum[1])
        # setData already redraws. This used to call plot1_PlotWidget.clear() and re-add only
        # signal_image - and clear() removes EVERY item, so the fit curve vanished the moment you
        # touched the peak range, threshold or separation, and stayed gone until the next fit.
        if self.signal_image not in self.plot1_PlotWidget.items():
            self.plot1_PlotWidget.addItem(self.signal_image)

    def data_updated(self):
        self.peak_range_spinBox.setMaximum(int(1e9))
        self.peak_range_spinBox.setMinimum(0)
        self.peak_threshold_spinBox.setMaximum(int(1e9))
        self.peak_threshold_spinBox.setMinimum(1)
        self.peak_separation_spinBox.setMaximum(int(1e9))
        self.peak_separation_spinBox.setMinimum(0)
        self.get_peaks()
        self.update_spectrum()
        pass

    @Slot(str, object)
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
        self.fit_image.setData(x=fit_result.high_res_best_fit[0], y=fit_result.high_res_best_fit[1])
        if self.fit_image not in self.plot1_PlotWidget.items():
            self.plot1_PlotWidget.addItem(self.fit_image)
