# -*- coding: utf-8 -*-
"""
wire_field_mapping.py

Qudi version of the MATLAB loop_ESR_BD_gradient_newpulser.m wire-field
calibration: CW ODMR on a line of points across the wire gap, for a list of
wire-drive configurations (wire voltages on daq_1 / daq_2, normal or gradient
mode, AWG marker pattern of the pulser, bias field +-z), every point drift
corrected right before its ODMR, every spectrum saved immediately.

Analysis (`analyze_wire_field_mapping`): every spectrum is fitted for the full
field vector (all 8 lines, coil_linearity_analysis.fit_spectrum_field, seeded
by the coil calibration); the wire field of a drive configuration is its field
minus that of the reference configuration of the same group (same bias, wires
at 0 V) at the same position.

Mapping MATLAB -> qudi:
  DAQ_USB/ao0..3  -> daq_1 ao0..ao3      dev1/ao0..3 -> daq_2 ao0..ao3
  PB bit 10       -> d_ch15 (always_on_ch)   PB bit 1 -> d_ch6 (gradient_mode_ch)
  AWG marker k    -> d_ch<k> (k = 1..4); marker 3 = d_ch3 is the pulser trigger
                     (pulser_ch of bd_cw_odmr_gradient); bMarkerESR -> pulser_ch
                     listing all markers that are on, e.g. 'd_ch2,d_ch3,d_ch4'
                     ('' = pulser never fires)
  BFields (MHz, frame with an NV axis along (1,1,1)/sqrt(3), i.e. the cube
  frame) -> coil-calibration NV frame via CUBE_TO_NV, then coil currents via
  coil_field_calibration.currents_for_field.

Needs the qudi globals of pulser_autocalibrate (scanning_probe_logic,
pulsed_master_logic, analog_output_logic), coil_characterization
(pulsed_master_logic, coil_control_logic) and tracking injected (see
run_wire_field_mapping.py).
"""

import glob
import os
import re
import time

import numpy as np

# Cube frame (NV axes along (+-1, +-1, +-1)/sqrt(3)) -> NV frame of the coil
# calibration (axes (+-sqrt(2/3), 0, sqrt(1/3)), (0, +-sqrt(2/3), -sqrt(1/3))):
# x along [110]/sqrt(2), y along [-110]/sqrt(2), z along [001]. Rows: NV-frame
# axes in cube coordinates; maps the four cube NV axes exactly onto the
# calibration's quantization axes.
CUBE_TO_NV = np.array([[1.0, 1.0, 0.0] / np.sqrt(2),
                       [-1.0, 1.0, 0.0] / np.sqrt(2),
                       [0.0, 0.0, 1.0]])

# MATLAB bias fields (MHz, cube frame). All four NV projections are different
# and large, so all 8 lines are resolved; -z permutes the projections.
MATLAB_BIAS_CUBE = {'+': (165.0, -85.0, 500.0), '-': (165.0, -85.0, -500.0)}


def _cfg(label, group, role, volts, daq, gradient_mode, markers, bias):
    return {'label': label, 'group': group, 'role': role,
            'volts': tuple(0.35 * np.asarray(volts, float)), 'daq': daq,
            'gradient_mode': gradient_mode, 'markers': tuple(markers), 'bias': bias}


