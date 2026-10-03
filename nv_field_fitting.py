# -*- coding: utf-8 -*-
"""
nv_field_fitting.py

General-purpose NV-center magnetic-field fitting from CW ODMR data.

This module is FULLY STANDALONE (numpy + scipy + matplotlib + IPython
only) -- it has NO dependency on qudi objects and does not require any
namespace injection. It can be used anywhere ODMR-derived magnetic field
estimation is needed, independent of any particular measurement pipeline.

UNITS CONVENTION: D, E, freq ranges/dip positions, and the fitted/guessed
magnetic field vector B are all expected in the SAME frequency units
(Hz by default). B is represented directly in frequency units -- i.e.
B_freq = gamma_NV * B_gauss -- NOT in Gauss. Use field_freq_to_gauss() /
field_gauss_to_freq() to convert between this internal representation
and an actual field in Gauss.

Overview of the fitting entry points:

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
    fit_coil_axis_from_dips        -- array/sequence of measurements known
                                     to all lie along a SINGLE fixed field
                                     direction (e.g. one coil's current
                                     sweep, with the other coils held at
                                     zero) -- jointly fits that direction
                                     (per-amp) plus a shared background
                                     offset across the whole sequence, far
                                     better constrained than fitting each
                                     point's field independently.

Interactive, click-based dip picking/fitting for a single ODMR spectrum
is provided by pick_dips_interactive_spectrum() / fit_odmr_dips_interactive()
-- see their docstrings for required setup (the ipympl backend) and
controls. coil_characterization.py's multi-point picker is a thin loop
around pick_dips_interactive_spectrum(), so both share identical picking
mechanics and figure-display handling.

Also provided: predict_odmr_dips() / plot_toy_odmr_spectrum(), for the
reverse direction (given known axes and a field, show what the ODMR
spectrum SHOULD look like) -- useful for sanity-checking axis estimates,
planning scan ranges, or visually explaining a fit result.
"""

import itertools

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from matplotlib.widgets import SpanSelector
from scipy.optimize import curve_fit, least_squares, linear_sum_assignment, minimize_scalar
from scipy.signal import find_peaks, peak_widths
from IPython.display import display


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

def skip_first_odmr_points(freq, signal, n):
    """
    Drop the first `n` points of an ODMR spectrum, in the order they are
    stored (i.e. the first `n` frequencies of the sweep as measured).

    Useful when the first few points of every sweep are distorted, e.g.
    by NV charge-state dynamics right after the sweep (re)starts.

    Parameters
    ----------
    freq, signal : array_like
        Spectrum, same length.
    n : int
        Number of leading points to drop (0 = keep everything).

    Returns
    -------
    freq, signal : ndarray
        The remaining points, as float arrays.
    """
    freq = np.asarray(freq, dtype=float)
    signal = np.asarray(signal, dtype=float)
    n = int(n)
    if n < 0:
        raise ValueError(f'skip_first_points must be >= 0; got {n}.')
    if n >= len(freq):
        raise ValueError(f'Cannot skip {n} points of a {len(freq)}-point spectrum.')
    return freq[n:], signal[n:]


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
                              prominence=None, plot=False, ax=None):
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
        is given). max_dips defaults to 8, matching the physical upper
        bound for NV ensembles: 4 orientation groups x 2 transitions
        (ms=0 -> +-1) each in the fully general (non-axial) field case.
    prominence : float, optional
        Passed to scipy.signal.find_peaks for candidate detection. If
        None, a reasonable default is estimated from the data.
    plot : bool
        If True, plot the raw data together with the chosen fit curve and
        a vertical dashed line at each fitted dip center -- useful for
        quickly sanity-checking whether a fit is reasonable.
    ax : matplotlib.axes.Axes, optional
        If given (and plot=True), plot into this axes instead of creating
        a new figure.

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

    # min/max rather than freq[0]/freq[-1]: the sweep may run downward
    f_min, f_max = float(np.min(freq)), float(np.max(freq))

    def _fit_for_n(n_try):
        candidates = _find_dip_candidates(freq, signal, n_try, prominence=prominence)
        offset_guess = float(np.percentile(signal, 90))

        while len(candidates) < n_try:
            frac = (len(candidates) + 1) / (n_try + 1)
            fallback_center = float(f_min + frac * (f_max - f_min))
            fallback_sigma = float((f_max - f_min) / (4 * n_try))
            fallback_amp = 0.1 * (offset_guess - float(np.min(signal)) + 1e-9)
            candidates.append((fallback_center, fallback_amp, fallback_sigma))

        candidates = candidates[:n_try]
        flat_p0 = [offset_guess]
        lower, upper = [-np.inf], [np.inf]
        for c, a, s in candidates:
            flat_p0 += [c, s, a]
            lower += [f_min, 1e-9, 0.0]
            upper += [f_max, (f_max - f_min), np.inf]

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

    result = {
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

    if plot:
        _plot_lorentzian_fit(freq, signal, result, ax=ax)

    return result


def _plot_lorentzian_fit(freq, signal, fit_result, ax=None):
    """
    Plot raw ODMR data together with a multi-Lorentzian fit result (as
    returned by fit_multi_lorentzian_dips() or fit_lorentzian_dips_guided()):
    the data as points, the fitted curve as a solid line, and a vertical
    dashed line at each fitted dip center.
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 4))
    else:
        fig = ax.figure

    ax.plot(freq, signal, 'o', color='C0', markersize=3, label='data')
    ax.plot(freq, fit_result['best_fit'], '-', color='C1', linewidth=1.5,
            label=f"fit ({fit_result['n_dips']} dip(s))")

    for c in fit_result['centers']:
        ax.axvline(c, color='gray', linestyle='--', linewidth=1.0, alpha=0.7)

    ax.set_xlabel('Frequency')
    ax.set_ylabel('Signal')
    bic = fit_result.get('bic')
    title = f"Multi-Lorentzian fit (n_dips={fit_result['n_dips']}"
    title += f", BIC={bic:.2f})" if bic is not None else ")"
    ax.set_title(title)
    ax.legend(loc='lower right', fontsize=8)

    return fig, ax


# =============================================================================
# Guided (user-seeded) multi-Lorentzian dip fitting
# =============================================================================
#
# The automatic dip-finder (fit_multi_lorentzian_dips) is convenient but can
# lock onto noise spikes or merge/skip real dips when the spectrum is messy
# (weak dips, uneven baseline, nearby NV branches overlapping). This guided
# version instead takes the USER's visual estimate of dip centers as the
# initial parameter vector for a bounded fit, so the fit is constrained to
# stay near physically-plausible peaks the user has already identified by
# eye. Uses the exact same sum-of-Lorentzian-dips model and output
# conventions as fit_multi_lorentzian_dips(), so downstream code (plotting,
# field fitting) can treat the two interchangeably.

def fit_lorentzian_dips_guided(freq, signal, center_guesses, amplitude_guesses=None,
                               sigma_guess=1.0e6, offset_guess=None, center_window=10.0e6,
                               center_bounds=None, sigma_bounds=(0.1e6, 10.0e6), maxfev=20000,
                               plot=False, ax=None):
    """
    Fit a sum of N Lorentzian dips to an ODMR spectrum, seeded and
    constrained by user-supplied initial guesses for dip centers (and
    optionally amplitudes).

    Unlike fit_multi_lorentzian_dips() (which detects dip candidates
    automatically via scipy.signal.find_peaks, and can occasionally lock
    onto noise/unphysical features), this function takes the number and
    approximate locations of dips directly from the caller, and bounds
    each fitted center to stay within an acceptable range -- intended
    for spectra visually inspected (or interactively clicked, see
    pick_dips_interactive_spectrum() below) by a human first.

    Uses the same model as fit_multi_lorentzian_dips():
        signal(freq) = offset - sum_i amplitude_i * sigma_i^2 /
                                        ((freq - center_i)^2 + sigma_i^2)
    (sigma is the HWHM of each dip, matching this module's convention
    elsewhere).

    Parameters
    ----------
    freq : array_like
        Frequency axis (same units as D elsewhere in this module, Hz by
        default).
    signal : array_like
        ODMR signal (e.g. fluorescence counts or normalized contrast).
    center_guesses : sequence of float
        User-estimated dip center frequencies. One entry per dip; the
        number of entries sets the number of Lorentzians fit. An empty
        sequence is allowed (returns a trivial n_dips=0 result).
    amplitude_guesses : sequence of float, optional
        User-estimated dip depths, one per dip. If None, estimated
        automatically from the data at each guessed center relative to
        the baseline.
    sigma_guess : float or sequence of float, default 1e6
        Initial HWHM guess. Scalar (applied to all dips) or per-dip list.
    offset_guess : float, optional
        Initial off-resonance signal level. If None, uses median(signal).
    center_window : float, default 10e6
        Half-width of the DEFAULT symmetric box constraint around each
        `center_guesses` entry, i.e. [center - center_window,
        center + center_window]. Used for any dip whose corresponding
        entry in `center_bounds` (see below) is None or not given.
    center_bounds : sequence of (float, float) or None, optional
        EXPLICIT (lo, hi) acceptable-range bounds, Hz, one entry per
        dip, overriding the symmetric `center_window` default for that
        dip. Pass None for a given entry to fall back to the symmetric
        `center_window` behavior for just that dip. If `center_bounds`
        itself is None (default), the symmetric `center_window` is used
        for ALL dips (unchanged behavior from before this parameter was
        added). This is the mechanism used by
        pick_dips_interactive_spectrum()/coil_characterization.py's
        picker to pass through user-drawn, non-symmetric acceptable
        ranges.
    sigma_bounds : (float, float), default (0.1e6, 10e6)
        Hard lower/upper bounds on fitted HWHM, applied to all dips.
    maxfev : int, default 20000
        Max function evaluations passed to scipy.optimize.curve_fit.
    plot : bool, default False
        If True, plot data + fit curve + dip centers (reuses
        _plot_lorentzian_fit()).
    ax : matplotlib.axes.Axes, optional
        Passed through if plot=True.

    Returns
    -------
    dict with the SAME keys as fit_multi_lorentzian_dips() output:
        'n_dips', 'centers', 'center_errs', 'amplitudes', 'sigmas',
        'offset', 'popt', 'pcov', 'best_fit', 'success'
        ('bic'/'candidates_tried' are omitted since only a single, fixed
        N is ever tried here.)
        If the fit raises an exception, 'success' is False and all other
        fields reflect the INITIAL GUESSES, not a fit result.
    """
    freq = np.asarray(freq, dtype=float)
    signal = np.asarray(signal, dtype=float)
    center_guesses = np.asarray(center_guesses, dtype=float)
    n_dips = len(center_guesses)

    if n_dips == 0:
        flat_offset = float(np.median(signal)) if offset_guess is None else float(offset_guess)
        return {
            'n_dips': 0, 'centers': np.array([]), 'center_errs': np.array([]),
            'amplitudes': np.array([]), 'sigmas': np.array([]),
            'offset': flat_offset, 'popt': None, 'pcov': None,
            'best_fit': np.full_like(signal, flat_offset), 'success': True,
        }

    if offset_guess is None:
        offset_guess = float(np.median(signal))

    if amplitude_guesses is None:
        amplitude_guesses = []
        for c in center_guesses:
            idx = int(np.argmin(np.abs(freq - c)))
            depth = offset_guess - signal[idx]
            amplitude_guesses.append(max(depth, 1e-3 * abs(offset_guess) + 1e-9))
    amplitude_guesses = np.asarray(amplitude_guesses, dtype=float)

    if np.isscalar(sigma_guess):
        sigma_guess = np.full(n_dips, float(sigma_guess))
    else:
        sigma_guess = np.asarray(sigma_guess, dtype=float)

    if center_bounds is None:
        center_bounds = [None] * n_dips
    elif len(center_bounds) != n_dips:
        raise ValueError(f'center_bounds has {len(center_bounds)} entries but '
                          f'center_guesses has {n_dips}.')

    amp_span = max(np.max(signal) - np.min(signal), 1e-9)

    p0 = [offset_guess]
    lower = [min(np.min(signal), offset_guess) - abs(offset_guess) - 1.0]
    upper = [max(np.max(signal), offset_guess) + abs(offset_guess) + 1.0]

    for i in range(n_dips):
        if center_bounds[i] is not None:
            lo_i, hi_i = center_bounds[i]
        else:
            lo_i, hi_i = center_guesses[i] - center_window, center_guesses[i] + center_window
        # Clip the initial guess into its own bounds, in case an explicit
        # center_bounds entry doesn't happen to straddle center_guesses[i].
        c0 = min(max(center_guesses[i], lo_i), hi_i)

        p0 += [c0, sigma_guess[i], amplitude_guesses[i]]
        lower += [lo_i, sigma_bounds[0], 0.0]
        upper += [hi_i, sigma_bounds[1], 3.0 * amp_span]

    model = _make_model_func(n_dips)

    try:
        popt, pcov = curve_fit(model, freq, signal, p0=p0, bounds=(lower, upper), maxfev=maxfev)
        success = True
    except Exception as exc:  # noqa: BLE001 -- report failure, don't crash caller
        print(f'fit_lorentzian_dips_guided: fit failed ({exc}); returning initial guesses.')
        popt = np.array(p0, dtype=float)
        pcov = None
        success = False

    best_fit = model(freq, *popt)

    offset = popt[0]
    flat = popt[1:]
    centers = flat[0::3]
    sigmas = flat[1::3]
    amplitudes = flat[2::3]

    if pcov is not None:
        errs = np.sqrt(np.clip(np.diag(pcov), 0, None))
        center_errs = errs[1::3]
    else:
        center_errs = np.full(n_dips, np.nan)

    order = np.argsort(centers)
    centers = centers[order]
    sigmas = sigmas[order]
    amplitudes = amplitudes[order]
    center_errs = center_errs[order]

    result = {
        'n_dips': n_dips,
        'centers': np.asarray(centers, dtype=float),
        'center_errs': np.asarray(center_errs, dtype=float),
        'amplitudes': np.asarray(amplitudes, dtype=float),
        'sigmas': np.asarray(sigmas, dtype=float),
        'offset': float(offset),
        'popt': popt,
        'pcov': pcov,
        'best_fit': best_fit,
        'success': bool(success),
    }

    if plot:
        _plot_lorentzian_fit(freq, signal, result, ax=ax)

    return result


# =============================================================================
# ipympl / widget-backend display helpers
# =============================================================================
#
# These exist to work around two ipympl-specific quirks observed in
# practice (both appear to be timing/rendering issues in ipympl itself,
# not bugs in how figures are built here):
#
#   1. The FIRST widget-backend figure created in a kernel session can
#      render as a blank, number-less canvas in the frontend. Every
#      subsequent figure using the SAME display mechanism renders fine.
#      _warm_up_ipympl_backend() absorbs this one-time glitch on a
#      disposable throwaway figure.
#
#   2. This "first figure of its kind renders blank" behavior is per
#      DISPLAY MECHANISM, not strictly per kernel session: a figure built
#      with plain plt.subplots()+plt.show() can hit the SAME blank-canvas
#      issue the first time THAT code path runs, even if other figures
#      (built via explicit display(fig.canvas) instead of plt.show())
#      already rendered correctly earlier in the same session.
#      _display_figure() standardizes every figure built anywhere in
#      this module (and in coil_characterization.py, which reuses these
#      same helpers) on ONE display mechanism, so there is only ever a
#      single code path that might need warming up.
#
# _next_fig_num() assigns each figure an explicit, always-increasing
# number (shared across this module AND coil_characterization.py, which
# imports and uses the SAME counter/functions rather than keeping its
# own) so that matplotlib doesn't recycle low numbers (e.g. reusing
# "Figure 1" repeatedly) once earlier figures are closed, and so two
# different modules' figures can never collide on the same number.

_fig_counter = [-1]


def _next_fig_num():
    """Return a fresh figure number not used by any open figure. Numbers
    still held by open figures are skipped -- e.g. after this module is
    reloaded (which resets the counter) while earlier figures are open,
    since plt.subplots(num=...) raises if that figure already exists."""
    _fig_counter[0] += 1
    while plt.fignum_exists(_fig_counter[0]):
        _fig_counter[0] += 1
    return _fig_counter[0]


_ipympl_warmed_up = False


def _warm_up_ipympl_backend():
    """
    Create, render, display, and immediately close a tiny throwaway
    figure, ONCE per kernel session, using the exact same display
    mechanism as every other figure in this module (_display_figure()).

    No-op (and silent) if the current backend isn't ipympl/widget, or if
    this has already run once in this session.
    """
    global _ipympl_warmed_up
    if _ipympl_warmed_up:
        return
    backend = matplotlib.get_backend().lower()
    if 'widget' not in backend and 'ipympl' not in backend:
        return

    with plt.ioff():
        warm_fig, warm_ax = plt.subplots(figsize=(0.1, 0.1), num=_next_fig_num())
        warm_ax.plot([0, 1], [0, 1])
    _display_figure(warm_fig)
    plt.close(warm_fig)
    _ipympl_warmed_up = True


def _display_figure(fig):
    """
    Display a fully-built figure, using whichever mechanism actually
    renders reliably for the active backend.

    For the ipympl/widget backend specifically: force a render
    (fig.canvas.draw()) and then explicitly display the LIVE canvas
    widget (display(fig.canvas)) -- relying on plt.show() alone for this
    backend has been observed to occasionally produce a blank canvas.

    For any other backend (e.g. inline, if this module is used outside
    an interactive picking session), falls back to plain plt.show().
    """
    backend = matplotlib.get_backend().lower()
    if 'widget' in backend or 'ipympl' in backend:
        fig.canvas.draw()
        display(fig.canvas)
    else:
        plt.show()


# =============================================================================
# Interactive dip picking (single spectrum, click-based)
# =============================================================================

_DEFAULT_PICKER_PROMPT = (
    'Drag ranges and/or right-click to mark dips (see '
    'pick_dips_interactive_spectrum() docstring for exact behavior), '
    '"u" to undo. Press Enter here when done with this spectrum...'
)


def pick_dips_interactive_spectrum(freq, signal, freq_units='MHz', figsize=(11, 5.5),
                                   default_window=10.0e6, title=None, prompt=None,
                                   skip_first_points=0):
    """
    Interactively pick dip-center guesses (and an acceptable location
    RANGE for each) directly off a plotted ODMR spectrum, by clicking --
    rather than reading numeric frequency values off a (possibly
    too-small/sparse) tick axis by eye.

    Because the exact clicked x-coordinate (event.xdata) is used
    directly as the guess, NOT a value read off the tick labels, this
    sidesteps the tick-density/plot-size problem entirely: zoom in (via
    the toolbar ipympl provides automatically) on a dip of interest for
    a precise click, then zoom back out for the next one.

    REQUIRES the ipympl interactive backend to be active in the
    notebook -- run

        %matplotlib widget

    in a cell BEFORE calling this function (and `pip install ipympl` if
    not already installed, plus a full Jupyter server restart if ipympl
    was just installed/upgraded). With the default inline/static
    backend, clicks will not be registered.

    Controls:
        LEFT-click-and-drag    : draw a RANGE and immediately register it
                                  as a dip guess, with its CENTER taken as
                                  the midpoint of the dragged range (shown
                                  as a shaded span with a dashed center
                                  line). No right-click is required for
                                  this to count as a guess.
        RIGHT-click             : EITHER refines the most recently drawn
                                  range (if one is still "pending", i.e.
                                  no other action has happened since it
                                  was drawn) by moving that same dip's
                                  center to the exact clicked frequency,
                                  while keeping the dragged range as its
                                  acceptable-location window; OR, if there
                                  is no pending range, registers a brand
                                  new, independent dip guess at the
                                  clicked frequency with the default
                                  +-`default_window` range.
        'u' key (click the plot canvas first to focus it, then press 'u')
                               : undo the most recently added/confirmed
                                  dip guess (and its range).
        Enter (in the text prompt printed below the plot)
                               : finish and return the recorded guesses.
                                  Any still-pending drawn range is kept as
                                  its own guess (centered on its midpoint).

    In short: a drag ALONE is a complete guess; a click ALONE is a
    complete guess; a drag immediately followed by a click is treated as
    ONE guess (the click's location as center, the drag's extent as the
    window) -- matching the common case of "roughly bracket the dip, then
    point more precisely at its peak."

    Parameters
    ----------
    freq, signal : array_like
        Raw ODMR spectrum. `freq` in Hz (matching this module's
        convention elsewhere); only used for DISPLAY purposes here (the
        returned values are always in Hz regardless of `freq_units`).
    freq_units : {'Hz', 'kHz', 'MHz', 'GHz'}, default 'MHz'
        Units to DISPLAY the frequency axis in.
    figsize : (float, float), default (11, 5.5)
        Figure size, inches.
    default_window : float, default 10e6
        Acceptable +-range (Hz) assigned to a dip center registered via
        an independent click (i.e. with no pending drag at the time).
    title : str, optional
        Plot title. If None, no title is set.
    prompt : str, optional
        Text shown in the input() prompt below the plot. If None, a
        generic default is used.
    skip_first_points : int, default 0
        Drop the first n points of the spectrum before plotting (see
        skip_first_odmr_points()), e.g. points distorted by NV charge
        dynamics at the start of each sweep.

    Returns
    -------
    dip_guesses : list of float
        Recorded dip-center frequencies, Hz, sorted ascending. Pass
        directly as `center_guesses` to fit_lorentzian_dips_guided(), or
        as one entry of the `dip_guesses` list expected by
        coil_characterization.fit_sweep_dips().
    dip_windows : list of (float, float)
        One (lo, hi) acceptable-range bound (Hz) per entry in
        `dip_guesses`, same order. Pass as `center_bounds` to
        fit_lorentzian_dips_guided(), or as one entry of the
        `dip_windows` list expected by coil_characterization.fit_sweep_dips().
    """
    backend = matplotlib.get_backend().lower()
    if 'widget' not in backend and 'ipympl' not in backend:
        print(f"WARNING: current matplotlib backend is '{backend}', which does not "
              f"support click interaction in a notebook. Run '%matplotlib widget' "
              f"in a cell (and 'pip install ipympl' if needed, plus a FULL Jupyter "
              f"server restart) BEFORE calling pick_dips_interactive_spectrum(), "
              f"then retry.")

    _warm_up_ipympl_backend()

    scale = {'Hz': 1.0, 'kHz': 1e3, 'MHz': 1e6, 'GHz': 1e9}[freq_units]
    freq, signal = skip_first_odmr_points(freq, signal, skip_first_points)

    centers = []    # Hz
    windows = []    # (lo, hi) Hz
    artifacts = []  # [line, span_patch, text] per entry, parallel to centers/windows
    pending_index = [None]  # index of a drag-created entry not yet click-confirmed

    with plt.ioff():
        fig, ax = plt.subplots(figsize=figsize, num=_next_fig_num())
        freq_disp = freq / scale
        ax.plot(freq_disp, signal, '.-', ms=3, color='0.4')
        ax.xaxis.set_major_locator(MaxNLocator(nbins=25))
        ax.grid(True, which='major', alpha=0.4)
        ax.set_xlabel(f'Frequency ({freq_units})')
        ax.set_ylabel('Signal')
        if title is not None:
            ax.set_title(title)

        f_lo, f_hi = float(np.min(freq_disp)), float(np.max(freq_disp))
        margin = 0.02 * (f_hi - f_lo) if f_hi > f_lo else 1.0
        ax.set_xlim(f_lo - margin, f_hi + margin)
        ax.autoscale(enable=False)

    def _add_peak(center_hz, lo_hz, hi_hz):
        """Register a new dip guess, draw its artifacts, return its index."""
        centers.append(center_hz)
        windows.append((lo_hz, hi_hz))
        center_disp, lo_disp, hi_disp = center_hz / scale, lo_hz / scale, hi_hz / scale
        line = ax.axvline(center_disp, color='r', ls='--', lw=1.2)
        span_patch = ax.axvspan(lo_disp, hi_disp, color='r', alpha=0.08)
        text = ax.text(center_disp, ax.get_ylim()[1], f'{center_disp:.4f}',
                       rotation=90, va='top', ha='right', fontsize=8, color='r')
        artifacts.append([line, span_patch, text])
        fig.canvas.draw_idle()
        return len(centers) - 1

    def _move_peak_center(index, new_center_hz):
        """Move an existing entry's center marker, keeping its window."""
        centers[index] = new_center_hz
        line, span_patch, text = artifacts[index]
        center_disp = new_center_hz / scale
        line.set_xdata([center_disp, center_disp])
        text.set_position((center_disp, ax.get_ylim()[1]))
        text.set_text(f'{center_disp:.4f}')
        fig.canvas.draw_idle()

    def _on_span_select(xmin, xmax):
        lo_hz, hi_hz = min(xmin, xmax) * scale, max(xmin, xmax) * scale
        center_hz = 0.5 * (lo_hz + hi_hz)
        idx = _add_peak(center_hz, lo_hz, hi_hz)
        pending_index[0] = idx

    span_selector = SpanSelector(ax, _on_span_select, direction='horizontal',
                                 useblit=True, button=1,
                                 props=dict(alpha=0.2, facecolor='C0'))

    def _on_click(event):
        if event.inaxes != ax or event.button != 3 or event.xdata is None:
            return
        f_hz = float(event.xdata) * scale
        if pending_index[0] is not None:
            _move_peak_center(pending_index[0], f_hz)
            pending_index[0] = None
        else:
            _add_peak(f_hz, f_hz - default_window, f_hz + default_window)

    def _on_key(event):
        if event.key == 'u' and centers:
            centers.pop()
            windows.pop()
            line, span_patch, text = artifacts.pop()
            line.remove()
            span_patch.remove()
            text.remove()
            pending_index[0] = None  # the removed entry was the only possible pending one
            fig.canvas.draw_idle()

    cid_click = fig.canvas.mpl_connect('button_press_event', _on_click)
    cid_key = fig.canvas.mpl_connect('key_press_event', _on_key)

    _display_figure(fig)

    input(prompt if prompt is not None else _DEFAULT_PICKER_PROMPT)

    fig.canvas.mpl_disconnect(cid_click)
    fig.canvas.mpl_disconnect(cid_key)
    span_selector.disconnect_events()
    plt.close(fig)

    order = np.argsort(centers) if centers else np.array([], dtype=int)
    sorted_centers = [centers[i] for i in order]
    sorted_windows = [windows[i] for i in order]

    return sorted_centers, sorted_windows


def fit_odmr_dips_interactive(freq, signal, sigma_guess=2.0e6, default_window=10.0e6,
                              freq_units='MHz', figsize=(11, 5.5), title=None,
                              plot_fit=True, verbose=True, skip_first_points=0,
                              sigma_bounds=(0.1e6, 40.0e6)):
    """
    Interactively pick dip-center guesses (and acceptable location
    ranges) directly off a single ODMR spectrum by clicking (see
    pick_dips_interactive_spectrum() for controls), then immediately fit
    Lorentzian dips constrained to stay within the picked ranges (see
    fit_lorentzian_dips_guided()), printing and returning the fitted
    result.

    This is the single-measurement, standalone counterpart to the
    per-point picking used by coil_characterization.py's
    pick_dips_interactive() / fit_sweep_dips() (which loop this same
    picking mechanism over a whole current sweep) -- intended for
    one-off interactive fitting of an individual ODMR spectrum, e.g. when
    sanity-checking a measurement outside of any particular sweep
    pipeline, while sharing identical picking mechanics and the exact
    same guided-fit function for consistency.

    REQUIRES the ipympl interactive backend -- see
    pick_dips_interactive_spectrum()'s docstring for setup.

    Parameters
    ----------
    freq, signal : array_like
        Raw ODMR spectrum. `freq` in Hz (matching this module's
        convention elsewhere), `signal` in arbitrary units.
    sigma_guess : float, default 2e6
        Initial HWHM guess, Hz, applied to all dips (see
        fit_lorentzian_dips_guided()).
    default_window : float, default 10e6
        Acceptable +-range (Hz) assigned to a dip center registered via
        an independent click (see pick_dips_interactive_spectrum()).
    freq_units, figsize :
        Passed through to pick_dips_interactive_spectrum().
    title : str, optional
        Plot title for the picking figure.
    plot_fit : bool, default True
        If True, after fitting, display a second figure showing the
        spectrum together with the fitted Lorentzian model and dip
        centers (via _plot_lorentzian_fit()).
    verbose : bool, default True
        If True, print the fitted dip centers (with uncertainties, where
        available), amplitudes, and widths to the console.
    skip_first_points : int, default 0
        Drop the first n points of the spectrum before picking and
        fitting (see skip_first_odmr_points()), e.g. points distorted by
        NV charge dynamics at the start of each sweep.
    sigma_bounds : (float, float), default (0.1e6, 40e6)
        Allowed range of each dip's fitted HWHM, Hz. If fitted widths sit
        at the upper bound, the dips are broader than allowed and the
        fitted centers are distorted -- raise it.

    Returns
    -------
    dict
        The SAME dict as returned by fit_lorentzian_dips_guided(): keys
        'n_dips', 'centers', 'center_errs', 'amplitudes', 'sigmas',
        'offset', 'popt', 'pcov', 'best_fit', 'success' -- plus 'freq'
        and 'signal', the (possibly trimmed) spectrum actually fitted, on
        which 'best_fit' is evaluated.
    """
    freq, signal = skip_first_odmr_points(freq, signal, skip_first_points)

    dip_guesses, dip_windows = pick_dips_interactive_spectrum(
        freq, signal, freq_units=freq_units, figsize=figsize,
        default_window=default_window, title=title)

    fit = fit_lorentzian_dips_guided(freq, signal, dip_guesses,
                                     sigma_guess=sigma_guess, center_bounds=dip_windows,
                                     sigma_bounds=sigma_bounds)
    if fit['n_dips'] and np.any(np.abs(fit['sigmas']) >= 0.999 * sigma_bounds[1]):
        print(f'NOTE: some fitted widths sit at the upper bound '
              f'({sigma_bounds[1] / 1e6:g} MHz HWHM) -- those dips are broader than allowed '
              f'and their centers may be distorted; consider a larger sigma_bounds.')
    fit['freq'], fit['signal'] = freq, signal

    if verbose:
        if fit['n_dips'] == 0:
            print('No dips picked/fitted.')
        else:
            status = '' if fit['success'] else ' (FIT FAILED -- showing initial guesses)'
            print(f"Fitted {fit['n_dips']} dip(s){status}:")
            for i in range(fit['n_dips']):
                err = fit['center_errs'][i]
                err_str = f' +/- {err:.4e} Hz' if np.isfinite(err) else ''
                print(f"  center = {fit['centers'][i]:.6e} Hz{err_str}, "
                      f"amplitude = {fit['amplitudes'][i]:.4g}, "
                      f"sigma (HWHM) = {fit['sigmas'][i]:.4e} Hz")

    if plot_fit and fit['n_dips'] > 0:
        with plt.ioff():
            fig_fit, ax_fit = plt.subplots(figsize=(8, 4), num=_next_fig_num())
            _plot_lorentzian_fit(freq, signal, fit, ax=ax_fit)
        _display_figure(fig_fit)

    return fit


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
         scipy.optimize.least_squares (Levenberg-Marquardt, or
         trust-region if too few residuals for LM).
      4. Repeat until both the matching and B stop changing.

    NOTE on ambiguity: a single measurement's dips can be equally well
    explained by more than one B (e.g. related by a sign flip of a
    transverse component) -- this is NOT resolved by this function alone.
    To disambiguate, fit a CONTINUOUS SWEEP of measurements and chain each
    fit's result as the next point's B_guess -- see
    fit_field_from_dips_sequence() below, or (for a single-coil sweep
    known to lie along one fixed direction) fit_coil_axis_from_dips().

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
        'n_matches'           : int, number of matched dips in the final fit.
        'underdetermined'     : bool -- True if n_matches < 3 (cannot fully
                               constrain a 3-component B; result, if any,
                               should not be trusted).
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

        # 'lm' (MINPACK) requires n_residuals >= n_params (3); fall back
        # to a trust-region method otherwise (result is then not fully
        # constrained -- see 'underdetermined' in the returned fit_info).
        lsq_method = 'lm' if len(matching) >= 3 else 'trf'
        result = least_squares(_residuals, B, method=lsq_method)
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

    n_matches = len(matched_out)
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
        'n_matches': n_matches,
        'underdetermined': n_matches < 3,
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
                            prominence=None, plot=False, ax=None, skip_first_points=0,
                            **field_fit_kwargs):
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
    plot : bool
        If True, plot the raw spectrum together with the multi-Lorentzian
        fit curve and dip centers (see fit_multi_lorentzian_dips()) --
        useful for quickly checking whether the underlying spectral fit
        (which the field fit depends on) looks reasonable.
    ax : matplotlib.axes.Axes, optional
        If given (and plot=True), plot into this axes instead of creating
        a new figure.
    skip_first_points : int, default 0
        Drop the first n points of the spectrum before fitting (see
        skip_first_odmr_points()), e.g. points distorted by NV charge
        dynamics at the start of each sweep.
    **field_fit_kwargs :
        Passed through to fit_field_from_dips() (e.g. max_match_distance).

    Returns
    -------
    B_fit : ndarray, shape (3,)
    fit_info : dict
        As returned by fit_field_from_dips(), with an added
        'lorentzian_fit' key containing the full dict returned by
        fit_multi_lorentzian_dips() (dip centers/errors/amplitudes/etc.),
        plus its 'freq'/'signal': the (possibly trimmed) spectrum on
        which its 'best_fit' is evaluated.
    """
    freq, signal = skip_first_odmr_points(freq, signal, skip_first_points)
    lorentzian_fit = fit_multi_lorentzian_dips(
        freq, signal, n_dips=n_dips, min_dips=min_dips, max_dips=max_dips,
        prominence=prominence, plot=plot, ax=ax
    )
    lorentzian_fit['freq'], lorentzian_fit['signal'] = freq, signal

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
                                    prominence=None, plot=False, skip_first_points=0,
                                    **field_fit_kwargs):
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
    plot : bool
        If True, produce one plot PER measurement (each in its own new
        figure), showing that measurement's raw spectrum, fit curve, and
        dip centers -- useful for scanning through an entire sweep to
        spot-check fit quality. For large sequences, consider leaving
        this False and instead calling fit_field_from_spectrum() manually
        with plot=True on a few individual measurements of interest.
    skip_first_points : int, default 0
        Drop the first n points of every spectrum (see
        fit_field_from_spectrum()).
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
            plot=plot, skip_first_points=skip_first_points,
            **field_fit_kwargs
        )
        results.append((B_fit, fit_info))
        B_guess = B_fit

    return results


# =============================================================================
# Joint single-coil-axis field fitting (fixed-direction prior across a
# whole current sweep)
# =============================================================================

def _match_dips_one_point(pred_freqs, obs, distance=None, widths=None):
    """
    Match predicted transition frequencies to observed dips at one point.

    One-to-one assignment via linear_sum_assignment (pairs further apart
    than `distance` are dropped; None = no cutoff). If `widths` (fitted
    HWHM of each observed dip, Hz) is given, every predicted transition
    left unassigned whose nearest observed dip is already matched and
    lies within that dip's HWHM is MERGED into it: such transitions are
    hidden inside one unresolved dip (e.g. the near-4-fold-degenerate
    lines of a field along a cube axis), and the dip's center is best
    compared with the MEAN of all transitions it contains, not with an
    arbitrary one of them.

    Returns
    -------
    groups : list of (obs_index, [pred_index, ...]), ordered by each
        group's first (one-to-one assigned) pred_index.
    """
    pred_freqs = np.asarray(pred_freqs, dtype=float)
    obs = np.asarray(obs, dtype=float)
    if len(pred_freqs) == 0 or len(obs) == 0:
        return []

    cost = (pred_freqs[:, None] - obs[None, :]) ** 2
    row_ind, col_ind = linear_sum_assignment(cost)
    if distance is not None:
        keep = np.abs(pred_freqs[row_ind] - obs[col_ind]) <= distance
        row_ind, col_ind = row_ind[keep], col_ind[keep]

    groups = {int(c): [int(r)] for r, c in zip(row_ind, col_ind)}
    if widths is not None:
        assigned = set(int(r) for r in row_ind)
        for r in range(len(pred_freqs)):
            if r in assigned:
                continue
            c = int(np.argmin(np.abs(obs - pred_freqs[r])))
            if c in groups and abs(pred_freqs[r] - obs[c]) <= widths[c]:
                groups[c].append(r)
    # Same order as the plain one-to-one assignment (row_ind ascending), so
    # that without merging the residual vector is identical to before.
    return sorted(groups.items(), key=lambda item: item[1][0])


def _fit_linear_field_model(quantization_axes, current_vecs, observed_dips_list, M_guess,
                            B_offset_guess=None, D=2.87e9, E=0.0, freq_range=None,
                            dip_errors_list=None, fit_offset=True,
                            max_match_distance=None, bootstrap_max_match_distance=None,
                            max_iterations=20, convergence_tol=1e3, dip_widths_list=None):
    """
    Shared engine behind fit_coil_axis_from_dips() and
    fit_coil_matrix_from_dips(): fit the linear field model

        B_k = M @ I_k + B_offset

    to the observed dips at every point k, where M has shape
    (3, n_coils) and I_k is the length-n_coils current vector at point k.

    See fit_coil_axis_from_dips() for the algorithm (alternating matching
    / joint least squares, bootstrap fallback, dual convergence
    criterion) and the meaning of every argument; here `current_vecs`
    (shape (n, n_coils)) and `M_guess` (shape (3, n_coils)) replace the
    single-coil `currents` and `u_guess`, and `dip_widths_list` enables
    merging of unresolved transitions (see _match_dips_one_point()).

    Returns
    -------
    M_fit : ndarray, shape (3, n_coils)
    B_offset_fit : ndarray, shape (3,)
    fit_info : dict, as documented in fit_coil_axis_from_dips() (with
        'underdetermined' judged against 3*n_coils (+3 if fit_offset)
        free parameters).
    """
    axes = [_normalize(a) for a in quantization_axes]
    M = np.array(M_guess, dtype=float).reshape(3, -1)
    n_coils = M.shape[1]
    current_vecs = np.asarray(current_vecs, dtype=float).reshape(-1, n_coils)
    n_points = len(current_vecs)

    if len(observed_dips_list) != n_points:
        raise ValueError('current_vecs and observed_dips_list must have matching length.')

    observed_dips_list = [np.asarray(d, dtype=float) for d in observed_dips_list]

    if dip_errors_list is None:
        dip_errors_list = [None] * n_points
    cleaned_errors_list = []
    for d, e in zip(observed_dips_list, dip_errors_list):
        if e is None:
            cleaned_errors_list.append(np.ones_like(d))
            continue
        e = np.asarray(e, dtype=float).copy()
        valid = np.isfinite(e) & (e > 0)
        fallback = np.median(e[valid]) if np.any(valid) else 1.0
        e[~valid] = fallback
        cleaned_errors_list.append(e)
    dip_errors_list = cleaned_errors_list

    if dip_widths_list is None:
        dip_widths_list = [None] * n_points
    dip_widths_list = [None if w is None else np.asarray(w, dtype=float)
                       for w in dip_widths_list]

    B_offset = np.zeros(3) if B_offset_guess is None else np.array(B_offset_guess, dtype=float)

    n_M = 3 * n_coils
    n_params = n_M + 3 if fit_offset else n_M
    n_matches_per_point = [0] * n_points
    underdetermined = False
    converged = False
    n_iterations = 0
    bootstrap_iterations = []
    prev_matching_ids = None

    def _match_all_points(M_trial, B_offset_trial, distance):
        """Match predicted-vs-observed dips independently at every
        point, for the given trial (M, B_offset) and match-distance
        cutoff (None = no cutoff)."""
        matches = []
        counts = [0] * n_points
        for k in range(n_points):
            B_k = M_trial @ current_vecs[k] + B_offset_trial

            predicted = []  # (axis_index, branch, freq)
            for ai, axis in enumerate(axes):
                f_lo, f_hi = nv_transition_frequencies(axis, B_k, D=D, E=E)
                for branch, f in ((0, f_lo), (1, f_hi)):
                    if freq_range is None or (freq_range[0] <= f <= freq_range[1]):
                        predicted.append((ai, branch, f))

            obs = observed_dips_list[k]
            errs = dip_errors_list[k]
            if len(predicted) == 0 or len(obs) == 0:
                continue

            pred_freqs = np.array([p[2] for p in predicted])
            groups = _match_dips_one_point(pred_freqs, obs, distance=distance,
                                           widths=dip_widths_list[k])

            counts[k] = len(groups)
            for c, rows in groups:
                matches.append({
                    'point_index': k,
                    # (axis_index, branch) of every transition inside this
                    # observed dip; more than one if unresolved lines merged
                    'transitions': tuple((predicted[r][0], predicted[r][1]) for r in rows),
                    'predicted_freq': float(np.mean(pred_freqs[rows])),
                    'observed_freq': float(obs[c]),
                    'err': float(errs[c]),
                })
        return matches, counts

    def _rms_of(matches):
        """Unweighted RMS of (predicted - observed) over a match list, Hz."""
        if not matches:
            return float('nan')
        return float(np.sqrt(np.mean([(m['predicted_freq'] - m['observed_freq']) ** 2
                                      for m in matches])))

    for iteration in range(max_iterations):
        n_iterations = iteration + 1

        all_matches, n_matches_per_point = _match_all_points(M, B_offset, max_match_distance)
        n_matches_total = len(all_matches)

        if n_matches_total < n_params:
            # Strict tolerance yielded too few matches -- retry THIS
            # iteration with a much looser (or no) cutoff, purely to get
            # a usable refinement started. See fit_coil_axis_from_dips()
            # docstring for rationale.
            all_matches, n_matches_per_point = _match_all_points(
                M, B_offset, bootstrap_max_match_distance)
            n_matches_total = len(all_matches)
            bootstrap_iterations.append(iteration)

            if n_matches_total < n_params:
                # Even the loosest possible matching can't find enough
                # dips overall -- this is a genuine data insufficiency,
                # not a tolerance problem, so give up.
                underdetermined = True
                break

        # === Matching signature for convergence check ===
        # Records which observed dip (identified by its frequency) is
        # assigned to which (point, NV axis, branch) transitions at this
        # iteration.
        matching_ids = sorted(
            (m['point_index'], m['transitions'], m['observed_freq'])
            for m in all_matches
        )

        def _residuals(params, _matches=all_matches, _fit_offset=fit_offset, _B_fixed=B_offset):
            M_try = params[0:n_M].reshape(3, n_coils)
            B_off_try = params[n_M:n_M + 3] if _fit_offset else _B_fixed
            res = np.empty(len(_matches))
            for i, m in enumerate(_matches):
                B_k = M_try @ current_vecs[m['point_index']] + B_off_try
                pred_fs = []
                for axis_index, branch in m['transitions']:
                    f_lo, f_hi = nv_transition_frequencies(axes[axis_index], B_k, D=D, E=E)
                    pred_fs.append(f_lo if branch == 0 else f_hi)
                res[i] = (np.mean(pred_fs) - m['observed_freq']) / m['err']
            return res

        p0 = np.concatenate([M.ravel(), B_offset]) if fit_offset else M.ravel().copy()
        lsq_method = 'lm' if n_matches_total >= n_params else 'trf'
        result = least_squares(_residuals, p0, method=lsq_method)

        M_new = result.x[0:n_M].reshape(3, n_coils)
        B_offset_new = result.x[n_M:n_M + 3] if fit_offset else B_offset

        change = max(np.max(np.abs(M_new - M)), np.max(np.abs(B_offset_new - B_offset)))
        M, B_offset = M_new, B_offset_new

        # === Convergence: stable matching AND small parameter step ===
        if prev_matching_ids == matching_ids and change < convergence_tol:
            converged = True
            break
        prev_matching_ids = matching_ids

    per_point_B = current_vecs @ M.T + B_offset

    # === Goodness-of-fit at the final (M, B_offset) ===
    final_matches, _ = _match_all_points(M, B_offset, max_match_distance)
    all_dip_matches, _ = _match_all_points(M, B_offset, None)

    fit_info = {
        'n_matches_per_point': n_matches_per_point,
        'n_matches_total': int(sum(n_matches_per_point)),
        'underdetermined': underdetermined or (sum(n_matches_per_point) < n_params),
        'converged': converged,
        'n_iterations': n_iterations,
        'bootstrap_iterations': bootstrap_iterations,
        'per_point_B': per_point_B,
        'rms_residual': _rms_of(final_matches),
        'rms_residual_all_dips': _rms_of(all_dip_matches),
    }

    return M, B_offset, fit_info


def fit_coil_axis_from_dips(quantization_axes, currents, observed_dips_list, u_guess,
                            B_offset_guess=None, D=2.87e9, E=0.0, freq_range=None,
                            dip_errors_list=None, fit_offset=True,
                            max_match_distance=None, bootstrap_max_match_distance=None,
                            max_iterations=20, convergence_tol=1e3, dip_widths_list=None):
    """
    Jointly fit a single coil's field-per-amp vector u (Hz/A, i.e.
    gamma_NV * B_gauss per amp) and a shared background offset B_offset,
    given a SEQUENCE of current values and, for each, a set of observed
    dip frequencies, under the model

        B(I) = u * I + B_offset

    This directly encodes the physical prior that a single coil's field
    direction is fixed across its entire current sweep -- it does NOT
    fit an independent 3-vector at every current (unlike calling
    fit_field_from_dips() pointwise); the fit has only 6 free parameters
    (or 3, if fit_offset=False) shared across ALL currents, rather than 3
    per point, making it far better-constrained even when individual
    points have very few matched dips.

    Uses the same alternating matching/least-squares scheme as
    fit_field_from_dips(): at each outer iteration, dips are predicted and
    matched to observations independently at every current (via
    scipy.optimize.linear_sum_assignment, using the CURRENT (u, B_offset)
    estimate), then a single joint scipy.optimize.least_squares call
    updates (u, B_offset) to minimize the combined, weighted residuals
    across all matched dips at all currents. Repeats until BOTH the
    dip-to-transition matching is unchanged from the previous iteration
    AND the change in (u, B_offset) falls below convergence_tol (the same
    dual criterion as fit_field_from_dips()), or max_iterations is
    reached.

    BOOTSTRAP FALLBACK: at any outer iteration, if the total number of
    matches found using `max_match_distance` is too few to constrain the
    fit (< 6, or < 3 if fit_offset=False), matching is RETRIED for that
    same iteration using `bootstrap_max_match_distance` instead (a much
    looser tolerance, or no cutoff at all if left as None). This exists
    because a poor initial `u_guess`/`B_offset_guess` can easily push
    every predicted transition more than `max_match_distance` away from
    its true observed dip on the very first iteration, which would
    otherwise cause the fit to abort immediately (reported as
    'underdetermined', having never actually attempted a refinement) even
    though the underlying data is perfectly fine. Once (u, B_offset) has
    been refined at least once, subsequent iterations should naturally
    satisfy the strict `max_match_distance` again without needing to
    bootstrap -- if bootstrapping is still needed after several
    iterations, that is a sign the fit has not yet converged to the
    correct solution (see 'bootstrap_iterations' in the returned
    fit_info) and the initial guess or dip-guess quality may be worth a
    second look.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
        Fixed/known NV quantization axes.
    currents : array_like, shape (n,)
        Coil current at each point, Amps.
    observed_dips_list : list of array_like
        One array per current, of observed dip center frequencies, Hz.
        An empty array for a given point means "no usable dips at this
        current" -- it contributes zero matches but does not otherwise
        break the fit.
    u_guess : array-like, shape (3,)
        Initial guess for u (Hz/A). A nonzero, roughly-correct-sign guess
        (e.g. the coil's nominal axis times a rough Hz/A scale) is
        strongly recommended, since a poor first guess can cause the very
        first matching step to lock onto the wrong transitions -- though
        the bootstrap fallback above makes the fit considerably more
        tolerant of this than it would otherwise be.
    B_offset_guess : array-like, shape (3,), optional
        Initial guess for B_offset, Hz. Defaults to (0, 0, 0).
    D, E : float
        Zero-field-splitting / strain parameters, Hz.
    freq_range : (float, float), optional
        If given, only predicted transitions inside this window are
        eligible for matching at every current (mirroring
        fit_field_from_dips()'s freq_range argument). If None, all
        predicted transitions (2 per axis) are considered regardless of
        frequency.
    dip_errors_list : list of array_like, optional
        One array per current, of 1-sigma uncertainties on the
        corresponding observed_dips_list entry, Hz. If None (or if an
        individual entry is None), that point's errors are filled with a
        fallback (median of that point's own valid errors, else 1.0).
    fit_offset : bool, default True
        If True, B_offset is a free parameter; if False, held fixed at
        B_offset_guess.
    max_match_distance : float, optional
        Passed through to the matching step at every current: matches
        whose |predicted - observed| exceeds this value are discarded. If
        None, no filtering is applied even on the "strict" pass (i.e. the
        bootstrap fallback above never triggers, since the strict pass
        already accepts everything linear_sum_assignment proposes).
    bootstrap_max_match_distance : float, optional
        Loosened match tolerance used ONLY as a fallback when the strict
        `max_match_distance` yields too few matches at a given outer
        iteration (see above). Defaults to None, meaning NO distance
        cutoff at all on the bootstrap pass (i.e. accept whatever
        linear_sum_assignment proposes, however far off) -- the most
        permissive possible fallback, intended to always succeed in
        getting *a* refinement started as long as there are enough
        observed dips overall.
    max_iterations : int, default 20
        Maximum number of outer (match -> joint least-squares) iterations.
    convergence_tol : float, default 1e3
        Outer-loop convergence threshold, Hz. Only declares convergence
        if the matching is ALSO unchanged from the previous iteration, so
        a small parameter step taken while dips are still being
        reassigned between transitions is not mistaken for convergence.
    dip_widths_list : list of array_like, optional
        One array per current, of the fitted HWHM (Hz) of each observed
        dip (e.g. the 'sigmas' of fit_lorentzian_dips_guided()). If given,
        predicted transitions that are left over after one-to-one
        matching and lie within an observed dip's HWHM are merged into
        that dip, which is then compared with the MEAN of its
        transitions. This matters whenever lines overlap -- e.g. for a
        field along a cube axis, where all four NV orientations are
        nearly degenerate -- since otherwise a merged dip is assigned to
        one arbitrary transition and biases the fit. If None (default),
        matching is strictly one-to-one.

    Returns
    -------
    u_fit : ndarray, shape (3,)
        Fitted coil field-per-amp vector, Hz/A.
    B_offset_fit : ndarray, shape (3,)
        Fitted (or fixed) background offset, Hz.
    fit_info : dict:
        'n_matches_per_point' : list of int, number of matched dips at
                                  each current, from the FINAL iteration
                                  (points with 0 matches contribute
                                  nothing to the fit -- useful for
                                  spotting bad dip guesses).
        'n_matches_total'     : int, sum over all points, final iteration.
        'underdetermined'     : bool -- True if n_matches_total (even
                                  after the bootstrap fallback) is less
                                  than the number of free parameters (3
                                  or 6); result should not be trusted.
        'converged'           : bool.
        'n_iterations'        : int.
        'bootstrap_iterations' : list of int -- outer-iteration indices
                                  (0-based) at which the bootstrap
                                  fallback was needed. Non-empty entries
                                  here (especially beyond iteration 0)
                                  suggest the initial guess was
                                  significantly off, or that
                                  max_match_distance may be too tight for
                                  the data.
        'per_point_B'         : ndarray, shape (n, 3) -- u*I_k + B_offset
                                  at the final fit, one row per input
                                  current, for diagnostic plotting.
        'rms_residual'        : float, Hz -- unweighted RMS of
                                  (predicted - observed) over dips matched
                                  within max_match_distance at the final
                                  fit (same meaning as in
                                  fit_field_from_dips()).
        'rms_residual_all_dips' : float, Hz -- same, but matching with NO
                                  distance cutoff, so every observed dip
                                  counts. Unlike 'rms_residual', a fit
                                  cannot make this look small by pushing
                                  awkward dips outside max_match_distance,
                                  so it is the fairer score for comparing
                                  fits from different starting guesses
                                  (see fit_coil_axis_from_dips_multistart()).
    """
    currents = np.asarray(currents, dtype=float)
    M_fit, B_offset_fit, fit_info = _fit_linear_field_model(
        quantization_axes, currents[:, None], observed_dips_list,
        np.asarray(u_guess, dtype=float).reshape(3, 1),
        B_offset_guess=B_offset_guess, D=D, E=E, freq_range=freq_range,
        dip_errors_list=dip_errors_list, fit_offset=fit_offset,
        max_match_distance=max_match_distance,
        bootstrap_max_match_distance=bootstrap_max_match_distance,
        max_iterations=max_iterations, convergence_tol=convergence_tol,
        dip_widths_list=dip_widths_list)

    return M_fit[:, 0], B_offset_fit, fit_info


def fit_coil_axis_from_dips_multistart(quantization_axes, currents, observed_dips_list,
                                       u_guess, B_offset_guess=None, n_starts=10,
                                       u_spread=0.2, B_offset_spread=3.0e6, seed=0,
                                       **fit_kwargs):
    """
    Run fit_coil_axis_from_dips() from several randomly perturbed starting
    guesses and keep the best result.

    fit_coil_axis_from_dips() re-matches dips to transitions only at the
    current estimate, so a starting guess that is somewhat off can lock in
    a wrong dip assignment and settle on a wrong (u, B_offset) that still
    reports converged=True. A guess lying exactly on a symmetric
    configuration (e.g. u along a lab axis with zero off-axis components)
    can also leave those components stuck at zero. Restarting from
    several nearby guesses and keeping the lowest-residual fit avoids
    both.

    Start 0 always uses (u_guess, B_offset_guess) unperturbed; starts
    1..n_starts-1 add independent Gaussian perturbations to every
    component.

    Parameters
    ----------
    quantization_axes, currents, observed_dips_list, u_guess, B_offset_guess :
        As in fit_coil_axis_from_dips().
    n_starts : int, default 10
        Total number of fits, including the unperturbed one.
    u_spread : float, default 0.2
        Standard deviation of the perturbation added to EACH component of
        u_guess, as a fraction of |u_guess| (Hz/A). E.g. 0.2 with
        |u_guess| = 50 MHz/A perturbs each component by ~10 MHz/A.
    B_offset_spread : float, default 3e6
        Standard deviation of the perturbation added to each component of
        B_offset_guess, Hz (3 MHz ~ 1 G). Ignored if fit_offset=False is
        passed in fit_kwargs.
    seed : int or None, default 0
        Seed for the perturbation RNG, so repeated calls on the same data
        give the same result. None for a fresh random draw.
    **fit_kwargs :
        Passed through unchanged to fit_coil_axis_from_dips() (D, E,
        freq_range, dip_errors_list, dip_widths_list, fit_offset,
        max_match_distance, bootstrap_max_match_distance, max_iterations,
        convergence_tol).

    Returns
    -------
    u_fit, B_offset_fit, fit_info :
        As returned by fit_coil_axis_from_dips() for the best start, with
        fit_info additionally containing:
        'best_start' : int, index of the winning start (0 = unperturbed).
        'starts'     : list of dict, one per start, each with keys
                       'u_guess', 'B_offset_guess', 'u_fit',
                       'B_offset_fit', 'converged', 'underdetermined',
                       'n_matches_total', 'rms_residual',
                       'rms_residual_all_dips'.
        'ambiguous'  : bool, True if any other start reached a clearly
                       different (u, B_offset) (by > 1% of |u|, or
                       > 100 kHz in B_offset) whose all-dip RMS is about
                       as good as the best (within 1.5x, or 10 kHz).
        'alternative_solutions' : list of dict ('start', 'u_fit',
                       'B_offset_fit'), the distinct equally good
                       solutions behind 'ambiguous'.

    Notes
    -----
    Ranking: fits that are not underdetermined beat ones that are; among
    those, the lowest 'rms_residual_all_dips' wins. That score matches
    every observed dip with no distance cutoff, so a wrong fit cannot win
    by pushing awkward dips outside max_match_distance.

    Several starts can reach clearly different (u, B_offset) with the same
    score. Often this is exact symmetry, not noise: the four NV axes map
    onto themselves under some lab-frame rotations/reflections, and ODMR
    only sees |B.n|, so e.g. swapping two lab components (with sign
    flips) of BOTH u and B_offset leaves every spectrum unchanged. One
    coil's sweep cannot resolve this; the background offset shared by all
    three coils can. fit_info['ambiguous'] and
    fit_info['alternative_solutions'] report it.
    """
    u_guess = np.asarray(u_guess, dtype=float)
    B_offset_guess = (np.zeros(3) if B_offset_guess is None
                      else np.asarray(B_offset_guess, dtype=float))
    fit_offset = fit_kwargs.get('fit_offset', True)

    rng = np.random.default_rng(seed)
    u_scale = u_spread * np.linalg.norm(u_guess)

    # === Run every start ===
    results = []
    for k in range(max(1, int(n_starts))):
        if k == 0:
            u0, b0 = u_guess.copy(), B_offset_guess.copy()
        else:
            u0 = u_guess + rng.normal(scale=u_scale, size=3)
            b0 = (B_offset_guess + rng.normal(scale=B_offset_spread, size=3)
                  if fit_offset else B_offset_guess.copy())

        u_fit, b_fit, info = fit_coil_axis_from_dips(
            quantization_axes, currents, observed_dips_list, u0,
            B_offset_guess=b0, **fit_kwargs)
        results.append((u0, b0, u_fit, b_fit, info))

    # === Pick the best start ===
    def _score(r):
        info = r[4]
        rms = info['rms_residual_all_dips']
        return (info['underdetermined'], np.inf if not np.isfinite(rms) else rms)

    best_start = min(range(len(results)), key=lambda i: _score(results[i]))
    _, _, u_best, b_best, info_best = results[best_start]

    # === Detect equally good but different solutions ===
    # The tetrahedral NV axis set maps onto itself under several lab-frame
    # rotations/reflections, and ODMR is blind to the sign of B.n, so
    # distinct (u, B_offset) can give IDENTICAL spectra at every current.
    best_rms = info_best['rms_residual_all_dips']
    rms_tol = max(1.5 * best_rms, best_rms + 10.0e3)
    u_tol = 0.01 * np.linalg.norm(u_best)
    b_tol = 100.0e3
    alternatives = []  # (start index, u_fit, B_offset_fit), deduplicated
    for i, (_, _, u_fit, b_fit, info) in enumerate(results):
        if i == best_start or info['underdetermined']:
            continue
        if not (info['rms_residual_all_dips'] <= rms_tol):
            continue
        known = [(u_best, b_best)] + [(a[1], a[2]) for a in alternatives]
        if all(np.max(np.abs(u_fit - uk)) > u_tol or np.max(np.abs(b_fit - bk)) > b_tol
               for uk, bk in known):
            alternatives.append((i, u_fit, b_fit))

    info_best = dict(info_best)
    info_best['best_start'] = best_start
    info_best['ambiguous'] = len(alternatives) > 0
    info_best['alternative_solutions'] = [
        {'start': i, 'u_fit': u_fit, 'B_offset_fit': b_fit} for (i, u_fit, b_fit) in alternatives]
    info_best['starts'] = [{
        'u_guess': u0,
        'B_offset_guess': b0,
        'u_fit': u_fit,
        'B_offset_fit': b_fit,
        'converged': info['converged'],
        'underdetermined': info['underdetermined'],
        'n_matches_total': info['n_matches_total'],
        'rms_residual': info['rms_residual'],
        'rms_residual_all_dips': info['rms_residual_all_dips'],
    } for (u0, b0, u_fit, b_fit, info) in results]

    return u_best, b_best, info_best


# =============================================================================
# NV-axis symmetries and full three-coil calibration (mixed-current data)
# =============================================================================

def nv_axis_symmetries(quantization_axes, tol=1e-6):
    """
    Find every orthogonal lab-frame transformation g that maps the set of
    NV quantization axes onto itself up to sign (g @ n_i = +/- n_j for
    every axis n_i).

    With E = 0, each NV orientation's transitions depend only on |B.n|
    and |B|, so for any such g the field g @ B produces EXACTLY the same
    ODMR spectrum as B. A fit of single-coil data therefore determines
    (u, B_offset) only up to applying one of these g to both. For the
    standard four <111> axes of a lab-aligned diamond this is the full
    cube group: all 48 signed permutation matrices. Computing it from the
    actual `quantization_axes` keeps this correct for a diamond that is
    not aligned with the lab frame.

    With E != 0 the transverse strain direction can break some of these
    symmetries; the returned set is then a superset of the true
    ambiguities, which is harmless for resolve_coil_symmetry_from_mixed_dips()
    (the extra candidates simply score worse).

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
        NV quantization axes. At least three must be linearly independent.
    tol : float, default 1e-6
        Numerical tolerance for orthogonality / axis-matching checks.

    Returns
    -------
    symmetries : list of ndarray, shape (3, 3)
        Distinct orthogonal matrices, always including the identity.
    """
    axes = [_normalize(a) for a in quantization_axes]

    # === Pick three linearly independent axes as a basis ===
    basis = None
    for combo in itertools.combinations(range(len(axes)), 3):
        N = np.column_stack([axes[i] for i in combo])
        if abs(np.linalg.det(N)) > 1e-6:
            basis = combo
            break
    if basis is None:
        raise ValueError('nv_axis_symmetries needs at least three linearly independent '
                         'quantization axes.')
    N_inv = np.linalg.inv(np.column_stack([axes[i] for i in basis]))

    # === Try every signed assignment of the basis axes to axes ===
    symmetries = []
    for targets in itertools.permutations(range(len(axes)), 3):
        for signs in itertools.product((1.0, -1.0), repeat=3):
            T = np.column_stack([s * axes[t] for s, t in zip(signs, targets)])
            g = T @ N_inv
            if not np.allclose(g.T @ g, np.eye(3), atol=tol):
                continue
            maps_set = all(any(abs(abs(np.dot(g @ a, b)) - 1.0) < tol for b in axes)
                           for a in axes)
            if maps_set and not any(np.allclose(g, h, atol=tol) for h in symmetries):
                symmetries.append(g)
    return symmetries


def coil_direction_candidates(u, B_offset, nominal_direction, symmetries,
                              angle_tol_deg=0.01):
    """
    List every single-coil fit equivalent to (u, B_offset) under the NV-axis
    symmetries that is consistent with the coil pointing (for positive
    current) roughly along `nominal_direction`.

    Every g in `symmetries` is applied to both u and B_offset; the
    transformed fits whose u makes the smallest angle with
    `nominal_direction` are kept (all within `angle_tol_deg` of that
    smallest angle). How many remain depends on how the nominal
    direction sits relative to the NV axes: 8 for a coil along a cube
    axis (same angle to all four NV axes; e.g. every coil of a
    lab-aligned diamond), 4 for a coil along a <110>-type direction
    (perpendicular to two NV axes; e.g. x and y of a diamond rotated
    45 deg about z). All share |u|, the component along the nominal
    axis, and the tilt angle, and differ only in which way the coil
    tilts. Single-coil data cannot tell these apart; mixed-current data
    can (see resolve_coil_symmetry_from_mixed_dips()).

    Parameters
    ----------
    u : array-like, shape (3,)
        Fitted field-per-amp vector of one coil, Hz/A.
    B_offset : array-like, shape (3,)
        Background offset fitted together with u, Hz.
    nominal_direction : array-like, shape (3,)
        Rough direction of this coil's field for POSITIVE current.
    symmetries : list of ndarray, shape (3, 3)
        As returned by nv_axis_symmetries().
    angle_tol_deg : float, default 0.01
        Candidates within this many degrees of the best angle are kept.

    Returns
    -------
    candidates : list of dict, each
        {'g': (3, 3) ndarray, 'u': (3,) ndarray, 'B_offset': (3,) ndarray,
         'angle_deg': float}
        Deduplicated (candidates with identical u AND B_offset, which
        happen when u or B_offset has zero or equal-magnitude components,
        appear once).
    """
    u = np.asarray(u, dtype=float)
    B_offset = np.asarray(B_offset, dtype=float)
    nominal = _normalize(nominal_direction)
    u_norm = np.linalg.norm(u)

    transformed = []
    for g in symmetries:
        u_c = g @ u
        cos_angle = np.clip(np.dot(u_c, nominal) / u_norm, -1.0, 1.0) if u_norm > 0 else 1.0
        transformed.append({'g': g, 'u': u_c, 'B_offset': g @ B_offset,
                            'angle_deg': float(np.degrees(np.arccos(cos_angle)))})

    best_angle = min(c['angle_deg'] for c in transformed)
    candidates = []
    for c in transformed:
        if c['angle_deg'] > best_angle + angle_tol_deg:
            continue
        duplicate = any(np.allclose(c['u'], k['u'], rtol=0, atol=1e-9 * max(u_norm, 1.0))
                        and np.allclose(c['B_offset'], k['B_offset'], rtol=0, atol=1e-3)
                        for k in candidates)
        if not duplicate:
            candidates.append(c)
    return candidates


def _all_dip_rms(axes, B_list, observed_dips_list, D=2.87e9, E=0.0, freq_range=None,
                 dip_widths_list=None):
    """
    Unweighted RMS (Hz) over EVERY observed dip at every point of its
    distance to the predicted transitions for that point's field.

    Dips are matched to predictions as in the field fits (one-to-one,
    plus merging of unresolved transitions if `dip_widths_list` is given;
    see _match_dips_one_point()), and each matched dip is compared with
    the mean of its transitions. Observed dips left over (more observed
    than predicted) are scored against their nearest prediction, so a
    candidate cannot look better by predicting fewer in-range
    transitions. `axes` must already be normalized. Returns inf if a
    point has observed dips but no predicted ones, nan if there are no
    observed dips at all.
    """
    if dip_widths_list is None:
        dip_widths_list = [None] * len(observed_dips_list)
    sq = []
    for B, obs, widths in zip(B_list, observed_dips_list, dip_widths_list):
        obs = np.asarray(obs, dtype=float)
        if len(obs) == 0:
            continue
        pred = []
        for axis in axes:
            for f in nv_transition_frequencies(axis, B, D=D, E=E):
                if freq_range is None or (freq_range[0] <= f <= freq_range[1]):
                    pred.append(f)
        if not pred:
            return float('inf')
        pred = np.asarray(pred)
        groups = _match_dips_one_point(pred, obs, widths=widths)
        for c, rows in groups:
            sq.append((np.mean(pred[rows]) - obs[c]) ** 2)
        matched = {c for c, _ in groups}
        for c in range(len(obs)):
            if c not in matched:
                sq.append(np.min((pred - obs[c]) ** 2))
    return float(np.sqrt(np.mean(sq))) if sq else float('nan')


def resolve_coil_symmetry_from_mixed_dips(quantization_axes, coil_candidates, current_vecs,
                                          observed_dips_list, D=2.87e9, E=0.0,
                                          freq_range=None, use_offset=True,
                                          dip_widths_list=None):
    """
    Choose one candidate per coil (from coil_direction_candidates()) so
    that the assembled calibration matrix best explains dips measured at
    MIXED currents (several coils on at once).

    Each coil's single-coil fit is only known up to an NV-axis symmetry,
    and nothing in single-coil data relates one coil's choice to
    another's. At a mixed-current point, though, the spectrum depends on
    how the coils' fields add, so only the true combination (generically)
    reproduces it. Every combination is scored by _all_dip_rms() over all
    mixed points; the lowest wins.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    coil_candidates : list of list of dict
        One candidate list per coil, in coil (column) order, each as
        returned by coil_direction_candidates().
    current_vecs : array_like, shape (n, n_coils)
        Coil currents at each mixed point, A.
    observed_dips_list : list of array_like
        Observed dip centers at each mixed point, Hz.
    D, E, freq_range :
        As in fit_coil_axis_from_dips().
    use_offset : bool, default True
        If True, each combination is scored with B_offset = mean of its
        chosen candidates' B_offset; if False, with B_offset = 0. Use
        False if the single-coil fits were run with a fixed zero offset.
    dip_widths_list : list of array_like, optional
        Fitted HWHM of each observed dip at each mixed point, Hz; enables
        merging of unresolved transitions as in fit_coil_axis_from_dips().

    Returns
    -------
    result : dict
        'choice'          : tuple of int, chosen candidate index per coil.
        'M'               : ndarray (3, n_coils), chosen u's as columns.
        'B_offset'        : ndarray (3,), offset used for scoring the choice.
        'rms'             : float, Hz, all-dip RMS of the choice.
        'runner_up_rms'   : float, Hz, best RMS among combinations whose M
                            differs from the chosen one (by > 1% of the
                            largest column norm); inf if there is none.
        'margin'          : float, runner_up_rms / rms. Large (>> 1) means
                            a clear decision; near 1 means the mixed points
                            do not distinguish the candidates (add more,
                            or larger, mixed currents).
        'n_combinations'  : int.
        'scores'          : list of (rms, choice), sorted best-first.
    """
    axes = [_normalize(a) for a in quantization_axes]
    current_vecs = np.asarray(current_vecs, dtype=float).reshape(len(observed_dips_list), -1)
    n_coils = len(coil_candidates)
    if current_vecs.shape[1] != n_coils:
        raise ValueError(f'current_vecs has {current_vecs.shape[1]} columns but '
                         f'{n_coils} coil candidate lists were given.')

    # === Score every combination ===
    scores = []
    for choice in itertools.product(*[range(len(c)) for c in coil_candidates]):
        chosen = [coil_candidates[j][choice[j]] for j in range(n_coils)]
        M = np.column_stack([c['u'] for c in chosen])
        B_off = (np.mean([c['B_offset'] for c in chosen], axis=0) if use_offset
                 else np.zeros(3))
        B_list = current_vecs @ M.T + B_off
        rms = _all_dip_rms(axes, B_list, observed_dips_list, D=D, E=E, freq_range=freq_range,
                           dip_widths_list=dip_widths_list)
        scores.append((rms if np.isfinite(rms) else float('inf'), choice, M, B_off))
    scores.sort(key=lambda s: s[0])

    best_rms, best_choice, best_M, best_B = scores[0]

    # === Margin against the best genuinely different M ===
    M_tol = 0.01 * max(np.linalg.norm(best_M, axis=0).max(), 1.0)
    runner_up_rms = float('inf')
    for rms, _, M, _ in scores[1:]:
        if np.max(np.abs(M - best_M)) > M_tol:
            runner_up_rms = rms
            break
    margin = runner_up_rms / best_rms if best_rms > 0 else float('inf')

    return {
        'choice': best_choice,
        'M': best_M,
        'B_offset': best_B,
        'rms': best_rms,
        'runner_up_rms': runner_up_rms,
        'margin': margin,
        'n_combinations': len(scores),
        'scores': [(s[0], s[1]) for s in scores],
    }


def fit_coil_matrix_from_dips(quantization_axes, current_vecs, observed_dips_list, M_guess,
                              B_offset_guess=None, D=2.87e9, E=0.0, freq_range=None,
                              dip_errors_list=None, fit_offset=True,
                              max_match_distance=None, bootstrap_max_match_distance=None,
                              max_iterations=20, convergence_tol=1e3, dip_widths_list=None):
    """
    Jointly fit the full coil calibration matrix M (Hz/A, columns = one
    coil each) and background offset under the model

        B(I) = M @ I + B_offset

    to dips measured at arbitrary current vectors I -- typically all
    single-coil sweep points together with a few mixed-current points.
    This is the multi-coil generalization of fit_coil_axis_from_dips()
    (same matching / least-squares loop, bootstrap fallback, and
    convergence criterion; see its docstring).

    Like any local fit it needs a good starting point: M_guess should come
    from resolve_coil_symmetry_from_mixed_dips(), so that every coil's
    off-axis components already sit in the right symmetry-equivalent
    configuration. Starting from a single-coil fit's arbitrary choice
    instead would likely converge to a wrong local minimum.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    current_vecs : array_like, shape (n, n_coils)
        Coil currents at each point, A (e.g. (I_X, I_Y, I_Z)).
    observed_dips_list : list of array_like
        Observed dip centers at each point, Hz.
    M_guess : array_like, shape (3, n_coils)
        Initial guess for M, Hz/A.
    B_offset_guess, D, E, freq_range, dip_errors_list, fit_offset,
    max_match_distance, bootstrap_max_match_distance, max_iterations,
    convergence_tol, dip_widths_list :
        As in fit_coil_axis_from_dips().

    Returns
    -------
    M_fit : ndarray, shape (3, n_coils)
    B_offset_fit : ndarray, shape (3,)
    fit_info : dict
        As in fit_coil_axis_from_dips(); 'per_point_B' is M @ I_k +
        B_offset for every input point.
    """
    return _fit_linear_field_model(
        quantization_axes, current_vecs, observed_dips_list, M_guess,
        B_offset_guess=B_offset_guess, D=D, E=E, freq_range=freq_range,
        dip_errors_list=dip_errors_list, fit_offset=fit_offset,
        max_match_distance=max_match_distance,
        bootstrap_max_match_distance=bootstrap_max_match_distance,
        max_iterations=max_iterations, convergence_tol=convergence_tol,
        dip_widths_list=dip_widths_list)


# =============================================================================
# Automated calibration from rough coil strengths + mixed-current points
# =============================================================================

def estimate_coil_strength_from_dips(quantization_axes, observed_dips, current,
                                     nominal_direction, D=2.87e9, E=0.0, b_max=None,
                                     n_grid=4000):
    """
    Rough field-per-amp of one coil from the dips of ONE single-coil
    spectrum, assuming the field points exactly along `nominal_direction`.

    Fits the single parameter b = |B| (frequency units) of B = b * n_hat
    by minimizing, over all observed dips, the squared distance to the
    NEAREST predicted transition (so several degenerate transitions may
    share one dip -- for a field along a cube axis all four NV
    orientations are nearly degenerate and the spectrum shows ~2 dips).
    A coarse grid over [0, b_max] is followed by a bounded 1-D refinement.

    Meant only as the rough per-coil prior for
    fit_coil_matrix_from_mixed_dips(): a coil tilted by angle t from its
    nominal axis biases the result by roughly (1 - cos t), i.e. ~1% at
    8 deg. ODMR cannot see the sign of B, so the returned strength is
    always positive; the sign convention comes from `nominal_direction`.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    observed_dips : sequence of float
        Fitted dip centers of the spectrum, Hz.
    current : float
        Coil current at which the spectrum was taken, A (nonzero).
    nominal_direction : array-like, shape (3,)
        Nominal direction of this coil's field.
    D, E : float
        Zero-field splitting / strain splitting, Hz.
    b_max : float, optional
        Upper end of the |B| search range, Hz. Defaults to
        3 * max|f_dip - D| + 10 MHz.
    n_grid : int, default 4000
        Number of grid points for the coarse search.

    Returns
    -------
    strength : float
        |B| / |current|, Hz/A.
    info : dict
        'B_magnitude' : float, fitted |B|, Hz.
        'rms'         : float, RMS distance (Hz) of the observed dips to
                        their nearest predicted transition at the fit.
    """
    axes = [_normalize(a) for a in quantization_axes]
    n_hat = _normalize(nominal_direction)
    obs = np.asarray(observed_dips, dtype=float)
    if len(obs) == 0:
        raise ValueError('estimate_coil_strength_from_dips needs at least one dip.')
    if current == 0:
        raise ValueError('current must be nonzero.')
    if b_max is None:
        b_max = 3.0 * float(np.max(np.abs(obs - D))) + 10.0e6

    def _cost(b):
        pred = np.array([f for a in axes
                         for f in nv_transition_frequencies(a, b * n_hat, D=D, E=E)])
        return float(np.sum(np.min((pred[:, None] - obs[None, :]) ** 2, axis=0)))

    grid = np.linspace(0.0, b_max, n_grid)
    costs = np.array([_cost(b) for b in grid])
    i = int(np.argmin(costs))
    step = grid[1] - grid[0]
    res = minimize_scalar(_cost, bounds=(max(0.0, grid[i] - step), grid[i] + step),
                          method='bounded', options={'xatol': 1.0})
    b_fit = float(res.x) if res.fun <= costs[i] else float(grid[i])
    return b_fit / abs(current), {'B_magnitude': b_fit,
                                  'rms': float(np.sqrt(_cost(b_fit) / len(obs)))}


def _batch_transitions(axes, B_array, D=2.87e9, E=0.0):
    """
    Vectorized nv_transition_frequencies() for many trial fields at once.

    Parameters
    ----------
    axes : list of normalized axes.
    B_array : array_like, shape (N, 3), fields in Hz.

    Returns
    -------
    freqs : ndarray, shape (N, 2 * len(axes)) -- (f_lo, f_hi) of each axis
        in turn, Hz.
    """
    B_array = np.asarray(B_array, dtype=float).reshape(-1, 3)
    out = []
    for axis in axes:
        H0 = nv_hamiltonian(axis, np.zeros(3), D=D, E=E)
        H = (H0[None] + B_array[:, 0, None, None] * _SX + B_array[:, 1, None, None] * _SY
             + B_array[:, 2, None, None] * _SZ)
        ev = np.linalg.eigvalsh(H)
        out += [ev[:, 1] - ev[:, 0], ev[:, 2] - ev[:, 0]]
    return np.column_stack(out)


def _fit_field_global(axes, obs, freq_range, D=2.87e9, E=0.0, dip_errors=None,
                      extra_guesses=(), n_directions=2000, n_magnitudes=21, n_refine=5):
    """
    Fit the field at ONE point without needing a good starting guess.

    A grid of directions (Fibonacci sphere) x magnitudes (spanning
    0.9-1.9x the largest |f_dip - D|, which brackets |B| for four
    tetrahedral axes) is scored with a matching-free cost (each observed
    dip's squared distance to its nearest predicted transition). The
    `n_refine` best, mutually distinct grid fields plus `extra_guesses`
    are refined with fit_field_from_dips(); the lowest-RMS result wins.
    Any symmetry image of the true field is an equally good answer.

    Returns
    -------
    (B_fit, rms) or None if every refinement was underdetermined.
    """
    obs = np.asarray(obs, dtype=float)
    k = np.arange(n_directions) + 0.5
    polar = np.arccos(1.0 - 2.0 * k / n_directions)
    azimuth = np.pi * (1.0 + 5.0 ** 0.5) * k
    dirs = np.column_stack([np.cos(azimuth) * np.sin(polar),
                            np.sin(azimuth) * np.sin(polar), np.cos(polar)])
    f_scale = float(np.max(np.abs(obs - D)))
    mags = np.linspace(0.9, 1.9, n_magnitudes) * f_scale
    trial = (mags[:, None, None] * dirs[None, :, :]).reshape(-1, 3)

    pred = _batch_transitions(axes, trial, D=D, E=E)
    cost = np.min((pred[:, :, None] - obs[None, None, :]) ** 2, axis=1).sum(axis=1)

    guesses = list(extra_guesses)
    for i in np.argsort(cost):
        if len(guesses) >= len(extra_guesses) + n_refine:
            break
        B_i = trial[i]
        # skip grid points next to an already-chosen one (same basin)
        if all(np.linalg.norm(B_i - g) > 0.1 * np.linalg.norm(B_i)
               for g in guesses[len(extra_guesses):]):
            guesses.append(B_i)

    best = None
    for B0 in guesses:
        B_k, info_k = fit_field_from_dips(axes, obs, freq_range, B0, D=D, E=E,
                                          dip_errors=dip_errors)
        if info_k['underdetermined']:
            continue
        if best is None or info_k['rms_residual'] < best[1]:
            best = (B_k, float(info_k['rms_residual']))
    return best


def _min_dip_separation(axes, B, D=2.87e9, E=0.0):
    """Smallest spacing (Hz) between any two of the predicted transitions
    for field B; `axes` must already be normalized."""
    f = np.sort([f for a in axes for f in nv_transition_frequencies(a, B, D=D, E=E)])
    return float(np.min(np.diff(f)))


def most_resolved_field_directions(quantization_axes, B_magnitude, n_directions=6,
                                   D=2.87e9, E=0.0, n_grid=3000, exclude_directions=None):
    """
    Field directions at which all ODMR transitions are as well separated
    as possible, for mixed-current calibration points.

    Searches a near-uniform (Fibonacci) grid of directions on the sphere
    for the one that maximizes the smallest spacing between any two
    predicted transitions at |B| = B_magnitude. Its images under the
    NV-axis symmetries (nv_axis_symmetries()) are exactly as well
    resolved; `n_directions` of them, spread as far apart as possible
    (greedy farthest-point selection), are returned, so the calibration
    points also sample very different current vectors.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    B_magnitude : float
        Field magnitude (Hz) at which separations are evaluated.
    n_directions : int, default 6
    D, E : float
    n_grid : int, default 3000
        Number of trial directions in the search.
    exclude_directions : array_like, shape (m, 3), optional
        Directions already used (e.g. measured in an earlier round); the
        returned ones are chosen as far as possible from these too.

    Returns
    -------
    directions : ndarray, shape (n_directions, 3)
        Unit vectors.
    min_separation : float
        Smallest transition spacing (Hz) at the best direction and
        B_magnitude (the same for all returned directions when E = 0).
    """
    axes = [_normalize(a) for a in quantization_axes]

    # === Coarse search for the best-resolved direction ===
    k = np.arange(n_grid) + 0.5
    polar = np.arccos(1.0 - 2.0 * k / n_grid)
    azimuth = np.pi * (1.0 + 5.0 ** 0.5) * k
    trial = np.column_stack([np.cos(azimuth) * np.sin(polar),
                             np.sin(azimuth) * np.sin(polar), np.cos(polar)])
    seps = np.array([_min_dip_separation(axes, B_magnitude * d, D=D, E=E) for d in trial])
    best = trial[int(np.argmax(seps))]

    # === Spread-out symmetry images of it ===
    orbit = []
    for g in nv_axis_symmetries(quantization_axes):
        d = g @ best
        if not any(np.allclose(d, o, atol=1e-6) for o in orbit):
            orbit.append(d)
    reference = ([] if exclude_directions is None
                 else [_normalize(d) for d in np.asarray(exclude_directions, dtype=float)])
    chosen = [] if reference else [orbit[0]]
    while len(chosen) < min(n_directions, len(orbit)):
        min_angle = [min(np.arccos(np.clip(np.dot(o, c), -1.0, 1.0))
                         for c in reference + chosen) for o in orbit]
        chosen.append(orbit[int(np.argmax(min_angle))])
    return np.array(chosen), float(np.max(seps))


def fit_coil_matrix_from_mixed_dips(quantization_axes, current_vecs, observed_dips_list,
                                    M_prior, dip_errors_list=None, dip_widths_list=None,
                                    D=2.87e9, E=0.0, freq_range=None, fit_offset=True,
                                    max_angle_deg=30.0, max_candidates_per_point=4,
                                    n_grid_directions=2000, refine=True,
                                    max_match_distance=5.0e6, max_iterations=20,
                                    convergence_tol=1e3):
    """
    Determine the full coil calibration matrix M (B = M @ I + B_offset)
    from dips measured at several mixed-current points, given only a
    rough prior M_prior (e.g. nominal coil directions times rough
    strengths from estimate_coil_strength_from_dips(), zero tilts).

    A local fit of M started directly from M_prior is fragile: unknown
    coil tilts of several degrees mispredict the dips by several MHz,
    comparable to their spacing, so the dip-to-transition matching can
    lock in wrongly. Instead:

      1. FIELD PER POINT: fit B_k independently at every point, without
         relying on the prior: a grid search over field directions and
         magnitudes with a matching-free cost, followed by
         fit_field_from_dips() refinement of the best grid points (and of
         the prior prediction). With all 8 dips resolved this pins B_k
         down up to the NV-axis symmetries (nv_axis_symmetries()).
      2. PICK THE IMAGES: for each point, the symmetry images g @ B_k
         within `max_angle_deg` of the prior prediction M_prior @ I_k are
         candidates (at most `max_candidates_per_point`, nearest first;
         the nearest is always kept). Every combination across points is
         scored by how well a LINEAR model B_k = M @ I_k + B_offset fits
         the chosen fields (least squares); the best one wins. A wrong
         image at even one point breaks linearity by a large margin.
      3. LINEAR M: M and B_offset from that least-squares solve.
      4. REFINE (if `refine`): fit_coil_matrix_from_dips() on all dips,
         starting from the linear solution.

    The prior is only used to choose among symmetry images, so it may be
    off by a good fraction in strength and by over 10 deg in direction
    (the images of a well-resolved field are >~ 20 deg apart).

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    current_vecs : array_like, shape (n, n_coils)
        Coil currents at each mixed point, A.
    observed_dips_list : list of array_like
        Observed dip centers at each point, Hz.
    M_prior : array_like, shape (3, n_coils)
        Rough prior for M, Hz/A.
    dip_errors_list : list of array_like, optional
        1-sigma center errors, Hz (weights; as in fit_coil_axis_from_dips()).
    dip_widths_list : list of array_like, optional
        Fitted HWHMs, Hz; enables merging of unresolved transitions in
        the refinement (as in fit_coil_axis_from_dips()).
    D, E : float
    freq_range : (float, float), optional
        Scan window; only predicted transitions inside it are matched.
    fit_offset : bool, default True
        Fit B_offset (needs >= 4 usable points; else >= 3).
    max_angle_deg : float, default 30
        Candidate window around each point's prior-predicted field.
    max_candidates_per_point : int, default 4
    n_grid_directions : int, default 2000
        Directions in the step-1 grid search (~4.5 deg spacing at 2000).
    refine : bool, default True
    max_match_distance, max_iterations, convergence_tol :
        For the refinement, as in fit_coil_matrix_from_dips().

    Returns
    -------
    M_fit : ndarray, shape (3, n_coils)
    B_offset_fit : ndarray, shape (3,)
    info : dict
        'point_fields'     : list of ndarray or None -- chosen field per
                             point (None if the point was unusable).
        'point_field_rms'  : list of float -- RMS of each point's own
                             field fit, Hz (nan if unusable).
        'used_points'      : list of int, indices used in steps 2-3.
        'linear_rms'       : float, RMS (Hz, per field component) of the
                             linear model over the chosen fields.
        'runner_up_linear_rms' : float, best linear RMS among choices
                             giving a clearly different M (inf if none).
        'margin'           : float, runner_up_linear_rms / linear_rms.
        'n_combinations'   : int.
        'M_linear', 'B_offset_linear' : step-3 solution.
        'refine_info'      : fit_info of step 4, or None.
    """
    axes = [_normalize(a) for a in quantization_axes]
    M_prior = np.array(M_prior, dtype=float).reshape(3, -1)
    n_coils = M_prior.shape[1]
    current_vecs = np.asarray(current_vecs, dtype=float).reshape(-1, n_coils)
    n_points = len(current_vecs)
    if len(observed_dips_list) != n_points:
        raise ValueError('current_vecs and observed_dips_list must have matching length.')
    if dip_errors_list is None:
        dip_errors_list = [None] * n_points
    fr = freq_range if freq_range is not None else (-np.inf, np.inf)
    symmetries = nv_axis_symmetries(quantization_axes)

    # === Step 1: field at each point, up to symmetry ===
    point_fields, point_rms, candidates, used = [], [], [], []
    for k in range(n_points):
        obs = np.asarray(observed_dips_list[k], dtype=float)
        B_pred = M_prior @ current_vecs[k]
        if len(obs) < 3 or np.linalg.norm(B_pred) == 0:
            point_fields.append(None)
            point_rms.append(float('nan'))
            candidates.append([])
            continue
        best = _fit_field_global(axes, obs, fr, D=D, E=E, dip_errors=dip_errors_list[k],
                                 extra_guesses=[B_pred], n_directions=n_grid_directions)
        if best is None:
            point_fields.append(None)
            point_rms.append(float('nan'))
            candidates.append([])
            continue
        point_rms.append(float(best[1]))

        # Symmetry images near the prior prediction, nearest first.
        images = []
        for g in symmetries:
            img = g @ best[0]
            if not any(np.allclose(img, x, rtol=0, atol=1e-3 * np.linalg.norm(img) + 1.0)
                       for x in images):
                images.append(img)
        angles = [np.degrees(np.arccos(np.clip(
            np.dot(img, B_pred) / (np.linalg.norm(img) * np.linalg.norm(B_pred)), -1, 1)))
            for img in images]
        order = np.argsort(angles)
        near = [images[i] for i in order if angles[i] <= max_angle_deg]
        if not near:
            near = [images[order[0]]]
        candidates.append(near[:max_candidates_per_point])
        point_fields.append(None)  # filled in after step 2
        used.append(k)

    n_needed = 4 if fit_offset else 3
    if len(used) < n_needed:
        raise ValueError(f'Only {len(used)} point(s) gave a usable field fit; need at least '
                         f'{n_needed} to determine M{" and B_offset" if fit_offset else ""}.')

    # === Steps 2-3: choose images by linear consistency, solve for M ===
    A = current_vecs[used]
    if fit_offset:
        A = np.column_stack([A, np.ones(len(used))])

    scored = []
    for choice in itertools.product(*[range(len(candidates[k])) for k in used]):
        Bs = np.array([candidates[k][c] for k, c in zip(used, choice)])
        X, *_ = np.linalg.lstsq(A, Bs, rcond=None)
        rms = float(np.sqrt(np.mean((A @ X - Bs) ** 2)))
        scored.append((rms, choice, X))
    scored.sort(key=lambda s: s[0])

    linear_rms, best_choice, X_best = scored[0]
    M_lin = X_best[:n_coils].T
    B_lin = X_best[n_coils] if fit_offset else np.zeros(3)
    M_tol = 0.01 * max(np.linalg.norm(M_lin, axis=0).max(), 1.0)
    runner_up = next((s[0] for s in scored[1:]
                      if np.max(np.abs(s[2][:n_coils].T - M_lin)) > M_tol), float('inf'))
    margin = runner_up / linear_rms if linear_rms > 0 else float('inf')
    for k, c in zip(used, best_choice):
        point_fields[k] = candidates[k][c]

    # === Step 4: refine on all dips ===
    M_fit, B_fit, refine_info = M_lin, B_lin, None
    if refine:
        M_fit, B_fit, refine_info = fit_coil_matrix_from_dips(
            axes, current_vecs, observed_dips_list, M_lin, B_offset_guess=B_lin,
            D=D, E=E, freq_range=freq_range, dip_errors_list=dip_errors_list,
            dip_widths_list=dip_widths_list, fit_offset=fit_offset,
            max_match_distance=max_match_distance, max_iterations=max_iterations,
            convergence_tol=convergence_tol)

    info = {
        'point_fields': point_fields,
        'point_field_rms': point_rms,
        'used_points': used,
        'linear_rms': linear_rms,
        'runner_up_linear_rms': runner_up,
        'margin': margin,
        'n_combinations': len(scored),
        'M_linear': M_lin,
        'B_offset_linear': B_lin,
        'refine_info': refine_info,
    }
    return M_fit, B_fit, info


# =============================================================================
# Fully automatic helpers: strength from a raw spectrum, field for a window
# =============================================================================

def outer_dip_offset(quantization_axes, B, D=2.87e9, E=0.0):
    """Largest |f - D| (Hz) over all predicted transitions for field B (Hz)."""
    axes = [_normalize(a) for a in quantization_axes]
    f = [f for a in axes for f in nv_transition_frequencies(a, B, D=D, E=E)]
    return float(np.max(np.abs(np.asarray(f) - D)))


def field_magnitude_for_outer_offset(quantization_axes, direction, offset, D=2.87e9, E=0.0,
                                     tol=1.0e3):
    """
    |B| (Hz) such that a field along `direction` puts its outermost
    transition exactly `offset` (Hz) away from D (bisection; the outer
    offset grows monotonically with |B|).
    """
    d = _normalize(direction)
    lo, hi = 0.0, max(2.0 * offset, 1.0e6)
    while outer_dip_offset(quantization_axes, hi * d, D=D, E=E) < offset:
        hi *= 2.0
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if outer_dip_offset(quantization_axes, mid * d, D=D, E=E) < offset:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def estimate_coil_strength_from_spectrum(quantization_axes, freq, signal, current,
                                         nominal_direction, D=2.87e9, E=0.0, b_max=None,
                                         linewidths=(0.5e6, 1.0e6, 2.0e6, 4.0e6), n_grid=800,
                                         min_snr=8.0):
    """
    Rough field-per-amp of one coil directly from a RAW single-coil ODMR
    spectrum -- no dip finding (which is unreliable on these spectra: a
    field along a cube axis gives blends of near-degenerate lines, and a
    fixed-count dip fit then invents spurious dips).

    For a grid of |B| = b along `nominal_direction` and a few trial
    linewidths, the predicted transitions are turned into a template
    spectrum (sum of equal Lorentzian dips), and the template's
    correlation with the measured spectrum is computed; the best (b,
    linewidth) is then refined by a least-squares fit of
    offset - amplitude * template to the data. A coil tilted by t from its
    nominal axis splits the lines slightly, which biases b by roughly
    (1 - cos t) -- fine for a rough prior.

    Parameters
    ----------
    quantization_axes : sequence of array-like, each shape (3,)
    freq, signal : array_like
        Raw spectrum, Hz / arbitrary units (dips downward).
    current : float
        Coil current of the spectrum, A (nonzero).
    nominal_direction : array-like, shape (3,)
    D, E : float
    b_max : float, optional
        Largest |B| tried, Hz. Default: twice the largest |f - D| in the
        window (beyond that no line would be inside it).
    linewidths : sequence of float
        Trial HWHMs, Hz, for the template search.
    n_grid : int, default 800
        Number of |B| grid points.
    min_snr : float, default 8
        Minimum (smoothed) dip depth / noise for the spectrum to count as
        containing dips at all.

    Returns
    -------
    strength : float
        |B| / |current|, Hz/A (nan if no dips were detected).
    info : dict
        'B_magnitude' (Hz), 'linewidth' (fitted HWHM, Hz), 'score'
        (correlation of the best template, 0-1), 'snr', 'has_dips' (bool),
        'outer_offset' (Hz, outermost predicted line from D at the fit).
    """
    axes = [_normalize(a) for a in quantization_axes]
    n_hat = _normalize(nominal_direction)
    freq = np.asarray(freq, dtype=float)
    signal = np.asarray(signal, dtype=float)
    if current == 0:
        raise ValueError('current must be nonzero.')

    # === Is there anything there? ===
    baseline = float(np.percentile(signal, 90))
    y = baseline - signal
    noise = 1.4826 * float(np.median(np.abs(np.diff(signal) - np.median(np.diff(signal))))) / np.sqrt(2)
    y_smooth = np.convolve(y, np.ones(5) / 5.0, mode='same')
    snr = float(np.max(y_smooth) / max(noise / np.sqrt(5.0), 1e-300))
    nan_info = {'B_magnitude': float('nan'), 'linewidth': float('nan'), 'score': 0.0,
                'snr': snr, 'has_dips': False, 'outer_offset': float('nan')}
    if snr < min_snr:
        return float('nan'), nan_info

    # === Template-correlation grid search over (b, linewidth) ===
    if b_max is None:
        b_max = 2.0 * float(np.max(np.abs(freq - D)))
    b_grid = np.linspace(0.0, b_max, n_grid)
    lines = _batch_transitions(axes, b_grid[:, None] * n_hat[None, :], D=D, E=E)
    yc = y - y.mean()
    yc_norm = np.linalg.norm(yc)
    best = (-np.inf, 0.0, linewidths[0])
    for gamma in linewidths:
        T = np.zeros((n_grid, len(freq)))
        for j in range(lines.shape[1]):
            T += gamma ** 2 / ((freq[None, :] - lines[:, j, None]) ** 2 + gamma ** 2)
        Tc = T - T.mean(axis=1, keepdims=True)
        norms = np.linalg.norm(Tc, axis=1)
        score = (Tc @ yc) / np.maximum(norms * yc_norm, 1e-300)
        i = int(np.argmax(score))
        if score[i] > best[0]:
            best = (float(score[i]), float(b_grid[i]), float(gamma))
    score0, b0, gamma0 = best

    # === Least-squares refinement of the full template model ===
    def _model(params):
        b, gamma, amp, off = params
        lines_b = [f for a in axes for f in nv_transition_frequencies(a, b * n_hat, D=D, E=E)]
        t = np.zeros_like(freq)
        for f0 in lines_b:
            t += gamma ** 2 / ((freq - f0) ** 2 + gamma ** 2)
        return off - amp * t

    t0 = baseline - _model((b0, gamma0, 1.0, baseline))
    amp0 = max(float(np.dot(t0, y) / max(np.dot(t0, t0), 1e-300)), 1e-12)
    try:
        res = least_squares(lambda p: _model(p) - signal, x0=[b0, gamma0, amp0, baseline],
                            bounds=([0.0, 0.05e6, 0.0, -np.inf], [np.inf, 50.0e6, np.inf, np.inf]),
                            x_scale=[max(b0, 1e6), gamma0, amp0, abs(baseline) + 1e-12])
        b_fit, gamma_fit = float(res.x[0]), float(res.x[1])
    except Exception:
        b_fit, gamma_fit = b0, gamma0

    return b_fit / abs(current), {
        'B_magnitude': b_fit, 'linewidth': gamma_fit, 'score': score0, 'snr': snr,
        'has_dips': True, 'outer_offset': outer_dip_offset(axes, b_fit * n_hat, D=D, E=E)}


# =============================================================================
# Calibration matrix from user-verified (coil currents, dip frequencies) data
# =============================================================================

def _parse_verified_measurements(measurements, n_coils=3):
    """
    Normalize [(coil_values, peaks), ...] into arrays/lists. coil_values:
    sequence of n_coils currents (A) or {'X','Y','Z'} dict. peaks: sequence
    of dip frequencies (Hz) or a dip-fit dict with 'centers' (+ optional
    'center_errs', 'sigmas').
    """
    current_vecs, obs, errs, widths = [], [], [], []
    for i, item in enumerate(measurements):
        try:
            coil_values, peaks = item
        except (TypeError, ValueError):
            raise ValueError(f'measurement {i} must be a (coil_values, peaks) pair.')
        if isinstance(coil_values, dict):
            coil_values = [coil_values[a] for a in ('X', 'Y', 'Z')[:n_coils]]
        I = np.asarray(coil_values, dtype=float).ravel()
        if I.size != n_coils:
            raise ValueError(f'measurement {i}: expected {n_coils} coil currents, got {I.size}.')
        if isinstance(peaks, dict):
            c = np.asarray(peaks['centers'], dtype=float).ravel()
            e = peaks.get('center_errs')
            w = peaks.get('sigmas')
        else:
            c = np.asarray(peaks, dtype=float).ravel()
            e = w = None
        if not np.all(np.isfinite(c)):
            raise ValueError(f'measurement {i}: non-finite dip frequency.')
        current_vecs.append(I)
        obs.append(c)
        errs.append(None if e is None else np.asarray(e, dtype=float).ravel())
        widths.append(None if w is None else np.abs(np.asarray(w, dtype=float).ravel()))

    # Dip errors: use them only if EVERY point has them (mixing real errors
    # with unit placeholders would make the weighting meaningless).
    if all(e is not None and len(e) == len(c) for e, c in zip(errs, obs)):
        pool = np.concatenate([e[np.isfinite(e) & (e > 0)] for e in errs] or [np.array([])])
        fill = float(np.median(pool)) if pool.size else 1.0
        errs = [np.where(np.isfinite(e) & (e > 0), e, fill) for e in errs]
    else:
        errs = None
    widths = [w if (w is not None and len(w) == len(c)) else None for w, c in zip(widths, obs)]
    return np.array(current_vecs), obs, errs, widths


def _matrix_fit_covariance(axes, current_vecs, obs, errs, widths, M, B0, D, E, freq_range,
                           fit_offset, max_match_distance):
    """
    Parameter covariance of (M, B_offset) at a converged fit, from the
    Jacobian of the weighted residuals with the final dip matching held
    fixed. Returns (cov, reduced_chi2, n_residuals, eigenvalues of J^T J,
    eigenvectors) -- cov is None if the problem is singular.
    """
    n_coils = M.shape[1]
    fr = freq_range if freq_range is not None else (-np.inf, np.inf)
    entries = []
    for k, o in enumerate(obs):
        if len(o) == 0:
            continue
        B = M @ current_vecs[k] + B0
        pred = [(ai, br, f) for ai, a in enumerate(axes)
                for br, f in enumerate(nv_transition_frequencies(a, B, D=D, E=E))
                if fr[0] <= f <= fr[1]]
        if not pred:
            continue
        pf = np.array([p[2] for p in pred])
        for c, rows in _match_dips_one_point(pf, o, distance=max_match_distance,
                                             widths=widths[k]):
            err = errs[k][c] if errs is not None else 1.0
            entries.append((k, [(pred[r][0], pred[r][1]) for r in rows], o[c], err))

    n_M = 3 * n_coils

    def _res(p):
        M_ = p[:n_M].reshape(3, n_coils)
        B_ = p[n_M:n_M + 3] if fit_offset else B0
        out = np.empty(len(entries))
        for i, (k, trans, o, err) in enumerate(entries):
            B = M_ @ current_vecs[k] + B_
            fs = [nv_transition_frequencies(axes[ai], B, D=D, E=E)[br] for ai, br in trans]
            out[i] = (np.mean(fs) - o) / err
        return out

    p0 = np.concatenate([M.ravel(), B0]) if fit_offset else M.ravel().copy()
    r0 = _res(p0)
    J = np.empty((len(entries), len(p0)))
    h = 1.0e3  # Hz/A for M entries, Hz for B_offset
    for j in range(len(p0)):
        dp = np.zeros_like(p0)
        dp[j] = h
        J[:, j] = (_res(p0 + dp) - _res(p0 - dp)) / (2 * h)
    JTJ = J.T @ J
    evals, evecs = np.linalg.eigh(JTJ)
    dof = len(entries) - len(p0)
    chi2_red = float(np.sum(r0 ** 2) / dof) if dof > 0 else float('nan')
    if dof <= 0 or evals[0] <= 1e-12 * evals[-1]:
        return None, chi2_red, len(entries), evals, evecs
    cov = np.linalg.inv(JTJ) * (chi2_red if np.isfinite(chi2_red) else 1.0)
    return cov, chi2_red, len(entries), evals, evecs


def load_verified_measurements(folder='FittedCoilODMRs'):
    """
    Load verified (coil currents, fitted dips) measurements from a folder
    of .npz files, e.g. as written by coil_characterization's
    review_odmr_folder(). Every .npz with 'current_vec' and 'centers'
    arrays is used ('center_errs' and 'sigmas' too, if present); files are
    taken in natural order of their names (fitted_odmr_2 before
    fitted_odmr_10).

    Returns
    -------
    measurements : list of (coil_values, peaks)
        Ready for fit_coil_matrix_from_measurements(); `peaks` are dicts
        with 'centers' (+ 'center_errs', 'sigmas'), plus 'file'.
    """
    import os
    import re
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ValueError(f'Folder not found: {folder}')

    def _natural_key(name):
        return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', name)]

    measurements = []
    for name in sorted((n for n in os.listdir(folder) if n.endswith('.npz')), key=_natural_key):
        with np.load(os.path.join(folder, name), allow_pickle=False) as d:
            if 'current_vec' not in d.files or 'centers' not in d.files:
                continue
            peaks = {'centers': np.asarray(d['centers'], dtype=float), 'file': name}
            for key in ('center_errs', 'sigmas'):
                if key in d.files:
                    peaks[key] = np.asarray(d[key], dtype=float)
            measurements.append((tuple(float(c) for c in d['current_vec']), peaks))
    if not measurements:
        raise ValueError(f'No verified measurements (.npz with current_vec and centers) in '
                         f'{folder}.')
    return measurements


def fit_coil_matrix_from_measurements(measurements, quantization_axes, nominal_axes=None,
                                      D=2.87e9, E=0.0, freq_range=None, fit_offset=True,
                                      coil_strength_guess=None, max_match_distance=5.0e6,
                                      min_dips_for_field=6, n_refine=12,
                                      min_delta_chi2=25.0, n_ambiguity_checks=4,
                                      outlier_factor=3.0, drop_outliers=True, max_rms=None,
                                      verbose=True):
    """
    Most likely coil calibration matrix M and background offset
    (B = M @ I + B_offset) from an arbitrary set of VERIFIED measurements:
    pairs of (coil currents, dip frequencies), e.g. with the dips picked
    and fitted by hand via fit_odmr_dips_interactive().

    Any mix of points works -- single-coil points, mixed-current points,
    any currents -- as long as together they determine M (checked; see
    Raises). No rough coil strengths are needed: a prior is estimated from
    the data itself.

    Procedure
    ---------
    1. Identifiability checks (see Raises).
    2. Rough per-coil strengths from the field MAGNITUDE at each point
       (symmetry-invariant): |B| from single-coil points via
       estimate_coil_strength_from_dips(), from other points with >= 3
       dips via a prior-free field fit; then |B_k|^2 ~ sum_j s_j^2 I_kj^2
       solved for s_j^2 (non-negative least squares). Skipped if
       `coil_strength_guess` is given.
    3. Starting M, by every route the data allow:
       A. mixed-current points with >= `min_dips_for_field` dips:
          per-point field fits -> symmetry images -> linear solve
          (fit_coil_matrix_from_mixed_dips());
       B. single-coil points for all coils + any mixed point: per-coil
          axis fits -> symmetry candidates -> best combinations on the
          mixed points (resolve_coil_symmetry_from_mixed_dips()).
    4. Joint fit of M and B_offset to ALL dips from each starting point
       (fit_coil_matrix_from_dips()); the lowest all-dip RMS wins.
    5. Checks on the result: parameter uncertainties from the fit
       Jacobian (error if singular); whether a symmetry-equivalent M
       (some coils' tilts mirrored, see coil_direction_candidates())
       still fits almost as well after refitting -- i.e. the data cannot
       tell which way those coils tilt (error); and points fitting much
       worse than the rest (likely mis-picked dips; warning, and by
       default excluded and re-fitted before the other checks).

    Parameters
    ----------
    measurements : sequence of (coil_values, peaks), or str
        A folder path is loaded with load_verified_measurements().
        coil_values : (I_X, I_Y, I_Z) in A, or {'X': .., 'Y': .., 'Z': ..}.
        peaks : dip frequencies in Hz, or a dip-fit dict as returned by
            fit_odmr_dips_interactive() / fit_lorentzian_dips_guided()
            ('centers', and if present 'center_errs' -> weights and
            'sigmas' -> merging of unresolved lines; see
            fit_coil_axis_from_dips()). List every dip you trust; a dip
            hiding several unresolved lines should be listed ONCE.
    quantization_axes : sequence of array-like, each shape (3,)
    nominal_axes : dict, optional
        Positive-current field direction of each coil; defaults to
        +x/+y/+z. Used to fix the overall orientation (and in route B).
    D, E : float
    freq_range : (float, float), optional
        Scan window, Hz. Predicted transitions outside it are not
        expected to be observed. Recommended if dips can leave the window.
    fit_offset : bool, default True
        Fit B_offset (12 unknowns) or hold it at zero (9 unknowns).
    coil_strength_guess : dict, optional
        {'X': Hz/A, ...} rough strengths, instead of estimating them.
    max_match_distance : float, default 5e6
        Hz; dips farther than this from any predicted transition are left
        unmatched during the fit.
    min_dips_for_field : int, default 6
        Minimum dips for a mixed point to be used in route A.
    n_refine : int, default 12
        Starting combinations refined in route B.
    min_delta_chi2 : float, default 25
        Raise if, after refitting, an M with some coils' tilts mirrored
        fits worse than the result by less than this chi^2 (25 ~ 5 sigma;
        chi^2 scaled by the fit's own residual variance).
    n_ambiguity_checks : int, default 4
        Number of most competitive mirrored-tilt alternatives refitted
        for that test.
    outlier_factor : float, default 3.0
        Flag points whose RMS exceeds this many times the median (and
        50 kHz).
    drop_outliers : bool, default True
        Exclude flagged points and re-fit (with a warning) -- one bad
        point otherwise distorts M and weakens every check. Only up to
        max(1, 10%) of the points are ever dropped: more disagreeing
        points mean the fit itself is unreliable, which raises instead.
    max_rms : float, optional
        Hz; raise if the final all-dip RMS exceeds this. Default: 10% of
        the median fitted dip HWHM ('sigmas'), or 200 kHz if no widths
        are given. A correct M reproduces verified dips far better than
        that; a wrong tilt combination typically does not.
    verbose : bool, default True

    Returns
    -------
    M : ndarray, shape (3, 3)
        Hz/A, columns ordered (X, Y, Z).
    B_offset : ndarray, shape (3,)
        Hz (zeros if fit_offset=False).
    info : dict
        'M_err', 'B_offset_err' : 1-sigma uncertainties (same shapes).
        'cov'            : full parameter covariance (M row-major, then B).
        'rms'            : all-dip RMS deviation, Hz.
        'reduced_chi2'   : reduced chi^2 (meaningful if dip errors given).
        'point_rms'      : list of per-point RMS, Hz (nan if excluded).
        'outlier_points' : indices of points flagged as likely mis-picked.
        'dropped_points' : indices excluded from the result.
        'ambiguity_delta_chi2' : chi^2 by which the best mirrored-tilt
                           alternative fits worse (inf if none competes).
        'route'          : 'A' or 'B', the route of the winning start.
        'routes_tried'   : {route: all-dip RMS}.
        'strength_prior' : {axis: Hz/A}.
        'fit_info'       : fit_info of the final joint fit.
        'warnings'       : list of str.

    Raises
    ------
    ValueError
        If the measurements cannot determine M: fewer dips than unknowns;
        a coil never energized; current vectors not spanning all coils
        (plus offset); no point with two or more coils on (the direction
        of each coil's tilt is then undetermined); no usable starting
        route; a singular fit; the fit disagreeing with more than
        max(1, 10%) of the points; a final RMS above `max_rms`; or a
        symmetry-equivalent M fitting as well
        as the result (naming the coils concerned). The message says what
        to add.
    """
    required = ('X', 'Y', 'Z')
    if isinstance(measurements, str):
        measurements = load_verified_measurements(measurements)
    axes = [_normalize(a) for a in quantization_axes]
    if nominal_axes is None:
        nominal_axes = {'X': (1.0, 0.0, 0.0), 'Y': (0.0, 1.0, 0.0), 'Z': (0.0, 0.0, 1.0)}
    nominal = [_normalize(nominal_axes[a]) for a in required]
    warnings_list = []

    def _say(msg):
        if verbose:
            print(msg)

    def _warn(msg):
        warnings_list.append(msg)
        print(f'WARNING: {msg}')

    # === 1. Identifiability checks ===
    I, obs, errs, widths = _parse_verified_measurements(measurements)
    n_pts = len(obs)
    n_params = 12 if fit_offset else 9
    n_dips = sum(len(o) for o in obs)
    if n_pts == 0:
        raise ValueError('No measurements given.')
    if n_dips < n_params:
        raise ValueError(f'Only {n_dips} dips in total for {n_params} unknowns '
                         f'({"M and B_offset" if fit_offset else "M"}); add measurements.')
    has_dips = np.array([len(o) > 0 for o in obs])
    I_used = I[has_dips]
    never = [required[j] for j in range(3) if not np.any(np.abs(I_used[:, j]) > 0)]
    if never:
        raise ValueError(f'Coil(s) {never} never carry current in any measurement with dips, '
                         f'so their columns of M are undetermined.')
    A = np.column_stack([I_used, np.ones(len(I_used))]) if fit_offset else I_used
    if np.linalg.matrix_rank(A, tol=1e-9 * max(np.abs(A).max(), 1.0)) < A.shape[1]:
        raise ValueError('The current vectors do not span all coils'
                         + (' plus the offset (e.g. every point uses the same current '
                            'pattern, or no two points differ independently)' if fit_offset
                            else '') + '; M is undetermined. Add points with different '
                         'current combinations.')
    n_on = np.sum(np.abs(I) > 0, axis=1)
    mixed = [k for k in range(n_pts) if has_dips[k] and n_on[k] >= 2]
    if not mixed:
        raise ValueError('No measurement has two or more coils on. Single-coil data fix each '
                         "coil's strength and tilt ANGLE but not which way it tilts (an exact "
                         'NV-axis symmetry), so M is not determined. Add a few points with '
                         'several coils on at once (all dips resolved).')
    fr = freq_range if freq_range is not None else (-np.inf, np.inf)
    _say(f'{n_pts} measurement(s), {n_dips} dips, {len(mixed)} with several coils on; '
         f'{n_params} unknowns.')

    # === 2. Rough strengths from field magnitudes ===
    if coil_strength_guess is not None:
        strengths = np.array([abs(coil_strength_guess[a]) for a in required], dtype=float)
    else:
        rows, mags, I_used_rows = [], [], []
        for k in range(n_pts):
            if not has_dips[k]:
                continue
            on = np.flatnonzero(np.abs(I[k]) > 0)
            if len(on) == 1:
                j = on[0]
                s, _ = estimate_coil_strength_from_dips(axes, obs[k], I[k, j], nominal[j],
                                                        D=D, E=E)
                b = s * abs(I[k, j])
            elif len(obs[k]) >= 3:
                best = _fit_field_global(axes, obs[k], fr, D=D, E=E)
                if best is None:
                    continue
                b = float(np.linalg.norm(best[0]))
            else:
                continue
            rows.append(I[k] ** 2)
            mags.append(b ** 2)
            I_used_rows.append(I[k])
        from scipy.optimize import nnls
        rows, mags = np.array(rows).reshape(-1, 3), np.array(mags)
        Iu = np.array(I_used_rows).reshape(-1, 3)
        Isq = np.abs(Iu)  # |I_j| per point
        # Preferred: full quadratic form |B|^2 = I^T G I (includes the cross terms
        # u_i.u_j of non-orthogonal coils) -- needs 6 independent combinations.
        A6 = np.column_stack([rows, 2 * Iu[:, 0] * Iu[:, 1], 2 * Iu[:, 0] * Iu[:, 2],
                              2 * Iu[:, 1] * Iu[:, 2]])
        strengths = None
        if len(rows) >= 6 and np.linalg.matrix_rank(A6) == 6:
            g, *_ = np.linalg.lstsq(A6, mags, rcond=None)
            if np.all(g[:3] > 0):
                strengths = np.sqrt(g[:3])
        if strengths is None:
            if len(rows) < 3 or np.linalg.matrix_rank(rows) < 3:
                n_patterns = len({tuple(np.round(r, 6)) for r in Isq})
                raise ValueError(
                    f'Cannot estimate the coil strengths from the data: the points use only '
                    f'{n_patterns} distinct pattern(s) of current MAGNITUDES (|I_X|, |I_Y|, '
                    f'|I_Z|), e.g. sign variations of the same currents (as from '
                    f'plan_spread_currents), which cannot separate three strengths. Pass '
                    f'coil_strength_guess, or add points with different current magnitudes '
                    f'(e.g. the same pattern at a lower overall current, or single-coil points).')
            s2, _ = nnls(rows, mags)
            strengths = np.sqrt(s2)
        if np.any(strengths <= 0):
            raise ValueError(f'Could not estimate a strength for coil(s) '
                             f'{[required[j] for j in range(3) if strengths[j] <= 0]} from the '
                             f'data; pass coil_strength_guess.')
    M_prior = np.column_stack([strengths[j] * nominal[j] for j in range(3)])
    _say('Strength prior (MHz/A): ' + ', '.join(f'{a} {strengths[j] / 1e6:.2f}'
                                                 for j, a in enumerate(required)))

    def _refine(M0, B00):
        return fit_coil_matrix_from_dips(
            axes, I, obs, M0, B_offset_guess=B00, D=D, E=E, freq_range=freq_range,
            dip_errors_list=errs, dip_widths_list=widths, fit_offset=fit_offset,
            max_match_distance=max_match_distance)

    def _score(info):
        rms = info['rms_residual_all_dips']
        return (info['underdetermined'], rms if np.isfinite(rms) else np.inf)

    results = {}   # route -> (M, B, fit_info)

    # === 3A. Route A: rich mixed points -> per-point fields -> linear M ===
    rich = [k for k in mixed if len(obs[k]) >= min_dips_for_field]
    n_needed = 4 if fit_offset else 3
    A_rich = (np.column_stack([I[rich], np.ones(len(rich))]) if fit_offset else I[rich]) \
        if rich else np.zeros((0, n_needed))
    if len(rich) >= n_needed and np.linalg.matrix_rank(A_rich) == A_rich.shape[1]:
        try:
            M_lin, B_lin, info_A = fit_coil_matrix_from_mixed_dips(
                axes, I[rich], [obs[k] for k in rich], M_prior,
                dip_errors_list=None if errs is None else [errs[k] for k in rich],
                dip_widths_list=[widths[k] for k in rich], D=D, E=E,
                freq_range=freq_range, fit_offset=fit_offset, refine=False)
            results['A'] = _refine(M_lin, B_lin)
            _say(f"Route A ({len(rich)} well-resolved mixed point(s)): all-dip RMS "
                 f"{results['A'][2]['rms_residual_all_dips'] / 1e3:.1f} kHz.")
        except ValueError as exc:
            _say(f'Route A not usable: {exc}')

    # === 3B. Route B: single-coil axis fits + symmetry resolution ===
    single = {j: [k for k in range(n_pts) if has_dips[k] and n_on[k] == 1
                  and abs(I[k, j]) > 0] for j in range(3)}
    n_axis_params = 6 if fit_offset else 3
    if all(sum(len(obs[k]) for k in single[j]) >= n_axis_params for j in range(3)):
        symmetries = nv_axis_symmetries(axes)
        candidates = []
        for j in range(3):
            ks = single[j]
            u, b, _ = fit_coil_axis_from_dips_multistart(
                axes, I[ks, j], [obs[k] for k in ks], M_prior[:, j], D=D, E=E,
                freq_range=freq_range,
                dip_errors_list=None if errs is None else [errs[k] for k in ks],
                dip_widths_list=[widths[k] for k in ks], fit_offset=fit_offset,
                max_match_distance=max_match_distance)
            candidates.append(coil_direction_candidates(u, b, nominal[j], symmetries))
        resolution = resolve_coil_symmetry_from_mixed_dips(
            axes, candidates, I[mixed], [obs[k] for k in mixed], D=D, E=E,
            freq_range=freq_range, use_offset=fit_offset,
            dip_widths_list=[widths[k] for k in mixed])
        starts = []
        for _, choice in resolution['scores']:
            M0 = np.column_stack([candidates[j][choice[j]]['u'] for j in range(3)])
            B00 = (np.mean([candidates[j][choice[j]]['B_offset'] for j in range(3)], axis=0)
                   if fit_offset else np.zeros(3))
            if all(np.max(np.abs(M0 - s[0])) > 0.01 * np.linalg.norm(M0, axis=0).max()
                   for s in starts):
                starts.append((M0, B00))
            if len(starts) >= n_refine:
                break
        refined = [_refine(M0, B00) for M0, B00 in starts]
        results['B'] = min(refined, key=lambda r: _score(r[2]))
        _say(f"Route B (single-coil fits + {len(mixed)} mixed point(s)): all-dip RMS "
             f"{results['B'][2]['rms_residual_all_dips'] / 1e3:.1f} kHz.")

    if not results:
        raise ValueError(
            f'Not enough information for a starting estimate of M. Provide EITHER at least '
            f'{n_needed} points with several coils on and >= {min_dips_for_field} resolved dips '
            f'each (with current vectors spanning all coils{" plus offset" if fit_offset else ""}),'
            f' OR single-coil points for every coil (>= {n_axis_params} dips per coil in total) '
            f'plus at least one point with several coils on.')

    route = min(results, key=lambda r: _score(results[r][2]))
    M, B_offset, fit_info = results[route]
    if not fit_offset:
        B_offset = np.zeros(3)
    if fit_info['underdetermined']:
        raise ValueError('The joint fit matched too few dips for the number of unknowns; '
                         'check that the listed dips belong to these currents and D/E.')

    # === 5a. Outliers (likely mis-picked dips): flag, optionally drop and re-fit ===
    def _point_rms(M_, B_, keep):
        point_B = I @ M_.T + B_
        return [(_all_dip_rms(axes, [point_B[k]], [obs[k]], D=D, E=E, freq_range=freq_range,
                              dip_widths_list=[widths[k]]) if (has_dips[k] and k in keep)
                 else float('nan')) for k in range(n_pts)]

    keep = set(range(n_pts))
    point_rms = _point_rms(M, B_offset, keep)
    finite = [r for r in point_rms if np.isfinite(r)]
    med = float(np.median(finite)) if finite else float('nan')
    outliers = [k for k, r in enumerate(point_rms)
                if np.isfinite(r) and r > outlier_factor * med and r > 50.0e3]
    dropped = []
    n_outlier_max = max(1, int(0.1 * int(np.sum(has_dips))))
    if len(outliers) > n_outlier_max:
        # Too many disagreeing points to be occasional mis-picks: the fitted
        # M itself is most likely wrong, and dropping them would only hide it.
        raise ValueError(f'The best fit is inconsistent with {len(outliers)} of '
                         f'{int(np.sum(has_dips))} points ({outliers}: '
                         f'{", ".join(f"{point_rms[k] / 1e3:.0f}" for k in outliers)} kHz vs '
                         f'median {med / 1e3:.0f} kHz). Either several dips are mis-picked, or '
                         f'the data do not pin down the coil tilt directions (this happens with '
                         f'very few mixed-current points). Check those points, and add points '
                         f'with several coils on at once.')
    if outliers:
        _warn(f'point(s) {outliers} fit much worse than the rest '
              f'({", ".join(f"{point_rms[k] / 1e3:.0f}" for k in outliers)} kHz vs median '
              f'{med / 1e3:.0f} kHz): likely a mis-picked or missing dip -- re-check '
              + ('them; they are excluded from the result.' if drop_outliers else 'them.'))
        if drop_outliers:
            dropped = list(outliers)
            keep -= set(dropped)
            kept = sorted(keep)
            I_k = I[kept]
            A_k = np.column_stack([I_k, np.ones(len(kept))]) if fit_offset else I_k
            if np.linalg.matrix_rank(A_k) < A_k.shape[1] or \
                    sum(len(obs[k]) for k in kept) < n_params:
                raise ValueError(f'Without the outlier point(s) {dropped} the data no longer '
                                 f'determine M; fix those dips or add measurements.')

    kept = sorted(keep)
    I_k, obs_k = I[kept], [obs[k] for k in kept]
    errs_k = None if errs is None else [errs[k] for k in kept]
    widths_k = [widths[k] for k in kept]

    def _refine_kept(M0, B00):
        return fit_coil_matrix_from_dips(
            axes, I_k, obs_k, M0, B_offset_guess=B00, D=D, E=E, freq_range=freq_range,
            dip_errors_list=errs_k, dip_widths_list=widths_k, fit_offset=fit_offset,
            max_match_distance=max_match_distance)

    if dropped:
        M, B_offset, fit_info = _refine_kept(M, B_offset)
        if not fit_offset:
            B_offset = np.zeros(3)
        point_rms = _point_rms(M, B_offset, keep)

    # === 5b. Absolute fit quality ===
    # A correct M reproduces verified dips far better than a linewidth;
    # a wrong tilt combination typically misses by a good fraction of one.
    rms_kept = _all_dip_rms(axes, I_k @ M.T + B_offset, obs_k, D=D, E=E,
                            freq_range=freq_range, dip_widths_list=widths_k)
    hw = [w for w in widths_k if w is not None and len(w)]
    default_rms_limit = 0.1 * float(np.median(np.concatenate(hw))) if hw else 200.0e3
    rms_limit = default_rms_limit if max_rms is None else float(max_rms)
    if not rms_kept <= rms_limit:
        raise ValueError(f'No M consistent with the data was found: the best fit misses the '
                         f'dips by {rms_kept / 1e3:.0f} kHz RMS (limit {rms_limit / 1e3:.0f} kHz, '
                         f'~10% of the dip half-width). This usually means too few points with '
                         f'several coils on ({len(mixed)} given; >= 4 with resolved dips is '
                         f'recommended) to determine the coil tilt directions, or several '
                         f'mis-picked dips. If the dips are known to be this uncertain, pass '
                         f'a larger max_rms.')

    # === 5c. Uncertainties ===
    cov, chi2_red, n_res, evals, evecs = _matrix_fit_covariance(
        axes, I_k, obs_k, errs_k, widths_k, M, B_offset, D, E, freq_range, fit_offset,
        max_match_distance)
    if cov is None:
        weak = evecs[:, 0]
        names = [f'M[{r},{c}]' for r in range(3) for c in range(3)] + \
                (['B_x', 'B_y', 'B_z'] if fit_offset else [])
        top = [names[i] for i in np.argsort(-np.abs(weak))[:3]]
        raise ValueError(f'The fit is singular ({n_res} matched dips for {n_params} unknowns): '
                         f'the data do not constrain the combination dominated by {top}. Add '
                         f'measurements with different current combinations.')
    sig = np.sqrt(np.diag(cov))
    M_err = sig[:9].reshape(3, 3)
    B_err = sig[9:12] if fit_offset else np.zeros(3)

    # === 5d. Symmetry ambiguity: could mirrored coil tilts fit as well? ===
    n_scored = sum(len(o) for o in obs_k)
    dof = max(n_scored - n_params, 1)
    rms = _all_dip_rms(axes, I_k @ M.T + B_offset, obs_k, D=D, E=E, freq_range=freq_range,
                       dip_widths_list=widths_k)
    symmetries = nv_axis_symmetries(axes)
    cands = [coil_direction_candidates(M[:, j], B_offset, nominal[j], symmetries)
             for j in range(3)]
    col_tol = 0.01 * np.linalg.norm(M, axis=0).max()
    alternatives = []
    for combo in itertools.product(*[range(len(c)) for c in cands]):
        M_alt = np.column_stack([cands[j][combo[j]]['u'] for j in range(3)])
        if np.max(np.abs(M_alt - M)) <= col_tol:
            continue
        rms_alt = _all_dip_rms(axes, I_k @ M_alt.T + B_offset, obs_k, D=D, E=E,
                               freq_range=freq_range, dip_widths_list=widths_k)
        alternatives.append((rms_alt, M_alt))
    alternatives.sort(key=lambda a: a[0])

    # Refine the most competitive alternatives: if one still fits about as
    # well, the data cannot tell which way (some) coils tilt.
    min_dchi2, worst_cols = float('inf'), []
    for _, M_alt in alternatives[:n_ambiguity_checks]:
        M_r, B_r, info_r = _refine_kept(M_alt, B_offset)
        if np.max(np.abs(M_r - M)) <= col_tol or info_r['underdetermined']:
            continue  # converged back to the best solution
        rms_r = info_r['rms_residual_all_dips']
        if not np.isfinite(rms_r):
            continue
        dchi2 = dof * ((rms_r / rms) ** 2 - 1.0) if rms > 0 else float('inf')
        if dchi2 < min_dchi2:
            min_dchi2 = dchi2
            worst_cols = [('X', 'Y', 'Z')[j] for j in range(3)
                          if np.max(np.abs(M_r[:, j] - M[:, j])) > col_tol]
    if min_dchi2 < min_delta_chi2:
        poor_fit_note = (
            f' NOTE: the best fit itself misses the dips by {rms / 1e3:.0f} kHz RMS, more than '
            f'the default limit -- with such a misfit every alternative fits about equally '
            f'badly, so this test is inconclusive. First find out why no linear M fits the '
            f'data (actual vs recorded currents, coil nonlinearity, dip picks).'
            if rms > default_rms_limit else '')
        raise ValueError(f'Not enough information to fix which way coil(s) {worst_cols} tilt: '
                         f'an M with their tilt mirrored fits almost as well (delta chi^2 = '
                         f'{min_dchi2:.1f} < {min_delta_chi2}). Add points with several coils on '
                         f'at once in which coil(s) {worst_cols} contribute a substantial part '
                         f'of the field, with all dips resolved.' + poor_fit_note)

    if verbose:
        print(f'Best route: {route}; all-dip RMS {rms / 1e3:.1f} kHz over {n_res} matched dips'
              + (f', reduced chi^2 {chi2_red:.2f}' if errs is not None else '')
              + (f'; nearest mirrored-tilt alternative worse by delta chi^2 = {min_dchi2:.0f}'
                 if np.isfinite(min_dchi2) else '; no competing mirrored-tilt alternative')
              + (f'; excluded point(s) {dropped}' if dropped else '') + '.')
        print('M (MHz/A) +/- 1 sigma:')
        for r in range(3):
            print('  [' + '  '.join(f'{M[r, c] / 1e6:9.4f} +/- {M_err[r, c] / 1e6:.4f}'
                                    for c in range(3)) + ' ]')
        if fit_offset:
            print(f'B_offset (MHz): {np.round(B_offset / 1e6, 4)} +/- '
                  f'{np.round(B_err / 1e6, 4)}  ({np.round(field_freq_to_gauss(B_offset), 4)} G)')

    info = {
        'M_err': M_err, 'B_offset_err': B_err, 'cov': cov, 'rms': rms,
        'reduced_chi2': chi2_red, 'point_rms': point_rms, 'outlier_points': outliers,
        'dropped_points': dropped, 'ambiguity_delta_chi2': min_dchi2, 'route': route,
        'routes_tried': {r: results[r][2]['rms_residual_all_dips'] for r in results},
        'strength_prior': dict(zip(required, strengths)), 'fit_info': fit_info,
        'warnings': warnings_list,
    }
    return M, B_offset, info
