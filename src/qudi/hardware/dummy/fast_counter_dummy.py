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
from scipy import ndimage

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface


class FastCounterDummy(FastCounterInterface):
    """ Implementation of the FastCounter interface methods for a dummy usage.

    Example config for copy-paste:

    fastcounter_dummy:
        module.Class: 'fast_counter_dummy.FastCounterDummy'
        options:
            gated: False
            #load_trace: None # path to the saved dummy trace

    """

    # config option
    _gated = ConfigOption('gated', False, missing='warn')
    trace_path = ConfigOption('load_trace', None)
    # Optional manual override for the number of laser pulses to simulate in ungated
    # mode. Normally this is discovered automatically from the running measurement
    # (see `_number_of_points`), so it only needs to be set when running the dummy
    # without a PulsedMeasurementLogic.
    _ungated_points = ConfigOption('ungated_points', None)

    # The demo trace file was recorded for a Rabi measurement with exactly this many
    # laser pulses/points. Its individual laser pulses are located by flank detection
    # and re-emitted at a regular period, so that any requested number of points can
    # be built by cycling through them.
    _reference_points = 50

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.trace_path is None:
            self.trace_path = os.path.abspath(os.path.join(
                os.path.dirname(__file__),
                'FastComTec_demo_timetrace.asc'
            ))
            self.log.debug(f"Loading dummy fastcounter trace: {self.trace_path}")

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self.statusvar = 0
        self._binwidth = 1
        self._gate_length_bins = 8192
        self._number_of_gates = 0
        self._points_origin = 'gate count'
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
        self._binwidth = int(np.rint(bin_width_s * 1e9 * 950 / 1000))
        self._gate_length_bins = int(np.rint(record_length_s / bin_width_s))
        self._number_of_gates = int(number_of_gates)
        actual_binwidth = self._binwidth * 1000 / 950e9
        actual_length = self._gate_length_bins * actual_binwidth
        self.statusvar = 1
        return actual_binwidth, actual_length, number_of_gates

    def set_ungated_points(self, number_of_points=None):
        """ Override the number of laser pulses simulated in ungated mode.

        Can be called at runtime, e.g. from the qudi console:
            fast_counter_dummy.set_ungated_points(200)

        @param int number_of_points: number of laser pulses to simulate. Pass None
                                     or 0 to go back to determining the number
                                     automatically from the running measurement.

        @return int: the active override, or None if determined automatically

        The new value is used the next time the measurement is started.
        """
        self._ungated_points = max(1, int(number_of_points)) if number_of_points else None
        if self._ungated_points is None:
            self.log.info('Ungated point count is determined automatically again.')
        else:
            self.log.info(f'Ungated point count fixed to {self._ungated_points}.')
        return self._ungated_points

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
        time.sleep(1)
        try:
            count_data = np.loadtxt(self.trace_path, dtype='int64').ravel()
        except (OSError, ValueError):
            self.statusvar = -1
            self.log.exception(
                f'Unable to load dummy fastcounter trace: {self.trace_path}'
            )
            return -1

        rising_ind, falling_ind = self._detect_reference_edges(count_data)
        number_of_points = self._number_of_points()
        # cycle through the reference pulses (wrapping around with modulo) so that
        # requesting more points than were originally recorded continues the same
        # periodic pattern seamlessly instead of jumping or getting noisy
        point_indices = np.arange(number_of_points) % self._reference_points

        if self._gated:
            bursts = self._bursts_from_edges(count_data, rising_ind, falling_ind)
            self._count_data = self._build_gated_count_data(bursts, point_indices)
        else:
            windows = self._build_reference_windows(count_data, rising_ind)
            self._count_data = windows[point_indices].reshape(-1)

        self.log.info(
            f'Simulating {"gated" if self._gated else "ungated"} trace for '
            f'{number_of_points} laser pulses (from {self._points_origin}), '
            f'shape {self._count_data.shape}.'
        )
        self.statusvar = 2
        return 0

    def _build_reference_windows(self, count_data, rising_ind):
        """Cut one fixed-length window around every reference laser pulse.

        The recorded demo trace cannot simply be sliced into equally sized chunks:
        its laser pulses are *not* equally spaced. Because the Rabi sequence adds
        one tau step per point, the pulse-to-pulse distance grows continuously
        (roughly 1127 -> 1245 bins across the trace), so fixed-size chunks slowly
        drift out of sync with the pulses. Some chunks then contain two pulses and
        others none, and cycling such chunks produces gaps and doublets that make
        qudi's flank detection miss or double-count pulses.

        Instead every pulse is located by flank detection and re-emitted in its own
        window of constant length, keeping the real pulse shape and the real
        background around it, but at a strictly regular period. Cycling these
        windows therefore yields a clean, evenly spaced pulse train for any
        requested number of points.
        """
        # constant period/offset derived from the recording itself
        period = int(np.median(np.diff(rising_ind))) if rising_ind.size > 1 else count_data.size
        period = max(1, period)
        lead = int(min(rising_ind.min(), period // 4)) if rising_ind.size else 0

        windows = np.zeros((self._reference_points, period), dtype='int64')
        for i in range(self._reference_points):
            start = max(0, rising_ind[i] - lead)
            # a window running past the end of the recording is padded with zeros
            # rather than wrapped, so that no partial extra pulse is introduced
            segment = count_data[start:start + period]
            windows[i, :segment.size] = segment
        return windows

    def _build_gated_count_data(self, bursts, point_indices):
        """Build the gated (gate_index, timebin_index) trace.

        The gated extraction (`gated_conv_deriv`) finds a single rising/falling
        flank shared by all gates by summing them together, then reads each
        gate's signal from that same bin range. If every gate used a
        differently-shaped slice of raw data, that shared flank would not
        correspond to any single gate's actual pulse and the extracted
        amplitudes would come out scrambled instead of following the Rabi
        oscillation. So every gate reuses one common, clean laser-pulse shape
        (isolated the same way the ungated extraction isolates it - see
        `_detect_reference_edges`) placed at the start of the gate, and only
        its intensity - that point's true laser pulse amplitude - differs
        between points. This keeps gated and ungated mode showing the same
        underlying oscillation.
        """
        amplitudes = bursts.sum(axis=1).astype(float)
        prototype = bursts.mean(axis=0)
        prototype_sum = prototype.sum()
        scales = amplitudes[point_indices] / prototype_sum if prototype_sum else np.ones(point_indices.size)

        burst = np.rint(prototype).astype('int64')
        gate_length = max(self._gate_length_bins, 1)
        pulses = np.zeros((point_indices.size, gate_length), dtype='int64')
        fill_length = min(burst.size, gate_length)
        for row, scale in zip(pulses, scales):
            row[:fill_length] = np.rint(burst[:fill_length] * scale).astype('int64')
        return pulses

    def _detect_reference_edges(self, count_data):
        """Locate the rising and falling flank of every laser pulse in the trace.

        This mirrors the logic of qudi's own `ungated_conv_deriv` pulse
        extraction (rising/falling flank detection on a gaussian-smoothed
        derivative, iterated once per point) so that the pulses isolated here
        are exactly the ones the measurement itself would extract.
        """
        trace = count_data.astype(float)
        conv_std_dev = 20.0
        n = self._reference_points

        conv_deriv = np.gradient(ndimage.gaussian_filter1d(trace, conv_std_dev))
        conv_deriv_ref = np.gradient(ndimage.gaussian_filter1d(trace, 10.0))

        rising_ind = np.empty(n, dtype='int64')
        falling_ind = np.empty(n, dtype='int64')
        for i in range(n):
            rising_ind[i] = self._refine_edge_index(
                conv_deriv, conv_deriv_ref, np.argmax(conv_deriv), conv_std_dev, np.argmax
            )
            self._suppress_around(conv_deriv, rising_ind[i], conv_std_dev)

            falling_ind[i] = self._refine_edge_index(
                conv_deriv, conv_deriv_ref, np.argmin(conv_deriv), conv_std_dev, np.argmin
            )
            self._suppress_around(conv_deriv, falling_ind[i], conv_std_dev)

        rising_ind.sort()
        falling_ind.sort()
        return rising_ind, falling_ind

    def _bursts_from_edges(self, count_data, rising_ind, falling_ind):
        """Cut the bare laser pulse (without surrounding background) of every
        reference point, all padded to a common length."""
        burst_length = max(1, int(np.max(falling_ind - rising_ind)))
        bursts = np.zeros((self._reference_points, burst_length), dtype='int64')
        for i in range(self._reference_points):
            segment = count_data[rising_ind[i]:rising_ind[i] + burst_length]
            bursts[i, :segment.size] = segment
        return bursts

    @staticmethod
    def _refine_edge_index(conv_deriv, conv_deriv_ref, coarse_index, conv_std_dev, arg_extreme):
        start = max(0, int(coarse_index - conv_std_dev))
        stop = min(len(conv_deriv), int(coarse_index + conv_std_dev))
        if start == stop:
            stop = start + 1
        return start + arg_extreme(conv_deriv_ref[start:stop])

    @staticmethod
    def _suppress_around(conv_deriv, index, conv_std_dev):
        start = 0 if index < 2 * conv_std_dev else int(index - 2 * conv_std_dev)
        stop = conv_deriv.size - 1 if (conv_deriv.size - index) < 2 * conv_std_dev else int(index + 2 * conv_std_dev)
        conv_deriv[start:stop] = 0

    def _number_of_points(self):
        """Determine how many Rabi points/laser pulses to generate for the current
        configuration.

        qudi's pulse extraction expects to find *exactly* as many laser pulses in
        the trace as the measurement settings announce, so the dummy has to match
        that number. A gated counter is told it directly via
        `configure(..., number_of_gates)`. An ungated counter is not - the fast
        counter interface only passes the total record length, from which the
        point count cannot be recovered unambiguously - so it is looked up from
        the running measurement instead (see `_discover_number_of_lasers`), which
        is what makes the number of points selected in the GUI take effect here.
        """
        if self._gated:
            return max(1, self._number_of_gates)
        if self._ungated_points:
            self._points_origin = 'ungated_points override'
            return max(1, int(self._ungated_points))
        discovered = self._discover_number_of_lasers()
        if discovered:
            self._points_origin = 'running measurement'
            return discovered
        self._points_origin = 'reference trace fallback'
        return self._reference_points

    def _discover_number_of_lasers(self):
        """Look up the number of laser pulses of the measurement this dummy is
        currently serving, or None if it cannot be determined.

        Searches the active qudi modules for the logic module that has this
        instance connected as its fast counter and reads its configured number of
        laser pulses. This is deliberately best-effort: any failure simply falls
        back to the number of points of the recorded reference trace, so the dummy
        keeps working when used stand-alone (e.g. in tests) or if the logic module
        ever stops exposing that attribute.
        """
        try:
            from qudi.core.modulemanager import ModuleManager

            manager = ModuleManager.instance()
            instances = [] if manager is None else list(manager.module_instances.values())
        except Exception:
            self.log.debug('Unable to access the qudi module manager.', exc_info=True)
            return None

        for instance in instances:
            if instance is self:
                continue
            # a single unrelated module misbehaving must not abort the search
            try:
                number_of_lasers = getattr(instance, '_number_of_lasers', None)
                fastcounter = getattr(instance, '_fastcounter', None)
                if not number_of_lasers or fastcounter is None:
                    continue
                if not self._is_this_module(fastcounter()):
                    continue
            except Exception:
                continue
            return int(number_of_lasers)

        self.log.debug('No running measurement found to take the number of laser pulses '
                       'from. Falling back to the reference trace.')
        return None

    def _is_this_module(self, module):
        """Check whether `module` refers to this hardware module.

        A `Connector` hands out an `OverloadProxy` wrapping the connected module
        rather than the module itself, so an identity check would always fail
        here. Modules are therefore compared by their unique module id.
        """
        if module is self:
            return True
        own_uuid = getattr(self, 'module_uuid', None)
        return own_uuid is not None and getattr(module, 'module_uuid', None) == own_uuid

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
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