# The 12 configurations of loop_ESR_BD_gradient_newpulser.m, in its order:
# triplets (drive, reference at 0 V, drive) per bias sign and mode.
MATLAB_CONFIGS = [
    _cfg('normal +B: wire 3',    0, 'drive',     (0, 0, 1.2, 0), 1, 0, (0, 0, 1, 0), '+'),
    _cfg('normal +B: reference', 0, 'reference', (0, 0, 0, 0),   1, 0, (0, 0, 1, 0), '+'),
    _cfg('normal +B: wire 1',    0, 'drive',     (0.6, 0, 0, 0), 1, 0, (0, 0, 1, 0), '+'),
    _cfg('normal -B: wire 4',    1, 'drive',     (0, 0, 0, 1.2), 1, 0, (0, 0, 1, 0), '-'),
    _cfg('normal -B: reference', 1, 'reference', (0, 0, 0, 0),   1, 0, (0, 0, 1, 0), '-'),
    _cfg('normal -B: wire 2',    1, 'drive',     (0, 0.6, 0, 0), 1, 0, (0, 0, 1, 0), '-'),
    _cfg('gradient +B: wires 3+4', 2, 'drive',     (0, 0, 1.2, 1.2), 2, 1, (0, 1, 1, 1), '+'),
    _cfg('gradient +B: reference', 2, 'reference', (0, 0, 0, 0),     2, 1, (0, 0, 0, 0), '+'),
    _cfg('gradient +B: wires 1+2', 2, 'drive',     (0.6, 0.6, 0, 0), 2, 1, (0, 0, 1, 0), '+'),
    _cfg('gradient -B: wires 3+4', 3, 'drive',     (0, 0, 1.2, 1.2), 2, 1, (0, 0, 1, 0), '-'),
    _cfg('gradient -B: reference', 3, 'reference', (0, 0, 0, 0),     2, 1, (0, 0, 0, 0), '-'),
    _cfg('gradient -B: wires 1+2', 3, 'drive',     (0.6, 0.6, 0, 0), 2, 1, (0, 1, 1, 1), '-'),
]


def markers_to_pulser_ch(markers):
    """MATLAB bMarkerESR (4 flags) -> pulser_ch of bd_cw_odmr_gradient."""
    return ','.join('d_ch{0:d}'.format(k + 1) for k, m in enumerate(markers) if m)


def cube_to_nv(B_cube):
    return CUBE_TO_NV @ np.asarray(B_cube, float)


# =============================================================================
# Planning: bias currents, frequency window, sequence variants
# =============================================================================

def plan_bias(cal, bias_cube=None, scale=None, max_fraction=0.85, nv_axes=None, verbose=True):
    """
    Bias fields (cube frame, MHz) -> coil currents. scale=None: the largest
    common scale <= 1 that keeps every current within max_fraction of the
    calibrated range. Returns {sign: {'B_cube', 'B_nv', 'currents',
    'lines_MHz', 'projections_MHz'}} and the scale used.
    """
    import coil_field_calibration as cfc
    bias_cube = bias_cube or MATLAB_BIAS_CUBE
    lim = np.array([cal['valid_current_range_A'][c] for c in 'XYZ'])
    if scale is None:
        scale = 1.0
        for Bc in bias_cube.values():
            I = cfc.currents_for_field(cube_to_nv(Bc), cal, check_limits=False)
            scale = min(scale, float(np.min(max_fraction * lim / np.maximum(np.abs(I), 1e-12))))
    axes = np.array(nv_axes if nv_axes is not None else cal['quantization_axes'], float)
    out = {}
    for sign, Bc in bias_cube.items():
        Bn = scale * cube_to_nv(Bc)
        I = cfc.currents_for_field(Bn, cal)
        Bfit = cfc.field_from_currents(I, cal)
        out[sign] = {'B_cube': scale * np.asarray(Bc, float), 'B_nv': Bn, 'currents': I,
                     'lines_MHz': cfc.predicted_odmr_lines(I, cal) / 1e6,
                     'projections_MHz': np.abs(axes @ Bfit)}
        if verbose:
            l = out[sign]['lines_MHz']
            print(f'bias {sign}: B_nv {np.round(Bn, 1)} MHz ({np.linalg.norm(Bn) / cal["gamma_MHz_per_G"]:.0f} G), '
                  f'coil currents {np.round(I, 3)} A, lines {np.round(l, 0)} MHz '
                  f'(min spacing {np.min(np.diff(l)):.0f} MHz)')
    return out, scale


