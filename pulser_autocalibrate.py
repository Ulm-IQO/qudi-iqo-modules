# -*- coding: utf-8 -*-
"""
pulser_autocalibrate.py

Automation pipeline for calibrating NV magnetic-field pulsing wires using
CW ODMR: for a set of target locations (offset perpendicular to the gap
between two imaged wires) and a set of wire current/voltage settings, this
module runs CW ODMR at each combination and fits the resulting spectrum to
extract dip center frequencies (plus full fit parameters) for later
magnetic-field-calibration analysis.

This file assumes the following qudi objects are available as GLOBAL
variables in the calling namespace (as they would be if this file's
contents were pasted directly into a Jupyter notebook connected to a
running qudi instance):

    scanning_probe_logic   -- ScanningProbeLogic
    pulsed_master_logic    -- PulsedMasterLogic
    analog_output_logic    -- AnalogOutputLogic

If this file is instead `import`-ed as a module (recommended for reuse),
these names will NOT automatically be visible inside it, since Python
module globals are isolated per file. Before calling anything from this
module, inject the qudi objects into its namespace once, e.g.:

    import pulser_autocalibrate as autocal
    autocal.scanning_probe_logic = scanning_probe_logic
    autocal.pulsed_master_logic = pulsed_master_logic
    autocal.analog_output_logic = analog_output_logic

Drift correction (DriftCorrector class) lives separately in tracking.py
and is passed into run_field_mapping_experiment() as an already-configured
instance (with set_reference() already called) -- it is not duplicated
here.
"""

import time
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit, least_squares
from scipy.special import erf

from qudi.util.fit_models.lorentzian import DoubleLorentzian


# =============================================================================
# Generic helpers
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


# =============================================================================
# Wire current control (via AnalogOutputLogic / daq_1, daq_2)
# =============================================================================

def ensure_wire_outputs_active():
    """
    Activate all channels on both daq_1 and daq_2, so subsequent
    set_setpoint() calls take effect immediately. No-op for channels
    already active.
    """
    for device in ('daq_1', 'daq_2'):
        for ch in analog_output_logic.channels_for_device(device):
            if not analog_output_logic.get_activity_state(device, ch):
                analog_output_logic.set_activity_state(device, ch, True)


def set_wire_currents(currents, use_daq1=True):
    """
    Set the 4 wire voltages on either daq_1 (ao0..ao3) or daq_2 (ao0..ao3),
    zeroing the OTHER daq's channels.

    Parameters
    ----------
    currents : sequence of 4 floats
        Voltages to apply to ao0, ao1, ao2, ao3 (in wire order) on the
        active daq.
    use_daq1 : bool
        If True, apply `currents` to daq_1 and zero daq_2.
        If False, apply `currents` to daq_2 and zero daq_1.
    """
    if len(currents) != 4:
        raise ValueError(f'currents must have exactly 4 values, got {len(currents)}.')

    device_on = 'daq_1' if use_daq1 else 'daq_2'
    device_off = 'daq_2' if use_daq1 else 'daq_1'
    channels = ('ao0', 'ao1', 'ao2', 'ao3')

    for ch, value in zip(channels, currents):
        analog_output_logic.set_setpoint(device_on, ch, float(value))
    for ch in channels:
        analog_output_logic.set_setpoint(device_off, ch, 0.0)


# =============================================================================
# PulseBlaster-only laser bypass (fast confocal scanning without disturbing
# the loaded AWG+PulseBlaster experiment sequence)
# =============================================================================

def _get_pulseblaster_hw():
    """
    Drill down through pulsed_master_logic's connectors to obtain a direct
    reference to the raw PulseBlaster hardware module, bypassing the AWG
    entirely. Also returns the AwgPulseBlasterInterfuse instance itself,
    since its channel-mapping helpers (_is_pb_d_ch, _d_ch_to_pb_hw) are
    reused here to correctly translate the qudi-facing laser channel name
    into the PulseBlaster module's own zero-based channel key.

    Confirmed connector chain:
      pulsed_master_logic.sequencegeneratorlogic() -> SequenceGeneratorLogic
      SequenceGeneratorLogic.pulsegenerator()      -> AwgPulseBlasterInterfuse
      AwgPulseBlasterInterfuse.pulseblaster()      -> raw PulseBlaster hw module
    """
    seq_gen = pulsed_master_logic.sequencegeneratorlogic()
    interfuse = seq_gen.pulsegenerator()
    pb_hw = interfuse.pulseblaster()
    return pb_hw, interfuse


