# SPDX-License-Identifier: Apache-2.0
"""The scenario media job: the evaluator reads each trial's MCAP, the collector puts it on the report.

The result files under fixtures/scenario-media-results are what livingproof-evaluator-scenario's
media runner wrote for real sim MCAP and bindings (see its PROVENANCE.md).
"""

import json
import shutil
import sys
import types
from pathlib import Path

import pytest
import yaml

from capability_verifier.collect import ThresholdCitationError, collect

ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/scenario.yml").read_text()
DOC = yaml.safe_load(WORKFLOW)
RESULTS = Path(__file__).parent / "fixtures" / "scenario-media-results"
TOOL = ROOT / "tools" / "run_scenario_media.py"


def _bundle():
    return {"results": [{"id": "stock-seed-01"}, {"id": "stock-seed-02"}]}


def _steps(result):
    document = result["evaluator_results"][0]
    return {s["id"]: s for s in document["scenario"]["steps"]}


def test_the_collector_puts_wall_contact_and_not_evaluated_steps_on_the_report(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    bundle, report = _bundle(), tmp_path / "report"
    counts: dict = {}
    collect(bundle, directory, report, counts)
    assert counts["livingproof-scenario"]["appended"] == 2
    for result in bundle["results"]:
        steps = _steps(result)
        assert steps["hallway-wall-contact"]["verdict"] == "FAIL"
        assert "wall-left" in steps["hallway-wall-contact"]["summary"]
        assert steps["hallway-fall"]["verdict"] == "NOT_EVALUATED"
        assert steps["hallway-fall"]["reason"] == "missing_threshold"
    assert (report / "media" / "thresholds.json").is_file()


def test_the_verdict_carries_the_evaluator_identity(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    bundle = _bundle()
    collect(bundle, directory)
    document = bundle["results"][0]["evaluator_results"][0]
    assert document["evaluator"]["id"] == "livingproof-scenario"
    assert document["evaluator"]["version"]
    identity = [i for i in document["inputs"] if i["role"] == "evaluator"]
    assert len(identity) == 1 and "@" in identity[0]["uri"] and len(identity[0]["sha256"]) == 64


def test_its_limits_sit_beside_the_observation_graded_limits_without_clashing(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    other = directory / "livingproof-scenario"
    other.mkdir()
    (other / "thresholds.json").write_text('{"limits":{"time_limit":1}}\n')
    collect(_bundle(), directory)  # two different files at two paths: no substitution error


def test_one_thresholds_file_cited_with_two_digests_is_still_refused(tmp_path):
    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    (directory / "livingproof-scenario-media" / "media" / "thresholds.json").write_text("{}\n")
    with pytest.raises(ThresholdCitationError, match="altered"):
        collect(_bundle(), directory)


def test_the_workflow_has_a_media_scenario_job_the_report_waits_for():
    job = DOC["jobs"]["scenario-media"]
    assert job["needs"] == ["plan", "evidence", "media"]
    assert "!cancelled()" in job["if"]
    report = DOC["jobs"]["report"]
    assert "scenario-media" in report["needs"]
    assert "scenario-media" not in report["if"]  # a media problem never blocks the report
    uploads = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert uploads[0]["with"]["name"].endswith(
        "evaluator-results-livingproof-scenario-media-${{ matrix.configuration }}"
    )
    assert (
        uploads[0]["with"]["name"]
        .split("evaluator-results-")[1]
        .endswith("-${{ matrix.configuration }}")
    )  # the report's evaluator-results-*-<configuration> pattern picks it up


def test_the_job_passes_no_thresholds_and_reads_the_media_artifact():
    text = json.dumps(DOC["jobs"]["scenario-media"])
    assert "run_scenario_media.py" in text
    assert "--thresholds" not in text and "hallway.thresholds" not in text
    assert "mcap-${{ matrix.configuration }}" in text  # the media job's artifact
    assert "needs.plan.outputs.evaluator-commit" in text  # identity comes from the lock's pin


def test_the_tool_takes_thresholds_from_nowhere_but_the_evaluator():
    text = TOOL.read_text()
    assert "thresholds(" not in text and "import thresholds" not in text
    assert "capability_verifier.hallway import grading_inputs" in text


def _stub_media(monkeypatch, calls):
    module = types.ModuleType("livingproof_evaluator_scenario.media")

    def write_media_run(manifest, media, out, **kwargs):
        calls.append((Path(manifest), Path(media), Path(out), kwargs))
        return []

    module.write_media_run = write_media_run
    import livingproof_evaluator_scenario as package

    monkeypatch.setattr(package, "media", module, raising=False)
    monkeypatch.setitem(sys.modules, "livingproof_evaluator_scenario.media", module)


def _tool(monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    monkeypatch.delitem(sys.modules, "run_scenario_media", raising=False)
    import run_scenario_media

    return run_scenario_media


def _run(tmp_path, with_media=True):
    manifest = tmp_path / "evidence" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"format": "x"}))
    media = tmp_path / "media"
    media.mkdir()
    if with_media:
        (media / "media.json").write_text("{}")
    return manifest, media, tmp_path / "out"


def test_the_tool_hands_the_world_the_media_and_the_identity_to_the_evaluator(
    tmp_path, monkeypatch
):
    calls: list = []
    _stub_media(monkeypatch, calls)
    tool = _tool(monkeypatch)
    monkeypatch.setattr(tool, "grading_inputs", lambda document: ({"places": []}, {"p": 1}))
    manifest, media, out = _run(tmp_path)
    code = tool.main(
        [str(manifest), str(media), str(out), "--identity", "git+https://x@" + "a" * 40]
    )
    assert code == 0
    ((got_manifest, got_media, got_out, kwargs),) = calls
    assert (got_manifest, got_media, got_out) == (manifest, media, out)
    assert kwargs["world"] == {"places": []} and kwargs["parameters"] == {"p": 1}
    assert kwargs["identity"] == "git+https://x@" + "a" * 40
    assert "thresholds" not in kwargs


def test_without_media_there_is_a_note_and_no_result(tmp_path, monkeypatch):
    calls: list = []
    _stub_media(monkeypatch, calls)
    tool = _tool(monkeypatch)
    manifest, media, out = _run(tmp_path, with_media=False)
    assert tool.main([str(manifest), str(media), str(out), "--identity", "i"]) == 0
    assert calls == [] and (out / "NO-MEDIA-RESULTS.txt").is_file()


def test_a_pinned_evaluator_without_the_media_runner_is_a_note_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "livingproof_evaluator_scenario.media", None)
    tool = _tool(monkeypatch)
    manifest, media, out = _run(tmp_path)
    assert tool.main([str(manifest), str(media), str(out), "--identity", "i"]) == 0
    assert "no media runner" in (out / "NO-MEDIA-RESULTS.txt").read_text()