def plan_odmr_window(bias_plan, margin_MHz=50.0, step_MHz=2.0):
    """Frequency window covering every predicted line of every bias, +- margin."""
    lines = np.concatenate([b['lines_MHz'] for b in bias_plan.values()])
    f0 = np.floor((lines.min() - margin_MHz) / step_MHz) * step_MHz
    f1 = np.ceil((lines.max() + margin_MHz) / step_MHz) * step_MHz
    return f0 * 1e6, f1 * 1e6, int(round((f1 - f0) / step_MHz)) + 1


def sequence_variants(configs, prefix='wfm'):
    """{(gradient_mode, pulser_ch): sequence name} for the configurations."""
    out = {}
    for c in configs:
        key = (int(c['gradient_mode']), markers_to_pulser_ch(c['markers']))
        if key not in out:
            tag = ''.join(str(int(m)) for m in c['markers'])
            out[key] = '{0}_g{1}_m{2}'.format(prefix, key[0], tag)
    return out


def estimate_sequence_samples(odmr_kwargs, generation_parameters, sample_rate):
    """Rough AWG samples of one bd_cw_odmr_gradient variant: point waveforms (+ the shared
    tail with shared_tail=True) + the laser warm-up."""
    point = odmr_kwargs.get('rising_time', 100e-9) + odmr_kwargs['mw_length']
    tail = (generation_parameters.get('laser_delay', 0.5e-6) + generation_parameters.get('wait_time', 1e-6)
            + odmr_kwargs.get('falling_time', 100e-9))
    if odmr_kwargs.get('shared_tail', False):
        total = odmr_kwargs['num_of_points'] * point + tail
    else:
        total = odmr_kwargs['num_of_points'] * (point + tail)
    total += odmr_kwargs.get('laser_warmup_time', 100e-6) + odmr_kwargs.get('laser_cooldown', 10e-6)
    return int(total * sample_rate)


def clear_awg(timeout=120.0):
    """Stop any measurement / the pulser and delete ALL waveforms and sequences on the
    pulse generator (= the "clear pulser" button), so the variants have the AWG memory
    to themselves. Previously sampled assets must be re-sampled afterwards."""
    import pulser_autocalibrate as autocal
    pml = autocal.pulsed_master_logic
    if pml.status_dict['measurement_running']:
        pml.toggle_pulsed_measurement(False)
        autocal._wait_while(lambda: pml.status_dict['measurement_running'], 'measurement to stop', 30.0)
    if pml.status_dict['pulser_running']:
        pml.toggle_pulse_generator(False)
        autocal._wait_while(lambda: pml.status_dict['pulser_running'], 'pulser to stop', 30.0)
    print('clearing the pulse generator (all waveforms / sequences on the AWG) ...')
    t = time.time()
    pml.clear_pulse_generator()
    autocal._wait_while(lambda: pml.loaded_asset[0] != '', 'the pulse generator to clear', timeout)
    print(f'   cleared in {time.time() - t:.0f} s')


_BUSY_FLAGS = ('sampling_ensemble_busy', 'sampling_sequence_busy', 'sampload_busy', 'loading_busy',
               'predefined_generation_busy')


def _pulsed_idle(pml):
    return not any(pml.status_dict.get(k, False) for k in _BUSY_FLAGS)


def _wait_pulsed_idle(pml, timeout=300.0, quiet_time=2.0):
    """Wait until no generation / sampling / loading is in progress, and has not been
    for quiet_time s (some completion callbacks arrive a little later)."""
    t0 = time.time()
    quiet_since = None
    while True:
        if _pulsed_idle(pml):
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= quiet_time:
                return
        else:
            quiet_since = None
        if time.time() - t0 > timeout:
            raise TimeoutError(f'pulsed logic still busy after {timeout:.0f} s: {dict(pml.status_dict)}')
        time.sleep(0.2)


def _sampled_sequences(pml):
    try:
        return set(pml.sequencegeneratorlogic().sampled_sequences)
    except Exception:
        return set()


