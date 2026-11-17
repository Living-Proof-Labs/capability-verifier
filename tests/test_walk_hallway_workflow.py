# SPDX-License-Identifier: Apache-2.0
"""The reusable walk-hallway workflow: callable, lock-driven, renderer is the reporter image."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from declared_digest import declare_parameters_digest

from capability_verifier import definition

WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/scenario.yml").read_text()
WRAPPER = (Path(__file__).parents[1] / ".github/workflows/walk-hallway.yml").read_text()
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
        'if [ -n "${EVALUATOR_COMMIT:-}" ]; then\n'
        "  if [ -d workflow-src/src ]; then\n"
        "    pin_src=workflow-src/src\n"
        "  else\n"
        "    pin_src=verifier/src\n"
        "  fi\n"
        '  PYTHONPATH="$pin_src" python3 -m capability_verifier.evaluator_pin \\\n'
        '    --repository "$EVALUATOR_REPOSITORY" \\\n'
        '    --tag "$EVALUATOR_TAG" \\\n'
        '    --commit "$EVALUATOR_COMMIT" \\\n'
        "    --verifier verifier\n"
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
    assert "needs: [plan, verify, scenario-evaluator, media, run-integrity]" in report


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
        check=False,
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
    assert 'if [ "${EVIDENCE_SOURCE:-}" != "caller" ]; then' in script
    assert "committed in the robot repository" in script
    assert "pin=caller-verification.lock.json" in script
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
    assert "needs: [plan, verify, scenario-evaluator, media, run-integrity]" in STEPS
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
    extracted = "\n".join(line.removeprefix(indent) for line in WORKFLOW[start:end].splitlines())
    source = (Path(__file__).parents[1] / "src/capability_verifier/collect.py").read_text()
    assert extracted + "\n" == source


# The verify job's catalog step. fetch_hallway_specs.py --from reads the evidence
# lock's declared specs commit and world digest. No other command.
_HALLWAY_CATALOG_STEP = {
    "name": "Scenario catalog from the declared lock",
    "run": "uv run --no-sync python tools/fetch_hallway_specs.py --from evidence/manifest.json",
}


def _tools_that_read_the_hallway_catalog():
    """Tool scripts that read the checkout fetch_hallway_specs.py writes."""
    root = Path(__file__).parents[1] / "tools"
    found = []
    for path in sorted(root.glob("*.py")):
        if path.name == "fetch_hallway_specs.py":
            continue
        text = path.read_text()
        if (
            ".specs-hallway" in text
            or "load_parameters(" in text
            or "from capability_verifier.hallway import thresholds" in text
            or "grading_inputs" in text
        ):
            found.append(path.name)
    return tuple(found)


def test_every_job_that_reads_hallway_specs_fetches_them_first():
    """A job that reads the locked catalog fetches it before that read.

    run_scenario_evaluator.py calls hallway.thresholds, which reads
    .specs-hallway/specs/scenarios/walk-hallway/parameters.json.
    verify_hallway.py reads that same checkout. v1.20.1 runs scenario-evaluator
    without fetch_hallway_specs.py, so the parameter file is missing.
    """
    import yaml

    tools = _tools_that_read_the_hallway_catalog()
    assert "run_scenario_evaluator.py" in tools
    assert "verify_hallway.py" in tools
    workflows = Path(__file__).parents[1] / ".github" / "workflows"
    readers = []
    for path in sorted(workflows.glob("*.yml")):
        document = yaml.safe_load(path.read_text())
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            steps = job.get("steps") or []
            runs = [step.get("run") if isinstance(step.get("run"), str) else "" for step in steps]
            reading = [i for i, run in enumerate(runs) if any(name in run for name in tools)]
            if not reading:
                continue
            where = f"{path.name} job {job_id}"
            readers.append(where)
            fetching = [i for i, run in enumerate(runs) if "fetch_hallway_specs.py" in run]
            assert fetching, f"{where} reads the hallway catalog and does not fetch it"
            assert min(fetching) < min(reading), f"{where} fetches the catalog after reading it"
            assert steps[min(fetching)] == _HALLWAY_CATALOG_STEP, where
            assert "permissions" not in job, where
            checkout = next(
                step
                for step in steps[: min(fetching)]
                if str(step.get("uses", "")).startswith("actions/checkout@")
                and step.get("with", {}).get("repository")
                == "${{ needs.plan.outputs.verifier-repository }}"
            )
            assert checkout["with"]["ref"] == "${{ needs.plan.outputs.verifier-commit }}"
            assert checkout["with"]["persist-credentials"] is False
            assert "path" not in checkout["with"]
    assert readers == [
        "scenario.yml job verify",
        "scenario.yml job scenario-evaluator",
    ]


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
    assert "needs: [plan, verify, scenario-evaluator, media, run-integrity]" in report_head
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
        check=False,
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
        (
            "run --no-sync python tools/verify_hallway.py --declared --bundle-version 1.1.0 "
            "evidence/manifest.json verified"
        )
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


def test_collect_then_render_keeps_thresholds_in_an_existing_reports_directory(tmp_path):
    """Collect writes reports/ first. Render must still run and checksum thresholds.json.

    v1.21.0 added a collect step that copies cited thresholds.json into reports/,
    then the render step ran `mkdir reports` and failed with 'File exists' for
    every caller (capability-verifier#99). These two workflow steps run in that
    order here so the regression fails in this repo rather than in a caller.
    """
    work = tmp_path / "job"
    work.mkdir()
    cited = b'{"limits":{"exit_segment":0.75}}\n'
    digest = hashlib.sha256(cited).hexdigest()
    manifest = work / "verified" / "bundle" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"results": [{"id": "playground-seed-01"}]}) + "\n")
    results = work / "evaluator-results" / "livingproof-scenario"
    results.mkdir(parents=True)
    document = {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": "livingproof-scenario", "version": "1.2.1"},
        "inputs": [{"role": "thresholds", "uri": "thresholds.json", "sha256": digest}],
        "scenario": {"trial_id": "playground-seed-01", "steps": []},
    }
    (results / "playground-seed-01.json").write_text(json.dumps(document) + "\n")
    (results / "thresholds.json").write_bytes(cited)

    subprocess.run(
        ["bash", "-e", "-c", _step("Append each evaluator's result documents")],
        cwd=work,
        check=True,
    )
    assert (work / "reports" / "thresholds.json").read_bytes() == cited

    _run(work, _step("Validate, then render"), "livingproof-capability-report", "1.1.0")
    assert (work / "reports" / "thresholds.json").read_bytes() == cited
    assert (work / "reports" / "report.json").is_file()
    sums = (work / "reports" / "SHA256SUMS").read_text()
    assert f"{digest}  thresholds.json" in sums


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
            check=False,
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
    assert STEPS.count("if: needs.plan.outputs.verifier-image == ''") == 7


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
    # The committed trace steps farther from the route than its stored maximum.
    # This render only needs a gradable trial, so the copy's trace stays inside that maximum.
    for trial in manifest["trials"]["results"]:
        path = evidence / trial["path"]
        observation = json.loads(path.read_text())
        for sample in observation["trace"]["samples"]:
            sample[2] = 0.0
        body = (json.dumps(observation) + "\n").encode()
        path.write_bytes(body)
        trial["sha256"] = hashlib.sha256(body).hexdigest()
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


_EVIDENCE = Path(__file__).parent / "fixtures" / "hallway" / "evidence-2.0.0"
_FINDING = {
    "kind": "test-system",
    "name": "reproducibility",
    "detail": (
        "Identical trials disagreed on trajectory hash or outcome. "
        "This is a test-system reproducibility finding, not a robot FAIL."
    ),
    "trajectory_sha256": ["a" * 64, "b" * 64],
    "outcomes": ["crossed_exit", "crossed_exit"],
}
_UV_STUB = """#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["CALLS"]).open("a").write(" ".join(args) + "\\n")
if "livingproof_sim_mujoco.evidence" not in args:
    sys.exit("uv stub expected the evidence command, got: " + " ".join(args))
code = int(os.environ["EVIDENCE_EXIT"])
if code == 4:
    dest = Path(args[-1])
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(os.environ["EVIDENCE_FIXTURE"], dest)
raise SystemExit(code)
"""


def _produced(dest: Path) -> Path:
    """Simulated livingproof-sim-mujoco 2.0.0 evidence with a reproducibility finding."""
    shutil.copytree(_EVIDENCE, dest)
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["trials"]["reproducibility"] = {
        "compared": True,
        "identical": False,
        "finding": _FINDING,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest) + "\n")
    return dest


def _run_evidence(tmp_path, code, fixture=None):
    """Run the evidence step the way ``definition.run`` does. Verify is separate."""
    robot = tmp_path / "robot"
    robot.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uv"
    stub.write_text(_UV_STUB)
    stub.chmod(0o755)
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "EVIDENCE_EXIT": str(code),
        "ADAPTER": "robot.adapter:ROBOT",
        "CONFIGURATION": "stock",
    }
    if fixture is not None:
        env["EVIDENCE_FIXTURE"] = str(fixture)
    script = definition.step(WORKFLOW, "Evidence (observations only")
    try:
        outputs = definition.sh(script, robot, env, tmp_path / "github-output.txt")
    except definition.DefinitionError as err:
        return {"continued": False, "error": str(err), "calls": calls.read_text(), "robot": robot}
    return {"continued": True, "outputs": outputs, "calls": calls.read_text(), "robot": robot}


def _run_verify(work: Path):
    """Run the assess step under ``bash -e``, which is how ``definition.sh`` runs it."""
    root = Path(__file__).parents[1]
    declare_parameters_digest(work / "evidence", root / ".specs-hallway")
    for name in ("src", "tools", "pyproject.toml", "uv.lock", "hallway-specs.lock.json", ".venv"):
        target = root / name
        if target.exists():
            (work / name).symlink_to(target)
    env = os.environ.copy()
    env["BUNDLE_VERSION"] = "1.1.0"
    if not env.get("LIVINGPROOF_VERIFIER_VERSION"):
        env["LIVINGPROOF_VERIFIER_VERSION"] = "dev"
    if not env.get("SOURCE_COMMIT"):
        env["SOURCE_COMMIT"] = "0" * 40
    script = definition.step(WORKFLOW, "Assess every trial")
    return subprocess.run(
        ["bash", "-e", "-c", script],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


def test_evidence_branches_on_the_exit_code_and_verify_needs_that_job():
    root = Path(__file__).parents[1]
    evidence = WORKFLOW.split("\n  evidence:\n", 1)[1].split("\n  verify:\n", 1)[0]
    verify = WORKFLOW.split("\n  verify:\n", 1)[1].split("\n  scenario-evaluator:\n", 1)[0]
    head = verify.split("    steps:\n", 1)[0]
    script = definition.step(WORKFLOW, "Evidence (observations only")
    assess = definition.step(WORKFLOW, "Assess every trial")
    same = definition.step(
        definition.procedure_text(root, "walk-hallway"), "Evidence (observations only"
    )
    assert same == script
    assert "continue-on-error" not in evidence
    assert "set +e" in script and "code=$?" in script
    assert 'if [ "$code" -eq 4 ]' in script
    assert "continuing to verify" in script
    assert "verify will not run" in script
    assert 'exit "$code"' in script
    assert 'python -m livingproof_sim_mujoco.evidence "$ADAPTER"' in script
    assert "without a test-system reproducibility finding" in script
    assert "needs: [plan, evidence]" in head
    assert "\n    if:" not in head
    assert "set +e" in assess
    assert 'if [ "$code" -ne 0 ]' in assess
    assert 'exit "$code"' in assess
    assert "finding is on the record; exiting 4" in assess
    assert "verify_hallway.py" in assess
    assert "if: always()" in verify


def test_evidence_exit_0_continues(tmp_path):
    done = _run_evidence(tmp_path, 0)
    assert done["continued"] is True, done.get("error")
    assert done["outputs"] == {"evidence-exit": "0"}
    assert "livingproof_sim_mujoco.evidence" in done["calls"]


def test_evidence_exit_1_stops_before_verify(tmp_path):
    """A hard failure never reaches verify. The Actions ``needs:`` edge is the same rule."""
    done = _run_evidence(tmp_path, 1)
    assert done["continued"] is False
    assert "exit 1" in done["error"]
    assert "verify_hallway.py" not in done["calls"]
    assert not (done["robot"] / "artifacts").exists()


def test_evidence_exit_4_verify_prints_the_summary_then_fails(tmp_path):
    """Exit 4 leaves the manifest in place. Verify prints the finding, writes it, then exits 4.

    The fixture trials are inconclusive, so the repeated-outcome grade is
    INSUFFICIENT_EVIDENCE. The finding stays test-system.
    """
    done = _run_evidence(tmp_path, 4, _produced(tmp_path / "produced"))
    assert done["continued"] is True, done.get("error")
    assert done["outputs"]["evidence-exit"] == "4"
    manifest = done["robot"] / "artifacts" / "evidence" / "stock" / "manifest.json"
    recorded = json.loads(manifest.read_text())
    assert recorded["format"] == "livingproof-hallway-evidence/2.0.0"
    assert recorded["trials"]["reproducibility"]["finding"]["kind"] == "test-system"
    assert recorded["trials"]["reproducibility"]["finding"]["name"] == "reproducibility"
    work = tmp_path / "verifier"
    work.mkdir()
    shutil.copytree(manifest.parent, work / "evidence")
    verified = _run_verify(work)
    assert verified.returncode == 4, verified.stderr
    assert "test-system reproducibility:" in verified.stdout
    assert _FINDING["detail"] in verified.stdout
    assert "exiting 4" in verified.stderr
    grade = verified.stdout.splitlines()[0]
    assert grade.endswith(": INSUFFICIENT_EVIDENCE")
    report = json.loads((work / "verified" / "verification.json").read_text())
    assert report["summary"]["grade"] == "INSUFFICIENT_EVIDENCE"
    assert report["summary"]["aggregation"]["id"] == "repeated-outcome"
    assert report["summary"]["k"] is None
    assert report["findings"][0]["kind"] == "test-system"
    assert report["findings"][0]["name"] == "reproducibility"
    assert [trial["status"] for trial in report["trials"]] == ["INSUFFICIENT_EVIDENCE"] * 3
    bundle = json.loads((work / "verified" / "bundle" / "manifest.json").read_text())
    assert bundle["results"][0]["provenance"]["findings"][0]["kind"] == "test-system"
    assert bundle["results"][0]["original_result"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_evidence_exit_4_without_a_finding_stops(tmp_path):
    """Exit 4 with no test-system finding is a failed evidence step. Verify is not run."""
    done = _run_evidence(tmp_path, 4, _EVIDENCE)
    assert done["continued"] is False
    assert "exit 4" in done["error"]
    manifest = done["robot"] / "artifacts" / "evidence" / "stock" / "manifest.json"
    recorded = json.loads(manifest.read_text())
    assert recorded["trials"]["reproducibility"]["finding"] is None


def _run_assess_stub(work: Path, code: int):
    """Run the assess script with ``uv`` exiting ``code``, as an older verifier would."""
    bindir = work / "bin"
    bindir.mkdir()
    stub = bindir / "uv"
    stub.write_text('#!/bin/sh\necho "$*" >> "$CALLS"\nexit "$STUB_EXIT"\n')
    stub.chmod(0o755)
    calls = work / "calls.txt"
    calls.write_text("")
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["CALLS"] = str(calls)
    env["STUB_EXIT"] = str(code)
    env["BUNDLE_VERSION"] = "1.1.0"
    script = definition.step(WORKFLOW, "Assess every trial")
    done = subprocess.run(
        ["bash", "-e", "-c", script],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    return done, calls.read_text()


def test_an_older_verifier_exit_0_still_fails_verify_when_the_finding_is_present(tmp_path):
    """The workflow gate, not the locked verifier, fails the job after a disagreement."""
    work = tmp_path / "verifier"
    shutil.copytree(_produced(tmp_path / "produced"), work / "evidence")
    done, calls = _run_assess_stub(work, 0)
    assert done.returncode == 4, done.stderr
    assert "finding is on the record; exiting 4" in done.stderr
    assert "verify_hallway.py" in calls


def test_assess_keeps_a_nonzero_exit_from_the_verifier(tmp_path):
    work = tmp_path / "verifier"
    shutil.copytree(_produced(tmp_path / "produced"), work / "evidence")
    done, calls = _run_assess_stub(work, 2)
    assert done.returncode == 2, done.stderr
    assert "on the record" not in done.stderr
    assert "verify_hallway.py" in calls


def test_assess_exits_0_when_an_older_verifier_returns_0_and_trials_agree(tmp_path):
    work = tmp_path / "verifier"
    shutil.copytree(_EVIDENCE, work / "evidence")
    done, calls = _run_assess_stub(work, 0)
    assert done.returncode == 0, done.stderr
    assert "verify_hallway.py" in calls


def _step_head(name: str) -> str:
    block = WORKFLOW[WORKFLOW.index(f"- name: {name}") :]
    return block[: block.index("run: |")]


def _refs_tree(robot: Path) -> None:
    robot.mkdir(parents=True)
    verifier = {
        "repository": "Example/verifier",
        "tag": "v1.16.1",
        "commit": "a" * 40,
        "image": "ghcr.io/example/hallway",
        "digest": "sha256:" + "1" * 64,
    }
    reporter = {
        "image": "ghcr.io/example/reporter",
        "tag": "v3.3.1",
        "digest": "sha256:" + "2" * 64,
    }
    configurations = [{"name": "stock"}]

    def body(commit):
        return {
            "configurations": configurations,
            "releases": {"verifier": verifier, "reporter": reporter},
            "evaluators": {
                "livingproof-evaluator-scenario": {
                    "repository": "Example/evaluator",
                    "tag": "v1.2.3",
                    "commit": commit,
                }
            },
        }

    (robot / "verification.lock.json").write_text(json.dumps(body("f" * 40)))
    (robot / "caller-verification.lock.json").write_text(json.dumps(body("e" * 40)))


# The verifier runtime has no jq. This reader covers the filters that step uses,
# so the caller-path assertion does not depend on a host jq.
_JQ_STUB = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
cleaned = []
i = 0
while i < len(args):
    if args[i] in ("-c", "-r", "-n", "-C", "-e"):
        i += 1
        continue
    if args[i] == "--argjson":
        i += 3
        continue
    cleaned.append(args[i])
    i += 1
files = [item for item in cleaned if item.endswith(".json")]
filt = next((item for item in cleaned if item not in files), "")
doc = json.loads(Path(files[-1]).read_text()) if files else {}


def emit(value):
    if isinstance(value, (list, dict)):
        print(json.dumps(value, separators=(",", ":")))
    else:
        print("" if value is None else value)


evaluators = (doc.get("evaluators") or {}).get("livingproof-evaluator-scenario") or {}
verifier = (doc.get("releases") or {}).get("verifier") or {}
reporter = (doc.get("releases") or {}).get("reporter") or {}
if "configurations[].name" in filt:
    emit([item["name"] for item in doc.get("configurations") or []])
elif "].commit" in filt:
    emit(evaluators.get("commit", ""))
elif "].repository" in filt:
    emit(evaluators.get("repository", ""))
elif "].tag" in filt:
    emit(evaluators.get("tag", ""))
elif "verifier.repository" in filt:
    emit(verifier.get("repository", ""))
elif "verifier.commit" in filt:
    emit(verifier.get("commit", ""))
elif "verifier.tag" in filt:
    emit(str(verifier.get("tag", "")).removeprefix("v"))
elif "if .image" in filt:
    if verifier.get("image"):
        tag = str(verifier.get("tag", "")).removeprefix("v")
        emit(f"{verifier['image']}:{tag}@{verifier['digest']}")
    else:
        emit("")
elif "reporter" in filt:
    tag = str(reporter.get("tag", "")).removeprefix("v")
    emit(f"{reporter['image']}:{tag}@{reporter['digest']}")
elif ".scenario" in filt:
    emit(doc.get("scenario") or "")
elif ".world.id" in filt:
    emit((doc.get("world") or {}).get("id") or "")
else:
    emit("")
"""


def _run_refs(robot: Path, source: str):
    _refs_tree(robot)
    output = robot / "github-output.txt"
    bindir = robot / "bin"
    bindir.mkdir()
    jq = bindir / "jq"
    jq.write_text(_JQ_STUB)
    jq.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_OUTPUT"] = str(output)
    env["ONLY"] = ""
    env["EVIDENCE_SOURCE"] = source
    env.pop("PYTHONPATH", None)
    done = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", _step("Refs from verification.lock.json")],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )
    text = output.read_text() if output.is_file() else ""
    return done, text


def test_a_committed_caller_lock_is_rejected_on_the_robot_path(tmp_path):
    """No verification-lock input must not read a caller lock committed in the robot."""
    assert "EVIDENCE_SOURCE:" in _step_head("Refs from verification.lock.json")
    rejected, _output = _run_refs(tmp_path / "robot-path", "robot")
    assert rejected.returncode != 0, rejected.stderr
    assert "committed in the robot repository" in rejected.stderr
    unset, _output = _run_refs(tmp_path / "unset", "")
    assert unset.returncode != 0, unset.stderr
    assert "committed in the robot repository" in unset.stderr
    accepted, outputs = _run_refs(tmp_path / "caller-path", "caller")
    assert accepted.returncode == 0, accepted.stderr
    assert f"evaluator-commit={'e' * 40}" in outputs
    assert f"evaluator-commit={'f' * 40}" not in outputs


def test_the_plan_job_receives_the_evaluator_tag():
    """The tag read from the lock is an input of the step that enforces it."""
    assert "steps.lock.outputs.evaluator-tag" in _step_head("Every ref is a released tag")


def test_the_scenario_evaluator_job_receives_the_evaluator_tag():
    assert "needs.plan.outputs.evaluator-tag" in _step_head(
        "Scenario evaluator, one result file per trial"
    )


_PIN_REPO = "Living-Proof-Labs/livingproof-evaluator-scenario"
_PIN_TAG = "v1.2.0"
_PIN_COMMIT = "c0b11bf0e3b555638baa064214793bae125e5a8b"

_EVALUATOR_UV_STUB = """#!/bin/sh
echo "$*" >> "$CALLS"
if [ "$1" = sync ]; then
  python3 -m venv evaluator/.venv
  site=$(evaluator/.venv/bin/python -c 'import site; print(next(p for p in site.getsitepackages() if p.endswith("site-packages")))')
  mkdir -p "$site/livingproof_scenario_parameters"
  printf '%s\\n' 'MARKER = "evaluator-deps"' > "$site/livingproof_scenario_parameters/__init__.py"
  # What hatch-vcs builds: the tag's version once the tag is in the checkout.
  info="$site/livingproof_evaluator_scenario-0.dist-info"
  mkdir -p "$info"
  printf 'Metadata-Version: 2.1\\nName: livingproof-evaluator-scenario\\nVersion: %s\\n' \
    "${EVALUATOR_BUILT_VERSION:-${EVALUATOR_TAG#v}}" > "$info/METADATA"
  exit 0
fi
if [ "$1" = run ]; then
  shift
  if [ "$1" = --no-sync ]; then
    shift
  fi
  if [ "$1" = python ]; then
    shift
  fi
  if [ -x .venv/bin/python ]; then
    exec .venv/bin/python "$@"
  fi
  exec python3 "$@"
fi
exit 0
"""

_GIT_STUB = """#!/bin/sh
if [ "$1" = ls-remote ]; then
  printf '%s\\trefs/tags/%s^{}\\n' "$GIT_TAG_COMMIT" "$GIT_TAG_NAME"
  exit 0
fi
if [ "$1" = -C ]; then
  echo "git $*" >> "${CALLS:-/dev/null}"
  shift 2
  case "$1 $2" in
    "rev-parse HEAD") echo "$EVALUATOR_COMMIT"; exit 0 ;;
    "rev-parse --verify") echo "$GIT_TAG_COMMIT"; exit 0 ;;
    "fetch -q"|"tag -d") exit 0 ;;
  esac
fi
echo "unexpected git $*" >&2
exit 1
"""


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _installed_pin(root: Path, tag: str, commit: str) -> None:
    dependency = f"livingproof-evaluator-scenario @ git+https://github.com/{_PIN_REPO}.git@{tag}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "capability-verifier"\n'
        'version = "0.0.0"\n'
        "dependencies = [\n"
        f'  "{dependency}",\n'
        "]\n"
    )
    source = f"https://github.com/{_PIN_REPO}.git?rev={tag}#{commit}"
    (root / "uv.lock").write_text(
        "[[package]]\n"
        'name = "livingproof-evaluator-scenario"\n'
        'version = "1.2.0"\n'
        f'source = {{ git = "{source}" }}\n'
    )


