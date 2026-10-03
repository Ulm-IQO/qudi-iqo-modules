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
import numpy as np

# %%
quantization_axes = [
    (np.sqrt(2/3), 0, np.sqrt(1/3)),
    (-np.sqrt(2/3), 0, np.sqrt(1/3)),
    (0, np.sqrt(2/3), -np.sqrt(1/3)),
    (0, -np.sqrt(2/3), -np.sqrt(1/3))
]

# %%
import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic

# %%
# %matplotlib widget
import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic

total_sweep = coilchar.sweep_single_coil_odmr('X', np.linspace(3, 4, 4),
                                               seq_name='cw_odmr', num_sweeps=15000, plot=True)

dip_guesses, dip_windows = coilchar.pick_dips_interactive(total_sweep)

total_sweep, u_fit, B_offset_fit, fit_info = coilchar.interactive_fit_coil_axis(
    total_sweep, dip_guesses, dip_windows=dip_windows, quantization_axes=quantization_axes)

# %%
import nv_field_fitting as nvfit
import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic
import numpy as np

# %%
quantization_axes = [
    (np.sqrt(2/3), 0, np.sqrt(1/3)),
    (-np.sqrt(2/3), 0, np.sqrt(1/3)),
    (0, np.sqrt(2/3), -np.sqrt(1/3)),
    (0, -np.sqrt(2/3), -np.sqrt(1/3))
]

# %%
# %matplotlib widget

# %%
data = pulsed_master_logic.signal_data
freq, signal = nvfit.skip_first_odmr_points(
    coilchar._to_plain_array(data[0]), coilchar._to_plain_array(data[1]), 0)

fit = nvfit.fit_odmr_dips_interactive(freq, signal)            # e.g. X coil at 4 A
s_X, _ = nvfit.estimate_coil_strength_from_dips(quantization_axes, fit['centers'], 4.0, (1, 0, 0))

print(s_X/1e6)

# %%
data = pulsed_master_logic.signal_data
freq, signal = nvfit.skip_first_odmr_points(
    coilchar._to_plain_array(data[0]), coilchar._to_plain_array(data[1]), 0)

fit = nvfit.fit_odmr_dips_interactive(freq, signal)            # e.g. X coil at 4 A
s_Y, _ = nvfit.estimate_coil_strength_from_dips(quantization_axes, fit['centers'], 3.0, (0, 1, 0))

print(s_Y/1e6)

# %%
data = pulsed_master_logic.signal_data
freq, signal = nvfit.skip_first_odmr_points(
    coilchar._to_plain_array(data[0]), coilchar._to_plain_array(data[1]), 0)

fit = nvfit.fit_odmr_dips_interactive(freq, signal)            # e.g. X coil at 4 A
s_Z, _ = nvfit.estimate_coil_strength_from_dips(quantization_axes, fit['centers'], 2.5, (0, 0, 1))

print(s_Z/1e6)

# %%
# Step 2, automated:
M, B_offset, info, mixed_sweep = coilchar.auto_calibrate_coils(
    {'X': s_X, 'Y': s_Y, 'Z': s_Z}, quantization_axes, 'cw_odmr',
    target_field_gauss=100.0, max_current={'X': 4.0, 'Y': 3.0, 'Z': 3.0}, num_sweeps=25000, skip_first_points=8)

# %%
M, B_offset, info = coilchar.full_auto_calibrate_coils(
    quantization_axes, {'X': 4.0, 'Y': 3.0, 'Z': 3.0}, 'cw_odmr',
    freq_start=2.40e9, freq_stop=3.4e9, n_freq_points=200,
    skip_first_points=10, settle_time=5.0, num_sweeps=25000, D=2.874e9)
if not info['validated']:
    print(info['warnings'])

# %%
coilchar._run_odmr_sequence_and_collect('cw_odmr', 1000)

# %%
coilchar.save_odmr_point()

# %%
# %matplotlib widget
import importlib, coil_characterization as coilchar, nv_field_fitting as nvfit
importlib.reload(nvfit); importlib.reload(coilchar)

# %%
summary = coilchar.review_odmr_folder(skip_first_points=3)

# %%
M, B_offset, info = nvfit.fit_coil_matrix_from_measurements(
    'FittedCoilODMRs', quantization_axes, freq_range=(2.4e9, 3.4e9), D=2.874e9, coil_strength_guess={'X': 55e6, 'Y': 110e6, 'Z': 110e6}, max_rms=14e6)

# %%
