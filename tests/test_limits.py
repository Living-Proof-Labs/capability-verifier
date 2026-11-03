# SPDX-License-Identifier: Apache-2.0
"""specs #30: resolved limits and the scenario description pass through into the bundle."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from capability_verifier import hallway

PLAN = {
    "scenario": "walk-hallway@0.1.0",
    "trials": 3,
    "seeds": [1, 2, 3],
    "command_speed": 0.5,
    "required_passes": 3,
}
# Widths are each robot repository's robot.json dimensions.width.value at the merged
# adopt commits; the limits are the unrounded values the verifier grades against.
ROBOTS = [
    ("unitree-g1", "humanoid", 0.52, 0.49, 0.05),
    ("unitree-go1", "quadruped", 0.282, 0.609, 0.10),
    ("booster-t1", "humanoid", 0.541, 0.4795, 0.05),
]


def _tool():
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_limits", Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(tmp_path, robot="unitree-g1", cls="humanoid", width=0.52, lateral=0.21):
    trials = []
    for seed in PLAN["seeds"]:
        o = {
            "format": hallway.OBSERVATION_FORMAT,
            "end": "crossed_exit",
            "duration_s": 12.0,
            "exit_time": 12.0,
            "max_abs_lateral": {"value": lateral if seed == 2 else 0.21, "time": 7.3},
            "min_base_height": {"value": 0.71, "time": 3.0},
            "wall_contacts": [],
            "trace": {
                "columns": ["t", "x", "y", "z", "wall_distance"],
                "samples": [[0.0, -2.5, 0.0, 0.75, 0.3], [12.0, 3.0, 0.1, 0.74, 0.2]],
            },
        }
        p = tmp_path / f"trial-{seed:02d}.json"
        p.write_text(json.dumps(o))
        trials.append(
            {"seed": seed, "path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        )
    m = {
        "format": hallway.MANIFEST_FORMAT,
        "plan": PLAN,
        "robot": {
            "id": robot,
            "profile": {
                "class": cls,
                "dimensions": {"width": {"value": width, "unit": "m", "source": "measured"}},
            },
        },
        "configuration": {
            "name": "stock",
            "version": "1.0.0",
            "profile": {"locomotion": {"fall_base_height": {"value": 0.5, "unit": "m"}}},
        },
        "world": {
            "id": "hallway-open",
            "version": "0.1.0",
            "clear_width": 1.5,
            "route_length": 5.5,
        },
        "preconditions": [{"name": "fits_width", "met": True}],
        "trials": trials,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(m))
    return path


DESCRIPTION = {
    "format": "livingproof-scenario-description/0.1",
    "scenario": {"id": "walk-hallway", "revision": "0.1.0"},
    "mission": {"title": "Walk the hallway", "description": "text"},
    "world": {"id": "hallway-open", "content_version": "0.1.0", "title": "Open hallway"},
    "parameters": {},
    "limits": {"lateral_limit": {"formula": "clear_width ÷ 2 − robot_width ÷ 2"}},
}


@pytest.mark.parametrize(("robot", "cls", "width", "limit", "clearance"), ROBOTS)
def test_limits_name_the_width_formula_and_unrounded_value(
    tmp_path, robot, cls, width, limit, clearance
):
    m = json.loads(_manifest(tmp_path, robot, cls, width).read_text())
    lim = hallway.limits(m)
    th = hallway.thresholds(m)
    lat = lim["lateral_limit"]
    assert lat["value"] == limit == th["lateral_limit"]
    assert lat["inputs"]["robot_width"] == width and lat["inputs"]["clear_width"] == 1.5
    assert lat["formula"] == "clear_width / 2 - robot_width / 2"
    assert lim["exit_segment"]["value"] == th["half_width"] == 0.75
    assert lim["time_limit"]["value"] == th["time_limit"] == 27.0
    assert lim["fall_height"]["value"] == th["fall_height"] == 0.5
    fit = lim["fits_width"]
    assert fit["met"] and fit["inputs"]["wall_clearance"] == clearance
    assert fit["inputs"]["robot_class"] == cls


def test_verify_result_carries_limits_and_grades_as_before(tmp_path):
    r = hallway.verify(_manifest(tmp_path, lateral=0.5))  # 0.5 > 0.49: seed 2 fails
    assert r["limits"]["lateral_limit"]["value"] == r["thresholds"]["lateral_limit"]
    assert (r["summary"]["k"], r["summary"]["grade"]) == (2, hallway.FAIL)
    assert [t["status"] for t in r["trials"]] == [hallway.PASS, hallway.FAIL, hallway.PASS]


@pytest.mark.parametrize("bundle_version", ["1.0.0", "1.1.0"])
def test_bundle_carries_limits_and_no_description_before_the_catalog_has_one(
    tmp_path, monkeypatch, bundle_version
):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    src = tmp_path / "evidence"
    src.mkdir()
    out = tmp_path / "verified"
    _tool().export(
        _manifest(src), out, True, bundle_version, description_path=tmp_path / "absent.json"
    )
    bundle = json.loads((out / "bundle" / "manifest.json").read_text())
    for r in bundle["results"]:
        assert r["provenance"]["limits"]["lateral_limit"]["value"] == 0.49
        assert r["provenance"]["limits"]["lateral_limit"]["inputs"]["robot_width"] == 0.52
        assert "description" not in r["provenance"]
    assert "description" not in bundle


def test_bundle_passes_the_description_through_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    src = tmp_path / "evidence"
    src.mkdir()
    text = tmp_path / "description.json"
    text.write_text(json.dumps(DESCRIPTION, ensure_ascii=False))
    out = tmp_path / "verified"
    tool = _tool()
    tool.export(_manifest(src), out, True, "1.1.0", description_path=text)
    bundle = json.loads((out / "bundle" / "manifest.json").read_text())
    d = copy.deepcopy(bundle["description"])
    source = d.pop("source")
    assert d == DESCRIPTION
    assert source["sha256"] == hashlib.sha256(text.read_bytes()).hexdigest()
    assert "commit" not in source  # not the locked catalog's own file
    assert source["path"] == "specs/scenarios/walk-hallway/description.json"
    assert all("description" not in r["provenance"] for r in bundle["results"])


@pytest.mark.parametrize(
    "change",
    [
        {"format": "livingproof-scenario-description/0.2"},
        {"scenario": {"id": "walk-hallway", "revision": "0.2.0"}},
        {"world": {"id": "hallway-open", "content_version": "0.2.0"}},
    ],
)
def test_bundle_refuses_a_description_for_another_scenario_or_world(tmp_path, monkeypatch, change):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    src = tmp_path / "evidence"
    src.mkdir()
    text = tmp_path / "description.json"
    text.write_text(json.dumps({**DESCRIPTION, **change}))
    out = tmp_path / "verified"
    with pytest.raises(SystemExit):
        _tool().export(_manifest(src), out, True, "1.1.0", description_path=text)
    assert not out.exists()


def test_the_locked_catalog_description_names_its_commit(tmp_path):
    tool = _tool()
    text = tmp_path / "description.json"
    text.write_text(json.dumps(DESCRIPTION))
    d = tool.description(text, "c" * 40)
    assert d["source"]["commit"] == "c" * 40
    assert tool.description(tmp_path / "absent.json") is None
