# SPDX-License-Identifier: Apache-2.0
"""Real jq and repin.check for resolved pins.

The plan step's shell script is what CI runs. These tests do not use the jq
stub. Each one is a probe that failed when the fill stopped at the plan outputs.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from capability_verifier import definition, evidence_lock, repin
from capability_verifier.resolved_pins import apply_resolved_pins, record_resolved_pins

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".github/scripts/apply_resolved_pins.sh"
VERIFIER_SHA = "a" * 40
OTHER_SHA = "b" * 40
EVAL_SHA = "e" * 40
SPECS_SHA = "1" * 40
RUNNER_SHA = "2" * 40
REPORTER_SHA = "3" * 40
VERIFIER_DIGEST = "ab" * 32
REPORTER_DIGEST = "cd" * 32
VERIFIER_IMAGE = (
    "ghcr.io/living-proof-labs/capability-verifier-hallway:2.11.4@sha256:" + VERIFIER_DIGEST
)
REPORTER_IMAGE = (
    "ghcr.io/living-proof-labs/livingproof-capability-report:3.15.4@sha256:" + REPORTER_DIGEST
)
WRONG_VERIFIER_IMAGE = (
    "ghcr.io/living-proof-labs/capability-verifier-hallway:2.11.5@sha256:" + VERIFIER_DIGEST
)
OTHER_VERIFIER_NAME = "ghcr.io/living-proof-labs/not-the-hallway"
OTHER_DIGEST = "ff" * 32
PYPROJECT = (
    "[tool.uv.sources]\n"
    "livingproof-sim-mujoco = { git = "
    '"https://github.com/Living-Proof-Labs/livingproof-sim-mujoco.git", tag = "v3.2.5" }\n'
)


def _release(tag, commit=None, image=None, digest=None, repository="Living-Proof-Labs/component"):
    entry = {"repository": repository, "tag": tag}
    if commit is not None:
        entry["commit"] = commit
    if image is not None:
        entry["image"] = image
        entry["digest"] = digest
    return entry


def _lock(
    *, verifier_commit=VERIFIER_SHA, verifier_image=True, reporter_image=True, evaluator=EVAL_SHA
):
    verifier = _release(
        "v2.11.4",
        verifier_commit,
        "ghcr.io/living-proof-labs/capability-verifier-hallway" if verifier_image else None,
        "sha256:" + VERIFIER_DIGEST if verifier_image else None,
        "Living-Proof-Labs/capability-verifier",
    )
    reporter = _release(
        "v3.15.4",
        REPORTER_SHA,
        "ghcr.io/living-proof-labs/livingproof-capability-report" if reporter_image else None,
        "sha256:" + REPORTER_DIGEST if reporter_image else None,
        "Living-Proof-Labs/livingproof-capability-report",
    )
    body = {
        "format": "livingproof-verification-lock/1.0.0",
        "configurations": [{"name": "stock"}],
        "scenario": "walk-hallway@0.1.0",
        "world": {"id": "hallway-open"},
        "releases": {
            "specs": _release(
                "v2.3.0", SPECS_SHA, repository="Living-Proof-Labs/robot-capability-specs"
            ),
            "runner": _release(
                "v3.2.5", RUNNER_SHA, repository="Living-Proof-Labs/livingproof-sim-mujoco"
            ),
            "verifier": verifier,
            "reporter": reporter,
        },
    }
    if evaluator is not None:
        body["evaluators"] = {
            "livingproof-evaluator-scenario": _release(
                "v1.5.16",
                evaluator,
                repository="Living-Proof-Labs/livingproof-evaluator-scenario",
            )
        }
    return body


def _pins(**overrides):
    env = {
        "RESOLVED_VERIFIER_COMMIT": VERIFIER_SHA,
        "RESOLVED_VERIFIER_IMAGE": VERIFIER_IMAGE,
        "RESOLVED_REPORTER_IMAGE": REPORTER_IMAGE,
        "RESOLVED_EVALUATOR_COMMIT": EVAL_SHA,
    }
    env.update(overrides)
    return env


def _run(robot: Path, body: dict, pins: dict):
    robot.mkdir()
    (robot / "verification.lock.json").write_text(json.dumps(body))
    (robot / "pyproject.toml").write_text(PYPROJECT)
    env = os.environ.copy()
    for name in (
        "RESOLVED_VERIFIER_COMMIT",
        "RESOLVED_VERIFIER_IMAGE",
        "RESOLVED_REPORTER_IMAGE",
        "RESOLVED_EVALUATOR_COMMIT",
    ):
        env.pop(name, None)
    env.update(pins)
    env["EVIDENCE_SOURCE"] = "robot"
    done = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )
    written = json.loads((robot / "verification.lock.json").read_text())
    return done, written


def _problems(robot: Path) -> list[str]:
    lock = json.loads((robot / "verification.lock.json").read_text())
    return repin.check(lock, (robot / "pyproject.toml").read_text())


def test_a_missing_jq_is_named(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "verification.lock.json").write_text("{}\n")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    done = subprocess.run(
        [shutil.which("bash"), str(SCRIPT)],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0
    assert done.stdout == ""
    assert done.stderr.strip() == "jq is required"


def test_jq_is_the_real_binary():
    """These probes failed under the stub and passed only with jq."""
    found = subprocess.run(["jq", "--version"], check=True, capture_output=True, text=True)
    assert found.stdout.startswith("jq-")


def test_a_supplied_verifier_commit_satisfies_repin_check(tmp_path):
    """(a) The plan used to accept the commit and leave repin without one."""
    robot = tmp_path / "robot"
    done, written = _run(
        robot,
        _lock(verifier_commit=None),
        {"RESOLVED_VERIFIER_COMMIT": OTHER_SHA},
    )
    assert done.returncode == 0, done.stderr
    assert written["releases"]["verifier"]["commit"] == OTHER_SHA
    problems = _problems(robot)
    assert not any("needs a vX.Y.Z tag and a full commit" in item for item in problems), problems
    assert problems == []


def test_a_reporter_tag_without_an_image_is_absent(tmp_path):
    """(b) `:3.15.4@` is jq's composition of a missing image, not a stale ref."""
    robot = tmp_path / "robot"
    done, written = _run(
        robot,
        _lock(reporter_image=False),
        {"RESOLVED_REPORTER_IMAGE": REPORTER_IMAGE},
    )
    assert done.returncode == 0, done.stderr
    assert "the verification lock is stale" not in done.stderr
    reporter = written["releases"]["reporter"]
    assert reporter["image"] == "ghcr.io/living-proof-labs/livingproof-capability-report"
    assert reporter["digest"] == "sha256:" + REPORTER_DIGEST
    assert reporter["tag"] == "v3.15.4"
    assert _problems(robot) == []


