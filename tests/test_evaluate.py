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
        "All required evaluators and every retained assessment pass"
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
    unknown["spec"]["tag"] = "v9.9.9"
    assert policy.decide(unknown, True).reason == "Unsupported scenario contract"
    # A released tag is the identity. The recorded commit and revision are not compared.
    retagged = result()
    retagged["spec"]["tag"] = "v2.2.0"
    retagged["spec"]["commit"] = "f" * 40
    assert policy.decide(retagged, True).status == P
    matched = result()
    matched["spec"]["tag"] = "v2.2.0"
    matched["spec"]["commit"] = "23bec516bda29be7b26555286cbf2e8217ac5c81"
    assert policy.decide(matched, True).status == P
    matched["spec"]["revision"] = "9.9.9"
    assert policy.decide(matched, True).status == P
    del matched["spec"]["revision"]
    assert policy.decide(matched, True).status == P
    version = result()
    version["assessments"][0]["version"] = "9.9.9"
    assert policy.decide(version, True).status == X
    impostor = result()
    impostor["assessments"][0]["kind"] = "human"
    impostor["assessments"][0]["provenance"]["assessor"] = {"type": "human"}
    assert policy.decide(impostor, True).status == X


@pytest.mark.parametrize(
    "commit",
    [
        "b4ba31eb2c7eaca2721d68d5d375260e2a114449",  # pre-merge candidate
        "a378e92216fd9498984f290e677b9f4e7a33232c",  # specs #25 candidate
    ],
)
def test_placeholder_v0_0_0_is_incompatible_and_says_why(commit):
    """An unreleased catalog writes tag v0.0.0. The grade stays INCOMPATIBLE, and the reason says so."""
    prototype = result()
    prototype["spec"]["tag"] = "v0.0.0"
    prototype["spec"]["commit"] = commit
    decision = evaluate.AllRequiredV1().decide(prototype, True)
    assert decision.status == X
    assert decision.reason == "tag v0.0.0 is a placeholder, not a release"


def test_placeholder_tag_from_another_repository_names_the_repository():
    """Tag v0.0.0 from another repository is an unsupported contract."""
    r = result()
    r["spec"]["repository"] = "https://github.com/example/specs"
    r["spec"]["tag"] = "v0.0.0"
    decision = evaluate.AllRequiredV1().decide(r, True)
    assert decision.status == X
    assert decision.reason == "Unsupported scenario contract"


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
        # Evidence 2.0.0: trials.count, no required_passes. The threshold is N.
        ([P, P, P], {"command_speed": 0.5, "trials": {"count": 3}}, P, 3),
        ([P, F], {"trials": {"count": 2}}, F, 2),
        ([P], {"trials": {"count": 3}}, U, 3),
        ([P, P, P], {"trials": {"count": True}}, U, 3),
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


def test_run_summary_counts_each_failed_trial_once_and_keeps_the_worst_values():
    """cv #36: failure summary by primary category, and worst value per check with its seed."""
    drift_and_contact = observation(
        max_abs_lateral={"value": 0.62, "time": 10.1},
        wall_contacts=[
            {"wall": "wall-left", "start": 9.5, "end": 9.7, "depth": 0.01},
            {"wall": "wall-left", "start": 10.2, "end": 10.7, "depth": 0.04},
        ],
    )
    obs = [observation(), drift_and_contact, FAILING["wall_contacts"], FAILING["exit_time"]]
    results = []
    for seed, o in enumerate(obs, start=1):
        r = result(f"r{seed}", obs=o, plan={**PLAN, "trials": 4, "required_passes": 3})
        r["original_result"] = {"status": "x", "seed": seed}
        results.append(r)
    run = evaluate.assess_bundle(results, {r["id"]: True for r in results})["runs"][0]
    assert run["grade"] == F and run["k"] == 1
    assert run["failure_summary"] == {
        "failed_trials": 3,
        "by_primary": [
            {"check": "hallway-lateral-limit", "count": 1, "seeds": [2], "result_ids": ["r2"]},
            {"check": "hallway-wall-contact", "count": 1, "seeds": [3], "result_ids": ["r3"]},
            {"check": "hallway-exit-reached", "count": 1, "seeds": [4], "result_ids": ["r4"]},
        ],
    }
    worst = {w["check"]: w for w in run["worst"]}
    assert worst["hallway-lateral-limit"] == {
        "check": "hallway-lateral-limit", "worst": "max", "unit": "m",
        "observed": 0.62, "seed": 2, "t": 10.1, "result_id": "r2",
    }  # fmt: skip
    deep = worst["hallway-wall-contact"]
    assert (deep["observed"], deep["seed"], deep["t"], deep["wall"]) == (0.04, 2, 10.2, "wall-left")
    assert (worst["hallway-fall"]["observed"], worst["hallway-fall"]["seed"]) == (0.71, 1)
    assert worst["hallway-exit-reached"]["missing"] == 1
    # Not part of the reporter's summary shape (its 1.1.0 check reads only RUN keys).
    summary = evaluate.as_reporter_summary(run)
    assert "failure_summary" not in summary and "worst" not in summary


