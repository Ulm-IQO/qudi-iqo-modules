# -*- coding: utf-8 -*-

"""
This is a utility module for the workflows
It tests the installation script installation/install.py by running a complete installation and
update in a temporary directory.

The installation script of the current checkout is tested, the modules are installed from GitHub
like in a real installation:
- unattended, editable installation using a settings file (--config), optionally of a certain
  repository and branch (--iqo-repo, --iqo-branch)
- check of the created files, the virtual environment, the Jupyter kernel and a headless qudi start
- update of the cloned repository, which is reset to an older commit to be behind its remote branch
  (fast-forward)
- update with local changes conflicting with the update (stash), which has to stop before
  installing the packages
- optionally (--with-pypi) an interactive installation of the PyPI releases with piped answers

Usage: python test_install_script.py [--work-dir DIR] [--iqo-repo URL] [--iqo-branch BRANCH] [--with-pypi]

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[2] / "installation" / "install.py"
SUBPROCESS_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")


def load_installer():
    """Import install.py as module to get the locations of the installed files from its InstallationConfig."""
    spec = importlib.util.spec_from_file_location("qudi_installer", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_installer()


class TestFailed(Exception):
    pass


_group_open = False


def section(title: str):
    """Start a new section of the output, collapsible on GitHub Actions."""
    global _group_open
    if os.environ.get("GITHUB_ACTIONS"):
        if _group_open:
            print("::endgroup::", flush=True)
        print(f"::group::{title}", flush=True)
        _group_open = True
    else:
        print(f"\n===== {title}", flush=True)


def check(condition, message: str):
    if not condition:
        raise TestFailed(message)
    print(f"  ok: {message}", flush=True)


def run(cmd: list, cwd=None, stdin: str = "", log_file: Path = None) -> subprocess.CompletedProcess:
    """Run a command, write its output into log_file and print it."""
    print(f"> {' '.join(str(part) for part in cmd)}", flush=True)
    result = subprocess.run(
        [str(part) for part in cmd],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        errors="replace",
        env=SUBPROCESS_ENV,
    )
    output = result.stdout + result.stderr
    if log_file:
        log_file.write_text(output, encoding="utf-8")
    print(output, flush=True)
    return result


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode:
        raise TestFailed(f"git {' '.join(args)} failed in {repo}:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def reset_behind_remote(clone: Path) -> str:
    """Reset the cloned repository to an older commit, so the update has to fast-forward it to its remote branch.

    Returns the name of a file modified by the update, which can be used to cause a conflict.
    """
    upstream = git(clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    for depth in (5, 20, 100):
        old = git(clone, "rev-parse", f"{upstream}~{depth}")
        modified = git(clone, "diff", "--name-only", "--diff-filter=M", old, upstream).splitlines()
        text_files = [name for name in modified if name.endswith((".md", ".py", ".txt"))]
        if text_files:
            git(clone, "reset", "-q", "--hard", old)
            return text_files[0]
    raise TestFailed(f"No text file modified in the last 100 commits of {upstream}")


def restore_tested_installer(config):
    """The update replaces install.py with the version of the cloned repository, use the tested one again."""
    shutil.copy2(INSTALLER, config.installer_file)


def check_installation(config):
    """Check the files, the virtual environment and the Jupyter kernel of an installation."""
    for path in (
        config.config_file,
        config.installer_file,
        config.activation_script,
        config.launcher_script,
        config.jupyter_script,
        config.update_script,
        config.config_dir / "default.cfg",
        config.scripts_dir,
        config.python_bin,
        *config.desktop_files,
    ):
        check(path.exists(), f"{path.relative_to(config.install_dir)} exists")
    check(list(config.log_dir.glob("install_*.log")), "installation log written")

    # the editable package has to be imported from the cloned repository
    code = (
        "import json, qudi.core, qudi.logic.pulsed.pulse_objects as p; "
        "print(json.dumps({'core': qudi.core.__file__, 'iqo': p.__file__}))"
    )
    result = run([config.python_bin, "-c", code])
    check(result.returncode == 0, "qudi-core and qudi-iqo-modules can be imported in the virtual environment")
    files = json.loads(result.stdout.strip().splitlines()[-1])
    if config.iqo_modules_dir:
        check(
            Path(files["iqo"]).resolve().is_relative_to(config.iqo_modules_dir.resolve()),
            "qudi-iqo-modules is imported from the cloned repository",
        )
    check(
        Path(files["core"]).resolve().is_relative_to(config.venv_dir.resolve()),
        "qudi-core is imported from the virtual environment",
    )

    result = run([config.python_bin, "-m", "jupyter", "kernelspec", "list", "--json"])
    kernels = json.loads(result.stdout)["kernelspecs"]
    check("qudi" in kernels, "qudi Jupyter kernel installed")
    kernel_python = Path(kernels["qudi"]["spec"]["argv"][0])
    check(
        os.path.normcase(kernel_python.resolve()) == os.path.normcase(config.python_bin.resolve()),
        "qudi Jupyter kernel uses the Python of the virtual environment",
    )


def check_qudi_starts(config, work_dir: Path, timeout: float = 180):
    log_dir = work_dir / "qudi_log"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "qudi.log"
    cmd = [str(config.python_bin), "-m", "qudi.core", "--no-gui", "--logdir", str(log_dir)]
    print(f"> {' '.join(cmd)}", flush=True)
    process = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=SUBPROCESS_ENV
    )
    try:
        started = False
        end = time.time() + timeout
        while time.time() < end and process.poll() is None:
            if log_file.exists() and "Initialization complete" in log_file.read_text(errors="replace"):
                started = True
                break
            time.sleep(1)
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
    if log_file.exists():
        print(log_file.read_text(errors="replace"), flush=True)
    check(started, "qudi starts")


def test_editable_installation(work_dir: Path, iqo_repo: str = "", iqo_branch: str = ""):
    install_dir = work_dir / "install"
    settings = {
        "install_dir": str(install_dir),
        "core_mode": "PyPi",
        "iqo_mode": "Editable",
        "iqo_branch": iqo_branch,
        "create_start_menu_shortcuts": True,
        "create_desktop_shortcuts": True,
    }
    if iqo_repo:
        settings["iqo_repo"] = iqo_repo
    settings_file = work_dir / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=4))
    download_dir = work_dir / "download"
    download_dir.mkdir()
    shutil.copy2(INSTALLER, download_dir / "install.py")

    section("Unattended editable installation")
    result = run(
        [sys.executable, download_dir / "install.py", "--config", settings_file],
        cwd=download_dir,
        log_file=work_dir / "install_editable.out",
    )
    check(result.returncode == 0, "installation succeeded")

    section("Check editable installation")
    config = installer.InstallationConfig.load(install_dir / "installation.json")
    check(config.iqo_modules_dir is not None and config.core_dir is None, "installation modes stored in installation.json")
    check_installation(config)
    clone = config.iqo_modules_dir
    check(git(clone, "remote", "get-url", "origin") == config.iqo_repo, f"cloned from {config.iqo_repo}")
    if iqo_branch:
        check(git(clone, "branch", "--show-current") == iqo_branch, f"branch {iqo_branch} checked out")
    check_qudi_starts(config, work_dir)

    section("Update of a repository behind its remote branch (fast-forward)")
    reset_behind_remote(clone)
    restore_tested_installer(config)
    result = run(
        [config.python_bin, config.installer_file, "--update"],
        cwd=install_dir,
        log_file=work_dir / "update_ff.out",
    )
    check(result.returncode == 0, "update succeeded")
    check(git(clone, "rev-parse", "HEAD") == git(clone, "rev-parse", "@{u}"), "cloned repository updated")
    check(list(config.log_dir.glob("update_*.log")), "update log written")

    section("Update with conflicting local changes (stash)")
    conflict_file = reset_behind_remote(clone)
    (clone / conflict_file).write_text("local change conflicting with the update\n", encoding="utf-8")
    restore_tested_installer(config)
    result = run(
        [config.python_bin, config.installer_file, "--update"],
        cwd=install_dir,
        stdin="1\n",
        log_file=work_dir / "update_conflict.out",
    )
    output = result.stdout + result.stderr
    check(result.returncode != 0, "update fails")
    check("caused conflicts" in output, f"conflict in {conflict_file} reported")
    check("pip install -e" not in output, "packages not installed with conflicts in the repository")
    check(git(clone, "stash", "list"), "local changes kept in the stash")


def test_interactive_pypi_installation(work_dir: Path):
    section("Interactive installation of the PyPI releases")
    install_dir = work_dir / "install_pypi"
    download_dir = work_dir / "download_pypi"
    download_dir.mkdir()
    shutil.copy2(INSTALLER, download_dir / "install.py")
    # no default installation, custom directory, default venv name, PyPI releases, no shortcuts
    answers = f"n\n3\n{install_dir}\n1\n3\nn\nn\n"
    result = run(
        [sys.executable, download_dir / "install.py"],
        cwd=download_dir,
        stdin=answers,
        log_file=work_dir / "install_pypi.out",
    )
    check(result.returncode == 0, "installation succeeded")

    section("Check PyPI installation")
    config = installer.InstallationConfig.load(install_dir / "installation.json")
    check(config.core_dir is None and config.iqo_modules_dir is None, "installation modes stored in installation.json")
    log = next(config.log_dir.glob("install_*.log")).read_text(encoding="utf-8")
    check("Enter 1, 2, 3 (default 1): 3" in log, "answers written to the installation log")
    check_installation(config)


def main():
    parser = argparse.ArgumentParser(description="Test the qudi installation script.")
    parser.add_argument("--work-dir", type=Path, help="directory for the test installations (default: temporary)")
    parser.add_argument("--iqo-repo", default="", help="qudi-iqo-modules repository to install (default: GitHub)")
    parser.add_argument(
        "--iqo-branch", default="", help="branch of qudi-iqo-modules to install (default: default branch)"
    )
    parser.add_argument(
        "--with-pypi", action="store_true", help="additionally test an interactive installation of the PyPI releases"
    )
    args = parser.parse_args()

    work_dir = (args.work_dir or Path(tempfile.mkdtemp(prefix="qudi_install_test_"))).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    if any(work_dir.iterdir()):
        raise SystemExit(f"Work directory {work_dir} is not empty")
    print(f"Testing {INSTALLER} in {work_dir}", flush=True)

    try:
        test_editable_installation(work_dir, args.iqo_repo, args.iqo_branch)
        if args.with_pypi:
            test_interactive_pypi_installation(work_dir)
    except TestFailed as e:
        section("Result")
        print(f"FAILED: {e}", flush=True)
        sys.exit(1)
    section("Result")
    print("All installation script tests passed", flush=True)


if __name__ == "__main__":
    main()