def test_the_evidence_lock_records_the_filled_commit(tmp_path):
    """(c) A fresh evidence copy must carry the commit the plan decided."""
    robot = tmp_path / "plan"
    done, written = _run(
        robot, _lock(verifier_commit=None), _pins(RESOLVED_VERIFIER_COMMIT=OTHER_SHA)
    )
    assert done.returncode == 0, done.stderr
    assert written["releases"]["verifier"]["commit"] == OTHER_SHA
    # The evidence job checks out again and projects the original lock.
    fresh = _lock(verifier_commit=None)
    fresh["format"] = "livingproof-verification-lock/0.2.0"
    fresh["plan"] = {"command_speed": 0.5}
    stamped = record_resolved_pins(
        evidence_lock.evidence_document(fresh),
        {
            "VERIFIER_COMMIT": OTHER_SHA,
            "VERIFIER_COMMIT_SOURCE": "input",
            "VERIFIER_IMAGE": VERIFIER_IMAGE,
            "VERIFIER_IMAGE_SOURCE": "input",
            "REPORTER_IMAGE": REPORTER_IMAGE,
            "REPORTER_IMAGE_SOURCE": "input",
            "EVALUATOR_COMMIT": EVAL_SHA,
            "EVALUATOR_COMMIT_SOURCE": "input",
        },
    )
    # Replay reads this commit (definition.run, releases.verifier.commit).
    assert stamped["releases"]["verifier"]["commit"] == OTHER_SHA
    assert stamped["releases"]["verifier"]["image"].endswith("capability-verifier-hallway")
    replayed = apply_resolved_pins(fresh, _pins(RESOLVED_VERIFIER_COMMIT=OTHER_SHA))
    assert replayed["releases"]["verifier"]["commit"] == OTHER_SHA


