"""Policy, aggregation and bundle 1.1.0, ported from livingproof-capability-report v1.10.0."""

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

from capability_verifier import evaluate, hallway
from test_hallway import FAILING, PLAN, TH, observation
from test_hallway import _manifest as _write_manifest

ROOT = Path(__file__).resolve().parents[1]
SPEC = {
    "repository": evaluate.SPECS,
    "tag": "v1.6.1",
    "commit": "0bdf1db3f975b044692c0b8f1ec37737ad8b889f",
    "capability": "walk-hallway",
    "revision": "0.1.0",
}
P, F, U, X = evaluate.PASS, evaluate.FAIL, evaluate.INSUFFICIENT, evaluate.INCOMPATIBLE


def _manifest(path, **kw):
    path.mkdir(parents=True, exist_ok=True)
    return _write_manifest(path, **kw)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_eval", ROOT / "tools" / "verify_hallway.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle_assessment(a):
    return {
        "id": a["id"],
        "evaluator": a["evaluator"],
        "kind": a["assessor"]["type"],
        "version": a["version"],
        "status": a["status"],
        "rationale": a["rationale"],
        "mandatory": a["mandatory"],
        "evidence_ids": a["evidence_ids"],
        "provenance": {"assessor": a["assessor"], "details": a["details"]},
    }


def result(rid="r1", obs=None, plan=PLAN, run_id="run-1", **provenance):
    assessments = evaluate.HallwayEvaluator().assess(obs or observation(), TH, rid, seed=1)
    return {
        "id": rid,
        "spec": dict(SPEC),
        "system": {"id": "unitree-g1/stock", "version": "1.1.0"},
        "environment": {"id": "hallway-open", "version": "1.0.0"},
        "evidence": [{"id": rid, "path": f"evidence/{rid}.json", "sha256": "0" * 64}],
        "assessments": [bundle_assessment(a) for a in assessments],
        "provenance": {"run_id": run_id, "plan": plan, **provenance},
    }


def test_hallway_evaluator_matches_assess_trial():
    for obs in [observation(), *FAILING.values(), {"format": "other"}]:
        mine = evaluate.HallwayEvaluator().assess(obs, TH, "e", seed=3)
        assert mine == hallway.assess_trial(obs, TH, "e", seed=3)["assessments"]


def test_policy_pass_fail_and_incomplete_evidence():
    policy = evaluate.AllRequiredV1()
    assert policy.decide(result(), True).status == P
    assert policy.decide(result(), True).reason == (
        "All four hallway evaluators and every retained assessment pass"
    )
    assert policy.decide(result(), True).assessor_types == ["deterministic"]
    failed = policy.decide(result(obs=FAILING["max_abs_lateral"]), True)
    assert failed.status == F and failed.reason.startswith("Failed: ")
    assert [f["check"] for f in failed.failures] == ["hallway-lateral-limit"]
    assert policy.decide(result(), False) == evaluate.Decision(
        "r1", U, "Evidence missing or digest mismatch", [], ["deterministic"]
    )
    # A failure is never hidden by missing evidence.
    assert policy.decide(result(obs=FAILING["max_abs_lateral"]), False).status == F


def _f(check, t, **extra):
    return hallway.failure(check, 1.0, 0.5, "m", t, 4, **extra)


def test_primary_failure_follows_the_category_precedence_not_the_clock():
    """cv #36: deterministic primary category; every other failure kept as secondary."""
    lat, wall = _f("hallway-lateral-limit", 10.6), _f("hallway-wall-contact", 10.3, wall="w")
    exit_, fall = _f("hallway-exit-reached", 9.0, via="wall-left"), _f("hallway-fall", 11.0)
    # A contact timed before the lateral maximum still ranks after it (chronology only).
    assert evaluate.ranked_failures([exit_, wall, lat]) == [lat, wall, exit_]
    assert evaluate.ranked_failures([lat, fall]) == [fall, lat]
    # Within a category the earliest time first, then recorded order; untimed last.
    w1, w2, w0 = (
        _f("hallway-wall-contact", t, wall=w) for t, w in ((11.1, "a"), (10.6, "b"), (None, "c"))
    )
    assert evaluate.ranked_failures([w0, w1, w2]) == [w2, w1, w0]
    # Checks outside the precedence (recorded reviews) follow, by name.
    human = {**_f("z-review", 1.0), "check": "z-review"}
    assert evaluate.ranked_failures([human, wall]) == [wall, human]
    assert evaluate.ranked_failures([]) == []


