# -*- coding: utf-8 -*-

"""
This file contains an interfuse that combines multiple independent NI
counter hardware modules (each an NIXSeriesCounter instance) into a
single logical counter -- a full drop-in replacement for one
NIXSeriesCounter, usable anywhere a single such module would be
connected as 'fastcounter', 'streamer', or a scanning counter, INCLUDING
connectors that check for the literal class name 'NIXSeriesCounter'
(see "WHY THIS SUBCLASSES NIXSeriesCounter" below).

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

------------------------------------------------------------------------

WHY THIS SUBCLASSES NIXSeriesCounter

Some other module in this setup (e.g. PIE710CounterInterfuse) declares its
own connector for a photon counter using qudi's string-based Connector
check:

    photon_counter = Connector(name='photon_counter', interface='NIXSeriesCounter')

When 'interface' is given as a plain string rather than an actual ABC
class, qudi's Connector.connect() checks whether that string names any
class in the connected instance's MRO -- it is NOT an isinstance() check
against a real interface class. Because of this, ANY object connected to
that connector must have NIXSeriesCounter literally somewhere in its class
hierarchy, regardless of whether it duck-types all the right methods.

This module is therefore written as `class NICounterStackInterfuse(NIXSeriesCounter)`
rather than composing NIXSeriesCounter instances behind a separate base
class. NIXSeriesCounter's own __init__ is still called (it only sets
placeholder instance attributes to None/defaults -- it performs no actual
hardware I/O until on_activate() runs), but this subclass's own
on_activate()/on_deactivate() and every public method are completely
overridden below; none of NIXSeriesCounter's own hardware-specific logic
ever executes on an instance of this class.

Because NIXSeriesCounter declares ~18 of its own ConfigOptions (device_name,
photon_pfi, scan_counter_channel, etc.), all of which would otherwise be
resolved against THIS module's own config section (since ConfigOption
descriptors are inherited) and could log spurious "missing" warnings for
options we deliberately never set here, every single one of them is
redeclared below with missing='nothing' -- silencing that noise
explicitly, rather than leaving it as an unexplained wall of irrelevant
warnings on every activation.

------------------------------------------------------------------------

OVERVIEW

This interfuse implements all three roles NIXSeriesCounter itself
provides:

  1. FastCounterInterface  -- used by PulsedMeasurementLogic's
     'fastcounter' connector. Every call is fanned out to every connected
     sub-counter; histogram data is combined by elementwise-summing their
     individual accumulator arrays.

  2. DataInStreamInterface -- used wherever a 'streamer' connector expects
     one. Every sub-counter's own channels (its two built-in rate
     channels, PLUS every configured digital_sources/analog_sources
     channel) are exposed with a unique prefix (that counter's own qudi
     module name). Three derived sum channels are always present:
     'sum_rate_all_hz', 'sum_rate_gated_hz', and 'sum_digital_hz' -- see
     "SUM CHANNELS" below.

  3. Scanning-counter protocol -- used by PIE710CounterInterfuse's
     counter connector. Fans out to whichever trio the caller uses (see
     "SCANNING TRIGGER MODES" below), summing every connected counter's
     raw per-pixel counts into ONE reported channel. All three trios are
     implemented here, unconditionally, mirroring NIXSeriesCounter's own
     design -- this module has no notion of "current mode" either; the
     caller decides which trio to call.

------------------------------------------------------------------------

SCANNING TRIGGER MODES

Mirrors NIXSeriesCounter's own three mutually-exclusive scan trios (see
that module's docstring for the full acquisition details of each) -- this
interfuse simply fans each one out to every connected sub-counter and
combines the result:

  'clock'             : arm() / read() / stop()
                        Summed per-pixel across all sub-counters.

  'position_distance' : arm_position_trigger() / read_position_trigger()
                        / stop_position_trigger()
                        Summed per-pixel across all sub-counters.
                        read_position_trigger() runs every sub-counter's
                        read in its own thread (each one polls/blocks
                        independently), so combined latency is that of
                        the SLOWEST sub-counter, not their sum.

  'point_by_point'    : arm_point_scan() / count_point(duration_s) /
                        disarm_point_scan()
                        count_point() MUST run every sub-counter's own
                        count_point() call CONCURRENTLY (via threads) --
                        each sub-counter's count_point() internally does
                        its own start -> sleep(duration_s) -> read ->
                        stop, so calling them one after another would
                        have each card count over a DIFFERENT real time
                        window instead of the same one, silently
                        producing an uncorrelated sum. Threaded exactly
                        like read()/read_position_trigger() above.

The caller (e.g. PIE710CounterInterfuse) picks which trio to call --
this module does not read or care about counter_trigger_mode itself; all
three trios are always available, on every connected sub-counter.

------------------------------------------------------------------------

DEFAULT ACTIVE CHANNELS

Matching NIXSeriesCounter's own default behavior exactly
(self._active_channels = list(self._all_channels) at activation), this
interfuse's default active-channel set is EVERY channel of EVERY
connected counter (all built-in rate channels, all configured digital
sources, all configured analog sources), plus all three derived sum
channels.

------------------------------------------------------------------------

SUM CHANNELS

Three derived channels are always present, each an elementwise sum across
every connected counter:

  sum_rate_all_hz   : sum of every connected counter's own rate_all_hz
  sum_rate_gated_hz : sum of every connected counter's own rate_gated_hz
  sum_digital_hz    : sum of every connected counter's own configured
                      digital_sources channel(s) -- e.g. if counter A has
                      digital_sources: ['PFI8'] and counter B has
                      digital_sources: ['PFI7'], this sums those two
                      channels together, even though their underlying
                      physical terminal names differ, since what matters
                      is the ROLE (a live digital photon-rate channel per
                      card), not the specific PFI terminal number used on
                      each card.

Which channels on a given counter count as "digital source" channels for
the sum_digital_hz calculation is determined via that counter's own
public constraints.channel_units property: any channel reporting unit
'counts/s' that is NOT one of the two built-in rate-channel names
(rate_all_hz, rate_gated_hz) is treated as a digital source channel. This
avoids reaching into any sub-counter's private attributes.

Analog (voltage) channels are deliberately NOT summed across counters.

------------------------------------------------------------------------

CONNECTOR COUNT

Qudi Connectors are declared statically in code (resolved at class
definition time, before config is read), so there is no way to create
"however many the config specifies" via a runtime loop. This module
declares a fixed set of optional connectors (counter1 through counter8)
and simply ignores whichever ones are left unconnected in your YAML
config. If more than 8 are ever needed, this number needs to be increased
here, in code (one line per extra slot).

------------------------------------------------------------------------

Example config for copy-paste:

    ni_stacked_counter:
        module.Class: 'interfuse.ni_counter_stack_interfuse.NICounterStackInterfuse'
        connect:
            counter1: 'ni_combined_1'
            counter2: 'ni_combined_2'
        options:
            sum_channel_name: 'Sum'
            sum_digital_channel_name: 'sum_digital_hz'
"""

