# -*- coding: utf-8 -*-

"""
This file contains models and estimators for iqo fitting routines for qudi based on the lmfit package.

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

__all__ = ['N15Model', 'N14Model', 'SaturationModel', 'SaturationModelBackground']

import numpy as np
from qudi.util.fit_models.model import FitModelBase, estimator
from qudi.util.fit_models.lorentzian import DoubleLorentzian, TripleLorentzian

class N15Model(DoubleLorentzian):
    """
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @estimator('N15 dips')
    def estimate_N15_dips(self, data, x):
        estimate = self.estimate_dips(data, x)
        hf_splitting = 3.03e6
        test_shift = np.array([-1, 1]) * hf_splitting
        ref_str = '1' if abs(estimate['amplitude_1'].value) > abs(estimate['amplitude_2'].value) else '2'
        sum_res = np.zeros(len(test_shift))
        for ii, shift in enumerate(test_shift):
            lor_estimate = self._model_function(x, estimate['offset'].value,
                                                estimate['center_{}'.format(ref_str)].value,
                                                estimate['center_{}'.format(ref_str)].value + shift,
                                                estimate['sigma_{}'.format(ref_str)].value,
                                                estimate['sigma_{}'.format(ref_str)].value,
                                                estimate['amplitude_{}'.format(ref_str)].value,
                                                estimate['amplitude_{}'.format(ref_str)].value / 2)
            sum_res[ii] = np.abs(data - lor_estimate).sum()
        hf_shift = test_shift[sum_res.argmin()]

        estimate['center_1'].set(value=estimate['center_{}'.format(ref_str)].value,
                                 min=estimate['center_{}'.format(ref_str)].min,
                                 max=estimate['center_{}'.format(ref_str)].max)
        estimate['amplitude_1'].set(value=estimate['amplitude_{}'.format(ref_str)].value / 2,
                                    min=estimate['amplitude_{}'.format(ref_str)].min,
                                    max=estimate['amplitude_{}'.format(ref_str)].max)
        estimate['sigma_1'].set(value=estimate['sigma_{}'.format(ref_str)].value,
                                min=estimate['sigma_{}'.format(ref_str)].min,
                                max=estimate['sigma_{}'.format(ref_str)].max)

        estimate['center_2'].set(value=estimate['center_1'].value + hf_shift,
                                 min=estimate['center_1'].min + hf_shift,
                                 max=estimate['center_1'].max + hf_shift,
                                 expr='center_1+{0}'.format(hf_shift))
        estimate['amplitude_2'].set(value=estimate['amplitude_1'].value / 2,
                                    min=estimate['amplitude_1'].min,
                                    max=estimate['amplitude_1'].max)
        estimate['sigma_2'].set(value=estimate['sigma_1'].value,
                                min=estimate['sigma_1'].min,
                                max=estimate['sigma_1'].max,
                                expr='sigma_1')
        return estimate

    @estimator('N15 peaks')
    def estimate_N15_peaks(self, data, x):
        estimate = self.estimate_N15_dips(-data, x)
        estimate['offset'].set(value=-estimate['offset'].value,
                               min=-estimate['offset'].max,
                               max=-estimate['offset'].min)
        estimate['amplitude_1'].set(value=-estimate['amplitude_1'].value,
                                  min=-estimate['amplitude_1'].max,
                                  max=-estimate['amplitude_1'].min)
        estimate['amplitude_2'].set(value=-estimate['amplitude_2'].value,
                                    min=-estimate['amplitude_2'].max,
                                    max=-estimate['amplitude_2'].min)
        return estimate


