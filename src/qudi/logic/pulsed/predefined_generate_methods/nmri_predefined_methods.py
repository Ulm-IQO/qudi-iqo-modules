# -*- coding: utf-8 -*-

"""
This file contains the Qudi predefined methods for the nanoMRI setup (AWG70k + PulseBlaster).

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
from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockEnsemble, PulseSequence
from qudi.logic.pulsed.pulse_objects import PredefinedGeneratorBase

"""
Rabi, pulsed ODMR and CW ODMR as pulse SEQUENCES, modelled on the dx_pulser methods but without
I/Q modulation, pulser channel, duty-cycle correction or RF switch.

The microwave is the plain MW element of the generation parameters (microwave_channel,
microwave_frequency, microwave_amplitude), e.g. a sine sampled directly by the AWG.

Sequence structure (Rabi / pulsed ODMR):
    trigger -> [mw_k -> readout] for every point k -> loop back to the trigger
    trigger: sync pulse on sync_channel (on this setup also the PulseBlaster -> AWG trigger)
    mw_k:    MW pulse of point k
    readout: laser + gate (laser_length) -> gate (laser_delay) -> [repolarization] -> wait
             (wait_time), shared by all points, so it is uploaded only once
Sequence structure (CW ODMR):
    trigger -> [mw_laser_k -> readout] per point; mw_laser_k is MW + laser + gate together
    (mw_length), followed by gate (laser_delay); readout is [repolarization] -> wait (wait_time).

Repolarization (repolarization_time > 0): a laser-only pulse (gate off, so it is not counted) at
the start of every wait, i.e. between every readout and the next point. Use it if the laser alone
does not reset the spins within one readout, e.g. at low laser power: then the next point (and
in particular the reference point of the alternating trace) starts with the spin state left
behind by the previous point, and the reference shows the same ODMR/Rabi signal as the signal.

Granularity correction: every block is padded with idle time to the pulse generator's minimum
waveform length and length step, so the sequence generator never has to append its own
idle_extension. In MW blocks the padding is put BEFORE the MW pulse, so the MW pulse always ends
right where the readout starts, independent of the swept tau.

Alternating time trace (alternating=True): every point is directly followed by a reference
point, so the measurement shows the signal and its reference side by side:
    alternating_mode 1: the reference point has no MW (idle wait of the same length)
    alternating_mode 2: the reference point plays the same MW pulse plus an additional pi pulse
                        (rabi_period / 2 at microwave_frequency); Rabi and pulsed ODMR only
