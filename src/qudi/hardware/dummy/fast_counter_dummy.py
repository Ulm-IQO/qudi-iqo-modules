# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware dummy for fast counting devices.

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

import time
import os
import numpy as np
from copy import deepcopy

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.hardware.dummy.pulse_data_simulation import simulate_photon_trace, select_pulse_edges


class FastCounterDummy(FastCounterInterface):
    """ Implementation of the FastCounter interface methods for a dummy usage.

    Example config for copy-paste:

    fastcounter_dummy:
        module.Class: 'dummy.fast_counter_dummy.FastCounterDummy'
        options:
            gated: False
            # load_trace: null  # optional 950 MHz counts file; rows are gates
            # ungated_points: 50  # standalone fallback, overwritten by measurement metadata
            # laser_length: 3e-6
            # laser_delay: 500e-9
            # rabi_period: 100e-9
            # poisson_noise: False
            # random_seed: null

    """

    # config option
    _gated = ConfigOption('gated', False, missing='warn')
    trace_path = ConfigOption('load_trace', None)
    _ungated_points = ConfigOption('ungated_points', 50)
    _laser_length = ConfigOption('laser_length', 3e-6)
    _laser_delay = ConfigOption('laser_delay', 500e-9)
    _rabi_period = ConfigOption('rabi_period', 100e-9)
    _poisson_noise = ConfigOption('poisson_noise', False)
    _random_seed = ConfigOption('random_seed', None)

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self.statusvar = 0
        self._binwidth = 1
        self._gate_length_bins = 8192
        self._number_of_gates = 0
        self._ungated_number_of_pulses = int(self._ungated_points or 50)
        self._sampling_information = {}
        self._measurement_settings = {}
        self._extraction_method = None
        self._count_data = np.zeros((0, 0) if self._gated else 0, dtype='int64')
        return

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        self.statusvar = -1
        return

    def get_constraints(self):
        """ Retrieve the hardware constrains from the Fast counting device.

        @return dict: dict with keys being the constraint names as string and
                      items are the definition for the constaints.

         The keys of the returned dictionary are the str name for the constraints
        (which are set in this method).

                    NO OTHER KEYS SHOULD BE INVENTED!

        If you are not sure about the meaning, look in other hardware files to
        get an impression. If still additional constraints are needed, then they
        have to be added to all files containing this interface.

        The items of the keys are again dictionaries which have the generic
        dictionary form:
            {'min': <value>,
             'max': <value>,
             'step': <value>,
             'unit': '<value>'}

        Only the key 'hardware_binwidth_list' differs, since they
        contain the list of possible binwidths.

        If the constraints cannot be set in the fast counting hardware then
        write just zero to each key of the generic dicts.
        Note that there is a difference between float input (0.0) and
        integer input (0), because some logic modules might rely on that
        distinction.

        ALL THE PRESENT KEYS OF THE CONSTRAINTS DICT MUST BE ASSIGNED!
        """

        constraints = dict()

        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seconds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = [1/950e6, 2/950e6, 4/950e6, 8/950e6]

        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates = 0):
        """ Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse
                                    sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, gate_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual set gate length in seconds
                    number_of_gates: the number of gated, which are accepted
        """
        if self.statusvar in (2, 3):
            raise RuntimeError('Stop the counter before configuring it.')
        if not np.isfinite(bin_width_s) or bin_width_s <= 0:
            raise ValueError('Bin width must be finite and positive.')
        if not np.isfinite(record_length_s) or record_length_s <= 0:
            raise ValueError('Record length must be finite and positive.')
        widths = np.array(self.get_constraints()['hardware_binwidth_list'])
        actual_binwidth = float(widths[np.argmin(abs(widths - bin_width_s))])
        bins = int(np.rint(record_length_s / actual_binwidth))
        if bins < 1:
            raise ValueError('Record length must contain at least one time bin.')
        gates = int(number_of_gates) if self._gated else 0
        if self._gated and (gates < 1 or gates != number_of_gates):
            raise ValueError('Gated acquisition requires a positive integer gate count.')
        self._binwidth = round(actual_binwidth * 950e6)
        self._gate_length_bins = bins
        self._number_of_gates = gates
        self._count_data = np.zeros((gates, bins) if self._gated else bins, dtype='int64')
        self.statusvar = 1
        return actual_binwidth, bins * actual_binwidth, gates

    def set_measurement_point_count(self, number_of_points):
        """Set the standalone ungated demo pulse count for the next start."""
        if int(number_of_points) != number_of_points or number_of_points < 1:
            raise ValueError('Pulse count must be a positive integer.')
        self._ungated_number_of_pulses = int(number_of_points)

    def set_ungated_points(self, number_of_points=None):
        """Compatibility alias for the PR's standalone pulse-count override."""
        self.set_measurement_point_count(50 if number_of_points is None else number_of_points)
        return self._ungated_number_of_pulses

    def set_simulation_settings(self, sampling_information, measurement_settings):
        """Optional capability used by measurement logic before starting the dummy.

        Copy public sequence metadata; never discover or inspect other modules.
        Loaded traces deliberately ignore simulation settings.
        """
        self._sampling_information = deepcopy(sampling_information)
        self._measurement_settings = deepcopy(measurement_settings)
        self.set_measurement_point_count(measurement_settings['number_of_lasers'])

    def get_status(self):
        """ Receives the current status of the Fast Counter and outputs it as
            return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        return self.statusvar

    def start_measure(self):
        if self.statusvar != 1:
            return -1
        try:
            if self.trace_path is None:
                self._count_data = self._simulate_trace()
            else:
                self._count_data = self._load_trace()
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            # Remain configurable after a failed simulation attempt.
            self.statusvar = 1
            self.log.exception('Unable to prepare fast-counter dummy data.')
            return -1
        self.statusvar = 2
        return 0

    def _load_trace(self):
        """Load nonnegative counts sampled at 950 MHz; rows are gates.

        Only complete base-clock bins belonging to the configured record are
        summed. Short traces or mismatched gate counts are errors, not repeated.
        """
        data = np.loadtxt(os.path.expanduser(self.trace_path), ndmin=2)
        if not np.all(np.isfinite(data)) or np.any(data < 0) or np.any(data != np.floor(data)):
            raise ValueError('Loaded trace must contain nonnegative integer counts.')
        if not self._gated:
            if min(data.shape) != 1:
                raise ValueError('Ungated load_trace must contain a single row or column.')
            data = data.reshape(1, -1)
        elif data.shape[0] != self._number_of_gates:
            raise ValueError('Loaded trace must have one row per configured gate.')
        required = self._gate_length_bins * self._binwidth
        if data.shape[1] < required:
            raise ValueError('Loaded trace is shorter than the configured record.')
        # Check before casting/summing to avoid signed integer overflow.
        if np.any(data >= np.iinfo(np.int64).max / self._binwidth):
            raise ValueError('Loaded counts exceed the int64 range after binning.')
        data = data[:, :required].astype('int64')
        data = data.reshape(data.shape[0], self._gate_length_bins, self._binwidth).sum(axis=2)
        return data if self._gated else data[0]

    def _simulate_trace(self):
        self._discover_measurement_settings()
        sampling = self._sampling_information
        generation = sampling.get('generation_parameters', {})
        delay = float(generation.get('laser_delay', self._laser_delay))
        count = self._number_of_gates if self._gated else self._ungated_number_of_pulses
        width = self.get_binwidth()
        record_length = self._gate_length_bins * width
        if not np.isfinite(delay) or delay < 0:
            raise ValueError('Laser delay must be finite and nonnegative.')
        signal = self._simulation_signal(count)
        simulation_bins = self._gate_length_bins
        if self._gated:
            # A generated gate covers the laser first and the delay afterwards.
            # Pass-through therefore exposes the laser at t=0 and leaves the
            # configured delay as a dark tail at the end of each gate.
            # Leave a short dark margin before the laser.  The unchanged gated
            # convolution extractor needs a real rising edge inside the record;
            # placing the pulse directly on bin zero makes that edge invisible.
            edge_margin = min(delay / 5, max(0.0, record_length / 10))
            starts = np.full(count, edge_margin)
            # Gate count is configured by the counter. An older sampled asset
            # may contain a different number of pulses; it must not prevent a
            # gated demo with a new number of measurement points.
            default_length = min(float(self._laser_length), record_length - edge_margin)
            lengths = np.full(count, float(generation.get('laser_length', default_length)))
            rising = np.asarray(sampling.get('laser_rising_bins', []), dtype=float)
            falling = np.asarray(sampling.get('laser_falling_bins', []), dtype=float)
            if sampling and rising.shape == falling.shape == (count,):
                rate = float(sampling['pulse_generator_settings']['sample_rate'])
                if not np.isfinite(rate) or rate <= 0:
                    raise ValueError('Invalid pulse-generator sample rate.')
                lengths = (falling - rising) / rate
                if generation.get('gate_channel'):
                    lengths = lengths - delay
            elif sampling:
                self.log.warning(
                    f'Sampled asset has {rising.size} rising and {falling.size} falling edges; '
                    f'simulating {count} configured gates using the laser duration.'
                )
            # In gated mode each row is one gate.  Some ungated sequence
            # metadata reports the duration of the complete multi-point
            # sequence as record_length.  Do not let that make every gated row
            # grow with the number of measurement points.
            gate_duration = float(np.max(starts + lengths)) + edge_margin
            simulation_bins = min(
                self._gate_length_bins,
                max(1, int(np.ceil(gate_duration / width)))
            )
        elif sampling:
            rate = float(sampling['pulse_generator_settings']['sample_rate'])
            if not np.isfinite(rate) or rate <= 0:
                raise ValueError('Invalid pulse-generator sample rate.')
            starts = np.asarray(sampling['laser_rising_bins'], dtype=float) / rate
            ends = np.asarray(sampling['laser_falling_bins'], dtype=float) / rate
            # If sync and laser use the same digital channel, sequence analysis
            # can report the sync edge across the waveform boundary: its falling
            # edge is at zero and its rising edge is last. Pair every rise with
            # the next chronological fall before selecting readout durations.
            expected_length = float(generation.get('laser_length', self._laser_length))
            tolerance = max(2 / rate, width)
            try:
                starts, ends = select_pulse_edges(
                    starts, ends, count, expected_length, tolerance
                )
            except ValueError:
                raise ValueError(
                    f'Ungated sequence has {len(sampling["laser_rising_bins"])} rising and '
                    f'{len(sampling["laser_falling_bins"])} falling edges, but {count} laser '
                    f'pulses with duration {expected_length:g} s could not be identified. '
                    'Regenerate, sample and load the sequence with matching laser settings.'
                )
            lengths = ends - starts
            if generation.get('gate_channel'):
                # Sampled "laser" edges describe the gate, including the delay.
                lengths = lengths - delay
            starts = starts + delay
        else:
            self.log.warning('No sampled sequence timing: using standalone dummy laser spacing. '
                             'Load a sampled sequence for timing-based extraction.')
            lengths = np.full(count, float(self._laser_length))
            starts = delay + np.arange(count) * (2 * float(self._laser_length) + delay)

        return simulate_photon_trace(
            starts, lengths, signal, simulation_bins, width,
            gated=self._gated, poisson_noise=self._poisson_noise, seed=self._random_seed
        )

    def _simulation_signal(self, count):
        """Return the oscillating signal level for each measurement point."""
        times = (np.arange(count) + 1) * 10e-9
        control = np.asarray(self._measurement_settings.get('controlled_variable', []), dtype=float)
        units = self._measurement_settings.get('units', ('', ''))
        if units[0] == 's' and control.shape == (count,):
            times = control
        if not np.isfinite(self._rabi_period) or self._rabi_period <= 0:
            raise ValueError('Demo Rabi period must be finite and positive.')
        return 500 + 100 * np.cos(2 * np.pi * times / self._rabi_period)

    def _discover_measurement_settings(self):
        """Read point count and public measurement data from the connected logic.

        FastCounterInterface.configure() does not pass the point count to an
        ungated counter.  Keep this compatibility lookup in the dummy so the
        regular measurement and extraction modules can remain unchanged.
        """
        try:
            from qudi.core.modulemanager import ModuleManager

            manager = ModuleManager.instance()
            instances = [] if manager is None else list(manager.module_instances.values())
        except Exception:
            return

        for instance in instances:
            if instance is self:
                continue
            try:
                connector = getattr(instance, '_fastcounter', None)
                if connector is None or not self._is_this_module(connector()):
                    continue
                count = int(getattr(instance, '_number_of_lasers'))
                if count < 1:
                    continue
                self._ungated_number_of_pulses = count
                source_sampling = getattr(instance, 'sampling_information', {})
                self._extraction_method = getattr(instance, 'extraction_settings', {}).get('method')
                if source_sampling:
                    # Private runtime marker used only to distinguish this
                    # synthetic raw trace from real pass-through hardware data.
                    source_sampling['_fast_counter_dummy_raw_trace'] = True
                if not self._gated and source_sampling:
                    rate = float(source_sampling['pulse_generator_settings']['sample_rate'])
                    rising_bins = np.asarray(source_sampling['laser_rising_bins'])
                    falling_bins = np.asarray(source_sampling['laser_falling_bins'])
                    if rising_bins.size != count or falling_bins.size != count:
                        generation = source_sampling.get('generation_parameters', {})
                        laser_length = float(generation.get('laser_length', self._laser_length))
                        rising, falling = select_pulse_edges(
                            rising_bins / rate, falling_bins / rate, count, laser_length,
                            max(2 / rate, self.get_binwidth())
                        )
                        # The unchanged timing-based extractor reads this public
                        # dictionary directly, so remove the shared sync edge here.
                        source_sampling['laser_rising_bins'] = np.rint(rising * rate).astype('int64')
                        source_sampling['laser_falling_bins'] = np.rint(falling * rate).astype('int64')
                        self.log.warning('Ignored one shared sync edge in ungated laser timing.')
                self._sampling_information = deepcopy(source_sampling)
                self._measurement_settings = deepcopy(
                    getattr(instance, 'measurement_settings', {})
                )
                self._measurement_settings.setdefault('number_of_lasers', count)
                return
            except (AttributeError, TypeError, ValueError):
                continue

    def _is_this_module(self, module):
        """Return whether a connector proxy refers to this dummy module."""
        if module is self:
            return True
        own_uuid = getattr(self, 'module_uuid', None)
        return own_uuid is not None and getattr(module, 'module_uuid', None) == own_uuid

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        if self.statusvar != 2:
            return -1
        time.sleep(1)
        self.statusvar = 3
        return 0

    def stop_measure(self):
        """ Stop the fast counter. """

        time.sleep(1)
        self.statusvar = 1
        return 0

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """

        if self.statusvar != 3:
            return -1
        self.statusvar = 2
        return 0

    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """

        return self._gated

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        width_in_seconds = self._binwidth * 1/950e6
        return width_in_seconds

    def get_data_trace(self):
        """ Polls the current timetrace data from the fast counter.

        Return value is a numpy array (dtype = int64).
        The binning, specified by calling configure() in forehand, must be
        taken care of in this hardware class. A possible overflow of the
        histogram bins must be caught here and taken care of.
        If the counter is NOT GATED it will return a tuple (1D-numpy-array, info_dict) with
            returnarray[timebin_index]
        If the counter is GATED it will return a tuple (2D-numpy-array, info_dict) with
            returnarray[gate_index, timebin_index]

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds

        If the hardware does not support these features, the values should be None
        """

        # include an artificial waiting time
        time.sleep(0.5)
        info_dict = {'elapsed_sweeps': None, 'elapsed_time': None}
        return self._count_data, info_dict

    def get_frequency(self):
        freq = 950.
        time.sleep(0.5)
        return freq
