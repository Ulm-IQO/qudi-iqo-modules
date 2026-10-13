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

    # AWG hardware constraint: a given waveform can only be looped consecutively
    # via a single sequence step fewer than 2**16 times.
    _MAX_SEQUENCE_LOOP_COUNT = 2**16 - 1

    def _set_channel_high(self, element, channel):
        """Set a channel (digital or analog) HIGH for the entire duration of `element`.
        `channel` may also name several channels: a list/tuple, or a comma-separated string
        such as 'd_ch2,d_ch3,d_ch4' (e.g. a pulser trigger plus current-polarity markers).
        None or '' is a no-op."""
        if channel is None:
            return
        if isinstance(channel, (list, tuple)) or ',' in channel:
            for ch in (channel if isinstance(channel, (list, tuple)) else channel.split(',')):
                self._set_channel_high(element, ch.strip())
            return
        channel = channel.strip()
        if channel.startswith('d'):
            element.digital_high[channel] = True
        elif channel.startswith('a'):
            element.pulse_function[channel] = SamplingFunctions.DC(
                voltage=self.analog_trigger_voltage
            )

    def _set_always_on_channels(self, element, always_on_channel):
        """
        Accepts either a single channel string OR a list/tuple of channel strings
        and sets ALL of them HIGH for the entire duration of `element`.
        None or an empty list is a no-op.
        """
        if always_on_channel is None:
            return
        channels = [always_on_channel] if isinstance(always_on_channel, str) else always_on_channel
        for channel in channels:
            self._set_channel_high(element, channel)

    def _get_pulser_off_mw_element(self, length, increment, amp=None, freq=None, phase=None, always_on_channel=None):
        mw_element = self._get_mw_element(length=length, increment=increment, amp=amp, freq=freq, phase=phase)
        self._set_always_on_channels(mw_element, always_on_channel)
        return mw_element

    def _get_pulser_off_idle_element(self, length, increment, always_on_channel=None):
        idle_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(idle_element, always_on_channel)
        return idle_element

    def _get_pulser_on_idle_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        idle_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(idle_element, always_on_channel)
        self._set_channel_high(idle_element, pulser_channel)
        return idle_element

    def _get_pulser_on_laser_gate_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        laser_gate_element = self._get_laser_gate_element(length=length, increment=increment)
        self._set_always_on_channels(laser_gate_element, always_on_channel)
        self._set_channel_high(laser_gate_element, pulser_channel)
        return laser_gate_element

    def _get_pulser_on_delay_gate_element(self, always_on_channel=None, pulser_channel=None):
        delay_gate_element = self._get_delay_gate_element()
        self._set_always_on_channels(delay_gate_element, always_on_channel)
        self._set_channel_high(delay_gate_element, pulser_channel)
        return delay_gate_element

    def _get_pulser_on_mw_laser_gate_element(self, length, increment, amp, freq, phase,
                                            always_on_channel=None, pulser_channel=None):
        """Same idea as _get_pulser_on_laser_gate_element, but for the combined
        mw+laser gate element used by CW ODMR (mw and laser simultaneously HIGH)."""
        mw_laser_element = self._get_mw_laser_gate_element(
            length=length, increment=increment, amp=amp, freq=freq, phase=phase)
        self._set_always_on_channels(mw_laser_element, always_on_channel)
        self._set_channel_high(mw_laser_element, pulser_channel)
        return mw_laser_element

    def _get_pulser_on_laser_only_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        """
        Turns ON just the physical laser channel (self.laser_channel) for `length`
        seconds, WITHOUT toggling the detector gate channel (unlike
        _get_pulser_on_laser_gate_element / _get_mw_laser_gate_element, which
        trigger a counted readout event). Used exclusively for laser warm-up
        pulses that must NOT be seen by the pulsed-measurement analysis as an
        additional data point -- so no laser_ignore_list / number_of_lasers
        bookkeeping is required for it.
        """
        warmup_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(warmup_element, always_on_channel)
        self._set_channel_high(warmup_element, pulser_channel)
        self._set_channel_high(warmup_element, self.laser_channel)
        return warmup_element

    def _pad_ensemble_to_granularity(self, block, on, always_on_channel, pulser_channel,
                                    extra_high_channels=None):
        """
        Given a PulseBlock with its "real" elements already appended, measure
        its EXACT sample count via analyze_block_ensemble() (same rounding
        logic the sampler itself uses), then append an idle pad element
        (state = on/off, as specified) so the block's total length is both
        >= the pulse generator's minimum waveform length AND an exact
        multiple of its granularity step.

        This prevents SequenceGeneratorLogic.sample_pulse_block_ensemble()
        from later silently appending its OWN idle_extension block to fix
        granularity -- which forces ALL digital channels LOW (including any
        channel that must stay continuously HIGH, e.g. a PB "always on"
        channel) and whose length isn't known until AFTER sampling, making
        any duty-cycle calculation done ahead of time wrong.

        @param extra_high_channels: optional channel string, or list of channel
            strings, that must ALSO be held HIGH throughout the pad element (in
            addition to whichever of always_on_channel/pulser_channel apply from
            `on`). Required whenever the block being padded contains a custom
            element asserting a channel that _get_pulser_on_idle_element /
            _get_pulser_off_idle_element don't know about (e.g. self.laser_channel
            in a laser-only warm-up element) -- otherwise the pad would silently
            drop that channel for its (possibly very short) duration, creating a
            spurious high-frequency transition that can violate the pulse
            generator's minimum instruction length.

        @return (float, float): (pad_length_s added, final total length_s of
                                the block including the pad)
        """
        self.save_block(block)
        temp_ensemble = PulseBlockEnsemble(name='__tmp_granularity_check__', rotating_frame=False)
        temp_ensemble.append((block.name, 0))

        info = self.analyze_block_ensemble(temp_ensemble)
        nominal_samples = int(info['number_of_samples'])

        sample_rate  = self.pulse_generator_settings['sample_rate']
        min_samples  = int(self.pulse_generator_constraints.waveform_length.min)
        step_samples = int(self.pulse_generator_constraints.waveform_length.step)

        target_samples = max(nominal_samples, min_samples)
        remainder = target_samples % step_samples
        if remainder != 0:
            target_samples += step_samples - remainder
        pad_samples = target_samples - nominal_samples

        pad_length_s = 0.0
        if pad_samples > 0:
            pad_length_s = pad_samples / sample_rate
            if on:
                pad_element = self._get_pulser_on_idle_element(
                    length=pad_length_s, increment=0,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            else:
                pad_element = self._get_pulser_off_idle_element(
                    length=pad_length_s, increment=0, always_on_channel=always_on_channel)

            if extra_high_channels is not None:
                channels = [extra_high_channels] if isinstance(extra_high_channels, str) else extra_high_channels
                for ch in channels:
                    self._set_channel_high(pad_element, ch)

            block.append(pad_element)
            self.save_block(block)

        total_length_s = target_samples / sample_rate
        return pad_length_s, total_length_s

    def _pad_block_front_to_granularity(self, block, always_on_channel, pulser_channel):
        """
        Like _pad_ensemble_to_granularity, but the pad (pulser ON, idle) is inserted at the
        START of the block -- for blocks whose last element must run on seamlessly into the
        next sequence step (e.g. a readout gate continued by a shared tail).

        @return (float, float): (pad_length_s added, final total length_s of the block)
        """
        self.save_block(block)
        temp_ensemble = PulseBlockEnsemble(name='__tmp_granularity_check__', rotating_frame=False)
        temp_ensemble.append((block.name, 0))
        nominal_samples = int(self.analyze_block_ensemble(temp_ensemble)['number_of_samples'])
        sample_rate = self.pulse_generator_settings['sample_rate']
        min_samples = int(self.pulse_generator_constraints.waveform_length.min)
        step_samples = int(self.pulse_generator_constraints.waveform_length.step)
        target_samples = max(nominal_samples, min_samples)
        if target_samples % step_samples:
            target_samples += step_samples - target_samples % step_samples
        pad_samples = target_samples - nominal_samples
        pad_length_s = 0.0
        if pad_samples > 0:
            pad_length_s = pad_samples / sample_rate
            block.insert(0, self._get_pulser_on_idle_element(
                length=pad_length_s, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel))
            self.save_block(block)
        return pad_length_s, target_samples / sample_rate

    def _build_duty_cycle_correction(self, name, correction_length, correction_on,
                                    always_on_channel, pulser_channel,
                                    preferred_base_length,
                                    created_blocks, created_ensembles):
        """
        Build a duty-cycle correction PulseBlock/PulseBlockEnsemble whose base
        element is looped via sequence-step `repetitions` to reach
        `correction_length` in total (memory-efficient: a single small waveform
        is uploaded, not one giant unique one).

        If looping `preferred_base_length` enough times to reach
        correction_length would require more than _MAX_SEQUENCE_LOOP_COUNT
        consecutive plays -- exceeding the AWG's per-step loop-count limit --
        the base element's length is instead increased just enough to bring the
        required loop count back under the limit.

        @return (PulseBlockEnsemble, int): the created correction ensemble, and
            the `repetitions` value to use for it in the sequence step
            (i.e. total plays = repetitions + 1).
        """
        def _make_block(length):
            if correction_on:
                element = self._get_pulser_on_idle_element(
                    length=length, increment=0,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            else:
                element = self._get_pulser_off_idle_element(
                    length=length, increment=0, always_on_channel=always_on_channel)
            block = PulseBlock(name=name + '_duty_correction')
            block.append(element)
            _, actual_length_s = self._pad_ensemble_to_granularity(
                block, on=correction_on,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            return block, actual_length_s

        correction_block, actual_base_length = _make_block(preferred_base_length)
        n_plays_needed = max(1, int(np.ceil(correction_length / actual_base_length)))

        if n_plays_needed > self._MAX_SEQUENCE_LOOP_COUNT:
            self.log.warning(
                'Duty-cycle correction for "{0}" would require {1} loops of a {2:.3e} s '
                'base element, exceeding the AWG limit of {3}. Lengthening the base '
                'element instead of the loop count.'.format(
                    name, n_plays_needed, preferred_base_length, self._MAX_SEQUENCE_LOOP_COUNT))
            required_base_length = correction_length / self._MAX_SEQUENCE_LOOP_COUNT
            correction_block, actual_base_length = _make_block(required_base_length)
            n_plays_needed = max(1, int(np.ceil(correction_length / actual_base_length)))
            n_plays_needed = min(n_plays_needed, self._MAX_SEQUENCE_LOOP_COUNT)

        created_blocks.append(correction_block)

        correction_ensemble = PulseBlockEnsemble(name=name + '_duty_correction', rotating_frame=False)
        correction_ensemble.append((correction_block.name, 0))
        created_ensembles.append(correction_ensemble)

        reps = n_plays_needed - 1
        return correction_ensemble, reps

    def _build_looped_repolarization(self, name, total_length, always_on_channel, pulser_channel,
                                     created_blocks, created_ensembles, base_length=3e-6):
        """
        Laser-only (gate off) repolarization of `total_length`, realized as ONE short base
        waveform looped via sequence-step `repetitions`, so a long repolarization costs almost
        no waveform memory. The base is padded with the laser (and pulser/always-on channels)
        kept HIGH, so consecutive plays and the following readout join seamlessly. The total
        is split into whole plays of a base of about `base_length` (adjusted so the plays add up
        to total_length; exact up to the length granularity per play).

        @return (PulseBlockEnsemble, int, float): the ensemble, the `repetitions` value for its
            sequence step (total plays = repetitions + 1) and the realized total length in s.
        """
        n_plays = min(max(1, int(np.ceil(total_length / base_length - 1e-9))),
                      self._MAX_SEQUENCE_LOOP_COUNT)
        base_length = total_length / n_plays
        block = PulseBlock(name=name + '_repolarize')
        block.append(self._get_pulser_on_laser_only_element(
            length=base_length, increment=0,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel))
        _, actual_base_length = self._pad_ensemble_to_granularity(
            block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel,
            extra_high_channels=self.laser_channel)
        created_blocks.append(block)

        ensemble = PulseBlockEnsemble(name=block.name, rotating_frame=False)
        ensemble.append((block.name, 0))
        created_ensembles.append(ensemble)

        return ensemble, n_plays - 1, n_plays * actual_base_length

    def generate_bd_cw_odmr_gradient(self, name='cw_odmr_gradient', freq_start=2.8e9, freq_stop=3e9,
                                    num_of_points=10, mw_amp=0.2, mw_length=1e-6,
                                    always_on_ch='d_ch15', gradient_mode=0, gradient_mode_ch='d_ch6',
                                    pulser_ch='d_ch3', duty_cycle=0.2, rising_time=100e-9, falling_time=100e-9,
                                    laser_warmup_time=100e-6, laser_cooldown=10e-6, alternating=False,
                                    repolarize_time=30e-6, repolarize_reference_only=False,
                                    shared_tail=False):
        """
        CW ODMR sequence for combined AWG + PulseBlaster setup, extended with:

        - shared_tail    : if True, the detector delay, wait_time and falling ramp
                            (identical for every point) are ONE shared waveform
                            played as its own sequence step after every point, so
                            each point's waveform holds only its rising ramp and
                            the mw + laser readout -- e.g. 2.1 us instead of
                            13.2 us per point with wait_time = 10 us. The
                            granularity pad of a point is then placed at its
                            START (pulser ON, laser off, < 1 granularity step), so
                            the readout gate runs on into the shared tail without
                            a break. Same timing otherwise. Default False (the
                            original single-waveform points).

        - alternating    : if True, every frequency point is immediately followed
                            by a reference point with the identical block structure
                            (rising, laser + gate, delay, wait, falling, same pulser
                            states) but no microwave. The reference is the same for
                            every frequency, so one shared waveform is used. It is
                            included in the duty-cycle accounting. Trace 2 is the
                            reference; use the "Delta" alternative plot for
                            trace 1 - trace 2.
        - repolarize_time: alternating mode only -- a laser-only (ungated,
                            pulser ON) pulse right after the rising ramp of every
                            point (normal and reference), so the reference does not
                            start from the spin state left by the preceding
                            microwave point. 0 disables it. With it, every point
                            is played as [shared rising ramp] [shared 3 us
                            laser-only waveform, looped] [point without its own
                            rising ramp], so long repolarization times cost no
                            extra waveform memory.
        - repolarize_reference_only: if True, the repolarization is played only
                            before the reference points (normal points keep just
                            their rising ramp). A normal point always follows a
                            reference readout, so it already starts repolarized;
                            this roughly halves the repolarization overhead.

        - always_on_ch  : channel held HIGH for the ENTIRE sequence.
        - gradient_mode  : 0 -> gradient_mode_ch stays LOW everywhere (unused).
                            1 -> gradient_mode_ch stays HIGH everywhere, treated
                                identically to always_on_ch.
        - pulser_ch      : channel that must be HIGH while the odmr mw+laser
                            readout is happening, bracketed by rising_time/
                            falling_time.
        - duty_cycle     : target fraction of the TOTAL sequence duration for
                            which pulser_ch is HIGH. Realized via a single
                            small base idle element looped via sequence-step
                            `repetitions`. If the required loop count would
                            exceed the AWG's per-step loop-count limit
                            (_MAX_SEQUENCE_LOOP_COUNT), the base element's
                            length is automatically increased instead.
        - laser_warmup_time / laser_cooldown : an extra laser-ON pulse
                            (pulser_ch HIGH) followed by an idle cooldown
                            period (pulser_ch LOW), inserted ONCE right after
                            the trigger step, but ONLY if a duty-cycle
                            correction is needed at all (i.e. only if there is
                            a long idle stretch during which the laser goes
                            dark every loop). The warm-up pulse fires ONLY the
                            physical laser channel (self.laser_channel) -- it
                            never toggles the detector gate channel, so it is
                            never counted as a data point.

                            IMPORTANT: because warm-up holds pulser_ch HIGH and
                            cooldown holds it LOW, their (measured,
                            post-padding) durations are folded directly into
                            the total_on / total_off duty-cycle accounting
                            BEFORE correction_length is solved for -- so the
                            realized duty cycle over the ENTIRE sequence
                            (including warm-up/cooldown) matches `duty_cycle`,
                            not just the measurement-loop portion of it.

        Per-frequency-point block structure (pulser state noted in brackets):

            rising_element    [ON]   -- rising_time   : ramp pulser up to steady state
            mw_laser_element  [ON]   -- mw_length      : mw + laser readout
            delay_element     [ON]   -- fixed          : detector delay
            waiting_element   [ON]   -- wait_time      : dead time
            falling_element   [OFF]  -- falling_time   : ramp pulser back down

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks    = list()
        created_ensembles = list()
        created_sequences = list()

        freq_array = np.linspace(freq_start, freq_stop, num_of_points)
        repolarize_s = repolarize_time if (alternating and repolarize_time > 0) else 0.0

        # ---- channels that must be HIGH for literally every element ----------
        always_on_channels = [always_on_ch]
        if gradient_mode == 1:
            always_on_channels.append(gradient_mode_ch)
        elif gradient_mode != 0:
            self.log.error('gradient_mode must be 0 or 1 (got {0}); treating as 0.'.format(gradient_mode))
        # gradient_mode == 0 -> gradient_mode_ch is simply never referenced below,
        # so it is never touched and stays LOW for the whole sequence.

        # =========================================================================
        # BLOCK AND ENSEMBLE CREATION
        # =========================================================================

        # ── 1. Trigger (pulser OFF; static channels ON) ────────────────────────
        sync_element = self._get_sync_element()
        self._set_always_on_channels(sync_element, always_on_channels)

        trigger_block = PulseBlock(name='{0}_trigger'.format(name))
        trigger_block.append(sync_element)
        _, trigger_length_s = self._pad_ensemble_to_granularity(
            trigger_block, on=False,
            always_on_channel=always_on_channels, pulser_channel=pulser_ch)
        created_blocks.append(trigger_block)

        trigger_ensemble = PulseBlockEnsemble(name='{0}_trigger'.format(name), rotating_frame=False)
        trigger_ensemble.append((trigger_block.name, 0))
        created_ensembles.append(trigger_ensemble)

        # ── 2. One block/ensemble per frequency point ───────────────────────────
        cw_odmr_ensembles = dict()
        total_on_s  = 0.0
        total_off_s = trigger_length_s   # trigger contributes pure OFF time
        delay_length_s = None

        for kk, freq in enumerate(freq_array):
            rising_element = self._get_pulser_on_idle_element(
                length=rising_time, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            mw_laser_element = self._get_pulser_on_mw_laser_gate_element(
                length=mw_length, increment=0, amp=mw_amp, freq=freq, phase=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            delay_element = self._get_pulser_on_delay_gate_element(
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            waiting_element = self._get_pulser_on_idle_element(
                length=self.wait_time, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            falling_element = self._get_pulser_off_idle_element(
                length=falling_time, increment=0, always_on_channel=always_on_channels)

            if delay_length_s is None:
                delay_length_s = delay_element.init_length_s

            point_block = PulseBlock(name='{0}_freq_{1}'.format(name, kk))
            if repolarize_s == 0:
                # with repolarization the rising ramp is a separate shared step (see 2c)
                point_block.append(rising_element)
            point_block.append(mw_laser_element)
            if shared_tail:
                # delay / wait / falling come from the shared tail step (see 2d)
                pad_length_s, _ = self._pad_block_front_to_granularity(
                    point_block, always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            else:
                point_block.append(delay_element)
                point_block.append(waiting_element)
                point_block.append(falling_element)
                pad_length_s, _ = self._pad_ensemble_to_granularity(
                    point_block, on=False,
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(point_block)

            point_ensemble = PulseBlockEnsemble(name='{0}_freq_{1}'.format(name, kk), rotating_frame=False)
            point_ensemble.append((point_block.name, 0))
            created_ensembles.append(point_ensemble)
            cw_odmr_ensembles[kk] = point_ensemble

            total_on_s  += mw_length + delay_length_s + self.wait_time
            if repolarize_s == 0:
                total_on_s += rising_time
            if shared_tail:
                total_on_s  += pad_length_s            # front pad, pulser ON
                total_off_s += falling_time            # tail pad: added once per play (2d)
            else:
                total_off_s += falling_time + pad_length_s

        # ── 2b. Shared microwave-off reference point (alternating only) ─────────
        ref_ensemble = None
        if alternating:
            ref_block = PulseBlock(name='{0}_ref'.format(name))
            if repolarize_s == 0:
                ref_block.append(self._get_pulser_on_idle_element(
                    length=rising_time, increment=0,
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch))
            ref_block.append(self._get_pulser_on_laser_gate_element(
                length=mw_length, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch))
            if shared_tail:
                ref_pad_length_s, _ = self._pad_block_front_to_granularity(
                    ref_block, always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            else:
                ref_block.append(self._get_pulser_on_delay_gate_element(
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch))
                ref_block.append(self._get_pulser_on_idle_element(
                    length=self.wait_time, increment=0,
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch))
                ref_block.append(self._get_pulser_off_idle_element(
                    length=falling_time, increment=0, always_on_channel=always_on_channels))
                ref_pad_length_s, _ = self._pad_ensemble_to_granularity(
                    ref_block, on=False,
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(ref_block)

            ref_ensemble = PulseBlockEnsemble(name=ref_block.name, rotating_frame=False)
            ref_ensemble.append((ref_block.name, 0))
            created_ensembles.append(ref_ensemble)

            # played once per frequency point
            total_on_s  += num_of_points * (mw_length + delay_length_s + self.wait_time)
            if repolarize_s == 0:
                total_on_s += num_of_points * rising_time
            if shared_tail:
                total_on_s  += num_of_points * ref_pad_length_s
                total_off_s += num_of_points * falling_time
            else:
                total_off_s += num_of_points * (falling_time + ref_pad_length_s)

        # ── 2c. Shared rising ramp + looped laser-only repolarization (alternating
        # with repolarize_time > 0): played before every point (normal and reference) ──
        rising_ensemble = None
        repolarize_ensemble, repolarize_reps = None, 0
        if repolarize_s > 0:
            rising_block = PulseBlock(name='{0}_rising'.format(name))
            rising_block.append(self._get_pulser_on_idle_element(
                length=rising_time, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch))
            _, rising_length_s = self._pad_ensemble_to_granularity(
                rising_block, on=True,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(rising_block)
            rising_ensemble = PulseBlockEnsemble(name=rising_block.name, rotating_frame=False)
            rising_ensemble.append((rising_block.name, 0))
            created_ensembles.append(rising_ensemble)

            repolarize_ensemble, repolarize_reps, repolarize_length_s = \
                self._build_looped_repolarization(
                    name, repolarize_s, always_on_channel=always_on_channels,
                    pulser_channel=pulser_ch,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

            n_points_played = 2 * num_of_points if alternating else num_of_points
            n_repolarizations = num_of_points if repolarize_reference_only else n_points_played
            total_on_s += n_points_played * rising_length_s + n_repolarizations * repolarize_length_s

        # -- 2d. Shared tail: delay | wait | falling (shared_tail only), played after
        # every point (normal and reference) --
        tail_ensemble = None
        if shared_tail:
            tail_block = PulseBlock(name='{0}_tail'.format(name))
            tail_block.append(self._get_pulser_on_delay_gate_element(
                always_on_channel=always_on_channels, pulser_channel=pulser_ch))
            tail_block.append(self._get_pulser_on_idle_element(
                length=self.wait_time, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch))
            tail_block.append(self._get_pulser_off_idle_element(
                length=falling_time, increment=0, always_on_channel=always_on_channels))
            tail_pad_s, _ = self._pad_ensemble_to_granularity(
                tail_block, on=False,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(tail_block)
            tail_ensemble = PulseBlockEnsemble(name=tail_block.name, rotating_frame=False)
            tail_ensemble.append((tail_block.name, 0))
            created_ensembles.append(tail_ensemble)
            total_off_s += (2 * num_of_points if alternating else num_of_points) * tail_pad_s

        # =========================================================================
        # Duty-cycle correction -- warm-up (ON) + cooldown (OFF) are built FIRST
        # (whenever a correction is needed at all) so their MEASURED durations
        # can be folded into total_on_s / total_off_s before correction_length
        # is solved for. This ensures the realized duty cycle over the entire
        # sequence -- not just the measurement loop -- matches `duty_cycle`.
        # =========================================================================
        total_all_s = total_on_s + total_off_s
        p_on_base = total_on_s / total_all_s if total_all_s > 0 else 0.0
        need_correction = not np.isclose(duty_cycle, p_on_base)

        correction_ensemble = None
        correction_reps = 0
        warmup_ensemble = None
        cooldown_ensemble = None

        if need_correction:
            # ── Warm-up (pulser_ch HIGH; laser_channel explicitly HIGH too) ──
            warmup_element = self._get_pulser_on_laser_only_element(
                length=laser_warmup_time, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            warmup_block = PulseBlock(name=name + '_laser_warmup')
            warmup_block.append(warmup_element)
            _, warmup_length_s = self._pad_ensemble_to_granularity(
                warmup_block, on=True,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch,
                extra_high_channels=self.laser_channel)
            created_blocks.append(warmup_block)

            warmup_ensemble = PulseBlockEnsemble(name=name + '_laser_warmup', rotating_frame=False)
            warmup_ensemble.append((warmup_block.name, 0))
            created_ensembles.append(warmup_ensemble)

            # ── Cooldown (pulser_ch LOW; laser implicitly LOW, no special-casing) ──
            cooldown_element = self._get_pulser_off_idle_element(
                length=laser_cooldown, increment=0, always_on_channel=always_on_channels)
            cooldown_block = PulseBlock(name=name + '_laser_cooldown')
            cooldown_block.append(cooldown_element)
            _, cooldown_length_s = self._pad_ensemble_to_granularity(
                cooldown_block, on=False,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(cooldown_block)

            cooldown_ensemble = PulseBlockEnsemble(name=name + '_laser_cooldown', rotating_frame=False)
            cooldown_ensemble.append((cooldown_block.name, 0))
            created_ensembles.append(cooldown_ensemble)

            # ── Fold warm-up/cooldown into the totals BEFORE solving for
            # correction_length ──────────────────────────────────────────────
            total_on_s  += warmup_length_s
            total_off_s += cooldown_length_s
            total_all_s  = total_on_s + total_off_s
            p_on = total_on_s / total_all_s

            correction_on = None
            correction_length = 0.0
            if duty_cycle > p_on:
                L = (duty_cycle * total_all_s - total_on_s) / (1.0 - duty_cycle)
                if L > 0:
                    correction_on = True
                    correction_length = L
            elif duty_cycle < p_on:
                L = total_on_s / duty_cycle - total_all_s
                if L > 0:
                    correction_on = False
                    correction_length = L
            # else: warm-up/cooldown alone already hit duty_cycle exactly -->
            # no additional correction block needed.

            if correction_on is not None:
                preferred_base_length = self.wait_time if correction_on else falling_time
                correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                    name=name, correction_length=correction_length, correction_on=correction_on,
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch,
                    preferred_base_length=preferred_base_length,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================
        cw_odmr_sequence = PulseSequence(name=name, rotating_frame=False)

        cw_odmr_sequence.append(trigger_ensemble.name)
        cw_odmr_sequence[-1].repetitions = 0

        if warmup_ensemble is not None:
            cw_odmr_sequence.append(warmup_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

        if cooldown_ensemble is not None:
            cw_odmr_sequence.append(cooldown_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

        def _append_repolarization(repolarize_now=True):
            # the shared rising ramp is always needed (points have none of their own here)
            if repolarize_ensemble is not None:
                cw_odmr_sequence.append(rising_ensemble.name)
                cw_odmr_sequence[-1].repetitions = 0
                if repolarize_now:
                    cw_odmr_sequence.append(repolarize_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = repolarize_reps

        def _append_tail():
            if tail_ensemble is not None:
                cw_odmr_sequence.append(tail_ensemble.name)
                cw_odmr_sequence[-1].repetitions = 0

        for kk, freq in enumerate(freq_array):
            _append_repolarization(not repolarize_reference_only)
            cw_odmr_sequence.append(cw_odmr_ensembles[kk].name)
            cw_odmr_sequence[-1].repetitions = 0
            _append_tail()
            if ref_ensemble is not None:
                _append_repolarization()
                cw_odmr_sequence.append(ref_ensemble.name)
                cw_odmr_sequence[-1].repetitions = 0
                _append_tail()

        if correction_ensemble is not None:
            cw_odmr_sequence.append(correction_ensemble.name)
            cw_odmr_sequence[-1].repetitions = correction_reps

        cw_odmr_sequence[-1].go_to = 1

        cw_odmr_sequence.refresh_parameters()

        cw_odmr_sequence.measurement_information['alternating']         = alternating
        cw_odmr_sequence.measurement_information['laser_ignore_list']   = list()
        cw_odmr_sequence.measurement_information['controlled_variable'] = freq_array
        cw_odmr_sequence.measurement_information['units']               = ('Hz', '')
        cw_odmr_sequence.measurement_information['labels']              = ('Frequency', 'Signal')
        cw_odmr_sequence.measurement_information['number_of_lasers']    = (
            2 * len(freq_array) if alternating else len(freq_array))
        cw_odmr_sequence.measurement_information['counting_length']     = (mw_length + delay_length_s)

        created_sequences.append(cw_odmr_sequence)
        return created_blocks, created_ensembles, created_sequences

    def _build_warmup_and_duty_correction(self, name, total_on_s, total_off_s, duty_cycle,
                                          always_on_channels, pulser_ch, laser_warmup_time,
                                          laser_cooldown, preferred_on_base, preferred_off_base,
                                          created_blocks, created_ensembles):
        """
        Duty-cycle machinery of generate_bd_cw_odmr_gradient as a reusable piece: if the
        measurement part (total_on_s / total_off_s of pulser_ch HIGH / LOW) does not already
        give `duty_cycle`, build a laser warm-up (pulser HIGH, laser only, no gate) and a
        cooldown (pulser LOW), fold their measured lengths into the totals, and build a looped
        idle correction (pulser HIGH or LOW) that brings the whole sequence to `duty_cycle`.

        @return dict: 'warmup', 'cooldown', 'correction' (ensembles or None),
            'correction_reps' (sequence-step repetitions), 'total_on_s', 'total_off_s'
            (including everything built here), 'correction_length_s'.
        """
        out = {'warmup': None, 'cooldown': None, 'correction': None, 'correction_reps': 0,
               'total_on_s': total_on_s, 'total_off_s': total_off_s, 'correction_length_s': 0.0}
        total_all_s = total_on_s + total_off_s
        p_on_base = total_on_s / total_all_s if total_all_s > 0 else 0.0
        if np.isclose(duty_cycle, p_on_base):
            return out
        if not 0.0 < duty_cycle < 1.0:
            self.log.error('duty_cycle must be between 0 and 1 (got {0}); no duty-cycle '
                           'correction is played.'.format(duty_cycle))
            return out

        # ── Warm-up (pulser HIGH; laser_channel explicitly HIGH too, no gate) ──
        warmup_block = PulseBlock(name=name + '_laser_warmup')
        warmup_block.append(self._get_pulser_on_laser_only_element(
            length=laser_warmup_time, increment=0,
            always_on_channel=always_on_channels, pulser_channel=pulser_ch))
        _, warmup_length_s = self._pad_ensemble_to_granularity(
            warmup_block, on=True, always_on_channel=always_on_channels, pulser_channel=pulser_ch,
            extra_high_channels=self.laser_channel)
        created_blocks.append(warmup_block)
        out['warmup'] = PulseBlockEnsemble(name=warmup_block.name, rotating_frame=False)
        out['warmup'].append((warmup_block.name, 0))
        created_ensembles.append(out['warmup'])

        # ── Cooldown (pulser LOW) ──
        cooldown_block = PulseBlock(name=name + '_laser_cooldown')
        cooldown_block.append(self._get_pulser_off_idle_element(
            length=laser_cooldown, increment=0, always_on_channel=always_on_channels))
        _, cooldown_length_s = self._pad_ensemble_to_granularity(
            cooldown_block, on=False, always_on_channel=always_on_channels, pulser_channel=pulser_ch)
        created_blocks.append(cooldown_block)
        out['cooldown'] = PulseBlockEnsemble(name=cooldown_block.name, rotating_frame=False)
        out['cooldown'].append((cooldown_block.name, 0))
        created_ensembles.append(out['cooldown'])

        total_on_s += warmup_length_s
        total_off_s += cooldown_length_s
        total_all_s = total_on_s + total_off_s
        p_on = total_on_s / total_all_s

        correction_on, correction_length = None, 0.0
        if duty_cycle > p_on:
            L = (duty_cycle * total_all_s - total_on_s) / (1.0 - duty_cycle)
            if L > 0:
                correction_on, correction_length = True, L
        elif duty_cycle < p_on:
            L = total_on_s / duty_cycle - total_all_s
            if L > 0:
                correction_on, correction_length = False, L

        if correction_on is not None:
            out['correction'], out['correction_reps'] = self._build_duty_cycle_correction(
                name=name, correction_length=correction_length, correction_on=correction_on,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch,
                preferred_base_length=preferred_on_base if correction_on else preferred_off_base,
                created_blocks=created_blocks, created_ensembles=created_ensembles)
            played = self._ensemble_length_s(out['correction']) * (out['correction_reps'] + 1)
            if correction_on:
                total_on_s += played
            else:
                total_off_s += played
            out['correction_length_s'] = played
        out['total_on_s'], out['total_off_s'] = total_on_s, total_off_s
        return out

    def _ensemble_length_s(self, ensemble):
        """Exact length (s) of an already saved, single-pass ensemble."""
        info = self.analyze_block_ensemble(ensemble)
        return int(info['number_of_samples']) / self.pulse_generator_settings['sample_rate']

    def generate_bd_t1_gradient(self, name='t1_gradient', tau_start=1.0e-6, tau_end=1.0e-3,
                                num_of_points=20, log_spacing=True,
                                always_on_ch='d_ch15', gradient_mode=0, gradient_mode_ch='d_ch6',
                                pulser_ch='d_ch3', pulser_during_tau=False, duty_cycle=0.2,
                                rising_time=100e-9, falling_time=100e-9, init_time=3e-6,
                                laser_warmup_time=100e-6, laser_cooldown=10e-6, alternating=False):
        """
        T1 (spin-lattice relaxation) sequence for the combined AWG + PulseBlaster setup, with the
        always-on / gradient / pulser channels and duty-cycle control of
        generate_bd_cw_odmr_gradient.

        Every point: the NV is polarized by the preceding laser readout, relaxes in the dark for
        tau, and is read out (laser + gate). With alternating, every point is followed by a
        partner point that starts with a pi pulse (self.rabi_period / 2 at
        self.microwave_frequency / self.microwave_amplitude) right before its tau wait; trace 2
        is the pi-pulse partner (use the "Delta" alternative plot for the difference).

        - tau_start, tau_end, num_of_points, log_spacing : tau values, log-spaced (default)
                            or linear from tau_start to tau_end (tau_start = 0 with log
                            spacing: 0 plus log spacing from 1 ns). Long taus are played as
                            a short shared idle waveform looped via sequence-step
                            repetitions plus a small per-point remainder, so tau can reach
                            milliseconds without using waveform memory. The controlled
                            variable holds the actually played taus (granularity-rounded,
                            at least the pulse generator's minimum waveform length).
        - always_on_ch    : channel held HIGH for the ENTIRE sequence.
        - gradient_mode   : 0 -> gradient_mode_ch stays LOW everywhere (unused).
                            1 -> gradient_mode_ch stays HIGH everywhere, like always_on_ch.
        - pulser_ch       : channel HIGH around every laser readout, bracketed by
                            rising_time / falling_time.
        - pulser_during_tau : False (default): pulser_ch LOW during the tau waits (and the
                            pi pulses) -- every readout gets its own rising / falling ramp.
                            True: pulser_ch HIGH during the tau waits and pi pulses as well,
                            i.e. continuously HIGH through the whole measurement loop; only
                            the initialization pulse has a rising ramp and only the end of
                            the loop a falling ramp.
        - duty_cycle      : target fraction of the TOTAL sequence duration for which
                            pulser_ch is HIGH, realized with a looped idle correction step
                            (pulser HIGH or LOW) at the end of the loop, as in
                            generate_bd_cw_odmr_gradient. With pulser_during_tau the
                            measurement loop alone is mostly HIGH, so duty cycles below that
                            fraction are reached by a (long) LOW correction.
        - laser_warmup_time / laser_cooldown : laser-only pulse (pulser HIGH, no gate, never
                            counted as a data point) + idle (pulser LOW), played once after
                            the trigger, only if a duty-cycle correction is needed; included
                            in the duty-cycle accounting.
        - init_time       : laser-only (no gate) pulse right before the first point: after
                            the trigger, warm-up / cooldown and the duty-cycle correction the
                            NV has relaxed, so point 0 needs its own polarization (all other
                            points are polarized by the preceding readout).

        Sequence (pulser state in brackets; pulser_during_tau=False):

            trigger [OFF] -> (laser warm-up [ON] -> cooldown [OFF])
            -> init: rising [ON] | laser only, init_time [ON] | falling [OFF]
            -> per point: (pi pulse) + tau [OFF], long taus looped
                          -> readout: rising | laser + gate | delay | wait_time [ON] | falling [OFF]
            -> (duty-cycle correction [ON or OFF], looped) -> back to the trigger

        Note: the readout's delay, wait_time and falling ramp (and, for the pi-pulse
        partners, the pi pulse) add a constant dark time to every tau.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        sample_rate = self.pulse_generator_settings['sample_rate']
        min_samples = int(self.pulse_generator_constraints.waveform_length.min)
        step_samples = int(self.pulse_generator_constraints.waveform_length.step)

        if log_spacing:
            if tau_start <= 0.0:
                tau_array = np.insert(np.geomspace(1e-9, tau_end, num_of_points - 1), 0, 0.0)
            else:
                tau_array = np.geomspace(tau_start, tau_end, num_of_points)
        else:
            tau_array = np.linspace(tau_start, tau_end, num_of_points)

        # ---- channels that must be HIGH for literally every element ----------
        always_on_channels = [always_on_ch]
        if gradient_mode == 1:
            always_on_channels.append(gradient_mode_ch)
        elif gradient_mode != 0:
            self.log.error('gradient_mode must be 0 or 1 (got {0}); treating as 0.'.format(gradient_mode))

        tau_on = bool(pulser_during_tau)

        def _idle(length_s, on):
            if on:
                return self._get_pulser_on_idle_element(
                    length=length_s, increment=0,
                    always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            return self._get_pulser_off_idle_element(
                length=length_s, increment=0, always_on_channel=always_on_channels)

        def _register(block, on):
            """Pad block to the granularity, make a single-block ensemble of it."""
            pad_s, length_s = self._pad_ensemble_to_granularity(
                block, on=on, always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(block)
            ensemble = PulseBlockEnsemble(name=block.name, rotating_frame=False)
            ensemble.append((block.name, 0))
            created_ensembles.append(ensemble)
            return ensemble, pad_s, length_s

        total_on_s = 0.0
        total_off_s = 0.0

        # =========================================================================
        # BLOCK AND ENSEMBLE CREATION
        # =========================================================================

        # ── 1. Trigger (pulser OFF; static channels ON) ────────────────────────
        sync_element = self._get_sync_element()
        self._set_always_on_channels(sync_element, always_on_channels)
        trigger_block = PulseBlock(name='{0}_trigger'.format(name))
        trigger_block.append(sync_element)
        trigger_ensemble, _, trigger_length_s = _register(trigger_block, on=False)
        total_off_s += trigger_length_s

        # ── 2. Initialization of point 0: rising | laser only (no gate) | (falling) ──
        init_block = PulseBlock(name='{0}_init'.format(name))
        init_block.append(_idle(rising_time, on=True))
        init_block.append(self._get_pulser_on_laser_only_element(
            length=init_time, increment=0,
            always_on_channel=always_on_channels, pulser_channel=pulser_ch))
        if tau_on:
            init_ensemble, init_pad_s, init_length_s = _register(init_block, on=True)
            total_on_s += init_length_s
        else:
            init_block.append(_idle(falling_time, on=False))
            init_ensemble, init_pad_s, init_length_s = _register(init_block, on=False)
            total_on_s += rising_time + init_time
            total_off_s += init_length_s - rising_time - init_time

        # ── 3. Shared readout: (rising) | laser + gate | delay | wait | (falling) ──
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        if not tau_on:
            readout_block.append(_idle(rising_time, on=True))
        readout_block.append(self._get_pulser_on_laser_gate_element(
            length=self.laser_length, increment=0,
            always_on_channel=always_on_channels, pulser_channel=pulser_ch))
        delay_element = self._get_pulser_on_delay_gate_element(
            always_on_channel=always_on_channels, pulser_channel=pulser_ch)
        delay_length_s = delay_element.init_length_s
        readout_block.append(delay_element)
        readout_block.append(_idle(self.wait_time, on=True))
        if tau_on:
            readout_ensemble, _, readout_length_s = _register(readout_block, on=True)
            readout_on_s, readout_off_s = readout_length_s, 0.0
        else:
            readout_block.append(_idle(falling_time, on=False))
            readout_ensemble, _, readout_length_s = _register(readout_block, on=False)
            readout_on_s = rising_time + self.laser_length + delay_length_s + self.wait_time
            readout_off_s = readout_length_s - readout_on_s

        # ── 4. Pi pulse elements for the alternating partners (pulser state of tau) ──
        pi_elements = list()
        if alternating:
            pi_element = self._get_pulser_off_mw_element(
                length=self.rabi_period / 2, increment=0, amp=self.microwave_amplitude,
                freq=self.microwave_frequency, phase=0, always_on_channel=always_on_channels)
            if tau_on:
                self._set_channel_high(pi_element, pulser_ch)
            pi_elements.append(pi_element)
        pi_length_s = sum(el.init_length_s for el in pi_elements)

        # ── 5. Tau waits: per-point head (pi + remainder) + shared looped base ──
        # base: ~1 us, a whole number of granularity steps (never padded), long enough
        # that the longest tau stays below the AWG loop-count limit
        base_samples = max(int(round(1e-6 * sample_rate)),
                           int(np.ceil(max(tau_array) * sample_rate / self._MAX_SEQUENCE_LOOP_COUNT)))
        base_samples = int(np.ceil(base_samples / step_samples)) * step_samples
        base_samples = max(base_samples, int(np.ceil(min_samples / step_samples)) * step_samples)
        base_s = base_samples / sample_rate
        base_ensemble = None

        def _tau_point(label, kk, tau, prefix_elements, prefix_length_s):
            """Steps [(ensemble, repetitions), ...] playing prefix + tau; returns (steps, tau played,
            total length of the steps in s)."""
            nonlocal base_ensemble
            tau_samples = int(round(tau * sample_rate))
            prefix_samples = int(round(prefix_length_s * sample_rate))
            rest_needed = max(0, min_samples - prefix_samples)
            n_base = max(0, (tau_samples - rest_needed) // base_samples)
            rest_samples = tau_samples - n_base * base_samples
            head = PulseBlock(name='{0}_{1}_{2:d}'.format(name, label, kk))
            for el in prefix_elements:
                head.append(el)
            if rest_samples > 0 or not prefix_elements:
                head.append(_idle(max(rest_samples, 1) / sample_rate, on=tau_on))
            head_ensemble, _, head_length_s = _register(head, on=tau_on)
            steps = [(head_ensemble, 0)]
            if n_base > 0:
                if base_ensemble is None:
                    base_block = PulseBlock(name='{0}_wait_base'.format(name))
                    base_block.append(_idle(base_s, on=tau_on))
                    base_ensemble, _, _ = _register(base_block, on=tau_on)
                steps.append((base_ensemble, n_base - 1))
            played_s = head_length_s - prefix_length_s + n_base * base_s
            return steps, played_s, head_length_s + n_base * base_s

        point_steps = list()
        played_taus = list()
        for kk, tau in enumerate(tau_array):
            steps, played, length_s = _tau_point('tau', kk, tau, [], 0.0)
            point_steps.append(steps)
            played_taus.append(played)
            if tau_on:
                total_on_s += length_s
            else:
                total_off_s += length_s
            total_on_s += readout_on_s
            total_off_s += readout_off_s
            if alternating:
                steps, _, length_s = _tau_point('alt_tau', kk, tau, pi_elements, pi_length_s)
                point_steps.append(steps)
                if tau_on:
                    total_on_s += length_s
                else:
                    total_off_s += length_s
                total_on_s += readout_on_s
                total_off_s += readout_off_s

        # ── 6. Falling ramp at the end of the loop (pulser_during_tau only) ──
        end_ensemble = None
        if tau_on:
            end_block = PulseBlock(name='{0}_falling'.format(name))
            end_block.append(_idle(falling_time, on=False))
            end_ensemble, _, end_length_s = _register(end_block, on=False)
            total_off_s += end_length_s

        # ── 7. Duty cycle: warm-up / cooldown + looped correction ──
        dc = self._build_warmup_and_duty_correction(
            name, total_on_s, total_off_s, duty_cycle, always_on_channels, pulser_ch,
            laser_warmup_time, laser_cooldown, preferred_on_base=self.wait_time,
            preferred_off_base=max(falling_time, 1e-6),
            created_blocks=created_blocks, created_ensembles=created_ensembles)
        realized = dc['total_on_s'] / (dc['total_on_s'] + dc['total_off_s'])
        self.log.debug('generate_bd_t1_gradient: realized pulser duty cycle {0:.4f} (target '
                       '{1:.4f}), loop length {2:.4e} s.'.format(
                           realized, duty_cycle, dc['total_on_s'] + dc['total_off_s']))

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================
        n_steps = (3 + (2 if dc['warmup'] is not None else 0) + (1 if end_ensemble else 0)
                   + (1 if dc['correction'] is not None else 0)
                   + sum(len(s) + 1 for s in point_steps))
        max_steps = int(self.pulse_generator_constraints.sequence_steps.max)
        if n_steps > max_steps:
            raise ValueError('generate_bd_t1_gradient: {0} sequence steps needed, more than the AWG '
                             'maximum of {1}; use fewer points.'.format(n_steps, max_steps))

        t1_sequence = PulseSequence(name=name, rotating_frame=False)

        def _add(ensemble, reps=0):
            t1_sequence.append(ensemble.name)
            t1_sequence[-1].repetitions = reps

        _add(trigger_ensemble)
        if dc['warmup'] is not None:
            _add(dc['warmup'])
            _add(dc['cooldown'])
        _add(init_ensemble)
        for steps in point_steps:
            for ensemble, reps in steps:
                _add(ensemble, reps)
            _add(readout_ensemble)
        if end_ensemble is not None:
            _add(end_ensemble)
        if dc['correction'] is not None:
            _add(dc['correction'], dc['correction_reps'])
        t1_sequence[-1].go_to = 1
        t1_sequence.refresh_parameters()

        t1_sequence.measurement_information['alternating'] = alternating
        t1_sequence.measurement_information['laser_ignore_list'] = list()
        t1_sequence.measurement_information['controlled_variable'] = np.array(played_taus)
        t1_sequence.measurement_information['units'] = ('s', '')
        t1_sequence.measurement_information['labels'] = ('Tau<sub>relaxation</sub>', 'Signal')
        t1_sequence.measurement_information['number_of_lasers'] = (
            2 * num_of_points if alternating else num_of_points)
        t1_sequence.measurement_information['counting_length'] = self.laser_length + delay_length_s

        created_sequences.append(t1_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_bd_pulsed_rabi(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9,
                        num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                        duty_cycle=0.2, rising_time=50e-6, falling_time=50e-6,
                        laser_warmup_time=100e-6, laser_cooldown=10e-6, alternating=False):
        """
        Sequence-mode Rabi. Every block that becomes its own AWG waveform is
        explicitly padded to satisfy the pulse generator's minimum waveform
        length / granularity step (via _pad_ensemble_to_granularity), so
        SequenceGeneratorLogic never needs to silently append its own
        idle_extension block afterward.

        The duty-cycle correction is realized as a single small base idle
        element looped via sequence-step `repetitions`; if the required loop
        count would exceed the AWG's per-step loop-count limit
        (_MAX_SEQUENCE_LOOP_COUNT), the base element's length is automatically
        increased instead (see _build_duty_cycle_correction).

        laser_warmup_time / laser_cooldown : an extra laser-ON pulse (pulser
            HIGH) followed by an idle cooldown period (pulser LOW), inserted
            ONCE right after the trigger step, but ONLY if a duty-cycle
            correction is needed at all. The warm-up pulse fires ONLY the
            physical laser channel (never the gate channel), so it is never
            counted as a data point.

            IMPORTANT: because warm-up holds pulser_channel HIGH and cooldown
            holds it LOW, their (measured, post-padding) durations are folded
            directly into the total_on / total_off duty-cycle accounting
            BEFORE correction_length is solved for -- so the realized duty
            cycle over the ENTIRE sequence (including warm-up/cooldown)
            matches `duty_cycle`, not just the tau-sweep portion of it.

        alternating : if True, every tau point (falling, mw, rising, readout) is
            immediately followed by a reference point that is identical except
            that the MW block is replaced by an idle of the same (padded) length.
            Taus whose MW blocks pad to the same length share one reference
            waveform. Trace 2 is the reference; use the "Delta" alternative plot
            for trace 1 - trace 2.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        tau_array = tau_start + np.arange(num_of_points) * tau_step
        sample_rate = self.pulse_generator_settings['sample_rate']

        # ── Shared elements ─────────────────────────────────────────────────
        falling_element = self._get_pulser_off_idle_element(
            length=falling_time, increment=0, always_on_channel=always_on_channel)
        rising_element = self._get_pulser_on_idle_element(
            length=rising_time, increment=0,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        waiting_element = self._get_pulser_on_idle_element(
            length=self.wait_time, increment=0,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        laser_element = self._get_pulser_on_laser_gate_element(
            length=self.laser_length, increment=0,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        delay_element = self._get_pulser_on_delay_gate_element(
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)

        # =========================================================================
        # BLOCK AND ENSEMBLE CREATION -- each padded to granularity immediately
        # =========================================================================

        # ── 1. Trigger ──────────────────────────────────────────────────────
        sync_element = self._get_sync_element()
        self._set_always_on_channels(sync_element, always_on_channel)
        trigger_block = PulseBlock(name='{0}_trigger'.format(name))
        trigger_block.append(sync_element)
        _, trigger_length_s = self._pad_ensemble_to_granularity(
            trigger_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(trigger_block)

        trigger_ensemble = PulseBlockEnsemble(name='{0}_trigger'.format(name), rotating_frame=False)
        trigger_ensemble.append((trigger_block.name, 0))
        created_ensembles.append(trigger_ensemble)

        # ── 2. Falling (shared, off) ────────────────────────────────────────
        falling_block = PulseBlock(name='{0}_falling'.format(name))
        falling_block.append(falling_element)
        _, falling_length_s = self._pad_ensemble_to_granularity(
            falling_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(falling_block)

        falling_ensemble = PulseBlockEnsemble(name='{0}_falling'.format(name), rotating_frame=False)
        falling_ensemble.append((falling_block.name, 0))
        created_ensembles.append(falling_ensemble)

        # ── 3. Rising (shared, on) ──────────────────────────────────────────
        rising_block = PulseBlock(name='{0}_rising'.format(name))
        rising_block.append(rising_element)
        _, rising_length_s = self._pad_ensemble_to_granularity(
            rising_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(rising_block)

        rising_ensemble = PulseBlockEnsemble(name='{0}_rising'.format(name), rotating_frame=False)
        rising_ensemble.append((rising_block.name, 0))
        created_ensembles.append(rising_ensemble)

        # ── 4. Readout (shared, on) ─────────────────────────────────────────
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        readout_block.append(waiting_element)
        _, readout_length_s = self._pad_ensemble_to_granularity(
            readout_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(readout_block)

        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        # ── 5. One MW ensemble per tau (off), each padded individually ──────
        mw_ensembles = dict()
        ref_ensembles = dict()     # kk -> reference ensemble (alternating only)
        ref_by_samples = dict()    # padded MW length in samples -> shared reference ensemble
        mw_length_total_s = 0.0
        for kk, tau in enumerate(tau_array):
            mw_element = self._get_pulser_off_mw_element(
                length=tau, increment=0,
                amp=self.microwave_amplitude, freq=self.microwave_frequency, phase=0,
                always_on_channel=always_on_channel)

            mw_block = PulseBlock(name='{0}_mw_{1}'.format(name, kk))
            mw_block.append(mw_element)
            _, mw_length_s = self._pad_ensemble_to_granularity(
                mw_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            mw_length_total_s += mw_length_s
            created_blocks.append(mw_block)

            mw_ensembles[kk] = PulseBlockEnsemble(name='{0}_mw_{1}'.format(name, kk), rotating_frame=False)
            mw_ensembles[kk].append((mw_block.name, 0))
            created_ensembles.append(mw_ensembles[kk])

            # ── 5b. Microwave-off reference of the same padded length (off) ──
            if alternating:
                n_samples = int(round(mw_length_s * sample_rate))
                if n_samples not in ref_by_samples:
                    ref_block = PulseBlock(name='{0}_ref_{1}'.format(name, len(ref_by_samples)))
                    ref_block.append(self._get_pulser_off_idle_element(
                        length=mw_length_s, increment=0, always_on_channel=always_on_channel))
                    self._pad_ensemble_to_granularity(
                        ref_block, on=False, always_on_channel=always_on_channel,
                        pulser_channel=pulser_channel)
                    created_blocks.append(ref_block)
                    ref_ensemble = PulseBlockEnsemble(name=ref_block.name, rotating_frame=False)
                    ref_ensemble.append((ref_block.name, 0))
                    created_ensembles.append(ref_ensemble)
                    ref_by_samples[n_samples] = ref_ensemble
                ref_ensembles[kk] = ref_by_samples[n_samples]
                mw_length_total_s += mw_length_s   # the reference plays for the same time, OFF

        # =========================================================================
        # Duty-cycle correction -- warm-up (ON) + cooldown (OFF) are built FIRST
        # (whenever a correction is needed at all) so their MEASURED durations
        # can be folded into total_on / total_off before correction_length is
        # solved for.
        # =========================================================================
        n_readouts = 2 * num_of_points if alternating else num_of_points
        total_on  = n_readouts * (rising_length_s + readout_length_s)
        total_off = n_readouts * falling_length_s + mw_length_total_s + trigger_length_s

        total_all = total_on + total_off
        p_on_base = total_on / total_all if total_all > 0 else 0.0
        need_correction = not np.isclose(duty_cycle, p_on_base)

        correction_ensemble = None
        correction_reps = 0
        warmup_ensemble = None
        cooldown_ensemble = None

        if need_correction:
            # ── Warm-up (pulser_channel HIGH; laser_channel explicitly HIGH too) ──
            warmup_element = self._get_pulser_on_laser_only_element(
                length=laser_warmup_time, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            warmup_block = PulseBlock(name=name + '_laser_warmup')
            warmup_block.append(warmup_element)
            _, warmup_length_s = self._pad_ensemble_to_granularity(
                warmup_block, on=True,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                extra_high_channels=self.laser_channel)
            created_blocks.append(warmup_block)

            warmup_ensemble = PulseBlockEnsemble(name=name + '_laser_warmup', rotating_frame=False)
            warmup_ensemble.append((warmup_block.name, 0))
            created_ensembles.append(warmup_ensemble)

            # ── Cooldown (pulser_channel LOW; laser implicitly LOW) ─────────
            cooldown_element = self._get_pulser_off_idle_element(
                length=laser_cooldown, increment=0, always_on_channel=always_on_channel)
            cooldown_block = PulseBlock(name=name + '_laser_cooldown')
            cooldown_block.append(cooldown_element)
            _, cooldown_length_s = self._pad_ensemble_to_granularity(
                cooldown_block, on=False,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(cooldown_block)

            cooldown_ensemble = PulseBlockEnsemble(name=name + '_laser_cooldown', rotating_frame=False)
            cooldown_ensemble.append((cooldown_block.name, 0))
            created_ensembles.append(cooldown_ensemble)

            # ── Fold warm-up/cooldown into the totals BEFORE solving for
            # correction_length ──────────────────────────────────────────────
            total_on  += warmup_length_s
            total_off += cooldown_length_s
            total_all  = total_on + total_off
            p_on = total_on / total_all

            correction_on = None
            correction_length = 0.0
            if duty_cycle > p_on:
                L = (duty_cycle * total_all - total_on) / (1.0 - duty_cycle)
                if L > 0:
                    correction_on = True
                    correction_length = L
            elif duty_cycle < p_on:
                L = total_on / duty_cycle - total_all
                if L > 0:
                    correction_on = False
                    correction_length = L
            # else: warm-up/cooldown alone already hit duty_cycle exactly -->
            # no additional correction block needed.

            if correction_on is not None:
                preferred_base_length = self.wait_time if correction_on else falling_time
                correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                    name=name, correction_length=correction_length, correction_on=correction_on,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                    preferred_base_length=preferred_base_length,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================

        rabi_sequence = PulseSequence(name=name, rotating_frame=False)

        rabi_sequence.append(trigger_ensemble.name)
        rabi_sequence[-1].repetitions = 0

        if warmup_ensemble is not None:
            rabi_sequence.append(warmup_ensemble.name)
            rabi_sequence[-1].repetitions = 0

        if cooldown_ensemble is not None:
            rabi_sequence.append(cooldown_ensemble.name)
            rabi_sequence[-1].repetitions = 0

        for kk, tau in enumerate(tau_array):
            rabi_sequence.append(falling_ensemble.name)
            rabi_sequence[-1].repetitions = 0

            rabi_sequence.append(mw_ensembles[kk].name)
            rabi_sequence[-1].repetitions = 0

            rabi_sequence.append(rising_ensemble.name)
            rabi_sequence[-1].repetitions = 0

            rabi_sequence.append(readout_ensemble.name)
            rabi_sequence[-1].repetitions = 0

            if alternating:
                for ensemble in (falling_ensemble, ref_ensembles[kk], rising_ensemble,
                                 readout_ensemble):
                    rabi_sequence.append(ensemble.name)
                    rabi_sequence[-1].repetitions = 0

        if correction_ensemble is not None:
            rabi_sequence.append(correction_ensemble.name)
            rabi_sequence[-1].repetitions = correction_reps

        rabi_sequence[-1].go_to = 1

        rabi_sequence.refresh_parameters()

        rabi_sequence.measurement_information['alternating'] = alternating
        rabi_sequence.measurement_information['laser_ignore_list'] = list()
        rabi_sequence.measurement_information['controlled_variable'] = tau_array
        rabi_sequence.measurement_information['units'] = ('s', '')
        rabi_sequence.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        rabi_sequence.measurement_information['number_of_lasers'] = n_readouts
        rabi_sequence.measurement_information['counting_length'] = (self.laser_length + delay_element.init_length_s)

        created_sequences.append(rabi_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_bd_pulsedodmr(self, name='pulsedODMR', freq_start=2870.0e6, freq_step=0.2e6, num_of_points=50,
                               alternating=False):
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
        alternating : bool
            If True, each frequency point is followed by a reference point in which the pi-pulse
            is replaced by an idle of the same length (trace 2; use the "Delta" alternative plot
            for trace 1 - trace 2).

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
        ref_element = self._get_idle_element(length=self.rabi_period / 2, increment=0)

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
            if alternating:
                pulsedodmr_block.append(ref_element)
                pulsedodmr_block.append(laser_element)
                pulsedodmr_block.append(delay_element)
                pulsedodmr_block.append(waiting_element)
        created_blocks.append(pulsedodmr_block)

        # Create block ensemble
        block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
        block_ensemble.append((pulsedodmr_block.name, 0))

        # add metadata to invoke settings later on
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['labels'] = ('Frequency', 'Signal')
        block_ensemble.measurement_information['number_of_lasers'] = (
            2 * num_of_points if alternating else num_of_points)
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
                             expected_t2=5e-6, alternating=False):
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
        alternating : bool
            If True, each chirped pulse point is followed by a reference point in which the pulse
            is replaced by an idle of the same length (trace 2; use the "Delta" alternative plot
            for trace 1 - trace 2).

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
        ref_element = self._get_idle_element(length=pulse_length, increment=0)

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
            if alternating:
                chirpedodmr_block.append(ref_element)
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
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['labels'] = ('Frequency', '')
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['number_of_lasers'] = (
            2 * num_of_points if alternating else num_of_points)
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    def generate_bd_AEchirpedodmr(self, name='AllenEberlyChirpODMR', mw_freq_center=2870.0e6, freq_range=500.0e6,
                               freq_overlap=20.0e6, num_of_points=50, pulse_length=500e-9, truncation_ratio=0.1,
                               expected_rabi_frequency=30e6, expected_t2=5e-6, peak_mw_amplitude=0.25,
                               alternating=False):
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
        alternating : bool
            If True, each chirped pulse point is followed by a reference point in which the pulse
            is replaced by an idle of the same length (trace 2; use the "Delta" alternative plot
            for trace 1 - trace 2).

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
        ref_element = self._get_idle_element(length=pulse_length, increment=0)

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
            if alternating:
                chirpedodmr_block.append(ref_element)
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
        block_ensemble.measurement_information['alternating'] = alternating
        block_ensemble.measurement_information['laser_ignore_list'] = list()
        block_ensemble.measurement_information['controlled_variable'] = freq_array
        block_ensemble.measurement_information['labels'] = ('Frequency', '')
        block_ensemble.measurement_information['units'] = ('Hz', '')
        block_ensemble.measurement_information['number_of_lasers'] = (
            2 * num_of_points if alternating else num_of_points)
        block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
            ensemble=block_ensemble, created_blocks=created_blocks)

        # append ensemble to created ensembles
        created_ensembles.append(block_ensemble)
        return created_blocks, created_ensembles, created_sequences

    ################################################################################################
    #                             Generation methods for sequences                                 #
    ################################################################################################
    # def generate_bd_t1_sequencing(self, name='t1_seq', tau_start=1.0e-6,
    #                         tau_max=1.0e-3, num_of_points=10):
    #     """
    #     T1 sequence adapted for combined AWG + PulseBlaster interfuse.

    #     Key differences from the standalone AWG version:
    #     - laser_channel, gate_channel, sync_channel now point to PB channels
    #         (d_ch5, d_ch6, d_ch7 — set in Generator Settings, NOT changed here).
    #     - The interfuse's write_sequence() builds a combined PB waveform that
    #         tiles the per-step PB content in the correct order and inserts an AWG
    #         trigger at the start of each loop cycle.
    #     - The AWG sequence steps contain only AWG channels (idle for T1 without
    #         MW pulse, or pi pulse for inversion-recovery T1). PB channels (laser,
    #         gate) are handled transparently by the interfuse.
    #     - All other logic, element building, and sequence construction is unchanged.
    #     """
    #     created_blocks    = list()
    #     created_ensembles = list()
    #     created_sequences = list()

    #     # ── Tau array (same as original) ─────────────────────────────────────────
    #     k_array   = np.unique(
    #         np.rint(
    #             np.logspace(0., np.log10(tau_max / tau_start), num_of_points)
    #         ).astype(int)
    #     )
    #     tau_array = k_array * tau_start

    #     # ── Readout block (same as original) ─────────────────────────────────────
    #     # _get_laser_gate_element uses self.laser_channel which is now a PB channel
    #     # (e.g. d_ch5). No change needed here — the element builder is channel-agnostic.
    #     laser_element   = self._get_laser_gate_element(length=self.laser_length, increment=0)
    #     delay_element   = self._get_delay_gate_element()
    #     waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

    #     readout_block = PulseBlock(name='{0}_readout'.format(name))
    #     readout_block.append(laser_element)
    #     readout_block.append(delay_element)
    #     readout_block.append(waiting_element)
    #     created_blocks.append(readout_block)

    #     readout_ensemble = PulseBlockEnsemble(
    #         name='{0}_readout'.format(name), rotating_frame=False
    #     )
    #     readout_ensemble.append((readout_block.name, 0))
    #     created_ensembles.append(readout_ensemble)

    #     # ── Sync readout block (same as original) ─────────────────────────────────
    #     # sync_channel now points to a PB channel (e.g. d_ch7).
    #     # _get_sync_element() is channel-agnostic — no change needed.
    #     if self.sync_channel:
    #         sync_element = self._get_sync_element()

    #         sync_readout_block = PulseBlock(name='{0}_readout_sync'.format(name))
    #         sync_readout_block.append(laser_element)
    #         sync_readout_block.append(delay_element)
    #         sync_readout_block.append(waiting_element)
    #         sync_readout_block.append(sync_element)
    #         created_blocks.append(sync_readout_block)

    #         sync_readout_ensemble = PulseBlockEnsemble(
    #             name='{0}_readout_sync'.format(name), rotating_frame=False
    #         )
    #         sync_readout_ensemble.append((sync_readout_block.name, 0))
    #         created_ensembles.append(sync_readout_ensemble)

    #     # ── Tau block (same as original) ──────────────────────────────────────────
    #     # _get_idle_element creates a LOW-on-all-channels element.
    #     # In the combined setup this means both AWG and PB channels are idle —
    #     # the interfuse handles routing each channel to the correct device.
    #     tau_element = self._get_idle_element(length=tau_start, increment=0)

    #     tau_block = PulseBlock(name='{0}_tau'.format(name))
    #     tau_block.append(tau_element)
    #     created_blocks.append(tau_block)

    #     tau_ensemble = PulseBlockEnsemble(
    #         name='{0}_tau'.format(name), rotating_frame=False
    #     )
    #     tau_ensemble.append((tau_block.name, 0))
    #     created_ensembles.append(tau_ensemble)

    #     # ── Build PulseSequence (same as original) ────────────────────────────────
    #     # The interfuse's write_sequence() will:
    #     #   1. Write the AWG sequence (tau + readout waveforms, with repetitions)
    #     #   2. Build a combined PB waveform by tiling per-step PB content:
    #     #        [tau PB content × k_1] + [readout PB content] +
    #     #        [tau PB content × k_2] + [readout PB content] + ...
    #     #   3. Insert AWG trigger at t=0 of the combined PB waveform
    #     #   4. Both devices loop at identical rates → stay synchronised
    #     t1_sequence = PulseSequence(name=name, rotating_frame=False)

    #     for k in k_array:
    #         t1_sequence.append(tau_ensemble.name)
    #         t1_sequence[-1].repetitions = int(k) - 1

    #         if self.sync_channel and k == k_array[-1]:
    #             t1_sequence.append(sync_readout_ensemble.name)
    #         else:
    #             t1_sequence.append(readout_ensemble.name)

    #     # Loop infinitely (same as original)
    #     t1_sequence[-1].go_to = 1

    #     t1_sequence.refresh_parameters()

    #     # ── Measurement metadata (same as original) ───────────────────────────────
    #     t1_sequence.measurement_information['alternating']        = False
    #     t1_sequence.measurement_information['laser_ignore_list']  = list()
    #     t1_sequence.measurement_information['controlled_variable'] = tau_array
    #     t1_sequence.measurement_information['units']              = ('s', '')
    #     t1_sequence.measurement_information['labels']             = (
    #         'Tau<sub>pulse spacing</sub>', 'Signal'
    #     )
    #     t1_sequence.measurement_information['number_of_lasers']   = len(tau_array)
    #     t1_sequence.measurement_information['counting_length']    = \
    #         self._get_sequence_count_length(
    #             t1_sequence, created_ensembles, created_blocks
    #         )

    #     created_sequences.append(t1_sequence)
    #     return created_blocks, created_ensembles, created_sequences

    def generate_bd_t1_sequencing(self, name='t1_seq', tau_start=1.0e-6,
                            tau_max=1.0e-3, num_of_points=10):
        """
        T1 relaxation sequence for combined AWG + PulseBlaster setup.

        Sequence structure
        ------------------
        Step 1  TWAIT=ON (forced by interfuse):
            trigger_ensemble — sync_channel HIGH for one sync pulse duration.
            PB fires this at t=0 of every loop, releasing the AWG's TWAIT and
            starting a complete T1 sweep. AWG idles during this step.

        Steps 2..2N+1 (no TWAIT, alternating):
            tau_ensemble   x k  — free evolution, all channels idle.
            readout_ensemble    — laser HIGH + detector gate HIGH on PB channels.

        Last step: go_to = 1  -> AWG returns to trigger step and waits.
        PB loop restarts      -> fires trigger -> next average begins.

        Required Generator Settings
        ---------------------------
            sync_channel:  d_ch{N}  (PB channel wired to AWG TRIGGER IN BNC)
            laser_channel: d_ch{N}  (PB channel wired to laser AOM)
            gate_channel:  d_ch{N}  (PB channel wired to photon counter gate)

        With default config (pb_channel_d_offset=5):
            d_ch5  = PB hw ch 0
            d_ch6  = PB hw ch 1
            ...
            d_ch13 = PB hw ch 8

        Parameters
        ----------
        name : str
            Name of the PulseSequence to be generated.
        tau_start : float
            Minimum free evolution time in seconds.
        tau_max : float
            Maximum free evolution time in seconds.
        num_of_points : int
            Number of logarithmically spaced tau points.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks    = list()
        created_ensembles = list()
        created_sequences = list()

        # ── Tau array (logarithmically spaced) ───────────────────────────────────
        k_array = np.unique(
            np.rint(
                np.logspace(0., np.log10(tau_max / tau_start), num_of_points)
            ).astype(int)
        )
        tau_array = k_array * tau_start

        # =========================================================================
        # BLOCK AND ENSEMBLE CREATION
        # =========================================================================

        # ── 1. Trigger ensemble (sequence step 1, TWAIT=ON set by interfuse) ─────
        # sync_channel is the PB channel wired to the AWG TRIGGER IN BNC.
        # _get_sync_element() creates an element with sync_channel HIGH.
        # On the AWG side: this step is analog idle (a_ch outputs zero).
        # On the PB side: sync_channel fires HIGH, triggering the AWG to advance.
        sync_element = self._get_sync_element()

        trigger_block = PulseBlock(name='{0}_trigger'.format(name))
        trigger_block.append(sync_element)
        created_blocks.append(trigger_block)

        trigger_ensemble = PulseBlockEnsemble(
            name='{0}_trigger'.format(name),
            rotating_frame=False
        )
        trigger_ensemble.append((trigger_block.name, 0))
        created_ensembles.append(trigger_ensemble)

        # ── 2. Readout ensemble ───────────────────────────────────────────────────
        # laser_channel HIGH, gate_channel HIGH during readout window.
        # AWG analog output is idle (no MW pulse during readout in standard T1).
        laser_element   = self._get_laser_gate_element(length=self.laser_length, increment=0)
        delay_element   = self._get_delay_gate_element()
        waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(laser_element)
        readout_block.append(delay_element)
        readout_block.append(waiting_element)
        created_blocks.append(readout_block)

        readout_ensemble = PulseBlockEnsemble(
            name='{0}_readout'.format(name),
            rotating_frame=False
        )
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        # ── 3. Tau ensemble ───────────────────────────────────────────────────────
        # All channels idle for the full tau_start duration.
        # AWG analog = 0V, all PB channels LOW.
        # Repeated k-1 times by the sequence, giving total free evolution = k * tau_start.
        tau_element = self._get_idle_element(length=tau_start, increment=0)

        tau_block = PulseBlock(name='{0}_tau'.format(name))
        tau_block.append(tau_element)
        created_blocks.append(tau_block)

        tau_ensemble = PulseBlockEnsemble(
            name='{0}_tau'.format(name),
            rotating_frame=False
        )
        tau_ensemble.append((tau_block.name, 0))
        created_ensembles.append(tau_ensemble)

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================
        #
        # Full sequence layout (N tau points):
        #
        #   Step 1      : trigger_ensemble   repetitions=0  (plays once)  TWAIT=ON
        #   Step 2      : tau_ensemble       repetitions=k1-1             TWAIT=OFF
        #   Step 3      : readout_ensemble   repetitions=0                TWAIT=OFF
        #   Step 4      : tau_ensemble       repetitions=k2-1             TWAIT=OFF
        #   Step 5      : readout_ensemble   repetitions=0                TWAIT=OFF
        #   ...
        #   Step 2N     : tau_ensemble       repetitions=kN-1             TWAIT=OFF
        #   Step 2N+1   : readout_ensemble   repetitions=0  go_to=1       TWAIT=OFF
        #
        # Total AWG sequence steps = 1 + 2*N

        t1_sequence = PulseSequence(name=name, rotating_frame=False)

        # Step 1: trigger — TWAIT=ON is forced on this step by the interfuse's
        # write_sequence() method, making it equivalent to TRIG mode in waveform mode.
        t1_sequence.append(trigger_ensemble.name)
        t1_sequence[-1].repetitions = 0

        # Steps 2..2N+1: alternating tau and readout
        for k in k_array:
            # Tau: free evolution for k * tau_start total
            t1_sequence.append(tau_ensemble.name)
            t1_sequence[-1].repetitions = int(k) - 1

            # Readout: laser + gate on PB channels
            t1_sequence.append(readout_ensemble.name)
            t1_sequence[-1].repetitions = 0

        # After last readout: return to trigger step and wait for next PB trigger
        t1_sequence[-1].go_to = 1

        # ── Finalise ──────────────────────────────────────────────────────────────
        t1_sequence.refresh_parameters()

        t1_sequence.measurement_information['alternating']         = False
        t1_sequence.measurement_information['laser_ignore_list']   = list()
        t1_sequence.measurement_information['controlled_variable'] = tau_array
        t1_sequence.measurement_information['units']               = ('s', '')
        t1_sequence.measurement_information['labels']              = (
            'Tau<sub>pulse spacing</sub>', 'Signal'
        )
        t1_sequence.measurement_information['number_of_lasers']    = len(tau_array)
        t1_sequence.measurement_information['counting_length']     = (
            self._get_sequence_count_length(
                t1_sequence, created_ensembles, created_blocks
            )
        )

        created_sequences.append(t1_sequence)
        return created_blocks, created_ensembles, created_sequences


    def generate_laser_mw_gate_on(self, name='laser_mw_gate_on', length=3.0e-6):
            """Generates a laser and microwave pulse block ensemble.
    
            Parameters
            ----------
            name : str
                Name of the PulseBlockEnsemble to be generated.
            length : float
                Laser and microwave pulse duration in seconds.
    
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
    
            # create the laser_mw element
            laser_mw_element = self._get_mw_laser_gate_element(length=length,
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

    def generate_bd_cw_odmr(self, name='cw_odmr', freq_start=2.8e9, freq_stop=3e9, num_of_points=10, mw_amp=0.2, mw_length=1e-6, test_time=1e-3,
                            alternating=False, repolarize_time=30e-6, repolarize_reference_only=False):
            """
            CW ODMR sequence for combined AWG + PulseBlaster setup.

            With alternating=True, every frequency point is immediately followed by a
            reference point that is identical (laser + gate for mw_length, gate delay, wait)
            except that no microwave is played. The reference is the same for every
            frequency, so a single shared waveform is used for it.

            In alternating mode every point (normal and reference) starts with a
            laser-only repolarization pulse of repolarize_time (gate off). Without it the
            reference readout would start from the spin state the preceding microwave point
            left behind (it survives the dark gate delay / wait), so the reference would show
            the ODMR dip too. The repolarization is one short shared laser-only waveform looped
            via sequence repetitions (plays of about 3 us), so long repolarization
            times cost no extra waveform memory.

            Parameters
            ----------
            name : str
                Name of the PulseSequence to be generated.
            freq_start : float
                Minimum frequency in Hz.
            freq_stop : float
                Maximum frequency in Hz.
            num_of_points : int
                Number of logarithmically spaced tau points.
            mw_amp : float
                Amplitude of the microwave pulse.
            mw_length : float
                Length of the microwave pulse in seconds.
            alternating : bool
                If True, each frequency point is followed by a microwave-off reference point
                (trace 2; use the "Delta" alternative plot for trace 1 - trace 2).
            repolarize_time : float
                Alternating mode only: laser-only (ungated) pulse before every point, s.
                0 disables it.
            repolarize_reference_only : bool
                If True, repolarize only before the reference points. A normal point always
                follows a reference readout, so it already starts repolarized; this roughly
                halves the repolarization overhead.

            Returns
            -------
            created_blocks : list
            created_ensembles : list
            created_sequences : list
            """
            created_blocks    = list()
            created_ensembles = list()
            created_sequences = list()
    
            # ── Frequency array (linearly spaced) ───────────────────────────────────
            freq_array = np.linspace(freq_start, freq_stop, num_of_points)
            repolarize = alternating and repolarize_time > 0

            # =========================================================================
            # BLOCK AND ENSEMBLE CREATION
            # =========================================================================
    
            # ── 1. Trigger ensemble (sequence step 1, TWAIT=ON set by interfuse) ─────
            sync_element = self._get_sync_element()
    
            trigger_block = PulseBlock(name='{0}_trigger'.format(name))
            trigger_block.append(sync_element)
            created_blocks.append(trigger_block)
    
            trigger_ensemble = PulseBlockEnsemble(
                name='{0}_trigger'.format(name),
                rotating_frame=False
            )
            trigger_ensemble.append((trigger_block.name, 0))
            created_ensembles.append(trigger_ensemble)

            # ── 2. MW and Readout ensembles ───────────────────────────────────────────────────
            cw_odmr_blocks = dict()
            cw_odmr_ensembles = dict()
            for kk, freq in enumerate(freq_array):             
                laser_mw_gate_element = self._get_mw_laser_gate_element(length=mw_length,
                                                                        increment=0,
                                                                        amp=mw_amp,
                                                                        freq=freq,
                                                                        phase=0)
                delay_element   = self._get_delay_gate_element()
                waiting_element = self._get_idle_element(length=self.wait_time, increment=0)
        
                cw_odmr_blocks[kk] = PulseBlock(name='freq_{0}'.format(kk))
                cw_odmr_blocks[kk].append(laser_mw_gate_element)
                cw_odmr_blocks[kk].append(delay_element)
                cw_odmr_blocks[kk].append(waiting_element)
                created_blocks.append(cw_odmr_blocks[kk])
    
                cw_odmr_ensembles[kk] = PulseBlockEnsemble(
                    name='freq_{0}'.format(kk),
                    rotating_frame=False
                )
                cw_odmr_ensembles[kk].append((cw_odmr_blocks[kk].name, 0))
                created_ensembles.append(cw_odmr_ensembles[kk])

            # ── 2b. Shared microwave-off reference point (alternating only) ─────────
            ref_ensemble = None
            if alternating:
                ref_block = PulseBlock(name='{0}_ref'.format(name))
                ref_block.append(self._get_laser_gate_element(length=mw_length, increment=0))
                ref_block.append(self._get_delay_gate_element())
                ref_block.append(self._get_idle_element(length=self.wait_time, increment=0))
                created_blocks.append(ref_block)

                ref_ensemble = PulseBlockEnsemble(name=ref_block.name, rotating_frame=False)
                ref_ensemble.append((ref_block.name, 0))
                created_ensembles.append(ref_ensemble)

            # ── 2c. Shared, looped laser-only repolarization (alternating only) ─────
            repolarize_ensemble, repolarize_reps = None, 0
            if repolarize:
                repolarize_ensemble, repolarize_reps, _ = self._build_looped_repolarization(
                    name, repolarize_time, always_on_channel=None, pulser_channel=None,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

            test_block = PulseBlock(name='test')
            test_element = self._get_idle_element(length=test_time, increment=0)
            test_block.append(test_element)
            created_blocks.append(test_block)

            test_ensemble = PulseBlockEnsemble(
                                name='test',
                                rotating_frame=False
                            )
            test_ensemble.append((test_block.name, 0))
            created_ensembles.append(test_ensemble)
            
            # =========================================================================
            # SEQUENCE CONSTRUCTION
            # =========================================================================
    
            cw_odmr_sequence = PulseSequence(name=name, rotating_frame=False)
                
            # Step 1: trigger — TWAIT=ON is forced on this step by the interfuse's
            # write_sequence() method, making it equivalent to TRIG mode in waveform mode.
            cw_odmr_sequence.append(trigger_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0
    
            # Steps 2..2N+1: alternating tau and readout
            for kk, freq in enumerate(freq_array):
                if repolarize_ensemble is not None and not repolarize_reference_only:
                    cw_odmr_sequence.append(repolarize_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = repolarize_reps
                cw_odmr_sequence.append(cw_odmr_ensembles[kk].name)
                cw_odmr_sequence[-1].repetitions = 0
                if ref_ensemble is not None:
                    if repolarize_ensemble is not None:
                        cw_odmr_sequence.append(repolarize_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = repolarize_reps
                    cw_odmr_sequence.append(ref_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

            cw_odmr_sequence.append(test_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

            # After last readout: return to trigger step and wait for next PB trigger
            cw_odmr_sequence[-1].go_to = 1
    
            # ── Finalise ──────────────────────────────────────────────────────────────
            cw_odmr_sequence.refresh_parameters()
    
            cw_odmr_sequence.measurement_information['alternating']         = alternating
            cw_odmr_sequence.measurement_information['laser_ignore_list']   = list()
            cw_odmr_sequence.measurement_information['controlled_variable'] = freq_array
            cw_odmr_sequence.measurement_information['units']               = ('Hz', '')
            cw_odmr_sequence.measurement_information['labels']              = (
                'Frequency', 'Signal'
            )
            cw_odmr_sequence.measurement_information['number_of_lasers']    = (
                2 * len(freq_array) if alternating else len(freq_array))
            cw_odmr_sequence.measurement_information['counting_length']     = (mw_length + delay_element.init_length_s)
    
            created_sequences.append(cw_odmr_sequence)
            return created_blocks, created_ensembles, created_sequences

    def generate_bd_cw_odmr_compact(self, name='cw_odmr_compact', freq_start=2.8e9, freq_stop=3e9,
                                    num_of_points=10, mw_amp=0.2, mw_length=10e-6,
                                    chunk_length=0.0, test_time=1e-3, test_chunk_length=10e-6,
                                    alternating=False, repolarize_time=30e-6,
                                    repolarize_reference_only=False):
        """
        Memory-efficient CW ODMR sequence for the combined AWG + PulseBlaster
        setup: same measurement as generate_bd_cw_odmr, but each frequency
        point stores only ONE short microwave chunk (`chunk_length`) on the
        AWG, which the sequence repeats to give `mw_length` of continuous
        microwave + laser + gate. The gate delay / wait time and the final
        `test` step are shared waveforms instead of being baked into every
        point. AWG memory per point drops from (mw_length + delay +
        wait_time) to chunk_length -- e.g. 252k -> 12k samples at 12 GS/s,
        so ~1000+ points fit easily instead of ~257.

        Phase continuity: a repeated chunk only joins seamlessly if it holds
        a WHOLE number of sine periods. The chunk has n samples (a multiple
        of the pulse generator's length granularity -- for the AWG +
        PulseBlaster interfuse that is the PB's minimum instruction in AWG
        samples, e.g. 180), and every frequency is snapped to the nearest
        multiple of the grid sample_rate / n. The snapped frequencies are
        what is stored as the measurement's x-axis.

        chunk_length=0 (default) picks n automatically: the shortest chunk
        whose grid divides the frequency step exactly, so the requested
        frequencies are reproduced exactly (e.g. 1 MHz steps at 12 GS/s with
        granularity 180 -> n = lcm(12000, 180) = 36000 samples = 3 us, grid
        333 kHz). If that would not fit in waveform memory, the shortest
        chunk with grid <= frequency step is used instead (points stay
        distinct but are snapped by up to half a grid step; warned).

        Per-point readout time = (number of chunk plays) x chunk duration,
        i.e. mw_length rounded to a whole number of chunks.

        Sequence: trigger | (chunk_k x plays, tail) for every k | test (looped)
        -- 2 * num_of_points + 2 steps (AWG7k limit 8000 -> <= 3999 points).

        alternating=True: every point is followed by a microwave-off reference
        (ref x plays, tail), identical except that the chunk holds only laser +
        gate. A single shared reference chunk is used for all points. Every
        point (normal and reference) is preceded by a shared laser-only
        repolarization step of repolarize_time (gate off), so the reference
        does not start from the spin state left by the preceding microwave point.
        -- 6 * num_of_points + 2 steps (AWG7k limit 8000 -> <= 1333 points),
        5 * num_of_points + 2 with repolarize_reference_only (<= 1599 points),
        4 * num_of_points + 2 with repolarize_time=0.

        Parameters
        ----------
        name : str
        freq_start, freq_stop : float
            Frequency range, Hz.
        num_of_points : int
        mw_amp : float
            Microwave amplitude.
        mw_length : float
            Microwave/readout time per point, s (rounded to whole chunks).
        chunk_length : float
            Duration of the stored, repeated microwave chunk, s. 0 (default):
            automatic, see above. If > 0, it is used as the MINIMUM chunk
            length (rounded up to the exact-grid / granularity multiple as
            above).
        test_time : float
            Duration of the final idle step, s (realized by looping a
            `test_chunk_length` idle waveform).
        test_chunk_length : float
            Base length of the looped final idle waveform, s.
        alternating : bool
            If True, each frequency point is followed by a microwave-off reference point
            (trace 2; use the "Delta" alternative plot for trace 1 - trace 2).
        repolarize_time : float
            Alternating mode only: laser-only (ungated) pulse before every point, s. Played as a
            shared ~3 us laser-only waveform looped via repetitions,
            so it costs no extra waveform memory. 0 disables it.
        repolarize_reference_only : bool
            If True, repolarize only before the reference points. A normal point always follows
            a reference readout, so it already starts repolarized; this roughly halves the
            repolarization overhead (5 instead of 6 sequence steps per point).
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        sample_rate = self.pulse_generator_settings['sample_rate']
        constraints = self.pulse_generator_constraints
        min_samples = int(constraints.waveform_length.min)
        step_samples = int(constraints.waveform_length.step)
        max_steps = int(constraints.sequence_steps.max)

        repolarize = alternating and repolarize_time > 0
        if not alternating:
            steps_per_point = 2
        elif not repolarize:
            steps_per_point = 4
        else:
            steps_per_point = 5 if repolarize_reference_only else 6
        n_steps = steps_per_point * num_of_points + 2
        if n_steps > max_steps:
            raise ValueError('generate_bd_cw_odmr_compact: {0} points need {1} sequence steps, '
                             'more than the AWG maximum of {2}.'.format(
                                 num_of_points, n_steps, max_steps))
        # number of stored chunks: one per frequency, plus the shared reference
        n_chunks_stored = num_of_points + 1 if alternating else num_of_points

        # ── Chunk length (samples) ───────────────────────────────────────────
        # Multiple of the length granularity, and if possible such that the
        # frequency grid sample_rate / n divides the frequency step exactly.
        base = step_samples
        n_floor = int(np.ceil(min_samples / base)) * base
        if chunk_length > 0:
            n_floor = max(n_floor, int(np.ceil(chunk_length * sample_rate / base - 1e-9)) * base)
        memory_budget = 0.9 * float(constraints.waveform_length.max)  # leave room for tail etc.
        freq_step = (freq_stop - freq_start) / (num_of_points - 1) if num_of_points > 1 else None
        n_chunk = None
        if freq_step:
            q = sample_rate / abs(freq_step)          # samples per period of the step frequency
            if abs(q - round(q)) < 1e-6 * q:
                n_exact = int(np.lcm(base, int(round(q))))
                n_try = n_exact * int(np.ceil(n_floor / n_exact))
                if n_try * n_chunks_stored <= memory_budget:
                    n_chunk = n_try
            if n_chunk is None:
                n_chunk = max(n_floor, int(np.ceil(q / base - 1e-9)) * base)
                self.log.warning(
                    'generate_bd_cw_odmr_compact: no chunk length that reproduces the {0:.4e} Hz '
                    'frequency step exactly fits in memory; using {1} samples (grid {2:.4e} Hz), so '
                    'frequencies are snapped by up to {3:.3e} Hz.'.format(
                        freq_step, n_chunk, sample_rate / n_chunk, 0.5 * sample_rate / n_chunk))
        else:
            n_chunk = n_floor
        if n_chunk * n_chunks_stored > float(constraints.waveform_length.max):
            raise ValueError(
                'generate_bd_cw_odmr_compact: {0} chunks x {1} samples per chunk = {2:.3e} samples '
                'exceeds the waveform memory ({3:.3e}). Use fewer points or a coarser frequency '
                'step.'.format(n_chunks_stored, n_chunk, n_chunks_stored * n_chunk,
                               float(constraints.waveform_length.max)))
        chunk_s = n_chunk / sample_rate
        grid = sample_rate / n_chunk

        # ── Frequencies snapped to whole periods per chunk ───────────────────
        requested = np.linspace(freq_start, freq_stop, num_of_points)
        freq_array = np.round(requested / grid) * grid
        if num_of_points > 1 and np.any(np.diff(freq_array) == 0):
            self.log.warning(
                'generate_bd_cw_odmr_compact: the frequency step ({0:.3e} Hz) is finer than '
                'the {1:.3e} Hz grid of a {2:.3e} s chunk, so some points coincide. Use '
                'chunk_length >= {3:.3e} s for distinct points.'.format(
                    (freq_stop - freq_start) / (num_of_points - 1), grid, chunk_s,
                    1.0 / abs((freq_stop - freq_start) / (num_of_points - 1))))

        plays = max(1, int(round(mw_length / chunk_s)))
        if plays > self._MAX_SEQUENCE_LOOP_COUNT:
            raise ValueError('generate_bd_cw_odmr_compact: mw_length / chunk_length = {0} plays '
                             'exceeds the AWG loop limit of {1}; increase chunk_length.'.format(
                                 plays, self._MAX_SEQUENCE_LOOP_COUNT))
        readout_s = plays * chunk_s
        self.log.info(
            'generate_bd_cw_odmr_compact: chunk {0} samples ({1:.3e} s), frequency grid '
            '{2:.3e} Hz, {3} plays per point -> {4:.3e} s readout; AWG memory ~{5:.2e} '
            'samples for the frequency chunks.'.format(
                n_chunk, chunk_s, grid, plays, readout_s, n_chunk * n_chunks_stored))

        # ── 1. Trigger (sequence step 1, TWAIT=ON set by the interfuse) ──────
        trigger_block = PulseBlock(name='{0}_trigger'.format(name))
        trigger_block.append(self._get_sync_element())
        self._pad_ensemble_to_granularity(trigger_block, on=False, always_on_channel=None,
                                          pulser_channel=None)
        created_blocks.append(trigger_block)
        trigger_ensemble = PulseBlockEnsemble(name='{0}_trigger'.format(name), rotating_frame=False)
        trigger_ensemble.append((trigger_block.name, 0))
        created_ensembles.append(trigger_ensemble)

        # ── 2. One short microwave+laser+gate chunk per frequency ───────────
        chunk_ensembles = list()
        for kk, freq in enumerate(freq_array):
            chunk_block = PulseBlock(name='{0}_f{1}'.format(name, kk))
            chunk_block.append(self._get_mw_laser_gate_element(
                length=chunk_s, increment=0, amp=mw_amp, freq=freq, phase=0))
            pad_s, _ = self._pad_ensemble_to_granularity(
                chunk_block, on=False, always_on_channel=None, pulser_channel=None)
            if pad_s > 0:
                # Would break the laser/gate/phase continuity between plays.
                raise RuntimeError('generate_bd_cw_odmr_compact: chunk of {0} samples needed '
                                   'padding ({1:.3e} s); expected an exact length.'.format(
                                       n_chunk, pad_s))
            created_blocks.append(chunk_block)
            chunk_ensemble = PulseBlockEnsemble(name=chunk_block.name, rotating_frame=False)
            chunk_ensemble.append((chunk_block.name, 0))
            created_ensembles.append(chunk_ensemble)
            chunk_ensembles.append(chunk_ensemble)

        # ── 2b. Shared microwave-off reference chunk (alternating only) ──────
        ref_ensemble = None
        if alternating:
            ref_block = PulseBlock(name='{0}_ref'.format(name))
            ref_block.append(self._get_laser_gate_element(length=chunk_s, increment=0))
            pad_s, _ = self._pad_ensemble_to_granularity(
                ref_block, on=False, always_on_channel=None, pulser_channel=None)
            if pad_s > 0:
                raise RuntimeError('generate_bd_cw_odmr_compact: reference chunk of {0} samples '
                                   'needed padding ({1:.3e} s); expected an exact length.'.format(
                                       n_chunk, pad_s))
            created_blocks.append(ref_block)
            ref_ensemble = PulseBlockEnsemble(name=ref_block.name, rotating_frame=False)
            ref_ensemble.append((ref_block.name, 0))
            created_ensembles.append(ref_ensemble)

        # ── 2c. Shared, looped laser-only repolarization step (alternating only) ─
        repolarize_ensemble, repolarize_reps = None, 0
        if repolarize:
            repolarize_ensemble, repolarize_reps, _ = self._build_looped_repolarization(
                name, repolarize_time, always_on_channel=None, pulser_channel=None,
                created_blocks=created_blocks, created_ensembles=created_ensembles)

        # ── 3. Shared tail: gate delay + wait time (same for every point) ────
        delay_element = self._get_delay_gate_element()
        tail_block = PulseBlock(name='{0}_tail'.format(name))
        tail_block.append(delay_element)
        tail_block.append(self._get_idle_element(length=self.wait_time, increment=0))
        self._pad_ensemble_to_granularity(tail_block, on=False, always_on_channel=None,
                                          pulser_channel=None)
        created_blocks.append(tail_block)
        tail_ensemble = PulseBlockEnsemble(name=tail_block.name, rotating_frame=False)
        tail_ensemble.append((tail_block.name, 0))
        created_ensembles.append(tail_ensemble)

        # ── 4. Final idle 'test' step, looped from a short idle waveform ─────
        test_block = PulseBlock(name='{0}_test'.format(name))
        test_block.append(self._get_idle_element(length=min(test_time, test_chunk_length),
                                                  increment=0))
        _, test_base_s = self._pad_ensemble_to_granularity(
            test_block, on=False, always_on_channel=None, pulser_channel=None)
        created_blocks.append(test_block)
        test_ensemble = PulseBlockEnsemble(name=test_block.name, rotating_frame=False)
        test_ensemble.append((test_block.name, 0))
        created_ensembles.append(test_ensemble)
        test_plays = min(max(1, int(np.ceil(test_time / test_base_s))),
                         self._MAX_SEQUENCE_LOOP_COUNT)

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================
        sequence = PulseSequence(name=name, rotating_frame=False)
        sequence.append(trigger_ensemble.name)
        sequence[-1].repetitions = 0
        for chunk_ensemble in chunk_ensembles:
            if repolarize_ensemble is not None and not repolarize_reference_only:
                sequence.append(repolarize_ensemble.name)
                sequence[-1].repetitions = repolarize_reps
            sequence.append(chunk_ensemble.name)
            sequence[-1].repetitions = plays - 1      # total plays = repetitions + 1
            sequence.append(tail_ensemble.name)
            sequence[-1].repetitions = 0
            if ref_ensemble is not None:
                if repolarize_ensemble is not None:
                    sequence.append(repolarize_ensemble.name)
                    sequence[-1].repetitions = repolarize_reps
                sequence.append(ref_ensemble.name)
                sequence[-1].repetitions = plays - 1
                sequence.append(tail_ensemble.name)
                sequence[-1].repetitions = 0
        sequence.append(test_ensemble.name)
        sequence[-1].repetitions = test_plays - 1
        sequence[-1].go_to = 1                        # back to the trigger step
        sequence.refresh_parameters()

        sequence.measurement_information['alternating'] = alternating
        sequence.measurement_information['laser_ignore_list'] = list()
        sequence.measurement_information['controlled_variable'] = freq_array
        sequence.measurement_information['units'] = ('Hz', '')
        sequence.measurement_information['labels'] = ('Frequency', 'Signal')
        sequence.measurement_information['number_of_lasers'] = (
            2 * len(freq_array) if alternating else len(freq_array))
        sequence.measurement_information['counting_length'] = readout_s + delay_element.init_length_s

        created_sequences.append(sequence)
        return created_blocks, created_ensembles, created_sequences