def test_a_tag_only_lock_with_all_four_inputs_passes_repin_check(tmp_path):
    robot = tmp_path / "robot"
    body = _lock(verifier_commit=None, verifier_image=False, reporter_image=False, evaluator=None)
    body["evaluators"] = {
        "livingproof-evaluator-scenario": {
            "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
            "tag": "v1.5.16",
        }
    }
    done, written = _run(robot, body, _pins())
    assert done.returncode == 0, done.stderr
    assert written["releases"]["verifier"]["commit"] == VERIFIER_SHA
    assert written["releases"]["verifier"]["digest"] == "sha256:" + VERIFIER_DIGEST
    assert written["releases"]["reporter"]["digest"] == "sha256:" + REPORTER_DIGEST
    assert written["evaluators"]["livingproof-evaluator-scenario"]["commit"] == EVAL_SHA
    assert _problems(robot) == []
    fresh = json.loads(json.dumps(body))
    stamped = record_resolved_pins(
        fresh,
        {
            "VERIFIER_COMMIT": VERIFIER_SHA,
            "VERIFIER_COMMIT_SOURCE": "input",
            "VERIFIER_IMAGE": VERIFIER_IMAGE,
            "VERIFIER_IMAGE_SOURCE": "input",
            "REPORTER_IMAGE": REPORTER_IMAGE,
            "REPORTER_IMAGE_SOURCE": "input",
            "EVALUATOR_COMMIT": EVAL_SHA,
            "EVALUATOR_COMMIT_SOURCE": "input",
        },
    )
    assert stamped["releases"]["verifier"]["commit"] == written["releases"]["verifier"]["commit"]
    assert stamped["releases"]["reporter"]["digest"] == written["releases"]["reporter"]["digest"]


def test_an_image_semver_that_differs_from_the_lock_tag_fails(tmp_path):
    robot = tmp_path / "robot"
    original = _lock(verifier_image=False)
    done, written = _run(
        robot,
        original,
        {"RESOLVED_VERIFIER_IMAGE": WRONG_VERIFIER_IMAGE},
    )
    assert done.returncode != 0
    assert (
        "verifier-image semver 2.11.5 does not match releases.verifier.tag v2.11.4" in done.stderr
    )
    assert "image" not in written["releases"]["verifier"]


def test_a_supplied_commit_that_differs_from_the_tags_commit_fails(tmp_path):
    robot = tmp_path / "robot"
    done, written = _run(robot, _lock(), {"RESOLVED_VERIFIER_COMMIT": OTHER_SHA})
    assert done.returncode != 0
    assert (
        f"verifier-commit {OTHER_SHA} does not match the commit for tag v2.11.4 ({VERIFIER_SHA})"
        in done.stderr
    )
    assert written["releases"]["verifier"]["commit"] == VERIFIER_SHA


@pytest.mark.parametrize("path", ["script", "apply", "stamp"])
@pytest.mark.parametrize(
    ("env_name", "ref"),
    [
        (
            "RESOLVED_VERIFIER_IMAGE",
            "ghcr.io/evil/capability-verifier-hallway:2.11.4@sha256:" + VERIFIER_DIGEST,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            "ghcr.io/living-proof-labs-evil/capability-verifier-hallway:2.11.4@sha256:"
            + VERIFIER_DIGEST,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            "ghcr.io/living-proof-labs/capability-verifier-hallway-evil:2.11.4@sha256:"
            + VERIFIER_DIGEST,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            "ghcr.io@evil.com/living-proof-labs/capability-verifier-hallway:2.11.4@sha256:"
            + VERIFIER_DIGEST,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            "ghcr.io/evil/livingproof-capability-report:3.15.4@sha256:" + REPORTER_DIGEST,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            "ghcr.io/living-proof-labs-evil/livingproof-capability-report:3.15.4@sha256:"
            + REPORTER_DIGEST,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            "ghcr.io/living-proof-labs/livingproof-capability-report-evil:3.15.4@sha256:"
            + REPORTER_DIGEST,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            "ghcr.io@evil.com/living-proof-labs/livingproof-capability-report:3.15.4@sha256:"
            + REPORTER_DIGEST,
        ),
    ],
)
def test_another_owner_or_lookalike_name_is_rejected(tmp_path, path, env_name, ref):
    """The shell script and the Python apply/stamp path share one owner pattern."""
    lock = _lock(verifier_image=False, reporter_image=False)
    if path == "script":
        done, _written = _run(tmp_path / "robot", lock, {env_name: ref})
        assert done.returncode != 0, ref
        assert "must be" in done.stderr
        return
    if path == "apply":
        with pytest.raises(SystemExit, match="must be"):
            apply_resolved_pins(lock, {env_name: ref})
        return
    key = "VERIFIER_IMAGE" if env_name.endswith("VERIFIER_IMAGE") else "REPORTER_IMAGE"
    with pytest.raises(SystemExit, match="must be"):
        record_resolved_pins(lock, {key: ref, f"{key}_SOURCE": "input"})


