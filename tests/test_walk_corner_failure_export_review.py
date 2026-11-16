# SPDX-License-Identifier: Apache-2.0
"""A failing walk-corner run exports a FAIL bundle (Vera review of #116).

livingproof-evaluator-scenario v1.3.0 reports a ``corner-turned`` failure with the place
it missed and no numeric limit (it is an order check). The bundle must carry that failure
and decide FAIL, not refuse the assessment.
"""

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from capability_verifier import hallway

ROOT = Path(__file__).parents[1]
RUN = ROOT / "tests" / "fixtures" / "corner" / "g1-stock"


def _export(manifest, out, bundle_version):
    path = ROOT / "tools" / "verify_hallway.py"
    loaded = importlib.util.spec_from_file_location("verify_hallway_failure_review", path)
    tool = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(tool)
    tool.export(manifest, out, False, bundle_version)
    return json.loads((out / "bundle" / "manifest.json").read_text())


def _never_turned(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(RUN, run)
    manifest = json.loads((run / "manifest.json").read_text())
    trial = run / manifest["trials"]["results"][0]["path"]
    observation = json.loads(trial.read_text())
    corner = next(
        p for p in hallway.world_document(manifest["world"])["places"] if p["name"] == "corner"
    )
    xs = [v[0] for v in corner["polygon"]] if "polygon" in corner else [-0.75, 0.75]
    ys = [v[1] for v in corner["polygon"]] if "polygon" in corner else [-0.75, 0.75]
    observation["trace"]["samples"] = [
        s
        for s in observation["trace"]["samples"]
        if not (min(xs) <= s[1] <= max(xs) and min(ys) <= s[2] <= max(ys))
    ]
    trial.write_text(json.dumps(observation))
    manifest["trials"]["results"][0]["sha256"] = hashlib.sha256(trial.read_bytes()).hexdigest()
    (run / "manifest.json").write_text(json.dumps(manifest))
    return run / "manifest.json"


@pytest.mark.parametrize("bundle_version", ["1.0.0", "1.1.0"])
def test_a_run_that_never_turns_exports_a_fail_bundle(tmp_path, monkeypatch, bundle_version):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    manifest = _never_turned(tmp_path)
    assert hallway.verify(manifest)["summary"]["grade"] == hallway.FAIL
    bundle = _export(manifest, tmp_path / "verified", bundle_version)
    [result] = bundle["results"]
    assert result["spec"]["capability"] == "walk-corner"
    if bundle_version == "1.1.0":
        assert result["decision"]["status"] == hallway.FAIL
        assert [f["check"] for f in result["decision"]["failures"]] == ["corner-turned"]


def _assessment(**extra):
    f = {"check": "corner-turned", "observed": None, "limit": None, "unit": "s"}
    f.update(t=None, seed=1, **extra)
    a = {"id": "x", "evaluator": f["check"], "status": hallway.FAIL}
    a["provenance"] = {"details": {"failures": [f]}}
    return a


def test_a_missed_place_with_no_limit_is_a_valid_structured_failure():
    from capability_verifier.evaluate import structured_failures

    [f] = structured_failures(_assessment(place="corner"))
    assert (f["place"], f["limit"]) == ("corner", None)


@pytest.mark.parametrize("bad", [{}, {"place": ""}, {"place": 3}, {"place": None}])
def test_a_null_limit_still_needs_the_place_it_missed(bad):
    from capability_verifier.evaluate import structured_failures

    with pytest.raises(ValueError):
        structured_failures(_assessment(**bad))
