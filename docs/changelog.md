# Changelog

## Pre-Release

### Breaking Changes
- Refactoring of data classes in `qudi.interface.scanning_probe_interface`. Will break custom modules
  implementing this interface.
- Add back scan data and back scan settings getter and setter methods
  to `qudi.interface.scanning_probe_interface`.

### Bugfixes
- Fix failure of saving plots in `QDPlotLogic` when fiting is used.
- Improve handling of errors during start of a scan in the scanning probe toolchain.
- Now correct microwave phases in predefined method generate_hahnecho_exp()
- "NFiniteSamplingInput supporting both trigger polarities via ConfigOption
- Old ODMR fits are now removed when starting a new measurement
- Remove hardcoded limits of the setpoint in the PID gui.
- Fixed missing `plot_index` when calling `QDPlotLogic._remove_plot` in `QDPlotLogic._set_plot_count`
- Fixed `QDPlotterGui` example config
- Fixed psu-dependent bugs for laser_quantum_laser
- Laser logic does not automatically start query loop, gui will still start it on startup

### New Features
- New `qudi.interface.scanning_probe_interface.ScanSettings` dataclass added.
- Checkers and clipping methods added to `qudi.interface.scanning_probe_interface.Constraints`
- Data classes of the scanning probe interface now have an improved hierarchy
  (e.g. `ScanData` contains `ScanSettings`).
- Improved scanning probe dummy: new image generator class, more realistic forward and backward scan simulation.
- Major refactoring of scanning probe toolchain, including scanning probe logic, optimizer logic and gui modules.
  Implementation of configurable backward scans.
- Add widgets to configure back scan resolution and frequency from scanning GUI.
- Add config option to save backward scan data to scanning data logic.
- Get current sweeps and runtime of fastcomtec fastcounter MCS6 when pulling the data trace. 
- Re-introduced tilt correction (from old core) to the scanning probe toolchain.
- Improved support for Stanford Research Systems signal generators
- Expanded documentation of the microwave interface
- Add new hardware module for the PID laser control functionality of HighFinesse wavemeters.
- Added option to specify custom save file name in scanning GUI (PR #148)



### Other
- Remove the (non-functional) wavemeter dummy based on the already removed wavemeter interface.

## Version 0.5.1

**⚠ DEPRECATION WARNING**
This is the last release before major changes in the interfaces of the scanning probe toolchain (see PR #97).
No action is required if you're using our `ni_scanning_probe_interfuse` hardware. If you integrated new hardware
into the scanning probe toolchain, you will be required to adapt to the new interface beyond this version.


### Breaking Changes
- Major rework of `qudi.interface.data_instream_interface.DataInStreamInterface`. Time series 
toolchain and NI x-series hardware module have been adapted but custom modules relying on this 
interface will break.  
Configuration for time series toolchain needs changes as well. See `default.cfg` or module 
docstrings.

### Bugfixes
- Basic data saving in `TimeSeriesReaderLogic` works now.
- Fix missing meta info `generation_method_parameters` that occurred for generated sequences with granularity mismatch.
- Ni Finite Sampling Input module now returns digital input channel values in "clicks/counts" per second and not "clicks/counts" per clock cycle
- Fix wrong asset name, non-invokable settings for AWG Tek 70k in sequence mode.
- Fix disfunctional `mw_source_smbv`
- Fix Keysight AWG sample rate only set-able with too coarse 10 MHz precision
- Fix various Poimanager crashes 

### New Features
- Added remote streamer support to `TimeSeriesReaderLogic`.
- New `qudi.interface.data_instream_interface.SampleTiming` Enum added to `DataInStreamInterface` 
constraints to allow non-uniform sampling mode.
- Pulsed and odmr now add fit parameters to saved meta data.
- New hardware module added that implements the HighFinesse wavemeter as a data instream device, replacing the old (non-functional) wavemeter toolchain.
- Add option to save waveforms and sequence information for debugging to pulser dummy
- Introduce plugins to the pulsed toolchain that allow more control over `generation_parameters` and can influence all loaded `pulse_objects`.

### Other
- Bumped `qudi-core` package minimum version requirement to v1.5.0
- Got rid of deprecated `qudi.core.interface` module usage
- Support for Python 3.10
- This version 0.5.1 fixes a requirement issue found while pushing release 0.5.0 to test-pypi 

## Version 0.4.0
### Breaking Changes
- `QDPlotLogic` has changed its public method signatures 
- `OkFpgaPulser` now has a mandatory config option pointing towards a directory with the bitfiles necessary.

### Bugfixes
- Resolved some issues with QDPlot GUI layouts and improved overall QDPlot GUI code quality
- catching null bytes in Keysight M3202A module
- 2D gaussian fit arguments changed to be compatible with the datafitting toolchain.
### New Features
- First stable version of new scanning toolchain (aka omniscan):
    - New `blocking` argument for scanner moves executed via `ScanningProbeInterface`
    - Multiple bugfixes for the new scanning toolchain with NICard. 
    - The NiScanningProbeInterfuse now polls data in chunks and independent of logic calls, as it should be.
    - More meta data of scans in saved data
- Support for Zaber (linear) motorized stages (in `qudi.hardware.motor.zaber_motion`)
- Overhaul of QDPlot toolchain (GUI and logic) to improve stability and consistency as well as 
adding minor GUI features.
- Added mixin `qudi.interface.mixins.process_control_switch.ProcessControlSwitchMixin` to provide 
optional default implementation satisfying the `SwitchInterface` for process control hardware 
modules implementing any of the interfaces contained in `qudi.interface.process_control_interface`
- Overhaul of PID toolchain: added units support, normalization option, gui reset feature,
dependency option for `process_control_dummy` to simulate PID control
- support for Thorlabs power meters using the TLPM driver
- pulsed toolchain: generation parameters of sequence saved as meta data

### Other
- Bumped `qudi-core` package minimum version requirement to v1.2.0
