# -*- coding: utf-8 -*-
"""
wire_centerline.py

Automatic centerline of the gap between two pulsing wires, from a confocal
xy scan. No rough edge estimates needed (unlike
pulser_autocalibrate.fit_tilted_wire_gap).

In the scan the two wires are dark and the gap between them is brighter --
strongly where the NV spot is, faintly elsewhere. The NV spot may cover only
part of the gap along the wires, and its brightness varies; the edges of the
gap are what is fitted, not the spot.

Method:
  1. Seed: for a grid of tilt angles, project all pixels onto the normal of
     a line at that angle and build the mean cross-gap profile; the angle
     with the sharpest profile is the wire direction, and the strongest
     rising edge followed by a falling edge (dark -> bright -> dark) is the
     gap.
  2. Global fit: baseline + slope + amplitude x (blurred box between two
     parallel edges), edges shared by the whole image, brightness per band:
     start values and the edge blur. Only pixels within `window_margin`
     outside the gap are used, so the outer wire edges never enter.
  3. Every scan column (line across the wires) is fitted on its own: gap
     center, width, brightness. The centerline is a robust weighted line
     through the column centers: columns without clear contrast, or whose
     gap width does not match (NV spot not filling the gap across), are
     dropped, outliers are rejected iteratively, and every column's weight is
     capped (column_floor) so the bright spot cannot dominate the tilt.
     Uncertainties from the line fit (scaled by its reduced chi^2).

    import wire_centerline as wc
    scan = wc.latest_xy_scan()        # newest xy scan (scanning_data_logic history)
    gap = wc.find_wire_gap(scan, plot=True)
    gap.center_y(36.0)                                  # centerline y at x = 36
    gap.point(x=36.0, offset=0.5)                       # 0.5 off the centerline, perpendicular
    gap.locations([-1, -0.5, 0, 0.5, 1], x=36.0)        # list of {'x': .., 'y': ..}

Lengths are in scanner units (those of the scan axes).
"""

import numpy as np
from scipy.optimize import least_squares
from scipy.special import erf


# =============================================================================
# Scan access
# =============================================================================

def _plain(a):
    if hasattr(a, 'magnitude'):
        a = a.magnitude
    try:
        import rpyc
        a = rpyc.classic.obtain(a)
    except Exception:
        pass
    return np.array(a, dtype=float)


def _qudi_module(name, port=18861):
    """A qudi module by name: from the calling notebook's globals if there,
    else from qudi's local namespace server (localhost:port)."""
    import __main__
    if hasattr(__main__, name):
        return getattr(__main__, name)
    import rpyc
    conn = rpyc.connect('localhost', port, config={'allow_all_attrs': True, 'allow_pickle': True,
                                                   'sync_request_timeout': 60})
    ns = conn.root.get_namespace_dict()
    if name not in ns:
        raise NameError(f'qudi module "{name}" is not loaded (available: {sorted(ns)})')
    _qudi_module._conns = getattr(_qudi_module, '_conns', []) + [conn]     # keep the connection alive
    return ns[name]


def latest_xy_scan(logic=None, x_axis='x', y_axis='y'):
    """The most recent 2D scan over (x_axis, y_axis): `logic` may be
    scanning_data_logic (searches its scan history, newest first) or
    scanning_probe_logic (its current scan_data). None (default):
    scanning_data_logic from the notebook, or from qudi's namespace server."""
    if logic is None:
        logic = _qudi_module('scanning_data_logic')
    if hasattr(logic, '_scan_history'):
        for entry in reversed(list(logic._scan_history)):
            sd = entry[0] if isinstance(entry, (tuple, list)) else entry
            if set(sd.settings.axes) == {x_axis, y_axis} and sd.data is not None:
                return sd
        raise ValueError(f'no ({x_axis}, {y_axis}) scan in the scan history')
    sd = logic.scan_data
    if sd is None or set(sd.settings.axes) != {x_axis, y_axis}:
        raise ValueError(f'current scan_data is not a ({x_axis}, {y_axis}) scan; pass scanning_data_logic')
    return sd


