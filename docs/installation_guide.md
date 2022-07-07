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

The configuration file specifies all the modules and hardware that are loaded to qudi. Additionally, many modules come with
configuration parameters that are set in this file. On your first startup, the qudi manager might be empty.
- As a first step, it is instructive
to load (via File -> Load configuration) the default _dummy_ configuration (LINK MISSING) that we provide with qudi-iqo-modules. It allows to have a look at the available toolchains and modules
without the need to attach real hardware.
- Currently, we provide the following toolchains:
    - Time series (/_slow counting_)
    - Scanning (/_confocal_)
    - Poi manager 
    - CW ODMR 
    - Pulsed
    - Camera
    - Switches
    - Laser 
    - Spectrometer
    - Task runner (MISSING in dummy)
    - Qdplot
    - NV Calculator (MISSING in dummy)

- Continue by settting up real hardware. For the more complex toolchains above, we added links to help files that explain their configuration. 
  Otherwise, we advise you to start with the respective gui section in the dummy config file and iteratively go through all the connected modules (logic/hardware)
  to adapt them for working with real hardware.
  
As an IQO member, we strongly advise to store your configuration in [qudi-iqo-config repo](https://github.com/Ulm-IQO/qudi-iqo-config). In there, you can find configurations for multiple setups in the institute.
- To set this up, navigate in your console to the folder where you want to store your configuration. We recommend your user directory, because qudi by default stores logs and data there:
  `cd C:\Users\quantumguy\qudi`
- Clone the repo from git:
  `git clone https://github.com/Ulm-IQO/qudi-iqo-config`
- Open the created folder in Pycharm via File -> Open -> Attach
- Copy your configuration file into this folder.
- Commit your file by right clicking on it in Pycharm -> Git -> Commit
- Push your change online by Git -> Push

Whenever you make changes to your configuration, you should create such an commit and make it available online. All configurations are accessible for iqo members only.

### Remote

Qudi allows to access modules (including hardware) that run on a different computer that is connected to the same LAN network.
Please find the instruction to configure the [server](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md#remote_modules_server) and each of the [remote modules](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md#Remote%20Module).

### Pitfalls


## Jupyter notebooks/ measurement scripts

### Transcribing scripts from qudi v0.1
Ipython in Qudi (either in Manager or jupyter notebook) is running 
now in its own process. The communication between QuDi and the
corresponding ipython process is done via rpyc. 

Not python built in objects need to be 
copied via netobtain(). We plan to have indepth documentation 
in the new core. 
