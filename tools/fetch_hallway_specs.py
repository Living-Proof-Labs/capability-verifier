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
lock declared, not hallway-specs.lock.json or worlds.lock.json. Every file in the
lock's ``releases.specs.files`` must match its declared sha256, or the command fails.

With ``--record PATH`` (only with ``--from``), it then writes
``livingproof-specs-files/0.1`` to PATH: the run id, the specs repository and tag, the
commit the checkout resolved to, and the sha256 of each declared file as read from the
checkout. The digests are of the bytes read here, not the lock's values. A failed run
writes no record and removes one left at PATH by an earlier run.
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


SPECS_RECORD_FORMAT = "livingproof-specs-files/0.1"


def _verify_specs_files(destination: Path, declared: object) -> dict[str, str]:
    """The sha256 of each declared spec file as read from ``destination``.

    Fails on a missing file, a path that leaves the checkout, or a digest that differs from
    the declared one.
    """
    files = declared if isinstance(declared, dict) else {}
    read: dict[str, str] = {}
    base = destination.resolve()
    for name, expected in files.items():
        path = (base / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(base):
            raise SystemExit(f"Specs file path must stay inside the specs checkout: {name}")
        if not path.is_file():
            raise SystemExit(f"Specs file is not in the checkout: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Specs file differs from the lock: {name}")
        read[name] = actual
    return read


def fetch_from_manifest(
    manifest_path: Path,
    record: Path | None = None,
    specs_dir: Path | None = None,
    worlds_dir: Path | None = None,
) -> None:
    if record is not None:
        # A record from an earlier run must not outlive a failure of this one.
        Path(record).unlink(missing_ok=True)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    content = _lock_content(manifest)
    releases = content.get("releases") if isinstance(content.get("releases"), dict) else {}
    specs = releases.get("specs") if isinstance(releases.get("specs"), dict) else {}
    if not specs.get("repository") or not specs.get("commit"):
        raise SystemExit("manifest does not declare a specs repository and commit")
    destination = Path(specs_dir) if specs_dir is not None else root / ".specs-hallway"
    head = _clone_commit(_repo_git(specs["repository"]), specs["commit"], destination)
    if head != specs["commit"]:
        raise SystemExit(f"Specs checkout is {head}, not declared {specs['commit']}")
    label = specs.get("tag") or head[:7]
    print(f"Fetched declared specs {label} at {head}")
    read = _verify_specs_files(destination, specs.get("files"))
    if read:
        print(f"Verified {len(read)} declared specs files")
    world = content.get("world") if isinstance(content.get("world"), dict) else {}
    worlds_dir = Path(worlds_dir) if worlds_dir is not None else root / ".worlds"
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
    if record is not None:
        document = {
            "format": SPECS_RECORD_FORMAT,
            "run_id": manifest.get("run_id"),
            "repository": specs["repository"],
            "tag": specs.get("tag"),
            "commit": head,
            "files": read,
        }
        Path(record).parent.mkdir(parents=True, exist_ok=True)
        Path(record).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote specs record {record}")


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


def main(argv: list[str]) -> None:
    if len(argv) == 2 and argv[0] == "--from":
        fetch_from_manifest(Path(argv[1]))
    elif len(argv) == 4 and argv[0] == "--from" and argv[2] == "--record":
        fetch_from_manifest(Path(argv[1]), record=Path(argv[3]))
    elif not argv:
        fetch_from_verifier_locks()
    else:
        raise SystemExit("usage: fetch_hallway_specs.py [--from MANIFEST [--record PATH]]")


if __name__ == "__main__":
    main(sys.argv[1:])
