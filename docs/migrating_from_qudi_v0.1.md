# Migrating from old qudi (release v0.1) to new core 
This document gives a short overview how to migrate from an existing qudi installation (<= v0.1) to the new qudi release vXXX (aka new core).
You can easily keep your old installation in parallel. So moving to the new core is mainly about porting your old
configuration file.
The good news is: If you have a running configuration for v0.1 already,
it should be straightforward to adapt it to your new qudi installation.
Please note that we are describing the most common use case, but can't cover all modules here. If you encounter issues with any configuration, it might help to look into the source (.py) file. There you'll find an example config in the docstring of the respective class.

## General qudi config

The core qudi facilities are configured in the 'global' section of the config file. You can find a detailed description in the qudi-core [configuration guide](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md).
It might be instructive to have a look at the respective section in the [default config]((https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/default.cfg)) which sets up a dummy configuration without real hardware.

### Remote modules

The setup of remote connections has changed quite a bit. Please find the instruction to configure the [server](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md#remote_modules_server) and each of the [remote modules](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md#Remote%20Module).

### Qudi kernel

When you're switching between an old (v0.1) installation and the new core, you need to register the qudi kernel.
- Open your Anaconda prompt and activate the respective qudi environment:

Before starting the new core:

    activate qudi-env
    qudi-install-kernel

Before starting your old installation:

    activate qudi 
    cd C:\Software\qudi\core
    python qudikernel.py install

## Module config

The configuration for the following modules has changed substantially:
- [timeseries](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_timeseries.md)

    Formerly known as slow counter. The new implantation is more flexible and allows more data sources (than the TTL counting supported by our old slow counter).

- [scanning](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_confocal_scanning.md)

    Formely known as confocal. The refined version was rewritten from scratch and supports arbitrary input sources and axis configurations.

- [cw odmr](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_odmr.md)
  
  Toolchain has changed but this doesn't affect the configuration a lot. Changes in the cw odmr configuration result from huge restructuring the NI card duties. For cw odmr `ni_x_finite_sampling_input` is required. For the correct configuration please find the example config in the docstring of the `NIXSeriesFiniteSamplingInput` class in qudi\hardware\ni_x_series\ni_x_series_finite_sampling_input.py. All ports need to adapted to your custom setup, of course. You should find the correct ports in your old configuration of the NI card. 

For the following modules no/ only little changes should be required:
- laser
- pulsed


# Known missing features 
Compared to the old release v0.1, the following features are currently not available yet:
- Magnet control GUI
- Confocal/Scanning: tilt correction, loop scans, moving arbitrary axis during scan, puase and resume scan  
- wavemeter toolchain
- Hardware PID. Software PID soon to come (PR in testing)

In case you need these features, please reach out to us to discuss how to move forward.
We might have already started to port a feature or can assist you in contributing.

# Untested features
Some of the toolchains and modules have been ported, but not thoroughly tested yet.
Please let us know if you successfully use or find errors in the following modules:

- motors hardware files
- power supply hardware files
- temperature
- camera toolchain


# Miscellaneous

