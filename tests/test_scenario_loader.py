# SPDX-License-Identifier: Apache-2.0
"""A declared scenario evaluator runs through the loader (va#54 slice 3c).

Walk-hallway only. A world-graded scenario keeps the existing wrapper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


_UV_STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$UV_LOG"
case "$*" in
  *"capability_verifier.evaluators route"*)
    if [ -n "$ROUTE_REAL" ]; then
      PYTHONPATH="$ROUTE_PYTHONPATH" exec "$ROUTE_PYTHON" -m capability_verifier.evaluators route \
        --lines evaluators.txt \
        --evidence-manifest evidence/manifest.json
    fi
    printf '%s\n' "$ROUTE_MODE"
    exit "${ROUTE_STATUS:-0}"
    ;;
esac
if [ "$1" = sync ]; then
  mkdir -p evaluator/.venv/bin
  cat > evaluator/.venv/bin/python << 'EOF'
#!/bin/sh
printf '%s\n' /tmp/site-packages
EOF
  chmod +x evaluator/.venv/bin/python
  exit 0
fi
case "$*" in
  *"livingproof_evaluator_scenario as package"*)
    printf '%s\n' /tmp/evaluator/src/livingproof_evaluator_scenario/__init__.py
    exit 0
    ;;
  *importlib.metadata*)
    printf '%s\n' "${EVALUATOR_TAG#v}"
    exit 0
    ;;
esac
exit 0
"""

_PYTHON_STUB = r"""#!/bin/sh
if [ "$1" = -m ] && [ "$2" = capability_verifier.evaluator_pin ]; then
  printf '%s\n' "${EVALUATOR_TAG#v}"
  exit 0
fi
printf 'unexpected python3 %s\n' "$*" >&2
exit 1
"""


def _run_step(
    script: str,
    mode: str,
    work: Path,
    *,
    route_status: int = 0,
    evaluators: str = "Living-Proof-Labs/example@v1.2.3/livingproof-evaluator.json",
    route_real: bool = False,
    shell: str = "bash",
    evaluator_commit: str = "c" * 40,
) -> tuple[subprocess.CompletedProcess, str]:
    """Run one workflow step with a stub uv.

    ``mode`` is what route prints, and ``route_status`` is its exit code.
    ``route_real`` runs the installed loader for route instead of the stub.
    ``evaluator_commit`` is ``EVALUATOR_COMMIT`` (empty: no lock pin).
    """
    work.mkdir(parents=True)
    bindir = work / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text(_UV_STUB)
    uv.chmod(0o755)
    python3 = bindir / "python3"
    python3.write_text(_PYTHON_STUB)
    python3.chmod(0o755)
    (work / "evidence").mkdir()
    (work / "evidence" / "manifest.json").write_text("{}\n", encoding="utf-8")
    log = work / "uv.log"
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["UV_LOG"] = str(log)
    env["ROUTE_MODE"] = mode
    env["ROUTE_STATUS"] = str(route_status)
    if route_real:
        env["ROUTE_REAL"] = "1"
        env["ROUTE_PYTHON"] = sys.executable
        env["ROUTE_PYTHONPATH"] = str(ROOT / "src")
    env["EVALUATORS"] = evaluators
    env["EVALUATOR_REPOSITORY"] = "Living-Proof-Labs/livingproof-evaluator-scenario"
    env["EVALUATOR_TAG"] = "v1.2.3"
    env["EVALUATOR_COMMIT"] = evaluator_commit
    done = subprocess.run(
        [shell, "-e", "-c", script],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    return done, text


def _assert_split(log: str, mode: str, phase: str, tool: str) -> None:
    loader = "capability_verifier.evaluators run" in log and f"--phase {phase}" in log
    hardcoded = tool in log
    if mode == "declared":
        assert loader, log
        assert not hardcoded, log
    else:
        assert hardcoded, log
        assert not loader, log


def test_a_declared_scenario_does_not_also_run_the_hardcoded_trial(tmp_path):
    """The trial step runs the loader or the wrapper, not both."""
    script = _script("scenario-evaluator", "Scenario evaluator, one result file per trial")
    for mode in ("declared", "wrapper"):
        done, log = _run_step(script, mode, tmp_path / mode)
        assert done.returncode == 0, done.stderr or done.stdout
        _assert_split(log, mode, "trial", "run_scenario_evaluator.py")


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


def test_a_declared_scenario_does_not_also_run_the_hardcoded_media_job(tmp_path):
    """The media step runs the loader or the wrapper, not both."""
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    for mode in ("declared", "wrapper"):
        done, log = _run_step(script, mode, tmp_path / mode)
        assert done.returncode == 0, done.stderr or done.stdout
        _assert_split(log, mode, "media", "run_scenario_media.py")


_STEPS = (
    (
        "scenario-evaluator",
        "Scenario evaluator, one result file per trial",
        "run_scenario_evaluator.py",
    ),
    (
        "scenario-media",
        "Scenario evaluator on the recordings",
        "run_scenario_media.py",
    ),
)


def _tools_ran(log: str, tool: str) -> bool:
    return "capability_verifier.evaluators run" in log or tool in log


def test_a_failing_route_runs_neither_tool(tmp_path):
    """``mode=$(route) || mode=wrapper`` would run the wrapper and pass.

    The container step's shell is sh. Both sh -e and bash -e must stop.
    """
    for shell in ("bash", "sh"):
        for job, name, tool in _STEPS:
            script = _script(job, name)
            done, log = _run_step(
                script,
                "declared",
                tmp_path / f"{shell}-{job}",
                route_status=1,
                shell=shell,
            )
            assert done.returncode != 0, (shell, job, done.stderr)
            assert "capability_verifier.evaluators route" in log
            assert not _tools_ran(log, tool), log


def test_an_unknown_route_mode_fails_both_steps(tmp_path):
    """Any exit-0 output other than declared or wrapper used to run the wrapper."""
    for index, mode in enumerate(("", "garbage", "declared ")):
        for job, name, tool in _STEPS:
            script = _script(job, name)
            done, log = _run_step(script, mode, tmp_path / f"{job}-{index}")
            assert done.returncode != 0, (job, mode, done.stderr)
            assert "expected declared or wrapper" in done.stderr
            assert not _tools_ran(log, tool), log


def test_a_bad_evaluators_input_fails_both_steps_with_a_clear_error(tmp_path):
    """An invalid or comments-only evaluators value names the line and the next command."""
    cases = (
        ("# only a comment", "no declaration lines"),
        ("not-a-line", "is not a declaration line"),
    )
    hint = "acme/widgets@v1.2.3/evaluator.json"
    command = "python -m capability_verifier.evaluators check --lines FILE"
    for evaluators, detail in cases:
        for job, name, tool in _STEPS:
            script = _script(job, name)
            done, log = _run_step(
                script,
                "",
                tmp_path / f"{job}-{detail.split()[0]}",
                evaluators=evaluators,
                route_real=True,
            )
            assert done.returncode != 0, (job, evaluators, done.stderr)
            assert "refused:" in done.stderr
            assert detail in done.stderr
            assert hint in done.stderr
            assert command in done.stderr
            assert not _tools_ran(log, tool), log
