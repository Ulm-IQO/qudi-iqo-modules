# -*- coding: utf-8 -*-

"""
This is a utility module for the workflows
It builds a matrix of Python *minor* versions from pyproject.toml.

Output:
- stdout: JSON array like ["3.10", "3.11", "3.12", "3.13"]
- If $GITHUB_OUTPUT is set: writes 'matrix=<same JSON>' for workflows.

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

import json
import os
from urllib.request import urlopen
from urllib.error import URLError

# tomllib (3.11+) / tomli fallback
try:
    import tomllib  # Python 3.11+
    def load_toml(path: str):
        with open(path, "rb") as f:
            return tomllib.load(f)
except ModuleNotFoundError:
    import tomli
    def load_toml(path: str):
        with open(path, "rb") as f:
            return tomli.load(f)

from packaging.version import Version
from packaging.specifiers import SpecifierSet

# Python minor versions support on Github Actions
MANIFEST_URL = "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"


def read_requires_python(pyproject_path: str) -> str:
    data = load_toml(pyproject_path)

    # PEP 621 (preferred)
    proj = data.get("project") or {}
    if "requires-python" in proj:
        return str(proj["requires-python"])

    raise SystemExit("requires-python not found in pyproject.toml")


def fetch_manifest(url: str = MANIFEST_URL) -> list[dict]:
    try:
        with urlopen(url, timeout=30) as r:
            return json.load(r)
    except URLError as e:
        raise SystemExit(f"Failed to fetch versions-manifest.json: {e}")


def select_minors(spec_text: str, manifest: list[dict]) -> list[str]:
    allow_prereleases = os.getenv("INCLUDE_PRERELEASES", "").lower() in {"1", "true", "yes"}
    spec = SpecifierSet(spec_text, prereleases=allow_prereleases)

    minors = set()
    for entry in manifest:
        v_str = entry.get("version")
        if not v_str:
            continue
        v = Version(v_str)
        if v in spec:
            minors.add(f"{v.major}.{v.minor}")

    ordered = sorted(minors, key=lambda s: tuple(map(int, s.split("."))))

    return ordered


def main():
    pyproject = os.getenv("PYPROJECT_FILE", "pyproject.toml")
    spec = read_requires_python(pyproject)
    manifest = fetch_manifest()
    minor_versions = select_minors(spec, manifest)

    if not minor_versions:
        raise SystemExit(f"No Python *minor* versions found that satisfy '{spec}'")

    out = json.dumps(minor_versions)
    print(out)

    gha = os.getenv("GITHUB_OUTPUT")
    if gha:
        with open(gha, "a", encoding="utf-8") as f:
            f.write(f"matrix={out}\n")


if __name__ == "__main__":
    main()