@pytest.mark.parametrize("path", ["script", "apply"])
@pytest.mark.parametrize(
    ("env_name", "ref"),
    [
        ("RESOLVED_VERIFIER_COMMIT", OTHER_SHA),
        ("RESOLVED_VERIFIER_IMAGE", VERIFIER_IMAGE),
    ],
)
def test_a_verifier_input_fails_when_the_lock_has_no_tag(tmp_path, path, env_name, ref):
    lock = _lock(verifier_commit=None, verifier_image=False)
    del lock["releases"]["verifier"]["tag"]
    if path == "script":
        done, written = _run(tmp_path / "robot", lock, {env_name: ref})
        assert done.returncode != 0
        assert "verifier-" in done.stderr
        assert "lock has no tag" in done.stderr
        assert "<missing>" not in done.stderr
        assert "commit" not in written["releases"]["verifier"]
        return
    with pytest.raises(SystemExit, match="lock has no tag"):
        apply_resolved_pins(lock, {env_name: ref})


def _isolated_script(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "tree"
    script = root / ".github" / "scripts" / "apply_resolved_pins.sh"
    script.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script)
    package = root / "src" / "capability_verifier"
    package.mkdir(parents=True)
    shutil.copy(ROOT / "src/capability_verifier/__init__.py", package / "__init__.py")
    shutil.copy(ROOT / "src/capability_verifier/resolved_pins.py", package / "resolved_pins.py")
    return script, package / "resolved_pins.py"


def _run_image(tmp_path: Path, script: Path):
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "verification.lock.json").write_text(json.dumps(_lock(verifier_image=False)))
    env = os.environ.copy()
    for name in (
        "RESOLVED_VERIFIER_COMMIT",
        "RESOLVED_VERIFIER_IMAGE",
        "RESOLVED_REPORTER_IMAGE",
        "RESOLVED_EVALUATOR_COMMIT",
        "PYTHONPATH",
    ):
        env.pop(name, None)
    env["RESOLVED_VERIFIER_IMAGE"] = VERIFIER_IMAGE
    env["EVIDENCE_SOURCE"] = "robot"
    return subprocess.run(
        ["bash", str(script)],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )


def test_a_failed_pattern_load_is_refused(tmp_path):
    script, module = _isolated_script(tmp_path)
    module.unlink()
    done = _run_image(tmp_path, script)
    assert done.returncode != 0
    assert done.stdout == ""
    assert "could not load the image patterns from capability_verifier.resolved_pins" in done.stderr


def test_empty_image_patterns_are_refused(tmp_path):
    script, module = _isolated_script(tmp_path)
    text = module.read_text()
    text = re.sub(
        r"VERIFIER_IMAGE_PATTERN = \([^)]*\)",
        'VERIFIER_IMAGE_PATTERN = ""',
        text,
        count=1,
    )
    text = re.sub(
        r"REPORTER_IMAGE_PATTERN = \([^)]*\)",
        'REPORTER_IMAGE_PATTERN = ""',
        text,
        count=1,
    )
    module.write_text(text)
    done = _run_image(tmp_path, script)
    assert done.returncode != 0
    assert done.stdout == ""
    assert "could not load the image patterns from capability_verifier.resolved_pins" in done.stderr


