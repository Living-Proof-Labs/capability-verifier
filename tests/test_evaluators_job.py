# SPDX-License-Identifier: Apache-2.0
"""The loader runs a pinned evaluator on recorded evidence.

The loader fetches the released descriptor. Install, execution, and the
schema pin use the network, as ``test_run_integrity_job`` does.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from capability_verifier.evaluators import run_lines

ROOT = Path(__file__).parents[1]
EVIDENCE = (
    ROOT
    / "tests"
    / "fixtures"
    / "lpv-run-43402628666"
    / "unitree-g1-stock"
    / "evidence"
    / "manifest.json"
)
MEDIA = ROOT / "tests" / "fixtures" / "run-integrity-media"
LINE = "Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json"
REPO = "https://github.com/Living-Proof-Labs/livingproof-evaluator-run.git"
TAG = "v1.9.0"


def _without_timestamps(value):
    if isinstance(value, dict):
        return {
            key: _without_timestamps(item)
            for key, item in value.items()
            if key not in {"timestamps", "started_at", "completed_at", "requested_at"}
        }
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _view(document: dict) -> dict:
    integrity = document["integrity"]
    findings = []
    for check in integrity["checks"]:
        findings.extend(check.get("findings") or [])
    return {
        "status": integrity["run"]["test_system_status"],
        "percentage": integrity["run"].get("percentage"),
        "findings": _without_timestamps(findings),
    }


def _hardcoded(tmp_path: Path, media: Path | None) -> dict:
    """Same command the scenario.yml run-integrity job uses, at the tagged release."""
    checkout = tmp_path / "checkout"
    out = tmp_path / "hardcoded.json"
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", TAG, REPO, str(checkout)],
        check=False,
        capture_output=True,
        text=True,
    )
    if clone.returncode != 0:
        pytest.fail(f"could not clone {REPO}@{TAG}: {clone.stderr}")
    sync = subprocess.run(
        ["uv", "sync", "--locked", "--directory", str(checkout)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert sync.returncode == 0, sync.stderr
    command = [
        "uv",
        "run",
        "--no-sync",
        "--directory",
        str(checkout),
        "python",
        "-m",
        "livingproof_evaluator_run",
        str(EVIDENCE),
    ]
    if media is not None:
        command.extend(["--media", str(media)])
    command.extend(["--out", str(out)])
    done = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(checkout / "src")},
    )
    assert done.returncode == 0, done.stderr or done.stdout
    return json.loads(out.read_text())


@pytest.mark.parametrize("media", [None, MEDIA], ids=["without-media", "with-media"])
def test_loader_matches_the_hardcoded_job(tmp_path, media):
    out = tmp_path / "loader"
    records = run_lines(
        LINE + "\n",
        evidence=EVIDENCE,
        out=out,
        media_dir=media,
    )
    assert records[0].outcome == "result", records[0].note
    produced = json.loads((out / records[0].evaluator_id / "result.json").read_text())
    assert _view(produced) == _view(_hardcoded(tmp_path, media))
