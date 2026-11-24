# SPDX-License-Identifier: Apache-2.0
"""Resolved pin inputs for local replay and the evidence-job record.

The plan step decides with ``apply_resolved_pins.sh`` and real jq, then writes
the working lock. This module is the same decision for ``definition run``, which
has no jq, and the stamp the evidence and media jobs apply to a fresh checkout.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

COMMIT = re.compile(r"^[0-9a-f]{40}$")
SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
# apply_resolved_pins.sh loads these two strings. Do not copy them into the shell.
VERIFIER_IMAGE_PATTERN = (
    rf"^ghcr\.io/living-proof-labs/capability-verifier-hallway:{SEMVER}@sha256:[0-9a-f]{{64}}$"
)
REPORTER_IMAGE_PATTERN = (
    rf"^ghcr\.io/living-proof-labs/livingproof-capability-report:{SEMVER}@sha256:[0-9a-f]{{64}}$"
)
VERIFIER_IMAGE = re.compile(VERIFIER_IMAGE_PATTERN)
REPORTER_IMAGE = re.compile(REPORTER_IMAGE_PATTERN)

INPUTS = {
    "verifier-commit": "RESOLVED_VERIFIER_COMMIT",
    "verifier-image": "RESOLVED_VERIFIER_IMAGE",
    "reporter-image": "RESOLVED_REPORTER_IMAGE",
    "evaluator-commit": "RESOLVED_EVALUATOR_COMMIT",
}


class PinError(SystemExit):
    pass


def _present(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _tag_semver(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.removeprefix("v")


def _split_image(ref: str) -> tuple[str, str, str]:
    digest = ref.rsplit("@", 1)[1]
    rest = ref[: -(len(digest) + 1)]
    name, semver = rest.rsplit(":", 1)
    return name, semver, digest


def _release(lock: dict, name: str) -> dict:
    releases = lock.setdefault("releases", {})
    entry = releases.get(name)
    if not isinstance(entry, dict):
        entry = {}
        releases[name] = entry
    return entry


def _evaluator(lock: dict) -> dict:
    evaluators = lock.setdefault("evaluators", {})
    entry = evaluators.get("livingproof-evaluator-scenario")
    if not isinstance(entry, dict):
        entry = {}
        evaluators["livingproof-evaluator-scenario"] = entry
    return entry


def _require_tag(label: str, tag: object) -> str:
    if not _present(tag):
        raise PinError(f"{label}: lock has no tag")
    return str(tag)


def _check_commit(label: str, given: str, recorded: object, tag: object) -> str:
    if not COMMIT.fullmatch(given):
        raise PinError(f"{label} must be a 40-character lowercase hexadecimal SHA (got {given!r})")
    # livingproof-evaluator-scenario has no default. v1.5.0 belongs to
    # livingproof-evaluator-run, the run-integrity pin.
    shown = _require_tag(label, tag)
    if _present(recorded) and recorded != given:
        raise PinError(f"{label} {given} does not match the commit for tag {shown} ({recorded})")
    return shown


def _check_image(
    label: str, given: str, pattern: re.Pattern[str], release_name: str, entry: dict
) -> tuple[str, str, str]:
    if pattern.fullmatch(given) is None:
        requirement = (
            "ghcr.io/living-proof-labs/capability-verifier-hallway:<semver>@sha256:<64 hex>"
            if release_name == "verifier"
            else (
                "ghcr.io/living-proof-labs/livingproof-capability-report image, "
                "tag <semver>, digest sha256:<64 hex>"
            )
        )
        raise PinError(f"{label} must be {requirement} (got {given!r})")
    name, semver, digest = _split_image(given)
    tag = _require_tag(label, entry.get("tag"))
    if semver != _tag_semver(tag):
        raise PinError(f"{label} semver {semver} does not match releases.{release_name}.tag {tag}")
    recorded_name = entry.get("image")
    recorded_digest = entry.get("digest")
    if _present(recorded_name) and recorded_name != name:
        raise PinError(
            f"{label} {given} does not match releases.{release_name}.image "
            f"({recorded_name}); the verification lock is stale"
        )
    if _present(recorded_digest) and recorded_digest != digest:
        raise PinError(
            f"{label} {given} does not match releases.{release_name}.digest "
            f"({recorded_digest}); the verification lock is stale"
        )
    return name, semver, digest


def apply_resolved_pins(lock: dict, environ: dict[str, str] | None = None) -> dict:
    """Return a copy of ``lock`` with supplied pins checked and filled in.

    An empty input leaves that field unchanged. A non-empty component already
    in the lock must match the input. A missing or empty component is filled.
    """
    env = os.environ if environ is None else environ
    result = copy.deepcopy(lock)
    verifier_commit = env.get("RESOLVED_VERIFIER_COMMIT", "")
    verifier_image = env.get("RESOLVED_VERIFIER_IMAGE", "")
    reporter_image = env.get("RESOLVED_REPORTER_IMAGE", "")
    evaluator_commit = env.get("RESOLVED_EVALUATOR_COMMIT", "")
    if verifier_commit:
        entry = _release(result, "verifier")
        _check_commit("verifier-commit", verifier_commit, entry.get("commit"), entry.get("tag"))
        entry["commit"] = verifier_commit
    if verifier_image:
        entry = _release(result, "verifier")
        name, _semver, digest = _check_image(
            "verifier-image", verifier_image, VERIFIER_IMAGE, "verifier", entry
        )
        entry["image"] = name
        entry["digest"] = digest
    if reporter_image:
        entry = _release(result, "reporter")
        name, _semver, digest = _check_image(
            "reporter-image", reporter_image, REPORTER_IMAGE, "reporter", entry
        )
        entry["image"] = name
        entry["digest"] = digest
    if evaluator_commit:
        entry = _evaluator(result)
        _check_commit("evaluator-commit", evaluator_commit, entry.get("commit"), entry.get("tag"))
        entry["commit"] = evaluator_commit
    return result


def record_resolved_pins(lock: dict, environ: dict[str, str] | None = None) -> dict:
    """Stamp plan outputs onto a fresh evidence lock.

    ``*_SOURCE=input`` is a value the plan step already accepted. ``lock``
    leaves that field as the fresh checkout wrote it.
    """
    env = os.environ if environ is None else environ
    result = copy.deepcopy(lock)

    def source(name: str) -> str:
        return env.get(f"{name}_SOURCE", "")

    def value(name: str) -> str:
        return env.get(name, "")

    if source("VERIFIER_COMMIT") == "input" and value("VERIFIER_COMMIT"):
        _release(result, "verifier")["commit"] = value("VERIFIER_COMMIT")
    if source("VERIFIER_IMAGE") == "input" and value("VERIFIER_IMAGE"):
        entry = _release(result, "verifier")
        name, _semver, digest = _check_image(
            "verifier-image", value("VERIFIER_IMAGE"), VERIFIER_IMAGE, "verifier", entry
        )
        entry["image"] = name
        entry["digest"] = digest
    if source("REPORTER_IMAGE") == "input" and value("REPORTER_IMAGE"):
        entry = _release(result, "reporter")
        name, _semver, digest = _check_image(
            "reporter-image", value("REPORTER_IMAGE"), REPORTER_IMAGE, "reporter", entry
        )
        entry["image"] = name
        entry["digest"] = digest
    if source("EVALUATOR_COMMIT") == "input" and value("EVALUATOR_COMMIT"):
        _evaluator(result)["commit"] = value("EVALUATOR_COMMIT")
    return result


def _write(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n")


def stamp_record(path: Path, environ: dict[str, str] | None = None) -> dict:
    """Write plan outputs into ``path`` and a sibling caller lock, when present."""
    document = record_resolved_pins(json.loads(path.read_text()), environ)
    _write(path, document)
    caller = path.parent / "caller-verification.lock.json"
    if caller.is_file():
        _write(caller, record_resolved_pins(json.loads(caller.read_text()), environ))
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--record",
        type=Path,
        required=True,
        help="evidence lock to stamp with the plan outputs",
    )
    args = parser.parse_args(argv)
    try:
        stamp_record(args.record)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{args.record}: {exc}") from None
    return 0


if __name__ == "__main__":
    sys.exit(main())
