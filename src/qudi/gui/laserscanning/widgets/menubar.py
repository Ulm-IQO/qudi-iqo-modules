# -*- coding: utf-8 -*-
"""
Contains the QMenuBar for the laser scanning toolchain GUI.

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

__all__ = ['LaserScanningMenuBar']

from typing import Optional
from PySide2 import QtWidgets

from qudi.gui.laserscanning.widgets.actions import LaserScanningActions


class LaserScanningMenuBar(QtWidgets.QMenuBar):
    """ QMenuBar specialization for laser scanning toolchain GUI """
    def __init__(self, actions: LaserScanningActions, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        menu = self.addMenu('File')
        menu.addAction(actions.action_start_stop_record)
        menu.addAction(actions.action_start_stop_scan)
        menu.addSeparator()
        menu.addAction(actions.action_clear_data)
        menu.addAction(actions.action_autoscale_histogram)
        menu.addSeparator()
        menu.addAction(actions.action_save)
        menu.addSeparator()
        menu.addAction(actions.action_close)
        menu = self.addMenu('Settings')
        menu.addAction(actions.action_laser_only)
        menu.addAction(actions.action_show_frequency)
        menu.addSeparator()
        menu.addAction(actions.action_show_fit_configuration)
        menu = self.addMenu('View')

        menu.addAction(actions.action_show_histogram_region)
        menu.addSeparator()
        menu.addAction(actions.action_restore_view)
