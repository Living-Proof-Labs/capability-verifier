# SPDX-License-Identifier: Apache-2.0
"""Characterization of today's dual specs identities (livingproof-verification#100 step 1).

Evidence for the T1 hallway run names specs v2.0.2. The verifier catalog
(hallway-specs.lock.json) is v2.3.0. Export writes the catalog into every bundle
result's ``spec`` and copies the caller pin through on the evidence lock. This
module records that behaviour. It does not change it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIX = ROOT / "tests" / "fixtures" / "issue-100"
EVIDENCE = ROOT / "tests" / "fixtures" / "lpv-run-43402628666" / "booster-t1-stock" / "evidence"
CATALOG = json.loads((FIX / "catalog" / "hallway-specs.lock.json").read_text())
CALLER_PIN = json.loads((FIX / "evidence-specs.json").read_text())
INVENTORY = json.loads((FIX / "existing-hallway-corner-sha256.json").read_text())

BUNDLE_SPEC = {
    "repository": CATALOG["repository"],
    "tag": CATALOG["tag"],
    "commit": CATALOG["commit"],
    "capability": "walk-hallway",
    "revision": "0.1.0",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tool():
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_issue_100", ROOT / "tools" / "verify_hallway.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_digests(name: str) -> dict[str, str]:
    base = ROOT / "tests" / "fixtures" / name
    found = {}
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(ROOT / "tests" / "fixtures").as_posix()
        found[rel] = _sha(path)
    return found


def test_fixture_names_v2_0_2_evidence_specs_and_a_v2_3_0_catalog():
    catalog_path = FIX / "catalog" / "hallway-specs.lock.json"
    assert catalog_path.read_bytes() == (ROOT / "hallway-specs.lock.json").read_bytes()
    assert CATALOG["tag"] == "v2.3.0"
    assert CATALOG["commit"] == "57317e617014a6df97bb7fba9d92a890af8d42e5"
    assert CATALOG["repository"] == "https://github.com/Living-Proof-Labs/robot-capability-specs"
    assert "specs/scenarios/walk-hallway/parameters.json" in CATALOG["files"]

    assert CALLER_PIN == {
        "repository": "Living-Proof-Labs/robot-capability-specs",
        "tag": "v2.0.2",
        "commit": "16524cbfeef18ff0454ebf125668b010d38a3993",
    }
    evidence = json.loads((EVIDENCE / "manifest.json").read_text())
    assert evidence["lock"]["content"]["releases"]["specs"] == CALLER_PIN


def test_bundle_spec_is_the_catalog_and_the_caller_pin_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.18.5")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    before = {path: _sha(path) for path in EVIDENCE.rglob("*") if path.is_file()}
    copied = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, copied)
    _tool().export(copied / "manifest.json", tmp_path / "verified")
    bundle = json.loads((tmp_path / "verified" / "bundle" / "manifest.json").read_text())
    assert bundle["results"], "export wrote no results"
    for result in bundle["results"]:
        assert result["spec"] == BUNDLE_SPEC
        assert result["provenance"]["lock"]["content"]["releases"]["specs"] == CALLER_PIN
    # The caller pin is still on the evidence the export read; nothing rewrote it.
    after_copy = json.loads((copied / "manifest.json").read_text())
    assert after_copy["lock"]["content"]["releases"]["specs"] == CALLER_PIN
    assert {path: _sha(path) for path in EVIDENCE.rglob("*") if path.is_file()} == before


def test_existing_hallway_and_corner_fixtures_are_byte_stable():
    found = {**_fixture_digests("hallway"), **_fixture_digests("corner")}
    assert found == INVENTORY
