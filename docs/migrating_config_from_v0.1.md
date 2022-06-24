# Config conversion from old qudi (release v0.1) to new core 
This document gives a short overview how to port an existing configuration to the new qudi release vXXX (aka new core). The good new is: If you have a running configuration for v0.1 already,
it should be straightforward to adapt it to your new qudi installation.
Please note that we are describing the most common use case, but can't cover all modules here. If you encounter issues with any configuration, it might help to look into the source (.py) file. There you'll find an example config in the docstring of the respective class.

## General qudi config

The core qudi facilities are configured in the 'global' section of the config file. You can find a detailed description in the qudi-core configuration guide (LINK BROKEN).
It might be instructive to have a look at the respective section in the default config (NO LINK YET) which sets up a dummy configuration without real hardware.

### Remote modules

The setup of remote connections has changed quite a bit. Please find the instruction here (NO LINK YET).

## Module config

The configuration for the following modules has changed substantially:
- timeseries (LINK MISSING)
Formerly known as slow counter. The new implantation is more flexible and allows more data sources (than the TTL counting supported by our old slow counter).

- scanning (LINK MISSING)
Formely known as confocal. The refined version was rewritten from scratch and supports arbitrary input sources and axis configurations.

- cw odmr (NO LINK YET)

For the following modules no/ only little changes should be required:
- laser
- pulsed


# Known missing features 
Compared to the old release v0.1, the following features are currently not available yet:
- Magnet control GUI
- Confocal/Scanning: tilt correction, loop scans

# Miscellaneous

