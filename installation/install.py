import argparse
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
from dataclasses import dataclass, asdict, fields
from enum import Enum


class InstallationMode(Enum):
    EDITABLE = "Editable"
    PYPI = "PyPi"


@dataclass
class InstallationConfig:
    install_dir: Path = Path.home() / "qudi"
    venv_name: str = "qudi-env"

    # installation modes
    core_mode: InstallationMode = InstallationMode.PYPI
    iqo_mode: InstallationMode = InstallationMode.EDITABLE

    # answers to yes / no questions, used for unattended installation
    modify_existing_venv: bool = False
    create_start_menu_shortcuts: bool = False
    create_desktop_shortcuts: bool = False

    # repositories
    core_repo: str = "https://github.com/Ulm-IQO/qudi-core.git"
    iqo_repo: str = "https://github.com/Ulm-IQO/qudi-iqo-modules.git"

    # URLs
    manifest_url: str = "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"
    pyproject_url: str = (
        "https://raw.githubusercontent.com/Ulm-IQO/qudi-iqo-modules/refs/heads/main/pyproject.toml"
    )
    default_config_url: str = (
        "https://raw.githubusercontent.com/Ulm-IQO/qudi-iqo-modules/refs/heads/main/installation/default.json"
    )

    @property
    def venv_dir(self) -> Path:
        return self.install_dir / self.venv_name

    @property
    def core_dir(self) -> Optional[Path]:
        """Location of the cloned qudi-core repository (None means pip install from PyPI)"""
        if self.core_mode == InstallationMode.EDITABLE:
            return self.install_dir / "qudi-core"
        return None

    @property
    def iqo_modules_dir(self) -> Optional[Path]:
        """Location of the cloned qudi-iqo-modules repository (None means pip install from PyPI)"""
        if self.iqo_mode == InstallationMode.EDITABLE:
            return self.install_dir / "qudi-iqo-modules"
        return None

    @property
    def config_dir(self) -> Path:
        return self.install_dir / "config"

    @property
    def scripts_dir(self) -> Path:
        return self.install_dir / "scripts"

    @property
    def config_file(self) -> Path:
        return self.install_dir / "installation.json"

    @property
    def site_packages_dir(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Lib" / "site-packages"
        else:
            return (
                self.venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
            )

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
        return self.site_packages_dir / "jupyter_server" / "static" / "favicons" / "favicon.ico"

    @property
    def environment_icon_file(self) -> str:
        if os.name == "nt":
            return f"{self.python_bin},0"
        else:
            # qtconsole is a dependency of qudi-core, so its terminal icon is always available in the venv
            return str(self.site_packages_dir / "qtconsole" / "resources" / "icon" / "JupyterConsole.svg")

    @property
    def qudi_icon_url(self) -> str:
        return (
            "https://raw.githubusercontent.com/Ulm-IQO/qudi-core/refs/heads/main/src/qudi/artwork/logo/"
            + self.qudi_icon_name
        )

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
    def desktop_files(self) -> list[Path]:
        return [self.launcher_desktop_file, self.jupyter_desktop_file, self.activation_desktop_file]

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
        """Load settings from a JSON file. Missing keys keep their default value."""
        return cls.from_json(filename.read_text(), source=str(filename))

    @classmethod
    def download(cls, url: str):
        """Load settings from a JSON file at `url`. Missing keys keep their default value."""
        try:
            with urlopen(url, timeout=30) as response:
                text = response.read().decode("utf-8")
        except URLError as e:
            raise SystemExit(f"Failed to download settings file {url}: {e}")
        return cls.from_json(text, source=url)

    @classmethod
    def from_json(cls, text: str, source: str):
        config = cls()
        field_names = {field.name for field in fields(cls)}

        for key, value in json.loads(text).items():
            if key not in field_names:
                print(f'Warning: ignoring unknown setting "{key}" in {source}')
                continue
            # convert the JSON value to the type of the default value, e.g. Path or InstallationMode
            default = getattr(config, key)
            if isinstance(default, Path):
                value = Path(value).expanduser().resolve()
            elif isinstance(default, Enum):
                value = type(default)(value)
            setattr(config, key, value)

        return config


CONFIG = InstallationConfig()
# answer all questions with the values from CONFIG instead of asking the user
UNATTENDED = False


def read_requires_python() -> str:
    with urlopen(CONFIG.pyproject_url) as response:
        content = response.read().decode("utf-8")

    match = re.search(r'requires-python\s*=\s*"([^"]+)"', content, re.DOTALL)

    if match:
        return str(match.group(1))

    raise SystemExit("requires-python not found in pyproject.toml")


def fetch_manifest(url: Optional[str] = None) -> list[str]:
    url = url or CONFIG.manifest_url
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


def configured_choice(message: str, key: str) -> bool:
    """Ask a yes / no question, or take the answer from the setting `key` of CONFIG in unattended mode."""
    if UNATTENDED:
        answer = getattr(CONFIG, key)
        print(f"{message} {'yes' if answer else 'no'} (from settings file)")
    else:
        answer = yes_no_choice(message)
        setattr(CONFIG, key, answer)
    return answer


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
        print(
            f"Detected Python version ({version}) is not in supported versions {supported_versions}. "
            "Please use a supported Python version."
        )
        sys.exit(1)
    print(f"Found Python version {version}")


def check_git():
    if shutil.which("git") is None:
        print("Git not found. Please install Git.")
        sys.exit(1)
    print("Found Git installation")


def confirm_existing_venv():
    if not CONFIG.venv_dir.exists():
        return

    print(f"Virtual environment '{CONFIG.venv_dir}' already exists.")
    if not configured_choice(
        "Continuing will modify the existing virtual environment, do you want to continue?", "modify_existing_venv"
    ):
        print("Aborting Qudi installation")
        sys.exit(1)


def create_venv():
    if not CONFIG.venv_dir.exists():
        print(f"Creating virtual environment in '{CONFIG.venv_dir}'...")
        venv.create(CONFIG.venv_dir, with_pip=True)
    else:
        print(f"Using existing virtual environment '{CONFIG.venv_dir}'.")


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
        # scripts are dot-sourced, so the activation persists in the calling scope
        CONFIG.activation_script.write_text(f"""Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. "{CONFIG.venv_dir}\\Scripts\\Activate.ps1"
""")

        CONFIG.launcher_script.write_text(f""". "{CONFIG.activation_script}"
qudi -d
""")

        CONFIG.jupyter_script.write_text(f""". "{CONFIG.activation_script}"
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
    print(
        f"Created launcher scripts at {CONFIG.activation_script}, {CONFIG.launcher_script}, {CONFIG.jupyter_script}"
    )


def create_dirs():
    CONFIG.config_dir.mkdir(exist_ok=True)
    CONFIG.scripts_dir.mkdir(exist_ok=True)
    if CONFIG.iqo_modules_dir:
        default_cfg = CONFIG.iqo_modules_dir / "src" / "qudi" / "default.cfg"
    else:
        default_cfg = CONFIG.site_packages_dir / "qudi" / "default.cfg"
    shutil.copy2(default_cfg, CONFIG.config_dir / "default.cfg")
    print(f"Created config directory in {CONFIG.config_dir} and copied default config into it")
    print(f"Created script directory in {CONFIG.scripts_dir}")


def create_windows_shortcut(shortcut: Path, script: Path, icon, no_exit: bool = False):
    arguments = f'-ExecutionPolicy Bypass {"-NoExit " if no_exit else ""}-File "{script}"'
    ps_command = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut}")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.WorkingDirectory = "{CONFIG.install_dir}"
$Shortcut.Arguments = '{arguments}'
$Shortcut.IconLocation = "{icon}"
$Shortcut.Save()
"""
    subprocess.run(["powershell", "-Command", ps_command], check=True)


def create_linux_desktop_file(desktop_file: Path, name: str, comment: str, exec_cmd: str, icon, categories: str):
    desktop_file.write_text(f"""[Desktop Entry]
Version=1.0
Type=Application
Name={name}
Comment={comment}
Exec={exec_cmd}
Path={CONFIG.install_dir}
Icon={icon}
Terminal=true
Categories={categories}
StartupNotify=true
""")
    desktop_file.chmod(0o755)


def create_desktop_files():
    print(f"Downloading Qudi icon from {CONFIG.qudi_icon_url}")
    try:
        with urlopen(CONFIG.qudi_icon_url, timeout=30) as response:
            CONFIG.qudi_icon_file.write_bytes(response.read())
    except URLError as e:
        print(f"Warning: Could not download Qudi icon ({e})")

    if os.name == "nt":
        create_windows_shortcut(CONFIG.launcher_desktop_file, CONFIG.launcher_script, CONFIG.qudi_icon_file)
        create_windows_shortcut(CONFIG.jupyter_desktop_file, CONFIG.jupyter_script, CONFIG.jupyter_icon_file)
        create_windows_shortcut(
            CONFIG.activation_desktop_file, CONFIG.activation_script, CONFIG.environment_icon_file, no_exit=True
        )
    else:
        create_linux_desktop_file(
            CONFIG.launcher_desktop_file,
            name="Qudi",
            comment="Start Qudi Measurement Software",
            exec_cmd=f'"{CONFIG.launcher_script}"',
            icon=CONFIG.qudi_icon_file,
            categories="Science;",
        )
        create_linux_desktop_file(
            CONFIG.jupyter_desktop_file,
            name="Qudi Jupyter",
            comment="Start Jupyter Lab in the Qudi environment",
            exec_cmd=f'"{CONFIG.jupyter_script}"',
            icon=CONFIG.jupyter_icon_file,
            categories="Science;",
        )
        create_linux_desktop_file(
            CONFIG.activation_desktop_file,
            name="Qudi Environment",
            comment="Open a shell with the Qudi environment activated",
            # desktop entry spec: quote the argument and escape nested quotes as \\"
            exec_cmd=f'bash -c "source \\\\"{CONFIG.activation_script}\\\\" && exec bash -i"',
            icon=CONFIG.environment_icon_file,
            categories="Development;",
        )

    print(
        f"Created desktop files at (launcher: {CONFIG.launcher_desktop_file}), "
        f"(environment: {CONFIG.activation_desktop_file}), (jupyter: {CONFIG.jupyter_desktop_file})"
    )


def copy_desktop_files(destination_dir: Path):
    destination_dir.mkdir(parents=True, exist_ok=True)
    for desktop_file in CONFIG.desktop_files:
        destination = destination_dir / desktop_file.name
        shutil.copy2(desktop_file, destination)
        print(f"Created shortcut at {destination}")


def choose_shortcuts():
    """Ask for the shortcuts up front, so the installation itself runs without further questions."""
    configured_choice("Create Start Menu shortcuts?", "create_start_menu_shortcuts")
    configured_choice("Create Desktop shortcuts?", "create_desktop_shortcuts")


def start_menu_shortcut():
    if not CONFIG.create_start_menu_shortcuts:
        return

    if os.name == "nt":
        start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Qudi"
    else:
        start_menu = Path.home() / ".local" / "share" / "applications"

    copy_desktop_files(start_menu)


def desktop_shortcut():
    if not CONFIG.create_desktop_shortcuts:
        return

    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        print(f"Desktop directory {desktop} not found, skipping Desktop shortcuts.")
        return

    copy_desktop_files(desktop)


def installation_choice():
    print("How to install Qudi?")
    print("1) Latest, editable version of qudi-iqo-modules, but stable qudi-core release")
    print("2) Latest, editable versions of qudi-core and qudi-iqo-modules")
    print("3) Latest stable, uneditable release of qudi-core and qudi-iqo-modules")

    choice = input("Enter 1, 2, 3 (default 1): ").strip()

    if choice == "3":
        CONFIG.core_mode = InstallationMode.PYPI
        CONFIG.iqo_mode = InstallationMode.PYPI
    elif choice == "2":
        CONFIG.core_mode = InstallationMode.EDITABLE
        CONFIG.iqo_mode = InstallationMode.EDITABLE
    else:
        CONFIG.core_mode = InstallationMode.PYPI
        CONFIG.iqo_mode = InstallationMode.EDITABLE


def parse_args():
    parser = argparse.ArgumentParser(description="Install Qudi and the qudi-iqo-modules.")
    parser.add_argument(
        "--config",
        type=Path,
        help="settings file (JSON, e.g. an installation.json of a previous installation) for an unattended installation",
    )
    return parser.parse_args()


def main():
    global CONFIG, UNATTENDED
    args = parse_args()

    if args.config:
        config_file = args.config.expanduser().resolve()
        if not config_file.is_file():
            raise SystemExit(f"Settings file {config_file} not found")
        CONFIG = InstallationConfig.load(config_file)
        UNATTENDED = True
        print(f"Unattended installation using settings from {config_file}")
    elif yes_no_choice("Use the recommended default installation?"):
        print(f"Downloading default settings from {CONFIG.default_config_url}")
        CONFIG = InstallationConfig.download(CONFIG.default_config_url)
        UNATTENDED = True
        print("Unattended installation using the default settings")

    check_python()

    if not UNATTENDED:
        choose_install_dir()
        choose_venv_name()
        installation_choice()

    confirm_existing_venv()
    choose_shortcuts()

    if not UNATTENDED:
        print("\nAll questions answered, the installation now runs without further input.\n")

    CONFIG.install_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(CONFIG.install_dir)

    if CONFIG.core_dir or CONFIG.iqo_modules_dir:
        check_git()

    create_venv()

    install_modules()

    create_dirs()

    create_launcher_scripts()
    create_desktop_files()

    start_menu_shortcut()
    desktop_shortcut()

    CONFIG.save()
    print(f"Saved installation settings to {CONFIG.config_file}")

    print("\nInstallation complete!")
    print(f"Location: {CONFIG.install_dir}")


if __name__ == "__main__":
    main()
