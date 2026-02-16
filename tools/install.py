import os
import subprocess
import sys
import shutil
from pathlib import Path
from tools import fetch_python_versions

INSTALL_DIR = Path.home() / "qudi"
VENV_DIR = INSTALL_DIR / "qudi-env"

CORE_REPO = "https://github.com/Ulm-IQO/qudi-core.git"
IQO_REPO = "https://github.com/Ulm-IQO/qudi-iqo-modules.git"


def run(cmd, cwd=None):
    print(f"> {cmd}")
    subprocess.check_call(cmd, shell=True, cwd=cwd)


def check_python():
    versions = fetch_python_versions.main()
    if f"{sys.version_info.major}.{sys.version_info.minor}" not in versions:
        print(f"Could not detect correct Python version. Install one of the following versions: {versions}.")
        sys.exit(1)


def check_git():
    if shutil.which("git") is None:
        print("Git not found. Please install Git.")
        sys.exit(1)


def create_venv():
    run(f"{sys.executable} -m venv {VENV_DIR}")


def install_modules():
    pip = VENV_DIR / ("Scripts/pip" if os.name == "nt" else "bin/pip")
    run(f"{pip} install --upgrade pip")

    run(f"{pip} install -e {INSTALL_DIR/'qudi-core'}")
    run(f"{pip} install -e {INSTALL_DIR/'qudi-iqo-modules'}")
    run(f"qudi-install-kernel")


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

    INSTALL_DIR.mkdir(exist_ok=True)
    os.chdir(INSTALL_DIR)

    if not (INSTALL_DIR / "qudi-core").exists():
        run(f"git clone {CORE_REPO}")

    if not (INSTALL_DIR / "qudi-iqo-modules").exists():
        run(f"git clone {IQO_REPO}")

    create_venv()
    install_modules()
    create_launcher()

    print("\nInstallation complete!")
    print(f"Location: {INSTALL_DIR}")


if __name__ == "__main__":
    main()
