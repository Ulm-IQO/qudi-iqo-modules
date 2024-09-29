# -*- coding: utf-8 -*-

"""
This file contains unit tests for all qudi fit routines for exponential decay models.

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
import weakref
import pytest
from PySide2 import QtWidgets
from qudi.core import application
from qudi.util.yaml import yaml_load

CONFIG = os.path.join(os.getcwd(),'tests/test.cfg')


@pytest.fixture(scope="module")
def qt_app():
    app_cls = QtWidgets.QApplication
    app = app_cls.instance()
    if app is None:
        app = app_cls()
    return app

@pytest.fixture(scope="module")
def qudi_instance():
    instance = application.Qudi.instance()
    if instance is None:
        instance = application.Qudi(config_file=CONFIG)
    instance_weak = weakref.ref(instance)
    return instance_weak()

@pytest.fixture(scope="module")
def module_manager(qudi_instance):
    return qudi_instance.module_manager

@pytest.fixture(scope='module')
def config():
    configuration = (yaml_load(CONFIG))
    return configuration

@pytest.fixture(scope='module')
def gui_modules(config):
    base = 'gui'
    modules  = list(config[base].keys())
    return modules

@pytest.fixture(scope='module')
def logic_modules(config):
    base = 'logic'
    modules  = list(config[base].keys())
    return modules

@pytest.fixture(scope='module')
def hardware_modules(config):
    base = 'hardware'
    modules  = list(config[base].keys())
    return modules