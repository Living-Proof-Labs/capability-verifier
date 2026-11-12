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
    assert "needs: [plan, verify, scenario-evaluator, media]" in STEPS
    assert "needs.verify.result == 'success'" in STEPS


def test_scenario_evaluator_is_a_parallel_job_and_the_report_collects_by_pattern():
    scenario = STEPS[STEPS.index("\n  scenario-evaluator:") : STEPS.index("\n  media:")]
    assert "needs: [plan, evidence]" in scenario
    assert "needs: [plan, verify]" not in scenario
    assert "tools/run_scenario_evaluator.py" in scenario
    assert "evaluator-results-livingproof-scenario-" in scenario
    assert "needs.scenario-evaluator.result == 'success'" in STEPS
    pattern = (
        "pattern: ${{ inputs.artifact-prefix }}evaluator-results-*-${{ matrix.configuration }}"
    )
    assert pattern in STEPS
    # The download names the configuration, not an evaluator. A second job is another upload.
    after = STEPS[STEPS.index(pattern) :]
    assert "livingproof-scenario" not in after[:240]
    start = WORKFLOW.index("<<'COLLECT'\n") + len("<<'COLLECT'\n")
    end = WORKFLOW.index("\n          COLLECT\n", start)
    indent = "          "
    extracted = "\n".join(
        line[len(indent) :] if line.startswith(indent) else line
        for line in WORKFLOW[start:end].splitlines()
    )
    source = (Path(__file__).parents[1] / "src/capability_verifier/collect.py").read_text()
    assert extracted + "\n" == source


def test_report_collects_with_media_on_and_with_media_off():
    """The collector is in report, so a skipped media job still stores evaluator entries."""
    media = WORKFLOW.split("\n  media:\n", 1)[1].split("\n  report:\n", 1)[0]
    report = WORKFLOW.split("\n  report:\n", 1)[1].split("\n  comment:\n", 1)[0]
    media_head = media.split("    steps:\n", 1)[0]
    report_head = report.split("    steps:\n", 1)[0]
    assert "needs: [plan, verify]\n" in media_head
    assert "scenario-evaluator" not in media_head
    assert "if: inputs.media &&" in media_head
    assert "COLLECT" not in media
    assert "evaluator-results" not in media
    assert "needs: [plan, verify, scenario-evaluator, media]" in report_head
    assert "needs.media.result" not in report_head
    assert "needs.scenario-evaluator.result == 'success'" in report_head
    pattern = "evaluator-results-*-${{ matrix.configuration }}"
    assert pattern in report
    assert report.index("<<'COLLECT'") < report.index("livingproof-capability-report render")
    # Links stay optional when media is off. Collection does not.
    assert "if: needs.media.result == 'success'" in report
    collect_step = report[
        report.index("- name: Append each evaluator") : report.index("<<'COLLECT'")
    ]
    assert "if:" not in collect_step


def test_comment_runs_whatever_media_did():
    comment = STEPS[STEPS.index("\n  comment:") :]
    assert "always() && needs.report.result == 'success'" in comment
    assert "needs: [plan, report]" in comment  # never on media


def _capability_script():
    block = WORKFLOW[WORKFLOW.index("id: capability") :]
    block = block[block.index("\n", block.index("<<'PY'")) + 1 : block.index("          PY\n")]
    return "\n".join(line[10:] for line in block.splitlines())


