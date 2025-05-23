# Installation Guide

This guide is a step-by-step instruction how to get started with qudi + iqo-modules installation.
For additional information, we recommend checking the [qudi-core documentation](https://github.com/Ulm-IQO/qudi-core/blob/main/docs/index.md).
If you're migrating an existing qudi v0.1 installation, there is a dedicated [porting guide](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/migrating_from_qudi_v0.1.md).

## Install qudi core

Follow the [qudi-core installation](https://ulm-iqo.github.io/qudi-core/setup/installation.html) instructions to setup your Python environment and the basic qudi installation. We recommend installing qudi-core from PyPI (non dev), as typical users shouldn't need to change core code too often. You can still change your measurements modules that are installed next.

## Install qudi-iqo-modules

The last step in the qudi-core installation instructions briefly explains setting up the measurement modules. More detailedly, this is how you install the qudi-iqo-modules in dev mode. In this way, you can easily change code in the measurement toolchains.

- Make sure you have a working git installation and that you can run the  `git` command from your console.
- Open your Anaconda prompt and `activate qudi-env` (or activate your venv in your other Python distro), the same environment you used to install the core.
- Navigate to the folder you want the modules to install to, eg.
`cd C:/Software`
- Clone the iqo-modules via `git clone https://github.com/Ulm-IQO/qudi-iqo-modules.git`. This will create a new folder `C:/Software/qudi-iqo-modules`. Do not copy/move this folder around after finishing the installation!
- Navigate into the folder `cd C:/Software/qudi-iqo-modules`
- Install and register the modules to your current qudi environment via `python -m pip install -e .`

Now you qudi-core installation will know about the measurement modules and it's time to set up a proper qudi configuration file.

## ⚠ Troubleshooting

- Installing according to this guide will leave you with the most recent version of qudi and all dependency packages. 
  If you encounter bugs, especially ones that relate to dependency packages, you can roll back to the latest stable release by:

        cd C:/Software/qudi-iqo-modules
        git checkout tags/v0.6.0
        python -m pip install -e .

- In rare cases and mostly with old versions of qudi-core, qudi-iqo-modules can be incompatible with qudi-core. If you encounter errors related to this, try to update manually to the latest qudi-core github release via `python -m pip install git+https://github.com/Ulm-IQO/qudi-core.git@main`.

## Configure Pycharm
It is possible to run Qudi just from the command line. To this end, just type `qudi` into your console.
Having the code as a project in the Pycharm IDE allows to easily navigate and run the qudi code.
- Open your Anaconda prompt and `activate qudi-env` (or activate your venv in your other Python distro)
- Create a new empty project in Pycharm. Don't open any source code yet.

To run Qudi via Pycharm you have to configure the right Python environment as a project interpreter.
- In Pycharm, navigate to 'File'->'Settings'->'Project:qudi'->'Project interpreter'
- If the correct environment ist not listed yet, you can add it via the "+" button. If you followed the qudi-core installation incstructions, the environment should be named `qudi-env` (or whatever name you give it during the core installation).
- You can find the path to the environment by (mind activating your qudi environment!) `python -c "import os, sys; print(os.path.dirname(sys.executable))"`
- Choose the correct environment, like shown on the screenshot.
  <img src="https://user-images.githubusercontent.com/5861249/176209579-3175f422-e940-4a58-98e1-821a85211de3.png" alt="drawing" width="700"/>

Now we open the code in Pycharm.
- Add the qudi-iqo-modules (and potentially qudi-core) folder by 'File'->'Open..'. After selecting the folder, a pop up window will ask you how to open the project. Press the 'Attach' option to have seperate locations open in the same project. If you did install qudi-core in non-developer mode, you can find your qudi-core folder by `python -c "import os, sys; print(os.path.dirname(sys.executable)+'\Lib\site-packages\qudi')"`


- Now navigate in Pycharm to 'Run'->'Edit configuration' and create a new 'Shell script' configuration just as shown below. The '-d' flag enables debug output and is optional.

  <img src="https://user-images.githubusercontent.com/5861249/190195589-dff2a80e-65f8-43bd-ae1c-cef937c099ce.png" alt="drawing" width="500"/>

You may run Qudi now from Pycharm via  'Run'->'Run qudi'.

### Switching branches
Switching to some other development branch is easy, if you installed your modules in dev mode. Just look in the lower right to access Pycharm's branch control and
'checkout' the desired branch from remote/origin (that is branches available online, not copies on your local computer).

<img src="https://user-images.githubusercontent.com/5861249/178280865-70936ade-f1d4-488f-9979-86ece4cba5cb.png" alt="drawing" width="500"/>

Now you will have a local copy of this branch in which you can create commits and push these online.

## Qudi configuration 

The configuration file specifies all the modules and hardware that are loaded to Qudi. Additionally, many modules come with
configuration parameters that are set in this file. On your first startup, the Qudi manager might be empty.
As a first step, it is instructive to load the default [_dummy_ configuration](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/src/qudi/default.cfg) that we provide with qudi-iqo-modules. It allows to have a look at the available toolchains and modules
without the need to attach real hardware. 
- Copy the default.cfg (from qudi-iqo-modules\src\qudi\default.cfg) into your user data folder, eg. to `C:\Users\quantumguy\qudi\config`. We strongly discourage to store any configuration (except the default.cfg) in the source folder of qudi.
- Start qudi, and then load (via File -> Load configuration) the default config that you just copied.
- Currently, we provide the following toolchains:
    - [Time series](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_timeseries.md) (/_slow counting_)
    - [Scanning](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_confocal_scanning.md) (/_confocal_)
    - Poi manager 
    - [CW ODMR](https://github.com/Ulm-IQO/qudi-iqo-modules/blob/main/docs/setup_odmr.md) 
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
Please find the [configuration instructions](https://ulm-iqo.github.io/qudi-core/design_concepts/remote_modules.html) in the qudi-core docs. 


## Jupyter notebooks/ measurement scripts

Qudi runs a IPython kernel that can be accessed from a jupyter notebook. In this way you can write your own measurements
scripts as described [here](https://ulm-iqo.github.io/qudi-core/setup/jupyter.html).

### Comparing notebooks
Pycharm lets you easily compare text based files (like .py) between different branches or versions by right-clicking 
on the file ->Git->Compare with. This fails for content enriched files like jupyter notebooks (.ipynb).
For similar functionality, we configure Pycharm to use the `nbdime` tool.

- Open your Anaconda prompt and `activate qudi-env` (or activate your venv in your other Python distro)
- Install via `conda install nbdime`
- Find the executable of nbdime by `where nbdiff-web`
- Navigate in Pycharm to File->Settings->Tools->Diff and Merge->External Diff Tools and paste this path into 'Path to executable'
- Add as Parameters:  `--ignore-details --ignore-metadata --ignore-outputs %1 %2`

Now you can open nbdime from Pycharms diff tool by hitting the hammer symbol.

### Transcribing scripts from qudi v0.1
Ipython in Qudi (either in Manager or jupyter notebook) is running 
now in its own process. The communication between QuDi and the
corresponding ipython process is done via rpyc. 

Not python built in objects need to be 
copied via netobtain(). We plan to have indepth documentation 
in the new core. 

## Ruff Autoformatting
To ensure a consistent code style across the project, we've included a configuration for [Ruff](https://github.com/astral-sh/ruff), a fast Python linter and formatter.

Before submitting a pull request, please format your code using Ruff. You can run it as a standalone tool or integrate it with your IDE for automatic formatting.

For installation and setup instructions, refer to the [official Ruff documentation](https://docs.astral.sh/ruff/installation/).