def scan_to_image(scan, channel='Sum', x_axis='x', y_axis='y'):
    """(img[ix, iy], x, y) from a qudi ScanData."""
    axes = tuple(scan.settings.axes)
    img = _plain(scan.data[channel])
    img = np.moveaxis(img, [axes.index(x_axis), axes.index(y_axis)], [0, 1])
    rng = [tuple(map(float, r)) for r in scan.settings.range]
    res = [int(r) for r in scan.settings.resolution]
    x = np.linspace(*rng[axes.index(x_axis)], res[axes.index(x_axis)])
    y = np.linspace(*rng[axes.index(y_axis)], res[axes.index(y_axis)])
    return img, x, y


# =============================================================================
# Model
# =============================================================================

def _noise_floor(img):
    """Positive floor for the shot-noise weights 1/sqrt(signal): the 5th percentile of
    the scan, at least 1 % of its median and at least 1 (zero-count pixels)."""
    return max(float(np.percentile(img, 5)), 0.01 * float(np.median(np.abs(img))), 1.0)


def _edge(u, s):
    return 0.5 * (1.0 + erf(u / (np.sqrt(2.0) * s)))


def _box(v, w, s):
    """Blurred box: 1 inside |v| < w/2, 0 outside, edges blurred by s."""
    return _edge(v + 0.5 * w, s) - _edge(v - 0.5 * w, s)


class WireGap:
    """Result of find_wire_gap. Centerline: points p with
    (p - p0) . normal = 0; normal points towards +y (wires along x) or +x
    (wires along y)."""

    def __init__(self, info):
        self.info = info
        self.p0 = np.array(info['center_point'])
        self.direction = np.array(info['direction'])
        self.normal = np.array(info['normal'])
        self.width = info['gap_width']
        self.x_axis, self.y_axis = info['x_axis'], info['y_axis']

    def center_y(self, x):
        """Centerline y at x (wires roughly along x)."""
        return self.p0[1] + (np.asarray(x, float) - self.p0[0]) * self.direction[1] / self.direction[0]

    def center_x(self, y):
        """Centerline x at y (wires roughly along y)."""
        return self.p0[0] + (np.asarray(y, float) - self.p0[1]) * self.direction[0] / self.direction[1]

    def center_se_at(self, pos):
        """Standard error (perpendicular) of the centerline at position pos
        along the wires (x for wires along x, y for wires along y)."""
        cov = self.info['line_cov']
        if cov is None:
            return float('nan')
        k = 0 if self.info['along'] == 'x' else 1
        dx = np.asarray(pos, float) - self.p0[k]
        return np.sqrt(cov[1][1] + 2 * dx * cov[0][1] + dx ** 2 * cov[0][0]) * abs(self.direction[k])

    def offset_of(self, x, y):
        """Signed perpendicular distance of (x, y) from the centerline."""
        return (np.array([x, y], float) - self.p0) @ self.normal

    def point(self, x=None, y=None, offset=0.0):
        """Point at perpendicular distance `offset` from the centerline, at
        the centerline point with this x (or y; default: the scan center).
        Returns {x_axis: .., y_axis: ..}."""
        if x is not None:
            c = np.array([x, self.center_y(x)])
        elif y is not None:
            c = np.array([self.center_x(y), y])
        else:
            c = self.p0
        p = c + offset * self.normal
        return {self.x_axis: float(p[0]), self.y_axis: float(p[1])}

    def locations(self, offsets, x=None, y=None):
        """[point(x, y, offset) for offset in offsets] -- e.g. for
        pulser_autocalibrate.run_field_mapping_experiment."""
        return [self.point(x=x, y=y, offset=o) for o in offsets]

    def edge_lines(self):
        """The two fitted gap edges as (point, direction) pairs (lower, upper
        along the normal)."""
        h = 0.5 * self.width
        return [(self.p0 - h * self.normal, self.direction), (self.p0 + h * self.normal, self.direction)]

    def __repr__(self):
        i = self.info
        return (f"WireGap(center ({i['center_point'][0]:.3f}, {i['center_point'][1]:.3f}) "
                f"+- {i['center_se']:.3f}, angle {i['angle_deg']:.2f} +- {i['angle_se_deg']:.2f} deg, "
                f"gap width {i['gap_width']:.3f} +- {i['gap_width_se']:.3f}, edge blur {i['edge_sigma']:.3f}, "
                f"{i['n_columns_used']}/{len(i['columns'])} columns)")


