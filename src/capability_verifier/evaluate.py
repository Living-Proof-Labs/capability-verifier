"""Grading interfaces: per-trial evaluation, per-trial policy and per-run aggregation.

The capability verifier owns every grading decision; a reporter renders what is written
here and never recomputes it. Three seams, each named and versioned so a bundle records
exactly which rule produced it:

* ``TrialEvaluator`` turns one observation into deterministic assessments.
* ``Policy.decide`` turns the retained assessments of one result into a status and reason.
* ``Aggregator.aggregate`` turns the decisions of one run into k of N, a Wilson interval
  and a grade.

Decisions and run assessments are computed from reporter-bundle results (the shape in
``bundle/manifest.json``), so they match what livingproof-capability-report v1.10.0
computes from the same bundle: the INCOMPATIBLE grade, the no-usable-plan fallback
(N is the recorded count and the grade INSUFFICIENT_EVIDENCE) and runs keyed by
specification commit, system, environment and run id.

Human re-grading (``assess --human``) is not implemented here. It plugs in without an
interface change: recorded human or agent conclusions are appended to a result's
``assessments`` (never generated here) and the same ``Policy.decide`` and ``Aggregator``
run again, producing a new bundle that names its parent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from . import hallway

PASS, FAIL, INSUFFICIENT = hallway.PASS, hallway.FAIL, hallway.INSUFFICIENT
INCOMPATIBLE = "INCOMPATIBLE"
STATUSES = (PASS, FAIL, INSUFFICIENT, INCOMPATIBLE)
FORMAT = "livingproof-run-assessment/0.1"
CAPABILITY = "walk-hallway"
SPECS = "https://github.com/Living-Proof-Labs/robot-capability-specs"
# Contracts the hallway policy grades, matched by repository, commit and revision; the same
# set livingproof-capability-report v1.10.0 accepts.
CONTRACTS = {
    (SPECS, "34681d20bf13ba0a836e03ac09869c7fb13665f1", "0.1.0"),  # v1.6.0
    (SPECS, "b4ba31eb2c7eaca2721d68d5d375260e2a114449", "0.1.0"),  # pre-merge candidate
    (SPECS, "a378e92216fd9498984f290e677b9f4e7a33232c", "0.1.0"),  # specs #25 candidate
    (SPECS, "0bdf1db3f975b044692c0b8f1ec37737ad8b889f", "0.1.0"),  # v1.6.1
}
EVALUATOR_VERSIONS = {name: hallway.VERSION for name in hallway.EVALUATORS}
FAILURE_KEYS = {"check", "observed", "limit", "unit", "t", "seed"}


@dataclass(frozen=True)
class Decision:
    result_id: str
    status: str
    reason: str
    failures: list = field(default_factory=list)
    assessor_types: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "result_id": self.result_id,
            "status": self.status,
            "reason": self.reason,
            "failures": [dict(f) for f in self.failures],
            "assessor_types": list(self.assessor_types),
        }


class TrialEvaluator(Protocol):
    """Deterministic assessments of one observation; never a human or agent conclusion."""

    id: str
    version: str

    def assess(self, observation: dict, thresholds: dict, evidence_id: str, seed=None) -> list: ...


class HallwayEvaluator:
    """The four walk-hallway evaluators, unchanged from ``hallway.EVALUATE``."""

    id = "walk-hallway"
    version = hallway.VERSION

    def assess(self, observation, thresholds, evidence_id, seed=None):
        if observation.get("format") != hallway.OBSERVATION_FORMAT:
            return [
                hallway.assessment(
                    n, INSUFFICIENT, f"unknown observation format{hallway._seed(seed)}", evidence_id
                )
                for n in hallway.EVALUATORS
            ]
        return [
            hallway.EVALUATE[n](observation, thresholds, evidence_id, seed)
            for n in hallway.EVALUATORS
        ]


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def structured_failures(assessment: dict) -> list:
    """Validated structured failures of one bundle assessment (empty when none are given)."""
    details = assessment["provenance"].get("details", {})
    items = details.get("failures", []) if isinstance(details, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"Invalid structured failures in {assessment['id']}")
    for f in items:
        ok = (
            isinstance(f, dict)
            and FAILURE_KEYS <= set(f) <= FAILURE_KEYS | {"wall"}
            and f["check"] == assessment["evaluator"]
            and (f["observed"] is None or _number(f["observed"]))
            and _number(f["limit"])
            and isinstance(f["unit"], str)
            and f["unit"]
            and (f["t"] is None or _number(f["t"]))
            and (f["seed"] is None or type(f["seed"]) is int)
            and ("wall" not in f or isinstance(f["wall"], str))
        )
        if not ok:
            raise ValueError(f"Invalid structured failure in {assessment['id']}")
    if items and assessment["status"] != FAIL:
        raise ValueError(f"Structured failures on a non-failing assessment {assessment['id']}")
    return [dict(f) for f in items]


def assessor_type(assessment: dict) -> str:
    declared = assessment["provenance"].get("assessor", {})
    kind = declared.get("type", assessment["kind"]) if isinstance(declared, dict) else None
    if kind != assessment["kind"]:
        raise ValueError(f"Assessor type does not match kind in {assessment['id']}")
    return kind


class Policy:
    """Base per-result policy. Subclasses set ``id``/``version`` and implement ``decide``."""

    id: str
    version: str

    @property
    def ref(self) -> dict:
        return {"id": self.id, "version": self.version}

    def decide(self, result: dict, complete: bool) -> Decision:
        raise NotImplementedError


class AllRequiredV1(Policy):
    """all-required-v1 for walk-hallway results, as the reporter applies it today.

    ``result`` is one reporter-bundle result. ``complete`` is whether every evidence
    file it names is present with a matching sha256. Retained human or agent assessments
    in ``result['assessments']`` are judged with the deterministic ones; none is ever
    replaced or averaged away.
    """

    id, version = hallway.POLICY["id"], hallway.POLICY["version"]

    def decide(self, result, complete):
        rid, assessments = result["id"], result["assessments"]
        compatibility = result["provenance"].get("compatibility")
        if "compatibility" in result["provenance"] and (
            not isinstance(compatibility, dict)
            or compatibility.get("status") != INCOMPATIBLE
            or compatibility.get("scope") != "execution"
            or not isinstance(compatibility.get("reason"), str)
            or not compatibility["reason"].strip()
        ):
            raise ValueError("Invalid execution compatibility declaration")
        if compatibility is not None:
            return Decision(rid, INCOMPATIBLE, compatibility["reason"])
        if result["spec"]["capability"] != CAPABILITY:
            return Decision(rid, INCOMPATIBLE, f"{self.id} grades {CAPABILITY} only")
        spec = result["spec"]
        if (spec["repository"], spec["commit"], spec["revision"]) not in CONTRACTS:
            return Decision(rid, INCOMPATIBLE, "Unsupported walk-hallway contract")
        if not self.members_supported(assessments):
            return Decision(
                rid, INCOMPATIBLE, "Unsupported hallway evaluator, version or assessor kind"
            )
        found = [f for a in assessments for f in structured_failures(a)]
        types = sorted({assessor_type(a) for a in assessments})
        failed = [a for a in assessments if a["status"] == FAIL]
        if failed:
            why = "; ".join(a["rationale"] for a in failed)
            return Decision(rid, FAIL, f"Failed: {why}", found, types)
        if not complete:
            return Decision(rid, INSUFFICIENT, "Evidence missing or digest mismatch", found, types)
        deterministic = [a["evaluator"] for a in assessments if a["kind"] == "deterministic"]
        if (
            sorted(deterministic) != sorted(EVALUATOR_VERSIONS)
            or not all(a["mandatory"] for a in assessments if a["kind"] == "deterministic")
            or any(a["status"] != PASS or not a["evidence_ids"] for a in assessments)
        ):
            return Decision(
                rid,
                INSUFFICIENT,
                "Missing, duplicate or insufficient required assessment",
                found,
                types,
            )
        return Decision(
            rid,
            PASS,
            "All four hallway evaluators and every retained assessment pass",
            found,
            types,
        )

    @staticmethod
    def members_supported(assessments) -> bool:
        for a in assessments:
            if a["kind"] not in hallway.ASSESSOR_TYPES:
                return False
            if (
                a["kind"] == "deterministic"
                and EVALUATOR_VERSIONS.get(a["evaluator"]) != a["version"]
            ):
                return False
            if a["kind"] != "deterministic" and a["evaluator"] in EVALUATOR_VERSIONS:
                return False  # a person or agent cannot stand in for a deterministic evaluator
        return True


def wilson(k, n):
    return hallway.wilson(k, n)


def run_key(result: dict) -> tuple:
    return (
        result["spec"]["commit"],
        result["system"]["id"],
        result["system"]["version"],
        result["environment"]["id"],
        result["environment"]["version"],
        result["provenance"].get("run_id"),
    )


class Aggregator:
    """k-of-n-wilson95: k PASS of planned N, needing ``required_passes``; Wilson 95%."""

    id, version = hallway.AGGREGATION["id"], hallway.AGGREGATION["version"]

    @property
    def ref(self) -> dict:
        return {"id": self.id, "version": self.version}

    def grade(self, statuses: list, plan: dict) -> tuple[str, int, str]:
        """(grade, planned N, rationale) for one run's per-result statuses."""
        n, need = plan.get("trials"), plan.get("required_passes")
        k = statuses.count(PASS)
        usable = type(n) is int and type(need) is int
        planned = n if type(n) is int and n > 0 else len(statuses)
        if INCOMPATIBLE in statuses:
            return INCOMPATIBLE, planned, f"{statuses.count(INCOMPATIBLE)} result(s) INCOMPATIBLE"
        if not usable:
            return INSUFFICIENT, planned, f"No usable trial plan; N is the {len(statuses)} recorded"
        if len(statuses) != n:
            return INSUFFICIENT, planned, f"{len(statuses)} of {n} planned trials recorded"
        if k >= need:
            return PASS, planned, f"{k}/{n} PASS, needs {need}"
        unsure = statuses.count(INSUFFICIENT)
        if k + unsure >= need:
            return INSUFFICIENT, planned, f"{k}/{n} PASS, needs {need}; {unsure} inconclusive"
        return FAIL, planned, f"{k}/{n} PASS, needs {need}"

    def aggregate(self, results: Iterable[dict], decisions: Iterable[Decision]) -> list[dict]:
        by_id = {d.result_id: d for d in decisions}
        groups: dict = {}
        for r in results:
            if r["spec"]["capability"] == CAPABILITY:
                groups.setdefault(run_key(r), []).append(r)
        runs = []
        for key, members in groups.items():
            statuses = [by_id[r["id"]].status for r in members]
            plan = members[0]["provenance"].get("plan") or {}
            grade, planned, rationale = self.grade(statuses, plan)
            k = statuses.count(PASS)
            lower, upper = wilson(k, planned)
            runs.append(
                {
                    "capability": CAPABILITY,
                    "spec": dict(members[0]["spec"]),
                    "system": dict(members[0]["system"]),
                    "environment": dict(members[0]["environment"]),
                    "run_id": key[-1],
                    "k": k,
                    "n": planned,
                    "required_passes": plan.get("required_passes"),
                    "interval": {"method": "wilson", "level": 0.95, "lower": lower, "upper": upper},
                    "grade": grade,
                    "rationale": rationale,
                    "result_ids": [r["id"] for r in members],
                    "failures": [dict(f) for r in members for f in by_id[r["id"]].failures],
                    "prototype": members[0]["provenance"].get("prototype"),
                }
            )
        return runs


