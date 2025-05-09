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

import numpy as np
from time import perf_counter
from PySide2 import QtCore

from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.widgets.fitting import FitConfigurationDialog, FitWidget
# Ensure specialized QMainWindow widget is reloaded as well when reloading this module
import qudi.gui.excitation_scanner.excitation_window as excitation_window


class ScanningExcitationGui(GuiBase):
    """ The GUI class for scanning excitation control.
    Example config for copy-paste:
    ```
      excitation_scanner_gui:
        module.Class: 'excitation_scanner.excitation_gui.ScanningExcitationGui'
        connect:
          excitation_logic: excitation_scanner_logic
    ```
    """

    # declare connectors
    _excitation_logic = Connector(name='excitation_logic', interface='ScanningExcitationLogic')

    # StatusVars
    _delete_fit = StatusVar(name='delete_fit', default=True)
    _target_x = StatusVar(name='target_x', default=0)

    # ConfigOptions
    _progress_poll_interval = ConfigOption(name='progress_poll_interval',
                                           default=1,
                                           missing='nothing')
    _status_poll_interval = ConfigOption(name='status_poll_interval',
                                           default=3,
                                           missing='nothing')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = excitation_window.ScanningExcitationMainWindow()
        self._fsd = None
        self._start_acquisition_timestamp = 0
        self._progress_timer = None
        self._status_update_timer = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """
        # process value for progress bar and poll timer
        self._start_acquisition_timestamp = 0
        self._progress_timer = QtCore.QTimer(parent=self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(round(1000 * self._progress_poll_interval))
        self._progress_timer.timeout.connect(self._update_progress_bar)
        self._status_update_timer = QtCore.QTimer(parent=self)
        self._status_update_timer.setSingleShot(False)
        self._status_update_timer.setInterval(round(1000 * self._status_poll_interval))
        self._status_update_timer.timeout.connect(self.update_all)

        # setting up the window
        self._mw = excitation_window.ScanningExcitationMainWindow()

        # Fit settings dialog
        self._fsd = FitConfigurationDialog(
            parent=self._mw,
            fit_config_model=self._excitation_logic().fit_config_model
        )
        self._mw.action_show_fit_settings.triggered.connect(self._fsd.show)

        # Link fit widget to logic
        self._mw.data_widget.fit_widget.link_fit_container(self._excitation_logic().fit_container)
        self._mw.data_widget.fit_widget.sigDoFit.connect(self._excitation_logic().do_fit)

        # fill initial settings
        self._mw.data_widget.target_point.setPos(self._target_x)
        self.populate_settings()
        self.update_state()
        self.update_data()
        available_channels = self._excitation_logic().available_channels()
        self._mw.data_widget.channel_input_combo_box.addItems(available_channels)

        # Connect signals
        self._excitation_logic().sig_data_updated.connect(self.update_data)
        self._excitation_logic().sig_state_updated.connect(self.update_state)
        self._excitation_logic().sig_scanner_variables_updated.connect(self.update_scanner_variables)
        self._excitation_logic().sig_fit_updated.connect(self.update_fit)
        self._excitation_logic().sig_scanner_state_updated.connect(self.update_scanner_status)
        #
        self._mw.control_widget.sig_toggle_acquisition.connect(self.acquire_spectrum)
        self._mw.control_widget.exposure_spinbox.valueChanged.connect(self.set_exposure)
        self._mw.control_widget.repetitions_spinbox.valueChanged.connect(self.set_repetitions)
        self._mw.control_widget.sig_variable_set.connect(self.set_variable)
        self._mw.control_widget.notes_text_input.textChanged.connect(self.set_notes)
        self._mw.action_save_spectrum.triggered.connect(self.save_spectrum)
        self._mw.data_widget.fit_region_from.editingFinished.connect(self.fit_region_value_changed)
        self._mw.data_widget.fit_region_to.editingFinished.connect(self.fit_region_value_changed)
        self._mw.data_widget.scan_no_fit.editingFinished.connect(self.fit_region_value_changed)
        self._mw.data_widget.target_x.editingFinished.connect(self.target_updated)
        self._mw.data_widget.channel_input_combo_box.currentTextChanged.connect(self._excitation_logic().set_display_channel_from_name)

        self._mw.data_widget.fit_region.sigRegionChangeFinished.connect(self.fit_region_changed)
        self._mw.data_widget.target_point.sigPositionChangeFinished.connect(self.target_changed)


        self._status_update_timer.start()

        # show the gui and update the data
        self.show()

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # Delete and disconnect timer
        self._progress_timer.timeout.disconnect()
        self._progress_timer.stop()
        self._progress_timer = None
        self._status_update_timer.timeout.disconnect()
        self._status_update_timer.stop()
        self._status_update_timer = None


        # clean up the fit
        self._mw.action_show_fit_settings.triggered.disconnect()
        self._fsd.close()
        self._fsd = None
        self._mw.data_widget.fit_widget.sigDoFit.disconnect()

        # disconnect signals
        self._excitation_logic().sig_data_updated.disconnect(self.update_data)
        self._excitation_logic().sig_state_updated.disconnect(self.update_state)
        self._excitation_logic().sig_scanner_variables_updated.disconnect(self.update_scanner_variables)
        self._excitation_logic().sig_fit_updated.disconnect(self.update_fit)


        self._mw.control_widget.acquire_button.clicked.disconnect()
        self._mw.control_widget.notes_text_input.textChanged.disconnect()
        self._mw.action_save_spectrum.triggered.disconnect()
        self._mw.data_widget.fit_region_from.editingFinished.disconnect()
        self._mw.data_widget.fit_region_to.editingFinished.disconnect()
        self._mw.data_widget.target_x.editingFinished.disconnect()

        self._mw.data_widget.channel_input_combo_box.currentTextChanged.disconnect()

        self._mw.data_widget.fit_region.sigRegionChangeFinished.disconnect()
        self._mw.data_widget.target_point.sigPositionChangeFinished.disconnect()

        self._mw.close()

    def show(self):
        """Make window visible and put it above all other windows.
        """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def update_scanner_status(self, st):
        self._mw.control_widget.status_label.setText(str(st))

    def update_all(self):
        self.update_state()
        self.update_data()
        self.update_scanner_variables()
        self.update_fit(self._excitation_logic().fit_method, self._excitation_logic().fit_results)

    def update_state(self):
        # Update the text of the buttons according to logic state
        if self._excitation_logic().acquisition_running:
            self._start_acquisition_timestamp = perf_counter()
            self._mw.control_widget.progress_bar.setValue(0)
            self._progress_timer.start()
            self._mw.control_widget.acquire_button.setText('Stop Spectrum')
        else:
            self._mw.control_widget.progress_bar.setValue(
                self._mw.control_widget.progress_bar.maximum()
            )
            self._progress_timer.stop()
            self._mw.control_widget.acquire_button.setText('Acquire Spectrum')

        # update settings shown by the gui
        self._mw.data_widget.fit_region.blockSignals(True)
        self._mw.data_widget.fit_region_from.blockSignals(True)
        self._mw.data_widget.fit_region_to.blockSignals(True)

        self._mw.data_widget.fit_region.setRegion(self._excitation_logic().fit_region)
        self._mw.data_widget.fit_region_from.setValue(self._excitation_logic().fit_region[0])
        self._mw.data_widget.fit_region_to.setValue(self._excitation_logic().fit_region[1])

        self._mw.data_widget.fit_region.blockSignals(False)
        self._mw.data_widget.fit_region_from.blockSignals(False)
        self._mw.data_widget.fit_region_to.blockSignals(False)

    def update_data(self):
        """ The function that grabs the data and sends it to the plot.
        """
        frequency = self._excitation_logic().frequency
        spectrum = self._excitation_logic().spectrum
        step_numbers = self._excitation_logic().step_number
        if frequency is None or spectrum is None:
            return
        l = min(len(frequency), len(spectrum), len(step_numbers))
        frequency = frequency[:l]
        spectrum = spectrum[:l]
        step_numbers = step_numbers[:l]
        all_steps = np.unique(step_numbers)

        # erase previous fit line
        if self._delete_fit:
            self._mw.data_widget.fit_curve.setData(x=[], y=[])

        self._target_x = self._excitation_logic().idle
        self._mw.data_widget.target_point.setPos(self._target_x)
        self.target_changed()
        missing_curves = len(all_steps) - len(self._mw.data_widget.data_curves) 
        if missing_curves > 0:
            for _ in range(missing_curves):
                self._mw.data_widget.add_curve()
        elif missing_curves < 0:
            for i in range(-missing_curves):
                self._mw.data_widget.data_curves[-(i+1)].setData(x=[], y=[])
        # draw new data
        for (i,step) in enumerate(all_steps):
            roi = step_numbers == step
            self._mw.data_widget.data_curves[i].setData(x=frequency[roi], y=spectrum[roi])

    def update_scanner_variables(self):
        variables = self._excitation_logic().variables
        self._mw.control_widget.update_variable_widgets(variables)
        if not self._mw.control_widget.notes_text_input.hasFocus():
            self._mw.control_widget.notes_text_input.blockSignals(True)
            self._mw.control_widget.notes_text_input.setPlainText(self._excitation_logic().notes)        
            self._mw.control_widget.notes_text_input.blockSignals(False)

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
        if not self._excitation_logic().acquisition_running:
            self._excitation_logic().get_spectrum()
        else:
            self._excitation_logic().stop()

    def save_spectrum(self):
        self._excitation_logic().save_spectrum_data()

    def fit_region_changed(self):
        self._excitation_logic().fit_region = self._mw.data_widget.fit_region.getRegion()

    def fit_region_value_changed(self):
        self._excitation_logic().fit_region = (self._mw.data_widget.fit_region_from.value(),
                                                 self._mw.data_widget.fit_region_to.value(),
                                                 self._mw.data_widget.scan_no_fit.value(),
                                                 )
    def set_exposure(self, v):
        self._excitation_logic().exposure_time = v
    def set_repetitions(self, v):
        self._excitation_logic().repetitions = v
    def set_variable(self, name, v):
        self.log.debug(f"gui got {name}: {v}")
        self._excitation_logic().set_variable(name, v)
    def set_notes(self):
        self._excitation_logic().notes = self._mw.control_widget.notes_text_input.toPlainText()

    def populate_settings(self):
        exposure_time = float(self._excitation_logic().exposure_time)
        repetitions = self._excitation_logic().repetitions
        self._mw.control_widget.exposure_spinbox.blockSignals(True)
        self._mw.control_widget.exposure_spinbox.setValue(exposure_time)
        self._mw.control_widget.exposure_spinbox.blockSignals(False)
        self._mw.control_widget.repetitions_spinbox.blockSignals(True)
        self._mw.control_widget.repetitions_spinbox.setValue(repetitions)
        self._mw.control_widget.repetitions_spinbox.blockSignals(False)
        self._mw.control_widget.progress_bar.setRange(0, round(100 * exposure_time * repetitions))
        variables = self._excitation_logic().variables
        self.log.debug(f"Creating variables {variables}")
        self._mw.control_widget.create_variable_widgets(variables)

    def target_changed(self):
        frequency = self._excitation_logic().frequency
        if frequency is None or len(frequency)==0:
            return
        self._target_x = self._mw.data_widget.target_point.pos()[0]

        if self._target_x < min(frequency):
            self._target_x = frequency[0]
        elif self._target_x > max(frequency):
            self._target_x = frequency[-1]

        new_y = self._excitation_logic().get_spectrum_at_x(self._target_x)
        self._mw.data_widget.target_x.setValue(self._target_x)
        self._mw.data_widget.target_y.setValue(new_y)

        self._mw.data_widget.target_point.setPos(self._target_x)
        if self._mw.data_widget.laser_follow_cursor.current_state == "Yes":
            self._excitation_logic().idle = self._target_x

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

