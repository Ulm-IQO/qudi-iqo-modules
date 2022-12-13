# Changelog

## Pre-Release

### Breaking Changes
- `QDPlotLogic` has changed its public method signatures

### Bugfixes
- Resolved some issues with QDPlot GUI layouts and improved overall QDPlot GUI code quality
- catching null bytes in Keysight M3202A module
- Multiple bugfixes for the new scanning toolchain with NICard. 
- The NiScanningProbeInterfuse now polls data in chunks and independent of logic calls, as it should be.

### New Features
- Support for Zaber (linear) motorized stages (in `qudi.hardware.motor.zaber_motion`)
- Overhaul of QDPlot toolchain (GUI and logic) to improve stability and consistency as well as 
adding minor GUI features.
- Added mixin `qudi.interface.mixins.process_control_switch.ProcessControlSwitchMixin` to provide 
optional default implementation satisfying the `SwitchInterface` for process control hardware 
modules implementing any of the interfaces contained in `qudi.interface.process_control_interface`
- New `blocking` argument for moves executed via `ScanningProbeInterface`

### Other
- Bumped `qudi-core` package minimum version requirement to v1.2.0
