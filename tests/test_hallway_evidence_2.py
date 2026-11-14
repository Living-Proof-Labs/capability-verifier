# SPDX-License-Identifier: Apache-2.0
"""Evidence 2.0.0 and media 2.0.0, plus a clean refusal of any format this verifier does not know."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from capability_verifier import evaluate, hallway

FIXTURES = Path(__file__).parent / "fixtures" / "hallway"
EVIDENCE = FIXTURES / "evidence-2.0.0"
WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "scenario.yml"
FINDING = {
    "kind": "test-system",
    "name": "reproducibility",
    "detail": (
        "Identical trials disagreed on trajectory hash or outcome. "
        "This is a test-system reproducibility finding, not a robot FAIL."
    ),
    "trajectory_sha256": ["a" * 64, "b" * 64],
    "outcomes": ["crossed_exit", "crossed_exit"],
}


def _load():
    return json.loads((EVIDENCE / "manifest.json").read_text())


def _copy(tmp_path, manifest=None):
    dest = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, dest)
    if manifest is not None:
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return dest / "manifest.json"


def _rewrite(path, change):
    obs = json.loads(path.read_text())
    change(obs)
    body = (json.dumps(obs, separators=(",", ":")) + "\n").encode()
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_fixture_matches_the_producer_trial_record():
    """The committed fixture uses the producer's file bytes and trial-result keys."""
    manifest = _load()
    assert manifest["format"] == "livingproof-hallway-evidence/2.0.0"
    assert manifest["plan"] == {"command_speed": 0.5, "trials": {"count": 3}}
    assert "seeds" not in manifest["plan"] and "required_passes" not in manifest["plan"]
    report = manifest["trials"]
    assert report["count"] == 3 and report["reproducibility"]["identical"] is True
    assert report["reproducibility"]["finding"] is None
    for trial in report["results"]:
        raw = (EVIDENCE / trial["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == trial["sha256"]
        assert trial["path"] == f"trials/trial-{trial['index']:02d}.json"
        assert set(trial) == {
            "index",
            "path",
            "sha256",
            "trajectory_sha256",
            "outcome",
            "wall_clock_s",
        }
        assert trial["outcome"] == "crossed_exit"


def test_evidence_2_0_0_grades_every_identical_trial(tmp_path):
    result = hallway.verify(_copy(tmp_path))
    assert [t["index"] for t in result["trials"]] == [1, 2, 3]
    assert [t["id"] for t in result["trials"]] == [
        "stock-seed-01",
        "stock-seed-02",
        "stock-seed-03",
    ]
    assert "seed" not in result["trials"][0]
    # The stored lateral value is inside the limit and the trace is not, so each
    # trial is INSUFFICIENT_EVIDENCE. The repeats still agree with each other.
    assert all(t["status"] == hallway.INSUFFICIENT for t in result["trials"])
    summary = result["summary"]
    count = _load()["trials"]["count"]
    statement = f"{count} of {count} repeats agree"
    assert summary["grade"] == hallway.INSUFFICIENT
    assert summary["rationale"] == statement
    assert summary["reproducibility"] == {
        "compared": True,
        "identical": True,
        "repeats": count,
        "agreeing": count,
        "statement": statement,
        "finding": None,
    }
    assert summary["k"] is None and summary["n"] is None
    assert summary["required_passes"] is None and summary["wilson95"] == [None, None]
    assert summary["aggregation"]["id"] == "repeated-outcome"
    lines = hallway.summary_lines(result)
    assert lines[0] == f"{statement}: {hallway.INSUFFICIENT}"
    assert [line for line in lines[1:]] == [
        "  trial  1 INSUFFICIENT_EVIDENCE: "
        "observation max_abs_lateral disagrees with its trace (trial 1)",
        "  trial  2 INSUFFICIENT_EVIDENCE: "
        "observation max_abs_lateral disagrees with its trace (trial 2)",
        "  trial  3 INSUFFICIENT_EVIDENCE: "
        "observation max_abs_lateral disagrees with its trace (trial 3)",
    ]
    assert result["findings"] == []
    assert "findings" not in summary


def test_identical_repeats_are_one_bundle_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.15.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_tool_identical", Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.export(_copy(tmp_path), tmp_path / "verified", prototype=True, bundle_version="1.1.0")
    bundle = json.loads((tmp_path / "verified" / "bundle" / "manifest.json").read_text())
    report = json.loads((tmp_path / "verified" / "verification.json").read_text())
    run = bundle["assessment"]["runs"][0]
    count = _load()["trials"]["count"]
    assert run["grade"] == report["summary"]["grade"] == hallway.INSUFFICIENT
    assert run["reproducibility"] == report["summary"]["reproducibility"]
    assert run["reproducibility"]["statement"] == f"{count} of {count} repeats agree"
    assert len(run["result_ids"]) == count
    assert run["k"] is None and run["n"] is None and run["required_passes"] is None
    assert run["interval"] == {
        "method": "wilson",
        "level": 0.95,
        "lower": None,
        "upper": None,
    }
    assert bundle["assessment"]["aggregation"] == {"id": "repeated-outcome", "version": "1.0.0"}


def test_a_reproducibility_finding_is_not_a_robot_fail(tmp_path):
    dest = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, dest)
    digest = _rewrite(
        dest / "trials" / "trial-02.json",
        lambda obs: obs["max_abs_lateral"].update(value=0.22),
    )
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["trials"]["results"][1]["sha256"] = digest
    manifest["trials"]["reproducibility"] = {
        "compared": True,
        "identical": False,
        "finding": FINDING,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest) + "\n")
    result = hallway.verify(dest / "manifest.json")
    summary = result["summary"]
    assert summary["grade"] != hallway.PASS
    assert summary["grade"] == hallway.INSUFFICIENT
    assert summary["k"] is None and summary["n"] is None
    assert summary["required_passes"] is None and summary["wilson95"] == [None, None]
    assert summary["reproducibility"]["compared"] is True
    assert summary["reproducibility"]["identical"] is False
    assert summary["reproducibility"]["finding"] == FINDING
    assert summary["reproducibility"]["repeats"] == manifest["trials"]["count"]
    assert "agreeing" not in summary["reproducibility"]
    assert "statement" not in summary["reproducibility"]
    assert [row["index"] for row in summary["reproducibility"]["differing"]] == [
        trial["index"] for trial in manifest["trials"]["results"]
    ]
    assert summary["reproducibility"]["differing"][1]["sha256"] == digest
    text = "\n".join(hallway.summary_lines(result))
    assert "Wilson" not in text and "/" not in text.splitlines()[0]
    assert any(line.startswith("test-system reproducibility:") for line in text.splitlines())
    assert result["findings"] == [FINDING]
    assert summary["findings"] == [FINDING]


