# Installation Guide

This guide is a step-by-step instruction how to get started with qudi + iqo-modules installation.
For additional information, we recommend checking the [qudi-core documentation](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/index.md).
If you're migrating an existing qudi v0.1 installation, there is a dedicated porting guide (LINK MISSING).

## Install qudi core

Follow the [qudi-core installation](https://ulm-iqo.github.io/qudi-core/setup/installation.html) instructions to setup your Python environment and the basic qudi installation. We recommend installing qudi-core from PyPI (non dev), as typical users shouldn't need to change core code too often. You can still change your measurements modules that are installed next.

## Install qudi-iqo-modules

The last step in the qudi-core installation instructions briefly explains setting up the measurement modules. More detailedly, this is how you install the qudi-iqo-modules in dev mode. In this way, you can easily change code in the measurement toolchains.

- Make sure you have a working git installation and that you can run the  `git` command from your console.
- Open your Anaconda prompt and `activate qudi-env` (or activate your venv in your other Python distro)
- Navigate to the folder you want the modules to install to, eg.
`cd C:/Software`
- Clone the iqo-modules via `git clone https://github.com/Ulm-IQO/qudi-iqo-modules.git`. This will create a new folder `C:/Software/qudi-iqo-modules`. Do not copy/move this folder around after finishing the installation!
- Navigate into the folder `cd C:/Software/qudi-iqo-modules`
- Install and register the modules to your current qudi environment via `python -m pip install -e .`

Now you qudi-core installation will know about the measurement modules and it's time to set up a proper qudi configuration file.

## Configure Pycharm
It is possible to run qudi just from the command line. To this end, just type `qudi` into your console.
Having the code as a project in the Pycharm IDE allows to easily navigate and run the qudi code.
- Open your Anaconda prompt and `activate qudi-env` (or activate your venv in your other Python distro)
- Create a new project in Pycharm and add the qudi-core and qudi-iqo-modules folders to it by 'File'->'Open..'->('Attach' option). If you did install qudi-core in non-developer mode, you can find your qudi-core folder by `python -c "import os, sys; print(os.path.dirname(sys.executable)+'\Lib\site-packages\qudi')"`

To run Qudi via Pycharm you have to configure the right Python environment.
- In Pycharm, navigate to 'File'->'Settings'->'Project:qudi'->'Project interpreter'
- If the correct environment ist not listed yet, you can add it via the "+" button. If you followed the qudi-core installation incstructions, the environment should be named `qudi-env`.
- You can find the path to the environment by `python -c "import os, sys; print(os.path.dirname(sys.executable))"`
- Choose the correct environment, like shown on the screenshot.
![grafik](https://user-images.githubusercontent.com/5861249/176209579-3175f422-e940-4a58-98e1-821a85211de3.png)
- Now open the file `qudi-core\src\qudi\runnable.py` in Pycharm. If the environment is recognized correctly to Pycharm, you can run qudi via 'Run'->'Run runnable.py'.
- To enable debug output displayed in the qudi manager, navigate to 'Run'->'Edit configurations' and add the flag `-d' in the line 'Parameters'

- working directory?

## qudi Configuration file

- Start by playing with the dummy config (LINK MISSING) 
- - Load the dummy config file via file -> load configuration
- Continue by settting up real hardware. These links may help you:
- As an IQO member, you might want to checkout the following repo (LINK MISSING). In there, you can find and store configuration for multiple setups in the institute.

### Remote
### Pitfalls
### Scanning Tool Chain
### Laser Tool Chain
### Spectrometer
### CW ODMR Tool Chain

## Jupyter notebooks/ measurement scripts

### Transcribing scripts from qudi v0.1
Ipython in Qudi (either in Manager or jupyter notebook) is running 
now in its own process. The communication between QuDi and the
corresponding ipython process is done via rpyc. 

Not python built in objects need to be 
copied via netobtain(). We plan to have indepth documentation 
in the new core. 
