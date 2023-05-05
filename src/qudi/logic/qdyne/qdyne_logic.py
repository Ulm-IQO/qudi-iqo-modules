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
import numpy as np
import time
from datetime import datetime
from collections import OrderedDict
from PySide2 import QtCore
from dataclasses import dataclass

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import RecursiveMutex
from qudi.util.datastorage import TextDataStorage, CsvDataStorage, NpyDataStorage

from qudi.logic.qdyne.qdyne_state_estimator import (
    StateEstimator, TimeTagBasedEstimatorSettings, TimeSeriesBasedEstimatorSettings)
from qudi.logic.qdyne.qdyne_time_trace_analyzer import (
    TimeTraceAnalyzer, FourierSettings)

@dataclass
class MainDataClass:
    raw_data: np.ndarray = np.array([], dtype=int)
    time_trace: np.ndarray = np.array([], dtype=float)
    signal: np.ndarray = np.array([], dtype=float)
    spectrum: np.ndarray = np.array([], dtype=float)

class QdyneLogic:

    estimator_method = ConfigOption(name='estimator_method', default='TimeTag', missing='warn')
    analyzer_method = ConfigOption(name='analyzer_method', default='Fourier', missing='warn')




    def __init__(self):
        self.estimator = StateEstimator()
        self.analyzer = TimeTraceAnalyzer()
        self.settings = QdyneSettings()
        self.data = MainDataClass()

    def configure(self):
        self.estimator.configure_method(self.estimator_method)
        self.analyzer.configure_method(self.analyzer_method)


        pass

    def input_estimator_settings(self):
        self.estimator.input_settings(self.settings.state_estimator_stg)
    def input_analyzer_settings(self):
        self.analyzer.input_settings(self.settings.time_trace_analysis_stg)

    def start_measurement(self):
        pass

    def stop_measurement(self):
        pass

    def get_raw_data(self):
        pass

    def process_raw_data(self):
        self.data.time_trace = self.estimator.estimate(self.data.raw_data)

    def analyze_time_trace(self):
        self.data.signal = self.analyzer.analyze(self.data.time_trace)

    def get_spectrum(self):
        self.data.spectrum = self.analyzer.get_spectrum(self.data.signal)

    def save(self):
        pass

class QdyneSettings:

    def __init__(self):
        self.state_estimator_method = ''
        self.time_trace_analysis_method = ''
        self.measurement_stg = None
        self.state_estimator_stg = None
        self.time_trace_analysis_stg = None


    def on_activate(self):
        self.measurement_stg =
        self.state_estimator_stg = self.get_state_estimator_stg(self.state_estimator_method)
        self.time_trace_analysis_stg = self.get_time_trace_analysis_stg(self.time_trace_analysis_method)

    def get_measurement_stg(self):
        pass

    def get_state_estimator_stg(self, state_estimator_method):
        if state_estimator_method == 'TimeSeries':
            self.state_estimator_stg = TimeSeriesBasedEstimatorSettings()

        elif state_estimator_method == 'TimeTag':
            self.state_estimator_stg = TimeTagBasedEstimatorSettings()

        else:
            self.state_estimator_stg = None

    def get_time_trace_analysis_stg(self, time_trace_analysis_method):
        if time_trace_analysis_method == 'Fourier':
            self.time_trace_analysis_stg = FourierSettings()

        else:
            self.time_trace_analysis_stg = None

@dataclass
class QdyneSaveOptions:
    use_default: bool = True
    timestamp: datetime.datetime = None
    metadata: dict = None
    notes: str = None
    nametag: str = None
    column_headers: str = None
    column_dtypes: list = None
    filename: str = None
    additional_nametag: str = None

    def __init__(self, nametag, column_headers, additional_nametag):
        self.nametag = nametag
        self.column_headers = column_headers
        self.additional_nametag = additional_nametag

    @property
    def custom_nametag(self):
        return self.nametag + self.additional_nametag

    def get_default_timestamp(self):
        self.timestamp = datetime.now()

    def get_file_path(self, file_path):
        if file_path is None:
            self.data_dir = self.module_default_data_dir
            self.filename = None
        else:
            self.data_dir, self.filename = os.path.split(file_path)

    def set_static_options(self, nametag, column_headers, column_dtypes,
                           filename, additional_nametag):
        if nametag is not None:
            self.nametag = nametag
        if column_headers is not None:
            self.column_headers = column_headers
        if column_dtypes is not None:
            self.column_dtypes = column_dtypes
        if self.filename is not None:
            self.filename = filename
        if additional_nametag is not None:
            self.additional_nametag = additional_nametag

    def set_dynamic_options(self, timestamp, metadata, notes):
        if timestamp is not None:
            self.timestamp = timestamp
        if metadata is not None:
            self.metadata = metadata
        if notes is not None:
            self.metadata = metadata

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

class QdyneSaver:
    data_storage_options = ['text', 'csv', 'npy']

    def __init__(self, data_dir, storage_class):
        self.data_dir = data_dir
        self.storage_class = storage_class
        self.storage = None
        self.options = QdyneSaveOptions()
        self.raw_data_options = QdyneSaveOptions(nametag='qdyne',
                                                 column_headers='Signal',
                                                 additional_nametag='_raw_data')
        self.timetrace_options = QdyneSaveOptions(nametag='qdyne',
                                                  column_headers='Signal',
                                                  additional_nametag='_timetrace')
        self.signal_options = QdyneSaveOptions(nametag='qdyne',
                                               column_headers='Signal',
                                               additional_nametag='_signal')

    def _set_data_storage(self, cfg_str):
        cfg_str = cfg_str.lower()
        if cfg_str == 'text':
            return TextDataStorage
        if cfg_str == 'csv':
            return CsvDataStorage
        if cfg_str == 'npy':
            return NpyDataStorage
        raise ValueError('Invalid ConfigOption value to specify data storage type.')

    def create_storage(self):
        storage_cls = self._set_data_storage(self.storage_class)
        self.storage = storage_cls(self.data_dir)


    def save_data(self, data, options:QdyneSaveOptions)->None:
        self.storage.save_data(
            data=data,
            nametag=options.custom_nametag,
            timestamp=options.timestamp,
            metadata=options.metadata,
            notes=options.notes,
            column_headers=options.column_headers,
            column_dtypes=options.column_dtypes,
            filename=options.filename)

    def save_raw_data(self, raw_data):
        self.save_data(raw_data, self.raw_data_options)

    def save_time_trace(self, time_trace):
        self.save_data(time_trace, self.timetrace_options)

    def save_signal(self, signal):
        self.save_data(signal, self.signal_options)






