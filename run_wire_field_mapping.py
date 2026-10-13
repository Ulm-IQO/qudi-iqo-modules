# -*- coding: utf-8 -*-
"""
Wire-field mapping across the gap (qudi version of loop_ESR_BD_gradient_newpulser.m).

For each of the 12 MATLAB configurations (wire voltages on daq_1 / daq_2, normal or
gradient mode, AWG marker pattern, bias field +-z), CW ODMR at 5 positions on a line
perpendicular to the wire-gap centerline (offsets 0.4 x (-2..2)), plus one extra
thermal-stabilization point first; drift correction (xy + z) before every ODMR.
Every spectrum is saved immediately as WireFieldMapping/wfm_<n>.npz; wires and
coils are zeroed at the end / on error / on interrupt.

Before running:
  1. xy scan with the flashlight ON (wires visible)       -> WIRE_SCAN_INDEX
  2. xy scan with the flashlight OFF (NV fluorescence),
     scanner left on the NV spot at the good z             -> TRACKING_SCAN_INDEX
  (indices count the xy scans in the scan history, -1 = newest)

Run from the Jupyter kernel connected to qudi:

    %cd C:\\Software\\qudi-iqo-modules
    import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
    for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
    %run -i run_wire_field_mapping.py

Analysis afterwards (any Python, no qudi needed):

    import wire_field_mapping as wfm
    points, results = wfm.analyze_wire_field_mapping('WireFieldMapping')
"""
import numpy as np

import coil_characterization as coilchar
import coil_field_calibration as cfc
import pulser_autocalibrate as autocal
import tracking
import wire_centerline as wc
import wire_field_mapping as wfm

# =============================================================================
# Settings -- check these before running
# =============================================================================
CONFIGS = wfm.MATLAB_CONFIGS          # or a subset / reordering, e.g. wfm.MATLAB_CONFIGS[6:]
OFFSETS = 0.4 * np.arange(-2, 3)      # perpendicular to the centerline (scanner units)
THERMAL_EXTRA_POINT = True            # one extra (discarded) ODMR at OFFSETS[0] per configuration
FOLDER = 'WireFieldMapping'
START_INDEX = 3                       # configuration to start at (resume after an interruption)
STOP_INDEX = None                     # last configuration to run (inclusive; None: to the end)
Z_STOP = 5.2                         # e.g. 5.6: stop before the next configuration once z is above this

# bias: the MATLAB fields (cube frame, MHz), scaled to the coil range (None = largest scale
# keeping every coil within 85 % of its calibrated range; 0.55 -> ~105 G, 0.6-2.5 A)
BIAS_CUBE = wfm.MATLAB_BIAS_CUBE
BIAS_SCALE = None

# ODMR (bd_cw_odmr_gradient); freq window from the predicted lines of both biases
FREQ_STEP_MHZ = 2.0
FREQ_MARGIN_MHZ = 50.0
ODMR_KWARGS = dict(mw_amp=0.1, mw_length=2e-6, always_on_ch='d_ch15', gradient_mode_ch='d_ch6',
                   duty_cycle=0.2, rising_time=100e-9, falling_time=100e-9,
                   laser_warmup_time=100e-6, laser_cooldown=10e-6, alternating=False,
                   shared_tail=True)      # delay + wait + falling shared by all points: ~6x less AWG memory
SEQ_PREFIX = 'wfm'
CLEAR_AWG_FIRST = True               # clear ALL waveforms on the AWG before uploading (earlier
                                      # uploads stay in its memory; the variants need ~35 M samples)
PREPARE_SEQUENCES = True              # False if the variants are already sampled (same settings)

NUM_SWEEPS = 30000                    # per ODMR, at REFERENCE_GATED_RATE (None: plain sweeps)
REFERENCE_GATED_RATE = 3.5e6
RATE_INTERVAL = 5.0
MIN_SWEEPS = 10000
SETTLE_TIME = 5.0                     # s after setting wires + bias (MATLAB: 5 s)
WAIT_BETWEEN = 0.0                    # s from one ODMR's end to the next drift correction

# wire-gap centerline (wire_centerline) on the flashlight-on scan
REFERENCE_FILE = 'WireFieldMapping/run_reference_20261006_run1.npz'                 # None: take the scans below from the scan history (and save them
                                      # as <FOLDER>/run_reference_<time>.npz); a path: reuse that saved
                                      # wire scan + tracking scan + tracking position instead