def test_a_failed_trial_stays_failed_and_the_run_is_not_a_robot_fail(tmp_path):
    dest = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, dest)
    digest = _rewrite(
        dest / "trials" / "trial-02.json",
        lambda obs: obs.update(max_abs_lateral={"value": 0.62, "time": 10.1}),
    )
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["trials"]["results"][1]["sha256"] = digest
    manifest["trials"]["reproducibility"] = {
        "compared": True,
        "identical": False,
        "finding": FINDING,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest) + "\n")
    result = hallway.verify(dest / "manifest.json")
    assert result["summary"]["grade"] == hallway.INSUFFICIENT
    assert result["summary"]["grade"] != hallway.FAIL
    assert result["summary"]["rationale"] == "repeats differ"
    assert result["summary"]["k"] is None and result["summary"]["wilson95"] == [None, None]
    assert result["summary"]["reproducibility"]["finding"]["kind"] == "test-system"
    assert result["findings"][0]["kind"] == "test-system"
    failed = result["trials"][1]
    assert failed["index"] == 2 and failed["status"] == hallway.FAIL
    assert failed["failures"][0]["seed"] == 2
    assert "(trial 2)" in failed["assessments"][1]["rationale"]
    assert "reproducibility" not in failed["assessments"][1]["rationale"]
    text = "\n".join(hallway.summary_lines(result))
    assert "not a robot FAIL.: FAIL" not in text
    assert text.splitlines()[0] == f"repeats differ: {hallway.INSUFFICIENT}"
    assert any(line.startswith("test-system reproducibility:") for line in text.splitlines())


