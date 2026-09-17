# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.5
#   kernelspec:
#     display_name: qudi
#     language: python
#     name: qudi
# ---

# %%
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


# %%
def ensure_wire_outputs_active():
    """Activate all channels on both daq_1 and daq_2, so set_setpoint() calls
    take effect immediately (no-op for channels already active)."""
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


# %%
import numpy as np


def _to_plain_array(data):
    """Convert to a plain float ndarray, working around array-likes
    (e.g. pint Quantities) whose __array__() doesn't support the
    NumPy 2.0 dtype/copy keyword protocol."""
    if hasattr(data, 'magnitude'):
        data = data.magnitude
    arr = np.array(data)
    return arr.astype(float, copy=False)


def _get_pulseblaster_hw():
    seq_gen = pulsed_master_logic.sequencegeneratorlogic()
    interfuse = seq_gen.pulsegenerator()
    pb_hw = interfuse.pulseblaster()
    return pb_hw, interfuse


def _wait_while(condition_fn, description, timeout, poll_interval=0.2):
    t_start = time.time()
    while condition_fn():
        if time.time() - t_start > timeout:
            raise TimeoutError(f'Timed out waiting for {description}.')
        time.sleep(poll_interval)


def _laser_on_for_scanning(laser_channel=None, laser_length=3.0e-6, timeout=30.0):
    """Stop any running pulsed measurement/pulser, then load+run 'laser_on'
    directly on the PulseBlaster (bypassing the AWG), so confocal scanning
    has a photon signal to work with."""
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


# %%
from qudi.util.fit_models.lorentzian import DoubleLorentzian


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
    Fit two Lorentzian dips to ODMR data.

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
                                   timeout=None, stall_timeout=120.0, min_sweeps_settle_time=5.0):
    """
    Reload the (already generated+sampled+loaded) ODMR PulseSequence
    (fast -- PB-only re-write, AWG untouched), run it until num_sweeps
    completed sweeps are reported, then stop and return (freq, signal).

    NOTE: relies on pulsed_master_logic.elapsed_sweeps being correctly
    reported by the fast-counter hardware. If this value never increases,
    a stall-timeout error is raised instead of hanging forever -- see
    "Assumptions to verify" in the accompanying explanation.
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
    # elapsed_sweeps for THIS measurement, before trusting it. Without this,
    # elapsed_sweeps can still reflect the tail end of a PREVIOUS
    # measurement for a short window after start_pulsed_measurement()
    # returns (it's only actually recomputed inside the periodic
    # _pulsed_analysis_loop(), not synchronously on start).
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
                f'(stuck at {current_sweeps}).'
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


# %%
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
    locations : list of (x, y) tuples
        Target xy positions (within the shared reference xy scan's frame).
    wire_settings : list of 4-tuples
        Each tuple is (I1, I2, I3, I4) -- voltages for ao0..ao3 on whichever
        daq is active (see use_daq1).
    use_daq1 : bool
        True to apply every entry in wire_settings via daq_1 (zeroing
        daq_2) for this entire run; False for the reverse.
    odmr_kwargs : dict
        Keyword arguments for generate_bd_cw_odmr_gradient (freq_start,
        freq_stop, num_of_points, mw_amp, mw_length, etc.) EXCLUDING 'name'.
    num_sweeps : int
        Number of sweeps to average before stopping each measurement.
    drift_corrector : DriftCorrector
        Must already have set_reference() called.
    seq_name : str
        Name to use for the generated sequence.

    Returns
    -------
    list of dict
        One entry per (location, wire_setting) pair.
    """
    ensure_wire_outputs_active()

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

            set_wire_currents(currents, use_daq1=use_daq1)

            _laser_on_for_scanning()
            drift, ref_target = drift_corrector.correct_drift()
            print(f'  Drift: {drift}, reference target after correction: {ref_target}')

            target_pos = move_xy_only(drift_corrector, location_xy)
            print(f'  Moved to location target: {target_pos}')

            freq, signal = _run_odmr_sequence_and_collect(seq_name, num_sweeps)

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


# %%

# %%
import time
import numpy as np
import matplotlib.pyplot as plt
from IPython.display import display, clear_output
from skimage.registration import phase_cross_correlation
from qudi.logic.scanning_optimize_logic import OptimizationType, OptimizationMethod


# Module-level state used to remember what was running before switching to
# laser-only tracking mode, so it can be restored afterward.
_tracking_state = {}


def _get_pulseblaster_hw():
    """
    Drill down through pulsed_master_logic's connectors to obtain a direct
    reference to the raw PulseBlaster hardware module, bypassing the AWG
    entirely. Also returns the AwgPulseBlasterInterfuse instance itself.

    Confirmed connector chain:
      pulsed_master_logic.sequencegeneratorlogic() -> SequenceGeneratorLogic
      SequenceGeneratorLogic.pulsegenerator()      -> AwgPulseBlasterInterfuse
      AwgPulseBlasterInterfuse.pulseblaster()      -> raw PulseBlaster hw module
    """
    seq_gen = pulsed_master_logic.sequencegeneratorlogic()
    interfuse = seq_gen.pulsegenerator()
    pb_hw = interfuse.pulseblaster()
    return pb_hw, interfuse


def _wait_while(condition_fn, description, timeout, poll_interval=0.2):
    t_start = time.time()
    while condition_fn():
        if time.time() - t_start > timeout:
            raise TimeoutError(f'Timed out waiting for {description}.')
        time.sleep(poll_interval)