@pytest.mark.parametrize("path", ["script", "apply"])
@pytest.mark.parametrize("shape", ["absent", "no-tag"])
def test_an_evaluator_commit_without_a_tag_is_refused(tmp_path, path, shape):
    """livingproof-evaluator-scenario has no default. v1.5.0 is the run-integrity pin."""
    lock = _lock(evaluator=None)
    if shape == "no-tag":
        lock["evaluators"] = {
            "livingproof-evaluator-scenario": {
                "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
            }
        }
    if path == "script":
        done, written = _run(
            tmp_path / f"{path}-{shape}", lock, {"RESOLVED_EVALUATOR_COMMIT": EVAL_SHA}
        )
        assert done.returncode != 0
        assert "evaluator-commit: lock has no tag" in done.stderr
        assert "v1.5.0" not in json.dumps(written)
        scenario = (written.get("evaluators") or {}).get("livingproof-evaluator-scenario") or {}
        assert "commit" not in scenario
        return
    with pytest.raises(SystemExit, match="evaluator-commit: lock has no tag"):
        apply_resolved_pins(lock, {"RESOLVED_EVALUATOR_COMMIT": EVAL_SHA})


def test_the_script_writes_the_verifier_commit_into_the_caller_lock(tmp_path):
    """repin --lock caller-verification.lock.json reads this file when a caller passes it."""
    robot = tmp_path / "robot"
    body = _lock(verifier_commit=None)
    robot.mkdir()
    (robot / "verification.lock.json").write_text(json.dumps(body))
    (robot / "caller-verification.lock.json").write_text(json.dumps(body))
    (robot / "pyproject.toml").write_text(PYPROJECT)
    env = os.environ.copy()
    for name in (
        "RESOLVED_VERIFIER_COMMIT",
        "RESOLVED_VERIFIER_IMAGE",
        "RESOLVED_REPORTER_IMAGE",
        "RESOLVED_EVALUATOR_COMMIT",
    ):
        env.pop(name, None)
    env["RESOLVED_VERIFIER_COMMIT"] = OTHER_SHA
    env["EVIDENCE_SOURCE"] = "caller"
    done = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    caller = json.loads((robot / "caller-verification.lock.json").read_text())
    assert caller["releases"]["verifier"]["commit"] == OTHER_SHA
    assert repin.check(caller, PYPROJECT) == []


def test_the_script_refuses_an_image_name_that_differs_from_the_lock(tmp_path):
    robot = tmp_path / "robot"
    body = _lock()
    body["releases"]["verifier"]["image"] = OTHER_VERIFIER_NAME
    done, written = _run(robot, body, {"RESOLVED_VERIFIER_IMAGE": VERIFIER_IMAGE})
    assert done.returncode != 0
    assert (
        f"verifier-image {VERIFIER_IMAGE} does not match releases.verifier.image "
        f"({OTHER_VERIFIER_NAME}); the verification lock is stale"
    ) in done.stderr
    assert written["releases"]["verifier"]["image"] == OTHER_VERIFIER_NAME


def test_apply_refuses_an_image_semver_that_differs_from_the_lock_tag():
    lock = _lock(verifier_image=False)
    with pytest.raises(
        SystemExit,
        match="verifier-image semver 2.11.5 does not match releases.verifier.tag v2.11.4",
    ):
        apply_resolved_pins(lock, {"RESOLVED_VERIFIER_IMAGE": WRONG_VERIFIER_IMAGE})


def test_apply_refuses_a_commit_that_differs_from_the_recorded_commit():
    with pytest.raises(
        SystemExit,
        match=(
            f"verifier-commit {OTHER_SHA} does not match the commit for tag "
            f"v2.11.4 \\({VERIFIER_SHA}\\)"
        ),
    ):
        apply_resolved_pins(_lock(), {"RESOLVED_VERIFIER_COMMIT": OTHER_SHA})


def test_apply_refuses_an_image_name_that_differs_from_the_lock():
    lock = _lock()
    lock["releases"]["verifier"]["image"] = OTHER_VERIFIER_NAME
    with pytest.raises(
        SystemExit,
        match=re.escape(
            f"verifier-image {VERIFIER_IMAGE} does not match releases.verifier.image "
            f"({OTHER_VERIFIER_NAME}); the verification lock is stale"
        ),
    ):
        apply_resolved_pins(lock, {"RESOLVED_VERIFIER_IMAGE": VERIFIER_IMAGE})


