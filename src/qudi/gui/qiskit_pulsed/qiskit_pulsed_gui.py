# -*- coding: utf-8 -*-

"""
This file contains the qudi GUI module for compiling Qiskit circuits into pulse assets and playing
them on the pulse generator.

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

__all__ = ('QiskitPulsedGui',)

from typing import Any, Mapping

from PySide6 import QtCore

from qudi.core.connector import Connector
from qudi.core.module import GuiBase
from qudi.logic.pulsed.qiskit_pulsed_logic import QiskitPulsedLogic

from .qiskit_pulsed_dockwidgets import CircuitDockWidget, PulseShapeDockWidget, SummaryDockWidget
from .qiskit_pulsed_main_window import QiskitPulsedMainWindow

# Delay between the last edit of a circuit parameter and the refresh of the preview
PREVIEW_DELAY_MS = 400


class QiskitPulsedGui(GuiBase):
    """
    GUI for compiling Qiskit circuits into pulse assets and playing them on the pulse generator.

    Pick a circuit template, edit its parameters (or write the circuit as a gate string or as
    Python code), check the live preview, then Generate, Sample & Load and Run. The pulse envelope
    and the microwave parameters are the generation parameters of the sequence generator logic, so
    they are shared with the pulsed measurement GUI.

    example config for copy-paste:

    qiskit_pulsed_gui:
        module.Class: 'qiskit_pulsed.qiskit_pulsed_gui.QiskitPulsedGui'
        connect:
            qiskit_pulsed_logic: 'qiskit_pulsed_logic'
    """

    # declare connectors
    _qiskit_pulsed_logic = Connector(name='qiskit_pulsed_logic', interface=QiskitPulsedLogic)

    sigGenerate = QtCore.Signal(str, dict, bool)
    sigSampleAndLoad = QtCore.Signal()
    sigTogglePulser = QtCore.Signal(bool)
    sigSetPulseEnvelope = QtCore.Signal(object)
    sigSetGenerationParameters = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._circuit_dockwidget = None
        self._pulse_shape_dockwidget = None
        self._summary_dockwidget = None
        self._preview_timer = None
        self._loaded_asset = ('', '')

    def on_activate(self):
        """Create the main window and the dock widgets and connect them to the logic."""
        logic = self._qiskit_pulsed_logic()

        # Create main window and dock widgets
        self._mw = QiskitPulsedMainWindow()
        self._circuit_dockwidget = CircuitDockWidget(parent=self._mw)
        self._pulse_shape_dockwidget = PulseShapeDockWidget(parent=self._mw)
        self._summary_dockwidget = SummaryDockWidget(parent=self._mw)
        self._preview_timer = QtCore.QTimer(self._mw)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)

        # Initialize widget contents
        self._circuit_dockwidget.set_templates(logic.templates)
        self._circuit_dockwidget.template_combobox.blockSignals(True)
        self._circuit_dockwidget.template_combobox.setCurrentText(logic.selected_template)
        self._circuit_dockwidget.template_combobox.blockSignals(False)
        self._template_changed(logic.selected_template)
        self._generation_parameters_updated(logic.generation_parameters)
        self._pulser_running_updated(logic.pulser_running)
        self._status_updated(logic.status_dict)
        self._loaded_asset_updated(*logic.loaded_asset)
        if logic.current_asset:
            self._asset_generated(logic.current_asset, logic.asset_summary(logic.current_asset))

        # Connect signals
        self.__connect_main_window_actions()
        self.__connect_dockwidget_signals()
        self.__connect_logic_signals()
        self.__connect_gui_signals()

        self.restore_default_view()
        self.show()

    def on_deactivate(self):
        """Disconnect all signals and close the main window."""
        # Disconnect signals
        self.__disconnect_main_window_actions()
        self.__disconnect_dockwidget_signals()
        self.__disconnect_logic_signals()
        self.__disconnect_gui_signals()
        self._preview_timer.stop()
        self._save_window_geometry(self._mw)
        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows."""
        self._restore_window_geometry(self._mw)
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def __connect_main_window_actions(self):
        self._mw.action_generate.triggered.connect(self._generate_clicked)
        self._mw.action_sample_load.triggered.connect(self._sample_load_clicked)
        self._mw.action_generate_sample_load.triggered.connect(self._generate_sample_load_clicked)
        self._mw.action_toggle_pulser.triggered[bool].connect(self._pulser_toggled)
        self._mw.action_restore_default_view.triggered.connect(self.restore_default_view)

    def __connect_dockwidget_signals(self):
        self._circuit_dockwidget.sigTemplateChanged.connect(self._template_changed)
        self._circuit_dockwidget.sigParametersEdited.connect(self._preview_timer.start)
        self._preview_timer.timeout.connect(self._update_preview)
        self._pulse_shape_dockwidget.sigEnvelopeChanged.connect(self.sigSetPulseEnvelope)
        self._pulse_shape_dockwidget.sigMicrowaveParametersChanged.connect(self.sigSetGenerationParameters)

    def __connect_logic_signals(self):
        logic = self._qiskit_pulsed_logic()
        logic.sigTemplatesUpdated.connect(self._circuit_dockwidget.set_templates)
        logic.sigAssetGenerated.connect(self._asset_generated)
        logic.sigLoadedAssetUpdated.connect(self._loaded_asset_updated)
        logic.sigPulserRunningUpdated.connect(self._pulser_running_updated)
        logic.sigGenerationParametersUpdated.connect(self._generation_parameters_updated)
        logic.sigStatusUpdated.connect(self._status_updated)
        logic.sigStatusMessage.connect(self._show_status_message)

    def __connect_gui_signals(self):
        logic = self._qiskit_pulsed_logic()
        self.sigGenerate.connect(logic.generate_template, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSampleAndLoad.connect(logic.sample_and_load, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigTogglePulser.connect(logic.toggle_pulser, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSetPulseEnvelope.connect(logic.set_pulse_envelope, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSetGenerationParameters.connect(
            logic.set_generation_parameters, QtCore.Qt.ConnectionType.QueuedConnection
        )

    def __disconnect_main_window_actions(self):
        self._mw.action_generate.triggered.disconnect()
        self._mw.action_sample_load.triggered.disconnect()
        self._mw.action_generate_sample_load.triggered.disconnect()
        self._mw.action_toggle_pulser.triggered[bool].disconnect()
        self._mw.action_restore_default_view.triggered.disconnect()

    def __disconnect_dockwidget_signals(self):
        self._circuit_dockwidget.sigTemplateChanged.disconnect()
        self._circuit_dockwidget.sigParametersEdited.disconnect()
        self._preview_timer.timeout.disconnect()
        self._pulse_shape_dockwidget.sigEnvelopeChanged.disconnect()
        self._pulse_shape_dockwidget.sigMicrowaveParametersChanged.disconnect()

    def __disconnect_logic_signals(self):
        # Only this GUI listens to these signals. They are disconnected without naming the slot, as in
        # the pulsed measurement GUI, because connections of dict-typed signals can not always be
        # found again by slot.
        logic = self._qiskit_pulsed_logic()
        for signal in (
            logic.sigTemplatesUpdated,
            logic.sigAssetGenerated,
            logic.sigLoadedAssetUpdated,
            logic.sigPulserRunningUpdated,
            logic.sigGenerationParametersUpdated,
            logic.sigStatusUpdated,
            logic.sigStatusMessage,
        ):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def __disconnect_gui_signals(self):
        self.sigGenerate.disconnect()
        self.sigSampleAndLoad.disconnect()
        self.sigTogglePulser.disconnect()
        self.sigSetPulseEnvelope.disconnect()
        self.sigSetGenerationParameters.disconnect()

    @QtCore.Slot()
    def restore_default_view(self):
        """Show all dock widgets again in their default position."""
        for dockwidget in (self._circuit_dockwidget, self._pulse_shape_dockwidget, self._summary_dockwidget):
            dockwidget.setFloating(False)
            dockwidget.show()
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self._circuit_dockwidget)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self._pulse_shape_dockwidget)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self._summary_dockwidget)

    # ------------------------------------------------------------------ user interaction --------

    def _selected_template_and_parameters(self):
        template = self._circuit_dockwidget.template_combobox.currentText()
        parameters = self._circuit_dockwidget.parameter_form.get_values()
        return template, parameters

    @QtCore.Slot()
    def _generate_clicked(self):
        template, parameters = self._selected_template_and_parameters()
        self.sigGenerate.emit(template, parameters, False)

    @QtCore.Slot()
    def _generate_sample_load_clicked(self):
        template, parameters = self._selected_template_and_parameters()
        self.sigGenerate.emit(template, parameters, True)

    @QtCore.Slot()
    def _sample_load_clicked(self):
        self.sigSampleAndLoad.emit()

    @QtCore.Slot(bool)
    def _pulser_toggled(self, checked):
        # Disable the action until the logic reports the new state
        self._mw.action_toggle_pulser.setEnabled(False)
        self.sigTogglePulser.emit(bool(checked))

    @QtCore.Slot(str)
    def _template_changed(self, template):
        logic = self._qiskit_pulsed_logic()
        self._circuit_dockwidget.description_label.setText(logic.template_description(template))
        self._circuit_dockwidget.parameter_form.set_parameters(logic.template_parameters(template))
        self._update_preview()

    @QtCore.Slot()
    def _update_preview(self):
        template, parameters = self._selected_template_and_parameters()
        if template:
            self._circuit_dockwidget.set_preview(self._qiskit_pulsed_logic().preview(template, parameters))

    # ------------------------------------------------------------- updates from the logic --------

    def _asset_generated(self, asset_name, summary):
        self._mw.timeline_widget.set_elements(summary.get('elements', list()))
        self._summary_dockwidget.set_summary(self._format_summary(summary))

    @QtCore.Slot(str, str)
    def _loaded_asset_updated(self, asset_name, asset_type):
        self._loaded_asset = (str(asset_name), str(asset_type))
        if asset_name:
            self._summary_dockwidget.setWindowTitle(f'Generated Asset (loaded: {asset_name})')
        else:
            self._summary_dockwidget.setWindowTitle('Generated Asset (nothing loaded)')

    @QtCore.Slot(bool)
    def _pulser_running_updated(self, running):
        self._mw.action_toggle_pulser.blockSignals(True)
        self._mw.action_toggle_pulser.setChecked(bool(running))
        self._mw.action_toggle_pulser.setText('Stop' if running else 'Run')
        self._mw.action_toggle_pulser.blockSignals(False)
        self._mw.action_toggle_pulser.setEnabled(True)

    def _generation_parameters_updated(self, parameters):
        logic = self._qiskit_pulsed_logic()
        envelope = parameters.get('pulse_envelope')
        if envelope is not None:
            self._pulse_shape_dockwidget.set_envelope(envelope)
        self._pulse_shape_dockwidget.set_microwave_parameters(parameters)
        self._pulse_shape_dockwidget.set_readouts(logic.pulse_shape_summary)
        # The envelope changes the area matching, so the preview may report a new error or none.
        self._preview_timer.start()

    def _status_updated(self, status):
        busy = bool(status.get('generation_busy') or status.get('sampling_busy') or status.get('loading_busy'))
        self._mw.action_generate.setEnabled(not busy)
        self._mw.action_sample_load.setEnabled(not busy)
        self._mw.action_generate_sample_load.setEnabled(not busy)
        self._mw.action_toggle_pulser.setEnabled(not busy)

    @QtCore.Slot(str)
    def _show_status_message(self, message):
        self._mw.statusBar().showMessage(str(message))

    # ---------------------------------------------------------------------- rendering --------

    @staticmethod
    def _format_summary(summary: Mapping[str, Any]) -> str:
        if not summary:
            return ''
        length = float(summary.get('length', 0.0))
        mw_on_time = float(summary.get('mw_on_time', 0.0))
        duty_cycle = float(summary.get('duty_cycle', 0.0))
        lines = [
            f'Asset               : {summary.get("asset", "-")}',
            f'Template            : {summary.get("template", "-")}',
            f'Gates in circuit    : {summary.get("gates", "-")}',
            f'Microwave pulses    : {summary.get("mw_pulses", 0)}   (rz gates emit no pulse)',
            f'Repetition length   : {length * 1e6:.3f} us' + (f'   ({1.0 / length / 1e3:.1f} kHz)' if length else ''),
            f'Microwave on        : {mw_on_time * 1e9:.2f} ns   duty cycle {duty_cycle * 100:.2f} %',
            f'Laser pulses        : {summary.get("number_of_lasers", 0)}',
            f'Envelope            : {summary.get("envelope", "-")}',
            f'Microwave frequency : {summary.get("microwave_frequency", 0.0) / 1e9:.6f} GHz',
            f'Microwave amplitude : {summary.get("microwave_amplitude", 0.0):.4f} V',
            f'Rabi period         : {summary.get("rabi_period", 0.0) * 1e9:.2f} ns',
            '',
            'Elements in playback order:',
        ]
        for index, element in enumerate(summary.get('elements', ())):
            lines.append(f'  {index:02d}  {element["label"]:<14} {float(element["duration"]) * 1e9:12.2f} ns')
        parameters = summary.get('generation_method_parameters', dict())
        if parameters:
            lines.append('')
            lines.append('Generation parameters:')
            for key, value in parameters.items():
                text = str(value).replace('\n', ' ')
                if len(text) > 80:
                    text = text[:77] + '...'
                lines.append(f'  {key:<12} {text}')
        return '\n'.join(lines)