def start_laser_tracking(laser_channel=None, laser_length=3.0e-6,
                         measurement_action='pause',
                         timeout=30.0, poll_interval=0.2):
    """
    Save the currently loaded/running experiment state, stop or pause any
    running measurement, and switch to a direct PulseBlaster-only
    'laser on' pattern, bypassing the AWG entirely.

    Call stop_laser_tracking_and_resume() afterward to restore the
    experiment sequence/ensemble and optionally resume/restart the
    measurement.

    Parameters
    ----------
    laser_channel : str, optional
        The qudi-facing digital channel name driving the laser (e.g.
        'd_ch9'). If None, taken from
        pulsed_master_logic.generation_parameters['laser_channel'].
    laser_length : float
        Duration (s) of one repetition of the HIGH pulse programmed onto
        the PulseBlaster.
    measurement_action : str
        What to do with a currently running measurement before switching
        to laser tracking:
          'pause' (default) -- pause the measurement, preserving
                                accumulated data, so it can later be
                                resumed from where it left off.
                                NOTE: pausing already turns off the pulse
                                generator internally (pulsed_measurement_logic
                                pause_pulsed_measurement()), confirmed below
                                by polling status_dict['pulser_running'].
          'stop'             -- fully stop the measurement.
        Ignored if no measurement is currently running.
    timeout, poll_interval : float
        Passed to internal wait loops.
    """
    if measurement_action not in ('pause', 'stop'):
        raise ValueError(f"measurement_action must be 'pause' or 'stop', got {measurement_action!r}.")

    if _tracking_state.get('active'):
        pulsed_master_logic.log.warning(
            'start_laser_tracking() called while already in tracking mode; '
            'ignoring to avoid overwriting the saved experiment state. '
            'Call stop_laser_tracking_and_resume() first if you need to restart.'
        )
        return

    # ---------- 0. save current state ----------
    loaded_name, loaded_type = pulsed_master_logic.loaded_asset
    was_measurement_running = pulsed_master_logic.status_dict['measurement_running']
    was_pulser_running = pulsed_master_logic.status_dict['pulser_running']

    _tracking_state.clear()
    _tracking_state['active'] = True
    _tracking_state['loaded_name'] = loaded_name
    _tracking_state['loaded_type'] = loaded_type
    _tracking_state['was_pulser_running'] = was_pulser_running
    _tracking_state['was_measurement_running'] = was_measurement_running
    _tracking_state['measurement_action'] = measurement_action if was_measurement_running else None

    pulsed_master_logic.log.info(
        f'Saving experiment state before laser tracking: '
        f'loaded=({loaded_name!r}, {loaded_type!r}), '
        f'pulser_running={was_pulser_running}, '
        f'measurement_running={was_measurement_running} '
        f'(action={_tracking_state["measurement_action"]})'
    )

    # ---------- 1. pause or stop the running measurement ----------
    if was_measurement_running:
        if measurement_action == 'pause':
            pulsed_master_logic.toggle_pulsed_measurement_pause(True)
            # pause_pulsed_measurement() turns off the pulse generator as a
            # side effect -- use that as confirmation the pause has actually
            # been applied (more reliable than a fixed sleep, since the
            # call is dispatched via a queued Qt connection).
            _wait_while(
                lambda: pulsed_master_logic.status_dict['pulser_running'],
                'measurement pause to take effect (pulser off)', timeout, poll_interval
            )
            pulsed_master_logic.log.info('Measurement paused.')
        else:  # 'stop'
            pulsed_master_logic.toggle_pulsed_measurement(False)
            _wait_while(
                lambda: pulsed_master_logic.status_dict['measurement_running'],
                'running measurement to stop', timeout, poll_interval
            )
            pulsed_master_logic.log.info('Measurement stopped.')

    # ---------- 2. safety net: ensure pulser is off before reprogramming PB ----------
    # (covers the case where the pulser was on without a measurement running,
    # or as a fallback if the above didn't already turn it off)
    if pulsed_master_logic.status_dict['pulser_running']:
        pulsed_master_logic.toggle_pulse_generator(False)
        _wait_while(
            lambda: pulsed_master_logic.status_dict['pulser_running'],
            'pulse generator to stop', timeout, poll_interval
        )

    # ---------- 3. resolve hardware objects & laser channel ----------
    pb_hw, interfuse = _get_pulseblaster_hw()

    if laser_channel is None:
        laser_channel = pulsed_master_logic.generation_parameters.get('laser_channel')
        if not laser_channel:
            raise ValueError(
                'No laser_channel provided and none found in '
                'pulsed_master_logic.generation_parameters. Pass laser_channel explicitly.'
            )

    if not interfuse._is_pb_d_ch(laser_channel):
        raise ValueError(
            f'Laser channel "{laser_channel}" is not routed through the PulseBlaster '
            f'according to the interfuse\'s own channel mapping. This bypass method '
            f'only works for PulseBlaster-routed laser channels.'
        )

    pb_hw_channel = 'd_ch{0:d}'.format(interfuse._d_ch_to_pb_hw(laser_channel))

    # ---------- 4. build and upload a constant-HIGH waveform directly on the PB ----------
    if pb_hw.get_status()[0] != 0:
        pb_hw.pulser_off()

    pb_constraints = pb_hw.get_constraints()
    sample_rate = pb_hw.get_sample_rate() or pb_constraints.sample_rate.default

    n_samples = max(
        int(pb_constraints.waveform_length.min),
        int(round(laser_length * sample_rate))
    )

    digital_samples = {pb_hw_channel: np.ones(n_samples, dtype=bool)}

    pb_name = 'laser_on_direct'
    written, _ = pb_hw.write_waveform(
        pb_name, {}, digital_samples, True, True, n_samples
    )
    if written < 0:
        raise RuntimeError('Failed to write laser-on waveform directly to the PulseBlaster.')

    pb_hw.load_waveform([pb_name])

    # ---------- 5. start the PulseBlaster output ----------
    pb_hw.pulser_on()

    pulsed_master_logic.log.info(
        f'Laser tracking mode active: PulseBlaster channel "{pb_hw_channel}" '
        f'(qudi channel "{laser_channel}") running directly, AWG bypassed.'
    )


