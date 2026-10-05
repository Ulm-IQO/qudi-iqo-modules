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
# 1. Acquire a reference scan once, e.g. at the start of a long measurement
scanning_probe_logic.set_scan_range('x', (40, 55))
scanning_probe_logic.set_scan_range('y', (30, 45))
scanning_probe_logic.set_scan_resolution('x', 70)
scanning_probe_logic.set_scan_resolution('y', 50)

scanning_probe_logic.start_scan(('x', 'y'))
while scanning_probe_logic.module_state() != 'idle':
    time.sleep(0.2)

reference_scan = scanning_probe_logic.scan_data   # store this for later comparison

# %%
import time
import numpy as np
from skimage.registration import phase_cross_correlation


def _to_plain_array(data):
    """Convert to a plain float ndarray, working around array-likes
    (e.g. pint Quantities) whose __array__() doesn't accept a dtype arg."""
    return np.asarray(np.array(data), dtype=float)


def correct_drift(reference_scan, channel='Sum', move=True, upsample_factor=10):
    """
    Correct for sample/stage drift by cross-correlating a freshly acquired scan
    against a previously stored reference scan, then shifting the scanner
    target position to compensate.

    Parameters
    ----------
    reference_scan : ScanData
        A previously acquired 2D ScanData object, e.g. obtained via
        `reference_scan = scanning_probe_logic.scan_data` right after a scan.
    channel : str
        Name of the data channel to use for cross-correlation (default 'Sum').
    move : bool
        If True, actually move the scanner target to correct for drift.
        If False, only compute and report the drift (dry run).
    upsample_factor : int
        Upsampling factor for subpixel registration accuracy.

    Returns
    -------
    drift : dict
        Computed drift per axis, in µm.
    new_target : dict
        Scanner target position after correction (or that would be applied
        if move=False).
    """

    axes = reference_scan.settings.axes
    if len(axes) != 2:
        raise ValueError('Drift correction currently only supports 2D scans.')

    ref_range = reference_scan.settings.range
    ref_resolution = reference_scan.settings.resolution

    # --- configure and acquire a new scan with IDENTICAL settings to reference ---
    for i, ax in enumerate(axes):
        scanning_probe_logic.set_scan_range(ax, ref_range[i])
        scanning_probe_logic.set_scan_resolution(ax, ref_resolution[i])

    scanning_probe_logic.start_scan(axes)
    while scanning_probe_logic.module_state() != 'idle':
        time.sleep(0.2)

    new_scan = scanning_probe_logic.scan_data

    ref_img = _to_plain_array(reference_scan.data[channel])
    new_img = _to_plain_array(new_scan.data[channel])

    if ref_img.shape != new_img.shape:
        raise ValueError('Reference and current scan images have different '
                          'shapes; cannot cross-correlate.')

    # --- subpixel cross-correlation to find shift between the two images ---
    shift_px, error, diffphase = phase_cross_correlation(
        ref_img, new_img, upsample_factor=upsample_factor
    )

    # --- convert pixel shift to physical units (µm, since ranges are in µm) ---
    px_size = [
        (ref_range[i][1] - ref_range[i][0]) / ref_resolution[i]
        for i in range(2)
    ]
    drift = {
        axes[0]: shift_px[0] * px_size[0],
        axes[1]: shift_px[1] * px_size[1],
    }

    scanning_probe_logic.log.info(f'Detected drift (µm): {drift}')

    # --- apply correction by shifting the scanner target position ---
    current_target = scanning_probe_logic.scanner_target
    new_target = dict(current_target)
    for ax in axes:
        new_target[ax] = current_target[ax] - drift[ax]

    if move:
        scanning_probe_logic.set_target_position(new_target, move_blocking=True)
        scanning_probe_logic.log.info(f'Corrected target position: {new_target}')

    return drift, new_target


# %%
drift, new_target = correct_drift(reference_scan)
print(f'Drift: {drift} µm, moved to {new_target}')

# %%
known_shift = {'x': 2.0}
current = scanning_probe_logic.scanner_target
target = dict(current)
target['x'] += known_shift['x']
scanning_probe_logic.set_target_position(target, move_blocking=True)

