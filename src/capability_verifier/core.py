# SPDX-License-Identifier: Apache-2.0
"""Small, explicit runner -> evaluators -> policy demonstration."""

import hashlib
import json
import math
from pathlib import Path

PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT_EVIDENCE"
MEMBERS = {"pause-rules", "mock-ai", "human-fixture"}


def collect(path: Path) -> dict:
    """Runner collects evidence and its digest; it makes no assessment."""
    raw = path.read_bytes()
    evidence = json.loads(raw)
    if not isinstance(evidence, dict):
        raise ValueError("Evidence must be an object")
    return {
        "evidence": evidence,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "runner": {"id": "fixture-runner", "version": "1.0.0"},
    }


def assessment(name, status, rationale, mandatory=False):
    return {
        "evaluator": name,
        "version": "1.0.0",
        "status": status,
        "rationale": rationale,
        "mandatory": mandatory,
    }


def evaluate_pause(evidence):
    # A known mandatory failure wins even if other observations are missing.
    if evidence.get("motion_inhibited") is False:
        return assessment("pause-rules", FAIL, "Mandatory motion inhibition failed", True)
    elapsed = evidence.get("elapsed_ms")
    valid = (
        type(elapsed) in (int, float)
        and math.isfinite(elapsed)
        and elapsed >= 0
        and type(evidence.get("motion_inhibited")) is bool
        and isinstance(evidence.get("requested"), str)
        and isinstance(evidence.get("observed"), str)
    )
    if not valid:
        return assessment("pause-rules", INSUFFICIENT, "Missing or malformed observations", True)
    passed = evidence["requested"] == evidence["observed"] == "paused" and elapsed <= 500
    return assessment(
        "pause-rules", PASS if passed else FAIL, "Pause state and 500 ms limit assessed", True
    )


def evaluate_recorded(evidence, name):
    record = evidence.get("assessments", {}).get(name, {})
    status = record.get("status")
    reason = record.get("rationale")
    if status not in {PASS, FAIL, INSUFFICIENT} or not isinstance(reason, str) or not reason:
        return assessment(name, INSUFFICIENT, "Recorded assessment unavailable or invalid")
    label = "MOCK AI; no model was called" if name == "mock-ai" else "HUMAN FIXTURE; no live review"
    return assessment(name, status, f"{label}: {reason}")


def grade(assessments):
    """Conservative all-required policy: never average away a failure."""
    if any(a.get("status") == FAIL for a in assessments):
        return FAIL
    names = [a.get("evaluator") for a in assessments]
    if len(names) != len(MEMBERS) or set(names) != MEMBERS:
        return INSUFFICIENT
    if any(a.get("status") != PASS for a in assessments):
        return INSUFFICIENT
    return PASS


def verify(path, specs, omit_human=False):
    collected = collect(path)
    evidence = collected["evidence"]
    assessments = [evaluate_pause(evidence), evaluate_recorded(evidence, "mock-ai")]
    if not omit_human:
        assessments.append(evaluate_recorded(evidence, "human-fixture"))
    return {
        "status": grade(assessments),
        "assessments": assessments,
        "provenance": {
            "spec": {**specs, "capability": "pause", "revision": "1.0.0"},
            "system": {"id": "pause-fixtures", "version": "1.0.0", "fixture": path.name},
            "simulator": None,
            "runner": collected["runner"],
            "policy": {"id": "all-required-v1", "version": "1.0.0", "members": sorted(MEMBERS)},
            "evidence_sha256": collected["sha256"],
        },
    }
