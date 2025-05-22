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
from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockEnsemble
from qudi.logic.pulsed.pulse_objects import PredefinedGeneratorBase

"""
This is the application
    xy8_element_list = self._get_xy8_elements(xy8_order=xy8_order, tau=tau_DD, phase_init=0.0, phase_final=90.0)
    xy8_alt_element_list = self._get_xy8_elements(xy8_order=xy8_order, tau=tau_DD, phase_init=0.0,
                                                  phase_final=-90.0)
    # Create block and append to created_blocks list
    corrspec_block = PulseBlock(name=name)
    corrspec_block.extend(xy8_element_list)
"""


class DDPredefinedGenerator(PredefinedGeneratorBase):
    """

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _get_xy8_elements(self, xy8_order=4, tau_start=1e-6, tau_step=0.01e-6, phase_init=0.0, phase_final=0.0,
                          use_random_phase=False, correlation_factor=1):
        """
        Generates an XY8 sequence, where the pulse spacing tau is the controlled variable.
        Optionally adds a random phase in each block to suppress spurious harmonics.
        Supports correlation measurements by adding phase shifts in parts of the sequence.

        Parameters
        ----------
        xy8_order : int
            Number of XY8 blocks
        tau_start : float
            Start value of the tau array
        tau_step : float
            Step size of the tau array
        phase_init : float
            Phase shift of the initial pi/2 pulse
        phase_final : float
            Phase shift of the final pi/2 pulse
        correlation_factor : int
            Correlation number, where the random phase is correlated every correlation_factor blocks
            xy8_order = xy8_order*correlation_factor
        use_random_phase : bool
            Whether to use random phase shifts in the XY8 blocks
        correlation_factor : int
            Divides the sequence into parts, adding phase shifts after each part

        Returns
        -------
        xy8_elements : list
        """
        tauhalf_start = tau_start / 2 - 3 / 8 * self.rabi_period
        tau_start = tau_start - self.rabi_period / 2

        pihalf_init_element = self._get_mw_element(length=self.rabi_period / 4, increment=0,
                                                   amp=self.microwave_amplitude, freq=self.microwave_frequency,
                                                   phase=phase_init)
        pihalf_final_element = self._get_mw_element(length=self.rabi_period / 4, increment=0,
                                                    amp=self.microwave_amplitude, freq=self.microwave_frequency,
                                                    phase=phase_final)

        tauhalf_element = self._get_idle_element(length=tauhalf_start, increment=tau_step / 2)
        tau_element = self._get_idle_element(length=tau_start, increment=tau_step)

        xy8_elements = [pihalf_init_element, tauhalf_element]
        total_blocks = xy8_order * correlation_factor

        if use_random_phase:
            random_phase = np.random.uniform(0, 360, xy8_order)
            if correlation_factor > 1:
                phase_step = 360.0 / correlation_factor * np.random.choice([-1,1])
                random_phase = [rp + i*phase_step for rp in random_phase for i in range(correlation_factor)]
        else:
            random_phase = np.zeros(total_blocks)

        for block in range(total_blocks):

            pix_element = self._get_mw_element(length=self.rabi_period / 2, increment=0,
                                               amp=self.microwave_amplitude, freq=self.microwave_frequency,
                                               phase=0 + random_phase[block])
            piy_element = self._get_mw_element(length=self.rabi_period / 2, increment=0,
                                               amp=self.microwave_amplitude, freq=self.microwave_frequency,
                                               phase=90 + random_phase[block])

            xy8_elements.extend([pix_element, tau_element, piy_element, tau_element,
                                 pix_element, tau_element, piy_element, tau_element,
                                 piy_element, tau_element, pix_element, tau_element,
                                 piy_element, tau_element, pix_element])

            if block != total_blocks - 1:
                xy8_elements.append(tau_element)

        xy8_elements.extend([tauhalf_element, pihalf_final_element])
        return xy8_elements

    def generate_xy8_tau(self, name='xy8_tau', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy8_order=4, alternating=True):
        """
        Generates an XY8 sequence, where the pulse spacing tau is the controlled variable.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble
        tau_start : float
            Start value of the tau array
        tau_step : float
            Step size of the tau array
        num_of_points : int
            Number of points in the tau array
        xy8_order : int
            Number of XY8 blocks
        alternating : bool
            If the sequence should be alternating with 3pi/2 and pi/2 pulses at the end

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # Create the XY8 elements using the helper function
        xy8_elements = self._get_xy8_elements(xy8_order=xy8_order, tau_start=tau_start, tau_step=tau_step)

        # Create block and append XY8 elements
        xy8_block = PulseBlock(name=name)
        xy8_block.extend(xy8_elements)

        # Add final elements for laser, delay, and waiting
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        xy8_block.extend([laser_element, delay_element, waiting_element])

        # Handle alternating sequence if enabled
        if alternating:
            alternating_elements = self._get_xy8_elements(xy8_order=xy8_order,
                                                          tau_start=tau_start, tau_step=tau_step,
                                                          phase_final=180)
            xy8_block.extend(alternating_elements)
            xy8_block.extend([laser_element, delay_element, waiting_element])

        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Add metadata
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created ensembles
        created_ensembles.append(block_ensemble)

        return created_blocks, created_ensembles, created_sequences

    def generate_xy8_freq(self, name='xy8_freq', freq_start=0.1e6, freq_step=0.01e6,
                          num_of_points=50, xy8_order=4, alternating=True):
        """
        Generates an XY8 sequence, where the frequency, half of the inverse pulse spacing tau,
        is the controlled variable.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble
        freq_start : float
            Start value of the frequency array
        freq_step : float
            Step size of the frequency array
        num_of_points : int
            Number of points in the frequency array
        xy8_order : int
            Number of XY8 blocks
        alternating : bool
            If the sequence should be alternating with 3pi/2 and pi/2 pulses at the end

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get frequency array for measurement ticks
        freq_array = freq_start + np.arange(num_of_points) * freq_step
        # get tau array from freq array
        tau_array = 1 / (2 * freq_array)

        # Create the XY8 elements using the helper function
        xy8_block = PulseBlock(name=name)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

        for tau in tau_array:
            xy8_elements = self._get_xy8_elements(xy8_order=xy8_order, tau_start=tau, tau_step=0)

            # Create block and append XY8 elements
            xy8_block.extend(xy8_elements)

            # Add final elements for laser, delay, and waiting
            xy8_block.extend([laser_element, delay_element, waiting_element])

            # Handle alternating sequence if enabled
            if alternating:
                alternating_elements = self._get_xy8_elements(xy8_order=xy8_order,
                                                              tau_start=tau, tau_step=0, phase_final=180)
                xy8_block.extend(alternating_elements)
                xy8_block.extend([laser_element, delay_element, waiting_element])

        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Add metadata
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['labels'] = ('Frequency', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created ensembles
        created_ensembles.append(block_ensemble)

        return created_blocks, created_ensembles, created_sequences

    def generate_xy8_random_tau(self, name='xy8_random_tau', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                                xy8_order=24, alternating=True):
        """
        Generates an XY8 sequence with random phase in each block,
        where the pulse spacing tau is the controlled variable.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble
        tau_start : float
            Start value of the tau array
        tau_step : float
            Step size of the tau array
        num_of_points : int
            Number of points in the tau array
        xy8_order : int
            Number of XY8 blocks
        alternating : bool
            If the sequence should be alternating with 3pi/2 and pi/2 pulses at the end

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # Create the XY8 elements using the helper function
        xy8_elements = self._get_xy8_elements(xy8_order=xy8_order, tau_start=tau_start, tau_step=tau_step,
                                              use_random_phase = True)

        # Create block and append XY8 elements
        xy8_block = PulseBlock(name=name)
        xy8_block.extend(xy8_elements)

        # Add final elements for laser, delay, and waiting
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        xy8_block.extend([laser_element, delay_element, waiting_element])

        # Handle alternating sequence if enabled
        if alternating:
            alternating_elements = self._get_xy8_elements(xy8_order=xy8_order,
                                                          tau_start=tau_start, phase_final=180,
                                                          use_random_phase = True)
            xy8_block.extend(alternating_elements)
            xy8_block.extend([laser_element, delay_element, waiting_element])

        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Add metadata
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created ensembles
        created_ensembles.append(block_ensemble)

        return created_blocks, created_ensembles, created_sequences

    def generate_xy8_random_freq(self, name='xy8_random_freq', freq_start=0.1e6, freq_step=0.01e6,
                                 num_of_points=50, xy8_order=4, alternating=True):
        """
        Generates an XY8 sequence with random phase in each block,
        where the frequency, half of the inverse pulse spacing tau, is the controlled variable.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble
        freq_start : float
            Start value of the frequency array
        freq_step : float
            Step size of the frequency array
        num_of_points : int
            Number of points in the frequency array
        xy8_order : int
            Number of XY8 blocks
        alternating : bool
            If the sequence should be alternating with 3pi/2 and pi/2 pulses at the end

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get frequency array for measurement ticks
        freq_array = freq_start + np.arange(num_of_points) * freq_step
        # get tau array from freq array
        tau_array = 1 / (2 * freq_array)

        # Create the XY8 elements using the helper function
        xy8_block = PulseBlock(name=name)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

        for tau in tau_array:
            xy8_elements = self._get_xy8_elements(xy8_order=xy8_order, tau_start=tau, tau_step=0,
                                                  use_random_phase=True)

            # Create block and append XY8 elements
            xy8_block.extend(xy8_elements)

            # Add final elements for laser, delay, and waiting
            xy8_block.extend([laser_element, delay_element, waiting_element])

            # Handle alternating sequence if enabled
            if alternating:
                alternating_elements = self._get_xy8_elements(xy8_order=xy8_order,
                                                              tau_start=tau, tau_step=0, phase_final=180,
                                                              use_random_phase=True)
                xy8_block.extend(alternating_elements)
                xy8_block.extend([laser_element, delay_element, waiting_element])

        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Add metadata
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['labels'] = ('Frequency', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created ensembles
        created_ensembles.append(block_ensemble)

        return created_blocks, created_ensembles, created_sequences

    def generate_xy8_random_tau_corr(self, name='xy8_rp_taucorr', tau_start=50e-9, tau_step=2e-9,
                                   num_of_points=50, xy8_order_xcorr=12, correlation_factor=2, alternating=True):
        """
        Generates an XY8 sequence with random phase in each block correlated every correlation_factor blocks,
        where the pulse spacing tau is the controlled variable.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble
        tau_start : float
            Start value of the tau array
        tau_step : float
            Step size of the tau array
        num_of_points : int
            Number of points in the tau array
        xy8_order_xcorr : int
            Number of XY8 blocks that will be multiplied by correlation_factor
        correlation_factor : int
            Correlation number, where the random phase is correlated every correlation_factor blocks
            xy8_order = xy8_order*correlation_factor
        alternating : bool
            If the sequence should be alternating with 3pi/2 and pi/2 pulses at the end

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # Get tau array for measurement ticks
        tau_array = tau_start + np.arange(num_of_points) * tau_step

        # Create the XY8 elements using the helper function
        xy8_elements = self._get_xy8_elements(xy8_order=xy8_order_xcorr, tau_start=tau_start, tau_step=tau_step,
                                              correlation_factor=correlation_factor, use_random_phase=True)

        # Create block and append XY8 elements
        xy8_block = PulseBlock(name=name)
        xy8_block.extend(xy8_elements)

        # Add final elements for laser, delay, and waiting
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        xy8_block.extend([laser_element, delay_element, waiting_element])

        # Handle alternating sequence if enabled
        if alternating:
            alternating_elements = self._get_xy8_elements(xy8_order=xy8_order_xcorr, tau_start=tau_start,
                                                          tau_step=tau_step, phase_final=180,
                                                          correlation_factor=correlation_factor, use_random_phase=True)
            xy8_block.extend(alternating_elements)
            xy8_block.extend([laser_element, delay_element, waiting_element])

        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Add metadata
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created ensembles
        created_ensembles.append(block_ensemble)

        return created_blocks, created_ensembles, created_sequences


    def generate_xy8_random_freq_corr(self, name='xy8_rp_freqcorr', freq_start=0.1e6, freq_step=0.01e6,
                                      num_of_points=50, xy8_order_xcorr=12, correlation_factor=2, alternating=True):
        """
        Generates an XY8 sequence with random phase in each block correlated every correlation_factor blocks,
        where the frequency, half of the inverse pulse spacing tau, is the controlled variable.

        Parameters
        ----------
        name : str
            Name of the PulseBlockEnsemble
        freq_start : float
            Start value of the frequency array
        freq_step : float
            Step size of the frequency array
        num_of_points : int
            Number of points in the tau array
        xy8_order_xcorr : int
            Number of XY8 blocks that will be multiplied by correlation_factor
        correlation_factor : int
            Correlation number, where the random phase is correlated every correlation_factor blocks
            xy8_order = xy8_order*correlation_factor
        alternating : bool
            If the sequence should be alternating with 3pi/2 and pi/2 pulses at the end

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # get frequency array for measurement ticks
        freq_array = freq_start + np.arange(num_of_points) * freq_step
        # get tau array from freq array
        tau_array = 1 / (2 * freq_array)

        # Create the XY8 elements using the helper function
        xy8_block = PulseBlock(name=name)
        laser_element = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

        for tau in tau_array:
            xy8_elements = self._get_xy8_elements(xy8_order=xy8_order_xcorr, tau_start=tau, tau_step=0,
                                                  correlation_factor=correlation_factor, use_random_phase=True)

            # Create block and append XY8 elements
            xy8_block.extend(xy8_elements)

            # Add final elements for laser, delay, and waiting
            xy8_block.extend([laser_element, delay_element, waiting_element])

            # Handle alternating sequence if enabled
            if alternating:
                alternating_elements = self._get_xy8_elements(xy8_order=xy8_order_xcorr,
                                                              tau_start=tau, tau_step=0, phase_final=180,
                                                              correlation_factor=correlation_factor,
                                                              use_random_phase=True)
                xy8_block.extend(alternating_elements)
                xy8_block.extend([laser_element, delay_element, waiting_element])

        created_blocks.append(xy8_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=True)
        block_ensemble.append((xy8_block.name, num_of_points - 1))

        # Add metadata
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        number_of_lasers = num_of_points * 2 if alternating else num_of_points
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = tau_array
        block_ensemble.measurement_information['units'] = ('s', '')
        block_ensemble.measurement_information['labels'] = ('Tau', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = number_of_lasers
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # Append ensemble to created ensembles
        created_ensembles.append(block_ensemble)

        return created_blocks, created_ensembles, created_sequences



    ################################################################################################
    #                             Generation methods for sequences                                 #
    ################################################################################################
