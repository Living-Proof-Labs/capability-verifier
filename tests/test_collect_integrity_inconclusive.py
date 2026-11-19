# SPDX-License-Identifier: Apache-2.0
"""A 1.2.0 INCONCLUSIVE integrity run is ungraded, not a failed integrity result.

livingproof-evaluator#38 / PR 51: when no integrity check applies, the run is
INCONCLUSIVE with no percentage. The fixture is the PR 51 example
(examples/integrity/all-not-applicable/result.json). A real failure (ERROR) and
the 1.0 / 1.1 results keep their old reading.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from capability_verifier.collect import _integrity_failed, collect, integrity_report_lines

BUNDLE = Path(__file__).parent / "fixtures" / "g1-playground-bundle"
FIXTURE = Path(__file__).parent / "fixtures" / "integrity-all-not-applicable" / "result.json"


def _document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _run(document: dict) -> dict:
    return document["integrity"]["run"]


def test_fixture_is_the_pr51_shape():
    document = _document()
    assert document["format"] == "livingproof-evaluator-result/1.2.0"
    assert _run(document)["test_system_status"] == "INCONCLUSIVE"
    assert _run(document)["success"] is False
    assert "percentage" not in _run(document)


def test_inconclusive_run_is_not_a_failed_integrity_result():
    assert _integrity_failed(_document()) is False


def test_failed_run_still_counts_as_failed():
    document = _document()
    document["format"] = "livingproof-evaluator-result/1.1.0"
    _run(document).update(
        {"test_system_status": "ERROR", "conclusion": "INCONCLUSIVE", "percentage": 0}
    )
    assert _integrity_failed(document) is True


def test_an_unrecognised_run_status_is_not_graded():
    # livingproof-evaluator#38 compat rule: a 1.x minor may add run statuses, and a
    # consumer treats any status it does not recognise as not graded.
    for status in ("PARTIAL", "SKIPPED", "inconclusive"):
        document = _document()
        document["format"] = "livingproof-evaluator-result/1.3.0"
        _run(document).update({"test_system_status": status, "success": False})
        assert _integrity_failed(document) is False, status


def test_an_unrecognised_status_does_not_mark_robot_runs(tmp_path):
    bundle_dir = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle_dir)
    bundle = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    before = [dict(run) for run in bundle["assessment"]["runs"]]
    document = _document()
    _run(document).update({"test_system_status": "PARTIAL", "success": False})
    dest = tmp_path / "downloads" / "evaluator-results-livingproof-run-integrity-playground"
    dest.mkdir(parents=True)
    (dest / "result.json").write_text(json.dumps(document), encoding="utf-8")
    collect(bundle, tmp_path / "downloads")
    assert bundle["assessment"]["runs"] == before


def test_old_formats_keep_their_reading():
    document = _document()
    document["format"] = "livingproof-evaluator-result/1.0.0"
    _run(document).clear()
    _run(document).update(
        {"id": "r", "test_system_status": "COMPLETED", "success": True, "percentage": 100}
    )
    assert _integrity_failed(document) is False


def test_collect_leaves_robot_runs_ungraded_by_integrity(tmp_path):
    bundle_dir = tmp_path / "bundle"
    shutil.copytree(BUNDLE, bundle_dir)
    bundle = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    before = [dict(run) for run in bundle["assessment"]["runs"]]
    dest = tmp_path / "downloads" / "evaluator-results-livingproof-run-integrity-playground"
    dest.mkdir(parents=True)
    shutil.copy(FIXTURE, dest / "result.json")
    collect(bundle, tmp_path / "downloads")
    assert bundle["assessment"]["runs"] == before
    stored = bundle["assessment"]["integrity_results"]["livingproof-run-integrity"]
    assert _run(stored)["test_system_status"] == "INCONCLUSIVE"
    assert "Run Integrity: INCONCLUSIVE" in integrity_report_lines(bundle)
