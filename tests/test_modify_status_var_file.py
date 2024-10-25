# -*- coding: utf-8 -*-

"""
This test modifies all status variables via file and then runs all the modules through a remote connection

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
import os
import time
import random
import string
import pytest
import numpy as np
from qudi.util.yaml import yaml_load, yaml_dump
from qudi.util.paths import get_module_app_data_path


@pytest.fixture
def module_manager(remote_instance):
    """ 
    Fixture for module manager of qudi remote instance 
    """
    return remote_instance.module_manager

def generate_random_value(var):
    """
    Generate random values for a given data type

    Parameters
    ----------
    var : Any
        Initial value

    Returns
    -------
    Any
        Random value

    Raises
    ------
    ValueError
        when data type is not valid
    """    
    if isinstance(var, int):
        return random.randint(-100, 100)

    elif isinstance(var, float):
        return random.uniform(-100.0, 100.0)

    elif isinstance(var, str):
        return ''.join(random.choices(string.ascii_lowercase, k=len(var)))

    elif isinstance(var, list):
        return [generate_random_value(elem) for elem in var]

    elif isinstance(var, tuple):
        return tuple(generate_random_value(elem) for elem in var)

    elif isinstance(var, dict):
        return {key: generate_random_value(value) for key, value in var.items()}

    elif isinstance(var, np.ndarray):
        return np.random.random(var.shape) * 200 - 100 

    else:
        raise ValueError(f"Unsupported data type: {type(var)}")

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

def modify_status_var(status_vars):
    """
    This function updates the status variables with random values of the same data type

    Parameters
    ----------
    status_vars : dict
        All status vars of a module

    Returns
    -------
    dict
        updated status vars
    """    
    for status_var in status_vars:
        status_var_value = status_vars[status_var]
        if not ( isinstance(status_var_value, float) or isinstance(status_var_value, int)):
            continue
        #print(status_var, status_var_value , type(status_var_value))
        random_value = generate_random_value(status_var_value)
        status_vars[status_var] = random_value
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


'''
# Updating status vars to illegal values might break modules, ( For example, PID has no way no reset status vars)
def test_update_status_vars(qudi_instance, gui_modules, hardware_modules, qt_app):
    """
    Test to check if qudi modules launch correctly after modifying saved status variables 

    Parameters
    ----------
    qudi_instance : fixture
        Running qudi instance
    gui_modules : fixture
        List of GUI modules
    hardware_modules : fixture
        List of hardware modules
    qt_app : fixture
        qt app instance
    """    
    module_manager = qudi_instance.module_manager
    qudi_instance._configure_qudi()

    for gui_module in gui_modules:
        gui_managed_module = module_manager.modules[gui_module]
        required_managed_modules = gui_managed_module.required_modules
        required_modules = [required_managed_module().name for required_managed_module in required_managed_modules]

        linked_required_managed_modules = [required_managed_module().required_modules for required_managed_module in required_managed_modules]
        #print(f'linked required managed modules are {linked_required_managed_modules}')
      
        linked_required_modules = []
        for linked_required_managed_module in linked_required_managed_modules:
            linked_required_modules.extend([required_managed_module().name for required_managed_module in linked_required_managed_module])

        required_logic_modules = required_modules
        required_logic_modules.extend([linked_required_module for linked_required_module in linked_required_modules if linked_required_module not in hardware_modules ])
        #print(f'for {gui_module}, required logic are {required_logic_modules}')

        for required_logic_module in required_logic_modules:
            try:
                module_manager.modules[required_logic_module]._load()
            except Exception as e:
                print(f'cant load {required_logic_module} , {e}')
                continue
            logic_instance = module_manager.modules[required_logic_module].instance
            status_var_file_path = get_status_var_file(logic_instance)
            vars = load_status_var(status_var_file_path)
            modified_vars = modify_status_var(vars)
            dump_status_variables(modified_vars, status_var_file_path)


def test_status_vars(module_manager, gui_modules, qudi_instance, qt_app):
    """
    Test if GUI modules are activated correctly after updating the saved files.

    Parameters
    ----------
    module_manager : fixture
        Remote module manager
    gui_modules : fixture
        List of GUI modules
    qudi_instance : fixture
        So that Qudi objects don't go out of scope
    qt_app : fixture
        Instance of Qt app
    """    
    for gui_module in gui_modules:
        module_manager.activate_module(gui_module)
        gui_managed_module = module_manager.modules[gui_module]
        assert gui_managed_module.is_active
        time.sleep(5)
        module_manager.deactivate_module(gui_module)
        time.sleep(1)
    qudi_instance.quit()

'''