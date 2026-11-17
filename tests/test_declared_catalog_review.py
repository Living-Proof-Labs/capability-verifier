# SPDX-License-Identifier: Apache-2.0
"""Reviewer coverage for the declared catalog (capability-verifier#138, #139).

Real caller locks in livingproof-verification declare the parameters digest in
``releases.specs.files`` (livingproof-verification#102), not as
``releases.specs.sha256``. A missing or malformed caller schema is a schema
refusal, not an exception. A refusal must not leave an earlier
``verification.json`` beside it (follow-ups from #133).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import test_declared_catalog as base

from capability_verifier.refusal import GRADE_NAME, REFUSAL_EXIT, REFUSAL_NAME

PARAMETERS = "specs/scenarios/walk-corner/parameters.json"
SCHEMA = "specs/scenarios/scenario-parameters.schema.json"


def _real_lock_specs(parameters_sha: str) -> dict:
    """releases.specs as livingproof-verification writes it: per-file digests."""
    specs = {k: v for k, v in base.SPECS_V230.items() if k != "sha256"}
    specs["files"] = {PARAMETERS: parameters_sha}
    return specs


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    specs_root = base._specs_root(tmp_path, base._parameters(scenario_id="walk-corner"))
    digest = hashlib.sha256((specs_root / PARAMETERS).read_bytes()).hexdigest()
    worlds_root = base._worlds_root(tmp_path)
    manifest = base._evidence(
        tmp_path, scenario="walk-corner@0.1.0", specs=_real_lock_specs(digest)
    )
    return specs_root, worlds_root, manifest


def _refusal(out: Path) -> dict:
    return json.loads((out / REFUSAL_NAME).read_text(encoding="utf-8"))


def test_real_lock_format_untouched_parameters_grade(tmp_path):
    specs_root, worlds_root, manifest = _setup(tmp_path)
    out = tmp_path / "out"
    assert base._assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root) == 0
    assert (out / GRADE_NAME).exists()
    assert not (out / REFUSAL_NAME).exists()


def test_parameters_tampered_against_lock_files_digest_refuse(tmp_path):
    specs_root, worlds_root, manifest = _setup(tmp_path)
    parameters = specs_root / PARAMETERS
    parameters.write_bytes(parameters.read_bytes() + b"\n")
    out = tmp_path / "out"
    code = base._assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT, "parameters bytes differ from releases.specs.files"
    assert not (out / GRADE_NAME).exists()
    assert _refusal(out)["code"] == "integrity"


def _schema_refuses(tmp_path: Path, schema_bytes: bytes | None) -> None:
    specs_root, worlds_root, manifest = _setup(tmp_path)
    schema = specs_root / SCHEMA
    if schema_bytes is None:
        schema.unlink()
    else:
        schema.write_bytes(schema_bytes)
    out = tmp_path / "out"
    code = base._assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT
    assert not (out / GRADE_NAME).exists()
    assert _refusal(out)["code"] == "schema"


def test_missing_caller_schema_is_a_schema_refusal(tmp_path):
    _schema_refuses(tmp_path, None)


def test_caller_schema_that_is_not_json_is_a_schema_refusal(tmp_path):
    _schema_refuses(tmp_path, b"{not json")


def test_caller_schema_that_is_not_a_valid_schema_is_a_schema_refusal(tmp_path):
    _schema_refuses(tmp_path, b'{"type": 5}')


def test_refusal_clears_an_earlier_verification_json(tmp_path):
    specs_root, worlds_root, manifest = _setup(tmp_path)
    world = worlds_root / "worlds" / "hallway-corner" / "0.1.0" / "world.json"
    world.write_bytes(world.read_bytes() + b"\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / GRADE_NAME).write_text('{"summary": {"grade": "PASS"}}\n', encoding="utf-8")
    code = base._assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT
    assert _refusal(out)["code"] == "integrity"
    assert not (out / GRADE_NAME).exists(), "a stale grade sits beside the refusal"
