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
from numpy.ma import cos
from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockEnsemble, PulseSequence
from qudi.logic.pulsed.sampling_functions import SamplingFunctions, PulseEnvelope, PulseEnvelopeType
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

    # Fallback safety factor applied to the AWG Nyquist limit (sample_rate/2) when the
    # generation parameter "microwave_iq_max_frequency" has not been set explicitly. This is
    # only a generic mathematical upper bound - your mixer's real analog IF bandwidth is likely
    # narrower. Set "microwave_iq_max_frequency" (in Hz) via the generation parameters to reflect
    # your actual hardware limit once known.
    _IQ_NYQUIST_SAFETY_FACTOR = 0.8

    # Default name of the microwave hardware/interfuse module to query for the live LO frequency,
    # matching this setup's qudi config (hardware.mw_always_on). Can be overridden per-session via
    # the "microwave_hardware_module_name" generation parameter if ever needed (e.g. to point at
    # 'sg384' directly instead).
    _DEFAULT_MICROWAVE_MODULE_NAME = 'mw_always_on'

    # Minimum digital pulse duration enforced by the PulseBlaster's 100 MHz clock. MW elements
    # shorter than this (when routed through a digital MW switch channel) must be preceded by
    # idle "free evolution" padding so that padding + MW pulse together reach at least this
    # duration.
    _MW_ELEMENT_MIN_LENGTH = 60.0e-9

    # AWG hardware constraint: a given waveform can only be looped consecutively via a single
    # sequence step fewer than 2**16 times.
    _MAX_SEQUENCE_LOOP_COUNT = 2**16 - 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    ################################################################################################
    #             Direct microwave hardware access (bypasses PulsedMeasurementLogic)               #
    ################################################################################################

    def _get_microwave_module_instance(self):
        """
        Looks up the microwave hardware/interfuse module instance directly via Qudi's module
        manager, using the module name given by the generation parameter
        "microwave_hardware_module_name" if set, otherwise falling back to
        _DEFAULT_MICROWAVE_MODULE_NAME ("mw_always_on", matching this setup's qudi config).

        This substitutes for a Connector, which is not usable here since this generator plugin is
        not itself an activated qudi Base module and therefore has no Connector of its own. It
        borrows the live reference to the SAME already-connected hardware instance that
        PulsedMeasurementLogic itself uses - it does NOT open a second, competing connection to
        the physical signal generator.

        @return object or None: the live module instance, or None if unavailable.
        """
        module_name = getattr(self, 'microwave_hardware_module_name', None) \
            or self._DEFAULT_MICROWAVE_MODULE_NAME

        try:
            from qudi.core.application import Qudi
            instance = Qudi.instance().module_manager[module_name].instance
        except Exception as exc:
            self.log.error(
                f'Could not look up module "{module_name}" via the qudi module manager: {exc}\n'
                f'Check that this name matches a module in your qudi config exactly.'
            )
            return None

        if instance is None:
            self.log.error(
                f'Module "{module_name}" was found but is not currently activated.'
            )
            return None

        return instance

    def _get_live_microwave_state(self):
        """
        Reads the live frequency, power and constraints directly off the microwave hardware
        module, bypassing PulsedMeasurementLogic entirely.

        @return dict or None: {'frequency': float (Hz), 'power': float (dBm),
                               'constraints': MicrowaveConstraints} or None if unavailable.
        """
        instance = self._get_microwave_module_instance()
        if instance is None:
            return None

        try:
            return {
                'frequency': float(instance.cw_frequency),
                'power': float(instance.cw_power),
                'constraints': instance.constraints,
            }
        except Exception as exc:
            self.log.error(f'Failed to read cw_frequency/cw_power/constraints from hardware: {exc}')
            return None

    def _get_iq_modulation_frequency(self, target_frequency=None):
        """
        Computes the baseband I/Q modulation frequency required to shift the external signal
        generator's live CW output frequency to the desired ABSOLUTE output frequency
        "target_frequency".

        f_out = f_LO + sideband_sign * f_IQ    (sideband_sign defaults to +1)

        f_LO is read LIVE, directly off the microwave hardware module (see
        _get_live_microwave_state) - no manual duplicate entry required. If that lookup fails for
        any reason, falls back to f_IQ = 0 Hz with a warning.

        Optional generation parameters:
            microwave_iq_max_frequency     : Maximum usable |f_IQ| of your I/Q mixer/AWG
                                              combination, in Hz. Falls back to a Nyquist-based
                                              estimate from the AWG sample rate if not set.
            microwave_iq_sideband_sign     : +1 (default) if your mixer produces the upper
                                              sideband, -1 if lower sideband.
            microwave_hardware_module_name : overrides which qudi module to query (default:
                                              "mw_always_on").

        Raises a ValueError (logged) if the requested target_frequency is not reachable.

        @param float target_frequency: desired ABSOLUTE output frequency in Hz. Defaults to
                                       self.microwave_frequency if not given.
        @return float: required I/Q baseband modulation frequency in Hz
        """
        if target_frequency is None:
            target_frequency = self.microwave_frequency

        live_state = self._get_live_microwave_state()
        if live_state is None:
            self.log.warning(
                'Could not read the live signal generator frequency from hardware. Falling back '
                'to f_IQ = 0 Hz, i.e. assuming the signal generator is already set to the '
                'requested target frequency.'
            )
            return 0.0

        lo_frequency = live_state['frequency']
        constraints = live_state['constraints']
        self.log.debug(
            f'Live signal generator state: {lo_frequency / 1e9:.6f} GHz, '
            f'{live_state["power"]:.2f} dBm.'
        )

        sideband_sign = getattr(self, 'microwave_iq_sideband_sign', 1)
        if sideband_sign not in (1, -1):
            self.log.warning(
                f'Generation parameter "microwave_iq_sideband_sign" must be +1 or -1, got '
                f'{sideband_sign!r}. Defaulting to +1.'
            )
            sideband_sign = 1

        # Sanity check: is the LO frequency itself within the hardware's tunable range?
        if constraints is not None:
            try:
                is_valid, _ = constraints.frequency_in_range(lo_frequency)
                if not is_valid:
                    f_min, f_max = constraints.frequency_limits
                    err_msg = (
                        f'Signal generator LO frequency {lo_frequency / 1e9:.6f} GHz is outside '
                        f'the hardware\'s tunable range [{f_min / 1e9:.6f}, {f_max / 1e9:.6f}] GHz.'
                    )
                    self.log.error(err_msg)
                    raise ValueError(err_msg)
            except ValueError:
                raise
            except Exception:
                pass

        iq_frequency = sideband_sign * (target_frequency - lo_frequency)

        # Determine the allowed |f_IQ| range
        try:
            iq_max = self.microwave_iq_max_frequency
        except AttributeError:
            iq_max = None
            try:
                sample_rate = self.pulse_generator_settings['sample_rate']
            except (AttributeError, KeyError, TypeError):
                sample_rate = None
            if sample_rate:
                iq_max = self._IQ_NYQUIST_SAFETY_FACTOR * 0.5 * sample_rate
                self.log.warning(
                    'Generation parameter "microwave_iq_max_frequency" is not set. Falling back '
                    f'to a Nyquist-based estimate of +/-{iq_max / 1e6:.3f} MHz (sample_rate = '
                    f'{sample_rate / 1e9:.3f} GS/s). This is likely more generous than your '
                    'actual mixer bandwidth - set "microwave_iq_max_frequency" (in Hz) explicitly '
                    'to reflect your real hardware limit.'
                )
            else:
                self.log.warning(
                    'Could neither determine "microwave_iq_max_frequency" nor the AWG sample '
                    'rate. Skipping range validation of the I/Q modulation frequency.'
                )
                return iq_frequency

        if abs(iq_frequency) > iq_max:
            err_msg = (
                f'Cannot reach requested target frequency {target_frequency / 1e9:.6f} GHz '
                f'with the signal generator currently at {lo_frequency / 1e9:.6f} GHz: required '
                f'I/Q modulation frequency ({iq_frequency / 1e6:.3f} MHz) exceeds the allowed '
                f'range of +/-{iq_max / 1e6:.3f} MHz. Either change the signal generator '
                f'frequency to something closer to your target, or choose a target frequency '
                f'within reach.'
            )
            self.log.error(err_msg)
            raise ValueError(err_msg)

        return iq_frequency

    def _get_dx_mw_element(
            self,
            length,
            increment,
            amp=None,
            freq=None,
            phase=None,
            envelope: PulseEnvelope = PulseEnvelope(PulseEnvelopeType.from_gen_settings),
        ):
            """
            Creates an I/Q-modulated MW pulse PulseBlockElement, up-converted onto a fixed-
            frequency carrier via external I/Q modulation.

            Handles both use cases:
              - Fixed-frequency methods (e.g. Rabi): call without "freq" - defaults to
                self.microwave_frequency, used identically for every element in the sweep.
              - Frequency-swept methods (e.g. CW ODMR): pass the current sweep point explicitly
                as "freq" on every call.

            The physical IQ mixer implements:
                V_RF(t) = I(t)*cos(2*pi*f_LO*t) - Q(t)*sin(2*pi*f_LO*t)
            To produce a single sideband at f_LO + f_IQ with a given amplitude/phase, the
            baseband envelopes fed to the I and Q ports must be in genuine time-domain quadrature:
                I(t) = amp * cos(2*pi*f_IQ*t + phase)
                Q(t) = amp * sin(2*pi*f_IQ*t + phase)
            which correctly reduces to a pure DC phase-shifter (I=amp*cos(phase),
            Q=amp*sin(phase)) when f_IQ = 0.

            f_IQ is computed and validated live against the actual signal generator frequency and
            your I/Q mixer/AWG bandwidth via _get_iq_modulation_frequency().

            @param float length: MW pulse duration in seconds
            @param float increment: MW pulse duration increment in seconds
            @param float amp: MW amplitude in case of analogue MW channel in V
            @param float freq: desired ABSOLUTE output frequency in Hz. Defaults to
                               self.microwave_frequency if not given.
            @param float phase: MW phase in case of analogue MW channel in deg

            @return: PulseBlockElement, the generated MW element
            """
            if phase is None:
                raise ValueError(
                    '_get_dx_mw_element requires a phase value for I/Q-modulated analog '
                    'microwave channels; got phase=None.'
                )

            iq_frequency = self._get_iq_modulation_frequency(target_frequency=freq)

            envelope = self._get_envelope(envelope)
            self.log.debug(f"_get_dx_mw_element called with envelope {envelope}")

            if self.microwave_channel.startswith('d'):
                mw_element = self._get_trigger_element(length=length, increment=increment, channels=self.microwave_channel)
            else:
                mw_element = self._get_idle_element(length=length, increment=increment)
                if envelope.type == PulseEnvelopeType.rectangle:
                    mw_element.pulse_function['a_ch1'] = SamplingFunctions.Sin(
                        amplitude=amp, frequency=iq_frequency, phase=phase + 90.0
                        )
                    mw_element.pulse_function['a_ch2'] = SamplingFunctions.Sin(
                        amplitude=amp, frequency=iq_frequency, phase=phase
                        )
                elif envelope.type == PulseEnvelopeType.parabola:
                    mw_element.pulse_function['a_ch1'] = SamplingFunctions.SinEnvelopeParabola(
                        amplitude=amp, frequency=iq_frequency, phase=phase + 90.0, order=envelope.parameters['order']
                        )
                    mw_element.pulse_function['a_ch2'] = SamplingFunctions.SinEnvelopeParabola(
                        amplitude=amp, frequency=iq_frequency, phase=phase, order=envelope.parameters['order']
                        )
                elif envelope.type == PulseEnvelopeType.sin_n:
                    mw_element.pulse_function['a_ch1'] = SamplingFunctions.SinEnvelopeSinn(
                        amplitude=amp, frequency=iq_frequency, phase=phase + 90.0, order=envelope.parameters['order']
                        )
                    mw_element.pulse_function['a_ch2'] = SamplingFunctions.SinEnvelopeSinn(
                        amplitude=amp, frequency=iq_frequency, phase=phase, order=envelope.parameters['order']
                        )
                else:
                    raise ValueError(f"Unsupported envelope type: {envelope.type.name}")
            return mw_element

    def _get_dx_mw_element_padded(
            self, length, increment, amp=None, freq=None, phase=None,
            envelope: PulseEnvelope = PulseEnvelope(PulseEnvelopeType.from_gen_settings),
            min_length=None,
        ):
        """
        Wraps _get_dx_mw_element with automatic free-evolution (idle) padding, prepended
        whenever the requested MW pulse "length" is shorter than the PulseBlaster's minimum
        digital pulse duration (_MW_ELEMENT_MIN_LENGTH, default 60 ns @ 100 MHz clock).

        Prepending an idle padding element of length (min_length - length) restores a combined
        minimum duration of min_length, while leaving the MW pulse itself at its physically
        intended (possibly sub-60-ns) length.

        @param float length: nominal MW pulse duration in seconds.
        @param float increment: MW pulse duration increment in seconds.
        @param min_length: minimum combined duration threshold, in seconds. Defaults to
                            self._MW_ELEMENT_MIN_LENGTH if not given.
        (amp, freq, phase, envelope: passed straight through to _get_dx_mw_element)

        @return list of PulseBlockElement: [mw_element] if no padding is needed, otherwise
                [idle_padding_element, mw_element] (in that order).
        """
        if min_length is None:
            min_length = self._MW_ELEMENT_MIN_LENGTH

        mw_element = self._get_dx_mw_element(length=length, increment=increment, amp=amp,
                                             freq=freq, phase=phase, envelope=envelope)

        if length >= min_length:
            return [mw_element]

        padding_length = min_length - length
        idle_padding_element = self._get_idle_element(length=padding_length, increment=0)
        return [idle_padding_element, mw_element]

    def _get_dx_mw_laser_gate_element(self, length, increment, amp=None, freq=None, phase=None):
            """
            Combines an I/Q-modulated MW element (see _get_dx_mw_element) with laser and, if
            configured, gate channel activation on top of it.

            @param length:
            @param increment:
            @param amp:
            @param freq: desired ABSOLUTE output frequency in Hz (see _get_dx_mw_element).
            @param phase:
            @return:
            """
            mw_laser_gate_element = self._get_dx_mw_element(length=length, increment=increment, amp=amp, freq=freq, phase=phase)
            if self.laser_channel.startswith('d'):
                mw_laser_gate_element.digital_high[self.laser_channel] = True
            elif self.laser_channel.startswith('a'):
                mw_laser_gate_element.pulse_function[self.laser_channel] = SamplingFunctions.DC(
                    voltage=self.analog_trigger_voltage
                )
            if self.gate_channel:
                if self.gate_channel.startswith('d'):
                    mw_laser_gate_element.digital_high[self.gate_channel] = True
                elif self.gate_channel.startswith('a'):
                    mw_laser_gate_element.pulse_function[self.gate_channel] = SamplingFunctions.DC(
                        voltage=self.analog_trigger_voltage
                    )

            mw_laser_gate_element.laser_on = True
            return mw_laser_gate_element

    ################################################################################################
    #                                    For pulser control                                        #
    ################################################################################################

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
        Accepts either a single channel string OR a list/tuple of channel strings and sets ALL
        of them HIGH for the entire duration of `element`. None or an empty list is a no-op.
        """
        if always_on_channel is None:
            return
        channels = [always_on_channel] if isinstance(always_on_channel, str) else always_on_channel
        for channel in channels:
            self._set_channel_high(element, channel)

    def _get_pulser_off_idle_element(self, length, increment, always_on_channel=None):
        """Idle element with always_on_channel(s) held HIGH."""
        idle_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(idle_element, always_on_channel)
        return idle_element

    def _get_pulser_on_idle_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        """Idle element with always_on_channel(s) and pulser_channel held HIGH."""
        idle_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(idle_element, always_on_channel)
        self._set_channel_high(idle_element, pulser_channel)
        return idle_element

    def _get_pulser_on_laser_gate_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        """Laser/gate readout element with always_on_channel(s) and pulser_channel held HIGH."""
        laser_gate_element = self._get_laser_gate_element(length=length, increment=increment)
        self._set_always_on_channels(laser_gate_element, always_on_channel)
        self._set_channel_high(laser_gate_element, pulser_channel)
        return laser_gate_element

    def _get_pulser_on_delay_gate_element(self, always_on_channel=None, pulser_channel=None):
        """Delay/gate element with always_on_channel(s) and pulser_channel held HIGH."""
        delay_gate_element = self._get_delay_gate_element()
        self._set_always_on_channels(delay_gate_element, always_on_channel)
        self._set_channel_high(delay_gate_element, pulser_channel)
        return delay_gate_element

    def _get_pulser_on_laser_only_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        """
        Turns ON just the physical laser channel (self.laser_channel) for `length` seconds,
        without toggling the detector gate channel. Used for laser warm-up pulses that must not
        be counted as an additional data point by the pulsed-measurement analysis.
        """
        warmup_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(warmup_element, always_on_channel)
        self._set_channel_high(warmup_element, pulser_channel)
        self._set_channel_high(warmup_element, self.laser_channel)
        return warmup_element

    def _get_pulser_off_dx_mw_element_padded(self, length, increment, amp=None, freq=None, phase=None,
                                              envelope: PulseEnvelope = PulseEnvelope(PulseEnvelopeType.from_gen_settings),
                                              min_length=None, always_on_channel=None):
        """
        I/Q-modulated MW element (built via _get_dx_mw_element_padded, which may prepend a short
        idle pad - see that method's docstring), with always_on_channel(s) held HIGH on every
        returned element.

        @return list of PulseBlockElement.
        """
        elements = self._get_dx_mw_element_padded(length=length, increment=increment, amp=amp,
                                                   freq=freq, phase=phase, envelope=envelope,
                                                   min_length=min_length)
        for elem in elements:
            self._set_always_on_channels(elem, always_on_channel)
        return elements

    def _get_pulser_on_dx_mw_laser_gate_element(self, length, increment, amp=None, freq=None, phase=None,
                                                 always_on_channel=None, pulser_channel=None):
        """
        Combined I/Q-modulated MW+laser+gate element (see _get_dx_mw_laser_gate_element), with
        always_on_channel(s) and pulser_channel held HIGH. Used for CW ODMR, where MW and
        laser/readout happen simultaneously in one element.
        """
        mw_laser_gate_element = self._get_dx_mw_laser_gate_element(
            length=length, increment=increment, amp=amp, freq=freq, phase=phase)
        self._set_always_on_channels(mw_laser_gate_element, always_on_channel)
        self._set_channel_high(mw_laser_gate_element, pulser_channel)
        return mw_laser_gate_element

    def _pad_ensemble_to_granularity(self, block, on, always_on_channel, pulser_channel,
                                    extra_high_channels=None):
        """
        Given a PulseBlock with its "real" elements already appended, measure its exact sample
        count via analyze_block_ensemble(), then append an idle pad element (state = on/off) so
        the block's total length is both >= the pulse generator's minimum waveform length AND an
        exact multiple of its granularity step.

        This prevents SequenceGeneratorLogic from later appending its own idle_extension block to
        fix granularity, which would force all digital channels LOW and whose length isn't known
        until after sampling.

        @param extra_high_channels: optional channel string, or list of channel strings, that
            must also be held HIGH throughout the pad element - needed whenever the padded block
            contains a custom element asserting a channel that the idle-element helpers above
            don't know about (e.g. self.laser_channel in a laser-only warm-up element).

        @return (float, float): (pad_length_s added, final total length_s of the block including
                                the pad)
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
        Build a duty-cycle correction PulseBlock/PulseBlockEnsemble whose base element is looped
        via sequence-step `repetitions` to reach `correction_length` in total (a single small
        waveform is uploaded, not one giant unique one).

        If looping `preferred_base_length` enough times would require more than
        _MAX_SEQUENCE_LOOP_COUNT consecutive plays, the base element's length is instead
        increased just enough to bring the required loop count back under the limit.

        @return (PulseBlockEnsemble, int): the created correction ensemble, and the
            `repetitions` value to use for it in the sequence step (total plays = repetitions + 1).
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

    def _get_pulser_off_dx_mw_laser_gate_element(self, length, increment, amp=None, freq=None, phase=None,
                                                  always_on_channel=None):
        """
        Combined I/Q-modulated MW+laser+gate element (see _get_dx_mw_laser_gate_element), with
        always_on_channel(s) held HIGH, pulser_channel left untouched (LOW).
        """
        mw_laser_gate_element = self._get_dx_mw_laser_gate_element(
            length=length, increment=increment, amp=amp, freq=freq, phase=phase)
        self._set_always_on_channels(mw_laser_gate_element, always_on_channel)
        return mw_laser_gate_element

    def _get_pulser_off_sync_element(self, always_on_channel=None):
        """Sync/trigger element with always_on_channel(s) held HIGH, so the always-on channel
        never drops low even for the short duration of the sync pulse itself. pulser_channel is
        intentionally left untouched (LOW)."""
        sync_element = self._get_sync_element()
        self._set_always_on_channels(sync_element, always_on_channel)
        return sync_element

    ################################################################################################
    #                             Generation methods for waveforms                                 #
    ################################################################################################

    def generate_dx_rabi(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9, num_of_points=50):
        """
        Rabi sequence for combined AWG + PulseBlaster setup: a leading sync/trigger step
        (TWAIT=ON forced by the interfuse), followed by one ensemble per tau point (MW pulse of
        varying length, then separate laser readout), looping back to the trigger step at the
        end.

        MW pulses shorter than the PulseBlaster's minimum digital pulse duration
        (_MW_ELEMENT_MIN_LENGTH, 60 ns by default) are automatically preceded by idle free
        evolution padding - see _get_dx_mw_element_padded.

        Parameters
        ----------
        name : str
            Name of the PulseSequence to be generated.
        tau_start : float
            Length of the first MW pulse in seconds.
        tau_step : float
            Increment of the MW pulse length in seconds.
        num_of_points : int
            Number of tau steps to be generated.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks    = list()
        created_ensembles = list()
        created_sequences = list()

        tau_array = tau_start + np.arange(num_of_points) * tau_step

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

        # ── 2. MW pulse + readout ensembles, one per tau point ───────────────────
        rabi_blocks    = dict()
        rabi_ensembles = dict()
        for kk, tau in enumerate(tau_array):
            mw_elements = self._get_dx_mw_element_padded(length=tau,
                                                          increment=0,
                                                          amp=self.microwave_amplitude,
                                                          phase=0)
            laser_element   = self._get_laser_gate_element(length=self.laser_length, increment=0)
            delay_element   = self._get_delay_gate_element()
            waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

            rabi_blocks[kk] = PulseBlock(name='tau_{0}'.format(kk))
            for mw_elem in mw_elements:
                rabi_blocks[kk].append(mw_elem)
            rabi_blocks[kk].append(laser_element)
            rabi_blocks[kk].append(delay_element)
            rabi_blocks[kk].append(waiting_element)
            created_blocks.append(rabi_blocks[kk])

            rabi_ensembles[kk] = PulseBlockEnsemble(
                name='tau_{0}'.format(kk),
                rotating_frame=False
            )
            rabi_ensembles[kk].append((rabi_blocks[kk].name, 0))
            created_ensembles.append(rabi_ensembles[kk])

        rabi_sequence = PulseSequence(name=name, rotating_frame=False)

        rabi_sequence.append(trigger_ensemble.name)
        rabi_sequence[-1].repetitions = 0

        for kk, tau in enumerate(tau_array):
            rabi_sequence.append(rabi_ensembles[kk].name)
            rabi_sequence[-1].repetitions = 0

        rabi_sequence[-1].go_to = 1

        rabi_sequence.refresh_parameters()

        rabi_sequence.measurement_information['alternating']         = False
        rabi_sequence.measurement_information['laser_ignore_list']   = list()
        rabi_sequence.measurement_information['controlled_variable'] = tau_array
        rabi_sequence.measurement_information['units']               = ('s', '')
        rabi_sequence.measurement_information['labels']              = (
            'Tau<sub>pulse spacing</sub>', 'Signal'
        )
        rabi_sequence.measurement_information['number_of_lasers']    = len(tau_array)
        rabi_sequence.measurement_information['counting_length']     = (
            self.laser_length + delay_element.init_length_s
        )

        created_sequences.append(rabi_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_dx_pulsedodmr(self, name='pODMR', freq_start=3.47e9, freq_stop=3.57e9, num_of_points=50):
        """
        Pulsed ODMR sequence for combined AWG + PulseBlaster setup: a leading sync/trigger step
        (TWAIT=ON forced by the interfuse), followed by one ensemble per frequency point (MW
        pi-pulse, then separate laser readout), looping back to the trigger step at the end.

        Requires self.rabi_period to already be calibrated (e.g. via a prior Rabi measurement),
        since the MW pulse length here is fixed at rabi_period / 2 (a pi-pulse). MW pulses
        shorter than the PulseBlaster's minimum digital pulse duration
        (_MW_ELEMENT_MIN_LENGTH, 60 ns by default) are automatically preceded by idle free
        evolution padding - see _get_dx_mw_element_padded.

        Parameters
        ----------
        name : str
            Name of the PulseSequence to be generated.
        freq_start : float
            Start frequency in Hz.
        freq_stop : float
            Stop frequency in Hz.
        num_of_points : int
            Number of frequency steps to be generated.

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

        # ── 2. MW pi-pulse + readout ensembles, one per frequency point ──────────
        pulsedodmr_blocks    = dict()
        pulsedodmr_ensembles = dict()
        for kk, freq in enumerate(freq_array):
            mw_elements = self._get_dx_mw_element_padded(length=self.rabi_period / 2,
                                                          increment=0,
                                                          amp=self.microwave_amplitude,
                                                          freq=freq,
                                                          phase=0)
            laser_element   = self._get_laser_gate_element(length=self.laser_length, increment=0)
            delay_element   = self._get_delay_gate_element()
            waiting_element = self._get_idle_element(length=self.wait_time, increment=0)

            pulsedodmr_blocks[kk] = PulseBlock(name='freq_{0}'.format(kk))
            for mw_elem in mw_elements:
                pulsedodmr_blocks[kk].append(mw_elem)
            pulsedodmr_blocks[kk].append(laser_element)
            pulsedodmr_blocks[kk].append(delay_element)
            pulsedodmr_blocks[kk].append(waiting_element)
            created_blocks.append(pulsedodmr_blocks[kk])

            pulsedodmr_ensembles[kk] = PulseBlockEnsemble(
                name='freq_{0}'.format(kk),
                rotating_frame=False
            )
            pulsedodmr_ensembles[kk].append((pulsedodmr_blocks[kk].name, 0))
            created_ensembles.append(pulsedodmr_ensembles[kk])

        pulsedodmr_sequence = PulseSequence(name=name, rotating_frame=False)

        pulsedodmr_sequence.append(trigger_ensemble.name)
        pulsedodmr_sequence[-1].repetitions = 0

        for kk, freq in enumerate(freq_array):
            pulsedodmr_sequence.append(pulsedodmr_ensembles[kk].name)
            pulsedodmr_sequence[-1].repetitions = 0

        pulsedodmr_sequence[-1].go_to = 1

        pulsedodmr_sequence.refresh_parameters()

        pulsedodmr_sequence.measurement_information['alternating']         = False
        pulsedodmr_sequence.measurement_information['laser_ignore_list']   = list()
        pulsedodmr_sequence.measurement_information['controlled_variable'] = freq_array
        pulsedodmr_sequence.measurement_information['units']               = ('Hz', '')
        pulsedodmr_sequence.measurement_information['labels']              = (
            'Frequency', 'Signal'
        )
        pulsedodmr_sequence.measurement_information['number_of_lasers']    = len(freq_array)
        pulsedodmr_sequence.measurement_information['counting_length']     = (
            self.laser_length + delay_element.init_length_s
        )

        created_sequences.append(pulsedodmr_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_dx_cw_odmr(self, name='cw_odmr', freq_start=3.4e9, freq_stop=3.6e9, num_of_points=50, mw_amp=0.2, mw_length=10e-6):
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
                Number of linearly spaced frequency points.
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

            freq_array = np.linspace(freq_start, freq_stop, num_of_points)

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
                laser_mw_gate_element = self._get_dx_mw_laser_gate_element(length=mw_length,
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

            cw_odmr_sequence = PulseSequence(name=name, rotating_frame=False)

            cw_odmr_sequence.append(trigger_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

            for kk, freq in enumerate(freq_array):
                cw_odmr_sequence.append(cw_odmr_ensembles[kk].name)
                cw_odmr_sequence[-1].repetitions = 0

            cw_odmr_sequence[-1].go_to = 1

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

    ################################################################################################
    #                       Generation methods with pulser + always-on channel                     #
    ################################################################################################

    def generate_dx_rabi_ao_trig(self, name='rabi_ao_trig', tau_start=10.0e-9, tau_step=10.0e-9,
                                  num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                                  duty_cycle=0.2, rising_time=50e-6, falling_time=50e-6,
                                  laser_warmup_time=100e-6, laser_cooldown=10e-6):
        """
        Sequence-mode Rabi with an always-on channel and a duty-cycle-controlled pulser channel.

        Every block that becomes its own AWG waveform is padded to the pulse generator's minimum
        waveform length / granularity step via _pad_ensemble_to_granularity, so
        SequenceGeneratorLogic never needs to append its own idle_extension block afterward.

        Per tau point: falling (pulser OFF) -> MW pulse (pulser OFF) -> rising (pulser ON) ->
        laser + delay + wait readout (pulser ON). falling/rising/readout are shared blocks
        reused every point; only the MW element is unique per point.

        The duty-cycle correction is realized as a single small base idle element looped via
        sequence-step `repetitions`; if the required loop count would exceed the AWG's per-step
        loop-count limit, the base element's length is increased instead
        (see _build_duty_cycle_correction).

        If a duty-cycle correction is needed at all, a one-time laser warm-up pulse (pulser ON,
        physical laser channel only, not counted as a data point) followed by a cooldown period
        (pulser OFF) is inserted right after the trigger step. Their measured durations are
        folded into the duty-cycle accounting before the correction length is solved for, so the
        realized duty cycle over the entire sequence matches `duty_cycle`.

        Parameters
        ----------
        name : str
        tau_start : float
        tau_step : float
        num_of_points : int
        always_on_channel : str or list of str
            Channel(s) held HIGH for the entire sequence.
        pulser_channel : str
            Channel toggled LOW (falling + MW) / HIGH (rising + readout) each point.
        duty_cycle : float
            Target fraction of total sequence time with pulser_channel HIGH.
        rising_time, falling_time : float
            Pulser ramp buffer durations, in seconds.
        laser_warmup_time, laser_cooldown : float
            One-time laser warm-up/cooldown durations, in seconds.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        tau_array = tau_start + np.arange(num_of_points) * tau_step

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

        # ── Trigger ──────────────────────────────────────────────────────
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

        # ── Falling (shared, off) ────────────────────────────────────────
        falling_block = PulseBlock(name='{0}_falling'.format(name))
        falling_block.append(falling_element)
        _, falling_length_s = self._pad_ensemble_to_granularity(
            falling_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(falling_block)

        falling_ensemble = PulseBlockEnsemble(name='{0}_falling'.format(name), rotating_frame=False)
        falling_ensemble.append((falling_block.name, 0))
        created_ensembles.append(falling_ensemble)

        # ── Rising (shared, on) ──────────────────────────────────────────
        rising_block = PulseBlock(name='{0}_rising'.format(name))
        rising_block.append(rising_element)
        _, rising_length_s = self._pad_ensemble_to_granularity(
            rising_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(rising_block)

        rising_ensemble = PulseBlockEnsemble(name='{0}_rising'.format(name), rotating_frame=False)
        rising_ensemble.append((rising_block.name, 0))
        created_ensembles.append(rising_ensemble)

        # ── Readout (shared, on) ─────────────────────────────────────────
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

        # ── One MW ensemble per tau point (off), each padded individually ──
        mw_ensembles = dict()
        mw_length_total_s = 0.0
        for kk, tau in enumerate(tau_array):
            mw_elements = self._get_pulser_off_dx_mw_element_padded(
                length=tau, increment=0,
                amp=self.microwave_amplitude, freq=None, phase=0,
                always_on_channel=always_on_channel)

            mw_block = PulseBlock(name='{0}_mw_{1}'.format(name, kk))
            for mw_elem in mw_elements:
                mw_block.append(mw_elem)
            _, mw_length_s = self._pad_ensemble_to_granularity(
                mw_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            mw_length_total_s += mw_length_s
            created_blocks.append(mw_block)

            mw_ensembles[kk] = PulseBlockEnsemble(name='{0}_mw_{1}'.format(name, kk), rotating_frame=False)
            mw_ensembles[kk].append((mw_block.name, 0))
            created_ensembles.append(mw_ensembles[kk])

        # ── Duty-cycle correction ────────────────────────────────────────
        total_on  = num_of_points * (rising_length_s + readout_length_s)
        total_off = num_of_points * falling_length_s + mw_length_total_s + trigger_length_s

        total_all = total_on + total_off
        p_on_base = total_on / total_all if total_all > 0 else 0.0
        need_correction = not np.isclose(duty_cycle, p_on_base)

        correction_ensemble = None
        correction_reps = 0
        warmup_ensemble = None
        cooldown_ensemble = None

        if need_correction:
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

            if correction_on is not None:
                preferred_base_length = self.wait_time if correction_on else falling_time
                correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                    name=name, correction_length=correction_length, correction_on=correction_on,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                    preferred_base_length=preferred_base_length,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

        # ── Sequence construction ────────────────────────────────────────
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

    def generate_dx_pulsedodmr_ao_trig(self, name='pODMR_ao_trig', freq_start=3.47e9, freq_stop=3.57e9,
                                        num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                                        duty_cycle=0.2, rising_time=50e-6, falling_time=50e-6,
                                        laser_warmup_time=100e-6, laser_cooldown=10e-6):
        """
        Sequence-mode pulsed ODMR with an always-on channel and a duty-cycle-controlled pulser
        channel. Same structure as generate_dx_rabi_ao_trig, swept over frequency with a fixed
        pi-pulse length (self.rabi_period / 2) instead of swept tau.

        Requires self.rabi_period to already be calibrated (e.g. via a prior Rabi measurement).

        Parameters
        ----------
        name : str
        freq_start : float
        freq_stop : float
        num_of_points : int
        always_on_channel : str or list of str
            Channel(s) held HIGH for the entire sequence.
        pulser_channel : str
            Channel toggled LOW (falling + MW) / HIGH (rising + readout) each point.
        duty_cycle : float
            Target fraction of total sequence time with pulser_channel HIGH.
        rising_time, falling_time : float
            Pulser ramp buffer durations, in seconds.
        laser_warmup_time, laser_cooldown : float
            One-time laser warm-up/cooldown durations, in seconds.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        freq_array = np.linspace(freq_start, freq_stop, num_of_points)

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

        # ── Trigger ──────────────────────────────────────────────────────
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

        # ── Falling (shared, off) ────────────────────────────────────────
        falling_block = PulseBlock(name='{0}_falling'.format(name))
        falling_block.append(falling_element)
        _, falling_length_s = self._pad_ensemble_to_granularity(
            falling_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(falling_block)

        falling_ensemble = PulseBlockEnsemble(name='{0}_falling'.format(name), rotating_frame=False)
        falling_ensemble.append((falling_block.name, 0))
        created_ensembles.append(falling_ensemble)

        # ── Rising (shared, on) ──────────────────────────────────────────
        rising_block = PulseBlock(name='{0}_rising'.format(name))
        rising_block.append(rising_element)
        _, rising_length_s = self._pad_ensemble_to_granularity(
            rising_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(rising_block)

        rising_ensemble = PulseBlockEnsemble(name='{0}_rising'.format(name), rotating_frame=False)
        rising_ensemble.append((rising_block.name, 0))
        created_ensembles.append(rising_ensemble)

        # ── Readout (shared, on) ─────────────────────────────────────────
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

        # ── One MW (pi-pulse) ensemble per frequency point (off), padded individually ──
        mw_ensembles = dict()
        mw_length_total_s = 0.0
        for kk, freq in enumerate(freq_array):
            mw_elements = self._get_pulser_off_dx_mw_element_padded(
                length=self.rabi_period / 2, increment=0,
                amp=self.microwave_amplitude, freq=freq, phase=0,
                always_on_channel=always_on_channel)

            mw_block = PulseBlock(name='{0}_mw_{1}'.format(name, kk))
            for mw_elem in mw_elements:
                mw_block.append(mw_elem)
            _, mw_length_s = self._pad_ensemble_to_granularity(
                mw_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            mw_length_total_s += mw_length_s
            created_blocks.append(mw_block)

            mw_ensembles[kk] = PulseBlockEnsemble(name='{0}_mw_{1}'.format(name, kk), rotating_frame=False)
            mw_ensembles[kk].append((mw_block.name, 0))
            created_ensembles.append(mw_ensembles[kk])

        # ── Duty-cycle correction ────────────────────────────────────────
        total_on  = num_of_points * (rising_length_s + readout_length_s)
        total_off = num_of_points * falling_length_s + mw_length_total_s + trigger_length_s

        total_all = total_on + total_off
        p_on_base = total_on / total_all if total_all > 0 else 0.0
        need_correction = not np.isclose(duty_cycle, p_on_base)

        correction_ensemble = None
        correction_reps = 0
        warmup_ensemble = None
        cooldown_ensemble = None

        if need_correction:
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

            if correction_on is not None:
                preferred_base_length = self.wait_time if correction_on else falling_time
                correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                    name=name, correction_length=correction_length, correction_on=correction_on,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                    preferred_base_length=preferred_base_length,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

        # ── Sequence construction ────────────────────────────────────────
        pulsedodmr_sequence = PulseSequence(name=name, rotating_frame=False)

        pulsedodmr_sequence.append(trigger_ensemble.name)
        pulsedodmr_sequence[-1].repetitions = 0

        if warmup_ensemble is not None:
            pulsedodmr_sequence.append(warmup_ensemble.name)
            pulsedodmr_sequence[-1].repetitions = 0

        if cooldown_ensemble is not None:
            pulsedodmr_sequence.append(cooldown_ensemble.name)
            pulsedodmr_sequence[-1].repetitions = 0

        for kk, freq in enumerate(freq_array):
            pulsedodmr_sequence.append(falling_ensemble.name)
            pulsedodmr_sequence[-1].repetitions = 0

            pulsedodmr_sequence.append(mw_ensembles[kk].name)
            pulsedodmr_sequence[-1].repetitions = 0

            pulsedodmr_sequence.append(rising_ensemble.name)
            pulsedodmr_sequence[-1].repetitions = 0

            pulsedodmr_sequence.append(readout_ensemble.name)
            pulsedodmr_sequence[-1].repetitions = 0

        if correction_ensemble is not None:
            pulsedodmr_sequence.append(correction_ensemble.name)
            pulsedodmr_sequence[-1].repetitions = correction_reps

        pulsedodmr_sequence[-1].go_to = 1
        pulsedodmr_sequence.refresh_parameters()

        pulsedodmr_sequence.measurement_information['alternating'] = False
        pulsedodmr_sequence.measurement_information['laser_ignore_list'] = list()
        pulsedodmr_sequence.measurement_information['controlled_variable'] = freq_array
        pulsedodmr_sequence.measurement_information['units'] = ('Hz', '')
        pulsedodmr_sequence.measurement_information['labels'] = ('Frequency', 'Signal')
        pulsedodmr_sequence.measurement_information['number_of_lasers'] = len(freq_array)
        pulsedodmr_sequence.measurement_information['counting_length'] = (self.laser_length + delay_element.init_length_s)

        created_sequences.append(pulsedodmr_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_dx_cw_odmr_ao_trig(self, name='cw_odmr_ao_trig', freq_start=3.4e9, freq_stop=3.6e9,
                                     num_of_points=50, mw_amp=0.2, mw_length=10e-6,
                                     always_on_channel='d_ch15', pulser_channel='d_ch3', pulser_mode=1,
                                     duty_cycle=0.2, falling_time=50e-6,
                                     laser_warmup_time=100e-6, laser_cooldown=10e-6):
        """
        CW ODMR sequence, extended with an always-on channel and a duty-cycle-controlled pulser
        channel.

        pulser_mode : 0 -> pulser_channel is LOW during the mw+laser element.
                      1 -> pulser_channel is HIGH during the mw+laser element (default, matches
                           the original behavior of this method).
        In both modes, falling keeps pulser_channel LOW, and delay + waiting keep it HIGH -
        unchanged from the original structure.

        Per-frequency-point block structure:
            falling_block (shared, OFF)
            mw_block: mw+laser element [ON if pulser_mode=1 else OFF] + delay element [ON]
            readout_block (shared, ON): waiting element

        duty_cycle is realized via a single small base idle element looped via sequence-step
        `repetitions` (see _build_duty_cycle_correction). If a correction is needed at all, a
        one-time laser warm-up pulse (pulser ON, physical laser channel only) followed by a
        cooldown period (pulser OFF) is inserted right after the trigger step, and their
        measured durations are folded into the duty-cycle accounting before the correction
        length is solved for.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        freq_array = np.linspace(freq_start, freq_stop, num_of_points)

        if pulser_mode not in (0, 1):
            self.log.error('pulser_mode must be 0 or 1 (got {0}); treating as 1.'.format(pulser_mode))
            pulser_mode = 1

        falling_element = self._get_pulser_off_idle_element(
            length=falling_time, increment=0, always_on_channel=always_on_channel)
        waiting_element = self._get_pulser_on_idle_element(
            length=self.wait_time, increment=0,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        delay_element = self._get_pulser_on_delay_gate_element(
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)

        # ── Trigger (pulser OFF; always_on_channel ON) ──────────────────────
        sync_element = self._get_pulser_off_sync_element(always_on_channel=always_on_channel)
        trigger_block = PulseBlock(name='{0}_trigger'.format(name))
        trigger_block.append(sync_element)
        _, trigger_length_s = self._pad_ensemble_to_granularity(
            trigger_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(trigger_block)

        trigger_ensemble = PulseBlockEnsemble(name='{0}_trigger'.format(name), rotating_frame=False)
        trigger_ensemble.append((trigger_block.name, 0))
        created_ensembles.append(trigger_ensemble)

        # ── Falling (shared, off) ────────────────────────────────────────
        falling_block = PulseBlock(name='{0}_falling'.format(name))
        falling_block.append(falling_element)
        _, falling_length_s = self._pad_ensemble_to_granularity(
            falling_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(falling_block)

        falling_ensemble = PulseBlockEnsemble(name='{0}_falling'.format(name), rotating_frame=False)
        falling_ensemble.append((falling_block.name, 0))
        created_ensembles.append(falling_ensemble)

        # ── Readout (shared, on): waiting only - mw+laser element itself carries the pulser
        # state from the moment it starts, delay is handled inside each mw_block ──────────────
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        _, readout_length_s = self._pad_ensemble_to_granularity(
            readout_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(readout_block)

        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        # ── One mw+laser+delay ensemble per frequency point ──────────────────
        mw_ensembles = dict()
        mw_on_total_s = 0.0
        mw_off_total_s = 0.0
        for kk, freq in enumerate(freq_array):
            if pulser_mode == 1:
                mw_laser_gate_element = self._get_pulser_on_dx_mw_laser_gate_element(
                    length=mw_length, increment=0, amp=mw_amp, freq=freq, phase=0,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            else:
                mw_laser_gate_element = self._get_pulser_off_dx_mw_laser_gate_element(
                    length=mw_length, increment=0, amp=mw_amp, freq=freq, phase=0,
                    always_on_channel=always_on_channel)

            mw_block = PulseBlock(name='{0}_mw_{1}'.format(name, kk))
            mw_block.append(mw_laser_gate_element)
            mw_block.append(delay_element)
            _, mw_length_s = self._pad_ensemble_to_granularity(
                mw_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(mw_block)

            # mw_length_s = mw_length + delay_length + pad, all measured together. Split into
            # the ON portion (delay + pad, always ON) and OFF portion (mw_length itself, only
            # ON if pulser_mode == 1).
            if pulser_mode == 1:
                mw_on_total_s += mw_length_s
            else:
                mw_on_total_s  += mw_length_s - mw_length
                mw_off_total_s += mw_length

            mw_ensembles[kk] = PulseBlockEnsemble(name='{0}_mw_{1}'.format(name, kk), rotating_frame=False)
            mw_ensembles[kk].append((mw_block.name, 0))
            created_ensembles.append(mw_ensembles[kk])

        # ── Duty-cycle correction ─────────────────────────────────────────
        total_on  = num_of_points * readout_length_s + mw_on_total_s
        total_off = num_of_points * falling_length_s + trigger_length_s + mw_off_total_s

        total_all = total_on + total_off
        p_on_base = total_on / total_all if total_all > 0 else 0.0
        need_correction = not np.isclose(duty_cycle, p_on_base)

        correction_ensemble = None
        correction_reps = 0
        warmup_ensemble = None
        cooldown_ensemble = None

        if need_correction:
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

            if correction_on is not None:
                preferred_base_length = self.wait_time if correction_on else falling_time
                correction_ensemble, correction_reps = self._build_duty_cycle_correction(
                    name=name, correction_length=correction_length, correction_on=correction_on,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                    preferred_base_length=preferred_base_length,
                    created_blocks=created_blocks, created_ensembles=created_ensembles)

        # ── Sequence construction ────────────────────────────────────────
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
            cw_odmr_sequence.append(falling_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

            cw_odmr_sequence.append(mw_ensembles[kk].name)
            cw_odmr_sequence[-1].repetitions = 0

            cw_odmr_sequence.append(readout_ensemble.name)
            cw_odmr_sequence[-1].repetitions = 0

        if correction_ensemble is not None:
            cw_odmr_sequence.append(correction_ensemble.name)
            cw_odmr_sequence[-1].repetitions = correction_reps

        cw_odmr_sequence[-1].go_to = 1
        cw_odmr_sequence.refresh_parameters()

        cw_odmr_sequence.measurement_information['alternating'] = False
        cw_odmr_sequence.measurement_information['laser_ignore_list'] = list()
        cw_odmr_sequence.measurement_information['controlled_variable'] = freq_array
        cw_odmr_sequence.measurement_information['units'] = ('Hz', '')
        cw_odmr_sequence.measurement_information['labels'] = ('Frequency', 'Signal')
        cw_odmr_sequence.measurement_information['number_of_lasers'] = len(freq_array)
        cw_odmr_sequence.measurement_information['counting_length'] = (mw_length + delay_element.init_length_s)

        created_sequences.append(cw_odmr_sequence)
        return created_blocks, created_ensembles, created_sequences