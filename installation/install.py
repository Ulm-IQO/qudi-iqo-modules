import os
import subprocess
import sys
from typing import Optional
import venv
import shutil
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
import json
import re

INSTALL_DIR = Path.home() / "qudi"

VENV_DIR = INSTALL_DIR / "qudi-env"

CORE_REPO = "https://github.com/Ulm-IQO/qudi-core.git"
IQO_REPO = "https://github.com/Ulm-IQO/qudi-iqo-modules.git"

CORE_DIR: Optional[Path] = None
IQO_MODULES_DIR: Optional[Path] = None

ICON_URL = "https://raw.githubusercontent.com/Ulm-IQO/qudi-core/refs/heads/main/src/qudi/artwork/logo/"
MANIFEST_URL = "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"
PYPROJECT_URL = "https://raw.githubusercontent.com/Ulm-IQO/qudi-iqo-modules/refs/heads/main/pyproject.toml"


def read_requires_python() -> str:
    with urlopen(PYPROJECT_URL) as response:
        content = response.read().decode("utf-8")

    match = re.search(
        r'requires-python\s*=\s*"([^"]+)"',
        content,
        re.DOTALL,
    )

    if match:
        return str(match.group(1))

    raise SystemExit("requires-python not found in pyproject.toml")


def fetch_manifest(url: str = MANIFEST_URL) -> list[str]:
    try:
        with urlopen(url, timeout=30) as r:
            manifest = json.load(r)
            versions = set()
            for entry in manifest:
                ver = entry.get("version").split(".")
                versions.add(f"{ver[0]}.{ver[1]}")
        return sorted(versions, key=lambda s: tuple(map(int, s.split("."))))

    except URLError as e:
        raise ValueError(f"Failed to fetch versions-manifest.json: {e}")


def parse_spec(spec: str):
    """
    Convert a comma-separated spec string into a list of check functions.
    Supports: >=, <, !=, ^, exact versions
    """
    checks = []
    for part in spec.split(","):
        part = part.strip()
        if part.startswith(">="):
            ver = tuple(map(int, part[2:].split(".")))
            checks.append(lambda v, ver=ver: v >= ver)
        elif part.startswith("<"):
            ver = tuple(map(int, part[1:].split(".")))
            checks.append(lambda v, ver=ver: v < ver)
        elif part.startswith("!="):
            ver = tuple(map(int, part[2:].split(".")))
            checks.append(lambda v, ver=ver: v != ver)
        elif part.startswith("^"):
            major, minor = map(int, part[1:].split("."))
            min_ver = (major, minor)
            max_ver = (major + 1, 0)
            checks.append(lambda v, min_ver=min_ver, max_ver=max_ver: min_ver <= v < max_ver)
        else:
            # exact version like "3.10"
            ver = tuple(map(int, part.split(".")))
            checks.append(lambda v, ver=ver: v == ver)
    return checks


def is_allowed(version: str, checks: list):
    major, minor = map(int, version.split("."))
    v = (major, minor)
    return all(check(v) for check in checks)


def filter_versions(spec: str, available_versions: list[str]) -> list[str]:
    checks = parse_spec(spec)
    return [v for v in available_versions if is_allowed(v, checks)]


def yes_no_choice(message: str) -> bool:
    choice = input(f"{message} [y / N] ").strip()

    if choice in ['y', 'Y']:
        return True
    else:
        return False


def clone_repo(repo: str, location: Path):
    if not location.exists():
        run(f'git clone {repo} "{location}"')
    else:
        print(f"{location} already exists, doing nothing.")


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
        custom_path = input("Enter full path for installation: ").strip().strip('"')
        if not custom_path:
            return
        print(custom_path)
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
    supported_versions = filter_versions(read_requires_python(), fetch_manifest())
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if version not in supported_versions:
        print(f"Detected Python version ({version}) is not in supported versions {supported_versions}. Please use a supported Python version.")
        sys.exit(1)
    print(f"Found Python version {version}")


def check_git():
    if shutil.which("git") is None:
        print("Git not found. Please install Git.")
        sys.exit(1)
    print("Found Git installation")


def create_venv():
    choose_venv_name()
    if not os.path.exists(VENV_DIR):
        print(f"Creating virtual environment in '{VENV_DIR}'...")
        venv.create(VENV_DIR, with_pip=True)
    else:
        print(f"Virtual environment '{VENV_DIR}' already exists.")

        if not yes_no_choice("Continuing will modify the existing virtual environment, do you want to continue?"):
            print("Aborting Qudi installation")
            sys.exit(1)


