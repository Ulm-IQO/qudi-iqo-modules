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

__all__ = ['SpectrometerGui']

import importlib
from time import perf_counter
from PySide2 import QtCore

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.widgets.fitting import FitConfigurationDialog, FitWidget
# Ensure specialized QMainWindow widget is reloaded as well when reloading this module
try:
    importlib.reload(spectrometer_window)
except NameError:
    import qudi.gui.spectrometer.spectrometer_window as spectrometer_window


class SpectrometerGui(GuiBase):
    # declare connectors
    _spectrometer_logic = Connector(name='spectrometer_logic', interface='SpectrometerLogic')

    # StatusVars
    _delete_fit = StatusVar(name='delete_fit', default=True)
    _target_x = StatusVar(name='target_x', default=0)

    # ConfigOptions
    _progress_poll_interval = ConfigOption(name='progress_poll_interval',
                                           default=1,
                                           missing='nothing')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._fsd = None
        self._start_acquisition_timestamp = 0
        self._progress_timer = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """
        # process value for progress bar and poll timer
        self._start_acquisition_timestamp = 0
        self._progress_timer = QtCore.QTimer(parent=self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(round(1000 * self._progress_poll_interval))
        self._progress_timer.timeout.connect(self._update_progress_bar)

        # setting up the window
        self._mw = spectrometer_window.SpectrometerMainWindow()

        # Fit settings dialog
        self._fsd = FitConfigurationDialog(
            parent=self._mw,
            fit_config_model=self._spectrometer_logic().fit_config_model
        )
        self._mw.action_show_fit_settings.triggered.connect(self._fsd.show)

        # Link fit widget to logic
        self._mw.data_widget.fit_widget.link_fit_container(self._spectrometer_logic().fit_container)
        self._mw.data_widget.fit_widget.sigDoFit.connect(self._spectrometer_logic().do_fit)

        # Connect signals
        self._spectrometer_logic().sig_data_updated.connect(self.update_data)
        self._spectrometer_logic().sig_state_updated.connect(self.update_state)
        self._spectrometer_logic().sig_fit_updated.connect(self.update_fit)

        self._mw.control_widget.acquire_button.clicked.connect(self.acquire_spectrum)
        self._mw.control_widget.spectrum_continue_button.clicked.connect(self.continue_spectrum)
        self._mw.control_widget.background_button.clicked.connect(self.acquire_background)
        self._mw.action_save_spectrum.triggered.connect(self.save_spectrum)
        self._mw.action_save_background.triggered.connect(self.save_background)
        self._mw.control_widget.background_correction_switch.sigStateChanged.connect(
            self.background_correction_changed
        )
        self._mw.control_widget.constant_acquisition_switch.sigStateChanged.connect(
            self.constant_acquisition_changed
        )
        self._mw.control_widget.differential_spectrum_switch.sigStateChanged.connect(
            self.differential_spectrum_changed
        )
        self._mw.data_widget.fit_region_from.editingFinished.connect(self.fit_region_value_changed)
        self._mw.data_widget.fit_region_to.editingFinished.connect(self.fit_region_value_changed)
        self._mw.data_widget.axis_type.sigStateChanged.connect(self.axis_type_changed)
        self._mw.data_widget.target_x.editingFinished.connect(self.target_updated)

        # Settings dialog
        self._mw.settings_dialog.accepted.connect(self.apply_settings)
        self._mw.settings_dialog.rejected.connect(self.keep_settings)

        self._mw.data_widget.fit_region.sigRegionChangeFinished.connect(self.fit_region_changed)
        self._mw.data_widget.target_point.sigPositionChangeFinished.connect(self.target_changed)

        # fill initial settings
        self._mw.data_widget.axis_type.setChecked(self._spectrometer_logic().axis_type_frequency)
        self._mw.data_widget.target_point.setPos(self._target_x)
        self.keep_settings()
        self.update_state()
        self.update_data()

        # show the gui and update the data
        self.show()

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # Delete and disconnect timer
        self._progress_timer.timeout.disconnect()
        self._progress_timer.stop()
        self._progress_timer = None

        # clean up the fit
        self._mw.action_show_fit_settings.triggered.disconnect()
        self._fsd.close()
        self._fsd = None
        self._mw.data_widget.fit_widget.sigDoFit.disconnect()

        # disconnect signals
        self._spectrometer_logic().sig_data_updated.disconnect(self.update_data)
        self._spectrometer_logic().sig_state_updated.disconnect(self.update_state)
        self._spectrometer_logic().sig_fit_updated.disconnect(self.update_fit)

        self._mw.control_widget.acquire_button.clicked.disconnect()
        self._mw.control_widget.spectrum_continue_button.clicked.disconnect()
        self._mw.control_widget.background_button.clicked.disconnect()
        self._mw.action_save_spectrum.triggered.disconnect()
        self._mw.action_save_background.triggered.disconnect()
        self._mw.control_widget.background_correction_switch.sigStateChanged.disconnect()
        self._mw.control_widget.constant_acquisition_switch.sigStateChanged.disconnect()
        self._mw.control_widget.differential_spectrum_switch.sigStateChanged.disconnect()
        self._mw.data_widget.fit_region_from.editingFinished.disconnect()
        self._mw.data_widget.fit_region_to.editingFinished.disconnect()
        self._mw.data_widget.target_x.editingFinished.disconnect()
        self._mw.data_widget.axis_type.sigStateChanged.disconnect()

        self._mw.data_widget.fit_region.sigRegionChangeFinished.disconnect()
        self._mw.data_widget.target_point.sigPositionChangeFinished.disconnect()
        self._mw.settings_dialog.accepted.disconnect()
        self._mw.settings_dialog.rejected.disconnect()

        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def update_state(self):
        # Update the text of the buttons according to logic state
        if self._spectrometer_logic().acquisition_running:
            self._start_acquisition_timestamp = perf_counter()
            self._mw.control_widget.progress_bar.setValue(0)
            self._progress_timer.start()
            self._mw.control_widget.acquire_button.setText('Stop Spectrum')
            self._mw.control_widget.background_button.setText('Stop Background')
        else:
            self._mw.control_widget.progress_bar.setValue(
                self._mw.control_widget.progress_bar.maximum()
            )
            self._progress_timer.stop()
            self._mw.control_widget.acquire_button.setText('Acquire Spectrum')
            self._mw.control_widget.background_button.setText('Acquire Background')

        # update settings shown by the gui
        self._mw.control_widget.background_correction_switch.blockSignals(True)
        self._mw.control_widget.constant_acquisition_switch.blockSignals(True)
        self._mw.data_widget.fit_region.blockSignals(True)
        self._mw.data_widget.fit_region_from.blockSignals(True)
        self._mw.data_widget.fit_region_to.blockSignals(True)

        self._mw.control_widget.background_correction_switch.setChecked(
            self._spectrometer_logic().background_correction
        )
        self._mw.control_widget.constant_acquisition_switch.setChecked(
            self._spectrometer_logic().constant_acquisition
        )
        self._mw.control_widget.spectrum_continue_button.setEnabled(
            self._spectrometer_logic().constant_acquisition
        )

        self._mw.data_widget.fit_region.setRegion(self._spectrometer_logic().fit_region)
        self._mw.data_widget.fit_region_from.setValue(self._spectrometer_logic().fit_region[0])
        self._mw.data_widget.fit_region_to.setValue(self._spectrometer_logic().fit_region[1])

        self._mw.control_widget.background_correction_switch.blockSignals(False)
        self._mw.control_widget.constant_acquisition_switch.blockSignals(False)
        self._mw.data_widget.fit_region.blockSignals(False)
        self._mw.data_widget.fit_region_from.blockSignals(False)
        self._mw.data_widget.fit_region_to.blockSignals(False)

        self._mw.control_widget.differential_spectrum_switch.blockSignals(True)
        self._mw.control_widget.differential_spectrum_switch.setEnabled(
            self._spectrometer_logic().differential_spectrum_available
        )
        self._mw.control_widget.differential_spectrum_switch.setChecked(
            self._spectrometer_logic().differential_spectrum
        )
        self._mw.control_widget.differential_spectrum_switch.blockSignals(False)

        if self._spectrometer_logic().axis_type_frequency:
            self._mw.data_widget.plot_widget.setLabel('bottom', 'Frequency', units='Hz')
            self._mw.data_widget.target_x.setSuffix('Hz')
            self._mw.data_widget.fit_region_from.setSuffix('Hz')
            self._mw.data_widget.fit_region_to.setSuffix('Hz')
        else:
            self._mw.data_widget.plot_widget.setLabel('bottom', 'Wavelength', units='m')
            self._mw.data_widget.target_x.setSuffix('m')
            self._mw.data_widget.fit_region_from.setSuffix('m')
            self._mw.data_widget.fit_region_to.setSuffix('m')

    def update_data(self):
        """ The function that grabs the data and sends it to the plot.
        """
        x_data = self._spectrometer_logic().x_data
        spectrum = self._spectrometer_logic().spectrum
        if x_data is None or spectrum is None:
            return

        # erase previous fit line
        if self._delete_fit:
            self._mw.data_widget.fit_curve.setData(x=[], y=[])

        self.target_changed()

        # draw new data
        self._mw.data_widget.data_curve.setData(x=x_data, y=spectrum)

    def update_fit(self, fit_method, fit_results):
        """ Update the drawn fit curve.
        """
        if fit_method != 'No Fit' and fit_results is not None:
            # redraw the fit curve in the GUI plot.
            self._mw.data_widget.fit_curve.setData(x=fit_results.high_res_best_fit[0],
                                                   y=fit_results.high_res_best_fit[1])
        else:
            self._mw.data_widget.fit_curve.setData(x=[], y=[])

    def acquire_spectrum(self):
        if not self._spectrometer_logic().acquisition_running:
            self._spectrometer_logic().background_correction = self._mw.control_widget.background_correction_switch.isChecked()
            self._spectrometer_logic().constant_acquisition = self._mw.control_widget.constant_acquisition_switch.isChecked()
            self._spectrometer_logic().differential_spectrum = self._mw.control_widget.differential_spectrum_switch.isChecked()
            self._spectrometer_logic().run_get_spectrum()
            self._mw.control_widget.acquire_button.setText('Stop Spectrum')
        else:
            self._spectrometer_logic().stop()
            self._mw.control_widget.acquire_button.setText('Acquire Spectrum')

    def continue_spectrum(self):
        if not self._spectrometer_logic().acquisition_running:
            self._spectrometer_logic().background_correction = self._mw.control_widget.background_correction_switch.isChecked()
            self._spectrometer_logic().constant_acquisition = self._mw.control_widget.constant_acquisition_switch.isChecked()
            self._spectrometer_logic().differential_spectrum = self._mw.control_widget.differential_spectrum_switch.isChecked()
            self._spectrometer_logic().run_get_spectrum(reset=False)
            self._mw.control_widget.acquire_button.setText('Stop Spectrum')

    def acquire_background(self):
        if not self._spectrometer_logic().acquisition_running:
            self._spectrometer_logic().background_correction = self._mw.control_widget.background_correction_switch.isChecked()
            self._spectrometer_logic().constant_acquisition = self._mw.control_widget.constant_acquisition_switch.isChecked()
            self._spectrometer_logic().differential_spectrum = self._mw.control_widget.differential_spectrum_switch.isChecked()
            self._spectrometer_logic().run_get_background()
            self._mw.control_widget.background_button.setText('Stop Background')
        else:
            self._spectrometer_logic().stop()
            self._mw.control_widget.background_button.setText('Acquire Background')

    def save_spectrum(self):
        self._spectrometer_logic().save_spectrum_data(background=False)

    def save_background(self):
        self._spectrometer_logic().save_spectrum_data(background=True)

    def background_correction_changed(self):
        self._spectrometer_logic().background_correction = self._mw.control_widget.background_correction_switch.isChecked()

    def constant_acquisition_changed(self):
        self._spectrometer_logic().constant_acquisition = self._mw.control_widget.constant_acquisition_switch.isChecked()

    def differential_spectrum_changed(self):
        self._spectrometer_logic().differential_spectrum = self._mw.control_widget.differential_spectrum_switch.isChecked()

    def fit_region_changed(self):
        self._spectrometer_logic().fit_region = self._mw.data_widget.fit_region.getRegion()

    def fit_region_value_changed(self):
        self._spectrometer_logic().fit_region = (self._mw.data_widget.fit_region_from.value(),
                                                 self._mw.data_widget.fit_region_to.value())

    def axis_type_changed(self):
        self._spectrometer_logic().axis_type_frequency = self._mw.data_widget.axis_type.isChecked()

    def apply_settings(self):
        exposure_time = self._mw.settings_dialog.exposure_time_spinbox.value()
        self._spectrometer_logic().exposure_time = exposure_time
        max_repetitions = self._mw.settings_dialog.max_repetitions_spinbox.value()
        self._spectrometer_logic().max_repetitions = max_repetitions
        self._mw.control_widget.progress_bar.setValue(0)
        self._mw.control_widget.progress_bar.setRange(0, round(100 * exposure_time))
        self._delete_fit = self._mw.settings_dialog.delete_fit.isChecked()

    def keep_settings(self):
        exposure_time = float(self._spectrometer_logic().exposure_time)
        self._mw.settings_dialog.exposure_time_spinbox.setValue(round(exposure_time))
        self._mw.settings_dialog.max_repetitions_spinbox.setValue(self._spectrometer_logic().max_repetitions)
        self._mw.control_widget.progress_bar.setRange(0, round(100 * exposure_time))
        self._mw.settings_dialog.delete_fit.setChecked(self._delete_fit)

    def target_changed(self):
        x_data = self._spectrometer_logic().x_data
        if x_data is None:
            return
        start_index = -1 if self._spectrometer_logic().axis_type_frequency else 0
        end_index = 0 if self._spectrometer_logic().axis_type_frequency else -1
        self._target_x = self._mw.data_widget.target_point.pos()[0]

        if self._target_x < min(x_data):
            self._target_x = x_data[start_index]
        elif self._target_x > max(x_data):
            self._target_x = x_data[end_index]

        new_y = self._spectrometer_logic().get_spectrum_at_x(self._target_x)
        self._mw.data_widget.target_x.setValue(self._target_x)
        self._mw.data_widget.target_y.setValue(new_y)

        self._mw.data_widget.target_point.setPos(self._target_x)

    def target_updated(self):
        self._target_x = self._mw.data_widget.target_x.value()
        self._mw.data_widget.target_point.setPos(self._target_x)
        self.target_changed()

    @QtCore.Slot()
    def _update_progress_bar(self) -> None:
        progress_bar = self._mw.control_widget.progress_bar
        max_ticks = progress_bar.maximum()
        if progress_bar.value() < max_ticks:
            elapsed_time_ticks = round(100 * (perf_counter() - self._start_acquisition_timestamp))
            progress_bar.setValue(elapsed_time_ticks)
            self._progress_timer.start()
