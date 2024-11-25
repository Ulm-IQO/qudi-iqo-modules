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


class DDPredefinedGenerator(PredefinedGeneratorBase):
    """

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def generate_xy8_tau(self, name='xy8_tau', tau_start=0.5e-6, tau_step=0.01e-6, num_of_points=50,
                         xy8_order=4, alternating=True):
        """
        Generates a XY8 sequence, where the pulse spacing tau is the controlled variable.

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
        Generates a XY8 sequence, same as in generate_xy8, but with frequency as controlled variable.

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
    
    def generate_xy8_random_tau(self, name='xy8_random_tau', tau_start=50e-9, tau_step=2e-9, num_of_points=50,
                         xy8_order=24, alternating=True):
        """
        Generates the RXY8 dynamical decoupling sequence,
        where a random phase is added in each block in order to supress spurious harmonics.

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

        tauhalf_element = self._get_idle_element(length=tau_start / 2, increment=tau_step / 2)
        tau_element = self._get_idle_element(length=tau_start, increment=tau_step)

        # Create block and append to created_blocks list
        xy8_block = PulseBlock(name=name)
        xy8_block.append(pihalf_element)
        xy8_block.append(tauhalf_element)
        for n in range(xy8_order):
            random_phase = np.random.uniform(0,360)
            pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase)
            piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase)


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
                random_phase = np.random.uniform(0,360)
                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase)

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
        if self.sync_channel:
            sync_block = PulseBlock(name='sync_trigger')
            sync_block.append(self._get_sync_element())
            created_blocks.append(sync_block)
            block_ensemble.append((sync_block.name, 0))

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
    
    def generate_xy8_random_freq(self, name='xy8_random_freq', freq_start=0.1e6, freq_step=0.01e6,
                          num_of_points=50, xy8_order=4, alternating=True):
        """
        Generates the RXY8 dynamical decoupling sequence, but with tau=1/(2*tau) as controlled variable.

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
        xy8_block = PulseBlock(name=name)

        for ii, tau in enumerate(tau_pspacing_array):
            tauhalf_element = self._get_idle_element(length=tau / 2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy8_block.append(pihalf_element)
            xy8_block.append(tauhalf_element)
            for n in range(xy8_order):
                random_phase = np.random.uniform(0,360)
                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase)


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
                    random_phase = np.random.uniform(0,360)
                    pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase)
                    piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase)

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
        if self.sync_channel:
            sync_block = PulseBlock(name='sync_trigger')
            sync_block.append(self._get_sync_element())
            created_blocks.append(sync_block)
            block_ensemble.append((sync_block.name, 0))

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
    
    def generate_xy8_random_g2_tau(self, name='xy8_random_g2_tau', tau_start=50e-9, tau_step=2e-9,
                                    num_of_points=50, xy8_order_x2=12, alternating=True):
        """
        Generates a RXY8 dynamical decoupling sequence with correlation g=2,
        where the random phase are correlated every 2 blocks with the second opposite to the first,
        thus improving suppresion of pulse errors.

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
        xy8_order_x2 : int
            Number of XY8 blocks times 2
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

        tauhalf_element = self._get_idle_element(length=tau_start / 2, increment=tau_step / 2)
        tau_element = self._get_idle_element(length=tau_start, increment=tau_step)

        # Create block and append to created_blocks list
        xy8_block = PulseBlock(name=name)
        xy8_block.append(pihalf_element)
        xy8_block.append(tauhalf_element)

        for n in range(xy8_order_x2):
            random_phase = np.random.uniform(0,360)
            pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase)
            piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase)


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

            xy8_block.append(tau_element)

            pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=180+random_phase)
            piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+180+random_phase)

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
            if n != xy8_order_x2 - 1:
                xy8_block.append(tau_element)

        xy8_block.append(tauhalf_element)
        xy8_block.append(pihalf_element)
        xy8_block.append(laser_element)
        xy8_block.append(delay_element)
        xy8_block.append(waiting_element)


        if alternating:
            for n in range(xy8_order_x2):
                random_phase = np.random.uniform(0,360)
                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase)


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

                xy8_block.append(tau_element)

                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=180+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+180+random_phase)

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
                if n != xy8_order_x2 - 1:
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
        if self.sync_channel:
            sync_block = PulseBlock(name='sync_trigger')
            sync_block.append(self._get_sync_element())
            created_blocks.append(sync_block)
            block_ensemble.append((sync_block.name, 0))

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
    
    def generate_xy8_random_g2_freq(self, name='xy8_random_g2_freq', freq_start=0.1e6, freq_step=0.01e6,
                                    num_of_points=50, xy8_order_x2=12, alternating=True):
        """
        Generates a RXY8 dynamical decoupling sequence with correlation g=2,with tau=1/(2*tau) as controlled variable.

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
        xy8_order_x2 : int
            Number of XY8 blocks times 2
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
        xy8_block = PulseBlock(name=name)
        for ii, tau in enumerate(tau_pspacing_array):
            tauhalf_element = self._get_idle_element(length=tau / 2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy8_block.append(pihalf_element)
            xy8_block.append(tauhalf_element)
            for n in range(xy8_order_x2):
                random_phase = np.random.uniform(0,360)
                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase)


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

                xy8_block.append(tau_element)

                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=180+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+180+random_phase)

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
                if n != xy8_order_x2 - 1:
                    xy8_block.append(tau_element)

            xy8_block.append(tauhalf_element)
            xy8_block.append(pihalf_element)
            xy8_block.append(laser_element)
            xy8_block.append(delay_element)
            xy8_block.append(waiting_element)


            if alternating:
                for n in range(xy8_order_x2):
                    random_phase = np.random.uniform(0,360)
                    pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase)
                    piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase)


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

                    xy8_block.append(tau_element)

                    pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=180+random_phase)
                    piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+180+random_phase)

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
                    if n != xy8_order_x2 - 1:
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
        if self.sync_channel:
            sync_block = PulseBlock(name='sync_trigger')
            sync_block.append(self._get_sync_element())
            created_blocks.append(sync_block)
            block_ensemble.append((sync_block.name, 0))

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

    def generate_xy8_random_g3_tau(self, name='xy8_random_g3_tau', tau_start=50e-9, tau_step=2e-9,
                                    num_of_points=50, xy8_order_x3=8, alternating=True):
        """
        Generates a RXY8 dynamical decoupling sequence with correlation g=3, where the random phase are correlated every 3 blocks adding up to 0.

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
        xy8_order_x3 : int
            Number of XY8 blocks times 3
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

        tauhalf_element = self._get_idle_element(length=tau_start / 2, increment=tau_step / 2)
        tau_element = self._get_idle_element(length=tau_start, increment=tau_step)

        # Create block and append to created_blocks list
        xy8_block = PulseBlock(name=name)
        xy8_block.append(pihalf_element)
        xy8_block.append(tauhalf_element)

        for n in range(xy8_order_x3):
            random_phase = np.random.uniform(0,360)
            random_increment = np.random.choice([-120, 120])
            pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase)
            piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase)


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

            xy8_block.append(tau_element)

            pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase+random_increment)
            piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase+random_increment)

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

            xy8_block.append(tau_element)

            pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase+2*random_increment)
            piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase+2*random_increment)

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

            if n != xy8_order_x3 - 1:
                xy8_block.append(tau_element)

        xy8_block.append(tauhalf_element)
        xy8_block.append(pihalf_element)
        xy8_block.append(laser_element)
        xy8_block.append(delay_element)
        xy8_block.append(waiting_element)

        if alternating:
            for n in range(xy8_order_x3):
                random_phase = np.random.uniform(0,360)
                random_increment = np.random.choice([-120, 120])
                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase)


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

                xy8_block.append(tau_element)

                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase+random_increment)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase+random_increment)

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

                xy8_block.append(tau_element)

                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=0+random_phase+2*random_increment)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                               increment=0,
                                               amp=self.microwave_amplitude,
                                               freq=self.microwave_frequency,
                                               phase=90+random_phase+2*random_increment)

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

                if n != xy8_order_x3 - 1:
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
        if self.sync_channel:
            sync_block = PulseBlock(name='sync_trigger')
            sync_block.append(self._get_sync_element())
            created_blocks.append(sync_block)
            block_ensemble.append((sync_block.name, 0))

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
    
    def generate_xy8_random_g3_freq(self, name='xy8_random_g3_freq', freq_start=0.1e6, freq_step=0.01e6,
                                    num_of_points=50, xy8_order_x3=8, alternating=True):
        """
        Generates a RXY8 dynamical decoupling sequence with correlation g=3, where the random phase are correlated every 3 blocks adding up to 0.

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
        xy8_order_x3 : int
            Number of XY8 blocks times 3
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
        xy8_block = PulseBlock(name=name)

        for ii, tau in enumerate(tau_pspacing_array):
            tauhalf_element = self._get_idle_element(length=tau / 2, increment=0)
            tau_element = self._get_idle_element(length=tau, increment=0)
            xy8_block.append(pihalf_element)
            xy8_block.append(tauhalf_element)

            for n in range(xy8_order_x3):
                random_phase = np.random.uniform(0,360)
                random_increment = np.random.choice([-120, 120])
                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase)


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

                xy8_block.append(tau_element)

                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase+random_increment)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase+random_increment)

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

                xy8_block.append(tau_element)

                pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase+2*random_increment)
                piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase+2*random_increment)

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

                if n != xy8_order_x3 - 1:
                    xy8_block.append(tau_element)

            xy8_block.append(tauhalf_element)
            xy8_block.append(pihalf_element)
            xy8_block.append(laser_element)
            xy8_block.append(delay_element)
            xy8_block.append(waiting_element)

            if alternating:
                for n in range(xy8_order_x3):
                    random_phase = np.random.uniform(0,360)
                    random_increment = np.random.choice([-120, 120])
                    pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase)
                    piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase)


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

                    xy8_block.append(tau_element)

                    pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase+random_increment)
                    piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase+random_increment)

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

                    xy8_block.append(tau_element)

                    pix_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=0+random_phase+2*random_increment)
                    piy_element = self._get_mw_element(length=self.rabi_period / 2,
                                                increment=0,
                                                amp=self.microwave_amplitude,
                                                freq=self.microwave_frequency,
                                                phase=90+random_phase+2*random_increment)

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

                    if n != xy8_order_x3 - 1:
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
        if self.sync_channel:
            sync_block = PulseBlock(name='sync_trigger')
            sync_block.append(self._get_sync_element())
            created_blocks.append(sync_block)
            block_ensemble.append((sync_block.name, 0))

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
