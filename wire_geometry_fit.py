# -*- coding: utf-8 -*-
"""
wire_geometry_fit.py

Where is the NV ensemble relative to the four chip wires? Estimated from wire-field
mapping data (wire_field_mapping.py) with the published chip geometry: four thin gold
strips of widths 10, 5, 5, 10 um separated by 6 um gaps (u = 0: center of the gap
between the two 5-um wires).

1. on_fields(folder): wire field of every drive spectrum WHILE THE WIRE WAS ON. Each
   spectrum is fitted as a mixture: a fraction f of the run with a free field B_on, the
   rest with the reference field of the same position (wire off) -- the drive can drop
   out during a run (pulse-board supply trips), which biases a single-field fit.
2. fit_geometry(data): the ratio of in-plane to vertical field of a wire depends only on
   where the NV is relative to it (not on the current), so the field directions and
   their change across the offsets fix the NV height h above the wire plane and its
   lateral position u0. The single-wire drives are found from the voltages saved with
   the spectra; every assignment of these wires to strips is tried; the per-wire
   currents are free (solved linearly) and converted to A/V with the saved voltages.

Frame (coil-calibration NV frame): y = in-plane perpendicular to the wires, z = out of
plane; wires run along x.

    import wire_geometry_fit as wgf
    data = wgf.on_fields('WireFieldMapping')          # ~1 min
    best = wgf.fit_geometry(data)                      # ~10 min (grid over h, u0)
    wgf.print_results(best)

Result for the 2026-10-06 mapping run: h = 5.6 um (+- ~1 um systematic), u0 within
0.5 um of the gap center; wire 2 = a central 5-um wire, wires 3 and 4 = the outer
10-um wires (physical order 3, 1, NV, 2, 4), currents 0.33-0.43 A/V.
"""
import itertools

import numpy as np
from scipy.optimize import least_squares

# thin strips (um): widths 10, 5, 5, 10, gaps 6, centered on the central gap
STRIPS = {'outer A (10 um)': (-24.0, -14.0), 'inner A (5 um)': (-8.0, -3.0),
          'inner B (5 um)': (3.0, 8.0), 'outer B (10 um)': (14.0, 24.0)}
GAMMA_MHZ_PER_UT = 2.8025e-2


def strip_field_uT(a, b, u, h):
    """(in-plane, vertical) field in uT per A of a thin strip [a, b] (um) with uniform
    current, at lateral position u and height h (um): mu0 I / (2 pi w) * geometry."""
    w = b - a
    Bu = -(np.arctan((u - a) / h) - np.arctan((u - b) / h))
    Bv = 0.5 * np.log(((u - a) ** 2 + h ** 2) / ((u - b) ** 2 + h ** 2))
    return 0.2 / w * np.array([Bu, Bv]) * 1e6


# =============================================================================
# 1. wire-on fields (two-field mixture fits)
# =============================================================================

def _mixture_fit(f, s, nv, D, B_off, seeds, skip=15):
    f = f[skip:]; s = s[skip:]
    noise = float(np.std(np.diff(s)) / np.sqrt(2))
    fm = 0.5 * (f[0] + f[-1])
    base = float(np.percentile(s, 90)); depth = max(base - s.min(), 5 * noise)
    fk_off = nv.transitions(B_off, D)

    def model(p):
        fk_on = nv.transitions(p[0:3], D)
        A = np.tile(p[7:11], 2); g = np.tile(p[11:15], 2)
        L = lambda fk: (A / (1 + ((f[:, None] - fk) / g) ** 2)).sum(1)
        return p[4] + p[5] * (f - fm) - (p[3] * L(fk_on) + (1 - p[3]) * L(fk_off))

    lo = [-np.inf] * 3 + [0, -np.inf, -np.inf, -np.inf] + [0.1 * depth] * 4 + [1.5] * 4
    hi = [np.inf] * 3 + [1, np.inf, np.inf, np.inf] + [3 * depth] * 4 + [40] * 4
    best = None
    for B0 in seeds:
        for f0 in (0.2, 0.5, 0.9):
            p0 = np.r_[B0, f0, base, 0, 0, np.full(4, 0.4 * depth), np.full(4, 10.0)]
            p0 = np.clip(p0, np.array(lo) + 1e-9, np.array(hi) - 1e-9)
            r = least_squares(lambda p: (model(p) - s) / noise, p0, bounds=(lo, hi), loss='soft_l1',
                              f_scale=2.5, x_scale='jac', max_nfev=3000)
            if best is None or r.cost < best.cost:
                best = r
    se = np.sqrt(np.diag(np.linalg.pinv(best.jac.T @ best.jac, rcond=1e-10)))
    return best.x, se


