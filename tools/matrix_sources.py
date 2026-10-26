# SPDX-License-Identifier: Apache-2.0
"""Fetch released matrix sources; verify commit and tracked bytes before execution."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".matrix-sources"


def contracts():
    return json.loads((ROOT / "reporting-specs.lock.json").read_text())


def executors():
    return json.loads((ROOT / "matrix.lock.json").read_text())["executors"]


def source_path(pin):
    return CACHE / pin["repository"].rsplit("/", 1)[1] / pin["tag"]


def verify_source(pin):
    path = source_path(pin)
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True)
    if actual.strip() != pin["commit"]:
        raise ValueError(f"Released commit mismatch: {pin['tag']}")
    subprocess.run(["git", "-C", str(path), "diff", "--exit-code", "HEAD", "--"], check=True)
    return path


def fetch():
    for pin in contracts() + executors():
        path = source_path(pin)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    pin["tag"],
                    pin["repository"] + ".git",
                    str(path),
                ],
                check=True,
            )
        verify_source(pin)


if __name__ == "__main__":
    fetch()
