#!/usr/bin/env python3
"""
Qudi Installation Module

Provides simplified, user-friendly installation options for Qudi.
All configuration is managed through a single dataclass: InstallationConfig().

See INSTALLATION_CONFIG.md for detailed documentation.
"""

import os
from socket import CAN_RAW_JOIN_FILTERS
import subprocess
import sys
import venv
import shutil
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
import json
import re
from dataclasses import dataclass, asdict
from typing import Optional

from enum import Enum


class InstallationMode(Enum):
    NONE = "None"
    EDITABLE = "Editable"
    PYPI = "PyPi"


@dataclass
class InstallationConfig:
    """Configuration object for Qudi installation.
    
    All parameters can be set in ONE PLACE using this dataclass:
    
    Example:
        config = main(
            install_dir=Path("/home/tobias/qudi"),
            venv_name="qudi-env",
            installation_iqo='Editable',
            installation_core='PyPi',
        )
        main(config, verbose=True)
        
        # Save and use later
        config.config_file.write_text(...)"  saves to ~/qudi/qudi-env.json
        
        # Or load
        saved_config = load_config_file("/path/to/config.json")
    """
    # Basic configuration
    install_dir: Path = Path.home() / "qudi"
    venv_name: str = "qudi-env"

    # Installation mode (installation_mode["iqo"]: None=pypi, EDITABLE=editable, PYPI=pypi)
    installation_iqo: Optional[InstallationMode] = None
    installation_core: Optional[InstallationMode] = None
    installation_scripts: bool = True

    # Repositories
    core_repo: str = "https://github.com/Ulm-IQO/qudi-core.git"
    iqo_repo: str = "https://github.com/Ulm-IQO/qudi-iqo-modules.git"

    # Cloned repositories (None means pip install from PyPI)
    core_dir: Optional[Path] = None
    iqo_modules_dir: Optional[Path] = None

    # URLs for fetching package information
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
        return Path(str(self.install_dir) + "/" + str(self.venv_name) + ".json")

    @property
    def python_bin(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        else:
            return self.venv_dir / "bin" / "python"

    def update_urls(self, manifest_url: str = None, pyproject_url: str = None):
        """Update URLs after config creation"""
        self.manifest_url = manifest_url or self.manifest_url
        self.pyproject_url = pyproject_url or self.pyproject_url

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
        for key in ["install_dir", "core_dir", "iqo_modules_dir"]:
            if data.get(key):
                data[key] = Path(data[key])
        return cls(**data)


# Global default configuration object for backward compatibility
INSTALLATION_CONFIG = InstallationConfig(
    installation_iqo='PyPi',
    installation_core='PyPi'
)


def run_command():
    """Helper function to run shell commands"""
    return subprocess.check_call(cmd, shell=True)


def load_config_file(filename: Path = None):
    """Load configuration from a saved JSON file.

    Example:
        config = load_config_file("/path/to/config.json")
    """
    if filename is None:
        filename = INSTALLATION_CONFIG.config_file
    
    loaded_config = InstallationConfig.load(filename)
    
    return loaded_config


def save_config(config: InstallationConfig = None):
    """Save configuration to a file.

    Args:
        config: Configuration object (defaults to global state if available).
        
    Example:
        config = main(custom_dir='/path')
        save_config(config)  # Saves to ~/qudi/qudi-env.json
    """
    if config is not None:
        config.save()
    else:
        config = INSTALLATION_CONFIG
        config.save()
    
    print(f"Configuration saved to {config.config_file}")


def read_requires_python():
    with urlopen(INSTALLATION_CONFIG.pyproject_url) as response:
        content = response.read().decode("utf-8")
    match = re.search(
        r'requires-python\s*=\s*"([^"]+)"', content, re.DOTALL
    )
    if match:
        return str(match.group(1))
    raise SystemExit("requires-python not found in pyproject.toml")


def fetch_manifest(url: str = INSTALLATION_CONFIG.manifest_url) -> list[str]:
    """Fetch Python versions from manifest."""
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
    """Convert a comma-separated spec string into a list of check functions."""
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
    """Clone repository if not already present."""
    if not location.exists():
        run(f'git clone {repo} "{location}"')
    else:
        print(f"{location} already exists, doing nothing.")


def check_python():
    """Check Python version compatibility."""
    supported_versions = filter_versions(read_requires_python(), fetch_manifest())
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if version not in supported_versions:
        print(f"Detected Python version ({version}) is not in supported versions {supported_versions}.")
        sys.exit(1)
    print(f"Found Python version {version}")


def check_git():
    """Check for git installation."""
    if shutil.which("git") is None:
        print("Git not found. Please install Git.")
        sys.exit(1)
    print("Found Git installation")


def create_venv(config: InstallationConfig, verbose: bool = False):
    """Create virtual environment with optional user input."""
    if config is None:
        print("Please provide a config object for the installation")
    
    if not os.path.exists(config.venv_dir):
        print(f"Creating virtual environment in '{config.venv_dir}'...")
        venv.create(config.venv_dir, with_pip=True)
        
        should_continue = input(
            f"Continuing will modify the existing virtual environment "
            f"(at {config.venv_dir}). Continue? [y/N]: "
        ).strip().lower() in ('yes', 'y')
        
        if not should_continue:
            print("Aborting Qudi installation")
            sys.exit(1)


def install_modules(config: InstallationConfig, verbose: bool = False):
    """Install modules using configuration."""
    pip_upgrade_cmd = f'"{config.python_bin}" -m pip install --upgrade pip'
    
    if config.core_dir:
        clone_repo(config.core_repo, config.core_dir)
        if verbose:
            print(f"Installing qudi-core (editable) in {config.core_dir}")
        run(f'"{config.python_bin}" -m pip install -e "{config.core_dir}"')
    else:
        if verbose:
            print("Installing qudi-core from PyPI")
        run(f'"{config.python_bin}" -m pip install qudi-core')

    if config.iqo_modules_dir:
        clone_repo(config.iqo_repo, config.iqo_modules_dir)
        if verbose:
            print(f"Installing qudi-iqo-modules (editable) in {config.iqo_modules_dir}")
        run(f'"{config.python_bin}" -m pip install -e "{config.iqo_modules_dir}"')
    else:
        if verbose:
            print("Installing qudi-iqo-modules from PyPI")
        run(f'"{config.python_bin}" -m pip install qudi-iqo-modules')
    
    if verbose:
        print("Installing Qudi kernel")
    run(f'"{config.python_bin}" -c "from qudi.core.qudikernel import install_kernel; install_kernel()"')


def create_venv_scripts(config: InstallationConfig):
    """Create launcher and activation scripts for Windows/Linux."""
    # Bash script (works on both platforms)
    activate_script = f"""#!/bin/bash
source "{config.venv_dir}/bin/activate"
"""
    config.activation_script.write_text(activate_script)
    config.activation_script.chmod(0o755)

    launcher_template = f"""\n\n# Launch Qudi GUI
qudi -d\nread -n 1 -s -r -p "Press any key to close..."\necho\n"""
    
    config.launcher_script.write_text(activate_script + launcher_template)
    config.launcher_script.chmod(0o755)

    jupyter_script = f"""#!/bin/bash
source "{config.venv_dir}/bin/activate"
cd "{config.scripts_dir}"
jupyter lab
read -n 1 -s -r -p "Press any key to close..."\necho\n"""
    config.jupyter_script.write_text(jupyter_script)
    config.jupyter_script.chmod(0o755)


def create_windows_shortcuts(config: InstallationConfig):
    """Create Windows shortcut (.lnk/.desktop) files."""
    # Download and save icon for Windows use
    try:
        with urlopen(config.qudi_icon_url) as response:
            config.qudi_icon_file.write_bytes(response.read())
    except URLError as e:
        print(f"Warning: Could not download icon ({e})")
    
    # Create launcher command using PowerShell COM objects
    ps_command = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{config.launcher_desktop_file}")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.WorkingDirectory = "{config.install_dir}"
$Shortcut.Arguments = '-ExecutionPolicy Bypass -NoExit -File "{config.launcher_script}"'
$Shortcut.IconLocation = "{config.qudi_icon_file}"
$Shortcut.Save()
"""
    subprocess.run(["powershell", "-Command", ps_command], check=True)


def create_desktop_entry_files(config: InstallationConfig):
    """Create bash scripts and desktop entry files for all platforms."""
    
    # Bash activation script (works on both Windows/Linux bash)
    activate_script = f"""#!/bin/bash
source "{config.venv_dir}/bin/activate"
"""
    config.activation_script.write_text(activate_script)
    config.activation_script.chmod(0o755)

    # Launcher script (bash)
    launcher_file = f"""#!/bin/bash
source "{config.venv_dir}/bin/activate"
cd "{config.scripts_dir}"
qudi -d
read -n 1 -s -r -p "Press any key to close..."
echo
"""
    config.launcher_script.write_text(launcher_file)
    config.launcher_script.chmod(0o755)

    # Jupyter script (bash)
    jupyter_file = f"""#!/bin/bash
source "{config.venv_dir}/bin/activate"
cd "{config.scripts_dir}"
jupyter lab
read -n 1 -s -r -p "Press any key to close..."
echo
"""
    config.jupyter_script.write_text(jupyter_file)
    config.jupyter_script.chmod(0o755)

    # Download and save icon for all platforms
    try:
        with urlopen(config.qudi_icon_url) as response:
            config.qudi_icon_file.write_bytes(response.read())
    except URLError as e:
        print(f"Warning: Could not download icon ({e})")


def create_dirs(config: InstallationConfig):
    """Create config and script directories."""
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.scripts_dir.mkdir(parents=True, exist_ok=True)
    
    if config.iqo_modules_dir:
        shutil.copy2(
            config.iqo_modules_dir / "qudi-iqo-modules/src/qudi/default.cfg",
            config.config_dir / "default.cfg"
        )
    else:
        # Extract from installed packages
        site_packages = next((config.venv_dir / "lib".glob("python*/site-packages")), None)
        
        if site_packages:
            shutil.copy2(
                site_packages / "qudi/default.cfg",
                config.config_dir / "default.cfg"
            )
    
    print(f"Created config directory in {config.config_dir}")
    print(f"Created script directory in {config.scripts_dir}")


def start_menu_shortcut(config: InstallationConfig = None):
    """Create a Start Menu shortcut (Windows only)."""
    if os.name != "nt":
        print("Start menu shortcuts not supported on this platform")
        return
    
    if config is None:
        config = INSTALLATION_CONFIG
    
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"

    destination = start_menu / config.launcher_desktop_file.name
    shutil.copy2(config.launcher_desktop_file, destination)
    print(f"Registered Start Menu shortcut at {destination}")


def desktop_shortcut(config: InstallationConfig = None):
    """Create a Desktop shortcut."""
    if config is None:
        config = INSTALLATION_CONFIG
    
    destination = Path.home() / "Desktop" / config.launcher_desktop_file.name

    shutil.copy2(config.launcher_desktop_file, destination)
    print(f"Created Desktop shortcut at {destination}")


def installation_choice(config: InstallationConfig):
    """User-friendly prompt for installation options.
    
    When called from main() with a proper config object, this will show optional prompts.
    Users can choose programmatically by setting mode parameter in main().
    
    Args:
        config: Configuration object
    """
    # Only prompt for user input if config hasn't been set up yet
    if config.installation_iqo is not None or config.installation_core is not None:
        print("Using provided configuration")
        return
    
    # Show interactive prompts for new users
    print("\nHow to install Qudi?")
    print("1) Editable qudi-iqo-modules with PyPI qudi-core")
    print("2) Both editable versions of qudi-core and qudi-iqo-modules")
    print("3) Use saved configuration from file")

    mode = input("Enter 1, 2, or 3 (default 3): ").strip()
    
    try:
        if len(mode) > 0:
            m = int(mode)
            if m == 1:
                print("Editable qudi-iqo-modules with PyPI")
            elif m == 2:
                config.core_dir = config.install_dir / 'qudi-core'
            elif m != 3:
                return
    except ValueError:
        # Invalid input - just use default (option 3 - saved config)
        print("Using saved configuration")
    else:
        # Valid input was provided
        pass


def main(config: InstallationConfig = None, verbose: bool = False):
    """Run installation using the given configuration.

    Args:
        config: InstallationConfig object. If not provided:
            - First tries to use saved config from file  
            - Then uses defaults (editable IQO modules, PyPI core)
        
        verbose: Whether to show verbose output during installation

    Example:
        # Quick install with ALL parameters in ONE place
        config = main(
            install_dir=Path("/home/tobias/qudi"),
            venv_name="my-qudi",
            installation_iqo='Editable',
            installation_core='PyPi'
        )
        
        # Then save for later use
        config.save()
        
        # Or load and reuse
        saved_config = load_config_file("/path/to/config.json")
        main(saved_config)
    """
    if config is None:
        try:
            # Try to load from file first
            config = InstallationConfig.load(INSTALLATION_CONFIG.config_file)
        except (FileNotFoundError, ValueError):
            # Use defaults for editable IQO modules, PyPI core
            config = INSTALLATION_CONFIG
    
    check_python()
    check_git()

    config.install_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(config.install_dir)

    create_venv(config, verbose=verbose)

    installation_choice(config)

    install_modules(config, verbose=verbose)

    create_dirs(config)

    # Create cross-platform scripts and shortcuts
    create_windows_shortcuts(config)
    create_desktop_entry_files(config)
    
    start_menu_shortcut(config)
    desktop_shortcut(config)

    print("\nInstallation complete!")
    print(f"Location: {config.install_dir}")


if __name__ == "__main__":
    # Default behavior when run directly
    try:
        installed_config = load_config_file(INSTALLATION_CONFIG.config_file)
    except (FileNotFoundError, ValueError):
        # First-time users - show help
        print("Starting Qudi installation...")
        
        # Get user choices interactively
        mode_choice = input(
            "Install type:\n"
            "1. Editable IQO with PyPI core\n"
            "2. Both as editable\n"
            "3. Use default config (skip interactive)\n"
        ).strip()
        
        if len(mode_choice) > 0:
            try:
                mode = int(mode_choice)
                if mode == 1:
                    print("Installing editable IQO modules with PyPI core")
                elif mode == 2:
                    INSTALLATION_CONFIG.core_dir = INSTALLATION_CONFIG.install_dir / 'qudi-core'
                    # Create a config object for this run
                    config = InstallationConfig(core_dir=INSTALLATION_CONFIG.core_dir)
                    main(config, verbose=True)
                else:
                    print("Using default configuration")
                    main(INSTALLATION_CONFIG, verbose=True)
            except ValueError:
                mode = 3
    
    # Direct call with all parameters (main now has all config as keyword args!)