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

__all__ = ['N15Model']

import numpy as np
from qudi.util.fit_models.model import FitModelBase, estimator
from qudi.util.fit_models.lorentzian import DoubleLorentzian, TripleLorentzian

class N15Model(DoubleLorentzian):
    """
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @estimator('N15')
    def estimate_N15(self, data, x):
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
        hf_shift = test_shift[sum_res.argmax()]

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

class N14Model(TripleLorentzian):
    """
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @estimator('N14')
    def estimate_N14(self, data, x):
        # Todo
        estimate = self.estimate_dips(data, x)
        return estimate