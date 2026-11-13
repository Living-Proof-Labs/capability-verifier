# SPDX-License-Identifier: Apache-2.0
"""Walk-hallway policy, thresholds and bundle export.

The four step checks are ``livingproof_evaluator_scenario.checks``. ``EVALUATE``
delegates to that package. This module still resolves thresholds, applies
all-required-v1, and writes the bundle assessments. The scenario result
documents are written by the scenario-evaluator job, not here.

Inputs are evidence only: an evidence manifest written by the simulator (it echoes the
robot repository's verification lock, including the trial plan) and one observation
file per trial. Nothing here hard-codes a trial count or speed. Evidence 1.0.0 carries
those in ``plan.trials``, ``plan.seeds`` and ``plan.required_passes``. Evidence 2.0.0
carries ``plan.trials.count`` identical nominal trials and no seeds or required passes.
Thresholds are resolved from the robot profile, the configuration profile and the
world quantities, as the specification's acceptance table defines.

Every assessment records its assessor. These evaluators are ``deterministic``.
Assessments by a ``human`` or an ``agent`` arrive through the same recorded-input
path (``recorded=``) and are retained and judged by the same policy; the plan can make
them required with ``required_assessors``. None are produced here.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from livingproof_evaluator_scenario.checks import (
    EVALUATE,
    _seed,
    assessment,
    exit_reached,
    failure,
    fall,
    lateral_limit,
    wall_contact,
)

# Re-exported so existing callers keep `hallway.exit_reached` and the other checks.
__all__ = [
    "EVALUATE",
    "assessment",
    "exit_reached",
    "failure",
    "fall",
    "lateral_limit",
    "wall_contact",
]

PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT_EVIDENCE"


def _num(v):
    return type(v) in (int, float) and math.isfinite(v)


# A run whose manifest declares an unmet required precondition: never graded PASS/FAIL.
UNSUPPORTED = "UNSUPPORTED"
ASSESSOR_TYPES = {"deterministic", "human", "agent"}
# Reporter bundle 1.0.0 'kind' for each assessor type; agents have no reporter kind yet.
REPORTER_KIND = {"deterministic": "deterministic", "human": "human", "agent": None}
VERSION = "1.0.0"
POLICY = {"id": "all-required-v1", "version": "1.0.0"}
AGGREGATION = {"id": "k-of-n-wilson95", "version": "1.0.0"}
EVALUATORS = (
    "hallway-exit-reached",
    "hallway-lateral-limit",
    "hallway-wall-contact",
    "hallway-fall",
)
# walk-hallway README: preconditions the specification requires, and wall clearance per class.
SPEC_PRECONDITIONS = (
    "fits_width",
    "configuration_offered",
    "velocity_interface",
    "simulator_declared",
)
WALL_CLEARANCE = {"humanoid": 0.05, "quadruped": 0.10}
# The newest runner observation format, and every format the evaluators read. 2.0.0
# (livingproof-sim-mujoco, capability-verifier#37) adds the left_hallway end, measures a
# contact's depth as intrusion past the wall's inner face and ends crossed_exit only within
# the exit segment. Any other format is refused: every evaluator reports INSUFFICIENT.
OBSERVATION_FORMAT = "livingproof-hallway-observation/2.0.0"
OBSERVATION_FORMATS = frozenset({"livingproof-hallway-observation/1.0.0", OBSERVATION_FORMAT})
# Trial ends each format may report; anything else is malformed evidence.
OBSERVATION_ENDS = {
    "livingproof-hallway-observation/1.0.0": {"crossed_exit", "time_limit", "non_finite_state"},
    OBSERVATION_FORMAT: {"crossed_exit", "time_limit", "non_finite_state", "left_hallway"},
}
# 1.0.0 stays accepted. Robots pinned to livingproof-sim-mujoco v1.8.3 still record it,
# and they keep verifying if they update this verifier before they repin the runner.
MANIFEST_FORMAT = "livingproof-hallway-evidence/1.0.0"
EVIDENCE_V1 = MANIFEST_FORMAT
EVIDENCE_V2 = "livingproof-hallway-evidence/2.0.0"
MANIFEST_FORMATS = frozenset({EVIDENCE_V1, EVIDENCE_V2})
MEDIA_V1 = "livingproof-hallway-media/1.0.0"
MEDIA_V2 = "livingproof-hallway-media/2.0.0"
MEDIA_FORMATS = frozenset({MEDIA_V1, MEDIA_V2})


class ManifestError(ValueError):
    """The evidence manifest is malformed; the message names the field."""


_NUM = (int, float)
_INT = int
# Field path -> accepted types. "{value}" also accepts {"value": <number>} (profile style).
MANIFEST_FIELDS = (
    ("format", str),
    ("plan", dict),
    ("plan.trials", _INT),
    ("plan.seeds", list),
    ("plan.required_passes", _INT),
    ("plan.command_speed", _NUM),
    ("robot", dict),
    ("robot.id", str),
    ("robot.profile.dimensions.width", "{value}"),
    ("configuration", dict),
    ("configuration.name", str),
    ("configuration.version", str),
    ("configuration.profile.locomotion.fall_base_height", "{value}"),
    ("world", dict),
    ("world.id", str),
    ("world.version", str),
    ("world.clear_width", _NUM),
    ("world.route_length", _NUM),
    ("trials", list),
)
TRIAL_FIELDS = (("seed", _INT), ("path", str), ("sha256", str))


def _is(value, kind) -> bool:
    if kind == "{value}":
        value = value.get("value") if isinstance(value, dict) else value
        kind = _NUM
    if isinstance(value, bool):
        return False
    return isinstance(value, kind)


# Evidence 2.0.0: plan.trials and the trials record are objects. Seeds and required_passes
# are not part of this format. The results list may be empty when a precondition is unmet
# (no trial is graded); the index check then happens only for a run that is graded.
V2_FIELDS = (
    ("plan", dict),
    ("plan.trials", dict),
    ("plan.command_speed", _NUM),
    ("robot", dict),
    ("robot.id", str),
    ("robot.profile.dimensions.width", "{value}"),
    ("configuration", dict),
    ("configuration.name", str),
    ("configuration.version", str),
    ("configuration.profile.locomotion.fall_base_height", "{value}"),
    ("world", dict),
    ("world.id", str),
    ("world.version", str),
    ("world.clear_width", _NUM),
    ("world.route_length", _NUM),
    ("trials", dict),
)
V2_RESULT_FIELDS = (("index", _INT), ("path", str), ("sha256", str))
_ABSENT = object()


def _check_fields(manifest, fields) -> None:
    for path, kind in fields:
        node = manifest
        for i, key in enumerate(path.split(".")):
            if not isinstance(node, dict) or key not in node:
                where = ".".join(path.split(".")[: i + 1])
                raise ManifestError(f"manifest field '{where}' is missing")
            node = node[key]
        if not _is(node, kind):
            raise ManifestError(f"manifest field '{path}' has the wrong type")


def _positive_count(value, where: str) -> None:
    if not _is(value, _INT) or value < 1:
        raise ManifestError(f"manifest field '{where}' must be an integer >= 1")


def _validate_finding(finding) -> None:
    kind = finding.get("kind", _ABSENT)
    if kind is _ABSENT:
        raise ManifestError("manifest field 'trials.reproducibility.finding.kind' is missing")
    if kind != "test-system":
        raise ManifestError(
            "manifest field 'trials.reproducibility.finding.kind' must be 'test-system'"
        )
    name = finding.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError(
            "manifest field 'trials.reproducibility.finding.name' is missing or not a string"
        )
    if not isinstance(finding.get("detail"), str):
        raise ManifestError(
            "manifest field 'trials.reproducibility.finding.detail' has the wrong type"
        )


def _validate_reproducibility(report) -> None:
    for key in ("compared", "identical", "finding"):
        if key not in report:
            raise ManifestError(f"manifest field 'trials.reproducibility.{key}' is missing")
    if not isinstance(report["compared"], bool):
        raise ManifestError("manifest field 'trials.reproducibility.compared' has the wrong type")
    identical, finding = report["identical"], report["finding"]
    if identical is not None and not isinstance(identical, bool):
        raise ManifestError("manifest field 'trials.reproducibility.identical' has the wrong type")
    if finding is not None and not isinstance(finding, dict):
        raise ManifestError("manifest field 'trials.reproducibility.finding' has the wrong type")
    if report["compared"] is False:
        if identical is not None or finding is not None:
            raise ManifestError(
                "manifest field 'trials.reproducibility' is not a comparison, "
                "so identical and finding must be null"
            )
        return
    if identical is None:
        raise ManifestError(
            "manifest field 'trials.reproducibility.identical' is missing for a comparison"
        )
    if identical is True and finding is not None:
        raise ManifestError(
            "manifest field 'trials.reproducibility.finding' must be null when trials agree"
        )
    if identical is False:
        if not isinstance(finding, dict):
            raise ManifestError("manifest field 'trials.reproducibility.finding' is missing")
        _validate_finding(finding)


def _validate_v1(manifest) -> None:
    _check_fields(manifest, MANIFEST_FIELDS)
    if not all(_is(s, _INT) for s in manifest["plan"]["seeds"]):
        raise ManifestError("manifest field 'plan.seeds' must list integers")
    for i, t in enumerate(manifest["trials"]):
        if not isinstance(t, dict):
            raise ManifestError(f"manifest field 'trials[{i}]' has the wrong type")
        for key, kind in TRIAL_FIELDS:
            if key not in t:
                raise ManifestError(f"manifest field 'trials[{i}].{key}' is missing")
            if not _is(t[key], kind):
                raise ManifestError(f"manifest field 'trials[{i}].{key}' has the wrong type")


def _validate_v2(manifest) -> None:
    _check_fields(manifest, V2_FIELDS)
    for retired in ("seeds", "required_passes"):
        if retired in manifest["plan"]:
            raise ManifestError(f"manifest field 'plan.{retired}' is not part of {EVIDENCE_V2}")
    plan_trials = manifest["plan"]["trials"]
    if "count" not in plan_trials:
        raise ManifestError("manifest field 'plan.trials.count' is missing")
    _positive_count(plan_trials["count"], "plan.trials.count")
    trials = manifest["trials"]
    for key, kind in (("count", _INT), ("results", list), ("reproducibility", dict)):
        if key not in trials:
            raise ManifestError(f"manifest field 'trials.{key}' is missing")
        if not _is(trials[key], kind):
            raise ManifestError(f"manifest field 'trials.{key}' has the wrong type")
    _positive_count(trials["count"], "trials.count")
    if trials["count"] != manifest["plan"]["trials"]["count"]:
        raise ManifestError(
            f"manifest field 'trials.count' {trials['count']} does not match "
            f"plan.trials.count {manifest['plan']['trials']['count']}"
        )
    for i, t in enumerate(trials["results"]):
        if not isinstance(t, dict):
            raise ManifestError(f"manifest field 'trials.results[{i}]' has the wrong type")
        for key, kind in V2_RESULT_FIELDS:
            if key not in t:
                raise ManifestError(f"manifest field 'trials.results[{i}].{key}' is missing")
            if not _is(t[key], kind):
                raise ManifestError(
                    f"manifest field 'trials.results[{i}].{key}' has the wrong type"
                )
        if t["index"] < 1:
            raise ManifestError(
                f"manifest field 'trials.results[{i}].index' must be an integer >= 1"
            )
    _validate_reproducibility(trials["reproducibility"])


def validate_manifest(manifest) -> None:
    """Raise ManifestError naming the first missing or mistyped field; nothing is assessed.

    Unknown evidence formats are refused here, before any format-specific field is read.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest: expected a JSON object")
    fmt = manifest.get("format", _ABSENT)
    if fmt is _ABSENT:
        raise ManifestError("manifest field 'format' is missing")
    if not isinstance(fmt, str):
        raise ManifestError("manifest field 'format' has the wrong type")
    if fmt not in MANIFEST_FORMATS:
        accepted = ", ".join(sorted(MANIFEST_FORMATS))
        raise ManifestError(f"manifest field 'format' {fmt!r} is not one of {accepted}")
    if fmt == EVIDENCE_V2:
        _validate_v2(manifest)
    else:
        _validate_v1(manifest)


