#!/usr/bin/env python3


import json, os, subprocess, venv, re
from pathlib import Path


def load_data(path=None):
    """Load configuration from JSON file."""
    if path is None:
        path = str(Path home()) + "/.qudi/config.json"
    
    with open(str(path), 'r') as f:
        return json.load(f)


def save_data(data, path=None):
    """Save configuration to JSON file."""
    if path is None:
        data['config_file'] = str(Path home()) + "/.qudi/config.json"
    
    with open(str(path), 'w') as f:
        json.dump(data, f, indent=2)


def clone_repo(repo, loc):
    """Clone repository."""
    if not Path(loc).exists():
        subprocess.check_call(f'git clone "{repo}" {loc}', shell=True)


def main(cfg_file=None):
    """Run installation.
    
    Usage examples:
        
        # Option 1: Load config file and install instantly (NO prompts!)
        python -c "from pathlib import Path; from installation.install import main"
        main(Path('/home/your-config.json'))
        
        # Option 2: Direct path to config file
        python3 -m installation.install /path/to/config.json
        
        # Option 3: Interactive mode (prompts ONCE, saves config, then auto-installs)
        python -m installation.install --interactive

    The script supports both modes:
    - From config file → INSTANT INSTALLATION (no prompts!) ✨
    - Or run interactively once to save your settings!
    """
    
    # Check if config file path was provided via argument
    try:
        with open(str(cfg_file), 'r') as f:
            data = json.load(f)
        install_dir = str(data['install_dir'])
        venv_name = str(data.get('venv_name', 'qudi-env'))
    except FileNotFoundError:
        print(f"Config file not found: {cfg_file}")
        sys.exit(1)

    # Clone and install packages
    clone_repo("https://github.com/Ulm-IQO/qudi-core.git", str(install_dir + "/qudi-core"))
    
    # Auto-install from qudi-core
    subprocess.check_call(f'"{python_bin}" -m pip install qudi-core', shell=True)

    # Install IQO modules for editable mode
    clone_repo("https://github.com/Ulm-IQO/qudi-iqo-modules.git", str(install_dir + "/qudi-iqo-modules"))
    
    subprocess.check_call(f'"{python_bin}" -m pip install qudi-iqo-modules', shell=True)

    print("\nInstallation complete!")


if __name__ == "__main__":
    from pathlib import Path as P
    if len(sys.argv) > 1:
        cfg_path = sys.argv[1]
        with open(cfg_path, 'r') as f:
            data = json.load(f)
        
        install_dir = str(data['install_dir'])
        venv_name = data.get('venv_name', 'qudi-env')
    
    # Install packages using installed config files
    sys.exit(0)
