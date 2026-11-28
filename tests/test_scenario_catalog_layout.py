# SPDX-License-Identifier: Apache-2.0
"""The declared scenario branch finds the catalog the job fetched (capability-verifier#272).

``scenario.yml`` fetches the scenario catalog into ``.specs-hallway`` in the primary
verifier checkout, then runs the loader with ``PYTHONPATH=workflow-src/src``. The
package then sits under ``workflow-src``, so a path taken from the package location
points at ``workflow-src/.specs-hallway``, which does not exist. The workflow passes the
fetched catalog to the loader as ``--specs-root``.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_scenario_loader import _run_step

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scenario.yml"
CATALOG = ROOT / ".specs-hallway"
# A real walk-hallway evidence manifest, so the thresholds come from the real parameter file.
EVIDENCE = ROOT / "tests" / "fixtures" / "hallway" / "review-g1" / "agree" / "manifest.json"

# Runs in the primary checkout with the package imported from workflow-src/src.
DRIVER = r"""
import sys
from pathlib import Path

import capability_verifier.evaluators as loader

assert "workflow-src" in Path(loader.__file__).parts, loader.__file__
import test_scenario_loader as shared

options = {}
if sys.argv[1] == "explicit":
    options["specs_root"] = Path(".specs-hallway")
calls = []
try:
    loader.run_lines(
        shared.LINE_A + "\n",
        evidence=Path("evidence/manifest.json"),
        out=Path("evaluator-results"),
        work=Path("work"),
        phase="trial",
        kind="scenario",
        **shared._lines(
            "",
            {shared.LINE_A: shared.SCENARIO.read_bytes()},
            None,
            run=shared._run(calls, "livingproof-evaluator-scenario"),
        ),
        **options,
    )
except loader.Refusal as exc:
    print(f"refused: {exc}")
    raise SystemExit(1)
print("ran")
"""


def _layout(base: Path) -> Path:
    """The declared branch's directories: a catalog in the primary checkout and the
    loader package under ``workflow-src/src``."""
    primary = base / "primary"
    (primary / "evidence").mkdir(parents=True)
    shutil.copy(EVIDENCE, primary / "evidence" / "manifest.json")
    shutil.copytree(CATALOG, primary / ".specs-hallway")
    package = base / "primary" / "workflow-src" / "src"
    package.mkdir(parents=True)
    shutil.copytree(
        ROOT / "src" / "capability_verifier",
        package / "capability_verifier",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return primary


def _loader(primary: Path, how: str) -> subprocess.CompletedProcess:
    path = [str(primary / "workflow-src" / "src"), str(ROOT / "tests")]
    return subprocess.run(
        [sys.executable, "-c", DRIVER, how],
        cwd=primary,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(not CATALOG.is_dir(), reason="run tools/fetch_hallway_specs.py first")
def test_the_catalog_is_missing_when_the_loader_looks_next_to_its_package(tmp_path):
    """The defect: with no catalog path the loader reads ``workflow-src/.specs-hallway``."""
    primary = _layout(tmp_path)
    done = _loader(primary, "implicit")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "not in the catalog checkout" in done.stdout
    assert "workflow-src/.specs-hallway" in done.stdout


@pytest.mark.skipif(not CATALOG.is_dir(), reason="run tools/fetch_hallway_specs.py first")
def test_the_loader_reads_the_catalog_it_is_given(tmp_path):
    """Removing ``specs_root`` from ``run_lines`` fails this."""
    primary = _layout(tmp_path)
    done = _loader(primary, "explicit")
    assert done.returncode == 0, done.stdout + done.stderr
    thresholds = json.loads(
        (primary / "work" / "livingproof-scenario" / "thresholds.json").read_text()
    )
    assert thresholds["time_limit"] > 0


def _script(job: str, name: str) -> str:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        item for item in document["jobs"][job]["steps"] if item.get("name", "").startswith(name)
    )
    return step["run"]


@pytest.mark.parametrize(
    "job,name,phase",
    [
        ("scenario-evaluator", "Scenario evaluator, one result file per trial", "trial"),
        ("scenario-media", "Scenario evaluator on the recordings", "media"),
    ],
)
def test_both_declared_branches_pass_the_fetched_catalog(tmp_path, job, name, phase):
    """The catalog step and the loader run in the same directory, so one path serves both."""
    script = _script(job, name)
    done, log = _run_step(script, "declared", tmp_path / job)
    assert done.returncode == 0, done.stderr or done.stdout
    run = next(line for line in log.splitlines() if "capability_verifier.evaluators run" in line)
    words = shlex.split(run)
    assert f"--phase {phase}" in run
    # Token match so a typo or suffix (.specs-hallways) fails; a substring check would pass.
    assert words[words.index("--specs-root") + 1] == ".specs-hallway"
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"][job]["steps"]
    fetch = [s for s in steps if "fetch_hallway_specs.py" in (s.get("run") or "")]
    assert fetch and "working-directory" not in fetch[0]


def test_the_command_line_takes_the_catalog_path(tmp_path, monkeypatch):
    """``evaluators run --specs-root`` reaches ``run_lines``."""
    from capability_verifier import evaluators

    seen = {}

    def fake(text, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(evaluators, "run_lines", fake)
    lines = tmp_path / "lines.txt"
    lines.write_text("a/b@v1.0.0/c.json\n", encoding="utf-8")
    code = evaluators.main(
        [
            "run",
            "--lines",
            str(lines),
            "--evidence-manifest",
            str(tmp_path / "m.json"),
            "--out",
            str(tmp_path / "out"),
            "--specs-root",
            ".specs-hallway",
        ]
    )
    assert code == 0
    assert seen["specs_root"] == Path(".specs-hallway")