def on_fields(folder='WireFieldMapping', min_fraction=0.5, min_sweeps=1000, verbose=True):
    """Per drive spectrum: on-fraction and wire-on field dB_on (MHz, NV frame) relative
    to the mean reference of the same group and position. Returns a list of dicts
    (label, config_index, offset, thermal, frac_on, frac_se, dB_on, dB_on_se, use);
    use = not thermal, enough sweeps, frac_on >= min_fraction and well determined."""
    import coil_field_calibration as cfc
    import coil_linearity_analysis as cla
    import wire_field_mapping as wfm
    cal = cfc.load_coil_calibration()
    nv = cla.NVAxes(cal['quantization_axes'])
    D = cal['D_MHz']
    pts, _ = wfm.analyze_wire_field_mapping(folder, cal=cal, plot=False, verbose=False)
    ok = lambda p: p.get('elapsed_sweeps', min_sweeps) >= min_sweeps
    refs = {}
    for p in pts:
        if p['role'] == 'reference' and not p['thermal'] and ok(p):
            refs.setdefault((p['group'], p['position_index']), []).append(p['B'])
    refs = {k: np.mean(v, 0) for k, v in refs.items()}
    out = []
    for p in pts:
        key = (p['group'], p['position_index'])
        if p['role'] != 'drive' or not ok(p) or key not in refs:
            continue
        same = [q['B'] for q in pts if q['config_index'] == p['config_index'] and ok(q)
                and np.linalg.norm(q['B'] - refs[(q['group'], q['position_index'])]) > 15]
        x, se = _mixture_fit(np.asarray(p['freq'], float) / 1e6, np.asarray(p['signal'], float), nv, D,
                             refs[key], [p['B']] + same)
        r = {'label': p['label'], 'config_index': int(p['config_index']), 'offset': float(p['offset']),
             'thermal': bool(p['thermal']), 'volts': np.asarray(p['volts'], float),
             'gradient_mode': int(p['gradient_mode']), 'frac_on': float(x[3]), 'frac_se': float(se[3]),
             'dB_on': x[:3] - refs[key], 'dB_on_se': se[:3]}
        r['use'] = (not r['thermal']) and r['frac_on'] >= min_fraction and r['frac_se'] < 0.1
        out.append(r)
        if verbose:
            print(f"{r['label']:26s} {r['offset']:+.1f}{' T' if r['thermal'] else '  '} on {r['frac_on']:.2f} "
                  f"dB_on {np.round(r['dB_on'], 1)} +- {np.round(r['dB_on_se'], 1)}{'' if r['use'] else '  (not used)'}")
    return out


# =============================================================================
# 2. geometry fit
# =============================================================================

def single_wire_drives(on):
    """{wire number (1-4): drive voltage} of the normal-mode drives with exactly one wire
    on, read from the saved voltages of the spectra."""
    out = {}
    for r in on:
        nz = np.flatnonzero(r['volts'])
        if r['gradient_mode'] == 0 and len(nz) == 1:
            out[int(nz[0]) + 1] = float(r['volts'][nz[0]])
    return dict(sorted(out.items()))