WIRE_SCAN_INDEX = -2
TRACKING_SCAN_INDEX = -1
EXPECTED_GAP = None                   # gap width AS SEEN in the wire scan (None: free)
ANCHOR_X = None                       # x of the line of points (None: x of the scanner target)

# drift correction (as in tutorial_bd_wire_calibration.py)
POSITION_BOUNDS = {'x': (10, 90), 'y': (10, 90), 'z': (0, 6.2)}
MAX_XY_SHIFT = 5.0

DRY_RUN = False                        # True: print the plan only (no sequences, no hardware)

# =============================================================================
# qudi objects (from the notebook if defined there, else from qudi's namespace
# server) into the modules' namespaces
# =============================================================================
_needed = ('scanning_probe_logic', 'scanning_optimize_logic', 'scanning_data_logic',
           'pulsed_master_logic', 'analog_output_logic', 'coil_control_logic')
def _alive(obj):
    """False for a stale reference (e.g. into a qudi that has since been restarted)."""
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
    print(f'NOTE: qudi modules not loaded: {_missing} -- load them in qudi before a real run.')
    if not DRY_RUN:
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
# Bias, frequency window, sequence variants
# =============================================================================
cal = cfc.load_coil_calibration()
bias_plan, bias_scale = wfm.plan_bias(cal, BIAS_CUBE, scale=BIAS_SCALE)
print(f'bias scale {bias_scale:.3f} of the MATLAB fields')
f_start, f_stop, n_points = wfm.plan_odmr_window(bias_plan, FREQ_MARGIN_MHZ, FREQ_STEP_MHZ)
odmr_kwargs = dict(ODMR_KWARGS, freq_start=f_start, freq_stop=f_stop, num_of_points=n_points)
print(f'ODMR: {f_start / 1e9:.3f}-{f_stop / 1e9:.3f} GHz, {n_points} points ({FREQ_STEP_MHZ} MHz)')

variants = wfm.sequence_variants(CONFIGS, SEQ_PREFIX)
gen_params = dict(pulsed_master_logic.generation_parameters)
fs = pulsed_master_logic.pulse_generator_settings['sample_rate']
n_samples = wfm.estimate_sequence_samples(odmr_kwargs, gen_params, fs)
mem = float(pulsed_master_logic.pulse_generator_constraints.waveform_length.max)
print(f'{len(variants)} sequence variants, ~{n_samples / 1e6:.1f} M samples each, '
      f'{len(variants) * n_samples / 1e6:.1f} M in total (AWG memory {mem / 1e6:.1f} M)')
for (gm, pch), name in variants.items():
    print(f'   {name}: gradient_mode {gm}, pulser_ch "{pch}"')
if len(variants) * n_samples > 0.8 * mem:
    raise ValueError('the sequence variants would not fit in AWG memory together: reduce mw_length, '
                     'raise FREQ_STEP_MHZ or narrow FREQ_MARGIN_MHZ.')

# =============================================================================
# Positions: wire-gap centerline, points perpendicular to it
# =============================================================================
if REFERENCE_FILE:
    wire_scan, tracking_scan, target_now = wfm.load_run_reference(REFERENCE_FILE)
    print(f'reusing {REFERENCE_FILE}: wire scan {wire_scan.timestamp}, tracking scan {tracking_scan.timestamp}, '
          f'tracking position {target_now}')
else:
    xy_scans = [e[0] if isinstance(e, (tuple, list)) else e for e in scanning_data_logic._scan_history]
    xy_scans = [s for s in xy_scans if set(s.settings.axes) == {'x', 'y'} and s.data is not None]
    wire_scan, tracking_scan = xy_scans[WIRE_SCAN_INDEX], xy_scans[TRACKING_SCAN_INDEX]
    print(f'wire scan: {wire_scan.timestamp}, tracking scan: {tracking_scan.timestamp}')
    target_now = {k: tracking.DriftCorrector._to_float(v) for k, v in scanning_probe_logic.scanner_target.items()}
gap = wc.find_wire_gap(wire_scan, expected_gap=EXPECTED_GAP)
print(gap)
anchor_x = target_now['x'] if ANCHOR_X is None else ANCHOR_X
locations = gap.locations(OFFSETS, x=anchor_x)
print('positions:', [{k: round(v, 3) for k, v in p.items()} for p in locations])

