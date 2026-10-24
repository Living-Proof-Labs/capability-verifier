# SPDX-License-Identifier: Apache-2.0
"""Export portable evidence without a dependency on the reporting package."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from capability_verifier.core import evaluate_pause, verify

ROOT = Path(__file__).resolve().parents[1]


def export(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "evidence").mkdir()
    contracts = json.loads((ROOT / "reporting-specs.lock.json").read_text())
    results = []
    for contract in contracts:
        for name in ("pass", "fail", "insufficient", "slow"):
            source = ROOT / "fixtures" / f"{name}.json"
            target = destination / "evidence" / source.name
            shutil.copyfile(source, target)
            original = verify(source, contract)
            original["provenance"]["spec"]["revision"] = contract["revision"]
            if contract["revision"] == "2.0.0":
                evidence = json.loads(source.read_text())
                deterministic = evaluate_pause(evidence)
                if deterministic["status"] == "PASS" and evidence["elapsed_ms"] > 250:
                    deterministic["status"] = "FAIL"
                deterministic["rationale"] += "; strict revision uses 250 ms inclusive limit"
                deterministic["version"] = "1.1.0"
                original["assessments"][0] = deterministic
                from capability_verifier.core import grade

                original["status"] = grade(original["assessments"])
            system_version = "2.0.0" if name == "slow" else "1.0.0"
            original["provenance"]["system"]["version"] = system_version
            kinds = {
                "pause-rules": "deterministic",
                "mock-ai": "mock-agent",
                "human-fixture": "human",
            }
            results.append(
                {
                    "id": f"{contract['tag']}-{name}",
                    "spec": {**contract, "capability": "pause"},
                    "system": {"id": "pause-fixtures", "version": system_version},
                    "environment": {"id": "deterministic-fixture-no-robot", "version": "1.0.0"},
                    "evidence": [
                        {
                            "id": "observation",
                            "path": f"evidence/{source.name}",
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        }
                    ],
                    "assessments": [
                        {
                            **a,
                            "id": a["evaluator"],
                            "kind": kinds[a["evaluator"]],
                            "evidence_ids": ["observation"],
                            "provenance": {
                                "source": "recorded fixture; no live review",
                                "mock": a["evaluator"] == "mock-ai",
                            },
                        }
                        for a in original["assessments"]
                    ],
                    "provenance": original["provenance"],
                    "original_result": original,
                }
            )
    commit = (
        os.environ.get("SOURCE_COMMIT")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    )
    bundle = {
        "schema_version": "1.0.0",
        "producer": {"id": "capability-verifier", "version": "1.1.0", "commit": commit},
        "results": results,
    }
    (destination / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")
    return bundle


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export(parser.parse_args().destination)
