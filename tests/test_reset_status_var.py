# -*- coding: utf-8 -*-

"""
This test resets all status variables for logic modules. GUI modules are tested by activating them after resetting the variables.

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
import time


def test_reset_module(gui_modules, hardware_modules, remote_instance):
    """
    This tests clearing all the logic, hardware module status variables and reloads the GUI modules

    Parameters
    ----------
    gui_modules : Fixture
        List of gui module names
    hardware_modules : Fixture
        List of hardware module names
    remote_instance : Fixture
        Remote qudi rpyc instance
    """    
    module_manager = remote_instance.module_manager
    modules = module_manager.modules
    for module in modules:
        module_manager.deactivate_module(module)

        module_manager.clear_module_app_data(module)
        module_manager.reload_module(module)

    for gui_module in gui_modules:
        gui_managed_module = module_manager.modules[gui_module]
        required_managed_modules = gui_managed_module.required_modules
        required_modules = [required_managed_module().name for required_managed_module in required_managed_modules]

        linked_required_managed_modules = [required_managed_module().required_modules for required_managed_module in required_managed_modules]      
        linked_required_modules = []
        for linked_required_managed_module in linked_required_managed_modules:
            linked_required_modules.extend([required_managed_module().name for required_managed_module in linked_required_managed_module])

        required_logic_modules = required_modules
        required_logic_modules.extend([linked_required_module for linked_required_module in linked_required_modules if linked_required_module not in hardware_modules ])
        required_hardware_managed_modules = [ module_manager.modules[logic_module].required_modules for logic_module in required_logic_modules ]
        required_hardware_modules = []
        for linked_required_managed_module in required_hardware_managed_modules:
            required_hardware_modules.extend([required_managed_module().name for required_managed_module in linked_required_managed_module])

        for required_hardware_module in required_hardware_modules:
            module_manager.deactivate_module(required_hardware_module)
            module_manager.clear_module_app_data(required_hardware_module)
        
        for required_logic_module in required_logic_modules:
            module_manager.deactivate_module(required_logic_module)
            module_manager.clear_module_app_data(required_logic_module)
     
        module_manager.activate_module(gui_module)
        gui_managed_module = module_manager.modules[gui_module]
        assert gui_managed_module.is_active

        time.sleep(10)
        module_manager.deactivate_module(gui_module)
        for required_logic_module in required_logic_modules:
            module_manager.deactivate_module(required_logic_module)

        for required_hardware_module in required_hardware_modules:
            module_manager.deactivate_module(required_hardware_module)

