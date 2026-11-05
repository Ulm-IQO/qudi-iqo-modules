# -*- coding: utf-8 -*-
"""
Master logic to combine sequence_generator_logic and pulsed_measurement_logic to be
used with a single GUI.

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
from PySide6 import QtCore

from qudi.core.connector import Connector
from qudi.core.module import LogicBase
from qudi.logic.pulsed.pulsed_measurement_logic import PulsedMeasurementLogic
from qudi.logic.pulsed.sequence_generator_logic import SequenceGeneratorLogic
from qudi.logic.pulsed.pulsed_data.pulsed_master_logic_data import PulsedMasterStatus, FitContainers
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    FastCounterSettings,
    MicrowaveSettings,
    ReadoutSettings,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import (
    generation_parameters_class,
    PulseGeneratorSettings,
)
from qudi.logic.pulsed.pulsed_data.settings_coercion import SettingsTypeError, as_settings_dict
# Master owns the SampLoad workflow machine; the other two states are mirrored, not owned.
from qudi.logic.pulsed.pulsed_fsm.state_machines import StateMachineError
from qudi.logic.pulsed.pulsed_fsm.master_state import SampLoadState, SampLoadStateMachine
from qudi.logic.pulsed.pulsed_fsm.generator_state import GeneratorState
from qudi.logic.pulsed.pulsed_fsm.measurement_state import MeasurementState


class PulsedMasterLogic(LogicBase):
    """
    This logic module combines the functionality of two modules.

    It can be used to generate pulse sequences/waveforms and to control the settings for the pulse
    generator via SequenceGeneratorLogic. Essentially this part controls what is played on the
    pulse generator.
    Furthermore it can be used to set up a pulsed measurement with an already set-up pulse generator
    together with a fast counting device via PulsedMeasurementLogic.

    The main purpose for this module is to provide a single interface while maintaining a modular
    structure for complex pulsed measurements. Each of the sub-modules can be used without this
    module but more care has to be taken in that case.
    Automatic transfer of information from one sub-module to the other for convenience is also
    handled here.
    Another important aspect is the use of this module in scripts (e.g. jupyter notebooks).
    All calls to sub-module setter functions (PulsedMeasurementLogic and SequenceGeneratorLogic)
    are decoupled from the calling thread via Qt queued connections.
    This ensures a more intuitive and less error prone use of scripting.

    Example config:

    pulsed_master_logic:
        module.Class: 'pulsed.pulsed_master_logic.PulsedMasterLogic'
        connect:
            pulsedmeasurementlogic: 'pulsed_measurement_logic'
            sequencegeneratorlogic: 'sequence_generator_logic'
    """

    # declare connectors
    pulsedmeasurementlogic = Connector(interface=PulsedMeasurementLogic)
    sequencegeneratorlogic = Connector(interface=SequenceGeneratorLogic)

    # PulsedMeasurementLogic control signals
    sigDoFit = QtCore.Signal(str, bool)
    sigToggleMeasurement = QtCore.Signal(bool, str)
    sigToggleMeasurementPause = QtCore.Signal(bool)
    sigTogglePulser = QtCore.Signal(bool)
    sigToggleExtMicrowave = QtCore.Signal(bool)
    sigFastCounterSettingsChanged = QtCore.Signal(dict)
    sigMeasurementSettingsChanged = QtCore.Signal(dict)
    sigExtMicrowaveSettingsChanged = QtCore.Signal(dict)
    sigAnalysisSettingsChanged = QtCore.Signal(dict)
    sigExtractionSettingsChanged = QtCore.Signal(dict)
    sigTimerIntervalChanged = QtCore.Signal(float)
    sigAlternativeDataTypeChanged = QtCore.Signal(str)
    sigManuallyPullData = QtCore.Signal()

    # signals for master module (i.e. GUI) coming from PulsedMeasurementLogic
    sigMeasurementDataUpdated = QtCore.Signal()
    sigTimerUpdated = QtCore.Signal(float, int, float)
    sigFitUpdated = QtCore.Signal(str, object, bool)
    sigMeasurementStatusUpdated = QtCore.Signal(bool, bool)
    sigPulserRunningUpdated = QtCore.Signal(bool)
    sigExtMicrowaveRunningUpdated = QtCore.Signal(bool)
    sigExtMicrowaveSettingsUpdated = QtCore.Signal(dict)
    sigFastCounterSettingsUpdated = QtCore.Signal(dict)
    sigMeasurementSettingsUpdated = QtCore.Signal(dict)
    sigAnalysisSettingsUpdated = QtCore.Signal(dict)
    sigExtractionSettingsUpdated = QtCore.Signal(dict)

    # SequenceGeneratorLogic control signals
    sigSavePulseBlock = QtCore.Signal(object)
    sigSaveBlockEnsemble = QtCore.Signal(object)
    sigSaveSequence = QtCore.Signal(object)
    sigDeletePulseBlock = QtCore.Signal(str)
    sigDeleteBlockEnsemble = QtCore.Signal(str)
    sigDeleteSequence = QtCore.Signal(str)
    sigLoadBlockEnsemble = QtCore.Signal(str)
    sigLoadSequence = QtCore.Signal(str)
    sigSampleBlockEnsemble = QtCore.Signal(str)
    sigSampleSequence = QtCore.Signal(str)
    sigClearPulseGenerator = QtCore.Signal()
    sigGeneratorSettingsChanged = QtCore.Signal(dict)
    sigSamplingSettingsChanged = QtCore.Signal(dict)
    sigGeneratePredefinedSequence = QtCore.Signal(str, dict)

    # signals for master module (i.e. GUI) coming from SequenceGeneratorLogic
    sigBlockDictUpdated = QtCore.Signal(dict)
    sigEnsembleDictUpdated = QtCore.Signal(dict)
    sigSequenceDictUpdated = QtCore.Signal(dict)
    sigAvailableWaveformsUpdated = QtCore.Signal(list)
    sigAvailableSequencesUpdated = QtCore.Signal(list)
    sigSampleEnsembleComplete = QtCore.Signal(object)
    sigSampleSequenceComplete = QtCore.Signal(object)
    sigLoadedAssetUpdated = QtCore.Signal(str, str)
    sigGeneratorSettingsUpdated = QtCore.Signal(dict)
    sigSamplingSettingsUpdated = QtCore.Signal(dict)
    sigPredefinedSequenceGenerated = QtCore.Signal(object, bool)

    #: (old_state, new_state) forwarded on from the two logic modules, so the GUI can show what the
    #: toolchain is doing without polling ten booleans.
    sigGeneratorStateChanged = QtCore.Signal(object, object)
    sigMeasurementStateChanged = QtCore.Signal(object, object)

    #: (old_state, new_state) for master's own generate -> sample -> load chain, completing the set.
    #: Worth relaying for one step in particular: the generator does not lock itself while uploading
    #: to the device, so LOADING is the only activity in the toolchain that no other machine reports.
    sigSampLoadStateChanged = QtCore.Signal(object, object)

    #: reset_toolchain() has done what it can. The GUI's cue to re-derive its widget states, which
    #: is the half of the recovery that lives up there rather than here.
    sigToolchainReset = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        """ Create PulsedMasterLogic object with connectors.
        """
        super().__init__(*args, **kwargs)

        # Container serving as status register. Its seven mirror flags are recomputed from the
        # state machines by _sync_status_dict(); fitting_busy and benchmark_busy remain real
        # master-owned state, written directly (the GUI writes benchmark_busy itself).
        self.status_dict = PulsedMasterStatus()

        # The generate -> sample -> load chain, the one multi-step workflow master drives itself.
        self._sampload_fsm = SampLoadStateMachine(parent=self)
        self._sampload_fsm.sigStateChanged.connect(self._sync_status_dict)
        # Signal-to-signal: republishes every transition unchanged, no handler needed.
        self._sampload_fsm.sigStateChanged.connect(self.sigSampLoadStateChanged)

        # Mirrors of the other two modules' states, kept fresh by their state-changed signals.
        self.generator_state = GeneratorState.IDLE
        self.measurement_state = MeasurementState.IDLE

    def _generator_state_updated(self, old_state, new_state):
        """Mirror SequenceGeneratorLogic's state and refresh the flags derived from it.

        Parameters
        ----------
        old_state, new_state : GeneratorState
        """
        self.generator_state = new_state
        self._sync_status_dict()
        self.sigGeneratorStateChanged.emit(old_state, new_state)

    def _measurement_state_updated(self, old_state, new_state):
        """Mirror PulsedMeasurementLogic's state and refresh the flags derived from it.

        Parameters
        ----------
        old_state, new_state : MeasurementState
        """
        self.measurement_state = new_state
        self._sync_status_dict()
        self.sigMeasurementStateChanged.emit(old_state, new_state)

    @property
    def sampload_state(self):
        """Where the generate -> sample -> load chain currently is.

        A property rather than a mirror attribute like `generator_state`/`measurement_state`: those
        two are mirrors because they arrive over queued signals from other modules and there is
        nothing local to read. This machine belongs to master, so a copy would only be a second
        thing to keep in step.

        Returns
        -------
        SampLoadState
        """
        return self._sampload_fsm.state

    @property
    def _generator_busy(self):
        """Whether SequenceGeneratorLogic is doing anything, asked of it directly.

        Gating decisions must not use `self.generator_state`: that mirror is one queued signal
        behind, so a script calling sample_ensemble() then clear_pulse_generator() would slip past
        a check made against it. module_state() is the generator's own live value.
        """
        return self.sequencegeneratorlogic().module_state() == 'locked'

    def _sync_status_dict(self, *_):
        """Recompute the status flags that are derived from the three state machines.

        The single writer of those flags, so they cannot drift. fitting_busy and benchmark_busy are
        deliberately absent: they are master's own state, written directly (the GUI sets
        benchmark_busy itself), and must not be overwritten here.
        """
        generator = self.generator_state
        sampload = self._sampload_fsm.state
        self.status_dict.predefined_generation_busy = generator is GeneratorState.GENERATING
        self.status_dict.sampling_ensemble_busy = generator is GeneratorState.SAMPLING_ENSEMBLE
        self.status_dict.sampling_sequence_busy = generator is GeneratorState.SAMPLING_SEQUENCE
        self.status_dict.measurement_running = self.measurement_state is not MeasurementState.IDLE
        self.status_dict.loading_busy = sampload is SampLoadState.LOADING
        self.status_dict.sampload_busy = sampload is not SampLoadState.IDLE

    def _refresh_loaded_asset(self):
        """Announce what the pulse generator holds, without touching the chain.

        What every refused-request path needs, and the reason it cannot just call
        `loaded_asset_updated()`: that method's first act is `finish()`, and on a refusal the chain
        it would be finishing is the one still running that caused the refusal.

        Reading `loaded_asset` goes to the pulse generator, quite possibly the thing that is
        unwell. A failure is reported and the asset announced as empty rather than allowed out of a
        queued slot - the GUI re-enables its load and sample buttons on this signal, so staying
        silent would trade a stuck chain for a stuck toolbar.
        """
        try:
            name, asset_type = self.loaded_asset
        except Exception:
            self.log.exception('Could not read the loaded asset back from the pulse generator. '
                               'Reporting it as empty:')
            name, asset_type = '', ''
        self.sigLoadedAssetUpdated.emit(name, asset_type)

    def _resync_pulser_running(self):
        """Re-derive the pulser_running flag from the device instead of trusting the cached one.

        The flag is only ever written by pulse_generator_on()/off() reporting what they believe
        happened, and nothing re-reads it afterwards. That is fine while those calls either fully
        succeed or fully fail, and wrong when one half-succeeds: a real instrument that accepts the
        stop command and then times out on the reply raises, so off() leaves the flag saying
        "running" while the device is off. Nothing would ever correct it, and the GUI's pulser
        button would stay wrong for the rest of the session.

        Note this is not the same as an off() that never reached the device at all - there the flag
        stays True and the device really is still running, so it was right all along and this
        changes nothing. Only the half-succeeded case is what this is for.

        A read, not a change: it stays inside reset_toolchain()'s "operational state only" remit.
        Guarded because the pulse generator may well be the thing that is unwell.
        """
        try:
            running = self.sequencegeneratorlogic().pulsegenerator().get_status()[0] > 0
        except Exception:
            self.log.exception('Could not read the pulse generator status back while resetting, so '
                               'the reported pulser state may still be wrong:')
            return
        if running != self.status_dict.pulser_running:
            self.log.warning('Toolchain reset: the pulse generator is actually {0}, correcting the '
                             'reported state.'.format('running' if running else 'off'))
        self.pulser_running_updated(running)

    def _asset_is_loaded_and_running(self, asset_name, asset_type):
        """Whether the pulse generator is running the named asset right now.

        Errs towards True when the device cannot be asked: the callers use this to refuse deleting
        an asset out from under a running pulse sequence, and a refusal is recoverable where the
        delete is not.

        Parameters
        ----------
        asset_name : str
            Name to compare against what the device reports.
        asset_type : str
            'PulseBlockEnsemble' or 'PulseSequence'.

        Returns
        -------
        bool
        """
        if not self.status_dict.pulser_running:
            # No need to ask the device at all, which is the common case.
            return False
        try:
            # The comparison is inside the try as well as the read: whatever the device hands back
            # is only as trustworthy as the device, and this runs in a queued slot.
            loaded = self.loaded_asset
            return loaded.name == asset_name and loaded.asset_type == asset_type
        except Exception:
            self.log.exception('Could not read the loaded asset back from the pulse generator '
                               'while checking whether "{0}" is in use. Assuming it is:'
                               ''.format(asset_name))
            return True

    def _recover_sampload_chain(self):
        """Return the chain to IDLE and put the GUI back in step with the hardware.

        The asset notification is what re-enables the load buttons and shows what is really on the
        device, so it has to go out with the recovery rather than after the next successful load.
        """
        self._sampload_fsm.recover()
        self._refresh_loaded_asset()

    @QtCore.Slot()
    def reset_sampload_chain(self):
        """Force the generate/sample/load chain back to IDLE.

        Safe to call at any time: it is a no-op when no chain is running. It does not stop whatever
        the other modules may still be doing - it only stops master refusing new work - so check
        the log for why the chain stalled before starting another one. Most callers want
        reset_toolchain() instead, which does this and stops the measurement too.
        """
        if self._sampload_fsm.state is SampLoadState.IDLE:
            return
        self.log.warning('Generate/sample/load chain reset by request while in state {0}.'
                         ''.format(self._sampload_fsm.state.name))
        self._recover_sampload_chain()

    @QtCore.Slot()
    def reset_toolchain(self):
        """Return the pulsed toolchain to idle after something has gone wrong.

        The escape hatch behind the GUI's Reset button, and a public slot so an unattended script
        can call it too. Stops any measurement and releases master's generate/sample/load chain.
        Operational state only - settings, saved assets and the pulse generator's memory are left
        exactly as they are.

        SequenceGeneratorLogic is deliberately **not** forced. It does its work synchronously in its
        own thread, bracketed by `try/finally`, so forcing its machine to IDLE from here would leave
        that `finally` firing an end event from a state the table has no row for - a StateMachineError
        raised out of a `finally`, out of a queued slot, which is worse than the stall it was meant
        to clear. It is also unnecessary: the `finally` releases it on every path it can return from.
        If it is still busy this says so and leaves it be.
        """
        was_measuring = self.status_dict.measurement_running
        if was_measuring:
            self.log.warning('Toolchain reset: stopping the running measurement.')
            # The queued path the GUI's stop button uses, rather than a direct cross-thread call.
            self.sigToggleMeasurement.emit(False, '')
        self.reset_sampload_chain()

        if self._generator_busy:
            self.log.warning(
                'Toolchain reset: the sequence generator is still working and has been left alone - '
                'it releases itself when it finishes. If it never does, the pulse generator has '
                'stopped answering and only a restart of the module will clear it.'
            )
        elif not was_measuring:
            # Only worth asking, and only safe to ask, when nothing else is talking to the device.
            # A measurement that is stopping maintains this flag itself on the way down (and the
            # stop above is queued, so right now the pulser is legitimately still on); a busy
            # generator may hold the device long enough for the read to block, which is the last
            # thing this method should do.
            self._resync_pulser_running()

        self.sigToolchainReset.emit()

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

        # Initialize status register
        self.status_dict = PulsedMasterStatus()

        # Connect signals controlling PulsedMeasurementLogic
        self.sigDoFit.connect(
            self.pulsedmeasurementlogic().do_fit, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigToggleMeasurement.connect(
            self.pulsedmeasurementlogic().toggle_pulsed_measurement, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigToggleMeasurementPause.connect(
            self.pulsedmeasurementlogic().toggle_measurement_pause, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigTogglePulser.connect(
            self.pulsedmeasurementlogic().toggle_pulse_generator, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigToggleExtMicrowave.connect(
            self.pulsedmeasurementlogic().toggle_microwave, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigFastCounterSettingsChanged.connect(
            self.pulsedmeasurementlogic().set_fast_counter_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigMeasurementSettingsChanged.connect(
            self.pulsedmeasurementlogic().set_measurement_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigExtMicrowaveSettingsChanged.connect(
            self.pulsedmeasurementlogic().set_microwave_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigAnalysisSettingsChanged.connect(
            self.pulsedmeasurementlogic().set_analysis_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigExtractionSettingsChanged.connect(
            self.pulsedmeasurementlogic().set_extraction_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigTimerIntervalChanged.connect(
            self.pulsedmeasurementlogic().set_timer_interval, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigAlternativeDataTypeChanged.connect(
            self.pulsedmeasurementlogic().set_alternative_data_type, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigManuallyPullData.connect(
            self.pulsedmeasurementlogic().manually_pull_data, QtCore.Qt.ConnectionType.QueuedConnection)

        # Connect signals coming from PulsedMeasurementLogic
        self.pulsedmeasurementlogic().sigMeasurementDataUpdated.connect(
            self.sigMeasurementDataUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigTimerUpdated.connect(
            self.sigTimerUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigFitUpdated.connect(
            self.fit_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigMeasurementStatusUpdated.connect(
            self.measurement_status_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigMeasurementStateChanged.connect(
            self._measurement_state_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigPulserRunningUpdated.connect(
            self.pulser_running_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigExtMicrowaveRunningUpdated.connect(
            self.ext_microwave_running_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigExtMicrowaveSettingsUpdated.connect(
            self.sigExtMicrowaveSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigFastCounterSettingsUpdated.connect(
            self.sigFastCounterSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigMeasurementSettingsUpdated.connect(
            self.sigMeasurementSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigAnalysisSettingsUpdated.connect(
            self.sigAnalysisSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.pulsedmeasurementlogic().sigExtractionSettingsUpdated.connect(
            self.sigExtractionSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)

        # Connect signals controlling SequenceGeneratorLogic
        self.sigSavePulseBlock.connect(
            self.sequencegeneratorlogic().save_block, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSaveBlockEnsemble.connect(
            self.sequencegeneratorlogic().save_ensemble, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSaveSequence.connect(
            self.sequencegeneratorlogic().save_sequence, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigDeletePulseBlock.connect(
            self.sequencegeneratorlogic().delete_block, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigDeleteBlockEnsemble.connect(
            self.sequencegeneratorlogic().delete_ensemble, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigDeleteSequence.connect(
            self.sequencegeneratorlogic().delete_sequence, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigLoadBlockEnsemble.connect(
            self.sequencegeneratorlogic().load_ensemble, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigLoadSequence.connect(
            self.sequencegeneratorlogic().load_sequence, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSampleBlockEnsemble.connect(
            self.sequencegeneratorlogic().sample_pulse_block_ensemble, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSampleSequence.connect(
            self.sequencegeneratorlogic().sample_pulse_sequence, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigClearPulseGenerator.connect(
            self.sequencegeneratorlogic().clear_pulser, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigGeneratorSettingsChanged.connect(
            self.sequencegeneratorlogic().set_pulse_generator_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigSamplingSettingsChanged.connect(
            self.sequencegeneratorlogic().set_generation_parameters, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigGeneratePredefinedSequence.connect(
            self.sequencegeneratorlogic().generate_predefined_sequence, QtCore.Qt.ConnectionType.QueuedConnection)

        # Connect signals coming from SequenceGeneratorLogic
        self.sequencegeneratorlogic().sigBlockDictUpdated.connect(
            self.sigBlockDictUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigEnsembleDictUpdated.connect(
            self.sigEnsembleDictUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigSequenceDictUpdated.connect(
            self.sigSequenceDictUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigAvailableWaveformsUpdated.connect(
            self.sigAvailableWaveformsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigAvailableSequencesUpdated.connect(
            self.sigAvailableSequencesUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigGeneratorSettingsUpdated.connect(
            self.sigGeneratorSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigSamplingSettingsUpdated.connect(
            self.sigSamplingSettingsUpdated, QtCore.Qt.ConnectionType.QueuedConnection)
        # Step 3 of the PulsedMeasurement consolidation: keep PulsedMeasurementLogic's held
        # generator_settings fresh without giving SequenceGeneratorLogic a Connector to it -
        # PulsedMasterLogic already has connectors to both, so it relays the push instead.
        self.sequencegeneratorlogic().sigGeneratorSettingsUpdated.connect(
            self._refresh_measurement_logic_generator_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigSamplingSettingsUpdated.connect(
            self._refresh_measurement_logic_generator_settings, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigGeneratorStateChanged.connect(
            self._generator_state_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigPredefinedSequenceGenerated.connect(
            self.predefined_sequence_generated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigSampleEnsembleComplete.connect(
            self.sample_ensemble_finished, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigSampleSequenceComplete.connect(
            self.sample_sequence_finished, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigLoadedAssetUpdated.connect(
            self.loaded_asset_updated, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sequencegeneratorlogic().sigBenchmarkComplete.connect(
            self.benchmark_completed, QtCore.Qt.ConnectionType.QueuedConnection)

        return

    def on_deactivate(self):
        """
        """
        # Disconnect all signals
        # Disconnect signals controlling PulsedMeasurementLogic
        self.sigDoFit.disconnect()
        self.sigToggleMeasurement.disconnect()
        self.sigToggleMeasurementPause.disconnect()
        self.sigTogglePulser.disconnect()
        self.sigToggleExtMicrowave.disconnect()
        self.sigFastCounterSettingsChanged.disconnect()
        self.sigMeasurementSettingsChanged.disconnect()
        self.sigExtMicrowaveSettingsChanged.disconnect()
        self.sigAnalysisSettingsChanged.disconnect()
        self.sigExtractionSettingsChanged.disconnect()
        self.sigTimerIntervalChanged.disconnect()
        self.sigAlternativeDataTypeChanged.disconnect()
        self.sigManuallyPullData.disconnect()
        # Disconnect signals coming from PulsedMeasurementLogic
        self.pulsedmeasurementlogic().sigMeasurementDataUpdated.disconnect()
        self.pulsedmeasurementlogic().sigTimerUpdated.disconnect()
        self.pulsedmeasurementlogic().sigFitUpdated.disconnect()
        self.pulsedmeasurementlogic().sigMeasurementStatusUpdated.disconnect()
        self.pulsedmeasurementlogic().sigMeasurementStateChanged.disconnect()
        self.pulsedmeasurementlogic().sigPulserRunningUpdated.disconnect()
        self.pulsedmeasurementlogic().sigExtMicrowaveRunningUpdated.disconnect()
        self.pulsedmeasurementlogic().sigExtMicrowaveSettingsUpdated.disconnect()
        self.pulsedmeasurementlogic().sigFastCounterSettingsUpdated.disconnect()
        self.pulsedmeasurementlogic().sigMeasurementSettingsUpdated.disconnect()
        self.pulsedmeasurementlogic().sigAnalysisSettingsUpdated.disconnect()
        self.pulsedmeasurementlogic().sigExtractionSettingsUpdated.disconnect()

        # Disconnect signals controlling SequenceGeneratorLogic
        self.sigSavePulseBlock.disconnect()
        self.sigSaveBlockEnsemble.disconnect()
        self.sigSaveSequence.disconnect()
        self.sigDeletePulseBlock.disconnect()
        self.sigDeleteBlockEnsemble.disconnect()
        self.sigDeleteSequence.disconnect()
        self.sigLoadBlockEnsemble.disconnect()
        self.sigLoadSequence.disconnect()
        self.sigSampleBlockEnsemble.disconnect()
        self.sigSampleSequence.disconnect()
        self.sigClearPulseGenerator.disconnect()
        self.sigGeneratorSettingsChanged.disconnect()
        self.sigSamplingSettingsChanged.disconnect()
        self.sigGeneratePredefinedSequence.disconnect()
        # Disconnect signals coming from SequenceGeneratorLogic
        self.sequencegeneratorlogic().sigBlockDictUpdated.disconnect()
        self.sequencegeneratorlogic().sigEnsembleDictUpdated.disconnect()
        self.sequencegeneratorlogic().sigSequenceDictUpdated.disconnect()
        self.sequencegeneratorlogic().sigAvailableWaveformsUpdated.disconnect()
        self.sequencegeneratorlogic().sigAvailableSequencesUpdated.disconnect()
        self.sequencegeneratorlogic().sigGeneratorSettingsUpdated.disconnect()
        self.sequencegeneratorlogic().sigSamplingSettingsUpdated.disconnect()
        self.sequencegeneratorlogic().sigGeneratorStateChanged.disconnect()
        self.sequencegeneratorlogic().sigPredefinedSequenceGenerated.disconnect()
        self.sequencegeneratorlogic().sigSampleEnsembleComplete.disconnect()
        self.sequencegeneratorlogic().sigSampleSequenceComplete.disconnect()
        self.sequencegeneratorlogic().sigLoadedAssetUpdated.disconnect()
        self.sequencegeneratorlogic().sigBenchmarkComplete.disconnect()
        return

    #######################################################################
    ###             Pulsed measurement properties                       ###
    #######################################################################
    @property
    def fast_counter_constraints(self):
        return self.pulsedmeasurementlogic().fast_counter_constraints

    @property
    def fast_counter_settings(self):
        return self.pulsedmeasurementlogic().fast_counter_settings

    @property
    def elapsed_sweeps(self):
        return self.pulsedmeasurementlogic().elapsed_sweeps

    @property
    def elapsed_time(self):
        return self.pulsedmeasurementlogic().elapsed_time

    @property
    def ext_microwave_constraints(self):
        return self.pulsedmeasurementlogic().ext_microwave_constraints

    @property
    def ext_microwave_settings(self):
        return self.pulsedmeasurementlogic().ext_microwave_settings

    @property
    def measurement_settings(self):
        return self.pulsedmeasurementlogic().measurement_settings

    @property
    def timer_interval(self):
        return self.pulsedmeasurementlogic().timer_interval

    @property
    def analysis_methods(self):
        return self.pulsedmeasurementlogic().analysis_methods

    @property
    def alt_plot_methods(self):
        return self.pulsedmeasurementlogic().alt_plot_methods

    @property
    def alt_plot_labels(self):
        return self.pulsedmeasurementlogic().alt_plot_labels

    @property
    def extraction_methods(self):
        return self.pulsedmeasurementlogic().extraction_methods

    @property
    def analysis_settings(self):
        return self.pulsedmeasurementlogic().analysis_settings

    @property
    def extraction_settings(self):
        return self.pulsedmeasurementlogic().extraction_settings

    @property
    def signal_data(self):
        return self.pulsedmeasurementlogic().signal_data

    @property
    def signal_alt_data(self):
        return self.pulsedmeasurementlogic().signal_alt_data

    @property
    def measurement_error(self):
        return self.pulsedmeasurementlogic().measurement_error

    @property
    def raw_data(self):
        return self.pulsedmeasurementlogic().raw_data

    @property
    def laser_data(self):
        return self.pulsedmeasurementlogic().laser_data

    @property
    def alternative_data_type(self):
        return self.pulsedmeasurementlogic().alternative_data_type

    @property
    def fit_containers(self):
        return FitContainers(primary=self.pulsedmeasurementlogic().fc,
                             alternative=self.pulsedmeasurementlogic().alt_fc)

    @property
    def fit_config_model(self):
        return self.pulsedmeasurementlogic().fit_config_model

    @property
    def default_data_dir(self):
        return self.pulsedmeasurementlogic().module_default_data_dir

    #######################################################################
    ###             Pulsed measurement methods                          ###
    #######################################################################
    def _relay_settings(self, signal, settings, kwargs, name, dataclass_type=None):
        """Normalize a settings argument to a plain dict and emit it on `signal`.

        A QtCore.Signal(dict) cannot carry a dataclass across the queued cross-thread connection
        to the target logic module, so a dataclass argument has to be flattened here rather than
        passed through. A caller mistake is logged rather than raised: these are queued slots,
        where an escaping exception cannot reach the emitter and may take the whole application
        down mid-measurement.

        Returns
        -------
        dict or None
            The dict that was emitted, or None if the argument could not be interpreted.
        """
        try:
            settings_dict = as_settings_dict(settings, kwargs, dataclass_type)
        except SettingsTypeError as err:
            self.log.error(f'Unable to change {name}. {err}')
            return None
        signal.emit(settings_dict)
        return settings_dict

    @QtCore.Slot(dict)
    def set_measurement_settings(self, settings=None, **kwargs):
        """
        Parameters
        ----------
        settings : ReadoutSettings or dict or None
        kwargs
        """
        self._relay_settings(self.sigMeasurementSettingsChanged, settings, kwargs,
                             'measurement settings', ReadoutSettings)
        return

    @QtCore.Slot(dict)
    def set_fast_counter_settings(self, settings=None, **kwargs):
        """
        Parameters
        ----------
        settings : FastCounterSettings or dict or None
        kwargs
        """
        self._relay_settings(self.sigFastCounterSettingsChanged, settings, kwargs,
                             'fast counter settings', FastCounterSettings)
        return

    @QtCore.Slot(dict)
    def set_ext_microwave_settings(self, settings=None, **kwargs):
        """
        Parameters
        ----------
        settings : MicrowaveSettings or dict or None
        kwargs
        """
        self._relay_settings(self.sigExtMicrowaveSettingsChanged, settings, kwargs,
                             'external microwave settings', MicrowaveSettings)
        return

    @QtCore.Slot(dict)
    def set_analysis_settings(self, settings=None, **kwargs):
        """
        Parameters
        ----------
        settings : dict or None
        kwargs
        """
        # No dataclass counterpart: AnalysisParameters is a dict subclass because the parameter
        # names are discovered at runtime from whichever analysis method is selected.
        self._relay_settings(self.sigAnalysisSettingsChanged, settings, kwargs,
                             'analysis settings')
        return

    @QtCore.Slot(dict)
    def set_extraction_settings(self, settings=None, **kwargs):
        """
        Parameters
        ----------
        settings : dict or None
        kwargs
        """
        # No dataclass counterpart - see set_analysis_settings above.
        self._relay_settings(self.sigExtractionSettingsChanged, settings, kwargs,
                             'extraction settings')
        return

    @QtCore.Slot(int)
    @QtCore.Slot(float)
    def set_timer_interval(self, interval):
        """
        Parameters
        ----------
        interval : int or float
            The timer interval to set in seconds.
        """
        if not isinstance(interval, (int, float)) or isinstance(interval, bool):
            self.log.error(f'Unable to change timer interval. Expected int or float, got '
                           f'{type(interval).__name__}.')
            return
        self.sigTimerIntervalChanged.emit(interval)
        return

    @QtCore.Slot(str)
    def set_alternative_data_type(self, alt_data_type):
        """
        Parameters
        ----------
        alt_data_type
        """
        if not isinstance(alt_data_type, str):
            self.log.error(f'Unable to change alternative data type. Expected str, got '
                           f'{type(alt_data_type).__name__}.')
            return
        self.sigAlternativeDataTypeChanged.emit(alt_data_type)
        return

    @QtCore.Slot()
    def manually_pull_data(self):
        """
        """
        self.sigManuallyPullData.emit()
        return

    @QtCore.Slot(bool)
    def toggle_ext_microwave(self, switch_on):
        """
        Parameters
        ----------
        switch_on
        """
        if isinstance(switch_on, bool):
            self.sigToggleExtMicrowave.emit(switch_on)
        return

    @QtCore.Slot(bool)
    def ext_microwave_running_updated(self, is_running):
        """
        Parameters
        ----------
        is_running
        """
        if isinstance(is_running, bool):
            self.status_dict.microwave_running = is_running
            self.sigExtMicrowaveRunningUpdated.emit(is_running)
        return

    @QtCore.Slot(bool)
    def toggle_pulse_generator(self, switch_on):
        """
        Parameters
        ----------
        switch_on
        """
        if isinstance(switch_on, bool):
            self.sigTogglePulser.emit(switch_on)
        return

    @QtCore.Slot(bool)
    def pulser_running_updated(self, is_running):
        """
        Parameters
        ----------
        is_running
        """
        if isinstance(is_running, bool):
            self.status_dict.pulser_running = is_running
            self.sigPulserRunningUpdated.emit(is_running)
        return

    @QtCore.Slot(bool)
    @QtCore.Slot(bool, str)
    def toggle_pulsed_measurement(self, start, stash_raw_data_tag=''):
        """
        Parameters
        ----------
        start : bool
        stash_raw_data_tag : str
        """
        if isinstance(start, bool) and isinstance(stash_raw_data_tag, str):
            self.sigToggleMeasurement.emit(start, stash_raw_data_tag)
        return

    @QtCore.Slot(bool)
    def toggle_pulsed_measurement_pause(self, pause):
        """
        Parameters
        ----------
        pause
        """
        if isinstance(pause, bool):
            self.sigToggleMeasurementPause.emit(pause)
        return

    @QtCore.Slot(bool, bool)
    def measurement_status_updated(self, is_running, is_paused):
        """
        Parameters
        ----------
        is_running
        is_paused
        """
        if isinstance(is_running, bool) and isinstance(is_paused, bool):
            # measurement_running is no longer set here - it is derived in _sync_status_dict() from
            # the mirrored MeasurementState, which also knows about paused, unlike these two bools.
            self.sigMeasurementStatusUpdated.emit(is_running, is_paused)
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def do_fit(self, fit_function, use_alternative_data=False):
        """
        Parameters
        ----------
        fit_function : str
        use_alternative_data : bool
        """
        if isinstance(fit_function, str) and isinstance(use_alternative_data, bool):
            self.status_dict.fitting_busy = True
            self.sigDoFit.emit(fit_function, use_alternative_data)
        return

    @QtCore.Slot(str, object, bool)
    def fit_updated(self, fit_name, fit_result, use_alternative_data):
        """
        """
        self.status_dict.fitting_busy = False
        self.sigFitUpdated.emit(fit_name, fit_result, use_alternative_data)
        return

    @QtCore.Slot()
    def benchmark_completed(self):
        self.status_dict.benchmark_busy = False

    def get_pulsed_measurement(self):
        """Full settings+data+objects snapshot of the current measurement, combining both
        sub-modules' state - for scripting/notebook use (see this module's own docstring, which
        already names that as a design goal). PulsedMasterLogic is the only module with
        Connectors to both PulsedMeasurementLogic and SequenceGeneratorLogic, so it is the only
        place that can build the combined object - including resolving the loaded asset's
        ensemble/block closure, which requires SequenceGeneratorLogic's saved-asset registries.

        Returns
        -------
        PulsedMeasurement
            Snapshot built from independent copies (see
            PulsedMeasurementLogic.get_pulsed_measurement()) - safe to hold onto, will not change
            as the measurement continues running or assets get reloaded/edited later.
        """
        ensembles, blocks = self.sequencegeneratorlogic().resolve_asset_closure(
            self.pulsedmeasurementlogic().loaded_asset
        )
        return self.pulsedmeasurementlogic().get_pulsed_measurement(
            generator_settings=self.sequencegeneratorlogic().generator_settings,
            ensembles=ensembles,
            blocks=blocks,
        )

    def save_measurement_data(self, tag=None, notes=None, file_path=None, storage_cls=None,
                              with_error=True, save_laser_pulses=True, save_pulsed_measurement=True,
                              save_figure=None, save_measurement_snapshot=False):
        """ Prepare data to be saved and create a proper plot of the data.
        Combines the current SequenceGeneratorLogic settings with the measurement logic's own
        settings/data before handing off, since only this module has Connectors to both.

        Parameters
        ----------
        tag : str
            A name tag which will be included in the filename if file_path is None.
        file_path : str
            Optional, custom full file path including file extension to use.
            If given, tag is ignored.
        storage_cls : type
            Optional, the explicit data storage class to use.
        with_error : bool
            Select whether errors should be plotted.
        save_laser_pulses : bool
            Select whether extracted lasers should be saved.
        save_pulsed_measurement : bool
            Select whether final measurement should be saved.
        save_figure : bool
            Select whether a thumbnail plot should be saved.
        notes : str
            Optional, string that is included in the metadata "as-is" without a field.
        save_measurement_snapshot : bool
            Optional, additionally pickle the complete
            PulsedMeasurement snapshot (settings + data + the loaded sequence/ensemble) to a
            single '.pulsedmeasurement' file - see PulsedMeasurementLogic.save_measurement_data().
        """
        still_busy = self.status_dict.sampload_busy or self._generator_busy
        if still_busy:
            self.log.error('Can not save measurement data while a load/sample operation is still '
                           'in progress. The currently loaded asset\'s settings have not fully '
                           'propagated yet - saving now would save stale data from the previously '
                           'loaded asset. Wait for loading/sampling to finish and try again.')
            return
        # Resolve the ensemble/block closure unconditionally - it's embedded in every
        # raw/laser/signal .dat file's metadata now (see
        # PulsedMeasurementLogic._get_loaded_asset_metadata()), not just the optional full
        # snapshot, so it's needed on every save, not only when save_measurement_snapshot=True.
        ensembles, blocks = self.sequencegeneratorlogic().resolve_asset_closure(
            self.pulsedmeasurementlogic().loaded_asset
        )
        return self.pulsedmeasurementlogic().save_measurement_data(
            tag=tag,
            file_path=file_path,
            storage_cls=storage_cls,
            with_error=with_error,
            save_laser_pulses=save_laser_pulses,
            save_pulsed_measurement=save_pulsed_measurement,
            save_figure=save_figure,
            notes=notes,
            save_measurement_snapshot=save_measurement_snapshot,
            ensembles=ensembles,
            blocks=blocks,
            # PulsedMasterLogic is the only module with Connectors to both PulsedMeasurementLogic
            # and SequenceGeneratorLogic, so it is the one place that can hand the sequence
            # generator's settings down to be included in the saved measurement metadata (see
            # PulsedMeasurementLogic.get_pulsed_measurement()/_get_signal_metadata()).
            generator_settings=self.sequencegeneratorlogic().generator_settings,
        )

    #######################################################################
    ###             Sequence generator properties                       ###
    #######################################################################
    @property
    def pulse_generator_constraints(self):
        return self.sequencegeneratorlogic().pulse_generator_constraints

    @property
    def pulse_generator_settings(self):
        return self.sequencegeneratorlogic().pulse_generator_settings

    @property
    def generation_parameters(self):
        return self.sequencegeneratorlogic().generation_parameters

    @property
    def analog_channels(self):
        return self.sequencegeneratorlogic().analog_channels

    @property
    def digital_channels(self):
        return self.sequencegeneratorlogic().digital_channels

    @property
    def saved_pulse_blocks(self):
        return self.sequencegeneratorlogic().saved_pulse_blocks

    @property
    def saved_pulse_block_ensembles(self):
        return self.sequencegeneratorlogic().saved_pulse_block_ensembles

    @property
    def saved_pulse_sequences(self):
        return self.sequencegeneratorlogic().saved_pulse_sequences

    @property
    def sampled_waveforms(self):
        return self.sequencegeneratorlogic().sampled_waveforms

    @property
    def sampled_sequences(self):
        return self.sequencegeneratorlogic().sampled_sequences

    @property
    def loaded_asset(self):
        return self.sequencegeneratorlogic().loaded_asset

    @property
    def generate_methods(self):
        return getattr(self.sequencegeneratorlogic(), 'generate_methods', dict())

    @property
    def generate_method_params(self):
        return getattr(self.sequencegeneratorlogic(), 'generate_method_params', dict())

    #######################################################################
    ###             Sequence generator methods                          ###
    #######################################################################
    @QtCore.Slot()
    def clear_pulse_generator(self):
        still_busy = self.status_dict.sampload_busy or self._generator_busy
        if still_busy:
            self.log.error('Can not clear pulse generator. Sampling/Loading still in progress.')
        elif self.status_dict.measurement_running:
            self.log.error('Can not clear pulse generator. Measurement is still running.')
        else:
            if self.status_dict.pulser_running:
                self.log.warning('Can not clear pulse generator while it is still running. '
                                 'Turned off.')
                self.pulsedmeasurementlogic().pulse_generator_off()
            self.sigClearPulseGenerator.emit()
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def sample_ensemble(self, ensemble_name, with_load=False):
        # Deliberately does NOT gate on self.generator_state. That mirror lags one queued-signal
        # hop, and this slot is reached from predefined_sequence_generated() while the generator's
        # "back to IDLE" update is still in the queue - so gating on it refuses every GenSampLo.
        # The mirror is for display. Whether sampling may start is decided by master's own SampLoad
        # machine below, and authoritatively by the generator's own machine when the request lands.
        if with_load and not self._enter_sampling(ensemble_name):
            return
        self.sigSampleBlockEnsemble.emit(ensemble_name)
        return

    @QtCore.Slot(object)
    def sample_ensemble_finished(self, ensemble):
        self.sigSampleEnsembleComplete.emit(ensemble)
        # Both halves are needed, and they answer different questions. The chain being in SAMPLING
        # says master is running a sample-and-load; `sampling_sequence_busy` says the generator is
        # part-way through a PulseSequence and this completion is one of its constituent ensembles,
        # announced by the same signal (sample_pulse_block_ensemble() emits it whether it was called
        # standalone or from the sequence loop). Advancing on one of those would load a constituent
        # ensemble and leave the sequence itself never loaded, because the chain would already have
        # left SAMPLING by the time sample_sequence_finished() arrived.
        #
        # The flag is derived from the generator mirror, which lags a queued hop in general but not
        # here: sigGeneratorStateChanged and sigSampleEnsembleComplete are both queued from the
        # generator's thread to this one, so Qt delivers them in emission order and the mirror has
        # already been updated by the time this runs.
        if (self._sampload_fsm.state is SampLoadState.SAMPLING
                and not self.status_dict.sampling_sequence_busy):
            if ensemble is None:
                self._sampload_fsm.abort()
                self._refresh_loaded_asset()
            else:
                self.load_ensemble(ensemble.name)
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def sample_sequence(self, sequence_name, with_load=False):
        # See sample_ensemble() on why the generator_state mirror is not consulted here.
        if with_load and not self._enter_sampling(sequence_name):
            return
        self.sigSampleSequence.emit(sequence_name)
        return

    def _enter_sampling(self, asset_name):
        """Move into the SAMPLING step, continuing a GenSampLo chain or starting a fresh one.

        Parameters
        ----------
        asset_name : str
            Only used for the error message.

        Returns
        -------
        bool
            False if another chain was already in progress, in which case nothing was started.
        """
        if self._sampload_fsm.state is SampLoadState.GENERATING:
            self._sampload_fsm.continue_to_sample()
            return True
        return self._begin_sampload('begin_sample', asset_name)

    def _begin_sampload(self, event, asset_name):
        """Enter the generate/sample/load chain, or report that one is already running.

        Parameters
        ----------
        event : str
            'begin_generate', 'begin_sample' or 'begin_load'.
        asset_name : str
            Only used for the error message.

        Returns
        -------
        bool
            False if a chain was already in progress, in which case nothing was started.
        """
        try:
            self._sampload_fsm.trigger(event)
        except StateMachineError:
            self.log.error('A generate/sample/load operation is already in progress.\n'
                           '"{0}" not started!'.format(asset_name))
            return False
        return True

    @QtCore.Slot(object)
    def sample_sequence_finished(self, sequence):
        self.sigSampleSequenceComplete.emit(sequence)
        if self._sampload_fsm.state is SampLoadState.SAMPLING:
            if sequence is None:
                self._sampload_fsm.abort()
                self._refresh_loaded_asset()
            else:
                self.load_sequence(sequence.name)
        return

    def _abort_own_sampling_chain(self):
        """End the chain if this refusal is the tail of a sample-and-load we ourselves started.

        The distinction the two refusals in the load slots turn on. A chain in SAMPLING got there
        because sample_ensemble()/sample_sequence() put it there, and the load being refused now is
        the step that was going to end it - so nothing else ever will, and leaving it running blocks
        every later operation and keeps the GUI's run/stop action disabled until the module is
        restarted. A chain in GENERATING or LOADING belongs to an operation still in flight, which
        is the case the sibling `_enter_loading()` refusal deliberately leaves alone.
        """
        if self._sampload_fsm.state is SampLoadState.SAMPLING:
            self._sampload_fsm.abort()

    def _enter_loading(self, asset_name):
        """Move into the LOADING step, continuing a sample-and-load chain or starting a fresh one.

        Parameters
        ----------
        asset_name : str
            Only used for the error message.

        Returns
        -------
        bool
            False if another chain was already in progress, in which case nothing was started.
        """
        if self._sampload_fsm.state is SampLoadState.SAMPLING:
            self._sampload_fsm.continue_to_load()
            return True
        return self._begin_sampload('begin_load', asset_name)

    @QtCore.Slot(str)
    def load_ensemble(self, ensemble_name):
        # Neither refusal below goes through loaded_asset_updated(): that would finish() whatever
        # chain happens to be running, and on the second refusal that is precisely the chain still
        # in flight which caused the refusal - ending it there would turn a GenSampLo into a bare
        # generate with nothing to show for the difference. They differ in what they do about the
        # chain instead. The first is the tail of a sample-and-load of our own, so it ends it (see
        # _abort_own_sampling_chain); the second belongs to someone else, so it leaves it alone.
        if self.status_dict.measurement_running:
            self.log.error('Loading of ensemble not possible while measurement is running.\n'
                           'PulseBlockEnsemble "{0}" not loaded!'.format(ensemble_name))
            self._abort_own_sampling_chain()
            self._refresh_loaded_asset()
            return
        if not self._enter_loading(ensemble_name):
            self._refresh_loaded_asset()
            return
        if self.status_dict.pulser_running:
            self.log.warning('Can not load new asset into pulse generator while it is still '
                             'running. Turned off.')
            self.pulsedmeasurementlogic().pulse_generator_off()
        self.sigLoadBlockEnsemble.emit(ensemble_name)
        return

    @QtCore.Slot(str)
    def load_sequence(self, sequence_name):
        # See load_ensemble() on why these two refusals must not call loaded_asset_updated().
        if self.status_dict.measurement_running:
            self.log.error('Loading of sequence not possible while measurement is running.\n'
                           'PulseSequence "{0}" not loaded!'.format(sequence_name))
            self._abort_own_sampling_chain()
            self._refresh_loaded_asset()
            return
        if not self._enter_loading(sequence_name):
            self._refresh_loaded_asset()
            return
        if self.status_dict.pulser_running:
            self.log.warning('Can not load new asset into pulse generator while it is still '
                             'running. Turned off.')
            self.pulsedmeasurementlogic().pulse_generator_off()
        self.sigLoadSequence.emit(sequence_name)
        return

    @QtCore.Slot(str, str)
    def _refresh_measurement_logic_generator_settings(self, _changed_dict=None):
        """Slot for SequenceGeneratorLogic.sigGeneratorSettingsUpdated/sigSamplingSettingsUpdated -
        pushes the current generator_settings into PulsedMeasurementLogic's held PulsedMeasurement
        (see PulsedMeasurementLogic.refresh_generator_settings()). The emitted dict itself isn't
        used - both signals just indicate "something changed", so this always re-fetches the
        current, complete generator_settings rather than trying to patch in only what changed.
        """
        self.pulsedmeasurementlogic().refresh_generator_settings(self.sequencegeneratorlogic().generator_settings)

    def loaded_asset_updated(self, asset_name, asset_type):
        """
        Parameters
        ----------
        asset_name
        asset_type
        """
        # Terminates the chain. Guarded because this slot doubles as a "refresh the GUI" helper
        # called from the error paths above, where no chain was ever started.
        if self._sampload_fsm.state is not SampLoadState.IDLE:
            self._sampload_fsm.finish()
        self.sigLoadedAssetUpdated.emit(asset_name, asset_type)
        # Transfer sequence information from PulseBlockEnsemble or PulseSequence to
        # PulsedMeasurementLogic to be able to invoke measurement settings from them
        if not asset_type:
            # If no asset loaded or asset type unknown, clear the sampling/measurement/generation
            # information containers below
            object_instance = None
        elif asset_type == 'PulseBlockEnsemble':
            object_instance = self.saved_pulse_block_ensembles.get(asset_name)
        elif asset_type == 'PulseSequence':
            object_instance = self.saved_pulse_sequences.get(asset_name)
        else:
            object_instance = None

        self.pulsedmeasurementlogic().loaded_asset = object_instance
        # Step 3 of the PulsedMeasurement consolidation: keep the held PulsedMeasurement's
        # resolved ensembles/blocks fresh too, reusing the same closure-resolution helper the
        # on-demand get_pulsed_measurement()/save_measurement_data() snapshot paths already use.
        ensembles, blocks = self.sequencegeneratorlogic().resolve_asset_closure(object_instance)
        self.pulsedmeasurementlogic().refresh_loaded_asset_closure(ensembles, blocks)
        return

    @QtCore.Slot(object)
    def save_pulse_block(self, block_instance):
        """
        Parameters
        ----------
        block_instance
        """
        self.sigSavePulseBlock.emit(block_instance)
        return

    @QtCore.Slot(object)
    def save_block_ensemble(self, ensemble_instance):
        """
        Parameters
        ----------
        ensemble_instance
        """
        self.sigSaveBlockEnsemble.emit(ensemble_instance)
        return

    @QtCore.Slot(object)
    def save_sequence(self, sequence_instance):
        """
        Parameters
        ----------
        sequence_instance
        """
        self.sigSaveSequence.emit(sequence_instance)
        return

    @QtCore.Slot(str)
    def delete_pulse_block(self, block_name):
        """
        Parameters
        ----------
        block_name
        """
        self.sigDeletePulseBlock.emit(block_name)
        return

    @QtCore.Slot()
    def delete_all_pulse_blocks(self):
        """
        Helper method to delete all pulse blocks at once.
        """
        to_delete = tuple(self.saved_pulse_blocks)
        for block_name in to_delete:
            self.sigDeletePulseBlock.emit(block_name)
        return

    @QtCore.Slot(str)
    def delete_block_ensemble(self, ensemble_name):
        """
        Parameters
        ----------
        ensemble_name
        """
        if self._asset_is_loaded_and_running(ensemble_name, 'PulseBlockEnsemble'):
            self.log.error('Can not delete PulseBlockEnsemble "{0}" since the corresponding '
                           'waveform(s) is(are) currently loaded and running.'
                           ''.format(ensemble_name))
        else:
            self.sigDeleteBlockEnsemble.emit(ensemble_name)
        return

    @QtCore.Slot()
    def delete_all_block_ensembles(self):
        """
        Helper method to delete all pulse block ensembles at once.
        """
        if self.status_dict.pulser_running or self.status_dict.measurement_running:
            self.log.error('Can not delete all PulseBlockEnsembles. Pulse generator is currently '
                           'running or measurement is in progress.')
        else:
            to_delete = tuple(self.saved_pulse_block_ensembles)
            for ensemble_name in to_delete:
                self.sigDeleteBlockEnsemble.emit(ensemble_name)
        return

    @QtCore.Slot(str)
    def delete_sequence(self, sequence_name):
        """
        Parameters
        ----------
        sequence_name
        """
        if self._asset_is_loaded_and_running(sequence_name, 'PulseSequence'):
            self.log.error('Can not delete PulseSequence "{0}" since the corresponding sequence is '
                           'currently loaded and running.'.format(sequence_name))
        else:
            self.sigDeleteSequence.emit(sequence_name)
        return

    @QtCore.Slot()
    def delete_all_pulse_sequences(self):
        """
        Helper method to delete all pulse sequences at once.
        """
        if self.status_dict.pulser_running or self.status_dict.measurement_running:
            self.log.error('Can not delete all PulseSequences. Pulse generator is currently '
                           'running or measurement is in progress.')
        else:
            to_delete = tuple(self.saved_pulse_sequences)
            for sequence_name in to_delete:
                self.sigDeleteSequence.emit(sequence_name)
        return

    @QtCore.Slot()
    def refresh_pulse_generator_settings(self):
        """
        Trigger updated settings when values within might have changed without being
        explicitly set by the setter method.
        :return:
        """
        # causes update of benchmark results
        self.sigGeneratorSettingsChanged.emit({})

    @QtCore.Slot(dict)
    def set_pulse_generator_settings(self, settings=None, **kwargs):
        """
        Either accept a PulseGeneratorSettings instance or a settings dictionary as positional
        argument, or keyword arguments. If both are present both are being used by updating the
        settings_dict with kwargs. The keyword arguments take precedence over the items in
        settings_dict if there are conflicting names.

        Parameters
        ----------
        settings : PulseGeneratorSettings or dict or None
        kwargs
        """
        self._relay_settings(self.sigGeneratorSettingsChanged, settings, kwargs,
                             'pulse generator settings', PulseGeneratorSettings)
        return

    @QtCore.Slot(dict)
    def set_generation_parameters(self, settings=None, **kwargs):
        """
        Either accept a GenerationParameters instance or a settings dictionary as positional
        argument, or keyword arguments. If both are present both are being used by updating the
        settings_dict with kwargs. The keyword arguments take precedence over the items in
        settings_dict if there are conflicting names.

        Parameters
        ----------
        settings : GenerationParameters or dict or None
        kwargs
        """
        try:
            settings_dict = as_settings_dict(settings, kwargs, generation_parameters_class())
        except SettingsTypeError as err:
            self.log.error(f'Unable to change generation parameters. {err}')
            return

        # Force empty gate channel if fast counter is not gated
        if 'gate_channel' in settings_dict and not self.fast_counter_settings.get('is_gated'):
            settings_dict['gate_channel'] = ''
        self.sigSamplingSettingsChanged.emit(settings_dict)
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, dict)
    @QtCore.Slot(str, dict, bool)
    def generate_predefined_sequence(self, generator_method_name, kwarg_dict=None, sample_and_load=False):
        """
        Parameters
        ----------
        generator_method_name
        kwarg_dict
        sample_and_load
        """
        if not isinstance(kwarg_dict, dict):
            kwarg_dict = dict()
        # Only a sample-and-load run is a master workflow; a bare generate is entirely the
        # generator's business and shows up through its own state.
        if sample_and_load and not self._begin_sampload('begin_generate', generator_method_name):
            return
        self.sigGeneratePredefinedSequence.emit(generator_method_name, kwarg_dict)
        return

    @QtCore.Slot(object, bool)
    def predefined_sequence_generated(self, asset_name, produced_sequence):
        """
        Parameters
        ----------
        asset_name : str or None
            Name of the generated asset, or None if generation failed.
        produced_sequence : bool
            Whether the generate method returned any PulseSequence. Not to be confused with
            PulseSequence.is_sequence, which asks whether a given *object* is a sequence.
        """
        in_chain = self._sampload_fsm.state is SampLoadState.GENERATING
        if in_chain and asset_name is None:
            self._sampload_fsm.abort()
            in_chain = False
        self.sigPredefinedSequenceGenerated.emit(asset_name, produced_sequence)
        if in_chain:
            # sample_sequence/sample_ensemble fire continue_to_sample, since we are in GENERATING.
            if produced_sequence:
                self.sample_sequence(asset_name, True)
            else:
                self.sample_ensemble(asset_name, True)
        return

    def get_ensemble_info(self, ensemble):
        """
        This helper method is just there for backwards compatibility. Essentially it will call the
        method "analyze_block_ensemble".

        Will return information like length in seconds and bins (with currently set sampling rate)
        as well as number of laser pulses (with currently selected laser/gate channel)

        Parameters
        ----------
        ensemble : PulseBlockEnsemble
            The PulseBlockEnsemble instance to analyze.

        Returns
        -------
        (float, int, int)
            Length in seconds, length in bins, number of laser/gate pulses.
        """
        return self.sequencegeneratorlogic().get_ensemble_info(ensemble=ensemble)

    def get_sequence_info(self, sequence):
        """
        This helper method will analyze a PulseSequence and return information like length in
        seconds and bins (with currently set sampling rate), number of laser pulses (with currently
        selected laser/gate channel)

        Parameters
        ----------
        sequence : PulseSequence
            The PulseSequence instance to analyze.

        Returns
        -------
        (float, int, int)
            Length in seconds, length in bins, number of laser/gate pulses.
        """
        return self.sequencegeneratorlogic().get_sequence_info(sequence=sequence)

    def analyze_block_ensemble(self, ensemble):
        """
        This helper method runs through each element of a PulseBlockEnsemble object and extracts
        important information about the Waveform that can be created out of this object.
        Especially the discretization due to the set self.sample_rate is taken into account.
        The positions in time (as integer time bins) of the PulseBlockElement transitions are
        determined here (all the "rounding-to-best-match-value").
        Additional information like the total number of samples, total number of PulseBlockElements
        and the timebins for digital channel low-to-high transitions get returned as well.

        This method assumes that sanity checking has been already performed on the
        PulseBlockEnsemble (via _sampling_ensemble_sanity_check). Meaning it assumes that all
        PulseBlocks are actually present in saved blocks and the channel activation matches the
        current pulse settings.

        Parameters
        ----------
        ensemble
            A PulseBlockEnsemble object (see logic.pulse_objects.py).

        Returns
        -------
        number_of_samples : int
            The total number of samples in a Waveform provided the current sample_rate and
            PulseBlockEnsemble object.
        total_elements : int
            The total number of PulseBlockElements (incl. repetitions) in the provided
            PulseBlockEnsemble.
        elements_length_bins : 1D numpy.ndarray[int]
            Array of number of timebins for each PulseBlockElement in chronological order
            (incl. repetitions).
        digital_rising_bins : dict
            Dictionary with keys being the digital channel descriptor string and items being
            arrays of chronological low-to-high transition positions (in timebins; incl.
            repetitions) for each digital channel.
        """
        return self.sequencegeneratorlogic().analyze_block_ensemble(ensemble=ensemble)

    def analyze_sequence(self, sequence):
        """
        This helper method runs through each step of a PulseSequence object and extracts
        important information about the Sequence that can be created out of this object.
        Especially the discretization due to the set self.sample_rate is taken into account.
        The positions in time (as integer time bins) of the PulseBlockElement transitions are
        determined here (all the "rounding-to-best-match-value").
        Additional information like the total number of samples, total number of PulseBlockElements
        and the timebins for digital channel low-to-high transitions get returned as well.

        This method assumes that sanity checking has been already performed on the
        PulseSequence (via _sampling_ensemble_sanity_check). Meaning it assumes that all
        PulseBlocks are actually present in saved blocks and the channel activation matches the
        current pulse settings.

        Parameters
        ----------
        sequence
            A PulseSequence object (see logic.pulse_objects.py).

        Returns
        -------
        number_of_samples : int
            The total number of samples in a Waveform provided the current sample_rate and
            PulseBlockEnsemble object.
        total_elements : int
            The total number of PulseBlockElements (incl. repetitions) in the provided
            PulseBlockEnsemble.
        elements_length_bins : 1D numpy.ndarray[int]
            Array of number of timebins for each PulseBlockElement in chronological order
            (incl. repetitions).
        digital_rising_bins : dict
            Dictionary with keys being the digital channel descriptor string and items being
            arrays of chronological low-to-high transition positions (in timebins; incl.
            repetitions) for each digital channel.
        """
        return self.sequencegeneratorlogic().analyze_sequence(sequence=sequence)

    #######################################################################
    ###             Helper  methods                                     ###
    #######################################################################
    # def _get_asset_parameters(self, asset_obj):
    #     """
    #
    #     Parameters
    #     ----------
    #     asset_obj
    #     """
    #     if type(asset_obj).__name__ == 'PulseSequence':
    #         self.log.warning('Calculation of measurement sequence parameters not implemented yet '
    #                          'for PulseSequence objects.')
    #         return {'err_code': -1}
    #     # Create return dictionary
    #     return_params = {'err_code': 0}
    #
    #     # Get activation config and name
    #     if asset_obj.activation_config is None:
    #         return_params['activation_config'] = self._generator_logic.activation_config
    #         self.log.warning('No activation config specified in asset "{0}" metadata. Choosing '
    #                          'currently set activation config "{1}" from sequence_generator_logic.'
    #                          ''.format(asset_obj.name, return_params['activation_config']))
    #     else:
    #         return_params['activation_config'] = asset_obj.activation_config
    #     config_name = None
    #     avail_configs = self._measurement_logic.get_pulser_constraints().activation_config
    #     for config in avail_configs:
    #         if return_params['activation_config'] == avail_configs[config]:
    #             config_name = config
    #             break
    #     if config_name is None:
    #         self.log.error('Activation config {0} is not part of the allowed activation '
    #                        'configs in the pulse generator hardware.'
    #                        ''.format(return_params['activation_config']))
    #         return_params['err_code'] = -1
    #         return return_params
    #     else:
    #         return_params['config_name'] = config_name
    #
    #     # Get analogue voltages
    #     if asset_obj.amplitude_dict is None:
    #         return_params['amplitude_dict'] = self._generator_logic.amplitude_dict
    #         self.log.warning('No amplitude dictionary specified in asset "{0}" metadata. Choosing '
    #                          'currently set amplitude dict "{1}" from sequence_generator_logic.'
    #                          ''.format(asset_obj.name, return_params['amplitude_dict']))
    #     else:
    #         return_params['amplitude_dict'] = asset_obj.amplitude_dict
    #
    #     # Get sample rate
    #     if asset_obj.sample_rate is None:
    #         return_params['sample_rate'] = self._generator_logic.sample_rate
    #         self.log.warning('No sample rate specified in asset "{0}" metadata. Choosing '
    #                          'currently set sample rate "{1:.2e}" from sequence_generator_logic.'
    #                          ''.format(asset_obj.name, return_params['sample_rate']))
    #     else:
    #         return_params['sample_rate'] = asset_obj.sample_rate
    #
    #     # Get sequence length
    #     return_params['sequence_length'] = asset_obj.length_s
    #     return_params['sequence_length_bins'] = asset_obj.length_s*self._generator_logic.sample_rate
    #
    #     # Get number of laser pulses and max laser length
    #     if asset_obj.laser_channel is None:
    #         laser_chnl = self._generator_logic.laser_channel
    #         self.log.warning('No laser channel specified in asset "{0}" metadata. Choosing '
    #                          'currently set laser channel "{1}" from sequence_generator_logic.'
    #                          ''.format(asset_obj.name, laser_chnl))
    #     else:
    #         laser_chnl = asset_obj.laser_channel
    #     num_of_lasers = 0
    #     max_laser_length = 0.0
    #     tmp_laser_on = False
    #     tmp_laser_length = 0.0
    #     for block, reps in asset_obj.block_list:
    #         tmp_lasers_num = 0
    #         for element in block.element_list:
    #             if 'd_ch' in laser_chnl:
    #                 d_channels = [ch for ch in return_params['activation_config'] if 'd_ch' in ch]
    #                 chnl_index = d_channels.index(laser_chnl)
    #                 if not tmp_laser_on and element.digital_high[chnl_index]:
    #                     tmp_laser_on = True
    #                     tmp_lasers_num += 1
    #                 elif not element.digital_high[chnl_index]:
    #                     tmp_laser_on = False
    #                 if tmp_laser_on:
    #                     if element.increment_s > 1.0e-15:
    #                         tmp_laser_length += (element.init_length_s + reps * element.increment_s)
    #                     else:
    #                         tmp_laser_length += element.init_length_s
    #                     if tmp_laser_length > max_laser_length:
    #                         max_laser_length = tmp_laser_length
    #                 else:
    #                     tmp_laser_length = 0.0
    #             else:
    #                 self.log.error('Invoke measurement settings from a PulseBlockEnsemble with '
    #                                'analogue laser channel is not implemented yet.')
    #                 return_params['err_code'] = -1
    #                 return
    #         num_of_lasers += (tmp_lasers_num * (reps + 1))
    #     return_params['num_of_lasers'] = num_of_lasers
    #     return_params['max_laser_length'] = max_laser_length
    #
    #     # Get laser ignore list
    #     if asset_obj.laser_ignore_list is None:
    #         return_params['laser_ignore_list'] = []
    #         self.log.warning('No laser ignore list specified in asset "{0}" metadata. '
    #                          'Assuming that no lasers should be ignored.'.format(asset_obj.name))
    #     else:
    #         return_params['laser_ignore_list'] = asset_obj.laser_ignore_list
    #
    #     # Get alternating
    #     if asset_obj.alternating is None:
    #         return_params['is_alternating'] = self._measurement_logic.alternating
    #         self.log.warning('No alternating specified in asset "{0}" metadata. Choosing '
    #                          'currently set state "{1}" from pulsed_measurement_logic.'
    #                          ''.format(asset_obj.name, return_params['is_alternating']))
    #     else:
    #         return_params['is_alternating'] = asset_obj.alternating
    #
    #     # Get controlled variable values
    #     if len(asset_obj.controlled_vals_array) < 1:
    #         ana_lasers = num_of_lasers - len(return_params['laser_ignore_list'])
    #         controlled_vals_array = np.arange(1, ana_lasers + 1)
    #         self.log.warning('No measurement ticks specified in asset "{0}" metadata. Choosing '
    #                          'laser indices instead.'.format(asset_obj.name))
    #         if return_params['is_alternating']:
    #             controlled_vals_array = controlled_vals_array[0:ana_lasers//2]
    #     else:
    #         controlled_vals_array = asset_obj.controlled_vals_array
    #     return_params['controlled_vals_arr'] = controlled_vals_array
    #
    #     # return all parameters
    #     return return_params