def test_no_completed_trial_is_insufficient_evidence(tmp_path):
    path = _copy(tmp_path)
    for trial in path.parent.glob("trials/*.json"):
        trial.unlink()
    result = hallway.verify(path)
    count = _load()["trials"]["count"]
    statement = f"{count} of {count} repeats agree"
    assert all(t["status"] == hallway.INSUFFICIENT for t in result["trials"])
    assert result["summary"]["grade"] == hallway.INSUFFICIENT
    assert result["summary"]["grade"] != hallway.FAIL
    # Every recorded result is the same status, so the compared-repeat rule
    # labels the run as that one outcome, not a k-of-N missing-measurement rate.
    assert result["summary"]["rationale"] == statement
    assert result["summary"]["k"] is None and result["summary"]["wilson95"] == [None, None]
    assert result["summary"]["reproducibility"]["statement"] == statement


def test_a_missing_repeat_of_an_inconsistent_trace_still_agrees(tmp_path):
    """The stored lateral value disagrees with the trace, so every trial is inconclusive.

    A missing file is the same status. The repeats still agree, and the grade is not a
    robot FAIL and not a pass rate.
    """
    path = _copy(tmp_path)
    (path.parent / "trials" / "trial-02.json").unlink()
    result = hallway.verify(path)
    summary = result["summary"]
    count = _load()["trials"]["count"]
    statement = f"{count} of {count} repeats agree"
    assert [t["status"] for t in result["trials"]] == [hallway.INSUFFICIENT] * count
    assert summary["grade"] == hallway.INSUFFICIENT
    assert summary["grade"] != hallway.PASS
    assert summary["k"] is None and summary["n"] is None
    assert summary["wilson95"] == [None, None]
    assert summary["rationale"] == statement
    assert summary["reproducibility"]["agreeing"] == count
    lines = hallway.summary_lines(result)
    assert "Wilson" not in lines[0] and "/" not in lines[0]
    assert lines[0].startswith(statement)


def test_evidence_2_0_0_exports_the_finding_without_changing_the_grade(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.15.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    dest = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, dest)
    digest = _rewrite(
        dest / "trials" / "trial-03.json",
        lambda obs: obs["max_abs_lateral"].update(value=0.2),
    )
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["trials"]["results"][2]["sha256"] = digest
    manifest["trials"]["reproducibility"] = {
        "compared": True,
        "identical": False,
        "finding": FINDING,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest) + "\n")
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_tool_v2", Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.export(
        dest / "manifest.json", tmp_path / "verified", prototype=True, bundle_version="1.1.0"
    )
    report = json.loads((tmp_path / "verified" / "verification.json").read_text())
    bundle = json.loads((tmp_path / "verified" / "bundle" / "manifest.json").read_text())
    assert report["summary"]["grade"] == hallway.INSUFFICIENT
    assert report["summary"]["k"] is None and report["summary"]["wilson95"] == [None, None]
    assert report["findings"] == [FINDING]
    assert bundle["results"][0]["provenance"]["findings"] == [FINDING]
    assert bundle["results"][0]["provenance"]["reproducibility"]["identical"] is False
    assert bundle["results"][0]["original_result"] == {
        "status": hallway.INSUFFICIENT,
        "index": 1,
        "seed": 1,
    }
    run = bundle["assessment"]["runs"][0]
    assert run["grade"] == hallway.INSUFFICIENT
    assert run["k"] is None and run["n"] is None and run["required_passes"] is None
    assert run["interval"] == {
        "method": "wilson",
        "level": 0.95,
        "lower": None,
        "upper": None,
    }
    assert run["reproducibility"]["finding"] == FINDING
    assert bundle["assessment"]["aggregation"]["id"] == "repeated-outcome"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda m: m.update(format="livingproof-hallway-evidence/9.0.0"), "9.0.0"),
        (lambda m: m["plan"].update(seeds=[1, 2, 3]), "plan.seeds"),
        (lambda m: m["plan"].update(required_passes=2), "plan.required_passes"),
        (lambda m: m["plan"].update(trials=3), "plan.trials"),
        (lambda m: m.update(format=3), "wrong type"),
    ],
)
def test_unknown_or_retired_evidence_is_a_manifest_error(mutate, message):
    manifest = _load()
    mutate(manifest)
    with pytest.raises(hallway.ManifestError, match=message) as caught:
        hallway.validate_manifest(manifest)
    assert not isinstance(caught.value, KeyError)