drift, _ = correct_drift(reference_scan, move=False)
print(drift)  # expect drift['x'] ≈ +2.0

# %%
import copy

def simulate_drift_test(reference_scan, channel='Sum', shift_pixels=(3, -5)):
    """
    Create a synthetic 'new_scan' by rolling the reference image by a known
    pixel amount, then verify that the sign convention correctly recovers it.
    """
    ref_img = _to_plain_array(reference_scan.data[channel])

    # Roll the image to simulate a known pixel-space shift
    shifted_img = np.roll(ref_img, shift=shift_pixels, axis=(0, 1))

    shift_px, error, diffphase = phase_cross_correlation(
        ref_img, shifted_img, upsample_factor=10
    )

    print(f'Applied shift (pixels): {shift_pixels}')
    print(f'Recovered shift_px from phase_cross_correlation: {shift_px}')


# %%
simulate_drift_test(reference_scan, shift_pixels=(3, -5))

# %%
import time
import numpy as np
from skimage.registration import phase_cross_correlation
from qudi.logic.scanning_optimize_logic import OptimizationType, OptimizationMethod


def _to_float(value):
    """Coerce a numpy/pint/other numeric type into a plain Python float."""
    if hasattr(value, 'magnitude'):
        value = value.magnitude
    return float(value)


def _to_plain_array(data):
    """Convert to a plain float ndarray, working around array-likes
    (e.g. pint Quantities) whose __array__() doesn't support the
    NumPy 2.0 dtype/copy keyword protocol."""
    if hasattr(data, 'magnitude'):
        data = data.magnitude  # strip pint units if present
    arr = np.array(data)       # no dtype/copy kwargs here -> avoids the warning
    return arr.astype(float, copy=False)


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


