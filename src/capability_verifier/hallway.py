# SPDX-License-Identifier: Apache-2.0
"""Walk-hallway evaluators, per-trial all-required-v1 policy and k/N aggregation.

Inputs are evidence only: an evidence manifest written by the simulator (it echoes the
robot repository's verification lock, including the trial plan) and one observation
file per trial. Nothing here hard-codes a trial count, seed list or speed; they come
from the manifest's ``plan``. Thresholds are resolved from the robot profile, the
configuration profile and the world quantities, as the specification's acceptance
table defines.

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

PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT_EVIDENCE"
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
MANIFEST_FORMAT = "livingproof-hallway-evidence/1.0.0"


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


def validate_manifest(manifest) -> None:
    """Raise ManifestError naming the first missing or mistyped field; nothing is assessed."""
    if not isinstance(manifest, dict):
        raise ManifestError("manifest: expected a JSON object")
    for path, kind in MANIFEST_FIELDS:
        node = manifest
        for i, key in enumerate(path.split(".")):
            if not isinstance(node, dict) or key not in node:
                where = ".".join(path.split(".")[: i + 1])
                raise ManifestError(f"manifest field '{where}' is missing")
            node = node[key]
        if not _is(node, kind):
            raise ManifestError(f"manifest field '{path}' has the wrong type")
    if manifest["format"] != MANIFEST_FORMAT:
        raise ManifestError(f"manifest field 'format' is not {MANIFEST_FORMAT}")
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


def assessment(evaluator, status, rationale, evidence_id, details=None, assessor="deterministic"):
    return {
        "id": f"{evidence_id}/{evaluator}",
        "evaluator": evaluator,
        "version": VERSION,
        "assessor": {"type": assessor, "id": evaluator, "version": VERSION},
        "status": status,
        "rationale": rationale,
        "mandatory": True,
        "evidence_ids": [evidence_id],
        "details": details or {},
    }


def _num(v):
    return type(v) in (int, float) and math.isfinite(v)


def _extreme(o, name):
    e = o.get(name)
    if not isinstance(e, dict) or not _num(e.get("value")) or not _num(e.get("time")):
        return None
    return e


def _length(v, limit):
    """Fewest decimals (at least 2) at which observed and limit no longer read as equal."""
    for d in range(2, 7):
        if f"{v:.{d}f}" != f"{limit:.{d}f}":
            return f"{v:.{d}f}", f"{limit:.{d}f}"
    return f"{v:.6g}", f"{limit:.6g}"


def failure(check, observed, limit, unit, t, seed, **extra):
    """Structured failed check, so a PR comment can group failures."""
    return {
        "check": check,
        "observed": observed,
        "limit": limit,
        "unit": unit,
        "t": None if t is None else round(t, 2),
        "seed": seed,
        **extra,
    }


def _seed(seed):
    return f" (seed {seed})" if seed is not None else ""


def _insufficient(name, what, eid, seed):
    return assessment(
        name, INSUFFICIENT, f"missing or malformed observation {what}{_seed(seed)}", eid
    )


def _exit_y(o, t):
    """The base's y in the trace sample at time ``t`` (the exit crossing), or None."""
    samples = (o.get("trace") or {}).get("samples")
    if not isinstance(samples, list):
        return None
    at = [s for s in samples if isinstance(s, list) and len(s) > 2 and s[0] == t and _num(s[2])]
    return at[-1][2] if at else None


