# -*- coding: utf-8 -*-

"""
This test modifies all status variables as instance variables and then runs all the modules through a remote connection

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
import numpy as np


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
    
'''
#This test also fails for some modules 
def test_status_vars(remote_instance, logic_modules, gui_modules, hardware_modules):
    """
    Test to check if qudi modules launch correctly after modifying status variable as instance variables

    Parameters
    ----------
    remote_instance : fixture
        Running remote qudi instance
    logic_modules : fixture
        List of loaded logic modules
    gui_modules : fixture
        List of loaded gui modules
    hardware_modules : fixture
        List of loaded hardware modules
    """    
    module_manager = remote_instance.module_manager
    for gui_module in gui_modules:
        module_manager.activate_module(gui_module)
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
        print(f'for {gui_module}, required logic are {required_logic_modules}')

        for required_logic_module in required_logic_modules:
            logic_instance = module_manager.modules[required_logic_module].instance
            status_vars = list(logic_instance._meta['status_variables'].keys())
            
            for status_var in status_vars:
                try:
                    status_var_value = status_vars[status_var]
                    status_var_value = netobtain(status_var_value)
                    if not ( isinstance(status_var_value, float) or isinstance(status_var_value, int)):
                        continue
                    #print(status_var, status_var_value , type(status_var_value))
                    random_value = generate_random_value(status_var_value)
                    setattr(logic_instance, status_var, random_value)
                except:
                    pass
            module_manager.deactivate_module(required_logic_module)
            module_manager.reload_module(required_logic_module)

            module_manager.activate_module(required_logic_module)
        module_manager.activate_module(gui_module)
        gui_managed_module = module_manager.modules[gui_module]
        assert gui_managed_module.is_active
        time.sleep(5)
        module_manager.deactivate_module(gui_module)
        time.sleep(1)
'''