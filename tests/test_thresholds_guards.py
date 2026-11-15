# SPDX-License-Identifier: Apache-2.0
"""Guards around cited thresholds that the #96 hand checks covered.

A result may not cite a path other than a relative thresholds.json, and two
results may not export the same relative path with different bytes. The report
job also records every copied thresholds.json in SHA256SUMS. Proposed by Vera in
the capability-verifier#96 review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from capability_verifier.collect import ThresholdCitationError, collect

ROOT = Path(__file__).parents[1]

CITED = b'{"limits":{"exit_segment":0.75}}\n'
OTHER = b'{"limits":{"exit_segment":0.1}}\n'
SHA256SUMS = (
    "(cd reports && find . -type f -name thresholds.json -print | sed 's#^\\./##' "
    '| LC_ALL=C sort | while IFS= read -r file; do sha256sum "$file" >> SHA256SUMS; done)'
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collect(tmp_path: Path, results: Path):
    bundle = {"results": [{"id": "playground-seed-01"}, {"id": "playground-seed-02"}]}
    collect(bundle, results, tmp_path / "reports")
    return bundle, tmp_path / "reports"


def _write(directory: Path, trial_id: str, uri: str, data: bytes | None, digest: str | None = None):
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "format": "livingproof-evaluator-result/1.1.0",
        "inputs": [
            {
                "role": "thresholds",
                "uri": uri,
                "sha256": digest or _digest(data or CITED),
            }
        ],
        "scenario": {"trial_id": trial_id, "steps": []},
    }
    (directory / f"{trial_id}.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    if data is not None:
        dest = directory / "thresholds.json"
        dest.write_bytes(data)


@pytest.mark.parametrize(
    "uri",
    [
        "../thresholds.json",
        "/tmp/thresholds.json",
        "https://example.com/thresholds.json",
        "limits.json",
    ],
)
def test_a_citation_that_is_not_a_relative_thresholds_file_is_substituted(tmp_path, uri):
    results = tmp_path / "evaluator-results"
    directory = results / "livingproof-scenario"
    _write(directory, "playground-seed-01", uri, CITED)
    (results / "thresholds.json").write_bytes(CITED)
    with pytest.raises(ThresholdCitationError, match="substituted"):
        _collect(tmp_path, results)
    assert not (tmp_path / "reports" / "thresholds.json").exists()


def test_two_results_cannot_export_the_same_path_with_different_bytes(tmp_path):
    results = tmp_path / "evaluator-results"
    _write(results / "playground", "playground-seed-01", "thresholds.json", CITED)
    _write(results / "stock", "playground-seed-02", "thresholds.json", OTHER)
    with pytest.raises(ThresholdCitationError, match="substituted"):
        _collect(tmp_path, results)
    assert not (tmp_path / "reports" / "thresholds.json").exists()


def test_the_report_job_adds_thresholds_to_sha256sums():
    text = (ROOT / ".github/workflows/scenario.yml").read_text(encoding="utf-8")
    assert SHA256SUMS in text
    assert "path: reports/" in text
