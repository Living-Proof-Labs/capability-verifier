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


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


def _seed_pass(out: Path) -> None:
    out.mkdir()
    (out / GRADE_NAME).write_text('{"summary": {"grade": "PASS"}}\n', encoding="utf-8")


def _seed_refusal(out: Path) -> None:
    out.mkdir()
    (out / REFUSAL_NAME).write_text(
        '{"status": "REFUSED", "code": "integrity"}\n', encoding="utf-8"
    )


def test_assess_does_not_clobber_an_earlier_result(tmp_path):
    """An old PASS (or refusal) is never presented as this run's result, and never erased.

    Replaces test_refusal_clears_an_earlier_verification_json under Rivet's
    authorization (issuecomment-6941712983): Aaron's never-clobber decision
    on livingproof-verification#100 supersedes deleting a stale grade.
    """
    from capability_verifier.declared import assess

    cases = (
        ("pass-then-grade", _seed_pass, False),
        ("pass-then-refuse", _seed_pass, True),
        ("refusal-then-grade", _seed_refusal, False),
        ("refusal-then-refuse", _seed_refusal, True),
    )
    errors: list[str] = []
    for name, seed, tamper in cases:
        root = tmp_path / name
        specs_root, worlds_root, manifest = _setup(root)
        if tamper:
            world = worlds_root / "worlds" / "hallway-corner" / "0.1.0" / "world.json"
            world.write_bytes(world.read_bytes() + b"\n")
        out = root / "out"
        seed(out)
        before = _snapshot(out)
        raised = None
        code = None
        try:
            code = assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
        except FileExistsError as err:
            raised = err
        after = _snapshot(out)
        if after != before:
            errors.append(f"{name}: occupied destination was changed")
            continue
        if raised is not None:
            continue
        if code == 0:
            siblings = [path for path in out.parent.iterdir() if path != out and path.is_dir()]
            if not any(
                (path / GRADE_NAME).is_file() or (path / REFUSAL_NAME).is_file()
                for path in siblings
            ):
                errors.append(
                    f"{name}: success at the occupied destination would present the earlier result"
                )
        elif code != REFUSAL_EXIT:
            errors.append(f"{name}: unexpected exit {code}")
    assert not errors, "; ".join(errors)


def test_occupied_destination_never_returns_without_a_new_result(tmp_path):
    """Returning normally on an occupied destination must produce a new result elsewhere.

    Otherwise the earlier verification.json or refusal.json is the only result a
    caller can find for this invocation. Raising FileExistsError is the clean refusal.
    """
    from capability_verifier.declared import assess

    for name, seed, tamper in (
        ("pass-grade", _seed_pass, False),
        ("pass-refuse", _seed_pass, True),
        ("refusal-grade", _seed_refusal, False),
        ("refusal-refuse", _seed_refusal, True),
    ):
        root = tmp_path / name
        specs_root, worlds_root, manifest = _setup(root)
        if tamper:
            world = worlds_root / "worlds" / "hallway-corner" / "0.1.0" / "world.json"
            world.write_bytes(world.read_bytes() + b"\n")
        out = root / "out"
        seed(out)
        try:
            assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
        except FileExistsError:
            continue
        fresh = [
            path
            for path in root.rglob("*.json")
            if path.name in (GRADE_NAME, REFUSAL_NAME) and path.parent != out
        ]
        assert fresh, f"{name}: returned with only the earlier result in {out.name}"


def test_undeclared_parameters_digest_is_an_integrity_refusal(tmp_path):
    specs_root = base._specs_root(tmp_path, base._parameters(scenario_id="walk-corner"))
    worlds_root = base._worlds_root(tmp_path)
    specs = {k: v for k, v in base.SPECS_V230.items() if k not in ("sha256", "files")}
    manifest = base._evidence(tmp_path, scenario="walk-corner@0.1.0", specs=specs)
    out = tmp_path / "out"
    code = base._assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert code == REFUSAL_EXIT, "no declared digest must not self-certify"
    assert not (out / GRADE_NAME).exists()
    assert _refusal(out)["code"] == "integrity"
