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
import os
import numpy as np
import pyqtgraph as pg
from PySide2 import QtWidgets, QtCore

from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget

class StateEstimationTab(QtWidgets.QWidget):

    def __init__(self, logic):
        super().__init__()
        self._instantiate_widgets(logic)
        self._form_layout()

    def _instantiate_widgets(self, logic):
        self._sew_layout = QtWidgets.QVBoxLayout(self)
        self._sw = StateEstimationSettingWidget(logic)
        self._pw = StateEstimationPulseWidget(logic)
        self._ttw = StateEstimationTimeTraceWidget(logic)
        self._sew_layout.addWidget(self._sw)
        self._sew_layout.addWidget(self._pw)
        self._sew_layout.addWidget(self._ttw)

    def _form_layout(self):
        self._sw.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._pw.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._ttw.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self._sew_layout.setStretchFactor(self._sw, 1)
        self._sew_layout.setStretchFactor(self._pw, 3)
        self._sew_layout.setStretchFactor(self._ttw, 3)

    def connect_signals(self):
        self._sw.connect_signals()
        self._pw.connect_signals()
        self._ttw.connect_signals()
        self.connect_mutual_signals()
        self._pw.update_lines()

        self._sw.method_updated_sig.connect(self.reconnect_mutual_signals)
        self._sw.method_updated_sig.connect(self._pw.toggle_lines)
        self._sw.setting_name_updated_sig.connect(self.reconnect_mutual_signals)
        self._sw.setting_name_updated_sig.connect(self._pw.update_lines)

    def connect_mutual_signals(self):
        param_names = self._sw.settings.current_setting.__annotations__
        if 'sig_start' in param_names:
            self._sw.se_settings_widget.widgets['sig_start'].valueChanged.connect(self._pw.update_lines)
        if 'sig_end' in param_names:
            self._sw.se_settings_widget.widgets['sig_end'].valueChanged.connect(self._pw.update_lines)
        if 'ref_start' in param_names:
            self._sw.se_settings_widget.widgets['ref_start'].valueChanged.connect(self._pw.update_lines)
        if 'ref_end' in param_names:
            self._sw.se_settings_widget.widgets['ref_end'].valueChanged.connect(self._pw.update_lines)

        self._pw.sig_line_changed_sig.connect(self._sw.update_from_sig_lines)
        self._pw.ref_line_changed_sig.connect(self._sw.update_from_ref_lines)

    def disconnect_mutual_signals(self):
        param_names = self._sw.settings.current_setting.__annotations__
        if 'sig_start' in param_names:
            self._sw.se_settings_widget.widgets['sig_start'].valueChanged.disconnect()
        if 'sig_end' in param_names:
            self._sw.se_settings_widget.widgets['sig_end'].valueChanged.disconnect()
        if 'ref_start' in param_names:
            self._sw.se_settings_widget.widgets['ref_start'].valueChanged.disconnect()
        if 'ref_end' in param_names:
            self._sw.se_settings_widget.widgets['ref_end'].valueChanged.disconnect()

        self._pw.sig_line_changed_sig.disconnect()
        self._pw.ref_line_changed_sig.disconnect()

    def reconnect_mutual_signals(self):
        self.connect_mutual_signals()

    def disconnect_signals(self):
        self._sw.disconnect_signals()
        self._pw.disconnect_signals()
        self._ttw.disconnect_signals()

    def activate_ui(self):
        self._sw.activate()
        self._pw.activate()
        self._ttw.activate()

    def deactivate_ui(self):
        self._sw.deactivate()
        self._pw.deactivate()
        self._ttw.deactivate()

