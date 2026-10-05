# -*- coding: utf-8 -*-

"""
Versions of the qudi-core Sine, ExponentialDecaySine and (Double/Triple) Lorentzian fit models
that fit exactly like the originals (same functions, same fit parameters, same estimators) and
additionally report derived quantities with their propagated fit errors:

  - Sine models:        period   = 1 / frequency
                        contrast = 100 * (max - min) / max of the oscillation, in percent
                                   (for the decaying sine: of the undamped oscillation at x = 0)
  - Lorentzian dips:    contrast = 100 * dip depth / offset, in percent
  - Lorentzian peaks:   contrast = 100 * peak height / offset, in percent

Double/triple Lorentzian estimators additionally estimate the baseline offset (the qudi-core
estimators start at offset 0) and number the lines from low to high center.

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
from qudi.util.fit_models.model import estimator
from qudi.util.fit_models.helpers import correct_offset_histogram, smooth_data, sort_check_data
from qudi.util.fit_models.sine import Sine, ExponentialDecaySine
from qudi.util.fit_models.lorentzian import Lorentzian, DoubleLorentzian, TripleLorentzian
from qudi.util.units import round_value_to_error

# lmfit propagates the fit errors into derived (expr) parameters, but qudi's result formatting
# shows every non-varied parameter as "(fixed)" without error. The models below therefore record
# these errors after each fit, keyed by (name, value), for _contrast_formatted_output to use.
_DERIVED_ERRORS = dict()
_MAX_DERIVED_ERRORS = 1000


class _DerivedErrors:
    """ Mixin recording the propagated errors of derived parameters after each fit. Not a
    FitModelBase subclass itself, so qudi does not list it as a fit model. """

    def fit(self, *args, **kwargs):
        result = super().fit(*args, **kwargs)
        for name, param in result.params.items():
            if (param.expr is not None and param.stderr is not None
                    and np.isfinite(param.stderr)):
                _DERIVED_ERRORS[(name, float(param.value))] = float(param.stderr)
        while len(_DERIVED_ERRORS) > _MAX_DERIVED_ERRORS:
            _DERIVED_ERRORS.pop(next(iter(_DERIVED_ERRORS)))
        return result


# Derived parameters are also evaluated at the default parameter values (e.g. frequency = 0,
# offset = 0) by the GUI fit configuration dialog, so every division is guarded to give 0
# instead of raising ZeroDivisionError.
_PERIOD_EXPR = '1/frequency if frequency != 0 else 0'
# Sine contrast (max - min) / max = 2A / (offset + A), in percent
_SINE_CONTRAST_EXPR = '200*amplitude/(offset+amplitude) if offset+amplitude != 0 else 0'


class SineContrast(_DerivedErrors, Sine):
    """ qudi-core Sine, additionally reporting period and contrast. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('period', expr=_PERIOD_EXPR)
        self.set_param_hint('contrast', expr=_SINE_CONTRAST_EXPR)

    @estimator('default')
    def estimate(self, data, x):
        return Sine.estimate(self, data, x)

    @estimator('Zero Phase')
    def estimate_zero_phase(self, data, x):
        return Sine.estimate_zero_phase(self, data, x)


class ExponentialDecaySineContrast(_DerivedErrors, ExponentialDecaySine):
    """ qudi-core ExponentialDecaySine, additionally reporting period and contrast. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('period', expr=_PERIOD_EXPR)
        self.set_param_hint('contrast', expr=_SINE_CONTRAST_EXPR)

    @estimator('Decay')
    def estimate_decay(self, data, x):
        return ExponentialDecaySine.estimate_decay(self, data, x)

    @estimator('Stretched Decay')
    def estimate_stretched_decay(self, data, x):
        return ExponentialDecaySine.estimate_stretched_decay(self, data, x)


def _contrast_expr(amplitude_name, sign):
    """ Lorentzian contrast in percent of the offset. sign = -1 for dips (negative amplitudes). """
    return '{0}100*{1}/offset if offset != 0 else 0'.format('-' if sign < 0 else '', amplitude_name)


def _improve_multi_lorentzian_estimate(params, data, n_lines):
    """ The qudi-core double/triple Lorentzian estimators leave the offset at 0 and number the
    lines by height. Sets a baseline estimate (most common value of the smoothed data, as in the
    single Lorentzian estimator) and renumbers the lines from low to high center. """
    data_smoothed, filter_width = smooth_data(data)
    _, offset = correct_offset_histogram(data_smoothed, bin_width=2 * filter_width)
    data_span = abs(max(data) - min(data))
    params['offset'].set(value=offset, min=min(data) - data_span / 2,
                         max=max(data) + data_span / 2, vary=True)

    suffixes = ['_{0:d}'.format(ii) for ii in range(1, n_lines + 1)]
    names = ('center', 'sigma', 'amplitude')
    old = {(name, sfx): (params[name + sfx].value, params[name + sfx].min,
                         params[name + sfx].max) for name in names for sfx in suffixes}
    ordered = sorted(suffixes, key=lambda sfx: old[('center', sfx)][0])
    for new_sfx, old_sfx in zip(suffixes, ordered):
        for name in names:
            value, vmin, vmax = old[(name, old_sfx)]
            params[name + new_sfx].set(value=value, min=vmin, max=vmax, vary=True)
    return params


class LorentzianDipContrast(_DerivedErrors, Lorentzian):
    """ qudi-core Lorentzian (dip estimator), additionally reporting the dip contrast. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('contrast', expr=_contrast_expr('amplitude', -1))

    @estimator('Dip')
    def estimate_dip(self, data, x):
        return Lorentzian.estimate_dip(self, data, x)


