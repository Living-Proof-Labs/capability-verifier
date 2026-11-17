# SPDX-License-Identifier: Apache-2.0
"""Reviewer tests for #150 (livingproof-verification#119 slice 5, part 1).

Placing ``sources.*`` must never write outside the robot root (including
through a symlink), and must never replace a file the robot or this verifier
owns: a robot file with other bytes, the evidence or caller lock, ``.git``, or
the other binding's file. A path that names a directory is refused, not an
IsADirectoryError. Placing the same bytes again is allowed.
"""

from __future__ import annotations

import os

import pytest
from test_source_files import WORLD_BYTES, WORLD_PATH, _fetch, _lock_with_sources

from capability_verifier import evidence_lock


def _robot(tmp_path):
    robot = tmp_path / "robot"
    robot.mkdir()
    return robot


def _place(lock, robot, tmp_path):
    evidence_lock.place_source_files(lock, robot, tmp_path / "work", fetch=_fetch)


def test_a_symlinked_directory_cannot_lead_outside_the_robot(tmp_path):
    robot = _robot(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, robot / "link")
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "link/world.json"
    with pytest.raises(SystemExit, match="inside the robot"):
        _place(lock, robot, tmp_path)
    assert not (outside / "world.json").exists()


def test_a_symlinked_file_cannot_lead_outside_the_robot(tmp_path):
    robot = _robot(tmp_path)
    secret = tmp_path / "secret.json"
    secret.write_text("keep")
    os.symlink(secret, robot / "world.json")
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "world.json"
    with pytest.raises(SystemExit, match="inside the robot"):
        _place(lock, robot, tmp_path)
    assert secret.read_text() == "keep"


def test_a_malformed_sha256_is_refused(tmp_path):
    lock = _lock_with_sources()
    lock["sources"]["world"]["sha256"] = "abc"
    with pytest.raises(SystemExit, match="64 hex"):
        _place(lock, _robot(tmp_path), tmp_path)


def test_placing_identical_bytes_again_is_allowed(tmp_path):
    robot = _robot(tmp_path)
    (robot / WORLD_PATH).parent.mkdir(parents=True)
    (robot / WORLD_PATH).write_bytes(WORLD_BYTES)
    _place(_lock_with_sources(), robot, tmp_path)
    assert (robot / WORLD_PATH).read_bytes() == WORLD_BYTES


def test_an_existing_robot_file_with_other_bytes_is_not_overwritten(tmp_path):
    robot = _robot(tmp_path)
    (robot / "pyproject.toml").write_text("[project]\n")
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "pyproject.toml"
    with pytest.raises(SystemExit, match="pyproject.toml"):
        _place(lock, robot, tmp_path)
    assert (robot / "pyproject.toml").read_text() == "[project]\n"


@pytest.mark.parametrize(
    "path",
    [
        "verification.lock.json",
        evidence_lock.CALLER_LOCK_NAME,
        ".git/hooks/post-checkout",
    ],
)
def test_a_path_owned_by_the_verifier_or_git_is_refused(tmp_path, path):
    robot = _robot(tmp_path)
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = path
    with pytest.raises(SystemExit):
        _place(lock, robot, tmp_path)
    assert not (robot / path).exists()


def test_world_and_parameters_cannot_name_the_same_path(tmp_path):
    robot = _robot(tmp_path)
    lock = _lock_with_sources()
    lock["sources"]["parameters"]["path"] = WORLD_PATH
    with pytest.raises(SystemExit, match="parameters"):
        _place(lock, robot, tmp_path)
    assert not (robot / WORLD_PATH).exists()


@pytest.mark.parametrize("path", [".", "src"])
def test_a_path_that_names_a_directory_is_refused_clearly(tmp_path, path):
    robot = _robot(tmp_path)
    (robot / "src").mkdir()
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = path
    with pytest.raises(SystemExit, match="sources.world"):
        _place(lock, robot, tmp_path)


def _written(robot):
    return sorted(p.relative_to(robot).as_posix() for p in robot.rglob("*") if p.is_file())


def test_a_path_through_an_existing_file_is_refused_clearly(tmp_path):
    robot = _robot(tmp_path)
    (robot / "pyproject.toml").write_text("[project]\n")
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = "pyproject.toml/world.json"
    with pytest.raises(SystemExit, match="sources.world"):
        _place(lock, robot, tmp_path)
    assert _written(robot) == ["pyproject.toml"]
    assert (robot / "pyproject.toml").read_text() == "[project]\n"


@pytest.mark.parametrize(
    "world,parameters",
    [
        pytest.param("locked", "locked/parameters.json", id="world-is-parent-of-parameters"),
        pytest.param("locked/world.json", "locked", id="parameters-is-parent-of-world"),
    ],
)
def test_nested_bindings_are_refused_before_anything_is_written(tmp_path, world, parameters):
    robot = _robot(tmp_path)
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = world
    lock["sources"]["parameters"]["path"] = parameters
    with pytest.raises(SystemExit, match="sources."):
        _place(lock, robot, tmp_path)
    assert _written(robot) == []


@pytest.mark.parametrize(
    "path",
    [
        ".GIT/hooks/post-checkout",
        "./.Git/config",
        "VERIFICATION.LOCK.JSON",
        evidence_lock.CALLER_LOCK_NAME.upper(),
    ],
)
def test_reserved_names_are_refused_in_any_case(tmp_path, path):
    robot = _robot(tmp_path)
    lock = _lock_with_sources()
    lock["sources"]["world"]["path"] = path
    with pytest.raises(SystemExit, match="reserved"):
        _place(lock, robot, tmp_path)
    assert _written(robot) == []