POLICIES = {(AllRequiredV1.id, AllRequiredV1.version): AllRequiredV1}
AGGREGATORS = {(Aggregator.id, Aggregator.version): Aggregator}


def assess_bundle(
    results: list,
    complete: dict,
    policy: Policy | None = None,
    aggregator: Aggregator | None = None,
) -> dict:
    """The ``assessment`` block of bundle 1.1.0 (livingproof-run-assessment/0.1).

    ``complete`` maps result id to whether all of its evidence verified.
    """
    policy = policy or AllRequiredV1()
    aggregator = aggregator or Aggregator()
    decisions = [policy.decide(r, complete[r["id"]]) for r in results]
    return {
        "format": FORMAT,
        "policy": policy.ref,
        "aggregation": aggregator.ref,
        "decisions": [d.to_json() for d in decisions],
        "runs": aggregator.aggregate(results, decisions),
    }


def as_reporter_summary(run: dict) -> dict:
    """A run in the reporter's report.json ``summaries`` shape (``wilson95`` pair)."""
    out = {k: v for k, v in run.items() if k not in ("interval", "rationale")}
    out["wilson95"] = [run["interval"]["lower"], run["interval"]["upper"]]
    order = (
        "capability",
        "spec",
        "system",
        "environment",
        "run_id",
        "k",
        "n",
        "required_passes",
        "wilson95",
        "grade",
        "result_ids",
        "failures",
        "prototype",
    )
    return {k: out[k] for k in order}
