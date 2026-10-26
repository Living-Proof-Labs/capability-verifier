# SPDX-License-Identifier: Apache-2.0
"""Run four released execution coordinates without merging their capability grades."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from matrix_sources import ROOT, contracts, executors, verify_source

KINDS = {"pause-rules": "deterministic", "mock-ai": "mock-agent", "human-fixture": "human"}


def record(contract, executor, fixture, destination, run_id, control=False):
    verify_source(contract)
    source = verify_source(executor)
    observation = source / "fixtures" / f"{fixture}.json"
    relative = f"evidence/{executor['tag']}/{fixture}.json"
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(observation, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    compatible = contract["revision"] in executor["revisions"]
    compatibility = {
        "status": "COMPATIBLE" if compatible else "INCOMPATIBLE",
        "scope": "execution",
        "reason": "Released executor implements selected revision"
        if compatible
        else "Released executor supports pause revision 1.0.0 only",
    }
    original = {"status": "INCOMPATIBLE", "assessments": []}
    if compatible:
        original = json.loads(
            subprocess.check_output(
                [
                    sys.executable,
                    str(ROOT / "tools/matrix_execute.py"),
                    str(source),
                    json.dumps(contract),
                    fixture,
                    executor["tag"],
                ],
                text=True,
                env={**os.environ, "SOURCE_COMMIT": executor["commit"]},
            )
        )
    fixture_version = executor["fixture_version"] if fixture == "slow" else "1.0.0"
    return {
        "id": f"{contract['tag']}-{executor['tag']}-{fixture}",
        "spec": {**contract, "capability": "pause"},
        "system": {"id": "pause-fixtures", "version": fixture_version},
        "environment": {"id": "deterministic-fixture-no-robot", "version": "1.0.0"},
        "evidence": [{"id": "observation", "path": relative, "sha256": digest}],
        "assessments": [
            {
                **a,
                "id": a["evaluator"],
                "kind": KINDS[a["evaluator"]],
                "evidence_ids": ["observation"],
                "provenance": {
                    "source": "released recorded fixture; no live review",
                    "mock": a["evaluator"] == "mock-ai",
                },
            }
            for a in original["assessments"]
        ],
        "provenance": {
            "run_id": run_id,
            "role": "control" if control else "matrix",
            "fixture": {
                "id": "pause-fixtures",
                "version": fixture_version,
                **{k: executor[k] for k in ("repository", "tag", "commit")},
                "path": f"fixtures/{fixture}.json",
            },
            "executor": {
                "id": "capability-verifier",
                "version": executor["tag"][1:],
                **{k: executor[k] for k in ("repository", "tag", "commit")},
            },
            "runtime": {"id": "CPython", "version": platform.python_version()},
            "compatibility": compatibility,
            "evidence_state": {"observation": "verified"},
            "simulator": None,
        },
        "original_result": original,
    }


def export(destination, run_id=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    run_id = run_id or str(uuid.uuid4())
    results = [
        record(c, e, e["fixture"], destination, run_id) for c in contracts() for e in executors()
    ]
    controls = [
        record(c, executors()[1], fixture, destination, run_id, control=True)
        for c in contracts()
        for fixture in ("fail", "insufficient")
    ]
    commit = (
        os.environ.get("SOURCE_COMMIT")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    )
    bundle = {
        "schema_version": "1.0.0",
        "producer": {"id": "capability-verifier-matrix", "version": "1.0.0", "commit": commit},
        "provenance": {
            "preview": True,
            "note": "Unreleased matrix adapter; sources use released tags",
        },
        "results": results,
    }
    (destination / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")
    (destination / "controls.json").write_text(
        json.dumps({**bundle, "results": controls}, indent=2) + "\n"
    )
    rows = [
        "| Spec | Executor | Fixture | Compatibility | Capability outcome |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results + controls:
        rows.append(
            f"| {r['spec']['tag']} | {r['provenance']['executor']['tag']} | "
            f"{r['provenance']['fixture']['path']} | "
            f"{r['provenance']['compatibility']['status']} | "
            f"{r['original_result']['status']} |"
        )
    (destination / "summary.md").write_text("\n".join(rows) + "\n")
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    export(args.destination, args.run_id)
