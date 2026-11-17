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

With ``--from MANIFEST``, the checkout is the specs commit and world the evidence
lock declared, not hallway-specs.lock.json or worlds.lock.json.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from capability_verifier.specs_ref import SPECS_TAG, catalog_reference_ok


def _repo_git(name: str) -> str:
    if name.startswith("git@") or name.endswith(".git"):
        return name
    if name.startswith(("https://", "http://")):
        return name if name.endswith(".git") else name + ".git"
    return f"https://github.com/{name}.git"


def _clone_commit(url: str, commit: str, dest: Path) -> str:
    if dest.exists():
        try:
            head = subprocess.check_output(
                ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            head = ""
        if head == commit:
            return head
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "fetch", "-q", "--depth", "1", url, commit],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(dest),
            "-c",
            "advice.detachedHead=false",
            "checkout",
            "-q",
            "FETCH_HEAD",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()


def _lock_content(manifest: dict) -> dict:
    lock = manifest.get("lock") if isinstance(manifest.get("lock"), dict) else {}
    content = lock.get("content") if isinstance(lock.get("content"), dict) else {}
    return content if isinstance(content, dict) else {}


def fetch_from_manifest(manifest_path: Path) -> None:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    content = _lock_content(manifest)
    releases = content.get("releases") if isinstance(content.get("releases"), dict) else {}
    specs = releases.get("specs") if isinstance(releases.get("specs"), dict) else {}
    if not specs.get("repository") or not specs.get("commit"):
        raise SystemExit("manifest does not declare a specs repository and commit")
    destination = root / ".specs-hallway"
    head = _clone_commit(_repo_git(specs["repository"]), specs["commit"], destination)
    if head != specs["commit"]:
        raise SystemExit(f"Specs checkout is {head}, not declared {specs['commit']}")
    label = specs.get("tag") or head[:7]
    print(f"Fetched declared specs {label} at {head}")
    world = content.get("world") if isinstance(content.get("world"), dict) else {}
    worlds_dir = root / ".worlds"
    if world.get("repository") and world.get("commit"):
        worlds_head = _clone_commit(_repo_git(world["repository"]), world["commit"], worlds_dir)
        if worlds_head != world["commit"]:
            raise SystemExit(f"Worlds checkout is {worlds_head}, not declared {world['commit']}")
        print(f"Fetched declared world {world.get('tag', '')} at {worlds_head}")
    world_path = None
    if world.get("id") and world.get("version"):
        world_path = worlds_dir / "worlds" / str(world["id"]) / str(world["version"]) / "world.json"
    digest = world.get("sha256")
    if isinstance(digest, str) and digest:
        if world_path is None or not world_path.is_file():
            raise SystemExit("declared world file is not in the checkout")
        actual = hashlib.sha256(world_path.read_bytes()).hexdigest()
        if actual != digest:
            raise SystemExit("declared world digest does not match the checkout")
        print(f"Verified declared world digest {digest}")


def fetch_from_verifier_locks() -> None:
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
        raise SystemExit(
            f"Worlds checkout is {worlds_head}, not {worlds['tag']} {worlds['commit']}"
        )
    for name, expected in worlds["files"].items():
        if hashlib.sha256((worlds_dir / name).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Worlds content differs from the lock: {name}")
    print(f"Verified worlds {worlds['tag']} at {worlds_head}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--from":
        fetch_from_manifest(Path(sys.argv[2]))
    elif len(sys.argv) == 1:
        fetch_from_verifier_locks()
    else:
        raise SystemExit("usage: fetch_hallway_specs.py [--from MANIFEST]")