def exit_reached(o, th, eid, seed=None):
    """PASS only for a crossed_exit through the exit segment (|y| <= half-width) in time.
    A left_hallway end (through a wall, or the exit line outside the segment) is a FAIL;
    an exit whose crossing point cannot be read is INSUFFICIENT, never a PASS."""
    name = "hallway-exit-reached"
    if "exit_time" not in o or not (o["exit_time"] is None or _num(o["exit_time"])):
        return _insufficient(name, "exit_time", eid, seed)
    end = o.get("end")
    if end not in OBSERVATION_ENDS.get(o.get("format"), ()):
        return _insufficient(name, "end", eid, seed)
    t, limit, half = o["exit_time"], th["time_limit"], th["half_width"]
    if end == "left_hallway":
        left = o.get("left_hallway")
        if t is not None or not (isinstance(left, dict) and _num(left.get("time"))):
            return _insufficient(name, "left_hallway", eid, seed)
        via = left.get("via") if isinstance(left.get("via"), str) else "unknown"
        f = failure(name, None, limit, "s", left["time"], seed, via=via)
        text = f"left the hallway via {via} at {left['time']:.2f} s{_seed(seed)}"
        return assessment(
            name,
            FAIL,
            text,
            eid,
            {"observed": None, "threshold": limit, "time": left["time"], "failures": [f]},
        )
    if (t is None) != (end != "crossed_exit"):
        return _insufficient(name, "exit_time", eid, seed)
    if t is not None:
        y = _exit_y(o, t)
        if y is None:
            return _insufficient(name, "exit crossing point", eid, seed)
        if abs(y) > half:
            a, b = _length(abs(y), half)
            f = failure(name, abs(y), half, "m", t, seed, via="exit-outside-segment")
            text = f"exit line crossed at |y| {a} m > {b} m, outside the exit{_seed(seed)}"
            details = {"observed": abs(y), "threshold": half, "time": t, "failures": [f]}
            return assessment(name, FAIL, text, eid, details)
    if t is None:
        end = o.get("duration_s") if _num(o.get("duration_s")) else limit
        f = failure(name, None, limit, "s", end, seed)
        text = f"exit not reached by {limit:.2f} s{_seed(seed)}"
        return assessment(
            name, FAIL, text, eid, {"observed": None, "threshold": limit, "failures": [f]}
        )
    margin = round(limit - t, 4)
    if t > limit:
        f = failure(name, t, limit, "s", t, seed)
        text = f"exit {t:.2f} s > {limit:.2f} s at {t:.2f} s{_seed(seed)}"
        return assessment(
            name,
            FAIL,
            text,
            eid,
            {"observed": t, "threshold": limit, "time": t, "margin": margin, "failures": [f]},
        )
    text = f"exit at {t:.2f} s <= {limit:.2f} s ({margin:.2f} s spare){_seed(seed)}"
    return assessment(
        name, PASS, text, eid, {"observed": t, "threshold": limit, "time": t, "margin": margin}
    )


def lateral_limit(o, th, eid, seed=None):
    name = "hallway-lateral-limit"
    e = _extreme(o, "max_abs_lateral")
    if e is None:
        return _insufficient(name, "max_abs_lateral", eid, seed)
    v, limit = e["value"], th["lateral_limit"]
    margin = round(limit - v, 6)
    a, b = _length(v, limit)
    if v > limit:
        # First time over the limit, from the trace when it is present.
        t = next(
            (s[0] for s in (o.get("trace") or {}).get("samples", []) if abs(s[2]) > limit),
            e["time"],
        )
        f = failure(name, v, limit, "m", t, seed)
        text = f"lateral {a} m > {b} m at {t:.2f} s{_seed(seed)}"
        return assessment(
            name,
            FAIL,
            text,
            eid,
            {"observed": v, "threshold": limit, "time": t, "margin": margin, "failures": [f]},
        )
    text = f"lateral {a} m <= {b} m, largest at {e['time']:.2f} s{_seed(seed)}"
    return assessment(
        name,
        PASS,
        text,
        eid,
        {"observed": v, "threshold": limit, "time": e["time"], "margin": margin},
    )


def wall_contact(o, th, eid, seed=None):
    name = "hallway-wall-contact"
    contacts = o.get("wall_contacts")
    ok_shape = isinstance(contacts, list) and all(
        isinstance(c, dict) and _num(c.get("start")) and _num(c.get("depth")) for c in contacts
    )
    if not ok_shape:
        return _insufficient(name, "wall_contacts", eid, seed)
    if contacts:
        fails = [
            failure(name, c["depth"], 0.0, "m", c["start"], seed, wall=c.get("wall"))
            for c in contacts
        ]
        first = contacts[0]
        a, _ = _length(first["depth"], 0.0)
        more = f"; {len(contacts)} contacts" if len(contacts) > 1 else ""
        text = f"wall contact depth {a} m at {first['start']:.2f} s{_seed(seed)}{more}"
        return assessment(
            name,
            FAIL,
            text,
            eid,
            {
                "observed": len(contacts),
                "threshold": 0,
                "time": first["start"],
                "margin": -max(c["depth"] for c in contacts),
                "failures": fails,
            },
        )
    samples = (o.get("trace") or {}).get("samples") or []
    if samples and len(samples[0]) > 4:
        closest = min(samples, key=lambda s: s[4])
        text = f"no wall contact; closest {closest[4]:.3f} m at {closest[0]:.2f} s{_seed(seed)}"
        details = {"observed": 0, "threshold": 0, "time": closest[0], "margin": closest[4]}
    else:
        text, details = f"no wall contact{_seed(seed)}", {"observed": 0, "threshold": 0}
    return assessment(name, PASS, text, eid, details)


