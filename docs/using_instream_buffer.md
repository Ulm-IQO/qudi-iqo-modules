# About

The data instream buffer interfuse can be used to buffer a data stream from any streaming modules 
based on [`DataInStreamInterface`](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/interface/data_instream_interface.py).
Connecting multiple of these instream buffers to the same streaming hardware module will allow for 
multiple independent consumers to read the same data stream.

Since the buffer is implemented as a ringbuffer, you can choose in the configuratio
(`allow_overwrite: True|False`) if you want to raise an exception in case of overwriting unread
data or ignore this silently instead.

The actual hardware module is only stopped if the calling buffer interfuse is the last active buffer
interfuse. Configuration changes of the hardware are also only possible once all buffer interfuses
have been stopped.

Be aware that each instance of the data instream buffer interfuse will copy the stream data and 
thus increase memory consumption. Depending on the configured `max_poll_rate`, this can 
also increase CPU load noticeably. If you encounter problems with these resources, use only as many
instances as are absolutely necessary for your application and reduce the maximum poll rate as much
as possible.

Be also advised that all instream buffer instances that connect to the same streaming hardware 
need to be run in the same qudi process. Connecting some buffers via remote connection and some 
locally does not work if they connect to the same hardware module.


# Example config

Here we demonstrate a dummy streaming module being buffered and distributed to two separate time 
series toolchains. For this we need the following qudi modules:

gui:
- [time_series.time_series_gui](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/gui/time_series/time_series_gui.py)

logic:
- [time_series_reader_logic](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/logic/time_series_reader_logic.py)

hardware:
- [dummy.data_instream_dummy](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/dummy/data_instream_dummy.py)
- [interfuse.instream_buffer](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/hardware/interfuse/instream_buffer.py)

Note: This readme file might not be up-to-date with the most recent development. We advise to check
the examplary config present in the docstring of every module's Python file. In the list above, a 
direct link for every module is provided:

```yaml
gui:
    time_series_gui1:
        module.Class: 'time_series.time_series_gui.TimeSeriesGui'
        options:
            use_antialias: True  # optional, set to False if you encounter performance issues
        connect:
            _time_series_logic_con: 'time_series_logic1'
            
    time_series_gui2:
        module.Class: 'time_series.time_series_gui.TimeSeriesGui'
        options:
            use_antialias: True  # optional, set to False if you encounter performance issues
        connect:
            _time_series_logic_con: 'time_series_logic2'
            
logic:
    time_series_logic1:
        module.Class: 'time_series_reader_logic.TimeSeriesReaderLogic'
        options:
            max_frame_rate: 30  # optional (10Hz by default)
            channel_buffer_size: 1048576  # optional (default: 1MSample)
            calc_digital_freq: True  # optional (True by default)
        connect:
            streamer: 'stream_buffer1'
            
    time_series_logic2:
        module.Class: 'time_series_reader_logic.TimeSeriesReaderLogic'
        options:
            max_frame_rate: 30  # optional (10Hz by default)
            channel_buffer_size: 1048576  # optional (default: 1MSample)
            calc_digital_freq: True  # optional (True by default)
        connect:
            streamer: 'stream_buffer2'
            
hardware:
    instream_dummy:
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
    
    stream_buffer1:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False
            max_poll_rate: 30.0
        connect:
            streamer: 'instream_dummy'
            
    stream_buffer2:
        module.Class: 'interfuse.instream_buffer.DataInStreamBuffer'
        options:
            allow_overwrite: False
            max_poll_rate: 30.0
        connect:
            streamer: 'instream_dummy'
```
