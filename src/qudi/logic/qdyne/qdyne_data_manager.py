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
from qudi.logic.qdyne.qdyne_data.measurement_data import DATA_TYPES, MainDataClass, QDyneMetadata
from qudi.logic.qdyne.qdyne_data.save_options import QdyneSaveOptions
from qudi.util.conversions import convert_nested_numpy_to_list

logger = logging.getLogger(__name__)


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

    def save_data(self, data, options: Optional[QdyneSaveOptions] = None) -> None:
        # `options=QdyneSaveOptions()` as a default evaluated once at import, so every caller that
        # omitted it shared one options object - including its mutable `metadata` dict, which then
        # accumulated across unrelated saves.
        if options is None:
            options = QdyneSaveOptions()
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
    #: Single definition, shared with QdyneDataManager and MainDataClass - this used to be declared
    #: separately on two classes that had to agree.
    data_types = DATA_TYPES

    def __init__(self, default_data_dir: str):
        self.default_data_dir = default_data_dir
        self.options: Dict[str, QdyneSaveOptions] = dict()
        self.set_options()

    def set_options(self, **kwargs):
        """Apply `kwargs` to every data type's options, keeping whatever was set before.

        This used to build a fresh QdyneSaveOptions per data type and throw the old ones away. It is
        called from load_options(), so loading a file silently wiped every per-type nametag and any
        accumulated metadata - the caller only meant to update the few fields the file described.
        """
        kwargs.setdefault('data_dir', self.default_data_dir)

        for data_type in self.data_types:
            existing = self.options.get(data_type)
            if existing is None:
                self.options[data_type] = QdyneSaveOptions(**kwargs)
            else:
                for key, value in kwargs.items():
                    setattr(existing, key, value)
        self.set_columns()

    def set_columns(self):
        for data_type in self.data_types:
            self.options[data_type].column_headers = 'Signal (a.u.)'

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
    #: Shared with DataManagerSettings and MainDataClass - see DATA_TYPES.
    data_types = DATA_TYPES

    def __init__(self, data: MainDataClass, settings: DataManagerSettings,
                 storage_class: str = 'npy'):
        """
        Parameters
        ----------
        storage_class : str
            One of DataStorage.data_storage_options. Comes from QdyneLogic's `data_storage_class`
            ConfigOption, which previously had no effect at all - the storage type was hardcoded to
            'npy' for every data type regardless of what the config said.
        """
        self.log = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.data: MainDataClass = data
        self.settings: DataManagerSettings = settings
        self.storage_class = storage_class
        self.storages = dict()
        self.activate_storage()

    @property
    def save_data_types(self):
        return ['all'] + list(self.data_types)

    def activate_storage(self):
        for data_type in self.data_types:
            self.storages[data_type] = DataStorage(
                self.settings.options[data_type].data_dir, self.storage_class)

    def _storage_for(self, data_type: str) -> DataStorage:
        """Return the storage for `data_type`, rebuilding it if the target directory has moved.

        The storages were built once in __init__ and never revisited, so changing the data directory
        afterwards had no effect: the settings said one path and every save still went to the other.
        """
        storage = self.storages[data_type]
        wanted_dir = self.settings.options[data_type].data_dir
        if storage.data_dir != wanted_dir or storage.storage_class != self.storage_class:
            self.log.debug(f'Rebuilding {data_type} storage for {wanted_dir}.')
            storage = DataStorage(wanted_dir, self.storage_class)
            self.storages[data_type] = storage
        return storage

    def save_data(self, data_type, timestamp: Optional[datetime.datetime] = None):
        self.log.debug(f"saving data, {data_type=}, {timestamp=}")
        data = getattr(self.data, data_type)
        options = self.settings.options[data_type]
        if timestamp:
            options.timestamp = timestamp
        self.settings.set_metadata(data_type, self.data.metadata.to_dict())
        self._storage_for(data_type).save_data(data, options)

    def load_data(self, data_type, file_path, index=None):
        loaded_data, metadata, general = self._storage_for(data_type).load_data(file_path)
        if index is not None and index != "":
            loaded_data = loaded_data[index]
        setattr(self.data, data_type, loaded_data)
        self.data.metadata = self._metadata_from_dict(metadata)
        self.settings.load_options(general, metadata)

    @staticmethod
    def _metadata_from_dict(metadata) -> QDyneMetadata:
        """Build QDyneMetadata from a saved file's metadata, tolerating schema drift.

        Delegates to the container's own from_dict() rather than reimplementing the filtering here -
        two copies of the same rule is how they drift apart. Kept as a method because the tests pin
        the behaviour through this name.
        """
        return QDyneMetadata.from_dict(metadata)

    def set_metadata(self, metadata: dict, data_type: str = "") -> None:
        if not data_type:
            self.settings.set_metadata_all(metadata)
            return
        self.settings.set_metadata(data_type, metadata)