def test_run_summary_with_no_failures_or_contacts():
    run = evaluate.assess_bundle([result()], {"r1": True})["runs"][0]
    assert run["failure_summary"] == {"failed_trials": 0, "by_primary": []}
    contact = next(w for w in run["worst"] if w["check"] == "hallway-wall-contact")
    assert contact["observed"] is None and contact["seed"] is None


def _pause_and_evidence():
    """A real pause result and the real compared-repeat record. No hallway inputs."""
    pause = json.loads((ROOT / "examples/execution-matrix/manifest.json").read_text())
    evidence = json.loads(
        (ROOT / "tests/fixtures/hallway/evidence-2.0.0/manifest.json").read_text()
    )
    base = next(
        item
        for item in pause["results"]
        if item["spec"]["capability"] == "pause" and item["original_result"]["status"] == P
    )
    return base, evidence


def _pause_results(base, evidence, record):
    results = []
    for trial in evidence["trials"]["results"]:
        provenance = dict(base["provenance"])
        provenance["plan"] = evidence["plan"]
        provenance["reproducibility"] = record
        provenance["repeat_records"] = evidence["trials"]["results"]
        results.append(
            {
                "id": f"{base['id']}-{trial['index']}",
                "spec": base["spec"],
                "system": base["system"],
                "environment": base["environment"],
                "assessments": base["assessments"],
                "provenance": provenance,
            }
        )
    return results


def test_compared_repeats_aggregate_for_a_pause_capability():
    """The repeat rule uses the evidence record, not a hallway or robot input."""
    base, evidence = _pause_and_evidence()
    count = evidence["trials"]["count"]
    record = evidence["trials"]["reproducibility"]
    results = _pause_results(base, evidence, record)
    status = base["original_result"]["status"]
    decisions = [evaluate.Decision(item["id"], status, status) for item in results]
    [run] = evaluate.Aggregator().aggregate(results, decisions)
    statement = f"{count} of {count} repeats agree"
    assert run["capability"] == "pause"
    assert run["grade"] == status
    assert run["rationale"] == statement
    assert run["reproducibility"]["repeats"] == count
    assert run["reproducibility"]["agreeing"] == count
    assert run["reproducibility"]["statement"] == statement
    assert run["result_ids"] == [item["id"] for item in results]
    assert run["k"] is None and run["n"] is None and run["required_passes"] is None
    assert run["interval"]["lower"] is None and run["interval"]["upper"] is None
    assert "hallway" not in json.dumps(run["reproducibility"])
    assert "failure_summary" not in run and "worst" not in run
    summary = evaluate.as_reporter_summary(run)
    assert summary["k"] is None and summary["wilson95"] == [None, None]
    assert run["reproducibility"]["statement"] == statement

    disagree = {
        "compared": True,
        "identical": False,
        "finding": {
            "kind": "test-system",
            "name": "reproducibility",
            "detail": (
                "Identical trials disagreed on trajectory hash or outcome. "
                "This is a test-system reproducibility finding, not a robot FAIL."
            ),
            "trajectory_sha256": [
                trial["trajectory_sha256"] for trial in evidence["trials"]["results"]
            ],
            "outcomes": [trial["outcome"] for trial in evidence["trials"]["results"]],
        },
    }
    disagreed = _pause_results(base, evidence, disagree)
    decisions = [evaluate.Decision(item["id"], status, status) for item in disagreed]
    [run] = evaluate.Aggregator().aggregate(disagreed, decisions)
    assert run["grade"] != P
    assert run["grade"] == U
    assert run["reproducibility"]["finding"] == disagree["finding"]
    assert [row["index"] for row in run["reproducibility"]["differing"]] == [
        trial["index"] for trial in evidence["trials"]["results"]
    ]
    assert run["k"] is None and run["n"] is None
    assert run["interval"]["lower"] is None and run["interval"]["upper"] is None
    assert "agreeing" not in run["reproducibility"]


