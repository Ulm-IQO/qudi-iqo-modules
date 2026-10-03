# -*- coding: utf-8 -*-
"""
coil_characterization.py

Interactive, human-in-the-loop, ONE-COIL-AT-A-TIME workflow for
calibrating a 3-axis electromagnet coil set against the CW ODMR spectrum
of an NV ensemble.

Workflow
--------
Earlier versions of this pipeline tried to fit all three coils jointly
(either fully automatically, or via independent per-point field fits
across an arbitrary current grid). Both approaches turned out to be
fragile: automatic peak-detection occasionally locked onto unphysical
dips, and independent per-point field fits are only weakly constrained
(3 unknowns from as few as 1-2 matched dips).

This version instead calibrates ONE coil at a time, in three explicit,
inspectable steps, exploiting the physical prior that a single coil
produces a field along a FIXED direction (nominally along its lab axis,
possibly tilted by a small unknown angle) that scales linearly with its
current:

    1. `sweep_single_coil_odmr()` -- the user picks ONE coil axis and an
       explicit list of currents to test. The other two coils are
       explicitly forced to 0 A throughout. ODMR is run at each current
       and the raw spectra collected into `total_sweep`. Purely a
       data-acquisition step; does no fitting.

    2. `pick_dips_interactive()` -- the user visually/interactively marks
       approximate dip locations (and acceptable location ranges) for
       every current, directly by clicking on each spectrum in turn.
       This is a thin loop around
       `nv_field_fitting.pick_dips_interactive_spectrum()` -- see that
       function's docstring for the exact picking controls. This
       replaces trying to read precise frequency values off a (possibly
       too-small/sparse) static tick axis by eye.

       `fit_sweep_dips()` then fits Lorentzian dips constrained to stay
       within those user-marked ranges (see
       `nv_field_fitting.fit_lorentzian_dips_guided`) -- the fix for
       "fitting to unphysical peaks": the user, having looked at the
       spectrum, tells the fitter approximately where the real dips are
       (and how far a fitted center is allowed to wander from that).

    3. `fit_coil_axis()` -- given the fitted dips from ALL currents in
       the sweep, jointly fits a single field-per-amp vector `u` (Hz/A)
       and background offset `B_offset` shared across the WHOLE sweep
       (see `nv_field_fitting.fit_coil_axis_from_dips`), i.e. it fits the
       model B(I) = u*I + B_offset using every current's matched dips
       simultaneously. Because this has only 6 free parameters shared
       across potentially dozens of data points (rather than 3 per
       point), it is much better constrained than fitting each point's
       field independently.

    Steps 2 (fitting) and 3 can be done together via
    `interactive_fit_coil_axis()` (dip-fitting + field-fitting; dip-
    picking itself is still a separate, prior call to
    `pick_dips_interactive()`).

Once this has been done independently for each of the X, Y, and Z coils,
the three single-axis fits are combined into the full 3x3 calibration
matrix M (B_NV = M @ I_coil + B_offset):

    4. `measure_mixed_currents_odmr()` + `pick_dips_interactive()` +
       `fit_sweep_dips()` -- measure and fit 5-6 points with SEVERAL
       coils on at once, with the total field along a direction where
       all 8 dips are resolved (let `plan_mixed_currents()` choose it for
       your `quantization_axes`), then `resolve_coil_calibration()` --
       build M.

       This step is needed because the four NV axes map onto themselves
       under 48 lab-frame rotations/reflections and ODMR only sees |B.n|,
       so a single-coil sweep fixes each coil's |u|, its component along
       the nominal axis and its tilt angle, but NOT which way it tilts
       (4-8 equivalent candidates per coil, depending on how the coil
       axis sits relative to the NV axes, given the prior that +X points
       roughly along +x etc.). Mixed-current spectra depend on how the
       coils' fields add, which singles out one combination. A final
       joint fit of M over the mixed points refines it. This works
       without any measurable background field.

`assemble_coil_calibration()` is the older shortcut that simply stacks
the three single-coil u's as columns (and cross-checks their B_offsets);
its off-axis components are only correct up to the ambiguity above.

qudi dependencies
------------------
This file assumes the following qudi objects are available as GLOBAL
variables in the calling namespace (as they would be if this file's
contents were pasted directly into a Jupyter notebook connected to a
running qudi instance):

    pulsed_master_logic   -- PulsedMasterLogic
    coil_control_logic    -- CoilControlLogic (exposes set_current(axis, current))

If this file is instead `import`-ed as a module (recommended for reuse),
these names will NOT automatically be visible inside it, since Python
module globals are isolated per file. Before calling anything from this
module that touches hardware (`sweep_single_coil_odmr`,
`measure_mixed_currents_odmr`), inject the
qudi objects into its namespace once, e.g.:

    import coil_characterization as coilchar
    coilchar.pulsed_master_logic = pulsed_master_logic
    coilchar.coil_control_logic = coil_control_logic

`fit_sweep_dips`, `fit_coil_axis`, `interactive_fit_coil_axis`,
`pick_dips_interactive`, `compute_coil_axis_direction`,
`resolve_coil_calibration`, `assemble_coil_calibration`, and all
plotting functions have NO qudi
dependency -- they only require `nv_field_fitting.py` (a fully standalone
module) and the data structures produced by `sweep_single_coil_odmr` /
`measure_mixed_currents_odmr`.

Interactive dip-picking (`pick_dips_interactive`) requires the ipympl
interactive backend (`%matplotlib widget`, plus `pip install ipympl` and a
FULL Jupyter server restart if not already set up) -- see
`nv_field_fitting.pick_dips_interactive_spectrum()`'s docstring for
details and controls (this module's `pick_dips_interactive()` is a thin
per-point loop around that same function, so the controls are identical).

Assumes the relevant ODMR PulseSequence has ALREADY been generated,
sampled, and loaded (e.g. via
pulsed_master_logic.generate_predefined_sequence(..., sample_and_load=True));
`sweep_single_coil_odmr()` only starts/stops/collects the measurement at
each coil setting, it does not (re-)generate or (re-)sample anything.
"""

import os
import re
import time
import numpy as np
import matplotlib.pyplot as plt

import nv_field_fitting as nvfit


# =============================================================================
# Generic hardware helpers
# =============================================================================

def _to_plain_array(data):
    """Convert to a plain float ndarray, working around array-likes
    (e.g. pint Quantities) whose __array__() doesn't support the
    NumPy 2.0 dtype/copy keyword protocol."""
    if hasattr(data, 'magnitude'):
        data = data.magnitude
    arr = np.array(data)
    return arr.astype(float, copy=False)


def _wait_while(condition_fn, description, timeout, poll_interval=0.2):
    """Poll condition_fn() until it returns False, or raise TimeoutError
    after `timeout` seconds."""
    t_start = time.time()
    while condition_fn():
        if time.time() - t_start > timeout:
            raise TimeoutError(f'Timed out waiting for {description}.')
        time.sleep(poll_interval)


def _get_pulseblaster_hw():
    """
    Drill down through pulsed_master_logic's connectors to obtain a direct
    reference to the raw PulseBlaster hardware module, bypassing the AWG
    entirely.

    Confirmed connector chain:
      pulsed_master_logic.sequencegeneratorlogic() -> SequenceGeneratorLogic
      SequenceGeneratorLogic.pulsegenerator()      -> AwgPulseBlasterInterfuse
      AwgPulseBlasterInterfuse.pulseblaster()      -> raw PulseBlaster hw module
    """
    seq_gen = pulsed_master_logic.sequencegeneratorlogic()
    interfuse = seq_gen.pulsegenerator()
    pb_hw = interfuse.pulseblaster()
    return pb_hw, interfuse


def _point_label(point):
    """Short human-readable current label for a sweep point: 'X = 0.300 A'
    for single-coil points, 'I = (0.300, 0.300, 0.300) A' for mixed ones."""
    if point.get('axis') in ('X', 'Y', 'Z') and 'current' in point:
        return f"{point['axis']} = {point['current']:.3f} A"
    if 'current_vec' in point:
        ix, iy, iz = point['current_vec']
        return f"I = ({ix:.3f}, {iy:.3f}, {iz:.3f}) A"
    return ''


def _collect_dips(total_sweep):
    """
    Gather each point's fitted dip centers, 1-sigma center errors and
    HWHMs (all Hz) from `'dip_fit'`, in the form the nv_field_fitting
    field fits expect. Points with no fit (or zero dips) get empty
    arrays; NaN/non-positive center errors (failed covariance) fall back
    to 100 kHz.
    """
    observed_dips_list, dip_errors_list, dip_widths_list = [], [], []
    for p in total_sweep:
        fit = p.get('dip_fit')
        if fit is None or fit['n_dips'] == 0:
            observed_dips_list.append(np.array([]))
            dip_errors_list.append(np.array([]))
            dip_widths_list.append(np.array([]))
            continue
        observed_dips_list.append(np.asarray(fit['centers'], dtype=float))
        center_errs = np.asarray(fit['center_errs'], dtype=float)
        errs = np.where(np.isfinite(center_errs) & (center_errs > 0),
                         center_errs, 1.0e5)  # fallback error if fit gave NaN
        dip_errors_list.append(errs)
        dip_widths_list.append(np.abs(np.asarray(fit['sigmas'], dtype=float)))
    return observed_dips_list, dip_errors_list, dip_widths_list


def _set_coil_currents(ix, iy, iz):
    """Set all three coil currents via coil_control_logic.set_current()."""
    coil_control_logic.set_current('X', float(ix))
    coil_control_logic.set_current('Y', float(iy))
    coil_control_logic.set_current('Z', float(iz))


def _run_odmr_sequence_and_collect(seq_name, num_sweeps, poll_interval=0.5,
                                   timeout=None, stall_timeout=120.0,
                                   min_sweeps_settle_time=6.0, skip_first_points=0):
    """
    Reload the (already generated+sampled+loaded) ODMR PulseSequence
    (fast -- PB-only re-write, AWG untouched), run it until num_sweeps
    completed sweeps are reported, then stop and return (freq, signal).

    Parameters
    ----------
    seq_name : str
        Name of the already-generated/sampled/loaded PulseSequence.
    num_sweeps : int
        Number of sweeps to average before stopping.
    poll_interval : float
        Polling interval (s) while waiting for sweeps to accumulate.
    timeout : float, optional
        Overall timeout (s) for the measurement. Defaults to
        max(300, num_sweeps * 5).
    stall_timeout : float
        If elapsed_sweeps does not increase for this many seconds, raise
        an error rather than waiting indefinitely.
    min_sweeps_settle_time : float
        Time (s) to wait after starting the measurement before beginning
        to poll elapsed_sweeps (must be at least as long as
        pulsed_master_logic.timer_interval, ideally 1.5-2x it).
    skip_first_points : int, default 0
        Drop the first n points of the returned spectrum (the first n
        frequencies of the sweep, in measurement order), e.g. points
        distorted by NV charge dynamics at the start of each sweep. See
        `nv_field_fitting.skip_first_odmr_points`.

    Returns
    -------
    freq : ndarray
    signal : ndarray
        With the first `skip_first_points` points removed.
    """
    pb_hw, _ = _get_pulseblaster_hw()
    if pb_hw.get_status()[0] != 0:
        pb_hw.pulser_off()

    pulsed_master_logic.load_sequence(seq_name)
    _wait_while(lambda: pulsed_master_logic.status_dict['loading_busy'],
                f'"{seq_name}" to load', 60.0)

    if pulsed_master_logic.loaded_asset[0] != seq_name:
        raise RuntimeError(
            f'Failed to load "{seq_name}"; currently loaded: {pulsed_master_logic.loaded_asset}'
        )

    pulsed_master_logic.toggle_pulsed_measurement(True)
    _wait_while(lambda: not pulsed_master_logic.status_dict['measurement_running'],
                'measurement to start', 30.0)

    t_settle_start = time.time()
    while time.time() - t_settle_start < min_sweeps_settle_time:
        time.sleep(poll_interval)

    if timeout is None:
        timeout = max(300.0, num_sweeps * 5.0)

    t_start = time.time()
    last_sweeps = -1
    last_change_time = t_start

    while pulsed_master_logic.elapsed_sweeps < num_sweeps:
        now = time.time()
        if now - t_start > timeout:
            raise TimeoutError(
                f'ODMR measurement for "{seq_name}" timed out after {timeout:.0f} s '
                f'(reached {pulsed_master_logic.elapsed_sweeps}/{num_sweeps} sweeps).'
            )
        current_sweeps = pulsed_master_logic.elapsed_sweeps
        if current_sweeps != last_sweeps:
            last_sweeps = current_sweeps
            last_change_time = now
        elif now - last_change_time > stall_timeout:
            raise RuntimeError(
                f'elapsed_sweeps has not increased for {stall_timeout:.0f} s '
                f'(stuck at {current_sweeps}). This likely means the fast counter '
                f'hardware does not report elapsed_sweeps -- check '
                f'pulsed_master_logic.elapsed_sweeps manually while a measurement runs.'
            )
        time.sleep(poll_interval)

    pulsed_master_logic.toggle_pulsed_measurement(False)
    _wait_while(lambda: pulsed_master_logic.status_dict['measurement_running'],
                'measurement to stop', 30.0)

    data = pulsed_master_logic.signal_data
    freq, signal = nvfit.skip_first_odmr_points(
        _to_plain_array(data[0]), _to_plain_array(data[1]), skip_first_points)

    if not np.any(signal):
        raise RuntimeError(
            f'ODMR signal for "{seq_name}" is all zeros after waiting for '
            f'{pulsed_master_logic.elapsed_sweeps} sweeps. Check hardware / '
            f'counter wiring, or increase min_sweeps_settle_time if this '
            f'happens intermittently right after starting a measurement.'
        )

    return freq, signal


# =============================================================================
# Step 1: single-coil current sweep + ODMR acquisition
# =============================================================================

def sweep_single_coil_odmr(axis, currents, seq_name, num_sweeps=10000,
                            settle_time=0.0, reset_after=True,
                            plot=False, ncols=3, figsize_per_axes=(4.0, 3.0),
                            skip_first_points=0, before_each=None):
    """
    Sweep ONE coil's current over an explicit, user-supplied list of
    values, running one ODMR measurement at each point. The other two
    coils are explicitly forced to 0 A before the sweep begins (not
    merely assumed to already be at 0), so this sweep genuinely isolates
    the chosen coil's effect.

    Assumes the ODMR PulseSequence named `seq_name` has ALREADY been
    generated, sampled, and loaded -- this function only
    starts/stops/collects the measurement at each coil setting, it does
    not (re-)generate or (re-)sample anything.

    Parameters
    ----------
    axis : {'X', 'Y', 'Z'}
        Which coil to sweep.
    currents : sequence of float
        List of current values (A) to test on `axis`, in the order they
        should be tested.
    seq_name : str
        Name of the already-generated/sampled/loaded ODMR PulseSequence.
    num_sweeps : int, default 10000
        Number of sweeps to average before stopping each measurement.
    settle_time : float, default 0.0
        Time (s) to wait after setting a new coil current, before
        starting the ODMR measurement.
    reset_after : bool, default True
        If True, set all three coils back to 0 A after the sweep
        finishes (or if an exception occurs mid-sweep).
    plot : bool, default False
        If True, plot all acquired raw spectra in a single grid figure
        immediately after the sweep (see `plot_sweep_spectra`). Defaults
        to False, since the typical next step is
        `pick_dips_interactive()`, which already displays every spectrum
        individually (and at a much larger, more legible size) -- the
        grid here would just be a redundant extra figure in that
        workflow. Set True if you want a quick static overview instead
        (e.g. before deciding whether to proceed to interactive picking
        at all).
    ncols, figsize_per_axes :
        Passed through to `plot_sweep_spectra` if `plot=True`.
    skip_first_points : int, default 0
        Drop the first n points of every spectrum (the first n
        frequencies of the sweep, in measurement order), e.g. points
        distorted by NV charge dynamics at the start of each sweep. All
        downstream steps then only ever see the trimmed spectra.
    before_each : callable, optional
        Called with no arguments after each point's currents are set and
        settled, right before its ODMR run -- e.g. a `tracking.ZTracker`
        to re-optimize the focus. A returned dict is stored with the point
        under 'tracking'.

    Returns
    -------
    total_sweep : list of dict
        One entry per current, in the same order as `currents`, each:
            {'axis': axis, 'current': float, 'current_vec': (ix, iy, iz),
             'freq': ndarray, 'signal': ndarray, 'skipped_points': int}
        `'current'` is the scalar value on `axis` (the other two
        components of `'current_vec'` are always 0.0 by construction).
        `'skipped_points'` records how many leading points were dropped.
        This is the primary data structure passed to all downstream steps.
    """
    axis = axis.upper()
    if axis not in ('X', 'Y', 'Z'):
        raise ValueError(f"axis must be one of 'X', 'Y', 'Z'; got {axis!r}.")

    other_axes = [a for a in ('X', 'Y', 'Z') if a != axis]
    total_sweep = []

    try:
        print(f'=== Sweeping coil {axis}: {len(currents)} point(s) ===')
        for other_axis in other_axes:
            coil_control_logic.set_current(other_axis, 0.0)
        print(f'  Coils {other_axes} set to 0 A for this sweep.')

        for i, current in enumerate(currents):
            print(f'--- Point {i + 1}/{len(currents)}: {axis} = {current:.4f} A ---')

            coil_control_logic.set_current(axis, float(current))

            if settle_time > 0:
                print(f'  Waiting {settle_time:.1f} s to settle...')
                time.sleep(settle_time)
            tracking = before_each() if before_each is not None else None

            freq, signal = _run_odmr_sequence_and_collect(
                seq_name, num_sweeps, skip_first_points=skip_first_points)

            current_vec = {'X': (float(current), 0.0, 0.0),
                            'Y': (0.0, float(current), 0.0),
                            'Z': (0.0, 0.0, float(current))}[axis]

            total_sweep.append({'axis': axis, 'current': float(current),
                                 'current_vec': current_vec,
                                 'freq': freq, 'signal': signal,
                                 'skipped_points': int(skip_first_points)})
            if tracking:
                total_sweep[-1]['tracking'] = dict(tracking)

    finally:
        if reset_after:
            _set_coil_currents(0.0, 0.0, 0.0)
            print('All coils reset to 0 A.')

    if plot:
        plot_sweep_spectra(total_sweep, ncols=ncols, figsize_per_axes=figsize_per_axes)

    return total_sweep


def measure_mixed_currents_odmr(current_vecs, seq_name, num_sweeps=10000,
                                settle_time=0.0, reset_after=True,
                                plot=False, ncols=3, figsize_per_axes=(4.0, 3.0),
                                skip_first_points=0, before_each=None):
    """
    Run one ODMR measurement at each of an explicit list of (I_X, I_Y, I_Z)
    current vectors, with SEVERAL coils on at once -- the data needed by
    `resolve_coil_calibration()` to fix which way each coil tilts off its
    nominal axis (see the module docstring, step 4).

    Choosing the points: measure at least 5 (5-6 is a good number). A
    full fit of M from mixed points alone needs at least 4 (each point
    fixes at most the 3 field components), and the 5th adds a check.
    What matters most is that all 8 dips are clearly RESOLVED, which
    depends on the DIRECTION of the total field relative to the NV axes:
    along symmetric directions (e.g. along an NV axis, or in a plane
    that contains NV axes symmetrically) several orientations become
    degenerate and dips coincide. WHICH lab directions those are depends
    on how the diamond sits in the coil frame, i.e. on
    `quantization_axes` -- so do not choose them by hand from rules of
    thumb. Use `plan_mixed_currents()`, which searches for the field
    directions with the best-resolved dips for the given
    `quantization_axes` (and coil strengths, current limits and target
    field) and returns the currents; the resolved region around the
    best direction is small, so near-misses can already merge dips.
    Good points also:
      - have every coil contribute a substantial part of the field, so
        its off-axis tilt moves the dips measurably (the planned
        directions do);
      - vary the sign pattern between points (e.g. (+,+,+), (+,-,+),
        (-,+,-)), so the current vectors span all coils and the offset.

    Assumes the ODMR PulseSequence named `seq_name` has ALREADY been
    generated, sampled, and loaded (as for `sweep_single_coil_odmr`).

    Parameters
    ----------
    current_vecs : sequence of (float, float, float)
        (I_X, I_Y, I_Z) in A for each point, in measurement order.
    seq_name : str
        Name of the already-generated/sampled/loaded ODMR PulseSequence.
    num_sweeps : int, default 10000
        Number of sweeps to average before stopping each measurement.
    settle_time : float, default 0.0
        Time (s) to wait after setting new coil currents, before starting
        the ODMR measurement.
    reset_after : bool, default True
        If True, set all three coils back to 0 A afterwards (or if an
        exception occurs mid-measurement).
    plot, ncols, figsize_per_axes, skip_first_points, before_each :
        As in `sweep_single_coil_odmr`.

    Returns
    -------
    mixed_sweep : list of dict
        One entry per point, each:
            {'axis': 'mixed', 'current_vec': (ix, iy, iz),
             'freq': ndarray, 'signal': ndarray, 'skipped_points': int}
        Same structure as `sweep_single_coil_odmr`'s output (minus the
        scalar `'current'`), so `pick_dips_interactive()` and
        `fit_sweep_dips()` work on it unchanged.
    """
    current_vecs = [tuple(float(c) for c in v) for v in current_vecs]
    for v in current_vecs:
        if len(v) != 3:
            raise ValueError(f'Each current vector must be (I_X, I_Y, I_Z); got {v}.')

    mixed_sweep = []
    try:
        print(f'=== Mixed-current ODMR: {len(current_vecs)} point(s) ===')
        for i, (ix, iy, iz) in enumerate(current_vecs):
            print(f'--- Point {i + 1}/{len(current_vecs)}: '
                  f'I = ({ix:.4f}, {iy:.4f}, {iz:.4f}) A ---')

            _set_coil_currents(ix, iy, iz)

            if settle_time > 0:
                print(f'  Waiting {settle_time:.1f} s to settle...')
                time.sleep(settle_time)
            tracking = before_each() if before_each is not None else None

            freq, signal = _run_odmr_sequence_and_collect(
                seq_name, num_sweeps, skip_first_points=skip_first_points)

            mixed_sweep.append({'axis': 'mixed', 'current_vec': (ix, iy, iz),
                                'freq': freq, 'signal': signal,
                                'skipped_points': int(skip_first_points)})
            if tracking:
                mixed_sweep[-1]['tracking'] = dict(tracking)

    finally:
        if reset_after:
            _set_coil_currents(0.0, 0.0, 0.0)
            print('All coils reset to 0 A.')

    if plot:
        plot_sweep_spectra(mixed_sweep, ncols=ncols, figsize_per_axes=figsize_per_axes)

    return mixed_sweep


