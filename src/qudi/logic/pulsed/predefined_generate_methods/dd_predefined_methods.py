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
