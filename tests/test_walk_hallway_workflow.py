# SPDX-License-Identifier: Apache-2.0
"""The reusable walk-hallway workflow: callable, lock-driven, renderer is the reporter image."""

from pathlib import Path

WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/walk-hallway.yml").read_text()
STEPS = "\n".join(line for line in WORKFLOW.splitlines() if not line.lstrip().startswith("#"))


def test_is_a_reusable_workflow_with_fan_out_inputs():
    assert "workflow_call:" in STEPS
    for name in ("repository:", "ref:", "configurations:", "artifact-prefix:", "comment:"):
        assert name in STEPS, name
    assert "report-artifacts:" in STEPS


def test_every_ref_comes_from_the_robot_lock():
    assert "repin.py --root robot --check" in STEPS
    assert "needs.plan.outputs.reporter-image" in STEPS
    assert "needs.plan.outputs.verifier-commit" in STEPS
    assert "LIVINGPROOF_VERIFIER_VERSION: ${{ needs.plan.outputs.verifier-version }}" in STEPS
    assert "livingproof-capability-report:1." not in STEPS
    assert '"@" + .releases.reporter.digest' in STEPS


def test_capability_fail_is_not_a_ci_failure_and_reporter_posts_the_comment():
    assert "--require-pass" not in STEPS
    assert "livingproof-capability-report comment --repo" in STEPS
    assert "pr_comment.py" not in STEPS and "gh api" not in STEPS
    assert "pull-requests: write" in STEPS


def test_check_and_evidence_need_no_robot_taskfile():
    assert "task " not in STEPS and "go-task/setup-task" not in STEPS
    assert '["tool"]["livingproof"]["adapter"]' in STEPS
    assert 'python -m livingproof_sim_mujoco.evidence "$ADAPTER"' in STEPS
    for step in (
        "ruff check .",
        "ruff format --check .",
        "pytest tests --junitxml",
        "uv sync --locked",
    ):
        assert step in STEPS, step
    assert "if: inputs.check" in STEPS