# both scans with the fitted gap, the centerline and the planned points
import matplotlib.pyplot as plt
fig, (ax_w, ax_t) = plt.subplots(1, 2, figsize=(14, 6))
img_w, x_w, y_w = wc.scan_to_image(wire_scan)
wc.plot_wire_gap(img_w, x_w, y_w, gap, ax=ax_w)
ax_w.set_title(f'wire scan (flashlight on), {wire_scan.timestamp:%Y-%m-%d %H:%M:%S}\n{ax_w.get_title()}', fontsize=8)
img_t, x_t, y_t = wc.scan_to_image(tracking_scan)
ax_t.imshow(img_t.T, origin='lower', extent=[x_t[0], x_t[-1], y_t[0], y_t[-1]], cmap='gray', aspect='equal')
t = np.linspace(-2, 2, 3) * max(x_t[-1] - x_t[0], y_t[-1] - y_t[0])
for (p, dvec) in gap.edge_lines():
    ax_t.plot(p[0] + t * dvec[0], p[1] + t * dvec[1], '--', color='tab:orange', lw=1)
ax_t.plot(gap.p0[0] + t * gap.direction[0], gap.p0[1] + t * gap.direction[1], '-', color='yellow', lw=1.5,
          label='centerline (from the wire scan)')
ax_t.plot(target_now['x'], target_now['y'], '*', color='red', ms=14, label='tracking reference (scanner now)')
ax_t.set_xlim(x_t[0], x_t[-1]); ax_t.set_ylim(y_t[0], y_t[-1])
ax_t.set_title(f'tracking scan (flashlight off), {tracking_scan.timestamp:%Y-%m-%d %H:%M:%S}', fontsize=8)
for ax in (ax_w, ax_t):
    ax.plot([p['x'] for p in locations], [p['y'] for p in locations], 'P', color='lime', ms=9,
            label='planned points')
    for p, o in zip(locations, OFFSETS):
        ax.annotate(f'{o:+.2f}', (p['x'], p['y']), xytext=(6, 0), textcoords='offset points',
                    color='lime', fontsize=7, va='center')
    ax.set_xlabel('x'); ax.set_ylabel('y')
    ax.legend(loc='upper right', fontsize=7)
fig.tight_layout()
plt.show()

# =============================================================================
# Run
# =============================================================================
wfm.run_wire_field_mapping(CONFIGS, bias_plan, variants, locations, OFFSETS, None, NUM_SWEEPS,
                           folder=FOLDER, thermal_extra_point=THERMAL_EXTRA_POINT,
                           start_index=START_INDEX, stop_index=STOP_INDEX, z_stop=Z_STOP, dry_run=True)
proceed = False
if not DRY_RUN:
    if PREPARE_SEQUENCES and CLEAR_AWG_FIRST:
        print('NOTE: proceeding clears ALL waveforms/sequences on the AWG first (CLEAR_AWG_FIRST).')
    answer = input('Check the scans, points and plan above. Proceed with the measurement? [y/N] ')
    proceed = answer.strip().lower() in ('y', 'yes')
    if not proceed:
        print('Not started (nothing was changed on the hardware).')
if proceed and not REFERENCE_FILE:
    import os, time as _time
    _ref = os.path.join(FOLDER, 'run_reference_{0}.npz'.format(_time.strftime('%Y%m%d_%H%M%S')))
    wfm.save_run_reference(_ref, wire_scan, tracking_scan, target_now)
    print(f'scans + tracking position saved as {_ref} (REFERENCE_FILE to reuse them)')
if proceed:
    drift_corrector = tracking.DriftCorrector(
        xy_axes=('x', 'y'), z_axis='z', channel='Sum', position_bounds=POSITION_BOUNDS,
        max_xy_correlation_error=0.5, max_xy_shift=MAX_XY_SHIFT)
    drift_corrector.set_reference(tracking_scan, target_now, z_resolution=100, z_frequency=50.0)
    if PREPARE_SEQUENCES:
        wfm.prepare_sequences(variants, odmr_kwargs, clear_first=CLEAR_AWG_FIRST)
    paths = wfm.run_wire_field_mapping(
        CONFIGS, bias_plan, variants, locations, OFFSETS, drift_corrector, NUM_SWEEPS,
        folder=FOLDER, thermal_extra_point=THERMAL_EXTRA_POINT, settle_time=SETTLE_TIME,
        wait_between=WAIT_BETWEEN, reference_gated_rate=REFERENCE_GATED_RATE,
        rate_interval=RATE_INTERVAL, min_sweeps=MIN_SWEEPS, start_index=START_INDEX,
        stop_index=STOP_INDEX, z_stop=Z_STOP)
    print(f'Done: {len(paths)} spectra in {FOLDER}/.')
