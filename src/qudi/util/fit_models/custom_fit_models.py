# -*- coding: utf-8 -*-

"""
This file contains models of Gaussian fitting routines for qudi based on the lmfit package.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-core/>

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
__all__ = ('NegativeSincSquared')

import numpy as np
from qudi.util.fit_models.model import FitModelBase, estimator
from qudi.util.fit_models.helpers import correct_offset_histogram, smooth_data, sort_check_data
from qudi.util.fit_models.helpers import estimate_double_peaks, estimate_triple_peaks
from qudi.util.fit_models.linear import Linear

def negative_sinc_squared(x, amplitude, center, width, offset):
    """
    Definition of a negative sinc^2 function.

    :param float x: The independent variable
    :param float amplitude: The amplitude of the sinc function
    :param float center: The center position of the sinc function
    :param float width: The width parameter affecting the oscillation frequency
    :param float offset: The vertical offset
    :return: Negative sinc^2 function value
    """
    if width <= 0:
        return np.full_like(x, np.nan)

        # Calculate sinc^2 with error handling
    with np.errstate(divide='ignore', invalid='ignore'):
        sinc_part = np.sinc((x - center) / width)
        result = offset - amplitude * sinc_part ** 2
        result[np.isnan(result)] = np.nan

    return result


class NegativeSincSquared(FitModelBase):
    """
    Fitting model for a negative sinc function.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('offset', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('amplitude', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('center', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('width', value=0., min=0., max=np.inf)

    @staticmethod
    def _model_function(x, offset, amplitude, center, width):
        return negative_sinc_squared(x, offset, amplitude, center, width)
    @estimator('Dip')
    def estimate_dip(self, data, x):
        data, x = sort_check_data(data, x)
        # Smooth data
        filter_width = max(1, int(round(len(x) / 20)))
        data_smoothed, _ = smooth_data(data, filter_width)
        data_smoothed, offset = correct_offset_histogram(data_smoothed, bin_width=10*filter_width)

        # determine peak position
        center = x[np.argmin(data_smoothed)]

        # calculate amplitude
        amplitude = abs(min(data_smoothed))

        # according to the derived formula, calculate sigma. The crucial part is here that the
        # offset was estimated correctly, then the area under the curve is calculated correctly:
        numerical_integral = np.trapz(data_smoothed - offset, x)
        # width (using approx. for sinc-squared fwhm: 2*sqrt(3))
        width = abs(numerical_integral / (2 * np.sqrt(3) * amplitude))
        print(width)
        x_spacing = min(abs(np.ediff1d(x)))

        x_span = abs(x[-1] - x[0])
        data_span = abs(max(data) - min(data))

        estimate = self.make_params()
        estimate['offset'].set(value=offset, min=min(data) - data_span / 2, max=max(data) + data_span / 2)
        estimate['amplitude'].set(value=amplitude, min=0, max=amplitude)
        estimate['center'].set(value=center, min=min(x) - x_span, max=max(x) + x_span )
        estimate['width'].set(value=width, min=x_spacing, max=x_span)
        print(estimate)
        return estimate

    @estimator('Peak')
    def estimate_peak(self, data, x):
        estimate = self.estimate_dip(-data, x)
        estimate['offset'].set(value=-estimate['offset'].value,
                               min=-estimate['offset'].max,
                               max=-estimate['offset'].min)
        estimate['amplitude'].set(value=-estimate['amplitude'].value,
                                  min=-estimate['amplitude'].max,
                                  max=-estimate['amplitude'].min)
        print(estimate)
        return estimate