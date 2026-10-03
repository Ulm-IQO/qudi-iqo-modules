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
import nv_field_fitting as nvfit
import numpy as np


# %%
def _to_plain_array(data):
    if hasattr(data, 'magnitude'):
        data = data.magnitude
    arr = np.array(data)
    return arr.astype(float, copy=False)

data = pulsed_master_logic.signal_data
freq = _to_plain_array(data[0])
signal = _to_plain_array(data[1])

# %%
quantization_axes = [
    (np.sqrt(2/3), 0, np.sqrt(1/3)),
    (-np.sqrt(2/3), 0, np.sqrt(1/3)),
    (0, np.sqrt(2/3), -np.sqrt(1/3)),
    (0, -np.sqrt(2/3), -np.sqrt(1/3))
]

# %%
B_fit, fit_info = nvfit.fit_field_from_spectrum(quantization_axes, freq, signal, B_guess=[-140e6, 230e6, -150e6], plot=True, min_dips=8, max_dips=8)

# %%
print(B_fit)

# %%
desired_field = np.array([56.569, -176.777, 500])

# %%
desired_currents = desired_field/np.array([55.15, 113.15, 110.57])

# %%
print(desired_currents)

# %%
import numpy as np

# Define the base matrix (before scaling)
M_base = np.array([
    [-37.9308, 16.0551, -6.4920],
    [ 42.5206, 21.8447,  1.4170],
    [ -0.0231, -0.0011, 78.2594]
])

# Apply the scaling factor
M = M_base * 2.8

# Target field vectors in the NV frame (MHz)
B1 = np.array([165, -85, 500])
B2 = np.array([165, -85, -500])

# Solve M @ I = B  =>  I = M^-1 @ B
I1 = np.linalg.solve(M, B1)
I2 = np.linalg.solve(M, B2)

# Display results
print("Matrix M_base:")
print(M_base)
print()

print("Matrix M:")
print(M)
print()

print(f"For B = {B1} MHz:")
print(f"  I = [{I1[0]:.4f}, {I1[1]:.4f}, {I1[2]:.4f}]")
print()

print(f"For B = {B2} MHz:")
print(f"  I = [{I2[0]:.4f}, {I2[1]:.4f}, {I2[2]:.4f}]")
print()

# Verification: multiply back to confirm
print("Verification (M @ I should equal B):")
print(f"  M @ I1 = {M @ I1}")
print(f"  M @ I2 = {M @ I2}")

# %%
nvfit.plot_toy_odmr_spectrum(quantization_axes, [-140e6, 230e6, -100e6])

# %%
print(np.array([-140, 230, -100])/np.array([55.15, 113.15, 110.57]))

# %%
print(np.array([-140e6, 230e6, -100e6])/np.array([1.39025068e+08, 2.20650210e+08, 1.12549408e+08])*np.array([2.176, 1.768, 0.904]))

# %%
import pickle

# --- Save ---
with open('results.pkl', 'wb') as f:
    pickle.dump(results, f)

# %%

# %%
import pickle

# --- Load ---
with open('results.pkl', 'rb') as f:
    results = pickle.load(f)

import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic

# %%
results = coilchar.characterize_coil_odmr_response(
    seq_name='cw_odmr',  # must already be loaded
    num_sweeps=25000,
)

# %%
M, B_offset, fit_info = coilchar.fit_coil_calibration_matrix(
    results, quantization_axes, D=2.874e9,
    fit_offset=False,
    guess_scale={'X': 50e6, 'Y': 100e6, 'Z': 100e6},
    plot_calibration=True,
)

# %%
coil_results = results

# %%
import numpy as np
import matplotlib.pyplot as plt
import nv_field_fitting as nvfit

quantization_axes = [
    (np.sqrt(2/3), 0, np.sqrt(1/3)),
    (-np.sqrt(2/3), 0, np.sqrt(1/3)),
    (0, np.sqrt(2/3), -np.sqrt(1/3)),
    (0, -np.sqrt(2/3), -np.sqrt(1/3)),
]

D = 2.87e9

# --- Isolate and sort the Z sweep ---
z_points = sorted((p for p in coil_results if p['axis'] == 'Z'), key=lambda p: p['current'])
print(f'Found {len(z_points)} Z-sweep points.')