"""


class NMRIPredefinedGenerator(PredefinedGeneratorBase):
    """
    Sequence-mode Rabi, pulsed ODMR and CW ODMR with granularity correction and an optional
    alternating (reference) time trace.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    ################################################################################################
    #                                    Generation methods                                        #
    ################################################################################################

    def generate_nmri_rabi(self, name='nmri_rabi', tau_start=10.0e-9, tau_step=10.0e-9,
                           num_of_points=50, alternating=False, alternating_mode=1,
                           repolarization_time=0.0):
        """
        Rabi as sequence: trigger -> [MW pulse of length tau -> readout] per tau.

        repolarization_time : float
            Length of a laser-only pulse (not counted) after every readout, in s. 0: none.

        alternating : bool
            If True, each tau point is directly followed by a reference point.
        alternating_mode : int
            1 -> the reference point has no MW: an idle wait of length tau.
            2 -> the reference point plays the tau MW pulse plus an additional pi pulse
                 (rabi_period / 2).
        """
        alternating_mode = self._check_alternating_mode(alternating, alternating_mode, (1, 2))
        created_blocks, created_ensembles, created_sequences = list(), list(), list()

        tau_array = tau_start + np.arange(num_of_points) * tau_step
        pi_length = self.rabi_period / 2

        points = list()
        for kk, tau in enumerate(tau_array):
            points.append(self._register_block(
                '{0}_mw_{1}'.format(name, kk), [self._get_nmri_mw_element(tau)],
                created_blocks, created_ensembles, pad_at_start=True))
            if not alternating:
                continue
            if alternating_mode == 1:
                alt_elements = [self._get_idle_element(length=tau, increment=0)]
            else:
                alt_elements = [self._get_nmri_mw_element(tau),
                                self._get_nmri_mw_element(pi_length)]
            points.append(self._register_block(
                '{0}_alt_mw_{1}'.format(name, kk), alt_elements,
                created_blocks, created_ensembles, pad_at_start=True))

        sequence = self._build_nmri_sequence(
            name, points, created_blocks, created_ensembles,
            readout_elements=self._get_readout_elements(repolarization_time))
        self._set_measurement_information(
            sequence, alternating, tau_array, units=('s', ''),
            labels=('Tau<sub>pulse spacing</sub>', 'Signal'), num_points=len(points),
            counting_length=self.laser_length + self.laser_delay)
        created_sequences.append(sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_nmri_pulsedodmr(self, name='nmri_pODMR', freq_start=2.82e9, freq_stop=2.92e9,
                                 num_of_points=50, alternating=False, alternating_mode=1,
                                 repolarization_time=0.0):
        """
        Pulsed ODMR as sequence: trigger -> [pi pulse (rabi_period / 2) at frequency f -> readout]
        per frequency. Requires a calibrated rabi_period.

        repolarization_time : float
            Length of a laser-only pulse (not counted) after every readout, in s. 0: none.

        alternating : bool
            If True, each frequency point is directly followed by a reference point.
        alternating_mode : int
            1 -> the reference point has no MW: an idle wait of pi-pulse length. It is the same
                 for every frequency, so one shared waveform is used.
            2 -> the reference point plays the swept pi pulse plus an additional pi pulse at
                 microwave_frequency.
        """
        alternating_mode = self._check_alternating_mode(alternating, alternating_mode, (1, 2))
        created_blocks, created_ensembles, created_sequences = list(), list(), list()

        freq_array = np.linspace(freq_start, freq_stop, num_of_points)
        pi_length = self.rabi_period / 2

        shared_alt_point = None
        if alternating and alternating_mode == 1:
            shared_alt_point = self._register_block(
                name + '_alt_mw', [self._get_idle_element(length=pi_length, increment=0)],
                created_blocks, created_ensembles, pad_at_start=True)

        points = list()
        for kk, freq in enumerate(freq_array):
            points.append(self._register_block(
                '{0}_mw_{1}'.format(name, kk), [self._get_nmri_mw_element(pi_length, freq=freq)],
                created_blocks, created_ensembles, pad_at_start=True))
            if not alternating:
                continue
            if alternating_mode == 1:
                points.append(shared_alt_point)
            else:
                points.append(self._register_block(
                    '{0}_alt_mw_{1}'.format(name, kk),
                    [self._get_nmri_mw_element(pi_length, freq=freq),
                     self._get_nmri_mw_element(pi_length)],
                    created_blocks, created_ensembles, pad_at_start=True))

        sequence = self._build_nmri_sequence(
            name, points, created_blocks, created_ensembles,
            readout_elements=self._get_readout_elements(repolarization_time))
        self._set_measurement_information(
            sequence, alternating, freq_array, units=('Hz', ''), labels=('Frequency', 'Signal'),
            num_points=len(points), counting_length=self.laser_length + self.laser_delay)
        created_sequences.append(sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_nmri_cw_odmr(self, name='nmri_cw_odmr', freq_start=2.82e9, freq_stop=2.92e9,
                              num_of_points=50, mw_amp=0.2, mw_length=10e-6, alternating=False,
                              repolarization_time=0.0):
        """
        CW ODMR as sequence: trigger -> [MW at frequency f together with laser + gate for
        mw_length -> gate for laser_delay -> wait] per frequency.

        mw_amp : float
            MW amplitude in V (analog microwave channel).
        mw_length : float
            Duration of the simultaneous MW + laser + gate window in s.
        alternating : bool
            If True, each frequency point is directly followed by a reference point with laser
            and gate only (no MW). It is the same for every frequency, so one shared waveform is
            used.
        repolarization_time : float
            Length of a laser-only pulse (not counted) after every point, in s. 0: none.
        """
        created_blocks, created_ensembles, created_sequences = list(), list(), list()

        freq_array = np.linspace(freq_start, freq_stop, num_of_points)

        shared_alt_point = None
        if alternating:
            shared_alt_point = self._register_block(
                name + '_alt_mw',
                [self._get_laser_gate_element(length=mw_length, increment=0),
                 self._get_delay_gate_element()],
                created_blocks, created_ensembles)

        points = list()
        for kk, freq in enumerate(freq_array):
            mw_laser_gate = self._get_mw_laser_gate_element(
                length=mw_length, increment=0, amp=mw_amp, freq=freq, phase=0)
            points.append(self._register_block(
                '{0}_mw_{1}'.format(name, kk), [mw_laser_gate, self._get_delay_gate_element()],
                created_blocks, created_ensembles))
            if alternating:
                points.append(shared_alt_point)

        sequence = self._build_nmri_sequence(
            name, points, created_blocks, created_ensembles,
            readout_elements=self._get_wait_elements(repolarization_time))
        self._set_measurement_information(
            sequence, alternating, freq_array, units=('Hz', ''), labels=('Frequency', 'Signal'),
            num_points=len(points), counting_length=mw_length + self.laser_delay)
        created_sequences.append(sequence)
        return created_blocks, created_ensembles, created_sequences

    ################################################################################################
    #                                         Helpers                                              #
    ################################################################################################

    def _get_nmri_mw_element(self, length, freq=None):
        """ MW pulse of the given length at freq (default: microwave_frequency), with the
        microwave_amplitude of the generation parameters. """
        return self._get_mw_element(
            length=length, increment=0, amp=self.microwave_amplitude,
            freq=self.microwave_frequency if freq is None else freq, phase=0)

    def _get_readout_elements(self, repolarization_time=0.0):
        """ laser + gate (laser_length) -> gate (laser_delay) -> [repolarization] -> wait """
        return [self._get_laser_gate_element(length=self.laser_length, increment=0),
                self._get_delay_gate_element()] + self._get_wait_elements(repolarization_time)

    def _get_wait_elements(self, repolarization_time=0.0):
        """ [laser-only repolarization pulse, gate off] -> wait (wait_time); either is left out
        if its length is 0 """
        elements = list()
        if repolarization_time > 0:
            elements.append(self._get_laser_element(length=repolarization_time, increment=0))
        if self.wait_time > 0:
            elements.append(self._get_idle_element(length=self.wait_time, increment=0))
        return elements

    def _check_alternating_mode(self, alternating, alternating_mode, allowed):
        if alternating and alternating_mode not in allowed:
            self.log.error('alternating_mode must be one of {0} (got {1}); using 1.'.format(
                allowed, alternating_mode))
            return 1
        return alternating_mode

    def _pad_block_to_granularity(self, block, at_start=False):
        """
        Measures the exact sample count of `block` and pads it with an idle element so its
        length is >= the pulse generator's minimum waveform length and an exact multiple of its
        length step. This keeps the sequence generator from appending its own idle_extension.

        @param bool at_start: insert the padding before the block's elements (e.g. so a MW pulse
                              always ends directly before the following readout) instead of after.
        """
        self.save_block(block)
        temp_ensemble = PulseBlockEnsemble(name='__tmp_granularity_check__{0}'.format(block.name),
                                           rotating_frame=False)
        temp_ensemble.append((block.name, 0))
        nominal_samples = int(self.analyze_block_ensemble(temp_ensemble)['number_of_samples'])

        sample_rate = self.pulse_generator_settings['sample_rate']
        min_samples = int(self.pulse_generator_constraints.waveform_length.min)
        step_samples = max(1, int(self.pulse_generator_constraints.waveform_length.step))

        target_samples = max(nominal_samples, min_samples)
        if target_samples % step_samples:
            target_samples += step_samples - target_samples % step_samples
        pad_samples = target_samples - nominal_samples
        if pad_samples > 0:
            pad_element = self._get_idle_element(length=pad_samples / sample_rate, increment=0)
            if at_start:
                block.insert(0, pad_element)
            else:
                block.append(pad_element)
            self.save_block(block)

    def _register_block(self, block_name, elements, created_blocks, created_ensembles,
                        pad_at_start=False):
        """
        Builds a PulseBlock from `elements`, pads it to the granularity, wraps it into a
        single-block PulseBlockEnsemble of the same name and appends both to the created_* lists.

        @return (PulseBlock, PulseBlockEnsemble)
        """
        block = PulseBlock(name=block_name)
        for element in elements:
            block.append(element)
        self._pad_block_to_granularity(block, at_start=pad_at_start)
        created_blocks.append(block)
        ensemble = PulseBlockEnsemble(name=block_name, rotating_frame=False)
        ensemble.append((block.name, 0))
        created_ensembles.append(ensemble)
        return block, ensemble

    def _build_nmri_sequence(self, name, points, created_blocks, created_ensembles,
                             readout_elements=None):
        """
        trigger -> [point -> readout] for every point -> loop back to the trigger.

        @param list points: (PulseBlock, PulseBlockEnsemble) per point, in play order
                            (reference points already interleaved).
        @param list readout_elements: elements of the shared readout block. Default: laser +
                                      gate, gate for laser_delay, wait (see _get_readout_elements).

        @return PulseSequence
        """
        if readout_elements is None:
            readout_elements = self._get_readout_elements()
        _, trigger = self._register_block(name + '_trigger', [self._get_sync_element()],
                                          created_blocks, created_ensembles)
        _, readout = self._register_block(name + '_readout', readout_elements,
                                          created_blocks, created_ensembles)

        sequence = PulseSequence(name=name, rotating_frame=False)
        sequence.append(trigger.name)
        for _, point in points:
            sequence.append(point.name)
            sequence.append(readout.name)
        # AWG step indices are 1-based: loop back onto the trigger
        sequence[-1].go_to = 1
        sequence.refresh_parameters()
        return sequence

    @staticmethod
    def _set_measurement_information(sequence, alternating, controlled_variable, units, labels,
                                     num_points, counting_length):
        sequence.measurement_information['alternating'] = alternating
        sequence.measurement_information['laser_ignore_list'] = list()
        sequence.measurement_information['controlled_variable'] = controlled_variable
        sequence.measurement_information['units'] = units
        sequence.measurement_information['labels'] = labels
        sequence.measurement_information['number_of_lasers'] = num_points
        sequence.measurement_information['counting_length'] = counting_length