def test_a_mixed_bundle_keeps_seeded_k_of_n_beside_compared_repeats():
    """Older seeded results stay k of N when a compared-repeat run is in the same bundle."""
    playground = json.loads(
        (ROOT / "tests/fixtures/g1-playground-bundle/manifest.json").read_text()
    )
    seeded = []
    complete = {}
    for item in playground["results"]:
        row = {key: value for key, value in item.items() if key != "decision"}
        seeded.append(row)
        complete[row["id"]] = True
    base, evidence = _pause_and_evidence()
    compared = _pause_results(base, evidence, evidence["trials"]["reproducibility"])
    for row in compared:
        complete[row["id"]] = True
    assessment = evaluate.assess_bundle(seeded + compared, complete)
    runs = {run["capability"]: run for run in assessment["runs"]}
    hallway_run = runs["walk-hallway"]
    assert (hallway_run["k"], hallway_run["n"], hallway_run["required_passes"]) == (3, 3, 3)
    assert hallway_run["interval"] == {
        "method": "wilson",
        "level": 0.95,
        "lower": 0.4385,
        "upper": 1.0,
    }
    assert hallway_run["rationale"] == "3/3 PASS, needs 3"
    assert "reproducibility" not in hallway_run
    pause_run = runs["pause"]
    count = evidence["trials"]["count"]
    statement = f"{count} of {count} repeats agree"
    assert pause_run["k"] is None and pause_run["n"] is None
    assert pause_run["interval"]["lower"] is None and pause_run["interval"]["upper"] is None
    assert pause_run["rationale"] == statement
    assert pause_run["reproducibility"]["statement"] == statement


@pytest.mark.parametrize(
    "change",
    [
        {"commit": None},
        {"commit": "0bdf1db"},
        {"commit": "0BDF1DB3F975B044692C0B8F1EC37737AD8B889F"},
        {"revision": None},
    ],
)
def test_contract_commit_or_revision_mismatch_is_accepted(change):
    """A released tag is accepted when the commit or revision does not match."""
    r = result()
    r["spec"].update(change)
    assert evaluate.AllRequiredV1().decide(r, True).status == P


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"tag": "v9.9.9"}, "Unsupported scenario contract"),
        ({"tag": None}, "Unsupported scenario contract"),
        ({"repository": "https://github.com/example/specs"}, "Unsupported scenario contract"),
    ],
)
def test_contract_identity_edge_cases_are_incompatible(change, reason):
    """An unknown tag, a null tag, or another repository is an unsupported contract."""
    r = result()
    r["spec"].update(change)
    decision = evaluate.AllRequiredV1().decide(r, True)
    assert decision.status == X
    assert decision.reason == reason


def test_contract_commit_missing_entirely_is_accepted():
    r = result()
    del r["spec"]["commit"]
    assert evaluate.AllRequiredV1().decide(r, True).status == P


def test_contract_revision_absent_with_matching_commit_is_graded():
    r = result()
    del r["spec"]["revision"]
    assert evaluate.AllRequiredV1().decide(r, True).status == P


