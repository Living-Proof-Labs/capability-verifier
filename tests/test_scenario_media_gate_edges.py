# SPDX-License-Identifier: Apache-2.0
"""Edges of the scenario-media gate that capability-verifier#277 left unpinned.

Each test fails when the workflow edit named in its docstring is made.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from test_scenario_loader import _run_step, _script

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
SCENARIO = yaml.safe_load((WORKFLOWS / "scenario.yml").read_text())
WALK = yaml.safe_load((WORKFLOWS / "walk-hallway.yml").read_text())
RUN_INTEGRITY = "Living-Proof-Labs/livingproof-evaluator-run@v1.9.2/livingproof-evaluator.json"


def _would_run(condition: str, *, cancelled: bool, evidence: str, evaluators: str, commit: str):
    """Evaluate a job ``if`` over the four facts it may read. Any other token is an error."""
    values = {
        "!cancelled()": not cancelled,
        "needs.evidence.result == 'success'": evidence == "success",
        "needs.plan.outputs.evaluator-commit != ''": bool(commit),
        "inputs.evaluators != ''": bool(evaluators),
    }
    expr = condition.replace("${{", "").replace("}}", "").strip()
    for token in sorted(values, key=len, reverse=True):
        expr = expr.replace(token, repr(values[token]))
    expr = expr.replace("&&", " and ").replace("||", " or ")
    assert not re.findall(r"[A-Za-z_][\w.]*", re.sub(r"\b(True|False|and|or)\b", "", expr)), expr
    return bool(eval(expr, {"__builtins__": {}}, {}))


@pytest.mark.parametrize("evidence", ("failure", "skipped", "cancelled"))
def test_the_media_job_does_not_run_when_evidence_did_not_succeed(evidence):
    """Dropping ``needs.evidence.result == 'success'`` from the media job if fails this."""
    condition = SCENARIO["jobs"]["scenario-media"]["if"]
    declared = "owner/scenario@v1.6.0/livingproof-evaluator.json"
    for commit in ("", "a" * 40):
        assert not _would_run(
            condition, cancelled=False, evidence=evidence, evaluators=declared, commit=commit
        )


def test_the_media_job_does_not_run_when_the_workflow_is_cancelled():
    """Dropping ``!cancelled()`` from the media job if fails this."""
    condition = SCENARIO["jobs"]["scenario-media"]["if"]
    assert not _would_run(
        condition,
        cancelled=True,
        evidence="success",
        evaluators="owner/scenario@v1.6.0/livingproof-evaluator.json",
        commit="a" * 40,
    )


def test_walk_hallway_forwards_the_caller_inputs_unchanged():
    """Replacing a forwarded value (for example evaluators: '') fails this."""
    forwarded = WALK["jobs"]["scenario"]["with"]
    assert forwarded["evaluators"] == "${{ inputs.evaluators }}"
    assert forwarded["evaluator-commit"] == "${{ inputs.evaluator-commit }}"


def _conditions(workflow: dict):
    for job_name, job in workflow["jobs"].items():
        if "if" in job:
            yield f"job {job_name}", str(job["if"])
        for step in job.get("steps", []):
            if "if" in step:
                yield (
                    f"job {job_name}, step {step.get('name', step.get('id', '?'))}",
                    str(step["if"]),
                )


def test_only_the_known_jobs_and_steps_gate_on_evaluator_commit():
    """Every ``if`` that reads evaluator-commit, in every workflow, is one we reviewed.

    A new gate on the lock pin alone would skip a declared caller again. Each reviewed
    gate either also accepts ``inputs.evaluators`` or guards only the pin checkout.
    """
    found = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for where, condition in _conditions(yaml.safe_load(path.read_text())):
            if "evaluator-commit" in condition:
                found[(path.name, where)] = " ".join(condition.split())
    pin_only = "needs.plan.outputs.evaluator-commit != ''"
    either = "needs.plan.outputs.evaluator-commit != '' || inputs.evaluators != ''"
    pin = "Scenario evaluator package at the locked pin"
    source = "Workflow source for the evaluator pin"
    assert found == {
        ("scenario.yml", f"job scenario-evaluator, step {pin}"): pin_only,
        ("scenario.yml", f"job scenario-evaluator, step {source}"): either,
        ("scenario.yml", f"job scenario-media, step {pin}"): pin_only,
        ("scenario.yml", f"job scenario-media, step {source}"): either,
        ("scenario.yml", "job scenario-media"): (
            "${{ !cancelled() && needs.evidence.result == 'success' && "
            "(needs.plan.outputs.evaluator-commit != '' || inputs.evaluators != '') }}"
        ),
    }


def test_route_sends_a_declaration_without_a_scenario_line_to_the_wrapper(tmp_path, monkeypatch):
    """Premise of the next test: a caller that declares only Run Integrity routes to wrapper."""
    from types import SimpleNamespace

    from capability_verifier import evaluators

    prepared = [SimpleNamespace(document={"kind": "integrity"})]
    monkeypatch.setattr(evaluators, "prepare", lambda *args, **kwargs: prepared)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    assert evaluators.route_lines(RUN_INTEGRITY, manifest) == "wrapper"


def test_a_declaration_without_a_scenario_line_and_without_a_pin_does_not_fail_media(tmp_path):
    """The evidence manifest has no world document, so there is no media to lose."""
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    work = tmp_path / "integrity-only"
    done, log = _run_step(
        script,
        "",
        work,
        evaluators=RUN_INTEGRITY,
        route_real=True,
        evaluator_commit="",
    )
    assert done.returncode == 0, done.stderr or done.stdout
    assert "run_scenario_media.py" not in log
    notes = list((work / "evaluator-results").rglob("NO-MEDIA-RESULTS.txt"))
    assert notes, "wrapper media with no world document must write a note for the upload"
    assert "no world document" in notes[0].read_text(encoding="utf-8")
