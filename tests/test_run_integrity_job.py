# SPDX-License-Identifier: Apache-2.0
"""The run-integrity job's command, at the v1.0.0 tag, actually runs and passes."""

import json
import os
import subprocess
from pathlib import Path

import pytest

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
REPO = "https://github.com/Living-Proof-Labs/livingproof-evaluator-run.git"
TAG = "v1.0.0"


def test_run_integrity_v1_0_0_evaluates_stock_evidence_and_passes(tmp_path):
    """Same command the scenario.yml run-integrity job uses, at the tagged release."""
    checkout = tmp_path / "run-integrity"
    out = tmp_path / "result.json"
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
    done = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "--directory",
            str(checkout),
            "python",
            "-m",
            "livingproof_evaluator_run",
            str(EVIDENCE),
            "--out",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(checkout / "src")},
    )
    assert done.returncode == 0, done.stderr or done.stdout
    result = json.loads(out.read_text())
    assert result["format"] == "livingproof-evaluator-result/1.1.0"
    assert result["evaluator"]["id"] == "livingproof-run-integrity"
    assert result["evaluator"]["version"] == "1.0.0"
    assert result["evaluator"]["kind"] == "integrity"
    run = result["integrity"]["run"]
    assert run["test_system_status"] == "COMPLETED"
    assert run["success"] is True
    assert run["percentage"] == 100.0
