# SPDX-License-Identifier: Apache-2.0
"""The refs step must refuse a committed caller lock before it invokes jq.

The existing robot-path test supplies a jq stand-in so the caller-path assertion
can run without a host jq. That stand-in hides a regression: if the refusal
moves after the first jq call, the test still passes wherever jq is installed,
and fails in the verifier image with ``jq: command not found``.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/scenario.yml").read_text()


def _step(name):
    block = WORKFLOW[WORKFLOW.index(f"- name: {name}") :]
    block = block[block.index("run: |") + len("run: |") + 1 :]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


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

    def body(commit):
        return {
            "configurations": [{"name": "stock"}],
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


def _run_refs(robot: Path, source: str, bindir: Path):
    _refs_tree(robot)
    bash = shutil.which("bash")
    assert bash, "bash is required to replay the workflow step"
    output = robot / "github-output.txt"
    env = os.environ.copy()
    env["PATH"] = str(bindir)
    env["GITHUB_OUTPUT"] = str(output)
    env["ONLY"] = ""
    env["EVIDENCE_SOURCE"] = source
    env.pop("PYTHONPATH", None)
    for name in (
        "RESOLVED_VERIFIER_COMMIT",
        "RESOLVED_VERIFIER_IMAGE",
        "RESOLVED_REPORTER_IMAGE",
        "RESOLVED_EVALUATOR_COMMIT",
    ):
        env.pop(name, None)
    return subprocess.run(
        [bash, "-eo", "pipefail", "-c", _step("Refs from verification.lock.json")],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )


def test_a_committed_caller_lock_is_rejected_before_jq(tmp_path):
    """A canary jq must not run when EVIDENCE_SOURCE is not caller."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    jq = bindir / "jq"
    jq.write_text("#!/bin/sh\necho jq-was-invoked >&2\nexit 99\n")
    jq.chmod(0o755)
    rejected = _run_refs(tmp_path / "robot-path", "robot", bindir)
    assert rejected.returncode != 0, rejected.stderr
    assert "committed in the robot repository" in rejected.stderr
    assert "jq-was-invoked" not in rejected.stderr
    unset = _run_refs(tmp_path / "unset", "", bindir)
    assert unset.returncode != 0, unset.stderr
    assert "committed in the robot repository" in unset.stderr
    assert "jq-was-invoked" not in unset.stderr


def test_the_plan_accepts_a_matching_evaluator_pin(tmp_path):
    """The plan step's pin check is not only tested in the failing direction."""
    from test_walk_hallway_workflow import _PIN_COMMIT, _run_plan_pin

    done = _run_plan_pin(tmp_path / "plan", installed_commit=_PIN_COMMIT, git_commit=_PIN_COMMIT)
    assert done.returncode == 0, done.stderr
