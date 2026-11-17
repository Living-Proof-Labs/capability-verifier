# SPDX-License-Identifier: Apache-2.0
"""Run-level integrity results (livingproof-evaluator-run#3 wiring).

The collector used to append only by scenario.trial_id, so an integrity
result was skipped. Results are keyed by evaluator.id; more than one
integrity evaluator per run is kept. A failure marks the run INCONCLUSIVE
and never changes the robot grade.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from capability_verifier.collect import collect

BUNDLE = Path(__file__).parent / "fixtures" / "g1-playground-bundle"
WHEN = "2026-11-17T08:00:00Z"


def _bundle(tmp_path: Path) -> tuple[dict, Path]:
    dest = tmp_path / "bundle"
    shutil.copytree(BUNDLE, dest)
    return json.loads((dest / "manifest.json").read_text(encoding="utf-8")), dest


def _integrity(
    evaluator_id: str,
    *,
    ok: bool,
    display_name: str = "Run Integrity",
) -> dict:
    run = {
        "id": "example-run",
        "test_system_status": "COMPLETED" if ok else "ERROR",
        "success": ok,
        "percentage": 100 if ok else 0,
    }
    if not ok:
        run["conclusion"] = "INCONCLUSIVE"
    return {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {
            "id": evaluator_id,
            "version": "1.0.0",
            "kind": "integrity",
            "execution": "deterministic",
            "display_name": display_name,
            "publisher": "Living Proof Labs",
        },
        "inputs": [],
        "timestamps": {"started_at": WHEN, "completed_at": WHEN},
        "integrity": {
            "run": run,
            "trials": [],
            "checks": [],
        },
        "descriptor": {
            "title": display_name,
            "purpose": "Run integrity.",
            "version": "1.0.0",
            "kind": "integrity",
            "tier": "test_system_status",
        },
    }


def _write(directory: Path, name: str, document: dict) -> None:
    dest = directory / name
    dest.mkdir(parents=True)
    (dest / "result.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _robot_grades(bundle: dict) -> tuple:
    runs = bundle["assessment"]["runs"]
    return (
        [run["grade"] for run in runs],
        [result["decision"]["status"] for result in bundle["results"]],
        [result["original_result"]["status"] for result in bundle["results"]],
    )


def test_integrity_failure_is_inconclusive_and_leaves_the_robot_grade(tmp_path):
    bundle, _ = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    assert grades[0] == ["PASS"]
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-livingproof-run-integrity-playground",
        _integrity("livingproof-run-integrity", ok=False),
    )
    collect(bundle, downloads)
    assert _robot_grades(bundle) == grades
    stored = bundle["assessment"]["integrity_results"]
    assert stored["livingproof-run-integrity"]["integrity"]["run"]["test_system_status"] == "ERROR"
    for run in bundle["assessment"]["runs"]:
        assert run["conclusion"] == "INCONCLUSIVE"
        assert run["grade"] == "PASS"
    from capability_verifier.collect import integrity_report_lines

    assert "Run Integrity: ERROR" in integrity_report_lines(bundle)


def test_two_integrity_evaluators_are_both_kept(tmp_path):
    bundle, _ = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-livingproof-run-integrity-playground",
        _integrity("livingproof-run-integrity", ok=True),
    )
    _write(
        downloads,
        "evaluator-results-example-pins-playground",
        _integrity("example-pins", ok=True, display_name="Pins"),
    )
    collect(bundle, downloads)
    assert _robot_grades(bundle) == grades
    stored = bundle["assessment"]["integrity_results"]
    assert set(stored) == {"livingproof-run-integrity", "example-pins"}
    assert stored["livingproof-run-integrity"]["evaluator"]["id"] == "livingproof-run-integrity"
    assert stored["example-pins"]["evaluator"]["id"] == "example-pins"
    for run in bundle["assessment"]["runs"]:
        assert "conclusion" not in run
        assert run["grade"] == "PASS"
    from capability_verifier.collect import integrity_report_lines

    lines = integrity_report_lines(bundle)
    assert "Run Integrity: COMPLETED" in lines
    assert "Pins: COMPLETED" in lines
