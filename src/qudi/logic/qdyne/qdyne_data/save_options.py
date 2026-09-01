# -*- coding: utf-8 -*-
"""Per-data-type save options.

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
import os
from dataclasses import dataclass, field
from typing import List, Optional

__all__ = ['QdyneSaveOptions']


@dataclass
class QdyneSaveOptions:
    """Arguments for one call into a qudi DataStorage.

    Two evaluated-once traps used to live here:

    * ``timestamp`` defaulted to ``datetime.datetime.now()`` written directly as the default value,
      which Python evaluates **once, at import**. Every save that did not pass an explicit timestamp
      was therefore stamped with the moment qudi started rather than the moment of the save.
    * ``metadata`` needs default_factory for the usual reason - one shared dict across every
      instance otherwise.

    Both now use default_factory, so each instance gets its own.
    """

    data_dir: Optional[str] = None
    use_default: bool = True
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)
    metadata: dict = field(default_factory=dict)
    notes: Optional[str] = None
    nametag: Optional[str] = None
    column_headers: Optional[str] = None
    column_dtypes: Optional[List] = None
    filename: Optional[str] = None

    def refresh_timestamp(self) -> None:
        """Stamp with the current time."""
        self.timestamp = datetime.datetime.now()

    def get_default_timestamp(self) -> None:
        """Deprecated alias for refresh_timestamp(), kept for existing callers."""
        self.refresh_timestamp()

    def get_file_path(self, file_path: str) -> None:
        self.data_dir, self.filename = os.path.split(file_path)

    @staticmethod
    def _get_patched_filename_nametag(file_name=None, nametag=None, suffix_str=''):
        """Return either a full file name or a nametag for a storage object's save_data().

        If a file_name is given, return a file_name with suffix_str patched in and None as nametag.
        If a tag is given, append suffix_str to it and return None as file_name.
        """
        if file_name is None:
            if nametag is None:
                nametag = ''
            return None, f'{nametag}{suffix_str}'
        file_name_stub, file_extension = file_name.rsplit('.', 1)
        return f'{file_name_stub}{suffix_str}.{file_extension}', None
