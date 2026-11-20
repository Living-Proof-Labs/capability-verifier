# SPDX-License-Identifier: Apache-2.0
"""Run the scenario-media step's script against stub tools, and check what it does.

test_scenario_media_evaluator_version.py reads the workflow text. These tests execute the step's
`run` script under `bash -e` (as Actions does) with `python3`, `uv` and the evaluator's python
replaced by stubs that log each call. They pin the behaviour the text checks cannot: the guard
exits 1 when the built evaluator reports a version other than the locked release (and the media
runner is never reached), a failed pin step stops the job before `uv sync`, and the calls come in
the order pin, sync, version read, runner.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
DOC = yaml.safe_load((ROOT / ".github/workflows/scenario.yml").read_text())
STEP = next(
    s
    for s in DOC["jobs"]["scenario-media"]["steps"]
    if s.get("name", "").startswith("Scenario evaluator on the recordings")
)
REPOSITORY = "Living-Proof-Labs/livingproof-evaluator-scenario"
TAG = "v1.5.3"
COMMIT = "ca9d337a771269d07feec94c99f199bef7fca808"


def _stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    *,
    pin_exit: int = 0,
    release: str = "1.5.3",
    reported: str = "1.5.3",
    workflow_src: bool = True,
):
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    # python3 is only ever the evaluator_pin call in this step; it prints the release version.
    _stub(
        bin_dir / "python3",
        f'echo "pin $*" >> "{log}"\necho "pin-path $PYTHONPATH" >> "{log}"\n[ {pin_exit} -eq 0 ] && echo {release}\nexit {pin_exit}\n',
    )
    # uv sync, `uv run ... importlib.metadata` (the version read) and `uv run ... run_scenario_media.py`.
    _stub(
        bin_dir / "uv",
        'case "$*" in\n'
        f'  *importlib.metadata*) echo "version-read" >> "{log}"; echo {reported} ;;\n'
        f'  *run_scenario_media.py*) echo "runner $*" >> "{log}" ;;\n'
        f'  sync*) echo "sync $*" >> "{log}" ;;\n'
        "esac\n",
    )
    _stub(
        tmp_path / "evaluator/.venv/bin/python",
        'echo "/site/lib/python3.12/site-packages"\n',
    )
    if workflow_src:
        (tmp_path / "workflow-src/src").mkdir(parents=True)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "EVALUATOR_REPOSITORY": REPOSITORY,
        "EVALUATOR_COMMIT": COMMIT,
        "EVALUATOR_TAG": TAG,
    }
    done = subprocess.run(
        ["bash", "-e", "-c", STEP["run"]],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return done, calls


def test_a_build_that_reports_another_version_is_refused_before_the_runner(tmp_path):
    done, calls = _run(tmp_path, reported="0.1.dev1")
    assert done.returncode == 1
    assert "reports 0.1.dev1, not the locked v1.5.3" in done.stderr
    assert not any(call.startswith("runner") for call in calls)


def test_a_build_that_reports_the_release_reaches_the_runner_after_the_pin_and_sync(tmp_path):
    done, calls = _run(tmp_path)
    assert done.returncode == 0, done.stderr
    kinds = [call.split()[0] for call in calls if call.split()[0] != "pin-path"]
    assert kinds == ["pin", "sync", "version-read", "runner"]
    pin = calls[0]
    assert f"--repository {REPOSITORY}" in pin
    assert f"--tag {TAG}" in pin
    assert f"--commit {COMMIT}" in pin
    assert pin.endswith("--checkout evaluator")
    assert "--directory evaluator" in calls[2]


def test_the_guard_compares_against_the_release_the_pin_step_printed(tmp_path):
    # The pin step prints the version of the tag it fetched; the build must report that one.
    done, calls = _run(tmp_path, release="1.5.4", reported="1.5.3")
    assert done.returncode == 1
    assert "reports 1.5.3" in done.stderr
    assert not any(call.startswith("runner") for call in calls)


def test_a_failed_pin_step_stops_the_job_before_uv_sync(tmp_path):
    done, calls = _run(tmp_path, pin_exit=2)
    assert done.returncode != 0
    assert [call.split()[0] for call in calls] == ["pin", "pin-path"]


@pytest.mark.parametrize(("workflow_src", "expected"), [(True, "workflow-src/src"), (False, "src")])
def test_the_pin_module_comes_from_the_workflow_source_when_it_is_checked_out(
    tmp_path, workflow_src, expected
):
    done, calls = _run(tmp_path, workflow_src=workflow_src)
    assert done.returncode == 0, done.stderr
    assert f"pin-path {expected}" in calls


def test_the_workflow_source_checkout_is_the_one_scenario_evaluator_uses():
    # The step's script reads workflow-src/src; a checkout that lands elsewhere or at another ref
    # would silently fall back to the (possibly older) verifier tree.
    def checkout(job: str) -> dict:
        step = next(
            s
            for s in DOC["jobs"][job]["steps"]
            if s.get("name") == "Workflow source for the evaluator pin"
        )
        return {key: value for key, value in step.items() if key != "if"}

    media = checkout("scenario-media")
    assert media == checkout("scenario-evaluator")
    assert media["with"]["path"] == "workflow-src"
    assert media["with"]["repository"] == "${{ job.workflow_repository }}"
    assert media["with"]["ref"] == "${{ job.workflow_sha }}"
