# -*- coding: utf-8 -*-

"""
This file contains the Qudi Predefined Methods for sequence generator

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
from qudi.logic.pulsed.sampling_functions import SamplingFunctions
from qudi.util.helpers import csv_2_list

"""
General Pulse Creation Procedure:
=================================
- Create at first each PulseBlockElement object
- add all PulseBlockElement object to a list and combine them to a
  PulseBlock object.
- Create all needed PulseBlock object with that idea, that means
  PulseBlockElement objects which are grouped to PulseBlock objects.
- Create from the PulseBlock objects a PulseBlockEnsemble object.
- If needed and if possible, combine the created PulseBlockEnsemble objects
  to the highest instance together in a PulseSequence object.
"""


class BasicPredefinedGenerator(PredefinedGeneratorBase):
    """

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    ################################################################################################
    #                             Generation methods for waveforms                                 #
    ################################################################################################
    def generate_laser_on(self, name='laser_on', length=3.0e-6):
        """ Generates Laser on.

        @param str name: Name of the PulseBlockEnsemble
        @param float length: laser duration in seconds

        @return object: the generated PulseBlockEnsemble object.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # create the laser element
        laser_element = self._get_laser_element(length=length, increment=0)
        # Create block and append to created_blocks list
        laser_block = PulseBlock(name=name)
        laser_block.append(laser_element)
        created_blocks.append(laser_block)
        # Create block ensemble and append to created_ensembles list
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((laser_block.name, 0))
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_laser_mw_on(self, name='laser_mw_on', length=3.0e-6):
        """ General generation method for laser on and microwave on generation.

        @param string name: Name of the PulseBlockEnsemble to be generated
        @param float length: Length of the PulseBlockEnsemble in seconds

        @return object: the generated PulseBlockEnsemble object.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # create the laser_mw element
        laser_mw_element = self._get_mw_laser_element(length=length,
                                                      increment=0,
                                                      amp=self.microwave_amplitude,
                                                      freq=self.microwave_frequency,
                                                      phase=0)
        # Create block and append to created_blocks list
        laser_mw_block = PulseBlock(name=name)
        laser_mw_block.append(laser_mw_element)
        created_blocks.append(laser_mw_block)
        # Create block ensemble and append to created_ensembles list
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((laser_mw_block.name, 0))
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_two_digital_high(self, name='digital_high', length=3.0e-6,
                                  digital_channel1='d_ch1', digital_channel2='d_ch1'):
        """ General generation method for laser on and microwave on generation.

        @param string name: Name of the PulseBlockEnsemble to be generated
        @param float length: Length of the PulseBlockEnsemble in seconds

        @return object: the generated PulseBlockEnsemble object.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        digital_channels = list([digital_channel1, digital_channel2])
        # create the laser_mw element
        trigger_element = self._get_trigger_element(length=length,
                                                    increment=0,
                                                    channels=list(digital_channels))

        # Create block and append to created_blocks list
        laser_mw_block = PulseBlock(name=name)
        laser_mw_block.append(trigger_element)
        created_blocks.append(laser_mw_block)
        # Create block ensemble and append to created_ensembles list
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((laser_mw_block.name, 0))
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_n_digital_high(self, name='digital_high', length=3.0e-6,
                                  digital_channels="1,2,3,4"):
        """ General generation method for laser on and microwave on generation.

        @param string name: Name of the PulseBlockEnsemble to be generated
        @param float length: Length of the PulseBlockEnsemble in seconds
        @params string digital_channels: Comma separated channel numbers.

        @return object: the generated PulseBlockEnsemble object.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        d_ch_nums = csv_2_list(digital_channels)
        digital_channels = ["d_ch{}".format(int(i)) for i in d_ch_nums]
        # create the laser_mw element
        trigger_element = self._get_trigger_element(length=length,
                                                    increment=0,
                                                    channels=list(digital_channels))

        # Create block and append to created_blocks list
        laser_mw_block = PulseBlock(name=name)
        laser_mw_block.append(trigger_element)
        created_blocks.append(laser_mw_block)
        # Create block ensemble and append to created_ensembles list
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((laser_mw_block.name, 0))
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences


    def generate_idle(self, name='idle', length=3.0e-6):
        """ Generate just a simple idle ensemble.

        @param str name: Name of the PulseBlockEnsemble to be generated
        @param float length: Length of the PulseBlockEnsemble in seconds

        @return object: the generated PulseBlockEnsemble object.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # create the laser_mw element
        idle_element = self._get_idle_element(length=length, increment=0)
        # Create block and append to created_blocks list
        idle_block = PulseBlock(name=name)
        idle_block.append(idle_element)
        created_blocks.append(idle_block)
        # Create block ensemble and append to created_ensembles list
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((idle_block.name, 0))
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_rabi(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9, num_of_points=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # create the laser_mw element
        mw_element = self._get_mw_element(length=tau_start,
                                          increment=tau_step,
                                          amp=self.microwave_amplitude,
                                          freq=self.microwave_frequency,
                                          phase=0)
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        rabi_block = PulseBlock(name=name)
        rabi_block.append(mw_element)
        rabi_block.append(laser_element)
        rabi_block.append(delay_element)
        rabi_block.append(waiting_element)
        created_blocks.append(rabi_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((rabi_block.name, num_of_points - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = False
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = num_of_points
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created_ensembles list
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_pulsedodmr(self, name='pulsedODMR', freq_start=2870.0e6, freq_step=0.2e6,
                            num_of_points=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Create frequency array
        freq_array = freq_start + np.arange(num_of_points) * freq_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        pulsedodmr_block = PulseBlock(name=name)
        for mw_freq in freq_array:
            mw_element = self._get_mw_element(length=self.rabi_period / 2,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=mw_freq,
                                              phase=0)
            pulsedodmr_block.append(mw_element)
            pulsedodmr_block.append(laser_element)
            pulsedodmr_block.append(delay_element)
            pulsedodmr_block.append(waiting_element)
        created_blocks.append(pulsedodmr_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((pulsedodmr_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = False
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['labels'] = ('Frequency', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = num_of_points
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_ramsey(self, name='ramsey', tau_start=1.0e-6, tau_step=1.0e-6, num_of_points=50,
                        alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
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
        tau_element = self._get_idle_element(length=tau_pspacing_start, increment=tau_step)

        # Create block and append to created_blocks list
        ramsey_block = PulseBlock(name=name)
        ramsey_block.append(pihalf_element)
        ramsey_block.append(tau_element)
        ramsey_block.append(pihalf_element)
        ramsey_block.append(laser_element)
        ramsey_block.append(delay_element)
        ramsey_block.append(waiting_element)
        if alternating:
            ramsey_block.append(pihalf_element)
            ramsey_block.append(tau_element)
            ramsey_block.append(pi3half_element)
            ramsey_block.append(laser_element)
            ramsey_block.append(delay_element)
            ramsey_block.append(waiting_element)
        created_blocks.append(ramsey_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((ramsey_block.name, num_of_points - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = 2 * num_of_points if alternating else num_of_points
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

    def generate_ramsey_from_list(self, name='ramsey', tau_list='[1e-6, 2e-6]', alternating=True):
        """
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        try:
            tau_array = csv_2_list(tau_list)
        except TypeError:
            tau_array = tau_list
        tau_pspacing_array = self.tau_2_pulse_spacing(tau_array)

        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()
        # get pihalf element
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)

        if alternating:
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

        # Create block and append to created_blocks list
        ramsey_block = PulseBlock(name=name)
        for tau_pspacing in tau_pspacing_array:
            tau_element = self._get_idle_element(length=tau_pspacing, increment=0)
            ramsey_block.append(pihalf_element)
            ramsey_block.append(tau_element)
            ramsey_block.append(tau_element)
            ramsey_block.append(pihalf_element)
            ramsey_block.append(laser_element)
            ramsey_block.append(delay_element)
            ramsey_block.append(waiting_element)

            if alternating:
                ramsey_block.append(pihalf_element)
                ramsey_block.append(tau_element)
                ramsey_block.append(pi3half_element)
                ramsey_block.append(laser_element)
                ramsey_block.append(delay_element)
                ramsey_block.append(waiting_element)

        created_blocks.append(ramsey_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((ramsey_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = 2 * len(tau_array) if alternating else len(tau_array)
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

    def generate_hahnecho(self, name='hahn_echo', tau_start=0.0e-6, tau_step=1.0e-6,
                          num_of_points=50, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step
        tau_pspacing_start = self.tau_2_pulse_spacing(tau_start)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        pi_element = self._get_mw_element(length=self.rabi_period / 2,
                                          increment=0,
                                          amp=self.microwave_amplitude,
                                          freq=self.microwave_frequency,
                                          phase=0)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
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
        tau_element = self._get_idle_element(length=tau_pspacing_start, increment=tau_step)

        # Create block and append to created_blocks list
        hahn_block = PulseBlock(name=name)
        hahn_block.append(pihalf_element)
        hahn_block.append(tau_element)
        hahn_block.append(pi_element)
        hahn_block.append(tau_element)
        hahn_block.append(pihalf_element)
        hahn_block.append(laser_element)
        hahn_block.append(delay_element)
        hahn_block.append(waiting_element)
        if alternating:
            hahn_block.append(pihalf_element)
            hahn_block.append(tau_element)
            hahn_block.append(pi_element)
            hahn_block.append(tau_element)
            hahn_block.append(pi3half_element)
            hahn_block.append(laser_element)
            hahn_block.append(delay_element)
            hahn_block.append(waiting_element)
        created_blocks.append(hahn_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((hahn_block.name, num_of_points - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = 2 * num_of_points if alternating else num_of_points
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

    def generate_hahnecho_exp(self, name='hahn_echo', tau_start=1.0e-6, tau_end=1.0e-6,
                              num_of_points=50, alternating=True):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        if tau_start == 0.0:
            tau_array = np.geomspace(1e-9, tau_end, num_of_points - 1)
            tau_array = np.insert(tau_array, 0, 0.0)
        else:
            tau_array = np.geomspace(tau_start, tau_end, num_of_points)

        tau_pspacing_array = self.tau_2_pulse_spacing(tau_array)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        pi_element = self._get_mw_element(length=self.rabi_period / 2,
                                          increment=0,
                                          amp=self.microwave_amplitude,
                                          freq=self.microwave_frequency,
                                          phase=0)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
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

        # Create block and append to created_blocks list
        hahn_block = PulseBlock(name=name)
        for tau_pspacing in tau_pspacing_array:
            tau_element = self._get_idle_element(length=tau_pspacing, increment=0.0)
            hahn_block.append(pihalf_element)
            hahn_block.append(tau_element)
            hahn_block.append(pi_element)
            hahn_block.append(tau_element)
            hahn_block.append(pihalf_element)
            hahn_block.append(laser_element)
            hahn_block.append(delay_element)
            hahn_block.append(waiting_element)
            if alternating:
                hahn_block.append(pihalf_element)
                hahn_block.append(tau_element)
                hahn_block.append(pi_element)
                hahn_block.append(tau_element)
                hahn_block.append(pi3half_element)
                hahn_block.append(laser_element)
                hahn_block.append(delay_element)
                hahn_block.append(waiting_element)
        created_blocks.append(hahn_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((hahn_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = 2 * num_of_points if alternating else num_of_points
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

    def generate_t1(self, name='T1', tau_start=1.0e-6, tau_step=1.0e-6,
                    num_of_points=50, alternating=False):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()
        if alternating:  # get pi element
            pi_element = self._get_mw_element(length=self.rabi_period / 2,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)

        tau_element = self._get_idle_element(length=tau_start, increment=tau_step)
        t1_block = PulseBlock(name=name)
        t1_block.append(tau_element)
        t1_block.append(laser_element)
        t1_block.append(delay_element)
        t1_block.append(waiting_element)
        if alternating:
            t1_block.append(pi_element)
            t1_block.append(tau_element)
            t1_block.append(laser_element)
            t1_block.append(delay_element)
            t1_block.append(waiting_element)
        created_blocks.append(t1_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((t1_block.name, num_of_points - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = 2 * num_of_points if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)
        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_t1_exponential(self, name='T1_exp', tau_start=1.0e-6, tau_end=1.0e-6,
                                num_of_points=50, alternating=False):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        if tau_start == 0.0:
            tau_array = np.geomspace(1e-9, tau_end, num_of_points - 1)
            tau_array = np.insert(tau_array, 0, 0.0)
        else:
            tau_array = np.geomspace(tau_start, tau_end, num_of_points)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()
        if alternating:  # get pi element
            pi_element = self._get_mw_element(length=self.rabi_period / 2,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        t1_block = PulseBlock(name=name)
        for tau in tau_array:
            tau_element = self._get_idle_element(length=tau, increment=0.0)
            t1_block.append(tau_element)
            t1_block.append(laser_element)
            t1_block.append(delay_element)
            t1_block.append(waiting_element)
            if alternating:
                t1_block.append(pi_element)
                t1_block.append(tau_element)
                t1_block.append(laser_element)
                t1_block.append(delay_element)
                t1_block.append(waiting_element)
        created_blocks.append(t1_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((t1_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        number_of_lasers = 2 * num_of_points if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)
        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_HHamp(self, name='hh_amp', spinlock_length=20e-6, amp_start=0.05, amp_step=0.01,
                       num_of_points=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get amplitude array for measurement ticks
        amp_array = amp_start + np.arange(num_of_points) * amp_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
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

        # Create block and append to created_blocks list
        hhamp_block = PulseBlock(name=name)
        for sl_amp in amp_array:
            sl_element = self._get_mw_element(length=spinlock_length,
                                              increment=0,
                                              amp=sl_amp,
                                              freq=self.microwave_frequency,
                                              phase=90)
            hhamp_block.append(pihalf_element)
            hhamp_block.append(sl_element)
            hhamp_block.append(pihalf_element)
            hhamp_block.append(laser_element)
            hhamp_block.append(delay_element)
            hhamp_block.append(waiting_element)

            hhamp_block.append(pi3half_element)
            hhamp_block.append(sl_element)
            hhamp_block.append(pihalf_element)
            hhamp_block.append(laser_element)
            hhamp_block.append(delay_element)
            hhamp_block.append(waiting_element)
        created_blocks.append(hhamp_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((hhamp_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = True
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = amp_array
        block_ensemble.measurement_information['units'] = ('V', '')
        block_ensemble.measurement_information['labels'] = ('MW amplitude', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = 2 * num_of_points
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_HHtau(self, name='hh_tau', spinlock_amp=0.1, tau_start=1e-6, tau_step=1e-6,
                       num_of_points=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
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
        sl_element = self._get_mw_element(length=tau_start,
                                          increment=tau_step,
                                          amp=spinlock_amp,
                                          freq=self.microwave_frequency,
                                          phase=90)

        # Create block and append to created_blocks list
        hhtau_block = PulseBlock(name=name)
        hhtau_block.append(pihalf_element)
        hhtau_block.append(sl_element)
        hhtau_block.append(pihalf_element)
        hhtau_block.append(laser_element)
        hhtau_block.append(delay_element)
        hhtau_block.append(waiting_element)

        hhtau_block.append(pi3half_element)
        hhtau_block.append(sl_element)
        hhtau_block.append(pihalf_element)
        hhtau_block.append(laser_element)
        hhtau_block.append(delay_element)
        hhtau_block.append(waiting_element)
        created_blocks.append(hhtau_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((hhtau_block.name, num_of_points - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = True
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Spinlock time', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = 2 * num_of_points
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_HHpol(self, name='hh_pol', spinlock_length=20.0e-6, spinlock_amp=0.1,
                       polarization_steps=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get steps array for measurement ticks
        steps_array = np.arange(2 * polarization_steps)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        pihalf_element = self._get_mw_element(length=self.rabi_period / 4,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
        # Use a 180 deg phase shiftet pulse as 3pihalf pulse if microwave channel is analog
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
        sl_element = self._get_mw_element(length=spinlock_length,
                                          increment=0,
                                          amp=spinlock_amp,
                                          freq=self.microwave_frequency,
                                          phase=90)

        # Create block for "up"-polarization and append to created_blocks list
        up_block = PulseBlock(name=name + '_up')
        up_block.append(pihalf_element)
        up_block.append(sl_element)
        up_block.append(pihalf_element)
        up_block.append(laser_element)
        up_block.append(delay_element)
        up_block.append(waiting_element)
        created_blocks.append(up_block)

        # Create block for "down"-polarization and append to created_blocks list
        down_block = PulseBlock(name=name + '_down')
        down_block.append(pi3half_element)
        down_block.append(sl_element)
        down_block.append(pi3half_element)
        down_block.append(laser_element)
        down_block.append(delay_element)
        down_block.append(waiting_element)
        created_blocks.append(down_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((up_block.name, polarization_steps - 1))
        block_ensemble.append((down_block.name, polarization_steps - 1))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = False
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = steps_array
        block_ensemble.measurement_information['units'] = ('#', '')
        block_ensemble.measurement_information['labels'] = ('Polarization Steps', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = 2 * polarization_steps
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    ################################################################################################
    #                             Generation methods for sequences                                 #
    ################################################################################################
    def generate_t1_sequencing(self, name='t1_seq', tau_start=1.0e-6, tau_max=1.0e-3,
                               num_of_points=10):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Get logarithmically spaced steps in multiples of tau_start.
        # Note that the number of points and the position of the last point can change here.
        k_array = np.unique(
            np.rint(np.logspace(0., np.log10(tau_max / tau_start), num_of_points)).astype(int))
        # get tau array for measurement ticks
        tau_array = k_array * tau_start

        # Create the readout PulseBlockEnsemble
        # Get necessary PulseBlockElements
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        # Create PulseBlock and append PulseBlockElements
        readout_block = PulseBlock(name='{0}_readout'.format(name))
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
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the tau/waiting PulseBlockEnsemble
        # Get tau PulseBlockElement
        tau_element = self._get_idle_element(length=tau_start, increment=0)
        # Create PulseBlock and append PulseBlockElements
        tau_block = PulseBlock(name='{0}_tau'.format(name))
        tau_block.append(tau_element)
        created_blocks.append(tau_block)
        # Create PulseBlockEnsemble and append block to it
        tau_ensemble = PulseBlockEnsemble(name='{0}_tau'.format(name), rotating_frame=False)
        tau_ensemble.append((tau_block.name, 0))
        created_ensembles.append(tau_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        t1_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        for k in k_array:
            t1_sequence.append(tau_ensemble.name)
            t1_sequence[-1].repetitions = int(k) - 1
            count_length += k * self._get_ensemble_count_length(ensemble=tau_ensemble,
                                                                created_blocks=created_blocks)

            if self.sync_channel and k == k_array[-1]:
                t1_sequence.append(sync_readout_ensemble.name)
            else:
                t1_sequence.append(readout_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=readout_ensemble,
                                                            created_blocks=created_blocks)
        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        t1_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        t1_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        t1_sequence.measurement_information['alternating'] = False
        t1_sequence.measurement_information['laser_ignore_list'] = list()
        t1_sequence.measurement_information['controlled_variable'] = tau_array
        t1_sequence.measurement_information['units'] = ('s', '')
        t1_sequence.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        t1_sequence.measurement_information['number_of_lasers'] = len(tau_array)
        t1_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(t1_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_chirpedodmr(self, name='LinearChirpedODMR', mw_freq_center=2870.0e6,
                             freq_range=500.0e6, freq_overlap=20.0e6, num_of_points=50,
                             pulse_length=500e-9, expected_rabi_frequency=30e6, expected_t2=5e-6):
        """
        @param str name: name of Pulse Block Ensemble
        @param float mw_freq_center: central frequency of the chirped ODMR in Hz
        @param float freq_range: target frequency range of the whole ODMR scan in Hz
        @param float freq_overlap: additional 'overlap' frequency range for each chirped pulse,
        i.e. the frequency range of each single chirped pulse is
        (freq_range / num_points) + freq_overlap
        @param float num_of_points: number of chirped pulses, used in the scan
        @param float pulse_length: length of the mw pulse
        @param float expected_rabi_frequency: expected value of the Rabi frequency - used to
        calculate adiabaticity
        @param float expected_t2: expected T2 time - used to check if the chirped pulse is shorter
        than T2

        @return: created_blocks, created_ensembles, created_sequences for the generated pulse
            sequences
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        chirpedodmr_block = PulseBlock(name=name)

        # Create frequency array
        mw_freq_start = mw_freq_center - freq_range / 2.
        mw_freq_incr = freq_range / num_of_points
        freq_array = mw_freq_start + np.arange(num_of_points) * mw_freq_incr + mw_freq_incr / 2.

        if pulse_length > expected_t2:
            self.log.error('The duration of the chirped pulse exceeds expected the T2 time')

        for mw_freq in freq_array:
            mw_element = self._get_mw_element_linearchirp(length=pulse_length,
                                                          increment=0,
                                                          amplitude=self.microwave_amplitude,
                                                          start_freq=(mw_freq - mw_freq_incr / 2.
                                                                      - freq_overlap),
                                                          stop_freq=(mw_freq + mw_freq_incr / 2.
                                                                     + freq_overlap),
                                                          phase=0)
            chirpedodmr_block.append(mw_element)
            chirpedodmr_block.append(laser_element)
            chirpedodmr_block.append(delay_element)
            chirpedodmr_block.append(waiting_element)
        created_blocks.append(chirpedodmr_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((chirpedodmr_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # chirp range
        pulse_freq_range = mw_freq + mw_freq_incr / 2. + freq_overlap - (
                mw_freq - mw_freq_incr / 2. - freq_overlap)

        # chirp rate
        chirp_rate = pulse_freq_range / pulse_length

        # adiabaticity condition
        adiab = 2 * np.pi * expected_rabi_frequency ** 2 / chirp_rate
        # adiab >> 1 is needed for adiabatic evolution. Simulations show that adiab > 5 works very
        # well,
        # adiab > 2 will work but is on the edge, so we impose a check if adiab < 2.5 to give a
        # warning.

        if adiab < 2.5:
            self.log.error(
                'Adiabadicity conditions not matched. Rabi**2/(pulse_freq_range/pulse_length)>>1 is'
                ' not fulfilled,  Rabi**2/(pulse_freq_range/pulse_length) = {}'.format(adiab))
        else:
            self.log.info(
                'Adiabadicity conditions is Rabi**2/(pulse_freq_range/pulse_length) = '
                '{} >> 1'.format(adiab))

        # Approximate expected transfer efficiency in case of perfect adiabaticity for a linear
        # chirp this formula works very well for adiab = 5 and overestimates the efficiency by
        # 5-10% for adiab = 2.5
        approx_transfer_eff_perfect_adiab = 1 - 2 / (
                4 + (pulse_freq_range / expected_rabi_frequency) ** 2)

        self.log.info(
            'Expected transfer efficiency in case of perfect adiabaticity = ' + str(
                approx_transfer_eff_perfect_adiab))

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = False
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['labels'] = ('Frequency', '')
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['number_of_lasers'] = num_of_points
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_AEchirpedodmr(self, name='AllenEberlyChirpODMR', mw_freq_center=2870.0e6,
                               freq_range=500.0e6,
                               freq_overlap=20.0e6, num_of_points=50, pulse_length=500e-9,
                               truncation_ratio=0.1,
                               expected_rabi_frequency=30e6, expected_t2=5e-6,
                               peak_mw_amplitude=0.25):
        """
        @param str name: name of Pulse Block Ensemble
        @param float mw_freq_center: central frequency of the chirped ODMR in Hz
        @param float freq_range: target frequency range of the whole ODMR scan in Hz
        @param float freq_overlap: additional 'overlap' frequency range for each chirped pulse,
            i.e. the frequency range of each single chirped pulse is (freq_range / num_points) +
            freq_overlap. Truncation is usually negligible for values <0.2.
        @param float num_of_points: number of chirped pulses, used in the scan
        @param float pulse_length: length of the mw pulse
        @param float truncation_ratio: ratio that characterizes the truncation of the chirped pulse
            Specifically, the pulse shape is given by sech(t/ truncation ratio /pulse length)
            truncation_ratio = 0.1 is excellent; the scheme will work for 0.2. Higher values
            truncate the sech pulse and reduce the frequency range of ODMR as the transfer
            efficiency in the wings of the pulse range drops.
        @param float expected_rabi_frequency: expected value of the Rabi frequency - used to
            calculate adiabaticity
        @param float expected_t2: expected T2 time - used to check if the chirped pulse is shorter
            than T2
        @param float peak_mw_amplitude: Peak amplitude of the Allen-Eberly Chirp pulse

        @return: created_blocks, created_ensembles, created_sequences for the generated pulse
            sequences

        Additional information about the Allen-Eberly chirped ODMR
        Chirped ODMR with a pulse, following the Allen-Eberly model: a sech amplitude shape and a
        tanh shaped detuning. The AE pulse has very good properties in terms of adiabaticity and is
        often preferable to the standard Landau-Zener-Stueckelberg-Majorana model with a constant
        amplitude and a linear chirp (see class Chirp). More information about the Allen-Eberly
        model can be found in:
        L. Allen and J. H. Eberly, Optical Resonance and Two-Level Atoms Dover, New York, 1987,
        Analytical solution is given in: F. T. Hioe, Phys. Rev. A 30, 2100 (1984).
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        chirpedodmr_block = PulseBlock(name=name)

        # Create frequency array
        mw_freq_start = mw_freq_center - freq_range / 2.
        mw_freq_incr = freq_range / num_of_points
        freq_array = mw_freq_start + np.arange(num_of_points) * mw_freq_incr + mw_freq_incr / 2.

        if pulse_length > expected_t2:
            self.log.error('The duration of the chirped pulse exceeds the expected T2 time')

        for mw_freq in freq_array:
            mw_element = self._get_mw_element_AEchirp(length=pulse_length,
                                                      increment=0,
                                                      amp=peak_mw_amplitude,
                                                      start_freq=(mw_freq - mw_freq_incr / 2.
                                                                  - freq_overlap),
                                                      stop_freq=(mw_freq + mw_freq_incr / 2.
                                                                 + freq_overlap),
                                                      phase=0,
                                                      truncation_ratio=truncation_ratio)
            chirpedodmr_block.append(mw_element)
            chirpedodmr_block.append(laser_element)
            chirpedodmr_block.append(delay_element)
            chirpedodmr_block.append(waiting_element)
        created_blocks.append(chirpedodmr_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((chirpedodmr_block.name, 0))

        # Create and append sync trigger block if needed
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)

        # chirp range
        pulse_freq_range = mw_freq + mw_freq_incr / 2. + freq_overlap - (
                mw_freq - mw_freq_incr / 2. - freq_overlap)

        # chirp rate for the AE model at the moment of level crossing
        chirp_rate_ae = pulse_freq_range / pulse_length / truncation_ratio
        # In comparison to linear chirp, the chirp rate is divided by the truncation_ratio

        # adiabaticity condition for the AE model
        adiab_ae = 2 * np.pi * expected_rabi_frequency ** 2 / chirp_rate_ae
        # adiab_ae >> 1 is needed for adiabatic evolution. Simulations show adiab_ae > 2 will work
        # but is on the edge, so we impose a check if adiab_ae < 2.5 to give a warning.

        if adiab_ae < 2.5:
            self.log.error(
                'Adiabadicity conditions not matched. Rabi**2/(pulse_freq_range/'
                'pulse_length/truncation_ratio)>>1 is not fulfilled,  Rabi**2/(pulse_freq_range / '
                'pulse_length / truncation_ratio) = {}'.format(adiab_ae))
        else:
            self.log.info(
                'Adiabadicity conditions is Rabi**2/'
                '(pulse_freq_range / pulse_length / truncation_ratio) = {} >> 1'.format(adiab_ae))

        # Approximate expected transfer efficiency in case of perfect adiabaticity for a AE pulse
        # this formula works very well for adiab > 2.5
        approx_transfer_eff_perfect_adiab_ae = 1 - 2 / (
                4 + (pulse_freq_range * np.sinh(1 / 2 / truncation_ratio)
                     / expected_rabi_frequency) ** 2)

        self.log.info(
            'Expected transfer efficiency in case of perfect adiabaticity = ' + str(
                approx_transfer_eff_perfect_adiab_ae))

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = False
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['labels'] = ('Frequency', '')
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['number_of_lasers'] = num_of_points
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_pulsedodmr_seq(self, name='pulsedODMRseq', freq_start=2870.0e6, freq_step=0.2e6,
                                num_of_points=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Create frequency array
        freq_array = freq_start + np.arange(num_of_points) * freq_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        pulsedodmr_block = PulseBlock(name=name)
        pulsedodmr_block.append(laser_element)
        pulsedodmr_block.append(delay_element)
        created_blocks.append(pulsedodmr_block)

        # Create block ensemble and append block to it
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((pulsedodmr_block.name, 0))
        created_ensembles.append(block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        pulsedodmr_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        for freq in freq_array:
            MW_block = PulseBlock(name='{0}{1}_mw'.format(name, freq))
            mw_element = self._get_mw_element(length=self.rabi_period / 2,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=freq,
                                              phase=0)
            MW_block.append(mw_element)
            MW_block.append(waiting_element)  # TODO, as this should be before laser_element, but due to minimal bin constrains from awg we write it after the MW
            created_blocks.append(MW_block)
            MW_ensemble = PulseBlockEnsemble(name='{0}{1}_mw'.format(name, freq), rotating_frame=False)
            MW_ensemble.append((MW_block.name, 0))
            created_ensembles.append(MW_ensemble)
            pulsedodmr_sequence.append(MW_ensemble.name)
            if self.sync_channel and freq == freq_array[-1]:
                pulsedodmr_sequence.append(sync_readout_ensemble.name)
            else:
                pulsedodmr_sequence.append(block_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=MW_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=block_ensemble,
                                                            created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        pulsedodmr_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        pulsedodmr_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        pulsedodmr_sequence.measurement_information['alternating'] = False
        pulsedodmr_sequence.measurement_information['laser_ignore_list'] = list()
        pulsedodmr_sequence.measurement_information['controlled_variable'] = freq_array
        pulsedodmr_sequence.measurement_information['units'] = ('Hz', '')
        pulsedodmr_sequence.measurement_information['labels'] = ('Frequency', 'Signal')
        pulsedodmr_sequence.measurement_information['number_of_lasers'] = num_of_points
        if self.gate_channel:
            pulsedodmr_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                block_ensemble, created_blocks)
        else:
            pulsedodmr_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(pulsedodmr_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_rabi_seq(self, name='rabi_seq', tau_start=10.0e-9, tau_step=10.0e-9, num_of_points=50):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        rabi_seq_block = PulseBlock(name=name)
        rabi_seq_block.append(laser_element)
        rabi_seq_block.append(delay_element)
        created_blocks.append(rabi_seq_block)

        # Create block ensemble and append block to it
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((rabi_seq_block.name, 0))
        created_ensembles.append(block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        rabi_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        for tau in tau_array:
            MW_block = PulseBlock(name='{0}{1}_mw'.format(name, tau))
            mw_element = self._get_mw_element(length=tau,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
            MW_block.append(mw_element)
            MW_block.append(waiting_element)  # TODO, as this should be before laser_element,
            # but due to minimal bin constrains from awg we write it after the MW
            created_blocks.append(MW_block)
            MW_ensemble = PulseBlockEnsemble(name='{0}{1}_mw'.format(name, tau), rotating_frame=False)
            MW_ensemble.append((MW_block.name, 0))
            created_ensembles.append(MW_ensemble)
            rabi_sequence.append(MW_ensemble.name)
            if self.sync_channel and tau == tau_array[-1]:
                rabi_sequence.append(sync_readout_ensemble.name)
            else:
                rabi_sequence.append(block_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=MW_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=block_ensemble,
                                                            created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        rabi_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        rabi_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        rabi_sequence.measurement_information['alternating'] = False
        rabi_sequence.measurement_information['laser_ignore_list'] = list()
        rabi_sequence.measurement_information['controlled_variable'] = tau_array
        rabi_sequence.measurement_information['units'] = ('s', '')
        rabi_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        rabi_sequence.measurement_information['number_of_lasers'] = num_of_points
        if self.gate_channel:
            rabi_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                block_ensemble, created_blocks)
        else:
            rabi_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(rabi_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_swapgate_test_seq(self, name='swapgate_test_seq', rf_freq_start=2.0e6, rf_freq_step=0.2e6,
                                num_of_points=50, rf_amplitude=1e-3, rf_rabi_period=30e-6, rf_wait_time=10e-6,
                                   mw_freq_alt=3e9, mw_period_alt=1e-6, mw_amplitude_alt=1e-3, alternating=False):
        """
            Choose mw_alt such that it is the other weak pi pulse!
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Create frequency array
        rf_freq_array = rf_freq_start + np.arange(num_of_points) * rf_freq_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        # due to ringing of rf pulse
        rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        if alternating:
            pix_element_alt = self._get_mw_element(length=mw_period_alt / 2,
                                                   increment=0,
                                                   amp=mw_amplitude_alt,
                                                   freq=mw_freq_alt,
                                                   phase=0)

        # Create block and append to created_blocks list
        # I was thinking about adding rf waiting and pi element to this block as it does not change,
        # however then the ensemble count length for the gated mode does not match
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        # Create block ensemble and append block to it
        readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        swap_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        for rf_freq in rf_freq_array:
            swap_block = PulseBlock(name='{0}{1}_rf'.format(name, rf_freq))
            rf_element = self._get_mw_element(length=rf_rabi_period / 2,
                                              increment=0,
                                              amp=rf_amplitude,
                                              freq=rf_freq,
                                              phase=0)
            swap_block.append(pix_element)
            swap_block.append(rf_element)
            swap_block.append(rf_waiting_element)
            swap_block.append(pix_element)
            created_blocks.append(swap_block)
            swap_ensemble = PulseBlockEnsemble(name='{0}{1}_rf'.format(name, rf_freq), rotating_frame=False)
            swap_ensemble.append((swap_block.name, 0))
            created_ensembles.append(swap_ensemble)
            if alternating:
                # rf_element_2 = self._get_mw_element(length=3*rf_rabi_period/4,
                #                                   increment=0,
                #                                   amp=rf_amplitude,
                #                                   freq=rf_freq,
                #                                   phase=0)
                swap_block_alt = PulseBlock(name='{0}{1}_rf_alt'.format(name, rf_freq))
                swap_block_alt.append(pix_element)
                swap_block_alt.append(rf_element)
                swap_block_alt.append(rf_waiting_element)
                swap_block_alt.append(pix_element_alt)
                created_blocks.append(swap_block_alt)
                swap_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_rf_alt'.format(name, rf_freq), rotating_frame=False)
                swap_ensemble_alt.append((swap_block_alt.name, 0))
                created_ensembles.append(swap_ensemble_alt)

            swap_sequence.append(swap_ensemble.name)
            if self.sync_channel and rf_freq == rf_freq_array[-1] and not alternating:
                swap_sequence.append(sync_readout_ensemble.name)
            else:
                swap_sequence.append(readout_block_ensemble.name)
            if alternating:
                swap_sequence.append(swap_ensemble_alt.name)
                if self.sync_channel and rf_freq == rf_freq_array[-1]:
                    swap_sequence.append(sync_readout_ensemble.name)
                else:
                    swap_sequence.append(readout_block_ensemble.name)

            count_length += self._get_ensemble_count_length(ensemble=swap_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
                                                            created_blocks=created_blocks)
            if alternating:
                count_length += self._get_ensemble_count_length(ensemble=swap_ensemble_alt,
                                                                created_blocks=created_blocks)
                count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
                                                                created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        swap_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        swap_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        swap_sequence.measurement_information['alternating'] = alternating
        swap_sequence.measurement_information['laser_ignore_list'] = list()
        swap_sequence.measurement_information['controlled_variable'] = rf_freq_array
        swap_sequence.measurement_information['units'] = ('Hz', '')
        swap_sequence.measurement_information['labels'] = ('RF frequency', 'Signal')
        swap_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            swap_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                readout_block_ensemble, created_blocks)
        else:
            swap_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(swap_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_test_seq(self, name='test_seq'):
        """
            Choose mw_alt such that it is the other weak pi pulse!
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        two_pix_element = self._get_mw_element(length=self.rabi_period,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        # Create block and append to created_blocks list
        # I was thinking about adding rf waiting and pi element to this block as it does not change,
        # however then the ensemble count length for the gated mode does not match
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        # Create block ensemble and append block to it
        readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        swap_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0

        swap_block_1 = PulseBlock(name='test_1')
        swap_block_1.append(pix_element)
        swap_block_1.append(pix_element)
        created_blocks.append(swap_block_1)
        swap_ensemble_1 = PulseBlockEnsemble(name='test_1', rotating_frame=False)
        swap_ensemble_1.append((swap_block_1.name, 0))
        created_ensembles.append(swap_ensemble_1)

        swap_block_2 = PulseBlock(name='test_2')
        swap_block_2.append(pix_element)
        created_blocks.append(swap_block_2)
        swap_ensemble_2 = PulseBlockEnsemble(name='test_2', rotating_frame=False)
        swap_ensemble_2.append((swap_block_2.name, 0))
        created_ensembles.append(swap_ensemble_2)

        swap_block_3 = PulseBlock(name='test_3')
        swap_block_3.append(two_pix_element)
        created_blocks.append(swap_block_3)
        swap_ensemble_3 = PulseBlockEnsemble(name='test_3', rotating_frame=False)
        swap_ensemble_3.append((swap_block_3.name, 0))
        created_ensembles.append(swap_ensemble_3)

        swap_sequence.append(swap_ensemble_1.name)
        swap_sequence.append(readout_block_ensemble.name)
        swap_sequence.append(swap_ensemble_2.name)
        swap_sequence.append(readout_block_ensemble.name)
        swap_sequence.append(swap_ensemble_3.name)
        swap_sequence.append(readout_block_ensemble.name)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        swap_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        swap_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = 3
        swap_sequence.measurement_information['alternating'] = False
        swap_sequence.measurement_information['laser_ignore_list'] = list()
        swap_sequence.measurement_information['controlled_variable'] = np.arange(3)
        swap_sequence.measurement_information['units'] = ('Hz', '')
        swap_sequence.measurement_information['labels'] = ('RF frequency', 'Signal')
        swap_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            swap_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                readout_block_ensemble, created_blocks)
        else:
            swap_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(swap_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_swapgate_tau_sweep(self, name='swapgate_tau_sweep', tau_start=5e-6, tau_step=5e-6,
                                num_of_points=20, rf_amplitude=1e-3, rf_freq=500e3, rf_wait_time=30e-6,
                                   mw_freq_alt=3e9, mw_period_alt=1e-6, mw_amplitude_alt=1e-3, alternating=False):
        """
            Choose mw_alt such that it is the other weak pi pulse!
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Create frequency array
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        # due to ringing of rf pulse
        rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        if alternating:
            pix_element_alt = self._get_mw_element(length=mw_period_alt / 2,
                                                   increment=0,
                                                   amp=mw_amplitude_alt,
                                                   freq=mw_freq_alt,
                                                   phase=0)

        # Create block and append to created_blocks list
        # I was thinking about adding rf waiting and pi element to this block as it does not change,
        # however then the ensemble count length for the gated mode does not match
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        # Create block ensemble and append block to it
        readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        swap_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        for tau in tau_array:
            swap_block = PulseBlock(name='{0}{1}_tau'.format(name, tau))
            rf_element = self._get_mw_element(length=tau,
                                              increment=0,
                                              amp=rf_amplitude,
                                              freq=rf_freq,
                                              phase=0)
            swap_block.append(pix_element)
            swap_block.append(rf_element)
            swap_block.append(rf_waiting_element)
            swap_block.append(pix_element)
            created_blocks.append(swap_block)
            swap_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=False)
            swap_ensemble.append((swap_block.name, 0))
            created_ensembles.append(swap_ensemble)
            if alternating:
                swap_block_alt = PulseBlock(name='{0}{1}_tau_alt'.format(name, tau))
                swap_block_alt.append(pix_element)
                swap_block_alt.append(rf_element)
                swap_block_alt.append(rf_waiting_element)
                swap_block_alt.append(pix_element_alt)
                created_blocks.append(swap_block_alt)
                swap_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, tau), rotating_frame=False)
                swap_ensemble_alt.append((swap_block_alt.name, 0))
                created_ensembles.append(swap_ensemble_alt)

            swap_sequence.append(swap_ensemble.name)
            if self.sync_channel and tau == tau_array[-1] and not alternating:
                swap_sequence.append(sync_readout_ensemble.name)
            else:
                swap_sequence.append(readout_block_ensemble.name)
            if alternating:
                swap_sequence.append(swap_ensemble_alt.name)
                if self.sync_channel and tau == tau_array[-1]:
                    swap_sequence.append(sync_readout_ensemble.name)
                else:
                    swap_sequence.append(readout_block_ensemble.name)

            count_length += self._get_ensemble_count_length(ensemble=swap_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
                                                            created_blocks=created_blocks)
            if alternating:
                count_length += self._get_ensemble_count_length(ensemble=swap_ensemble_alt,
                                                                created_blocks=created_blocks)
                count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
                                                                created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        swap_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        swap_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        swap_sequence.measurement_information['alternating'] = alternating
        swap_sequence.measurement_information['laser_ignore_list'] = list()
        swap_sequence.measurement_information['controlled_variable'] = tau_array
        swap_sequence.measurement_information['units'] = ('s', '')
        swap_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        swap_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            swap_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                readout_block_ensemble, created_blocks)
        else:
            swap_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(swap_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_weak_rabi_seq(self, name='weak_rabi_seq', tau_start=200.0e-9, tau_step=300.0e-9, num_of_points=30, mw_freq_2=3.0e9):
        """

        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        waiting_element = self._get_idle_element(length=self.wait_time,
                                                 increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        # Create block and append to created_blocks list
        rabi_seq_block = PulseBlock(name=name)
        rabi_seq_block.append(laser_element)
        rabi_seq_block.append(delay_element)
        created_blocks.append(rabi_seq_block)

        # Create block ensemble and append block to it
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        block_ensemble.append((rabi_seq_block.name, 0))
        created_ensembles.append(block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        rabi_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0
        for tau in tau_array:
            MW_block = PulseBlock(name='{0}{1}_mw'.format(name, tau))
            mw_element_1 = self._get_mw_element(length=tau,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=self.microwave_frequency,
                                              phase=0)
            mw_element_2 = self._get_mw_element(length=tau,
                                              increment=0,
                                              amp=self.microwave_amplitude,
                                              freq=mw_freq_2,
                                              phase=0)
            MW_block.append(mw_element_1)
            MW_block.append(mw_element_2)
            MW_block.append(waiting_element)  # This should be before laser_element;
            # but due to minimal bin constrains from awg I write it after the MW
            created_blocks.append(MW_block)
            MW_ensemble = PulseBlockEnsemble(name='{0}{1}_mw'.format(name, tau), rotating_frame=False)
            MW_ensemble.append((MW_block.name, 0))
            created_ensembles.append(MW_ensemble)
            rabi_sequence.append(MW_ensemble.name)
            if self.sync_channel and tau == tau_array[-1]:
                rabi_sequence.append(sync_readout_ensemble.name)
            else:
                rabi_sequence.append(block_ensemble.name)
            count_length += self._get_ensemble_count_length(ensemble=MW_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=block_ensemble,
                                                            created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        rabi_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        rabi_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        rabi_sequence.measurement_information['alternating'] = False
        rabi_sequence.measurement_information['laser_ignore_list'] = list()
        rabi_sequence.measurement_information['controlled_variable'] = tau_array
        rabi_sequence.measurement_information['units'] = ('s', '')
        rabi_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        rabi_sequence.measurement_information['number_of_lasers'] = num_of_points
        if self.gate_channel:
            rabi_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                block_ensemble, created_blocks)
        else:
            rabi_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(rabi_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_swap_add_rf(self, name='swap_add_rf', rf_freq_start=400e3, rf_freq_step=4e3,
                                   num_of_points=51, rf_amplitude=1e-3, rf_rabi_period=30e-6, rf_wait_time=10e-6,
                                   mw_freq_alt=3e9, mw_period_alt=1e-6, mw_amplitude_alt=1e-3):
        """
            Choose mw_alt such that it is the other weak pi pulse!
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Create frequency array
        rf_freq_array = rf_freq_start + np.arange(num_of_points) * rf_freq_step

        control_array = np.arange(2)
        control_array = np.append(control_array, rf_freq_array)

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        # due to ringing of rf pulse
        rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        pix_element_alt = self._get_mw_element(length=mw_period_alt / 2,
                                               increment=0,
                                               amp=mw_amplitude_alt,
                                               freq=mw_freq_alt,
                                               phase=0)

        # Create block and append to created_blocks list
        # I was thinking about adding rf waiting and pi element to this block as it does not change,
        # however then the ensemble count length for the gated mode does not match
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        # Create block ensemble and append block to it
        readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_block_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_block_ensemble)

        contrast_block_1 = PulseBlock(name='{0}_contrast1'.format(name))
        contrast_block_1.append(pix_element)
        contrast_block_1.append(rf_waiting_element)
        contrast_block_1.append(pix_element_alt)
        created_blocks.append(contrast_block_1)

        contrast_ensemble_1 = PulseBlockEnsemble(name='{0}_contrast1'.format(name), rotating_frame=False)
        contrast_ensemble_1.append((contrast_block_1.name, 0))
        created_ensembles.append(contrast_ensemble_1)

        contrast_block_2 = PulseBlock(name='{0}_contrast2'.format(name))
        contrast_block_2.append(rf_waiting_element)
        created_blocks.append(contrast_block_2)

        contrast_ensemble_2 = PulseBlockEnsemble(name='{0}_contrast2'.format(name), rotating_frame=False)
        contrast_ensemble_2.append((contrast_block_2.name, 0))
        created_ensembles.append(contrast_ensemble_2)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        swap_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0

        # for normalisation
        swap_sequence.append(contrast_ensemble_1.name)
        swap_sequence.append(readout_block_ensemble.name)
        swap_sequence.append(contrast_ensemble_2.name)
        swap_sequence.append(readout_block_ensemble.name)

        count_length += self._get_ensemble_count_length(ensemble=contrast_ensemble_1,
                                                        created_blocks=created_blocks)
        count_length += self._get_ensemble_count_length(ensemble=contrast_ensemble_2,
                                                        created_blocks=created_blocks)
        count_length += 2 * self._get_ensemble_count_length(ensemble=readout_block_ensemble,
                                                            created_blocks=created_blocks)

        for rf_freq in rf_freq_array:
            swap_block = PulseBlock(name='{0}{1}_rf'.format(name, rf_freq))
            swap_block_2 = PulseBlock(name='{0}{1}_rf_2'.format(name, rf_freq))
            rf_element = self._get_mw_element(length=rf_rabi_period / 2,
                                              increment=0,
                                              amp=rf_amplitude,
                                              freq=rf_freq,
                                              phase=0)
            rf_element_minus = self._get_mw_element(length=rf_rabi_period / 2,
                                                    increment=0,
                                                    amp=rf_amplitude,
                                                    freq=rf_freq,
                                                    phase=180)
            swap_block.append(pix_element)
            swap_block.append(rf_element)
            swap_block.append(rf_waiting_element)
            swap_block.append(pix_element)
            created_blocks.append(swap_block)
            swap_ensemble = PulseBlockEnsemble(name='{0}{1}_rf'.format(name, rf_freq), rotating_frame=False)
            swap_ensemble.append((swap_block.name, 0))
            created_ensembles.append(swap_ensemble)

            swap_block_2.append(rf_element_minus)
            swap_block_2.append(rf_waiting_element)
            created_blocks.append(swap_block_2)
            swap_ensemble_2 = PulseBlockEnsemble(name='{0}{1}_rf_2'.format(name, rf_freq), rotating_frame=False)
            swap_ensemble_2.append((swap_block_2.name, 0))
            created_ensembles.append(swap_ensemble_2)

            swap_sequence.append(swap_ensemble.name)
            if self.sync_channel and rf_freq == rf_freq_array[-1]:
                swap_sequence.append(sync_readout_ensemble.name)
            else:
                swap_sequence.append(readout_block_ensemble.name)
            swap_sequence.append(swap_ensemble_2.name)

            count_length += self._get_ensemble_count_length(ensemble=swap_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=swap_ensemble_2,
                                                            created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        swap_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        swap_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        swap_sequence.measurement_information['alternating'] = False
        swap_sequence.measurement_information['laser_ignore_list'] = list()
        swap_sequence.measurement_information['controlled_variable'] = control_array
        swap_sequence.measurement_information['units'] = ('Hz', '')
        swap_sequence.measurement_information['labels'] = ('RF frequency', 'Signal')
        swap_sequence.measurement_information['number_of_lasers'] = num_of_points + 2
        if self.gate_channel:
            swap_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                readout_block_ensemble, created_blocks)
        else:
            swap_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(swap_sequence)
        return created_blocks, created_ensembles, created_sequences

    # def generate_swapgate_tau_sweep_add_rf(self, name='swapgate_tau_sweep_add_rf', tau_start=5e-6, tau_step=5e-6,
    #                             num_of_points=20, rf_amplitude=1e-3, rf_freq=500e3, rf_wait_time=30e-6,
    #                                mw_freq_alt=3e9, mw_period_alt=1e-6, mw_amplitude_alt=1e-3, alternating=False):
    #     """
    #         Choose mw_alt such that it is the other weak pi pulse!
    #     """
    #     created_blocks = list()
    #     created_ensembles = list()
    #     created_sequences = list()
    #
    #     # Create frequency array
    #     tau_array = tau_start + np.arange(num_of_points) * tau_step
    #
    #     # create the elements
    #     waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
    #     # due to ringing of rf pulse
    #     rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
    #     laser_element = self._get_laser_gate_element(length=self.laser_length,
    #                                                  increment=0)
    #     delay_element = self._get_delay_gate_element()
    #
    #     pix_element = self._get_mw_element(length=self.rabi_period / 2,
    #                                        increment=0,
    #                                        amp=self.microwave_amplitude,
    #                                        freq=self.microwave_frequency,
    #                                        phase=0)
    #
    #     if alternating:
    #         pix_element_alt = self._get_mw_element(length=mw_period_alt / 2,
    #                                                increment=0,
    #                                                amp=mw_amplitude_alt,
    #                                                freq=mw_freq_alt,
    #                                                phase=0)
    #
    #     # Create block and append to created_blocks list
    #     # I was thinking about adding rf waiting and pi element to this block as it does not change,
    #     # however then the ensemble count length for the gated mode does not match
    #     readout_block = PulseBlock(name='{0}_readout'.format(name))
    #     readout_block.append(waiting_element)
    #     readout_block.append(laser_element)
    #     readout_block.append(delay_element)
    #     created_blocks.append(readout_block)
    #
    #     # Create block ensemble and append block to it
    #     readout_block_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
    #     readout_block_ensemble.append((readout_block.name, 0))
    #     created_ensembles.append(readout_block_ensemble)
    #
    #     if self.sync_channel:
    #         # Create the last readout PulseBlockEnsemble including a sync trigger
    #         # Get necessary PulseBlockElements
    #         sync_element = self._get_sync_element()
    #         # Create PulseBlock and append PulseBlockElements
    #         sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
    #         sync_readout_block.append(waiting_element)
    #         sync_readout_block.append(laser_element)
    #         sync_readout_block.append(delay_element)
    #         sync_readout_block.append(sync_element)
    #         created_blocks.append(sync_readout_block)
    #         # Create PulseBlockEnsemble and append block to it
    #         sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
    #                                                    rotating_frame=False)
    #         sync_readout_ensemble.append((sync_readout_block.name, 0))
    #         created_ensembles.append(sync_readout_ensemble)
    #
    #     # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
    #     # together with the necessary parameters.
    #     swap_sequence = PulseSequence(name=name, rotating_frame=False)
    #     count_length = 0.0
    #     for tau in tau_array:
    #         swap_block = PulseBlock(name='{0}{1}_tau'.format(name, tau))
    #         swap_block_2 = PulseBlock(name='{0}{1}_rf_2'.format(name, rf_freq))
    #         rf_element = self._get_mw_element(length=tau,
    #                                           increment=0,
    #                                           amp=rf_amplitude,
    #                                           freq=rf_freq,
    #                                           phase=0)
    #         swap_block.append(pix_element)
    #         swap_block.append(rf_element)
    #         swap_block.append(rf_waiting_element)
    #         swap_block.append(pix_element)
    #         rf_element_minus = self._get_mw_element(length=tau,
    #                                                 increment=0,
    #                                                 amp=rf_amplitude,
    #                                                 freq=rf_freq,
    #                                                 phase=180)
    #         created_blocks.append(swap_block)
    #         swap_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=False)
    #         swap_ensemble.append((swap_block.name, 0))
    #         created_ensembles.append(swap_ensemble)
    #         swap_block_2.append(rf_element_minus)
    #         swap_block_2.append(rf_waiting_element)
    #         created_blocks.append(swap_block_2)
    #         swap_ensemble_2 = PulseBlockEnsemble(name='{0}{1}_rf_2'.format(name, rf_freq), rotating_frame=False)
    #         swap_ensemble_2.append((swap_block_2.name, 0))
    #         created_ensembles.append(swap_ensemble_2)
    #         # if alternating:
    #         #     swap_block_alt = PulseBlock(name='{0}{1}_tau_alt'.format(name, tau))
    #         #     swap_block_alt.append(pix_element)
    #         #     swap_block_alt.append(rf_element)
    #         #     swap_block_alt.append(rf_waiting_element)
    #         #     swap_block_alt.append(pix_element_alt)
    #         #     created_blocks.append(swap_block_alt)
    #         #     swap_ensemble_alt = PulseBlockEnsemble(name='{0}{1}_tau_alt'.format(name, tau), rotating_frame=False)
    #         #     swap_ensemble_alt.append((swap_block_alt.name, 0))
    #         #     created_ensembles.append(swap_ensemble_alt)
    #
    #         swap_sequence.append(swap_ensemble.name)
    #         if self.sync_channel and tau == tau_array[-1] and not alternating:
    #             swap_sequence.append(sync_readout_ensemble.name)
    #         else:
    #             swap_sequence.append(readout_block_ensemble.name)
    #         swap_sequence.append(swap_ensemble_2.name)
    #         # if alternating:
    #         #     swap_sequence.append(swap_ensemble_alt.name)
    #         #     if self.sync_channel and tau == tau_array[-1]:
    #         #         swap_sequence.append(sync_readout_ensemble.name)
    #         #     else:
    #         #         swap_sequence.append(readout_block_ensemble.name)
    #
    #         count_length += self._get_ensemble_count_length(ensemble=swap_ensemble,
    #                                                         created_blocks=created_blocks)
    #         count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
    #                                                         created_blocks=created_blocks)
    #         count_length += self._get_ensemble_count_length(ensemble=swap_ensemble_2,
    #                                                         created_blocks=created_blocks)
    #         # if alternating:
    #         #     count_length += self._get_ensemble_count_length(ensemble=swap_ensemble_alt,
    #         #                                                     created_blocks=created_blocks)
    #         #     count_length += self._get_ensemble_count_length(ensemble=readout_block_ensemble,
    #         #                                                     created_blocks=created_blocks)
    #
    #     # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
    #     # step to the first step.
    #     swap_sequence[-1].go_to = 1
    #
    #     # Trigger the calculation of parameters in the PulseSequence instance
    #     swap_sequence.refresh_parameters()
    #
    #     # add metadata to invoke settings later on
    #     number_of_lasers = num_of_points * 2 if alternating else num_of_points
    #     swap_sequence.measurement_information['alternating'] = alternating
    #     swap_sequence.measurement_information['laser_ignore_list'] = list()
    #     swap_sequence.measurement_information['controlled_variable'] = tau_array
    #     swap_sequence.measurement_information['units'] = ('s', '')
    #     swap_sequence.measurement_information['labels'] = ('Tau', 'Signal')
    #     swap_sequence.measurement_information['number_of_lasers'] = number_of_lasers
    #     if self.gate_channel:
    #         swap_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
    #                                                                             readout_block_ensemble, created_blocks)
    #     else:
    #         swap_sequence.measurement_information['counting_length'] = count_length
    #
    #     # Append PulseSequence to created_sequences list
    #     created_sequences.append(swap_sequence)
    #     return created_blocks, created_ensembles, created_sequences


    def generate_nuclear_rabi(self, name='nuclear_rabi', tau_start=5e-6, tau_step=5e-6,
                                num_of_points=20, rf_amplitude=1e-3, rf_freq=500e3, rf_wait_time=30e-6,
                               rf_pi_pulse=60e-6):
        """
            Choose mw_alt such that it is the other weak pi pulse!
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Create frequency array
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # create the elements
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        # due to ringing of rf pulse
        rf_waiting_element = self._get_idle_element(length=rf_wait_time, increment=0)
        laser_element = self._get_laser_gate_element(length=self.laser_length,
                                                     increment=0)
        delay_element = self._get_delay_gate_element()

        pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                           increment=0,
                                           amp=self.microwave_amplitude,
                                           freq=self.microwave_frequency,
                                           phase=0)

        # Create block and append to created_blocks list
        # I was thinking about adding rf waiting and pi element to this block as it does not change,
        # however then the ensemble count length for the gated mode does not match
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        created_blocks.append(readout_block)

        # creating nuclear swap block which should initialize the nuclear spin
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
        nuclear_readout_block_ensemble = PulseBlockEnsemble(name='{0}_nuclear_readout'.format(name), rotating_frame=False)
        nuclear_readout_block_ensemble.append((readout_block.name, 0))
        nuclear_readout_block_ensemble.append((swap_block.name,0))
        created_ensembles.append(nuclear_readout_block_ensemble)

        if self.sync_channel:
            # Create the last readout PulseBlockEnsemble including a sync trigger
            # Get necessary PulseBlockElements
            sync_element = self._get_sync_element()
            # Create PulseBlock and append PulseBlockElements
            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)
            # Create PulseBlockEnsemble and append block to it
            sync_readout_ensemble = PulseBlockEnsemble(name='{0}_readout_sync'.format(name),
                                                       rotating_frame=False)
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # Create the PulseSequence and append the PulseBlockEnsemble names as sequence steps
        # together with the necessary parameters.
        nuclear_rabi_sequence = PulseSequence(name=name, rotating_frame=False)
        count_length = 0.0

        for tau in tau_array:
            rf_block = PulseBlock(name='{0}{1}_rf_tau'.format(name, tau))
            rf_element_tau = self._get_mw_element(length=tau,
                                              increment=0,
                                              amp=rf_amplitude,
                                              freq=rf_freq,
                                              phase=0)
            rf_block.append(rf_element_tau)
            rf_block.append(rf_waiting_element)
            created_blocks.append(rf_block)
            rf_ensemble = PulseBlockEnsemble(name='{0}{1}_tau'.format(name, tau), rotating_frame=False)
            rf_ensemble.append((rf_block.name, 0))
            created_ensembles.append(rf_ensemble)

            nuclear_rabi_sequence.append(rf_ensemble.name)
            if self.sync_channel and tau == tau_array[-1]:
                # Please be aware that this is not working at the moment!
                nuclear_rabi_sequence.append(sync_readout_ensemble.name)
            else:
                nuclear_rabi_sequence.append(nuclear_readout_block_ensemble.name)
                nuclear_rabi_sequence[-1].repetitions = 1

            count_length += self._get_ensemble_count_length(ensemble=rf_ensemble,
                                                            created_blocks=created_blocks)
            count_length += self._get_ensemble_count_length(ensemble=nuclear_readout_block_ensemble,
                                                            created_blocks=created_blocks)

        # Make the sequence loop infinitely by setting the go_to parameter of the last sequence
        # step to the first step.
        nuclear_rabi_sequence[-1].go_to = 1

        # Trigger the calculation of parameters in the PulseSequence instance
        nuclear_rabi_sequence.refresh_parameters()

        # add metadata to invoke settings later on
        number_of_lasers = num_of_points
        nuclear_rabi_sequence.measurement_information['alternating'] = False
        nuclear_rabi_sequence.measurement_information['laser_ignore_list'] = list()
        nuclear_rabi_sequence.measurement_information['controlled_variable'] = tau_array
        nuclear_rabi_sequence.measurement_information['units'] = ('s', '')
        nuclear_rabi_sequence.measurement_information['labels'] = ('Tau', 'Signal')
        nuclear_rabi_sequence.measurement_information['number_of_lasers'] = number_of_lasers
        if self.gate_channel:
            nuclear_rabi_sequence.measurement_information['counting_length'] = self._get_ensemble_count_length(
                                                                                readout_block_ensemble, created_blocks)
        else:
            nuclear_rabi_sequence.measurement_information['counting_length'] = count_length

        # Append PulseSequence to created_sequences list
        created_sequences.append(nuclear_rabi_sequence)
        return created_blocks, created_ensembles, created_sequences
