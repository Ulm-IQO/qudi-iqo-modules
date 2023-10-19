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

from PySide2 import QtCore, QtWidgets
from qudi.util import uic

class QdyneMainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\maingui.ui')

        # Load it
        super(QdyneMainWindow, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass

class MeasurementWidget(QtWidgets.QWidget):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\measurement_widget.ui')

        # Load it
        super(MeasurementWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass


class StateEstimatorWidget(QtWidgets.QWidget):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\state_estimator_widget.ui')

        # Load it
        super(StateEstimatorWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass


class TimeTraceAnalysisWidget(QtWidgets.QWidget):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\time_trace_analysis_widget.ui')

        # Load it
        super(TimeTraceAnalysisWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self, analyzer, settings):
        self.analyzer = analyzer
        self.settings = settings
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass
