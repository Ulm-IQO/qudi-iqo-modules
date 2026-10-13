# -*- coding: utf-8 -*-
"""
coil_linearity_analysis.py

Analysis of the coil-linearity data taken with run_coil_linearity.py
(coil_characterization.run_linearity_scans): per-coil current scans, return
points (hysteresis) and a scaled mixed-current scan.

Standalone: needs only nv_field_fitting.py (no qudi).

Two stages:

  1. `fit_spectrum_field` -- the field B (and D) of every point, fitted
     DIRECTLY to its raw spectrum: sloped baseline minus 8 Lorentzian dips at
     the transition frequencies of the 4 NV axes for field B (free amplitude
     and width per dip). A soft-L1 loss makes it robust against the narrow
     spurious dips in the spectra. Several starting fields per point (the
     prediction from the coil model, neighbouring points, a matching-free
     grid search on detected dips); the best fit wins. Any of the 48
     symmetry images of B gives the same spectrum.

  2. `fit_field_model` -- B(I) = B0 + sum_c (u_c I_c + w_c I_c^2 + v_c I_c^3)
     (which terms: `terms`), fitted to the per-point fields by weighted least
     squares, with every point represented by whichever of its symmetry
     images is closest to the model (alternating assignment / fit).
     Single-coil data fix each coil only up to the NV-axis symmetries
     ("which way it tilts"); the mixed points decide (`resolve_coil_tilts`
     tries every near-nominal combination and keeps the best).

  3. `joint_spectral_fit` -- one field model fitted to many raw spectra at
     once; `fit_lines_model` / `jackknife_lines_model` -- the same on the
     sorted stage-1 line frequencies (fast, symmetry invariant).

Commanded vs physical currents: the saved currents are setpoints, and the
polarity relays of X and Y were swapped in the 2026-10-05 data (see
RELAY_OF_COIL_2026_10_05); `polarity_relay_history` + `physical_currents`
give the currents that actually flowed. The resulting calibration is written
to CoilCalibration/ and used through coil_field_calibration.py.

All fields and frequencies are in MHz internally (B in frequency units,
gamma_NV = 2.8025 MHz/G).
"""

import glob
import itertools
import os
import re

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks

import nv_field_fitting as nvfit

GAMMA_MHZ_PER_G = nvfit.GAMMA_NV_HZ_PER_G / 1e6
COILS = ('X', 'Y', 'Z')


# =============================================================================
# Data
# =============================================================================

def _scalar(v):
    v = np.asarray(v)
    if v.ndim == 0:
        return v.item()
    return v


def load_linearity_folder(folder='CoilLinearity'):
    """All odmr_point_<n>.npz of `folder` as dicts (in order of n), with
    'n', 'I' (read-back current vector, A) and 'freq_MHz' added."""
    paths = glob.glob(os.path.join(folder, 'odmr_point_*.npz'))
    paths.sort(key=lambda p: int(re.findall(r'(\d+)\.npz$', p)[0]))
    points = []
    for p in paths:
        with np.load(p, allow_pickle=False) as d:
            pt = {k: _scalar(d[k]) for k in d.files}
        pt['n'] = int(re.findall(r'(\d+)\.npz$', p)[0])
        pt['path'] = p
        pt['I'] = np.asarray(pt['current_vec'], dtype=float)
        pt['freq_MHz'] = np.asarray(pt['freq'], dtype=float) / 1e6
        pt['signal'] = np.asarray(pt['signal'], dtype=float)
        points.append(pt)
    return points


# =============================================================================
# Polarity relays: commanded vs physical coil currents
# =============================================================================

# In data taken before the 2026-10-05 config fix, the polarity relay commanded
# as 'X' reversed the Y coil and vice versa (found from the CoilLinearity +
# CoilSuperposition data; the coil_control config cross-connected the X/Y
# supplies but not the X/Y relays; switch_names is now x: 'Y', y: 'X'). Entry c:
# index of the commanded axis whose relay is in coil c's circuit. For data taken
# after the fix use (0, 1, 2).
RELAY_OF_COIL_2026_10_05 = (1, 0, 2)


def polarity_relay_history(points, initial=(1, 1, 1)):
    """Relay state (+1 / -1) of every axis at every point, reconstructed from
    the commanded currents in measurement order ('saved_at'): a sign change
    flips that axis's relay, a 0 A setpoint leaves it untouched
    (CoilControlInterfuse). `initial`: assumed states before the first point
    (only matters for axes that are still 0 A). Sets point['relays']."""
    state = list(initial)
    for p in sorted(points, key=lambda p: str(p['saved_at'])):
        I = np.asarray(p['set_current_vec'] if 'set_current_vec' in p else p['I'], dtype=float)
        for c in range(3):
            if I[c] > 1e-9:
                state[c] = 1
            elif I[c] < -1e-9:
                state[c] = -1
        p['relays'] = tuple(state)
    return points


def physical_currents(points, relay_of_coil=RELAY_OF_COIL_2026_10_05):
    """Signed currents that actually flowed in the X, Y, Z coils: the magnitude
    of that coil's commanded current, the sign of the relay in its circuit
    (point['relays'], see polarity_relay_history). Sets point['I_phys'] and
    returns them as an (N, 3) array."""
    out = []
    for p in points:
        I = np.asarray(p['I'], dtype=float)
        p['I_phys'] = np.array([abs(I[c]) * p['relays'][relay_of_coil[c]] for c in range(3)])
        out.append(p['I_phys'])
    return np.array(out)


# =============================================================================
# Fast transitions for 4 axes (MHz)
# =============================================================================

class NVAxes:
    """Transition frequencies (MHz) of all axes for a field B (MHz), E = 0."""

    def __init__(self, quantization_axes):
        self.axes = [nvfit._normalize(a) for a in quantization_axes]
        S = np.array([nvfit._SX, nvfit._SY, nvfit._SZ])
        self._S = S
        self._Sa2 = np.array([np.tensordot(a, S, 1) @ np.tensordot(a, S, 1) for a in self.axes])
        self.symmetries = nvfit.nv_axis_symmetries(self.axes)

    def transitions(self, B, D):
        H = D * self._Sa2 + np.tensordot(np.asarray(B, dtype=float), self._S, 1)[None]
        ev = np.linalg.eigvalsh(H)                       # (n_axes, 3)
        return np.concatenate([ev[:, 1] - ev[:, 0], ev[:, 2] - ev[:, 0]])

    def images(self, B):
        return np.array([g @ B for g in self.symmetries])


# =============================================================================
# Stage 1: field per spectrum
# =============================================================================

def _detect_dips(f, s, noise, max_dips=8):
    """Rough dip centres (MHz) for seeding: smoothed spectrum, prominent
    minima that are wider than a few points (spurs are narrow)."""
    k = 7
    sm = np.convolve(s, np.ones(k) / k, mode='same')
    sm[:k] = sm[k]
    sm[-k:] = sm[-k - 1]
    peaks, props = find_peaks(-sm, prominence=4 * noise / np.sqrt(k), width=4)
    order = np.argsort(props['prominences'])[::-1][:max_dips]
    return np.sort(f[peaks[order]])


def _perturbed_seeds(B, n_tilt=6, tilt_deg=12.0, scales=(0.85, 1.15)):
    """A few starting fields around B: rescaled, and tilted by tilt_deg
    towards n_tilt directions perpendicular to B."""
    B = np.asarray(B, dtype=float)
    b = np.linalg.norm(B)
    if b == 0:
        return []
    out = [s * B for s in scales]
    e1, e2 = nvfit._perp_basis(B / b)
    t = np.radians(tilt_deg)
    for phi in np.linspace(0, 2 * np.pi, n_tilt, endpoint=False):
        d = np.cos(t) * B / b + np.sin(t) * (np.cos(phi) * e1 + np.sin(phi) * e2)
        out.append(b * d)
    return out


def fit_spectrum_field(freq_MHz, signal, nv, seeds, D_seed=2870.5, skip_first_points=15,
                       grid_search=True, f_scale_noise=2.5, width_bounds=(1.5, 40.0),
                       D_bounds=(2869.0, 2873.0), min_amp_noise=2.0, min_amp_depth=0.15):
    """
    Fit field B (MHz), D (MHz), a sloped baseline and the 2 Lorentzian dips
    of every NV axis to one raw spectrum. Both dips of an axis share one
    amplitude and one width (equal strength under linear MW drive), and
    every axis amplitude is at least max(min_amp_noise x noise,
    min_amp_depth x the spectrum's depth) -- so no transition can be
    switched off to dodge the data. `seeds`: starting fields (MHz); the
    best of all starts wins.

    Returns a dict: 'B', 'D', 'cov_BD' (4x4, statistical, from the noise
    level), 'cost', 'rms' and 'noise' (counts), 'baseline', 'amps' and
    'widths' (per axis), 'model' (on the fitted points), 'f', 's',
    'n_seeds', 'params'.
    """
    f = np.asarray(freq_MHz, dtype=float)[skip_first_points:]
    s = np.asarray(signal, dtype=float)[skip_first_points:]
    noise = float(np.std(np.diff(s)) / np.sqrt(2))
    f_mid = 0.5 * (f[0] + f[-1])
    base = float(np.percentile(s, 90))
    depth = max(base - float(np.min(s)), 5 * noise)
    n_ax = len(nv.axes)

    def model(p):
        fk = nv.transitions(p[0:3], p[3])               # lo of every axis, then hi
        A = np.tile(p[6:6 + n_ax], 2)
        g = np.tile(p[6 + n_ax:6 + 2 * n_ax], 2)
        x = (f[:, None] - fk[None, :]) / g[None, :]
        return p[4] + p[5] * (f - f_mid) - (A[None, :] / (1.0 + x * x)).sum(axis=1)

    def resid(p):
        return (model(p) - s) / noise

    seeds = [np.asarray(b, dtype=float) for b in seeds]
    if grid_search:
        obs = _detect_dips(f, s, noise)
        if obs.size >= 2:
            try:
                best = nvfit._fit_field_global(nv.axes, obs * 1e6, (f[0] * 1e6, f[-1] * 1e6),
                                               D=D_seed * 1e6)
                if best is not None:
                    seeds.append(best[0] / 1e6)
            except Exception:
                pass
    a_min = max(min_amp_noise * noise, min_amp_depth * depth)
    lo = ([-np.inf] * 3 + [D_bounds[0], -np.inf, -np.inf] + [a_min] * n_ax
          + [width_bounds[0]] * n_ax)
    hi = ([np.inf] * 3 + [D_bounds[1], np.inf, np.inf] + [3 * depth] * n_ax
          + [width_bounds[1]] * n_ax)

    best = None
    for B0 in seeds:
        p0 = np.concatenate([B0, [D_seed, base, 0.0], np.full(n_ax, max(0.4 * depth, 1.5 * a_min)),
                             np.full(n_ax, 8.0)])
        p0 = np.clip(p0, np.array(lo) + 1e-9, np.array(hi) - 1e-9)
        r = least_squares(resid, p0, bounds=(lo, hi), loss='soft_l1', f_scale=f_scale_noise,
                          x_scale='jac', max_nfev=4000)
        if best is None or r.cost < best.cost:
            best = r
    p = best.x
    # statistical covariance (noise-normalized residuals): pinv(J^T J)
    J = best.jac
    cov = np.linalg.pinv(J.T @ J, rcond=1e-10)
    m = model(p)
    return {'B': p[0:3].copy(), 'D': float(p[3]), 'cov_BD': cov[:4, :4],
            'cost': float(best.cost), 'rms': float(np.sqrt(np.mean((m - s) ** 2))),
            'noise': noise, 'baseline': p[4:6].copy(), 'amps': p[6:6 + n_ax].copy(),
            'widths': p[6 + n_ax:].copy(), 'model': m, 'f': f, 's': s,
            'n_seeds': len(seeds), 'params': p}


def fit_all_spectra(points, nv, coil_strengths=None, nominal_axes=None, skip_first_points=15,
                    verbose=True, **fit_kwargs):
    """
    Stage 1 for every point (adds point['fit']). Starting fields: the
    nominal coil model (coil_strengths MHz/A along nominal_axes), the
    neighbouring point of the same scan scaled to this current (scans are
    fitted outwards from 0 A; scaling any symmetry image is fine), the
    up-scan fit at the same current for return points, perturbations of
    these, and a grid search on detected dips.
    """
    strengths = coil_strengths or {'X': 55.0, 'Y': 110.0, 'Z': 110.0}
    nominal = nominal_axes or {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}
    U = np.column_stack([strengths[c] * nvfit._normalize(nominal[c]) for c in COILS])

    def nominal_seed(I):
        B = U @ I
        return B if np.linalg.norm(B) > 1.0 else np.array([1.0, 0.7, 0.4])

    done = {}

    def run(p, extra):
        seeds = [nominal_seed(p['I'])] + list(extra)
        seeds += [s for b in list(seeds) for s in _perturbed_seeds(b)]
        p['fit'] = fit_spectrum_field(p['freq_MHz'], p['signal'], nv, seeds,
                                      skip_first_points=skip_first_points, **fit_kwargs)
        done[p['n']] = p
        if verbose:
            r = p['fit']
            print(f"  #{p['n']:2d} {str(p['scan']):5s} {str(p['direction']):4s} I={np.round(p['I'], 3)}"
                  f"  |B|={np.linalg.norm(r['B']):7.2f} MHz  D={r['D']:.3f}  rms/noise="
                  f"{r['rms'] / r['noise']:.2f}")

    # single-coil scans: up-scan outwards from the smallest |I|, then return points
    for c, coil in enumerate(COILS):
        scan = [p for p in points if p['scan'] == coil]
        up = sorted([p for p in scan if p['direction'] == 'up'], key=lambda p: p['I'][c])
        i0 = int(np.argmin([abs(p['I'][c]) for p in up]))
        run(up[i0], [])
        for side in (range(i0 + 1, len(up)), range(i0 - 1, -1, -1)):
            prev = up[i0]
            for i in side:
                p = up[i]
                extra = []
                if abs(prev['I'][c]) > 1e-6:
                    extra.append(prev['fit']['B'] * p['I'][c] / prev['I'][c])
                run(p, extra)
                prev = p
        for p in [p for p in scan if p['direction'] != 'up']:
            same = min(up, key=lambda q: abs(q['I'][c] - p['I'][c]))
            run(p, [same['fit']['B']])
    # mixed scan: in order of increasing scale, seeded from the previous scale
    mixed = sorted([p for p in points if p['scan'] == 'mixed'], key=lambda p: np.linalg.norm(p['I']))
    prev = None
    for p in mixed:
        extra = [] if prev is None else [prev['fit']['B'] * np.linalg.norm(p['I'])
                                         / np.linalg.norm(prev['I'])]
        same = [q for q in mixed if q['n'] in done and np.allclose(q['I'], p['I'], atol=1e-3)]
        extra += [q['fit']['B'] for q in same]
        run(p, extra)
        prev = p
    for p in points:                      # anything not covered above
        if p['n'] not in done:
            run(p, [])
    return points


# =============================================================================
# Stage 2: coil model fitted to the per-point fields (modulo symmetry)
# =============================================================================

def _basis_rows(I, terms):
    """Design rows [1, I_c^p for (c, p) in terms] for currents I (N, 3).
    terms: dict coil -> list of powers, e.g. {'X': [1, 3], 'Y': [1], 'Z': [1]}."""
    I = np.atleast_2d(I)
    cols = [np.ones(len(I))]
    labels = ['B0']
    for c, coil in enumerate(COILS):
        for pw in terms.get(coil, []):
            cols.append(I[:, c] ** pw)
            labels.append(f'{coil}^{pw}')
    return np.column_stack(cols), labels


def predict_field(theta, terms, I):
    """Model field(s) (MHz) for current vector(s) I: rows @ theta."""
    rows, _ = _basis_rows(I, terms)
    return rows @ theta


def field_data(points, cov_floor=0.3, use=None):
    """(I, B, W) arrays from stage-1 fits: currents (N,3), fields (N,3) MHz and
    weight matrices (N,3,3) = inv(cov_B + cov_floor^2). `use`: optional
    predicate on points."""
    sel = [p for p in points if use is None or use(p)]
    I = np.array([p['I'] for p in sel])
    B = np.array([p['fit']['B'] for p in sel])
    W = np.array([np.linalg.inv(p['fit']['cov_BD'][:3, :3] + cov_floor ** 2 * np.eye(3))
                  for p in sel])
    return sel, I, B, W


def fit_field_model(I, B, W, nv, terms, theta0, n_iter=100):
    """
    Weighted least squares of B(I) = rows(I) @ theta to the per-point fields,
    every point represented by its symmetry image closest (in the weighted
    norm) to the current model. Alternates image assignment and the linear
    solve until the assignment is stable.

    Returns dict: 'theta' (n_basis, 3), 'chi2', 'dof', 'images' (N,3) aligned
    fields, 'g_index' (N,), 'resid' (N,3) MHz, 'chi2_points' (N,).
    """
    G = np.array(nv.symmetries)                       # (48, 3, 3)
    rows, labels = _basis_rows(I, terms)
    nb = rows.shape[1]
    theta = np.array(theta0, dtype=float).reshape(nb, 3)
    gi_prev = None
    for _ in range(n_iter):
        m = rows @ theta                              # (N, 3)
        Y = np.einsum('gab,nb->nga', G, B)            # (N, 48, 3) images
        Wg = np.einsum('gab,nbc,gdc->ngad', G, W, G)  # image weights
        d = Y - m[:, None, :]
        c2 = np.einsum('nga,ngab,ngb->ng', d, Wg, d)
        gi = np.argmin(c2, axis=1)
        y = Y[np.arange(len(I)), gi]
        Wy = Wg[np.arange(len(I)), gi]
        # normal equations, theta flattened as (nb*3,)
        A = np.einsum('nb,ij->nibj', rows, np.eye(3)).reshape(len(I), 3, nb * 3)
        lhs = np.einsum('nia,nij,njb->ab', A, Wy, A)
        rhs = np.einsum('nia,nij,nj->a', A, Wy, y)
        theta = np.linalg.solve(lhs, rhs).reshape(nb, 3)
        if gi_prev is not None and np.array_equal(gi, gi_prev):
            break
        gi_prev = gi
    m = rows @ theta
    resid = y - m
    chi2_pts = np.einsum('na,nab,nb->n', resid, Wy, resid)
    cov_theta = np.linalg.inv(lhs)
    return {'theta': theta, 'labels': labels, 'terms': terms, 'chi2': float(chi2_pts.sum()),
            'dof': int(3 * len(I) - nb * 3), 'images': y, 'g_index': gi, 'resid': resid,
            'chi2_points': chi2_pts, 'cov_theta': cov_theta}


def align_to_nominal(theta, terms, nv, nominal_axes=None):
    """Apply the global symmetry g (the overall solution is only defined up to
    one) that best aligns every coil's linear term with its nominal axis."""
    nominal = nominal_axes or {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}
    _, labels = _basis_rows(np.zeros((1, 3)), terms)
    best = None
    for g in nv.symmetries:
        score = 0.0
        for coil in COILS:
            if f'{coil}^1' in labels:
                u = g @ theta[labels.index(f'{coil}^1')]
                score += np.dot(u, nvfit._normalize(nominal[coil])) / max(np.linalg.norm(u), 1e-12)
        if best is None or score > best[0]:
            best = (score, g)
    g = best[1]
    return theta @ g.T, g


def resolve_coil_tilts(points_data, nv, terms_lin=None, nominal_axes=None, coil_strengths=None,
                       verbose=True):
    """
    Linear model over all points, trying every combination of near-nominal
    symmetry candidates for each coil's direction (single-coil scans cannot
    tell them apart; the mixed points can). Returns (best_fit, all results
    sorted by chi2).
    """
    I, B, W = points_data
    nominal = nominal_axes or {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}
    strengths = coil_strengths or {'X': 55.0, 'Y': 110.0, 'Z': 110.0}
    terms = terms_lin or {c: [1] for c in COILS}
    # per-coil single-coil fits (only that coil's points + zero-current points)
    cand = {}
    for c, coil in enumerate(COILS):
        sel = np.array([np.all(np.abs(np.delete(i, c)) < 1e-9) for i in I])
        th0 = np.zeros((2, 3))
        th0[1] = strengths[coil] * nvfit._normalize(nominal[coil])
        r = fit_field_model(I[sel], B[sel], W[sel], nv, {coil: [1]}, th0)
        cand[coil] = nvfit.coil_direction_candidates(r['theta'][1], r['theta'][0], nominal[coil],
                                                     nv.symmetries, angle_tol_deg=0.5)
        if verbose:
            print(f'  {coil}: |u| = {np.linalg.norm(r["theta"][1]):.2f} MHz/A, '
                  f'{len(cand[coil])} tilt candidates')
    results = []
    for combo in itertools.product(*[cand[c] for c in COILS]):
        _, labels = _basis_rows(np.zeros((1, 3)), terms)
        th0 = np.zeros((len(labels), 3))
        th0[0] = np.mean([k['B_offset'] for k in combo], axis=0)
        for coil, k in zip(COILS, combo):
            th0[labels.index(f'{coil}^1')] = k['u']
        results.append(fit_field_model(I, B, W, nv, terms, th0))
    results.sort(key=lambda r: r['chi2'])
    return results[0], results


# =============================================================================
# Stage 3: one coil model fitted jointly to all spectra
# =============================================================================

def _cross_rows(I, cross):
    """Extra design columns for bilinear terms, e.g. cross=[('X','Y')] -> I_X*I_Y."""
    I = np.atleast_2d(I)
    idx = {c: k for k, c in enumerate(COILS)}
    cols = [I[:, idx[a]] * I[:, idx[b]] for a, b in cross]
    return (np.column_stack(cols) if cols else np.zeros((len(I), 0))), [f'{a}{b}' for a, b in cross]


def model_rows(I, terms, cross=()):
    rows, labels = _basis_rows(I, terms)
    xr, xl = _cross_rows(I, cross)
    return np.hstack([rows, xr]), labels + xl


def joint_spectral_fit(spectra, nv, terms, theta0, cross=(), D=2871.3, f_scale_noise=2.5,
                       max_nfev=200, verbose=0, per_dip=False, fit_D=False, D_bounds=(2865.0, 2877.0)):
    """
    Fit ONE field model B(I) = rows(I) @ theta to many raw spectra at once.
    `spectra`: list of dicts with 'f' (MHz), 's' (counts), 'I' (A) and a
    stage-1 'fit' (start values of the per-spectrum nuisance: baseline,
    per-axis amplitudes and widths, which stay free per spectrum), and
    optionally a boolean 'mask' over 'f' (False: point not fitted -- e.g. the
    strain-affected central lines of near-perpendicular axes). Spectra are
    invariant under the NV-axis symmetries, so no image assignment is
    needed; theta0 must be in the right basin (tilts).

    per_dip: free amplitude and width for each of the 8 dips (instead of one
    per axis). fit_D: D is one more global parameter (start: D).

    Returns dict: 'theta', 'labels', 'cost', 'n_res', 'per_spectrum_cost',
    'fields' (model field per spectrum), 'nuisance', 'D', 'result'.
    """
    from scipy.sparse import lil_matrix
    n_ax = len(nv.axes)
    n_dip = 2 * n_ax
    rows, labels = model_rows(np.array([sp['I'] for sp in spectra]), terms, cross)
    nb = rows.shape[1]
    n_glob = nb * 3 + (1 if fit_D else 0)
    n_amp = n_dip if per_dip else n_ax
    n_nui = 2 + 2 * n_amp
    fs, ss = [], []
    for sp in spectra:
        m = sp.get('mask')
        fs.append(np.asarray(sp['f'], float) if m is None else np.asarray(sp['f'], float)[m])
        ss.append(np.asarray(sp['s'], float) if m is None else np.asarray(sp['s'], float)[m])
    sizes = [len(f) for f in fs]
    offs = np.concatenate([[0], np.cumsum(sizes)])
    noise = np.array([sp['fit']['noise'] for sp in spectra])
    fmid = [0.5 * (sp['f'][0] + sp['f'][-1]) for sp in spectra]

    def unpack(x):
        theta = x[:nb * 3].reshape(nb, 3)
        D_ = x[nb * 3] if fit_D else D
        nui = x[n_glob:].reshape(len(spectra), n_nui)
        return theta, D_, nui

    def resid(x):
        theta, D_, nui = unpack(x)
        Bs = rows @ theta
        out = np.empty(offs[-1])
        for k in range(len(spectra)):
            fk = nv.transitions(Bs[k], D_)
            A = nui[k, 2:2 + n_amp]
            g = nui[k, 2 + n_amp:]
            if not per_dip:
                A, g = np.tile(A, 2), np.tile(g, 2)
            xx = (fs[k][:, None] - fk[None, :]) / g[None, :]
            m = nui[k, 0] + nui[k, 1] * (fs[k] - fmid[k]) - (A[None, :] / (1.0 + xx * xx)).sum(1)
            out[offs[k]:offs[k + 1]] = (m - ss[k]) / noise[k]
        return out

    x0 = [np.asarray(theta0, dtype=float).reshape(-1)] + ([np.array([D])] if fit_D else [])
    lo = [np.full(nb * 3, -np.inf)] + ([np.array([D_bounds[0]])] if fit_D else [])
    hi = [np.full(nb * 3, np.inf)] + ([np.array([D_bounds[1]])] if fit_D else [])
    for sp in spectra:
        r = sp['fit']
        amps, widths = np.asarray(r['amps'], float), np.asarray(r['widths'], float)
        if per_dip and amps.size == n_ax:
            amps, widths = np.tile(amps, 2), np.tile(widths, 2)
        a_min = 0.5 * float(np.min(amps)) if per_dip else 0.0
        x0.append(np.concatenate([r['baseline'], amps, widths]))
        lo.append(np.concatenate([[-np.inf, -np.inf], np.full(n_amp, a_min), np.full(n_amp, 1.0)]))
        hi.append(np.concatenate([[np.inf, np.inf], np.full(n_amp, np.inf), np.full(n_amp, 60.0)]))
    x0, lo, hi = np.concatenate(x0), np.concatenate(lo), np.concatenate(hi)
    x0 = np.clip(x0, lo + 1e-9, hi - 1e-9)

    sparsity = lil_matrix((offs[-1], len(x0)), dtype=int)
    sparsity[:, :n_glob] = 1
    for k in range(len(spectra)):
        sparsity[offs[k]:offs[k + 1], n_glob + k * n_nui:n_glob + (k + 1) * n_nui] = 1

    res = least_squares(resid, x0, bounds=(lo, hi), loss='soft_l1', f_scale=f_scale_noise,
                        jac_sparsity=sparsity, x_scale='jac', max_nfev=max_nfev, verbose=verbose,
                        tr_solver='lsmr')
    theta, D_fit, nui = unpack(res.x)
    r = resid(res.x)
    rho = 2 * f_scale_noise ** 2 * (np.sqrt(1 + (r / f_scale_noise) ** 2) - 1)   # soft_l1
    per = np.array([rho[offs[k]:offs[k + 1]].sum() / 2 for k in range(len(spectra))])
    return {'theta': theta, 'labels': labels, 'terms': terms, 'cross': tuple(cross),
            'D': float(D_fit), 'per_dip': per_dip, 'n_glob': n_glob,
            'cost': float(res.cost), 'n_res': int(offs[-1]), 'per_spectrum_cost': per,
            'fields': rows @ theta, 'nuisance': nui, 'result': res, 'resid': r, 'offs': offs}


def spectrum_lines(points, nv, D=2871.3):
    """Sorted transition frequencies (MHz) of every point's stage-1 field
    (point['lines']) -- symmetry-invariant observables for fit_lines_model."""
    for p in points:
        p['lines'] = np.sort(nv.transitions(p['fit']['B'], D))
    return points


def fit_lines_model(points, nv, terms, theta0, cross=(), D=2871.3, current_key='I_phys',
                    loss='linear', f_scale=2.0):
    """
    Field model B(I) = rows(I) @ theta fitted to the sorted stage-1 line
    frequencies of many spectra (spectrum_lines), with point[current_key] as
    the currents. Sorting makes it independent of the NV-axis symmetries (no
    image assignment); theta0 must be in the right basin (tilt signs).
    Returns (theta, residuals (N, 8) MHz).
    """
    I = np.array([p[current_key] for p in points])
    rows, labels = model_rows(I, terms, cross)
    obs = np.array([p['lines'] for p in points])

    def resid(x):
        Bm = rows @ x.reshape(len(labels), 3)
        return (np.array([np.sort(nv.transitions(b, D)) for b in Bm]) - obs).ravel()

    r = least_squares(resid, np.asarray(theta0, dtype=float).ravel(), loss=loss, f_scale=f_scale)
    return r.x.reshape(len(labels), 3), resid(r.x).reshape(len(points), -1)


def jackknife_lines_model(points, nv, terms, theta0, cross=(), D=2871.3, current_key='I_phys',
                          align=True):
    """Delete-one-group jackknife of fit_lines_model (a group = all repeats of
    one current vector): standard errors of theta that include point-to-point
    systematics. With align, every fit is put in the nominal frame
    (align_to_nominal) first. Returns (theta_full, theta_se, thetas)."""
    th_full, _ = fit_lines_model(points, nv, terms, theta0, cross, D, current_key)
    if align:
        th_full, _ = align_to_nominal(th_full, terms, nv)
    groups = {}
    for i, p in enumerate(points):
        groups.setdefault(tuple(np.round(p[current_key], 3)), []).append(i)
    thetas = []
    for idx in groups.values():
        sub = [p for i, p in enumerate(points) if i not in idx]
        th, _ = fit_lines_model(sub, nv, terms, th_full, cross, D, current_key)
        if align:
            th, _ = align_to_nominal(th, terms, nv)
        thetas.append(th)
    thetas = np.array(thetas)
    g = len(thetas)
    se = np.sqrt((g - 1) / g * ((thetas - thetas.mean(0)) ** 2).sum(0))
    return th_full, se, thetas


# =============================================================================
# Planning a superposition / cross-coil data set
# =============================================================================

# Linear coil models (B = M @ I + B0; MHz and MHz/A; columns X, Y, Z) fitted to
# the 2026-10-04 linearity run and the 2026-10-02 points. Their coil-pair
# angles still disagree, so plans are checked against all of them.
CANDIDATE_MODELS_2026_10_05 = [
    ('robust projections, Oct 4',
     np.array([[56.04, 9.72, 1.83], [11.39, 113.78, -8.19], [2.98, 4.36, 111.69]]),
     np.array([0.48, -0.78, -1.16])),
    ('Oct 2 points only',
     np.array([[55.6, 10.3, 2.3], [9.9, 116.6, -6.6], [4.2, 2.3, 113.2]]),
     np.array([1.6, -1.2, -2.6])),
    ('joint spectral, Oct 4',
     np.array([[55.83, 12.94, -3.33], [6.33, 112.59, 8.05], [2.33, 5.32, 111.29]]),
     np.array([0.83, 0.49, -1.22])),
]

OCT2_CURRENTS = [(3.26, -3.0, 1.86), (-3.26, 3.0, 1.86), (-3.26, -3.0, 1.86), (1.25, -2.0, 3.27),
                 (-1.25, 2.0, -3.27), (-1.25, -2.0, -3.27), (-3.26, 3.0, -1.86), (3.26, -3.0, -1.86),
                 (-3.26, -3.0, -1.86)]


def check_resolvable(I, nv, models=None, D=2871.3, window=(2440.0, 3375.0), min_projection=30.0,
                     min_separation=12.0):
    """Whether currents I give a usable spectrum under EVERY model: all 8
    transitions inside `window` (MHz), no NV axis with |B.n| < min_projection
    (near-perpendicular axes have strain-affected central lines), and all
    transitions at least min_separation apart. Returns (ok, worst-case stats)."""
    models = models or CANDIDATE_MODELS_2026_10_05
    worst = {'f_min': np.inf, 'f_max': -np.inf, 'proj_min': np.inf, 'sep_min': np.inf}
    for _, M, B0 in models:
        B = M @ np.asarray(I, dtype=float) + B0
        f = np.sort(nv.transitions(B, D))
        worst['f_min'] = min(worst['f_min'], f[0])
        worst['f_max'] = max(worst['f_max'], f[-1])
        worst['proj_min'] = min(worst['proj_min'], float(np.min(np.abs(np.array(nv.axes) @ B))))
        worst['sep_min'] = min(worst['sep_min'], float(np.min(np.diff(f))))
    ok = (worst['f_min'] >= window[0] and worst['f_max'] <= window[1]
          and worst['proj_min'] >= min_projection and worst['sep_min'] >= min_separation)
    return ok, worst


def _largest_feasible(shape, nv, limits, min_scale=0.3, **kw):
    """Largest s <= 1 with s*shape within the current limits and resolvable
    (check_resolvable, default thresholds); None if there is none >= min_scale."""
    shape = np.asarray(shape, dtype=float)
    s_lim = min(limits[c] / abs(shape[k]) for k, c in enumerate(COILS) if shape[k] != 0)
    for s in np.linspace(min(1.0, s_lim), min_scale, 60):
        if check_resolvable(s * shape, nv, **kw)[0]:
            return s
    return None