def _laser_on_for_scanning(laser_channel=None, laser_length=3.0e-6, timeout=30.0):
    """
    Stop any running pulsed measurement/pulser, then load+run a constant
    'laser on' waveform directly on the PulseBlaster (bypassing the AWG
    entirely), so confocal scanning/drift-correction has a photon signal
    to work with -- without needing to regenerate/reload the full
    AWG+PulseBlaster ODMR sequence in between drift-correction cycles.

    Parameters
    ----------
    laser_channel : str, optional
        The qudi-facing digital channel name driving the laser (e.g.
        'd_ch9'). If None, taken from
        pulsed_master_logic.generation_parameters['laser_channel'].
    laser_length : float
        Duration (s) of one repetition of the HIGH pulse programmed onto
        the PulseBlaster. Since the pattern loops continuously once
        started, the laser remains effectively continuously on regardless
        of this value.
    timeout : float
        Timeout (s) for waiting on measurement/pulser stop.
    """
    if pulsed_master_logic.status_dict['measurement_running']:
        pulsed_master_logic.toggle_pulsed_measurement(False)
        _wait_while(lambda: pulsed_master_logic.status_dict['measurement_running'],
                    'measurement to stop', timeout)

    if pulsed_master_logic.status_dict['pulser_running']:
        pulsed_master_logic.toggle_pulse_generator(False)
        _wait_while(lambda: pulsed_master_logic.status_dict['pulser_running'],
                    'pulse generator to stop', timeout)

    pb_hw, interfuse = _get_pulseblaster_hw()

    if laser_channel is None:
        laser_channel = pulsed_master_logic.generation_parameters.get('laser_channel')
        if not laser_channel:
            raise ValueError('No laser_channel found; pass laser_channel explicitly.')

    if not interfuse._is_pb_d_ch(laser_channel):
        raise ValueError(f'Laser channel "{laser_channel}" is not a PulseBlaster channel.')

    pb_hw_channel = 'd_ch{0:d}'.format(interfuse._d_ch_to_pb_hw(laser_channel))

    if pb_hw.get_status()[0] != 0:
        pb_hw.pulser_off()

    pb_constraints = pb_hw.get_constraints()
    sample_rate = pb_hw.get_sample_rate() or pb_constraints.sample_rate.default
    n_samples = max(int(pb_constraints.waveform_length.min), int(round(laser_length * sample_rate)))

    pb_name = 'laser_on_direct'
    written, _ = pb_hw.write_waveform(pb_name, {}, {pb_hw_channel: np.ones(n_samples, dtype=bool)},
                                      True, True, n_samples)
    if written < 0:
        raise RuntimeError('Failed to write laser-on waveform to PulseBlaster.')

    pb_hw.load_waveform([pb_name])
    pb_hw.pulser_on()


# =============================================================================
# CW ODMR execution and double-Lorentzian dip fitting
# =============================================================================

def _extract_lmfit_params(fit_result):
    """
    Extract every fit parameter's value, standard error, and bounds from an
    lmfit ModelResult into a plain, picklable dict of dicts:

        {param_name: {'value': ..., 'stderr': ..., 'min': ..., 'max': ..., 'vary': ...}}

    Also includes a few overall fit-quality metrics under the '_meta' key
    (chisqr, redchi, success, message).
    """
    params_dict = {}
    for name, param in fit_result.params.items():
        params_dict[name] = {
            'value': float(param.value) if param.value is not None else None,
            'stderr': float(param.stderr) if param.stderr is not None else None,
            'min': float(param.min) if param.min is not None else None,
            'max': float(param.max) if param.max is not None else None,
            'vary': bool(param.vary),
        }

    params_dict['_meta'] = {
        'chisqr': float(fit_result.chisqr) if fit_result.chisqr is not None else None,
        'redchi': float(fit_result.redchi) if fit_result.redchi is not None else None,
        'success': bool(fit_result.success),
        'message': str(fit_result.message) if fit_result.message else '',
    }
    return params_dict


