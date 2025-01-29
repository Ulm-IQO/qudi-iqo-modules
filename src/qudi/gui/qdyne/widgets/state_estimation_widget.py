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
from logging import getLogger
import os
import numpy as np
import pyqtgraph as pg
from PySide2 import QtWidgets, QtCore

from qudi.core.logger import get_logger
from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.widgets.dataclass_widget import DataclassWidget
from qudi.gui.qdyne.widgets.settings_widget import SettingsWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class StateEstimationTab(QtWidgets.QWidget):
    def __init__(self, logic):
        super().__init__()
        self._log = get_logger(__name__)
        self._logic = logic
        self._instantiate_widgets(logic)
        self._form_layout()

    def _instantiate_widgets(self, logic):
        self._sew_layout = QtWidgets.QVBoxLayout(self)
        self._sw = StateEstimationSettingsWidget(logic().settings.estimator_stg,
                                                 logic().estimator.method_list,
                                                 self._invoke_func)
        self._pw = StateEstimationPulseWidget(logic)
        self._ttw = StateEstimationTimeTraceWidget(logic)
        self._analysis_interval_spinbox = ScienDSpinBox()
        self._analysis_interval_spinbox.setSuffix("s")
        self._analysis_interval_spinbox.setMinimum(0)
        self._analysis_interval_spinbox.setValue(self._logic().measure.analysis_timer_interval)
        self._analysis_interval_label = QtWidgets.QLabel("Analysis interval")
        self._sew_layout.addWidget(self._sw)
        self._sew_layout.addWidget(self._pw)
        self._sew_layout.addWidget(self._ttw)
        self._sew_layout.addWidget(self._analysis_interval_label)
        self._sew_layout.addWidget(self._analysis_interval_spinbox)

    def _invoke_func(self):
        """
        functions invoked after the values of the settings changed.
        """
        self._logic().settings.estimator_stg.sync_param_dict()
        self._pw.update_lines()

    def _form_layout(self):
        self._sw.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self._pw.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self._ttw.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

        self._sew_layout.setStretchFactor(self._sw, 1)
        self._sew_layout.setStretchFactor(self._pw, 3)
        self._sew_layout.setStretchFactor(self._ttw, 3)

    def connect_signals(self):
        self._sw.connect_signals()
        self._pw.connect_signals()
        self._ttw.connect_signals()
        self._analysis_interval_spinbox.editingFinished.connect(self.analysis_timer_interval)
        self._logic().measure.sigTimerIntervalUpdated.connect(self._analysis_interval_spinbox.setValue)
        self.connect_mutual_signals()
        self._pw.update_lines()

        self._sw.method_updated_sig.connect(self.reconnect_mutual_signals)
        self._sw.method_updated_sig.connect(self._pw.toggle_lines)
        self._sw.setting_name_updated_sig.connect(self.reconnect_mutual_signals)
        self._sw.setting_name_updated_sig.connect(self._pw.update_lines)

    def connect_mutual_signals(self):
        param_names = self._sw.settings.current_setting.__annotations__
        if "sig_start" in param_names:
            self._sw.settings_widget.widgets["sig_start"].valueChanged.connect(
                self._pw.update_lines
            )
        if "sig_end" in param_names:
            self._sw.settings_widget.widgets["sig_end"].valueChanged.connect(
                self._pw.update_lines
            )
        if "ref_start" in param_names:
            self._sw.settings_widget.widgets["ref_start"].valueChanged.connect(
                self._pw.update_lines
            )
        if "ref_end" in param_names:
            self._sw.settings_widget.widgets["ref_end"].valueChanged.connect(
                self._pw.update_lines
            )

        self._pw.sig_line_changed_sig.connect(self._sw.update_from_sig_lines)
        self._pw.ref_line_changed_sig.connect(self._sw.update_from_ref_lines)

    def disconnect_mutual_signals(self):
        param_names = self._sw.settings.current_setting.__annotations__
        if "sig_start" in param_names:
            self._sw.settings_widget.widgets["sig_start"].valueChanged.disconnect()
        if "sig_end" in param_names:
            self._sw.settings_widget.widgets["sig_end"].valueChanged.disconnect()
        if "ref_start" in param_names:
            self._sw.settings_widget.widgets["ref_start"].valueChanged.disconnect()
        if "ref_end" in param_names:
            self._sw.settings_widget.widgets["ref_end"].valueChanged.disconnect()

        self._pw.sig_line_changed_sig.disconnect()
        self._pw.ref_line_changed_sig.disconnect()

    def reconnect_mutual_signals(self):
        self.connect_mutual_signals()

    def disconnect_signals(self):
        self._sw.disconnect_signals()
        self._pw.disconnect_signals()
        self._ttw.disconnect_signals()
        self._analysis_interval_spinbox.editingFinished.disconnect(self.analysis_timer_interval)
        self._logic().measure.sigTimerIntervalUpdated.disconnect(self._analysis_interval_spinbox.setValue)

    def activate_ui(self):
        self._sw.activate()
        self._pw.activate()
        self._ttw.activate()

    def deactivate_ui(self):
        self._sw.deactivate()
        self._pw.deactivate()
        self._ttw.deactivate()


    def analysis_timer_interval(self):
        self._logic().measure.analysis_timer_interval = self._analysis_interval_spinbox.value()


