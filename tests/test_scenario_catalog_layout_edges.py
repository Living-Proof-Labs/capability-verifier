# SPDX-License-Identifier: Apache-2.0
"""Edges of ``evaluators run --specs-root`` that the layout test leaves open (capability-verifier#272).

Each case was first checked by hand in the real two-checkout layout, then pinned here:

* the value both declared steps pass is exactly the directory the catalog step writes,
  not a string that merely contains it (``.specs-hallways`` and ``.specs-hallway-old`` passed
  the substring check);
* the workflow's own value loads the catalog in the layout;
* a relative ``--specs-root`` is read against the current directory, not the package;
* a missing or wrong directory is a refusal that names the path it looked in;
* with no flag the command line and ``run_lines`` still ask for ``None``;
* the catalog context does not outlive the run.
"""

from __future__ import annotations

import inspect
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_scenario_catalog_layout import (
    CATALOG,
    DRIVER,
    ROOT,
    WORKFLOW,
    _layout,
)

JOBS = ("scenario-evaluator", "scenario-media")
NEEDS_CATALOG = pytest.mark.skipif(not CATALOG.is_dir(), reason="run tools/fetch_hallway_specs.py")


def _steps(job: str) -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][job]["steps"]


def _declared_command(job: str) -> list[str]:
    """The ``python -m capability_verifier.evaluators run`` argument list in the declared branch."""
    script = next(s["run"] for s in _steps(job) if "evaluators run" in (s.get("run") or ""))
    lines = script.splitlines()
    start = next(i for i, text in enumerate(lines) if "capability_verifier.evaluators run" in text)
    words: list[str] = []
    for text in lines[start:]:
        words += shlex.split(text.rstrip("\\"))
        if not text.rstrip().endswith("\\"):
            break
    return words[words.index("run") + 1 :]


def _option(words: list[str], name: str) -> list[str]:
    return [words[i + 1] for i, word in enumerate(words) if word == name]


@pytest.mark.parametrize("job", JOBS)
def test_the_declared_step_passes_exactly_the_directory_the_catalog_step_writes(job):
    """A typo or suffix on the path (``.specs-hallways``) fails this; the substring check passes it."""
    words = _declared_command(job)
    assert _option(words, "--specs-root") == [".specs-hallway"]
    fetch = [s for s in _steps(job) if "fetch_hallway_specs.py" in (s.get("run") or "")]
    assert len(fetch) == 1 and "working-directory" not in fetch[0]
    source = (ROOT / "tools" / "fetch_hallway_specs.py").read_text(encoding="utf-8")
    assert 'root / ".specs-hallway"' in source


def _driver(
    primary: Path, path: str, cwd: Path | None = None, tag: str = ""
) -> subprocess.CompletedProcess:
    """DRIVER with ``--specs-root`` taken from argv (empty: no option), run from ``cwd``."""
    code = DRIVER.replace(
        'if sys.argv[1] == "explicit":\n    options["specs_root"] = Path(".specs-hallway")',
        'if sys.argv[1]:\n    options["specs_root"] = Path(sys.argv[1])',
    )
    assert code != DRIVER
    for name in ("evaluator-results", "work"):
        code = code.replace(f'Path("{name}")', f'Path("{primary / (name + tag)}")')
    code = code.replace(
        'Path("evidence/manifest.json")', f'Path("{primary / "evidence/manifest.json"}")'
    )
    path_entries = [str(primary / "workflow-src" / "src"), str(ROOT / "tests")]
    return subprocess.run(
        [sys.executable, "-c", code, path],
        cwd=cwd or primary,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": ":".join(path_entries),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )


@NEEDS_CATALOG
@pytest.mark.parametrize("job", JOBS)
def test_the_workflows_own_value_loads_the_catalog_in_the_layout(tmp_path, job):
    value = _option(_declared_command(job), "--specs-root")[0]
    done = _driver(_layout(tmp_path), value)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == "ran"


@NEEDS_CATALOG
def test_a_relative_specs_root_is_read_against_the_current_directory(tmp_path):
    """The same relative path loads from the primary checkout and refuses from elsewhere."""
    primary = _layout(tmp_path)
    assert _driver(primary, ".specs-hallway").returncode == 0
    elsewhere = primary / "evidence"
    done = _driver(primary, ".specs-hallway", cwd=elsewhere, tag="-b")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "not in the catalog checkout" in done.stdout
    assert _driver(primary, "../.specs-hallway", cwd=elsewhere, tag="-c").returncode == 0


@NEEDS_CATALOG
@pytest.mark.parametrize(
    "wrong", ["nowhere/.specs-hallway", "evidence", "workflow-src/.specs-hallway"]
)
def test_a_wrong_specs_root_is_refused_and_names_where_it_looked(tmp_path, wrong):
    done = _driver(_layout(tmp_path), wrong)
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout.startswith("refused: ")
    assert "not in the catalog checkout" in done.stdout
    assert f"{wrong}/specs/scenarios/walk-hallway/parameters.json" in done.stdout


def test_without_the_flag_nothing_changes(tmp_path, monkeypatch):
    """The command line passes None, and ``run_lines`` defaults to None, so the lookup stays put."""
    from capability_verifier import evaluators

    assert inspect.signature(evaluators.run_lines).parameters["specs_root"].default is None
    seen = {}

    def fake(text, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(evaluators, "run_lines", fake)
    lines = tmp_path / "lines.txt"
    lines.write_text("a/b@v1.0.0/c.json\n", encoding="utf-8")
    argv = ["run", "--lines", str(lines), "--evidence-manifest", "m.json", "--out", "out"]
    assert evaluators.main(argv) == 0
    assert seen["specs_root"] is None


def test_the_option_is_in_the_run_help_and_not_on_the_other_commands(capsys):
    from capability_verifier import evaluators

    with pytest.raises(SystemExit) as caught:
        evaluators.main(["run", "--help"])
    assert caught.value.code == 0
    assert "--specs-root" in capsys.readouterr().out
    for command in ("route", "check"):
        with pytest.raises(SystemExit) as other:
            evaluators.main([command, "--specs-root", "x"])
        assert other.value.code == 2
        capsys.readouterr()


@NEEDS_CATALOG
def test_the_catalog_context_does_not_outlive_the_run(tmp_path):
    """After a run with ``specs_root`` the lookup is back to the default."""
    from test_scenario_loader import LINE_A, SCENARIO, _lines, _run

    from capability_verifier import evaluators, scenario_parameters

    primary = _layout(tmp_path)
    calls: list = []
    evaluators.run_lines(
        LINE_A + "\n",
        evidence=primary / "evidence" / "manifest.json",
        out=tmp_path / "out",
        work=tmp_path / "work",
        phase="trial",
        kind="scenario",
        specs_root=primary / ".specs-hallway",
        **_lines(
            "",
            {LINE_A: SCENARIO.read_bytes()},
            None,
            run=_run(calls, "livingproof-evaluator-scenario"),
        ),
    )
    assert scenario_parameters._DECLARED_SPECS.get() is None
    assert scenario_parameters._DECLARED_WORLDS.get() is None
