# SPDX-License-Identifier: Apache-2.0
"""What the collector says about a result it leaves off: its own wording, the reason, the evaluator.

Asked by Vera (capability-verifier#202 recheck, Lows 1 and 3). The invalid-JSON skip applies to any
result file, not only those under media/ (Rivet's decision), and the docs say so.
"""

import json
import shutil
from pathlib import Path

from capability_verifier.collect import PROBLEMS_FILE, collect, main

ROOT = Path(__file__).parents[1]
RESULTS = Path(__file__).parent / "fixtures" / "scenario-media-results"
MEDIA = "livingproof-scenario-media"


def _setup(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    manifest, report = tmp_path / "manifest.json", tmp_path / "report"
    report.mkdir()
    manifest.write_text(json.dumps({"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}))
    return directory, manifest, report


def _missing_thresholds(directory):
    shutil.copytree(directory / MEDIA, directory / "a-other-run")  # keeps one evaluator's results
    (directory / MEDIA / "media" / "thresholds.json").unlink()


def test_a_guarded_result_is_not_called_a_trial_from_another_bundle(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    _missing_thresholds(directory)
    assert main([str(manifest), str(directory), str(report)]) == 0
    out = capsys.readouterr().out
    assert "unusable result" in out and "media/thresholds.json" in out
    assert "skipped 2 (trial not in this bundle)" not in out
    assert "skipped 0 (trial not in this bundle)" in out


def test_each_unusable_result_is_named_with_its_reason_on_stdout(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    _missing_thresholds(directory)
    main([str(manifest), str(directory), str(report)])
    lines = [x for x in capsys.readouterr().out.splitlines() if "unusable result" in x]
    assert len(lines) == 2
    assert any("stock-seed-01.json" in x for x in lines)
    assert any("stock-seed-02.json" in x for x in lines)
    assert all("missing" in x for x in lines)


def test_a_trial_from_another_bundle_is_still_a_plain_skip(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    manifest.write_text(json.dumps({"results": [{"id": "stock-seed-01"}, {"id": "other"}]}))
    assert main([str(manifest), str(directory), str(report)]) == 0
    out = capsys.readouterr().out
    assert "appended 1, skipped 1 (trial not in this bundle)" in out
    assert "unusable" not in out


def test_the_problems_file_names_the_evaluator_when_it_is_known(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    _missing_thresholds(directory)
    main([str(manifest), str(directory), str(report)])
    lines = (report / PROBLEMS_FILE).read_text().splitlines()
    assert len(lines) == 2
    document = json.loads((RESULTS / MEDIA / "stock-seed-01.json").read_text())
    evaluator = document["evaluator"]
    for line in lines:
        assert f"evaluator {evaluator['id']} {evaluator['version']}" in line


def test_the_problems_file_says_the_evaluator_is_unknown_for_unreadable_json(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_text("{")
    assert main([str(manifest), str(directory), str(report)]) == 0
    (line,) = (report / PROBLEMS_FILE).read_text().splitlines()
    assert "stock-seed-02.json" in line and "evaluator unknown" in line and "not valid JSON" in line


def test_a_broken_result_outside_media_is_left_off_too(tmp_path, capsys):
    """Rivet's decision: the invalid-JSON skip applies to any result file."""
    directory, manifest, report = _setup(tmp_path)
    other = directory / "livingproof-scenario"
    other.mkdir()
    (other / "stock-seed-01.json").write_text('{"format": "livingproof-eva')
    assert main([str(manifest), str(directory), str(report)]) == 0
    assert "livingproof-scenario/stock-seed-01.json" in (report / PROBLEMS_FILE).read_text()


def test_collect_alone_gives_the_evaluator_in_each_problem(tmp_path):
    directory, _, report = _setup(tmp_path)
    _missing_thresholds(directory)
    counts: dict = {}
    collect(
        {"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}, directory, report, counts
    )
    assert len(counts["_problems"]) == 2
    assert all("evaluator livingproof-scenario" in p for p in counts["_problems"])


def test_the_docs_say_any_unreadable_result_is_left_off():
    text = (ROOT / "docs" / "locks.md").read_text()
    section = text.split("## Recordings in the scenario media job")[1].split("\n## ")[0]
    assert "any result file" in section
    assert "names the evaluator" in section