def plan_superposition_set(nv, limits=None, half_scale=0.65, n_random=14, drift_every=8,
                           drift_point=(-3.26, -3.0, -1.86), include_oct2=True, seed=0, D=2871.3,
                           window=(2440.0, 3375.0), verbose=True):
    """
    Plan mixed-current measurements that pin down the coil-pair angles and
    test superposition:
      * 'oct2'       -- the 2026-10-02 currents again (those within limits);
      * 'pair'       -- XY, XZ and YZ with ++ and +- signs, at the largest
                        resolvable scale and at half_scale x that (with
                        +-25 % of the third coil if the pure pair gives
                        coinciding dips);
      * 'octant'     -- (+,+,+), (+,+,-), (+,-,+), (+,-,-), same two scales
                        (a wrong linear map shows up in proportion to the
                        current, a real nonlinearity faster);
      * 'octant-neg' -- the full-scale octant points negated (odd symmetry /
                        background field);
      * 'random'     -- n_random further directions (not single-coil-like);
      * 'drift'      -- `drift_point` at the start, every `drift_every`
                        points and at the end (drift / reproducibility).
    Every point must pass check_resolvable for all candidate models (min
    |B.n| 30 MHz and dip spacing 12 MHz at full scale, 20 and 8 MHz at the
    reduced scale); a failing shape is
    varied (one coil x0.8 / x1.2) before it is dropped. The order is
    randomized (seed) so slow drift does not correlate with direction.

    Returns the plan as a list of dicts in measurement order, in the format
    of coil_characterization.run_linearity_scans ('scan', 'index',
    'direction', 'scale', 'set_current_vec').
    """
    limits = limits or {'X': 4.0, 'Y': 3.0, 'Z': 3.0}
    lim = np.array([limits[c] for c in COILS])
    rng = np.random.default_rng(seed)
    kw = dict(D=D, window=window)
    points = []

    def add(kind, I, scale=1.0):
        points.append({'scan': kind, 'direction': 'up', 'scale': float(scale),
                       'set_current_vec': tuple(float(np.round(v, 3)) for v in I)})

    def add_shape(kind, shape, scales=(1.0,)):
        shape = np.asarray(shape, dtype=float)
        variants = [shape]
        for k in range(3):
            for f in (0.8, 1.2):
                v = shape.copy()
                v[k] *= f
                variants.append(v)
        for k in np.flatnonzero(shape == 0):     # pairs: a small third-coil current breaks
            for f in (0.25, -0.25):              # the dip degeneracy of in-plane fields
                v = shape.copy()
                v[k] = f * lim[k]
                variants.append(v)
        for v in variants:
            s = _largest_feasible(v, nv, limits, **kw)
            if s is None:
                continue
            if all(check_resolvable(sc * s * v, nv, min_projection=30.0 if sc == 1.0 else 20.0,
                                    min_separation=12.0 if sc == 1.0 else 8.0, **kw)[0]
                   for sc in scales):
                for sc in scales:
                    add(kind, sc * s * v, sc * s)
                return True
        if verbose:
            print(f'  (no resolvable variant of {kind} shape {np.round(shape, 2)} -- skipped)')
        return False

    if include_oct2:
        for I in OCT2_CURRENTS:
            I = np.array(I)
            if np.all(np.abs(I) <= lim + 1e-9):
                add('oct2', I)
            elif verbose:
                print(f'  (Oct 2 point {I} exceeds the current limits -- skipped; raise the '
                      f'limit to include it)')
    for a, b in ((0, 1), (0, 2), (1, 2)):
        for sb in (1.0, -1.0):
            shape = np.zeros(3)
            shape[a], shape[b] = lim[a], sb * lim[b]
            add_shape('pair', shape, scales=(1.0, half_scale))
    for sy in (1.0, -1.0):
        for sz in (1.0, -1.0):
            if add_shape('octant', lim * np.array([1.0, sy, sz]), scales=(1.0, half_scale)):
                full = points[-2]
                add('octant-neg', -np.array(full['set_current_vec']), full['scale'])
    n_added, tries = 0, 0
    while n_added < n_random and tries < 500:
        tries += 1
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        if np.max(np.abs(d)) > 0.9:          # essentially single-coil: already measured
            continue
        if add_shape('random', d * lim):
            n_added += 1

    body = [points[k] for k in rng.permutation(len(points))]
    drift = {'scan': 'drift', 'direction': 'up', 'scale': 1.0,
             'set_current_vec': tuple(float(v) for v in drift_point)}
    plan = []
    for k, p in enumerate(body):
        if k % drift_every == 0:
            plan.append(dict(drift))
        plan.append(p)
    plan.append(dict(drift))
    for i, p in enumerate(plan):
        p['index'] = i
    if verbose:
        kinds = {}
        for p in plan:
            kinds[p['scan']] = kinds.get(p['scan'], 0) + 1
        print(f'{len(plan)} measurements: ' + ', '.join(f'{v} {k}' for k, v in kinds.items()))
        for p in plan:
            _, w = check_resolvable(p['set_current_vec'], nv, min_projection=0.0, **kw)
            print(f"  {p['index']:2d} {p['scan']:10s} I = {np.round(p['set_current_vec'], 3)!s:24s} "
                  f"dips {w['f_min']:.0f}-{w['f_max']:.0f} MHz, min |B.n| {w['proj_min']:5.1f}, "
                  f"min dip spacing {w['sep_min']:5.1f} MHz (worst case over models)")
    return plan
