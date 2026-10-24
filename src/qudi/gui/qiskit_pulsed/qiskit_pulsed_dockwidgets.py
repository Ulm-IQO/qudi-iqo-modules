# -*- coding: utf-8 -*-

"""
This file contains the dock widgets of the Qiskit pulsed GUI: circuit selection and editing, pulse
shape settings and the summary of the generated asset.

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

__all__ = ('CircuitDockWidget', 'ParameterFormWidget', 'PulseShapeDockWidget', 'SummaryDockWidget')

from enum import Enum
from typing import Any, Dict, Mapping

from PySide6 import QtCore, QtGui, QtWidgets

from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox

# Parameters edited in a multi-line text field instead of a single line.
MULTILINE_PARAMETERS = ('source', 'gates')
# Envelope types the microwave elements can be built with. Optimal control needs its own assets.
SELECTABLE_ENVELOPES = tuple(
    envelope_type for envelope_type in PulseEnvelopeType if envelope_type is not PulseEnvelopeType.optimal
)


def monospace_font() -> QtGui.QFont:
    """Fixed width system font for the circuit, preview and summary displays."""
    font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(QtWidgets.QApplication.font().pointSize())
    return font


def section_label(text: str) -> QtWidgets.QLabel:
    """A bold label used as a section heading inside a form layout."""
    label = QtWidgets.QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


class ParameterFormWidget(QtWidgets.QWidget):
    """
    A form built at runtime from the parameters of a generate method, in the same way the pulsed
    measurement GUI builds its predefined method widgets. The editor follows the type of the default
    value: bool, int, float, str and Enum are supported. The free-form circuit inputs get a
    multi-line editor.
    """

    sigParametersEdited = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._editors = dict()
        self._layout = QtWidgets.QFormLayout()
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setLayout(self._layout)

    def set_parameters(self, parameters: Mapping[str, Any]) -> None:
        """Rebuild the form for a new set of parameters."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._editors = dict()
        for name, default in parameters.items():
            editor = self._create_editor(name, default)
            if editor is None:
                continue
            self._editors[name] = editor
            label = QtWidgets.QLabel(f'{name}:')
            if name in MULTILINE_PARAMETERS:
                label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
            self._layout.addRow(label, editor)

    def _create_editor(self, name: str, default: Any):
        """Return an editor widget for a parameter, or None if the type is not supported."""
        if isinstance(default, bool):
            editor = QtWidgets.QCheckBox()
            editor.setChecked(default)
            editor.stateChanged.connect(self.sigParametersEdited)
        elif isinstance(default, Enum):
            editor = QtWidgets.QComboBox()
            for option in type(default):
                editor.addItem(option.name, option)
            editor.setCurrentText(default.name)
            editor.currentIndexChanged.connect(self.sigParametersEdited)
        elif isinstance(default, int):
            editor = ScienSpinBox()
            editor.setValue(default)
            editor.valueChanged.connect(self.sigParametersEdited)
        elif isinstance(default, float):
            editor = ScienDSpinBox()
            if 'amp' in name or 'volt' in name:
                editor.setSuffix('V')
            elif 'freq' in name:
                editor.setSuffix('Hz')
            elif 'time' in name or 'period' in name or 'tau' in name or 'length' in name:
                editor.setSuffix('s')
            elif 'deg' in name:
                editor.setSuffix('°')
            editor.setValue(default)
            editor.valueChanged.connect(self.sigParametersEdited)
        elif isinstance(default, str) and name in MULTILINE_PARAMETERS:
            editor = QtWidgets.QPlainTextEdit()
            editor.setFont(monospace_font())
            editor.setPlainText(default)
            editor.setMinimumHeight(140)
            editor.setTabStopDistance(4 * QtGui.QFontMetrics(editor.font()).horizontalAdvance(' '))
            editor.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
            editor.textChanged.connect(self.sigParametersEdited)
        elif isinstance(default, str):
            editor = QtWidgets.QLineEdit()
            editor.setText(default)
            editor.editingFinished.connect(self.sigParametersEdited)
        else:
            return None
        return editor

    def get_values(self) -> Dict[str, Any]:
        """Read the current values of all parameters."""
        values = dict()
        for name, editor in self._editors.items():
            if isinstance(editor, QtWidgets.QCheckBox):
                values[name] = editor.isChecked()
            elif isinstance(editor, QtWidgets.QComboBox):
                values[name] = editor.currentData()
            elif isinstance(editor, (ScienSpinBox, ScienDSpinBox)):
                values[name] = editor.value()
            elif isinstance(editor, QtWidgets.QPlainTextEdit):
                values[name] = editor.toPlainText()
            else:
                values[name] = editor.text()
        return values


