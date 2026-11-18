# SPDX-License-Identifier: Apache-2.0
"""How the evidence step ended is recorded in the evidence artifact (#155).

The evidence step's shell sees the runner's exit code, and only the workflow knows how the
step ended. These tests run the workflow's own scripts, under `sh -e` like the verifier
image's job shell, with a stub in place of `uv run ... livingproof_sim_mujoco.evidence`:

- a clean run, a non-zero exit, exit 4, and a runner killed by a signal are recorded by the
  evidence step itself;
- a step whose own shell dies (job timeout, cancellation, the shell killed) writes nothing, so
  the `always()` step after it records `unfinished` with the step's outcome;
- with no manifest.json, the run-integrity job still writes a Run Integrity result, by
  running the evaluator on a stand-in manifest, instead of failing on the missing input.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from capability_verifier import definition

ROOT = Path(__file__).parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/scenario.yml"
WORKFLOW = WORKFLOW_PATH.read_text()
RUN_INTEGRITY_REPO = "https://github.com/Living-Proof-Labs/livingproof-evaluator-run.git"

# Stands in for `uv run python -m livingproof_sim_mujoco.evidence ...`.
UV = """#!/bin/sh
case "$EVIDENCE_MODE" in
  exit)
    dir=$(printf '%s\\n' "$@" | sed -n '8p')
    if [ -n "$WRITE_MANIFEST" ]; then mkdir -p "$dir"; echo '{}' > "$dir/manifest.json"; fi
    exit "$EVIDENCE_EXIT" ;;
  kill-self) kill -"$EVIDENCE_SIGNAL" $$ ;;
  kill-parent) kill -KILL "$PPID"; sleep 5 ;;
esac
"""


def _steps():
    document = yaml.safe_load(WORKFLOW)
    return document["jobs"]["evidence"]["steps"]


def _step(prefix):
    return next(s for s in _steps() if str(s.get("name", "")).startswith(prefix))


def _env(tmp_path, **extra):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "uv").write_text(UV)
    (bindir / "uv").chmod(0o755)
    temp = tmp_path / "runner-temp"
    temp.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(temp),
        "CONFIGURATION": "go1",
        "ADAPTER": "lpl_go1.adapter:ROBOT",
    }
    env.update(extra)
    return env


def _run(script, cwd, env):
    return subprocess.run(
        ["sh", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def _go(tmp_path, *, outcome, lock=None, manifest=False, **env_extra):
    """Run the evidence step then the always() step; the second reads the first's file."""
    robot = tmp_path / "robot"
    robot.mkdir(exist_ok=True)
    if lock is not None:
        (robot / "verification.lock.json").write_text(json.dumps(lock))
    env = _env(tmp_path, **env_extra)
    if manifest:
        env["WRITE_MANIFEST"] = "1"
    evidence = _run(definition.step(WORKFLOW, "Evidence (observations only"), robot, env)
    finish_env = {**env, "STEP_OUTCOME": outcome(evidence) if callable(outcome) else outcome}
    finish = _run(definition.step(WORKFLOW, "Evidence ending"), robot, finish_env)
    path = robot / "artifacts" / "evidence" / "go1" / "evidence-ending.json"
    record = json.loads(path.read_text()) if path.exists() else None
    return evidence, finish, record


LOCK = {"plan": {"trials": {"count": 3}}}


def test_a_clean_run_records_exit_0(tmp_path):
    evidence, finish, record = _go(
        tmp_path,
        outcome="success",
        lock=LOCK,
        manifest=True,
        EVIDENCE_MODE="exit",
        EVIDENCE_EXIT="0",
    )
    assert evidence.returncode == 0, evidence.stderr
    assert finish.returncode == 0, finish.stderr
    assert record == {
        "format": "livingproof-evidence-ending/0.1",
        "configuration": "go1",
        "ended": "exited",
        "exit_code": 0,
        "signal": None,
        "step_outcome": "success",
        "manifest_present": True,
        "planned_trials": 3,
    }