def test_apply_refuses_a_digest_that_differs_from_the_lock():
    lock = _lock()
    lock["releases"]["verifier"]["digest"] = "sha256:" + OTHER_DIGEST
    with pytest.raises(
        SystemExit,
        match=re.escape(
            f"verifier-image {VERIFIER_IMAGE} does not match releases.verifier.digest "
            f"(sha256:{OTHER_DIGEST}); the verification lock is stale"
        ),
    ):
        apply_resolved_pins(lock, {"RESOLVED_VERIFIER_IMAGE": VERIFIER_IMAGE})


def test_apply_refuses_a_malformed_commit():
    lock = _lock(verifier_commit=None)
    with pytest.raises(
        SystemExit,
        match=re.escape(
            "verifier-commit must be a 40-character lowercase hexadecimal SHA (got 'not-a-sha')"
        ),
    ):
        apply_resolved_pins(lock, {"RESOLVED_VERIFIER_COMMIT": "not-a-sha"})


def test_the_stamp_records_the_evaluator_commit():
    fresh = _lock(evaluator=None)
    fresh["evaluators"] = {
        "livingproof-evaluator-scenario": {
            "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
            "tag": "v1.5.16",
        }
    }
    stamped = record_resolved_pins(
        fresh,
        {"EVALUATOR_COMMIT": EVAL_SHA, "EVALUATOR_COMMIT_SOURCE": "input"},
    )
    assert stamped["evaluators"]["livingproof-evaluator-scenario"]["commit"] == EVAL_SHA


def _clear_pin_env(monkeypatch):
    for name in (
        "RESOLVED_VERIFIER_COMMIT",
        "RESOLVED_VERIFIER_IMAGE",
        "RESOLVED_REPORTER_IMAGE",
        "RESOLVED_EVALUATOR_COMMIT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_definition_run_checks_out_the_filled_verifier_commit(tmp_path, monkeypatch):
    """Replay reads releases.verifier.commit. The supplied commit has to be applied first."""
    lock = {
        "releases": {
            "verifier": {
                "repository": "Living-Proof-Labs/capability-verifier",
                "tag": "v2.11.4",
            }
        }
    }
    monkeypatch.setattr(definition, "check", lambda definition_path, work: lock)
    seen = {}

    def checkout(repository, commit, dest):
        seen["repository"] = repository
        seen["commit"] = commit
        raise SystemExit("stop after verifier checkout")

    monkeypatch.setattr(definition, "checkout", checkout)
    _clear_pin_env(monkeypatch)
    monkeypatch.setenv("RESOLVED_VERIFIER_COMMIT", OTHER_SHA)
    with pytest.raises(SystemExit, match="stop after verifier checkout"):
        definition.run(tmp_path / "definition.json", tmp_path / "out")
    assert seen == {
        "repository": "Living-Proof-Labs/capability-verifier",
        "commit": OTHER_SHA,
    }


def test_prepare_evidence_inputs_writes_the_filled_lock(tmp_path, monkeypatch):
    robot = tmp_path / "robot"
    robot.mkdir()
    caller = tmp_path / "caller-verification.lock.json"
    caller.write_text("{}\n")
    lock = {
        "format": "livingproof-verification-lock/0.2.0",
        "scenario": "walk-hallway@0.1.0",
        "world": {"id": "hallway-open"},
        "configurations": [{"name": "stock"}],
        "plan": {"command_speed": 0.5},
        "releases": {
            "verifier": {
                "repository": "Living-Proof-Labs/capability-verifier",
                "tag": "v2.11.4",
            }
        },
    }
    _clear_pin_env(monkeypatch)
    monkeypatch.setenv("RESOLVED_VERIFIER_COMMIT", OTHER_SHA)
    assert definition.prepare_evidence_inputs(lock, robot, caller, work=tmp_path / "work") == {
        "EVIDENCE_SOURCE": "caller"
    }
    evidence = json.loads((robot / "verification.lock.json").read_text())
    assert evidence["format"] == "livingproof-verification-lock/1.0.0"
    assert evidence["releases"]["verifier"]["commit"] == OTHER_SHA
