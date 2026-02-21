
# -*- coding: utf-8 -*-

"""
This file contains unit tests for the scanning probe logic module with GUI enabled.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-core/>

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

import pytest
import time
import numpy as np
from datetime import datetime

from qudi.util.network import netobtain


LOGIC_MODULE = 'scanning_probe_logic'
GUI_MODULE = 'scanner_gui'

BASE = 'logic'
AXIS_IDX = {'x':0, 'y':1, 'z':2}

@pytest.fixture(scope='module')
def module(qudi_client):
    """
    Fixture that returns scanning probe logic instance.
    """
    module_manager = qudi_client.module_manager
    module_manager.activate_module(GUI_MODULE)
    return module_manager._modules[LOGIC_MODULE].instance



def test_set_target_position(module):
    """
    Test for setting target position

    Parameters
    ----------
    module : fixture
        logic module instance
    """
    logic = module

    scanner = logic._scanner()

    remote_pos = netobtain(scanner.get_target())
    
    new_pos = {}
    for axis in remote_pos:
        val = float(remote_pos[axis])  
        new_val = val + 0.0001
        new_pos[axis] = new_val 

    
    result = logic.set_target_position(new_pos, move_blocking=True)

    final_pos = scanner.get_target()

    for axis in new_pos:
        assert np.isclose(float(final_pos[axis]), new_pos[axis], atol=0.01)


def test_start_and_stop_scan(module):
    """
    Start a scan and verify if scan data becomes available.
        
    Parameters
    ----------
    module : fixture
        logic module instance
    """
    module.set_default_scan_settings()
    axes = list(module.scanner_axes.keys())
    if not axes:
        pytest.skip("No scanner axes available.")

    scan_axes = (axes[0],axes[1])
    time.sleep(5)
    module.start_scan(scan_axes)
    time.sleep(15)
    scan_data = module.scan_data
    assert scan_data is not None
    assert hasattr(scan_data, 'data') 
    dt = netobtain(scan_data.timestamp)
    now = datetime.now()
    assert abs((now - dt).total_seconds()) <= 60

    module.stop_scan()
    assert module.module_state() == 'idle'



