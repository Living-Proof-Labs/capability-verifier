# SPDX-License-Identifier: Apache-2.0
"""A declared Scenario line's provenance reaches the bundle at collect (#273).

The Scenario line runs in the trial job, away from the collect job. The loader writes the
resolved repo, ref, commit and descriptor digest beside the results it uploads, and the
collector stores them at ``assessment.scenario_provenance[<id>]``, the way an advisory
evaluator's record is stored. A bundle with no declared Scenario line has no such key.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from test_collect_integrity import _bundle, _robot_grades, _write
from test_evaluators import COMMIT, ROOT, _no_branch, _plant, _resolve, _schema_fetch

from capability_verifier import collect as collector
from capability_verifier.collect import ScenarioProvenanceError, collect
from capability_verifier.evaluators import run_lines

SCENARIO = ROOT / "tests" / "fixtures" / "evaluators" / "scenario.json"
LINE = "acme/widgets@v1.2.3/scenario.json"
EVALUATOR = "livingproof-scenario"
FILE = f"{EVALUATOR}.scenario-provenance.json"
IDENTITY = {
    "line": LINE,
    "repo": "acme/widgets",
    "ref": "v1.2.3",
    "commit": COMMIT,
    "descriptor_sha256": "",
    "package_name": "livingproof-evaluator-scenario",
    "installed_version": "1.2.3",
}


def _run(calls: list):
    def run(command, **kwargs):
        calls.append(list(command))
        if command[:2] == ["git", "ls-remote"]:
            return _no_branch(command, **kwargs)
        if command[0] == "uv" and command[1] == "pip":
            _plant(Path(command[4]), "livingproof-evaluator-scenario", "1.2.3", COMMIT)
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def _loader(tmp_path, phase="trial", world=False, line=LINE, **extra):
    evidence = tmp_path / "manifest.json"
    scenario = "walk-corner@0.1.0" if world else "walk-hallway@0.1.0"
    evidence.write_text(json.dumps({"lock": {"content": {"scenario": scenario}}}) + "\n")
    out = tmp_path / "out"
    records = run_lines(
        line + "\n",
        evidence=evidence,
        out=out,
        work=tmp_path / "work",
        phase=phase,
        kind="scenario",
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=_run([]),
        injected={line: SCENARIO.read_bytes()},
        **extra,
    )
    return out, records


def _stored(out):
    return json.loads((out / FILE).read_text(encoding="utf-8"))


def _provenance(**changes):
    record = dict(IDENTITY, **changes)
    return {
        "format": "livingproof-scenario-provenance/1.0.0",
        "evaluator": {"id": EVALUATOR},
        "provenance": record,
    }


def _plant_file(directory: Path, document: dict, name: str = FILE) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(document) + "\n", encoding="utf-8")


# --- the loader writes it ----------------------------------------------------------------


def test_the_loader_writes_the_resolved_pin_beside_the_scenario_results(tmp_path):
    out, records = _loader(tmp_path)
    document = _stored(out)
    assert document["format"] == "livingproof-scenario-provenance/1.0.0"
    assert document["evaluator"] == {"id": EVALUATOR}
    record = document["provenance"]
    assert set(record) == set(IDENTITY)
    assert record["repo"] == "acme/widgets"
    assert record["ref"] == "v1.2.3"
    assert record["commit"] == COMMIT
    assert record["descriptor_sha256"] == records[0].descriptor_sha256
    assert len(record["descriptor_sha256"]) == 64
    assert record["line"] == LINE
    assert record["installed_version"] == "1.2.3"


def test_the_record_carries_no_per_phase_outcome(tmp_path):
    """The trial and the media job write one identical file, so they never disagree."""
    record = _stored(_loader(tmp_path)[0])["provenance"]
    assert "outcome" not in record and "exit_code" not in record


def test_a_skipped_media_phase_writes_nothing(tmp_path):
    out, records = _loader(tmp_path, phase="media")
    assert records[0].outcome == "skipped"
    assert not (out / FILE).exists()


def test_the_existing_provenance_file_is_unchanged(tmp_path):
    out, _ = _loader(tmp_path)
    listed = json.loads((out / "evaluators-provenance.json").read_text(encoding="utf-8"))
    assert [set(item) for item in listed] == [
        {
            "line",
            "repo",
            "ref",
            "commit",
            "descriptor_sha256",
            "package_name",
            "installed_version",
            "exit_code",
            "outcome",
        }
    ]


# --- the collector stores it -------------------------------------------------------------


def test_collect_stores_the_record_keyed_by_evaluator_id_and_it_does_not_grade(tmp_path):
    bundle, _ = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    before = copy.deepcopy(bundle)
    out, records = _loader(tmp_path)
    collect(bundle, out)
    stored = bundle["assessment"]["scenario_provenance"]
    assert list(stored) == [EVALUATOR]
    assert stored[EVALUATOR] == _stored(out)["provenance"]
    assert stored[EVALUATOR]["descriptor_sha256"] == records[0].descriptor_sha256
    assert _robot_grades(bundle) == grades
    for key in before["assessment"]:
        assert bundle["assessment"][key] == before["assessment"][key]
    assert "advisory_provenance" not in bundle["assessment"]


def test_a_bundle_with_no_scenario_line_has_no_scenario_provenance(tmp_path):
    bundle, _ = _bundle(tmp_path)
    collect(bundle, tmp_path / "empty")
    assert "scenario_provenance" not in bundle["assessment"]
    downloads = tmp_path / "downloads"
    _write(downloads, "evaluator-results-x", {"format": "not-a-result"})
    collect(bundle, downloads)
    assert "scenario_provenance" not in bundle["assessment"]


def test_the_trial_and_the_media_copies_agree_and_are_stored_once(tmp_path):
    bundle, _ = _bundle(tmp_path)
    document = _provenance(descriptor_sha256="d" * 64)
    _plant_file(tmp_path / "r" / "trial", document)
    _plant_file(tmp_path / "r" / "media", document)
    collect(bundle, tmp_path / "r")
    assert bundle["assessment"]["scenario_provenance"][EVALUATOR] == document["provenance"]


def test_two_different_records_for_one_evaluator_fail_the_collection(tmp_path):
    bundle, _ = _bundle(tmp_path)
    _plant_file(tmp_path / "r" / "trial", _provenance(descriptor_sha256="d" * 64))
    _plant_file(tmp_path / "r" / "media", _provenance(descriptor_sha256="e" * 64))
    with pytest.raises(ScenarioProvenanceError, match=EVALUATOR):
        collect(bundle, tmp_path / "r")
    assert "scenario_provenance" not in bundle["assessment"]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"repo": "not a repo"}, "repo"),
        ({"ref": "main"}, "ref"),
        ({"commit": "A" * 40}, "commit"),
        ({"commit": "a" * 39}, "commit"),
        ({"descriptor_sha256": "d" * 63}, "descriptor_sha256"),
        ({"descriptor_sha256": "D" * 64}, "descriptor_sha256"),
        ({"line": 5}, "line"),
        ({"package_name": ""}, "package_name"),
        ({"installed_version": 3}, "installed_version"),
        ({"extra": "x"}, "unexpected"),
    ],
)
def test_a_malformed_record_fails_the_collection(tmp_path, change, match):
    bundle, _ = _bundle(tmp_path)
    document = _provenance(descriptor_sha256="d" * 64)
    document["provenance"].update(change)
    _plant_file(tmp_path / "r", document)
    with pytest.raises(ScenarioProvenanceError, match=match):
        collect(bundle, tmp_path / "r")
    assert "scenario_provenance" not in bundle["assessment"]


@pytest.mark.parametrize("ref", ["v1.2.3", "b" * 40])
def test_a_tag_or_a_commit_ref_is_accepted(tmp_path, ref):
    bundle, _ = _bundle(tmp_path)
    _plant_file(tmp_path / "r", _provenance(ref=ref, descriptor_sha256="d" * 64))
    collect(bundle, tmp_path / "r")
    assert bundle["assessment"]["scenario_provenance"][EVALUATOR]["ref"] == ref


def test_a_missing_record_part_or_a_bad_evaluator_id_fails(tmp_path):
    bundle, _ = _bundle(tmp_path)
    document = _provenance(descriptor_sha256="d" * 64)
    del document["provenance"]
    _plant_file(tmp_path / "a", document)
    with pytest.raises(ScenarioProvenanceError, match="provenance"):
        collect(bundle, tmp_path / "a")
    document = _provenance(descriptor_sha256="d" * 64)
    document["evaluator"]["id"] = "Not An Id"
    _plant_file(tmp_path / "b", document)
    with pytest.raises(ScenarioProvenanceError, match="evaluator"):
        collect(bundle, tmp_path / "b")


def test_the_command_reports_a_bad_record_and_leaves_the_manifest_unchanged(tmp_path, capsys):
    _bundle_data, dest = _bundle(tmp_path)
    manifest = dest / "manifest.json"
    before = manifest.read_bytes()
    document = _provenance(descriptor_sha256="d" * 64)
    document["provenance"]["commit"] = "zz"
    _plant_file(tmp_path / "r", document)
    code = collector.main([str(manifest), str(tmp_path / "r"), str(tmp_path / "report")])
    assert code == 1
    assert "commit" in capsys.readouterr().err
    assert manifest.read_bytes() == before


def test_the_reporter_still_renders_a_bundle_that_carries_the_record(tmp_path):
    from livingproof_capability_report.assessed import report_from_bundle
    from livingproof_capability_report.core import render

    bundle, _dest = _bundle(tmp_path)
    plain = render(report_from_bundle(copy.deepcopy(bundle)), "markdown")
    _plant_file(tmp_path / "r", _provenance(descriptor_sha256="d" * 64))
    collect(bundle, tmp_path / "r")
    assert render(report_from_bundle(bundle), "markdown") == plain