def test_decision_names_one_primary_failure_and_keeps_the_rest():
    policy = evaluate.AllRequiredV1()
    obs = observation(
        max_abs_lateral={"value": 0.62, "time": 10.1},
        wall_contacts=[{"wall": "wall-left", "start": 9.5, "end": 10.7, "depth": 0.032}],
    )
    failed = policy.decide(result(obs=obs), True).to_json()
    assert failed["status"] == F
    assert failed["primary_failure"]["check"] == "hallway-lateral-limit"
    assert [f["check"] for f in failed["secondary_failures"]] == ["hallway-wall-contact"]
    # Additive: ``failures`` is unchanged and still holds every failure, in recorded order.
    assert [f["check"] for f in failed["failures"]] == [
        "hallway-lateral-limit",
        "hallway-wall-contact",
    ]
    assert sorted([failed["primary_failure"], *failed["secondary_failures"]], key=str) == sorted(
        failed["failures"], key=str
    )
    passed = policy.decide(result(), True).to_json()
    assert passed["primary_failure"] is None and passed["secondary_failures"] == []
    # Grades are unchanged: status and reason do not depend on the ranking.
    for o in FAILING.values():
        d = policy.decide(result(obs=o), True)
        assert d.status == F and d.to_json()["primary_failure"] == d.failures[0]


def test_policy_incompatible_cases():
    policy = evaluate.AllRequiredV1()
    declared = result(compatibility={"status": X, "scope": "execution", "reason": "no camera"})
    assert policy.decide(declared, True) == evaluate.Decision("r1", X, "no camera")
    with pytest.raises(ValueError):
        policy.decide(result(compatibility={"status": X, "scope": "run", "reason": "x"}), True)
    unknown = result()
    unknown["spec"]["commit"] = "f" * 40
    assert policy.decide(unknown, True).reason == "Unsupported walk-hallway contract"
    version = result()
    version["assessments"][0]["version"] = "9.9.9"
    assert policy.decide(version, True).status == X
    impostor = result()
    impostor["assessments"][0]["kind"] = "human"
    impostor["assessments"][0]["provenance"]["assessor"] = {"type": "human"}
    assert policy.decide(impostor, True).status == X


def test_policy_missing_member_is_insufficient():
    r = result()
    r["assessments"].pop()
    d = evaluate.AllRequiredV1().decide(r, True)
    assert (d.status, d.reason) == (U, "Missing, duplicate or insufficient required assessment")


def test_retained_human_assessment_is_judged_with_the_rest():
    """The seam assess --human will use: append a recorded conclusion, decide again."""
    r = result()
    human = {
        "id": "r1/human-review",
        "evaluator": "human-review",
        "kind": "human",
        "version": "1.0.0",
        "status": F,
        "rationale": "robot brushed the wall",
        "mandatory": False,
        "evidence_ids": ["r1"],
        "provenance": {"assessor": {"type": "human", "id": "reviewer"}, "details": {}},
    }
    r["assessments"].append(human)
    d = evaluate.AllRequiredV1().decide(r, True)
    assert d.status == F and d.assessor_types == ["deterministic", "human"]
    assert "robot brushed the wall" in d.reason


@pytest.mark.parametrize(
    "statuses, plan, grade, n",
    [
        ([P] * 18 + [F] * 2, PLAN, P, 20),
        ([P] * 17 + [F] * 3, PLAN, F, 20),
        ([P] * 17 + [U] + [F] * 2, PLAN, U, 20),
        ([P] * 19, PLAN, U, 20),  # a missing trial counts as not passed
        ([P] * 19 + [X], PLAN, X, 20),
        ([P] * 3, {}, U, 3),  # no usable plan: N is what was recorded
        ([P] * 3, dict(PLAN, required_passes=None), U, 20),
        ([P] * 3, dict(PLAN, trials="20"), U, 3),
    ],
)
def test_aggregator_matches_reporter_rules(statuses, plan, grade, n):
    got, planned, rationale = evaluate.Aggregator().grade(statuses, plan)
    assert (got, planned) == (grade, n) and rationale


def test_aggregator_groups_by_run_and_reports_wilson():
    policy, agg = evaluate.AllRequiredV1(), evaluate.Aggregator()
    results = [result(f"a{i}") for i in range(20)] + [result("b0", run_id="run-2")]
    results[0] = result("a0", obs=FAILING["min_base_height"])
    decisions = [policy.decide(r, True) for r in results]
    runs = agg.aggregate(results, decisions)
    assert [(r["run_id"], r["k"], r["n"], r["grade"]) for r in runs] == [
        ("run-1", 19, 20, P),
        ("run-2", 1, 20, U),
    ]
    assert runs[0]["interval"] == {
        "method": "wilson",
        "level": 0.95,
        "lower": 0.7639,
        "upper": 0.9911,
    }
    assert evaluate.as_reporter_summary(runs[0])["wilson95"] == [0.7639, 0.9911]
    assert [f["check"] for f in runs[0]["failures"]] == ["hallway-fall"]