@pytest.mark.parametrize(
    "tag, commit",
    [
        ("v1.6.0", "34681d20bf13ba0a836e03ac09869c7fb13665f1"),
        ("v1.6.1", "0bdf1db3f975b044692c0b8f1ec37737ad8b889f"),
        ("v1.9.0", "d677bd9770e60a6d42ea597966799e119dc7dd18"),
        ("v2.2.0", "23bec516bda29be7b26555286cbf2e8217ac5c81"),
    ],
)
def test_every_released_tag_grades_with_its_own_commit(tag, commit):
    """The tag's own commit is graded, and so is a different commit and revision."""
    r = result()
    r["spec"]["tag"], r["spec"]["commit"] = tag, commit
    assert evaluate.AllRequiredV1().decide(r, True).status == P
    r["spec"]["commit"] = "f" * 40
    r["spec"]["revision"] = "9.9.9"
    assert evaluate.AllRequiredV1().decide(r, True).status == P


@pytest.mark.parametrize(
    "change",
    [{"commit": "f" * 40}, {"revision": "9.9.9"}],
)
def test_a_commit_or_revision_mismatch_is_accepted_as_a_run(change):
    """A tag identifies the contract, so a commit or revision mismatch is still graded."""
    plan = {"trials": {"count": 2}}
    results = [result("r1", plan=plan), result("r2", plan=plan)]
    for row in results:
        row["spec"].update(change)
    assessment = evaluate.assess_bundle(results, {"r1": True, "r2": True})
    assert {decision["status"] for decision in assessment["decisions"]} == {P}
    [run] = assessment["runs"]
    assert run["grade"] == P
    assert run["rationale"] == "2/2 PASS, needs 2"


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "https://github.com/example/specs"},
        {"tag": "v0.0.0"},
    ],
)
def test_a_rejected_contract_is_still_an_incompatible_run(change):
    """A wrong repository or the placeholder tag is an INCOMPATIBLE run, not left out."""
    results = [result("r1"), result("r2")]
    for r in results:
        r["spec"].update(change)
    a = evaluate.assess_bundle(results, {"r1": True, "r2": True})
    assert {d["status"] for d in a["decisions"]} == {X}
    assert [run["grade"] for run in a["runs"]] == [X]
    assert a["runs"]


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "https://github.com/example/specs"},
        {"tag": "v0.0.0"},
    ],
)
def test_rejected_contract_run_counts_incompatible_results(change):
    """v1.19.0 reported the run as N result(s) INCOMPATIBLE, without trial detail."""
    results = [result("r1"), result("r2")]
    for row in results:
        row["spec"].update(change)
    [run] = evaluate.assess_bundle(results, {"r1": True, "r2": True})["runs"]
    assert run["grade"] == X
    assert run["rationale"] == "2 result(s) INCOMPATIBLE"
    assert "failure_summary" not in run
    assert "worst" not in run


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "https://github.com/example/specs"},
        {"tag": "v0.0.0"},
    ],
)
def test_one_uncompared_trial_with_a_rejected_contract_is_an_incompatible_run(change):
    """The one-trial default is k of N. A rejected contract is still that run."""
    trial = result("r1", plan={"trials": {"count": 1}})
    trial["spec"].update(change)
    trial["provenance"]["reproducibility"] = {
        "compared": False,
        "identical": None,
        "finding": None,
    }
    [run] = evaluate.assess_bundle([trial], {"r1": True})["runs"]
    assert run["grade"] == X
    assert run["rationale"] == "1 result(s) INCOMPATIBLE"
    assert "reproducibility" not in run


def test_pause_results_without_compared_repeats_are_not_runs():
    """Pause names the specs repository. It is a run only when repeats were compared."""
    pause = json.loads((ROOT / "examples/execution-matrix/manifest.json").read_text())
    results = [item for item in pause["results"] if item["spec"]["capability"] == "pause"]
    assert results
    assessment = evaluate.assess_bundle(results, {item["id"]: True for item in results})
    assert assessment["runs"] == []


@pytest.mark.parametrize("plan", [PLAN, {"trials": {"count": 1}}])
def test_an_unknown_tag_is_an_incompatible_run(plan):
    """A walk-hallway result with an unreleased tag is reported as INCOMPATIBLE, not left out."""
    trial = result("r1", plan=plan)
    trial["spec"]["tag"] = "v9.9.9"
    a = evaluate.assess_bundle([trial], {"r1": True})
    assert [d["status"] for d in a["decisions"]] == [X]
    assert [run["grade"] for run in a["runs"]] == [X]