# =============================================================================
# Fit
# =============================================================================

def _seed(img, x, y, angles_deg, min_gap, max_gap):
    """Tilt and gap (center offset, width) from projected profiles."""
    X, Y = np.meshgrid(x, y, indexing='ij')
    xc, yc = 0.5 * (x[0] + x[-1]), 0.5 * (y[0] + y[-1])
    dy = abs(y[1] - y[0])
    # normalize every column (level and robust scale) so that a bright NV spot in a
    # few columns does not dominate the profile shape over the wires seen in all columns
    z = img - np.median(img, axis=1, keepdims=True)
    mad = np.median(np.abs(z), axis=1, keepdims=True)
    z = z / np.maximum(mad, 0.2 * np.median(mad) + 1e-12)
    z = np.clip(z, -10, 10)
    best = None
    for a in np.radians(angles_deg):
        v = (Y - yc) * np.cos(a) - (X - xc) * np.sin(a)
        edges = np.arange(v.min(), v.max() + dy, dy)
        idx = np.digitize(v.ravel(), edges)
        cnt = np.bincount(idx, minlength=len(edges) + 1)[1:len(edges)]
        sm = np.bincount(idx, weights=z.ravel(), minlength=len(edges) + 1)[1:len(edges)]
        ok = cnt >= max(3, 0.3 * np.median(cnt[cnt > 0]))
        vb = 0.5 * (edges[:-1] + edges[1:])[ok]
        pb = sm[ok] / cnt[ok]
        if len(pb) < 8:
            continue
        pb = np.convolve(pb, np.ones(3) / 3, mode='same')
        d = np.gradient(pb, vb)
        score = float(np.sum(d[2:-2] ** 2))
        if best is None or score > best[0]:
            best = (score, a, vb, pb, d)
    score, a, vb, pb, d = best
    # strongest rising edge (dark wire -> bright gap) followed by a falling edge
    cand = None
    for i in range(2, len(vb) - 2):
        for j in range(i + 1, len(vb) - 2):
            g = vb[j] - vb[i]
            if g < min_gap or g > max_gap:
                continue
            s = d[i] - d[j]
            if cand is None or s > cand[0]:
                cand = (s, vb[i], vb[j])
    if cand is None:
        raise RuntimeError('no dark-bright-dark gap structure found in the scan')
    _, v1, v2 = cand
    return a, 0.5 * (v1 + v2), v2 - v1, score


def _bands(x, band_width):
    if band_width is None:                       # one column per band
        return [np.array([i]) for i in range(len(x))]
    n = max(1, int(round((x[-1] - x[0]) / band_width)))
    edges = np.linspace(x[0], x[-1] + 1e-12, n + 1)
    return [np.flatnonzero((x >= edges[k]) & (x < edges[k + 1])) for k in range(n)]


