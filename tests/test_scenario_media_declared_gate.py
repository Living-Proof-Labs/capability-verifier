# SPDX-License-Identifier: Apache-2.0
"""scenario-media runs on the declared path without evaluator-commit (capability-verifier#277).

The trial job already runs when ``inputs.evaluators`` is set, even if the lock pin is
empty. v3.1.3 skipped the media job unless that pin was set, so a caller that only
declares Scenario never ran the media phase. A world-graded (wrapper) run that still
needs the pin must fail loudly, not skip the job.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml
from test_scenario_loader import _run_step, _script

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = yaml.safe_load((ROOT / ".github" / "workflows" / "scenario.yml").read_text())
WALK = yaml.safe_load((ROOT / ".github" / "workflows" / "walk-hallway.yml").read_text())

# (evaluators, evaluator_commit) -> the job must run.
# The declared path is a non-empty evaluators input with an empty pin.
CASES = (
    ("", "", False),
    ("", "a" * 40, True),
    ("owner/scenario@v1.6.0/livingproof-evaluator.json", "", True),
    ("owner/scenario@v1.6.0/livingproof-evaluator.json", "a" * 40, True),
)


def _step(job: str, name: str) -> dict:
    return next(s for s in SCENARIO["jobs"][job]["steps"] if s.get("name", "").startswith(name))


def _job_would_run(condition: str | None, *, evaluators: str, commit: str) -> bool:
    """True when the job-level ``if`` would run, assuming evidence succeeded and the job was not cancelled."""
    if condition is None:
        return True
    expr = condition.replace("${{", "").replace("}}", "").strip()
    values = {
        "!cancelled()": "true",
        "needs.evidence.result == 'success'": "true",
        "needs.plan.outputs.evaluator-commit != ''": "true" if commit else "false",
        "inputs.evaluators != ''": "true" if evaluators else "false",
    }
    for token, value in sorted(values.items(), key=lambda item: -len(item[0])):
        expr = expr.replace(token, value)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    leftover = re.findall(r"[A-Za-z_][\w.]*", expr)
    leftover = [word for word in leftover if word not in {"true", "false", "and", "or"}]
    assert not leftover, f"unsubstituted job if: {condition!r} -> {leftover}"
    tree = ast.parse(expr, mode="eval")
    return bool(
        eval(
            compile(tree, "<job-if>", "eval"), {"__builtins__": {}}, {"true": True, "false": False}
        )
    )


@pytest.mark.parametrize("evaluators,commit,should_run", CASES)
@pytest.mark.parametrize("job", ("scenario-evaluator", "scenario-media"))
def test_both_job_gates_accept_the_declared_path(evaluators, commit, should_run, job):
    """Reverting the media job to an evaluator-commit-only if fails the declared, no-pin case."""
    condition = SCENARIO["jobs"][job].get("if")
    # The trial job has no job-level if: it always runs. The media job must still
    # skip when nothing was declared and there is no pin.
    expected = True if job == "scenario-evaluator" else should_run
    assert _job_would_run(condition, evaluators=evaluators, commit=commit) is expected, (
        f"{job} if={condition!r} evaluators={evaluators!r} commit={commit!r}"
    )


def test_the_media_job_checkouts_use_the_same_gates_as_the_trial_job():
    """An unconditional evaluator checkout would fail the declared path (empty ref)."""
    pin = "Scenario evaluator package at the locked pin"
    source = "Workflow source for the evaluator pin"
    for name in (pin, source):
        assert _step("scenario-media", name).get("if") == _step("scenario-evaluator", name).get(
            "if"
        ), name
    assert _step("scenario-media", pin)["if"] == "needs.plan.outputs.evaluator-commit != ''"
    assert (
        _step("scenario-media", source)["if"]
        == "needs.plan.outputs.evaluator-commit != '' || inputs.evaluators != ''"
    )


def test_walk_hallway_forwards_both_inputs_and_has_no_own_media_gate():
    """walk-hallway.yml is a wrapper around scenario.yml; the gate lives there."""
    assert "scenario-media" not in yaml.dump(WALK["jobs"])
    forwarded = WALK["jobs"]["scenario"]["with"]
    assert forwarded["evaluators"] == "${{ inputs.evaluators }}"
    assert forwarded["evaluator-commit"] == "${{ inputs.evaluator-commit }}"


def test_the_declared_media_branch_runs_without_evaluator_commit(tmp_path):
    """The media step's declared branch does not need the lock pin."""
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    done, log = _run_step(script, "declared", tmp_path / "declared", evaluator_commit="")
    assert done.returncode == 0, done.stderr or done.stdout
    assert "capability_verifier.evaluators run" in log and "--phase media" in log
    assert "run_scenario_media.py" not in log


def test_wrapper_media_without_evaluator_commit_fails_loudly(tmp_path):
    """A world-graded scenario still needs the pin; skipping the job hid that."""
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    done, log = _run_step(
        script,
        "wrapper",
        tmp_path / "wrapper",
        evaluator_commit="",
        manifest='{"plan": {"scenario": "walk-corner@0.1.0"}}\n',
    )
    text = (done.stderr or "") + (done.stdout or "")
    assert done.returncode != 0, text or log
    assert "evaluator-commit" in text
    assert "run_scenario_media.py" not in log
