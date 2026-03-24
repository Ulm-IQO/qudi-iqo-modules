# -*- coding: utf-8 -*-
"""
Scan control widget for laser scanning toolchain GUI.
Holds Start/Stop scan and direction toggle.
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

        # Reuse existing QAction for scan start/stop so logic stays identical
        self.start_stop_button = QtWidgets.QToolButton()
        self.start_stop_button.setDefaultAction(actions.action_start_stop_scan)
        self.start_stop_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)

        # Direction toggle
        self.direction_button = QtWidgets.QToolButton()
        self.direction_button.setCheckable(True)
        self.direction_button.setToolTip('Toggle scan direction (can be changed during scan).')

        # Provide basic fallback text/icons; replace with your icon theme if desired
        self._icon_up = self.style().standardIcon(QtWidgets.QStyle.SP_ArrowUp)
        self._icon_down = self.style().standardIcon(QtWidgets.QStyle.SP_ArrowDown)

        self.direction_button.toggled.connect(self._direction_toggled)
        self.set_direction(LaserScanDirection.UP)  # default

        # Layout
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
        """Update button state from outside (logic)."""
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