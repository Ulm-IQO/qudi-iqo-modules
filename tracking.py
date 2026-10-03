# -*- coding: utf-8 -*-
"""
tracking.py

Drift-tracking and laser-only scanning utilities for NV ensemble
experiments run via qudi's confocal scanning + AWG/PulseBlaster pulsed
measurement stack.

Provides two independent pieces of functionality:

1. Laser-only tracking bypass (start_laser_tracking / stop_laser_tracking_and_resume):
   Temporarily pause or stop whatever pulsed measurement/sequence is
   currently loaded and running, and switch the PulseBlaster (bypassing
   the AWG entirely) to a simple, continuously-running 'laser on' pattern
   -- fast, since the AWG's own sequence/waveform memory is never
   touched. This is meant to be used as a brief interruption around a
   confocal drift-correction cycle (see DriftCorrector below), after
   which the original experiment sequence/ensemble is reloaded and the
   measurement optionally resumed, restarted, or left stopped/paused.

2. DriftCorrector:
   Periodically re-scans a small xy region, cross-correlates it against a
   stored reference xy scan to estimate lateral (x/y) drift, then
   optimizes the z position (via ScanningOptimizeLogic's Gaussian-fit
   optimizer) at the drift-corrected xy location. Includes fail-safes
   against low-confidence correlation matches and implausibly large
   single-cycle shifts, and supports a reference scan with a DIFFERENT
   (typically higher) resolution than the fast iterative tracking scans.

3. ZTracker:
   z-only re-optimization (full z window, Gaussian fit, laser switched on
   via the PulseBlaster bypass) between successive measurements -- for
   setups where only z drifts. Usable as the `before_each` hook of
   coil_characterization's measurement functions.

This file assumes the following qudi objects are available as GLOBAL
variables in the calling namespace (as they would be if this file's
contents were pasted directly into a Jupyter notebook connected to a
running qudi instance):

    scanning_probe_logic    -- ScanningProbeLogic
    scanning_optimize_logic -- ScanningOptimizeLogic
    pulsed_master_logic     -- PulsedMasterLogic

If this file is instead `import`-ed as a module (recommended for reuse),
these names will NOT automatically be visible inside it, since Python
module globals are isolated per file. Before calling anything from this
module, inject the qudi objects into its namespace once, e.g.:

    import tracking
    tracking.scanning_probe_logic = scanning_probe_logic
    tracking.scanning_optimize_logic = scanning_optimize_logic
    tracking.pulsed_master_logic = pulsed_master_logic
"""

import time
import numpy as np
import matplotlib.pyplot as plt
from IPython.display import display, clear_output
from skimage.registration import phase_cross_correlation
from skimage.transform import resize
from qudi.logic.scanning_optimize_logic import OptimizationType, OptimizationMethod


# =============================================================================
# Laser-only tracking bypass (PulseBlaster-direct, AWG untouched)
# =============================================================================

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


def _wait_while(condition_fn, description, timeout, poll_interval):
    """Poll condition_fn() until it returns False, or raise TimeoutError
    after `timeout` seconds."""
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


# =============================================================================
# DriftCorrector: xy cross-correlation + z Gaussian-fit optimization
# =============================================================================

