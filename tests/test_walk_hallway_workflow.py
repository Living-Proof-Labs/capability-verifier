# SPDX-License-Identifier: Apache-2.0
"""The reusable walk-hallway workflow: callable, lock-driven, renderer is the reporter image."""

from pathlib import Path

WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/walk-hallway.yml").read_text()
STEPS = "\n".join(line for line in WORKFLOW.splitlines() if not line.lstrip().startswith("#"))


# Published livingproof-verification pins. Each robot commit contains
# verification.lock.json, and those calls do not pass verification-lock, so a
# released walk-hallway.yml keeps today's plan, repin and evidence steps for them.
CURRENT_PINS = (
    (
        "unitree-g1-stock",
        "Living-Proof-Labs/livingproof-unitree-g1",
        "862cf979f57e40d6158070c59b432b0cf4eaa4bd",
        "44acb9ed412a04fc458addba47fdf019b6d21027",
    ),
    (
        "unitree-g1-playground",
        "Living-Proof-Labs/livingproof-unitree-g1",
        "862cf979f57e40d6158070c59b432b0cf4eaa4bd",
        "44acb9ed412a04fc458addba47fdf019b6d21027",
    ),
    (
        "unitree-go1-playground",
        "Living-Proof-Labs/livingproof-unitree-go1",
        "c56816dc1cf8bfe3be9c1385a609dd6c7de97694",
        "67bb6bbc330645f9fee66b9ab16f6c055cd313f8",
    ),
    (
        "booster-t1-stock",
        "Living-Proof-Labs/livingproof-booster-t1",
        "9bbcd3001998ab242e63273ea070b483d73c24d5",
        "67bb6bbc330645f9fee66b9ab16f6c055cd313f8",
    ),
)


def test_is_a_reusable_workflow_with_fan_out_inputs():
    assert "workflow_call:" in STEPS
    for name in (
        "repository:",
        "ref:",
        "configurations:",
        "verification-lock:",
        "artifact-prefix:",
        "comment:",
    ):
        assert name in STEPS, name
    assert "report-artifacts:" in STEPS


def test_every_ref_comes_from_the_robot_lock():
    assert "repin.py --root robot --check" in STEPS
    assert "needs.plan.outputs.reporter-image" in STEPS
    assert "needs.plan.outputs.verifier-commit" in STEPS
    assert "LIVINGPROOF_VERIFIER_VERSION: ${{ needs.plan.outputs.verifier-version }}" in STEPS
    assert "livingproof-capability-report:1." not in STEPS
    assert '"@" + .releases.reporter.digest' in STEPS


def test_current_pins_keep_the_robot_lock_and_a_caller_lock_overrides_it():
    """verification-lock defaults to empty, so the four published calls are unchanged.

    A set verification-lock is written even when the robot file is present.
    The same write runs when the robot file is absent. repin --check then reads
    the caller lock. scenario-evaluator stays parallel to verify, and report needs it.
    """
    import yaml

    from capability_verifier import evidence_lock

    assert [pin[0] for pin in CURRENT_PINS] == [
        "unitree-g1-stock",
        "unitree-g1-playground",
        "unitree-go1-playground",
        "booster-t1-stock",
    ]
    doc = yaml.safe_load(WORKFLOW)
    inputs = doc[True]["workflow_call"]["inputs"]
    assert inputs["verification-lock"]["default"] == ""
    assert inputs["verification-lock"]["type"] == "string"
    assert _step("Every ref is a released tag") == (
        'if [ "$EVIDENCE_SOURCE" = "caller" ]; then\n'
        "  python3 verifier/src/capability_verifier/repin.py --root robot "
        f"--lock {evidence_lock.CALLER_LOCK_NAME} --check\n"
        "else\n"
        "  python3 verifier/src/capability_verifier/repin.py --root robot --check\n"
        "fi\n"
    )
    writer_if = (
        "${{ inputs.verification-lock != '' || steps.robot-lock.outputs.present == 'false' }}"
    )
    assert WORKFLOW.count(f"if: {writer_if}") == 9
    writers = []
    for name, job in doc["jobs"].items():
        for step in job.get("steps") or []:
            if "write_evidence_inputs.sh" in (step.get("run") or ""):
                writers.append(name)
                assert step["if"] == writer_if
    assert writers == ["plan", "evidence", "media"]
    report = WORKFLOW.split("\n  report:\n", 1)[1].split("\n  comment:\n", 1)[0]
    assert "needs: [plan, verify, scenario-evaluator, media]" in report


