# SPDX-License-Identifier: Apache-2.0
"""The tests that render the real corner-left bundle also run on arm64 in CI (#219, Vera's L8).

The ``verify`` job runs the whole suite on amd64 only, and the report jobs run the placement code on
arm64 without pytest, so the real-report fixture tests were never run natively on arm64. A job on the
ubuntu-24.04-arm runner runs them through the Taskfile task ``test:real-report``, which has to name
every test file that uses the real-report fixture or the pinned reporter.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "verify.yml").read_text())
RUNTIME = yaml.safe_load((ROOT / "Taskfile.runtime.yml").read_text())
HOST = yaml.safe_load((ROOT / "Taskfile.yml").read_text())


def _uses_real_report(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        "fixtures/real-report" in text
        or '"real-report"' in text
        or "test_report_integrity_not_run_render" in text
    )


def test_a_job_on_the_arm64_runner_runs_the_real_report_task():
    jobs = [
        job
        for job in WORKFLOW["jobs"].values()
        if job.get("runs-on") == "ubuntu-24.04-arm"
        and any("test:real-report" in str(step.get("run", "")) for step in job["steps"])
    ]
    assert len(jobs) == 1


def test_the_arm64_job_gates_the_release():
    (name,) = [
        key
        for key, job in WORKFLOW["jobs"].items()
        if job.get("runs-on") == "ubuntu-24.04-arm"
        and any("test:real-report" in str(step.get("run", "")) for step in job["steps"])
    ]
    assert name in WORKFLOW["jobs"]["release"]["needs"]


def test_the_task_names_every_test_file_that_uses_the_real_report_fixture():
    commands = " ".join(str(c) for c in RUNTIME["tasks"]["test:real-report"]["cmds"])
    named = {part for part in commands.split() if part.endswith(".py")}
    needed = {
        f"tests/{path.name}"
        for path in (ROOT / "tests").glob("test_*.py")
        if _uses_real_report(path) and path.name != Path(__file__).name
    }
    assert needed and needed <= named, sorted(needed - named)


def test_the_host_taskfile_wraps_it_like_the_other_container_tasks():
    commands = " ".join(str(c) for c in HOST["tasks"]["test:real-report"]["cmds"])
    assert "docker compose run --build --rm verifier test:real-report" in commands
