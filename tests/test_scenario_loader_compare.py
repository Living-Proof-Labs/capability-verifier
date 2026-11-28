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


def _documents(directory: Path) -> dict[str, dict]:
    """Result documents keyed by trial id, without timestamps or version stamps."""
    found: dict[str, dict] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.rglob("*.json")):
        if path.name == "thresholds.json" or not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        if not str(document.get("format", "")).startswith("livingproof-evaluator-result/"):
            continue
        scenario = document.get("scenario")
        trial_id = scenario.get("trial_id") if isinstance(scenario, dict) else None
        if not isinstance(trial_id, str) or not trial_id:
            continue
        found[trial_id] = _without_stamps(document)
    return found


def _placed(bundle: dict) -> list:
    return [
        _without_stamps(result.get("evaluator_results"))
        for result in bundle["results"]
        if isinstance(result, dict)
    ]


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
    wrapper_docs = _documents(wrapper)
    assert wrapper_docs, "the wrapper wrote no scenario results"

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
    loader_docs = _documents(loader)
    assert set(loader_docs) == set(wrapper_docs)
    assert loader_docs == wrapper_docs

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
    assert _documents(wrapper_media) == _documents(loader_media) == {}

    wrapper_bundle = json.loads(json.dumps(bundle))
    loader_bundle = json.loads(json.dumps(bundle))
    collect(wrapper_bundle, wrapper)
    collect(loader_bundle, loader)
    placed = _placed(loader_bundle)
    assert placed == _placed(wrapper_bundle)
    assert any(entry for entry in placed)
    assert [run["grade"] for run in loader_bundle["assessment"]["runs"]] == ["FAIL"]