import threading
import numpy as np

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.interface.data_instream_interface import (
    DataInStreamConstraints,
    SampleTiming,
    StreamingMode,
)

# Adjust this import to match wherever your actual NIXSeriesCounter class
# lives -- your config uses module.Class: 'ni_x_series.ni_test_counter.NIXSeriesCounter',
# so the corresponding import path is:
from qudi.hardware.ni_x_series.ni_test_counter import NIXSeriesCounter


# Fixed, generous connector count -- see module docstring, "CONNECTOR COUNT".
_MAX_STACKED_COUNTERS = 8

_ACCEPTED_RATE_UNITS = {'counts/s', 'c/s'}

# Must match NIXSeriesCounter's own _CH_ALL / _CH_GATED constants exactly.
_RATE_ALL_SUFFIX   = 'rate_all_hz'
_RATE_GATED_SUFFIX = 'rate_gated_hz'

_SUM_RATE_ALL_CHANNEL   = 'sum_rate_all_hz'
_SUM_RATE_GATED_CHANNEL = 'sum_rate_gated_hz'


class NICounterStackInterfuse(NIXSeriesCounter):
    """ Combines N independent NIXSeriesCounter hardware modules into a
    single logical counter. Subclasses NIXSeriesCounter itself so that
    string-based Connector interface checks against the literal class
    name 'NIXSeriesCounter' succeed -- see module docstring, "WHY THIS
    SUBCLASSES NIXSeriesCounter".

    None of NIXSeriesCounter's own hardware logic is ever executed on an
    instance of this class -- every method below is a full override.

    Implements all three scanning trigger-mode trios (see module
    docstring, "SCANNING TRIGGER MODES") by fanning each one out to
    every connected sub-counter and summing the per-pixel result.
    """

    counter1 = Connector(interface=NIXSeriesCounter, optional=True)
    counter2 = Connector(interface=NIXSeriesCounter, optional=True)
    counter3 = Connector(interface=NIXSeriesCounter, optional=True)
    counter4 = Connector(interface=NIXSeriesCounter, optional=True)
    counter5 = Connector(interface=NIXSeriesCounter, optional=True)
    counter6 = Connector(interface=NIXSeriesCounter, optional=True)
    counter7 = Connector(interface=NIXSeriesCounter, optional=True)
    counter8 = Connector(interface=NIXSeriesCounter, optional=True)

    _sum_channel_name         = ConfigOption('sum_channel_name',         default='Sum')
    _sum_digital_channel_name = ConfigOption('sum_digital_channel_name', default='sum_digital_hz')

    # ── Silence every inherited ConfigOption from NIXSeriesCounter ─────────────
    # These are deliberately unused by this subclass (see module docstring).
    # Redeclared here purely to override their 'missing' policy so none of
    # them log a spurious warning/info message on activation, since we
    # never set any of them in this module's own config section.
    _device_name          = ConfigOption('device_name',          'Dev2',  missing='nothing')
    _photon_pfi_line       = ConfigOption('photon_pfi',           'PFI0', missing='nothing')
    _gate_pfi_line         = ConfigOption('gate_pfi',             'PFI1', missing='nothing')
    _diag_enabled          = ConfigOption('diag_enabled',         False,  missing='nothing')
    _diag_interval_s       = ConfigOption('diag_interval_s',      2.0,    missing='nothing')
    _cfg_sample_rate       = ConfigOption('sample_rate',          10.0,   missing='nothing')
    _cfg_channel_buf_size  = ConfigOption('channel_buffer_size',  100,    missing='nothing')
    _cfg_digital_sources   = ConfigOption('digital_sources',      [],     missing='nothing')
    _cfg_analog_sources    = ConfigOption('analog_sources',       [],     missing='nothing')
    _cfg_adc_range         = ConfigOption('adc_voltage_range',    [-10, 10], missing='nothing')
    _cfg_max_hw_buf        = ConfigOption('max_channel_samples_buffer', 1024**2, missing='nothing')
    _cfg_rw_timeout        = ConfigOption('read_write_timeout',   10,     missing='nothing')
    _scan_counter_ch       = ConfigOption('scan_counter_channel', 'ctr0', missing='nothing')
    _scan_clock_ctr        = ConfigOption('scan_clock_counter',   'ctr1', missing='nothing')
    _scan_trigger_term     = ConfigOption('scan_trigger_terminal','PFI1', missing='nothing')
    _scan_apd_term         = ConfigOption('scan_apd_terminal',    None,   missing='nothing')
    _scan_ch_name          = ConfigOption('scan_channel_name',    'APD1', missing='nothing')
    _scan_rw_timeout       = ConfigOption('scan_read_timeout',    30.0,   missing='nothing')
    _sync_max_lag_cycles    = ConfigOption('sync_max_lag_cycles', 2000,   missing='nothing')
    _pt_trigger_counter_ch  = ConfigOption('scan_trigger_counter_channel', 'ctr2', missing='nothing')
    _pt_sample_rate_hz      = ConfigOption('position_trigger_sample_rate_hz', 100000.0, missing='nothing')
    _pt_max_total_time_s    = ConfigOption('position_trigger_max_total_time_s', 30.0, missing='nothing')
    _pt_read_settle_s       = ConfigOption('position_trigger_read_settle_s', 0.1, missing='nothing')
    _pt_read_poll_timeout_s = ConfigOption('position_trigger_read_poll_timeout_s', 3.0, missing='nothing')
    _pt_match_tolerance_frac = ConfigOption('position_trigger_match_tolerance_frac', 0.4, missing='nothing')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._counters = []
        self._counter_prefixes = []

        self._fc_bin_width     = None
        self._fc_record_length = None
        self._fc_num_gates     = None

        self._instream_constraints = None
        self._sample_rate          = None
        self._channel_buffer_size  = None
        self._streaming_mode       = StreamingMode.CONTINUOUS
        self._active_channels      = []
        self._all_channels         = []
        self._per_counter_active   = {}
        # Per-counter, unprefixed names of channels treated as "digital
        # source" channels for the sum_digital_hz calculation -- see
        # module docstring, "SUM CHANNELS".
        self._digital_source_names = {}

    # ══════════════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def on_activate(self):
        """ Deliberately does NOT call super().on_activate() -- see class
        docstring. NIXSeriesCounter's own on_activate() would try to open
        real DAQmx tasks against this module's own (unset) device_name,
        which is not wanted here at all.
        """
        self._counters = []
        for i in range(1, _MAX_STACKED_COUNTERS + 1):
            connector = getattr(self, f'counter{i}')
            if connector.is_connected:
                self._counters.append(connector())

        if not self._counters:
            raise RuntimeError(
                'NICounterStackInterfuse: no counters connected. Wire up '
                'at least one of counter1..counter{0} in the connect: '
                'block of this module\'s config.'.format(_MAX_STACKED_COUNTERS)
            )

        self._counter_prefixes = [c.module_name for c in self._counters]
        if len(set(self._counter_prefixes)) != len(self._counter_prefixes):
            raise RuntimeError(
                'NICounterStackInterfuse: connected counters do not have '
                'unique module names: {0}. This should not be possible '
                'under normal qudi configuration.'.format(self._counter_prefixes)
            )

        self.log.info(
            'NICounterStackInterfuse: {0} counter(s) connected: {1}'.format(
                len(self._counters), self._counter_prefixes
            )
        )

        self._build_instream_constraints()

        # Default active channels: EVERY channel of EVERY connected
        # counter (matching NIXSeriesCounter's own "all channels active
        # by default" behavior), plus all three derived sum channels.
        self._active_channels = list(self._all_channels)
        self._per_counter_active = {}
        for counter, prefix in zip(self._counters, self._counter_prefixes):
            self._per_counter_active[prefix] = self._counter_all_channel_names(counter)

    def _counter_all_channel_names(self, counter):
        """ Every channel name a given connected counter can provide,
        via its own public constraints property (not private attributes).
        """
        return list(counter.constraints.channel_units.keys())

    def on_deactivate(self):
        """ Best-effort shutdown of every role on every connected counter.
        Never raises.
        """
        try:
            self.stop_measure()
        except Exception as exc:
            self.log.warning(f'NICounterStackInterfuse: stop_measure() warning during deactivation: {exc}')
        try:
            self.stop_stream()
        except Exception as exc:
            self.log.warning(f'NICounterStackInterfuse: stop_stream() warning during deactivation: {exc}')
        self.stop()
        self.stop_position_trigger()
        self.disarm_point_scan()

    # ══════════════════════════════════════════════════════════════════════════
    #  Generic fan-out helpers, shared by all three scanning trios
    # ══════════════════════════════════════════════════════════════════════════

    def _fan_out_arm(self, method_name, rollback_method_name, *args, **kwargs):
        """ Calls counter.<method_name>(*args, **kwargs) on every
        connected sub-counter, in order. On failure, calls
        counter.<rollback_method_name>() on every counter armed so far,
        then re-raises. Shared by arm()/arm_position_trigger()/
        arm_point_scan().
        """
        armed = []
        try:
            for counter in self._counters:
                getattr(counter, method_name)(*args, **kwargs)
                armed.append(counter)
        except Exception:
            self.log.error(
                f'NICounterStackInterfuse: {method_name}() failed on '
                f'counter index {len(armed)} of {len(self._counters)}. '
                f'Rolling back {len(armed)} already-armed counter(s).'
            )
            for counter in armed:
                try:
                    getattr(counter, rollback_method_name)()
                except Exception as exc:
                    self.log.warning(
                        f'NICounterStackInterfuse: error while rolling '
                        f'back an already-armed counter: {exc}'
                    )
            raise

    def _fan_out_stop(self, method_name):
        """ Calls counter.<method_name>() on every connected sub-counter.
        Never raises -- logs a warning per failing counter. Shared by
        stop()/stop_position_trigger()/disarm_point_scan().
        """
        for i, counter in enumerate(self._counters):
            try:
                getattr(counter, method_name)()
            except Exception as exc:
                self.log.warning(
                    f'NICounterStackInterfuse: {method_name}() warning on '
                    f'counter index {i}: {exc}'
                )

    def _fan_out_parallel(self, method_name, *args, **kwargs):
        """ Calls counter.<method_name>(*args, **kwargs) on every
        connected sub-counter CONCURRENTLY, in its own thread, and waits
        for all to finish. Required whenever the call blocks for a real
        amount of time on hardware (read/count) -- running sequentially
        would either add up latencies (read) or count over
        non-overlapping time windows (count_point), see module docstring,
        "SCANNING TRIGGER MODES".

        Returns a list of per-counter results (None on that counter's
        failure), and a parallel list of exceptions (None on success).
        """
        results = [None] * len(self._counters)
        errors  = [None] * len(self._counters)

        def _call_one(index, counter):
            try:
                results[index] = getattr(counter, method_name)(*args, **kwargs)
            except Exception as exc:
                errors[index] = exc

        threads = [
            threading.Thread(target=_call_one, args=(i, counter), daemon=True)
            for i, counter in enumerate(self._counters)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        return results, errors

    def _sum_pixel_dicts(self, results, n_pixels, method_name):
        """ Sums a list of {channel_name: np.ndarray(n_pixels,)} dicts
        (one per sub-counter) into a single {sum_channel_name: array}
        dict. Returns None (logging an error) if any result is missing.
        Shared by read()/read_position_trigger().
        """
        for i, result in enumerate(results):
            if result is None:
                self.log.error(
                    f'NICounterStackInterfuse: {method_name}() returned '
                    f'None from counter index {i}. Aborting combined read.'
                )
                return None

        total = np.zeros(n_pixels, dtype=np.float64)
        for result in results:
            for array in result.values():
                total += array

        return {self._sum_channel_name: total}

    # ══════════════════════════════════════════════════════════════════════════
    #  FastCounterInterface
    # ══════════════════════════════════════════════════════════════════════════

    def get_constraints(self):
        constraints_list = [c.get_constraints() for c in self._counters]
        first = constraints_list[0]
        if any(c != first for c in constraints_list[1:]):
            self.log.warning(
                'NICounterStackInterfuse: connected counters report '
                'differing get_constraints() results: {0}. Using the '
                'first connected counter\'s constraints.'.format(constraints_list)
            )
        return first

    def configure(self, bin_width_s=None, record_length_s=None,
                  number_of_gates=0, active_channels=None,
                  streaming_mode=None, channel_buffer_size=None,
                  sample_rate=None):
        if bin_width_s is not None and isinstance(bin_width_s, (int, float)):
            return self._fc_configure(bin_width_s, record_length_s, number_of_gates)
        if active_channels is not None:
            return self._is_configure(active_channels, streaming_mode,
                                      channel_buffer_size, sample_rate)
        raise TypeError(
            'configure() requires either (bin_width_s, record_length_s) '
            'for the fast counter role or keyword arguments '
            '(active_channels, streaming_mode, channel_buffer_size, '
            'sample_rate) for the instreamer role.')

    def _fc_configure(self, bin_width_s, record_length_s, number_of_gates=0):
        results = []
        for counter in self._counters:
            results.append(counter.configure(
                bin_width_s=bin_width_s,
                record_length_s=record_length_s,
                number_of_gates=number_of_gates,
            ))

        first = results[0]
        if any(r != first for r in results[1:]):
            raise RuntimeError(
                'NICounterStackInterfuse: connected counters returned '
                'different actual (bin_width_s, record_length_s, '
                'number_of_gates) values from configure(): {0}. Every '
                'connected counter must produce identical histogram '
                'shapes for get_data_trace() to be combined correctly.'
                ''.format(results)
            )

        self._fc_bin_width, self._fc_record_length, self._fc_num_gates = first
        return first

    def get_status(self):
        statuses = [c.get_status() for c in self._counters]
        first = statuses[0]
        if any(s != first for s in statuses[1:]) or first == self.STATUS_ERROR:
            if any(s != first for s in statuses[1:]):
                self.log.error(
                    'NICounterStackInterfuse: connected counters report '
                    'disagreeing statuses: {0} (prefixes: {1}). Reporting '
                    'combined status as ERROR.'.format(statuses, self._counter_prefixes)
                )
            return self.STATUS_ERROR
        return first

    def start_measure(self):
        self._fan_out_arm('start_measure', 'stop_measure')

    def stop_measure(self):
        """ Stops every connected counter's fast-counter role, logging a
        one-line summary comparing each counter's final elapsed_sweeps
        BEFORE stopping any of them (stopping a counter resets its own
        internal state, so the comparison must happen first, or every
        value would already read back as zero).
        """
        final_sweeps = {}
        for prefix, counter in zip(self._counter_prefixes, self._counters):
            try:
                _, info = counter.get_data_trace()
                final_sweeps[prefix] = info['elapsed_sweeps']
            except Exception as exc:
                final_sweeps[prefix] = f'<error: {exc}>'

        self.log.info(
            'NICounterStackInterfuse: measurement stopped. Final '
            'elapsed_sweeps per counter: {0}.'.format(final_sweeps)
        )

        self._fan_out_stop('stop_measure')

    def pause_measure(self):
        self._fan_out_stop('pause_measure')

    def continue_measure(self):
        self._fan_out_arm('continue_measure', 'pause_measure')

    def is_gated(self):
        values = [c.is_gated() for c in self._counters]
        if len(set(values)) != 1:
            raise RuntimeError(
                'NICounterStackInterfuse: connected counters disagree on '
                'is_gated(): {0} (prefixes: {1}).'.format(
                    values, self._counter_prefixes
                )
            )
        return values[0]

    def get_binwidth(self):
        values = [c.get_binwidth() for c in self._counters]
        first = values[0]
        if any(v != first for v in values[1:]):
            self.log.warning(
                'NICounterStackInterfuse: connected counters report '
                'different get_binwidth() values: {0}. Using the first '
                'connected counter\'s value.'.format(values)
            )
        return first

    def get_data_trace(self):
        """ Combines every connected counter's histogram into one, aligned
        to the SLOWEST connected counter's actual completed-cycle count.

        Any counter that is currently ahead has its excess, not-yet-matched
        cycles held back (via get_data_trace_up_to()) rather than summed
        in immediately -- see module docstring, "CROSS-COUNTER CYCLE
        SYNCHRONIZATION" in NIXSeriesCounter. This makes the combined
        result always internally consistent: every contribution
        corresponds to the same (or, due to checkpoint rounding, a
        slightly smaller) number of completed cycles, never a mix of
        differing per-counter cycle counts summed together as if matched.
        """
        # First pass: full, untruncated reads, to discover each counter's
        # true current elapsed_sweeps. For any counter already at (or
        # below) the group minimum, this result is used as-is -- no
        # second call needed.
        traces      = [c.get_data_trace() for c in self._counters]
        sweeps_list = [info['elapsed_sweeps'] for _, info in traces]
        min_sweeps  = min(sweeps_list)

        arrays       = []
        served_list  = []
        for (arr, info), counter in zip(traces, self._counters):
            if info['elapsed_sweeps'] <= min_sweeps:
                arrays.append(arr)
                served_list.append(info['elapsed_sweeps'])
            else:
                # This counter is ahead of the group -- request a view
                # truncated to the slowest counter's actual progress,
                # holding its extra cycles back rather than including
                # them now.
                truncated_arr, truncated_info = counter.get_data_trace_up_to(min_sweeps)
                arrays.append(truncated_arr)
                served_list.append(truncated_info['elapsed_sweeps'])

        first_shape = arrays[0].shape
        for i, arr in enumerate(arrays[1:], start=1):
            if arr.shape != first_shape:
                raise RuntimeError(
                    'NICounterStackInterfuse: counter index {0} returned '
                    'a histogram of shape {1}, expected {2} to match '
                    'counter index 0. This should not be possible if '
                    'configure() succeeded earlier -- check for a '
                    'reconfiguration on one counter only.'.format(
                        i, arr.shape, first_shape
                    )
                )

        combined = arrays[0].copy()
        for arr in arrays[1:]:
            combined += arr

        time_list = [info['elapsed_time'] for _, info in traces]

        # served_list should already all equal min_sweeps exactly, except
        # for any counter whose get_data_trace_up_to() had to round down
        # to a checkpoint slightly below min_sweeps (see that method's
        # docstring) -- taking the min here guarantees the reported
        # combined cycle count never overstates what was actually summed.
        combined_sweeps = min(served_list)

        spread = max(sweeps_list) - min(sweeps_list)
        if spread > 0:
            # Routine, expected behavior of the sync mechanism -- logged
            # at debug level, not warning, since holding back a small,
            # bounded number of cycles from independently-clocked
            # counters is exactly what this is designed to do, not a
            # fault condition.
            self.log.debug(
                'NICounterStackInterfuse: holding back up to {0} cycle(s) '
                'from the lead counter(s) pending the slowest counter '
                'catching up (raw elapsed_sweeps: {1}, prefixes: {2}).'
                ''.format(spread, sweeps_list, self._counter_prefixes)
            )

        return combined, {
            'elapsed_sweeps': combined_sweeps,
            'elapsed_time':   max(time_list),
        }

    def get_count_rates(self):
        total_all, total_gated = 0.0, 0.0
        for counter in self._counters:
            rate_all, rate_gated = counter.get_count_rates()
            total_all   += rate_all
            total_gated += rate_gated
        return total_all, total_gated

    def get_hardware_status(self):
        combined = {}
        any_stall = False
        for counter, prefix in zip(self._counters, self._counter_prefixes):
            status = counter.get_hardware_status()
            combined[prefix] = status
            any_stall = any_stall or bool(status.get('gate_stall_warning', False))
        combined['gate_stall_warning'] = any_stall
        return combined

    def print_summary(self):
        for counter, prefix in zip(self._counters, self._counter_prefixes):
            print(f'\n=== {prefix} ===')
            counter.print_summary()

        data, info = self.get_data_trace()
        cycles_done = info['elapsed_sweeps']
        if cycles_done == 0:
            print('\n=== Combined ===\nNo complete cycles acquired yet.')
            return
        total_photons = int(data.sum())
        print(f'\n=== Combined ({len(self._counters)} counters) ===')
        print(f'  Cycles completed      : {cycles_done}')
        print(f'  Total gated photons   : {total_photons:,}')
        print(f'  Mean photons/cycle    : {total_photons / cycles_done:.1f}')

    # ══════════════════════════════════════════════════════════════════════════
    #  DataInStreamInterface -- properties
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def constraints(self):
        return self._instream_constraints

    @property
    def available_samples(self):
        return min(c.available_samples for c in self._counters)

    @property
    def sample_rate(self):
        return self._sample_rate

    @property
    def channel_buffer_size(self):
        return self._channel_buffer_size

    @property
    def streaming_mode(self):
        return self._streaming_mode

    @property
    def active_channels(self):
        return list(self._active_channels)

    # ══════════════════════════════════════════════════════════════════════════
    #  DataInStreamInterface -- constraints construction
    # ══════════════════════════════════════════════════════════════════════════

    def _build_instream_constraints(self):
        channel_units = {}
        self._digital_source_names = {}

        for counter, prefix in zip(self._counters, self._counter_prefixes):
            counter_units = counter.constraints.channel_units
            digital_names_this_counter = []
            for name, unit in counter_units.items():
                channel_units[f'{prefix}_{name}'] = unit
                if unit.lower() in _ACCEPTED_RATE_UNITS and name not in (
                        _RATE_ALL_SUFFIX, _RATE_GATED_SUFFIX):
                    digital_names_this_counter.append(name)
            self._digital_source_names[prefix] = digital_names_this_counter

        rate_units = set()
        for counter in self._counters:
            for suffix in (_RATE_ALL_SUFFIX, _RATE_GATED_SUFFIX):
                unit = counter.constraints.channel_units.get(suffix)
                if unit is not None:
                    rate_units.add(unit.lower())
        if len(rate_units) > 1 or not rate_units.issubset(_ACCEPTED_RATE_UNITS):
            raise RuntimeError(
                'NICounterStackInterfuse: connected counters report '
                'inconsistent or non-count-rate units for their built-in '
                'rate channels: {0}.'.format(rate_units)
            )
        sum_unit = next(iter(rate_units)) if rate_units else 'counts/s'
        channel_units[_SUM_RATE_ALL_CHANNEL]         = sum_unit
        channel_units[_SUM_RATE_GATED_CHANNEL]       = sum_unit
        channel_units[self._sum_digital_channel_name] = sum_unit

        self._all_channels = list(channel_units.keys())

        sr_mins = [c.constraints.sample_rate.minimum for c in self._counters]
        sr_maxs = [c.constraints.sample_rate.maximum for c in self._counters]
        sr_min, sr_max = max(sr_mins), min(sr_maxs)
        if sr_min > sr_max:
            raise RuntimeError(
                'NICounterStackInterfuse: connected counters have '
                'non-overlapping sample_rate ranges: mins={0}, maxs={1}.'
                ''.format(sr_mins, sr_maxs)
            )
        sr_default = float(np.clip(
            self._counters[0].constraints.sample_rate.default, sr_min, sr_max))

        cb_mins = [c.constraints.channel_buffer_size.minimum for c in self._counters]
        cb_maxs = [c.constraints.channel_buffer_size.maximum for c in self._counters]
        cb_min, cb_max = max(cb_mins), min(cb_maxs)
        if cb_min > cb_max:
            raise RuntimeError(
                'NICounterStackInterfuse: connected counters have '
                'non-overlapping channel_buffer_size ranges: mins={0}, '
                'maxs={1}.'.format(cb_mins, cb_maxs)
            )
        cb_default = int(np.clip(
            self._counters[0].constraints.channel_buffer_size.default, cb_min, cb_max))

        self._instream_constraints = DataInStreamConstraints(
            channel_units=channel_units,
            sample_timing=SampleTiming.CONSTANT,
            streaming_modes=[StreamingMode.CONTINUOUS],
            data_type=np.float64,
            channel_buffer_size=ScalarConstraint(
                default=cb_default, bounds=(cb_min, cb_max),
                increment=1, enforce_int=True,
            ),
            sample_rate=ScalarConstraint(
                default=sr_default, bounds=(sr_min, sr_max),
                increment=0.1, enforce_int=False,
            ),
        )
        self._sample_rate         = sr_default
        self._channel_buffer_size = cb_default

    # ══════════════════════════════════════════════════════════════════════════
    #  DataInStreamInterface -- configure / start / stop / read
    # ══════════════════════════════════════════════════════════════════════════

    def _is_configure(self, active_channels, streaming_mode,
                      channel_buffer_size, sample_rate):
        streaming_mode = StreamingMode(streaming_mode)
        if streaming_mode not in self._instream_constraints.streaming_modes:
            raise ValueError(
                f'Invalid streaming mode "{streaming_mode}". '
                'Only CONTINUOUS is supported.')

        derived = {_SUM_RATE_ALL_CHANNEL, _SUM_RATE_GATED_CHANNEL,
                  self._sum_digital_channel_name}
        invalid = set(active_channels) - set(self._all_channels) - derived
        if invalid:
            raise ValueError(
                f'Invalid channels {invalid}. '
                f'Valid channels are {set(self._all_channels) | derived}.')

        self._instream_constraints.sample_rate.check(sample_rate)
        self._instream_constraints.channel_buffer_size.check(channel_buffer_size)

        self._per_counter_active = {}
        for counter, prefix in zip(self._counters, self._counter_prefixes):
            requested_for_this_counter = [
                ch[len(prefix) + 1:] for ch in active_channels
                if ch.startswith(f'{prefix}_')
            ]
            # If nothing was explicitly requested for this specific
            # counter, default to ALL of its channels (matching this
            # interfuse's own overall default), not just its two rate
            # channels -- fixes the earlier digital-channel-dropping bug.
            channels_to_request = (requested_for_this_counter
                                   or self._counter_all_channel_names(counter))
            counter.configure(
                active_channels=channels_to_request,
                streaming_mode=streaming_mode,
                channel_buffer_size=channel_buffer_size,
                sample_rate=sample_rate,
            )
            self._per_counter_active[prefix] = list(counter.active_channels)

        self._active_channels     = list(active_channels)
        self._streaming_mode      = streaming_mode
        self._sample_rate         = float(sample_rate)
        self._channel_buffer_size = int(channel_buffer_size)

    def start_stream(self):
        self._fan_out_arm('start_stream', 'stop_stream')

    def stop_stream(self):
        self._fan_out_stop('stop_stream')

    def read_data_into_buffer(self, data_buffer, samples_per_channel,
                              timestamp_buffer=None):
        n_ch = len(self._active_channels)
        per_counter_rows = []
        for counter, prefix in zip(self._counters, self._counter_prefixes):
            buf, _ = counter.read_data(samples_per_channel)
            active = self._per_counter_active[prefix]
            per_counter_rows.append(
                (prefix, active, buf.reshape(samples_per_channel, len(active)))
            )

        flat = data_buffer.ravel()
        for row_idx in range(samples_per_channel):
            values = {}
            for prefix, active, arr in per_counter_rows:
                for ch_idx, ch_name in enumerate(active):
                    values[f'{prefix}_{ch_name}'] = arr[row_idx, ch_idx]

            sum_all = sum(
                values.get(f'{prefix}_{_RATE_ALL_SUFFIX}', 0.0)
                for prefix in self._counter_prefixes
            )
            sum_gated = sum(
                values.get(f'{prefix}_{_RATE_GATED_SUFFIX}', 0.0)
                for prefix in self._counter_prefixes
            )
            sum_digital = sum(
                values.get(f'{prefix}_{name}', 0.0)
                for prefix in self._counter_prefixes
                for name in self._digital_source_names.get(prefix, [])
            )
            values[_SUM_RATE_ALL_CHANNEL]           = sum_all
            values[_SUM_RATE_GATED_CHANNEL]         = sum_gated
            values[self._sum_digital_channel_name]  = sum_digital

            for ch_idx, ch_name in enumerate(self._active_channels):
                flat[row_idx * n_ch + ch_idx] = values.get(ch_name, 0.0)

    def read_available_data_into_buffer(self, data_buffer,
                                        timestamp_buffer=None):
        n_ch    = len(self._active_channels)
        to_read = min(self.available_samples, data_buffer.size // n_ch)
        if to_read == 0:
            return 0
        self.read_data_into_buffer(data_buffer, to_read, timestamp_buffer)
        return to_read

    def read_data(self, samples_per_channel=None):
        if samples_per_channel is None:
            samples_per_channel = self.available_samples
        n_ch = len(self._active_channels)
        buf  = np.empty(samples_per_channel * n_ch, dtype=np.float64)
        self.read_data_into_buffer(buf, samples_per_channel)
        return buf, None

    def read_single_point(self):
        n_ch = len(self._active_channels)
        buf  = np.empty(n_ch, dtype=np.float64)
        self.read_data_into_buffer(buf, 1)
        return buf, None

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning-counter protocol -- 'clock' trigger mode
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def channel_names(self):
        return [self._sum_channel_name]

    @property
    def channel_units(self):
        return {self._sum_channel_name: 'counts/s'}

    def arm(self, n_pixels, t_pixel):
        self._fan_out_arm('arm', 'stop', n_pixels, t_pixel)

    def read(self, n_pixels):
        results, errors = self._fan_out_parallel('read', n_pixels)
        for i, exc in enumerate(errors):
            if exc is not None:
                self.log.error(
                    f'NICounterStackInterfuse: read() failed on counter '
                    f'index {i}: {exc}'
                )
                return None
        return self._sum_pixel_dicts(results, n_pixels, 'read')

    def stop(self):
        self._fan_out_stop('stop')

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning-counter protocol -- 'position_distance' trigger mode
    # ══════════════════════════════════════════════════════════════════════════

    def arm_position_trigger(self, n_pixels, t_pixel):
        """ Fans out to every connected sub-counter's own
        arm_position_trigger(). See NIXSeriesCounter's module docstring
        for the acquisition model this drives on each card.
        """
        self._fan_out_arm(
            'arm_position_trigger', 'stop_position_trigger',
            n_pixels=n_pixels, t_pixel=t_pixel,
        )

    def read_position_trigger(self, n_pixels):
        """ Runs every sub-counter's own read_position_trigger()
        CONCURRENTLY (each one polls/blocks independently until its own
        edge-matching completes), then sums the per-pixel result. See
        module docstring, "SCANNING TRIGGER MODES".
        """
        results, errors = self._fan_out_parallel(
            'read_position_trigger', n_pixels)
        for i, exc in enumerate(errors):
            if exc is not None:
                self.log.error(
                    f'NICounterStackInterfuse: read_position_trigger() '
                    f'failed on counter index {i}: {exc}'
                )
                return None
        return self._sum_pixel_dicts(results, n_pixels, 'read_position_trigger')

    def stop_position_trigger(self):
        self._fan_out_stop('stop_position_trigger')

    # ══════════════════════════════════════════════════════════════════════════
    #  Scanning-counter protocol -- 'point_by_point' trigger mode
    # ══════════════════════════════════════════════════════════════════════════

    def arm_point_scan(self):
        """ Fans out to every connected sub-counter's own
        arm_point_scan(). Call ONCE before a sequence of count_point()
        calls, exactly like the single-counter case.
        """
        self._fan_out_arm('arm_point_scan', 'disarm_point_scan')

    def count_point(self, duration_s):
        """ Runs every sub-counter's own count_point(duration_s)
        CONCURRENTLY, so all cards count over the SAME real time window,
        then sums the resulting per-pixel counts. Running these
        sequentially instead would have each card count over a
        DIFFERENT window -- see module docstring, "SCANNING TRIGGER
        MODES" -- so this must stay threaded.

        @param duration_s : real time to count for, in seconds
        @return           : summed real edge count across all counters
        """
        results, errors = self._fan_out_parallel('count_point', duration_s)
        for i, exc in enumerate(errors):
            if exc is not None:
                self.log.error(
                    f'NICounterStackInterfuse: count_point() failed on '
                    f'counter index {i}: {exc}. Treating as 0 counts.'
                )
        return float(sum(r for r in results if r is not None))

    def disarm_point_scan(self):
        self._fan_out_stop('disarm_point_scan')