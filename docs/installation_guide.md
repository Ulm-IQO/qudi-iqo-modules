# Installation Guide
## Install qudi core
## Install qudi-iqo-modules
## Config
(add link to example config)
### Remote
### Pitfalls
### Scanning Tool Chain
### Laser Tool Chain
### Spectrometer
### CW ODMR Tool Chain
## Transcribing measurement scripts
Ipython in Qudi (either in Manager or jupyter notebook) is running 
now in its own process. The communication between QuDi and the
corresponding ipython process is done via rpyc. 

Not python built in objects need to be 
copied via netobtain(). We plan to have indepth documentation 
in the new core. 
