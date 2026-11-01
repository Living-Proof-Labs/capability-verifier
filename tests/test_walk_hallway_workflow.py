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


def _outputs(tmp_path, runner, reporter, mcap=True, verifier="v1.7.0"):
    import json
    import subprocess
    import sys

    packages = [("livingproof-sim-mujoco", runner)] + ([("mcap", "1.6.0")] if mcap else [])
    (tmp_path / "uv.lock").write_text(
        "".join(f'[[package]]\nname = "{n}"\nversion = "{v}"\n\n' for n, v in packages)
    )
    (tmp_path / "verification.lock.json").write_text(
        json.dumps({"releases": {"reporter": {"tag": reporter}, "verifier": {"tag": verifier}}})
    )
    out = subprocess.run(
        [sys.executable, "-c", _capability_script()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


def _capable(tmp_path, runner, reporter, mcap=True):
    return "media-capable=" + _outputs(tmp_path, runner, reporter, mcap)["media-capable"]


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


def _step(name):
    """The run script of the step whose name starts with ``name``."""
    block = WORKFLOW[WORKFLOW.index(f"- name: {name}") :]
    block = block[block.index("run: |") + len("run: |") + 1 :]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


def _run(tmp_path, script, tool, bundle_version):
    """Run a workflow step with ``tool`` replaced by a stub that logs its arguments."""
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / tool
    stub.write_text(
        '#!/bin/sh\necho "$*" >> "$CALLS"\n'
        'for a; do case "$prev" in -o) : > "$a";; esac; prev=$a; done\n'
    )
    stub.chmod(0o755)
    calls = tmp_path / "calls.txt"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CALLS=str(calls))
    env["BUNDLE_VERSION"] = bundle_version
    subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env, check=True)
    return calls.read_text().splitlines()


def test_bundle_version_follows_verifier_and_reporter_releases(tmp_path):
    def bundle(verifier, reporter):
        return _outputs(tmp_path, "1.6.0", reporter, verifier=verifier)["bundle-version"]

    assert bundle("v1.7.0", "v1.10.0") == "1.0.0"
    assert bundle("v1.9.0", "v1.11.0") == "1.0.0"  # reporter 1.x validates 1.0.0 only
    assert bundle("v1.8.0", "v2.0.0") == "1.0.0"  # verifier cannot write 1.1.0 yet
    assert bundle("v1.9.0", "v2.0.0") == "1.1.0"
    assert bundle("v1.10.0", "v2.1.0") == "1.1.0"
    assert "bundle-version: ${{ steps.capability.outputs.bundle-version }}" in STEPS
    assert STEPS.count("BUNDLE_VERSION: ${{ needs.plan.outputs.bundle-version }}") == 2


def test_verify_writes_the_bundle_version_from_the_plan(tmp_path):
    script = _step("Assess every trial")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    old = _run(tmp_path / "a", script, "uv", "1.0.0")
    new = _run(tmp_path / "b", script, "uv", "1.1.0")
    assert old == ["run --no-sync python tools/verify_hallway.py evidence/manifest.json verified"]
    assert new == [
        "run --no-sync python tools/verify_hallway.py --bundle-version 1.1.0 "
        "evidence/manifest.json verified"
    ]


def _report_calls(tmp_path, bundle_version, links):
    tmp_path.mkdir()
    if links:
        (tmp_path / "links").mkdir()
        (tmp_path / "links/evidence-links.json").write_text("{}")
    calls = _run(
        tmp_path, _step("Validate, then render"), "livingproof-capability-report", bundle_version
    )
    return [c for c in calls if not c.startswith(("--version", "validate", "render reports/"))]


def test_reporter_below_2_grades_bundle_1_0_0_with_links(tmp_path):
    report = "verified/bundle/manifest.json"
    assert _report_calls(tmp_path / "links", "1.0.0", True) == [
        f"grade {report} --evidence-links links/evidence-links.json -o reports/report.json"
    ]
    assert _report_calls(tmp_path / "bare", "1.0.0", False) == [
        f"grade {report} -o reports/report.json"
    ]


def test_reporter_2_renders_bundle_1_1_0_and_never_grades(tmp_path):
    report = "verified/bundle/manifest.json"
    assert _report_calls(tmp_path / "links", "1.1.0", True) == [
        f"render {report} --evidence-links links/evidence-links.json --format json "
        "-o reports/report.json"
    ]
    assert _report_calls(tmp_path / "bare", "1.1.0", False) == [
        f"render {report} --format json -o reports/report.json"
    ]


def test_markdown_html_and_text_still_render_from_report_json(tmp_path):
    tmp_path = tmp_path / "r"
    tmp_path.mkdir()
    calls = _run(tmp_path, _step("Validate, then render"), "livingproof-capability-report", "1.1.0")
    assert "validate verified/bundle/manifest.json" in calls
    assert "render reports/report.json --format markdown -o reports/report.md" in calls
    assert "render reports/report.json --format html -o reports/report.html" in calls


def test_pre_and_dev_releases_fail_closed_and_never_count_as_the_release(tmp_path):
    import json
    import subprocess
    import sys

    def run(verifier, reporter, runner="1.6.0"):
        (tmp_path / "uv.lock").write_text(
            f'[[package]]\nname = "livingproof-sim-mujoco"\nversion = "{runner}"\n\n'
            '[[package]]\nname = "mcap"\nversion = "1.6.0"\n'
        )
        (tmp_path / "verification.lock.json").write_text(
            json.dumps({"releases": {"reporter": {"tag": reporter}, "verifier": {"tag": verifier}}})
        )
        done = subprocess.run(
            [sys.executable, "-c", _capability_script()],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr  # no crash on any tag
        return dict(line.split("=", 1) for line in done.stdout.splitlines()), done.stderr

    out, err = run("v1.7.0rc1", "v1.10.0")
    assert out["bundle-version"] == "1.0.0"  # parses (no crash); never counts as 1.7.0
    assert "verifier v1.7.0rc1 is a pre-release or dev release" in err
    out, err = run("v2.0.0.dev1", "v2.0.0")
    assert out["bundle-version"] == "1.0.0"
    assert "verifier v2.0.0.dev1 is a pre-release or dev release" in err
    out, err = run("v1.9.0", "v2.0.0rc1")
    assert out == {"media-capable": "false", "bundle-version": "1.0.0"}
    assert "reporter v2.0.0rc1 is a pre-release" in err
    out, err = run("v1.9.0", "v2.0.0", runner="1.6.0.dev3")
    assert out == {"media-capable": "false", "bundle-version": "1.1.0"}
    assert "runner livingproof-sim-mujoco 1.6.0.dev3" in err
    out, err = run("not-a-version", "v2.0.0")
    assert out["bundle-version"] == "1.0.0" and "not a PEP 440 version" in err
    out, err = run("v1.9.0.post1", "v2.0.0")
    assert out["bundle-version"] == "1.1.0" and err == ""  # a post-release is a release


def test_capability_installs_pinned_packaging_before_the_gate():
    block = WORKFLOW[WORKFLOW.index("id: capability") :]
    assert block.index("pip install --user packaging==") < block.index("<<'PY'")