def fit_double_lorentzian_dips(freq, signal):
    """
    Fit two Lorentzian dips to CW ODMR data.

    NOTE: this function does NOT return the raw (freq, signal) trace --
    only the fit results. Callers (e.g. run_field_mapping_experiment)
    already have freq/signal from the acquisition step and are
    responsible for storing them alongside these fit results if needed.

    Parameters
    ----------
    freq : ndarray
        Frequency axis (Hz).
    signal : ndarray
        Measured ODMR signal (e.g. normalized counts) at each frequency.

    Returns
    -------
    fit_result : lmfit ModelResult
        The raw fit result object (contains .best_fit, .params, etc.).
    dips : tuple of two dicts (dip_lo, dip_hi)
        Sorted so dip_lo has the lower center frequency. Each dict has
        keys 'center', 'amplitude', 'sigma', each mapping to
        {'value': ..., 'stderr': ...}.
    """
    if not np.any(signal) or np.ptp(signal) == 0:
        raise RuntimeError(
            f'ODMR signal data is degenerate (all zeros or constant; '
            f'min={np.min(signal):.3g}, max={np.max(signal):.3g}). Cannot fit.'
        )

    model = DoubleLorentzian()
    params = model.estimate_dips(signal, freq)
    fit_result = model.fit(signal, params, x=freq)

    def _param_pair(suffix):
        p = fit_result.params
        return {
            'center': {
                'value': float(p[f'center_{suffix}'].value),
                'stderr': float(p[f'center_{suffix}'].stderr) if p[f'center_{suffix}'].stderr is not None else None,
            },
            'amplitude': {
                'value': float(p[f'amplitude_{suffix}'].value),
                'stderr': float(p[f'amplitude_{suffix}'].stderr) if p[f'amplitude_{suffix}'].stderr is not None else None,
            },
            'sigma': {
                'value': float(p[f'sigma_{suffix}'].value),
                'stderr': float(p[f'sigma_{suffix}'].stderr) if p[f'sigma_{suffix}'].stderr is not None else None,
            },
        }

    dip_1 = _param_pair('1')
    dip_2 = _param_pair('2')

    # Sort the two dips by center frequency, keeping each dip's
    # center/amplitude/sigma bundled together consistently.
    if dip_1['center']['value'] <= dip_2['center']['value']:
        dip_lo, dip_hi = dip_1, dip_2
    else:
        dip_lo, dip_hi = dip_2, dip_1

    return fit_result, (dip_lo, dip_hi)


def _run_odmr_sequence_and_collect(seq_name, num_sweeps, poll_interval=0.5,
                                   timeout=None, stall_timeout=120.0,
                                   min_sweeps_settle_time=6.0):
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
        an error rather than waiting indefinitely (catches a fast counter
        that is not actually reporting sweep counts).
    min_sweeps_settle_time : float
        Time (s) to wait after starting the measurement before beginning
        to poll elapsed_sweeps. This must be AT LEAST as long as
        pulsed_master_logic.timer_interval (ideally 1.5-2x it), since
        elapsed_sweeps is only recomputed periodically by the analysis
        timer, not synchronously when the measurement starts -- without
        this settle time, elapsed_sweeps can briefly still reflect a
        PREVIOUS measurement's final count right after starting a new one,
        causing the polling loop to exit before genuinely new data has
        been collected.

    Returns
    -------
    freq : ndarray
    signal : ndarray
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

    # Give the analysis timer time to run at least once and genuinely reset
    # elapsed_sweeps for THIS measurement before trusting it.
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
    freq = _to_plain_array(data[0])
    signal = _to_plain_array(data[1])

    if not np.any(signal):
        raise RuntimeError(
            f'ODMR signal for "{seq_name}" is all zeros after waiting for '
            f'{pulsed_master_logic.elapsed_sweeps} sweeps. Check hardware / '
            f'counter wiring, or increase min_sweeps_settle_time if this '
            f'happens intermittently right after starting a measurement.'
        )

    return freq, signal


# =============================================================================
# Scanner target movement helper (xy-only, leaving z untouched)
# =============================================================================

def move_xy_only(drift_corrector, location_xy):
    """
    Move the scanner's x/y to location_xy + drift_corrector.total_drift,
    WITHOUT touching z (keeps whatever z is currently set, e.g. from the
    most recent DriftCorrector.correct_drift() z-optimization).

    Parameters
    ----------
    drift_corrector : DriftCorrector
        Must have already run correct_drift() at least once (so
        total_drift is populated).
    location_xy : dict or tuple/list
        Target (un-drift-corrected) xy position, either as {axis: value}
        or as a plain (x, y) tuple matching drift_corrector.xy_axes order.

    Returns
    -------
    dict : the resulting scanner target position.
    """
    xy_axes = drift_corrector.xy_axes

    if not isinstance(location_xy, dict):
        location_xy = {ax: val for ax, val in zip(xy_axes, location_xy)}

    xy_target = {
        ax: drift_corrector._clip_position(ax, location_xy[ax] + drift_corrector.total_drift[ax])
        for ax in xy_axes
    }

    target_pos = dict(scanning_probe_logic.scanner_target)
    target_pos.update(xy_target)
    target_pos = {ax: drift_corrector._to_float(v) for ax, v in target_pos.items()}
    scanning_probe_logic.set_target_position(target_pos, move_blocking=True)

    return target_pos


# =============================================================================
# Top-level field-mapping experiment orchestrator
# =============================================================================

