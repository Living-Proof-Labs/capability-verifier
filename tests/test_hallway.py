# SPDX-License-Identifier: Apache-2.0
"""Walk-hallway evaluators bound to the locked catalog's verifier scenarios."""

import copy
import importlib.util
import os
import hashlib
import json
import re
import shutil
import signal
import subprocess
import sys
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


@given(
    parsers.parse("{trials:d} trial results of one configuration, of which {passed:d} pass"),
    target_fixture="trial_set",
)
def results(trials, passed):
    return [{"status": hallway.PASS}] * passed + [{"status": hallway.FAIL}] * (trials - passed)


@when("the trial results are aggregated", target_fixture="agg")
def aggregated(trial_set):
    return hallway.aggregate(trial_set, PLAN)


@then(
    parsers.parse(
        "the result reports {passed:d} of {trials:d} with Wilson 95% interval {low:f} to {high:f}"
    )
)
def wilson(agg, passed, trials, low, high):
    assert (agg["k"], agg["n"]) == (passed, trials)
    assert agg["wilson95"] == [low, high]


@then(parsers.parse("the grade is {grade} because at least {required_passes:d} trials must pass"))
def grade(agg, grade, required_passes):
    assert PLAN["required_passes"] == required_passes
    assert agg["grade"] == grade


def test_insufficient_evidence_blocks_a_pass_it_could_change():
    """Not in the catalog's Examples: 17 PASS + 1 INSUFFICIENT + 2 FAIL of 20 (needs 18)."""
    ok = {"status": hallway.PASS}
    unsure = [ok] * 17 + [{"status": hallway.INSUFFICIENT}] + [{"status": hallway.FAIL}] * 2
    assert hallway.aggregate(unsure, PLAN)["grade"] == hallway.INSUFFICIENT


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
    with pytest.raises(hallway.ManifestError, match="do not match plan.seeds"):
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


@pytest.mark.parametrize(
    "drop, field",
    [
        (("plan",), "plan"),
        (("plan", "required_passes"), "plan.required_passes"),
        (("robot", "id"), "robot.id"),
        (("world", "id"), "world.id"),
        (("configuration", "name"), "configuration.name"),
        (("trials", 0, "sha256"), "trials[0].sha256"),
    ],
)
def test_malformed_manifest_names_the_field(tmp_path, drop, field):
    path = _manifest(tmp_path)
    m = json.loads(path.read_text())
    node = m
    for key in drop[:-1]:
        node = node[key]
    del node[drop[-1]]
    path.write_text(json.dumps(m))
    with pytest.raises(hallway.ManifestError, match=rf"'{re.escape(field)}' is missing"):
        hallway.verify(path)