def test_a_v2_body_with_an_unknown_format_does_not_read_seeds():
    """The old validator read plan.seeds before it looked at the format string."""
    manifest = _load()
    manifest["format"] = "livingproof-hallway-evidence/1.5.0"
    with pytest.raises(hallway.ManifestError, match="1.5.0") as caught:
        hallway.validate_manifest(manifest)
    assert "plan.seeds" not in str(caught.value)


def test_indexes_must_be_one_through_count(tmp_path):
    manifest = _load()
    manifest["trials"]["results"][2]["index"] = 4
    path = _copy(tmp_path, manifest)
    with pytest.raises(hallway.ManifestError, match="indexes"):
        hallway.verify(path)


def test_an_unmet_precondition_on_2_0_0_is_unsupported(tmp_path):
    manifest = _load()
    manifest["robot"]["profile"]["dimensions"]["width"] = 1.41
    manifest["world"]["clear_width"] = 1.5
    manifest["trials"]["results"] = []
    manifest["trials"]["reproducibility"] = {"compared": False, "identical": None, "finding": None}
    path = _copy(tmp_path, manifest)
    result = hallway.verify(path)
    assert result["summary"]["grade"] == hallway.UNSUPPORTED
    assert result["trials"] == []
    assert result["findings"] == []


def test_one_trial_is_not_a_comparison(tmp_path):
    manifest = _load()
    manifest["plan"]["trials"]["count"] = 1
    manifest["trials"]["count"] = 1
    manifest["trials"]["results"] = manifest["trials"]["results"][:1]
    manifest["trials"]["reproducibility"] = {"compared": False, "identical": None, "finding": None}
    result = hallway.verify(_copy(tmp_path, manifest))
    assert result["summary"]["grade"] == hallway.INSUFFICIENT
    assert (
        result["summary"]["k"],
        result["summary"]["n"],
        result["summary"]["required_passes"],
    ) == (
        0,
        1,
        1,
    )
    assert result["summary"]["wilson95"] == hallway.wilson(0, 1)
    assert result["summary"]["aggregation"] == {"id": "k-of-n-wilson95", "version": "1.0.0"}
    assert "reproducibility" not in result["summary"]
    low, high = result["summary"]["wilson95"]
    lines = hallway.summary_lines(result)
    assert lines[0] == (
        f"0/1 trials PASS (needs 1), Wilson 95% [{low:.4f}, {high:.4f}]: {hallway.INSUFFICIENT}"
    )
    assert lines[1:] == [
        "  trial  1 INSUFFICIENT_EVIDENCE: "
        "observation max_abs_lateral disagrees with its trace (trial 1)"
    ]
    assert result["findings"] == []


def test_media_formats_name_their_trial_key():
    assert (
        hallway.media_trial_key(json.loads((FIXTURES / "media-2.0.0.json").read_text())) == "index"
    )
    assert (
        hallway.media_trial_key(json.loads((FIXTURES / "media-1.0.0.json").read_text())) == "seed"
    )
    with pytest.raises(hallway.ManifestError, match="9.0.0") as caught:
        hallway.media_trial_key(json.loads((FIXTURES / "media-unknown.json").read_text()))
    assert not isinstance(caught.value, KeyError)


def _media_script():
    text = WORKFLOW.read_text()
    block = text[text.index("- name: Recordings") :]
    body = block[block.index("<<'PY'") :]
    body = body[body.index("\n") + 1 : body.index("\n          PY\n")]
    return "\n".join(line[10:] for line in body.splitlines()) + "\n"


