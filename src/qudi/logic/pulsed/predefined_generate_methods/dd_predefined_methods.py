# -*- coding: utf-8 -*-

"""
This file contains the Qudi Predefined Methods for dynamical decoupling sequences

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


class DDPredefinedGenerator(PredefinedGeneratorBase):
    """

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def generate_xy8_tau(self, name='xy8_tau', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy8_order=4, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step
        # calculate "real" start length of tau due to finite pi-pulse length
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shifted pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pi3half_element = self._get_mw_element(length=self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=180)
        else:
            pi3half_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=0)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)
        tauhalf_element = self._get_idle_element(length=tau_pspacing_start / 2, increment=tau_step / 2)
        tau_element = self._get_idle_element(length=tau_pspacing_start, increment=tau_step)

        # Create block and append to created_blocks list
        xy8_block = PulseBlock(name=name)
        xy8_block.append(pihalf_element)
        xy8_block.append(tauhalf_element)
        for n in range(xy8_order):
            xy8_block.append(pix_element)
            xy8_block.append(tau_element)
            xy8_block.append(piy_element)
            xy8_block.append(tau_element)
            xy8_block.append(pix_element)
            xy8_block.append(tau_element)
            xy8_block.append(piy_element)
            xy8_block.append(tau_element)
            xy8_block.append(piy_element)
            xy8_block.append(tau_element)
            xy8_block.append(pix_element)
            xy8_block.append(tau_element)
            xy8_block.append(piy_element)
            xy8_block.append(tau_element)
            xy8_block.append(pix_element)
            if n != xy8_order - 1:
                xy8_block.append(tau_element)
        xy8_block.append(tauhalf_element)
        xy8_block.append(pihalf_element)
        xy8_block.append(laser_element)
        xy8_block.append(delay_element)
        xy8_block.append(waiting_element)
        if alternating:
            xy8_block.append(pihalf_element)
            xy8_block.append(tauhalf_element)
            for n in range(xy8_order):
                xy8_block.append(pix_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(pix_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(pix_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(pix_element)
                if n != xy8_order - 1:
                    xy8_block.append(tau_element)
            xy8_block.append(tauhalf_element)
            xy8_block.append(pi3half_element)
            xy8_block.append(laser_element)
            xy8_block.append(delay_element)
            xy8_block.append(waiting_element)
        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_xy8_freq(self, name='xy8_freq', freq_start=0.1e6, freq_step=0.01e6,
                          num_of_points=50, xy8_order=4, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get frequency array for measurement ticks
        freq_array = freq_start + np.arange(num_of_points) * freq_step
        # get tau array from freq array
        tau_array = 1 / (2 * freq_array)
        tau_pspacing_array = self.tau_2_pulse_spacing(tau_array)

        # Convert back to frequency in order to account for clipped values
        freq_array = 1 / (2 * (self.tau_2_pulse_spacing(tau_pspacing_array, inverse=True)))

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shifted pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pi3half_element = self._get_mw_element(length=self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=180)
        else:
            pi3half_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=0)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)

        # Create block and append to created_blocks list
        xy8_block = PulseBlock(name=name)
        for ii, tau in enumerate(tau_pspacing_array):
            tauhalf_element = self._get_idle_element(length=tau / 2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy8_block.append(pihalf_element)
            xy8_block.append(tauhalf_element)
            for n in range(xy8_order):
                xy8_block.append(pix_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(pix_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(pix_element)
                xy8_block.append(tau_element)
                xy8_block.append(piy_element)
                xy8_block.append(tau_element)
                xy8_block.append(pix_element)
                if n != xy8_order - 1:
                    xy8_block.append(tau_element)
            xy8_block.append(tauhalf_element)
            xy8_block.append(pihalf_element)
            xy8_block.append(laser_element)
            xy8_block.append(delay_element)
            xy8_block.append(waiting_element)
            if alternating:
                xy8_block.append(pihalf_element)
                xy8_block.append(tauhalf_element)
                for n in range(xy8_order):
                    xy8_block.append(pix_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(piy_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(pix_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(piy_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(piy_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(pix_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(piy_element)
                    xy8_block.append(tau_element)
                    xy8_block.append(pix_element)
                    if n != xy8_order - 1:
                        xy8_block.append(tau_element)
                xy8_block.append(tauhalf_element)
                xy8_block.append(pi3half_element)
                xy8_block.append(laser_element)
                xy8_block.append(delay_element)
                xy8_block.append(waiting_element)
        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['labels'] = ('Frequency', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    ################################################################################################
    #                             Generation methods for sequences                                 #
    ################################################################################################

    def generate_xy8_tau_seq(self, name='xy8_tau_seq', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy8_order=4, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # calculate "real" start length of tau due to finite pi-pulse length
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)
        # get tau array for measurement itself which considers finite pulse length
        tau_array = tau_pspacing_start + np.arange(num_of_points) * tau_step
        # get tau array for measurement ticks
        control_array = tau_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shifted pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pi3half_element = self._get_mw_element(length=self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=180)
        else:
            pi3half_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=0)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)

        # Create block and append to created_blocks list
        xy8_seq_block_readout = PulseBlock(name='{0}_readout'.format(name))
        xy8_seq_block_readout.append(waiting_element) # TODO as in my opinion this should be here, but is not for the normal xy8
        xy8_seq_block_readout.append(laser_element)
        xy8_seq_block_readout.append(delay_element)
        created_blocks.append(xy8_seq_block_readout)
        # Create block ensemble
        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((xy8_seq_block_readout.name, 0))
        created_ensembles.append(readout_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            sync_element = self._get_sync_element()
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name), rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the pulse sequence
        xy8_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        # Append PulseBlockEnsembles
        for tau in tau_array:
            xy8_seq_block = PulseBlock(name='{0}{1}_tau'.format(name, tau))
            tauhalf_element = self._get_idle_element(length=tau/2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy8_seq_block.append(pihalf_element)
            xy8_seq_block.append(tauhalf_element)
            for n in range(xy8_order):
                xy8_seq_block.append(pix_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(pix_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(pix_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(pix_element)
                if n != xy8_order - 1:
                    xy8_seq_block.append(tau_element)
            xy8_seq_block.append(tauhalf_element)
            xy8_seq_block.append(pihalf_element)
            created_blocks.append(xy8_seq_block)
            tau_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=True)
            tau_ensemble.append((xy8_seq_block.name, 0))
            created_ensembles.append(tau_ensemble)

            if alternating:
                xy8_seq_block_alt = PulseBlock(name='{0}{1}_tau_alt'.format(name, tau))
                xy8_seq_block_alt.append(pihalf_element)
                xy8_seq_block_alt.append(tauhalf_element)
                for n in range(xy8_order):
                    xy8_seq_block_alt.append(pix_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(pix_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(pix_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(pix_element)
                    if n != xy8_order - 1:
                        xy8_seq_block_alt.append(tau_element)
                xy8_seq_block_alt.append(tauhalf_element)
                xy8_seq_block_alt.append(pi3half_element)
                created_blocks.append(xy8_seq_block_alt)
                tau_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, tau), rotating_frame=True)
                tau_ensemble_alt.append((xy8_seq_block_alt.name, 0))
                created_ensembles.append(tau_ensemble_alt)

            xy8_sequence.append(tau_ensemble.name)
            if self.sync_channel and tau == tau_array[-1] and not alternating:
                xy8_sequence.append(sync_readout_ensemble.name)
            else:
                xy8_sequence.append(readout_ensemble.name)
            if alternating:
                xy8_sequence.append(tau_ensemble_alt.name)
                if self.sync_channel and tau == tau_array[-1]:
                    xy8_sequence.append(sync_readout_ensemble.name)
                else:
                    xy8_sequence.append(readout_ensemble.name)

            count_length += self._get_ensemble_count_length(ensemble=tau_ensemble, created_blocks=created_blocks)
            if alternating:
                count_length += 2 * self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)
                count_length += self._get_ensemble_count_length(ensemble=tau_ensemble_alt, created_blocks=created_blocks)
            else:
                count_length += self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)

        # Make sequence loop infinitely by setting go_to parameter of the last sequence to the first step
        xy8_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        xy8_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        xy8_sequence.measurement_information['alternating'] = alternating
        xy8_sequence.measurement_information['laser_ignore_list'] = list()
        xy8_sequence.measurement_information['controlled_variable'] = control_array #tau_array
        xy8_sequence.measurement_information['units'] = ('s', '')
        xy8_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        xy8_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            xy8_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                ensemble=readout_ensemble, created_blocks=created_blocks)
        else:
            xy8_sequence.measurement_information['counting_length'] = count_length

        # append Pulse sequence to created_sequence list
        created_sequences.append(xy8_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_xy4_tau_seq(self, name='xy4_tau_seq', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy4_order=4, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # calculate "real" start length of tau due to finite pi-pulse length
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)
        # get tau array for measurement ticks
        tau_array = tau_pspacing_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shifted pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pi3half_element = self._get_mw_element(length=self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=180)
        else:
            pi3half_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=0)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)

        # Create block and append to created_blocks list
        xy4_seq_block_readout = PulseBlock(name='{0}_readout'.format(name))
        xy4_seq_block_readout.append(waiting_element) # TODO as in my opinion this should be here
        xy4_seq_block_readout.append(laser_element)
        xy4_seq_block_readout.append(delay_element)
        created_blocks.append(xy4_seq_block_readout)
        # Create block ensemble
        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((xy4_seq_block_readout.name, 0))
        created_ensembles.append(readout_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            sync_element = self._get_sync_element()
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name), rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the pulse sequence
        xy4_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        # Append PulseBlockEnsembles
        for tau in tau_array:
            xy4_seq_block = PulseBlock(name='{0}{1}_tau'.format(name, tau))
            tauhalf_element = self._get_idle_element(length=tau/2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy4_seq_block.append(pihalf_element)
            xy4_seq_block.append(tauhalf_element)
            for n in range(xy4_order):
                xy4_seq_block.append(pix_element)
                xy4_seq_block.append(tau_element)
                xy4_seq_block.append(piy_element)
                xy4_seq_block.append(tau_element)
                xy4_seq_block.append(pix_element)
                xy4_seq_block.append(tau_element)
                xy4_seq_block.append(piy_element)
                if n != xy4_order - 1:
                    xy4_seq_block.append(tau_element)
            xy4_seq_block.append(tauhalf_element)
            xy4_seq_block.append(pihalf_element)
            created_blocks.append(xy4_seq_block)
            tau_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=True)
            tau_ensemble.append((xy4_seq_block.name, 0))
            created_ensembles.append(tau_ensemble)

            if alternating:
                xy4_seq_block_alt = PulseBlock(name='{0}{1}_tau_alt'.format(name, tau))
                xy4_seq_block_alt.append(pihalf_element)
                xy4_seq_block_alt.append(tauhalf_element)
                for n in range(xy4_order):
                    xy4_seq_block_alt.append(pix_element)
                    xy4_seq_block_alt.append(tau_element)
                    xy4_seq_block_alt.append(piy_element)
                    xy4_seq_block_alt.append(tau_element)
                    xy4_seq_block_alt.append(pix_element)
                    xy4_seq_block_alt.append(tau_element)
                    xy4_seq_block_alt.append(piy_element)
                    if n != xy4_order - 1:
                        xy4_seq_block_alt.append(tau_element)
                xy4_seq_block_alt.append(tauhalf_element)
                xy4_seq_block_alt.append(pi3half_element)
                created_blocks.append(xy4_seq_block_alt)
                tau_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, tau), rotating_frame=True)
                tau_ensemble_alt.append((xy4_seq_block_alt.name, 0))
                created_ensembles.append(tau_ensemble_alt)

            xy4_sequence.append(tau_ensemble.name)
            if self.sync_channel and tau == tau_array[-1] and not alternating:
                xy4_sequence.append(sync_readout_ensemble.name)
            else:
                xy4_sequence.append(readout_ensemble.name)
            if alternating:
                xy4_sequence.append(tau_ensemble_alt.name)
                if self.sync_channel and tau == tau_array[-1]:
                    xy4_sequence.append(sync_readout_ensemble.name)
                else:
                    xy4_sequence.append(readout_ensemble.name)

            count_length += self._get_ensemble_count_length(ensemble=tau_ensemble, created_blocks=created_blocks)
            if alternating:
                count_length += 2 * self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)
                count_length += self._get_ensemble_count_length(ensemble=tau_ensemble_alt, created_blocks=created_blocks)
            else:
                count_length += self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)

        # Make sequence loop infinitely by setting go_to parameter of the last sequence to the first step
        xy4_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        xy4_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        xy4_sequence.measurement_information['alternating'] = alternating
        xy4_sequence.measurement_information['laser_ignore_list'] = list()
        xy4_sequence.measurement_information['controlled_variable'] = tau_array
        xy4_sequence.measurement_information['units'] = ('s', '')
        xy4_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        xy4_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            xy4_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                ensemble=readout_ensemble, created_blocks=created_blocks)
        else:
            xy4_sequence.measurement_information['counting_length'] = count_length

        # append Pulse sequence to created_sequence list
        created_sequences.append(xy4_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_xy4_N_seq(self, name='xy4_N_seq', tau_spacing=0.1e6, num_of_points=50, xy4_order_start=4, order_step=1,
                           phase1=0, phase2=0, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get order array for measurement ticks
        order_array = xy4_order_start + np.arange(num_of_points) * order_step
        # get tau array from freq array
        # tau = 1 / (2 * freq_start)
        # calculate "real" tau array (finite pi-pulse length) ?
        # real_tau = tau - self.rabi_period / 2
        # Convert back to frequency in order to account for clipped values
        #freq_real = 1 / (2 * (real_tau + self.rabi_period / 2))

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        xy4_N_seq = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0

        # Create the readout PulseBlockEnsemble
        # Get necessary PulseBlockElements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        # Create PulseBlock and append PulseBlockElements
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element) #TODO
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)
        # Create PulseBlockEnsemble and append block to it
        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element) #TODO
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        pihalf1_element = self._get_mw_element(length=self.rabi_period / 4,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=phase1)
        pihalf2_element = self._get_mw_element(length=self.rabi_period / 4,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=phase2)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pihalf2_alt_element = self._get_mw_element(length=self.rabi_period / 4,
                                                       increment=0,
                                                       amp=self.microwave_amplitude,
                                                       freq=self.microwave_frequency,
                                                       phase=phase2 + 180)
        else:
            pihalf2_alt_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                       increment=0,
                                                       amp=self.microwave_amplitude,
                                                       freq=self.microwave_frequency,
                                                       phase=phase2)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)
        tauhalf_element = self._get_idle_element(length=tau_spacing / 2, increment=0)
        tau_element = self._get_idle_element(length=tau_spacing, increment=0)

        # Create block and append to created_blocks list
        for ii, xy4_order in enumerate(order_array):
            xy4_block = PulseBlock(name='{0}{1}_mw'.format(name, xy4_order))
            xy4_block.append(pihalf1_element)
            xy4_block.append(tauhalf_element)
            for n in range(xy4_order):
                xy4_block.append(pix_element)
                xy4_block.append(tau_element)
                xy4_block.append(piy_element)
                xy4_block.append(tau_element)
                xy4_block.append(pix_element)
                xy4_block.append(tau_element)
                xy4_block.append(piy_element)
                if n != xy4_order - 1:
                    xy4_block.append(tau_element)
            xy4_block.append(tauhalf_element)
            xy4_block.append(pihalf2_element)
            created_blocks.append(xy4_block)
            xy4_ensemble = PulseBlockEnsemble(name='{0}{1}_mw'.format(name, xy4_order), rotating_frame=True)
            xy4_ensemble.append((xy4_block.name, 0))
            created_ensembles.append(xy4_ensemble)
            xy4_N_seq.append(xy4_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=xy4_ensemble, created_blocks=created_blocks)
            if self.sync_channel and xy4_order == order_array[-1] and not alternating:
                xy4_N_seq.append(sync_readout_ensemble.name)
            else:
                xy4_N_seq.append(readout_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)
            if alternating:
                xy4_block_alt = PulseBlock(name='{0}{1}_mw_alt'.format(name, xy4_order))
                xy4_block_alt.append(pihalf1_element)
                xy4_block_alt.append(tauhalf_element)
                for n in range(xy4_order):
                    xy4_block_alt.append(pix_element)
                    xy4_block_alt.append(tau_element)
                    xy4_block_alt.append(piy_element)
                    xy4_block_alt.append(tau_element)
                    xy4_block_alt.append(pix_element)
                    xy4_block_alt.append(tau_element)
                    xy4_block_alt.append(piy_element)
                    if n != xy4_order - 1:
                        xy4_block_alt.append(tau_element)
                xy4_block_alt.append(tauhalf_element)
                xy4_block_alt.append(pihalf2_alt_element)
                created_blocks.append(xy4_block_alt)
                xy4_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_mw_alt'.format(name, xy4_order), rotating_frame=True)
                xy4_ensemble_alt.append((xy4_block_alt.name, 0))
                created_ensembles.append(xy4_ensemble_alt)
                xy4_N_seq.append(xy4_ensemble_alt.name)
                count_length += self._get_ensemble_count_length(ensemble=xy4_ensemble_alt,
                                                                created_blocks=created_blocks)
                if self.sync_channel and xy4_order == order_array[-1]:
                    xy4_N_seq.append(sync_readout_ensemble.name)
                else:
                    xy4_N_seq.append(readout_ensemble.name)
                count_length += self._get_ensemble_count_length(ensemble=readout_ensemble,
                                                                created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        xy4_N_seq[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        xy4_N_seq.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        xy4_N_seq.measurement_information['alternating'] = alternating
        xy4_N_seq.measurement_information['laser_ignore_list'] = list()
        xy4_N_seq.measurement_information['controlled_variable'] = order_array
        xy4_N_seq.measurement_information['units'] = ('', '')
        xy4_N_seq.measurement_information['labels'] = ('XY4 Order', 'Signal')
        xy4_N_seq.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            xy4_N_seq.measurement_information['counting_length'] = self._get_ensemble_count_length(
                ensemble=readout_ensemble, created_blocks=created_blocks)
        else:
            xy4_N_seq.measurement_information['counting_length'] = count_length

        # append seq to created seq
        created_sequences.append(xy4_N_seq)
        return created_blocks, created_ensembles, created_sequences

    def generate_nuclear_cpmg(self, name='nuclear_cpmg', tau_start=1e-3, tau_max=20e-3,
                                    num_of_points=9, order=1, rf_amplitude=1e-3, rf_freq=493.9e3, rf_wait_time=30e-6,
                                    rf_pi_pulse=65e-6, add_wait=1e-3, min_wait_time=20e-3,
                                    mw_freq_2=3.0e9, alternating=True, rf_2_amplitude=1e-3, rf_2_freq=2.49e6, rf_2_tau=45e-6,
                                    tau_rabi_start=200e-9, tau_rabi_step=200e-9, rabi_points=16):
        """
            TODO: Note that it is only written in gated mode
            Choose mw_2 such that it is the other weak pi pulse.
            Rabi points must be even when alternating.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Get logarithmically spaced steps in multiples of tau_start.
        # Note that the number of points and the position of the last point can change here.
        k_array = np.unique(
            np.rint(np.logspace(0., np.log10(tau_max / tau_start), num_of_points)).astype(int))
        # Create measurement ticks
        tau_array = k_array * tau_start
        # Create control array
        if alternating:
            rabi_array = tau_max + np.arange(rabi_points/2+1)*tau_start
        else:
            rabi_array = tau_max + np.arange(rabi_points+1)
        control_array = np.append(tau_array, rabi_array)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        add_wait_element = self._get_idle_element(length=add_wait, increment=0)
        # due to ringing of rf pulse
        rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        laser_element_silent = self._get_laser_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        delay_element_silent = self._get_delay_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        # Create block and append to created_blocks list
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        silent_laser_block = PulseBlock(name='{0}_silent'.format(name))
        silent_laser_block.append(waiting_element)
        silent_laser_block.append(laser_element_silent)
        silent_laser_block.append(delay_element_silent)
        created_blocks.append(silent_laser_block)

        # creating nuclear swap block which should initialize the nuclear spin with additional laser before
        swap_block = PulseBlock(name='{0}_swap'.format(name))
        rf_element_swap = self._get_mw_element(length=rf_pi_pulse,
                                               increment=0,
                                               amp=rf_amplitude,
                                               freq=rf_freq,
                                               phase=0)
        swap_block.append(pix_element)
        swap_block.append(rf_element_swap)
        swap_block.append(rf_waiting_element)
        swap_block.append(pix_element)
        created_blocks.append(swap_block)

        # Create block ensemble and append block to it
        readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name),
                                                    rotating_frame=False)
        readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_block_ensemble)
        silent_laser_ensemble = PulseBlockEnsemble(name='{0}_silent'.format(name),
                                                   rotating_frame=False)
        silent_laser_ensemble.append((silent_laser_block.name, 0))
        created_ensembles.append(silent_laser_ensemble)
        nuclear_readout_block_ensemble = PulseBlockEnsemble(name='{0}_nuclear_readout'.format(name),
                                                            rotating_frame=False)
        nuclear_readout_block_ensemble.append((silent_laser_block.name, 0))
        nuclear_readout_block_ensemble.append((swap_block.name, 0))
        nuclear_readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(nuclear_readout_block_ensemble)

        # generation of additional wait time ensemble that gets repeated later
        add_wait_block = PulseBlock(name='{0}_add_wait'.format(name))
        add_wait_block.append(add_wait_element)
        created_blocks.append(add_wait_block)
        add_wait_ensemble = PulseBlockEnsemble(name='{0}_add_wait'.format(name),
                                               rotating_frame=False)
        add_wait_ensemble.append((add_wait_block.name, 0))
        created_ensembles.append(add_wait_ensemble)

        # create rf pi half element with rf 2
        rf_2_pi_half = self._get_mw_element(length=rf_2_tau/2,
                                            increment=0,
                                            amp=rf_2_amplitude,
                                            freq=rf_2_freq,
                                            phase=0)

        tau_rep_element = self._get_idle_element(length=tau_start, increment=0)
        #tau_rep_element_laser = self._get_laser_element(length=tau_start-self.laser_delay, increment=0)
        tau_rep_block = PulseBlock(name='{0}_tau_rep'.format(name))
        tau_rep_block.append(tau_rep_element)
        # tau_rep_block.append(tau_rep_element_laser)
        # tau_rep_block.append(delay_element_silent)
        created_blocks.append(tau_rep_block)
        tau_rep_ensemble = PulseBlockEnsemble(name='{0}_tau'.format(name), rotating_frame=False)
        tau_rep_ensemble.append((tau_rep_block.name, 0))
        created_ensembles.append(tau_rep_ensemble)
        # t2 = self._get_ensemble_count_length(tau_rep_ensemble, created_blocks) #TODO ensemble count length doesnt do what you expect
        t2 = tau_start
        t_rf = 1 / rf_2_freq
        t1 = rf_2_tau/2
        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        nuclear_ramsey_sequence = PulseSequence(name=name, rotating_frame=False)

        # here without rf waiting elements after rf s.t. observation of true tau values
        for k in k_array:
            rf_block_1 = PulseBlock(name='{0}{1}_rf1_tau'.format(name, k))
            rf_block_2 = PulseBlock(name='{0}{1}_rf2_tau'.format(name, k))

            rf_block_1.append(rf_2_pi_half)

            created_blocks.append(rf_block_1)

            rf_ensemble_1 = PulseBlockEnsemble(name='{0}{1}_tau1'.format(name, k), rotating_frame=False)
            rf_ensemble_1.append((rf_block_1.name, 0))
            created_ensembles.append(rf_ensemble_1)

            phase2 = ((((2*order+1)*t1 + order*2*k * t2) / t_rf) % 1) * 360
            rf_pi_half_phase_matched = self._get_mw_element(length=rf_2_tau / 2,
                                                            increment=0,
                                                            amp=rf_2_amplitude,
                                                            freq=rf_2_freq,
                                                            phase=phase2)

            rf_block_2.append(rf_pi_half_phase_matched)
            created_blocks.append(rf_block_2)
            rf_ensemble_2 = PulseBlockEnsemble(name='{0}{1}_tau2'.format(name, k), rotating_frame=False)
            rf_ensemble_2.append((rf_block_2.name, 0))
            created_ensembles.append(rf_ensemble_2)

            # calculate wait rep s.t. minimum wait time between rf pulses is waited
            add_wait_repetitions = 0 if k*tau_start > min_wait_time else int((min_wait_time-tau_start*k)*1e3)

            nuclear_ramsey_sequence.append(add_wait_ensemble.name)
            nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
            nuclear_ramsey_sequence.append(silent_laser_ensemble.name)
            nuclear_ramsey_sequence.append(rf_ensemble_1.name)
            for n in range(order):
                rf_block_3 = PulseBlock(name='{0}{1}{2}_rf3_tau'.format(name, k, n+1))
                nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = k - 1
                # create here the rf piy ensemble phases matched
                phase1 = ((((2*n+1)*t1 + (2*n+1) * k * t2) / t_rf) % 1) * 360 + 90
                rf_pi_phase_matched = self._get_mw_element(length=rf_2_tau,
                                                           increment=0,
                                                           amp=rf_2_amplitude,
                                                           freq=rf_2_freq,
                                                           phase=phase1)
                rf_block_3.append(rf_pi_phase_matched)
                created_blocks.append(rf_block_3)
                rf_ensemble_3 = PulseBlockEnsemble(name='{0}{1}{2}_tau3'.format(name, k, n+1), rotating_frame=False)
                rf_ensemble_3.append((rf_block_3.name, 0))
                created_ensembles.append(rf_ensemble_3)

                nuclear_ramsey_sequence.append(rf_ensemble_3.name)
                nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = k - 1
            nuclear_ramsey_sequence.append(rf_ensemble_2.name)
            nuclear_ramsey_sequence.append(add_wait_ensemble.name)
            nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
            nuclear_ramsey_sequence.append(nuclear_readout_block_ensemble.name)
            if alternating:
                # rf_block_1_alt is the same as rf_block_1
                rf_block_2_alt = PulseBlock(name='{0}{1}_rf2_tau_alt'.format(name, k))

                rf_3pi_half_phase_matched = self._get_mw_element(length=rf_2_tau / 2,
                                                                 increment=0,
                                                                 amp=rf_2_amplitude,
                                                                 freq=rf_2_freq,
                                                                 phase=phase2-180)

                rf_block_2_alt.append(rf_3pi_half_phase_matched)
                created_blocks.append(rf_block_2_alt)
                rf_ensemble_2_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, k), rotating_frame=False)
                rf_ensemble_2_alt.append((rf_block_2_alt.name, 0))
                created_ensembles.append(rf_ensemble_2_alt)

                nuclear_ramsey_sequence.append(add_wait_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
                nuclear_ramsey_sequence.append(silent_laser_ensemble.name)
                nuclear_ramsey_sequence.append(rf_ensemble_1.name)
                for n in range(order):
                    rf_block_4 = PulseBlock(name='{0}{1}{2}_rf4_tau'.format(name, k,n+1))
                    nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                    nuclear_ramsey_sequence[-1].repetitions = k - 1
                    # create here the rf piy ensemble phases matched
                    phase1 = ((((2 * n + 1) * t1 + (2 * n + 1) * k * t2) / t_rf) % 1) * 360 + 90
                    rf_pi_phase_matched = self._get_mw_element(length=rf_2_tau,
                                                               increment=0,
                                                               amp=rf_2_amplitude,
                                                               freq=rf_2_freq,
                                                               phase=phase1)
                    rf_block_4.append(rf_pi_phase_matched)
                    created_blocks.append(rf_block_4)
                    rf_ensemble_4 = PulseBlockEnsemble(name='{0}{1}{2}_tau4_alt'.format(name, k, n+1), rotating_frame=False)
                    rf_ensemble_4.append((rf_block_4.name, 0))
                    created_ensembles.append(rf_ensemble_4)

                    nuclear_ramsey_sequence.append(rf_ensemble_4.name)
                    nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                    nuclear_ramsey_sequence[-1].repetitions = k - 1

                nuclear_ramsey_sequence.append(rf_ensemble_2_alt.name)
                nuclear_ramsey_sequence.append(add_wait_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
                nuclear_ramsey_sequence.append(nuclear_readout_block_ensemble.name)

        # contrast pulses
        tau_rabi_array = tau_rabi_start + np.arange(rabi_points) * tau_rabi_step
        for tau in tau_rabi_array:
            pix_element = self._get_mw_element(length=tau,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0)

            pix_element_2 = self._get_mw_element(length=tau,
                                                 increment=0,
                                                 amp=self.microwave_amplitude,
                                                 freq=mw_freq_2,
                                                 phase=0)

            # generation of contrast ensembles
            contrast_block_1 = PulseBlock(name='{0}_contrast1'.format(tau))
            contrast_block_1.append(pix_element)
            contrast_block_1.append(pix_element_2)
            created_blocks.append(contrast_block_1)

            contrast_ensemble_1 = PulseBlockEnsemble(name='{0}_contrast1'.format(tau), rotating_frame=False)
            contrast_ensemble_1.append((contrast_block_1.name, 0))
            created_ensembles.append(contrast_ensemble_1)

            nuclear_ramsey_sequence.append(contrast_ensemble_1.name)
            nuclear_ramsey_sequence.append(readout_block_ensemble.name)

        if alternating:
            nuclear_ramsey_sequence.append(readout_block_ensemble.name)
        nuclear_ramsey_sequence.append(nuclear_readout_block_ensemble.name)
        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        nuclear_ramsey_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        nuclear_ramsey_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = 2 * len(tau_array) + rabi_points + 2 if alternating else len(tau_array) + rabi_points + 1
        nuclear_ramsey_sequence.measurement_information['alternating'] = alternating
        nuclear_ramsey_sequence.measurement_information['laser_ignore_list'] = list()
        nuclear_ramsey_sequence.measurement_information['controlled_variable'] = control_array
        nuclear_ramsey_sequence.measurement_information['units'] = ('s', '')
        nuclear_ramsey_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        nuclear_ramsey_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            nuclear_ramsey_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                readout_block_ensemble, created_blocks)

        # Append PulseSequence to created_sequences list
        created_sequences.append(nuclear_ramsey_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_nuclear_xy8(self, name='nuclear_xy8', tau_start=1e-3, tau_max=20e-3,
                                   num_of_points=9, rf_amplitude=1e-3, rf_freq=493.9e3, rf_wait_time=30e-6,
                                   rf_pi_pulse=65e-6, add_wait=1e-3, min_wait_time=20e-3,
                                   mw_freq_2=3.0e9, alternating=True, rf_2_amplitude=1e-3, rf_2_freq=2.49e6, rf_2_tau=45e-6,
                                   tau_rabi_start=200e-9, tau_rabi_step=200e-9, rabi_points=16):
        """
            TODO: Note that it is only written in gated mode
            TODO: Only order=1
            Choose mw_2 such that it is the other weak pi pulse.
            Rabi points must be even when alternating.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Get logarithmically spaced steps in multiples of tau_start.
        # Note that the number of points and the position of the last point can change here.
        k_array = np.unique(
            np.rint(np.logspace(0., np.log10(tau_max / tau_start), num_of_points)).astype(int))
        # Create measurement ticks
        tau_array = k_array * tau_start
        # Create control array
        if alternating:
            rabi_array = tau_max + np.arange(rabi_points/2+1)*tau_start
        else:
            rabi_array = tau_max + np.arange(rabi_points+1)
        control_array = np.append(tau_array, rabi_array)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        add_wait_element = self._get_idle_element(length=add_wait, increment=0)
        # due to ringing of rf pulse
        rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        laser_element_silent = self._get_laser_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        delay_element_silent = self._get_delay_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        # Create block and append to created_blocks list
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        silent_laser_block = PulseBlock(name='{0}_silent'.format(name))
        silent_laser_block.append(waiting_element)
        silent_laser_block.append(laser_element_silent)
        silent_laser_block.append(delay_element_silent)
        created_blocks.append(silent_laser_block)

        # creating nuclear swap block which should initialize the nuclear spin with additional laser before
        swap_block = PulseBlock(name='{0}_swap'.format(name))
        rf_element_swap = self._get_mw_element(length=rf_pi_pulse,
                                               increment=0,
                                               amp=rf_amplitude,
                                               freq=rf_freq,
                                               phase=0)
        swap_block.append(pix_element)
        swap_block.append(rf_element_swap)
        swap_block.append(rf_waiting_element)
        swap_block.append(pix_element)
        created_blocks.append(swap_block)

        # Create block ensemble and append block to it
        readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name),
                                                    rotating_frame=False)
        readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_block_ensemble)
        silent_laser_ensemble = PulseBlockEnsemble(name='{0}_silent'.format(name),
                                                   rotating_frame=False)
        silent_laser_ensemble.append((silent_laser_block.name, 0))
        created_ensembles.append(silent_laser_ensemble)
        nuclear_readout_block_ensemble = PulseBlockEnsemble(name='{0}_nuclear_readout'.format(name),
                                                            rotating_frame=False)
        nuclear_readout_block_ensemble.append((silent_laser_block.name, 0))
        nuclear_readout_block_ensemble.append((swap_block.name, 0))
        nuclear_readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(nuclear_readout_block_ensemble)

        # generation of additional wait time ensemble that gets repeated later
        add_wait_block = PulseBlock(name='{0}_add_wait'.format(name))
        add_wait_block.append(add_wait_element)
        created_blocks.append(add_wait_block)
        add_wait_ensemble = PulseBlockEnsemble(name='{0}_add_wait'.format(name),
                                               rotating_frame=False)
        add_wait_ensemble.append((add_wait_block.name, 0))
        created_ensembles.append(add_wait_ensemble)

        # create rf pi half element with rf 2
        rf_2_pi_half = self._get_mw_element(length=rf_2_tau/2,
                                            increment=0,
                                            amp=rf_2_amplitude,
                                            freq=rf_2_freq,
                                            phase=0)

        tau_rep_element = self._get_idle_element(length=tau_start, increment=0)
        #tau_rep_element_laser = self._get_laser_element(length=tau_start-self.laser_delay, increment=0)
        tau_rep_block = PulseBlock(name='{0}_tau_rep'.format(name))
        tau_rep_block.append(tau_rep_element)
        # tau_rep_block.append(tau_rep_element_laser)
        # tau_rep_block.append(delay_element_silent)
        created_blocks.append(tau_rep_block)
        tau_rep_ensemble = PulseBlockEnsemble(name='{0}_tau'.format(name), rotating_frame=False)
        tau_rep_ensemble.append((tau_rep_block.name, 0))
        created_ensembles.append(tau_rep_ensemble)
        # t2 = self._get_ensemble_count_length(tau_rep_ensemble, created_blocks) #TODO ensemble count length doesnt do what you expect
        t2 = tau_start
        t_rf = 1 / rf_2_freq
        t1 = rf_2_tau/2
        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        nuclear_ramsey_sequence = PulseSequence(name=name, rotating_frame=False)

        # here without rf waiting elements after rf s.t. observation of true tau values
        for k in k_array:
            rf_block_1 = PulseBlock(name='{0}{1}_rf1_tau'.format(name, k))
            rf_block_2 = PulseBlock(name='{0}{1}_rf2_tau'.format(name, k))

            rf_block_1.append(rf_2_pi_half)

            created_blocks.append(rf_block_1)

            rf_ensemble_1 = PulseBlockEnsemble(name='{0}{1}_tau1'.format(name, k), rotating_frame=False)
            rf_ensemble_1.append((rf_block_1.name, 0))
            created_ensembles.append(rf_ensemble_1)

            phase2 = ((((8*2+1)*t1 + 8*2*k * t2) / t_rf) % 1) * 360
            rf_pi_half_phase_matched = self._get_mw_element(length=rf_2_tau / 2,
                                                            increment=0,
                                                            amp=rf_2_amplitude,
                                                            freq=rf_2_freq,
                                                            phase=phase2)

            rf_block_2.append(rf_pi_half_phase_matched)
            created_blocks.append(rf_block_2)
            rf_ensemble_2 = PulseBlockEnsemble(name='{0}{1}_tau2'.format(name, k), rotating_frame=False)
            rf_ensemble_2.append((rf_block_2.name, 0))
            created_ensembles.append(rf_ensemble_2)

            # calculate wait rep s.t. minimum wait time between rf pulses is waited
            add_wait_repetitions = 0 if k*tau_start > min_wait_time else int((min_wait_time-tau_start*k)*1e3)

            nuclear_ramsey_sequence.append(add_wait_ensemble.name)
            nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
            nuclear_ramsey_sequence.append(silent_laser_ensemble.name)
            nuclear_ramsey_sequence.append(rf_ensemble_1.name)
            for n in range(8):  # TODO currently only order 1
                rf_block_3 = PulseBlock(name='{0}{1}{2}_rf3_tau'.format(name, k, n+1))
                nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = k - 1
                # create here the rf pix,y ensemble phases matched
                if n == 1 or n == 3 or n == 4 or n == 6:  # pi y element
                    phase1 = ((((2 * n + 1) * t1 + (2 * n + 1) * k * t2) / t_rf) % 1) * 360 + 90
                else:  # pi x element
                    phase1 = ((((2 * n + 1) * t1 + (2 * n + 1) * k * t2) / t_rf) % 1) * 360
                rf_pi_phase_matched = self._get_mw_element(length=rf_2_tau,
                                                           increment=0,
                                                           amp=rf_2_amplitude,
                                                           freq=rf_2_freq,
                                                           phase=phase1)
                rf_block_3.append(rf_pi_phase_matched)
                created_blocks.append(rf_block_3)
                rf_ensemble_3 = PulseBlockEnsemble(name='{0}{1}{2}_tau3'.format(name, k, n+1), rotating_frame=False)
                rf_ensemble_3.append((rf_block_3.name, 0))
                created_ensembles.append(rf_ensemble_3)

                nuclear_ramsey_sequence.append(rf_ensemble_3.name)
                nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = k - 1
            nuclear_ramsey_sequence.append(rf_ensemble_2.name)
            nuclear_ramsey_sequence.append(add_wait_ensemble.name)
            nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
            nuclear_ramsey_sequence.append(nuclear_readout_block_ensemble.name)
            if alternating:
                # rf_block_1_alt is the same as rf_block_1
                rf_block_2_alt = PulseBlock(name='{0}{1}_rf2_tau_alt'.format(name, k))

                rf_3pi_half_phase_matched = self._get_mw_element(length=rf_2_tau / 2,
                                                                 increment=0,
                                                                 amp=rf_2_amplitude,
                                                                 freq=rf_2_freq,
                                                                 phase=phase2-180)

                rf_block_2_alt.append(rf_3pi_half_phase_matched)
                created_blocks.append(rf_block_2_alt)
                rf_ensemble_2_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, k), rotating_frame=False)
                rf_ensemble_2_alt.append((rf_block_2_alt.name, 0))
                created_ensembles.append(rf_ensemble_2_alt)

                nuclear_ramsey_sequence.append(add_wait_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
                nuclear_ramsey_sequence.append(silent_laser_ensemble.name)
                nuclear_ramsey_sequence.append(rf_ensemble_1.name)
                for n in range(8):  # TODO currently only order 1
                    rf_block_4 = PulseBlock(name='{0}{1}{2}_rf4_tau'.format(name, k, n+1))
                    nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                    nuclear_ramsey_sequence[-1].repetitions = k - 1
                    # create here the rf pix and y ensembles phase matched
                    if n == 1 or n == 3 or n == 4 or n == 6:  # pi y element
                        phase1 = ((((2 * n + 1) * t1 + (2 * n + 1) * k * t2) / t_rf) % 1) * 360 + 90
                    else:  # pi x element
                        phase1 = ((((2 * n + 1) * t1 + (2 * n + 1) * k * t2) / t_rf) % 1) * 360
                    rf_pi_phase_matched = self._get_mw_element(length=rf_2_tau,
                                                               increment=0,
                                                               amp=rf_2_amplitude,
                                                               freq=rf_2_freq,
                                                               phase=phase1)
                    rf_block_4.append(rf_pi_phase_matched)
                    created_blocks.append(rf_block_4)
                    rf_ensemble_4 = PulseBlockEnsemble(name='{0}{1}{2}_tau4_alt'.format(name, k, n+1), rotating_frame=False)
                    rf_ensemble_4.append((rf_block_4.name, 0))
                    created_ensembles.append(rf_ensemble_4)

                    nuclear_ramsey_sequence.append(rf_ensemble_4.name)
                    nuclear_ramsey_sequence.append(tau_rep_ensemble.name)
                    nuclear_ramsey_sequence[-1].repetitions = k - 1

                nuclear_ramsey_sequence.append(rf_ensemble_2_alt.name)
                nuclear_ramsey_sequence.append(add_wait_ensemble.name)
                nuclear_ramsey_sequence[-1].repetitions = add_wait_repetitions
                nuclear_ramsey_sequence.append(nuclear_readout_block_ensemble.name)

        # contrast pulses
        tau_rabi_array = tau_rabi_start + np.arange(rabi_points) * tau_rabi_step
        for tau in tau_rabi_array:
            pix_element = self._get_mw_element(length=tau,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0)

            pix_element_2 = self._get_mw_element(length=tau,
                                                 increment=0,
                                                 amp=self.microwave_amplitude,
                                                 freq=mw_freq_2,
                                                 phase=0)

            # generation of contrast ensembles
            contrast_block_1 = PulseBlock(name='{0}_contrast1'.format(tau))
            contrast_block_1.append(pix_element)
            contrast_block_1.append(pix_element_2)
            created_blocks.append(contrast_block_1)

            contrast_ensemble_1 = PulseBlockEnsemble(name='{0}_contrast1'.format(tau), rotating_frame=False)
            contrast_ensemble_1.append((contrast_block_1.name, 0))
            created_ensembles.append(contrast_ensemble_1)

            nuclear_ramsey_sequence.append(contrast_ensemble_1.name)
            nuclear_ramsey_sequence.append(readout_block_ensemble.name)

        if alternating:
            nuclear_ramsey_sequence.append(readout_block_ensemble.name)
        nuclear_ramsey_sequence.append(nuclear_readout_block_ensemble.name)
        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        nuclear_ramsey_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        nuclear_ramsey_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = 2 * len(tau_array) + rabi_points + 2 if alternating else len(tau_array) + rabi_points + 1
        nuclear_ramsey_sequence.measurement_information['alternating'] = alternating
        nuclear_ramsey_sequence.measurement_information['laser_ignore_list'] = list()
        nuclear_ramsey_sequence.measurement_information['controlled_variable'] = control_array
        nuclear_ramsey_sequence.measurement_information['units'] = ('s', '')
        nuclear_ramsey_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        nuclear_ramsey_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            nuclear_ramsey_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                readout_block_ensemble, created_blocks)

        # Append PulseSequence to created_sequences list
        created_sequences.append(nuclear_ramsey_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_xy8_tau_seq(self, name='xy8_tau_seq', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy8_order=4, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # calculate "real" start length of tau due to finite pi-pulse length
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)
        # get tau array for measurement ticks
        tau_array = tau_pspacing_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shifted pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pi3half_element = self._get_mw_element(length=self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=180)
        else:
            pi3half_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=0)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)

        # Create block and append to created_blocks list
        xy8_seq_block_readout = PulseBlock(name='{0}_readout'.format(name))
        xy8_seq_block_readout.append(waiting_element) # TODO as in my opinion this should be here, but is not for the normal xy8
        xy8_seq_block_readout.append(laser_element)
        xy8_seq_block_readout.append(delay_element)
        created_blocks.append(xy8_seq_block_readout)
        # Create block ensemble
        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((xy8_seq_block_readout.name, 0))
        created_ensembles.append(readout_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            sync_element = self._get_sync_element()
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name), rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the pulse sequence
        xy8_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        # Append PulseBlockEnsembles
        for tau in tau_array:
            xy8_seq_block = PulseBlock(name='{0}{1}_tau'.format(name, tau))
            tauhalf_element = self._get_idle_element(length=tau/2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy8_seq_block.append(pihalf_element)
            xy8_seq_block.append(tauhalf_element)
            for n in range(xy8_order):
                xy8_seq_block.append(pix_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(pix_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(pix_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(piy_element)
                xy8_seq_block.append(tau_element)
                xy8_seq_block.append(pix_element)
                if n != xy8_order - 1:
                    xy8_seq_block.append(tau_element)
            xy8_seq_block.append(tauhalf_element)
            xy8_seq_block.append(pihalf_element)
            created_blocks.append(xy8_seq_block)
            tau_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=True)
            tau_ensemble.append((xy8_seq_block.name, 0))
            created_ensembles.append(tau_ensemble)

            if alternating:
                xy8_seq_block_alt = PulseBlock(name='{0}{1}_tau_alt'.format(name, tau))
                xy8_seq_block_alt.append(pihalf_element)
                xy8_seq_block_alt.append(tauhalf_element)
                for n in range(xy8_order):
                    xy8_seq_block_alt.append(pix_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(pix_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(pix_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(piy_element)
                    xy8_seq_block_alt.append(tau_element)
                    xy8_seq_block_alt.append(pix_element)
                    if n != xy8_order - 1:
                        xy8_seq_block_alt.append(tau_element)
                xy8_seq_block_alt.append(tauhalf_element)
                xy8_seq_block_alt.append(pi3half_element)
                created_blocks.append(xy8_seq_block_alt)
                tau_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, tau), rotating_frame=True)
                tau_ensemble_alt.append((xy8_seq_block_alt.name, 0))
                created_ensembles.append(tau_ensemble_alt)

            xy8_sequence.append(tau_ensemble.name)
            if self.sync_channel and tau == tau_array[-1] and not alternating:
                xy8_sequence.append(sync_readout_ensemble.name)
            else:
                xy8_sequence.append(readout_ensemble.name)
            if alternating:
                xy8_sequence.append(tau_ensemble_alt.name)
                if self.sync_channel and tau == tau_array[-1]:
                    xy8_sequence.append(sync_readout_ensemble.name)
                else:
                    xy8_sequence.append(readout_ensemble.name)

            count_length += self._get_ensemble_count_length(ensemble=tau_ensemble, created_blocks=created_blocks)
            if alternating:
                count_length += 2 * self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)
                count_length += self._get_ensemble_count_length(ensemble=tau_ensemble_alt, created_blocks=created_blocks)
            else:
                count_length += self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)

        # Make sequence loop infinitely by setting go_to parameter of the last sequence to the first step
        xy8_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        xy8_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        xy8_sequence.measurement_information['alternating'] = alternating
        xy8_sequence.measurement_information['laser_ignore_list'] = list()
        xy8_sequence.measurement_information['controlled_variable'] = tau_array
        xy8_sequence.measurement_information['units'] = ('s', '')
        xy8_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        xy8_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            xy8_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                ensemble=readout_ensemble, created_blocks=created_blocks)
        else:
            xy8_sequence.measurement_information['counting_length'] = count_length

        # append Pulse sequence to created_sequence list
        created_sequences.append(xy8_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_xy4_tau_seq(self, name='xy4_tau_seq', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy4_order=4, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # calculate "real" start length of tau due to finite pi-pulse length
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)
        # get tau array for measurement ticks
        tau_array = tau_pspacing_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shifted pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pi3half_element = self._get_mw_element(length=self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=180)
        else:
            pi3half_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                   increment=0,
                                                   amp=self.microwave_amplitude,
                                                   freq=self.microwave_frequency,
                                                   phase=0)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)

        # Create block and append to created_blocks list
        xy4_seq_block_readout = PulseBlock(name='{0}_readout'.format(name))
        xy4_seq_block_readout.append(waiting_element) # TODO as in my opinion this should be here
        xy4_seq_block_readout.append(laser_element)
        xy4_seq_block_readout.append(delay_element)
        created_blocks.append(xy4_seq_block_readout)
        # Create block ensemble
        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((xy4_seq_block_readout.name, 0))
        created_ensembles.append(readout_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            sync_element = self._get_sync_element()
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name), rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the pulse sequence
        xy4_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        # Append PulseBlockEnsembles
        for tau in tau_array:
            xy4_seq_block = PulseBlock(name='{0}{1}_tau'.format(name, tau))
            tauhalf_element = self._get_idle_element(length=tau/2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy4_seq_block.append(pihalf_element)
            xy4_seq_block.append(tauhalf_element)
            for n in range(xy4_order):
                xy4_seq_block.append(pix_element)
                xy4_seq_block.append(tau_element)
                xy4_seq_block.append(piy_element)
                xy4_seq_block.append(tau_element)
                xy4_seq_block.append(pix_element)
                xy4_seq_block.append(tau_element)
                xy4_seq_block.append(piy_element)
                if n != xy4_order - 1:
                    xy4_seq_block.append(tau_element)
            xy4_seq_block.append(tauhalf_element)
            xy4_seq_block.append(pihalf_element)
            created_blocks.append(xy4_seq_block)
            tau_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=True)
            tau_ensemble.append((xy4_seq_block.name, 0))
            created_ensembles.append(tau_ensemble)

            if alternating:
                xy4_seq_block_alt = PulseBlock(name='{0}{1}_tau_alt'.format(name, tau))
                xy4_seq_block_alt.append(pihalf_element)
                xy4_seq_block_alt.append(tauhalf_element)
                for n in range(xy4_order):
                    xy4_seq_block_alt.append(pix_element)
                    xy4_seq_block_alt.append(tau_element)
                    xy4_seq_block_alt.append(piy_element)
                    xy4_seq_block_alt.append(tau_element)
                    xy4_seq_block_alt.append(pix_element)
                    xy4_seq_block_alt.append(tau_element)
                    xy4_seq_block_alt.append(piy_element)
                    if n != xy4_order - 1:
                        xy4_seq_block_alt.append(tau_element)
                xy4_seq_block_alt.append(tauhalf_element)
                xy4_seq_block_alt.append(pi3half_element)
                created_blocks.append(xy4_seq_block_alt)
                tau_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, tau), rotating_frame=True)
                tau_ensemble_alt.append((xy4_seq_block_alt.name, 0))
                created_ensembles.append(tau_ensemble_alt)

            xy4_sequence.append(tau_ensemble.name)
            if self.sync_channel and tau == tau_array[-1] and not alternating:
                xy4_sequence.append(sync_readout_ensemble.name)
            else:
                xy4_sequence.append(readout_ensemble.name)
            if alternating:
                xy4_sequence.append(tau_ensemble_alt.name)
                if self.sync_channel and tau == tau_array[-1]:
                    xy4_sequence.append(sync_readout_ensemble.name)
                else:
                    xy4_sequence.append(readout_ensemble.name)

            count_length += self._get_ensemble_count_length(ensemble=tau_ensemble, created_blocks=created_blocks)
            if alternating:
                count_length += 2 * self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)
                count_length += self._get_ensemble_count_length(ensemble=tau_ensemble_alt, created_blocks=created_blocks)
            else:
                count_length += self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)

        # Make sequence loop infinitely by setting go_to parameter of the last sequence to the first step
        xy4_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        xy4_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        xy4_sequence.measurement_information['alternating'] = alternating
        xy4_sequence.measurement_information['laser_ignore_list'] = list()
        xy4_sequence.measurement_information['controlled_variable'] = tau_array
        xy4_sequence.measurement_information['units'] = ('s', '')
        xy4_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        xy4_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            xy4_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                ensemble=readout_ensemble, created_blocks=created_blocks)
        else:
            xy4_sequence.measurement_information['counting_length'] = count_length

        # append Pulse sequence to created_sequence list
        created_sequences.append(xy4_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_xy4_N_seq(self, name='xy4_N_seq', tau_spacing=0.1e6, num_of_points=50, xy4_order_start=4, order_step=1,
                           phase1=0, phase2=0, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get order array for measurement ticks
        order_array = xy4_order_start + np.arange(num_of_points) * order_step
        # get tau array from freq array
        # tau = 1 / (2 * freq_start)
        # calculate "real" tau array (finite pi-pulse length) ?
        # real_tau = tau - self.rabi_period / 2
        # Convert back to frequency in order to account for clipped values
        #freq_real = 1 / (2 * (real_tau + self.rabi_period / 2))

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        xy4_N_seq = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0

        # Create the readout PulseBlockEnsemble
        # Get necessary PulseBlockElements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        # Create PulseBlock and append PulseBlockElements
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element) #TODO
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)
        # Create PulseBlockEnsemble and append block to it
        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element) #TODO
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        pihalf1_element = self._get_mw_element(length=self.rabi_period / 4,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=phase1)
        pihalf2_element = self._get_mw_element(length=self.rabi_period / 4,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=phase2)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
        if self.microwave_channel.startswith('a'):
            pihalf2_alt_element = self._get_mw_element(length=self.rabi_period / 4,
                                                       increment=0,
                                                       amp=self.microwave_amplitude,
                                                       freq=self.microwave_frequency,
                                                       phase=phase2 + 180)
        else:
            pihalf2_alt_element = self._get_mw_element(length=3 * self.rabi_period / 4,
                                                       increment=0,
                                                       amp=self.microwave_amplitude,
                                                       freq=self.microwave_frequency,
                                                       phase=phase2)
        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)
        piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=90)
        tauhalf_element = self._get_idle_element(length=tau_spacing / 2, increment=0)
        tau_element = self._get_idle_element(length=tau_spacing, increment=0)

        # Create block and append to created_blocks list
        for ii, xy4_order in enumerate(order_array):
            xy4_block = PulseBlock(name='{0}{1}_mw'.format(name, xy4_order))
            xy4_block.append(pihalf1_element)
            xy4_block.append(tauhalf_element)
            for n in range(xy4_order):
                xy4_block.append(pix_element)
                xy4_block.append(tau_element)
                xy4_block.append(piy_element)
                xy4_block.append(tau_element)
                xy4_block.append(pix_element)
                xy4_block.append(tau_element)
                xy4_block.append(piy_element)
                if n != xy4_order - 1:
                    xy4_block.append(tau_element)
            xy4_block.append(tauhalf_element)
            xy4_block.append(pihalf2_element)
            created_blocks.append(xy4_block)
            xy4_ensemble = PulseBlockEnsemble(name='{0}{1}_mw'.format(name, xy4_order), rotating_frame=True)
            xy4_ensemble.append((xy4_block.name, 0))
            created_ensembles.append(xy4_ensemble)
            xy4_N_seq.append(xy4_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=xy4_ensemble, created_blocks=created_blocks)
            if self.sync_channel and xy4_order == order_array[-1] and not alternating:
                xy4_N_seq.append(sync_readout_ensemble.name)
            else:
                xy4_N_seq.append(readout_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=readout_ensemble, created_blocks=created_blocks)
            if alternating:
                xy4_block_alt = PulseBlock(name='{0}{1}_mw_alt'.format(name, xy4_order))
                xy4_block_alt.append(pihalf1_element)
                xy4_block_alt.append(tauhalf_element)
                for n in range(xy4_order):
                    xy4_block_alt.append(pix_element)
                    xy4_block_alt.append(tau_element)
                    xy4_block_alt.append(piy_element)
                    xy4_block_alt.append(tau_element)
                    xy4_block_alt.append(pix_element)
                    xy4_block_alt.append(tau_element)
                    xy4_block_alt.append(piy_element)
                    if n != xy4_order - 1:
                        xy4_block_alt.append(tau_element)
                xy4_block_alt.append(tauhalf_element)
                xy4_block_alt.append(pihalf2_alt_element)
                created_blocks.append(xy4_block_alt)
                xy4_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_mw_alt'.format(name, xy4_order), rotating_frame=True)
                xy4_ensemble_alt.append((xy4_block_alt.name, 0))
                created_ensembles.append(xy4_ensemble_alt)
                xy4_N_seq.append(xy4_ensemble_alt.name)
                count_length += self._get_ensemble_count_length(ensemble=xy4_ensemble_alt,
                                                                created_blocks=created_blocks)
                if self.sync_channel and xy4_order == order_array[-1]:
                    xy4_N_seq.append(sync_readout_ensemble.name)
                else:
                    xy4_N_seq.append(readout_ensemble.name)
                count_length += self._get_ensemble_count_length(ensemble=readout_ensemble,
                                                                created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        xy4_N_seq[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        xy4_N_seq.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        xy4_N_seq.measurement_information['alternating'] = alternating
        xy4_N_seq.measurement_information['laser_ignore_list'] = list()
        xy4_N_seq.measurement_information['controlled_variable'] = order_array
        xy4_N_seq.measurement_information['units'] = ('', '')
        xy4_N_seq.measurement_information['labels'] = ('XY4 Order', 'Signal')
        xy4_N_seq.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            xy4_N_seq.measurement_information['counting_length'] = self._get_ensemble_count_length(
                ensemble=readout_ensemble, created_blocks=created_blocks)
        else:
            xy4_N_seq.measurement_information['counting_length'] = count_length

        # append seq to created seq
        created_sequences.append(xy4_N_seq)
        return created_blocks, created_ensembles, created_sequences
