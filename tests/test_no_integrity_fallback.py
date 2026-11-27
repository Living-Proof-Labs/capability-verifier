# SPDX-License-Identifier: Apache-2.0
"""An empty evaluators input does not run a hardcoded integrity evaluator (#264).

Putting the run-integrity job, its result variable, or those evaluator names
back into a workflow fails this test.
"""

from __future__ import annotations

import inspect
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


def test_collecting_files_does_not_invent_an_integrity_card(tmp_path):
    bundle = {
        "results": [{"id": "trial-01"}],
        "assessment": {"runs": [{"grade": "FAIL", "conclusion": "FAIL"}]},
    }
    directory = tmp_path / "downloads"
    note = directory / "evaluator-results-livingproof-run-integrity-stock"
    note.mkdir(parents=True)
    (note / "refused.txt").write_text("refused\n", encoding="utf-8")
    (note / "no-result.txt").write_text("no result\n", encoding="utf-8")
    before = bundle["assessment"]["runs"][0]["conclusion"]
    collect(bundle, directory)
    assert "integrity_not_run" not in bundle["assessment"]
    assert "integrity_results" not in bundle["assessment"]
    assert bundle["assessment"]["runs"][0]["conclusion"] == before