def _run_media(tmp_path, name):
    media = tmp_path / "media"
    media.mkdir(parents=True)
    shutil.copy(FIXTURES / name, media / "media.json")
    done = subprocess.run(
        [sys.executable, "-c", _media_script()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    # The workflow redirects this script's stdout to GITHUB_OUTPUT. Here stdout is the script.
    return done, done.stdout


def test_the_workflow_reads_index_for_media_2_and_seed_for_media_1(tmp_path):
    script = _media_script()
    assert "livingproof-hallway-media/2.0.0" in script
    assert "livingproof-hallway-media/1.0.0" in script
    assert "t['seed']" not in script and 't["seed"]' not in script
    ok, text = _run_media(tmp_path / "v2", "media-2.0.0.json")
    assert ok.returncode == 0, ok.stderr
    assert "pass-seed=1" in text and "fail-seed=3" in text
    assert "video/trial-01-pass.mp4" in text and "video/trial-03-fail.mp4" in text
    ok, text = _run_media(tmp_path / "v1", "media-1.0.0.json")
    assert ok.returncode == 0, ok.stderr
    assert "pass-seed=7" in text and "fail-seed=8" in text


def test_the_workflow_rejects_an_unknown_media_format(tmp_path):
    done, _ = _run_media(tmp_path, "media-unknown.json")
    assert done.returncode != 0
    assert "livingproof-hallway-media/9.0.0" in done.stderr
    assert "KeyError" not in done.stderr and "Traceback" not in done.stderr


def test_evidence_2_plan_is_the_same_threshold_the_bundle_aggregator_uses():
    grade, n, rationale = evaluate.Aggregator().grade(
        [hallway.PASS, hallway.PASS, hallway.FAIL],
        {"trials": {"count": 3}},
    )
    assert (grade, n) == (hallway.FAIL, 3)
    assert "needs 3" in rationale


def _trace_inside_stored_maximum(tmp_path, manifest=None):
    """A copy whose trace stays inside the stored maximum, so evaluator v1.2.0 grades it.

    The committed trace steps farther from the route than its stored maximum, which
    evaluator v1.2.0 reports as INSUFFICIENT_EVIDENCE. This copy keeps the v1.19.0
    repeat checks about repeats, not about that disagreement.
    """
    path = _copy(tmp_path, manifest)
    document = json.loads(path.read_text())

    def on_route(obs):
        for sample in obs["trace"]["samples"]:
            sample[2] = 0.0

    for trial in document["trials"]["results"]:
        trial["sha256"] = _rewrite(path.parent / trial["path"], on_route)
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def test_v1_19_0_identical_repeats_are_one_pass_outcome(tmp_path):
    result = hallway.verify(_trace_inside_stored_maximum(tmp_path))
    count = _load()["trials"]["count"]
    statement = f"{count} of {count} repeats agree"
    assert [t["status"] for t in result["trials"]] == [hallway.PASS] * count
    summary = result["summary"]
    assert summary["grade"] == hallway.PASS
    assert summary["rationale"] == statement
    assert summary["reproducibility"]["agreeing"] == count
    assert summary["k"] is None and summary["n"] is None
    assert summary["aggregation"]["id"] == "repeated-outcome"
    assert hallway.summary_lines(result) == [f"{statement}: {hallway.PASS}"]


def test_v1_19_0_missing_repeat_is_a_mismatched_comparison(tmp_path):
    path = _trace_inside_stored_maximum(tmp_path)
    (path.parent / "trials" / "trial-02.json").unlink()
    result = hallway.verify(path)
    summary = result["summary"]
    assert [t["status"] for t in result["trials"]] == [
        hallway.PASS,
        hallway.INSUFFICIENT,
        hallway.PASS,
    ]
    assert summary["grade"] == hallway.INSUFFICIENT
    assert summary["k"] is None and summary["n"] is None
    assert summary["wilson95"] == [None, None]
    assert summary["rationale"] == "recorded results do not match the evidence comparison"
    assert "agreeing" not in summary["reproducibility"]
    assert hallway.summary_lines(result)[0].startswith(
        "recorded results do not match the evidence comparison"
    )


def _tool():
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_tool_exit", Path(__file__).parents[1] / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def _cli(manifest, out):
    env = os.environ.copy()
    env["LIVINGPROOF_VERIFIER_VERSION"] = "1.15.2"
    env["SOURCE_COMMIT"] = "0" * 40
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "tools" / "verify_hallway.py"),
            "--bundle-version",
            "1.1.0",
            str(manifest),
            str(out),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
    )


def _with_finding(tmp_path):
    dest = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, dest)
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["trials"]["reproducibility"] = {
        "compared": True,
        "identical": False,
        "finding": FINDING,
    }
    path = dest / "manifest.json"
    path.write_text(json.dumps(manifest) + "\n")
    return path


