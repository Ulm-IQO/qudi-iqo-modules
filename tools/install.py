import os
import subprocess
import sys
import venv
import shutil
from pathlib import Path
#import win32com.client

INSTALL_DIR = Path.home() / "qudi"

VENV_DIR = INSTALL_DIR / "qudi-env"

CORE_REPO = "https://github.com/Ulm-IQO/qudi-core.git"
IQO_REPO = "https://github.com/Ulm-IQO/qudi-iqo-modules.git"


def choose_install_dir():
    global INSTALL_DIR
    print("Where do you want to install Qudi?")
    print("1) Home directory")
    print("2) Current directory")
    print("3) Custom directory")

    choice = input("Enter 1, 2, or 3 (default 1): ").strip()

    if choice == "2":
        INSTALL_DIR = Path.cwd()
    elif choice == "3":
        custom_path = input("Enter full path for installation: ").strip()
        if not custom_path:
            return
        INSTALL_DIR = Path(custom_path).expanduser().resolve()


def choose_venv_name():
    global VENV_DIR
    print("Use a custom virtual environment name?")
    print("1) qudi-env")
    print("2) Custom name")

    choice = input("Enter 1, 2 (default 1): ").strip()

    custom_name = "qudi-env"
    if choice == "2":
        choice = input("Enter name: ").strip()
        if choice:
            custom_name = choice
    VENV_DIR = INSTALL_DIR / custom_name


def run(cmd, cwd=None):
    print(f"> {cmd}")
    subprocess.check_call(cmd, shell=True, cwd=cwd)


def check_python():
    pass
    # if f"{sys.version_info.major}.{sys.version_info.minor}" not in versions:
    #    print(f"Could not detect correct Python version. Install one of the following versions: {versions}.")
    #    sys.exit(1)


def check_git():
    if shutil.which("git") is None:
        print("Git not found. Please install Git.")
        sys.exit(1)


def create_venv():
    choose_venv_name()
    if not os.path.exists(VENV_DIR):
        print(f"Creating virtual environment in '{VENV_DIR}'...")
        venv.create(VENV_DIR, with_pip=True)
    else:
        print(f"Virtual environment '{VENV_DIR}' already exists.")

        choice = input(
            "Continuing will modify the existing virtual environment, do you want to continue? [y / N] "
        ).strip()
        if choice in ['y', 'Y']:
            return
        else:
            print("Aborting Qudi installation")
            sys.exit(1)


def install_modules():
    if os.name == "nt":
        python_bin = os.path.join(VENV_DIR, "Scripts", "python.exe")
    else:
        python_bin = os.path.join(VENV_DIR, "bin", "python")
    run(f"{python_bin} -m pip install --upgrade pip")

    run(f"{python_bin} -m pip install -e {INSTALL_DIR / 'qudi-core'}")
    run(f"{python_bin} -m pip install -e {INSTALL_DIR / 'qudi-iqo-modules'}")
    run(f'{python_bin} -c "from qudi.core.qudikernel import install_kernel; install_kernel()"')


def create_launcher():
    if os.name == "nt":
        launcher = INSTALL_DIR / "start_qudi.bat"
        launcher.write_text(f"""@echo off
call "{VENV_DIR}\\Scripts\\qudi"
pause
""")
    else:
        launcher = INSTALL_DIR / "start_qudi.sh"
        launcher.write_text(f"""#!/bin/bash
cd {INSTALL_DIR}
exec "{VENV_DIR}/bin/qudi"
""")
        launcher.chmod(0o755)
    print(f"Created launcher script at {launcher}")


def create_config_dir():
    config_dir = INSTALL_DIR / "config/"
    config_dir.mkdir(exist_ok=True)
    shutil.copy2(INSTALL_DIR / "qudi-iqo-modules/src/qudi/default.cfg", config_dir / "default.cfg")
    print(f"Created config directory in {config_dir} and copied default config into it")


def create_desktop_file():
    iconfile = INSTALL_DIR / "/qudi-core/src/qudi/artwork/logo/logo-qudi"
    if os.name == "nt":
        desktopfile = INSTALL_DIR / "qudi.url"
        desktopfile.write_text(f"""[InternetShortcut]
URL=file:///{(INSTALL_DIR / "start_qudi.bat").as_posix()}
IconFile={iconfile}.ico
IconIndex=0
""")
        #start_menu = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
        #desktopfile = start_menu / "qudi.lnk"
        #shell = win32com.client.Dispatch("WScript.Shell")
        #shortcut = shell.CreateShortcut(str(desktopfile))
        #shortcut.TargetPath = str(INSTALL_DIR / "start_qudi.bat")
        #shortcut.WorkingDirectory = str(INSTALL_DIR)
        #shortcut.IconLocation = str(iconfile)
        #shortcut.Save()

    else:
        desktopfile = INSTALL_DIR / "qudi.desktop"
        desktopfile.write_text(f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Qudi
Comment=Start Qudi Measurement Software
Exec={INSTALL_DIR}/start_qudi.sh
Icon={iconfile}.svg
Terminal=true
Categories=Science;Education;
StartupNotify=true
""")
        desktopfile.chmod(0o755)
        applications_dir = Path.home() / ".local/share/applications"
        applications_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(desktopfile, applications_dir / "qudi.desktop")
        print(f"Registered desktop file at {applications_dir / 'qudi.desktop'}")

    print(f"Created desktop file at {desktopfile}")


def main():
    check_python()
    check_git()

    choose_install_dir()

    INSTALL_DIR.mkdir(exist_ok=True)
    os.chdir(INSTALL_DIR)

    if not (INSTALL_DIR / "qudi-core").exists():
        run(f"git clone {CORE_REPO}")
    else:
        print(f"{INSTALL_DIR / 'qudi-core'} already exists, doing nothing.")

    if not (INSTALL_DIR / "qudi-iqo-modules").exists():
        run(f"git clone {IQO_REPO}")
    else:
        print(f"{INSTALL_DIR / 'qudi-iqo-modules'} already exists, doing nothing.")

    create_venv()
    install_modules()
    create_launcher()
    create_desktop_file()

    create_config_dir()

    print("\nInstallation complete!")
    print(f"Location: {INSTALL_DIR}")


if __name__ == "__main__":
    main()
