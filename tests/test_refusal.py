# SPDX-License-Identifier: Apache-2.0
"""livingproof-verification-refusal/0.1 (livingproof-verification#100 step 2).

The emitter is not wired into the live hallway path. These cases call it
directly: a tampered digest, a parameter-schema reject, and an unknown check
id. Each must exit non-zero and write no verification.json grade.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from livingproof_evaluator_scenario.checks import EVALUATORS

FORMAT = "livingproof-verification-refusal/0.1"
ROOT = Path(__file__).parents[1]
PARAMETERS_SCHEMA = (
    ROOT / ".specs-hallway" / "specs" / "scenarios" / "scenario-parameters.schema.json"
)
HALLWAY_PARAMETERS = (
    ROOT / ".specs-hallway" / "specs" / "scenarios" / "walk-hallway" / "parameters.json"
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["format", "scenario", "checks"],
    "properties": {
        "format": {"type": "string", "const": "livingproof-scenario-parameters/0.1"},
        "scenario": {"type": "object"},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["evaluator"],
                "properties": {"evaluator": {"type": "string"}},
            },
        },
    },
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def _valid_parameters(check_ids=None) -> dict:
    names = list(check_ids) if check_ids is not None else [EVALUATORS[0]]
    return {
        "format": "livingproof-scenario-parameters/0.1",
        "scenario": {"id": "walk-hallway", "revision": "0.1.0"},
        "checks": [{"evaluator": name} for name in names],
    }


def _identity(_path: Path, digest: str) -> dict:
    return {
        "repository": "https://github.com/Living-Proof-Labs/robot-capability-specs",
        "tag": "v2.3.0",
        "commit": "57317e617014a6df97bb7fba9d92a890af8d42e5",
        "path": "specs/scenarios/walk-hallway/parameters.json",
        "sha256": digest,
    }


def _request(path: Path, digest: str, *, check_ids=None, schema=None) -> dict:
    if check_ids is None:
        names = [c["evaluator"] for c in json.loads(path.read_text())["checks"]]
    else:
        names = list(check_ids)
    declared = _identity(path, digest)
    return {
        "files": [{"role": "parameters", "path": str(path), "declared": declared}],
        "schema": {
            "role": "parameters",
            "path": str(path),
            "schema": SCHEMA if schema is None else schema,
            "declared": declared,
        },
        "vocabulary": {
            "role": "checks",
            "check_ids": names,
            "declared": declared,
            "supported_check_ids": list(EVALUATORS),
        },
    }


def _emitter():
    from capability_verifier import refusal

    return refusal


def _assert_refusal(out: Path, *, code: str, role: str, declared: dict, actual_digest: str | None):
    assert not (out / "verification.json").exists(), "a refusal must not write a grade"
    document = json.loads((out / "refusal.json").read_text())
    assert document["format"] == FORMAT
    assert document["status"] == "REFUSED"
    assert document["code"] == code
    assert document["role"] == role
    assert document["declared"] == declared
    assert document["sentence"].endswith(".")
    assert document["sentence"].count(".") == 1
    if actual_digest is None:
        assert "actual_digest" not in document
    else:
        assert document["actual_digest"] == actual_digest
    return document


def test_a_tampered_digest_exits_nonzero_and_writes_no_grade(tmp_path):
    path = _write(tmp_path / "parameters.json", _valid_parameters())
    actual = _sha(path)
    declared_digest = "0" * 64
    assert declared_digest != actual
    out = tmp_path / "out"
    request = _request(path, declared_digest)
    assert _emitter().run(request, out) != 0
    _assert_refusal(
        out,
        code="integrity",
        role="parameters",
        declared=_identity(path, declared_digest),
        actual_digest=actual,
    )


def test_a_parameter_schema_reject_exits_nonzero_and_writes_no_grade(tmp_path):
    path = _write(tmp_path / "parameters.json", {"format": "livingproof-scenario-parameters/0.1"})
    digest = _sha(path)
    out = tmp_path / "out"
    request = _request(path, digest, check_ids=[EVALUATORS[0]])
    assert _emitter().run(request, out) != 0
    _assert_refusal(
        out,
        code="schema",
        role="parameters",
        declared=_identity(path, digest),
        actual_digest=digest,
    )


def test_an_unknown_check_id_exits_nonzero_and_writes_no_grade(tmp_path):
    unknown = "hallway-hover"
    assert unknown not in EVALUATORS
    path = _write(tmp_path / "parameters.json", _valid_parameters([unknown]))
    digest = _sha(path)
    out = tmp_path / "out"
    request = _request(path, digest, check_ids=[unknown])
    assert _emitter().run(request, out) != 0
    _assert_refusal(
        out,
        code="vocabulary",
        role="checks",
        declared=_identity(path, digest),
        actual_digest=digest,
    )


def test_an_empty_scenario_id_is_refused_against_the_official_schema(tmp_path):
    """The pinned specs v2.3.0 schema rejects an empty scenario.id; the gate must too."""
    document = json.loads(HALLWAY_PARAMETERS.read_text(encoding="utf-8"))
    document["scenario"]["id"] = ""
    path = _write(tmp_path / "parameters.json", document)
    digest = _sha(path)
    out = tmp_path / "out"
    request = _request(
        path, digest, schema=json.loads(PARAMETERS_SCHEMA.read_text(encoding="utf-8"))
    )
    assert _emitter().run(request, out) != 0
    _assert_refusal(
        out,
        code="schema",
        role="parameters",
        declared=_identity(path, digest),
        actual_digest=digest,
    )


def _emitted_refusals(tmp_path: Path) -> list[dict]:
    """Every refusal shape the emitter writes today: both integrity forms, schema, vocabulary."""
    refusal = _emitter()
    documents = []

    tampered = _write(tmp_path / "tampered.json", _valid_parameters())
    out = tmp_path / "out-tampered"
    request = _request(tampered, "0" * 64)
    assert refusal.run(request, out) != 0
    documents.append(json.loads((out / "refusal.json").read_text()))

    present = _write(tmp_path / "present.json", _valid_parameters())
    missing_req = _request(present, _sha(present))
    missing_req["files"][0]["path"] = str(tmp_path / "absent.json")
    out = tmp_path / "out-missing"
    assert refusal.run(missing_req, out) != 0
    documents.append(json.loads((out / "refusal.json").read_text()))

    invalid = _write(tmp_path / "invalid.json", {"format": "livingproof-scenario-parameters/0.1"})
    out = tmp_path / "out-schema"
    request = _request(invalid, _sha(invalid), check_ids=["hallway-exit-reached"])
    assert refusal.run(request, out) != 0
    documents.append(json.loads((out / "refusal.json").read_text()))

    unknown = _write(tmp_path / "unknown.json", _valid_parameters(["hallway-hover"]))
    out = tmp_path / "out-vocabulary"
    request = _request(unknown, _sha(unknown), check_ids=["hallway-hover"])
    assert refusal.run(request, out) != 0
    documents.append(json.loads((out / "refusal.json").read_text()))

    return documents


def test_every_emitted_refusal_matches_the_document_schema(tmp_path):
    schema = _emitter().document_schema()
    validator = Draft202012Validator(schema)
    documents = _emitted_refusals(tmp_path)
    assert {document["code"] for document in documents} == {"integrity", "schema", "vocabulary"}
    assert sum(1 for document in documents if document["code"] == "integrity") == 2
    for document in documents:
        validator.validate(document)
