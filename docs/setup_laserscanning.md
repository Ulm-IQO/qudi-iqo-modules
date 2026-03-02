# About

The laserscanning toolchain can visualize and process data from specialized streaming modules based
on [`DataInStreamInterface`](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/interface/data_instream_interface.py)
that contain one stream channel with laser frequency (Hz) or wavelength (m) data.

Optionally, it can also connect to a 
[`ScannableLaserInterface`](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/interface/scannable_laser_interface.py)
module to actively control laser scanning (settings/start/stop) during data acquisition.
In the absence of a scannable laser hardware module, the data stream is just recorded and the 
actual laser scanning is assumed to be controlled from elsewhere.

While the laser scanning toolchain just requires a streaming module to work, this requirement has 
some pitfalls in common experiment setups:
1. Streaming modules (and in extension the laser scanning toolchain) assume all data channels to be 
in-sync with each other. However laser frequency/wavelength feedback is often provided by an 
independent device, e.g. a wavemeter, and additional data by another device, e.g. NI-DAQ.  
This requires synchronization of two data streams into a virtual combined stream 
[as described here](./using_instream_sync.md).
2. It is often required to feed the same data stream into multiple toolchains, e.g. the laser 
scanning toolchain _and_ the time series toolchain. To this end you can make use of a stream buffer
interfuse to "multiply" the stream data for multiple consumers [as described here](./using_instream_buffer.md).

A typical working toolchain consists out of the following qudi modules:

gui:
- [laserscanning.laser_scanning_gui](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/gui/laserscanning/laser_scanning_gui.py)

logic:
- [laser_scanning_logic](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/logic/laser_scanning_logic.py)

hardware (here: NI X-series streamer and high finesse wavemeter):
- [wavemeter.high_finesse_wavemeter](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/wavemeter/high_finesse_wavemeter.py)
- [ni_x_series.ni_x_series_in_streamer](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/ni_x_series/ni_x_series_in_streamer.py)
- ([qudi.interface.scannable_laser_interface.ScannableLaserInterface](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/interface/scannable_laser_interface.py), optional)
- [interfuse.instream_sync](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/interfuse/instream_sync.py)
- [interfuse.instream_buffer](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/interfuse/instream_buffer.py)


# Example config

These modules need to be configured and connected in your qudi config file.
We here provide an exemplary config for a toolchain based on a NI X-series streamer and high 
finesse wavemeter.

Note: This readme file might not be up-to-date with the most recent development. We advise to check
the examplary config present in the docstring of every module's Python file. In the list above, a 
direct link for every module is provided:

```yaml
gui:
    laser_scanning_gui:
        module.Class: 'laserscanning.laser_scanning_gui.LaserScanningGui'
        options:
            max_display_points: 1000  # optional, Maximum number of simultaneously displayed data points
        connect:
            laser_scanning_logic: 'laser_scanning_logic'

logic:
    laser_scanning_logic:
        module.Class: 'laser_scanning_logic.LaserScanningLogic'
        connect:
            streamer: 'wavemeter_ni_sync_interfuse'
            # laser: '<scannable_laser>'  # optional, ScannableLaserInterface
        options:
            laser_channel: 'red_laser'
            max_update_rate: 30.0
            max_samples: -1
            
hardware:
    wavemeter_ni_sync_interfuse:
        module.Class: 'interfuse.instream_sync.DataInStreamSync'
        options:
            allow_overwrite: False  # optional, allow ringbuffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate (1/s) for connected hardware
            min_interpolation_samples: 2  # optional, minimum samples per frame to interpolate (must be >= 2)
            delay_time: 0  # optional, time offset for secondary stream interpolation
        connect:
            primary_streamer: ni_buffer1
            secondary_streamer: wavemeter_buffer1
            
    ni_buffer1:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False  # optional, allow ringbuffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate (1/s) for connected hardware
        connect:
            streamer: 'ni_streamer'
            
    ni_buffer2:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False  # optional, allow ringbuffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate (1/s) for connected hardware
        connect:
            streamer: 'ni_streamer'
            
    wavemeter_buffer1:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False  # optional, allow ringbuffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate (1/s) for connected hardware
        connect:
            streamer: 'wavemeter'
            
    wavemeter_buffer2:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False  # optional, allow ringbuffer overflows
            max_poll_rate: 100.0  # optional, maximum data poll rate (1/s) for connected hardware
        connect:
            streamer: 'wavemeter'
            
    ni_streamer:
        module.Class: 'ni_x_series.ni_x_series_in_streamer.NIXSeriesInStreamer'
        options:
            device_name: 'Dev1'
            digital_sources:  # optional
                - 'PFI15'
            analog_sources:  # optional
                - 'ai0'
            # external_sample_clock_source: 'PFI0'  # optional
            # external_sample_clock_frequency: 1000  # optional
            adc_voltage_range: [-10, 10]  # optional
            max_channel_samples_buffer: 10000000  # optional
            read_write_timeout: 10  # optional
            
    wavemeter:
        module.Class: 'wavemeter.high_finesse_wavemeter.HighFinesseWavemeter'
        connect:
            proxy: 'wavemeter_proxy'
        options:
            channels:
                red_laser:
                    switch_ch: 1    # channel on the wavemeter switch
                    unit: 'm'    # wavelength (m) or frequency (Hz)
                    exposure: 10  # exposure time in ms, optional
                green_laser:
                    switch_ch: 2
                    unit: 'Hz'
                    exposure: 10
                    
    wavemeter_proxy:
        module.Class: 'wavemeter.high_finesse_proxy.HighFinesseProxy'
        options:
            watchdog_interval: 1.0  # how often the watchdog checks for errors/changes in s
```
