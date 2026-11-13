# SPDX-License-Identifier: Apache-2.0
"""Evidence inputs written from a 0.2.0 verification lock match the robot file."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from capability_verifier import definition as d
from capability_verifier import evidence_lock, repin

ROOT = Path(__file__).parents[1]
G1_LOCK = Path(__file__).parent / "fixtures" / "g1-862cf979-verification.lock.json"
THIN_PYPROJECT = (Path(__file__).parent / "fixtures" / "thin-robot" / "pyproject.toml").read_text()
SCRIPT = ROOT / ".github" / "scripts" / "write_evidence_inputs.sh"
EVALUATOR = {
    "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
    "tag": "v1.0.0",
    "commit": "752a6f0a9f9d448c950e56a8e345a69a6693a907",
}


def _robot_lock():
    return json.loads(G1_LOCK.read_text())


def v2_from_robot(robot_lock, configurations=("playground",)):
    """A 0.2.0 lock whose evidence fields are the published G1 robot file."""
    return {
        "format": evidence_lock.FORMAT_V2,
        "definition": {
            "path": "unitree-g1-playground-mujoco-hallway.toml",
            "sha256": "ab" * 32,
        },
        "procedure": "walk-hallway",
        "scenario": robot_lock["scenario"],
        "robot": {
            "repository": "Living-Proof-Labs/livingproof-unitree-g1",
            "ref": "862cf979f57e40d6158070c59b432b0cf4eaa4bd",
            "commit": "862cf979f57e40d6158070c59b432b0cf4eaa4bd",
            "configurations": list(configurations),
            "adapter": "lpl_g1.adapter:ROBOT",
        },
        "releases": robot_lock["releases"],
        "world": robot_lock["world"],
        "configurations": robot_lock["configurations"],
        "plan": robot_lock["plan"],
        "evaluators": {"livingproof-evaluator-scenario": EVALUATOR},
    }


def test_read_lock_accepts_the_published_g1_evidence_as_0_2_0(tmp_path):
    robot_lock = _robot_lock()
    lock = v2_from_robot(robot_lock)
    path = tmp_path / "g1.lock.json"
    path.write_text(json.dumps(lock))
    assert d.read_lock(path) == lock
    path.write_text(json.dumps(v2_from_robot(robot_lock, configurations=("stock", "playground"))))
    assert d.read_lock(path)["robot"]["configurations"] == ["stock", "playground"]


def test_the_robot_evidence_file_is_not_a_verification_lock(tmp_path):
    path = tmp_path / "verification.lock.json"
    path.write_text(G1_LOCK.read_text())
    with pytest.raises(d.DefinitionError, match="0\\.2\\.0"):
        d.read_lock(path)


def test_written_evidence_inputs_equal_the_robot_file(tmp_path):
    """Parity with livingproof-unitree-g1 @ 862cf979, minus the ``$comment``."""
    robot_lock = _robot_lock()
    document = evidence_lock.evidence_document(v2_from_robot(robot_lock))
    assert document == {key: value for key, value in robot_lock.items() if key != "$comment"}
    assert list(document) == list(evidence_lock.EVIDENCE_FIELDS)
    assert set(robot_lock) - set(document) == {"$comment"}
    dest = tmp_path / "verification.lock.json"
    assert evidence_lock.write_evidence_document(v2_from_robot(robot_lock), dest) == document
    assert json.loads(dest.read_text()) == document


def test_a_0_1_0_lock_is_not_an_evidence_source():
    with pytest.raises(SystemExit, match="0\\.2\\.0"):
        evidence_lock.evidence_document({"format": d.FORMAT, "releases": {}})


def test_repin_check_of_the_caller_lock_still_fails_pending_release():
    lock = v2_from_robot(_robot_lock())
    assert repin.check(lock, THIN_PYPROJECT) == []
    lock["releases"]["runner"]["tag"] = "PENDING_RELEASE"
    lock["releases"]["runner"]["commit"] = "PENDING_RELEASE"
    problems = repin.check(lock, THIN_PYPROJECT)
    assert any("PENDING_RELEASE" in problem for problem in problems)
    assert any(problem.startswith("releases.runner is PENDING_RELEASE") for problem in problems)


def _run_script(robot, caller, lock_rel, env=None):
    script_env = os.environ.copy()
    script_env.pop("PYTHONPATH", None)
    if env:
        script_env.update(env)
    return subprocess.run(
        ["sh", str(SCRIPT), str(robot), str(caller), lock_rel, str(ROOT)],
        capture_output=True,
        text=True,
        env=script_env,
    )


def test_an_empty_verification_lock_leaves_the_robot_file(tmp_path):
    """No verification-lock input. The four current pins take this path.

    unitree-g1 862cf979 (stock and playground, walk-hallway.yml @ 44acb9ed),
    unitree-go1 c56816dc and booster-t1 9bbcd300 (walk-hallway.yml @ 67bb6bbc).
    They call walk-hallway without verification-lock, so a robot file stays the source.
    """
    robot = tmp_path / "robot"
    robot.mkdir()
    original = G1_LOCK.read_bytes()
    (robot / "verification.lock.json").write_bytes(original)
    done = _run_script(robot, tmp_path / "missing-caller", "")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "source=robot"
    assert done.stderr == ""
    assert (robot / "verification.lock.json").read_bytes() == original
    assert not (robot / evidence_lock.CALLER_LOCK_NAME).exists()


def test_the_workflow_script_writes_the_robot_files_evidence_inputs(tmp_path):
    robot_lock = _robot_lock()
    caller = tmp_path / "caller"
    (caller / "verifications").mkdir(parents=True)
    lock = v2_from_robot(robot_lock)
    lock_path = caller / "verifications" / "g1.lock.json"
    raw = (json.dumps(lock, indent=2) + "\n").encode()
    lock_path.write_bytes(raw)
    robot = tmp_path / "robot"
    robot.mkdir()
    done = _run_script(
        robot,
        caller,
        "verifications/g1.lock.json",
        env={"COPY_CALLER_LOCK": "1"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "source=caller"
    assert done.stderr.splitlines() == evidence_lock.evidence_source_lines(None)
    written = json.loads((robot / "verification.lock.json").read_text())
    assert written == {key: value for key, value in robot_lock.items() if key != "$comment"}
    assert "overridden_robot_lock" not in written
    assert (robot / evidence_lock.CALLER_LOCK_NAME).read_bytes() == raw


@pytest.mark.parametrize(
    ("lock_rel", "message"),
    [
        ("", "pass verification-lock"),
        ("/etc/passwd", "relative path"),
        ("../secret", "relative path"),
        ("verifications/missing.lock.json", "no verification lock"),
    ],
)
def test_the_workflow_script_refuses_a_bad_caller_path(tmp_path, lock_rel, message):
    robot = tmp_path / "robot"
    robot.mkdir()
    done = _run_script(robot, tmp_path / "caller", lock_rel)
    assert done.returncode != 0
    assert message in done.stderr


def test_a_missing_caller_lock_does_not_fall_back_to_the_robot_file(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    original = G1_LOCK.read_bytes()
    (robot / "verification.lock.json").write_bytes(original)
    done = _run_script(robot, tmp_path / "caller", "verifications/missing.lock.json")
    assert done.returncode != 0
    assert "no verification lock" in done.stderr
    assert (robot / "verification.lock.json").read_bytes() == original


def test_prepare_evidence_inputs_matches_the_workflow_script(tmp_path, capsys):
    robot_lock = _robot_lock()
    lock = v2_from_robot(robot_lock)
    caller = tmp_path / "caller.lock.json"
    caller.write_text(json.dumps(lock, indent=2) + "\n")
    absent = tmp_path / "absent"
    absent.mkdir()
    env = d.prepare_evidence_inputs(lock, absent, caller)
    assert env == {"EVIDENCE_SOURCE": "caller"}
    assert capsys.readouterr().err.splitlines() == evidence_lock.evidence_source_lines(None)
    assert json.loads(
        (absent / "verification.lock.json").read_text()
    ) == evidence_lock.evidence_document(lock)
    assert (absent / evidence_lock.CALLER_LOCK_NAME).read_bytes() == caller.read_bytes()


def test_prepare_evidence_inputs_overrides_a_robot_lock(tmp_path, capsys):
    import hashlib

    robot_lock = _robot_lock()
    caller_body = v2_from_robot(robot_lock)
    caller_body["releases"] = json.loads(json.dumps(robot_lock["releases"]))
    caller_body["releases"]["verifier"]["commit"] = "b" * 40
    caller_body["releases"]["verifier"]["digest"] = "sha256:" + "2" * 64
    caller_body["plan"] = {"trials": {"count": 3}, "command_speed": 0.8}
    caller_body["evaluators"]["livingproof-evaluator-scenario"]["commit"] = "d" * 40
    caller = tmp_path / "caller.lock.json"
    caller.write_text(json.dumps(caller_body) + "\n")
    robot = tmp_path / "robot"
    robot.mkdir()
    original = G1_LOCK.read_bytes()
    (robot / "verification.lock.json").write_bytes(original)
    assert d.prepare_evidence_inputs(caller_body, robot, caller) == {"EVIDENCE_SOURCE": "caller"}
    digest = hashlib.sha256(original).hexdigest()
    assert capsys.readouterr().err.splitlines() == evidence_lock.evidence_source_lines(digest)
    written = json.loads((robot / "verification.lock.json").read_text())
    assert written["releases"]["verifier"]["commit"] == "b" * 40
    assert written["plan"]["command_speed"] == 0.8
    assert written["overridden_robot_lock"]["sha256"] == digest
    copied = json.loads((robot / evidence_lock.CALLER_LOCK_NAME).read_text())
    assert copied["evaluators"]["livingproof-evaluator-scenario"]["commit"] == "d" * 40


def test_a_v1_lock_leaves_a_robot_file(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    original = G1_LOCK.read_bytes()
    (robot / "verification.lock.json").write_bytes(original)
    assert d.prepare_evidence_inputs({"format": d.FORMAT}, robot, tmp_path / "missing") == {}
    assert (robot / "verification.lock.json").read_bytes() == original
