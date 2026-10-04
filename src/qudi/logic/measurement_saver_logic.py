# -*- coding: utf-8 -*-

"""
This file contains the Qudi Logic module base class.

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

from __future__ import annotations

import datetime
from contextlib import contextmanager
from cattrs import Converter
from typing import Any, Iterable, Iterator, Mapping, Optional, Type


from qudi.core.module import LogicBase
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.util.data_conversion import get_converter
from qudi.util.datastorage import (DataStorageBase, TextDataStorage, CsvDataStorage,
                                    NpyDataStorage)

__all__ = ['MeasurementSaverLogic']


class MeasurementSaverLogic(LogicBase):
    """Connectable logic module that all data saving is routed through."""

    _default_storage = ConfigOption(name='default_storage', default='text')

    _STORAGE_CLASSES = {'text': TextDataStorage,
                        'csv': CsvDataStorage,
                        'npy': NpyDataStorage}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_lock = RecursiveMutex()
        self._converter: Optional[Converter] = None
        self._timestamp: Optional[datetime.datetime] = None
        self._session_storage: Optional[DataStorageBase] = None

    def on_activate(self) -> None:
        self._converter = self._create_converter()
        self._timestamp = None
        self._session_storage = None

    def on_deactivate(self) -> None:
        self._converter = None
        self._timestamp = None
        self._session_storage = None

    # -------------- config --



    def _create_converter(self) -> Converter:
        """ the cattrs converter used to unstructure typed metadata.

        """
        return get_converter()

    # --------------- storage --

    def _resolve_storage_cls(self, storage_cls) -> Type[DataStorageBase]:
        if storage_cls is None:
            return self._STORAGE_CLASSES[self._default_storage]
        if isinstance(storage_cls, str):
            return self._STORAGE_CLASSES[storage_cls]
        return storage_cls

    def _create_storage(self, root_dir=None, storage_cls=None, **storage_options) -> DataStorageBase:
        cls = self._resolve_storage_cls(storage_cls)
        
        root = self.module_default_data_dir if root_dir is None else root_dir
        return cls(root_dir=root, **storage_options)

    # -------------- metadata --

    def _to_metadata_dict(self, metadata: Any) -> dict[str, Any]:
        if isinstance(metadata, Mapping):
            return dict(metadata)
        return self._converter.unstructure(metadata)

    def to_metadata_dict(self, metadata: Any) -> Optional[dict[str, Any]]:
        """Unstructure metadata into the plain dict save_data() expects.

        Accepts a typed dataclass, a dict, None, or a sequence of those (merged
        left to right).
        """
        if metadata is None:
            return None
        items: Iterable = metadata if isinstance(metadata, (list, tuple)) else (metadata,)
        merged: dict[str, Any] = {}
        for item in items:
            merged.update(self._to_metadata_dict(item))
        return merged

    # -------------- name patching --

    @staticmethod
    def join_nametag(*parts, sep: str = '_') -> str:
        """Join parts into a nametag, skipping None/empty parts.

       """
        return sep.join(str(p) for p in parts if p not in (None, ''))

    @staticmethod
    def patch_filename(filename: str, *suffix_parts, sep: str = '_') -> str:
        """Insert a suffix before an explicit filename's extension.
        """
        stub, ext = filename.rsplit('.', 1)
        tail = sep.join(str(p) for p in suffix_parts if p not in (None, ''))
        return f'{stub}{sep}{tail}.{ext}' if tail else filename

    # --------------------- save --

    @contextmanager
    def save_session(self, *, root_dir=None, storage_cls=None, **storage_options) -> Iterator['MeasurementSaverLogic']:
        """Group several saves into one operation."""
        with self._thread_lock:
            self._timestamp = datetime.datetime.now()
            self._session_storage = self._create_storage(root_dir, storage_cls, **storage_options)
            try:
                yield self
            finally:
                self._timestamp = None
                self._session_storage = None

    def save_data(self, data, *, metadata: Any = None, root_dir=None, storage_cls=None,
                  storage_options: Optional[dict] = None, **kwargs):
        """Unstructure metadata and delegate to the storage backend.

        """
        with self._thread_lock:
            storage = self._session_storage
            if storage is None:
                storage = self._create_storage(root_dir, storage_cls, **(storage_options or {}))
            kwargs.setdefault('timestamp', self._timestamp or datetime.datetime.now())
            return storage.save_data(data,
                                     metadata=self.to_metadata_dict(metadata),
                                     **kwargs)

    def save_thumbnail(self, mpl_figure, file_path, *, root_dir=None, storage_cls=None,
                       storage_options: Optional[dict] = None):
        """Save a thumbnail, honoring the save_thumbnails config (no-op if off)."""
        with self._thread_lock:
            storage = self._session_storage
            if storage is None:
                storage = self._create_storage(root_dir, storage_cls, **(storage_options or {}))
            return storage.save_thumbnail(mpl_figure, file_path)
