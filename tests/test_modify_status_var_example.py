# -*- coding: utf-8 -*-

"""
This test shows how a single status variable can be updated via file and tested through a remote connection

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
from qudi.util.yaml import yaml_load, yaml_dump
from qudi.util.paths import get_module_app_data_path

LOGIC_MODULE = 'odmr_logic'
GUI_MODULE = 'odmr_gui'
STATUS_VAR = 'run_time'
VALUE = 10


@pytest.fixture(scope='module')
def logic_instance(remote_instance):
    """ 
    This fixture returns Odmr logic instance
    """
    module_manager = remote_instance.module_manager
    module_manager.activate_module(GUI_MODULE)
    logic_instance = module_manager._modules[LOGIC_MODULE].instance
    return logic_instance


def get_status_var_file(instance):
    """
    This function returns the path for status variable file 

    Parameters
    ----------
    instance : Object
        Instance of the logic module

    Returns
    -------
    str
        File path
    """    
    file_path = get_module_app_data_path(
            instance.__class__.__name__, instance.module_base, instance.module_name
        )
    return file_path

def load_status_var(file_path):    
    """
    This function returns the loaded status variable from the file

    Parameters
    ----------
    file_path : str
        file path of status variable

    Returns
    -------
    dict
        dictionary of status variables
    """    
    try:
        variables = yaml_load(file_path, ignore_missing=True)
    except Exception as e:
        variables = dict()
        print("Failed to load status variables:", e)
    return variables

def modify_status_var(status_vars, var, value):
    """
    Setting status variable

    Parameters
    ----------
    status_vars : dict
        status variable dict
    var : str
        the variable to be set from the status variable dict
    value : Any
        value to be set

    Returns
    -------
    dict
        status var
    """    
    status_vars[var] = value
    return status_vars

def dump_status_variables(vars, file_path):
    """
    Dump updated status variable to yaml

    Parameters
    ----------
    vars : dict
        status variable dict
    file_path : str
        file path for status variable
    """    
    try:
        yaml_dump(file_path, vars)
    except Exception as e:
        print("Failed to save status variables:", e)


def test_status_vars(qudi_instance, qt_app):
    """ 
    Modifying a specific saved status variable for a specific module

    Parameters
    ----------
    qudi_instance : fixture
        Running qudi instance
    qt_app : fixture
        qt app instance
    """    
    module_manager = qudi_instance.module_manager
    qudi_instance._configure_qudi()
    try:
        module_manager.modules[LOGIC_MODULE]._load()
    except Exception as e:
        print(f'cant load {LOGIC_MODULE} , {e}')

    logic_instance = module_manager.modules[LOGIC_MODULE].instance
    status_var_file_path = get_status_var_file(logic_instance)
    status_vars = load_status_var(status_var_file_path)
    #print(f'Status variables are {status_vars}')
    modified_vars = modify_status_var(status_vars, STATUS_VAR, VALUE)
    dump_status_variables(modified_vars, status_var_file_path)

def test_status_vars_changed(logic_instance, qudi_instance):
    """
    Test whether the status variable has changed
    
    Parameters
    ----------
    logic_instance : fixture
        Remote logic instance
    qudi_instance : fixture
        So that Qudi objects don't go out of scope
    """    
    status_variable = '_' + STATUS_VAR
    assert getattr(logic_instance, status_variable) == VALUE
