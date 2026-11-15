# SPDX-License-Identifier: Apache-2.0
"""The report keeps each cited thresholds.json, or collection fails closed.

A result cites the file by URI and sha256. The bytes in the report are that
file. A missing file, a file whose bytes differ, and a different path all
fail. These checks fail while collect only appends the result document.
"""

import hashlib
import json
from pathlib import Path

import pytest

from capability_verifier.collect import collect

ROOT = Path(__file__).parents[1]
TRIAL = "playground-seed-01"
CITED = b'{"limits":{"exit_segment":0.75},"parameters":{"sha256":"ab"}}\n'
OTHER = b'{"limits":{"exit_segment":0.1},"parameters":{"sha256":"ff"}}\n'


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document(trial_id: str, *, uri: str, digest: str) -> dict:
    return {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": "livingproof-scenario", "version": "1.2.1"},
        "inputs": [
            {"role": "observation", "uri": "trials/trial-01.json", "sha256": "cd" * 32},
            {"role": "thresholds", "uri": uri, "sha256": digest},
        ],
        "scenario": {"trial_id": trial_id, "steps": []},
    }


def _write_result(directory: Path, document: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{document['scenario']['trial_id']}.json"
    (directory / name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _collect(tmp_path: Path, results: Path):
    report = tmp_path / "reports"
    bundle = {"results": [{"id": TRIAL}, {"id": "playground-seed-02"}]}
    collect(bundle, results, report)
    return bundle, report


def test_a_valid_thresholds_file_is_copied_into_the_report(tmp_path):
    """The cited bytes land in the report, and the input references stay."""
    digest = _digest(CITED)
    document = _document(TRIAL, uri="thresholds.json", digest=digest)
    original = json.loads(json.dumps(document["inputs"]))
    results = tmp_path / "evaluator-results"
    directory = results / "livingproof-scenario"
    _write_result(directory, document)
    (directory / "thresholds.json").write_bytes(CITED)
    plain = {
        "format": "livingproof-evaluator-result/1.1.0",
        "inputs": [{"role": "observation", "uri": "trials/trial-02.json", "sha256": "ab" * 32}],
        "scenario": {"trial_id": "playground-seed-02", "steps": []},
    }
    _write_result(directory, plain)
    bundle, report = _collect(tmp_path, results)
    copied = (report / "thresholds.json").read_bytes()
    assert copied == CITED
    assert _digest(copied) == digest
    stored = bundle["results"][0]["evaluator_results"][0]["inputs"]
    assert stored == original
    assert stored[0] == {"role": "observation", "uri": "trials/trial-01.json", "sha256": "cd" * 32}
    assert stored[1] == {"role": "thresholds", "uri": "thresholds.json", "sha256": digest}
    assert bundle["results"][1]["evaluator_results"][0]["inputs"] == plain["inputs"]


def test_missing_thresholds_fail(tmp_path):
    """The cited path is absent. A same-named file elsewhere is not a substitute."""
    document = _document(TRIAL, uri="thresholds.json", digest=_digest(CITED))
    results = tmp_path / "evaluator-results"
    _write_result(results / "livingproof-scenario", document)
    decoy = results / "other-configuration" / "thresholds.json"
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(CITED)
    with pytest.raises(Exception) as raised:
        _collect(tmp_path, results)
    assert "missing" in str(raised.value)
    assert not (tmp_path / "reports" / "thresholds.json").exists()


def test_altered_thresholds_fail(tmp_path):
    """The cited path exists, and its bytes are not the cited digest."""
    document = _document(TRIAL, uri="thresholds.json", digest=_digest(CITED))
    directory = tmp_path / "evaluator-results" / "livingproof-scenario"
    _write_result(directory, document)
    altered = CITED.replace(b"0.75", b"9.99")
    assert altered != CITED
    (directory / "thresholds.json").write_bytes(altered)
    with pytest.raises(Exception) as raised:
        _collect(tmp_path, tmp_path / "evaluator-results")
    message = str(raised.value)
    assert "altered" in message
    assert "sha256" in message
    assert not (tmp_path / "reports" / "thresholds.json").exists()


def test_substituted_thresholds_fail(tmp_path):
    """A different path is refused even when its bytes match the citation.

    The result cites thresholds.json. That path is a symlink to another
    configuration's file, and the file's bytes are the cited digest. Accepting
    it would publish a different path than the one the result names.
    """
    document = _document(TRIAL, uri="thresholds.json", digest=_digest(CITED))
    results = tmp_path / "evaluator-results"
    directory = results / "playground"
    _write_result(directory, document)
    other = results / "stock" / "thresholds.json"
    other.parent.mkdir(parents=True)
    other.write_bytes(CITED)
    (directory / "thresholds.json").symlink_to(other)
    with pytest.raises(Exception) as raised:
        _collect(tmp_path, results)
    assert "substituted" in str(raised.value)
    assert not (tmp_path / "reports" / "thresholds.json").exists()


def test_the_report_job_copies_thresholds_into_the_uploaded_directory():
    text = (ROOT / ".github/workflows/scenario.yml").read_text(encoding="utf-8")
    command = (
        "python3 /tmp/collect_evaluator_results.py "
        "verified/bundle/manifest.json evaluator-results reports"
    )
    assert command in text
    assert "path: reports/" in text