def stop_laser_tracking_and_resume(resume_mode='resume', timeout=30.0, poll_interval=0.2):
    """
    Stop the direct PulseBlaster-only laser output and restore whatever
    experiment sequence/ensemble was active before the matching
    start_laser_tracking() call. What happens to the *measurement*
    afterward is controlled by resume_mode.

    Note: pulse generator on/off is handled internally by
    start_pulsed_measurement()/continue_pulsed_measurement() themselves
    (they call pulse_generator_on() as part of starting/resuming), so this
    function deliberately does NOT toggle the pulser separately in those
    cases to avoid conflicting with that internal bookkeeping (e.g. pause
    duration correction in continue_pulsed_measurement()).

    Parameters
    ----------
    resume_mode : str
        What to do with the measurement after reloading the original
        asset:
          'resume'  (default) -- if the measurement was paused, unpause it
                                 to continue from where it left off. If it
                                 was instead fully stopped, this starts a
                                 fresh measurement (nothing to resume from).
          'restart'           -- start the measurement over from the
                                 beginning, discarding any previously
                                 accumulated data.
          'none'              -- leave the measurement stopped/paused;
                                 only the hardware asset is reloaded. If it
                                 was paused before, it remains validly
                                 paused (same state as a normal user-paused
                                 measurement) so it can be continued
                                 manually later.
        Ignored if no measurement was running before start_laser_tracking()
        was called.
    timeout, poll_interval : float
        Passed to internal wait loops.
    """
    if resume_mode not in ('resume', 'restart', 'none'):
        raise ValueError(f"resume_mode must be 'resume', 'restart', or 'none', got {resume_mode!r}.")

    if not _tracking_state.get('active'):
        pulsed_master_logic.log.warning(
            'stop_laser_tracking_and_resume() called but no active tracking '
            'state was found. Nothing to do.'
        )
        return

    pb_hw, _ = _get_pulseblaster_hw()
    pb_hw.pulser_off()

    loaded_name = _tracking_state.get('loaded_name')
    loaded_type = _tracking_state.get('loaded_type')
    measurement_action = _tracking_state.get('measurement_action')
    was_measurement_running = _tracking_state.get('was_measurement_running')
    was_pulser_running = _tracking_state.get('was_pulser_running')

    # ---------- 1. reload the original asset onto the hardware ----------
    if not loaded_name:
        pulsed_master_logic.log.info(
            'No previously loaded asset to restore. Leaving pulse generator idle.'
        )
        _tracking_state.clear()
        _tracking_state['active'] = False
        return

    pulsed_master_logic.log.info(
        f'Restoring previously loaded asset: ({loaded_name!r}, {loaded_type!r})'
    )

    is_sequence = loaded_type == 'PulseSequence'
    is_ensemble = loaded_type == 'PulseBlockEnsemble'

    if is_sequence:
        pulsed_master_logic.load_sequence(loaded_name)
    elif is_ensemble:
        pulsed_master_logic.load_ensemble(loaded_name)
    else:
        pulsed_master_logic.log.error(
            f'Unrecognized loaded_type "{loaded_type}" for asset "{loaded_name}"; '
            f'restore aborted -- please reload manually.'
        )
        _tracking_state.clear()
        _tracking_state['active'] = False
        return

    _wait_while(
        lambda: pulsed_master_logic.status_dict['loading_busy'],
        f'"{loaded_name}" to finish loading', timeout, poll_interval
    )

    current_loaded_name, _ = pulsed_master_logic.loaded_asset
    if current_loaded_name != loaded_name:
        pulsed_master_logic.log.error(
            f'Restore failed: expected "{loaded_name}" to be loaded, but '
            f'currently loaded asset is "{current_loaded_name}". Check the '
            f'qudi log for load errors.'
        )
        _tracking_state.clear()
        _tracking_state['active'] = False
        return

    # ---------- 2. handle the measurement / pulser according to resume_mode ----------
    if not was_measurement_running:
        # No measurement was running before tracking -- just restore the
        # pulser's prior on/off state directly, since no measurement
        # start/continue method will do that for us in this case.
        if was_pulser_running:
            pulsed_master_logic.toggle_pulse_generator(True)
            _wait_while(
                lambda: not pulsed_master_logic.status_dict['pulser_running'],
                'pulse generator to resume running', timeout, poll_interval
            )
        pulsed_master_logic.log.info('No measurement to resume; hardware asset restored.')

    elif resume_mode == 'none':
        if measurement_action == 'stop':
            pulsed_master_logic.log.info(
                'Measurement left stopped, as requested (resume_mode="none").'
            )
        else:  # 'pause' -- leave it validly paused; pulser stays off, matching normal pause state
            pulsed_master_logic.log.info(
                'Measurement left paused, as requested (resume_mode="none"). '
                'It can be continued manually later.'
            )

    elif measurement_action == 'pause':
        if resume_mode == 'resume':
            # continue_pulsed_measurement() turns the pulser back on itself
            # and corrects elapsed-time bookkeeping for the pause duration.
            pulsed_master_logic.toggle_pulsed_measurement_pause(False)
            _wait_while(
                lambda: not pulsed_master_logic.status_dict['pulser_running'],
                'measurement to resume from pause (pulser on)', timeout, poll_interval
            )
            pulsed_master_logic.log.info('Measurement unpaused; continuing from where it left off.')
        else:  # 'restart'
            # Currently paused (module_state still 'locked') -- fully stop
            # first to discard accumulated data, then start fresh.
            pulsed_master_logic.toggle_pulsed_measurement(False)
            _wait_while(
                lambda: pulsed_master_logic.status_dict['measurement_running'],
                'paused measurement to stop before restart', timeout, poll_interval
            )
            pulsed_master_logic.toggle_pulsed_measurement(True)
            _wait_while(
                lambda: not pulsed_master_logic.status_dict['measurement_running'],
                'measurement to restart', timeout, poll_interval
            )
            pulsed_master_logic.log.info('Measurement restarted from the beginning.')

    else:  # measurement_action == 'stop' -- fully stopped, nothing to resume; 'resume' and
          # 'restart' both just mean "start fresh" here
        pulsed_master_logic.toggle_pulsed_measurement(True)
        _wait_while(
            lambda: not pulsed_master_logic.status_dict['measurement_running'],
            'measurement to start', timeout, poll_interval
        )
        pulsed_master_logic.log.info('Measurement started fresh (was fully stopped before tracking).')

    pulsed_master_logic.log.info(f'Experiment "{loaded_name}" successfully restored.')

    _tracking_state.clear()
    _tracking_state['active'] = False


