# -*- coding: utf-8 -*-
"""
Short wire-3 test at the centerline only: does wire 3 stay on under continuous drive?

Same hardware settings as the wire-field-mapping run (normal mode, wire 3 at 0.42 V on
daq_1, pulser on d_ch3, +B bias, same ODMR sequence wfm_g0_m0010, which is reused from
the AWG if it is still there), but one position (offset 0) and short ODMRs (~4 min) so
a dropout shows up in time:

    warm-up (discarded) | reference x2 | wire 3 x N_WIRE_ON | reference x2

The wire voltage is re-written before every ODMR (one "configuration" per ODMR), so a
dropout that recovers when the DAQ value is re-written would also show up.
Spectra go to Wire3Test/wfm_<n>.npz.

Run from the Jupyter kernel connected to qudi:

    %cd C:\\Software\\qudi-iqo-modules
    import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
    for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
    %run -i run_wire3_test.py

Analysis (any Python): wire field of every wire-3 ODMR, in time order
(the warm-up is ignored):

    import wire_field_mapping as wfm
    points, results = wfm.analyze_wire_field_mapping('Wire3Test', plot=False)
"""
import numpy as np

import coil_characterization as coilchar
import coil_field_calibration as cfc
import pulser_autocalibrate as autocal
import tracking
import wire_centerline as wc
import wire_field_mapping as wfm

# =============================================================================
# Settings
# =============================================================================
N_WIRE_ON = 12                        # consecutive wire-3 ODMRs (~5 min each incl. drift correction)
N_REF_BEFORE = 2
N_REF_AFTER = 2
OFFSETS = np.array([0.0])             # centerline only
FOLDER = 'Wire3Test'
Z_STOP = None                         # stop before the next ODMR once z is above this

# ODMR: identical to the mapping run, so its sampled sequence is reused (no AWG upload)
FREQ_STEP_MHZ = 2.0
FREQ_MARGIN_MHZ = 50.0
ODMR_KWARGS = dict(mw_amp=0.1, mw_length=2e-6, always_on_ch='d_ch15', gradient_mode_ch='d_ch6',
                   duty_cycle=0.2, rising_time=100e-9, falling_time=100e-9,
                   laser_warmup_time=100e-6, laser_cooldown=10e-6, alternating=False,
                   shared_tail=True)
SEQ_PREFIX = 'wfm'
CLEAR_AWG_FIRST = False               # keep what is on the AWG; only (re)generates if missing / different

NUM_SWEEPS = 8000                     # ~4 min per ODMR (the mapping run: 30000, ~15 min)
REFERENCE_GATED_RATE = 3.5e6
RATE_INTERVAL = 5.0
MIN_SWEEPS = 3000
SETTLE_TIME = 5.0

# same centerline + tracking reference as the mapping run (None: take new scans from the
# scan history, flashlight-on wire scan = WIRE_SCAN_INDEX, tracking scan = TRACKING_SCAN_INDEX)
REFERENCE_FILE = 'WireFieldMapping/run_reference_20261006_run1.npz'
WIRE_SCAN_INDEX = -2
TRACKING_SCAN_INDEX = -1
EXPECTED_GAP = None
ANCHOR_X = None

POSITION_BOUNDS = {'x': (10, 90), 'y': (10, 90), 'z': (0, 6.2)}
MAX_XY_SHIFT = 5.0

DRY_RUN = False

# =============================================================================
# Configurations (from the mapping run's wire-3 triplet, +B)
# =============================================================================
_ref = dict(wfm.MATLAB_CONFIGS[1], label='wire3 test: reference')
_on = dict(wfm.MATLAB_CONFIGS[0], label='wire3 test: wire 3')
_warm = dict(_ref, label='wire3 test: warm-up', role='warmup')   # first ODMR after a load can be cut short
CONFIGS = [_warm] + [_ref] * N_REF_BEFORE + [_on] * N_WIRE_ON + [_ref] * N_REF_AFTER

# =============================================================================
# qudi objects
# =============================================================================
_needed = ('scanning_probe_logic', 'scanning_optimize_logic', 'scanning_data_logic',
           'pulsed_master_logic', 'analog_output_logic', 'coil_control_logic')


def _alive(obj):
    try:
        obj.module_state()
        return True
    except Exception:
        return False


_missing = []
for _name in _needed:
    if globals().get(_name) is not None and not _alive(globals()[_name]):
        print(f'{_name}: stale reference (qudi restarted?) -- fetching it again from qudi.')
        globals()[_name] = None
    if globals().get(_name) is None:
        try:
            globals()[_name] = wc._qudi_module(_name)
        except NameError:
            globals()[_name] = None
            _missing.append(_name)