def _global_fit(img, x, y, bands, geo0, window_margin, width_bounds=None):
    """Variable projection: shared geometry (v0, angle, width, sigma) by
    nonlinear least squares, per-band (baseline, slope, amplitude) solved
    linearly inside. Returns (geo, per-band linear params, masks, residuals)."""
    X, Y = np.meshgrid(x, y, indexing='ij')
    xc, yc = 0.5 * (x[0] + x[-1]), 0.5 * (y[0] + y[-1])
    wts = 1.0 / np.sqrt(np.clip(img, _noise_floor(img), None))     # ~ shot noise

    def design(geo):
        v0, a, w, s = geo
        v = (Y - yc) * np.cos(a) - (X - xc) * np.sin(a) - v0
        return v, _box(v, w, s)

    def solve(geo, return_all=False):
        v0, a, w, s = geo
        v, G = design(geo)
        res, lin, masks = [], [], []
        for b in bands:
            vb, Gb, Ib = v[b].ravel(), G[b].ravel(), img[b].ravel()
            # smooth window: pixels within window_margin outside the gap edges
            Wb = wts[b].ravel() * _box(vb, w + 2 * window_margin, 0.25)
            A = np.column_stack([np.ones(vb.size), vb, Gb]) * Wb[:, None]
            coef = np.linalg.lstsq(A, Ib * Wb, rcond=None)[0]
            res.append(A @ coef - Ib * Wb)
            lin.append(coef)
            masks.append(Wb > 0.5 * wts[b].ravel())
        r = np.concatenate(res)
        return (r, np.array(lin), masks) if return_all else r

    wb = width_bounds or (0.2 * geo0[2], 3.0 * geo0[2])
    lo = [-np.inf, -np.radians(30), wb[0], 0.02]
    hi = [np.inf, np.radians(30), wb[1], 0.5 * wb[1]]
    geo0 = np.clip(geo0, np.array(lo) + 1e-9, np.array(hi) - 1e-9)
    fit = least_squares(solve, geo0, bounds=(lo, hi), x_scale=[0.1, 0.01, 0.1, 0.05])
    r, lin, masks = solve(fit.x, return_all=True)
    return fit.x, lin, masks, r, fit


def _column_fit(img, x, y, b, geo, window_margin, width_bounds=None):
    """Columns b on their own, angle and blur fixed: center v0 (working
    coords), its standard error, gap width, amplitude and SNR."""
    a, s = geo[1], geo[3]
    X, Y = np.meshgrid(x[b], y, indexing='ij')
    xc, yc = 0.5 * (x[0] + x[-1]), 0.5 * (y[0] + y[-1])
    vr = ((Y - yc) * np.cos(a) - (X - xc) * np.sin(a)).ravel()
    I = img[b].ravel()
    W = 1.0 / np.sqrt(np.clip(I, _noise_floor(img), None))

    def resid(q, full=False):
        v = vr - q[0]
        Wt = W * _box(v, q[1] + 2 * window_margin, 0.25)
        A = np.column_stack([np.ones(v.size), v, _box(v, q[1], s)]) * Wt[:, None]
        c = np.linalg.lstsq(A, I * Wt, rcond=None)[0]
        r = A @ c - I * Wt
        return (r[Wt > 0.5 * W], c) if full else r

    wb = width_bounds or (0.3 * geo[2], 2.5 * geo[2])
    fit = least_squares(resid, [geo[0], np.clip(geo[2], wb[0] + 1e-6, wb[1] - 1e-6)],
                        bounds=([geo[0] - geo[2], wb[0]], [geo[0] + geo[2], wb[1]]))
    r, c = resid(fit.x, full=True)
    noise = np.std(r) + 1e-12
    J = fit.jac
    cov = np.linalg.pinv(J.T @ J) * noise ** 2
    se_v0 = float(np.sqrt(max(cov[0, 0], 0.0)))
    return fit.x[0], se_v0, fit.x[1], c[2], abs(c[2]) * np.mean(W) / noise


