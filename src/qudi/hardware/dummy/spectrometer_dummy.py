# -*- coding: utf-8 -*-
"""
This module contains fake spectrometer.

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

from qudi.interface.spectrometer_interface import SpectrometerInterface

from time import strftime, localtime

import time
import numpy as np


class SpectrometerDummy(SpectrometerInterface):
    """ Dummy spectrometer module.

    Shows a silicon vacancy spectrum at liquid helium temperatures.

    Example config for copy-paste:

    spectrometer_dummy:
        module.Class: 'spectrometer.spectrometer_dummy.SpectrometerInterfaceDummy'

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exposure = 0.5

    def on_activate(self):
        """ Activate module.
        """
        pass

    def on_deactivate(self):
        """ Deactivate module.
        """
        pass

    def record_spectrum(self):
        """ Record a dummy spectrum.

            @return ndarray: 1024-value ndarray containing wavelength and intensity of simulated spectrum
        """
        length = 1024

        data = np.empty((2, length), dtype=np.double)
        data[0] = np.arange(730, 750, 20 / length)
        data[1] = np.random.uniform(0, 2000, length)

        # lorentz, params = self._fitLogic.make_multiplelorentzian_model(no_of_functions=4)
        # sigma = 0.05
        # params.add('l0_amplitude', value=2000)
        # params.add('l0_center', value=736.46)
        # params.add('l0_sigma', value=1.5 * sigma)
        # params.add('l1_amplitude', value=5800)
        # params.add('l1_center', value=736.545)
        # params.add('l1_sigma', value=sigma)
        # params.add('l2_amplitude', value=7500)
        # params.add('l2_center', value=736.923)
        # params.add('l2_sigma', value=sigma)
        # params.add('l3_amplitude', value=1000)
        # params.add('l3_center', value=736.99)
        # params.add('l3_sigma', value=1.5 * sigma)
        # params.add('offset', value=50000.)
        #
        # data[1] += lorentz.eval(x=data[0], params=params)

        data[0] = data[0] * 1e-9  # return to logic in SI units (m)

        time.sleep(self.exposure_time)
        return data

    @property
    def exposure_time(self):
        """ Get exposure time.
        """
        return self._exposure

    @exposure_time.setter
    def exposure_time(self, value):
        """ Set exposure time.
        """
        self._exposure = float(value)
