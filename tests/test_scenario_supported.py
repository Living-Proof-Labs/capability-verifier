# SPDX-License-Identifier: Apache-2.0
"""An unmet scenario_supported record is UNSUPPORTED, and the report says not supported.

robot-capability-specs v2.3.0: a scenario that requires ``scenario_supported`` and is
not declared is graded before any trial. The evidence record is
``UNSUPPORTED: precondition scenario_supported unmet`` with no trials. That grade is
never FAIL and is never a robot failure. A record with trials grades as before.
"""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from capability_verifier import hallway

RECORD = "UNSUPPORTED: precondition scenario_supported unmet"
ROBOT = "unitree-g1"


def _observation(**changes):
    observation = {
        "format": hallway.OBSERVATION_FORMAT,
        "end": "crossed_exit",
        "duration_s": 12.0,
        "exit_time": 12.0,
        "max_abs_lateral": {"value": 0.21, "time": 7.3},
        "min_base_height": {"value": 0.71, "time": 3.0},
        "wall_contacts": [],
        "trace": {
            "columns": ["t", "x", "y", "z", "wall_distance"],
            "samples": [
                [0.0, -2.5, 0.0, 0.75, 0.3],
                [9.8, 1.0, 0.21, 0.74, 0.041],
                [10.1, 1.2, 0.21, 0.73, 0.05],
                [12.0, 3.0, 0.1, 0.74, 0.2],
            ],
        },
    }
    observation.update(changes)
    return observation


def _failing_lateral():
    return _observation(max_abs_lateral={"value": 0.62, "time": 10.1})


def _profile():
    return {
        "robot": {
            "id": ROBOT,
            "profile": {"class": "humanoid", "dimensions": {"width": 0.42}},
        },
        "configuration": {
            "name": "stock",
            "version": "1.1.0",
            "profile": {"locomotion": {"fall_base_height": 0.5}},
        },
        "world": {
            "id": "hallway-open",
            "version": "0.1.0",
            "clear_width": 1.4,
            "route_length": 5.5,
        },
        "preconditions": [
            {"name": "fits_width", "met": True, "detail": "0.42 + 2 x 0.05 = 0.52 m vs 1.4 m"}
        ],
    }


