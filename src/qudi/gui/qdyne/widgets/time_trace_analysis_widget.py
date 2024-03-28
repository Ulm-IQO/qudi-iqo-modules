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
import time
import os
import pyqtgraph as pg
import numpy as np
from PySide2 import QtCore, QtWidgets

from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget

class TimeTraceAnalysisTab(QtWidgets.QWidget):

    def __init__(self, logic, gui):
        super().__init__()
        self._instantiate_widgets(logic, gui)
        self._form_layout()

    def _instantiate_widgets(self, logic, gui):
        self._tta_layout = QtWidgets.QVBoxLayout(self)
        self._lw = TimeTraceAnalysisLoaderWidget(logic, gui)
        self._sw = TimeTraceAnalysisSettingsWidget(logic, gui)
        self._dw = TimeTraceAnalysisDataWidget(logic, gui)
        self._tta_layout.addWidget(self._lw)
        self._tta_layout.addWidget(self._sw)
        self._tta_layout.addWidget(self._dw)

    def _form_layout(self):
        self._lw.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Minimum)
        self._sw.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Minimum)
        self._dw.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def connect_signals(self):
        self._lw.connect_signals()
        self._sw.connect_signals()
        self._dw.connect_signals()

    def disconnect_signals(self):
        self._lw.disconnect_signals()
        self._sw.disconnect_signals()
        self._dw.disconnect_signals()

    def activate(self):
        self._lw.activate()
        self._sw.activate()
        self._dw.activate()

    def deactivate(self):
        pass

class TimeTraceAnalysisLoaderWidget(QtWidgets.QWidget):
    sigTTFileNameChanged = QtCore.Signal(str)
    sigLoadTT = QtCore.Signal()

    def __init__(self, logic, gui):
        self._logic = logic()
        self._gui = gui

        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\time_trace_analysis_loader_widget.ui')

        # Load it
        super(TimeTraceAnalysisLoaderWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass
    def connect_signals(self):
        # connect internal signals
        self.tta_filename_LineEdit.editingFinished.connect(self.tt_filename_changed)
        self.tta_browsefile_PushButton.clicked.connect(self.browse_file)
        self.tta_loadfile_PushButton.clicked.connect(self.load_time_trace)

    def disconnect_signals(self):
        self.sigTTFileNameChanged.disconnect()
        self.sigLoadTT.disconnect()

        self._logic.sigTTFileNameUpdated.disconnect()
        self._logic.sigFitUpdated.disconnect()

        self.tta_filename_LineEdit.editingFinished.disconnect()
        self.tta_browsefile_PushButton.clicked.disconnect()
        self.tta_loadfile_PushButton.clicked.disconnect()

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
                                                          self._logic.module_default_data_dir,
                                                          'Data files (*.npz)')[0]
        if this_file:
            self.sigTTFileNameChanged.emit(this_file)
        return

    @QtCore.Slot()
    def load_time_trace(self):
        self.sigLoadTT.emit()
        return


