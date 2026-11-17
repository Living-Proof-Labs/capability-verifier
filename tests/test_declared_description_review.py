# SPDX-License-Identifier: Apache-2.0
"""Reviewer coverage for the declared corner description (capability-verifier#146, #147).

The bundle's description must be walk-corner's file, carry its catalog sha256,
and state the declared specs commit, so the published description is traceable
to the caller lock rather than the verifier's own catalog.
"""

from __future__ import annotations

import hashlib
import json
import shutil

import test_walk_corner as base


def test_declared_corner_description_states_the_declared_commit_and_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    evidence = tmp_path / "evidence"
    shutil.copytree(base.CORNER / "go1-playground", evidence)
    manifest = evidence / "manifest.json"
    document = json.loads(manifest.read_text())
    catalog = json.loads((base.ROOT / "hallway-specs.lock.json").read_text())
    specs = document["lock"]["content"]["releases"]["specs"]
    specs["repository"] = "Living-Proof-Labs/robot-capability-specs"
    specs["files"] = {
        path: digest
        for path, digest in catalog["files"].items()
        if path.startswith("specs/scenarios/walk-corner/") and path.endswith(".json")
    }
    manifest.write_text(json.dumps(document))
    out = tmp_path / "verified"
    base._tool().export(manifest, out, False, "1.1.0", declared=True)
    source = json.loads((out / "bundle" / "manifest.json").read_text())["description"]["source"]
    path = "specs/scenarios/walk-corner/description.json"
    checkout = base.ROOT / ".specs-hallway" / path
    assert source["path"] == path
    assert source["commit"] == specs["commit"]
    assert source["sha256"] == hashlib.sha256(checkout.read_bytes()).hexdigest()
    assert source["sha256"] == catalog["files"][path]