class StateEstimationSettingsWidget(SettingsWidget):
    def __init__(self, settings, method_list, invoke_func=None):
        super(StateEstimationSettingsWidget, self).__init__(settings, method_list, invoke_func)

    @QtCore.Slot(float, float)
    def update_from_sig_lines(self, sig_start, sig_end):
        param_names = self.settings.current_setting.__annotations__
        if "sig_start" in param_names:
            self.settings.current_setting.sig_start = sig_start
            self.settings_widget.widgets["sig_start"].setValue(sig_start)
        if "sig_end" in param_names:
            self.settings.current_setting.sig_end = sig_end
            self.settings_widget.widgets["sig_end"].setValue(sig_end)

    @QtCore.Slot(float, float)
    def update_from_ref_lines(self, ref_start, ref_end):
        param_names = self.settings.current_setting.__annotations__
        if "ref_start" in param_names:
            self.settings.current_setting.ref_start = ref_start
            self.settings_widget.widgets["ref_start"].setValue(ref_start)

        if "ref_end" in param_names:
            self.settings.current_setting.ref_end = ref_end
            self.settings_widget.widgets["ref_end"].setValue(ref_end)


class StateEstimationPulseWidget(QtWidgets.QWidget):
    sig_line_changed_sig = QtCore.Signal(float, float)
    ref_line_changed_sig = QtCore.Signal(float, float)

    def __init__(self, logic):
        self._logic = logic()
        self._log = getLogger(__name__)
        self.estimator = logic().estimator
        self.settings = logic().settings.estimator_stg
        self.sig_start = 0
        self.sig_end = 0
        self.ref_start = 0
        self.ref_end = 0
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, "ui", "state_estimation_pulse_widget.ui")

        # Load it
        super(StateEstimationPulseWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._activate_widgets()

    def _activate_widgets(self):
        self.sig_start_line = pg.InfiniteLine(
            pos=0, pen={"color": palette.c3, "width": 1}, movable=True
        )
        self.sig_end_line = pg.InfiniteLine(
            pos=0, pen={"color": palette.c3, "width": 1}, movable=True
        )
        self.ref_start_line = pg.InfiniteLine(
            pos=0, pen={"color": palette.c4, "width": 1}, movable=True
        )
        self.ref_end_line = pg.InfiniteLine(
            pos=0, pen={"color": palette.c4, "width": 1}, movable=True
        )
        self.pulse_image = pg.PlotDataItem(np.arange(10), np.zeros(10), pen=palette.c1)

        self.pulse_PlotWidget.addItem(self.pulse_image)
        self.pulse_PlotWidget.addItem(self.sig_start_line)
        self.pulse_PlotWidget.addItem(self.sig_end_line)
        self.pulse_PlotWidget.addItem(self.ref_start_line)
        self.pulse_PlotWidget.addItem(self.ref_end_line)
        self.pulse_PlotWidget.setLabel(axis="bottom", text="time", units="s")
        self.pulse_PlotWidget.setLabel(axis="left", text="events", units="#")

        self.toggle_lines()

    def deactivate(self):
        self.close()

    def connect_signals(self):
        self.sig_start_line.sigPositionChangeFinished.connect(self.sig_lines_dragged)
        self.sig_end_line.sigPositionChangeFinished.connect(self.sig_lines_dragged)
        self.ref_start_line.sigPositionChangeFinished.connect(self.ref_lines_dragged)
        self.ref_end_line.sigPositionChangeFinished.connect(self.ref_lines_dragged)
        self.update_pushButton.clicked.connect(self.update_pulse)
        # Connect update signals from qdyne_measurement_logic
        self._logic.measure.sigPulseDataUpdated.connect(self.pulse_updated)
        # Connect to measurement state changes
        self._logic.measure.sigMeasurementStarted.connect(lambda: self.set_lines_movable(False))
        self._logic.measure.sigMeasurementStopped.connect(lambda: self.set_lines_movable(True))

    #        self.settings.current_stg_changed_sig.connect(self.update_lines)
    def disconnect_signals(self):
        self.sig_start_line.sigPositionChangeFinished.disconnect()
        self.sig_end_line.sigPositionChangeFinished.disconnect()
        self.ref_start_line.sigPositionChangeFinished.disconnect()
        self.ref_end_line.sigPositionChangeFinished.disconnect()
        self._logic.measure.sigPulseDataUpdated.disconnect()

    def toggle_lines(self):
        param_names = self.settings.current_setting.__annotations__
        self.sig_start_line.setVisible("sig_start" in param_names)
        self.sig_end_line.setVisible("sig_end" in param_names)
        self.ref_start_line.setVisible("ref_start" in param_names)
        self.ref_end_line.setVisible("ref_end" in param_names)

    def sig_lines_dragged(self):
        sig_start = self.sig_start_line.value()
        sig_end = self.sig_end_line.value()
        self.settings.current_setting.sig_start = (
            sig_start if sig_start <= sig_end else sig_end
        )
        self.settings.current_setting.sig_end = (
            sig_end if sig_end >= sig_start else sig_start
        )
        self.sig_line_changed_sig.emit(sig_start, sig_end)

    def ref_lines_dragged(self):
        ref_start = self.ref_start_line.value()
        ref_end = self.ref_end_line.value()
        self.settings.current_setting.ref_start = (
            ref_start if ref_start <= ref_end else ref_end
        )
        self.settings.current_setting.ref_end = (
            ref_end if ref_end >= ref_start else ref_start
        )
        self.ref_line_changed_sig.emit(ref_start, ref_end)

    def update_lines(self):
        param_names = self.settings.current_setting.__annotations__
        if "sig_start" in param_names:
            self.sig_start_line.setValue(self.settings.current_setting.sig_start)
        if "sig_end" in param_names:
            self.sig_end_line.setValue(self.settings.current_setting.sig_end)
        if "ref_start" in param_names:
            self.ref_start_line.setValue(self.settings.current_setting.ref_start)
        if "ref_end" in param_names:
            self.ref_end_line.setValue(self.settings.current_setting.ref_end)

    def update_pulse(self):
        self._logic.measure.get_raw_data()
        self._logic.measure.get_pulse()
        self._logic.measure.sigPulseDataUpdated.emit()
        self._logic.measure.extract_data()
        self._logic.measure.estimate_state()
        self._logic.measure.sigTimeTraceDataUpdated.emit()

    def pulse_updated(self):
        pulse = self._logic.data.pulse_data
        self._log.warning(f"{pulse=}")
        self.pulse_image.setData(x=pulse[0], y=pulse[1])

    def set_lines_movable(self, movable):
        """
        Enable or disable the ability to move the lines.

        :param bool movable: If True, lines can be moved. If False, they are locked in place.
        """
        self.sig_start_line.setMovable(movable)
        self.sig_end_line.setMovable(movable)
        self.ref_start_line.setMovable(movable)
        self.ref_end_line.setMovable(movable)
class StateEstimationTimeTraceWidget(QtWidgets.QWidget):
    _log = get_logger(__name__)

    def __init__(self, logic):
        self._logic = logic()
        self.estimator = logic().estimator
        self.settings = logic().settings.estimator_stg
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, "ui", "state_estimation_time_trace_widget.ui")

        # Load it
        super(StateEstimationTimeTraceWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        self._activate_widgets()

    def _activate_widgets(self):
        self.time_trace_image = pg.PlotDataItem(
            np.arange(10), np.zeros(10), pen=palette.c1
        )
        self.time_trace_PlotWidget.addItem(self.time_trace_image)
        self.time_trace_PlotWidget.setLabel(axis="top", text="readouts", units="#")
        self.time_trace_PlotWidget.setLabel(axis="bottom", text="time", units="s")
        self.time_trace_PlotWidget.setLabel(axis="left", text="signal", units="")

    def deactivate(self):
        self.close()

    def connect_signals(self):
        self.get_time_trace_pushButton.clicked.connect(self.update_time_trace)
        # Connect update signals from qdyne_measurement_logic
        self._logic.measure.sigTimeTraceDataUpdated.connect(self.time_trace_updated)

    def disconnect_signals(self):
        self.get_time_trace_pushButton.clicked.disconnect()
        self._logic.measure.sigTimeTraceDataUpdated.disconnect()

    def update_time_trace(self):
        self._logic.measure.get_raw_data()
        self._logic.measure.get_pulse()
        self._logic.measure.sigPulseDataUpdated.emit()
        self._logic.measure.extract_data()
        self._logic.measure.estimate_state()
        self._logic.measure.sigTimeTraceDataUpdated.emit()

    def time_trace_updated(self):
        y = self._logic.data.time_trace
        time_between_readouts = (
            self._logic.pulsedmasterlogic()
            .sequencegeneratorlogic()
            .get_ensemble_info(
                self._logic.pulsedmasterlogic().sequencegeneratorlogic().loaded_asset[0]
            )[0]
        )
        self.time_trace_PlotWidget.setLabel(axis="bottom", text="time", units="s")
        if time_between_readouts == 0:
            time_between_readouts = 1
            self._log.warn(
                "Time between readouts could not be determined from loaded pulse sequence. Make sure a pulse sequence is loaded. Switching to number of readouts as x axis."
            )
            self.time_trace_PlotWidget.setLabel(
                axis="bottom", text="readouts", units="#"
            )
        x = np.arange(len(y)) * time_between_readouts
        self.time_trace_image.setData(x=x, y=y)
