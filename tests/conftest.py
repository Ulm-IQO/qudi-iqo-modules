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
import multiprocessing
import rpyc
from PySide2 import QtWidgets
from PySide2.QtCore import QTimer
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

CONFIG = os.path.join(os.getcwd(),'tests/test.cfg')


def run_qudi(timeout=150000):
    """
    Runs a Qudi instance with a timer.

    Parameters
    ----------
    timeout : int, optional
        timeout for the Qudi session in milliseconds, by default 150000.
    """
    app_cls = QtWidgets.QApplication
    app = app_cls.instance()
    if app is None:
        app = app_cls()
    qudi_instance = application.Qudi.instance()
    if qudi_instance is None:
        qudi_instance = application.Qudi(config_file=CONFIG)
    QTimer.singleShot(timeout, qudi_instance.quit)
    qudi_instance.run()


@pytest.fixture(scope='module')
def start_qudi_process():
    """
    Fixture that starts the Qudi process and ensures it's running before returning.
    """
    qudi_process = multiprocessing.Process(target=run_qudi)
    qudi_process.start()
    time.sleep(10)
    yield
    qudi_process.join(timeout=10)
    if qudi_process.is_alive():
        qudi_process.terminate()

def connect_with_retries(host, port, config, retries=5, delay=2):
    """
    Attempt to connect multiple times to the RPyC server.

    Parameters
    ----------
    host    : str 
        The server's hostname or IP address.
    port    : int 
        The port number to connect to.
    retries : int 
        Number of retries before giving up.
    delay   : int or float
        Delay in seconds between attempts.

    Returns
    -------
    conn (rpyc.Connection)
        The RPyC connection if successful.

    Raises:
        Exception: If all attempts fail.
    """
    attempt = 0
    while attempt < retries:
        try:
            print(f"Attempt {attempt + 1} of {retries}...")
            conn = rpyc.connect(host, port, config=config)
            print("Connection successful!")
            return conn  # Return the connection object if successful
        except Exception as e:
            print(f"Connection attempt {attempt + 1} failed: {e}")
            attempt += 1
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("All connection attempts failed.")
                raise  # Re-raise the last exception after final attempt

@pytest.fixture(scope='module')
def remote_instance(start_qudi_process):
    """
    Fixture that connects to the running Qudi ipython kernel through rpyc client and returns the client instance.
    """
    time.sleep(5)
    conn = connect_with_retries(host="localhost", port=18861, config={'sync_request_timeout': 20})
    root = conn.root
    qudi_instance = root._qudi
    return qudi_instance