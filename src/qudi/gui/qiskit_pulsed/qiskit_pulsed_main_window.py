# -*- coding: utf-8 -*-

"""
This file contains the main window of the Qiskit pulsed GUI, with its actions and the pulse
timeline plot.

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

__all__ = ('PulseTimelineWidget', 'QiskitPulsedMainWindow')

import os
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.paths import get_artwork_dir


class PulseTimelineWidget(pg.GraphicsLayoutWidget):
    """
    Laser and microwave timeline of one repetition of the generated asset. Microwave pulses are
    drawn with their real envelope, which comes with the element description from the logic.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.laser_plot = self.addPlot(row=0, col=0)
        self.mw_plot = self.addPlot(row=1, col=0)
        # The gates last tens of nanoseconds inside a repetition of several microseconds, so they
        # get a third panel that shows only the gate window.
        self.gate_plot = self.addPlot(row=2, col=0)
        for plot, label in ((self.laser_plot, 'Laser'), (self.mw_plot, 'Microwave'), (self.gate_plot, 'Gates (zoom)')):
            plot.setLabel('left', label)
            plot.setMouseEnabled(x=True, y=False)
            plot.setYRange(-0.05, 1.15)
            plot.showGrid(x=True, y=True, alpha=0.2)
        self.mw_plot.setLabel('bottom', 'Time', units='s')
        self.gate_plot.setLabel('bottom', 'Time', units='s')
        self.mw_plot.setXLink(self.laser_plot)
        self.laser_plot.getAxis('bottom').setStyle(showValues=False)

        mw_pen = pg.mkPen(palette.c3, width=1)
        mw_brush = pg.mkBrush(*palette.c3.getRgb()[:3], 100)
        self.laser_curve = self.laser_plot.plot(
            pen=pg.mkPen(palette.c1, width=1), fillLevel=0.0, brush=pg.mkBrush(*palette.c1.getRgb()[:3], 100)
        )
        self.mw_curve = self.mw_plot.plot(pen=mw_pen, fillLevel=0.0, brush=mw_brush)
        self.gate_curve = self.gate_plot.plot(pen=mw_pen, fillLevel=0.0, brush=mw_brush)

    def set_elements(self, elements):
        """
        Draw a list of element descriptions, as provided by QiskitPulsedLogic.asset_summary.

        @param list elements: dicts with 'duration', 'laser', 'mw' and 'envelope' entries
        """
        if not elements:
            for curve in (self.laser_curve, self.mw_curve, self.gate_curve):
                curve.setData(x=[], y=[])
            return
        laser_x, laser_y, mw_x, mw_y = list(), list(), list(), list()
        gate_start, gate_stop = None, None
        time = 0.0
        for element in elements:
            start, stop = time, time + float(element['duration'])
            laser_x += [start, stop]
            laser_y += [1.0 if element['laser'] else 0.0] * 2
            envelope = element.get('envelope') or list()
            if element['mw'] and envelope:
                mw_x += [start] + list(np.linspace(start, stop, len(envelope))) + [stop]
                mw_y += [0.0] + [float(value) for value in envelope] + [0.0]
                gate_start = start if gate_start is None else gate_start
                gate_stop = stop
            else:
                mw_x += [start, stop]
                mw_y += [0.0, 0.0]
            time = stop
        self.laser_curve.setData(x=laser_x, y=laser_y)
        self.mw_curve.setData(x=mw_x, y=mw_y)
        self.laser_plot.setXRange(0.0, time, padding=0.02)
        if gate_start is None:
            self.gate_curve.setData(x=[], y=[])
            self.gate_plot.setXRange(0.0, time, padding=0.02)
        else:
            self.gate_curve.setData(x=mw_x, y=mw_y)
            margin = max(0.1 * (gate_stop - gate_start), 5e-9)
            self.gate_plot.setXRange(max(0.0, gate_start - margin), gate_stop + margin, padding=0.0)


class QiskitPulsedMainWindow(QtWidgets.QMainWindow):
    """The main window for the Qiskit pulsed GUI"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('qudi: Qiskit Pulsed')
        # Create central plot widget
        self.setCentralWidget(PulseTimelineWidget())
        # Create status bar
        self.setStatusBar(QtWidgets.QStatusBar())

        # Create QActions
        icon_path = os.path.join(get_artwork_dir(), 'icons')

        self.action_generate = QtGui.QAction('Generate')
        self.action_generate.setIcon(QtGui.QIcon(os.path.join(icon_path, 'document-new')))
        self.action_generate.setToolTip('Compile the selected circuit into a pulse block ensemble.')

        self.action_sample_load = QtGui.QAction('Sample && Load')
        self.action_sample_load.setIcon(QtGui.QIcon(os.path.join(icon_path, 'network-connect')))
        self.action_sample_load.setToolTip('Sample the generated ensemble and load it into the pulse generator.')

        self.action_generate_sample_load = QtGui.QAction('Generate, Sample && Load')
        self.action_generate_sample_load.setIcon(QtGui.QIcon(os.path.join(icon_path, 'go-home')))
        self.action_generate_sample_load.setToolTip(
            'Compile the selected circuit, sample it and load it into the pulse generator in one go.'
        )

        icon = QtGui.QIcon(os.path.join(icon_path, 'media-playback-start'))
        icon.addFile(os.path.join(icon_path, 'media-playback-stop'), state=QtGui.QIcon.State.On)
        self.action_toggle_pulser = QtGui.QAction('Run')
        self.action_toggle_pulser.setCheckable(True)
        self.action_toggle_pulser.setIcon(icon)
        self.action_toggle_pulser.setToolTip('Start or stop the pulse generator output.')

        self.action_restore_default_view = QtGui.QAction('Restore Default')

        self.action_close = QtGui.QAction('Close')
        self.action_close.setIcon(QtGui.QIcon(os.path.join(icon_path, 'application-exit')))

        # Create toolbar and add actions
        toolbar = QtWidgets.QToolBar()
        toolbar.setObjectName('Qiskit Pulsed Toolbar')
        toolbar.addAction(self.action_generate)
        toolbar.addAction(self.action_sample_load)
        toolbar.addAction(self.action_generate_sample_load)
        toolbar.addSeparator()
        tool_button = QtWidgets.QToolButton()
        tool_button.setDefaultAction(self.action_toggle_pulser)
        tool_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addWidget(tool_button)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)

        # Create menu bar and add actions
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu('File')
        menu.addAction(self.action_generate)
        menu.addAction(self.action_sample_load)
        menu.addAction(self.action_generate_sample_load)
        menu.addSeparator()
        menu.addAction(self.action_toggle_pulser)
        menu.addSeparator()
        menu.addAction(self.action_close)
        menu = menu_bar.addMenu('View')
        menu.addAction(self.action_restore_default_view)
        self.setMenuBar(menu_bar)

        # Connecting the close action
        self.action_close.triggered.connect(self.close)

    @property
    def timeline_widget(self) -> PulseTimelineWidget:
        """The timeline plot in the center of the window."""
        return self.centralWidget()
