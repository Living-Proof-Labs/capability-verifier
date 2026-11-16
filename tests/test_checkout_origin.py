# SPDX-License-Identifier: Apache-2.0
"""A brand-new checkout records origin so evidence can name the robot repository.

livingproof-sim-mujoco evidence writes run-manifest ``robot.repo`` from the lock's
``robot.repo`` or from ``git remote get-url origin``. The evidence document projected
from a 0.2.0 lock has no ``robot.repo``, so a fresh destination has to carry ``origin``
pointing at the source that was fetched.
"""

import subprocess

import pytest

from capability_verifier import definition as d
from capability_verifier.evidence_lock import FORMAT_V2, evidence_document


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def bare_source(tmp_path, monkeypatch):
    """A local bare repository. Checkout fetches this URL; nothing uses the network."""
    base = tmp_path / "git"
    bare = base / "Org" / "robot.git"
    bare.mkdir(parents=True)
    _git(bare, "init", "--bare", "-q", "-b", "main")
    assert _git(bare, "rev-parse", "--is-bare-repository").stdout.strip() == "true"
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


def _evidence_robot_repo(dest):
    """The evidence step that needs origin when the projected lock has no robot.repo."""
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
        pytest.fail(str(exc))


@pytest.mark.parametrize("kind", ["missing", "empty"])
def test_fresh_destination_records_origin_for_evidence(tmp_path, bare_source, kind):
    dest = tmp_path / kind
    if kind == "empty":
        dest.mkdir()
        assert not any(dest.iterdir())
    else:
        assert not dest.exists()
    assert d.checkout(bare_source["repo"], bare_source["pin"], dest) == bare_source["pin"]
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    recorded = _evidence_robot_repo(dest)
    origin = _git(dest, "remote", "get-url", "origin", check=False)
    assert origin.returncode == 0, origin.stderr
    assert origin.stdout.strip() == bare_source["source"]
    assert recorded == bare_source["source"]