def prepare_sequences(variants, odmr_kwargs, timeout=900.0, clear_first=True, start_timeout=30.0):
    """Generate + sample + upload every variant of bd_cw_odmr_gradient once (they
    stay in AWG memory; switching later is a fast load_sequence).

    Variants already sampled on the device are kept (assumed to be from the same
    settings -- set clear_first and delete them / clear the AWG if the settings
    changed). clear_first: clear the AWG first (clear_awg) if anything else than
    the variants is on it, since earlier uploads stay in its memory.

    Every request waits for the pulsed logic to be completely idle first; a variant
    counts as done once it is sampled and loaded; a request that never starts is
    sent once more.
    """
    import pulser_autocalibrate as autocal
    pml = autocal.pulsed_master_logic
    names = set(variants.values())
    # the running qudi must have the current bd_predefined_methods (it reads them at startup
    # and silently drops parameters a method does not have)
    try:
        known = set(pml.sequencegeneratorlogic().generate_method_params['bd_cw_odmr_gradient'])
    except Exception:
        known = None
    if known is not None:
        unknown = sorted(set(odmr_kwargs) - known)
        if unknown:
            raise RuntimeError(f'the running qudi does not know the bd_cw_odmr_gradient parameter(s) {unknown}: '
                               f'it was started before bd_predefined_methods.py was updated. Restart qudi.')
    _wait_pulsed_idle(pml)

    def _wanted(gradient_mode, pulser_ch, name):
        kw = dict(odmr_kwargs)
        kw.update(name=name, gradient_mode=gradient_mode, pulser_ch=pulser_ch)
        return kw

    def _matches(name, kw):
        """sampled variant generated with exactly these parameters?"""
        try:
            stored = dict(pml.sequencegeneratorlogic().saved_pulse_sequences[name].generation_method_parameters)
        except Exception:
            return False
        for k, v in kw.items():
            if k not in stored:
                return False
            a, b = stored[k], v
            if isinstance(b, (int, float)) and not isinstance(b, bool):
                if not np.isclose(float(a), float(b), rtol=1e-9, atol=0.0):
                    return False
            elif a != b:
                return False
        return True

    stale = [n for key, n in variants.items()
             if n in _sampled_sequences(pml) and not _matches(n, _wanted(*key, n))]
    if stale:
        print(f'sampled with other settings (re-generated): {stale}')
        clear_awg()
        _wait_pulsed_idle(pml)
    elif clear_first:
        others = _sampled_sequences(pml) - names
        try:
            others |= {w for w in pml.sequencegeneratorlogic().sampled_waveforms
                       if not w.startswith('*')
                       and not any(w.split('awg_', 1)[-1].startswith(n) for n in names)}
        except Exception:
            pass
        if others:
            print(f'{len(others)} other asset(s) on the pulse generator, e.g. {sorted(others)[:3]}')
            clear_awg()
            _wait_pulsed_idle(pml)
    for (gradient_mode, pulser_ch), name in variants.items():
        if name in _sampled_sequences(pml):
            print(f'"{name}" is already sampled on the device -- reused.')
            continue
        kw = dict(odmr_kwargs)
        kw.update(name=name, gradient_mode=gradient_mode, pulser_ch=pulser_ch)
        for attempt in (1, 2):
            print(f'generating + sampling "{name}" (gradient_mode {gradient_mode}, pulser_ch "{pulser_ch}")'
                  f'{" -- retry" if attempt == 2 else ""} ...')
            _wait_pulsed_idle(pml)
            t = time.time()
            pml.generate_predefined_sequence('bd_cw_odmr_gradient', kw, sample_and_load=True)
            started = False
            while True:
                time.sleep(0.5)
                done = name in _sampled_sequences(pml) and pml.loaded_asset[0] == name
                if done and _pulsed_idle(pml):
                    break
                if not _pulsed_idle(pml) or name in _sampled_sequences(pml):
                    started = True
                if not started and time.time() - t > start_timeout:
                    break                                   # request lost -> retry
                if started and _pulsed_idle(pml) and time.time() - t > start_timeout and not done:
                    raise RuntimeError(f'"{name}" did not sample/load (loaded: {pml.loaded_asset}); '
                                       f'see the qudi log.')
                if time.time() - t > timeout:
                    raise TimeoutError(f'"{name}" not sampled/loaded after {timeout:.0f} s.')
            if done:
                print(f'   done in {time.time() - t:.0f} s')
                break
        else:
            raise RuntimeError(f'"{name}": the generation request was not processed (twice); see the qudi log.')