def test_verify_exits_0_when_the_repeats_agree(tmp_path):
    """An agreeing repeated-outcome run is a result. It does not fail verify."""
    done = _cli(_copy(tmp_path), tmp_path / "verified")
    assert done.returncode == 0, done.stderr
    assert "test-system reproducibility:" not in done.stdout
    assert "exiting 4" not in done.stderr
    report = json.loads((tmp_path / "verified" / "verification.json").read_text())
    assert report["findings"] == []
    assert report["summary"]["aggregation"]["id"] == "repeated-outcome"
    assert report["summary"]["k"] is None


def test_verify_reports_the_finding_then_exits_4(tmp_path):
    """Disagreeing repeats stay repeated-outcome, and the finding fails verify after the summary."""
    out = tmp_path / "verified"
    done = _cli(_with_finding(tmp_path), out)
    assert done.returncode == 4, done.stderr
    assert "test-system reproducibility:" in done.stdout
    assert FINDING["detail"] in done.stdout
    assert "exiting 4" in done.stderr
    grade = done.stdout.splitlines()[0]
    assert grade.endswith(f": {hallway.INSUFFICIENT}")
    assert grade.startswith("test-system") is False
    report = json.loads((out / "verification.json").read_text())
    assert report["summary"]["grade"] == hallway.INSUFFICIENT
    assert report["summary"]["aggregation"]["id"] == "repeated-outcome"
    assert report["summary"]["k"] is None and report["summary"]["wilson95"] == [None, None]
    assert report["findings"] == [FINDING]
    assert [t["status"] for t in report["trials"]] == [hallway.INSUFFICIENT] * 3
    bundle = json.loads((out / "bundle" / "manifest.json").read_text())
    assert bundle["results"][0]["provenance"]["findings"][0]["kind"] == "test-system"
    assert bundle["results"][0]["original_result"]["status"] == hallway.INSUFFICIENT
    assert bundle["assessment"]["aggregation"]["id"] == "repeated-outcome"


def test_job_exit_follows_the_finding():
    tool = _tool()
    assert tool.job_exit({"summary": {"grade": hallway.PASS}}) == 0
    assert tool.job_exit({"findings": [], "summary": {"grade": hallway.FAIL}}) == 0
    assert tool.job_exit({"findings": [FINDING], "summary": {"grade": hallway.INSUFFICIENT}}) == 4
    with pytest.raises(SystemExit, match="not test-system") as caught:
        tool.job_exit({"findings": [{"kind": "FAIL", "name": "robot", "detail": "fell"}]})
    assert caught.value.code == "finding kind is not test-system: 'robot'"


def test_passing_trials_that_disagree_exit_4_and_are_not_graded_pass(tmp_path):
    """Observations that pass stay PASS. The run is repeated-outcome and the process exits 4."""
    path = _trace_inside_stored_maximum(tmp_path)
    document = json.loads(path.read_text())
    document["trials"]["reproducibility"] = {
        "compared": True,
        "identical": False,
        "finding": FINDING,
    }
    path.write_text(json.dumps(document) + "\n")
    out = tmp_path / "verified"
    done = _cli(path, out)
    assert done.returncode == 4, done.stderr
    assert "test-system reproducibility:" in done.stdout
    report = json.loads((out / "verification.json").read_text())
    count = _load()["trials"]["count"]
    assert [trial["status"] for trial in report["trials"]] == [hallway.PASS] * count
    assert report["summary"]["grade"] == hallway.INSUFFICIENT
    assert report["summary"]["aggregation"]["id"] == "repeated-outcome"
    assert report["findings"] == [FINDING]


def test_v1_19_0_one_trial_is_k_of_n(tmp_path):
    manifest = _load()
    manifest["plan"]["trials"]["count"] = 1
    manifest["trials"]["count"] = 1
    manifest["trials"]["results"] = manifest["trials"]["results"][:1]
    manifest["trials"]["reproducibility"] = {"compared": False, "identical": None, "finding": None}
    result = hallway.verify(_trace_inside_stored_maximum(tmp_path, manifest))
    summary = result["summary"]
    assert summary["grade"] == hallway.PASS
    assert (summary["k"], summary["n"], summary["required_passes"]) == (1, 1, 1)
    assert summary["aggregation"] == {"id": "k-of-n-wilson95", "version": "1.0.0"}
    assert "reproducibility" not in summary