class StateEstimationSettingWidget(QtWidgets.QWidget):
    method_updated_sig = QtCore.Signal()
    setting_name_updated_sig = QtCore.Signal()
    setting_widget_updated_sig = QtCore.Signal()

    def __init__(self, logic):
        self.logic = logic()
        self.estimator = logic().estimator
        self.settings = logic().settings.estimator_stg
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\state_estimation_setting_widget.ui')

        # Load it
        super(StateEstimationSettingWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._activate_widgets()

    def _activate_widgets(self):
        self.se_method_comboBox.addItems(self.estimator.method_lists)
        self.se_method_comboBox.setCurrentText(self.settings.current_method)
        self.se_setting_comboBox.addItems(self.settings.current_setting_list)
        self.se_setting_comboBox.setCurrentText(self.settings.current_stg_name)

        self.se_settings_widget = DataclassWidget(self.settings.current_setting)
        self.se_settings_gridLayout.addWidget(self.se_settings_widget)

    def deactivate(self):
        pass

    def connect_signals(self):
        self.se_method_comboBox.currentTextChanged.connect(self.update_current_method)
        self.se_setting_comboBox.currentTextChanged.connect(self.update_current_setting)
        self.se_setting_add_pushButton.clicked.connect(self.add_setting)
        self.se_setting_delete_pushButton.clicked.connect(self.delete_setting)


#        self.settings.current_stg_changed_sig.connect(self.update_current_setting)


    def disconnect_signals(self):
        self.se_method_comboBox.currentTextChanged.disconnect()
        self.se_setting_comboBox.currentTextChanged.disconnect()
        self.se_setting_add_pushButton.clicked.disconnect()
        self.se_setting_delete_pushButton.clicked.disconnect()

    def update_current_method(self):
        self.settings.current_method = self.se_method_comboBox.currentText()
        self.se_setting_comboBox.blockSignals(True)
        self.se_setting_comboBox.clear()
        self.se_setting_comboBox.blockSignals(False)
        self.se_setting_comboBox.addItems(self.settings.current_setting_list)
        self.settings.current_stg_name = 'default'
        self.se_setting_comboBox.setCurrentText(self.settings.current_stg_name)

    def update_current_setting(self):
        self.settings.current_stg_name = self.se_setting_comboBox.currentText()
        self.update_widget()
        self.setting_name_updated_sig.emit()

    def update_widget(self):
        self.se_settings_widget.update_data(self.settings.current_setting)
        self.setting_widget_updated_sig.emit()

    def add_setting(self):
        self.settings.add_setting()
        self.se_setting_comboBox.addItem(self.settings.current_stg_name)
        self.se_setting_comboBox.setCurrentText(self.settings.current_stg_name)

    def delete_setting(self):
        if self.settings.current_stg_name == 'default':
            return
        self.settings.current_stg_name = self.se_setting_comboBox.currentText()
        current_index = self.se_setting_comboBox.currentIndex()
        self.settings.remove_setting()
        self.se_setting_comboBox.removeItem(current_index)
#        self.update_widget()

    @QtCore.Slot(float, float)
    def update_from_sig_lines(self, sig_start, sig_end):
        param_names = self.settings.current_setting.__annotations__
        if 'sig_start' in param_names:
            self.settings.current_setting.sig_start = sig_start
            self.se_settings_widget.widgets['sig_start'].setValue(sig_start)
        if 'sig_end' in param_names:
            self.settings.current_setting.sig_end = sig_end
            self.se_settings_widget.widgets['sig_end'].setValue(sig_end)

    @QtCore.Slot(float, float)
    def update_from_ref_lines(self, ref_start, ref_end):
        param_names = self.settings.current_setting.__annotations__
        if 'ref_start' in param_names:
            self.settings.current_setting.ref_start = ref_start
            self.se_settings_widget.widgets['ref_start'].setValue(ref_start)

        if 'ref_end' in param_names:
            self.settings.current_setting.ref_end = ref_end
            self.se_settings_widget.widgets['ref_end'].setValue(ref_end)


class StateEstimationPulseWidget(QtWidgets.QWidget):
    sig_line_changed_sig = QtCore.Signal(float, float)
    ref_line_changed_sig = QtCore.Signal(float, float)

    def __init__(self, logic):
        self.logic = logic()
        self.estimator = logic().estimator
        self.settings = logic().settings.estimator_stg
        self.sig_start = 0
        self.sig_end = 0
        self.ref_start = 0
        self.ref_end = 0
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\state_estimation_pulse_widget.ui')

        # Load it
        super(StateEstimationPulseWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._activate_widgets()

    def _activate_widgets(self):
        self.sig_start_line = pg.InfiniteLine(pos=0,
                                              pen={'color': palette.c3, 'width': 1},
                                              movable=True)
        self.sig_end_line = pg.InfiniteLine(pos=0,
                                            pen={'color': palette.c3, 'width': 1},
                                            movable=True)
        self.ref_start_line = pg.InfiniteLine(pos=0,
                                              pen={'color': palette.c4, 'width': 1},
                                              movable=True)
        self.ref_end_line = pg.InfiniteLine(pos=0,
                                            pen={'color': palette.c4, 'width': 1},
                                            movable=True)
        self.pulse_image = pg.PlotDataItem(np.arange(10), np.zeros(10), pen=palette.c1)


        self.pulse_PlotWidget.addItem(self.pulse_image)
        self.pulse_PlotWidget.addItem(self.sig_start_line)
        self.pulse_PlotWidget.addItem(self.sig_end_line)
        self.pulse_PlotWidget.addItem(self.ref_start_line)
        self.pulse_PlotWidget.addItem(self.ref_end_line)
        self.pulse_PlotWidget.setLabel(axis='bottom', text='time', units='s')
        self.pulse_PlotWidget.setLabel(axis='left', text='events', units='#')

        self.toggle_lines()

    def deactivate(self):
        pass

    def connect_signals(self):
        self.sig_start_line.sigPositionChangeFinished.connect(self.sig_lines_dragged)
        self.sig_end_line.sigPositionChangeFinished.connect(self.sig_lines_dragged)
        self.ref_start_line.sigPositionChangeFinished.connect(self.ref_lines_dragged)
        self.ref_end_line.sigPositionChangeFinished.connect(self.ref_lines_dragged)
        self.update_pushButton.clicked.connect(self.update_pulse)

#        self.settings.current_stg_changed_sig.connect(self.update_lines)
    def disconnect_signals(self):
        self.sig_start_line.sigPositionChangeFinished.disconnect()
        self.sig_end_line.sigPositionChangeFinished.disconnect()
        self.ref_start_line.sigPositionChangeFinished.disconnect()
        self.ref_end_line.sigPositionChangeFinished.disconnect()

    def toggle_lines(self):
        param_names = self.settings.current_setting.__annotations__
        self.sig_start_line.setVisible('sig_start' in param_names)
        self.sig_end_line.setVisible('sig_end' in param_names)
        self.ref_start_line.setVisible('ref_start' in param_names)
        self.ref_end_line.setVisible('ref_end' in param_names)

    def sig_lines_dragged(self):
        sig_start = self.sig_start_line.value()
        sig_end = self.sig_end_line.value()
        self.settings.current_setting.sig_start = sig_start if sig_start <= sig_end else sig_end
        self.settings.current_setting.sig_end = sig_end if sig_end >= sig_start else sig_start
        self.sig_line_changed_sig.emit(sig_start, sig_end)

    def ref_lines_dragged(self):
        ref_start = self.ref_start_line.value()
        ref_end = self.ref_end_line.value()
        self.settings.current_setting.ref_start = ref_start if ref_start <= ref_end else ref_end
        self.settings.current_setting.ref_end = ref_end if ref_end >= ref_start else ref_start
        self.ref_line_changed_sig.emit(ref_start, ref_end)

    def update_lines(self):
        param_names = self.settings.current_setting.__annotations__
        if 'sig_start' in param_names:
            self.sig_start_line.setValue(self.settings.current_setting.sig_start)
        if 'sig_end' in param_names:
            self.sig_end_line.setValue(self.settings.current_setting.sig_end)
        if 'ref_start' in param_names:
            self.ref_start_line.setValue(self.settings.current_setting.ref_start)
        if 'ref_end' in param_names:
            self.ref_end_line.setValue(self.settings.current_setting.ref_end)

    def update_pulse(self):
        pulse = self.logic.get_pulse()
        self.pulse_image.setData(x=pulse[0], y=pulse[1])
        self.pulse_PlotWidget.addItem(self.pulse_image)

class StateEstimationTimeTraceWidget(QtWidgets.QWidget):

    def __init__(self, logic):
        self.logic = logic()
        self.estimator = logic().estimator
        self.settings = logic().settings.estimator_stg
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\state_estimation_time_trace_widget.ui')

        # Load it
        super(StateEstimationTimeTraceWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._activate_widgets()

    def _activate_widgets(self):
        self.time_trace_image = pg.PlotDataItem(np.arange(10), np.zeros(10), pen=palette.c1)
        self.time_trace_PlotWidget.addItem(self.time_trace_image)

    def deactivate(self):
        pass
    def connect_signals(self):
        self.get_time_trace_pushButton.clicked.connect(self.update_time_trace)

    def disconnect_signals(self):
        self.get_time_trace_pushButton.clicked.disconnect()

    def update_time_trace(self):
        self.logic.extract_data()
        self.logic.estimate_state()
        y = self.logic.data.time_trace
        x = np.arange(len(y))
        self.time_trace_image.setData(x=x, y=y)