def _plan(tmp_path, runner, reporter, mcap=True, verifier="v1.9.0"):
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
    return subprocess.run(
        [sys.executable, "-c", _capability_script()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def _outputs(tmp_path, runner, reporter, mcap=True, verifier="v1.9.0"):
    done = _plan(tmp_path, runner, reporter, mcap, verifier)
    assert done.returncode == 0, done.stderr
    return dict(line.split("=", 1) for line in done.stdout.splitlines())


def _capable(tmp_path, runner, reporter, mcap=True):
    return "media-capable=" + _outputs(tmp_path, runner, reporter, mcap)["media-capable"]


def test_media_is_skipped_not_failed_for_robots_before_the_repin(tmp_path):
    assert "if: inputs.media && needs.plan.outputs.media-capable == 'true'" in STEPS
    assert _capable(tmp_path, "1.5.0", "v2.0.0") == "media-capable=false"
    assert _capable(tmp_path, "1.6.0", "v2.0.0", mcap=False) == "media-capable=false"
    assert _capable(tmp_path, "1.6.0", "v2.0.0") == "media-capable=true"
    assert _capable(tmp_path, "1.10.2", "v3.0.0") == "media-capable=true"


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


def test_bundle_1_1_0_only_and_older_locks_fail_with_the_way_out(tmp_path):
    """Bundle 1.0.0 needed the reporter's grade, which reporter 3.0.0 removed."""

    def bundle(verifier, reporter):
        return _outputs(tmp_path, "1.6.0", reporter, verifier=verifier)["bundle-version"]

    assert bundle("v1.9.0", "v2.0.0") == "1.1.0"
    assert bundle("v1.10.0", "v2.1.0") == "1.1.0"
    assert bundle("v1.10.0", "v3.0.0") == "1.1.0"
    for verifier, reporter in (("v1.7.0", "v1.10.0"), ("v1.9.0", "v1.11.0"), ("v1.8.0", "v2.0.0")):
        done = _plan(tmp_path, "1.6.0", reporter, verifier=verifier)
        assert done.returncode != 0 and "bundle-version" not in done.stdout
        assert "needs verifier >= 1.9.0 and reporter >= 2.0.0" in done.stderr
        assert "walk-hallway.yml at v1.10.0 or earlier" in done.stderr
    assert "bundle-version: ${{ steps.capability.outputs.bundle-version }}" in STEPS
    assert STEPS.count("BUNDLE_VERSION: ${{ needs.plan.outputs.bundle-version }}") == 1


def test_verify_writes_the_bundle_version_from_the_plan(tmp_path):
    script = _step("Assess every trial")
    (tmp_path / "b").mkdir()
    new = _run(tmp_path / "b", script, "uv", "1.1.0")
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


def test_the_report_job_never_calls_grade():
    report = STEPS[STEPS.index("\n  report:") : STEPS.index("\n  comment:")]
    assert "capability-report grade" not in report and "BUNDLE_VERSION" not in report


def test_reporter_2_renders_bundle_1_1_0_and_never_grades(tmp_path):
    report = "verified/bundle/manifest.json"
    linked = f"render {report} --evidence-links links/evidence-links.json"
    plain = f"render {report}"
    assert _report_calls(tmp_path / "links", "1.1.0", True) == [
        f"{linked} --format json -o reports/report.json",
        f"{linked} --format markdown -o reports/report.md",
        f"{linked} --format html -o reports/report.html",
    ]
    assert _report_calls(tmp_path / "bare", "1.1.0", False) == [
        f"{plain} --format json -o reports/report.json",
        f"{plain} --format markdown -o reports/report.md",
        f"{plain} --format html -o reports/report.html",
    ]


def test_html_and_markdown_render_from_the_bundle_text_from_report_json(tmp_path):
    tmp_path = tmp_path / "r"
    tmp_path.mkdir()
    calls = _run(tmp_path, _step("Validate, then render"), "livingproof-capability-report", "1.1.0")
    manifest = "verified/bundle/manifest.json"
    assert "validate verified/bundle/manifest.json" in calls
    assert f"render {manifest} --format markdown -o reports/report.md" in calls
    assert f"render {manifest} --format html -o reports/report.html" in calls
    assert "render reports/report.json --format markdown -o reports/report.md" not in calls
    assert "render reports/report.json --format html -o reports/report.html" not in calls
    assert calls.count("render reports/report.json") == 1


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
        assert "Traceback" not in done.stderr  # no crash on any tag
        if done.returncode:
            assert "needs verifier >= 1.9.0 and reporter >= 2.0.0" in done.stderr
            return None, done.stderr
        return dict(line.split("=", 1) for line in done.stdout.splitlines()), done.stderr

    out, err = run("v1.9.0rc1", "v2.0.0")
    assert out is None  # parses (no crash) and never counts as 1.9.0: refused
    assert "verifier v1.9.0rc1 is a pre-release or dev release" in err
    out, err = run("v2.0.0.dev1", "v2.0.0")
    assert out is None and "verifier v2.0.0.dev1 is a pre-release or dev release" in err
    out, err = run("v1.9.0", "v2.0.0rc1")
    assert out is None and "reporter v2.0.0rc1 is a pre-release" in err
    out, err = run("v1.9.0", "v2.0.0", runner="1.6.0.dev3")
    assert out == {"media-capable": "false", "bundle-version": "1.1.0"}
    assert "runner livingproof-sim-mujoco 1.6.0.dev3" in err
    out, err = run("not-a-version", "v2.0.0")
    assert out is None and "not a PEP 440 version" in err
    out, err = run("v1.9.0.post1", "v2.0.0")
    assert out["bundle-version"] == "1.1.0" and err == ""  # a post-release is a release


def test_capability_installs_pinned_packaging_before_the_gate():
    block = WORKFLOW[WORKFLOW.index("id: capability") :]
    assert block.index("pip install --user packaging==") < block.index("<<'PY'")


def test_evidence_verify_and_media_run_in_the_locked_hallway_image():
    assert ".releases.verifier | if .image then .image" in STEPS
    assert "ghcr.io/living-proof-labs/capability-verifier-hallway" not in STEPS  # lock only
    for job in ("evidence", "verify", "scenario-evaluator", "media"):
        body = WORKFLOW.split(f"\n  {job}:\n", 1)[1].split("\n  report:\n", 1)[0]
        head = body.split("    steps:\n", 1)[0]
        assert "image: ${{ needs.plan.outputs.verifier-image }}" in head, job
        assert "options: --user root" in head, job
    # without an image in the lock the jobs still provision the runner themselves
    assert STEPS.count("if: needs.plan.outputs.verifier-image == ''") == 6


# Reporter 3.4.0 release assets. Duration cells are filled only when render's input is the
# bundle manifest, so this pins that release rather than whatever reporter happens to be installed.
_REPORTER_RELEASE = (
    "https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/download/v3.4.0"
)
_REPORTER_WHEELS = {
    "livingproof_common-1.0.0-py3-none-any.whl": (
        "8b7795748b54eeb89610f3716245f45db48ac0474465a5abbf974e7075c1476c"
    ),
    "livingproof_capability_report-3.4.0-py3-none-any.whl": (
        "d688aa86dd4e5fd603587e41e1f494358e58117e311bf1b2ee17b6e55095c496"
    ),
}
# Three trials, four scenario steps. Distinct values so a shared placeholder cannot pass.
_VARIED_DURATION_S = {1: 11.5, 2: 13.25, 3: 8.75}
_DURATION_CELLS = ["11.50 s", "13.25 s", "8.75 s"] * 4


def _download_wheel(dest, name):
    import hashlib
    import urllib.request

    target = dest / name
    url = f"{_REPORTER_RELEASE}/{name}"
    with urllib.request.urlopen(url, timeout=60) as response:
        target.write_bytes(response.read())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert digest == _REPORTER_WHEELS[name], name
    return target


def _reporter_3_4_0(tmp_path):
    """Install the pinned reporter 3.4.0 wheels and return the directory that holds the CLI."""
    import shutil
    import subprocess
    import sys

    venv = tmp_path / "reporter-venv"
    uv = shutil.which("uv")
    if uv:
        subprocess.run([uv, "venv", str(venv)], check=True)
        pip = [uv, "pip", "install", "--python", str(venv / "bin" / "python")]
    else:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        pip = [str(venv / "bin" / "python"), "-m", "pip", "install"]
    wheels = [_download_wheel(tmp_path, name) for name in _REPORTER_WHEELS]
    subprocess.run([*pip, *(str(wheel) for wheel in wheels)], check=True)
    return venv / "bin"


def _bundle_with_varied_durations(tmp_path, monkeypatch):
    """Evidence 2.0.0 fixture, each trial given its own duration_s, exported as bundle 1.1.0."""
    import hashlib
    import importlib.util
    import json
    import shutil

    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.16.1")
    monkeypatch.setenv("SOURCE_COMMIT", "a" * 40)
    evidence = tmp_path / "evidence"
    shutil.copytree(Path(__file__).parent / "fixtures" / "hallway" / "evidence-2.0.0", evidence)
    manifest = json.loads((evidence / "manifest.json").read_text())
    for trial in manifest["trials"]["results"]:
        path = evidence / trial["path"]
        observation = json.loads(path.read_text())
        observation["duration_s"] = _VARIED_DURATION_S[trial["index"]]
        body = (json.dumps(observation, separators=(",", ":")) + "\n").encode()
        path.write_bytes(body)
        trial["sha256"] = hashlib.sha256(body).hexdigest()
    (evidence / "manifest.json").write_text(json.dumps(manifest) + "\n")
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_duration", Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.export(evidence / "manifest.json", tmp_path / "verified", bundle_version="1.1.0")
    bundle = tmp_path / "verified" / "bundle"
    found = {}
    for trial in manifest["trials"]["results"]:
        copied = json.loads((bundle / "evidence" / Path(trial["path"]).name).read_text())
        found[trial["index"]] = copied["duration_s"]
    assert found == _VARIED_DURATION_S
    return bundle


def test_rendered_html_shows_each_trial_duration(tmp_path, monkeypatch):
    """Duration cells follow observation duration_s. An em dash here means the HTML was
    rendered away from the bundle, which is the v1.16.1 walk-hallway failure."""
    import json
    import os
    import re
    import subprocess

    bundle = _bundle_with_varied_durations(tmp_path, monkeypatch)
    job = tmp_path / "job"
    (job / "verified").mkdir(parents=True)
    bundle.rename(job / "verified" / "bundle")
    (job / "links").mkdir()
    (job / "links" / "evidence-links.json").write_text(
        json.dumps(
            {
                "run": "https://example.invalid/run/1",
                "links": [
                    {
                        "label": "MCAP, all 3 trials",
                        "url": "https://example.invalid/mcap",
                        "kind": "mcap",
                    },
                    {
                        "label": "video",
                        "url": "https://example.invalid/pass",
                        "kind": "video",
                        "seed": 1,
                        "status": "PASS",
                    },
                ],
            },
            indent=2,
        )
        + "\n"
    )
    env = dict(os.environ)
    env["PATH"] = f"{_reporter_3_4_0(tmp_path)}{os.pathsep}{env['PATH']}"
    subprocess.run(
        ["bash", "-e", "-c", _step("Validate, then render")], cwd=job, env=env, check=True
    )
    html = (job / "reports" / "report.html").read_text()
    cells = re.findall(r"<td data-label='Duration'><span class='cell-value'>([^<]*)</span>", html)
    assert cells == _DURATION_CELLS
    assert "—" not in cells
    subprocess.run(
        [
            "livingproof-capability-report",
            "render",
            "reports/report.json",
            "--format",
            "markdown",
            "-o",
            "reports/from-report-json.md",
        ],
        cwd=job,
        env=env,
        check=True,
    )
    assert (job / "reports" / "from-report-json.md").read_text() == (
        job / "reports" / "report.md"
    ).read_text()
