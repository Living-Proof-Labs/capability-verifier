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
from pathlib import Path

PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT_EVIDENCE"
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
OBSERVATION_FORMAT = "livingproof-hallway-observation/1.0.0"
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


def exit_reached(o, th, eid, seed=None):
    name = "hallway-exit-reached"
    if "exit_time" not in o or not (o["exit_time"] is None or _num(o["exit_time"])):
        return _insufficient(name, "exit_time", eid, seed)
    t, limit = o["exit_time"], th["time_limit"]
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
    if observation.get("format") != OBSERVATION_FORMAT:
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
    if (
        sorted(t["seed"] for t in manifest["trials"]) != sorted(plan["seeds"])
        or len(plan["seeds"]) != plan["trials"]
    ):
        raise ValueError("evidence trials do not match the locked plan")
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
            res = assess_trial(
                json.loads(path.read_text()),
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
    lines = [
        f"{s['k']}/{s['n']} trials PASS (needs {s['required_passes']}), "
        f"Wilson 95% [{s['wilson95'][0]:.4f}, {s['wilson95'][1]:.4f}]: {s['grade']}"
    ]
    for t in result["trials"]:
        if t["status"] != PASS:
            why = "; ".join(a["rationale"] for a in t["assessments"] if a["status"] != PASS)
            lines.append(f"  seed {t['seed']:2d} {t['status']}: {why}")
    return lines
