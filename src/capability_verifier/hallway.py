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


def exit_reached(o, th, eid):
    name = "hallway-exit-reached"
    if "exit_time" not in o or not (o["exit_time"] is None or _num(o["exit_time"])):
        return assessment(name, INSUFFICIENT, "missing or malformed observation exit_time", eid)
    t, limit = o["exit_time"], th["time_limit"]
    if t is None:
        last = (o.get("trace") or {}).get("samples") or [[None, None]]
        x = last[-1][1]
        where = (
            f"; base ended at x {x:.2f} m after {o.get('duration_s', 0):.1f} s" if _num(x) else ""
        )
        return assessment(
            name,
            FAIL,
            f"exit not reached within {limit:.1f} s{where}",
            eid,
            {"observed": None, "threshold": limit},
        )
    margin = round(limit - t, 4)
    ok = t <= limit
    text = (
        f"exit at {t:.1f} s {'<=' if ok else '>'} {limit:.1f} s "
        f"({abs(margin):.1f} s {'spare' if ok else 'late'})"
    )
    return assessment(
        name,
        PASS if ok else FAIL,
        text,
        eid,
        {"observed": t, "threshold": limit, "time": t, "margin": margin},
    )


def lateral_limit(o, th, eid):
    name = "hallway-lateral-limit"
    e = _extreme(o, "max_abs_lateral")
    if e is None:
        return assessment(
            name, INSUFFICIENT, "missing or malformed observation max_abs_lateral", eid
        )
    v, limit = e["value"], th["lateral_limit"]
    if v > limit:
        # First time over the limit, from the trace when it is present.
        t = next(
            (s[0] for s in (o.get("trace") or {}).get("samples", []) if abs(s[2]) > limit),
            e["time"],
        )
        text = f"lateral {v:.3f} m > {limit:.3f} m at {t:.1f} s (over by {v - limit:.3f} m)"
        return assessment(
            name,
            FAIL,
            text,
            eid,
            {"observed": v, "threshold": limit, "time": t, "margin": round(limit - v, 4)},
        )
    text = f"lateral {v:.3f} m <= {limit:.3f} m, largest at {e['time']:.1f} s (margin {limit - v:.3f} m)"
    return assessment(
        name,
        PASS,
        text,
        eid,
        {"observed": v, "threshold": limit, "time": e["time"], "margin": round(limit - v, 4)},
    )


def wall_contact(o, th, eid):
    name = "hallway-wall-contact"
    contacts = o.get("wall_contacts")
    ok_shape = isinstance(contacts, list) and all(
        isinstance(c, dict) and _num(c.get("start")) and _num(c.get("depth")) for c in contacts
    )
    if not ok_shape:
        return assessment(name, INSUFFICIENT, "missing or malformed observation wall_contacts", eid)
    if contacts:
        first = contacts[0]
        deepest = max(c["depth"] for c in contacts)
        more = (
            f"; {len(contacts)} contacts, deepest {deepest * 100:.1f} cm"
            if len(contacts) > 1
            else ""
        )
        text = (
            f"wall contact with {first.get('wall', 'a wall')} at {first['start']:.1f} s, "
            f"depth {first['depth'] * 100:.1f} cm{more}"
        )
        return assessment(
            name,
            FAIL,
            text,
            eid,
            {"observed": len(contacts), "threshold": 0, "time": first["start"], "margin": -deepest},
        )
    samples = (o.get("trace") or {}).get("samples") or []
    if samples and len(samples[0]) > 4:
        closest = min(samples, key=lambda s: s[4])
        text = f"no wall contact; closest {closest[4] * 100:.1f} cm at {closest[0]:.1f} s"
        details = {"observed": 0, "threshold": 0, "time": closest[0], "margin": closest[4]}
    else:
        text, details = "no wall contact", {"observed": 0, "threshold": 0}
    return assessment(name, PASS, text, eid, details)


def fall(o, th, eid):
    name = "hallway-fall"
    e = _extreme(o, "min_base_height")
    if e is None:
        return assessment(
            name, INSUFFICIENT, "missing or malformed observation min_base_height", eid
        )
    v, limit = e["value"], th["fall_height"]
    ok = v >= limit
    text = (
        f"base height {v:.2f} m {'>=' if ok else '<'} {limit:.2f} m at {e['time']:.1f} s "
        f"(margin {v - limit:.2f} m)"
    )
    return assessment(
        name,
        PASS if ok else FAIL,
        text,
        eid,
        {"observed": v, "threshold": limit, "time": e["time"], "margin": round(v - limit, 4)},
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


def assess_trial(observation, th, eid, recorded=(), required_assessors=()):
    if observation.get("format") != OBSERVATION_FORMAT:
        assessments = [
            assessment(n, INSUFFICIENT, "unknown observation format", eid) for n in EVALUATORS
        ]
    else:
        assessments = [EVALUATE[n](observation, th, eid) for n in EVALUATORS]
    for r in recorded:  # human or agent conclusions, never generated here
        if r.get("assessor", {}).get("type") not in ASSESSOR_TYPES - {"deterministic"}:
            raise ValueError("recorded assessments must name a human or agent assessor")
        assessments.append(r)
    required = list(EVALUATORS) + list(required_assessors)
    return {"status": policy(assessments, required), "assessments": assessments}


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def aggregate(trials, plan):
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
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError("not a hallway evidence manifest")
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
            )
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