def test_mistyped_manifest_field_is_named(tmp_path):
    path = _manifest(tmp_path)
    m = json.loads(path.read_text())
    m["plan"]["trials"] = "20"
    path.write_text(json.dumps(m))
    with pytest.raises(hallway.ManifestError, match="'plan.trials' has the wrong type"):
        hallway.verify(path)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_tool", Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_export_writes_nothing_when_the_manifest_is_malformed(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    src = tmp_path / "evidence"
    src.mkdir()
    path = _manifest(src)
    m = json.loads(path.read_text())
    del m["plan"]
    path.write_text(json.dumps(m))
    out = tmp_path / "verified"
    with pytest.raises(hallway.ManifestError, match="'plan' is missing"):
        _tool().export(path, out, prototype=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["evidence"]  # no out, no staging


def test_export_moves_a_complete_bundle_into_place(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    src = tmp_path / "evidence"
    src.mkdir()
    out = tmp_path / "verified"
    _tool().export(_manifest(src), out, prototype=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["evidence", "verified"]
    bundle = json.loads((out / "bundle" / "manifest.json").read_text())
    assert len(bundle["results"]) == PLAN["trials"]
    with pytest.raises(FileExistsError):
        _tool().export(_manifest(src), out, prototype=True)


def test_missing_trials_use_the_planned_n():
    """Same rule as the capability report: N and Wilson come from the plan."""
    trials = [{"status": hallway.PASS}] * 18
    s = hallway.aggregate(trials, PLAN)
    assert (s["k"], s["n"], s["grade"]) == (18, 20, hallway.INSUFFICIENT)
    assert s["wilson95"] == hallway.wilson(18, 20) == [0.6990, 0.9721]


def _cli(tmp_path, manifest, out, version, commit="0" * 40):
    """Run the tool from a copied tree with no .git, as in the image."""
    tree = tmp_path / "tree"
    if not tree.exists():
        shutil.copytree(Path(__file__).parents[1] / "tools", tree / "tools")
        for name in ("hallway-specs.lock.json", "pyproject.toml"):
            shutil.copy(Path(__file__).parents[1] / name, tree)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("LIVINGPROOF_VERIFIER_VERSION", "SOURCE_COMMIT")
    }
    env.update(
        GIT_CEILING_DIRECTORIES=str(tmp_path), PYTHONPATH=str(Path(__file__).parents[1] / "src")
    )
    if version is not None:
        env["LIVINGPROOF_VERIFIER_VERSION"] = version
    if commit is not None:
        env["SOURCE_COMMIT"] = commit
    assert not (tree / ".git").exists()
    return subprocess.run(
        [sys.executable, "tools/verify_hallway.py", str(manifest), str(out), "--prototype"],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
    )


def test_export_needs_no_git_and_labels_release_and_dev_builds(tmp_path):
    src = tmp_path / "evidence"
    src.mkdir()
    manifest = _manifest(src)
    r = _cli(tmp_path, manifest, tmp_path / "release", "v1.4.2")
    assert r.returncode == 0, r.stderr
    bundle = json.loads((tmp_path / "release/bundle/manifest.json").read_text())
    assert bundle["producer"] == {
        "id": "capability-verifier",
        "version": "1.4.2",
        "commit": "0" * 40,
    }
    assert all("producer" not in res["provenance"] for res in bundle["results"])

    r = _cli(tmp_path, manifest, tmp_path / "dev", "dev", "a" * 40)
    assert r.returncode == 0, r.stderr
    bundle = json.loads((tmp_path / "dev/bundle/manifest.json").read_text())
    assert bundle["producer"]["version"] == "0.0.0" and bundle["producer"]["commit"] == "a" * 40
    notes = {res["provenance"]["producer"] for res in bundle["results"]}
    assert notes == {
        f"DEV: capability-verifier unreleased build at {'a' * 40}; "
        "producer version 0.0.0 is a placeholder, not a release"
    }


@pytest.mark.parametrize(
    "version, commit, message",
    [
        (None, "0" * 40, "LIVINGPROOF_VERIFIER_VERSION is unset"),
        ("", "0" * 40, "LIVINGPROOF_VERIFIER_VERSION is unset"),
        ("main", "0" * 40, "neither X.Y.Z nor 'dev'"),
        ("1.4.2-3-gabc", "0" * 40, "neither X.Y.Z nor 'dev'"),
        ("dev", None, "SOURCE_COMMIT is unset and there is no git checkout"),
    ],
)
def test_export_fails_loudly_without_an_honest_producer_label(tmp_path, version, commit, message):
    src = tmp_path / "evidence"
    src.mkdir()
    out = tmp_path / "out"
    r = _cli(tmp_path, _manifest(src), out, version, commit)
    assert r.returncode != 0 and message in r.stderr
    assert "Traceback" not in r.stderr
    assert not out.exists() and not list(tmp_path.glob(".out.*"))


def _corrupt_json(m):
    m.write_text("{not json")


def _plan_mismatch(m):
    doc = json.loads(m.read_text())
    doc["plan"] = dict(doc["plan"], seeds=list(range(2, 22)))
    m.write_text(json.dumps(doc))


def _bad_trial_file(m):
    doc = json.loads(m.read_text())
    trial = m.parent / doc["trials"][0]["path"]
    trial.write_text("{truncated")
    doc["trials"][0]["sha256"] = hashlib.sha256(trial.read_bytes()).hexdigest()
    m.write_text(json.dumps(doc))


@pytest.mark.parametrize(
    "break_it, message",
    [
        (_corrupt_json, "manifest is not valid JSON"),
        (_plan_mismatch, "do not match plan.seeds"),
        (_bad_trial_file, "is not valid JSON"),
    ],
)
def test_bad_evidence_is_a_manifest_error_with_no_traceback(tmp_path, break_it, message):
    src = tmp_path / "evidence"
    src.mkdir()
    manifest = _manifest(src)
    break_it(manifest)
    with pytest.raises(hallway.ManifestError, match=message):
        hallway.verify(manifest)
    out = tmp_path / "out"
    r = _cli(tmp_path, manifest, out, "1.4.2")
    assert r.returncode != 0
    assert r.stderr.startswith("invalid evidence manifest") and message in r.stderr
    assert "Traceback" not in r.stderr
    assert not out.exists() and not list(tmp_path.glob(".out.*"))


def test_sigterm_removes_the_staging_folder(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.4.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    src = tmp_path / "evidence"
    src.mkdir()
    manifest = _manifest(src)

    def killed(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(tool.hallway, "verify", killed)
    with pytest.raises(SystemExit) as err:
        tool.export(manifest, tmp_path / "out")
    assert err.value.code == 128 + signal.SIGTERM
    assert not (tmp_path / "out").exists() and not list(tmp_path.glob(".out.*"))
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_task_labels_only_an_exact_release_tag(tmp_path):
    """Taskfile: X.Y.Z only at the tagged commit; one commit later it is dev."""
    text = (Path(__file__).parents[1] / "Taskfile.yml").read_text()
    cmd = re.search(r"LIVINGPROOF_VERIFIER_VERSION:\n(?:\s*#.*\n)?\s*sh: '(.*)'\n", text).group(1)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(tmp_path)]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "a"], check=True)
    subprocess.run([*git, "tag", "v1.4.2"], check=True)
    env = {k: v for k, v in os.environ.items() if k != "LIVINGPROOF_VERIFIER_VERSION"}

    def label():
        return subprocess.run(
            ["sh", "-c", cmd], cwd=tmp_path, env=env, capture_output=True, text=True, check=True
        ).stdout.strip()

    assert label() == "1.4.2"
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "b"], check=True)
    assert label() == "dev"
