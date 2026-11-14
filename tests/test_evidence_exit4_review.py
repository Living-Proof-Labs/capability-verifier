# SPDX-License-Identifier: Apache-2.0
"""Independent-review checks for #55 / PR #88 (Vera).

Real livingproof-sim-mujoco evidence (G1 v2.0.0 playground, 3 identical trials) drives the
scenario.yml evidence and assess steps the way ``definition.run`` executes them. The
disagreeing fixture is a genuine physics disagreement (one trial started 0.1 mm off nominal
in y), so the simulator itself exited 4 and wrote the finding.

The last three tests describe the gate this PR still lacks. They fail at add36d9.
"""

import json
import os
import shutil
import subprocess
import tarfile
import io
from pathlib import Path

import pytest

from capability_verifier import definition

ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/scenario.yml").read_text()
FIX = Path(__file__).parent / "fixtures" / "hallway" / "review-g1"
BASE = "a9cf73811f9290fa4905bc7f6b6ec7da1cfdd342"  # v1.20.0, the PR base: no job_exit


def _verifier_tree(dest: Path, commit: str | None) -> Path:
    """This checkout, or ``commit`` of it, with the fetched catalogs linked in."""
    dest.mkdir(parents=True)
    if commit is None:
        names = ("src", "tools", "pyproject.toml", "uv.lock", "hallway-specs.lock.json")
        for name in names:
            (dest / name).symlink_to(ROOT / name)
    else:
        blob = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "archive",
                "--format=tar",
                commit,
                "src",
                "tools",
                "pyproject.toml",
                "uv.lock",
                "hallway-specs.lock.json",
            ],
            capture_output=True,
            check=True,
        ).stdout
        tarfile.open(fileobj=io.BytesIO(blob)).extractall(dest, filter="data")
    for name in (".specs", ".specs-hallway", ".venv"):
        if (ROOT / name).exists():
            (dest / name).symlink_to(ROOT / name)
    return dest


def _assess(verifier: Path, evidence: Path):
    shutil.copytree(evidence, verifier / "evidence")
    env = os.environ.copy()
    env.update(BUNDLE_VERSION="1.1.0", SOURCE_COMMIT="0" * 40)
    env.setdefault("LIVINGPROOF_VERIFIER_VERSION", "dev")
    script = definition.step(WORKFLOW, "Assess every trial")
    done = subprocess.run(
        ["bash", "-e", "-c", script], cwd=verifier, env=env, capture_output=True, text=True
    )
    report = verifier / "verified" / "verification.json"
    return done, (json.loads(report.read_text()) if report.exists() else None)


_UV_STUB = """#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
dest = Path(sys.argv[-1])
src = os.environ.get("EVIDENCE_FIXTURE")
if src:
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
raise SystemExit(int(os.environ["EVIDENCE_EXIT"]))
"""


def _evidence_step(tmp_path: Path, code: int, fixture: Path | None):
    robot = tmp_path / "robot"
    robot.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uv").write_text(_UV_STUB)
    (bindir / "uv").chmod(0o755)
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "EVIDENCE_EXIT": str(code),
        "ADAPTER": "lpl_g1.adapter:ROBOT",
        "CONFIGURATION": "playground",
    }
    if fixture is not None:
        env["EVIDENCE_FIXTURE"] = str(fixture)
    script = definition.step(WORKFLOW, "Evidence (observations only")
    try:
        definition.sh(script, robot, env, tmp_path / "out.txt")
    except definition.DefinitionError:
        return None
    return robot / "artifacts" / "evidence" / "playground"


def test_real_agreeing_g1_evidence_is_pass_and_exit_0(tmp_path):
    done, report = _assess(_verifier_tree(tmp_path / "v", None), FIX / "agree")
    assert done.returncode == 0, done.stderr
    assert report["summary"]["grade"] == "PASS" and report["findings"] == []


def test_real_disagreeing_g1_evidence_is_not_pass_and_exits_4(tmp_path):
    """All three trials cross the exit, two trajectories: never a robot PASS."""
    manifest = json.loads((FIX / "disagree" / "manifest.json").read_text())
    assert manifest["trials"]["reproducibility"]["identical"] is False
    assert {t["outcome"] for t in manifest["trials"]["results"]} == {"crossed_exit"}
    done, report = _assess(_verifier_tree(tmp_path / "v", None), FIX / "disagree")
    assert done.returncode == 4, done.stderr
    assert report["summary"]["grade"] == "INSUFFICIENT_EVIDENCE"
    assert [f["kind"] for f in report["findings"]] == ["test-system"]
    assert "test-system reproducibility:" in done.stdout


@pytest.mark.parametrize("code", [1, 2, 3, 5, 124, 130, 137, 143])
def test_every_other_evidence_exit_stops_before_verify(tmp_path, code):
    assert _evidence_step(tmp_path, code, FIX / "agree") is None


def test_real_exit_4_evidence_continues_and_head_verify_fails_the_job(tmp_path):
    evidence = _evidence_step(tmp_path, 4, FIX / "disagree")
    assert evidence is not None
    done, _ = _assess(_verifier_tree(tmp_path / "v", None), evidence)
    assert done.returncode == 4


# --- gaps at add36d9 (expected to fail until the workflow itself gates on the finding) ---


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs git history for the base")
def test_finding_fails_the_job_with_the_base_release_verifier(tmp_path):
    """scenario.yml runs the verifier at the lock's commit. A lock that pins a release
    without job_exit must not turn a disagreement into a green verify job."""
    evidence = _evidence_step(tmp_path, 4, FIX / "disagree")
    done, report = _assess(_verifier_tree(tmp_path / "v", BASE), evidence)
    assert report["findings"], "the base verifier still records the finding"
    assert done.returncode != 0, "verify job is green, so report and PR comment run"


def test_evidence_exit_4_without_a_finding_is_not_green(tmp_path):
    """Exit 4 is only meaningful with a recorded finding; a manifest without one is not a pass."""
    evidence = _evidence_step(tmp_path, 4, FIX / "agree")
    if evidence is None:  # stopped at the evidence job: not green
        return
    done, _ = _assess(_verifier_tree(tmp_path / "v", None), evidence)
    assert done.returncode != 0


def test_disagreeing_repeats_with_a_failing_trial_are_not_a_robot_fail(tmp_path):
    """#55: verify reports the finding as test-system, never as a robot PASS or FAIL."""
    done, report = _assess(_verifier_tree(tmp_path / "v", None), FIX / "disagree-fail")
    assert done.returncode == 4
    assert report["summary"]["grade"] != "FAIL"