# =============================================================================
# Saving / loading single ODMR points (spectrum + coil currents)
# =============================================================================

_ODMR_POINT_PATTERN = re.compile(r'^odmr_point_(\d+)\.npz$')


def _next_free_numbered_path(folder, prefix):
    """`<folder>/<prefix><n>.npz` with n the lowest number (from 0) not yet
    used by a `<prefix><n>.npz` file in `folder` (created if needed)."""
    folder = os.path.abspath(folder)
    os.makedirs(folder, exist_ok=True)
    pattern = re.compile(rf'^{re.escape(prefix)}(\d+)\.npz$')
    used = {int(m.group(1)) for m in map(pattern.match, os.listdir(folder)) if m}
    n = 0
    while n in used:
        n += 1
    return os.path.join(folder, f'{prefix}{n}.npz')


def save_odmr_point(folder='CoilODMRs', notes=''):
    """
    Save the ODMR result currently in `pulsed_master_logic.signal_data`
    together with the coil currents read back from `coil_control_logic`,
    as `<folder>/odmr_point_<n>.npz`, where n is the lowest number (from 0)
    not yet used in that folder. The folder (default: 'CoilODMRs' in the
    current working directory) is created if needed.

    The FULL, untrimmed spectrum is saved, so `skip_first_points` can be
    applied (and changed) at analysis time. The currents are the supplies'
    readback values, i.e. what was actually applied.

    Parameters
    ----------
    folder : str, default 'CoilODMRs'
        Target folder, relative to the current working directory or
        absolute.
    notes : str, optional
        Free text stored with the point.

    Returns
    -------
    path : str
        Absolute path of the written file.
    """
    data = pulsed_master_logic.signal_data
    freq = _to_plain_array(data[0])
    signal = _to_plain_array(data[1])
    if freq.size == 0 or not np.any(signal):
        raise RuntimeError('pulsed_master_logic.signal_data is empty or all zeros -- '
                           'nothing to save.')

    currents = coil_control_logic.get_all_currents()
    # Axis names are matched case-insensitively ('X' or 'x', depending on the hardware).
    by_axis = {str(k).upper(): v for k, v in currents.items()}
    missing = [a for a in ('X', 'Y', 'Z') if by_axis.get(a) is None]
    if missing:
        raise RuntimeError(f'Could not read back the current of coil(s) {missing} '
                           f'(got {currents}); not saving.')
    current_vec = np.array([float(by_axis[a]) for a in ('X', 'Y', 'Z')])

    path = _next_free_numbered_path(folder, 'odmr_point_')

    np.savez(path, freq=freq, signal=signal, current_vec=current_vec,
             notes=np.array(str(notes)), saved_at=np.array(time.strftime('%Y-%m-%d %H:%M:%S')))
    print(f'Saved {path}  (I = ({current_vec[0]:.4f}, {current_vec[1]:.4f}, '
          f'{current_vec[2]:.4f}) A, {freq.size} points)')
    return path


def load_odmr_point(path, skip_first_points=0):
    """
    Load a point saved by `save_odmr_point` as a sweep-point dict, usable
    directly with `pick_dips_interactive` / `fit_sweep_dips` (as one entry
    of a sweep list), or via its 'freq'/'signal' with
    `nv_field_fitting.fit_odmr_dips_interactive`.

    Parameters
    ----------
    path : str
    skip_first_points : int, default 0
        Drop the first n spectrum points on loading (the file keeps all).

    Returns
    -------
    point : dict
        {'axis': 'X'/'Y'/'Z' if only that coil carries current, else
         'mixed'; 'current' (for single-coil points); 'current_vec';
         'freq'; 'signal'; 'skipped_points'; 'notes'; 'saved_at'; 'path'}
    """
    with np.load(path, allow_pickle=False) as d:
        freq, signal = nvfit.skip_first_odmr_points(d['freq'], d['signal'], skip_first_points)
        current_vec = tuple(float(c) for c in d['current_vec'])
        notes = str(d['notes']) if 'notes' in d else ''
        saved_at = str(d['saved_at']) if 'saved_at' in d else ''
    point = {'current_vec': current_vec, 'freq': freq, 'signal': signal,
             'skipped_points': int(skip_first_points), 'notes': notes, 'saved_at': saved_at,
             'path': os.path.abspath(path)}
    on = [a for a, c in zip(('X', 'Y', 'Z'), current_vec) if c != 0]
    if len(on) == 1:
        point['axis'] = on[0]
        point['current'] = current_vec[('X', 'Y', 'Z').index(on[0])]
    else:
        point['axis'] = 'mixed'
    return point


def load_odmr_folder(folder='CoilODMRs', skip_first_points=0):
    """
    Load every `odmr_point_<n>.npz` in `folder` (see `save_odmr_point`),
    in order of n, as a list of point dicts (see `load_odmr_point`).
    """
    folder = os.path.abspath(folder)
    found = sorted((int(m.group(1)), name) for name in os.listdir(folder)
                   for m in [_ODMR_POINT_PATTERN.match(name)] if m)
    return [load_odmr_point(os.path.join(folder, name), skip_first_points=skip_first_points)
            for _, name in found]


# =============================================================================
# Linearity scans (is B linear in the coil currents?)
# =============================================================================

def _read_coil_values(getter):
    """Read {'x'/'X': value} from a coil_control_logic getter as an (X, Y, Z)
    array, case-insensitively; missing/None values become NaN."""
    try:
        values = {str(k).upper(): v for k, v in getter().items()}
    except Exception:
        return np.full(3, np.nan)
    return np.array([np.nan if values.get(a) is None else float(values[a])
                     for a in ('X', 'Y', 'Z')])


def _half_depth_hwhm(freq, signal, center, search=50.0e6):
    """
    Model-free half width at half depth (Hz) of the dip at `center`: the
    local baseline is the highest signal within +/-`search`, the depth is
    measured at the dip, and the half-depth crossings are found by walking
    outwards (linear interpolation). None if a side never crosses (the
    dip merges into a neighbour or the window edge).
    """
    order = np.argsort(freq)
    f, s = freq[order], signal[order]
    near = np.abs(f - center) <= search
    if np.count_nonzero(near) < 5:
        return None
    i0 = int(np.argmin(np.abs(f - center)))
    lo_i, hi_i = np.flatnonzero(near)[[0, -1]]
    i_min = lo_i + int(np.argmin(s[lo_i:hi_i + 1][max(0, i0 - lo_i - 3):i0 - lo_i + 4]))
    i_min += max(0, i0 - lo_i - 3)
    baseline = float(np.max(s[near]))
    half = baseline - 0.5 * (baseline - s[i_min])
    edges = []
    for step in (-1, 1):
        i = i_min
        while lo_i <= i + step <= hi_i and s[i + step] < half:
            i += step
        j = i + step
        if not lo_i <= j <= hi_i:
            return None
        edges.append(f[i] + (half - s[i]) * (f[j] - f[i]) / (s[j] - s[i]))
    return 0.5 * abs(edges[1] - edges[0])


def _reference_linewidths(reference_folder):
    """Half widths at half depth (Hz) of all dips in a folder of fitted
    points, measured directly on the raw spectra (no Lorentzian fit, whose
    widths are unreliable for overlapping broad dips)."""
    hwhm = []
    for name in sorted(os.listdir(reference_folder)):
        if not name.endswith('.npz'):
            continue
        with np.load(os.path.join(reference_folder, name), allow_pickle=False) as d:
            if 'centers' not in d.files:
                continue
            skip = int(d['skipped_points']) if 'skipped_points' in d.files else 0
            freq, signal = nvfit.skip_first_odmr_points(d['freq'], d['signal'], skip)
            centers = np.asarray(d['centers'], dtype=float)
        for c in centers:
            w = _half_depth_hwhm(freq, signal, c)
            if w is not None:
                hwhm.append(w)
    return np.array(hwhm)


