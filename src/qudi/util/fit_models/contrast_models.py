# -*- coding: utf-8 -*-

"""
Re-parametrized versions of the qudi-core Sine, ExponentialDecaySine and (Double/Triple)
Lorentzian fit models, which fit the measurement contrast (and, for the sine models, the period)
directly. Because contrast and period are genuine fit parameters, the fit result reports them
including their fit errors.

The sine models fit the period and additionally report frequency = 1 / period (derived).

Contrast definitions (fit parameter "contrast", in percent of the bright signal level):
  - Sine models:        contrast = 100 * (max - min) / max of the oscillation
                        (for the decaying sine: of the undamped oscillation at x = 0).
  - Lorentzian dips:    contrast = 100 * dip depth / offset
  - Lorentzian peaks:   contrast = 100 * peak height / offset
Double/triple Lorentzian lines are numbered from low to high center.

The start values are taken from the corresponding qudi-core estimators and converted.

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

__all__ = ('SineContrast', 'ExponentialDecaySineContrast', 'LorentzianDipContrast',
           'LorentzianPeakContrast', 'DoubleLorentzianDipsContrast',
           'DoubleLorentzianPeaksContrast', 'TripleLorentzianDipsContrast',
           'TripleLorentzianPeaksContrast')

import numpy as np
from qudi.util.fit_models.model import FitModelBase, estimator
from qudi.util.fit_models.helpers import correct_offset_histogram, smooth_data, sort_check_data
from qudi.util.fit_models.sine import Sine, ExponentialDecaySine
from qudi.util.fit_models.lorentzian import Lorentzian, DoubleLorentzian, TripleLorentzian
from qudi.util.fit_models.lorentzian import multiple_lorentzian
from qudi.util.units import round_value_to_error

# Upper bound of the sine contrast (100 * (max - min) / max must stay below 200 %)
_MAX_SINE_CONTRAST_PERCENT = 199.9


def _sine_amplitude(offset, contrast):
    """ Sine amplitude A for a given contrast c = 2A / (offset + A). """
    c = contrast / 100
    return offset * c / (2 - c)


def _sine_contrast(offset, amplitude):
    """ Contrast c = 2A / (offset + A) in percent. """
    return 100 * 2 * amplitude / (offset + amplitude)


def _set_sine_contrast_params(estimate, core_estimate, x):
    """ Converts a qudi-core (decaying) sine estimate (amplitude, frequency) into contrast and
    period start values. All other parameters are copied as they are. """
    for name, param in core_estimate.items():
        if name in estimate and name not in ('amplitude', 'frequency'):
            estimate[name].set(value=param.value, min=param.min, max=param.max, vary=param.vary)

    offset = core_estimate['offset'].value
    amplitude = core_estimate['amplitude'].value
    contrast = _sine_contrast(offset, amplitude) if offset + amplitude != 0 else 0
    estimate['contrast'].set(value=float(np.clip(contrast, 0, _MAX_SINE_CONTRAST_PERCENT)),
                                     min=0, max=_MAX_SINE_CONTRAST_PERCENT, vary=True)

    x_span = abs(max(x) - min(x))
    x_step = min(abs(np.ediff1d(x)))
    frequency = max(core_estimate['frequency'].value, 1 / (10 * x_span))
    estimate['period'].set(value=1 / frequency, min=2 * x_step, max=np.inf, vary=True)
    return estimate


class SineContrast(FitModelBase):
    """ offset + A * sin(2 pi x / period + phase), with A = offset * c / (2 - c) and
    c = contrast / 100 = (max - min) / max.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('offset', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('contrast', value=0., min=0., max=_MAX_SINE_CONTRAST_PERCENT)
        self.set_param_hint('period', value=1., min=0., max=np.inf)
        self.set_param_hint('frequency', expr='1/period')
        self.set_param_hint('phase', value=0., min=-np.pi, max=np.pi)

    @staticmethod
    def _model_function(x, offset, contrast, period, phase):
        amplitude = _sine_amplitude(offset, contrast)
        return offset + amplitude * np.sin(2 * np.pi * x / period + phase)

    @estimator('default')
    def estimate(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_sine_contrast_params(self.make_params(), Sine().estimate(data, x), x)

    @estimator('Zero Phase')
    def estimate_zero_phase(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_sine_contrast_params(self.make_params(), Sine().estimate_zero_phase(data, x), x)


class ExponentialDecaySineContrast(FitModelBase):
    """ offset + A * exp(-(x / decay) ** stretch) * sin(2 pi x / period + phase), with
    A = offset * c / (2 - c) and c = contrast / 100 (contrast of the undamped
    oscillation at x = 0).
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('offset', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('contrast', value=0., min=0., max=_MAX_SINE_CONTRAST_PERCENT)
        self.set_param_hint('period', value=1., min=0., max=np.inf)
        self.set_param_hint('frequency', expr='1/period')
        self.set_param_hint('phase', value=0., min=-np.pi, max=np.pi)
        self.set_param_hint('decay', value=1., min=0., max=np.inf)
        self.set_param_hint('stretch', value=1., min=0., max=np.inf)

    @staticmethod
    def _model_function(x, offset, contrast, period, phase, decay, stretch):
        amplitude = _sine_amplitude(offset, contrast)
        return offset + amplitude * np.exp(-(x / decay) ** stretch) * np.sin(
            2 * np.pi * x / period + phase)

    @estimator('Decay')
    def estimate_decay(self, data, x):
        data, x = sort_check_data(data, x)
        core = ExponentialDecaySine().estimate_decay(data, x)
        return _set_sine_contrast_params(self.make_params(), core, x)

    @estimator('Stretched Decay')
    def estimate_stretched_decay(self, data, x):
        data, x = sort_check_data(data, x)
        core = ExponentialDecaySine().estimate_stretched_decay(data, x)
        return _set_sine_contrast_params(self.make_params(), core, x)


def _offset_estimate(data):
    """ Baseline estimate (most common value of the smoothed data), as used by the qudi-core
    Lorentzian estimators. """
    data_smoothed, filter_width = smooth_data(data)
    _, offset = correct_offset_histogram(data_smoothed, bin_width=2 * filter_width)
    return offset


def _set_lorentzian_contrast_params(estimate, core_estimate, data, n_lines, sign):
    """ Converts a qudi-core (multiple) Lorentzian estimate (signed amplitudes) into contrast
    start values, relative to the baseline offset. Centers and widths are copied. """
    suffixes = [''] if n_lines == 1 else ['_{0:d}'.format(ii) for ii in range(1, n_lines + 1)]

    offset_param = core_estimate['offset']
    offset = offset_param.value
    if n_lines > 1 or offset == 0:
        # The qudi-core double/triple estimators do not estimate the offset
        offset = _offset_estimate(data)
        data_span = abs(max(data) - min(data))
        estimate['offset'].set(value=offset, min=min(data) - data_span / 2,
                               max=max(data) + data_span / 2, vary=True)
    else:
        estimate['offset'].set(value=offset, min=offset_param.min, max=offset_param.max, vary=True)

    # Number the lines from low to high center (the qudi-core estimators order them by height)
    core_suffixes = sorted(suffixes, key=lambda sfx: core_estimate['center' + sfx].value)
    for suffix, core_suffix in zip(suffixes, core_suffixes):
        for name in ('center', 'sigma'):
            param = core_estimate[name + core_suffix]
            estimate[name + suffix].set(value=param.value, min=param.min, max=param.max, vary=True)
        amplitude = sign * core_estimate['amplitude' + core_suffix].value
        contrast = 100 * amplitude / offset if offset != 0 else 0
        upper = 100 if sign < 0 else np.inf
        estimate['contrast' + suffix].set(value=float(np.clip(contrast, 0, upper)),
                                          min=0, max=upper, vary=True)
    return estimate


class _LorentzianContrastHints:
    """ Mixin with the shared parameter hints of the single-line contrast Lorentzians. Not a
    FitModelBase subclass itself, so qudi does not list it as a (abstract) fit model. """
    _sign = 1

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('offset', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('contrast', value=0., min=0.,
                            max=100. if self._sign < 0 else np.inf)
        self.set_param_hint('center', value=0., min=-np.inf, max=np.inf)
        self.set_param_hint('sigma', value=0., min=0., max=np.inf)


class LorentzianDipContrast(_LorentzianContrastHints, FitModelBase):
    """ offset * (1 - contrast / 100 * L(x)), L normalized to 1 at the center,
    sigma = half width at half maximum.
    """
    _sign = -1

    @staticmethod
    def _model_function(x, offset, center, sigma, contrast):
        return offset * (1 - multiple_lorentzian(x, (center,), (sigma,), (contrast / 100,)))

    @estimator('Dip')
    def estimate_dip(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_lorentzian_contrast_params(
            self.make_params(), Lorentzian().estimate_dip(data, x), data, 1, -1)


class LorentzianPeakContrast(_LorentzianContrastHints, FitModelBase):
    """ offset * (1 + contrast / 100 * L(x)), L normalized to 1 at the center,
    sigma = half width at half maximum.
    """
    _sign = 1

    @staticmethod
    def _model_function(x, offset, center, sigma, contrast):
        return offset * (1 + multiple_lorentzian(x, (center,), (sigma,), (contrast / 100,)))

    @estimator('Peak')
    def estimate_peak(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_lorentzian_contrast_params(
            self.make_params(), Lorentzian().estimate_peak(data, x), data, 1, 1)


class _MultiLorentzianContrastHints:
    """ Mixin with the shared parameter hints of the double/triple contrast Lorentzians. Not a
    FitModelBase subclass itself, so qudi does not list it as a (abstract) fit model. """
    _sign = 1
    _n_lines = 2

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('offset', value=0., min=-np.inf, max=np.inf)
        for ii in range(1, self._n_lines + 1):
            self.set_param_hint('contrast_{0:d}'.format(ii), value=0., min=0.,
                                max=100. if self._sign < 0 else np.inf)
            self.set_param_hint('center_{0:d}'.format(ii), value=0., min=-np.inf, max=np.inf)
            self.set_param_hint('sigma_{0:d}'.format(ii), value=0., min=0., max=np.inf)


class DoubleLorentzianDipsContrast(_MultiLorentzianContrastHints, FitModelBase):
    """ offset * (1 - sum_i contrast_i / 100 * L_i(x)) """
    _sign = -1
    _n_lines = 2

    @staticmethod
    def _model_function(x, offset, center_1, center_2, sigma_1, sigma_2,
                        contrast_1, contrast_2):
        return offset * (1 - multiple_lorentzian(
            x, (center_1, center_2), (sigma_1, sigma_2),
            (contrast_1 / 100, contrast_2 / 100)))

    @estimator('Dips')
    def estimate_dips(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_lorentzian_contrast_params(
            self.make_params(), DoubleLorentzian().estimate_dips(data, x), data, 2, -1)


class DoubleLorentzianPeaksContrast(_MultiLorentzianContrastHints, FitModelBase):
    """ offset * (1 + sum_i contrast_i / 100 * L_i(x)) """
    _sign = 1
    _n_lines = 2

    @staticmethod
    def _model_function(x, offset, center_1, center_2, sigma_1, sigma_2,
                        contrast_1, contrast_2):
        return offset * (1 + multiple_lorentzian(
            x, (center_1, center_2), (sigma_1, sigma_2),
            (contrast_1 / 100, contrast_2 / 100)))

    @estimator('Peaks')
    def estimate_peaks(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_lorentzian_contrast_params(
            self.make_params(), DoubleLorentzian().estimate_peaks(data, x), data, 2, 1)


class TripleLorentzianDipsContrast(_MultiLorentzianContrastHints, FitModelBase):
    """ offset * (1 - sum_i contrast_i / 100 * L_i(x)) """
    _sign = -1
    _n_lines = 3

    @staticmethod
    def _model_function(x, offset, center_1, center_2, center_3, sigma_1, sigma_2, sigma_3,
                        contrast_1, contrast_2, contrast_3):
        return offset * (1 - multiple_lorentzian(
            x, (center_1, center_2, center_3), (sigma_1, sigma_2, sigma_3),
            (contrast_1 / 100, contrast_2 / 100, contrast_3 / 100)))

    @estimator('Dips')
    def estimate_dips(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_lorentzian_contrast_params(
            self.make_params(), TripleLorentzian().estimate_dips(data, x), data, 3, -1)


class TripleLorentzianPeaksContrast(_MultiLorentzianContrastHints, FitModelBase):
    """ offset * (1 + sum_i contrast_i / 100 * L_i(x)) """
    _sign = 1
    _n_lines = 3

    @staticmethod
    def _model_function(x, offset, center_1, center_2, center_3, sigma_1, sigma_2, sigma_3,
                        contrast_1, contrast_2, contrast_3):
        return offset * (1 + multiple_lorentzian(
            x, (center_1, center_2, center_3), (sigma_1, sigma_2, sigma_3),
            (contrast_1 / 100, contrast_2 / 100, contrast_3 / 100)))

    @estimator('Peaks')
    def estimate_peaks(self, data, x):
        data, x = sort_check_data(data, x)
        return _set_lorentzian_contrast_params(
            self.make_params(), TripleLorentzian().estimate_peaks(data, x), data, 3, 1)


def _contrast_formatted_output(original):
    """ Wraps qudi.util.units.create_formatted_output (used by FitContainer.formatted_result, i.e.
    by the fit widgets and the saved pulsed plots) for the parameters of the models above:
      - "contrast..." parameters are shown as plain percent, e.g. "(25.12 ± 0.08) %", instead of
        with an SI prefix chosen from the error size.
      - the derived "frequency" (= 1 / period) is shown with its error propagated from the period
        error, instead of as "(fixed)".
    Results of all other fit models do not contain these parameter names and are formatted by the
    original function unchanged.
    """
    def formatted_output(param_dict, *args, **kwargs):
        if not any(name.startswith('contrast') or name == 'period' for name in param_dict):
            return original(param_dict, *args, **kwargs)

        output = ''
        for name, entry in param_dict.items():
            entry = dict(entry)
            if name == 'frequency' and 'period' in param_dict and entry.get('error') is None:
                period = param_dict['period']['value']
                period_error = param_dict['period'].get('error')
                if period and period_error is not None:
                    entry['error'] = period_error / period ** 2
            error = entry.get('error')
            if (name.startswith('contrast') and error is not None and np.isfinite(error)
                    and error > 0):
                value, error, _ = round_value_to_error(entry['value'], error)
                output += '{0}: ({1} ± {2}) % \n'.format(name, value, error)
            else:
                output += original({name: entry}, *args, **kwargs)
        return output

    formatted_output._contrast_models_wrapper = True
    return formatted_output


def _install_contrast_formatted_output():
    """ Installs the wrapper above into qudi.util.datafitting (once). That module imports all fit
    model modules while it is being imported itself; its create_formatted_output reference
    already exists at that point, so this works whether or not the import is still running. """
    import qudi.util.datafitting as datafitting
    current = getattr(datafitting, 'create_formatted_output', None)
    if current is not None and not getattr(current, '_contrast_models_wrapper', False):
        datafitting.create_formatted_output = _contrast_formatted_output(current)


_install_contrast_formatted_output()
