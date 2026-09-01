# -*- coding: utf-8 -*-
"""
This module contains a Qdyne manager class.
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
import datetime
import logging
import math
from contextlib import contextmanager
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np
from PySide6 import QtCore

from qudi.util.mutex import RecursiveMutex
from qudi.logic.qdyne.qdyne_data.measurement_data import MainDataClass, MeasurementChunk
from qudi.logic.qdyne.qdyne_settings import QdyneSettings
from qudi.logic.qdyne.qdyne_state_estimator import StateEstimatorMain
from qudi.logic.qdyne.qdyne_time_trace_analyzer import TimeTraceAnalyzerMain
from qudi.logic.qdyne.tools.state_enums import DataSource

if TYPE_CHECKING:
    # Type-checking only. qdyne_logic imports THIS module to build the measurement, so a runtime
    # import back the other way closes a cycle that currently survives only because both sides use
    # `import package.module` and resolve the attribute lazily at call time. The first
    # `from ... import QdyneLogic` written in either file would break module activation with an
    # ImportError pointing at neither cause.
    from qudi.logic.qdyne.qdyne_logic import QdyneLogic


class QdyneMeasurement(QtCore.QObject):
    """Drives one Qdyne measurement: the hardware, the analysis timer and the data pipeline.

    All logging goes through `self.log`. This class used to carry a module-level `logger` as well,
    with the start/stop methods using one and the analysis loop the other, so messages from a single
    object landed in two different places and a filtered log view showed half the story.
    """

    #: Consecutive failed analysis passes before the measurement gives up. A transient glitch should
    #: not stop a long run, but an unfixable one should not be allowed to look like a working
    #: measurement either. Note an *empty* pass is not a failure - see qdyne_analysis_loop().
    MAX_CONSECUTIVE_FAILURES = 5

    #: Analysis interval used when the stored one cannot be interpreted. A status file holding 0, a
    #: negative, a NaN or a string must not be able to install a 0 ms timer.
    DEFAULT_TIMER_INTERVAL = 1.0

    sigTimerIntervalUpdated = QtCore.Signal(float)
    # analysis timer signals
    sigStartTimer = QtCore.Signal()
    sigStopTimer = QtCore.Signal()
    #: Queued like the two above, so the interval is applied in the timer's own thread. QTimer is
    #: not thread-safe and the interval is set from whichever thread drives the property.
    sigSetTimerInterval = QtCore.Signal(int)
    sigMeasurementStarted = QtCore.Signal()
    sigMeasurementStopped = QtCore.Signal()
    # notification signals for master module (i.e. GUI)
    sigPulseDataUpdated = QtCore.Signal(object)
    sigTimeTraceDataUpdated = QtCore.Signal(object, object)
    sigQdyneDataUpdated = QtCore.Signal()
    #: Emitted with the error text when the analysis loop gives up - see MAX_CONSECUTIVE_FAILURES.
    #: Gives the GUI something to show instead of a measurement that silently produces nothing.
    sigAnalysisFailed = QtCore.Signal(str)

    def __init__(self, qdyne_logic: "QdyneLogic"):
        super().__init__()
        self.log: logging.Logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        self.__lock = RecursiveMutex()
        #: Signals staged during a locked section and emitted once it is released - see _queue_emit.
        self.__pending_emissions: List[Tuple[QtCore.SignalInstance, tuple]] = []
        #: Nesting depth of _deferred_emissions(). Zero means "emit straight away".
        self.__defer_depth = 0

        self.qdyne_logic: "QdyneLogic" = qdyne_logic
        self.data: MainDataClass = self.qdyne_logic.data
        #: One poll's worth of data. A dedicated type rather than a second MainDataClass, which gave
        #: the per-tick object a freq_domain, a metadata and a freq_data that never meant anything.
        self.new_data: MeasurementChunk = self.qdyne_logic.new_data
        self.estimator: StateEstimatorMain = self.qdyne_logic.estimator
        self.settings: QdyneSettings = self.qdyne_logic.settings
        self.analyzer: TimeTraceAnalyzerMain = self.qdyne_logic.analyzer

        self.__start_time = 0.0
        self.__elapsed_time = 0.0
        self.__elapsed_sweeps = 0

        self._measurement_running = False
        #: Written directly by the state-estimation widget, so the name is kept. Prefer the
        #: pulse_histogram_enabled property / set_pulse_histogram_enabled() slot in new code.
        self._pulse_histogram_disabled = False
        self._consecutive_failures = 0
        #: (name, type) of the asset the running measurement was started against.
        self._loaded_asset: Tuple[str, str] = ('', '')
        #: Resolved once per measurement rather than on every analysis tick - see readout_interval.
        self._readout_interval: Optional[float] = None

        # The pulse histogram is accumulated per chunk rather than recomputed from the whole
        # history (see get_pulse), which makes it O(new samples) instead of quadratic - but it also
        # means the existing bins only stay meaningful while the binning does. Change bin_width or
        # record_length and every stored bin refers to a different slice of time, so the accumulator
        # has to be thrown away. Subscribing here is what keeps that optimisation honest.
        self.settings.estimator_stg.subscribe(
            on_data=self._on_estimator_settings_changed,
            on_mode=self._on_estimator_settings_changed,
            on_renewed=self._on_estimator_settings_changed,
            # A method change additionally has to rebuild the estimator object itself. Doing it here
            # is what lets the analysis loop stop calling configure_method() on every single tick.
            on_method=self._on_estimator_method_changed,
        )
        self._pulse_binning = self._current_binning()

        # set up the analysis timer
        self.__analysis_timer = QtCore.QTimer()
        self.__analysis_timer.setSingleShot(True)
        self.__analysis_timer.setInterval(round(1000.0 * self.DEFAULT_TIMER_INTERVAL))
        self.__analysis_timer.timeout.connect(
            self.qdyne_analysis_loop, QtCore.Qt.QueuedConnection
        )
        # set up the analysis timer signals
        self.sigStartTimer.connect(
            self.__analysis_timer.start, QtCore.Qt.QueuedConnection
        )
        self.sigStopTimer.connect(
            self.__analysis_timer.stop, QtCore.Qt.QueuedConnection
        )
        self.sigSetTimerInterval.connect(
            self.__analysis_timer.setInterval, QtCore.Qt.QueuedConnection
        )

        # Apply the stored interval through the property setter, which validates. __init__ used to
        # call setInterval() directly with `round(1000.0 * stored)`, bypassing every check the
        # setter performs - so a StatusVar holding 0.0 installed a 0 ms single-shot timer that
        # re-fires on every pass of the event loop and saturates a core with analysis passes.
        self.analysis_timer_interval = self.qdyne_logic.analysis_timer_interval

    # ------------------------------------------------------------------ deferred signal emission

    @contextmanager
    def _deferred_emissions(self):
        """Hold back staged signals until this section - and any section nesting it - has finished.

        Always wrap this *around* `with self.__lock`, never inside it, so the flush happens after
        the lock is released.

        Re-entrant on purpose. pull_data_and_estimate() is both a step of the analysis loop and a
        public method the state-estimation widget calls on its own; the depth counter is what lets
        the loop batch its emissions while a standalone call still delivers them immediately, rather
        than staging signals that nothing would ever flush.
        """
        self.__defer_depth += 1
        try:
            yield
        finally:
            self.__defer_depth -= 1
            if self.__defer_depth <= 0:
                self.__defer_depth = 0
                self._flush_emissions()

    def _queue_emit(self, signal, *args) -> None:
        """Emit a signal, deferring it if a _deferred_emissions() section is open.

        Emitting inside the locked section runs every directly-connected slot there too, so a slow
        consumer - a plot redraw - holds the measurement lock for its whole duration and blocks a
        settings change arriving on another thread.
        """
        if self.__defer_depth > 0:
            self.__pending_emissions.append((signal, args))
        else:
            signal.emit(*args)

    def _flush_emissions(self) -> None:
        """Emit everything staged so far. Must be called outside the lock."""
        pending, self.__pending_emissions = self.__pending_emissions, []
        for signal, args in pending:
            try:
                signal.emit(*args)
            except Exception:
                # One broken consumer must not take down the measurement, nor swallow the rest.
                self.log.exception('Failed to emit a staged measurement signal.')

    # ------------------------------------------------------------------ settings access

    @staticmethod
    def _is_settings(obj) -> bool:
        """Whether `obj` is usable as a settings container.

        The pipeline methods below accept an optional pre-fetched settings object so one analysis
        tick uses one consistent set of values throughout. Several of them are also wired straight to
        QPushButton.clicked in the GUI, which passes its `checked` bool as the first argument - so
        the parameter has to recognise a settings object rather than merely test for None, or that
        stray False would be used as the settings and fail on .to_dict().
        """
        return obj is not None and hasattr(obj, 'to_dict')

    def _require_estimator_settings(self):
        """The current estimator settings, or a clear error saying why there are none.

        `current_data` returns None when the current method has no settings at all. That used to be
        dereferenced unguarded, so the failure surfaced as an AttributeError on NoneType inside the
        analysis loop's except clause - a message that says nothing about the real cause.
        """
        settings = self.settings.estimator_stg.current_data
        if settings is None:
            raise RuntimeError(
                f"No estimator settings available for method "
                f"'{self.settings.estimator_stg.current_method}'. Cannot run the analysis."
            )
        return settings

    def _require_analyzer_settings(self):
        settings = self.settings.analyzer_stg.current_data
        if settings is None:
            raise RuntimeError(
                f"No analyzer settings available for method "
                f"'{self.settings.analyzer_stg.current_method}'. Cannot run the analysis."
            )
        return settings

    def _current_binning(self):
        """The settings the accumulated pulse histogram is binned by."""
        settings = self.settings.estimator_stg.current_data
        if settings is None:
            return None
        return (settings.bin_width, settings.record_length)

    def _on_estimator_settings_changed(self, _payload=None):
        """Drop the accumulated pulse histogram when the binning it was built with changes."""
        # Locked: this arrives from whichever thread changed the settings, while the analysis loop
        # may be inside add_pulse_counts() on the measurement thread.
        with self.__lock:
            binning = self._current_binning()
            if binning != self._pulse_binning:
                self._pulse_binning = binning
                self.data.reset_pulse_counts()
                self.log.debug('Estimator binning changed - pulse histogram reset.')

    def _on_estimator_method_changed(self, _payload=None):
        """A different estimator method became current: rebuild it, then re-check the binning."""
        with self.__lock:
            self._reconfigure_estimator()
            self._on_estimator_settings_changed()

    def _reconfigure_estimator(self) -> None:
        """Make the estimator object match the selected method, rebuilding only when it changed.

        The analysis loop used to call `configure_method()` on every tick, which constructs a brand
        new estimator each time and - unlike the `method` setter - leaves StateEstimatorMain._method
        untouched, so `estimator.method` could disagree with the object in `estimator.estimator`.
        Going through the setter keeps the two in step; the guard makes it a string compare on the
        hot path instead of an allocation.
        """
        method = self.settings.estimator_stg.current_method
        if not method:
            return
        if getattr(self.estimator, 'method', None) != method:
            self.estimator.method = method

    # ------------------------------------------------------------------ lifecycle

    def teardown(self):
        """Release everything this object holds. Called from QdyneLogic.on_deactivate().

        This used to be __del__, which Qt makes a bad place for disconnection: it runs whenever the
        garbage collector gets round to it, potentially after the C++ objects are already gone, and
        it never ran at deactivation because nothing held the last reference at that point.

        Safe to call more than once.
        """
        if self._measurement_running:
            # Deactivating mid-run used to leave the counter and the pulse generator switched on.
            self.log.warning('Tearing down while a measurement is running - stopping the hardware.')
            self._measurement_running = False
            self._shutdown_hardware()

        # Without this the mediator keeps a strong reference to these bound methods, which pins the
        # whole logic graph in memory and lets a later settings change call into a torn-down object.
        # Targeted rather than unsubscribe_all(): the GUI's callbacks are not ours to cancel.
        try:
            self.settings.estimator_stg.unsubscribe(self._on_estimator_settings_changed)
            self.settings.estimator_stg.unsubscribe(self._on_estimator_method_changed)
        except Exception:
            self.log.exception('Failed to unsubscribe from the estimator settings mediator.')

        for signal in (
            self.__analysis_timer.timeout,
            self.sigStartTimer,
            self.sigStopTimer,
            self.sigSetTimerInterval,
        ):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass    # already disconnected, or the underlying object is gone
        try:
            self.__analysis_timer.stop()
        except RuntimeError:
            pass        # the C++ timer is already gone

    # ------------------------------------------------------------------ readout interval

    @property
    def readout_interval(self) -> float:
        """Length of one readout, in seconds. Resolved once per measurement and cached.

        This used to be a property that reached through `pulsedmasterlogic().sequencegeneratorlogic()`
        and called `get_ensemble_info(loaded_asset[0])` on *every analysis tick*. With nothing loaded
        the asset name is '' and that raises KeyError; with a PulseSequence loaded it is the wrong
        call entirely (`get_sequence_info` is needed). Because it sat inside the analysis pipeline,
        either raise counted towards the consecutive-failure limit and stopped the measurement.
        """
        if self._readout_interval is None:
            self._readout_interval = self._compute_readout_interval()
        return self._readout_interval

    def _compute_readout_interval(self) -> float:
        """Ask the pulsed stack how long one readout is, falling back rather than raising.

        This value scales the time axis of the time-trace view. Getting it wrong is a display
        problem; raising here would be a measurement problem, so every failure degrades to a
        warning and a usable fallback.
        """
        try:
            master = self.qdyne_logic.pulsedmasterlogic()
            # While a measurement runs, use the asset it started against - swapping the loaded
            # waveform mid-run must not silently rescale the time axis of data already taken.
            # Otherwise ask the pulser what is loaded now, because the cached value is stale the
            # moment the run ends.
            if self._measurement_running and self._loaded_asset[0]:
                name, asset_type = self._loaded_asset
            else:
                name, asset_type = master.loaded_asset
            if not name:
                fallback = self._fallback_readout_interval()
                self.log.warning(
                    f'No waveform loaded - using {fallback} s as the readout interval. The time '
                    f'trace axis will be wrong if this is not the real sequence length.'
                )
                return fallback
            # Asked through PulsedMasterLogic, which exposes both helpers, rather than reaching two
            # levels down into sequencegeneratorlogic(). And branched on the asset type, which the
            # single get_ensemble_info() call could not do.
            if asset_type == 'PulseSequence':
                return float(master.get_sequence_info(name)[0])
            return float(master.get_ensemble_info(name)[0])
        except Exception:
            fallback = self._fallback_readout_interval()
            self.log.exception(
                f'Could not determine the readout interval - falling back to {fallback} s.'
            )
            return fallback

    def _fallback_readout_interval(self) -> float:
        """The estimator's sequence_length, or 1 s if even that is unusable (never 0)."""
        settings = self.settings.estimator_stg.current_data
        try:
            value = float(getattr(settings, 'sequence_length', 0.0))
        except (TypeError, ValueError):
            value = 0.0
        return value if value > 0 else 1.0

    # ------------------------------------------------------------------ pulse histogram switch

    @property
    def pulse_histogram_enabled(self) -> bool:
        return not self._pulse_histogram_disabled

    @QtCore.Slot(bool)
    def set_pulse_histogram_enabled(self, enabled: bool) -> None:
        """Turn the per-tick pulse histogram on or off.

        It is the most expensive part of a tick after the estimator itself, and a long run that
        nobody is watching does not need it.
        """
        self._pulse_histogram_disabled = not bool(enabled)
        self.log.info(f'Pulse histogram {"enabled" if enabled else "disabled"}.')

    # ------------------------------------------------------------------ start / stop

    @QtCore.Slot(bool)
    def toggle_qdyne_measurement(self, start):
        """
        Convenience method to start/stop measurement

        @param bool start: Start the measurement (True) or stop the measurement (False)
        """
        if start:
            self.start_qdyne_measurement()
        else:
            self.stop_qdyne_measurement()
        return

    def _check_ready_to_start(self) -> bool:
        """Refuse to start a measurement that cannot possibly produce data.

        `loaded_asset` returns ('', '') both when nothing is loaded and when the pulse generator's
        channels hold mismatched assets - a partial or half-overwritten load. Both mean the sequence
        is not ready. Nothing used to check: the counter was configured from stale settings, an empty
        pulser was switched on, the metadata lookup raised a KeyError that a bare `except` swallowed,
        and the run then died at the first analysis tick in readout_interval and self-stopped five
        ticks later. What the operator saw was a measurement that started and quietly gave up.
        """
        try:
            name, asset_type = self.qdyne_logic.measurement_generator.loaded_asset
        except Exception:
            self.log.exception(
                'Cannot start a Qdyne measurement: failed to query the loaded asset.'
            )
            return False
        if not name:
            self.log.error(
                'Cannot start a Qdyne measurement: no waveform is loaded on the pulse generator, '
                'or its channels hold mismatched assets. Load an asset and try again.'
            )
            return False
        self._loaded_asset = (name, asset_type)
        self._warn_if_count_window_is_empty()
        return True

    def _warn_if_count_window_is_empty(self) -> None:
        """Say so, at the start, if the estimator will count nothing.

        An empty [sig_start, sig_end) window is not an error - the measurement runs, the counter
        acquires, and every readout simply counts zero photons. What it produces is a perfectly
        flat time trace, an all-zero spectrum, and a fit that dies inside lmfit with
        "Parameter 'offset' has min == max". Three baffling symptoms, one cause, and nothing
        anywhere said which. Since sig_end defaults to 0.0, this is the state a freshly configured
        module starts in.
        """
        settings = self.settings.estimator_stg.current_data
        sig_start = getattr(settings, 'sig_start', None)
        # effective_sig_end, not sig_end: 0.0 in the field means "the whole record", resolved
        # against the current record_length - see TimeTagStateEstimatorSettings.effective_sig_end.
        sig_end = getattr(settings, 'effective_sig_end', None)
        if sig_start is None or sig_end is None:
            return
        if sig_end <= sig_start:
            self.log.warning(
                f'The estimator count window [{sig_start}, {sig_end}) s is empty, so this '
                f'measurement will acquire data but every readout will count zero photons - a flat '
                f'time trace and an all-zero spectrum. Set sig_end greater than sig_start in the '
                f'state estimation settings.'
            )

    def start_qdyne_measurement(self):
        if self._measurement_running:
            self.log.warning('Qdyne measurement is already running - ignoring the start request.')
            return
        if not self._check_ready_to_start():
            return

        self.log.debug('Starting QDyne measurement')
        # Claimed here, where the measurement actually starts, not in QdyneLogic's signal-emitting
        # toggle. Anything calling this method directly used to inherit whatever load_data() last
        # left behind, so a measurement started after a load ran the LOADED branch forever: it
        # looked alive and never once polled the hardware.
        self.qdyne_logic.data_source = DataSource.MEASUREMENT

        self.log.debug('resetting data')
        self.data.reset()
        self.new_data.clear()
        self._consecutive_failures = 0
        self._pulse_binning = self._current_binning()
        self._readout_interval = None

        # Metadata must never block a measurement: recorded in its own guard so a pulsed-stack
        # hiccup costs provenance, not the run.
        try:
            self._record_start_metadata()
        except Exception:
            self.log.exception('Failed to record start metadata - continuing with the measurement.')

        try:
            self._start_hardware()
        except Exception:
            # Without this the counter could be left running with _measurement_running still False,
            # so nothing would ever call stop_qdyne_measurement() to switch it off again.
            self.log.exception('Failed to start the Qdyne measurement - rolling back the hardware.')
            self._shutdown_hardware()
            return

        self.__start_time = datetime.datetime.now().timestamp()
        # Flag first: _compute_readout_interval() prefers the asset the run started against only
        # while the run is live, so it has to see the measurement as running.
        self._measurement_running = True
        self._readout_interval = self._compute_readout_interval()
        self.log.debug('emitting started signals')
        self.sigMeasurementStarted.emit()
        # Guarded, like the re-arm at the end of the loop. Starting unconditionally with a disabled
        # interval fired the timer once on whatever stale interval it happened to hold, ran a single
        # analysis pass, and then never re-armed - which looks like the analysis silently died.
        if self._timer_interval_is_active():
            self.sigStartTimer.emit()
        else:
            self.log.info(
                'Analysis timer is disabled (interval <= 0) - the measurement is acquiring, but no '
                'automatic analysis will run until the interval is set to a positive value.'
            )

    def _start_hardware(self) -> None:
        """Bring up the counter and the pulse generator. Raises if either refuses."""
        generator = self.qdyne_logic.measurement_generator

        # Todo: is this needed?
        #  set settings to make sure that hardware has actual settings (and not of pulsed)
        self.log.debug('set counter settings')
        generator.set_counter_settings(generator.counter_settings)
        self.log.debug('set measurement_settings')
        generator.set_measurement_settings(generator.measurement_settings)

        self.log.debug('start measurement')
        self.qdyne_logic._data_streamer().start_measure()

        self.log.debug('start pulser')
        # pulse_generator_on() RETURNS an error code and only logs on failure - it does not raise.
        # Discarding it meant a pulser that refused to switch on produced a measurement that ran
        # happily and acquired nothing but background.
        err = self.qdyne_logic.pulsedmasterlogic().pulsedmeasurementlogic().pulse_generator_on()
        try:
            failed = err is not None and int(err) < 0
        except (TypeError, ValueError):
            failed = False      # a hardware module that returns something else is not an error
        if failed:
            raise RuntimeError(f'Pulse generator refused to switch on (error code {err}).')

    def _shutdown_hardware(self) -> None:
        """Switch everything off, giving each device its own chance to fail.

        Sequentially and unguarded, a raising pulse_generator_off() meant stop_measure() was never
        reached and the counter kept running.
        """
        for what, call in (
            (
                'pulse generator',
                lambda: self.qdyne_logic.pulsedmasterlogic()
                .pulsedmeasurementlogic()
                .pulse_generator_off(),
            ),
            ('qdyne counter', lambda: self.qdyne_logic._data_streamer().stop_measure()),
        ):
            try:
                call()
            except Exception:
                self.log.exception(f'Failed to stop the {what}.')

    def stop_qdyne_measurement(self):
        if not self._measurement_running:
            self.log.debug('Qdyne measurement is not running - nothing to stop.')
            return
        self.log.debug('Stopping QDyne measurement')
        # Software state first: it cannot fail, and it is what stops the timer re-arming. Doing the
        # hardware first meant a raising device left _measurement_running True and the timer alive,
        # firing into a half-stopped measurement - and this is the path the analysis loop's own
        # failure branch takes, so a hardware fault became a runaway loop.
        self._measurement_running = False
        self.sigStopTimer.emit()
        self._shutdown_hardware()
        self.sigMeasurementStopped.emit()
        return

    # ------------------------------------------------------------------ metadata

    def _record_start_metadata(self) -> None:
        self.data.metadata.start_time = datetime.datetime.now().isoformat(timespec='seconds')
        self.data.metadata.counter_hardware = type(self.qdyne_logic._data_streamer()).__name__
        # dict(...), not the object: generation_parameters hands back a live dictionary, so storing
        # the reference let any later generation-parameter change silently rewrite the metadata of
        # the run already in progress. Same fix as counter_settings in MeasurementGenerator.
        self.data.metadata.generation_parameters = dict(
            self.qdyne_logic.measurement_generator.generation_parameters
        )
        self._record_generation_metadata()

    def _record_generation_metadata(self) -> None:
        """Record how the loaded waveform was generated, or record nothing at all.

        This used to be `generate_method_params[loaded_asset[0]]`, which is wrong twice over:

        * it indexes a dict keyed by generate-*method* name with an *asset* name, so it raised
          KeyError for any waveform the user had renamed - the file's own TODO; and
        * far worse, on a hit it returned that method's **static signature defaults**, collected once
          at import by inspect.signature(). Those are not the arguments the measurement ran with, so
          a successful lookup wrote plausible, authoritative-looking, wrong provenance into the
          saved file. Silently wrong metadata is worse than missing metadata: nothing else in the
          file records the real values, so it cannot be caught afterwards.

        The parameters come instead from PulsedMeasurementLogic.generation_method_parameters, which
        the pulsed stack fills from the loaded asset itself (sequence_generator_logic stamps the real
        kwargs onto every ensemble it generates, and loaded_asset_updated copies them across). That
        is keyed to what is actually loaded and survives across sessions.

        The generate-method *name* is not stored on the asset, so it is taken from what this module
        last generated, and only when that provably describes the loaded asset.
        """
        metadata = self.data.metadata
        asset_name = self._loaded_asset[0]

        try:
            params = (
                self.qdyne_logic.pulsedmasterlogic()
                .pulsedmeasurementlogic()
                .generation_method_parameters
            )
            # A copy: the property hands back the live StatusVar-backed dict.
            metadata.generation_method_parameters = dict(params or {})
        except Exception:
            self.log.exception(
                f"Could not read the generation parameters for '{asset_name}' - "
                f"leaving generation_method_parameters empty."
            )
            metadata.generation_method_parameters = {}

        if not metadata.generation_method_parameters:
            self.log.warning(
                f"No generation parameters recorded for the loaded asset '{asset_name}'. It was "
                f"probably not produced by a predefined generate method."
            )

        last = getattr(self.qdyne_logic.measurement_generator, 'last_generation', None) or {}
        generated_name = (last.get('params') or {}).get('name')
        if last and generated_name == asset_name:
            metadata.generation_method = str(last.get('method', ''))
        else:
            # Anything we cannot prove, we leave blank rather than guess.
            metadata.generation_method = ''
            self.log.debug(
                f"Generate-method name for '{asset_name}' is unknown (last generated: "
                f"{generated_name!r}) - leaving generation_method empty."
            )

    # ------------------------------------------------------------------ analysis loop

    def qdyne_analysis_loop(self):
        # sigStopTimer is a QUEUED connection, so a timeout already sitting in the event queue when
        # the measurement stops will still be delivered afterwards. Without this guard that late
        # tick runs a full analysis pass against hardware that has just been switched off - and if
        # it fails, it increments the failure counter and re-emits sigAnalysisFailed for a
        # measurement that already gave up.
        if not self._measurement_running:
            self.log.debug('Analysis tick arrived after the measurement stopped - ignoring.')
            return

        rearm = False
        # Deferred outside the lock, so staged data signals go out after it is released - including
        # when the pass fails part way through or the failure branch returns early.
        with self._deferred_emissions():
            with self.__lock:
                self.log.debug('Entering Analysis loop')
                try:
                    self.pull_data_and_estimate()

                    if self.data.time_trace.size == 0:
                        # An empty tick is a normal state, not a failure. The counter yields nothing
                        # until a sweep has closed, and the analyzer cannot transform an empty trace
                        # (np.fft.rfft(x, 0) raises, and the amplitude spectrum divides by len()).
                        # Counting these as failures meant five slow ticks at the start of a run
                        # tripped MAX_CONSECUTIVE_FAILURES and stopped a perfectly healthy
                        # measurement - the most likely reason a run "just stops".
                        self.log.debug('No complete sweep yet - skipping analysis this tick.')
                    else:
                        self.analyze_time_trace()
                        self.get_spectrum()
                        self.log.debug('staging sigQdyneDataUpdated')
                        self._queue_emit(self.sigQdyneDataUpdated)
                    self._consecutive_failures = 0
                except Exception as e:
                    # This used to log and carry on indefinitely, so a persistent fault spammed the
                    # log once per tick while the measurement appeared to be running and produced
                    # nothing. Only the first failure gets a full traceback; repeats are one line.
                    self._consecutive_failures += 1
                    if self._consecutive_failures == 1:
                        self.log.exception('Analysis pass failed.')
                    else:
                        self.log.error(
                            f'Analysis failed again ({self._consecutive_failures} in a row): {e}'
                        )
                    if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                        self.log.error(
                            f'Stopping the measurement after {self._consecutive_failures} '
                            f'consecutive failed analysis passes.'
                        )
                        self.sigAnalysisFailed.emit(str(e))
                        self.stop_qdyne_measurement()
                        return
                self.log.debug('Exiting Analysis loop')
                rearm = self._measurement_running

        # Re-arm outside the lock. The interval check replaces the timer blockSignals() pair, which
        # suppressed *all* of the QTimer's signals from the wrong thread to achieve the same thing.
        if rearm and self._timer_interval_is_active():
            self.sigStartTimer.emit()

    def _apply_timer_interval(self, milliseconds: int) -> None:
        """Set the analysis timer's interval from whichever thread we happen to be on.

        Direct when we are already in the timer's own thread: QTimer is only unsafe *across*
        threads, and routing through the queued signal there would leave the interval stale until
        the event loop next runs - which is wrong for anything that reads it back, and for a script
        or notebook driving the loop by hand it would never be applied at all. Queued otherwise, so
        the call still lands in the timer's thread.
        """
        if QtCore.QThread.currentThread() is self.__analysis_timer.thread():
            self.__analysis_timer.setInterval(milliseconds)
        else:
            self.sigSetTimerInterval.emit(milliseconds)

    def _timer_interval_is_active(self) -> bool:
        try:
            return float(self.qdyne_logic.analysis_timer_interval) > 0
        except (TypeError, ValueError):
            return False

    def rerun_pipeline_from(self, data_type: str) -> None:
        """Re-derive the stages that sit downstream of a data product just loaded from file.

        QdyneLogic.load_data() used to call pull_data_and_estimate() whatever had been loaded. Its
        LOADED branch re-extracts from self.data.raw_data - empty when you loaded a time trace - and
        estimate_state() then assigned that empty result straight over the time trace that had just
        been read from the file. Loading anything other than raw_data destroyed it.
        """
        with self._deferred_emissions():
            with self.__lock:
                if data_type == 'raw_data':
                    self.pull_data_and_estimate()
                    self._analyze_if_possible(data_type)
                elif data_type == 'time_trace':
                    self._analyze_if_possible(data_type)
                else:
                    self.log.debug(
                        f"Loaded '{data_type}' - nothing downstream of it to re-derive."
                    )
                self._queue_emit(self.sigQdyneDataUpdated)

    def _analyze_if_possible(self, data_type: str) -> None:
        if self.data.time_trace.size == 0:
            self.log.warning(
                f"Loaded {data_type} produced an empty time trace - skipping the analysis and "
                f"spectrum. The loaded data itself is unchanged."
            )
            return
        self.analyze_time_trace()
        self.get_spectrum()

    # ------------------------------------------------------------------ pipeline

    def pull_data_and_estimate(self):
        """Poll one chunk and run it through the estimator.

        Public: the state-estimation widget calls this on its own to refresh without waiting for a
        tick, so it takes the lock and manages its own emission batch. Both are re-entrant, so the
        analysis loop nesting this call inside its own section costs nothing and keeps one batch.
        """
        with self._deferred_emissions():
            with self.__lock:
                # Stale extracted_data / time_trace / info from a previously failed tick used to
                # survive into the next one; in LOADED mode `info` kept whatever the last live poll
                # left behind, so elapsed_sweeps could describe a different measurement entirely.
                self.new_data.clear()

                self._reconfigure_estimator()
                settings = self._require_estimator_settings()

                stg = self.settings.estimator_stg
                self.data.metadata.state_estimation_method = stg.current_method
                self.data.metadata.state_estimation_mode = stg.current_mode
                self.data.metadata.state_estimation_settings = settings.to_dict()

                self.get_raw_data()

                if not self._pulse_histogram_disabled:
                    self.get_pulse(settings)
                    self.log.debug('staging sigPulseDataUpdated')
                    # Staged exactly once. get_pulse() used to emit this itself and then be followed
                    # by a second emit here, so every connected slot ran twice per tick.
                    self._queue_emit(self.sigPulseDataUpdated, self.data.pulse_data)

                self.extract_data(settings)
                self.estimate_state(settings)
                self.log.debug('staging sigTimeTraceDataUpdated')
                # .copy(): data.time_trace is a live view into the GrowableArray buffer, which
                # reallocates on growth. A receiver - especially across a queued cross-thread
                # connection - was left holding a window onto a stale allocation.
                self._queue_emit(
                    self.sigTimeTraceDataUpdated,
                    self.data.time_trace.copy(),
                    self.readout_interval,
                )

    def get_raw_data(self):
        if self.qdyne_logic.data_source is DataSource.LOADED:
            self.new_data.raw_data = self.data.raw_data
            return

        raw, info = self._poll_counter()
        self.new_data.raw_data = raw
        self.new_data.info = info
        self._record_progress(info)
        # append_raw_data(), not np.append(): the latter reallocates and copies the entire
        # history on every poll, making accumulation quadratic over a run.
        self.data.append_raw_data(raw)

    def _poll_counter(self) -> Tuple[np.ndarray, dict]:
        """One poll from the counter, validated.

        The second element is the hardware's info_dict (elapsed_sweeps / elapsed_time). It was
        discarded at the call site, which is why those two attributes never updated despite existing.
        """
        result = self.qdyne_logic._data_streamer().get_data()
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise TypeError(
                f'Qdyne counter get_data() must return a (data, info_dict) pair, got '
                f'{type(result).__name__}.'
            )
        raw, info = result
        raw = np.asarray(raw)
        if raw.ndim != 1:
            # GrowableArray.append() ravels, so a gated 2-D array (gate x bin) used to be flattened
            # silently and the gate structure lost - after which the pipeline produces
            # plausible-looking nonsense. Full 2-D support is a larger change; this at least makes
            # the limitation loud and locatable.
            raise NotImplementedError(
                f'Qdyne counter returned data of shape {raw.shape}. Only 1-D data is supported - '
                f'the accumulation buffer would silently flatten anything else.'
            )
        return raw, info if isinstance(info, dict) else {}

    def _record_progress(self, info: dict) -> None:
        """Carry the counter's own progress figures into the measurement and its metadata."""
        sweeps = info.get('elapsed_sweeps')
        elapsed = info.get('elapsed_time')
        # Guarded conversions: the interface allows None, and a hardware module reporting something
        # non-numeric must cost a warning rather than a failed analysis pass.
        if sweeps is not None:
            try:
                self.__elapsed_sweeps = int(sweeps)
                self.data.metadata.elapsed_sweeps = self.__elapsed_sweeps
            except (TypeError, ValueError):
                self.log.warning(f"Counter reported a non-numeric 'elapsed_sweeps': {sweeps!r}.")
        if elapsed is not None:
            try:
                self.__elapsed_time = float(elapsed)
                self.data.metadata.elapsed_time = self.__elapsed_time
            except (TypeError, ValueError):
                self.log.warning(f"Counter reported a non-numeric 'elapsed_time': {elapsed!r}.")

    def get_pulse(self, settings=None):
        """Update the pulse histogram from the newest chunk only.

        A histogram is additive, so accumulating each chunk's counts is exactly equivalent to
        re-histogramming the whole stream - and costs O(new samples) rather than O(all samples). This
        used to run over `self.data.raw_data`, the entire accumulated history, on every analysis
        tick, which made the pulse view a second quadratic path independent of the append cost.
        """
        settings = settings if self._is_settings(settings) else self._require_estimator_settings()
        loaded = self.qdyne_logic.data_source is DataSource.LOADED
        source = self.data.raw_data if loaded else self.new_data.raw_data

        if np.asarray(source).size == 0:
            return

        time_array, counts = self.estimator.get_pulse(source, settings)
        if loaded:
            self.data.reset_pulse_counts()
        total = self.data.add_pulse_counts(counts)
        # .copy() on both: `total` IS MainDataClass._pulse_hist, the very array add_pulse_counts
        # increments in place on the next tick, so consumers were handed an object that changed
        # under them between reads.
        self.data.pulse_data = [np.asarray(time_array).copy(), np.asarray(total).copy()]

    def extract_data(self, settings=None):
        settings = settings if self._is_settings(settings) else self._require_estimator_settings()
        if np.asarray(self.new_data.raw_data).size == 0:
            return

        self.new_data.extracted_data = self.estimator.extract(self.new_data.raw_data, settings)
        if self.qdyne_logic.data_source is DataSource.LOADED:
            self.data.extracted_data = self.new_data.extracted_data
            return

        self.data.append_extracted_data(self.new_data.extracted_data)

    def estimate_state(self, settings=None):
        settings = settings if self._is_settings(settings) else self._require_estimator_settings()
        if np.asarray(self.new_data.extracted_data).size == 0:
            return

        self.new_data.time_trace = self.estimator.estimate(self.new_data.extracted_data, settings)
        if self.qdyne_logic.data_source is DataSource.LOADED:
            self.data.time_trace = self.new_data.time_trace
            return

        self.data.append_time_trace(self.new_data.time_trace)

    def analyze_time_trace(self, settings=None):
        # No local try/except: it logged and then re-raised with `raise e`, which resets the
        # traceback to this frame, and the analysis loop logs the failure again anyway.
        settings = settings if self._is_settings(settings) else self._require_analyzer_settings()
        self.data.metadata.analysis_method = self.settings.analyzer_stg.current_method
        self.data.metadata.analysis_mode = self.settings.analyzer_stg.current_mode
        self.data.metadata.analysis_settings = settings.to_dict()
        self.data.signal = self.analyzer.analyze(self.data, settings)

    def get_spectrum(self, settings=None):
        settings = settings if self._is_settings(settings) else self._require_analyzer_settings()
        freq_domain = np.asarray(self.analyzer.get_freq_domain_signal(self.data, settings))
        if freq_domain.ndim < 1 or freq_domain.shape[0] < 2:
            # Otherwise this surfaces as a bare IndexError on the next line, which says nothing
            # about which side of the contract was broken.
            raise ValueError(
                f'Analyzer returned a frequency-domain array of shape {freq_domain.shape}; '
                f'expected [frequencies, values].'
            )
        self.data.freq_domain = freq_domain
        self.data.freq_data.x = freq_domain[0]
        self.data.freq_data.y = freq_domain[1]

    # ------------------------------------------------------------------ analysis timer interval

    @property
    def analysis_timer_interval(self) -> float:
        """
        Property to return the currently set analysis timer interval in seconds.
        """
        return self.qdyne_logic.analysis_timer_interval

    @analysis_timer_interval.setter
    def analysis_timer_interval(self, interval: float):
        """Set the analysis interval in seconds. Zero or negative disables the timer.

        Every path ends with a usable interval stored: an unreadable value falls back to the default
        rather than being ignored, so a corrupt status file cannot leave analysis silently dead.
        """
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            self.log.error(
                f'Analysis timer interval {interval!r} is not a number - falling back to '
                f'{self.DEFAULT_TIMER_INTERVAL} s.'
            )
            interval = self.DEFAULT_TIMER_INTERVAL
        if not math.isfinite(interval):
            # int(inf) raises OverflowError and nan silently compares False against every bound.
            self.log.error(
                f'Analysis timer interval must be finite, got {interval} - falling back to '
                f'{self.DEFAULT_TIMER_INTERVAL} s.'
            )
            interval = self.DEFAULT_TIMER_INTERVAL

        self.qdyne_logic.analysis_timer_interval = interval

        if interval > 0:
            # round() consistently - __init__ used round() and this setter used int(), so 1.5 ms
            # became 2 ms in one and 1 ms in the other. The 1 ms floor stops a tiny-but-positive
            # interval collapsing into a 0 ms timer that re-fires on every event loop pass.
            self._apply_timer_interval(max(1, round(1000.0 * interval)))
            if self._measurement_running:
                self.sigStartTimer.emit()
        else:
            self.log.info('Analysis interval <= 0. Analysis timer disabled.')
            self.sigStopTimer.emit()

        self.sigTimerIntervalUpdated.emit(interval)