def _release(commit, digest, *, tag, image, repository="Org/component"):
    return {
        "repository": repository,
        "tag": tag,
        "commit": commit,
        "image": image,
        "digest": digest,
    }


def _evidence_lock(verifier, reporter, evaluator, *, world, speed):
    """A 0.2.0 lock whose evidence fields and evaluator pin are the arguments."""
    return {
        "format": "livingproof-verification-lock/0.2.0",
        "scenario": "walk-hallway@0.1.0",
        "releases": {
            "specs": _release(
                "a" * 40, "sha256:" + "a" * 64, tag="v2.0.2", image="ghcr.io/example/specs"
            ),
            "runner": _release(
                "b" * 40, "sha256:" + "b" * 64, tag="v2.0.3", image="ghcr.io/example/runner"
            ),
            "verifier": verifier,
            "reporter": reporter,
        },
        "world": {"id": world, "version": "0.1.0"},
        "configurations": [
            {
                "name": "playground",
                "version": "1.0.0",
                "path": "configurations/playground.json",
                "sha256": "d" * 64,
            }
        ],
        "plan": {"trials": {"count": 3}, "command_speed": speed},
        "evaluators": {"livingproof-evaluator-scenario": evaluator},
    }


def _write_lock(robot, caller_dir, relative, *, copy):
    import os
    import subprocess

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if copy:
        env["COPY_CALLER_LOCK"] = "1"
    return subprocess.run(
        [
            "sh",
            str(Path(__file__).parents[1] / ".github/scripts/write_evidence_inputs.sh"),
            str(robot),
            str(caller_dir),
            relative,
            str(Path(__file__).parents[1]),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def _plan_outputs(robot):
    """The plan job's ref outputs, read the way its jq reads the selected lock.

    The runtime image has no jq. The workflow script's jq is pinned below so this
    reader and that script stay on the same files and fields.
    """
    import json

    script = _step("Refs from verification.lock.json")
    assert "jq -r '.releases.verifier.commit' verification.lock.json" in script
    assert "jq -r '.releases.verifier.repository' verification.lock.json" in script
    assert (
        'jq -r \'.releases.verifier | if .image then .image + ":" + '
        '(.tag | ltrimstr("v")) + "@" + .digest else "" end\' verification.lock.json'
    ) in script
    assert "pin=verification.lock.json" in script
    assert (
        "if [ -f caller-verification.lock.json ]; then\n  pin=caller-verification.lock.json\nfi\n"
        in script
    )
    for field in ("repository", "commit", "tag"):
        assert (
            f'jq -r \'(.evaluators // {{}})["livingproof-evaluator-scenario"].{field} // empty\' "$pin"'
        ) in script
    evidence = json.loads((robot / "verification.lock.json").read_text())
    pin_path = robot / "caller-verification.lock.json"
    pin = json.loads(pin_path.read_text()) if pin_path.is_file() else evidence
    verifier = evidence["releases"]["verifier"]
    reporter = evidence["releases"]["reporter"]
    evaluator = (pin.get("evaluators") or {}).get("livingproof-evaluator-scenario") or {}
    image = ""
    if verifier.get("image"):
        image = f"{verifier['image']}:{verifier['tag'].removeprefix('v')}@{verifier['digest']}"
    return {
        "verifier-repository": verifier["repository"],
        "verifier-commit": verifier["commit"],
        "verifier-image": image,
        "reporter-image": (
            f"{reporter['image']}:{reporter['tag'].removeprefix('v')}@{reporter['digest']}"
        ),
        "evaluator-repository": evaluator.get("repository") or "",
        "evaluator-commit": evaluator.get("commit") or "",
        "evaluator-tag": evaluator.get("tag") or "",
    }


def _image(ref):
    return f"{ref['image']}:{ref['tag'].removeprefix('v')}@{ref['digest']}"


def test_caller_lock_wins_over_a_robot_lock_for_verifier_image_and_evaluator(tmp_path):
    """Both locks exist. Checkout ref, hallway image and evaluator pin follow the caller."""
    import hashlib
    import json

    from capability_verifier import evidence_lock

    caller_verifier = _release(
        "b" * 40,
        "sha256:" + "2" * 64,
        tag="v1.18.0",
        image="ghcr.io/example/caller-hallway",
        repository="Example/caller-verifier",
    )
    robot_verifier = _release(
        "a" * 40,
        "sha256:" + "1" * 64,
        tag="v1.16.1",
        image="ghcr.io/example/robot-hallway",
        repository="Example/robot-verifier",
    )
    caller_reporter = _release(
        "c" * 40,
        "sha256:" + "3" * 64,
        tag="v3.5.0",
        image="ghcr.io/example/caller-reporter",
    )
    robot_reporter = _release(
        "d" * 40,
        "sha256:" + "4" * 64,
        tag="v3.3.1",
        image="ghcr.io/example/robot-reporter",
    )
    caller_eval = {"repository": "Example/caller-evaluator", "tag": "v1.2.3", "commit": "e" * 40}
    robot_eval = {"repository": "Example/robot-evaluator", "tag": "v9.9.9", "commit": "f" * 40}
    caller_lock = _evidence_lock(
        caller_verifier, caller_reporter, caller_eval, world="caller-world", speed=0.8
    )
    robot_lock = {
        "format": "livingproof-verification-lock/1.0.0",
        "scenario": "walk-hallway@0.1.0",
        "releases": {"verifier": robot_verifier, "reporter": robot_reporter},
        "world": {"id": "robot-world", "version": "0.1.0"},
        "configurations": [
            {
                "name": "stock",
                "version": "1.0.0",
                "path": "configurations/stock.json",
                "sha256": "0" * 64,
            }
        ],
        "plan": {"trials": {"count": 1}, "command_speed": 0.1},
        "evaluators": {"livingproof-evaluator-scenario": robot_eval},
    }
    caller = tmp_path / "caller"
    (caller / "verifications").mkdir(parents=True)
    (caller / "verifications" / "lock.json").write_text(json.dumps(caller_lock) + "\n")
    robot = tmp_path / "robot"
    robot.mkdir()
    original = (json.dumps(robot_lock) + "\n").encode()
    (robot / "verification.lock.json").write_bytes(original)
    done = _write_lock(robot, caller, "verifications/lock.json", copy=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "source=caller"
    digest = hashlib.sha256(original).hexdigest()
    assert done.stderr.splitlines() == evidence_lock.evidence_source_lines(digest)
    written = json.loads((robot / "verification.lock.json").read_text())
    assert written["releases"]["verifier"] == caller_verifier
    assert written["releases"]["reporter"] == caller_reporter
    assert written["world"]["id"] == "caller-world"
    assert written["plan"]["command_speed"] == 0.8
    assert written["configurations"][0]["name"] == "playground"
    assert written["overridden_robot_lock"] == {
        "path": "verification.lock.json",
        "sha256": digest,
    }
    assert "evaluators" not in written  # evidence.py's document; the pin is on the caller copy
    outputs = _plan_outputs(robot)
    assert outputs["verifier-repository"] == "Example/caller-verifier"
    assert outputs["verifier-commit"] == "b" * 40
    assert outputs["verifier-image"] == _image(caller_verifier)
    assert outputs["reporter-image"] == _image(caller_reporter)
    assert outputs["evaluator-repository"] == "Example/caller-evaluator"
    assert outputs["evaluator-commit"] == "e" * 40
    assert outputs["evaluator-tag"] == "v1.2.3"
    assert robot_verifier["commit"] not in outputs.values()
    assert robot_verifier["digest"] not in outputs["verifier-image"]
    assert robot_eval["commit"] not in outputs.values()
    assert "ghcr.io/example/robot-hallway" not in outputs["verifier-image"]


def test_caller_lock_without_a_robot_lock_logs_that_and_supplies_the_pins(tmp_path):
    import json

    from capability_verifier import evidence_lock

    verifier = _release(
        "b" * 40,
        "sha256:" + "2" * 64,
        tag="v1.18.0",
        image="ghcr.io/example/caller-hallway",
        repository="Example/caller-verifier",
    )
    reporter = _release(
        "c" * 40, "sha256:" + "3" * 64, tag="v3.5.0", image="ghcr.io/example/caller-reporter"
    )
    evaluator = {"repository": "Example/caller-evaluator", "tag": "v1.2.3", "commit": "e" * 40}
    lock = _evidence_lock(verifier, reporter, evaluator, world="caller-world", speed=0.8)
    caller = tmp_path / "caller"
    (caller / "verifications").mkdir(parents=True)
    (caller / "verifications" / "lock.json").write_text(json.dumps(lock) + "\n")
    robot = tmp_path / "robot"
    robot.mkdir()
    done = _write_lock(robot, caller, "verifications/lock.json", copy=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "source=caller"
    assert done.stderr.splitlines() == evidence_lock.evidence_source_lines(None)
    written = json.loads((robot / "verification.lock.json").read_text())
    assert "overridden_robot_lock" not in written
    outputs = _plan_outputs(robot)
    assert outputs["verifier-commit"] == "b" * 40
    assert outputs["verifier-image"] == _image(verifier)
    assert outputs["evaluator-commit"] == "e" * 40
    assert outputs["evaluator-repository"] == "Example/caller-evaluator"


def test_no_caller_lock_uses_the_robot_lock_and_no_evaluator_default(tmp_path):
    import json

    verifier = _release(
        "a" * 40,
        "sha256:" + "1" * 64,
        tag="v1.16.1",
        image="ghcr.io/example/robot-hallway",
        repository="Example/robot-verifier",
    )
    reporter = _release(
        "d" * 40, "sha256:" + "4" * 64, tag="v3.3.1", image="ghcr.io/example/robot-reporter"
    )
    robot_lock = {
        "format": "livingproof-verification-lock/1.0.0",
        "configurations": [{"name": "playground"}],
        "releases": {"verifier": verifier, "reporter": reporter},
    }
    robot = tmp_path / "robot"
    robot.mkdir()
    original = (json.dumps(robot_lock) + "\n").encode()
    (robot / "verification.lock.json").write_bytes(original)
    done = _write_lock(robot, tmp_path / "caller", "", copy=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "source=robot"
    assert done.stderr == ""
    assert (robot / "verification.lock.json").read_bytes() == original
    outputs = _plan_outputs(robot)
    assert outputs["verifier-repository"] == "Example/robot-verifier"
    assert outputs["verifier-commit"] == "a" * 40
    assert outputs["verifier-image"] == _image(verifier)
    assert outputs["reporter-image"] == _image(reporter)
    assert outputs["evaluator-repository"] == ""
    assert outputs["evaluator-commit"] == ""
    assert outputs["evaluator-tag"] == ""


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
    assert "needs.plan.outputs.verifier-commit" in scenario
    assert "needs.plan.outputs.verifier-image" in scenario
    assert "needs.plan.outputs.evaluator-repository" in scenario
    assert "needs.plan.outputs.evaluator-commit" in scenario
    assert "ghcr.io/" not in scenario
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


# Reporter 3.6.0, the release livingproof-verification pins. Duration cells are filled
# only when render's input is the bundle manifest. 3.4.0's markdown multiplies the
# stored Wilson bounds, so it cannot render a compared-repeat run whose bounds are null.
_REPORTER_RELEASE = (
    "https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/download/v3.6.0"
)
_REPORTER_WHEELS = {
    "livingproof_common-1.0.0-py3-none-any.whl": (
        "8b7795748b54eeb89610f3716245f45db48ac0474465a5abbf974e7075c1476c"
    ),
    "livingproof_capability_report-3.6.0-py3-none-any.whl": (
        "c600c502f6e8722c358264cf9f104bba759188334e6329821b463fbaab862824"
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


def _reporter_3_6_0(tmp_path):
    """Install the pinned reporter 3.6.0 wheels and return the directory that holds the CLI."""
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
    env["PATH"] = f"{_reporter_3_6_0(tmp_path)}{os.pathsep}{env['PATH']}"
    subprocess.run(
        ["bash", "-e", "-c", _step("Validate, then render")], cwd=job, env=env, check=True
    )
    html = (job / "reports" / "report.html").read_text()
    cells = re.findall(r"<td data-label='Duration'><span class='cell-value'>([^<]*)</span>", html)
    assert cells == _DURATION_CELLS
    assert "—" not in cells
    # The run interval is null, so v3.6.0 draws no confidence range. Step rings still
    # count the stored trials; that count is the reporter follow-up, not this render.
    assert "95% confidence range" not in html
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
