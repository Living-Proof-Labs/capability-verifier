# SPDX-License-Identifier: Apache-2.0
"""Write the evidence inputs livingproof-sim-mujoco v2.0.3 reads.

``evidence.py`` loads ``verification.lock.json`` from the robot root (format
``livingproof-verification-lock/1.0.0``) for releases, plan, world, scenario and
each configuration sha256. A ``livingproof-verification-lock/0.2.0`` owns those
fields. This module projects them into the document v2.0.3 already reads, so the
runner does not need a new release.

The robot file's ``$comment`` is not an evidence input. Every other field of the
locks pinned for the four current robots is copied through.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

FORMAT_V2 = "livingproof-verification-lock/0.2.0"
ROBOT_LOCK_FORMAT = "livingproof-verification-lock/1.0.0"
ROBOT_LOCK_NAME = "verification.lock.json"
CALLER_LOCK_NAME = "caller-verification.lock.json"
# Fields evidence.py reads. Order matches the robot files, without ``$comment``.
EVIDENCE_FIELDS = ("format", "scenario", "releases", "world", "configurations", "plan")


def evidence_document(lock: dict) -> dict:
    """The ``verification.lock.json`` object v2.0.3 ``evidence.py`` loads."""
    if not isinstance(lock, dict) or lock.get("format") != FORMAT_V2:
        found = lock.get("format") if isinstance(lock, dict) else type(lock).__name__
        raise SystemExit(
            "evidence inputs are written from a livingproof-verification-lock/0.2.0 "
            f"(got {found!r}). A 0.1.0 lock does not own plan, world or configuration sha256s."
        )
    missing = [key for key in EVIDENCE_FIELDS if key != "format" and key not in lock]
    if missing:
        raise SystemExit(f"verification lock is missing {', '.join(missing)}")
    document = {
        "format": ROBOT_LOCK_FORMAT,
        "scenario": lock["scenario"],
        "releases": lock["releases"],
        "world": lock["world"],
        "configurations": lock["configurations"],
        "plan": lock["plan"],
    }
    # Source-file bindings the simulator consumes (livingproof-verification#119).
    # Optional on locks that predate sources; copied through when the caller declares them.
    if "sources" in lock:
        document["sources"] = lock["sources"]
    return document


FILE_SHA = re.compile(r"^[0-9a-f]{64}$")


def _relative_robot_path(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    return not Path(path).is_absolute() and ".." not in Path(path).parts


def _source_entry(lock: dict, kind: str) -> dict | None:
    sources = lock.get("sources")
    if not isinstance(sources, dict) or kind not in sources:
        return None
    entry = sources[kind]
    if not isinstance(entry, dict):
        raise SystemExit(f"sources.{kind} must be an object with path and sha256")
    if not _relative_robot_path(entry.get("path")):
        raise SystemExit(
            f"sources.{kind} path must stay inside the robot repository, got {entry.get('path')!r}"
        )
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not FILE_SHA.fullmatch(digest):
        raise SystemExit(f"sources.{kind} sha256 must be 64 hex characters, got {digest!r}")
    return entry


def _upstream(lock: dict, kind: str) -> tuple[str, str, str]:
    """Repository, commit and path of the released file that ``sources.{kind}`` names."""
    if kind == "world":
        world = lock["world"]
        return (
            world["repository"],
            world["commit"],
            f"worlds/{world['id']}/{world['version']}/world.json",
        )
    specs = lock["releases"]["specs"]
    scenario = str(lock["scenario"]).split("@", 1)[0]
    return (
        specs["repository"],
        specs["commit"],
        f"specs/scenarios/{scenario}/parameters.json",
    )


def _fetch_source_file(repository: str, commit: str, path: str, work: Path) -> bytes:
    from capability_verifier.definition import show

    return show(repository, commit, path, work)


def place_source_files(lock: dict, robot: Path, work: Path, *, fetch=None) -> None:
    """Write ``sources.world`` and ``sources.parameters`` into ``robot``, verifying sha256.

    Locks that omit ``sources`` are left unchanged. Bytes come from the locked
    world or specs release, then are placed at the binding's path in the robot root.
    """
    get = fetch or _fetch_source_file
    robot = robot.resolve()
    for kind in ("world", "parameters"):
        entry = _source_entry(lock, kind)
        if entry is None:
            continue
        repository, commit, upstream = _upstream(lock, kind)
        raw = get(repository, commit, upstream, work)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry["sha256"]:
            raise SystemExit(
                f"sources.{kind} sha256 mismatch for {entry['path']}: "
                f"expected {entry['sha256']}, got {digest}"
            )
        dest = (robot / entry["path"]).resolve()
        if not dest.is_relative_to(robot):
            raise SystemExit(
                f"sources.{kind} path must stay inside the robot repository, got {entry['path']!r}"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)


def write_evidence_document(lock: dict, dest: Path) -> dict:
    """Write ``evidence_document`` to ``dest`` and return it."""
    document = evidence_document(lock)
    dest.write_text(json.dumps(document, indent=2) + "\n")
    return document


def evidence_source_lines(digest: str | None) -> list[str]:
    """Log lines when the caller lock is the source.

    ``digest`` is the sha256 of the robot ``verification.lock.json`` this run
    replaced. ``None`` means the robot commit has no such file.
    """
    if digest is None:
        detail = "robot commit has no verification.lock.json"
    else:
        detail = f"overridden robot verification.lock.json sha256: {digest}"
    return ["EVIDENCE_SOURCE: caller", detail]


def note_overridden_robot_lock(dest: Path, digest: str) -> None:
    """Record the robot lock ``dest`` replaced.

    livingproof-sim-mujoco v2.0.3 copies the whole ``verification.lock.json`` into
    the evidence manifest's ``lock.content``, so this digest is part of the
    evidence record. ``digest`` is the sha256 of the file before replacement.
    """
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(f"overridden robot lock sha256 must be 64 hex characters, got {digest!r}")
    document = json.loads(dest.read_text())
    document["overridden_robot_lock"] = {"path": ROBOT_LOCK_NAME, "sha256": digest}
    dest.write_text(json.dumps(document, indent=2) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, required=True, help="caller verification lock")
    parser.add_argument("--out", type=Path, required=True, help="robot verification.lock.json")
    parser.add_argument(
        "--copy-caller",
        type=Path,
        help="also copy the caller lock here for repin.py --lock",
    )
    parser.add_argument(
        "--overridden-sha256",
        help="sha256 of the robot verification.lock.json this write replaces",
    )
    parser.add_argument("--robot", type=Path, help="robot root that receives source files")
    parser.add_argument("--work", type=Path, help="checkout directory used to fetch source files")
    args = parser.parse_args(argv)
    try:
        raw = args.lock.read_bytes()
        lock = json.loads(raw)
    except OSError as exc:
        raise SystemExit(f"{args.lock}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.lock}: not valid JSON ({exc})") from None
    write_evidence_document(lock, args.out)
    if args.overridden_sha256:
        note_overridden_robot_lock(args.out, args.overridden_sha256)
    if args.copy_caller is not None:
        args.copy_caller.write_bytes(raw)
    if args.robot is not None:
        work = args.work or Path(tempfile.mkdtemp(prefix="source-files-"))
        place_source_files(lock, args.robot, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