def run_field_mapping_experiment(locations, wire_settings, use_daq1, odmr_kwargs,
                                 num_sweeps, drift_corrector, seq_name='cw_odmr_gradient_meas'):
    """
    Full field-mapping pipeline: for every (location, wire_setting) pair,
    set the wire currents, drift-correct (xy cross-correlation + z
    optimization AT THE REFERENCE TARGET), move to the desired location's
    (x, y) + total drift (z left as-is, no re-optimization there), run the
    CW ODMR gradient sequence for num_sweeps sweeps, and fit two Lorentzian
    dips.

    Parameters
    ----------
    locations : list of (x, y) tuples or dicts
        Target xy positions (within the shared reference xy scan's frame).
        See compute_target_locations() for a way to generate these
        automatically from imaged wire geometry.
    wire_settings : list of 4-tuples
        Each tuple is (V1, V2, V3, V4) -- voltages for ao0..ao3 on
        whichever daq is active (see use_daq1).
    use_daq1 : bool
        True to apply every entry in wire_settings via daq_1 (zeroing
        daq_2) for this entire run; False for the reverse. Fixed for the
        whole run since the ODMR sequence/settings are generated once and
        depend on which daq is being used -- run this function again with
        the other value (and correspondingly adjusted odmr_kwargs) for the
        other daq.
    odmr_kwargs : dict
        Keyword arguments for generate_bd_cw_odmr_gradient (freq_start,
        freq_stop, num_of_points, mw_amp, mw_length, etc.) EXCLUDING 'name'.
    num_sweeps : int
        Number of sweeps to average before stopping each measurement.
    drift_corrector : DriftCorrector
        Must already have set_reference() called (see tracking.py).
    seq_name : str
        Name to use for the generated sequence.

    Returns
    -------
    list of dict
        One entry per (location, wire_setting) pair, each containing:
        location_index, location_xy, wire_index, wire_currents, use_daq1,
        drift, target_position, freq, signal, dip_lo, dip_hi, fit_params,
        fit_result.
    """
    ensure_wire_outputs_active()

    # Generate + sample + load the ODMR sequence ONCE (fixed frequency sweep
    # pattern -- current/B-field changes don't require regenerating it).
    gen_kwargs = dict(odmr_kwargs)
    gen_kwargs['name'] = seq_name
    pulsed_master_logic.generate_predefined_sequence(
        'bd_cw_odmr_gradient', gen_kwargs, sample_and_load=True
    )
    _wait_while(lambda: pulsed_master_logic.status_dict['sampload_busy'],
                f'"{seq_name}" to generate/sample/load', 300.0)

    if pulsed_master_logic.loaded_asset[0] != seq_name:
        raise RuntimeError(
            f'Failed to generate/load "{seq_name}"; loaded asset is '
            f'{pulsed_master_logic.loaded_asset}. Check the qudi log.'
        )

    results = []

    for loc_idx, location_xy in enumerate(locations):
        for wire_idx, currents in enumerate(wire_settings):
            print(f'--- Location {loc_idx} ({location_xy}), wire setting {wire_idx} ({currents}) ---')

            # 1. Set wire currents
            set_wire_currents(currents, use_daq1=use_daq1)

            # 2. Drift correction: xy cross-correlation + z optimization AT reference_target
            _laser_on_for_scanning()
            drift, ref_target = drift_corrector.correct_drift()
            print(f'  Drift: {drift}, reference target after correction: {ref_target}')

            # 3. Move to this location's xy + drift, keeping z from step 2 as-is
            target_pos = move_xy_only(drift_corrector, location_xy)
            print(f'  Moved to location target: {target_pos}')

            # 4. Run ODMR
            freq, signal = _run_odmr_sequence_and_collect(seq_name, num_sweeps)

            # 5. Fit two Lorentzian dips
            fit_result, (dip_lo, dip_hi) = fit_double_lorentzian_dips(freq, signal)
            fit_params = _extract_lmfit_params(fit_result)

            print(f'  Dip centers (Hz): {dip_lo["center"]["value"]:.4e} '
                  f'(+/- {dip_lo["center"]["stderr"]}), '
                  f'{dip_hi["center"]["value"]:.4e} (+/- {dip_hi["center"]["stderr"]})')

            results.append({
                'location_index': loc_idx,
                'location_xy': dict(zip(drift_corrector.xy_axes, location_xy)) if not isinstance(location_xy, dict) else dict(location_xy),
                'wire_index': wire_idx,
                'wire_currents': tuple(currents),
                'use_daq1': use_daq1,
                'drift': dict(drift),
                'target_position': dict(target_pos),
                'freq': freq,
                'signal': signal,
                'dip_lo': dip_lo,
                'dip_hi': dip_hi,
                'fit_params': fit_params,
                'fit_result': fit_result,
            })

    return results


# =============================================================================
# Wire geometry: tilted wire-edge fitting and perpendicular target-location
# computation (used to automatically determine the 5 target locations from
# an imaged reference scan of the two pulsing wires)
# =============================================================================