class TimeTraceAnalysisSettingsWidget(QtWidgets.QWidget):
    def __init__(self, logic, gui):
        self._logic = logic()
        self._gui = gui
        self.analyzer = logic().analyzer
        self.settings = logic().settings.analyzer_stg
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\time_trace_analysis_settings_widget.ui')

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
        self.tta_setting_comboBox.currentTextChanged.connect(self.update_current_setting)
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
        ui_file = os.path.join(qdyne_dir, r'ui\time_trace_analysis_data_widget.ui')

        # Load it
        super(TimeTraceAnalysisDataWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._form_layout()
        self._activate_plot1_widget()
        #self._activate_plot2_widget()

    def _form_layout(self):
        self.tta_gridGroupBox.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Minimum)
        self.plot1_GroupBox.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.plot2_GroupBox.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)


    def _activate_plot1_widget(self):
        self.range_spinBox.setValue(self.freq_data.range_index)
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

        self.plot1_fitwidget.link_fit_container(self._logic.fit.fit_container)

    def _activate_plot2_widget(self):
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
        self.plot2_fitwidget.link_fit_container(self._logic.fit_container2)


    def connect_signals(self):
        self.tta_analyze_pushButton.clicked.connect(self._logic.analyze_time_trace)
        self.tta_get_spectrum_pushButton.clicked.connect(self.get_spectrum)
        self.get_peaks_pushButton.clicked.connect(self.get_peaks)
        self.current_peak_comboBox.currentTextChanged.connect(self.update_spectrum)
        self.range_spinBox.valueChanged.connect(self.update_spectrum)
        self.plot1_fitwidget.sigDoFit.connect(
            lambda x: self._logic.do_fit(x)
        ) #fit config is input
        self.plot2_fitwidget.sigDoFit.connect(
            lambda x: self._logic.do_fit(x)
        )

        self._logic.sigFitUpdated.connect(self.fit_data_updated)


    def disconnect_signals(self):
        self.get_peaks_pushButton.clicked.disconnect()
        self.current_peak_comboBox.currentTextChanged.disconnect()
        self.range_spinBox.valueChanged.disconnect()
        self.plot1_fitwidget.sigDoFit.disconnect()
        self.plot2_fitwidget.sigDoFit.disconnect()

    def get_spectrum(self):
        self._logic.get_spectrum()

    def get_peaks(self):
        self.freq_data.get_peaks()
        self.current_peak_comboBox.clear()
        peak_str_list = [str(peak) for peak in self.freq_data.peaks]
        self.current_peak_comboBox.addItems(peak_str_list)

    def update_spectrum(self):
        self.freq_data.current_peak = int(self.current_peak_comboBox.currentText())
        self.freq_data.range_index = self.range_spinBox.value()
        spectrum = self.freq_data.data_around_peak
        self.signal_image.setData(x=spectrum[0], y=spectrum[1])
        self.plot1_PlotWidget.clear()
        self.plot1_PlotWidget.addItem(self.signal_image)

    @QtCore.Slot(str, object)
    def fit_data_updated(self, fit_config, fit_result):
        """

        @param str fit_config:
        @param object fit_result:
        @param bool use_alternative_data:
        @return:
        """
        if not fit_config or fit_config == 'No Fit':
            self._set_plot_removed()
        else:
            self._set_fit(fit_result)

    def _set_plot_removed(self):
        if self.fit_image in self.plot1_PlotWidget.items():
            self.plot1_PlotWidget.removeItem(self.fit_image)

    def _set_fit(self, fit_result):
        self.fit_image.setData(x=fit_result.high_res_best_fit[0],
                               y=fit_result.high_res_best_fit[1])
        if self.fit_image not in self.plot1_PlotWidget.items():
            self.plot1_PlotWidget.addItem(self.fit_image)