def test_bundle_1_0_0_is_unchanged_and_1_1_0_carries_the_assessment(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "dev")
    tool = _tool()
    manifest = _manifest(tmp_path / "ev", failing_seed=3)
    old = tool.export(manifest, tmp_path / "old", prototype=True)
    new = tool.export(manifest, tmp_path / "new", prototype=True, bundle_version="1.1.0")
    assert old == new
    a = json.loads((tmp_path / "old/bundle/manifest.json").read_text())
    b = json.loads((tmp_path / "new/bundle/manifest.json").read_text())
    assert a["schema_version"] == "1.0.0" and "assessment" not in a
    assert b["schema_version"] == "1.1.0"
    assessment = b.pop("assessment")
    stripped = copy.deepcopy(b)
    for r in stripped["results"]:
        decision = r.pop("decision")
        assert decision["policy"] == hallway.POLICY
    stripped["schema_version"] = "1.0.0"
    assert stripped == a
    assert assessment["format"] == evaluate.FORMAT
    assert assessment["policy"] == hallway.POLICY
    assert assessment["aggregation"] == hallway.AGGREGATION
    [run] = assessment["runs"]
    summary = old["summary"]
    assert (run["k"], run["n"], run["required_passes"], run["grade"]) == (
        summary["k"],
        summary["n"],
        summary["required_passes"],
        summary["grade"],
    )
    assert [run["interval"]["lower"], run["interval"]["upper"]] == summary["wilson95"]
    assert [d["status"] for d in assessment["decisions"]] == [t["status"] for t in old["trials"]]


def test_bundle_1_1_0_marks_tampered_evidence_insufficient(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "dev")
    tool = _tool()
    manifest = _manifest(tmp_path / "ev")
    (tmp_path / "ev/trial-05.json").write_text("{}")
    tool.export(manifest, tmp_path / "out", prototype=True, bundle_version="1.1.0")
    b = json.loads((tmp_path / "out/bundle/manifest.json").read_text())
    statuses = [d["status"] for d in b["assessment"]["decisions"]]
    assert statuses.count(U) == 1 and statuses.count(P) == 19
    run = b["assessment"]["runs"][0]
    assert (run["k"], run["grade"]) == (19, P)  # 19 >= 18: one unsure trial cannot change it


def test_unknown_bundle_version_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "dev")
    with pytest.raises(ValueError):
        _tool().export(_manifest(tmp_path / "ev"), tmp_path / "out", bundle_version="2.0.0")
    assert not (tmp_path / "out").exists()


GOLDEN = os.environ.get("LIVINGPROOF_GOLDEN")


@pytest.mark.skipif(not GOLDEN, reason="set LIVINGPROOF_GOLDEN to a golden report directory")
@pytest.mark.parametrize(
    "config",
    [
        "unitree-g1-stock",
        "unitree-g1-playground",
        "unitree-go1-playground",
        "booster-t1-stock",
        "booster-t1-playground",
    ],
)
def test_assessment_matches_golden_reporter(config, tmp_path, monkeypatch):
    """Bundle 1.1.0 decisions and runs equal the report.json capability report v1.10.0 wrote."""
    golden = Path(GOLDEN) / config
    evidence = Path(os.environ.get("LIVINGPROOF_GOLDEN_EVIDENCE", "/tmp/base")) / config
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.7.0")
    _tool().export(evidence / "manifest.json", tmp_path / "out", bundle_version="1.1.0")
    bundle = json.loads((tmp_path / "out/bundle/manifest.json").read_text())
    report = json.loads((golden / "report.json").read_text())
    spec = importlib.util.spec_from_file_location(
        "compare", ROOT / "tools" / "compare_assessment.py"
    )
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    assert compare.differences(bundle, report) == []


def test_compare_assessment_ignores_the_derived_primary_split():
    """A report.json (no primary/secondary keys) still matches the 1.1.0 assessment."""
    spec = importlib.util.spec_from_file_location("compare", ROOT / "tools/compare_assessment.py")
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    results = [result("r1", obs=FAILING["max_abs_lateral"]), result("r2")]
    a = evaluate.assess_bundle(results, {"r1": True, "r2": True})
    report = {
        "decisions": [
            {k: v for k, v in d.items() if k not in compare.DERIVED} for d in a["decisions"]
        ],
        "summaries": [evaluate.as_reporter_summary(r) for r in a["runs"]],
    }
    assert compare.differences({"assessment": a}, report) == []
    report["decisions"][0]["status"] = P
    assert compare.differences({"assessment": a}, report) == ["decisions differ"]
