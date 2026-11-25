# SPDX-License-Identifier: Apache-2.0
"""Container jobs must run bash-only scripts under bash (#251).

GitHub Actions runs a ``run:`` step inside ``container:`` with ``sh`` unless the
step or the job names another shell. ``sh`` on the verifier image is dash, which
rejects ``set -o pipefail``, here-strings, and arrays before the step does any work.
The check parses workflow YAML. A quoted heredoc body is data, not the step shell.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# Bash syntax that dash rejects. Arithmetic ``$((...))`` is POSIX and is not listed.
_BASH_ONLY = (
    ("pipefail", re.compile(r"\bpipefail\b")),
    ("here-string", re.compile(r"<<<")),
    ("double-bracket", re.compile(r"\[\[")),
    (
        "array",
        re.compile(
            r"(?m)\$\{#?[A-Za-z_][A-Za-z0-9_]*\["
            r"|(?:^|[\s;|&(])[A-Za-z_][A-Za-z0-9_]*=\("
        ),
    ),
    ("process-substitution", re.compile(r"<\(|\>\(")),
    (
        "declare",
        re.compile(r"(?m)(?:^|[\s;|&(])(?:declare|typeset|shopt|mapfile|readarray)\b"),
    ),
    ("function", re.compile(r"(?m)(?:^|[\s;|&(])function\s+[A-Za-z_]")),
)


def _comment_starts(emitted: list[str]) -> bool:
    if not emitted:
        return True
    return emitted[-1] in " \t;|&({"


def _read_heredoc(line: str, index: int) -> tuple[str | None, bool, int]:
    """Read the delimiter after ``<<``. ``index`` points at the first character after it."""
    strip_tabs = False
    if index < len(line) and line[index] == "-":
        strip_tabs = True
        index += 1
    while index < len(line) and line[index] in " \t":
        index += 1
    if index >= len(line):
        return None, False, index
    quote = line[index]
    if quote in {"'", '"'}:
        end = line.find(quote, index + 1)
        if end < 0:
            return None, False, index
        return line[index + 1 : end], strip_tabs, end + 1
    end = index
    while end < len(line) and line[end] not in " \t|&;<>()$`\\\"'":
        end += 1
    if end == index:
        return None, False, index
    return line[index:end], strip_tabs, end


def _scan_line(line: str) -> tuple[str, list[tuple[str, bool]]]:
    """Return shell text with comments removed, and heredocs opened on this line."""
    emitted: list[str] = []
    heredocs: list[tuple[str, bool]] = []
    index = 0
    length = len(line)
    in_single = False
    in_double = False
    brace = 0
    while index < length:
        char = line[index]
        if in_single:
            emitted.append(char)
            if char == "'":
                in_single = False
            index += 1
            continue
        if in_double:
            emitted.append(char)
            if char == "\\" and index + 1 < length:
                emitted.append(line[index + 1])
                index += 2
                continue
            if char == '"':
                in_double = False
            elif char == "$" and index + 1 < length and line[index + 1] == "{":
                brace += 1
            elif char == "}" and brace:
                brace -= 1
            index += 1
            continue
        if char == "\\" and index + 1 < length:
            emitted.append(char)
            emitted.append(line[index + 1])
            index += 2
            continue
        if char == "'":
            in_single = True
            emitted.append(char)
            index += 1
            continue
        if char == '"':
            in_double = True
            emitted.append(char)
            index += 1
            continue
        if char == "$" and index + 1 < length and line[index + 1] == "{":
            brace += 1
            emitted.append("${")
            index += 2
            continue
        if char == "}" and brace:
            brace -= 1
            emitted.append(char)
            index += 1
            continue
        if char == "#" and brace == 0 and _comment_starts(emitted):
            break
        if char == "<" and line.startswith("<<<", index):
            emitted.append("<<<")
            index += 3
            continue
        if char == "<" and line.startswith("<<", index):
            delimiter, strip_tabs, nxt = _read_heredoc(line, index + 2)
            if delimiter is None:
                emitted.append("<<")
                index += 2
                continue
            heredocs.append((delimiter, strip_tabs))
            emitted.append("<<" + delimiter)
            index = nxt
            continue
        emitted.append(char)
        index += 1
    return "".join(emitted), heredocs


def executed_shell(script: str) -> str:
    """The shell commands a ``run:`` step executes, without heredoc bodies or comments."""
    pending: list[tuple[str, bool]] = []
    kept: list[str] = []
    for raw in script.splitlines():
        if pending:
            delimiter, strip_tabs = pending[0]
            body = raw.lstrip("\t") if strip_tabs else raw
            if body == delimiter:
                pending.pop(0)
            continue
        cleaned, heredocs = _scan_line(raw)
        kept.append(cleaned)
        pending.extend(heredocs)
    return "\n".join(kept)


def bash_only_features(script: str) -> list[str]:
    code = executed_shell(script)
    return [name for name, pattern in _BASH_ONLY if pattern.search(code)]


def _shell_from_defaults(node: object) -> str | None:
    if not isinstance(node, dict):
        return None
    run = node.get("run")
    if not isinstance(run, dict):
        return None
    shell = run.get("shell")
    if isinstance(shell, str) and shell.strip():
        return shell.strip()
    return None


def _container_job(job: dict) -> bool:
    container = job.get("container")
    if isinstance(container, str):
        return container.strip() != ""
    return isinstance(container, dict) and bool(container)


def effective_shell(step: dict, job: dict, workflow: dict) -> str:
    """The shell GitHub uses for this ``run:`` step."""
    step_shell = step.get("shell")
    if isinstance(step_shell, str) and step_shell.strip():
        return step_shell.strip()
    for node in (job, workflow):
        shell = _shell_from_defaults(node.get("defaults") if isinstance(node, dict) else None)
        if shell:
            return shell
    if _container_job(job):
        return "sh"
    return "bash"


def uses_bash(shell: str) -> bool:
    program = shell.split(None, 1)[0].removesuffix("{0}")
    return Path(program).name == "bash"


def container_shell_problems(workflow: object, filename: str) -> list[str]:
    if not isinstance(workflow, dict):
        return [f"{filename}: workflow is not a mapping"]
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return []
    problems: list[str] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict) or not _container_job(job):
            continue
        steps = job.get("steps") or []
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            shell = effective_shell(step, job, workflow)
            if uses_bash(shell):
                continue
            features = bash_only_features(step["run"])
            if not features:
                continue
            label = step.get("name") or step.get("id") or f"step {index}"
            problems.append(
                f"{filename}: job {job_name} step {label} uses {', '.join(features)} under {shell}"
            )
    return problems


def test_repository_container_jobs_run_bash_syntax_under_bash():
    problems: list[str] = []
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert paths, "no workflow files found"
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        relative = path.relative_to(ROOT).as_posix()
        problems.extend(container_shell_problems(document, relative))
    assert problems == []


def test_pipefail_here_strings_and_arrays_are_bash_only():
    script = """
    set -euo pipefail
    repository="$(jq -r .workflow_repository <<<"$JOB_JSON")"
    items=(one two)
    printf '%s\n' "${items[@]}"
    """
    assert bash_only_features(script) == ["pipefail", "here-string", "array"]


def test_a_quoted_heredoc_is_not_the_step_shell():
    script = """
    cat > /tmp/collect.py <<'COLLECT'
    set -euo pipefail
    values = []
    found.append((path, document))
    spans: list[tuple[int, int]] = []
    COLLECT
    python3 /tmp/collect.py
    signal=$(kill -l "$((code - 128))" 2>/dev/null | tr -cd 'A-Z0-9' || true)
    """
    assert bash_only_features(script) == []


def test_comments_do_not_count_as_bash_syntax():
    assert bash_only_features("# set -euo pipefail\necho hi # <<<\n") == []


def test_a_container_step_without_bash_is_rejected_and_a_bash_shell_is_accepted():
    script = 'set -euo pipefail\nrepository="$(jq -r .workflow_repository <<<"$JOB_JSON")"\n'
    bare = {
        "jobs": {
            "evidence": {
                "container": {"image": "example"},
                "steps": [{"id": "resolved-pin-source", "run": script}],
            }
        }
    }
    problems = container_shell_problems(bare, "scenario.yml")
    assert problems == [
        "scenario.yml: job evidence step resolved-pin-source uses pipefail, here-string under sh"
    ]
    bare["jobs"]["evidence"]["defaults"] = {"run": {"shell": "bash"}}
    assert container_shell_problems(bare, "scenario.yml") == []
    bare["jobs"]["evidence"]["steps"][0]["shell"] = "sh"
    assert container_shell_problems(bare, "scenario.yml") == [
        "scenario.yml: job evidence step resolved-pin-source uses pipefail, here-string under sh"
    ]


def test_a_runner_job_keeps_the_platform_bash_default():
    workflow = {
        "jobs": {
            "plan": {
                "runs-on": "ubuntu-24.04",
                "steps": [{"run": "set -euo pipefail\n"}],
            }
        }
    }
    assert container_shell_problems(workflow, "scenario.yml") == []


def test_double_brackets_and_process_substitution_are_bash_only():
    assert bash_only_features('[[ "$VERSION" =~ ^[0-9]+$ ]]\n') == ["double-bracket"]
    assert bash_only_features("diff <(echo a) <(echo b)\n") == ["process-substitution"]