def _marker_package(directory: Path, name: str, marker: str) -> None:
    package = directory / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(f'MARKER = "{marker}"\n')


def _venv_markers(root: Path, package_marker: str, dependency_marker: str) -> None:
    subprocess.run(["python3", "-m", "venv", str(root / ".venv")], check=True)
    py = root / ".venv" / "bin" / "python"
    site = Path(
        subprocess.check_output(
            [
                str(py),
                "-c",
                (
                    "import site\n"
                    "print(next(p for p in site.getsitepackages() if p.endswith('site-packages')))\n"
                ),
            ],
            text=True,
        ).strip()
    )
    _marker_package(site, "livingproof_evaluator_scenario", package_marker)
    _marker_package(site, "livingproof_scenario_parameters", dependency_marker)


def _evaluator_tool(root: Path) -> None:
    tools = root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "run_scenario_evaluator.py").write_text(
        "import livingproof_evaluator_scenario as package\n"
        "import livingproof_scenario_parameters as dependency\n"
        "print('package-marker', package.MARKER)\n"
        "print('dependency-marker', dependency.MARKER)\n"
        "print('package', package.__file__)\n"
        "print('dependency', dependency.__file__)\n"
    )


def _pin_env(env: dict, *, tag: str, commit: str, git_commit: str) -> None:
    env["EVALUATOR_REPOSITORY"] = _PIN_REPO
    env["EVALUATOR_TAG"] = tag
    env["EVALUATOR_COMMIT"] = commit
    env["GIT_TAG_NAME"] = tag
    env["GIT_TAG_COMMIT"] = git_commit
    env.pop("PYTHONPATH", None)