def _write(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def _v1(tmp_path: Path, *, scenario: str, record: str | None, failing: bool, trials: int) -> Path:
    """One seeded plan. ``trials`` 0 with the record is the no-trial case that grades FAIL today."""
    seeds = list(range(1, trials + 1))
    rows = []
    for seed in seeds:
        body = _failing_lateral() if failing else _observation()
        name = f"trial-{seed:02d}.json"
        target = tmp_path / name
        target.write_text(json.dumps(body))
        rows.append(
            {"seed": seed, "path": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        )
    manifest = {
        "format": hallway.MANIFEST_FORMAT,
        "plan": {
            "scenario": scenario,
            "trials": trials,
            "seeds": seeds,
            "command_speed": 0.5,
            "required_passes": 1,
        },
        **_profile(),
        "trials": rows,
    }
    if record is not None:
        manifest["record"] = record
    return _write(tmp_path, manifest)


def _v2_record(tmp_path: Path, scenario: str) -> Path:
    """The simulator shape: the record, a planned trial, and no trial results."""
    manifest = {
        "format": hallway.EVIDENCE_V2,
        "plan": {"scenario": scenario, "command_speed": 0.5, "trials": {"count": 1}},
        **_profile(),
        "trials": {
            "count": 1,
            "results": [],
            "reproducibility": {"compared": False, "identical": None, "finding": None},
        },
        "record": RECORD,
    }
    manifest["world"] = {
        "id": "hallway-corner",
        "version": "0.1.0",
        "clear_width": 1.5,
        "route_length": 5.5,
    }
    return _write(tmp_path, manifest)


def _report(result: dict) -> str:
    return "\n".join(hallway.summary_lines(result))


def test_the_scenario_supported_record_grades_unsupported(tmp_path):
    """walk-corner evidence that holds the record, with no trials, is UNSUPPORTED."""
    result = hallway.verify(_v2_record(tmp_path, "walk-corner@0.1.0"))
    summary = result["summary"]
    assert summary["grade"] == hallway.UNSUPPORTED
    assert summary["grade"] != hallway.FAIL
    assert result["trials"] == []
    assert result["failures"] == []
    report = _report(result)
    assert "not supported" in report
    assert "walk-corner" in report
    assert ROBOT in report
    assert hallway.FAIL not in report


def test_a_no_trial_record_never_grades_fail(tmp_path):
    """No completed trials and the record is not a robot FAIL.

    With the record ignored, k of N on an empty plan is FAIL. The record stays UNSUPPORTED.
    """
    path = _v1(tmp_path, scenario="walk-hallway@0.1.0", record=RECORD, failing=False, trials=0)
    result = hallway.verify(path)
    assert result["summary"]["grade"] == hallway.UNSUPPORTED
    assert result["summary"]["grade"] != hallway.FAIL
    assert result["trials"] == []
    assert result["failures"] == []
    report = _report(result)
    assert "not supported" in report
    assert "walk-hallway" in report
    assert hallway.FAIL not in report


def _sim_unsupported(tmp_path: Path) -> Path:
    """The manifest livingproof-sim-mujoco writes when scenario_supported is unmet.

    No stdout sentence is stored. The world has only id and version. No trial results.
    """
    manifest = {
        "format": hallway.EVIDENCE_V2,
        "plan": {
            "scenario": "walk-corner@0.1.0",
            "command_speed": 0.5,
            "trials": {"count": 1},
        },
        "robot": {"id": ROBOT},
        "configuration": {"name": "stock", "version": "1.1.0"},
        "world": {"id": "hallway-corner", "version": "0.1.0"},
        "preconditions": [
            {
                "name": "scenario_supported",
                "met": False,
                "detail": "walk-corner is not named by the robot manifest scenarios",
            }
        ],
        "trials": {
            "count": 1,
            "results": [],
            "reproducibility": {"compared": False, "identical": None, "finding": None},
        },
    }
    assert "clear_width" not in manifest["world"]
    assert "route_length" not in manifest["world"]
    assert RECORD not in json.dumps(manifest)
    return _write(tmp_path, manifest)


def _sim55_manifest(tmp_path: Path) -> Path:
    """The manifest ``_record_unsupported`` writes at livingproof-sim-mujoco 3b5c638.

    Field names and the walk-corner values come from that function and from
    ``test_real_walk_corner_file_is_unsupported_before_the_world_fixture``.
    ``scenario`` is on the lock, not the plan. The world has only id and version.
    The stdout sentence is not stored.
    """
    source = Path(__file__).parents[1] / "tests" / "fixtures" / "sim-mujoco-55-unsupported.json"
    manifest = json.loads(source.read_text())
    assert manifest["world"] == {"id": "hallway-corner", "version": "0.1.0"}
    assert "scenario" not in manifest["plan"]
    assert manifest["lock"]["content"]["scenario"] == "walk-corner@0.1.0"
    assert manifest["preconditions"] == [
        {
            "name": "scenario_supported",
            "met": False,
            "detail": "walk-corner is not named by the robot manifest scenarios",
        }
    ]
    assert manifest["trials"]["results"] == []
    assert RECORD not in source.read_text()
    path = tmp_path / "manifest.json"
    path.write_text(source.read_text())
    return path


def test_the_manifest_sim_mujoco_55_writes_is_unsupported(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    evidence = _sim55_manifest(tmp_path)
    result = hallway.verify(evidence)
    assert result["summary"]["grade"] == hallway.UNSUPPORTED
    assert result["trials"] == []
    assert result["failures"] == []
    report = _report(result)
    assert "not supported" in report
    assert "walk-corner" in report
    assert "toy-robot" in report
    assert hallway.FAIL not in report
    out = tmp_path / "verified"
    exported = _tool().export(evidence, out, prototype=True)
    assert exported["summary"]["grade"] == hallway.UNSUPPORTED
    assert not (out / "bundle").exists()
    reports = out / "reports"
    for name in ("report.json", "report.md", "report.html", "report.txt"):
        text = (reports / name).read_text()
        assert "not supported" in text
        assert hallway.FAIL not in text
    check = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=reports,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def test_sim_evidence_without_world_geometry_is_unsupported(tmp_path):
    """The #55 record is graded before clear_width is required."""
    result = hallway.verify(_sim_unsupported(tmp_path))
    assert result["summary"]["grade"] == hallway.UNSUPPORTED
    assert result["summary"]["grade"] != hallway.FAIL
    assert result["trials"] == []
    assert result["failures"] == []
    report = _report(result)
    assert "not supported" in report
    assert "walk-corner" in report
    assert ROBOT in report
    assert hallway.FAIL not in report


def test_recorded_trials_beside_unmet_scenario_supported_are_an_error(tmp_path):
    """A FAIL trial list is not rewritten as UNSUPPORTED, and the disagreement is an error."""
    path = _v1(tmp_path, scenario="walk-hallway@0.1.0", record=None, failing=True, trials=1)
    manifest = json.loads(path.read_text())
    manifest["preconditions"].append(
        {
            "name": "scenario_supported",
            "met": False,
            "detail": "walk-hallway is not named by the robot manifest scenarios",
        }
    )
    path.write_text(json.dumps(manifest))
    with pytest.raises(hallway.ManifestError, match="disagreement"):
        hallway.verify(path)


def test_a_normal_pass_record_is_unchanged(tmp_path):
    result = hallway.verify(
        _v1(tmp_path, scenario="walk-hallway@0.1.0", record=None, failing=False, trials=1)
    )
    assert result["summary"]["grade"] == hallway.PASS
    assert result["failures"] == []
    assert "not supported" not in _report(result)


def test_a_normal_fail_record_is_unchanged(tmp_path):
    result = hallway.verify(
        _v1(tmp_path, scenario="walk-hallway@0.1.0", record=None, failing=True, trials=1)
    )
    assert result["summary"]["grade"] == hallway.FAIL
    assert result["failures"]
    assert result["trials"][0]["status"] == hallway.FAIL
    assert "not supported" not in _report(result)


def _tool():
    tool_path = Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    loaded = importlib.util.spec_from_file_location("verify_hallway_supported", tool_path)
    tool = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(tool)
    return tool


def test_export_writes_a_checksummed_report_and_no_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    out = tmp_path / "verified"
    result = _tool().export(_sim_unsupported(tmp_path), out, prototype=True)
    assert result["summary"]["grade"] == hallway.UNSUPPORTED
    assert not (out / "bundle").exists()
    reports = out / "reports"
    for name in ("report.json", "report.md", "report.html", "report.txt", "SHA256SUMS"):
        assert (reports / name).is_file()
    for name in ("report.json", "report.md", "report.html", "report.txt"):
        text = (reports / name).read_text()
        assert "not supported" in text
        assert hallway.FAIL not in text
    body = json.loads((reports / "report.json").read_text())
    assert body["grade"] == hallway.UNSUPPORTED
    assert body["summaries"][0]["failures"] == []
    check = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=reports,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
