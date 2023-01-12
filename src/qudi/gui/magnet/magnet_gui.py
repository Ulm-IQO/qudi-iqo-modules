# -*- coding: utf-8 -*-

"""
This file contains the QuDi main GUI for pulsed measurements.

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
import datetime
import numpy as np
import pyqtgraph as pg
from enum import Enum

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.helpers import natural_sort
from qudi.util.datastorage import get_timestamp_filename
from qudi.util.datastorage import TextDataStorage, CsvDataStorage, NpyDataStorage
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.core.module import GuiBase
from qudi.util import uic
from PySide2 import QtCore, QtWidgets
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from qudi.util.widgets.loading_indicator import CircleLoadingIndicator

class MagnetMainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_magnet_maingui.ui')
        super(MagnetMainWindow, self).__init__()
        uic.loadUi(ui_file, self)

class DeviceDisplayTab(QtWidgets.QWidget):
    def __init__(self):
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_device_display_tab.ui')
        super().__init__()
        uic.loadUi(ui_file, self)

class MagnetGui(GuiBase):

    _magnet_logic = Connector(name='magnet_logic', interface='MagnetLogic')


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_activate(self):
        self._mw = MagnetMainWindow()
        self._ddt = DeviceDisplayTab()
#        self._mct = MagnetControlTab()
#        self._mot = MagnetOptimizerTab()

        self._add_tabs()
        self._activate_ui()
        self._connect_signals()
        self.show()

    def _add_tabs(self):
        self._mw.tabWidget.addTab(self._ddt, 'Device Display')

    def _activate_ui(self):
        pass

    def _connect_signals(self):
        pass

    def show(self):
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def on_deactivate(self):
        self._deactivate_ui()
        self._disconnect_signals()
        self._mw.close()
        pass

    def _deactivate_ui(self):
        pass
    def _disconnect_signals(self):
        pass





