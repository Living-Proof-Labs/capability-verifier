# SPDX-License-Identifier: Apache-2.0
"""A declared scenario evaluator runs through the loader (va#54 slice 3c).

Walk-hallway only. A world-graded scenario keeps the existing wrapper.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_evaluators import (
    COMMIT,
    ROOT,
    _no_branch,
    _plant,
    _resolve,
    _schema_fetch,
)

from capability_verifier.evaluators import Refusal, prepare, route_lines, run_lines

PAIR = ROOT / "tests" / "fixtures" / "evaluators" / "two-robot-graders.json"
SCENARIO = ROOT / "tests" / "fixtures" / "evaluators" / "scenario.json"
WORKFLOW = ROOT / ".github" / "workflows" / "scenario.yml"
LINE_A = "acme/widgets@v1.2.3/scenario.json"
LINE_B = "acme/other@v1.2.3/other.json"
HALLWAY = {"lock": {"content": {"scenario": "walk-hallway@0.1.0"}}}
CORNER = {"lock": {"content": {"scenario": "walk-corner@0.1.0"}}}


def _pair() -> tuple[bytes, bytes]:
    documents = json.loads(PAIR.read_text(encoding="utf-8"))
    return json.dumps(documents[0]).encode(), json.dumps(documents[1]).encode()


def _manifest(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    return path


def _run(calls: list, package: str, commit: str = COMMIT):
    def run(command, **kwargs):
        calls.append(list(command))
        if command[:2] == ["git", "ls-remote"]:
            return _no_branch(command, **kwargs)
        if command[0] == "uv" and command[1] == "pip":
            _plant(Path(command[4]), package, "1.2.3", commit)
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def _lines(text: str, injected: dict[str, bytes], evidence: Path, **kwargs):
    return dict(
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=kwargs.pop("run"),
        injected=injected,
        **kwargs,
    )


def test_two_robot_graders_are_refused_before_anything_is_installed(tmp_path):
    """Removing the one-grading check lets this list install."""
    first, second = _pair()
    calls: list = []
    evidence = _manifest(tmp_path / "manifest.json", HALLWAY)
    with pytest.raises(Refusal, match="at most one grading evaluator") as caught:
        run_lines(
            LINE_A + "\n" + LINE_B + "\n",
            evidence=evidence,
            out=tmp_path / "out",
            work=tmp_path / "work",
            **_lines(
                "",
                {LINE_A: first, LINE_B: second},
                evidence,
                run=_run(calls, "livingproof-evaluator-scenario"),
            ),
        )
    text = str(caught.value)
    assert LINE_A in text
    assert LINE_B in text
    assert not any(
        command[:2] == ["uv", "venv"] or command[:2] == ["uv", "pip"] for command in calls
    )


def test_a_scenario_descriptor_is_accepted_and_an_unknown_kind_is_still_refused():
    prepared = prepare(
        LINE_A + "\n",
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=_no_branch,
        injected={LINE_A: SCENARIO.read_bytes()},
    )
    assert prepared[0].document["kind"] == "scenario"
    assert prepared[0].document["grading"] == "robot"
    document = json.loads(SCENARIO.read_text(encoding="utf-8"))
    document["kind"] = "other"
    schema_path = ROOT / "tests/fixtures/evaluator-schemas/implementation.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["kind"]["enum"] = ["integrity", "advisory", "scenario", "other"]
    with pytest.raises(Refusal, match="unknown kind"):
        prepare(
            LINE_A + "\n",
            fetch=lambda url: _schema_fetch(url, schema),
            resolve=_resolve,
            run=_no_branch,
            injected={LINE_A: json.dumps(document).encode()},
        )


def test_the_trial_uses_result_dir_and_never_result(tmp_path, monkeypatch):
    evidence = _manifest(tmp_path / "manifest.json", {})
    calls: list = []
    monkeypatch.setattr(
        "capability_verifier.evaluators._hallway_thresholds",
        lambda manifest: {"time_limit": 1, "lateral_limit": 1, "half_width": 1, "fall_height": 1},
    )
    run_lines(
        LINE_A + "\n",
        evidence=evidence,
        out=tmp_path / "out",
        work=tmp_path / "work",
        phase="trial",
        kind="scenario",
        **_lines(
            "",
            {LINE_A: SCENARIO.read_bytes()},
            evidence,
            run=_run(calls, "livingproof-evaluator-scenario"),
        ),
    )
    command = next(item for item in calls if "-m" in item)
    module = command[command.index("-m") + 1]
    assert module == "livingproof_evaluator_scenario"
    assert "--thresholds" in command
    result_dir = Path(command[command.index("--out") + 1])
    assert result_dir.name == "livingproof-scenario"
    assert result_dir.is_dir()
    assert not any(part.endswith("result.json") or part == "{result}" for part in command)


def test_the_media_run_is_invoked_when_a_world_and_recordings_are_present(tmp_path, monkeypatch):
    """Deleting the media invocation leaves no media command."""
    evidence = _manifest(tmp_path / "manifest.json", {})
    media = tmp_path / "media"
    media.mkdir()
    (media / "media.json").write_text("{}\n", encoding="utf-8")
    world = tmp_path / "world.json"
    world.write_text("{}\n", encoding="utf-8")
    calls: list = []

    def world_file(manifest, destination):
        return world

    monkeypatch.setattr("capability_verifier.evaluators._world_file", world_file)
    run_lines(
        LINE_A + "\n",
        evidence=evidence,
        out=tmp_path / "out",
        media_dir=media,
        work=tmp_path / "work",
        phase="media",
        kind="scenario",
        **_lines(
            "",
            {LINE_A: SCENARIO.read_bytes()},
            evidence,
            run=_run(calls, "livingproof-evaluator-scenario"),
        ),
    )
    command = next(
        item for item in calls if "-m" in item and item[item.index("-m") + 1].endswith(".media")
    )
    assert "--world" in command
    assert command[command.index("--world") + 1] == str(world)
    assert not any(part.endswith("result.json") or part == "{result}" for part in command)
    assert any(part.endswith("livingproof-scenario-media") for part in command)


def test_walk_hallway_starts_no_media_process(tmp_path):
    evidence = _manifest(tmp_path / "manifest.json", HALLWAY)
    media = tmp_path / "media"
    media.mkdir()
    (media / "media.json").write_text("{}\n", encoding="utf-8")
    calls: list = []
    records = run_lines(
        LINE_A + "\n",
        evidence=evidence,
        out=tmp_path / "out",
        media_dir=media,
        work=tmp_path / "work",
        phase="media",
        kind="scenario",
        **_lines(
            "",
            {LINE_A: SCENARIO.read_bytes()},
            evidence,
            run=_run(calls, "livingproof-evaluator-scenario"),
        ),
    )
    assert records[0].outcome == "skipped"
    assert not any("-m" in item and item[item.index("-m") + 1].endswith(".media") for item in calls)
    assert not any(command[:1] == ["uv"] for command in calls)


def test_route_selects_the_loader_only_for_walk_hallway(tmp_path):
    hallway = _manifest(tmp_path / "hallway.json", HALLWAY)
    corner = _manifest(tmp_path / "corner.json", CORNER)
    common = {
        "fetch": lambda url: _schema_fetch(url),
        "resolve": _resolve,
        "run": _no_branch,
        "injected": {LINE_A: SCENARIO.read_bytes()},
    }
    assert route_lines(LINE_A + "\n", hallway, **common) == "declared"
    assert route_lines(LINE_A + "\n", corner, **common) == "wrapper"
    integrity = (ROOT / "tests/fixtures/evaluators/run-integrity.json").read_bytes()
    integrity_line = "acme/widgets@v1.2.3/integrity.json"
    assert (
        route_lines(
            integrity_line + "\n",
            hallway,
            fetch=lambda url: _schema_fetch(url),
            resolve=_resolve,
            run=_no_branch,
            injected={integrity_line: integrity},
        )
        == "wrapper"
    )


def _script(job: str, name: str) -> str:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        item for item in document["jobs"][job]["steps"] if item.get("name", "").startswith(name)
    )
    return step["run"]


def _branches(script: str, declared: str, wrapper: str) -> tuple[str, str]:
    start = script.index(declared)
    middle = script.index(wrapper)
    return script[start:middle], script[middle:]


def test_a_declared_scenario_does_not_also_run_the_hardcoded_trial():
    """Putting the hardcoded tool in the declared branch fails this test."""
    script = _script("scenario-evaluator", "Scenario evaluator, one result file per trial")
    declared, wrapper = _branches(
        script, "# declared-scenario-evaluator", "# wrapper-scenario-evaluator"
    )
    assert "capability_verifier.evaluators run" in declared
    assert "--phase trial" in declared
    assert "run_scenario_evaluator.py" not in declared
    assert "run_scenario_evaluator.py" in wrapper
    assert "capability_verifier.evaluators run" not in wrapper


SCENARIO_LINE = "Living-Proof-Labs/livingproof-evaluator-scenario@v1.6.0/livingproof-evaluator.json"
SCENARIO_COMMIT = "5eb435ac42d121708e668ceb049abfd2519b42ab"


def test_the_scenario_declaration_line_is_the_v1_6_0_release():
    """Changing the scenario declaration line fails this test."""
    import urllib.request

    from test_scenario_loader_compare import LINE

    from capability_verifier.repin import resolve as resolve_tag

    assert LINE == SCENARIO_LINE
    for path in (ROOT / "README.md", ROOT / "docs" / "locks.md"):
        assert SCENARIO_LINE in path.read_text(encoding="utf-8"), path
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        assert SCENARIO_LINE not in path.read_text(encoding="utf-8"), path
    assert resolve_tag("Living-Proof-Labs/livingproof-evaluator-scenario", "v1.6.0") == (
        SCENARIO_COMMIT
    )
    url = (
        "https://raw.githubusercontent.com/Living-Proof-Labs/"
        f"livingproof-evaluator-scenario/{SCENARIO_COMMIT}/livingproof-evaluator.json"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        released = response.read()
    assert released == (ROOT / "tests/fixtures/evaluators/scenario.json").read_bytes()


def test_a_declared_scenario_does_not_also_run_the_hardcoded_media_job():
    """Putting the hardcoded media tool in the declared branch fails this test."""
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    declared, wrapper = _branches(script, "# declared-scenario-media", "# wrapper-scenario-media")
    assert "capability_verifier.evaluators run" in declared
    assert "--phase media" in declared
    assert "run_scenario_media.py" not in declared
    assert "run_scenario_media.py" in wrapper
    assert "capability_verifier.evaluators run" not in wrapper
