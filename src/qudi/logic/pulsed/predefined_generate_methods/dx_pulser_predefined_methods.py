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
from qudi.logic.pulsed.sampling_functions import SamplingFunctions, PulseEnvelope, PulseEnvelopeType
from qudi.logic.pulsed.pulse_objects import PredefinedGeneratorBase

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


class DXPulserPredefinedGenerator(PredefinedGeneratorBase):
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
        self.log.info(
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
            self.log.info(f"_get_dx_mw_element called with envelope {envelope}")

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

    def _get_pulser_off_laser_gate_element(self, length, increment, always_on_channel=None):
        """Laser/gate readout element with always_on_channel(s) held HIGH, pulser_channel left
        untouched (LOW). Used by the alternating-trace feature (mode 1) to build a "laser+gate,
        no MW" element in setups where the pulser channel is never driven."""
        laser_gate_element = self._get_laser_gate_element(length=length, increment=increment)
        self._set_always_on_channels(laser_gate_element, always_on_channel)
        return laser_gate_element

    def _get_pulser_on_delay_gate_element(self, always_on_channel=None, pulser_channel=None):
        """Delay/gate element with always_on_channel(s) and pulser_channel held HIGH."""
        delay_gate_element = self._get_delay_gate_element()
        self._set_always_on_channels(delay_gate_element, always_on_channel)
        self._set_channel_high(delay_gate_element, pulser_channel)
        return delay_gate_element

    def _get_pulser_off_delay_gate_element(self, always_on_channel=None):
        """Delay/gate element with always_on_channel(s) held HIGH, pulser_channel left
        untouched (LOW)."""
        delay_gate_element = self._get_delay_gate_element()
        self._set_always_on_channels(delay_gate_element, always_on_channel)
        return delay_gate_element

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

    def _check_mw_switch_channel(self, mw_switch_channel):
        """ Validates the RF switch channel (empty = no switch). Raises ValueError if it is not an
        active digital channel of the pulse generator. """
        if not mw_switch_channel:
            return
        if not mw_switch_channel.startswith('d') or mw_switch_channel not in self.digital_channels:
            raise ValueError(
                'mw_switch_channel "{0}" is not an active digital channel of the pulse generator '
                '(active: {1}).'.format(mw_switch_channel, sorted(self.digital_channels)))

    def _gate_mw_elements(self, elements, mw_switch_channel, mw_switch_margin, always_on_channel,
                          pulser_on=False, pulser_channel=None):
        """
        Opens the RF switch around a group of MW elements: returns
        [lead idle (mw_switch_margin)] + elements + [lag idle (mw_switch_margin)], with
        mw_switch_channel HIGH on all of them. The pulses themselves stay shaped by the AWG I/Q
        signal; the switch only has to be open while they play, so its own (slower) switching
        edges fall into the lead/lag margins. Outside these windows the switch blocks the MW
        path, i.e. AWG noise and carrier leakage, e.g. during T1 waits.

        @param bool pulser_on: pulser state of the lead/lag idles (must match the block).
        @return list of PulseBlockElement (elements unchanged if mw_switch_channel is empty).
        """
        elements = list(elements)
        if not mw_switch_channel:
            return elements
        margins = list()
        for _ in range(2):
            if pulser_on:
                margins.append(self._get_pulser_on_idle_element(
                    length=mw_switch_margin, increment=0,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel))
            else:
                margins.append(self._get_pulser_off_idle_element(
                    length=mw_switch_margin, increment=0, always_on_channel=always_on_channel))
        gated = [margins[0]] + elements + [margins[1]]
        for element in gated:
            self._set_channel_high(element, mw_switch_channel)
        return gated

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
        """
        Sync/trigger element with always_on_channel(s) held HIGH, pulser_channel always left
        LOW - the pulser must be off during the sync pulse itself, regardless of which block it
        gets merged into.
        """
        sync_element = self._get_sync_element()
        self._set_always_on_channels(sync_element, always_on_channel)
        return sync_element

    def _pad_ensemble_to_granularity(self, block, on, always_on_channel, pulser_channel):
        """
        Given a PulseBlock with its "real" elements already appended, measure its exact sample
        count via analyze_block_ensemble(), then append an idle pad element (state = on/off) so
        the block's total length is both >= the pulse generator's minimum waveform length AND an
        exact multiple of its granularity step.

        This prevents SequenceGeneratorLogic from later appending its own idle_extension block to
        fix granularity, which would force all digital channels LOW and whose length isn't known
        until after sampling.

        @return (float, float): (pad_length_s added, final total length_s of the block including
                                the pad)
        """
        self.save_block(block)
        temp_ensemble = PulseBlockEnsemble(name='__tmp_granularity_check__{0}'.format(block.name), rotating_frame=False)
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

    def _get_pulser_on_laser_only_readout_element(self, length, increment, always_on_channel=None, pulser_channel=None):
        """
        Laser-only readout element: turns on ONLY the physical laser channel (self.laser_channel)
        for `length` seconds, WITHOUT the detector gate channel - unlike
        _get_pulser_on_laser_gate_element. Used for a one-time settle/flush exposure that must
        not be counted as a measurement data point.
        """
        readout_element = self._get_idle_element(length=length, increment=increment)
        self._set_always_on_channels(readout_element, always_on_channel)
        self._set_channel_high(readout_element, pulser_channel)
        self._set_channel_high(readout_element, self.laser_channel)
        return readout_element

    ################################################################################################
    #                     Duty-cycle measurement/correction (works on any sequence)                #
    ################################################################################################

    def _get_channel_high_fraction_in_block(self, block, channel):
        """
        Inspects every PulseBlockElement actually appended to `block` and returns the fraction
        of the block's total duration for which `channel` is HIGH - read directly off each
        element's own digital_high / pulse_function state.

        @return float: value in [0.0, 1.0]. 0.0 if channel is None.
        """
        if channel is None:
            return 0.0

        total_length_s = 0.0
        high_length_s = 0.0
        for element in block.element_list:
            length_s = element.init_length_s
            total_length_s += length_s
            is_high = False
            if channel.startswith('d'):
                is_high = bool(element.digital_high.get(channel, False))
            elif channel.startswith('a'):
                pulse_func = element.pulse_function.get(channel, None)
                is_high = getattr(pulse_func, 'voltage', None) not in (None, 0.0)
            if is_high:
                high_length_s += length_s

        if total_length_s <= 0.0:
            return 0.0
        return high_length_s / total_length_s

    def _measure_sequence_duty_cycle(self, sequence, created_blocks, created_ensembles, pulser_channel):
        """
        Measures the TOTAL on-time and off-time of `pulser_channel` by walking an already-built
        PulseSequence step by step - looking up each step's ensemble/block and its actual
        sampled state directly, weighted by that step's (repetitions + 1) plays.

        @param sequence: a PulseSequence with all its real steps already appended.
        @param created_blocks, created_ensembles: lists of all PulseBlock / PulseBlockEnsemble
            objects created so far, used to resolve each step's ensemble name to its block.
        @param pulser_channel: channel whose duty cycle is being measured.

        @return (float, float): (total_on_s, total_off_s).
        """
        block_by_name = {block.name: block for block in created_blocks}
        ensemble_by_name = {ensemble.name: ensemble for ensemble in created_ensembles}

        total_on_s = 0.0
        total_off_s = 0.0
        for step in sequence:
            ensemble = ensemble_by_name.get(step.ensemble)
            if ensemble is None:
                continue
            block_name = ensemble.block_list[0][0]
            block = block_by_name.get(block_name)
            if block is None:
                continue

            block_length_s = sum(elem.init_length_s for elem in block.element_list)
            high_fraction = self._get_channel_high_fraction_in_block(block, pulser_channel)

            n_plays = step.repetitions + 1
            total_on_s  += n_plays * block_length_s * high_fraction
            total_off_s += n_plays * block_length_s * (1.0 - high_fraction)

        return total_on_s, total_off_s

    def _measure_planned_duty_cycle(self, block_plays, pulser_channel):
        """
        Computes total on/off time of `pulser_channel` from a list of (PulseBlock, n_plays)
        tuples describing a PLANNED sequence structure - without needing an actual built
        PulseSequence. Used whenever the correction has to be computed BEFORE the real
        PulseSequence exists yet, e.g. because the correction needs to be distributed across
        several insertion points while the sequence is being built, rather than measured
        after the fact and appended once at the end (see
        _prepare_distributed_duty_cycle_correction).

        @param block_plays: list of (PulseBlock, int) - each block together with how many
            times it will be played in total (consecutively or not - only the total count
            matters for the aggregate duty cycle). The caller is responsible for making sure
            this list exactly mirrors what will actually be built.
        @param pulser_channel: channel whose duty cycle is being computed.

        @return (float, float): (total_on_s, total_off_s).
        """
        total_on_s = 0.0
        total_off_s = 0.0
        for block, n_plays in block_plays:
            block_length_s = sum(elem.init_length_s for elem in block.element_list)
            high_fraction = self._get_channel_high_fraction_in_block(block, pulser_channel)
            total_on_s += n_plays * block_length_s * high_fraction
            total_off_s += n_plays * block_length_s * (1.0 - high_fraction)
        return total_on_s, total_off_s

    def _solve_duty_cycle_correction_length(self, total_on_s, total_off_s, duty_cycle):
        """
        Given already-measured total_on_s/total_off_s, returns the (correction_on,
        correction_length) needed to bring the OVERALL duty cycle to `duty_cycle`.

        @return (bool or None, float): correction_on is True/False if a correction is needed,
            or None if the current duty cycle already matches (or total time is zero).
        """
        total_all_s = total_on_s + total_off_s
        if total_all_s <= 0.0:
            return None, 0.0

        p_on = total_on_s / total_all_s
        if np.isclose(duty_cycle, p_on):
            return None, 0.0

        if duty_cycle > p_on:
            L = (duty_cycle * total_all_s - total_on_s) / (1.0 - duty_cycle)
            return (True, L) if L > 0 else (None, 0.0)
        else:
            L = total_on_s / duty_cycle - total_all_s
            return (False, L) if L > 0 else (None, 0.0)

    def _distribute_correction_plays(self, total_plays, num_slots):
        """
        Evenly distributes `total_plays` total plays of a single correction ensemble across
        `num_slots` candidate insertion points, as evenly as possible - some slots get exactly
        one more play than others so the total is always exact (no rounding error, regardless
        of whether total_plays divides evenly by num_slots).

        @param total_plays: total number of plays to distribute (>= 0).
        @param num_slots: number of candidate insertion points (> 0).

        @return list of int, length num_slots: play count for each slot (>= 0). Sums exactly to
            total_plays.
        """
        base_count, remainder = divmod(total_plays, num_slots)
        return [base_count + (1 if i < remainder else 0) for i in range(num_slots)]

    def _apply_duty_cycle_correction(self, sequence, created_blocks, created_ensembles,
                                    always_on_channel, pulser_channel, duty_cycle,
                                    name, preferred_on_base_length, preferred_off_base_length,
                                    enabled=True):
        """
        Given an already fully-built PulseSequence (ALL real content already appended, and
        go_to already pointing back to the intended loop-back step), this measures the
        sequence's actual pulser_channel duty cycle (_measure_sequence_duty_cycle) and, if it
        doesn't already match `duty_cycle`, appends exactly ONE additional idle correction step
        (looped via sequence-step repetitions to reach the exact required length - see
        _build_duty_cycle_correction) right before the loop-back point, then moves go_to onto
        that new final step.

        Use this when there is no normal/alternating pairing constraint to respect (i.e.
        alternating=False) - a single lump-sum correction anywhere in the sequence is fine in
        that case. When normal/alternating points are interleaved and each point needs its own
        insertion opportunity, use _prepare_distributed_duty_cycle_correction instead, which
        spreads the correction across multiple insertion points rather than placing it all in
        one spot.

        Logs the measured on/off/total time and resulting duty cycle both before and after any
        correction is applied, using the exact same measurement function the correction decision
        itself is based on.

        @param sequence: PulseSequence, modified in place.
        @param created_blocks, created_ensembles: lists, appended to in place if a new
            correction block/ensemble is created.
        @param always_on_channel, pulser_channel: as elsewhere.
        @param duty_cycle: target fraction of total sequence time with pulser_channel HIGH.
        @param name: base name for the correction block/ensemble.
        @param preferred_on_base_length, preferred_off_base_length: preferred base element
            duration for the correction loop, depending on whether ON or OFF time needs to be
            added (see _build_duty_cycle_correction).
        @param enabled: if False, the duty cycle is only measured and logged, nothing is
            appended.

        @return bool: True if a correction step was appended.
        """
        def _log_duty_cycle(label, total_on_s, total_off_s):
            total_s = total_on_s + total_off_s
            p_on = total_on_s / total_s if total_s > 0 else float('nan')
            self.log.warning(
                '{0} duty cycle for "{1}": on={2:.6e} s, off={3:.6e} s, total={4:.6e} s, '
                'p_on={5:.6f}'.format(label, name, total_on_s, total_off_s, total_s, p_on))

        total_on_s, total_off_s = self._measure_sequence_duty_cycle(
            sequence, created_blocks, created_ensembles, pulser_channel)
        if not enabled:
            _log_duty_cycle('Uncorrected (duty cycle correction disabled)', total_on_s, total_off_s)
            return False
        _log_duty_cycle('Pre-correction', total_on_s, total_off_s)

        correction_on, correction_length = self._solve_duty_cycle_correction_length(
            total_on_s, total_off_s, duty_cycle)
        if correction_on is None:
            _log_duty_cycle('Post-correction (no correction needed)', total_on_s, total_off_s)
            return False

        preferred_base_length = preferred_on_base_length if correction_on else preferred_off_base_length
        correction_ensemble, correction_reps = self._build_duty_cycle_correction(
            name=name, correction_length=correction_length, correction_on=correction_on,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel,
            preferred_base_length=preferred_base_length,
            created_blocks=created_blocks, created_ensembles=created_ensembles)

        loop_back_target = sequence[-1].go_to
        sequence.append(correction_ensemble.name)
        sequence[-1].repetitions = correction_reps
        sequence[-2].go_to = 0
        sequence[-1].go_to = loop_back_target

        total_on_s, total_off_s = self._measure_sequence_duty_cycle(
            sequence, created_blocks, created_ensembles, pulser_channel)
        _log_duty_cycle('Post-correction', total_on_s, total_off_s)

        return True

    def _prepare_distributed_duty_cycle_correction(self, block_plays, always_on_channel, pulser_channel,
                                                    duty_cycle, name, preferred_on_base_length,
                                                    preferred_off_base_length, num_slots,
                                                    created_blocks, created_ensembles, enabled=True):
        """
        Prepares a duty-cycle correction that gets DISTRIBUTED across up to `num_slots`
        insertion points, instead of being appended once as a single lump-sum block. Use this
        whenever a single lump-sum correction could otherwise sit for a long stretch of the
        sequence (e.g. only once at the very end), leaving the actual real-time duty cycle far
        from target throughout most of the measurement, and/or whenever normal/alternating
        points need each their own insertion opportunity so every individual point can be
        immediately preceded by a correction of the same kind.

        Builds ONE correction PulseBlock/PulseBlockEnsemble exactly as usual (see
        _build_duty_cycle_correction - granularity padding happens exactly once), then
        distributes its total required repetitions evenly across `num_slots` candidate
        positions (_distribute_correction_plays, exact integer split, no rounding error).
        Because the base block is only ever padded/sized once regardless of how many
        insertion points end up being used, the achieved duty cycle is exactly as accurate as
        the single-block approach - splitting the correction across more points does not
        introduce any additional granularity error.

        Does NOT touch any PulseSequence - the caller must use the returned per-slot
        repetitions list while building its own PulseSequence, appending a step referencing
        the returned ensemble (with that slot's repetitions value) at each of its chosen
        insertion points, and skipping slots whose value is None entirely.

        Logs the measured on/off/total time and resulting duty cycle both before AND after the
        correction (computed directly from the planned block/play counts, mirroring exactly
        what _apply_duty_cycle_correction logs for the non-distributed case).

        @param block_plays: list of (PulseBlock, int) describing the ENTIRE planned sequence
            (excluding the correction itself) - must mirror exactly what the caller is about
            to build. Used to measure the pre-correction duty cycle via
            _measure_planned_duty_cycle.
        @param num_slots: number of candidate insertion points. If the total required
            correction needs fewer total plays than num_slots, most slots simply end up unused
            (value None in the returned list) - this is not a problem and is not warned about,
            since spreading correction across fewer physical locations than available slots
            has no accuracy cost. A warning IS logged if a single slot would end up needing
            more consecutive plays than the AWG's _MAX_SEQUENCE_LOOP_COUNT allows (only
            possible with very few slots and a very large correction).
        @param enabled: if False, the duty cycle is only measured and logged, and every slot
            is returned as None (nothing to insert).

        @return (PulseBlockEnsemble or None, list of (int or None)): the created correction
            ensemble (or None if no correction is needed at all), and a list of length
            num_slots giving the repetitions value to use for that slot's sequence step (None
            = skip this slot entirely, i.e. do not append anything there).
        """
        def _log_duty_cycle(label, total_on_s, total_off_s):
            total_s = total_on_s + total_off_s
            p_on = total_on_s / total_s if total_s > 0 else float('nan')
            self.log.warning(
                '{0} duty cycle for "{1}": on={2:.6e} s, off={3:.6e} s, total={4:.6e} s, '
                'p_on={5:.6f}'.format(label, name, total_on_s, total_off_s, total_s, p_on))

        total_on_s, total_off_s = self._measure_planned_duty_cycle(block_plays, pulser_channel)
        if not enabled:
            _log_duty_cycle('Uncorrected (duty cycle correction disabled)', total_on_s, total_off_s)
            return None, [None] * num_slots
        _log_duty_cycle('Pre-correction', total_on_s, total_off_s)

        correction_on, correction_length = self._solve_duty_cycle_correction_length(
            total_on_s, total_off_s, duty_cycle)
        if correction_on is None:
            _log_duty_cycle('Post-correction (no correction needed)', total_on_s, total_off_s)
            return None, [None] * num_slots

        preferred_base_length = preferred_on_base_length if correction_on else preferred_off_base_length
        correction_ensemble, correction_reps = self._build_duty_cycle_correction(
            name=name, correction_length=correction_length, correction_on=correction_on,
            always_on_channel=always_on_channel, pulser_channel=pulser_channel,
            preferred_base_length=preferred_base_length,
            created_blocks=created_blocks, created_ensembles=created_ensembles)

        total_plays = correction_reps + 1
        plays_per_slot = self._distribute_correction_plays(total_plays, num_slots)

        max_plays_in_slot = max(plays_per_slot) if plays_per_slot else 0
        if max_plays_in_slot > self._MAX_SEQUENCE_LOOP_COUNT:
            self.log.warning(
                'Distributed duty-cycle correction for "{0}" requires up to {1} consecutive '
                'plays in a single insertion slot, exceeding the AWG limit of {2}. Consider '
                'increasing the number of measurement points to spread the correction more '
                'thinly.'.format(name, max_plays_in_slot, self._MAX_SEQUENCE_LOOP_COUNT))

        self.log.warning(
            'Distributed duty-cycle correction for "{0}": correction_on={1}, total_plays={2}, '
            'num_slots={3}, plays_per_slot min/max={4}/{5}.'.format(
                name, correction_on, total_plays, num_slots,
                min(plays_per_slot) if plays_per_slot else 0, max_plays_in_slot))

        correction_block = created_blocks[-1]
        block_plays_with_correction = block_plays + [(correction_block, total_plays)]
        total_on_s, total_off_s = self._measure_planned_duty_cycle(
            block_plays_with_correction, pulser_channel)
        _log_duty_cycle('Post-correction', total_on_s, total_off_s)

        reps_per_slot = [(p - 1) if p > 0 else None for p in plays_per_slot]
        return correction_ensemble, reps_per_slot

    ################################################################################################
    #                       Generation methods with pulser + always-on channel                     #
    ################################################################################################

    def _register_block(self, block, on, always_on_channel, pulser_channel,
                        created_blocks, created_ensembles):
        """
        Pads `block` to the pulse generator granularity (see _pad_ensemble_to_granularity),
        wraps it into a single-block PulseBlockEnsemble of the same name and appends both to
        the created_* lists.

        @return PulseBlockEnsemble: the created ensemble.
        """
        self._pad_ensemble_to_granularity(
            block, on=on, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(block)
        ensemble = PulseBlockEnsemble(name=block.name, rotating_frame=False)
        ensemble.append((block.name, 0))
        created_ensembles.append(ensemble)
        return ensemble

    def _build_dx_pulsed_sequence(self, name, points, always_on_channel, pulser_channel,
                                  duty_cycle, duty_cycle_channel, rising_time, falling_time,
                                  duty_cycle_correction, distributed_correction, repolarize,
                                  created_blocks, created_ensembles):
        """
        Builds the shared sequence of generate_dx_rabi_ao_trig and generate_dx_pulsedodmr_ao_trig
        around an ordered list of already registered MW points (normal and alternating points
        already interleaved by the caller).

        Per point:  [falling] -> mw -> rising -> readout (laser + gate) -> [slot]
        The very first point's falling carries the sync trigger and is also the loop-back
        target, so every point is preceded by exactly one falling ramp.

        [slot] = duty-cycle correction (if any plays fall onto that slot) followed by the
        repolarization block (if repolarize=True; rising -> laser without gate -> laser_delay,
        all pulser ON). With distributed_correction=True there is one slot after every point,
        otherwise a single slot after the last point. With duty_cycle_correction=False there are
        no slots at all (and hence no repolarization blocks); the duty cycle is only logged.

        Without a pulser_channel there is nothing to ramp or correct: the sequence is just
        trigger -> [mw -> readout] per point, without rising/falling ramps, duty-cycle
        correction or repolarization.

        @param list points: MW points in play order. Each point is either one
            (PulseBlock, PulseBlockEnsemble), or a list of (PulseBlock, PulseBlockEnsemble,
            repetitions) sequence steps played in that order (e.g. a long wait built from a
            short block looped via sequence repetitions).
        @param str duty_cycle_channel: channel measured/driven by the duty-cycle correction.
            Falls back to pulser_channel if empty.

        @return PulseSequence: the sequence (go_to and refresh_parameters already applied,
            measurement_information still to be filled in by the caller).
        """
        correction_channel = duty_cycle_channel or pulser_channel
        use_pulser = bool(pulser_channel)
        if not use_pulser and duty_cycle_correction:
            if duty_cycle_channel:
                self.log.warning(
                    'No pulser_channel selected for "{0}": duty-cycle correction on '
                    'duty_cycle_channel "{1}" is skipped.'.format(name, duty_cycle_channel))
            duty_cycle_correction = False

        def _make(suffix, elements, on):
            block = PulseBlock(name='{0}_{1}'.format(name, suffix))
            for element in elements:
                block.append(element)
            ensemble = self._register_block(block, on, always_on_channel, pulser_channel,
                                            created_blocks, created_ensembles)
            return block, ensemble

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
        sync_element = self._get_pulser_off_sync_element(always_on_channel=always_on_channel)

        if use_pulser:
            trigger = _make('trigger_falling', [sync_element, falling_element], on=False)
            falling = _make('falling', [falling_element], on=False)
            rising = _make('rising', [rising_element], on=True)
        else:
            trigger = _make('trigger', [sync_element], on=False)
            falling = rising = None
        readout = _make('readout', [laser_element, delay_element, waiting_element], on=True)

        repolarization = None
        if repolarize and duty_cycle_correction:
            repolarize_laser_element = self._get_pulser_on_laser_only_readout_element(
                length=self.laser_length, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            repolarize_delay_element = self._get_pulser_on_idle_element(
                length=self.laser_delay, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            repolarization = _make(
                'repolarize', [rising_element, repolarize_laser_element, repolarize_delay_element],
                on=True)

        # Planned play order, with None marking a duty-cycle correction slot
        if not duty_cycle_correction:
            slot_points = set()
        elif distributed_correction:
            slot_points = set(range(len(points)))
        else:
            slot_points = {len(points) - 1}
        def _steps(item):
            # a point is either one (block, ensemble) or a list of (block, ensemble, repetitions)
            if isinstance(item, list):
                return item
            return [(item[0], item[1], 0)]

        plan = list()   # (block, ensemble, repetitions) steps, None = correction slot
        for ii, point in enumerate(points):
            if ii == 0:
                plan.extend(_steps(trigger))
            elif falling is not None:
                plan.extend(_steps(falling))
            plan.extend(_steps(point))
            if rising is not None:
                plan.extend(_steps(rising))
            plan.extend(_steps(readout))
            if ii in slot_points:
                plan.append(None)
                if repolarization is not None:
                    plan.extend(_steps(repolarization))

        block_plays = [(item[0], item[2] + 1) for item in plan if item is not None]
        correction_ensemble, reps_per_slot = self._prepare_distributed_duty_cycle_correction(
            block_plays, always_on_channel=always_on_channel, pulser_channel=correction_channel,
            duty_cycle=duty_cycle, name=name, preferred_on_base_length=self.wait_time,
            preferred_off_base_length=falling_time, num_slots=len(slot_points),
            created_blocks=created_blocks, created_ensembles=created_ensembles,
            enabled=duty_cycle_correction)

        sequence = PulseSequence(name=name, rotating_frame=False)
        slot_reps = iter(reps_per_slot)
        for item in plan:
            if item is None:
                reps = next(slot_reps)
                if reps is None:
                    continue
                sequence.append(correction_ensemble.name)
                sequence[-1].repetitions = reps
            else:
                sequence.append(item[1].name)
                sequence[-1].repetitions = item[2]

        # AWG step indices are 1-based: loop back onto the trigger block
        sequence[-1].go_to = 1
        sequence.refresh_parameters()
        return sequence

    def generate_dx_rabi_ao_trig(self, name='rabi_ao_trig', tau_start=10.0e-9, tau_step=10.0e-9,
                                  num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                                  duty_cycle_correction=True, duty_cycle=0.2, duty_cycle_channel='',
                                  distributed_correction=True, repolarize=True,
                                  rising_time=50e-6, falling_time=50e-6, alternating=False, alternating_mode=1,
                                  mw_switch_channel='', mw_switch_margin=50e-9):
        """
        Sequence-mode Rabi with an always-on channel and a duty-cycle-controlled pulser channel.
        The pulser is OFF during the MW pulse and ON during readout. See
        _build_dx_pulsed_sequence for the sequence structure.

        pulser_channel : str
            Channel held ON during rising, readout and repolarization. Leave empty if no
            pulser is used: then there are no rising/falling ramps, no duty-cycle correction
            and no repolarization, i.e. trigger -> [mw -> readout] per point.
        duty_cycle_correction : bool
            If True (default), correction blocks are added to hold duty_cycle. If False, no
            correction (and no repolarization) is played; the resulting duty cycle is only
            logged.
        duty_cycle : float
            Target fraction of the total sequence time with duty_cycle_channel HIGH.
        duty_cycle_channel : str
            Channel measured and driven by the duty-cycle correction. Empty (default) ->
            pulser_channel.
        distributed_correction : bool
            True (default): one correction slot after every point (normal or alternating),
            keeping the real-time duty cycle close to target throughout the sweep.
            False: a single correction block at the end of the sequence.
        repolarize : bool
            If True (default), every correction slot is followed by a repolarization block
            (rising -> laser without gate -> laser_delay, all pulser ON), so the polarization
            does not decay during long corrections before the next MW pulse.
        alternating : bool
            If True, each normal point is immediately followed by one alternating point.
        alternating_mode : int
            1 -> the alternating point replaces the MW pulse with an idle wait of the same
                 duration (MW never driven).
            2 -> the alternating point keeps the swept MW pulse and appends one additional
                 fixed pi-pulse (length self.rabi_period / 2, at self.microwave_frequency).
        mw_switch_channel : str
            Digital channel driving an RF switch in the MW path (e.g. an AWG marker 'd_ch1').
            Empty (default): no switch. Otherwise the channel is HIGH from mw_switch_margin
            before until mw_switch_margin after every MW pulse (group), and LOW otherwise, so
            AWG noise and carrier leakage are blocked between the pulses. Same option in all
            dx methods.
        mw_switch_margin : float
            Lead and lag time of the switch window around the MW pulses, in s. Must cover the
            switch's switching time (e.g. ZASWA-2-50DR+: 20 ns max). Adds 2 * margin to every
            MW block.

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        self._check_mw_switch_channel(mw_switch_channel)
        mw_frequency = self.microwave_frequency
        self.log.warning(f'Rabi drive frequency: {mw_frequency/1e9:.6f} GHz, '
                        f'amp={self.microwave_amplitude} V')

        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        tau_array = tau_start + np.arange(num_of_points) * tau_step

        if alternating and alternating_mode not in (1, 2):
            self.log.error(
                'alternating_mode must be 1 or 2 (got {0}); treating as 1.'.format(alternating_mode))
            alternating_mode = 1

        def _register(block_name, elements):
            block = PulseBlock(name=block_name)
            for element in elements:
                block.append(element)
            ensemble = self._register_block(block, False, always_on_channel, pulser_channel,
                                            created_blocks, created_ensembles)
            return block, ensemble

        points = list()
        for kk, tau in enumerate(tau_array):
            mw_elements = self._get_pulser_off_dx_mw_element_padded(
                length=tau, increment=0, amp=self.microwave_amplitude, freq=None, phase=0,
                always_on_channel=always_on_channel)
            mw_elements = self._gate_mw_elements(mw_elements, mw_switch_channel, mw_switch_margin, always_on_channel)
            points.append(_register('{0}_mw_{1}'.format(name, kk), mw_elements))

            if not alternating:
                continue
            if alternating_mode == 1:
                alt_elements = [self._get_pulser_off_idle_element(
                    length=max(tau, self._MW_ELEMENT_MIN_LENGTH), increment=0,
                    always_on_channel=always_on_channel)]
            else:
                alt_elements = self._get_pulser_off_dx_mw_element_padded(
                    length=tau, increment=0, amp=self.microwave_amplitude, freq=None, phase=0,
                    always_on_channel=always_on_channel)
                alt_elements += self._get_pulser_off_dx_mw_element_padded(
                    length=self.rabi_period / 2, increment=0, amp=self.microwave_amplitude,
                    freq=None, phase=0, always_on_channel=always_on_channel)
            if alternating_mode != 1:
                alt_elements = self._gate_mw_elements(alt_elements, mw_switch_channel, mw_switch_margin, always_on_channel)
            points.append(_register('{0}_alt_mw_{1}'.format(name, kk), alt_elements))

        rabi_sequence = self._build_dx_pulsed_sequence(
            name, points, always_on_channel=always_on_channel, pulser_channel=pulser_channel,
            duty_cycle=duty_cycle, duty_cycle_channel=duty_cycle_channel,
            rising_time=rising_time, falling_time=falling_time,
            duty_cycle_correction=duty_cycle_correction,
            distributed_correction=distributed_correction, repolarize=repolarize,
            created_blocks=created_blocks, created_ensembles=created_ensembles)

        rabi_sequence.measurement_information['alternating'] = alternating
        rabi_sequence.measurement_information['laser_ignore_list'] = list()
        rabi_sequence.measurement_information['controlled_variable'] = tau_array
        rabi_sequence.measurement_information['units'] = ('s', '')
        rabi_sequence.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        rabi_sequence.measurement_information['number_of_lasers'] = len(points)
        rabi_sequence.measurement_information['counting_length'] = self.laser_length + self.laser_delay

        created_sequences.append(rabi_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_dx_pulsedodmr_ao_trig(self, name='pODMR_ao_trig', freq_start=3.47e9, freq_stop=3.57e9,
                                        num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                                        duty_cycle_correction=True, duty_cycle=0.2, duty_cycle_channel='',
                                        distributed_correction=True, repolarize=True,
                                        rising_time=50e-6, falling_time=50e-6, alternating=False, alternating_mode=1,
                                        mw_switch_channel='', mw_switch_margin=50e-9):
        """
        Sequence-mode pulsed ODMR - identical structure to generate_dx_rabi_ao_trig, swept over
        frequency with a fixed pi-pulse length (self.rabi_period / 2) instead of swept tau.
        Requires self.rabi_period to already be calibrated. See generate_dx_rabi_ao_trig for
        the shared parameters.

        alternating_mode : int
            1 -> the alternating point replaces the pi-pulse with an idle wait of the same
                 duration (MW never driven). Identical for every frequency point, so a single
                 shared waveform is reused across the whole alternating sweep.
            2 -> the alternating point keeps the swept pi-pulse and appends one additional
                 fixed pi-pulse (length self.rabi_period / 2, at self.microwave_frequency).

        Returns
        -------
        created_blocks : list
        created_ensembles : list
        created_sequences : list
        """
        self._check_mw_switch_channel(mw_switch_channel)
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        freq_array = np.linspace(freq_start, freq_stop, num_of_points)
        pi_length = self.rabi_period / 2

        if alternating and alternating_mode not in (1, 2):
            self.log.error(
                'alternating_mode must be 1 or 2 (got {0}); treating as 1.'.format(alternating_mode))
            alternating_mode = 1

        def _register(block_name, elements):
            block = PulseBlock(name=block_name)
            for element in elements:
                block.append(element)
            ensemble = self._register_block(block, False, always_on_channel, pulser_channel,
                                            created_blocks, created_ensembles)
            return block, ensemble

        shared_alt_point = None
        if alternating and alternating_mode == 1:
            shared_alt_point = _register(name + '_alt_mw', [self._get_pulser_off_idle_element(
                length=max(pi_length, self._MW_ELEMENT_MIN_LENGTH), increment=0,
                always_on_channel=always_on_channel)])

        points = list()
        for kk, freq in enumerate(freq_array):
            mw_elements = self._get_pulser_off_dx_mw_element_padded(
                length=pi_length, increment=0, amp=self.microwave_amplitude, freq=freq, phase=0,
                always_on_channel=always_on_channel)
            mw_elements = self._gate_mw_elements(mw_elements, mw_switch_channel, mw_switch_margin, always_on_channel)
            points.append(_register('{0}_mw_{1}'.format(name, kk), mw_elements))

            if not alternating:
                continue
            if alternating_mode == 1:
                points.append(shared_alt_point)
            else:
                alt_elements = self._get_pulser_off_dx_mw_element_padded(
                    length=pi_length, increment=0, amp=self.microwave_amplitude, freq=freq,
                    phase=0, always_on_channel=always_on_channel)
                alt_elements += self._get_pulser_off_dx_mw_element_padded(
                    length=pi_length, increment=0, amp=self.microwave_amplitude, freq=None,
                    phase=0, always_on_channel=always_on_channel)
                alt_elements = self._gate_mw_elements(alt_elements, mw_switch_channel, mw_switch_margin, always_on_channel)
                points.append(_register('{0}_alt_mw_{1}'.format(name, kk), alt_elements))

        pulsedodmr_sequence = self._build_dx_pulsed_sequence(
            name, points, always_on_channel=always_on_channel, pulser_channel=pulser_channel,
            duty_cycle=duty_cycle, duty_cycle_channel=duty_cycle_channel,
            rising_time=rising_time, falling_time=falling_time,
            duty_cycle_correction=duty_cycle_correction,
            distributed_correction=distributed_correction, repolarize=repolarize,
            created_blocks=created_blocks, created_ensembles=created_ensembles)

        pulsedodmr_sequence.measurement_information['alternating'] = alternating
        pulsedodmr_sequence.measurement_information['laser_ignore_list'] = list()
        pulsedodmr_sequence.measurement_information['controlled_variable'] = freq_array
        pulsedodmr_sequence.measurement_information['units'] = ('Hz', '')
        pulsedodmr_sequence.measurement_information['labels'] = ('Frequency', 'Signal')
        pulsedodmr_sequence.measurement_information['number_of_lasers'] = len(points)
        pulsedodmr_sequence.measurement_information['counting_length'] = self.laser_length + self.laser_delay

        created_sequences.append(pulsedodmr_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_dx_t1_ao_trig(self, name='t1_ao_trig', tau_start=1.0e-6, tau_step=1.0e-6,
                                num_of_points=50, always_on_channel='d_ch15', pulser_channel='d_ch3',
                                duty_cycle_correction=True, duty_cycle=0.2, duty_cycle_channel='',
                                distributed_correction=True, repolarize=True,
                                rising_time=50e-6, falling_time=50e-6, alternating=False,
                                mw_switch_channel='', mw_switch_margin=50e-9):
        """
        Sequence-mode T1 with linearly spaced tau, analogous to the basic generate_t1, with the
        pulser/duty-cycle features of generate_dx_rabi_ao_trig (see there and
        _build_dx_pulsed_sequence for the shared parameters and the sequence structure).

        Per point the NV relaxes in the dark for tau with the pulser OFF (like the MW pulses of
        the Rabi/pODMR methods). Note that the readout's wait_time and the rising/falling ramps
        add a constant dark time to every point.

        alternating : bool
            If True, each normal point is followed by an alternating point with a pi-pulse
            (self.rabi_period / 2 at self.microwave_frequency) right before the tau wait.

        Long waits are built from a short idle block looped via sequence repetitions plus a
        small per-point remainder block, so tau can reach milliseconds without exhausting the
        AWG waveform memory. The controlled variable holds the actually played tau values
        (rounded to the pulse generator granularity).
        """
        tau_array = tau_start + np.arange(num_of_points) * tau_step
        return self._generate_dx_t1(
            name, tau_array, always_on_channel, pulser_channel, duty_cycle_correction, duty_cycle,
            duty_cycle_channel, distributed_correction, repolarize, rising_time, falling_time,
            alternating, mw_switch_channel, mw_switch_margin)

    def generate_dx_t1_exponential_ao_trig(self, name='t1_exp_ao_trig', tau_start=1.0e-6,
                                            tau_end=1.0e-3, num_of_points=50,
                                            always_on_channel='d_ch15', pulser_channel='d_ch3',
                                            duty_cycle_correction=True, duty_cycle=0.2,
                                            duty_cycle_channel='', distributed_correction=True,
                                            repolarize=True, rising_time=50e-6, falling_time=50e-6,
                                            alternating=False, mw_switch_channel='',
                                            mw_switch_margin=50e-9):
        """
        Sequence-mode T1 with exponentially (log) spaced tau from tau_start to tau_end,
        analogous to the basic generate_t1_exponential. Otherwise identical to
        generate_dx_t1_ao_trig (see there).
        """
        if tau_start == 0.0:
            tau_array = np.insert(np.geomspace(1e-9, tau_end, num_of_points - 1), 0, 0.0)
        else:
            tau_array = np.geomspace(tau_start, tau_end, num_of_points)
        return self._generate_dx_t1(
            name, tau_array, always_on_channel, pulser_channel, duty_cycle_correction, duty_cycle,
            duty_cycle_channel, distributed_correction, repolarize, rising_time, falling_time,
            alternating, mw_switch_channel, mw_switch_margin)

    def _generate_dx_t1(self, name, tau_array, always_on_channel, pulser_channel,
                        duty_cycle_correction, duty_cycle, duty_cycle_channel,
                        distributed_correction, repolarize, rising_time, falling_time,
                        alternating, mw_switch_channel='', mw_switch_margin=50e-9):
        """ Shared implementation of generate_dx_t1_ao_trig / generate_dx_t1_exponential_ao_trig.
        With mw_switch_channel, the switch is open only around the pi-pulse of the alternating
        points (lead + pulse + lag); the tau wait starts after the lag margin. """
        self._check_mw_switch_channel(mw_switch_channel)
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        sample_rate = self.pulse_generator_settings['sample_rate']
        min_samples = int(self.pulse_generator_constraints.waveform_length.min)
        step_samples = int(self.pulse_generator_constraints.waveform_length.step)

        def _register(block_name, elements):
            block = PulseBlock(name=block_name)
            for element in elements:
                block.append(element)
            ensemble = self._register_block(block, False, always_on_channel, pulser_channel,
                                            created_blocks, created_ensembles)
            length_s = sum(elem.init_length_s for elem in block.element_list)
            return block, ensemble, length_s

        def _idle(length_s):
            return self._get_pulser_off_idle_element(
                length=length_s, increment=0, always_on_channel=always_on_channel)

        # Shared base block of the long waits: ~1 us, a whole number of granularity steps (so it
        # is not padded), lengthened if needed to stay below the AWG loop count limit.
        base_samples = max(int(round(1e-6 * sample_rate)),
                           int(np.ceil(max(tau_array) * sample_rate / self._MAX_SEQUENCE_LOOP_COUNT)))
        base_samples = max(step_samples, int(np.ceil(base_samples / step_samples)) * step_samples)
        base_samples = max(base_samples, int(np.ceil(min_samples / step_samples)) * step_samples)
        base_block = base_ensemble = None

        if alternating:
            pi_elements = self._get_pulser_off_dx_mw_element_padded(
                length=self.rabi_period / 2, increment=0, amp=self.microwave_amplitude, freq=None,
                phase=0, always_on_channel=always_on_channel)
            pi_elements = self._gate_mw_elements(pi_elements, mw_switch_channel, mw_switch_margin, always_on_channel)
            pi_length_s = sum(elem.init_length_s for elem in pi_elements)

        def _wait_point(label, kk, tau, prefix_elements=(), prefix_length_s=0.0):
            """ Steps playing prefix_elements followed by a tau wait. Returns (steps, tau played). """
            nonlocal base_block, base_ensemble
            tau_samples = int(round(tau * sample_rate))
            # remainder block (prefix + rest of the wait) must reach the minimum waveform length
            prefix_samples = int(round(prefix_length_s * sample_rate))
            rest_needed = max(0, min_samples - prefix_samples)
            n_base = max(0, (tau_samples - rest_needed) // base_samples)
            rest_samples = tau_samples - n_base * base_samples   # >= rest_needed >= 0
            elements = list(prefix_elements)
            if rest_samples > 0:
                elements.append(_idle(rest_samples / sample_rate))
            if not elements:
                elements.append(_idle(min_samples / sample_rate))
            block, ensemble, _ = _register('{0}_{1}_{2:d}'.format(name, label, kk), elements)
            played_s = (sum(elem.init_length_s for elem in block.element_list) - prefix_length_s
                        + n_base * base_samples / sample_rate)
            steps = [(block, ensemble, 0)]
            if n_base > 0:
                if base_block is None:
                    base_block, base_ensemble, _ = _register(
                        '{0}_wait_base'.format(name), [_idle(base_samples / sample_rate)])
                steps.append((base_block, base_ensemble, n_base - 1))
            return steps, played_s

        points = list()
        played_taus = list()
        for kk, tau in enumerate(tau_array):
            steps, played = _wait_point('wait', kk, tau)
            points.append(steps)
            played_taus.append(played)
            if alternating:
                steps, _ = _wait_point('alt_wait', kk, tau, prefix_elements=pi_elements,
                                       prefix_length_s=pi_length_s)
                points.append(steps)

        t1_sequence = self._build_dx_pulsed_sequence(
            name, points, always_on_channel=always_on_channel, pulser_channel=pulser_channel,
            duty_cycle=duty_cycle, duty_cycle_channel=duty_cycle_channel,
            rising_time=rising_time, falling_time=falling_time,
            duty_cycle_correction=duty_cycle_correction,
            distributed_correction=distributed_correction, repolarize=repolarize,
            created_blocks=created_blocks, created_ensembles=created_ensembles)

        t1_sequence.measurement_information['alternating'] = alternating
        t1_sequence.measurement_information['laser_ignore_list'] = list()
        t1_sequence.measurement_information['controlled_variable'] = np.array(played_taus)
        t1_sequence.measurement_information['units'] = ('s', '')
        t1_sequence.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
        t1_sequence.measurement_information['number_of_lasers'] = len(points)
        t1_sequence.measurement_information['counting_length'] = self.laser_length + self.laser_delay

        created_sequences.append(t1_sequence)
        return created_blocks, created_ensembles, created_sequences

    def generate_dx_cw_odmr_ao_trig(self, name='cw_odmr_ao_trig', freq_start=3.4e9, freq_stop=3.6e9,
                                     num_of_points=50, mw_amp=0.2, mw_length=10e-6,
                                     always_on_channel='d_ch15', pulser_channel='d_ch3', pulser_mode=1,
                                     duty_cycle_correction=True, duty_cycle=0.2,
                                     rising_time=50e-6, falling_time=50e-6,
                                     alternating=False, alternating_mode=1,
                                     mw_switch_channel='', mw_switch_margin=50e-9):
        """
        CW ODMR sequence, extended with an always-on channel and a duty-cycle-controlled pulser
        channel.

        pulser_channel : str
            Leave empty if no pulser is used: then there are no rising/falling ramps and no
            duty-cycle correction (pulser_mode and duty_cycle_correction are ignored), i.e.
            trigger -> [mw -> readout] per point.

        duty_cycle_correction : bool
            If True (default), correction blocks are added to hold duty_cycle. If False, no
            correction is played; the resulting duty cycle is only logged.

        pulser_mode : 0 -> pulser_channel is never driven by the mw/readout blocks themselves.
                      However, the duty-cycle correction may still briefly drive pulser_channel
                      HIGH to hit the requested duty cycle - to absorb the resulting abrupt
                      transition, a falling ramp is still inserted before every mw AND every
                      alt_mw element even in this mode. The very first falling block carries
                      the trigger (every sequence must start with a trigger pulse).
                      1 -> pulser_channel is HIGH for the entire mw_block, with a rising ramp
                      inserted before every mw/alt_mw element and a falling ramp inserted after
                      every readout element (default). The very first rising block carries the
                      trigger.

        Sequence structure (pulser_mode == 1): trigger (sync merged into a one-off rising
        block, serving as point 0's rising) -> [rising -> mw -> readout -> falling] per point
        -> [duty-cycle correction, if needed] -> loop back.
        Sequence structure (pulser_mode == 0): trigger (sync merged into a one-off falling
        block, serving as point 0's falling) -> [falling -> mw -> readout] per point ->
        [duty-cycle correction, if needed] -> loop back.

        Normal and alternating points are strictly interleaved when alternating=True, and the
        duty-cycle correction is distributed across one insertion slot immediately after EVERY
        individual point (normal or alternating), not just after each pair - see
        generate_dx_rabi_ao_trig's docstring for the full rationale.

        alternating : bool
            If True, each normal point is immediately followed by one alternating partner
            point.
        alternating_mode : int
            Only mode 1 is supported for CW ODMR (the microwave drive is replaced by a wait of
            the same duration; MW never driven). Identical for every frequency point, so a
            single shared waveform is reused across the whole alternating sweep. Passing 2
            logs an error and falls back to mode 1.

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

        self._check_mw_switch_channel(mw_switch_channel)

        # Without a pulser there is nothing to ramp or correct (see pulser_channel above)
        use_pulser = bool(pulser_channel)
        if not use_pulser:
            pulser_mode = 0
            duty_cycle_correction = False

        if pulser_mode == 1:
            waiting_element = self._get_pulser_on_idle_element(
                length=self.wait_time, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        else:
            waiting_element = self._get_pulser_off_idle_element(
                length=self.wait_time, increment=0, always_on_channel=always_on_channel)
        delay_element_on = self._get_pulser_on_delay_gate_element(
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        delay_element_off = self._get_pulser_off_delay_gate_element(
            always_on_channel=always_on_channel)

        # ── Readout (shared): waiting only, pulser held according to pulser_mode ────────────
        readout_block = PulseBlock(name='{0}_readout'.format(name))
        readout_block.append(waiting_element)
        self._pad_ensemble_to_granularity(
            readout_block, on=(pulser_mode == 1),
            always_on_channel=always_on_channel, pulser_channel=pulser_channel)
        created_blocks.append(readout_block)

        readout_ensemble = PulseBlockEnsemble(name='{0}_readout'.format(name), rotating_frame=False)
        readout_ensemble.append((readout_block.name, 0))
        created_ensembles.append(readout_ensemble)

        # ── One mw+laser+delay ensemble per frequency point ──────────────────
        mw_blocks = dict()
        mw_ensembles = dict()
        for kk, freq in enumerate(freq_array):
            if pulser_mode == 1:
                mw_laser_gate_element = self._get_pulser_on_dx_mw_laser_gate_element(
                    length=mw_length, increment=0, amp=mw_amp, freq=freq, phase=0,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel)
                delay_elem = delay_element_on
            else:
                mw_laser_gate_element = self._get_pulser_off_dx_mw_laser_gate_element(
                    length=mw_length, increment=0, amp=mw_amp, freq=freq, phase=0,
                    always_on_channel=always_on_channel)
                delay_elem = delay_element_off

            mw_block = PulseBlock(name='{0}_mw_{1}'.format(name, kk))
            if mw_switch_channel:
                # switch open: lead margin, the whole MW+laser element, and the following delay
                # element (laser_delay, normally >> the switch's switching time) as lag
                lead = self._gate_mw_elements([], mw_switch_channel, mw_switch_margin,
                                              always_on_channel, pulser_on=(pulser_mode == 1),
                                              pulser_channel=pulser_channel)[0]
                if pulser_mode == 1:
                    delay_elem = self._get_pulser_on_delay_gate_element(
                        always_on_channel=always_on_channel, pulser_channel=pulser_channel)
                else:
                    delay_elem = self._get_pulser_off_delay_gate_element(
                        always_on_channel=always_on_channel)
                self._set_channel_high(mw_laser_gate_element, mw_switch_channel)
                self._set_channel_high(delay_elem, mw_switch_channel)
                mw_block.append(lead)
            mw_block.append(mw_laser_gate_element)
            mw_block.append(delay_elem)
            self._pad_ensemble_to_granularity(
                mw_block, on=(pulser_mode == 1),
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(mw_block)
            mw_blocks[kk] = mw_block

            mw_ensembles[kk] = PulseBlockEnsemble(name='{0}_mw_{1}'.format(name, kk), rotating_frame=False)
            mw_ensembles[kk].append((mw_block.name, 0))
            created_ensembles.append(mw_ensembles[kk])

        # ── Alternating mw+laser+delay ensemble (shared across all points, only if
        #     alternating - only mode 1 is supported here) ────────────────────────────────
        alt_mw_block = None
        alt_mw_ensemble = None
        if alternating:
            if alternating_mode != 1:
                self.log.error(
                    'alternating_mode must be 1 for generate_dx_cw_odmr_ao_trig (got {0}); CW '
                    'ODMR only supports replacing the microwave drive with a wait, not an extra '
                    'pi-pulse. Falling back to alternating_mode = 1.'.format(alternating_mode))

            if pulser_mode == 1:
                alt_laser_gate_element = self._get_pulser_on_laser_gate_element(
                    length=mw_length, increment=0,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel)
                alt_delay_elem = delay_element_on
            else:
                alt_laser_gate_element = self._get_pulser_off_laser_gate_element(
                    length=mw_length, increment=0, always_on_channel=always_on_channel)
                alt_delay_elem = delay_element_off

            alt_mw_block = PulseBlock(name=name + '_alt_mw')
            alt_mw_block.append(alt_laser_gate_element)
            alt_mw_block.append(alt_delay_elem)
            self._pad_ensemble_to_granularity(
                alt_mw_block, on=(pulser_mode == 1),
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(alt_mw_block)

            alt_mw_ensemble = PulseBlockEnsemble(name=name + '_alt_mw', rotating_frame=False)
            alt_mw_ensemble.append((alt_mw_block.name, 0))
            created_ensembles.append(alt_mw_ensemble)

        sync_element = self._get_pulser_off_sync_element(always_on_channel=always_on_channel)

        cw_odmr_sequence = PulseSequence(name=name, rotating_frame=False)

        if pulser_mode == 1:
            # ── Rising/falling ramps: rising before every mw/alt_mw element, falling after
            #    every readout element ───────────────────────────────────────────────────────
            rising_element = self._get_pulser_on_idle_element(
                length=rising_time, increment=0,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            falling_element = self._get_pulser_off_idle_element(
                length=falling_time, increment=0, always_on_channel=always_on_channel)

            rising_block = PulseBlock(name='{0}_rising'.format(name))
            rising_block.append(rising_element)
            self._pad_ensemble_to_granularity(
                rising_block, on=True, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(rising_block)

            rising_ensemble = PulseBlockEnsemble(name='{0}_rising'.format(name), rotating_frame=False)
            rising_ensemble.append((rising_block.name, 0))
            created_ensembles.append(rising_ensemble)

            falling_block = PulseBlock(name='{0}_falling'.format(name))
            falling_block.append(falling_element)
            self._pad_ensemble_to_granularity(
                falling_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(falling_block)

            falling_ensemble = PulseBlockEnsemble(name='{0}_falling'.format(name), rotating_frame=False)
            falling_ensemble.append((falling_block.name, 0))
            created_ensembles.append(falling_ensemble)

            # ── Trigger: sync (pulser OFF) merged into a one-off rising block, serving as
            #    point 0's rising ────────────────────────────────────────────────────────────
            first_rising_block = PulseBlock(name='{0}_trigger_rising'.format(name))
            first_rising_block.append(sync_element)
            first_rising_block.append(rising_element)
            self._pad_ensemble_to_granularity(
                first_rising_block, on=True,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(first_rising_block)

            first_rising_ensemble = PulseBlockEnsemble(name='{0}_trigger_rising'.format(name), rotating_frame=False)
            first_rising_ensemble.append((first_rising_block.name, 0))
            created_ensembles.append(first_rising_ensemble)

            if alternating:
                num_slots = 2 * num_of_points
                block_plays = []
                for kk in range(num_of_points):
                    block_plays.append((first_rising_block if kk == 0 else rising_block, 1))
                    block_plays.append((mw_blocks[kk], 1))
                    block_plays.append((readout_block, 1))
                    block_plays.append((falling_block, 1))
                    block_plays.append((rising_block, 1))
                    block_plays.append((alt_mw_block, 1))
                    block_plays.append((readout_block, 1))
                    block_plays.append((falling_block, 1))

                correction_ensemble, reps_per_slot = self._prepare_distributed_duty_cycle_correction(
                    block_plays, always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                    duty_cycle=duty_cycle, name=name, preferred_on_base_length=self.wait_time,
                    preferred_off_base_length=falling_time, num_slots=num_slots,
                    created_blocks=created_blocks, created_ensembles=created_ensembles,
                    enabled=duty_cycle_correction)

                for kk, freq in enumerate(freq_array):
                    cw_odmr_sequence.append(first_rising_ensemble.name if kk == 0 else rising_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(mw_ensembles[kk].name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(readout_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(falling_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    if reps_per_slot[2 * kk] is not None:
                        cw_odmr_sequence.append(correction_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = reps_per_slot[2 * kk]

                    cw_odmr_sequence.append(rising_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(alt_mw_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(readout_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(falling_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    if reps_per_slot[2 * kk + 1] is not None:
                        cw_odmr_sequence.append(correction_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = reps_per_slot[2 * kk + 1]

                cw_odmr_sequence[-1].go_to = 1
            else:
                for kk, freq in enumerate(freq_array):
                    cw_odmr_sequence.append(first_rising_ensemble.name if kk == 0 else rising_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(mw_ensembles[kk].name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(readout_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(falling_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                cw_odmr_sequence[-1].go_to = 1

                self._apply_duty_cycle_correction(
                    cw_odmr_sequence, created_blocks, created_ensembles,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel, duty_cycle=duty_cycle,
                    name=name, preferred_on_base_length=self.wait_time, preferred_off_base_length=falling_time,
                    enabled=duty_cycle_correction)
        else:
            # pulser_mode == 0: mw/readout/alt_mw blocks never drive pulser_channel
            # themselves, but the duty-cycle correction still may, briefly, to hit the
            # requested duty cycle. A falling ramp is inserted before every mw AND every
            # alt_mw element to absorb that abrupt transition, regardless of which correction
            # slot preceded it. The trigger is merged into the very first falling block.
            # Without a pulser there are no falling ramps at all and the trigger is a block of
            # its own.
            falling_block = None
            falling_ensemble = None
            trigger_suffix = 'trigger_falling' if use_pulser else 'trigger'
            first_falling_block = PulseBlock(name='{0}_{1}'.format(name, trigger_suffix))
            first_falling_block.append(sync_element)
            if use_pulser:
                falling_element = self._get_pulser_off_idle_element(
                    length=falling_time, increment=0, always_on_channel=always_on_channel)
                first_falling_block.append(falling_element)

                falling_block = PulseBlock(name='{0}_falling'.format(name))
                falling_block.append(falling_element)
                self._pad_ensemble_to_granularity(
                    falling_block, on=False, always_on_channel=always_on_channel, pulser_channel=pulser_channel)
                created_blocks.append(falling_block)

                falling_ensemble = PulseBlockEnsemble(name='{0}_falling'.format(name), rotating_frame=False)
                falling_ensemble.append((falling_block.name, 0))
                created_ensembles.append(falling_ensemble)

            self._pad_ensemble_to_granularity(
                first_falling_block, on=False,
                always_on_channel=always_on_channel, pulser_channel=pulser_channel)
            created_blocks.append(first_falling_block)

            first_falling_ensemble = PulseBlockEnsemble(name=first_falling_block.name, rotating_frame=False)
            first_falling_ensemble.append((first_falling_block.name, 0))
            created_ensembles.append(first_falling_ensemble)

            if alternating:
                num_slots = 2 * num_of_points
                block_plays = []
                for kk in range(num_of_points):
                    if kk == 0:
                        block_plays.append((first_falling_block, 1))
                    elif falling_block is not None:
                        block_plays.append((falling_block, 1))
                    block_plays.append((mw_blocks[kk], 1))
                    block_plays.append((readout_block, 1))
                    if falling_block is not None:
                        block_plays.append((falling_block, 1))
                    block_plays.append((alt_mw_block, 1))
                    block_plays.append((readout_block, 1))

                correction_ensemble, reps_per_slot = self._prepare_distributed_duty_cycle_correction(
                    block_plays, always_on_channel=always_on_channel, pulser_channel=pulser_channel,
                    duty_cycle=duty_cycle, name=name, preferred_on_base_length=self.wait_time,
                    preferred_off_base_length=falling_time, num_slots=num_slots,
                    created_blocks=created_blocks, created_ensembles=created_ensembles,
                    enabled=duty_cycle_correction)

                for kk, freq in enumerate(freq_array):
                    if kk == 0 or falling_ensemble is not None:
                        cw_odmr_sequence.append(
                            first_falling_ensemble.name if kk == 0 else falling_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(mw_ensembles[kk].name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(readout_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    if reps_per_slot[2 * kk] is not None:
                        cw_odmr_sequence.append(correction_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = reps_per_slot[2 * kk]

                    if falling_ensemble is not None:
                        cw_odmr_sequence.append(falling_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(alt_mw_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(readout_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                    if reps_per_slot[2 * kk + 1] is not None:
                        cw_odmr_sequence.append(correction_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = reps_per_slot[2 * kk + 1]

                cw_odmr_sequence[-1].go_to = 1
            else:
                for kk, freq in enumerate(freq_array):
                    if kk == 0 or falling_ensemble is not None:
                        cw_odmr_sequence.append(
                            first_falling_ensemble.name if kk == 0 else falling_ensemble.name)
                        cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(mw_ensembles[kk].name)
                    cw_odmr_sequence[-1].repetitions = 0

                    cw_odmr_sequence.append(readout_ensemble.name)
                    cw_odmr_sequence[-1].repetitions = 0

                cw_odmr_sequence[-1].go_to = 1

                self._apply_duty_cycle_correction(
                    cw_odmr_sequence, created_blocks, created_ensembles,
                    always_on_channel=always_on_channel, pulser_channel=pulser_channel, duty_cycle=duty_cycle,
                    name=name, preferred_on_base_length=self.wait_time, preferred_off_base_length=falling_time,
                    enabled=duty_cycle_correction)

        cw_odmr_sequence.refresh_parameters()

        cw_odmr_sequence.measurement_information['alternating'] = alternating
        cw_odmr_sequence.measurement_information['laser_ignore_list'] = list()
        cw_odmr_sequence.measurement_information['controlled_variable'] = freq_array
        cw_odmr_sequence.measurement_information['units'] = ('Hz', '')
        cw_odmr_sequence.measurement_information['labels'] = ('Frequency', 'Signal')
        cw_odmr_sequence.measurement_information['number_of_lasers'] = 2 * len(freq_array) if alternating else len(freq_array)
        cw_odmr_sequence.measurement_information['counting_length'] = (mw_length + delay_element_on.init_length_s)

        created_sequences.append(cw_odmr_sequence)
        return created_blocks, created_ensembles, created_sequences