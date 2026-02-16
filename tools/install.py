import os
import subprocess
import sys
import venv
import shutil
from pathlib import Path

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
    #if f"{sys.version_info.major}.{sys.version_info.minor}" not in versions:
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

        choice = input("Continuing will modify the existing virtual environment, do you want to continue? [y / N] ").strip()
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

    run(f"{python_bin} -m pip install -e {INSTALL_DIR/'qudi-core'}")
    run(f"{python_bin} -m pip install -e {INSTALL_DIR/'qudi-iqo-modules'}")
    run(f'{python_bin} -c "from qudi.core.qudikernel import install_kernel; install_kernel()"')


def create_launcher():
    if os.name == "nt":
        launcher = INSTALL_DIR / "Start_Qudi.bat"
        launcher.write_text(f"""
@echo off
call "{VENV_DIR}\\Scripts\\activate.bat"
qudi
pause
""")
    else:
        launcher = INSTALL_DIR / "start_qudi.sh"
        launcher.write_text(f"""
#!/bin/bash
source "{VENV_DIR}/bin/activate"
qudi
""")
        launcher.chmod(0o755)


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

    print("\nInstallation complete!")
    print(f"Location: {INSTALL_DIR}")


if __name__ == "__main__":
    main()
