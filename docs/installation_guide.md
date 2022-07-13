# Installation Guide

This guide is a step-by-step instruction how to get started with qudi + iqo-modules installation.
For additional information, we recommend checking the [qudi-core documentation](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/index.md).
If you're migrating an existing qudi v0.1 installation, there is a dedicated [porting guide](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/migrating_from_qudi_v0.1.md).

## Install qudi core

Follow the [qudi-core installation](https://ulm-iqo.github.io/qudi-core/setup/installation.html) instructions to setup your Python environment and the basic qudi installation. We recommend installing qudi-core from PyPI (non dev), as typical users shouldn't need to change core code too often. You can still change your measurements modules that are installed next.

> **⚠ WARNING:**
> 
> Currently (2022/07/12), qudi-core as installed from PyPi is incompatible with iqo-modules. If you installed in non-dev mode, you need to manually update to 
> the latest github release via `python -m pip install git+https://github.com/Ulm-IQO/qudi-core.git@main` after the qudi-core installation.

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
- Create a new empty project in Pycharm. Don't open any source code yet.

To run Qudi via Pycharm you have to configure the right Python environment as a project interpreter.
- In Pycharm, navigate to 'File'->'Settings'->'Project:qudi'->'Project interpreter'
- If the correct environment ist not listed yet, you can add it via the "+" button. If you followed the qudi-core installation incstructions, the environment should be named `qudi-env`.
- You can find the path to the environment by `python -c "import os, sys; print(os.path.dirname(sys.executable))"`
- Choose the correct environment, like shown on the screenshot.
<img src="https://user-images.githubusercontent.com/5861249/176209579-3175f422-e940-4a58-98e1-821a85211de3.png" alt="drawing" width="700"/>

Now we open the code in Pycharm.
- Add both the qudi-core and qudi-iqo-modules folders by 'File'->'Open..'. After selecting their respective folders you choose the 'Attach' option in the dialogue to have seperate locations open in the same project. If you did install qudi-core in non-developer mode, you can find your qudi-core folder by `python -c "import os, sys; print(os.path.dirname(sys.executable)+'\Lib\site-packages\qudi')"`


- Now open the file `qudi-core\src\qudi\runnable.py` in Pycharm. If the environment is recognized correctly to Pycharm, you can run qudi via 'Run'->'Run runnable.py'.
<img src="https://user-images.githubusercontent.com/5861249/178508718-0c141a2a-03ce-49ba-bddb-80a593ea4b25.png" alt="drawing" width="500"/>


- To enable debug output displayed in the qudi manager, navigate to 'Run'->'Edit configurations' and add the flag `-d' in the line 'Parameters'


### Switching branches
Switching to some other development branch is easy, if you installed your modules in dev mode. Just look in the lower right to access Pycharm's branch control and
'checkout' the desired branch from remote/origin (that is branches available online, not copies on your local computer).

<img src="https://user-images.githubusercontent.com/5861249/178280865-70936ade-f1d4-488f-9979-86ece4cba5cb.png" alt="drawing" width="500"/>

Now you will have a local copy of this branch in which you can create commits and push these online.

## Qudi configuration 

The configuration file specifies all the modules and hardware that are loaded to qudi. Additionally, many modules come with
configuration parameters that are set in this file. On your first startup, the qudi manager might be empty.
As a first step, it is instructive to load the default [_dummy_ configuration](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/default.cfg) that we provide with qudi-iqo-modules. It allows to have a look at the available toolchains and modules
without the need to attach real hardware. 
- Copy the default.cfg (from qudi-iqo-modules\src\qudi\default.cfg) into your user data folder, eg. to `C:\Users\quantumguy\qudi\config`. We strongly discourage to store any configuration (except the default.cfg) in the source folder of qudi.
- Start qudi, and then load (via File -> Load configuration) the default config that you just copied.
- Currently, we provide the following toolchains:
    - [Time series](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_timeseries.md) (/_slow counting_)
    - [Scanning](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_confocal_scanning.md) (/_confocal_)
    - Poi manager 
    - CW ODMR 
    - Pulsed
    - Camera
    - Switches
    - Laser 
    - Spectrometer
    - Task runner 
    - Qdplot
    - NV Calculator 

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

Whenever you make changes to your configuration, you should create such an commit and make it available online. All configurations in this repo are visible to iqo members only.

### Remote

Qudi allows to access modules (including hardware) that run on a different computer that is connected to the same LAN network.
Please find the instruction to configure the [server](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md#remote_modules_server) and each of the [remote modules](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/design_concepts/configuration.md#Remote%20Module).


## Jupyter notebooks/ measurement scripts

### Transcribing scripts from qudi v0.1
Ipython in Qudi (either in Manager or jupyter notebook) is running 
now in its own process. The communication between QuDi and the
corresponding ipython process is done via rpyc. 

Not python built in objects need to be 
copied via netobtain(). We plan to have indepth documentation 
in the new core. 
