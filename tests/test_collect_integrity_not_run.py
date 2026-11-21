# SPDX-License-Identifier: Apache-2.0
"""A failed or refused run-integrity is shown explicitly, never omitted (capability-verifier#214).

When livingproof-evaluator-run refuses (exit 2) or the run-integrity job fails, no integrity
result document exists. The report job still runs, and the collector, told the job's result
(``RUN_INTEGRITY_RESULT``), stores ``assessment.integrity_not_run`` with the reason, marks every
assessment run ``INCONCLUSIVE`` as a failed integrity result does, and the rendered report gets a
"Run Integrity: NOT RUN" line. The robot grade is untouched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_collect_cli import CITED, _layout
from test_collect_integrity import _bundle, _integrity, _robot_grades, _write

from capability_verifier.collect import collect, integrity_report_lines, main

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "scenario.yml"


def _empty(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return downloads


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_a_job_that_did_not_succeed_is_stored_and_makes_the_run_inconclusive(result, tmp_path):
    bundle, _ = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    collect(bundle, _empty(tmp_path), integrity_job=result)
    stored = bundle["assessment"]["integrity_not_run"]
    assert stored["evaluator"] == "livingproof-run-integrity"
    assert stored["job_result"] == result and stored["reason"]
    assert "integrity_results" not in bundle["assessment"]
    assert _robot_grades(bundle) == grades
    for run in bundle["assessment"]["runs"]:
        assert run["conclusion"] == "INCONCLUSIVE" and run["grade"] == "PASS"


def test_the_report_line_says_not_run_and_inconclusive(tmp_path):
    bundle, _ = _bundle(tmp_path)
    collect(bundle, _empty(tmp_path), integrity_job="failure")
    (line,) = integrity_report_lines(bundle)
    assert line.startswith("Run Integrity: NOT RUN")
    assert "INCONCLUSIVE" in line and "failure" in line


def test_a_succeeded_job_that_left_no_result_is_not_omitted_either(tmp_path):
    bundle, _ = _bundle(tmp_path)
    collect(bundle, _empty(tmp_path), integrity_job="success")
    assert bundle["assessment"]["integrity_not_run"]["job_result"] == "success"
    assert "no result" in bundle["assessment"]["integrity_not_run"]["reason"]


def test_the_reason_is_the_job_s_own_no_result_note_when_it_wrote_one(tmp_path):
    bundle, _ = _bundle(tmp_path)
    downloads = tmp_path / "downloads"
    note = downloads / "evaluator-results-livingproof-run-integrity-playground"
    note.mkdir(parents=True)
    (note / "no-result.txt").write_text(
        "no Run Integrity result for this run: planned_trials is 0\n"
    )
    collect(bundle, downloads, integrity_job="failure")
    assert "planned_trials is 0" in bundle["assessment"]["integrity_not_run"]["reason"]


def test_a_result_that_is_there_is_not_called_not_run(tmp_path):
    bundle, _ = _bundle(tmp_path)
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-livingproof-run-integrity-playground",
        _integrity("livingproof-run-integrity", ok=True),
    )
    collect(bundle, downloads, integrity_job="success")
    assert "integrity_not_run" not in bundle["assessment"]
    assert integrity_report_lines(bundle) == ["Run Integrity: COMPLETED"]


def test_without_the_job_result_nothing_changes(tmp_path):
    bundle, _ = _bundle(tmp_path)
    before = json.dumps(bundle, sort_keys=True)
    collect(bundle, _empty(tmp_path))
    assert json.dumps(bundle, sort_keys=True) == before


def test_the_command_line_reads_the_job_result_from_the_environment(tmp_path, monkeypatch):
    manifest, results, report, _original = _layout(tmp_path, CITED)
    monkeypatch.setenv("RUN_INTEGRITY_RESULT", "failure")
    assert main([str(manifest), str(results), str(report)]) == 0
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert stored["assessment"]["integrity_not_run"]["job_result"] == "failure"
    assert (report / "integrity-lines.txt").read_text().startswith("Run Integrity: NOT RUN")


def test_the_command_line_is_unchanged_when_the_variable_is_unset(tmp_path, monkeypatch):
    manifest, results, report, _original = _layout(tmp_path, CITED)
    monkeypatch.delenv("RUN_INTEGRITY_RESULT", raising=False)
    assert main([str(manifest), str(results), str(report)]) == 0
    assert "assessment" not in json.loads(manifest.read_text(encoding="utf-8"))
    assert not (report / "integrity-lines.txt").exists()


def _job(name):
    text = WORKFLOW.read_text()
    match = re.search(rf"^  {re.escape(name)}:\n", text, re.MULTILINE)
    rest = text[match.end() :]
    later = re.search(r"^  [a-z][a-z-]*:\n", rest, re.MULTILINE)
    return text[match.start() : match.end() + (later.start() if later else len(rest))]


def test_the_report_job_runs_whatever_run_integrity_did_and_is_told_what_it_did():
    job = _job("report")
    condition = re.search(r"^    if: (.*)$", job, re.MULTILINE).group(1)
    assert "run-integrity" not in condition and "!cancelled()" in condition
    assert "needs: [plan, verify, scenario-evaluator, scenario-media, media, run-integrity]" in job
    assert "RUN_INTEGRITY_RESULT: ${{ needs.run-integrity.result }}" in job


def test_a_no_result_note_from_another_evaluator_is_not_the_integrity_reason(tmp_path):
    """Only the run-integrity job writes ``no-result.txt`` today; the search is scoped anyway."""
    bundle, _ = _bundle(tmp_path)
    downloads = _empty(tmp_path)
    other = downloads / "evaluator-results-livingproof-scenario-stock"
    other.mkdir()
    (other / "no-result.txt").write_text("a note from the scenario evaluator\n")
    collect(bundle, downloads, integrity_job="failure")
    reason = bundle["assessment"]["integrity_not_run"]["reason"]
    assert "scenario evaluator" not in reason and "ended failure" in reason


def test_the_run_integrity_jobs_own_note_is_still_the_reason(tmp_path):
    bundle, _ = _bundle(tmp_path)
    downloads = _empty(tmp_path)
    mine = downloads / "evaluator-results-livingproof-run-integrity-stock"
    mine.mkdir()
    (mine / "no-result.txt").write_text("media is refused: no trials\n")
    collect(bundle, downloads, integrity_job="failure")
    assert bundle["assessment"]["integrity_not_run"]["reason"] == "media is refused: no trials"
