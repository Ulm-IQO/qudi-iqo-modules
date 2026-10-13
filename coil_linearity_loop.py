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
# %cd C:\Software\qudi-iqo-modules
import importlib, coil_characterization, tracking
importlib.reload(coil_characterization)
importlib.reload(tracking)
# %run -i run_coil_linearity.py

# %%
# %cd C:\Software\qudi-iqo-modules
import importlib, coil_characterization, tracking
importlib.reload(coil_characterization)
importlib.reload(tracking)
# %run -i run_coil_linearity.py

# %%
# %cd C:\Software\qudi-iqo-modules
import importlib, coil_characterization, coil_linearity_analysis, tracking
for m in (coil_characterization, coil_linearity_analysis, tracking): importlib.reload(m)
# %run -i run_coil_superposition.py

# %%
import importlib, wire_centerline as wc, pulser_autocalibrate as autocal
importlib.reload(wc); importlib.reload(autocal)
#autocal.scanning_probe_logic = scanning_probe_logic     # as before, for logging

scan = wc.latest_xy_scan()
locations, anchor, info = autocal.compute_target_locations_auto(
    scan, [-1.0, -0.5, 0.0, 0.5, 1.0], expected_gap=2.8, plot=True)

# %%
# %cd C:\Software\qudi-iqo-modules
import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
# %run -i run_wire_field_mapping.py

# %%
import tracking, wire_centerline as wc
tracking.pulsed_master_logic = wc._qudi_module('pulsed_master_logic')
tracking.scanning_probe_logic = wc._qudi_module('scanning_probe_logic')
tracking.start_laser_tracking(measurement_action='stop')

# %%
tracking.stop_laser_tracking_and_resume(resume_mode='none')

# %%
# %cd C:\Software\qudi-iqo-modules
import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
# %run -i run_wire3_test.py

# %%
# %cd C:\Software\qudi-iqo-modules
import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
# %run -i run_wire_check.py

# %%
SPOT = 'top_rerun2'
REFERENCE_FILE = 'WireCheck_top/run_reference_20261008_162011.npz'

# %cd C:\Software\qudi-iqo-modules
import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
import re
_src = open('run_wire_check.py', encoding='utf-8').read()
_src = re.sub(r"^SPOT = .*$", f"SPOT = {SPOT!r}", _src, count=1, flags=re.M)
_src = re.sub(r"^REFERENCE_FILE = .*$", f"REFERENCE_FILE = {REFERENCE_FILE!r}", _src, count=1, flags=re.M)
exec(compile(_src, 'run_wire_check.py', 'exec'))

# %%
SPOT = 'bottom'
REFERENCE_FILE = None

# %cd C:\Software\qudi-iqo-modules
import importlib, wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration
for m in (wire_field_mapping, wire_centerline, pulser_autocalibrate, coil_characterization, tracking, coil_field_calibration): importlib.reload(m)
import re
_src = open('run_wire_check.py', encoding='utf-8').read()
_src = re.sub(r"^SPOT = .*$", f"SPOT = {SPOT!r}", _src, count=1, flags=re.M)
_src = re.sub(r"^REFERENCE_FILE = .*$", f"REFERENCE_FILE = {REFERENCE_FILE!r}", _src, count=1, flags=re.M)
exec(compile(_src, 'run_wire_check.py', 'exec'))

# %%
