# SPDX-License-Identifier: Apache-2.0
"""scenario.yml passes the caller lock to livingproof-sim-mujoco >= 3.0 (specs#40 step 12).

livingproof-sim-mujoco has required ``--lock`` since v3.0.0. The robots step 12 pins
(g1 v2.2.1, t1 and go1 v2.2.x) lock sim v3.2.x, so the workflow's evidence command must
hand the sim the evidence document the plan wrote (``verification.lock.json`` in the robot
checkout). Robots still on sim v2.x read that file themselves and get the same command as
before, so the walk-hallway runs are unchanged. These tests run the workflow's own step
script, the way ``definition.run`` and CI run it, with ``uv`` stubbed to record its argv.
"""

import json
import os
from pathlib import Path

import pytest

from capability_verifier import definition

ROOT = Path(__file__).parents[1]
SCENARIO = (ROOT / ".github/workflows/scenario.yml").read_text()

_UV_STUB = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
out = Path(sys.argv[sys.argv.index("--no-sync") + 6])
out.mkdir(parents=True, exist_ok=True)
"""


def _uv_lock(version: str | None) -> str:
    packages = [("mujoco", "3.3.0"), ("numpy", "2.2.0")]
    if version is not None:
        packages.append(("livingproof-sim-mujoco", version))
    return "version = 1\n" + "".join(
        f'\n[[package]]\nname = "{n}"\nversion = "{v}"\n' for n, v in packages
    )


def _evidence_argv(tmp_path: Path, sim: str | None) -> list:
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "uv.lock").write_text(_uv_lock(sim))
    return _evidence_argv_in(tmp_path, robot)


def _evidence_argv_in(tmp_path: Path, robot: Path) -> list:
    (robot / "verification.lock.json").write_text(json.dumps({"format": "evidence"}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uv").write_text(_UV_STUB)
    (bindir / "uv").chmod(0o755)
    calls = tmp_path / "calls.txt"
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "ADAPTER": "lpl_g1.adapter:ROBOT",
        "CONFIGURATION": "stock",
    }
    script = definition.step(SCENARIO, "Evidence (observations only")
    definition.sh(script, robot, env, tmp_path / "github-output.txt")
    [argv] = [json.loads(line) for line in calls.read_text().splitlines()]
    return argv


EVIDENCE = [
    "run",
    "--no-sync",
    "python",
    "-m",
    "livingproof_sim_mujoco.evidence",
    "lpl_g1.adapter:ROBOT",
    "stock",
    "artifacts/evidence/stock",
]


@pytest.mark.parametrize("sim", ["3.0.0", "3.2.0", "3.2.1", "4.0.0"])
def test_sim_3_and_later_get_the_evidence_document_with_lock(tmp_path, sim):
    """Without --lock, sim v3.x exits 2: 'the following arguments are required: --lock'."""
    argv = _evidence_argv(tmp_path, sim)
    assert argv == [*EVIDENCE, "--lock", "verification.lock.json"]


@pytest.mark.parametrize("sim", ["2.0.3", "2.1.1", None])
def test_sim_2_keeps_the_walk_hallway_command_unchanged(tmp_path, sim):
    """Robots on sim v2.x (today's walk-hallway locks) run exactly the old command."""
    assert _evidence_argv(tmp_path, sim) == EVIDENCE


def test_the_wrapper_runs_the_same_evidence_step():
    wrapper = definition.procedure_text(ROOT, "walk-hallway")
    name = "Evidence (observations only"
    assert definition.step(wrapper, name) == definition.step(SCENARIO, name)


def _editable_root_uv_lock(sim: str) -> str:
    """The robots' uv.lock since setuptools-scm: the editable root package has no version."""
    return (
        _uv_lock(sim)
        + '\n[[package]]\nname = "mcap"\nversion = "1.2.2"\n'
        + '\n[[package]]\nname = "livingproof-unitree-g1"\nsource = { editable = "." }\n'
    )


def test_a_robot_whose_root_package_has_no_version_still_gets_lock(tmp_path):
    """g1 v2.2.1, t1 and go1 v2.2.0 lock their own package with no version (setuptools-scm)."""
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "uv.lock").write_text(_editable_root_uv_lock("3.2.1"))
    argv = _evidence_argv_in(tmp_path, robot)
    assert argv == [*EVIDENCE, "--lock", "verification.lock.json"]


def test_media_and_bundle_capability_reads_a_root_package_with_no_version(tmp_path):
    """The plan step reads the same uv.lock and must not stop on the versionless root."""
    robot = tmp_path / "robot"
    robot.mkdir()
    (robot / "uv.lock").write_text(_editable_root_uv_lock("3.2.1"))
    releases = {"reporter": {"tag": "v3.10.1"}, "verifier": {"tag": "v1.24.0"}}
    (robot / "verification.lock.json").write_text(json.dumps({"releases": releases}))
    script = definition.step(SCENARIO, "Media and bundle capability")
    outputs = definition.sh(
        f"uv run --no-project --with packaging==26.3 -- bash -e <<'LP'\n{script}LP\n",
        robot,
        {},
        tmp_path / "plan.out",
    )
    assert outputs == {"media-capable": "true", "bundle-version": "1.1.0"}
