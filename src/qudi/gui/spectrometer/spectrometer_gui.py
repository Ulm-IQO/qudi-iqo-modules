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

import numpy as np

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util import units
from qudi.util.widgets.fitting import FitConfigurationDialog, FitWidget

from .spectrometer_window import SpectrometerMainWindow


class SpectrometerGui(GuiBase):
    """
    """

    # declare connectors
    spectrumlogic = Connector(interface='SpectrometerLogic')

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

        self._mw.plot_top_layout.addWidget(self._fit_widget)
        self._fit_widget.sigDoFit.connect(self.spectrumlogic().do_fit)

        self._mw.plot_top_layout.addWidget(self._mw.plot_widget)

        # Connect signals
        self.spectrumlogic().sig_data_updated.connect(self.update_data)
        self.spectrumlogic().sig_state_updated.connect(self.update_state)
        self.spectrumlogic().sig_spectrum_fit_updated.connect(self.update_fit)
        self.spectrumlogic().sig_fit_domain_updated.connect(self.update_fit_domain)

        self._mw.spectrum_button.clicked.connect(self.acquire_spectrum)
        self._mw.spectrum_continue_button.clicked.connect(self.continue_spectrum)
        self._mw.background_button.clicked.connect(self.acquire_background)
        self._mw.save_spectrum_button.clicked.connect(self.save_spectrum)
        self._mw.save_background_button.clicked.connect(self.save_background)
        self._mw.background_correction_switch.sigStateChanged.connect(self.background_correction_changed)
        self._mw.constant_acquisition_switch.sigStateChanged.connect(self.constant_acquisition_changed)
        self._mw.differential_spectrum_switch.sigStateChanged.connect(self.differential_spectrum_changed)

        self._mw.show()
        self.update_state()
        self.update_data()

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # disconnect signals
        self._mw.action_show_fit_settings.triggered.disconnect()
        self._fsd.close()
        self._fsd = None
        self._fit_widget.sigDoFit.disconnect()

        self.spectrumlogic().sig_data_updated.disconnect(self.update_data)
        self.spectrumlogic().sig_state_updated.disconnect(self.update_state)
        self.spectrumlogic().sig_spectrum_fit_updated.disconnect(self.update_fit)
        self.spectrumlogic().sig_fit_domain_updated.disconnect(self.update_fit_domain)

        self._mw.spectrum_button.clicked.disconnect()
        self._mw.spectrum_continue_button.clicked.disconnect()
        self._mw.background_button.clicked.disconnect()
        self._mw.save_spectrum_button.clicked.disconnect()
        self._mw.save_background_button.clicked.disconnect()
        self._mw.background_correction_switch.sigStateChanged.disconnect()
        self._mw.constant_acquisition_switch.sigStateChanged.disconnect()
        self._mw.differential_spectrum_switch.sigStateChanged.disconnect()

        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def update_state(self):
        if self.spectrumlogic().acquisition_running:
            self._mw.spectrum_button.setText('Stop Spectrum')
            self._mw.background_button.setText('Stop Background')
        else:
            self._mw.spectrum_button.setText('Acquire Spectrum')
            self._mw.background_button.setText('Acquire Background')
        self._mw.background_correction_switch.setChecked(self.spectrumlogic().background_correction)
        self._mw.constant_acquisition_switch.setChecked(self.spectrumlogic().constant_acquisition)
        self._mw.differential_spectrum_switch.setChecked(self.spectrumlogic().differential_spectrum)

    def update_data(self):
        """ The function that grabs the data and sends it to the plot.
        """
        # erase previous fit line
        self._mw.fit_curve.setData(x=[], y=[])

        # draw new data
        self._mw.data_curve.setData(x=self.spectrumlogic().wavelength,
                                    y=self.spectrumlogic().spectrum)

    def update_fit(self, fit_data, result_str_dict, current_fit):
        """ Update the drawn fit curve and displayed fit results.
        """
        if current_fit != 'No Fit':
            # display results as formatted text
            self._mw.spectrum_fit_results_DisplayWidget.clear()
            try:
                formated_results = units.create_formatted_output(result_str_dict)
            except:
                formated_results = 'this fit does not return formatted results'
            self._mw.spectrum_fit_results_DisplayWidget.setPlainText(formated_results)

            # redraw the fit curve in the GUI plot.
            self._curve2.setData(x=fit_data[0, :], y=fit_data[1, :])

    def acquire_spectrum(self):
        if not self.spectrumlogic().acquisition_running:
            self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()
            self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()
            self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()
            self.spectrumlogic().run_get_spectrum(background=False)
            self._mw.spectrum_button.setText('Stop Spectrum')
        else:
            self.spectrumlogic().stop()
            self._mw.spectrum_button.setText('Acquire Spectrum')

    def continue_spectrum(self):
        if not self.spectrumlogic().acquisition_running:
            self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()
            self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()
            self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()
            self.spectrumlogic().run_get_spectrum(background=False, reset=False)
            self._mw.spectrum_button.setText('Stop Spectrum')

    def acquire_background(self):
        if not self.spectrumlogic().acquisition_running:
            self.spectrumlogic().background_correction = self._mw.background_correction_switch.isChecked()
            self.spectrumlogic().constant_acquisition = self._mw.constant_acquisition_switch.isChecked()
            self.spectrumlogic().differential_spectrum = self._mw.differential_spectrum_switch.isChecked()
            self.spectrumlogic().run_get_spectrum(background=True)
            self._mw.spectrum_button.setText('Stop Background')
        else:
            self.spectrumlogic().stop()
            self._mw.spectrum_button.setText('Acquire Background')

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

    def set_fit_domain(self):
        """ Set the fit domain in the spectrum logic to values given by the GUI spinboxes.
        """
        lambda_min = self._mw.fit_domain_min_doubleSpinBox.value()
        lambda_max = self._mw.fit_domain_max_doubleSpinBox.value()

        new_fit_domain = np.array([lambda_min, lambda_max])

        self.spectrumlogic().set_fit_domain(new_fit_domain)

    def reset_fit_domain_all_data(self):
        """ Reset the fit domain to match the full data set.
        """
        self.spectrumlogic().set_fit_domain()

    def update_fit_domain(self, domain):
        """ Update the displayed fit domain to new values (set elsewhere).
        """
        self._mw.fit_domain_min_doubleSpinBox.setValue(domain[0])
        self._mw.fit_domain_max_doubleSpinBox.setValue(domain[1])