class CircuitDockWidget(AdvancedDockWidget):
    """
    Selection of the circuit template, editing of its parameters and a live preview of the
    transpiled circuit and the pulses it produces.
    """

    sigTemplateChanged = QtCore.Signal(str)
    sigParametersEdited = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('Circuit')
        self.setObjectName('Circuit')

        self.template_combobox = QtWidgets.QComboBox()
        self.template_combobox.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.template_combobox.currentTextChanged.connect(self.sigTemplateChanged)

        self.description_label = QtWidgets.QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)

        self.parameter_form = ParameterFormWidget()
        self.parameter_form.sigParametersEdited.connect(self.sigParametersEdited)

        self.preview_display = QtWidgets.QPlainTextEdit()
        self.preview_display.setReadOnly(True)
        self.preview_display.setFont(monospace_font())
        self.preview_display.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_display.setMinimumHeight(120)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(1, 1, 1, 1)
        template_layout = QtWidgets.QHBoxLayout()
        template_layout.addWidget(QtWidgets.QLabel('Template:'))
        template_layout.addWidget(self.template_combobox, 1)
        layout.addLayout(template_layout)
        layout.addWidget(self.description_label)
        layout.addWidget(self.parameter_form)
        layout.addWidget(QtWidgets.QLabel('Preview of the transpiled circuit and its pulses:'))
        layout.addWidget(self.preview_display, 1)

        main_widget = QtWidgets.QWidget()
        main_widget.setLayout(layout)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidget(main_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setWidget(scroll_area)

    def set_templates(self, templates) -> None:
        """Replace the selectable templates without emitting a change signal."""
        current = self.template_combobox.currentText()
        self.template_combobox.blockSignals(True)
        self.template_combobox.clear()
        self.template_combobox.addItems(list(templates))
        index = self.template_combobox.findText(current)
        if index >= 0:
            self.template_combobox.setCurrentIndex(index)
        self.template_combobox.blockSignals(False)

    def set_preview(self, preview: Mapping[str, Any]) -> None:
        """Show the compiled circuit, or the reason it cannot be compiled."""
        if not preview:
            self.preview_display.setPlainText('')
            return
        if not preview.get('ok', False):
            self.preview_display.setPlainText(f'Cannot compile this circuit:\n{preview.get("error", "")}')
            return
        pulses = preview.get('pulses', list())
        lines = [preview.get('drawing', '').rstrip(), '']
        lines.append(f'{preview.get("gates", 0)} gates -> {len(pulses)} microwave pulses (rz emits no pulse)')
        for index, pulse in enumerate(pulses):
            lines.append(
                f'  {index + 1:2d}  {pulse["gate"]}  {pulse["pi_fraction"]:.4g} pi  at  {pulse["phase"]:.1f} deg'
            )
        self.preview_display.setPlainText('\n'.join(lines))


class PulseShapeDockWidget(AdvancedDockWidget):
    """
    Pulse envelope settings, the microwave parameters that set every pulse length, and the
    quantities that follow from them. The rotation angle is set by the pulse area, so a shaped
    envelope needs a longer pulse or a higher peak to deliver the same rotation.
    """

    sigEnvelopeChanged = QtCore.Signal(object)
    sigMicrowaveParametersChanged = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('Pulse Shape')
        self.setObjectName('Pulse Shape')

        # envelope
        self.envelope_combobox = QtWidgets.QComboBox()
        for envelope_type in SELECTABLE_ENVELOPES:
            self.envelope_combobox.addItem(envelope_type.name, envelope_type)
        self.envelope_combobox.setToolTip(
            'Shape of every microwave pulse. A Gaussian envelope has no spectral sidelobes, so it '
            'drives far less off-resonant transitions than a rectangular pulse.'
        )
        self.order_spinbox = ScienDSpinBox()
        self.order_spinbox.setRange(0, 1000)
        self.order_spinbox.setValue(1)
        self.order_spinbox.setToolTip('Order of the parabola or sin^n envelope.')
        self.n_sigma_spinbox = ScienDSpinBox()
        self.n_sigma_spinbox.setRange(0.5, 10)
        self.n_sigma_spinbox.setValue(2.0)
        self.n_sigma_spinbox.setToolTip('Truncation point of the Gaussian, in units of sigma.')
        self.lifted_checkbox = QtWidgets.QCheckBox('start and end at zero')
        self.lifted_checkbox.setChecked(True)
        self.lifted_checkbox.setToolTip(
            'Offset the Gaussian so that it starts and ends at exactly zero. Without this there is a '
            'step at each edge, which broadens the spectrum.'
        )
        for widget in (self.envelope_combobox, self.order_spinbox, self.n_sigma_spinbox, self.lifted_checkbox):
            widget.setMinimumWidth(90)
        self.envelope_combobox.currentIndexChanged.connect(self._envelope_edited)
        self.order_spinbox.editingFinished.connect(self._envelope_edited)
        self.n_sigma_spinbox.editingFinished.connect(self._envelope_edited)
        self.lifted_checkbox.stateChanged.connect(self._envelope_edited)

        envelope_layout = QtWidgets.QFormLayout()
        envelope_layout.setContentsMargins(1, 1, 1, 1)
        envelope_layout.addRow(section_label('Envelope'))
        envelope_layout.addRow('Envelope:', self.envelope_combobox)
        envelope_layout.addRow('Order:', self.order_spinbox)
        envelope_layout.addRow('Truncation:', self.n_sigma_spinbox)
        envelope_layout.addRow('Lifted:', self.lifted_checkbox)
        self._envelope_layout = envelope_layout
        self._order_row = 2
        self._n_sigma_row = 3
        self._lifted_row = 4

        # microwave parameters, shared with the pulsed measurement GUI
        self.frequency_spinbox = ScienDSpinBox()
        self.frequency_spinbox.setSuffix('Hz')
        self.frequency_spinbox.setRange(0, 1e12)
        self.frequency_spinbox.setToolTip('Microwave frequency, e.g. the NV resonance from a CW ODMR measurement.')
        self.amplitude_spinbox = ScienDSpinBox()
        self.amplitude_spinbox.setSuffix('V')
        self.amplitude_spinbox.setRange(0, 100)
        self.amplitude_spinbox.setToolTip('Peak amplitude of the microwave pulses.')
        self.rabi_period_spinbox = ScienDSpinBox()
        self.rabi_period_spinbox.setSuffix('s')
        self.rabi_period_spinbox.setRange(0, 1)
        self.rabi_period_spinbox.setToolTip('Rabi period at this amplitude. Half of it is the length of a pi pulse.')
        for spinbox in (self.frequency_spinbox, self.amplitude_spinbox, self.rabi_period_spinbox):
            spinbox.setMinimumWidth(90)
            spinbox.editingFinished.connect(self._microwave_edited)

        microwave_layout = QtWidgets.QFormLayout()
        microwave_layout.setContentsMargins(1, 1, 1, 1)
        microwave_layout.addRow(section_label('Microwave (generation parameters)'))
        microwave_layout.addRow('Frequency:', self.frequency_spinbox)
        microwave_layout.addRow('Amplitude:', self.amplitude_spinbox)
        microwave_layout.addRow('Rabi period:', self.rabi_period_spinbox)

        # derived values
        self.area_factor_label = QtWidgets.QLabel('-')
        self.duration_scale_label = QtWidgets.QLabel('-')
        self.pi_pulse_label = QtWidgets.QLabel('-')
        self.full_scale_label = QtWidgets.QLabel('-')
        readout_layout = QtWidgets.QFormLayout()
        readout_layout.setContentsMargins(1, 1, 1, 1)
        readout_layout.addRow(section_label('Resulting pulses'))
        readout_layout.addRow('Area factor:', self.area_factor_label)
        readout_layout.addRow('Duration scale:', self.duration_scale_label)
        readout_layout.addRow('pi pulse:', self.pi_pulse_label)
        readout_layout.addRow('Full scale:', self.full_scale_label)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(12)
        layout.addLayout(envelope_layout)
        layout.addLayout(microwave_layout)
        layout.addLayout(readout_layout)
        layout.addStretch()
        main_widget = QtWidgets.QWidget()
        main_widget.setLayout(layout)
        self.setWidget(main_widget)
        self._update_visible_rows()

    def envelope(self) -> PulseEnvelope:
        """The envelope currently selected in the widgets."""
        envelope_type = self.envelope_combobox.currentData()
        if envelope_type == PulseEnvelopeType.parabola:
            return PulseEnvelope(envelope_type, {'order': round(self.order_spinbox.value())})
        if envelope_type == PulseEnvelopeType.sin_n:
            return PulseEnvelope(envelope_type, {'order': float(self.order_spinbox.value())})
        if envelope_type == PulseEnvelopeType.gaussian:
            return PulseEnvelope(
                envelope_type,
                {'n_sigma': float(self.n_sigma_spinbox.value()), 'lifted': self.lifted_checkbox.isChecked()},
            )
        return PulseEnvelope(envelope_type)

    def set_envelope(self, envelope: PulseEnvelope) -> None:
        """Show an envelope without emitting a change signal."""
        widgets = (self.envelope_combobox, self.order_spinbox, self.n_sigma_spinbox, self.lifted_checkbox)
        for widget in widgets:
            widget.blockSignals(True)
        envelope_type = envelope.type
        if envelope_type not in SELECTABLE_ENVELOPES:
            envelope_type = PulseEnvelopeType.rectangle
        self.envelope_combobox.setCurrentIndex(self.envelope_combobox.findData(envelope_type))
        parameters = envelope.parameters
        if 'order' in parameters:
            self.order_spinbox.setValue(float(parameters['order']))
        if 'n_sigma' in parameters:
            self.n_sigma_spinbox.setValue(float(parameters['n_sigma']))
        if 'lifted' in parameters:
            self.lifted_checkbox.setChecked(bool(parameters['lifted']))
        for widget in widgets:
            widget.blockSignals(False)
        self._update_visible_rows()

    def set_microwave_parameters(self, parameters: Mapping[str, Any]) -> None:
        """Show the microwave generation parameters without emitting a change signal."""
        spinboxes = (self.frequency_spinbox, self.amplitude_spinbox, self.rabi_period_spinbox)
        for spinbox in spinboxes:
            spinbox.blockSignals(True)
        self.frequency_spinbox.setValue(float(parameters.get('microwave_frequency', 0.0)))
        self.amplitude_spinbox.setValue(float(parameters.get('microwave_amplitude', 0.0)))
        self.rabi_period_spinbox.setValue(float(parameters.get('rabi_period', 0.0)))
        for spinbox in spinboxes:
            spinbox.blockSignals(False)

    def set_readouts(self, summary: Mapping[str, Any]) -> None:
        """Show the values that follow from the envelope and microwave parameters."""
        area_factor = float(summary.get('area_factor', 1.0))
        duration_scale = float(summary.get('duration_scale', 1.0))
        pi_pulse = float(summary.get('pi_pulse_length', 0.0))
        full_scale = summary.get('full_scale')
        amplitude = float(summary.get('microwave_amplitude', 0.0))
        self.area_factor_label.setText(f'{area_factor:.4f}')
        self.duration_scale_label.setText(f'x {duration_scale:.4f}')
        self.pi_pulse_label.setText(
            f'{pi_pulse * 1e9:.2f} ns rectangular, {pi_pulse * duration_scale * 1e9:.2f} ns with duration matching'
        )
        if full_scale is None:
            self.full_scale_label.setText('not reported by the pulse generator')
        else:
            self.full_scale_label.setText(f'{full_scale:.4g} V ({amplitude / full_scale * 100:.0f} % used)')

    @QtCore.Slot()
    def _envelope_edited(self):
        self._update_visible_rows()
        self.sigEnvelopeChanged.emit(self.envelope())

    @QtCore.Slot()
    def _microwave_edited(self):
        self.sigMicrowaveParametersChanged.emit(
            {
                'microwave_frequency': self.frequency_spinbox.value(),
                'microwave_amplitude': self.amplitude_spinbox.value(),
                'rabi_period': self.rabi_period_spinbox.value(),
            }
        )

    def _update_visible_rows(self):
        envelope_type = self.envelope_combobox.currentData()
        self._envelope_layout.setRowVisible(
            self._order_row, envelope_type in (PulseEnvelopeType.parabola, PulseEnvelopeType.sin_n)
        )
        self._envelope_layout.setRowVisible(self._n_sigma_row, envelope_type == PulseEnvelopeType.gaussian)
        self._envelope_layout.setRowVisible(self._lifted_row, envelope_type == PulseEnvelopeType.gaussian)


class SummaryDockWidget(AdvancedDockWidget):
    """Read-only summary of the most recently generated asset and of what is loaded."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('Generated Asset')
        self.setObjectName('Generated Asset')

        self.summary_display = QtWidgets.QPlainTextEdit()
        self.summary_display.setReadOnly(True)
        self.summary_display.setFont(monospace_font())
        self.summary_display.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.summary_display)
        main_widget = QtWidgets.QWidget()
        main_widget.setLayout(layout)
        self.setWidget(main_widget)

    def set_summary(self, text: str) -> None:
        """Show the given summary text."""
        self.summary_display.setPlainText(str(text))
