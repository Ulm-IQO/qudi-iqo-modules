# -*- coding: utf-8 -*-

"""
This file contains the fixtures and functions that are commonly used across tests.

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
import pytest
import sys
import time
import subprocess
from PySide2 import QtWidgets
from qudi.core import application
from qudi.util.yaml import yaml_load



CONFIG = os.path.join(os.getcwd(),'tests/test.cfg')


@pytest.fixture(scope="module")
def qt_app():
    """
    Fixture for QApplication instance to enable GUI.
    """
    app_cls = QtWidgets.QApplication
    app = app_cls.instance()
    if app is None:
        app = app_cls()
    return app

@pytest.fixture(scope="module")
def qudi_instance():
    """
    Fixture for Qudi instance.
    """
    instance = application.Qudi.instance()
    if instance is None:
        instance = application.Qudi(config_file=CONFIG)
    return instance

@pytest.fixture(scope="module")
def module_manager(qudi_instance):
    """
    Fixture for module manager.
    """
    return qudi_instance.module_manager

@pytest.fixture(scope='module')
def config():
    """
    Fixture for loaded config.
    """
    configuration = (yaml_load(CONFIG))
    return configuration

@pytest.fixture(scope='module')
def gui_modules(config):
    """
    Fixture for list of GUI modules from the config.
    """
    base = 'gui'
    modules = list(config[base].keys())
    return modules

@pytest.fixture(scope='module')
def logic_modules(config):
    """
    Fixture for list of logic modules from the config.
    """
    base = 'logic'
    modules = list(config[base].keys())
    return modules

@pytest.fixture(scope='module')
def hardware_modules(config):
    """
    Fixture for list of hardware modules from the config.
    """
    base = 'hardware'
    modules = list(config[base].keys())
    return modules


@pytest.fixture(scope="module")
def qudi_gui():
    """Start the Qudi GUI in a child process with the given config.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "qudi.core", "--config", str(CONFIG)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    time.sleep(3)
    try:
        yield proc
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

@pytest.fixture(scope="module")
def qudi_client(qudi_gui):
    """Attach to the running GUI using qudikernel.QudiKernelClient."""
    from qudi.core.qudikernel import QudiKernelClient
    client = QudiKernelClient()
    timeout = time.time() + 60
    last_err = None
    while time.time() < timeout:
        try:
            client.connect()   
            ns = client.get_active_modules()
            qudi = ns.get('qudi')
            return qudi
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"Could not connect to Qudi namespace server: {last_err}")
