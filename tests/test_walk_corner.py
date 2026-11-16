# SPDX-License-Identifier: Apache-2.0
"""walk-corner grading (robot-capability-specs#40 step 11c).

The fixtures are real walk-corner runs from released robot tags (tests/fixtures/corner/
PROVENANCE.json). The checks are walk-corner's parameter file, graded by
livingproof-evaluator-scenario v1.3.0 on the hallway-corner world from the locked
livingproof-worlds release. walk-hallway grading is unchanged.
"""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from capability_verifier import evaluate, hallway
from capability_verifier.scenario_parameters import load_parameters

ROOT = Path(__file__).parents[1]
CORNER = ROOT / "tests" / "fixtures" / "corner"
RUNS = ("g1-stock", "t1-stock", "go1-playground")
CORNER_CHECKS = [
    "corner-exit-reached",
    "corner-turned",
    "corner-route-limit",
    "corner-wall-contact",
    "corner-fall",
]
CORNER_WORLD_SHA256 = "8393881e8b38a090f51c3a424ca444623a901f642f75186cb4d41ac7482898f5"


def _tool():
    path = ROOT / "tools" / "verify_hallway.py"
    loaded = importlib.util.spec_from_file_location("verify_hallway_corner", path)
    tool = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(tool)
    return tool


def _copy(tmp_path, run):
    target = tmp_path / run
    shutil.copytree(CORNER / run, target)
    return target / "manifest.json"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_check_list_is_walk_corners_parameter_file():
    manifest = json.loads((CORNER / "g1-stock" / "manifest.json").read_text())
    document = load_parameters(manifest)
    assert [c["evaluator"] for c in document["checks"]] == CORNER_CHECKS
    assert hallway.scenario_evaluators(manifest) == tuple(CORNER_CHECKS)


@pytest.mark.parametrize("run", RUNS)
def test_released_corner_runs_pass_every_walk_corner_check(run):
    result = hallway.verify(CORNER / run / "manifest.json")
    assert result["summary"]["grade"] == hallway.PASS
    [trial] = result["trials"]
    assert [a["evaluator"] for a in trial["assessments"]] == CORNER_CHECKS
    assert {a["status"] for a in trial["assessments"]} == {hallway.PASS}
    turned = trial["assessments"][1]
    assert turned["rationale"].startswith("passed through corner before the exit")
    assert turned["details"]["threshold"] == ["corner"]


def test_corner_limits_use_the_parameter_file_speed():
    manifest = json.loads((CORNER / "g1-stock" / "manifest.json").read_text())
    assert "command_speed" not in manifest["plan"]
    result = hallway.verify(CORNER / "g1-stock" / "manifest.json")
    assert result["thresholds"]["time_limit"] == 27.0  # 2 * 5.5 / 0.5 + 5
    assert result["limits"]["time_limit"]["inputs"] == {"route_length": 5.5, "command_speed": 0.5}


def test_corner_never_entered_fails_corner_turned(tmp_path):
    path = _copy(tmp_path, "g1-stock")
    manifest = json.loads(path.read_text())
    trial_path = path.parent / manifest["trials"]["results"][0]["path"]
    observation = json.loads(trial_path.read_text())
    # Drop every sample inside the corner square; the exit crossing stays.
    observation["trace"]["samples"] = [
        s
        for s in observation["trace"]["samples"]
        if not (-0.75 <= s[1] <= 0.75 and -0.75 <= s[2] <= 0.75)
    ]
    trial_path.write_text(json.dumps(observation))
    manifest["trials"]["results"][0]["sha256"] = _sha(trial_path)
    path.write_text(json.dumps(manifest))
    result = hallway.verify(path)
    assert result["summary"]["grade"] == hallway.FAIL
    turned = next(
        a for a in result["trials"][0]["assessments"] if a["evaluator"] == "corner-turned"
    )
    assert turned["status"] == hallway.FAIL
    assert turned["rationale"].startswith("did not pass through corner in order before the exit")


def test_a_world_other_than_the_locked_release_is_refused(tmp_path):
    path = _copy(tmp_path, "g1-stock")
    manifest = json.loads(path.read_text())
    assert manifest["lock"]["content"]["world"]["sha256"] == CORNER_WORLD_SHA256
    manifest["lock"]["content"]["world"]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(hallway.ManifestError, match="world"):
        hallway.verify(path)


def test_the_corner_world_comes_from_the_locked_worlds_release():
    lock = json.loads((ROOT / "worlds.lock.json").read_text())
    assert lock["files"]["worlds/hallway-corner/0.1.0/world.json"] == CORNER_WORLD_SHA256
    world = hallway.world_document({"id": "hallway-corner", "version": "0.1.0"})
    assert {p["name"] for p in world["places"]} >= {"route-centreline", "exit", "corner"}


def test_unsupported_corner_evidence_stays_unsupported():
    result = hallway.verify(CORNER / "unsupported" / "manifest.json")
    assert result["summary"]["grade"] == hallway.UNSUPPORTED
    assert result["trials"] == []


def test_scenario_evaluator_writes_corner_results(tmp_path):
    out = tmp_path / "evaluator"
    run = subprocess.run(
        [
            sys.executable,
            "tools/run_scenario_evaluator.py",
            str(CORNER / "t1-stock" / "manifest.json"),
            str(out),
            "2026-11-16T00:00:00Z",
        ],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    [result] = [
        json.loads(p.read_text()) for p in sorted(out.glob("*.json")) if p.name != "thresholds.json"
    ]
    steps = result["scenario"]["steps"]
    assert [s["id"] for s in steps] == CORNER_CHECKS
    assert {s["verdict"] for s in steps} == {"PASS"}


@pytest.mark.parametrize("bundle_version", ["1.0.0", "1.1.0"])
def test_corner_bundle_names_walk_corner_and_passes(tmp_path, monkeypatch, bundle_version):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    out = tmp_path / "verified"
    _tool().export(CORNER / "go1-playground" / "manifest.json", out, False, bundle_version)
    bundle = json.loads((out / "bundle" / "manifest.json").read_text())
    [result] = bundle["results"]
    assert result["spec"]["capability"] == "walk-corner"
    assert result["spec"]["revision"] == "0.1.0"
    assert result["spec"]["tag"] == "v2.3.0"
    assert result["environment"] == {"id": "hallway-corner", "version": "0.1.0"}
    source = bundle["description"]["source"]
    assert source["path"] == "specs/scenarios/walk-corner/description.json"
    assert bundle["description"]["scenario"] == {"id": "walk-corner", "revision": "0.1.0"}
    if bundle_version == "1.1.0":
        assert result["decision"]["status"] == hallway.PASS
        assert bundle["assessment"]["runs"][0]["grade"] == hallway.PASS
        worst = {w["check"]: w for w in bundle["assessment"]["runs"][0]["worst"]}
        assert set(worst) == set(CORNER_CHECKS) - {"corner-turned"}
        assert worst["corner-exit-reached"]["observed"] == 11.98


def test_required_evaluators_come_from_the_scenario_parameter_file():
    spec = {"capability": "walk-corner", "revision": "0.1.0"}
    assert evaluate.required_evaluators(spec) == tuple(CORNER_CHECKS)
    hallway_spec = {"capability": "walk-hallway", "revision": "0.1.0"}
    assert evaluate.required_evaluators(hallway_spec) == hallway.EVALUATORS
