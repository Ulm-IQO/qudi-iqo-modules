# -*- coding: utf-8 -*-

"""
This file contains the GUI for qdyne measurements.

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
from PySide2 import QtWidgets

from qudi.util import uic

class QdyneMainWindow(QtWidgets.QMainWindow):
    def __init__(self, gui):
        self._gui = gui
        # Get the path to the *.ui file
        qdyne_dir = os.path.dirname(os.path.dirname(__file__))
        ui_file = os.path.join(qdyne_dir, r'ui\maingui.ui')

        # Load it
        super(QdyneMainWindow, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect_signals(self):
        self.action_Predefined_Methods_Config.triggered.connect(self._gui._gsw.show_predefined_methods_config)
        self.action_FitSettings.triggered.connect(self._gui._fcd.show)

    def disconnect_signals(self):
        self.action_Predefined_Methods_Config.triggered.disconnect()
        self.action_FitSettings.triggered.disconnect()






