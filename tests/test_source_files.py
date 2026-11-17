# SPDX-License-Identifier: Apache-2.0
"""Caller source-file bindings are materialized in the robot root (livingproof-verification#119)."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from test_evidence_lock import _robot_lock, v2_from_robot

from capability_verifier import definition as d
from capability_verifier import evidence_lock

WORLD_BYTES = b'{"format": "livingproof-world/0.3", "id": "hallway-corner"}\n'
PARAMETERS_BYTES = b'{"command_speed": 0.5}\n'
WORLD_SHA = hashlib.sha256(WORLD_BYTES).hexdigest()
PARAMETERS_SHA = hashlib.sha256(PARAMETERS_BYTES).hexdigest()
WORLD_PATH = "worlds/hallway-corner/0.2.0/world.json"
PARAMETERS_PATH = "specs/scenarios/walk-hallway/parameters.json"


def _lock_with_sources():
    lock = v2_from_robot(_robot_lock())
    lock["world"]["id"] = "hallway-corner"
    lock["world"]["version"] = "0.2.0"
    lock["world"]["sha256"] = WORLD_SHA
    lock["scenario"] = "walk-hallway@0.1.0"
    lock["sources"] = {
        "world": {"path": WORLD_PATH, "sha256": WORLD_SHA},
        "parameters": {"path": PARAMETERS_PATH, "sha256": PARAMETERS_SHA},
    }
    return lock


def _fetch(_repository, _commit, path, _work):
    if path.endswith("world.json"):
        return WORLD_BYTES
    if path.endswith("parameters.json"):
        return PARAMETERS_BYTES
    raise AssertionError(f"unexpected path {path}")


def test_place_source_files_writes_world_and_parameters_into_the_robot_root(tmp_path):
    """The verifier places the named source files in the robot root and keeps their sha256."""
    robot = tmp_path / "robot"
    robot.mkdir()
    lock = _lock_with_sources()
    fetched = []

    def fetch(repository, commit, path, work):
        fetched.append((repository, commit, path))
        return _fetch(repository, commit, path, work)

    evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=fetch)

    assert (robot / WORLD_PATH).read_bytes() == WORLD_BYTES
    assert (robot / PARAMETERS_PATH).read_bytes() == PARAMETERS_BYTES
    assert hashlib.sha256((robot / WORLD_PATH).read_bytes()).hexdigest() == WORLD_SHA
    assert hashlib.sha256((robot / PARAMETERS_PATH).read_bytes()).hexdigest() == PARAMETERS_SHA
    world_repo, world_commit, world_path = next(t for t in fetched if t[2].endswith("world.json"))
    assert world_repo == lock["world"]["repository"]
    assert world_commit == lock["world"]["commit"]
    assert world_path == WORLD_PATH
    specs_repo, specs_commit, params_path = next(
        t for t in fetched if t[2].endswith("parameters.json")
    )
    assert specs_repo == lock["releases"]["specs"]["repository"]
    assert specs_commit == lock["releases"]["specs"]["commit"]
    assert params_path == PARAMETERS_PATH


def test_place_source_files_does_nothing_when_the_lock_omits_sources(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    lock = v2_from_robot(_robot_lock())
    evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=_fetch)
    assert list(robot.iterdir()) == []


def test_place_source_files_refuses_a_sha256_mismatch(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    lock = _lock_with_sources()
    lock["sources"]["world"]["sha256"] = "ab" * 32
    with pytest.raises(SystemExit, match="sources.world sha256 mismatch"):
        evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=_fetch)
    assert not (robot / WORLD_PATH).exists()


def test_place_source_files_refuses_a_path_outside_the_robot(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "../secret.json"
    with pytest.raises(SystemExit, match="sources.world path must stay inside the robot"):
        evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=_fetch)


def test_prepare_evidence_inputs_places_source_files(tmp_path, capsys, monkeypatch):
    """The local run path writes the evidence lock and the named source files."""
    monkeypatch.setattr(evidence_lock, "_fetch_source_file", _fetch)
    lock = _lock_with_sources()
    caller = tmp_path / "caller.lock.json"
    caller.write_text(json.dumps(lock, indent=2) + "\n")
    robot = tmp_path / "robot"
    robot.mkdir()
    env = d.prepare_evidence_inputs(lock, robot, caller)
    assert env == {"EVIDENCE_SOURCE": "caller"}
    capsys.readouterr()
    written = json.loads((robot / "verification.lock.json").read_text())
    assert written["sources"] == lock["sources"]
    assert (robot / WORLD_PATH).read_bytes() == WORLD_BYTES
    assert (robot / PARAMETERS_PATH).read_bytes() == PARAMETERS_BYTES


def test_place_source_files_refuses_an_in_root_alias_into_git(tmp_path):
    """A directory symlink to .git is reserved even when the binding path is not lexical .git."""
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / ".git").mkdir()
    os.symlink(".git", robot / "alias")
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "alias/new-source.json"
    with pytest.raises(SystemExit, match="sources.world"):
        evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=_fetch)
    assert not (robot / ".git" / "new-source.json").exists()
    assert not (robot / PARAMETERS_PATH).exists()


def test_place_source_files_refuses_an_in_root_alias_to_the_evidence_lock(tmp_path):
    """A symlink to the evidence lock is reserved even when the binding names the alias."""
    robot = tmp_path / "robot"
    robot.mkdir()
    os.symlink(evidence_lock.ROBOT_LOCK_NAME, robot / "alias")
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "alias"
    with pytest.raises(SystemExit, match="sources.world"):
        evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=_fetch)
    assert not (robot / evidence_lock.ROBOT_LOCK_NAME).exists()
    assert not (robot / PARAMETERS_PATH).exists()
