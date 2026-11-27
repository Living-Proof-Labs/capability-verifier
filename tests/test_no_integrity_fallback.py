# SPDX-License-Identifier: Apache-2.0
"""An empty evaluators input does not run a hardcoded integrity evaluator (#264).

Putting the run-integrity job, its result variable, or those evaluator names
back into a workflow fails this test.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import yaml

from capability_verifier.collect import collect, main

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FORBIDDEN = (
    "livingproof-evaluator-run",
    "livingproof_evaluator_run",
    "livingproof-run-integrity",
)
# The mcap upload is not described as feeding the removed job.
UPLOAD_PHRASES = (
    "carries it to Run Integrity",
    "Run Integrity gets it",
)


def test_workflows_have_no_hardcoded_integrity_fallback():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN:
            assert name not in text, (path.name, name)
    scenario = yaml.safe_load((WORKFLOWS / "scenario.yml").read_text(encoding="utf-8"))
    jobs = scenario["jobs"]
    assert "run-integrity" not in jobs
    report = jobs["report"]
    assert "run-integrity" not in report["needs"]
    for step in report.get("steps") or []:
        assert "RUN_INTEGRITY_RESULT" not in (step.get("env") or {})
    outputs = jobs["plan"]["outputs"]
    assert "run-integrity-repository" not in outputs
    assert "run-integrity-tag" not in outputs
    description = scenario[True]["workflow_call"]["inputs"]["evaluators"]["description"]
    assert "no Run Integrity card" in description
    assert "integrity_job" not in inspect.signature(collect).parameters
    assert "RUN_INTEGRITY_RESULT" not in inspect.getsource(main)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    texts = [
        (path.name, path.read_text(encoding="utf-8")) for path in sorted(WORKFLOWS.glob("*.yml"))
    ]
    texts.append(("README.md", readme))
    for name, text in texts:
        for phrase in UPLOAD_PHRASES:
            assert phrase not in text, (name, phrase)


def test_collecting_files_does_not_invent_an_integrity_card(tmp_path, monkeypatch):
    """The report command, with the old job result set, still stores no integrity card.

    A scenario result is included so the command finishes and writes the manifest,
    as the report job does. The integrity folder holds only the notes that job left.
    """
    bundle = {
        "results": [{"id": "trial-01"}],
        "assessment": {"runs": [{"grade": "FAIL", "conclusion": "FAIL"}]},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
    directory = tmp_path / "downloads"
    scenario = directory / "evaluator-results-livingproof-scenario-stock"
    scenario.mkdir(parents=True)
    (scenario / "trial-01.json").write_text(
        json.dumps(
            {
                "format": "livingproof-evaluator-result/1.1.0",
                "scenario": {"trial_id": "trial-01"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    notes = directory / "evaluator-results-livingproof-run-integrity-stock"
    notes.mkdir()
    (notes / "refused.txt").write_text("refused\n", encoding="utf-8")
    (notes / "no-result.txt").write_text("no result\n", encoding="utf-8")
    report = tmp_path / "reports"
    report.mkdir()
    monkeypatch.setenv("RUN_INTEGRITY_RESULT", "failure")
    assert main([str(manifest), str(directory), str(report)]) == 0
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert "integrity_not_run" not in stored["assessment"]
    assert "integrity_results" not in stored["assessment"]
    assert stored["assessment"]["runs"][0]["conclusion"] == "FAIL"
