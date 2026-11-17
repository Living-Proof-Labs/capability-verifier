# SPDX-License-Identifier: Apache-2.0
"""Caller-lock declared catalog (capability-verifier#138, livingproof-verification#100 step 4).

Assessment uses the declared specs commit and world digest, not
hallway-specs.lock.json or worlds.lock.json. A scenario supplied only as
data still grades. Integrity, schema, and vocabulary failures write
livingproof-verification-refusal/0.1 and no verification.json grade.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from capability_verifier.refusal import GRADE_NAME, REFUSAL_EXIT, REFUSAL_NAME

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / ".specs-hallway" / "specs" / "scenarios" / "scenario-parameters.schema.json"
CORNER_PARAMETERS = (
    ROOT / ".specs-hallway" / "specs" / "scenarios" / "walk-corner" / "parameters.json"
)
CORNER_WORLD = ROOT / ".worlds" / "worlds" / "hallway-corner" / "0.1.0" / "world.json"
CORNER_EVIDENCE = ROOT / "tests" / "fixtures" / "corner" / "g1-stock"
HALLWAY_EVIDENCE = ROOT / "tests" / "fixtures" / "hallway" / "review-g1" / "agree"
CATALOG_LOCK = ROOT / "hallway-specs.lock.json"
WORLDS_LOCK = ROOT / "worlds.lock.json"

SPECS_V230 = {
    "repository": "Living-Proof-Labs/robot-capability-specs",
    "tag": "v2.3.0",
    "commit": "57317e617014a6df97bb7fba9d92a890af8d42e5",
}
SPECS_V202 = {
    "repository": "Living-Proof-Labs/robot-capability-specs",
    "tag": "v2.0.2",
    "commit": "16524cbfeef18ff0454ebf125668b010d38a3993",
}
CORNER_WORLD_SHA = "8393881e8b38a090f51c3a424ca444623a901f642f75186cb4d41ac7482898f5"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def _assess(manifest: Path, out: Path, *, specs_root: Path, worlds_root: Path) -> int:
    from capability_verifier.declared import assess

    return assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)


def _parameters(*, scenario_id: str = "walk-data", check_ids=None, empty_id: bool = False) -> dict:
    document = json.loads(CORNER_PARAMETERS.read_text(encoding="utf-8"))
    document["scenario"]["id"] = "" if empty_id else scenario_id
    if check_ids is not None:
        template = document["checks"][0]
        document["checks"] = [{**template, "evaluator": name} for name in check_ids]
    return document


def _specs_root(
    tmp_path: Path, document: dict, *, schema: bool = True, path_id: str | None = None
) -> Path:
    root = tmp_path / "specs-checkout"
    scenario_id = path_id or document["scenario"]["id"] or "walk-data"
    dest = root / "specs" / "scenarios" / scenario_id
    dest.mkdir(parents=True)
    _write(dest / "parameters.json", document)
    if schema:
        shutil.copy(SCHEMA, root / "specs" / "scenarios" / "scenario-parameters.schema.json")
    return root


def _declared_specs(specs: dict, specs_root: Path, scenario_id: str) -> dict:
    """Declare the parameters digest as real caller locks do in specs.files."""
    rel = f"specs/scenarios/{scenario_id}/parameters.json"
    declared = dict(specs)
    declared["files"] = {**(declared.get("files") or {}), rel: _sha(specs_root / rel)}
    return declared


def _worlds_root(tmp_path: Path) -> Path:
    root = tmp_path / "worlds-checkout"
    dest = root / "worlds" / "hallway-corner" / "0.1.0"
    dest.mkdir(parents=True)
    shutil.copy(CORNER_WORLD, dest / "world.json")
    return root


def _evidence(
    tmp_path: Path,
    source: Path = CORNER_EVIDENCE,
    *,
    scenario: str | None = "walk-data@0.1.0",
    specs: dict | None = SPECS_V230,
) -> Path:
    dest = tmp_path / "evidence"
    shutil.copytree(source, dest)
    path = dest / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if scenario is not None:
        if isinstance(manifest.get("plan"), dict):
            manifest["plan"]["scenario"] = scenario
        manifest["lock"]["content"]["scenario"] = scenario
    if specs is not None:
        manifest["lock"]["content"].setdefault("releases", {})["specs"] = dict(specs)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def _guard_catalog_locks(monkeypatch) -> None:
    forbidden = {CATALOG_LOCK.resolve(), WORLDS_LOCK.resolve()}

    def _wrap(original):
        def guarded(self, *args, **kwargs):
            if Path(self).resolve() in forbidden:
                raise AssertionError(f"declared assess read catalog lock {self}")
            return original(self, *args, **kwargs)

        return guarded

    monkeypatch.setattr(Path, "read_text", _wrap(Path.read_text))
    monkeypatch.setattr(Path, "read_bytes", _wrap(Path.read_bytes))


def test_new_scenario_supplied_as_data_grades_without_catalog_entry(tmp_path, monkeypatch):
    assert "walk-data" not in CATALOG_LOCK.read_text(encoding="utf-8")
    parameters = _parameters(scenario_id="walk-data")
    specs_root = _specs_root(tmp_path, parameters)
    worlds_root = _worlds_root(tmp_path)
    manifest = _evidence(
        tmp_path,
        scenario="walk-data@0.1.0",
        specs=_declared_specs(SPECS_V230, specs_root, "walk-data"),
    )
    out = tmp_path / "out"
    _guard_catalog_locks(monkeypatch)
    code = _assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == 0
    assert not (out / REFUSAL_NAME).exists()
    grade = json.loads((out / GRADE_NAME).read_text(encoding="utf-8"))
    assert grade["summary"]["grade"]


def test_tampered_bytes_refuse(tmp_path):
    parameters = _parameters(scenario_id="walk-corner")
    specs_root = _specs_root(tmp_path, parameters)
    worlds_root = _worlds_root(tmp_path)
    world = worlds_root / "worlds" / "hallway-corner" / "0.1.0" / "world.json"
    world.write_bytes(world.read_bytes() + b"\n")
    assert _sha(world) != CORNER_WORLD_SHA
    manifest = _evidence(
        tmp_path,
        scenario="walk-corner@0.1.0",
        specs=_declared_specs(SPECS_V230, specs_root, "walk-corner"),
    )
    out = tmp_path / "out"
    code = _assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT
    assert not (out / GRADE_NAME).exists()
    refusal = json.loads((out / REFUSAL_NAME).read_text(encoding="utf-8"))
    assert refusal["status"] == "REFUSED"
    assert refusal["code"] == "integrity"


def test_schema_reject_refuses(tmp_path):
    parameters = _parameters(scenario_id="walk-corner", empty_id=True)
    specs_root = _specs_root(tmp_path, parameters, path_id="walk-corner")
    worlds_root = _worlds_root(tmp_path)
    manifest = _evidence(
        tmp_path,
        scenario="walk-corner@0.1.0",
        specs=_declared_specs(SPECS_V230, specs_root, "walk-corner"),
    )
    out = tmp_path / "out"
    code = _assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT
    assert not (out / GRADE_NAME).exists()
    refusal = json.loads((out / REFUSAL_NAME).read_text(encoding="utf-8"))
    assert refusal["status"] == "REFUSED"
    assert refusal["code"] == "schema"


def test_unknown_check_id_refuses(tmp_path):
    parameters = _parameters(scenario_id="walk-corner", check_ids=["hallway-hover"])
    specs_root = _specs_root(tmp_path, parameters)
    worlds_root = _worlds_root(tmp_path)
    manifest = _evidence(
        tmp_path,
        scenario="walk-corner@0.1.0",
        specs=_declared_specs(SPECS_V230, specs_root, "walk-corner"),
    )
    out = tmp_path / "out"
    code = _assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT
    assert not (out / GRADE_NAME).exists()
    refusal = json.loads((out / REFUSAL_NAME).read_text(encoding="utf-8"))
    assert refusal["status"] == "REFUSED"
    assert refusal["code"] == "vocabulary"


def test_specs_v2_0_2_refused_because_parameter_file_absent(tmp_path):
    specs_root = tmp_path / "specs-v2.0.2"
    specs_root.mkdir()
    worlds_root = tmp_path / "worlds-empty"
    worlds_root.mkdir()
    manifest = _evidence(
        tmp_path,
        HALLWAY_EVIDENCE,
        scenario="walk-hallway@0.1.0",
        specs=SPECS_V202,
    )
    out = tmp_path / "out"
    code = _assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT
    assert not (out / GRADE_NAME).exists()
    refusal = json.loads((out / REFUSAL_NAME).read_text(encoding="utf-8"))
    assert refusal["status"] == "REFUSED"
    assert refusal["code"] == "integrity"


def test_corner_v2_3_0_still_grades(tmp_path, monkeypatch):
    parameters = json.loads(CORNER_PARAMETERS.read_text(encoding="utf-8"))
    specs_root = _specs_root(tmp_path, parameters)
    worlds_root = _worlds_root(tmp_path)
    manifest = _evidence(
        tmp_path,
        scenario="walk-corner@0.1.0",
        specs=_declared_specs(SPECS_V230, specs_root, "walk-corner"),
    )
    out = tmp_path / "out"
    _guard_catalog_locks(monkeypatch)
    code = _assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == 0
    assert not (out / REFUSAL_NAME).exists()
    grade = json.loads((out / GRADE_NAME).read_text(encoding="utf-8"))
    assert grade["summary"]["grade"] == "PASS"
