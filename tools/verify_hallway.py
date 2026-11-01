# SPDX-License-Identifier: Apache-2.0
"""Assess hallway evidence and export a reporter bundle (schema 1.0.0).

    python tools/verify_hallway.py <evidence>/manifest.json <out> [--prototype]

Writes ``<out>/verification.json`` (per-trial assessments and the k/N summary) and
``<out>/bundle/`` (``manifest.json`` plus ``evidence/``) for
livingproof-capability-report. The bundle schema accepts only released catalog tags;
with an UNRELEASED hallway lock the export refuses unless ``--prototype`` is given,
which writes the placeholder tag ``v0.0.0`` and says so in every result's provenance.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from capability_verifier import hallway

ROOT = Path(__file__).resolve().parents[1]


def export(manifest_path: Path, out: Path, prototype: bool = False) -> dict:
    manifest_path = Path(manifest_path)
    out.mkdir(parents=True, exist_ok=False)
    lock = json.loads((ROOT / "hallway-specs.lock.json").read_text())
    released = re.fullmatch(r"v\d+\.\d+\.\d+", lock["tag"]) is not None
    if not released and not prototype:
        raise SystemExit("hallway lock is UNRELEASED; pass --prototype to export a labelled bundle")
    evidence = json.loads(manifest_path.read_text())
    result = hallway.verify(manifest_path)
    (out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    bundle_dir = out / "bundle"
    (bundle_dir / "evidence").mkdir(parents=True)
    note = (
        None
        if released
        else (
            f"PROTOTYPE: catalog unreleased at {lock['commit']}; tag v0.0.0 is a placeholder, not a release"
        )
    )
    spec = {
        "repository": lock["repository"],
        "tag": lock["tag"] if released else "v0.0.0",
        "commit": lock["commit"],
        "capability": lock["capability"],
        "revision": lock["revision"],
    }
    conf = evidence["configuration"]
    results = []
    for t in result["trials"]:
        name = Path(t["evidence"]["path"]).name
        shutil.copyfile(
            manifest_path.parent / t["evidence"]["path"], bundle_dir / "evidence" / name
        )
        assessments = []
        for a in t["assessments"]:
            kind = hallway.REPORTER_KIND[a["assessor"]["type"]]
            if kind is None:
                raise SystemExit("agent assessments have no reporter bundle kind yet")
            assessments.append(
                {
                    "id": a["id"],
                    "evaluator": a["evaluator"],
                    "kind": kind,
                    "version": a["version"],
                    "status": a["status"],
                    "rationale": a["rationale"],
                    "mandatory": a["mandatory"],
                    "evidence_ids": a["evidence_ids"],
                    "provenance": {"assessor": a["assessor"], "details": a["details"]},
                }
            )
        provenance = {
            "run_id": evidence.get("run_id"),
            "plan": result["plan"],
            "thresholds": result["thresholds"],
            "policy": result["policy"],
            "lock": evidence.get("lock"),
            "simulator": evidence.get("environment"),
            "summary": result["summary"],
        }
        if note:
            provenance["prototype"] = note
        results.append(
            {
                "id": t["id"],
                "spec": spec,
                "system": {
                    "id": f"{evidence['robot']['id']}/{conf['name']}",
                    "version": conf["version"],
                },
                "environment": {
                    "id": evidence["world"]["id"],
                    "version": evidence["world"]["version"],
                },
                "evidence": [
                    {
                        "id": t["evidence"]["id"],
                        "path": f"evidence/{name}",
                        "sha256": t["evidence"]["sha256"],
                    }
                ],
                "assessments": assessments,
                "provenance": provenance,
                "original_result": {"status": t["status"], "seed": t["seed"]},
            }
        )
    commit = (
        os.environ.get("SOURCE_COMMIT")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    )
    described = subprocess.check_output(
        ["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT, text=True
    ).strip()
    # The schema takes X.Y.Z only: the nearest release tag; 'commit' names the exact head.
    bundle = {
        "schema_version": "1.0.0",
        "producer": {
            "id": "capability-verifier",
            "version": described.removeprefix("v"),
            "commit": commit,
        },
        "results": results,
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("manifest", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--prototype", action="store_true")
    a = p.parse_args()
    r = export(a.manifest, a.out, a.prototype)
    print("\n".join(hallway.summary_lines(r)))