def _robust_line(xk, yk, sek, floor, n_sigma=3.0, n_iter=10):
    """Weighted line y = m (x - x_mean) + c through (xk, yk), weights
    1/(se^2 + floor^2), iteratively dropping points more than n_sigma robust
    sigma off. Returns (m, c, cov(m, c), used mask, x_mean)."""
    use = np.ones(len(xk), bool)

    def solve(use):
        w = 1.0 / (sek[use] ** 2 + floor ** 2)
        xm = np.average(xk[use], weights=w)
        A = np.column_stack([xk[use] - xm, np.ones(use.sum())])
        sol = np.linalg.lstsq(A * np.sqrt(w)[:, None], yk[use] * np.sqrt(w), rcond=None)[0]
        return sol, xm, w, A

    for _ in range(n_iter):
        sol, xm, w, A = solve(use)
        z = (yk - (sol[0] * (xk - xm) + sol[1])) / np.sqrt(sek ** 2 + floor ** 2)
        scale = max(1.0, 1.4826 * np.median(np.abs(z[use])))
        new = np.abs(z) <= n_sigma * scale
        if new.sum() < 3 or np.array_equal(new, use):
            break
        use = new
    sol, xm, w, A = solve(use)
    res = yk[use] - A @ sol
    chi2r = float(np.sum(w * res ** 2) / max(1, use.sum() - 2))
    cov = np.linalg.inv((A * w[:, None]).T @ A) * max(1.0, chi2r)
    return sol[0], sol[1], cov, use, xm


