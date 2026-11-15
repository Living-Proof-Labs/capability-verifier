# SPDX-License-Identifier: Apache-2.0
"""A reused checkout keeps its existing origin, including a different URL.

checkout() only adds origin when it creates the git directory. A second run
into the same dest keeps whatever origin is already there, and does not add
one if it was removed. Proposed by Vera in the capability-verifier#97 review.
"""

from __future__ import annotations

import subprocess

import pytest

from capability_verifier import definition as d
from capability_verifier.evidence_lock import FORMAT_V2, evidence_document


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def bare_source(tmp_path, monkeypatch):
    base = tmp_path / "git"
    bare = base / "Org" / "robot.git"
    bare.mkdir(parents=True)
    _git(bare, "init", "--bare", "-q", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "tracked.txt").write_text("PINNED\n")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pin")
    pin = _git(seed, "rev-parse", "HEAD").stdout.strip()
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "origin", "HEAD:main")
    monkeypatch.setattr(d, "GITHUB", f"file://{base}")
    return {"pin": pin, "repo": "Org/robot", "source": f"file://{base}/Org/robot.git"}


def _evidence_robot_repo(dest, *, expect_error=False):
    from livingproof_sim_mujoco.evidence import robot_repo

    document = evidence_document(
        {
            "format": FORMAT_V2,
            "scenario": "walk-hallway@0.1.0",
            "releases": {},
            "world": {"id": "hallway-open", "version": "0.1.0"},
            "configurations": [],
            "plan": {"command_speed": 0.5},
        }
    )
    assert "robot" not in document
    try:
        return robot_repo(dest, document)
    except SystemExit as exc:
        if expect_error:
            raise
        pytest.fail(str(exc))


def test_a_reused_destination_keeps_a_different_origin(tmp_path, bare_source):
    dest = tmp_path / "work"
    assert d.checkout(bare_source["repo"], bare_source["pin"], dest) == bare_source["pin"]
    other = "file:///tmp/elsewhere.git"
    _git(dest, "remote", "set-url", "origin", other)
    assert d.checkout(bare_source["repo"], bare_source["pin"], dest) == bare_source["pin"]
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    origin = _git(dest, "remote", "get-url", "origin")
    assert origin.returncode == 0
    assert origin.stdout.strip() == other
    assert _evidence_robot_repo(dest) == other


def test_a_reused_destination_without_origin_is_left_without_one(tmp_path, bare_source):
    dest = tmp_path / "work"
    assert d.checkout(bare_source["repo"], bare_source["pin"], dest) == bare_source["pin"]
    _git(dest, "remote", "remove", "origin")
    assert d.checkout(bare_source["repo"], bare_source["pin"], dest) == bare_source["pin"]
    origin = _git(dest, "remote", "get-url", "origin", check=False)
    assert origin.returncode != 0
    with pytest.raises(SystemExit, match="git remote origin"):
        _evidence_robot_repo(dest, expect_error=True)
