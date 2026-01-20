# -*- coding: utf-8 -*-

"""
This file contains unit tests for the scanning probe logic module without GUI.

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
import numpy as np
from qudi.core import modulemanager
from qudi.util.network import netobtain
from qudi.interface.scanning_probe_interface import ScanSettings


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
    #qudi_instance._configure_qudi()
    module_manager.activate_module(LOGIC_MODULE)
    return module_manager._modules[LOGIC_MODULE].instance


def alter_scan_parameters(module, axis):
        """
        Change scan settings and return them

        Parameters
        ----------
        module : object
            logic module instance
        axis: 
            scan axis
        """
        axis_idx = AXIS_IDX[axis]
        scan_settings = module.get_scan_settings_per_ax()
        axis_scan_settings = scan_settings[axis_idx] 
       
        settings, back_settings = axis_scan_settings
        range = settings.range[0]
        resolution = settings.resolution[0]
        frequency = settings.frequency

        new_range = (range[0] + 0.00005, range[1] - 0.00005)
        new_resolution = resolution+50
        new_frequency = frequency-500

        new_settings =  ScanSettings(
                channels=tuple(module.scanner_channels),
                axes= tuple((axis,)),
                range= tuple((new_range,)),
                resolution=tuple((new_resolution,)),
                frequency=new_frequency,
            )
 

        return new_settings, (new_range, new_resolution, new_frequency)


def test_initial_scan_settings(module):
    """
    Verify default scan settings are initialized and valid.

    Parameters
    ----------
    module : fixture
        logic module instance
    """
    scan_ranges = module.scan_ranges
    scan_resolution = module.scan_resolution
    scan_frequency = module.scan_frequency

    assert isinstance(scan_ranges, dict)
    assert isinstance(scan_resolution, dict)
    assert isinstance(scan_frequency, dict)

    for axis in module.scanner_axes:
        assert axis in scan_ranges
        assert axis in scan_resolution
        assert axis in scan_frequency
        r_min, r_max = scan_ranges[axis]
        assert r_min < r_max
        assert scan_resolution[axis] > 0
        assert scan_frequency[axis] > 0


def test_set_scan_settings(module):
    """
    Test if scan settings are updated correctly.

    Parameters
    ----------
    module : fixture
        logic module instance
    """
    axes = list(module.scanner_axes.keys())
    if not axes:
        pytest.skip("No scanner axes available.")

    for axis_idx,axis in enumerate(axes):
        altered_settings, (new_range, new_resolution, new_frequency) = alter_scan_parameters(module, axis)
        module.set_scan_settings(altered_settings)
        updated_range = module.scan_ranges[axis]
        updated_resolution = module.scan_resolution[axis]
        updated_frequency = module.scan_frequency[axis]
        assert np.allclose(updated_range, new_range)
        assert np.allclose(updated_resolution, new_resolution)
        assert updated_frequency == new_frequency
    module.set_default_scan_settings()


def test_tilt_vector_dict_2_array(module):
    """
    Test for tilt_vector_dict_2_arra

    Parameters
    ----------
    module : fixture
        logic module instance
    """
    axes = list(module.scanner_axes.keys())
    if len(axes) < 3:
        pytest.skip("Tilt correction requires at least 3 axes.")
    vecs = [
        {axes[0]: 1, axes[1]: 0, axes[2]: 0},
        {axes[0]: 0, axes[1]: 1, axes[2]: 0},
        {axes[0]: 0, axes[1]: 0, axes[2]: 1}
    ]
    vecs_arr = [np.array([1., 0., 0.]), np.array([0., 1., 0.]), np.array([0., 0., 1.])]
    res = netobtain(module.tilt_vector_dict_2_array(vecs))
    for axis in range(len(vecs_arr)):
        assert (vecs_arr[axis]==res[axis]).all()


def test_tilt_vector_array_2_dict(module):
    """
    Test for tilt_vector_array_2_dict
    Parameters
    ----------
    module : fixture
        logic module instance
    """
    vecs_arr = [np.array([1., 0., 0.]), np.array([0., 1., 0.]), np.array([0., 0., 1.])]
    axes = list(module.scanner_axes.keys())
    if len(axes) < 3:
        pytest.skip("Tilt correction requires at least 3 axes.")
    vecs = {'x': np.array([1., 0., 0.]), 'y': np.array([0., 1., 0.]), 'z': np.array([0., 0., 1.])}
    res = module.tilt_vector_array_2_dict(vecs_arr,False)

    for key in vecs:
        assert np.allclose(vecs[key], res[key]), f"Mismatch at key: {key}"


def test_configure_tilt_correction(module, qtbot):
    """
    Test for Configure tilt correction.

    Parameters
    ----------
    module : fixture
        logic module instance
    qtbot : fixture
    """
    axes = list(module.scanner_axes.keys())
    if len(axes) < 3:
        pytest.skip("Tilt correction requires at least 3 axes.")

    support = [
        {'x': 1, 'y': 0, 'z': 0},
        {'x': 1, 'y': 0, 'z': 1},
        {'x': 0, 'y': 1, 'z': 1},
    ]
    with qtbot.waitSignal( module.sigTiltCorrSettingsChanged) as blockers:
        module.configure_tilt_correction(support_vecs=support)

        # collect transformed support vectors (flat virtual plane to real tilted plane)
        T = netobtain(module._tilt_corr_transform)   
        print(T)
        # After the transform , the inverse transform should make all points have same z - coordinate, since the tranformation is supposed to transform the plane so that its normal aligns with z-axis
        flattened = np.vstack([T([v['x'], v['y'], v['z']], invert=True) for v in support])
        #  all z-coordinates must now be identical (plane horizontal) 
        assert np.allclose(flattened[:, 2], flattened[0, 2])
        #  all three spatial axes participate in the correction
        assert netobtain(module._tilt_corr_axes) == ['x', 'y', 'z']
        
        emitted_signal = blockers.args
    assert emitted_signal[0]['vec_1']  ==  {'x': 1, 'y': 0, 'z': 0}

