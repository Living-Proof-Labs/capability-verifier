# SPDX-License-Identifier: Apache-2.0
"""Advisory results are stored by kind and a cited details file is checked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_collect_integrity import _bundle, _integrity, _robot_grades, _write

from capability_verifier.collect import (
    DetailsCitationError,
    collect,
    integrity_report_lines,
)

ADVISORY = Path(__file__).parent / "fixtures" / "advisory" / "battery-thermal"
NOTES = (ADVISORY / "notes.txt").read_bytes()
OTHER = b"A different note.\n"


def _document() -> dict:
    return json.loads((ADVISORY / "result.json").read_text(encoding="utf-8"))


def _plant(root: Path, name: str, document: dict, notes: bytes | None, filename: str = "notes.txt"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    if notes is not None:
        dest = directory.joinpath(*filename.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(notes)
    return directory


def test_a_bundle_with_no_advisory_evaluator_has_no_advisory_results(tmp_path):
    bundle, _ = _bundle(tmp_path)
    collect(bundle, tmp_path / "empty")
    for key in ("advisory_results", "advisory_provenance", "advisory_not_run"):
        assert key not in bundle["assessment"]
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-livingproof-run-integrity-playground",
        _integrity("livingproof-run-integrity", ok=True),
    )
    collect(bundle, downloads)
    assert "integrity_results" in bundle["assessment"]
    for key in ("advisory_results", "advisory_provenance", "advisory_not_run"):
        assert key not in bundle["assessment"]


def test_a_cited_details_file_is_copied_and_does_not_grade(tmp_path):
    bundle, _ = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    lines = integrity_report_lines(bundle)
    integrity = bundle["assessment"].get("integrity_results")
    conclusions = [run.get("conclusion") for run in bundle["assessment"]["runs"]]
    document = _document()
    results = tmp_path / "results"
    _plant(results, "battery-thermal", document, NOTES)
    report = tmp_path / "reports"
    collect(bundle, results, report)
    copied = (report / "notes.txt").read_bytes()
    assert copied == NOTES
    assert hashlib.sha256(copied).hexdigest() == document["advisory"]["details_file"]["sha256"]
    placed = bundle["assessment"]["advisory_results"]["battery-thermal"]
    assert isinstance(placed, dict)
    assert placed["evaluator"]["id"] == "battery-thermal"
    assert placed["advisory"]["details_file"]["uri"] == "notes.txt"
    assert _robot_grades(bundle) == grades
    assert bundle["assessment"].get("integrity_results") == integrity
    assert integrity_report_lines(bundle) == lines
    assert [run.get("conclusion") for run in bundle["assessment"]["runs"]] == conclusions


def test_an_altered_details_file_is_refused(tmp_path):
    bundle, _ = _bundle(tmp_path)
    results = tmp_path / "results"
    _plant(results, "battery-thermal", _document(), b"different bytes\n")
    report = tmp_path / "reports"
    with pytest.raises(DetailsCitationError, match="altered"):
        collect(bundle, results, report)
    assert "advisory_results" not in bundle["assessment"]
    assert not (report / "notes.txt").exists()


def test_a_missing_details_file_is_refused(tmp_path):
    bundle, _ = _bundle(tmp_path)
    results = tmp_path / "results"
    _plant(results, "battery-thermal", _document(), None)
    with pytest.raises(DetailsCitationError, match="missing"):
        collect(bundle, results, tmp_path / "reports")
    assert "advisory_results" not in bundle["assessment"]


def test_two_details_citations_cannot_export_different_bytes(tmp_path):
    bundle, _ = _bundle(tmp_path)
    first = _document()
    second = _document()
    second["evaluator"]["id"] = "other-observation"
    second["advisory"]["details_file"]["sha256"] = hashlib.sha256(OTHER).hexdigest()
    results = tmp_path / "results"
    _plant(results, "battery-thermal", first, NOTES)
    _plant(results, "other-observation", second, OTHER)
    with pytest.raises(DetailsCitationError, match="substituted"):
        collect(bundle, results, tmp_path / "reports")
    assert "advisory_results" not in bundle["assessment"]


def test_a_symlinked_details_file_is_substituted(tmp_path):
    bundle, _ = _bundle(tmp_path)
    results = tmp_path / "results"
    directory = _plant(results, "battery-thermal", _document(), None)
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(NOTES)
    (directory / "notes.txt").symlink_to(target)
    with pytest.raises(DetailsCitationError, match="substituted"):
        collect(bundle, results, tmp_path / "reports")
    assert "advisory_results" not in bundle["assessment"]


@pytest.mark.parametrize(
    "uri",
    [
        "../notes.txt",
        "/tmp/notes.txt",
        "https://example.com/notes.txt",
        "notes.md",
        "notes.txt?x=1",
        "notes.txt#frag",
        "a/../../notes.txt",
        "a" * 253 + ".txt",
    ],
)
def test_a_details_path_outside_the_contract_is_refused(tmp_path, uri):
    bundle, _ = _bundle(tmp_path)
    document = _document()
    document["advisory"]["details_file"]["uri"] = uri
    results = tmp_path / "results"
    _plant(results, "battery-thermal", document, NOTES)
    with pytest.raises(DetailsCitationError, match="outside the contract"):
        collect(bundle, results, tmp_path / "reports")
    assert "advisory_results" not in bundle["assessment"]
