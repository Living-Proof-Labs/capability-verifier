# SPDX-License-Identifier: Apache-2.0
"""Loader and wrapper scenario verdicts on recorded G1 evidence.

The loader installs the released scenario descriptor,
``Living-Proof-Labs/livingproof-evaluator-scenario@v1.6.0/livingproof-evaluator.json``
(commit 5eb435ac42d121708e668ceb049abfd2519b42ab). The implementation contract
is livingproof-evaluator v1.11.0.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "tests" / "fixtures" / "lpv-run-43402628666" / "unitree-g1-stock" / "evidence"
LINE = "Living-Proof-Labs/livingproof-evaluator-scenario@v1.6.0/livingproof-evaluator.json"
STAMP_KEYS = {"version", "timestamps", "started_at", "completed_at"}


def _without_stamps(value):
    if isinstance(value, dict):
        return {key: _without_stamps(item) for key, item in value.items() if key not in STAMP_KEYS}
    if isinstance(value, list):
        return [_without_stamps(item) for item in value]
    return value


def _verdicts(directory: Path) -> list[dict]:
    """Per-trial scenario verdicts and the summary text of each step."""
    rows = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.rglob("*.json")):
        if path.name == "thresholds.json" or not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        if not str(document.get("format", "")).startswith("livingproof-evaluator-result/"):
            continue
        scenario = document.get("scenario")
        if not isinstance(scenario, dict):
            continue
        rows.append(
            _without_stamps(
                {
                    "trial_id": scenario.get("trial_id"),
                    "steps": [
                        {
                            "id": step.get("id"),
                            "verdict": step.get("verdict"),
                            "summary": step.get("summary"),
                            "reason": step.get("reason"),
                        }
                        for step in scenario.get("steps") or []
                    ],
                }
            )
        )
    return rows


def _grade(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.18.5")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_g1", ROOT / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    evidence = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, evidence)
    tool.export(evidence / "manifest.json", tmp_path / "verified", bundle_version="1.1.0")
    bundle = json.loads((tmp_path / "verified" / "bundle" / "manifest.json").read_text())
    grades = [run["grade"] for run in bundle["assessment"]["runs"]]
    assert grades == ["FAIL"]
    return bundle


def _wrapper(manifest: Path, out: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    done = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "run_scenario_evaluator.py"),
            str(manifest),
            str(out),
            "2026-01-01T00:00:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr or done.stdout


def _wrapper_media(manifest: Path, media: Path, out: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    done = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "run_scenario_media.py"),
            str(manifest),
            str(media),
            str(out),
            "--identity",
            "git+https://github.com/Living-Proof-Labs/livingproof-evaluator-scenario@v1.6.0",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr or done.stdout


def test_the_loader_matches_the_wrapper_on_recorded_g1_evidence(tmp_path, monkeypatch):
    """G1 still grades FAIL, and the loader's scenario verdicts match the wrapper."""
    from capability_verifier.collect import collect
    from capability_verifier.evaluators import run_lines

    bundle = _grade(tmp_path, monkeypatch)
    manifest = tmp_path / "evidence" / "manifest.json"
    wrapper = tmp_path / "wrapper"
    _wrapper(manifest, wrapper)
    wrapper_verdicts = _verdicts(wrapper)
    assert wrapper_verdicts, "the wrapper wrote no scenario verdicts"
    assert any(step["verdict"] == "FAIL" for row in wrapper_verdicts for step in row["steps"])

    loader = tmp_path / "loader"
    records = run_lines(
        LINE + "\n",
        evidence=manifest,
        out=loader,
        phase="trial",
        kind="scenario",
        work=tmp_path / "loader-work",
    )
    assert records[0].outcome == "result", records[0].note
    loader_verdicts = _verdicts(loader)
    assert loader_verdicts == wrapper_verdicts

    media = tmp_path / "media"
    media.mkdir()
    wrapper_media = tmp_path / "wrapper-media"
    loader_media = tmp_path / "loader-media"
    _wrapper_media(manifest, media, wrapper_media)
    media_records = run_lines(
        LINE + "\n",
        evidence=manifest,
        out=loader_media,
        media_dir=media,
        phase="media",
        kind="scenario",
        work=tmp_path / "loader-media-work",
    )
    assert media_records[0].outcome == "skipped"
    assert _verdicts(wrapper_media) == _verdicts(loader_media) == []

    collect(bundle, loader)
    grades = [run["grade"] for run in bundle["assessment"]["runs"]]
    assert grades == ["FAIL"]
    assert bundle["results"][0].get("evaluator_results"), "scenario results were not placed"
