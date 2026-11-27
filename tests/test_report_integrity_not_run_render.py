# SPDX-License-Identifier: Apache-2.0
"""The report job does not invent a Run Integrity card when no integrity result was collected (#264).

Runs the workflow's collect step, then its render step with reporter 3.6.0, on a real bundle
1.1.0 export. Setting ``RUN_INTEGRITY_RESULT`` and leaving ``refused.txt`` or ``no-result.txt``
does not store ``integrity_not_run`` or print a Run Integrity line.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from test_walk_hallway_workflow import (
    _bundle_with_varied_durations,
    _isolated_collector,
    _reporter_3_6_0,
    _step,
)


def _job(
    tmp_path, monkeypatch, *, integrity: dict | None, note: str | None, refusal: str | None = None
):
    bundle = _bundle_with_varied_durations(tmp_path, monkeypatch)
    job = tmp_path / "job"
    (job / "verified").mkdir(parents=True)
    bundle.rename(job / "verified" / "bundle")
    manifest = json.loads((job / "verified" / "bundle" / "manifest.json").read_text())
    scenario = job / "evaluator-results" / "evaluator-results-livingproof-scenario-playground"
    scenario.mkdir(parents=True)
    for result in manifest["results"]:
        document = {
            "format": "livingproof-evaluator-result/1.1.0",
            "evaluator": {
                "id": "livingproof-scenario",
                "version": "1.5.3",
                "kind": "scenario",
                "execution": "deterministic",
            },
            "scenario": {"trial_id": result["id"], "steps": []},
        }
        (scenario / f"{result['id']}.json").write_text(json.dumps(document) + "\n")
    integrity_dir = (
        job / "evaluator-results" / "evaluator-results-livingproof-run-integrity-playground"
    )
    integrity_dir.mkdir(parents=True)
    (integrity_dir / "specs-files.json").write_text("{}\n")
    if note is not None:
        (integrity_dir / "no-result.txt").write_text(note + "\n")
    if refusal is not None:
        (integrity_dir / "refused.txt").write_text(refusal + "\n")
    if integrity is not None:
        (integrity_dir / "result.json").write_text(json.dumps(integrity) + "\n")
    return job


def _run(job, tmp_path, result):
    env = dict(os.environ)
    env["PATH"] = f"{_reporter_3_6_0(tmp_path)}{os.pathsep}{env['PATH']}"
    env["RUN_INTEGRITY_RESULT"] = result
    env["EVALUATOR_RESULTS_PATTERN"] = "evaluator-results-*-playground"
    for name in ("Append each evaluator's result documents", "Validate, then render"):
        subprocess.run(
            ["bash", "-e", "-c", _isolated_collector(_step(name), job)],
            cwd=job,
            env=env,
            check=True,
        )
    return (job / "reports" / "report.md").read_text(), (job / "reports" / "report.txt").read_text()


@pytest.mark.parametrize("result", ["failure", "cancelled", "success"])
def test_a_missing_integrity_result_is_not_shown(result, tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, integrity=None, note=None, refusal="refused the run")
    md, txt = _run(job, tmp_path, result)
    for text in (md, txt):
        assert "Run Integrity" not in text
    stored = json.loads((job / "verified" / "bundle" / "manifest.json").read_text())
    assert "integrity_not_run" not in stored["assessment"]
    assert "integrity_results" not in stored["assessment"]


def test_a_no_result_note_does_not_become_a_run_integrity_line(tmp_path, monkeypatch):
    note = "no Run Integrity result for this run: planned_trials is 0"
    job = _job(tmp_path, monkeypatch, integrity=None, note=note)
    md, txt = _run(job, tmp_path, "failure")
    assert note not in md and note not in txt
    assert "Run Integrity" not in md and "Run Integrity" not in txt
