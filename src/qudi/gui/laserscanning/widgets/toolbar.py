# -*- coding: utf-8 -*-
"""
Contains the QToolBar for the laser scanning toolchain GUI.

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

__all__ = ['LaserScanningToolBar']

from typing import Optional
from PySide2 import QtWidgets

from qudi.gui.laserscanning.widgets.actions import LaserScanningActions

class LaserScanningToolBar(QtWidgets.QToolBar):
    """ QToolBar specialization for laser scanning toolchain GUI """
    def __init__(self, actions: LaserScanningActions, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self.setMovable(False)
        self.setFloatable(False)

        self.save_tag_line_edit = QtWidgets.QLineEdit()
        self.save_tag_line_edit.setMaximumWidth(400)
        self.save_tag_line_edit.setMinimumWidth(150)
        self.save_tag_line_edit.setToolTip('Enter a nametag which will be added to the filename.')

        self.addAction(actions.action_start_stop_record)
        self.addAction(actions.action_start_stop_scan)
        self.addAction(actions.action_clear_data)
        self.addAction(actions.action_autoscale_histogram)
        self.addAction(actions.action_show_all_data)
        self.addSeparator()
        self.addAction(actions.action_save)
        self.addWidget(self.save_tag_line_edit)
        self.addSeparator()
        self.addAction(actions.action_show_frequency)
        self.addAction(actions.action_laser_only)