def _run_plan_pin(work: Path, *, installed_commit: str, git_commit: str):
    verifier = work / "verifier"
    _installed_pin(verifier, _PIN_TAG, installed_commit)
    repin = verifier / "src" / "capability_verifier"
    repin.mkdir(parents=True)
    (repin / "repin.py").write_text("raise SystemExit(0)\n")
    (work / "workflow-src").symlink_to(Path(__file__).parents[1])
    bindir = work / "bin"
    bindir.mkdir()
    _write_executable(bindir / "git", _GIT_STUB)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["EVIDENCE_SOURCE"] = "robot"
    _pin_env(env, tag=_PIN_TAG, commit=_PIN_COMMIT, git_commit=git_commit)
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", _step("Every ref is a released tag")],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


def _run_scenario(
    work: Path,
    *,
    tag: str,
    commit: str,
    installed_tag: str,
    installed_commit: str,
    git_commit: str,
    checkout: bool,
    built_version: str | None = None,
):
    _installed_pin(work, installed_tag, installed_commit)
    _venv_markers(work, "stale", "verifier-deps")
    _evaluator_tool(work)
    if checkout:
        _marker_package(work / "evaluator" / "src", "livingproof_evaluator_scenario", "checkout")
    (work / "workflow-src").symlink_to(Path(__file__).parents[1])
    bindir = work / "bin"
    bindir.mkdir()
    _write_executable(bindir / "uv", _EVALUATOR_UV_STUB)
    _write_executable(bindir / "git", _GIT_STUB)
    calls = work / "calls.txt"
    calls.write_text("")
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["CALLS"] = str(calls)
    _pin_env(env, tag=tag, commit=commit, git_commit=git_commit)
    if built_version is not None:
        env["EVALUATOR_BUILT_VERSION"] = built_version
    done = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", _step("Scenario evaluator, one result file per trial")],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    return done, calls.read_text()


