# -*- coding: utf-8 -*-
"""
Quick check of all four wires at one NV spot: is a missing field a fault of one
wire / channel or of the whole drive (pulser, supply, chip)?

Same hardware settings as the wire-field-mapping run (normal mode, daq_1, pulser on
d_ch3, +B bias, sequence wfm_g0_m0010 reused from the AWG), one position, short ODMRs
(~4 min each):

    [warm-up (discarded)] | [reference] | wire 1 | wire 2 | wire 3 | wire 4 | reference
    (x N_ROUNDS; warm-up / first / last reference only with WARMUP / REFERENCE_FIRST /
    REFERENCE_LAST)

Each wire alone, all at 0.21 V (WIRE_VOLTS).
What to look for (qualitative -- the NV-to-wire air gap changes with every remount, so
no absolute numbers): at a side gap (top / bottom) the two wires bordering that gap
give the largest fields, the far outer wire the smallest; in the central gap the two
central wires dominate. A wire with no field anywhere is not driving current.
Spectra go to WireCheck_<SPOT>/wfm_<n>.npz.

Position (POSITION):
  'target': the scanner target when the script starts -- put it on the NV spot (e.g.
            in the top or bottom gap) and take the flashlight-off tracking scan there
            first (TRACKING_SCAN_INDEX); no centerline fit.
  'gap':    centerline of the wire gap found in the flashlight-on wire scan
            (WIRE_SCAN_INDEX), at the x of the scanner target.
Scans from the scan history are saved as <FOLDER>/run_reference_<time>.npz (set
REFERENCE_FILE to that to repeat at the same spot).

Stop any running measurement first (interrupt the kernel: wires and coils are zeroed).
Run from the Jupyter kernel connected to qudi:

    %cd C:\\Software\\qudi-iqo-modules
    import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
    for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
    %run -i run_wire_check.py

Analysis (any Python): wire field of every ODMR, in time order (warm-up ignored):

    import wire_field_mapping as wfm
    points, results = wfm.analyze_wire_field_mapping('WireCheck', plot=False)
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
N_ROUNDS = 1                          # repeats of reference + wires 1-4
WARMUP = False                        # discarded first ODMR (the first one after a sequence load can be cut short)
REFERENCE_FIRST = True                # reference before the first round
REFERENCE_LAST = True                 # reference after the last round
# (with N_ROUNDS > 1 there is always a reference between rounds; keep at least one
# reference in the run -- or reuse one taken earlier at the same spot)
WIRES = (1, 2, 3, 4)
WIRE_VOLTS = {1: 0.21, 2: 0.21, 3: 0.21, 4: 0.21}   # V; the mapping run had 0.42 V on wires 3, 4 --
# at the top / bottom gap that would put ~90 MHz fields on the spectra and push the outermost
# line out of the frequency window of the reused sequence (2594-3180 MHz)
OFFSETS = np.array([0.0])             # one point
SPOT = 'top'                          # label: 'top', 'bottom', 'center', ...
FOLDER = f'WireCheck_{SPOT}'
POSITION = 'target'                   # 'target': the scanner target (on the NV spot); 'gap': gap centerline
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

# None: take the scans from the scan history (flashlight-on wire scan = WIRE_SCAN_INDEX,
# flashlight-off tracking scan with the scanner on the NV spot = TRACKING_SCAN_INDEX) and
# the scanner target as tracking position; a path: reuse a saved run reference instead
# (e.g. 'WireFieldMapping/run_reference_20261006_run1.npz' = the mapping run's central spot)
REFERENCE_FILE = None
WIRE_SCAN_INDEX = -2
TRACKING_SCAN_INDEX = -1
EXPECTED_GAP = None
ANCHOR_X = None

POSITION_BOUNDS = {'x': (10, 90), 'y': (10, 90), 'z': (0, 6.2)}
MAX_XY_SHIFT = 5.0

DRY_RUN = False

# =============================================================================
# Configurations (normal mode, daq_1, markers 0010, +B; all one group)
# =============================================================================
_ref = dict(wfm.MATLAB_CONFIGS[1], label='wire check: reference')
_warm = dict(_ref, label='wire check: warm-up', role='warmup')   # first ODMR after a load can be cut short


def _wire(k):
    volts = [0.0] * 4
    volts[k - 1] = WIRE_VOLTS[k]
    return dict(_ref, label=f'wire check: wire {k}', role='drive', volts=tuple(volts))


_rounds = []
for _r in range(N_ROUNDS):
    _rounds += ([_ref] if (_r > 0 or REFERENCE_FIRST) else []) + [_wire(k) for k in WIRES]
CONFIGS = ([_warm] if WARMUP else []) + _rounds + ([_ref] if REFERENCE_LAST else [])
if not any(c['role'] == 'reference' for c in CONFIGS):
    print('NOTE: no reference ODMR in this run (REFERENCE_FIRST and REFERENCE_LAST are False) -- '
          'the wire fields need a reference taken earlier at the same spot.')

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
# Position: scanner target (NV spot) or gap centerline
# =============================================================================
if REFERENCE_FILE:
    wire_scan, tracking_scan, target_now = wfm.load_run_reference(REFERENCE_FILE)
    print(f'reusing {REFERENCE_FILE}: tracking position {target_now}')
else:
    xy_scans = [e[0] if isinstance(e, (tuple, list)) else e for e in scanning_data_logic._scan_history]
    xy_scans = [s for s in xy_scans if set(s.settings.axes) == {'x', 'y'} and s.data is not None]
    wire_scan, tracking_scan = xy_scans[WIRE_SCAN_INDEX], xy_scans[TRACKING_SCAN_INDEX]
    target_now = {k: tracking.DriftCorrector._to_float(v) for k, v in scanning_probe_logic.scanner_target.items()}
    print(f'wire scan {wire_scan.timestamp}, tracking scan {tracking_scan.timestamp}, '
          f'scanner target {({k: round(v, 3) for k, v in target_now.items()})}')
if POSITION == 'target':
    gap = None
    locations = [{'x': target_now['x'], 'y': target_now['y']}]
else:
    gap = wc.find_wire_gap(wire_scan, expected_gap=EXPECTED_GAP)
    anchor_x = target_now['x'] if ANCHOR_X is None else ANCHOR_X
    locations = gap.locations(OFFSETS, x=anchor_x)
print(f'{SPOT} spot, position:', {k: round(v, 3) for k, v in locations[0].items()})

import matplotlib.pyplot as plt
fig, (ax_w, ax_t) = plt.subplots(1, 2, figsize=(12, 6))
img_w, x_w, y_w = wc.scan_to_image(wire_scan)
if gap is not None:
    wc.plot_wire_gap(img_w, x_w, y_w, gap, ax=ax_w)
else:
    ax_w.imshow(img_w.T, origin='lower', extent=[x_w[0], x_w[-1], y_w[0], y_w[-1]], cmap='gray', aspect='equal')
ax_w.set_title(f'wire scan (flashlight on), {wire_scan.timestamp:%Y-%m-%d %H:%M:%S}', fontsize=8)
img_t, x_t, y_t = wc.scan_to_image(tracking_scan)
ax_t.imshow(img_t.T, origin='lower', extent=[x_t[0], x_t[-1], y_t[0], y_t[-1]], cmap='gray', aspect='equal')
ax_t.set_title(f'tracking scan (flashlight off), {tracking_scan.timestamp:%Y-%m-%d %H:%M:%S}', fontsize=8)
for ax in (ax_w, ax_t):
    ax.plot(locations[0]['x'], locations[0]['y'], 'P', color='lime', ms=10, label=f'{SPOT} test point')
    ax.legend(loc='upper right', fontsize=7)
plt.show()

# =============================================================================
# Run
# =============================================================================
wfm.run_wire_field_mapping(CONFIGS, bias_plan, variants, locations, OFFSETS, None, NUM_SWEEPS,
                           folder=FOLDER, thermal_extra_point=False, z_stop=Z_STOP, dry_run=True)
proceed = False
if not DRY_RUN:
    proceed = input('Proceed with the wire check? [y/N] ').strip().lower() in ('y', 'yes')
    if not proceed:
        print('Not started (nothing was changed on the hardware).')
if proceed and not REFERENCE_FILE:
    import os, time as _time
    os.makedirs(FOLDER, exist_ok=True)
    _ref = os.path.join(FOLDER, 'run_reference_{0}.npz'.format(_time.strftime('%Y%m%d_%H%M%S')))
    wfm.save_run_reference(_ref, wire_scan, tracking_scan, target_now)
    print(f'scans + tracking position saved as {_ref} (REFERENCE_FILE to repeat at this spot)')
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