class N14Model(TripleLorentzian):
    """
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @estimator('N14 dips')
    def estimate_N14_dips(self, data, x):
        estimate = self.estimate_dips(data, x)
        print(estimate)
        hf_splitting = 2.15e6
        test_shift = np.array([-1, 0, 1]) * hf_splitting
        ref_str = str(np.argmax([estimate['amplitude_1'].value, estimate['amplitude_2'].value,
                                 estimate['amplitude_3'].value]) + 1)
        sum_res = np.zeros(len(test_shift))
        for ii, shift in enumerate(test_shift):
            lor_estimate = self._model_function(x, estimate['offset'].value,
                                                estimate['center_{}'.format(ref_str)].value - hf_splitting + shift,
                                                estimate['center_{}'.format(ref_str)].value + shift,
                                                estimate['center_{}'.format(ref_str)].value + hf_splitting + shift,
                                                estimate['sigma_{}'.format(ref_str)].value,
                                                estimate['sigma_{}'.format(ref_str)].value,
                                                estimate['sigma_{}'.format(ref_str)].value,
                                                estimate['amplitude_{}'.format(ref_str)].value,
                                                estimate['amplitude_{}'.format(ref_str)].value,
                                                estimate['amplitude_{}'.format(ref_str)].value)
            sum_res[ii] = np.abs(data - lor_estimate).sum()
        print(sum_res)
        hf_shift = test_shift[sum_res.argmin()]

        estimate['center_1'].set(value=estimate['center_{}'.format(ref_str)].value - hf_splitting + hf_shift,
                                 min=estimate['center_{}'.format(ref_str)].min - hf_splitting + hf_shift,
                                 max=estimate['center_{}'.format(ref_str)].max - hf_splitting + hf_shift)
        estimate['amplitude_1'].set(value=estimate['amplitude_{}'.format(ref_str)].value,
                                    min=estimate['amplitude_{}'.format(ref_str)].min,
                                    max=estimate['amplitude_{}'.format(ref_str)].max)
        estimate['sigma_1'].set(value=estimate['sigma_{}'.format(ref_str)].value,
                                min=estimate['sigma_{}'.format(ref_str)].min,
                                max=estimate['sigma_{}'.format(ref_str)].max)

        estimate['center_2'].set(value=estimate['center_1'].value + hf_splitting,
                                 min=estimate['center_1'].min + hf_splitting,
                                 max=estimate['center_1'].max + hf_splitting,
                                 expr='center_1+{0}'.format(hf_splitting))
        estimate['amplitude_2'].set(value=estimate['amplitude_1'].value,
                                    min=estimate['amplitude_1'].min,
                                    max=estimate['amplitude_1'].max)
        estimate['sigma_2'].set(value=estimate['sigma_1'].value,
                                min=estimate['sigma_1'].min,
                                max=estimate['sigma_1'].max,
                                expr='sigma_1')

        estimate['center_3'].set(value=estimate['center_1'].value + 2 * hf_splitting,
                                 min=estimate['center_1'].min + 2 * hf_splitting,
                                 max=estimate['center_1'].max + 2 * hf_splitting,
                                 expr='center_1+{0}'.format(2 * hf_splitting))
        estimate['amplitude_3'].set(value=estimate['amplitude_1'].value,
                                    min=estimate['amplitude_1'].min,
                                    max=estimate['amplitude_1'].max)
        estimate['sigma_3'].set(value=estimate['sigma_1'].value,
                                min=estimate['sigma_1'].min,
                                max=estimate['sigma_1'].max,
                                expr='sigma_1')
        return estimate

    @estimator('N14 peaks')
    def estimate_N14_peaks(self, data, x):
        estimate = self.estimate_N14_dips(-data, x)
        estimate['offset'].set(value=-estimate['offset'].value,
                               min=-estimate['offset'].max,
                               max=-estimate['offset'].min)
        estimate['amplitude_1'].set(value=-estimate['amplitude_1'].value,
                                    min=-estimate['amplitude_1'].max,
                                    max=-estimate['amplitude_1'].min)
        estimate['amplitude_2'].set(value=-estimate['amplitude_2'].value,
                                    min=-estimate['amplitude_2'].max,
                                    max=-estimate['amplitude_2'].min)
        estimate['amplitude_3'].set(value=-estimate['amplitude_3'].value,
                                    min=-estimate['amplitude_3'].max,
                                    max=-estimate['amplitude_3'].min)
        return estimate


class SaturationModel(FitModelBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('amplitude', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('saturation', value=0., min=0., max=np.inf)
        self.set_param_hint('offset', value=0., min=0., max=np.inf)

    @staticmethod
    def _model_function(x, amplitude, saturation,  offset):
        return amplitude / saturation * x / (1 + x / saturation) + offset

    @estimator('Saturation')
    def estimate_saturation(self, data, x):

        estimate = self.make_params()
        estimate['amplitude'].set(value=np.abs(max(data)-min(data)), min=0.0, max=np.inf)
        estimate['saturation'].set(value=(x[-1]+x[0])/2, min=0, max=np.inf)
        estimate['offset'].set(value=min(data), min=0, max=np.inf, vary=True)

        return estimate


class SaturationModelBackground(FitModelBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('amplitude', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('saturation', value=0., min=0., max=np.inf)
        self.set_param_hint('offset', value=0., min=0., max=np.inf)
        self.set_param_hint('slope', value=0., min=0., max=np.inf)

    @staticmethod
    def _model_function(x, amplitude, saturation, slope, offset):
        return amplitude / saturation * x / (1 + x / saturation) + slope * x + offset

    @estimator('Saturation')
    def estimate_saturation(self, data, x):

        estimate = self.make_params()
        estimate['amplitude'].set(value=np.abs(max(data)-min(data)), min=0.0, max=np.inf)
        estimate['saturation'].set(value=(x[-1]+x[0])/2, min=0, max=np.inf)
        estimate['slope'].set(value=np.abs(max(data)-min(data))/np.abs(max(x)-min(x)), min=0, max=np.inf)
        estimate['offset'].set(value=min(data), min=min(data), max=max(data), vary=True)

        return estimate