class DriftCorrector:
    """
    Tracks drift of an NV sample over time by periodically re-scanning xy
    (cross-correlated against a reference) and then optimizing z (full
    range, via ScanningOptimizeLogic's Gaussian-fit optimizer) at the
    reference target. The xy scan range is shifted along with the
    accumulated drift estimate so the region of interest stays in view.

    The reference scan may have a DIFFERENT (typically higher) resolution
    than the iterative drift-correction scans, as long as it covers the
    SAME physical xy bounds. The reference image is resampled (via
    skimage.transform.resize) to match the iterative scan's resolution
    before each cross-correlation.

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

        # Reference scan's own physical range/resolution, snapshotted as
        # plain values at set_reference() time (decoupled from
        # reference_xy_scan.settings, since ScanData.copy() does not
        # deep-copy .settings -- reading it fresh later risks reflecting a
        # since-mutated/reused ScanSettings object from the scanner).
        self.ref_xy_range = None
        self.ref_xy_res = None

        # Resolution to use for the iterative drift-correction scans.
        # Defaults to the reference scan's own resolution (original
        # behavior) unless overridden in set_reference().
        self.iter_xy_resolution = None

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
        """Return the full z scan range from configured position bounds."""
        if self.z_axis not in self.position_bounds:
            raise ValueError(
                f'position_bounds must include an entry for z axis '
                f'"{self.z_axis}" to define the full z scan range.'
            )
        return tuple(self.position_bounds[self.z_axis])

    def _clip_position(self, ax, value):
        """Clip a single scalar position to the configured bounds for axis ax."""
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
        """
        Shift a (min, max) range by `drift`, preserving its width, and clip
        so the resulting range stays within the configured bounds for `ax`.
        """
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
        """Log diagnostics about an image and return True if it's usable
        for cross-correlation (i.e., not all-NaN and not constant)."""
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

    def _get_reference_image_resampled(self):
        """
        Return the reference xy image, resampled (via
        skimage.transform.resize) to match self.iter_xy_resolution if it
        differs from the reference scan's own (native) resolution. If the
        resolutions already match, the image is returned completely
        unchanged -- no resampling/blurring is performed unnecessarily.

        Resampling assumes the reference scan and iterative scans cover
        the SAME physical xy bounds (self.ref_xy_range); only the pixel
        resolution may differ.
        """
        ref_img = self._to_plain_array(self.reference_xy_scan.data[self.channel])

        if tuple(ref_img.shape) == tuple(self.iter_xy_resolution):
            return ref_img

        scanning_probe_logic.log.debug(
            f'Resampling reference image from {ref_img.shape} to '
            f'{tuple(self.iter_xy_resolution)} to match iterative scan resolution.'
        )
        resampled = resize(ref_img, tuple(self.iter_xy_resolution), order=1,
                           mode='edge', anti_aliasing=True, preserve_range=True)
        return resampled

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
    def set_reference(self, xy_scan, target_position, z_resolution=100, z_frequency=50.0,
                      iter_xy_resolution=None):
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
            Reference xy scan for cross-correlation. May have a DIFFERENT
            resolution than the iterative drift-correction scans (e.g.
            much higher, since it is only acquired once), but must cover
            the SAME physical xy bounds as the iterative scans will use.
        target_position : dict
            Scanner target position at the reference point (must include
            the z axis; assumed to already be optimized in z).
        z_resolution : int
            Resolution (number of points) to use for the z-optimization
            scan performed during correct_drift().
        z_frequency : float
            Scan frequency (Hz) to use for the z-optimization scan.
        iter_xy_resolution : dict, optional
            {axis: n} resolution to use for the iterative drift-correction
            scans, e.g. {'x': 50, 'y': 50}. If None (default), the
            iterative scans simply reuse the reference scan's own
            resolution (original behavior, no resampling needed).
        """
        self.reference_xy_scan = xy_scan.copy()
        self.reference_target = dict(target_position)
        self.z_resolution = z_resolution
        self.z_frequency = z_frequency
        self.total_drift = {ax: 0.0 for ax in self.total_drift}
        self.position_history = []

        # Snapshot range/resolution as plain values NOW, decoupled from
        # reference_xy_scan.settings -- ScanData.copy() does not deep-copy
        # the `settings` attribute, so if the underlying ScanSettings
        # object is later mutated/reused by the scanner (e.g. when a
        # z-scan is configured), reading .settings.range/.resolution later
        # could silently reflect the WRONG scan configuration.
        self.ref_xy_range = tuple(
            (self._to_float(r[0]), self._to_float(r[1])) for r in xy_scan.settings.range
        )
        self.ref_xy_res = tuple(int(r) for r in xy_scan.settings.resolution)

        self.iter_xy_resolution = (
            tuple(int(iter_xy_resolution[ax]) for ax in self.xy_axes)
            if iter_xy_resolution is not None
            else self.ref_xy_res
        )

        ref_img = self._to_plain_array(self.reference_xy_scan.data[self.channel])
        self._check_image_quality(ref_img, label='reference xy scan')

    def correct_drift(self, move=True):
        """
        Perform one drift-correction cycle:
          1. xy scan (range shifted by current drift estimate; resolution
             given by self.iter_xy_resolution) + cross-correlation against
             the reference xy image (resampled to match, if its native
             resolution differs), with fail-safe rejection of
             low-confidence / implausible matches
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
        ref_xy_range = self.ref_xy_range
        iter_res = self.iter_xy_resolution

        for i, ax in enumerate(xy_axes):
            shifted_range = self._clip_range_shift(ref_xy_range[i], self.total_drift[ax], ax)
            scanning_probe_logic.set_scan_range(ax, shifted_range)
            scanning_probe_logic.set_scan_resolution(ax, iter_res[i])

        new_xy_scan = self._run_scan_and_wait(xy_axes)

        ref_img = self._get_reference_image_resampled()
        new_img = self._to_plain_array(new_xy_scan.data[self.channel])

        ref_ok = self._check_image_quality(ref_img, label='reference xy scan (resampled)')
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
                        (ref_xy_range[i][1] - ref_xy_range[i][0]) / iter_res[i]
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



# =============================================================================
# ZTracker: z-only re-optimization between measurements
# =============================================================================

class ZTracker:
    """
    Re-optimize only the z position (no xy scans) between successive
    measurements, for setups where z is the only coordinate that drifts.

    Each call (`tracker()`):
      1. switches the PulseBlaster to a continuous laser-on pattern
         (start_laser_tracking(measurement_action='stop'); the AWG is
         untouched and any running pulsed measurement is stopped);
      2. finds the best z over the full window `z_range` at the current
         xy position, by `method`:
           'gaussian'  -- ScanningOptimizeLogic's 1D Gaussian-fit
                          optimizer (same routine as
                          DriftCorrector._optimize_z()), which moves the
                          scanner to the fitted peak;
           'brightest' -- a plain 1D z scan; the signal is blended over
                          `n_average` neighbouring points (so a single
                          spurious point cannot win) and the scanner is
                          moved to the brightest blended point. Always
                          lands inside the window: if the true peak has
                          drifted out of range, z simply sits at the
                          brightest point still available (signal somewhat
                          lower, measurement unaffected otherwise). The
                          `n_average` points are blended by a moving
                          median by default (`average`);
      3. reloads the previously loaded pulsed asset
         (stop_laser_tracking_and_resume(resume_mode='none')), leaving the
         measurement stopped so the caller can start the next one.

    Fail-safe: if the optimization fails (fit failure / optimum outside
    `z_range` for 'gaussian'; unusable scan data for 'brightest'), z is
    moved back to the last good position (the starting one on the first
    call), a warning is logged, and the call returns normally -- a long
    measurement series is not aborted by one bad cycle (set
    `raise_on_failure=True` to raise instead). An optimum within
    `edge_fraction` of either end of the window is noted (the true peak
    may lie outside it; widen the window if the signal suffers).

    Usable directly as the `before_each` hook of coil_characterization's
    run_linearity_scans / measure_mixed_currents_odmr /
    sweep_single_coil_odmr: the returned {'z': ..., 'counts': ...} is then
    saved with every point (as 'tracking_z', 'tracking_counts'; counts =
    averaged signal at the chosen z for 'brightest', NaN otherwise and on
    calls skipped by every_n / min_interval).

    The 'brightest' scan temporarily sets the z scan range / resolution /
    frequency of scanning_probe_logic and disables saving to the scan
    history; the previous values are restored afterwards.

    Requires the injected globals scanning_probe_logic,
    scanning_optimize_logic and pulsed_master_logic (see module
    docstring).

    Parameters
    ----------
    z_range : (float, float)
        Allowed z window (scanner units, e.g. m), scanned in full each time.
    z_axis : str, default 'z'
    channel : str, default 'Sum'
        Data channel to maximize.
    z_resolution : int, default 100
        Points in the z scan.
    z_frequency : float, default 50.0
        z scan frequency, Hz.
    method : {'gaussian', 'brightest'}, default 'gaussian'
    n_average : int, default 1
        'brightest' only: number of neighbouring scan points blended
        (centred window; at the edges only the points available).
    average : {'median', 'mean'}, default 'median'
        'brightest' only: how the n_average points are blended. The
        median ignores a single spurious point entirely (n_average >= 3);
        the mean only dilutes it (and less so near the window edges).
    every_n : int, default 1
        Optimize on every n-th call only (other calls return the last z).
    min_interval : float, optional
        Also skip calls less than this many seconds after the last
        optimization.
    laser_channel : str, optional
        Passed to start_laser_tracking (default: from the pulsed logic's
        generation parameters).
    edge_fraction : float, default 0.05
    raise_on_failure : bool, default False
    verbose : bool, default True

    Attributes
    ----------
    history : list of dict
        One entry per optimization attempt: {'time', 'z_before', 'z',
        'counts', 'success', 'message'}.
    last_scan : dict or None
        'brightest' only: {'z', 'counts', 'smoothed'} arrays of the most
        recent z scan (see plot_last_scan()).
    """

    def __init__(self, z_range, z_axis='z', channel='Sum', z_resolution=100, z_frequency=50.0,
                 method='gaussian', n_average=1, average='median', every_n=1, min_interval=None,
                 laser_channel=None, edge_fraction=0.05, raise_on_failure=False, verbose=True):
        lo, hi = sorted(float(v) for v in z_range)
        if not hi > lo:
            raise ValueError(f'z_range must have nonzero width, got {z_range}.')
        if method not in ('gaussian', 'brightest'):
            raise ValueError(f"method must be 'gaussian' or 'brightest', got {method!r}.")
        self.z_range = (lo, hi)
        self.z_axis = z_axis
        self.channel = channel
        self.method = method
        self.n_average = max(1, int(n_average))
        if average not in ('median', 'mean'):
            raise ValueError(f"average must be 'median' or 'mean', got {average!r}.")
        self.average = average
        self.last_scan = None
        self.every_n = max(1, int(every_n))
        self.min_interval = min_interval
        self.laser_channel = laser_channel
        self.edge_fraction = edge_fraction
        self.raise_on_failure = raise_on_failure
        self.verbose = verbose
        self.history = []
        self._n_calls = 0
        self._last_time = None
        self._last_good_z = None

        # Reuse DriftCorrector's tested z optimization (full-window Gaussian fit).
        self._dc = DriftCorrector(z_axis=z_axis, channel=channel,
                                  position_bounds={z_axis: self.z_range})
        self._dc.z_resolution = int(z_resolution)
        self._dc.z_frequency = float(z_frequency)

    def _current_z(self):
        return DriftCorrector._to_float(scanning_probe_logic.scanner_target[self.z_axis])

    def _move_z(self, z):
        target = {ax: DriftCorrector._to_float(v)
                  for ax, v in dict(scanning_probe_logic.scanner_target).items()}
        target[self.z_axis] = float(z)
        scanning_probe_logic.set_target_position(target, move_blocking=True)

    def _brightest_z(self):
        """1D z scan over the window; move to the brightest point of the
        n_average-point moving average. Returns (z, averaged counts)."""
        spl = scanning_probe_logic
        z_ax = self.z_axis
        lo, hi = self.z_range
        prev_range = spl.scan_ranges.get(z_ax)
        prev_res = spl.scan_resolution.get(z_ax)
        prev_freq = spl.scan_frequency.get(z_ax)
        prev_hist = spl.save_to_history
        try:
            spl.save_to_history = False
            spl.set_scan_range(z_ax, (lo, hi))
            spl.set_scan_resolution(z_ax, int(self._dc.z_resolution))
            spl.set_scan_frequency(z_ax, float(self._dc.z_frequency))
            data = self._dc._run_scan_and_wait((z_ax,))
            counts = DriftCorrector._to_plain_array(data.data[self.channel]).ravel()
            scan_lo, scan_hi = (DriftCorrector._to_float(v) for v in data.settings.range[0])
        finally:
            if prev_range is not None:
                spl.set_scan_range(z_ax, tuple(prev_range))
            if prev_res is not None:
                spl.set_scan_resolution(z_ax, prev_res)
            if prev_freq is not None:
                spl.set_scan_frequency(z_ax, prev_freq)
            spl.save_to_history = prev_hist

        z = np.linspace(scan_lo, scan_hi, counts.size)
        if not np.any(np.isfinite(counts)):
            raise RuntimeError('z scan returned no valid data')
        # Centred moving median (default) or mean over n_average points; the
        # median ignores isolated spurious points, including at the edges.
        half = self.n_average // 2
        reduce = np.nanmedian if self.average == 'median' else np.nanmean
        smoothed = np.array([reduce(counts[max(0, k - half):k + half + 1])
                             if np.any(np.isfinite(counts[max(0, k - half):k + half + 1]))
                             else np.nan for k in range(counts.size)])
        i = int(np.nanargmax(smoothed))
        self.last_scan = {'z': z, 'counts': counts, 'smoothed': smoothed}
        z_best = float(np.clip(z[i], lo, hi))
        self._move_z(z_best)
        return z_best, float(smoothed[i])

    def optimize(self):
        """Run one z optimization now (regardless of every_n/min_interval).
        Returns the z position afterwards."""
        lo, hi = self.z_range
        z_before = self._current_z()
        if self._last_good_z is None:
            self._last_good_z = float(np.clip(z_before, lo, hi))
        record = {'time': time.time(), 'z_before': z_before, 'counts': float('nan')}

        start_laser_tracking(laser_channel=self.laser_channel, measurement_action='stop')
        try:
            try:
                if self.method == 'brightest':
                    z_new, record['counts'] = self._brightest_z()
                else:
                    z_new = self._dc._optimize_z()
                    if not lo <= z_new <= hi:
                        raise RuntimeError(f'optimum z = {z_new:.6g} outside the window '
                                           f'[{lo:.6g}, {hi:.6g}]')
            except Exception as exc:
                self._move_z(self._last_good_z)
                record.update(z=self._last_good_z, success=False, message=str(exc))
                self.history.append(record)
                msg = (f'z optimization failed ({exc}); z restored to the last good '
                       f'position {self._last_good_z:.6g}.')
                scanning_probe_logic.log.warning(msg)
                if self.verbose:
                    print(f'  ZTracker: {msg}')
                if self.raise_on_failure:
                    raise
                return self._last_good_z
        finally:
            stop_laser_tracking_and_resume(resume_mode='none')

        width = hi - lo
        near_edge = min(z_new - lo, hi - z_new) < self.edge_fraction * width
        if not near_edge:
            message = ''
        elif self.method == 'brightest':
            # Expected when the true peak drifts out of range -- just a note.
            message = 'brightest point at the edge of the z window (true peak likely outside)'
        else:
            message = 'optimum near the edge of the z window -- consider widening it'
            scanning_probe_logic.log.warning(f'ZTracker: {message} (z = {z_new:.6g}).')
        self._last_good_z = z_new
        record.update(z=z_new, success=True, message=message)
        self.history.append(record)
        if self.verbose:
            counts = (f', signal {record["counts"]:.4g}' if np.isfinite(record['counts'])
                      else '')
            print(f'  ZTracker: z {z_before:.6g} -> {z_new:.6g}{counts}'
                  + (f'  ({message})' if message else ''))
        return z_new

    def __call__(self):
        """Hook entry point: optimize if due (every_n / min_interval), and
        return {'z': current z, 'counts': signal at it (NaN if not
        measured this call)} for saving with the measurement."""
        self._n_calls += 1
        now = time.time()
        due = (self._n_calls - 1) % self.every_n == 0
        if self.min_interval is not None and self._last_time is not None:
            due = due and (now - self._last_time) >= self.min_interval
        if due:
            self._last_time = now
            z = self.optimize()
            counts = self.history[-1]['counts'] if self.history[-1]['success'] else float('nan')
        else:
            z, counts = self._current_z(), float('nan')
        return {'z': z, 'counts': counts}

    def plot_last_scan(self, ax=None):
        """'brightest' mode: plot the most recent z scan (raw and averaged)
        and the chosen z."""
        if self.last_scan is None:
            print('No z scan recorded yet (method="brightest" only).')
            return None
        if ax is None:
            fig, ax = plt.subplots(figsize=(7, 3.5))
        else:
            fig = ax.figure
        s = self.last_scan
        ax.plot(s['z'], s['counts'], '.', color='0.6', label='scan')
        ax.plot(s['z'], s['smoothed'], '-', label=f'{self.n_average}-point {self.average}')
        ax.axvline(self._last_good_z, color='r', ls='--', label='chosen z')
        ax.set_xlabel(self.z_axis)
        ax.set_ylabel(self.channel)
        ax.legend(fontsize=8)
        fig.tight_layout()
        return fig, ax

    def plot_history(self, ax=None):
        """Plot optimized z vs elapsed time (failed attempts in red)."""
        if not self.history:
            print('No z optimizations recorded yet.')
            return None
        t0 = self.history[0]['time']
        t = np.array([(h['time'] - t0) / 60.0 for h in self.history])
        z = np.array([h['z'] for h in self.history])
        ok = np.array([h['success'] for h in self.history])
        if ax is None:
            fig, ax = plt.subplots(figsize=(7, 3.5))
        else:
            fig = ax.figure
        ax.plot(t, z, '-', color='0.6')
        ax.plot(t[ok], z[ok], 'o', label='optimized')
        if np.any(~ok):
            ax.plot(t[~ok], z[~ok], 'x', color='r', label='failed (restored)')
        for edge in self.z_range:
            ax.axhline(edge, color='k', ls=':', lw=0.8)
        ax.set_xlabel('Elapsed time (min)')
        ax.set_ylabel(f'{self.z_axis}')
        ax.legend(fontsize=8)
        fig.tight_layout()
        return fig, ax
