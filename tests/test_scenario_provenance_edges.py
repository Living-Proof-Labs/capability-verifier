# SPDX-License-Identifier: Apache-2.0
"""Edges of the scenario provenance record that no test pinned (#273).

Each test below fails on a mutation that the first suite let through: dropping the
``loader --bundle`` placement, loosening the tag or repo grammar, and the command's
success and conflict paths with a real trial result beside the record.
"""

from __future__ import annotations

import json

import pytest
from test_collect_counts import PATTERN, _layout, _result
from test_collect_integrity import _bundle
from test_scenario_provenance import EVALUATOR, _loader, _plant_file, _provenance, _stored

from capability_verifier import collect as collector
from capability_verifier.collect import ScenarioProvenanceError, collect

TRIAL = "playground-seed-01"
DIGEST = "d" * 64


@pytest.fixture(autouse=True)
def _thresholds(monkeypatch):
    monkeypatch.setattr(
        "capability_verifier.evaluators._hallway_thresholds",
        lambda manifest: {"time_limit": 1, "lateral_limit": 1, "half_width": 1, "fall_height": 1},
    )


def test_the_bundle_placement_stores_the_record_the_file_carries(tmp_path):
    """``loader --bundle`` places the same seven fields the uploaded file carries."""
    bundle, dest = _bundle(tmp_path)
    manifest = dest / "manifest.json"
    manifest.write_text(json.dumps(bundle), encoding="utf-8")
    out, _records = _loader(tmp_path, bundle=manifest)
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(stored["assessment"]["scenario_provenance"]) == [EVALUATOR]
    assert stored["assessment"]["scenario_provenance"][EVALUATOR] == _stored(out)["provenance"]


@pytest.mark.parametrize(
    "ref",
    ["v01.2.3", "v1.02.3", "v1.2.03", "v1.2", "v1.2.3.4", "v1.2.3-rc1", "v1.2.3+b1", "V1.2.3"]
    + ["v1.2.3\n", " v1.2.3", "v1.2.x", "1.2.3", "a" * 39, "a" * 41, "A" * 40],
)
def test_a_ref_that_is_not_a_plain_tag_or_a_lowercase_commit_fails(tmp_path, ref):
    bundle, _ = _bundle(tmp_path)
    _plant_file(tmp_path / "r", _provenance(ref=ref, descriptor_sha256=DIGEST))
    with pytest.raises(ScenarioProvenanceError, match="ref"):
        collect(bundle, tmp_path / "r")
    assert "scenario_provenance" not in bundle["assessment"]


@pytest.mark.parametrize(
    "repo",
    ["acme/..", "acme/.", "acme/widgets/extra", "/widgets", "acme/", "acme", "-acme/widgets"]
    + ["acme/widgets\n", "acme/ widgets", "acmé/widgets", "acme/widgets?x=1", "acme/wid gets"],
)
def test_a_repo_that_is_not_owner_slash_name_fails(tmp_path, repo):
    bundle, _ = _bundle(tmp_path)
    _plant_file(tmp_path / "r", _provenance(repo=repo, descriptor_sha256=DIGEST))
    with pytest.raises(ScenarioProvenanceError, match="repo"):
        collect(bundle, tmp_path / "r")
    assert "scenario_provenance" not in bundle["assessment"]


def test_the_command_stores_the_record_beside_a_trial_result(tmp_path, capsys, monkeypatch):
    """The command path a job runs: a trial result and the record, one manifest write."""
    monkeypatch.setenv("EVALUATOR_RESULTS_PATTERN", PATTERN)
    args, manifest, _original = _layout(tmp_path, [TRIAL], [_result(TRIAL)])
    _plant_file(tmp_path / "evaluator-results" / "trial", _provenance(descriptor_sha256=DIGEST))
    assert collector.main(args) == 0
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert [len(r["evaluator_results"]) for r in stored["results"]] == [1]
    assert stored["assessment"]["scenario_provenance"] == {
        EVALUATOR: _provenance(descriptor_sha256=DIGEST)["provenance"]
    }
    assert "scenario" not in capsys.readouterr().err


def test_the_command_refuses_two_different_records_and_leaves_the_manifest(
    tmp_path, capsys, monkeypatch
):
    """A trial result is present, so only the conflict can fail the run."""
    monkeypatch.setenv("EVALUATOR_RESULTS_PATTERN", PATTERN)
    args, manifest, original = _layout(tmp_path, [TRIAL], [_result(TRIAL)])
    results = tmp_path / "evaluator-results"
    _plant_file(results / "trial", _provenance(descriptor_sha256="d" * 64))
    _plant_file(results / "media", _provenance(descriptor_sha256="e" * 64))
    assert collector.main(args) == 1
    err = capsys.readouterr().err
    assert EVALUATOR in err and "two different" in err
    assert manifest.read_text(encoding="utf-8") == original
