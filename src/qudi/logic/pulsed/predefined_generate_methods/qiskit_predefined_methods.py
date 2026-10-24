# -*- coding: utf-8 -*-

"""
This file contains the qudi predefined methods that compile Qiskit circuits into pulse assets.

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

import numpy as np

from qudi.logic.pulsed.pulse_objects import PredefinedGeneratorBase, PulseBlock, PulseBlockEnsemble
from qudi.logic.pulsed.qiskit_compiler import (
    DEFAULT_GATE_STRING,
    DEFAULT_PYTHON_CIRCUIT_ONE_LINE,
    AreaMatch,
    SingleQubitGate,
    build_template_circuit,
    compile_circuit,
)
from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType


class QiskitPredefinedGenerator(PredefinedGeneratorBase):
    """
    Predefined methods that compile single-qubit Qiskit circuits into pulse block ensembles.

    A single-qubit rotation has an axis in the equatorial plane and an angle. The axis becomes the
    phase of the microwave drive and the angle becomes the area of the pulse envelope, so the pulse
    length is |angle| / pi times half the Rabi period. An rz gate emits no pulse; it shifts the
    phase of every later pulse instead. A negative angle is a rotation about the opposite axis, so
    its sign adds 180 degrees to the drive phase.

    The envelope comes from the generation parameters, as for every other predefined method. Since
    the rotation angle is set by the pulse area, a non rectangular envelope delivers less rotation
    than a rectangular one of the same length and peak. The `area_match` parameter compensates for
    this by stretching the pulse (duration), by raising the peak (amplitude) or not at all (none).

    One block holds the pulses followed by the laser readout, the laser delay and the wait time, in
    the same arrangement as the rabi method, and is repeated by the pulse generator.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    ################################################################################################
    #                             Generation methods for waveforms                                 #
    ################################################################################################
    def generate_qiskit_gate(self, name='qiskit_gate', gate=SingleQubitGate.x, area_match=AreaMatch.duration):
        """Generates a single gate followed by the laser readout.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        gate : SingleQubitGate
            The gate to play. Rotations by pi/2 are listed as x90, y90, x_minus_90 and y_minus_90.
        area_match : AreaMatch
            How a non rectangular pulse envelope is compensated so that the rotation angle is kept.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
        """
        return self._generate_from_template(name, 'gate', {'gate': gate}, area_match)

    def generate_qiskit_ramsey_virtual_z(self, name='qiskit_ramsey', phase_deg=90.0, area_match=AreaMatch.duration):
        """Generates pi/2, a virtual Z rotation and pi/2, followed by the laser readout.

        The Z rotation emits no pulse and only shifts the phase of the second pi/2 pulse. Sweeping
        the phase gives a Ramsey fringe and confirms that rz reaches the drive phase.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        phase_deg : float
            Angle of the virtual Z rotation in degrees.
        area_match : AreaMatch
            How a non rectangular pulse envelope is compensated so that the rotation angle is kept.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
        """
        return self._generate_from_template(name, 'ramsey_virtual_z', {'phase_deg': phase_deg}, area_match)

    def generate_qiskit_xy4(self, name='qiskit_xy4', area_match=AreaMatch.duration):
        """Generates the XY4 block, four pi pulses about X, Y, X, Y, followed by the laser readout.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        area_match : AreaMatch
            How a non rectangular pulse envelope is compensated so that the rotation angle is kept.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
        """
        return self._generate_from_template(name, 'xy4', dict(), area_match)

    def generate_qiskit_null_sequence(self, name='qiskit_null', area_match=AreaMatch.duration):
        """Generates rx(pi/2), ry(-pi/2), ry(pi/2), rx(-pi/2), which is equal to the identity.

        The spin returns to its initial state, so the fluorescence stays at its initial level. Any
        deviation shows that the emitted pulses do not match the circuit. Negative angles are used.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        area_match : AreaMatch
            How a non rectangular pulse envelope is compensated so that the rotation angle is kept.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
        """
        return self._generate_from_template(name, 'null_sequence', dict(), area_match)

    def generate_qiskit_gate_string(
        self, name='qiskit_gates', gates=DEFAULT_GATE_STRING, area_match=AreaMatch.duration
    ):
        """Generates the circuit written as a gate string, followed by the laser readout.

        Gates are separated by semicolons. Gates with an angle are written as rx(pi/2) or rz(90deg),
        gates without an angle by name, for example h or t. Gates outside rx, ry and rz are
        transpiled into them.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        gates : str
            The gate string.
        area_match : AreaMatch
            How a non rectangular pulse envelope is compensated so that the rotation angle is kept.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
        """
        return self._generate_from_template(name, 'gate_string', {'gates': gates}, area_match)

    def generate_qiskit_python_circuit(
        self, name='qiskit_circuit', source=DEFAULT_PYTHON_CIRCUIT_ONE_LINE, area_match=AreaMatch.duration
    ):
        """Generates the circuit built by Python source, followed by the laser readout.

        The source runs with QuantumCircuit, np and pi predefined and has to assign a single-qubit
        QuantumCircuit to a variable named "circuit".

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        source : str
            Python source that builds the circuit.
        area_match : AreaMatch
            How a non rectangular pulse envelope is compensated so that the rotation angle is kept.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
        """
        return self._generate_from_template(name, 'python_circuit', {'source': source}, area_match)

    ################################################################################################
    #                                       Helper methods                                         #
    ################################################################################################
    def envelope_area_factor(self, envelope=None, samples=10001):
        """
        Mean height of the envelope of a microwave pulse, relative to its peak.

        The rotation angle is set by the pulse area, so this is the fraction of the rotation a shaped
        pulse delivers compared with a rectangular pulse of the same length and peak. The value is
        sampled from the very sampling function the pulses use, with the carrier turned into a
        constant, so it is right for every envelope type. A digital microwave channel has no envelope.

        @param PulseEnvelope envelope: the envelope, or None for the one in the generation parameters
        @param int samples: number of samples for the numerical mean
        @return float: mean envelope height in (0, 1]
        """
        if envelope is None:
            envelope = PulseEnvelope(PulseEnvelopeType.from_gen_settings)
        envelope = self._get_envelope(envelope)
        if self.microwave_channel is None or self.microwave_channel.startswith('d'):
            return 1.0
        element = self._get_mw_element(length=1e-6, increment=0, amp=1.0, freq=0.0, phase=90.0, envelope=envelope)
        function = element.pulse_function[self.microwave_channel]
        return float(np.mean(function.get_samples(np.linspace(0.0, 1e-6, samples))))

    def area_matching(self, area_match, area_factor):
        """
        Duration scale and peak amplitude that preserve the rotation angle for a shaped envelope.

        @param AreaMatch area_match: which quantity is scaled
        @param float area_factor: mean envelope height from envelope_area_factor
        @return tuple(float, float): factor applied to every pulse length, peak amplitude in V
        """
        if not isinstance(area_match, AreaMatch):
            # also accepts the name of a member and remote proxies of a member
            area_match = AreaMatch(str(getattr(area_match, 'value', area_match)))
        amplitude = self.microwave_amplitude
        duration_scale = 1.0
        if area_match == AreaMatch.duration:
            duration_scale = 1.0 / area_factor
        elif area_match == AreaMatch.amplitude:
            amplitude = amplitude / area_factor
        full_scale = self.microwave_full_scale
        if full_scale is not None and amplitude > full_scale * (1 + 1e-9):
            if area_match == AreaMatch.amplitude:
                reason = f'Amplitude area matching needs a peak of {amplitude:.4g} V'
                advice = 'Use duration matching or lower the microwave amplitude.'
            else:
                reason = f'The microwave amplitude is {amplitude:.4g} V'
                advice = 'Lower the microwave amplitude or raise the channel amplitude of the pulse generator.'
            raise ValueError(
                f'{reason}, but the full scale of channel {self.microwave_channel} is {full_scale:.4g} V, so the '
                f'pulses would be clipped. {advice}'
            )
        return duration_scale, amplitude

    @property
    def microwave_full_scale(self):
        """
        Half the peak-to-peak voltage of the microwave channel, or None if it is not known.
        """
        analog_levels = self.pulse_generator_settings.get('analog_levels')
        if not analog_levels:
            return None
        pp_voltage = analog_levels[0].get(self.microwave_channel)
        return None if pp_voltage is None else pp_voltage / 2

    def _generate_from_template(self, name, template, parameters, area_match):
        """Build the circuit of a template and compile it into a pulse block ensemble."""
        return self._generate_from_circuit(name, build_template_circuit(template, parameters), area_match)

    def _generate_from_circuit(self, name, circuit, area_match):
        """
        Compile a single-qubit circuit into one pulse block ensemble.

        The block holds one microwave element per emitted pulse, followed by the laser readout, the
        laser delay and the wait time, so the pulse generator replays init, gates and readout in a
        loop. The rotating frame is kept across the ensemble, so the drive phase of every pulse
        refers to one continuously running reference. This is what makes the virtual Z meaningful.

        @param str name: name of the PulseBlockEnsemble
        @param qiskit.QuantumCircuit circuit: circuit on one qubit
        @param AreaMatch area_match: envelope compensation
        @return tuple(list, list, list): created blocks, ensembles and sequences
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        compiled = compile_circuit(circuit)
        envelope = self._get_envelope(PulseEnvelope(PulseEnvelopeType.from_gen_settings))
        duration_scale, amplitude = self.area_matching(area_match, self.envelope_area_factor(envelope))
        if not amplitude > 0:
            self.log.warning(
                'The microwave amplitude of the generation parameters is 0 V. The gates carry no microwave '
                'power until it is set in the pulse generator settings.'
            )

        # One block: the gate pulses, then the laser readout, which is also the initialisation of
        # the next repetition, the laser delay and the wait time.
        circuit_block = PulseBlock(name=name)
        for pulse in compiled.pulses:
            circuit_block.append(
                self._get_mw_element(
                    length=pulse.pi_fraction * self.rabi_period / 2 * duration_scale,
                    increment=0,
                    amp=amplitude,
                    freq=self.microwave_frequency,
                    phase=pulse.phase,
                    envelope=envelope,
                )
            )
        laser_element, delay_element, waiting_element = self._get_readout_element()
        circuit_block.append(laser_element)
        circuit_block.append(delay_element)
        circuit_block.append(waiting_element)
        created_blocks.append(circuit_block)

        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((circuit_block.name, 0))

        # Create and append sync trigger block if needed
        created_blocks, block_ensemble = self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        # add metadata to invoke settings later on
        block_ensemble = self._add_metadata_to_settings(
            block_ensemble,
            created_blocks=created_blocks,
            alternating=False,
            controlled_variable=np.array([0.0]),
            units=('', ''),
            labels=('', 'Signal'),
            number_of_lasers=1,
        )
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences
