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

from logging import getLogger
import os
import numpy as np
import pyqtgraph as pg
from PySide2.QtCore import Signal, Slot
from PySide2.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy

from qudi.core.logger import get_logger
from qudi.util import uic
from qudi.util.colordefs import QudiPalettePale as palette

from qudi.gui.qdyne.tools.multi_settings_widget import MultiSettingsWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox


class StateEstimationTab(QWidget):
    def __init__(self, logic):
        super().__init__()
        self._log = get_logger(__name__)
        self._logic = logic
        self._instantiate_widgets(logic)
        self._form_layout()

    def _instantiate_widgets(self, logic):
        self._sew_layout = QVBoxLayout(self)
        self._settings_widget = StateEstimationSettingsWidget(logic().settings.estimator_stg.estimator_mediator)
        self._pulse_widget = StateEstimationPulseWidget()
        self._time_trace_widget = StateEstimationTimeTraceWidget()

        self._analysis_interval_spinbox = ScienDSpinBox()
        self._analysis_interval_spinbox.setSuffix("s")
        self._analysis_interval_spinbox.setMinimum(0)
        self._analysis_interval_spinbox.setValue(self._logic().measure.analysis_timer_interval)
        self._analysis_interval_label = QLabel("Analysis interval")

        self._sew_layout.addWidget(self._settings_widget)
        self._sew_layout.addWidget(self._pulse_widget)
        self._sew_layout.addWidget(self._time_trace_widget)
        self._sew_layout.addWidget(self._analysis_interval_label)
        self._sew_layout.addWidget(self._analysis_interval_spinbox)

    def _form_layout(self):
        self._settings_widget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._pulse_widget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._time_trace_widget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self._sew_layout.setStretchFactor(self._settings_widget, 1)
        self._sew_layout.setStretchFactor(self._pulse_widget, 3)
        self._sew_layout.setStretchFactor(self._time_trace_widget, 3)

    def connect_signals(self):
        self._settings_widget.connect_signals()
        self._pulse_widget.connect_signals()

        self._connect_settings_widget_signals()
        self._connect_pulse_widget_signals()
        self._connect_measurement_signals(self._logic().measure)

        self._analysis_interval_spinbox.editingFinished.connect(self.analysis_timer_interval)
        self._logic().measure.sigTimerIntervalUpdated.connect(self._analysis_interval_spinbox.setValue)
        self.connect_mutual_signals()
        self._pulse_widget.update_lines()

    def _connect_settings_widget_signals(self):
        self._settings_widget.data_widget_updated_sig.connect(self._pulse_widget.toggle_lines)
        self._settings_widget.data_widget_updated_sig.connect(self._pulse_widget.update_lines)

    def _connect_pulse_widget_signals(self):
        self._pulse_widget.sig_line_changed_sig.connect(self._settings_widget.set_data_from_dict)
        self._pulse_widget.ref_line_changed_sig.connect(self._settings_widget.set_data_from_dict)

    def _connect_measurement_signals(self, measurement_logic):
        measurement_logic.sigPulseDataUpdated.connect(self._pulse_widget.pulse_updated)
        measurement_logic.sigMeasurementStarted.connect(lambda: self._pulse_widget.set_lines_movable(False))
        measurement_logic.sigMeasurementStopped.connect(lambda: self._pulse_widget.set_lines_movable(True))

        measurement_logic.sigTimeTraceDataUpdated.connect(self._time_trace_widget.update_time_trace_image)

    def disconnect_signals(self):
        self._settings_widget.disconnect_signals()
        self._pulse_widget.disconnect_signals()

        self._disconnect_settings_widget_signals()
        self._disconnect_pulse_widget_signals()
        self._disconnect_measurement_signals(self._logic().measure)

        self._analysis_interval_spinbox.editingFinished.disconnect(self.analysis_timer_interval)
        self._logic().measure.sigTimerIntervalUpdated.disconnect(self._analysis_interval_spinbox.setValue)

    def _disconnect_settings_widget_signals(self):
        self._settings_widget.data_widget_updated_sig.disconnect()

    def _disconnect_pulse_widget_signals(self):
        self._pulse_widget.sig_line_changed_sig.disconnect()
        self._pulse_widget.ref_line_changed_sig.disconnect()

    def _disconnect_measurement_signals(self, measurement_logic):
        measurement_logic.sigPulseDataUpdated.disconnect()
        measurement_logic.sigMeasurementStarted.disconnect()
        measurement_logic.sigMeasurementStopped.disconnect()

        measurement_logic.sigTimeTraceDataUpdated.disconnect()

    def activate_ui(self):
        self._settings_widget.activate()
        self._pulse_widget.activate()
        self._time_trace_widget.activate()

    def deactivate_ui(self):
        self._settings_widget.deactivate()
        self._pulse_widget.deactivate()
        self._time_trace_widget.deactivate()

    def analysis_timer_interval(self):
        self._logic().measure.analysis_timer_interval = self._analysis_interval_spinbox.value()


