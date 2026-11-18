# SPDX-License-Identifier: Apache-2.0
"""Declared contracts must survive export, not just the first grading stage."""

import json

import pytest
import test_declared_catalog as base
import test_walk_corner as corner

from capability_verifier import evaluate


def _setup(tmp_path, monkeypatch):
    specs = base._specs_root(tmp_path, base._parameters())
    worlds = base._worlds_root(tmp_path)
    (tmp_path / ".specs-hallway").symlink_to(specs, target_is_directory=True)
    (tmp_path / ".worlds").symlink_to(worlds, target_is_directory=True)
    manifest = base._evidence(
        tmp_path,
        specs=base._declared_specs({**base.SPECS_V230, "tag": "v3.1.0"}, specs, "walk-data"),
    )
    tool = corner._tool()
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "2.3.0")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    return tool, manifest, specs


def test_new_tag_and_scenario_export_same_verdict(tmp_path, monkeypatch):
    tool, manifest, _ = _setup(tmp_path, monkeypatch)
    out = tmp_path / "out"
    result = tool.export(
        manifest,
        out,
        bundle_version="1.1.0",
        declared=True,
        description_path=tmp_path / "no-description.json",
    )
    bundle = json.loads((out / "bundle/manifest.json").read_text())
    assert result["summary"]["grade"] == "PASS"
    assert bundle["results"][0]["decision"]["status"] == "PASS"
    assert bundle["assessment"]["runs"][0]["grade"] == "PASS"
    assert bundle["results"][0]["spec"]["tag"] == "v3.1.0"
    # The legacy path still refuses an unvalidated reference.
    decision = evaluate.AllRequiredV1().decide(bundle["results"][0], True)
    assert decision.status == "INCOMPATIBLE"
    # Validated context cannot silently bless a different spec.
    policy = evaluate.AllRequiredV1(
        validated_spec=bundle["results"][0]["spec"], required=tuple(corner.CORNER_CHECKS)
    )
    changed = {**bundle["results"][0], "spec": {**bundle["results"][0]["spec"], "tag": "v4.0.0"}}
    assert policy.decide(changed, True).status == "INCOMPATIBLE"


@pytest.mark.parametrize("problem", ["digest", "schema", "vocabulary"])
def test_declared_export_still_refuses_bad_inputs(tmp_path, monkeypatch, problem):
    tool, manifest, specs = _setup(tmp_path, monkeypatch)
    parameters = specs / "specs/scenarios/walk-data/parameters.json"
    if problem == "digest":
        parameters.write_bytes(parameters.read_bytes() + b"\n")
    elif problem == "schema":
        (specs / "specs/scenarios/scenario-parameters.schema.json").unlink()
    else:
        content = json.loads(parameters.read_text())
        content["checks"][0]["evaluator"] = "unsupported-check"
        parameters.write_text(json.dumps(content))
        doc = json.loads(manifest.read_text())
        doc["lock"]["content"]["releases"]["specs"]["files"][
            "specs/scenarios/walk-data/parameters.json"
        ] = base._sha(parameters)
        manifest.write_text(json.dumps(doc))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        tool.export(
            manifest,
            out,
            bundle_version="1.1.0",
            declared=True,
            description_path=tmp_path / "no-description.json",
        )
    assert exc.value.code == 2
    assert (out / "refusal.json").exists()
    assert not (out / "bundle/manifest.json").exists()
