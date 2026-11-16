# SPDX-License-Identifier: Apache-2.0
"""The report job's collector logs what it appended and fails on nothing (#62).

Per evaluator, the collector prints how many result documents it appended to
this configuration's bundle and how many it skipped because their trial is not
in this bundle (another configuration). A bundle with trials and no appended
result fails and names the artifact pattern it expected. Skipped results alone
do not fail a run that also appended some, and a bundle with no trials (a
"not supported" run) has nothing to append and passes.
"""

from __future__ import annotations

import json
from pathlib import Path

from capability_verifier.collect import main

ROOT = Path(__file__).parents[1]
PATTERN = "walk-hallway-evaluator-results-*-playground"


def _result(trial: str, evaluator: str = "livingproof-scenario") -> dict:
    return {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": evaluator},
        "inputs": [],
        "scenario": {"trial_id": trial, "steps": []},
    }


def _layout(tmp_path: Path, trials: list[str], documents: list[dict]):
    manifest = tmp_path / "manifest.json"
    original = json.dumps({"results": [{"id": t} for t in trials]}, indent=2) + "\n"
    manifest.write_text(original, encoding="utf-8")
    results = tmp_path / "evaluator-results"
    for document in documents:
        folder = results / document["evaluator"]["id"]
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{document['scenario']['trial_id']}.json"
        (folder / name).write_text(json.dumps(document) + "\n", encoding="utf-8")
    report = tmp_path / "reports"
    report.mkdir()
    return [str(manifest), str(results), str(report)], manifest, original


def test_counts_are_logged_per_evaluator_and_skipped_results_do_not_fail(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("EVALUATOR_RESULTS_PATTERN", PATTERN)
    args, manifest, _original = _layout(
        tmp_path,
        ["playground-seed-01", "playground-seed-02"],
        [
            _result("playground-seed-01"),
            _result("playground-seed-02"),
            _result("stock-seed-01"),
            _result("playground-seed-01", "example-observer"),
        ],
    )
    assert main(args) == 0
    out = capsys.readouterr().out.splitlines()
    assert "evaluator example-observer: appended 1, skipped 0 (trial not in this bundle)" in out
    assert "evaluator livingproof-scenario: appended 2, skipped 1 (trial not in this bundle)" in out
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert [len(r["evaluator_results"]) for r in stored["results"]] == [2, 1]


def test_nothing_appended_for_this_configuration_fails_and_names_the_pattern(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("EVALUATOR_RESULTS_PATTERN", PATTERN)
    args, manifest, original = _layout(tmp_path, ["playground-seed-01"], [_result("stock-seed-01")])
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "evaluator livingproof-scenario: appended 0, skipped 1" in captured.out
    assert "no evaluator result was appended" in captured.err
    assert PATTERN in captured.err
    assert manifest.read_text(encoding="utf-8") == original


def test_an_empty_download_fails_too(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("EVALUATOR_RESULTS_PATTERN", PATTERN)
    args, manifest, original = _layout(tmp_path, ["playground-seed-01"], [])
    assert main(args) == 1
    captured = capsys.readouterr()
    assert PATTERN in captured.err and "no evaluator result was appended" in captured.err
    assert manifest.read_text(encoding="utf-8") == original


def test_a_bundle_with_no_trials_has_nothing_to_append(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("EVALUATOR_RESULTS_PATTERN", PATTERN)
    args, _manifest, _original = _layout(tmp_path, [], [])
    assert main(args) == 0
    assert "no trials in this bundle" in capsys.readouterr().out


def test_the_report_job_passes_the_expected_pattern_to_the_collector():
    text = (ROOT / ".github/workflows/scenario.yml").read_text(encoding="utf-8")
    start = text.index("- name: Append each evaluator's result documents")
    step = text[start : text.index("- uses:", start)]
    assert (
        "EVALUATOR_RESULTS_PATTERN: "
        "${{ inputs.artifact-prefix }}evaluator-results-*-${{ matrix.configuration }}"
    ) in step
