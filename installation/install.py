import os
from socket import CAN_RAW_JOIN_FILTERS
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
from dataclasses import dataclass, asdict

from enum import Enum


class InstallationMode(Enum):
    NONE = "None"
    EDITABLE = "Editable"
    PYPI = "PyPi"

@dataclass
class InstallationConfig:
    install_dir: Path = Path.home() / "qudi"
    venv_name: str = "qudi-env"

    # Installation mode
    installation_mode: dict[str, InstallationMode] = {
        "core": InstallationMode.PYPI,
        "iqo": InstallationMode.EDITABLE,
        "scripts": InstallationMode.NONE,
    }

    # repositories
    core_repo: str = "https://github.com/Ulm-IQO/qudi-core.git"
    iqo_repo: str = "https://github.com/Ulm-IQO/qudi-iqo-modules.git"

    # cloned repositories (None means pip install from PyPI)
    core_dir: Optional[Path] = None
    iqo_modules_dir: Optional[Path] = None

    # URLs
    manifest_url: str = (
        "https://raw.githubusercontent.com/actions/python-versions/"
        "main/versions-manifest.json"
    )
    pyproject_url: str = (
        "https://raw.githubusercontent.com/Ulm-IQO/qudi-iqo-modules/"
        "refs/heads/main/pyproject.toml"
    )

    @property
    def venv_dir(self):
        return self.install_dir / self.venv_name

    @property
    def config_dir(self):
        return self.install_dir / "config"

    @property
    def scripts_dir(self):
        return self.install_dir / "scripts"

    @property
    def config_file(self):
        return self.install_dir / "installation.json"

    @property
    def qudi_icon_name(self) -> str:
        if os.name == "nt":
            return "logo_qudi.ico"
        else:
            return "logo-qudi.svg"

    @property
    def qudi_icon_file(self) -> Path:
        return self.install_dir / self.qudi_icon_name

    @property
    def jupyter_icon_file(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Lib/site-packages/jupyter_server/static/favicons/favicon.ico"
        else:
            return self.venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "jupyter_server" / "static" / "favicons" / "favicon.ico"

    @property
    def environment_icon_file(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Scripts/python.exe,0"
        else:
            return self.venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "idlelib" / "Icons" / "idle_48.png"

    @property
    def qudi_icon_url(self) -> str:
        return "https://raw.githubusercontent.com/Ulm-IQO/qudi-core/refs/heads/main/src/qudi/artwork/logo/" + self.qudi_icon_name

    @property
    def activation_script(self) -> Path:
        if os.name == "nt":
            return self.install_dir / "qudi_environment.ps1"
        else:
            return self.install_dir / "qudi_environment.sh"

    @property
    def launcher_script(self) -> Path:
        if os.name == "nt":
            return self.install_dir / "qudi.ps1"
        else:
            return self.install_dir / "qudi.sh"

    @property
    def jupyter_script(self) -> Path:
        if os.name == "nt":
            return self.install_dir / "qudi_jupyter.ps1"
        else:
            return self.install_dir / "qudi_jupyter.sh"

    @property
    def activation_desktop_file(self) -> Path:
        if os.name == "nt":
            return self.install_dir / "qudi_environment.lnk"
        else:
            return self.install_dir / "qudi_environment.desktop"

    @property
    def launcher_desktop_file(self) -> Path:
        if os.name == "nt":
            return self.install_dir / "qudi.lnk"
        else:
            return self.install_dir / "qudi.desktop"

    @property
    def jupyter_desktop_file(self) -> Path:
        if os.name == "nt":
            return self.install_dir / "qudi_jupyter.lnk"
        else:
            return self.install_dir / "qudi_jupyter.desktop"

    @property
    def python_bin(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        else:
            return self.venv_dir / "bin" / "python"

    def save(self):
        data = asdict(self)

        for key, value in data.items():
            if isinstance(value, Path):
                data[key] = str(value)
            elif isinstance(value, Enum):
                data[key] = value.value


        self.config_file.write_text(json.dumps(data, indent=4))

    @classmethod
    def load(cls, filename: Path):
        data = json.loads(filename.read_text())

        for key in [
            "install_dir",
            "core_dir",
            "iqo_modules_dir",
        ]:
            if data.get(key):
                data[key] = Path(data[key])

        for key, value in data["installation_mode"].items():
            data["installation_mode"][key] = InstallationMode(value)

        return cls(**data)

CONFIG = InstallationConfig()


def read_requires_python() -> str:
    with urlopen(CONFIG.pyproject_url) as response:
        content = response.read().decode("utf-8")

    match = re.search(
        r'requires-python\s*=\s*"([^"]+)"',
        content,
        re.DOTALL,
    )

    if match:
        return str(match.group(1))

    raise SystemExit("requires-python not found in pyproject.toml")


def fetch_manifest(url: str = CONFIG.manifest_url) -> list[str]:
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
    print("Where do you want to install Qudi?")
    print("1) Home directory")
    print("2) Current directory")
    print("3) Custom directory")

    choice = input("Enter 1, 2, or 3 (default 1): ").strip()

    if choice == "2":
        CONFIG.install_dir = Path.cwd()
    elif choice == "3":
        custom_path = input("Enter full path for installation: ").strip().strip('"')
        if not custom_path:
            return
        print(custom_path)
        CONFIG.install_dir = Path(custom_path).expanduser().resolve()


def choose_venv_name():
    print("Use a custom virtual environment name?")
    print("1) qudi-env")
    print("2) Custom name")

    choice = input("Enter 1, 2 (default 1): ").strip()

    if choice == "2":
        choice = input("Enter name: ").strip()
        if choice:
            CONFIG.venv_name = choice


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
    if not os.path.exists(CONFIG.venv_dir):
        print(f"Creating virtual environment in '{CONFIG.venv_dir}'...")
        venv.create(CONFIG.venv_dir, with_pip=True)
    else:
        print(f"Virtual environment '{CONFIG.venv_dir}' already exists.")

        if not yes_no_choice("Continuing will modify the existing virtual environment, do you want to continue?"):
            print("Aborting Qudi installation")
            sys.exit(1)


def install_modules():
    run(f'"{CONFIG.python_bin}" -m pip install --upgrade pip')

    if CONFIG.core_dir:
        clone_repo(CONFIG.core_repo, CONFIG.core_dir)
        run(f'"{CONFIG.python_bin}" -m pip install -e "{CONFIG.core_dir}"')
    else:
        run(f'"{CONFIG.python_bin}" -m pip install qudi-core')

    if CONFIG.iqo_modules_dir:
        clone_repo(CONFIG.iqo_repo, CONFIG.iqo_modules_dir)
        run(f'"{CONFIG.python_bin}" -m pip install -e "{CONFIG.iqo_modules_dir}"')
    else:
        run(f'"{CONFIG.python_bin}" -m pip install qudi-iqo-modules')

    run(f'"{CONFIG.python_bin}" -c "from qudi.core.qudikernel import install_kernel; install_kernel()"')


def create_launcher_scripts():
    if os.name == "nt":
        CONFIG.activation_script.write_text(f"""Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
& "{CONFIG.venv_dir}\\Scripts\\Activate.ps1"
""")

        CONFIG.launcher_script.write_text(f"""& "{CONFIG.activation_script}"
        qudi -d
        """)

        CONFIG.jupyter_script.write_text(f"""& "{CONFIG.activation_script}"
        Set-Location "{CONFIG.scripts_dir}"
        jupyter lab
        """)
    else:
        CONFIG.activation_script.write_text(f"""#!/bin/bash
source "{CONFIG.venv_dir}/bin/activate"
""")
        CONFIG.activation_script.chmod(0o755)

        CONFIG.launcher_script.write_text(f"""#!/bin/bash
source "{CONFIG.activation_script}"
qudi -d
read -n 1 -s -r -p "Press any key to close..."
echo
""")
        CONFIG.launcher_script.chmod(0o755)

        CONFIG.jupyter_script.write_text(f"""#!/bin/bash
source "{CONFIG.activation_script}"
cd "{CONFIG.scripts_dir}"
jupyter lab
read -n 1 -s -r -p "Press any key to close..."
echo
""")
        CONFIG.jupyter_script.chmod(0o755)
    print(f"Created launcher scripts at {CONFIG.activation_script}, {CONFIG.launcher_script}, {CONFIG.jupyter_script}")


def create_dirs():
    CONFIG.config_dir.mkdir(exist_ok=True)
    CONFIG.scripts_dir.mkdir(exist_ok=True)
    if CONFIG.iqo_modules_dir:
        shutil.copy2(CONFIG.iqo_modules_dir / "qudi-iqo-modules/src/qudi/default.cfg", CONFIG.config_dir / "default.cfg")
    else:
        site_packages = site_packages = next((CONFIG.venv_dir / "lib").glob("python*/site-packages"))
        shutil.copy2(site_packages / "qudi/default.cfg", CONFIG.config_dir / "default.cfg")
    print(f"Created config directory in {CONFIG.config_dir} and copied default config into it")
    print(f"Created script directory in {CONFIG.scripts_dir}")


def create_desktop_file():
    if os.name == "nt":
        ps_command = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{CONFIG.launcher_desktop_file}")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.WorkingDirectory = "{CONFIG.install_dir}"
$Shortcut.Arguments = '-ExecutionPolicy Bypass -NoExit -File "{CONFIG.launcher_script}"'
$Shortcut.IconLocation = "{CONFIG.qudi_icon_file}"
$Shortcut.Save()
        """
        subprocess.run(["powershell", "-Command", ps_command], check=True)

        ps_command = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{CONFIG.jupyter_desktop_file}")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.WorkingDirectory = "{CONFIG.install_dir}"
$Shortcut.Arguments = '-ExecutionPolicy Bypass -NoExit -File "{CONFIG.jupyter_script}"'
$Shortcut.IconLocation = "{CONFIG.jupyter_icon_file}"
$Shortcut.Save()
        """
        subprocess.run(["powershell", "-Command", ps_command], check=True)

        ps_command = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{CONFIG.activation_desktop_file}")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.WorkingDirectory = "{CONFIG.install_dir}"
$Shortcut.Arguments = '-ExecutionPolicy Bypass -NoExit -File "{CONFIG.activation_script}"'
$Shortcut.IconLocation = "{CONFIG.environment_icon_file}"
$Shortcut.Save()
        """
        subprocess.run(["powershell", "-Command", ps_command], check=True)

    else:
        # Qudi
        CONFIG.launcher_desktop_file.write_text(f"""[Desktop Entry]
    Version=1.0
    Type=Application
    Name=Qudi
    Comment=Start Qudi Measurement Software
    Exec="{CONFIG.launcher_script}"
    Path={CONFIG.install_dir}
    Icon={CONFIG.qudi_icon_file}
    Terminal=true
    Categories=Science;Education;
    StartupNotify=true
    """)
        CONFIG.launcher_desktop_file.chmod(0o755)

        # Jupyter
        CONFIG.jupyter_desktop_file.write_text(f"""[Desktop Entry]
    Version=1.0
    Type=Application
    Name=Qudi Jupyter
    Comment=Start Jupyter Notebook in the Qudi environment
    Exec="{CONFIG.jupyter_script}"
    Path={CONFIG.install_dir}
    Icon={CONFIG.jupyter_icon_file}
    Terminal=true
    Categories=Science;Education;
    StartupNotify=true
    """)
        CONFIG.jupyter_desktop_file.chmod(0o755)

        # Environment shell
        CONFIG.activation_desktop_file.write_text(f"""[Desktop Entry]
    Version=1.0
    Type=Application
    Name=Qudi Environment
    Comment=Open a shell with the Qudi environment activated
    Exec=bash -i -c 'source "{CONFIG.activation_script}"; exec bash'
    Path={CONFIG.install_dir}
    Icon={CONFIG.environment_icon_file}
    Terminal=true
    Categories=Development;
    StartupNotify=true
    """)
        CONFIG.activation_desktop_file.chmod(0o755)

    print(f"Downloading Qudi icon from {CONFIG.qudi_icon_url}")
    with urlopen(CONFIG.qudi_icon_url) as response:
        CONFIG.qudi_icon_file.write_bytes(response.read())

    print(f"Created desktop file at (launcher: {CONFIG.launcher_desktop_file}) , (environment: {CONFIG.activation_desktop_file}), (jupyter: {CONFIG.jupyter_desktop_file})")

def start_menu_shortcut():
    if not yes_no_choice("Create Start Menu shortcut?"):
        return

    if os.name == "nt":
        start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"

    else:
        start_menu = Path.home() / ".local/share/applications"

    destination = start_menu / CONFIG.launcher_desktop_file.name
    shutil.copy2(CONFIG.launcher_desktop_file, destination)
    print(f"Registered Start Menu shortcut at {destination}")


def desktop_shortcut():
    if not yes_no_choice("Create Desktop shortcut?"):
        return

    destination = Path.home() / "Desktop" / CONFIG.launcher_desktop_file.name

    shutil.copy2(CONFIG.launcher_desktop_file, destination)
    print(f"Created Desktop shortcut at {destination}")


def installation_choice():
    print("How to install Qudi?")
    print("1) Latest, editable version of qudi-iqo-modules, but stable qudi-core release")
    print("2) Latest, editable versions of qudi-core and qudi-iqo-modules")
    print("3) Latest stable, uneditable release of qudi-core and qudi-iqo-modules")

    choice = input("Enter 1, 2, 3 (default 1): ").strip()

    if choice == "3":
        return

    CONFIG.iqo_modules_dir = CONFIG.install_dir / 'qudi-iqo-modules'

    if choice != "2":
        return

    CONFIG.core_dir = CONFIG.install_dir / 'qudi-core'


def main():
    global CONFIG
    check_python()
    check_git()

    choose_install_dir()

    CONFIG.install_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(CONFIG.install_dir)

    create_venv()

    installation_choice()

    install_modules()

    create_dirs()

    create_launcher_scripts()
    create_desktop_file()

    start_menu_shortcut()
    desktop_shortcut()

    print("\nInstallation complete!")
    print(f"Location: {CONFIG.install_dir}")


if __name__ == "__main__":
    main()
