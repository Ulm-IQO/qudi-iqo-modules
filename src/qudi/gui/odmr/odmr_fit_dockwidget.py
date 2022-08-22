# -*- coding: utf-8 -*-

"""
This file contains a custom QDockWidget subclass to be used in the ODMR GUI module.

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

__all__ = ('OdmrFitDockWidget',)

from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
from qudi.util.widgets.fitting import FitWidget


class OdmrFitDockWidget(AdvancedDockWidget):
    """
    """

    def __init__(self, *args, fit_container=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('ODMR Fit')
        self.setFeatures(self.DockWidgetFloatable | self.DockWidgetMovable)

        self.fit_widget = FitWidget(fit_container=fit_container)
        self.setWidget(self.fit_widget)