def _extract_y_profile(xy_scan, x_position, x_window, channel, x_axis, y_axis):
    """
    Extract a 1D intensity profile along y_axis from a 2D reference scan,
    averaged over a small window in x_axis centered at x_position (or the
    single nearest column if x_window is None).

    Assumes the scan data array's dimensions correspond, in order, to
    xy_scan.settings.axes (i.e. axis i of the array has length
    xy_scan.settings.resolution[i] and spans xy_scan.settings.range[i]) --
    consistent with the 'ij'-indexing convention used elsewhere in this
    codebase (e.g. ScanningOptimizeLogic).

    Returns
    -------
    y_positions : ndarray
    profile : ndarray
    """
    axes = tuple(xy_scan.settings.axes)
    if x_axis not in axes or y_axis not in axes:
        raise ValueError(f'xy_scan axes {axes} do not contain both "{x_axis}" and "{y_axis}".')

    x_idx = axes.index(x_axis)
    y_idx = axes.index(y_axis)

    x_range = xy_scan.settings.range[x_idx]
    y_range = xy_scan.settings.range[y_idx]
    x_res = xy_scan.settings.resolution[x_idx]
    y_res = xy_scan.settings.resolution[y_idx]

    x_positions = np.linspace(x_range[0], x_range[1], x_res)
    y_positions = np.linspace(y_range[0], y_range[1], y_res)

    img = _to_plain_array(xy_scan.data[channel])
    if img.shape[x_idx] != x_res or img.shape[y_idx] != y_res:
        raise ValueError(
            f'Unexpected data shape {img.shape} for scan axes {axes} with '
            f'resolutions {xy_scan.settings.resolution}.'
        )

    # Move the x axis to the front so we can average over it uniformly,
    # regardless of whether x is array dimension 0 or 1.
    img_x_first = np.moveaxis(img, x_idx, 0)

    if x_window is None or x_window <= 0:
        col_idx = int(np.argmin(np.abs(x_positions - x_position)))
        profile = img_x_first[col_idx:col_idx + 1].mean(axis=0)
    else:
        x_lo = x_position - x_window / 2.0
        x_hi = x_position + x_window / 2.0
        col_mask = (x_positions >= x_lo) & (x_positions <= x_hi)
        if not np.any(col_mask):
            raise ValueError(
                f'No scan columns found within x_window={x_window} of x_position={x_position}.'
            )
        profile = img_x_first[col_mask].mean(axis=0)

    return y_positions, profile


def _refine_edge_position(xy_scan, x_position, y_guess, channel='Sum', x_axis='x', y_axis='y',
                          x_window=None, search_half_width=2.0):
    """
    Locally fit a single blurred step-edge model (error function) around
    a rough (x_position, y_guess) estimate of a wire edge, to precisely
    locate the true edge position.

    Only the data within +/- search_half_width of y_guess is used, so
    brightness variance elsewhere inside the wire (outside this local
    window) has no effect -- this fits only the local wire<->background
    transition, not the wire's interior brightness.

    Parameters
    ----------
    xy_scan : ScanData
    x_position : float
    y_guess : float
        Rough estimate (with expected error, e.g. from blurring) of the
        edge's y-position at this x_position.
    channel : str
    x_axis, y_axis : str
    x_window : float, optional
        Averaging window in x for profile extraction.
    search_half_width : float
        Half-width of the local fitting window around y_guess. Should be
        smaller than roughly half the wire width / gap width, so the
        window captures only ONE edge transition, not two.

    Returns
    -------
    y0 : float
        Refined edge position.
    y0_err : float or None
        Estimated 1-sigma uncertainty on y0 (from the fit covariance), or
        None if unavailable.
    diagnostics : dict
        {'y_local', 'profile_local', 'fit_curve_local', 'popt'} for
        plotting/inspection.
    """
    y_positions, profile = _extract_y_profile(xy_scan, x_position, x_window, channel, x_axis, y_axis)

    mask = (y_positions >= y_guess - search_half_width) & (y_positions <= y_guess + search_half_width)
    if mask.sum() < 5:
        raise ValueError(
            f'Not enough data points ({mask.sum()}) within search_half_width='
            f'{search_half_width} of y_guess={y_guess} at x={x_position}.'
        )

    y_local = y_positions[mask]
    p_local = profile[mask]

    offset_guess = float(np.mean(p_local))
    amplitude_guess = float(p_local[-1] - p_local[0]) / 2.0
    if amplitude_guess == 0:
        amplitude_guess = (float(np.max(p_local)) - float(np.min(p_local))) / 2.0 + 1e-9
    sigma_guess = max(1e-3, search_half_width / 3.0)

    def _edge_model(y, offset, amplitude, y0, sigma):
        return offset + amplitude * erf((y - y0) / (np.sqrt(2) * sigma))

    p0 = [offset_guess, amplitude_guess, y_guess, sigma_guess]
    bounds = (
        [-np.inf, -np.inf, y_local[0], 1e-3],
        [np.inf, np.inf, y_local[-1], 2 * search_half_width],
    )

    try:
        popt, pcov = curve_fit(_edge_model, y_local, p_local, p0=p0, bounds=bounds, maxfev=10000)
        y0 = float(popt[2])
        y0_err = float(np.sqrt(pcov[2, 2])) if pcov is not None and np.isfinite(pcov[2, 2]) else None
    except Exception:
        scanning_probe_logic.log.warning(
            f'Edge fit failed at x={x_position}, y_guess={y_guess}; using rough estimate as-is.'
        )
        popt = np.array(p0)
        y0 = float(y_guess)
        y0_err = None

    diagnostics = {
        'y_local': y_local,
        'profile_local': p_local,
        'fit_curve_local': _edge_model(y_local, *popt),
        'popt': popt,
    }
    return y0, y0_err, diagnostics


