# -*- coding: utf-8 -*-
"""
Scan control widget for laser scanning toolchain GUI.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['LaserScanControlWidget', 'LaserScanControlDockWidget']

from typing import Optional
from PySide2 import QtCore, QtWidgets, QtGui

from qudi.interface.scannable_laser_interface import LaserScanDirection
from qudi.gui.laserscanning.widgets.actions import LaserScanningActions


class LaserScanControlWidget(QtWidgets.QWidget):
    sigToggleDirection = QtCore.Signal(object)  # LaserScanDirection

    def __init__(self,
                 actions: LaserScanningActions,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self.start_stop_button = QtWidgets.QToolButton()
        self.start_stop_button.setDefaultAction(actions.action_start_stop_scan)
        self.start_stop_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)

        # Direction toggle
        self.direction_button = QtWidgets.QToolButton()
        self.direction_button.setCheckable(True)
        self.direction_button.setToolTip('Toggle scan direction (can be changed during scan).')

        self._icon_up = self.style().standardIcon(QtWidgets.QStyle.SP_ArrowUp)
        self._icon_down = self.style().standardIcon(QtWidgets.QStyle.SP_ArrowDown)

        self.direction_button.toggled.connect(self._direction_toggled)
        self.set_direction(LaserScanDirection.UP)  # default

        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(QtWidgets.QLabel('Scan:'))
        layout.addWidget(self.start_stop_button)
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel('Direction:'))
        layout.addWidget(self.direction_button)
        layout.addStretch(1)
        layout.setContentsMargins(4, 4, 4, 4)
        self.setLayout(layout)

    def set_direction(self, direction: LaserScanDirection) -> None:
        """Update button state from logic."""
        is_down = (direction == LaserScanDirection.DOWN)
        self.direction_button.blockSignals(True)
        self.direction_button.setChecked(is_down)
        self.direction_button.blockSignals(False)
        self._apply_direction_ui(direction)

    def _apply_direction_ui(self, direction: LaserScanDirection) -> None:
        if direction == LaserScanDirection.DOWN:
            self.direction_button.setIcon(self._icon_down)
            self.direction_button.setText('Down')
        else:
            self.direction_button.setIcon(self._icon_up)
            self.direction_button.setText('Up')

    def _direction_toggled(self, checked: bool) -> None:
        direction = LaserScanDirection.DOWN if checked else LaserScanDirection.UP
        self._apply_direction_ui(direction)
        self.sigToggleDirection.emit(direction)


class LaserScanControlDockWidget(QtWidgets.QDockWidget):
    def __init__(self, *args, actions: LaserScanningActions, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_widget = LaserScanControlWidget(actions=actions)
        self.setWidget(self.control_widget)
        self.control_widget.setFixedHeight(self.control_widget.sizeHint().height())

        # Re-export for convenience
        self.sigToggleDirection = self.control_widget.sigToggleDirection
        self.set_direction = self.control_widget.set_direction