# class TimeTraceAnalysisWidget(QtWidgets.QWidget):
#     # declare signals
#     sigTTFileNameChanged = QtCore.Signal(str)
#     sigLoadTT = QtCore.Signal()
#
#     def __init__(self, gui, logic):
#         self._gui = gui
#         self.logic = logic
#
#         self.analyzer = logic().analyzer
#         self.settings = logic().settings.analyzer_stg
#
#         # Get the path to the *.ui file
#         qdyne_dir = os.path.dirname(os.path.dirname(__file__))
#         ui_file = os.path.join(qdyne_dir, r'ui\time_trace_analysis_widget.ui')
#
#         # Load it
#         super(TimeTraceAnalysisWidget, self).__init__()
#
#         uic.loadUi(ui_file, self)
#
#     def activate(self):
#         self._activate_widgets()
#
#     def _activate_widgets(self):
#         self.tta_method_comboBox.addItems(self.analyzer.method_lists)
#         self.tta_setting_comboBox.addItems(self.settings.current_setting_list)
#         self.tta_settings_widget = DataclassWidget(self.settings.current_setting)
#         self.tta_settings_gridLayout.addWidget(self.tta_settings_widget)
#
#         # Configure the main signal display:
#         self.signal_image = pg.PlotDataItem(pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
#                                              style=QtCore.Qt.DotLine,
#                                              symbol='o',
#                                              symbolPen=palette.c1,
#                                              symbolBrush=palette.c1,
#                                              symbolSize=7)
#         self.plot1_PlotWidget.addItem(self.signal_image)
#         self.plot1_PlotWidget.showGrid(x=True, y=True, alpha=0.8)
#
#         # Configure the fit of the data in the signal display:
#         self.fit_image = pg.PlotDataItem(pen=palette.c3)
#         self.plot1_PlotWidget.addItem(self.fit_image)
#
#         # Configure the errorbars of the data in the signal display:
#         self.signal_image_error_bars = pg.ErrorBarItem(x=np.arange(10),
#                                                        y=np.zeros(10),
#                                                        top=0.,
#                                                        bottom=0.,
#                                                        pen=palette.c2)
#
#         # Configure the second signal plot display:
#         self.second_signal_image = pg.PlotDataItem(pen=pg.mkPen(palette.c1, style=QtCore.Qt.DotLine),
#                                                    style=QtCore.Qt.DotLine,
#                                                    symbol='o',
#                                                    symbolPen=palette.c1,
#                                                    symbolBrush=palette.c1,
#                                                    symbolSize=7)
#         self.plot2_PlotWidget.addItem(self.second_signal_image)
#         self.plot2_PlotWidget.showGrid(x=True, y=True, alpha=0.8)
#         # Configure the fit of the data in the secondary pulse analysis display:
#         self.second_fit_image = pg.PlotDataItem(pen=palette.c3)
#         self.plot2_PlotWidget.addItem(self.second_fit_image)
#
#         # Fit settings dialog
#         fit_containers = (self._logic.fit_container1, self._logic.fit_container2)
#         self.plot1_fitwidget.link_fit_container(fit_containers[0])
#         self.plot2_fitwidget.link_fit_container(fit_containers[1])
#
#     def deactivate(self):
#         pass
#
#     def connect_signals(self):
#         # connect control signals to logic
#         self.tta_method_comboBox.currentTextChanged.connect(self.update_current_method)
#         self.tta_setting_comboBox.currentTextChanged.connect(self.update_current_setting)
#         self.tta_setting_add_pushButton.clicked.connect(self.add_setting)
#         self.tta_setting_delete_pushButton.clicked.connect(self.delete_setting)
#         self.sigTTFileNameChanged.connect(
#             self._logic.set_tt_filename, QtCore.Qt.QueuedConnection)
#         self.sigLoadTT.connect(self._logic.load_tt_from_file, QtCore.Qt.QueuedConnection)
#         self.plot1_fitwidget.sigDoFit.connect(
#             lambda x: self._logic.do_fit(x, False)
#         )
#         self.plot2_fitwidget.sigDoFit.connect(
#             lambda x: self._logic.do_fit(x, True)
#         )
#
#         # connect update signals from logic
#         self._logic.sigTTFileNameUpdated.connect(self.update_tt_filename, QtCore.Qt.QueuedConnection)
#         self._logic.sigFitUpdated.connect(self.fit_data_updated)
#
#         # connect internal signals
#         self.tta_filename_LineEdit.editingFinished.connect(self.tt_filename_changed)
#         self.tta_browsefile_PushButton.clicked.connect(self.browse_file)
#         self.tta_loadfile_PushButton.clicked.connect(self.load_time_trace)
#
#     def disconnect_signals(self):
#         self.tta_method_comboBox.currentTextChanged.disconnect()
#         self.tta_setting_comboBox.currentTextChanged.disconnect()
#         self.tta_setting_add_pushButton.clicked.disconnect()
#         self.tta_setting_delete_pushButton.clicked.disconnect()
#         self.sigTTFileNameChanged.disconnect()
#         self.sigLoadTT.disconnect()
#         self.plot1_fitwidget.sigDoFit.disconnect()
#         self.plot2_fitwidget.sigDoFit.disconnect()
#
#         self._logic.sigTTFileNameUpdated.disconnect()
#         self._logic.sigFitUpdated.disconnect()
#
#         self.tta_filename_LineEdit.editingFinished.disconnect()
#         self.tta_browsefile_PushButton.clicked.disconnect()
#         self.tta_loadfile_PushButton.clicked.disconnect()
#
#     def update_current_method(self):
#         self.settings.current_method = self.tta_method_comboBox.currentText()
#
#     def update_current_setting(self):
#         self.settings.current_stg_name = self.tta_setting_comboBox.currentText()
#         current_setting = copy.deepcopy(self.settings.current_setting)
#         self.tta_settings_widget.update_data(current_setting)
#
#     def add_setting(self):
#         self.settings.add_setting()
#         self.tta_setting_comboBox.addItem(self.settings.current_stg_name)
#         self.tta_setting_comboBox.setCurrentText(self.settings.current_stg_name)
#
#     def delete_setting(self):
#         self.settings.current_stg_name = self.tta_setting_comboBox.currentText()
#         current_index = self.tta_setting_comboBox.currentIndex()
#         self.settings.remove_setting()
#         self.tta_setting_comboBox.removeItem(current_index)
#
#     @QtCore.Slot()
#     def tt_filename_changed(self):
#         self.sigTTFileNameChanged.emit(self.tta_filename_LineEdit.text())
#         return
#
#     @QtCore.Slot(str)
#     def update_tt_filename(self, name):
#         if name is None:
#             name = ''
#         self.tta_filename_LineEdit.blockSignals(True)
#         self.tta_filename_LineEdit.setText(name)
#         self.tta_filename_LineEdit.blockSignals(False)
#         return
#
#     @QtCore.Slot()
#     def browse_file(self):
#         """ Browse a saved time trace from file."""
#         this_file = QtWidgets.QFileDialog.getOpenFileName(self._gui._mainw,
#                                                           'Open time trace file',
#                                                           self._logic.module_default_data_dir,
#                                                           'Data files (*.npz)')[0]
#         if this_file:
#             self.sigTTFileNameChanged.emit(this_file)
#         return
#
#     @QtCore.Slot()
#     def load_time_trace(self):
#         self.sigLoadTT.emit()
#         return
#
#     @QtCore.Slot(str, object, bool)
#     def fit_data_updated(self, fit_config, result, use_alternative_data):
#         """
#
#         @param str fit_config:
#         @param object result:
#         @param bool use_alternative_data:
#         @return:
#         """
#         plot1_fitwidget
#         # Update plot.
#         if use_alternative_data:
#             if not fit_config or fit_config == 'No Fit':
#                 if self.second_fit_image in self._pa.pulse_analysis_second_PlotWidget.items():
#                     self._pa.pulse_analysis_second_PlotWidget.removeItem(self.second_fit_image)
#             else:
#                 self.second_fit_image.setData(x=result.high_res_best_fit[0],
#                                               y=result.high_res_best_fit[1])
#                 if self.second_fit_image not in self._pa.pulse_analysis_second_PlotWidget.items():
#                     self._pa.pulse_analysis_second_PlotWidget.addItem(self.second_fit_image)
#         else:
#             if not fit_config or fit_config == 'No Fit':
#                 if self.fit_image in self._pa.pulse_analysis_PlotWidget.items():
#                     self._pa.pulse_analysis_PlotWidget.removeItem(self.fit_image)
#             else:
#                 self.fit_image.setData(x=result.high_res_best_fit[0],
#                                        y=result.high_res_best_fit[1])
#                 if self.fit_image not in self._pa.pulse_analysis_PlotWidget.items():
#                     self._pa.pulse_analysis_PlotWidget.addItem(self.fit_image)
#         return
