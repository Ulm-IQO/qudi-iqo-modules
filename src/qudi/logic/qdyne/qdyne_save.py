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
import datetime
from dataclasses import dataclass

from qudi.util.datastorage import TextDataStorage, CsvDataStorage, NpyDataStorage


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


class QdyneSaveSettings:
    raw_data_options = QdyneSaveOptions(nametag='qdyne',
                                        column_headers='Signal',
                                        additional_nametag='_raw_data')
    timetrace_options = QdyneSaveOptions(nametag='qdyne',
                                         column_headers='Signal',
                                         additional_nametag='_timetrace')
    signal_options = QdyneSaveOptions(nametag='qdyne',
                                      column_headers='Signal',
                                      additional_nametag='_signal')

class QdyneSave:
    data_storage_options = ['text', 'csv', 'npy']

    def __init__(self, data_dir, storage_class):
        self.data_dir = data_dir
        self.storage_class = storage_class
        self.storage = None

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

    def save_data(self, data, options: QdyneSaveOptions) -> None:
        self.storage.save_data(
            data=data,
            nametag=options.custom_nametag,
            timestamp=options.timestamp,
            metadata=options.metadata,
            notes=options.notes,
            column_headers=options.column_headers,
            column_dtypes=options.column_dtypes,
            filename=options.filename)
