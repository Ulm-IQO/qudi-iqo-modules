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
scanning_probe_logic.set_scan_range('x', (30, 50))
scanning_probe_logic.set_scan_range('y', (30, 50))
scanning_probe_logic.set_scan_resolution('x', 80)
scanning_probe_logic.set_scan_resolution('y', 80)

scanning_probe_logic.start_scan(('x', 'y'))
while scanning_probe_logic.module_state() != 'idle':
    time.sleep(0.2)

reference_scan = scanning_probe_logic.scan_data   # store this for later comparison

# %%
import time
import numpy as np
from skimage.registration import phase_cross_correlation


def _to_plain_array(data):
    """Strip pint units (if present) and return a plain float ndarray."""
    if hasattr(data, 'magnitude'):
        data = data.magnitude
    return np.asarray(data, dtype=float)


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
        Computed drift per axis, in µm, e.g. {'x': dx, 'y': dy}.
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
    # shift_px follows the array index order of ref_img/new_img.
    # Verify this matches (axes[0], axes[1]) order on your setup - see note below.

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
        new_target[ax] = current_target[ax] + drift[ax]

    if move:
        scanning_probe_logic.set_target_position(new_target, move_blocking=True)
        scanning_probe_logic.log.info(f'Corrected target position: {new_target}')

    return drift, new_target


# %%
drift, new_target = correct_drift(reference_scan)
print(f'Drift: {drift} µm, moved to {new_target}')

# %%
reference_scan.data['Sum']

# %%
d = reference_scan.data['Sum']
print(type(d))
print(getattr(d, 'units', None))  # if pint, this will show something like 'count / second'

# %%