def install_modules():
    if os.name == "nt":
        python_bin = VENV_DIR / "Scripts" / "python.exe"
    else:
        python_bin = VENV_DIR / "bin" / "python"
    run(f'"{python_bin}" -m pip install --upgrade pip')

    if CORE_DIR:
        clone_repo(CORE_REPO, CORE_DIR)
        run(f'"{python_bin}" -m pip install -e "{CORE_DIR}"')
    else:
        run(f'"{python_bin}" -m pip install qudi-core')

    if IQO_MODULES_DIR:
        clone_repo(IQO_REPO, IQO_MODULES_DIR)
        run(f'"{python_bin}" -m pip install -e "{IQO_MODULES_DIR}"')
    else:
        run(f'"{python_bin}" -m pip install qudi-iqo-modules')

    run(f'"{python_bin}" -c "from qudi.core.qudikernel import install_kernel; install_kernel()"')


def create_launcher():
    if os.name == "nt":
        launcher = INSTALL_DIR / "start_qudi.bat"
        launcher.write_text(f"""@echo off
cd "{INSTALL_DIR}"
call "{VENV_DIR}\\Scripts\\activate.bat"
qudi
pause
""")
    else:
        launcher = INSTALL_DIR / "start_qudi.sh"
        launcher.write_text(f"""#!/bin/bash
cd "{INSTALL_DIR}"
source "{VENV_DIR}/bin/activate"
qudi
read -n 1 -s -r -p "Press any key to close..."
echo
""")
        launcher.chmod(0o755)
    print(f"Created launcher script at {launcher}")


def create_config_dir():
    config_dir = INSTALL_DIR / "config/"
    config_dir.mkdir(exist_ok=True)
    if IQO_MODULES_DIR:
        shutil.copy2(INSTALL_DIR / "qudi-iqo-modules/src/qudi/default.cfg", config_dir / "default.cfg")
    else:
        shutil.copy2(VENV_DIR / "lib/python3.10/site-packages/qudi/default.cfg", config_dir / "default.cfg")
    print(f"Created config directory in {config_dir} and copied default config into it")


def create_desktop_file():
    global DESKTOP_FILE, ICON_URL

    iconfile = INSTALL_DIR
    if os.name == "nt":
        iconname = "logo_qudi.ico"
        ICON_URL += iconname
        iconfile = iconfile / iconname
        DESKTOP_FILE = INSTALL_DIR / "qudi.lnk"

        ps_command = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{DESKTOP_FILE}")
$Shortcut.TargetPath = "{INSTALL_DIR / "start_qudi.bat"}"
$Shortcut.WorkingDirectory = "{INSTALL_DIR}"
$Shortcut.IconLocation = "{iconfile}"
$Shortcut.Save()
        """
        subprocess.run(["powershell", "-Command", ps_command], check=True)

    else:
        iconname = "logo-qudi.svg"
        ICON_URL += iconname
        iconfile = iconfile / iconname
        DESKTOP_FILE = INSTALL_DIR / "qudi.desktop"
        DESKTOP_FILE.write_text(f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Qudi
Comment=Start Qudi Measurement Software
Exec="{INSTALL_DIR}/start_qudi.sh"
Icon={iconfile}
Terminal=true
Categories=Science;Education;
StartupNotify=true
""")
        DESKTOP_FILE.chmod(0o755)

    print(f"Downloading Qudi icon from {ICON_URL}")
    with urlopen(ICON_URL) as response:
        iconfile.write_bytes(response.read())

    print(f"Created desktop file at {DESKTOP_FILE}")

def start_menu_shortcut():
    if not yes_no_choice("Create Start Menu shortcut?"):
        return

    if os.name == "nt":
        start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"

    else:
        start_menu = Path.home() / ".local/share/applications"

    destination = start_menu / DESKTOP_FILE.name
    shutil.copy2(DESKTOP_FILE, destination)
    print(f"Registered Start Menu shortcut at {destination}")


def desktop_shortcut():
    if not yes_no_choice("Create Desktop shortcut?"):
        return

    destination = Path.home() / "Desktop" / DESKTOP_FILE.name

    shutil.copy2(DESKTOP_FILE, destination)
    print(f"Created Desktop shortcut at {destination}")


def installation_choice():
    global CORE_DIR, IQO_MODULES_DIR
    print("How to install Qudi?")
    print("1) Latest, editable version of qudi-iqo-modules, but stable qudi-core release")
    print("2) Latest, editable versions of qudi-core and qudi-iqo-modules")
    print("3) Latest stable, uneditable release of qudi-core and qudi-iqo-modules")

    choice = input("Enter 1, 2, 3 (default 1): ").strip()

    if choice == "3":
        return

    IQO_MODULES_DIR = INSTALL_DIR / 'qudi-iqo-modules'

    if choice != "2":
        return

    CORE_DIR = INSTALL_DIR / 'qudi-core'


def main():
    global CORE_DIR, IQO_MODULES_DIR
    check_python()
    check_git()

    choose_install_dir()

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(INSTALL_DIR)

    create_venv()

    installation_choice()

    install_modules()

    create_launcher()
    create_desktop_file()

    start_menu_shortcut()
    desktop_shortcut()

    create_config_dir()

    print("\nInstallation complete!")
    print(f"Location: {INSTALL_DIR}")


if __name__ == "__main__":
    main()
