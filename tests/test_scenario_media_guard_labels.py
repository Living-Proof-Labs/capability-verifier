# SPDX-License-Identifier: Apache-2.0
"""Gaps left by the wording tests (Vera, capability-verifier#202 recheck at 5354d98).

Each stdout and problems-file line for a result left off names the file (also for unreadable JSON,
where the reason does not), the evaluator is "unknown" unless its id is a string, and a missing
version reads "(no version)".
"""

import json
import shutil
from pathlib import Path

from capability_verifier.collect import PROBLEMS_FILE, main

RESULTS = Path(__file__).parent / "fixtures" / "scenario-media-results"
MEDIA = "livingproof-scenario-media"


def _setup(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    manifest, report = tmp_path / "manifest.json", tmp_path / "report"
    report.mkdir()
    manifest.write_text(json.dumps({"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}))
    return directory, manifest, report


def _edit_evaluator(directory, name, change):
    path = directory / MEDIA / name
    document = json.loads(path.read_text())
    change(document["evaluator"])
    path.write_text(json.dumps(document))
    (directory / MEDIA / "media" / "thresholds.json").unlink()


def test_an_unreadable_result_is_named_by_file_on_stdout(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    (directory / MEDIA / "stock-seed-02.json").write_text("{")
    assert main([str(manifest), str(directory), str(report)]) == 0
    (line,) = [x for x in capsys.readouterr().out.splitlines() if "unusable result" in x]
    assert line.startswith("evaluator unknown: skipped (unusable result: ")
    assert f"{MEDIA}/stock-seed-02.json: not valid JSON" in line


def test_an_evaluator_whose_id_is_not_a_string_is_unknown(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    _edit_evaluator(directory, "stock-seed-01.json", lambda e: e.update(id=5))
    main([str(manifest), str(directory), str(report)])
    problems = (report / PROBLEMS_FILE).read_text().splitlines()
    (mine,) = [x for x in problems if "stock-seed-01.json" in x]
    assert "[evaluator unknown]" in mine
    out = capsys.readouterr().out
    assert any(
        x.startswith("evaluator unknown: skipped") and "stock-seed-01.json" in x
        for x in out.splitlines()
    )


def test_an_evaluator_without_a_version_reads_no_version(tmp_path, capsys):
    directory, manifest, report = _setup(tmp_path)
    _edit_evaluator(directory, "stock-seed-01.json", lambda e: e.pop("version"))
    main([str(manifest), str(directory), str(report)])
    problems = (report / PROBLEMS_FILE).read_text().splitlines()
    (mine,) = [x for x in problems if "stock-seed-01.json" in x]
    assert "[evaluator livingproof-scenario (no version)]" in mine
    assert "evaluator livingproof-scenario (no version): skipped (unusable result:" in (
        capsys.readouterr().out
    )
