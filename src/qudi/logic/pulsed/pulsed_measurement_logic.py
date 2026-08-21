# -*- coding: utf-8 -*-
"""
This file contains the Qudi logic which controls all pulsed measurements.

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
import os.path
import pickle
import shutil

from PySide6 import QtCore
import numpy as np
import time
import datetime
import matplotlib.pyplot as plt

from dataclasses import replace
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain
from qudi.util.datafitting import FitConfigurationsModel, FitContainer
from qudi.util.datastorage import (
    TextDataStorage,
    CsvDataStorage,
    NpyDataStorage,
    create_dir_for_file,
    get_timestamp_filename,
)
from qudi.util.paths import get_module_app_data_path
from qudi.util.yaml import yaml_load
from qudi.util.units import ScaledFloat
from qudi.util.colordefs import QudiMatplotlibStyle
from qudi.logic.pulsed.pulse_extractor import PulseExtractor
from qudi.logic.pulsed.pulse_analyzer import PulseAnalyzer
from qudi.logic.pulsed.pulse_alt_plot import AltPlotAnalyzer

from qudi.interface.pulser_interface import PulserInterface
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.interface.microwave_interface import MicrowaveInterface

#IMPORT ALL CLASSES FROM DATA CLASS
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    AlternativeSignalSettings,
    AnalysisParameters,
    DataStashCache,
    ExecutionState,
    ExtractionParameters,
    GenerationMethodParameters,
    MeasurementInformation,
    MetadataDictRepresentation,
    to_plain_metadata,
    MicrowaveSettings,
    FastCounterSettings,
    ReadoutSettings,
    PulsedMeasurementSettings,
    PulsedMeasurementData,
    _DEFAULT_MICROWAVE_SETTINGS,
    _DEFAULT_FAST_COUNTER_SETTINGS,
    _DEFAULT_READOUT_SETTINGS,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import SamplingInformation
from qudi.logic.pulsed.pulsed_data.pulsed_measurement import PulsedMeasurement, PulsedData, PulseObjects, Settings
from qudi.logic.pulsed.pulsed_data.settings_coercion import (
    SettingsTypeError,
    as_settings_dict,
    coerce_settings,
)



def _data_storage_from_cfg_option(cfg_str):
    cfg_str = cfg_str.lower()
    if cfg_str == 'text':
        return TextDataStorage
    if cfg_str == 'csv':
        return CsvDataStorage
    if cfg_str == 'npy':
        return NpyDataStorage
    raise ValueError('Invalid ConfigOption value to specify data storage type.')


_DEFAULT_FIT_CONFIGS = (
    {'name': 'Gaussian Dip',
     'model': 'Gaussian',
     'estimator': 'Dip',
     'custom_parameters': None},

    {'name': 'Gaussian Peak',
     'model': 'Gaussian',
     'estimator': 'Peak',
     'custom_parameters': None},

    {'name': 'Lorentzian Dip',
     'model': 'Lorentzian',
     'estimator': 'Dip',
     'custom_parameters': None},

    {'name': 'Lorentzian Peak',
     'model': 'Lorentzian',
     'estimator': 'Peak',
     'custom_parameters': None},

    {'name': 'Double Lorentzian Dips',
     'model': 'DoubleLorentzian',
     'estimator': 'Dips',
     'custom_parameters': None},

    {'name': 'Double Lorentzian Peaks',
     'model': 'DoubleLorentzian',
     'estimator': 'Peaks',
     'custom_parameters': None},

    {'name': 'Triple Lorentzian Dip',
     'model': 'TripleLorentzian',
     'estimator': 'Dips',
     'custom_parameters': None},

    {'name': 'Triple Lorentzian Peaks',
     'model': 'TripleLorentzian',
     'estimator': 'Peaks',
     'custom_parameters': None},

    {'name': 'Sine',
     'model': 'Sine',
     'estimator': 'default',
     'custom_parameters': None},

    {'name': 'Double Sine',
     'model': 'DoubleSine',
     'estimator': 'default',
     'custom_parameters': None},

    {'name': 'Exp. Decay Sine',
     'model': 'ExponentialDecaySine',
     'estimator': 'Decay',
     'custom_parameters': None},

    {'name': 'Stretched Exp. Decay Sine',
     'model': 'ExponentialDecaySine',
     'estimator': 'Stretched Decay',
     'custom_parameters': None},

    {'name': 'Stretched Exp. Decay',
     'model': 'ExponentialDecay',
     'estimator': 'Stretched Decay',
     'custom_parameters': None},
)


def _default_measurement_settings():
    """Factory for the default PulsedMeasurementSettings, used both as the StatusVar default
    and as the fallback when restoring a malformed/legacy status value.

    microwave_settings/fast_counter_settings/readout_settings reuse the single shared default
    instances defined in pulsed_measurement_logic_data.py (that file can't import back from this
    one, so it owns these literals rather than duplicating them here) - PulsedMeasurementSettings.
    from_dict() falls back to the exact same instances when a saved settings file is missing one
    of these keys. alternate_signal_settings/extraction_parameters/analysis_parameters are mutable
    (AltPlotAnalyzer/PulseExtractor/PulseAnalyzer hold and mutate them in place as live state), so
    - unlike the frozen settings above - each one is built fresh here rather than sharing a single
    module-level instance, to avoid one measurement's live state leaking into another's defaults.
    """
    return PulsedMeasurementSettings(
        microwave_settings=_DEFAULT_MICROWAVE_SETTINGS,
        fast_counter_settings=_DEFAULT_FAST_COUNTER_SETTINGS,
        readout_settings=_DEFAULT_READOUT_SETTINGS,
        alternate_signal_settings=AlternativeSignalSettings(),
        extraction_parameters=ExtractionParameters(),
        analysis_parameters=AnalysisParameters(),
    )


class PulsedMeasurementLogic(LogicBase):
    """
    This is the Logic class for the control of pulsed measurements.

    Example config:

    pulsed_measurement_logic:
        module.Class: 'pulsed.pulsed_measurement_logic.PulsedMeasurementLogic'
        options:
            raw_data_save_type: 'text'
            #additional_extraction_path: # optional
            #additional_analysis_path:   # optional
        connect:
            fastcounter: 'fast_counter_dummy'
            pulsegenerator: 'pulser_dummy'
    """

    # declare connectors
    _fastcounter = Connector(name='fastcounter', interface=FastCounterInterface)
    _pulsegenerator = Connector(name='pulsegenerator', interface=PulserInterface)
    _microwave = Connector(name='microwave', interface=MicrowaveInterface, optional=True)

    # Config options
    # Optional additional paths to import from
    extraction_import_path = ConfigOption(name='additional_extraction_path', default=None)
    analysis_import_path = ConfigOption(name='additional_analysis_path', default=None)
    # Optional additional path to import alternative (second) plot methods from
    alt_plot_import_path = ConfigOption(name='additional_alt_plot_path', default=None)
    # Optional file type descriptor for saving raw data to file.
    # todo: doesn't warn if checker not satisfied
    _default_data_storage_cls = ConfigOption(name='default_data_storage_type',
                                             default='text',
                                             constructor=_data_storage_from_cfg_option)
    _save_thumbnails = ConfigOption(name='save_thumbnails', default=True)

    # status variables
    # Single PulsedMeasurement instance holding this module's working state: settings,
    # acquired/evaluated data, and the loaded pulse asset. Only measurement_settings is actually
    # persisted (see representer/constructor below) - `data`/`objects` are transient, rebuilt on
    # every activation. name='_settings' keeps the on-disk key unchanged despite the Python
    # attribute rename, so _migrate_legacy_settings_if_needed() keeps working off that string key.
    _pulsed_measurement = StatusVar(
        name='_settings',
        default=PulsedMeasurement(settings=Settings(measurement_settings=_default_measurement_settings())),
        constructor=lambda x: PulsedMeasurement(
            settings=Settings(
                measurement_settings=PulsedMeasurementSettings.from_dict(x)
                if isinstance(x, dict)
                else _default_measurement_settings()
            )
        ),
        representer=lambda obj: obj.settings.measurement_settings.to_dict(),
    )

    # timer_interval_s/fit_configs are deliberately independent StatusVars, NOT part of
    # PulsedMeasurementSettings/_pulsed_measurement above - they're operational/GUI bookkeeping
    # (analysis-loop cadence, available fit-curve definitions), not measurement-defining settings,
    # so they shouldn't show up in a saved measurement's settings dict. This mirrors exactly how
    # both were persisted before this module's settings were consolidated into one dataclass.
    _timer_interval_s = StatusVar(name='timer_interval_s', default=5.0)
    # fit_configs' representer/constructor read/write self.fit_config_model directly (see below) -
    # the model (a QAbstractListModel from qudi-core) is the live, authoritative state; this
    # StatusVar is just a thin persistence bridge over it, so no separate sync-on-change plumbing
    # is needed the way a plain stored value would require.
    _fit_configs = StatusVar(name='fit_configs', default=None)

    # notification signals for master module (i.e. GUI)
    sigMeasurementDataUpdated = QtCore.Signal()
    # Emitted whenever a pulsed-analysis tick fails (length mismatch, extraction/analysis
    # exception, ...) with a human-readable message, and again with '' once a subsequent tick
    # succeeds - see _report_analysis_problem()/_clear_analysis_problem(). GUI code MAY subscribe
    # to show/clear a warning; nothing relies on it being connected.
    sigAnalysisProblem = QtCore.Signal(str)
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
    # Internal signals
    sigStartTimer = QtCore.Signal()
    sigStopTimer = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # timer for measurement
        self.__analysis_timer = None
        self.__execution_state = ExecutionState()
        # last message reported via sigAnalysisProblem - lets _report_analysis_problem() dedup
        # repeated identical failures instead of logging/emitting on every single tick.
        self.__last_analysis_problem = None

        # threading
        self._threadlock = Mutex()

        self._data_stash=DataStashCache()  # manages the memory for stashed/recalled raw data

        # for fit:
        self.fit_config_model = None  # Model for custom fit configurations
        self.fc = None  # Fit container
        self.alt_fc = None

        # Holding area for a newly loaded asset/closure that hasn't been committed as the
        # actively-measured one yet - see loaded_asset setter/_commit_pending_asset().
        self._pending_loaded_asset = None
        self._pending_ensembles = {}
        self._pending_blocks = {}

        # measurement data/loaded asset/fit results are NOT initialized here - they live on
        # self._pulsed_measurement.data/.objects (see the _settings/measurement_data/
        # _loaded_asset/_fit_result/_fit_result_alt properties below), which is only meaningfully
        # populated once qudi-core's StatusVar restoration has run (before on_activate()) and
        # once _initialize_data_arrays() has built real data arrays (start of on_activate()) -
        # exactly mirroring how _settings itself already worked before this class held a single
        # consolidated PulsedMeasurement instance: nothing here reads/writes settings-derived
        # state before activation either.

    @property
    def _settings(self):
        """The real, shared PulsedMeasurementSettings this module actually works with - lives on
        self._pulsed_measurement.settings.measurement_settings. Kept as a property backed by one
        consolidated PulsedMeasurement instance (rather than renaming every one of this class's
        many internal `self._settings.xxx` call sites) so this really is a change in storage -
        _settings/measurement_data/_loaded_asset/_fit_result/_fit_result_alt no longer
        independently exist as attributes - without needing to touch every existing read/write
        site below, which continue to work completely unchanged.
        """
        return self._pulsed_measurement.settings.measurement_settings

    @_settings.setter
    def _settings(self, new_settings):
        self._pulsed_measurement.settings = replace(
            self._pulsed_measurement.settings, measurement_settings=new_settings
        )

    @property
    def measurement_data(self):
        """The real, shared PulsedMeasurementData held on self._pulsed_measurement.data - mutated
        in place by the measurement loop exactly as before (PulsedMeasurementData is a plain,
        non-frozen dataclass); see _initialize_data_arrays() for how the object itself gets
        (re)built."""
        return self._pulsed_measurement.data.measurement_data

    @property
    def _loaded_asset(self):
        """Whichever PulseBlockEnsemble/PulseSequence is currently loaded, held on
        self._pulsed_measurement.objects.sequence. Not independently persisted - only ever set
        externally by PulsedMasterLogic, since this module has no Connector to
        SequenceGeneratorLogic to re-derive it on its own. sampling_information/
        measurement_information/generation_method_parameters are exposed as properties over this
        one reference rather than copied, avoiding the save/load race that existed when they were
        copied on every load."""
        return self._pulsed_measurement.objects.sequence

    @_loaded_asset.setter
    def _loaded_asset(self, asset):
        self._pulsed_measurement.objects.sequence = asset

    @property
    def _fit_result(self):
        return self._pulsed_measurement.data.fit_result

    @_fit_result.setter
    def _fit_result(self, result):
        self._pulsed_measurement.data.fit_result = result

    @property
    def _fit_result_alt(self):
        return self._pulsed_measurement.data.fit_result_alt

    @_fit_result_alt.setter
    def _fit_result_alt(self, result):
        self._pulsed_measurement.data.fit_result_alt = result

    def _apply_microwave_settings(self, new_settings):
        self._settings = replace(self._settings, microwave_settings=new_settings)

    def _apply_fast_counter_settings(self, new_settings):
        self._settings = replace(self._settings, fast_counter_settings=new_settings)

    def _apply_readout_settings(self, new_settings):
        self._settings = replace(self._settings, readout_settings=new_settings)

    def _apply_timer_interval(self, new_interval):
        self._timer_interval_s = float(new_interval)

    @property
    def loaded_asset(self):
        return self._loaded_asset

    @loaded_asset.setter
    def loaded_asset(self, asset):
        """Does NOT apply the new asset's settings immediately - only stores it as pending.
        Settings/data/the active loaded-asset reference all stay whatever they were before,
        fully self-consistent for saving, until _commit_pending_asset() runs at Play - see
        start_pulsed_measurement()."""
        if asset is not self._loaded_asset:
            self.log.warning(
                'A new sequence/ensemble has been loaded, but its settings have not been applied '
                'to the measurement yet - Saving before starting the measurement uses the '
                'previous asset\'s name, settings, and data.'
            )
        self._pending_loaded_asset = asset
        return

    def refresh_generator_settings(self, generator_settings):
        """Keep self._pulsed_measurement.settings.generator_settings fresh without a Connector to
        SequenceGeneratorLogic. PulsedMasterLogic (which has that connector) calls this whenever
        SequenceGeneratorLogic emits sigGeneratorSettingsUpdated/sigSamplingSettingsUpdated, so
        this module's held live state - not just its on-demand get_pulsed_measurement() snapshots
        - reflects the current generator settings."""
        self._pulsed_measurement.settings = replace(
            self._pulsed_measurement.settings, generator_settings=generator_settings
        )

    def refresh_loaded_asset_closure(self, ensembles, blocks):
        """Stores the resolved closure as pending, same as the loaded_asset setter - not applied
        until _commit_pending_asset() runs at Play, so the sequence and its ensembles/blocks
        always commit together, never independently."""
        self._pending_ensembles = dict(ensembles) if ensembles else {}
        self._pending_blocks = dict(blocks) if blocks else {}

    def _commit_pending_asset(self):
        """Promote whatever's pending (from the last loaded_asset/refresh_loaded_asset_closure()
        call) to the actively-measured asset - called at the start of start_pulsed_measurement(),
        right before settings get invoked from it and data arrays get rebuilt to match."""
        self._loaded_asset = self._pending_loaded_asset
        self._pulsed_measurement.objects.ensembles = self._pending_ensembles
        self._pulsed_measurement.objects.blocks = self._pending_blocks

    def get_pulsed_measurement(self, generator_settings=None, ensembles=None, blocks=None):
        """Build a PulsedMeasurement snapshot bundling this module's settings/data/loaded asset
        with the caller-supplied SequenceGeneratorSettings and resolved ensemble/block closure
        (this module has no Connector to SequenceGeneratorLogic, so it can't fetch those itself -
        see PulsedMasterLogic.get_pulsed_measurement()). Leave all three at their defaults for a
        measurement-only snapshot.

        Returns independent copies (not live references), so the snapshot is frozen in time.

        Parameters
        ----------
        generator_settings : SequenceGeneratorSettings
            Optional, the sequence generator settings in effect for this measurement.
        ensembles : dict
            Optional, {name: PulseBlockEnsemble} already resolved and copied by the caller (see
            SequenceGeneratorLogic.resolve_asset_closure()).
        blocks : dict
            Optional, {name: PulseBlock}, same caveat as `ensembles`.

        Returns
        -------
        PulsedMeasurement
            Snapshot of this module's current settings/data/loaded asset, plus the given
            generator_settings/ensembles/blocks.
        """
        current = self._pulsed_measurement
        return PulsedMeasurement(
            settings=Settings(
                measurement_settings=current.settings.measurement_settings,
                generator_settings=generator_settings,
            ),
            data=PulsedData(
                measurement_data=current.data.measurement_data.copy(),
                fit_result=current.data.fit_result,
                fit_result_alt=current.data.fit_result_alt,
            ),
            objects=PulseObjects(
                sequence=current.objects.sequence.copy() if current.objects.sequence is not None else None,
                ensembles=dict(ensembles) if ensembles else {},
                blocks=dict(blocks) if blocks else {},
            ),
        )

    def _migrate_legacy_settings_if_needed(self):
        """One-time upgrade path for a status file saved by either the pre-refactor version of
        this module (~20 individually-keyed StatusVars instead of one '_settings' key), or a
        later format with timer_interval_s/fit_configs nested inside '_settings' instead of their
        own independent StatusVars.

        qudi-core's StatusVar loading only looks up each StatusVar's own top-level key by name -
        it can't see old sibling keys or nested values elsewhere in the file, so without this,
        those values would silently reset to defaults and then be permanently lost on the next
        save (which overwrites, not merges). This detects both cases, backs up the raw file
        (pre-refactor case only), and rebuilds the affected attributes from the old values.
        """
        file_path = get_module_app_data_path(self.__class__.__name__, self.module_base, self.module_name)
        try:
            raw_status = yaml_load(file_path, ignore_missing=True)
        except Exception:
            self.log.exception('Failed to read status file while checking for legacy settings format:')
            return

        if '_settings' not in raw_status and PulsedMeasurementSettings.LEGACY_STATUS_VAR_KEYS.intersection(raw_status):
            self.log.warning(
                'Found measurement settings saved by a pre-refactor version of this module '
                '(individually-keyed microwave/fast counter/readout/etc. settings instead of the '
                'current consolidated format). Migrating them now instead of silently resetting to '
                'defaults. The original file has been backed up to "{0}.legacy_backup".'.format(file_path)
            )
            try:
                backup_path = file_path + '.legacy_backup'
                if not os.path.exists(backup_path):
                    shutil.copy2(file_path, backup_path)
            except Exception:
                self.log.exception(
                    'Failed to back up legacy status file before migrating (continuing anyway):'
                )

            try:
                self._settings = PulsedMeasurementSettings.from_legacy_dict(raw_status)
            except Exception:
                self.log.exception('Failed to migrate legacy measurement settings - keeping defaults:')

            # fit_configs' pre-refactor top-level key ('fit_configs') is identical to its current
            # independent StatusVar's key - qudi-core's normal restore already found it with no
            # help needed here. timer_interval_s's pre-refactor key was mangled/private, so it
            # still needs migrating explicitly.
            self._timer_interval_s = float(
                raw_status.get('_PulsedMeasurementLogic__timer_interval', self._timer_interval_s)
            )
            return

        # A status file saved by this session's brief in-between "everything in one dataclass"
        # format has timer_interval_s/fit_configs nested one level deeper (inside '_settings')
        # than either the pre-refactor or the current independent-StatusVar format expects - back
        # -fill them so upgrading from that format doesn't silently reset to defaults either.
        nested = raw_status.get('_settings')
        if isinstance(nested, dict):
            if 'timer_interval_s' in nested:
                self._timer_interval_s = float(nested['timer_interval_s'])
            if nested.get('fit_configs'):
                self._fit_configs = tuple(nested['fit_configs'])

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Must run first: self._settings has already been restored (or defaulted) by qudi-core's
        # StatusVar machinery by this point - see this method's docstring for why that default
        # can be wrong for a pre-refactor status file, and why this has to be corrected here
        # rather than in the StatusVar's own constructor function.
        self._migrate_legacy_settings_if_needed()

        # Create an instance of PulseExtractor
        self._pulseextractor = PulseExtractor(pulsedmeasurementlogic=self)
        self._pulseanalyzer = PulseAnalyzer(pulsedmeasurementlogic=self)
        # Create an instance of the alternative (second) plot manager
        self._altplotanalyzer = AltPlotAnalyzer(pulsedmeasurementlogic=self)

        # QTimer must be created here instead of __init__ because otherwise the timer will not run
        # in this logic's thread but in the manager instead.
        self.__analysis_timer = QtCore.QTimer()
        self.__analysis_timer.setSingleShot(False)
        self.__analysis_timer.setInterval(round(1000. * self._timer_interval_s))
        self.__analysis_timer.timeout.connect(self._pulsed_analysis_loop,
                                              QtCore.Qt.ConnectionType.QueuedConnection)

        # Fitting
        self.fit_config_model = FitConfigurationsModel(parent=self)
        self.fit_config_model.load_configs(self._fit_configs)
        # FitConfigurationsModel keeps its own internal storage behind Qt's row-based model API
        # (it's a QAbstractListModel from the separately-installed qudi-core package). No
        # sync-on-change listener is needed here - _fit_configs' representer (see its declaration)
        # reads self.fit_config_model.dump_configs() directly at save time, so the model is always
        # the live, authoritative source rather than something that needs mirroring into a
        # separately-stored value.
        self.fc = FitContainer(parent=self, config_model=self.fit_config_model)
        self.alt_fc = FitContainer(parent=self, config_model=self.fit_config_model)

        # Turn off pulse generator
        self.pulse_generator_off()

        # Check and configure fast counter
        binning_constraints = self._fastcounter().get_constraints()['hardware_binwidth_list']
        fixes = {}
        if self._settings.fast_counter_settings.bin_width not in binning_constraints:
            fixes['bin_width'] = binning_constraints[0]
        if self._settings.fast_counter_settings.record_length <= 0:
            fixes['record_length'] = 3e-6
        if fixes:
            self._apply_fast_counter_settings(self._settings.fast_counter_settings.update_from_dict(fixes))
        self.fast_counter_off()
        if self._fastcounter().is_gated() and self._settings.fast_counter_settings.number_of_gates < 1:
            self._apply_fast_counter_settings(
                replace(
                    self._settings.fast_counter_settings,
                    number_of_gates=max(1, self._settings.readout_settings.number_of_lasers),
                )
            )
        self.set_fast_counter_settings()

        # Check and configure external microwave
        if self._settings.microwave_settings.use_ext_microwave:
            self.microwave_off()
            self.set_microwave_settings(self._settings.microwave_settings.to_dict())
        # initialize arrays for the measurement data
        self._initialize_data_arrays()

        # Connect internal signals
        self.sigStartTimer.connect(self.__analysis_timer.start, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sigStopTimer.connect(self.__analysis_timer.stop, QtCore.Qt.ConnectionType.QueuedConnection)
        return

    def on_deactivate(self):
        """ Deactivate the module properly.
        """
        if self.module_state() == 'locked':
            self.stop_pulsed_measurement()
        self.__analysis_timer.timeout.disconnect()
        self.sigStartTimer.disconnect()
        self.sigStopTimer.disconnect()
        return

    @property
    def extraction_parameters(self):
        """The real, shared ExtractionParameters object (not a copy/dict) - PulseExtractor
        holds a reference to this exact instance and mutates it in place as its live state."""
        return self._settings.extraction_parameters

    @property
    def analysis_parameters(self):
        """The real, shared AnalysisParameters object (not a copy/dict) - PulseAnalyzer holds
        a reference to this exact instance and mutates it in place as its live state."""
        return self._settings.analysis_parameters

    @property
    def alternate_signal_settings(self):
        """The real, shared AlternativeSignalSettings object (not a copy/dict) - AltPlotAnalyzer
        holds a reference to this exact instance and mutates it in place as its live state."""
        return self._settings.alternate_signal_settings

    @property
    def fit_configs(self):
        """The persisted tuple of fit configuration dicts, backed by the independent _fit_configs
        StatusVar (see its declaration and the representer/constructor hooks below)."""
        return self._fit_configs

    @_fit_configs.representer
    def __repr_fit_configs(self, value):
        configs = self.fit_config_model.dump_configs()
        return configs if len(configs) >= 1 else None

    @_fit_configs.constructor
    def __constr_fit_configs(self, value):
        return value if value else _DEFAULT_FIT_CONFIGS

    ############################################################################
    # Fast counter control methods and properties
    ############################################################################
    @property
    def fast_counter_settings(self):
        return self._settings.fast_counter_settings.to_dict()

    @fast_counter_settings.setter
    def fast_counter_settings(self, settings):
        if not isinstance(settings, (FastCounterSettings, dict)):
            raise SettingsTypeError(f'fast_counter_settings expects FastCounterSettings or dict, '
                                    f'got {type(settings).__name__}')
        self.set_fast_counter_settings(settings)
        return

    @property
    def fast_counter_constraints(self):
        return self._fastcounter().get_constraints()

    @QtCore.Slot(dict)
    def set_fast_counter_settings(self, settings=None, **kwargs):
        """
        Either accepts a FastCounterSettings instance or a settings dictionary as positional
        argument, or keyword arguments. A dict/kwargs is a partial patch of the current settings,
        a FastCounterSettings is a full replacement. If both a settings_dict and kwargs are
        present, the keyword arguments take precedence for conflicting names.

        Parameters
        ----------
        settings : FastCounterSettings or dict or None
        kwargs
        """
        try:
            requested = coerce_settings(
                settings, kwargs, self._settings.fast_counter_settings, FastCounterSettings
            )
        except SettingsTypeError as err:
            # A caller mistake - log it and leave the settings untouched. Deliberately not raised:
            # this is a queued cross-thread slot, where an escaping exception cannot reach the
            # emitter and may take the whole application down mid-measurement.
            self.log.error(f'Unable to apply new fast counter settings. {err}')
            self.sigFastCounterSettingsUpdated.emit(self.fast_counter_settings)
            return self.fast_counter_settings

        # Check if fast counter is running and do nothing if that is the case
        counter_status = self._fastcounter().get_status()
        if not counter_status >= 2 and not counter_status < 0:
            # Set parameters, check if it is gated
            if not self._fastcounter().is_gated():
                requested = replace(requested, number_of_gates=0)

            self._apply_fast_counter_settings(requested)

            # Apply the settings to hardware
            bin_width, record_length, number_of_gates = self._fastcounter().configure(
                self._settings.fast_counter_settings.bin_width,
                self._settings.fast_counter_settings.record_length,
                self._settings.fast_counter_settings.number_of_gates
             )
            self._apply_fast_counter_settings(
                replace(
                    self._settings.fast_counter_settings,
                    bin_width=bin_width,
                    record_length=record_length,
                    number_of_gates=number_of_gates,
                    is_gated=self._fastcounter().is_gated(),
                )
            )
        else:
            self.log.warning('Fast counter is not idle (status: {0}).\n'
                             'Unable to apply new settings.'.format(counter_status))

        # emit update signal for master (GUI or other logic module)
        self.sigFastCounterSettingsUpdated.emit(self.fast_counter_settings)
        return self.fast_counter_settings

    def fast_counter_on(self):
        """Switching on the fast counter.

        Returns
        -------
        int
            Error code (0:OK, -1:error).
        """
        return self._fastcounter().start_measure()

    def fast_counter_off(self):
        """Switching off the fast counter.

        Returns
        -------
        int
            Error code (0:OK, -1:error).
        """
        return self._fastcounter().stop_measure()

    @QtCore.Slot(bool)
    def toggle_fast_counter(self, switch_on):
        """
        """
        if not isinstance(switch_on, bool):
            return -1

        if switch_on:
            err = self.fast_counter_on()
        else:
            err = self.fast_counter_off()
        return err

    def fast_counter_pause(self):
        """Switching off the fast counter.

        Returns
        -------
        int
            Error code (0:OK, -1:error).
        """
        return self._fastcounter().pause_measure()

    def fast_counter_continue(self):
        """Switching off the fast counter.

        Returns
        -------
        int
            Error code (0:OK, -1:error).
        """
        return self._fastcounter().continue_measure()

    @QtCore.Slot(bool)
    def fast_counter_pause_continue(self, continue_counter):
        """
        """
        if not isinstance(continue_counter, bool):
            return -1

        if continue_counter:
            err = self.fast_counter_continue()
        else:
            err = self.fast_counter_pause()
        return err

    @property
    def elapsed_sweeps(self):
        return self.measurement_data.elapsed_sweeps

    @property
    def elapsed_time(self):
        return self.measurement_data.elapsed_time

    @property
    def raw_data(self):
        return self.measurement_data.raw_data

    @property
    def laser_data(self):
        return self.measurement_data.laser_data

    @property
    def signal_data(self):
        return self.measurement_data.signal_data

    @property
    def signal_alt_data(self):
        return self.measurement_data.signal_alt_data

    @property
    def measurement_error(self):
        return self.measurement_data.measurement_error
    ############################################################################

    ############################################################################
    # External microwave control methods and properties
    ############################################################################
    @property
    def ext_microwave_settings(self):
        return self._settings.microwave_settings.to_dict()

    @ext_microwave_settings.setter
    def ext_microwave_settings(self, settings):
        if not isinstance(settings, (MicrowaveSettings, dict)):
            raise SettingsTypeError(f'ext_microwave_settings expects MicrowaveSettings or dict, '
                                    f'got {type(settings).__name__}')
        self.set_microwave_settings(settings)
        return
    
    @property
    def ext_microwave_constraints(self):
        microwave = self._microwave()
        if microwave is None:
            return None
        return microwave.constraints

    def microwave_on(self):
        """
        Turns the external (CW) microwave output on.

        :return int: error code (0:OK, -1:error)
        """
        microwave = self._microwave()
        if microwave is None:
            self.sigExtMicrowaveRunningUpdated.emit(False)
        else:
            microwave.cw_on()
            is_running = microwave.module_state() != 'idle'
            if not is_running:
                self.log.error('Failed to turn on external CW microwave output.')
            self.sigExtMicrowaveRunningUpdated.emit(is_running)
        return 0

    def microwave_off(self):
        """
        Turns the external (CW) microwave output off.

        :return int: error code (0:OK, -1:error)
        """
        microwave = self._microwave()
        if microwave is None:
            self.sigExtMicrowaveRunningUpdated.emit(False)
        else:
            microwave.off()
            is_running = microwave.module_state() != 'idle'
            if is_running:
                self.log.error('Failed to turn off external CW microwave output.')
            self.sigExtMicrowaveRunningUpdated.emit(is_running)
        return 0

    @QtCore.Slot(bool)
    def toggle_microwave(self, switch_on):
        """
        Turn the external microwave output on/off.

        :param switch_on: bool, turn microwave on (True) or off (False)
        :return int: error code (0:OK, -1:error)
        """
        if not isinstance(switch_on, bool):
            return -1

        if switch_on:
            err = self.microwave_on()
        else:
            err = self.microwave_off()
        return err

    @QtCore.Slot(dict)
    def set_microwave_settings(self, settings=None, **kwargs):
        """
        Apply new settings to the external microwave device.
        Either accepts a MicrowaveSettings instance or a settings dictionary as positional
        argument, or keyword arguments. A dict/kwargs is a partial patch of the current settings,
        a MicrowaveSettings is a full replacement. If both a settings_dict and kwargs are present,
        the keyword arguments take precedence for conflicting names.

        Parameters
        ----------
        settings : MicrowaveSettings or dict or None
        kwargs
        """
        try:
            requested = coerce_settings(
                settings, kwargs, self._settings.microwave_settings, MicrowaveSettings
            )
        except SettingsTypeError as err:
            # See set_fast_counter_settings() on why a caller mistake is logged, not raised, here.
            self.log.error(f'Unable to apply new microwave settings. {err}')
            self.sigExtMicrowaveSettingsUpdated.emit(self.ext_microwave_settings)
            return self.ext_microwave_settings

        # Check if microwave is running and do nothing if that is the case
        try:
            microwave = self._microwave()
            if (microwave is not None) and (microwave.module_state() != 'idle') :
                self.log.warning('Microwave device is running.\nUnable to apply new settings.')
            else:
                # use_ext_microwave can only ever be True if a microwave connector is actually
                # present - otherwise force it back to False regardless of what was requested.
                if requested.use_ext_microwave and microwave is None:
                    requested = replace(requested, use_ext_microwave=False)
                self._apply_microwave_settings(requested)
                if self._settings.microwave_settings.use_ext_microwave and microwave is not None:
                    # Apply the settings to hardware
                    microwave.set_cw(
                        frequency=self._settings.microwave_settings.frequency, power=self._settings.microwave_settings.power
                    )
                    self._apply_microwave_settings(
                        replace(
                            self._settings.microwave_settings,
                            frequency=microwave.cw_frequency,
                            power=microwave.cw_power,
                        )
                    )
                
        finally:
            # emit update signal for master (GUI or other logic module)
            self.sigExtMicrowaveSettingsUpdated.emit(self.ext_microwave_settings)
        return self.ext_microwave_settings
    ############################################################################

    ############################################################################
    # Pulse generator control methods and properties
    ############################################################################
    @property
    def pulse_generator_constraints(self):
        return self._pulsegenerator().get_constraints()

    def pulse_generator_on(self):
        """Switching on the pulse generator. """
        err = self._pulsegenerator().pulser_on()
        if err < 0:
            self.log.error('Failed to turn on pulse generator output.')
            self.sigPulserRunningUpdated.emit(False)
        else:
            self.sigPulserRunningUpdated.emit(True)
        return err

    def pulse_generator_off(self):
        """Switching off the pulse generator. """
        err = self._pulsegenerator().pulser_off()
        if err < 0:
            self.log.error('Failed to turn off pulse generator output.')
            self.sigPulserRunningUpdated.emit(True)
        else:
            self.sigPulserRunningUpdated.emit(False)
        return err

    @QtCore.Slot(bool)
    def toggle_pulse_generator(self, switch_on):
        """
        Switch the pulse generator on or off.

        :param switch_on: bool, turn the pulse generator on (True) or off (False)
        :return int: error code (0: OK, -1: error)
        """
        if not isinstance(switch_on, bool):
            return -1

        if switch_on:
            err = self.pulse_generator_on()
        else:
            err = self.pulse_generator_off()
        return err
    ############################################################################

    ############################################################################
    # Measurement control methods and properties
    ############################################################################
    @property
    def measurement_settings(self):
        return self._settings.readout_settings.to_dict()

    @measurement_settings.setter
    def measurement_settings(self, settings):
        if not isinstance(settings, (ReadoutSettings, dict)):
            raise SettingsTypeError(f'measurement_settings expects ReadoutSettings or dict, '
                                    f'got {type(settings).__name__}')
        self.set_measurement_settings(settings)
        return

    @property
    def measurement_information(self):
        """Metadata about whichever PulseBlockEnsemble/PulseSequence is currently loaded - read
        live off the loaded_asset reference (see loaded_asset property), not an independently
        stored copy."""
        if self._loaded_asset is None:
            return MeasurementInformation().to_dict()
        return self._loaded_asset.measurement_information.to_dict()

    @measurement_information.setter
    def measurement_information(self, info_dict):
        if isinstance(info_dict, MeasurementInformation):
            info = info_dict
        elif isinstance(info_dict, dict):
            info = MeasurementInformation.from_dict(info_dict)
        else:
            info = MeasurementInformation()

        if self._loaded_asset is None:
            self.log.warning('Cannot set measurement_information: no asset is currently loaded.')
            return

        # Check if mandatory params to invoke settings are missing and set empty container in
        # that case.
        if not info.is_valid:
            self.log.debug('The set measurement_information did not contain all the necessary '
                           'information or was not a dict. Setting empty container.')
            self._loaded_asset.measurement_information = MeasurementInformation()
            return

        # Mutate the loaded asset's own measurement_information - the same object
        # SequenceGeneratorLogic sees, not a copy.
        self._loaded_asset.measurement_information = info

        # invoke settings if needed
        if self._settings.readout_settings.invoke_settings and info:
            self._apply_invoked_settings()
            self.sigMeasurementSettingsUpdated.emit(self.measurement_settings)
        return

    @property
    def sampling_information(self):
        """Sampling metadata for whichever PulseBlockEnsemble/PulseSequence is currently loaded
        - read live off the loaded_asset reference, not an independently stored copy. Returns a
        plain dict, consistent with the sibling measurement_information/generation_method_parameters
        properties (previously returned the raw SamplingInformation dataclass instance)."""
        if self._loaded_asset is None:
            return SamplingInformation().to_dict()
        return self._loaded_asset.sampling_information.to_dict()

    @sampling_information.setter
    def sampling_information(self, info_dict):
        if isinstance(info_dict, SamplingInformation):
            new_value = info_dict
        elif isinstance(info_dict, dict):
            new_value = SamplingInformation.from_dict(info_dict)
        else:
            new_value = SamplingInformation()
        if self._loaded_asset is None:
            self.log.warning('Cannot set sampling_information: no asset is currently loaded.')
            return
        self._loaded_asset.sampling_information = new_value
        return

    @property
    def generation_method_parameters(self):
        """Generation kwargs for whichever PulseBlockEnsemble/PulseSequence is currently loaded
        - read live off the loaded_asset reference, not an independently stored copy."""
        if self._loaded_asset is None:
            return GenerationMethodParameters().to_dict()
        return self._loaded_asset.generation_method_parameters.to_dict()

    @generation_method_parameters.setter
    def generation_method_parameters(self, info_dict):
        if isinstance(info_dict, GenerationMethodParameters):
            new_value = info_dict
        elif isinstance(info_dict, dict):
            new_value = GenerationMethodParameters.from_dict(info_dict)
        else:
            new_value = GenerationMethodParameters()
        if self._loaded_asset is None:
            self.log.warning('Cannot set generation_method_parameters: no asset is currently loaded.')
            return
        self._loaded_asset.generation_method_parameters = new_value
        return

    @property
    def timer_interval(self):
        return float(self._timer_interval_s)

    @timer_interval.setter
    def timer_interval(self, value):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SettingsTypeError(f'timer_interval expects int or float, '
                                    f'got {type(value).__name__}')
        self.set_timer_interval(value)
        return

    @property
    def alternative_data_type(self):
        method = self._altplotanalyzer.current_method
        return str(method) if method is not None else 'None'

    @alternative_data_type.setter
    def alternative_data_type(self, alt_data_type):
        if not (isinstance(alt_data_type, str) or alt_data_type is None):
            raise SettingsTypeError(f'alternative_data_type expects str or None, '
                                    f'got {type(alt_data_type).__name__}')
        self.set_alternative_data_type(alt_data_type)
        return

    @property
    def alt_plot_methods(self):
        """ Naturally sorted list of available alternative (second) plot method names. """
        return self._altplotanalyzer.alt_plot_methods

    @property
    def alt_plot_labels(self):
        """ Axis labels for every alternative plot method (dict of primitive strings). """
        return self._altplotanalyzer.alt_plot_labels

    @property
    def analysis_methods(self):
        return self._pulseanalyzer.analysis_methods

    @property
    def extraction_methods(self):
        return self._pulseextractor.extraction_methods

    @property
    def analysis_settings(self):
        return self._pulseanalyzer.analysis_settings

    @analysis_settings.setter
    def analysis_settings(self, settings_dict):
        # No dataclass counterpart: AnalysisParameters is a dict subclass because the parameter
        # names are discovered at runtime from whichever analysis method is selected.
        if not isinstance(settings_dict, dict):
            raise SettingsTypeError(f'analysis_settings expects a dict, '
                                    f'got {type(settings_dict).__name__}')
        self.set_analysis_settings(settings_dict)
        return

    @property
    def extraction_settings(self):
        return self._pulseextractor.extraction_settings

    @extraction_settings.setter
    def extraction_settings(self, settings_dict):
        # No dataclass counterpart - see analysis_settings above.
        if not isinstance(settings_dict, dict):
            raise SettingsTypeError(f'extraction_settings expects a dict, '
                                    f'got {type(settings_dict).__name__}')
        self.set_extraction_settings(settings_dict)
        return

    @QtCore.Slot(dict)
    def set_analysis_settings(self, settings_dict=None, **kwargs):
        """
        Apply new analysis settings.
        Either accept a settings dictionary as positional argument or keyword arguments.
        If both are present both are being used by updating the settings_dict with kwargs.
        The keyword arguments take precedence over the items in settings_dict if there are
        conflicting names.

        Parameters
        ----------
        settings_dict
        kwargs
        """
        # Determine complete settings dictionary. Built as a new dict rather than updated in
        # place, so the caller's dict is never mutated by the bin snapping below.
        try:
            settings_dict = as_settings_dict(settings_dict, kwargs)
        except SettingsTypeError as err:
            # See set_fast_counter_settings() on why a caller mistake is logged, not raised, here.
            self.log.error(f'Unable to apply new analysis settings. {err}')
            self.sigAnalysisSettingsUpdated.emit(self.analysis_settings)
            return

        bin_width = self.fast_counter_settings['bin_width']
        for key in settings_dict:
            if key in ['signal_start', 'signal_end', 'norm_start', 'norm_end']:
                num_bins_fast = round(settings_dict[key] / bin_width)
                settings_dict[key] = num_bins_fast * bin_width

        # Use threadlock to update settings during a running measurement
        with self._threadlock:
            self._pulseanalyzer.analysis_settings = settings_dict
            self.sigAnalysisSettingsUpdated.emit(self.analysis_settings)
        return

    @QtCore.Slot(dict)
    def set_extraction_settings(self, settings_dict=None, **kwargs):
        """
        Apply new extraction settings.
        Either accept a settings dictionary as positional argument or keyword arguments.
        If both are present both are being used by updating the settings_dict with kwargs.
        The keyword arguments take precedence over the items in settings_dict if there are
        conflicting names.

        Parameters
        ----------
        settings_dict
        kwargs
        """
        # Determine complete settings dictionary
        try:
            settings_dict = as_settings_dict(settings_dict, kwargs)
        except SettingsTypeError as err:
            # See set_fast_counter_settings() on why a caller mistake is logged, not raised, here.
            self.log.error(f'Unable to apply new extraction settings. {err}')
            self.sigExtractionSettingsUpdated.emit(self.extraction_settings)
            return

        # Use threadlock to update settings during a running measurement
        with self._threadlock:
            self._pulseextractor.extraction_settings = settings_dict
            self.sigExtractionSettingsUpdated.emit(self.extraction_settings)
        return

    @QtCore.Slot(dict)
    def set_measurement_settings(self, settings=None, **kwargs):
        """
        Apply new measurement (readout) settings.
        Either accepts a ReadoutSettings instance or a settings dictionary as positional argument,
        or keyword arguments. A dict/kwargs is a partial patch of the current settings, a
        ReadoutSettings is a full replacement. If both a settings_dict and kwargs are present, the
        keyword arguments take precedence for conflicting names.

        Note that not every field can be applied in every module state: only invoke_settings,
        units and labels are accepted while a measurement is running. Fields the current state
        does not permit keep their current value, whether they were named explicitly or arrived
        as part of a complete ReadoutSettings.

        Parameters
        ----------
        settings : ReadoutSettings or dict or None
        kwargs
        """
        try:
            requested = coerce_settings(
                settings, kwargs, self._settings.readout_settings, ReadoutSettings
            )
        except SettingsTypeError as err:
            # See set_fast_counter_settings() on why a caller mistake is logged, not raised, here.
            self.log.error(f'Unable to apply new measurement settings. {err}')
            self.sigMeasurementSettingsUpdated.emit(self.measurement_settings)
            return self.measurement_settings

        # invoke_settings flag can change regardless of module state
        self._apply_readout_settings(
            replace(self._settings.readout_settings, invoke_settings=requested.invoke_settings)
        )
        if self._settings.readout_settings.invoke_settings:
            if self.measurement_information:
                self._apply_invoked_settings()
        else:
            # units/labels can change while a measurement is running
            with self._threadlock:
                self._apply_readout_settings(
                    replace(
                        self._settings.readout_settings,
                        units=requested.units,
                        labels=requested.labels,
                    )
                )
            if self.module_state() == 'idle':
                # Everything else describes the pulse sequence being measured and can only change
                # while no measurement is running.
                self._apply_readout_settings(
                    replace(
                        self._settings.readout_settings,
                        controlled_variable=requested.controlled_variable,
                        number_of_lasers=requested.number_of_lasers,
                        laser_ignore_list=requested.laser_ignore_list,
                        alternating=requested.alternating,
                    )
                )
                # A gated fast counter needs exactly one gate per laser pulse. Driven off the
                # actual values rather than "was number_of_lasers named in the call", so this also
                # heals the two ever drifting apart.
                if (self._fastcounter().is_gated()
                        and self._settings.fast_counter_settings.number_of_gates
                        != self._settings.readout_settings.number_of_lasers):
                    self.set_fast_counter_settings(
                        number_of_gates=self._settings.readout_settings.number_of_lasers)
        self._measurement_settings_sanity_check()
        self.sigMeasurementSettingsUpdated.emit(self.measurement_settings)
        return self.measurement_settings

    @QtCore.Slot(bool, str)
    def toggle_pulsed_measurement(self, start, stash_raw_data_tag=''):
        """
        Convenience method to start/stop measurement.

        Parameters
        ----------
        start : bool
            Start the measurement (True) or stop the measurement (False).
        """
        if start:
            self.start_pulsed_measurement(stash_raw_data_tag)
        else:
            self.stop_pulsed_measurement(stash_raw_data_tag)
        return

    @QtCore.Slot(str)
    def start_pulsed_measurement(self, stashed_raw_data_tag=''):
        """Start the analysis loop."""
        self.sigMeasurementStatusUpdated.emit(True, False)

        self._commit_pending_asset()

        # Check if measurement settings need to be invoked
        if self._settings.readout_settings.invoke_settings:
            if self.measurement_information:
                self._apply_invoked_settings()
                self.sigMeasurementSettingsUpdated.emit(self.measurement_settings)
            else:
                # abort measurement if settings could not be invoked
                self.log.error('Unable to invoke measurement settings.\nThis feature can only be '
                               'used when creating the pulse sequence via predefined methods.\n'
                               'Aborting measurement start.')
                self.set_measurement_settings(invoke_settings=False)
                self.sigMeasurementStatusUpdated.emit(False, False)
                return

        with self._threadlock:
            if self.module_state() == 'idle':
                # Lock module state
                self.module_state.lock()

                # Clear previous fits
                self.do_fit('No Fit', False)
                self.do_fit('No Fit', True)

                # initialize data arrays
                self._initialize_data_arrays()

                # recall stashed raw data
                if self._data_stash.recall(stashed_raw_data_tag) is not None:
                     self.log.info('Starting pulsed measurement with stashed raw data "{0}".'
                                     ''.format(stashed_raw_data_tag))
                #On a miss, the recall method will return None,
                #the log message will not be printed, and the active_tag is cleared internally

                # start microwave source
                if self._settings.microwave_settings.use_ext_microwave:
                    self.microwave_on()
                # start fast counter
                self.fast_counter_on()
                # start pulse generator
                self.pulse_generator_on()

                # start the time and set the paused state to false
                self.__execution_state.start()

                # initialize analysis_timer
                self.sigTimerUpdated.emit(self.measurement_data.elapsed_time,
                                          self.measurement_data.elapsed_sweeps,
                                          self._timer_interval_s)

                self.sigStartTimer.emit()
            else:
                self.log.warning('Unable to start pulsed measurement. Measurement already running.')
        return

    @QtCore.Slot(str)
    def stop_pulsed_measurement(self, stash_raw_data_tag=''):
        """
        Stop the measurement
        """
        # Get raw data and analyze it a last time just before stopping the measurement. Best
        # effort - must never block stopping the measurement - but still logged, unlike a bare
        # `except: pass` which would also swallow KeyboardInterrupt/SystemExit.
        try:
            self._pulsed_analysis_loop()
        except Exception:
            self.log.exception('Final analysis pass before stopping measurement failed:')

        with self._threadlock:
            if self.module_state() == 'locked':
                # stopping the timer
                self.sigStopTimer.emit()
                # Turn off fast counter
                self.fast_counter_off()
                # Turn off pulse generator
                self.pulse_generator_off()
                # Turn off microwave source
                if self._settings.microwave_settings.use_ext_microwave:
                    self.microwave_off()

                # stash raw data if requested
                if stash_raw_data_tag:
                          self._data_stash.stash(stash_raw_data_tag, self.measurement_data.raw_data,
                                                 self.measurement_data.elapsed_sweeps, self.measurement_data.elapsed_time)
                self._data_stash.clear_active()

                # Set measurement paused flag
                self.__execution_state.is_paused = False

                self.module_state.unlock()
                self.sigMeasurementStatusUpdated.emit(False, False)
        return

    @QtCore.Slot(bool)
    def toggle_measurement_pause(self, pause):
        """
        Convenience method to pause/continue measurement.

        Parameters
        ----------
        pause : bool
            Pause the measurement (True) or continue the measurement (False).
        """
        if pause:
            self.pause_pulsed_measurement()
        else:
            self.continue_pulsed_measurement()
        return

    @QtCore.Slot()
    def pause_pulsed_measurement(self):
        """
        Pauses the measurement
        """
        with self._threadlock:
            if self.module_state() == 'locked':
                # pausing the timer
                if self.__analysis_timer.isActive():
                    # stopping the timer
                    self.sigStopTimer.emit()

                self.fast_counter_pause()
                self.pulse_generator_off()
                if self._settings.microwave_settings.use_ext_microwave:
                    self.microwave_off()

                # Set measurement paused flag
                self.__execution_state.pause()

                self.sigMeasurementStatusUpdated.emit(True, True)
            else:
                self.log.warning('Unable to pause pulsed measurement. No measurement running.')
                self.sigMeasurementStatusUpdated.emit(False, False)
        return

    @QtCore.Slot()
    def continue_pulsed_measurement(self):
        """
        Continues the measurement
        """
        with self._threadlock:
            if self.module_state() == 'locked':
                if self._settings.microwave_settings.use_ext_microwave:
                    self.microwave_on()
                self.fast_counter_continue()
                self.pulse_generator_on()

                # un-pausing the timer
                if not self.__analysis_timer.isActive():
                    self.sigStartTimer.emit()

                # Set measurement paused flag
                self.__execution_state.unpause()

                self.sigMeasurementStatusUpdated.emit(True, False)
            else:
                self.log.warning('Unable to continue pulsed measurement. No measurement running.')
                self.sigMeasurementStatusUpdated.emit(False, False)
        return

    @QtCore.Slot(float)
    @QtCore.Slot(int)
    def set_timer_interval(self, interval):
        """
        Change the interval of the measurement analysis timer.

        Parameters
        ----------
        interval : int or float
            Interval of the timer in s.
        """
        if not isinstance(interval, (int, float)) or isinstance(interval, bool):
            # See set_fast_counter_settings() on why a caller mistake is logged, not raised, here.
            self.log.error(f'Unable to set timer interval. Expected int or float, got '
                           f'{type(interval).__name__}.')
            return
        with self._threadlock:
            self._apply_timer_interval(interval)
            if self._timer_interval_s > 0:
                self.__analysis_timer.setInterval(int(1000. * self._timer_interval_s))
                if self.module_state() == 'locked' and not self.__execution_state.is_paused:
                    self.sigStartTimer.emit()
            else:
                self.sigStopTimer.emit()

            self.sigTimerUpdated.emit(self.measurement_data.elapsed_time, self.measurement_data.elapsed_sweeps,
                                      self._timer_interval_s)
        return

    @QtCore.Slot(str)
    def set_alternative_data_type(self, alt_data_type):
        """ Set the selected alternative (second) plot method by name ('None'/None disables it). """
        if not (isinstance(alt_data_type, str) or alt_data_type is None):
            # See set_fast_counter_settings() on why a caller mistake is logged, not raised, here.
            self.log.error(f'Unable to set alternative data type. Expected str or None, got '
                           f'{type(alt_data_type).__name__}.')
            return
        with self._threadlock:
            if alt_data_type != self.alternative_data_type:
                self.do_fit('No Fit', True)

            if alt_data_type in (None, 'None', ''):
                self._altplotanalyzer.current_method = None
            elif alt_data_type not in self._altplotanalyzer.alt_plot_methods:
                self.log.error('Unknown alternative plot method "{0}". Keeping previous type "{1}".'
                               ''.format(alt_data_type, self.alternative_data_type))
            elif not self._altplotanalyzer.is_available(alt_data_type):
                self.log.error('Alternative plot method "{0}" is not available for the current '
                               'measurement settings. Disabling alternative plot.'
                               ''.format(alt_data_type))
                self._altplotanalyzer.current_method = None
            else:
                self._altplotanalyzer.current_method = alt_data_type

            self._compute_alt_data()
            self.sigMeasurementDataUpdated.emit()
        return

    @QtCore.Slot()
    def manually_pull_data(self):
        """ Analyse and display the data
        """
        if self.module_state() == 'locked':
            self._pulsed_analysis_loop()
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def do_fit(self, fit_config, use_alternative_data=False):
        """
        Performs the chosen fit on the measured data.

        Parameters
        ----------
        fit_config : str
            Name of the fit configuration to use.
        use_alternative_data : bool
            Flag indicating if the signal data (False) or the alternative signal data (True)
            should be fitted. Ignored if data is given as parameter.

        Returns
        -------
        result_object
            The lmfit result object.
        """
        container = self.alt_fc if use_alternative_data else self.fc
        data = self.measurement_data.signal_alt_data if use_alternative_data else self.measurement_data.signal_data
        try:
            config, result = container.fit_data(fit_config, data[0], data[1])
            if result:
                result.result_str = container.formatted_result(result)
            if use_alternative_data:
                self._fit_result_alt = result
            else:
                self._fit_result = result

        except:
            config, result = '', None
            self.log.exception('Something went wrong while trying to perform data fit.')

        self.sigFitUpdated.emit(config, result, use_alternative_data)
        return result

    def _apply_invoked_settings(self):
        info = self._loaded_asset.measurement_information if self._loaded_asset is not None else MeasurementInformation()
        if not isinstance(info, MeasurementInformation) or not info.is_valid:
            self.log.warning('Can\'t invoke measurement settings from sequence information '
                         'since no measurement_information container is given.')
            return

        # First try to set parameters that can be changed during a running measurement
        units_labels = {}
        if info.units is not None:
            units_labels['units'] = info.units
        if info.labels is not None:
            units_labels['labels'] = info.labels
        if units_labels:
            with self._threadlock:
                self._apply_readout_settings(self._settings.readout_settings.update_from_dict(units_labels))

        if self.module_state() == 'locked':
            return

        required = ('number_of_lasers', 'laser_ignore_list', 'alternating', 'controlled_variable', 'counting_length')
        for key in required:
            if getattr(info, key) is None:
                self.log.error(f'Unable to invoke setting for "{key}".\n'
                           'Measurement information container is incomplete/invalid.')
                return

        self._apply_readout_settings(
            self._settings.readout_settings.update_from_dict({
                'number_of_lasers': int(info.number_of_lasers),
                'laser_ignore_list': sorted(info.laser_ignore_list),
                'alternating': bool(info.alternating),
                'controlled_variable': info.controlled_variable,
            })
        )

        fast_counter_record_length = info.counting_length
        if self._fastcounter().is_gated():
            self.set_fast_counter_settings(number_of_gates=self._settings.readout_settings.number_of_lasers,
                                       record_length=fast_counter_record_length)
        else:
            self.set_fast_counter_settings(record_length=fast_counter_record_length)
        return

    def _measurement_settings_sanity_check(self):
        settings = self._settings.readout_settings
        number_of_analyzed_lasers = settings.number_of_lasers - len(settings.laser_ignore_list)
        if len(settings.controlled_variable) < 1:
            self.log.error('Tried to set empty controlled variables array. This can not work.')

        if settings.alternating and (number_of_analyzed_lasers // 2) != len(settings.controlled_variable):
            self.log.error('Half of the number of laser pulses to analyze ({0}) does not match the '
                       'number of controlled_variable ticks ({1:d}).'
                       ''.format(number_of_analyzed_lasers // 2, len(settings.controlled_variable)))
        elif not settings.alternating and number_of_analyzed_lasers != len(settings.controlled_variable):
            self.log.error('Number of laser pulses to analyze ({0:d}) does not match the number of '
                       'controlled_variable ticks ({1:d}).'
                       ''.format(number_of_analyzed_lasers, len(settings.controlled_variable)))

        if self._fastcounter().is_gated() and settings.number_of_lasers != self._settings.fast_counter_settings.number_of_gates:
            self.log.error('Gated fast counter gate number ({0:d}) differs from number of laser pulses ({1:d})'
                       'configured in measurement settings.'.format(settings.number_of_lasers,
                                                                    self._settings.fast_counter_settings.number_of_gates))
        return

    def _report_analysis_problem(self, message, level='error'):
        """Report a problem hit during a pulsed-analysis tick: logged (at `level`, default
        'error' - pass 'warning' for a more routine/transient condition) and emitted via
        sigAnalysisProblem, but only when `message` differs from the last-reported one - a
        persistent problem (e.g. a settings mismatch that isn't self-correcting) logs/emits once
        instead of spamming identically on every single tick. Always pair a call to this with an
        early `return` from the tick that hit the problem - see _pulsed_analysis_loop()."""
        if message != self.__last_analysis_problem:
            getattr(self.log, level)(message)
            self.sigAnalysisProblem.emit(message)
        self.__last_analysis_problem = message

    def _clear_analysis_problem(self):
        """Signal that analysis has recovered, if it was previously reported as broken."""
        if self.__last_analysis_problem is not None:
            self.sigAnalysisProblem.emit('')
        self.__last_analysis_problem = None

    @QtCore.Slot()
    def _pulsed_analysis_loop(self):
        """ Acquires laser pulses from fast counter,
            calculates fluorescence signal and creates plots.
        """
        with self._threadlock:

            try:
                self._extract_laser_pulses()
                tmp_signal, tmp_error = self._analyze_laser_pulses()
            except Exception:
                self.log.exception('Pulsed analysis failed this tick - keeping previous data:')
                self._report_analysis_problem(
                    'Pulsed analysis raised an exception - see log for the full traceback.'
                )
                return

            # laser_data legitimately being all-zero is effectively never valid experimental data
            # (shot noise alone makes an exactly-zero trace vanishingly unlikely) - it means
            # extraction either got a flat/undetectable trace or explicitly gave up (several
            # built-in extraction methods, and potentially custom ones, silently return all-zeros
            # on failure rather than raising). raw_data is already available from
            # _extract_laser_pulses() above, so distinguish the two likely causes for free rather
            # than reporting one generic message.
            if not self.measurement_data.laser_data.any():
                if not self.measurement_data.raw_data.any():
                    self._report_analysis_problem(
                        'No new laser pulses: the fast counter returned all zeros this tick. '
                        'Check that the laser/trigger sequence is actually running and the '
                        'counter is wired/gated correctly.',
                        level='warning',
                    )
                else:
                    self._report_analysis_problem(
                        'No new laser pulses: raw counts were received, but extraction could not '
                        'find a laser pulse edge in the trace. Check the extraction method/'
                        'parameters against the actual pulse sequence.',
                        level='warning',
                    )
                return

            # exclude laser pulses to ignore
            ignore_list = list(self._settings.readout_settings.laser_ignore_list)
            if ignore_list:
                ignore_list = sorted(idx if idx >= 0 else len(tmp_signal) + idx for idx in ignore_list)
                tmp_signal = np.delete(tmp_signal, ignore_list)
                tmp_error = np.delete(tmp_error, ignore_list)


            # order data according to alternating flag
            if self._settings.readout_settings.alternating:
                if len(self.measurement_data.signal_data[0]) != len(tmp_signal[::2]):
                    self._report_analysis_problem(
                        'Length of controlled variable ({0}) does not match length of number of readout '
                        'pulses ({1}).'.format(len(self.measurement_data.signal_data[0]), len(tmp_signal[::2]))
                    )
                    return
                self.measurement_data.signal_data[1] = tmp_signal[::2]
                self.measurement_data.signal_data[2] = tmp_signal[1::2]
                self.measurement_data.measurement_error[1] = tmp_error[::2]
                self.measurement_data.measurement_error[2] = tmp_error[1::2]
            else:
                if len(self.measurement_data.signal_data[0]) != len(tmp_signal):
                    self._report_analysis_problem(
                        'Length of controlled variable ({0}) does not match length of number of readout '
                        'pulses ({1}).'.format(len(self.measurement_data.signal_data[0]), len(tmp_signal))
                    )
                    return
                self.measurement_data.signal_data[1] = tmp_signal
                self.measurement_data.measurement_error[1] = tmp_error

            # Compute alternative data array from signal
            self._compute_alt_data()

            self._clear_analysis_problem()

            # emit signals
            self.sigTimerUpdated.emit(self.measurement_data.elapsed_time, self.measurement_data.elapsed_sweeps,
                                      self._timer_interval_s)
            self.sigMeasurementDataUpdated.emit()
            return

    def _extract_laser_pulses(self):
        # Get counter raw data (including recalled raw data from previous measurement)
        if self.module_state() == 'locked':
            fc_data, info_dict = self._get_raw_data()
            self.measurement_data.raw_data = fc_data
            self.measurement_data.elapsed_sweeps = info_dict['elapsed_sweeps']
            self.measurement_data.elapsed_time = info_dict['elapsed_time']

        # extract laser pulses from raw data - runs every tick, using whichever raw_data is
        # currently available (freshly fetched above if locked, otherwise whatever was there
        # before), so analysis/plotting keeps refreshing even with no measurement running
        return_dict = self._pulseextractor.extract_laser_pulses(self.measurement_data.raw_data)
        self.measurement_data.laser_data = return_dict['laser_counts_arr']
        return

    def _analyze_laser_pulses(self):
        # analyze pulses and get data points for signal array. Also check if extraction
        # worked (non-zero array returned).
        if self.measurement_data.laser_data.any():
            tmp_signal, tmp_error = self._pulseanalyzer.analyse_laser_pulses(
                self.measurement_data.laser_data)
        else:
            tmp_signal = np.zeros(self.measurement_data.laser_data.shape[0])
            tmp_error = np.zeros(self.measurement_data.laser_data.shape[0])
        return tmp_signal, tmp_error

    def _get_raw_data(self):
        """
        Get the raw count data from the fast counting hardware and perform sanity checks.
        Also add recalled raw data to the newly received data.

        Returns
        -------
        tuple(numpy.ndarray, info_dict)
            The count data (1D for ungated, 2D for gated counter) and info_dict with keys
            'elapsed_sweeps' and 'elapsed_time'.
        """
        # get raw data from fast counter
        fc_data = self._fastcounter().get_data_trace()
        if type(fc_data) == tuple and len(fc_data) == 2:  # if the hardware implement the new version of the interface
            fc_data, info_dict = fc_data
        else:
            info_dict = {'elapsed_sweeps': None, 'elapsed_time': None}
        fc_data = netobtain(fc_data)

        if isinstance(info_dict, dict) and info_dict.get('elapsed_sweeps') is not None:
            elapsed_sweeps = info_dict['elapsed_sweeps']
        else:
            elapsed_sweeps = -1

        if isinstance(info_dict, dict) and info_dict.get('elapsed_time') is not None:
            elapsed_time = info_dict['elapsed_time']
        else:
            elapsed_time = self.__execution_state.get_live_elapsed_time()

        # add old raw data from previous measurements if necessary
        # (the "starting measurement with stashed raw data" info log already happens once, in
        # start_pulsed_measurement() - logging it again here on every analysis timer tick would
        # spam the log for the whole duration of the measurement)
        stashed_data = self._data_stash.get_active()
        if stashed_data is not None:
            elapsed_sweeps += stashed_data['elapsed_sweeps']
            elapsed_time += stashed_data['elapsed_time']
            if not fc_data.any():
                self.log.warning('Only zeros received from fast counter!\n'
                                 'Using recalled raw data only.')
                fc_data = stashed_data['data']
            elif stashed_data['data'].shape == fc_data.shape:
                self.log.debug('Recalled raw data has the same shape as current data.')
                fc_data = stashed_data['data'] + fc_data
            else:
                self.log.warning('Recalled raw data has not the same shape as current data.'
                                 '\nDid NOT add recalled raw data to current time trace.')
        elif not fc_data.any():
            self.log.warning('Only zeros received from fast counter!')
            fc_data = np.zeros(fc_data.shape, dtype='int64')

        return fc_data, {'elapsed_sweeps': elapsed_sweeps, 'elapsed_time': elapsed_time}

    def _initialize_data_arrays(self):
        """
        Initializing the signal, error, laser and raw data arrays.

        Builds a fresh PulsedMeasurementData/PulsedData and assigns self._pulsed_measurement.data
        wholesale (rather than mutating an existing object's fields in place), since - unlike
        before this class held one consolidated PulsedMeasurement instance - self._pulsed_measurement.data
        may not exist yet the very first time this runs (defaults to None; see PulsedMeasurement).
        fit_result/fit_result_alt are carried over from whatever data object existed before (or
        None on first call) - this method has never touched them, on either side of this change.
        """
        settings = self._settings.readout_settings
        fc_settings = self._settings.fast_counter_settings
        signal_dim = 3 if settings.alternating else 2

        signal_data = np.zeros((signal_dim, len(settings.controlled_variable)), dtype=float)
        signal_data[0] = settings.controlled_variable

        signal_alt_data = np.zeros((signal_dim, len(settings.controlled_variable)), dtype=float)
        signal_alt_data[0] = settings.controlled_variable

        measurement_error = np.zeros((signal_dim, len(settings.controlled_variable)), dtype=float)
        measurement_error[0] = settings.controlled_variable

        number_of_bins = int(fc_settings.record_length / fc_settings.bin_width)
        laser_length = number_of_bins if fc_settings.number_of_gates > 0 else 500
        laser_data = np.zeros((settings.number_of_lasers, laser_length), dtype='int64')

        if fc_settings.number_of_gates > 0:
            raw_data = np.zeros((settings.number_of_lasers, number_of_bins), dtype='int64')
        else:
            raw_data = np.zeros(number_of_bins, dtype='int64')

        new_measurement_data = PulsedMeasurementData(
            signal_data=signal_data,
            signal_alt_data=signal_alt_data,
            measurement_error=measurement_error,
            laser_data=laser_data,
            raw_data=raw_data,
        )
        existing_data = self._pulsed_measurement.data
        self._pulsed_measurement.data = PulsedData(
            measurement_data=new_measurement_data,
            fit_result=existing_data.fit_result if existing_data is not None else None,
            fit_result_alt=existing_data.fit_result_alt if existing_data is not None else None,
        )

        self.sigMeasurementDataUpdated.emit()
        return

    ############################################################################
    def _get_master_settings_metadata(self, generator_settings=None):
        """Builds the single 'pulsed measurement settings' dict shared by all three saved-file
        metadata functions below (_get_raw_metadata/_get_laser_metadata/_get_signal_metadata),
        so raw/laser/signal files all describe the exact same configuration without duplicating
        this construction logic three times. Built directly (not via get_pulsed_measurement())
        to avoid that method's measurement_data/loaded_asset .copy() calls - wasted work here
        since only .settings is used."""
        return Settings(
            measurement_settings=self._settings, generator_settings=generator_settings
        ).to_dict()

    def _get_loaded_asset_metadata(self, ensembles=None, blocks=None):
        """Resolved closure of the currently loaded sequence/ensemble - name/type plus the
        sequence/ensembles/blocks/elements structure, embedded directly into every raw/laser/
        signal .dat file's metadata rather than requiring a separate '.pulsedmeasurement'
        snapshot file. Uses PulseObjects.to_metadata_dict() (not to_dict()) - the trimmed,
        display-only variant that drops duplicate/bulky SamplingInformation fields (see its
        docstring); the full snapshot file still uses to_dict() for lossless round-tripping.

        Parameters
        ----------
        ensembles : dict
            Optional, {name: PulseBlockEnsemble} already resolved by the caller (see
            SequenceGeneratorLogic.resolve_asset_closure()) - PulsedMeasurementLogic has no
            Connector to SequenceGeneratorLogic and cannot resolve this itself.
        blocks : dict
            Optional, {name: PulseBlock}, same caveat as `ensembles`.
        """
        if self._loaded_asset is None:
            return {}
        objects = PulseObjects(
            sequence=self._loaded_asset,
            ensembles=dict(ensembles) if ensembles else {},
            blocks=dict(blocks) if blocks else {},
        )
        return {'loaded asset name': self._loaded_asset.name,
                'loaded asset type': type(self._loaded_asset).__name__,
                'loaded asset objects': objects.to_metadata_dict()}

    def _get_raw_metadata(self, generator_settings=None, ensembles=None, blocks=None):
        fc = self._settings.fast_counter_settings
        ms = self._settings.readout_settings
        metadata = {'bin width (s)'               : fc.bin_width,
            'record length (s)'           : fc.record_length,
            'gated counting'              : fc.is_gated,
            'Number of laser pulses'      : ms.number_of_lasers,
            'alternating'                 : ms.alternating,
            'Controlled variable'         : list(self.measurement_data.signal_data[0]),
            'Approx. measurement time (s)': self.measurement_data.elapsed_time,
            'Measurement sweeps'          : self.measurement_data.elapsed_sweeps,
            'pulsed measurement settings' : self._get_master_settings_metadata(generator_settings)}
        metadata.update(self._get_loaded_asset_metadata(ensembles=ensembles, blocks=blocks))
        return self._finalize_metadata(metadata)

    def _get_laser_metadata(self, generator_settings=None, ensembles=None, blocks=None):
        fc = self._settings.fast_counter_settings
        metadata = {'bin width (s)'        : fc.bin_width,
            'record length (s)'    : fc.record_length,
            'gated counting'       : fc.is_gated,
            'extraction parameters': self.extraction_settings,
            'pulsed measurement settings': self._get_master_settings_metadata(generator_settings)}
        metadata.update(self._get_loaded_asset_metadata(ensembles=ensembles, blocks=blocks))
        return self._finalize_metadata(metadata)

    def _get_signal_metadata(self, generator_settings=None, ensembles=None, blocks=None):
        ms = self._settings.readout_settings
        # 'generation parameters' fallback: self.sampling_information.get('generation_parameters')
        # CAN be populated - SequenceGeneratorLogic's sample_pulse_block_ensemble()/
        # sample_pulse_sequence() store an EnsembleAnalysisResult/SequenceAnalysisResult (which has
        # its own generation_parameters field) into sampling_information; since that key isn't a
        # declared SamplingInformation field, it lands in _legacy_data as a raw GenerationParameters
        # instance, not a dict. Normalize it here so this key is always a dict (or None) regardless
        # of which branch supplied it. generator_settings, handed down from PulsedMasterLogic (see
        # save_measurement_data), is preferred when available.
        if generator_settings is not None:
            generation_parameters = generator_settings.generation_parameters.to_dict()
        else:
            generation_parameters = self.sampling_information.get('generation_parameters')
            if hasattr(generation_parameters, 'to_dict'):
                generation_parameters = generation_parameters.to_dict()
        metadata = {'Approx. measurement time (s)': self.measurement_data.elapsed_time,
                'Measurement sweeps'          : self.measurement_data.elapsed_sweeps,
                'Number of laser pulses'      : ms.number_of_lasers,
                'Laser ignore indices'        : list(ms.laser_ignore_list),
                'alternating'                 : ms.alternating,
                'analysis parameters'         : self.analysis_settings,
                'extraction parameters'       : self.extraction_settings,
                'fast counter settings'       : self.fast_counter_settings,
                'generation parameters'       : generation_parameters,
                'generation method parameters': self.generation_method_parameters,
                'pulsed measurement settings' : self._get_master_settings_metadata(generator_settings)}
        metadata.update(self._get_loaded_asset_metadata(ensembles=ensembles, blocks=blocks))
        if self._fit_result:
            metadata['fit result'] = FitContainer.dict_result(self._fit_result)
        if self._fit_result_alt:
            metadata['fit result alt'] = FitContainer.dict_result(self._fit_result_alt)
        return self._finalize_metadata(metadata)

    @staticmethod
    def _finalize_metadata(metadata):
        """Shared tail of _get_raw_metadata()/_get_laser_metadata()/_get_signal_metadata().

        Two steps, in this order:

        1. to_plain_metadata() converts every value into types whose repr() can be read back by
           qudi.util.datastorage's eval()-based parser - see its docstring for the three reprs that
           otherwise arrive as unparsed strings, and for the >1000-element array truncation it
           avoids.
        2. Dicts are wrapped in MetadataDictRepresentation so the header shows them pretty-printed
           across several lines instead of one wall-of-text line.

        Sanitizing first matters: the wrapper's repr() is what ends up in the file, so it has to be
        holding already-plain values. The two are independent otherwise - line breaks never affected
        whether a value parses back (a dict literal spanning lines is a perfectly valid eval
        expression, and ConfigParser rejoins continuation lines before the parse).
        """
        return {
            key: (MetadataDictRepresentation(plain) if isinstance(plain, dict) else plain)
            for key, plain in ((key, to_plain_metadata(value)) for key, value in metadata.items())
        }

    @staticmethod
    def _get_patched_filename_nametag(file_name=None, nametag=None, suffix_str=''):
        """ Helper method to return either a full file name or a nametag to be used as arguments in
        storage objects save_data methods.
        If a file_name is given, return a file_name with patched-in suffix_str and None as nametag.
        If tag is given, append suffix_str to it and return None as file_name.
        """
        if file_name is None:
            if nametag is None:
                nametag = ''
            return None, f'{nametag}{suffix_str}'
        else:
            file_name_stub, file_extension = file_name.rsplit('.', 1)
            return f'{file_name_stub}{suffix_str}.{file_extension}', None

    def _get_signal_column_headers(self):
        """ Helper method to retrieve formatted column header strings for pulsed measurement data.

        Returns
        -------
        list
            List of column header strings.
        """
        labels, units = self._settings.readout_settings.labels, self._settings.readout_settings.units
        column_headers = [f'{labels[0]} ({units[0]})', f'{labels[1]} ({units[1]})']
        column_headers.append(f'{labels[1]} ({units[1]})')
        return column_headers

    def save_measurement_data(self, tag=None, notes=None, file_path=None, storage_cls=None,
                              with_error=True, save_laser_pulses=True, save_pulsed_measurement=True,
                              save_figure=None, generator_settings=None, ensembles=None, blocks=None,
                              save_measurement_snapshot=False):
        """ Prepare data to be saved and create a proper plot of the data

        Parameters
        ----------
        tag : str
            A name tag which will be included in the filename if file_path is None.
        file_path : str
            Optional, custom full file path including file extension to use.
            If given, tag is ignored.
        storage_cls : type
            Optional, override for data storage class to use.
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
        generator_settings : SequenceGeneratorSettings
            Optional, the SequenceGeneratorLogic settings in effect for this measurement.
            PulsedMeasurementLogic has no Connector to SequenceGeneratorLogic and cannot fetch
            this itself - PulsedMasterLogic. save_measurement_data supplies it. Included in the
            saved signal metadata as part of a combined PulsedMeasurement snapshot (see
            get_pulsed_measurement()); left out (None) if this method is called directly without
            going through PulsedMasterLogic.
        ensembles : dict
            Optional, {name: PulseBlockEnsemble} already resolved and copied by the caller (see
            SequenceGeneratorLogic.resolve_asset_closure()) - same caveat as generator_settings.
            Embedded in every raw/laser/signal .dat file's metadata (see
            _get_loaded_asset_metadata()) as well as the optional full snapshot below.
        blocks : dict
            Optional, {name: PulseBlock}, same caveat as `ensembles`.
        save_measurement_snapshot : bool
            Optional, additionally pickle the complete PulsedMeasurement snapshot (settings +
            data + the loaded sequence/ensemble/ensembles/blocks, see get_pulsed_measurement())
            to a single '.pulsedmeasurement' file alongside the raw/laser/signal files - see
            load_measurement_snapshot() to restore it. Off by default since it's a new,
            potentially large binary artifact; the sequence/ensembles/blocks/elements themselves
            are always in the raw/laser/signal .dat metadata regardless of this flag - this only
            adds settings/data/fit results on top, in one loadable object.
        """
        # Use default data storage type if none has been given explicitly
        if storage_cls is None:
            storage_cls = self._default_data_storage_cls

        # Use default data dir if none has been given explicitly.
        # Ignore tag if explicit file_path is given
        if file_path is None:
            data_dir = self.module_default_data_dir
            file_name = None
        else:
            data_dir, file_name = os.path.split(file_path)

        # If save_figure is not explicitly given, use module default
        if save_figure is None:
            save_figure = self._save_thumbnails

        # Create common timestamp for all files to save
        timestamp = datetime.datetime.now()

        # get and initialize data storage object. Daily sub-directory behaviour is already
        # included in self.module_default_data_dir.
        data_storage = storage_cls(root_dir=data_dir)

        ###############
        # Save raw data
        ###############
        # Use either a name tag or a fixed file name
        save_filename, nametag = self._get_patched_filename_nametag(file_name,
                                                                    tag,
                                                                    '_raw_timetrace')
        # Save data to file
        data_storage.save_data(
            self.measurement_data.raw_data.astype('int64')[:, np.newaxis] if self.measurement_data.raw_data.ndim == 1 else self.measurement_data.raw_data.astype('int64'),
            metadata=self._get_raw_metadata(
                generator_settings=generator_settings, ensembles=ensembles, blocks=blocks
            ),
            nametag=nametag,
            filename=save_filename,
            timestamp=timestamp,
            notes=notes,
            column_headers='Signal (counts)'
        )

        ###########################
        # Save extracted laser data
        ###########################
        if save_laser_pulses:
            save_filename, nametag = self._get_patched_filename_nametag(file_name,
                                                                        tag,
                                                                        '_laser_pulses')
            data_storage.save_data(self.measurement_data.laser_data,
                                   metadata=self._get_laser_metadata(
                                       generator_settings=generator_settings, ensembles=ensembles, blocks=blocks
                                   ),
                                   nametag=nametag,
                                   filename=save_filename,
                                   timestamp=timestamp,
                                   notes=notes,
                                   column_headers='Signal (counts)')

        ############################
        # Save evaluated signal data
        ############################
        if save_pulsed_measurement:
            save_filename, nametag = self._get_patched_filename_nametag(file_name,
                                                                        tag,
                                                                        '_pulsed_measurement')

            # Format data to save - error columns are always included; with_error only affects the plot.
            data = np.vstack((self.measurement_data.signal_data, self.measurement_data.measurement_error[1:])).transpose()

            save_path, _, _ = data_storage.save_data(
                data,
                metadata=self._get_signal_metadata(
                    generator_settings=generator_settings, ensembles=ensembles, blocks=blocks
                ),
                nametag=nametag,
                filename=save_filename,
                timestamp=timestamp,
                notes=notes,
                column_headers=self._get_signal_column_headers()
            )

            # save thumbnail figure if required
            if save_figure:
                fig = self._plot_pulsed_thumbnail(with_error=with_error)
                fig_path = save_path.rsplit('.', 1)[0]
                data_storage.save_thumbnail(fig, file_path=fig_path)

        ##########################################
        # Save full PulsedMeasurement snapshot
        ##########################################
        if save_measurement_snapshot:
            save_filename, nametag = self._get_patched_filename_nametag(file_name,
                                                                        tag,
                                                                        '_measurement_snapshot')
            if save_filename is not None:
                snapshot_filename = save_filename.rsplit('.', 1)[0] + '.pulsedmeasurement'
            else:
                snapshot_filename = get_timestamp_filename(timestamp, nametag) + '.pulsedmeasurement'
            snapshot_path = os.path.join(data_storage.root_dir, snapshot_filename)
            create_dir_for_file(snapshot_path)
            with open(snapshot_path, 'wb') as snapshot_file:
                pickle.dump(
                    self.get_pulsed_measurement(
                        generator_settings=generator_settings, ensembles=ensembles, blocks=blocks
                    ),
                    snapshot_file,
                )

    @staticmethod
    def load_measurement_snapshot(file_path):
        """Load a PulsedMeasurement snapshot previously saved via
        save_measurement_data(save_measurement_snapshot=True).

        Parameters
        ----------
        file_path : str
            Full path to the '.pulsedmeasurement' file to load.

        Returns
        -------
        PulsedMeasurement
            The restored snapshot (settings + data + sequence).
        """
        with open(file_path, 'rb') as snapshot_file:
            return pickle.load(snapshot_file)

    def _plot_pulsed_thumbnail(self, with_error=False):

        def _plot_fit(axis, use_alternative_data=False):
            fit_res = self._fit_result
            if use_alternative_data:
                fit_res = self._fit_result_alt

            fit_x, fit_y = fit_res.high_res_best_fit

            label = 'fit'
            if use_alternative_data:
                label = 'secondary fit'

            x_axis_fit_scaled = fit_x / scaled_float.scale_val
            axis.plot(x_axis_fit_scaled, fit_y,
                     color=colors[2], marker='None', linewidth=1.5,
                     label=label)

            # Parameters for the text plot:
            rel_offset = 0.02
            rel_len_fac = 0.011
            entries_per_col = 24

            result_str = fit_res.result_str
            entry_list = result_str.split('\n')
            chunks = [entry_list[x:x + entries_per_col] for x in range(0, len(entry_list), entries_per_col)]

            is_first_column = True  # first entry should contain header or \n

            for column in chunks:
                max_length = max(column, key=len)  # get the longest entry
                column_text = ''
                for entry in column:
                    column_text += entry + '\n'

                column_text = column_text[:-1]  # remove the last new line

                heading = ''
                if is_first_column:
                    heading = 'Fit results:'

                column_text = heading + '\n' + column_text

                axis.text(1.00 + rel_offset, 0.99, column_text,
                         verticalalignment='top',
                         horizontalalignment='left',
                         transform=axis.transAxes,
                         fontsize=12)

                rel_offset += rel_len_fac * len(max_length)
                is_first_column = False

        def _is_fit_available(use_alternative_data=False):
            fit = self._fit_result if not use_alternative_data else self._fit_result_alt
            if not fit:
                return False
            else:
                return fit.success and np.sum(np.abs(fit.high_res_best_fit[1])) > 0

        def _subscript_html_2_tex(text_str):
            if "$" in text_str:
                self.log.warning("Latex character '$' in string; expect wrong text rendering."
                                 " Please use rich text html instead.")
            else:
                text_str = r"$\mathrm{" + text_str + "}$"

            text_str = text_str.replace("<sub>","_{").replace("</sub>","}")
            text_str = text_str.replace(" ", r"\ ")
            return text_str

        # Prepare the figure to save as a "data thumbnail"
        plt.style.use(QudiMatplotlibStyle.style)

        # extract the possible colors from the colorscheme:
        prop_cycle = QudiMatplotlibStyle.style['axes.prop_cycle']
        colors = {i: color_setting['color'] for i, color_setting in enumerate(prop_cycle)}

        # ------------------------------------------------------------------------
        # OPTIMIZATION: Grab local references to avoid making RAM-heavy copies
        # and to cleanly access our new dataclasses.
        # ------------------------------------------------------------------------
        data = self.measurement_data
        settings = self._settings.readout_settings
        alt_type = self.alternative_data_type

        # scale the x_axis for plotting
        max_val = np.max(data.signal_data[0])
        scaled_float = ScaledFloat(max_val)
        counts_prefix = scaled_float.scale
        x_axis_scaled = data.signal_data[0] / scaled_float.scale_val

        # Create the figure object
        if alt_type and alt_type != 'None':
            fig, (ax1, ax2) = plt.subplots(2, 1)
        else:
            fig, ax1 = plt.subplots()

        if with_error:
            ax1.errorbar(x=x_axis_scaled, y=data.signal_data[1],
                         yerr=data.measurement_error[1],
                         linestyle=':', linewidth=0.5, color=colors[0],
                         ecolor=colors[1], capsize=3, capthick=0.9,
                         elinewidth=1.2, label='data trace 1')

            if settings.alternating:
                ax1.errorbar(x=x_axis_scaled, y=data.signal_data[2],
                             yerr=data.measurement_error[2], fmt='-D',
                             linestyle=':', linewidth=0.5, color=colors[3],
                             ecolor=colors[4],  capsize=3, capthick=0.7,
                             elinewidth=1.2, label='data trace 2')
        else:
            ax1.plot(x_axis_scaled, data.signal_data[1], color=colors[0],
                     linestyle=':', linewidth=0.5, label='data trace 1')

            if settings.alternating:
                ax1.plot(x_axis_scaled, data.signal_data[2], '-o',
                         color=colors[3], linestyle=':', linewidth=0.5,
                         label='data trace 2')

        if _is_fit_available():
            _plot_fit(axis=ax1)

        # handle the save of the alternative data plot
        if alt_type and alt_type != 'None':

            # scale the x_axis for plotting
            max_val = np.max(data.signal_alt_data[0])
            scaled_float = ScaledFloat(max_val)
            x_axis_prefix = scaled_float.scale
            x_axis_ft_scaled = data.signal_alt_data[0] / scaled_float.scale_val

            # Axis labels are provided by the selected alternative plot method. The x-axis unit is
            # prefixed with the SI scale of the (scaled) x-axis; the y-axis is not scaled.
            alt_labels = self._altplotanalyzer.current_labels
            x_alt_label = alt_labels.get('x_label', '')
            x_alt_unit = alt_labels.get('x_unit', '')
            y_alt_label = alt_labels.get('y_label', '')
            y_alt_unit = alt_labels.get('y_unit', '')

            if x_alt_unit:
                x_axis_ft_label = '{0} ({1}{2})'.format(x_alt_label, x_axis_prefix, x_alt_unit)
            else:
                x_axis_ft_label = '{0}'.format(x_alt_label)
            if y_alt_unit:
                y_axis_ft_label = '{0} ({1})'.format(y_alt_label, y_alt_unit)
            else:
                y_axis_ft_label = '{0}'.format(y_alt_label)

            ft_label = '{0} of data trace 1'.format(alt_type)

            ax2.plot(x_axis_ft_scaled, data.signal_alt_data[1],
                     linestyle=':', linewidth=0.5, color=colors[0],
                     label=ft_label)
            if settings.alternating and len(data.signal_alt_data) > 2:
                ax2.plot(x_axis_ft_scaled, data.signal_alt_data[2],
                         linestyle=':', linewidth=0.5, color=colors[3],
                         label=ft_label.replace('1', '2'))

            data_ft_label_tex = [_subscript_html_2_tex(x_axis_ft_label),
                                 _subscript_html_2_tex(y_axis_ft_label)]

            ax2.set_xlabel(data_ft_label_tex[0])
            ax2.set_ylabel(data_ft_label_tex[1])
            ax2.legend(bbox_to_anchor=(0., 1.02, 1., .102), loc=3, ncol=2,
                       mode="expand", borderaxespad=0.)

            if _is_fit_available(use_alternative_data=True):
                _plot_fit(axis=ax2, use_alternative_data=True)

        data_label_tex = [_subscript_html_2_tex(settings.labels[0]),
                          _subscript_html_2_tex(settings.labels[1])]

        ax1.set_xlabel(
            '{0} ({1}{2})'.format(data_label_tex[0], counts_prefix, settings.units[0]))
        
        if settings.units[1]:
            ax1.set_ylabel('{0} ({1})'.format(data_label_tex[1], settings.units[1]))
        else:
            ax1.set_ylabel('{0}'.format(data_label_tex[1]))

        fig.tight_layout()
        ax1.legend(bbox_to_anchor=(0., 1.02, 1., .102), loc=3, ncol=2,
                   mode="expand", borderaxespad=0.)

        return fig

    def _compute_alt_data(self):
        """
        Compute the alternative (second) plot data via the selected alternative plot method. Falls
        back to a flat default plot if no method is selected, it is unavailable, or it fails.
        """
        md = self.measurement_data
        alt_data = None
        try:
            alt_data = self._altplotanalyzer.compute_alt_data(md.signal_data)
        except:
            self.log.exception('Error while computing alternative plot data:')
            alt_data = None

        if alt_data is None:
            md.signal_alt_data = np.zeros(md.signal_data.shape, dtype=float)
            if md.signal_data.shape[1] > 0:
                md.signal_alt_data[0] = md.signal_data[0]
        else:
            md.signal_alt_data = alt_data
        return