def test_the_plan_refuses_an_evaluator_the_verifier_does_not_resolve(tmp_path):
    """The plan fails when uv.lock resolved a different commit than the lock."""
    done = _run_plan_pin(tmp_path / "plan", installed_commit="a" * 40, git_commit=_PIN_COMMIT)
    assert done.returncode != 0, done.stderr
    assert "the verifier resolves" in done.stderr


def test_scenario_evaluator_refuses_a_tag_that_is_not_the_pinned_commit(tmp_path):
    done, _calls = _run_scenario(
        tmp_path / "tag",
        tag=_PIN_TAG,
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit=_PIN_COMMIT,
        git_commit="b" * 40,
        checkout=True,
    )
    assert done.returncode != 0, done.stdout + done.stderr
    assert "not the locked commit" in done.stderr


def test_scenario_evaluator_refuses_a_pin_with_no_tag(tmp_path):
    done, _calls = _run_scenario(
        tmp_path / "notag",
        tag="",
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit=_PIN_COMMIT,
        git_commit=_PIN_COMMIT,
        checkout=True,
    )
    assert done.returncode != 0, done.stdout + done.stderr
    assert "tag is required" in done.stderr


def test_scenario_evaluator_refuses_an_installed_pin_that_differs(tmp_path):
    done, _calls = _run_scenario(
        tmp_path / "installed",
        tag=_PIN_TAG,
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit="a" * 40,
        git_commit=_PIN_COMMIT,
        checkout=True,
    )
    assert done.returncode != 0, done.stdout + done.stderr
    assert "the verifier resolves" in done.stderr