def _fit_line_through_points(points):
    """
    Fit a line y = slope * x + intercept through a list of
    (x, y, y_err) tuples. Uses exact 2-point solution if len==2, or
    (optionally error-)weighted least squares otherwise.
    """
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    errs = np.array([p[2] if (len(p) > 2 and p[2]) else 1.0 for p in points], dtype=float)

    if len(xs) < 2:
        raise ValueError('Need at least 2 points to fit a line.')

    if len(xs) == 2:
        slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
        intercept = ys[0] - slope * xs[0]
        return float(slope), float(intercept)

    weights = 1.0 / np.clip(errs, 1e-9, None) ** 2
    A = np.vstack([xs, np.ones_like(xs)]).T
    Wsqrt = np.sqrt(weights)[:, None]
    slope, intercept = np.linalg.lstsq(A * Wsqrt, ys * Wsqrt[:, 0], rcond=None)[0]
    return float(slope), float(intercept)


def fit_tilted_wire_gap(xy_scan, wire_estimates, channel='Sum', x_axis='x', y_axis='y',
                        x_window=None, search_half_width=2.0,
                        expected_wire_width=None, expected_gap_width=None,
                        plot=False):
    """
    Fit a line to each wire's top and bottom edges (from user-provided
    rough estimates at multiple x-locations), then compute the gap-center
    position as a function of x -- correctly accounting for a small tilt
    of the wires relative to x_axis, and robust to brightness variance
    within the wires (each edge is fit LOCALLY, near the user's estimate,
    so interior wire brightness elsewhere never enters the fit).

    Parameters
    ----------
    xy_scan : ScanData
        Reference 2D xy scan containing the two wire shadows.
    wire_estimates : sequence of 2 wires
        Each wire is a sequence of >=2 rough estimates:
        [(x1, y_top_guess1, y_bottom_guess1), (x2, y_top_guess2, y_bottom_guess2), ...]
        i.e. for each wire, at 2+ different x-locations, a rough estimate
        of that wire's top and bottom y-position (with expected error due
        to blurring -- these are only STARTING guesses for the local edge
        fits, not required to be precise).
    channel : str
    x_axis, y_axis : str
        Names of the scan axes corresponding to x and y.
    x_window : float, optional
        Averaging window in x for profile extraction (reduces noise).
    search_half_width : float
        Half-width of the local search window used to refine each rough
        edge estimate (see _refine_edge_position). Should be smaller than
        roughly half the wire width / gap width.
    expected_wire_width, expected_gap_width : float, optional
        If given, used only as a DIAGNOSTIC consistency check (logs a
        warning if the fitted wire width / gap width deviates
        significantly) -- not enforced as a constraint on the fit.
    plot : bool
        If True, show the 2D reference scan with the refined edge points
        and fitted lines (including the gap-center line) overlaid, for
        visual verification.

    Returns
    -------
    gap_center_func : callable
        gap_center_func(x) -> fitted y-position of the gap center at that
        x, accounting for wire tilt.
    fit_info : dict
        Diagnostic information: per-wire refined edge points and fitted
        lines, which wire was identified as upper/lower, and the overall
        gap-center line (slope, intercept).
    """
    if len(wire_estimates) != 2:
        raise ValueError(f'wire_estimates must contain exactly 2 wires, got {len(wire_estimates)}.')

    wires_fit = []
    for wire_idx, points in enumerate(wire_estimates):
        if len(points) < 2:
            raise ValueError(
                f'Wire {wire_idx} must have at least 2 (x, y_top, y_bottom) estimates '
                f'to fit a line; got {len(points)}.'
            )

        top_points, bottom_points = [], []
        for (x, y_top_guess, y_bottom_guess) in points:
            y0_top, err_top, _ = _refine_edge_position(
                xy_scan, x, y_top_guess, channel, x_axis, y_axis, x_window, search_half_width
            )
            y0_bot, err_bot, _ = _refine_edge_position(
                xy_scan, x, y_bottom_guess, channel, x_axis, y_axis, x_window, search_half_width
            )
            top_points.append((x, y0_top, err_top))
            bottom_points.append((x, y0_bot, err_bot))

        top_line = _fit_line_through_points(top_points)
        bottom_line = _fit_line_through_points(bottom_points)
        mean_y = float(np.mean([p[1] for p in top_points] + [p[1] for p in bottom_points]))

        wires_fit.append({
            'top_points': top_points,
            'bottom_points': bottom_points,
            'top_line': top_line,
            'bottom_line': bottom_line,
            'mean_y': mean_y,
        })

    # Identify upper (higher y) vs. lower wire.
    if wires_fit[0]['mean_y'] >= wires_fit[1]['mean_y']:
        upper_idx, lower_idx = 0, 1
    else:
        upper_idx, lower_idx = 1, 0
    upper, lower = wires_fit[upper_idx], wires_fit[lower_idx]

    # Inner edges: upper wire's BOTTOM edge, lower wire's TOP edge.
    inner_upper_slope, inner_upper_intercept = upper['bottom_line']
    inner_lower_slope, inner_lower_intercept = lower['top_line']

    gap_slope = (inner_upper_slope + inner_lower_slope) / 2.0
    gap_intercept = (inner_upper_intercept + inner_lower_intercept) / 2.0

    def gap_center_func(x):
        return gap_slope * x + gap_intercept

    fit_info = {
        'wires_fit': wires_fit,
        'upper_wire_index': upper_idx,
        'lower_wire_index': lower_idx,
        'inner_upper_line': (inner_upper_slope, inner_upper_intercept),
        'inner_lower_line': (inner_lower_slope, inner_lower_intercept),
        'gap_center_line': (gap_slope, gap_intercept),
    }

    # --- diagnostic consistency checks (optional) ---
    if expected_wire_width is not None or expected_gap_width is not None:
        sample_xs = [p[0] for p in wire_estimates[0]]
        for x in sample_xs:
            upper_width = (upper['top_line'][0] * x + upper['top_line'][1]) - \
                          (upper['bottom_line'][0] * x + upper['bottom_line'][1])
            lower_width = (lower['bottom_line'][0] * x + lower['bottom_line'][1]) - \
                          (lower['top_line'][0] * x + lower['top_line'][1])
            gap = (inner_upper_slope * x + inner_upper_intercept) - \
                  (inner_lower_slope * x + inner_lower_intercept)

            if expected_wire_width is not None:
                for label, w in (('upper', upper_width), ('lower', lower_width)):
                    if abs(w - expected_wire_width) > 0.3 * expected_wire_width:
                        scanning_probe_logic.log.warning(
                            f'{label} wire width at x={x} is {w:.2f}, expected '
                            f'~{expected_wire_width}; check estimates/fit quality.'
                        )
            if expected_gap_width is not None and abs(gap - expected_gap_width) > 0.3 * expected_gap_width:
                scanning_probe_logic.log.warning(
                    f'Gap width at x={x} is {gap:.2f}, expected ~{expected_gap_width}; '
                    f'check estimates/fit quality.'
                )

    if plot:
        _plot_wire_fit_overview(xy_scan, fit_info, channel, x_axis, y_axis)

    return gap_center_func, fit_info


