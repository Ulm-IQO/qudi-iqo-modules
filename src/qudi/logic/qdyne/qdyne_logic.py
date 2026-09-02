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

from PySide6 import QtCore
import datetime
import logging
import math

import numpy as np

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar

import qudi.logic.qdyne.qdyne_measurement
from qudi.logic.qdyne.qdyne_state_estimator import StateEstimatorMain
from qudi.logic.qdyne.qdyne_time_trace_analyzer import TimeTraceAnalyzerMain
from qudi.logic.qdyne.qdyne_fit import QdyneFit
from qudi.logic.qdyne.qdyne_data.measurement_data import MainDataClass, MeasurementChunk
from qudi.logic.qdyne.qdyne_data_manager import QdyneDataManager
from qudi.logic.qdyne.qdyne_settings import QdyneSettings
from qudi.interface.qdyne_counter_interface import GateMode
from qudi.logic.qdyne.tools.state_enums import DataSource

# This class had three loggers in play at once - a module-level one, self.log, and reach-throughs to
# self._qdyne_logic.log - so where a given message ended up depended on which line emitted it.
# Everything now goes through self.log.


def _positive_float(value, name, log):
    """Coerce `value` to a positive float, or return None with a warning.

    The settings containers are frozen dataclasses that raise ValueError on a non-positive bin
    width, record length or sequence length. Their setters are driven from on_activate() with
    values straight out of the status file, where an uncaught raise stops the whole module
    activating - and, because the offending value is itself persisted, keeps stopping it. Rejecting
    the value here costs one saved setting instead.

    A module-level function rather than a method: it needs nothing from the instance but a logger,
    and the tests drive these methods unbound off plain stubs, which cannot resolve sibling methods.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        log.warning(f'Ignoring non-numeric {name} {value!r}.')
        return None
    if not math.isfinite(number):
        # Checked explicitly because NaN slips through every comparison: `nan <= 0` is False, and
        # so is `nan > 0`. The settings containers validate with exactly that test, so a NaN would
        # sail past them and poison the frequency axis instead of being rejected here.
        log.warning(f'Ignoring non-finite {name} {number}.')
        return None
    if number <= 0:
        log.warning(f'Ignoring non-positive {name} {number}.')
        return None
    return number


class FitTarget:
    """Which plot a fit belongs to.

    Plain string constants rather than an Enum so they cross a Qt Signal(str) unchanged and stay
    readable in logs.
    """

    FREQ = 'freq'
    TIME = 'time'


def _why_not_fittable_curve(curve, what):
    """Why an [x, y] curve cannot be fitted, as a sentence - or None if it can.

    The shape-independent half of _why_not_fittable(), used for the time-domain trace, which has
    no peak selection to narrow it down.
    """
    curve = np.asarray(curve)
    if curve.ndim < 2 or curve.shape[0] < 2 or curve.shape[1] == 0:
        return f'No {what} to fit yet - run a measurement or load data first.'
    y = np.asarray(curve[1])
    if y.size < 3:
        return f'Only {y.size} point(s) in the {what} - too few to fit.'
    if float(np.ptp(y)) == 0.0:
        return (f'The {what} is flat - there is nothing to fit. This usually means the '
                f'measurement produced no signal.')
    return None


def _why_not_fittable(freq_data):
    """Why the current spectrum cannot be fitted, as a sentence - or None if it can.

    Named reasons beat "Something went wrong while trying to perform data fit" for the two cases
    that actually happen. Fitting before a measurement has run is one. The other is fitting a
    spectrum that is identically zero: lmfit's estimator then produces a parameter whose min equals
    its max and raises `ValueError: Parameter 'offset' has min == max` from deep inside the fit - a
    message that says nothing at all about the data being flat.

    Module-level for the same reason as _positive_float(): it needs no instance state, so do_fit()
    stays drivable unbound off a plain stub.
    """
    if freq_data.x is None or freq_data.y is None:
        return 'No spectrum to fit yet - run a measurement or load data first.'
    _peak_x, peak_y = freq_data.data_around_peak
    peak_y = np.asarray(peak_y)
    if peak_y.size < 3:
        return (f'Only {peak_y.size} point(s) around the selected peak - too few to fit. '
                f'Widen the peak range or pick a different peak.')
    if float(np.ptp(peak_y)) == 0.0:
        return ('The spectrum around the selected peak is flat - there is nothing to fit. This '
                'usually means the measurement produced no signal.')
    return None


def _parse_data_index(index, log):
    """Interpret the data index the GUI sends as text.

    It arrives straight from a QLineEdit through Signal(str, str, str), and
    QdyneDataManager.load_data() applies it as `loaded_data[index]`. Indexing a numpy array with a
    string raises IndexError, so typing anything into that box had never worked. An empty box means
    "load the whole array".

    Module-level for the same reason as _positive_float(): it needs nothing from the instance but a
    logger, and keeping it off the class lets load_data() be driven unbound off a plain stub.
    """
    if index is None or (isinstance(index, str) and not index.strip()):
        return None
    try:
        return int(index)
    except (TypeError, ValueError):
        log.error(f'Data index {index!r} is not an integer - loading the whole array.')
        return None


class MeasurementGenerator:
    """
    Class that gives access to the settings for the generation of sequences from the pulsedmasterlogic.
    """

    def __init__(self, pulsedmasterlogic, qdyne_logic: 'QdyneLogic', data_streamer):
        self.log: logging.Logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._pulsedmasterlogic = pulsedmasterlogic
        self._qdyne_logic = qdyne_logic
        self._data_streamer = data_streamer

        self._invoke_settings = False

        #: What the last predefined-sequence generation was actually called with: {'method', 'params'}.
        #: This is the ONLY place those arguments survive - PulsedMasterLogic.generate_method_params
        #: is built once at import from `inspect.signature()` and holds each method's *defaults*, so
        #: reading provenance back out of it records values the measurement may never have used.
        self.last_generation: dict = {}

        # Todo: get something clever for the sequence length
        self.__sequence_length = self._data_streamer().record_length

    # The counter's own properties are the single source of truth for bin width, record length, gate
    # mode and data type. This class used to keep private mirrors of all four, refreshed on every
    # set_counter_settings() call - so between a hardware change and the next call the two disagreed,
    # and every read had to guess which one to believe. Reading through to the device is cheap (these
    # are cached attributes on the hardware module, not bus round trips) and cannot drift.

    @property
    def _binwidth(self):
        return self._data_streamer().binwidth

    @property
    def _record_length(self):
        return self._data_streamer().record_length

    @property
    def _gate_mode(self):
        return self._data_streamer().gate_mode

    @property
    def _data_type(self):
        return self._data_streamer().data_type

    def generate_predefined_sequence(self, method_name, param_dict, sample_and_load):
        self._pulsedmasterlogic().generate_predefined_sequence(
            method_name, param_dict, sample_and_load
        )
        # Remember what was asked for, so the measurement can record real provenance later. A copy,
        # not the caller's dict: they are free to reuse or mutate it after this returns.
        self.last_generation = {
            'method': method_name,
            'params': dict(param_dict or {}),
        }

    def set_generation_parameters(self, settings_dict):
        self._pulsedmasterlogic().set_generation_parameters(settings_dict)

    def _invoked_asset_length(self, what: str):
        """The loaded asset's length, if it can describe a single Qdyne readout - else None.

        Qdyne is a *single-readout* technique: one sequence repetition, one readout, so the record
        length has to map to exactly one laser pulse. A standard Rabi ensemble carries one laser per
        tau point and a Ramsey sequence carries three, so there is no single length to derive from
        them - the mapping is genuinely ambiguous. Declining is correct.

        What was not correct was `raise ValueError('Number of lasers has to be 1, ...')`. Every
        caller of this is reached from a Qt slot, and an exception in a slot aborts that callback
        where it stands:

          * generating a sequence with invoke settings on raised inside set_counter_settings() and
            never reached set_measurement_settings() at all;
          * the raise came before configure(), so the counter was never reconfigured and
            sigCounterSettingsUpdated never fired - the estimator never learned the new bin width
            either;
          * and because _invoke_settings is sticky, every subsequent generate raised again, so
            turning the checkbox off was the only way out.

        Returning None and saying why lets the caller keep its current value and finish normally.
        """
        loaded_asset, asset_type = self._pulsedmasterlogic().loaded_asset
        if asset_type == 'PulseBlockEnsemble':
            ens_length, _bins, ens_lasers = self._pulsedmasterlogic().get_ensemble_info(loaded_asset)
        elif asset_type == 'PulseSequence':
            ens_length, _bins, ens_lasers = self._pulsedmasterlogic().get_sequence_info(loaded_asset)
        else:
            self.log.warning(f'No valid waveform loaded. Cannot invoke {what}.')
            return None

        if ens_lasers != 1:
            # ens_lasers == 0 lands here too - that is an empty or unsampled ensemble, for which
            # get_ensemble_info() returns (0.0, 0, 0).
            self.log.warning(
                f"Cannot invoke {what} from '{loaded_asset}': it has {ens_lasers} laser pulse(s) "
                f"and Qdyne needs exactly one readout per sequence repetition. Keeping the current "
                f"{what} - set it by hand, or load a single-readout asset."
            )
            return None
        return ens_length

    def set_counter_settings(self, settings_dict=None, **kwargs) -> bool:
        """
        Either accepts a settings dictionary as positional argument or keyword arguments.
        If both are present, both are being used by updating the settings_dict with kwargs.
        The keyword arguments take precedence over the items in settings_dict if there are
        conflicting names.

        @param settings_dict:
        @param kwargs:
        @return bool: True if the settings were applied. False means nothing changed - the counter
                      was busy. Both cases used to return None, so no caller could tell them apart.
        """

        # Check if fast counter is running and do nothing if that is the case
        counter_status = self._data_streamer().get_status()
        if counter_status >= 2 or counter_status < 0:
            self.log.warning(
                "Qdyne counter is not idle (status: {0}).\n"
                "Unable to apply new settings.".format(counter_status)
            )
            return False
        # Determine complete settings dictionary. A copy of the caller's: the invoke branch below
        # writes `record_length` back into this dict, and on activation the dict the caller passes
        # IS the StatusVar - so applying the saved settings silently rewrote them.
        settings_dict = dict(settings_dict) if isinstance(settings_dict, dict) else dict(kwargs)
        settings_dict.update(kwargs)

        if 'invoke_settings' in settings_dict:
            self._invoke_settings = bool(settings_dict.get('invoke_settings'))

        if self._invoke_settings:
            ens_length = self._invoked_asset_length('record length')
            if ens_length is not None:
                settings_dict['record_length'] = ens_length

        # Start from what the hardware currently has and apply only what the caller asked to change.
        # There is no local copy to keep in step - configure() returns the values the device actually
        # applied, and the properties above read straight back from it.
        #
        # Only SUPPLIED values are constraint-checked. These two checkers were previously called
        # from the generation widget alone, so every other route - the StatusVar restore at
        # activation, a script, a notebook - reached configure() unvalidated. But a value read back
        # from the device is by definition what the device is already using: snapping it against its
        # own allowed set would silently change the hardware for no reason. That matters because
        # _start_hardware() round-trips counter_settings through here on every measurement start,
        # and a float allowed-set rarely contains the exact value a device reports (a set built with
        # np.arange does not even contain its own round numbers).
        if "bin_width" in settings_dict:
            bin_width = self.check_counter_binwidth_constraint(float(settings_dict["bin_width"]))
        else:
            bin_width = float(self._binwidth)
        if "record_length" in settings_dict:
            record_length = self.check_counter_record_length_constraint(
                float(settings_dict["record_length"])
            )
        else:
            record_length = float(self._record_length)
        gate_mode = (
            GateMode(int(settings_dict["is_gated"]))
            if "is_gated" in settings_dict
            else self._gate_mode
        )
        data_type = settings_dict.get("data_type", self._data_type)

        # configure() returns the values the device ACTUALLY applied - that is its interface
        # contract, and the dummy already forces GateMode.UNGATED. The return used to be discarded
        # and the metadata recorded the *request*, so a hardware that clipped anything produced a
        # saved file claiming settings the measurement never ran with.
        applied = self._data_streamer().configure(bin_width, record_length, gate_mode, data_type)
        try:
            bin_width, record_length, gate_mode, data_type = applied
        except (TypeError, ValueError):
            self.log.warning(
                'Qdyne counter configure() did not return the four applied values - recording the '
                'requested settings instead.'
            )
        else:
            settings_dict["bin_width"] = float(bin_width)
            settings_dict["record_length"] = float(record_length)
            settings_dict["is_gated"] = bool(getattr(gate_mode, 'value', gate_mode))

        # dict(...) rather than the caller's object: this used to store the very dict the caller
        # passed in, so anything they did to it afterwards silently rewrote the saved metadata.
        self._qdyne_logic.data.metadata.counter_settings = dict(settings_dict)
        self._qdyne_logic.sigCounterSettingsUpdated.emit(settings_dict)
        return True

    def set_measurement_settings(self, settings_dict=None, **kwargs) -> bool:
        # Determine complete settings dictionary. A copy - see set_counter_settings().
        settings_dict = dict(settings_dict) if isinstance(settings_dict, dict) else dict(kwargs)
        settings_dict.update(kwargs)

        if 'invoke_settings' in settings_dict:
            self._invoke_settings = bool(settings_dict.get('invoke_settings'))

        if self._invoke_settings:
            ens_length = self._invoked_asset_length('sequence length')
            if ens_length is not None:
                settings_dict['sequence_length'] = ens_length

        # Guarded on "bin_width", not "_bin_width": the leading underscore meant this branch never
        # fired for the key callers actually pass, so the estimator's bin_width was never kept in
        # step with the measurement settings.
        # Values are screened before being pushed into the settings containers - see
        # _positive_float(). They are frozen dataclasses that raise on an impossible value, and this
        # method runs during on_activate() against whatever the status file holds.
        if "bin_width" in settings_dict:
            bin_width = _positive_float(settings_dict["bin_width"], 'bin_width', self.log)
            if bin_width is not None:
                settings_dict["bin_width"] = bin_width  # add to configure estimator settings
                self._qdyne_logic.settings.estimator_stg.set_single_value('bin_width', bin_width)
        if "sequence_length" in settings_dict:
            sequence_length = _positive_float(
                settings_dict["sequence_length"], 'sequence_length', self.log
            )
            if sequence_length is not None:
                settings_dict["sequence_length"] = sequence_length
                self.__sequence_length = sequence_length
                self._qdyne_logic.settings.estimator_stg.set_single_value(
                    'sequence_length', sequence_length)
                self._qdyne_logic.settings.analyzer_stg.set_single_value(
                    'sequence_length', sequence_length)
        self.log.debug(f"{settings_dict=}")
        self._qdyne_logic.data.metadata.measurement_settings = dict(settings_dict)
        self._qdyne_logic.sigMeasurementSettingsUpdated.emit(settings_dict)
        return True

    def check_counter_record_length_constraint(self, record_length: float):
        record_length_constraint = self._data_streamer().constraints.record_length
        if not record_length_constraint.is_valid(record_length):
            try:
                record_length_constraint.check_value_type(record_length)
                record_length_constraint.check_value_range(record_length)
            except TypeError:
                record_length = self._record_length
                self.log.error(
                    f"Record length is not of correct type. Keep record length {self._record_length}s."
                )
            except ValueError:
                record_length = record_length_constraint.clip(record_length)
                self.log.error(
                    f"Record length out of bounds. Clipping to bound {record_length}s."
                )
        return record_length

    def check_counter_binwidth_constraint(self, binwidth: float):
        binwidth_constraint = self._data_streamer().constraints.binwidth
        if not binwidth_constraint.is_valid(binwidth):
            try:
                binwidth_constraint.check_value_type(binwidth)
                binwidth_constraint.check_value_range(binwidth)
            except TypeError:
                binwidth = self._binwidth
                self.log.error(
                    f"Binwidth is not of correct type. Keep binwidth {self._binwidth}s."
                )
            except ValueError:
                binwidth = binwidth_constraint.clip(binwidth)
                self.log.error(
                    f"Binwidth out of bounds. Clipping to bound {binwidth}s."
                )
            try:
                binwidth_constraint.check_allowed_values(binwidth)
            except ValueError:
                binwidth = binwidth_constraint.clip(binwidth)
                self.log.warning(
                    f"Binwidth does not match allowed binwidth condition of hardware. "
                    f"Set closest allowed binwidth {binwidth}s."
                )
        return binwidth

    @property
    def status_dict(self):
        return self._pulsedmasterlogic().status_dict

    @property
    def generation_parameters(self):
        return self._pulsedmasterlogic().generation_parameters

    @property
    def measurement_settings(self):
        settings_dict = self._pulsedmasterlogic().measurement_settings
        # overwrite invoke_settings option from pulsed
        settings_dict['invoke_settings'] = self._invoke_settings
        settings_dict['sequence_length'] = self.__sequence_length
        return settings_dict

    @property
    def counter_settings(self):
        settings_dict = dict()
        settings_dict["bin_width"] = float(self._data_streamer().binwidth)
        settings_dict["record_length"] = float(
            self._data_streamer().record_length
        )
        settings_dict["is_gated"] = bool(
            self._data_streamer().gate_mode.value
        )
        return settings_dict

    @property
    def loaded_asset(self):
        return self._pulsedmasterlogic().loaded_asset

    @property
    def digital_channels(self):
        return self._pulsedmasterlogic().digital_channels

    @property
    def analog_channels(self):
        return self._pulsedmasterlogic().analog_channels

    @property
    def generate_method_params(self):
        return self._pulsedmasterlogic().generate_method_params

    @property
    def generate_methods(self):
        return self._pulsedmasterlogic().generate_methods


class QdyneLogic(LogicBase):
    """
    This is the Logic class for Qdyne measurements.

    example config for copy-paste:

    qdyne_logic:
        module.Class: 'qdyne.qdyne_logic.QdyneLogic'
        connect:
            data_streamer: <qdyne_counter_name>
            pulsedmasterlogic: pulsed_master_logic
        options:
            default_estimator_method: 'TimeTag'
            default_analyzer_method: 'Fourier'
            data_storage_class: 'text'
    """

    # declare connectors
    pulsedmasterlogic = Connector(interface="PulsedMasterLogic")
    _data_streamer = Connector(name="data_streamer", interface="QdyneCounterInterface")

    # declare config options
    # `estimator_method` and `analyzer_method` used to be declared alongside these, duplicating
    # them. All four were read by nothing whatsoever - the method in use comes from the
    # _current_*_method StatusVars - so setting any of them in a config had no effect, and the two
    # carrying missing="warn" made qudi warn at startup about options that did nothing. The
    # duplicates are gone, and these two now genuinely act as the fallback when there is no saved
    # selection - see initialize_settings() in on_activate().
    default_estimator_method = ConfigOption(
        name="default_estimator_method", default="TimeTag", missing="nothing"
    )
    default_analyzer_method = ConfigOption(
        name="default_analyzer_method", default="Fourier", missing="nothing"
    )
    data_storage_class = ConfigOption(
        name="data_storage_class", default="text", missing="nothing"
    )

    _measurement_generator_dict = StatusVar(default=dict())
    _counter_settings_dict = StatusVar(default=dict())
    _measurement_settings_dict = StatusVar(default=dict())
    _estimator_stg_dict = StatusVar(default=dict())
    _analyzer_stg_dict = StatusVar(default=dict())
    _current_estimator_method = StatusVar(default="TimeTag")
    _current_estimator_mode = StatusVar(default="default")
    _current_analyzer_method = StatusVar(default="Fourier")
    _current_analyzer_mode = StatusVar(default="default")
    analysis_timer_interval = StatusVar(default=1.0)

    _fit_configs = StatusVar(name="fit_configs", default=None)

    # signals for connecting modules
    #: (fit_config, fit_result, FitTarget) - the third argument says which plot the result is for,
    #: so the GUI can paint it into the right item.
    sigFitUpdated = QtCore.Signal(str, object, str)
    sigToggleQdyneMeasurement = QtCore.Signal(bool)
    sigCounterSettingsUpdated = QtCore.Signal(dict)
    sigMeasurementSettingsUpdated = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.measure: "qudi.logic.qdyne.qdyne_measurement.QdyneMeasurement" = None
        self.estimator: StateEstimatorMain = None
        self.analyzer: TimeTraceAnalyzerMain = None
        self.settings: QdyneSettings = None
        self.data: MainDataClass = None
        self.new_data: MeasurementChunk = None
        self.fit: QdyneFit = None
        self.measurement_generator: MeasurementGenerator = None
        self.data_manager: QdyneDataManager = None
        self._data_source = DataSource.MEASUREMENT

    def on_activate(self):
        def activate_classes():
            self.data = MainDataClass()
            self.new_data = MeasurementChunk()
            self.estimator = StateEstimatorMain(self.log)
            self.analyzer = TimeTraceAnalyzerMain()
            # QdyneSettings passes this straight to DataManagerSettings, whose set_options() does
            # kwargs.setdefault('data_dir', default_data_dir) for every data type - so the
            # set_data_dir_all() call that used to follow was a no-op that read as load-bearing.
            self.settings = QdyneSettings(self.module_default_data_dir)
            self.measurement_generator = MeasurementGenerator(
                self.pulsedmasterlogic, self, self._data_streamer
            )
            self.fit = QdyneFit(self, self._fit_configs)
            self.measure = qudi.logic.qdyne.qdyne_measurement.QdyneMeasurement(self)
            self.data_manager = QdyneDataManager(
                self.data, self.settings.data_manager_stg, self.data_storage_class
            )

        #            self.fitting = QdyneFittingMain()


        def initialize_settings(mediator, saved, method, mode, apply_method, what, config_default):
            """Restore one mediator from its StatusVar, then select the saved method and mode.

            The mediator constructor already builds defaults for every registered method, and
            load_from_dict() tolerates a method that is missing from the saved dict - so there is no
            longer a default-or-load fork here, just a load when there is something to load.
            """
            if not mediator.method_list:
                # Nothing registered: apply_method() would reach implementation('') and raise out
                # of on_activate. A registry is only ever empty by mistake, but the module should
                # say so rather than fail on an unrelated-looking lookup error.
                self.log.error(f'No {what} methods are registered. Skipping {what} settings.')
                return

            if saved:
                mediator.load_from_dict(saved)
            else:
                self.log.info(f"No saved {what} settings. Using defaults.")

            # Saved selection first, then the configured default, then whatever is registered. The
            # config default used to be declared and never consulted, so `default_estimator_method`
            # in a config file did nothing at all.
            for candidate in (method, config_default):
                if candidate in mediator.method_list:
                    mediator.set_method(candidate)
                    break
            else:
                self.log.warning(
                    f"Neither the saved {what} method '{method}' nor the configured default "
                    f"'{config_default}' is available. Using '{mediator.method_list[0]}'."
                )
                mediator.set_method(mediator.method_list[0])

            if mode in mediator.mode_list:
                mediator.set_mode(mode)

            apply_method()

        def initialize_estimator_settings():
            initialize_settings(
                self.settings.estimator_stg,
                self._estimator_stg_dict,
                self._current_estimator_method,
                self._current_estimator_mode,
                self.input_estimator_method,
                'estimator',
                self.default_estimator_method,
            )

        def initialize_analyzer_settings():
            initialize_settings(
                self.settings.analyzer_stg,
                self._analyzer_stg_dict,
                self._current_analyzer_method,
                self._current_analyzer_mode,
                self.input_analyzer_method,
                'analyzer',
                self.default_analyzer_method,
            )

        # The module instance is reused across deactivate/activate cycles - qudi never clears it -
        # so a DataSource.LOADED left behind by load_data() used to survive a module restart.
        self._data_source = DataSource.MEASUREMENT

        activate_classes()
        initialize_estimator_settings()
        initialize_analyzer_settings()

        # Connected BEFORE the settings restore below, not after. set_counter_settings() emits this
        # signal, and it is what carries the counter's real bin_width and record_length into the
        # estimator settings - so with the connection made afterwards, the one emission that happens
        # during activation reached nobody. The estimator kept its placeholder defaults (1e-9) while
        # the counter ran at 100e-9, which made the count window come out empty in bins and every
        # readout count zero photons.
        self.sigToggleQdyneMeasurement.connect(
            self.measure.toggle_qdyne_measurement, QtCore.Qt.QueuedConnection
        )
        self.sigCounterSettingsUpdated.connect(self._sync_estimator_with_counter)

        # A stale or corrupt status file must cost you your saved settings, never the module. These
        # three feed persisted values straight into frozen settings containers that raise on an
        # impossible value, and an uncaught raise here stopped QdyneLogic activating at all - with
        # no way back, since the offending value is itself persisted.
        for what, restore in (
            ('generation parameters',
             lambda: self.measurement_generator.set_generation_parameters(
                 self._measurement_generator_dict)),
            ('counter settings',
             lambda: self.measurement_generator.set_counter_settings(
                 self._counter_settings_dict)),
            ('measurement settings',
             lambda: self.measurement_generator.set_measurement_settings(
                 self._measurement_settings_dict)),
        ):
            try:
                restore()
            except Exception:
                self.log.exception(
                    f'Failed to restore saved {what} - continuing with defaults for those.'
                )
        return

    @QtCore.Slot(dict)
    def _sync_estimator_with_counter(self, counter_settings: dict) -> None:
        """Forward only the counter settings the estimator actually has fields for.

        This signal used to be wired straight to estimator_stg.set_values(). The counter dict
        carries `is_gated`, which is a live hardware property and not a field of any estimator
        settings class, so every counter-settings change logged an "Ignoring key(s) ['is_gated']"
        warning - on activation, on every measurement start, and on every settings edit.
        """
        data = self.settings.estimator_stg.current_data
        if data is None:
            return
        fields = type(data).field_names()
        relevant = {k: v for k, v in counter_settings.items() if k in fields}
        if relevant:
            self.settings.estimator_stg.set_values(relevant)

    def on_deactivate(self):
        # Saved FIRST. qudi dumps the StatusVar attributes after on_deactivate() returns (in a
        # `finally`, so nothing here can lose the status file) - but _save_status_variables() is
        # what copies THIS session's live state into those attributes. With it last, anything that
        # raised before it left them holding the values loaded at activation, and the session's
        # settings silently reverted to the previous session's.
        try:
            self._save_status_variables()
        except Exception:
            self.log.exception('Failed to save status variables.')

        for what, call in (
            # Explicit teardown rather than relying on __del__ to disconnect the measurement's own
            # timer signals when the garbage collector eventually gets to it.
            ('measurement teardown',
             lambda: self.measure.teardown() if self.measure is not None else None),
            # Disconnect only what this class connected. A bare disconnect() drops every receiver,
            # including any a GUI attached - those are the GUI's to release.
            ('toggle signal',
             lambda: self.sigToggleQdyneMeasurement.disconnect(
                 self.measure.toggle_qdyne_measurement) if self.measure is not None else None),
            ('counter settings signal',
             lambda: self.sigCounterSettingsUpdated.disconnect(
                 self._sync_estimator_with_counter)),
        ):
            try:
                call()
            except Exception:
                self.log.exception(f'Failed during {what} on deactivation.')
        return

    def _save_status_variables(self):
        """Copy this session's live state into the StatusVar attributes.

        Each source is guarded on its own: one unreachable connector must not cost the other six.
        """

        def store(what, attr, produce):
            """Set one StatusVar from `produce()`, or leave it alone and say why."""
            try:
                setattr(self, attr, produce())
            except Exception:
                self.log.exception(f'Could not save {what} - keeping the previously stored value.')

        def generation_parameters():
            # dict(...): the pop() below must not mutate a dict owned by the sequence generator. It
            # hands back a copy today, but that is a property two modules away.
            params = dict(self.measurement_generator.generation_parameters)
            # pop(..., None): is_gated is a live hardware property, not a generation parameter, so
            # it is kept out of the StatusVar - but it is not guaranteed to be present, and a bare
            # pop() made deactivation raise KeyError.
            params.pop('is_gated', None)
            return params

        store('generation parameters', '_measurement_generator_dict', generation_parameters)
        store('counter settings', '_counter_settings_dict',
              lambda: self.measurement_generator.counter_settings)
        store('measurement settings', '_measurement_settings_dict',
              lambda: self.measurement_generator.measurement_settings)
        store('estimator settings', '_estimator_stg_dict',
              lambda: self.settings.estimator_stg.dump_as_dict())
        store('analyzer settings', '_analyzer_stg_dict',
              lambda: self.settings.analyzer_stg.dump_as_dict())

        # These four are read at activation to restore the current method and mode, and used to be
        # assigned nowhere at all - so qudi kept re-persisting whatever had been loaded and the
        # module always reopened on TimeTag/default, however you had left it. The per-method
        # settings above WERE saved, which is what made it look as though the settings had been
        # lost when in fact only the selection had.
        store('estimator method', '_current_estimator_method',
              lambda: self.settings.estimator_stg.current_method)
        store('estimator mode', '_current_estimator_mode',
              lambda: self.settings.estimator_stg.current_mode)
        store('analyzer method', '_current_analyzer_method',
              lambda: self.settings.analyzer_stg.current_method)
        store('analyzer mode', '_current_analyzer_mode',
              lambda: self.settings.analyzer_stg.current_mode)

        # Loaded at activation into QdyneFit and never written back, so every fit configuration a
        # user added was lost on deactivate. Both sibling modules declaring the same StatusVar -
        # odmr_logic and pulsed_measurement_logic - persist theirs; this one did not.
        if self.fit is not None:
            store('fit configurations', '_fit_configs',
                  lambda: self.fit.fit_config_model.dump_configs())

    def input_estimator_method(self):
        self.estimator.method = self.settings.estimator_stg.current_method

    def input_analyzer_method(self):
        self.analyzer.method = self.settings.analyzer_stg.current_method

    # A second @Slot(bool, str) used to be stacked here, and a @Slot(str, bool) on do_fit, each
    # advertising a signature the method cannot accept. PySide6 tolerates the mismatch by dropping
    # the surplus argument, so it never broke - it just documented a contract that was not real.
    @QtCore.Slot(bool)
    def toggle_qdyne_measurement(self, start):
        """
        @param bool start: True for start measurement, False for stop measurement
        """
        # bool(start) rather than `if isinstance(start, bool)`: the old guard silently did nothing
        # for a numpy.bool_ or a plain int, both of which a script would reasonably pass - and it
        # had already moved _data_source by then, so the state changed even when the toggle did not.
        start = bool(start)
        if start:
            self._data_source = DataSource.MEASUREMENT
        self.sigToggleQdyneMeasurement.emit(start)
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, str)
    def do_fit(self, fit_config, plot=FitTarget.FREQ):
        """Fit either the spectrum or the time-domain trace.

        `plot` says which plot asked. Without it both fit widgets fitted the spectrum and both
        results were drawn on the frequency plot, so a fit requested from the Time domain tab
        silently redrew the other one.
        """
        if plot == FitTarget.TIME:
            return self._do_fit_time_domain(fit_config)

        # Refuse the fits that cannot work, with a reason - see _why_not_fittable(). Both cases it
        # covers used to surface as "Something went wrong while trying to perform data fit" plus a
        # traceback from inside lmfit.
        reason = _why_not_fittable(self.data.freq_data)
        if reason:
            self.log.error(reason)
            self.data.fit_config, self.data.fit_result = "", None
            self.sigFitUpdated.emit(self.data.fit_config, self.data.fit_result, FitTarget.FREQ)
            return None
        try:
            self.data.fit_config, self.data.fit_result = self.fit.perform_fit(
                self.data.freq_data.data_around_peak, fit_config
            )
        except Exception:
            # `except Exception`, not a bare `except`: the latter also swallowed KeyboardInterrupt
            # and SystemExit, so Ctrl-C during a fit was reported as a fit failure.
            self.data.fit_config, self.data.fit_result = "", None
            self.log.exception("Something went wrong while trying to perform data fit.")
        self.sigFitUpdated.emit(self.data.fit_config, self.data.fit_result, FitTarget.FREQ)
        return self.data.fit_result

    def _do_fit_time_domain(self, fit_config):
        time_domain = np.asarray(self.data.time_domain)
        reason = _why_not_fittable_curve(time_domain, "time trace")
        if reason:
            self.log.error(reason)
            self.data.time_fit_config, self.data.time_fit_result = "", None
            self.sigFitUpdated.emit(
                self.data.time_fit_config, self.data.time_fit_result, FitTarget.TIME
            )
            return None
        try:
            self.data.time_fit_config, self.data.time_fit_result = self.fit.perform_fit(
                time_domain, fit_config, container=self.fit.fit_container2
            )
        except Exception:
            self.data.time_fit_config, self.data.time_fit_result = "", None
            self.log.exception("Something went wrong while trying to perform data fit.")
        self.sigFitUpdated.emit(
            self.data.time_fit_config, self.data.time_fit_result, FitTarget.TIME
        )
        return self.data.time_fit_result

    @QtCore.Slot(str)
    def save_data(self, data_type: str):
        self.log.debug(f"Saving data, {data_type=}")
        timestamp = datetime.datetime.now()
        # == 'all', not `'all' in data_type`: the substring test would fire for any future data type
        # whose name merely contains "all". The GUI's combo box is built from ['all'] + data_types,
        # so the value is exactly 'all'. The loop variable no longer rebinds the parameter either.
        if data_type == 'all':
            for one_type in self.data_manager.data_types:
                self.data_manager.save_data(one_type, timestamp)
        else:
            self.data_manager.save_data(data_type, timestamp)

    @QtCore.Slot(str, str, str)
    def load_data(self, data_type, file_path, index):
        self._data_source = DataSource.LOADED
        if data_type == 'all':
            self.log.error("Select one data type")
            return
        self.data_manager.load_data(data_type, file_path, _parse_data_index(index, self.log))
        metadata = self.data.metadata
        self._restore_loaded_mode(
            self.settings.estimator_stg,
            metadata.state_estimation_method,
            metadata.state_estimation_settings,
            'state estimation',
        )
        self._restore_loaded_mode(
            self.settings.analyzer_stg,
            metadata.analysis_method,
            metadata.analysis_settings,
            'analysis',
        )
        # Re-derive only the stages that sit downstream of whatever was actually loaded. This used
        # to call pull_data_and_estimate() unconditionally: its LOADED branch re-extracts from
        # self.data.raw_data - which is empty when you loaded a time trace - and then assigned that
        # empty result straight over the time trace just read from the file. Loading anything other
        # than raw_data destroyed it.
        try:
            self.measure.rerun_pipeline_from(data_type)
        except Exception:
            self.log.exception(
                f'Loaded {data_type} from {file_path}, but re-deriving the downstream data failed. '
                f'The loaded data itself is intact.'
            )
        self.log.info(f"Loaded {data_type} data from {file_path}")

    def _restore_loaded_mode(self, mediator, method, settings_dict, what):
        """Put the settings that produced a saved measurement into that mediator's 'loaded' mode.

        Built through the settings class's tolerant from_dict() rather than cls(**settings_dict):
        a file written before a field was added or removed used to raise TypeError here. The
        estimator half was wrapped in a try/except that logged and continued, and the analyzer half
        was not wrapped at all - so the same bad file either silently skipped the estimator settings
        or took the whole load down, depending on which container drifted.
        """
        if not method:
            self.log.warning(f'Saved data carries no {what} method. Keeping current settings.')
            return
        if method not in mediator.method_list:
            self.log.warning(
                f"Saved {what} method '{method}' is not available. Keeping current settings."
            )
            return
        mediator.update_method(method)
        settings_cls = mediator.settings_classes[method]
        mediator.add_mode('loaded', True, settings_cls.from_dict(settings_dict))

    @property
    def data_source(self):
        return self._data_source

    @data_source.setter
    def data_source(self, source: DataSource):
        """Writable so the measurement can claim the source where it actually starts.

        It used to be set to MEASUREMENT only inside toggle_qdyne_measurement(), which is the signal
        path - so anything calling QdyneMeasurement.start_qdyne_measurement() directly (a script, a
        notebook, a test) inherited whatever load_data() last left here. A measurement started after
        a load then ran the LOADED branch forever: it looked alive and never polled the hardware.
        """
        self._data_source = DataSource(source)
