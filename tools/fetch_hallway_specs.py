# SPDX-License-Identifier: Apache-2.0
"""Fetch the walk-hallway catalog at its locked tag and verify commit and files.

A released lock has ``"tag": "vX.Y.Z"`` and is cloned from the repository. Until the
catalog is released the lock says ``"tag": "UNRELEASED"``; then the locked commit is
taken from a local checkout named by ``HALLWAY_SPECS_SOURCE`` and must still match the
locked commit and file hashes. Releasing changes only the "tag" line (and the "commit"
line if the release commit differs).
"""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "hallway-specs.lock.json").read_text())
destination = root / ".specs-hallway"
released = re.fullmatch(r"v\d+\.\d+\.\d+", lock["tag"]) is not None
if not destination.exists():
    if released:
        source = lock["repository"] + ".git"
        args = ["git", "clone", "--depth", "1", "--branch", lock["tag"], source]
    else:
        source = os.environ.get("HALLWAY_SPECS_SOURCE")
        if not source:
            raise SystemExit(
                "Unreleased hallway lock: set HALLWAY_SPECS_SOURCE to a local checkout"
            )
        args = ["git", "clone", "--no-checkout", source]
    subprocess.run([*args, str(destination)], check=True)
    if not released:
        subprocess.run(
            ["git", "-C", str(destination), "checkout", "-q", lock["commit"]], check=True
        )
head = subprocess.check_output(
    ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
).strip()
if head != lock["commit"]:
    raise SystemExit("Catalog checkout does not match the locked commit")
for name, expected in lock["files"].items():
    if hashlib.sha256((destination / name).read_bytes()).hexdigest() != expected:
        raise SystemExit(f"Catalog content differs from the lock: {name}")
label = lock["tag"] if released else f"UNRELEASED {head[:7]} (prototype)"
print(f"Verified walk-hallway catalog {label} at {head}")
