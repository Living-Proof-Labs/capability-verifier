# SPDX-License-Identifier: Apache-2.0
"""A missing integrity result is not invented from a job result (#264).

An integrity result document is still stored and shown. A directory with no
such document does not store ``integrity_not_run``, and the collector command
ignores ``RUN_INTEGRITY_RESULT``.
"""

from __future__ import annotations

import json

from test_collect_cli import CITED, _layout
from test_collect_integrity import _bundle, _integrity, _robot_grades, _write

from capability_verifier.collect import collect, integrity_report_lines, main


def test_a_result_document_is_stored_and_not_called_not_run(tmp_path):
    bundle, _ = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-example-integrity-playground",
        _integrity("example-integrity", ok=True),
    )
    collect(bundle, downloads)
    assert "integrity_not_run" not in bundle["assessment"]
    assert integrity_report_lines(bundle) == ["Run Integrity: PASS"]
    assert _robot_grades(bundle) == grades


def test_without_a_result_document_nothing_is_stored(tmp_path):
    bundle, _ = _bundle(tmp_path)
    before = json.dumps(bundle, sort_keys=True)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    collect(bundle, downloads)
    assert json.dumps(bundle, sort_keys=True) == before
    assert integrity_report_lines(bundle) == []


def test_the_command_ignores_a_job_result_variable(tmp_path, monkeypatch):
    manifest, results, report, _original = _layout(tmp_path, CITED)
    monkeypatch.setenv("RUN_INTEGRITY_RESULT", "failure")
    assert main([str(manifest), str(results), str(report)]) == 0
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert "integrity_not_run" not in stored.get("assessment", {})
    assert not (report / "integrity-lines.txt").exists()
