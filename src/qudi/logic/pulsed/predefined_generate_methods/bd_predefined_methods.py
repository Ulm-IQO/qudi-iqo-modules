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
    A collection of basic pulse sequences.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    ################################################################################################
    #                             Generation methods for waveforms                                 #
    ################################################################################################

    def generate_bd_rabi(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9, num_of_points=50):
        """Generates a Rabi pulse block ensemble where the pulse length is varied linearly.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_start : float
            Length of the first pulse in seconds.
        tau_step : float
            Increment of the pulse length in seconds.
        num_of_points : int
            Number of tau steps to be generated.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((rabi_block.name, num_of_points - 1))

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

    def generate_bd_pulsedodmr(self, name='pulsedODMR', freq_start=2870.0e6, freq_step=0.2e6, num_of_points=50):
        """Generates a pulsed ODMR pulse block ensemble where the microwave frequency is varied linearly.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        freq_start : float
            Start frequency in Hz.
        freq_step : float
            Frequency step in Hz.
        num_of_points : int
            Number of frequency steps to be generated.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((pulsedodmr_block.name, 0))

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

    def generate_bd_ramsey(self, name='ramsey', tau_start=1.0e-6, tau_step=1.0e-6, num_of_points=50, alternating=True):
        """Generates a Ramsey pulse block ensemble where the free evolution time tau is varied linearly.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_start : float
            Start tau in seconds.
        tau_step : float
            Tau step in seconds.
        num_of_points : int
            Number of tau points to be generated.
        alternating : bool
            If True, the final pi/2 pulse is alternated with either a -pi/2 pulse or 3pi/2 pulse depending on whether an
            analog or digital channel is used for microwave generation respectively. Default is True.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((ramsey_block.name, num_of_points - 1))

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

    def generate_bd_ramsey_from_list(self, name='ramsey', tau_list='[1e-6, 2e-6]', alternating=True):
        """Generates a Ramsey pulse block ensemble where the free evolution time tau is passed as a list.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_list : str
            List of tau values in seconds as a string e.g. '[1e-6, 2e-6]'.
        alternating : bool
            If True, the final pi/2 pulse is alternated with either a -pi/2 pulse or 3pi/2 pulse depending on whether an
            analog or digital channel is used for microwave generation respectively. Default is True.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((ramsey_block.name, 0))

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

    def generate_bd_hahnecho(self, name='hahn_echo', tau_start=0.0e-6, tau_step=1.0e-6, num_of_points=50,
                          alternating=True):
        """Generates a Hahn echo pulse block ensemble where the free evolution time tau is varied linearly.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_start : float
            Start tau in seconds.
        tau_step : float
            Tau step in seconds.
        num_of_points : int
            Number of tau points to be generated.
        alternating : bool
            If True, the final pi/2 pulse is alternated with either a -pi/2 pulse or 3pi/2 pulse depending on whether an
            analog or digital channel is used for microwave generation respectively. Default is True.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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

    def generate_bd_hahnecho_exp(self, name='hahn_echo', tau_start=1.0e-6, tau_end=1.0e-6, num_of_points=50,
                              alternating=True):
        """Generates a Hahn echo pulse block ensemble where the free evolution time tau is varied exponentially.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_start : float
            Start tau in seconds.
        tau_end : float
            End tau in seconds.
        num_of_points : int
            Number of tau points to be generated.
        alternating : bool
            If True, the final pi/2 pulse is alternated with either a -pi/2 pulse or 3pi/2 pulse depending on whether an
            analog or digital channel is used for microwave generation respectively. Default is True.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((hahn_block.name, 0))

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

    def generate_bd_t1(self, name='T1', tau_start=1.0e-6, tau_step=1.0e-6, num_of_points=50, alternating=False):
        """Generates a T1 pulse block ensemble where the free evolution time tau is varied linearly.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_start: float
            Start tau in seconds.
        tau_step: float
            Tau step in seconds.
        num_of_points: int
            Number of tau points.
        alternating: bool
            If True, the final pi/2 pulse is alternated with either a -pi/2 pulse or 3pi/2 pulse depending on whether an
            analog or digital channel is used for microwave generation respectively. Default is False.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((t1_block.name, num_of_points - 1))

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

    def generate_bd_t1_exponential(self, name='T1_exp', tau_start=1.0e-6, tau_end=1.0e-6, num_of_points=50,
                                alternating=False):
        """Generates a T1 pulse block ensemble where the free evolution time tau is varied exponentially.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        tau_start : float
            Start tau in seconds.
        tau_end : float
            End tau in seconds.
        num_of_points : int
            Number of tau points.
        alternating : bool
            If True, the final pi/2 pulse is alternated with either a -pi/2 pulse or 3pi/2 pulse depending on whether an
            analog or digital channel is used for microwave generation respectively. Default is False.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((t1_block.name, 0))

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

    def generate_bd_HHamp(self, name='hh_amp', spinlock_length=20e-6, amp_start=0.05, amp_step=0.01,
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((hhamp_block.name, 0))

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

    def generate_bd_HHtau(self, name='hh_tau', spinlock_amp=0.1, tau_start=1e-6, tau_step=1e-6,
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((hhtau_block.name, num_of_points - 1))

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

    def generate_bd_HHpol(self, name='hh_pol', spinlock_length=20.0e-6, spinlock_amp=0.1,
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((up_block.name, polarization_steps - 1))
        block_ensemble.append((down_block.name, polarization_steps - 1))

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

    def generate_bd_chirpedodmr(self, name='LinearChirpedODMR', mw_freq_center=2870.0e6, freq_range=500.0e6,
                             freq_overlap=20.0e6, num_of_points=50, pulse_length=500e-9, expected_rabi_frequency=30e6,
                             expected_t2=5e-6):
        """Generates a chirped ODMR pulse block ensemble where the microwave frequency is chirped linearly.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        mw_freq_center : float
            Central frequency of the chirped ODMR in Hz.
        freq_range : float
            Target frequency range of the whole ODMR scan in Hz.
        freq_overlap : float
            Additional 'overlap' frequency range for each chirped pulse, i.e. the frequency range of each single chirped
            pulse is (freq_range / num_points) + freq_overlap.
        num_of_points : float
            Number of chirped pulses used in the scan.
        pulse_length : float
            Length of the mw pulse.
        expected_rabi_frequency : float
            Expected value of the Rabi frequency - used to calculate adiabaticity.
        expected_t2 : float
            Expected T2 time - used to check if the chirped pulse is shorter than T2.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((chirpedodmr_block.name, 0))

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

    def generate_bd_AEchirpedodmr(self, name='AllenEberlyChirpODMR', mw_freq_center=2870.0e6, freq_range=500.0e6,
                               freq_overlap=20.0e6, num_of_points=50, pulse_length=500e-9, truncation_ratio=0.1,
                               expected_rabi_frequency=30e6, expected_t2=5e-6, peak_mw_amplitude=0.25):
        """Generates a chirped ODMR pulse block ensemble where the microwave frequency is chirped using the Allen-Eberly
        model.

        Additional information about the Allen-Eberly chirped ODMR
        Chirped ODMR with a pulse, following the Allen-Eberly model: a sech amplitude shape and a
        tanh shaped detuning. The AE pulse has very good properties in terms of adiabaticity and is
        often preferable to the standard Landau-Zener-Stueckelberg-Majorana model with a constant
        amplitude and a linear chirp (see class Chirp). More information about the Allen-Eberly
        model can be found in:
        L. Allen and J. H. Eberly, Optical Resonance and Two-Level Atoms Dover, New York, 1987,
        Analytical solution is given in: F. T. Hioe, Phys. Rev. A 30, 2100 (1984).

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble to be generated.
        mw_freq_center : float
            Central frequency of the chirped ODMR in Hz.
        freq_range : float
            Target frequency range of the whole ODMR scan in Hz.
        freq_overlap : float
            Additional 'overlap' frequency range for each chirped pulse, i.e. the frequency range of each single chirped
            pulse is (freq_range / num_points) + freq_overlap. Truncation is usually negligible for values <0.2.
        num_of_points : float
            Number of chirped pulses used in the scan.
        pulse_length : float
            Length of the mw pulse.
        truncation_ratio : float
            Ratio that characterizes the truncation of the chirped pulse. Specifically, the pulse shape is given by
            sech(t/ truncation ratio /pulse length). truncation_ratio = 0.1 is excellent; the scheme will work for 0.2.
            Higher values truncate the sech pulse and reduce the frequency range of ODMR as the transfer efficiency in
            the wings of the pulse range drops.
        expected_rabi_frequency : float
            Expected value of the Rabi frequency - used to calculate adiabaticity.
        expected_t2 : float
            Expected T2 time - used to check if the chirped pulse is shorter than T2.
        peak_mw_amplitude : float
            Peak amplitude of the Allen-Eberly Chirp pulse.

        Returns
        -------
        created_blocks : list
            List of PulseBlock objects created.
        created_ensembles : list
            List of PulseBlockEnsemble objects created.
        created_sequences : list
            List of PulseSequence objects created.
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
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((chirpedodmr_block.name, 0))

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

    ################################################################################################
    #                             Generation methods for sequences                                 #
    ################################################################################################
    def generate_bd_t1_sequencing(self, name='t1_seq', tau_start=1.0e-6,
                            tau_max=1.0e-3, num_of_points=10):
        """
        T1 sequence adapted for combined AWG + PulseBlaster interfuse.

        Key differences from the standalone AWG version:
        - laser_channel, gate_channel, sync_channel now point to PB channels
            (d_ch5, d_ch6, d_ch7 — set in Generator Settings, NOT changed here).
        - The interfuse's write_sequence() builds a combined PB waveform that
            tiles the per-step PB content in the correct order and inserts an AWG
            trigger at the start of each loop cycle.
        - The AWG sequence steps contain only AWG channels (idle for T1 without
            MW pulse, or pi pulse for inversion-recovery T1). PB channels (laser,
            gate) are handled transparently by the interfuse.
        - All other logic, element building, and sequence construction is unchanged.
        """
        created_blocks    = list()
        created_ensembles = list()
        created_sequences = list()

        # ── Tau array (same as original) ─────────────────────────────────────────
        k_array   = np.unique(
            np.rint(
                np.logspace(0., np.log10(tau_max / tau_start), num_of_points)
            ).astype(int)
        )
        tau_array = k_array * tau_start

        # ── Readout block (same as original) ─────────────────────────────────────
        # _get_laser_gate_element uses self.laser_channel which is now a PB channel
        # (e.g. d_ch5). No change needed here — the element builder is channel-agnostic.
        laser_element   = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element   = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        readout_block.append(waiting_element)
        created_blocks.append(readout_block)

        readout_ensemble = PulseBlockEnsemble(
            name='{0}_readout'.format(name), rotating_frame=False
        )
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        # ── Sync readout block (same as original) ─────────────────────────────────
        # sync_channel now points to a PB channel (e.g. d_ch7).
        # _get_sync_element() is channel-agnostic — no change needed.
        if self.sync_channel:
            sync_element = self._get_sync_element()

            sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
            sync_readout_block.append(laser_element)
            sync_readout_block.append(delay_element)
            sync_readout_block.append(waiting_element)
            sync_readout_block.append(sync_element)
            created_blocks.append(sync_readout_block)

            sync_readout_ensemble = PulseBlockEnsemble(
                name='{0}_readout_sync'.format(name), rotating_frame=False
            )
            sync_readout_ensemble.append((sync_readout_block.name, 0))
            created_ensembles.append(sync_readout_ensemble)

        # ── Tau block (same as original) ──────────────────────────────────────────
        # _get_idle_element creates a LOW-on-all-channels element.
        # In the combined setup this means both AWG and PB channels are idle —
        # the interfuse handles routing each channel to the correct device.
        tau_element = self._get_idle_element(length=tau_start, increment=0)

        tau_block = PulseBlock(name='{0}_tau'.format(name))
        tau_block.append(tau_element)
        created_blocks.append(tau_block)

        tau_ensemble = PulseBlockEnsemble(
            name='{0}_tau'.format(name), rotating_frame=False
        )
        tau_ensemble.append((tau_block.name, 0))
        created_ensembles.append(tau_ensemble)

        # ── Build PulseSequence (same as original) ────────────────────────────────
        # The interfuse's write_sequence() will:
        #   1. Write the AWG sequence (tau + readout waveforms, with repetitions)
        #   2. Build a combined PB waveform by tiling per-step PB content:
        #        [tau PB content × k_1] + [readout PB content] +
        #        [tau PB content × k_2] + [readout PB content] + ...
        #   3. Insert AWG trigger at t=0 of the combined PB waveform
        #   4. Both devices loop at identical rates → stay synchronised
        t1_sequence = PulseSequence(name=name, rotating_frame=False)

        for k in k_array:
            t1_sequence.append(tau_ensemble.name)
            t1_sequence[-1].repetitions = int(k) - 1

            if self.sync_channel and k == k_array[-1]:
                t1_sequence.append(sync_readout_ensemble.name)
            else:
                t1_sequence.append(readout_ensemble.name)

        # Loop infinitely (same as original)
        t1_sequence[-1].go_to = 1

        t1_sequence.refresh_parameters()

        # ── Measurement metadata (same as original) ───────────────────────────────
        t1_sequence.measurement_information['alternating']        = False
        t1_sequence.measurement_information['laser_ignore_list']  = list()
        t1_sequence.measurement_information['controlled_variable'] = tau_array
        t1_sequence.measurement_information['units']              = ('s', '')
        t1_sequence.measurement_information['labels']             = (
            'Tau<sub>pulse spacing</sub>', 'Signal'
        )
        t1_sequence.measurement_information['number_of_lasers']   = len(tau_array)
        t1_sequence.measurement_information['counting_length']    = \
            self._get_sequence_count_length(
                t1_sequence, created_ensembles, created_blocks
            )

        created_sequences.append(t1_sequence)
        return created_blocks, created_ensembles, created_sequences


def generate_test_sequencing(self, name='t1_seq2', tau_start=1.0e-6,
                            tau_max=1.0e-3, num_of_points=10):
    """
    T1 sequence for combined AWG + PulseBlaster setup.

    Sequence structure
    ------------------
    Step 1 (TWAIT=ON, forced by interfuse):
        trigger_ensemble — sync_channel HIGH for one pulse duration.
        The PB fires this at the start of every loop, triggering the AWG
        to begin a complete T1 sweep. The AWG waits here between sweeps.

    Steps 2..2N+1 (alternating, no TWAIT):
        tau_ensemble × k  — free evolution (all channels idle)
        readout_ensemble  — laser ON, detector gate ON (on PB channels)

    Last step: go_to = 1 → AWG returns to trigger step and waits again.

    Requirements
    ------------
    - Activation config:  A1_M1_M2_pb3   (single AWG analog ch + PB channels)
    - sync_channel:       d_ch7           (PB channel wired to AWG TRIGGER IN)
    - laser_channel:      d_ch5           (PB channel wired to laser AOM)
    - gate_channel:       d_ch6           (PB channel wired to photon counter gate)

    Parameters
    ----------
    name : str
        Name of the PulseSequence.
    tau_start : float
        Start tau in seconds (minimum free evolution time).
    tau_max : float
        Maximum tau in seconds.
    num_of_points : int
        Number of logarithmically spaced tau points.

    Returns
    -------
    created_blocks, created_ensembles, created_sequences : list, list, list
    """
    created_blocks    = list()
    created_ensembles = list()
    created_sequences = list()

    # ── Sanity check ──────────────────────────────────────────────────────────
    if not self.sync_channel:
        self.log.error(
            'sync_channel must be configured for combined AWG+PB T1 sequence.\n'
            'Set sync_channel to the PB channel connected to the AWG TRIGGER IN BNC\n'
            '(e.g. d_ch7 if using pb_channels=[0,1,2,3,4] with pb_channel_d_offset=5).'
        )
        return created_blocks, created_ensembles, created_sequences

    # ── Tau array ─────────────────────────────────────────────────────────────
    # Logarithmically spaced steps in multiples of tau_start
    k_array = np.unique(
        np.rint(
            np.logspace(0., np.log10(tau_max / tau_start), num_of_points)
        ).astype(int)
    )
    tau_array = k_array * tau_start

    # =========================================================================
    # BLOCK / ENSEMBLE CONSTRUCTION
    # =========================================================================

    # ── 1. Trigger ensemble ───────────────────────────────────────────────────
    # Played ONCE as step 1 of the sequence (repetitions=0).
    # TWAIT=ON is forced on step 1 by the interfuse's write_sequence().
    # The PB combined waveform starts with this content, so the trigger
    # pulse is at t=0 of every PB loop — releasing the AWG's TWAIT and
    # starting a complete T1 sweep.
    #
    # _get_sync_element() creates a pulse on self.sync_channel (= d_ch7).
    # On the PB side this HIGH pulse fires the AWG trigger input.
    # On the AWG side d_ch7 is a PB channel so it has no effect on the
    # AWG waveform itself — the AWG waveform for this step is simply idle.
    sync_element = self._get_sync_element()

    trigger_block = PulseBlock(name='{0}_trigger'.format(name))
    trigger_block.append(sync_element)
    created_blocks.append(trigger_block)

    trigger_ensemble = PulseBlockEnsemble(
        name='{0}_trigger'.format(name), rotating_frame=False
    )
    trigger_ensemble.append((trigger_block.name, 0))
    created_ensembles.append(trigger_ensemble)

    # ── 2. Readout ensemble ───────────────────────────────────────────────────
    # laser_channel and gate_channel are PB channels (d_ch5, d_ch6).
    # The AWG analog output (a_ch1) is idle during readout — the readout
    # window only uses the PB laser and gate pulses.
    laser_element   = self._get_laser_gate_element(length=self.laser_length, increment=0)
    delay_element   = self._get_delay_gate_element()
    waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

    readout_block = PulseBlock(name='{0}_readout'.format(name))
    readout_block.append(laser_element)
    readout_block.append(delay_element)
    readout_block.append(waiting_element)
    created_blocks.append(readout_block)

    readout_ensemble = PulseBlockEnsemble(
        name='{0}_readout'.format(name), rotating_frame=False
    )
    readout_ensemble.append((readout_block.name, 0))
    created_ensembles.append(readout_ensemble)

    # ── 3. Tau ensemble ───────────────────────────────────────────────────────
    # Pure idle: all channels LOW/zero. The free evolution period.
    # On the AWG: a_ch1 = 0V (no MW pulse during tau).
    # On the PB:  all channels LOW (no laser, no gate, no trigger during tau).
    #
    # This ensemble is repeated k times per sequence step, giving total
    # free evolution time = k * tau_start.
    tau_element = self._get_idle_element(length=tau_start, increment=0)

    tau_block = PulseBlock(name='{0}_tau'.format(name))
    tau_block.append(tau_element)
    created_blocks.append(tau_block)

    tau_ensemble = PulseBlockEnsemble(
        name='{0}_tau'.format(name), rotating_frame=False
    )
    tau_ensemble.append((tau_block.name, 0))
    created_ensembles.append(tau_ensemble)

    # =========================================================================
    # SEQUENCE CONSTRUCTION
    # =========================================================================
    #
    # Final sequence (N = len(k_array) tau points):
    #
    #   Step  1         : trigger_ensemble  (repetitions=0 → plays once)
    #   Step  2         : tau_ensemble      (repetitions=k1-1 → plays k1 times)
    #   Step  3         : readout_ensemble  (repetitions=0 → plays once)
    #   Step  4         : tau_ensemble      (repetitions=k2-1 → plays k2 times)
    #   Step  5         : readout_ensemble  (repetitions=0 → plays once)
    #     ...
    #   Step  2N        : tau_ensemble      (repetitions=kN-1)
    #   Step  2N+1      : readout_ensemble  (go_to=1)
    #
    # Total steps = 1 + 2*N
    # AWG go_to=1 on last step → returns to trigger step, waits for PB
    # PB loops naturally → fires trigger → next T1 average begins

    t1_sequence = PulseSequence(name=name, rotating_frame=False)

    # Step 1: trigger (TWAIT=ON forced by interfuse.write_sequence)
    t1_sequence.append(trigger_ensemble.name)
    t1_sequence[-1].repetitions = 0    # plays exactly once per sweep

    # Steps 2..2N+1: tau × k, then readout
    for k in k_array:
        # Tau step: free evolution for k * tau_start
        t1_sequence.append(tau_ensemble.name)
        t1_sequence[-1].repetitions = int(k) - 1   # AWG loops k times total

        # Readout step: laser + gate on PB
        t1_sequence.append(readout_ensemble.name)
        t1_sequence[-1].repetitions = 0             # plays once

    # After last readout: return to trigger step → wait for next PB trigger
    t1_sequence[-1].go_to = 1

    # ── Finalise sequence ─────────────────────────────────────────────────────
    t1_sequence.refresh_parameters()

    t1_sequence.measurement_information['alternating']         = False
    t1_sequence.measurement_information['laser_ignore_list']   = list()
    t1_sequence.measurement_information['controlled_variable'] = tau_array
    t1_sequence.measurement_information['units']               = ('s', '')
    t1_sequence.measurement_information['labels']              = (
        'Tau<sub>pulse spacing</sub>', 'Signal'
    )
    # One laser readout per tau point
    t1_sequence.measurement_information['number_of_lasers']    = len(tau_array)
    t1_sequence.measurement_information['counting_length']     = \
        self._get_sequence_count_length(
            t1_sequence, created_ensembles, created_blocks
        )

    created_sequences.append(t1_sequence)
    return created_blocks, created_ensembles, created_sequences