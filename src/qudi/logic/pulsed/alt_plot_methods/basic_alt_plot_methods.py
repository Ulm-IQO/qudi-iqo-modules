# -*- coding: utf-8 -*-
"""
Basic alternative plot methods for the pulsed measurement analysis tab.

These reference implementations of ``AltPlotMethodBase`` reproduce the behaviour previously
hardcoded in ``PulsedMeasurementLogic._compute_alt_data``. To add a new alternative plot method,
copy the structure of one of the classes below into this package or the configured
``additional_alt_plot_path`` directory.

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

import numpy as np

from qudi.util.math import compute_ft
from qudi.logic.pulsed.pulse_alt_plot import AltPlotMethodBase


class FourierTransformAltPlot(AltPlotMethodBase):
    """ Fourier transform of the measured data trace(s). FFT parameters are exposed as configurable,
    persisted parameters via the keyword arguments of :meth:`compute`. """

    name = 'FFT'

    def compute(self, signal_data, zeropad=0, window='none', base_corr=True, psd=False):
        if signal_data.shape[1] < 2:
            return None

        fft_x, fft_y = compute_ft(x_val=signal_data[0],
                                  y_val=signal_data[1],
                                  zeropad_num=zeropad,
                                  window=window,
                                  base_corr=base_corr,
                                  psd=psd)

        alt_data = np.empty((len(signal_data), len(fft_x)), dtype=float)
        alt_data[0] = fft_x
        alt_data[1] = fft_y
        for dim in range(2, len(signal_data)):
            _, alt_data[dim] = compute_ft(x_val=signal_data[0],
                                          y_val=signal_data[dim],
                                          zeropad_num=zeropad,
                                          window=window,
                                          base_corr=base_corr,
                                          psd=psd)
        return alt_data

    # FFT swaps the x-axis to the inverse domain and renders the y-axis in arbitrary units.
    @property
    def x_axis_label(self):
        return 'FT {0}'.format(self.data_labels[0])

    @property
    def x_axis_unit(self):
        x_unit = self.data_units[0]
        if x_unit == 's':
            return 'Hz'
        elif x_unit == 'Hz':
            return 's'
        elif x_unit:
            return '(1/{0})'.format(x_unit)
        return ''

    @property
    def y_axis_label(self):
        return 'FT({0})'.format(self.data_labels[1])

    @property
    def y_axis_unit(self):
        return 'arb. u.'


class DeltaAltPlot(AltPlotMethodBase):
    """ Difference of the two traces of an alternating measurement (trace 1 - trace 2). Only
    available for alternating measurements; uses the default (primary) axis labels. """

    name = 'Delta'

    def compute(self, signal_data):
        if len(signal_data) != 3:
            return None
        alt_data = np.empty((2, signal_data.shape[1]), dtype=float)
        alt_data[0] = signal_data[0]
        alt_data[1] = signal_data[1] - signal_data[2]
        return alt_data

    def is_available(self):
        return self.is_alternating

