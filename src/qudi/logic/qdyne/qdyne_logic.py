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

import os
from os import stat
from typing import Optional
import numpy as np
import time
from collections import OrderedDict
from PySide2 import QtCore
import datetime
import logging

from qudi.util.paths import get_userdata_dir
from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.constraints import DiscreteScalarConstraint
from qudi.util.mutex import RecursiveMutex

from qudi.logic.qdyne.qdyne_measurement import (
    QdyneMeasurement,
    QdyneMeasurementSettings,
)
from qudi.logic.qdyne.qdyne_state_estimator import StateEstimatorMain
from qudi.logic.qdyne.qdyne_time_trace_analyzer import TimeTraceAnalyzerMain
from qudi.logic.qdyne.qdyne_fit import QdyneFit
from qudi.logic.qdyne.qdyne_dataclass import MainDataClass
from qudi.logic.qdyne.qdyne_data_manager import QdyneDataManager
from qudi.logic.qdyne.qdyne_settings import QdyneSettings
from qudi.interface.qdyne_counter_interface import GateMode, QdyneCounterConstraints
from qudi.logic.qdyne.tools.state_enums import DataSource

_logger = logging.getLogger(__name__)


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

        self.__active_channels = self._data_streamer().active_channels
        self.__binwidth = self._data_streamer().binwidth
        self.__record_length = self._data_streamer().record_length
        self.__gate_mode = self._data_streamer().gate_mode
        self.__data_type = self._data_streamer().data_type
        # Todo: get something clever for the sequence length
        self.__sequence_length = self._data_streamer().record_length

    def generate_predefined_sequence(self, method_name, param_dict, sample_and_load):
        self._pulsedmasterlogic().generate_predefined_sequence(
            method_name, param_dict, sample_and_load
        )

    def set_generation_parameters(self, settings_dict):
        self._pulsedmasterlogic().set_generation_parameters(settings_dict)

    def set_counter_settings(self, settings_dict=None, **kwargs):
        """
        Either accepts a settings dictionary as positional argument or keyword arguments.
        If both are present, both are being used by updating the settings_dict with kwargs.
        The keyword arguments take precedence over the items in settings_dict if there are
        conflicting names.

        @param settings_dict:
        @param kwargs:
        @return:
        """

        # Check if fast counter is running and do nothing if that is the case
        counter_status = self._data_streamer().get_status()
        if counter_status >= 2 or counter_status < 0:
            _logger.warning(
                "Qdyne counter is not idle (status: {0}).\n"
                "Unable to apply new settings.".format(counter_status)
            )
            return
        # Determine complete settings dictionary
        if not isinstance(settings_dict, dict):
            settings_dict = kwargs
        else:
            settings_dict.update(kwargs)

        if 'invoke_settings' in settings_dict:
            self._invoke_settings = bool(settings_dict.get('invoke_settings'))

        if self._invoke_settings:
            loaded_asset, asset_type = self._pulsedmasterlogic().loaded_asset
            if asset_type == 'PulseBlockEnsemble':
                ens_length, ens_bins, ens_lasers = \
                    self._pulsedmasterlogic().get_ensemble_info(loaded_asset)
            elif asset_type == 'PulseSequence':
                ens_length, ens_bins, ens_lasers = \
                    self._pulsedmasterlogic().get_sequence_info(loaded_asset)
            else:
                ens_length, ens_lasers = (self.__record_length, 1)
                self._qdyne_logic.log.warning('No valid waveform loaded. Cannot invoke record length.')
            if ens_lasers != 1:
                raise ValueError(f'Number of lasers has to be 1, but is {ens_lasers}.')
            settings_dict['record_length'] = ens_length

        # Set parameters if present
        if "bin_width" in settings_dict:
            self.__binwidth = float(settings_dict["bin_width"])
        if "record_length" in settings_dict:
            self.__record_length = float(settings_dict["record_length"])
            self._qdyne_logic.log.debug(['set count sett: rec len', self.__record_length])
        if "active_channels" in settings_dict:
            self.__active_channels = settings_dict["active_channels"]
        if "gate_mode" in settings_dict:
            self.__gate_mode = GateMode(int(settings_dict["gate_mode"]))
        if "data_type" in settings_dict:
            self.__data_type = settings_dict["data_type"]

        # Set settings in pulsed to update to qdyne settings
        settings = {
            "bin_width": self.__binwidth,
            "record_length": self.__record_length,
            "number_of_gates": 0,
        }

        (self.__active_channels,
        self.__binwidth,
        self.__record_length,
        self.__gate_mode,
        self.__data_type) = self._data_streamer().configure(
            self.__active_channels,
            self.__binwidth,
            self.__record_length,
            self.__gate_mode,
            self.__data_type,
        )
        self._qdyne_logic.data.metadata.counter_settings = settings_dict
        self._qdyne_logic.sigCounterSettingsUpdated.emit(settings_dict)
        return

    def set_measurement_settings(self, settings_dict=None, **kwargs):
        # Determine complete settings dictionary
        if not isinstance(settings_dict, dict):
            settings_dict = kwargs
        else:
            settings_dict.update(kwargs)

        if 'invoke_settings' in settings_dict:
            self._invoke_settings = bool(settings_dict.get('invoke_settings'))

        if self._invoke_settings:
            loaded_asset, asset_type = self._pulsedmasterlogic().loaded_asset
            if asset_type == 'PulseBlockEnsemble':
                ens_length, ens_bins, ens_lasers = \
                    self._pulsedmasterlogic().get_ensemble_info(loaded_asset)
            elif asset_type == 'PulseSequence':
                ens_length, ens_bins, ens_lasers = \
                    self._pulsedmasterlogic().get_sequence_info(loaded_asset)
            else:
                ens_length, ens_lasers = (self.__sequence_length, 1)
                self._qdyne_logic.log.warning('No valid waveform loaded. Cannot invoke sequence length.')
            if ens_lasers != 1:
                raise ValueError(f'Number of lasers has to be 1, but is {ens_lasers}.')
            settings_dict['sequence_length'] = ens_length

        if "_bin_width" in settings_dict:
            settings_dict["bin_width"] = float(settings_dict["bin_width"])  # add to configure estimator settings
            self._qdyne_logic.settings.estimator_stg.set_single_value('bin_width', settings_dict["bin_width"])
        if "sequence_length" in settings_dict:
            self.__sequence_length = float(settings_dict["sequence_length"])
            self._qdyne_logic.settings.estimator_stg.set_single_value(
                'sequence_length', settings_dict["sequence_length"])
            self._qdyne_logic.settings.analyzer_stg.set_single_value(
                'sequence_length', settings_dict["sequence_length"])
        self.log.debug(f"{settings_dict=}")
        self._qdyne_logic.data.metadata.measurement_settings = settings_dict
        self._qdyne_logic.sigMeasurementSettingsUpdated.emit(settings_dict)

    def check_counter_record_length_constraint(self, record_length: float):
        record_length_constraint = self._data_streamer().constraints.record_length
        if not record_length_constraint.is_valid(record_length):
            try:
                record_length_constraint.check_value_type(record_length)
                record_length_constraint.check_value_range(record_length)
            except TypeError:
                record_length = self.__record_length
                self._qdyne_logic.log.error(
                    f"Record length is not of correct type. Keep record length {self.__record_length}s."
                )
            except ValueError:
                record_length = record_length_constraint.clip(record_length)
                self._qdyne_logic.log.error(
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
                binwidth = self.__binwidth
                self._qdyne_logic.log.error(
                    f"Binwidth is not of correct type. Keep binwidth {self.__binwidth}s."
                )
            except ValueError:
                binwidth = binwidth_constraint.clip(binwidth)
                self._qdyne_logic.log.error(
                    f"Binwidth out of bounds. Clipping to bound {binwidth}s."
                )
            try:
                binwidth_constraint.check_allowed_values(binwidth)
            except ValueError:
                binwidth = binwidth_constraint.clip(binwidth)
                self._qdyne_logic.log.warning(
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
    """

    # declare connectors
    pulsedmasterlogic = Connector(interface="PulsedMasterLogic")
    _data_streamer = Connector(name="data_streamer", interface="QdyneCounterInterface")

    # declare config options
    estimator_method = ConfigOption(
        name="estimator_method", default="TimeTag", missing="warn"
    )
    analyzer_method = ConfigOption(
        name="analyzer_method", default="Fourier", missing="nothing"
    )
    default_estimator_method = ConfigOption(
        name="default_estimator_method", default="TimeTag", missing="warn"
    )
    default_analyzer_method = ConfigOption(
        name="analyzer_method", default="Fourier", missing="nothing"
    )
    # data_save_dir = ConfigOption(name='data_save_dir')
    data_storage_class = ConfigOption(
        name="data_storage_class", default="text", missing="nothing"
    )

    #    estimator_method = StatusVar(default='TimeTag')
    #    analyzer_method = StatusVar(default='Fourier')
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
    _estimator_method = "TimeTag"
    _analysis_method = "Fourier"

    # signals for connecting modules
    sigFitUpdated = QtCore.Signal(str, object)
    sigToggleQdyneMeasurement = QtCore.Signal(bool)
    sigCounterSettingsUpdated = QtCore.Signal(dict)
    sigMeasurementSettingsUpdated = QtCore.Signal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.measure = None
        self.estimator: StateEstimatorMain = None
        self.analyzer: TimeTraceAnalyzerMain = None
        self.settings: QdyneSettings = None
        self.data: MainDataClass = None
        self.new_data: MainDataClass = None
        self.fit: QdyneFit = None
        self.save = None
        self.measurement_generator: MeasurementGenerator = None
        self.data_manager: QdyneDataManager = None
        self._data_source = DataSource.MEASUREMENT

    def on_activate(self):
        def activate_classes():
            self.data = MainDataClass()
            self.new_data = MainDataClass()
            self.estimator = StateEstimatorMain(self.log)
            self.analyzer = TimeTraceAnalyzerMain()
            self.settings = QdyneSettings(self.module_default_data_dir)
            self.settings.data_manager_stg.set_data_dir_all(
                self.module_default_data_dir
            )
            self.measurement_generator = MeasurementGenerator(
                self.pulsedmasterlogic, self, self._data_streamer
            )
            self.fit = QdyneFit(self, self._fit_configs)
            self.measure = QdyneMeasurement(self)
            self.data_manager = QdyneDataManager(
                self.data, self.settings.data_manager_stg
            )

        #            self.fitting = QdyneFittingMain()


        def initialize_estimator_settings():
            if self._estimator_stg_dict:
                self.settings.estimator_stg.load_from_dict(
                    self.settings.estimator_cls_dict, self._estimator_stg_dict)

            else:
                self.settings.estimator_stg.create_default(self.settings.estimator_cls_dict)
                self.log.info("Default estimator settings created.")

            self.settings.estimator_stg.set_method(self._current_estimator_method)
            self.settings.estimator_stg.set_mode(self._current_estimator_mode)

            self.input_estimator_method()

        def initialize_analyzer_settings():
            if self._analyzer_stg_dict:
                self.settings.analyzer_stg.load_from_dict(
                    self.settings.analyzer_cls_dict, self._analyzer_stg_dict)

            else:
                self.settings.analyzer_stg.create_default(self.settings.analyzer_cls_dict)
                self.log.info("Default settings created")

            self.settings.analyzer_stg.set_method(self._current_analyzer_method)
            self.settings.analyzer_stg.set_mode(self._current_analyzer_mode)

            self.input_analyzer_method()

        activate_classes()
        initialize_estimator_settings()
        initialize_analyzer_settings()
        self.measurement_generator.set_generation_parameters(
            self._measurement_generator_dict
        )
        self.measurement_generator.set_counter_settings(
            self._counter_settings_dict
        )
        self.measurement_generator.set_measurement_settings(
            self._measurement_settings_dict
        )

        self.sigToggleQdyneMeasurement.connect(
            self.measure.toggle_qdyne_measurement, QtCore.Qt.QueuedConnection
        )
        self.sigCounterSettingsUpdated.connect(self.settings.estimator_stg.set_values)
        return

    def on_deactivate(self):
        self.sigToggleQdyneMeasurement.disconnect()
        self.sigCounterSettingsUpdated.disconnect(self.settings.estimator_stg.set_values)
        self._save_status_variables()
        return

    def _save_status_variables(self):
        self._measurement_generator_dict = self.measurement_generator.generation_parameters
        self._counter_settings_dict = self.measurement_generator.counter_settings
        self._measurement_settings_dict = self.measurement_generator.measurement_settings
        # self._estimator_stg_dict = self.settings.estimator_stg.convert_settings()
        # self._analyzer_stg_dict = self.settings.analyzer_stg.convert_settings()
        self._estimator_stg_dict = self.settings.estimator_stg.dump_as_dict()
        # self.settings.estimator_stg.save_data_container()
        # self.settings.analyzer_stg.save_data_container()
        self._analyzer_stg_dict = self.settings.analyzer_stg.dump_as_dict()

    def input_estimator_method(self):
        self.estimator.method = self.settings.estimator_stg.current_method

    def input_analyzer_method(self):
        self.analyzer.method = self.settings.analyzer_stg.current_method

    @QtCore.Slot(bool)
    @QtCore.Slot(bool, str)
    def toggle_qdyne_measurement(self, start):
        """
        @param bool start: True for start measurement, False for stop measurement
        """
        self._data_source = DataSource.MEASUREMENT
        if isinstance(start, bool):
            self.sigToggleQdyneMeasurement.emit(start)
        return

    @QtCore.Slot(str)
    @QtCore.Slot(str, bool)
    def do_fit(self, fit_config):
        try:
            self.data.fit_config, self.data.fit_result = self.fit.perform_fit(
                self.data.freq_data.data_around_peak, fit_config
            )
        except:
            self.data.fit_config, self.data.fit_result = "", None
            self.log.exception("Something went wrong while trying to perform data fit.")
        self.sigFitUpdated.emit(self.data.fit_config, self.data.fit_result)
        return self.data.fit_result

    @QtCore.Slot(str)
    def save_data(self, data_type: str):
        self.log.debug(f"Saving data, {data_type=}")
        timestamp = datetime.datetime.now()
        if "all" in data_type:
            for data_type in self.data_manager.data_types:
                self.data_manager.save_data(data_type, timestamp)
        else:
            self.data_manager.save_data(data_type, timestamp)

    @QtCore.Slot(str, str, str)
    def load_data(self, data_type, file_path, index):
        self._data_source = DataSource.LOADED
        if "all" in data_type:
            self.log.error("Select one data type")
            return
        self.data_manager.load_data(data_type, file_path, index)
        self.settings.estimator_stg.update_method(self.data.metadata.state_estimation_method)
        try:
            self.settings.estimator_stg.add_mode("loaded", True, self.settings.estimator_cls_dict[self.data.metadata.state_estimation_method](**self.data.metadata.state_estimation_settings))
        except Exception as e:
            self.log.exception(e)
        self.settings.analyzer_stg.update_method(self.data.metadata.analysis_method)
        self.settings.analyzer_stg.add_mode("loaded", True, self.settings.analyzer_cls_dict[self.data.metadata.analysis_method](**self.data.metadata.analysis_settings))
        self.measure.pull_data_and_estimate()
        # TODO: Fix what to do when it is not raw_data
        self.log.info(f"Loaded {data_type} data from {file_path}")

    @property
    def data_source(self):
        return self._data_source