def test_a_non_zero_exit_is_recorded_and_still_fails_the_step(tmp_path):
    evidence, finish, record = _go(
        tmp_path, outcome="failure", EVIDENCE_MODE="exit", EVIDENCE_EXIT="1"
    )
    assert evidence.returncode == 1
    assert finish.returncode == 0, finish.stderr
    assert (record["ended"], record["exit_code"], record["signal"]) == ("exited", 1, None)
    assert record["step_outcome"] == "failure"
    assert record["manifest_present"] is False
    assert record["planned_trials"] is None


def test_exit_4_with_a_manifest_is_recorded_as_exit_4(tmp_path):
    # The step continues only when the manifest records a test-system finding; with `{}` it
    # does not, so here the step fails, and the exit code is 4 in the record either way.
    evidence, _, record = _go(
        tmp_path,
        outcome="failure",
        lock=LOCK,
        manifest=True,
        EVIDENCE_MODE="exit",
        EVIDENCE_EXIT="4",
    )
    assert evidence.returncode == 4
    assert record["exit_code"] == 4 and record["ended"] == "exited"
    assert record["manifest_present"] is True


@pytest.mark.parametrize(("signal", "code", "name"), [("KILL", 137, "KILL"), ("TERM", 143, "TERM")])
def test_a_runner_killed_by_a_signal_is_recorded_with_the_signal(tmp_path, signal, code, name):
    evidence, finish, record = _go(
        tmp_path,
        outcome="failure",
        lock=LOCK,
        EVIDENCE_MODE="kill-self",
        EVIDENCE_SIGNAL=signal,
    )
    assert evidence.returncode == code
    assert finish.returncode == 0, finish.stderr
    assert record["ended"] == "signalled"
    assert record["exit_code"] == code
    assert record["signal"] == name
    assert record["manifest_present"] is False


@pytest.mark.parametrize("outcome", ["cancelled", "failure"])
def test_a_step_that_never_finished_is_recorded_as_unfinished(tmp_path, outcome):
    # The step's own shell was killed (a job timeout or cancellation, or the shell itself):
    # it wrote nothing, and the always() step records what the workflow knows.
    evidence, finish, record = _go(
        tmp_path, outcome=outcome, lock=LOCK, EVIDENCE_MODE="kill-parent"
    )
    assert evidence.returncode != 0
    assert finish.returncode == 0, finish.stderr
    assert record["ended"] == "unfinished"
    assert record["exit_code"] is None and record["signal"] is None
    assert record["step_outcome"] == outcome
    assert record["planned_trials"] == 3


def test_a_stale_file_from_an_earlier_attempt_is_not_read(tmp_path):
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    (temp / "evidence-ending.json").write_text(
        json.dumps({"ended": "exited", "exit_code": 0, "signal": None})
    )
    _, _, record = _go(tmp_path, outcome="cancelled", EVIDENCE_MODE="kill-parent")
    assert record["ended"] == "unfinished"


def test_the_record_is_written_even_when_the_runner_made_no_evidence_directory(tmp_path):
    _, _, record = _go(tmp_path, outcome="failure", EVIDENCE_MODE="exit", EVIDENCE_EXIT="2")
    assert record is not None
    # The step's own temp file stays out of the uploaded directory.
    uploaded = tmp_path / "robot" / "artifacts" / "evidence" / "go1"
    assert sorted(p.name for p in uploaded.iterdir()) == ["evidence-ending.json"]


def test_the_always_step_follows_the_evidence_step_before_the_upload():
    steps = _steps()
    evidence = _step("Evidence (observations only")
    ending = _step("Evidence ending")
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert steps.index(evidence) < steps.index(ending) < steps.index(upload)
    assert evidence["id"] == "evidence"
    assert ending["if"] == "${{ always() }}"
    assert ending["env"]["STEP_OUTCOME"] == "${{ steps.evidence.outcome }}"
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "artifacts/evidence/${{ matrix.configuration }}/"


