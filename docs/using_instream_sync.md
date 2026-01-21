# About

The data instream sync interfuse can be used to synchronize 2 data streams from any streaming modules 
based on [`DataInStreamInterface`](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/interface/data_instream_interface.py)
via simple linear interpolation.

Since the interpolated data stream is stored in a ringbuffer, you can choose in the configuration
(`allow_overwrite: True|False`) if you want to raise an exception in case of overwriting unread
data or ignore this silently instead.

Configuration of the connected streaming devices is generally only possible for the primary 
streamer. The same applies for reading the configuration back.  
The only case where the secondary streamer will be configured and read back is when no active 
primary streamer channels are set.

The interpolation will always take the primary streamer as timebase and interpolate the secondary 
stream on this grid. Optionally, you can displace the relative timing between both streams by 
setting the configuration option `delay_time` to a non-zero value (in seconds).  
The configuration option `min_interpolation_samples` will ensure a minimum number of samples for 
both streams needs to be acquired to attempt a new interpolation frame (minimum 2).


# Example config

Here we demonstrate two dummy streaming modules being synced and fed into a time series toolchain.
For this we need the following qudi modules:

gui:
- [time_series.time_series_gui](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/gui/time_series/time_series_gui.py)

logic:
- [time_series_reader_logic](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/logic/time_series_reader_logic.py)

hardware:
- [dummy.data_instream_dummy](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/dummy/data_instream_dummy.py)
- [interfuse.instream_sync](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/interfuse/instream_sync.py)

Note: This readme file might not be up-to-date with the most recent development. We advise to check
the examplary config present in the docstring of every module's Python file. In the list above, a 
direct link for every module is provided:

```yaml
gui:
    time_series_gui:
        module.Class: 'time_series.time_series_gui.TimeSeriesGui'
        options:
            use_antialias: True  # optional, set to False if you encounter performance issues
        connect:
            _time_series_logic_con: 'time_series_logic'
            
logic:
    time_series_logic:
        module.Class: 'time_series_reader_logic.TimeSeriesReaderLogic'
        options:
            max_frame_rate: 30.0  # optional (10Hz by default)
            channel_buffer_size: 1048576  # optional (default: 1MSample)
            calc_digital_freq: True  # optional (True by default)
        connect:
            streamer: 'sync_interfuse'
            
hardware:
    instream_dummy1:
        module.Class: 'dummy.data_instream_dummy.InStreamDummy'
        options:
            channel_names:
                - 'APD'
                - 'analog 1'
            channel_units:
                - 'Hz'
                - 'V'
            channel_signals:  # Can be 'counts' or 'sine'
                - 'counts'
                - 'sine'
            data_type: 'float64'
            sample_timing: 'CONSTANT'  # Can be 'CONSTANT', 'TIMESTAMP' or 'RANDOM'
            
    instream_dummy2:
        module.Class: 'dummy.data_instream_dummy.InStreamDummy'
        options:
            channel_names:
                - 'analog 2'
            channel_units:
                - 'V'
            channel_signals:  # Can be 'counts' or 'sine'
                - 'sine'
            data_type: 'float64'
            sample_timing: 'CONSTANT'  # Can be 'CONSTANT', 'TIMESTAMP' or 'RANDOM'
    
    sync_interfuse:
        module.Class: 'interfuse.instream_sync.DataInStreamSync'
        options:
            min_interpolation_samples: 3
            allow_overwrite: False
            delay_time: 0
            max_poll_rate: 30.0
        connect:
            primary_streamer: 'instream_dummy1'
            secondary_streamer: 'instream_dummy2'
```
