# SPDX-License-Identifier: Apache-2.0
"""Boundaries of the collector's guard for the scenario media job's results (verification-written).

Only a result whose cited ``media/`` thresholds file is missing, or a result file that is not valid
JSON, is left off the report (named on stderr and in evaluator-result-problems.txt). Every other
citation problem still fails, and nothing usable at all is still an error.
"""

import json
import shutil
from pathlib import Path

import pytest

from capability_verifier.collect import PROBLEMS_FILE, collect, main

RESULTS = Path(__file__).parent / "fixtures" / "scenario-media-results"
MEDIA = "livingproof-scenario-media"


def _setup(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    manifest, report = tmp_path / "manifest.json", tmp_path / "report"
    report.mkdir()
    manifest.write_text(json.dumps({"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}))
    return directory, manifest, report


def _run(manifest, directory, report):
    return main([str(manifest), str(directory), str(report)])


def _recite(path, uri):
    document = json.loads(path.read_text())
    for item in document["inputs"]:
        if item.get("role") == "thresholds":
            item["uri"] = uri
    path.write_text(json.dumps(document))


def test_invalid_json_that_is_not_truncated_is_listed_not_fatal(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_text("this is not json\n")
    assert _run(manifest, directory, report) == 0
    assert "stock-seed-02.json" in capsys.readouterr().err
    stored = json.loads(manifest.read_text())["results"]
    assert len(stored[0]["evaluator_results"]) == 1 and "evaluator_results" not in stored[1]


def test_a_result_file_that_is_not_utf8_is_listed_not_fatal(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_bytes(b"\xff\xfe\x00{")
    assert _run(manifest, directory, report) == 0
    assert "stock-seed-02.json" in (report / PROBLEMS_FILE).read_text()


def test_each_skipped_result_is_named_once_and_the_good_one_is_not(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_text("{")
    assert _run(manifest, directory, report) == 0
    lines = (report / PROBLEMS_FILE).read_text().splitlines()
    assert len(lines) == 1 and "stock-seed-02.json" in lines[0]
    assert "stock-seed-01" not in lines[0]


def test_a_missing_citation_outside_media_still_fails(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    _recite(directory / MEDIA / "stock-seed-02.json", "other/thresholds.json")
    assert _run(manifest, directory, report) == 1
    assert "missing" in capsys.readouterr().err
    assert not (report / PROBLEMS_FILE).exists()


def test_a_missing_citation_at_a_look_alike_media_prefix_still_fails(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    _recite(directory / MEDIA / "stock-seed-02.json", "media2/thresholds.json")
    assert (
        _run(manifest, directory, report) == 1
    )  # seed-01 is collected, so only the citation fails


def test_an_altered_media_thresholds_file_still_fails_through_main(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "media" / "thresholds.json").write_text("{}\n")
    assert _run(manifest, directory, report) == 1
    assert "altered" in capsys.readouterr().err
    assert not (report / PROBLEMS_FILE).exists()


def test_a_media_citation_that_escapes_the_results_still_fails(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    _recite(directory / MEDIA / "stock-seed-02.json", "media/../../thresholds.json")
    assert _run(manifest, directory, report) == 1


def test_a_media_thresholds_symlink_still_fails(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    target = directory / MEDIA / "media" / "thresholds.json"
    real = directory / "elsewhere.txt"
    real.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(real)
    assert _run(manifest, directory, report) == 1


def test_every_result_unusable_and_nothing_else_collected_exits_1(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "media" / "thresholds.json").unlink()
    assert _run(manifest, directory, report) == 1
    err = capsys.readouterr().err
    assert "no evaluator result was appended" in err
    assert "media/thresholds.json" in err  # the reason is still named
    assert json.loads(manifest.read_text()) == {
        "results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]
    }


def test_all_results_truncated_exits_1_and_leaves_the_manifest_unchanged(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    for name in ("stock-seed-01.json", "stock-seed-02.json"):
        (directory / MEDIA / name).write_text("{")
    before = manifest.read_text()
    assert _run(manifest, directory, report) == 1
    assert manifest.read_text() == before


def test_unusable_media_results_do_not_hide_another_evaluators_good_results(tmp_path):
    directory, manifest, report = _setup(tmp_path)
    other = directory / "another-evaluator"
    shutil.copytree(directory / MEDIA, other)
    (directory / MEDIA / "media" / "thresholds.json").unlink()
    assert _run(manifest, directory, report) == 0
    stored = json.loads(manifest.read_text())["results"]
    assert all(len(r["evaluator_results"]) == 1 for r in stored)
    assert "media/thresholds.json" in (report / PROBLEMS_FILE).read_text()


def test_the_problems_file_is_written_when_the_report_directory_does_not_exist_yet(tmp_path):
    # The workflow runs the collector before it creates reports/ (mkdir -p reports follows).
    directory, manifest, report = _setup(tmp_path)
    report.rmdir()
    (directory / MEDIA / "stock-seed-02.json").write_text("{")
    assert _run(manifest, directory, report) == 0
    assert "stock-seed-02.json" in (report / PROBLEMS_FILE).read_text()


def test_collect_alone_reports_problems_through_counts(tmp_path):
    directory, _, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_text("{")
    counts: dict = {}
    collect(
        {"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}, directory, report, counts
    )
    assert len(counts["_problems"]) == 1


@pytest.mark.parametrize("name", ["stock-seed-01.json", "stock-seed-02.json"])
def test_one_unusable_result_leaves_exactly_the_other_collected(tmp_path, name):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / name).write_text("[")
    assert _run(manifest, directory, report) == 0
    stored = {r["id"]: r for r in json.loads(manifest.read_text())["results"]}
    kept = [k for k, v in stored.items() if v.get("evaluator_results")]
    assert kept == [
        n.removesuffix(".json") for n in ("stock-seed-01.json", "stock-seed-02.json") if n != name
    ]


def test_a_result_left_off_the_report_is_not_counted_as_appended(tmp_path):
    directory, _, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_text("{")
    (directory / MEDIA / "media" / "thresholds.json").unlink()
    shutil.copy(RESULTS / MEDIA / "stock-seed-01.json", directory / MEDIA / "stock-seed-01.json")
    counts: dict = {}
    collect(
        {"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}, directory, report, counts
    )
    assert counts["livingproof-scenario"]["appended"] == 0
