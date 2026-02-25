import os
import json

def write_python_version_badge(versions, path='pages/python-version-badge.json'):
    badge = {
        "schemaVersion": 1,
        "label": "python",
        "message": " | ".join(sorted(versions, key=lambda v: tuple(map(int, v.split("."))))),
        "color": "blue"
    }

    os.makedirs(path.rsplit("/", 1)[0], exist_ok=True)

    with open(path, "w") as f:
        json.dump(badge, f, indent=2)


if __name__ == "__main__":
    versions = json.loads(os.environ["MATRIX"])
    write_python_version_badge(versions)


