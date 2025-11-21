#!/usr/bin/env python3
"""
Build a matrix of Python *minor* versions from pyproject.toml.

Output:
- stdout: JSON array like ["3.10", "3.11", "3.12", "3.13"]
- If $GITHUB_OUTPUT is set: writes 'matrix=<same JSON>' for workflows.

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
