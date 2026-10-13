# -*- coding: utf-8 -*-
"""
Coil superposition / cross-coil run: mixed-current points in many directions
(coil pairs, octants at two scales, negated octants, random directions), the
2026-10-02 currents again, and a drift reference point every ~8 runs -- all
chosen so that every spectrum has resolved dips and no NV axis nearly
perpendicular to the field (see coil_linearity_analysis.plan_superposition_set).

Same machinery as run_coil_linearity.py: z re-focus before every ODMR run,
rate-scaled sweep counts, every point saved immediately as
CoilSuperposition/odmr_point_<n>.npz, coils back to 0 A at the end / on error
/ on interrupt.

Run from the Jupyter kernel connected to qudi:

    %cd C:\\Software\\qudi-iqo-modules
    import importlib, coil_characterization, coil_linearity_analysis, tracking
    for m in (coil_characterization, coil_linearity_analysis, tracking): importlib.reload(m)
    %run -i run_coil_superposition.py

Assumes the 1001-point ODMR sequence (no alternating) is generated, sampled
and loaded as SEQ_NAME, and that FREQ_START / FREQ_STOP / N_FREQ_POINTS match.
"""
import numpy as np

import coil_characterization as coilchar
import coil_linearity_analysis as cla
import tracking

# =============================================================================
# Settings -- check these before running
# =============================================================================
SEQ_NAME = 'cw_odmr_compact'
FREQ_START = 2.4e9          # Hz, as programmed in the sequence
FREQ_STOP = 3.4e9           # Hz
N_FREQ_POINTS = 1001
SKIP_FIRST_POINTS = 15      # stored with the data; files keep all points

NUM_SWEEPS = 30000          # sweeps per ODMR run -- at REFERENCE_GATED_RATE, if set
REFERENCE_GATED_RATE = 3.5e6  # Hz (sum_rate_gated_hz) at which NUM_SWEEPS applies. None: plain sweeps
RATE_INTERVAL = 5.0         # s between gated-rate readings
MIN_SWEEPS = 10000          # with REFERENCE_GATED_RATE: never fewer actual sweeps than this per run
SETTLE_TIME = 5.0           # s after each current change (counts towards WAIT_BETWEEN)
WAIT_BETWEEN = 0.0          # s from the end of one ODMR run to the next z re-focus + run
FOLDER = 'CoilSuperposition'

QUANTIZATION_AXES = [(np.sqrt(2 / 3), 0.0, np.sqrt(1 / 3)),
                     (-np.sqrt(2 / 3), 0.0, np.sqrt(1 / 3)),
                     (0.0, np.sqrt(2 / 3), -np.sqrt(1 / 3)),
                     (0.0, -np.sqrt(2 / 3), -np.sqrt(1 / 3))]
COIL_STRENGTHS = {'X': 55e6, 'Y': 110e6, 'Z': 110e6}    # Hz/A, only stored (plan is fixed below)
MAX_CURRENT = {'X': 4.0, 'Y': 3.0, 'Z': 3.0}            # A. Z 3.27 adds the 3 remaining Oct 2 points
PLAN_SEED = 0               # order / random directions of the plan

# z re-focus ('brightest' point of a full scan of the z window, 5-point moving median)
Z_AXIS = 'z'
Z_CHANNEL = 'Sum'
Z_MIN, Z_MAX = 0.0, 6.2     # scanner units: z window scanned at every re-focus (clipped to the scanner)
Z_RESOLUTION = 100
Z_FREQUENCY = 30.0

DRY_RUN = False              # True: only print the plan

# =============================================================================
# qudi objects into the modules' namespaces
# =============================================================================
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic
tracking.scanning_probe_logic = scanning_probe_logic
tracking.scanning_optimize_logic = scanning_optimize_logic
tracking.pulsed_master_logic = pulsed_master_logic

# =============================================================================
# Plan
# =============================================================================
nv_axes = cla.NVAxes(QUANTIZATION_AXES)
freq_step_MHz = (FREQ_STOP - FREQ_START) / 1e6 / (N_FREQ_POINTS - 1)
dip_window = (FREQ_START / 1e6 + SKIP_FIRST_POINTS * freq_step_MHz + 25.0,   # 25 MHz margin
              FREQ_STOP / 1e6 - 25.0)
plan = cla.plan_superposition_set(nv_axes, limits=MAX_CURRENT, seed=PLAN_SEED, window=dip_window)
for p in plan:
    if any(abs(v) > MAX_CURRENT[c] + 1e-9 for v, c in zip(p['set_current_vec'], 'XYZ')):
        raise ValueError(f'planned current {p["set_current_vec"]} exceeds MAX_CURRENT')

# =============================================================================
# z tracker: window [Z_MIN, Z_MAX], clipped to the scanner range
# =============================================================================
z_now = tracking.DriftCorrector._to_float(scanning_probe_logic.scanner_target[Z_AXIS])
z_lo, z_hi = sorted((float(Z_MIN), float(Z_MAX)))
try:
    z_min, z_max = (float(v) for v in
                    scanning_probe_logic.scanner_constraints.axes[Z_AXIS].position.bounds)
    z_lo, z_hi = max(z_lo, z_min), min(z_hi, z_max)
except Exception as exc:
    print(f'(could not read the z scanner range, window not clipped: {exc})')
print(f'z window: {z_lo:.6g} .. {z_hi:.6g} (current z {z_now:.6g})')
if not z_lo <= z_now <= z_hi:
    print('WARNING: the current z is outside the z window.')

z_tracker = tracking.ZTracker(z_range=(z_lo, z_hi), z_axis=Z_AXIS, channel=Z_CHANNEL,
                              z_resolution=Z_RESOLUTION, z_frequency=Z_FREQUENCY,
                              method='brightest', n_average=5, average='median')

# =============================================================================
# Run
# =============================================================================
per_run = (f'{NUM_SWEEPS} sweeps at {REFERENCE_GATED_RATE / 1e6:.2f} MHz gated rate '
           f'(>= {MIN_SWEEPS} actual)'
           if REFERENCE_GATED_RATE is not None else f'{NUM_SWEEPS} sweeps')
print(f'{len(plan)} ODMR runs of {per_run} -- roughly {len(plan) * 11 / 60:.1f} h at the '
      f'linearity run\'s ~11 min per point.')
if not DRY_RUN:
    superposition_paths, superposition_plan = coilchar.run_linearity_scans(
        SEQ_NAME, FREQ_START, FREQ_STOP, N_FREQ_POINTS, QUANTIZATION_AXES, COIL_STRENGTHS,
        skip_first_points=SKIP_FIRST_POINTS, num_sweeps=NUM_SWEEPS, settle_time=SETTLE_TIME,
        wait_between=WAIT_BETWEEN, reference_gated_rate=REFERENCE_GATED_RATE,
        rate_interval=RATE_INTERVAL, min_sweeps=MIN_SWEEPS,
        folder=FOLDER, plan=plan, before_each=z_tracker)
    print(f'Done: {len(superposition_paths)} points saved in {FOLDER}/.')
    z_tracker.plot_history()
