# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from export_matrix import export  # noqa: E402
from matrix_sources import contracts, executors, verify_source  # noqa: E402


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    path = tmp_path_factory.mktemp("execution") / "bundle"
    return path, export(path, run_id="test-run-001")


@pytest.mark.parametrize(
    "catalog,executor,expected",
    [
        ("v1.1.1", "v1.0.1", "PASS"),
        ("v1.1.1", "v1.1.0", "PASS"),
        ("v1.2.0", "v1.0.1", "INCOMPATIBLE"),
        ("v1.2.0", "v1.1.0", "FAIL"),
    ],
)
def test_released_execution_coordinate(matrix, catalog, executor, expected, record_property):
    _, bundle = matrix
    result = next(
        r
        for r in bundle["results"]
        if r["spec"]["tag"] == catalog and r["provenance"]["executor"]["tag"] == executor
    )
    record_property("expected_capability_status", expected)
    record_property("actual_capability_status", result["original_result"]["status"])
    record_property("compatibility", result["provenance"]["compatibility"]["status"])
    assert result["original_result"]["status"] == expected
    if expected == "INCOMPATIBLE":
        assert result["assessments"] == []
        assert result["provenance"]["compatibility"]["scope"] == "execution"
    else:
        assert len(result["assessments"]) == 3
        assert result["provenance"]["compatibility"]["status"] == "COMPATIBLE"


def test_evidence_survives_incompatible_executor_and_round_trip(matrix):
    path, bundle = matrix
    assert len(bundle["results"]) == 4
    for result in bundle["results"]:
        evidence = result["evidence"][0]
        raw = (path / evidence["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == evidence["sha256"]
        assert json.loads(raw)["elapsed_ms"] == (
            120 if result["provenance"]["executor"]["tag"] == "v1.0.1" else 300
        )
        assert result["provenance"]["run_id"] == "test-run-001"
        assert result["provenance"]["evidence_state"] == {"observation": "verified"}
    assert json.loads((path / "manifest.json").read_text()) == bundle


def test_released_strict_evaluator_is_executed(matrix):
    _, bundle = matrix
    strict = next(
        r
        for r in bundle["results"]
        if r["spec"]["tag"] == "v1.2.0" and r["provenance"]["executor"]["tag"] == "v1.1.0"
    )
    assert "250 ms" in strict["assessments"][0]["rationale"]
    assert strict["assessments"][0]["version"] == "1.1.0"
    assert strict["assessments"][0]["status"] == "FAIL"


@pytest.mark.parametrize("catalog", ["v1.1.1", "v1.2.0"])
@pytest.mark.parametrize(
    "fixture,expected", [("fail", "FAIL"), ("insufficient", "INSUFFICIENT_EVIDENCE")]
)
def test_controls_preserve_negative_and_missing_evidence(matrix, catalog, fixture, expected):
    path, _ = matrix
    controls = json.loads((path / "controls.json").read_text())["results"]
    result = next(
        r
        for r in controls
        if r["spec"]["tag"] == catalog
        and r["provenance"]["fixture"]["path"] == f"fixtures/{fixture}.json"
    )
    assert result["provenance"]["role"] == "control"
    assert result["provenance"]["compatibility"]["status"] == "COMPATIBLE"
    assert result["original_result"]["status"] == expected


def test_refuses_wrong_released_identity():
    with pytest.raises(ValueError, match="commit mismatch"):
        verify_source({**executors()[0], "commit": "0" * 40})


def test_locked_released_contract_content_has_real_limits():
    for pin, version, limit in zip(contracts(), ("v1", "v2"), ("500", "250"), strict=True):
        source = verify_source(pin)
        assert limit in (source / "specs" / version / "README.md").read_text()


def test_existing_output_is_never_overwritten(matrix):
    with pytest.raises(FileExistsError):
        export(matrix[0])