class DriftCorrector:
    """
    Tracks drift of an NV sample over time by periodically re-scanning xy
    (cross-correlated against a reference) and then optimizing z (full
    range, via ScanningOptimizeLogic's Gaussian-fit optimizer) at the
    corrected xy position. The xy scan range is shifted along with the
    accumulated drift estimate so the region of interest stays in view.

    Parameters
    ----------
    xy_axes : tuple of str
        Names of the two lateral scan axes, e.g. ('x', 'y').
    z_axis : str
        Name of the axial scan axis, e.g. 'z'.
    channel : str
        Data channel used for both xy correlation and z optimization.
    xy_upsample_factor : int
        Subpixel upsampling factor for the xy cross-correlation.
    position_bounds : dict, optional
        Dict of {axis_name: (min, max)} giving absolute allowed position
        limits for each axis. xy scan ranges and target positions are
        clipped to stay within these bounds. The z scan always uses the
        full range given by position_bounds[z_axis] (required for z).
    max_xy_correlation_error : float, optional
        If the normalized RMS error reported by phase_cross_correlation
        exceeds this value, the xy correlation result for that cycle is
        considered unreliable and the xy drift estimate is left unchanged.
    max_xy_shift : float, optional
        Sanity limit (in um) on the magnitude of a single-cycle xy shift
        computed from cross-correlation. If exceeded, the result is
        considered implausible and the xy drift estimate is left
        unchanged. Set to None to disable this check.
    """

    def __init__(self, xy_axes=('x', 'y'), z_axis='z', channel='Sum',
                 xy_upsample_factor=10, position_bounds=None,
                 max_xy_correlation_error=0.5, max_xy_shift=None):
        self.xy_axes = xy_axes
        self.z_axis = z_axis
        self.channel = channel
        self.xy_upsample_factor = xy_upsample_factor
        self.position_bounds = position_bounds or {}
        self.max_xy_correlation_error = max_xy_correlation_error
        self.max_xy_shift = max_xy_shift

        self.reference_xy_scan = None
        self.reference_target = None
        self.z_resolution = 100
        self.z_frequency = 50.0

        self.total_drift = {ax: 0.0 for ax in (*xy_axes, z_axis)}

        # history of {'time': ..., axis: position, ...} dicts, populated by
        # correct_drift(); used by plot_position_history()/track_drift()
        self.position_history = []

    # ------------------------------------------------------------------
    # -------------------------- helper methods --------------------------
    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(value):
        """Coerce a numpy/pint/other numeric type into a plain Python float."""
        if hasattr(value, 'magnitude'):
            value = value.magnitude
        return float(value)

    @staticmethod
    def _to_plain_array(data):
        """Convert to a plain float ndarray, working around array-likes
        (e.g. pint Quantities) whose __array__() doesn't support the
        NumPy 2.0 dtype/copy keyword protocol."""
        if hasattr(data, 'magnitude'):
            data = data.magnitude  # strip pint units if present
        arr = np.array(data)       # no dtype/copy kwargs here -> avoids the warning
        return arr.astype(float, copy=False)

    @staticmethod
    def _estimate_scan_duration(axes):
        """Estimate scan duration (seconds) from currently configured
        resolution/frequency for the given axes."""
        resolution = scanning_probe_logic.scan_resolution
        frequency = scanning_probe_logic.scan_frequency

        fast_axis = axes[0]
        line_time = resolution[fast_axis] / frequency[fast_axis]

        if len(axes) > 1:
            n_lines = resolution[axes[1]]
        else:
            n_lines = 1

        return line_time * n_lines

    def _run_scan_and_wait(self, axes, timeout=None, poll_interval=0.2,
                            safety_factor=12.0, min_timeout=10.0):
        """
        Start a scan on the given axes and block until finished.
        Verifies that a NEW scan actually ran (by checking the timestamp AND
        that the returned data isn't just NaN placeholder data), retrying if not.
        """
        if timeout is None:
            estimated = self._estimate_scan_duration(axes)
            timeout = max(min_timeout, estimated * safety_factor)
            scanning_probe_logic.log.debug(
                f'Estimated scan duration for axes {axes}: {estimated:.1f} s; '
                f'using timeout {timeout:.1f} s.'
            )

        prev_data = scanning_probe_logic.scan_data
        prev_timestamp = prev_data.timestamp if prev_data is not None else None

        for attempt in range(3):
            t_call = time.time()
            scanning_probe_logic.start_scan(axes)

            t_start = time.time()
            while scanning_probe_logic.module_state() != 'idle':
                if time.time() - t_start > timeout:
                    scanning_probe_logic.log.error(
                        f'Scan on axes {axes} timed out after {timeout:.1f} s. '
                        f'module_state={scanning_probe_logic.module_state()}, '
                        f'scanner_state={scanning_probe_logic._scanner().module_state()}'
                    )
                    raise TimeoutError(f'Scan on axes {axes} timed out.')
                time.sleep(poll_interval)

            elapsed = time.time() - t_call
            new_data = scanning_probe_logic.scan_data

            if new_data is None:
                scanning_probe_logic.log.warning(
                    f'Scan on axes {axes} returned no data (attempt {attempt + 1}). Retrying...'
                )
                time.sleep(0.5)
                continue

            if prev_timestamp is not None and new_data.timestamp == prev_timestamp:
                scanning_probe_logic.log.warning(
                    f'Scan on axes {axes} did not produce new data (stale timestamp, '
                    f'attempt {attempt + 1}). Retrying after a short delay...'
                )
                time.sleep(0.5)
                continue

            # Check that the data actually contains real values, not just the
            # NaN placeholder buffer created at the start of a (possibly failed) scan.
            channel = next(iter(new_data.channel_units))
            arr = self._to_plain_array(new_data.data[channel])
            n_nan = int(np.isnan(arr).sum())
            n_total = arr.size

            scanning_probe_logic.log.debug(
                f'Scan on axes {axes} finished in {elapsed:.2f} s, '
                f'{n_nan}/{n_total} NaN pixels.'
            )

            if n_nan == n_total:
                scanning_probe_logic.log.warning(
                    f'Scan on axes {axes} returned an all-NaN buffer (attempt {attempt + 1}). '
                    f'This means start_scan() likely failed silently on the hardware side. '
                    f'Elapsed time was only {elapsed:.2f} s. Retrying...'
                )
                time.sleep(0.5)
                continue

            if n_nan > 0:
                scanning_probe_logic.log.warning(
                    f'Scan on axes {axes} completed with {n_nan}/{n_total} NaN pixels '
                    f'(partial data). Proceeding anyway, but this may indicate an issue.'
                )

            return new_data.copy()

        raise RuntimeError(
            f'Scan on axes {axes} failed to produce valid data after retrying. '
            f'Check the qudi log for hardware errors (e.g. "Could not start scan").'
        )

    def _full_z_range(self):
        if self.z_axis not in self.position_bounds:
            raise ValueError(
                f'position_bounds must include an entry for z axis '
                f'"{self.z_axis}" to define the full z scan range.'
            )
        return tuple(self.position_bounds[self.z_axis])

    def _clip_position(self, ax, value):
        value = self._to_float(value)
        if ax in self.position_bounds:
            lo, hi = self.position_bounds[ax]
            clipped = float(np.clip(value, lo, hi))
            if clipped != value:
                scanning_probe_logic.log.warning(
                    f'Position {value:.3f} for axis "{ax}" out of bounds '
                    f'({lo}, {hi}); clipped to {clipped:.3f}.'
                )
            return clipped
        return value

    def _clip_range_shift(self, ref_range, drift, ax):
        width = self._to_float(ref_range[1]) - self._to_float(ref_range[0])
        shifted_min = self._to_float(ref_range[0]) + self._to_float(drift)
        shifted_max = self._to_float(ref_range[1]) + self._to_float(drift)

        if ax in self.position_bounds:
            lo, hi = self.position_bounds[ax]
            if shifted_min < lo:
                shifted_min = lo
                shifted_max = lo + width
            elif shifted_max > hi:
                shifted_max = hi
                shifted_min = hi - width

        return (float(shifted_min), float(shifted_max))

    @staticmethod
    def _check_image_quality(img, label=''):
        n_total = img.size
        n_nan = int(np.isnan(img).sum())
        if n_nan > 0:
            scanning_probe_logic.log.warning(
                f'{label}: {n_nan}/{n_total} pixels are NaN.'
            )
        finite_vals = img[~np.isnan(img)]
        if finite_vals.size == 0:
            scanning_probe_logic.log.error(f'{label}: all pixels are NaN.')
            return False
        if np.nanstd(img) == 0:
            scanning_probe_logic.log.error(
                f'{label}: image is constant (std=0, min={np.nanmin(img)}, '
                f'max={np.nanmax(img)}). Cross-correlation cannot work on this data.'
            )
            return False
        return True

    def _optimize_z(self, timeout=None):
        """
        Run a 1D optimize scan along z (covering the full configured z
        range) at the current xy target position, using
        ScanningOptimizeLogic's Gaussian-fit-based optimizer. The optimizer
        itself moves the scanner directly to the fitted brightness peak.

        Returns the new z target position after optimization.
        """
        z_axis = self.z_axis
        full_z_range = self._full_z_range()
        range_width = full_z_range[1] - full_z_range[0]
        z_center = (full_z_range[0] + full_z_range[1]) / 2.0

        z_res = self.z_resolution
        z_freq = self.z_frequency

        # Temporarily center the z target so that the optimizer's centered
        # scan window (target +/- range/2) covers exactly the full z range.
        current_target = dict(scanning_probe_logic.scanner_target)
        temp_target = dict(current_target)
        temp_target[z_axis] = z_center
        scanning_probe_logic.set_target_position(
            {ax: self._to_float(v) for ax, v in temp_target.items()}, move_blocking=True
        )

        scanning_optimize_logic.set_optimize_settings(
            data_channel=self.channel,
            scan_sequence=((z_axis,),),
            scan_dimension=[1],
            range={z_axis: range_width},
            resolution={z_axis: z_res},
            frequency={z_axis: z_freq},
            optimization_methods={OptimizationType.ONE_D: OptimizationMethod.GAUSSIAN},
        )

        scanning_optimize_logic.start_optimize()

        if timeout is None:
            timeout = max(10.0, self._estimate_scan_duration((z_axis,)) * 12.0)

        t_start = time.time()
        while scanning_optimize_logic.module_state() != 'idle':
            if time.time() - t_start > timeout:
                raise TimeoutError('z optimization timed out.')
            time.sleep(0.2)

        optimal = scanning_optimize_logic.optimal_position
        if z_axis not in optimal:
            raise RuntimeError(
                'z optimization did not return a valid position (fit likely failed). '
                'Check the qudi log for details.'
            )

        return self._to_float(optimal[z_axis])

    # ------------------------------------------------------------------
    # --------------------------- public API ---------------------------
    # ------------------------------------------------------------------
    def set_reference(self, xy_scan, target_position, z_resolution=100, z_frequency=50.0):
        """
        Call once at the start, right after acquiring the initial xy
        reference scan, with the target sitting on the structure/NV of
        interest (assumed to already be at the brightness-optimal z).

        No reference z-scan is needed: z drift is determined fresh each
        cycle via ScanningOptimizeLogic's Gaussian-fit optimizer (see
        _optimize_z()), which only requires a resolution/frequency to run
        with, not a stored reference scan/peak position.

        Parameters
        ----------
        xy_scan : ScanData
            Reference xy scan for cross-correlation.
        target_position : dict
            Scanner target position at the reference point (must include
            the z axis; assumed to already be optimized in z).
        z_resolution : int
            Resolution (number of points) to use for the z-optimization
            scan performed during correct_drift().
        z_frequency : float
            Scan frequency (Hz) to use for the z-optimization scan.
        """
        self.reference_xy_scan = xy_scan.copy()
        self.reference_target = dict(target_position)
        self.z_resolution = z_resolution
        self.z_frequency = z_frequency
        self.total_drift = {ax: 0.0 for ax in self.total_drift}
        self.position_history = []

        ref_img = self._to_plain_array(self.reference_xy_scan.data[self.channel])
        self._check_image_quality(ref_img, label='reference xy scan')

    def correct_drift(self, move=True):
        """
        Perform one drift-correction cycle:
          1. xy scan (range shifted by current drift estimate) + cross-correlation
             (with fail-safe rejection of low-confidence / implausible matches)
          2. move target's x/y to the corrected position
          3. z optimization over the FULL configured z range at that xy
             position, via ScanningOptimizeLogic (Gaussian fit)
          4. record the resulting target position in position_history

        Returns (total_drift, new_target).
        """
        if self.reference_xy_scan is None:
            raise RuntimeError('No reference set. Call set_reference() first.')

        xy_axes = self.xy_axes
        z_axis = self.z_axis

        # ---------- 1. xy scan ----------
        ref_xy_range = self.reference_xy_scan.settings.range
        ref_xy_res = self.reference_xy_scan.settings.resolution

        for i, ax in enumerate(xy_axes):
            shifted_range = self._clip_range_shift(ref_xy_range[i], self.total_drift[ax], ax)
            scanning_probe_logic.set_scan_range(ax, shifted_range)
            scanning_probe_logic.set_scan_resolution(ax, ref_xy_res[i])

        new_xy_scan = self._run_scan_and_wait(xy_axes)

        ref_img = self._to_plain_array(self.reference_xy_scan.data[self.channel])
        new_img = self._to_plain_array(new_xy_scan.data[self.channel])

        ref_ok = self._check_image_quality(ref_img, label='reference xy scan')
        new_ok = self._check_image_quality(new_img, label='new xy scan')

        incremental_drift_xy = {ax: 0.0 for ax in xy_axes}

        if not (ref_ok and new_ok):
            scanning_probe_logic.log.warning(
                'Skipping xy cross-correlation this cycle due to bad image data. '
                'xy drift estimate unchanged.'
            )
        else:
            ref_mask = ~np.isnan(ref_img)
            new_mask = ~np.isnan(new_img)

            try:
                if ref_mask.all() and new_mask.all():
                    shift_px, error, _ = phase_cross_correlation(
                        ref_img, new_img, upsample_factor=self.xy_upsample_factor
                    )
                else:
                    shift_px, error, _ = phase_cross_correlation(
                        ref_img, new_img, reference_mask=ref_mask, moving_mask=new_mask
                    )

                scanning_probe_logic.log.debug(
                    f'xy cross-correlation error metric: {error}'
                )

                # --- Fail-safe 1: reject poor-confidence correlation matches ---
                if error is not None and error > self.max_xy_correlation_error:
                    scanning_probe_logic.log.warning(
                        f'xy cross-correlation error ({error:.3f}) exceeds threshold '
                        f'({self.max_xy_correlation_error:.3f}); no strong match found. '
                        f'Skipping xy drift update this cycle.'
                    )
                else:
                    px_size = [
                        (ref_xy_range[i][1] - ref_xy_range[i][0]) / ref_xy_res[i]
                        for i in range(2)
                    ]
                    candidate_drift_xy = {
                        xy_axes[0]: -shift_px[0] * px_size[0],
                        xy_axes[1]: -shift_px[1] * px_size[1],
                    }

                    # --- Fail-safe 2: reject implausibly large shifts ---
                    shift_magnitude = float(np.hypot(*candidate_drift_xy.values()))
                    if self.max_xy_shift is not None and shift_magnitude > self.max_xy_shift:
                        scanning_probe_logic.log.warning(
                            f'Computed xy shift magnitude ({shift_magnitude:.3f} um) exceeds '
                            f'sanity limit ({self.max_xy_shift:.3f} um); likely a bad match. '
                            f'Skipping xy drift update this cycle.'
                        )
                    else:
                        incremental_drift_xy = candidate_drift_xy

            except ValueError as e:
                scanning_probe_logic.log.error(
                    f'Cross-correlation failed ({e}); skipping xy update this cycle.'
                )

        for ax in xy_axes:
            self.total_drift[ax] += incremental_drift_xy[ax]

        scanning_probe_logic.log.info(f'Incremental xy drift (um): {incremental_drift_xy}')

        # ---------- 2. move target's x/y ----------
        xy_target = {
            ax: self._clip_position(ax, self.reference_target[ax] + self.total_drift[ax])
            for ax in xy_axes
        }
        target_pos = dict(scanning_probe_logic.scanner_target)
        target_pos.update(xy_target)
        target_pos = {ax: self._to_float(val) for ax, val in target_pos.items()}
        scanning_probe_logic.set_target_position(target_pos, move_blocking=True)

        # ---------- 3. z optimization (full range, via ScanningOptimizeLogic) ----------
        new_z_pos = self._optimize_z()
        self.total_drift[z_axis] = new_z_pos - self.reference_target[z_axis]
        scanning_probe_logic.log.info(f'z drift (um): {self.total_drift[z_axis]}')

        # ---------- 4. final target (z already moved by optimizer; re-clip for safety) ----------
        new_target = dict(scanning_probe_logic.scanner_target)
        clipped_z = self._clip_position(z_axis, new_target[z_axis])
        if clipped_z != new_target[z_axis]:
            new_target[z_axis] = clipped_z
            if move:
                scanning_probe_logic.set_target_position(
                    {ax: self._to_float(v) for ax, v in new_target.items()}, move_blocking=True
                )

        new_target = {ax: self._to_float(val) for ax, val in new_target.items()}
        scanning_probe_logic.log.info(f'Corrected target position: {new_target}')

        # ---------- record history ----------
        self.position_history.append({'time': time.time(), **new_target})

        return dict(self.total_drift), new_target

    # ------------------------------------------------------------------
    # ------------------------- plotting methods -------------------------
    # ------------------------------------------------------------------
    def plot_position_history(self, ax=None):
        """
        Plot the recorded target x, y, z positions (from position_history)
        as a function of elapsed time. If `ax` (an iterable of 3
        matplotlib Axes) is provided, plot into those axes and redraw;
        otherwise create a new figure.
        """
        if not self.position_history:
            print('No position history recorded yet. Call correct_drift() first.')
            return

        t0 = self.position_history[0]['time']
        times_min = [(rec['time'] - t0) / 60.0 for rec in self.position_history]

        axes_names = (*self.xy_axes, self.z_axis)

        if ax is None:
            fig, ax = plt.subplots(len(axes_names), 1, sharex=True, figsize=(7, 6))
        else:
            fig = ax[0].figure

        for a, name in zip(ax, axes_names):
            a.clear()
            values = [rec.get(name, np.nan) for rec in self.position_history]
            a.plot(times_min, values, marker='o')
            a.set_ylabel(f'{name} (µm)')
            a.grid(True)

        ax[-1].set_xlabel('Elapsed time (min)')
        fig.suptitle('Target position over time')
        fig.tight_layout()
        return fig, ax

    def track_drift(self, interval=60.0, n_iterations=None, move=True):
        """
        Continually call correct_drift() every `interval` seconds, and
        live-update a plot of the target's x, y, z positions vs. time in
        the notebook. Runs until `n_iterations` cycles have completed, or
        indefinitely if n_iterations is None (interrupt the notebook cell
        / raise KeyboardInterrupt to stop).

        Parameters
        ----------
        interval : float
            Time (seconds) to wait between successive correct_drift() calls.
        n_iterations : int, optional
            Number of correction cycles to run. Runs forever if None.
        move : bool
            Passed through to correct_drift(); whether to actually move
            the scanner target to the corrected position each cycle.
        """
        fig, ax = plt.subplots(len(self.xy_axes) + 1, 1, sharex=True, figsize=(7, 6))

        iteration = 0
        try:
            while n_iterations is None or iteration < n_iterations:
                drift, new_target = self.correct_drift(move=move)

                clear_output(wait=True)
                self.plot_position_history(ax=ax)
                display(fig)

                print(f'Iteration {iteration + 1}: drift = {drift}')

                iteration += 1
                if n_iterations is None or iteration < n_iterations:
                    time.sleep(interval)
        except KeyboardInterrupt:
            scanning_probe_logic.log.info('Drift tracking stopped by user (KeyboardInterrupt).')
        finally:
            plt.close(fig)