# =============================================================================
# Run reference: wire scan + tracking scan + tracking target, saved / reused
# =============================================================================

class ArchivedScan:
    """Stand-in for a qudi ScanData restored from a file: what wire_centerline and
    tracking.DriftCorrector use (settings.axes / range / resolution, data, timestamp,
    copy())."""

    def __init__(self, axes, rng, resolution, data, timestamp):
        import datetime
        import types
        self.settings = types.SimpleNamespace(axes=tuple(axes), range=tuple(tuple(r) for r in rng),
                                              resolution=tuple(int(n) for n in resolution))
        self.data = dict(data)
        self.timestamp = (timestamp if isinstance(timestamp, datetime.datetime)
                          else datetime.datetime.fromisoformat(str(timestamp)))

    def copy(self):
        return ArchivedScan(self.settings.axes, self.settings.range, self.settings.resolution,
                            {k: np.array(v, copy=True) for k, v in self.data.items()}, self.timestamp)


def _scan_fields(scan, prefix):
    import wire_centerline as wc
    out = {prefix + 'axes': np.array([str(a) for a in scan.settings.axes]),
           prefix + 'range': np.array([[float(r[0]), float(r[1])] for r in scan.settings.range]),
           prefix + 'resolution': np.array([int(n) for n in scan.settings.resolution]),
           prefix + 'timestamp': scan.timestamp.isoformat()}
    for k, v in scan.data.items():
        out[prefix + 'data_' + k] = wc._plain(v)
    return out


def _scan_from_fields(d, prefix):
    data = {k[len(prefix) + 5:]: d[k] for k in d.files if k.startswith(prefix + 'data_')}
    return ArchivedScan([str(a) for a in d[prefix + 'axes']], d[prefix + 'range'], d[prefix + 'resolution'],
                        data, str(d[prefix + 'timestamp']))


