#!/usr/bin/env python3
import os
import sys
import argparse
import requests
from packaging import version as pkg_version
from packaging.specifiers import SpecifierSet
from packaging.requirements import Requirement

sys.path.append('.')
from workflow_utils import get_pinned_packages


def parse_args() -> tuple[str, str]:
    """
    Returns (full_version_str, minor_str). Accepts '3.10' or '3.10.14'.
    """
    p = argparse.ArgumentParser(description="Check for dependency updates for a given Python version.")
    p.add_argument(
        "-v", "--python", "--py",
        dest="python_version",
        required=True,
        help="Target Python version (e.g., 3.10 or 3.10.14).",
    )
    args = p.parse_args()
    try:
        v = pkg_version.Version(args.python_version)
    except Exception as e:
        raise SystemExit(f"Invalid Python version '{args.python_version}': {e}")
    return str(v), f"{v.major}.{v.minor}"


def get_compatible_latest_version(package_name: str, project_python_version: str) -> str :
    """
    Fetches the latest compatible version of a package from PyPI,
    respecting the package's requires_python spec, if any.
    """
    resp = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=30)
    resp.raise_for_status()
    data = resp.json()

    latest = data["info"]["version"]
    requires_python = data["info"].get("requires_python")

    if not requires_python:
        return latest

    spec = SpecifierSet(requires_python)
    return latest if spec.contains(project_python_version) else None


def get_constrained_dependencies() -> set[str]:
    """Collect package names that are explicitly constrained (to skip suggesting updates)."""
    core_pyproject_toml = 'workflow_utils/pyproject_core.toml'
    iqo_pyproject_toml  = 'workflow_utils/pyproject_iqo.toml'

    core_constraints = get_pinned_packages.get_constrained_dependencies(core_pyproject_toml)
    iqo_constraints  = get_pinned_packages.get_constrained_dependencies(iqo_pyproject_toml)

    names: set[str] = set()
    for constraints in (core_constraints, iqo_constraints):
        for constraint in constraints:
            req = Requirement(constraint)
            names.add(req.name)
    return names


def main() -> int:
    full_py, minor_py = parse_args()
    constrained = get_constrained_dependencies()

    reqs_path = f"workflow_utils/reqs_{minor_py}.txt"
    if not os.path.exists(reqs_path):
        raise SystemExit(f"Requirements file not found: {reqs_path}")

    updates_available = False

    with open(reqs_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue

            pkg_name, current_version = line.split("==", 1)
            pkg_name = pkg_name.strip()
            if pkg_name in constrained:
                continue

            current_version = current_version.split("#")[0].strip()

            latest = get_compatible_latest_version(pkg_name, full_py)
            if latest and pkg_version.parse(latest) > pkg_version.parse(current_version):
                print(f"Update available: {pkg_name} ({current_version} -> {latest})")
                updates_available = True

    # Expose decision to later jobs (don’t crash if not on GHA)
    gha_out = os.environ.get("GITHUB_OUTPUT")
    if gha_out:
        with open(gha_out, "a", encoding="utf-8") as f:
            f.write(f"updates-available={'true' if updates_available else 'false'}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