def test_the_media_job_has_a_timeout_so_a_hang_cannot_hold_the_report():
    timeout = DOC["jobs"]["scenario-media"].get("timeout-minutes")
    assert timeout == 30


def test_a_truncated_result_file_is_reported_and_does_not_fail_the_collection(tmp_path, capsys):
    from capability_verifier.collect import main

    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    broken = directory / "livingproof-scenario-media" / "stock-seed-02.json"
    broken.write_text(broken.read_text()[:200])
    manifest, report = tmp_path / "manifest.json", tmp_path / "report"
    report.mkdir()
    manifest.write_text(json.dumps(_bundle()))
    assert main([str(manifest), str(directory), str(report)]) == 0
    assert "stock-seed-02.json" in capsys.readouterr().err
    stored = json.loads(manifest.read_text())["results"]
    assert len(stored[0]["evaluator_results"]) == 1  # the intact trial is still collected
    assert "evaluator_results" not in stored[1]
    problems = (report / "evaluator-result-problems.txt").read_text()
    assert "stock-seed-02.json" in problems and "not valid JSON" in problems


def test_a_result_whose_media_thresholds_file_is_missing_is_reported_not_fatal(tmp_path, capsys):
    from capability_verifier.collect import main

    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    shutil.copytree(directory / "livingproof-scenario-media", directory / "a-other-run")
    (directory / "livingproof-scenario-media" / "media" / "thresholds.json").unlink()
    manifest, report = tmp_path / "manifest.json", tmp_path / "report"
    report.mkdir()
    manifest.write_text(json.dumps(_bundle()))
    assert main([str(manifest), str(directory), str(report)]) == 0
    err = capsys.readouterr().err
    assert "media/thresholds.json" in err and "missing" in err
    assert len(json.loads(manifest.read_text())["results"][0]["evaluator_results"]) == 1
    assert "media/thresholds.json" in (report / "evaluator-result-problems.txt").read_text()


def test_a_clean_collection_writes_no_problems_file(tmp_path):
    from capability_verifier.collect import main

    directory = tmp_path / "evaluator-results"
    shutil.copytree(RESULTS, directory)
    manifest, report = tmp_path / "manifest.json", tmp_path / "report"
    report.mkdir()
    manifest.write_text(json.dumps(_bundle()))
    assert main([str(manifest), str(directory), str(report)]) == 0
    assert not (report / "evaluator-result-problems.txt").exists()


def test_the_docs_say_the_job_runs_the_runner_from_scenario_v1_5_0():
    text = (ROOT / "docs" / "locks.md").read_text()
    section = text.split("## Recordings in the scenario media job")[1].split("\n## ")[0]
    assert "v1.5.0" in section
    assert "does nothing" not in section