def fall(o, th, eid, seed=None):
    name = "hallway-fall"
    e = _extreme(o, "min_base_height")
    if e is None:
        return _insufficient(name, "min_base_height", eid, seed)
    v, limit = e["value"], th["fall_height"]
    a, b = _length(v, limit)
    details = {"observed": v, "threshold": limit, "time": e["time"], "margin": round(v - limit, 6)}
    if v < limit:
        details["failures"] = [failure(name, v, limit, "m", e["time"], seed)]
        return assessment(
            name, FAIL, f"base height {a} m < {b} m at {e['time']:.2f} s{_seed(seed)}", eid, details
        )
    return assessment(
        name,
        PASS,
        f"base height {a} m >= {b} m, lowest at {e['time']:.2f} s{_seed(seed)}",
        eid,
        details,
    )


EVALUATE = {
    "hallway-exit-reached": exit_reached,
    "hallway-lateral-limit": lateral_limit,
    "hallway-wall-contact": wall_contact,
    "hallway-fall": fall,
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


def aggregate(trials, plan):
    """k of the planned N. Wilson uses the planned N; a missing trial counts as not passed
    and makes the grade INSUFFICIENT_EVIDENCE (same rule as the capability report)."""
    k = sum(t["status"] == PASS for t in trials)
    unsure = sum(t["status"] == INSUFFICIENT for t in trials)
    n, need = plan["trials"], plan["required_passes"]
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


def verify(manifest_path: Path, recorded: dict | None = None) -> dict:
    """Assess every trial named by the evidence manifest; return per-trial and k/N results."""
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
        return {
            "plan": plan,
            "policy": POLICY,
            "trials": [],
            "unsupported": unmet,
            "summary": {
                "k": 0,
                "n": plan["trials"],
                "required_passes": plan["required_passes"],
                "grade": UNSUPPORTED,
                "reason": reason,
                "aggregation": AGGREGATION,
            },
            "failures": [],
        }
    if (
        sorted(t["seed"] for t in manifest["trials"]) != sorted(plan["seeds"])
        or len(plan["seeds"]) != plan["trials"]
    ):
        raise ManifestError(
            f"trials: seeds {sorted(t['seed'] for t in manifest['trials'])} do not match "
            f"plan.seeds {sorted(plan['seeds'])} with plan.trials {plan['trials']}"
        )
    th = thresholds(manifest)
    base = manifest_path.parent
    trials = []
    for t in sorted(manifest["trials"], key=lambda t: t["seed"]):
        eid = f"{manifest['configuration']['name']}-seed-{t['seed']:02d}"
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
                raise ManifestError(
                    f"trials[seed={t['seed']}]: {t['path']} is not valid JSON: {e}"
                ) from None
            res = assess_trial(
                observation,
                th,
                eid,
                (recorded or {}).get(eid, ()),
                plan.get("required_assessors", ()),
                t["seed"],
            )
        res.setdefault("failures", [])
        trials.append(
            {
                "id": eid,
                "seed": t["seed"],
                "evidence": {"id": eid, "path": t["path"], "sha256": t["sha256"]},
                **res,
            }
        )
    return {
        "plan": plan,
        "thresholds": th,
        "policy": POLICY,
        "trials": trials,
        "summary": aggregate(trials, plan),
        "failures": [f for t in trials for f in t["failures"]],
    }


def summary_lines(result: dict) -> list[str]:
    s = result["summary"]
    if s["grade"] == UNSUPPORTED:
        return [f"{UNSUPPORTED}: {s['reason']} (no trials assessed)"]
    lines = [
        f"{s['k']}/{s['n']} trials PASS (needs {s['required_passes']}), "
        f"Wilson 95% [{s['wilson95'][0]:.4f}, {s['wilson95'][1]:.4f}]: {s['grade']}"
    ]
    for t in result["trials"]:
        if t["status"] != PASS:
            why = "; ".join(a["rationale"] for a in t["assessments"] if a["status"] != PASS)
            lines.append(f"  seed {t['seed']:2d} {t['status']}: {why}")
    return lines
