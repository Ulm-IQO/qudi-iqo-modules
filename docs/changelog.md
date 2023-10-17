# Changelog

## Pre-Release
### Breaking Changes
- Major rework of `qudi.interface.data_instream_interface.DataInStreamInterface`. Time series 
toolchain and NI x-series hardware module have been adapted but custom modules relying on this 
interface will break.  
Configuration for time series toolchain needs changes as well. See `default.cfg` or module 
docstrings.

### Bugfixes
- Basic data saving in `TimeSeriesReaderLogic` works now.
- Added remote streamer support to `TimeSeriesReaderLogic`.
- Ni Finite Sampling Input module now returns digital input channel values in "clicks/counts" per second and not "clicks/counts" per clock cycle 

### New Features
- New `qudi.interface.data_instream_interface.SampleTiming` Enum added to `DataInStreamInterface` 
constraints to allow non-uniform sampling mode.
- New hardware module added that implements the HighFinesse wavemeter as a data instream device, replacing the old (non-functional) wavemeter toolchain.

### Other
- Bumped `qudi-core` package minimum version requirement to v1.4.0
- Got rid of deprecated `qudi.core.interface` module usage
- Support for Python 3.10

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
