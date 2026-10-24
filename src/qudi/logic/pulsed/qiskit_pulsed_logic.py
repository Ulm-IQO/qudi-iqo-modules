# -*- coding: utf-8 -*-

"""
This file contains the qudi logic module that drives the Qiskit pulsed toolchain: it lists the
Qiskit circuit templates of the sequence generator, runs generation, sampling and loading, switches
the pulse generator and describes the generated asset for display.

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

__all__ = ['QiskitPulsedLogic']

import copy
import inspect
import warnings
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
from PySide6 import QtCore

from qudi.core.connector import Connector
from qudi.core.module import LogicBase
from qudi.core.statusvariable import StatusVar
from qudi.logic.pulsed.qiskit_compiler import (
    DEFAULT_GATE_STRING,
    DEFAULT_PYTHON_CIRCUIT,
    AreaMatch,
    build_template_circuit,
    compile_circuit,
)
from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType
from qudi.logic.pulsed.sequence_generator_logic import SequenceGeneratorLogic
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain

# Generate methods of the sequence generator that belong to this toolchain start with this prefix.
TEMPLATE_PREFIX = 'qiskit_'


class QiskitPulsedLogic(LogicBase):
    """
    Logic module for compiling Qiskit circuits into pulse assets and playing them on the pulse
    generator.

    The compilation itself is done by the predefined generate methods of QiskitPredefinedGenerator,
    so the same templates are available from the pulsed measurement GUI. This module lists those
    templates, checks a circuit before it is handed over so that mistakes are reported with their
    reason, runs generation, sampling and loading through the signals of the sequence generator
    logic, switches the pulse generator and describes the generated asset for display.

    Example config for copy-paste:

    qiskit_pulsed_logic:
        module.Class: 'pulsed.qiskit_pulsed_logic.QiskitPulsedLogic'
        connect:
            sequencegeneratorlogic: 'sequence_generator_logic'
    """

    # declare connectors
    sequencegeneratorlogic = Connector(interface=SequenceGeneratorLogic)

    # declare status variables
    _selected_template = StatusVar(name='selected_template', default='qiskit_ramsey_virtual_z')
    _python_source = StatusVar(name='python_source', default=DEFAULT_PYTHON_CIRCUIT)
    _gate_string = StatusVar(name='gate_string', default=DEFAULT_GATE_STRING)

    # signals controlling the SequenceGeneratorLogic
    sigGeneratePredefinedSequence = QtCore.Signal(str, dict)
    sigSampleBlockEnsemble = QtCore.Signal(str)
    sigLoadBlockEnsemble = QtCore.Signal(str)
    sigGenerationParametersChanged = QtCore.Signal(dict)
    sigPulseGeneratorSettingsChanged = QtCore.Signal(dict)

    # signals for the GUI
    sigTemplatesUpdated = QtCore.Signal(list)
    sigAssetGenerated = QtCore.Signal(str, dict)
    sigLoadedAssetUpdated = QtCore.Signal(str, str)
    sigPulserRunningUpdated = QtCore.Signal(bool)
    sigGenerationParametersUpdated = QtCore.Signal(dict)
    sigStatusUpdated = QtCore.Signal(dict)
    sigStatusMessage = QtCore.Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = Mutex()
        # Dictionary serving as status register
        self.status_dict = dict()
        self._current_asset = None
        self._current_preview = dict()

    def on_activate(self):
        """Initialisation performed during activation of the module."""
        self.status_dict = {
            'generation_busy': False,
            'sampling_busy': False,
            'loading_busy': False,
            'sample_and_load': False,
            'pulser_running': False,
        }
        self._current_asset = None
        self._current_preview = dict()

        generator = self.sequencegeneratorlogic()
        # Connect signals controlling the SequenceGeneratorLogic
        self.sigGeneratePredefinedSequence.connect(
            generator.generate_predefined_sequence, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.sigSampleBlockEnsemble.connect(
            generator.sample_pulse_block_ensemble, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.sigLoadBlockEnsemble.connect(generator.load_ensemble, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigGenerationParametersChanged.connect(
            generator.set_generation_parameters, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.sigPulseGeneratorSettingsChanged.connect(
            generator.set_pulse_generator_settings, QtCore.Qt.ConnectionType.QueuedConnection
        )
        # Connect signals coming from the SequenceGeneratorLogic
        self._connect_generator_signals()

        templates = self.templates
        if not templates:
            self.log.error(
                'The sequence generator logic offers no "qiskit_" generate methods. Check that qiskit is '
                'installed and that qiskit_predefined_methods imports without error.'
            )
        elif self._selected_template not in templates:
            self._selected_template = templates[0]
        self.status_dict['pulser_running'] = self.pulser_running

    def on_deactivate(self):
        """Deinitialisation performed during deactivation of the module."""
        self.sigGeneratePredefinedSequence.disconnect()
        self.sigSampleBlockEnsemble.disconnect()
        self.sigLoadBlockEnsemble.disconnect()
        self.sigGenerationParametersChanged.disconnect()
        self.sigPulseGeneratorSettingsChanged.disconnect()
        self._disconnect_generator_signals()

    # -------------------------------------------------------------------------- templates --------

    @property
    def templates(self) -> List[str]:
        """Names of the Qiskit circuit templates, i.e. the qiskit_* generate methods."""
        return sorted(
            name for name in self.sequencegeneratorlogic().generate_methods if name.startswith(TEMPLATE_PREFIX)
        )

    @property
    def selected_template(self) -> str:
        """The template generated or selected most recently."""
        return self._selected_template

    @property
    def python_source(self) -> str:
        """The Python source last used for the qiskit_python_circuit template."""
        return self._python_source

    @property
    def gate_string(self) -> str:
        """The gate string last used for the qiskit_gate_string template."""
        return self._gate_string

    def template_parameters(self, template: str) -> Dict[str, Any]:
        """
        The parameters of a template with their default values, as read from the generate method.
        The free-form circuit inputs are replaced by the values used last.

        @param str template: template name
        @return dict: parameter name -> default value
        """
        parameters = dict(self.sequencegeneratorlogic().generate_method_params.get(template, dict()))
        if 'source' in parameters:
            parameters['source'] = self._python_source
        if 'gates' in parameters:
            parameters['gates'] = self._gate_string
        return parameters

    def template_description(self, template: str) -> str:
        """
        The first paragraph of the docstring of the generate method behind a template.

        @param str template: template name
        @return str: description
        """
        method = self.sequencegeneratorlogic().generate_methods.get(template)
        doc = inspect.getdoc(method) if method is not None else ''
        return (doc or '').split('\n\n')[0].replace('\n', ' ')

    def preview(self, template: str, parameters: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """
        Compile a template without generating anything, to show the circuit and report mistakes.

        The same compiler and the same area matching as the generate method are used, so any error
        the generation would raise is reported here with its message.

        @param str template: template name
        @param dict parameters: template parameters
        @return dict: 'ok', 'error', 'drawing', 'gates', 'pulses' (list of dicts) and 'frame_phase'
        """
        template = str(template)
        parameters = self._local_parameters(parameters)
        result = {'ok': False, 'error': '', 'drawing': '', 'gates': 0, 'pulses': list(), 'frame_phase': 0.0}
        try:
            compiled = compile_circuit(build_template_circuit(template[len(TEMPLATE_PREFIX) :], parameters))
            generator = self._qiskit_generator()
            if generator is not None:
                generator.area_matching(
                    parameters.get('area_match', AreaMatch.duration), generator.envelope_area_factor()
                )
        except Exception as err:
            result['error'] = str(err) or type(err).__name__
            return result
        result['ok'] = True
        result['drawing'] = compiled.drawing
        result['gates'] = compiled.gate_count
        result['frame_phase'] = compiled.frame_phase
        result['pulses'] = [
            {'gate': pulse.gate, 'pi_fraction': pulse.pi_fraction, 'phase': pulse.phase, 'label': pulse.label}
            for pulse in compiled.pulses
        ]
        return result

    # ---------------------------------------------------------------- generation parameters --------

    @property
    def generation_parameters(self) -> Dict[str, Any]:
        """The generation parameters of the sequence generator logic."""
        return self.sequencegeneratorlogic().generation_parameters

    @property
    def pulse_envelope(self) -> PulseEnvelope:
        """The pulse envelope selected in the generation parameters."""
        envelope = self.generation_parameters.get('pulse_envelope')
        return PulseEnvelope(PulseEnvelopeType.rectangle) if envelope is None else envelope

    @property
    def pulse_shape_summary(self) -> Dict[str, Any]:
        """
        The envelope settings together with the quantities that follow from them.

        @return dict: envelope type name and parameters, area factor, duration scale, length of a
                      rectangular pi pulse, microwave frequency, amplitude, full scale, Rabi period
        """
        parameters = self.generation_parameters
        envelope = self.pulse_envelope
        generator = self._qiskit_generator()
        area_factor = generator.envelope_area_factor(envelope) if generator is not None else 1.0
        full_scale = generator.microwave_full_scale if generator is not None else None
        rabi_period = float(parameters.get('rabi_period', 0.0))
        return {
            'envelope': envelope.type.name,
            'parameters': dict(envelope.parameters),
            'area_factor': area_factor,
            'duration_scale': 1.0 / area_factor,
            'pi_pulse_length': rabi_period / 2,
            'rabi_period': rabi_period,
            'microwave_frequency': float(parameters.get('microwave_frequency', 0.0)),
            'microwave_amplitude': float(parameters.get('microwave_amplitude', 0.0)),
            'full_scale': full_scale,
        }

    @QtCore.Slot(object)
    def set_pulse_envelope(self, envelope) -> None:
        """
        Select the pulse envelope in the generation parameters. The order of a parabola or sin^n
        envelope is stored alongside, as the pulsed measurement GUI does.

        @param PulseEnvelope envelope: the envelope to use for every microwave pulse. A remote proxy,
                                       the name of an envelope type or a dict with 'type' and
                                       'parameters' entries are accepted as well.
        """
        envelope = self._local_envelope(envelope)
        parameters = {'pulse_envelope': envelope}
        if 'order' in envelope.parameters:
            parameters['pulse_envelope_order'] = envelope.parameters['order']
        self.sigGenerationParametersChanged.emit(parameters)

    @QtCore.Slot(dict)
    def set_generation_parameters(self, parameters: Mapping[str, Any]) -> None:
        """
        Change generation parameters of the sequence generator logic, e.g. the microwave frequency.

        @param dict parameters: parameter name -> value
        """
        parameters = self._local_parameters(parameters)
        if 'pulse_envelope' in parameters:
            parameters['pulse_envelope'] = self._local_envelope(parameters['pulse_envelope'])
        self.sigGenerationParametersChanged.emit(parameters)

    # -------------------------------------------------------------------------- generation --------

    @property
    def current_asset(self) -> Optional[str]:
        """Name of the asset generated most recently by this module, or None."""
        return self._current_asset

    @property
    def loaded_asset(self) -> Tuple[str, str]:
        """Name and type of the asset loaded into the pulse generator."""
        return self.sequencegeneratorlogic().loaded_asset

    @QtCore.Slot(str, dict)
    @QtCore.Slot(str, dict, bool)
    def generate_template(
        self, template: str, parameters: Optional[Mapping[str, Any]] = None, sample_and_load: bool = False
    ) -> None:
        """
        Generate the pulse block ensemble of a template, and optionally sample and load it as well.

        @param str template: template name
        @param dict parameters: template parameters, as offered by template_parameters
        @param bool sample_and_load: also sample the ensemble and load it into the pulse generator
        """
        with self._thread_lock:
            if self._is_busy():
                self.log.error('Generation, sampling or loading of a previous asset is still in progress.')
                return
            template = str(template)
            if template not in self.templates:
                self.log.error(f'Unknown template "{template}". Available: {", ".join(self.templates)}')
                return
            parameters = self._local_parameters(parameters)
            self._remember_inputs(template, parameters)
            self._selected_template = str(template)
            preview = self.preview(template, parameters)
            if not preview['ok']:
                self.log.error(f'The circuit of "{template}" cannot be compiled: {preview["error"]}')
                self.sigStatusMessage.emit(f'Cannot compile the circuit: {preview["error"]}')
                return
            try:
                activation_config = self._activation_config_for_generation()
            except ValueError as err:
                self.log.error(str(err))
                self.sigStatusMessage.emit(str(err))
                return
            if activation_config is not None:
                # The blocks are built for the active channels only, so the channels have to be
                # active before generation. Both signals are queued to the same thread in order.
                if self.status_dict['pulser_running']:
                    self._set_pulser(False)
                self.log.info(
                    f'Switching the channel activation of the pulse generator to "{activation_config}" so '
                    f'that it includes the laser and microwave channels.'
                )
                self.sigPulseGeneratorSettingsChanged.emit({'activation_config': activation_config})
            self._refresh_generator_signals()
            self._current_preview = preview
            self.status_dict['generation_busy'] = True
            self.status_dict['sample_and_load'] = bool(sample_and_load)
            self._emit_status()
            self.sigStatusMessage.emit(f'Generating "{template}" ...')
            self.sigGeneratePredefinedSequence.emit(template, parameters)

    # Slots receiving dict or object arguments from the SequenceGeneratorLogic are not decorated,
    # as in the other qudi modules, so that their connections can be found again to disconnect them.
    def _predefined_sequence_generated(self, asset_name, is_sequence):
        with self._thread_lock:
            # Generation started elsewhere, e.g. from the pulsed measurement GUI, is not ours.
            if not self.status_dict['generation_busy']:
                return
            self.status_dict['generation_busy'] = False
            if asset_name is None or is_sequence:
                if is_sequence:
                    self.log.error('The pulse generator forces sequences, which this toolchain does not support.')
                self.status_dict['sample_and_load'] = False
                self._emit_status()
                self.sigStatusMessage.emit('Generation failed. See the log for details.')
                return
            self._current_asset = str(asset_name)
            summary = self.asset_summary(self._current_asset)
            self.sigAssetGenerated.emit(self._current_asset, summary)
            self.sigStatusMessage.emit(
                f'"{asset_name}" generated: {summary.get("mw_pulses", 0)} microwave pulses, '
                f'{summary.get("length", 0.0) * 1e6:.3f} us per repetition.'
            )
            if self.status_dict['sample_and_load']:
                self._start_sampling(self._current_asset)
            else:
                self._emit_status()

    @QtCore.Slot()
    def sample_and_load(self) -> None:
        """Sample the asset generated most recently and load it into the pulse generator."""
        with self._thread_lock:
            if self._current_asset is None:
                self.sigStatusMessage.emit('Generate a circuit first.')
                return
            if self._is_busy():
                self.log.error('Generation, sampling or loading of a previous asset is still in progress.')
                return
            self._refresh_generator_signals()
            self.status_dict['sample_and_load'] = True
            self._start_sampling(self._current_asset)

    def _start_sampling(self, asset_name: str) -> None:
        """Start sampling an ensemble. The lock must be held."""
        if self.status_dict['pulser_running']:
            self.log.warning('Pulse generator switched off before a new asset is loaded.')
            self._set_pulser(False)
        self.status_dict['sampling_busy'] = True
        self._emit_status()
        self.sigStatusMessage.emit(f'Sampling "{asset_name}" ...')
        self.sigSampleBlockEnsemble.emit(asset_name)

    def _sample_ensemble_finished(self, ensemble):
        with self._thread_lock:
            if not self.status_dict['sampling_busy']:
                return
            self.status_dict['sampling_busy'] = False
            if ensemble is None:
                self.status_dict['sample_and_load'] = False
                self._emit_status()
                self.sigStatusMessage.emit('Sampling failed. See the log for details.')
                return
            if not self.status_dict['sample_and_load']:
                self._emit_status()
                self.sigStatusMessage.emit(f'"{ensemble.name}" sampled.')
                return
            self.status_dict['loading_busy'] = True
            self._emit_status()
            self.sigStatusMessage.emit(f'Loading "{ensemble.name}" into the pulse generator ...')
            self.sigLoadBlockEnsemble.emit(ensemble.name)

    @QtCore.Slot(str, str)
    def _loaded_asset_updated(self, asset_name, asset_type):
        with self._thread_lock:
            was_loading = self.status_dict['loading_busy']
            self.status_dict['loading_busy'] = False
            self.status_dict['sample_and_load'] = False
            self.sigLoadedAssetUpdated.emit(str(asset_name), str(asset_type))
            self._emit_status()
            if was_loading:
                if asset_name:
                    self.sigStatusMessage.emit(f'"{asset_name}" loaded into the pulse generator. Press Run to play it.')
                else:
                    self.sigStatusMessage.emit('Loading failed. See the log for details.')

    # ----------------------------------------------------------------------- pulse generator --------

    @property
    def pulser_running(self) -> bool:
        """Whether the pulse generator output is running."""
        try:
            return self.sequencegeneratorlogic().pulsegenerator().get_status()[0] == 1
        except Exception:
            self.log.exception('Could not read the pulse generator status:')
            return False

    @QtCore.Slot(bool)
    def toggle_pulser(self, switch_on: bool) -> None:
        """
        Start or stop the pulse generator output.

        @param bool switch_on: True to start, False to stop
        """
        with self._thread_lock:
            switch_on = bool(switch_on)
            if switch_on and (self.status_dict['sampling_busy'] or self.status_dict['loading_busy']):
                self.log.error('The pulse generator can not be started while an asset is being sampled or loaded.')
                self.sigPulserRunningUpdated.emit(self.status_dict['pulser_running'])
                return
            if switch_on and not self.loaded_asset[0]:
                self.sigStatusMessage.emit(
                    'Nothing is loaded into the pulse generator. Sample and load an asset first.'
                )
                self.sigPulserRunningUpdated.emit(False)
                return
            self._set_pulser(switch_on)

    def _set_pulser(self, switch_on: bool) -> None:
        """Switch the pulse generator and report its state. The lock must be held."""
        pulser = self.sequencegeneratorlogic().pulsegenerator()
        error = pulser.pulser_on() if switch_on else pulser.pulser_off()
        if error < 0:
            self.log.error(f'Failed to switch the pulse generator {"on" if switch_on else "off"}.')
        running = self.pulser_running
        self.status_dict['pulser_running'] = running
        self.sigPulserRunningUpdated.emit(running)
        self.sigStatusMessage.emit('Pulse generator running.' if running else 'Pulse generator stopped.')

    # ------------------------------------------------------------------------- description --------

    def asset_summary(self, asset_name: str) -> Dict[str, Any]:
        """
        Describe a generated pulse block ensemble for display.

        Every element of every block is listed with its duration, whether the laser is on and, for
        microwave pulses, the drive phase, amplitude, sampling function and normalised envelope. The
        envelope is sampled from the element's own sampling function, so it cannot differ from what
        is uploaded.

        @param str asset_name: name of a saved PulseBlockEnsemble
        @return dict: the description, empty if the ensemble is unknown
        """
        generator = self.sequencegeneratorlogic()
        ensemble = generator.saved_pulse_block_ensembles.get(asset_name)
        if ensemble is None:
            return dict()
        parameters = generator.generation_parameters
        laser_channel = parameters.get('laser_channel', '')
        microwave_channel = parameters.get('microwave_channel', '')
        blocks = generator.saved_pulse_blocks

        elements = list()
        for block_name, repetitions in ensemble.block_list:
            block = blocks.get(block_name)
            if block is None:
                continue
            for _ in range(int(repetitions) + 1):
                for element in block.element_list:
                    elements.append(self._describe_element(element, laser_channel, microwave_channel))

        length = sum(element['duration'] for element in elements)
        mw_on_time = sum(element['duration'] for element in elements if element['mw'])
        summary = {
            'asset': str(asset_name),
            'template': self._selected_template,
            'elements': elements,
            'length': length,
            'mw_pulses': sum(1 for element in elements if element['mw']),
            'mw_on_time': mw_on_time,
            'duty_cycle': (mw_on_time / length) if length else 0.0,
            'number_of_lasers': sum(1 for element in elements if element['laser']),
            'generation_method_parameters': {
                key: (value.name if isinstance(value, Enum) else value)
                for key, value in ensemble.generation_method_parameters.items()
            },
            'envelope': self.pulse_envelope.type.name,
            'rabi_period': float(parameters.get('rabi_period', 0.0)),
            'microwave_frequency': float(parameters.get('microwave_frequency', 0.0)),
            'microwave_amplitude': float(parameters.get('microwave_amplitude', 0.0)),
        }
        if asset_name == self._current_asset and self._current_preview:
            summary['drawing'] = self._current_preview['drawing']
            summary['gates'] = self._current_preview['gates']
            summary['pulses'] = list(self._current_preview['pulses'])
        return summary

    @staticmethod
    def _describe_element(element, laser_channel: str, microwave_channel: str) -> Dict[str, Any]:
        """Describe one PulseBlockElement: duration, laser state and microwave pulse properties."""
        function = element.pulse_function.get(microwave_channel)
        is_mw = function is not None and all(hasattr(function, name) for name in ('amplitude', 'frequency', 'phase'))
        entry = {
            'duration': float(element.init_length_s),
            'laser': bool(element.laser_on or element.digital_high.get(laser_channel, False)),
            'mw': is_mw,
            'label': 'idle',
            'phase': None,
            'amplitude': None,
            'function': None,
            'envelope': list(),
        }
        if entry['laser']:
            entry['label'] = 'laser'
        if is_mw:
            phase = float(function.phase) % 360.0
            entry['label'] = f'mw {phase:.0f} deg'
            entry['phase'] = phase
            entry['amplitude'] = float(function.amplitude)
            entry['function'] = type(function).__name__
            entry['envelope'] = QiskitPulsedLogic._envelope_shape(function)
        return entry

    @staticmethod
    def _envelope_shape(function, points: int = 101) -> List[float]:
        """
        Normalised envelope of a sine-like sampling function. A copy is sampled with frequency 0 and
        phase 90 degrees, which turns the carrier into a constant and leaves the envelope.
        """
        probe = copy.deepcopy(function)
        probe.amplitude = 1.0
        probe.frequency = 0.0
        probe.phase = 90.0
        return [float(value) for value in probe.get_samples(np.linspace(0.0, 1.0, points))]

    # ------------------------------------------------------------------------------ helpers --------

    def _qiskit_generator(self):
        """The QiskitPredefinedGenerator instance behind the templates, or None."""
        for name, method in self.sequencegeneratorlogic().generate_methods.items():
            if name.startswith(TEMPLATE_PREFIX):
                return getattr(method, '__self__', None)
        return None

    def _generator_connections(self):
        """The connections from signals of the SequenceGeneratorLogic to this module."""
        generator = self.sequencegeneratorlogic()
        return (
            (generator.sigPredefinedSequenceGenerated, self._predefined_sequence_generated),
            (generator.sigSampleEnsembleComplete, self._sample_ensemble_finished),
            (generator.sigLoadedAssetUpdated, self._loaded_asset_updated),
            (generator.sigSamplingSettingsUpdated, self.sigGenerationParametersUpdated),
        )

    def _connect_generator_signals(self) -> None:
        for signal, slot in self._generator_connections():
            signal.connect(slot, QtCore.Qt.ConnectionType.QueuedConnection)

    def _disconnect_generator_signals(self) -> None:
        # Tolerate connections that are already gone: the pulsed master logic disconnects every
        # receiver of these signals when it is deactivated. PySide6 reports that with a
        # RuntimeWarning instead of an exception, which is expected here and therefore silenced.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            for signal, slot in self._generator_connections():
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass

    def _refresh_generator_signals(self) -> None:
        """Connect the SequenceGeneratorLogic signals again, exactly once, before an operation."""
        self._disconnect_generator_signals()
        self._connect_generator_signals()

    def _activation_config_for_generation(self) -> Optional[str]:
        """
        Check that the active channels of the pulse generator include every channel the generated
        blocks use (laser, microwave and, if set, sync and gate channel).

        @return str: None if they do, otherwise the name of the smallest channel activation that does
        """
        generator = self.sequencegeneratorlogic()
        parameters = generator.generation_parameters
        required = {
            str(parameters.get(key))
            for key in ('laser_channel', 'microwave_channel', 'sync_channel', 'gate_channel')
            if parameters.get(key)
        }
        active = set(generator.pulse_generator_settings.get('activation_config', ('', set()))[1])
        if required.issubset(active):
            return None
        configs = generator.pulse_generator_constraints.activation_config
        candidates = sorted((len(channels), name) for name, channels in configs.items() if required.issubset(channels))
        if not candidates:
            raise ValueError(
                f'No channel activation of the pulse generator includes the channels {", ".join(sorted(required))}. '
                f'Check the laser and microwave channel in the pulse generator settings of the pulsed GUI.'
            )
        return candidates[0][1]

    @staticmethod
    def _local_parameters(parameters: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Copy a parameter mapping into a local dict, fetching remote proxies by value."""
        if not parameters:
            return dict()
        return {str(key): netobtain(value) for key, value in dict(parameters).items()}

    @staticmethod
    def _local_envelope(envelope) -> PulseEnvelope:
        """
        Rebuild a PulseEnvelope from a local object, a remote proxy of one, an envelope type name or
        a dict with 'type' and 'parameters' entries, so that comparisons with PulseEnvelopeType work.
        """
        # Remote proxies pass isinstance checks against the local class, so fetch them by value first
        # and only trust the exact local type.
        envelope = netobtain(envelope)
        if type(envelope) is PulseEnvelope:
            return envelope
        if isinstance(envelope, Mapping):
            envelope_type, parameters = envelope['type'], envelope.get('parameters', dict())
        elif isinstance(envelope, str):
            envelope_type, parameters = envelope, dict()
        else:
            envelope_type = getattr(envelope, 'type', envelope)
            parameters = getattr(envelope, 'parameters', dict())
        text = str(getattr(envelope_type, 'value', envelope_type))
        try:
            local_type = PulseEnvelopeType(text)
        except ValueError:
            local_type = PulseEnvelopeType[text]
        local_parameters = {str(key): netobtain(value) for key, value in dict(parameters).items()}
        return PulseEnvelope(local_type, local_parameters) if local_parameters else PulseEnvelope(local_type)

    def _remember_inputs(self, template: str, parameters: Mapping[str, Any]) -> None:
        """Keep the free-form circuit inputs, so they are offered again next time."""
        if 'source' in parameters:
            self._python_source = str(parameters['source'])
        if 'gates' in parameters:
            self._gate_string = str(parameters['gates'])

    def _is_busy(self) -> bool:
        return bool(
            self.status_dict['generation_busy'] or self.status_dict['sampling_busy'] or self.status_dict['loading_busy']
        )

    def _emit_status(self) -> None:
        self.sigStatusUpdated.emit(self.status_dict.copy())