def save_run_reference(path, wire_scan, tracking_scan, target):
    """Save the wire scan (flashlight on), the tracking scan (flashlight off) and the
    tracking target position (dict) of a run, to reuse them later
    (REFERENCE_FILE in run_wire_field_mapping.py)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    np.savez(path, target_axes=np.array(sorted(target)),
             target=np.array([float(target[k]) for k in sorted(target)]),
             saved_at=time.strftime('%Y-%m-%d %H:%M:%S'),
             **_scan_fields(wire_scan, 'wire_'), **_scan_fields(tracking_scan, 'tracking_'))
    return path


def load_run_reference(path):
    """(wire_scan, tracking_scan, target) saved by save_run_reference."""
    with np.load(path, allow_pickle=False) as d:
        target = {str(k): float(v) for k, v in zip(d['target_axes'], d['target'])}
        return _scan_from_fields(d, 'wire_'), _scan_from_fields(d, 'tracking_'), target


def load_archived_scan(path):
    """A scan saved by archive_scans.py, as an ArchivedScan."""
    with np.load(path, allow_pickle=False) as d:
        data = {k[5:]: d[k] for k in d.files if k.startswith('data_')}
        return ArchivedScan([str(a) for a in d['axes']], d['range'], d['resolution'], data, str(d['timestamp']))


# =============================================================================
# Measurement
# =============================================================================

def _zero_everything():
    import pulser_autocalibrate as autocal
    import coil_characterization as coilchar
    try:
        autocal.set_wire_currents((0, 0, 0, 0), use_daq1=True)     # zeroes daq_2 as well
    except Exception as exc:
        print(f'!! could not zero the wire outputs: {exc}')
    try:
        coilchar._set_coil_currents(0.0, 0.0, 0.0)
    except Exception as exc:
        print(f'!! could not zero the coils: {exc}')


def run_wire_field_mapping(configs, bias_plan, variants, locations, offsets, drift_corrector,
                           num_sweeps, folder='WireFieldMapping', thermal_extra_point=True,
                           settle_time=5.0, wait_between=0.0, reference_gated_rate=None,
                           rate_interval=5.0, min_sweeps=0, start_index=0, stop_index=None,
                           z_stop=None, z_axis='z', dry_run=False):
    """
    For every configuration (in order): set the wire voltages (daq_1 or daq_2, the
    other daq zeroed), the bias coil currents, load the sequence variant, wait
    settle_time; then for every location (plus, with thermal_extra_point, one
    extra measurement at the first location first, flagged 'thermal'):
    drift-correct (xy cross-correlation + z at the reference target), move to
    the location (+ drift), run the ODMR, save it immediately as
    <folder>/wfm_<n>.npz. Wires and coils are zeroed at the end / on error /
    on interrupt.

    configs: list of configuration dicts (MATLAB_CONFIGS format).
    bias_plan: from plan_bias. variants: from sequence_variants (already
    prepared with prepare_sequences). locations / offsets: target positions
    (dicts {x_axis: .., y_axis: ..}, reference-scan frame) and their
    perpendicular offsets from the wire-gap centerline.
    num_sweeps etc.: as coil_characterization._run_odmr_sequence_and_collect.
    start_index: configuration index to start at (to resume a run).
    stop_index: last configuration index to run (inclusive; None: to the end).
    z_stop: if the z of the last drift correction is above this when a new
        configuration is due, stop there (the configuration to resume from is
        printed) -- so the focus cannot drift out of the z range unattended.
    dry_run: print the plan only.
    """
    import pulser_autocalibrate as autocal
    import coil_characterization as coilchar
    os.makedirs(folder, exist_ok=True)
    existing = [int(m) for p in glob.glob(os.path.join(folder, 'wfm_*.npz'))
                for m in re.findall(r'wfm_(\d+)\.npz$', p)]
    n_file = max(existing) + 1 if existing else 0
    plan = []
    last = len(configs) - 1 if stop_index is None else min(int(stop_index), len(configs) - 1)
    for ci, c in enumerate(configs):
        if ci < start_index or ci > last:
            continue
        pts = list(range(len(locations)))
        if thermal_extra_point:
            pts = [0] + pts
        for k, li in enumerate(pts):
            plan.append((ci, li, thermal_extra_point and k == 0))
    if dry_run:
        for ci, c in enumerate(configs):
            if ci < start_index or ci > last:
                continue
            b = bias_plan[c['bias']]
            print(f"{ci:2d} {c['label']:26s} daq_{c['daq']} V={np.round(c['volts'], 3)!s:24s} "
                  f"seq {variants[(c['gradient_mode'], markers_to_pulser_ch(c['markers']))]:14s} "
                  f"bias {c['bias']} I={np.round(b['currents'], 3)}")
        print(f'{len(plan)} ODMR runs ({len(locations)} positions'
              f'{" + 1 thermal" if thermal_extra_point else ""} per configuration), configurations '
              f'{start_index}-{last}{f"; stops early if z > {z_stop}" if z_stop is not None else ""}.')
        return []
    autocal.ensure_wire_outputs_active()
    saved = []
    current_cfg = None
    last_end = None
    last_z = None
    try:
        for ci, li, thermal in plan:
            c = configs[ci]
            seq_name = variants[(c['gradient_mode'], markers_to_pulser_ch(c['markers']))]
            if ci != current_cfg:
                if z_stop is not None and last_z is not None and last_z > z_stop:
                    print(f'=== stopped before configuration {ci}: z = {last_z:.3f} > Z_STOP = {z_stop} '
                          f'-- refocus and resume with START_INDEX = {ci} ===')
                    break
                print(f"=== configuration {ci}: {c['label']} ===")
                autocal.set_wire_currents(c['volts'], use_daq1=(c['daq'] == 1))
                I = bias_plan[c['bias']]['currents']
                coilchar._set_coil_currents(*I)
                time.sleep(settle_time)
                current_cfg = ci
            if wait_between > 0 and last_end is not None:
                time.sleep(max(0.0, wait_between - (time.time() - last_end)))
            autocal._laser_on_for_scanning()
            drift, ref_target = drift_corrector.correct_drift()
            target = autocal.move_xy_only(drift_corrector, locations[li])
            last_z = float(target.get(z_axis, np.nan)) if hasattr(target, 'get') else None
            t0 = time.time()
            freq, signal = coilchar._run_odmr_sequence_and_collect(
                seq_name, num_sweeps, reference_gated_rate=reference_gated_rate,
                rate_interval=rate_interval, min_sweeps=min_sweeps)
            last_end = time.time()
            info = dict(getattr(coilchar, 'last_run_info', {}) or {})
            path = os.path.join(folder, 'wfm_{0:d}.npz'.format(n_file))
            b = bias_plan[c['bias']]
            np.savez(path, freq=np.asarray(freq, float), signal=np.asarray(signal, float),
                     config_index=ci, label=c['label'], group=c['group'], role=c['role'],
                     volts=np.asarray(c['volts'], float), daq=c['daq'],
                     gradient_mode=c['gradient_mode'], markers=np.asarray(c['markers'], int),
                     pulser_ch=markers_to_pulser_ch(c['markers']), sequence=seq_name,
                     bias=c['bias'], bias_currents=np.asarray(b['currents'], float),
                     bias_B_nv_MHz=np.asarray(b['B_nv'], float),
                     position_index=li, offset=float(offsets[li]), thermal=bool(thermal),
                     location=np.array([float(v) for v in locations[li].values()]),
                     location_axes=np.array(list(locations[li].keys())),
                     target=np.array([float(target[k]) for k in sorted(target)]),
                     target_axes=np.array(sorted(target)),
                     drift=np.array([float(drift[k]) for k in sorted(drift)]),
                     drift_axes=np.array(sorted(drift)),
                     elapsed_sweeps=info.get('elapsed_sweeps', -1),
                     equivalent_sweeps=info.get('equivalent_sweeps', np.nan),
                     mean_gated_rate_hz=info.get('mean_gated_rate_hz', np.nan),
                     run_duration_s=last_end - t0,
                     saved_at=time.strftime('%Y-%m-%d %H:%M:%S'))
            print(f"   #{n_file}: offset {offsets[li]:+.3f}{' (thermal)' if thermal else ''} "
                  f"-> {os.path.basename(path)} ({last_end - t0:.0f} s)")
            saved.append(path)
            n_file += 1
    finally:
        _zero_everything()
        print('wires and coils set to 0.')
    return saved


# =============================================================================
# Analysis
# =============================================================================

def load_mapping_folder(folder='WireFieldMapping'):
    pts = []
    for p in sorted(glob.glob(os.path.join(folder, 'wfm_*.npz')),
                    key=lambda p: int(re.findall(r'wfm_(\d+)\.npz$', p)[0])):
        with np.load(p, allow_pickle=False) as d:
            pt = {k: (d[k].item() if d[k].ndim == 0 else d[k]) for k in d.files}
        pt['path'] = p
        pts.append(pt)
    return pts


def analyze_wire_field_mapping(folder='WireFieldMapping', cal=None, skip_first_points=15,
                               D=None, plot=True, verbose=True):
    """
    Fit every spectrum for its field vector (NV frame of the coil calibration,
    all 8 lines; the symmetry image nearest the planned bias), then
    wire field = drive - reference of the same group at the same position.

    Returns (points, results): points with 'B' (MHz) added; results one dict
    per drive measurement: label, group, offset, volts, dB (MHz, NV frame),
    dB_G, dB_along_bias (MHz, component along the bias), dB_per_volt (MHz/V,
    by the largest drive voltage).
    """
    import coil_field_calibration as cfc
    import coil_linearity_analysis as cla
    cal = cal or cfc.load_coil_calibration()
    D = D or cal['D_MHz']
    nv = cla.NVAxes(cal['quantization_axes'])
    pts = load_mapping_folder(folder)
    for p in pts:
        B_bias = cfc.field_from_currents(p['bias_currents'], cal)
        seeds = [B_bias] + cla._perturbed_seeds(B_bias)
        r = cla.fit_spectrum_field(np.asarray(p['freq'], float) / 1e6, p['signal'], nv, seeds, D_seed=D,
                                   D_bounds=(D - 1e-3, D + 1e-3), skip_first_points=skip_first_points)
        imgs = nv.images(r['B'])
        p['B'] = imgs[np.argmin(np.linalg.norm(imgs - B_bias, axis=1))]
        p['B_se'] = np.sqrt(np.diag(r['cov_BD'][:3, :3]))
        p['fit_rms_noise'] = r['rms'] / r['noise']
        if verbose:
            print(f"{os.path.basename(p['path']):12s} {p['label']:26s} offset {p['offset']:+.2f}"
                  f"{' T' if p['thermal'] else '  '} |B| {np.linalg.norm(p['B']):7.2f} MHz "
                  f"(bias plan {np.linalg.norm(B_bias):.1f}), fit rms/noise {p['fit_rms_noise']:.2f}")
    results = []
    use = [p for p in pts if not p['thermal']]
    for p in use:
        if p['role'] != 'drive':
            continue
        refs = [q for q in use if q['role'] == 'reference' and q['group'] == p['group']
                and q['position_index'] == p['position_index']]
        if not refs:
            continue
        Bref = np.mean([q['B'] for q in refs], axis=0)
        dB = p['B'] - Bref
        bhat = Bref / np.linalg.norm(Bref)
        vmax = float(np.max(np.abs(p['volts'])))
        results.append({'label': p['label'], 'group': p['group'], 'offset': p['offset'],
                        'position_index': p['position_index'], 'volts': p['volts'],
                        'dB': dB, 'dB_G': dB / cal['gamma_MHz_per_G'],
                        'dB_se': np.sqrt(p['B_se'] ** 2 + np.mean([q['B_se'] for q in refs], 0) ** 2),
                        'dB_along_bias': float(dB @ bhat),
                        'dB_per_volt': dB / vmax if vmax > 0 else dB * np.nan})
    if verbose:
        print('\nwire field = drive - reference (MHz, calibration NV frame):')
        for r in results:
            print(f"  {r['label']:26s} offset {r['offset']:+.2f}: dB {np.round(r['dB'], 3)} "
                  f"(+- {np.round(r['dB_se'], 3)}), along bias {r['dB_along_bias']:+.3f} MHz")
    if plot and results:
        import matplotlib.pyplot as plt
        labels = list(dict.fromkeys(r['label'] for r in results))
        fig, axs = plt.subplots(1, len(labels), figsize=(3.6 * len(labels), 3.4), squeeze=False, sharey=True)
        for ax, lab in zip(axs[0], labels):
            rr = sorted([r for r in results if r['label'] == lab], key=lambda r: r['offset'])
            off = [r['offset'] for r in rr]
            for k, comp in enumerate('xyz'):
                ax.errorbar(off, [r['dB'][k] for r in rr], [r['dB_se'][k] for r in rr], marker='o', ms=3,
                            label=f'dB_{comp}')
            ax.plot(off, [r['dB_along_bias'] for r in rr], 'k--', label='along bias')
            ax.set_title(lab, fontsize=8); ax.set_xlabel('offset from centerline')
        axs[0][0].set_ylabel('wire field (MHz)'); axs[0][0].legend(fontsize=7)
        fig.tight_layout()
        plt.show()
    return pts, results
