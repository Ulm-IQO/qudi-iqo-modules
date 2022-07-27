# -*- coding: utf-8 -*-

"""
This module controls spectrometers from Ocean Optics Inc.
All spectrometers supported by python-seabreeze should work.
Please visit https://python-seabreeze.readthedocs.io/en/latest/index.html for more information.

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

from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.interface.spectrometer_interface import SpectrometerInterface

import numpy as np
import seabreeze.spectrometers as sb


class OceanOptics(SpectrometerInterface):
    """ Hardware module for reading spectra from the Ocean Optics spectrometer software.

    Example config for copy-paste:

    myspectrometer:
        module.Class: 'spectrometer.oceanoptics_spectrometer.OceanOptics'
        options:
            spectrometer_serial: 'QEP01583' #insert here the right serial number.

    """
    _serial = ConfigOption(name='spectrometer_serial', default=None, missing='warn')
    _integration_time = StatusVar(name='integration_time', default=0.1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._spectrometer = None

    def on_activate(self):
        """ Activate module.
        """
        self.log.info(f'available spectrometers: {sb.list_devices()}')
        self._spectrometer = sb.Spectrometer.from_serial_number(self._serial)
        self.log.info(''.format(self._spectrometer.model, self._spectrometer.serial_number))
        self.exposure_time = self._integration_time
        self.log.info(f'Exposure set to {self._integration_time} seconds')

    def on_deactivate(self):
        """ Deactivate module.
        """
        self._spectrometer.close()

    def record_spectrum(self):
        """ Record spectrum from Ocean Optics spectrometer.

            @return []: spectrum data
        """
        wavelengths = self._spectrometer.wavelengths()
        specdata = np.empty((2, len(wavelengths)), dtype=np.double)
        specdata[0] = wavelengths / 1e9
        specdata[1] = self._spectrometer.intensities()
        return specdata

    @property
    def exposure_time(self):
        """ Get exposure.
            @return float: exposure time
            Not implemented.
        """
        return self._integration_time

    @exposure_time.setter
    def exposure_time(self, value):
        """ Set exposure.
            @param float value: exposure time in seconds
        """
        assert isinstance(value, (float, int)), f'exposure_time needs to be a float in seconds, but was {value}'
        self._integration_time = float(value)
        self._spectrometer.integration_time_micros(int(self._integration_time * 1e6))
