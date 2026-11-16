# SPDX-License-Identifier: Apache-2.0
"""Fetch the scenario catalog at its locked tag and verify the tag and files.

The reference is the tag, not the commit. The commit is recorded as evidence of
what the tag resolved to. File hashes still have to match. A released lock has
``"tag": "vX.Y.Z"`` and is cloned from the repository. Until the catalog is
released the lock says ``"tag": "UNRELEASED"``; then a local checkout named by
``HALLWAY_SPECS_SOURCE`` supplies the files and must match the file hashes.

The livingproof-worlds release in ``worlds.lock.json`` is fetched the same way, into
``.worlds``: a scenario graded on its world (walk-corner) reads ``world.json`` from it.
Its tag must resolve to the locked commit and its files must match the lock.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from capability_verifier.specs_ref import SPECS_TAG, catalog_reference_ok

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
if released:
    checked_out = subprocess.check_output(
        ["git", "-C", str(destination), "describe", "--tags", "--exact-match"], text=True
    ).strip()
    if lock["tag"] != SPECS_TAG or not catalog_reference_ok(
        checked_out, lock["tag"], head, lock.get("commit", "")
    ):
        raise SystemExit(f"Catalog checkout is tag {checked_out}, not {SPECS_TAG}")
for name, expected in lock["files"].items():
    if hashlib.sha256((destination / name).read_bytes()).hexdigest() != expected:
        raise SystemExit(f"Catalog content differs from the lock: {name}")
label = lock["tag"] if released else f"UNRELEASED {head[:7]} (prototype)"
print(f"Verified scenario catalog {label} at {head}")

worlds = json.loads((root / "worlds.lock.json").read_text())
worlds_dir = root / ".worlds"
if not worlds_dir.exists():
    subprocess.run(
        [
            "git",
            "-c",
            "advice.detachedHead=false",
            "clone",
            "-q",
            "--depth",
            "1",
            "--branch",
            worlds["tag"],
            worlds["repository"] + ".git",
            str(worlds_dir),
        ],
        check=True,
    )
worlds_head = subprocess.check_output(
    ["git", "-C", str(worlds_dir), "rev-parse", "HEAD"], text=True
).strip()
if worlds_head != worlds["commit"]:
    raise SystemExit(f"Worlds checkout is {worlds_head}, not {worlds['tag']} {worlds['commit']}")
for name, expected in worlds["files"].items():
    if hashlib.sha256((worlds_dir / name).read_bytes()).hexdigest() != expected:
        raise SystemExit(f"Worlds content differs from the lock: {name}")
print(f"Verified worlds {worlds['tag']} at {worlds_head}")