def plan_linearity_scans(freq_start, freq_stop, n_freq_points, quantization_axes,
                         coil_strengths, skip_first_points=0, max_current=3.0,
                         nominal_axes=None, D=2.87e9, E=0.0, fill_fraction=0.85,
                         strength_margin=1.15, tilt_margin_deg=10.0, n_steps=13,
                         return_fractions=(2.0 / 3.0, 0.0, -2.0 / 3.0),
                         reference_folder='FittedCoilODMRs', mixed_triplet=None,
                         mixed_scales=(0.25, 0.5, 0.75, 1.0), mixed_return_scale=0.5,
                         reference_mw=0.2, new_mw=0.1, verbose=True):
    """
    Plan the measurements that test whether the field is linear in the coil
    currents: one current scan per coil (other coils at 0 A) and one scan
    of a mixed current triplet at several overall scales, each followed by
    a few points measured on the way back (hysteresis check). Pure
    planning -- no hardware; run the plan with `run_linearity_scans`.

    Current amplitudes are chosen from the ODMR sweep settings:
      * usable window = linspace(freq_start, freq_stop, n_freq_points)
        minus its first `skip_first_points` points, minus a margin of 2
        dip half-widths on each side;
      * each coil is scanned symmetrically up to the largest current
        (<= max_current) for which its outermost dip stays within
        `fill_fraction` of the usable half-window around D, assuming the
        coil may be `strength_margin` x stronger than `coil_strengths`
        and tilted by up to `tilt_margin_deg` from its nominal axis;
      * the mixed triplet (default: the reference point whose fitted dips
        were best resolved) is scaled so that its lowest and highest dips
        -- MEASURED in the reference spectrum, scaled about D -- stay 2
        half-widths inside the window (no fill fraction or strength
        margin needed, since its spectrum is known), capped at scale 1
        and by max_current.

    If `reference_folder` (fitted points, e.g. from `review_odmr_folder`)
    is given, its raw spectra are re-fitted to measure the dip half-widths
    at the reference microwave amplitude `reference_mw`. At `new_mw` the
    lines narrow by up to new_mw/reference_mw (if power broadening
    dominates) or not at all (field inhomogeneity); the margin uses the
    reference widths, and the frequency step is checked against the
    narrower case (warning if fewer than ~2 points per half-width).

    Parameters
    ----------
    freq_start, freq_stop, n_freq_points : float, float, int
        The ODMR sequence's sweep (Hz), as programmed.
    quantization_axes : as required by nv_field_fitting.
    coil_strengths : dict
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A}, rough field per amp.
    skip_first_points : int, default 0
        Points that will be dropped from the start of every spectrum.
    max_current : float or dict, default 3.0
        Current limit(s), A.
    nominal_axes : dict, optional
        Positive-current field direction per coil; defaults to +x/+y/+z.
    D, E : float
    fill_fraction : float, default 0.8
    strength_margin : float, default 1.3
    tilt_margin_deg : float, default 10
    n_steps : int, default 13
        Points per single-coil scan, from -I_max to +I_max (odd -> 0 A
        included, giving the offset).
    return_fractions : sequence of float
        Points re-measured after the up-scan, as fractions of I_max.
    fill_fraction, strength_margin, tilt_margin_deg :
        Safety factors for the single-coil scans (default 0.85, 1.15,
        10 deg).
    reference_folder : str or None, default 'FittedCoilODMRs'
    mixed_triplet : (float, float, float), optional
        Current triplet for the mixed scan at scale 1; default from the
        reference folder (or none if there is no reference data).
    mixed_scales : sequence of float
        Scales of the mixed scan, as fractions of the largest allowed one.
    mixed_return_scale : float or None
        Scale re-measured after the mixed up-scan.
    reference_mw, new_mw : float
        Microwave amplitudes of the reference data and of the new scans.
    verbose : bool, default True

    Returns
    -------
    plan : list of dict
        In measurement order: {'scan': 'X'|'Y'|'Z'|'mixed', 'index': int,
        'direction': 'up'|'down', 'scale': float, 'set_current_vec':
        (I_X, I_Y, I_Z)}.
    info : dict
        'window' (lo, hi) Hz, 'usable_half' Hz, 'reference_hwhm' Hz
        (median, or None), 'freq_step' Hz, 'max_scan_current' {axis: A},
        'mixed_triplet', 'mixed_reference_outer_offset' Hz, 'warnings'.
    """
    required = ('X', 'Y', 'Z')
    axes_n = [nvfit._normalize(a) for a in quantization_axes]
    nominal_axes = _nominal_axes_dict(nominal_axes)
    if isinstance(max_current, dict):
        limits = {a: float(max_current[a]) for a in required}
    else:
        limits = {a: float(max_current) for a in required}
    warnings_list = []

    # === Sweep window and linewidth ===
    lo, hi, kept_axis = _usable_window(freq_start, freq_stop, n_freq_points, skip_first_points)
    if not lo < D < hi:
        raise ValueError(f'D = {D / 1e9:.4f} GHz is outside the sweep window '
                         f'{lo / 1e9:.4f}-{hi / 1e9:.4f} GHz.')
    freq_step = abs(float(freq_stop) - float(freq_start)) / (int(n_freq_points) - 1)

    ref_hwhm = None
    if reference_folder is not None and os.path.isdir(reference_folder):
        widths = _reference_linewidths(reference_folder)
        if widths.size:
            ref_hwhm = float(np.median(widths))
    hwhm_margin = ref_hwhm if ref_hwhm is not None else 5.0e6
    narrowest_expected = hwhm_margin * min(1.0, new_mw / reference_mw)
    if freq_step > narrowest_expected / 2.0:
        warnings_list.append(
            f'frequency step {freq_step / 1e6:.2f} MHz may under-sample the dips: with '
            f'lines as narrow as ~{narrowest_expected / 1e6:.1f} MHz HWHM (if power '
            f'broadening dominates at {new_mw:g}) use <= {narrowest_expected / 2e6:.2f} MHz, '
            f'i.e. >= {int(np.ceil(abs(freq_stop - freq_start) / (narrowest_expected / 2))) + 1}'
            f' points.')
    usable_half = min(D - lo, hi - D) - 2.0 * hwhm_margin
    if usable_half <= 0:
        raise ValueError('The sweep window is too narrow for the dip linewidth margin.')
    target = fill_fraction * usable_half

    # === Single-coil scan amplitudes (worst case over strength and tilt) ===
    cone = [np.array([0.0, 0.0, 1.0])]
    ang = np.radians(tilt_margin_deg)
    for phi in np.linspace(0, 2 * np.pi, 12, endpoint=False):
        cone.append(np.array([np.sin(ang) * np.cos(phi), np.sin(ang) * np.sin(phi), np.cos(ang)]))

    def _worst_outer(axis_vec, b):
        z = nvfit._normalize(axis_vec)
        e1 = np.cross(z, [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(z, e1)
        dirs = np.array([c[0] * e1 + c[1] * e2 + c[2] * z for c in cone])
        f = nvfit._batch_transitions(axes_n, b * dirs, D=D, E=E)
        return float(np.max(np.abs(f - D)))

    max_scan = {}
    for a in required:
        s = strength_margin * abs(coil_strengths[a])
        lo_I, hi_I = 0.0, limits[a]
        if _worst_outer(nominal_axes[a], s * hi_I) <= target:
            max_scan[a] = hi_I
        else:
            for _ in range(40):
                mid = 0.5 * (lo_I + hi_I)
                if _worst_outer(nominal_axes[a], s * mid) <= target:
                    lo_I = mid
                else:
                    hi_I = mid
            max_scan[a] = lo_I
        max_scan[a] = float(np.floor(max_scan[a] * 100) / 100)

    plan = []
    for j, a in enumerate(required):
        currents = np.round(np.linspace(-max_scan[a], max_scan[a], int(n_steps)), 3)
        for i, c in enumerate(currents):
            vec = [0.0, 0.0, 0.0]
            vec[j] = float(c)
            plan.append({'scan': a, 'index': i, 'direction': 'up',
                         'scale': float(c) / max_scan[a] if max_scan[a] else 0.0,
                         'set_current_vec': tuple(vec)})
        for i, fr in enumerate(return_fractions):
            vec = [0.0, 0.0, 0.0]
            vec[j] = float(np.round(fr * max_scan[a], 3))
            plan.append({'scan': a, 'index': len(currents) + i, 'direction': 'down',
                         'scale': float(fr), 'set_current_vec': tuple(vec)})

    # === Mixed triplet, scaled from its reference spectrum ===
    ref_outer = ref_low = ref_high = None
    if mixed_triplet is None and reference_folder is not None and os.path.isdir(reference_folder):
        # The reference point whose fitted dips were best resolved, and how far
        # its outermost dip actually was from D.
        best = None
        try:
            reference = nvfit.load_verified_measurements(reference_folder)
        except ValueError:
            reference = []
        for coil_values, peaks in reference:
            c = np.sort(peaks['centers'])
            if len(c) < 2:
                continue
            gap = float(np.min(np.diff(c)))
            if best is None or gap > best[0]:
                best = (gap, coil_values, float(np.max(np.abs(c - D))),
                        float(D - c[0]), float(c[-1] - D))
        if best is not None:
            mixed_triplet, ref_outer = best[1], best[2]
            ref_low, ref_high = best[3], best[4]
    if mixed_triplet is not None:
        mixed_triplet = np.asarray(mixed_triplet, dtype=float)
        if ref_outer is None:  # no measured spectrum: predict it, with the margins
            B_nominal = np.column_stack([nominal_axes[a] * coil_strengths[a]
                                         for a in required]) @ mixed_triplet
            ref_outer = _worst_outer(B_nominal, strength_margin * np.linalg.norm(B_nominal))
        if ref_low is not None:  # measured: keep both outer dips 2 HWHM inside the window
            scale_max = min(1.0, (D - lo - 2.0 * hwhm_margin) / ref_low,
                            (hi - D - 2.0 * hwhm_margin) / ref_high)
        else:
            scale_max = target / ref_outer
        scale_max = min(scale_max, min(limits[a] / abs(mixed_triplet[j])
                                       for j, a in enumerate(required) if mixed_triplet[j] != 0))
        scales = [float(s) * scale_max for s in mixed_scales]
        if mixed_return_scale is not None:
            scales_down = [float(mixed_return_scale) * scale_max]
        else:
            scales_down = []
        for i, s in enumerate(scales + scales_down):
            plan.append({'scan': 'mixed', 'index': i,
                         'direction': 'up' if i < len(scales) else 'down',
                         'scale': s, 'set_current_vec': tuple(np.round(s * mixed_triplet, 3))})

    info = {'window': (lo, hi), 'usable_half': usable_half, 'reference_hwhm': ref_hwhm,
            'freq_step': freq_step, 'max_scan_current': max_scan,
            'mixed_triplet': None if mixed_triplet is None else tuple(mixed_triplet),
            'mixed_reference_outer_offset': ref_outer, 'warnings': warnings_list}

    if verbose:
        print(f'Sweep {min(freq_start, freq_stop) / 1e9:.4f}-{max(freq_start, freq_stop) / 1e9:.4f} '
              f'GHz, {n_freq_points} points ({freq_step / 1e6:.2f} MHz step), first '
              f'{skip_first_points} skipped -> window {lo / 1e9:.4f}-{hi / 1e9:.4f} GHz.')
        if ref_hwhm is not None:
            print(f'Reference dips ({reference_mw:g}): median HWHM {ref_hwhm / 1e6:.1f} MHz; '
                  f'expected at {new_mw:g}: {narrowest_expected / 1e6:.1f}-{ref_hwhm / 1e6:.1f} MHz.')
        print(f'Outermost dips kept within {target / 1e6:.0f} MHz of D '
              f'(assuming coils up to {strength_margin:g}x stronger, tilted up to '
              f'{tilt_margin_deg:g} deg).')
        for a in required:
            print(f'  {a} scan: -{max_scan[a]:.2f} .. +{max_scan[a]:.2f} A in {n_steps} steps, then '
                  f'{[round(f * max_scan[a], 2) for f in return_fractions]} A'
                  + ('  (limited by max_current)' if max_scan[a] >= limits[a] - 0.005 else ''))
        if mixed_triplet is not None:
            print(f'  mixed scan: {np.round(mixed_triplet, 3)} A x scales '
                  f'{[round(s, 3) for s in scales]}'
                  + (f', then x {round(scales_down[0], 3)}' if scales_down else ''))
        print(f'{len(plan)} measurements in total.')
        for w in warnings_list:
            print(f'WARNING: {w}')
    return plan, info


def run_linearity_scans(seq_name, freq_start, freq_stop, n_freq_points, quantization_axes,
                        coil_strengths, skip_first_points=0, num_sweeps=10000, settle_time=5.0,
                        folder='CoilLinearity', plan=None, dry_run=False, before_each=None,
                        **plan_kwargs):
    """
    Plan (`plan_linearity_scans`) and measure the coil-linearity scans,
    saving every point as `<folder>/odmr_point_<n>.npz` immediately (an
    interrupted run keeps everything measured so far; coils are reset to
    0 A at the end or on error).

    Each file holds the FULL raw spectrum ('freq', 'signal'; skipping is
    applied only at analysis), 'set_current_vec', the supplies' read-back
    currents and voltages just before and just after the ODMR run
    ('current_before', 'current_after', 'voltage_before', 'voltage_after';
    NaN where unavailable), 'current_vec' (mean read-back current, or the
    set value if no read-back), and 'scan', 'index', 'direction', 'scale',
    'saved_at', plus the sweep settings. The format is that of
    `save_odmr_point`, so `load_odmr_point` and `review_odmr_folder(folder,
    ...)` work on these files too.

    Parameters
    ----------
    seq_name : str
        Name of the generated/sampled/loaded ODMR sequence matching
        freq_start / freq_stop / n_freq_points.
    freq_start, freq_stop, n_freq_points, quantization_axes, coil_strengths,
    skip_first_points :
        As in `plan_linearity_scans`.
    num_sweeps : int, default 10000
    settle_time : float, default 5.0
        Wait after each current change, s.
    folder : str, default 'CoilLinearity'
    plan : list of dict, optional
        A plan from `plan_linearity_scans` (skips planning).
    dry_run : bool, default False
        Only print the plan.
    before_each : callable, optional
        Called with no arguments after each point's currents are set and
        settled, right before its ODMR run -- e.g. a `tracking.ZTracker`
        to re-optimize the focus. If it returns a dict, each entry is
        saved with the point as 'tracking_<key>' (e.g. 'tracking_z').
    **plan_kwargs :
        Passed to `plan_linearity_scans` (max_current, D, reference_folder,
        reference_mw, new_mw, n_steps, mixed_triplet, ...).

    Returns
    -------
    paths : list of str
        Saved files, in measurement order (empty for a dry run).
    plan : list of dict
    """
    if plan is None:
        plan, _ = plan_linearity_scans(freq_start, freq_stop, n_freq_points, quantization_axes,
                                       coil_strengths, skip_first_points=skip_first_points,
                                       **plan_kwargs)
    if dry_run:
        return [], plan

    expected = np.linspace(float(freq_start), float(freq_stop), int(n_freq_points))
    paths = []
    t_start = time.time()
    try:
        for n, step in enumerate(plan):
            I_set = np.array(step['set_current_vec'], dtype=float)
            _set_coil_currents(*I_set)
            if settle_time > 0:
                time.sleep(settle_time)
            tracking = before_each() if before_each is not None else None
            tracking = {f'tracking_{k}': np.array(v) for k, v in (tracking or {}).items()}
            cur_before = _read_coil_values(coil_control_logic.get_all_currents)
            volt_before = _read_coil_values(coil_control_logic.get_all_voltages)

            freq, signal = _run_odmr_sequence_and_collect(seq_name, num_sweeps,
                                                          skip_first_points=0)

            cur_after = _read_coil_values(coil_control_logic.get_all_currents)
            volt_after = _read_coil_values(coil_control_logic.get_all_voltages)
            readback = np.nanmean(np.vstack([cur_before, cur_after]), axis=0)
            current_vec = np.where(np.isfinite(readback), readback, I_set)

            if n == 0 and (len(freq) != len(expected) or not np.allclose(
                    freq, expected, rtol=0, atol=0.5 * abs(expected[1] - expected[0]))):
                print(f'WARNING: measured frequency axis ({len(freq)} points, '
                      f'{freq[0] / 1e9:.4f}-{freq[-1] / 1e9:.4f} GHz) does not match '
                      f'freq_start/freq_stop/n_freq_points -- was the new sequence loaded?')

            path = _next_free_numbered_path(folder, 'odmr_point_')
            np.savez(path, freq=freq, signal=signal, current_vec=current_vec,
                     set_current_vec=I_set, current_before=cur_before, current_after=cur_after,
                     voltage_before=volt_before, voltage_after=volt_after,
                     scan=np.array(step['scan']), index=np.array(int(step['index'])),
                     direction=np.array(step['direction']), scale=np.array(float(step['scale'])),
                     freq_start=np.array(float(freq_start)), freq_stop=np.array(float(freq_stop)),
                     n_freq_points=np.array(int(n_freq_points)),
                     skip_first_points=np.array(int(skip_first_points)),
                     notes=np.array(f"linearity scan {step['scan']} #{step['index']} "
                                    f"({step['direction']})"),
                     saved_at=np.array(time.strftime('%Y-%m-%d %H:%M:%S')), **tracking)
            paths.append(path)
            elapsed = time.time() - t_start
            remaining = elapsed / (n + 1) * (len(plan) - n - 1)
            print(f"[{n + 1}/{len(plan)}] {step['scan']:5s} {step['direction']:4s} "
                  f"set {np.round(I_set, 3)} A, read {np.round(cur_before, 3)} A, "
                  f"V {np.round(volt_before, 2)} -> {os.path.basename(path)} "
                  f"(~{remaining / 60:.0f} min left)")
    finally:
        _set_coil_currents(0.0, 0.0, 0.0)
        print('All coils reset to 0 A.')
    return paths, plan


def review_odmr_folder(folder='CoilODMRs', fitted_folder='FittedCoilODMRs',
                       skip_first_points=0, sigma_guess=2.0e6, default_window=10.0e6,
                       freq_units='MHz', figsize=(11, 5.5), sigma_bounds=(0.1e6, 40.0e6)):
    """
    Go through every saved ODMR point in `folder` (see `save_odmr_point`),
    fit its dips interactively, and decide for each whether to keep it.

    For each point, in order of its number:
      1. the interactive picker opens
         (`nv_field_fitting.fit_odmr_dips_interactive`; drag/right-click to
         mark dips, 'u' to undo, Enter in the prompt to finish), then the
         resulting fit is shown;
      2. you answer the prompt:
           y -- include: the point (raw spectrum, currents, AND the fitted
                dips) is MOVED to `fitted_folder` as fitted_odmr_<m>.npz
                (m = lowest free number there), in the format read by
                `nv_field_fitting.load_verified_measurements` /
                `fit_coil_matrix_from_measurements(fitted_folder, ...)`;
           n -- reject: the point stays in `folder` untouched;
           r -- redo the picking for this point;
           q -- stop here (remaining points stay in `folder`).

    The ipympl backend is warmed up first, so the first figure renders.
    Requires `%matplotlib widget` (see
    `nv_field_fitting.pick_dips_interactive_spectrum`). No hardware access.

    Parameters
    ----------
    folder : str, default 'CoilODMRs'
    fitted_folder : str, default 'FittedCoilODMRs'
        Created if needed.
    skip_first_points : int, default 0
        Leading spectrum points to ignore while fitting (the moved file
        still contains the full raw spectrum, plus this number).
    sigma_guess, default_window, freq_units, figsize, sigma_bounds :
        Passed to `nv_field_fitting.fit_odmr_dips_interactive`
        (sigma_bounds: allowed dip HWHM range, Hz).

    Returns
    -------
    summary : dict
        'included' : list of (source file name, new path)
        'rejected' : list of source file names (left in `folder`)
        'remaining': list of source file names not reviewed (after 'q')
    """
    folder = os.path.abspath(folder)
    names = sorted((int(m.group(1)), name) for name in os.listdir(folder)
                   for m in [_ODMR_POINT_PATTERN.match(name)] if m)
    names = [name for _, name in names]
    summary = {'included': [], 'rejected': [], 'remaining': []}
    if not names:
        print(f'No odmr_point_<n>.npz files in {folder}.')
        return summary

    nvfit._warm_up_ipympl_backend()
    print(f'Reviewing {len(names)} ODMR point(s) from {folder}.')

    for i, name in enumerate(names):
        src = os.path.join(folder, name)
        point = load_odmr_point(src, skip_first_points=skip_first_points)
        ix, iy, iz = point['current_vec']
        label = f'{name} ({i + 1}/{len(names)}): I = ({ix:.4f}, {iy:.4f}, {iz:.4f}) A'
        print(f'\n=== {label} ===' + (f"  notes: {point['notes']}" if point['notes'] else ''))

        while True:
            fit = nvfit.fit_odmr_dips_interactive(
                point['freq'], point['signal'], sigma_guess=sigma_guess,
                default_window=default_window, freq_units=freq_units, figsize=figsize,
                title=label, sigma_bounds=sigma_bounds)
            if fit['n_dips'] == 0 or not fit['success']:
                print('  (no usable fit -- answer r to redo, or n to reject)')
            answer = ''
            while answer not in ('y', 'n', 'r', 'q'):
                answer = input('Include this fit? [y]es / [n]o / [r]edo / [q]uit: ').strip().lower()[:1]
            if answer != 'r':
                break

        if answer == 'q':
            summary['remaining'] = names[i:]
            print(f'Stopped; {len(names) - i} point(s) left in {folder}.')
            break
        if answer == 'n':
            summary['rejected'].append(name)
            print(f'  rejected; {name} stays in {folder}.')
            continue

        # y: write raw data + fit to the fitted folder, then remove the original
        with np.load(src, allow_pickle=False) as d:
            raw = {k: d[k] for k in d.files}
        dst = _next_free_numbered_path(fitted_folder, 'fitted_odmr_')
        np.savez(dst, **raw,
                 centers=np.asarray(fit['centers'], dtype=float),
                 center_errs=np.asarray(fit['center_errs'], dtype=float),
                 sigmas=np.asarray(fit['sigmas'], dtype=float),
                 amplitudes=np.asarray(fit['amplitudes'], dtype=float),
                 offset=np.array(float(fit['offset'])),
                 skipped_points=np.array(int(skip_first_points)),
                 source_file=np.array(name),
                 fitted_at=np.array(time.strftime('%Y-%m-%d %H:%M:%S')))
        os.remove(src)
        summary['included'].append((name, dst))
        print(f"  included ({fit['n_dips']} dips): moved to {dst}")

    print(f"\nDone: {len(summary['included'])} included, {len(summary['rejected'])} rejected, "
          f"{len(summary['remaining'])} not reviewed.")
    return summary


def trim_sweep_points(total_sweep, skip_first_points):
    """
    Drop the first n points of every spectrum in an ALREADY-measured
    sweep (from `sweep_single_coil_odmr` / `measure_mixed_currents_odmr`),
    for data taken without `skip_first_points` or to try a different n.

    Returns a NEW list; the input is not modified. Each point is a copy
    with 'freq'/'signal' trimmed and 'skipped_points' increased by n. Any
    'dip_fit' / 'field_fit' is dropped, since it was fitted on the
    untrimmed spectrum -- re-run the dip picking/fitting on the result.

    Parameters
    ----------
    total_sweep : list of dict
    skip_first_points : int
        Number of leading points to drop from each spectrum (in addition
        to any already dropped at acquisition).

    Returns
    -------
    trimmed_sweep : list of dict
    """
    trimmed = []
    for p in total_sweep:
        q = {k: v for k, v in p.items() if k not in ('dip_fit', 'field_fit')}
        q['freq'], q['signal'] = nvfit.skip_first_odmr_points(p['freq'], p['signal'],
                                                              skip_first_points)
        q['skipped_points'] = int(p.get('skipped_points', 0)) + int(skip_first_points)
        trimmed.append(q)
    return trimmed


def measurements_from_sweeps(*sweeps):
    """
    Convert one or more sweeps (from `sweep_single_coil_odmr` /
    `measure_mixed_currents_odmr`) whose dips were picked and fitted
    (`pick_dips_interactive` + `fit_sweep_dips`) into the
    [(coil_values, peaks), ...] list taken by
    `nv_field_fitting.fit_coil_matrix_from_measurements`. Points without
    a 'dip_fit' are skipped.

    Example::

        meas = measurements_from_sweeps(sweep_X, sweep_Y, sweep_Z, mixed_sweep)
        M, B_offset, info = nvfit.fit_coil_matrix_from_measurements(
            meas, quantization_axes, freq_range=(f_lo, f_hi))
    """
    measurements = []
    for sweep in sweeps:
        for p in sweep:
            if p.get('dip_fit') is not None:
                measurements.append((tuple(p['current_vec']), p['dip_fit']))
    return measurements


def plot_sweep_spectra(total_sweep, ncols=3, figsize_per_axes=(4.0, 3.0)):
    """
    Plot raw ODMR spectra for every point in `total_sweep` in a single
    grid figure, for a quick static overview (e.g. before deciding
    whether/how to proceed to interactive dip-picking).

    Parameters
    ----------
    total_sweep : list of dict
        As returned by `sweep_single_coil_odmr`.
    ncols : int, default 3
        Number of subplot columns.
    figsize_per_axes : (float, float)
        Figure size *per subplot*, inches; total figure scales with grid size.

    Returns
    -------
    (fig, axes) : matplotlib Figure and array of Axes.
    """
    n = len(total_sweep)
    nrows = int(np.ceil(n / ncols))

    with plt.ioff():
        fig, axes = plt.subplots(nrows, ncols,
                                  figsize=(figsize_per_axes[0] * ncols,
                                           figsize_per_axes[1] * nrows),
                                  squeeze=False, num=nvfit._next_fig_num())
        for i, point in enumerate(total_sweep):
            ax = axes[i // ncols][i % ncols]
            ax.plot(point['freq'] / 1e6, point['signal'], '.-', ms=3)
            ax.set_title(_point_label(point), fontsize=9)
            ax.set_xlabel('Frequency (MHz)')
            ax.set_ylabel('Signal')

        for j in range(n, nrows * ncols):
            axes[j // ncols][j % ncols].axis('off')

        fig.tight_layout()

    nvfit._display_figure(fig)
    return fig, axes


# =============================================================================
# Step 2a: interactive dip picking (per-point loop)
# =============================================================================

def pick_dips_interactive(total_sweep, freq_units='MHz', figsize=(11, 5.5),
                           default_window=10.0e6):
    """
    Interactively pick dip-center guesses (and acceptable location
    ranges) for EVERY point in `total_sweep`, by clicking through each
    spectrum in turn.

    This is a thin loop around
    `nv_field_fitting.pick_dips_interactive_spectrum()` (see its
    docstring for the exact picking controls/mechanics) -- using the
    SAME underlying picker (and the same figure-display/numbering
    helpers) as any standalone interactive ODMR fitting done directly
    through nv_field_fitting.py, for consistency.

    REQUIRES the ipympl interactive backend to be active in the
    notebook -- run

        %matplotlib widget

    in a cell BEFORE calling this function (and `pip install ipympl` if
    not already installed, plus a full Jupyter server restart if ipympl
    was just installed/upgraded). With the default inline/static
    backend, clicks will not be registered.

    Parameters
    ----------
    total_sweep : list of dict
        As returned by `sweep_single_coil_odmr` or
        `measure_mixed_currents_odmr` (needs 'freq', 'signal'; the coil
        current(s) are shown in the plot title, if present).
    freq_units, figsize, default_window :
        Passed through to
        `nv_field_fitting.pick_dips_interactive_spectrum()` for every
        point.

    Returns
    -------
    dip_guesses : list of list of float
        One list per point in `total_sweep` (same order/length), each
        the recorded dip-center frequencies for that spectrum, Hz. Pass
        directly as the `dip_guesses` argument to `fit_sweep_dips` /
        `interactive_fit_coil_axis`.
    dip_windows : list of list of (float, float)
        One list per point, each a list of (lo, hi) acceptable-range
        bounds (Hz), one per entry in the corresponding `dip_guesses`
        list. Pass as the `dip_windows` argument to `fit_sweep_dips` /
        `interactive_fit_coil_axis`.
    """
    scale = {'Hz': 1.0, 'kHz': 1e3, 'MHz': 1e6, 'GHz': 1e9}[freq_units]

    all_dip_guesses = []
    all_dip_windows = []

    for point_index, point in enumerate(total_sweep):
        label = _point_label(point)
        title = (f"Point {point_index + 1}/{len(total_sweep)}"
                 + (f" ({label})" if label else ''))
        prompt = (f'Point {point_index + 1}/{len(total_sweep)}: drag ranges and/or '
                  f'right-click to mark dips (see pick_dips_interactive_spectrum() '
                  f'docstring for exact behavior), "u" to undo. '
                  f'Press Enter here when done with this spectrum...')

        centers, windows = nvfit.pick_dips_interactive_spectrum(
            point['freq'], point['signal'], freq_units=freq_units, figsize=figsize,
            default_window=default_window, title=title, prompt=prompt)

        all_dip_guesses.append(centers)
        all_dip_windows.append(windows)

        print(f'  -> recorded {len(centers)} dip(s): '
              f'{[round(c / scale, 4) for c in centers]} {freq_units}')

    return all_dip_guesses, all_dip_windows


# =============================================================================
# Step 2b: guided dip fitting
# =============================================================================

def fit_sweep_dips(total_sweep, dip_guesses, amplitude_guesses=None,
                    dip_windows=None, sigma_guess=2.0e6, plot=True, ncols=3,
                    figsize_per_axes=(4.0, 3.0)):
    """
    Fit Lorentzian dips to every spectrum in `total_sweep`, seeded by
    user-supplied per-point guesses (see
    `nv_field_fitting.fit_lorentzian_dips_guided`), typically produced by
    `pick_dips_interactive()`.

    Parameters
    ----------
    total_sweep : list of dict
        As returned by `sweep_single_coil_odmr`. Modified in place: each
        entry gains a `'dip_fit'` key holding the fit-result dict from
        `fit_lorentzian_dips_guided`.
    dip_guesses : list of sequence of float
        One entry per point in `total_sweep`, each a list of guessed dip
        center frequencies (Hz) for that spectrum. Must be the same
        length as `total_sweep`. An empty list for a given point means
        "skip fitting this point" (its `'dip_fit'` will have `n_dips=0`
        and will contribute zero matches to the step-3 joint fit, without
        otherwise breaking it).
    amplitude_guesses : list of (sequence of float or None), optional
        One entry per point, each either None (auto-estimate depths) or
        an explicit list of guessed depths matching that point's
        `dip_guesses`.
    dip_windows : list of (sequence of (float, float) or None), optional
        One entry per point, each a list of explicit (lo, hi) acceptable
        location bounds (Hz), one per entry in that point's
        `dip_guesses` -- as produced by `pick_dips_interactive()`. If
        None (the whole argument, or an individual point's entry, or an
        individual dip's entry within that), falls back to the default
        symmetric `sigma_guess`-scaled window from
        `fit_lorentzian_dips_guided`.
    sigma_guess : float, default 2e6
        Initial HWHM guess, Hz, applied to all dips at all points (see
        `nv_field_fitting.fit_lorentzian_dips_guided`).
    plot : bool, default True
        If True, plot each spectrum with its fitted Lorentzians overlaid,
        so fit quality can be checked by eye before trusting the dip list.
    ncols, figsize_per_axes :
        Grid-plot layout parameters (as in `plot_sweep_spectra`).

    Returns
    -------
    total_sweep : list of dict
        Same object as passed in (modified in place), returned for
        convenience/chaining.
    """
    if len(dip_guesses) != len(total_sweep):
        raise ValueError(f'dip_guesses has {len(dip_guesses)} entries but '
                          f'total_sweep has {len(total_sweep)}.')
    if amplitude_guesses is None:
        amplitude_guesses = [None] * len(total_sweep)
    if dip_windows is None:
        dip_windows = [None] * len(total_sweep)

    for point, centers, amps, windows in zip(total_sweep, dip_guesses,
                                             amplitude_guesses, dip_windows):
        fit = nvfit.fit_lorentzian_dips_guided(
            point['freq'], point['signal'], centers,
            amplitude_guesses=amps, sigma_guess=sigma_guess, center_bounds=windows)
        point['dip_fit'] = fit
        if not fit['success']:
            print(f"WARNING: dip fit failed for {_point_label(point)}")

    if plot:
        _plot_sweep_with_dip_fits(total_sweep, ncols=ncols, figsize_per_axes=figsize_per_axes)

    return total_sweep


def _plot_sweep_with_dip_fits(total_sweep, ncols=3, figsize_per_axes=(4.0, 3.0),
                               predicted_lines=None):
    """
    Internal helper: plot raw spectrum + fitted Lorentzian dip model (and
    optionally predicted transition frequencies from a field fit) for
    every point in `total_sweep`, in a single grid figure.

    Parameters
    ----------
    predicted_lines : list of (sequence of float or None), optional
        One entry per point; if given, vertical dashed lines are drawn at
        these frequencies (Hz) -- used by `interactive_fit_coil_axis` to
        overlay the joint-fit's predicted dip positions for visual
        cross-check against the fitted dip centers.
    """
    n = len(total_sweep)
    nrows = int(np.ceil(n / ncols))

    with plt.ioff():
        fig, axes = plt.subplots(nrows, ncols,
                                  figsize=(figsize_per_axes[0] * ncols,
                                           figsize_per_axes[1] * nrows),
                                  squeeze=False, num=nvfit._next_fig_num())

        for i, point in enumerate(total_sweep):
            ax = axes[i // ncols][i % ncols]
            freq = point['freq']
            signal = point['signal']
            ax.plot(freq / 1e6, signal, '.', ms=3, color='0.5', label='data')

            fit = point.get('dip_fit')
            if fit is not None and fit['n_dips'] > 0:
                # 'best_fit' is the fitted model already evaluated at
                # `freq` (same array as the input spectrum's own
                # frequency axis).
                ax.plot(freq / 1e6, fit['best_fit'], 'r-', lw=1.5, label='fit')
                for c in fit['centers']:
                    ax.axvline(c / 1e6, color='r', ls=':', lw=0.8)

            if predicted_lines is not None and predicted_lines[i]:
                for pf in predicted_lines[i]:
                    ax.axvline(pf / 1e6, color='b', ls='--', lw=1.0)

            ax.set_title(_point_label(point), fontsize=9)
            ax.set_xlabel('Frequency (MHz)')
            ax.set_ylabel('Signal')

        for j in range(n, nrows * ncols):
            axes[j // ncols][j % ncols].axis('off')

        fig.tight_layout()

    nvfit._display_figure(fig)
    return fig, axes


# =============================================================================
# Step 3: joint single-coil-axis field fitting
# =============================================================================

def fit_coil_axis(total_sweep, quantization_axes, D=2.87e9, E=0.0, freq_range=None,
                   max_match_distance=5.0e6, bootstrap_max_match_distance=None,
                   u_guess=None, B_offset_guess=None,
                   fit_offset=True, max_iterations=20, convergence_tol=1.0e3,
                   guess_scale=None, n_starts=10, u_spread=0.2, B_offset_spread=3.0e6,
                   seed=0, merge_unresolved_dips=True):
    """
    Given `total_sweep` after `fit_sweep_dips` has populated `'dip_fit'`
    for every point, jointly fit a single field-per-amp vector `u` (Hz/A)
    and background offset `B_offset` shared across the ENTIRE sweep
    (see `nv_field_fitting.fit_coil_axis_from_dips`), using the physical
    prior that this coil's field direction is fixed for its whole current
    sweep.

    Parameters
    ----------
    total_sweep : list of dict
        Must have `'dip_fit'` populated for every point, and all points
        must share the same `'axis'` (as produced by
        `sweep_single_coil_odmr`).
    quantization_axes : as required by nv_field_fitting.
    D, E : float
        Zero-field-splitting / strain parameters, Hz.
    freq_range : (float, float), optional
        Passed through to nv_field_fitting; restricts which predicted
        transitions are eligible for matching at every current.
    max_match_distance : float, default 5e6
        Passed through to nv_field_fitting. If the initial guess is
        significantly off, this is automatically relaxed on a
        per-iteration basis via the bootstrap fallback -- see
        `bootstrap_max_match_distance` and
        `nv_field_fitting.fit_coil_axis_from_dips`'s docstring.
    bootstrap_max_match_distance : float, optional
        Loosened match-distance fallback used only when
        `max_match_distance` yields too few matches to constrain the fit
        at a given outer iteration. Defaults to None (no cutoff at all
        on the fallback pass -- maximally permissive). Rarely needs to
        be changed; exposed here mainly for diagnostic purposes.
    u_guess : (float, float, float), optional
        Initial guess for u, Hz/A. If None, defaults to the coil's
        nominal lab-axis direction times `guess_scale[axis]`.
    B_offset_guess : (float, float, float), optional
        Initial guess for the background offset, Hz. Defaults to zero.
    fit_offset : bool, default True
        If True, B_offset is fit as a free parameter.
    max_iterations, convergence_tol :
        Passed through to `fit_coil_axis_from_dips`.
    guess_scale : dict, optional
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A} rough scale used to build the
        default `u_guess` if not supplied explicitly. Defaults to
        {'X': 50e6, 'Y': 100e6, 'Z': 100e6}.
    n_starts : int, default 10
        Number of starting guesses (the unperturbed guess plus
        `n_starts - 1` randomly perturbed ones); the lowest-residual fit
        is kept. See `nv_field_fitting.fit_coil_axis_from_dips_multistart`.
        Set to 1 for a single fit from the given guess only.
    u_spread, B_offset_spread, seed :
        Perturbation size (fraction of |u_guess|; Hz) and RNG seed,
        passed through to `fit_coil_axis_from_dips_multistart`.
    merge_unresolved_dips : bool, default True
        If True, predicted transitions hidden inside one fitted dip
        (within its HWHM) are merged into it and compared via their mean
        (see `dip_widths_list` in
        `nv_field_fitting.fit_coil_axis_from_dips`). Important for
        single-coil sweeps along a symmetric direction (e.g. a cube axis,
        where all four NV orientations are nearly degenerate), where
        several lines overlap.

    Returns
    -------
    u_fit : ndarray, shape (3,)
        Fitted field-per-amp vector for this coil, Hz/A.
    B_offset_fit : ndarray, shape (3,)
        Fitted (or fixed) background offset, Hz.
    fit_info : dict
        As returned by `fit_coil_axis_from_dips_multistart` (including
        `'best_start'` and the per-start `'starts'` list), plus `'axis'`
        and `'direction_info'` (see `compute_coil_axis_direction`).
    """
    axes_present = {p['axis'] for p in total_sweep}
    if len(axes_present) != 1:
        raise ValueError(f"fit_coil_axis expects a single-coil sweep (all points sharing "
                          f"the same 'axis'), but found: {axes_present}. Use "
                          f"sweep_single_coil_odmr() to build total_sweep.")
    axis = axes_present.pop()

    if guess_scale is None:
        guess_scale = {'X': 50.0e6, 'Y': 100.0e6, 'Z': 100.0e6}
    nominal_axes = {'X': np.array([1.0, 0.0, 0.0]),
                     'Y': np.array([0.0, 1.0, 0.0]),
                     'Z': np.array([0.0, 0.0, 1.0])}

    if u_guess is None:
        u_guess = nominal_axes[axis] * guess_scale[axis]
    if B_offset_guess is None:
        B_offset_guess = np.zeros(3)

    currents = [p['current'] for p in total_sweep]
    observed_dips_list, dip_errors_list, dip_widths_list = _collect_dips(total_sweep)

    u_fit, B_offset_fit, fit_info = nvfit.fit_coil_axis_from_dips_multistart(
        quantization_axes, currents, observed_dips_list, u_guess,
        B_offset_guess=B_offset_guess, n_starts=n_starts, u_spread=u_spread,
        B_offset_spread=B_offset_spread, seed=seed,
        D=D, E=E, freq_range=freq_range,
        dip_errors_list=dip_errors_list, fit_offset=fit_offset,
        max_match_distance=max_match_distance,
        bootstrap_max_match_distance=bootstrap_max_match_distance,
        max_iterations=max_iterations, convergence_tol=convergence_tol,
        dip_widths_list=dip_widths_list if merge_unresolved_dips else None)

    for p, B_k in zip(total_sweep, fit_info['per_point_B']):
        p['field_fit'] = B_k

    direction_info = compute_coil_axis_direction(u_fit, axis)
    fit_info['axis'] = axis
    fit_info['direction_info'] = direction_info

    print(f"Coil {axis} fit {'converged' if fit_info['converged'] else 'DID NOT CONVERGE'} "
          f"after {fit_info['n_iterations']} iteration(s); "
          f"{fit_info['n_matches_total']} total matched dips "
          f"(per point: {fit_info['n_matches_per_point']}).")
    print(f"  RMS residual: {fit_info['rms_residual'] / 1e3:.1f} kHz (matched dips), "
          f"{fit_info['rms_residual_all_dips'] / 1e3:.1f} kHz (all dips, no cutoff).")
    if len(fit_info['starts']) > 1:
        print(f"  Best of {len(fit_info['starts'])} starting guesses: start "
              f"{fit_info['best_start']} (0 = unperturbed guess).")
        for k, s in enumerate(fit_info['starts']):
            marker = '*' if k == fit_info['best_start'] else ' '
            print(f"   {marker} start {k:2d}: all-dip RMS "
                  f"{s['rms_residual_all_dips'] / 1e3:9.1f} kHz, "
                  f"|u| {np.linalg.norm(s['u_fit']) / 1e6:8.3f} MHz/A, "
                  f"{'converged' if s['converged'] else 'not converged'}"
                  f"{', UNDERDETERMINED' if s['underdetermined'] else ''}")
    if fit_info['ambiguous']:
        print(f"  WARNING: {len(fit_info['alternative_solutions'])} other starting guess(es) "
              f"reached a different (u, B_offset) that fits the data equally well. This is "
              f"usually an exact NV-axis symmetry (identical spectra), so this coil's sweep "
              f"alone cannot pick between them:")
        for alt in fit_info['alternative_solutions']:
            print(f"      start {alt['start']:2d}: u = {np.round(alt['u_fit'] / 1e6, 3)} MHz/A, "
                  f"B_offset = {np.round(alt['B_offset_fit'] / 1e6, 3)} MHz")
        print(f"    This is expected: a single-coil sweep cannot fix which way the coil "
              f"tilts off its nominal axis. |u|, the on-axis component and the tilt "
              f"angle are still reliable. Resolve the rest with mixed-current points "
              f"via measure_mixed_currents_odmr() + resolve_coil_calibration().")
    if fit_info['bootstrap_iterations']:
        print(f"  NOTE: matching required a loosened tolerance (bootstrap) at outer "
              f"iteration(s) {fit_info['bootstrap_iterations']} -- this usually means "
              f"the initial guess (u_guess/B_offset_guess) was significantly off from "
              f"the true values, or max_match_distance is tight relative to how far off "
              f"the guess was. If this list includes iterations beyond 0, or the fit "
              f"still did not converge, consider supplying a better u_guess "
              f"(e.g. from a rough by-eye estimate of the total splitting range) or "
              f"increasing max_match_distance.")
    print(f"  u ({axis} coil, Hz/A): {u_fit}")
    print(f"  magnitude: {direction_info['magnitude'] / 1e6:.3f} MHz/A "
          f"({nvfit.field_freq_to_gauss(direction_info['magnitude']):.4f} G/A)")
    print(f"  direction: {np.round(direction_info['direction'], 4)}, "
          f"tilt from nominal {axis}: {direction_info['angle_deg']:.2f} deg")
    print(f"  B_offset (Hz): {B_offset_fit}  ({nvfit.field_freq_to_gauss(B_offset_fit)} G)")
    if fit_info['underdetermined']:
        print(f"  WARNING: fit is underdetermined (too few matched dips, even with "
              f"loosened tolerance, for the number of free parameters) -- result is "
              f"not reliable. This means there simply aren't enough usable dips across "
              f"the whole sweep; check individual points' dip_guesses/n_dips.")

    return u_fit, B_offset_fit, fit_info


def compute_coil_axis_direction(u, axis, nominal_axes=None):
    """
    Decompose a single coil's fitted field-per-amp vector `u` into
    magnitude and direction, and report the angular deviation from its
    nominal lab axis.

    Parameters
    ----------
    u : (float, float, float)
        Fitted field-per-amp vector, Hz/A.
    axis : {'X', 'Y', 'Z'}
        Which nominal lab axis this coil is meant to align with.
    nominal_axes : dict, optional
        {'X': unit vector, 'Y': ..., 'Z': ...}. Defaults to the standard
        basis vectors.

    Returns
    -------
    dict with keys:
        'magnitude'  : float, |u|, Hz/A.
        'direction'  : ndarray, shape (3,), unit vector.
        'angle_deg'  : float, angle between `direction` and the nominal
                       axis, degrees.
    """
    if nominal_axes is None:
        nominal_axes = {'X': np.array([1.0, 0.0, 0.0]),
                         'Y': np.array([0.0, 1.0, 0.0]),
                         'Z': np.array([0.0, 0.0, 1.0])}
    nominal = nominal_axes[axis]
    u = np.asarray(u, dtype=float)
    magnitude = float(np.linalg.norm(u))
    direction = u / magnitude if magnitude > 0 else np.zeros(3)
    cos_angle = np.clip(np.dot(direction, nominal), -1.0, 1.0)
    angle_deg = float(np.degrees(np.arccos(cos_angle)))
    return {'magnitude': magnitude, 'direction': direction, 'angle_deg': angle_deg}


# =============================================================================
# Combined interactive step (fitting dips + fitting the coil axis together)
# =============================================================================

def interactive_fit_coil_axis(total_sweep, dip_guesses, quantization_axes,
                               amplitude_guesses=None, dip_windows=None, sigma_guess=2.0e6,
                               D=2.87e9, E=0.0, freq_range=None, max_match_distance=5.0e6,
                               bootstrap_max_match_distance=None,
                               u_guess=None, B_offset_guess=None, fit_offset=True,
                               max_iterations=20, convergence_tol=1.0e3, guess_scale=None,
                               n_starts=10, u_spread=0.2, B_offset_spread=3.0e6, seed=0,
                               merge_unresolved_dips=True,
                               plot=True, ncols=3, figsize_per_axes=(4.0, 3.0)):
    """
    Convenience wrapper combining `fit_sweep_dips` and `fit_coil_axis` in
    one call, given `dip_guesses`/`dip_windows` already obtained (e.g.
    via `pick_dips_interactive`).

    After fitting, produces one grid figure showing, for every current:
    raw data, fitted Lorentzian model, fitted dip centers (red dotted
    lines), and the dip positions predicted by the joint (u, B_offset)
    fit at that current (blue dashed lines) -- letting you immediately
    see whether the fixed-direction model is consistent with the observed
    dips at every current, or whether something has gone wrong.

    Parameters are as in `fit_sweep_dips` and `fit_coil_axis`.

    Returns
    -------
    total_sweep : list of dict
        With `'dip_fit'` and `'field_fit'` populated for every point.
    u_fit, B_offset_fit, fit_info :
        As returned by `fit_coil_axis`.
    """
    fit_sweep_dips(total_sweep, dip_guesses, amplitude_guesses=amplitude_guesses,
                    dip_windows=dip_windows, sigma_guess=sigma_guess, plot=False)

    u_fit, B_offset_fit, fit_info = fit_coil_axis(
        total_sweep, quantization_axes, D=D, E=E, freq_range=freq_range,
        max_match_distance=max_match_distance,
        bootstrap_max_match_distance=bootstrap_max_match_distance,
        u_guess=u_guess, B_offset_guess=B_offset_guess,
        fit_offset=fit_offset, max_iterations=max_iterations, convergence_tol=convergence_tol,
        guess_scale=guess_scale, n_starts=n_starts, u_spread=u_spread,
        B_offset_spread=B_offset_spread, seed=seed,
        merge_unresolved_dips=merge_unresolved_dips)

    if plot:
        predicted_lines = []
        for point in total_sweep:
            predicted_dicts = nvfit.predict_odmr_dips(
                quantization_axes, point['field_fit'], D=D, E=E, freq_range=freq_range)
            predicted_lines.append([d['freq'] for d in predicted_dicts])
        _plot_sweep_with_dip_fits(total_sweep, ncols=ncols, figsize_per_axes=figsize_per_axes,
                                  predicted_lines=predicted_lines)

    return total_sweep, u_fit, B_offset_fit, fit_info


# =============================================================================
# Diagnostics
# =============================================================================

def plot_coil_axis_fan(total_sweep, u, B_offset, quantization_axes, D=2.87e9, E=0.0,
                        freq_range=None, n_curve_points=200):
    """
    Plot a 'fan diagram': the fitted dip centers (scatter) and
    model-predicted transition frequencies (continuous curves, evaluated
    on a dense current grid using the fitted (u, B_offset)) vs. coil
    current, all on one axes.

    This is usually the most direct visual check of whether the
    fixed-direction linear coil model actually describes the data: each
    predicted transition should trace a continuous curve that the fitted
    dip centers fall on/near at every current.

    Note: since NV transitions can cross/reorder as current (and hence
    field) increases, the predicted curves are re-ordered at each step to
    best match the previous step's curve values (simple greedy
    nearest-neighbor) purely for visual continuity -- this does not
    affect any fit result, only how the curves are drawn.

    Parameters
    ----------
    total_sweep : list of dict
        Must have `'dip_fit'` populated (see `fit_sweep_dips`).
    u, B_offset : (float, float, float)
        Fitted coil field-per-amp vector and background offset, Hz (Hz/A
        for u), as returned by `fit_coil_axis`.
    quantization_axes, D, E, freq_range :
        Passed through to `nv_field_fitting.predict_odmr_dips`.
    n_curve_points : int, default 200
        Number of points used to draw the continuous model curves.

    Returns
    -------
    (fig, ax) : matplotlib Figure and Axes.
    """
    u = np.asarray(u, dtype=float)
    B_offset = np.asarray(B_offset, dtype=float)
    currents = np.array([p['current'] for p in total_sweep], dtype=float)

    with plt.ioff():
        fig, ax = plt.subplots(figsize=(8, 5), num=nvfit._next_fig_num())

        for p in total_sweep:
            fit = p.get('dip_fit')
            if fit is None or fit['n_dips'] == 0:
                continue
            ax.plot([p['current']] * fit['n_dips'], np.asarray(fit['centers']) / 1e6,
                     'o', color='C0', ms=4)

        current_line = np.linspace(currents.min(), currents.max(), n_curve_points)
        predicted_curves = []
        for I in current_line:
            B = u * I + B_offset
            predicted_dicts = nvfit.predict_odmr_dips(quantization_axes, B, D=D, E=E,
                                                        freq_range=freq_range)
            predicted = np.array([d['freq'] for d in predicted_dicts], dtype=float)
            predicted_curves.append(predicted)

        # Greedy nearest-neighbor re-ordering, for visual curve continuity only.
        ordered_curves = [predicted_curves[0]]
        for pc in predicted_curves[1:]:
            prev = ordered_curves[-1]
            if len(pc) == 0 or len(prev) == 0:
                ordered_curves.append(pc)
                continue
            remaining = list(pc)
            new_order = []
            for prev_val in prev:
                if not remaining:
                    break
                idx = int(np.argmin(np.abs(np.array(remaining) - prev_val)))
                new_order.append(remaining.pop(idx))
            new_order.extend(remaining)
            ordered_curves.append(np.array(new_order))
        predicted_curves = ordered_curves

        max_n = max(len(pc) for pc in predicted_curves)
        for j in range(max_n):
            vals = [pc[j] / 1e6 if j < len(pc) else np.nan for pc in predicted_curves]
            ax.plot(current_line, vals, '-', color='C1', lw=1.0, alpha=0.8)

        ax.set_xlabel('Coil current (A)')
        ax.set_ylabel('Frequency (MHz)')
        ax.set_title('Fitted dips (blue) vs. model-predicted transitions (orange)')
        fig.tight_layout()

    nvfit._display_figure(fig)
    return fig, ax


# =============================================================================
# Final assembly: combine three independent single-coil fits
# =============================================================================

def assemble_coil_calibration(fit_results, offset_consistency_tol_gauss=0.5):
    """
    Combine three independent single-coil-axis fits (one per X/Y/Z coil,
    each from `fit_coil_axis`/`interactive_fit_coil_axis`) into a full 3x3
    coil-current -> NV-frame-field calibration matrix M and a single
    consensus B_offset.

    Column j of the returned M is exactly the `u_fit` from that coil's
    axis fit -- this function does no additional fitting of its own; it
    only assembles/cross-checks results already obtained independently
    for each coil.

    NOTE: each single-coil fit fixes |u|, the on-axis component and the
    tilt angle, but not which way the coil tilts (an exact NV-axis
    symmetry; see the module docstring, step 4). The off-axis entries of
    the M returned here are therefore one arbitrary choice out of 8 per
    coil. Use `resolve_coil_calibration()` with a few mixed-current points
    for a fully determined M.

    Since each single-coil sweep independently fits its OWN B_offset (the
    ambient/background field at zero current on that sweep), and all
    three sweeps should in principle see the SAME ambient field (all
    other coils held at 0 A throughout each sweep), the three fitted
    B_offset values act as a built-in consistency check: if they disagree
    by more than `offset_consistency_tol_gauss`, this likely indicates a
    problem with at least one of the individual coil fits (bad dip
    guesses, wrong-signed u_guess, drift between measurement sessions,
    etc), and a warning is printed.

    Parameters
    ----------
    fit_results : dict
        {'X': (u_X, B_offset_X, fit_info_X), 'Y': (...), 'Z': (...)} --
        exactly the return values of `fit_coil_axis`/
        `interactive_fit_coil_axis` for each coil, keyed by axis label.
        All three keys must be present.
    offset_consistency_tol_gauss : float, default 0.5
        Maximum allowed pairwise difference (Gauss) between the three
        fitted B_offset vectors before a warning is printed.

    Returns
    -------
    M : ndarray, shape (3, 3)
        Assembled coil-current -> NV-frame-field matrix, Hz/A. Columns
        ordered (X, Y, Z).
    B_offset : ndarray, shape (3,)
        Consensus B_offset, Hz -- the mean of the three individually
        fitted offsets.
    info : dict
        {'B_offsets_individual': dict of the three original B_offset
         vectors, 'B_offset_spread_gauss': float (max pairwise distance,
         Gauss), 'axis_directions': dict of `compute_coil_axis_direction`
         results per axis}
    """
    required = ('X', 'Y', 'Z')
    missing = [a for a in required if a not in fit_results]
    if missing:
        raise ValueError(f'fit_results missing entries for axes: {missing}')

    u_vecs, offsets, directions = {}, {}, {}
    for axis in required:
        u_fit, B_offset_fit, info_axis = fit_results[axis]
        u_vecs[axis] = np.asarray(u_fit, dtype=float)
        offsets[axis] = np.asarray(B_offset_fit, dtype=float)
        directions[axis] = info_axis.get('direction_info', compute_coil_axis_direction(u_fit, axis))

    M = np.column_stack([u_vecs['X'], u_vecs['Y'], u_vecs['Z']])

    offset_list_gauss = [nvfit.field_freq_to_gauss(offsets[a]) for a in required]
    max_spread = 0.0
    for i in range(3):
        for j in range(i + 1, 3):
            spread = float(np.linalg.norm(offset_list_gauss[i] - offset_list_gauss[j]))
            max_spread = max(max_spread, spread)

    B_offset = np.mean([offsets[a] for a in required], axis=0)

    if max_spread > offset_consistency_tol_gauss:
        print(f'WARNING: fitted B_offset differs by up to {max_spread:.3f} G between '
              f'the three independent coil-axis fits (tolerance '
              f'{offset_consistency_tol_gauss} G) -- check individual coil fits for '
              f'consistency before trusting the combined calibration.')
        for a in required:
            print(f'  B_offset from {a} sweep: {offsets[a]} Hz '
                  f'({nvfit.field_freq_to_gauss(offsets[a])} G)')

    info = {
        'B_offsets_individual': offsets,
        'B_offset_spread_gauss': max_spread,
        'axis_directions': directions,
    }

    print(f'Assembled coil calibration matrix M (Hz/A):\n{M}')
    print(f'M (Gauss/A):\n{nvfit.field_freq_to_gauss(M)}')
    print(f'Consensus B_offset (Hz): {B_offset}  ({nvfit.field_freq_to_gauss(B_offset)} G)')

    return M, B_offset, info


# =============================================================================
# Full calibration: resolve per-coil symmetry ambiguity with mixed currents
# =============================================================================

def resolve_coil_calibration(fit_results, mixed_sweep, quantization_axes,
                             single_coil_sweeps=None, D=2.87e9, E=0.0, freq_range=None,
                             nominal_axes=None, refine=True, max_match_distance=5.0e6,
                             bootstrap_max_match_distance=None, fit_offset=True,
                             max_iterations=20, convergence_tol=1.0e3, n_refine=12,
                             margin_warn=2.0, merge_unresolved_dips=True,
                             plot=True, ncols=3, figsize_per_axes=(4.0, 3.0)):
    """
    Build the full, unambiguous 3x3 coil calibration matrix M
    (B = M @ I + B_offset) from the three single-coil fits plus a few
    mixed-current points.

    Steps:
      1. For each coil, list the 4-8 fits equivalent to its single-coil
         fit under the NV-axis symmetries that keep +coil pointing
         closest to its nominal direction
         (`nv_field_fitting.coil_direction_candidates`).
      2. Score all combinations (e.g. 8 x 8 x 8) against the dips at the
         mixed-current points
         (`nv_field_fitting.resolve_coil_symmetry_from_mixed_dips`).
      3. If `refine`, take the `n_refine` best-scoring combinations (with
         distinct M) as starting points, jointly fit all 9 entries of M
         (+ B_offset) from each to the mixed points and, if given, all
         single-coil sweep points
         (`nv_field_fitting.fit_coil_matrix_from_dips`), and keep the
         lowest-residual result. Without `refine`, the best-scoring
         combination from step 2 is used as-is.

    The ratio of the best residual among genuinely DIFFERENT M's to the
    best one ('margin') measures how clearly the data single out the
    answer.

    Why step 3 refines several starts rather than just the best-scoring
    one: when a coil points along a symmetric direction (e.g. a cube
    axis, at the same angle to all four NV axes), several NV
    orientations are nearly degenerate, so its single-coil sweep pins
    down the off-axis components only roughly (they live in
    sub-linewidth splittings). Every candidate, including the right one, then
    mispredicts the mixed dips somewhat and the unrefined scores can be
    close; after refinement the right combination fits clearly better.

    No measurable background field is needed: the mixed points alone
    break the ambiguity.

    Parameters
    ----------
    fit_results : dict
        {'X': (u_X, B_offset_X, fit_info_X), 'Y': ..., 'Z': ...} -- the
        return values of `fit_coil_axis`/`interactive_fit_coil_axis` per
        coil (same format as for `assemble_coil_calibration`).
    mixed_sweep : list of dict
        From `measure_mixed_currents_odmr`, with `'dip_fit'` populated by
        `fit_sweep_dips` (after `pick_dips_interactive`). Modified in
        place: each point gains `'field_fit'` (M @ I + B_offset).
    quantization_axes : as required by nv_field_fitting.
    single_coil_sweeps : dict, optional
        {'X': total_sweep_X, 'Y': ..., 'Z': ...}, the single-coil sweeps
        (with `'dip_fit'`), to include in the step-3 joint fits. Not
        modified. Usually better left out: single-coil spectra along a
        symmetric direction (e.g. a cube axis) consist mostly of blends
        of unresolved lines, whose
        fitted centers carry systematic errors (~ a few hundred kHz)
        far larger than their statistical errors, so they pull M off.
        In simulations, the joint fit on 5-6 well-resolved mixed points
        alone was about twice as accurate as with the single-coil data
        added. Include them only if you have fewer than 5 mixed points
        (the mixed-only fit needs at least 4, or 3 with
        `fit_offset=False`, since each point fixes at most the 3 field
        components).
    D, E, freq_range :
        As in `fit_coil_axis`.
    nominal_axes : dict, optional
        {'X': direction, 'Y': ..., 'Z': ...} -- rough field direction of
        each coil for POSITIVE current. Defaults to +x, +y, +z.
    refine : bool, default True
        Run the joint fits (step 3). If False, M is the best-scoring
        combination of single-coil fits as-is.
    max_match_distance, bootstrap_max_match_distance, fit_offset,
    max_iterations, convergence_tol :
        Passed through to the joint fit, as in `fit_coil_axis`. With
        `fit_offset=False`, step 2 also scores with zero background
        instead of the candidates' fitted offsets.
    n_refine : int, default 12
        Number of top-scoring combinations refined in step 3. Each is
        one joint fit (typically well under a second to a few seconds).
    margin_warn : float, default 2.0
        Print a warning if the best different M's RMS is less than this
        many times the best one's.
    merge_unresolved_dips : bool, default True
        As in `fit_coil_axis`; applied to both the scoring (step 2) and
        the joint fit (step 3).
    plot : bool, default True
        Plot every mixed point's spectrum and dip fit with the final
        model's predicted dips overlaid (blue dashed).
    ncols, figsize_per_axes :
        Grid-plot layout, as in `plot_sweep_spectra`.

    Returns
    -------
    M : ndarray, shape (3, 3)
        Calibration matrix, Hz/A, columns ordered (X, Y, Z).
    B_offset : ndarray, shape (3,)
        Background offset, Hz.
    info : dict
        'coil_candidates'   : {axis: candidate list} (step 1).
        'chosen_candidates' : {axis: candidate dict the final M started from}.
        'resolution'        : dict from
                              `resolve_coil_symmetry_from_mixed_dips`
                              (unrefined step-2 scoring: 'choice', 'rms',
                              'runner_up_rms', 'margin', 'scores', ...).
        'refined'           : list of dict ('chosen', 'M_start', 'M',
                              'B_offset', 'fit_info'), one per refined
                              start, best first; empty if not `refine`.
        'margin'            : float, see above (from step 3 if `refine`,
                              else from step 2).
        'ambiguous'         : bool, margin < margin_warn.
        'refine_info'       : fit_info of the winning joint fit, or None.
        'axis_directions'   : {axis: `compute_coil_axis_direction` result}.
    """
    required = ('X', 'Y', 'Z')
    missing = [a for a in required if a not in fit_results]
    if missing:
        raise ValueError(f'fit_results missing entries for axes: {missing}')
    if any('dip_fit' not in p for p in mixed_sweep):
        raise ValueError('Every mixed_sweep point needs a \'dip_fit\' -- run '
                         'pick_dips_interactive() and fit_sweep_dips() on it first.')

    if nominal_axes is None:
        nominal_axes = {'X': (1.0, 0.0, 0.0), 'Y': (0.0, 1.0, 0.0), 'Z': (0.0, 0.0, 1.0)}
    nominal_axes = {a: np.asarray(nominal_axes[a], dtype=float)
                       / np.linalg.norm(nominal_axes[a]) for a in required}

    # === Step 1: symmetry-equivalent candidates per coil ===
    symmetries = nvfit.nv_axis_symmetries(quantization_axes)
    coil_candidates = {}
    for a in required:
        u_fit, B_offset_fit, _ = fit_results[a]
        coil_candidates[a] = nvfit.coil_direction_candidates(
            u_fit, B_offset_fit, nominal_axes[a], symmetries)

    # === Step 2: score every combination on the mixed points ===
    mixed_I = np.array([p['current_vec'] for p in mixed_sweep], dtype=float)
    mixed_obs, mixed_errs, mixed_widths = _collect_dips(mixed_sweep)
    if sum(len(o) for o in mixed_obs) == 0:
        raise ValueError('No fitted dips at any mixed-current point.')

    resolution = nvfit.resolve_coil_symmetry_from_mixed_dips(
        quantization_axes, [coil_candidates[a] for a in required], mixed_I, mixed_obs,
        D=D, E=E, freq_range=freq_range, use_offset=fit_offset,
        dip_widths_list=mixed_widths if merge_unresolved_dips else None)

    print(f"Symmetry resolution: {len(symmetries)} NV-axis symmetries; candidates per coil "
          f"{ {a: len(coil_candidates[a]) for a in required} }; "
          f"{resolution['n_combinations']} combinations scored on {len(mixed_sweep)} "
          f"mixed point(s).")
    print(f"  Unrefined scoring: best all-dip RMS {resolution['rms'] / 1e3:.1f} kHz, "
          f"runner-up {resolution['runner_up_rms'] / 1e3:.1f} kHz "
          f"(margin {resolution['margin']:.1f}x).")

    def _combination(choice):
        chosen_c = {a: coil_candidates[a][choice[j]] for j, a in enumerate(required)}
        M_c = np.column_stack([chosen_c[a]['u'] for a in required])
        B_c = (np.mean([chosen_c[a]['B_offset'] for a in required], axis=0) if fit_offset
               else np.zeros(3))
        return chosen_c, M_c, B_c

    def _distinct(M_a, M_b):
        return np.max(np.abs(M_a - M_b)) > 0.01 * np.linalg.norm(M_a, axis=0).max()

    refine_info = None
    refined = []
    if not refine:
        chosen, M, B_offset = _combination(resolution['choice'])
        margin = resolution['margin']
    else:
        # === Step 3: refine the top combinations on all data, keep the best ===
        current_vecs = list(mixed_I)
        observed_dips_list = list(mixed_obs)
        dip_errors_list = list(mixed_errs)
        dip_widths_list = list(mixed_widths)
        if single_coil_sweeps:
            for a in required:
                sweep = single_coil_sweeps.get(a)
                if not sweep:
                    continue
                obs, errs, widths = _collect_dips(sweep)
                current_vecs += [np.asarray(p['current_vec'], dtype=float) for p in sweep]
                observed_dips_list += obs
                dip_errors_list += errs
                dip_widths_list += widths
        else:
            n_needed = 4 if fit_offset else 3
            n_mixed = sum(1 for o in mixed_obs if len(o) > 0)
            if n_mixed < n_needed:
                print(f'  WARNING: only {n_mixed} mixed point(s) with dips and no '
                      f'single_coil_sweeps -- a fit of M from mixed points alone needs at '
                      f'least {n_needed} (each fixes at most 3 field components), so the '
                      f'result is not reliable. Measure more mixed points, or pass '
                      f'single_coil_sweeps.')
            elif n_mixed == n_needed:
                print(f'  NOTE: {n_mixed} mixed points exactly determine M, leaving no '
                      f'redundancy to catch a bad dip fit; a 5th point is recommended.')

        # Top-scoring combinations with distinct M, as starting points.
        starts = []
        for _, choice in resolution['scores']:
            chosen_c, M_c, B_c = _combination(choice)
            if all(_distinct(M_c, s[1]) for s in starts):
                starts.append((chosen_c, M_c, B_c))
            if len(starts) >= n_refine:
                break

        for chosen_c, M_c, B_c in starts:
            M_r, B_r, info_r = nvfit.fit_coil_matrix_from_dips(
                quantization_axes, current_vecs, observed_dips_list, M_c,
                B_offset_guess=B_c, D=D, E=E, freq_range=freq_range,
                dip_errors_list=dip_errors_list, fit_offset=fit_offset,
                max_match_distance=max_match_distance,
                bootstrap_max_match_distance=bootstrap_max_match_distance,
                max_iterations=max_iterations, convergence_tol=convergence_tol,
                dip_widths_list=dip_widths_list if merge_unresolved_dips else None)
            refined.append({'chosen': chosen_c, 'M_start': M_c, 'M': M_r, 'B_offset': B_r,
                            'fit_info': info_r})

        def _score(r):
            rms = r['fit_info']['rms_residual_all_dips']
            return (r['fit_info']['underdetermined'], rms if np.isfinite(rms) else np.inf)

        refined.sort(key=_score)
        best = refined[0]
        chosen, M, B_offset, refine_info = (best['chosen'], best['M'], best['B_offset'],
                                            best['fit_info'])
        best_rms = refine_info['rms_residual_all_dips']
        runner_up_rms = next((r['fit_info']['rms_residual_all_dips'] for r in refined[1:]
                              if _distinct(r['M'], M)), float('inf'))
        margin = runner_up_rms / best_rms if best_rms > 0 else float('inf')

        if np.isfinite(runner_up_rms):
            outcome = (f"best different M {runner_up_rms / 1e3:.1f} kHz "
                       f"(margin {margin:.1f}x)")
        else:
            outcome = 'every start converged to the same M'
        print(f"  Refined the top {len(refined)} combination(s) on all {len(current_vecs)} "
              f"point(s): best all-dip RMS {best_rms / 1e3:.1f} kHz; {outcome}.")
        print(f"Joint fit of M {'converged' if refine_info['converged'] else 'DID NOT CONVERGE'} "
              f"after {refine_info['n_iterations']} iteration(s); "
              f"{refine_info['n_matches_total']} matched dips; RMS "
              f"{refine_info['rms_residual'] / 1e3:.1f} kHz (matched), "
              f"{best_rms / 1e3:.1f} kHz (all dips).")
        if refine_info['bootstrap_iterations']:
            print(f"  NOTE: matching needed the loosened (bootstrap) tolerance at "
                  f"iteration(s) {refine_info['bootstrap_iterations']}.")
        if refine_info['underdetermined']:
            print('  WARNING: joint fit is underdetermined (too few matched dips for '
                  '12 or 9 free parameters) -- result is not reliable.')

    ambiguous = margin < margin_warn
    if ambiguous:
        print(f"  WARNING: margin below {margin_warn}x -- the data do not clearly single "
              f"out one combination, so the off-axis parts of M may be wrong. Add mixed "
              f"points with a larger field along a generic direction (all 8 dips "
              f"resolved; see measure_mixed_currents_odmr), and check the dip fits at the "
              f"existing ones.")

    for p in mixed_sweep:
        p['field_fit'] = M @ np.asarray(p['current_vec'], dtype=float) + B_offset

    # === Report ===
    directions = {a: compute_coil_axis_direction(M[:, j], a, nominal_axes=nominal_axes)
                  for j, a in enumerate(required)}
    print(f'Coil calibration matrix M (Hz/A):\n{M}')
    print(f'M (Gauss/A):\n{nvfit.field_freq_to_gauss(M)}')
    for a in required:
        d = directions[a]
        print(f"  {a} coil: {d['magnitude'] / 1e6:.3f} MHz/A "
              f"({nvfit.field_freq_to_gauss(d['magnitude']):.4f} G/A), direction "
              f"{np.round(d['direction'], 4)}, tilt from nominal {d['angle_deg']:.2f} deg")
    print(f'B_offset (Hz): {B_offset}  ({nvfit.field_freq_to_gauss(B_offset)} G)')

    if plot:
        predicted_lines = []
        for p in mixed_sweep:
            predicted_dicts = nvfit.predict_odmr_dips(
                quantization_axes, p['field_fit'], D=D, E=E, freq_range=freq_range)
            predicted_lines.append([d['freq'] for d in predicted_dicts])
        _plot_sweep_with_dip_fits(mixed_sweep, ncols=ncols, figsize_per_axes=figsize_per_axes,
                                  predicted_lines=predicted_lines)

    info = {
        'coil_candidates': coil_candidates,
        'chosen_candidates': chosen,
        'resolution': resolution,
        'refined': refined,
        'margin': margin,
        'ambiguous': ambiguous,
        'refine_info': refine_info,
        'axis_directions': directions,
    }
    return M, B_offset, info


# =============================================================================
# Automated calibration: rough per-coil strengths + mixed-current points
# =============================================================================

def _nominal_axes_dict(nominal_axes):
    """Normalized {'X','Y','Z'} -> nominal positive-current field direction."""
    if nominal_axes is None:
        nominal_axes = {'X': (1.0, 0.0, 0.0), 'Y': (0.0, 1.0, 0.0), 'Z': (0.0, 0.0, 1.0)}
    return {a: np.asarray(nominal_axes[a], dtype=float) / np.linalg.norm(nominal_axes[a])
            for a in ('X', 'Y', 'Z')}


def _prior_matrix(coil_strengths, nominal_axes, M_prior=None):
    """M_prior (Hz/A): `M_prior` itself if given, else column j = strength
    of coil j times its nominal direction."""
    if M_prior is not None:
        return np.asarray(M_prior, dtype=float).reshape(3, 3)
    if coil_strengths is None:
        raise ValueError('Give either coil_strengths or M_prior.')
    return np.column_stack([coil_strengths[a] * nominal_axes[a] for a in ('X', 'Y', 'Z')])


def _clean_auto_dip_fit(fit, min_width_frac=0.3, max_width_factor=4.0, min_depth_frac=0.3):
    """
    Drop spurious dips from an automatic multi-Lorentzian fit with a FIXED
    dip count: when the spectrum has fewer resolved dips than requested
    (e.g. two lines merged), the extra Lorentzian ends up on a noise
    spike (width far below the others, often below the frequency step)
    or as a broad, shallow baseline wiggle. Dips narrower than
    `min_width_frac` x, broader than `max_width_factor` x, or shallower
    than `min_depth_frac` x the median dip are removed. Merged pairs
    (deeper and somewhat broader than single lines) are kept.

    Returns a copy of `fit` with 'centers', 'center_errs', 'amplitudes',
    'sigmas' and 'n_dips' filtered, plus 'n_rejected'. 'best_fit' (the
    full model curve) is left unchanged for plotting.
    """
    sig = np.abs(np.asarray(fit['sigmas'], dtype=float))
    amp = np.asarray(fit['amplitudes'], dtype=float)
    if len(sig) == 0:
        return dict(fit, n_rejected=0)
    s_med, a_med = np.median(sig), np.median(amp)
    keep = ((sig >= min_width_frac * s_med) & (sig <= max_width_factor * s_med)
            & (amp >= min_depth_frac * a_med))
    cleaned = dict(fit)
    for key in ('centers', 'center_errs', 'amplitudes', 'sigmas'):
        cleaned[key] = np.asarray(fit[key])[keep]
    cleaned['n_dips'] = int(np.sum(keep))
    cleaned['n_rejected'] = int(len(keep) - np.sum(keep))
    return cleaned


def plan_mixed_currents(coil_strengths, quantization_axes, target_field_gauss=20.0,
                        max_current=None, n_points=6, nominal_axes=None, D=2.87e9, E=0.0,
                        M_prior=None, exclude_directions=None):
    """
    Choose current vectors for the mixed-current calibration points, such
    that the total field points along the directions where all 8 ODMR
    dips are best resolved (`nv_field_fitting.most_resolved_field_directions`).

    Currents are computed from the ROUGH prior M_prior (each coil's
    `coil_strengths` along its nominal axis, no tilt), so the actual
    fields differ by the (unknown) coil tilts and strength errors. The
    well-resolved region around the best direction is small (all dips
    stay >~ 9 MHz apart within 5 deg of it at 20 G, but can merge 10 deg
    away), so with tilts of ~8 deg or more a pair of dips may merge at
    some points; `auto_calibrate_coils` then re-plans with the fitted M.

    Parameters
    ----------
    coil_strengths : dict or None
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A} rough field-per-amp of each coil
        (e.g. from `nv_field_fitting.estimate_coil_strength_from_dips`;
        `nv_field_fitting.field_gauss_to_freq` converts from G/A).
        Ignored if `M_prior` is given.
    quantization_axes : as required by nv_field_fitting.
    target_field_gauss : float, default 20.0
        Desired |B| at every point, Gauss. Larger -> better-separated dips
        (spacing grows ~linearly with |B|), as long as they stay inside the
        ODMR scan window.
    max_current : float or dict, optional
        Current limit, A, either for all coils or {'X': ..., 'Y': ..., 'Z':
        ...}. A point needing more is scaled down (smaller |B|) to fit.
    n_points : int, default 6
    nominal_axes : dict, optional
        Positive-current field direction of each coil; defaults to +x/+y/+z.
    D, E : float
    M_prior : array_like, shape (3, 3), optional
        Full prior matrix (Hz/A) to plan with instead of
        `coil_strengths` -- e.g. a previous fit of M.
    exclude_directions : array_like, shape (m, 3), optional
        Field directions already measured; the planned directions are
        chosen as far as possible from these (and each other).

    Returns
    -------
    current_vecs : list of (float, float, float)
        (I_X, I_Y, I_Z) per point, A.
    info : dict
        'directions'         : (n, 3) target field directions.
        'predicted_fields'   : (n, 3) M_prior @ I, Hz.
        'predicted_min_separation' : list of float, Hz -- smallest predicted
                               dip spacing per point (after any scaling).
    """
    nominal_axes = _nominal_axes_dict(nominal_axes)
    M_prior = _prior_matrix(coil_strengths, nominal_axes, M_prior)
    B_mag = nvfit.field_gauss_to_freq(target_field_gauss)
    directions, _ = nvfit.most_resolved_field_directions(
        quantization_axes, B_mag, n_directions=n_points, D=D, E=E,
        exclude_directions=exclude_directions)

    if max_current is None:
        limits = np.full(3, np.inf)
    elif isinstance(max_current, dict):
        limits = np.array([max_current[a] for a in ('X', 'Y', 'Z')], dtype=float)
    else:
        limits = np.full(3, float(max_current))

    axes_n = [nvfit._normalize(a) for a in quantization_axes]
    current_vecs, fields, seps = [], [], []
    for d in directions:
        I = np.linalg.solve(M_prior, B_mag * d)
        scale = min(1.0, float(np.min(limits / np.maximum(np.abs(I), 1e-12))))
        I = I * scale
        B = M_prior @ I
        current_vecs.append(tuple(float(c) for c in I))
        fields.append(B)
        seps.append(nvfit._min_dip_separation(axes_n, B, D=D, E=E))

    print(f'Planned {len(current_vecs)} mixed-current point(s) '
          f'(target |B| = {target_field_gauss:.1f} G):')
    for I, B, s in zip(current_vecs, fields, seps):
        print(f'  I = ({I[0]:+.3f}, {I[1]:+.3f}, {I[2]:+.3f}) A -> |B| ~ '
              f'{nvfit.field_freq_to_gauss(np.linalg.norm(B)):.1f} G, closest dips ~ '
              f'{s / 1e6:.1f} MHz apart')

    return current_vecs, {'directions': directions, 'predicted_fields': np.array(fields),
                          'predicted_min_separation': seps}


def _fibonacci_directions(n):
    """n near-uniformly spread unit vectors (Fibonacci sphere), shape (n, 3)."""
    k = np.arange(n) + 0.5
    polar = np.arccos(1.0 - 2.0 * k / n)
    azimuth = np.pi * (1.0 + 5.0 ** 0.5) * k
    return np.column_stack([np.cos(azimuth) * np.sin(polar),
                            np.sin(azimuth) * np.sin(polar), np.cos(polar)])


def plan_spread_currents(freq_start, freq_stop, quantization_axes, coil_strengths=None,
                         span_fraction=0.8, max_current=None, nominal_axes=None, M_prior=None,
                         D=2.87e9, E=0.0, robustness_deg=5.0, robustness_frac=0.05,
                         n_directions=4000, n_magnitudes=81, n_refine=20, n_steps=60, seed=0,
                         include_sign_flips=False, verbose=True):
    """
    Current triplets that spread the 8 ODMR dips as far apart as possible
    over a chosen part of a frequency range -- e.g. for well-resolved
    calibration points. No hardware access.

    The dips are confined to the central `span_fraction` of
    [freq_start, freq_stop] (e.g. 0.8 leaves 10% free at each end). Among
    all fields whose dips fit there and whose currents respect
    `max_current`, the one maximizing the WORST-CASE smallest gap between
    neighbouring dips is chosen, where the worst case is taken over the
    actual field being off by up to `robustness_deg` in direction and
    `robustness_frac` in strength (unknown coil tilts / rough strengths).
    Search: a coarse grid of directions x magnitudes, then a shrinking
    random local search around the `n_refine` best grid points.

    All symmetry-equivalent copies of the winning field (see
    `nv_field_fitting.nv_axis_symmetries`) give the IDENTICAL spectrum;
    each is converted to currents with the prior M, and those within
    `max_current` are returned, lowest maximum current first. Flipping
    all three signs of a triplet also gives the same spectrum (returned
    explicitly only with `include_sign_flips`). Several of them, with
    varied sign patterns, make a good set of calibration points.

    Parameters
    ----------
    freq_start, freq_stop : float
        Frequency range, Hz (order does not matter). D must lie inside
        the chosen sub-range.
    quantization_axes : as required by nv_field_fitting.
    coil_strengths : dict, optional
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A} rough field-per-amp of each coil
        along its nominal axis (`nv_field_fitting.field_gauss_to_freq`
        converts from G/A). Ignored if `M_prior` is given.
    span_fraction : float, default 0.8
        Fraction of the range, centred, that the dips may occupy.
    max_current : float or dict, optional
        Current limit, A, for all coils or {'X': ..., 'Y': ..., 'Z': ...}.
    nominal_axes : dict, optional
        Positive-current field direction of each coil; defaults to +x/+y/+z.
    M_prior : array_like, shape (3, 3), optional
        Full calibration matrix (Hz/A), e.g. a previous fit, instead of
        `coil_strengths`.
    D, E : float
    robustness_deg, robustness_frac : float, default 5.0, 0.05
        Size of the field errors the spacing must survive (0, 0 optimizes
        the nominal spacing only).
    n_directions, n_magnitudes, n_refine, n_steps, seed :
        Search resolution / effort / RNG seed.
    include_sign_flips : bool, default False
        Also list every triplet with all signs flipped.
    verbose : bool, default True
        Print the result.

    Returns
    -------
    current_vecs : list of (float, float, float)
        (I_X, I_Y, I_Z) in A, lowest maximum |I| first.
    info : dict
        'field' (Hz, the chosen field for current_vecs[0]'s pattern up to
        symmetry), 'field_magnitude' (Hz), 'field_gauss', 'dips' (Hz,
        sorted), 'gaps' (Hz), 'min_gap' (Hz, nominal), 'worst_case_min_gap'
        (Hz), 'worst_case_extent' ((lo, hi) Hz, dips under the field
        errors), 'window' ((lo, hi) Hz), 'max_abs_current' (A, per triplet).

    Raises
    ------
    ValueError
        If D is outside the sub-range, or no field satisfies the window
        and current limits.
    """
    required = ('X', 'Y', 'Z')
    axes_n = [nvfit._normalize(a) for a in quantization_axes]
    nominal_axes = _nominal_axes_dict(nominal_axes)
    M = _prior_matrix(coil_strengths, nominal_axes, M_prior)
    M_inv = np.linalg.inv(M)
    if max_current is None:
        limits = np.full(3, np.inf)
    elif isinstance(max_current, dict):
        limits = np.array([float(max_current[a]) for a in required])
    else:
        limits = np.full(3, float(max_current))

    f_lo, f_hi = sorted((float(freq_start), float(freq_stop)))
    margin = 0.5 * (1.0 - float(span_fraction)) * (f_hi - f_lo)
    lo, hi = f_lo + margin, f_hi - margin
    if not lo < D < hi:
        raise ValueError(f'D = {D / 1e9:.4f} GHz lies outside the target sub-range '
                         f'{lo / 1e9:.4f}-{hi / 1e9:.4f} GHz.')

    def _min_gaps_and_ok(B):
        f = np.sort(nvfit._batch_transitions(axes_n, B, D=D, E=E), axis=1)
        in_window = (f[:, 0] >= lo) & (f[:, -1] <= hi)
        in_limits = np.all(np.abs(B @ M_inv.T) <= limits, axis=1)
        return np.min(np.diff(f, axis=1), axis=1), in_window & in_limits, f

    # === Fixed set of field perturbations for the worst-case score ===
    phis = np.linspace(0.0, 2 * np.pi, 8, endpoint=False)
    scales = (1.0 - robustness_frac, 1.0, 1.0 + robustness_frac)
    ang = np.radians(robustness_deg)

    def _perturbed(m, d):
        e1 = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(d, e1)
        ds = [d] + [np.cos(ang) * d + np.sin(ang) * (np.cos(p) * e1 + np.sin(p) * e2)
                    for p in phis]
        return np.array([s * m * v for v in ds for s in scales])

    def _robust_score(m, d):
        """(worst-case min gap, nominal ok); nominal field must fit the
        window and current limits, perturbed ones only count for gaps."""
        gaps, ok, _ = _min_gaps_and_ok(m * d[None, :])
        if not ok[0]:
            return -np.inf
        f = np.sort(nvfit._batch_transitions(axes_n, _perturbed(m, d), D=D, E=E), axis=1)
        return float(np.min(np.diff(f, axis=1)))

    # === Coarse grid (nominal score) ===
    dirs = _fibonacci_directions(n_directions)
    b_hi = 2.5 * max(D - lo, hi - D)
    candidates = []
    for m in np.linspace(0.02, 1.0, n_magnitudes) * b_hi:
        gaps, ok, _ = _min_gaps_and_ok(m * dirs)
        for i in np.flatnonzero(ok):
            candidates.append((float(gaps[i]), m, dirs[i]))
    if not candidates:
        raise ValueError(f'No field puts all dips inside {lo / 1e9:.4f}-{hi / 1e9:.4f} GHz '
                         f'within the current limits {dict(zip(required, limits))} A. Widen '
                         f'the range / span_fraction or raise max_current.')
    candidates.sort(key=lambda c: -c[0])
    starts = []
    for gap, m, d in candidates:
        if all(np.degrees(np.arccos(np.clip(abs(np.dot(d, s[1])), -1, 1))) > 2.0
               or abs(m / s[0] - 1) > 0.05 for s in starts):
            starts.append((m, d))
        if len(starts) >= n_refine:
            break

    # === Local refinement on the worst-case score ===
    rng = np.random.default_rng(seed)
    best = (-np.inf, None, None)
    for m, d in starts:
        score = _robust_score(m, d)
        for step in range(n_steps):
            scale = 1.0 - step / n_steps
            dd = d + np.radians(2.0 * scale) * rng.normal(size=3)
            dd /= np.linalg.norm(dd)
            mm = m * (1.0 + 0.02 * scale * rng.normal())
            s_new = _robust_score(mm, dd)
            if s_new > score:
                m, d, score = mm, dd, s_new
        if score > best[0]:
            best = (score, m, d)
    worst_gap, m, d = best
    if not np.isfinite(worst_gap):
        raise ValueError('No robust solution found; try robustness_deg=0 or a wider range.')

    # === Symmetry-equivalent current triplets ===
    images = []
    for g in nvfit.nv_axis_symmetries(axes_n):
        v = g @ d
        if not any(np.allclose(v, w, atol=1e-6) or np.allclose(v, -w, atol=1e-6)
                   for w in images):
            images.append(v)
    triplets = []
    for v in images:
        I = M_inv @ (m * v)
        if np.all(np.abs(I) <= limits):
            triplets.append(I)
            if include_sign_flips:
                triplets.append(-I)
    triplets.sort(key=lambda I: (float(np.max(np.abs(I))), tuple(-np.sign(I))))
    current_vecs = [tuple(float(c) for c in I) for I in triplets]

    B = m * d
    dips = np.sort([f for a in axes_n for f in nvfit.nv_transition_frequencies(a, B, D=D, E=E)])
    pert = np.sort(nvfit._batch_transitions(axes_n, _perturbed(m, d), D=D, E=E), axis=1)
    info = {
        'field': B, 'field_magnitude': float(m), 'field_gauss': float(nvfit.field_freq_to_gauss(m)),
        'dips': dips, 'gaps': np.diff(dips), 'min_gap': float(np.min(np.diff(dips))),
        'worst_case_min_gap': float(worst_gap),
        'worst_case_extent': (float(pert[:, 0].min()), float(pert[:, -1].max())),
        'window': (lo, hi),
        'max_abs_current': [float(np.max(np.abs(I))) for I in current_vecs],
    }

    if verbose:
        print(f'Dips confined to {lo / 1e9:.4f}-{hi / 1e9:.4f} GHz '
              f'({100 * span_fraction:.0f}% of {f_lo / 1e9:.4f}-{f_hi / 1e9:.4f} GHz).')
        print(f'Best field: |B| = {m / 1e6:.0f} MHz ({info["field_gauss"]:.0f} G); dips span '
              f'{dips[0] / 1e9:.3f}-{dips[-1] / 1e9:.3f} GHz; smallest gap '
              f'{info["min_gap"] / 1e6:.0f} MHz (>= {worst_gap / 1e6:.0f} MHz if the field is '
              f'off by {robustness_deg:g} deg / {100 * robustness_frac:g}%).')
        print('  dips (GHz):', ', '.join(f'{x / 1e9:.3f}' for x in dips))
        print('  gaps (MHz):', ', '.join(f'{x / 1e6:.0f}' for x in np.diff(dips)))
        print(f'{len(current_vecs)} current triplet(s) giving this spectrum'
              + ('' if include_sign_flips else ' (each also with all signs flipped)') + ':')
        for I, imax in zip(current_vecs, info['max_abs_current']):
            print(f'  ({I[0]:+6.3f}, {I[1]:+6.3f}, {I[2]:+6.3f}) A   max |I| {imax:.2f} A')
    return current_vecs, info


def _calibration_set_checks(current_vecs, fit_offset=True):
    """Identifiability of a calibration current set: rank of [I, 1] (M and
    offset), rank of the |B|^2 = I^T G I system (coil strengths incl. cross
    terms), and the number of distinct |I| patterns."""
    I = np.asarray(current_vecs, dtype=float).reshape(-1, 3)
    A_lin = np.column_stack([I, np.ones(len(I))]) if fit_offset else I
    A_quad = np.column_stack([I ** 2, 2 * I[:, 0] * I[:, 1], 2 * I[:, 0] * I[:, 2],
                              2 * I[:, 1] * I[:, 2]])
    patterns = {tuple(np.round(np.abs(v), 3)) for v in I}
    return {'linear_rank': int(np.linalg.matrix_rank(A_lin)) if len(I) else 0,
            'linear_rank_needed': A_lin.shape[1],
            'strength_rank': int(np.linalg.matrix_rank(A_quad)) if len(I) else 0,
            'n_magnitude_patterns': len(patterns)}


def plan_calibration_set(freq_start, freq_stop, quantization_axes, coil_strengths=None,
                         n_freq_points=None, skip_first_points=0, span_fraction=0.8,
                         max_current=None, nominal_axes=None, M_prior=None, D=2.87e9, E=0.0,
                         n_points=8, linewidth_hwhm=None, min_gap_hwhm=2.0,
                         reference_folder='FittedCoilODMRs',
                         span_levels=(1.0, 0.85, 0.7, 0.55, 0.4), min_magnitude_patterns=3,
                         fit_offset=True, robustness_deg=5.0, robustness_frac=0.05,
                         verbose=True):
    """
    A set of coil current triplets that together FULLY constrain the coil
    calibration (M, B_offset and the individual coil strengths, via
    `nv_field_fitting.fit_coil_matrix_from_measurements`), while keeping
    every spectrum's dips as well split as possible.

    Why not just the triplets of one `plan_spread_currents` call: those
    are symmetry copies of ONE field, so they share very few patterns of
    current MAGNITUDES -- which cannot separate the three coil strengths,
    would hide any per-coil nonlinearity, and give identical spectra
    that make the fit fragile. Here the robust spread optimum is computed
    for several smaller sub-windows too (`span_levels` x `span_fraction`;
    weaker fields -> different current magnitudes), all symmetry copies
    and sign flips within `max_current` form a candidate pool, and points
    are chosen greedily:
      1. candidates whose worst-case closest dip pair (field off by
         `robustness_deg` / `robustness_frac`) is at least `min_gap_hwhm`
         dip half-widths apart are eligible (2 = lines a full linewidth
         apart, just resolved);
      2. first, points that raise what the set can determine: rank of
         [I, 1] (M and offset), rank of the |B|^2 = I^T G I system (coil
         strengths incl. cross terms, needs 6) and the number of distinct
         |I| patterns (>= `min_magnitude_patterns`);
      3. then, until `n_points`, the best-split candidates pointing in the
         most different current directions, preferring ones that add a
         new |I| pattern (more current magnitudes per coil -- also a
         check for nonlinearity).

    Parameters
    ----------
    freq_start, freq_stop : float
        Sweep range, Hz.
    quantization_axes : as required by nv_field_fitting.
    coil_strengths : dict, optional
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A}; or give `M_prior`.
    n_freq_points : int, optional
        Sweep points; if given, the window accounts for `skip_first_points`
        exactly and the frequency step is checked against the linewidth.
    skip_first_points : int, default 0
    span_fraction : float, default 0.8
        Largest fraction of the (usable) range the dips may span.
    max_current : float or dict, optional
    nominal_axes, M_prior, D, E :
        As in `plan_spread_currents`.
    n_points : int, default 8
        Size of the set (raised automatically if the constraints need more).
    linewidth_hwhm : float, optional
        Dip half-width, Hz. Default: median measured in `reference_folder`
        (see `plan_linearity_scans`), else 10 MHz.
    min_gap_hwhm : float, default 2.0
        Required worst-case closest-dip separation, in half-widths.
    reference_folder : str or None, default 'FittedCoilODMRs'
    span_levels : sequence of float
        Sub-window sizes tried, as fractions of `span_fraction`.
    min_magnitude_patterns : int, default 3
    fit_offset : bool, default True
        Whether B_offset will be fitted (needs rank 4 instead of 3).
    robustness_deg, robustness_frac :
        As in `plan_spread_currents`.
    verbose : bool, default True

    Returns
    -------
    current_vecs : list of (float, float, float)
        The set, A.
    info : dict
        'points': per point {'current_vec', 'field_gauss', 'dips' (Hz),
        'min_gap', 'worst_case_min_gap' (Hz), 'span_fraction'};
        'checks': see `_calibration_set_checks`; 'min_gap_required' (Hz);
        'linewidth_hwhm' (Hz); 'levels': per span level the field and gaps
        found; 'warnings'.

    Raises
    ------
    ValueError
        If no candidate satisfies the gap requirement, or the eligible
        candidates cannot reach full rank / enough magnitude patterns
        (the message says what to relax).
    """
    required = ('X', 'Y', 'Z')
    warnings_list = []
    nominal = _nominal_axes_dict(nominal_axes)
    M = _prior_matrix(coil_strengths, nominal, M_prior)

    # === Window and linewidth ===
    if n_freq_points is not None:
        lo, hi, _ = _usable_window(freq_start, freq_stop, n_freq_points, skip_first_points)
        step = abs(float(freq_stop) - float(freq_start)) / (int(n_freq_points) - 1)
    else:
        lo, hi = sorted((float(freq_start), float(freq_stop)))
        step = None
    if linewidth_hwhm is None:
        widths = (_reference_linewidths(reference_folder)
                  if reference_folder is not None and os.path.isdir(reference_folder)
                  else np.array([]))
        linewidth_hwhm = float(np.median(widths)) if widths.size else 10.0e6
        source = (f'median of {widths.size} reference dips' if widths.size
                  else 'default (no reference data)')
    else:
        source = 'given'
    min_gap_required = min_gap_hwhm * linewidth_hwhm
    if step is not None and step > linewidth_hwhm / 2:
        warnings_list.append(f'frequency step {step / 1e6:.2f} MHz is coarse for '
                             f'{linewidth_hwhm / 1e6:.1f} MHz half-width dips (use <= '
                             f'{linewidth_hwhm / 2e6:.2f} MHz).')

    # === Candidate pool: robust optimum per sub-window, all copies + flips ===
    candidates, levels = [], []
    for level in span_levels:
        frac = span_fraction * level
        try:
            with _quiet_unless_error():
                vecs, sinfo = plan_spread_currents(
                    lo, hi, quantization_axes, coil_strengths=coil_strengths,
                    span_fraction=frac, max_current=max_current, nominal_axes=nominal_axes,
                    M_prior=M_prior, D=D, E=E, robustness_deg=robustness_deg,
                    robustness_frac=robustness_frac, include_sign_flips=True, verbose=False)
        except ValueError as exc:
            levels.append({'span_fraction': frac, 'error': str(exc)})
            continue
        levels.append({'span_fraction': frac, 'field_gauss': sinfo['field_gauss'],
                       'min_gap': sinfo['min_gap'],
                       'worst_case_min_gap': sinfo['worst_case_min_gap'],
                       'n_triplets': len(vecs)})
        if sinfo['worst_case_min_gap'] < min_gap_required:
            continue
        for v in vecs:
            candidates.append({'current_vec': tuple(float(c) for c in v),
                               'field_gauss': sinfo['field_gauss'], 'dips': sinfo['dips'],
                               'min_gap': sinfo['min_gap'],
                               'worst_case_min_gap': sinfo['worst_case_min_gap'],
                               'span_fraction': frac})
    if not candidates:
        best = max((lv.get('worst_case_min_gap', 0) for lv in levels), default=0)
        raise ValueError(f'No field keeps the closest dips {min_gap_required / 1e6:.0f} MHz apart '
                         f'({min_gap_hwhm:g} x {linewidth_hwhm / 1e6:.1f} MHz HWHM); best worst-case '
                         f'gap found: {best / 1e6:.0f} MHz. Lower min_gap_hwhm or the linewidth '
                         f'(e.g. after reducing the microwave power), or widen the window.')

    # === Greedy selection ===
    limits = (np.array([float(max_current[a]) for a in required]) if isinstance(max_current, dict)
              else np.full(3, float(max_current)) if max_current is not None
              else np.max(np.abs([c['current_vec'] for c in candidates]), axis=0))
    best_gap = max(c['worst_case_min_gap'] for c in candidates)

    def _deficit(vecs):
        ch = _calibration_set_checks(vecs, fit_offset=fit_offset)
        return ((ch['linear_rank_needed'] - ch['linear_rank'])
                + (6 - ch['strength_rank'])
                + max(0, min_magnitude_patterns - ch['n_magnitude_patterns']))

    chosen = []
    pool = list(candidates)
    pool.sort(key=lambda c: -c['worst_case_min_gap'])
    chosen.append(pool.pop(0))
    while pool:
        vecs = [c['current_vec'] for c in chosen]
        deficit = _deficit(vecs)
        if deficit == 0 and len(chosen) >= n_points:
            break
        U = np.array(vecs) / limits
        U = U / np.linalg.norm(U, axis=1, keepdims=True)

        patterns = {tuple(np.round(np.abs(v), 3)) for v in vecs}

        def _score(c):
            u = np.asarray(c['current_vec']) / limits
            u = u / np.linalg.norm(u)
            min_angle = float(np.min(np.arccos(np.clip(U @ u, -1, 1))))
            gain = deficit - _deficit(vecs + [c['current_vec']])
            # Extra magnitude patterns (beyond the minimum) also help: they
            # sample each coil at more current magnitudes (nonlinearity check).
            new_pattern = tuple(np.round(np.abs(c['current_vec']), 3)) not in patterns
            return (gain, c['worst_case_min_gap'] / best_gap + min_angle / np.pi
                    + 0.5 * new_pattern)

        k = max(range(len(pool)), key=lambda i: _score(pool[i]))
        if deficit > 0 and _score(pool[k])[0] <= 0 and len(chosen) >= n_points:
            break
        chosen.append(pool.pop(k))

    current_vecs = [c['current_vec'] for c in chosen]
    checks = _calibration_set_checks(current_vecs, fit_offset=fit_offset)
    problems = []
    if checks['linear_rank'] < checks['linear_rank_needed']:
        problems.append(f"current vectors span rank {checks['linear_rank']} of "
                        f"{checks['linear_rank_needed']} (M{' + offset' if fit_offset else ''})")
    if checks['strength_rank'] < 6:
        problems.append(f"coil-strength system has rank {checks['strength_rank']} of 6")
    if checks['n_magnitude_patterns'] < min_magnitude_patterns:
        problems.append(f"only {checks['n_magnitude_patterns']} distinct |I| pattern(s)")
    if problems:
        raise ValueError('The well-resolved candidates cannot fully constrain the fit: '
                         + '; '.join(problems) + '. Relax min_gap_hwhm, add span_levels, or '
                         'raise max_current.')

    info = {'points': chosen, 'checks': checks, 'min_gap_required': min_gap_required,
            'linewidth_hwhm': linewidth_hwhm, 'levels': levels, 'warnings': warnings_list}

    if verbose:
        print(f'Window {lo / 1e9:.4f}-{hi / 1e9:.4f} GHz; dip HWHM {linewidth_hwhm / 1e6:.1f} MHz '
              f'({source}); required closest-dip gap {min_gap_required / 1e6:.0f} MHz '
              f'(worst case: field off by {robustness_deg:g} deg / {100 * robustness_frac:g}%).')
        print('Spread optimum per sub-window:')
        for lv in levels:
            if 'error' in lv:
                print(f"  span {lv['span_fraction']:.2f}: none ({lv['error'][:60]}...)")
            else:
                ok = lv['worst_case_min_gap'] >= min_gap_required
                print(f"  span {lv['span_fraction']:.2f}: |B| {lv['field_gauss']:5.0f} G, closest dips "
                      f"{lv['min_gap'] / 1e6:4.0f} MHz (worst case {lv['worst_case_min_gap'] / 1e6:4.0f}), "
                      f"{lv['n_triplets']} triplets{'' if ok else '  -- too close, not used'}")
        print(f"Selected {len(chosen)} points: [I,1] rank {checks['linear_rank']}/"
              f"{checks['linear_rank_needed']}, strength rank {checks['strength_rank']}/6, "
              f"{checks['n_magnitude_patterns']} magnitude patterns.")
        for c in chosen:
            I = c['current_vec']
            print(f"  ({I[0]:+6.3f}, {I[1]:+6.3f}, {I[2]:+6.3f}) A   |B| {c['field_gauss']:4.0f} G, "
                  f"closest dips {c['min_gap'] / 1e6:3.0f} MHz (worst {c['worst_case_min_gap'] / 1e6:3.0f})")
        for w in warnings_list:
            print(f'WARNING: {w}')
    return current_vecs, info


def _auto_fit_dips(sweep, M, B_offset, axes_n, D=2.87e9, E=0.0, n_dips=None):
    """
    Automatic dip fit of every point in `sweep` (in place, `'dip_fit'`):
    fit as many Lorentzians as the model M @ I + B_offset predicts inside
    the scan window (or `n_dips`), then remove spurious ones
    (`_clean_auto_dip_fit`). Returns the common (freq_lo, freq_hi) window.
    """
    freq_lo = min(float(np.min(p['freq'])) for p in sweep)
    freq_hi = max(float(np.max(p['freq'])) for p in sweep)
    for p in sweep:
        B_pred = M @ np.asarray(p['current_vec'], dtype=float) + B_offset
        n_expected = sum(1 for a in axes_n
                         for f in nvfit.nv_transition_frequencies(a, B_pred, D=D, E=E)
                         if freq_lo <= f <= freq_hi)
        raw_fit = nvfit.fit_multi_lorentzian_dips(
            p['freq'], p['signal'], n_dips=n_dips if n_dips is not None else n_expected)
        p['dip_fit'] = _clean_auto_dip_fit(raw_fit)
        if not raw_fit['success']:
            print(f"WARNING: automatic dip fit failed for {_point_label(p)}")
    return freq_lo, freq_hi


def fit_coil_calibration_auto(mixed_sweep, coil_strengths, quantization_axes,
                              nominal_axes=None, D=2.87e9, E=0.0, n_dips=None,
                              fit_offset=True, max_angle_deg=30.0, max_match_distance=5.0e6,
                              n_grid_directions=2000, M_prior=None, plot=True, ncols=3,
                              figsize_per_axes=(4.0, 3.0)):
    """
    Fully automatic analysis of mixed-current ODMR data: find the dips in
    every spectrum, then determine the full calibration matrix M
    (B = M @ I + B_offset) using only the rough per-coil strengths as a
    prior (`nv_field_fitting.fit_coil_matrix_from_mixed_dips`). No
    hardware access -- can be re-run on saved data.

    Dips are found with `nv_field_fitting.fit_multi_lorentzian_dips`,
    fitting `n_dips` dips per spectrum (default: as many as the prior
    predicts inside the scan window, i.e. 8), after which spurious ones
    (noise spikes / baseline wiggles fitted when fewer dips are resolved
    than requested) are removed (`_clean_auto_dip_fit`); a merged pair of
    lines then simply counts as one dip. This works because the points
    from `plan_mixed_currents` are chosen so that the dips are well
    resolved -- but it is NOT checked by a human, so always look at the
    plot (red = fitted dips, blue dashed = final model) before trusting
    M. If a spectrum is fitted badly, re-fit that point with
    `pick_dips_interactive` + `fit_sweep_dips` and pass the dips to
    `nv_field_fitting.fit_coil_matrix_from_mixed_dips` directly.

    Parameters
    ----------
    mixed_sweep : list of dict
        From `measure_mixed_currents_odmr`. Modified in place: each point
        gains `'dip_fit'` and `'field_fit'` (M @ I + B_offset).
    coil_strengths : dict or None
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A} rough field-per-amp of each coil.
        Ignored if `M_prior` is given.
    quantization_axes : as required by nv_field_fitting.
    nominal_axes : dict, optional
        Positive-current field direction of each coil; defaults to +x/+y/+z.
    D, E : float
    n_dips : int, optional
        Dips to fit per spectrum before cleanup; default: predicted
        in-window count.
    fit_offset : bool, default True
        Fit the background offset (needs >= 4 usable points; else 3).
    max_angle_deg, n_grid_directions, max_match_distance :
        Passed to `nv_field_fitting.fit_coil_matrix_from_mixed_dips`.
    M_prior : array_like, shape (3, 3), optional
        Full prior matrix (Hz/A) instead of `coil_strengths` -- e.g. the
        result of an earlier round.
    plot : bool, default True
    ncols, figsize_per_axes :
        Grid-plot layout, as in `plot_sweep_spectra`.

    Returns
    -------
    M : ndarray, shape (3, 3)
        Calibration matrix, Hz/A, columns ordered (X, Y, Z).
    B_offset : ndarray, shape (3,)
        Background offset, Hz.
    info : dict
        As returned by `fit_coil_matrix_from_mixed_dips`, plus 'M_prior',
        'axis_directions' ({axis: `compute_coil_axis_direction` result})
        and 'min_dip_separation' (list, Hz: smallest spacing of the dips
        predicted by the final M at each point -- small values mean that
        point had merged dips).
    """
    required = ('X', 'Y', 'Z')
    nominal_axes = _nominal_axes_dict(nominal_axes)
    M_prior = _prior_matrix(coil_strengths, nominal_axes, M_prior)
    axes_n = [nvfit._normalize(a) for a in quantization_axes]

    # === Automatic dip fits ===
    freq_lo, freq_hi = _auto_fit_dips(mixed_sweep, M_prior, np.zeros(3), axes_n, D=D, E=E,
                                      n_dips=n_dips)

    # === Field per point -> symmetry images -> linear M -> refinement ===
    current_vecs = np.array([p['current_vec'] for p in mixed_sweep], dtype=float)
    obs, errs, widths = _collect_dips(mixed_sweep)
    M, B_offset, info = nvfit.fit_coil_matrix_from_mixed_dips(
        quantization_axes, current_vecs, obs, M_prior, dip_errors_list=errs,
        dip_widths_list=widths, D=D, E=E, freq_range=(freq_lo, freq_hi),
        fit_offset=fit_offset, max_angle_deg=max_angle_deg,
        n_grid_directions=n_grid_directions, max_match_distance=max_match_distance)

    for p in mixed_sweep:
        p['field_fit'] = M @ np.asarray(p['current_vec'], dtype=float) + B_offset
    min_seps = [nvfit._min_dip_separation(axes_n, p['field_fit'], D=D, E=E)
                for p in mixed_sweep]

    # === Report ===
    print('Per-point dips and field fits (field fit = RMS of its dips, any symmetry image):')
    for k, p in enumerate(mixed_sweep):
        rms = info['point_field_rms'][k]
        status = (f'{rms / 1e3:.1f} kHz' if np.isfinite(rms) else 'UNUSABLE (excluded)')
        rejected = p['dip_fit'].get('n_rejected', 0)
        print(f"  {_point_label(p)}: {p['dip_fit']['n_dips']} dips"
              f"{f' ({rejected} spurious removed)' if rejected else ''}, "
              f"closest predicted pair {min_seps[k] / 1e6:.1f} MHz apart, field fit {status}")
    print(f"Linear model over chosen fields: RMS {info['linear_rms'] / 1e3:.1f} kHz; "
          + (f"best clearly different M {info['runner_up_linear_rms'] / 1e3:.1f} kHz "
             f"(margin {info['margin']:.1f}x)" if np.isfinite(info['runner_up_linear_rms'])
             else 'no other candidate combination') + '.')
    if info['margin'] < 3.0:
        print('  WARNING: weak margin -- the symmetry images were not picked clearly. '
              'Check the dip fits in the plot (a wrong dip makes a point\'s field '
              'wrong) and the coil_strengths prior.')
    refine_info = info['refine_info']
    if refine_info is not None:
        print(f"Joint fit of M {'converged' if refine_info['converged'] else 'DID NOT CONVERGE'} "
              f"after {refine_info['n_iterations']} iteration(s); "
              f"{refine_info['n_matches_total']} matched dips; RMS "
              f"{refine_info['rms_residual'] / 1e3:.1f} kHz (matched), "
              f"{refine_info['rms_residual_all_dips'] / 1e3:.1f} kHz (all dips).")
        if refine_info['underdetermined']:
            print('  WARNING: joint fit is underdetermined -- result is not reliable.')

    directions = {a: compute_coil_axis_direction(M[:, j], a, nominal_axes=nominal_axes)
                  for j, a in enumerate(required)}
    print(f'Coil calibration matrix M (Hz/A):\n{M}')
    print(f'M (Gauss/A):\n{nvfit.field_freq_to_gauss(M)}')
    for j, a in enumerate(required):
        d = directions[a]
        ratio = d['magnitude'] / np.linalg.norm(M_prior[:, j])
        print(f"  {a} coil: {d['magnitude'] / 1e6:.3f} MHz/A "
              f"({nvfit.field_freq_to_gauss(d['magnitude']):.4f} G/A; {ratio:.3f}x prior), "
              f"direction {np.round(d['direction'], 4)}, tilt from nominal "
              f"{d['angle_deg']:.2f} deg")
        if d['angle_deg'] > 25.0 or not 0.7 < ratio < 1.4:
            print(f'    WARNING: far from the prior for coil {a} -- check the dip fits '
                  f'and coil_strengths/nominal_axes.')
    print(f'B_offset (Hz): {B_offset}  ({nvfit.field_freq_to_gauss(B_offset)} G)')

    if plot:
        predicted_lines = []
        for p in mixed_sweep:
            predicted_dicts = nvfit.predict_odmr_dips(
                quantization_axes, p['field_fit'], D=D, E=E, freq_range=(freq_lo, freq_hi))
            predicted_lines.append([d['freq'] for d in predicted_dicts])
        _plot_sweep_with_dip_fits(mixed_sweep, ncols=ncols, figsize_per_axes=figsize_per_axes,
                                  predicted_lines=predicted_lines)

    info['M_prior'] = M_prior
    info['axis_directions'] = directions
    info['min_dip_separation'] = min_seps
    return M, B_offset, info


def auto_calibrate_coils(coil_strengths, quantization_axes, seq_name, target_field_gauss=20.0,
                         max_current=None, n_points=6, current_vecs=None, num_sweeps=10000,
                         settle_time=0.0, nominal_axes=None, D=2.87e9, E=0.0,
                         replan_min_separation=5.0e6, max_rounds=2, plot=True,
                         skip_first_points=0, **fit_kwargs):
    """
    Automated step 2 of the quick calibration: plan the mixed-current
    points, measure them, and fit the full calibration matrix M -- no
    user input beyond the rough per-coil strengths.

    Quick-calibration workflow:
      1. For each coil, take ONE single-coil ODMR spectrum at a fairly
         high current, mark its dips (e.g.
         `nv_field_fitting.fit_odmr_dips_interactive`, with the same
         `skip_first_points`), and convert them to a rough strength with
         `nv_field_fitting.estimate_coil_strength_from_dips`.
      2. Call this function with those strengths. It runs
         `plan_mixed_currents` (unless `current_vecs` is given),
         `measure_mixed_currents_odmr`, and `fit_coil_calibration_auto`.

    Adaptive second round: the planned currents assume untilted coils, so
    with large tilts (or a poor prior) the actual fields can land where
    two dips merge. The round-1 fit usually still succeeds (merged pairs
    are handled), and its M is accurate to well under a degree. If, by
    that M, any measured point had a dip pair closer than
    `replan_min_separation`, another `n_points` points are planned with
    the fitted M (away from the directions already measured), measured,
    and everything is re-fitted together -- up to `max_rounds` rounds.

    Assumes the ODMR PulseSequence named `seq_name` has ALREADY been
    generated, sampled, and loaded, and that its scan window contains all
    dips at |B| = target_field_gauss (check the planned fields printed
    first; 20 G spans roughly D +/- 60 MHz).

    Parameters
    ----------
    coil_strengths : dict
        {'X': Hz/A, 'Y': Hz/A, 'Z': Hz/A} rough field-per-amp of each coil.
    quantization_axes : as required by nv_field_fitting.
    seq_name : str
        Name of the already-generated/sampled/loaded ODMR PulseSequence.
    target_field_gauss, max_current, n_points :
        Passed to `plan_mixed_currents`.
    current_vecs : sequence of (float, float, float), optional
        Explicit (I_X, I_Y, I_Z) points for round 1 instead of planning.
    num_sweeps, settle_time :
        Passed to `measure_mixed_currents_odmr`.
    nominal_axes : dict, optional
        Positive-current field direction of each coil; defaults to +x/+y/+z.
    D, E : float
    replan_min_separation : float, default 5e6
        Hz; trigger for another round (see above). None disables it.
    max_rounds : int, default 2
    plot : bool, default True
        Plot the final fit (intermediate rounds are not plotted).
    skip_first_points : int, default 0
        Drop the first n points of every measured spectrum, as in
        `measure_mixed_currents_odmr`.
    **fit_kwargs :
        Passed to `fit_coil_calibration_auto` (n_dips, fit_offset,
        max_angle_deg, max_match_distance, n_grid_directions, ncols,
        figsize_per_axes).

    Returns
    -------
    M : ndarray, shape (3, 3)
    B_offset : ndarray, shape (3,)
    info : dict
        As returned by `fit_coil_calibration_auto` (for the final fit),
        plus 'n_rounds'.
    mixed_sweep : list of dict
        All measured points of all rounds (with 'dip_fit' and
        'field_fit'), e.g. for saving or re-analysis with
        `fit_coil_calibration_auto`.
    """
    if current_vecs is None:
        current_vecs, _ = plan_mixed_currents(
            coil_strengths, quantization_axes, target_field_gauss=target_field_gauss,
            max_current=max_current, n_points=n_points, nominal_axes=nominal_axes, D=D, E=E)

    mixed_sweep = []
    M_prior = None
    n_rounds = 0
    while True:
        n_rounds += 1
        mixed_sweep += measure_mixed_currents_odmr(current_vecs, seq_name,
                                                   num_sweeps=num_sweeps,
                                                   settle_time=settle_time,
                                                   skip_first_points=skip_first_points)
        print(f'--- Analysis after round {n_rounds} ({len(mixed_sweep)} point(s)) ---')
        M, B_offset, info = fit_coil_calibration_auto(
            mixed_sweep, coil_strengths, quantization_axes, nominal_axes=nominal_axes,
            D=D, E=E, M_prior=M_prior, plot=False, **fit_kwargs)

        poorly_resolved = [k for k, s in enumerate(info['min_dip_separation'])
                           if replan_min_separation is not None and s < replan_min_separation]
        if not poorly_resolved or n_rounds >= max_rounds:
            if poorly_resolved:
                print(f'NOTE: {len(poorly_resolved)} point(s) still have dips closer than '
                      f'{replan_min_separation / 1e6:.1f} MHz after {n_rounds} round(s); the '
                      f'merged dips are handled, but check the plot.')
            break

        print(f'{len(poorly_resolved)} point(s) had dips closer than '
              f'{replan_min_separation / 1e6:.1f} MHz (the planned fields were off by the '
              f'coil tilts / prior error) -- planning round {n_rounds + 1} with the fitted M.')
        M_prior = M
        measured_dirs = [p['field_fit'] / np.linalg.norm(p['field_fit']) for p in mixed_sweep]
        current_vecs, _ = plan_mixed_currents(
            None, quantization_axes, target_field_gauss=target_field_gauss,
            max_current=max_current, n_points=n_points, nominal_axes=nominal_axes, D=D, E=E,
            M_prior=M, exclude_directions=measured_dirs)

    if plot:
        # Re-run the (fast) final analysis once more just to draw it.
        M, B_offset, info = fit_coil_calibration_auto(
            mixed_sweep, coil_strengths, quantization_axes, nominal_axes=nominal_axes,
            D=D, E=E, M_prior=M_prior, plot=True, **fit_kwargs)

    info['n_rounds'] = n_rounds
    return M, B_offset, info, mixed_sweep


# =============================================================================
# Complete automatic calibration (strengths -> M -> validation)
# =============================================================================

def _usable_window(freq_start, freq_stop, n_freq_points, skip_first_points):
    """(lo, hi, axis) of the sweep's frequency axis that survives skipping
    the first `skip_first_points` points in measurement order."""
    axis = np.linspace(float(freq_start), float(freq_stop), int(n_freq_points))
    kept_axis, _ = nvfit.skip_first_odmr_points(axis, np.zeros_like(axis), skip_first_points)
    return float(np.min(kept_axis)), float(np.max(kept_axis)), kept_axis


def _choose_validation_currents(M, B_offset, quantization_axes, B_mag, max_current,
                                measured_dirs, n_points, D=2.87e9, E=0.0,
                                min_angle_deg=15.0, n_grid=2000):
    """
    Current vectors for validation: field directions (by the fitted M)
    with well-resolved dips, at least `min_angle_deg` from every measured
    calibration field direction, mutually spread out (directions are
    compared up to sign, since +B and -B give identical spectra).
    """
    axes_n = [nvfit._normalize(a) for a in quantization_axes]
    k = np.arange(n_grid) + 0.5
    polar = np.arccos(1.0 - 2.0 * k / n_grid)
    azimuth = np.pi * (1.0 + 5.0 ** 0.5) * k
    dirs = np.column_stack([np.cos(azimuth) * np.sin(polar),
                            np.sin(azimuth) * np.sin(polar), np.cos(polar)])
    f = nvfit._batch_transitions(axes_n, B_mag * dirs, D=D, E=E)
    seps = np.min(np.diff(np.sort(f, axis=1), axis=1), axis=1)

    # +d and -d give identical spectra, so compare directions up to sign
    measured = np.array([nvfit._normalize(d) for d in measured_dirs])
    min_cos = np.cos(np.radians(min_angle_deg))
    far = np.all(np.abs(dirs @ measured.T) < min_cos, axis=1)
    if not far.any():
        far = np.ones(len(dirs), dtype=bool)
    good = far & (seps >= 0.5 * seps[far].max())
    pool = dirs[good]
    order = np.argsort(-seps[good])

    chosen = [pool[order[0]]]
    while len(chosen) < n_points and len(chosen) < len(pool):
        min_angle = np.min(np.arccos(np.clip(np.abs(pool @ np.array(chosen).T), 0, 1)), axis=1)
        chosen.append(pool[int(np.argmax(min_angle))])

    current_vecs = []
    for d in chosen:
        I = np.linalg.solve(M, B_mag * d - B_offset)
        scale = min(1.0, float(np.min(max_current / np.maximum(np.abs(I), 1e-12))))
        current_vecs.append(tuple(float(c) for c in I * scale))
    return current_vecs


def full_auto_calibrate_coils(quantization_axes, max_current, seq_name, freq_start, freq_stop,
                              n_freq_points, skip_first_points=0, num_sweeps=10000,
                              strength_num_sweeps=None, settle_time=0.0, nominal_axes=None,
                              D=2.87e9, E=0.0, fill_fraction=0.8, coil_strength_guess=None,
                              initial_current_fraction=0.1, n_points=6, n_validation=3,
                              validation_tol=0.3e6, replan_min_separation=5.0e6, max_rounds=2,
                              plot=True, **fit_kwargs):
    """
    Complete, unattended coil calibration: rough per-coil strengths,
    full calibration matrix M (B = M @ I + B_offset), and a validation
    against fresh ODMR sweeps -- no dip picking by hand.

    Steps
    -----
    1. USABLE WINDOW: the sweep's frequency axis is taken as
       linspace(freq_start, freq_stop, n_freq_points); its first
       `skip_first_points` points (in measurement order, i.e. starting at
       `freq_start`) are dropped, as they will be by every measurement.
       The remaining window, minus a margin of 3 linewidths, sets how far
       from D the dips may go. D must lie inside it. After the first
       measurement the actual frequency axis is checked against this.
    2. ROUGH STRENGTHS (per coil, other coils at 0 A): measure at
       `initial_current_fraction` x max current (or, if
       `coil_strength_guess` is given, directly at the current that
       should fill the window), estimate the strength from the RAW
       spectrum (`nv_field_fitting.estimate_coil_strength_from_spectrum`,
       template matching, no dip finding), adapting the current if no
       dips are seen (field pushes them out of the window -> lower) or
       they are still unresolved (-> higher). Then measure once more at
       the current that puts the outermost dips at `fill_fraction` of
       the usable half-window (capped at max_current) and re-estimate:
       the larger splitting makes this estimate more precise.
    3. MATRIX: `auto_calibrate_coils` with `target_field_gauss` chosen so
       the outermost dips of the planned points also sit at
       `fill_fraction` of the usable half-window -- i.e. using most of the
       frequency range, for the best resolution.
    4. VALIDATION: `n_validation` new points along different (still
       well-resolved) field directions, at least 15 deg from every
       calibration direction, are measured and their automatically fitted
       dips compared with those predicted by M. If any point's RMS
       deviation exceeds `validation_tol`, a warning is printed and
       info['validated'] is False.

    Assumes the ODMR PulseSequence `seq_name` is ALREADY generated,
    sampled and loaded, with the frequency axis described by
    freq_start / freq_stop / n_freq_points.

    Parameters
    ----------
    quantization_axes : as required by nv_field_fitting.
    max_current : float or dict
        Largest current amplitude allowed, A -- for all coils or
        {'X': ..., 'Y': ..., 'Z': ...}. Currents are used with both signs.
    seq_name : str
        Name of the already-generated/sampled/loaded ODMR PulseSequence.
    freq_start, freq_stop : float
        First and last frequency of the sequence's sweep, Hz.
    n_freq_points : int
        Number of frequency points in the sequence.
    skip_first_points : int, default 0
        Leading points dropped from every spectrum (see
        `measure_mixed_currents_odmr`).
    num_sweeps : int, default 10000
        Sweeps per ODMR measurement (calibration and validation points).
    strength_num_sweeps : int, optional
        Sweeps per measurement in step 2; defaults to `num_sweeps`.
    settle_time : float, default 0.0
        Wait after every current change, s. Set this to your supplies'
        settling time -- consecutive points swing coils through zero.
    nominal_axes : dict, optional
        Positive-current field direction of each coil; defaults to +x/+y/+z.
    D, E : float
    fill_fraction : float, default 0.8
        Outermost dips are placed at this fraction of the usable
        half-window (after the margin).
    coil_strength_guess : dict, optional
        {'X': Hz/A, ...} rough strengths, if known; skips the low-current
        bootstrap measurement.
    initial_current_fraction : float, default 0.1
        Fraction of max_current for the first step-2 measurement per coil.
    n_points : int, default 6
        Mixed calibration points per round (see `auto_calibrate_coils`).
    n_validation : int, default 3
        Validation points.
    validation_tol : float, default 0.3e6
        Maximum acceptable RMS deviation (Hz) between measured and
        predicted dips at any validation point.
    replan_min_separation, max_rounds :
        Passed to `auto_calibrate_coils`.
    plot : bool, default True
        Plot the calibration fit and the validation spectra (with the
        dips predicted by M as blue dashed lines).
    **fit_kwargs :
        Passed to `fit_coil_calibration_auto` (via `auto_calibrate_coils`).

    Returns
    -------
    M : ndarray, shape (3, 3)
        Calibration matrix, Hz/A, columns ordered (X, Y, Z).
    B_offset : ndarray, shape (3,)
        Background offset, Hz.
    info : dict
        'validated'          : bool -- all validation points within tolerance.
        'validation_rms'     : list of float, Hz, per validation point.
        'warnings'           : list of str -- every warning raised.
        'coil_strengths'     : {axis: Hz/A} from step 2.
        'strength_measurements' : {axis: list of dicts (current, strength,
                               estimate info)} for every step-2 measurement.
        'usable_window'      : (lo, hi), Hz, after skipping points.
        'target_field_gauss' : float, |B| used for the calibration points.
        'calibration_info'   : info dict of `auto_calibrate_coils`.
        'calibration_sweep', 'validation_sweep', 'strength_sweeps' :
                               all measured data, for saving/re-analysis.
    """
    required = ('X', 'Y', 'Z')
    nominal_axes = _nominal_axes_dict(nominal_axes)
    axes_n = [nvfit._normalize(a) for a in quantization_axes]
    if isinstance(max_current, dict):
        I_max = np.array([float(max_current[a]) for a in required])
    else:
        I_max = np.full(3, float(max_current))
    max_current_dict = dict(zip(required, I_max))
    if strength_num_sweeps is None:
        strength_num_sweeps = num_sweeps
    warnings_list = []

    def _warn(msg):
        warnings_list.append(msg)
        print(f'WARNING: {msg}')

    # === Step 1: usable frequency window ===
    lo, hi, kept_axis = _usable_window(freq_start, freq_stop, n_freq_points, skip_first_points)
    if not lo < D < hi:
        raise ValueError(f'D = {D / 1e9:.4f} GHz is outside the usable sweep window '
                         f'{lo / 1e9:.4f}-{hi / 1e9:.4f} GHz (after skipping '
                         f'{skip_first_points} point(s)).')
    hwhm = 2.0e6  # until measured
    half_window = lambda: min(D - lo, hi - D) - 3.0 * hwhm
    print(f'Usable sweep window: {lo / 1e9:.4f}-{hi / 1e9:.4f} GHz '
          f'({len(kept_axis)} of {n_freq_points} points; first {skip_first_points} skipped), '
          f'i.e. up to {min(D - lo, hi - D) / 1e6:.1f} MHz from D on both sides.')
    axis_checked = [False]

    def _measure(current_vecs, sweeps):
        nonlocal lo, hi
        with _quiet_unless_error():
            pts = measure_mixed_currents_odmr(current_vecs, seq_name, num_sweeps=sweeps,
                                              settle_time=settle_time,
                                              skip_first_points=skip_first_points)
        if not axis_checked[0]:
            axis_checked[0] = True
            f = pts[0]['freq']
            if len(f) != len(kept_axis) or not np.allclose(f, kept_axis, rtol=0,
                                                         atol=0.5 * abs(kept_axis[1] - kept_axis[0])):
                _warn(f'measured frequency axis ({len(f)} points, {f[0] / 1e9:.4f}-'
                      f'{f[-1] / 1e9:.4f} GHz) does not match freq_start/freq_stop/'
                      f'n_freq_points after skipping ({len(kept_axis)} points, '
                      f'{kept_axis[0] / 1e9:.4f}-{kept_axis[-1] / 1e9:.4f} GHz); '
                      f'using the measured window.')
                lo, hi = float(np.min(f)), float(np.max(f))
        return pts

    # === Step 2: rough strength of each coil ===
    print('=== Step 2: rough coil strengths ===')
    strengths, strength_meas, strength_sweeps, linewidths = {}, {}, {}, []
    for j, a in enumerate(required):
        n_hat = nominal_axes[a]
        strength_meas[a], strength_sweeps[a] = [], []
        if coil_strength_guess is not None:
            b_t = nvfit.field_magnitude_for_outer_offset(axes_n, n_hat,
                                                         fill_fraction * half_window(), D=D, E=E)
            I = min(I_max[j], b_t / abs(coil_strength_guess[a]))
        else:
            I = initial_current_fraction * I_max[j]

        accepted = None
        for attempt in range(6):
            vec = tuple(I * np.eye(3)[j])
            pt = _measure([vec], strength_num_sweeps)[0]
            strength_sweeps[a].append(pt)
            s, est = nvfit.estimate_coil_strength_from_spectrum(
                axes_n, pt['freq'], pt['signal'], I, n_hat, D=D, E=E)
            strength_meas[a].append({'current': I, 'strength': s, **est})
            if not est['has_dips']:
                print(f'  {a} at {I:.3f} A: no dips in the window -> lowering the current')
                I /= 4.0
                continue
            if est['outer_offset'] > min(D - lo, hi - D):
                print(f'  {a} at {I:.3f} A: dips would extend past the window -> lowering')
                I *= 0.5 * min(D - lo, hi - D) / est['outer_offset']
                continue
            if est['outer_offset'] < 4.0 * est['linewidth'] and I < 0.999 * I_max[j]:
                print(f'  {a} at {I:.3f} A: splitting not yet resolved -> raising the current')
                I = min(I_max[j], 4.0 * I)
                continue
            if accepted is None:
                accepted = (s, est)
                linewidths.append(est['linewidth'])
                hwhm = float(np.median(linewidths))
                # one more measurement near the window edge, for precision
                b_t = nvfit.field_magnitude_for_outer_offset(
                    axes_n, n_hat, fill_fraction * half_window(), D=D, E=E)
                I_new = min(I_max[j], b_t / s)
                if abs(I_new - I) <= 0.2 * I:
                    break
                I = I_new
                continue
            accepted = (s, est)
            break
        if accepted is None:
            raise RuntimeError(f'Could not estimate the strength of coil {a}: no usable '
                               f'spectrum after {len(strength_meas[a])} attempt(s) (see '
                               f'info). Check the coil, its current range and the window.')
        strengths[a] = accepted[0]
        last = strength_meas[a][-1]
        print(f"  {a}: {strengths[a] / 1e6:.3f} MHz/A "
              f"({nvfit.field_freq_to_gauss(strengths[a]):.4f} G/A) from the spectrum at "
              f"{last['current']:.3f} A (template score {last['score']:.2f})")

    # === Step 3: full matrix from mixed points using most of the window ===
    target_offset = fill_fraction * half_window()
    if target_offset <= 0:
        raise ValueError('The usable window is narrower than the dip linewidth margin.')
    B_mag = target_offset
    for _ in range(2):  # best direction depends weakly on |B|
        dirs, _ = nvfit.most_resolved_field_directions(axes_n, B_mag, n_directions=1, D=D, E=E)
        B_mag = nvfit.field_magnitude_for_outer_offset(axes_n, dirs[0], target_offset, D=D, E=E)
    target_gauss = float(nvfit.field_freq_to_gauss(B_mag))
    print(f'=== Step 3: calibration matrix (|B| = {target_gauss:.1f} G, outermost dips '
          f'~{target_offset / 1e6:.0f} MHz from D) ===')
    M, B_offset, cal_info, cal_sweep = auto_calibrate_coils(
        strengths, quantization_axes, seq_name, target_field_gauss=target_gauss,
        max_current=max_current_dict, n_points=n_points, num_sweeps=num_sweeps,
        settle_time=settle_time, nominal_axes=nominal_axes, D=D, E=E,
        replan_min_separation=replan_min_separation, max_rounds=max_rounds, plot=plot,
        skip_first_points=skip_first_points, **fit_kwargs)
    achieved = [nvfit.field_freq_to_gauss(np.linalg.norm(p['field_fit'])) for p in cal_sweep]
    if min(achieved) < 0.9 * target_gauss:
        print(f'  NOTE: max_current limited the calibration fields to '
              f'{min(achieved):.1f}-{max(achieved):.1f} G (target {target_gauss:.1f} G), so '
              f'not all of the window is used; dips are still well resolved if the closest '
              f'pairs above are several MHz apart.')
    if cal_info['margin'] < 3.0:
        print(f"  NOTE: modest symmetry-image margin in the calibration fit "
              f"({cal_info['margin']:.1f}x); the validation below is the decisive check.")
    if cal_info['refine_info'] is not None and not cal_info['refine_info']['converged']:
        _warn('the joint fit of M did not converge.')

    # === Step 4: validation against new sweeps ===
    print(f'=== Step 4: validation ({n_validation} new point(s)) ===')
    measured_dirs = [p['field_fit'] for p in cal_sweep]
    val_vecs = _choose_validation_currents(M, B_offset, axes_n, B_mag, I_max, measured_dirs,
                                           n_validation, D=D, E=E)
    val_sweep = _measure(val_vecs, num_sweeps)
    _auto_fit_dips(val_sweep, M, B_offset, axes_n, D=D, E=E)
    obs, _, widths = _collect_dips(val_sweep)
    val_rms = []
    for p, o, w in zip(val_sweep, obs, widths):
        p['field_fit'] = M @ np.asarray(p['current_vec'], dtype=float) + B_offset
        rms = nvfit._all_dip_rms(axes_n, [p['field_fit']], [o], D=D, E=E,
                                 freq_range=(lo, hi), dip_widths_list=[w])
        val_rms.append(rms)
        n_pred = sum(1 for a in axes_n
                     for f in nvfit.nv_transition_frequencies(a, p['field_fit'], D=D, E=E)
                     if lo <= f <= hi)
        ok = np.isfinite(rms) and rms <= validation_tol
        print(f"  {_point_label(p)}: {len(o)} dips found ({n_pred} predicted), RMS deviation "
              f"from prediction {rms / 1e3:.1f} kHz {'OK' if ok else 'TOO LARGE'}")
    validated = all(np.isfinite(r) and r <= validation_tol for r in val_rms)
    if validated:
        print(f'Validation PASSED: all points within {validation_tol / 1e3:.0f} kHz RMS.')
    else:
        _warn(f'validation FAILED: worst point deviates by '
              f'{np.nanmax(val_rms) / 1e3:.0f} kHz RMS (tolerance '
              f'{validation_tol / 1e3:.0f} kHz). Do not trust M without checking the '
              f'calibration and validation plots.')

    if plot:
        predicted_lines = [[d['freq'] for d in nvfit.predict_odmr_dips(
            quantization_axes, p['field_fit'], D=D, E=E, freq_range=(lo, hi))]
            for p in val_sweep]
        _plot_sweep_with_dip_fits(val_sweep, predicted_lines=predicted_lines)

    info = {
        'validated': validated,
        'validation_rms': val_rms,
        'warnings': warnings_list,
        'coil_strengths': strengths,
        'strength_measurements': strength_meas,
        'usable_window': (lo, hi),
        'target_field_gauss': target_gauss,
        'calibration_info': cal_info,
        'calibration_sweep': cal_sweep,
        'validation_sweep': val_sweep,
        'strength_sweeps': strength_sweeps,
    }
    return M, B_offset, info


class _quiet_unless_error:
    """Context manager silencing the per-point acquisition printout of
    measure_mixed_currents_odmr (replayed if an exception occurs)."""

    def __enter__(self):
        import contextlib
        import io
        self._buf = io.StringIO()
        self._cm = contextlib.redirect_stdout(self._buf)
        self._cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._cm.__exit__(exc_type, exc, tb)
        if exc_type is not None:
            print(self._buf.getvalue())
        return False
