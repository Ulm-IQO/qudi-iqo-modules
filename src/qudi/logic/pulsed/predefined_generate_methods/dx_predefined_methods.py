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

        Confirmed working access pattern against this setup's running qudi instance (qudi's
        ModuleManager is dict-like via __getitem__, returning a ManagedModule wrapper whose
        .instance attribute holds the live module object):

            Qudi.instance().module_manager['mw_always_on'].instance

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

        Optional generation parameters (set via the pulsed GUI's generation-parameters panel, or
        e.g. pulsed_master_logic.set_generation_parameters(...) from a console):
            microwave_iq_max_frequency     : Maximum usable |f_IQ| of your I/Q mixer/AWG
                                              combination, in Hz. Falls back to a Nyquist-based
                                              estimate from the AWG sample rate if not set.
            microwave_iq_sideband_sign     : +1 (default) if your mixer produces the upper
                                              sideband, -1 if lower sideband.
            microwave_hardware_module_name : overrides which qudi module to query (default:
                                              "mw_always_on").

        Raises a ValueError (logged) if the requested target_frequency is not reachable.

        @param float target_frequency: desired ABSOLUTE output frequency in Hz. Defaults to
                                       self.microwave_frequency if not given (e.g. for methods
                                       that use a single, fixed MW frequency for the whole
                                       ensemble, such as Rabi). Methods that sweep frequency
                                       (e.g. CW ODMR) should pass the current sweep point
                                       explicitly here.
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
                pass  # unexpected constraints shape - skip this particular sanity check silently

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
                as "freq" on every call - each element then gets its own, independently computed
                and validated I/Q offset.

            The physical IQ mixer implements:
                V_RF(t) = I(t)*cos(2*pi*f_LO*t) - Q(t)*sin(2*pi*f_LO*t)
            To produce a single sideband at f_LO + f_IQ with a given amplitude/phase, the
            baseband envelopes fed to the I and Q ports must be in genuine time-domain quadrature:
                I(t) = amp * cos(2*pi*f_IQ*t + phase)
                Q(t) = amp * sin(2*pi*f_IQ*t + phase)
            which correctly reduces to a pure DC phase-shifter (I=amp*cos(phase),
            Q=amp*sin(phase)) when f_IQ = 0.

            f_IQ is computed and validated live against the actual signal generator frequency and
            your I/Q mixer/AWG bandwidth via _get_iq_modulation_frequency() - see that method's
            docstring for details and the relevant generation parameters.

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

            # Compute (and validate) the I/Q baseband frequency needed to reach the requested
            # absolute target frequency ("freq", or self.microwave_frequency if None), given the
            # LIVE signal generator frequency read directly off hardware. Raises ValueError if
            # unreachable.
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

        This is required because sufficiently short digital pulses (e.g. driving a fast MW
        switch channel) cannot be reliably generated by the PulseBlaster hardware. Prepending an
        idle padding element of length (min_length - length) restores a combined minimum
        duration of min_length, while leaving the MW pulse itself at its physically intended
        (possibly sub-60-ns) length - i.e. the padding is inserted as extra free evolution time
        BEFORE the MW pulse, not by artificially stretching the pulse itself.

        @param float length: nominal MW pulse duration in seconds.
        @param float increment: MW pulse duration increment in seconds, passed straight through
                                 to the MW element (typically 0 in sequence mode, where each
                                 point is built as its own explicit ensemble rather than swept
                                 via hardware increment).
        @param min_length: minimum combined duration threshold, in seconds. Defaults to
                            self._MW_ELEMENT_MIN_LENGTH if not given.
        (amp, freq, phase, envelope: passed straight through to _get_dx_mw_element)

        @return list of PulseBlockElement: [mw_element] if no padding is needed, otherwise
                [idle_padding_element, mw_element] (in that order - append both, in order, to
                your block).

        Note on analog MW channels: this padding is only strictly necessary when
        self.microwave_channel is a digital channel (subject to the PulseBlaster's clock). For
        analog channels the AWG generates the waveform directly and this constraint may not
        apply - but padding is harmless (just adds idle time) so it is applied unconditionally
        here for simplicity.
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
            configured, gate channel activation on top of it. Analogous to the pre-existing
            _get_mw_laser_gate_element/_get_mw_element pair, but routed through the I/Q-aware
            _get_dx_mw_element instead of the single-channel, direct-synthesis _get_mw_element.

            Supports both fixed-frequency (freq=None -> self.microwave_frequency) and
            frequency-swept (explicit "freq" per call, e.g. CW ODMR) use cases - see
            _get_dx_mw_element docstring.

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
    #                             Generation methods for waveforms                                 #
    ################################################################################################

    # def generate_dx_rabi_one(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9, num_of_points=50):
    #     """Generates a Rabi pulse block ensemble where the pulse length is varied linearly.

    #     Parameters
    #     ----------
    #     name : str
    #         Name of the PulseBlockEnsemble to be generated.
    #     tau_start : float
    #         Length of the first pulse in seconds.
    #     tau_step : float
    #         Increment of the pulse length in seconds.
    #     num_of_points : int
    #         Number of tau steps to be generated.

    #     Returns
    #     -------
    #     created_blocks : list
    #         List of PulseBlock objects created.
    #     created_ensembles : list
    #         List of PulseBlockEnsemble objects created.
    #     created_sequences : list
    #         List of PulseSequence objects created.
    #     """
    #     created_blocks = list()
    #     created_ensembles = list()
    #     created_sequences = list()

    #     # get tau array for measurement ticks
    #     tau_array = tau_start + np.arange(num_of_points) * tau_step

    #     # create the laser_mw element
    #     mw_element = self._get_dx_mw_element(length=tau_start,
    #                                          increment=tau_step,
    #                                          amp=self.microwave_amplitude,
    #                                          phase=0)
    #     waiting_element = self._get_idle_element(length=self.wait_time,
    #                                              increment=0)
    #     laser_element = self._get_laser_gate_element(length=self.laser_length,
    #                                                  increment=0)
    #     delay_element = self._get_delay_gate_element()

    #     # Create block and append to created_blocks list
    #     rabi_block = PulseBlock(name=name)
    #     rabi_block.append(mw_element)
    #     rabi_block.append(laser_element)
    #     rabi_block.append(delay_element)
    #     rabi_block.append(waiting_element)
    #     created_blocks.append(rabi_block)

    #     # Create block ensemble
    #     block_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
    #     self._add_trigger(created_blocks=created_blocks, block_ensemble=block_ensemble)
    #     block_ensemble.append((rabi_block.name, num_of_points - 1))

    #     # add metadata to invoke settings later on
    #     block_ensemble.measurement_information['alternating'] = False
    #     block_ensemble.measurement_information['laser_ignore_list'] = list()
    #     block_ensemble.measurement_information['controlled_variable'] = tau_array
    #     block_ensemble.measurement_information['units'] = ('s', '')
    #     block_ensemble.measurement_information['labels'] = ('Tau<sub>pulse spacing</sub>', 'Signal')
    #     block_ensemble.measurement_information['number_of_lasers'] = num_of_points
    #     block_ensemble.measurement_information['counting_length'] = self._get_ensemble_count_length(
    #         ensemble=block_ensemble, created_blocks=created_blocks)

    #     # Append ensemble to created_ensembles list
    #     created_ensembles.append(block_ensemble)
    #     return created_blocks, created_ensembles, created_sequences

    def generate_dx_rabi(self, name='rabi', tau_start=10.0e-9, tau_step=10.0e-9, num_of_points=50):
        """
        Rabi sequence for combined AWG + PulseBlaster setup, structured analogously to
        generate_dx_cw_odmr: a leading sync/trigger step (TWAIT=ON forced by the interfuse),
        followed by one ensemble per tau point (MW pulse of varying length, then separate laser
        readout), looping back to the trigger step at the end.

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

        # ── Tau array (linearly spaced) ──────────────────────────────────────────
        tau_array = tau_start + np.arange(num_of_points) * tau_step

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

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================

        rabi_sequence = PulseSequence(name=name, rotating_frame=False)

        # Step 1: trigger — TWAIT=ON is forced on this step by the interfuse's
        # write_sequence() method, making it equivalent to TRIG mode in waveform mode.
        rabi_sequence.append(trigger_ensemble.name)
        rabi_sequence[-1].repetitions = 0

        # Steps 2..N+1: one per tau point
        for kk, tau in enumerate(tau_array):
            rabi_sequence.append(rabi_ensembles[kk].name)
            rabi_sequence[-1].repetitions = 0

        # After last readout: return to trigger step and wait for next PB trigger
        rabi_sequence[-1].go_to = 1

        # ── Finalise ──────────────────────────────────────────────────────────────
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
        Pulsed ODMR sequence for combined AWG + PulseBlaster setup, structured analogously to
        generate_dx_cw_odmr: a leading sync/trigger step (TWAIT=ON forced by the interfuse),
        followed by one ensemble per frequency point (MW pi-pulse, then separate laser
        readout), looping back to the trigger step at the end.

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

        # ── Frequency array (linearly spaced) ────────────────────────────────────
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

        # =========================================================================
        # SEQUENCE CONSTRUCTION
        # =========================================================================

        pulsedodmr_sequence = PulseSequence(name=name, rotating_frame=False)

        # Step 1: trigger — TWAIT=ON is forced on this step by the interfuse's
        # write_sequence() method, making it equivalent to TRIG mode in waveform mode.
        pulsedodmr_sequence.append(trigger_ensemble.name)
        pulsedodmr_sequence[-1].repetitions = 0

        # Steps 2..N+1: one per frequency point
        for kk, freq in enumerate(freq_array):
            pulsedodmr_sequence.append(pulsedodmr_ensembles[kk].name)
            pulsedodmr_sequence[-1].repetitions = 0

        # After last readout: return to trigger step and wait for next PB trigger
        pulsedodmr_sequence[-1].go_to = 1

        # ── Finalise ──────────────────────────────────────────────────────────────
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

