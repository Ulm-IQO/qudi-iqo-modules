# -*- coding: utf-8 -*-

"""
Shared helpers for simulating pulsed photon-counting data.

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


def select_pulse_edges(rising_s, falling_s, expected_count, expected_length_s, tolerance_s):
    """Select chronological laser edges and discard an overlapping sync edge."""
    rising = np.asarray(rising_s, dtype=float)
    falling = np.asarray(falling_s, dtype=float)
    candidates = []
    fall_index = 0
    for start in rising:
        while fall_index < falling.size and falling[fall_index] <= start:
            fall_index += 1
        if fall_index == falling.size:
            break
        stop = falling[fall_index]
        fall_index += 1
        if abs((stop - start) - expected_length_s) <= tolerance_s:
            candidates.append((start, stop))
    if len(candidates) != expected_count:
        raise ValueError(
            f'Sampled sequence contains {len(candidates)} valid laser pulses; '
            f'{expected_count} are required.'
        )
    return np.asarray(candidates, dtype=float).T


def simulate_photon_trace(starts_s, lengths_s, signal_counts, record_bins, bin_width_s,
                          gated=False, signal_length_s=200e-9, reference_counts=600,
                          base_bin_width_s=1 / 950e6, poisson_noise=False, seed=None):
    """Integrate two-region laser pulses into counter bins.

    Counts refer to one base-clock bin. Fractional overlaps are integrated before
    rounding, so changing bin width preserves photon counts and physical timing.
    Each gated row has its own start/length; ungated pulses share one time axis.
    The early signal region is followed by a constant reference plateau.
    """
    starts = np.asarray(starts_s, dtype=float)
    lengths = np.asarray(lengths_s, dtype=float)
    signals = np.asarray(signal_counts, dtype=float)
    if (starts.ndim != 1 or starts.size == 0 or starts.shape != lengths.shape
            or starts.shape != signals.shape):
        raise ValueError('Supply one start, length and signal value per pulse.')
    if (not np.isfinite(bin_width_s) or bin_width_s <= 0
            or not np.isfinite(base_bin_width_s) or base_bin_width_s <= 0
            or int(record_bins) != record_bins or record_bins < 1
            or not np.isfinite(signal_length_s) or signal_length_s <= 0
            or not np.isfinite(reference_counts) or reference_counts < 0):
        raise ValueError('Invalid binning, record length or pulse parameters.')
    if (not np.all(np.isfinite([starts, lengths, signals])) or np.any(starts < 0)
            or np.any(lengths <= 0) or np.any(signals < 0)):
        raise ValueError('Pulse timing and counts must be finite and nonnegative.')
    ends = starts + lengths
    if np.any(ends > record_bins * bin_width_s + bin_width_s * (0.5 + 1e-9)):
        raise ValueError('Laser pulses exceed the record length; check sequence/gate settings.')
    if not gated and np.any(starts[1:] < ends[:-1]):
        raise ValueError('Ungated laser pulses must be ordered and non-overlapping.')
    shape = (starts.size, int(record_bins)) if gated else (int(record_bins),)
    counts = np.zeros(shape, dtype=float)
    for index, (start, end, signal) in enumerate(zip(starts, ends, signals)):
        first = int(np.floor(start / bin_width_s))
        last = min(int(record_bins), int(np.ceil(end / bin_width_s)))
        left = np.arange(first, last) * bin_width_s
        right = left + bin_width_s
        signal_end = min(end, start + signal_length_s)
        overlap = np.maximum(0, np.minimum(right, end) - np.maximum(left, start))
        signal_overlap = np.maximum(0, np.minimum(right, signal_end) - np.maximum(left, start))
        values = (reference_counts * overlap + (signal - reference_counts) * signal_overlap)
        target = counts[index] if gated else counts
        target[first:last] += values / base_bin_width_s
    if np.any(counts >= np.iinfo(np.int64).max):
        raise ValueError('Simulated counts exceed int64 range.')
    if poisson_noise:
        return np.random.default_rng(seed).poisson(counts).astype('int64')
    return np.rint(counts).astype('int64')


def simulate_sine_counts(number_of_points, baseline_counts=1000, contrast=0.2,
                         number_of_periods=1, phase=0):
    """Sample one sine period as integer photon counts."""
    number_of_points = int(number_of_points)
    if number_of_points < 0:
        raise ValueError('Number of points must be non-negative.')

    phases = phase + np.linspace(
        0, 2 * np.pi * number_of_periods, number_of_points, endpoint=False
    )
    return np.rint(baseline_counts * (1 + contrast * np.sin(phases))).astype('int64')


def simulate_sine_laser_pulses(number_of_pulses, pulse_length_bins, signal_length_bins,
                               baseline_counts=1000, contrast=0.2, number_of_periods=1,
                               phase=0):
    """Create laser pulses whose leading signal region follows one sine period.

    The remainder of every pulse is kept at ``baseline_counts`` so that signal
    and normalization windows can be placed in visibly distinct regions.
    """
    number_of_pulses = int(number_of_pulses)
    pulse_length_bins = int(pulse_length_bins)
    signal_length_bins = min(int(signal_length_bins), pulse_length_bins)
    if number_of_pulses < 0 or pulse_length_bins < 1 or signal_length_bins < 1:
        raise ValueError('Pulse count must be non-negative and pulse lengths must be positive.')

    signal_counts = simulate_sine_counts(
        number_of_pulses, baseline_counts, contrast, number_of_periods, phase
    )
    pulses = np.full((number_of_pulses, pulse_length_bins), baseline_counts, dtype='int64')
    pulses[:, :signal_length_bins] = signal_counts[:, None]
    return pulses


def simulate_gated_sine_trace(number_of_gates, record_length_bins, signal_length_bins,
                              edge_spacing_bins, baseline_counts=1000, contrast=0.2,
                              number_of_periods=1, phase=0):
    """Create gated traces with zero-filled regions around each laser pulse."""
    record_length_bins = int(record_length_bins)
    edge_spacing_bins = int(edge_spacing_bins)
    if record_length_bins < 1 or edge_spacing_bins < 0:
        raise ValueError('Record length must be positive and edge spacing must be non-negative.')

    edge_spacing_bins = min(edge_spacing_bins, (record_length_bins - 1) // 2)
    pulse_length_bins = record_length_bins - 2 * edge_spacing_bins
    pulses = simulate_sine_laser_pulses(
        number_of_gates, pulse_length_bins, min(signal_length_bins, pulse_length_bins),
        baseline_counts, contrast, number_of_periods, phase
    )
    traces = np.zeros((int(number_of_gates), record_length_bins), dtype='int64')
    traces[:, edge_spacing_bins:edge_spacing_bins + pulse_length_bins] = pulses
    return traces


def simulate_ungated_sine_trace(number_of_pulses, pulse_length_bins, signal_length_bins,
                                delay_bins, spacing_bins, baseline_counts=1000, contrast=0.2,
                                number_of_periods=1, phase=0):
    """Create a flat pulse train with zero-filled delay and inter-pulse spacing."""
    delay_bins = int(delay_bins)
    spacing_bins = int(spacing_bins)
    if delay_bins < 0 or spacing_bins < 0:
        raise ValueError('Delay and spacing must be non-negative.')

    pulses = simulate_sine_laser_pulses(number_of_pulses, pulse_length_bins,
                                        signal_length_bins, baseline_counts, contrast,
                                        number_of_periods, phase)
    pulse_periods = np.zeros(
        (int(number_of_pulses), delay_bins + int(pulse_length_bins) + spacing_bins),
        dtype='int64'
    )
    pulse_periods[:, delay_bins:delay_bins + int(pulse_length_bins)] = pulses
    return pulse_periods.ravel()
