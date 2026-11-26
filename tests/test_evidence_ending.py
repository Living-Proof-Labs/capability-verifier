# SPDX-License-Identifier: Apache-2.0
"""How the evidence step ended is recorded in the evidence artifact (#155).

The evidence step's shell sees the runner's exit code, and only the workflow knows how the
step ended. These tests run the evidence job's scripts under `bash -eo pipefail`, that
job's shell, with a stub in place of `uv run ... livingproof_sim_mujoco.evidence`:

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
    # GitHub's `shell: bash` is `bash --noprofile --norc -eo pipefail`.
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
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


@pytest.mark.parametrize(
    "payload",
    ["{}", '{"ended": "exited"}', '{"ended": "exited", "exit_code": 1}'],
    ids=["empty-object", "missing-exit-and-signal", "missing-signal"],
)
def test_an_incomplete_temp_record_is_still_recorded_as_unfinished(tmp_path, payload):
    # Invalid JSON is already unfinished. A valid object with missing keys must not
    # be unlinked and then crash, leaving the artifact with no record.
    robot = tmp_path / "robot"
    robot.mkdir()
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    (temp / "evidence-ending.json").write_text(payload)
    env = {
        **os.environ,
        "RUNNER_TEMP": str(temp),
        "CONFIGURATION": "go1",
        "STEP_OUTCOME": "failure",
    }
    finish = _run(definition.step(WORKFLOW, "Evidence ending"), robot, env)
    record_path = robot / "artifacts" / "evidence" / "go1" / "evidence-ending.json"
    assert finish.returncode == 0, finish.stderr
    assert record_path.is_file()
    record = json.loads(record_path.read_text())
    assert record["ended"] == "unfinished"
    assert record["exit_code"] is None and record["signal"] is None


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '"exited"',
        "null",
        '{"ended": "weird", "exit_code": 1, "signal": null}',
        '{"ended": "unfinished", "exit_code": null, "signal": null}',
        '{"ended": "exited", "exit_code": "1", "signal": null}',
        '{"ended": "exited", "exit_code": true, "signal": null}',
        '{"ended": "signalled", "exit_code": 137, "signal": 9}',
    ],
    ids=[
        "list",
        "string",
        "null",
        "unknown-ending",
        "unfinished-from-the-step",
        "string-exit-code",
        "boolean-exit-code",
        "numeric-signal",
    ],
)
def test_a_temp_record_of_the_wrong_shape_is_recorded_as_unfinished(tmp_path, payload):
    robot = tmp_path / "robot"
    robot.mkdir()
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    (temp / "evidence-ending.json").write_text(payload)
    env = {
        **os.environ,
        "RUNNER_TEMP": str(temp),
        "CONFIGURATION": "go1",
        "STEP_OUTCOME": "failure",
    }
    finish = _run(definition.step(WORKFLOW, "Evidence ending"), robot, env)
    assert finish.returncode == 0, finish.stderr
    record = json.loads((robot / "artifacts/evidence/go1/evidence-ending.json").read_text())
    assert record["ended"] == "unfinished"
    assert record["exit_code"] is None and record["signal"] is None
    # What the step left is kept in the job log, since the record cannot carry it.
    assert "evidence-ending.json" in finish.stdout


def test_the_temp_file_is_kept_until_the_record_is_written(tmp_path):
    # `artifacts` is a file, so the record cannot be written. The step fails, and what the
    # evidence step left is still there to read, not unlinked first.
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "artifacts").write_text("in the way")
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    saved = '{"ended": "exited", "exit_code": 1, "signal": null}'
    (temp / "evidence-ending.json").write_text(saved)
    env = {
        **os.environ,
        "RUNNER_TEMP": str(temp),
        "CONFIGURATION": "go1",
        "STEP_OUTCOME": "failure",
    }
    finish = _run(definition.step(WORKFLOW, "Evidence ending"), robot, env)
    assert finish.returncode != 0
    assert (temp / "evidence-ending.json").read_text() == saved


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
    # Only run-integrity runs after a failed evidence job, and only when evaluators
    # is empty. Empty is the default, so a caller that does not set it still gets the job.
    assert jobs["run-integrity"]["if"] == "${{ !cancelled() && inputs.evaluators == '' }}"


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


def _integrity(tmp_path, ending, name="work"):
    work = tmp_path / name
    (work / "evidence").mkdir(parents=True, exist_ok=True)
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
    assert any("stand-in" in uri for uri in uris), result["inputs"]
    stand_in = json.loads(
        (tmp_path / "work" / "no-evidence" / "stand-in-manifest.json").read_text()
    )
    assert stand_in["stand_in"]["ending"] == KILLED
    assert "without a manifest" in done.stderr


# Why a no-manifest run gets no Run Integrity result, per case. The evaluator refuses a plan
# of 0 trials (exit 2, nothing written), so a stand-in with 0 cannot produce one either.
NO_RESULT = [
    pytest.param({**KILLED, "planned_trials": None}, "planned_trials is null", id="null-count"),
    pytest.param({**KILLED, "planned_trials": 0}, "planned_trials is 0", id="zero-count"),
    pytest.param({**KILLED, "planned_trials": -2}, "planned_trials is -2", id="negative-count"),
    pytest.param(
        {**KILLED, "planned_trials": "3"},
        "planned_trials is not a whole number: '3'",
        id="malformed-count",
    ),
    pytest.param(
        {**KILLED, "planned_trials": True},
        "planned_trials is not a whole number: True",
        id="boolean-count",
    ),
    pytest.param(
        {k: v for k, v in KILLED.items() if k != "planned_trials"},
        "planned_trials is missing",
        id="no-count-key",
    ),
    pytest.param(["not", "an", "object"], "is not a JSON object", id="not-an-object"),
    pytest.param(None, "evidence-ending.json is missing", id="no-ending-file"),
]


@pytest.mark.parametrize(("ending", "reason"), NO_RESULT)
def test_no_result_fails_the_job_on_purpose_and_says_why(tmp_path, ending, reason):
    # Exit 0 with nothing written made upload-artifact (if-no-files-found: error) fail the
    # job later with a message about files. The job fails here, naming the cause, and leaves
    # a file saying so, so the upload that follows has something to keep.
    job = yaml.safe_load(WORKFLOW)["jobs"]["run-integrity"]
    upload = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "evaluator-results/"
    _checkout(tmp_path, "v1.0.0")
    done, result = _integrity(tmp_path, ending)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert result is None
    output = done.stdout + done.stderr
    assert "::error::" in output and reason in output, output
    work = tmp_path / "work"
    marker = work / "evaluator-results" / "livingproof-run-integrity" / "no-result.txt"
    assert marker.is_file()
    assert reason in marker.read_text()
    # No stand-in is evaluated for these, and nothing that looks like a result is written.
    assert not (work / "no-evidence").exists()
    assert [p.name for p in marker.parent.iterdir()] == ["no-result.txt"]


def test_the_run_integrity_job_downloads_what_it_always_did():
    job = yaml.safe_load(WORKFLOW)["jobs"]["run-integrity"]
    text = json.dumps(job["steps"])
    assert "download-artifact" in text
    script = definition.step(WORKFLOW, "Run Integrity, one result")
    assert 'manifest="$GITHUB_WORKSPACE/evidence/manifest.json"' in script
    assert "stand_in" in script


def test_the_specs_record_step_waits_for_the_manifest_it_reads():
    # tools/fetch_hallway_specs.py --from evidence/manifest.json cannot run without one.
    steps = yaml.safe_load(WORKFLOW)["jobs"]["run-integrity"]["steps"]
    specs = next(s for s in steps if "fetch_hallway_specs.py" in s.get("run", ""))
    assert specs["if"] == "${{ hashFiles('evidence/manifest.json') != '' }}"


def test_the_specs_record_is_kept_with_the_result_when_there_is_one(tmp_path):
    _checkout(tmp_path, "v1.0.0")
    work = tmp_path / "work"
    (work / "evidence").mkdir(parents=True, exist_ok=True)
    (work / "evidence" / "specs-files.json").write_text("{}")
    done, result = _integrity(tmp_path, KILLED)
    assert done.returncode == 0, done.stderr
    kept = work / "evaluator-results" / "livingproof-run-integrity" / "specs-files.json"
    assert kept.read_text() == "{}"
    assert result is not None


SCHEMA_PATH = ROOT / "src/capability_verifier/livingproof-evidence-ending-0.1.schema.json"


def _schema_validator():
    from jsonschema import Draft202012Validator

    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_the_ending_schema_is_draft_2020_12_and_names_its_format():
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "livingproof-evidence-ending/0.1"
    _schema_validator()


@pytest.mark.parametrize(
    ("outcome", "mode", "extra"),
    [
        ("success", "exit", {"EVIDENCE_EXIT": "0"}),
        ("failure", "exit", {"EVIDENCE_EXIT": "1"}),
        ("failure", "kill-self", {"EVIDENCE_SIGNAL": "KILL"}),
        ("failure", "kill-self", {"EVIDENCE_SIGNAL": "TERM"}),
        ("cancelled", "kill-parent", {}),
        ("failure", "kill-parent", {}),
    ],
    ids=["clean", "exit-1", "kill", "term", "cancelled", "shell-died"],
)
def test_every_record_the_workflow_writes_matches_the_schema(tmp_path, outcome, mode, extra):
    _, _, record = _go(tmp_path, outcome=outcome, lock=LOCK, EVIDENCE_MODE=mode, **extra)
    assert record is not None
    assert list(_schema_validator().iter_errors(record)) == []


@pytest.mark.parametrize(
    ("count", "recorded"),
    [(0, 0), (-1, None), (True, None), ("3", None), (2.5, None), (None, None)],
    ids=["zero", "negative", "boolean", "string", "float", "null"],
)
def test_the_planned_count_is_recorded_only_when_it_is_a_whole_number_from_zero(
    tmp_path, count, recorded
):
    _, _, record = _go(
        tmp_path,
        outcome="failure",
        lock={"plan": {"trials": {"count": count}}},
        EVIDENCE_MODE="exit",
        EVIDENCE_EXIT="1",
    )
    assert record["planned_trials"] == recorded
    assert type(record["planned_trials"]) is type(recorded)
    assert list(_schema_validator().iter_errors(record)) == []


def test_a_record_with_no_planned_count_and_a_manifest_matches_the_schema(tmp_path):
    _, _, record = _go(
        tmp_path, outcome="success", manifest=True, EVIDENCE_MODE="exit", EVIDENCE_EXIT="0"
    )
    assert record["planned_trials"] is None and record["manifest_present"] is True
    assert list(_schema_validator().iter_errors(record)) == []


def test_the_stand_in_carries_an_ending_that_matches_the_schema(tmp_path):
    _checkout(tmp_path, "v1.0.0")
    _integrity(tmp_path, KILLED)
    stand_in = json.loads((tmp_path / "work/no-evidence/stand-in-manifest.json").read_text())
    assert list(_schema_validator().iter_errors(stand_in["stand_in"]["ending"])) == []


MALFORMED_ENDINGS = {
    "an extra field": {**KILLED, "extra": 1},
    "another format": {**KILLED, "format": "livingproof-evidence-ending/0.2"},
    "an unknown ending": {**KILLED, "ended": "died"},
    "a string exit code": {**KILLED, "exit_code": "137"},
    "a boolean exit code": {**KILLED, "exit_code": True},
    "a numeric signal": {**KILLED, "signal": 9},
    "a lower-case signal": {**KILLED, "signal": "kill"},
    "a bad step outcome": {**KILLED, "step_outcome": "broken"},
    "a string manifest_present": {**KILLED, "manifest_present": "yes"},
    "a negative planned count": {**KILLED, "planned_trials": -1},
    "a string planned count": {**KILLED, "planned_trials": "3"},
    "an empty configuration": {**KILLED, "configuration": ""},
    "an unfinished ending with an exit code": {
        **KILLED,
        "ended": "unfinished",
        "signal": None,
    },
    "an exited ending with a signal": {**KILLED, "ended": "exited", "exit_code": 1},
    "a signalled ending with no signal": {**KILLED, "signal": None},
    "no ended": {k: v for k, v in KILLED.items() if k != "ended"},
    "no planned_trials": {k: v for k, v in KILLED.items() if k != "planned_trials"},
}


@pytest.mark.parametrize("name", sorted(MALFORMED_ENDINGS))
def test_the_ending_schema_rejects(name):
    assert list(_schema_validator().iter_errors(MALFORMED_ENDINGS[name])), name
