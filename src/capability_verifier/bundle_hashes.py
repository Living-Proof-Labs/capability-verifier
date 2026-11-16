# SPDX-License-Identifier: Apache-2.0
"""Every sha256 a run bundle declares resolves (capability-verifier#86).

An evidence folder's hashes are resolved by the pinned livingproof-sim-mujoco
``evidence.unresolved_hashes`` (v3.2.5, sim#74): artifact hashes against the file at the
sibling ``path``, ``trajectory_sha256`` recomputed from the trial file with
``evidence.trajectory_sha256_of`` (the observation's ``trace`` as compact, key-sorted
JSON), and pinned source inputs by name. No formula is copied here.

The sim catalog does not document three fields that released bundles carry. This module
documents them, and nothing else is skipped:

- ``lock.content.definition.sha256``: ``source``. The verification-lock 0.2.0 digest of
  the robot's definition file (``definition.lock``), which is not in the evidence folder.
- ``lock.content.overridden_robot_lock.sha256``: ``source``. The robot
  ``verification.lock.json`` a caller lock replaced (``evidence_lock``).
- ``trials.reproducibility.finding.trajectory_sha256``: ``copy``. The sim's disagreement
  finding lists each trial's ``trajectory_sha256`` in result order, so it resolves when it
  equals ``trials.results[].trajectory_sha256`` (each of which resolves by recompute).

``unresolved_report`` does the same for the verifier's ``verification.json``: a hash with
a sibling ``path`` is the evidence file at that path, and a repeat row (``index``) copies
the evidence result with that index.
"""

import hashlib
import json
from pathlib import Path

from livingproof_sim_mujoco import evidence

VERIFIER_DECLARED_HASHES = {
    "manifest.json": {
        "lock.content.definition.sha256": "source",
        "lock.content.overridden_robot_lock.sha256": "source",
        "trials.reproducibility.finding.trajectory_sha256": "copy",
    },
}


def _hash_fields(node, key: str = ""):
    """``(key, value, parent)`` for every field whose name contains ``sha256``."""
    if isinstance(node, dict):
        for name, value in node.items():
            here = f"{key}.{name}" if key else name
            if "sha256" in name:
                yield here, value, node
            yield from _hash_fields(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _hash_fields(value, f"{key}[{index}]")


def _results(folder: Path) -> list:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    trials = manifest.get("trials") if isinstance(manifest, dict) else None
    results = trials.get("results") if isinstance(trials, dict) else None
    return results if isinstance(results, list) else []


def unresolved(folder: Path) -> list[str]:
    """One message per hash in evidence ``folder`` that does not resolve. Empty when all do."""
    folder = Path(folder)
    verifier = {}
    for item in evidence.declared_hashes(folder):
        if item["covers"] is None:
            covers = VERIFIER_DECLARED_HASHES.get(item["file"], {}).get(item["key"])
            if covers is not None:
                verifier[f"{item['file']} {item['key']}"] = (covers, item)
    problems = [
        message
        for message in evidence.unresolved_hashes(folder)
        if not any(message.startswith(f"{where} ") for where in verifier)
    ]
    for where, (covers, item) in verifier.items():
        if covers != "copy":
            continue
        trial_hashes = [r.get("trajectory_sha256") for r in _results(folder)]
        if item["sha256"] != trial_hashes:
            problems.append(
                f"{where} is {item['sha256']!r} but trials.results[].trajectory_sha256 "
                f"is {trial_hashes!r}."
            )
    return problems


def report_hashes(report: Path) -> list[tuple[str, object, dict]]:
    """``(key, value, parent)`` for every sha256 field in a ``verification.json``."""
    return list(_hash_fields(json.loads(Path(report).read_text(encoding="utf-8"))))


def unresolved_report(report: Path, folder: Path) -> list[str]:
    """Hashes in ``report`` that are neither an evidence file in ``folder`` nor a copy."""
    report, folder = Path(report), Path(folder)
    by_index = {r.get("index"): r for r in _results(folder) if isinstance(r, dict)}
    problems = []
    for key, value, parent in report_hashes(report):
        where = f"{report.name} {key}"
        name = key.rsplit(".", 1)[-1]
        rel = parent.get("path")
        if isinstance(rel, str):
            target = folder / rel
            if not target.is_file():
                problems.append(f"{where}: its path {rel!r} is not a file in the evidence.")
                continue
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != value:
                problems.append(f"{where} is {value} but the file {rel} gives {actual}.")
        elif "index" in parent:
            source = by_index.get(parent["index"])
            if source is None or source.get(name) != value:
                got = None if source is None else source.get(name)
                problems.append(
                    f"{where} is {value} but evidence result {parent['index']} has {got}."
                )
        else:
            problems.append(f"{where} names no evidence file and copies no evidence result.")
    return problems