class LorentzianPeakContrast(_DerivedErrors, Lorentzian):
    """ qudi-core Lorentzian (peak estimator), additionally reporting the peak contrast. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_param_hint('contrast', expr=_contrast_expr('amplitude', 1))

    @estimator('Peak')
    def estimate_peak(self, data, x):
        return Lorentzian.estimate_peak(self, data, x)


class DoubleLorentzianDipsContrast(_DerivedErrors, DoubleLorentzian):
    """ qudi-core DoubleLorentzian (dips estimator), additionally reporting the dip contrasts. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for ii in (1, 2):
            self.set_param_hint('contrast_{0:d}'.format(ii),
                                expr=_contrast_expr('amplitude_{0:d}'.format(ii), -1))

    @estimator('Dips')
    def estimate_dips(self, data, x):
        data, x = sort_check_data(data, x)
        return _improve_multi_lorentzian_estimate(
            DoubleLorentzian.estimate_dips(self, data, x), data, 2)


class DoubleLorentzianPeaksContrast(_DerivedErrors, DoubleLorentzian):
    """ qudi-core DoubleLorentzian (peaks estimator), additionally reporting the peak contrasts. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for ii in (1, 2):
            self.set_param_hint('contrast_{0:d}'.format(ii),
                                expr=_contrast_expr('amplitude_{0:d}'.format(ii), 1))

    @estimator('Peaks')
    def estimate_peaks(self, data, x):
        data, x = sort_check_data(data, x)
        return _improve_multi_lorentzian_estimate(
            DoubleLorentzian.estimate_peaks(self, data, x), data, 2)


class TripleLorentzianDipsContrast(_DerivedErrors, TripleLorentzian):
    """ qudi-core TripleLorentzian (dips estimator), additionally reporting the dip contrasts. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for ii in (1, 2, 3):
            self.set_param_hint('contrast_{0:d}'.format(ii),
                                expr=_contrast_expr('amplitude_{0:d}'.format(ii), -1))

    @estimator('Dips')
    def estimate_dips(self, data, x):
        data, x = sort_check_data(data, x)
        return _improve_multi_lorentzian_estimate(
            TripleLorentzian.estimate_dips(self, data, x), data, 3)


class TripleLorentzianPeaksContrast(_DerivedErrors, TripleLorentzian):
    """ qudi-core TripleLorentzian (peaks estimator), additionally reporting the peak contrasts. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for ii in (1, 2, 3):
            self.set_param_hint('contrast_{0:d}'.format(ii),
                                expr=_contrast_expr('amplitude_{0:d}'.format(ii), 1))

    @estimator('Peaks')
    def estimate_peaks(self, data, x):
        data, x = sort_check_data(data, x)
        return _improve_multi_lorentzian_estimate(
            TripleLorentzian.estimate_peaks(self, data, x), data, 3)


def _contrast_formatted_output(original):
    """ Wraps qudi.util.units.create_formatted_output (used by FitContainer.formatted_result, i.e.
    by the fit widgets and the saved pulsed plots) for the parameters of the models above:
      - derived parameters ("period", "contrast...") are shown with the error lmfit propagated
        into them (including correlations), instead of as "(fixed)".
      - "contrast..." parameters are shown as plain percent, e.g. "(25.12 ± 0.08) %", instead of
        with an SI prefix chosen from the error size.
    Results of all other fit models do not contain these parameter names and are formatted by the
    original function unchanged.
    """
    def formatted_output(param_dict, *args, **kwargs):
        if not any(name.startswith('contrast') or name == 'period' for name in param_dict):
            return original(param_dict, *args, **kwargs)

        output = ''
        for name, entry in param_dict.items():
            entry = dict(entry)
            if entry.get('error') is None:
                entry['error'] = _DERIVED_ERRORS.get((name, float(entry['value'])))
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
