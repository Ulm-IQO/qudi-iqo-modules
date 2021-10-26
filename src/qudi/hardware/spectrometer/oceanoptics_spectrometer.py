# -*- coding: utf-8 -*-
"""
This module controls spectrometers from Ocean Optics Inc.
All spectrometers supported by python-seabreeze should work.

Do "conda install -c poehlmann python-seabreeze"
before using this module.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>

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
        spectrometer_serial: 'QEP01583' #insert here the right serial number.

    """
    _serial = ConfigOption('spectrometer_serial', missing='warn')
    _integration_time = StatusVar('integration_time', default=0.1)

    def on_activate(self):
        """ Activate module.
        """
        self.spec = sb.Spectrometer.from_serial_number(self._serial)
        self.log.info(''.format(self.spec.model, self.spec.serial_number))
        self.exposure_time = self._integration_time
        self.log.info(f'Exposure set to {self._integration_time} seconds')

    def on_deactivate(self):
        """ Deactivate module.
        """
        self.spec.close()

    def record_spectrum(self):
        """ Record spectrum from Ocean Optics spectrometer.

            @return []: spectrum data
        """
        wavelengths = self.spec.wavelengths()
        specdata = np.empty((2, len(wavelengths)), dtype=np.double)
        specdata[0] = wavelengths / 1e9
        specdata[1] = self.spec.intensities()
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
        self.spec.integration_time_micros(int(self._integration_time * 1e6))
