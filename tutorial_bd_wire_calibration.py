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

# %% [markdown]
# # Tutorial file for BD pulser calibration, including tracking

# %% [markdown]
# ## Required imports

# %%
import pulser_autocalibrate as autocal
autocal.scanning_probe_logic = scanning_probe_logic
autocal.pulsed_master_logic = pulsed_master_logic
autocal.analog_output_logic = analog_output_logic

# %%
import tracking
tracking.scanning_probe_logic = scanning_probe_logic
tracking.scanning_optimize_logic = scanning_optimize_logic
tracking.pulsed_master_logic = pulsed_master_logic

# %% [markdown]
# ## Settings reference scans and target points

# %%
## First, get a high-resolution flashlight-on scan to fit the wire edges and get the target points for calibration:
flashlight_reference_xy_scan = scanning_probe_logic.scan_data

## Now, at two x-values, eyeball the top/bottom y-values for each wire:
# Rough estimates: for each wire, at 2 x-locations, (x, y_top_guess, y_bottom_guess).
# These don't need to be precise -- just roughly where each wire's edges are,
# eyeballed from a plot of the reference scan.
wire_estimates = [
    # Wire A (e.g. the visually upper wire)
    [(30.411, 48.075, 44.139), (43.764, 48.202, 43.908)],
    # Wire B (the lower wire)
    [(30.411, 40.110, 36.355), (43.764, 40.476, 36.538)],
]

## Get the target locations based on the desired center x-position and the spacings:
locations, anchor, fit_info = autocal.compute_target_locations(
    flashlight_reference_xy_scan,
    x_position=36.112,
    relative_offsets=[-1.0, -0.5, 0.0, 0.5, 1.0],
    wire_estimates=wire_estimates,
    x_window=1.0,
    search_half_width=1.5,          # keep smaller than ~half of wire_width(5)/gap_width(6)
    plot=True,                      # visually verify fit before trusting it
)

print(f'Anchor point: {anchor}')
print(f'Target locations (perpendicular offsets): {locations}')

# %%
## Now, set the flashlight-off reference scan for tracking purposes:
tracking_reference_xy_scan = scanning_probe_logic.scan_data

# %%
## Create a DriftCorrector object for this reference:
drift_corrector = tracking.DriftCorrector(
    xy_axes=('x', 'y'), z_axis='z', channel='Sum',
    position_bounds={'x': (10, 90), 'y': (10, 90), 'z': (0, 6.2)},
    max_xy_correlation_error=0.5, max_xy_shift=5.0,
)

drift_corrector.set_reference(tracking_reference_xy_scan, scanning_probe_logic.scanner_target,
                              z_resolution=100, z_frequency=50.0)

# %% [markdown]
# ## Running the calibration and saving the data

# %%
## Choose the wire values you want to calibrate over:
wire_settings = [
    (2.0, 0.0, 0.0, 0.0),
    (0.0, 0.25, 0.0, 0.0),
    (0.0, 0.5, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.25, 0.0),
    (0.0, 0.0, 0.5, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 2.0)
]

# %%
## Choose the ESR parameters you want:
odmr_kwargs = dict(
    freq_start=2.55e9, freq_stop=3.25e9, num_of_points=100,
    mw_amp=0.2, mw_length=10e-6,
    always_on_ch='d_ch15', gradient_mode=0, gradient_mode_ch='d_ch6',
    pulser_ch='d_ch3', duty_cycle=0.2,
)

# %%
## Run the calibration (here, use_daq1=True means that we are using pulsing field mode):
results_daq1 = autocal.run_field_mapping_experiment(
    locations, wire_settings, use_daq1=True,
    odmr_kwargs=odmr_kwargs, num_sweeps=10000,
    drift_corrector=drift_corrector,
)

# %%
