# -*- coding: utf-8 -*-
"""
This module contains a GUI for operating the spectrum logic module.

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

__all__ = ['SpectrometerMainWindow']

import os
import importlib
from PySide2 import QtCore
from PySide2 import QtWidgets
from PySide2 import QtGui

from qudi.util.paths import get_artwork_dir
from qudi.util.widgets.advanced_dockwidget import AdvancedDockWidget
# Ensure specialized QMainWindow widget is reloaded as well when reloading this module
try:
    importlib.reload(settingsdialog)
except NameError:
    import qudi.gui.spectrometer.settingsdialog as settingsdialog
try:
    importlib.reload(control_widget)
except NameError:
    import qudi.gui.spectrometer.control_widget as control_widget
try:
    importlib.reload(data_widget)
except NameError:
    import qudi.gui.spectrometer.data_widget as data_widget


class SpectrometerMainWindow(QtWidgets.QMainWindow):
    """ Main Window for the SpectrometerGui module """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.setStyleSheet('border: 1px solid #f00;')  # debugging help for the gui
        self.setWindowTitle('qudi: Spectrometer')
        self.setDockNestingEnabled(True)
        self.setTabPosition(QtCore.Qt.AllDockWidgetAreas, QtWidgets.QTabWidget.North)
        icon_path = os.path.join(get_artwork_dir(), 'icons')

        # Create control central widget
        self.control_widget = control_widget.SpectrometerControlWidget()
        self.setCentralWidget(self.control_widget)

        # Create data dockwidget
        self.data_widget = data_widget.SpectrometerDataWidget()
        self.data_dockwidget = AdvancedDockWidget('Spectrometer Data', parent=self)
        self.data_dockwidget.setWidget(self.data_widget)

        # Create spectrometer settings dialog
        self.settings_dialog = settingsdialog.SettingsDialog()

        # Create QActions
        close_icon = QtGui.QIcon(os.path.join(icon_path, 'application-exit'))
        self.action_close = QtWidgets.QAction(icon=close_icon, text='Close Window', parent=self)

        restore_icon = QtGui.QIcon(os.path.join(icon_path, 'view-refresh'))
        self.action_restore_view = QtWidgets.QAction(icon=restore_icon, text='Restore', parent=self)
        self.action_restore_view.setToolTip('Restore the view to the default.')

        spec_setting_icon = QtGui.QIcon(os.path.join(icon_path, 'utilities-terminal'))
        self.action_spectrometer_settings = QtWidgets.QAction(icon=spec_setting_icon,
                                                              text='Show Spectrometer Settings',
                                                              parent=self)
        self.action_spectrometer_settings.setToolTip('Show the Spectrometer Settings.')

        fit_settings_icon = QtGui.QIcon(os.path.join(icon_path, 'configure'))
        self.action_show_fit_settings = QtWidgets.QAction(icon=fit_settings_icon,
                                                          text='Show Fit Settings',
                                                          parent=self)
        self.action_show_fit_settings.setToolTip('Show the Fit Settings.')

        save_spec_icon = QtGui.QIcon(os.path.join(icon_path, 'document-save'))
        self.action_save_spectrum = QtWidgets.QAction(icon=save_spec_icon,
                                                      text='Save Spectrum',
                                                      parent=self)
        self.action_save_spectrum.setToolTip('Save the currently shown spectrum.')

        save_back_icon = QtGui.QIcon(os.path.join(icon_path, 'document-save'))
        self.action_save_background = QtWidgets.QAction(icon=save_back_icon,
                                                        text='Save Background',
                                                        parent=self)
        self.action_save_background.setToolTip('Save the current background spectrum.')

        self.action_show_data = QtWidgets.QAction(text='Show Data', parent=self)
        self.action_show_data.setToolTip('Show/Hide spectrometer data plot and analysis dock.')
        self.action_show_data.setCheckable(True)
        self.action_show_data.setChecked(True)

        # Create the menu bar
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu('File')
        menu.addAction(self.action_save_spectrum)
        menu.addAction(self.action_save_background)
        menu.addSeparator()
        menu.addAction(self.action_close)
        menu = menu_bar.addMenu('View')
        menu.addAction(self.action_spectrometer_settings)
        menu.addAction(self.action_show_fit_settings)
        menu.addSeparator()
        menu.addAction(self.action_show_data)
        menu.addSeparator()
        menu.addAction(self.action_restore_view)
        self.setMenuBar(menu_bar)

        # connecting up the internal signals
        self.action_close.triggered.connect(self.close)
        self.action_restore_view.triggered.connect(self.restore_view)
        self.action_show_data.triggered[bool].connect(self.data_dockwidget.setVisible)
        self.data_dockwidget.sigClosed.connect(lambda: self.action_show_data.setChecked(False))
        self.action_spectrometer_settings.triggered.connect(self.settings_dialog.exec_)

        # Attach action to QToolButton
        self.control_widget.save_spectrum_button.setDefaultAction(self.action_save_spectrum)
        self.control_widget.save_background_button.setDefaultAction(self.action_save_background)

        self.restore_view()

    def restore_view(self):
        # Resize main window
        # screen_size = QtWidgets.QApplication.instance().desktop().availableGeometry(self).size()
        # self.resize(screen_size.width() // 3, screen_size.height() // 2)

        # Rearrange DockWidget
        if not self.action_show_data.isChecked():
            self.data_dockwidget.show()
        self.data_dockwidget.setFloating(False)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.data_dockwidget)