def _plot_wire_fit_overview(xy_scan, fit_info, channel, x_axis, y_axis,
                            anchor=None, locations=None):
    """
    Overlay refined edge points and fitted lines (per wire, plus the
    resulting gap-center line) on top of the 2D reference scan image.
    Optionally also shows the anchor point and perpendicular-offset
    target locations.
    """
    img = _to_plain_array(xy_scan.data[channel])
    axes = tuple(xy_scan.settings.axes)
    x_idx = axes.index(x_axis)
    y_idx = axes.index(y_axis)
    x_range = xy_scan.settings.range[x_idx]
    y_range = xy_scan.settings.range[y_idx]

    img_disp = np.moveaxis(img, [y_idx, x_idx], [0, 1])  # -> shape (ny, nx)

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(img_disp, extent=[x_range[0], x_range[1], y_range[0], y_range[1]],
             origin='lower', aspect='auto', cmap='gray')

    x_plot = np.linspace(x_range[0], x_range[1], 200)
    colors = ['tab:orange', 'tab:cyan']

    for wire_idx, wire in enumerate(fit_info['wires_fit']):
        color = colors[wire_idx % len(colors)]
        top_x = [p[0] for p in wire['top_points']]
        top_y = [p[1] for p in wire['top_points']]
        bot_x = [p[0] for p in wire['bottom_points']]
        bot_y = [p[1] for p in wire['bottom_points']]

        ax.plot(top_x, top_y, 'o', color=color, markersize=7, label=f'wire {wire_idx} top (fit)')
        ax.plot(bot_x, bot_y, 's', color=color, markersize=7, label=f'wire {wire_idx} bottom (fit)')

        top_slope, top_intercept = wire['top_line']
        bot_slope, bot_intercept = wire['bottom_line']
        ax.plot(x_plot, top_slope * x_plot + top_intercept, '--', color=color, linewidth=1.2)
        ax.plot(x_plot, bot_slope * x_plot + bot_intercept, '--', color=color, linewidth=1.2)

    gap_slope, gap_intercept = fit_info['gap_center_line']
    ax.plot(x_plot, gap_slope * x_plot + gap_intercept, '-', color='yellow', linewidth=2,
           label='gap center')

    if anchor is not None:
        ax.plot(anchor[x_axis], anchor[y_axis], '*', color='red', markersize=16,
               label='anchor (x_given, y_center)')

    if locations is not None:
        loc_x = [loc[x_axis] for loc in locations]
        loc_y = [loc[y_axis] for loc in locations]
        ax.plot(loc_x, loc_y, 'P', color='lime', markersize=10, label='target locations')

    ax.set_xlabel(x_axis)
    ax.set_ylabel(y_axis)
    ax.legend(loc='upper right', fontsize=8, framealpha=0.8)
    ax.set_title('Wire edge fits, gap-center line, and target locations')
    plt.show()