def test_scenario_evaluator_rejects_a_site_packages_import(tmp_path):
    """A stale copy in the verifier environment must not run when the checkout is missing it."""
    done, _calls = _run_scenario(
        tmp_path / "stale",
        tag=_PIN_TAG,
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit=_PIN_COMMIT,
        git_commit=_PIN_COMMIT,
        checkout=False,
    )
    assert done.returncode != 0, done.stdout + done.stderr
    assert "not evaluator/src" in done.stderr


def test_scenario_evaluator_imports_the_checkout_and_its_own_dependencies(tmp_path):
    """The import is evaluator/src, and dependencies come from that checkout's install."""
    done, calls = _run_scenario(
        tmp_path / "checkout",
        tag=_PIN_TAG,
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit=_PIN_COMMIT,
        git_commit=_PIN_COMMIT,
        checkout=True,
    )
    assert done.returncode == 0, done.stderr
    assert "package-marker checkout" in done.stdout
    assert "dependency-marker evaluator-deps" in done.stdout
    assert "package " + str(tmp_path / "checkout" / "evaluator" / "src") in done.stdout
    assert "sync --locked --directory evaluator" in calls


def test_scenario_evaluator_fetches_the_locked_tag_before_the_build(tmp_path):
    """#125: the tag is fetched into the checkout, checked, and only then built."""
    done, calls = _run_scenario(
        tmp_path / "release",
        tag=_PIN_TAG,
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit=_PIN_COMMIT,
        git_commit=_PIN_COMMIT,
        checkout=True,
    )
    assert done.returncode == 0, done.stderr
    fetch = f"git -C evaluator fetch -q --no-tags --depth=1 origin +refs/tags/{_PIN_TAG}"
    lines = calls.splitlines()
    [fetched] = [i for i, line in enumerate(lines) if line.startswith(fetch)]
    [synced] = [i for i, line in enumerate(lines) if "sync --locked --directory evaluator" in line]
    assert fetched < synced


