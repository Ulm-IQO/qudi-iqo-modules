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
        """Set a single channel (digital or analog) HIGH for the entire duration of `element`."""
        if channel is None:
            return
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

    def generate_bd_cw_odmr_gradient(self, name='cw_odmr_gradient', freq_start=2.8e9, freq_stop=3e9,
                                    num_of_points=10, mw_amp=0.2, mw_length=1e-6,
                                    always_on_ch='d_ch15', gradient_mode=0, gradient_mode_ch='d_ch6',
                                    pulser_ch='d_ch3', duty_cycle=0.2, rising_time=100e-9, falling_time=100e-9,
                                    laser_warmup_time=100e-6, laser_cooldown=10e-6):
        """
        CW ODMR sequence for combined AWG + PulseBlaster setup, extended with:

        - always_on_ch  : channel held HIGH for the ENTIRE sequence
                            (trigger, every frequency point, and the duty-cycle
                            correction chunk).
        - gradient_mode  : 0 -> gradient_mode_ch stays LOW everywhere (unused).
                            1 -> gradient_mode_ch stays HIGH everywhere, treated
                                identically to always_on_ch.
        - pulser_ch      : channel that must be HIGH while the odmr mw+laser
                            readout is happening, bracketed by rising_time/
                            falling_time so it is at steady state throughout
                            the actual measurement window.
        - duty_cycle     : target fraction of the TOTAL sequence duration for
                            which pulser_ch is HIGH. A single small base idle
                            element (ON or OFF, whichever is needed) is created
                            once and looped via sequence-step `repetitions` at
                            the very end of the sequence to hit this value as
                            closely as possible, WITHOUT uploading one large
                            unique correction waveform. If the required loop
                            count would exceed the AWG's per-step loop-count
                            limit (_MAX_SEQUENCE_LOOP_COUNT), the base
                            element's length is automatically increased instead
                            of exceeding that limit.
        - laser_warmup_time : length (s) of an extra laser-ON pulse inserted
                            ONCE, immediately after the trigger step and before
                            the first real frequency point, but ONLY if a
                            duty-cycle correction block is being appended at
                            the end of the sequence (i.e. only if there is a
                            long idle stretch during which the laser goes dark
                            every loop). This pulse fires ONLY the physical
                            laser channel (self.laser_channel) -- it never
                            toggles the detector gate channel, so it is never
                            counted as a data point.
        - laser_cooldown : length (s) of an extra idle period (pulser OFF,
                            laser implicitly OFF) inserted immediately after
                            the laser_warmup pulse and before the first real
                            frequency point, but ONLY if a duty-cycle
                            correction block (and therefore a warm-up pulse)
                            exists. Gives the laser time to settle back to its
                            normal OFF state after the warm-up pulse, rather
                            than transitioning directly into the first point's
                            rising ramp.

        Per-frequency-point block structure (pulser state noted in brackets):

            rising_element    [ON]   -- rising_time   : ramp pulser up to steady state
            mw_laser_element  [ON]   -- mw_length      : mw + laser readout
            delay_element     [ON]   -- fixed          : detector delay
            waiting_element   [ON]   -- wait_time      : dead time
            falling_element   [OFF]  -- falling_time   : ramp pulser back down

        i.e. pulser_ch turns ON just before the mw+laser measurement (after
        settling for rising_time), stays ON through the measurement/delay/dead
        time, then turns OFF (settling for falling_time) before the next
        point's rising ramp. Consecutive points are therefore self-consistent:
        each one starts and ends in the OFF state.

        Every block (trigger, each frequency point, warm-up, cooldown, and the
        duty-cycle correction base element) is individually padded to the pulse
        generator's minimum waveform length / granularity step via
        _pad_ensemble_to_granularity(), and the duty-cycle math uses the
        resulting MEASURED lengths -- exactly as in the sequence-mode
        generate_bd_pulsed_rabi() -- so that SequenceGeneratorLogic never needs
        to silently insert its own idle_extension block (which would force ALL
        digital channels LOW, breaking always_on_ch/gradient_mode_ch).

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
        # trailing (only) element is OFF for the pulser -> pad matches OFF
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
            point_block.append(rising_element)
            point_block.append(mw_laser_element)
            point_block.append(delay_element)
            point_block.append(waiting_element)
            point_block.append(falling_element)

            # trailing element (falling_element) is OFF -> pad, if any, matches OFF
            pad_length_s, _ = self._pad_ensemble_to_granularity(
                point_block, on=False,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(point_block)

            point_ensemble = PulseBlockEnsemble(name='{0}_freq_{1}'.format(name, kk), rotating_frame=False)
            point_ensemble.append((point_block.name, 0))
            created_ensembles.append(point_ensemble)
            cw_odmr_ensembles[kk] = point_ensemble

            total_on_s  += rising_time + mw_length + delay_length_s + self.wait_time
            total_off_s += falling_time + pad_length_s

        # =========================================================================
        # Duty-cycle correction -- small base element, looped via `repetitions`
        # in the sequence step (NOT one giant unique waveform), to avoid
        # exhausting AWG waveform memory for long corrections, and automatically
        # re-derived if the naive loop count would exceed the AWG's per-step
        # loop-count limit.
        # =========================================================================
        total_all_s = total_on_s + total_off_s
        p_on = total_on_s / total_all_s if total_all_s > 0 else 0.0

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
        # else: duty_cycle already matches p_on -> no correction needed

        correction_ensemble = None
        correction_reps = 0
        if correction_on is not None:
            preferred_base_length = self.wait_time if correction_on else falling_time
            correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                name=name, correction_length=correction_length, correction_on=correction_on,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch,
                preferred_base_length=preferred_base_length,
                created_blocks=created_blocks, created_ensembles=created_ensembles)

        # =========================================================================
        # Laser warm-up + cooldown -- ONLY if a duty-cycle correction exists
        # =========================================================================
        warmup_ensemble = None
        cooldown_ensemble = None
        if correction_ensemble is not None:
            warmup_element = self._get_pulser_on_laser_only_element(
                length=laser_warmup_time, increment=0,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)

            warmup_block = PulseBlock(name=name + '_laser_warmup')
            warmup_block.append(warmup_element)
            self._pad_ensemble_to_granularity(
                warmup_block, on=True,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch,
                extra_high_channels=self.laser_channel)
            created_blocks.append(warmup_block)

            warmup_ensemble = PulseBlockEnsemble(name=name + '_laser_warmup', rotating_frame=False)
            warmup_ensemble.append((warmup_block.name, 0))
            created_ensembles.append(warmup_ensemble)

            # Cooldown: plain OFF idle -- laser_channel is not referenced, so it
            # simply defaults low, same as falling_element/trigger_block. No
            # extra_high_channels needed here.
            cooldown_element = self._get_pulser_off_idle_element(
                length=laser_cooldown, increment=0, always_on_channel=always_on_channels)

            cooldown_block = PulseBlock(name=name + '_laser_cooldown')
            cooldown_block.append(cooldown_element)
            self._pad_ensemble_to_granularity(
                cooldown_block, on=False,
                always_on_channel=always_on_channels, pulser_channel=pulser_ch)
            created_blocks.append(cooldown_block)

            cooldown_ensemble = PulseBlockEnsemble(name=name + '_laser_cooldown', rotating_frame=False)
            cooldown_ensemble.append((cooldown_block.name, 0))
            created_ensembles.append(cooldown_ensemble)

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

        for kk, freq in enumerate(freq_array):
            cw_odmr_sequence.append(cw_odmr_ensembles[kk].name)
            cw_odmr_sequence[-1].repetitions = 0

        if correction_ensemble is not None:
            cw_odmr_sequence.append(correction_ensemble.name)
            cw_odmr_sequence[-1].repetitions = correction_reps

        cw_odmr_sequence[-1].go_to = 1

        cw_odmr_sequence.refresh_parameters()

        cw_odmr_sequence.measurement_information['alternating']         = False
        cw_odmr_sequence.measurement_information['laser_ignore_list']   = list()
        cw_odmr_sequence.measurement_information['controlled_variable'] = freq_array
        cw_odmr_sequence.measurement_information['units']               = ('Hz', '')
        cw_odmr_sequence.measurement_information['labels']              = ('Frequency', 'Signal')
        cw_odmr_sequence.measurement_information['number_of_lasers']    = len(freq_array)
        cw_odmr_sequence.measurement_information['counting_length']     = (mw_length + delay_length_s)

        created_sequences.append(cw_odmr_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_bd_pulsed_rabi(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9,
                        num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                        duty_cycle=0.2, rising_time=50e-6, falling_time=50e-6,
                        laser_warmup_time=100e-6, laser_cooldown=10e-6):
        """
        Sequence-mode Rabi. Every block that becomes its own AWG waveform is
        explicitly padded to satisfy BOTH the AWG's minimum waveform length
        AND the combined AWG/PB granularity step (constraints.waveform_length
        .min / .step), measured precisely via analyze_block_ensemble(). This
        guarantees SequenceGeneratorLogic never needs to silently append its
        own idle_extension block afterward -- which would force ALL digital
        channels LOW (breaking any "always on" channel) and whose length
        can't be predicted ahead of time for duty-cycle accounting.

        The duty-cycle correction is realized as a single small base idle
        element looped via sequence-step `repetitions`; if the required loop
        count would exceed the AWG's per-step loop-count limit
        (_MAX_SEQUENCE_LOOP_COUNT), the base element's length is automatically
        increased instead (see _build_duty_cycle_correction).

        laser_warmup_time : length (s) of an extra laser-ON pulse inserted ONCE,
            immediately after the trigger step and before the first tau point,
            but ONLY if a duty-cycle correction block is being appended. Fires
            ONLY the physical laser channel (never the gate channel), so it is
            never counted as a data point.
        laser_cooldown : length (s) of an extra idle period (pulser OFF, laser
            implicitly OFF) inserted immediately after laser_warmup_time and
            before the first tau point, but ONLY if a duty-cycle correction
            block exists.
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        tau_array = tau_start + np.arange(num_of_points) * tau_step

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
        # ASSUMPTION: trigger channel(s) are idle/LOW again by the end of the
        # sync pulse -- pad matches "off". Verify against _get_sync_element().
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

        # =========================================================================
        # Duty-cycle math -- using MEASURED (post-padding) lengths throughout
        # =========================================================================
        total_on  = num_of_points * (rising_length_s + readout_length_s)
        total_off = num_of_points * falling_length_s + mw_length_total_s + trigger_length_s

        total_all = total_on + total_off
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

        # ── 6. Optional duty-cycle correction, padded, reps computed from its
        # ACTUAL (post-pad) base length, and re-derived if the naive loop count
        # would exceed the AWG's per-step loop-count limit ───────────────────
        correction_ensemble = None
        correction_reps = 0
        if correction_on is not None:
            preferred_base_length = self.wait_time if correction_on else falling_time
            correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                name=name, correction_length=correction_length, correction_on=correction_on,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                preferred_base_length=preferred_base_length,
                created_blocks=created_blocks, created_ensembles=created_ensembles)

        # =========================================================================
        # Laser warm-up + cooldown -- only if a duty-cycle correction exists
        # =========================================================================
        warmup_ensemble = None
        cooldown_ensemble = None
        if correction_ensemble is not None:
            warmup_element = self._get_pulser_on_laser_only_element(
                length=laser_warmup_time, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)

            warmup_block = PulseBlock(name=name + '_laser_warmup')
            warmup_block.append(warmup_element)
            self._pad_ensemble_to_granularity(
                warmup_block, on=True,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                extra_high_channels=self.laser_channel)
            created_blocks.append(warmup_block)

            warmup_ensemble = PulseBlockEnsemble(name=name + '_laser_warmup', rotating_frame=False)
            warmup_ensemble.append((warmup_block.name, 0))
            created_ensembles.append(warmup_ensemble)

            cooldown_element = self._get_pulser_off_idle_element(
                length=laser_cooldown, increment=0, always_on_channel=always_on_channel)

            cooldown_block = PulseBlock(name=name + '_laser_cooldown')
            cooldown_block.append(cooldown_element)
            self._pad_ensemble_to_granularity(
                cooldown_block, on=False,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(cooldown_block)

            cooldown_ensemble = PulseBlockEnsemble(name=name + '_laser_cooldown', rotating_frame=False)
            cooldown_ensemble.append((cooldown_block.name, 0))
            created_ensembles.append(cooldown_ensemble)

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

        if correction_ensemble is not None:
            rabi_sequence.append(correction_ensemble.name)
            rabi_sequence[-1].repetitions = correction_reps

        rabi_sequence[-1].go_to = 1

        rabi_sequence.refresh_parameters()

        rabi_sequence.measurement_information['alternating'] = False
        rabi_sequence.measurement_information['laser_ignore_list'] = list()
        rabi_sequence.measurement_information['controlled_variable'] = tau_array
        rabi_sequence.measurement_information['units'] = ('s', '')
        rabi_sequence.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        rabi_sequence.measurement_information['number_of_lasers'] = num_of_points
        rabi_sequence.measurement_information['counting_length'] = (self.laser_length + delay_element.init_length_s)

        created_sequences.append(rabi_sequence)
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

    def generate_bd_cw_odmr(self, name='cw_odmr', freq_start=2.8e9, freq_stop=3e9, num_of_points=10, mw_amp=0.2, mw_length=1e-6, test_time=1e-3):
            """
            CW ODMR sequence for combined AWG + PulseBlaster setup.
    
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
                # Tau: free evolution for k * tau_start total
                cw_odmr_sequence.append(cw_odmr_ensembles[kk].name)
                cw_odmr_sequence[-1].repetitions = 0

            cw_odmr_sequence.append(test_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

            # After last readout: return to trigger step and wait for next PB trigger
            cw_odmr_sequence[-1].go_to = 1
    
            # ── Finalise ──────────────────────────────────────────────────────────────
            cw_odmr_sequence.refresh_parameters()
    
            cw_odmr_sequence.measurement_information['alternating']         = False
            cw_odmr_sequence.measurement_information['laser_ignore_list']   = list()
            cw_odmr_sequence.measurement_information['controlled_variable'] = freq_array
            cw_odmr_sequence.measurement_information['units']               = ('Hz', '')
            cw_odmr_sequence.measurement_information['labels']              = (
                'Frequency', 'Signal'
            )
            cw_odmr_sequence.measurement_information['number_of_lasers']    = len(freq_array)
            cw_odmr_sequence.measurement_information['counting_length']     = (mw_length + delay_element.init_length_s)
    
            created_sequences.append(cw_odmr_sequence)
            return created_blocks, created_ensembles, created_sequences