def media_trial_key(document) -> str:
    """Trial identity key for a media manifest: ``index`` at 2.0.0, ``seed`` at legacy 1.0.0."""
    if not isinstance(document, dict):
        raise ManifestError("media manifest: expected a JSON object")
    fmt = document.get("format", _ABSENT)
    if fmt is _ABSENT:
        raise ManifestError("media field 'format' is missing")
    if not isinstance(fmt, str):
        raise ManifestError("media field 'format' has the wrong type")
    if fmt == MEDIA_V2:
        return "index"
    if fmt == MEDIA_V1:
        return "seed"
    accepted = ", ".join(sorted(MEDIA_FORMATS))
    raise ManifestError(f"media field 'format' {fmt!r} is not one of {accepted}")


def millimetres(metres: float) -> int:
    """Metres to integer millimetres, rounding half up from the decimal text of the value."""
    return int((Decimal(str(metres)) * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fits_width(manifest: dict) -> dict:
    """fits_width recomputed from the manifest's robot and world, never taken from its ``met``.

    Rule (walk-hallway README): robot width + 2 x wall clearance <= clear width, with each
    quantity converted to whole millimetres (round half up) before comparing.
    """
    profile = manifest["robot"]["profile"]
    cls = profile.get("class")
    if cls not in WALL_CLEARANCE:
        raise ManifestError(
            f"manifest field 'robot.profile.class' must be one of {sorted(WALL_CLEARANCE)} "
            "to check fits_width"
        )
    width = profile["dimensions"]["width"]
    width = width["value"] if isinstance(width, dict) else width
    if not _num(width):
        raise ManifestError("manifest field 'robot.profile.dimensions.width' is not a number")
    clear, clearance = manifest["world"]["clear_width"], WALL_CLEARANCE[cls]
    need = millimetres(width) + 2 * millimetres(clearance)
    met = need <= millimetres(clear)
    return {
        "name": "fits_width",
        "met": met,
        "detail": f"{width} + 2 x {clearance} = {need / 1000:.3f} m "
        f"{'<=' if met else '>'} {clear} m (recomputed by the verifier)",
    }


def unmet_precondition(manifest: dict) -> dict | None:
    """The first required precondition that is unmet, or None if all are met.

    ``preconditions`` must be a non-empty list of objects with a string ``name``, a boolean
    ``met`` and an optional string ``detail``; ``required`` is optional and boolean (default
    true), and may not be false for a precondition the specification requires. Anything
    else is a ManifestError: invalid data is never read as supported. ``fits_width`` is
    recomputed from the robot and world, whatever the manifest declares.
    """
    pre = manifest.get("preconditions")
    if pre is None:
        raise ManifestError("manifest field 'preconditions' is missing")
    if not isinstance(pre, list) or not pre:
        raise ManifestError("manifest field 'preconditions' must be a non-empty list")
    unmet = None
    for i, p in enumerate(pre):
        where = f"manifest field 'preconditions[{i}]"
        if not isinstance(p, dict):
            raise ManifestError(f"{where}' has the wrong type")
        if not isinstance(p.get("name"), str) or not p["name"]:
            raise ManifestError(f"{where}.name' is missing or not a string")
        if not isinstance(p.get("met"), bool):
            raise ManifestError(f"{where}.met' is missing or not true/false")
        if "detail" in p and not isinstance(p["detail"], str):
            raise ManifestError(f"{where}.detail' has the wrong type")
        if not isinstance(p.get("required", True), bool):
            raise ManifestError(f"{where}.required' has the wrong type")
        if p["name"] in SPEC_PRECONDITIONS and p.get("required", True) is False:
            raise ManifestError(
                f"{where}.required' is false, but the specification requires {p['name']}"
            )
        if p["name"] == "fits_width":
            continue  # recomputed below
        if unmet is None and p.get("required", True) and not p["met"]:
            unmet = {"name": p["name"], "detail": p.get("detail", "")}
    fit = fits_width(manifest)
    if not fit["met"]:
        return {"name": fit["name"], "detail": fit["detail"]}
    return unmet


def thresholds(manifest: dict) -> dict:
    """Acceptance from the specification, filled from profiles, world and plan."""
    robot = manifest["robot"]["profile"]
    conf = manifest["configuration"]["profile"]
    world = manifest["world"]
    plan = manifest["plan"]
    width = robot["dimensions"]["width"]
    width = width["value"] if isinstance(width, dict) else width
    fall = conf["locomotion"]["fall_base_height"]
    return {
        "time_limit": round(2 * world["route_length"] / plan["command_speed"] + 5, 4),
        "lateral_limit": round(world["clear_width"] / 2 - width / 2, 4),
        "half_width": round(world["clear_width"] / 2, 4),  # the exit segment is |y| <= this
        "fall_height": fall["value"] if isinstance(fall, dict) else fall,
    }


LATERAL_FORMULA = "clear_width / 2 - robot_width / 2"
EXIT_SEGMENT_FORMULA = "|y| <= clear_width / 2"
TIME_LIMIT_FORMULA = "2 * route_length / command_speed + 5 s"
FITS_WIDTH_FORMULA = "robot_width + 2 * wall_clearance <= clear_width"


def limits(manifest: dict) -> dict:
    """Each limit with the inputs it was resolved from and its formula (specs #30).

    The values are exactly the ones ``thresholds`` grades against (never re-rounded for
    display), so a report can render the width used, the formula and the limit without
    computing anything. Additive: no grading decision reads this.
    """
    th = thresholds(manifest)
    robot = manifest["robot"]["profile"]
    world, plan = manifest["world"], manifest["plan"]
    width = robot["dimensions"]["width"]
    width = width["value"] if isinstance(width, dict) else width
    cls = robot.get("class")
    fit = fits_width(manifest)
    return {
        "lateral_limit": {
            "value": th["lateral_limit"],
            "unit": "m",
            "formula": LATERAL_FORMULA,
            "inputs": {
                "clear_width": world["clear_width"],
                "robot_width": width,
                "robot_width_source": "robot.profile.dimensions.width",
            },
        },
        "exit_segment": {
            "value": th["half_width"],
            "unit": "m",
            "formula": EXIT_SEGMENT_FORMULA,
            "inputs": {"clear_width": world["clear_width"]},
        },
        "time_limit": {
            "value": th["time_limit"],
            "unit": "s",
            "formula": TIME_LIMIT_FORMULA,
            "inputs": {
                "route_length": world["route_length"],
                "command_speed": plan["command_speed"],
            },
        },
        "fall_height": {
            "value": th["fall_height"],
            "unit": "m",
            "formula": "configuration.profile.locomotion.fall_base_height",
            "inputs": {},
        },
        "fits_width": {
            "met": fit["met"],
            "unit": "m",
            "formula": FITS_WIDTH_FORMULA,
            "inputs": {
                "robot_width": width,
                "robot_class": cls,
                "wall_clearance": WALL_CLEARANCE[cls],
                "clear_width": world["clear_width"],
            },
            "detail": fit["detail"],
        },
    }


def policy(assessments, required):
    """all-required-v1: any FAIL wins; a missing or inconclusive required member is INSUFFICIENT."""
    if any(a["status"] == FAIL for a in assessments):
        return FAIL
    present = {a["evaluator"] for a in assessments if a["status"] == PASS}
    if not set(required) <= present or any(a["status"] != PASS for a in assessments):
        return INSUFFICIENT
    return PASS


def assess_trial(observation, th, eid, recorded=(), required_assessors=(), seed=None):
    if observation.get("format") not in OBSERVATION_FORMATS:
        assessments = [
            assessment(n, INSUFFICIENT, f"unknown observation format{_seed(seed)}", eid)
            for n in EVALUATORS
        ]
    else:
        assessments = [EVALUATE[n](observation, th, eid, seed) for n in EVALUATORS]
    for r in recorded:  # human or agent conclusions, never generated here
        if r.get("assessor", {}).get("type") not in ASSESSOR_TYPES - {"deterministic"}:
            raise ValueError("recorded assessments must name a human or agent assessor")
        assessments.append(r)
    required = list(EVALUATORS) + list(required_assessors)
    status = policy(assessments, required)
    found = [f for a in assessments for f in a.get("details", {}).get("failures", [])]
    return {"status": status, "assessments": assessments, "failures": found}


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def planned_counts(plan: dict) -> tuple:
    """``(n, required_passes)`` for a run that is still graded k of N.

    Evidence 1.0.0 stores an integer ``plan.trials`` and ``plan.required_passes``.
    Evidence 2.0.0 stores ``plan.trials.count`` and no ``required_passes``. A count
    that is not an integer >= 1 is not a usable plan (``(None, None)``). Compared
    repeats do not use this threshold: ``aggregate`` reports one outcome instead.
    """
    trials = plan.get("trials") if isinstance(plan, dict) else None
    if isinstance(trials, dict):
        n = trials.get("count")
        if isinstance(n, bool) or type(n) is not int or n < 1:
            return None, None
        return n, n
    if isinstance(plan, dict):
        return plan.get("trials"), plan.get("required_passes")
    return None, None


def aggregate(trials, plan, reproducibility=None, repeat_records=None):
    """k of the planned N, unless the evidence compared the repeats.

    Wilson uses the planned N; a missing trial counts as not passed and makes the
    grade INSUFFICIENT_EVIDENCE (same rule as the capability report). When
    ``reproducibility.compared`` is true, the summary is one outcome plus that
    record: no k, no n, and no Wilson interval.
    """
    repeats = None
    if isinstance(plan, dict) and isinstance(plan.get("trials"), dict):
        repeats = plan["trials"].get("count")
    # Imported here: evaluate imports this module, and this call happens after both load.
    from .evaluate import repeat_assessment

    block = repeat_assessment(
        reproducibility, repeats, [t["status"] for t in trials], repeat_records
    )
    if block is not None:
        # k, n and the interval are present and empty. The reporter requires the keys
        # and raises when they are missing; None is not a rate and not a numeric bound.
        return {
            "k": None,
            "n": None,
            "required_passes": None,
            "wilson95": [None, None],
            "grade": block["grade"],
            "rationale": block["rationale"],
            "reproducibility": block["reproducibility"],
            "aggregation": block["aggregation"],
        }
    k = sum(t["status"] == PASS for t in trials)
    unsure = sum(t["status"] == INSUFFICIENT for t in trials)
    n, need = planned_counts(plan)
    if len(trials) != n:
        grade = INSUFFICIENT
    elif k >= need:
        grade = PASS
    elif k + unsure >= need:
        grade = INSUFFICIENT
    else:
        grade = FAIL
    return {
        "k": k,
        "n": n,
        "required_passes": need,
        "wilson95": wilson(k, n),
        "grade": grade,
        "aggregation": AGGREGATION,
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reproducibility_findings(manifest: dict) -> list | None:
    """Test-system findings copied from an evidence 2.0.0 manifest, or None for 1.0.0.

    A reproducibility disagreement is one of these findings. It is not a trial status
    and it is not a robot FAIL. Evidence 1.0.0 has no such record.
    """
    if manifest.get("format") != EVIDENCE_V2:
        return None
    finding = manifest["trials"]["reproducibility"]["finding"]
    return [dict(finding)] if finding else []


def _with_findings(result: dict, findings: list | None) -> dict:
    if findings is None:
        return result
    result["findings"] = findings
    if findings:
        result["summary"] = {**result["summary"], "findings": findings}
    return result


def verify(manifest_path: Path, recorded: dict | None = None) -> dict:
    """Assess every trial named by the evidence manifest.

    Seeded evidence keeps a k-of-N summary. Compared repeats are one outcome plus
    the evidence reproducibility record.
    """
    manifest_path = Path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest is not valid JSON: {e}") from None
    validate_manifest(manifest)
    plan = manifest["plan"]
    unmet = unmet_precondition(manifest)
    if unmet is not None:
        # Declared unsupported: no trial is read or graded, whatever the manifest lists.
        reason = f"precondition {unmet['name']} unmet" + (
            f": {unmet['detail']}" if unmet["detail"] else ""
        )
        n, need = planned_counts(plan)
        return _with_findings(
            {
                "plan": plan,
                "policy": POLICY,
                "trials": [],
                "unsupported": unmet,
                "summary": {
                    "k": 0,
                    "n": n,
                    "required_passes": need,
                    "grade": UNSUPPORTED,
                    "reason": reason,
                    "aggregation": AGGREGATION,
                },
                "failures": [],
            },
            reproducibility_findings(manifest),
        )
    v2 = manifest["format"] == EVIDENCE_V2
    if v2:
        count = plan["trials"]["count"]
        results = manifest["trials"]["results"]
        indexes = [t["index"] for t in results]
        if sorted(indexes) != list(range(1, count + 1)):
            raise ManifestError(
                f"trials: indexes {sorted(indexes)} do not match trials.count {count}"
            )
        ordered = sorted(results, key=lambda t: t["index"])
    elif (
        sorted(t["seed"] for t in manifest["trials"]) != sorted(plan["seeds"])
        or len(plan["seeds"]) != plan["trials"]
    ):
        raise ManifestError(
            f"trials: seeds {sorted(t['seed'] for t in manifest['trials'])} do not match "
            f"plan.seeds {sorted(plan['seeds'])} with plan.trials {plan['trials']}"
        )
    else:
        ordered = sorted(manifest["trials"], key=lambda t: t["seed"])
    th = thresholds(manifest)
    base = manifest_path.parent
    trials = []
    for t in ordered:
        # The id keeps the -seed- suffix. The pinned reporter matches video links on it
        # and has no index field. For evidence 2.0.0 the number is the trial index.
        number = t["index"] if v2 else t["seed"]
        eid = f"{manifest['configuration']['name']}-seed-{number:02d}"
        seed_arg = ("trial", number) if v2 else number
        path = base / t["path"]
        if not path.exists() or _sha(path) != t["sha256"]:
            res = {
                "status": INSUFFICIENT,
                "assessments": [
                    assessment(n, INSUFFICIENT, "evidence missing or digest mismatch", eid)
                    for n in EVALUATORS
                ],
            }
        else:
            try:
                observation = json.loads(path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                label = f"index={number}" if v2 else f"seed={number}"
                raise ManifestError(
                    f"trials[{label}]: {t['path']} is not valid JSON: {e}"
                ) from None
            res = assess_trial(
                observation,
                th,
                eid,
                (recorded or {}).get(eid, ()),
                plan.get("required_assessors", ()),
                seed_arg,
            )
        res.setdefault("failures", [])
        record = {
            "id": eid,
            "evidence": {"id": eid, "path": t["path"], "sha256": t["sha256"]},
            **res,
        }
        if v2:
            record["index"] = number
        else:
            record["seed"] = number
        trials.append(record)
    return _with_findings(
        {
            "plan": plan,
            "thresholds": th,
            "limits": limits(manifest),
            "policy": POLICY,
            "trials": trials,
            "summary": aggregate(
                trials,
                plan,
                manifest["trials"]["reproducibility"] if v2 else None,
                manifest["trials"]["results"] if v2 else None,
            ),
            "failures": [f for t in trials for f in t["failures"]],
        },
        reproducibility_findings(manifest),
    )


def _repeat_line(rec: dict) -> str:
    """One evidence repeat, using only fields the evidence stored."""
    bits = []
    if "index" in rec:
        bits.append(f"index {rec['index']}")
    if "outcome" in rec:
        bits.append(f"outcome {rec['outcome']}")
    if "trajectory_sha256" in rec:
        bits.append(f"trajectory_sha256 {rec['trajectory_sha256']}")
    if "sha256" in rec:
        bits.append(f"sha256 {rec['sha256']}")
    return "  " + ", ".join(bits)


def summary_lines(result: dict) -> list[str]:
    s = result["summary"]
    if s["grade"] == UNSUPPORTED:
        lines = [f"{UNSUPPORTED}: {s['reason']} (no trials assessed)"]
    elif (
        isinstance(s.get("reproducibility"), dict) and s["reproducibility"].get("compared") is True
    ):
        lines = [f"{s['rationale']}: {s['grade']}"]
        for t in result["trials"]:
            if t["status"] != PASS:
                why = "; ".join(a["rationale"] for a in t["assessments"] if a["status"] != PASS)
                where = f"trial {t['index']:2d}" if "index" in t else f"seed {t['seed']:2d}"
                lines.append(f"  {where} {t['status']}: {why}")
        for rec in s["reproducibility"].get("differing") or []:
            if isinstance(rec, dict):
                lines.append(_repeat_line(rec))
    else:
        lines = [
            f"{s['k']}/{s['n']} trials PASS (needs {s['required_passes']}), "
            f"Wilson 95% [{s['wilson95'][0]:.4f}, {s['wilson95'][1]:.4f}]: {s['grade']}"
        ]
        for t in result["trials"]:
            if t["status"] != PASS:
                why = "; ".join(a["rationale"] for a in t["assessments"] if a["status"] != PASS)
                where = f"trial {t['index']:2d}" if "index" in t else f"seed {t['seed']:2d}"
                lines.append(f"  {where} {t['status']}: {why}")
    for finding in result.get("findings") or []:
        lines.append(f"test-system {finding['name']}: {finding['detail']}")
    return lines
