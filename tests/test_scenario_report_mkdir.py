# SPDX-License-Identifier: Apache-2.0
"""Guards for the v1.21.0 report-job mkdir regression (capability-verifier#99).

The collect step copies cited thresholds.json into reports/. The render step
must create that directory with `mkdir -p` so an existing reports/ is kept,
including the copied thresholds and their SHA256SUMS entries. The behaviour
test is test_collect_then_render_keeps_thresholds_in_an_existing_reports_directory.
Proposed by Vera in the capability-verifier#100 review.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = (ROOT / ".github/workflows/scenario.yml").read_text(encoding="utf-8")
WALK = (ROOT / ".github/workflows/walk-hallway.yml").read_text(encoding="utf-8")
VERIFY = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")

_SHELL_MKDIR = re.compile(r"^(?P<cmd>mkdir(?:\s+-p)?)\s+(?P<dir>\S+)")


def _step(workflow: str, name: str) -> str:
    """The run script of the step whose name starts with ``name``."""
    block = workflow[workflow.index(f"- name: {name}") :]
    block = block[block.index("run: |") + len("run: |") + 1 :]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


def _run_scripts(workflow: str) -> list[str]:
    """Each ``run: |`` body in ``workflow``, as a script string."""
    scripts: list[str] = []
    lines = workflow.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip(" ")
        indent = len(lines[i]) - len(stripped)
        if stripped.startswith("run: |"):
            body: list[str] = []
            i += 1
            while i < len(lines):
                raw = lines[i]
                if raw.strip() == "":
                    body.append("")
                    i += 1
                    continue
                if len(raw) - len(raw.lstrip(" ")) <= indent:
                    break
                body.append(raw[indent + 2 :])
                i += 1
            scripts.append("\n".join(body) + "\n")
            continue
        i += 1
    return scripts


def test_the_render_step_creates_reports_with_mkdir_p():
    """The render step must be idempotent: collect may already have created reports/."""
    script = _step(SCENARIO, "Validate, then render")
    assert "mkdir -p reports" in script
    for line in script.splitlines():
        match = _SHELL_MKDIR.match(line.strip())
        if match and match.group("dir") == "reports":
            assert match.group("cmd") == "mkdir -p", (
                "the render step must use mkdir -p reports, not "
                f"{match.group('cmd')} {match.group('dir')}"
            )


def test_a_plain_mkdir_in_the_reusable_scenario_workflow_is_the_first_command():
    """``mkdir dir`` without -p is only safe as the first command of its step."""
    for script in _run_scripts(SCENARIO):
        first_command = None
        for line in script.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if first_command is None:
                first_command = stripped
            match = _SHELL_MKDIR.match(stripped)
            if match and " -p" not in match.group("cmd"):
                assert stripped == first_command, (
                    "a non-idempotent mkdir in scenario.yml must be the first "
                    f"command of its step, not {stripped!r} after {first_command!r}"
                )


def test_walk_hallway_has_no_directory_creation_of_its_own():
    assert "workflow_call:" in WALK
    assert "mkdir" not in WALK


def test_this_repos_verify_workflow_is_not_reusable():
    """verify.yml still has ``mkdir reports``, but callers do not run that file."""
    assert "workflow_call:" not in VERIFY
    assert re.search(r"(?m)^\s+mkdir reports$", VERIFY)
