# -*- coding: utf-8 -*-
"""
Coil-linearity data run: one current scan per coil (other coils at 0 A, with
return points for hysteresis) plus a scaled mixed-current scan, with a z
re-focus right before every ODMR run and a fixed wait between runs.

Run from the Jupyter kernel connected to qudi (so the qudi logic modules are
global variables there):

    %run -i run_coil_linearity.py

Every point is saved immediately as CoilLinearity/odmr_point_<n>.npz (full raw
spectrum, set + read-back currents/voltages, z focus, timestamps), so an
interrupted run keeps everything measured so far. The coils are set back to
0 A at the end, on error, and on a kernel interrupt.

Assumes the 1001-point ODMR sequence (no alternating) is generated, sampled
and loaded as SEQ_NAME, and that FREQ_START / FREQ_STOP / N_FREQ_POINTS below
match it.
"""
import numpy as np

import coil_characterization as coilchar
import tracking

# =============================================================================
# Settings -- check these before running
# =============================================================================
SEQ_NAME = 'cw_odmr_compact'
FREQ_START = 2.4e9          # Hz, as programmed in the sequence
FREQ_STOP = 3.4e9           # Hz
N_FREQ_POINTS = 1001
SKIP_FIRST_POINTS = 15      # only used for planning (and stored); files keep all points

NUM_SWEEPS = 30000          # sweeps per ODMR run -- at REFERENCE_GATED_RATE, if set
REFERENCE_GATED_RATE = 3.5e6  # Hz (sum_rate_gated_hz) at which NUM_SWEEPS applies; each chunk of
                            # sweeps counts x (live rate / this), so half the rate -> twice the
                            # sweeps. None: plain NUM_SWEEPS sweeps.
RATE_INTERVAL = 5.0         # s between gated-rate readings
MIN_SWEEPS = 10000          # with REFERENCE_GATED_RATE: never fewer actual sweeps than this per run
SETTLE_TIME = 5.0           # s after each current change (counts towards WAIT_BETWEEN)
WAIT_BETWEEN = 0.0        # s from the end of one ODMR run to the next z re-focus + run
FOLDER = 'CoilLinearity'

D_ZFS = 2.87e9
QUANTIZATION_AXES = [(np.sqrt(2 / 3), 0.0, np.sqrt(1 / 3)),
                     (-np.sqrt(2 / 3), 0.0, np.sqrt(1 / 3)),
                     (0.0, np.sqrt(2 / 3), -np.sqrt(1 / 3)),
                     (0.0, -np.sqrt(2 / 3), -np.sqrt(1 / 3))]
COIL_STRENGTHS = {'X': 55e6, 'Y': 110e6, 'Z': 110e6}    # rough Hz/A, for planning
MAX_CURRENT = {'X': 4.0, 'Y': 3.0, 'Z': 3.0}            # A
REFERENCE_FOLDER = 'FittedCoilODMRs'                    # linewidths + mixed triplet
REFERENCE_MW, NEW_MW = 0.2, 0.1                          # MW amplitudes: reference data / this run

# z re-focus ('brightest' point of a full scan of the z window, 5-point moving median)
Z_AXIS = 'z'
Z_CHANNEL = 'Sum'
Z_MIN, Z_MAX = 0.0, 6.2    # scanner units (m): z window scanned at every re-focus (clipped to the scanner)
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
plan, plan_info = coilchar.plan_linearity_scans(
    FREQ_START, FREQ_STOP, N_FREQ_POINTS, QUANTIZATION_AXES, COIL_STRENGTHS,
    skip_first_points=SKIP_FIRST_POINTS, max_current=MAX_CURRENT, D=D_ZFS,
    reference_folder=REFERENCE_FOLDER, reference_mw=REFERENCE_MW, new_mw=NEW_MW)

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
print(f'{len(plan)} ODMR runs of {per_run}, >= {WAIT_BETWEEN:.0f} s apart '
      f'(~{len(plan) * WAIT_BETWEEN / 3600:.1f} h of waiting alone).')
if not DRY_RUN:
    linearity_paths, linearity_plan = coilchar.run_linearity_scans(
        SEQ_NAME, FREQ_START, FREQ_STOP, N_FREQ_POINTS, QUANTIZATION_AXES, COIL_STRENGTHS,
        skip_first_points=SKIP_FIRST_POINTS, num_sweeps=NUM_SWEEPS, settle_time=SETTLE_TIME,
        wait_between=WAIT_BETWEEN, reference_gated_rate=REFERENCE_GATED_RATE,
        rate_interval=RATE_INTERVAL, min_sweeps=MIN_SWEEPS,
        folder=FOLDER, plan=plan, before_each=z_tracker)
    print(f'Done: {len(linearity_paths)} points saved in {FOLDER}/.')
    z_tracker.plot_history()