# %%
drift_corrector = DriftCorrector(
    xy_axes=('x', 'y'), z_axis='z', channel='Sum',
    position_bounds={'x': (10, 90), 'y': (10, 90), 'z': (0, 6.2)},
    max_xy_correlation_error=0.5, max_xy_shift=5.0,
)

# %%
drift_corrector.set_reference(scanning_probe_logic.scan_data, scanning_probe_logic.scanner_target,
                              z_resolution=100, z_frequency=50.0)

# %%
print(scanning_probe_logic.scanner_target)

# %%
print(scanning_probe_logic.scanner_target)

# %%
ytop = 44.3757
ybottom = 40.2671
ymean = (ytop + ybottom)/2
ystep = (ytop - ybottom)/10
locations = [(35.24, ymean - 2*ystep), (35.24, ymean - 1*ystep), (35.24, ymean), (35.24, ymean + 1*ystep), (35.24, ymean + 2*ystep)]

# %%
wire_settings = [
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0)
]

# %%
odmr_kwargs = dict(
    freq_start=2.65e9, freq_stop=3.05e9, num_of_points=50,
    mw_amp=0.2, mw_length=10e-6,
    always_on_ch='d_ch15', gradient_mode=0, gradient_mode_ch='d_ch6',
    pulser_ch='d_ch3', duty_cycle=0.2,
)