class StateEstimationSettingsWidget(MultiSettingsWidget):
    def __init__(self, estimator_settings_mediator):
        super(MultiSettingsWidget, self).__init__(estimator_settings_mediator)


class StateEstimationPulseWidget(QWidget):
    """
    Widget to confirm the shape of pulse data. A set of start line and end line is provided for signal and reference.
    They are used to change the dataclass widgets.
    Communication with dataclass mediators should be done solely by the dataclass widgets.
    """
    sig_line_changed_sig = Signal(float, float)
    ref_line_changed_sig = Signal(float, float)

    def __init__(self):
        self._log = getLogger(__name__)

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
        """
        connect internal signals from lines.
        """
        self.sig_start_line.sigPositionChangeFinished.connect(self.sig_lines_dragged)
        self.sig_end_line.sigPositionChangeFinished.connect(self.sig_lines_dragged)
        self.ref_start_line.sigPositionChangeFinished.connect(self.ref_lines_dragged)
        self.ref_end_line.sigPositionChangeFinished.connect(self.ref_lines_dragged)

    def disconnect_signals(self):
        """
        disconnect internal signals from lines.
        """
        self.sig_start_line.sigPositionChangeFinished.disconnect()
        self.sig_end_line.sigPositionChangeFinished.disconnect()
        self.ref_start_line.sigPositionChangeFinished.disconnect()
        self.ref_end_line.sigPositionChangeFinished.disconnect()

    @Slot(dict)
    def toggle_lines(self, current_values_dict):
        """
        toggle lines based on the current values
        """
        self.sig_start_line.setVisible("sig_start" in current_values_dict)
        self.sig_end_line.setVisible("sig_end" in current_values_dict)
        self.ref_start_line.setVisible("ref_start" in current_values_dict)
        self.ref_end_line.setVisible("ref_end" in current_values_dict)

    def sig_lines_dragged(self):
        sig_start = self.sig_start_line.value()
        sig_end = self.sig_end_line.value()

        sig_start = (sig_start if sig_start <= sig_end else sig_end)
        sig_end = (sig_end if sig_end >= sig_start else sig_start)

        update_dict = dict()
        update_dict["sig_start"] = sig_start
        update_dict["sig_end"] = sig_end
        self.sig_line_changed_sig.emit(update_dict)

    def ref_lines_dragged(self):
        ref_start = self.ref_start_line.value()
        ref_end = self.ref_end_line.value()

        ref_start = (ref_start if ref_start <= ref_end else ref_end)
        ref_end = (ref_end if ref_end >= ref_start else ref_start)

        update_dict = dict()
        update_dict["ref_start"] = ref_start
        update_dict["ref_end"] = ref_end
        self.ref_line_changed_sig.emit(update_dict)

    @Slot(dict)
    def update_lines(self, data_dict):
        if "sig_start" in data_dict:
            self.sig_start_line.setValue(data_dict["sig_start"])
        if "sig_end" in data_dict:
            self.sig_end_line.setValue(data_dict["sig_end"])
        if "ref_start" in data_dict:
            self.ref_start_line.setValue(data_dict["ref_start"])
        if "ref_end" in data_dict:
            self.ref_end_line.setValue(data_dict["ref_end"])

    @Slot()
    def pulse_updated(self, pulse_data):
        self.pulse_image.setData(x=pulse_data[0], y=pulse_data[1])

    @Slot(bool)
    def set_lines_movable(self, movable):
        """
        Enable or disable the ability to move the lines.

        :param bool movable: If True, lines can be moved. If False, they are locked in place.
        """
        self.sig_start_line.setMovable(movable)
        self.sig_end_line.setMovable(movable)
        self.ref_start_line.setMovable(movable)
        self.ref_end_line.setMovable(movable)


class StateEstimationTimeTraceWidget(QWidget):
    _log = get_logger(__name__)

    def __init__(self):
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

    @Slot()
    def update_time_trace_image(self, time_trace, readout_interval):
        y = time_trace
        self.time_trace_PlotWidget.setLabel(axis="bottom", text="time", units="s")
        if readout_interval == 0:
            readout_interval = 1
            self._log.warn(
                "Time between readouts could not be determined from loaded pulse sequence. "
                "Make sure a pulse sequence is loaded. Switching to number of readouts as x axis."
            )
            self.time_trace_PlotWidget.setLabel(
                axis="bottom", text="readouts", units="#"
            )
        x = np.arange(len(y)) * readout_interval
        self.time_trace_image.setData(x=x, y=y)