# --- Step 1: plot every raw spectrum with its Lorentzian fit (forcing n_dips=2) ---
for p in z_points:
    lorentzian_fit = nvfit.fit_multi_lorentzian_dips(
        p['freq'], p['signal'], n_dips=2, plot=True
    )
    plt.gca().set_title(f"Z current = {p['current']:+.3f} A | " + plt.gca().get_title())
    plt.show()

    print(f"I_z={p['current']:+.3f}  centers={np.round(lorentzian_fit['centers']/1e6, 3)} MHz  "
          f"amplitudes={np.round(lorentzian_fit['amplitudes'], 3)}  "
          f"sigmas={np.round(lorentzian_fit['sigmas']/1e6, 3)} MHz  "
          f"bic={lorentzian_fit['bic']:.2f}")

# %%
coil_results = coilchar.characterize_coil_odmr_response(
    seq_name='cw_odmr',
    num_sweeps=20000,
    n_points=5,
    x_range=(2.8, 4.0),
    yz_range=(1.9, 2.5),
)

# %%
# --- Save ---
with open('coil_results.pkl', 'wb') as f:
    pickle.dump(coil_results, f)

# %%
M, B_offset, fit_info = coilchar.fit_coil_calibration_matrix(
    coil_results, quantization_axes, D=2.87e9,
    fit_offset=False,   # important: force through origin, since we have no near-zero data
    guess_scale={'X': 56e6, 'Y': 118e6, 'Z': 149e6},
    plot_calibration=True,
)

# %%
import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic

fit_results = {}
for axis, currents in [('X', np.linspace(-3, 3, 9)),
                        ('Y', np.linspace(-1.5, 1.5, 9)),
                        ('Z', np.linspace(-1.5, 1.5, 9))]:

    total_sweep = coilchar.sweep_single_coil_odmr(axis, currents, seq_name='odmr_seq', num_sweeps=10000)

    dip_guesses = [[2.855e9, 2.885e9], [2.840e9, 2.900e9], ...]  # one list per current

    total_sweep, u_fit, B_offset_fit, fit_info = coilchar.interactive_fit_coil_axis(
        total_sweep, dip_guesses, quantization_axes=my_quant_axes)

    coilchar.plot_coil_axis_fan(total_sweep, u_fit, B_offset_fit, my_quant_axes)

    fit_results[axis] = (u_fit, B_offset_fit, fit_info)

M, B_offset, info = coilchar.assemble_coil_calibration(fit_results)

# %%
import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic

# %%
dip_guesses_x = [[2.73e9, 2.86e9, 2.90e9, 3.01e9], [2.72e9, 2.86e9, 2.902e9, 3.015e9], [2.715e9, 2.86e9, 2.91e9, 3.04e9], [2.702e9, 2.87e9, 2.91e9, 3.04e9], [2.70e9, 2.88e9, 2.92e9, 3.06e9]]

# %%
total_sweep_x = coilchar.sweep_single_coil_odmr('X', np.linspace(3, 4, 5), seq_name='cw_odmr', num_sweeps=25000)

# %%
total_sweep_x, u_fit, B_offset_fit, fit_info = coilchar.interactive_fit_coil_axis(
        total_sweep_x, dip_guesses_x, quantization_axes=quantization_axes)

# %%
total_sweep_y = coilchar.sweep_single_coil_odmr('Y', np.linspace(2, 2.3, 4), seq_name='cw_odmr', num_sweeps=25000)

# %%
total_sweep_z = coilchar.sweep_single_coil_odmr('Z', np.linspace(1.5, 2, 5), seq_name='cw_odmr', num_sweeps=25000)

# %%
dipguesses = [[2.79e9, 2.985e9], [2.78e9, 2.99e9], [2.78e9, 3.00e9], [2.775e9, 3.01e9], [2.773e9, 3.02e9]]

# %%
total_sweep_z, u_fit, B_offset_fit, fit_info = coilchar.interactive_fit_coil_axis(
        total_sweep_z, dipguesses, quantization_axes=quantization_axes)

# %%
# %matplotlib widget
import coil_characterization as coilchar
coilchar.pulsed_master_logic = pulsed_master_logic
coilchar.coil_control_logic = coil_control_logic

total_sweep = coilchar.sweep_single_coil_odmr('X', np.linspace(3, 4, 4),
                                               seq_name='cw_odmr', num_sweeps=10000)

dip_guesses, dip_windows = coilchar.pick_dips_interactive(total_sweep)

total_sweep, u_fit, B_offset_fit, fit_info = coilchar.interactive_fit_coil_axis(
    total_sweep, dip_guesses, dip_windows=dip_windows, quantization_axes=quantization_axes)

# %%
import sys
# !{sys.executable} -m pip show ipympl ipywidgets jupyterlab | grep -E "Name|Version"

# %%