def _wire_data(on, wires, sys_floor):
    """{'wire k': rows (offset, dB_y, dB_z, sigma_y, sigma_z)} of the usable wire-on spectra
    of the single-wire drives of the wires in `wires`."""
    data = {}
    for k in wires:
        rows = [(r['offset'], r['dB_on'][1], r['dB_on'][2], np.hypot(r['dB_on_se'][1], sys_floor),
                 np.hypot(r['dB_on_se'][2], sys_floor)) for r in on
                if r['use'] and r['gradient_mode'] == 0 and np.count_nonzero(r['volts']) == 1
                and r['volts'][k - 1] != 0]
        if len(rows) >= 2:
            data[f'wire {k}'] = np.array(rows)
    return data


def _chi2(data, assign, h, u0, s, rho):
    chi2, cur = 0.0, {}
    for w, d in data.items():
        F = strip_field_uT(*STRIPS[assign[w]], u0 + s * d[:, 0], h) * GAMMA_MHZ_PER_UT
        F[1] *= rho
        y = np.r_[d[:, 1], d[:, 2]]; sig = np.r_[d[:, 3], d[:, 4]]; f = np.r_[F[0], F[1]]
        I = np.sum(f * y / sig ** 2) / np.sum(f * f / sig ** 2)
        chi2 += np.sum(((y - I * f) / sig) ** 2)
        cur[w] = I
    return chi2, cur


def fit_geometry(on, wires=None, sys_floor=1.0, h_grid=None, u0_grid=None):
    """Grid fit of NV height h and lateral position u0 (um) for every assignment of the
    measured single wires to strips, offset direction s and vertical-axis sign rho.
    The single-wire drives are found from the saved voltages; wires: which wire numbers
    to use (default: every single-wire drive with >= 2 usable wire-on spectra -- a wire
    that never showed a field drops out by itself). The voltages are not used by the fit
    (the currents are free); they only convert the fitted currents to A/V.
    Returns the results sorted by chi2, each a dict with chi2, assign, s, rho, h, u0,
    currents_A, amps_per_volt, dof, and the chi2 profile in h of that assignment."""
    drives = single_wire_drives(on)
    data = _wire_data(on, wires if wires is not None else drives, sys_floor)
    h_grid = h_grid if h_grid is not None else np.r_[np.arange(0.5, 6, 0.1), np.arange(6, 30, 0.5)]
    u0_grid = u0_grid if u0_grid is not None else np.arange(-12, 12.01, 0.1)
    n = sum(2 * len(d) for d in data.values())
    results = []
    for perm in itertools.permutations(STRIPS, len(data)):
        assign = dict(zip(data, perm))
        for s in (1, -1):
            for rho in (1, -1):
                prof = []
                for h in h_grid:
                    c2, u0 = min((_chi2(data, assign, h, u0, s, rho)[0], u0) for u0 in u0_grid)
                    prof.append((h, c2, u0))
                prof = np.array(prof)
                k = int(np.argmin(prof[:, 1]))
                h, c2, u0 = prof[k]
                cur = _chi2(data, assign, h, u0, s, rho)[1]
                results.append({'chi2': float(c2), 'assign': assign, 's': s, 'rho': rho, 'h': float(h),
                                'u0': float(u0), 'currents_A': cur,
                                'amps_per_volt': {w: I / drives[int(w.split()[-1])] for w, I in cur.items()},
                                'dof': n - 2 - len(data), 'profile': prof})
    results.sort(key=lambda r: r['chi2'])
    return results


def print_results(results, n=6):
    for r in results[:n]:
        print(f"chi2 {r['chi2']:6.1f} (dof {r['dof']})  h {r['h']:4.1f} um  u0 {r['u0']:+4.1f} um  "
              + ', '.join(f"{w} -> {r['assign'][w]} {r['currents_A'][w] * 1e3:+.0f} mA "
                          f"({r['amps_per_volt'][w]:+.2f} A/V)" for w in r['assign']))
    best = results[0]
    p = best['profile']
    ok = p[p[:, 1] < best['chi2'] * (1 + 1 / max(best['dof'], 1))]   # chi2 + 1, scaled by chi2/dof
    print(f"best: h = {best['h']:.1f} um (statistical range {ok[:, 0].min():.1f}-{ok[:, 0].max():.1f} um; "
          f"geometry/model systematics ~ +-1 um), u0 = {best['u0']:+.1f} um")
