# -*- coding: utf-8 -*-
"""
This module contains the data display and analysis widget for ScanningExcitationGui.
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

__all__ = ['ScanningExcitationDataWidget']

import pyqtgraph as pg
from PySide2 import QtCore
from PySide2 import QtWidgets

from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.widgets.toggle_switch import ToggleSwitch
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox
from qudi.util.widgets.fitting import FitWidget


class ScanningExcitationDataWidget(QtWidgets.QWidget):
    """
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        main_layout = QtWidgets.QGridLayout()
        self.setLayout(main_layout)

        fit_region_group_box = QtWidgets.QGroupBox('Fit Region')
        main_layout.addWidget(fit_region_group_box, 0, 0)
        fit_region_layout = QtWidgets.QGridLayout()
        fit_region_group_box.setLayout(fit_region_layout)
        from_label = QtWidgets.QLabel('From:')
        from_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        fit_region_layout.addWidget(from_label, 0, 0)
        to_label = QtWidgets.QLabel('To:')
        to_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        fit_region_layout.addWidget(to_label, 1, 0)
        scan_no_label = QtWidgets.QLabel('Scan no.:')
        scan_no_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        fit_region_layout.addWidget(scan_no_label, 2, 0)
        self.fit_region_from = ScienDSpinBox()
        self.fit_region_from.setMinimumWidth(100)
        fit_region_layout.addWidget(self.fit_region_from, 0, 1)
        self.fit_region_to = ScienDSpinBox()
        self.fit_region_to.setMinimumWidth(100)
        fit_region_layout.addWidget(self.fit_region_to, 1, 1)
        self.scan_no_fit = QtWidgets.QSpinBox()
        self.scan_no_fit.setMinimum(0)
        self.scan_no_fit.setMaximum(100)
        fit_region_layout.addWidget(self.scan_no_fit, 2, 1)

        target_group_box = QtWidgets.QGroupBox('Target')
        main_layout.addWidget(target_group_box, 0, 1)
        target_layout = QtWidgets.QGridLayout()
        target_group_box.setLayout(target_layout)
        x_label = QtWidgets.QLabel('X:')
        x_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        target_layout.addWidget(x_label, 0, 0)
        y_label = QtWidgets.QLabel('Y:')
        y_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        target_layout.addWidget(y_label, 1, 0)
        self.target_x = ScienDSpinBox()
        self.target_x.setDecimals(6, dynamic_precision=False)
        self.target_x.setMinimumWidth(100)
        target_layout.addWidget(self.target_x, 0, 1)
        self.target_y = ScienDSpinBox()
        self.target_y.setReadOnly(True)
        self.target_y.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.target_y.setDecimals(6, dynamic_precision=False)
        self.target_y.setMinimumWidth(100)
        target_layout.addWidget(self.target_y, 1, 1)

        laser_follow_cursor_label = QtWidgets.QLabel('Laser follow cursor:')
        laser_follow_cursor_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.laser_follow_cursor = ToggleSwitch(state_names=('No', 'Yes'))
        channel_input_label = QtWidgets.QLabel("Displayed input channel")
        self.channel_input_combo_box = QtWidgets.QComboBox()
        # main_layout.addWidget(axis_type_label, 1, 0)
        # main_layout.addWidget(self.axis_type, 1, 1)
        h_layout = QtWidgets.QHBoxLayout()
        h_layout.addWidget(laser_follow_cursor_label)
        h_layout.addWidget(self.laser_follow_cursor)
        h_layout.addWidget(channel_input_label)
        h_layout.addWidget(self.channel_input_combo_box)
        h_layout.addStretch()
        main_layout.addLayout(h_layout, 1, 0, 1, 2)

        self.fit_widget = FitWidget()
        main_layout.addWidget(self.fit_widget, 0, 2, 2, 1)

        self.plot_widget = pg.PlotWidget(
            axisItems={'bottom': CustomAxis(orientation='bottom'),
                       'left'  : CustomAxis(orientation='left')}
        )
        self.plot_widget.getAxis('bottom').nudge = 0
        self.plot_widget.getAxis('left').nudge = 0
        self.plot_widget.showGrid(x=True, y=True, alpha=0.5)

        # Create an empty plot curve to be filled later, set its pen
        self.data_curves = []
        self.add_curve()

        self.fit_curve = self.plot_widget.plot()
        self.fit_curve.setPen(palette.c2, width=2)

        self.fit_region = pg.LinearRegionItem(values=(0, 1),
                                              brush=pg.mkBrush(122, 122, 122, 30),
                                              hoverBrush=pg.mkBrush(196, 196, 196, 30))
        self.plot_widget.addItem(self.fit_region)

        self.target_point = pg.InfiniteLine(pos=0,
                                            angle=90,
                                            movable=True,
                                            pen=pg.mkPen(color='green', width=2))
        self.plot_widget.addItem(self.target_point)

        self.plot_widget.setLabel('left', 'Intensity', units='arb.u.')
        self.plot_widget.setMinimumHeight(300)
        self.plot_widget.setLabel('bottom', 'Frequency', units='Hz')
        self.target_x.setSuffix('Hz')
        self.fit_region_from.setSuffix('Hz')
        self.fit_region_to.setSuffix('Hz')

        main_layout.addWidget(self.plot_widget, 2, 0, 1, 3)
    def add_curve(self):
        self.data_curves.append(self.plot_widget.plot(symbol='o', symbolSize=5))
        self.data_curves[-1].setPen(palette.c1, width=2)


class CustomAxis(pg.AxisItem):
    """ This is a CustomAxis that extends the normal pyqtgraph to be able to nudge the axis labels.
    """

    @property
    def nudge(self):
        if not hasattr(self, "_nudge"):
            self._nudge = 5
        return self._nudge

    @nudge.setter
    def nudge(self, nudge):
        self._nudge = nudge
        s = self.size()
        # call resizeEvent indirectly
        self.resize(s + QtCore.QSizeF(1, 1))
        self.resize(s)

    def resizeEvent(self, ev=None):
        # Set the position of the label
        nudge = self.nudge
        br = self.label.boundingRect()
        p = QtCore.QPointF(0, 0)
        if self.orientation == "left":
            p.setY(int(self.size().height() / 2 + br.width() / 2))
            p.setX(-nudge)
        elif self.orientation == "right":
            p.setY(int(self.size().height() / 2 + br.width() / 2))
            p.setX(int(self.size().width() - br.height() + nudge))
        elif self.orientation == "top":
            p.setY(-nudge)
            p.setX(int(self.size().width() / 2.0 - br.width() / 2.0))
        elif self.orientation == "bottom":
            p.setX(int(self.size().width() / 2.0 - br.width() / 2.0))
            p.setY(int(self.size().height() - br.height() + nudge))
        self.label.setPos(p)
        self.picture = None


if __name__ == '__main__':
    import sys
    import os
    from qudi.util.paths import get_artwork_dir
    import qudi.core.application

    stylesheet_path = os.path.join(get_artwork_dir(), 'styles', 'qdark.qss')
    with open(stylesheet_path, 'r') as file:
        stylesheet = file.read()
    path = os.path.join(os.path.dirname(stylesheet_path), 'qdark').replace('\\', '/')
    stylesheet = stylesheet.replace('{qdark}', path)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(stylesheet)
    mw = QtWidgets.QMainWindow()
    widget = ScanningExcitationDataWidget()
    mw.setCentralWidget(widget)
    mw.show()
    sys.exit(app.exec_())

