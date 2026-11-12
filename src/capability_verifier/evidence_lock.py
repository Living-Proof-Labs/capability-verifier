#!/usr/bin/env python3
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
import json
import sys
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
    return {
        "format": ROBOT_LOCK_FORMAT,
        "scenario": lock["scenario"],
        "releases": lock["releases"],
        "world": lock["world"],
        "configurations": lock["configurations"],
        "plan": lock["plan"],
    }


def write_evidence_document(lock: dict, dest: Path) -> dict:
    """Write ``evidence_document`` to ``dest`` and return it."""
    document = evidence_document(lock)
    dest.write_text(json.dumps(document, indent=2) + "\n")
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, required=True, help="caller verification lock")
    parser.add_argument("--out", type=Path, required=True, help="robot verification.lock.json")
    parser.add_argument(
        "--copy-caller",
        type=Path,
        help="also copy the caller lock here for repin.py --lock",
    )
    args = parser.parse_args(argv)
    try:
        raw = args.lock.read_bytes()
        lock = json.loads(raw)
    except OSError as exc:
        raise SystemExit(f"{args.lock}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.lock}: not valid JSON ({exc})") from None
    write_evidence_document(lock, args.out)
    if args.copy_caller is not None:
        args.copy_caller.write_bytes(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
