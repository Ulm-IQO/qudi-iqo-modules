# -*- coding: utf-8 -*-
"""
This file contains the Qudi logic class that captures and processes fluorescence spectra.

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

from PySide2 import QtCore
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import Mutex
from qudi.util.network import netobtain
from qudi.core.module import LogicBase
from qudi.util.datastorage import TextDataStorage
from qudi.util.datafitting import FitContainer, FitConfigurationsModel


class SpectrometerLogic(LogicBase):
    """This logic module gathers data from the spectrometer.

    Demo config:

    spectrumlogic:
        module.Class: 'spectrometer_logic.SpectrometerLogic'
        connect:
            spectrometer: 'myspectrometer'
            modulation_device: 'my_odmr'
    """

    # declare connectors
    spectrometer = Connector(interface='SpectrometerInterface')
    modulation_device = Connector(interface='ModulationInterface', optional=True)

    # declare status variables
    _spectrum_data = StatusVar(name='spectrum_data', default=np.empty((2, 0)))
    _spectrum_background = StatusVar(name='spectrum_background', default=np.empty((2, 0)))
    _background_correction = StatusVar(name='background_correction', default=False)
    _constant_acquisition = StatusVar(name='constant_acquisition', default=False)
    _differential_spectrum = StatusVar(name='differential_spectrum', default=False)

    _fit_config = StatusVar(name='fit_config', default=None)

    # Internal signals
    sig_data_updated = QtCore.Signal()

    # External signals eg for GUI module
    spectrum_fit_updated_Signal = QtCore.Signal(np.ndarray, dict, str)
    fit_domain_updated_Signal = QtCore.Signal(np.ndarray)

    def __init__(self, **kwargs):
        """ Create SpectrometerLogic object with connectors.

          @param dict kwargs: optional parameters
        """
        super().__init__(**kwargs)
        self._fit_config_model = None
        self._fit_container = None

        # locking for thread safety
        self._lock = Mutex()

        self._spectrum = [None, None]
        self._wavelength = None
        self._background = None
        self._repetitions = 0
        self._stop_acquisition = False

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._fit_config_model = FitConfigurationsModel(parent=self)
        self._fit_config_model.load_configs(self._fit_config)
        self._fit_container = FitContainer(parent=self, config_model=self._fit_config_model)

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        pass

    def stop(self):
        self._stop_acquisition = True

    def get_spectrum(self, background=False, constant_acquisition=None, differential_spectrum=None, reset=True):
        if constant_acquisition is not None:
            self._constant_acquisition = bool(constant_acquisition)
        if differential_spectrum is not None:
            self._differential_spectrum = bool(differential_spectrum)
            if self._differential_spectrum and not self.modulation_device.is_connected:
                self.log.warning(f'differential_spectrum was requested, but no modulation device was connected.')
                self._differential_spectrum = False
        self._stop_acquisition = False

        if background:
            self._background = np.array(netobtain(self.spectrometer().record_spectrum()))

        if reset:
            self._spectrum = [None, None]
            self._wavelength = None
            self._repetitions = 0

        if self._differential_spectrum:
            self.modulation_device().modulation_on()
            data = np.array(netobtain(self.spectrometer().record_spectrum()))
            if self._spectrum[0] is None:
                self._spectrum[0] = data[1, :]
            else:
                self._spectrum[0] += data[1, :]

            self.modulation_device().modulation_off()
            data = np.array(netobtain(self.spectrometer().record_spectrum()))
            if self._spectrum[1] is None:
                self._spectrum[1] = data[1, :]
            else:
                self._spectrum[1] += data[1, :]
        else:
            data = np.array(netobtain(self.spectrometer().record_spectrum()))
            if self._spectrum[0] is None:
                self._spectrum[0] = data[1, :]
            else:
                self._spectrum[0] += data[1, :]
            self._spectrum[1] = None

        self._wavelength = data[0, :]
        self._repetitions += 1
        self.sig_data_updated.emit()

        if self._constant_acquisition and not self._stop_acquisition:
            return self.get_spectrum(background=background,
                                     constant_acquisition=self._constant_acquisition,
                                     differential_spectrum=self._differential_spectrum,
                                     reset=False)
        return self.spectrum

    @property
    def spectrum(self):
        if self._spectrum[0] is None:
            return None
        data = self._spectrum[0]
        if self._differential_spectrum and self._spectrum[1] is not None:
            data = data - self._spectrum[1]
        if self._background_correction:
            if len(data[1, :]) == len(self._background):
                data = data - self._background
            else:
                self.log.warning(f'Length of spectrum ({len(data[1, :])}) does not match '
                                 f'background ({len(self._background[1, :])}, returning pure spectrum.')

        self.sig_data_updated.emit()
        return data

    @property
    def wavelength(self):
        return self._wavelength

    @property
    def repetitions(self):
        return self._repetitions

    @property
    def background_correction(self):
        return self._background_correction

    @background_correction.setter
    def background_correction(self, value):
        self._background_correction = bool(value)
        self.sig_data_updated.emit()

    @property
    def constant_acquisition(self):
        return self._constant_acquisition

    @constant_acquisition.setter
    def constant_acquisition(self, value):
        self._constant_acquisition = bool(value)

    @property
    def differential_spectrum(self):
        return self._differential_spectrum

    @differential_spectrum.setter
    def differential_spectrum(self, value):
        self._differential_spectrum = bool(value)

    def save_spectrum_data(self, background=False, name_tag='', root_dir=None, parameter=None):
        """ Saves the current spectrum data to a file.

        @param bool background: Whether this is a background spectrum (dark field) or not.
        @param string name_tag: postfix name tag for saved filename.
        @param string root_dir: overwrite the file position in necessary
        @param dict parameter: additional parameters to add to the saved file
        """

        timestamp = datetime.now()

        # write experimental parameters
        parameters = {'acquisition repetitions': self.repetitions,
                      'differential_spectrum': self.differential_spectrum,
                      'background_correction': self.background_correction,
                      'constant_acquisition': self.constant_acquisition}
        if parameter:
            parameters.update(parameter)

        # prepare the data
        if not background:
            data = [self.wavelength * 1e9, self.spectrum]
            file_label = 'spectrum' + name_tag
        else:
            data = [self.wavelength * 1e9, self.background]
            file_label = 'background' + name_tag

        header = ['Wavelength (nm)', 'Signal']

        if not background:
            # if background correction was on, also save the data without correction
            if self._background_correction:
                self._background_correction = False
                data.append(self.spectrum)
                self._background_correction = True
                header.append('Signal raw')

            # If the differential spectra arrays are not empty, save them as raw data
            if self._differential_spectrum and self._spectrum[1] is not None:
                data.append(self._spectrum[0])
                header.append('Signal ON')
                data.append(self._spectrum[1])
                header.append('Signal OFF')

        # save the date to file
        ds = TextDataStorage(root_dir=self.module_default_data_dir if root_dir is None else root_dir)

        file_path, _, _ = ds.save_data(np.array(data).T,
                                       column_headers=header,
                                       metadata=parameters,
                                       nametag=file_label,
                                       timestamp=timestamp,
                                       column_dtypes=[float] * len(header))

        # save the figure into a file
        figure, ax1 = plt.subplots()
        rescale_factor, prefix = self._get_si_scaling(np.max(data[1]))

        ax1.plot(data[0],
                 data[1] / rescale_factor,
                 linestyle=':',
                 linewidth=0.5
                 )

        ax1.set_xlabel('Wavelength (nm)')
        ax1.set_ylabel('Intensity ({}count)'.format(prefix))
        figure.tight_layout()

        ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])

        self.log.debug(f'Spectrum saved to:{file_path}')

    @staticmethod
    def _get_si_scaling(number):

        prefix = ['', 'k', 'M', 'G', 'T', 'P']
        prefix_index = 0
        rescale_factor = 1

        # Rescale spectrum data with SI prefix
        while number / rescale_factor > 1000:
            rescale_factor = rescale_factor * 1000
            prefix_index = prefix_index + 1

        intensity_prefix = prefix[prefix_index]
        return rescale_factor, intensity_prefix

    def save_raw_spectrometer_file(self, path='', postfix=''):
        """Ask the hardware device to save its own raw file.
        """
        # TODO: sanity check the passed parameters.
        self.spectrometer().save_spectrum(path, postfix=postfix)

    ################
    # Fitting things

    @_fit_config.representer
    def __repr_fit_config(self, value):
        config = self.fit_config_model.dump_configs()
        if not config or len(config) < 1:
            config = None
        return config

    @_fit_config.constructor
    def __constr_fit_config(self, value):
        if not value:
            return dict()
        return value

    @property
    def fit_config_model(self):
        return self._fit_config_model

    @property
    def fit_container(self):
        return self._fit_container

    def get_fit_functions(self):
        """ Return the hardware constraints/limits
        @return list(str): list of fit function names
        """
        return []

    def do_fit(self, fit_function=None, x_data=None, y_data=None):
        """
        Execute the currently configured fit on the measurement data. Optionally on passed data

        @param string fit_function: The name of one of the defined fit functions.

        @param array x_data: wavelength data for spectrum.

        @param array y_data: intensity data for spectrum.
        """
        self.log.warning('fitting not yet supported')
        return None

        if (x_data is None) or (y_data is None):
            x_data = self.spectrum_data[0]
            y_data = self.spectrum_data[1]
            if self.fit_domain.any():
                start_idx = self._find_nearest_idx(x_data, self.fit_domain[0])
                stop_idx = self._find_nearest_idx(x_data, self.fit_domain[1])

                x_data = x_data[start_idx:stop_idx]
                y_data = y_data[start_idx:stop_idx]

        if fit_function is not None and isinstance(fit_function, str):
            if fit_function in self.get_fit_functions():
                self.fc.set_current_fit(fit_function)
            else:
                self.fc.set_current_fit('No Fit')
                if fit_function != 'No Fit':
                    self.log.warning('Fit function "{0}" not available in Spectrum logic '
                                     'fit container.'.format(fit_function)
                                     )

        spectrum_fit_x, spectrum_fit_y, result = self.fc.do_fit(x_data, y_data)

        self.spectrum_fit = np.array([spectrum_fit_x, spectrum_fit_y])

        if result is None:
            result_str_dict = {}
        else:
            result_str_dict = result.result_str_dict
        self.spectrum_fit_updated_Signal.emit(self.spectrum_fit,
                                              result_str_dict,
                                              self.fc.current_fit
                                              )
        return

    def _find_nearest_idx(self, array, value):
        """ Find array index of element nearest to given value

        @param list array: array to be searched.
        @param float value: desired value.

        @return index of nearest element.
        """

        idx = (np.abs(array - value)).argmin()
        return idx

    def set_fit_domain(self, domain=None):
        """ Set the fit domain to a user specified portion of the data.

        If no domain is given, then this method sets the fit domain to match the full data domain.

        @param np.array domain: two-element array containing min and max of domain.
        """
        if domain is not None:
            self.fit_domain = domain
        else:
            self.fit_domain = np.array([self.spectrum_data[0, 0], self.spectrum_data[0, -1]])

        self.fit_domain_updated_Signal.emit(self.fit_domain)
