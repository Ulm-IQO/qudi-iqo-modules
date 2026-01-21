# -*- coding: utf-8 -*-

"""
This is a utility module for the status variable workflow
It saves the current status variables to a temporary directory to be pushed to workflow_save branch 

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
import shutil
import pathlib
from qudi.util.paths import get_appdata_dir

SAVED_STATUS_VAR_DIR = 'saved_status_vars'
if not os.path.isdir(SAVED_STATUS_VAR_DIR) :
    os.mkdir(SAVED_STATUS_VAR_DIR)
ACTIVE_STATUS_VAR_DIR = get_appdata_dir()
SV_STATUS_FILE = 'status_var_changes.txt'

with open(SV_STATUS_FILE, 'r') as file:
    sv_status = ''.join(file.readlines())
    if not 'No differences found'in sv_status:
        for active_sv_file in os.listdir(ACTIVE_STATUS_VAR_DIR):
            file_extension = pathlib.Path(active_sv_file).suffix
            if not ('logic' in active_sv_file or 'hardware' in active_sv_file) or file_extension!='.cfg':
                continue
            active_sv_file_path = os.path.join(ACTIVE_STATUS_VAR_DIR, active_sv_file)
            shutil.copy(active_sv_file_path, SAVED_STATUS_VAR_DIR)



