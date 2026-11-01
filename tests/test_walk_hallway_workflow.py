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


def test_media_links_are_named_artifacts_handed_to_the_reporter():
    assert "if: inputs.media" in STEPS
    assert 'python -m livingproof_sim_mujoco.media "$ADAPTER"' in STEPS
    for name in ("mcap-", "video-", "links-"):
        assert "${{ inputs.artifact-prefix }}" + name in STEPS, name
    for step in (
        "steps.mcap.outputs.artifact-url",
        "steps.video-pass.outputs.artifact-url",
        "steps.video-fail.outputs.artifact-url",
        "actions/runs/${{ github.run_id }}",
    ):
        assert step in STEPS, step
    assert "--evidence-links links/evidence-links.json" in STEPS
    # the report never waits on media succeeding, and links are rendered only by the reporter
    assert "needs: [plan, verify, media]" in STEPS
    assert "needs.verify.result == 'success'" in STEPS


def test_comment_runs_whatever_media_did():
    comment = STEPS[STEPS.index("\n  comment:") :]
    assert "always() && needs.report.result == 'success'" in comment
    assert "needs: [plan, report]" in comment  # never on media


def _capability_script():
    block = WORKFLOW[WORKFLOW.index("id: capability") :]
    block = block[block.index("\n", block.index("<<'PY'")) + 1 : block.index("          PY\n")]
    return "\n".join(line[10:] for line in block.splitlines())


def _capable(tmp_path, runner, reporter, mcap=True):
    import json
    import subprocess
    import sys

    packages = [("livingproof-sim-mujoco", runner)] + ([("mcap", "1.6.0")] if mcap else [])
    (tmp_path / "uv.lock").write_text(
        "".join(f'[[package]]\nname = "{n}"\nversion = "{v}"\n\n' for n, v in packages)
    )
    (tmp_path / "verification.lock.json").write_text(
        json.dumps({"releases": {"reporter": {"tag": reporter}}})
    )
    out = subprocess.run(
        [sys.executable, "-c", _capability_script()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.strip()


def test_media_is_skipped_not_failed_for_robots_before_the_repin(tmp_path):
    assert "if: inputs.media && needs.plan.outputs.media-capable == 'true'" in STEPS
    assert _capable(tmp_path, "1.5.0", "v1.10.0") == "media-capable=false"
    assert _capable(tmp_path, "1.6.0", "v1.10.0") == "media-capable=false"
    assert _capable(tmp_path, "1.6.0", "v1.11.0", mcap=False) == "media-capable=false"
    assert _capable(tmp_path, "1.6.0", "v1.11.0") == "media-capable=true"
    assert _capable(tmp_path, "1.10.2", "v2.0.0") == "media-capable=true"


def test_release_uploads_replace_assets_so_a_rerun_still_publishes():
    verify = (Path(__file__).parents[1] / ".github/workflows/verify.yml").read_text()
    uploads = [line for line in verify.splitlines() if "gh release upload" in line]
    assert uploads and all("--clobber" in line for line in uploads)