def _find(img, x, y, along, band_width, window_margin, expected_gap, max_angle_deg, min_col_snr,
          reject_width_frac, column_floor, plot, ax, x_axis, y_axis, gap_bright=True):
    import warnings
    sign = 1.0 if gap_bright else -1.0
    if along == 'y':        # work with the wires along the first index, transform back at the end
        img_w, xa, ya = img.T, y, x
    else:
        img_w, xa, ya = img, x, y
    span = ya[-1] - ya[0]
    gmin, gmax = ((0.75 * expected_gap, 1.25 * expected_gap) if expected_gap
                  else (2.5 * abs(ya[1] - ya[0]), 0.6 * span))
    wbounds = (gmin, gmax) if expected_gap else None
    angles = np.arange(-max_angle_deg, max_angle_deg + 1e-9, 0.25)
    a0, v00, w0, _ = _seed(sign * img_w, xa, ya, angles, gmin, gmax)
    # 1. global fit (shared geometry, brightness per 1-unit band): start values and edge blur
    geo0 = np.array([v00, a0, w0, 0.3 * abs(ya[1] - ya[0]) + 0.1 * w0])
    geo = _global_fit(img_w, xa, ya, _bands(xa, 1.0), geo0, window_margin, wbounds)[0]
    xc, yc = 0.5 * (xa[0] + xa[-1]), 0.5 * (ya[0] + ya[-1])
    cols = _bands(xa, band_width)
    floor = column_floor if column_floor is not None else 0.02 * (expected_gap or geo[2])
    cov, xm = None, xc
    # 2. every column on its own; 3. robust line through the column centers (twice: angle update)
    for _ in range(2):
        a = geo[1]
        rows = []
        for b in cols:
            v0k, sev, wk, ak, snr = _column_fit(img_w, xa, ya, b, geo, window_margin)
            xk = float(np.mean(xa[b]))
            yk = yc + (v0k + (xk - xc) * np.sin(a)) / np.cos(a)
            rows.append((xk, yk, sev / np.cos(a), wk, ak, snr))
        R = np.array(rows)
        strong = (R[:, 5] >= min_col_snr) & (sign * R[:, 4] > 0)
        w_ref = expected_gap or (np.median(R[strong, 3]) if strong.sum() else geo[2])
        cand = strong & (np.abs(R[:, 3] - w_ref) <= reject_width_frac * w_ref)
        if cand.sum() < 3:
            warnings.warn('wire_centerline: fewer than 3 columns with a clear gap of the expected width; '
                          'using the global fit only. Check the plot / expected_gap.')
            used = cand
            m, xm = np.tan(a), xc
            c0 = yc + geo[0] / np.cos(a)
            cov = None
            break
        m, c0, cov, u, xm = _robust_line(R[cand, 0], R[cand, 1], R[cand, 2], floor)
        used = np.zeros(len(R), bool)
        used[np.flatnonzero(cand)[u]] = True
        a_new = np.arctan(m)
        v0_new = (c0 - yc) * np.cos(a_new) - (xm - xc) * np.sin(a_new)
        geo = np.array([v0_new, a_new, float(np.median(R[used, 3])), geo[3]])

    a = np.arctan(m)
    widths = R[used, 3] if used.sum() else np.array([geo[2]])
    w = float(np.median(widths))
    w_se = float(1.2533 * 1.4826 * np.median(np.abs(widths - w)) / np.sqrt(max(1, len(widths))))
    p0 = np.array([xm, c0])
    d = np.array([np.cos(a), np.sin(a)])
    nrm = np.array([-np.sin(a), np.cos(a)])
    if cov is not None:
        se_m, se_c = np.sqrt(np.diag(cov))
        line_cov = cov.tolist()
    else:
        se_m, se_c, line_cov = np.nan, np.nan, None
    if along == 'y':        # swap back to (x, y)
        p0, d, nrm = p0[::-1], d[::-1], nrm[::-1]
    info = {
        'x_axis': x_axis, 'y_axis': y_axis, 'along': along,
        'center_point': tuple(map(float, p0)), 'direction': tuple(map(float, d)), 'normal': tuple(map(float, nrm)),
        'angle_deg': float(np.degrees(a)), 'angle_se_deg': float(np.degrees(se_m / (1 + m ** 2))),
        'center_se': float(se_c * np.cos(a)), 'line_cov': line_cov,
        'gap_width': w, 'gap_width_se': w_se, 'edge_sigma': float(geo[3]),
        'columns': [{'pos': float(r[0]), 'center': float(r[1]), 'center_se': float(r[2]), 'width': float(r[3]),
                     'amplitude': float(r[4]), 'snr': float(r[5]), 'used': bool(u_)} for r, u_ in zip(R, used)],
        'n_columns_used': int(used.sum()),
        'seed': {'angle_deg': float(np.degrees(a0)), 'width': float(w0)},
    }
    gap = WireGap(info)
    if geo[3] > 0.35 * w or (expected_gap and abs(w - expected_gap) > 0.15 * expected_gap):
        warnings.warn(f'wire_centerline: fitted gap width {w:.3f} with edge blur {geo[3]:.3f} looks like the NV '
                      f'spot rather than the wire edges; pass expected_gap (the gap width as seen in the scan) and check the plot.')
    if plot:
        plot_wire_gap(img, x, y, gap, ax=ax)
    return gap


def _seed_score(img, x, y, max_angle_deg):
    angles = np.arange(-max_angle_deg, max_angle_deg + 1e-9, 0.25)
    try:
        return _seed(img, x, y, angles, 2.5 * abs(y[1] - y[0]), 0.6 * (y[-1] - y[0]))[3]
    except RuntimeError:
        return -np.inf


