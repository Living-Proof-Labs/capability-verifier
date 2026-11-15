# SPDX-License-Identifier: Apache-2.0
"""The collector CLI fails closed: it does not rewrite the manifest or copy
thresholds.json when the citation is missing or altered. It needs a report
directory. Proposed by Vera in the capability-verifier#96 review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from capability_verifier.collect import main

CITED = b'{"limits":{"exit_segment":0.75}}\n'
ALTERED = b'{"limits":{"exit_segment":9.99}}\n'
TRIAL = "playground-seed-01"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _layout(
    tmp_path: Path, data: bytes | None, *, digest: str | None = None
) -> tuple[Path, Path, Path, str]:
    manifest = tmp_path / "manifest.json"
    original = json.dumps({"results": [{"id": TRIAL}]}, indent=2) + "\n"
    manifest.write_text(original, encoding="utf-8")
    results = tmp_path / "evaluator-results" / "livingproof-scenario"
    results.mkdir(parents=True)
    cited = digest or _digest(CITED)
    document = {
        "format": "livingproof-evaluator-result/1.1.0",
        "inputs": [{"role": "thresholds", "uri": "thresholds.json", "sha256": cited}],
        "scenario": {"trial_id": TRIAL, "steps": []},
    }
    (results / f"{TRIAL}.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    if data is not None:
        (results / "thresholds.json").write_bytes(data)
    report = tmp_path / "reports"
    report.mkdir()
    return manifest, tmp_path / "evaluator-results", report, original


def test_usage_requires_the_report_directory():
    assert main([]) == 2
    assert main(["manifest.json", "evaluator-results"]) == 2


def test_altered_thresholds_leave_the_manifest_and_report_untouched(tmp_path, capsys):
    manifest, results, report, original = _layout(tmp_path, ALTERED)
    assert main([str(manifest), str(results), str(report)]) == 1
    captured = capsys.readouterr()
    assert "altered" in captured.err
    assert manifest.read_text(encoding="utf-8") == original
    assert not (report / "thresholds.json").exists()


def test_missing_thresholds_leave_the_manifest_and_report_untouched(tmp_path, capsys):
    manifest, results, report, original = _layout(tmp_path, None)
    assert main([str(manifest), str(results), str(report)]) == 1
    captured = capsys.readouterr()
    assert "missing" in captured.err
    assert manifest.read_text(encoding="utf-8") == original
    assert not (report / "thresholds.json").exists()


def test_a_valid_citation_is_written_to_the_report_and_the_manifest(tmp_path):
    manifest, results, report, _original = _layout(tmp_path, CITED)
    assert main([str(manifest), str(results), str(report)]) == 0
    assert (report / "thresholds.json").read_bytes() == CITED
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert stored["results"][0]["evaluator_results"][0]["inputs"][0]["sha256"] == _digest(CITED)
