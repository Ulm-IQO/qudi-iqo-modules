# Setting up confocal scanning
## Introduction

The scanning toolchain is designed to be fully configurable with respect to multiple signal inputs (eg. APD counts, analogue inputs) and an arbitrary scanning axes configuration.
To this end, its written in a very modular way.
A typical working toolchain consists out of the following qudi modules:

logic:
- [scanning_data_logic](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/logic/scanning_data_logic.py#L50)
- [scanning_probe_logic](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/logic/scanning_probe_logic.py#L33)
- [scanning_optimize_logic](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/logic/scanning_optimize_logic.py#L33)

hardware (here NI X-series):
- [analog_output](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/ni_x_series/ni_x_series_analog_output.py#L39)
- [finite_sampling_input](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/ni_x_series/ni_x_series_finite_sampling_input.py#L46)
- [finite_sampling_io](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/ni_x_series/ni_x_series_finite_sampling_io.py#L50)
- ([in_streamer](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/ni_x_series/ni_x_series_in_streamer.py#L45), optional)

gui:
- [scannergui](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/gui/scanning/scannergui.py#L83)

## Example config

These modules need to be configured and connected in your qudi config file.
We here provide an examplary config for a toolchain based on a NI X-series scanner with analogue output and digital (APD TTL) input.
Note: This readme file might not be up-to-date with the most recent development. We advice to check the examplary config present in the 
docstring of every module's python file. In the list above, a direct link for every module is provided:


    gui:
        scanner_gui:
          module.Class: 'scanning.scannergui.ScannerGui'
          options:  
              image_axes_padding: 0.02
              default_position_unit_prefix: null  # optional, use unit prefix characters, e.g. 'u' or 'n'
              optimizer_plot_dimensions: [2,1]
          connect:
              scanning_logic: scanning_probe_logic
              data_logic: scanning_data_logic
              optimize_logic: scanning_optimize_logic
    
    
    logic:
        scanning_probe_logic:
            module.Class: 'scanning_probe_logic.ScanningProbeLogic'
            options:  
                max_history_length: 20
                max_scan_update_interval: 2
                position_update_interval: 1
            connect:
                scanner: ni_scanner

        scanning_data_logic:
            module.Class: 'scanning_data_logic.ScanningDataLogic'
            options:  
                max_history_length: 20
            connect:
                scan_logic: scanning_probe_logic

        scanning_optimize_logic:
            module.Class: 'scanning_optimize_logic.ScanningOptimizeLogic'
            connect:
                scan_logic: scanning_probe_logic

    
    hardware:
        ni_scanner:
            module.Class: 'interfuse.ni_scanning_probe_interfuse.NiScanningProbeInterfuse'
            connect:
                scan_hardware: 'ni_io'
                analog_output: 'ni_ao'
            options:  
                ni_channel_mapping:
                    x: 'ao0'
                    y: 'ao1'
                    z: 'ao2'
                    #a: 'ao3'
                    APD1: 'PFI8'
                    #APD2: 'PFI9'
                    #AI0: 'ai0'
                    #APD3: 'PFI10'
                position_ranges: # in m
                    x: [0, 200e-6]
                    y: [0, 200e-6]
                    z: [-100e-6, 100e-6]
                frequency_ranges:
                    x: [1, 5000]
                    y: [1, 5000]
                    z: [1, 1000]
                resolution_ranges:
                    x: [1, 10000]
                    y: [1, 10000]
                    z: [1, 10000]
                input_channel_units:
                    APD1: 'c/s'
                    #AI0: 'V'
                    #APD2: 'c/s'
                    #APD3: 'c/s'
                backwards_line_resolution: 50 # optional
                maximum_move_velocity: 400e-6 #m/s
        
        # dummy, if no real hardware available
        scanner_dummy:
            module.Class: 'dummy.scanning_probe_dummy.ScanningProbeDummy'
            options:
                position_ranges:
                    'x': [0, 200e-6]
                    'y': [0, 200e-6]
                    'z': [-100e-6, 100e-6]
                frequency_ranges:
                    'x': [0, 10000]
                    'y': [0, 10000]
                    'z': [0, 5000]
                resolution_ranges:
                    'x': [2, 2147483647]
                    'y': [2, 2147483647]
                    'z': [2, 2147483647]
                position_accuracy:
                    'x': 10e-9
                    'y': 10e-9
                    'z': 50e-9
                spot_density: 1e11
        
        ni_io:
            module.Class: 'ni_x_series.ni_x_series_finite_sampling_io.NIXSeriesFiniteSamplingIO'
            options:
                device_name: 'Dev1'
                input_channel_units:  # optional
                    PFI8: 'c/s'
                    #PFI9: 'c/s'
                    #PFI10: 'c/s'
                    #ai0: 'V'
                    #ai1: 'V'
                output_channel_units:
                    'ao0': 'V'
                    'ao1': 'V'
                    'ao2': 'V'
                adc_voltage_ranges:
                    #ai0: [-10, 10]  # optional
                    #ai1: [-10, 10]  # optional
                output_voltage_ranges:
                    ao0: [-10, 10]
                    ao1: [-10, 10]
                    ao2: [-10, 10]

                frame_size_limits: [1, 1e9]  # optional #TODO actual HW constraint?
                output_mode: 'JUMP_LIST' #'JUMP_LIST' # optional, must be name of SamplingOutputMode
                read_write_timeout: 10  # optional
                #sample_clock_output: '/Dev1/PFI11' # optional

        ni_ao:
            module.Class: 'ni_x_series.ni_x_series_analog_output.NIXSeriesAnalogOutput'
            options:
                device_name: 'Dev1'
                channels:
                    ao0:
                        limits: [-10.0, 10.0]
                        keep_value: True
                    ao1:
                        limits: [-10.0, 10.0]
                        keep_value: True
                    ao2:
                        limits: [-10.0, 10.0]
                        keep_value: True
                    ao3:
                        limits: [-10.0, 10.0]
                        keep_value: True

        
        # optional, for slow counter / timer series reader
        ni_instreamer:
            module.Class: 'ni_x_series.ni_x_series_in_streamer.NIXSeriesInStreamer'
            options:
                device_name: 'Dev1'
                digital_sources:  # optional
                    - 'PFI8'
                #analog_sources:  # optional
                #   - 'ai0'
                #   - 'ai1'
                # external_sample_clock_source: 'PFI0'  # optional
                # external_sample_clock_frequency: 1000  # optional
                adc_voltage_range: [-10, 10]  # optional
                max_channel_samples_buffer: 10000000  # optional
                read_write_timeout: 10  # optional

## Configuration hints
- The maximum scanning frequency is given by the bandwidth of your Piezo controller (check the datasheet). It might make sense to put an even smaller limit into your config, since scanning at the hardware limit might introduce artifacts/offsets to your confocal scan.
- The optimizer scan behavior and sequence are configurable in the scanning gui -> Settings -> Optimizer settings.

Deprecated:
- Until v0.5.1, the scanning gui's `optimizer_plot_dimensions` ConfigOption allowed to specify the optimizer's scanning behavior. The default setting `[2,1]` enables one 2D and one 1D optimization step. You may set to eg. `[2,2,2]` to have three two-dimensionsal scans done for optimzation. In the gui (Settings/Optimizer Settings), this will change the list of possible optimizer sequences.  

## Tilt correction

The above configuration will enable the tilt correction feature for the ScanningProbeDummy and NiScanningProbeInterfuse.
This allows to perform scans in tilted layers, eg. along the surface of a non-flat sample. 
- In the scanning_probe_gui, you can configure this feature in the menu enabled by 'View' -> 'Tilt correction'.
- Choose three support vectors in the plane that should become the new $\hat{e}_z$ plane.
  Instead of manually typing the coordinates of a support vector, hitting the 'Vec 1" button will
  insert the current crosshair position as support vector 1. 
- Enable the transformation by the "Tilt correction" button.

## Todo this readme