def test_scenario_evaluator_refuses_a_build_that_reports_a_dev_version(tmp_path):
    """#125: a checkout that still builds as 0.1.dev1 does not write results."""
    done, _calls = _run_scenario(
        tmp_path / "dev",
        tag=_PIN_TAG,
        commit=_PIN_COMMIT,
        installed_tag=_PIN_TAG,
        installed_commit=_PIN_COMMIT,
        git_commit=_PIN_COMMIT,
        checkout=True,
        built_version="0.1.dev1",
    )
    assert done.returncode != 0, done.stdout
    assert f"reports 0.1.dev1, not the locked {_PIN_TAG}" in done.stderr
    assert "package-marker" not in done.stdout


def test_run_integrity_job_is_enabled_and_checks_out_the_v1_0_0_tag():
    """The job is on by default and checks out evaluators.livingproof-evaluator-run by tag.

    The pin is tag-only (no commit). When the lock omits the pin, plan defaults
    to Living-Proof-Labs/livingproof-evaluator-run @ v1.0.0 so the job still runs.
    """
    import yaml

    refs = _step("Refs from verification.lock.json")
    assert "run-integrity-commit=" not in refs
    assert "Living-Proof-Labs/livingproof-evaluator-run" in refs
    assert 'tag="v1.0.0"' in refs or "tag=v1.0.0" in refs or "tag='v1.0.0'" in refs
    assert "run-integrity-tag=" in refs
    assert "run-integrity-commit:" not in STEPS
    assert "needs.plan.outputs.run-integrity-commit" not in STEPS
    assert "ref: ${{ needs.plan.outputs.run-integrity-tag }}" in STEPS

    doc = yaml.safe_load(WORKFLOW)
    job = doc["jobs"]["run-integrity"]
    assert job["if"] == "${{ !cancelled() }}"
    assert "run-integrity-commit" not in doc["jobs"]["plan"]["outputs"]
    checkout = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run Integrity evaluator at the locked pin"
    )
    assert checkout["with"]["ref"] == "${{ needs.plan.outputs.run-integrity-tag }}"
    assert checkout["with"]["repository"] == "${{ needs.plan.outputs.run-integrity-repository }}"