def find_wire_gap(scan, channel='Sum', x_axis='x', y_axis='y', along='x', expected_gap=None,
                  window_margin=1.5, band_width=None, max_angle_deg=10.0, min_col_snr=5.0,
                  reject_width_frac=0.2, column_floor=None, gap_bright=True, plot=False, ax=None):
    """
    Fit the centerline of the bright gap between two dark wires in an xy scan.

    scan: qudi ScanData, or a tuple (img[ix, iy], x, y).
    along: rough wire direction, 'x' (default), 'y', or 'auto' (the one with
        the sharper cross-wire profile; unreliable for a small NV spot on
        faint wires).
    gap_bright: True (default) if the gap is brighter than the wires (NV
        fluorescence scans); False if the wires are brighter (e.g. reflective
        wires under the flashlight).
    expected_gap: width of the bright gap AS SEEN IN THE SCAN (scanner units),
        i.e. between the inner shadow edges -- not the nominal wire spacing,
        which shadows / blur make look narrower (~2.8 on this setup for the
        nominal 6 um gap). Optional but recommended: the fitted width is kept
        within 0.75-1.25x of it and columns whose own width is more than
        reject_width_frac off are not used (needed when a small NV spot does
        not fill the gap across). The centerline is accurate as long as the
        two shadows are symmetric about it.
    window_margin: how far outside the gap edges pixels are used.
    band_width: columns are fitted one by one (None, default) or in groups of
        this length along the wires.
    min_col_snr: columns whose own gap contrast (SNR) is below this are not
        used for the centerline.
    reject_width_frac: columns whose own gap width differs from expected_gap
        (or the median width) by more than this fraction are not used -- e.g.
        where the NV spot does not fill the gap across.
    column_floor: systematic floor added in quadrature to every column's
        center uncertainty (default 2 % of the gap width), so a few very
        bright columns cannot dominate the line.
    Returns a WireGap (gap.info holds all numbers and per-band diagnostics).
    """
    if isinstance(scan, tuple):
        img, x, y = (np.asarray(a, float) for a in scan)
    else:
        img, x, y = scan_to_image(scan, channel, x_axis, y_axis)
    if along == 'auto':
        sgn = 1.0 if gap_bright else -1.0
        along = 'x' if (_seed_score(sgn * img, x, y, max_angle_deg)
                        >= _seed_score(sgn * img.T, y, x, max_angle_deg)) else 'y'
    return _find(img, x, y, along, band_width, window_margin, expected_gap, max_angle_deg, min_col_snr,
                 reject_width_frac, column_floor, plot, ax, x_axis, y_axis, gap_bright)


def plot_wire_gap(img, x, y, gap, ax=None, offsets=None):
    """Scan with the fitted gap edges, centerline, per-band status and
    (optionally) points at the given perpendicular offsets."""
    import matplotlib.pyplot as plt
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 6))
    ax.imshow(img.T, origin='lower', extent=[x[0], x[-1], y[0], y[-1]], cmap='gray', aspect='equal')
    t = np.linspace(-2, 2, 3) * max(x[-1] - x[0], y[-1] - y[0])
    for (p, dvec), ls in zip(gap.edge_lines(), ('--', '--')):
        ax.plot(p[0] + t * dvec[0], p[1] + t * dvec[1], ls, color='tab:orange', lw=1.2)
    ax.plot(gap.p0[0] + t * gap.direction[0], gap.p0[1] + t * gap.direction[1], '-', color='yellow', lw=1.8,
            label=f'centerline (gap {gap.width:.2f}, {gap.info["angle_deg"]:.2f} deg)')
    for used, mk, col in ((True, '.', 'lime'), (False, 'x', 'red')):
        sel = [c for c in gap.info['columns'] if c['used'] == used]
        if gap.info['along'] == 'x':
            ax.plot([c['pos'] for c in sel], [c['center'] for c in sel], mk, color=col, ms=4)
        else:
            ax.plot([c['center'] for c in sel], [c['pos'] for c in sel], mk, color=col, ms=4)
    if offsets is not None:
        pts = gap.locations(offsets)
        ax.plot([p[gap.x_axis] for p in pts], [p[gap.y_axis] for p in pts], 'P', color='lime', ms=9,
                label='offset points')
    ax.set_xlim(x[0], x[-1]); ax.set_ylim(y[0], y[-1])
    ax.set_xlabel(gap.x_axis); ax.set_ylabel(gap.y_axis)
    ax.legend(loc='upper right', fontsize=8)
    ax.set_title(repr(gap).replace('WireGap(', '').rstrip(')'), fontsize=7)
    return ax