if _missing:
    raise RuntimeError(f'qudi modules not loaded: {_missing}')
autocal.scanning_probe_logic = scanning_probe_logic
autocal.pulsed_master_logic = pulsed_master_logic
autocal.analog_output_logic = analog_output_logic
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic
tracking.scanning_probe_logic = scanning_probe_logic
tracking.scanning_optimize_logic = scanning_optimize_logic
tracking.pulsed_master_logic = pulsed_master_logic

# =============================================================================
# Bias, frequency window (both biases, as in the mapping run -> same sequence), variant
# =============================================================================
cal = cfc.load_coil_calibration()
bias_plan, bias_scale = wfm.plan_bias(cal, wfm.MATLAB_BIAS_CUBE)
f_start, f_stop, n_points = wfm.plan_odmr_window(bias_plan, FREQ_MARGIN_MHZ, FREQ_STEP_MHZ)
odmr_kwargs = dict(ODMR_KWARGS, freq_start=f_start, freq_stop=f_stop, num_of_points=n_points)
print(f'ODMR: {f_start / 1e9:.3f}-{f_stop / 1e9:.3f} GHz, {n_points} points')
variants = wfm.sequence_variants(CONFIGS, SEQ_PREFIX)
print('sequence:', list(variants.values()))

# =============================================================================
# Position: centerline
# =============================================================================
if REFERENCE_FILE:
    wire_scan, tracking_scan, target_now = wfm.load_run_reference(REFERENCE_FILE)
    print(f'reusing {REFERENCE_FILE}: tracking position {target_now}')
else:
    xy_scans = [e[0] if isinstance(e, (tuple, list)) else e for e in scanning_data_logic._scan_history]
    xy_scans = [s for s in xy_scans if set(s.settings.axes) == {'x', 'y'} and s.data is not None]
    wire_scan, tracking_scan = xy_scans[WIRE_SCAN_INDEX], xy_scans[TRACKING_SCAN_INDEX]
    target_now = {k: tracking.DriftCorrector._to_float(v) for k, v in scanning_probe_logic.scanner_target.items()}
gap = wc.find_wire_gap(wire_scan, expected_gap=EXPECTED_GAP)
anchor_x = target_now['x'] if ANCHOR_X is None else ANCHOR_X
locations = gap.locations(OFFSETS, x=anchor_x)
print('position:', {k: round(v, 3) for k, v in locations[0].items()})

import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(6, 6))
img_w, x_w, y_w = wc.scan_to_image(wire_scan)
wc.plot_wire_gap(img_w, x_w, y_w, gap, ax=ax)
ax.plot(locations[0]['x'], locations[0]['y'], 'P', color='lime', ms=10, label='test point')
ax.legend(loc='upper right', fontsize=7)
plt.show()

# =============================================================================
# Run
# =============================================================================
wfm.run_wire_field_mapping(CONFIGS, bias_plan, variants, locations, OFFSETS, None, NUM_SWEEPS,
                           folder=FOLDER, thermal_extra_point=False, z_stop=Z_STOP, dry_run=True)
proceed = False
if not DRY_RUN:
    proceed = input('Proceed with the wire-3 test? [y/N] ').strip().lower() in ('y', 'yes')
    if not proceed:
        print('Not started (nothing was changed on the hardware).')
if proceed:
    drift_corrector = tracking.DriftCorrector(
        xy_axes=('x', 'y'), z_axis='z', channel='Sum', position_bounds=POSITION_BOUNDS,
        max_xy_correlation_error=0.5, max_xy_shift=MAX_XY_SHIFT)
    drift_corrector.set_reference(tracking_scan, target_now, z_resolution=100, z_frequency=50.0)
    wfm.prepare_sequences(variants, odmr_kwargs, clear_first=CLEAR_AWG_FIRST)
    paths = wfm.run_wire_field_mapping(
        CONFIGS, bias_plan, variants, locations, OFFSETS, drift_corrector, NUM_SWEEPS,
        folder=FOLDER, thermal_extra_point=False, settle_time=SETTLE_TIME,
        reference_gated_rate=REFERENCE_GATED_RATE, rate_interval=RATE_INTERVAL,
        min_sweeps=MIN_SWEEPS, z_stop=Z_STOP)
    print(f'Done: {len(paths)} spectra in {FOLDER}/.')
