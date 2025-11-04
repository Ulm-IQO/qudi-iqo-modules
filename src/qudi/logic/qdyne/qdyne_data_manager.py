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
import logging
import os
import datetime
from dataclasses import asdict, dataclass, field, fields
from typing import Dict, Optional

from qudi.util.datastorage import TextDataStorage, CsvDataStorage, NpyDataStorage, DataStorageBase
from qudi.logic.qdyne.qdyne_dataclass import MainDataClass, QDyneMetadata
from qudi.util.conversions import convert_nested_numpy_to_list

logger = logging.getLogger(__name__)


@dataclass
class QdyneSaveOptions:
    data_dir: Optional[str] = None
    use_default: bool = True
    timestamp: Optional[datetime.datetime] = datetime.datetime.now()
    metadata: dict = field(default_factory=dict)
    notes: Optional[str] = None
    nametag: Optional[str] = None
    column_headers: Optional[str] = None
    column_dtypes: Optional[list] = None
    filename: Optional[str] = None

    def get_default_timestamp(self):
        self.timestamp = datetime.datetime.now()

    def get_file_path(self, file_path: str):
        self.data_dir, self.filename = os.path.split(file_path)

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


class DataStorage:
    data_storage_options = ['text', 'csv', 'npy']

    def __init__(self, data_dir, storage_class):
        self.data_dir = data_dir
        self.storage_class = storage_class
        self.storage: DataStorageBase = None

        self.create_storage()

    def create_storage(self):
        storage_cls = self._set_data_storage(self.storage_class)
        self.storage = storage_cls(root_dir=self.data_dir)

    def _set_data_storage(self, cfg_str):
        cfg_str = cfg_str.lower()
        if cfg_str == 'text':
            return TextDataStorage
        if cfg_str == 'csv':
            return CsvDataStorage
        if cfg_str == 'npy':
            return NpyDataStorage
        raise ValueError('Invalid ConfigOption value to specify data storage type.')

    def save_data(self, data, options: Optional[QdyneSaveOptions] = QdyneSaveOptions()) -> None:
        self.storage.save_data(
            data=data,
            nametag=options.nametag,
            timestamp=options.timestamp,
            metadata=convert_nested_numpy_to_list(options.metadata),
            notes=convert_nested_numpy_to_list(options.notes),
            column_headers=options.column_headers,
#            column_dtypes=options.column_dtypes,
            filename=options.filename)

    def load_data(self, file_path):
        data, metadata, general = self.storage.load_data(file_path)
        return data, metadata, general


class DataManagerSettings:
    data_types = ['raw_data', 'time_trace', 'freq_domain', 'time_domain']

    def __init__(self, default_data_dir: str):
        self.default_data_dir = default_data_dir
        self.options: Dict[str, QdyneSaveOptions] = dict()
        self.set_options()

    def set_options(self, **kwargs):
        if "data_dir" not in kwargs:
            kwargs["data_dir"] = self.default_data_dir

        for data_type in self.data_types:
            self.options[data_type] = QdyneSaveOptions(**kwargs)
        self.set_columns()

    def set_columns(self):
        self.options['raw_data'].column_headers = 'Signal (a.u.)'
        self.options['time_trace'].column_headers = 'Signal (a.u.)'
        self.options['freq_domain'].column_headers = 'Signal (a.u.)'
        self.options['time_domain'].column_headers = 'Signal (a.u.)'

    def set_all(self, method, value):
        for data_type in self.data_types:
            method(data_type, value)

    def set_data_dir(self, data_type, data_dir):
        self.options[data_type].data_dir = data_dir

    def set_data_dir_all(self, data_dir):
        self.set_all(self.set_data_dir, data_dir)

    def set_nametag(self, data_type, nametag):
        self.options[data_type].nametag = nametag + '_' + data_type

    def set_nametag_all(self, nametag):
        self.set_all(self.set_nametag, nametag)

    def set_metadata(self, data_type: str, metadata: dict) -> None:
        self.options[data_type].metadata.update(metadata)

    def set_metadata_all(self, metadata: dict) -> None:
        self.set_all(self.set_metadata, metadata)

    def load_options(self, general: dict, metadata: dict):
        dictionary = {**general, 'metadata': metadata}
        valid_fields = [f.name for f in fields(QdyneSaveOptions)]
        filtered_dict = {key: dictionary[key] for key in valid_fields if key in dictionary.keys()}
        self.set_options(**filtered_dict)


class QdyneDataManager:
    data_types = ['raw_data', 'time_trace', 'freq_domain', 'time_domain']
    storage_dict = {'raw_data': 'npy', 'time_trace': 'npy', 'freq_domain': 'npy', 'time_domain': 'npy'}

    def __init__(self, data: MainDataClass, settings: DataManagerSettings):
        self.log = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.data: MainDataClass = data
        self.settings: DataManagerSettings = settings
        self.storages = dict()
        self.activate_storage()

    @property
    def save_data_types(self):
        return ['all'] + self.data_types

    def activate_storage(self):
        for data_type in self.data_types:
            self.storages[data_type] = DataStorage(
                self.settings.options[data_type].data_dir, self.storage_dict[data_type])

    def save_data(self, data_type, timestamp: Optional[datetime.datetime] = None):
        self.log.debug(f"saving data, {data_type=}, {timestamp=}")
        data: MainDataClass = getattr(self.data, data_type)
        options = self.settings.options[data_type]
        if timestamp:
            options.timestamp = timestamp
        self.settings.set_metadata(data_type, asdict(self.data.metadata))
        self.storages[data_type].save_data(data, options)

    def load_data(self, data_type, file_path, index=None):
        loaded_data, metadata, general= self.storages[data_type].load_data(file_path)
        if index is not None and index != "":
            loaded_data = loaded_data[index]
        setattr(self.data, data_type, loaded_data)
        try:
            self.data.metadata = QDyneMetadata(**metadata)
        except Exception as e:
            self.log.exception(e)
        self.settings.load_options(general, metadata)

    def set_metadata(self, metadata: dict, data_type: str = "") -> None:
        if not data_type:
            self.settings.set_metadata_all(metadata)
            return
        self.settings.set_metadata(data_type, metadata)
