# -*- coding: utf-8 -*-
"""
This file contains a qudi logic module template

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

from qudi.core.connector import Connector
from qudi.core.module import LogicBase
from PySide2 import QtCore
import numpy as np
from qudi.core.statusvariable import StatusVar
import math

class PulsedPlottingLogic(LogicBase):

    sigPulsedPlotUpdated = QtCore.Signal(object,  QtCore.Qt.QueuedConnection)
    _pulsed = Connector(name='pulsed', interface='PulsedMasterLogic')
    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

    def on_activate(self):
        self.pulsed = self._pulsed()
        self.pulsed.sequencegeneratorlogic().sigLoadedAssetUpdated.connect(
            self.construct_timing_diagram, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        return 0

    @QtCore.Slot()
    def construct_timing_diagram(self, name, pulse_type):
        sample = self.pulsed.sequencegeneratorlogic().analyze_block_ensemble(name)
        length_s = sample['ideal_length']/2
        self.timing_diagram = {}
        xrange = np.linspace(0, length_s, sample['number_of_samples']+1)
        under_sample = math.ceil(len(xrange)/100000)
        for ch in self.pulsed.digital_channels:
            rising = sample['digital_rising_bins'][ch]
            falling = sample['digital_falling_bins'][ch]
            y = np.zeros_like(xrange)
            if len(rising)==len(falling):
                for j in range(len(rising)):
                    y[rising[j]:falling[j]] = 0.3
            self.timing_diagram[ch] = (xrange[::under_sample], y[::under_sample])
        self.sigPulsedPlotUpdated.emit(self.timing_diagram)