# %%
results = run_field_mapping_experiment(
    locations, wire_settings, use_daq1=True,
    odmr_kwargs=odmr_kwargs, num_sweeps=5000,
    drift_corrector=drift_corrector,
)

# %%

# %%

# %%
import numpy as np
from scipy.optimize import curve_fit
from scipy.special import erf
import matplotlib.pyplot as plt


def _to_plain_array(data):
    """Convert to a plain float ndarray, working around array-likes
    (e.g. pint Quantities) whose __array__() doesn't support the
    NumPy 2.0 dtype/copy keyword protocol."""
    if hasattr(data, 'magnitude'):
        data = data.magnitude
    arr = np.array(data)
    return arr.astype(float, copy=False)


def _extract_y_profile(xy_scan, x_position, x_window, channel, x_axis, y_axis):
    """
    Extract a 1D intensity profile along y_axis from a 2D reference scan,
    averaged over a small window in x_axis centered at x_position (or the
    single nearest column if x_window is None).
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
    """Overlay refined edge points and fitted lines (per wire, plus the
    resulting gap-center line) on top of the 2D reference scan image.
    Optionally also shows the anchor point and perpendicular-offset
    target locations."""
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
        that corresponds to increasing y when the line's tilt is small
        (see sign convention note below).
    wire_estimates, channel, x_axis, y_axis, x_window, search_half_width,
    expected_wire_width, expected_gap_width, plot :
        Passed through to fit_tilted_wire_gap().

    Returns
    -------
    locations : list of dict
        [{x_axis: x_t, y_axis: y_t} for each offset], where (x_t, y_t) is
        the point obtained by moving `offset` along the perpendicular
        direction from the anchor point (x_position, y_center).
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


# %%
reference_xy_scan = scanning_probe_logic.scan_data

# %%
# Rough estimates: for each wire, at 2 x-locations, (x, y_top_guess, y_bottom_guess).
# These don't need to be precise -- just roughly where each wire's edges are,
# eyeballed from a plot of the reference scan.
wire_estimates = [
    # Wire A (e.g. the visually upper wire)
    [(31.185, 48.516, 43.899), (42.924, 48.710, 44.436)],
    # Wire B (the lower wire)
    [(31.185, 40.693, 36.782), (42.924, 40.754, 36.842)],
]

locations, anchor, fit_info = compute_target_locations(
    reference_xy_scan,
    x_position=34.616,
    relative_offsets=[-1.0, -0.5, 0.0, 0.5, 1.0],
    wire_estimates=wire_estimates,
    x_window=1.0,
    search_half_width=1.5,          # keep smaller than ~half of wire_width(5)/gap_width(6)
    plot=True,                      # visually verify fit before trusting it
)

print(f'Anchor point: {anchor}')
print(f'Target locations (perpendicular offsets): {locations}')

# %%
