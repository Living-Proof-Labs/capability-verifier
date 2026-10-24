# SPDX-License-Identifier: Apache-2.0
"""Fetch only the released catalog tag and verify its recorded commit and files."""

import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "specs.lock.json").read_text())
destination = root / ".specs"
if not destination.exists():
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            lock["tag"],
            lock["repository"] + ".git",
            str(destination),
        ],
        check=True,
    )
head = subprocess.check_output(
    ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
).strip()
if head != lock["commit"]:
    raise SystemExit("Catalog checkout does not match the locked released commit")
for name, expected in lock["files"].items():
    if hashlib.sha256((destination / name).read_bytes()).hexdigest() != expected:
        raise SystemExit(f"Catalog content differs from released evidence: {name}")
print(f"Verified catalog {lock['tag']} at {head}")