def compute_target_locations(xy_scan, x_position, relative_offsets, wire_estimates,
                             channel='Sum', x_axis='x', y_axis='y', x_window=None,
                             search_half_width=2.0, expected_wire_width=None,
                             expected_gap_width=None, plot=False):
    """
    Fit tilted wire edges (see fit_tilted_wire_gap) and compute a list of
    target (x, y) locations by starting at the fitted gap center
    (evaluated at x_position) and moving PERPENDICULAR to the gap-center
    line by each value in relative_offsets -- rather than purely along
    the y-axis -- so that offsets correspond to true "across the gap"
    distances even when the wires (and hence the gap-center line) are
    tilted relative to the x-axis.

    Parameters
    ----------
    xy_scan : ScanData
    x_position : float
        The x-coordinate at which the gap center is evaluated (the
        "anchor" point that all target locations are offset from, along
        the perpendicular direction).
    relative_offsets : sequence of float
        Distances (same units as the scan axes) to move away from
        (x_position, y_center) along the direction PERPENDICULAR to the
        fitted gap-center line. Positive values move in the direction
        that corresponds to increasing y when the line's tilt is small.
    wire_estimates, channel, x_axis, y_axis, x_window, search_half_width,
    expected_wire_width, expected_gap_width, plot :
        Passed through to fit_tilted_wire_gap().

    Returns
    -------
    locations : list of dict
        [{x_axis: x_t, y_axis: y_t} for each offset], where (x_t, y_t) is
        the point obtained by moving `offset` along the perpendicular
        direction from the anchor point (x_position, y_center). Directly
        usable as the `locations` argument to run_field_mapping_experiment().
    anchor : dict
        {x_axis: x_position, y_axis: y_center} -- the un-offset gap-center
        point, provided for reference/plotting.
    fit_info : dict
        Diagnostic fit information from fit_tilted_wire_gap(), with an
        added 'perp_direction' key: the (dx, dy) unit vector used for the
        offsets.
    """
    gap_center_func, fit_info = fit_tilted_wire_gap(
        xy_scan, wire_estimates, channel=channel, x_axis=x_axis, y_axis=y_axis,
        x_window=x_window, search_half_width=search_half_width,
        expected_wire_width=expected_wire_width, expected_gap_width=expected_gap_width,
        plot=False,  # plot below instead, so we can also show the offset points
    )

    y_center = float(gap_center_func(x_position))
    slope, _ = fit_info['gap_center_line']

    # Perpendicular unit vector to the line with slope m: direction (1, m)
    # along the line -> perpendicular direction (-m, 1), normalized.
    # Sign chosen so that for small slopes, positive offsets move in the
    # +y direction (matching the original, non-tilted behavior).
    norm = np.sqrt(1.0 + slope ** 2)
    perp_dx = -slope / norm
    perp_dy = 1.0 / norm

    locations = []
    for offset in relative_offsets:
        x_t = x_position + offset * perp_dx
        y_t = y_center + offset * perp_dy
        locations.append({x_axis: float(x_t), y_axis: float(y_t)})

    anchor = {x_axis: float(x_position), y_axis: float(y_center)}
    fit_info['perp_direction'] = (float(perp_dx), float(perp_dy))

    scanning_probe_logic.log.info(
        f'Computed tilted-wire gap center at x={x_position}: anchor={anchor}, '
        f'perpendicular direction=({perp_dx:.4f}, {perp_dy:.4f}); '
        f'target locations: {locations}'
    )

    if plot:
        _plot_wire_fit_overview(xy_scan, fit_info, channel, x_axis, y_axis,
                                anchor=anchor, locations=locations)

    return locations, anchor, fit_info