def _run_scan_and_wait(axes, timeout=None, poll_interval=0.2, safety_factor=12.0, min_timeout=10.0):
    """
    Start a scan on the given axes and block until finished.
    Verifies that a NEW scan actually ran (by checking the timestamp AND
    that the returned data isn't just NaN placeholder data), retrying if not.
    """
    if timeout is None:
        estimated = _estimate_scan_duration(axes)
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
        arr = _to_plain_array(new_data.data[channel])
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
        self.reference_z_scan = None
        self.reference_target = None

        self.total_drift = {ax: 0.0 for ax in (*xy_axes, z_axis)}

    # ------------------------------------------------------------------
    def set_reference(self, xy_scan, z_scan, target_position):
        """
        Call once at the start, right after acquiring the initial xy and z
        reference scans, with the target sitting on the structure/NV of
        interest (assumed to already be at the brightness-optimal z).
        """
        self.reference_xy_scan = xy_scan.copy()
        self.reference_z_scan = z_scan.copy()
        self.reference_target = dict(target_position)
        self.total_drift = {ax: 0.0 for ax in self.total_drift}

        ref_img = _to_plain_array(self.reference_xy_scan.data[self.channel])
        self._check_image_quality(ref_img, label='reference xy scan')

    # ------------------------------------------------------------------
    def _full_z_range(self):
        if self.z_axis not in self.position_bounds:
            raise ValueError(
                f'position_bounds must include an entry for z axis '
                f'"{self.z_axis}" to define the full z scan range.'
            )
        return tuple(self.position_bounds[self.z_axis])

    def _clip_position(self, ax, value):
        value = _to_float(value)
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
        width = _to_float(ref_range[1]) - _to_float(ref_range[0])
        shifted_min = _to_float(ref_range[0]) + _to_float(drift)
        shifted_max = _to_float(ref_range[1]) + _to_float(drift)

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

    # ------------------------------------------------------------------
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

        z_res = self.reference_z_scan.settings.resolution[0]
        z_freq = _to_float(self.reference_z_scan.settings.frequency)

        # Temporarily center the z target so that the optimizer's centered
        # scan window (target +/- range/2) covers exactly the full z range.
        current_target = dict(scanning_probe_logic.scanner_target)
        temp_target = dict(current_target)
        temp_target[z_axis] = z_center
        scanning_probe_logic.set_target_position(
            {ax: _to_float(v) for ax, v in temp_target.items()}, move_blocking=True
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
            timeout = max(10.0, _estimate_scan_duration((z_axis,)) * 12.0)

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

        return _to_float(optimal[z_axis])

    # ------------------------------------------------------------------
    def correct_drift(self, move=True):
        """
        Perform one drift-correction cycle:
          1. xy scan (range shifted by current drift estimate) + cross-correlation
             (with fail-safe rejection of low-confidence / implausible matches)
          2. move target's x/y to the corrected position
          3. z optimization over the FULL configured z range at that xy
             position, via ScanningOptimizeLogic (Gaussian fit)
          4. return the total accumulated drift and the resulting target
             position

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

        new_xy_scan = _run_scan_and_wait(xy_axes)

        ref_img = _to_plain_array(self.reference_xy_scan.data[self.channel])
        new_img = _to_plain_array(new_xy_scan.data[self.channel])

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
        target_pos = {ax: _to_float(val) for ax, val in target_pos.items()}
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
                    {ax: _to_float(v) for ax, v in new_target.items()}, move_blocking=True
                )

        new_target = {ax: _to_float(val) for ax, val in new_target.items()}
        scanning_probe_logic.log.info(f'Corrected target position: {new_target}')

        return dict(self.total_drift), new_target


# %%
print(scanning_probe_logic.scanner_target)
drift_corrector.reference_target = scanning_probe_logic.scanner_target

# %%
# --- Step 0: create the tracker (once per measurement session) ---
drift_corrector = DriftCorrector(
    xy_axes=('x', 'y'),
    z_axis='z',
    channel='Sum',
    position_bounds={
        'x': (10, 90),    # µm
        'y': (10, 90),    # µm
        'z': (0, 6.2),     # µm
    }
)

# %%
# --- Step 2: acquire initial xy reference scan ---
scanning_probe_logic.set_scan_range('x', (40, 60))
scanning_probe_logic.set_scan_range('y', (35, 55))
scanning_probe_logic.set_scan_resolution('x', 60)
scanning_probe_logic.set_scan_resolution('y', 60)
scanning_probe_logic.start_scan(('x', 'y'))
while scanning_probe_logic.module_state() != 'idle':
    time.sleep(0.2)
reference_xy_scan = scanning_probe_logic.scan_data

# --- Step 3: acquire initial z reference scan (at the current xy position) ---
scanning_probe_logic.set_scan_range('z', (0, 6.2))
scanning_probe_logic.set_scan_resolution('z', 100)
scanning_probe_logic.start_scan(('z',))
while scanning_probe_logic.module_state() != 'idle':
    time.sleep(0.2)
reference_z_scan = scanning_probe_logic.scan_data

# %%
# --- Step 4: register the reference with the tracker ---
drift_corrector.set_reference(reference_xy_scan, reference_z_scan,
                               scanning_probe_logic.scanner_target)

# %%
# --- Step 5: periodically call this during your experiment ---
drift, new_target = drift_corrector.correct_drift()
print(f'Cumulative drift (µm): {drift}')
print(f'New target position: {new_target}')

# %%
# --- Step 5: periodically call this during your experiment ---
drift, new_target = drift_corrector.correct_drift()
print(f'Cumulative drift (µm): {drift}')
print(f'New target position: {new_target}')

# %%
# Reproduce the failure directly, bypassing exception swallowing

# 1. First, run a z-scan (to get into the "bad" state)
scanning_probe_logic.set_scan_range('z', (0, 50))
scanning_probe_logic.set_scan_resolution('z', 100)
scanning_probe_logic.start_scan(('z',))
while scanning_probe_logic.module_state() != 'idle':
    time.sleep(0.2)

# 2. Now try to directly configure + start an xy scan, catching the raw exception
settings = scanning_probe_logic.create_scan_settings(('x', 'y'))
back_settings = scanning_probe_logic.create_back_scan_settings(('x', 'y'))

try:
    scanning_probe_logic._scanner().configure_scan(settings)
    print("configure_scan succeeded")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("configure_scan FAILED:", e)

# %%
try:
    scanning_probe_logic._scanner().start_scan()
    print("start_scan succeeded")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("start_scan FAILED:", e)

# %%
print(scanning_probe_logic._scanner().module_state())
print(scanning_probe_logic._scanner().constraints)

# %%
