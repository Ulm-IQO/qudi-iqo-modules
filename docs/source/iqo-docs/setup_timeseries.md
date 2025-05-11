# Setting up time series
## Introduction
The timeseries toolchain allows to plot the timetrace of an incoming digital or analogue signal in real time.
For an confocal setup, the signal might be TTLs coming from a photon counter or the (analogue) output of a photodiode.
A typical working toolchain consists out of the following qudi modules:

logic:
- time_series_reader_logic

hardware:
- instreamer, eg. ni_instreamer

gui:
- time_series_gui


## Example config

These modules need to be configured and connected in your qudi config file.
We here provide an examplary config for a toolchain based on a NI X-series scanner with analogue output and digital (APD TTL) input.
Note: This readme file might not be up-to-date with the most recent development. We advice to check the examplary config present in the 
docstring of every module's python file. In the list above, a direct link for every module is provided:

    gui:
      time_series_gui:
          module.Class: 'time_series.time_series_gui.TimeSeriesGui'
          options:
            use_antialias: True  # optional, set to False if you encounter performance issues
          connect:
              _time_series_logic_con: time_series_reader_logic

    logic:
      time_series_reader_logic:
        module.Class: 'time_series_reader_logic.TimeSeriesReaderLogic'
        options:
            max_frame_rate: 20  # optional (10Hz by default)
            calc_digital_freq: True  # optional (True by default)
        connect:
            streamer: ni_instreamer


    hardware:
        ni_instreamer:
          module.Class: 'ni_x_series.ni_x_series_in_streamer.NIXSeriesInStreamer'
          options:
              device_name: 'Dev1'
              digital_sources:  # optional
                  - 'PFI8'
              #analog_sources:  # optional
                  #- 'ai0'
                  #- 'ai1'
              # external_sample_clock_source: 'PFI0'  # optional
              # external_sample_clock_frequency: 1000  # optional
              adc_voltage_range: [-10, 10]  # optional
              max_channel_samples_buffer: 10000000  # optional
              read_write_timeout: 10  # optional
    



## Configuration hints:
Make sure that the hardware in the conig file is named as it is called by the logic. (Copy paste out of the hardware file can name it differently).

## Todo this readme:

