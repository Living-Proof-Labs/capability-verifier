# SPDX-License-Identifier: Apache-2.0
"""Walk-hallway evaluators bound to the locked catalog's verifier scenarios."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenario, then, when

from capability_verifier import hallway

FEATURE = (
    Path(__file__).resolve().parents[1]
    / ".specs-hallway/specs/scenarios/walk-hallway/walk-hallway.feature"
)
TH = {"time_limit": 27.0, "lateral_limit": 0.49, "fall_height": 0.5}
# The test's own plan; the verifier reads the plan from evidence, never from code.
PLAN = {
    "scenario": "walk-hallway@0.1.0",
    "trials": 20,
    "seeds": list(range(1, 21)),
    "command_speed": 0.5,
    "required_passes": 18,
}


def observation(**changes):
    o = {
        "format": hallway.OBSERVATION_FORMAT,
        "end": "exit",
        "duration_s": 12.0,
        "exit_time": 12.0,
        "max_abs_lateral": {"value": 0.21, "time": 7.3},
        "min_base_height": {"value": 0.71, "time": 3.0},
        "wall_contacts": [],
        "trace": {
            "columns": ["t", "x", "y", "z", "wall_distance"],
            "samples": [
                [0.0, -2.5, 0.0, 0.75, 0.3],
                [9.8, 1.0, 0.21, 0.74, 0.041],
                [10.1, 1.2, 0.62, 0.73, 0.05],
                [12.0, 3.0, 0.1, 0.74, 0.2],
            ],
        },
    }
    o.update(changes)
    return o


FAILING = {
    "exit_time": observation(exit_time=None, end="time_limit", duration_s=27.0),
    "max_abs_lateral": observation(max_abs_lateral={"value": 0.62, "time": 10.1}),
    "wall_contacts": observation(
        wall_contacts=[{"wall": "wall-left", "start": 10.5, "end": 10.7, "depth": 0.032}]
    ),
    "min_base_height": observation(min_base_height={"value": 0.41, "time": 6.2}),
}


@scenario(str(FEATURE), "The verifier assesses each trial with the hallway evaluators")
def test_evaluators():
    pass


@scenario(str(FEATURE), "A trial passes only when every hallway evaluator passes")
def test_policy():
    pass


@scenario(str(FEATURE), "Trials are aggregated as k of N with a Wilson interval")
def test_aggregation():
    pass


@pytest.fixture
def ctx():
    return {}


@given("the observations of one walk-hallway trial")
def one_trial(ctx):
    ctx["passing"] = observation()


@given("thresholds resolved from the robot profile and the selected configuration profile")
def resolved(ctx):
    manifest = {
        "robot": {"profile": {"dimensions": {"width": 0.42}}},
        "configuration": {"profile": {"locomotion": {"fall_base_height": 0.5}}},
        "world": {"clear_width": 1.4, "route_length": 11.0},
        "plan": PLAN,
    }
    ctx["th"] = hallway.thresholds(manifest)
    assert ctx["th"] == {"time_limit": 49.0, "lateral_limit": 0.49, "fall_height": 0.5}
    ctx["th"] = dict(ctx["th"], time_limit=27.0)


@when(parsers.parse('evaluator "{evaluator}" assesses observation "{name}"'))
def assess(ctx, evaluator, name):
    ctx["evaluator"], ctx["name"] = evaluator, name
    ctx["pass"] = hallway.EVALUATE[evaluator](ctx["passing"], ctx["th"], "t")
    ctx["fail"] = hallway.EVALUATE[evaluator](FAILING[name], ctx["th"], "t", 7)


@then(
    parsers.re(
        r"the assessment is FAIL when the observation (?P<rule>.+) (?P<threshold>\S+(?: contact)?)$"
    )
)
def is_fail(ctx):
    assert ctx["pass"]["status"] == hallway.PASS, ctx["pass"]
    assert ctx["fail"]["status"] == hallway.FAIL, ctx["fail"]
    assert ctx["fail"]["assessor"]["type"] == "deterministic"


@then("a missing or malformed observation gives INSUFFICIENT_EVIDENCE")
def missing(ctx):
    for bad in (None, "nan", float("nan"), {"value": "x"}):
        o = observation()
        if bad is None:
            del o[ctx["name"]]
        else:
            o[ctx["name"]] = bad
        assert (
            hallway.EVALUATE[ctx["evaluator"]](o, ctx["th"], "t")["status"] == hallway.INSUFFICIENT
        )


@then("the rationale names the check, the time and the margin")
def rationale(ctx):
    expected = {
        "hallway-exit-reached": "exit not reached by 27.00 s (seed 7)",
        "hallway-lateral-limit": "lateral 0.62 m > 0.49 m at 10.10 s (seed 7)",
        "hallway-wall-contact": "wall contact depth 0.03 m at 10.50 s (seed 7)",
        "hallway-fall": "base height 0.41 m < 0.50 m at 6.20 s (seed 7)",
    }
    assert ctx["fail"]["rationale"] == expected[ctx["evaluator"]]
    structured = {
        "hallway-exit-reached": {"observed": None, "limit": 27.0, "unit": "s", "t": 27.0},
        "hallway-lateral-limit": {"observed": 0.62, "limit": 0.49, "unit": "m", "t": 10.1},
        "hallway-wall-contact": {
            "observed": 0.032,
            "limit": 0.0,
            "unit": "m",
            "t": 10.5,
            "wall": "wall-left",
        },
        "hallway-fall": {"observed": 0.41, "limit": 0.5, "unit": "m", "t": 6.2},
    }
    assert ctx["fail"]["details"]["failures"] == [
        {"check": ctx["evaluator"], "seed": 7, **structured[ctx["evaluator"]]}
    ]
    assert "failures" not in ctx["pass"]["details"]
    assert " s" in ctx["pass"]["rationale"]


@given("the four hallway assessments of one trial")
def four(ctx):
    ctx["passing"] = observation()


@when('policy "all-required-v1" is applied')
def apply_policy(ctx):
    ctx["pass"] = hallway.assess_trial(ctx["passing"], TH, "t")
    ctx["fail"] = hallway.assess_trial(FAILING["max_abs_lateral"], TH, "t")
    broken = FAILING["wall_contacts"] | {"min_base_height": None}
    ctx["fail_and_missing"] = hallway.assess_trial(broken, TH, "t")
    ctx["missing"] = hallway.assess_trial(observation(min_base_height=None), TH, "t")


@then("any FAIL makes the trial FAIL")
def any_fail(ctx):
    assert ctx["pass"]["status"] == hallway.PASS
    assert len(ctx["pass"]["assessments"]) == 4
    assert ctx["fail"]["status"] == ctx["fail_and_missing"]["status"] == hallway.FAIL


@then("any INSUFFICIENT_EVIDENCE without a FAIL makes the trial INSUFFICIENT_EVIDENCE")
def any_missing(ctx):
    assert ctx["missing"]["status"] == hallway.INSUFFICIENT


@given("<trials> trial results of one configuration")
def results(ctx):
    ok = {"status": hallway.PASS}
    ctx["sets"] = {
        18: [ok] * 18 + [{"status": hallway.FAIL}] * 2,
        17: [ok] * 17 + [{"status": hallway.FAIL}] * 3,
        "unsure": [ok] * 17 + [{"status": hallway.INSUFFICIENT}] + [{"status": hallway.FAIL}] * 2,
    }


@when("the trial results are aggregated")
def aggregated(ctx):
    ctx["agg"] = {k: hallway.aggregate(v, PLAN) for k, v in ctx["sets"].items()}


@then("the result reports passed trials k of N with its Wilson 95% interval")
def wilson(ctx):
    a = ctx["agg"][17]
    assert (a["k"], a["n"]) == (17, 20)
    assert a["wilson95"] == [0.6396, 0.9476]
    assert hallway.wilson(12, 20) == [0.3866, 0.7812]  # matches the step 2 G1 stock run


@then("the grade is PASS only if at least <required_passes> trials pass")
def grade(ctx):
    assert ctx["agg"][18]["grade"] == hallway.PASS
    assert ctx["agg"][17]["grade"] == hallway.FAIL
    assert ctx["agg"]["unsure"]["grade"] == hallway.INSUFFICIENT


def _manifest(tmp_path, plan=PLAN, failing_seed=None):
    trials = []
    for seed in plan["seeds"]:
        o = FAILING["max_abs_lateral"] if seed == failing_seed else observation()
        p = tmp_path / f"trial-{seed:02d}.json"
        p.write_text(json.dumps(o))
        trials.append(
            {"seed": seed, "path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        )
    m = {
        "format": hallway.MANIFEST_FORMAT,
        "plan": plan,
        "robot": {"id": "unitree-g1", "profile": {"dimensions": {"width": 0.42}}},
        "configuration": {
            "name": "stock",
            "version": "1.1.0",
            "profile": {"locomotion": {"fall_base_height": 0.5}},
        },
        "world": {
            "id": "hallway-open",
            "version": "1.0.0",
            "clear_width": 1.4,
            "route_length": 5.5,
        },
        "trials": trials,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(m))
    return path


def test_plan_comes_from_evidence(tmp_path):
    plan = dict(PLAN, trials=3, seeds=[4, 5, 6], required_passes=3)
    r = hallway.verify(_manifest(tmp_path, plan, failing_seed=5))
    assert [t["seed"] for t in r["trials"]] == [4, 5, 6]
    assert r["summary"]["k"] == 2 and r["summary"]["grade"] == hallway.FAIL
    assert r["thresholds"]["time_limit"] == 27.0


def test_tampered_evidence_is_insufficient(tmp_path):
    path = _manifest(tmp_path)
    (tmp_path / "trial-03.json").write_text("{}")
    r = hallway.verify(path)
    assert r["trials"][2]["status"] == hallway.INSUFFICIENT


def test_plan_mismatch_is_rejected(tmp_path):
    path = _manifest(tmp_path)
    m = json.loads(path.read_text())
    m["plan"] = dict(PLAN, seeds=list(range(2, 22)))
    path.write_text(json.dumps(m))
    with pytest.raises(ValueError):
        hallway.verify(path)


def test_recorded_human_assessment_is_judged_by_the_same_policy():
    human = hallway.assessment(
        "hallway-review", hallway.FAIL, "operator saw a stumble at 8.0 s", "t", assessor="human"
    )
    r = hallway.assess_trial(observation(), TH, "t", recorded=[human])
    assert r["status"] == hallway.FAIL and r["assessments"][-1]["assessor"]["type"] == "human"
    r = hallway.assess_trial(observation(), TH, "t", required_assessors=["hallway-review"])
    assert r["status"] == hallway.INSUFFICIENT
    with pytest.raises(ValueError):
        hallway.assess_trial(observation(), TH, "t", recorded=[copy.deepcopy(r["assessments"][0])])


def test_lengths_never_read_as_equal():
    o = observation(max_abs_lateral={"value": 0.4904, "time": 9.2})
    a = hallway.lateral_limit(o, TH, "t", 17)
    assert a["rationale"] == "lateral 0.4904 m > 0.4900 m at 10.10 s (seed 17)"
    assert a["details"]["failures"][0]["observed"] == 0.4904


def test_failures_are_collected_per_trial_and_run(tmp_path):
    r = hallway.verify(_manifest(tmp_path, failing_seed=7))
    assert r["failures"] == [
        {
            "check": "hallway-lateral-limit",
            "observed": 0.62,
            "limit": 0.49,
            "unit": "m",
            "t": 10.1,
            "seed": 7,
        }
    ]
    assert r["trials"][6]["failures"] == r["failures"] and r["trials"][0]["failures"] == []
