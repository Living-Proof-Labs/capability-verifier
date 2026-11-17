# SPDX-License-Identifier: Apache-2.0
"""Reviewer coverage for run-level integrity collection (livingproof-evaluator-run#3, #141).

A failed integrity result changes nothing but the runs' conclusion and the
stored integrity_results: the robot's decisions and every other bundle field
stay byte-for-byte. A run-level result alone does not stand in for the
per-trial evaluator results, so a missing scenario upload still fails.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import test_collect_integrity as base

from capability_verifier.collect import collect, main


def _write(directory: Path, document: dict) -> None:
    dest = directory / "livingproof-run-integrity"
    dest.mkdir(parents=True)
    (dest / "result.json").write_text(json.dumps(document), encoding="utf-8")


def _strip_integrity(bundle: dict) -> dict:
    bundle = copy.deepcopy(bundle)
    assessment = bundle.get("assessment") or {}
    assessment.pop("integrity_results", None)
    for run in assessment.get("runs") or []:
        if isinstance(run, dict):
            run.pop("conclusion", None)
    return bundle


def test_failed_integrity_changes_only_the_run_conclusion(tmp_path):
    bundle, _ = base._bundle(tmp_path)
    assert (bundle.get("assessment") or {}).get("runs"), "fixture needs assessment runs"
    before = copy.deepcopy(bundle)
    results = tmp_path / "results"
    _write(results, base._integrity("livingproof-run-integrity", ok=False))
    after = collect(bundle, results)
    assert all(run.get("conclusion") == "INCONCLUSIVE" for run in after["assessment"]["runs"])
    assert _strip_integrity(after) == _strip_integrity(before), "robot fields changed"


def test_integrity_result_alone_does_not_count_as_trial_results(tmp_path):
    bundle, dest = base._bundle(tmp_path)
    manifest = dest / "manifest.json"
    original = manifest.read_bytes()
    assert any(isinstance(result, dict) for result in bundle["results"])
    results = tmp_path / "results"
    _write(results, base._integrity("livingproof-run-integrity", ok=True))
    code = main([str(manifest), str(results), str(tmp_path / "reports")])
    assert code == 1, "a run-level result hid the missing per-trial results"
    assert manifest.read_bytes() == original
