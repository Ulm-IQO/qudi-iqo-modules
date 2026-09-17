# -*- coding: utf-8 -*-
"""
nv_field_fitting.py

General-purpose NV-center magnetic-field fitting from CW ODMR data.

This module is FULLY STANDALONE (numpy + scipy + matplotlib only) -- it
has NO dependency on qudi objects and does not require any namespace
injection. It can be used anywhere ODMR-derived magnetic field estimation
is needed, independent of any particular measurement pipeline.

UNITS CONVENTION: D, E, freq ranges/dip positions, and the fitted/guessed
magnetic field vector B are all expected in the SAME frequency units
(Hz by default). B is represented directly in frequency units -- i.e.
B_freq = gamma_NV * B_gauss -- NOT in Gauss. Use field_freq_to_gauss() /
field_gauss_to_freq() to convert between this internal representation
and an actual field in Gauss.

Overview of the four fitting entry points:

    fit_field_from_dips           -- single measurement, dip positions
                                     given directly (e.g. from an external
                                     fit already performed elsewhere).
    fit_field_from_spectrum        -- single measurement, raw (freq, signal)
                                     ODMR spectrum given; dip positions are
                                     extracted internally via
                                     fit_multi_lorentzian_dips().
    fit_field_from_dips_sequence   -- array/sequence of measurements (e.g.
                                     a coil-current sweep), dip positions
                                     given directly for each. Chains each
                                     fit's result as the next measurement's
                                     initial guess, which is what resolves
                                     the sign/branch ambiguity inherent to
                                     any single ODMR measurement.
    fit_field_from_spectra_sequence -- array/sequence of raw spectra;
                                     combines the above two ideas.

Also provided: predict_odmr_dips() / plot_toy_odmr_spectrum(), for the
reverse direction (given known axes and a field, show what the ODMR
spectrum SHOULD look like) -- useful for sanity-checking axis estimates,
planning scan ranges, or visually explaining a fit result.
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit, least_squares, linear_sum_assignment
from scipy.signal import find_peaks, peak_widths


# =============================================================================
# Physical constants and unit conversions
# =============================================================================

# NV gyromagnetic ratio, in Hz per Gauss (~2.8025 MHz/G). Override this
# value everywhere by passing gamma_nv explicitly to field_freq_to_gauss/
# field_gauss_to_freq if a more precise/local value is preferred.
GAMMA_NV_HZ_PER_G = 2.8025e6


def field_freq_to_gauss(B_freq, gamma_nv=GAMMA_NV_HZ_PER_G):
    """Convert a field vector expressed in frequency units (gamma_NV * B)
    to an actual field in Gauss."""
    return np.asarray(B_freq, dtype=float) / gamma_nv


def field_gauss_to_freq(B_gauss, gamma_nv=GAMMA_NV_HZ_PER_G):
    """Convert an actual field in Gauss to the frequency-unit representation
    (gamma_NV * B) used internally by all fitting/prediction functions in
    this module."""
    return np.asarray(B_gauss, dtype=float) * gamma_nv


# =============================================================================
# NV ground-state spin-1 Hamiltonian (forward model)
# =============================================================================

def _normalize(v):
    """Normalize a 3-vector to unit length."""
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError('Cannot normalize a zero vector.')
    return v / n


# Spin-1 operators (hbar=1), basis order |+1>, |0>, |-1>.
_SZ = np.diag([1.0, 0.0, -1.0]).astype(complex)
_SX = (1.0 / np.sqrt(2.0)) * np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], dtype=complex)
_SY = (1.0 / np.sqrt(2.0)) * np.array([[0, -1j, 0], [1j, 0, -1j], [0, 1j, 0]], dtype=complex)


def _perp_basis(axis):
    """
    Return two arbitrary mutually orthonormal vectors perpendicular to
    `axis`. Used to construct the E (strain) term when no explicit strain
    axis is supplied -- when E == 0 (the common case), the (arbitrary)
    choice of in-plane basis has no effect on the result.
    """
    axis = _normalize(axis)
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    v1 = _normalize(np.cross(axis, helper))
    v2 = np.cross(axis, v1)
    return v1, v2


def nv_hamiltonian(axis, B_vec, D=2.87e9, E=0.0, strain_axis=None):
    """
    Build the ground-state spin-1 NV Hamiltonian (3x3 Hermitian matrix, in
    the same frequency units as D and B_vec) for a single NV orientation.

        H = D * S_axis^2 + E * (S_ex^2 - S_ey^2) + B_vec . S

    where S_axis = axis . S is the spin operator projected along the NV's
    own quantization axis (squaring an operator commutes with unitary
    rotation, so this correctly reproduces the usual D*Sz'^2 term of the
    NV's own rotated frame), and B_vec is the field already expressed in
    frequency units (gamma_NV * B_gauss) -- see module-level units note.

    Parameters
    ----------
    axis : array-like, shape (3,)
        Quantization axis of this NV orientation (need not be
        pre-normalized).
    B_vec : array-like, shape (3,)
        Magnetic field, in frequency units, expressed in the SAME
        lab-frame coordinates as `axis`.
    D : float
        Zero-field splitting (frequency units).
    E : float
        Transverse (strain) splitting (frequency units). Defaults to 0.
    strain_axis : array-like, shape (3,), optional
        If E != 0, the direction (only its perpendicular component is
        used) of the strain's "x" axis. If None, an arbitrary
        perpendicular frame is used (fine when E == 0, or when its exact
        orientation is unknown/unimportant).

    Returns
    -------
    H : ndarray, shape (3, 3), complex
    """
    axis = _normalize(axis)
    Saxis = axis[0] * _SX + axis[1] * _SY + axis[2] * _SZ
    H = D * (Saxis @ Saxis)

    if E != 0:
        if strain_axis is not None:
            ex = np.asarray(strain_axis, dtype=float)
            ex = ex - axis * np.dot(ex, axis)  # project out any component along axis
            ex = _normalize(ex)
            ey = np.cross(axis, ex)
        else:
            ex, ey = _perp_basis(axis)
        Sex = ex[0] * _SX + ex[1] * _SY + ex[2] * _SZ
        Sey = ey[0] * _SX + ey[1] * _SY + ey[2] * _SZ
        H = H + E * (Sex @ Sex - Sey @ Sey)

    B_vec = np.asarray(B_vec, dtype=float)
    H = H + B_vec[0] * _SX + B_vec[1] * _SY + B_vec[2] * _SZ

    return H


def nv_transition_frequencies(axis, B_vec, D=2.87e9, E=0.0, strain_axis=None):
    """
    Compute the two ODMR transition frequencies (ms=0 -> each of the other
    two eigenstates) for a single NV orientation, given a trial field.

    The lowest-energy eigenstate is identified as "ms=0" (valid whenever
    D > 0 and the field is not so large/transverse-dominant that this
    ordering breaks down -- the standard assumption for this type of
    modeling). The two transition frequencies are the energy differences
    from that eigenstate to each of the other two.

    Returns
    -------
    f_lo, f_hi : float
        The two transition frequencies, with f_lo <= f_hi (same frequency
        units as D/B_vec).
    """
    H = nv_hamiltonian(axis, B_vec, D=D, E=E, strain_axis=strain_axis)
    eigvals = np.linalg.eigvalsh(H)  # ascending order, guaranteed real (H is Hermitian)
    f_lo = float(eigvals[1] - eigvals[0])
    f_hi = float(eigvals[2] - eigvals[0])
    return f_lo, f_hi


# =============================================================================
# Forward prediction: dip positions from a given field, + toy spectrum plot
# =============================================================================

def predict_odmr_dips(quantization_axes, B, D=2.87e9, E=0.0, freq_range=None):
    """
    Given known/fixed NV quantization axes and a trial field, predict all
    ODMR dip (transition) frequencies.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
        NV quantization axes (any number; need not be pre-normalized).
    B : array-like, shape (3,)
        Magnetic field, in frequency units (gamma_NV * B_gauss).
    D, E : float
        Zero-field splitting / strain splitting (frequency units).
    freq_range : (float, float), optional
        If given, only transitions falling within [freq_start, freq_stop]
        are returned. If None, all transitions (2 per axis) are returned
        regardless of frequency.

    Returns
    -------
    dips : list of dict
        Each dict: {'axis_index': int, 'branch': 'lo'|'hi', 'freq': float},
        sorted by frequency.
    """
    axes = [_normalize(a) for a in quantization_axes]
    dips = []
    for ai, axis in enumerate(axes):
        f_lo, f_hi = nv_transition_frequencies(axis, B, D=D, E=E)
        for branch, f in (('lo', f_lo), ('hi', f_hi)):
            if freq_range is None or (freq_range[0] <= f <= freq_range[1]):
                dips.append({'axis_index': ai, 'branch': branch, 'freq': f})
    dips.sort(key=lambda d: d['freq'])
    return dips


def plot_toy_odmr_spectrum(quantization_axes, B, D=2.87e9, E=0.0, freq_range=None,
                           dip_sigma=None, dip_amplitude=1.0, offset=1.0,
                           n_points=2000, ax=None, show=True):
    """
    Build and (optionally) plot a synthetic ODMR spectrum showing where
    dips WOULD appear for a given set of quantization axes and a trial
    field -- useful for sanity-checking axis estimates, planning a scan
    window, or visually explaining a fit result.

    The total synthetic spectrum (sum of identical toy Lorentzian dips) is
    drawn as a solid line; each individual predicted dip's location is
    additionally marked with a vertical dashed line, COLOR-CODED BY
    QUANTIZATION-AXIS GROUP (all dips belonging to the same axis share a
    color), so it's visually clear which dips originate from which group.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    B : array-like, shape (3,)
        Field, in frequency units (gamma_NV * B_gauss).
    D, E : float
        Zero-field splitting / strain splitting (frequency units).
    freq_range : (float, float), optional
        x-axis range for the plot/spectrum. If None, automatically chosen
        to span all predicted dip frequencies (ignoring E, all axes) with
        a margin of 10x dip_sigma on each side.
    dip_sigma : float, optional
        Lorentzian HWHM used for every toy dip. If None, defaults to
        D * 1e-4 (a generic, reasonable-looking NV linewidth scale).
    dip_amplitude : float
        Depth of each toy dip (identical for all, for simplicity).
    offset : float
        Background level of the toy spectrum.
    n_points : int
        Number of points in the synthetic frequency axis.
    ax : matplotlib.axes.Axes, optional
        If given, plot into this axes instead of creating a new figure.
    show : bool
        If True, call plt.show() (ignored if ax is given, matching
        typical non-blocking notebook usage).

    Returns
    -------
    freq : ndarray
    signal : ndarray
        The synthetic spectrum.
    dips : list of dict
        As returned by predict_odmr_dips(), restricted to freq_range.
    """
    if dip_sigma is None:
        dip_sigma = D * 1e-4

    all_dips = predict_odmr_dips(quantization_axes, B, D=D, E=E, freq_range=None)
    if freq_range is None:
        if all_dips:
            f_min = min(d['freq'] for d in all_dips) - 10 * dip_sigma
            f_max = max(d['freq'] for d in all_dips) + 10 * dip_sigma
        else:
            f_min, f_max = D - 10 * dip_sigma, D + 10 * dip_sigma
        freq_range = (f_min, f_max)

    dips = [d for d in all_dips if freq_range[0] <= d['freq'] <= freq_range[1]]

    freq = np.linspace(freq_range[0], freq_range[1], n_points)
    signal = np.full_like(freq, offset, dtype=float)
    for d in dips:
        signal -= dip_amplitude * dip_sigma ** 2 / ((freq - d['freq']) ** 2 + dip_sigma ** 2)

    n_groups = len(quantization_axes)
    cmap = plt.get_cmap('tab10')
    colors = {i: cmap(i % 10) for i in range(n_groups)}

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 4))
    else:
        fig = ax.figure

    ax.plot(freq, signal, color='black', linewidth=1.2, label='toy spectrum')

    seen_groups = set()
    for d in dips:
        color = colors[d['axis_index']]
        label = f"group {d['axis_index']}" if d['axis_index'] not in seen_groups else None
        seen_groups.add(d['axis_index'])
        ax.axvline(d['freq'], color=color, linestyle='--', linewidth=1.5, label=label)

    ax.set_xlabel('Frequency')
    ax.set_ylabel('Signal')
    ax.set_title('Toy ODMR spectrum (predicted dip positions by group)')
    ax.legend(loc='lower right', fontsize=8)

    if show and ax is None:
        plt.show()

    return freq, signal, dips


# =============================================================================
# Multi-dip Lorentzian spectrum fitting (automatic dip-count selection)
# =============================================================================

def _multi_lorentzian_dip_model(freq, offset, flat_params):
    """offset - sum_i amplitude_i * sigma_i^2 / ((freq - center_i)^2 + sigma_i^2)."""
    n_dips = len(flat_params) // 3
    result = np.full_like(freq, offset, dtype=float)
    for i in range(n_dips):
        c, s, a = flat_params[3 * i:3 * i + 3]
        result = result - a * s ** 2 / ((freq - c) ** 2 + s ** 2)
    return result


def _make_model_func(n_dips):
    """Build a curve_fit-compatible function signature for a given fixed N."""
    def model(freq, offset, *params):
        return _multi_lorentzian_dip_model(freq, offset, params)
    return model


def _find_dip_candidates(freq, signal, max_dips, prominence=None):
    """
    Find candidate dip locations via scipy.signal.find_peaks on the
    inverted, offset-subtracted signal, sorted by prominence (descending).
    Also estimates each candidate's width (for an initial sigma guess).

    Returns a list of (center_freq, amplitude_guess, sigma_guess) tuples,
    up to max_dips entries, sorted by prominence (most prominent first).
    """
    baseline = float(np.percentile(signal, 90))
    inverted = baseline - signal  # positive-going peaks at dip locations

    if prominence is None:
        prominence = 0.1 * (np.max(inverted) - np.min(inverted[inverted > 0])) if np.any(inverted > 0) else None

    peak_idx, properties = find_peaks(inverted, prominence=prominence)
    if len(peak_idx) == 0:
        return []

    prominences = properties['prominences']
    order = np.argsort(prominences)[::-1][:max_dips]
    peak_idx = peak_idx[order]
    prominences = prominences[order]

    widths_samples, _, _, _ = peak_widths(inverted, peak_idx, rel_height=0.5)
    df = float(np.mean(np.abs(np.diff(freq)))) if len(freq) > 1 else 1.0

    candidates = []
    for idx, prom, w in zip(peak_idx, prominences, widths_samples):
        center = float(freq[idx])
        amplitude_guess = float(prom)
        sigma_guess = max(df, float(w) * df / 2.3548)  # FWHM (samples) -> HWHM (freq units)
        candidates.append((center, amplitude_guess, sigma_guess))

    return candidates


def fit_multi_lorentzian_dips(freq, signal, n_dips=None, min_dips=1, max_dips=8,
                              prominence=None):
    """
    Fit a sum-of-N-Lorentzian-dips model to a raw ODMR spectrum:

        signal(freq) = offset - sum_i amplitude_i * sigma_i^2 /
                                        ((freq - center_i)^2 + sigma_i^2)

    If n_dips is None (default), the number of dips is chosen
    automatically by fitting every candidate N from min_dips to max_dips
    and selecting the one with the lowest Bayesian Information Criterion
    (BIC) -- this penalizes adding dips that don't meaningfully improve
    the fit, avoiding overfitting noise as extra "dips".

    Initial guesses for dip centers/amplitudes/widths come from
    scipy.signal.find_peaks applied to the inverted, offset-subtracted
    signal (most prominent candidates used first).

    Parameters
    ----------
    freq : ndarray
    signal : ndarray
    n_dips : int, optional
        If given, skip automatic selection and fit exactly this many dips.
    min_dips, max_dips : int
        Range of N to try during automatic selection (ignored if n_dips
        is given).
    prominence : float, optional
        Passed to scipy.signal.find_peaks for candidate detection. If
        None, a reasonable default is estimated from the data.

    Returns
    -------
    dict with keys:
        'n_dips'      : int, the chosen/used number of dips
        'centers'     : ndarray, shape (n_dips,)
        'center_errs' : ndarray, shape (n_dips,) (1-sigma, from covariance;
                        NaN where unavailable)
        'amplitudes'  : ndarray, shape (n_dips,)
        'sigmas'      : ndarray, shape (n_dips,)
        'offset'      : float
        'popt', 'pcov': raw curve_fit outputs
        'best_fit'    : ndarray, model evaluated at `freq`
        'bic'         : float, BIC of the chosen fit
        'success'     : bool
        'candidates_tried' : dict {N: bic} for every N attempted (only
                        populated when n_dips was None)
    """
    freq = np.asarray(freq, dtype=float)
    signal = np.asarray(signal, dtype=float)
    n = len(freq)

    candidates_tried = {}

    def _fit_for_n(n_try):
        candidates = _find_dip_candidates(freq, signal, n_try, prominence=prominence)
        offset_guess = float(np.percentile(signal, 90))

        # Pad with evenly spaced guesses if find_peaks found fewer candidates than n_try.
        while len(candidates) < n_try:
            frac = (len(candidates) + 1) / (n_try + 1)
            fallback_center = float(freq[0] + frac * (freq[-1] - freq[0]))
            fallback_sigma = float((freq[-1] - freq[0]) / (4 * n_try))
            fallback_amp = 0.1 * (offset_guess - float(np.min(signal)) + 1e-9)
            candidates.append((fallback_center, fallback_amp, fallback_sigma))

        candidates = candidates[:n_try]
        flat_p0 = [offset_guess]
        lower, upper = [-np.inf], [np.inf]
        for c, a, s in candidates:
            flat_p0 += [c, s, a]
            lower += [freq[0], 1e-9, 0.0]
            upper += [freq[-1], (freq[-1] - freq[0]), np.inf]

        model = _make_model_func(n_try)
        try:
            popt, pcov = curve_fit(model, freq, signal, p0=flat_p0,
                                   bounds=(lower, upper), maxfev=20000)
            success = True
        except Exception:
            popt = np.array(flat_p0)
            pcov = None
            success = False

        best_fit = model(freq, *popt)
        rss = float(np.sum((signal - best_fit) ** 2))
        k = len(popt)
        bic = n * np.log(max(rss, 1e-300) / n) + k * np.log(n)

        return popt, pcov, best_fit, bic, success

    if n_dips is not None:
        popt, pcov, best_fit, bic, success = _fit_for_n(n_dips)
        chosen_n = n_dips
    else:
        best = None
        for n_try in range(min_dips, max_dips + 1):
            popt, pcov, best_fit, bic, success = _fit_for_n(n_try)
            candidates_tried[n_try] = bic
            if best is None or bic < best[3]:
                best = (popt, pcov, best_fit, bic, success, n_try)
        popt, pcov, best_fit, bic, success, chosen_n = best

    offset = popt[0]
    flat = popt[1:]
    centers = flat[0::3]
    sigmas = flat[1::3]
    amplitudes = flat[2::3]

    if pcov is not None:
        errs = np.sqrt(np.clip(np.diag(pcov), 0, None))
        center_errs = errs[1::3]
    else:
        center_errs = np.full(chosen_n, np.nan)

    return {
        'n_dips': chosen_n,
        'centers': np.asarray(centers, dtype=float),
        'center_errs': np.asarray(center_errs, dtype=float),
        'amplitudes': np.asarray(amplitudes, dtype=float),
        'sigmas': np.asarray(sigmas, dtype=float),
        'offset': float(offset),
        'popt': popt,
        'pcov': pcov,
        'best_fit': best_fit,
        'bic': float(bic),
        'success': bool(success),
        'candidates_tried': candidates_tried,
    }


# =============================================================================
# Magnetic field fitting: from directly-given dip positions
# =============================================================================

def fit_field_from_dips(quantization_axes, observed_dips, freq_range, B_guess,
                        D=2.87e9, E=0.0, dip_errors=None,
                        max_match_distance=None, max_iterations=20,
                        convergence_tol=1e3):
    """
    Fit a magnetic field vector B (in frequency units) that best explains
    a list of observed ODMR dip frequencies, given known/fixed NV group
    quantization axes and the frequency window the dips were measured in.

    Uses an iterative "predict -> match -> refine" (ICP-style) approach,
    since the discrete correspondence between predicted transitions and
    observed dips can itself change as B is updated during the fit:
      1. For the current trial B, predict all transition frequencies (up
         to 2 per axis) and keep only those falling inside freq_range.
      2. Match predicted-in-range frequencies to observed_dips via
         scipy.optimize.linear_sum_assignment (minimizing total squared
         frequency difference). Any predicted line with no observed
         counterpart, or vice versa, is left UNMATCHED and ignored (no
         penalty) by default, since real data can show extra/missing dips
         from weak NV groups. Set max_match_distance to reject only
         implausibly-distant matches instead.
      3. With the matching held FIXED, refine B via
         scipy.optimize.least_squares (Levenberg-Marquardt).
      4. Repeat until both the matching and B stop changing.

    NOTE on ambiguity: a single measurement's dips can be equally well
    explained by more than one B (e.g. related by a sign flip of a
    transverse component) -- this is NOT resolved by this function alone.
    To disambiguate, fit a CONTINUOUS SWEEP of measurements and chain each
    fit's result as the next point's B_guess -- see
    fit_field_from_dips_sequence() below.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
        Fixed/known NV quantization axes (any number from 1 to 4; need
        not be pre-normalized).
    observed_dips : sequence of float
        Observed dip center frequencies (same units as D/freq_range/B).
    freq_range : (float, float)
        (freq_start, freq_stop) of the ODMR scan window used to acquire
        observed_dips. Only predicted transitions inside this window are
        considered "visible" and eligible for matching.
    B_guess : array-like, shape (3,)
        Initial guess for B, in frequency units, in the same lab-frame
        coordinates as quantization_axes.
    D, E : float
        Zero-field splitting / strain splitting (frequency units).
    dip_errors : sequence of float, optional
        1-sigma uncertainties for each entry in observed_dips, used as
        inverse-variance weights. Non-positive/missing values fall back
        to the median of the valid entries, or 1.0 if none are valid.
    max_match_distance : float, optional
        If given, matches whose |predicted - observed| exceeds this value
        are discarded (treated as unmatched).
    max_iterations : int
        Maximum number of predict-match-refine iterations.
    convergence_tol : float
        Stop once B changes by less than this (frequency units) AND the
        matching is unchanged from the previous iteration.

    Returns
    -------
    B_fit : ndarray, shape (3,)
    fit_info : dict
        'matched'            : list of dicts (axis_index, branch,
                               predicted_freq, observed_freq, residual)
        'unmatched_predicted' : list of (axis_index, branch, freq)
        'unmatched_observed'  : list of float
        'n_iterations', 'converged', 'rms_residual'
    """
    axes = [_normalize(a) for a in quantization_axes]
    observed_dips = np.asarray(observed_dips, dtype=float)

    if dip_errors is None:
        errs = np.ones_like(observed_dips)
    else:
        errs = np.asarray(dip_errors, dtype=float).copy()
        valid = np.isfinite(errs) & (errs > 0)
        fallback = np.median(errs[valid]) if np.any(valid) else 1.0
        errs[~valid] = fallback

    freq_start, freq_stop = freq_range
    B = np.array(B_guess, dtype=float)

    prev_matching_ids = None
    matching = []
    n_iterations = 0
    converged = False

    for iteration in range(max_iterations):
        n_iterations = iteration + 1

        predicted = []  # (axis_index, branch, freq)
        for ai, axis in enumerate(axes):
            f_lo, f_hi = nv_transition_frequencies(axis, B, D=D, E=E)
            for branch, f in ((0, f_lo), (1, f_hi)):
                if freq_start <= f <= freq_stop:
                    predicted.append((ai, branch, f))

        if len(predicted) == 0 or len(observed_dips) == 0:
            matching = []
            break

        pred_freqs = np.array([p[2] for p in predicted])
        cost = (pred_freqs[:, None] - observed_dips[None, :]) ** 2
        row_ind, col_ind = linear_sum_assignment(cost)

        if max_match_distance is not None:
            keep = np.abs(pred_freqs[row_ind] - observed_dips[col_ind]) <= max_match_distance
            row_ind = row_ind[keep]
            col_ind = col_ind[keep]

        matching = [
            {
                'axis_index': predicted[r][0],
                'branch': predicted[r][1],
                'observed_index': int(c),
                'observed_freq': float(observed_dips[c]),
                'err': float(errs[c]),
            }
            for r, c in zip(row_ind, col_ind)
        ]

        if len(matching) == 0:
            break

        matching_ids = frozenset(
            (m['axis_index'], m['branch'], m['observed_index']) for m in matching
        )

        def _residuals(B_trial):
            res = []
            for m in matching:
                f_lo, f_hi = nv_transition_frequencies(axes[m['axis_index']], B_trial, D=D, E=E)
                pred_f = f_lo if m['branch'] == 0 else f_hi
                res.append((pred_f - m['observed_freq']) / m['err'])
            return np.array(res)

        result = least_squares(_residuals, B, method='lm')
        B_new = result.x

        b_change = float(np.max(np.abs(B_new - B)))
        B = B_new

        if prev_matching_ids == matching_ids and b_change < convergence_tol:
            converged = True
            break
        prev_matching_ids = matching_ids

    matched_out = []
    for m in matching:
        f_lo, f_hi = nv_transition_frequencies(axes[m['axis_index']], B, D=D, E=E)
        pred_f = f_lo if m['branch'] == 0 else f_hi
        matched_out.append({
            'axis_index': m['axis_index'],
            'branch': 'lo' if m['branch'] == 0 else 'hi',
            'predicted_freq': pred_f,
            'observed_freq': m['observed_freq'],
            'residual': pred_f - m['observed_freq'],
        })

    matched_obs_indices = {m['observed_index'] for m in matching}
    unmatched_observed = [
        float(observed_dips[i]) for i in range(len(observed_dips)) if i not in matched_obs_indices
    ]

    matched_pred_keys = {(m['axis_index'], m['branch']) for m in matching}
    unmatched_predicted = []
    for ai, axis in enumerate(axes):
        f_lo, f_hi = nv_transition_frequencies(axis, B, D=D, E=E)
        for branch, f in ((0, f_lo), (1, f_hi)):
            if freq_start <= f <= freq_stop and (ai, branch) not in matched_pred_keys:
                unmatched_predicted.append((ai, 'lo' if branch == 0 else 'hi', f))

    rms_residual = (
        float(np.sqrt(np.mean([m['residual'] ** 2 for m in matched_out])))
        if matched_out else float('nan')
    )

    fit_info = {
        'matched': matched_out,
        'unmatched_predicted': unmatched_predicted,
        'unmatched_observed': unmatched_observed,
        'n_iterations': n_iterations,
        'converged': converged,
        'rms_residual': rms_residual,
    }

    return B, fit_info


def fit_field_from_dips_sequence(quantization_axes, measurements, B_guess0,
                                 D=2.87e9, E=0.0, **kwargs):
    """
    Array/sequence version of fit_field_from_dips(): fit a magnetic field
    for EACH measurement in an ordered sequence (e.g. a coil-current
    sweep), chaining each fit's result as the next measurement's initial
    guess. This continuity is what allows the sign/branch ambiguity
    inherent to a single ODMR measurement to be resolved in practice: as
    long as the true field varies smoothly across the sequence and
    consecutive steps are small enough, chaining prevents the fit from
    jumping to a spurious sign-flipped solution.

    Parameters
    ----------
    quantization_axes : sequence of array-like
        Fixed across the whole sequence.
    measurements : sequence of dict
        Each dict must have 'observed_dips' (sequence of float) and
        'freq_range' (tuple); may optionally have 'dip_errors'.
    B_guess0 : array-like, shape (3,)
        Initial guess for B at the FIRST measurement (typically the
        zero-field point).
    D, E : float
        Shared across all measurements.
    **kwargs :
        Passed through to fit_field_from_dips() for every measurement.

    Returns
    -------
    list of (B_fit, fit_info) tuples, one per entry in `measurements`, in
    the same order.
    """
    results = []
    B_guess = np.array(B_guess0, dtype=float)

    for meas in measurements:
        B_fit, fit_info = fit_field_from_dips(
            quantization_axes,
            meas['observed_dips'],
            meas['freq_range'],
            B_guess,
            D=D, E=E,
            dip_errors=meas.get('dip_errors'),
            **kwargs
        )
        results.append((B_fit, fit_info))
        B_guess = B_fit  # chain continuity into the next measurement

    return results


# =============================================================================
# Magnetic field fitting: from a raw ODMR spectrum
# =============================================================================

def fit_field_from_spectrum(quantization_axes, freq, signal, B_guess,
                            D=2.87e9, E=0.0, n_dips=None, min_dips=1, max_dips=8,
                            prominence=None, **field_fit_kwargs):
    """
    Fit a magnetic field directly from a raw ODMR spectrum: first extracts
    dip positions/uncertainties via fit_multi_lorentzian_dips(), then
    fits the field via fit_field_from_dips().

    Parameters
    ----------
    quantization_axes : sequence of array-like
    freq, signal : ndarray
        Raw ODMR spectrum. freq_range for the field fit is taken as
        (freq.min(), freq.max()).
    B_guess : array-like, shape (3,)
    D, E : float
    n_dips, min_dips, max_dips, prominence :
        Passed through to fit_multi_lorentzian_dips() (see its docstring
        for automatic dip-count selection via BIC).
    **field_fit_kwargs :
        Passed through to fit_field_from_dips() (e.g. max_match_distance).

    Returns
    -------
    B_fit : ndarray, shape (3,)
    fit_info : dict
        As returned by fit_field_from_dips(), with an added
        'lorentzian_fit' key containing the full dict returned by
        fit_multi_lorentzian_dips() (dip centers/errors/amplitudes/etc.).
    """
    lorentzian_fit = fit_multi_lorentzian_dips(
        freq, signal, n_dips=n_dips, min_dips=min_dips, max_dips=max_dips,
        prominence=prominence
    )

    observed_dips = lorentzian_fit['centers']
    dip_errors = lorentzian_fit['center_errs']
    freq_range = (float(np.min(freq)), float(np.max(freq)))

    B_fit, fit_info = fit_field_from_dips(
        quantization_axes, observed_dips, freq_range, B_guess,
        D=D, E=E, dip_errors=dip_errors, **field_fit_kwargs
    )
    fit_info['lorentzian_fit'] = lorentzian_fit

    return B_fit, fit_info


def fit_field_from_spectra_sequence(quantization_axes, measurements, B_guess0,
                                    D=2.87e9, E=0.0, min_dips=1, max_dips=8,
                                    prominence=None, **field_fit_kwargs):
    """
    Array/sequence version of fit_field_from_spectrum(): fit a magnetic
    field for EACH raw spectrum in an ordered sequence, chaining each
    fit's result as the next measurement's initial guess (see
    fit_field_from_dips_sequence() for why this matters).

    Parameters
    ----------
    quantization_axes : sequence of array-like
    measurements : sequence of dict
        Each dict must have 'freq' and 'signal' (ndarrays); may optionally
        have 'n_dips' to override automatic dip-count selection for that
        specific measurement.
    B_guess0 : array-like, shape (3,)
    D, E : float
    min_dips, max_dips, prominence :
        Defaults for fit_multi_lorentzian_dips(), used for any
        measurement that doesn't specify its own 'n_dips'.
    **field_fit_kwargs :
        Passed through to fit_field_from_dips() for every measurement.

    Returns
    -------
    list of (B_fit, fit_info) tuples, one per entry in `measurements`, in
    the same order.
    """
    results = []
    B_guess = np.array(B_guess0, dtype=float)

    for meas in measurements:
        B_fit, fit_info = fit_field_from_spectrum(
            quantization_axes, meas['freq'], meas['signal'], B_guess,
            D=D, E=E, n_dips=meas.get('n_dips'),
            min_dips=min_dips, max_dips=max_dips, prominence=prominence,
            **field_fit_kwargs
        )
        results.append((B_fit, fit_info))
        B_guess = B_fit

    return results