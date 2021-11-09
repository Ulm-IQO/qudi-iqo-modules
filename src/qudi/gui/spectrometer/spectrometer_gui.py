# -*- coding: utf-8 -*-
"""
This module contains a GUI for operating the spectrum logic module.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.widgets.fitting import FitConfigurationDialog, FitWidget

from .spectrometer_window import SpectrometerMainWindow


class SpectrometerGui(GuiBase):
    # declare connectors
    spectrumlogic = Connector(interface='SpectrometerLogic')

    # StatusVars
    _delete_fit = StatusVar(name='delete_fit', default=True)
    _target_x = StatusVar(name='target_x', default=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._fsd = None
        self._fit_widget = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        # setting up the window
        self._mw = SpectrometerMainWindow()

        # Fit settings dialogs
        self._fsd = FitConfigurationDialog(parent=self._mw,
                                           fit_config_model=self.spectrumlogic().fit_config_model)
        self._mw.action_show_fit_settings.triggered.connect(self._fsd.show)

        self._fit_widget = FitWidget(fit_container=self.spectrumlogic().fit_container)
        self._mw.fit_layout.addWidget(self._fit_widget, 0, 2, 2, 1)

        self._fit_widget.sigDoFit.connect(self.spectrumlogic().do_fit)

        # Connect signals
        self.spectrumlogic().sig_data_updated.connect(self.update_data)
        self.spectrumlogic().sig_state_updated.connect(self.update_state)
        self.spectrumlogic().sig_fit_updated.connect(self.update_fit)

        self._mw.spectrum_button.clicked.connect(self.acquire_spectrum)
        self._mw.spectrum_continue_button.clicked.connect(self.continue_spectrum)
        self._mw.background_button.clicked.connect(self.acquire_background)
        self._mw.save_spectrum_button.clicked.connect(self.save_spectrum)
        self._mw.save_background_button.clicked.connect(self.save_background)
        self._mw.background_correction_switch.sigStateChanged.connect(self.background_correction_changed)
        self._mw.constant_acquisition_switch.sigStateChanged.connect(self.constant_acquisition_changed)
        self._mw.differential_spectrum_switch.sigStateChanged.connect(self.differential_spectrum_changed)
        self._mw.fit_region_from.editingFinished.connect(self.fit_region_value_changed)
        self._mw.fit_region_to.editingFinished.connect(self.fit_region_value_changed)
        self._mw.axis_type.sigStateChanged.connect(self.axis_type_changed)
        self._mw.target_x.editingFinished.connect(self.target_updated)

        # Settings dialog
        self._mw.settings_dialog.accepted.connect(self.apply_settings)
        self._mw.settings_dialog.rejected.connect(self.keep_settings)

        self._mw.fit_region.sigRegionChangeFinished.connect(self.fit_region_changed)
        self._mw.target_point.sigPositionChangeFinished.connect(self.target_changed)

        # fill initial settings
        self._mw.axis_type.setChecked(self.spectrumlogic().axis_type_frequency)
        self._mw.target_x.setValue(self._target_x)
        self.target_updated()
        self.keep_settings()

        # show the gui and update the data
        self._mw.show()
        self.update_state()
        self.update_data()

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # clean up the fit
        self._mw.action_show_fit_settings.triggered.disconnect()
        self._fsd.close()
        self._fsd = None
        self._fit_widget.sigDoFit.disconnect()

        # disconnect signals
        self.spectrumlogic().sig_data_updated.disconnect(self.update_data)
        self.spectrumlogic().sig_state_updated.disconnect(self.update_state)
        self.spectrumlogic().sig_fit_updated.disconnect(self.update_fit)

        self._mw.spectrum_button.clicked.disconnect()
        self._mw.spectrum_continue_button.clicked.disconnect()
        self._mw.background_button.clicked.disconnect()
        self._mw.save_spectrum_button.clicked.disconnect()
        self._mw.save_background_button.clicked.disconnect()
        self._mw.background_correction_switch.sigStateChanged.disconnect()
        self._mw.constant_acquisition_switch.sigStateChanged.disconnect()
        self._mw.differential_spectrum_switch.sigStateChanged.disconnect()
        self._mw.fit_region_from.editingFinished.disconnect()
        self._mw.fit_region_to.editingFinished.disconnect()
        self._mw.target_x.editingFinished.disconnect()
        self._mw.axis_type.sigStateChanged.disconnect()

        self._mw.fit_region.sigRegionChangeFinished.disconnect()
        self._mw.target_point.sigPositionChangeFinished.disconnect()
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
        if self.spectrumlogic().acquisition_running:
            self._mw.spectrum_button.setText('Stop Spectrum')
            self._mw.background_button.setText('Stop Background')
        else:
            self._mw.spectrum_button.setText('Acquire Spectrum')
            self._mw.background_button.setText('Acquire Background')

        # update settings shown by the gui
        self._mw.background_correction_switch.blockSignals(True)
        self._mw.constant_acquisition_switch.blockSignals(True)
        self._mw.differential_spectrum_switch.blockSignals(True)
        self._mw.fit_region.blockSignals(True)
        self._mw.fit_region_from.blockSignals(True)
        self._mw.fit_region_to.blockSignals(True)

        self._mw.background_correction_switch.setChecked(self.spectrumlogic().background_correction)
        self._mw.constant_acquisition_switch.setChecked(self.spectrumlogic().constant_acquisition)
        self._mw.differential_spectrum_switch.setChecked(self.spectrumlogic().differential_spectrum)

        self._mw.fit_region.setRegion(self.spectrumlogic().fit_region)
        self._mw.fit_region_from.setValue(self.spectrumlogic().fit_region[0])
        self._mw.fit_region_to.setValue(self.spectrumlogic().fit_region[1])

        self._mw.background_correction_switch.blockSignals(False)
        self._mw.constant_acquisition_switch.blockSignals(False)
        self._mw.differential_spectrum_switch.blockSignals(False)
        self._mw.fit_region.blockSignals(False)
        self._mw.fit_region_from.blockSignals(False)
        self._mw.fit_region_to.blockSignals(False)

        if self.spectrumlogic().axis_type_frequency:
            self._mw.plot_widget.setLabel('bottom', 'Frequency', units='Hz')
            self._mw.target_x.setSuffix('Hz')
            self._mw.fit_region_from.setSuffix('Hz')
            self._mw.fit_region_to.setSuffix('Hz')
        else:
            self._mw.plot_widget.setLabel('bottom', 'Wavelength', units='m')
            self._mw.target_x.setSuffix('m')
            self._mw.fit_region_from.setSuffix('m')
            self._mw.fit_region_to.setSuffix('m')

    def update_data(self):
        """ The function that grabs the data and sends it to the plot.
        """
        x_data = self.spectrumlogic().x_data
        spectrum = self.spectrumlogic().spectrum
        if x_data is None or spectrum is None:
            return

        # erase previous fit line
        if self._delete_fit:
            self._mw.fit_curve.setData(x=[], y=[])

        self.target_changed()

        # draw new data
        self._mw.data_curve.setData(x=x_data,
                                    y=spectrum)

    def update_fit(self, fit_method, fit_results):
        """ Update the drawn fit curve.
        """
        if fit_method != 'No Fit' and fit_results is not None:
            # redraw the fit curve in the GUI plot.
            self._mw.fit_curve.setData(x=fit_results.high_res_best_fit[0],
                                       y=fit_results.high_res_best_fit[1])
        else:
            self._mw.fit_curve.setData(x=[], y=[])

    def acquire_spectrum(self):
        if not self.spectrumlogic().acquisition_running:
            self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()
            self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()
            self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()
            self.spectrumlogic().run_get_spectrum()
            self._mw.spectrum_button.setText('Stop Spectrum')
        else:
            self.spectrumlogic().stop()
            self._mw.spectrum_button.setText('Acquire Spectrum')

    def continue_spectrum(self):
        if not self.spectrumlogic().acquisition_running:
            self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()
            self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()
            self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()
            self.spectrumlogic().run_get_spectrum(reset=False)
            self._mw.spectrum_button.setText('Stop Spectrum')

    def acquire_background(self):
        if not self.spectrumlogic().acquisition_running:
            self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()
            self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()
            self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()
            self.spectrumlogic().run_get_background()
            self._mw.background_button.setText('Stop Background')
        else:
            self.spectrumlogic().stop()
            self._mw.background_button.setText('Acquire Background')

    def save_spectrum(self):
        self.spectrumlogic().save_spectrum_data(background=False)

    def save_background(self):
        self.spectrumlogic().save_spectrum_data(background=True)

    def background_correction_changed(self):
        self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()

    def constant_acquisition_changed(self):
        self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()

    def differential_spectrum_changed(self):
        self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()

    def fit_region_changed(self):
        self.spectrumlogic().fit_region = self._mw.fit_region.getRegion()

    def fit_region_value_changed(self):
        self.spectrumlogic().fit_region = (self._mw.fit_region_from.value(), self._mw.fit_region_to.value())

    def axis_type_changed(self):
        self.spectrumlogic().axis_type_frequency = self._mw.axis_type.isChecked()

    def apply_settings(self):
        self.spectrumlogic().exposure_time = self._mw.settings_dialog.exposure_time_spinbox.value()
        self._delete_fit = self._mw.settings_dialog.delete_fit.isChecked()

    def keep_settings(self):
        self._mw.settings_dialog.exposure_time_spinbox.setValue(self.spectrumlogic().exposure_time)
        self._mw.settings_dialog.delete_fit.setChecked(self._delete_fit)

    def target_changed(self):
        x_data = self.spectrumlogic().x_data
        start_index = -1 if self.spectrumlogic().axis_type_frequency else 0
        end_index = 0 if self.spectrumlogic().axis_type_frequency else -1
        position = self._mw.target_point.pos()
        self._target_x = position[0]

        if self._target_x < min(x_data):
            new_x = x_data[start_index]
        elif self._target_x > max(x_data):
            new_x = x_data[end_index]

        new_y = self.spectrumlogic().get_spectrum_at_x(self._target_x)
        self._mw.target_x.setValue(self._target_x)
        self._mw.target_y.setValue(new_y)

        self._mw.target_point.setPos(self._target_x)

    def target_updated(self):
        self._target_x = self._mw.target_x.value()
        self._mw.target_point.setPos(self._target_x)
        self.target_changed()