def test_an_evidence_failure_still_stops_grading():
    jobs = yaml.safe_load(WORKFLOW)["jobs"]
    verify = jobs["verify"]
    assert "evidence" in verify["needs"]
    assert "if" not in verify
    assert "evidence" in jobs["scenario-evaluator"]["needs"]
    assert "if" not in jobs["scenario-evaluator"]
    # Only run-integrity runs after a failed evidence job.
    assert jobs["run-integrity"]["if"] == "${{ !cancelled() }}"


def _checkout(tmp_path, tag):
    checkout = tmp_path / "run-integrity"
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", tag, RUN_INTEGRITY_REPO, str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        pytest.fail(f"could not clone {RUN_INTEGRITY_REPO}@{tag}: {clone.stderr}")
    sync = subprocess.run(
        ["uv", "sync", "--locked", "--directory", str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sync.returncode == 0, sync.stderr
    return checkout


def _integrity(tmp_path, ending):
    work = tmp_path / "work"
    (work / "evidence").mkdir(parents=True)
    if ending is not None:
        (work / "evidence" / "evidence-ending.json").write_text(json.dumps(ending))
    env = {
        **os.environ,
        "GITHUB_WORKSPACE": str(work),
        "PYTHONPATH": str(tmp_path / "run-integrity" / "src"),
        "CONFIGURATION": "go1",
        "GITHUB_RUN_ID": "1234",
    }
    (work / "run-integrity").symlink_to(tmp_path / "run-integrity")
    script = definition.step(WORKFLOW, "Run Integrity, one result")
    done = subprocess.run(
        ["sh", "-e", "-c", script], cwd=work, env=env, capture_output=True, text=True, check=False
    )
    out = work / "evaluator-results" / "livingproof-run-integrity" / "result.json"
    return done, (json.loads(out.read_text()) if out.exists() else None)


KILLED = {
    "format": "livingproof-evidence-ending/0.1",
    "configuration": "go1",
    "ended": "signalled",
    "exit_code": 137,
    "signal": "KILL",
    "step_outcome": "failure",
    "manifest_present": False,
    "planned_trials": 3,
}


@pytest.mark.parametrize("tag", ["v1.0.0", "v1.2.0"])
def test_no_manifest_still_gives_a_run_integrity_result(tmp_path, tag):
    _checkout(tmp_path, tag)
    done, result = _integrity(tmp_path, KILLED)
    assert done.returncode == 0, done.stderr
    assert result is not None
    run = result["integrity"]["run"]
    assert run["test_system_status"] == "ERROR"
    assert run["conclusion"] == "INCONCLUSIVE"
    assert run["success"] is False
    completeness = next(c for c in result["integrity"]["checks"] if c["id"] == "completeness")
    assert [f["category"] for f in completeness["findings"]] == ["missing-trial"] * 3
    assert {f["classification"] for f in completeness["findings"]} == {"test_system"}
    # The result's input is the stand-in, named as such, never a manifest.json of the run.
    uris = [i.get("uri", "") for i in result["inputs"]]
    assert any("no-evidence" in uri for uri in uris), result["inputs"]
    stand_in = json.loads((tmp_path / "work" / "no-evidence" / "manifest.json").read_text())
    assert stand_in["stand_in"]["ending"] == KILLED
    assert "without a manifest" in done.stderr


def test_no_manifest_and_no_planned_count_keeps_the_job_green_and_says_so(tmp_path):
    _checkout(tmp_path, "v1.0.0")
    done, result = _integrity(tmp_path, {**KILLED, "planned_trials": None})
    assert done.returncode == 0, done.stderr
    assert result is None
    assert "::warning::" in done.stdout + done.stderr
    done, result = _integrity(tmp_path / "again", None)
    assert done.returncode == 0 and result is None


def test_the_run_integrity_job_downloads_what_it_always_did():
    job = yaml.safe_load(WORKFLOW)["jobs"]["run-integrity"]
    text = json.dumps(job["steps"])
    assert "download-artifact" in text
    script = definition.step(WORKFLOW, "Run Integrity, one result")
    assert 'manifest="$GITHUB_WORKSPACE/evidence/manifest.json"' in script
    assert "stand_in" in script
